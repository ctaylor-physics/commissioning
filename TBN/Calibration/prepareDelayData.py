#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Aggregate a collection of complex visibility NPZ files generated by simpleFringe.py
or simpleFringeDemux.py/splitMultiVis.py together to make delay fitting a little 
easier.

Note:  The output of this script is always saved to an NPZ file named 
       prepared-dat-stopped-ref###.npz

Usage:
./prepareDelayData.py <reference source> <NPZ vis. file> [<NPZ vis. file> [...]]

$Rev$
$LastChangedBy$
$LastChangedDate$
"""

import os
import sys
import ephem
import numpy
import getopt
import tempfile

from hashlib import md5
from datetime import datetime

from scipy.optimize import leastsq, fmin
from scipy.stats import pearsonr

from lsl.common.constants import c as vLight
from lsl.astro import unix_to_utcjd, utcjd_to_unix
from lsl.common.stations import parseSSMIF, lwa1
from lsl.correlator.uvUtils import computeUVW
from lsl.misc.mathutil import to_dB
from lsl.statistics import robust
from lsl.common.progress import ProgressBar

import lsl.sim.vis as simVis

# List of bright radio sources and pulsars in PyEphem format
_srcs = ["ForA,f|J,03:22:41.70,-37:12:30.0,1",
         "TauA,f|J,05:34:32.00,+22:00:52.0,1", 
         "VirA,f|J,12:30:49.40,+12:23:28.0,1",
         "HerA,f|J,16:51:08.15,+04:59:33.3,1", 
         "SgrA,f|J,17:45:40.00,-29:00:28.0,1", 
         "CygA,f|J,19:59:28.30,+40:44:02.0,1", 
         "CasA,f|J,23:23:27.94,+58:48:42.4,1",]


def usage(exitCode=None):
	print """prepareDelayData.py - Aggregate a collection of complex visibility together to make
delay fitting a little easier.

Usage: prepareDelayData [OPTIONS] ref_source file [file [...]]

Options:
-h, --help            Display this help information
-o, --output          Name for the final NPZ file (default = 
                      'prepared-dat-stopped.npz')
