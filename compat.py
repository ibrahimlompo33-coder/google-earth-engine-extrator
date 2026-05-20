# -*- coding: utf-8 -*-
"""
Google Earth Engine Extractor -- Compatibility layer PyQt5 (QGIS 3.x) / PyQt6 (QGIS 4.x).
Import aliases from this module instead of using Qt enums directly.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QSizePolicy, QMessageBox

# ── Qt alignment / orientation enums ─────────────────────────────────────────
try:
    # PyQt6
    Qt_AlignCenter  = Qt.AlignmentFlag.AlignCenter
    Qt_AlignLeft    = Qt.AlignmentFlag.AlignLeft
    Qt_AlignVCenter = Qt.AlignmentFlag.AlignVCenter
    Qt_Horizontal   = Qt.Orientation.Horizontal
    Qt_UserRole     = Qt.ItemDataRole.UserRole
    SP_Fixed        = QSizePolicy.Policy.Fixed
    SP_Preferred    = QSizePolicy.Policy.Preferred
    SP_Expanding    = QSizePolicy.Policy.Expanding
    MsgBox_Yes      = QMessageBox.StandardButton.Yes
    MsgBox_No       = QMessageBox.StandardButton.No
except AttributeError:
    # PyQt5
    Qt_AlignCenter  = Qt.AlignCenter
    Qt_AlignLeft    = Qt.AlignLeft
    Qt_AlignVCenter = Qt.AlignVCenter
    Qt_Horizontal   = Qt.Horizontal
    Qt_UserRole     = Qt.UserRole
    SP_Fixed        = QSizePolicy.Fixed
    SP_Preferred    = QSizePolicy.Preferred
    SP_Expanding    = QSizePolicy.Expanding
    MsgBox_Yes      = QMessageBox.Yes
    MsgBox_No       = QMessageBox.No


# ── QgsField string type ──────────────────────────────────────────────────────
def make_string_field(name):
    """QgsField of type text, compatible with PyQt5 and PyQt6."""
    from qgis.core import QgsField
    try:
        from qgis.PyQt.QtCore import QMetaType
        return QgsField(name, QMetaType.Type.QString)
    except (ImportError, AttributeError):
        from qgis.PyQt.QtCore import QVariant
        return QgsField(name, QVariant.String)


# ── QgsMapLayerProxyModel filter ──────────────────────────────────────────────
def vector_layer_filter():
    """Return the correct VectorLayer filter enum for QgsMapLayerComboBox."""
    from qgis.core import QgsMapLayerProxyModel
    try:
        return QgsMapLayerProxyModel.Filter.VectorLayer
    except AttributeError:
        return QgsMapLayerProxyModel.VectorLayer


# ── Dialog exec() ─────────────────────────────────────────────────────────────
def dialog_exec(dlg):
    """Call exec() or exec_() depending on Qt version."""
    if hasattr(dlg, "exec"):
        return dlg.exec()
    return dlg.exec_()


# ── ScrollBarPolicy ───────────────────────────────────────────────────────────
# PyQt6: Qt.ScrollBarPolicy.AlwaysOff / AsNeeded / AlwaysOn
# PyQt5: Qt.ScrollBarAlwaysOff  (members are flat on Qt, not on Qt.ScrollBarPolicy)
try:
    ScrollBarAlwaysOff = Qt.ScrollBarPolicy.AlwaysOff
    ScrollBarAsNeeded  = Qt.ScrollBarPolicy.AsNeeded
    ScrollBarAlwaysOn  = Qt.ScrollBarPolicy.AlwaysOn
except AttributeError:
    ScrollBarAlwaysOff = Qt.ScrollBarAlwaysOff   # type: ignore[attr-defined]
    ScrollBarAsNeeded  = Qt.ScrollBarAsNeeded    # type: ignore[attr-defined]
    ScrollBarAlwaysOn  = Qt.ScrollBarAlwaysOn    # type: ignore[attr-defined]


# ── QFrame shape/shadow enums ─────────────────────────────────────────────────
def no_frame_shape():
    """Return QFrame NoFrame shape, compatible with PyQt5 and PyQt6."""
    from qgis.PyQt.QtWidgets import QFrame
    try:
        return QFrame.Shape.NoFrame
    except AttributeError:
        return QFrame.NoFrame  # type: ignore[attr-defined]


# ── QFrame line shape ─────────────────────────────────────────────────────────
def hline_shape():
    """Return HLine shape for QFrame."""
    from qgis.PyQt.QtWidgets import QFrame
    try:
        return QFrame.Shape.HLine
    except AttributeError:
        return QFrame.HLine
