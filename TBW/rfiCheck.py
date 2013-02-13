#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Script to take a single TBW capture and create a RFI-centered HDF5 file for stands 1, 10, 54, 
248, 251, and 258 (the outlier).  These stands correspond to the four corners of the array, the
center, and the outlier.  The HDF5 contains values for the spectral kurtosis estimated from
the data and various statistics about the timeseries (mean, std. dev., percentiles, etc.)

$Rev$
$LastChangedBy$
$LastChangedDate$
"""

import os
import sys
import math
import h5py
import numpy
import ephem
import getopt

from scipy.stats import scoreatpercentile as percentile

from lsl.common import stations
from lsl.reader import tbw, tbn
from lsl.reader import errors
from lsl.correlator import fx as fxc
from lsl.correlator._core import FEngineR2
from lsl.astro import unix_to_utcjd, DJD_OFFSET
from lsl.common.progress import ProgressBar
from lsl.statistics import kurtosis
from lsl.common.paths import data as dataPath

import matplotlib.pyplot as plt


def usage(exitCode=None):
	print """rfiCheck.py - Read in TBW files and create a collection of 
RFI statistics.

Usage: rfiCheck.py [OPTIONS] file

Options:
-h, --help                  Display this help information
-m, --metadata              Name of SSMIF file to use for mappings
-l, --fft-length            Set FFT length (default = 4096)
"""

	if exitCode is not None:
		sys.exit(exitCode)
	else:
		return True


def parseOptions(args):
	config = {}
	# Command line flags - default values
	config['SSMIF'] = ''
	config['force'] = False
	config['LFFT'] = 4096
	config['maxFrames'] = 30000*260
	config['applyGain'] = True
	config['verbose'] = True
	config['args'] = []

	# Read in and process the command line flags
	try:
		opts, args = getopt.getopt(args, "hm:fqtbnl:", ["help", "metadata=", "force", "quiet", "bartlett", "blackman", "hanning", "fft-length="])
	except getopt.GetoptError, err:
		# Print help information and exit:
		print str(err) # will print something like "option -a not recognized"
		usage(exitCode=2)
	
	# Work through opts
	for opt, value in opts:
		if opt in ('-h', '--help'):
			usage(exitCode=0)
		elif opt in ('-m', '--metadata'):
			config['SSMIF'] = value
		elif opt in ('-f', '--force'):
			config['force'] = True
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
		else:
			assert False
	
	# Add in arguments
	config['args'] = args

	# Return configuration
	return config


def expandMask(mask, radius=2, merge=False):
	"""
	Expand a 2-D numpy mask array around existing mask elements.
	"""
	
	mask2 = numpy.zeros(mask.shape, dtype=numpy.int16)
	for i in xrange(mask.shape[0]):
		for j in xrange(mask.shape[1]):
			if mask[i,j] == 1:
				for k in xrange(-radius,radius+1):
					try:
						mask2[i,j+k] = True
					except IndexError:
						pass
					
	if merge:
		mask3 = mask2.sum(axis=0)
		for i in xrange(mask.shape[1]):
			if mask3[i] > 0:
				mask2[:,i] = True
	
	mask2 = mask2.astype(numpy.bool)
	
	return mask2


def main(args):
	# Parse command line options
	config = parseOptions(args)
	
	# Set the station
	if config['SSMIF'] != '':
		station = stations.parseSSMIF(config['SSMIF'])
		ssmifContents = open(config['SSMIF']).readlines()
	else:
		station = stations.lwa1
		ssmifContents = open(os.path.join(dataPath, 'lwa1-ssmif.txt')).readlines()
	antennas = station.getAntennas()
	
	toKeep = []
	for g in (1, 10, 54, 248, 251, 258):
		for i,ant in enumerate(antennas):
			if ant.stand.id == g and ant.pol == 0:
				toKeep.append(i)
	for i,j in enumerate(toKeep):
		print i, j, antennas[j].stand.id

	# Length of the FFT
	LFFT = config['LFFT']

	# Make sure that the file chunk size contains is an integer multiple
	# of the FFT length so that no data gets dropped
	maxFrames = int(config['maxFrames']/float(LFFT))*LFFT
	# It seems like that would be a good idea, however...  TBW data comes one
	# capture at a time so doing something like this actually truncates data 
	# from the last set of stands for the first integration.  So, we really 
	# should stick with
	maxFrames = config['maxFrames']

	fh = open(config['args'][0], "rb")
	nFrames = os.path.getsize(config['args'][0]) / tbw.FrameSize
	dataBits = tbw.getDataBits(fh)
	# The number of ant/pols in the file is hard coded because I cannot figure out 
	# a way to get this number in a systematic fashion
	antpols = len(antennas)
	nChunks = int(math.ceil(1.0*nFrames/maxFrames))
	if dataBits == 12:
		nSamples = 400
	else:
		nSamples = 1200

	# Read in the first frame and get the date/time of the first sample 
	# of the frame.  This is needed to get the list of stands.
	junkFrame = tbw.readFrame(fh)
	fh.seek(0)
	beginTime = junkFrame.getTime()
	beginDate = ephem.Date(unix_to_utcjd(junkFrame.getTime()) - DJD_OFFSET)

	# File summary
	print "Filename: %s" % config['args'][0]
	print "Date of First Frame: %s" % str(beginDate)
	print "Ant/Pols: %i" % antpols
	print "Sample Length: %i-bit" % dataBits
	print "Frames: %i" % nFrames
	print "Chunks: %i" % nChunks
	print "==="

	nChunks = 1

	# Skip over any non-TBW frames at the beginning of the file
	i = 0
	junkFrame = tbw.readFrame(fh)
	while not junkFrame.header.isTBW():
		try:
			junkFrame = tbw.readFrame(fh)
		except errors.syncError:
			fh.seek(0)
			while True:
				try:
					junkFrame = tbn.readFrame(fh)
					i += 1
				except errors.syncError:
					break
			fh.seek(-2*tbn.FrameSize, 1)
			junkFrame = tbw.readFrame(fh)
		i += 1
	fh.seek(-tbw.FrameSize, 1)
	print "Skipped %i non-TBW frames at the beginning of the file" % i

	# Master loop over all of the file chunks
	masterSpectra = numpy.zeros((nChunks, antpols, LFFT-1))
	for i in range(nChunks):
		# Find out how many frames remain in the file.  If this number is larger
		# than the maximum of frames we can work with at a time (maxFrames),
		# only deal with that chunk
		framesRemaining = nFrames - i*maxFrames
		if framesRemaining > maxFrames:
			framesWork = maxFrames
		else:
			framesWork = framesRemaining
		print "Working on chunk %i, %i frames remaining" % ((i+1), framesRemaining)

		data = numpy.zeros((12, 12000000), dtype=numpy.int16)
		# If there are fewer frames than we need to fill an FFT, skip this chunk
		if data.shape[1] < 2*LFFT:
			break
		# Inner loop that actually reads the frames into the data array
		for j in range(framesWork):
			# Read in the next frame and anticipate any problems that could occur
			try:
				cFrame = tbw.readFrame(fh)
			except errors.eofError:
				break
			except errors.syncError:
				#print "WARNING: Mark 5C sync error on frame #%i" % (int(fh.tell())/tbw.FrameSize-1)
				continue
			if not cFrame.header.isTBW():
				continue
			
			stand = cFrame.header.parseID()
			# In the current configuration, stands start at 1 and go up to 10.  So, we
			# can use this little trick to populate the data array
			aStand = 2*(stand-1)
			#if cFrame.header.frameCount % 10000 == 0 and config['verbose']:
				#print "%3i -> %3i  %6.3f  %5i  %i" % (stand, aStand, cFrame.getTime(), cFrame.header.frameCount, cFrame.data.timeTag)

			# Actually load the data.  x pol goes into the even numbers, y pol into the 
			# odd numbers
			count = cFrame.header.frameCount - 1
			if aStand not in toKeep:
				continue
			
			# Convert to reduced index
			aStand = 2*toKeep.index(aStand)
			
			data[aStand,   count*nSamples:(count+1)*nSamples] = cFrame.data.xy[0,:]
			data[aStand+1, count*nSamples:(count+1)*nSamples] = cFrame.data.xy[1,:]
	
		# Time series analysis - mean, std. dev, saturation count
		tsMean = data.mean(axis=1)
		tsStd = data.std(axis=1)
		tsSat = numpy.where( (data == 2047) | (data == -2047), 1, 0 ).sum(axis=1)
		
		# Time series analysis - percentiles
		p = [50, 75, 90, 95, 99]
		tsPct = numpy.zeros((data.shape[0], len(p)))
		for i in xrange(len(p)):
			for j in xrange(data.shape[0]):
				tsPct[j,i] = percentile(numpy.abs(data[j,:]), p[i])
	
		# Frequency domain analysis - spectra
		freq = numpy.fft.fftfreq(2*config['LFFT'], d=1.0/196e6)
		freq = freq[1:config['LFFT']]
		
		delays = numpy.zeros((data.shape[0], freq.size))
		signalsF, validF = FEngineR2(data, freq, delays, LFFT=config['LFFT'], Overlap=1, SampleRate=196e6, ClipLevel=0)
		
		# Cleanup to save memory
		del validF, data
		print signalsF.shape
		
		# SK control values
		skM = signalsF.shape[2]
		skN = 1
		
		# Frequency domain analysis -  spectral kurtosis
		k = numpy.zeros((signalsF.shape[0], signalsF.shape[1]))
		for l in xrange(signalsF.shape[0]):
			for m in xrange(freq.size):
				k[l,m] = kurtosis.spectralFFT(signalsF[l,m,:])
		kl, kh = kurtosis.getLimits(4, skM, skN)
		print kl, kh
		
		# Integrate the spectra for as long as we can
		masterSpectra = (numpy.abs(signalsF)**2).mean(axis=2)
		del signalsF
		
		# Mask out bad values (high spectral kurtosis) for the plot
		mask = numpy.where( (k < kl) | ( k > kh), 1, 0 )
		mask = expandMask(mask, radius=4, merge=True)
		
		masterSpectra = numpy.ma.array(masterSpectra, mask=mask)
		
		# Save the data to an HDF5 file
		outname = os.path.splitext(config['args'][0])[0]
		outname = "%s-RFI.hdf5" % outname
		
		f = h5py.File(outname, 'w')
		f.attrs['filename'] = config['args'][0]
		f.attrs['mode'] = 'TBW'
		f.attrs['station'] = 'LWA-1'
		f.attrs['dataBits'] = dataBits
		f.attrs['startTime'] = beginTime
		f.attrs['startTime_units'] = 's'
		f.attrs['startTime_sys'] = 'unix'
		f.attrs['sampleRate'] = 196e6
		f.attrs['sampleRate_units'] = 'Hz'
		f.attrs['RBW'] = freq[1]-freq[0]
		f.attrs['RBW_Units'] = 'Hz'
		
		f.attrs['SK-M'] = skM
		f.attrs['SK-N'] = skN
		
		for l in xrange(len(toKeep)):
			antX = antennas[toKeep[l]]
			antY = antennas[toKeep[l]+1]
			
			stand = f.create_group('Stand%03i' % antX.stand.id)
			stand['freq'] = freq
			stand['freq'].attrs['Units'] = 'Hz'
			
			polX = stand.create_group('X')
			polY = stand.create_group('Y')
			polX.attrs['tsMean'] = tsMean[2*l]
			polY.attrs['tsMean'] = tsMean[2*l+1]
			polX.attrs['tsStd'] = tsStd[2*l]
			polY.attrs['tsStd'] = tsStd[2*l+1]
			polX.attrs['tsSat'] = tsSat[2*l]
			polY.attrs['tsSat'] = tsSat[2*l+1]
			for i,v in enumerate(p):
				polX.attrs['ts%02i' % v] = tsPct[2*l][i]
				polY.attrs['ts%02i' % v] = tsPct[2*l+1][i]
			
			polX['spectrum'] = masterSpectra[2*l,:]
			polX['spectrum'].attrs['axis0'] = 'frequency'
			polY['spectrum'] = masterSpectra[2*l+1,:]
			polY['spectrum'].attrs['axis0'] = 'frequency'
			
			polX['kurtosis'] = k[2*l,:]
			polX['kurtosis'].attrs['axis0'] = 'frequency'
			polY['kurtosis'] = k[2*l+1,:]
			polY['kurtosis'].attrs['axis0'] = 'frequency'
		
		# The plot
		fig = plt.figure()
		ax1 = fig.add_subplot(2, 1, 1)
		ax2 = fig.add_subplot(2, 1, 2)
		for l in xrange(k.shape[0]):
			ant = antennas[toKeep[l/2]]
			
			ax1.plot(freq/1e6, numpy.log10(masterSpectra[l,:])*10, label='Stand %i, Pol %i' % (ant.stand.id, ant.pol+l%2))
			
			ax2.plot(freq/1e6, k[l,:], label='Stand %i, Pol %i' % (ant.stand.id, ant.pol+l%2))
			
		ax2.hlines(kl, freq[0]/1e6, freq[-1]/1e6, linestyle=':', label='Kurtosis Limit 4$\sigma$')
		ax2.hlines(kh, freq[0]/1e6, freq[-1]/1e6, linestyle=':', label='Kurtosis Limit 4$\sigma$')
		
		ax1.set_xlabel('Frequency [MHz]')
		ax1.set_ylabel('PSD [arb. dB/RBW]')
		ax1.legend(loc=0)
		
		ax2.set_ylim((kl/2, kh*2))
		ax2.set_xlabel('Frequency [MHz]')
		ax2.set_ylabel('Spectral Kurtosis')
		ax2.legend(loc=0)
		
		plt.show()
		
		


if __name__ == "__main__":
	main(sys.argv[1:])
	
