# Google Earth Engine Extractor — QGIS Plugin

**Google Earth Engine Extractor by LOMPO Ibrahim** lets you download Google Earth Engine rasters directly
into QGIS from any vector layer bounding box, with no manual export or code required.

## Requirements

- QGIS 3.16 or above (including QGIS 4.x)
- A [Google Earth Engine](https://earthengine.google.com/) account
- Python package: `earthengine-api >= 0.1.370`
- Python package: `requests >= 2.28.0` (bundled with most QGIS installations)

### Installing dependencies

```bash
pip install earthengine-api
```

Or from the OSGeo4W Shell (Windows):

```bash
pip install earthengine-api
```

## Supported datasets

| Key | Dataset | Native resolution |
|---|---|---|
| `sentinel2_sr` | Sentinel-2 Surface Reflectance Harmonized | 10 m |
| `landsat8_sr` | Landsat 8 SR Collection 2 Tier 1 | 30 m |
| `landsat9_sr` | Landsat 9 SR Collection 2 Tier 1 | 30 m |
| `srtm` | SRTM Digital Elevation Model | 30 m |
| `modis_ndvi` | MODIS MOD13Q1 NDVI 16-day | 250 m |
| `gfc` | Global Forest Change 2023 (Hansen) | 30 m |

## Authentication

Two methods are supported:

1. **Application Default Credentials** (recommended for personal use): run
   `earthengine authenticate` in a terminal once, then leave the credentials
   field empty in the plugin.

2. **Service account JSON key**: create a GCP service account, grant it the
   *Earth Engine Resource Viewer* role, generate a JSON key, and point the
   plugin to that file. Suitable for deployment in shared environments.

## Workflow

1. Load a vector layer whose extent defines the area of interest.
2. Open **Google Earth Engine Extractor** from the Plugins menu or toolbar.
3. Select the reference layer, dataset, date range, and cloud cover threshold.
4. Set the output path (GeoTIFF).
5. Click **Extract from GEE**.

The plugin runs the EE request in a background thread and loads the result as a
QGIS raster layer on completion.

## Size limit

The direct download path (`getDownloadURL`) is limited to approximately 50 MB
(uncompressed float32). For larger extents, reduce resolution by choosing a
coarser dataset (MODIS), or clip your reference layer to a smaller area.
For production pipelines over large regions, use GEE's `Export.image.toDrive`
instead.

## License

GNU General Public License v2 — see LICENSE file.

## Contact

LOMPO Ibrahim — ibrahimlompo33@gmail.com
Repository: https://github.com/ibrahimlompo33-coder/google-earth-engine-extrator
Tracker: https://github.com/ibrahimlompo33-coder/google-earth-engine-extrator/issues
