# -*- coding: utf-8 -*-
"""
Google Earth Engine Extractor by Jambar Lab -- Earth Engine business logic.
"""

import json
import logging
import os
import zipfile

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsMessageLog,
    QgsProject,
    QgsRasterLayer,
)

_pylog   = logging.getLogger("GoogleEarthEngineExtractor")
_PLUGIN  = "GEE Extractor"


def _qlog(msg, level=Qgis.Info):
    QgsMessageLog.logMessage(str(msg), _PLUGIN, level=level)
    if level == Qgis.Critical:
        _pylog.error(msg)
    elif level == Qgis.Warning:
        _pylog.warning(msg)
    else:
        _pylog.info(msg)


# Keep backward-compat alias used throughout the module
log = type("_Log", (), {
    "info":    staticmethod(lambda m, *a: _qlog(m % a if a else m, Qgis.Info)),
    "warning": staticmethod(lambda m, *a: _qlog(m % a if a else m, Qgis.Warning)),
    "error":   staticmethod(lambda m, *a: _qlog(m % a if a else m, Qgis.Critical)),
})()

# ── Composite methods ─────────────────────────────────────────────────────────

COMPOSITE_METHODS = {
    "median": "Median (recommended, robust to outliers)",
    "mean":   "Mean (faster, sensitive to clouds)",
    "mosaic": "Mosaic (most recent pixel on top)",
    "min":    "Min (useful for water/shadow indices)",
    "max":    "Max (greenest pixel NDVI)",
}

# ── Dataset catalogue ─────────────────────────────────────────────────────────

