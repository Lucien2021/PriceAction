from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QPointF, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
    QPixmap,
    QWheelEvent,
    QMouseEvent,
)
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

_BG = QColor("#1e222d")
_GRID = QColor("#2B2B43")
_TEXT = QColor("#808899")
_LINE_UP = QColor("#26a69a")
_LINE_DOWN = QColor("#ef5350")
_RESET_LINE = QColor("#FFD700")
_DOT_WIN = QColor("#26a69a")
_DOT_LOSE = QColor("#ef5350")
_DOT_HOVER = QColor("#5b5bff")

_MARGIN_L = 70
_MARGIN_R = 20
_MARGIN_T = 30
_MARGIN_B = 40

_MIN_PX_PER_POINT = 6
_MAX_PX_PER_POINT = 120
_DEFAULT_PX_PER_POINT = 24


class EquityCurveWidget(QWidget):
    """Interactive equity curve with zoom, pan, hover tooltips and snapshot click."""
    point_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(280)
        self.setMouseTracking(True)
        self._data: List[dict] = []
        self._pts: List[QPointF] = []
        self._hover_idx = -1
        self._tooltip_widget: Optional[_TradeTooltip] = None

        self._px_per_pt = _DEFAULT_PX_PER_POINT
        self._offset_x = 0.0
        self._dragging = False
        self._drag_start_x = 0.0
        self._drag_start_offset = 0.0

    def set_data(self, points: List[dict]):
        self._data = points
        self._pts.clear()
        self._hover_idx = -1
        n = len(self._data)
        if n > 1:
            needed = (n - 1) * self._px_per_pt + _MARGIN_L + _MARGIN_R
            visible = self.width()
            if needed > visible:
                self._offset_x = max(0, needed - visible)
            else:
                self._offset_x = 0
        else:
            self._offset_x = 0
        self.update()

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    def _chart_width(self) -> float:
        n = len(self._data)
        return max((n - 1) * self._px_per_pt, self.width() - _MARGIN_L - _MARGIN_R)

    def _max_offset(self) -> float:
        total = self._chart_width() + _MARGIN_L + _MARGIN_R
        return max(0.0, total - self.width())

    def _clamp_offset(self):
        self._offset_x = max(0.0, min(self._offset_x, self._max_offset()))

    def _to_x(self, i: int) -> float:
        return _MARGIN_L + i * self._px_per_pt - self._offset_x

    def _to_y(self, val: float, eq_min: float, eq_max: float) -> float:
        h = self.height()
        chart_h = h - _MARGIN_T - _MARGIN_B
        return _MARGIN_T + (1.0 - (val - eq_min) / (eq_max - eq_min)) * chart_h

    def _eq_range(self):
        if not self._data:
            return 99000.0, 101000.0
        equities = [pt.get("equity_after", 100000) for pt in self._data]
        lo, hi = min(equities), max(equities)
        margin = max((hi - lo) * 0.05, 500)
        return lo - margin, hi + margin

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), _BG)
        w = self.width()
        h = self.height()

        if not self._data:
            p.setPen(QPen(_TEXT))
            p.setFont(QFont("sans-serif", 12))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无资金曲线数据")
            p.end()
            return

        eq_min, eq_max = self._eq_range()
        n = len(self._data)
        chart_h = h - _MARGIN_T - _MARGIN_B

        self._draw_grid(p, w, h, eq_min, eq_max, n)

        self._pts = []
        for i, pt in enumerate(self._data):
            x = self._to_x(i)
            y = self._to_y(pt.get("equity_after", 100000), eq_min, eq_max)
            self._pts.append(QPointF(x, y))

        p.setClipRect(_MARGIN_L, 0, w - _MARGIN_L - _MARGIN_R, h)

        for i, pt in enumerate(self._data):
            if pt.get("is_reset"):
                x = self._to_x(i)
                if _MARGIN_L <= x <= w - _MARGIN_R:
                    p.setPen(QPen(_RESET_LINE, 1, Qt.PenStyle.DashLine))
                    p.drawLine(QPointF(x, _MARGIN_T), QPointF(x, _MARGIN_T + chart_h))
                    p.setFont(QFont("sans-serif", 9))
                    p.setPen(QPen(_RESET_LINE))
                    p.drawText(QPointF(x + 2, _MARGIN_T + 12), "破产重置")

        if len(self._pts) > 1:
            for i in range(1, len(self._pts)):
                x0, x1 = self._pts[i - 1].x(), self._pts[i].x()
                if x1 < _MARGIN_L and x0 < _MARGIN_L:
                    continue
                if x0 > w - _MARGIN_R and x1 > w - _MARGIN_R:
                    continue
                eq_prev = self._data[i - 1].get("equity_after", 100000)
                eq_curr = self._data[i].get("equity_after", 100000)
                color = _LINE_UP if eq_curr >= eq_prev else _LINE_DOWN
                p.setPen(QPen(color, 2))
                p.drawLine(self._pts[i - 1], self._pts[i])

        for i, qp in enumerate(self._pts):
            if qp.x() < _MARGIN_L - 5 or qp.x() > w - _MARGIN_R + 5:
                continue
            pnl = self._data[i].get("pnl") or 0
            dot_color = _DOT_HOVER if i == self._hover_idx else (_DOT_WIN if pnl >= 0 else _DOT_LOSE)
            radius = 5 if i == self._hover_idx else 3
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(dot_color))
            p.drawEllipse(qp, radius, radius)

        p.setClipping(False)

        if 0 <= self._hover_idx < len(self._pts):
            pt = self._pts[self._hover_idx]
            if _MARGIN_L <= pt.x() <= w - _MARGIN_R:
                p.setPen(QPen(QColor("#5b5bff80"), 1, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(pt.x(), _MARGIN_T), QPointF(pt.x(), _MARGIN_T + chart_h))
                p.drawLine(QPointF(_MARGIN_L, pt.y()), QPointF(w - _MARGIN_R, pt.y()))
                eq_val = self._data[self._hover_idx].get("equity_after", 0)
                p.setPen(QPen(QColor("#d1d4dc")))
                p.setFont(QFont("sans-serif", 9, QFont.Weight.Bold))
                p.drawText(QPointF(4, pt.y() + 4), f"{eq_val:,.0f}")

        p.end()

    def _draw_grid(self, p: QPainter, w: int, h: int, eq_min: float, eq_max: float, n: int):
        chart_h = h - _MARGIN_T - _MARGIN_B
        p.setPen(QPen(_GRID, 1))
        for i in range(5):
            val = eq_min + (eq_max - eq_min) * i / 4
            y = self._to_y(val, eq_min, eq_max)
            p.drawLine(QPointF(_MARGIN_L, y), QPointF(w - _MARGIN_R, y))
            p.setFont(QFont("sans-serif", 9))
            p.setPen(QPen(_TEXT))
            p.drawText(QPointF(4, y + 4), f"{val:,.0f}")
            p.setPen(QPen(_GRID, 1))

        if n > 0:
            visible_start = max(0, int(self._offset_x / self._px_per_pt))
            visible_end = min(n - 1, int((self._offset_x + w) / self._px_per_pt) + 1)
            count_visible = visible_end - visible_start + 1
            step = max(1, count_visible // 8)
            p.setFont(QFont("sans-serif", 8))
            for i in range(visible_start, visible_end + 1, step):
                x = self._to_x(i)
                if x < _MARGIN_L or x > w - _MARGIN_R:
                    continue
                t = self._data[i].get("created_at") or self._data[i].get("exit_time") or ""
                label = t[:10] if len(t) >= 10 else t
                p.setPen(QPen(_TEXT))
                p.drawText(QPointF(x - 20, h - 8), label)

    # ------------------------------------------------------------------
    # Mouse interaction
    # ------------------------------------------------------------------

    def mouseMoveEvent(self, event: QMouseEvent):
        pos = event.position() if hasattr(event, 'position') else event.localPos()
        mx, my = pos.x(), pos.y()

        if self._dragging:
            dx = mx - self._drag_start_x
            self._offset_x = self._drag_start_offset - dx
            self._clamp_offset()
            self._pts.clear()
            self.update()
            return

        best = -1
        best_dist = 18.0
        for i, qp in enumerate(self._pts):
            if qp.x() < _MARGIN_L or qp.x() > self.width() - _MARGIN_R:
                continue
            d = ((qp.x() - mx) ** 2 + (qp.y() - my) ** 2) ** 0.5
            if d < best_dist:
                best_dist = d
                best = i

        if best != self._hover_idx:
            self._hover_idx = best
            self.update()
            if best >= 0:
                gp = event.globalPosition().toPoint() if hasattr(event, 'globalPosition') else event.globalPos()
                self._show_tooltip(best, gp)
            else:
                self._hide_tooltip()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._hover_idx >= 0:
                self.point_clicked.emit(self._hover_idx)
                data = self._data[self._hover_idx]
                snap = data.get("snapshot_path") or ""
                if snap and Path(snap).exists():
                    pix = QPixmap(snap)
                    if not pix.isNull():
                        viewer = SnapshotPopup(pix, self._format_trade_title(data), self.window())
                        viewer.exec()
                return
            self._dragging = True
            pos = event.position() if hasattr(event, 'position') else event.localPos()
            self._drag_start_x = pos.x()
            self._drag_start_offset = self._offset_x
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, event):
        if self._hover_idx >= 0:
            self._hover_idx = -1
            self.update()
            self._hide_tooltip()
        if self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        pos = event.position() if hasattr(event, 'position') else event.localPos()
        mx = pos.x()

        data_x_before = (mx + self._offset_x - _MARGIN_L) / self._px_per_pt

        if delta > 0:
            self._px_per_pt = min(_MAX_PX_PER_POINT, self._px_per_pt * 1.2)
        else:
            self._px_per_pt = max(_MIN_PX_PER_POINT, self._px_per_pt / 1.2)

        self._offset_x = data_x_before * self._px_per_pt - (mx - _MARGIN_L)
        self._clamp_offset()
        self._pts.clear()
        self._hide_tooltip()
        self._hover_idx = -1
        self.update()

    # ------------------------------------------------------------------
    # Tooltip
    # ------------------------------------------------------------------

    def _show_tooltip(self, idx: int, global_pos):
        data = self._data[idx]
        if self._tooltip_widget is None:
            self._tooltip_widget = _TradeTooltip()
        self._tooltip_widget.set_data(data)
        self._tooltip_widget.move(global_pos.x() + 16, global_pos.y() + 16)
        self._tooltip_widget.show()

    def _hide_tooltip(self):
        if self._tooltip_widget:
            self._tooltip_widget.hide()

    @staticmethod
    def _format_trade_title(data: dict) -> str:
        d = "做多" if data.get("direction") == "long" else "做空"
        pnl = data.get("pnl") or 0
        return f"{data.get('exit_time', '')[:16]} {d} {pnl:+.2f}"


class _TradeTooltip(QWidget):
    """Floating tooltip showing trade details + snapshot thumbnail."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setStyleSheet(
            "background:#2B2B43;color:#d1d4dc;border:1px solid #5b5bff;"
            "border-radius:6px;padding:6px;"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        self._lbl_info = QLabel()
        self._lbl_info.setWordWrap(True)
        self._lbl_info.setStyleSheet("border:none;font-size:11px;line-height:1.5;")
        layout.addWidget(self._lbl_info)
        self._lbl_snap = QLabel()
        self._lbl_snap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_snap.setStyleSheet("border:none;")
        self._lbl_snap.setFixedSize(260, 160)
        layout.addWidget(self._lbl_snap)
        self._lbl_hint = QLabel("点击查看大图")
        self._lbl_hint.setStyleSheet("border:none;color:#808899;font-size:9px;")
        self._lbl_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._lbl_hint)

    def set_data(self, data: dict):
        d = "做多" if data.get("direction") == "long" else "做空"
        pnl = data.get("pnl") or 0
        pnl_pct = data.get("pnl_pct") or 0
        r = data.get("r_multiple")
        r_str = f"  {r:.2f}R" if r is not None else ""
        eq_after = data.get("equity_after") or 0
        sym = data.get("symbol") or ""
        tf = data.get("timeframe") or ""
        setup = data.get("setup_type") or ""
        reason = data.get("exit_reason") or ""
        ep = data.get("entry_price") or 0
        xp = data.get("exit_price") or 0
        qty = data.get("quantity") or 0
        t = data.get("exit_time") or data.get("created_at") or ""
        sl = data.get("stop_loss")
        tp = data.get("take_profit")

        lines = [
            f"<b>{t[:16]}</b>  {sym} {tf}",
            f"{d}  {ep:.2f} -> {xp:.2f}  x{qty}",
            f"盈亏: <b style='color:{('#26a69a' if pnl>=0 else '#ef5350')}'>{pnl:+.2f} ({pnl_pct:+.2f}%){r_str}</b>",
            f"出场: {reason}  Setup: {setup}",
        ]
        if sl or tp:
            lines.append(f"SL: {sl or '-'}  TP: {tp or '-'}")
        lines.append(f"<b>资金: {eq_after:,.0f}</b>")
        if data.get("is_reset"):
            lines.append("<span style='color:#FFD700'>** 破产重置 **</span>")
        self._lbl_info.setText("<br>".join(lines))

        snap = data.get("snapshot_path") or ""
        if snap and Path(snap).exists():
            pix = QPixmap(snap)
            if not pix.isNull():
                self._lbl_snap.setPixmap(pix.scaled(
                    260, 160,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
                self._lbl_snap.show()
                self._lbl_hint.show()
                return
        self._lbl_snap.clear()
        self._lbl_snap.hide()
        self._lbl_hint.hide()


class SnapshotPopup(QDialog):
    """Full-screen zoomable snapshot viewer."""

    def __init__(self, pixmap: QPixmap, title: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title or "交易截图")
        self.setMinimumSize(600, 400)
        self.resize(1000, 650)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 4, 8, 4)
        self._zoom = 1.0
        for text, fn in [
            ("-", lambda: self._set_zoom(self._zoom - 0.2)),
            ("+", lambda: self._set_zoom(self._zoom + 0.2)),
            ("适应", self._fit),
            ("100%", lambda: self._set_zoom(1.0)),
        ]:
            b = QPushButton(text)
            b.setFixedSize(48, 28)
            b.clicked.connect(fn)
            toolbar.addWidget(b)
        self._lbl_zoom = QLabel("100%")
        toolbar.addWidget(self._lbl_zoom)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img = QLabel()
        self._img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setWidget(self._img)
        layout.addWidget(self._scroll)

        self._original = pixmap
        self._fit()

    def _set_zoom(self, z: float):
        self._zoom = max(0.2, min(5.0, z))
        self._lbl_zoom.setText(f"{int(self._zoom * 100)}%")
        w = int(self._original.width() * self._zoom)
        h = int(self._original.height() * self._zoom)
        scaled = self._original.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
        self._img.setPixmap(scaled)
        self._img.resize(scaled.size())

    def _fit(self):
        vw = self._scroll.viewport().width() - 20
        vh = self._scroll.viewport().height() - 20
        pw, ph = self._original.width(), self._original.height()
        if pw == 0 or ph == 0:
            return
        self._set_zoom(min(vw / pw, vh / ph, 3.0))

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        self._set_zoom(self._zoom + (0.1 if delta > 0 else -0.1))
