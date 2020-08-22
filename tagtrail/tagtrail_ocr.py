#  tagtrail: A bundle of tools to organize a minimal-cost, trust-based and thus
#  time efficient accounting system for small, self-service community stores.
#
#  Copyright (C) 2019, Simon Greuter
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.
import argparse
import cv2 as cv
import numpy as np
import itertools
import pytesseract
import PIL
import os
import math
import Levenshtein
import slugify
import tkinter
from tkinter import ttk
from tkinter import messagebox
from tkinter.simpledialog import Dialog
import imutils
import functools
from PIL import ImageTk,Image
from abc import ABC, abstractmethod
import helpers
from sheets import ProductSheet
from database import Database
from os import walk

class ProcessingStep(ABC):
    def __init__(self,
            name,
            outputDir = 'data/tmp/',
            log = helpers.Log()):
        self._name = name
        self._log = log
        self.outputDir = outputDir
        super().__init__()

    @property
    def prefix(self):
        return f'{self.outputDir}{self._name}'

    @abstractmethod
    def process(self, inputImg):
        self._log.info("#ProcessStep: {}".format(self._name))
        self._outputImg = inputImg

    def writeOutput(self):
        cv.imwrite(f'{self.prefix}.jpg', self._outputImg)

class LineBasedStep(ProcessingStep):
    drawLineLength = 1000

    @abstractmethod
    def process(self, inputImg):
        super().process(inputImg)
        self._linesImg = inputImg

    # pt0 = (x0, y0)
    # v0 = (dx, dy)
    def ptAndVecToPts(self, p0, v0):
        x0, y0 = p0
        dx, dy = v0
        return ((int(x0-self.drawLineLength*dx), int(y0-self.drawLineLength*dy)),
                (int(x0+self.drawLineLength*dx), int(y0+self.drawLineLength*dy)))

    # computes the minimal rotation angle [rad] necessary to make the line either
    # horizontal or vertical
    def minAngleToGridPts(self, pt0, pt1):
        x0, y0 = pt0
        x1, y1 = pt1
        if x0==x1 or y0==y1:
            return 0
        if x0<x1 and y0<y1:
            alpha = np.arctan((y1-y0) / (x1-x0))
            if alpha > np.pi/4: alpha = alpha - np.pi/2
        if x0<x1 and y0>y1:
            alpha = np.arctan((x1-x0) / (y0-y1))
            if alpha > np.pi/4: alpha = alpha - np.pi/2
        if x0>x1 and y0<y1:
            alpha = np.arctan((x0-x1) / (y1-y0))
            if alpha > np.pi/4: alpha = alpha - np.pi/2
        if x0>x1 and y0>y1:
            alpha = np.arctan((y0-y1) / (x0-x1))
            if alpha > np.pi/4: alpha = alpha - np.pi/2
        return alpha

    def minAngleToGridPtAndVec(self, pt0, v0):
        (pt0, pt1) = self.ptAndVecToPts(pt0, v0)
        return self.minAngleToGridPts(pt0, pt1)

    # pt0 = (x0, y0)
    # pt1 = (x1, y1)
    def drawLinePts(self, pt0, pt1):
        cv.line(self._linesImg, pt0, pt1, (255,0,0), 2)

    # pt0 = (x0, y0)
    # v0 = (dx, dy)
    def drawLinePtAndVec(self, pt0, v0):
        (pt0, pt1) = self.ptAndVecToPts(pt0, v0)
        self.drawLinePts(pt0, pt1)

    # Line defined by all points for which
    # rho = x * cos(theta) + y * sin(theta)
    # see https://docs.opencv.org/3.0-beta/doc/py_tutorials/py_imgproc/py_houghlines/py_houghlines.html
    def drawLineParametric(self, rho, theta):
        a = np.cos(theta)
        b = np.sin(theta)
        self.drawLinePtAndVec((a*rho, b*rho), (-b, a))

