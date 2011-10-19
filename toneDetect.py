#!/usr/bin/env python

"""
 * Copyright (C)2011, Edward M. Brown <edwardb[at]gmail.com>
 * 
 ****** ****** ****** ****** ****** ****** ****** ****** ****** ******
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 ****** ****** ****** ****** ****** ****** ****** ****** ****** ******
"""

""" Listens to audio and displays detected alerting tones
"""
import pyaudio
import wave
import numpy as np
from numpy import nan_to_num
import sys
import datetime
import csv
import time
import re
import os
import subprocess
from subprocess import Popen, PIPE
import smtplib
from optparse import OptionParser
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import logging
import logging.config
import logging.handlers
import math
import threading
import Queue
import ConfigParser
from decimal import Decimal, getcontext
from threading import Event, Thread



MAJOR_VERSION = "0."
MINOR_VERSION = "2."

__author__ = "Edward Brown and Travis Brown"
__copyright__ = "Copyright 2011, jetcom.org"
__credits__ = ["Edward Brown, Travis Brown"]
__license__ = "GPL V3"
__version__ = "$Revision: 190 $"
__maintainer__ = "Edward Brown"
__email__ = "edwardb@gmail.com"
__status__ = "alpha"
__revdate__ = "$Date: 2011-10-16 20:24:02 -0400 (Sun, 16 October 2011) $"
__commitby__ = "$Author: emb $"



##Change Log
##11/26/2010 Changed logging to use the logging.config. The logging setup is now done
##                     in logging_toneDetect.conf
##12/2/2010 Moved sampleRate to INI file
##        Changed percentDifference to plusMinus using the value of 2 to start
##        Changed to using RMS values to detect tones
##        Added command line parsing for -f --file and -d --debug
##12/7/2010 Added valid tone list. Once it determines the frequency of the tone it
##         picks the closest frequency from a list of known valid tones. The current
##         list contains all known Motorola QC2 and Plectron tones.
##
##         Change the tone map so that it has the correct tone in it, and removed
##         plusMinus from the tone check. the tone from the toneMapDict and the tone
##         being presented should be an exact match
##
##         Since we are doing only 2 and 1 tone this should speed up processing
##         and hopefully increase accuracy.
##12/12/2010 Changed to nested dictionaries fortone information and revamped the
##          getAlertInfo

"""
Set default parameters
"""

AUDIO_FILE = ""
INI_FILE = 'toneDetect.ini'
COMMASPACE = ", "
PLATFORM = sys.platform
RUNTIME_DIR = os.path.dirname(sys.argv[0])
ABS_PATH = os.path.abspath(RUNTIME_DIR)
SECTION = re.compile('^\s*\[\s*([^\]]*)\s*\]\s*$')
PARAM   = re.compile('^[-+]?[0-9]+(\.[0-9]+)?$')
COMMENT = re.compile('^\s*;.*$')
toneMapDict = {}  # define toneMap dictionary
ignoreTone = []  # define ignoreTone list
p = pyaudio.PyAudio()
#sampleRate = 11025
bufferSize = 1024
#chunks = []
pkt = []
tone = []
swidth = 2
CHANNELS = 1
reset = False
validTonesFile="validTones.ini"
ignoreTonesFile="ignoreTones.ini"
toneMapFile="toneMap.csv"

window = np.blackman(bufferSize)
toneDetected = False
audio = Queue.Queue()


LEVELS = {'debug': logging.DEBUG,
          'info': logging.INFO,
          'warning': logging.WARNING,
          'error': logging.ERROR,
          'critical': logging.CRITICAL}


logging.config.fileConfig('toneDetect_logging.conf')
logger = logging.getLogger("default")

usage = "usage: %prog [options] arg1 arg2"
parser = OptionParser(usage=usage)
parser = OptionParser()
parser.add_option("-d", "--debug", action="store_true", dest="debug",
                  help="set debug mode",default=False)

(options, args) = parser.parse_args()

if options.debug == True:
    level_name = "debug"
    level = LEVELS.get(level_name, logging.NOTSET)
    logging.basicConfig(level=level)