COLLECTIONS = {
    "sentinel2_sr": {
        "label":            "Sentinel-2 SR Harmonized (10 m)",
        "id":               "COPERNICUS/S2_SR_HARMONIZED",
        "type":             "image_collection",
        "default_bands":    ["B4", "B3", "B2"],
        "band_info": {
            "B2":  "Blue (490 nm)",
            "B3":  "Green (560 nm)",
            "B4":  "Red (665 nm)",
            "B5":  "Red Edge 1 (705 nm)",
            "B6":  "Red Edge 2 (740 nm)",
            "B7":  "Red Edge 3 (783 nm)",
            "B8":  "NIR (842 nm)",
            "B8A": "Red Edge 4 (865 nm)",
            "B11": "SWIR 1 (1610 nm)",
            "B12": "SWIR 2 (2190 nm)",
        },
        "scale":            10,
        "cloud_prop":       "CLOUDY_PIXEL_PERCENTAGE",
        "cloud_mask_fn":    "_mask_s2",
        "needs_dates":      True,
        "has_cloud_filter": True,
        "sr_scale_factor":  0.0001,
        "sr_offset":        0.0,
        "clamp_reflectance": True,
        "layer_name_prefix": "S2_SR",
    },
    "landsat8_sr": {
        "label":            "Landsat 8 SR Collection 2 (30 m)",
        "id":               "LANDSAT/LC08/C02/T1_L2",
        "type":             "image_collection",
        "default_bands":    ["SR_B4", "SR_B3", "SR_B2"],
        "band_info": {
            "SR_B1": "Coastal/Aerosol (443 nm)",
            "SR_B2": "Blue (482 nm)",
            "SR_B3": "Green (562 nm)",
            "SR_B4": "Red (655 nm)",
            "SR_B5": "NIR (865 nm)",
            "SR_B6": "SWIR 1 (1609 nm)",
            "SR_B7": "SWIR 2 (2201 nm)",
        },
        "scale":            30,
        "cloud_prop":       "CLOUD_COVER",
        "cloud_mask_fn":    "_mask_landsat",
        "needs_dates":      True,
        "has_cloud_filter": True,
        # Landsat C2 L2: rho = DN * 0.0000275 - 0.2
        "sr_scale_factor":  0.0000275,
        "sr_offset":        -0.2,
        "clamp_reflectance": True,
        "layer_name_prefix": "L8_SR",
    },
    "landsat9_sr": {
        "label":            "Landsat 9 SR Collection 2 (30 m)",
        "id":               "LANDSAT/LC09/C02/T1_L2",
        "type":             "image_collection",
        "default_bands":    ["SR_B4", "SR_B3", "SR_B2"],
        "band_info": {
            "SR_B1": "Coastal/Aerosol (443 nm)",
            "SR_B2": "Blue (482 nm)",
            "SR_B3": "Green (562 nm)",
            "SR_B4": "Red (654 nm)",
            "SR_B5": "NIR (865 nm)",
            "SR_B6": "SWIR 1 (1609 nm)",
            "SR_B7": "SWIR 2 (2201 nm)",
        },
        "scale":            30,
        "cloud_prop":       "CLOUD_COVER",
        "cloud_mask_fn":    "_mask_landsat",
        "needs_dates":      True,
        "has_cloud_filter": True,
        "sr_scale_factor":  0.0000275,
        "sr_offset":        -0.2,
        "clamp_reflectance": True,
        "layer_name_prefix": "L9_SR",
    },
    "srtm": {
        "label":            "SRTM DEM (30 m)",
        "id":               "USGS/SRTMGL1_003",
        "type":             "image",
        "default_bands":    ["elevation"],
        "band_info": {
            "elevation": "Elevation (metres above WGS84 ellipsoid)",
        },
        "scale":            30,
        "cloud_prop":       None,
        "cloud_mask_fn":    None,
        "needs_dates":      False,
        "has_cloud_filter": False,
        "sr_scale_factor":  None,
        "sr_offset":        0.0,
        "clamp_reflectance": False,
        "layer_name_prefix": "SRTM",
    },
    "modis_ndvi": {
        "label":            "MODIS MOD13Q1 NDVI 16-day (250 m)",
        "id":               "MODIS/061/MOD13Q1",
        "type":             "image_collection",
        "default_bands":    ["NDVI"],
        "band_info": {
            "NDVI": "NDVI (x0.0001, range -0.2 to 1.0)",
            "EVI":  "EVI  (x0.0001, range -0.2 to 1.0)",
        },
        "scale":            250,
        "cloud_prop":       None,
        "cloud_mask_fn":    None,
        "needs_dates":      True,
        "has_cloud_filter": False,
        "sr_scale_factor":  0.0001,
        "sr_offset":        0.0,
        "clamp_reflectance": False,
        "layer_name_prefix": "MODIS_NDVI",
    },
    "gfc": {
        "label":            "Global Forest Change 2023 (30 m)",
        "id":               "UMD/hansen/global_forest_change_2023_v1_11",
        "type":             "image",
        "default_bands":    ["treecover2000"],
        "band_info": {
            "treecover2000": "Tree canopy cover 2000 (%)",
            "loss":          "Forest loss 2000-2023 (binary)",
            "gain":          "Forest gain 2000-2012 (binary)",
            "lossyear":      "Year of loss (1-23, 0=none)",
        },
        "scale":            30,
        "cloud_prop":       None,
        "cloud_mask_fn":    None,
        "needs_dates":      False,
        "has_cloud_filter": False,
        "sr_scale_factor":  None,
        "sr_offset":        0.0,
        "clamp_reflectance": False,
        "layer_name_prefix": "GFC",
    },

    # ── Spectral indices (virtual datasets computed on EE) ────────────────────
    # type="index" : no EE asset id — computed from a source collection
    # source_key   : COLLECTIONS key whose ImageCollection is used
    # index_fn     : name of the _idx_* function to apply after compositing

    "s2_ndvi": {
        "label":            "NDVI — Sentinel-2 SR (10 m)",
        "type":             "index",
        "source_key":       "sentinel2_sr",
        "index_fn":         "_idx_ndvi_s2",
        "default_bands":    ["NDVI"],
        "band_info": {
            "NDVI": "NDVI = (NIR-Red)/(NIR+Red)  [B8-B4]/[B8+B4], range -1..1",
        },
        "scale":            10,
        "needs_dates":      True,
        "has_cloud_filter": True,
        "layer_name_prefix": "NDVI_S2",
    },
    "s2_ndwi": {
        "label":            "NDWI — Sentinel-2 SR (10 m)",
        "type":             "index",
        "source_key":       "sentinel2_sr",
        "index_fn":         "_idx_ndwi_s2",
        "default_bands":    ["NDWI"],
        "band_info": {
            "NDWI": "NDWI = (Green-NIR)/(Green+NIR)  [B3-B8]/[B3+B8], range -1..1",
        },
        "scale":            10,
        "needs_dates":      True,
        "has_cloud_filter": True,
        "layer_name_prefix": "NDWI_S2",
    },
    "s2_nbr": {
        "label":            "NBR — Sentinel-2 SR (20 m)",
        "type":             "index",
        "source_key":       "sentinel2_sr",
        "index_fn":         "_idx_nbr_s2",
        "default_bands":    ["NBR"],
        "band_info": {
            "NBR": "NBR = (NIR-SWIR2)/(NIR+SWIR2)  [B8A-B12]/[B8A+B12], range -1..1",
        },
        "scale":            20,
        "needs_dates":      True,
        "has_cloud_filter": True,
        "layer_name_prefix": "NBR_S2",
    },
    "s2_ndbi": {
        "label":            "NDBI — Sentinel-2 SR (20 m)",
        "type":             "index",
        "source_key":       "sentinel2_sr",
        "index_fn":         "_idx_ndbi_s2",
        "default_bands":    ["NDBI"],
        "band_info": {
            "NDBI": "NDBI = (SWIR1-NIR)/(SWIR1+NIR)  [B11-B8]/[B11+B8], range -1..1",
        },
        "scale":            20,
        "needs_dates":      True,
        "has_cloud_filter": True,
        "layer_name_prefix": "NDBI_S2",
    },
    "s2_evi": {
        "label":            "EVI — Sentinel-2 SR (10 m)",
        "type":             "index",
        "source_key":       "sentinel2_sr",
        "index_fn":         "_idx_evi_s2",
        "default_bands":    ["EVI"],
        "band_info": {
            "EVI": "EVI = 2.5*(NIR-Red)/(NIR+6*Red-7.5*Blue+1)  range -1..1",
        },
        "scale":            10,
        "needs_dates":      True,
        "has_cloud_filter": True,
        "layer_name_prefix": "EVI_S2",
    },
    "l8_ndvi": {
        "label":            "NDVI — Landsat 8 SR (30 m)",
        "type":             "index",
        "source_key":       "landsat8_sr",
        "index_fn":         "_idx_ndvi_l8",
        "default_bands":    ["NDVI"],
        "band_info": {
            "NDVI": "NDVI = (NIR-Red)/(NIR+Red)  [SR_B5-SR_B4]/[SR_B5+SR_B4]",
        },
        "scale":            30,
        "needs_dates":      True,
        "has_cloud_filter": True,
        "layer_name_prefix": "NDVI_L8",
    },
    "l8_ndwi": {
        "label":            "NDWI — Landsat 8 SR (30 m)",
        "type":             "index",
        "source_key":       "landsat8_sr",
        "index_fn":         "_idx_ndwi_l8",
        "default_bands":    ["NDWI"],
        "band_info": {
            "NDWI": "NDWI = (Green-NIR)/(Green+NIR)  [SR_B3-SR_B5]/[SR_B3+SR_B5]",
        },
        "scale":            30,
        "needs_dates":      True,
        "has_cloud_filter": True,
        "layer_name_prefix": "NDWI_L8",
    },
    "l8_nbr": {
        "label":            "NBR — Landsat 8 SR (30 m)",
        "type":             "index",
        "source_key":       "landsat8_sr",
        "index_fn":         "_idx_nbr_l8",
        "default_bands":    ["NBR"],
        "band_info": {
            "NBR": "NBR = (NIR-SWIR2)/(NIR+SWIR2)  [SR_B5-SR_B7]/[SR_B5+SR_B7]",
        },
        "scale":            30,
        "needs_dates":      True,
        "has_cloud_filter": True,
        "layer_name_prefix": "NBR_L8",
    },
}

