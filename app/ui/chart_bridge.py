from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Any

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView

from app.domain.candle import Candle, Timeframe


_HTML_PATH = Path(__file__).parent / "resources" / "chart.html"


class _Bridge(QObject):
    chart_ready_signal = Signal()

    @Slot()
    def chartReady(self):
        self.chart_ready_signal.emit()


class ChartWidget(QWebEngineView):
    chart_ready = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bridge = _Bridge()
        self._bridge.chart_ready_signal.connect(self._on_ready)
        self._channel = QWebChannel()
        self._channel.registerObject("bridge", self._bridge)
        self.page().setWebChannel(self._channel)

        self._is_ready = False
        self._pending_calls: list[str] = []
        self._timeframe: Timeframe = Timeframe.DAILY

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

    def set_timeframe(self, tf: Timeframe) -> None:
        self._timeframe = tf

    def set_candles(self, candles: List[Candle]) -> None:
        candle_data = [c.to_chart_dict(self._timeframe) for c in candles]
        volume_data = [c.to_volume_dict(self._timeframe) for c in candles]
        cj = json.dumps(candle_data, ensure_ascii=False)
        vj = json.dumps(volume_data, ensure_ascii=False)
        self._run_js(f"setData({cj}, {vj})")

    def add_candle(self, candle: Candle) -> None:
        cd = json.dumps(candle.to_chart_dict(self._timeframe), ensure_ascii=False)
        vd = json.dumps(candle.to_volume_dict(self._timeframe), ensure_ascii=False)
        self._run_js(f"addCandle({cd}, {vd})")

    def set_markers(self, markers: list) -> None:
        mj = json.dumps(markers, ensure_ascii=False)
        self._run_js(f"setMarkers({mj})")

    def draw_price_line(self, price: float, color: str = "#FFD700", width: int = 1) -> None:
        self._run_js(f"drawHorizontalLine({price}, '{color}', {width}, 2)")

    def clear(self) -> None:
        self._run_js("clearChart()")

    def fit(self) -> None:
        self._run_js("fitContent()")

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
