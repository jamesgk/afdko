#!/bin/env python

__copyright__ = """Copyright 2015 Adobe Systems Incorporated (http://www.adobe.com/). All Rights Reserved.
"""

__doc__ = """
"""
__usage__ = """
   makeInstancesUFO program v1.2 Oct 7 2015
   makeInstancesUFO -h
   makeInstancesUFO -u
   makeInstancesUFO [-d <design space file name>] [-a] [-c] [-n] [-dec] [-i 0,1,..n]

   -d <design space file path>
   Specifies alternate path to design space file. Default name is 'font.designspace'.

   -a ... do NOT autohint the instances. Default is to do so.
   -c ... do NOT remove the outline overlaps in the instances. Default is to do so.
   -n ... do NOT normalize the instances. Default is to do so.
   -dec . do NOT round coordinates to integer. Default is to do so.

   -i <list of instance indices>
   Specify the instances to generate. 'i' is a 0-based index of the instance records
   in the design space file.
      Example: '-i 1,4,22' -> generates only the 2nd, 5th, and 23rd instances
"""

__help__ = __usage__ + """
	makeInstancesUFO generates UFO font instances from a set of master design UFO fonts.
	It uses the same font math library as the LettError Superpolator program.
	The paths of the masters and instances fonts are specified in "design space" XML file.

"""

import sys
import os
import re
import time
import traceback
import FDKUtils
from subprocess import PIPE, Popen
from mutatorMath.ufo import build as mutatorMathBuild
import robofab.world
import shutil
import ufoTools
import defcon
try:
    import xml.etree.cElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET
XML = ET.XML
xmlToString = ET.tostring

try:
	import  ufoLib.ufonormalizer as ufonormalizerLib
	haveUfONormalizer = True
except ImportError:
	haveUfONormalizer = False

kFontName = "PSFontName"

kDefaultDesignSpaceFileName = "font.designspace"


class OptError(ValueError):
	pass
class ParseError(ValueError):
	pass
class SnapShotError(ValueError):
	pass

class Options:
	def __init__(self, args):
		self.dsPath = kDefaultDesignSpaceFileName
		self.doAutoHint = True
		self.doOverlapRemoval = True
		self.doNormalize = True
		self.logFile = None
		self.allowDecimalCoords = False
		self.indexList = []
		lenArgs = len(args)

		i = 0
		hadError = 0
		while i < lenArgs:
			arg = args[i]
			i += 1

			if arg == "-h":
				logMsg.log(__help__)
				sys.exit(0)
			elif arg == "-u":
				logMsg.log(__usage__)
				sys.exit(0)
			elif arg == "-d":
				self.dsPath = args[i]
				i +=1
			elif arg == "-log":
				logMsg.sendTo( args[i])
				i +=1
			elif arg == "-a":
				self.doAutoHint = False
			elif arg == "-c":
				self.doOverlapRemoval = False
			elif arg == "-n":
				self.doNormalize = False
			elif arg == "-dec":
				self.allowDecimalCoords = True
			elif arg == "-i":
				ilist = args[i]
				i +=1
				self.indexList =eval(ilist)
				if type(self.indexList) == type(0):
					self.indexList = (self.indexList,)
			else:
				logMsg.log("Error: unrcognized argument:", arg)
				hadError = 1

		if hadError:
			raise(OptError)

		if not os.path.exists(self.dsPath):
			logMsg.log("Note: could not find design space file path:", self.dsPath)
			hadError = 1

		if hadError:
			raise(OptError)


class LogMsg:
	def __init__(self, logFilePath = None):
		self.logFilePath = logFilePath
		self.logFile = None
		if  self.logFilePath:
			self.logFile = open(logFilePath, "wt")

	def log(self, *args):
		txt = " ".join(args)
		print txt
		if  self.logFilePath:
			self.logFile.write(txt + os.linesep)
			self.logFile.flush()

	def sendTo(self, logFilePath):
		self.logFilePath = logFilePath
		self.logFile = open(logFilePath, "wt")

logMsg = LogMsg()


def readDesignSpaceFile(options):
	""" Read design space file.
	build a new instancesList with all the instances from the ds file

	Promote all the source and instance filename attributes from relative to absolute paths
	write a temporary ds file
	return a path to the temporary ds file, and the current instances list.
	"""

	instanceEntryList = []
	print "Reading design space file: %s ..." % (options.dsPath)

	f = open(options.dsPath, "rt")
	data = f.read()
	f.close()
	ds = XML(data)

	instances = ds.find("instances")

	# Remove any instances that are not in the specified list of instance indices, from the option -i.
	if (len(options.indexList) > 0):
		newInstanceXMLList = instances.findall("instance")
		numInstances =  len(newInstanceXMLList)
		instanceIndex = numInstances
		while instanceIndex > 0:
			instanceIndex -= 1
			instanceXML = newInstanceXMLList[instanceIndex]
			if not (instanceIndex in options.indexList):
				instances.remove(instanceXML)

	# We want to build all remaining instances.
	for instanceXML in instances:
		try:
			curPath = instanceXML.attrib["filename"]
			psName = instanceXML.attrib["postscriptfontname"]
			print "adding %s to build list." % (psName)
			instanceEntryList.append(curPath)
			if os.path.exists(curPath):
				glyphDir = os.path.join(curPath, "glyphs")
				if os.path.exists(glyphDir):
					shutil.rmtree(glyphDir, ignore_errors=True)
		except KeyError:
			print "Skipping instance that does not have postscriptname attribute:", xmlToString(instanceXML)
			continue
	if not instanceEntryList:
		print "Failed to find any instances in the ds file '%s' that have the postscriptfilename attribute" % (options.dsPath)
		return None, None

	dsPath = options.dsPath + ".temp"
	data = xmlToString(ds)
	f = open(dsPath, "wt")
	f.write(data)
	f.close()

	return dsPath, instanceEntryList


