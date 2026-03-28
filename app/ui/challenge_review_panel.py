from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
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
    def __init__(self, service: ChallengeService, parent=None):
        super().__init__(parent)
        self._service = service
        self._group_items: dict[float, QTreeWidgetItem] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._lbl_summary = QLabel("选择左侧挑战目标金额分组查看统计。")
        self._lbl_summary.setWordWrap(True)
        layout.addWidget(self._lbl_summary)

        split = QSplitter(Qt.Orientation.Horizontal)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["挑战目标 (元)"])
        self._tree.setMinimumWidth(160)
        self._tree.currentItemChanged.connect(self._on_tree_changed)
        split.addWidget(self._tree)

        right = QWidget()
        rv = QVBoxLayout(right)
        self._table = QTableWidget()
        self._table.setColumnCount(12)
        self._table.setHorizontalHeaderLabels([
            "结果", "最终资金", "日K数", "耗时(秒)", "笔数", "胜率", "均持仓K",
            "手续费", "最大回撤%", "盈亏比", "末只股票", "开始时间",
        ])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        rv.addWidget(self._table)
        split.addWidget(right)
        split.setStretchFactor(1, 2)
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
                f"日K 均值: {s.bars_mean:.1f}  中位数: {med_s}\n"
                f"胜场最短日K: {s.bars_min_win}  最长: {s.bars_max_win}",
            )
            self._tree.addTopLevelItem(item)
            self._group_items[s.target_amount] = item

        if summaries:
            first = self._tree.topLevelItem(0)
            self._tree.setCurrentItem(first)

        self._lbl_summary.setText(
            "按「挑战目标金额」分组。日K 数 = 挑战过程中推进的未来 K 线根数（日线即自然日）。"
            if summaries
            else "暂无挑战记录。完成一场挑战后会在此显示。"
        )

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
            self._table.setItem(row, 2, QTableWidgetItem(str(r["bars_elapsed"])))
            self._table.setItem(row, 3, QTableWidgetItem(f"{r['calendar_seconds']:.0f}"))
            self._table.setItem(row, 4, QTableWidgetItem(str(r["trade_count"])))
            self._table.setItem(row, 5, QTableWidgetItem(f"{r['win_rate']:.1%}"))
            self._table.setItem(row, 6, QTableWidgetItem(f"{r['avg_hold_bars']:.1f}"))
            self._table.setItem(row, 7, QTableWidgetItem(f"{r['total_commission']:.2f}"))
            self._table.setItem(row, 8, QTableWidgetItem(f"{r['max_drawdown_pct']:.2f}"))
            pf = r["profit_factor"]
            pf_s = "∞" if pf and pf > 1e9 else f"{pf:.2f}"
            self._table.setItem(row, 9, QTableWidgetItem(pf_s))
            self._table.setItem(row, 10, QTableWidgetItem(r.get("last_symbol") or ""))
            st = r.get("started_at") or ""
            self._table.setItem(row, 11, QTableWidgetItem(st[:19] if st else ""))
