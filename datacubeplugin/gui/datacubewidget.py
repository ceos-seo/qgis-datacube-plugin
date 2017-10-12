import os

from qgis.core import *
from qgis.gui import QgsMessageBar

from qgis.utils import iface
from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QTreeWidgetItem, QLabel, QHBoxLayout, QWidget
from qgis.PyQt.QtGui import QSizePolicy, QPixmap, QImage, QPainter, QIcon
from qgis.PyQt.QtCore import Qt, QSize
from qgis.PyQt.QtSvg import QSvgRenderer
from qgiscommons2.layers import layerFromSource, WrongLayerSourceException
from qgiscommons2.gui import execute, askForFolder

from endpointselectiondialog import EndpointSelectionDialog

from datacubeplugin.selectionmaptools import PointSelectionMapTool, RegionSelectionMapTool
from datacubeplugin import layers
from datacubeplugin.connectors import connectors
from datacubeplugin.gui.plotwidget import plotWidget
from datacubeplugin.gui.mosaicwidget import mosaicWidget
from datacubeplugin import plotparams
from datacubeplugin.utils import addLayerIntoGroup, dateFromDays, daysFromDate, setLayerRGB

pluginPath = os.path.dirname(os.path.dirname(__file__))
WIDGET, BASE = uic.loadUiType(
    os.path.join(pluginPath, 'ui', 'datacubewidget.ui'))


