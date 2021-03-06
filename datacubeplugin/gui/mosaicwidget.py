import os
import numpy as np
from qgis.core import *
from qgis.gui import QgsMessageBar
from qgis.utils import iface
from qgis.PyQt import uic
from datacubeplugin import layers
from qgiscommons2.layers import layerFromSource, WrongLayerSourceException
from qgiscommons2.files import tempFilename, tempFolderInTempFolder
from dateutil import parser
from osgeo import gdal
from osgeo.gdalconst import GA_ReadOnly
from datacubeplugin.gui.selectextentmaptool import SelectExtentMapTool
from datacubeplugin.mosaicfunctions import mosaicFunctions, NO_DATA
from datacubeplugin.utils import addLayerIntoGroup, dateFromDays, daysFromDate
from datacubeplugin.layers import getArray
from qgiscommons2.gui import execute, startProgressBar, closeProgressBar, setProgressValue
import processing
import time as timelib
import logging
import math

logger = logging.getLogger('datacube')

pluginPath = os.path.dirname(os.path.dirname(__file__))
WIDGET, BASE = uic.loadUiType(
    os.path.join(pluginPath, 'ui', 'mosaicwidget.ui'))

class MosaicWidget(BASE, WIDGET):

    def __init__(self, parent=None):
        super(MosaicWidget, self).__init__(parent)
        self.setupUi(self)
        self.buttonCreateMosaic.clicked.connect(self.createMosaic)
        self.comboCoverage.currentIndexChanged.connect(self.updateDates)
        self.comboMosaicType.addItems([f.name for f in mosaicFunctions])
        self.buttonLayerExtent.clicked.connect(self.useLayerExtent)
        self.buttonCanvasExtent.clicked.connect(self.useCanvasExtent)
        self.buttonSelectExtentOnCanvas.clicked.connect(self.selectExtentOnCanvas)
        self.mapTool = SelectExtentMapTool(iface.mapCanvas(), self)
        self.sliderStartDate.valueChanged.connect(self.startDateChanged)
        self.sliderEndDate.valueChanged.connect(self.endDateChanged)

        iface.mapCanvas().mapToolSet.connect(self.unsetTool)

    def startDateChanged(self):
        self.txtStartDate.setText(str(dateFromDays(self.sliderStartDate.value())).split(" ")[0])

    def endDateChanged(self):
        self.txtEndDate.setText(str(dateFromDays(self.sliderEndDate.value())).split(" ")[0])

    def useCanvasExtent(self):
        self.setExtent(iface.mapCanvas().extent())

    def useLayerExtent(self):
        layer = iface.activeLayer()
        if layer:
            self.setExtent(layer.extent())

    def unsetTool(self, tool):
        from datacubeplugin.gui.selectextentmaptool import SelectExtentMapTool
        if not isinstance(tool, SelectExtentMapTool):
            self.buttonSelectExtentOnCanvas.setChecked(False)

    def selectExtentOnCanvas(self):
        self.buttonSelectExtentOnCanvas.setChecked(True)
        iface.mapCanvas().setMapTool(self.mapTool)

    def setExtent(self, extent):
        self.textXMin.setText(str(extent.xMinimum()))
        self.textYMin.setText(str(extent.yMinimum()))
        self.textXMax.setText(str(extent.xMaximum()))
        self.textYMax.setText(str(extent.yMaximum()))

    def _loadedLayersForCoverage(self, name, coverageName):
        loadedLayers = []
        for layerdef in layers._layers[name][coverageName]:
            source = layerdef.source()
            try:
                layer = layerFromSource(source)
                loadedLayers.append(layerdef)
            except WrongLayerSourceException:
                pass
        loadedLayers.sort(key=lambda lay: lay.time())
        return loadedLayers

    def updateDates(self):
        txt = self.comboCoverage.currentText()
        name, coverageName = txt.split(" : ")
        layers = self._loadedLayersForCoverage(name, coverageName)
        if layers:
            dates = [parser.parse(lay.time()) for lay in layers]
            minDays = daysFromDate(min(dates))
            maxDays = daysFromDate(max(dates))
            self.sliderStartDate.setMinimum(minDays)
            self.sliderStartDate.setMaximum(maxDays)
            self.sliderStartDate.setValue(minDays)
            self.sliderEndDate.setMinimum(minDays)
            self.sliderEndDate.setMaximum(maxDays)
            self.sliderEndDate.setValue(maxDays)

    def _tileDownloaded(self, i):
        setProgressValue(i)

    def createMosaic(self):
        execute(self._createMosaic)

    def _createMosaic(self):
        mosaicFunction = mosaicFunctions[self.comboMosaicType.currentIndex()]
        def getValue(textbox, paramName):
            try:
                v = float(textbox.text())
                return v
            except:
                iface.messageBar().pushMessage("", "Wrong value for parameter %s: %s" % (paramName, textbox.text()),
                                               level=QgsMessageBar.WARNING)
                raise
        try:
            widgets = [self.textXMin, self.textXMax, self.textYMin, self.textYMax]
            names = ["X min", "X max", "Y min", "Y max"]
            xmin, xmax, ymin, ymax = [getValue(w, n) for w, n in zip(widgets, names)]
        except:
            return
        extent = QgsRectangle(QgsPoint(xmin, ymin), QgsPoint(xmax, ymax))
        txt = self.comboCoverage.currentText()
        if not txt:
            iface.messageBar().pushMessage("", "No coverage selected",
                                               level=QgsMessageBar.WARNING)
            return
        name, coverageName = txt.split(" : ")
        loadedLayers = self._loadedLayersForCoverage(name, coverageName)
        minDays = self.sliderStartDate.value()
        maxDays = self.sliderEndDate.value()
        validLayers = []
        for layer in loadedLayers:
            time = daysFromDate(parser.parse(layer.time()))
            if (time >= minDays and time <= maxDays):
                validLayers.append(layer)

        bandNames = layers._coverages[name][coverageName].bands
        if validLayers:
            newBands = []
            tilesFolders = []
            dstFolder = tempFolderInTempFolder()

            '''We download the layers so we can access them locally'''
            lay = validLayers[0]
            xSize = extent.width() / lay.layer().rasterUnitsPerPixelX()
            ySize = extent.height() / lay.layer().rasterUnitsPerPixelY()
            xTiles = math.ceil(xSize / lay.TILESIZE)
            yTiles = math.ceil(ySize / lay.TILESIZE)
            logger.info("Downloading datacube layers to local files. Extent:%sx%s. Tiles count: %sx%s" %
                         (extent.width(), extent.height(),xTiles, yTiles))
            for i, lay in enumerate(validLayers):
                start = timelib.time()
                tilesFolders.append(lay.saveTiles(extent))
                end = timelib.time()
                logger.info("Layer %s downloaded in %s seconds." % (str(i), str(end-start)))

            try:
                qaBand = bandNames.index("pixel_qa")
            except:
                qaBand = None
            tileFiles = os.listdir(tilesFolders[0])
            if not tileFiles:
                iface.messageBar().pushMessage("", "No available data within the selected extent.",
                                               level=QgsMessageBar.WARNING)
                return
            startProgressBar("Processing mosaic data", len(tileFiles))
            '''Now we process all tiles separately'''
            for i, filename in enumerate(tileFiles):
                tilestart = timelib.time()
                setProgressValue(i)

                start = timelib.time()
                newBands = {}
                files = [os.path.join(folder, filename) for folder in tilesFolders]
                if qaBand is not None:
                    qaData = [getArray(f, qaBand + 1) for f in files]
                else:
                    qaData = None

                end = timelib.time()
                logger.info("QA band prepared in %s seconds" % (str(end-start)))


                if mosaicFunction.bandByBand:
                    '''
                    We operate band by band, since a given band in the the final result
                    layer depends only on the values of that band in the input layers,
                    not the value of other bands'''
                    start = timelib.time()
                    for band, bandName in enumerate(bandNames):
                        if band == qaBand:
                            newBands[bandName] = mosaicFunction.computeQAMask(qaData)
                        else:
                            bandData = [getArray(f, band + 1) for f in files]
                            newBands[bandName] = mosaicFunction.compute(bandData, qaData)
                            bandData = None
                    end = timelib.time()
                    logger.info("Tile %s read and processed in %s seconds." % (str(i), str(end-start)))
                else:
                    '''
                    We operate with all bands at once, and the output layer will
                    have only each band computed from the set of them in the input
                    layers'''
                    bandData = []
                    bandNamesArray = []
                    start = timelib.time()
                    for i, band in enumerate(bandNames):
                        if i != qaBand:
                            bandData.append([getArray(f, i + 1) for f in files])
                            bandNamesArray.append(band)
                    end = timelib.time()
                    logger.info("Tile %s data read and prepared in %s seconds." % (str(i), str(end-start)))
                    start = timelib.time()
                    newBandsArray = mosaicFunction.compute(bandData, qaData)
                    end = timelib.time()
                    logger.info("Tile %s data processed in %s seconds." % (str(i), str(end-start)))
                    newBands = {k: v for k, v in zip(bandNamesArray, newBandsArray)}
                    if qaBand is not None:
                        start = timelib.time()
                        newBands[bandNames[qaBand]] = mosaicFunction.computeQAMask(qaData)
                        end = timelib.time()
                        logger.info("QA band prepared in %s seconds" % (str(end-start)))
                    bandData = None

                start = timelib.time()
                '''We write the set of bands as a new layer. That will be an output tile'''
                templateFilename = os.path.join(tilesFolders[0], filename)
                ds = gdal.Open(templateFilename, GA_ReadOnly)
                bandCount = ds.RasterCount
                datatype = ds.GetRasterBand(1).DataType
                width = ds.RasterXSize
                height = ds.RasterYSize
                geotransform = ds.GetGeoTransform()
                projection = ds.GetProjection()
                del ds
                driver = gdal.GetDriverByName("GTiff")
                dstFilename = os.path.join(dstFolder, filename)
                dstDs= driver.Create(dstFilename, width, height, bandCount, datatype)

                for b, band in enumerate(bandNames):
                    gdalBand = dstDs.GetRasterBand(b+1)
                    gdalBand.SetNoDataValue(NO_DATA)
                    gdalBand.WriteArray(newBands[band])
                    gdalBand.FlushCache()
                del newBands

                dstDs.SetGeoTransform(geotransform)
                dstDs.SetProjection(projection)

                del dstDs

                end = timelib.time()
                logger.info("Tile %s written to local file in %s seconds." % (str(i), str(end-start)))

                tileend = timelib.time()
                logger.info("Total time to process tile: %s seconds." % (str(tileend-tilestart)))


            '''With all the tiles, we create a virtual raster'''
            toMerge = ";".join([os.path.join(dstFolder, f) for f in tileFiles])
            outputFile = os.path.join(dstFolder, "mosaic.vrt")
            processing.runalg("gdalogr:buildvirtualraster", {"INPUT":toMerge, "SEPARATE":False, "OUTPUT":outputFile})

            layer = QgsRasterLayer(outputFile, "Mosaic [%s]" % mosaicFunction.name, "gdal")

            layers._mosaicLayers[validLayers[0].datasetName()][validLayers[0].coverageName()].append(outputFile)

            addLayerIntoGroup(layer, validLayers[0].datasetName(), validLayers[0].coverageName(), bandNames)

            closeProgressBar()

            iface.messageBar().pushMessage("", "Mosaic has been correctly created and added to project.",
                                               level=QgsMessageBar.INFO)
        else:
            iface.messageBar().pushMessage("", "No layers available from the selected coverage.",
                                               level=QgsMessageBar.WARNING)

mosaicWidget = MosaicWidget(iface.mainWindow())

