#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import numpy
import getopt

from lsl.misc import beamformer
from lsl.common.stations import parseSSMIF

def usage(exitCode=None):
	print """estimateBeam.py - Read in SSMIF file and estimate the DRX beam 
for a given frequency and topogentric pointing center.  The estimate is based 
off a simple delay-and-sum beam former so it won't be an exact match.

Usage: generateDelays.py [OPTIONS] file

Options:
-h, --help                  Display this help information
-f, --frequency             Frequency in MHz to calculate the gain/delays for 
                            (Default = 65 MHz)
-a, --azimuth               Azimuth east of north in degrees for the pointing center
                            (Default = 90 degrees)
-e, --elevation             Elevation above the horizon in degrees for the pointing
                            center (Default = 90 degrees)
"""
	if exitCode is not None:
		sys.exit(exitCode)
	else:
		return True


def parseOptions(args):
	config = {}
	# Command line flags - default values
	config['freq'] = 65.0e6
	config['az'] = 90.0
	config['el'] = 90.0
	config['args'] = []

	# Read in and process the command line flags
	try:
		opts, args = getopt.getopt(args, "hf:a:e:", ["help", "frequency=", "azimuth=", "elevation="])
	except getopt.GetoptError, err:
		# Print help information and exit:
		print str(err) # will print something like "option -a not recognized"
		usage(exitCode=2)
	
	# Work through opts
	for opt, value in opts:
		if opt in ('-h', '--help'):
			usage(exitCode=0)
		elif opt in ('-f', '--frequency'):
			config['freq'] = float(value)*1e6
		elif opt in ('-a', '--azimuth'):
			config['az'] = float(value)
		elif opt in ('-e', '--elevation'):
			config['el'] = float(value)
		else:
			assert False
	
	# Add in arguments
	config['args'] = args

	# Return configuration
	return config

def main(args):
	config = parseOptions(args)
	filename = config['args'][0]

	station = parseSSMIF(filename)
	antennas = station.getAntennas()

	digs    = numpy.array([ant.digitizer  for ant in antennas])
	ants    = numpy.array([ant.id         for ant in antennas])
	stands  = numpy.array([ant.stand.id   for ant in antennas])
	pols    = numpy.array([ant.pol        for ant in antennas])
	antStat = numpy.array([ant.status     for ant in antennas])
	feeStat = numpy.array([ant.fee.status for ant in antennas])

	badStands = numpy.where( antStat != 3 )[0]
	badFees   = numpy.where( feeStat != 3 )[0]
	bad = numpy.where( (stands > 256) | (antStat != 3) | (feeStat != 3) )[0]
	print "Number of bad stands:   %3i" % len(badStands)
	print "Number of bad FEEs:     %3i" % len(badFees)
	print "---------------------------"
	print "Total number bad inuts: %3i" % len(bad)
	print " "

	print "Calculating beam for az. %.2f, el. %.2f at %.2f MHz" % (config['az'], config['el'], config['freq']/1e6)
	beam = beamformer.intBeamShape(antennas, azimuth=config['az'], elevation=config['el'], progress=True)
	
	import pylab
	pylab.imshow(beam)
	numpy.savez('test_%iMHz_%iaz_%iel.npz' % (config['freq']/1e6, config['az'], config['el']), beam=beam, az=config['az'], el=config['el'])
	pylab.show()


if __name__ == "__main__":
	main(sys.argv[1:])