class SheetSplitter(ProcessingStep):
    numberOfSheets = 4

    def __init__(self,
                 name,
                 outputDir = 'data/tmp/',
                 sheet0 = (0, 0, .5, .5), # x0, y0, x1, y1 relative
                 sheet1 = (.5, 0, 1, .5), # x0, y0, x1, y1 relative
                 sheet2 = (0, .5, .5, 1), # x0, y0, x1, y1 relative
                 sheet3 = (.5, .5, 1, 1), # x0, y0, x1, y1 relative
                 threshold = 140,
                 kernelSize = 7,
                 log = helpers.Log(),
                 ):
        super().__init__(name, outputDir, log)
        self._sheets = [sheet0, sheet1, sheet2, sheet3]
        self._log = log
        self._threshold = threshold
        self._smallKernel = cv.getStructuringElement(cv.MORPH_RECT,
                (kernelSize, kernelSize))
        self._mediumKernel = cv.getStructuringElement(cv.MORPH_RECT,
                (kernelSize*5, kernelSize*4))
        self._bigKernel = cv.getStructuringElement(cv.MORPH_RECT,
                (kernelSize*10, kernelSize*8))

    def process(self, inputImg):
        super().process(inputImg)

        self._inputImg = inputImg
        self._outputImg = np.copy(inputImg)

        self.unprocessedSheetImgs = []
        self._grayImgs = []
        self._adaptiveThresholdImgs = []
        self._denoisedAdaptiveThresholdImgs = []
        self._dilatedAdaptiveThresholdImgs = []
        self._erodedAdaptiveThresholdImgs = []
        self._labeledAdaptiveThresholdImgs = []
        self._biggestComponentImgs = []
        self._otsuThresholdImgs = []
        self._erodedOtsuThresholdImgs = []
        self._dilatedOtsuThresholdImgs = []
        self._thresholdImgs = []
        self._labeledImgs = []
        self._foregroundImgs = []
        self._rotatedImgs = []
        self._outputSheetImgs = []
        for x0rel, y0rel, x1rel, y1rel in self._sheets:
            height, width, _ = self._inputImg.shape
            x0, y0 = int(x0rel*width), int(y0rel*height)
            x1, y1 = int(x1rel*width), int(y1rel*height)
            unprocessedSheetImg = np.copy(self._inputImg[y0:y1, x0:x1, :])
            self.unprocessedSheetImgs.append(unprocessedSheetImg)
            self.processSheet(unprocessedSheetImg)

    def processSheet(self, sheetImg):
        """
        Process one part of the input image, extracting only the white paper.

        return: True if a sheet image was extracted and stored in
        outputSheetImgs, else False.
        """
        sheetImgWidth, sheetImgHeight, _ = sheetImg.shape
        grayImg = cv.cvtColor(sheetImg, cv.COLOR_BGR2GRAY)

        biggestComponentImg = self.biggestComponentFromAdaptiveThreshold(grayImg)
        if biggestComponentImg is None:
            # fallback to simple otsu thresholding
            otsuThresholdImg = cv.threshold(grayImg, self._threshold, 255,
                   cv.THRESH_BINARY | cv.THRESH_OTSU)[1]
            erodedOtsuThresholdImg = cv.erode(otsuThresholdImg, self._smallKernel, 1)
            dilatedOtsuThresholdImg = cv.dilate(erodedOtsuThresholdImg, self._mediumKernel, 1)
            thresholdImg = cv.erode(dilatedOtsuThresholdImg, self._bigKernel, 1)
        else:
            otsuThresholdImg = None
            erodedOtsuThresholdImg = None
            dilatedOtsuThresholdImg = None
            thresholdImg = biggestComponentImg

        foregroundSize = len(np.where(thresholdImg == 255)[0])
        self._log.debug(f'foregroundSize = {foregroundSize}')
        self._log.debug(f'imageSize = {sheetImgWidth * sheetImgHeight}')

        self._grayImgs.append(grayImg)
        self._biggestComponentImgs.append(biggestComponentImg)
        self._thresholdImgs.append(thresholdImg)
        self._otsuThresholdImgs.append(otsuThresholdImg)
        self._erodedOtsuThresholdImgs.append(erodedOtsuThresholdImg)
        self._dilatedOtsuThresholdImgs.append(dilatedOtsuThresholdImg)

        numComponents, labeledImg = cv.connectedComponents(thresholdImg)
        self._labeledImgs.append(labeledImg)
        if foregroundSize < sheetImgWidth * sheetImgHeight / 4:
            self._log.info('found empty sheet')
            self._foregroundImgs.append(None)
            self._rotatedImgs.append(None)
            self._outputSheetImgs.append(None)
            return False

        minAreaRect, foregroundImg = self.biggestComponentMinAreaRect(numComponents, labeledImg, thresholdImg)
        foregroundImg = cv.cvtColor(grayImg, cv.COLOR_GRAY2BGR)
        cv.drawContours(foregroundImg,[np.int0(cv.boxPoints(minAreaRect))],0,(0,0,255),2)
        center, (minAreaRectWidth, minAreaRectHeight), rotationAngle = minAreaRect

        # extract the minAreaRect from sheetImg
        # cudos to http://felix.abecassis.me/2011/10/opencv-rotation-deskewing/
        if rotationAngle < -45.0:
            rotationAngle += 90.0
            minAreaRectWidth, minAreaRectHeight = minAreaRectHeight, minAreaRectWidth
        rotationMatrix = cv.getRotationMatrix2D(center, rotationAngle, 1.0)
        rotatedImg = cv.warpAffine(
                sheetImg,
                rotationMatrix,
                (sheetImgHeight, sheetImgWidth),
                flags=cv.INTER_CUBIC,
                borderMode=cv.BORDER_REPLICATE)
        outputSheetImg = cv.getRectSubPix(
                rotatedImg,
                (int(minAreaRectWidth), int(minAreaRectHeight)),
                center)

        self._foregroundImgs.append(foregroundImg)
        self._rotatedImgs.append(rotatedImg)
        self._outputSheetImgs.append(outputSheetImg)
        return True

    def biggestComponentFromAdaptiveThreshold(self, grayImg):
        adaptiveThresholdImg = cv.adaptiveThreshold(cv.medianBlur(grayImg,7), 255,
                cv.ADAPTIVE_THRESH_GAUSSIAN_C, cv.THRESH_BINARY,11,2)
        denoisedAdaptiveThresholdImg = cv.erode(adaptiveThresholdImg, cv.getStructuringElement(cv.MORPH_RECT,
                (2,2)), 1)
        dilatedAdaptiveThresholdImg = cv.dilate(denoisedAdaptiveThresholdImg, cv.getStructuringElement(cv.MORPH_RECT,
                (7,7)), 1)
        erodedAdaptiveThresholdImg = np.where(
                cv.erode(dilatedAdaptiveThresholdImg, self._mediumKernel, 1) == 0,
                np.uint8(255.0), np.uint8(0.0))
        numComponents, labeledAdaptiveThresholdImg = cv.connectedComponents(erodedAdaptiveThresholdImg)

        self._adaptiveThresholdImgs.append(adaptiveThresholdImg)
        self._denoisedAdaptiveThresholdImgs.append(denoisedAdaptiveThresholdImg)
        self._dilatedAdaptiveThresholdImgs.append(dilatedAdaptiveThresholdImg)
        self._erodedAdaptiveThresholdImgs.append(erodedAdaptiveThresholdImg)
        self._labeledAdaptiveThresholdImgs.append(labeledAdaptiveThresholdImg)

        if numComponents < 2:
            self._log.debug('unable to identify biggest component from adaptive threshold img')
            return None
        else:
            minAreaRect, biggestComponentImg = self.biggestComponentMinAreaRect(
                    numComponents,
                    labeledAdaptiveThresholdImg,
                    erodedAdaptiveThresholdImg)
            return biggestComponentImg

    def biggestComponentMinAreaRect(self, numComponents, labeledImg, img):
        """
        Find the biggest white component in the img.
        Returns its minAreaRect and a black white image of the component.
        """
        componentIndices = [np.where(labeledImg == label) for label in range(numComponents)]
        componentAreas = [len(idx[0]) for idx in componentIndices]
        componentColors = [np.median(img[idx]) for idx in componentIndices]
        self._log.debug('component colors (label, median color) = {}', [(idx, color) for idx, color in enumerate(componentColors)])

        # filter out black components, sort by size
        components = [(label, componentAreas[label], componentColors[label]) for label in
                range(numComponents) if componentColors[label] != 0]
        components.sort(key = lambda x: x[1], reverse=True)
        self._log.debug('components (label, size) = {}', list(components))

        selectedLabel = components[0][0]
        self._log.debug(f'selectedLabel = {selectedLabel}')
        selectedImg = np.where(labeledImg == selectedLabel,
                np.uint8(255.0), np.uint8(0.0))
        contours, _ = cv.findContours(selectedImg, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
        minAreaRect = cv.minAreaRect(contours[0])
        box = np.int0(cv.boxPoints(minAreaRect))
        cv.drawContours(selectedImg, [box], 0, (255), thickness=cv.FILLED)
        return minAreaRect, selectedImg

    def writeOutput(self):
        def writeImg(img, sheetIdx, imgName):
            if img is not None:
                cv.imwrite(f'{self.prefix}_{sheetIdx}_{imgName}.jpg', img)

        writeImg(self._inputImg, 0, '00_input.jpg')
        for idx, img in enumerate(self._grayImgs):
            writeImg(img, idx, '01_grayImg.jpg')
        for idx, img in enumerate(self._adaptiveThresholdImgs):
            writeImg(img, idx, '02_adaptiveThresholdImg.jpg')
        for idx, img in enumerate(self._denoisedAdaptiveThresholdImgs):
            writeImg(img, idx, '03_denoisedAdaptiveThresholdImg.jpg')
        for idx, img in enumerate(self._dilatedAdaptiveThresholdImgs):
            writeImg(img, idx, '04_dilatedAdaptiveThresholdImg.jpg')
        for idx, img in enumerate(self._erodedAdaptiveThresholdImgs):
            writeImg(img, idx, '05_erodedAdaptiveThresholdImg.jpg')
        for idx, img in enumerate(self._labeledAdaptiveThresholdImgs):
            writeImg(img, idx, '06_labeledAdaptiveThresholdImg.jpg')
        for idx, img in enumerate(self._biggestComponentImgs):
            writeImg(img, idx, '07_biggestComponentImg.jpg')
        for idx, img in enumerate(self._otsuThresholdImgs):
            writeImg(img, idx, '08_otsuThresholdImg.jpg')
        for idx, img in enumerate(self._erodedOtsuThresholdImgs):
            writeImg(img, idx, '09_erodedOtsuThresholdImg.jpg')
        for idx, img in enumerate(self._dilatedOtsuThresholdImgs):
            writeImg(img, idx, '10_dilatedOtsuThresholdImg.jpg')
        for idx, img in enumerate(self._thresholdImgs):
            writeImg(img, idx, '11_thresholdImg.jpg')
        for idx, img in enumerate(self._labeledImgs):
            writeImg(img, idx, '12_labeledImg.jpg')
        for idx, img in enumerate(self._foregroundImgs):
            writeImg(img, idx, '13_foregroundImg.jpg')
        for idx, img in enumerate(self._rotatedImgs):
            writeImg(img, idx, '14_rotatedImg.jpg')
        for idx, img in enumerate(self._outputSheetImgs):
            writeImg(img, idx, '15_outputSheetImg.jpg')

    def generatedSheets(self):
        return [f'{self.prefix}_{idx}_14_outputSheetImg.jpg'
                for idx, _ in enumerate(self._outputSheetImgs)]

class RotateSheet(LineBasedStep):
    def __init__(self,
                 name,
                 outputDir = 'data/tmp/',
                 log = helpers.Log(),
                 minLineLength = 200,
                 rotPrecision = np.pi/720,
                 maxLineGap = 5,
                 voteThreshold = 10,
                 kernelSize = 2):
        super().__init__(name, outputDir, log)
        self._minLineLength = minLineLength
        self._rotPrecision = rotPrecision
        self._maxLineGap = maxLineGap
        self._voteThreshold = voteThreshold
        self._kernelSize = kernelSize

    def process(self, inputImg):
        super().process(inputImg)

        self._inputImg = inputImg
        self._grayImg = cv.cvtColor(inputImg,cv.COLOR_BGR2GRAY)
        self._cannyImg = cv.Canny(self._grayImg,50,150,apertureSize = 3)
        kernel = cv.getStructuringElement(cv.MORPH_RECT, (self._kernelSize,
            self._kernelSize))
        self._closedImg = cv.morphologyEx(self._cannyImg, cv.MORPH_CLOSE, kernel)
        self._dilatedImg = cv.dilate(self._closedImg, kernel, 1)
        self._linesImg = np.copy(inputImg)

        lines = cv.HoughLinesP(self._dilatedImg, 1, self._rotPrecision, 1,
                minLineLength=self._minLineLength, maxLineGap=self._maxLineGap)

        if lines is None:
            rotationAngle = 0
        else:
            rotationAngle = self.computeRotationAngle(lines)

        fillColor = self.determineBackgroundFillColor()

        # align inputImg
        rows,cols,_ = inputImg.shape
        rotMatrix = cv.getRotationMatrix2D((cols/2, rows/2), rotationAngle, 1)
        self._outputImg = cv.warpAffine(inputImg, rotMatrix, (cols, rows),
                borderMode=cv.BORDER_REPLICATE)

    def computeRotationAngle(self, lines):
        """
        Compute a rotation angle which aligns the given lines to the x/y-axis
        as good as possible.
        """

        # for each line, vote for the smallest correction angle that would
        # make it align to the vertical or horizontal axis (discretized to a
        # certain number of buckets)
        numBuckets = int(2*np.pi/self._rotPrecision)
        buckets = np.zeros(numBuckets)
        for line in lines:
            x1,y1,x2,y2 = line[0]
            alpha = self.minAngleToGridPts((x1,y1),(x2,y2))
            bucketIdx = int(round(alpha / self._rotPrecision))
            buckets[bucketIdx] += 1
            self.drawLinePts((x1, y1), (x2, y2))
            cv.putText(self._linesImg, "{}".format(alpha*180/np.pi), (x1, y1),
                    cv.FONT_HERSHEY_SIMPLEX, 3, 5, cv.LINE_AA)

        # discard votes for buckets that didn't get enough votes
        buckets = [numVotes if numVotes>self._voteThreshold else 0 for numVotes in buckets]
        self._log.debug(["numVotes={}, angle={}".format(v, idx*self._rotPrecision*180/np.pi)
            for idx, v in enumerate(buckets) if v>0])

        # compute the weighted average of all correction angles still in the game
        # Caution! these are angles, so we average them on the unit circle
        angles = [idx*self._rotPrecision for idx, _ in enumerate(buckets)]
        if sum(buckets)==0:
            self._log.warn("""not enough votes for any correction angle found,
            omitting image rotation""")
            angle = 0
        else:
            weights = buckets / sum(buckets)
            xSum, ySum = 0, 0
            for angle, weight in zip(angles, weights):
                xSum += math.cos(angle)*weight
                ySum += math.sin(angle)*weight
            angle = math.atan(ySum/xSum)
            if xSum < 0: angle += np.pi
            if xSum > 0 and ySum < 0: angle += 2*np.pi
            self._log.debug(["angle={}, weight={}".format(a, w) for a, w in zip(angles, weights) if w > 0.0])
        correctionAngleDeg = angle * 180 / np.pi
        self._log.debug("correctionAngleDeg={}".format(correctionAngleDeg))
        return correctionAngleDeg

    def determineBackgroundFillColor(self):
        # determine average color of all pixels that are bright enough to be
        # considered background
        hsv = cv.cvtColor(self._inputImg, cv.COLOR_BGR2HSV)
        mask = cv.inRange(hsv, np.array((0, 0, 100)),
                np.array(np.array((180, 255, 255))))
        self._fillColorPixels = cv.bitwise_and(self._inputImg, self._inputImg, mask=mask)
        bValues = self._fillColorPixels[:,:,0]
        gValues = self._fillColorPixels[:,:,1]
        rValues = self._fillColorPixels[:,:,2]
        return (bValues.sum() / (bValues != 0).sum(),
                gValues.sum() / (gValues != 0).sum(),
                rValues.sum() / (rValues != 0).sum())

    def writeOutput(self):
        cv.imwrite(f'{self.prefix}_0_input.jpg', self._inputImg)
        cv.imwrite(f'{self.prefix}_1_gray.jpg', self._grayImg)
        cv.imwrite(f'{self.prefix}_2_canny.jpg', self._cannyImg)
        cv.imwrite(f'{self.prefix}_3_closed.jpg', self._closedImg)
        cv.imwrite(f'{self.prefix}_4_dilated.jpg', self._dilatedImg)
        cv.imwrite(f'{self.prefix}_5_houghlines.jpg', self._linesImg)
        cv.imwrite(f'{self.prefix}_7_fillColorPixels.jpg', self._fillColorPixels)
        cv.imwrite(f'{self.prefix}_8_output.jpg', self._outputImg)

class RotateLabel(ProcessingStep):
    def __init__(self,
                 name,
                 outputDir = 'data/tmp/',
                 log = helpers.Log(),
                 kernelSize = 12,
                 borderSize = 20):
        super().__init__(name, outputDir, log)
        self._kernelSize = kernelSize
        self._borderSize = borderSize

    def process(self, inputImg, originalImg):
        super().process(inputImg)
        self._inputImg = cv.copyMakeBorder(inputImg, self._borderSize,
                self._borderSize, self._borderSize, self._borderSize,
                cv.BORDER_CONSTANT, value=(0, 0, 0))
        self._originalImg = cv.copyMakeBorder(originalImg, self._borderSize,
                self._borderSize, self._borderSize, self._borderSize,
                cv.BORDER_CONSTANT, value=(0, 0, 0))

        closingKernel = cv.getStructuringElement(cv.MORPH_RECT,
                (int(self._kernelSize*1.5), self._kernelSize))
        self._closedImg = cv.morphologyEx(self._inputImg, cv.MORPH_CLOSE,
                closingKernel)
        dilationKernel = cv.getStructuringElement(cv.MORPH_RECT,
                (self._kernelSize*4, self._kernelSize))
        self._dilatedImg = cv.dilate(self._closedImg, dilationKernel, 1)

        # select 2nd biggest component, assumed to be the actual text
        numComponents, self._labeledImg, stats, _ = \
                cv.connectedComponentsWithStats(self._dilatedImg)
        labels = [label for label in range(numComponents)]
        labels.sort(key = lambda label: stats[label, cv.CC_STAT_AREA],
                reverse=True)
        textLabel = labels[1] if numComponents > 1 else labels[0]
        self._selectedImg = np.where(self._labeledImg == textLabel,
                np.uint8(255.0), np.uint8(0.0))

        # prepare labeled components for graphical output
        if numComponents > 1:
            self._labeledImg = self._labeledImg / (numComponents-1) * 255
        elif numComponents == 1:
            self._labeledImg = self._labeledImg * 255

        # find minAreaRect
        self._minAreaImg = np.copy(self._selectedImg)
        contours, _ = cv.findContours(self._selectedImg, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
        minAreaRect = cv.minAreaRect(contours[0])
        center, (minAreaRectWidth, minAreaRectHeight), rotationAngle = minAreaRect
        self._minAreaImg = cv.cvtColor(self._minAreaImg, cv.COLOR_GRAY2BGR)
        cv.drawContours(self._minAreaImg,[np.int0(cv.boxPoints(minAreaRect))],0,(0,0,255),2)

        # extract the rotated minAreaRect from the original
        # cudos to http://felix.abecassis.me/2011/10/opencv-rotation-deskewing/
        if rotationAngle < -45.0:
            rotationAngle += 90.0
            minAreaRectWidth, minAreaRectHeight = minAreaRectHeight, minAreaRectWidth
        rotationMatrix = cv.getRotationMatrix2D(center, rotationAngle, 1.0)
        minAreaImgHeight, minAreaImgWidth, _ = self._minAreaImg.shape
        self._minAreaRotatedImg = cv.warpAffine(self._minAreaImg, rotationMatrix, (minAreaImgWidth, minAreaImgHeight), flags=cv.INTER_CUBIC, borderMode=cv.BORDER_REPLICATE)
        originalImgHeight, originalImgWidth, _ = self._originalImg.shape
        rotatedImg = cv.warpAffine(self._originalImg, rotationMatrix, (originalImgWidth, originalImgHeight), flags=cv.INTER_CUBIC, borderMode=cv.BORDER_REPLICATE)
        self._outputImg = cv.getRectSubPix(rotatedImg, (int(minAreaRectWidth), int(minAreaRectHeight)), center)

    def writeOutput(self):
        cv.imwrite(f'{self.prefix}_0_input.jpg', self._inputImg)
        cv.imwrite(f'{self.prefix}_1_closed.jpg', self._closedImg)
        cv.imwrite(f'{self.prefix}_2_dilated.jpg', self._dilatedImg)
        cv.imwrite(f'{self.prefix}_3_labeled.jpg', self._labeledImg)
        cv.imwrite(f'{self.prefix}_4_selected.jpg', self._selectedImg)
        cv.imwrite(f'{self.prefix}_5_minArea.jpg', self._minAreaImg)
        cv.imwrite(f'{self.prefix}_6_minAreaRotated.jpg', self._minAreaRotatedImg)
        cv.imwrite(f'{self.prefix}_7_output.jpg', self._outputImg)

class FindMarginsByLines(LineBasedStep):
    class Corner:
        def __init__(self, x, y):
            self.points = []
            self.addPoint(x, y)

        def addPoint(self, x, y):
            self.points.append((x, y))
            xSum, ySum = 0, 0
            for x0, y0 in self.points:
                xSum += x0
                ySum += y0
            self.x = int(round(xSum / len(self.points)))
            self.y = int(round(ySum / len(self.points)))

        def distanceToPoint(self, x, y):
            return math.sqrt(pow(x-self.x, 2) + pow(y-self.y, 2))

    threshold = 180
    def __init__(self,
                 name,
                 outputDir = 'data/tmp/',
                 log = helpers.Log(),
                 minLineLength = 800,
                 rotPrecision = np.pi/4,
                 maxLineGap = 1,
                 kernelSize = 9,
                 minExpectedImageSize = 800*600): # TODO: set correctly when merging patch to expect a certain input image size / rescaling
        super().__init__(name, outputDir, log)
        self._minLineLength = minLineLength
        self._rotPrecision = rotPrecision
        self._maxLineGap = maxLineGap
        self._cornerRadius = 6
        self._kernelSize = kernelSize
        self._minExpectedImageSize = minExpectedImageSize

    def process(self, inputImg):
        super().process(inputImg)
        self._frameImg = np.copy(inputImg)
        self._grayImg = cv.cvtColor(inputImg,cv.COLOR_BGR2GRAY)
        self._cannyImg = cv.Canny(self._grayImg,50,150,apertureSize = 3)
        kernel = cv.getStructuringElement(cv.MORPH_RECT, (self._kernelSize,
            self._kernelSize))
        self._closedImg = cv.morphologyEx(self._cannyImg, cv.MORPH_CLOSE, kernel)
        self._closingImg = cv.dilate(self._closedImg, kernel, 1)
        _, self._thresholdImg = cv.threshold(self._grayImg, self.threshold, 1,
                cv.THRESH_BINARY_INV)

        self._linesImg = np.copy(inputImg)
        lines = cv.HoughLinesP(self._closingImg, 1, self._rotPrecision, 1,
                minLineLength=self._minLineLength, maxLineGap=self._maxLineGap)

        # map the end points of each line we found to a candidate corner
        corners = []
        def mapToCorner(x, y):
            foundCorner = False
            for c in corners:
                if c.distanceToPoint(x, y) < self._cornerRadius:
                    c.addPoint(x, y)
                    foundCorner = True
                    break
            if not foundCorner:
                corners.append(FindMarginsByLines.Corner(x, y))

        if lines is None:
            self._outputImg=inputImg
            return

        for line in lines:
            x1,y1,x2,y2 = line[0]
            mapToCorner(x1, y1)
            mapToCorner(x2, y2)
            self.drawLinePts((x1, y1), (x2, y2))

        # select the corners closest to the image corners (top left, top right, bottom left, bottom right)
        # they probably span the frame printed on each product sheet
        if len(corners) < 4:
            self._log.warn('failed to find enough corner candidates, not cropping image')
            self._outputImg=inputImg
            return

        height, width, _ = self._linesImg.shape
        topLeft, topLeftDist = corners[0], width+height
        topRight, topRightDist = topLeft, topLeftDist
        bottomLeft, bottomLeftDist = topLeft, topLeftDist
        bottomRight, bottomRightDist = topLeft, topLeftDist
        for c in corners:
            cv.circle(self._linesImg, (c.x, c.y), self._cornerRadius, (0,255,0), 2)
            if c.distanceToPoint(0, 0) < topLeftDist:
                topLeft = c
                topLeftDist = c.distanceToPoint(0, 0)
            if c.distanceToPoint(width, 0) < topRightDist:
                topRight = c
                topRightDist = c.distanceToPoint(width, 0)
            if c.distanceToPoint(0, height) < bottomLeftDist:
                bottomLeft = c
                bottomLeftDist = c.distanceToPoint(0, height)
            if c.distanceToPoint(width, height) < bottomRightDist:
                bottomRight = c
                bottomRightDist = c.distanceToPoint(width, height)

        # draw corners and selected rectangle, crop output image
        cv.circle(self._frameImg, (topLeft.x, topLeft.y), self._cornerRadius, (255,0,0), 2)
        cv.circle(self._frameImg, (topRight.x, topRight.y), self._cornerRadius, (255,0,0), 2)
        cv.circle(self._frameImg, (bottomRight.x, bottomRight.y), self._cornerRadius, (255,0,0), 2)
        cv.circle(self._frameImg, (bottomLeft.x, bottomLeft.y), self._cornerRadius, (255,0,0), 2)
        x0, y0 = min(topLeft.x, bottomLeft.x), min(topLeft.y, topRight.y)
        x1, y1 = max(topRight.x, bottomRight.x), max(bottomLeft.y, bottomRight.y)
        if (x1-x0)*(y1-y0) < self._minExpectedImageSize:
            self._log.debug(f'x0={x0}, y0={y0}, x1={x1}, y1={y1}')
            self._log.warn('Failed to find plausible image margins, not cropping image')
            x0, y0 = 0, 0
            x1, y1 = width, height
        cv.rectangle(self._frameImg, (x0, y0), (x1, y1), (255,0,0), 9)
        self._outputImg=inputImg[y0:y1, x0:x1]

    def writeOutput(self):
        cv.imwrite(f'{self.prefix}_0_gray.jpg', self._grayImg)
        cv.imwrite(f'{self.prefix}_01_canny.jpg', self._cannyImg)
        cv.imwrite(f'{self.prefix}_02_closed.jpg', self._closedImg)
        cv.imwrite(f'{self.prefix}_03_closing.jpg', self._closingImg)
        cv.imwrite(f'{self.prefix}_1_threshold.jpg', self._thresholdImg*255)
        cv.imwrite(f'{self.prefix}_2_linesImg.jpg', self._linesImg)
        cv.imwrite(f'{self.prefix}_3_frames.jpg', self._frameImg)
        cv.imwrite(f'{self.prefix}_4_output.jpg', self._outputImg)

class FitToSheet(ProcessingStep):
    (frameP0, frameP1) = ProductSheet.getPageFramePts()

    def process(self, inputImg):
        super().process(inputImg)
        xMargin,yMargin = self.frameP0
        wMargin,hMargin = np.subtract(self.frameP1, self.frameP0)
        self._resizedImg = cv.resize(inputImg,(wMargin, hMargin))
        self._outputImg = cv.copyMakeBorder(self._resizedImg,yMargin,yMargin,xMargin,xMargin,cv.BORDER_CONSTANT,value=(255,255,255))

    def writeOutput(self):
        cv.imwrite(f'{self.prefix}_0_resizedImg.jpg', self._resizedImg)
        cv.imwrite(f'{self.prefix}_1_output.jpg', self._outputImg)

class RecognizeText(ProcessingStep):
    def __init__(self,
            name,
            outputDir,
            db,
            marginSize = 5,
            minComponentArea = 100,
            minNormalizedAspectRatio = .1,
            confidenceThreshold = 0.5,
            log = helpers.Log()
            ):
        super().__init__(name, outputDir, log)
        self.__db = db
        self.__sheet = ProductSheet()
        self.__fallbackSheetName = None
        self.__fallbackPageNumber = 0
        self.marginSize = marginSize
        self.confidenceThreshold = confidenceThreshold
        self.minComponentArea = minComponentArea
        self.minNormalizedAspectRatio = minNormalizedAspectRatio

    def productId(self):
        return self.__sheet.productId()

    def pageNumber(self):
        return self.__sheet.pageNumber

    def fileName(self):
        return self.__sheet.fileName()

    def prepareProcessing(self, fallbackSheetName):
        self.__fallbackSheetName = fallbackSheetName
        self.__fallbackPageNumber += 1

    def process(self, inputImg):
        assert(self.__fallbackSheetName is not None)

        super().process(inputImg)
        self._inputImg = inputImg
        self._grayImg = cv.cvtColor(inputImg,cv.COLOR_BGR2GRAY)
        self._thresholdImg = 255-cv.adaptiveThreshold(self._grayImg, 255,
                cv.ADAPTIVE_THRESH_GAUSSIAN_C, cv.THRESH_BINARY,11,3)
        closingKernel = cv.getStructuringElement(cv.MORPH_RECT, (5,5))
        self._closedImg = cv.morphologyEx(self._thresholdImg, cv.MORPH_CLOSE,
                closingKernel)
        openingKernel = cv.getStructuringElement(cv.MORPH_RECT, (4,4))
        self._openedImg = cv.morphologyEx(self._closedImg, cv.MORPH_OPEN,
                openingKernel)

        # prepare choices
        maxNumPages = self.__db.config.getint('tagtrail_gen',
                'max_num_pages_per_product')
        pageNumberString = self.__db.config.get('tagtrail_gen', 'page_number_string')
        pageNumbers = [pageNumberString.format(pageNumber=str(n)).upper()
                            for n in range(1, maxNumPages+1)]
        currency = self.__db.config.get('general', 'currency')
        names, units, prices = map(set, zip(*[
            (p.description.upper(),
             p.amountAndUnit.upper(),
             helpers.formatPrice(p.grossSalesPrice(), currency).upper())
            for p in self.__db.products.values()]))
        memberIds = [m.id for m in self.__db.members.values()]
        self._log.debug(f'names={list(names)}, units={list(units)}, prices={list(prices)}, ' +
                f'memberIds={list(memberIds)}, pageNumbers={list(pageNumbers)}')

        self._recognizedBoxTexts = {}
        for box in self.__sheet.boxes():
            if box.name == "nameBox":
                name, confidence = self.recognizeBoxText(box, names)
                if name == '' or confidence < 0.5:
                    box.text, box.confidence = self.__fallbackSheetName, 0
                else:
                    box.text, box.confidence = name, confidence
            elif box.name == "unitBox":
                box.text, box.confidence = self.recognizeBoxText(box, units)
                if box.text == '':
                    box.confidence = 0
            elif box.name == "priceBox":
                box.text, box.confidence = self.recognizeBoxText(box, prices)
                if box.text == '' or confidence < 1:
                    box.confidence = 0
            elif box.name == "pageNumberBox":
                pageNumber, confidence = self.recognizeBoxText(box,
                        pageNumbers)
                if pageNumber == '' or confidence < 1:
                    box.text, box.confidence = str(self.__fallbackPageNumber), 0
                else:
                    box.text, box.confidence = pageNumber, confidence
            elif box.name.find("dataBox") != -1:
                box.text, box.confidence = self.recognizeBoxText(box, memberIds)
            else:
                box.text, box.confidence = ("", 1.0)

        # try to fill in product infos if id is clear
        nameBox = self.__sheet.boxByName('nameBox')
        unitBox = self.__sheet.boxByName('unitBox')
        priceBox = self.__sheet.boxByName('priceBox')
        pageNumberBox = self.__sheet.boxByName('pageNumberBox')
        if nameBox.confidence == 1:
            product = self.__db.products[self.__sheet.productId()]
            expectedAmountAndUnit = product.amountAndUnit.upper()
            expectedPrice = helpers.formatPrice(product.grossSalesPrice(), currency).upper()
            if unitBox.confidence < 1:
                self._log.info(f'Inferred unit={expectedAmountAndUnit}')
                unitBox.text = expectedAmountAndUnit
                unitBox.confidence = 1
            elif unitBox.text != expectedAmountAndUnit:
                unitBox.confidence = 0
            if priceBox.confidence < 1:
                self._log.info(f'Inferred price={expectedPrice}')
                priceBox.text = expectedPrice
                priceBox.confidence = 1
            elif priceBox.text != expectedPrice:
                priceBox.confidence = 0
            if (product.previousQuantity < ProductSheet.maxQuantity()
                    and pageNumberBox.text == ''):
                # previousQuantity might also be small because many units were
                # already sold, while we still have more than one sheet
                # => this is just a good guess
                pageNumberBox.confidence = 0
                pageNumberBox.text = self.__db.config.get('tagtrail_gen',
                        'page_number_string').format(pageNumber='1')
                self._log.info(f'Inferred pageNumber={pageNumberBox.text}')

        # assume box should be filled if at least two neighbours are filled
        for box in self.__sheet.boxes():
            if box.text != '' or box.confidence == 0:
                continue
            numFilledNeighbours = 0
            for direction in ["Up", "Down", "Left", "Right"]:
                neighbourBox = self.__sheet.neighbourBox(box, direction)
                if neighbourBox is not None and neighbourBox.text != '':
                    numFilledNeighbours += 1
            if 2 <= numFilledNeighbours:
                box.confidence = 0

        for box in self.__sheet.boxes():
            if box.confidence < self.confidenceThreshold:
                box.bgColor = (0, 0, 80)

        self._outputImg = self.__sheet.createImg()

    """
    Returns (text, confidence) among candidateTexts
    """
    def recognizeBoxText(self, box, candidateTexts):
        (x0,y0),(x1,y1)=box.pt1,box.pt2
        openedImg = self._openedImg[y0-self.marginSize:y1+self.marginSize,
                x0-self.marginSize:x1+self.marginSize]
        originalImg = self._inputImg[y0-self.marginSize:y1+self.marginSize,
                x0-self.marginSize:x1+self.marginSize]
        cv.imwrite(f'{self.prefix}_0_{box.name}_0_originalImg.jpg', originalImg)
        cv.imwrite(f'{self.prefix}_0_{box.name}_1_openedImg.jpg', openedImg)

        numComponents, labeledImg, stats, _ = cv.connectedComponentsWithStats(openedImg)

        # find components touching the border of the image
        height, width = labeledImg.shape
        componentsTouchingBorder = set()
        for x in range(width):
            componentsTouchingBorder.add(labeledImg[0,x])
            componentsTouchingBorder.add(labeledImg[height-1,x])
        for y in range(height):
            componentsTouchingBorder.add(labeledImg[y,0])
            componentsTouchingBorder.add(labeledImg[y,width-1])
        self._log.debug(f'componentsTouchingBorder={list(componentsTouchingBorder)}')

        # remove spurious components
        bordersCleanedImg = labeledImg
        for label in range(numComponents):
            componentWidth = stats[label, cv.CC_STAT_WIDTH]
            componentHeight = stats[label, cv.CC_STAT_HEIGHT]
            normalizedAspectRatio = (min(componentWidth, componentHeight) /
                    max(componentWidth, componentHeight))
            self._log.debug(f'stats[label, cv.CC_STAT_WIDTH]={componentWidth}')
            self._log.debug(f'stats[label, cv.CC_STAT_HEIGHT]={componentHeight}')
            self._log.debug(f'normalizedAspectRatio={normalizedAspectRatio}')
            self._log.debug(f'stats[label, cv.CC_STAT_AREA]={stats[label, cv.CC_STAT_AREA]}')
            if (label in componentsTouchingBorder
                    or stats[label, cv.CC_STAT_AREA] < self.minComponentArea
                    or normalizedAspectRatio < self.minNormalizedAspectRatio):
                bordersCleanedImg = np.where(bordersCleanedImg == label,
                        np.uint8(0.0), bordersCleanedImg)

        bordersCleanedImg = np.where(bordersCleanedImg == 0,
                        np.uint8(0.0), np.uint8(255.0))
        labeledImg = labeledImg / numComponents * 255
        cv.imwrite(f'{self.prefix}_0_{box.name}_2_labeledImg.jpg', labeledImg)
        cv.imwrite(f'{self.prefix}_0_{box.name}_3_bordersCleanedImg.jpg', bordersCleanedImg)

        # assume empty box if not enough components are recognized
        numComponents, _ = cv.connectedComponents(bordersCleanedImg)
        self._log.debug(f'bordersCleanedImg of {box.name} has numComponents={numComponents}')
        if numComponents < 4:
            box.bgColor = (255, 0, 0)
            return ("", 1.0)

        p = RotateLabel(f'_0_{box.name}_3_rotation', self.prefix,
                log=self._log)
        p.process(bordersCleanedImg, originalImg)
        p.writeOutput()
        img = p._outputImg

        filename = f'{self.prefix}_0_{box.name}_4_ocrImage.jpg'
        cv.imwrite(filename, img)
        ocrText = pytesseract.image_to_string(PIL.Image.open(filename),
                config="--psm 7")

        confidence, text = self.findClosestString(ocrText.upper(), candidateTexts)
        self._log.info("(ocrText, confidence, text) = ({}, {}, {})", ocrText, confidence, text)
        return (text, confidence)

    def findClosestString(self, string, strings):
        strings=list(set(strings))
        self._log.debug("findClosestString: string={}, strings={}", string,
                strings)
        dists = list(map(lambda x: Levenshtein.distance(x, string), strings))
        self._log.debug("dists={}", dists)
        minDist, secondDist = np.partition(dists, 1)[:2]
        if minDist > 5 or minDist == secondDist:
            return 0, ""
        confidence = 1 - minDist / secondDist
        return confidence, strings[dists.index(minDist)]

    def resetSheetToFallback(self):
        self.__sheet.name = self.__fallbackSheetName
        self.__sheet.pageNumber = self.__fallbackPageNumber

    def storeSheet(self, outputDir):
        self.__sheet.store(outputDir)

    def writeOutput(self):
        cv.imwrite(f'{self.prefix}_1_grayImg.jpg', self._grayImg)
        cv.imwrite(f'{self.prefix}_2_thresholdImg.jpg', self._thresholdImg)
        cv.imwrite(f'{self.prefix}_3_closedImg.jpg', self._closedImg)
        cv.imwrite(f'{self.prefix}_4_openedImg.jpg', self._openedImg)
        cv.imwrite(f'{self.prefix}_5_output.jpg', self._outputImg)

class SplitSheetDialog(Dialog):
    canvasScreenPercentage = 0.75

    def __init__(self,
            root,
            inputImg,
            log = helpers.Log(helpers.Log.LEVEL_DEBUG)):
        self.inputImg = inputImg
        self.log = log
        self.outputImg = None
        self.isEmpty = False
        self.selectedCorners = []
        self.sheetCoordinates = None
        super().__init__(root)

    def body(self, master):
        self.width=master.winfo_screenwidth() * self.canvasScreenPercentage
        self.height=master.winfo_screenheight() * self.canvasScreenPercentage
        o_h, o_w, _ = self.inputImg.shape
        aspectRatio = min(self.height / o_h, self.width / o_w)
        canvas_h, canvas_w = int(o_h * aspectRatio), int(o_w * aspectRatio)
        resizedImg = cv.resize(self.inputImg, (canvas_w, canvas_h), Image.BILINEAR)
        self.resizedImg = ImageTk.PhotoImage(Image.fromarray(resizedImg))
        self.log.debug(f'canvas_w, canvas_h = {canvas_w}, {canvas_h}')

        self.canvas = tkinter.Canvas(master,
               width=canvas_w,
               height=canvas_h)
        self.canvas.bind("<Button-1>", self.onMouseDown)
        self.canvas.pack()
        self.resetCanvas()
        return None

    def buttonbox(self):
        box = tkinter.Frame(self)

        w = tkinter.Button(box, text="OK", width=10, command=self.ok,
                default=tkinter.ACTIVE)
        w.pack(side=tkinter.LEFT, padx=5, pady=5)
        w = tkinter.Button(box, text="Cancel", width=10, command=self.cancel)
        w.pack(side=tkinter.LEFT, padx=5, pady=5)
        w = tkinter.Button(box, text="Empty sheet", width=10, command=self.markEmpty)
        w.pack(side=tkinter.LEFT, padx=5, pady=5)

        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)

        box.pack()

    def markEmpty(self):
        self.isEmpty = True
        self.ok()

    def apply(self):
        if self.sheetCoordinates is not None:
            height, width, _ = self.inputImg.shape
            x0, y0 = int(self.sheetCoordinates[0]*width), int(self.sheetCoordinates[1]*height)
            x1, y1 = int(self.sheetCoordinates[2]*width), int(self.sheetCoordinates[3]*height)
            self.outputImg = np.copy(self.inputImg[y0:y1, x0:x1, :])

    def onMouseDown(self, event):
        if len(self.selectedCorners) < 2:
            self.selectedCorners.append([event.x, event.y])

        if len(self.selectedCorners) == 2:
            self.update()
            canvasWidth = self.canvas.winfo_width()
            canvasHeight = self.canvas.winfo_height()
            self.sheetCoordinates = [
                    self.selectedCorners[0][0] / canvasWidth,
                    self.selectedCorners[0][1] / canvasHeight,
                    self.selectedCorners[1][0] / canvasWidth,
                    self.selectedCorners[1][1] / canvasHeight
                    ]
            self.selectedCorners = []

        self.resetCanvas()

    def resetCanvas(self):
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=tkinter.NW, image=self.resizedImg)

        for corners in self.selectedCorners:
            r = 2
            self.canvas.create_oval(
                    corners[0]-r,
                    corners[1]-r,
                    corners[0]+r,
                    corners[1]+r,
                    outline = 'red')

        self.update()
        canvasWidth = self.canvas.winfo_width()
        canvasHeight = self.canvas.winfo_height()
        if self.sheetCoordinates is not None:
            self.canvas.create_rectangle(
                    self.sheetCoordinates[0] * canvasWidth,
                    self.sheetCoordinates[1] * canvasHeight,
                    self.sheetCoordinates[2] * canvasWidth,
                    self.sheetCoordinates[3] * canvasHeight,
                    outline = 'green',
                    width = 2)

