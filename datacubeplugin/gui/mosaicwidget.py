import os
from qgis.core import *
from qgis.utils import iface
from qgis.PyQt import uic
from datacubeplugin import layers
from qgiscommons2.layers import layerFromSource, WrongLayerSourceException
from qgiscommons2.files import tempFilename
from dateutil import parser
import numpy as np
from osgeo import gdal
from osgeo.gdalconst import GA_ReadOnly
from datacubeplugin.gui.selectextentmaptool import SelectExtentMapTool
from datacubeplugin.mosaicfunctions import mosaicFunctions

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

        iface.mapCanvas().mapToolSet.connect(self.unsetTool)

    def useCanvasExtent(self):
        self.setExtent(iface.mapCanvas().extent())

    def useLayerExtent(self):
        layer = iface.activeLayer()
        if layer:
            self.setExtent(layer.extent())

    def unsetTool(self, tool):
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
            years = [parser.parse(lay.time()).year for lay in layers]
            minYear = min(years)
            maxYear = max(years)
            self.sliderStartDate.setMinimum(minYear)
            self.sliderStartDate.setMaximum(maxYear)
            self.sliderStartDate.setValue(minYear)
            self.sliderEndDate.setMinimum(minYear)
            self.sliderEndDate.setMaximum(maxYear)
            self.sliderEndDate.setValue(maxYear)

    def createMosaic(self):
        mosaicFunction = mosaicFunctions[self.comboMosaicType.currentIndex()]
        xmin = float(self.textXMin.text())
        xmax = float(self.textXMax.text())
        ymin = float(self.textYMin.text())
        ymax = float(self.textYMax.text())
        extent = QgsRectangle(QgsPoint(xmin, ymin), QgsPoint(xmax, ymax))
        txt = self.comboCoverage.currentText()
        name, coverageName = txt.split(" : ")
        layers = self._loadedLayersForCoverage(name, coverageName)
        minYear = self.sliderStartDate.value()
        maxYear = self.sliderEndDate.value()
        validLayers = []
        for layer in layers:
            time = parser.parse(layer.time())
            if (time.year >= minYear and time.year <= maxYear):
                validLayers.append(layer)

        if validLayers:
            ds = gdal.Open(validLayers[0].layerFile(extent), GA_ReadOnly)
            datatype = ds.GetRasterBand(1).DataType
            bandCount = ds.RasterCount
            width = ds.RasterXSize
            height = ds.RasterYSize
            geotransform = ds.GetGeoTransform()
            projection = ds.GetProjection()
            newBands = []
            times = [lay.time() for lay in validLayers]
            for band in range(bandCount):
                bandData = [self.getArray(lay.layerFile(extent), band + 1) for lay in validLayers]
                newBands.append(mosaicFunction.compute(bandData, times))
                bandData = None
            driver = gdal.GetDriverByName("GTiff")
            dstFilename = tempFilename("tif")
            print dstFilename
            dstDs= driver.Create(dstFilename, width, height, bandCount, datatype)

            for i, band in enumerate(newBands):
                print band
                gdalBand = dstDs.GetRasterBand(i+1)
                gdalBand.WriteArray(band)
                gdalBand.FlushCache()
                del band

            dstDs.SetGeoTransform(geotransform)
            dstDs.SetProjection(projection)
            del dstDs

            layer = QgsRasterLayer(dstFilename, "Mosaic [%s]" % mosaicFunction.name, "gdal")
            addLayerIntoGroup(layer, validLayers[0].coverageName())

    def getArray(self, filename, bandidx):
        ds = gdal.Open(filename, GA_ReadOnly)
        band = ds.GetRasterBand(bandidx)
        array = band.ReadAsArray()
        nodata = band.GetNoDataValue()
        return (array, nodata)


mosaicWidget = MosaicWidget(iface.mainWindow())
