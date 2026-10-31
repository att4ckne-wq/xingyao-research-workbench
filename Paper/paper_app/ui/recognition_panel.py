"""Online recognition preview and reversible, in-memory form edits."""
from __future__ import annotations

import threading
from pathlib import Path
from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QTextCursor, QUndoCommand
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox,
    QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QHeaderView)
from ..services.recognition import LABELS, FIELDS, Result, RecognitionError, read_profile, recognize, sensitive_summary, validate_input

_RUNNING = set()
_SHUTDOWN_CONNECTED = False


def _shutdown_workers():
    for worker in tuple(_RUNNING):
        worker.cancel_event.set()
    # A cancelled in-flight HTTP request has a bounded read timeout. Do not destroy
    # QThread while it is running when the application is closed immediately.
    for worker in tuple(_RUNNING):
        worker.wait(35000)


class Worker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, text, profile):
        super().__init__()
        self.text, self.profile = text, profile
        self.cancel_event = threading.Event()

    def run(self):
        try:
            self.succeeded.emit(recognize(self.text, self.profile, confirmed=True, cancel=self.cancel_event))
        except RecognitionError as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit('识别失败，表单未改变。')


def editors(dialog):
    return {**dialog.field_edits, 'content':dialog.content_edit}


class FillCommand(QUndoCommand):
    """Snapshot documents, including image resources, as a single form command."""
    def __init__(self, dialog, choices):
        super().__init__('自动识别填入')
        self.dialog = dialog
        self.before, self.after = {}, {}
        for field, (mode, text) in choices.items():
            edit = editors(dialog)[field]
            if not text.strip() or (mode == 'empty' and edit.toPlainText().strip()):
                continue
            before = edit.document().clone(dialog)
            after = before.clone(dialog)
            cursor = QTextCursor(after)
            if mode == 'replace':
                cursor.select(QTextCursor.SelectionType.Document)
            else:
                cursor.movePosition(QTextCursor.MoveOperation.End)
                if not after.isEmpty(): cursor.insertText('\n\n')
            cursor.insertText(text)  # User input remains text, never executable HTML.
            self.before[field], self.after[field] = before, after

    def matches(self, expected):
        return all(editors(self.dialog)[field].document().toHtml() == doc.toHtml() for field, doc in expected.items())

    def restore(self, docs):
        for field, doc in docs.items():
            edit = editors(self.dialog)[field]
            edit.setDocument(doc.clone(edit))

    def undo(self): self.restore(self.before)
    def redo(self): self.restore(self.after)


