#! /usr/bin/env python

"""
Predict driftcurve for a given site using a given antenna model.
"""

import os
import sys
import math
import numpy
import pylab
import argparse

from lsl import skymap, astro
from lsl.common import stations
from lsl.common.paths import DATA as dataPath
from lsl.misc import parser as aph

__version__  = "0.1"
__author__    = "D.L.Wood"
__maintainer__ = "Jayce Dowell"


def main(args):
    # Beam
    beamFilename = args.filename
    beamDict = numpy.load(beamFilename)
    beam = beamDict['beam']
    beam /= beam.max()
    
    # Station, polarization, frequency, and beam simulation resolution
    name = beamDict['station'].item()
    pol = beamDict['pol'].item()
    freq = beamDict['freq'].item()
    try:
        res = beamDict['res'].item()
    except KeyError:
        res = 1.0
    ires = 1.0 / (min([1.0, res]))
        
    # Get the site information
    if name == 'lwa1':
        sta = stations.lwa1
    elif name == 'lwasv':
        sta = stations.lwasv
    else:
        raise RuntimeError("Unknown site: %s" % name)
        
    # Read in the skymap (GSM or LF map @ 74 MHz)
    if not args.lfsm:
        smap = skymap.SkyMapGSM(freq_MHz=freq/1e6)
        if args.verbose:
            print("Read in GSM map at %.2f MHz of %s pixels; min=%f, max=%f" % (freq/1e6, len(smap.ra), smap._power.min(), smap._power.max()))
    else:
        smap = skymap.SkyMapLFSM(freq_MHz=freq/1e6)
        if args.verbose:
            print("Read in LFSM map at %.2f MHz of %s pixels; min=%f, max=%f" % (freq/1e6, len(smap.ra), smap._power.min(), smap._power.max()))
            
    def BeamPattern(az, alt, beam=beam, ires=ires):
        iAz = (numpy.round(az*ires)).astype(numpy.int32)
        iAlt = (numpy.round(alt*ires)).astype(numpy.int32) 
        
        return beam[iAz,iAlt]
        
    if args.do_plot:
        az = numpy.arange(0,360*ires+1,1) / float(ires)
        alt = numpy.arange(0,90*ires+1,1) / float(ires)
        alt, az = numpy.meshgrid(alt, az)
        pylab.figure(1)
        pylab.title("Beam Response: %s pol. @ %0.2f MHz" % (pol, freq/1e6))
        pylab.imshow(BeamPattern(az, alt), interpolation='nearest', extent=(0,359, 0,89), origin='lower')
        pylab.xlabel("Azimuth [deg]")
        pylab.ylabel("Altitude [deg]")
        pylab.grid(1)
        pylab.draw()
        
    # Calculate times in both site LST and UTC
    t0 = astro.get_julian_from_sys()
    lst = astro.get_local_sidereal_time(sta.long*180.0/math.pi, t0) / 24.0
    t0 -= lst*(23.933/24.0) # Compensate for shorter sidereal days
    times = numpy.arange(0.0, 1.0, args.time_step/1440.0) + t0
    
    lstList = []
    powListAnt = [] 
    
    for t in times:
        # Project skymap to site location and observation time
        pmap = skymap.ProjectedSkyMap(smap, sta.lat*180.0/math.pi, sta.long*180.0/math.pi, t)
        lst = astro.get_local_sidereal_time(sta.long*180.0/math.pi, t)
        lstList.append(lst)
        
        # Convolution of user antenna pattern with visible skymap
        gain = BeamPattern(pmap.visibleAz, pmap.visibleAlt)
        powerAnt = (pmap.visiblePower * gain).sum() / gain.sum()
        powListAnt.append(powerAnt)

        if args.verbose:
            lstH = int(lst)
            lstM = int((lst - lstH)*60.0)
            lstS = ((lst - lstH)*60.0 - lstM)*60.0
            sys.stdout.write("LST: %02i:%02i:%04.1f, Power_ant: %.1f K \r" % (lstH, lstM, lstS, powerAnt))
            sys.stdout.flush()
    sys.stdout.write("\n")
    
    # Plot results
    if args.do_plot:
        pylab.figure(2)
        pylab.title("Driftcurve: %s pol. @ %0.2f MHz - %s" % \
            (pol, freq/1e6, name.upper()))
        pylab.plot(lstList, powListAnt, "ro",label="Antenna Pattern")
        pylab.xlabel("LST [hours]")
        pylab.ylabel("Temp. [K]")
        pylab.grid(2)
        pylab.draw()
        pylab.show()
        
    outputFile = "driftcurve_%s_%s_%.2f.txt" % (name, pol, freq/1e6)
    print("Writing driftcurve to file '%s'" % outputFile)
    mf = open(outputFile, "w")
    for lst,pow in zip(lstList, powListAnt):
        mf.write("%f  %f\n" % (lst,pow))
    mf.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='simulate a drift curve for a beam at LWA1 from a beam model generated by estimateBeam.py', 
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
        )
    parser.add_argument('filename', type=str, 
                        help='beam filename to use')
    parser.add_argument('-l', '--lfsm', action='store_true', 
                        help='use LFSM instead of GSM')
    parser.add_argument('-t', '--time-step', type=aph.positive_float, default=10.0, 
                        help='time step of the simulation in minutes')
    parser.add_argument('-x', '--do-plot', action='store_true', 
                        help='plot the driftcurve data')
    parser.add_argument('-v', '--verbose', action='store_true', 
                        help='run %(prog)s in verbose mode')
    args = parser.parse_args()
    main(args)
    
