# -*- coding: utf-8 -*-
"""
Google Earth Engine Extractor by Jambar Lab -- QGIS entry point.

Extract Google Earth Engine datasets (Sentinel-2, Landsat, SRTM, MODIS NDVI,
Global Forest Change) directly into QGIS by vector layer extent.

Copyright (C) 2026 by LOMPO Ibrahim (Jambar Lab)
Email: ibrahimlompo33@gmail.com

This file is part of Google Earth Engine Extractor.
Google Earth Engine Extractor is free software: you can redistribute it
and/or modify it under the terms of the GNU General Public License as
published by the Free Software Foundation, either version 2 of the License,
or (at your option) any later version.
"""


def name():
    return 'Google Earth Engine Extractor'


def description():
    return (
        'Extract Google Earth Engine datasets (Sentinel-2, Landsat, SRTM, '
        'MODIS NDVI, Global Forest Change) directly into QGIS by vector '
        'layer extent.'
    )


def version():
    return 'Version 1.0.1'


def icon():
    return 'icon.png'


def qgisMinimumVersion():
    return '3.16'


def author():
    return 'LOMPO Ibrahim'


def email():
    return 'ibrahimlompo33@gmail.com'


def category():
    return 'Raster'


def homepage():
    return 'https://github.com/ibrahimlompo33-coder/google-earth-engine-extrator'


def tracker():
    return 'https://github.com/ibrahimlompo33-coder/google-earth-engine-extrator/issues'


def repository():
    return 'https://github.com/ibrahimlompo33-coder/google-earth-engine-extrator'


def classFactory(iface):
    from .main import GoogleEarthEngineExtractor
    return GoogleEarthEngineExtractor(iface)
