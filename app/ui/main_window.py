from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.data.cache.repository import CacheRepository
from app.data.providers.akshare_provider import AKShareProvider
from app.domain.candle import MarketType, PredictionDirection, Symbol, Timeframe, TradeDirection
from app.domain.market_rules import AShareRules
from app.replay.engine import ReplayEngine
from app.replay.session import ReplaySession, SessionState, TrainingMode
from app.storage.models import get_connection
from app.storage.stats_service import StatsService
from app.training.predict_mode import PredictMode
from app.training.trade_mode import TradeMode
from app.ui.chart_bridge import ChartWidget
from app.ui.review_panel import ReviewPanel

_SNAPSHOT_DIR = Path.home() / ".priceaction" / "snapshots"

_TF_OPTIONS = [
    ("月线", Timeframe.MONTHLY),
    ("日线", Timeframe.DAILY),
    ("5分钟", Timeframe.M5),
    ("1分钟", Timeframe.M1),
]

_SETUP_OPTIONS = [
    "趋势回踩", "区间突破", "假突破反手", "反转确认", "供需区", "自定义",
]

_SCENARIO_OPTIONS = [
    "", "趋势回踩", "区间突破", "假突破", "反转确认", "震荡", "高波动",
]

_MISTAKE_TAGS = [
    "追涨杀跌", "过早止盈", "拖延止损", "计划外交易",
    "错过入场", "无效加仓", "仓位过大", "结构误判",
]


# ======================================================================
# Dialogs
# ======================================================================

class SessionPlanDialog(QDialog):
    def __init__(self, mode: TrainingMode, parent=None):
        super().__init__(parent)
        self._mode = mode
        self.setWindowTitle("训练前计划卡")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        grid = QGridLayout()

        grid.addWidget(QLabel("Setup:"), 0, 0)
        self._cmb_setup = QComboBox()
        for item in _SETUP_OPTIONS:
            self._cmb_setup.addItem(item)
        grid.addWidget(self._cmb_setup, 0, 1)

        grid.addWidget(QLabel("场景模板:"), 1, 0)
        self._cmb_scenario = QComboBox()
        self._cmb_scenario.addItem("随机", "")
        for item in _SCENARIO_OPTIONS[1:]:
            self._cmb_scenario.addItem(item, item)
        grid.addWidget(self._cmb_scenario, 1, 1)

        grid.addWidget(QLabel("预期方向:"), 2, 0)
        self._cmb_direction = QComboBox()
        self._cmb_direction.addItem("未设定", "")
        self._cmb_direction.addItem("看多", "long")
        self._cmb_direction.addItem("看空", "short")
        self._cmb_direction.addItem("震荡", "sideways")
        grid.addWidget(self._cmb_direction, 2, 1)

        grid.addWidget(QLabel("失效条件:"), 3, 0)
        self._txt_invalidation = QLineEdit()
        self._txt_invalidation.setPlaceholderText("例如：跌破前低 / 收回区间内部 / 结构失效")
        grid.addWidget(self._txt_invalidation, 3, 1)

        grid.addWidget(QLabel("难度(1-5):"), 4, 0)
        self._spn_difficulty = QSpinBox()
        self._spn_difficulty.setRange(1, 5)
        self._spn_difficulty.setValue(3)
        grid.addWidget(self._spn_difficulty, 4, 1)

        grid.addWidget(QLabel("单笔风险%:"), 5, 0)
        self._spn_risk = QDoubleSpinBox()
        self._spn_risk.setRange(0.1, 10.0)
        self._spn_risk.setDecimals(2)
        self._spn_risk.setSingleStep(0.25)
        self._spn_risk.setValue(1.0)
        self._spn_risk.setEnabled(mode == TrainingMode.TRADE)
        grid.addWidget(self._spn_risk, 5, 1)
        layout.addLayout(grid)

        self._txt_notes = QTextEdit()
        self._txt_notes.setMinimumHeight(120)
        self._txt_notes.setPlaceholderText(
            "交易模式：写入场逻辑、失效条件、预期出场。\n"
            "PA 模式: 写本次剧本推演, 例如[先等假突破, 再看收回确认]。"
        )
        layout.addWidget(QLabel("计划 / 剧本:"))
        layout.addWidget(self._txt_notes)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_plan(self) -> dict:
        return {
            "setup_type": self._cmb_setup.currentText().strip(),
            "scenario_tag": self._cmb_scenario.currentData() or "",
            "plan_direction": self._cmb_direction.currentData() or "",
            "plan_invalidation": self._txt_invalidation.text().strip(),
            "risk_pct": self._spn_risk.value(),
            "difficulty": self._spn_difficulty.value(),
            "plan_notes": self._txt_notes.toPlainText().strip(),
        }


