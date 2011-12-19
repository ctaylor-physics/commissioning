#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Split multi-vis NPZ files that are generated from combined TBN observations into
single NPZ files, one for each frequency.
"""

import os
import sys
import numpy

from datetime import datetime


def main(args):
	filename = args[0]
	dataDict = numpy.load(filename)
	
	print "Working on file '%s'" % filename

	# Load in the data
	refAnt = dataDict['ref'].item()
	refX   = dataDict['refX'].item()
	refY   = dataDict['refY'].item()
	tInt = dataDict['tInt'].item()
	
	times = dataDict['times']
	phase = dataDict['simpleVis']
	
	centralFreqs = dataDict['centralFreqs']

	# Find the unique sets of frequencies and report
	uFreq = numpy.unique(centralFreqs)
	print "  Found %i unique frequencies from %.3f to %.3f MHz" % (len(uFreq), uFreq.min()/1e6, uFreq.max()/1e6)
	
	# Report on the start time
	beginDate = datetime.utcfromtimestamp(times[0])
	print "  Start date/time of data: %s UTC" % beginDate
	
	# Split
	for i,f in enumerate(uFreq):
		## Select what we need and trim off the last index to deal 
		## with frequency changes
		toKeep = numpy.where( centralFreqs == f )[0]
		toKeep = toKeep[:-1]
		
		## Sub-sets of `times` and `phase`
		subTimes = times[toKeep]
		subPhase = phase[toKeep,:]
		
		if len(toKeep) < 20:
			print "  -> Skipping %.3f MHz with only %i integrations" % (f, len(toKeep))
			continue
		
		## Save the split data to its own file
		outname = filename.replace('.npz', '-%03i.npz' % (i+1,))
		numpy.savez(outname, ref=refAnt, refX=refX, refY=refY, tInt=tInt, centralFreq=f, 
					times=subTimes, simpleVis=subPhase)


if __name__ == "__main__":
	main(sys.argv[1:])
	