MAX_DOWNLOAD_BYTES = 48 * 1024 * 1024  # 48 MB conservative limit


# ── Authentication ────────────────────────────────────────────────────────────

def init_ee(credentials_path=None, project_id=None):
    """
    Initialize Earth Engine SDK.

    credentials_path : service account JSON key path, or None for ADC.
    project_id       : GCP project ID string (required by EE SDK >= 0.1.370
                       when using ADC; auto-read from service account JSON
                       if left empty there).

    Returns (True, None) or (False, error_str).
    """
    try:
        import ee
    except ImportError:
        return False, (
            "earthengine-api not installed. Run: pip install earthengine-api"
        )
    try:
        if credentials_path and os.path.isfile(credentials_path):
            with open(credentials_path, "r", encoding="utf-8") as fh:
                info = json.load(fh)
            sa_email = info.get("client_email", "")
            if not sa_email:
                return False, "Invalid service account JSON: missing 'client_email'."
            # Auto-detect project from JSON if not provided
            sa_project = project_id or info.get("project_id", "")
            creds      = ee.ServiceAccountCredentials(sa_email, credentials_path)
            _ee_init(ee, creds, sa_project or None)
        else:
            _ee_init(ee, None, project_id or None)
        return True, None
    except Exception as exc:
        return False, str(exc)


