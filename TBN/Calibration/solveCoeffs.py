#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Given the complex visibilities generated by simpleFringe.py, create a reference NPZ
file for use with phasedBeam.py/trackSource.py for generating phase-and-sum beam 
forming coefficients.

Usage:
./solveCoeffs.py <refernece source> <NPZ visibility file>

$Rev$
$LastChangedBy$
$LastChangedDate$
"""

import os
import sys
import ephem
import numpy
from datetime import datetime, timedelta

from scipy.signal import triang

from lsl.common.constants import c
from lsl.common.stations import lwa1
from lsl.correlator.uvUtils import computeUVW


# List of bright radio sources and pulsars in PyEphem format
_srcs = ["ForA,f|J,03:22:41.70,-37:12:30.0,1",
         "TauA,f|J,05:34:32.00,+22:00:52.0,1", 
         "VirA,f|J,12:30:49.40,+12:23:28.0,1",
         "HerA,f|J,16:51:08.15,+04:59:33.3,1", 
         "SgrA,f|J,17:45:40.00,-29:00:28.0,1", 
         "CygA,f|J,19:59:28.30,+40:44:02.0,1", 
         "CasA,f|J,23:23:27.94,+58:48:42.4,1",]


def getGeoDelay(antenna, az, el, freq, Degrees=False):
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
	
	cableDelay = antenna.cable.delay(freq)
	xyz = numpy.array([antenna.stand.x, antenna.stand.y, antenna.stand.z])
	return numpy.dot(source, xyz) / c - 0*cableDelay


def getFringeRate(antenna1, antenna2, observer, src, freq):
	"""
	Get the fringe rate for a baseline formed by antenna1-antenna2 for source src
	as observed by an observer.
	"""
	
	src.compute(observer)
	
	HA = (float(observer.sidereal_time()) - float(src.ra))*12.0/numpy.pi
	dec = float(src.dec)*180/numpy.pi
	uvw = computeUVW([antenna1, antenna2], HA=HA, dec=dec, freq=freq)
	
	return -(2*numpy.pi/86164.0905)*uvw[0,0,0]*numpy.cos(src.dec)


def main(args):
	observer = lwa1.getObserver()
	antennas = lwa1.getAntennas()
	
	reference = args[0]
	filename = args[1]
	dataDict = numpy.load(filename)
	
	ref  = dataDict['ref']
	refX = dataDict['refX'] - 1
	refY = dataDict['refY'] - 1
	centralFreq = dataDict['centralFreq']
	tInt = dataDict['tInt']
	times = dataDict['times']
	phase = dataDict['simpleVis']
	
	print "Central frequency: %.3f Hz" % centralFreq
	
	# Get the start time as a datetime object and build up the list of sources
	beginDate = datetime.utcfromtimestamp(times[0])
	observer.date = beginDate.strftime("%Y/%m/%d %H:%M:%S")
	print beginDate.strftime("%Y/%m/%d %H:%M:%S")
	srcs = [ephem.Sun(),]
	for line in _srcs:
		srcs.append( ephem.readdb(line) )
	
	# Compute the loations of all of the sources to figure out where the 
	# referenece is
	az = -99
	el = -99
	for i in xrange(len(srcs)):
		srcs[i].compute(observer)
		
		if srcs[i].alt > 0:
			print "source %s: alt %.1f degrees, az %.1f degrees" % (srcs[i].name, srcs[i].alt*180/numpy.pi, srcs[i].az*180/numpy.pi)
			
		if srcs[i].name == reference:
			az = srcs[i].az  * 180.0/numpy.pi
			el = srcs[i].alt * 180.0/numpy.pi
			
	if az == -99:
		print "Cannot find reference source '%s' in source list, aborting." % reference
		sys.exit(1)
	
	# Calculate the fringe rates of all sources
	fRate = {}
	for src in srcs:
		if src.alt > 0:
			fRate[src.name] = getFringeRate(antennas[0], antennas[refX], observer, src, centralFreq)
			print src.name, fRate[src.name]
	
	phase2 = phase*1.0
	
	# Compute the beam forming coefficients for the reference source
	bln = numpy.zeros(phase2.shape, dtype=numpy.complex128)
	for i in xrange(bln.shape[1]):
		if i % 2 == 0:
			bln[:,i] = phase2[:,i] / phase2[:,0]
		else:
			bln[:,i] = phase2[:,i] / phase2[:,1]
	bln = bln.conj() / numpy.abs(bln)
	
	# Compute the a^l_n terms for removing the array geometry.
	aln = []
	for i in xrange(phase.shape[1]):
		gd = getGeoDelay(antennas[i], az, el, centralFreq, Degrees=True)
		aln.append( numpy.exp(2j*numpy.pi*centralFreq*gd) )
	aln = numpy.array(aln)
	
	# Calculate the c^l_n terms by removing the array geometry from the
	# phases.
	cln = numpy.zeros(phase2.shape, dtype=numpy.complex128)
	for i in xrange(cln.shape[1]):
		if i % 2 == 0:
			cln[:,i] = phase2[:,i] / phase2[:,0]
		else:
			cln[:,i] = phase2[:,i] / phase2[:,1]
	cln /= aln
	
	# Save
	outname = filename.replace('-vis','-cln')
	numpy.savez(outname, cln=cln, centralFreq=centralFreq, reference=reference, basefile=filename)


if __name__ == "__main__":
	main(sys.argv[1:])
