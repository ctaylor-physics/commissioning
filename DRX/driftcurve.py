#! /usr/bin/env python
# -*- coding: utf-8 -*-

"""Predict driftcurve for a given site using a given antenna model."""

import os
import sys
import math
import numpy
import pylab
import getopt

from lsl import skymap, astro
from lsl.common import stations
from lsl.common.paths import data as dataPath

__revision__ = "$Revision: 94 $"
__version__  = "0.1"
__author__    = "D.L.Wood"
__maintainer__ = "Jayce Dowell"

def usage(exitCode=None):
	print """driftcurve.py - Generate a drift curve for a dipole at LWA-1 
observing at a given frequency in MHz.

Usage: driftcurve.py [OPTIONS] beam_file

Options:
-h, --help             Display this help information
-f, --freq             Frequency of the observations in MHz
                       (default = 74 MHz)
-p, --polarization     Polarization of the observations (NS or EW; 
                       default = EW)
-l, --lf-map           Use LF map instead of GSM
-t, --time-step        Time step of simulations in minutes (default = 
                       10)
-x, --do-plot          Plot the driftcurve data
-v, --verbose          Run driftcurve in vebose mode
"""

	if exitCode is not None:
		sys.exit(exitCode)
	else:
		return True


def parseOptions(args):
	config = {}
	# Command line flags - default values
	config['freq'] = 74.0e6
	config['pol'] = 'EW'
	config['GSM'] = True
	config['tStep'] = 10.0
	config['enableDisplay'] = False
	config['verbose'] = False
	config['args'] = []

	# Read in and process the command line flags
	try:
		opts, arg = getopt.getopt(args, "hvf:p:lt:x", ["help", "verbose", "freq=", "polarization=", "lf-map", "time-step=", "do-plot",])
	except getopt.GetoptError, err:
		# Print help information and exit:
		print str(err) # will print something like "option -a not recognized"
		usage(exitCode=2)
	
	# Work through opts
	for opt, value in opts:
		if opt in ('-h', '--help'):
			usage(exitCode=0)
		elif opt in ('-v', '--verbose'):
			config['verbose'] = True
		elif opt in ('-f', '--freq'):
			config['freq'] = float(value)*1e6
		elif opt in ('-p', '--polarization'):
			config['pol'] = value.upper()
		elif opt in ('-l', '--lf-map'):
			config['GSM'] = False
		elif opt in ('-t', '--time-step'):
			config['tStep'] = float(value)
		elif opt in ('-x', '--do-plot'):
			config['enableDisplay'] = True
		else:
			assert False
	
	# Add in arguments
	config['args'] = arg

	# Check the validity of arguments
	if config['pol'] not in ('NS', 'EW'):
		print "Invalid polarization: '%s'" % config['pol']
		usage(exitCode=2)

	# Return configuration
	return config