"""

	if exitCode is not None:
		sys.exit(exitCode)
	else:
		return True


def parseOptions(args):
	config = {}
	# Command line flags - default values
	config['output'] = 'prepared-dat-stopped.npz'
	
	# Read in and process the command line flags
	try:
		opts, args = getopt.getopt(args, "ho:", ["help", "output="])
	except getopt.GetoptError, err:
		# Print help information and exit:
		print str(err) # will print something like "option -a not recognized"
		usage(exitCode=2)
	
	# Work through opts
	for opt, value in opts:
		if opt in ('-h', '--help'):
			usage(exitCode=0)
		elif opt in ('-o', '--output'):
			config['output'] = value
		else:
			assert False
	
	# Add in arguments
	config['args'] = args

	# Return configuration
	return config


def md5sum(ssmifContents):
	m = md5()
	for line in ssmifContents:
		m.update(line)
	return m.hexdigest()


def getGeoDelay(antenna1, antenna2, az, el, Degrees=False):
	"""
	Get the geometrical delay (relative to a second antenna) for the 
	specified antenna for a source at azimuth az, elevation el.
	"""

	if Degrees:
		az = az*numpy.pi/180.0
		el = el*numpy.pi/180.0
	
	source = numpy.array([numpy.cos(el)*numpy.sin(az), 
					  numpy.cos(el)*numpy.cos(az), 
					  numpy.sin(el)])
	
	xyz1 = numpy.array([antenna1.stand.x, antenna1.stand.y, antenna1.stand.z])
	xyz2 = numpy.array([antenna2.stand.x, antenna2.stand.y, antenna2.stand.z])
	xyzRel = xyz1 - xyz2
	
	return numpy.dot(source, xyzRel) / vLight


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
	uvw = computeUVW([antenna1, antenna2], HA=HA, dec=dec, freq=freq)
	
	return -(2*numpy.pi/86164.0905)*uvw[0,0,0]*numpy.cos(src.dec)


def main(args):
	config = parseOptions(args)
	
	reference = config['args'][0]
	filenames = config['args'][1:]

	#
	# Gather the station meta-data from its various sources
	#
	dataDict = numpy.load(filenames[0])
	ssmifContents = dataDict['ssmifContents']
	if ssmifContents.shape == ():
		site = lwa1
	else:
		fh, tempSSMIF = tempfile.mkstemp(suffix='.txt', prefix='ssmif-')
		fh = open(tempSSMIF, 'w')
		for line in ssmifContents:
			fh.write('%s\n' % line)
		fh.close()
		
		site = parseSSMIF(tempSSMIF)
		os.unlink(tempSSMIF)
	observer = site.getObserver()
	antennas = site.getAntennas()
	nAnts = len(antennas)
	
	#
	# Find the reference source
	#
	srcs = [ephem.Sun(),]
	for line in _srcs:
		srcs.append( ephem.readdb(line) )

	refSrc = None
	for i in xrange(len(srcs)):
		if srcs[i].name == reference:
			refSrc = srcs[i]
			
	if refSrc is None:
		print "Cannot find reference source '%s' in source list, aborting." % reference
		sys.exit(1)
	
	#
	# Parse the input files
	#
	data = []
	time = []
	freq = []
	oldRef = None
	oldMD5 = None
	maxTime = -1
	for filename in filenames:
		dataDict = numpy.load(filename)
		
		refAnt = dataDict['ref'].item()
		refX   = dataDict['refX'].item()
		refY   = dataDict['refY'].item()
		tInt = dataDict['tInt'].item()
		
		times = dataDict['times']
		phase = dataDict['simpleVis']
		
		centralFreq = dataDict['centralFreq'].item()

		ssmifContents = dataDict['ssmifContents']
		
		beginDate = datetime.utcfromtimestamp(times[0])
		observer.date = beginDate.strftime("%Y/%m/%d %H:%M:%S")

		# Make sure we aren't mixing reference antennas
		if oldRef is None:
			oldRef = refAnt
		if refAnt != oldRef:
			raise RuntimeError("Dataset has different reference antennas than previous (%i != %i)" % (refAnt, oldRef))

		# Make sure we aren't mixing SSMIFs
		ssmifMD5 = md5sum(ssmifContents)
		if oldMD5 is None:
			oldMD5 = ssmifMD5
		if ssmifMD5 != oldMD5:
			raise RuntimeError("Dataset has different SSMIF than previous (%s != %s)" % (ssmifMD5, oldMD5))
			
		print "Central Frequency: %.3f Hz" % centralFreq
		print "Start date/time: %s" % beginDate.strftime("%Y/%m/%d %H:%M:%S")
		print "Integration Time: %.3f s" % tInt
		print "Number of time samples: %i (%.3f s)" % (phase.shape[0], phase.shape[0]*tInt)
		
		allRates = {}
		for src in srcs:
			src.compute(observer)
			if src.alt > 0:
				fRate = getFringeRate(antennas[0], antennas[refX], observer, src, centralFreq)
				allRates[src.name] = fRate
		# Calculate the fringe rates of all sources - for display purposes only
		print "Starting Fringe Rates:"
		for name,fRate in allRates.iteritems():
			print " %-4s: %+6.3f mHz" % (name, fRate*1e3)
			
		freq.append( centralFreq )
		time.append( numpy.array([unix_to_utcjd(t) for t in times]) )
		data.append( phase )
		
		## Save the length of the `time` entry so that we can trim them
		## all down to the same size later
		if time[-1].size > maxTime:
			maxTime = time[-1].size
	
	# Pad with NaNs to the same length
	for i in xrange(len(filenames)):
		nTimes = time[i].size
		
		if nTimes < maxTime:
			## Pad 'time'
			newTime = numpy.zeros(maxTime, dtype=time[i].dtype)
			newTime += numpy.nan
			newTime[0:nTimes] = time[i][:]
			time[i] = newTime
			
			## Pad 'data'
			newData = numpy.zeros((maxTime, data[i].shape[1]), dtype=data[i].dtype)
			newData += numpy.nan
			newData[0:nTimes,:] = data[i][:,:]
			data[i] = newData
	
	# Convert to 2-D and 3-D numpy arrays
	freq = numpy.array(freq)
	time = numpy.array(time)
	data = numpy.array(data)
	
	#
	# Sort the data by frequency
	#
	order = numpy.argsort(freq)
	freq = numpy.take(freq, order)
	time = numpy.take(time, order, axis=0)
	data = numpy.take(data, order, axis=0)
	
	# 
	# Find the fringe stopping averaging times
	#
	ls = {}
	for fStart in xrange(20, 90, 5):
		fStop = fStart + 5
		l = numpy.where( (freq >= fStart*1e6) & (freq < fStop*1e6) )[0]
		if len(l) > 0:
			ls[fStart] = l
			
	ms = {}
	for fStart in ls.keys():
		m = 1e6
		for l in ls[fStart]:
			good = numpy.where( numpy.isfinite(time[l,:]) == 1 )[0]
			if len(good) < m:
				m = len(good)
		ms[fStart] = m
			
	print "Minimum fringe stopping times:"
	for fStart in sorted(ls.keys()):
		fStop = fStart + 5
		m = ms[fStart]
		print "  >=%i Mhz and <%i MHz: %.3f s" % (fStart, fStop, m*tInt,)
		
	#
	# Report on progress and data coverage
	#
	nFreq = len(freq)
	
	print "Reference stand #%i (X: %i, Y: %i)" % (refAnt, refX, refY)
	print "-> X: %s" % str(antennas[refX])
	print "-> Y: %s" % str(antennas[refY])
	
	print "Using a set of %i frequencies" % nFreq
	print "->", freq/1e6
	
	#
	# Compute source positions/fringe stop and remove the source
	#
	print "Fringe stopping on '%s':" % refSrc.name
	pbar = ProgressBar(max=freq.size*520)

	
	for i in xrange(freq.size):
		fq = freq[i]
		
		for j in xrange(data.shape[2]):
			# Compute the times in seconds relative to the beginning
			times  = time[i,:] - time[i,0]
			times *= 24.0
			times *= 3600.0
			
			# Compute the fringe rates across all time
			fRate = [None,]*data.shape[1]
			for k in xrange(data.shape[1]):
				jd = time[i,k]
				
				try:
					currDate = datetime.utcfromtimestamp(utcjd_to_unix(jd))
				except ValueError:
					pass
				observer.date = currDate.strftime("%Y/%m/%d %H:%M:%S")
				refSrc.compute(observer)
		
				if j % 2 == 0:
					fRate[k] = getFringeRate(antennas[j], antennas[refX], observer, refSrc, fq)
				else:
					fRate[k] = getFringeRate(antennas[j], antennas[refY], observer, refSrc, fq)
		
			# Create the basis rate and the residual rates
			baseRate = fRate[0]
			residRate = numpy.array(fRate) - baseRate
		
			#import pylab
			#f = numpy.fft.fft(data[i,:,j])
			#pylab.plot(numpy.abs(f))
		
			# Fringe stop to more the source of interest to the DC component
			data[i,:,j] *= numpy.exp(-2j*numpy.pi* baseRate*(times - times[0]))
			data[i,:,j] *= numpy.exp(-2j*numpy.pi*residRate*(times - times[0]))
			
			#f = numpy.fft.fft(data[i,:,j])
			#pylab.plot(numpy.abs(f))
			#pylab.show()
			
			# Calculate the geometric delay term at the start of the data
			jd = time[i,0]
			
			try:
				currDate = datetime.utcfromtimestamp(utcjd_to_unix(jd))
			except ValueError:
				pass
			observer.date = currDate.strftime("%Y/%m/%d %H:%M:%S")
			refSrc.compute(observer)
			
			az = refSrc.az
			el = refSrc.alt
			if j % 2 == 0:
				gd = getGeoDelay(antennas[j], antennas[refX], az, el, Degrees=False)
			else:
				gd = getGeoDelay(antennas[j], antennas[refY], az, el, Degrees=False)
			
			# Remove the array geometry
			data[i,:,j] *= numpy.exp(-2j*numpy.pi*fq*gd)
			
			pbar.inc()
			sys.stdout.write("%s\r" % pbar.show())
			sys.stdout.flush()
	sys.stdout.write('\n')
	
	# Average down to remove other sources/the correlated sky
	print "Input (pre-averaging) data shapes:"
	print "  time:", time.shape
	print "  data:", data.shape
	time = time[:,0]
	
	data2 = numpy.zeros((data.shape[0], data.shape[2]), dtype=data.dtype)
	for j in xrange(data2.shape[1]):
		for fStart in ls.keys():
			l = ls[fStart]
			m = ms[fStart]
			data2[l,j] = data[l,:m,j].mean(axis=1)
	data = data2
	print "Output (post-averaging) data shapes:"
	print "  time:", time.shape
	print "  data:", data.shape

	#
	# Save
	#
	outname = config['output']
	outname, ext = os.path.splitext(outname)
	outname = "%s-ref%03i%s" % (outname, refAnt, ext)
	numpy.savez(outname, refAnt=refAnt, refX=refX, refY=refY, freq=freq, time=time, data=data, ssmifContents=ssmifContents)


if __name__ == "__main__":
	main(sys.argv[1:])