def _ee_init(ee, creds, project_id):
    """
    Call ee.Initialize() handling API differences across SDK versions.

    SDK < 0.1.370  : ee.Initialize(creds)
    SDK >= 0.1.370 : ee.Initialize(creds, project=project_id)
                     (project is required for ADC; optional for service accounts)
    """
    import inspect
    sig    = inspect.signature(ee.Initialize)
    params = sig.parameters

    kwargs = {}
    if creds is not None:
        kwargs["credentials"] = creds if "credentials" in params else creds
        if "credentials" not in params:
            # Very old SDK: positional only
            ee.Initialize(creds)
            return
    if "project" in params and project_id:
        kwargs["project"] = project_id

    if kwargs:
        ee.Initialize(**kwargs)
    else:
        ee.Initialize()


# ── Bounding box ──────────────────────────────────────────────────────────────

def get_bbox_wgs84(layer):
    """WGS84 bbox of a QGIS layer. Returns (S, W, N, E) or None."""
    if layer is None or not layer.isValid():
        return None
    try:
        extent  = layer.extent()
        wgs84   = QgsCoordinateReferenceSystem("EPSG:4326")
        src_crs = layer.crs()
        if src_crs.authid() != "EPSG:4326":
            tr     = QgsCoordinateTransform(src_crs, wgs84, QgsProject.instance())
            extent = tr.transformBoundingBox(extent)
        buf = 0.0001
        return (
            round(extent.yMinimum() - buf, 6),
            round(extent.xMinimum() - buf, 6),
            round(extent.yMaximum() + buf, 6),
            round(extent.xMaximum() + buf, 6),
        )
    except Exception as exc:
        log.error("Bbox computation failed: %s", exc)
        return None


def estimate_size_bytes(bbox, scale_m, n_bands):
    """Conservative float32 uncompressed size estimate."""
    s, w, n, e = bbox
    deg_to_m   = 111_320.0
    width_px   = max(1, int((e - w) * deg_to_m / scale_m))
    height_px  = max(1, int((n - s) * deg_to_m / scale_m))
    return width_px * height_px * n_bands * 4


# ── Cloud masking ─────────────────────────────────────────────────────────────

def _mask_s2(image):
    """Sentinel-2 QA60 cloud + cirrus mask (bits 10, 11)."""
    qa   = image.select("QA60")
    mask = (
        qa.bitwiseAnd(1 << 10).eq(0)
        .And(qa.bitwiseAnd(1 << 11).eq(0))
    )
    return image.updateMask(mask)


def _mask_landsat(image):
    """Landsat C2 QA_PIXEL: bit 3 = cloud, bit 4 = cloud shadow."""
    qa     = image.select("QA_PIXEL")
    cloud  = qa.bitwiseAnd(1 << 3).eq(0)
    shadow = qa.bitwiseAnd(1 << 4).eq(0)
    return image.updateMask(cloud.And(shadow))


_MASK_FNS = {
    "_mask_s2":      _mask_s2,
    "_mask_landsat": _mask_landsat,
}

_REDUCERS = {
    "median": lambda ic: ic.median(),
    "mean":   lambda ic: ic.mean(),
    "mosaic": lambda ic: ic.mosaic(),
    "min":    lambda ic: ic.min(),
    "max":    lambda ic: ic.max(),
}


# ── Image retrieval ───────────────────────────────────────────────────────────

# ── Spectral index computation functions ─────────────────────────────────────
# All functions receive an ee.Image whose SR bands are already rescaled to
# physical reflectance ([0,1] for optical). They return a single-band ee.Image
# with the index value in [-1, 1] (or EVI in similar range), renamed.

