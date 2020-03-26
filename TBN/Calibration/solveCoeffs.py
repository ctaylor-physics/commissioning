#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Given the complex visibilities generated by simpleFringe.py, create a reference NPZ
file for use with phasedBeam.py/trackSource.py for generating phase-and-sum beam 
forming coefficients.

Usage:
./solveCoeffs.py <refernece source> <NPZ visibility file>
"""

import os
import sys
import ephem
import numpy
from datetime import datetime, timedelta

from scipy.signal import triang

from lsl.common.constants import c as vLight
from lsl.common.stations import lwa1
from lsl.common.progress import ProgressBar
from lsl.correlator.uvutil import compute_uvw


# List of bright radio sources and pulsars in PyEphem format
_srcs = ["ForA,f|J,03:22:41.70,-37:12:30.0,1",
         "TauA,f|J,05:34:32.00,+22:00:52.0,1", 
         "VirA,f|J,12:30:49.40,+12:23:28.0,1",
         "HerA,f|J,16:51:08.15,+04:59:33.3,1", 
         "SgrA,f|J,17:45:40.00,-29:00:28.0,1", 
         "CygA,f|J,19:59:28.30,+40:44:02.0,1", 
         "CasA,f|J,23:23:27.94,+58:48:42.4,1",]


def getGeoDelay(antenna, az, el, Degrees=False):
    """
    Get the geometrical delay (relative to the center of the array)
    for the specified antenna for a source at azimuth az, elevation el.
    """

    if Degrees:
        az = az*numpy.pi/180.0
        el = el*numpy.pi/180.0
    
    source = numpy.array([numpy.cos(el)*numpy.sin(az), 
                    numpy.cos(el)*numpy.cos(az), 
                    numpy.sin(el)])
    
    xyz = numpy.array([antenna.stand.x, antenna.stand.y, antenna.stand.z])
    return numpy.dot(source, xyz) / vLight


def getFringeRate(antenna1, antenna2, observer, src, freq):
    """
    Get the fringe rate for a baseline formed by antenna1-antenna2 for source src
    as observed by an observer.
    """
    
    # Update the source position
    src.compute(observer)
    
    # Calculate the hour angle
    HA = (float(observer.sidereal_time()) - float(src.ra))*12.0/numpy.pi
    dec = float(src.dec)*180/numpy.pi
    
    # Get the u,v,w coordinates
    uvw = compute_uvw([antenna1, antenna2], HA=HA, dec=dec, freq=freq)
    
    return -(2*numpy.pi/86164.0905)*uvw[0,0,0]*numpy.cos(src.dec)


def main(args):
    observer = lwa1.get_observer()
    antennas = lwa1.antennas
    
    reference = args[0]
    filename = args[1]
    dataDict = numpy.load(filename)
    
    ref  = dataDict['ref'].item()
    refX = dataDict['refX'].item()
    refY = dataDict['refY'].item()
    central_freq = dataDict['central_freq'].item()
    tInt = dataDict['tInt'].item()
    times = dataDict['times']
    phase = dataDict['simpleVis']
    
    # Get the start time as a datetime object and build up the list of sources
    beginDate = datetime.utcfromtimestamp(times[0])
    observer.date = beginDate.strftime("%Y/%m/%d %H:%M:%S")
    srcs = [ephem.Sun(),]
    for line in _srcs:
        srcs.append( ephem.readdb(line) )
        
    # Report on the data so far...
    print "Central Frequency: %.3f Hz" % central_freq
    print "Start date/time: %s" % beginDate.strftime("%Y/%m/%d %H:%M:%S")
    print "Integration Time: %.3f s" % tInt
    print "Number of time samples: %i (%.3f s)" % (phase.shape[0], phase.shape[0]*tInt)
    
    # Compute the locations of all of the sources to figure out where the 
    # reference is
    print "Starting Source Positions:"
    
    refSrc = None
    for i in xrange(len(srcs)):
        srcs[i].compute(observer)
        
        if srcs[i].alt > 0:
            print " source %-4s: alt %4.1f degrees, az %5.1f degrees" % (srcs[i].name, srcs[i].alt*180/numpy.pi, srcs[i].az*180/numpy.pi)
            
        if srcs[i].name == reference:
            refSrc = srcs[i]
            
    if refSrc is None:
        print "Cannot find reference source '%s' in source list, aborting." % reference
        sys.exit(1)
    
    # Calculate the fringe rates of all sources - for display purposes only
    print "Starting Fringe Rates:"
    for src in srcs:
        if src.alt > 0:
            fRate = getFringeRate(antennas[0], antennas[refX], observer, src, central_freq)
            print " source %-4s: %+6.3f mHz" % (src.name, fRate*1e3)
    
    # Fringe stopping using the reference source
    print "Fringe stopping:"
    pbar = ProgressBar(max=phase.shape[1])

    phase2 = 1.0*phase
    for l in xrange(phase.shape[1]):
        # Compute the fringe rates across all time
        fRate = [None,]*phase.shape[0]
        for i in xrange(phase.shape[0]):
            currDate = datetime.utcfromtimestamp(times[i])
            observer.date = currDate.strftime("%Y/%m/%d %H:%M:%S")
        
            if l % 2 == 0:
                fRate[i] = getFringeRate(antennas[l], antennas[refX], observer, refSrc, central_freq)
            else:
                fRate[i] = getFringeRate(antennas[l], antennas[refY], observer, refSrc, central_freq)
        
        # Create the basis rate and the residual rates
        baseRate = fRate[0]
        residRate = numpy.array(fRate) - baseRate
    
        # Fringe stop to more the source of interest to the DC component
        phase2[:,l] *= numpy.exp(-2j*numpy.pi* baseRate*(times - times[0]))
        phase2[:,l] *= numpy.exp(-2j*numpy.pi*residRate*(times - times[0]))
        
        pbar.inc()
        sys.stdout.write("%s\r" % pbar.show())
        sys.stdout.flush()
    sys.stdout.write('\n')
    
    # Compute the beam forming coefficients for the reference source
    bln = numpy.zeros(phase2.shape, dtype=numpy.complex128)
    for i in xrange(bln.shape[1]):
        if i % 2 == 0:
            bln[:,i] = phase2[:,i] / phase2[:,0]
        else:
            bln[:,i] = phase2[:,i] / phase2[:,1]
    bln = bln.conj() / numpy.abs(bln)
    
    # Average all time steps together and make sure we end up with 
    # a 2-D array in the end
    bln = bln.mean(axis=0)
    bln.shape = (1,) + bln.shape
    
    # Compute the a^l_n terms for removing the array geometry.
    print "Computing array geometry:"
    pbar = ProgressBar(max=phase2.shape[1])
    
    aln = numpy.zeros_like(phase2)
    for l in xrange(phase2.shape[1]):
        currDate = datetime.utcfromtimestamp(times[0])
        observer.date = currDate.strftime("%Y/%m/%d %H:%M:%S")
        refSrc.compute(observer)
        
        az = refSrc.az
        el = refSrc.alt
        gd = getGeoDelay(antennas[l], az, el, Degrees=False)
        
        aln[:,l] = numpy.exp(2j*numpy.pi*central_freq*gd)
        
        pbar.inc()
        sys.stdout.write("%s\r" % pbar.show())
        sys.stdout.flush()
    sys.stdout.write('\n')
    
    # Calculate the c^l_n terms by removing the array geometry from the
    # phases.
    cln = numpy.zeros(phase2.shape, dtype=numpy.complex128)
    for i in xrange(cln.shape[1]):
        if i % 2 == 0:
            cln[:,i] = phase2[:,i] / phase2[:,0]
        else:
            cln[:,i] = phase2[:,i] / phase2[:,1]
    cln /= aln
    
    # Average all time steps together and make sure we end up with 
    # a 2-D array in the end
    cln = cln.mean(axis=0)
    cln.shape = (1,) + cln.shape
    
    # Save
    outname = filename.replace('-vis','-cln')
    numpy.savez(outname, bln=bln, cln=cln, central_freq=central_freq, reference=reference, basefile=filename)


if __name__ == "__main__":
    main(sys.argv[1:])