else:
    level_name = "info"
    level = LEVELS.get(level_name, logging.NOTSET)
    logging.basicConfig(level=level)
    
class RepeatTimer(Thread):
    def __init__(self, interval, function, iterations=0, args=[], kwargs={}):
        Thread.__init__(self)
        self.interval = interval
        self.function = function
        self.iterations = iterations
        self.args = args
        self.kwargs = kwargs
        self.finished = Event()
 
    def run(self):
        count = 0
        while not self.finished.is_set() and (self.iterations <= 0 or count < self.iterations):
            self.finished.wait(self.interval)
            if not self.finished.is_set():
                self.function(*self.args, **self.kwargs)
                count += 1
 
    def cancel(self):
        self.finished.set()
    
def makeToneMapDict():
    ##   decided to switch to a csv tone information file. That way the tone map can
    ##   easily be created in Excel, Open Office or even a text editor. The layout of
    ##   the file is a six colum table. The order is as follows:
    ##   Department, toneA, toneB, toneC, toneD, sendTo
    ##   Department is an alphanumeric field with the name of the department
    ##   All tone fields must be numeric to 1 decimal place. (999.9) if that tone
    ##   field is not in use it must contain a -1. example:
    ##   |589.7|2073.0|-1|-1| or |1082.0|1232.0|701.8|-1|
    ##   the final field is who an email should be sent to. It must be a fully formed
    ##   email address. Multiple addresses should be seperated by a semicolon ";"
    ##   toneman@acme.com;tonemanjunior@acme.com

    x = {}
    reader = csv.reader(open(toneMapFile, "r"))
    for row in reader:
        x.setdefault(row[1], {}).setdefault(row[2], {}).setdefault(row[3], {}).setdefault(row[4], {})['email']=row[5]
        x.setdefault(row[1], {}).setdefault(row[2], {}).setdefault(row[3], {}).setdefault(row[4], {})['dept']=row[0]
    return(x)

def makeValidToneArray():
    #Build a valid tones dictionary
    # The dictionary name comes from the [SECTION] of the validTonesFile
    x = []
    f = open(validTonesFile)
    for row in f:
        x.append(row)
    f.close()
    f = open(ignoreTonesFile)
    for row in f:
        x.append(row)
    f.close()
    y = np.asanyarray(x)
    return(y)

def checkINI():
    t = time.time()
    x = os.stat(validTonesFile).st_mtime
    y = os.stat(toneMapFile).st_mtime
    z = os.stat(ignoreTonesFile).st_mtime
    if t - x < 120:
        makeValidToneArray()
        print("ValidTones reloaded = ",t-x)
    if t - y < 120:
        makeToneMapDict()
        print("ToneMap reloaded")
    if t - z < 120:
        makeIgnoreToneDict()
        print("IgnoreTone reloaded")
    return()


def copyList(x):
    y = []
    y.extend(x)
    return(y)
 
def makeIgnoreTone():
    logger.debug("Starting load IgnoreTones")
    x = []
    f = open(ignoreTonesFile)
    for row in f:
        x.append(row.strip())
    logger.debug("Leaving makeIgnoreTones")
    return(x)

def calc(d, value):
    for k in d:
        if abs(k-value) <= limit:
            d[k].append(value)
            return d
    d[value] = [value]
    return(d)

def getRevInfo(x):
    x = x.split(" ",1)
    x = x[1].split("$")
    x = x[0].strip()
    return(x)

def remove_adjacent(seq):
    i = 1
    n = len(seq)
    while i < n: 
        if seq[i] == seq[i-1]:
            del seq[i]
            n -= 1
        else:
            i += 1
    return(seq)

def calcError(x,y):
    z = round(((x / y) - 1) * 100,5)
    return(z)


def pushFreqArray(array, freq):
    """Push frequency onto the stack and return the stack

    If the stack doesn't exist it is created and filled with
    number that will create a high rms value
    The top of the stack is removed and the new frequency
    is added at the bottom, creating a FIFI stack.
    """

    if len(array) == 0:
        array = [1000, 2000, 3000]
    if len(array) == 3:
        array = array[1:]
          # Add to stack
        array.append(freq)
    return(array)