def _idx_ndvi_s2(image):
    """NDVI from Sentinel-2 SR: (B8-B4)/(B8+B4)."""
    return (
        image.normalizedDifference(["B8", "B4"])
        .rename("NDVI")
        .clamp(-1.0, 1.0)
    )


def _idx_ndwi_s2(image):
    """NDWI (Gao) from Sentinel-2 SR: (B3-B8)/(B3+B8)."""
    return (
        image.normalizedDifference(["B3", "B8"])
        .rename("NDWI")
        .clamp(-1.0, 1.0)
    )


def _idx_nbr_s2(image):
    """NBR from Sentinel-2 SR: (B8A-B12)/(B8A+B12)."""
    return (
        image.normalizedDifference(["B8A", "B12"])
        .rename("NBR")
        .clamp(-1.0, 1.0)
    )


def _idx_ndbi_s2(image):
    """NDBI from Sentinel-2 SR: (B11-B8)/(B11+B8)."""
    return (
        image.normalizedDifference(["B11", "B8"])
        .rename("NDBI")
        .clamp(-1.0, 1.0)
    )


def _idx_evi_s2(image):
    """
    EVI from Sentinel-2 SR (Huete 1994):
    EVI = 2.5 * (NIR - Red) / (NIR + 6*Red - 7.5*Blue + 1)
    Bands: NIR=B8, Red=B4, Blue=B2.
    Input reflectances already in [0,1].
    """
    nir  = image.select("B8")
    red  = image.select("B4")
    blue = image.select("B2")
    evi  = (
        nir.subtract(red)
        .multiply(2.5)
        .divide(
            nir.add(red.multiply(6))
               .subtract(blue.multiply(7.5))
               .add(1.0)
        )
        .rename("EVI")
        .clamp(-1.0, 1.0)
    )
    return evi


def _idx_ndvi_l8(image):
    """NDVI from Landsat 8/9 SR C2: (SR_B5-SR_B4)/(SR_B5+SR_B4)."""
    return (
        image.normalizedDifference(["SR_B5", "SR_B4"])
        .rename("NDVI")
        .clamp(-1.0, 1.0)
    )


def _idx_ndwi_l8(image):
    """NDWI from Landsat 8/9 SR C2: (SR_B3-SR_B5)/(SR_B3+SR_B5)."""
    return (
        image.normalizedDifference(["SR_B3", "SR_B5"])
        .rename("NDWI")
        .clamp(-1.0, 1.0)
    )


def _idx_nbr_l8(image):
    """NBR from Landsat 8/9 SR C2: (SR_B5-SR_B7)/(SR_B5+SR_B7)."""
    return (
        image.normalizedDifference(["SR_B5", "SR_B7"])
        .rename("NBR")
        .clamp(-1.0, 1.0)
    )


_INDEX_FNS = {
    "_idx_ndvi_s2":  _idx_ndvi_s2,
    "_idx_ndwi_s2":  _idx_ndwi_s2,
    "_idx_nbr_s2":   _idx_nbr_s2,
    "_idx_ndbi_s2":  _idx_ndbi_s2,
    "_idx_evi_s2":   _idx_evi_s2,
    "_idx_ndvi_l8":  _idx_ndvi_l8,
    "_idx_ndwi_l8":  _idx_ndwi_l8,
    "_idx_nbr_l8":   _idx_nbr_l8,
}


def _compute_index(collection_key, bbox, date_start, date_end,
                   cloud_pct, composite_method, scale_override):
    """
    Build an ee.Image for a spectral index dataset.

    Workflow:
      1. Retrieve the source collection (e.g. sentinel2_sr) as a full composite
         with all required bands pre-selected.
      2. Apply the index function -> single-band ee.Image.
      3. Return (ee.Image, effective_scale, None) or (None, None, error_str).
    """
    cat        = COLLECTIONS[collection_key]
    source_key = cat["source_key"]
    src_cat    = COLLECTIONS[source_key]
    idx_fn     = _INDEX_FNS.get(cat["index_fn"])

    if idx_fn is None:
        return None, None, f"Unknown index function: {cat['index_fn']}"

    # Determine which source bands the index function needs
    # We always request all optical bands from the source to be safe
    all_src_bands = list(src_cat["band_info"].keys())
    scale = scale_override if scale_override else cat["scale"]

    # Build the composite from the source collection (full band set)
    src_image, _, err = get_image(
        source_key, bbox, date_start, date_end,
        cloud_pct=cloud_pct,
        bands=all_src_bands,
        composite_method=composite_method,
        scale_override=scale,
    )
    if err:
        return None, None, err

    try:
        index_image = idx_fn(src_image)
        log.info(
            "Index '%s' computed from %s composite.",
            cat["index_fn"], source_key,
        )
        return index_image, scale, None
    except Exception as exc:
        log.error("Index computation failed: %s", exc)
        return None, None, str(exc)


