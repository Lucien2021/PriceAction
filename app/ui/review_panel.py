from __future__ import annotations

import json
from typing import Optional, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QComboBox, QPushButton, QTextEdit, QGroupBox,
    QTabWidget, QAbstractItemView, QLineEdit,
    QDialog, QDialogButtonBox, QMessageBox,
    QGridLayout, QScrollArea,
)

from app.storage.stats_service import StatsService, AggregateStats


# Pre-defined PA pattern tags for quick tagging
PA_TAGS = [
    "Pin Bar", "Engulfing", "Inside Bar", "Outside Bar",
    "Double Top", "Double Bottom", "Head & Shoulders",
    "Break of Structure", "Order Block", "Fair Value Gap",
    "Trend Follow", "Mean Reversion", "Breakout", "False Break",
]


class ReviewPanel(QWidget):
    """Evaluation, statistics dashboard and trade journal."""
    session_selected = Signal(str)

    def __init__(self, stats_service: StatsService, parent=None):
        super().__init__(parent)
        self._stats = stats_service
        self._trade_id_map: dict[int, int] = {}  # row -> db trade id
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_overview_tab(), "总览")
        self._tabs.addTab(self._build_journal_tab(), "交易日志")
        self._tabs.addTab(self._build_sessions_tab(), "训练记录")
        layout.addWidget(self._tabs)

    # ------------------------------------------------------------------
    # Overview tab
    # ------------------------------------------------------------------

    def _build_overview_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)
        layout.setContentsMargins(6, 6, 6, 6)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("品种:"))
        self._filter_symbol = QComboBox()
        self._filter_symbol.addItem("全部", "")
        self._filter_symbol.setMinimumWidth(80)
        filter_row.addWidget(self._filter_symbol)
        filter_row.addWidget(QLabel("周期:"))
        self._filter_tf = QComboBox()
        self._filter_tf.addItem("全部", "")
        for label in ["daily", "60m", "30m", "15m"]:
            self._filter_tf.addItem(label, label)
        filter_row.addWidget(self._filter_tf)
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self.refresh)
        filter_row.addWidget(btn_refresh)
        layout.addLayout(filter_row)

        self._stats_group = QGroupBox("核心指标")
        sg = QVBoxLayout(self._stats_group)
        self._lbl_summary = QLabel("暂无数据")
        self._lbl_summary.setWordWrap(True)
        self._lbl_summary.setMinimumHeight(120)
        sg.addWidget(self._lbl_summary)
        layout.addWidget(self._stats_group)

        self._extra_group = QGroupBox("持续统计")
        eg = QVBoxLayout(self._extra_group)
        self._lbl_extra = QLabel("")
        self._lbl_extra.setWordWrap(True)
        self._lbl_extra.setMinimumHeight(60)
        eg.addWidget(self._lbl_extra)
        layout.addWidget(self._extra_group)

        layout.addStretch()
        return w

    # ------------------------------------------------------------------
    # Journal tab
    # ------------------------------------------------------------------

    def _build_journal_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        self._trade_table = QTableWidget()
        self._trade_table.setColumnCount(11)
        self._trade_table.setHorizontalHeaderLabels([
            "时间", "方向", "入场价", "出场价", "数量",
            "盈亏", "盈亏%", "R倍数", "持仓K线", "出场原因", "标签",
        ])
        self._trade_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._trade_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._trade_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._trade_table.currentCellChanged.connect(self._on_trade_selected)
        layout.addWidget(self._trade_table)

        # Tag buttons (grid layout, 4 per row)
        tag_group = QGroupBox("快速标签 (选中交易后点击)")
        tg = QGridLayout(tag_group)
        tg.setContentsMargins(4, 4, 4, 4)
        tg.setSpacing(4)
        self._tag_buttons: list[QPushButton] = []
        for i, tag in enumerate(PA_TAGS[:8]):
            btn = QPushButton(tag)
            btn.setCheckable(True)
            btn.setMinimumHeight(26)
            btn.setStyleSheet("font-size: 10px; padding: 2px 4px;")
            btn.clicked.connect(lambda checked, t=tag: self._on_tag_toggle(t, checked))
            tg.addWidget(btn, i // 4, i % 4)
            self._tag_buttons.append(btn)
        layout.addWidget(tag_group)

        # Notes area
        notes_group = QGroupBox("复盘笔记")
        ng = QVBoxLayout(notes_group)
        self._txt_notes = QTextEdit()
        self._txt_notes.setMaximumHeight(80)
        self._txt_notes.setPlaceholderText("选中交易后可添加复盘笔记…")
        ng.addWidget(self._txt_notes)

        btn_row = QHBoxLayout()
        btn_save_note = QPushButton("保存笔记与标签")
        btn_save_note.clicked.connect(self._on_save_note)
        btn_row.addWidget(btn_save_note)
        btn_row.addStretch()
        ng.addLayout(btn_row)
        layout.addWidget(notes_group)
        return w

    # ------------------------------------------------------------------
    # Sessions tab
    # ------------------------------------------------------------------

    def _build_sessions_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        self._session_table = QTableWidget()
        self._session_table.setColumnCount(7)
        self._session_table.setHorizontalHeaderLabels([
            "会话ID", "品种", "周期", "模式", "开始时间", "交易数", "净盈亏",
        ])
        self._session_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._session_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._session_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._session_table.cellDoubleClicked.connect(self._on_session_double_click)
        layout.addWidget(self._session_table)

        # Session detail area
        self._session_detail = QGroupBox("会话详情 (双击上方记录)")
        sd = QVBoxLayout(self._session_detail)
        self._lbl_session_detail = QLabel("选择一条训练记录查看详情")
        self._lbl_session_detail.setWordWrap(True)
        sd.addWidget(self._lbl_session_detail)

        # Session note
        sn_row = QHBoxLayout()
        self._txt_session_note = QTextEdit()
        self._txt_session_note.setMaximumHeight(60)
        self._txt_session_note.setPlaceholderText("为本次训练写复盘总结…")
        sn_row.addWidget(self._txt_session_note)
        sd.addLayout(sn_row)
        btn_save_session_note = QPushButton("保存训练笔记")
        btn_save_session_note.clicked.connect(self._on_save_session_note)
        sd.addWidget(btn_save_session_note)
        layout.addWidget(self._session_detail)

        return w

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def refresh(self):
        sym = self._filter_symbol.currentData() or None
        tf = self._filter_tf.currentData() or None
        agg = self._stats.get_overall_stats(symbol=sym, timeframe=tf)
        self._update_summary(agg)
        self._update_journal(sym, tf)
        self._update_sessions()
        self._update_extra_stats(sym, tf)
        self._update_filter_symbols()

    def _update_filter_symbols(self):
        current = self._filter_symbol.currentData()
        self._filter_symbol.blockSignals(True)
        self._filter_symbol.clear()
        self._filter_symbol.addItem("全部", "")
        sessions = self._stats.get_all_sessions()
        seen = set()
        for s in sessions:
            code = s["symbol"]
            if code and code not in seen:
                self._filter_symbol.addItem(code, code)
                seen.add(code)
        if current:
            idx = self._filter_symbol.findData(current)
            if idx >= 0:
                self._filter_symbol.setCurrentIndex(idx)
        self._filter_symbol.blockSignals(False)

    def _update_summary(self, agg: AggregateStats):
        r_str = f"{agg.avg_r:.2f}R" if agg.avg_r is not None else "N/A"
        pf_str = f"{agg.profit_factor:.2f}" if agg.profit_factor != float("inf") else "N/A"
        lines = [
            f"训练场次: {agg.total_sessions}        总交易: {agg.total_trades}",
            f"胜率: {agg.win_rate:.1%}        ({agg.winners}胜 / {agg.losers}负)",
            f"平均R倍数: {r_str}        盈亏比(PF): {pf_str}",
            f"期望值: {agg.expectancy:.2f}%        净盈亏: {agg.total_pnl:+.2f}",
            f"最大连胜: {agg.max_consecutive_wins}        最大连亏: {agg.max_consecutive_losses}",
            f"最大回撤: {agg.max_drawdown_pct:.2f}%",
            "",
            f"预测总数: {agg.total_predictions}    正确: {agg.correct_predictions}    "
            f"准确率: {agg.prediction_accuracy:.1%}",
        ]
        self._lbl_summary.setText("\n".join(lines))

    def _update_journal(self, symbol=None, timeframe=None):
        self._trade_table.setRowCount(0)
        self._trade_id_map.clear()
        sessions = self._stats.get_all_sessions()
        for sess in sessions:
            if symbol and sess["symbol"] != symbol:
                continue
            if timeframe and sess["timeframe"] != timeframe:
                continue
            trades = self._stats.get_session_trades(sess["id"])
            for t in trades:
                row = self._trade_table.rowCount()
                self._trade_table.insertRow(row)
                self._trade_id_map[row] = t["id"]

                r_str = f"{t['r_multiple']:.2f}" if t["r_multiple"] is not None else "-"
                tags_list = json.loads(t.get("tags", "[]") or "[]")
                tag_str = ", ".join(tags_list) if tags_list else ""
                values = [
                    (t.get("entry_time") or "")[:19],
                    "做多" if t["direction"] == "long" else "做空",
                    f"{t['entry_price']:.2f}",
                    f"{t['exit_price']:.2f}",
                    str(t["quantity"]),
                    f"{(t['pnl'] or 0):+.2f}",
                    f"{(t['pnl_pct'] or 0):+.2f}%",
                    r_str,
                    str(t.get("hold_bars", 0)),
                    t.get("exit_reason", ""),
                    tag_str,
                ]
                for col, val in enumerate(values):
                    item = QTableWidgetItem(val)
                    if col == 5:
                        pnl = t["pnl"] or 0
                        item.setForeground(
                            Qt.GlobalColor.red if pnl > 0 else
                            Qt.GlobalColor.green if pnl < 0 else
                            Qt.GlobalColor.gray
                        )
                    self._trade_table.setItem(row, col, item)

    def _update_sessions(self):
        sessions = self._stats.get_all_sessions()
        self._session_table.setRowCount(len(sessions))
        for i, s in enumerate(sessions):
            trades = self._stats.get_session_trades(s["id"])
            net_pnl = sum(t["pnl"] or 0 for t in trades)
            values = [
                s["id"][:8],
                s["symbol"],
                s["timeframe"],
                "交易" if s["mode"] == "trade" else "预测",
                (s.get("started_at") or "")[:19],
                str(len(trades)),
                f"{net_pnl:+.2f}",
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                if col == 6:
                    item.setForeground(
                        Qt.GlobalColor.red if net_pnl > 0 else
                        Qt.GlobalColor.green if net_pnl < 0 else
                        Qt.GlobalColor.gray
                    )
                self._session_table.setItem(i, col, item)

    def _update_extra_stats(self, symbol=None, timeframe=None):
        curve = self._stats.get_equity_curve(symbol=symbol, timeframe=timeframe)
        r_dist = self._stats.get_r_distribution()
        rolling = self._stats.get_rolling_win_rate(20)

        parts = []
        if r_dist:
            parts.append("R倍数分布:")
            for k, v in r_dist.items():
                bar = "█" * min(v, 20)
                parts.append(f"  {k:>10s}  {bar} ({v})")
        if rolling:
            last = rolling[-1]
            parts.append(f"\n近{last['sample_size']}笔滚动胜率: {last['win_rate']:.1%}")
        if curve:
            parts.append(f"累计净盈亏: {curve[-1]['cumulative_pnl']:+.2f}")
        self._lbl_extra.setText("\n".join(parts) if parts else "暂无统计数据")

    # ------------------------------------------------------------------
    # Trade selection & tagging
    # ------------------------------------------------------------------

    def _on_trade_selected(self, row, _col, _prev_row, _prev_col):
        if row < 0 or row not in self._trade_id_map:
            return
        trade_id = self._trade_id_map[row]
        trades = self._stats._conn.execute(
            "SELECT tags, notes FROM trades WHERE id=?", (trade_id,)
        ).fetchone()
        if not trades:
            return
        existing_tags = json.loads(trades["tags"] or "[]")
        self._txt_notes.setPlainText(trades["notes"] or "")
        for btn in self._tag_buttons:
            btn.blockSignals(True)
            btn.setChecked(btn.text() in existing_tags)
            btn.blockSignals(False)

    def _on_tag_toggle(self, tag: str, checked: bool):
        row = self._trade_table.currentRow()
        if row < 0 or row not in self._trade_id_map:
            return
        trade_id = self._trade_id_map[row]
        trades = self._stats._conn.execute(
            "SELECT tags FROM trades WHERE id=?", (trade_id,)
        ).fetchone()
        if not trades:
            return
        existing = json.loads(trades["tags"] or "[]")
        if checked and tag not in existing:
            existing.append(tag)
        elif not checked and tag in existing:
            existing.remove(tag)
        self._stats.update_trade_tags(trade_id, existing)
        tag_item = self._trade_table.item(row, 10)
        if tag_item:
            tag_item.setText(", ".join(existing))

    def _on_save_note(self):
        row = self._trade_table.currentRow()
        if row < 0 or row not in self._trade_id_map:
            QMessageBox.information(self, "提示", "请先在交易日志中选中一笔交易")
            return
        trade_id = self._trade_id_map[row]
        text = self._txt_notes.toPlainText().strip()

        current_tags = []
        for btn in self._tag_buttons:
            if btn.isChecked():
                current_tags.append(btn.text())

        self._stats.update_trade_tags(trade_id, current_tags, text)
        QMessageBox.information(self, "已保存", "笔记和标签已保存")

    # ------------------------------------------------------------------
    # Session detail
    # ------------------------------------------------------------------

    def _on_session_double_click(self, row, _col):
        item = self._session_table.item(row, 0)
        if not item:
            return
        sid_prefix = item.text()
        sessions = self._stats.get_all_sessions()
        session = None
        for s in sessions:
            if s["id"].startswith(sid_prefix):
                session = s
                break
        if not session:
            return

        sid = session["id"]
        trades = self._stats.get_session_trades(sid)
        notes = self._stats.get_session_notes(sid)
        preds = self._stats.get_session_predictions(sid)

        total = len(trades)
        winners = sum(1 for t in trades if (t["pnl"] or 0) > 0)
        losers = sum(1 for t in trades if (t["pnl"] or 0) < 0)
        net_pnl = sum(t["pnl"] or 0 for t in trades)
        r_vals = [t["r_multiple"] for t in trades if t["r_multiple"] is not None]
        avg_r = sum(r_vals) / len(r_vals) if r_vals else None
        win_rate = winners / (winners + losers) if (winners + losers) else 0

        lines = [
            f"会话: {sid[:8]}    品种: {session['symbol']}    周期: {session['timeframe']}",
            f"时间: {(session.get('started_at') or '')[:19]} → {(session.get('finished_at') or '')[:19]}",
            f"",
            f"交易数: {total}    胜率: {win_rate:.1%}    ({winners}胜/{losers}负)",
            f"净盈亏: {net_pnl:+.2f}    平均R: {avg_r:.2f}R" if avg_r else f"净盈亏: {net_pnl:+.2f}",
        ]

        if preds:
            correct = sum(1 for p in preds if p["is_correct"] == 1)
            lines.append(f"预测: {len(preds)}次    正确: {correct}    准确率: {correct/len(preds):.1%}")

        if trades:
            lines.append("\n逐笔明细:")
            for i, t in enumerate(trades, 1):
                r_str = f"{t['r_multiple']:.2f}R" if t["r_multiple"] is not None else "-"
                d = "多" if t["direction"] == "long" else "空"
                lines.append(
                    f"  #{i} {d} {t['entry_price']:.2f}→{t['exit_price']:.2f} "
                    f"盈亏:{(t['pnl'] or 0):+.2f} {r_str} [{t.get('exit_reason','')}]"
                )

        if notes:
            lines.append("\n笔记:")
            for n in notes:
                lines.append(f"  [{(n['created_at'] or '')[:16]}] {n['content']}")

        self._lbl_session_detail.setText("\n".join(lines))
        self._current_detail_session_id = sid

        existing_notes = self._stats.get_session_notes(sid)
        if existing_notes:
            self._txt_session_note.setPlainText(existing_notes[0]["content"])
        else:
            self._txt_session_note.clear()

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