def mean(x):
    """Calculates arthmetic mean of an array. Checks for zero length arrays"""

    if len(x) == 0:
        return 0
    else:
        y = sum(x)/len(x)
        return (y)

def fcmp(x1,x2):
    """ Compares floating point values for equality """
    if abs(x1-x2) < 0.00001:
        return True
    else:
        return False

def displaySoundDevices():
    print ("")
    print ("=================================")
    print ("Currently Available Audio Devices")
    maxDeviceCount = p.get_device_count()
    i = 0
    while i < maxDeviceCount:
        print("%s %s" % (i, p.get_device_info_by_index( i )['name']))
        i+=1
    print ("=================================")
    print ("")

def stream():
    #global chunks, inStream, bufferSize
    global audio, inStream, bufferSize
    while True:
        #chunks.append(inStream.read(bufferSize))
        audio.put(inStream.read(bufferSize))

def record():
    global inStream, p, bufferSize, deviceIndex
    inStream = p.open(format=pyaudio.paInt16,channels=1,\
        rate=sampleRate,input=True,\
        frames_per_buffer=bufferSize,\
        input_device_index = deviceIndex)
    t_str=threading.Thread(target=stream)
    t_str.daemon=True
    t_str.start()

def flatten(x):
    result = []
    for el in x:
        if hasattr(el, "__iter__") and not isinstance(el, basestring):
            result.extend(flatten(el))
        else:
            result.append(el)
    return result

def makeDirectory(dir_path):
    """Check to see if a directory exists, if not it creates it"""

    logger.debug("Checking for directory")
    try:
        os.makedirs(dir_path)
    except OSError:
        # If the path exists this will catch it
        if os.path.exists(dir_path):
            pass
        else:
            #Unknown error re-raise and let it be unhandled exception
            raise

def getFreq( pkt ):
    """ Use FFT to determine the peak frequency of the last chunk"""
    thefreq = 0
    
    if len(pkt) == bufferSize*swidth:
        indata = np.array(wave.struct.unpack("%dh"%(len(pkt)/swidth), pkt))*window

        # filter out everything outside of our bandpass Hz
        bp = np.fft.rfft(indata)
        minFilterBin = (bandPass[0] / (sampleRate/bufferSize)) + 1
        maxFilterBin = (bandPass[1] / (sampleRate/bufferSize)) - 1
        for i in range(len(bp)):
            if i<minFilterBin: bp[i]=0
            if i>maxFilterBin: bp[i]=0

        # Take the fft and square each value
        fftData = abs(bp)**2

        # find the maximum
        which = fftData[1:].argmax() + 1

        # Compute the magnitude of the sample we found
        dB = 10*np.log10(1e-20+abs(bp[which]))

        if dB >= minDbLevel:
            # use quadratic interpolation around the max
            if which != len(fftData)-1:
                y0,y1,y2 = np.log(fftData[which-1:which+2:])
                x1 = (y2 - y0) * .5 / (2 * y1 - y2 - y0)
                # find the frequency and output it
                thefreq = (which+x1)*sampleRate/bufferSize
            else:
                thefreq = which*sampleRate/bufferSize
                thefreq = thefreq
        else:
            thefreq = -1

        return thefreq

def findNearest(array,value):
    """Search through an array and find the closest numeric match"""
    array = np.asanyarray(array,float)
    idx=(np.abs(array-value)).argmin()
    return (array[idx].item())

def checkTone(x):
    x = findNearest(validToneArray, x)
    return(x)