def main(args):
	# Parse command line
	config = parseOptions(args)
	
	beamFilename = config['args'][0]
	beamDict = numpy.load(beamFilename)
	beam = beamDict['beam']
	beam /= beam.max()
	
	# Get the site information for LWA-1
	sta = stations.lwa1
	
	# Read in the skymap (GSM or LF map @ 74 MHz)
	if config['GSM']:
		smap = skymap.SkyMapGSM(freqMHz=config['freq']/1e6)
		if config['verbose']:
			print "Read in GSM map at %.2f MHz of %s pixels; min=%f, max=%f" % (config['freq']/1e6, len(smap.ra), smap._power.min(), smap._power.max())
	else:
		smap = skymap.SkyMap(freqMHz=config['freq']/1e6)
		if config['verbose']:
			print "Read in LF map at %.2f MHz of %d x %d pixels; min=%f, max=%f" % (config['freq']/1e6, smap.numPixelsX, smap.numPixelsY, smap._power.min(), smap._power.max())
	
	def BeamPattern(az, alt, beam=beam):
		iAz  = numpy.round( az).astype(numpy.int32)
		iAz %= 360
		iAlt = numpy.round(alt).astype(numpy.int32)
		iAlt = numpy.where( iAlt < 90, iAlt, 89 )
		
		return beam[iAz,iAlt]

	if config['enableDisplay']:
		az = numpy.zeros((90,360))
		alt = numpy.zeros((90,360))
		for i in range(360):
			az[:,i] = i
		for i in range(90):
			alt[i,:] = i
		pylab.figure(1)
		pylab.title("Beam Response: %s pol. @ %0.2f MHz" % (config['pol'], config['freq']/1e6))
		pylab.imshow(BeamPattern(az, alt), extent=(0,359, 0,89), origin='lower')
		pylab.xlabel("Azimuth [deg]")
		pylab.ylabel("Altitude [deg]")
		pylab.grid(1)
		pylab.draw()
	
	# Calculate times in both site LST and UTC
	t0 = astro.get_julian_from_sys()
	lst = astro.get_local_sidereal_time(sta.long*180.0/math.pi, t0) / 24.0
	t0 -= lst*(23.933/24.0) # Compensate for shorter sidereal days
	times = numpy.arange(0.0, 1.0, config['tStep']/1440.0) + t0
	
	lstList = []
	powListAnt = [] 
	
	for t in times:
		# Project skymap to site location and observation time
		pmap = skymap.ProjectedSkyMap(smap, sta.lat*180.0/math.pi, sta.long*180.0/math.pi, t)
		lst = astro.get_local_sidereal_time(sta.long*180.0/math.pi, t)
		lstList.append(lst)
		
		if config['GSM']:
			cdec = numpy.ones_like(pmap.visibleDec)
		else:
			cdec = numpy.cos(pmap.visibleDec * smap.degToRad)
		
		#print pmap.visibleAz.shape, pmap.visibleAlt.shape, pmap.visiblePower.shape
		#from matplotlib import mlab
		#pwr = mlab.griddata((pmap.visibleAz+180)%360, pmap.visibleAlt, pmap.visiblePower, az, alt)
		#pylab.figure(3)
		#pylab.imshow(pwr, extent=(0,359, 0,89), origin='lower')
		#pylab.xlabel("Azimuth [deg]")
		#pylab.ylabel("Altitude [deg]")
		#pylab.grid(1)
		#pylab.draw()
		
		# Convolution of user antenna pattern with visible skymap
		gain = BeamPattern((pmap.visibleAz+180)%360, pmap.visibleAlt)
		powerAnt = (pmap.visiblePower * gain * cdec).sum() / (gain * cdec).sum()
		powListAnt.append(powerAnt)

		if config['verbose']:
			lstH = int(lst)
			lstM = int((lst - lstH)*60.0)
			lstS = ((lst - lstH)*60.0 - lstM)*60.0
			sys.stdout.write("LST: %02i:%02i:%04.1f, Power_ant: %.1f K\r" % (lstH, lstM, lstS, powerAnt))
			sys.stdout.flush()
	sys.stdout.write("\n")
			
	# plot results
	if config['enableDisplay']:
		pylab.figure(2)
		pylab.title("Driftcurve: %s pol. @ %0.2f MHz - LWA-1" % \
			(config['pol'], config['freq']/1e6))
		pylab.plot(lstList, powListAnt, "ro",label="Antenna Pattern")
		pylab.xlabel("LST [hours]")
		pylab.ylabel("Temp. [K]")
		pylab.grid(2)
		pylab.draw()
		pylab.show()
	
	outputFile = "driftcurve_%s_%s_%.2f.txt" % ('lwa1', config['pol'], config['freq']/1e6)
	print "Writing driftcurve to file '%s'" % outputFile
	mf = file(outputFile, "w")
	for lst,pow in zip(lstList, powListAnt):
		mf.write("%f  %f\n" % (lst,pow))
	mf.close()


if __name__ == '__main__':
	main(sys.argv[1:])
