#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Given a DRX file, plot the time averaged spectra for each beam output over some 
period.

$Rev$
$LastChangedBy$
$LastChangedDate$
"""

import os
import sys
import h5py
import math
import numpy
import ephem
import getopt

import lsl.common.stations as stations
import lsl.reader.drx as drx
import lsl.reader.errors as errors
import lsl.statistics.robust as robust
import lsl.correlator.fx as fxc
from lsl.astro import unix_to_utcjd, DJD_OFFSET

import matplotlib.pyplot as plt


def usage(exitCode=None):
	print """hdfWaterfall.py - Read in DRX files and create a collection of 
time-averaged spectra.  These spectra are saved to a HDF5 file called <filename>-waterfall.hdf5.

Usage: hdfWaterfall.py [OPTIONS] file

Options:
-h, --help                  Display this help information
-t, --bartlett              Apply a Bartlett window to the data
-b, --blackman              Apply a Blackman window to the data
-n, --hanning               Apply a Hanning window to the data
-s, --skip                  Skip the specified number of seconds at the beginning
                            of the file (default = 0)
-a, --average               Number of seconds of data to average for spectra 
                            (default = 1)
-d, --duration              Number of seconds to calculate the waterfall for 
                            (default = 10)
-q, --quiet                 Run drxSpectra in silent mode and do not show the plots
-l, --fft-length            Set FFT length (default = 4096)
-c, --clip-level            FFT blanking clipping level in counts (default = 0, 
                            0 disables)
-e, --estimate-clip         Use robust statistics to estimate an approprite clip 
                            level (overrides the `-c` option)