def getAlertInfo( timestamp, tones, toneCounttoneMapDict ):
    """Find the matching tone sets in toneMapDict"""
    global toneDetected
 
    """ Clean up the lists before starting"""
    deptList = []
    toneList = []
    emailTo = []
    unusedTones = []
    tabTones = []
    mailList = []

    logger.debug("Starting getAlertInfo")
    logger.debug("%s, %s " % (timestamp, tones))
    """ First check to make sure there are tones to be checked"""
    logger.debug("%s tones to be checked" % (len(tones)))
    if len(tones) == 0:
        logger.debug("return [notones], %s" % ([0]))
        toneDetected = False
        return (['notones'], [], [], [])
    #since there is at least one tone start processing
    tone = copyList(tones)
    tone.reverse()
    while len(tone) > 0 and toneDetected == True:
        try:
            xtone = tone.pop()
            tcount = toneCount.pop()
        except IndexError:
            break
        if xtone > 0:
            stdTone = checkTone(xtone)
            toneErr = calcError(xtone,stdTone)
            # If the difference between the FFT frequency and the nearest standard paging frequency is more than >= .1% the correct standard paging frequency
            # is probably missing from the validTones file. Adding the correct standard paging tone to the valitTone files should solve the problem
            if toneErr >= .1:
                logger.info('%s  Probable tone identification error: xtone= %s stdTone = %s, ToneError = %s ' % (timestamp, xtone, stdTone, toneErr))
            tabTones.append(stdTone)
            logger.debug('%s  xtone = %s tcount = %s stdTone = %s ToneError = %s ' % (timestamp, xtone, tcount, stdTone, toneErr))
            fw = open("log/tone.log", "a")
            fw.write('"%s","%s","%s" \n' % (xtone, tcount, stdTone))
            fw.close()

    """
    If there is one tone only, check to see if it as an alert tone.
    If so set the dept to Single Tone Alert.
    """

    if len(tabTones) == 1 and str(tabTones[0]) in ignoreTone:
        deptList.append('Single Tone Alert')
        toneList.append(tabTones[0])
        tabTones = tabTones[1:]
        return (deptList, toneList, emailTo, unusedTones)

    """
    Check and remove any tone if is on the ignore list
    """
    logger.debug("Before removing ignoreTones: %s" % (tabTones))
    for tone in tabTones[:]:
        if str(tone) in ignoreTone: 
            tabTones.remove(tone)
    logger.debug("After removing ignoreTones: %s" % (tabTones))
    if len(tabTones) == 0:
        toneDetected = False
        return (['notones'], [], [], unusedTones)
    
    # Check for 3 tone sequence
    if len(tabTones) == 3:
        try:
            dept = toneMapDict[str(tabTones[0])][str(tabTones[1])][str(tabTones[2])]["-1"]['dept']
            email = toneMapDict[str(tabTones[0])][str(tabTones[1])][str(tabTones[2])]["-1"]['email'].split(';')
            for address in email:
                emailTo.append(address)
            deptList.append(dept)
            toneList.append([str(tabTones[0]), str(tabTones[1]), str(tabTones[2])])
            logger.debug("Match found: %s, %s %s = %s" % (tabTones[0], tabTones[1], tabTones[2], dept))
            tabTones = tabTones[3:]
        except KeyError:
            pass
    """
    If there is an even number of tabTones, test them in groups
    of two only. This reduces the chance if picking up a false
    hit
    """
    if len(tabTones)%2 <> 0:
        tabTones.append("-1")
    if len(tabTones)%2 == 0:
        logger.debug("Even number of tabTones found. Starting pair check")
        while len(tabTones) > 0:
            try:
                dept = toneMapDict[str(tabTones[0])][str(tabTones[1])]["-1"]["-1"]['dept']
                email = toneMapDict[str(tabTones[0])][str(tabTones[1])]["-1"]["-1"]['email'].split(';')
                for address in email:
                    emailTo.append(address)
                deptList.append(dept)
                toneList.append([str(tabTones[0]), str(tabTones[1])])
                logger.debug("Match found: %s, %s = %s" % (tabTones[0], tabTones[1], dept))
                tabTones = tabTones[2:]
            except KeyError:
                unusedTones.append(str(tabTones[0]))
                unusedTones.append(str(tabTones[1]))
                logger.debug("Match NOT found: %s, %s" % (tabTones[0], tabTones[1]))
                tabTones = tabTones[2:]
        logger.debug("Even number of tabTones check complete: %s %s %s" % (deptList, toneList, emailTo))
        return (deptList, toneList, emailTo, unusedTones)

    """
    If the number of tabTones is odd, test them two at a time,
    if no match remove the first one and try again
    """
    if len(tabTones)%2 == 1:
        logger.debug("Odd number of tabTones found. Starting single tone check")
        while len(tabTones) > 0:
            try:
                dept = toneMapDict[str(tabTones[0])]["-1"]["-1"]["-1"]['dept']
                email = toneMapDict[str(tabTones[0])]["-1"]["-1"]["-1"]['email'].split(';')
                for address in email:
                    emailTo.append(address)
                deptList.append(dept)
                toneList.append([str(tabTones[0])])
                logger.debug("Match found: %s = %s" % (tabTones[0], dept))
                tabTones = tabTones[1:]
            except KeyError:
                logger.debug("Match NOT found: %s " % (tabTones))
                unusedTones.append(str(tabTones[0]))
                tabTones = tabTones[1:]
                pass
        logger.debug("Odd number of tabTones check complete: %s %s %s" % (deptList, toneList, emailTo))
        return (deptList, toneList, emailTo, unusedTones)
    
    """ 
    If we get this far and still have tabTones left there is a problem.
    Capture the information and move on
    """
    logger.info("You should never see this message. If you do contact support")
    if len( deptList ) == 0:
        unusedTones.append(tabTones)
        logger.debug("deptList is empty. unusedTones = %s" % (unusedTones))

    
    if len(tabTones) > 0:
        unusedTones.append(tabTones)
        logger.debug("There are still tabTones left %s" % (tabTones))
        logger.debug("deptList: %s " % (deptList))
        logger.debug("unusedTones: %s " % (unusedTones))
        logger.debug("mailList: %s " % (mailList))
        return (deptList, toneList, emailTo, unusedTones)


    print "IF you see this message the has been a major failure in the AlertInfo Logic"
    print "so we are stopping the program"
    sys.exit()

