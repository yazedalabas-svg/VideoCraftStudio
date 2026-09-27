from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import (
    QEasingCurve,
    QAbstractAnimation,
    QElapsedTimer,
    QEvent,
    QObject,
    QPropertyAnimation,
    QPointF,
    QProcess,
    QProcessEnvironment,
    QRect,
    QRectF,
    QSettings,
    QSizeF,
    Qt,
    QTimer,
    QUrl,
    QVariantAnimation,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QCloseEvent,
    QColor,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QFontDatabase,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QRegion,
)
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame, QVideoSink
from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem, QVideoWidget
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from video_engine import (
    IMAGE_OUTPUT_SUFFIXES,
    IMAGE_SUFFIXES,
    ExportSettings,
    MediaInfo,
    VideoEngineError,
    build_ffmpeg_command,
    build_image_ffmpeg_command,
    format_duration,
    format_size,
    image_target_box,
    probe_media,
    target_box,
)
from ai_engine import (
    ai_engine_available,
    build_ai_pipeline_command,
    vulkan_driver_manifest,
    waifu2x_available,
)


APP_NAME = "VideoCraft Studio"
APP_NAME_AR = "استديو ڤيديو كرافت"
ROOT = Path(__file__).resolve().parent
ICON_PATH = ROOT / "assets" / "videocraft.svg"


STYLE = """
* {
    font-family: "Rubik", "Segoe UI", "Tahoma";
    font-size: 14px;
    color: #E4DFD7;
}
QMainWindow, QDialog, QWidget#Root {
    background: #121419;
}
QScrollArea, QScrollArea > QWidget > QWidget {
    background: transparent;
    border: none;
}
QFrame#Header {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(28, 31, 37, 235), stop:1 rgba(19, 21, 26, 235));
    border-bottom: 1px solid #2A2E36;
}
QFrame#Card, QFrame#JobCard {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #1D2027, stop:1 #191B21);
    border: 1px solid #2A2D35;
    border-top-color: #353A45;
    border-bottom-color: #0E0F13;
    border-radius: 20px;
}
QFrame#Card:hover { border-color: #3A3F4B; }
QFrame#DropZone {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #1A1D24, stop:1 #15171D);
    border: 2px dashed #6D573E;
    border-radius: 18px;
}
QFrame#DropZone:hover { border-color: #9A7448; }
QFrame#DropZone[dragActive="true"] {
    background: #241F17;
    border: 2px solid #D8904B;
}
QFrame#BatchPanel {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #1C2028, stop:1 #15171D);
    border: 1px solid #3A3226;
    border-radius: 18px;
}
QFrame#BatchTile {
    background: #111317;
    border: 1px solid #2C303A;
    border-radius: 12px;
}
QFrame#BatchTile:hover { border-color: #5F4A31; }
QFrame#BatchTile[active="true"] { border: 2px solid #E09A54; background: #1D1913; }
QFrame#BatchTile[state="done"] { border-color: #315343; }
QFrame#BatchTile[state="failed"] { border-color: #6A3434; }
QLabel#TileThumb { background: #0A0C0F; border-radius: 8px; color: #5D636E; }
QLabel#TileName { color: #E9E3DA; font-size: 11px; }
QLabel#TileStatus {
    border-radius: 8px; padding: 1px 7px; font-size: 10px; font-weight: 650;
    background: #23262E; color: #9AA0AB;
}
QLabel#TileStatus[state="running"] { background: #3A2B1A; color: #F4BD80; }
QLabel#TileStatus[state="done"] { background: #1C2923; color: #7ED0A3; }
QLabel#TileStatus[state="failed"] { background: #2E1B1B; color: #F09A9A; }
QLabel#TileIndex {
    background: rgba(10, 12, 15, 200); color: #EFBE87; border-radius: 8px;
    padding: 0 6px; font-size: 10px; font-weight: 700;
}
QPushButton#TileRemove {
    background: rgba(10, 12, 15, 210); border: none; border-radius: 9px;
    color: #C9CDD4; font-weight: 700; padding: 0; min-width: 18px; max-width: 18px;
    min-height: 18px; max-height: 18px;
}
QPushButton#TileRemove:hover { background: #7A2F2F; color: #FFFFFF; }
QProgressBar#BatchProgress { height: 6px; max-height: 6px; border-radius: 3px; }
QProgressBar#BatchProgress::chunk { border-radius: 3px; }
QScrollArea#BatchStrip { background: transparent; border: none; }
QScrollArea#BatchStrip > QWidget > QWidget { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 9px; margin: 2px 5px; }
QScrollBar::handle:horizontal { background: #343943; border-radius: 4px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QFrame#Thumb {
    background: #0A0C0F;
    border: 1px solid #2E3239;
    border-radius: 13px;
}
QFrame#ComparePane {
    background: #070809;
    border: 1px solid #2E3239;
    border-radius: 12px;
}
QSplitter#CompareSplitter::handle {
    background: #E09A54;
    width: 4px;
}
QFrame#Divider {
    background: #272B33;
    min-height: 1px;
    max-height: 1px;
}
QLabel#Brand {
    font-family: "Segoe UI", "Tahoma";
    font-size: 20px;
    font-weight: 700;
    color: #F1EBE2;
    letter-spacing: 0.4px;
}
QLabel#PageTitle {
    font-family: "Segoe UI", "Tahoma";
    font-size: 25px;
    font-weight: 700;
    color: #F1EBE2;
}
QLabel#CardTitle {
    font-family: "Segoe UI", "Tahoma";
    font-size: 17px;
    font-weight: 700;
    color: #EFE9E0;
}
QLabel#SourceName {
    font-family: "Segoe UI", "Tahoma";
    font-size: 17px;
    font-weight: 650;
    color: #F0EAE1;
}
QLabel#Muted, QLabel#CardSubtitle, QLabel#Hint, QLabel#Meta, QLabel#Footnote {
    color: #A3A9B3;
}
QLabel#CardSubtitle { font-size: 13px; }
QLabel#Hint { font-size: 12px; }
QLabel#Meta { font-size: 13px; }
QLabel#Footnote { font-size: 12px; color: #7D838D; }
QLabel#AccentText { color: #DDA872; font-weight: 650; }
QLabel#SuccessText { color: #7ED0A3; font-weight: 650; }
QLabel#ValueBadge {
    background: #2A251D;
    border: 1px solid #5F4A31;
    border-radius: 10px;
    color: #EFBE87;
    font-weight: 650;
    padding: 3px 9px;
    min-width: 42px;
}
QLabel#StatusPill {
    background: rgba(28, 41, 35, 200);
    border: 1px solid #315343;
    border-radius: 12px;
    color: #7ED0A3;
    padding: 5px 11px;
    font-size: 11px;
}
QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #2A2E36, stop:1 #252830);
    border: 1px solid #373C46;
    border-radius: 12px;
    padding: 9px 16px;
    font-weight: 600;
}
QPushButton:hover {
    background: #31353E;
    border-color: #4A505C;
    color: #F4EFE7;
}
QPushButton:pressed { background: #23262D; }
QPushButton:disabled { color: #5E636B; background: #1F2228; border-color: #282C33; }
QPushButton#Primary {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #E9B47A, stop:1 #B87236);
    border: 1px solid #E2AE78;
    color: #17100A;
    font-size: 14px;
    font-weight: 750;
    padding: 11px 22px;
}
QPushButton#Primary:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #FBC98C, stop:1 #CB7E3A);
    border-color: #F7C68C;
}
QPushButton#Primary:pressed { background: #AE662C; }
QPushButton#Soft {
    background: #26221C;
    border-color: #5C4932;
    color: #EDBA85;
}
QPushButton#Soft:hover { background: #312B22; border-color: #75593B; }
QPushButton#Danger { color: #EE9A9A; border-color: #663D3D; }
QPushButton#Danger:hover { background: #332226; }
QPushButton#Pill {
    background: #20232A;
    border: 1px solid #343841;
    border-radius: 12px;
    padding: 10px 12px;
    color: #B7BBC4;
}
QPushButton#Pill:hover { color: #F4EFE8; border-color: #565C68; background: #262A32; }
QPushButton#Pill:checked {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #332A1E, stop:1 #2A2218);
    border: 1px solid #D08A44;
    color: #F5BE83;
    font-weight: 700;
}
QLineEdit, QComboBox {
    background: #101318;
    border: 1px solid #32363F;
    border-radius: 10px;
    padding: 8px 11px;
    min-height: 20px;
    selection-background-color: #B97035;
}
QLineEdit:focus, QComboBox:focus { border-color: #C08147; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView {
    background: #191C22;
    border: 1px solid #3A3F4A;
    border-radius: 8px;
    selection-background-color: #3D2F22;
    padding: 4px;
    outline: none;
}
QCheckBox { spacing: 9px; color: #DAD7D1; }
QCheckBox:hover { color: #FFFFFF; }
QCheckBox::indicator {
    width: 19px; height: 19px;
    border-radius: 6px;
    border: 1px solid #4A505B;
    background: #12151A;
}
QCheckBox::indicator:hover { border-color: #C08147; }
QCheckBox::indicator:checked {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #E2A057, stop:1 #C67F3F);
    border-color: #D8904B;
    image: none;
}
QSlider::groove:horizontal {
    background: #2C3038;
    height: 5px;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #C07C3E, stop:1 #E0A05C);
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #F4BD80;
    border: 3px solid #4A3620;
    width: 15px;
    height: 15px;
    margin: -7px 0;
    border-radius: 10px;
}
QSlider::handle:horizontal:hover { background: #FFD3A0; }
QProgressBar {
    background: #23262D;
    border: none;
    border-radius: 6px;
    height: 10px;
    max-height: 10px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #B9793F, stop:1 #E6B07A);
    border-radius: 5px;
}
QScrollBar:vertical { background: transparent; width: 9px; margin: 6px 2px; }
QScrollBar::handle:vertical { background: #343943; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #454B57; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip {
    background: #22252C;
    color: #E9E4DC;
    border: 1px solid #4A4F5A;
    border-radius: 6px;
    padding: 6px;
}
"""

LIGHT_OVERRIDES = """
* { color: #2B2F36; }
QMainWindow, QDialog, QWidget#Root { background: #EDEAE3; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }
QFrame#Header {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(252, 250, 246, 250), stop:1 rgba(246, 243, 237, 250));
    border-bottom-color: #DFD9CF;
}
QFrame#Card, QFrame#JobCard {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #FBFAF7, stop:1 #F7F5F0);
    border-color: #DFD9CE;
}
QFrame#Card:hover { border-color: #CFC8BC; }
QFrame#DropZone { background: #FBF8F1; border-color: #C39A6D; }
QFrame#DropZone:hover { border-color: #A97B47; }
QFrame#DropZone[dragActive="true"] { background: #FFF1DE; border-color: #C87536; }
QFrame#Thumb, QFrame#ComparePane { background: #ECE9E2; border-color: #D5D0C6; }
QFrame#BatchPanel { background: #FBFAF7; border-color: #E4CBA8; }
QFrame#BatchTile { background: #F7F4EE; border-color: #E0DAD0; }
QFrame#BatchTile:hover { border-color: #D2B08A; }
QFrame#BatchTile[active="true"] { border-color: #C87536; background: #FFF4E6; }
QFrame#BatchTile[state="done"] { border-color: #B7DDC8; }
QFrame#BatchTile[state="failed"] { border-color: #E8B4B4; }
QLabel#TileThumb { background: #ECE9E2; color: #A2A7AF; }
QLabel#TileName { color: #1D2127; }
QLabel#TileStatus { background: #ECE9E2; color: #6C727D; }
QLabel#TileStatus[state="running"] { background: #FFF2E2; color: #A85C28; }
QLabel#TileStatus[state="done"] { background: #EAF7EF; color: #25784E; }
QLabel#TileStatus[state="failed"] { background: #FCEAEA; color: #B23B3B; }
QScrollBar::handle:horizontal { background: #D5D0C6; }
QFrame#Divider { background: #E3DFD7; }
QLabel#Brand, QLabel#PageTitle, QLabel#CardTitle, QLabel#SourceName { color: #1D2127; }
QLabel#Muted, QLabel#CardSubtitle, QLabel#Hint, QLabel#Meta { color: #646A74; }
QLabel#Footnote { color: #828892; }
QLabel#AccentText { color: #B26327; }
QLabel#SuccessText { color: #27845A; }
QLabel#ValueBadge { background: #FFF2E2; border-color: #E4BE91; color: #A85C28; }
QLabel#StatusPill { background: #EAF7EF; border-color: #B7DDC8; color: #25784E; }
QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #FDFCFA, stop:1 #F2EFE9);
    border-color: #D9D4CB;
    color: #2D3138;
}
QPushButton:hover { background: #ECE8E1; border-color: #C4BEB3; color: #14171B; }
QPushButton:pressed { background: #E2DED6; }
QPushButton:disabled { color: #A7ABB1; background: #F3F1ED; border-color: #E4E0D9; }
QPushButton#Primary {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #D98A4C, stop:1 #C06C30);
    border-color: #DD9558;
    color: #FFFFFF;
}
QPushButton#Primary:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #E0965A, stop:1 #CA7839);
    border-color: #E8A468;
}
QPushButton#Primary:pressed { background: #B5652C; }
QPushButton#Soft { background: #FFF3E5; border-color: #DEB98F; color: #A95D29; }
QPushButton#Soft:hover { background: #FFEAD3; }
QPushButton#Danger { color: #B84B4B; border-color: #E3BABA; }
QPushButton#Danger:hover { background: #FBEFEF; }
QPushButton#Pill { background: #F6F3EE; border-color: #DED9D0; color: #5F6570; }
QPushButton#Pill:hover { color: #1F2329; border-color: #C2BCB1; background: #EFEBE4; }
QPushButton#Pill:checked {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #FFF1DE, stop:1 #FFE7CB);
    border-color: #C87536;
    color: #A0521F;
    font-weight: 700;
}
QLineEdit, QComboBox { background: #FFFFFF; border-color: #D9D4CB; color: #252930; }
QLineEdit:focus, QComboBox:focus { border-color: #C87536; }
QComboBox QAbstractItemView { background: #FFFFFF; border-color: #D9D4CB; selection-background-color: #F7E1CB; }
QCheckBox { color: #3B4048; }
QCheckBox::indicator { border-color: #B8B3AA; background: #FFFFFF; }
QCheckBox::indicator:hover { border-color: #C87536; }
QCheckBox::indicator:checked {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #D98A4C, stop:1 #C06C30);
    border-color: #C87536;
}
QSlider::groove:horizontal { background: #DEDAD2; }
QSlider::sub-page:horizontal { background: #C87536; }
QSlider::handle:horizontal { background: #D98A4D; border-color: #F6D8B6; }
QSlider::handle:horizontal:hover { background: #E89B5E; }
QProgressBar { background: #E5E1DA; }
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #C87536, stop:1 #DE9558);
}
QScrollBar::handle:vertical { background: #C7C2BA; }
QScrollBar::handle:vertical:hover { background: #B4AFA6; }
QToolTip { background: #FFFFFF; color: #252930; border-color: #CFC9C0; }
"""


def style_for_theme(theme: str) -> str:
    return STYLE + LIGHT_OVERRIDES if theme == "light" else STYLE


def app_icon(name: str) -> QIcon:
    return QIcon(str(ROOT / "assets" / "icons" / f"{name}.svg"))


# ---------------------------------------------------------------- motion ---
# Small, calm animations: nothing faster than ~180 ms, nothing bouncy.

FONTS_DIR = ROOT / "assets" / "fonts"


def load_fonts() -> None:
    for font_file in sorted(FONTS_DIR.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(font_file))


def _keep(widget: QObject, animation: QAbstractAnimation) -> QAbstractAnimation:
    """Hold a reference on the widget so the animation is not collected mid-flight."""
    running = getattr(widget, "_vc_animations", None)
    if running is None:
        running = set()
        setattr(widget, "_vc_animations", running)
    running.add(animation)
    animation.finished.connect(lambda: running.discard(animation))
    return animation


def fade_in(widget: QWidget, duration: int = 420, delay: int = 0, start: float = 0.0) -> None:
    """Fade a widget in, restoring any drop shadow it had once the fade ends."""
    previous = widget.graphicsEffect()
    shadow = None
    if isinstance(previous, QGraphicsDropShadowEffect):
        shadow = (previous.blurRadius(), previous.offset(), previous.color())
    elif previous is not None:
        return
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(start)
    widget.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration)
    animation.setStartValue(start)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def restore() -> None:
        if shadow:
            restored = QGraphicsDropShadowEffect(widget)
            restored.setBlurRadius(shadow[0])
            restored.setOffset(shadow[1])
            restored.setColor(shadow[2])
            widget.setGraphicsEffect(restored)
        else:
            widget.setGraphicsEffect(None)

    animation.finished.connect(restore)
    _keep(widget, animation)
    QTimer.singleShot(delay, lambda: animation.start())


def reveal(widget: QWidget, duration: int = 320) -> None:
    """Grow a hidden widget open from zero height, then fade its contents in."""
    if widget.isVisible():
        return
    widget.setMaximumHeight(0)
    widget.show()
    target = max(1, widget.sizeHint().height())
    animation = QPropertyAnimation(widget, b"maximumHeight", widget)
    animation.setDuration(duration)
    animation.setStartValue(0)
    animation.setEndValue(target)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    animation.finished.connect(lambda: widget.setMaximumHeight(16777215))
    _keep(widget, animation)
    animation.start()
    fade_in(widget, duration + 120)


def collapse(widget: QWidget, duration: int = 240) -> None:
    if not widget.isVisible():
        return
    animation = QPropertyAnimation(widget, b"maximumHeight", widget)
    animation.setDuration(duration)
    animation.setStartValue(widget.height())
    animation.setEndValue(0)
    animation.setEasingCurve(QEasingCurve.Type.InCubic)

    def done() -> None:
        widget.hide()
        widget.setMaximumHeight(16777215)

    animation.finished.connect(done)
    _keep(widget, animation)
    animation.start()


class SmoothProgressBar(QProgressBar):
    """Glides between values instead of jumping."""

    def __init__(self) -> None:
        super().__init__()
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(380)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(lambda v: QProgressBar.setValue(self, int(v)))

    def setValue(self, value: int) -> None:  # type: ignore[override]
        self._animation.stop()
        if self.maximum() <= self.minimum() or not self.isVisible() or value < self.value():
            QProgressBar.setValue(self, value)
            return
        self._animation.setStartValue(max(self.value(), self.minimum()))
        self._animation.setEndValue(value)
        self._animation.start()

    def setRange(self, minimum: int, maximum: int) -> None:  # type: ignore[override]
        self._animation.stop()
        QProgressBar.setRange(self, minimum, maximum)


class HoverGlow(QObject):
    """Soft warm halo that eases in under important buttons on hover."""

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        if isinstance(obj, QPushButton) and event.type() in (QEvent.Type.Enter, QEvent.Type.Leave):
            if not obj.isEnabled():
                return False
            effect = obj.graphicsEffect()
            if not isinstance(effect, QGraphicsDropShadowEffect):
                if effect is not None:
                    return False
                effect = QGraphicsDropShadowEffect(obj)
                effect.setOffset(0, 4)
                effect.setBlurRadius(0)
                effect.setColor(QColor(224, 154, 84, 0))
                obj.setGraphicsEffect(effect)
            entering = event.type() == QEvent.Type.Enter
            animation = QVariantAnimation(obj)
            animation.setDuration(220 if entering else 300)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.setStartValue(effect.blurRadius() / 28.0)
            animation.setEndValue(1.0 if entering else 0.0)

            def step(value, effect=effect) -> None:
                t = float(value)
                effect.setBlurRadius(28 * t)
                effect.setColor(QColor(224, 154, 84, int(95 * t)))

            animation.valueChanged.connect(step)
            _keep(obj, animation)
            animation.start()
        return False


def install_hover_glow(root: QWidget) -> None:
    glow = HoverGlow(root)
    for button in root.findChildren(QPushButton):
        if button.objectName() in {"Primary", "Soft"}:
            button.installEventFilter(glow)


def crossfade_snapshot(window: QWidget, duration: int = 360) -> None:
    """Freeze the current look on top of the window and fade it away."""
    snapshot = QLabel(window)
    snapshot.setPixmap(window.grab())
    snapshot.setGeometry(window.rect())
    snapshot.show()
    snapshot.raise_()
    effect = QGraphicsOpacityEffect(snapshot)
    snapshot.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", snapshot)
    animation.setDuration(duration)
    animation.setStartValue(1.0)
    animation.setEndValue(0.0)
    animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
    animation.finished.connect(snapshot.deleteLater)
    _keep(snapshot, animation)
    animation.start()