def get_image(collection_key, bbox, date_start, date_end,
              cloud_pct=30, bands=None, composite_method="median",
              scale_override=None):
    """
    Build a ready-to-download ee.Image.

    Handles three dataset types:
      "image"            : static EE asset (SRTM, GFC)
      "image_collection" : filtered + composited ImageCollection
      "index"            : spectral index computed from a source collection

    Returns (ee.Image, effective_scale, None) or (None, None, error_str).
    """
    cat = COLLECTIONS[collection_key]

    # ── Delegate index computation ────────────────────────────────────────────
    if cat["type"] == "index":
        return _compute_index(
            collection_key, bbox, date_start, date_end,
            cloud_pct, composite_method, scale_override,
        )

    import ee
    bands = bands or cat["default_bands"]
    scale = scale_override if scale_override else cat["scale"]

    try:
        s, w, n, e = bbox
        region     = ee.Geometry.Rectangle([w, s, e, n])

        # ── Static image ──────────────────────────────────────────────────────
        if cat["type"] == "image":
            image = ee.Image(cat["id"]).select(bands)
            return image, scale, None

        # ── ImageCollection ───────────────────────────────────────────────────
        ic = (
            ee.ImageCollection(cat["id"])
            .filterBounds(region)
            .filterDate(date_start, date_end)
        )

        cloud_prop = cat.get("cloud_prop")
        if cloud_prop:
            ic = ic.filter(ee.Filter.lte(cloud_prop, cloud_pct))

        try:
            count = ic.size().getInfo()
        except Exception as exc:
            return None, None, f"EE scene count failed: {exc}"

        if count == 0:
            suffix = f" with cloud <= {cloud_pct}%." if cloud_prop else "."
            return None, None, (
                f"No scenes for '{cat['label']}' "
                f"between {date_start} and {date_end}{suffix}\n"
                "Try a wider date range or a higher cloud cover threshold."
            )
        log.info("%s: %d scenes available", cat["label"], count)

        mask_fn_name = cat.get("cloud_mask_fn")
        if mask_fn_name and mask_fn_name in _MASK_FNS:
            ic = ic.map(_MASK_FNS[mask_fn_name])

        reducer = _REDUCERS.get(composite_method, _REDUCERS["median"])
        image   = reducer(ic).select(bands)

        # Physical scale factors (DN -> surface reflectance)
        sf = cat.get("sr_scale_factor")
        if sf is not None:
            image = image.multiply(sf)
            offset = cat.get("sr_offset", 0.0)
            if offset != 0.0:
                image = image.add(offset)
            if cat.get("clamp_reflectance"):
                image = image.clamp(0.0, 1.0)

        return image, scale, None

    except Exception as exc:
        log.error("get_image error: %s", exc)
        return None, None, str(exc)


# ── Download ──────────────────────────────────────────────────────────────────

def _is_zip_file(path):
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == b"PK\x03\x04"
    except OSError:
        return False


def _build_gdal_vrt(tif_paths, vrt_path):
    try:
        from osgeo import gdal
        opts = gdal.BuildVRTOptions(separate=True)
        gdal.BuildVRT(vrt_path, tif_paths, options=opts)
        log.info("VRT built: %s (%d bands)", vrt_path, len(tif_paths))
        return True
    except Exception as exc:
        log.warning("VRT build failed (%s); falling back to band 1.", exc)
        return False


# ── Google Drive export ───────────────────────────────────────────────────────

# Human-readable EE task states
TASK_STATE_LABELS = {
    "UNSUBMITTED": "Pending submission",
    "READY":       "Queued on EE servers",
    "RUNNING":     "Processing on EE servers",
    "COMPLETED":   "Export complete",
    "FAILED":      "Export failed",
    "CANCELLED":   "Cancelled",
    "CANCEL_REQUESTED": "Cancellation requested",
}


