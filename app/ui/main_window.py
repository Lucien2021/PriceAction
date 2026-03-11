from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QLabel, QPushButton, QComboBox, QLineEdit,
    QSpinBox, QDoubleSpinBox, QGroupBox, QRadioButton,
    QButtonGroup, QStatusBar, QMenuBar, QMessageBox,
    QTabWidget, QSlider,
)

from app.domain.candle import (
    Symbol, Timeframe, MarketType,
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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Price Action 训练器")
        self.setMinimumSize(1200, 750)

        # services
        self._cache = CacheRepository()
        self._provider = AKShareProvider()
        self._engine = ReplayEngine(self._cache, self._provider)
        self._rules = AShareRules()
        self._db_conn = get_connection()
        self._stats_service = StatsService(self._db_conn)

        # session state
        self._session = ReplaySession()
        self._predict_mode: Optional[PredictMode] = None
        self._trade_mode: Optional[TradeMode] = None

        # auto-play timer
        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._on_auto_advance)

        self._chart_loaded = False
        self._build_ui()
        self._connect_signals()

    # ==================================================================
    # UI construction
    # ==================================================================

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(4, 4, 4, 4)

        # --- Toolbar ---
        toolbar = self._build_toolbar()
        root.addLayout(toolbar)

        # --- Main splitter ---
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Chart
        self._chart = ChartWidget()
        splitter.addWidget(self._chart)

        # Right panel tabs
        right_tabs = QTabWidget()
        right_tabs.setMaximumWidth(380)
        right_tabs.setMinimumWidth(300)

        # Training control tab
        training_widget = self._build_training_panel()
        right_tabs.addTab(training_widget, "训练")

        # Review tab
        self._review_panel = ReviewPanel(self._stats_service)
        right_tabs.addTab(self._review_panel, "复盘")

        splitter.addWidget(right_tabs)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, stretch=1)

        # --- Playback bar ---
        playback = self._build_playback_bar()
        root.addLayout(playback)

        # --- Status bar ---
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

        row.addWidget(QLabel("周期:"))
        self._cmb_tf = QComboBox()
        for tf in [Timeframe.DAILY, Timeframe.H1, Timeframe.M30, Timeframe.M15]:
            self._cmb_tf.addItem(tf.label, tf)
        row.addWidget(self._cmb_tf)

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

        # Mode selection
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

        # --- Trade controls ---
        self._trade_box = QGroupBox("交易操作")
        tb = QVBoxLayout(self._trade_box)

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
        sl_row.addWidget(QLabel("止盈:"))
        self._spn_tp = QDoubleSpinBox()
        self._spn_tp.setRange(0, 99999)
        self._spn_tp.setDecimals(2)
        self._spn_tp.setSpecialValueText("无")
        sl_row.addWidget(self._spn_tp)
        tb.addLayout(sl_row)

        btn_row = QHBoxLayout()
        self._btn_buy = QPushButton("做多")
        self._btn_buy.setStyleSheet("background:#ef5350; color:white; font-weight:bold;")
        self._btn_sell = QPushButton("做空")
        self._btn_sell.setStyleSheet("background:#26a69a; color:white; font-weight:bold;")
        self._btn_sell.setEnabled(False)
        self._btn_close = QPushButton("平仓")
        btn_row.addWidget(self._btn_buy)
        btn_row.addWidget(self._btn_sell)
        btn_row.addWidget(self._btn_close)
        tb.addLayout(btn_row)

        self._lbl_trade_info = QLabel("无持仓")
        self._lbl_trade_info.setWordWrap(True)
        tb.addWidget(self._lbl_trade_info)
        layout.addWidget(self._trade_box)

        # --- Predict controls ---
        self._predict_box = QGroupBox("方向预测")
        pb = QVBoxLayout(self._predict_box)
        pred_row = QHBoxLayout()
        self._btn_up = QPushButton("看涨 ▲")
        self._btn_up.setStyleSheet("background:#ef5350; color:white;")
        self._btn_down = QPushButton("看跌 ▼")
        self._btn_down.setStyleSheet("background:#26a69a; color:white;")
        self._btn_side = QPushButton("震荡 ◆")
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
        pb.addWidget(self._lbl_predict_info)
        self._predict_box.setVisible(False)
        layout.addWidget(self._predict_box)

        # --- Live stats ---
        stats_group = QGroupBox("本轮统计")
        sg = QVBoxLayout(stats_group)
        self._lbl_live_stats = QLabel("—")
        self._lbl_live_stats.setWordWrap(True)
        self._lbl_live_stats.setStyleSheet("font-size: 12px;")
        sg.addWidget(self._lbl_live_stats)
        layout.addWidget(stats_group)

        # --- End session ---
        self._btn_finish = QPushButton("结束训练并保存")
        layout.addWidget(self._btn_finish)

        layout.addStretch()
        return w

    def _build_playback_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._btn_prev = QPushButton("|◀")
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

    # ==================================================================
    # Signal wiring
    # ==================================================================

    def _connect_signals(self):
        self._chart.chart_ready.connect(self._on_chart_ready)
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

        self._btn_up.clicked.connect(lambda: self._on_predict(PredictionDirection.UP))
        self._btn_down.clicked.connect(lambda: self._on_predict(PredictionDirection.DOWN))
        self._btn_side.clicked.connect(lambda: self._on_predict(PredictionDirection.SIDEWAYS))

        self._mode_group.idToggled.connect(self._on_mode_toggle)

    # ==================================================================
    # Handlers
    # ==================================================================

    def _on_chart_ready(self):
        self._chart_loaded = True
        self._lbl_status.setText("图表就绪")

    def _on_download(self):
        code = self._inp_symbol.text().strip()
        if not code:
            return
        tf: Timeframe = self._cmb_tf.currentData()
        symbol = Symbol(code=code, name=code, market_type=MarketType.A_SHARE)
        self._lbl_status.setText(f"正在下载 {code} {tf.label} …")
        try:
            count = self._engine.ensure_data(symbol, tf)
            self._lbl_status.setText(f"下载完成: {code} {tf.label} 共 {count} 根K线")
        except Exception as e:
            QMessageBox.warning(self, "下载失败", str(e))
            self._lbl_status.setText("下载失败")

    def _on_start_session(self):
        code = self._inp_symbol.text().strip()
        if not code:
            return
        tf: Timeframe = self._cmb_tf.currentData()
        symbol = Symbol(code=code, name=code, market_type=MarketType.A_SHARE)

        if not self._cache.has_data(symbol, tf):
            QMessageBox.information(self, "提示", "请先下载数据")
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

        self._chart.set_timeframe(tf)
        self._chart.set_candles(visible)
        self._update_bar_label()
        self._update_live_stats()
        self._lbl_status.setText(f"训练开始: {code} {tf.label}")

    def _advance(self, steps: int = 1):
        if self._session.state != SessionState.RUNNING:
            return

        if self._trade_mode:
            closed = self._trade_mode.advance_and_check(steps)
            if closed:
                self._on_trade_closed(closed)
        elif self._predict_mode:
            self._predict_mode.reveal_and_evaluate(steps)
        else:
            self._session.advance(steps)

        revealed = self._session.displayed_candles
        self._chart.set_candles(revealed)
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

    # --- Mode toggle ---

    def _on_mode_toggle(self, btn_id, checked):
        if not checked:
            return
        is_trade = btn_id == 0
        self._trade_box.setVisible(is_trade)
        self._predict_box.setVisible(not is_trade)

    # --- Trade actions ---

    def _on_buy(self):
        if not self._trade_mode or self._session.state != SessionState.RUNNING:
            return
        sl = self._spn_sl.value() or None
        tp = self._spn_tp.value() or None
        pos = self._trade_mode.open_long(self._spn_qty.value(), sl, tp)
        if pos:
            self._update_position_display()
            self._chart.draw_price_line(pos.entry_price, "#FFD700")
            if sl:
                self._chart.draw_price_line(sl, "#ef5350")
            if tp:
                self._chart.draw_price_line(tp, "#26a69a")

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
        self._refresh_markers()
        self._update_position_display()
        self._update_live_stats()

    # --- Predict actions ---

    def _on_predict(self, direction: PredictionDirection):
        if not self._predict_mode or self._session.state != SessionState.RUNNING:
            return
        self._predict_mode.predict(direction, self._spn_look.value())
        self._update_live_stats()

    # --- Finish session ---

    def _on_finish_session(self):
        if self._session.state in (SessionState.IDLE,):
            return
        self._on_pause()

        if self._session.position:
            self._trade_mode.close("session_end") if self._trade_mode else None

        self._session.finish()
        if self._predict_mode:
            self._session.evaluate_predictions()

        self._stats_service.save_session(self._session)
        self._review_panel.refresh()
        self._update_live_stats()

        QMessageBox.information(self, "训练结束", "训练记录已保存!\n切换到[复盘]标签查看详情。")
        self._lbl_status.setText("训练已保存")

    # ==================================================================
    # Display helpers
    # ==================================================================

    def _update_bar_label(self):
        idx = self._session.current_index
        total = self._session.total_future_bars
        self._lbl_bar_info.setText(f"K线: {idx}/{total}")

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
            self._lbl_score.setText(
                f"胜率 {self._trade_mode.compute_stats().win_rate:.0%}"
            )
        elif self._predict_mode:
            self._lbl_live_stats.setText(self._predict_mode.summary_text())
            self._lbl_score.setText(
                f"准确率 {self._predict_mode.get_result().accuracy:.0%}"
            )

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
