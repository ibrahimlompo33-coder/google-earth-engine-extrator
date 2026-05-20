# -*- coding: utf-8 -*-
"""
Google Earth Engine Extractor by Jambar Lab -- Main dialog.

New in this version:
  - Band selection panel with checkboxes + descriptions (per-dataset)
  - Composite method selector (median / mean / mosaic / min / max)
  - Scale override spinbox (metres) with auto-size estimate
  - Size estimate label that updates on-the-fly
  - Worker passes composite_method + scale_override through to gee_logic
"""

import logging
import os
import tempfile
import time

from qgis.core import Qgis, QgsMessageLog

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QCheckBox, QPushButton, QProgressBar,
    QGroupBox, QFrame, QLineEdit, QComboBox,
    QFileDialog, QDateEdit, QSpinBox, QScrollArea,
    QWidget,
)
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QDate, QSettings

from qgis.gui import QgsMapLayerComboBox

from .compat import (
    Qt_AlignCenter,
    vector_layer_filter, hline_shape, no_frame_shape,
    ScrollBarAlwaysOff,
)
from .gee_logic import (
    COLLECTIONS, COMPOSITE_METHODS,
    get_bbox_wgs84, estimate_size_bytes,
    init_ee, get_image, download_image, load_raster_to_qgis,
    start_drive_export, poll_task_status, cancel_task,
    MAX_DOWNLOAD_BYTES,
)

_pylog = logging.getLogger("GoogleEarthEngineExtractor")
PLUGIN_TAG   = "GEE Extractor"
SETTINGS_NS  = "GoogleEarthEngineExtractor"   # QSettings group key


def _settings():
    """Return a QSettings scoped to this plugin."""
    s = QSettings()
    s.beginGroup(SETTINGS_NS)
    return s


def _qlog(msg, level=Qgis.Info):
    """Write to QGIS Log Messages panel AND Python logger simultaneously."""
    QgsMessageLog.logMessage(str(msg), PLUGIN_TAG, level=level)
    if level == Qgis.Critical:
        _pylog.error(msg)
    elif level == Qgis.Warning:
        _pylog.warning(msg)
    else:
        _pylog.info(msg)


# ── Custom checkbox (prefix indicator) ───────────────────────────────────────

class IndicatorCheckBox(QCheckBox):
    CHECKED_PREFIX   = "✔  "
    UNCHECKED_PREFIX = "○  "

    def __init__(self, label, parent=None):
        self._raw_label = label
        super().__init__(self.UNCHECKED_PREFIX + label, parent)
        self.toggled.connect(self._refresh_text)

    def _refresh_text(self, checked):
        prefix = self.CHECKED_PREFIX if checked else self.UNCHECKED_PREFIX
        self.setText(prefix + self._raw_label)


# ── Worker thread ─────────────────────────────────────────────────────────────

