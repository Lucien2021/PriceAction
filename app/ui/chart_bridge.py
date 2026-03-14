from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, List

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView

from app.domain.candle import Candle, Timeframe


_HTML_PATH = Path(__file__).parent / "resources" / "chart.html"


class _Bridge(QObject):
    chart_ready_signal = Signal()
    limit_price_moved = Signal(str, float)
    trade_line_moved = Signal(str, float)

    @Slot()
    def chartReady(self):
        self.chart_ready_signal.emit()

    @Slot(str, float)
    def onLimitPriceMoved(self, order_id: str, new_price: float):
        self.limit_price_moved.emit(order_id, new_price)

    @Slot(str, float)
    def onTradeLineMoved(self, line_id: str, new_price: float):
        self.trade_line_moved.emit(line_id, new_price)


class ChartWidget(QWebEngineView):
    chart_ready = Signal()
    limit_price_changed = Signal(str, float)
    trade_line_changed = Signal(str, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bridge = _Bridge()
        self._bridge.chart_ready_signal.connect(self._on_ready)
        self._bridge.limit_price_moved.connect(self.limit_price_changed)
        self._bridge.trade_line_moved.connect(self.trade_line_changed)
        self._channel = QWebChannel()
        self._channel.registerObject("bridge", self._bridge)
        self.page().setWebChannel(self._channel)

        self._is_ready = False
        self._pending_calls: list[str] = []
        self._timeframe: Timeframe = Timeframe.DAILY
        self._last_close: float = 0.0

        self.setUrl(QUrl.fromLocalFile(str(_HTML_PATH.resolve())))

    def _on_ready(self):
        self._is_ready = True
        self.chart_ready.emit()
        for js in self._pending_calls:
            self.page().runJavaScript(js)
        self._pending_calls.clear()

    def _run_js(self, js: str) -> None:
        if self._is_ready:
            self.page().runJavaScript(js)
        else:
            self._pending_calls.append(js)

    # ------------------------------------------------------------------
    # Core data
    # ------------------------------------------------------------------

    def set_timeframe(self, tf: Timeframe) -> None:
        self._timeframe = tf

    def set_candles(self, candles: List[Candle]) -> None:
        candle_data = [c.to_chart_dict(self._timeframe) for c in candles]
        volume_data = [c.to_volume_dict(self._timeframe) for c in candles]
        extra = self._build_extra(candles)
        self._run_js(f"setData({json.dumps(candle_data)}, {json.dumps(volume_data)})")
        self._run_js(f"setExtraData({json.dumps(extra)})")
        if candles:
            self._last_close = candles[-1].close

    def add_candle(self, candle: Candle) -> None:
        cd = json.dumps(candle.to_chart_dict(self._timeframe))
        vd = json.dumps(candle.to_volume_dict(self._timeframe))
        ext = json.dumps(self._candle_extra(candle, self._last_close))
        key = self._time_key(candle)
        self._run_js(f"addCandle({cd}, {vd})")
        self._run_js(f"addExtraPoint('{key}',{ext})")
        self._last_close = candle.close

    def set_markers(self, markers: list) -> None:
        self._run_js(f"setMarkers({json.dumps(markers, ensure_ascii=False)})")

    def set_ma_data(self, candles: List[Candle], period: int = 20) -> None:
        if len(candles) < period:
            return
        ma_data = []
        for i in range(period - 1, len(candles)):
            window = candles[i - period + 1: i + 1]
            avg = sum(c.close for c in window) / period
            c = candles[i]
            if self._timeframe.minutes >= Timeframe.DAILY.minutes:
                t = c.timestamp.strftime("%Y-%m-%d")
            else:
                t = int(c.timestamp.timestamp())
            ma_data.append({"time": t, "value": round(avg, 2)})
        self._run_js(f"setMAData({json.dumps(ma_data)})")

    def add_ma_point(self, candles: List[Candle], period: int = 20) -> None:
        if len(candles) < period:
            return
        window = candles[-period:]
        avg = sum(c.close for c in window) / period
        c = candles[-1]
        if self._timeframe.minutes >= Timeframe.DAILY.minutes:
            t = c.timestamp.strftime("%Y-%m-%d")
        else:
            t = int(c.timestamp.timestamp())
        self._run_js(f"addMAPoint({json.dumps({'time': t, 'value': round(avg, 2)})})")

    def _time_key(self, c: Candle) -> str:
        if self._timeframe.minutes >= Timeframe.DAILY.minutes:
            return c.timestamp.strftime("%Y-%m-%d")
        return str(int(c.timestamp.timestamp()))

    _WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    def _candle_extra(self, c: Candle, prev_close: float) -> dict:
        change = round(c.close - prev_close, 2) if prev_close else 0
        change_pct = round(change / prev_close * 100, 2) if prev_close else 0
        return {
            "date": c.timestamp.strftime("%Y/%m/%d"),
            "weekday": self._WEEKDAYS[c.timestamp.weekday()],
            "time": c.timestamp.strftime("%H:%M") if self._timeframe.minutes < Timeframe.DAILY.minutes else "",
            "volume": c.volume,
            "turnover": c.turnover,
            "change": change,
            "changePct": change_pct,
        }

    def _build_extra(self, candles: List[Candle]) -> dict:
        result = {}
        for i, c in enumerate(candles):
            prev_close = candles[i - 1].close if i > 0 else c.open
            result[self._time_key(c)] = self._candle_extra(c, prev_close)
        return result

    def clear(self) -> None:
        self._run_js("clearChart()")

    def fit(self) -> None:
        self._run_js("fitContent()")

    # ------------------------------------------------------------------
    # Limit orders
    # ------------------------------------------------------------------

    def add_limit_order(self, order_id: str, price: float, direction: str, color: str = "#FFD700") -> None:
        self._run_js(f"addLimitOrder('{order_id}', {price}, '{direction}', '{color}')")

    def remove_limit_order(self, order_id: str) -> None:
        self._run_js(f"removeLimitOrder('{order_id}')")

    def update_limit_price(self, order_id: str, price: float) -> None:
        self._run_js(f"updateLimitPrice('{order_id}', {price})")

    def remove_all_limits(self) -> None:
        self._run_js("removeAllLimitOrders()")

    # ------------------------------------------------------------------
    # Drawing tools
    # ------------------------------------------------------------------

    def set_draw_mode(self, mode: str) -> None:
        self._run_js(f"setDrawMode('{mode}')")

    def clear_drawings(self) -> None:
        self._run_js("clearDrawings()")

    def get_drawings(self, callback: Callable[[list], None]) -> None:
        def _done(result):
            try:
                payload = json.loads(result) if result else []
            except Exception:
                payload = []
            callback(payload)

        self.page().runJavaScript("getDrawingsJSON()", _done)

    def set_drawings(self, drawings: list) -> None:
        payload = json.dumps(drawings, ensure_ascii=False)
        self._run_js(f"setDrawingsJSON({json.dumps(payload)})")

    # ------------------------------------------------------------------
    # Trade protection lines (SL / TP / Entry)
    # ------------------------------------------------------------------

    def add_trade_line(self, line_id: str, price: float, line_type: str, color: str) -> None:
        self._run_js(f"addTradeLine('{line_id}', {price}, '{line_type}', '{color}')")

    def remove_trade_line(self, line_id: str) -> None:
        self._run_js(f"removeTradeLine('{line_id}')")

    def update_trade_line(self, line_id: str, price: float) -> None:
        self._run_js(f"updateTradeLinePrice('{line_id}', {price})")

    def remove_all_trade_lines(self) -> None:
        self._run_js("removeAllTradeLines()")

    # ------------------------------------------------------------------
    # Screenshot
    # ------------------------------------------------------------------

    def prepare_snapshot(self, start_idx: int, end_idx: int, markers: list) -> None:
        mk = json.dumps(markers, ensure_ascii=False)
        self._run_js(f"takeSnapshot({start_idx},{end_idx},{mk})")

    def capture_image(self, callback: Callable[[str], None]) -> None:
        def _done(result):
            callback(result or "")
        self.page().runJavaScript("getChartImage()", _done)

    # ------------------------------------------------------------------
    # Trade markers builder
    # ------------------------------------------------------------------

    def build_trade_markers(self, trades, timeframe: Timeframe) -> list:
        markers = []
        for t in trades:
            if timeframe.minutes >= Timeframe.DAILY.minutes:
                et = t.entry_time.strftime("%Y-%m-%d")
                xt = t.exit_time.strftime("%Y-%m-%d")
            else:
                et = int(t.entry_time.timestamp())
                xt = int(t.exit_time.timestamp())

            is_long = t.direction.value == "long"
            markers.append({
                "time": et,
                "position": "belowBar" if is_long else "aboveBar",
                "color": "#ef5350" if is_long else "#26a69a",
                "shape": "arrowUp" if is_long else "arrowDown",
                "text": f"{'买' if is_long else '卖'} {t.entry_price:.2f}",
            })
            win = t.pnl > 0
            markers.append({
                "time": xt,
                "position": "aboveBar" if is_long else "belowBar",
                "color": "#ef5350" if win else "#26a69a",
                "shape": "circle",
                "text": f"平 {t.exit_price:.2f} ({t.pnl:+.2f})",
            })
        markers.sort(key=lambda m: m["time"])
        return markers
