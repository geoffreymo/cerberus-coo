"""
Zoomable/pannable image view with overlays and observing gestures.

Gestures (image coordinates are always un-flipped sensor pixels):
    wheel            zoom about cursor
    left-drag        pan
    SHIFT+left-drag  select ROI (emits roi_dragged)
    right-click      set FWHM tracking target (emits fwhm_target_requested)
    CTRL+left-click  set photometry target aperture
    ALT+left-click   set photometry comparison aperture
The "click mode" combo in the live view can force any of these for a plain click.
The view is horizontally flipped by default (north up, east left).
"""

from typing import Optional, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap, QTransform
from PyQt6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView

MAGENTA = QColor(255, 0, 255)
GREEN = QColor(0, 230, 0)
CYAN = QColor(0, 220, 255)
YELLOW = QColor(255, 220, 0)


class ImageView(QGraphicsView):
    mouse_moved = pyqtSignal(int, int)                 # image x, y
    mouse_left = pyqtSignal()
    roi_dragged = pyqtSignal(int, int, int, int)       # x1, y1, x2, y2 (image coords)
    fwhm_target_requested = pyqtSignal(int, int)
    target_aperture_requested = pyqtSignal(int, int)
    comparison_aperture_requested = pyqtSignal(int, int)
    zoom_changed = pyqtSignal(float)

    CLICK_MODES = ("Pan", "ROI", "FWHM target", "Phot. target", "Phot. comparison")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.TransformationMode.FastTransformation)
        self._scene.addItem(self._item)

        self.setBackgroundBrush(QColor(20, 20, 20))
        self.setMouseTracking(True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        self._image_size: Tuple[int, int] = (0, 0)
        self._flip = True
        self._first_image = True
        self._fit_on_resize = True
        self.click_mode = "Pan"

        # Overlay state (image coordinates)
        self._roi_start: Optional[Tuple[int, int]] = None
        self._roi_end: Optional[Tuple[int, int]] = None
        self._roi_dragging = False
        self._fwhm_target: Optional[Tuple[int, int]] = None
        self._fwhm_box_px = 0
        self._fwhm_value: Optional[float] = None
        self._fwhm_ok = True
        self._target_aperture: Optional[Tuple[int, int]] = None
        self._comparison_aperture: Optional[Tuple[int, int]] = None
        self._aperture_radii = (20, 30, 45)
        self._show_apertures = False
        self._crosshair = False

        self._apply_flip()

    # ---- image ------------------------------------------------------------

    def set_image8(self, image8) -> None:
        """Display a contiguous uint8 HxW numpy array."""
        h, w = image8.shape[:2]
        qimg = QImage(image8.data, w, h, w, QImage.Format.Format_Grayscale8)
        self._item.setPixmap(QPixmap.fromImage(qimg))
        if (w, h) != self._image_size:
            self._image_size = (w, h)
            self._scene.setSceneRect(QRectF(0, 0, w, h))
            self.fit_to_window()
        elif self._first_image:
            self.fit_to_window()
        self._first_image = False

    @property
    def image_size(self) -> Tuple[int, int]:
        return self._image_size

    # ---- zoom / flip --------------------------------------------------------

    def _apply_flip(self):
        t = QTransform()
        if self._flip:
            t.scale(-1, 1)
        self.setTransform(t)

    def set_flipped(self, flipped: bool):
        if flipped != self._flip:
            self._flip = flipped
            self._apply_flip()
            self.fit_to_window()

    def is_flipped(self) -> bool:
        return self._flip

    def fit_to_window(self):
        if self._image_size == (0, 0):
            return
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._fit_on_resize = True
        self.zoom_changed.emit(self.zoom_factor())

    def zoom_1to1(self):
        self._apply_flip()
        self._fit_on_resize = False
        self.zoom_changed.emit(self.zoom_factor())

    def zoom_by(self, factor: float):
        self.scale(factor, factor)
        self._fit_on_resize = False
        self.zoom_changed.emit(self.zoom_factor())

    def zoom_factor(self) -> float:
        return abs(self.transform().m11())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_on_resize:
            self.fit_to_window()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            return
        self.zoom_by(1.25 if delta > 0 else 0.8)
        event.accept()

    # ---- overlays -------------------------------------------------------------

    def set_fwhm_overlay(self, target: Optional[Tuple[int, int]], box_px: int,
                         value: Optional[float], ok: bool = True):
        self._fwhm_target, self._fwhm_box_px, self._fwhm_value, self._fwhm_ok = target, box_px, value, ok
        self.viewport().update()

    def set_apertures(self, target, comparison, radii: Tuple[int, int, int], show: bool):
        self._target_aperture, self._comparison_aperture = target, comparison
        self._aperture_radii, self._show_apertures = radii, show
        self.viewport().update()

    def set_crosshair(self, on: bool):
        self._crosshair = on
        self.viewport().update()

    def clear_roi(self):
        self._roi_start = self._roi_end = None
        self._roi_dragging = False
        self.viewport().update()

    def drawForeground(self, painter: QPainter, rect: QRectF):
        super().drawForeground(painter, rect)
        if self._image_size == (0, 0):
            return

        def pen(color, width=2):
            p = QPen(color, width)
            p.setCosmetic(True)
            return p

        # -- shapes in scene coordinates (flip-aware automatically)
        if self._crosshair:
            w, h = self._image_size
            painter.setPen(pen(YELLOW, 1))
            painter.drawLine(QPointF(w / 2, 0), QPointF(w / 2, h))
            painter.drawLine(QPointF(0, h / 2), QPointF(w, h / 2))

        if self._roi_start and self._roi_end:
            painter.setPen(pen(GREEN))
            r = QRectF(QPointF(*self._roi_start), QPointF(*self._roi_end)).normalized()
            painter.drawRect(r)

        if self._fwhm_target is not None:
            cx, cy = self._fwhm_target
            painter.setPen(pen(MAGENTA if self._fwhm_ok else QColor(255, 120, 0)))
            r = max(4, self._fwhm_box_px / 2)
            painter.drawEllipse(QPointF(cx + 0.5, cy + 0.5), r, r)

        if self._show_apertures:
            r_ap, r_in, r_out = self._aperture_radii
            for pos, color in ((self._target_aperture, GREEN), (self._comparison_aperture, CYAN)):
                if pos is None:
                    continue
                painter.setPen(pen(color))
                c = QPointF(pos[0] + 0.5, pos[1] + 0.5)
                for rr in (r_ap, r_in, r_out):
                    painter.drawEllipse(c, rr, rr)

        # -- text/crosshairs in viewport coordinates (never mirrored)
        painter.save()
        painter.resetTransform()
        font = QFont()
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)

        if self._roi_start and self._roi_end:
            w = abs(self._roi_end[0] - self._roi_start[0])
            h = abs(self._roi_end[1] - self._roi_start[1])
            vp = self.mapFromScene(QPointF(*self._roi_end))
            painter.setPen(pen(GREEN))
            painter.drawText(vp.x() + 8, vp.y() - 8, f"{w} x {h}")

        if self._fwhm_target is not None:
            cx, cy = self._fwhm_target
            vp = self.mapFromScene(QPointF(cx + 0.5, cy + 0.5))
            r_view = max(4, self._fwhm_box_px / 2) * self.zoom_factor()
            painter.setPen(pen(MAGENTA if self._fwhm_ok else QColor(255, 120, 0)))
            painter.drawLine(vp.x() - 10, vp.y(), vp.x() + 10, vp.y())
            painter.drawLine(vp.x(), vp.y() - 10, vp.x(), vp.y() + 10)
            label = "F"
            if self._fwhm_value is not None:
                label += f'  {self._fwhm_value:.3f}"'
            elif not self._fwhm_ok:
                label += "  fit err"
            painter.drawText(int(vp.x() + r_view + 8), int(vp.y() + 5), label)

        if self._show_apertures:
            r_out = self._aperture_radii[2] * self.zoom_factor()
            for pos, color, label in ((self._target_aperture, GREEN, "T"), (self._comparison_aperture, CYAN, "C")):
                if pos is None:
                    continue
                vp = self.mapFromScene(QPointF(pos[0] + 0.5, pos[1] + 0.5))
                painter.setPen(pen(color))
                painter.drawText(int(vp.x() + r_out + 8), int(vp.y() + 5), label)
        painter.restore()

    # ---- mouse ---------------------------------------------------------------

    def _image_coords(self, pos) -> Optional[Tuple[int, int]]:
        if self._image_size == (0, 0):
            return None
        sp = self.mapToScene(pos)
        x, y = int(sp.x()), int(sp.y())
        w, h = self._image_size
        if 0 <= x < w and 0 <= y < h:
            return x, y
        return max(0, min(x, w - 1)), max(0, min(y, h - 1))

    def _inside(self, pos) -> bool:
        if self._image_size == (0, 0):
            return False
        sp = self.mapToScene(pos)
        w, h = self._image_size
        return 0 <= sp.x() < w and 0 <= sp.y() < h

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self._inside(pos):
            xy = self._image_coords(pos)
            if xy:
                self.mouse_moved.emit(*xy)
        else:
            self.mouse_left.emit()
        if self._roi_dragging:
            xy = self._image_coords(pos)
            if xy:
                self._roi_end = xy
                self.viewport().update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.mouse_left.emit()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        pos = event.position().toPoint()
        mods = event.modifiers()
        xy = self._image_coords(pos)
        button = event.button()
        M = Qt.KeyboardModifier

        if button == Qt.MouseButton.RightButton:
            if self._roi_dragging:
                self.clear_roi()
            elif xy:
                self.fwhm_target_requested.emit(*xy)
            event.accept()
            return

        if button == Qt.MouseButton.LeftButton and xy:
            mode = self.click_mode
            if mods & M.ShiftModifier:
                mode = "ROI"
            elif mods & M.ControlModifier:
                mode = "Phot. target"
            elif mods & M.AltModifier:
                mode = "Phot. comparison"

            if mode == "ROI":
                self._roi_start = self._roi_end = xy
                self._roi_dragging = True
                self.viewport().update()
                event.accept()
                return
            if mode == "FWHM target":
                self.fwhm_target_requested.emit(*xy)
                event.accept()
                return
            if mode == "Phot. target":
                self.target_aperture_requested.emit(*xy)
                event.accept()
                return
            if mode == "Phot. comparison":
                self.comparison_aperture_requested.emit(*xy)
                event.accept()
                return
            # Pan
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self._fit_on_resize = False
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._roi_dragging:
            self._roi_dragging = False
            if self._roi_start and self._roi_end and self._roi_start != self._roi_end:
                x1, y1 = self._roi_start
                x2, y2 = self._roi_end
                self.roi_dragged.emit(x1, y1, x2, y2)
            self.clear_roi()
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
