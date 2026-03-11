from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView

from app.domain.candle import Candle, Timeframe


_HTML_PATH = Path(__file__).parent / "resources" / "chart.html"


class _Bridge(QObject):
    """Object exposed to JavaScript via QWebChannel."""
    chart_ready_signal = Signal()

    @Slot()
    def chartReady(self):
        self.chart_ready_signal.emit()


class ChartWidget(QWebEngineView):
    chart_ready = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bridge = _Bridge()
        self._bridge.chart_ready_signal.connect(self.chart_ready)
        self._channel = QWebChannel()
        self._channel.registerObject("bridge", self._bridge)
        self.page().setWebChannel(self._channel)
        self.setUrl(QUrl.fromLocalFile(str(_HTML_PATH.resolve())))
        self._timeframe: Timeframe = Timeframe.DAILY

    def set_timeframe(self, tf: Timeframe) -> None:
        self._timeframe = tf

    def set_candles(self, candles: List[Candle]) -> None:
        candle_data = [c.to_chart_dict(self._timeframe) for c in candles]
        volume_data = [c.to_volume_dict(self._timeframe) for c in candles]
        cj = json.dumps(candle_data)
        vj = json.dumps(volume_data)
        self.page().runJavaScript(f"setData('{cj}', '{vj}')")

    def add_candle(self, candle: Candle) -> None:
        cd = json.dumps(candle.to_chart_dict(self._timeframe))
        vd = json.dumps(candle.to_volume_dict(self._timeframe))
        self.page().runJavaScript(f"addCandle('{cd}', '{vd}')")

    def set_markers(self, markers: list) -> None:
        mj = json.dumps(markers)
        self.page().runJavaScript(f"setMarkers('{mj}')")

    def draw_price_line(self, price: float, color: str = "#FFD700", width: int = 1) -> None:
        self.page().runJavaScript(f"drawHorizontalLine({price}, '{color}', {width}, 2)")

    def clear(self) -> None:
        self.page().runJavaScript("clearChart()")

    def fit(self) -> None:
        self.page().runJavaScript("fitContent()")

    def build_trade_markers(self, trades, timeframe: Timeframe) -> list:
        markers = []
        for t in trades:
            entry_time = t.entry_time
            exit_time = t.exit_time
            if timeframe.minutes >= Timeframe.DAILY.minutes:
                et = entry_time.strftime("%Y-%m-%d")
                xt = exit_time.strftime("%Y-%m-%d")
            else:
                et = int(entry_time.timestamp())
                xt = int(exit_time.timestamp())

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