def start_drive_export(image, bbox, scale, bands,
                       asset_name="gee_extract",
                       drive_folder="GEE_Extractor"):
    """
    Submit an ee.batch.Export.image.toDrive task.

    Parameters
    ----------
    image        : ee.Image ready for export (already scaled / masked)
    bbox         : (S, W, N, E) WGS84
    scale        : export resolution in metres
    bands        : list of band names (subset already selected on image)
    asset_name   : filename in Drive (without extension)
    drive_folder : Drive folder name (created if absent)

    Returns (task_id: str, None) or (None, error_str).
    """
    try:
        import ee
        s, w, n, e = bbox
        region     = ee.Geometry.Rectangle([w, s, e, n])

        task = ee.batch.Export.image.toDrive(
            image=image,
            description=asset_name,
            folder=drive_folder,
            fileNamePrefix=asset_name,
            region=region,
            scale=scale,
            crs="EPSG:4326",
            fileFormat="GeoTIFF",
            maxPixels=int(1e10),       # raise EE default 1e8 cap
        )
        task.start()
        task_id = task.id
        log.info(
            "Drive export task started: %s (folder='%s', file='%s.tif')",
            task_id, drive_folder, asset_name,
        )
        return task_id, None
    except Exception as exc:
        return None, str(exc)


def poll_task_status(task_id):
    """
    Query the status of an EE batch task.

    Returns a dict:
      {
        "state":    "RUNNING" | "COMPLETED" | "FAILED" | ...,
        "label":    human-readable state string,
        "progress": float 0.0-1.0 (None if unavailable),
        "error":    error message if FAILED, else None,
        "drive_file": filename in Drive if COMPLETED, else None,
      }
    """
    try:
        import ee
        statuses = ee.data.getTaskStatus(task_id)
        if not statuses:
            return {
                "state": "UNKNOWN", "label": "Unknown task",
                "progress": None, "error": None, "drive_file": None,
            }
        st = statuses[0]
        state     = st.get("state", "UNKNOWN")
        label     = TASK_STATE_LABELS.get(state, state)
        progress  = st.get("progress", None)
        error_msg = st.get("error_message", None) if state == "FAILED" else None

        # When COMPLETED, EE includes destination_uris
        drive_file = None
        if state == "COMPLETED":
            uris = st.get("destination_uris", [])
            if uris:
                # URI looks like: https://drive.google.com/#folders/...
                # The actual filename is the description + ".tif"
                desc       = st.get("description", "gee_extract")
                drive_file = desc + ".tif"

        return {
            "state":      state,
            "label":      label,
            "progress":   progress,
            "error":      error_msg,
            "drive_file": drive_file,
        }
    except Exception as exc:
        return {
            "state": "ERROR", "label": str(exc),
            "progress": None, "error": str(exc), "drive_file": None,
        }


def cancel_task(task_id):
    """Cancel a running EE batch task."""
    try:
        import ee
        ee.data.cancelTask(task_id)
        log.info("Task %s cancelled.", task_id)
        return True
    except Exception as exc:
        log.warning("Task cancel failed: %s", exc)
        return False