def setting_bool(value, default: bool = True) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def set_margins(layout: QVBoxLayout | QHBoxLayout, value: int) -> None:
    layout.setContentsMargins(value, value, value, value)


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    return line


def icon_heading(text: str, icon_name: str) -> QWidget:
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    icon_label = QLabel()
    icon_label.setPixmap(app_icon(icon_name).pixmap(19, 19))
    title = QLabel(text)
    title.setStyleSheet("font-weight: 650;")
    layout.addWidget(icon_label)
    layout.addWidget(title)
    layout.addStretch()
    return widget


def card(title: str, subtitle: str = "", icon_name: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    set_margins(layout, 20)
    layout.setSpacing(14)
    title_row = QHBoxLayout()
    title_row.setSpacing(9)
    if icon_name:
        icon_label = QLabel()
        icon_label.setPixmap(app_icon(icon_name).pixmap(22, 22))
        title_row.addWidget(icon_label)
    title_label = QLabel(title)
    title_label.setObjectName("CardTitle")
    title_row.addWidget(title_label)
    title_row.addStretch()
    layout.addLayout(title_row)
    if subtitle:
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("CardSubtitle")
        subtitle_label.setWordWrap(True)
        layout.addWidget(subtitle_label)
    return frame, layout


class BackgroundWidget(QWidget):
    def __init__(self, theme: str = "dark") -> None:
        super().__init__()
        self.theme = theme
        self.setObjectName("Root")
        self.background = QPixmap(str(ROOT / "assets" / "studio_background.png"))

        self._cache: QPixmap | None = None
        self._cache_key: tuple | None = None

    def set_theme(self, theme: str) -> None:
        self.theme = theme
        self._cache = None
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        key = (self.width(), self.height(), self.theme)
        if self._cache is None or self._cache_key != key:
            self._cache = self._compose()
            self._cache_key = key
        painter = QPainter(self)
        painter.drawPixmap(event.rect(), self._cache, event.rect())
        painter.end()

    def _compose(self) -> QPixmap:
        canvas = QPixmap(max(1, self.width()), max(1, self.height()))
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        base = QColor("#EDEAE3" if self.theme == "light" else "#121419")
        painter.fillRect(self.rect(), base)
        if not self.background.isNull():
            scaled = self.background.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (scaled.width() - self.width()) // 2
            y = (scaled.height() - self.height()) // 2
            painter.setOpacity(0.06 if self.theme == "light" else 0.10)
            painter.drawPixmap(0, 0, scaled, x, y, self.width(), self.height())
            painter.setOpacity(1.0)
        # A soft warm glow bleeding down from the top gives the studio depth
        # without competing with the cards.
        glow = QLinearGradient(0, 0, 0, int(self.height() * 0.55))
        if self.theme == "light":
            glow.setColorAt(0.0, QColor(232, 172, 108, 26))
            glow.setColorAt(1.0, QColor(232, 172, 108, 0))
        else:
            glow.setColorAt(0.0, QColor(216, 144, 75, 30))
            glow.setColorAt(1.0, QColor(216, 144, 75, 0))
        painter.fillRect(self.rect(), glow)
        wash = QLinearGradient(0, 0, self.width(), self.height())
        if self.theme == "light":
            wash.setColorAt(0, QColor(255, 255, 255, 208))
            wash.setColorAt(1, QColor(242, 240, 235, 224))
        else:
            wash.setColorAt(0, QColor(18, 20, 25, 196))
            wash.setColorAt(1, QColor(18, 20, 25, 228))
        painter.fillRect(self.rect(), wash)
        painter.end()
        return canvas


class ScrollSafeSlider(QSlider):
    """A settings slider that lets the surrounding page own the mouse wheel."""

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        event.ignore()


class ScrollSafeComboBox(QComboBox):
    """Prevents accidental selection changes while the settings page scrolls."""

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


BATCH_STATUS_TEXT = {
    "waiting": "في الانتظار",
    "running": "جارٍ…",
    "done": "تم ✓",
    "failed": "تعذّر",
}


class BatchTile(QFrame):
    clicked = pyqtSignal(int)
    remove_requested = pyqtSignal(int)

    def __init__(self, index: int, path: str) -> None:
        super().__init__()
        self.index = index
        self.path = path
        self.setObjectName("BatchTile")
        self.setFixedSize(150, 142)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(path)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 7)
        layout.setSpacing(5)

        self.thumb = QLabel("…")
        self.thumb.setObjectName("TileThumb")
        self.thumb.setFixedSize(136, 78)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.thumb)

        self.index_badge = QLabel(str(index + 1), self.thumb)
        self.index_badge.setObjectName("TileIndex")
        self.index_badge.move(5, 5)
        self.remove_button = QPushButton("×", self.thumb)
        self.remove_button.setObjectName("TileRemove")
        self.remove_button.setToolTip("إزالة من المجموعة")
        self.remove_button.move(136 - 23, 5)
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self.index))

        name = QLabel()
        name.setObjectName("TileName")
        name.setText(name.fontMetrics().elidedText(Path(path).name, Qt.TextElideMode.ElideMiddle, 136))
        layout.addWidget(name)

        self.status = QLabel()
        self.status.setObjectName("TileStatus")
        layout.addWidget(self.status, 0, Qt.AlignmentFlag.AlignLeft)
        self.set_state("waiting")

    def set_thumbnail(self, pixmap: QPixmap | None) -> None:
        if pixmap and not pixmap.isNull():
            self.thumb.setPixmap(
                pixmap.scaled(
                    136, 78,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                ).copy(0, 0, 136, 78)
            )
        else:
            self.thumb.setText("▶")

    def set_state(self, state: str, detail: str = "") -> None:
        if self.property("state") not in (None, state):
            fade_in(self.status, 300, 0, 0.25)
        self.setProperty("state", state)
        self.status.setProperty("state", state)
        self.status.setText(detail or BATCH_STATUS_TEXT.get(state, state))
        repolish(self)
        repolish(self.status)
        self.status.adjustSize()
        self.status.updateGeometry()

    def set_active(self, active: bool) -> None:
        self.setProperty("active", active)
        repolish(self)

    def set_locked(self, locked: bool) -> None:
        self.remove_button.setVisible(not locked)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)
        super().mousePressEvent(event)