class DataCubeWidget(BASE, WIDGET):

    def __init__(self, parent=None):
        super(DataCubeWidget, self).__init__(parent)
        self.setupUi(self)

        logoPath = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icons", "datacube.png")
        self.labelLogo.setText('<img src="%s" width="150">' % logoPath)

        self.plotParameters = []

        self.yAbsoluteMin = 0
        self.yAbsoluteMax = 1

        AddEndpointTreeItem(self.treeWidget.invisibleRootItem(),
                                self.treeWidget, self)

        self.treeWidget.itemClicked.connect(self.treeItemClicked)

        self.comboCoverageForRGB.currentIndexChanged.connect(self.coverageForRGBHasChanged)

        self.applyButton.clicked.connect(self.updateRGB)
        self.selectPointButton.clicked.connect(self.togglePointMapTool)
        self.selectRegionButton.clicked.connect(self.toggleRegionMapTool)

        self.comboCoverageToPlot.currentIndexChanged.connect(self.coverageToPlotHasChanged)
        self.comboParameterToPlot.currentIndexChanged.connect(self.parameterToPlotHasChanged)

        iface.mapCanvas().mapToolSet.connect(self.unsetTool)

        self.pointSelectionTool = PointSelectionMapTool(iface.mapCanvas())
        self.regionSelectionTool = RegionSelectionMapTool(iface.mapCanvas())
        self.sliderStartDate.valueChanged.connect(self.plotDataFilterChanged)
        self.sliderEndDate.valueChanged.connect(self.plotDataFilterChanged)
        self.sliderMinY.valueChanged.connect(self.plotDataFilterChanged)
        self.sliderMaxY.valueChanged.connect(self.plotDataFilterChanged)

        plotWidget.plotDataChanged.connect(self.plotDataChanged)

    def fromSliderValue(self, v):
        return v * (self.yAbsoluteMax - self.yAbsoluteMin) / self.SLIDER_MAX +  self.yAbsoluteMin

    def plotDataFilterChanged(self):
        xmin = dateFromDays(self.sliderStartDate.value())
        xmax = dateFromDays(self.sliderEndDate.value())
        ymin = self.fromSliderValue(self.sliderMinY.value())
        ymax = self.fromSliderValue(self.sliderMaxY.value())
        self.txtStartDate.setText(str(xmin).split(" ")[0])
        self.txtEndDate.setText(str(xmax).split(" ")[0])
        self.txtMinY.setText(str(ymin))
        self.txtMaxY.setText(str(ymax))
        _filter = [xmin, xmax, ymin, ymax]
        plotWidget.plot(_filter)

    SLIDER_MAX = 1000
    def plotDataChanged(self, xmin, xmax, ymin, ymax):
        widgets = [self.sliderMinY, self.sliderMaxY, self.sliderStartDate, self.sliderEndDate]

        for w in widgets:
            w.blockSignals(True)

        self.yAbsoluteMin = ymin
        self.yAbsoluteMax = ymax
        self.sliderMinY.setMinimum(0)
        self.sliderMaxY.setMinimum(0)
        self.sliderMinY.setMaximum(self.SLIDER_MAX)
        self.sliderMaxY.setMaximum(self.SLIDER_MAX)
        self.sliderMaxY.setValue(self.SLIDER_MAX)
        self.sliderMinY.setValue(0)

        self.sliderStartDate.setMinimum(daysFromDate(xmin) - 1)
        self.sliderEndDate.setMinimum(daysFromDate(xmin) - 1)
        self.sliderStartDate.setMaximum(daysFromDate(xmax) + 1)
        self.sliderEndDate.setMaximum(daysFromDate(xmax) + 1)
        self.sliderStartDate.setValue(daysFromDate(xmin) - 1)
        self.sliderEndDate.setValue(daysFromDate(xmax) + 1)

        self.txtStartDate.setText(str(xmin).split(" ")[0])
        self.txtEndDate.setText(str(xmax).split(" ")[0])
        self.txtMinY.setText(str(ymin))
        self.txtMaxY.setText(str(ymax))

        for w in widgets:
            w.blockSignals(False)

    def treeItemClicked(self, item, col):
        if isinstance(item, LayerTreeItem):
            item.addOrRemoveLayer()

    def unsetTool(self, tool):
        if not isinstance(tool, PointSelectionMapTool):
            self.selectPointButton.setChecked(False)
        if not isinstance(tool, RegionSelectionMapTool):
            self.selectRegionButton.setChecked(False)

    def togglePointMapTool(self):
        self.selectPointButton.setChecked(True)
        iface.mapCanvas().setMapTool(self.pointSelectionTool)

    def toggleRegionMapTool(self):
        self.selectRegionButton.setChecked(True)
        iface.mapCanvas().setMapTool(self.regionSelectionTool)

    def updateRGB(self):
        name, coverageName = self.comboCoverageForRGB.currentText().split(" : ")
        r = self.comboR.currentIndex()
        g = self.comboG.currentIndex()
        b = self.comboB.currentIndex()
        layers._rendering[name][coverageName] = (r, g, b)
        for layer in layers._layers[name][coverageName]:
            source = layer.source()
            try:
                layer = layerFromSource(source)
                setLayerRGB(layer, r, g, b)
            except WrongLayerSourceException:
                pass

    def coverageForRGBHasChanged(self):
        self.updateRGBFields()

    def updateRGBFields(self, nameToUpdate = None, coverageNameToUpdate = None):
        name, coverageName = self.comboCoverageForRGB.currentText().split(" : ")
        if nameToUpdate is not None and (name != nameToUpdate or coverageName != coverageNameToUpdate):
            return

        bands = layers._layers[name][coverageName][0].bands()
        try:
            r, g, b = layers._rendering[name][coverageName]
        except KeyError:
            if len(bands) > 2:
                try:
                    r = bands.index("red")
                    g = bands.index("green")
                    b = bands.index("blue")
                except ValueError:
                    r, g, b = 0, 1, 2
            else:
                r = g = b = 0

        self.comboR.clear()
        self.comboR.addItems(bands)
        self.comboR.setCurrentIndex(r)
        self.comboG.clear()
        self.comboG.addItems(bands)
        self.comboG.setCurrentIndex(g)
        self.comboB.clear()
        self.comboB.addItems(bands)
        self.comboB.setCurrentIndex(b)

    def parameterToPlotHasChanged(self):
        param = self.plotParameters[self.comboParameterToPlot.currentIndex()]
        plotWidget.plot(parameter=param)

    def coverageToPlotHasChanged(self):
        txt = self.comboCoverageToPlot.currentText()
        name, coverageName = txt.split(" : ")
        bands = layers._coverages[name][coverageName].bands
        self.plotParameters = plotparams.getParameters(bands)
        self.comboParameterToPlot.blockSignals(True)
        self.comboParameterToPlot.clear()
        self.comboParameterToPlot.addItems([str(p) for p in self.plotParameters])
        self.comboParameterToPlot.blockSignals(False)
        plotWidget.plot(dataset=name, coverage=coverageName, parameter=self.plotParameters[0])