def download_image(image, bbox, scale, output_path,
                   bands=None, stop_fn=None):
    """
    Download an ee.Image as GeoTIFF via getDownloadURL.
    Returns (final_path, None) or (None, error_str).
    """
    try:
        import ee
        import requests
    except ImportError as exc:
        return None, str(exc)

    try:
        s, w, n, e = bbox
        region = ee.Geometry.Rectangle([w, s, e, n])

        params = {
            "scale":       scale,
            "crs":         "EPSG:4326",
            "region":      region,
            "format":      "GEO_TIFF",
            "filePerBand": False,
        }
        if bands:
            params["bands"] = bands

        try:
            url = image.getDownloadURL(params)
        except Exception as exc:
            msg = str(exc)
            if "Too many pixels" in msg or "pixels" in msg.lower():
                return None, (
                    "Area too large for direct download at this resolution.\n"
                    "Increase the scale override or choose a coarser dataset."
                )
            return None, f"EE getDownloadURL failed: {msg}"

        log.info("Download URL obtained, streaming...")

        raw_path = output_path + ".raw"
        resp     = requests.get(url, timeout=300, stream=True)
        resp.raise_for_status()

        with open(raw_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                if stop_fn and stop_fn():
                    fh.close()
                    try:
                        os.remove(raw_path)
                    except OSError:
                        pass
                    return None, "Cancelled."
                fh.write(chunk)

        if _is_zip_file(raw_path):
            out_dir = os.path.dirname(output_path) or "."
            os.makedirs(out_dir, exist_ok=True)

            with zipfile.ZipFile(raw_path, "r") as zf:
                tif_names = sorted(
                    fname for fname in zf.namelist()
                    if fname.lower().endswith(".tif")
                )
                if not tif_names:
                    os.remove(raw_path)
                    return None, "Downloaded ZIP contains no GeoTIFF files."

                extracted = []
                for tname in tif_names:
                    zf.extract(tname, out_dir)
                    extracted.append(os.path.join(out_dir, tname))

            os.remove(raw_path)

            if len(extracted) == 1:
                os.replace(extracted[0], output_path)
                final_path = output_path
            else:
                vrt_path = os.path.splitext(output_path)[0] + ".vrt"
                if _build_gdal_vrt(extracted, vrt_path) and os.path.isfile(vrt_path):
                    final_path = vrt_path
                else:
                    os.replace(extracted[0], output_path)
                    for leftover in extracted[1:]:
                        try:
                            os.remove(leftover)
                        except OSError:
                            pass
                    final_path = output_path
        else:
            os.replace(raw_path, output_path)
            final_path = output_path

        log.info("Raster saved: %s", final_path)
        return final_path, None

    except Exception as exc:
        log.error("download_image error: %s", exc)
        for p in (output_path + ".raw", output_path):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        return None, str(exc)


# ── Load into QGIS ────────────────────────────────────────────────────────────

def _apply_renderer(layer, n_bands):
    """
    Apply the best renderer based on band count:
    - 1 band  : SingleBand pseudocolor (no change, QGIS default is fine)
    - 3+ bands: MultiBandColor RGB with ContrastEnhancement (min/max stretch)
    """
    try:
        from qgis.core import (
            QgsContrastEnhancement,
            QgsRasterMinMaxOrigin,
            QgsMultiBandColorRenderer,
        )
        if n_bands < 3:
            return   # SingleBand: QGIS default pseudocolor is already good

        renderer = QgsMultiBandColorRenderer(
            layer.dataProvider(), 1, 2, 3   # R=band1, G=band2, B=band3
        )

        origin = QgsRasterMinMaxOrigin()
        origin.setLimits(QgsRasterMinMaxOrigin.MinMax)
        origin.setExtent(QgsRasterMinMaxOrigin.WholeRaster)

        def _ce(band_idx):
            ce = QgsContrastEnhancement(layer.dataProvider().dataType(band_idx))
            ce.setContrastEnhancementAlgorithm(
                QgsContrastEnhancement.StretchToMinimumMaximum
            )
            stats = layer.dataProvider().bandStatistics(
                band_idx,
                flags=1,       # BandStatistics.All
            )
            ce.setMinimumValue(stats.minimumValue)
            ce.setMaximumValue(stats.maximumValue)
            return ce

        renderer.setRedContrastEnhancement(_ce(1))
        renderer.setGreenContrastEnhancement(_ce(2))
        renderer.setBlueContrastEnhancement(_ce(3))

        layer.setRenderer(renderer)
        layer.triggerRepaint()
    except Exception as exc:
        log.warning("Auto renderer failed (non-blocking): %s", exc)


def load_raster_to_qgis(path, layer_name, n_bands=None):
    """
    Add a raster to the current QGIS project with automatic renderer.
    n_bands : number of downloaded bands (for renderer selection).
    Returns (QgsRasterLayer, None) or (None, error_str).
    """
    layer = QgsRasterLayer(path, layer_name)
    if not layer.isValid():
        return None, f"QGIS cannot load raster: {path}"
    QgsProject.instance().addMapLayer(layer)
    log.info("Layer '%s' added to project.", layer_name)

    actual_bands = n_bands if n_bands else layer.bandCount()
    _apply_renderer(layer, actual_bands)

    return layer, None
