from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.storage.stats_service import AggregateStats, StatsService

PA_TAGS = [
    "Pin Bar", "Engulfing", "Inside Bar", "Outside Bar",
    "Double Top", "Double Bottom", "Head & Shoulders",
    "Break of Structure", "Order Block", "Fair Value Gap",
    "Trend Follow", "Mean Reversion", "Breakout", "False Break",
]


class ReviewPanel(QWidget):
    session_selected = Signal(str)

    def __init__(self, stats_service: StatsService, parent=None):
        super().__init__(parent)
        self._stats = stats_service
        self._trade_id_map: dict[int, int] = {}
        self._session_id_map: dict[int, str] = {}
        self._mistake_id_map: dict[int, int] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_overview_tab(), "总览")
        self._tabs.addTab(self._build_journal_tab(), "交易日志")
        self._tabs.addTab(self._build_sessions_tab(), "训练记录")
        self._tabs.addTab(self._build_mistakes_tab(), "错题本")
        layout.addWidget(self._tabs)

    # ------------------------------------------------------------------
    # Overview
    # ------------------------------------------------------------------

    def _build_overview_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(8)
        layout.setContentsMargins(6, 6, 6, 6)

        filters = QGridLayout()
        filters.addWidget(QLabel("品种:"), 0, 0)
        self._filter_symbol = QComboBox()
        filters.addWidget(self._filter_symbol, 0, 1)
        filters.addWidget(QLabel("周期:"), 0, 2)
        self._filter_tf = QComboBox()
        filters.addWidget(self._filter_tf, 0, 3)
        filters.addWidget(QLabel("Setup:"), 1, 0)
        self._filter_setup = QComboBox()
        filters.addWidget(self._filter_setup, 1, 1)
        filters.addWidget(QLabel("场景:"), 1, 2)
        self._filter_scenario = QComboBox()
        filters.addWidget(self._filter_scenario, 1, 3)

        btn_row = QHBoxLayout()
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self.refresh)
        btn_row.addWidget(btn_refresh)
        btn_row.addStretch()

        self._stats_group = QGroupBox("核心指标")
        stats_lay = QVBoxLayout(self._stats_group)
        self._lbl_summary = QTextBrowser()
        self._lbl_summary.setOpenExternalLinks(False)
        self._lbl_summary.setMinimumHeight(160)
        stats_lay.addWidget(self._lbl_summary)

        self._drill_group = QGroupBox("统计钻取")
        drill_lay = QVBoxLayout(self._drill_group)
        self._lbl_drilldown = QTextBrowser()
        self._lbl_drilldown.setMinimumHeight(160)
        drill_lay.addWidget(self._lbl_drilldown)

        self._extra_group = QGroupBox("训练建议")
        extra_lay = QVBoxLayout(self._extra_group)
        self._lbl_extra = QTextBrowser()
        self._lbl_extra.setMinimumHeight(100)
        extra_lay.addWidget(self._lbl_extra)

        layout.addLayout(filters)
        layout.addLayout(btn_row)
        layout.addWidget(self._stats_group)
        layout.addWidget(self._drill_group)
        layout.addWidget(self._extra_group)
        layout.addStretch()
        return widget

    # ------------------------------------------------------------------
    # Journal (with snapshot image)
    # ------------------------------------------------------------------

    def _build_journal_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        splitter = QSplitter(Qt.Orientation.Vertical)

        self._trade_table = QTableWidget()
        self._trade_table.setColumnCount(14)
        self._trade_table.setHorizontalHeaderLabels([
            "时间", "品种", "周期", "方向", "Setup",
            "盈亏", "盈亏%", "R倍数", "出场",
            "执行分", "错误分类", "标签", "入场理由", "出场复盘",
        ])
        self._trade_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._trade_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._trade_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._trade_table.currentCellChanged.connect(self._on_trade_selected)
        splitter.addWidget(self._trade_table)

        detail_widget = QWidget()
        detail_layout = QVBoxLayout(detail_widget)
        detail_layout.setContentsMargins(4, 4, 4, 4)

        self._trade_snapshot_label = QLabel("选中交易后在此显示截图")
        self._trade_snapshot_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._trade_snapshot_label.setMinimumHeight(200)
        self._trade_snapshot_label.setStyleSheet("background:#1e222d;color:#808899;border:1px solid #3d3d5c;")
        detail_layout.addWidget(self._trade_snapshot_label)

        tag_group = QGroupBox("标签与执行复盘")
        tg = QVBoxLayout(tag_group)

        quick = QGridLayout()
        self._tag_buttons: List[QPushButton] = []
        for idx, tag in enumerate(PA_TAGS[:8]):
            btn = QPushButton(tag)
            btn.setCheckable(True)
            btn.setMinimumHeight(26)
            btn.clicked.connect(lambda checked, t=tag: self._on_tag_toggle(t, checked))
            quick.addWidget(btn, idx // 4, idx % 4)
            self._tag_buttons.append(btn)
        tg.addLayout(quick)

        score_row = QHBoxLayout()
        score_row.addWidget(QLabel("执行评分:"))
        self._spn_exec_score = QSpinBox()
        self._spn_exec_score.setRange(0, 100)
        self._spn_exec_score.setSingleStep(5)
        score_row.addWidget(self._spn_exec_score)
        score_row.addWidget(QLabel("错误分类(逗号分隔):"))
        self._inp_mistakes = QLineEdit()
        score_row.addWidget(self._inp_mistakes)
        tg.addLayout(score_row)

        self._txt_entry_reason = QTextEdit()
        self._txt_entry_reason.setPlaceholderText("入场理由 / 是否计划内")
        tg.addWidget(QLabel("入场理由"))
        tg.addWidget(self._txt_entry_reason)

        self._txt_exit_review = QTextEdit()
        self._txt_exit_review.setPlaceholderText("出场复盘 / 错误归因")
        tg.addWidget(QLabel("出场复盘"))
        tg.addWidget(self._txt_exit_review)

        self._txt_notes = QTextEdit()
        self._txt_notes.setPlaceholderText("标签笔记 / 补充说明")
        tg.addWidget(QLabel("笔记"))
        tg.addWidget(self._txt_notes)

        br = QHBoxLayout()
        btn_save = QPushButton("保存选中交易")
        btn_save.clicked.connect(self._on_save_note)
        br.addWidget(btn_save)
        br.addStretch()
        tg.addLayout(br)
        detail_layout.addWidget(tag_group)

        detail_scroll = QScrollArea()
        detail_scroll.setWidget(detail_widget)
        detail_scroll.setWidgetResizable(True)
        splitter.addWidget(detail_scroll)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        layout.addWidget(splitter)
        return widget

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def _build_sessions_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self._session_table = QTableWidget()
        self._session_table.setColumnCount(10)
        self._session_table.setHorizontalHeaderLabels([
            "会话ID", "品种", "周期", "模式", "Setup",
            "场景", "开始时间", "交易数", "净盈亏", "分数",
        ])
        self._session_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._session_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._session_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._session_table.cellDoubleClicked.connect(self._on_session_double_click)
        layout.addWidget(self._session_table)

        self._session_detail = QGroupBox("会话详情")
        sd = QVBoxLayout(self._session_detail)
        self._txt_session_detail = QTextBrowser()
        self._txt_session_detail.setMinimumHeight(200)
        self._txt_session_detail.setPlaceholderText("双击上方记录查看训练计划、统计、PA 标注和建议。")
        sd.addWidget(self._txt_session_detail)
        self._txt_session_note = QTextEdit()
        self._txt_session_note.setPlaceholderText("训练总结 / 下次改进建议")
        sd.addWidget(self._txt_session_note)
        btn_save = QPushButton("保存训练笔记")
        btn_save.clicked.connect(self._on_save_session_note)
        sd.addWidget(btn_save)
        layout.addWidget(self._session_detail)
        return widget

    # ------------------------------------------------------------------
    # Mistakes
    # ------------------------------------------------------------------

    def _build_mistakes_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        top = QHBoxLayout()
        top.addWidget(QLabel("类别:"))
        self._mistake_category = QComboBox()
        self._mistake_category.addItem("全部", "")
        self._mistake_category.addItem("交易", "trade")
        self._mistake_category.addItem("PA", "pa")
        top.addWidget(self._mistake_category)
        btn_r = QPushButton("刷新错题")
        btn_r.clicked.connect(self._update_mistakes)
        top.addWidget(btn_r)
        top.addStretch()
        layout.addLayout(top)

        self._mistake_table = QTableWidget()
        self._mistake_table.setColumnCount(7)
        self._mistake_table.setHorizontalHeaderLabels([
            "时间", "品种", "周期", "类别", "Setup", "错误分类", "描述",
        ])
        self._mistake_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._mistake_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._mistake_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self._mistake_table)

        btn_done = QPushButton("标记为已复训")
        btn_done.clicked.connect(self._on_mark_retrained)
        layout.addWidget(btn_done)
        return widget

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def refresh(self):
        self._refresh_filters()
        sym = self._filter_symbol.currentData() or None
        tf = self._filter_tf.currentData() or None
        agg = self._stats.get_overall_stats(symbol=sym, timeframe=tf)
        self._update_summary(agg)
        self._update_drilldown(sym, tf)
        self._update_journal(sym, tf)
        self._update_sessions(sym, tf)
        self._update_suggestions(sym, tf)
        self._update_mistakes()

    def _refresh_filters(self):
        self._refill(self._filter_symbol, "全部", self._stats.get_distinct_session_values("symbol"))
        self._refill(self._filter_tf, "全部", self._stats.get_distinct_session_values("timeframe"))
        self._refill(self._filter_setup, "全部", self._stats.get_distinct_session_values("setup_type"))
        self._refill(self._filter_scenario, "全部", self._stats.get_distinct_session_values("scenario_tag"))

    @staticmethod
    def _refill(combo: QComboBox, first: str, values: List[str]):
        cur = combo.currentData() if combo.count() else ""
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(first, "")
        for v in values:
            combo.addItem(v, v)
        idx = combo.findData(cur)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    # ------------------------------------------------------------------
    # Overview
    # ------------------------------------------------------------------

    def _update_summary(self, agg: AggregateStats):
        r_str = f"{agg.avg_r:.2f}R" if agg.avg_r is not None else "N/A"
        pf = "N/A" if agg.profit_factor == float("inf") else f"{agg.profit_factor:.2f}"
        bankruptcy = self._stats.get_bankruptcy_count()
        lines = [
            f"训练场次: {agg.total_sessions}    总交易: {agg.total_trades}",
            f"胜率: {agg.win_rate:.1%}    ({agg.winners}胜 / {agg.losers}负)",
            f"平均R: {r_str}    PF: {pf}",
            f"期望值: {agg.expectancy:.2f}%    净盈亏: {agg.total_pnl:+.2f}",
            f"手续费: {agg.total_commission:.2f}    最大回撤: {agg.max_drawdown_pct:.2f}%",
            f"破产次数: {bankruptcy}",
            f"预测: {agg.total_predictions}次  正确: {agg.correct_predictions}  准确率: {agg.prediction_accuracy:.1%}",
        ]
        self._lbl_summary.setPlainText("\n".join(lines))

    def _update_drilldown(self, sym: Optional[str], tf: Optional[str]):
        setup = self._stats.get_stats_by_setup(symbol=sym, timeframe=tf)
        mistake = self._stats.get_stats_by_mistake(symbol=sym, timeframe=tf)
        scenario = self._stats.get_stats_by_scenario(symbol=sym, timeframe=tf)
        exit_r = self._stats.get_stats_by_exit_reason(symbol=sym, timeframe=tf)
        pa = self._stats.get_pa_stats(symbol=sym, timeframe=tf)

        lines: List[str] = []
        if setup:
            s = setup[0]
            wr = s["wins"] / max(s["cnt"], 1)
            lines.append(f"最佳 Setup: {s['setup_type']}  样本{s['cnt']}  胜率{wr:.1%}")
        if mistake:
            lines.append(f"最常见错误: {mistake[0]['tag']}  出现{mistake[0]['count']}次")
        if scenario:
            s = scenario[0]
            lines.append(f"高频场景: {s['scenario_tag']}  样本{s['cnt']}  净盈亏{s['total_pnl'] or 0:+.2f}")
        if exit_r:
            worst = min(exit_r, key=lambda x: x["avg_pnl_pct"] or 0)
            lines.append(f"最伤收益出场: {worst['exit_reason']}  平均盈亏%{worst['avg_pnl_pct'] or 0:+.2f}%")
        avg_exec = self._stats.get_execution_score_avg(symbol=sym, timeframe=tf)
        if avg_exec is not None:
            lines.append(f"平均执行评分: {avg_exec:.1f}")
        lines.append(f"PA标注准确率: {pa['annotation_accuracy']:.1%}  错题复训率: {pa['retrain_rate']:.1%}")
        self._lbl_drilldown.setPlainText("\n".join(lines) if lines else "暂无钻取统计")

    def _update_suggestions(self, sym: Optional[str], tf: Optional[str]):
        rolling = self._stats.get_rolling_win_rate(20)
        curve = self._stats.get_equity_curve(symbol=sym, timeframe=tf)
        mistakes = self._stats.get_stats_by_mistake(symbol=sym, timeframe=tf)
        parts: List[str] = []
        if rolling:
            last = rolling[-1]
            parts.append(f"近{last['sample_size']}笔滚动胜率: {last['win_rate']:.1%}")
        if curve:
            parts.append(f"累计净盈亏: {curve[-1]['cumulative_pnl']:+.2f}")
        if mistakes:
            parts.append(f"下次训练建议: 优先针对「{mistakes[0]['tag']}」做单项刻意练习。")
        else:
            parts.append("下次训练建议: 继续保持，并开始为每笔交易补全执行评分。")
        self._lbl_extra.setPlainText("\n".join(parts))

    # ------------------------------------------------------------------
    # Journal
    # ------------------------------------------------------------------

    def _update_journal(self, sym: Optional[str], tf: Optional[str]):
        setup_f = self._filter_setup.currentData() or ""
        scenario_f = self._filter_scenario.currentData() or ""

        self._trade_table.setRowCount(0)
        self._trade_id_map.clear()
        for sess in self._stats.get_all_sessions():
            if sym and sess["symbol"] != sym:
                continue
            if tf and sess["timeframe"] != tf:
                continue
            if setup_f and sess.get("setup_type") != setup_f:
                continue
            if scenario_f and sess.get("scenario_tag") != scenario_f:
                continue
            for t in self._stats.get_session_trades(sess["id"]):
                row = self._trade_table.rowCount()
                self._trade_table.insertRow(row)
                self._trade_id_map[row] = t["id"]
                tags = ", ".join(json.loads(t.get("tags", "[]") or "[]"))
                mtags = ", ".join(json.loads(t.get("mistake_tags", "[]") or "[]"))
                vals = [
                    (t.get("entry_time") or "")[:19],
                    sess.get("symbol", ""), sess.get("timeframe", ""),
                    "做多" if t.get("direction") == "long" else "做空",
                    sess.get("setup_type", ""),
                    f"{(t.get('pnl') or 0):+.2f}",
                    f"{(t.get('pnl_pct') or 0):+.2f}%",
                    f"{t['r_multiple']:.2f}" if t.get("r_multiple") is not None else "-",
                    t.get("exit_reason", ""),
                    str(t.get("execution_score") or 0),
                    mtags, tags,
                    t.get("entry_reason", ""),
                    t.get("exit_review", ""),
                ]
                for col, v in enumerate(vals):
                    item = QTableWidgetItem(v)
                    if col == 5:
                        pnl = t.get("pnl") or 0
                        item.setForeground(
                            Qt.GlobalColor.red if pnl > 0
                            else Qt.GlobalColor.green if pnl < 0
                            else Qt.GlobalColor.gray
                        )
                    self._trade_table.setItem(row, col, item)

    def _on_trade_selected(self, row, _col, _prev_row, _prev_col):
        if row < 0 or row not in self._trade_id_map:
            return
        tid = self._trade_id_map[row]
        rec = self._stats._conn.execute(
            "SELECT tags, notes, mistake_tags, execution_score, entry_reason, exit_review, snapshot_path "
            "FROM trades WHERE id=?", (tid,),
        ).fetchone()
        if not rec:
            return
        for btn in self._tag_buttons:
            btn.blockSignals(True)
            btn.setChecked(btn.text() in json.loads(rec["tags"] or "[]"))
            btn.blockSignals(False)
        self._txt_notes.setPlainText(rec["notes"] or "")
        self._inp_mistakes.setText(", ".join(json.loads(rec["mistake_tags"] or "[]")))
        self._spn_exec_score.setValue(rec["execution_score"] or 0)
        self._txt_entry_reason.setPlainText(rec["entry_reason"] or "")
        self._txt_exit_review.setPlainText(rec["exit_review"] or "")

        snap = rec["snapshot_path"] or ""
        if snap and Path(snap).exists():
            pix = QPixmap(snap)
            if not pix.isNull():
                self._trade_snapshot_label.setPixmap(
                    pix.scaledToWidth(
                        max(self._trade_snapshot_label.width(), 400),
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                self._trade_snapshot_label.setText("")
            else:
                self._trade_snapshot_label.setText("截图加载失败")
                self._trade_snapshot_label.setPixmap(QPixmap())
        else:
            self._trade_snapshot_label.setText("暂无截图")
            self._trade_snapshot_label.setPixmap(QPixmap())

    def _on_tag_toggle(self, tag: str, checked: bool):
        row = self._trade_table.currentRow()
        if row < 0 or row not in self._trade_id_map:
            return
        tid = self._trade_id_map[row]
        rec = self._stats._conn.execute("SELECT tags FROM trades WHERE id=?", (tid,)).fetchone()
        if not rec:
            return
        existing = json.loads(rec["tags"] or "[]")
        if checked and tag not in existing:
            existing.append(tag)
        elif not checked and tag in existing:
            existing.remove(tag)
        self._stats.update_trade_tags(tid, existing)
        cell = self._trade_table.item(row, 11)
        if cell:
            cell.setText(", ".join(existing))

    def _on_save_note(self):
        row = self._trade_table.currentRow()
        if row < 0 or row not in self._trade_id_map:
            QMessageBox.information(self, "提示", "请先在交易日志中选中一笔交易")
            return
        tid = self._trade_id_map[row]
        tags = [b.text() for b in self._tag_buttons if b.isChecked()]
        mtags = [s.strip() for s in self._inp_mistakes.text().split(",") if s.strip()]
        self._stats.update_trade_tags(tid, tags, self._txt_notes.toPlainText().strip())
        self._stats.update_trade_execution(
            tid,
            mistake_tags=mtags,
            execution_score=self._spn_exec_score.value(),
            entry_reason=self._txt_entry_reason.toPlainText().strip(),
            exit_review=self._txt_exit_review.toPlainText().strip(),
        )
        self.refresh()
        QMessageBox.information(self, "已保存", "交易复盘已保存")

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def _update_sessions(self, sym: Optional[str], tf: Optional[str]):
        setup_f = self._filter_setup.currentData() or ""
        scenario_f = self._filter_scenario.currentData() or ""
        filtered = []
        for s in self._stats.get_all_sessions():
            if sym and s["symbol"] != sym:
                continue
            if tf and s["timeframe"] != tf:
                continue
            if setup_f and s.get("setup_type") != setup_f:
                continue
            if scenario_f and s.get("scenario_tag") != scenario_f:
                continue
            filtered.append(s)

        self._session_table.setRowCount(len(filtered))
        self._session_id_map.clear()
        for i, s in enumerate(filtered):
            self._session_id_map[i] = s["id"]
            trades = self._stats.get_session_trades(s["id"])
            net = sum(t.get("pnl") or 0 for t in trades)
            vals = [
                s["id"][:8], s.get("symbol", ""), s.get("timeframe", ""),
                "交易" if s.get("mode") == "trade" else "PA",
                s.get("setup_type", ""), s.get("scenario_tag", ""),
                (s.get("started_at") or "")[:19],
                str(len(trades)), f"{net:+.2f}", str(s.get("score") or 0),
            ]
            for col, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if col == 8:
                    item.setForeground(
                        Qt.GlobalColor.red if net > 0
                        else Qt.GlobalColor.green if net < 0
                        else Qt.GlobalColor.gray
                    )
                self._session_table.setItem(i, col, item)

    def _on_session_double_click(self, row, _col):
        sid = self._session_id_map.get(row)
        if not sid:
            return
        sess = next((s for s in self._stats.get_all_sessions() if s["id"] == sid), None)
        if not sess:
            return

        trades = self._stats.get_session_trades(sid)
        preds = self._stats.get_session_predictions(sid)
        notes = self._stats.get_session_notes(sid)
        anns = self._stats.get_pa_annotations(sid)
        mistakes = [m for m in self._stats.get_mistakes() if m["session_id"] == sid]
        snapshots = self._stats.get_equity_snapshots(sid)

        w = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
        l_count = sum(1 for t in trades if (t.get("pnl") or 0) < 0)
        net = sum(t.get("pnl") or 0 for t in trades)
        es = [t.get("execution_score") or 0 for t in trades if t.get("execution_score")]
        ae = sum(es) / len(es) if es else None
        bankruptcies = max((s.get("bankruptcy_count", 0) for s in snapshots), default=0) if snapshots else 0

        lines = [
            f"会话: {sid[:8]}  |  品种: {sess.get('symbol','')}  |  周期: {sess.get('timeframe','')}",
            f"模式: {'交易' if sess.get('mode')=='trade' else 'PA'}  |  分数: {sess.get('score') or 0}  |  难度: {sess.get('difficulty') or 0}",
            f"Setup: {sess.get('setup_type') or '-'}  |  场景: {sess.get('scenario_tag') or '随机'}",
            f"计划方向: {sess.get('plan_direction') or '-'}  |  失效条件: {sess.get('plan_invalidation') or '-'}",
            f"计划/剧本: {sess.get('plan_notes') or '-'}",
            "",
            f"交易数: {len(trades)}  |  胜: {w}  |  负: {l_count}  |  净盈亏: {net:+.2f}",
        ]
        if bankruptcies > 0:
            lines.append(f"本轮破产: {bankruptcies}次")
        if ae is not None:
            lines.append(f"平均执行评分: {ae:.1f}")
        if preds:
            c = sum(1 for p in preds if p.get("is_correct") == 1)
            lines.append(f"预测: {c}/{len(preds)}  准确率: {c/len(preds):.1%}")
        if anns:
            lines.append(f"PA标注: {len(anns)}条")
        if mistakes:
            mtags: List[str] = []
            for m in mistakes:
                mtags.extend(json.loads(m.get("mistake_tags", "[]") or "[]"))
            lines.append(f"主要错误: {', '.join(mtags[:5]) if mtags else '无'}")
        if notes:
            lines.append("\n历史笔记:")
            for n in notes[:5]:
                lines.append(f"  [{(n.get('created_at') or '')[:16]}] {n.get('content','')}")
        if mistakes:
            lines.append(f"\n建议: 围绕同一 Setup 连续复训 {mistakes[0].get('category','trade')} 类错题。")
        else:
            lines.append("\n建议: 继续保持，尝试提高执行评分或增加结构标注密度。")

        self._txt_session_detail.setPlainText("\n".join(lines))
        self._current_detail_session_id = sid
        self._txt_session_note.setPlainText(notes[0]["content"] if notes else "")
        self.session_selected.emit(sid)

    def _on_save_session_note(self):
        sid = getattr(self, "_current_detail_session_id", None)
        if not sid:
            QMessageBox.information(self, "提示", "请先双击一条训练记录")
            return
        text = self._txt_session_note.toPlainText().strip()
        if not text:
            return
        self._stats.save_note(sid, text)
        QMessageBox.information(self, "已保存", "训练笔记已保存")
        self._on_session_double_click(self._session_table.currentRow(), 0)

    # ------------------------------------------------------------------
    # Mistakes
    # ------------------------------------------------------------------

    def _update_mistakes(self):
        cat = self._mistake_category.currentData() or None
        mistakes = self._stats.get_mistakes(category=cat)
        self._mistake_table.setRowCount(len(mistakes))
        self._mistake_id_map.clear()
        for i, m in enumerate(mistakes):
            self._mistake_id_map[i] = m["id"]
            tags = ", ".join(json.loads(m.get("mistake_tags", "[]") or "[]"))
            vals = [
                (m.get("created_at") or "")[:19],
                m.get("symbol", ""), m.get("timeframe", ""),
                m.get("category", ""), m.get("setup_type", ""),
                tags, m.get("description", ""),
            ]
            for col, v in enumerate(vals):
                self._mistake_table.setItem(i, col, QTableWidgetItem(v))

    def _on_mark_retrained(self):
        row = self._mistake_table.currentRow()
        if row < 0 or row not in self._mistake_id_map:
            QMessageBox.information(self, "提示", "请先选择一条错题记录")
            return
        self._stats.mark_mistake_retrained(self._mistake_id_map[row])
        self._update_mistakes()
        QMessageBox.information(self, "已标记", "该错题已标记为完成复训")