class SplitSheet():
    def __init__(self,
            inputScanFilepath,
            name,
            tmpDir,
            inputImg,
            outputImg,
            isEmpty
            ):
        self.inputScanFilepath=inputScanFilepath
        self.name=name
        self.tmpDir=tmpDir
        self.inputImg=inputImg
        self.outputImg=outputImg
        self.isEmpty=isEmpty

class GUI():
    previewColumnCount = 4
    progressBarLength = 400
    buttonFrameWidth = 200
    previewScrollbarWidth = 20

    def __init__(self,
            tmpDir,
            scanDir,
            outputDir,
            scanFilenames,
            db,
            log = helpers.Log(helpers.Log.LEVEL_DEBUG)):
        self.tmpDir = tmpDir
        self.scanDir = scanDir
        self.outputDir = outputDir
        self.scanFilenames = scanFilenames
        self.activeScanIdx = 0
        self.db = db
        self.log = log
        self.selectedCorners = []
        self.sheetCoordinates = list(range(4))
        self.setActiveSheet(None)
        self.loadConfig()

        self.root = tkinter.Tk()
        self.rotationAngle = self.db.config.getint('tagtrail_ocr', 'rotationAngle')
        self.width = self.db.config.getint('general', 'screen_width')
        self.height = self.db.config.getint('general', 'screen_height')
        if self.width == -1:
            self.width=self.root.winfo_screenwidth()
        if self.height == -1:
            self.height=self.root.winfo_screenheight()
        self.root.geometry(str(self.width)+'x'+str(self.height))
        self.initGUI()
        self.root.mainloop()

    def rotateImage90(self):
        self.rotationAngle = (self.rotationAngle + 90) % 360
        self.resetGUI()

    def initGUI(self):
        # canvas with first scan to configure rotation and select sheet areas
        scannedImg = cv.imread(self.scanDir + self.scanFilenames[self.activeScanIdx])
        rotatedImg = imutils.rotate_bound(scannedImg, self.rotationAngle)

        o_h, o_w, _ = rotatedImg.shape
        aspectRatio = min(self.height / o_h, (self.width - self.buttonFrameWidth - self.previewScrollbarWidth) / 2 / o_w)
        canvas_h, canvas_w = int(o_h * aspectRatio), int(o_w * aspectRatio)
        resizedImg = cv.resize(rotatedImg, (canvas_w, canvas_h), Image.BILINEAR)

        # Note: it is necessary to store the image locally for tkinter to show it
        self.resizedImg = ImageTk.PhotoImage(Image.fromarray(resizedImg))
        self.scanCanvas = tkinter.Canvas(self.root,
               width=canvas_w,
               height=canvas_h)
        self.scanCanvas.place(x=0, y=0)
        self.scanCanvas.bind("<Button-1>", self.onMouseDownOnScanCanvas)
        self.resetScanCanvas()

        # preview of split sheets with the current configuration
        self.previewCanvas = tkinter.Canvas(self.root,
               width=self.width - self.buttonFrameWidth - self.previewScrollbarWidth - canvas_w,
               height=canvas_h)
        self.previewCanvas.configure(scrollregion=self.previewCanvas.bbox("all"))
        self.previewCanvas.place(x=canvas_w, y=0)
        self.previewCanvas.bind('<Button-1>', self.onMouseDownOnPreviewCanvas)
        # with Windows OS
        self.previewCanvas.bind("<MouseWheel>", self.onMouseWheelPreviewCanvas)
        # with Linux OS
        self.previewCanvas.bind("<Button-4>", self.onMouseWheelPreviewCanvas)
        self.previewCanvas.bind("<Button-5>", self.onMouseWheelPreviewCanvas)
        self.scrollPreviewY = tkinter.Scrollbar(self.root, orient='vertical', command=self.previewCanvas.yview)
        self.scrollPreviewY.place(
                x=self.width - self.buttonFrameWidth - self.previewScrollbarWidth,
                y=0,
                width=self.previewScrollbarWidth,
                height=self.height)
        self.previewCanvas.configure(yscrollcommand=self.scrollPreviewY.set)

        self.root.update()
        scanHeight, scanWidth, _ = imutils.rotate_bound(
                cv.imread(self.scanDir + self.scanFilenames[self.activeScanIdx]),
                self.rotationAngle).shape
        self.previewColumnWidth, self.previewRowHeight = 0, 0
        for sheetCoords in self.sheetCoordinates:
            width = (sheetCoords[2] - sheetCoords[0]) * scanWidth
            height = (sheetCoords[3] - sheetCoords[1]) * scanHeight
            resizeRatio = self.previewCanvas.winfo_width() / (self.previewColumnCount * width)
            resizedWidth, resizedHeight = int(width * resizeRatio), int(height * resizeRatio)
            self.previewColumnWidth = max(self.previewColumnWidth, resizedWidth)
            self.previewRowHeight = max(self.previewRowHeight, resizedHeight)

        # Additional buttons
        self.buttonFrame = tkinter.Frame(self.root,
               width=self.buttonFrameWidth,
               height=canvas_h)
        self.buttonFrame.place(x=self.width - self.buttonFrameWidth, y=0)
        self.buttons = {}

        self.buttons['loadConfig'] = tkinter.Button(self.buttonFrame, text='Load configuration',
            command=self.loadConfigAndResetGUI)
        self.buttons['loadConfig'].bind('<Return>', self.loadConfigAndResetGUI)
        self.buttons['saveConfig'] = tkinter.Button(self.buttonFrame, text='Save configuration',
            command=self.saveConfig)
        self.buttons['saveConfig'].bind('<Return>', self.saveConfig)

        self.buttons['rotateImage90'] = tkinter.Button(self.buttonFrame, text='Rotate image',
            command=self.rotateImage90)
        self.buttons['rotateImage90'].bind('<Return>', self.rotateImage90)

        for idx in range(4):
            self.buttons[f'activateSheet{idx}'] = tkinter.Button(self.buttonFrame, text=f'Edit sheet {idx}',
                command=functools.partial(self.setActiveSheet, idx))
            self.buttons[f'activateSheet{idx}'].bind('<Return>', functools.partial(self.setActiveSheet, idx))

        self.buttons['splitSheets'] = tkinter.Button(self.buttonFrame, text='Split sheets',
            command=self.splitSheets)
        self.buttons['splitSheets'].bind('<Return>', self.splitSheets)

        self.buttons['recognizeTags'] = tkinter.Button(self.buttonFrame, text='Recognize tags',
            command=self.recognizeTags)
        self.buttons['recognizeTags'].bind('<Return>', self.recognizeTags)
        self.buttons['recognizeTags'].config(state='disabled')

        self.buttons['splitAndRecognize'] = tkinter.Button(self.buttonFrame, text='Split&Recognize',
            command=self.splitAndRecognize)
        self.buttons['splitAndRecognize'].bind('<Return>', self.splitAndRecognize)

        y = 60
        for b in self.buttons.values():
            b.place(relx=.5, y=y, anchor="center",
                    width=.8*self.buttonFrameWidth)
            b.update()
            y += b.winfo_height()

    def destroyCanvas(self):
        self.scanCanvas.destroy()
        self.buttonFrame.destroy()

    def resetGUI(self):
        self.destroyCanvas()
        self.initGUI()

    def loadConfigAndResetGUI(self):
        self.loadConfig()
        self.resetGUI()

    def loadConfig(self):
        self.rotationAngle = self.db.config.getint('tagtrail_ocr', 'rotationAngle')
        self.sheetCoordinates[0] = list(map(float,
            self.db.config.getcsvlist('tagtrail_ocr', 'sheet0_coordinates')))
        self.sheetCoordinates[1] = list(map(float,
            self.db.config.getcsvlist('tagtrail_ocr', 'sheet1_coordinates')))
        self.sheetCoordinates[2] = list(map(float,
            self.db.config.getcsvlist('tagtrail_ocr', 'sheet2_coordinates')))
        self.sheetCoordinates[3] = list(map(float,
            self.db.config.getcsvlist('tagtrail_ocr', 'sheet3_coordinates')))

    def saveConfig(self):
        self.db.config.set('tagtrail_ocr', 'rotationAngle', str(self.rotationAngle))
        self.db.config.set('tagtrail_ocr', 'sheet0_coordinates', str(', '.join(map(str, self.sheetCoordinates[0]))))
        self.db.config.set('tagtrail_ocr', 'sheet1_coordinates', str(', '.join(map(str, self.sheetCoordinates[1]))))
        self.db.config.set('tagtrail_ocr', 'sheet2_coordinates', str(', '.join(map(str, self.sheetCoordinates[2]))))
        self.db.config.set('tagtrail_ocr', 'sheet3_coordinates', str(', '.join(map(str, self.sheetCoordinates[3]))))
        self.db.writeConfig()

    def setActiveSheet(self, index):
        self.activeSheetIndex = index

    def onMouseDownOnScanCanvas(self, event):
        if self.activeSheetIndex is None:
            return

        if len(self.selectedCorners) < 2:
            self.selectedCorners.append([event.x, event.y])

        if len(self.selectedCorners) == 2:
            self.root.update()
            canvasWidth = self.scanCanvas.winfo_width()
            canvasHeight = self.scanCanvas.winfo_height()
            self.sheetCoordinates[self.activeSheetIndex] = [
                    self.selectedCorners[0][0] / canvasWidth,
                    self.selectedCorners[0][1] / canvasHeight,
                    self.selectedCorners[1][0] / canvasWidth,
                    self.selectedCorners[1][1] / canvasHeight
                    ]
            self.selectedCorners = []
            self.setActiveSheet(None)

        self.resetScanCanvas()

    def resetScanCanvas(self):
        self.scanCanvas.delete("all")
        self.scanCanvas.create_image(0,0, anchor=tkinter.NW, image=self.resizedImg)

        for corners in self.selectedCorners:
            r = 2
            self.scanCanvas.create_oval(
                    corners[0]-r,
                    corners[1]-r,
                    corners[0]+r,
                    corners[1]+r,
                    outline = 'red')

        self.root.update()
        canvasWidth = self.scanCanvas.winfo_width()
        canvasHeight = self.scanCanvas.winfo_height()
        sheetColors = ['green', 'blue', 'red', 'orange']
        for sheetIndex, sheetCoords in enumerate(self.sheetCoordinates):
            if sheetIndex == self.activeSheetIndex:
                continue

            self.scanCanvas.create_rectangle(
                    sheetCoords[0] * canvasWidth,
                    sheetCoords[1] * canvasHeight,
                    sheetCoords[2] * canvasWidth,
                    sheetCoords[3] * canvasHeight,
                    outline = sheetColors[sheetIndex],
                    width = 2)

    def onMouseDownOnPreviewCanvas(self, event):
        assert(self.previewCanvas == event.widget)
        x = self.previewCanvas.canvasx(event.x)
        y = self.previewCanvas.canvasy(event.y)
        self.log.debug(f'clicked at {event.x}, {event.y} - ({x}, {y}) on canvas')

        row = int(y // self.previewRowHeight)
        col = int(x // self.previewColumnWidth)
        sheetIdx = row*self.previewColumnCount + col
        self.log.debug(f'clicked on preview {sheetIdx}, row={row}, col={col}')
        if len(self.sheets) <= sheetIdx:
            return

        sheet = self.sheets[sheetIdx]
        dialog = SplitSheetDialog(self.root, sheet.inputImg)
        if dialog.isEmpty:
            sheet.outputImg = self.crossedOutCopy(sheet.inputImg)
            sheet.isEmpty = True
            self.resetPreviewCanvas()
        elif dialog.outputImg is not None:
            helpers.recreateDir(sheet.tmpDir)
            sheet.outputImg = self.fitSplitSheet(dialog.outputImg, sheet.tmpDir)
            sheet.isEmpty = False
            self.resetPreviewCanvas()

    def onMouseWheelPreviewCanvas(self, event):
        increment = 0
        # respond to Linux or Windows wheel event
        if event.num == 5 or event.delta < 0:
            increment = 1
        if event.num == 4 or event.delta > 0:
            increment = -1
        self.previewCanvas.yview_scroll(increment, "units")

    def abortProcess(self):
        self.abortProcess = True
        if self.__progressWindow:
            self.__progressWindow.destroy()
            self.__progressWindow = None
        self.log.info('Aborting preview generation')

    def splitAndRecognize(self):
        self.splitSheets()
        self.recognizeTags()

    def splitSheets(self):
        self.sheets = []
        self.buttons['recognizeTags'].config(state='disabled')
        self.abortProcess = False

        self.__progressWindow = tkinter.Toplevel()
        self.__progressWindow.title('Splitting progress')
        self.__progressWindow.protocol("WM_DELETE_WINDOW", self.abortProcess)
        progressBar = tkinter.ttk.Progressbar(self.__progressWindow, length=self.progressBarLength, mode='determinate')
        progressBar.pack(pady=10, padx=20)
        abortButton = tkinter.Button(self.__progressWindow, text='Abort',
            command=self.abortProcess)
        abortButton.bind('<Return>', self.abortProcess)
        abortButton.pack(pady=10)

        for scanFileIndex, scanFile in enumerate(self.scanFilenames):
            if self.abortProcess:
                break
            progressBar['value'] = scanFileIndex / len(self.scanFilenames) * 100
            self.__progressWindow.update()

            splitDir = f'{self.tmpDir}/{scanFile}/'
            helpers.recreateDir(splitDir)

            splitter = SheetSplitter(
                    f'0_splitSheets',
                    splitDir,
                    self.sheetCoordinates[0],
                    self.sheetCoordinates[1],
                    self.sheetCoordinates[2],
                    self.sheetCoordinates[3])

            rotatedImg = imutils.rotate_bound(cv.imread(self.scanDir + scanFile), self.rotationAngle)
            resizedImg = cv.resize(rotatedImg, (3672, 6528), Image.BILINEAR)
            self.log.info(f'Splitting scanned file: {scanFile}')
            splitter.process(resizedImg)
            splitter.writeOutput()
            for idx, splitImg in enumerate(splitter._outputSheetImgs):
                if self.abortProcess:
                    break

                sheetName = f'{scanFile}_sheet{idx}'
                self.log.info(f'sheetName = {sheetName}')
                sheetTmpDir = f'{self.tmpDir}{sheetName}/'
                helpers.recreateDir(sheetTmpDir)
                if splitImg is None:
                    self.sheets.append(SplitSheet(
                        self.scanDir + scanFile,
                        sheetName,
                        sheetTmpDir,
                        splitter.unprocessedSheetImgs[idx],
                            self.crossedOutCopy(splitter.unprocessedSheetImgs[idx]),
                        True))
                else:
                    self.sheets.append(SplitSheet(
                        self.scanDir + scanFile,
                        sheetName,
                        sheetTmpDir,
                        splitter.unprocessedSheetImgs[idx],
                        self.fitSplitSheet(splitImg, sheetTmpDir),
                        False))
                self.resetPreviewCanvas(scrollToBottom=True)

        if self.__progressWindow:
            self.__progressWindow.destroy()
            self.__progressWindow = None

        if not self.previewImages:
            messagebox.showwarning('Nothing to preview',
                f'All split sheets were found empty - probably sheet transformation settings are bad')
            return

        if not self.abortProcess:
            self.buttons['recognizeTags'].config(state='normal')

    def resetPreviewCanvas(self, scrollToBottom=False):
        self.previewCanvas.delete('all')
        self.previewImages = []

        for sheet in self.sheets:
            height, width, _ = sheet.outputImg.shape
            resizeRatio = self.previewCanvas.winfo_width() / (self.previewColumnCount * width)
            resizedWidth, resizedHeight = int(width * resizeRatio), int(height * resizeRatio)
            resizedImg = cv.resize(sheet.outputImg, (resizedWidth, resizedHeight), Image.BILINEAR)
            resizedImg = ImageTk.PhotoImage(Image.fromarray(resizedImg))
            # Note: it is necessary to store the image locally for tkinter to show it
            self.previewImages.append(resizedImg)

            row = (len(self.previewImages)-1) // self.previewColumnCount
            col = (len(self.previewImages)-1) % self.previewColumnCount
            self.previewCanvas.create_image(col*self.previewColumnWidth, row*self.previewRowHeight, anchor=tkinter.NW, image=resizedImg)
            self.previewCanvas.create_rectangle(
                    col*self.previewColumnWidth,
                    row*self.previewRowHeight,
                    (col+1)*self.previewColumnWidth,
                    (row+1)*self.previewRowHeight
                    )

        self.previewCanvas.configure(scrollregion=self.previewCanvas.bbox("all"))
        if scrollToBottom:
            self.previewCanvas.yview_moveto('1.0')
        self.root.update()

    def crossedOutCopy(self, img):
        height, width, _ = img.shape
        outputImg = np.copy(img)
        cv.line(outputImg, (0, 0), (width, height), (255,0,0), 20)
        cv.line(outputImg, (0, height), (width, 0), (255,0,0), 20)
        return outputImg

    def fitSplitSheet(self, splitImg, sheetDir):
        sheetProcessors = []
        sheetProcessors.append(RotateSheet("1_rotateSheet", self.tmpDir))
        sheetProcessors.append(FindMarginsByLines("2_findMarginsByLines", self.tmpDir))
        fitToSheetProcessor = FitToSheet("3_fitToSheet", self.tmpDir)
        sheetProcessors.append(fitToSheetProcessor)

        img = splitImg
        for p in sheetProcessors:
            p.outputDir = sheetDir
            p.process(img)
            p.writeOutput()
            img = p._outputImg

        return fitToSheetProcessor._outputImg

    def recognizeTags(self):
        self.log.info('Recognize tags:')
        if self.sheets == [] or self.abortProcess:
            messagebox.showerror('Sheets missing', 'Unable to recognize tags - input images need to be split first')
            return

        self.abortProcess = False
        self.__progressWindow = tkinter.Toplevel()
        self.__progressWindow.title('Splitting progress')
        self.__progressWindow.protocol("WM_DELETE_WINDOW", self.abortProcess)
        progressBar = tkinter.ttk.Progressbar(self.__progressWindow, length=self.progressBarLength, mode='determinate')
        progressBar.pack(pady=10, padx=20)
        abortButton = tkinter.Button(self.__progressWindow, text='Abort',
            command=self.abortProcess)
        abortButton.bind('<Return>', self.abortProcess)
        abortButton.pack(pady=10)

        helpers.recreateDir(self.outputDir)
        recognizer = RecognizeText("4_recognizeText", self.tmpDir, self.db)
        self.partiallyFilledFiles = set()
        for idx, sheet in enumerate(self.sheets):
            if self.abortProcess:
                break
            progressBar['value'] = idx / len(self.sheets) * 100
            self.__progressWindow.update()

            if sheet.isEmpty:
                self.partiallyFilledFiles.add(sheet.inputScanFilepath)
                continue

            recognizer.prepareProcessing(sheet.name)
            recognizer.process(sheet.outputImg)
            recognizer.writeOutput()
            if os.path.exists(f'{self.outputDir}{recognizer.fileName()}'):
                self.log.info(f'reset sheet to fallback, as {recognizer.fileName()} already exists')
                recognizer.resetSheetToFallback()
            recognizer.storeSheet(self.outputDir)
            cv.imwrite(f'{self.outputDir}{recognizer.fileName()}_normalized_scan.jpg',
                sheet.outputImg)

        if self.__progressWindow:
            self.__progressWindow.destroy()
            self.__progressWindow = None
        self.abortProcess = False

def main(accountingDir, tmpDir):
    outputDir = f'{accountingDir}2_taggedProductSheets/'
    helpers.recreateDir(tmpDir)
    db = Database(f'{accountingDir}0_input/')
    for (parentDir, dirNames, fileNames) in walk('{}0_input/scans/'.format(accountingDir)):
        gui = GUI(tmpDir, parentDir, outputDir, fileNames, db)
        if gui.sheets == [] or gui.abortProcess:
            break

        gui.log.info('')
        gui.log.info(f'successfully processed {len(fileNames)} files')
        gui.log.info(f'the following files generated less than {SheetSplitter.numberOfSheets} sheets')
        for f in gui.partiallyFilledFiles:
            gui.log.info(f)
        break

if __name__== "__main__":
    parser = argparse.ArgumentParser(
        description='Recognize tags on all input scans, storing them as CSV files')
    parser.add_argument('accountingDir',
            help='Top-level tagtrail directory to process, usually data/next/')
    parser.add_argument('--tmpDir', dest='tmpDir', default='data/tmp/',
            help='Directory to put temporary files in')
    args = parser.parse_args()
    main(args.accountingDir, args.tmpDir)
