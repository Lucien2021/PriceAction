from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QLabel, QPushButton, QComboBox, QLineEdit,
    QSpinBox, QDoubleSpinBox, QGroupBox, QRadioButton,
    QButtonGroup, QStatusBar, QMenuBar, QMessageBox,
    QTabWidget, QSlider, QScrollArea, QTableWidget,
    QTableWidgetItem, QHeaderView, QApplication,
)

from app.domain.candle import (
    Candle, Symbol, Timeframe, MarketType,
    TradeDirection, PredictionDirection,
)
from app.domain.market_rules import AShareRules
from app.data.providers.akshare_provider import AKShareProvider
from app.data.cache.repository import CacheRepository
from app.replay.engine import ReplayEngine
from app.replay.session import ReplaySession, SessionState, TrainingMode
from app.training.predict_mode import PredictMode
from app.training.trade_mode import TradeMode
from app.storage.models import get_connection
from app.storage.stats_service import StatsService
from app.ui.chart_bridge import ChartWidget
from app.ui.review_panel import ReviewPanel

_TF_OPTIONS = [
    ("月线", Timeframe.MONTHLY),
    ("日线", Timeframe.DAILY),
    ("5分钟", Timeframe.M5),
    ("1分钟", Timeframe.M1),
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Price Action 训练器")
        self.setMinimumSize(1200, 750)

        self._cache = CacheRepository()
        self._provider = AKShareProvider()
        self._engine = ReplayEngine(self._cache, self._provider)
        self._rules = AShareRules()
        self._db_conn = get_connection()
        self._stats_service = StatsService(self._db_conn)

        self._session = ReplaySession()
        self._predict_mode: Optional[PredictMode] = None
        self._trade_mode: Optional[TradeMode] = None
        self._pending_limit: Optional[dict] = None

        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._on_auto_advance)

        self._chart_loaded = False
        self._build_ui()
        self._connect_signals()

    # ==================================================================
    # UI
    # ==================================================================

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(4, 4, 4, 4)

        root.addLayout(self._build_toolbar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._chart = ChartWidget()
        splitter.addWidget(self._chart)

        right_tabs = QTabWidget()
        right_tabs.setMinimumWidth(340)

        training_widget = self._build_training_panel()
        training_scroll = QScrollArea()
        training_scroll.setWidget(training_widget)
        training_scroll.setWidgetResizable(True)
        training_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_tabs.addTab(training_scroll, "训练")

        self._review_panel = ReviewPanel(self._stats_service)
        review_scroll = QScrollArea()
        review_scroll.setWidget(self._review_panel)
        review_scroll.setWidgetResizable(True)
        review_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_tabs.addTab(review_scroll, "复盘")

        data_widget = self._build_data_panel()
        data_scroll = QScrollArea()
        data_scroll.setWidget(data_widget)
        data_scroll.setWidgetResizable(True)
        data_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_tabs.addTab(data_scroll, "数据")

        splitter.addWidget(right_tabs)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, stretch=1)

        root.addLayout(self._build_playback_bar())

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._lbl_status = QLabel("就绪")
        self._status.addWidget(self._lbl_status, 1)
        self._lbl_position = QLabel("")
        self._status.addPermanentWidget(self._lbl_position)
        self._lbl_score = QLabel("")
        self._status.addPermanentWidget(self._lbl_score)

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel("代码:"))
        self._inp_symbol = QLineEdit("000001")
        self._inp_symbol.setMaximumWidth(100)
        row.addWidget(self._inp_symbol)
        self._btn_search = QPushButton("搜索")
        row.addWidget(self._btn_search)

        row.addWidget(QLabel("可见K线:"))
        self._spn_visible = QSpinBox()
        self._spn_visible.setRange(20, 200)
        self._spn_visible.setValue(60)
        row.addWidget(self._spn_visible)
        row.addWidget(QLabel("未来K线:"))
        self._spn_future = QSpinBox()
        self._spn_future.setRange(20, 500)
        self._spn_future.setValue(120)
        row.addWidget(self._spn_future)

        row.addWidget(QLabel("周期:"))
        self._cmb_tf = QComboBox()
        for label, _ in _TF_OPTIONS:
            self._cmb_tf.addItem(label)
        self._cmb_tf.setCurrentIndex(1)
        row.addWidget(self._cmb_tf)

        self._btn_download = QPushButton("下载数据")
        row.addWidget(self._btn_download)

        self._btn_start = QPushButton("开始训练")
        self._btn_start.setStyleSheet("font-weight:bold;")
        row.addWidget(self._btn_start)

        row.addStretch()
        return row

    def _build_training_panel(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)
        layout.setContentsMargins(6, 6, 6, 6)

        mode_group = QGroupBox("训练模式")
        mg = QHBoxLayout(mode_group)
        self._rb_trade = QRadioButton("模拟交易")
        self._rb_predict = QRadioButton("方向预测")
        self._rb_trade.setChecked(True)
        self._mode_group = QButtonGroup()
        self._mode_group.addButton(self._rb_trade, 0)
        self._mode_group.addButton(self._rb_predict, 1)
        mg.addWidget(self._rb_trade)
        mg.addWidget(self._rb_predict)
        layout.addWidget(mode_group)

        self._trade_box = QGroupBox("交易操作")
        tb = QVBoxLayout(self._trade_box)
        tb.setSpacing(6)

        qty_row = QHBoxLayout()
        qty_row.addWidget(QLabel("数量:"))
        self._spn_qty = QSpinBox()
        self._spn_qty.setRange(100, 100000)
        self._spn_qty.setSingleStep(100)
        self._spn_qty.setValue(1000)
        qty_row.addWidget(self._spn_qty)
        tb.addLayout(qty_row)

        sl_row = QHBoxLayout()
        sl_row.addWidget(QLabel("止损:"))
        self._spn_sl = QDoubleSpinBox()
        self._spn_sl.setRange(0, 99999)
        self._spn_sl.setDecimals(2)
        self._spn_sl.setSpecialValueText("无")
        sl_row.addWidget(self._spn_sl)
        tb.addLayout(sl_row)

        tp_row = QHBoxLayout()
        tp_row.addWidget(QLabel("止盈:"))
        self._spn_tp = QDoubleSpinBox()
        self._spn_tp.setRange(0, 99999)
        self._spn_tp.setDecimals(2)
        self._spn_tp.setSpecialValueText("无")
        tp_row.addWidget(self._spn_tp)
        tb.addLayout(tp_row)

        btn_row = QHBoxLayout()
        self._btn_buy = QPushButton("市价做多")
        self._btn_buy.setMinimumHeight(32)
        self._btn_buy.setStyleSheet("background:#ef5350;color:white;font-weight:bold;")
        self._btn_sell = QPushButton("市价做空")
        self._btn_sell.setMinimumHeight(32)
        self._btn_sell.setStyleSheet("background:#26a69a;color:white;font-weight:bold;")
        self._btn_sell.setEnabled(False)
        self._btn_close = QPushButton("平仓")
        self._btn_close.setMinimumHeight(32)
        btn_row.addWidget(self._btn_buy)
        btn_row.addWidget(self._btn_sell)
        btn_row.addWidget(self._btn_close)
        tb.addLayout(btn_row)

        limit_row = QHBoxLayout()
        self._btn_limit_buy = QPushButton("限价买入")
        self._btn_limit_buy.setMinimumHeight(28)
        self._btn_limit_buy.setStyleSheet("background:#b85450;color:white;")
        self._btn_limit_sell = QPushButton("限价卖出")
        self._btn_limit_sell.setMinimumHeight(28)
        self._btn_limit_sell.setStyleSheet("background:#1e8c7e;color:white;")
        self._btn_limit_sell.setEnabled(False)
        self._btn_cancel_limit = QPushButton("撤单")
        self._btn_cancel_limit.setMinimumHeight(28)
        limit_row.addWidget(self._btn_limit_buy)
        limit_row.addWidget(self._btn_limit_sell)
        limit_row.addWidget(self._btn_cancel_limit)
        tb.addLayout(limit_row)

        self._lbl_trade_info = QLabel("无持仓")
        self._lbl_trade_info.setWordWrap(True)
        self._lbl_trade_info.setMinimumHeight(50)
        tb.addWidget(self._lbl_trade_info)
        layout.addWidget(self._trade_box)

        self._predict_box = QGroupBox("方向预测")
        pb = QVBoxLayout(self._predict_box)
        pb.setSpacing(6)
        pred_row = QHBoxLayout()
        self._btn_up = QPushButton("看涨")
        self._btn_up.setMinimumHeight(32)
        self._btn_up.setStyleSheet("background:#ef5350;color:white;font-weight:bold;")
        self._btn_down = QPushButton("看跌")
        self._btn_down.setMinimumHeight(32)
        self._btn_down.setStyleSheet("background:#26a69a;color:white;font-weight:bold;")
        self._btn_side = QPushButton("震荡")
        self._btn_side.setMinimumHeight(32)
        pred_row.addWidget(self._btn_up)
        pred_row.addWidget(self._btn_down)
        pred_row.addWidget(self._btn_side)
        pb.addLayout(pred_row)
        look_row = QHBoxLayout()
        look_row.addWidget(QLabel("预测前瞻:"))
        self._spn_look = QSpinBox()
        self._spn_look.setRange(1, 20)
        self._spn_look.setValue(3)
        look_row.addWidget(self._spn_look)
        look_row.addWidget(QLabel("根"))
        pb.addLayout(look_row)
        self._lbl_predict_info = QLabel("暂无预测")
        self._lbl_predict_info.setWordWrap(True)
        self._lbl_predict_info.setMinimumHeight(40)
        pb.addWidget(self._lbl_predict_info)
        self._predict_box.setVisible(False)
        layout.addWidget(self._predict_box)

        stats_group = QGroupBox("本轮统计")
        sg = QVBoxLayout(stats_group)
        self._lbl_live_stats = QLabel("--")
        self._lbl_live_stats.setWordWrap(True)
        self._lbl_live_stats.setMinimumHeight(60)
        sg.addWidget(self._lbl_live_stats)
        layout.addWidget(stats_group)

        self._btn_finish = QPushButton("结束训练并保存")
        self._btn_finish.setMinimumHeight(34)
        layout.addWidget(self._btn_finish)

        layout.addStretch()
        return w

    def _build_playback_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()

        self._btn_next = QPushButton("▶|")
        self._btn_next5 = QPushButton("▶▶5")
        self._btn_play = QPushButton("▶ 播放")
        self._btn_pause = QPushButton("⏸ 暂停")
        self._btn_pause.setVisible(False)

        row.addWidget(self._btn_next)
        row.addWidget(self._btn_next5)
        row.addWidget(self._btn_play)
        row.addWidget(self._btn_pause)

        row.addWidget(QLabel("速度:"))
        self._sld_speed = QSlider(Qt.Orientation.Horizontal)
        self._sld_speed.setRange(100, 3000)
        self._sld_speed.setValue(800)
        self._sld_speed.setMaximumWidth(150)
        row.addWidget(self._sld_speed)

        self._lbl_bar_info = QLabel("K线: 0/0")
        row.addWidget(self._lbl_bar_info)
        row.addStretch()
        return row

    def _build_data_panel(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(6)
        layout.setContentsMargins(6, 6, 6, 6)

        layout.addWidget(QLabel("已下载数据:"))
        self._data_table = QTableWidget()
        self._data_table.setColumnCount(5)
        self._data_table.setHorizontalHeaderLabels(["代码", "周期", "K线数", "起始", "结束"])
        self._data_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._data_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._data_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._data_table)

        btn_row = QHBoxLayout()
        self._btn_refresh_data = QPushButton("刷新列表")
        self._btn_refresh_data.clicked.connect(self._refresh_data_table)
        self._btn_delete_sel = QPushButton("删除选中")
        self._btn_delete_sel.clicked.connect(self._on_delete_selected_data)
        self._btn_delete_all = QPushButton("清空全部")
        self._btn_delete_all.clicked.connect(self._on_delete_all_data)
        btn_row.addWidget(self._btn_refresh_data)
        btn_row.addWidget(self._btn_delete_sel)
        btn_row.addWidget(self._btn_delete_all)
        layout.addLayout(btn_row)

        layout.addStretch()
        return w

    # ==================================================================
    # Signals
    # ==================================================================

    def _connect_signals(self):
        self._chart.chart_ready.connect(self._on_chart_ready)
        self._chart.limit_price_changed.connect(self._on_limit_price_dragged)
        self._btn_download.clicked.connect(self._on_download)
        self._btn_start.clicked.connect(self._on_start_session)
        self._btn_finish.clicked.connect(self._on_finish_session)

        self._btn_next.clicked.connect(lambda: self._advance(1))
        self._btn_next5.clicked.connect(lambda: self._advance(5))
        self._btn_play.clicked.connect(self._on_play)
        self._btn_pause.clicked.connect(self._on_pause)
        self._sld_speed.valueChanged.connect(self._on_speed_change)

        self._btn_buy.clicked.connect(self._on_buy)
        self._btn_sell.clicked.connect(self._on_sell)
        self._btn_close.clicked.connect(self._on_close_position)
        self._btn_limit_buy.clicked.connect(self._on_limit_buy)
        self._btn_limit_sell.clicked.connect(self._on_limit_sell)
        self._btn_cancel_limit.clicked.connect(self._on_cancel_limit)

        self._btn_up.clicked.connect(lambda: self._on_predict(PredictionDirection.UP))
        self._btn_down.clicked.connect(lambda: self._on_predict(PredictionDirection.DOWN))
        self._btn_side.clicked.connect(lambda: self._on_predict(PredictionDirection.SIDEWAYS))

        self._mode_group.idToggled.connect(self._on_mode_toggle)

    # ==================================================================
    # Handlers
    # ==================================================================

    def _selected_tf(self) -> Timeframe:
        return _TF_OPTIONS[self._cmb_tf.currentIndex()][1]

    def _on_chart_ready(self):
        self._chart_loaded = True
        self._lbl_status.setText("图表就绪")

    def _on_download(self):
        code = self._inp_symbol.text().strip()
        if not code:
            return
        symbol = Symbol(code=code, name=code, market_type=MarketType.A_SHARE)
        tf = self._selected_tf()

        self._btn_download.setEnabled(False)
        self._lbl_status.setText(f"正在下载 {code} {tf.label} ...")
        QApplication.processEvents()

        from datetime import datetime as _dt
        sd = "20100101"
        ed = _dt.now().strftime("%Y%m%d")

        try:
            cnt = self._engine.ensure_data(symbol, tf, start_date=sd, end_date=ed, force=True)
            self._lbl_status.setText(f"下载完成 {code} {tf.label}: {cnt} 根K线")
        except Exception as e:
            self._lbl_status.setText(f"下载失败 {code} {tf.label}: {e}")
            QMessageBox.warning(self, "下载失败", f"{tf.label} 下载失败:\n{e}\n\n请稍后重试。")

        self._btn_download.setEnabled(True)
        self._refresh_data_table()

    def _on_start_session(self):
        code = self._inp_symbol.text().strip()
        if not code:
            return
        symbol = Symbol(code=code, name=code, market_type=MarketType.A_SHARE)
        tf = self._selected_tf()

        if not self._cache.has_data(symbol, tf):
            QMessageBox.information(self, "提示", f"请先下载 {tf.label} 数据")
            return

        visible_n = self._spn_visible.value()
        future_n = self._spn_future.value()

        try:
            visible, future = self._engine.random_slice(symbol, tf, visible_n, future_n)
        except ValueError as e:
            QMessageBox.warning(self, "数据不足", str(e))
            return

        mode = TrainingMode.TRADE if self._rb_trade.isChecked() else TrainingMode.PREDICT
        self._session = ReplaySession()
        self._session.setup(symbol, tf, mode, visible, future)
        self._session.start()

        self._trade_mode = TradeMode(self._session) if mode == TrainingMode.TRADE else None
        self._predict_mode = PredictMode(self._session) if mode == TrainingMode.PREDICT else None
        self._btn_sell.setEnabled(self._rules.allows_short())
        self._btn_limit_sell.setEnabled(self._rules.allows_short())

        self._chart.set_timeframe(tf)
        self._chart.set_candles(visible)
        self._chart.set_ma_data(visible)
        self._update_bar_label()
        self._update_live_stats()

        self._lbl_status.setText(
            f"训练开始: {code} {tf.label} | "
            f"{visible[0].timestamp.strftime('%Y-%m-%d')} ~ "
            f"{future[-1].timestamp.strftime('%Y-%m-%d')} | "
            f"可见{len(visible)} + 未来{len(future)}"
        )

    def _advance(self, steps: int = 1):
        if self._session.state != SessionState.RUNNING:
            return

        old_count = len(self._session.displayed_candles)

        if self._trade_mode:
            closed = self._trade_mode.advance_and_check(steps)
            if closed:
                self._on_trade_closed(closed)
        elif self._predict_mode:
            self._predict_mode.reveal_and_evaluate(steps)
        else:
            self._session.advance(steps)

        new_candles = self._session.displayed_candles[old_count:]
        for candle in new_candles:
            self._chart.add_candle(candle)
            self._check_limit_order(candle)

        if new_candles:
            self._chart.add_ma_point(self._session.displayed_candles)

        self._refresh_markers()
        self._update_position_display()
        self._update_bar_label()
        self._update_live_stats()

        if self._session.state == SessionState.FINISHED:
            self._on_pause()
            self._lbl_status.setText("回放结束")

    def _on_play(self):
        if self._session.state == SessionState.PAUSED:
            self._session.resume()
        if self._session.state != SessionState.RUNNING:
            return
        self._play_timer.start(self._sld_speed.value())
        self._btn_play.setVisible(False)
        self._btn_pause.setVisible(True)

    def _on_pause(self):
        self._play_timer.stop()
        if self._session.state == SessionState.RUNNING:
            self._session.pause()
        self._btn_play.setVisible(True)
        self._btn_pause.setVisible(False)

    def _on_auto_advance(self):
        self._advance(1)
        if self._session.state == SessionState.FINISHED:
            self._on_pause()

    def _on_speed_change(self, val):
        if self._play_timer.isActive():
            self._play_timer.setInterval(val)

    def _on_mode_toggle(self, btn_id, checked):
        if not checked:
            return
        self._trade_box.setVisible(btn_id == 0)
        self._predict_box.setVisible(btn_id != 0)

    # --- Trade ---

    def _on_buy(self):
        if not self._trade_mode or self._session.state != SessionState.RUNNING:
            return
        sl = self._spn_sl.value() or None
        tp = self._spn_tp.value() or None
        pos = self._trade_mode.open_long(self._spn_qty.value(), sl, tp)
        if pos:
            self._update_position_display()

    def _on_sell(self):
        if not self._trade_mode or self._session.state != SessionState.RUNNING:
            return
        sl = self._spn_sl.value() or None
        tp = self._spn_tp.value() or None
        pos = self._trade_mode.open_short(self._spn_qty.value(), sl, tp)
        if pos:
            self._update_position_display()

    def _on_close_position(self):
        if not self._trade_mode:
            return
        closed = self._trade_mode.close("manual")
        if closed:
            self._on_trade_closed(closed)

    def _on_trade_closed(self, trade):
        self._chart.remove_all_limits()
        self._pending_limit = None
        self._refresh_markers()
        self._update_position_display()
        self._update_live_stats()

    # --- Limit orders ---

    def _on_limit_buy(self):
        if not self._trade_mode or self._session.state != SessionState.RUNNING:
            return
        if self._session.position:
            QMessageBox.information(self, "提示", "已有持仓，请先平仓")
            return
        candle = self._session.current_candle
        if not candle:
            return
        price = round(candle.close * 0.99, 2)
        oid = "limit_buy"
        self._pending_limit = {"id": oid, "price": price, "direction": "long"}
        self._chart.add_limit_order(oid, price, "long", "#ef5350")
        self._lbl_trade_info.setText(f"限价买入委托: {price:.2f}\n(拖动线调整价格)")

    def _on_limit_sell(self):
        if not self._trade_mode or self._session.state != SessionState.RUNNING:
            return
        if self._session.position:
            QMessageBox.information(self, "提示", "已有持仓，请先平仓")
            return
        candle = self._session.current_candle
        if not candle:
            return
        price = round(candle.close * 1.01, 2)
        oid = "limit_sell"
        self._pending_limit = {"id": oid, "price": price, "direction": "short"}
        self._chart.add_limit_order(oid, price, "short", "#26a69a")
        self._lbl_trade_info.setText(f"限价卖出委托: {price:.2f}\n(拖动线调整价格)")

    def _on_cancel_limit(self):
        if self._pending_limit:
            self._chart.remove_limit_order(self._pending_limit["id"])
            self._pending_limit = None
            self._lbl_trade_info.setText("委托已撤销")

    def _on_limit_price_dragged(self, order_id: str, new_price: float):
        if self._pending_limit and self._pending_limit["id"] == order_id:
            self._pending_limit["price"] = new_price
            d = "买入" if self._pending_limit["direction"] == "long" else "卖出"
            self._lbl_trade_info.setText(f"限价{d}委托: {new_price:.2f}\n(拖动线调整价格)")

    def _check_limit_order(self, candle):
        if not self._pending_limit or not self._trade_mode or self._session.position:
            return
        lo = self._pending_limit
        price = lo["price"]
        # K线必须实际触及限价线：low <= 限价 <= high
        if not (candle.low <= price <= candle.high):
            return
        triggered = False
        if lo["direction"] == "long":
            triggered = True
            pos = self._trade_mode.open_long(
                self._spn_qty.value(),
                self._spn_sl.value() or None,
                self._spn_tp.value() or None,
            )
            if pos:
                pos.entry_price = price
        elif lo["direction"] == "short":
            triggered = True
            pos = self._trade_mode.open_short(
                self._spn_qty.value(),
                self._spn_sl.value() or None,
                self._spn_tp.value() or None,
            )
            if pos:
                pos.entry_price = price
        if triggered:
            self._chart.remove_limit_order(lo["id"])
            self._pending_limit = None
            self._update_position_display()
            self._lbl_status.setText(f"限价委托成交 @ {price:.2f}")

    # --- Predict ---

    def _on_predict(self, direction: PredictionDirection):
        if not self._predict_mode or self._session.state != SessionState.RUNNING:
            return
        self._predict_mode.predict(direction, self._spn_look.value())
        self._update_live_stats()

    # --- Finish ---

    def _on_finish_session(self):
        if self._session.state == SessionState.IDLE:
            return
        self._on_pause()
        if self._session.position and self._trade_mode:
            self._trade_mode.close("session_end")
        self._session.finish()
        if self._predict_mode:
            self._session.evaluate_predictions()
        self._stats_service.save_session(self._session)
        self._review_panel.refresh()
        self._update_live_stats()
        QMessageBox.information(self, "训练结束", "训练记录已保存!\n切换到[复盘]标签查看详情。")
        self._lbl_status.setText("训练已保存")

    # --- Data management ---

    def _refresh_data_table(self):
        summary = self._cache.get_cache_summary()
        self._data_table.setRowCount(len(summary))
        for i, item in enumerate(summary):
            self._data_table.setItem(i, 0, QTableWidgetItem(item["symbol"]))
            self._data_table.setItem(i, 1, QTableWidgetItem(item["tf"]))
            self._data_table.setItem(i, 2, QTableWidgetItem(str(item["bars"])))
            self._data_table.setItem(i, 3, QTableWidgetItem(item["start"]))
            self._data_table.setItem(i, 4, QTableWidgetItem(item["end"]))

    def _on_delete_selected_data(self):
        rows = set(idx.row() for idx in self._data_table.selectedIndexes())
        if not rows:
            return
        if QMessageBox.question(self, "确认", "删除选中数据?") != QMessageBox.StandardButton.Yes:
            return
        for r in sorted(rows, reverse=True):
            sym = self._data_table.item(r, 0).text()
            tf = self._data_table.item(r, 1).text()
            self._cache.delete_symbol_data(sym, tf)
        self._refresh_data_table()

    def _on_delete_all_data(self):
        if QMessageBox.question(self, "确认", "清空所有已下载数据?") != QMessageBox.StandardButton.Yes:
            return
        for sym in self._cache.list_cached_symbols():
            self._cache.delete_symbol_data(sym)
        self._refresh_data_table()

    # ==================================================================
    # Display helpers
    # ==================================================================

    def _update_bar_label(self):
        idx = self._session.current_index
        total = self._session.total_future_bars
        tf = self._session.timeframe
        label = tf.label if tf else ""
        self._lbl_bar_info.setText(f"K线: {idx}/{total}  [{label}]")

    def _update_position_display(self):
        pos = self._session.position
        if pos is None:
            self._lbl_trade_info.setText("无持仓")
            self._lbl_position.setText("")
            return
        candle = self._session.current_candle
        cur_price = candle.close if candle else pos.entry_price
        pnl = pos.unrealized_pnl(cur_price)
        pct = pos.unrealized_pnl_pct(cur_price)
        r = pos.unrealized_r(cur_price)
        r_str = f"  {r:.2f}R" if r is not None else ""
        d = "多" if pos.is_long else "空"
        self._lbl_trade_info.setText(
            f"方向: {d}  入场: {pos.entry_price:.2f}\n"
            f"浮动盈亏: {pnl:+.2f} ({pct:+.2f}%){r_str}\n"
            f"止损: {pos.stop_loss or '无'}  止盈: {pos.take_profit or '无'}"
        )
        self._lbl_position.setText(f"持仓 {d} {pnl:+.2f}")

    def _update_live_stats(self):
        if self._trade_mode:
            self._lbl_live_stats.setText(self._trade_mode.summary_text())
            self._lbl_score.setText(f"胜率 {self._trade_mode.compute_stats().win_rate:.0%}")
        elif self._predict_mode:
            self._lbl_live_stats.setText(self._predict_mode.summary_text())
            self._lbl_score.setText(f"准确率 {self._predict_mode.get_result().accuracy:.0%}")

    def _refresh_markers(self):
        if not self._session.closed_trades:
            return
        tf = self._session.timeframe or Timeframe.DAILY
        markers = self._chart.build_trade_markers(self._session.closed_trades, tf)
        self._chart.set_markers(markers)

    # ==================================================================

    def closeEvent(self, event):
        self._play_timer.stop()
        self._cache.close()
        self._stats_service.close()
        super().closeEvent(event)