def updateInstance(options, fontInstancePath):
	"""
	Run checkOutlinesUFO and autohint, unless explicitly suppressed.
	"""
	if options.doOverlapRemoval:
		logMsg.log("\tdoing overlap removal with checkOutlinesUFO %s ..." % (fontInstancePath))
		logList = []
		opList = ["-e", fontInstancePath]
		if options.allowDecimalCoords:
			opList.insert(0, "-dec")
		if os.name == "nt":
			opList.insert(0, 'checkOutlinesUFO.cmd')
			proc = Popen(opList, stdout=PIPE)
		else:
			opList.insert(0, 'checkOutlinesUFO')
			proc = Popen(opList, stdout=PIPE)
		while 1:
			output = proc.stdout.readline()
			if output:
				print ".",
				logList.append(output)
			if proc.poll() != None:
				output = proc.stdout.readline()
				if output:
					print output,
					logList.append(output)
				break
		log = "".join(logList)
		if not ("Done with font" in log):
			print
			logMsg.log(log)
			logMsg.log("Error in checkOutlinesUFO %s" % (fontInstancePath))
			raise(SnapShotError)
		else:
			print

	if options.doAutoHint:
		logMsg.log("\tautohinting %s ..." % (fontInstancePath))
		logList = []
		opList = ['-q', '-nb', fontInstancePath]
		if options.allowDecimalCoords:
			opList.insert(0, "-dec")
		if os.name == "nt":
			opList.insert(0, 'autohint.cmd')
			proc = Popen(opList, stdout=PIPE)
		else:
			opList.insert(0, 'autohint')
			proc = Popen(opList, stdout=PIPE)
		while 1:
			output = proc.stdout.readline()
			if output:
				print output,
				logList.append(output)
			if proc.poll() != None:
				output = proc.stdout.readline()
				if output:
					print output,
					logList.append(output)
				break
		log = "".join(logList)
		if not ("Done with font" in log):
			print
			logMsg.log(log)
			logMsg.log("Error in autohinting %s" % (fontInstancePath))
			raise(SnapShotError)
		else:
			print

	return


def clearCustomLibs(fontPath):
	dFont = defcon.Font(fontPath)
	for key in dFont.lib.keys():
		if key not in ['public.glyphOrder', 'org.unifiedfontobject.normalizer.modTimes']:
			del(dFont.lib[key])

	libGlyphs = [g for g in dFont if len(g.lib)]
	for g in libGlyphs:
		g.lib.clear()
	dFont.save()


def run(args):
	options = Options(args)

	# Set the current dir to the design space dir, so that relative paths in
	# the design space file will work.
	dsDir,dsFile = os.path.split(os.path.abspath(options.dsPath))
	os.chdir(dsDir)
	options.dsPath = dsFile

	dsPath, newInstancesList = readDesignSpaceFile(options)
	if not dsPath:
		return

	version = 2
	if len(newInstancesList) == 1:
		logMsg.log("Building 1 instance...")
	else:
		logMsg.log("Building %s instances..." % (len(newInstancesList)))
	mutatorMathBuild(documentPath=dsPath, outputUFOFormatVersion=version, roundGeometry=(not options.allowDecimalCoords))
	if (dsPath != options.dsPath) and os.path.exists(dsPath):
		os.remove(dsPath)

	logMsg.log("Built %s instances." % (len(newInstancesList)))

	# Remove glyph.lib and font.lib (except for "public.glyphOrder")
	for instancePath in newInstancesList:
		clearCustomLibs(instancePath)

	if options.doNormalize:
		logMsg.log("Applying UFO normalization...")
		for instancePath in newInstancesList:
			if haveUfONormalizer:
				ufonormalizerLib.normalizeUFO(instancePath, outputPath=None, onlyModified=True)

	if options.doAutoHint or options.doOverlapRemoval:
		logMsg.log("Applying post-processing...")
		# Apply autohint and checkoutlines, if requested.
		for instancePath in newInstancesList:
			# make new instance font.
			updateInstance(options, instancePath)

	if not options.doOverlapRemoval: # checkOutlinesUFO does ufoTools.validateLayers()
		for instancePath in newInstancesList:
			# make sure that that there are no old glyphs left in the processed glyphs folder.
			ufoTools.validateLayers(instancePath)

	if options.doOverlapRemoval or options.doAutoHint: # The defcon library renames glyphs. Need to fix them again.
		for instancePath in newInstancesList:
			if haveUfONormalizer and options.doNormalize:
				ufonormalizerLib.normalizeUFO(instancePath, outputPath=None, onlyModified=False)

if __name__=='__main__':
	try:
		run(sys.argv[1:])
	except (OptError, ParseError, SnapShotError):
		logMsg.log("Quitting after error.")
		pass