def processRecording( timestamp,
                      tones,
                      toneMapDict,
                      recordedSamples,
                      sampleRate,
                      swidth,
                      CHANNELS,
                      toneCount):
    global toneDetected
    logger.debug("Starting processRecording")
    
    (deptList, toneList, mailList, unusedTones) = getAlertInfo( timestamp, tones, toneMapDict )

    if toneDetected == False:
        return()

    logger.debug("%s, %s, %s" % (deptList, toneList, mailList))
    logger.debug("Unused tones: %s" % (unusedTones))
    logger.info("Page received for %s." % (deptList))
    
    # Cleanup emails
      
    mailList = flatten(mailList)
    
    for mailaddr in mailList[:]:
        if mailaddr == 'N/A': mailList.remove(mailaddr)
        
    # Remove duplicates
    deptList = list(set(deptList))
    mailList = list(set(mailList))
    
    logger.debug("deptList= %s" % (deptList))
    logger.debug("mailList= %s" % (mailList))

    depts = COMMASPACE.join(deptList)

    if CHANNELS == 1:
        CHANNELStr = "m"
    elif CHANNELS == 2:
        CHANNELStr = "j"
    logger.debug("Beginning MP3 encoding. PLATFORM = %s" % (PLATFORM))
    if PLATFORM == "win32":
        tmpFile = "tmp.wav"
        if os.access(tmpFile,os.F_OK):
            os.remove(tmpFile)
        wf = wave.open(tmpFile, 'wb')
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(sampleRate)
        for frame in recordedSamples:
            wf.writeframes(frame)
        wf.close()
        mp3File = "out/%s-%s.mp3" % (re.sub('\:', '.', timestamp), depts)
        enc = subprocess.Popen(["lame", "--quiet", "-r", "-s", str(sampleRate/1000.0), "--bitwidth", str(swidth*8), "-m", CHANNELStr, tmpFile, mp3File])
    else:
        mp3File = "out/%s-%s.mp3" % (re.sub('\:', '.', timestamp), depts)
        enc = Popen(["lame", "--quiet", "-r", "-s", str(sampleRate/1000.0), "--bitwidth", str(swidth*8), "-m", CHANNELStr, "-", mp3File], stdin=PIPE)
        for frame in recordedSamples:
            enc.stdin.write(frame)

        enc.stdin.close()
        enc.wait()
    logger.debug("MP3 encoding complete")

    subject = "[Dispatch] %s (%s)" % ( depts, timestamp )

    if testMode == True:
        mailList.append(testModeEmail)
    logger.debug("There are %s emails to be sent" % (len(mailList)))
    if len( mailList ) > 0:
        mail( subject, mailList, "", mp3File)
    toneDetected = False
    logger.debug("Leaving processRecording")