class BatchPanel(QFrame):
    tile_clicked = pyqtSignal(int)
    remove_requested = pyqtSignal(int)
    clear_requested = pyqtSignal()
    open_folder_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("BatchPanel")
        self.tiles: list[BatchTile] = []
        self.kind = "ملفات"
        self._thumb_queue: list[BatchTile] = []
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.timeout.connect(self._next_thumbnail)

        layout = QVBoxLayout(self)
        set_margins(layout, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(9)
        icon = QLabel()
        icon.setPixmap(app_icon("file").pixmap(20, 20))
        self.title = QLabel("المجموعة")
        self.title.setObjectName("CardTitle")
        self.summary = QLabel()
        self.summary.setObjectName("CardSubtitle")
        self.count_badge = QLabel()
        self.count_badge.setObjectName("ValueBadge")
        self.count_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(icon)
        header.addWidget(self.title)
        header.addWidget(self.count_badge)
        header.addSpacing(6)
        header.addWidget(self.summary)
        header.addStretch()
        self.open_button = QPushButton("فتح المجلد")
        self.open_button.setObjectName("Soft")
        self.open_button.setIcon(app_icon("folder"))
        self.open_button.clicked.connect(self.open_folder_requested.emit)
        self.open_button.hide()
        self.clear_button = QPushButton("مسح المجموعة")
        self.clear_button.setObjectName("Soft")
        self.clear_button.setIcon(app_icon("cancel"))
        self.clear_button.clicked.connect(self.clear_requested.emit)
        header.addWidget(self.open_button)
        header.addWidget(self.clear_button)
        layout.addLayout(header)

        self.progress = SmoothProgressBar()
        self.progress.setObjectName("BatchProgress")
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.strip = QScrollArea()
        self.strip.setObjectName("BatchStrip")
        self.strip.setWidgetResizable(True)
        self.strip.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.strip.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.strip.setFixedHeight(162)
        holder = QWidget()
        self.tile_row = QHBoxLayout(holder)
        self.tile_row.setContentsMargins(0, 0, 0, 0)
        self.tile_row.setSpacing(10)
        self.tile_row.addStretch()
        self.strip.setWidget(holder)
        layout.addWidget(self.strip)
        self.hide()

    def set_items(self, paths: list[str], is_image: bool) -> None:
        for tile in self.tiles:
            tile.deleteLater()
        self.tiles = []
        for index, path in enumerate(paths):
            tile = BatchTile(index, path)
            tile.clicked.connect(self.tile_clicked.emit)
            tile.remove_requested.connect(self.remove_requested.emit)
            self.tile_row.insertWidget(index, tile)
            self.tiles.append(tile)
        self.kind = "صور" if is_image else "فيديوهات"
        self.title.setText("مجموعة " + self.kind)
        self.count_badge.setText(str(len(paths)))
        self.progress.setRange(0, max(1, len(paths)))
        self.progress.setValue(0)
        self.open_button.hide()
        self.summary.setText("نفس الإعدادات تُطبّق على الكل  •  اضغط أي ملف لمعاينته")
        self.set_locked(False)
        self._thumb_queue = list(self.tiles)
        self._thumb_timer.start(30)
        for order, tile in enumerate(self.tiles[:14]):
            fade_in(tile, 360, 120 + order * 45)
        if paths:
            reveal(self)
        else:
            collapse(self)

    def _next_thumbnail(self) -> None:
        if not self._thumb_queue:
            return
        tile = self._thumb_queue.pop(0)
        try:
            tile.set_thumbnail(quick_thumbnail(tile.path))
        except RuntimeError:
            pass  # tile was deleted while queued
        if self._thumb_queue:
            self._thumb_timer.start(10)

    def set_active(self, index: int) -> None:
        for tile in self.tiles:
            tile.set_active(tile.index == index)
        if 0 <= index < len(self.tiles):
            self.strip.ensureWidgetVisible(self.tiles[index], 20, 0)

    def set_state(self, index: int, state: str, detail: str = "") -> None:
        if 0 <= index < len(self.tiles):
            self.tiles[index].set_state(state, detail)

    def set_locked(self, locked: bool) -> None:
        self.clear_button.setEnabled(not locked)
        for tile in self.tiles:
            tile.set_locked(locked)

    def set_progress(self, done: int, failed: int, running: bool) -> None:
        total = len(self.tiles)
        self.progress.setValue(done + failed)
        if running:
            self.summary.setText(f"يُصدَّر الآن  •  اكتمل {done} من {total}" + (f"  •  تعذّر {failed}" if failed else ""))
        else:
            self.summary.setText(f"اكتمل {done} من {total}" + (f"  •  تعذّر {failed}" if failed else "  •  كل الملفات جاهزة"))


def quick_thumbnail(path: str) -> QPixmap | None:
    if Path(path).suffix.lower() in IMAGE_SUFFIXES:
        pixmap = QPixmap(path)
        return None if pixmap.isNull() else pixmap.scaled(
            280, 160, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    cache_dir = Path(tempfile.gettempdir()) / "VideoCraftStudio" / "batch_thumbs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    thumb = cache_dir / f"{abs(hash(path))}.jpg"
    if not thumb.exists():
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", "1", "-i", path,
            "-frames:v", "1", "-vf", "scale=280:160:force_original_aspect_ratio=increase",
            "-q:v", "4", str(thumb),
        ]
        try:
            subprocess.run(
                command, capture_output=True, timeout=12, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
    return QPixmap(str(thumb)) if thumb.exists() else None


class DropZone(QFrame):
    file_chosen = pyqtSignal(str)
    files_chosen = pyqtSignal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setProperty("dragActive", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        set_margins(layout, 18)
        layout.setSpacing(18)

        self.thumb_frame = QFrame()
        self.thumb_frame.setObjectName("Thumb")
        self.thumb_frame.setFixedSize(214, 122)
        thumb_layout = QVBoxLayout(self.thumb_frame)
        set_margins(thumb_layout, 3)
        self.thumbnail = QLabel("＋  فيديو أو صورة")
        self.thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail.setObjectName("Muted")
        thumb_layout.addWidget(self.thumbnail)
        layout.addWidget(self.thumb_frame)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(6)
        self.title = QLabel("اسحب الفيديو أو الصورة هنا، أو اخترها من جهازك")
        self.title.setObjectName("SourceName")
        self.description = QLabel(
            "MP4، MOV، MKV، WebM — وأيضًا JPG، PNG، WebP — تتم المعالجة على جهازك فقط"
        )
        self.description.setObjectName("Muted")
        self.description.setWordWrap(True)
        self.meta = QLabel("لم يتم اختيار ملف بعد")
        self.meta.setObjectName("AccentText")
        text_layout.addStretch()
        text_layout.addWidget(self.title)
        text_layout.addWidget(self.description)
        text_layout.addWidget(self.meta)
        text_layout.addStretch()
        layout.addLayout(text_layout, 1)

        self.select_button = QPushButton("اختيار ملف")
        self.select_button.setObjectName("Soft")
        self.select_button.setIcon(app_icon("upload"))
        self.select_button.clicked.connect(self.choose_file)
        layout.addWidget(self.select_button, 0, Qt.AlignmentFlag.AlignVCenter)

    def choose_file(self) -> None:
        images = " ".join(f"*{suffix}" for suffix in sorted(IMAGE_SUFFIXES))
        videos = "*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.mts *.m2ts *.flv"
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "اختر فيديو/صورة أو مجموعة ملفات",
            str(Path.home() / "Videos"),
            f"الفيديو والصور ({videos} {images});;"
            f"ملفات الفيديو ({videos});;"
            f"ملفات الصور ({images});;"
            "كل الملفات (*.*)",
        )
        if paths:
            self.files_chosen.emit(paths)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and not self.select_button.underMouse():
            self.choose_file()
        super().mousePressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
            self.setProperty("dragActive", True)
            self.style().unpolish(self)
            self.style().polish(self)

    def dragLeaveEvent(self, event) -> None:  # type: ignore[override]
        self.setProperty("dragActive", False)
        self.style().unpolish(self)
        self.style().polish(self)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self.setProperty("dragActive", False)
        self.style().unpolish(self)
        self.style().polish(self)
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.files_chosen.emit(paths)
            event.acceptProposedAction()

    def show_media(self, info: MediaInfo, thumbnail: QPixmap | None) -> None:
        name = Path(info.path).name
        self.title.setText(name)
        if info.is_image:
            self.description.setText(
                f"صورة  •  {info.display_width}×{info.display_height}  •  "
                f"{format_size(info.size_bytes)}"
            )
        else:
            self.description.setText(
                f"{info.display_width}×{info.display_height}  •  {info.fps:.2f} fps  •  "
                f"{format_duration(info.duration)}  •  {format_size(info.size_bytes)}"
            )
        self.meta.setText("جاهز للضبط والمعالجة")
        self.select_button.setText("تغيير الصورة" if info.is_image else "تغيير الفيديو")
        if thumbnail and not thumbnail.isNull():
            self.thumbnail.setPixmap(
                thumbnail.scaled(
                    208,
                    116,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            self.thumbnail.setText("")
            fade_in(self.thumbnail, 380)


class SliderRow(QWidget):
    value_changed = pyqtSignal(int)

    def __init__(
        self,
        title: str,
        hint: str,
        minimum: int,
        maximum: int,
        value: int,
        *,
        signed: bool = False,
        suffix: str = "%",
        icon_name: str | None = None,
    ) -> None:
        super().__init__()
        self.signed = signed
        self.suffix = suffix
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(7)
        top = QHBoxLayout()
        if icon_name:
            icon_label = QLabel()
            icon_label.setPixmap(app_icon(icon_name).pixmap(18, 18))
            top.addWidget(icon_label)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-weight: 600;")
        self.value_label = QLabel()
        self.value_label.setObjectName("ValueBadge")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(title_label)
        top.addStretch()
        top.addWidget(self.value_label)
        layout.addLayout(top)
        hint_label = QLabel(hint)
        hint_label.setObjectName("Hint")
        hint_label.setWordWrap(True)
        layout.addWidget(hint_label)
        self.slider = ScrollSafeSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(minimum, maximum)
        self.slider.setValue(value)
        self.slider.valueChanged.connect(self._changed)
        layout.addWidget(self.slider)
        self._changed(value)

    def _changed(self, value: int) -> None:
        sign = "+" if self.signed and value > 0 else ""
        self.value_label.setText(f"{sign}{value}{self.suffix}")
        self.value_changed.emit(value)

    def value(self) -> int:
        return self.slider.value()

    def setValue(self, value: int) -> None:
        self.slider.setValue(value)


class PreviewDialog(QDialog):
    def __init__(self, path: str, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(980, 650)
        self.setMinimumSize(720, 480)

        layout = QVBoxLayout(self)
        set_margins(layout, 14)
        layout.setSpacing(10)
        self.video = QVideoWidget()
        self.video.setStyleSheet("background: #070809; border-radius: 14px;")
        layout.addWidget(self.video, 1)

        controls = QHBoxLayout()
        self.play_button = QPushButton("إيقاف مؤقت")
        self.play_button.setObjectName("Soft")
        self.play_button.setIcon(app_icon("pause"))
        self.play_button.clicked.connect(self.toggle_playback)
        controls.addWidget(self.play_button)
        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(0, 0)
        self.timeline.sliderMoved.connect(self.seek)
        controls.addWidget(self.timeline, 1)
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setObjectName("Meta")
        controls.addWidget(self.time_label)
        layout.addLayout(controls)

        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.75)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self.update_position)
        self.player.durationChanged.connect(self.update_duration)
        self.player.playbackStateChanged.connect(self.update_play_button)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()

    def toggle_playback(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def update_play_button(self, state: QMediaPlayer.PlaybackState) -> None:
        self.play_button.setText(
            "إيقاف مؤقت" if state == QMediaPlayer.PlaybackState.PlayingState else "تشغيل"
        )
        self.play_button.setIcon(
            app_icon("pause")
            if state == QMediaPlayer.PlaybackState.PlayingState
            else app_icon("play")
        )

    def update_duration(self, duration: int) -> None:
        self.timeline.setRange(0, duration)
        self.update_position(self.player.position())

    def update_position(self, position: int) -> None:
        if not self.timeline.isSliderDown():
            self.timeline.setValue(position)
        self.time_label.setText(
            f"{format_duration(position / 1000)} / {format_duration(self.player.duration() / 1000)}"
        )

    def seek(self, position: int) -> None:
        self.player.setPosition(position)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.player.stop()
        super().closeEvent(event)


class SideBySideCompareDialog(QDialog):
    """Synchronized before/after playback with a draggable center divider."""

    def __init__(
        self,
        original_path: str,
        enhanced_path: str,
        parent: QWidget | None = None,
        *,
        original_offset_ms: int = 0,
        preview_comparison: bool = False,
    ) -> None:
        super().__init__(parent)
        self.original_offset_ms = max(0, int(original_offset_ms))
        self.setWindowTitle(
            "مقارنة معاينة الست ثواني" if preview_comparison else "مقارنة قبل وبعد"
        )
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(1180, 720)
        self.setMinimumSize(820, 520)

        layout = QVBoxLayout(self)
        set_margins(layout, 16)
        layout.setSpacing(11)

        heading = QHBoxLayout()
        heading_text = QVBoxLayout()
        heading_text.setSpacing(2)
        title = QLabel("قبل وبعد — في نفس اللحظة")
        title.setObjectName("CardTitle")
        note = QLabel(
            "يعمل القديم والجديد معًا. يتحرك الخط عند الفتح، وبعدها يمكنك سحبه بنفسك."
        )
        note.setObjectName("Hint")
        heading_text.addWidget(title)
        heading_text.addWidget(note)
        heading.addLayout(heading_text)
        heading.addStretch()
        self.sync_status = QLabel("●  متزامن")
        self.sync_status.setObjectName("StatusPill")
        heading.addWidget(self.sync_status)
        layout.addLayout(heading)

        labels = QHBoxLayout()
        labels.setDirection(QHBoxLayout.Direction.LeftToRight)
        before_label = QLabel("الأصلي — قبل")
        before_label.setObjectName("Muted")
        before_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        after_label = QLabel("النسخة المحسّنة — بعد")
        after_label.setObjectName("AccentText")
        after_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        labels.addWidget(before_label, 1)
        labels.addSpacing(4)
        labels.addWidget(after_label, 1)
        layout.addLayout(labels)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("CompareSplitter")
        self.splitter.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.splitter.setHandleWidth(4)
        self.splitter.setChildrenCollapsible(False)

        before_pane = QFrame()
        before_pane.setObjectName("ComparePane")
        before_layout = QVBoxLayout(before_pane)
        before_layout.setContentsMargins(3, 3, 3, 3)
        self.original_video = QVideoWidget()
        self.original_video.setStyleSheet("background: #050607;")
        before_layout.addWidget(self.original_video)

        after_pane = QFrame()
        after_pane.setObjectName("ComparePane")
        after_layout = QVBoxLayout(after_pane)
        after_layout.setContentsMargins(3, 3, 3, 3)
        self.enhanced_video = QVideoWidget()
        self.enhanced_video.setStyleSheet("background: #050607;")
        after_layout.addWidget(self.enhanced_video)

        self.splitter.addWidget(before_pane)
        self.splitter.addWidget(after_pane)
        self.splitter.setSizes([330, 830])
        layout.addWidget(self.splitter, 1)

        controls = QHBoxLayout()
        self.play_button = QPushButton("إيقاف مؤقت")
        self.play_button.setObjectName("Soft")
        self.play_button.setIcon(app_icon("pause"))
        self.play_button.clicked.connect(self.toggle_playback)
        controls.addWidget(self.play_button)
        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(0, 0)
        self.timeline.sliderMoved.connect(self.seek_both)
        controls.addWidget(self.timeline, 1)
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setObjectName("Meta")
        controls.addWidget(self.time_label)
        layout.addLayout(controls)

        self.original_audio = QAudioOutput(self)
        self.original_audio.setVolume(0.0)
        self.enhanced_audio = QAudioOutput(self)
        self.enhanced_audio.setVolume(0.78)

        self.original_player = QMediaPlayer(self)
        self.original_player.setAudioOutput(self.original_audio)
        self.original_player.setVideoOutput(self.original_video)
        self.original_player.setSource(QUrl.fromLocalFile(original_path))

        self.enhanced_player = QMediaPlayer(self)
        self.enhanced_player.setAudioOutput(self.enhanced_audio)
        self.enhanced_player.setVideoOutput(self.enhanced_video)
        self.enhanced_player.positionChanged.connect(self.update_position)
        self.enhanced_player.durationChanged.connect(self.update_duration)
        self.enhanced_player.playbackStateChanged.connect(self.update_play_button)
        self.enhanced_player.setSource(QUrl.fromLocalFile(enhanced_path))
        QTimer.singleShot(180, self.play_both)
        self.divider_animation = QVariantAnimation(self)
        self.divider_animation.setDuration(1450)
        self.divider_animation.setStartValue(0.28)
        self.divider_animation.setKeyValueAt(0.48, 0.70)
        self.divider_animation.setEndValue(0.50)
        self.divider_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.divider_animation.valueChanged.connect(self.animate_divider)
        QTimer.singleShot(260, self.divider_animation.start)

    def animate_divider(self, value) -> None:
        total = max(2, sum(self.splitter.sizes()))
        before_width = max(1, int(total * float(value)))
        self.splitter.setSizes([before_width, max(1, total - before_width)])

    def play_both(self) -> None:
        position = self.enhanced_player.position()
        if self.enhanced_player.duration() and position >= self.enhanced_player.duration() - 100:
            position = 0
            self.enhanced_player.setPosition(0)
        self.original_player.setPosition(position + self.original_offset_ms)
        self.original_player.play()
        self.enhanced_player.play()

    def toggle_playback(self) -> None:
        if self.enhanced_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.enhanced_player.pause()
            self.original_player.pause()
        else:
            self.play_both()

    def update_play_button(self, state: QMediaPlayer.PlaybackState) -> None:
        self.play_button.setText(
            "إيقاف مؤقت" if state == QMediaPlayer.PlaybackState.PlayingState else "تشغيل"
        )
        self.play_button.setIcon(
            app_icon("pause")
            if state == QMediaPlayer.PlaybackState.PlayingState
            else app_icon("play")
        )
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self.original_player.pause()

    def update_duration(self, duration: int) -> None:
        self.timeline.setRange(0, duration)
        self.update_position(self.enhanced_player.position())

    def update_position(self, position: int) -> None:
        if not self.timeline.isSliderDown():
            self.timeline.setValue(position)
        original_position = position + self.original_offset_ms
        if abs(self.original_player.position() - original_position) > 160:
            self.original_player.setPosition(original_position)
        self.time_label.setText(
            f"{format_duration(position / 1000)} / "
            f"{format_duration(self.enhanced_player.duration() / 1000)}"
        )

    def seek_both(self, position: int) -> None:
        self.original_player.setPosition(position + self.original_offset_ms)
        self.enhanced_player.setPosition(position)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.original_player.stop()
        self.enhanced_player.stop()
        super().closeEvent(event)


class WipeComparisonCanvas(QWidget):
    """Paints two synchronized video frames in one rect with a draggable wipe edge."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CompareCanvas")
        self.setMinimumSize(720, 405)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.SplitHCursor)
        self.before_image = QImage()
        self.after_image = QImage()
        self.split_ratio = 0.5
        self.dragging = False

    def set_before_frame(self, frame: QVideoFrame) -> None:
        image = frame.toImage()
        if not image.isNull():
            self.before_image = image.copy()
            self.update()

    def set_after_frame(self, frame: QVideoFrame) -> None:
        image = frame.toImage()
        if not image.isNull():
            self.after_image = image.copy()
            self.update()

    def set_split_ratio(self, value) -> None:
        self.split_ratio = max(0.025, min(0.975, float(value)))
        self.update()

    def center_divider(self) -> None:
        self.set_split_ratio(0.5)

    def _video_rect(self) -> QRect:
        image = self.before_image if not self.before_image.isNull() else self.after_image
        if image.isNull() or image.width() <= 0 or image.height() <= 0:
            return self.rect().adjusted(2, 2, -2, -2)
        available = self.size()
        scaled = image.size().scaled(available, Qt.AspectRatioMode.KeepAspectRatio)
        left = (self.width() - scaled.width()) // 2
        top = (self.height() - scaled.height()) // 2
        return QRect(left, top, scaled.width(), scaled.height())

    def _draw_badge(self, painter: QPainter, rect: QRect, text: str, accent: bool) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(205, 119, 59, 225) if accent else QColor(12, 15, 19, 215))
        painter.drawRoundedRect(rect, 10, 10)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        painter.fillRect(self.rect(), QColor("#050709"))
        target = self._video_rect()

        if self.before_image.isNull() and self.after_image.isNull():
            painter.setPen(QColor("#AEB3BC"))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "جاري تجهيز إطارات المقارنة…",
            )
            painter.end()
            return

        if not self.before_image.isNull():
            painter.drawImage(target, self.before_image)
        elif not self.after_image.isNull():
            painter.drawImage(target, self.after_image)

        edge_x = target.left() + int(target.width() * self.split_ratio)
        if not self.after_image.isNull():
            painter.save()
            painter.setClipRect(
                QRect(edge_x, target.top(), max(1, target.right() - edge_x + 1), target.height())
            )
            painter.drawImage(target, self.after_image)
            painter.restore()

        painter.setPen(QPen(QColor(216, 144, 75, 65), 11))
        painter.drawLine(edge_x, target.top(), edge_x, target.bottom())
        painter.setPen(QPen(QColor("#F0A45C"), 3))
        painter.drawLine(edge_x, target.top(), edge_x, target.bottom())

        center_y = target.center().y()
        handle_rect = QRect(edge_x - 21, center_y - 21, 42, 42)
        painter.setPen(QPen(QColor("#FFD6AA"), 2))
        painter.setBrush(QColor("#C87536"))
        painter.drawEllipse(handle_rect)
        painter.setPen(QPen(QColor("#FFFFFF"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(edge_x - 10, center_y, edge_x + 10, center_y)
        painter.drawLine(edge_x - 10, center_y, edge_x - 6, center_y - 4)
        painter.drawLine(edge_x - 10, center_y, edge_x - 6, center_y + 4)
        painter.drawLine(edge_x + 10, center_y, edge_x + 6, center_y - 4)
        painter.drawLine(edge_x + 10, center_y, edge_x + 6, center_y + 4)

        self._draw_badge(
            painter,
            QRect(target.left() + 16, target.top() + 16, 108, 34),
            "قبل • الأصلي",
            False,
        )
        self._draw_badge(
            painter,
            QRect(target.right() - 124, target.top() + 16, 108, 34),
            "بعد • الجديد",
            True,
        )
        painter.setPen(QPen(QColor(255, 255, 255, 36), 1))
        painter.drawRect(target.adjusted(0, 0, -1, -1))
        painter.end()

    def _set_from_x(self, x: float) -> None:
        target = self._video_rect()
        if target.width() > 0:
            self.set_split_ratio((x - target.left()) / target.width())

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self._set_from_x(event.position().x())
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self.dragging:
            self._set_from_x(event.position().x())
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        self.center_divider()
        event.accept()


class DividerOverlay(QWidget):
    ratio_changed = pyqtSignal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ratio = 0.5
        self.dragging = False
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.SplitHCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def set_ratio(self, value) -> None:
        self.ratio = max(0.025, min(0.975, float(value)))
        self.update()

    def _set_from_x(self, x: float) -> None:
        if self.width() > 0:
            value = max(0.025, min(0.975, x / self.width()))
            self.ratio_changed.emit(value)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        edge_x = int(self.width() * self.ratio)

        # A wide glow, dark outline and bright core keep the divider visible
        # over both very light and very dark footage.
        painter.setPen(QPen(QColor(240, 145, 66, 58), 19))
        painter.drawLine(edge_x, 0, edge_x, self.height())
        painter.setPen(QPen(QColor(5, 7, 9, 205), 8))
        painter.drawLine(edge_x, 0, edge_x, self.height())
        painter.setPen(QPen(QColor("#FF9F43"), 5))
        painter.drawLine(edge_x, 0, edge_x, self.height())
        painter.setPen(QPen(QColor("#FFF4E8"), 1))
        painter.drawLine(edge_x, 0, edge_x, self.height())

        center_y = self.height() // 2
        handle_rect = QRect(edge_x - 29, center_y - 29, 58, 58)
        painter.setPen(QPen(QColor(5, 7, 9, 210), 8))
        painter.setBrush(QColor("#FF963A"))
        painter.drawEllipse(handle_rect)
        painter.setPen(QPen(QColor("#FFF4E8"), 3))
        painter.drawEllipse(handle_rect)
        painter.setPen(
            QPen(
                QColor("#FFFFFF"),
                3,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )
        painter.drawLine(edge_x - 14, center_y, edge_x + 14, center_y)
        painter.drawLine(edge_x - 14, center_y, edge_x - 8, center_y - 6)
        painter.drawLine(edge_x - 14, center_y, edge_x - 8, center_y + 6)
        painter.drawLine(edge_x + 14, center_y, edge_x + 8, center_y - 6)
        painter.drawLine(edge_x + 14, center_y, edge_x + 8, center_y + 6)

        drag_badge = QRect(edge_x - 72, 15, 144, 34)
        painter.setPen(QPen(QColor("#FFD0A3"), 1))
        painter.setBrush(QColor(12, 15, 19, 235))
        painter.drawRoundedRect(drag_badge, 10, 10)
        badge_font = painter.font()
        badge_font.setBold(True)
        painter.setFont(badge_font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(drag_badge, Qt.AlignmentFlag.AlignCenter, "اسحب للمقارنة ↔")

        before_badge = QRect(16, 16, 108, 34)
        after_badge = QRect(max(16, self.width() - 124), 16, 108, 34)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(12, 15, 19, 220))
        painter.drawRoundedRect(before_badge, 10, 10)
        painter.setBrush(QColor(200, 117, 54, 230))
        painter.drawRoundedRect(after_badge, 10, 10)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(before_badge, Qt.AlignmentFlag.AlignCenter, "قبل • الأصلي")
        painter.drawText(after_badge, Qt.AlignmentFlag.AlignCenter, "بعد • الجديد")
        painter.end()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self._set_from_x(event.position().x())
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self.dragging:
            self._set_from_x(event.position().x())
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        self.ratio_changed.emit(0.5)
        event.accept()


class OverlayComparisonWidget(QGraphicsView):
    view_changed = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(720, 405)
        self.setStyleSheet(
            "QGraphicsView { background: #050709; border: 0; border-radius: 12px; }"
        )
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        self.split_ratio = 0.5
        self.zoom_factor = 1.0
        self.minimum_zoom = 1.0
        self.maximum_zoom = 6.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.drag_mode: str | None = None
        self.drag_start = QPointF()
        self.pan_start_x = 0.0
        self.pan_start_y = 0.0
        self.video_scene = QGraphicsScene(self)
        self.setScene(self.video_scene)

        self.before_video = self.create_layer()
        self.before_video.setZValue(0)
        self.video_scene.addItem(self.before_video)

        # The enhanced video is a child of a clipping item. Moving the clip's
        # left edge reveals the original without resizing either video frame.
        self.after_clip = QGraphicsRectItem()
        self.after_clip.setPen(QPen(Qt.PenStyle.NoPen))
        self.after_clip.setBrush(QColor(0, 0, 0, 0))
        self.after_clip.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape,
            True,
        )
        self.after_clip.setZValue(1)
        self.video_scene.addItem(self.after_clip)
        self.after_video = self.create_layer(self.after_clip)
        QTimer.singleShot(0, self._layout_layers)

    def create_layer(self, parent: QGraphicsItem | None = None):
        """The comparison chrome is identical for video and stills; only the
        layer type and how it is sized differ."""
        item = QGraphicsVideoItem(parent) if parent is not None else QGraphicsVideoItem()
        item.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        return item

    def size_layers(self, width: int, height: int) -> None:
        layer_size = QSizeF(width, height)
        self.before_video.setSize(layer_size)
        self.after_video.setSize(layer_size)

    def set_split_ratio(self, value) -> None:
        self.split_ratio = max(0.025, min(0.975, float(value)))
        self._layout_layers()

    def center_divider(self) -> None:
        self.set_split_ratio(0.5)

    def reset_view(self) -> None:
        self.zoom_factor = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._layout_layers()
        self.view_changed.emit(100)

    def set_zoom(self, value: float, anchor: QPointF | None = None) -> None:
        old_zoom = self.zoom_factor
        new_zoom = max(self.minimum_zoom, min(self.maximum_zoom, float(value)))
        if abs(new_zoom - old_zoom) < 0.0001:
            return
        if anchor is None:
            anchor = QPointF(self.viewport().width() / 2, self.viewport().height() / 2)
        content_x = (anchor.x() - self.pan_x) / max(0.001, old_zoom)
        content_y = (anchor.y() - self.pan_y) / max(0.001, old_zoom)
        self.zoom_factor = new_zoom
        self.pan_x = anchor.x() - content_x * new_zoom
        self.pan_y = anchor.y() - content_y * new_zoom
        self._layout_layers()
        self.view_changed.emit(int(round(new_zoom * 100)))

    def _clamp_pan(self, width: int, height: int) -> None:
        minimum_x = width * (1.0 - self.zoom_factor)
        minimum_y = height * (1.0 - self.zoom_factor)
        self.pan_x = max(minimum_x, min(0.0, self.pan_x))
        self.pan_y = max(minimum_y, min(0.0, self.pan_y))

    def _apply_video_transform(self, width: int, height: int) -> None:
        self._clamp_pan(width, height)
        for video_item in (self.before_video, self.after_video):
            video_item.setScale(self.zoom_factor)
            video_item.setPos(self.pan_x, self.pan_y)

    def _layout_layers(self) -> None:
        width = max(1, self.viewport().width())
        height = max(1, self.viewport().height())
        self.setSceneRect(QRectF(0, 0, width, height))
        self.size_layers(width, height)
        self._apply_video_transform(width, height)
        edge_x = width * self.split_ratio
        self.after_clip.setRect(QRectF(edge_x, 0, width - edge_x, height))
        self.viewport().update()

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:  # type: ignore[override]
        del rect
        width = self.sceneRect().width()
        height = self.sceneRect().height()
        edge_x = width * self.split_ratio
        center_y = height / 2

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(255, 143, 45, 90), 22))
        painter.drawLine(int(edge_x), 0, int(edge_x), int(height))
        painter.setPen(QPen(QColor(0, 0, 0, 235), 10))
        painter.drawLine(int(edge_x), 0, int(edge_x), int(height))
        painter.setPen(QPen(QColor("#FF8A24"), 6))
        painter.drawLine(int(edge_x), 0, int(edge_x), int(height))
        painter.setPen(QPen(QColor("#FFFFFF"), 2))
        painter.drawLine(int(edge_x), 0, int(edge_x), int(height))

        handle_rect = QRectF(edge_x - 31, center_y - 31, 62, 62)
        painter.setPen(QPen(QColor(0, 0, 0, 220), 9))
        painter.setBrush(QColor("#F47B20"))
        painter.drawEllipse(handle_rect)
        painter.setPen(QPen(QColor("#FFFFFF"), 3))
        painter.drawEllipse(handle_rect)
        painter.drawLine(int(edge_x - 15), int(center_y), int(edge_x + 15), int(center_y))
        painter.drawLine(int(edge_x - 15), int(center_y), int(edge_x - 8), int(center_y - 7))
        painter.drawLine(int(edge_x - 15), int(center_y), int(edge_x - 8), int(center_y + 7))
        painter.drawLine(int(edge_x + 15), int(center_y), int(edge_x + 8), int(center_y - 7))
        painter.drawLine(int(edge_x + 15), int(center_y), int(edge_x + 8), int(center_y + 7))

        drag_badge = QRectF(edge_x - 78, 14, 156, 38)
        painter.setPen(QPen(QColor("#FFD0A3"), 2))
        painter.setBrush(QColor(8, 10, 13, 238))
        painter.drawRoundedRect(drag_badge, 10, 10)
        badge_font = painter.font()
        badge_font.setBold(True)
        painter.setFont(badge_font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(drag_badge, Qt.AlignmentFlag.AlignCenter, "اسحب للمقارنة ↔")

        before_badge = QRectF(16, 16, 116, 36)
        after_badge = QRectF(max(16, width - 132), 16, 116, 36)
        painter.setPen(QPen(Qt.PenStyle.NoPen))
        painter.setBrush(QColor(9, 12, 15, 225))
        painter.drawRoundedRect(before_badge, 10, 10)
        painter.setBrush(QColor(226, 104, 30, 235))
        painter.drawRoundedRect(after_badge, 10, 10)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(before_badge, Qt.AlignmentFlag.AlignCenter, "قبل • الأصلي")
        painter.drawText(after_badge, Qt.AlignmentFlag.AlignCenter, "بعد • الجديد")

        if self.zoom_factor > 1.001:
            zoom_badge = QRectF(width - 132, height - 52, 116, 36)
            painter.setPen(QPen(QColor("#95E5D1"), 1))
            painter.setBrush(QColor(8, 12, 15, 230))
            painter.drawRoundedRect(zoom_badge, 10, 10)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(
                zoom_badge,
                Qt.AlignmentFlag.AlignCenter,
                f"تكبير {self.zoom_factor * 100:.0f}%",
            )
        painter.restore()

    def _set_from_view_x(self, x: float) -> None:
        width = self.viewport().width()
        if width > 0:
            self.set_split_ratio(x / width)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            edge_x = self.viewport().width() * self.split_ratio
            if abs(event.position().x() - edge_x) <= 36:
                self.drag_mode = "divider"
                self._set_from_view_x(event.position().x())
                self.setCursor(Qt.CursorShape.SplitHCursor)
            else:
                self.drag_mode = "pan"
                self.drag_start = event.position()
                self.pan_start_x = self.pan_x
                self.pan_start_y = self.pan_y
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.MiddleButton:
            self.reset_view()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self.drag_mode == "divider":
            self._set_from_view_x(event.position().x())
            event.accept()
            return
        if self.drag_mode == "pan":
            delta = event.position() - self.drag_start
            self.pan_x = self.pan_start_x + delta.x()
            self.pan_y = self.pan_start_y + delta.y()
            self._layout_layers()
            event.accept()
            return
        edge_x = self.viewport().width() * self.split_ratio
        self.setCursor(
            Qt.CursorShape.SplitHCursor
            if abs(event.position().x() - edge_x) <= 36
            else Qt.CursorShape.OpenHandCursor
        )
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_mode = None
            edge_x = self.viewport().width() * self.split_ratio
            self.setCursor(
                Qt.CursorShape.SplitHCursor
                if abs(event.position().x() - edge_x) <= 36
                else Qt.CursorShape.OpenHandCursor
            )
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        edge_x = self.viewport().width() * self.split_ratio
        if abs(event.position().x() - edge_x) <= 36:
            self.center_divider()
        else:
            self.reset_view()
        event.accept()

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        delta = event.angleDelta().y()
        if delta:
            steps = delta / 120.0
            self.set_zoom(self.zoom_factor * (1.18**steps), event.position())
            event.accept()
            return
        super().wheelEvent(event)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._layout_layers()


class CompareDialog(QDialog):
    def __init__(
        self,
        original_path: str,
        enhanced_path: str,
        parent: QWidget | None = None,
        *,
        original_offset_ms: int = 0,
        preview_comparison: bool = False,
    ) -> None:
        super().__init__(parent)
        self.original_offset_ms = max(0, int(original_offset_ms))
        self.setWindowTitle(
            "مقارنة معاينة الست ثواني" if preview_comparison else "مقارنة قبل وبعد"
        )
        self.setWindowIcon(app_icon("compare"))
        self.resize(1180, 760)
        self.setMinimumSize(840, 560)

        layout = QVBoxLayout(self)
        set_margins(layout, 16)
        layout.setSpacing(11)

        heading = QHBoxLayout()
        heading_text = QVBoxLayout()
        heading_text.setSpacing(2)
        title = QLabel("مقارنة فوق بعض — نفس الكادر ونفس اللحظة")
        title.setObjectName("CardTitle")
        note = QLabel(
            "عجلة الماوس تكبّر وتصغّر الفيديوين معًا، واسحب الصورة بالكليك اليسار لتحريكهما. أمسك الخط البرتقالي نفسه لتحريك المقارنة."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        heading_text.addWidget(title)
        heading_text.addWidget(note)
        heading.addLayout(heading_text, 1)
        self.sync_status = QLabel("●  متزامن")
        self.sync_status.setObjectName("StatusPill")
        heading.addWidget(self.sync_status)
        layout.addLayout(heading)

        self.canvas = OverlayComparisonWidget(self)
        self.canvas.center_divider()
        layout.addWidget(self.canvas, 1)

        controls = QHBoxLayout()
        self.play_button = QPushButton("إيقاف مؤقت")
        self.play_button.setObjectName("Soft")
        self.play_button.setIcon(app_icon("pause"))
        self.play_button.clicked.connect(self.toggle_playback)
        controls.addWidget(self.play_button)
        center_button = QPushButton("توسيط الحافة")
        center_button.setIcon(app_icon("compare"))
        center_button.clicked.connect(self.canvas.center_divider)
        controls.addWidget(center_button)
        reset_view_button = QPushButton("الحجم الأصلي")
        reset_view_button.setObjectName("Soft")
        reset_view_button.setIcon(app_icon("resolution"))
        reset_view_button.clicked.connect(self.canvas.reset_view)
        controls.addWidget(reset_view_button)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("StatusPill")
        self.canvas.view_changed.connect(
            lambda percent: self.zoom_label.setText(f"{percent}%")
        )
        controls.addWidget(self.zoom_label)
        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(0, 0)
        self.timeline.sliderMoved.connect(self.seek_both)
        controls.addWidget(self.timeline, 1)
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setObjectName("Meta")
        controls.addWidget(self.time_label)
        layout.addLayout(controls)

        self.original_audio = QAudioOutput(self)
        self.original_audio.setVolume(0.0)
        self.enhanced_audio = QAudioOutput(self)
        self.enhanced_audio.setVolume(0.78)

        self.original_player = QMediaPlayer(self)
        self.original_player.setAudioOutput(self.original_audio)
        self.original_player.setVideoOutput(self.canvas.before_video)
        self.original_player.setSource(QUrl.fromLocalFile(original_path))

        self.enhanced_player = QMediaPlayer(self)
        self.enhanced_player.setAudioOutput(self.enhanced_audio)
        self.enhanced_player.setVideoOutput(self.canvas.after_video)
        self.enhanced_player.positionChanged.connect(self.update_position)
        self.enhanced_player.durationChanged.connect(self.update_duration)
        self.enhanced_player.playbackStateChanged.connect(self.update_play_button)
        self.enhanced_player.errorOccurred.connect(self.media_error)
        self.enhanced_player.setSource(QUrl.fromLocalFile(enhanced_path))

        QTimer.singleShot(200, self.play_both)
        QTimer.singleShot(0, self.canvas.center_divider)

    def play_both(self) -> None:
        position = self.enhanced_player.position()
        if self.enhanced_player.duration() and position >= self.enhanced_player.duration() - 100:
            position = 0
            self.enhanced_player.setPosition(0)
        self.original_player.setPosition(position + self.original_offset_ms)
        self.original_player.play()
        self.enhanced_player.play()

    def toggle_playback(self) -> None:
        if self.enhanced_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.enhanced_player.pause()
            self.original_player.pause()
        else:
            self.play_both()

    def update_play_button(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText("إيقاف مؤقت" if playing else "تشغيل")
        self.play_button.setIcon(app_icon("pause") if playing else app_icon("play"))
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self.original_player.pause()

    def update_duration(self, duration: int) -> None:
        self.timeline.setRange(0, duration)
        self.update_position(self.enhanced_player.position())

    def update_position(self, position: int) -> None:
        if not self.timeline.isSliderDown():
            self.timeline.setValue(position)
        original_position = position + self.original_offset_ms
        if abs(self.original_player.position() - original_position) > 130:
            self.original_player.setPosition(original_position)
        self.time_label.setText(
            f"{format_duration(position / 1000)} / "
            f"{format_duration(self.enhanced_player.duration() / 1000)}"
        )

    def seek_both(self, position: int) -> None:
        self.original_player.setPosition(position + self.original_offset_ms)
        self.enhanced_player.setPosition(position)

    def media_error(self, _error, message: str) -> None:
        if message:
            self.sync_status.setText("تعذّر عرض إحدى الطبقتين")

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Space:
            self.toggle_playback()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.original_player.stop()
        self.enhanced_player.stop()
        super().closeEvent(event)


class ImageOverlayComparison(OverlayComparisonWidget):
    """The same wipe canvas, driven by two pixmaps instead of two players."""

    def create_layer(self, parent: QGraphicsItem | None = None):
        item = QGraphicsPixmapItem(parent) if parent is not None else QGraphicsPixmapItem()
        item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        return item

    def size_layers(self, width: int, height: int) -> None:
        del width, height  # pixmap layers are sized by the transform below

    def _apply_video_transform(self, width: int, height: int) -> None:
        self._clamp_pan(width, height)
        for layer in (self.before_video, self.after_video):
            pixmap = layer.pixmap()
            if pixmap.isNull():
                continue
            fit = min(width / pixmap.width(), height / pixmap.height())
            scale = fit * self.zoom_factor
            layer.setScale(scale)
            layer.setPos(
                self.pan_x + (width - pixmap.width() * scale) / 2,
                self.pan_y + (height - pixmap.height() * scale) / 2,
            )

    def set_images(self, before: QPixmap, after: QPixmap) -> None:
        self.before_video.setPixmap(before)
        self.after_video.setPixmap(after)
        self._layout_layers()


class ImageCompareDialog(QDialog):
    def __init__(
        self,
        original_path: str,
        enhanced_path: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("مقارنة قبل وبعد — صورة")
        self.setWindowIcon(app_icon("compare"))
        self.resize(1180, 760)
        self.setMinimumSize(840, 560)

        layout = QVBoxLayout(self)
        set_margins(layout, 16)
        layout.setSpacing(11)

        heading = QHBoxLayout()
        heading_text = QVBoxLayout()
        heading_text.setSpacing(2)
        title = QLabel("مقارنة فوق بعض — نفس الكادر بالبكسل")
        title.setObjectName("CardTitle")
        note = QLabel(
            "عجلة الماوس تكبّر الصورتين معًا، واسحب بالكليك اليسار لتحريكهما. "
            "أمسك الخط البرتقالي نفسه لتحريك حافة المقارنة."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        heading_text.addWidget(title)
        heading_text.addWidget(note)
        heading.addLayout(heading_text, 1)
        self.size_status = QLabel("—")
        self.size_status.setObjectName("StatusPill")
        heading.addWidget(self.size_status)
        layout.addLayout(heading)

        self.canvas = ImageOverlayComparison(self)
        self.canvas.center_divider()
        layout.addWidget(self.canvas, 1)

        controls = QHBoxLayout()
        center_button = QPushButton("توسيط الحافة")
        center_button.setIcon(app_icon("compare"))
        center_button.clicked.connect(self.canvas.center_divider)
        controls.addWidget(center_button)
        reset_view_button = QPushButton("الحجم الأصلي")
        reset_view_button.setObjectName("Soft")
        reset_view_button.setIcon(app_icon("resolution"))
        reset_view_button.clicked.connect(self.canvas.reset_view)
        controls.addWidget(reset_view_button)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("StatusPill")
        self.canvas.view_changed.connect(
            lambda percent: self.zoom_label.setText(f"{percent}%")
        )
        controls.addWidget(self.zoom_label)
        controls.addStretch()
        layout.addLayout(controls)

        before = QPixmap(original_path)
        after = QPixmap(enhanced_path)
        self.canvas.set_images(before, after)
        if not before.isNull() and not after.isNull():
            self.size_status.setText(
                f"{before.width()}×{before.height()}  ←  {after.width()}×{after.height()}"
            )
        else:
            self.size_status.setText("تعذّر عرض إحدى الصورتين")
        QTimer.singleShot(0, self.canvas.center_divider)


class ImageViewDialog(QDialog):
    def __init__(self, path: str, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowIcon(app_icon("compare"))
        self.resize(1000, 700)
        self.setMinimumSize(600, 420)

        layout = QVBoxLayout(self)
        set_margins(layout, 14)
        layout.setSpacing(10)

        self.pixmap = QPixmap(path)
        self.view = QLabel()
        self.view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.view.setMinimumSize(400, 300)
        self.view.setStyleSheet("background: #050709; border-radius: 12px;")
        layout.addWidget(self.view, 1)

        meta = QLabel(
            f"{self.pixmap.width()}×{self.pixmap.height()}  •  {Path(path).name}"
            if not self.pixmap.isNull()
            else "تعذّر عرض هذه الصورة"
        )
        meta.setObjectName("Meta")
        meta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(meta)
        self._render()

    def _render(self) -> None:
        if self.pixmap.isNull():
            self.view.setText("تعذّر عرض هذه الصورة")
            return
        self.view.setPixmap(
            self.pixmap.scaled(
                self.view.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._render()


class PreferencesDialog(QDialog):
    def __init__(
        self,
        theme: str,
        strict_export: bool,
        auto_compare: bool,
        completion_sound: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("إعدادات ڤيديو كرافت")
        self.setWindowIcon(app_icon("settings"))
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        set_margins(layout, 22)
        layout.setSpacing(15)

        title = QLabel("إعدادات الاستديو")
        title.setObjectName("PageTitle")
        subtitle = QLabel("اضبط شكل البرنامج وسلوك التصدير. تحفظ اختياراتك تلقائيًا.")
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        appearance, appearance_layout = card("المظهر", "تبديل كامل بين الواجهتين.", "sun")
        theme_row = QHBoxLayout()
        theme_label = QLabel("نمط الواجهة")
        theme_label.setStyleSheet("font-weight: 650;")
        self.theme_combo = ScrollSafeComboBox()
        self.theme_combo.addItem(app_icon("moon"), "داكن — تركيز أعلى", "dark")
        self.theme_combo.addItem(app_icon("sun"), "فاتح — وضوح نهاري", "light")
        index = self.theme_combo.findData(theme)
        self.theme_combo.setCurrentIndex(max(0, index))
        theme_row.addWidget(theme_label)
        theme_row.addStretch()
        theme_row.addWidget(self.theme_combo)
        appearance_layout.addLayout(theme_row)
        layout.addWidget(appearance)

        reliability, reliability_layout = card(
            "محرك التصدير الموثوق",
            "يحافظ على الدقة وFPS المطلوبين ويخفف فقط المرشحات الثقيلة إذا احتاج الجهاز.",
            "shield",
        )
        self.strict_check = QCheckBox("تشغيل الوضع الصارم وإعادة المحاولة تلقائيًا")
        self.strict_check.setChecked(strict_export)
        self.strict_check.setToolTip(
            "عند فشل مرشح أو ترميز، يعيد التصدير بمسار آمن مع نفس الدقة ومعدل الإطارات."
        )
        self.auto_compare_check = QCheckBox("فتح مقارنة قبل / بعد تلقائيًا عند النجاح")
        self.auto_compare_check.setChecked(auto_compare)
        self.completion_sound_check = QCheckBox("تشغيل صوت عند اكتمال تصدير الفيديو")
        self.completion_sound_check.setChecked(completion_sound)
        self.completion_sound_check.setToolTip(
            "يشغّل نغمة نظام قصيرة بعد نجاح التصدير الكامل فقط."
        )
        reliability_layout.addWidget(self.strict_check)
        reliability_layout.addWidget(self.auto_compare_check)
        reliability_layout.addWidget(self.completion_sound_check)
        layout.addWidget(reliability)

        info = QLabel("كل المعالجة محلية. لا توجد حسابات أو رفع ملفات أو تتبع.")
        info.setObjectName("Footnote")
        info.setWordWrap(True)
        layout.addWidget(info)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("حفظ الإعدادات")
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("Primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("إلغاء")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def selected_theme(self) -> str:
        return str(self.theme_combo.currentData())


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME_AR)
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(1380, 900)
        self.setMinimumSize(1080, 720)

        self.settings_store = QSettings("VideoCraft", "VideoCraftStudio")
        self.current_theme = str(self.settings_store.value("theme", "dark"))
        if self.current_theme not in {"dark", "light"}:
            self.current_theme = "dark"
        self.strict_export = setting_bool(self.settings_store.value("strict_export", True))
        self.auto_compare = setting_bool(self.settings_store.value("auto_compare", True))
        self.completion_sound_enabled = setting_bool(
            self.settings_store.value("completion_sound", True)
        )
        self.media_info: MediaInfo | None = None
        self.source_path: str | None = None
        self.batch_paths: list[str] = []
        self.batch_index = 0
        self.batch_running = False
        self.batch_done: list[str] = []
        self.batch_failed: list[str] = []
        self.batch_skipped = 0
        self.last_output: str | None = None
        self.last_compare_output: str | None = None
        self.last_compare_offset_ms = 0
        self.last_compare_is_preview = False
        self.last_compare_is_image = False
        self.process: QProcess | None = None
        self.active_job: str | None = None
        self.active_output: str | None = None
        self.active_settings: ExportSettings | None = None
        self.active_preview_start = 0.0
        self.cancel_requested = False
        self.hardware_retry_used = False
        self.safe_retry_used = False
        self.cpu_retry_used = False
        self.process_log: list[str] = []
        self._process_buffer = ""
        self.active_total_duration = 0.0
        self.progress_seen = False
        self.current_speed = ""
        self.progress_clock = QElapsedTimer()
        self.progress_timer = QTimer(self)
        self.progress_timer.setInterval(500)
        self.progress_timer.timeout.connect(self.update_progress_status)
        self.nvenc_ready = self.detect_nvenc()
        self.ai_ready = ai_engine_available()
        self.waifu2x_ready = waifu2x_available()
        self.active_ai = False
        self.active_is_image = False
        self.current_ai_stage = ""

        self.build_ui()
        self.apply_theme(self.current_theme, persist=False)
        self.load_preferences()
        self.connect_summary_updates()
        self.update_summary()

    @staticmethod
    def detect_nvenc() -> bool:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return False
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=64x64:d=0.05",
            "-frames:v",
            "1",
            "-c:v",
            "h264_nvenc",
            "-f",
            "null",
            "-",
        ]
        try:
            result = subprocess.run(command, capture_output=True, timeout=8, check=False)
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def build_ui(self) -> None:
        root = BackgroundWidget(self.current_theme)
        self.root_widget = root
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.setCentralWidget(root)

        header = QFrame()
        header.setObjectName("Header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(30, 14, 30, 14)
        header_layout.setSpacing(11)
        logo = QLabel()
        logo.setPixmap(QIcon(str(ICON_PATH)).pixmap(44, 44))
        header_layout.addWidget(logo)
        brand_box = QVBoxLayout()
        brand_box.setSpacing(0)
        brand = QLabel(APP_NAME_AR)
        brand.setObjectName("Brand")
        brand_box.addWidget(brand)
        brand_note = QLabel("صناعة محلية بحب — كل شيء يعمل على جهازك")
        brand_note.setObjectName("Footnote")
        brand_box.addWidget(brand_note)
        header_layout.addLayout(brand_box)
        header_layout.addStretch()
        self.local_status = QLabel("●  يعمل محليًا — ملفاتك لا تُرفع")
        self.local_status.setObjectName("StatusPill")
        header_layout.addWidget(self.local_status)
        self.theme_button = QPushButton()
        self.theme_button.setObjectName("Soft")
        self.theme_button.setToolTip("تبديل الوضع الداكن والفاتح")
        self.theme_button.clicked.connect(self.toggle_theme)
        header_layout.addWidget(self.theme_button)
        self.settings_button = QPushButton("الإعدادات")
        self.settings_button.setObjectName("Soft")
        self.settings_button.setIcon(app_icon("settings"))
        self.settings_button.clicked.connect(self.open_preferences)
        header_layout.addWidget(self.settings_button)
        root_layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(30, 27, 30, 30)
        page_layout.setSpacing(18)

        intro = QHBoxLayout()
        intro_text = QVBoxLayout()
        intro_text.setSpacing(5)
        page_title = QLabel("حسّن الفيديو والصور بضغطة واحدة")
        page_title.setObjectName("PageTitle")
        page_subtitle = QLabel(
            "اسحب ملفك، اختر لمسة جاهزة، واضغط تصدير — نحافظ على خصوصيتك ونتائج تليق بشغلك."
        )
        page_subtitle.setObjectName("Muted")
        page_subtitle.setWordWrap(True)
        intro_text.addWidget(page_title)
        intro_text.addWidget(page_subtitle)
        intro.addLayout(intro_text)
        intro.addStretch()
        steps = QHBoxLayout()
        steps.setSpacing(7)
        for text in ("01  المصدر", "02  اللمسة", "03  التصدير"):
            step = QLabel(text)
            step.setObjectName("ValueBadge")
            step.setAlignment(Qt.AlignmentFlag.AlignCenter)
            steps.addWidget(step, 0, Qt.AlignmentFlag.AlignVCenter)
        intro.addLayout(steps)
        page_layout.addLayout(intro)

        self.drop_zone = DropZone()
        self.drop_zone.files_chosen.connect(self.load_files)
        page_layout.addWidget(self.drop_zone)

        self.batch_panel = BatchPanel()
        self.batch_panel.tile_clicked.connect(self.select_batch_item)
        self.batch_panel.remove_requested.connect(self.remove_batch_item)
        self.batch_panel.clear_requested.connect(self.clear_batch)
        self.batch_panel.open_folder_requested.connect(self.open_output_folder)
        page_layout.addWidget(self.batch_panel)

        quick_card = QFrame()
        quick_card.setObjectName("Card")
        quick_layout = QVBoxLayout(quick_card)
        set_margins(quick_layout, 20)
        quick_layout.setSpacing(12)
        quick_title_row = QHBoxLayout()
        quick_title_row.setSpacing(9)
        quick_icon = QLabel()
        quick_icon.setPixmap(app_icon("route").pixmap(22, 22))
        quick_title = QLabel("بدايات سريعة")
        quick_title.setObjectName("CardTitle")
        quick_hint = QLabel("زر واحد يضبط كل شيء — وبعدها عدّل ما تشاء بحرية.")
        quick_hint.setObjectName("CardSubtitle")
        quick_title_row.addWidget(quick_icon)
        quick_title_row.addWidget(quick_title)
        quick_title_row.addSpacing(8)
        quick_title_row.addWidget(quick_hint)
        quick_title_row.addStretch()
        quick_layout.addLayout(quick_title_row)
        quick_row = QHBoxLayout()
        quick_row.setSpacing(8)
        self.quick_preset_buttons: dict[str, QPushButton] = {}
        for key, label, icon, tip in (
            ("fast", "سريع وجاهز", "motion", "1080p • 60fps • AI سريع — نتيجة ممتازة بأقل وقت"),
            ("best", "أفضل جودة", "sparkle", "أقصى تنقية وتفاصيل مع AI EXTREME — الأبطأ والأجمل"),
            ("vintage", "فيديو قديم", "shield", "تنظيف التشويش وإصلاح الخطوط وتثبيت الاهتزاز"),
            ("anime", "أنمي ورسوم", "palette", "Real‑CUGAN Pro مع ألوان حيوية — الأفضل للرسوم والأنمي"),
            ("social", "سوشيال ميديا", "upload", "1080p عمودي أو أفقي بحجم صغير ووضوح عالٍ للهاتف"),
            ("photo", "ترميم صورة", "quality", "إصلاح البكسلة واستعادة تفاصيل الصور القديمة"),
        ):
            button = QPushButton(label)
            button.setObjectName("Pill")
            button.setIcon(app_icon(icon))
            button.setToolTip(tip)
            button.setMinimumHeight(46)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _checked=False, k=key: self.apply_quick_preset(k))
            quick_row.addWidget(button)
            self.quick_preset_buttons[key] = button
        quick_layout.addLayout(quick_row)
        page_layout.addWidget(quick_card)

        columns = QHBoxLayout()
        columns.setSpacing(18)
        primary_column = QVBoxLayout()
        primary_column.setSpacing(18)
        secondary_column = QVBoxLayout()
        secondary_column.setSpacing(18)

        dimensions_card, dimensions = card(
            "المقاس والحركة",
            "نحافظ على نسبة الأبعاد تلقائيًا؛ الفيديو العمودي يبقى عموديًا من دون قص أو تمديد.",
            "motion",
        )
        dimensions.addWidget(icon_heading("الدقة النهائية", "resolution"))
        self.resolution_image_note = QLabel(
            "مع الصور تعمل هذه الأزرار كحدٍ أدنى فقط: نكبّر إذا كانت الصورة أصغر، "
            "ولا نصغّر صورة أكبر من المقاس المختار. «الأصلية» تبقي المقاس نفسه وتستفيد من AI في التنظيف."
        )
        self.resolution_image_note.setObjectName("Hint")
        self.resolution_image_note.setWordWrap(True)
        self.resolution_image_note.hide()
        res_row = QHBoxLayout()
        res_row.setSpacing(8)
        self.resolution_group = QButtonGroup(self)
        self.resolution_group.setExclusive(True)
        self.resolution_buttons: dict[str, QPushButton] = {}
        for key, label, note in (
            ("source", "الأصلية", "كما هي"),
            ("1080p", "1080p", "Full HD"),
            ("2k", "2K", "1440p"),
            ("4k", "4K", "2160p"),
        ):
            button = QPushButton(f"{label}\n{note}")
            button.setObjectName("Pill")
            button.setIcon(app_icon("resolution"))
            button.setCheckable(True)
            button.setProperty("resolution", key)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setMinimumHeight(62)
            self.resolution_group.addButton(button)
            self.resolution_buttons[key] = button
            res_row.addWidget(button)
        self.resolution_buttons["1080p"].setChecked(True)
        dimensions.addLayout(res_row)
        dimensions.addWidget(self.resolution_image_note)
        dimensions.addWidget(divider())

        fps_top = QHBoxLayout()
        fps_text = QVBoxLayout()
        fps_text.setSpacing(2)
        fps_note = QLabel("من 15 إلى 120 fps — القيم الأعلى أنعم وتحتاج وقتًا وحجمًا أكبر")
        fps_note.setObjectName("Hint")
        fps_text.addWidget(icon_heading("معدل الإطارات", "fps"))
        fps_text.addWidget(fps_note)
        fps_top.addLayout(fps_text)
        fps_top.addStretch()
        self.fps_value = QLabel("60 fps")
        self.fps_value.setObjectName("ValueBadge")
        fps_top.addWidget(self.fps_value)
        dimensions.addLayout(fps_top)

        self.fps_slider = ScrollSafeSlider(Qt.Orientation.Horizontal)
        self.fps_slider.setRange(15, 120)
        self.fps_slider.setValue(60)
        self.fps_slider.valueChanged.connect(lambda value: self.fps_value.setText(f"{value} fps"))
        dimensions.addWidget(self.fps_slider)
        fps_presets = QHBoxLayout()
        fps_presets.setSpacing(7)
        self.fps_preset_buttons: list[QPushButton] = []
        for value in (24, 30, 60, 90, 120):
            button = QPushButton(str(value))
            button.setObjectName("Pill")
            button.setMaximumHeight(38)
            button.clicked.connect(lambda _checked=False, fps=value: self.fps_slider.setValue(fps))
            fps_presets.addWidget(button)
            self.fps_preset_buttons.append(button)
        fps_presets.addStretch()
        dimensions.addLayout(fps_presets)
        self.interpolate_check = QCheckBox("إنشاء حركة بينية عند رفع الإطارات")
        self.interpolate_check.setChecked(True)
        self.interpolate_check.setToolTip(
            "يحلل حركة اللقطات ويبني انتقالات أكثر سلاسة. مفيد عند تحويل 30 إلى 60 أو 120 fps."
        )
        dimensions.addWidget(self.interpolate_check)
        self.motion_image_note = QLabel("المصدر صورة ثابتة — لا معنى لمعدل الإطارات أو الحركة البينية.")
        self.motion_image_note.setObjectName("Hint")
        self.motion_image_note.setWordWrap(True)
        self.motion_image_note.hide()
        dimensions.addWidget(self.motion_image_note)
        self.motion_widgets: list[QWidget] = [
            self.fps_value,
            self.fps_slider,
            self.interpolate_check,
            *self.fps_preset_buttons,
        ]
        primary_column.addWidget(dimensions_card)

        enhancement_card, enhancement = card(
            "تحسين الصورة",
            "ابدأ بقالب لوني متزن ثم عدّل ما تحتاجه فقط. القيم الافتراضية محافظة حتى لا يبدو الفيديو مصطنعًا.",
            "sparkle",
        )
        style_row = QHBoxLayout()
        style_text = QVBoxLayout()
        style_text.setSpacing(2)
        style_hint = QLabel("تغيير خفيف في المزاج العام، ويمكنك ضبط التفاصيل تحته")
        style_hint.setObjectName("Hint")
        style_text.addWidget(icon_heading("المعالجة اللونية", "palette"))
        style_text.addWidget(style_hint)
        style_row.addLayout(style_text, 1)
        self.color_style = ScrollSafeComboBox()
        self.color_style.addItem(app_icon("palette"), "طبيعي ومتزن", "natural")
        self.color_style.addItem(app_icon("brightness"), "دافئ وهادئ", "warm")
        self.color_style.addItem(app_icon("sun"), "نظيف ومشرق", "bright")
        self.color_style.addItem(app_icon("motion"), "سينمائي خفيف", "cinematic")
        self.color_style.addItem(app_icon("cancel"), "بدون قالب", "none")
        self.color_style.setMinimumWidth(170)
        style_row.addWidget(self.color_style)
        enhancement.addLayout(style_row)

        max_quality_row = QHBoxLayout()
        max_quality_text = QVBoxLayout()
        max_quality_text.setSpacing(2)
        self.max_quality_badge = QLabel("أقصى تحسين نشط")
        self.max_quality_badge.setObjectName("AccentText")
        max_quality_hint = QLabel(
            "تنقية 100%، تفاصيل 100%، ألوان قوية متوازنة وترميز بأقل فقد ممكن."
        )
        max_quality_hint.setObjectName("Hint")
        max_quality_text.addWidget(self.max_quality_badge)
        max_quality_text.addWidget(max_quality_hint)
        max_quality_row.addLayout(max_quality_text, 1)
        self.max_enhancement_button = QPushButton("تطبيق أقصى تحسين")
        self.max_enhancement_button.setObjectName("Primary")
        self.max_enhancement_button.setIcon(app_icon("sparkle"))
        self.max_enhancement_button.clicked.connect(self.apply_max_enhancement)
        max_quality_row.addWidget(self.max_enhancement_button)
        enhancement.addLayout(max_quality_row)
        enhancement.addWidget(divider())

        self.denoise_slider = SliderRow(
            "إزالة التشويش", "ينظف الحبوب الرقمية في الظلال مع إبقاء ملمس الصورة.", 0, 100, 18,
            icon_name="denoise",
        )
        self.sharpness_slider = SliderRow(
            "وضوح التفاصيل", "يعيد حضور الحواف بعد التكبير من دون هالات قوية.", 0, 100, 22,
            icon_name="sharpness",
        )
        self.contrast_slider = SliderRow(
            "التباين", "يفصل الضوء عن الظل؛ الزيادة البسيطة غالبًا تكفي.", -50, 50, 4,
            signed=True, suffix="", icon_name="contrast",
        )
        self.saturation_slider = SliderRow(
            "تشبع الألوان", "لون أغنى أو أهدأ مع الحفاظ على درجات البشرة.", -50, 50, 4,
            signed=True, suffix="", icon_name="saturation",
        )
        self.brightness_slider = SliderRow(
            "الإضاءة", "تصحيح عام للمشهد؛ استخدمه بهدوء لحماية التفاصيل.", -50, 50, 0,
            signed=True, suffix="", icon_name="brightness",
        )
        for index, control in enumerate(
            (
                self.denoise_slider,
                self.sharpness_slider,
                self.contrast_slider,
                self.saturation_slider,
                self.brightness_slider,
            )
        ):
            enhancement.addWidget(control)
            if index < 4:
                enhancement.addWidget(divider())

        advanced_row = QHBoxLayout()
        self.stabilize_check = QCheckBox("تثبيت الاهتزاز الخفيف")
        self.stabilize_check.setToolTip("يخفف اهتزاز اليد، وقد يغيّر أطراف الصورة بدرجة طفيفة.")
        self.deinterlace_check = QCheckBox("إصلاح خطوط الفيديو القديم")
        self.deinterlace_check.setToolTip("فعّله فقط إذا ظهرت خطوط أفقية أثناء الحركة في تسجيلات قديمة.")
        stabilize_icon = QLabel()
        stabilize_icon.setPixmap(app_icon("stabilize").pixmap(18, 18))
        interlace_icon = QLabel()
        interlace_icon.setPixmap(app_icon("interlace").pixmap(18, 18))
        advanced_row.addWidget(stabilize_icon)
        advanced_row.addWidget(self.stabilize_check)
        advanced_row.addWidget(interlace_icon)
        advanced_row.addWidget(self.deinterlace_check)
        advanced_row.addStretch()
        enhancement.addWidget(divider())
        enhancement.addLayout(advanced_row)
        primary_column.addWidget(enhancement_card)

        ai_card, ai_layout = card(
            "محرك AI المحلي",
            "تكبير عصبي حقيقي على بطاقة الرسوم. لا يرفع الفيديو للإنترنت، ويعود للمسار الموثوق تلقائيًا إذا لم تكفِ الذاكرة.",
            "sparkle",
        )
        ai_header = QHBoxLayout()
        ai_header_text = QVBoxLayout()
        ai_header_text.setSpacing(2)
        self.ai_enabled_check = QCheckBox("تشغيل تحسين AI للفيديو")
        self.ai_enabled_check.setChecked(self.ai_ready)
        self.ai_enabled_check.setEnabled(self.ai_ready)
        self.ai_status = QLabel(
            "✓ جاهز على GTX 1650 عبر Vulkan"
            if self.ai_ready
            else "محرك AI غير مثبت — سيُستخدم التحسين التقليدي"
        )
        self.ai_status.setObjectName("AccentText" if self.ai_ready else "Muted")
        ai_header_text.addWidget(self.ai_enabled_check)
        ai_header_text.addWidget(self.ai_status)
        ai_header.addLayout(ai_header_text, 1)
        ai_layout.addLayout(ai_header)
        ai_layout.addWidget(divider())

        ai_model_row = QHBoxLayout()
        ai_model_text = QVBoxLayout()
        ai_model_text.setSpacing(2)
        ai_model_title = QLabel("نموذج استعادة التفاصيل")
        ai_model_title.setStyleSheet("font-weight: 650;")
        ai_model_hint = QLabel("اختر Extreme لأقصى تفاصيل، أو النموذج السريع للمقاطع الطويلة.")
        ai_model_hint.setObjectName("Hint")
        ai_model_text.addWidget(ai_model_title)
        ai_model_text.addWidget(ai_model_hint)
        ai_model_row.addLayout(ai_model_text, 1)
        self.ai_model_combo = ScrollSafeComboBox()
        self.ai_model_combo.addItem(
            app_icon("sparkle"), "AI EXTREME — أقصى تفاصيل", "anime_extreme"
        )
        self.ai_model_combo.addItem(
            app_icon("motion"), "AnimeVideo AI — سريع", "anime_fast"
        )
        self.ai_model_combo.addItem(
            app_icon("sparkle"), "Anime4K GAN — حواف قوية", "anime_shader"
        )
        self.ai_model_combo.addItem(
            app_icon("quality"), "Real-ESRGAN — واقعي قوي", "general"
        )
        self.ai_model_combo.setMinimumWidth(210)
        self.ai_model_combo.setEnabled(self.ai_ready)
        ai_model_row.addWidget(self.ai_model_combo)
        ai_layout.addLayout(ai_model_row)
        ai_layout.addWidget(divider())

        self.photo_restore_row = QWidget()
        photo_restore_layout = QHBoxLayout(self.photo_restore_row)
        photo_restore_layout.setContentsMargins(0, 0, 0, 0)
        photo_restore_layout.setSpacing(12)
        photo_restore_text = QVBoxLayout()
        photo_restore_text.setSpacing(2)
        self.photo_restore_check = QCheckBox("ترميم الصور: إصلاح البكسلة واستعادة التفاصيل")
        self.photo_restore_check.setEnabled(self.ai_ready)
        self.photo_restore_check.setToolTip(
            "للصور الثابتة فقط: اختر محركًا واقعيًا للصور أو Waifu2x للرسوم "
            "والأنمي. كلاهما لا يضيف فلترًا لونيًا أو تجميليًا."
        )
        photo_restore_hint = QLabel(
            "Real-CUGAN Pro ×3: أقوى ترميم متوافق للخطوط والعيون والبكسلة بلا فلتر لوني."
        )
        photo_restore_hint.setObjectName("Hint")
        photo_restore_text.addWidget(self.photo_restore_check)
        photo_restore_text.addWidget(photo_restore_hint)
        photo_restore_layout.addLayout(photo_restore_text, 1)
        self.photo_restore_profile_combo = ScrollSafeComboBox()
        self.photo_restore_profile_combo.addItem("صور واقعية — Real-ESRGAN", "photo_restore")
        self.photo_restore_profile_combo.addItem(
            "أقوى للأنمي — Real-CUGAN Pro ×3", "anime_supreme"
        )
        self.photo_restore_profile_combo.addItem(
            "رسوم وأنمي — Waifu2x CUNet", "waifu2x_anime"
        )
        self.photo_restore_profile_combo.setMinimumWidth(205)
        self.photo_restore_profile_combo.setEnabled(self.ai_ready)
        photo_restore_layout.addWidget(self.photo_restore_profile_combo)
        self.photo_restore_scale_combo = ScrollSafeComboBox()
        self.photo_restore_scale_combo.addItem("×2 تفاصيل ودقة", 2)
        self.photo_restore_scale_combo.addItem("×4 أقصى ترميم", 4)
        self.photo_restore_scale_combo.setCurrentIndex(1)
        self.photo_restore_scale_combo.setMinimumWidth(150)
        self.photo_restore_scale_combo.setEnabled(self.ai_ready)
        photo_restore_layout.addWidget(self.photo_restore_scale_combo)
        self.photo_restore_row.hide()
        ai_layout.addWidget(self.photo_restore_row)
        ai_layout.addWidget(divider())

        self.ai_fast_mode_check = QCheckBox("تسريع التصدير مع الحفاظ على نموذج AI")
        self.ai_fast_mode_check.setChecked(True)
        self.ai_fast_mode_check.setEnabled(self.ai_ready)
        self.ai_fast_mode_check.setToolTip(
            "يسرّع الترميز والملفات الوسيطة ويحافظ على نفس نموذج الاستعادة؛ قد يزيد حجم الملف المؤقت قليلًا."
        )
        ai_layout.addWidget(self.ai_fast_mode_check)

        self.ai_motion_check = QCheckBox("حركة AI بنموذج RIFE 4.26 عند رفع FPS")
        self.ai_motion_check.setChecked(False)
        self.ai_motion_check.setEnabled(self.ai_ready)
        self.ai_motion_check.setToolTip(
            "أدق من دمج الإطارات التقليدي، لكنه أبطأ ويحتاج ذاكرة أكبر خصوصًا مع 4K."
        )
        ai_layout.addWidget(self.ai_motion_check)
        ai_speed_note = QLabel(
            "Extreme يستخدم Real‑CUGAN Pro وتنقية عميقة وشحذًا نهائيًا؛ هو الأقوى لكنه الأبطأ وقد يصنع تفاصيل جديدة في المصادر الضعيفة جدًا."
        )
        ai_speed_note.setObjectName("Footnote")
        ai_speed_note.setWordWrap(True)
        ai_layout.addWidget(ai_speed_note)
        primary_column.addWidget(ai_card)

        source_card, source = card("قراءة الفيديو", "ملخص سريع لما سيدخل إلى رحلة المعالجة.", "source")
        self.source_resolution_label = QLabel("الدقة: —")
        self.source_fps_label = QLabel("الإطارات: —")
        self.source_duration_label = QLabel("المدة: —")
        self.source_codec_label = QLabel("الترميز: —")
        for label in (
            self.source_resolution_label,
            self.source_fps_label,
            self.source_duration_label,
            self.source_codec_label,
        ):
            label.setObjectName("Meta")
            source.addWidget(label)
        self.preview_source_button = QPushButton("مشاهدة الفيديو الأصلي")
        self.preview_source_button.setIcon(app_icon("play"))
        self.preview_source_button.setEnabled(False)
        self.preview_source_button.clicked.connect(self.preview_source)
        source.addWidget(self.preview_source_button)
        secondary_column.addWidget(source_card)

        audio_card, audio = card(
            "الصوت",
            "المعالجة الافتراضية توحّد مستوى الصوت من غير ضغط مزعج.",
            "audio",
        )
        self.audio_normalize_check = QCheckBox("موازنة مستوى الصوت")
        self.audio_normalize_check.setChecked(True)
        self.audio_normalize_check.setToolTip("يضبط شدة الصوت إلى مستوى مريح وثابت للمشاهدة الشخصية.")
        self.audio_clean_check = QCheckBox("تنظيف الطنين والترددات الزائدة")
        self.audio_clean_check.setToolTip("يخفف الرجة المنخفضة والصفير العالي؛ لا يلزم للمصادر النظيفة.")
        audio.addWidget(self.audio_normalize_check)
        audio.addWidget(self.audio_clean_check)
        self.audio_card = audio_card
        secondary_column.addWidget(audio_card)

        export_card, export = card(
            "ملف التصدير",
            "إعدادات عملية للتشغيل على التلفاز والجوال والكمبيوتر.",
            "export",
        )
        self.codec_heading = icon_heading("الترميز", "codec")
        export.addWidget(self.codec_heading)
        self.codec_combo = ScrollSafeComboBox()
        self.codec_combo.addItem(app_icon("codec"), "H.264 — توافق أوسع", "h264")
        self.codec_combo.addItem(app_icon("codec"), "H.265 — حجم أقل وجودة أعلى", "h265")
        export.addWidget(self.codec_combo)

        self.image_format_heading = icon_heading("صيغة الصورة", "codec")
        self.image_format_heading.hide()
        export.addWidget(self.image_format_heading)
        self.image_format_combo = ScrollSafeComboBox()
        self.image_format_combo.addItem(app_icon("quality"), "PNG — بلا فقد إطلاقًا", "png")
        self.image_format_combo.addItem(app_icon("codec"), "JPG — توافق أوسع وحجم أقل", "jpg")
        self.image_format_combo.addItem(app_icon("codec"), "WebP — حجم أصغر بجودة قريبة", "webp")
        self.image_format_combo.hide()
        export.addWidget(self.image_format_combo)

        export.addWidget(icon_heading("مستوى الجودة", "quality"))
        self.quality_combo = ScrollSafeComboBox()
        self.quality_combo.addItem(app_icon("sparkle"), "قصوى جدًا — أقل فقد ممكن", 14)
        self.quality_combo.addItem(app_icon("quality"), "فائقة — ملف أكبر", 16)
        self.quality_combo.addItem(app_icon("quality"), "عالية — موصى بها", 18)
        self.quality_combo.addItem(app_icon("quality"), "متوازنة", 21)
        self.quality_combo.addItem(app_icon("quality"), "موفرة للمساحة", 24)
        self.quality_combo.setCurrentIndex(0)
        export.addWidget(self.quality_combo)

        self.hardware_check = QCheckBox("تسريع التصدير عبر بطاقة NVIDIA")
        self.hardware_check.setChecked(self.nvenc_ready)
        self.hardware_check.setEnabled(self.nvenc_ready)
        self.hardware_check.setToolTip(
            "متاح على جهازك" if self.nvenc_ready else "لم تتوفر بطاقة NVIDIA متوافقة؛ سيُستخدم المعالج بدقة أعلى."
        )
        export.addWidget(self.hardware_check)
        self.reliability_badge = QLabel("✓  وضع التصدير الموثوق مفعّل")
        self.reliability_badge.setObjectName("StatusPill")
        self.reliability_badge.setToolTip(
            "يفحص مساحة الحفظ ويستخدم معالجة تكيفية ثم يعيد المحاولة تلقائيًا عند الخطأ."
        )
        export.addWidget(self.reliability_badge)

        export.addWidget(icon_heading("اسم الملف", "file"))
        self.output_name = QLineEdit("video_enhanced")
        export.addWidget(self.output_name)
        export.addWidget(icon_heading("مجلد الحفظ", "folder"))
        folder_row = QHBoxLayout()
        self.output_folder = QLineEdit(str(Path.home() / "Videos" / "VideoCraft Exports"))
        folder_row.addWidget(self.output_folder, 1)
        browse_folder = QPushButton("تغيير")
        browse_folder.setIcon(app_icon("folder"))
        browse_folder.clicked.connect(self.choose_output_folder)
        folder_row.addWidget(browse_folder)
        export.addLayout(folder_row)
        secondary_column.addWidget(export_card)

        journey_card, journey = card("وصفة الإخراج", "تتغير لحظيًا بحسب قراراتك.", "route")
        self.recipe_target = QLabel("—")
        self.recipe_target.setObjectName("AccentText")
        self.recipe_target.setWordWrap(True)
        self.recipe_steps = QLabel("اختر فيديو أو صورة لعرض وصفة المعالجة كاملة.")
        self.recipe_steps.setObjectName("Meta")
        self.recipe_steps.setWordWrap(True)
        journey.addWidget(self.recipe_target)
        journey.addWidget(self.recipe_steps)
        secondary_column.addWidget(journey_card)
        secondary_column.addStretch()

        columns.addLayout(primary_column, 7)
        columns.addLayout(secondary_column, 4)
        page_layout.addLayout(columns)

        self.job_card = QFrame()
        self.job_card.setObjectName("JobCard")
        job_layout = QVBoxLayout(self.job_card)
        set_margins(job_layout, 18)
        job_layout.setSpacing(10)
        job_top = QHBoxLayout()
        self.job_title = QLabel("جاري تجهيز الفيديو")
        self.job_title.setObjectName("CardTitle")
        self.job_detail = QLabel("0%")
        self.job_detail.setObjectName("AccentText")
        job_top.addWidget(self.job_title)
        job_top.addStretch()
        job_top.addWidget(self.job_detail)
        job_layout.addLayout(job_top)
        self.progress_bar = SmoothProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        job_layout.addWidget(self.progress_bar)
        job_actions = QHBoxLayout()
        self.job_note = QLabel("يمكنك متابعة العمل؛ المعالجة محلية بالكامل.")
        self.job_note.setObjectName("Hint")
        job_actions.addWidget(self.job_note)
        job_actions.addStretch()
        self.open_output_button = QPushButton("فتح المجلد")
        self.open_output_button.setIcon(app_icon("folder"))
        self.open_output_button.clicked.connect(self.open_output_folder)
        self.open_output_button.hide()
        self.play_output_button = QPushButton("مشاهدة النتيجة")
        self.play_output_button.setObjectName("Soft")
        self.play_output_button.setIcon(app_icon("play"))
        self.play_output_button.clicked.connect(self.play_output)
        self.play_output_button.hide()
        self.compare_output_button = QPushButton("مقارنة قبل / بعد")
        self.compare_output_button.setObjectName("Primary")
        self.compare_output_button.setIcon(app_icon("compare"))
        self.compare_output_button.clicked.connect(self.compare_output)
        self.compare_output_button.hide()
        self.cancel_button = QPushButton("إلغاء")
        self.cancel_button.setObjectName("Danger")
        self.cancel_button.setIcon(app_icon("cancel"))
        self.cancel_button.clicked.connect(self.cancel_job)
        job_actions.addWidget(self.open_output_button)
        job_actions.addWidget(self.play_output_button)
        job_actions.addWidget(self.compare_output_button)
        job_actions.addWidget(self.cancel_button)
        job_layout.addLayout(job_actions)
        self.job_strip = QWidget()
        job_strip_layout = QVBoxLayout(self.job_strip)
        job_strip_layout.setContentsMargins(30, 8, 30, 16)
        job_strip_layout.addWidget(self.job_card)
        self.job_strip.hide()

        action_card = QFrame()
        action_card.setObjectName("Card")
        action_layout = QHBoxLayout(action_card)
        action_layout.setContentsMargins(20, 16, 20, 16)
        action_text = QVBoxLayout()
        action_text.setSpacing(2)
        self.ready_title = QLabel("أضف فيديو أو صورة للبدء")
        self.ready_title.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.ready_note = QLabel("ستتمكن من المعاينة والمقارنة قبل حفظ النسخة النهائية.")
        self.ready_note.setObjectName("Hint")
        action_text.addWidget(self.ready_title)
        action_text.addWidget(self.ready_note)
        action_layout.addLayout(action_text)
        action_layout.addStretch()
        self.preview_button = QPushButton("معاينة 6 ثوانٍ")
        self.preview_button.setObjectName("Soft")
        self.preview_button.setIcon(app_icon("compare"))
        self.preview_button.setEnabled(False)
        self.preview_button.clicked.connect(self.start_preview)
        action_layout.addWidget(self.preview_button)
        self.export_button = QPushButton("تصدير الفيديو")
        self.export_button.setObjectName("Primary")
        self.export_button.setIcon(app_icon("export"))
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.start_export)
        action_layout.addWidget(self.export_button)
        page_layout.addWidget(action_card)

        footnote = QLabel(
            "ملاحظة صادقة: رفع الدقة يحسّن المقاس والترشيح، لكنه لا يستعيد تفاصيل لم تسجلها الكاميرا أصلًا."
        )
        footnote.setObjectName("Footnote")
        footnote.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footnote.setWordWrap(True)
        page_layout.addWidget(footnote)

        scroll.setWidget(page)
        root_layout.addWidget(scroll, 1)
        root_layout.addWidget(self.job_strip)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if getattr(self, "_entrance_done", False):
            return
        self._entrance_done = True
        self.setWindowOpacity(0.0)
        window_fade = QPropertyAnimation(self, b"windowOpacity", self)
        window_fade.setDuration(320)
        window_fade.setStartValue(0.0)
        window_fade.setEndValue(1.0)
        window_fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        _keep(self, window_fade)
        window_fade.start()
        panels = [
            w for w in self.findChildren(QFrame)
            if w.objectName() in {"Card", "DropZone"} and w.isVisible()
        ]
        panels.sort(key=lambda w: (w.mapTo(self, w.rect().topLeft()).y(), w.mapTo(self, w.rect().topLeft()).x()))
        for order, panel in enumerate(panels[:12]):
            fade_in(panel, 460, 90 + order * 55)

    def apply_theme(self, theme: str, *, persist: bool = True) -> None:
        self.current_theme = "light" if theme == "light" else "dark"
        if persist and self.isVisible():
            crossfade_snapshot(self)
        app = QApplication.instance()
        if app:
            app.setStyleSheet(style_for_theme(self.current_theme))
        if hasattr(self, "root_widget"):
            self.root_widget.set_theme(self.current_theme)
        if hasattr(self, "theme_button"):
            if self.current_theme == "dark":
                self.theme_button.setText("الوضع الفاتح")
                self.theme_button.setIcon(app_icon("sun"))
            else:
                self.theme_button.setText("الوضع الداكن")
                self.theme_button.setIcon(app_icon("moon"))
        if persist:
            self.settings_store.setValue("theme", self.current_theme)
            self.settings_store.sync()

    def toggle_theme(self) -> None:
        self.apply_theme("light" if self.current_theme == "dark" else "dark")

    def play_completion_sound(self) -> None:
        if not self.completion_sound_enabled:
            return

        try:
            if os.name == "nt":
                import winsound

                winsound.PlaySound(
                    "SystemAsterisk",
                    winsound.SND_ALIAS
                    | winsound.SND_ASYNC
                    | winsound.SND_NODEFAULT,
                )
            else:
                QApplication.beep()
        except (ImportError, OSError, RuntimeError):
            QApplication.beep()

    def open_preferences(self) -> None:
        dialog = PreferencesDialog(
            self.current_theme,
            self.strict_export,
            self.auto_compare,
            self.completion_sound_enabled,
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.strict_export = dialog.strict_check.isChecked()
            self.auto_compare = dialog.auto_compare_check.isChecked()
            self.completion_sound_enabled = dialog.completion_sound_check.isChecked()
            self.settings_store.setValue("strict_export", self.strict_export)
            self.settings_store.setValue("auto_compare", self.auto_compare)
            self.settings_store.setValue(
                "completion_sound", self.completion_sound_enabled
            )
            self.apply_theme(dialog.selected_theme)
            self.settings_store.sync()
            self.update_summary()

    def connect_summary_updates(self) -> None:
        for button in self.resolution_buttons.values():
            button.toggled.connect(self.update_summary)
        self.fps_slider.valueChanged.connect(self.update_summary)
        self.interpolate_check.toggled.connect(self.update_summary)
        self.color_style.currentIndexChanged.connect(self.update_summary)
        self.codec_combo.currentIndexChanged.connect(self.update_summary)
        self.image_format_combo.currentIndexChanged.connect(self.update_summary)
        self.quality_combo.currentIndexChanged.connect(self.update_summary)
        self.ai_enabled_check.toggled.connect(self.update_summary)
        self.ai_model_combo.currentIndexChanged.connect(self.update_summary)
        self.photo_restore_check.toggled.connect(self.update_photo_restore_mode)
        self.photo_restore_profile_combo.currentIndexChanged.connect(self.update_summary)
        self.photo_restore_scale_combo.currentIndexChanged.connect(self.update_summary)
        self.ai_fast_mode_check.toggled.connect(self.update_summary)
        self.ai_motion_check.toggled.connect(self.update_summary)
        for slider in (
            self.denoise_slider,
            self.sharpness_slider,
            self.brightness_slider,
            self.contrast_slider,
            self.saturation_slider,
        ):
            slider.value_changed.connect(self.update_summary)
        for checkbox in (
            self.stabilize_check,
            self.deinterlace_check,
            self.audio_normalize_check,
            self.audio_clean_check,
        ):
            checkbox.toggled.connect(self.update_summary)

    def is_image_source(self) -> bool:
        return bool(self.media_info and self.media_info.is_image)

    def set_image_mode(self, image: bool) -> None:
        """Fold away everything that only means something for moving pictures."""
        for widget in self.motion_widgets:
            widget.setEnabled(not image)
        self.motion_image_note.setVisible(image)
        self.resolution_image_note.setVisible(image)
        self.audio_card.setEnabled(not image)
        self.stabilize_check.setEnabled(not image)
        self.deinterlace_check.setEnabled(not image)
        self.ai_motion_check.setEnabled(self.ai_ready and not image)
        self.ai_enabled_check.setText(
            "تشغيل تحسين AI للصورة" if image else "تشغيل تحسين AI للفيديو"
        )
        self.photo_restore_row.setVisible(image)
        self.update_photo_restore_mode()

        self.codec_heading.setVisible(not image)
        self.codec_combo.setVisible(not image)
        self.image_format_heading.setVisible(image)
        self.image_format_combo.setVisible(image)

        self.source_fps_label.setVisible(not image)
        self.source_duration_label.setVisible(not image)
        self.preview_source_button.setText(
            "عرض الصورة الأصلية" if image else "مشاهدة الفيديو الأصلي"
        )
        self.preview_button.setText("معاينة سريعة" if image else "معاينة 6 ثوانٍ")
        self.export_button.setText("تصدير الصورة" if image else "تصدير الفيديو")
        self.play_output_button.setText("عرض النتيجة" if image else "مشاهدة النتيجة")

    def update_photo_restore_mode(self, *_args) -> None:
        """Keep the photo-only restoration profile predictable and neutral."""
        image = self.is_image_source()
        selected_profile = str(self.photo_restore_profile_combo.currentData())
        selected_profile_ready = (
            selected_profile != "waifu2x_anime" or self.waifu2x_ready
        )
        active = (
            image
            and self.photo_restore_check.isChecked()
            and self.ai_ready
            and selected_profile_ready
        )
        self.photo_restore_profile_combo.setEnabled(self.ai_ready and image)
        self.photo_restore_scale_combo.setEnabled(self.ai_ready and image)
        self.ai_model_combo.setEnabled(self.ai_ready and not active)
        if active:
            self.ai_enabled_check.setChecked(True)
        self.update_summary()

    def apply_max_enhancement(self, *_args, persist: bool = True) -> None:
        """Apply the strongest profile while keeping color controls deliberate."""
        self.denoise_slider.setValue(100)
        self.sharpness_slider.setValue(100)
        self.brightness_slider.setValue(0)
        self.contrast_slider.setValue(20)
        self.saturation_slider.setValue(16)
        natural_style = self.color_style.findData("natural")
        if natural_style >= 0:
            self.color_style.setCurrentIndex(natural_style)
        maximum_quality = self.quality_combo.findData(14)
        if maximum_quality >= 0:
            self.quality_combo.setCurrentIndex(maximum_quality)
        self.interpolate_check.setChecked(True)
        if hasattr(self, "ai_enabled_check") and self.ai_ready:
            self.ai_enabled_check.setChecked(True)
            self.ai_fast_mode_check.setChecked(False)
            extreme_model = self.ai_model_combo.findData("anime_extreme")
            if extreme_model >= 0:
                self.ai_model_combo.setCurrentIndex(extreme_model)
        self.max_quality_badge.setText("✓ أقصى تحسين نشط")
        if persist:
            self.settings_store.setValue("enhancement_profile_version", 2)
            self.save_preferences()
        self.update_summary()

    def apply_quick_preset(self, key: str, persist: bool = True) -> None:
        """One-click starting points for people who don't want to tweak dials."""

        def set_resolution(value: str) -> None:
            button = self.resolution_buttons.get(value)
            if button:
                button.setChecked(True)

        def set_quality(value: int) -> None:
            index = self.quality_combo.findData(value)
            if index >= 0:
                self.quality_combo.setCurrentIndex(index)

        def set_color(value: str) -> None:
            index = self.color_style.findData(value)
            if index >= 0:
                self.color_style.setCurrentIndex(index)

        def set_ai_model(value: str) -> None:
            index = self.ai_model_combo.findData(value)
            if index >= 0:
                self.ai_model_combo.setCurrentIndex(index)

        if key == "best":
            self.apply_max_enhancement(persist=False)
        elif key == "fast":
            set_resolution("1080p")
            self.fps_slider.setValue(60)
            self.interpolate_check.setChecked(True)
            self.denoise_slider.setValue(18)
            self.sharpness_slider.setValue(22)
            self.brightness_slider.setValue(0)
            self.contrast_slider.setValue(20)
            self.saturation_slider.setValue(16)
            set_color("natural")
            set_quality(18)
            self.deinterlace_check.setChecked(False)
            self.stabilize_check.setChecked(False)
            if self.ai_ready:
                self.ai_enabled_check.setChecked(True)
                self.ai_fast_mode_check.setChecked(True)
                self.ai_motion_check.setChecked(False)
                set_ai_model("anime_fast")
        elif key == "anime":
            set_resolution("source")
            self.fps_slider.setValue(60)
            self.interpolate_check.setChecked(True)
            self.denoise_slider.setValue(30)
            self.sharpness_slider.setValue(45)
            self.brightness_slider.setValue(0)
            self.contrast_slider.setValue(28)
            self.saturation_slider.setValue(24)
            set_color("bright")
            set_quality(16)
            self.deinterlace_check.setChecked(False)
            self.stabilize_check.setChecked(False)
            if self.ai_ready:
                self.ai_enabled_check.setChecked(True)
                self.ai_fast_mode_check.setChecked(True)
                self.ai_motion_check.setChecked(False)
                set_ai_model("anime_extreme")
        elif key == "social":
            set_resolution("1080p")
            self.fps_slider.setValue(30)
            self.interpolate_check.setChecked(True)
            self.denoise_slider.setValue(22)
            self.sharpness_slider.setValue(38)
            self.brightness_slider.setValue(4)
            self.contrast_slider.setValue(26)
            self.saturation_slider.setValue(20)
            set_color("natural")
            set_quality(21)
            self.deinterlace_check.setChecked(False)
            self.stabilize_check.setChecked(False)
            if self.ai_ready:
                self.ai_enabled_check.setChecked(True)
                self.ai_fast_mode_check.setChecked(True)
                self.ai_motion_check.setChecked(False)
                set_ai_model("anime_shader")
        elif key == "vintage":
            set_resolution("1080p")
            self.fps_slider.setValue(30)
            self.interpolate_check.setChecked(True)
            self.denoise_slider.setValue(65)
            self.sharpness_slider.setValue(40)
            self.brightness_slider.setValue(0)
            self.contrast_slider.setValue(10)
            self.saturation_slider.setValue(8)
            set_color("warm")
            set_quality(18)
            self.deinterlace_check.setChecked(True)
            self.stabilize_check.setChecked(True)
            if self.ai_ready:
                self.ai_enabled_check.setChecked(True)
                self.ai_fast_mode_check.setChecked(True)
                self.ai_motion_check.setChecked(False)
                set_ai_model("anime_fast")
        elif key == "photo":
            set_resolution("source")
            self.denoise_slider.setValue(0)
            self.sharpness_slider.setValue(0)
            self.brightness_slider.setValue(0)
            self.contrast_slider.setValue(0)
            self.saturation_slider.setValue(0)
            set_color("none")
            set_quality(14)
            if self.ai_ready:
                profile = self.photo_restore_profile_combo.findData("anime_supreme")
                if profile >= 0:
                    self.photo_restore_profile_combo.setCurrentIndex(profile)
                scale = self.photo_restore_scale_combo.findData(4)
                if scale >= 0:
                    self.photo_restore_scale_combo.setCurrentIndex(scale)
                self.photo_restore_check.setChecked(True)
        if persist:
            self.save_preferences()
        self.update_summary()

    def current_resolution(self) -> str:
        checked = self.resolution_group.checkedButton()
        return str(checked.property("resolution")) if checked else "1080p"

    def collect_settings(self) -> ExportSettings:
        image = self.is_image_source()
        photo_restore_model = str(self.photo_restore_profile_combo.currentData())
        photo_restore_model_ready = (
            photo_restore_model != "waifu2x_anime" or self.waifu2x_ready
        )
        photo_restore = (
            image
            and self.photo_restore_check.isChecked()
            and self.ai_ready
            and photo_restore_model_ready
        )
        return ExportSettings(
            resolution=self.current_resolution(),
            fps=self.fps_slider.value(),
            interpolate=self.interpolate_check.isChecked() and not image,
            denoise=self.denoise_slider.value(),
            sharpness=self.sharpness_slider.value(),
            brightness=self.brightness_slider.value(),
            contrast=self.contrast_slider.value(),
            saturation=self.saturation_slider.value(),
            color_style=str(self.color_style.currentData()),
            deinterlace=self.deinterlace_check.isChecked() and not image,
            stabilize=self.stabilize_check.isChecked() and not image,
            audio_normalize=self.audio_normalize_check.isChecked() and not image,
            audio_clean=self.audio_clean_check.isChecked() and not image,
            codec=str(self.codec_combo.currentData()),
            quality=int(self.quality_combo.currentData()),
            hardware=self.hardware_check.isChecked() and self.nvenc_ready,
            container="mp4",
            safe_mode=self.strict_export,
            ai_enabled=(self.ai_enabled_check.isChecked() or photo_restore) and self.ai_ready,
            ai_model=photo_restore_model if photo_restore else str(self.ai_model_combo.currentData()),
            ai_motion=self.ai_motion_check.isChecked() and self.ai_ready and not image,
            ai_fast_mode=self.ai_fast_mode_check.isChecked() and self.ai_ready,
            photo_restore=photo_restore,
            photo_restore_scale=int(self.photo_restore_scale_combo.currentData() or 2),
        )

    def update_summary(self, *_args) -> None:
        settings = self.collect_settings()
        maximum_profile = (
            settings.denoise == 100
            and settings.sharpness == 100
            and settings.brightness == 0
            and settings.contrast == 20
            and settings.saturation == 16
            and settings.quality == 14
        )
        if hasattr(self, "max_quality_badge"):
            self.max_quality_badge.setText(
                "✓ أقصى تحسين نشط" if maximum_profile else "أقصى تحسين متاح"
            )
        if hasattr(self, "reliability_badge"):
            self.reliability_badge.setText(
                "✓  وضع التصدير الموثوق مفعّل"
                if settings.safe_mode
                else "الوضع اليدوي — بدون إعادة محاولة"
            )
        image = self.is_image_source()
        resolution_names = {"source": "الدقة الأصلية", "1080p": "1080p", "2k": "2K", "4k": "4K"}
        target = resolution_names.get(settings.resolution, settings.resolution)
        if self.media_info:
            box = (
                image_target_box(self.media_info, settings.resolution)
                if image
                else target_box(self.media_info, settings.resolution)
            )
            if box:
                target = f"{target} · حتى {box[0]}×{box[1]}"
            else:
                target = f"الأصلية · {self.media_info.display_width}×{self.media_info.display_height}"
        ai_name = "  •  AI محلي" if settings.ai_enabled else ""
        if image:
            format_name = str(self.image_format_combo.currentData() or "png").upper()
            self.recipe_target.setText(f"{target}  •  صورة {format_name}{ai_name}")
        else:
            codec_name = "H.265" if settings.codec == "h265" else "H.264"
            self.recipe_target.setText(
                f"{target}  •  {settings.fps} fps  •  {codec_name}{ai_name}"
            )
        steps: list[str] = []
        if settings.ai_enabled:
            model_names = {
                "anime_extreme": "استعادة AI EXTREME",
                "anime_fast": "استعادة AnimeVideo AI",
                "anime_shader": "تحسين Anime4K GAN",
                "general": "استعادة Real‑ESRGAN",
                "photo_restore": "ترميم الصور الواقعي بلا فلتر",
                "anime_supreme": "ترميم Real-CUGAN Pro ×3 القوي",
                "waifu2x_anime": "ترميم البكسلات للرسوم بـ Waifu2x",
            }
            steps.append(model_names.get(settings.ai_model, "تحسين AI"))
        if settings.ai_motion:
            steps.append("حركة RIFE 4.26")
        if settings.ai_enabled and settings.ai_fast_mode:
            steps.append("ترميز AI سريع")
        if settings.denoise and not settings.photo_restore:
            steps.append("تنظيف التشويش")
        if settings.sharpness and not settings.photo_restore:
            steps.append("استعادة الحواف")
        if not settings.photo_restore and (settings.color_style != "none" or any(
            (settings.brightness, settings.contrast, settings.saturation)
        )):
            steps.append("موازنة اللون والضوء")
        elif settings.photo_restore:
            steps.append("الألوان والإضاءة الأصلية بلا فلتر")
        if settings.interpolate:
            steps.append("حركة بينية عند الحاجة")
        if settings.stabilize:
            steps.append("تثبيت الاهتزاز")
        if settings.deinterlace:
            steps.append("إصلاح الخطوط")
        if settings.audio_normalize:
            steps.append("موازنة الصوت")
        if settings.safe_mode:
            steps.append("مسار موثوق")
        self.recipe_steps.setText("  ←  ".join(steps) if steps else "تحويل محافظ بلا تحسينات إضافية")

    def load_files(self, paths: list) -> None:
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.information(self, "المعالجة جارية", "ألغِ المهمة الحالية قبل تغيير الملف.")
            return
        paths = [str(p) for p in paths if Path(p).is_file()]
        if not paths:
            return
        # A batch shares one set of settings, so keep it to one kind of media.
        first_is_image = Path(paths[0]).suffix.lower() in IMAGE_SUFFIXES
        same_kind = [p for p in paths if (Path(p).suffix.lower() in IMAGE_SUFFIXES) == first_is_image]
        skipped = len(paths) - len(same_kind)
        self.batch_paths = same_kind if len(same_kind) > 1 else []
        self.batch_panel.kind = "صور" if first_is_image else "فيديوهات"
        if not self.load_media(same_kind[0]):
            self.batch_paths = []
            return
        self.batch_skipped = skipped
        self.batch_panel.set_items(self.batch_paths, first_is_image)
        if self.batch_paths:
            self.batch_panel.set_active(0)
            self.refresh_batch_header()

    def refresh_batch_header(self) -> None:
        if not self.batch_paths:
            return
        count = len(self.batch_paths)
        kind = self.batch_panel.kind
        self.drop_zone.title.setText(f"{count} {kind} في المجموعة")
        note = f"  •  تم تجاهل {self.batch_skipped} ملف من نوع مختلف" if self.batch_skipped else ""
        self.drop_zone.meta.setText(f"المعروض الآن: {Path(self.source_path or '').name}{note}")
        self.drop_zone.select_button.setText("تغيير المجموعة")
        self.export_button.setText(f"تصدير الكل ({count})")
        self.output_name.setEnabled(False)
        self.output_name.setText("يُحفظ كل ملف باسمه الأصلي + _محسن")

    def select_batch_item(self, index: int) -> None:
        if self.batch_running or not (0 <= index < len(self.batch_paths)):
            return
        if self.load_media(self.batch_paths[index]):
            self.batch_panel.set_active(index)
            self.refresh_batch_header()

    def remove_batch_item(self, index: int) -> None:
        if self.batch_running or not (0 <= index < len(self.batch_paths)):
            return
        remaining = self.batch_paths[:index] + self.batch_paths[index + 1:]
        self.batch_skipped = 0
        self.load_files(remaining)

    def clear_batch(self) -> None:
        if self.batch_running:
            return
        current = self.source_path
        self.batch_paths = []
        self.batch_panel.set_items([], True)
        if current:
            self.load_media(current)

    def load_media(self, path: str, *, quiet: bool = False) -> bool:
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.information(self, "المعالجة جارية", "ألغِ المهمة الحالية قبل تغيير الملف.")
            return False
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            info = probe_media(path)
        except VideoEngineError as exc:
            if not quiet:
                QMessageBox.warning(self, "تعذّر فتح الملف", str(exc))
            return False
        finally:
            QApplication.restoreOverrideCursor()

        self.media_info = info
        self.source_path = info.path
        self.set_image_mode(info.is_image)
        thumbnail = self.make_thumbnail(info)
        self.drop_zone.show_media(info, thumbnail)
        self.source_resolution_label.setText(f"الدقة: {info.display_width}×{info.display_height}")
        self.source_fps_label.setText(f"الإطارات: {info.fps:.2f} fps")
        self.source_duration_label.setText(f"المدة: {format_duration(info.duration)}")
        if info.is_image:
            self.source_codec_label.setText(f"الصيغة: {info.video_codec.upper()}")
        else:
            audio = f" / {info.audio_codec.upper()}" if info.audio_codec else " / بدون صوت"
            self.source_codec_label.setText(f"الترميز: {info.video_codec.upper()}{audio}")
        self.preview_source_button.setEnabled(True)
        self.preview_button.setEnabled(True)
        self.export_button.setEnabled(True)
        self.ready_title.setText("الإعدادات جاهزة لقرارك الأخير")
        self.ready_note.setText(
            "جرّب المعاينة السريعة لترى الفرق، أو ابدأ التصدير مباشرة."
            if info.is_image
            else "جرّب المعاينة القصيرة، أو ابدأ التصدير الكامل مباشرة."
        )
        clean_stem = re.sub(r"[^\w\- ]+", "", Path(path).stem, flags=re.UNICODE).strip()
        default_stem = "image" if info.is_image else "video"
        self.output_name.setText(f"{clean_stem or default_stem}_محسن")
        if self.batch_paths:
            self.refresh_batch_header()
        else:
            self.output_name.setEnabled(True)
        self.update_summary()
        return True

    def make_thumbnail(self, info: MediaInfo) -> QPixmap | None:
        if info.is_image:
            pixmap = QPixmap(info.path)
            if pixmap.isNull():
                return None
            return pixmap.scaled(
                520,
                300,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return None
        cache_dir = Path(tempfile.gettempdir()) / "VideoCraftStudio"
        cache_dir.mkdir(parents=True, exist_ok=True)
        thumb = cache_dir / "source_thumb.jpg"
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{min(max(info.duration * 0.18, 0), max(info.duration - 0.1, 0)):.3f}",
            "-i",
            info.path,
            "-frames:v",
            "1",
            "-vf",
            "scale=520:300:force_original_aspect_ratio=decrease",
            "-q:v",
            "3",
            str(thumb),
        ]
        try:
            subprocess.run(command, capture_output=True, timeout=20, check=False)
            return QPixmap(str(thumb)) if thumb.exists() else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    def preview_source(self) -> None:
        if not self.source_path:
            return
        if self.is_image_source():
            ImageViewDialog(self.source_path, "الصورة الأصلية", self).exec()
            return
        PreviewDialog(self.source_path, "الفيديو الأصلي", self).exec()

    def choose_output_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "اختر مجلد الحفظ", self.output_folder.text() or str(Path.home() / "Videos")
        )
        if selected:
            self.output_folder.setText(selected)

    def output_path(self) -> Path | None:
        folder_text = self.output_folder.text().strip()
        name = self.output_name.text().strip()
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(" .")
        if not folder_text or not name:
            QMessageBox.warning(self, "بيانات الحفظ ناقصة", "اختر مجلدًا واكتب اسمًا صالحًا للملف.")
            return None
        folder = Path(folder_text).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "تعذّر إنشاء المجلد", str(exc))
            return None
        if self.is_image_source():
            suffix = IMAGE_OUTPUT_SUFFIXES.get(
                str(self.image_format_combo.currentData() or "png"), ".png"
            )
            return folder / f"{name}{suffix}"
        return folder / f"{name}.mp4"

    def preflight_output(self, output: Path, settings: ExportSettings) -> bool:
        if not self.media_info:
            return False
        probe_file = output.parent / f".videocraft_write_{os.getpid()}.tmp"
        try:
            probe_file.write_bytes(b"ok")
            probe_file.unlink(missing_ok=True)
        except OSError as exc:
            QMessageBox.critical(
                self,
                "مجلد الحفظ غير قابل للكتابة",
                f"اختر مجلدًا آخر ثم أعد المحاولة.\n\n{exc}",
            )
            return False

        image = self.media_info.is_image
        box = (
            image_target_box(self.media_info, settings.resolution)
            if image
            else target_box(self.media_info, settings.resolution)
        )
        width, height = box or (self.media_info.display_width, self.media_info.display_height)
        if image:
            # A lossless PNG is the worst case: roughly three bytes per pixel
            # before compression, with room for the neural intermediate.
            estimated_bytes = max(8 * 1024 * 1024, int(width * height * 3 * 1.2))
        else:
            pixels_factor = max(0.25, (width * height) / (1920 * 1080))
            fps_factor = max(0.5, settings.fps / 30)
            quality_factor = max(0.7, (28 - settings.quality) / 10)
            estimated_mbps = min(180.0, 9.0 * (pixels_factor * fps_factor) ** 0.72 * quality_factor)
            estimated_bytes = max(
                80 * 1024 * 1024,
                int(self.media_info.duration * estimated_mbps * 1_000_000 / 8 * 1.35),
            )
        try:
            free_bytes = shutil.disk_usage(output.parent).free
        except OSError:
            free_bytes = estimated_bytes * 2
        if free_bytes < estimated_bytes:
            QMessageBox.critical(
                self,
                "المساحة غير كافية",
                f"الإعداد الحالي قد يحتاج قرابة {format_size(estimated_bytes)}، "
                f"والمتاح {format_size(free_bytes)} فقط. اختر قرصًا آخر.",
            )
            return False
        if settings.ai_enabled:
            ai_temp_bytes = max(
                (64 if image else 512) * 1024 * 1024,
                int(estimated_bytes * (2.2 if settings.ai_motion else 1.25)),
            )
            try:
                temp_free = shutil.disk_usage(tempfile.gettempdir()).free
            except OSError:
                temp_free = ai_temp_bytes * 2
            if temp_free < ai_temp_bytes:
                QMessageBox.critical(
                    self,
                    "المساحة المؤقتة لا تكفي لمحرك AI",
                    f"مراحل AI قد تحتاج قرابة {format_size(ai_temp_bytes)} على قرص النظام، "
                    f"والمتاح {format_size(temp_free)} فقط. حرّر مساحة أو أوقف AI.",
                )
                return False
        return True

    def start_preview(self) -> None:
        if not self.media_info or not self.source_path:
            return
        cache_dir = Path(tempfile.gettempdir()) / "VideoCraftStudio"
        cache_dir.mkdir(parents=True, exist_ok=True)
        if self.media_info.is_image:
            # A still is processed whole; the "preview" is the real result kept
            # outside the export folder until the user commits to it.
            self.hardware_retry_used = False
            self.safe_retry_used = False
            self.cpu_retry_used = False
            self.start_job("preview", cache_dir / "preview.png", self.collect_settings(), 0.0)
            return
        preview_path = cache_dir / "preview.mp4"
        start = max(0.0, min(self.media_info.duration * 0.25, max(0.0, self.media_info.duration - 6)))
        self.hardware_retry_used = False
        self.safe_retry_used = False
        self.cpu_retry_used = False
        self.start_job("preview", preview_path, self.collect_settings(), start)

    def start_export(self) -> None:
        if not self.media_info or not self.source_path:
            return
        if self.batch_paths and not self.batch_running:
            self.batch_running = True
            self.batch_index = 0
            self.batch_done = []
            self.batch_failed = []
            self.save_preferences()
            for i in range(len(self.batch_paths)):
                self.batch_panel.set_state(i, "waiting")
            self.batch_panel.set_locked(True)
            self.batch_panel.open_button.hide()
            self.batch_panel.set_progress(0, 0, True)
            self.run_next_batch_item()
            return
        output = self.output_path()
        if not output:
            return
        if Path(self.source_path).resolve() == output.resolve():
            QMessageBox.warning(
                self, "اسم غير صالح", "اختر اسمًا مختلفًا حتى لا يُستبدل الملف الأصلي."
            )
            return
        if output.exists():
            answer = QMessageBox.question(
                self,
                "الملف موجود",
                f"يوجد ملف بهذا الاسم:\n{output.name}\n\nهل تريد استبداله؟",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        selected_settings = self.collect_settings()
        if not self.preflight_output(output, selected_settings):
            return
        self.save_preferences()
        self.hardware_retry_used = False
        self.safe_retry_used = False
        self.cpu_retry_used = False
        self.start_job("export", output, selected_settings, 0.0)

    def run_next_batch_item(self) -> None:
        while self.batch_running and self.batch_index < len(self.batch_paths):
            path = self.batch_paths[self.batch_index]
            self.batch_index += 1
            current = self.batch_index - 1
            if not self.load_media(path, quiet=True):
                self.batch_failed.append(Path(path).name)
                self.batch_panel.set_state(current, "failed")
                self.batch_panel.set_progress(len(self.batch_done), len(self.batch_failed), True)
                continue
            self.batch_panel.set_active(current)
            self.batch_panel.set_state(current, "running")
            clean_stem = re.sub(r"[^\w\- ]+", "", Path(path).stem, flags=re.UNICODE).strip()
            self.output_name.setText(f"{clean_stem or 'file'}_محسن")
            output = self.output_path()
            self.output_name.setText("يُحفظ كل ملف باسمه الأصلي + _محسن")
            if not output:
                self.finish_batch()
                return
            counter = 2
            base = output
            while output.exists() or output.resolve() == Path(path).resolve():
                output = base.with_name(f"{base.stem}_{counter}{base.suffix}")
                counter += 1
            settings = self.collect_settings()
            if self.batch_index == 1 and not self.preflight_output(output, settings):
                self.finish_batch()
                return
            self.hardware_retry_used = False
            self.safe_retry_used = False
            self.cpu_retry_used = False
            self.start_job("export", output, settings, 0.0)
            self.job_title.setText(
                f"الدفعة {self.batch_index}/{len(self.batch_paths)}  •  {self.job_title.text()}"
            )
            return
        self.finish_batch()

    def finish_batch(self) -> None:
        if not self.batch_running:
            return
        self.batch_running = False
        total = len(self.batch_paths)
        self.batch_panel.set_locked(False)
        self.batch_panel.set_progress(len(self.batch_done), len(self.batch_failed), False)
        self.batch_panel.open_button.setVisible(bool(self.batch_done))
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(1000)
        self.cancel_button.hide()
        self.job_title.setText(f"اكتملت الدفعة: {len(self.batch_done)} من {total}")
        if self.batch_failed:
            self.job_note.setText("تعذّر: " + "، ".join(self.batch_failed[:6]))
            QMessageBox.warning(
                self,
                "انتهت الدفعة مع أخطاء",
                f"نجح {len(self.batch_done)} من {total}.\n\nتعذّرت معالجة:\n"
                + "\n".join(self.batch_failed),
            )
        else:
            self.job_note.setText("حُفظت كل الملفات في مجلد الحفظ.")
        if self.batch_done:
            self.open_output_button.show()
            self.play_completion_sound()
        self.export_button.setText(f"تصدير الكل ({total})")

    def start_job(
        self, job: str, output: Path, settings: ExportSettings, preview_start: float = 0.0
    ) -> None:
        if not self.media_info or not self.source_path:
            return
        use_ai = bool(settings.ai_enabled and self.ai_ready)
        image = self.media_info.is_image
        try:
            if use_ai:
                command = build_ai_pipeline_command(
                    self.source_path,
                    output,
                    self.media_info,
                    settings,
                    preview=job == "preview" and not image,
                    preview_start=preview_start,
                )
            elif image:
                command = build_image_ffmpeg_command(
                    self.source_path,
                    output,
                    self.media_info,
                    settings,
                )
            else:
                command = build_ffmpeg_command(
                    self.source_path,
                    output,
                    self.media_info,
                    settings,
                    preview=job == "preview",
                    preview_start=preview_start,
                )
        except (VideoEngineError, RuntimeError) as exc:
            QMessageBox.critical(self, "أداة المعالجة غير متاحة", str(exc))
            return

        self.active_job = job
        self.active_output = str(output)
        self.active_settings = settings
        self.active_ai = use_ai
        self.active_is_image = image
        self.current_ai_stage = (
            "upscale"
            if use_ai and image
            else "prepare"
            if use_ai and job == "preview"
            else "upscale"
            if use_ai
            else ""
        )
        self.active_preview_start = preview_start
        self.active_total_duration = (
            1.0
            if image
            else min(6.0, max(0.1, self.media_info.duration - preview_start))
            if job == "preview"
            else max(0.1, self.media_info.duration)
        )
        self.cancel_requested = False
        self.process_log.clear()
        self._process_buffer = ""
        self.progress_seen = False
        self.current_speed = ""
        self.progress_bar.setRange(0, 0)
        reveal(self.job_strip)
        if image:
            base_job_title = (
                "محرك AI يعيد بناء الصورة" if use_ai else "نعالج الصورة"
            )
        else:
            base_job_title = (
                "نحضّر معاينة AI قصيرة"
                if use_ai and job == "preview"
                else "محرك AI يعيد بناء الفيديو"
                if use_ai
                else "نحضّر معاينة قصيرة"
                if job == "preview"
                else "نصنع النسخة النهائية"
            )
        if self.safe_retry_used or self.cpu_retry_used or self.hardware_retry_used:
            base_job_title += "  •  استرداد تلقائي"
        self.job_title.setText(base_job_title)
        self.job_detail.setText("يعمل الآن • 00:00")
        if self.safe_retry_used or self.cpu_retry_used or self.hardware_retry_used:
            self.job_note.setText("المحاولة السابقة لم تكتمل؛ يعمل الآن المسار الموثوق بنفس المقاس وFPS.")
        else:
            self.job_note.setText(
                "يجهّز محرك AI الصورة على بطاقة الرسوم؛ العملية قصيرة عادةً."
                if use_ai and image
                else "نطبّق التنقية واللون والمقاس على الصورة."
                if image
                else "يجهّز محرك AI الإطارات على بطاقة الرسوم؛ ستظهر النسبة بعد بدء النموذج."
                if use_ai
                else "بدأت المعالجة؛ يتحرك الشريط بشكل مستمر حتى تصل أول قراءة زمنية."
                if job == "preview"
                else "بدأت المعالجة؛ تحليل الحركة قد يستغرق قليلًا قبل ظهور النسبة الدقيقة."
            )
        self.cancel_button.show()
        self.cancel_button.setEnabled(True)
        self.open_output_button.hide()
        self.play_output_button.hide()
        self.compare_output_button.hide()
        self.set_controls_busy(True)
        self.progress_clock.start()
        self.progress_timer.start()

        self.process = QProcess(self)
        process_environment = QProcessEnvironment.systemEnvironment()
        vulkan_manifest = vulkan_driver_manifest()
        if vulkan_manifest and not process_environment.value("VK_DRIVER_FILES"):
            # Some NVIDIA installations leave the ICD JSON in DriverStore but
            # omit the Khronos registry value. Passing it only to the AI child
            # restores Vulkan without changing machine-wide Windows settings.
            process_environment.insert("VK_DRIVER_FILES", str(vulkan_manifest))
            process_environment.insert("VK_ICD_FILENAMES", str(vulkan_manifest))
        self.process.setProcessEnvironment(process_environment)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.readyReadStandardOutput.connect(self.read_process_output)
        self.process.readyReadStandardError.connect(self.read_process_error)
        self.process.finished.connect(self.process_finished)
        self.process.errorOccurred.connect(self.process_error)
        self.process.start(command[0], command[1:])

    def set_controls_busy(self, busy: bool) -> None:
        self.preview_button.setEnabled(not busy and self.media_info is not None)
        self.export_button.setEnabled(not busy and self.media_info is not None)
        self.drop_zone.select_button.setEnabled(not busy)

    def read_process_output(self) -> None:
        if not self.process:
            return
        chunk = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._process_buffer += chunk.replace("\r", "\n")
        lines = self._process_buffer.split("\n")
        self._process_buffer = lines.pop() if lines else ""
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("ai_progress="):
                try:
                    self.set_progress_ratio(float(line.split("=", 1)[1]))
                except ValueError:
                    pass
            elif line.startswith("ai_stage="):
                self.current_ai_stage = line.split("=", 1)[1].strip()
            elif line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                if self.active_is_image:
                    # One frame carries no useful elapsed time; the bar stays
                    # indeterminate until the encoder reports the end.
                    continue
                try:
                    elapsed = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    continue
                self.set_progress_ratio(elapsed / self.active_total_duration)
            elif line.startswith("frame=") and not self.progress_seen and not self.active_is_image:
                try:
                    frame = int(line.split("=", 1)[1])
                    target_fps = self.active_settings.fps if self.active_settings else 30
                    self.set_progress_ratio(frame / (self.active_total_duration * target_fps))
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
            elif line.startswith("speed="):
                self.current_speed = line.split("=", 1)[1].strip()
            elif line == "progress=end":
                self.set_progress_ratio(1.0)
        self.update_progress_status()

    def read_process_error(self) -> None:
        if not self.process:
            return
        chunk = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        for raw_line in chunk.replace("\r", "\n").split("\n"):
            line = raw_line.strip()
            if line:
                self.process_log.append(line)
        self.process_log = self.process_log[-120:]

    def set_progress_ratio(self, ratio: float) -> None:
        ratio = max(0.0, min(1.0, ratio))
        if ratio <= 0 and not self.progress_seen:
            return
        if not self.progress_seen:
            self.progress_seen = True
            self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(int(ratio * 1000))

    def update_progress_status(self) -> None:
        if not self.process or self.process.state() == QProcess.ProcessState.NotRunning:
            return
        elapsed_text = format_duration(self.progress_clock.elapsed() / 1000)
        if self.progress_seen:
            percent = self.progress_bar.value() / 10
            self.job_detail.setText(f"{percent:.0f}%  •  {elapsed_text}")
            speed_note = f" • السرعة {self.current_speed}" if self.current_speed else ""
            if self.active_ai:
                stage_notes = {
                    "prepare": "تجهيز عينة نظيفة لمحرك AI",
                    "upscale": "استعادة التفاصيل والتكبير العصبي على GPU",
                    "motion": "توليد الحركة البينية بنموذج RIFE 4.26",
                    "encode": "الترميز النهائي وضبط الدقة وFPS",
                    "done": "اكتملت جميع مراحل الذكاء الاصطناعي",
                }
                self.job_note.setText(
                    stage_notes.get(self.current_ai_stage, "محرك AI يعالج الإطارات")
                    + speed_note
                )
            else:
                self.job_note.setText(f"تم إنشاء جزء من الملف النهائي{speed_note}")
        else:
            self.job_detail.setText(f"يعمل الآن  •  {elapsed_text}")
            self.job_note.setText(
                "يحمّل نموذج AI ويجهز ذاكرة Vulkan؛ قد تستغرق البداية عدة ثوانٍ."
                if self.active_ai
                else "يجري تحليل اللقطات وتجهيز أول إطارات؛ الشريط المتحرك يعني أن المهمة تعمل."
            )

    def process_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        output = Path(self.active_output) if self.active_output else None
        job = self.active_job
        self.process = None
        self.progress_timer.stop()
        self.set_controls_busy(False)

        if self.cancel_requested:
            if output and output.exists():
                try:
                    output.unlink()
                except OSError:
                    pass
            self.job_title.setText("تم إلغاء المهمة")
            self.job_detail.setText("—")
            self.job_note.setText("لم يتم المساس بالفيديو الأصلي.")
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(0)
            self.cancel_button.hide()
            if self.batch_running:
                self.batch_running = False
                self.job_title.setText(f"تم إلغاء الدفعة بعد {len(self.batch_done)} ملف")
                current = self.batch_index - 1
                self.batch_panel.set_state(current, "waiting", "أُلغي")
                self.batch_panel.set_locked(False)
                self.batch_panel.set_progress(len(self.batch_done), len(self.batch_failed), False)
                self.batch_panel.open_button.setVisible(bool(self.batch_done))
            return

        if exit_code == 0 and output and output.exists() and output.stat().st_size > 0:
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(1000)
            self.cancel_button.hide()
            self.last_compare_output = str(output)
            self.last_compare_offset_ms = (
                int(self.active_preview_start * 1000) if job == "preview" else 0
            )
            self.last_compare_is_preview = job == "preview"
            self.last_compare_is_image = self.active_is_image
            self.compare_output_button.show()
            if job == "preview":
                self.job_title.setText("المعاينة جاهزة")
                self.job_detail.setText("100%")
                self.job_note.setText(
                    "ستفتح مقارنة الصورة: الأصلية والمحسّنة فوق بعض."
                    if self.active_is_image
                    else "ستفتح مقارنة الست ثواني: القديم والجديد متزامنان."
                )
                if self.auto_compare:
                    QTimer.singleShot(220, self.compare_output)
            elif self.batch_running:
                self.last_output = str(output)
                self.batch_done.append(output.name)
                self.batch_panel.set_state(
                    self.batch_index - 1, "done", f"تم ✓  {format_size(output.stat().st_size)}"
                )
                self.batch_panel.set_progress(len(self.batch_done), len(self.batch_failed), True)
                QTimer.singleShot(250, self.run_next_batch_item)
            else:
                self.last_output = str(output)
                self.job_title.setText("اكتمل التصدير بنجاح")
                self.job_detail.setText(format_size(output.stat().st_size))
                self.job_note.setText(f"حُفظ الملف باسم: {output.name}")
                self.open_output_button.show()
                self.play_output_button.show()
                self.play_completion_sound()
                if self.auto_compare:
                    QTimer.singleShot(350, self.compare_output)
            return

        log_text = "\n".join(self.process_log).lower()
        hardware_problem = any(
            marker in log_text
            for marker in (
                "nvenc",
                "no capable devices",
                "cannot load nvcuda",
                "initialization error",
                "unsupported device",
            )
        )
        if (
            hardware_problem
            and self.active_settings
            and self.active_settings.hardware
            and not self.hardware_retry_used
            and output
        ):
            self.hardware_retry_used = True
            self.cpu_retry_used = True
            try:
                output.unlink(missing_ok=True)
            except OSError:
                pass
            self.hardware_check.setChecked(False)
            self.job_note.setText("تعذّر تشغيل تسريع NVIDIA؛ نعيد المحاولة بالمعالج تلقائيًا.")
            fallback = replace(self.active_settings, hardware=False)
            QTimer.singleShot(
                350, lambda: self.start_job(job or "export", output, fallback, self.active_preview_start)
            )
            return

        ai_attempt_failed = bool(
            self.active_ai
            and self.active_settings
            and self.active_settings.ai_enabled
            and not self.active_is_image
        )
        if (
            ai_attempt_failed
            and not self.safe_retry_used
            and output
        ):
            # The AI pipeline already retries internally; reaching this point
            # means every GPU attempt failed. Guarantee the user still gets a
            # result by finishing with the trusted non-AI path.
            self.safe_retry_used = True
            try:
                output.unlink(missing_ok=True)
            except OSError:
                pass
            fallback = replace(
                self.active_settings,
                interpolate=self.active_settings.interpolate,
                stabilize=False,
                denoise=min(self.active_settings.denoise, 45),
                sharpness=min(self.active_settings.sharpness, 45),
                safe_mode=True,
                ai_enabled=False,
                ai_motion=False,
            )
            self.job_title.setText("إعادة محاولة موثوقة")
            self.job_note.setText(
                "نحافظ على الدقة وFPS، ونستبدل AI أو المرشحات الثقيلة بمسار أكثر ثباتًا."
            )
            QTimer.singleShot(
                450, lambda: self.start_job(job or "export", output, fallback, self.active_preview_start)
            )
            return

        if (
            self.strict_export
            and self.active_settings
            and self.active_settings.hardware
            and not self.cpu_retry_used
            and output
        ):
            self.cpu_retry_used = True
            try:
                output.unlink(missing_ok=True)
            except OSError:
                pass
            self.hardware_check.setChecked(False)
            fallback = replace(self.active_settings, hardware=False, interpolate=False, safe_mode=True)
            self.job_title.setText("محاولة أخيرة بالمعالج")
            self.job_note.setText("نفس الدقة وFPS، بترميز برمجي متوافق على نطاق أوسع.")
            QTimer.singleShot(
                450, lambda: self.start_job(job or "export", output, fallback, self.active_preview_start)
            )
            return

        if self.batch_running and job == "export":
            self.batch_failed.append(Path(self.source_path or "").name)
            self.batch_panel.set_state(self.batch_index - 1, "failed")
            self.batch_panel.set_progress(len(self.batch_done), len(self.batch_failed), True)
            QTimer.singleShot(250, self.run_next_batch_item)
            return
        self.cancel_button.hide()
        self.job_title.setText("تعذّر إكمال المعالجة")
        self.job_detail.setText("خطأ")
        self.job_note.setText("الملف الأصلي سليم ولم يتغير.")
        details = self.readable_error()
        QMessageBox.critical(
            self,
            "تعذّرت المعالجة",
            f"لم تكتمل المهمة بعد المحاولات الآمنة. تحقق من مساحة القرص وملف المصدر.\n\nالتفصيل:\n{details}",
        )

    def readable_error(self) -> str:
        useful = []
        for line in self.process_log:
            lower = line.lower()
            if any(word in lower for word in ("error", "failed", "invalid", "unable", "cannot")):
                useful.append(line)
        return "\n".join(useful[-5:]) or (self.process_log[-1] if self.process_log else "خطأ غير معروف")

    def process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.progress_timer.stop()
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(0)
            self.set_controls_busy(False)
            self.job_title.setText("تعذّر تشغيل أداة الفيديو")
            self.job_note.setText("تحقق من تثبيت FFmpeg ومحرك AI ثم أعد المحاولة.")

    def kill_active_process_tree(self) -> None:
        if not self.process:
            return
        process_id = int(self.process.processId())
        if os.name == "nt" and process_id > 0:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process_id), "/T", "/F"],
                    capture_output=True,
                    timeout=8,
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
        else:
            self.process.kill()

    def cancel_job(self) -> None:
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.cancel_requested = True
            self.job_detail.setText("إلغاء…")
            self.cancel_button.setEnabled(False)
            self.kill_active_process_tree()

    def open_output_folder(self) -> None:
        if self.last_output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.last_output).parent)))

    def play_output(self) -> None:
        if not (self.last_output and Path(self.last_output).exists()):
            return
        if self.is_image_source():
            ImageViewDialog(self.last_output, "النسخة النهائية", self).exec()
            return
        PreviewDialog(self.last_output, "النسخة النهائية", self).exec()

    def compare_output(self) -> None:
        if not (
            self.source_path
            and self.last_compare_output
            and Path(self.source_path).exists()
            and Path(self.last_compare_output).exists()
        ):
            return
        if self.last_compare_is_image:
            ImageCompareDialog(self.source_path, self.last_compare_output, self).exec()
            return
        CompareDialog(
            self.source_path,
            self.last_compare_output,
            self,
            original_offset_ms=self.last_compare_offset_ms,
            preview_comparison=self.last_compare_is_preview,
        ).exec()

    def load_preferences(self) -> None:
        resolution = str(self.settings_store.value("resolution", "1080p"))
        if resolution in self.resolution_buttons:
            self.resolution_buttons[resolution].setChecked(True)
        self.fps_slider.setValue(int(self.settings_store.value("fps", 60)))
        profile_version = int(self.settings_store.value("enhancement_profile_version", 0))
        if profile_version < 2:
            self.apply_max_enhancement(persist=False)
            self.settings_store.setValue("enhancement_profile_version", 2)
        else:
            self.denoise_slider.setValue(int(self.settings_store.value("denoise", 100)))
            self.sharpness_slider.setValue(int(self.settings_store.value("sharpness", 100)))
            self.brightness_slider.setValue(int(self.settings_store.value("brightness", 0)))
            self.contrast_slider.setValue(int(self.settings_store.value("contrast", 20)))
            self.saturation_slider.setValue(int(self.settings_store.value("saturation", 16)))
        folder = str(self.settings_store.value("folder", self.output_folder.text()))
        self.output_folder.setText(folder)
        style = self.color_style.findData(str(self.settings_store.value("color_style", "natural")))
        if style >= 0:
            self.color_style.setCurrentIndex(style)
        codec = self.codec_combo.findData(str(self.settings_store.value("codec", "h264")))
        if codec >= 0:
            self.codec_combo.setCurrentIndex(codec)
        quality = self.quality_combo.findData(int(self.settings_store.value("quality", 14)))
        if quality >= 0:
            self.quality_combo.setCurrentIndex(quality)
        ai_enabled = setting_bool(self.settings_store.value("ai_enabled", True))
        self.ai_enabled_check.setChecked(ai_enabled and self.ai_ready)
        detail_profile_version = int(self.settings_store.value("ai_detail_profile_version", 0))
        speed_profile_version = int(self.settings_store.value("ai_speed_profile_version", 0))
        stored_ai_model = (
            "anime_fast"
            if speed_profile_version < 1
            else "anime_extreme"
            if detail_profile_version < 1
            else str(self.settings_store.value("ai_model", "anime_extreme"))
        )
        ai_model = self.ai_model_combo.findData(stored_ai_model)
        if ai_model >= 0:
            self.ai_model_combo.setCurrentIndex(ai_model)
        if detail_profile_version < 1:
            self.settings_store.setValue("ai_detail_profile_version", 1)
        if speed_profile_version < 1:
            self.settings_store.setValue("ai_speed_profile_version", 1)
            self.settings_store.setValue("ai_model", "anime_fast")
            self.settings_store.sync()
        self.ai_fast_mode_check.setChecked(
            setting_bool(self.settings_store.value("ai_fast_mode", True)) and self.ai_ready
        )
        self.ai_motion_check.setChecked(
            setting_bool(self.settings_store.value("ai_motion", False)) and self.ai_ready
        )
        self.photo_restore_check.setChecked(
            setting_bool(self.settings_store.value("photo_restore", False), default=False)
            and self.ai_ready
        )
        restore_profile_version = int(
            self.settings_store.value("photo_restore_profile_version", 0)
        )
        stored_restore_profile = (
            "anime_supreme"
            if restore_profile_version < 2
            else str(self.settings_store.value("photo_restore_profile", "anime_supreme"))
        )
        photo_restore_profile = self.photo_restore_profile_combo.findData(stored_restore_profile)
        if photo_restore_profile >= 0:
            self.photo_restore_profile_combo.setCurrentIndex(photo_restore_profile)
        if restore_profile_version < 2:
            self.settings_store.setValue("photo_restore_profile", "anime_supreme")
            self.settings_store.setValue("photo_restore_profile_version", 2)
            self.settings_store.setValue("photo_restore_scale", 4)
        photo_restore_scale = self.photo_restore_scale_combo.findData(
            int(self.settings_store.value("photo_restore_scale", 4))
        )
        if photo_restore_scale >= 0:
            self.photo_restore_scale_combo.setCurrentIndex(photo_restore_scale)
        if profile_version < 2:
            self.apply_max_enhancement(persist=False)
            self.save_preferences()

    def save_preferences(self) -> None:
        settings = self.collect_settings()
        self.settings_store.setValue("resolution", settings.resolution)
        self.settings_store.setValue("fps", settings.fps)
        self.settings_store.setValue("denoise", settings.denoise)
        self.settings_store.setValue("sharpness", settings.sharpness)
        self.settings_store.setValue("brightness", settings.brightness)
        self.settings_store.setValue("contrast", settings.contrast)
        self.settings_store.setValue("saturation", settings.saturation)
        self.settings_store.setValue("color_style", settings.color_style)
        self.settings_store.setValue("codec", settings.codec)
        self.settings_store.setValue("quality", settings.quality)
        self.settings_store.setValue("ai_enabled", settings.ai_enabled)
        self.settings_store.setValue("ai_model", settings.ai_model)
        self.settings_store.setValue("ai_motion", settings.ai_motion)
        self.settings_store.setValue("ai_fast_mode", settings.ai_fast_mode)
        self.settings_store.setValue("photo_restore", self.photo_restore_check.isChecked())
        self.settings_store.setValue(
            "photo_restore_profile", self.photo_restore_profile_combo.currentData()
        )
        self.settings_store.setValue("photo_restore_profile_version", 2)
        self.settings_store.setValue(
            "photo_restore_scale", self.photo_restore_scale_combo.currentData() or 2
        )
        self.settings_store.setValue("ai_detail_profile_version", 1)
        self.settings_store.setValue("ai_speed_profile_version", 1)
        self.settings_store.setValue("enhancement_profile_version", 2)
        self.settings_store.setValue("folder", self.output_folder.text())
        self.settings_store.setValue("theme", self.current_theme)
        self.settings_store.setValue("strict_export", self.strict_export)
        self.settings_store.setValue("auto_compare", self.auto_compare)
        self.settings_store.setValue("completion_sound", self.completion_sound_enabled)
        self.settings_store.sync()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            answer = QMessageBox.question(
                self,
                "المعالجة ما زالت جارية",
                "إغلاق البرنامج الآن سيُلغي المهمة الحالية. هل تريد الإغلاق؟",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.cancel_requested = True
            self.kill_active_process_tree()
        self.save_preferences()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("VideoCraft")
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    app.setStyle("Fusion")
    load_fonts()
    body_font = QFont("Rubik", 10)
    body_font.setHintingPreference(QFont.HintingPreference.PreferVerticalHinting)
    app.setFont(body_font)
    app.setStyleSheet(STYLE)
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    window = MainWindow()
    window.show()
    if "--smoke-test" in sys.argv:
        QTimer.singleShot(900, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
