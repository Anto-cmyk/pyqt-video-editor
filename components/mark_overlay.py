from PyQt5.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QWidget

HINT = (
    "Click the logo to mark it  ·  Drag to draw a box  ·  "
    "Wheel to resize  ·  Right-click a box to remove it  ·  M to finish"
)


class MarkOverlay(QWidget):
    """Transparent window over the VLC preview for marking watermarks.

    Boxes are stored as percentages of the video frame (not the widget),
    so they stay correct however the preview is resized or letterboxed.
    """

    add_requested = pyqtSignal(float, float, float, float)  # x, y, w, h (%)
    remove_requested = pyqtSignal(int)
    resize_requested = pyqtSignal(int, float)  # mark index, scale factor

    def __init__(self, target, marks_provider, size_provider, parent=None):
        super().__init__(
            parent,
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus,
        )

        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)

        self.target = target
        self.marks_provider = marks_provider
        self.size_provider = size_provider

        self.default_w = 22.0
        self.default_h = 7.0

        self._marks = []
        self._video = (0, 0)
        self._press = None
        self._drag = None
        self._cursor = None

        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._sync)
        self._timer.start()

    # ------------------------------------------------------------------
    # Showing / syncing with the preview
    # ------------------------------------------------------------------

    def show_over_target(self):
        self._sync(force=True)
        self.show()
        self.raise_()

    def refresh(self):
        self._sync(force=True)

    def _sync(self, force=False):
        if not force and not self.isVisible():
            return

        rect = QRect(self.target.mapToGlobal(QPoint(0, 0)), self.target.size())

        if self.geometry() != rect:
            self.setGeometry(rect)

        marks = [(i, dict(m)) for i, m in self.marks_provider()]
        size = tuple(self.size_provider())

        if marks != self._marks or size != self._video:
            self._marks = marks
            self._video = size
            self.update()

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------

    def _frame_rect(self):
        """Where the video frame actually sits inside this window."""
        w, h = self.width(), self.height()
        vw, vh = self._video

        if vw <= 0 or vh <= 0:
            return QRectF(0, 0, w, h)

        scale = min(w / vw, h / vh)
        fw, fh = vw * scale, vh * scale

        return QRectF((w - fw) / 2, (h - fh) / 2, fw, fh)

    def _mark_rect(self, m):
        fr = self._frame_rect()

        return QRectF(
            fr.x() + m["x"] / 100 * fr.width(),
            fr.y() + m["y"] / 100 * fr.height(),
            m["w"] / 100 * fr.width(),
            m["h"] / 100 * fr.height(),
        )

    def _hit(self, pos):
        if pos is None:
            return None

        point = QPointF(pos)

        for index, m in reversed(self._marks):
            if self._mark_rect(m).contains(point):
                return index

        return None

    def _rect_to_percent(self, rect):
        fr = self._frame_rect()
        rect = rect.intersected(fr)

        if rect.width() < 2 or rect.height() < 2:
            return None

        return (
            (rect.x() - fr.x()) / fr.width() * 100,
            (rect.y() - fr.y()) / fr.height() * 100,
            rect.width() / fr.width() * 100,
            rect.height() / fr.height() * 100,
        )

    def _click_to_percent(self, pos):
        fr = self._frame_rect()

        if not fr.contains(QPointF(pos)):
            return None

        px = (pos.x() - fr.x()) / fr.width() * 100
        py = (pos.y() - fr.y()) / fr.height() * 100

        w, h = self.default_w, self.default_h
        x = max(0.0, min(100.0 - w, px - w / 2))
        y = max(0.0, min(100.0 - h, py - h / 2))

        return x, y, w, h

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            index = self._hit(event.pos())

            if index is not None:
                self.remove_requested.emit(index)

            return

        if event.button() == Qt.LeftButton:
            self._press = event.pos()
            self._drag = None

    def mouseMoveEvent(self, event):
        self._cursor = event.pos()

        if self._press is not None:
            rect = QRect(self._press, event.pos()).normalized()
            self._drag = rect if (rect.width() > 5 or rect.height() > 5) else None

        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self._press is None:
            return

        press, drag = self._press, self._drag
        self._press = None
        self._drag = None

        if drag is not None:
            box = self._rect_to_percent(QRectF(drag))
        else:
            box = self._click_to_percent(press)

        if box:
            self.add_requested.emit(*box)

        self.update()

    def leaveEvent(self, event):
        self._cursor = None
        self.update()

    def wheelEvent(self, event):
        factor = 1.08 if event.angleDelta().y() > 0 else 1 / 1.08
        index = self._hit(event.pos())

        if index is not None:
            self.resize_requested.emit(index, factor)
        else:
            self.default_w = max(3.0, min(80.0, self.default_w * factor))
            self.default_h = max(2.0, min(50.0, self.default_h * factor))
            self.update()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)

        # Almost-invisible fill so Windows delivers mouse events here.
        painter.fillRect(self.rect(), QColor(0, 0, 0, 3))

        fr = self._frame_rect()

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 70), 1, Qt.DashLine))
        painter.drawRect(fr)

        hover = self._hit(self._cursor) if self._press is None else None

        for index, m in self._marks:
            color = QColor(255, 214, 64) if index == hover else QColor(255, 70, 70)

            painter.setPen(QPen(color, 2))
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), 60))
            painter.drawRect(self._mark_rect(m))

        if self._drag is not None:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(255, 214, 64), 2, Qt.DashLine))
            painter.drawRect(self._drag)

        elif (
            self._cursor is not None
            and hover is None
            and self._press is None
            and fr.contains(QPointF(self._cursor))
        ):
            gw = self.default_w / 100 * fr.width()
            gh = self.default_h / 100 * fr.height()
            ghost = QRectF(
                self._cursor.x() - gw / 2,
                self._cursor.y() - gh / 2,
                gw,
                gh,
            )

            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(255, 255, 255, 190), 1, Qt.DashLine))
            painter.drawRect(ghost)

        painter.fillRect(QRect(0, 0, self.width(), 24), QColor(0, 0, 0, 150))
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(
            QRect(8, 0, self.width() - 16, 24),
            Qt.AlignVCenter | Qt.AlignLeft,
            HINT,
        )

        painter.end()