def mail( subject, mailList, text, attach):
    logger.debug("Starting mail")
    logger.debug("%s, %s %s" % (subject, mailList, text))

    if len(mailList) == 0:
        logger.error("No email address specified")
        return

    while not os.path.isfile(attach):
        time.sleep(1)
    logger.debug("Beginning MIME attachment")
    msg = MIMEMultipart()
    msg['From'] = emailFrom
    msg['To'] = COMMASPACE.join(mailList)
    msg['Subject'] = subject

    msg.attach(MIMEText(text))

    part = MIMEBase('application', 'octet-stream')
    part.set_payload(open(attach, 'rb').read())
    encoders.encode_base64(part)
    part.add_header('Content-Disposition',
            'attachment; filename="%s"' % os.path.basename(attach))
    msg.attach(part)
    if sendMail == True:
        logger.debug("Begin SMTP connection")
        server = smtplib.SMTP(smtp_server)
        server.debug = 1
        server.sendmail(emailFrom, mailList, msg.as_string())
        server.close()
        logger.debug("SMTP connection closed")
        logger.info("eMail sent %s to %s" % (subject,mailList))
    logger.debug("Leaving mail")
    
def go():
    logger.info("=================================")
    logger.info("Starting ToneDetect")
    logger.info("Watching for tones:")
    t_rec=threading.Thread(target=record)
    t_rec.daemon=True
    t_rec.start()