class ExtractionWorker(QThread):
    """
    Hybrid download worker.

    Mode selection (automatic based on estimated size):
      DIRECT mode  : image.getDownloadURL()  — < MAX_DOWNLOAD_BYTES
      DRIVE  mode  : ee.batch.Export.toDrive + polling — >= MAX_DOWNLOAD_BYTES

    Signals
    -------
    progress(int)          : 0-100 progress bar value
    status(str)            : human-readable current step
    raster_ready(str,str,int) : (local_path, layer_name, n_bands) — DIRECT only
    drive_task_done(str,str)  : (task_id, drive_filename) — DRIVE mode
    finished(bool, str)    : (success, summary message)
    """

    progress       = pyqtSignal(int)
    status         = pyqtSignal(str)
    raster_ready   = pyqtSignal(str, str, int)   # path, layer_name, n_bands
    drive_task_done = pyqtSignal(str, str)        # task_id, drive_filename
    finished       = pyqtSignal(bool, str)

    POLL_INTERVAL  = 15   # seconds between EE task status queries
    DRIVE_PROGRESS = {    # EE state -> approximate progress bar value
        "UNSUBMITTED":      38,
        "READY":            42,
        "RUNNING":          55,
        "COMPLETED":        95,
        "FAILED":           95,
        "CANCELLED":        95,
        "CANCEL_REQUESTED": 90,
    }

    def __init__(self, params, parent=None):
        super().__init__(parent)
        self.params    = params
        self._stop     = False
        self._task_id  = None   # EE batch task ID (Drive mode only)

    def cancel(self):
        self._stop = True
        if self._task_id:
            cancel_task(self._task_id)

    # ── Entry point ───────────────────────────────────────────────────────────

    def run(self):
        p   = self.params
        cat = COLLECTIONS[p["collection_key"]]

        # ── Step 1 : authenticate ────────────────────────────────────────────
        self.progress.emit(5)
        self.status.emit("Authenticating with Earth Engine...")
        ok, err = init_ee(
            p.get("creds_path") or None,
            project_id=p.get("project_id") or None,
        )
        if err:
            self.finished.emit(False, f"Auth failed: {err}")
            return
        if self._stop:
            self.finished.emit(False, "Cancelled.")
            return

        # ── Step 2 : build ee.Image ──────────────────────────────────────────
        self.progress.emit(15)
        self.status.emit(f"Querying {cat['label']}...")
        image, eff_scale, err = get_image(
            p["collection_key"],
            p["bbox"],
            p["date_start"],
            p["date_end"],
            cloud_pct=p["cloud_pct"],
            bands=p["bands"],
            composite_method=p["composite_method"],
            scale_override=p.get("scale_override"),
        )
        if err:
            self.finished.emit(False, err)
            return
        if self._stop:
            self.finished.emit(False, "Cancelled.")
            return

        # ── Step 3 : choose mode based on size estimate ──────────────────────
        self.progress.emit(25)
        n_bands = len(p["bands"])
        est     = estimate_size_bytes(p["bbox"], eff_scale, n_bands)
        _qlog(f"Estimated size: {est/1e6:.1f} MB — scale {eff_scale} m, {n_bands} band(s)")

        use_drive = est >= MAX_DOWNLOAD_BYTES or p.get("force_drive", False)

        if use_drive:
            self._run_drive(p, image, eff_scale, n_bands)
        else:
            self._run_direct(p, image, eff_scale, n_bands, cat)

    # ── Direct download path ──────────────────────────────────────────────────

    def _run_direct(self, p, image, eff_scale, n_bands, cat):
        self.progress.emit(35)
        self.status.emit("Downloading raster from EE... (direct stream)")

        final_path, err = download_image(
            image,
            p["bbox"],
            eff_scale,
            p["output_path"],
            bands=p["bands"],
            stop_fn=lambda: self._stop,
        )
        if err:
            self.finished.emit(False, err)
            return
        if self._stop:
            self.finished.emit(False, "Cancelled.")
            return

        layer_name = self._layer_name(p, cat)
        self.progress.emit(95)
        self.raster_ready.emit(final_path, layer_name, n_bands)
        self.progress.emit(100)
        size_mb = os.path.getsize(final_path) / 1e6
        self.finished.emit(
            True,
            f"Layer '{layer_name}' added "
            f"({size_mb:.1f} MB — {n_bands} band(s), {eff_scale} m).",
        )

    # ── Drive export + polling path ───────────────────────────────────────────

    def _run_drive(self, p, image, eff_scale, n_bands, cat=None):
        if cat is None:
            cat = COLLECTIONS[p["collection_key"]]

        asset_name   = self._asset_name(p, cat)
        drive_folder = p.get("drive_folder", "GEE_Extractor")

        self.progress.emit(35)
        self.status.emit(
            f"Submitting export task to Drive folder '{drive_folder}'..."
        )

        task_id, err = start_drive_export(
            image,
            p["bbox"],
            eff_scale,
            p["bands"],
            asset_name=asset_name,
            drive_folder=drive_folder,
        )
        if err:
            self.finished.emit(False, f"Drive export submission failed: {err}")
            return

        self._task_id = task_id
        _qlog(f"Drive task submitted: {task_id}")

        # ── Polling loop ──────────────────────────────────────────────────────
        elapsed  = 0
        timeout  = p.get("drive_timeout_s", 3600)   # 1 hour default

        while elapsed < timeout:
            if self._stop:
                cancel_task(task_id)
                self.finished.emit(False, "Cancelled — EE task cancelled.")
                return

            st = poll_task_status(task_id)
            self.progress.emit(self.DRIVE_PROGRESS.get(st["state"], 50))

            pct_str = (
                f" — {st['progress']*100:.0f}%"
                if st.get("progress") is not None
                else ""
            )
            self.status.emit(f"EE task: {st['label']}{pct_str}")
            _qlog(f"Task {task_id} — {st['state']}{pct_str}")

            if st["state"] == "COMPLETED":
                drive_file = st.get("drive_file") or f"{asset_name}.tif"
                self.progress.emit(100)
                self.drive_task_done.emit(task_id, drive_file)
                self.finished.emit(
                    True,
                    f"Export complete. File '{drive_file}' is in your "
                    f"Google Drive folder '{drive_folder}'. "
                    "Download it manually and load it into QGIS.",
                )
                return

            if st["state"] in ("FAILED", "CANCELLED", "CANCEL_REQUESTED"):
                msg = st.get("error") or st["label"]
                self.finished.emit(False, f"EE task {st['state']}: {msg}")
                return

            # Wait between polls, checking _stop every second
            for _ in range(self.POLL_INTERVAL):
                if self._stop:
                    break
                time.sleep(1)
            elapsed += self.POLL_INTERVAL

        # Timeout
        self.finished.emit(
            False,
            f"Drive export timed out after {timeout//60} min. "
            f"Task '{task_id}' may still be running in your EE console.",
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _layer_name(p, cat):
        return (
            f"GEE_{cat['layer_name_prefix']}"
            f"_{p['date_start']}_{p['date_end']}"
        )

    @staticmethod
    def _asset_name(p, cat):
        """Drive filename: no spaces, no slashes, max 60 chars."""
        raw = (
            f"GEE_{cat['layer_name_prefix']}"
            f"_{p['date_start']}_{p['date_end']}"
        )
        return raw[:60]


# ── Main dialog ───────────────────────────────────────────────────────────────

class GoogleEarthEngineExtractorDialog(QDialog):

    def __init__(self, iface, parent=None):
        super().__init__(parent or iface.mainWindow())
        self.iface  = iface
        self.worker = None
        self._band_checks = {}

        self.setWindowTitle("Google Earth Engine Extractor")
        self.setMinimumWidth(460)
        self.setMinimumHeight(280)    # assez pour voir le bouton Run même très réduit
        self.resize(500, 700)

        # Allow minimize + maximize + close (all three buttons)
        try:
            flags = (
                Qt.WindowType.Window
                | Qt.WindowType.WindowMinimizeButtonHint
                | Qt.WindowType.WindowMaximizeButtonHint
                | Qt.WindowType.WindowCloseButtonHint
            )
        except AttributeError:
            # PyQt5
            flags = (
                Qt.Window
                | Qt.WindowMinimizeButtonHint
                | Qt.WindowMaximizeButtonHint
                | Qt.WindowCloseButtonHint
            )
        self.setWindowFlags(flags)

        self._build_ui()
        self._load_settings()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Outer layout: scroll area fills the dialog ────────────────────────
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Global scroll area — lets the user shrink the dialog to any height
        global_scroll = QScrollArea()
        global_scroll.setWidgetResizable(True)
        global_scroll.setFrameShape(no_frame_shape())
        global_scroll.setHorizontalScrollBarPolicy(ScrollBarAlwaysOff)
        outer.addWidget(global_scroll)

        # ── Inner container placed inside the scroll area ─────────────────────
        inner_widget = QWidget()
        root = QVBoxLayout(inner_widget)
        root.setSpacing(7)
        root.setContentsMargins(14, 12, 14, 12)

        root.addWidget(self._make_header())
        root.addWidget(self._make_layer_group())
        root.addWidget(self._make_dataset_group())
        root.addWidget(self._make_bands_group())
        root.addWidget(self._make_dates_cloud_group())
        root.addWidget(self._make_auth_group())
        root.addWidget(self._make_output_group())

        # Size estimate label
        self.lbl_size = QLabel("")
        self.lbl_size.setAlignment(Qt_AlignCenter)
        root.addWidget(self.lbl_size)

        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Ready")
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)

        self.lbl_status = QLabel("")
        self.lbl_status.setAlignment(Qt_AlignCenter)
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setVisible(False)
        root.addWidget(self.lbl_status)

        self.btn_run = QPushButton("Extract from Google Earth Engine")
        self.btn_run.setObjectName("btn_run")
        self.btn_run.clicked.connect(self._on_run)
        root.addWidget(self.btn_run)

        sep = QFrame()
        sep.setObjectName("sep")
        sep.setFrameShape(hline_shape())
        root.addWidget(sep)

        lbl_foot = QLabel(
            "Data: Google Earth Engine | (c) respective data providers"
        )
        lbl_foot.setAlignment(Qt_AlignCenter)
        root.addWidget(lbl_foot)

        root.addStretch()   # push everything up when dialog is taller than content

        global_scroll.setWidget(inner_widget)

        # Populate bands for the default dataset
        self._on_collection_changed(self.combo_collection.currentIndex())

    def _make_header(self):
        frame = QFrame()
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(0, 0, 0, 4)
        lbl = QLabel("<b>Google Earth Engine Extractor</b> — by LOMPO Ibrahim")
        try:
            lbl.setTextFormat(Qt.TextFormat.RichText)
        except AttributeError:
            lbl.setTextFormat(Qt.RichText)
        ver = QLabel("v1.0")
        ver.setStyleSheet("color: grey; font-style: italic;")
        lay.addWidget(lbl, 1)
        lay.addWidget(ver, 0)
        return frame

    def _make_layer_group(self):
        group = QGroupBox("Extraction zone — reference layer")
        lay   = QVBoxLayout(group)
        lay.setSpacing(4)
        lay.setContentsMargins(6, 4, 6, 6)

        hint = QLabel("Vector layer whose bounding box defines the area of interest:")
        lay.addWidget(hint)

        self.layer_combo = QgsMapLayerComboBox()
        self.layer_combo.setFilters(vector_layer_filter())
        self.layer_combo.setAllowEmptyLayer(False)
        self.layer_combo.layerChanged.connect(self._update_size_estimate)
        lay.addWidget(self.layer_combo)
        return group

    def _make_dataset_group(self):
        group = QGroupBox("Dataset / Composite / Scale")
        lay   = QVBoxLayout(group)
        lay.setSpacing(5)
        lay.setContentsMargins(6, 4, 6, 6)

        # Dataset
        lbl_ds = QLabel("Dataset:")
        lay.addWidget(lbl_ds)
        self.combo_collection = QComboBox()
        for key, cat in COLLECTIONS.items():
            self.combo_collection.addItem(cat["label"], userData=key)
        self.combo_collection.currentIndexChanged.connect(self._on_collection_changed)
        lay.addWidget(self.combo_collection)

        # Composite method
        row_c = QHBoxLayout()
        lbl_comp = QLabel("Composite:")
        lbl_comp.setFixedWidth(70)
        self.combo_composite = QComboBox()
        for key, label in COMPOSITE_METHODS.items():
            self.combo_composite.addItem(label, userData=key)
        row_c.addWidget(lbl_comp)
        row_c.addWidget(self.combo_composite, 1)
        lay.addLayout(row_c)

        # Scale override
        row_s = QHBoxLayout()
        lbl_sc = QLabel("Scale (m):")
        lbl_sc.setFixedWidth(70)
        self.spin_scale = QSpinBox()
        self.spin_scale.setRange(10, 10000)
        self.spin_scale.setSingleStep(10)
        self.spin_scale.setSpecialValueText("Native")
        self.spin_scale.setValue(10)          # will be overwritten on dataset change
        self.spin_scale.valueChanged.connect(self._update_size_estimate)
        row_s.addWidget(lbl_sc)
        row_s.addWidget(self.spin_scale, 1)
        lay.addLayout(row_s)

        return group

    def _make_bands_group(self):
        group = QGroupBox("Bands to download")
        lay   = QVBoxLayout(group)
        lay.setSpacing(4)
        lay.setContentsMargins(6, 4, 6, 6)

        # Select All / None buttons
        row = QHBoxLayout()
        btn_all = QPushButton("Select all")
        btn_all.setObjectName("btn_selall")
        btn_all.clicked.connect(self._select_all_bands)
        btn_none = QPushButton("Select none")
        btn_none.setObjectName("btn_selnone")
        btn_none.clicked.connect(self._select_no_bands)
        row.addWidget(btn_all)
        row.addWidget(btn_none)
        row.addStretch()
        lay.addLayout(row)

        # Scrollable band list
        self.scroll_bands = QScrollArea()
        self.scroll_bands.setWidgetResizable(True)
        self.scroll_bands.setMinimumHeight(70)
        self.scroll_bands.setMaximumHeight(120)

        self.bands_widget = QWidget()
        self.bands_layout = QVBoxLayout(self.bands_widget)
        self.bands_layout.setSpacing(2)
        self.bands_layout.setContentsMargins(4, 4, 4, 4)
        self.scroll_bands.setWidget(self.bands_widget)
        lay.addWidget(self.scroll_bands)

        return group

    def _make_dates_cloud_group(self):
        group = QGroupBox("Date range and cloud filter")
        lay   = QVBoxLayout(group)
        lay.setSpacing(5)
        lay.setContentsMargins(6, 4, 6, 6)

        # Dates row
        row_d = QHBoxLayout()
        row_d.setSpacing(8)

        col_from = QVBoxLayout()
        lbl_from = QLabel("From:")
        self.date_start = QDateEdit()
        self.date_start.setCalendarPopup(True)
        self.date_start.setDate(QDate.currentDate().addYears(-1))
        self.date_start.setDisplayFormat("yyyy-MM-dd")
        col_from.addWidget(lbl_from)
        col_from.addWidget(self.date_start)

        col_to = QVBoxLayout()
        lbl_to = QLabel("To:")
        self.date_end = QDateEdit()
        self.date_end.setCalendarPopup(True)
        self.date_end.setDate(QDate.currentDate())
        self.date_end.setDisplayFormat("yyyy-MM-dd")
        col_to.addWidget(lbl_to)
        col_to.addWidget(self.date_end)

        row_d.addLayout(col_from, 1)
        row_d.addLayout(col_to, 1)
        lay.addLayout(row_d)

        # Cloud cover
        row_cl = QHBoxLayout()
        self.lbl_cloud = QLabel("Max cloud cover (%):")
        self.spin_cloud = QSpinBox()
        self.spin_cloud.setRange(0, 100)
        self.spin_cloud.setValue(30)
        row_cl.addWidget(self.lbl_cloud)
        row_cl.addWidget(self.spin_cloud)
        row_cl.addStretch()
        lay.addLayout(row_cl)

        return group

    def _make_auth_group(self):
        group = QGroupBox("Authentication")
        lay   = QVBoxLayout(group)
        lay.setSpacing(4)
        lay.setContentsMargins(6, 4, 6, 6)

        hint = QLabel(
            "Leave both fields empty to use Application Default Credentials "
            "(earthengine authenticate). Or select a service account JSON key. "
            "GCP Project ID required for SDK >= 0.1.370 with ADC."
        )
        hint.setWordWrap(True)
        lay.addWidget(hint)

        # Service account JSON key
        row_k = QHBoxLayout()
        self.edit_creds = QLineEdit()
        self.edit_creds.setPlaceholderText("Service account JSON key (optional)")
        btn_k = QPushButton("Browse")
        btn_k.setObjectName("btn_browse")
        btn_k.clicked.connect(self._browse_creds)
        row_k.addWidget(self.edit_creds, 1)
        row_k.addWidget(btn_k, 0)
        lay.addLayout(row_k)

        # GCP Project ID
        lbl_proj = QLabel("GCP Project ID:")
        self.edit_project = QLineEdit()
        self.edit_project.setPlaceholderText(
            "e.g. my-gee-project (auto-read from JSON key if left empty)"
        )
        lay.addWidget(lbl_proj)
        lay.addWidget(self.edit_project)

        return group

    def _make_output_group(self):
        group = QGroupBox("Output")
        lay   = QVBoxLayout(group)
        lay.setSpacing(4)
        lay.setContentsMargins(6, 4, 6, 6)

        # ── Direct output path ────────────────────────────────────────────────
        hint = QLabel("Local output path (direct download, < 48 MB):")
        lay.addWidget(hint)

        row_f = QHBoxLayout()
        self.edit_output = QLineEdit()
        self.edit_output.setText(
            os.path.join(tempfile.gettempdir(), "gee_extract_output.tif")
        )
        btn_f = QPushButton("Browse")
        btn_f.setObjectName("btn_browse")
        btn_f.clicked.connect(self._browse_output)
        row_f.addWidget(self.edit_output, 1)
        row_f.addWidget(btn_f, 0)
        lay.addLayout(row_f)

        self.chk_add = IndicatorCheckBox("Add layer to QGIS after download")
        self.chk_add.setChecked(True)
        lay.addWidget(self.chk_add)

        # ── Drive export ──────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(hline_shape())
        lay.addWidget(sep)

        lbl_drive = QLabel(
            "Google Drive folder (large exports >= 48 MB — auto mode):"
        )
        lbl_drive.setWordWrap(True)
        lay.addWidget(lbl_drive)

        self.edit_drive_folder = QLineEdit()
        self.edit_drive_folder.setPlaceholderText("GEE_Extractor")
        self.edit_drive_folder.setText("GEE_Extractor")
        lay.addWidget(self.edit_drive_folder)

        self.chk_force_drive = IndicatorCheckBox(
            "Force Drive export (bypass direct download)"
        )
        self.chk_force_drive.setChecked(False)
        lay.addWidget(self.chk_force_drive)

        return group

    # ── Band panel management ─────────────────────────────────────────────────

    def _clear_band_panel(self):
        while self.bands_layout.count():
            item = self.bands_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._band_checks.clear()

    def _populate_band_panel(self, collection_key):
        self._clear_band_panel()
        cat          = COLLECTIONS[collection_key]
        default_bands = cat["default_bands"]

        for band_name, description in cat["band_info"].items():
            chk = IndicatorCheckBox(f"{band_name}  —  {description}")
            chk.setChecked(band_name in default_bands)
            chk.toggled.connect(self._update_size_estimate)
            self._band_checks[band_name] = chk
            self.bands_layout.addWidget(chk)

        self.bands_layout.addStretch()

    def _selected_bands(self):
        return [b for b, chk in self._band_checks.items() if chk.isChecked()]

    def _select_all_bands(self):
        for chk in self._band_checks.values():
            chk.setChecked(True)

    def _select_no_bands(self):
        for chk in self._band_checks.values():
            chk.setChecked(False)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_collection_changed(self, _index):
        key = self.combo_collection.currentData()
        if key is None:
            return
        cat = COLLECTIONS[key]

        # For index datasets, resolve state from the source collection
        if cat["type"] == "index":
            src_cat       = COLLECTIONS[cat["source_key"]]
            needs_dates   = cat.get("needs_dates",    src_cat["needs_dates"])
            has_cloud     = cat.get("has_cloud_filter", src_cat["has_cloud_filter"])
            has_composite = True   # indices always computed from a composite
        else:
            needs_dates   = cat["needs_dates"]
            has_cloud     = cat["has_cloud_filter"]
            has_composite = cat["type"] == "image_collection"

        # Repopulate bands
        self._populate_band_panel(key)

        # Update native scale in spinbox
        self.spin_scale.setValue(cat["scale"])

        # Enable/disable date and cloud widgets
        self.date_start.setEnabled(needs_dates)
        self.date_end.setEnabled(needs_dates)
        self.lbl_cloud.setEnabled(has_cloud)
        self.spin_cloud.setEnabled(has_cloud)
        self.combo_composite.setEnabled(has_composite)

        self._update_size_estimate()

    def _update_size_estimate(self):
        """Compute and display estimated download size from current settings."""
        key   = self.combo_collection.currentData()
        layer = self.layer_combo.currentLayer()
        bands = self._selected_bands()
        if key is None or layer is None or not bands:
            self.lbl_size.setText("")
            return

        bbox = get_bbox_wgs84(layer)
        if bbox is None:
            self.lbl_size.setText("")
            return

        scale  = self.spin_scale.value()
        n_bands = len(bands)
        est    = estimate_size_bytes(bbox, scale, n_bands)
        mb     = est / 1e6

        icon = "~"
        self.lbl_size.setText(
            f"{icon} Estimated size: {mb:.1f} MB ({n_bands} band(s), {scale} m)"
        )
        self.lbl_size.setStyleSheet(
            "color: orange;" if mb > MAX_DOWNLOAD_BYTES / 1e6
            else "color: grey; font-style: italic;"
        )

    def _browse_creds(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select service account JSON key", "", "JSON files (*.json)"
        )
        if path:
            self.edit_creds.setText(path)

    def _browse_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save GeoTIFF as", self.edit_output.text(),
            "GeoTIFF (*.tif);;VRT (*.vrt)"
        )
        if path:
            self.edit_output.setText(path)

    # ── Run ───────────────────────────────────────────────────────────────────

    def _on_run(self):
        layer = self.layer_combo.currentLayer()
        if layer is None or not layer.isValid():
            self.iface.messageBar().pushWarning(
                "GEE Extractor", "No valid layer selected."
            )
            return

        collection_key = self.combo_collection.currentData()
        cat            = COLLECTIONS[collection_key]

        bands = self._selected_bands()
        if not bands:
            self.iface.messageBar().pushWarning(
                "GEE Extractor", "Select at least one band."
            )
            return

        date_start = self.date_start.date().toString("yyyy-MM-dd")
        date_end   = self.date_end.date().toString("yyyy-MM-dd")

        # For index type, resolve needs_dates from source collection
        if cat["type"] == "index":
            src_cat     = COLLECTIONS[cat["source_key"]]
            needs_dates = src_cat["needs_dates"]
        else:
            needs_dates = cat["needs_dates"]

        if needs_dates and date_start >= date_end:
            self.iface.messageBar().pushWarning(
                "GEE Extractor", "Start date must be before end date."
            )
            return

        bbox = get_bbox_wgs84(layer)
        if bbox is None:
            self.iface.messageBar().pushCritical(
                "GEE Extractor",
                "Cannot compute layer extent. Check that the layer has a CRS."
            )
            return

        output_path = self.edit_output.text().strip()
        if not output_path:
            self.iface.messageBar().pushWarning(
                "GEE Extractor", "Specify an output path."
            )
            return
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        composite_method = self.combo_composite.currentData() or "median"
        scale_val        = self.spin_scale.value()

        params = {
            "collection_key":  collection_key,
            "bbox":            bbox,
            "date_start":      date_start,
            "date_end":        date_end,
            "cloud_pct":       self.spin_cloud.value(),
            "bands":           bands,
            "composite_method": composite_method,
            "scale_override":  scale_val,
            "output_path":     output_path,
            "creds_path":      self.edit_creds.text().strip(),
            "project_id":      self.edit_project.text().strip(),
            "drive_folder":    self.edit_drive_folder.text().strip() or "GEE_Extractor",
            "force_drive":     self.chk_force_drive.isChecked(),
        }

        self._set_running(True)
        self.iface.messageBar().pushInfo(
            "GEE Extractor",
            f"Extracting {cat['label']} — {len(bands)} band(s) "
            f"[{', '.join(bands)}] at {scale_val} m...",
        )

        self.worker = ExtractionWorker(params, parent=self)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.status.connect(self._on_status)
        self.worker.raster_ready.connect(self._on_raster_ready)
        self.worker.drive_task_done.connect(self._on_drive_done)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_status(self, msg):
        self.lbl_status.setText(msg)
        short = msg[:50] + "..." if len(msg) > 50 else msg
        self.progress_bar.setFormat(short)

    def _on_raster_ready(self, path, layer_name, n_bands):
        """Called in main thread — safe to interact with QGIS API."""
        if self.chk_add.isChecked():
            _layer, err = load_raster_to_qgis(path, layer_name, n_bands)
            if err:
                self.iface.messageBar().pushWarning("GEE Extractor", err)

    def _on_drive_done(self, task_id, drive_filename):
        """
        Called when a Drive export completes.
        Shows an info banner with the filename — user downloads manually.
        """
        self.iface.messageBar().pushInfo(
            "GEE Extractor — Drive export complete",
            f"File '{drive_filename}' is ready in your Google Drive. "
            "Download it and use Layer > Add Layer > Add Raster Layer to load it.",
        )
        _qlog(
            f"Drive export done — task {task_id} — file: {drive_filename}",
            level=Qgis.Info,
        )

    def _on_finished(self, success, summary):
        self._set_running(False)
        self.progress_bar.setFormat("Done" if success else "Failed")
        self.lbl_status.setText(summary)
        if success:
            self.iface.messageBar().pushSuccess("GEE Extractor", summary)
        else:
            self.iface.messageBar().pushWarning("GEE Extractor", summary)

    def _set_running(self, running):
        self.btn_run.setEnabled(not running)
        self.layer_combo.setEnabled(not running)
        self.combo_collection.setEnabled(not running)
        self.combo_composite.setEnabled(not running)
        self.spin_scale.setEnabled(not running)
        self.edit_drive_folder.setEnabled(not running)
        self.chk_force_drive.setEnabled(not running)
        self.chk_add.setEnabled(not running)
        self.edit_output.setEnabled(not running)
        for chk in self._band_checks.values():
            chk.setEnabled(not running)

        if running:
            self.date_start.setEnabled(False)
            self.date_end.setEnabled(False)
            self.spin_cloud.setEnabled(False)
            self.lbl_cloud.setEnabled(False)
        else:
            self._on_collection_changed(self.combo_collection.currentIndex())

        self.progress_bar.setVisible(True)
        self.lbl_status.setVisible(True)
        if running:
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("Starting...")
            self.lbl_status.setText("Connecting to Earth Engine...")

    # ── Settings persistence ──────────────────────────────────────────────────

    def _load_settings(self):
        """Restore last-used values from QSettings."""
        s = _settings()

        # Window geometry (position + size)
        geom = s.value("window_geometry")
        if geom:
            self.restoreGeometry(geom)

        # Dataset
        saved_key = s.value("collection_key", "sentinel2_sr")
        for i in range(self.combo_collection.count()):
            if self.combo_collection.itemData(i) == saved_key:
                self.combo_collection.setCurrentIndex(i)
                break

        # Composite
        saved_comp = s.value("composite_method", "median")
        for i in range(self.combo_composite.count()):
            if self.combo_composite.itemData(i) == saved_comp:
                self.combo_composite.setCurrentIndex(i)
                break

        # Dates
        d_start = s.value("date_start", "")
        d_end   = s.value("date_end",   "")
        if d_start:
            self.date_start.setDate(QDate.fromString(d_start, "yyyy-MM-dd"))
        if d_end:
            self.date_end.setDate(QDate.fromString(d_end, "yyyy-MM-dd"))

        # Cloud + scale
        self.spin_cloud.setValue(int(s.value("cloud_pct", 30)))
        saved_scale = int(s.value("scale_override", 0))
        if saved_scale > 0:
            self.spin_scale.setValue(saved_scale)

        # Credentials + output
        creds = s.value("creds_path", "")
        if creds:
            self.edit_creds.setText(creds)
        proj = s.value("project_id", "")
        if proj:
            self.edit_project.setText(proj)
        out = s.value("output_path", "")
        if out:
            self.edit_output.setText(out)
        drive = s.value("drive_folder", "GEE_Extractor")
        if drive:
            self.edit_drive_folder.setText(drive)

        s.endGroup()

    def _save_settings(self):
        """Persist current UI values to QSettings."""
        s = _settings()
        s.setValue("window_geometry",  self.saveGeometry())
        s.setValue("collection_key",   self.combo_collection.currentData())
        s.setValue("composite_method", self.combo_composite.currentData())
        s.setValue("date_start",       self.date_start.date().toString("yyyy-MM-dd"))
        s.setValue("date_end",         self.date_end.date().toString("yyyy-MM-dd"))
        s.setValue("cloud_pct",        self.spin_cloud.value())
        s.setValue("scale_override",   self.spin_scale.value())
        s.setValue("creds_path",       self.edit_creds.text().strip())
        s.setValue("project_id",       self.edit_project.text().strip())
        s.setValue("output_path",      self.edit_output.text().strip())
        s.setValue("drive_folder",     self.edit_drive_folder.text().strip())
        s.endGroup()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(5000)
        self._save_settings()
        event.accept()
