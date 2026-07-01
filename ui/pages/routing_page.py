"""
HypoMux 路由规则页 (RoutingPage) - 进程级分流规则编辑器

用户在表格中维护「进程名 -> 出口通道」规则，MainWindow 读取后动态
序列化为 sing-box route.rules。页面只负责视图、进程选择和规则数据回吐，
不直接触碰代理内核线程，避免破坏既有单端口、多端口、聚合引擎信号链。
"""

from __future__ import annotations

import csv
import subprocess
from io import StringIO
from typing import Iterable, List, Optional

from PySide6.QtCore import Qt, Signal, QThread, Slot
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QHeaderView
from qfluentwidgets import (
    TableWidget, TitleLabel, BodyLabel, PushButton, TransparentPushButton,
    LineEdit, ComboBox, FluentIcon, MessageBoxBase, SearchLineEdit, ListWidget,
    SubtitleLabel,
)

from ui.i18n import tr


_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _decode_process_output(raw: bytes) -> str:
    """兼容 Windows 本地代码页与 UTF-8 的子进程输出解码。"""
    for encoding in ("utf-8", "mbcs", "gbk"):
        try:
            return raw.decode(encoding)
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_tasklist_csv(text: str) -> List[str]:
    """从 tasklist CSV 输出中提取去重后的 .exe 进程名。"""
    names = set()
    reader = csv.reader(StringIO(text))
    for row in reader:
        if not row:
            continue
        name = str(row[0]).strip().strip('"')
        if not name or not name.lower().endswith(".exe"):
            continue
        if any(ch in name for ch in ("/", "\\", ":", "\0")):
            continue
        names.add(name)
    return sorted(names, key=str.lower)


class ProcessListWorker(QThread):
    """后台读取当前运行中的 Windows 进程列表。"""

    result_ready = Signal(list)
    failed = Signal(str)

    def run(self):
        try:
            proc = subprocess.Popen(
                "tasklist /NH /FO CSV",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                shell=True,
                creationflags=_CREATE_NO_WINDOW,
            )
            stdout, stderr = proc.communicate(timeout=8)
            if proc.returncode not in (0, None):
                message = _decode_process_output(stderr or stdout).strip()
                self.failed.emit(message or "tasklist failed")
                return
            self.result_ready.emit(_parse_tasklist_csv(_decode_process_output(stdout)))
        except Exception as e:
            self.failed.emit(str(e))


class ProcessSelectDialog(MessageBoxBase):
    """运行中进程搜索选择对话框。"""

    def __init__(self, processes: List[str], parent=None):
        super().__init__(parent)
        self._all_processes = list(processes or [])
        self._selected_process = ""

        self.widget.setFixedWidth(520)
        self._title = SubtitleLabel(tr("routing_process_dialog_title"), self.widget)
        self.search_edit = SearchLineEdit(self.widget)
        self.search_edit.setPlaceholderText(tr("routing_process_search_placeholder"))
        self.process_list = ListWidget(self.widget)
        self.process_list.setMinimumHeight(360)
        self._empty_label = BodyLabel(tr("routing_process_empty"), self.widget)
        self._empty_label.setAlignment(Qt.AlignCenter)

        self.viewLayout.addWidget(self._title)
        self.viewLayout.addWidget(self.search_edit)
        self.viewLayout.addWidget(self.process_list)
        self.viewLayout.addWidget(self._empty_label)

        self.yesButton.setText(tr("routing_dialog_ok"))
        self.cancelButton.setText(tr("routing_dialog_cancel"))

        self.search_edit.textChanged.connect(self._filter_processes)
        self.process_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._filter_processes("")

    def _filter_processes(self, keyword: str):
        keyword = (keyword or "").strip().lower()
        self.process_list.clear()
        matched = [
            name for name in self._all_processes
            if not keyword or keyword in name.lower()
        ]
        self.process_list.addItems(matched)
        has_items = bool(matched)
        self.process_list.setVisible(has_items)
        self._empty_label.setVisible(not has_items)
        if has_items:
            self.process_list.setCurrentRow(0)

    def _on_item_double_clicked(self, item):
        if item is not None:
            self._selected_process = item.text().strip()
            self.accept()

    def selected_process(self) -> str:
        item = self.process_list.currentItem()
        if item is not None:
            return item.text().strip()
        return self._selected_process

    def validate(self) -> bool:
        self._selected_process = self.selected_process()
        return bool(self._selected_process)