class TradeReviewDialog(QDialog):
    def __init__(self, trade, snapshot_path: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("交易执行复盘")
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)
        direction = "做多" if trade.direction == TradeDirection.LONG else "做空"
        summary = QLabel(
            f"{direction}  {trade.entry_price:.2f} → {trade.exit_price:.2f}\n"
            f"盈亏: {trade.pnl:+.2f}  |  出场: {trade.exit_reason or 'manual'}"
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        if snapshot_path and Path(snapshot_path).exists():
            from PySide6.QtGui import QPixmap
            pix = QPixmap(snapshot_path)
            if not pix.isNull():
                img_label = QLabel()
                img_label.setPixmap(pix.scaledToWidth(460, Qt.TransformationMode.SmoothTransformation))
                img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(img_label)

        tag_group = QGroupBox("错误分类")
        tag_layout = QGridLayout(tag_group)
        self._mistake_boxes: list[QCheckBox] = []
        for idx, tag in enumerate(_MISTAKE_TAGS):
            box = QCheckBox(tag)
            if tag in getattr(trade, "mistake_tags", []):
                box.setChecked(True)
            self._mistake_boxes.append(box)
            tag_layout.addWidget(box, idx // 2, idx % 2)
        layout.addWidget(tag_group)

        score_row = QHBoxLayout()
        score_row.addWidget(QLabel("执行评分:"))
        self._spn_score = QSpinBox()
        self._spn_score.setRange(0, 100)
        self._spn_score.setSingleStep(5)
        self._spn_score.setValue(getattr(trade, "execution_score", 70) or 70)
        score_row.addWidget(self._spn_score)
        score_row.addStretch()
        layout.addLayout(score_row)

        self._txt_entry = QTextEdit()
        self._txt_entry.setMaximumHeight(70)
        self._txt_entry.setPlaceholderText("入场理由 / 为什么是计划内交易")
        self._txt_entry.setPlainText(getattr(trade, "entry_reason", ""))
        layout.addWidget(QLabel("入场理由:"))
        layout.addWidget(self._txt_entry)

        self._txt_exit = QTextEdit()
        self._txt_exit.setMaximumHeight(90)
        self._txt_exit.setPlaceholderText("出场复盘 / 是否按计划执行")
        self._txt_exit.setPlainText(getattr(trade, "exit_review", ""))
        layout.addWidget(QLabel("出场复盘:"))
        layout.addWidget(self._txt_exit)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_review(self) -> dict:
        return {
            "mistake_tags": [box.text() for box in self._mistake_boxes if box.isChecked()],
            "execution_score": self._spn_score.value(),
            "entry_reason": self._txt_entry.toPlainText().strip(),
            "exit_review": self._txt_exit.toPlainText().strip(),
        }


# ======================================================================
# Main Window
# ======================================================================

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
        self._chart_loaded = False
        self._planned_risk_pct = 1.0
        self._pending_review_trades: List = []

        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._on_auto_advance)

        _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

        self._build_ui()
        self._connect_signals()
        self._refresh_data_table()
        self._review_panel.refresh()

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

        self._left_stack = QStackedWidget()
        self._chart = ChartWidget()
        self._left_stack.addWidget(self._chart)

        self._snapshot_page = QWidget()
        snap_layout = QVBoxLayout(self._snapshot_page)
        snap_layout.setContentsMargins(0, 0, 0, 0)
        snap_top = QHBoxLayout()
        self._btn_back_to_chart = QPushButton("返回图表")
        self._btn_back_to_chart.setMinimumHeight(28)
        self._btn_back_to_chart.clicked.connect(self._hide_snapshot_view)
        snap_top.addWidget(self._btn_back_to_chart)
        self._lbl_snap_title = QLabel("")
        self._lbl_snap_title.setStyleSheet("color:#d1d4dc;font-size:13px;")
        snap_top.addWidget(self._lbl_snap_title)
        snap_top.addStretch()
        snap_layout.addLayout(snap_top)
        self._snapshot_scroll = QScrollArea()
        self._snapshot_scroll.setWidgetResizable(True)
        self._snapshot_scroll.setStyleSheet("background:#1e222d;")
        self._snapshot_image_label = QLabel()
        self._snapshot_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._snapshot_scroll.setWidget(self._snapshot_image_label)
        snap_layout.addWidget(self._snapshot_scroll)
        self._left_stack.addWidget(self._snapshot_page)

        splitter.addWidget(self._left_stack)

        right_tabs = QTabWidget()
        right_tabs.setMinimumWidth(360)

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
        self._rb_predict = QRadioButton("PA / 方向训练")
        self._rb_trade.setChecked(True)
        self._mode_group = QButtonGroup()
        self._mode_group.addButton(self._rb_trade, 0)
        self._mode_group.addButton(self._rb_predict, 1)
        mg.addWidget(self._rb_trade)
        mg.addWidget(self._rb_predict)
        layout.addWidget(mode_group)

        plan_group = QGroupBox("本轮计划")
        pg = QVBoxLayout(plan_group)
        self._lbl_plan = QLabel("开始训练前会弹出计划卡。")
        self._lbl_plan.setWordWrap(True)
        pg.addWidget(self._lbl_plan)
        layout.addWidget(plan_group)

        self._trade_box = QGroupBox("交易操作")
        tb = QVBoxLayout(self._trade_box)
        tb.setSpacing(6)

        risk_row = QHBoxLayout()
        risk_row.addWidget(QLabel("风险%:"))
        self._spn_risk_pct = QDoubleSpinBox()
        self._spn_risk_pct.setRange(0.1, 10.0)
        self._spn_risk_pct.setDecimals(2)
        self._spn_risk_pct.setSingleStep(0.25)
        self._spn_risk_pct.setValue(1.0)
        risk_row.addWidget(self._spn_risk_pct)
        self._chk_auto_qty = QCheckBox("自动按风险算仓位")
        self._chk_auto_qty.setChecked(True)
        risk_row.addWidget(self._chk_auto_qty)
        self._btn_calc_qty = QPushButton("计算仓位")
        risk_row.addWidget(self._btn_calc_qty)
        tb.addLayout(risk_row)

        qty_row = QHBoxLayout()
        qty_row.addWidget(QLabel("数量:"))
        self._spn_qty = QSpinBox()
        self._spn_qty.setRange(100, 500000)
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
        self._btn_close = QPushButton("全平")
        self._btn_close.setMinimumHeight(32)
        btn_row.addWidget(self._btn_buy)
        btn_row.addWidget(self._btn_sell)
        btn_row.addWidget(self._btn_close)
        tb.addLayout(btn_row)

        partial_row = QHBoxLayout()
        self._btn_close_half = QPushButton("平1/2")
        self._btn_close_half.setMinimumHeight(28)
        self._btn_close_third = QPushButton("平1/3")
        self._btn_close_third.setMinimumHeight(28)
        self._btn_close_quarter = QPushButton("平1/4")
        self._btn_close_quarter.setMinimumHeight(28)
        partial_row.addWidget(self._btn_close_half)
        partial_row.addWidget(self._btn_close_third)
        partial_row.addWidget(self._btn_close_quarter)
        tb.addLayout(partial_row)

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

        review_row = QHBoxLayout()
        self._btn_write_review = QPushButton("写本笔复盘")
        self._btn_write_review.setMinimumHeight(28)
        self._btn_write_review.setEnabled(False)
        self._btn_write_review.setStyleSheet("background:#5b5bff;color:white;")
        review_row.addWidget(self._btn_write_review)
        tb.addLayout(review_row)

        self._lbl_trade_info = QLabel("无持仓")
        self._lbl_trade_info.setWordWrap(True)
        self._lbl_trade_info.setMinimumHeight(80)
        tb.addWidget(self._lbl_trade_info)
        layout.addWidget(self._trade_box)

        self._predict_box = QGroupBox("PA / 方向训练")
        pb = QVBoxLayout(self._predict_box)
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

        self._lbl_predict_info = QLabel("你可以在图上用现有工具做结构标注，结束时会保存为 PA 标注。")
        self._lbl_predict_info.setWordWrap(True)
        self._lbl_predict_info.setMinimumHeight(60)
        pb.addWidget(self._lbl_predict_info)
        self._predict_box.setVisible(False)
        layout.addWidget(self._predict_box)

        stats_group = QGroupBox("本轮统计")
        sg = QVBoxLayout(stats_group)
        self._lbl_live_stats = QLabel("--")
        self._lbl_live_stats.setWordWrap(True)
        self._lbl_live_stats.setMinimumHeight(90)
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
        self._btn_delete_sel = QPushButton("删除选中")
        self._btn_delete_all = QPushButton("清空全部")
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
        self._chart.trade_line_changed.connect(self._on_trade_line_dragged)
        self._review_panel.snapshot_to_chart.connect(self._show_snapshot_in_chart)
        self._btn_download.clicked.connect(self._on_download)
        self._btn_start.clicked.connect(self._on_start_session)
        self._btn_finish.clicked.connect(self._on_finish_session)
        self._btn_refresh_data.clicked.connect(self._refresh_data_table)
        self._btn_delete_sel.clicked.connect(self._on_delete_selected_data)
        self._btn_delete_all.clicked.connect(self._on_delete_all_data)

        self._btn_next.clicked.connect(lambda: self._advance(1))
        self._btn_next5.clicked.connect(lambda: self._advance(5))
        self._btn_play.clicked.connect(self._on_play)
        self._btn_pause.clicked.connect(self._on_pause)
        self._sld_speed.valueChanged.connect(self._on_speed_change)

        self._btn_buy.clicked.connect(self._on_buy)
        self._btn_sell.clicked.connect(self._on_sell)
        self._btn_close.clicked.connect(self._on_close_position)
        self._btn_close_half.clicked.connect(lambda: self._on_partial_close(1 / 2))
        self._btn_close_third.clicked.connect(lambda: self._on_partial_close(1 / 3))
        self._btn_close_quarter.clicked.connect(lambda: self._on_partial_close(1 / 4))
        self._btn_limit_buy.clicked.connect(self._on_limit_buy)
        self._btn_limit_sell.clicked.connect(self._on_limit_sell)
        self._btn_cancel_limit.clicked.connect(self._on_cancel_limit)
        self._btn_calc_qty.clicked.connect(self._on_calc_qty)
        self._btn_write_review.clicked.connect(self._on_write_review)

        self._btn_up.clicked.connect(lambda: self._on_predict(PredictionDirection.UP))
        self._btn_down.clicked.connect(lambda: self._on_predict(PredictionDirection.DOWN))
        self._btn_side.clicked.connect(lambda: self._on_predict(PredictionDirection.SIDEWAYS))
        self._mode_group.idToggled.connect(self._on_mode_toggle)

        self._spn_sl.valueChanged.connect(self._on_sl_spinbox_changed)
        self._spn_tp.valueChanged.connect(self._on_tp_spinbox_changed)

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
        try:
            cnt = self._engine.ensure_data(symbol, tf, start_date="20100101", end_date=_dt.now().strftime("%Y%m%d"), force=True)
            self._lbl_status.setText(f"下载完成 {code} {tf.label}: {cnt} 根K线")
        except Exception as exc:
            self._lbl_status.setText(f"下载失败 {code} {tf.label}: {exc}")
            QMessageBox.warning(self, "下载失败", f"{tf.label} 下载失败:\n{exc}\n\n请稍后重试。")
        finally:
            self._btn_download.setEnabled(True)
            self._refresh_data_table()

    def _on_start_session(self):
        code = self._inp_symbol.text().strip()
        if not code:
            return

        symbol = Symbol(code=code, name=code, market_type=MarketType.A_SHARE)
        tf = self._selected_tf()
        mode = TrainingMode.TRADE if self._rb_trade.isChecked() else TrainingMode.PREDICT

        if not self._cache.has_data(symbol, tf):
            QMessageBox.information(self, "提示", f"请先下载 {tf.label} 数据")
            return

        dialog = SessionPlanDialog(mode, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        plan = dialog.get_plan()

        try:
            visible, future = self._engine.random_slice(
                symbol, tf,
                self._spn_visible.value(), self._spn_future.value(),
                scenario_tag=plan["scenario_tag"],
            )
        except ValueError as exc:
            QMessageBox.warning(self, "数据不足", str(exc))
            return

        self._session = ReplaySession()
        self._session.setup(symbol, tf, mode, visible, future)
        self._session.setup_type = plan["setup_type"]
        self._session.scenario_tag = plan["scenario_tag"]
        self._session.plan_notes = plan["plan_notes"]
        self._session.plan_direction = plan["plan_direction"]
        self._session.plan_invalidation = plan["plan_invalidation"]
        self._session.difficulty = plan["difficulty"]
        self._session.start()

        self._planned_risk_pct = plan["risk_pct"]
        self._spn_risk_pct.setValue(plan["risk_pct"])
        if mode == TrainingMode.TRADE:
            persisted_capital = self._stats_service.get_last_equity()
            persisted_bankruptcy = self._stats_service.get_bankruptcy_count()
            self._trade_mode = TradeMode(
                self._session,
                initial_capital=persisted_capital,
                bankruptcy_count=persisted_bankruptcy,
                rules=self._rules,
            )
        else:
            self._trade_mode = None
        self._predict_mode = PredictMode(self._session) if mode == TrainingMode.PREDICT else None
        self._pending_review_trades.clear()
        self._btn_write_review.setEnabled(False)

        self._btn_sell.setEnabled(self._rules.allows_short())
        self._btn_limit_sell.setEnabled(self._rules.allows_short())
        self._pending_limit = None
        self._chart.set_timeframe(tf)
        self._chart.set_candles(visible)
        self._chart.set_ma_data(visible)
        self._chart.clear_drawings()
        self._chart.remove_all_trade_lines()
        self._refresh_markers()
        self._update_bar_label()
        self._update_live_stats()
        self._update_position_display()
        self._update_plan_label()

        self._lbl_status.setText(
            f"训练开始: {code} {tf.label} | "
            f"{visible[0].timestamp.strftime('%Y-%m-%d')} ~ {future[-1].timestamp.strftime('%Y-%m-%d')} | "
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

    def _on_speed_change(self, value):
        if self._play_timer.isActive():
            self._play_timer.setInterval(value)

    def _on_mode_toggle(self, btn_id, checked):
        if not checked:
            return
        self._trade_box.setVisible(btn_id == 0)
        self._predict_box.setVisible(btn_id != 0)

    # ------------------------------------------------------------------
    # Trade
    # ------------------------------------------------------------------

    def _on_calc_qty(self):
        if not self._trade_mode:
            return
        candle = self._session.current_candle
        stop_loss = self._spn_sl.value()
        if not candle or not stop_loss:
            QMessageBox.information(self, "提示", "请先设置止损，并在训练开始后计算仓位。")
            return
        qty = self._trade_mode.calculate_risk_position(self._spn_risk_pct.value(), candle.close, stop_loss)
        self._spn_qty.setValue(qty)

    def _resolve_quantity(self, entry_price: Optional[float] = None) -> Optional[int]:
        if not self._trade_mode:
            return None
        if not self._chk_auto_qty.isChecked():
            return self._spn_qty.value()
        stop_loss = self._spn_sl.value()
        if not stop_loss:
            QMessageBox.information(self, "提示", "启用自动仓位时，请先设置止损。")
            return None
        price = entry_price
        if price is None:
            candle = self._session.current_candle
            if not candle:
                return None
            price = candle.close
        qty = self._trade_mode.calculate_risk_position(self._spn_risk_pct.value(), price, stop_loss)
        self._spn_qty.setValue(qty)
        return qty

    def _on_buy(self):
        if not self._trade_mode or self._session.state != SessionState.RUNNING:
            return
        qty = self._resolve_quantity()
        if not qty:
            return
        pos = self._trade_mode.open_long(qty, self._spn_sl.value() or None, self._spn_tp.value() or None)
        if pos:
            self._show_trade_lines()
            self._update_position_display()

    def _on_sell(self):
        if not self._trade_mode or self._session.state != SessionState.RUNNING:
            return
        qty = self._resolve_quantity()
        if not qty:
            return
        pos = self._trade_mode.open_short(qty, self._spn_sl.value() or None, self._spn_tp.value() or None)
        if pos:
            self._show_trade_lines()
            self._update_position_display()

    def _on_close_position(self):
        if not self._trade_mode:
            return
        closed = self._trade_mode.close("manual")
        if closed:
            self._on_trade_closed(closed)

    def _on_partial_close(self, ratio: float):
        if not self._trade_mode:
            return
        pos = self._session.position
        if pos is None:
            QMessageBox.information(self, "提示", "当前无持仓")
            return
        closed = self._trade_mode.close_partial(ratio, "partial")
        if closed:
            self._on_trade_closed(closed, is_partial=True)

    def _on_trade_closed(self, trade, is_partial: bool = False):
        trade.planned_risk_pct = self._spn_risk_pct.value()
        if not is_partial or self._session.position is None:
            self._chart.remove_all_trade_lines()
        self._chart.remove_all_limits()
        self._pending_limit = None
        self._refresh_markers()
        self._update_position_display()
        self._update_live_stats()

        if self._trade_mode and self._trade_mode.capital < 2000:
            self._lbl_status.setText(
                f"破产! 资金低于2000，已重置为100,000 (第{self._trade_mode.bankruptcy_count}次)"
            )

        self._pending_review_trades.append(trade)
        self._btn_write_review.setEnabled(True)
        self._lbl_status.setText("已标记买卖点，请观察后点击【写本笔复盘】")

    # ------------------------------------------------------------------
    # Deferred review
    # ------------------------------------------------------------------

    def _on_write_review(self):
        if not self._pending_review_trades:
            return
        trade = self._pending_review_trades.pop(0)

        self._take_trade_snapshot(trade, self._open_review_for_trade)

    def _take_trade_snapshot(self, trade, callback):
        tf = self._session.timeframe or Timeframe.DAILY
        entry_markers = self._chart.build_trade_markers([trade], tf)

        buf_before = 10
        buf_after = 5
        start_idx = max(0, trade.entry_bar_index - buf_before)
        end_idx = trade.exit_bar_index + buf_after
        self._chart.prepare_snapshot(start_idx, end_idx, entry_markers)

        if tf.minutes >= Timeframe.DAILY.minutes:
            et = trade.entry_time.strftime("%Y-%m-%d")
            xt = trade.exit_time.strftime("%Y-%m-%d")
        else:
            et = int(trade.entry_time.timestamp())
            xt = int(trade.exit_time.timestamp())
        is_long = trade.direction.value == "long"
        self._chart.add_snapshot_overlay(
            et, trade.entry_price, xt, trade.exit_price,
            sl=trade.stop_loss or 0, tp=trade.take_profit or 0,
            is_long=is_long,
        )

        self._snapshot_trade = trade
        self._snapshot_callback = callback
        QTimer.singleShot(500, self._do_capture_snapshot)

    def _do_capture_snapshot(self):
        self._chart.capture_image(self._on_snapshot_captured)

    def _on_snapshot_captured(self, data_url: str):
        trade = self._snapshot_trade
        callback = self._snapshot_callback
        snap_path = ""

        if data_url and data_url.startswith("data:image/png;base64,"):
            raw = data_url.split(",", 1)[1]
            img_bytes = base64.b64decode(raw)
            fname = f"{self._session.session_id}_{trade.entry_bar_index}_{trade.exit_bar_index}.png"
            snap_path = str(_SNAPSHOT_DIR / fname)
            try:
                with open(snap_path, "wb") as f:
                    f.write(img_bytes)
                trade.snapshot_path = snap_path
            except Exception:
                snap_path = ""

        self._restore_full_chart()
        callback(trade, snap_path)

    def _restore_full_chart(self):
        self._chart.clear_snapshot_overlays()
        tf = self._session.timeframe or Timeframe.DAILY
        all_markers = self._chart.build_trade_markers(self._session.closed_trades, tf)
        self._chart.set_markers(all_markers)
        self._chart._run_js("fitContent()")

    def _open_review_for_trade(self, trade, snapshot_path: str):
        review = TradeReviewDialog(trade, snapshot_path, self)
        if review.exec() == QDialog.DialogCode.Accepted:
            result = review.get_review()
            trade.mistake_tags = result["mistake_tags"]
            trade.execution_score = result["execution_score"]
            trade.entry_reason = result["entry_reason"]
            trade.exit_review = result["exit_review"]

        if not self._pending_review_trades:
            self._btn_write_review.setEnabled(False)

    # ------------------------------------------------------------------
    # Dynamic SL/TP lines
    # ------------------------------------------------------------------

    def _show_trade_lines(self):
        pos = self._session.position
        if pos is None:
            return
        self._chart.remove_all_trade_lines()
        self._chart.add_trade_line("entry_line", pos.entry_price, "entry", "#FFD700")
        if pos.stop_loss:
            self._chart.add_trade_line("sl_line", pos.stop_loss, "stop_loss", "#ef5350")
        if pos.take_profit:
            self._chart.add_trade_line("tp_line", pos.take_profit, "take_profit", "#26a69a")

    def _on_trade_line_dragged(self, line_id: str, new_price: float):
        pos = self._session.position
        if pos is None:
            return
        if line_id == "sl_line":
            pos.update_stop_loss(new_price)
            self._spn_sl.blockSignals(True)
            self._spn_sl.setValue(new_price)
            self._spn_sl.blockSignals(False)
        elif line_id == "tp_line":
            pos.update_take_profit(new_price)
            self._spn_tp.blockSignals(True)
            self._spn_tp.setValue(new_price)
            self._spn_tp.blockSignals(False)
        self._update_position_display()

    def _on_sl_spinbox_changed(self, value: float):
        pos = self._session.position
        if pos is None:
            return
        pos.update_stop_loss(value if value > 0 else None)
        if value > 0:
            self._chart.add_trade_line("sl_line", value, "stop_loss", "#ef5350")
        else:
            self._chart.remove_trade_line("sl_line")
        self._update_position_display()

    def _on_tp_spinbox_changed(self, value: float):
        pos = self._session.position
        if pos is None:
            return
        pos.update_take_profit(value if value > 0 else None)
        if value > 0:
            self._chart.add_trade_line("tp_line", value, "take_profit", "#26a69a")
        else:
            self._chart.remove_trade_line("tp_line")
        self._update_position_display()

    # ------------------------------------------------------------------
    # Limit orders
    # ------------------------------------------------------------------

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
        self._pending_limit = {"id": "limit_buy", "price": price, "direction": "long"}
        self._chart.add_limit_order("limit_buy", price, "long", "#ef5350")
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
        self._pending_limit = {"id": "limit_sell", "price": price, "direction": "short"}
        self._chart.add_limit_order("limit_sell", price, "short", "#26a69a")
        self._lbl_trade_info.setText(f"限价卖出委托: {price:.2f}\n(拖动线调整价格)")

    def _on_cancel_limit(self):
        if self._pending_limit:
            self._chart.remove_limit_order(self._pending_limit["id"])
            self._pending_limit = None
            self._lbl_trade_info.setText("委托已撤销")

    def _on_limit_price_dragged(self, order_id: str, new_price: float):
        if self._pending_limit and self._pending_limit["id"] == order_id:
            self._pending_limit["price"] = new_price
            action = "买入" if self._pending_limit["direction"] == "long" else "卖出"
            self._lbl_trade_info.setText(f"限价{action}委托: {new_price:.2f}\n(拖动线调整价格)")

    def _check_limit_order(self, candle):
        if not self._pending_limit or not self._trade_mode or self._session.position:
            return
        price = self._pending_limit["price"]
        if not (candle.low <= price <= candle.high):
            return
        qty = self._resolve_quantity(price)
        if not qty:
            return

        if self._pending_limit["direction"] == "long":
            pos = self._trade_mode.open_long(qty, self._spn_sl.value() or None, self._spn_tp.value() or None)
        else:
            pos = self._trade_mode.open_short(qty, self._spn_sl.value() or None, self._spn_tp.value() or None)

        if not pos:
            return

        pos.entry_price = price
        self._chart.remove_limit_order(self._pending_limit["id"])
        self._pending_limit = None
        self._show_trade_lines()
        self._update_position_display()
        self._lbl_status.setText(f"限价委托成交 @ {price:.2f}")

    # ------------------------------------------------------------------
    # Predict / PA
    # ------------------------------------------------------------------

    def _on_predict(self, direction: PredictionDirection):
        if not self._predict_mode or self._session.state != SessionState.RUNNING:
            return
        self._predict_mode.predict(direction, self._spn_look.value())
        self._update_live_stats()

    # ------------------------------------------------------------------
    # Finish and persistence
    # ------------------------------------------------------------------

    def _on_finish_session(self):
        if self._session.state == SessionState.IDLE:
            return

        self._on_pause()

        if self._pending_review_trades:
            ans = QMessageBox.question(
                self, "待复盘交易",
                f"还有 {len(self._pending_review_trades)} 笔交易未写复盘。\n是否先写复盘再结束?",
            )
            if ans == QMessageBox.StandardButton.Yes:
                self._on_write_review()
                return

        if self._session.position and self._trade_mode:
            closed = self._trade_mode.close("session_end")
            if closed:
                closed.planned_risk_pct = self._spn_risk_pct.value()
                self._session.closed_trades.append(closed) if closed not in self._session.closed_trades else None
        self._session.finish()
        if self._predict_mode:
            self._session.evaluate_predictions()

        self._chart.get_drawings(self._finish_with_drawings)

    def _finish_with_drawings(self, drawings: list):
        self._session.score = self._compute_session_score(drawings)
        self._stats_service.save_session(self._session)

        if self._trade_mode:
            self._stats_service.save_equity_snapshots(
                self._session.session_id,
                self._trade_mode.equity_snapshots,
            )

        self._persist_drawings(drawings)
        self._persist_mistake_book(drawings)
        self._chart.remove_all_trade_lines()
        self._review_panel.refresh()
        self._update_live_stats()
        self._pending_review_trades.clear()
        self._btn_write_review.setEnabled(False)
        self._lbl_status.setText("训练已保存")
        QMessageBox.information(self, "训练结束", "训练记录已保存。\n切换到 [复盘] 查看统计、错误分类和 PA 标注。")

    def _compute_session_score(self, drawings: list) -> int:
        if self._trade_mode:
            scores = [t.execution_score for t in self._session.closed_trades if t.execution_score > 0]
            if scores:
                return round(sum(scores) / len(scores))
            stats = self._trade_mode.compute_stats()
            return round(stats.win_rate * 100)

        if self._predict_mode:
            result = self._predict_mode.get_result()
            score = round(result.accuracy * 100)
            if drawings:
                score = min(100, score + 10)
            return score
        return 0

    def _persist_drawings(self, drawings: list):
        if not drawings:
            return
        payload = {
            "drawings": drawings,
            "setup_type": self._session.setup_type,
            "scenario_tag": self._session.scenario_tag,
            "plan_direction": self._session.plan_direction,
        }
        self._stats_service.save_pa_annotation(
            self._session.session_id,
            "drawings",
            json.dumps(payload, ensure_ascii=False),
            notes=self._session.plan_notes,
        )

    def _persist_mistake_book(self, drawings: list):
        symbol = self._session.symbol.code if self._session.symbol else ""
        timeframe = self._session.timeframe.label if self._session.timeframe else ""
        for trade in self._session.closed_trades:
            if trade.mistake_tags:
                self._stats_service.save_mistake(
                    session_id=self._session.session_id,
                    symbol=symbol, timeframe=timeframe,
                    category="trade",
                    setup_type=self._session.setup_type,
                    mistake_tags=trade.mistake_tags,
                    description=(trade.exit_review or trade.entry_reason or "执行复盘"),
                    slice_start=trade.entry_bar_index,
                    slice_end=trade.exit_bar_index,
                )

        if self._predict_mode:
            result = self._predict_mode.get_result()
            pa_tags = []
            desc = []
            if result.total > 0 and result.accuracy < 0.5:
                pa_tags.append("预测准确率偏低")
                desc.append(f"预测准确率 {result.accuracy:.1%}")
            if not drawings:
                pa_tags.append("未做结构标注")
                desc.append("本次 PA 训练未保存图表标注")
            if pa_tags:
                self._stats_service.save_mistake(
                    session_id=self._session.session_id,
                    symbol=symbol, timeframe=timeframe,
                    category="pa",
                    setup_type=self._session.setup_type,
                    mistake_tags=pa_tags,
                    description="；".join(desc),
                    slice_start=len(self._session.visible_candles),
                    slice_end=len(self._session.displayed_candles),
                )

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def _refresh_data_table(self):
        summary = self._cache.get_cache_summary()
        self._data_table.setRowCount(len(summary))
        for row, item in enumerate(summary):
            self._data_table.setItem(row, 0, QTableWidgetItem(item["symbol"]))
            self._data_table.setItem(row, 1, QTableWidgetItem(item["tf"]))
            self._data_table.setItem(row, 2, QTableWidgetItem(str(item["bars"])))
            self._data_table.setItem(row, 3, QTableWidgetItem(item["start"]))
            self._data_table.setItem(row, 4, QTableWidgetItem(item["end"]))

    def _on_delete_selected_data(self):
        rows = sorted({idx.row() for idx in self._data_table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        if QMessageBox.question(self, "确认", "删除选中数据?") != QMessageBox.StandardButton.Yes:
            return
        for row in rows:
            symbol = self._data_table.item(row, 0).text()
            tf = self._data_table.item(row, 1).text()
            self._cache.delete_symbol_data(symbol, tf)
        self._refresh_data_table()

    def _on_delete_all_data(self):
        if QMessageBox.question(self, "确认", "清空所有已下载数据?") != QMessageBox.StandardButton.Yes:
            return
        for symbol in self._cache.list_cached_symbols():
            self._cache.delete_symbol_data(symbol)
        self._refresh_data_table()

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def _update_plan_label(self):
        direction_map = {"long": "看多", "short": "看空", "sideways": "震荡", "": "未设定"}
        self._lbl_plan.setText(
            f"Setup: {self._session.setup_type or '-'}\n"
            f"场景: {self._session.scenario_tag or '随机'}\n"
            f"方向: {direction_map.get(self._session.plan_direction, '未设定')}\n"
            f"失效: {self._session.plan_invalidation or '-'}\n"
            f"备注: {self._session.plan_notes or '-'}"
        )

    def _update_bar_label(self):
        label = self._session.timeframe.label if self._session.timeframe else ""
        self._lbl_bar_info.setText(f"K线: {self._session.current_index}/{self._session.total_future_bars}  [{label}]")

    def _update_position_display(self):
        pos = self._session.position
        if pos is None:
            cap = f"资金: {self._trade_mode.capital:,.0f}" if self._trade_mode else ""
            self._lbl_trade_info.setText(f"无持仓\n{cap}")
            self._lbl_position.setText(cap)
            self._chart.remove_all_trade_lines()
            return
        candle = self._session.current_candle
        current_price = candle.close if candle else pos.entry_price
        pnl = pos.unrealized_pnl(current_price)
        pct = pos.unrealized_pnl_pct(current_price)
        r_val = pos.unrealized_r(current_price)
        r_text = f"  {r_val:.2f}R" if r_val is not None else ""
        rr = pos.reward_risk_ratio(current_price)
        rr_text = f"  盈亏比: {rr}" if rr is not None else ""
        direction = "多" if pos.is_long else "空"
        cap_text = f"资金: {self._trade_mode.capital:,.0f}" if self._trade_mode else ""
        self._lbl_trade_info.setText(
            f"方向: {direction}  入场: {pos.entry_price:.2f}  数量: {pos.quantity}\n"
            f"浮盈: {pnl:+.2f} ({pct:+.2f}%){r_text}{rr_text}\n"
            f"SL: {pos.stop_loss or '无'}  TP: {pos.take_profit or '无'}  风险: {self._spn_risk_pct.value():.2f}%\n"
            f"{cap_text}"
        )
        self._lbl_position.setText(f"持仓 {direction} {pnl:+.2f}")

    def _update_live_stats(self):
        if self._trade_mode:
            self._lbl_live_stats.setText(self._trade_mode.summary_text())
            score = self._session.score or round(self._trade_mode.compute_stats().win_rate * 100)
            self._lbl_score.setText(f"会话分数 {score}")
        elif self._predict_mode:
            result = self._predict_mode.get_result()
            script = f"\n剧本: {self._session.plan_notes}" if self._session.plan_notes else ""
            self._lbl_live_stats.setText(self._predict_mode.summary_text() + script)
            score = self._session.score or round(result.accuracy * 100)
            self._lbl_score.setText(f"PA分数 {score}")
        else:
            self._lbl_live_stats.setText("--")
            self._lbl_score.setText("")

    def _refresh_markers(self):
        if not self._session.closed_trades:
            self._chart.set_markers([])
            return
        tf = self._session.timeframe or Timeframe.DAILY
        markers = self._chart.build_trade_markers(self._session.closed_trades, tf)
        self._chart.set_markers(markers)

    # ------------------------------------------------------------------
    # Snapshot in chart area
    # ------------------------------------------------------------------

    def _show_snapshot_in_chart(self, snapshot_path: str):
        if not snapshot_path or not Path(snapshot_path).exists():
            return
        pix = QPixmap(snapshot_path)
        if pix.isNull():
            return
        vw = self._snapshot_scroll.viewport().width()
        vh = self._snapshot_scroll.viewport().height()
        scaled = pix.scaled(
            max(vw, 600), max(vh, 400),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._snapshot_image_label.setPixmap(scaled)
        self._lbl_snap_title.setText("复盘截图 (点击[返回图表]恢复)")
        self._left_stack.setCurrentIndex(1)

    def _hide_snapshot_view(self):
        self._left_stack.setCurrentIndex(0)

    # ==================================================================

    def closeEvent(self, event):
        self._play_timer.stop()
        self._cache.close()
        self._stats_service.close()
        super().closeEvent(event)