class RecognitionDialog(QDialog):
    def __init__(self, parent, database: str = ''):
        super().__init__(parent)
        self.setWindowTitle('自动识别 · 在线提取与原文核对')
        self.resize(1060, 700)
        self.database = Path(database) if database else None
        self.current_values = {k:e.toPlainText() for k,e in editors(parent).items()}
        self.result = None
        self.choices = {}
        self.worker = None
        self.sent_text = ''
        self.profile = None
        outer = QVBoxLayout(self)
        title = QLabel('把一段记录，整理成清楚的实验过程'); title.setObjectName('pageTitle')
        outer.addWidget(title)
        self.model_label = QLabel(); self.model_label.setWordWrap(True); self.model_label.setObjectName('helper')
        outer.addWidget(self.model_label)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget(); ll = QVBoxLayout(left); ll.setContentsMargins(0,8,8,0)
        ll.addWidget(QLabel('1   输入原文'))
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText('直接粘贴未分类的实验文本。\n目的、参数、操作、观察和判断可以混在一起。\n不会发送其他记录或附件。')
        ll.addWidget(self.input,1)
        self.count_label = QLabel('仅发送这里的文字 · 上限 20,000 字'); self.count_label.setObjectName('helper'); ll.addWidget(self.count_label)
        right = QWidget(); rl = QVBoxLayout(right); rl.setContentsMargins(8,8,0,0)
        rl.addWidget(QLabel('2   核对区块与原文依据'))
        self.tree = QTreeWidget(); self.tree.setColumnCount(3)
        self.tree.setRootIsDecorated(False)
        self.tree.setHeaderLabels(['填入区块','识别片段','填入策略'])
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(0,100); self.tree.setColumnWidth(2,100)
        rl.addWidget(self.tree,1)
        self.detail = QPlainTextEdit(); self.detail.setReadOnly(True)
        self.detail.setPlaceholderText('选择区块，查看完整内容与原文位置。'); self.detail.setMaximumHeight(160)
        rl.addWidget(self.detail)
        self.splitter.addWidget(left); self.splitter.addWidget(right); self.splitter.setSizes([420,600]); outer.addWidget(self.splitter,1)
        self.status = QLabel('原文中的缺失信息保持空白；未归类文字会留在补充记录。')
        self.status.setWordWrap(True); self.status.setObjectName('helper'); outer.addWidget(self.status)
        buttons = QHBoxLayout()
        self.run_button = QPushButton('预览发送并识别…'); self.run_button.setProperty('primary',True)
        self.run_button.clicked.connect(self.start_recognition); buttons.addWidget(self.run_button)
        self.cancel_request = QPushButton('取消请求'); self.cancel_request.setEnabled(False); self.cancel_request.clicked.connect(self.cancel_running); buttons.addWidget(self.cancel_request)
        buttons.addStretch()
        self.apply_button = QPushButton('填入所选区块'); self.apply_button.setEnabled(False); self.apply_button.clicked.connect(self.accept_selection); buttons.addWidget(self.apply_button)
        close = QPushButton('关闭'); close.clicked.connect(self.reject); buttons.addWidget(close)
        outer.addLayout(buttons)
        self.tree.currentItemChanged.connect(self.show_detail)
        self.input.textChanged.connect(self.invalidate)
        self.refresh_profile()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self,'splitter'):
            self.splitter.setOrientation(Qt.Orientation.Vertical if self.width() < 800 else Qt.Orientation.Horizontal)

    def refresh_profile(self):
        try:
            self.profile = read_profile(self.database)
            self.model_label.setText(f'模型：{self.profile.name} / {self.profile.model}\n服务：{self.profile.base_url} · 凭据仅在确认发送后读取')
        except RecognitionError as exc:
            self.profile = None
            self.model_label.setText(str(exc) + ' 可关闭此窗口，到设置中检查模型来源。')

    def invalidate(self):
        self.result = None; self.apply_button.setEnabled(False)
        self.tree.clear(); self.detail.clear()
        self.count_label.setText(f'{len(self.input.toPlainText()):,} / 20,000 字 · 发送前会显示完整预览')

    def start_recognition(self):
        global _SHUTDOWN_CONNECTED
        text = self.input.toPlainText()
        try: validate_input(text)
        except RecognitionError as exc:
            self.status.setText(str(exc)); return
        self.refresh_profile()
        if self.profile is None: return
        preview = QDialog(self); preview.setWindowTitle('确认发送这段原文'); preview.resize(680,540)
        layout = QVBoxLayout(preview)
        destination = QLabel(f'目标模型：{self.profile.model}\n目标服务：{self.profile.base_url}\n本次只发送下方 {len(text):,} 字，可能产生模型服务费用。')
        destination.setWordWrap(True); layout.addWidget(destination)
        findings = sensitive_summary(text)
        warning = QLabel('敏感项提示：' + ('、'.join(findings) + '。请返回原文删除或脱敏后再发送。' if findings else '未命中内置模式；仍请检查私人或未公开信息。'))
        warning.setWordWrap(True); layout.addWidget(warning)
        content = QPlainTextEdit(text); content.setReadOnly(True); layout.addWidget(content,1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Ok).setText('确认发送并识别')
        box.button(QDialogButtonBox.StandardButton.Cancel).setText('返回编辑')
        box.accepted.connect(preview.accept); box.rejected.connect(preview.reject); layout.addWidget(box)
        if preview.exec() != QDialog.DialogCode.Accepted: return
        self.sent_text = text; self.result = None; self.apply_button.setEnabled(False)
        self.run_button.setEnabled(False); self.cancel_request.setEnabled(True)
        self.status.setText('正在识别…可以取消；关闭窗口后迟到的结果不会填入。')
        worker = Worker(text,self.profile); self.worker = worker
        if not _SHUTDOWN_CONNECTED:
            QApplication.instance().aboutToQuit.connect(_shutdown_workers)
            _SHUTDOWN_CONNECTED = True
        _RUNNING.add(worker)
        worker.succeeded.connect(self.receive_result); worker.failed.connect(self.receive_error)
        worker.finished.connect(lambda: self.finish_worker(worker))
        worker.finished.connect(lambda: (_RUNNING.discard(worker),worker.deleteLater()))
        worker.start()

    def finish_worker(self, worker):
        if self.worker is worker:
            self.worker = None; self.run_button.setEnabled(True); self.cancel_request.setEnabled(False)

    def cancel_running(self):
        if self.worker: self.worker.cancel_event.set()
        self.result = None; self.apply_button.setEnabled(False)
        self.status.setText('已取消。网络请求可能仍在结束，迟到结果不会填入。')

    def receive_error(self, message):
        if self.isVisible(): self.status.setText(message)

    def receive_result(self, result: Result):
        if self.worker and self.worker.cancel_event.is_set(): return
        if self.input.toPlainText() != self.sent_text:
            self.status.setText('原文已修改，本次结果未采用。请重新识别。'); return
        self.result = result; self.tree.clear(); self.policies = {}
        for field in FIELDS:
            value = result.fields[field]
            item = QTreeWidgetItem([LABELS[field],value.replace('\n',' ')[:90] or '原文未提供',''])
            item.setData(0,Qt.ItemDataRole.UserRole,field)
            item.setSizeHint(0,QSize(0,42))
            item.setCheckState(0,Qt.CheckState.Checked if value and not self.current_values[field].strip() else Qt.CheckState.Unchecked)
            item.setToolTip(1,value)
            self.tree.addTopLevelItem(item)
            combo = QComboBox()
            for label,code in [('仅填空','empty'),('追加','append'),('替换','replace')]: combo.addItem(label,code)
            self.tree.setItemWidget(item,2,combo); self.policies[field] = combo
        self.status.setText('已完成原文依据校验。勾选要填入的区块；已有内容默认不勾选。' + (' 未归类文字已保留在补充区。' if result.unassigned else ''))
        self.apply_button.setEnabled(any(result.fields.values()))

    def show_detail(self, item, previous):
        if not item or not self.result: return
        field = item.data(0,Qt.ItemDataRole.UserRole)
        positions = [f'原文字符 {start+1}–{end}' for f,start,end in self.result.evidence if f == field]
        if field == 'content' and self.result.unassigned: positions.append('包含未归类原文，留待人工确认')
        self.detail.setPlainText(self.result.fields[field] + '\n\n' + '\n'.join(positions))

    def accept_selection(self):
        if not self.result or self.input.toPlainText() != self.sent_text: return
        self.choices = {}
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i); field = item.data(0,Qt.ItemDataRole.UserRole)
            if item.checkState(0) == Qt.CheckState.Checked and self.result.fields[field]:
                self.choices[field] = (self.policies[field].currentData(),self.result.fields[field])
        if not self.choices:
            self.status.setText('请至少勾选一个有内容的区块。'); return
        self.accept()

    def done(self, result):
        if self.worker: self.worker.cancel_event.set()
        super().done(result)
