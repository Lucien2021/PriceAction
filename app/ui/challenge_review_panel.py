from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QLabel,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
)

from app.storage.challenge_service import ChallengeService


class ChallengeReviewPanel(QWidget):
    _HDR = [
        "结果",
        "最终资金",
        "推进天数",
        "笔数",
        "胜率",
        "均持仓(天)",
        "手续费",
        "最大回撤%",
        "盈亏比",
        "末只股票",
        "开始时间",
    ]

    def __init__(self, service: ChallengeService, parent=None):
        super().__init__(parent)
        self._service = service
        self._group_items: dict[float, QTreeWidgetItem] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self._lbl_summary = QLabel("选择左侧挑战目标金额分组查看统计。")
        self._lbl_summary.setWordWrap(True)
        self._lbl_summary.setStyleSheet("color:#333;font-size:13px;padding:4px 2px;")
        layout.addWidget(self._lbl_summary)

        split = QSplitter(Qt.Orientation.Horizontal)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["挑战目标 (元)"])
        self._tree.setMinimumWidth(180)
        self._tree.setFont(QFont(self._tree.font().family(), 12))
        self._tree.currentItemChanged.connect(self._on_tree_changed)
        split.addWidget(self._tree)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self._table = QTableWidget()
        self._table.setColumnCount(len(self._HDR))
        self._table.setHorizontalHeaderLabels(self._HDR)
        self._table.setFont(QFont(self._table.font().family(), 12))
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(True)
        self._table.setWordWrap(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setStyleSheet(
            "QTableWidget { gridline-color: #c8c8c8; }"
            "QHeaderView::section {"
            "  background-color: #e8e8e8;"
            "  padding: 8px 10px;"
            "  border: 1px solid #c0c0c0;"
            "  font-weight: 600;"
            "  min-height: 32px;"
            "}"
        )
        hh = self._table.horizontalHeader()
        hh.setMinimumSectionSize(96)
        hh.setDefaultSectionSize(120)
        hh.setStretchLastSection(True)
        for i in range(len(self._HDR)):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(len(self._HDR) - 1, QHeaderView.ResizeMode.Stretch)
        rv.addWidget(self._table)
        split.addWidget(right)
        split.setStretchFactor(1, 3)
        layout.addWidget(split)

    def refresh(self) -> None:
        self._tree.clear()
        self._group_items.clear()
        summaries = self._service.group_summaries()
        grouped = self._service.list_runs_grouped_by_target()

        for s in summaries:
            item = QTreeWidgetItem([f"{s.target_amount:,.0f}"])
            item.setData(0, Qt.ItemDataRole.UserRole, s.target_amount)
            n = s.run_count
            wins = s.wins
            med = s.bars_median
            med_s = f"{med:.0f}" if med is not None else "-"
            item.setToolTip(
                0,
                f"场次: {n}  胜场: {wins}  胜率: {s.win_rate_runs:.0%}\n"
                f"推进天数 均值: {s.bars_mean:.1f}  中位数: {med_s}\n"
                f"胜场最短: {s.bars_min_win} 天  最长: {s.bars_max_win} 天",
            )
            self._tree.addTopLevelItem(item)
            self._group_items[s.target_amount] = item

        if summaries:
            first = self._tree.topLevelItem(0)
            self._tree.setCurrentItem(first)

        self._lbl_summary.setText(
            "「推进天数」= 本场挑战里你点击推进的日 K 根数（日线下一根即一天）。"
            "「均持仓(天)」= 每笔平仓从入场 K 到平仓 K 所经历的 K 线根数（含两端）的平均。"
            if summaries
            else "暂无挑战记录。完成一场挑战后会在此显示。"
        )

    def _fmt_started(self, st: str) -> str:
        if not st:
            return ""
        s = st.replace("T", " ")
        return s[:16] if len(s) >= 16 else s

    def _on_tree_changed(self, current: Optional[QTreeWidgetItem], _prev) -> None:
        if current is None:
            self._table.setRowCount(0)
            return
        target = current.data(0, Qt.ItemDataRole.UserRole)
        if target is None:
            return
        grouped = self._service.list_runs_grouped_by_target()
        runs = grouped.get(float(target), [])
        self._table.setRowCount(len(runs))
        outcome_cn = {"win": "成功", "lose": "失败", "abandon": "放弃"}
        for row, r in enumerate(runs):
            oc = outcome_cn.get(r["outcome"], r["outcome"])
            self._table.setItem(row, 0, QTableWidgetItem(oc))
            self._table.setItem(row, 1, QTableWidgetItem(f"{r['final_equity']:,.0f}"))
            days = int(r["bars_elapsed"])
            it_days = QTableWidgetItem(str(days))
            cal = r.get("calendar_seconds") or 0
            if cal and cal > 0:
                it_days.setToolTip(f"真实耗时约 {int(cal // 60)} 分钟（仅供参考）")
            self._table.setItem(row, 2, it_days)
            self._table.setItem(row, 3, QTableWidgetItem(str(r["trade_count"])))
            self._table.setItem(row, 4, QTableWidgetItem(f"{r['win_rate']:.1%}"))
            self._table.setItem(row, 5, QTableWidgetItem(f"{r['avg_hold_bars']:.1f}"))
            self._table.setItem(row, 6, QTableWidgetItem(f"{r['total_commission']:.2f}"))
            self._table.setItem(row, 7, QTableWidgetItem(f"{r['max_drawdown_pct']:.2f}"))
            pf = r["profit_factor"]
            pf_s = "∞" if pf and pf > 1e9 else f"{pf:.2f}"
            self._table.setItem(row, 8, QTableWidgetItem(pf_s))
            self._table.setItem(row, 9, QTableWidgetItem(r.get("last_symbol") or ""))
            self._table.setItem(row, 10, QTableWidgetItem(self._fmt_started(r.get("started_at") or "")))

        self._table.resizeColumnsToContents()
        self._table.horizontalHeader().setSectionResizeMode(len(self._HDR) - 1, QHeaderView.ResizeMode.Stretch)
