# -*- coding: utf-8 -*-
"""
Google Earth Engine Extractor by Jambar Lab -- Plugin lifecycle.
"""

import os

from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon
from qgis.core import Qgis, QgsMessageLog

PLUGIN_NAME = "GEE Extractor"


def _check_earthengine_api():
    """
    Verify that earthengine-api is importable.
    Returns (available: bool, version: str | None).
    Pattern inspired by SemiAutomaticClassificationPlugin dependency check.
    """
    try:
        import ee
        version = getattr(ee, "__version__", "unknown")
        return True, version
    except ImportError:
        return False, None


class GoogleEarthEngineExtractor:

    MENU = "Google Earth Engine Extractor"

    def __init__(self, iface):
        self.iface      = iface
        self.plugin_dir = os.path.dirname(__file__)
        self._action    = None
        self._dialog    = None

        # Dependency check at construction time — mirrors SCP pattern
        self._ee_available, self._ee_version = _check_earthengine_api()
        if not self._ee_available:
            QgsMessageLog.logMessage(
                "earthengine-api not found. Install it with: "
                "pip install earthengine-api\n"
                "The plugin will load but extractions will fail until "
                "the dependency is available.",
                PLUGIN_NAME,
                Qgis.Warning,
            )

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        self._action = QAction(icon, self.MENU, self.iface.mainWindow())
        self._action.setToolTip(
            "Extract Google Earth Engine data by layer extent"
        )
        self._action.triggered.connect(self._open)

        self.iface.addPluginToMenu(self.MENU, self._action)
        self.iface.addToolBarIcon(self._action)

        # Surface dependency warning in the message bar (non-blocking)
        if not self._ee_available:
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME,
                "earthengine-api is not installed. "
                "Run: pip install earthengine-api",
                level=Qgis.Warning,
                duration=10,
            )

    def unload(self):
        if self._action:
            self.iface.removePluginMenu(self.MENU, self._action)
            self.iface.removeToolBarIcon(self._action)
            self._action = None
        if self._dialog:
            self._dialog.close()
            self._dialog = None

    def _open(self):
        from .dialog import GoogleEarthEngineExtractorDialog
        if self._dialog is None:
            self._dialog = GoogleEarthEngineExtractorDialog(self.iface)
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()