-o, --output                Output file name for waterfall image
"""

	if exitCode is not None:
		sys.exit(exitCode)
	else:
		return True


def parseOptions(args):
	config = {}
	# Command line flags - default values
	config['offset'] = 0.0
	config['average'] = 1.0
	config['LFFT'] = 4096
	config['freq1'] = 0
	config['freq2'] = 0
	config['maxFrames'] = 28000
	config['window'] = fxc.noWindow
	config['output'] = None
	config['duration'] = 10.0
	config['verbose'] = True
	config['clip'] = 0
	config['estimate'] = False
	config['args'] = []

	# Read in and process the command line flags
	try:
		opts, args = getopt.getopt(args, "hqtbnl:o:s:a:d:c:e", ["help", "quiet", "bartlett", "blackman", "hanning", "fft-length=", "output=", "skip=", "average=", "duration=", "freq1=", "freq2=", "clip-level=", "estimate-clip"])
	except getopt.GetoptError, err:
		# Print help information and exit:
		print str(err) # will print something like "option -a not recognized"
		usage(exitCode=2)
	
	# Work through opts
	for opt, value in opts:
		if opt in ('-h', '--help'):
			usage(exitCode=0)
		elif opt in ('-q', '--quiet'):
			config['verbose'] = False
		elif opt in ('-t', '--bartlett'):
			config['window'] = numpy.bartlett
		elif opt in ('-b', '--blackman'):
			config['window'] = numpy.blackman
		elif opt in ('-n', '--hanning'):
			config['window'] = numpy.hanning
		elif opt in ('-l', '--fft-length'):
			config['LFFT'] = int(value)
		elif opt in ('-o', '--output'):
			config['output'] = value
		elif opt in ('-s', '--skip'):
			config['offset'] = float(value)
		elif opt in ('-a', '--average'):
			config['average'] = float(value)
		elif opt in ('-d', '--duration'):
			config['duration'] = float(value)
		elif opt in ('-c', '--clip-level'):
			config['clip'] = int(value)
		elif opt in ('-e', '--estimate-clip'):
			config['estimate'] = True
		else:
			assert False
	
	# Add in arguments
	config['args'] = args

	# Return configuration
	return config


def bestFreqUnits(freq):
	"""Given a numpy array of frequencies in Hz, return a new array with the
	frequencies in the best units possible (kHz, MHz, etc.)."""

	# Figure out how large the data are
	scale = int(math.log10(freq.max()))
	if scale >= 9:
		divis = 1e9
		units = 'GHz'
	elif scale >= 6:
		divis = 1e6
		units = 'MHz'
	elif scale >= 3:
		divis = 1e3
		units = 'kHz'
	else:
		divis = 1
		units = 'Hz'

	# Convert the frequency
	newFreq = freq / divis

	# Return units and freq
	return (newFreq, units)


def main(args):
	# Parse command line options
	config = parseOptions(args)

	# Length of the FFT
	LFFT = config['LFFT']

	fh = open(config['args'][0], "rb")
	nFramesFile = os.path.getsize(config['args'][0]) / drx.FrameSize

	while True:
		try:
			junkFrame = drx.readFrame(fh)
			try:
				srate = junkFrame.getSampleRate()
				t0 = junkFrame.getTime()
				break
			except ZeroDivisionError:
				pass
		except errors.syncError:
			fh.seek(-drx.FrameSize+1, 1)
			
	fh.seek(-drx.FrameSize, 1)

	beam, tune, pol = junkFrame.parseID()
	beams = drx.getBeamCount(fh)
	tunepols = drx.getFramesPerObs(fh)
	tunepol = tunepols[0] + tunepols[1] + tunepols[2] + tunepols[3]
	beampols = tunepol

	# Offset in frames for beampols beam/tuning/pol. sets
	offset = int(config['offset'] * srate / 4096 * beampols)
	offset = int(1.0 * offset / beampols) * beampols
	config['offset'] = 1.0 * offset / beampols * 4096 / srate
	fh.seek(offset*drx.FrameSize, 1)

	# Iterate on the offsets until we reach the right point in the file.  This
	# is needed to deal with files that start with only one tuning and/or a 
	# different sample rate.  
	while True:
		## Figure out where in the file we are and what the current tuning/sample 
		## rate is
		junkFrame = drx.readFrame(fh)
		srate = junkFrame.getSampleRate()
		t1 = junkFrame.getTime()
		tunepols = drx.getFramesPerObs(fh)
		tunepol = tunepols[0] + tunepols[1] + tunepols[2] + tunepols[3]
		beampols = tunepol
		fh.seek(-drx.FrameSize, 1)
		
		## See how far off the current frame is from the target
		tDiff = t1 - (t0 + config['offset'])
		
		## Half that to come up with a new seek parameter
		tCorr = -tDiff / 2.0
		cOffset = int(tCorr * srate / 4096 * beampols)
		cOffset = int(1.0 * cOffset / beampols) * beampols
		offset += cOffset
		
		## If the offset is zero, we are done.  Otherwise, apply the offset
		## and check the location in the file again/
		if cOffset is 0:
			break
		fh.seek(cOffset*drx.FrameSize, 1)
	
	# Update the offset actually used
	config['offset'] = t1 - t0
	offset = int(round(config['offset'] * srate / 4096 * beampols))
	offset = int(1.0 * offset / beampols) * beampols

	# Make sure that the file chunk size contains is an integer multiple
	# of the FFT length so that no data gets dropped.  This needs to
	# take into account the number of beampols in the data, the FFT length,
	# and the number of samples per frame.
	maxFrames = int(1.0*config['maxFrames']/beampols*4096/float(LFFT))*LFFT/4096*beampols

	# Number of frames to integrate over
	nFramesAvg = int(config['average'] * srate / 4096 * beampols)
	nFramesAvg = int(1.0 * nFramesAvg / beampols*4096/float(LFFT))*LFFT/4096*beampols
	config['average'] = 1.0 * nFramesAvg / beampols * 4096 / srate
	maxFrames = nFramesAvg
	
	# Make the pseudo-antennas for Stokes calculation
	antennas = []
	for i in xrange(beampols):
		if i / 2 == 0:
			newAnt = stations.Antenna(1)
		else:
			newAnt = stations.Antenna(2)
			
		if i % 2 == 0:
			newAnt.pol = 0
		else:
			newAnt.pol = 1
			
		antennas.append(newAnt)
		print antennas[-1]

	# Number of remaining chunks (and the correction to the number of
	# frames to read in).
	nChunks = int(round(config['duration'] / config['average']))
	if nChunks == 0:
		nChunks = 1
	nFrames = nFramesAvg*nChunks
	
	# Date & Central Frequnecy
	beginDate = ephem.Date(unix_to_utcjd(junkFrame.getTime()) - DJD_OFFSET)
	centralFreq1 = 0.0
	centralFreq2 = 0.0
	for i in xrange(4):
		junkFrame = drx.readFrame(fh)
		b,t,p = junkFrame.parseID()
		if p == 0 and t == 1:
			try:
				centralFreq1 = junkFrame.getCentralFreq()
			except AttributeError:
				from lsl.common.dp import fS
				centralFreq1 = fS * ((junkFrame.data.flags>>32) & (2**32-1)) / 2**32
		elif p == 0 and t == 2:
			try:
				centralFreq2 = junkFrame.getCentralFreq()
			except AttributeError:
				from lsl.common.dp import fS
				centralFreq2 = fS * ((junkFrame.data.flags>>32) & (2**32-1)) / 2**32
		else:
			pass
	fh.seek(-4*drx.FrameSize, 1)
	
	config['freq1'] = centralFreq1
	config['freq2'] = centralFreq2

	# File summary
	print "Filename: %s" % config['args'][0]
	print "Date of First Frame: %s" % str(beginDate)
	print "Beams: %i" % beams
	print "Tune/Pols: %i %i %i %i" % tunepols
	print "Sample Rate: %i Hz" % srate
	print "Tuning Frequency: %.3f Hz (1); %.3f Hz (2)" % (centralFreq1, centralFreq2)
	print "Frames: %i (%.3f s)" % (nFramesFile, 1.0 * nFramesFile / beampols * 4096 / srate)
	print "---"
	print "Offset: %.3f s (%i frames)" % (config['offset'], offset)
	print "Integration: %.3f s (%i frames; %i frames per beam/tune/pol)" % (config['average'], nFramesAvg, nFramesAvg / beampols)
	print "Duration: %.3f s (%i frames; %i frames per beam/tune/pol)" % (config['average']*nChunks, nFrames, nFrames / beampols)
	print "Chunks: %i" % nChunks

	# Sanity check
	if nFrames > (nFramesFile - offset):
		raise RuntimeError("Requested integration time+offset is greater than file length")
	
	# Estimate clip level (if needed)
	if config['estimate']:
		filePos = fh.tell()
		
		# Read in the first 100 frames for each tuning/polarization
		count = {0:0, 1:0, 2:0, 3:0}
		data = numpy.zeros((4, 4096*100), dtype=numpy.csingle)
		for i in xrange(beampols*100):
			try:
				cFrame = drx.readFrame(fh, Verbose=False)
			except errors.eofError:
				break
			except errors.syncError:
				continue
			
			beam,tune,pol = cFrame.parseID()
			aStand = 2*(tune-1) + pol
			
			data[aStand, count[aStand]*4096:(count[aStand]+1)*4096] = cFrame.data.iq
			count[aStand] +=  1
		
		# Go back to where we started
		fh.seek(filePos)
		
		# Compute the robust mean and standard deviation for I and Q for each
		# tuning/polarization
		meanI = []
		meanQ = []
		stdsI = []
		stdsQ = []
		for i in xrange(4):
			meanI.append( robust.mean(data[i,:].real) )
			meanQ.append( robust.mean(data[i,:].imag) )
			
			stdsI.append( robust.std(data[i,:].real) )
			stdsQ.append( robust.std(data[i,:].imag) )
		
		# Report
		print "Statistics:"
		for i in xrange(4):
			print " Mean %i: %.3f + %.3f j" % (i+1, meanI[i], meanQ[i])
			print " Std  %i: %.3f + %.3f j" % (i+1, stdsI[i], stdsQ[i])
		
		# Come up with the clip levels based on 4 sigma
		clip1 = (meanI[0] + meanI[1] + meanQ[0] + meanQ[1]) / 4.0
		clip2 = (meanI[2] + meanI[3] + meanQ[2] + meanQ[3]) / 4.0
		
		clip1 += 5*(stdsI[0] + stdsI[1] + stdsQ[0] + stdsQ[1]) / 4.0
		clip2 += 5*(stdsI[2] + stdsI[3] + stdsQ[2] + stdsQ[3]) / 4.0
		
		clip1 = int(round(clip1))
		clip2 = int(round(clip2))
		
		# Report again
		print "Clip Levels:"
		print " Tuning 1: %i" % clip1
		print " Tuning 2: %i" % clip2
		
	else:
		clip1 = config['clip']
		clip2 = config['clip']
	
	# Setup the output file
	outname = config['args'][0].replace('.dat', '-waterfall.hdf5')
	
	f = h5py.File(outname, 'w')
	f.attrs['Beam'] = beam
	f.attrs['tInt'] = (maxFrames*4096/beampols/srate)
	f.attrs['tInt_Units'] = 's'
	f.attrs['sampleRate'] = srate
	f.attrs['sampleRate_Units'] = 'samples/s'
	freq = numpy.fft.fftshift( numpy.fft.fftfreq(LFFT, d=1.0/srate) )
	freq = freq[1:].astype(numpy.float64)
	f.attrs['RBW'] = freq[1]-freq[0]
	f.attrs['RBW_Units'] = 'Hz'
	masterTimes = f.create_dataset('time', (nChunks,), 'f8')
	
	tuning1 = f.create_group('/Tuning1')
	tuning1['freq'] = freq + config['freq1']
	tuning1['freq'].attrs['Units'] = 'Hz'
	spec1I = tuning1.create_dataset('I', (nChunks, LFFT-1), 'f4')
	tuning1['I'].attrs['axis0'] = 'time'
	tuning1['I'].attrs['axis1'] = 'frequency'
	spec1Q = tuning1.create_dataset('Q', (nChunks, LFFT-1), 'f4')
	tuning1['Q'].attrs['axis0'] = 'time'
	tuning1['Q'].attrs['axis1'] = 'frequency'
	spec1U = tuning1.create_dataset('U', (nChunks, LFFT-1), 'f4')
	tuning1['U'].attrs['axis0'] = 'time'
	tuning1['U'].attrs['axis1'] = 'frequency'
	spec1V = tuning1.create_dataset('V', (nChunks, LFFT-1), 'f4')
	tuning1['V'].attrs['axis0'] = 'time'
	tuning1['V'].attrs['axis1'] = 'frequency'
	
	tuning2 = f.create_group('/Tuning2')
	tuning2['freq'] = freq + config['freq2']
	tuning2['freq'].attrs['Units'] = 'Hz'
	spec2I = tuning2.create_dataset('I', (nChunks, LFFT-1), 'f4')
	tuning2['I'].attrs['axis0'] = 'time'
	tuning2['I'].attrs['axis1'] = 'frequency'
	spec2Q = tuning2.create_dataset('Q', (nChunks, LFFT-1), 'f4')
	tuning2['Q'].attrs['axis0'] = 'time'
	tuning2['Q'].attrs['axis1'] = 'frequency'
	spec2U = tuning2.create_dataset('U', (nChunks, LFFT-1), 'f4')
	tuning2['U'].attrs['axis0'] = 'time'
	tuning2['U'].attrs['axis1'] = 'frequency'
	spec2V = tuning2.create_dataset('V', (nChunks, LFFT-1), 'f4')
	tuning2['V'].attrs['axis0'] = 'time'
	tuning2['V'].attrs['axis1'] = 'frequency'

	# Master loop over all of the file chunks
	masterSpectra = [[spec1I, spec1Q, spec1U, spec1V], [spec2I, spec2Q, spec2U, spec2V]]
	for i in xrange(nChunks):
		# Find out how many frames remain in the file.  If this number is larger
		# than the maximum of frames we can work with at a time (maxFrames),
		# only deal with that chunk
		framesRemaining = nFrames - i*maxFrames
		if framesRemaining > maxFrames:
			framesWork = maxFrames
		else:
			framesWork = framesRemaining
		print "Working on chunk %i, %i frames remaining" % (i+1, framesRemaining)
		
		count = {0:0, 1:0, 2:0, 3:0}
		data = numpy.zeros((4,framesWork*4096/beampols), dtype=numpy.csingle)
		# If there are fewer frames than we need to fill an FFT, skip this chunk
		if data.shape[1] < LFFT:
			break

		# Inner loop that actually reads the frames into the data array
		print "Working on %.1f ms of data" % ((framesWork*4096/beampols/srate)*1000.0)

		for j in xrange(framesWork):
			# Read in the next frame and anticipate any problems that could occur
			try:
				cFrame = drx.readFrame(fh, Verbose=False)
			except errors.eofError:
				break
			except errors.syncError:
				continue

			beam,tune,pol = cFrame.parseID()
			aStand = 2*(tune-1) + pol
			if j is 0:
				cTime = cFrame.getTime()
			
			data[aStand, count[aStand]*4096:(count[aStand]+1)*4096] = cFrame.data.iq
			count[aStand] +=  1

		# Calculate the spectra for this block of data and then weight the results by 
		# the total number of frames read.  This is needed to keep the averages correct.
		if clip1 == clip2:
			freq, tempSpec1 = fxc.StokesMaster(data, antennas, LFFT=LFFT, window=config['window'], verbose=config['verbose'], SampleRate=srate, ClipLevel=clip1)
			
			# Save the results to the various master arrays
			masterTimes[i] = cTime
			
			for s in xrange(tempSpec1.shape[1]):
				masterSpectra[s][0][i,:] = tempSpec1[0,s,:]
				masterSpectra[s][1][i,:] = tempSpec1[1,s,:]
				masterSpectra[s][2][i,:] = tempSpec1[2,s,:]
				masterSpectra[s][3][i,:] = tempSpec1[3,s,:]
			
		else:
			freq, tempSpec1 = fxc.StokesMaster(data[:2,:], antennas[:2], LFFT=LFFT, window=config['window'], verbose=config['verbose'], SampleRate=srate, ClipLevel=clip1)
			
			freq, tempSpec2 = fxc.StokesMaster(data[2:,:], antennas[2:],  LFFT=LFFT, window=config['window'], verbose=config['verbose'], SampleRate=srate, ClipLevel=clip2)
			
			# Save the results to the various master arrays
			masterTimes[i] = cTime
			
			# Tuning 1 - I, Q, U, and V
			masterSpectra[1][0][i,:] = tempSpec1[0,0,:]
			masterSpectra[1][1][i,:] = tempSpec1[1,0,:]
			masterSpectra[1][2][i,:] = tempSpec1[2,0,:]
			masterSpectra[1][3][i,:] = tempSpec1[3,0,:]
			
			# Tuning 2 - I, Q, U, and V
			masterSpectra[1][0][i,:] = tempSpec2[0,0,:]
			masterSpectra[1][1][i,:] = tempSpec2[1,0,:]
			masterSpectra[1][2][i,:] = tempSpec2[2,0,:]
			masterSpectra[1][3][i,:] = tempSpec2[3,0,:]

		# We don't really need the data array anymore, so delete it
		del(data)

	# Save the output to a HDF5 file
	f.close()


if __name__ == "__main__":
	main(sys.argv[1:])