if __name__ == "__main__":

    makeDirectory("out")
    makeDirectory("log")
    logger.info("Version number: %s %s" % (MAJOR_VERSION + MINOR_VERSION+ __version__.rsplit(" ")[1], __status__))
    logger.info("Last commit: %s by %s" % (getRevInfo(__revdate__), getRevInfo(__commitby__)))
    logger.debug("Starting main")
    lastCheckTime = time.time()
    toneMapDict = makeToneMapDict()
    ignoreTone = makeIgnoreTone()
    validToneArray = makeValidToneArray()
    t = RepeatTimer(60.0,checkINI)
    t.start()
    #DO NOT MOVE THIS CODE
    #For the values contained in the ini file to be global, this code must be performed in the
    #mainstream of the program. This will change when we move to Python >= 3.0
    ###############################################################################################
    ###############################################################################################
    

    
    logger.debug("Loading INI file %s" % (INI_FILE))
    loadedINI = os.stat(INI_FILE).st_mtime
    f = file(INI_FILE,'r')
    text = f.readlines()
    for line in text:
        if re.search('^#',line):
            pass
        elif re.search('^[ \t\r\n]*$',line):
            pass
        else:
            result = re.sub("(?m)(#).*$","", line)
            result = re.sub("(?m)^[ \t]*$\r?\n", "", result)
            if len(result) > 0:
                result = re.split("(?m)^([^=\r\n]+)=(.*)", result, 1)
                result = str.strip(result[1]) + ' = ' + str.strip(result[2])
                exec result
                logger.info("%s",(result))

    ################################################################################################
    ################################################################################################

    samplesPerSecond = float(sampleRate)/float(bufferSize)
    sampleDuration = 1.0/samplesPerSecond
    minGroupSamples = int(minGroupTime/sampleDuration)

    logger.info("PLATFORM = %s" % (PLATFORM))
    logger.info("RUNTIME_DIR = %s" % (RUNTIME_DIR))
    logger.info("ABS_PATH = %s" % (ABS_PATH))
    logger.info("MAJOR_VERSION = %s" % (MAJOR_VERSION))
    logger.info("FORMAT = %s" % (FORMAT))
    logger.info("sampleDuration = %s" % (sampleDuration))
    logger.info("minGroupSamples = %s" % (minGroupSamples))
    logger.info("bufferSize = %s" % (bufferSize))
    logger.info("INI file complete")

    if options.debug == True:
        level_name = "debug"
        level = LEVELS.get(level_name, logging.NOTSET)
        logging.basicConfig(level=level)
    else:
        level_name = debugLevel
        level = LEVELS.get(level_name, logging.NOTSET)
        logging.basicConfig(level=level)
        
    # Constants
    freqDiffThreshold = 1.0 # percent

    count = 0                  # How many samples have we processed total
    freq = -1                  # The dominant frequency of the current sample
    silenceSamples = 0         # keep track of how many samples were silence
    voiceSamples = 0
    recording = False          # True of we are recording
    recordedSamples = []       # Set of samples of our current recording
    toneSamples = []           # collection of last samples we heard
    tones = []                 # list of confirmed tones indexed by timestamps
    toneCount = []             # number of samples of each tone
    freqArray = []
    highRMS = 0
    rms = -1

    # These just convert times to samples for easy checks below
    minToneSamples = minToneLength / sampleDuration
    minVoiceSamples = minVoiceLength / sampleDuration
    #maxVoiceSamples = maxVoiceLength / sampleDuration
    maxSilenceSamples = maxSilenceLength / sampleDuration
    maxRecordSamples = maxRecordLength/sampleDuration
    

    ## If we get more than two frequencies in a row within N percent, we presume
    ## it to be a tone. We need at least .4 seconds total though to really
    ## classify it as a tone.  Because some tones look like this:
    ##        A B C
    ##        A B
    ## and they are distinct dispatches, we can't go looking for tones as soon
    ## as we get them.  We have to wait until some time passes before we process
    ## tones.  Ugh!
    displaySoundDevices()
    audioDeviceName = p.get_device_info_by_index(deviceIndex)['name']
    print( "Opening audio device '"'%s --> %s'"'" % (deviceIndex, audioDeviceName))
    go()
    while 1:
        #if len(chunks) > 0:
        while not audio.empty():
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if audio.qsize() > 50: logger.debug("audio.qsize = %s)" % (audio.qsize()))
            #if len(chunks) > 50: logger.debug("len(chunks) = %s)" % (len(chunks)))
            pkt = audio.get()
            #pkt = chunks.pop(0)
            freq = getFreq( pkt )
            # If we are recording, store our samples
            if recording == True:
                recordedSamples.append( pkt )

            """
            If the len of the testFreq array is 3 remove the top item before adding
            the new freq at the botom, giving us a FIFO stack
            """
            if freq != -1 or toneDetected == True or recording == True:
                freqArray = pushFreqArray(freqArray,freq)
                 # Once we have 3 frequencies in the array
                if len(freqArray) == freqArrayLen:
                ##Calculate the RMS value of those three frequencies
                    rms = math.sqrt(((max(freqArray) - min(freqArray))**2)/freqArrayLen)
                    """
                    from observation 1 seems to be a good dividing line between tones and noise/voice
                    tracking the number of times high rms is found to determine if they are talking.
                    Added voiceSamples This value should indicate how much talking gas happened since
                    the tone was detected. Because there can be long periods od silence or a lot of tones
                    we dont want to start the clock on the completed recording until we have heard some
                    voice first. This should mean we can use shorter silence times to detect the end of 
                    of a dispatch.
                    """
            if rms >= 1:
                highRMS += 1
                if toneDetected == True:
                    silenceSamples = 0
                    voiceSamples += 1
            elif sum(freqArray)/freqArrayLen < 0 or rms == -1:
                highRMS += 1
                if toneDetected == True:
                    silenceSamples += 1
            else:
                highRMS = 0
                silenceSamples = 0
                voiceSamples = 0
                recording = True
                toneDetected = True
                tones.append(mean(freqArray))
                
    
    
            ##    If we've exceeded the max recording length, OR if we have recorded
            ##    the minimum amount of data and we've encountered N seconds of
            ##    silence, stop recording and process our data. If we are using an audio
            ##    file and the file is at the end, process the data.
            if toneDetected == True and (voiceSamples > minVoiceSamples\
               and silenceSamples > maxSilenceSamples) or len(recordedSamples) > maxRecordSamples:
   
                recording = False
    
                ##    New algorithim for finding the frequency of the tone. If the RMS of a group of three < 1
                ##    write the mean of those three to the tones array. I am no longer rounding it to the nearest
                ##    integer at this point. The next step is after the dispatch is done is to go through the array
                ##    andremove any -1 values as these indicate silence. We go through the array a second time
                ##    to identify individual tones. If the current tone is close to the previous tone it is written
                ##    to tmpArray. Once we find the next tone we take the average value of tmpArray and the the number
                ##    of items in the array and write it to a logfile and write the freq to the newTones Array. We continue
                ##    this until we reach the end. We should have all of the tones heard in the newTones Array. We set
                ##    the tones Array to be equal to the newTones array.
                
                if len(tones)> 0:
                    #remove "nan" from tones
                    for tone in tones:
                        if tone == 'nan':
                            logger.debug("tones = %s " % (tones))
                            tones.remove(tone)
                            logger.debug("tones = %s " % (tones))
                    #make sure everything is at or above 280Hz
                    for tone in tones:
                        if tone < 280 and tone > 0:
                            logger.debug("tones = %s " % (tones))
                            tones.remove(tone)
                            logger.debug("tones = %s " % (tones))
                    # Check for tones that are too short < 2 samples

                    # for long tones (group calls) add a second tone entry
                    # long tones should be able to be decoded by duplicate toneA and toneB
                    if len(tones) == 0:
                        toneDetected = False
                        reset = True
                        break
                    splits = [i for i in range(1, len(tones)) if abs(tones[i-1] - tones[i]) > 2]
                    splits = [0] + splits + [len(tones)]
                    logger.debug("splits = %s" % (splits))
                    tones = [mean(tones[splits[i-1]:splits[i]]) for i in range(1, len(splits))]
                    toneCount = [(splits[i] - splits[i-1]) for i in range(1, len(splits))]
                    logger.debug("tones = %s" % (tones))
                    logger.debug("toneCount = %s" % (toneCount))
                    for i in range(len(toneCount),0):
                        if toneCount[i] < 3:
                            logger.debug("Short Tone -- tones = %s  toneCount = %s" % (tones, toneCount))
                            tone.pop[i]
                            toneCount.pop[i]
                            logger.debug("After Short Tone -- tones = %s  toneCount = %s" % (tones, toneCount))
                    for i in range(len(toneCount),0):
                        if toneCount[i] > minGroupSamples:
                            logger.debug("Possible All Call -- tones = %s  toneCount = %s  minGroupSamples = %s i = %s" % (tones, toneCount, minGroupSamples, i))
                            toneCount[i] = minGroupSamples
                            toneCount.insert(i,minGroupSamples)
                            tone.insert(i,tone[i])
                            logger.debug("tones = %s  toneCount = %s" % (tones, toneCount))
    
    
            if toneDetected == True and recording == False and len(tones) > 0:
                logger.debug("Tone found and recording has stopped. Ready for processRecording")
                processRecording( timestamp, tones, toneMapDict, recordedSamples, sampleRate, swidth, CHANNELS, toneCount)
                logger.debug("Returned from processRecording, starting RESET")
                count = 0                  # How many samples have we processed total
                freq = -1                  # The dominant frequency of the current sample
                silenceSamples = 0         # keep track of how many samples were silence
                voiceSamples = 0
                recording = False          # True of we are recording
                recordedSamples = []       # Set of samples of our current recording
                toneSamples = []           # collection of last samples we heard
                tones = []                 # list of confirmed tones indexed by timestamps
                toneCount = []
                freqArray = []
                highRMS = 0
                rms = -1
                logger.debug("RESET complete")
    
    
    
    
            if reset == True:
                logger.debug("Reset requested, starting RESET")
                count = 0                  # How many samples have we processed total
                freq = -1                  # The dominant frequency of the current sample
                silenceSamples = 0         # keep track of how many samples were silence
                voiceSamples = 0
                recording = False          # True of we are recording
                recordedSamples = []       # Set of samples of our current recording
                toneSamples = []           # collection of last samples we heard
                tones = []                 # list of confirmed tones indexed by timestamps
                toneCount = []
                freqArray = []
                highRMS = 0
                rms = -1
                reset = False
                logger.debug("RESET complete")