class TreeItemWithLink(QTreeWidgetItem):

    def __init__(self, parent, tree, text, linkText, linkColor="blue", icon=None):
        QTreeWidgetItem.__init__(self, parent)
        self.parent = parent
        self.tree = tree
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel()
        if os.path.exists(icon):
            svg_renderer = QSvgRenderer(icon)
            image = QImage(32, 32, QImage.Format_ARGB32)
            # Set the ARGB to 0 to prevent rendering artifacts
            image.fill(0x00000000)
            svg_renderer.render(QPainter(image))
            pixmap = QPixmap.fromImage(image)
            icon = QIcon(pixmap)
            self.setIcon(0, icon)
            self.setSizeHint(0, QSize(32, 32))
        self.label.setText(text)
        layout.addWidget(self.label)
        if linkText:
            self.linkLabel = QLabel()
            self.linkLabel.setText("<a href='#' style='color: %s;'> %s</a>" % (linkColor, linkText))
            self.linkLabel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            layout.addWidget(self.linkLabel)
            self.linkLabel.linkActivated.connect(self.linkClicked)
        w = QWidget()
        w.setLayout(layout)
        self.tree.setItemWidget(self, 0, w)

class AddEndpointTreeItem(TreeItemWithLink):

    def __init__(self, parent, tree, widget):
        iconPath = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icons", "plus.svg")
        TreeItemWithLink.__init__(self, parent, tree, "", "Add new data source", "DodgerBlue", iconPath)
        self.widget = widget

    def linkClicked(self):
        from endpointselectiondialog import EndpointSelectionDialog
        dialog = EndpointSelectionDialog()
        dialog.exec_()
        if dialog.url is not None:
            self.addEndpoint(dialog.url)

    def addEndpoint(self, endpoint):
        execute(lambda: self._addEndpoint(endpoint))

    def _addEndpoint(self, endpoint):
        iface.mainWindow().statusBar().showMessage("Retrieving coverages info from endpoint...")
        connector = None
        for c in connectors:
            if c.isCompatible(endpoint):
                connector = c(endpoint)
                break
        if connector is None:
            iface.messageBar().pushMessage("", "Could not add coverages from the provided endpoint.",
                                               level=QgsMessageBar.WARNING)
            iface.mainWindow().statusBar().showMessage("")
            return
        layers._layers[connector.name()] = {}
        layers._coverages[connector.name()] = {}
        coverages = connector.coverages()
        if coverages:
            endpointItem = QTreeWidgetItem()
            endpointItem.setText(0, connector.name())
            self.tree.addTopLevelItem(endpointItem)
        for coverageName in coverages:
            #item.setText(0, coverageName)
            #endpointItem.addChild(item)
            coverage = connector.coverage(coverageName)
            item = CoverageItem(endpointItem, self.tree, coverage)
            timepositions = coverage.timePositions()
            timeLayers = []
            for time in timepositions:
                layer = coverage.layerForTimePosition(time)
                timeLayers.append(layer)
                subitem = LayerTreeItem(layer, self.widget)
                item.addChild(subitem)
            layers._layers[connector.name()][coverageName] = timeLayers
            layers._coverages[connector.name()][coverageName] = coverage
            self.widget.comboCoverageToPlot.addItem(connector.name() + " : " + coverageName)
            self.widget.comboCoverageForRGB.addItem(connector.name() + " : " + coverageName)
            mosaicWidget.comboCoverage.addItem(connector.name() + " : " + coverageName)
        iface.mainWindow().statusBar().showMessage("")

class CoverageItem(TreeItemWithLink):

    def __init__(self, parent, tree, coverage):
        TreeItemWithLink.__init__(self, parent, tree, coverage.name(), "Download")
        self.coverage = coverage

    def linkClicked(self):
        folder = askForFolder(self.tree, "Folder for local storage")
        if folder:
            timepositions = self.coverage.timePositions()
            for time in timepositions:
                layer = self.coverage.layerForTimePosition(time)
                layer.saveTo(folder)

class LayerTreeItem(QTreeWidgetItem):

    def __init__(self, layer, widget):
        QTreeWidgetItem.__init__(self)
        self.layer = layer
        self.widget = widget
        self.setCheckState(0, Qt.Unchecked);
        self.setText(0, layer.time())

    def addOrRemoveLayer(self):
        source = self.layer.source()
        if self.checkState(0) == Qt.Checked:
            try:
                layer = layerFromSource(source)
            except WrongLayerSourceException:
                layer = execute(self.layer.layer)
                if layer.isValid():
                    coverageName = self.layer.coverageName()
                    name = self.layer.datasetName()
                    addLayerIntoGroup(layer, name, coverageName, self.layer.bands())
                    mosaicWidget.updateDates()
                else:
                    iface.mainWindow().statusBar().showMessage("Invalid layer")
        else:
            try:
                layer = layerFromSource(source)
                QgsMapLayerRegistry.instance().removeMapLayers([layer.id()])
            except WrongLayerSourceException:
                pass