class RoutingPage(QWidget):
    """进程级分流规则管理页。"""

    rules_changed = Signal()

    COL_PROCESS = 0
    COL_OUTBOUND = 1
    ROW_HEIGHT = 38

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("routingPage")
        self._available_aliases: List[str] = []
        self._controls_enabled = True
        self._process_worker: Optional[ProcessListWorker] = None
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        self._title = TitleLabel(tr("routing_title"), self)
        self._hint = BodyLabel(tr("routing_hint"), self)
        self._hint.setWordWrap(True)
        root.addWidget(self._title)
        root.addWidget(self._hint)

        self._toolbar = QHBoxLayout()
        self._toolbar.setSpacing(12)
        self.add_btn = PushButton(FluentIcon.ADD, tr("routing_add"), self)
        self.add_btn.clicked.connect(self._on_add_rule)
        self.select_process_btn = TransparentPushButton(
            FluentIcon.APPLICATION, tr("routing_select_process"), self
        )
        self.select_process_btn.clicked.connect(self._on_select_process)
        self.remove_btn = PushButton(FluentIcon.DELETE, tr("routing_remove"), self)
        self.remove_btn.clicked.connect(self._on_remove_selected)

        self._toolbar.addWidget(self.add_btn)
        self._toolbar.addWidget(self.select_process_btn)
        self._toolbar.addWidget(self.remove_btn)
        self._toolbar.addStretch()
        root.addLayout(self._toolbar)

        self.tableWidget = TableWidget(self)
        self.table = self.tableWidget
        self.tableWidget.setBorderVisible(True)
        self.tableWidget.setBorderRadius(8)
        self.tableWidget.setWordWrap(False)
        self.tableWidget.setColumnCount(2)
        self.tableWidget.setRowCount(0)
        self.tableWidget.verticalHeader().hide()
        self.tableWidget.verticalHeader().setDefaultSectionSize(self.ROW_HEIGHT)
        self.tableWidget.setSelectionBehavior(TableWidget.SelectRows)
        self._apply_headers()

        header = self.tableWidget.horizontalHeader()
        header.setSectionResizeMode(self.COL_PROCESS, QHeaderView.Stretch)
        header.setSectionResizeMode(self.COL_OUTBOUND, QHeaderView.Stretch)

        root.addWidget(self.tableWidget, 1)

    def _apply_headers(self):
        self.tableWidget.setHorizontalHeaderLabels([
            tr("routing_col_process"),
            tr("routing_col_nic"),
        ])

    # ---------- 网卡出口选项 ----------
    def set_available_adapters(self, adapters: Iterable):
        """注入当前扫描到的真实网卡别名，并刷新已有下拉框。"""
        aliases: List[str] = []
        seen = set()
        for item in adapters or []:
            if isinstance(item, dict):
                alias = str(item.get("alias") or item.get("name") or "").strip()
            else:
                alias = str(item).strip()
            if not alias or alias in seen:
                continue
            seen.add(alias)
            aliases.append(alias)
        self._available_aliases = aliases
        self._refresh_outbound_combos()

    def _make_outbound_combo(self, current: str = "aggregation") -> ComboBox:
        combo = ComboBox(self.tableWidget)
        self._fill_outbound_combo(combo, current)
        combo.currentIndexChanged.connect(lambda _i: self.rules_changed.emit())
        combo.setEnabled(self._controls_enabled)
        return combo

    def _fill_outbound_combo(self, combo: ComboBox, current: str = "aggregation"):
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(tr("routing_outbound_aggregation"), userData="aggregation")
        for alias in self._available_aliases:
            combo.addItem(alias, userData=f"nic_{alias}")
        if current.startswith("nic_") and combo.findData(current) < 0:
            combo.addItem(current[4:], userData=current)
        combo.addItem(tr("routing_outbound_direct"), userData="direct")
        idx = combo.findData(current)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _refresh_outbound_combos(self):
        for row in range(self.tableWidget.rowCount()):
            combo = self.tableWidget.cellWidget(row, self.COL_OUTBOUND)
            if combo is not None:
                current = combo.currentData() or "aggregation"
                self._fill_outbound_combo(combo, current)
                combo.setEnabled(self._controls_enabled)

    # ---------- 行构建 ----------
    def _insert_row(self, process_name: str = "", outbound: str = "aggregation"):
        row = self.tableWidget.rowCount()
        self.tableWidget.insertRow(row)
        self.tableWidget.setRowHeight(row, self.ROW_HEIGHT)

        edit = LineEdit(self.tableWidget)
        edit.setPlaceholderText(tr("routing_placeholder_process"))
        edit.setText(process_name)
        edit.textChanged.connect(lambda _t: self.rules_changed.emit())
        edit.setEnabled(self._controls_enabled)
        self.tableWidget.setCellWidget(row, self.COL_PROCESS, edit)

        combo = self._make_outbound_combo(outbound)
        self.tableWidget.setCellWidget(row, self.COL_OUTBOUND, combo)

    # ---------- 交互 ----------
    def _on_add_rule(self):
        self._insert_row("", "aggregation")
        self.rules_changed.emit()

    def _on_remove_selected(self):
        rows = sorted({idx.row() for idx in self.tableWidget.selectedIndexes()}, reverse=True)
        if not rows and self.tableWidget.rowCount() > 0:
            rows = [self.tableWidget.rowCount() - 1]
        for row in rows:
            self.tableWidget.removeRow(row)
        if rows:
            self.rules_changed.emit()

    def _on_select_process(self):
        if self._process_worker is not None and self._process_worker.isRunning():
            return
        self.select_process_btn.setEnabled(False)
        self.select_process_btn.setText(tr("routing_process_loading"))
        self._process_worker = ProcessListWorker(self)
        self._process_worker.result_ready.connect(self._on_processes_loaded)
        self._process_worker.failed.connect(self._on_processes_failed)
        self._process_worker.finished.connect(self._cleanup_process_worker)
        self._process_worker.start()

    @Slot(list)
    def _on_processes_loaded(self, processes: list):
        self._restore_process_button()
        dialog = ProcessSelectDialog(list(processes), self)
        if dialog.exec():
            process = dialog.selected_process()
            if process:
                self._insert_row(process, "aggregation")
                self.rules_changed.emit()

    @Slot(str)
    def _on_processes_failed(self, _message: str):
        self._restore_process_button()
        dialog = ProcessSelectDialog([], self)
        dialog.exec()

    def _cleanup_process_worker(self):
        if self._process_worker is not None:
            self._process_worker.deleteLater()
            self._process_worker = None
        self._restore_process_button()

    def _restore_process_button(self):
        self.select_process_btn.setText(tr("routing_select_process"))
        self.select_process_btn.setEnabled(self._controls_enabled)

    # ---------- 状态机 ----------
    def set_controls_enabled(self, enabled: bool):
        """运行中锁死规则编辑入口，停止后恢复。"""
        self._controls_enabled = enabled
        self.add_btn.setEnabled(enabled)
        self.select_process_btn.setEnabled(enabled)
        self.remove_btn.setEnabled(enabled)
        self.tableWidget.setEnabled(enabled)
        for row in range(self.tableWidget.rowCount()):
            for col in (self.COL_PROCESS, self.COL_OUTBOUND):
                widget = self.tableWidget.cellWidget(row, col)
                if widget is not None:
                    widget.setEnabled(enabled)

    # ---------- 数据 API ----------
    def get_rules(self) -> list:
        """读取表格，返回 [{"process_name": [name], "outbound": tag}, ...]。"""
        rules = []
        for row in range(self.tableWidget.rowCount()):
            edit = self.tableWidget.cellWidget(row, self.COL_PROCESS)
            combo = self.tableWidget.cellWidget(row, self.COL_OUTBOUND)
            if edit is None or combo is None:
                continue
            name = edit.text().strip()
            if not name:
                continue
            tag = combo.currentData() or "aggregation"
            rules.append({"process_name": [name], "outbound": tag})
        return rules

    def load_rules(self, rules: list):
        """从持久化配置恢复规则到表格。"""
        self.tableWidget.setRowCount(0)
        for rule in (rules or []):
            if not isinstance(rule, dict):
                continue
            procs = rule.get("process_name", [])
            name = procs[0] if isinstance(procs, list) and procs else (
                procs if isinstance(procs, str) else ""
            )
            outbound = rule.get("outbound", "aggregation")
            if name:
                self._insert_row(str(name), str(outbound))

    def retranslate_ui(self):
        self._title.setText(tr("routing_title"))
        self._hint.setText(tr("routing_hint"))
        self.add_btn.setText(tr("routing_add"))
        self.select_process_btn.setText(tr("routing_select_process"))
        self.remove_btn.setText(tr("routing_remove"))
        self._apply_headers()
        for row in range(self.tableWidget.rowCount()):
            edit = self.tableWidget.cellWidget(row, self.COL_PROCESS)
            combo = self.tableWidget.cellWidget(row, self.COL_OUTBOUND)
            if edit is not None:
                edit.setPlaceholderText(tr("routing_placeholder_process"))
            if combo is not None:
                current = combo.currentData() or "aggregation"
                self._fill_outbound_combo(combo, current)
                combo.setEnabled(self._controls_enabled)
