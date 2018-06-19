#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
$Rev$
$LastChangedBy$
$LastChangedDate$
"""

import os
import sys
import aipy
import copy
import pytz
import ephem
import numpy
import getopt
import pyfits
from calendar import timegm
from datetime import datetime

from lsl import astro
from lsl.common import stations
from lsl.statistics.robust import *
from lsl.correlator import uvUtils
from lsl.writer.fitsidi import NumericStokes

from lsl.imaging import utils, selfCal
from lsl.sim import vis as simVis

from matplotlib.mlab import griddata
import matplotlib.pyplot as plt
from matplotlib.ticker import NullFormatter


MST = pytz.timezone('US/Mountain')
UTC = pytz.UTC


def usage(exitCode=None):
    print """applySelfCalTBW2.py - Self-calibrate a TBW FITS IDI file

Usage: applySelfCalTBW2.py [OPTIONS] file

Options:
-h, --help             Display this help information
-r, --reference        Reference stand to use (default = 173)
-l, --lower            Lowest frequency to consider in MHz 
                    (default = 35 MHz)
-u, --upper            Highest frequency to consider in MHz
                    (default = 85 MHz)
-p, --plot             Plot the results at the end (default = no)
"""

    if exitCode is not None:
        sys.exit(exitCode)
    else:
        return True


def parseConfig(args):
    config = {}
    # Command line flags - default values
    config['refAnt'] = 173
    config['freqLimits'] = [35e6, 85e6]
    config['plot'] = False

    # Read in and process the command line flags
    try:
        opts, arg = getopt.getopt(args, "hr:l:u:p", ["help", "reference=", "lower=", "upper=", "plot"])
    except getopt.GetoptError, err:
        # Print help information and exit:
        print str(err) # will print something like "option -a not recognized"
        usage(exitCode=2)
    
    # Work through opts
    for opt, value in opts:
        if opt in ('-h', '--help'):
            usage(exitCode=0)
        elif opt in ('-r', '--reference'):
            config['refAnt'] = int(value)
        elif opt in ('-l', '--lower'):
            config['freqLimits'][0] = float(value)
        elif opt in ('-u', '--upper'):
            config['freqLimits'][1] = float(value)
        elif opt in ('-p', '--plot'):
            config['plot'] = True
        else:
            assert False
            
    # Add in arguments
    config['args'] = arg

    # Return configuration
    return config


def graticle(ax, lst, lat, label=True):
    """
    For a matplotlib axis instance showing an image of the sky, plot lines of
    constant declination and RA.  Declinations are spaced at 20 degree intervals
    and RAs are spaced at 2 hour intervals.
    
    .. note::
        LST and latitude values should be passed as radians.  This is the default
        for lwa1.getObserver.sidereal_time() and lwa1.getObserver().lat.
    """
    
    # Lines of constant declination first
    decs = range(-80, 90, 20)
    ras = numpy.linspace(0, 360, 800)
    
    x = numpy.zeros(ras.size)
    x = numpy.ma.array(x, mask=numpy.zeros(ras.size))
    y = numpy.zeros(ras.size)
    y = numpy.ma.array(y, mask=numpy.zeros(ras.size))
    
    for dec in decs:
        x *= 0
        y *= 0
        
        # Loop over RA to compute the topocentric coordinates (used by the image) for
        # the lines.  Also, figure out the elevation for each point on the line so
        # we can mask those below the horizon
        for i,ra in enumerate(ras):
            eq = aipy.coord.radec2eq((-lst + ra*numpy.pi/180,dec*numpy.pi/180))
            xyz = numpy.dot(aipy.coord.eq2top_m(0, lat), eq)
            az,alt = aipy.coord.top2azalt(xyz)
            
            x[i] = xyz[0]
            y[i] = xyz[1]
            if alt <= 0:
                x.mask[i] = 1
                y.mask[i] = 1
            else:
                x.mask[i] = 0
                y.mask[i] = 0
                
        ax.plot(x, y, color='white', alpha=0.75)
        
        eq = aipy.coord.radec2eq((-lst + lst,(dec+5)*numpy.pi/180))
        xyz = numpy.dot(aipy.coord.eq2top_m(0, lat), eq)
        az,alt = aipy.coord.top2azalt(xyz)
        
        if alt > 15*numpy.pi/180 and label:
            ax.text(xyz[0], xyz[1], '%+i$^\circ$' % dec, color='white')
            
    # Lines of constant RA
    decs = numpy.linspace(-80, 80, 400)
    ras = range(0,360,30)
    
    x = numpy.zeros(decs.size)
    x = numpy.ma.array(x, mask=numpy.zeros(decs.size))
    y = numpy.zeros(decs.size)
    y = numpy.ma.array(y, mask=numpy.zeros(decs.size))
    
    for ra in ras:
        x *= 0
        y *= 0
        
        # Loop over dec. to compute the topocentric coordinates (used by the image) for
        # the lines.  Also, figure out the elevation for each point on the line so
        # we can mask those below the horizon
        for i,dec in enumerate(decs):
            eq = aipy.coord.radec2eq((-lst + ra*numpy.pi/180,dec*numpy.pi/180))
            xyz = numpy.dot(aipy.coord.eq2top_m(0, lat), eq)
            az,alt = aipy.coord.top2azalt(xyz)
            
            x[i] = xyz[0]
            y[i] = xyz[1]
            if alt <= 0:
                x.mask[i] = 1
                y.mask[i] = 1
            else:
                x.mask[i] = 0
                y.mask[i] = 0
                
        ax.plot(x, y, color='white', alpha=0.75)
        
        eq = aipy.coord.radec2eq((-lst + ra*numpy.pi/180,0))
        xyz = numpy.dot(aipy.coord.eq2top_m(0, lat), eq)
        az,alt = aipy.coord.top2azalt(xyz)
        
        if alt > 20*numpy.pi/180 and label:
            ax.text(xyz[0], xyz[1], '%i$^h$' % (ra/15,), color='white')


def main(args):
    config = parseConfig(args)
    filename = config['args'][0]
    
    idi = utils.CorrelatedData(filename)
    aa = idi.getAntennaArray()
    lo = idi.getObserver()
    lo.date = idi.dateObs.strftime("%Y/%m/%d %H:%M:%S")
    jd = lo.date + astro.DJD_OFFSET
    lst = str(lo.sidereal_time())

    nStand = len(idi.stands)
    nChan = len(idi.freq)
    freq = idi.freq
    
    print "Raw Stand Count: %i" % nStand
    print "Final Baseline Count: %i" % (nStand*(nStand-1)/2,)
    print "Spectra Coverage: %.3f to %.3f MHz in %i channels (%.2f kHz/channel)" % (freq[0]/1e6, freq[-1]/1e6, nChan, (freq[-1] - freq[0])/1e3/nChan)
    print "Polarization Products: %i starting with %i" % (len(idi.pols), idi.pols[0])
    print "JD: %.3f" % jd
    
    # Pull out something reasonable
    toWork = numpy.where((freq>=config['freqLimits'][0]) & (freq<=config['freqLimits'][1]))[0]
    
    print "Reading in FITS IDI data"
    nSets = idi.totalBaselineCount / (nStand*(nStand+1)/2)
    for set in range(1, nSets+1):
        print "Set #%i of %i" % (set, nSets)
        fullDict = idi.getDataSet(set)
        dataDict = utils.pruneBaselineRange(fullDict, uvMin=14.0)
        utils.sortDataDict(dataDict)
        
        # Gather up the polarizations and baselines
        pols = dataDict['jd'].keys()
        bls = dataDict['bls'][pols[0]]
        print "The reduced list has %i baselines and %i channels" % (len(bls), len(toWork))
        
        # Build a list of unique JDs for the data
        jdList = []
        for jd in dataDict['jd'][pols[0]]:
            if jd not in jdList:
                jdList.append(jd)
                
        # Build the simulated visibilities
        print "Building Model"
        simDict = simVis.buildSimData(aa, simVis.srcs, jd=[jdList[0],], pols=pols, baselines=bls)
        
        print "Running self cal."
        simDict  = utils.sortDataDict(simDict)
        dataDict = utils.sortDataDict(dataDict)
        fixedDataXX, delaysXX = selfCal.delayOnly(aa, dataDict, simDict, toWork, 'xx', refAnt=config['refAnt'], nIter=60)
        fixedDataYY, delaysYY = selfCal.delayOnly(aa, dataDict, simDict, toWork, 'yy', refAnt=config['refAnt'], nIter=60)
        fixedFullXX = simVis.scaleData(fullDict, delaysXX*0+1, delaysXX)
        fixedFullYY = simVis.scaleData(fullDict, delaysYY*0+1, delaysYY)
        
        print "    Saving results"
        outname = os.path.split(filename)[1]
        outname = os.path.splitext(outname)[0]
        outname = "%s.sc" % outname
        fh = open(outname, 'w')
        fh.write("################################\n")
        fh.write("#                              #\n")
        fh.write("# Columns:                     #\n")
        fh.write("# 1) Stand number              #\n")
        fh.write("# 2) X pol. amplitude          #\n")
        fh.write("# 3) X pol. delay (ns)         #\n")
        fh.write("# 4) Y pol. amplitude          #\n")
        fh.write("# 5) Y pol. delay (ns)         #\n")
        fh.write("#                              #\n")
        fh.write("################################\n")
        for i in xrange(delaysXX.size):
            fh.write("%3i  %.6g  %.6g  %.6g  %.6g\n" % (idi.stands[i], 1.0, delaysXX[i], 1.0, delaysYY[i]))
        fh.close()

        # Build up the images for each polarization
        if config['plot']:
            print "    Gridding"
            toWork = numpy.where((freq>=80e6) & (freq<=82e6))[0]
            try:
                imgXX = utils.buildGriddedImage(fullDict, MapSize=80, MapRes=0.5, pol='xx', chan=toWork)
            except:
                imgXX = None
                
            try:
                imgYY = utils.buildGriddedImage(fullDict, MapSize=80, MapRes=0.5, pol='yy', chan=toWork)
            except:
                imgYY = None
                
            try:
                simgXX = utils.buildGriddedImage(simDict, MapSize=80, MapRes=0.5, pol='xx', chan=toWork)
            except:
                simgXX = None
            try:
                simgYY = utils.buildGriddedImage(simDict, MapSize=80, MapRes=0.5, pol='yy', chan=toWork)
            except:
                simgYY = None
                
            try:
                fimgXX = utils.buildGriddedImage(fixedFullXX, MapSize=80, MapRes=0.5, pol='xx', chan=toWork)
            except:
                fimgXX = None
            try:
                fimgYY = utils.buildGriddedImage(fixedFullYY, MapSize=80, MapRes=0.5, pol='yy', chan=toWork)
            except:
                fimgYY = None
                
            # Plots
            print "    Plotting"
            fig = plt.figure()
            ax1 = fig.add_subplot(3, 2, 1)
            ax2 = fig.add_subplot(3, 2, 2)
            ax3 = fig.add_subplot(3, 2, 3)
            ax4 = fig.add_subplot(3, 2, 4)
            ax5 = fig.add_subplot(3, 2, 5)
            ax6 = fig.add_subplot(3, 2, 6)
            for ax, img, pol in zip([ax1, ax2, ax3, ax4, ax5, ax6], [imgXX, imgYY, simgXX, simgYY, fimgXX, fimgYY], ['XX', 'YY', 'simXX', 'simYY', 'scalXX', 'scalYY']):
                # Skip missing images
                if img is None:	
                    ax.text(0.5, 0.5, 'Not found in file', color='black', size=12, horizontalalignment='center')
                    
                    ax.xaxis.set_major_formatter( NullFormatter() )
                    ax.yaxis.set_major_formatter( NullFormatter() )
                    
                    ax.set_title("%s @ %s LST" % (pol, lst))
                    continue
                
                # Display the image and label with the polarization/LST
                out = img.image(center=(80,80))
                print pol, out.min(), out.max()
                #if pol == 'scalXX':
                    #out = numpy.rot90(out)
                    #out = numpy.rot90(out)
                cb = ax.imshow(out, extent=(1,-1,-1,1), origin='lower', 
                        vmin=img.image().min(), vmax=img.image().max())
                fig.colorbar(cb, ax=ax)
                ax.set_title("%s @ %s LST" % (pol, lst))
                
                # Turn off tick marks
                ax.xaxis.set_major_formatter( NullFormatter() )
                ax.yaxis.set_major_formatter( NullFormatter() )
                
                # Compute the positions of major sources and label the images
                compSrc = {}
                ax.plot(0, 0, marker='+', markersize=10, markeredgecolor='w')
                for name,src in simVis.srcs.iteritems():
                    src.compute(aa)
                    top = src.get_crds(crdsys='top', ncrd=3)
                    az, alt = aipy.coord.top2azalt(top)
                    compSrc[name] = [az, alt]
                    if alt <= 0:
                        continue
                    ax.plot(top[0], top[1], marker='x', markerfacecolor='None', markeredgecolor='w', 
                            linewidth=10.0, markersize=10)
                    ax.text(top[0], top[1], name, color='white', size=12)
                    
                # Add lines of constant RA and dec.
                graticle(ax, lo.sidereal_time(), lo.lat)
                
            plt.show()
            
    print "...Done"


if __name__ == "__main__":
    numpy.seterr(all='ignore')
    main(sys.argv[1:])
