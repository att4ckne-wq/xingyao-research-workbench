"""实验记录 - PySide6 主窗口与界面组件。"""

# 提权目的：更新导入（多选视图/文件图标/图片/缓冲/临时文件），支撑拖图入文、拖拽附件与批量选择三项新功能。
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path

# matplotlib 及其 QtAgg 后端首次导入约需 5 秒，改为首次绘图时按需加载，避免拖慢启动。
def _load_matplotlib():
    """按需导入 matplotlib，返回 (FigureCanvasQTAgg, Figure)。"""
    import matplotlib

    matplotlib.use("QtAgg")
    matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "sans-serif"]
    matplotlib.rcParams["axes.unicode_minus"] = False
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.figure import Figure

    return FigureCanvasQTAgg, Figure

# 提权目的：修正导入位置（QFileIconProvider 属 QtWidgets），支撑附件总览的格式图标显示。
# 提权目的：补充绘图类导入（QPainter/QColor/QPen/QPointF），用于绘制输入框下沿的可拖拽调整边。
from PySide6.QtCore import QBuffer, QDateTime, QFileInfo, QItemSelectionModel, QMimeData, QPoint, QPointF, QSize, Qt, QUrl, Signal  # noqa: E402
from PySide6.QtGui import QColor, QDrag, QImage, QPainter, QPen, QShortcut, QKeySequence, QUndoStack  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFileIconProvider,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QScrollArea,
    QHeaderView,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import db
from ..models import OCCURRED_FORMAT, PROJECT_STATUS, RECORD_FIELDS
from ..security import safe_filename
from ..services import export as export_service
from .text_tools import FormulaDialog, SymbolPaletteDialog, ToolTextEdit
from .theme import THEME_SCIENTIFIC, apply_theme, available_themes, theme_label, palette
from .recognition_panel import RecognitionDialog, FillCommand
from ..services.recognition import read_profile, RecognitionError


class RecordTree(QTreeWidget):
    """记录列表：拖拽时**实时移动**行，松手后把新顺序写回数据库。

    * 拖动过程中每经过一行就立即把选中块移到该位置（动态反馈，不会出现"拖完才跳"）；
    * 只做同层重排，不会产生父子嵌套；落点按鼠标在目标行的上/下半区决定插前还是插后；
    * 移动后保持选中与可见（`scrollToItem`），避免"拖完记录看不见"。
    """

    orderChanged = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setColumnCount(5)
        self.setHeaderLabels(["发生时间", "事件类型", "标题", "项目", "标签"])
        # 提权目的：启用记录列表 Ctrl/Shift 多选，实现批量选中事件功能。
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setRootIsDecorated(False)
        # 行由本类原位重排；startDrag 自行运行 QDrag，避免 Qt 默认流程在松手后移除源行。
        # 仅切换 DragDrop/InternalMove 不能关闭默认 startDrag 的源行清除。
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(False)  # 用"行实时位移"代替插入线
        self._drag_origin: list[QTreeWidgetItem] | None = None
        self._drop_committed = False

    # ---------- 内部工具 ----------

    def _rows(self) -> list[QTreeWidgetItem]:
        return [self.topLevelItem(index) for index in range(self.topLevelItemCount())]

    def _block_and_index(self, point) -> tuple[list[QTreeWidgetItem], list[QTreeWidgetItem], int] | None:
        rows = self._rows()
        selected = [item for item in rows if item.isSelected()]
        if not selected:
            return None
        rest = [item for item in rows if item not in selected]
        target = self.itemAt(point)
        if target is None:
            # 鼠标越过第一行/最后一行时只移动到相应边界；横向空白处按所在行处理。
            target = self.itemAt(QPoint(1, point.y()))
            if target is None:
                if rows and point.y() < self.visualItemRect(rows[0]).top():
                    return rest, selected, 0
                if rows and point.y() > self.visualItemRect(rows[-1]).bottom():
                    return rest, selected, len(rest)
                return None
        if target in selected:
            return None
        index = rest.index(target)
        rect = self.visualItemRect(target)
        if point.y() > rect.center().y():
            index += 1
        return rest, selected, index

    def _apply(self, rest, selected, index) -> bool:
        rows = self._rows()
        new_order = rest[:index] + selected + rest[index:]
        if new_order == rows:
            return False
        # 每次只挪动被拖的行，避免经过一行就拆装整个列表。
        self.setUpdatesEnabled(False)
        try:
            for item in reversed(selected):
                self.takeTopLevelItem(self.indexOfTopLevelItem(item))
            for offset, item in enumerate(selected):
                self.insertTopLevelItem(index + offset, item)
            self.clearSelection()
            for item in selected:
                item.setSelected(True)
            self.setCurrentItem(selected[0], 0, QItemSelectionModel.SelectionFlag.NoUpdate)
        finally:
            self.setUpdatesEnabled(True)
        self.scrollToItem(selected[0], QAbstractItemView.ScrollHint.EnsureVisible)
        self.viewport().update()
        return True

    def _restore_drag_origin(self) -> None:
        if self._drag_origin is None or self._rows() == self._drag_origin:
            return
        selected = [item for item in self._rows() if item.isSelected()]
        self.setUpdatesEnabled(False)
        try:
            while self.topLevelItemCount():
                self.takeTopLevelItem(0)
            self.insertTopLevelItems(0, self._drag_origin)
            self.clearSelection()
            for item in selected:
                item.setSelected(True)
        finally:
            self.setUpdatesEnabled(True)
        self.viewport().update()

    # ---------- 拖拽 ----------

    def startDrag(self, supported_actions) -> None:  # noqa: N802
        selected = [item for item in self._rows() if item.isSelected()]
        if not selected:
            return
        self._drag_origin = self._rows()
        self._drop_committed = False
        # 不调用 QTreeWidget.startDrag：其基类会在 MoveAction 返回后移除已选源行。
        # 本控件已经在 dragMoveEvent 中移动原有行，不能再让 Qt 执行一次源端清除。
        drag = QDrag(self)
        payload = QMimeData()
        payload.setData("application/x-paper-record-order", b"reorder")
        drag.setMimeData(payload)
        try:
            drag.exec(Qt.DropAction.MoveAction, Qt.DropAction.MoveAction)
        finally:
            if not self._drop_committed:
                self._restore_drag_origin()
            self._drag_origin = None
            drag.deleteLater()

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        # 仅接收本控件正在进行的排序；不读取外部或手工构造事件的 MIME 指针。
        if self._drag_origin is None:
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_origin is None:
            self._drag_origin = self._rows()
        point = event.position().toPoint() if hasattr(event, "position") else event.pos()
        placement = self._block_and_index(point)
        if placement is not None:
            self._apply(*placement)
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()

    def dropEvent(self, event) -> None:  # noqa: N802
        if self._drag_origin is None:
            self._drag_origin = self._rows()
        point = event.position().toPoint() if hasattr(event, "position") else event.pos()
        placement = self._block_and_index(point)
        if not self.selectedItems():
            event.ignore()
            return
        if placement is not None:
            self._apply(*placement)
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        self._drop_committed = True
        if self._rows() != self._drag_origin:
            ids = [item.data(0, Qt.ItemDataRole.UserRole) for item in self._rows()]
            self.orderChanged.emit(ids)
        self._drag_origin = None


class ProjectDialog(QDialog):
    """Edit project metadata without writing until the main window accepts it."""

    def __init__(self, project: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑项目")
        self.resize(500, 330)
        layout = QVBoxLayout(self)
        title = QLabel("编辑项目"); title.setObjectName("pageTitle")
        layout.addWidget(title)
        form = QFormLayout()
        self.name_edit = QLineEdit(project["name"])
        self.name_edit.setClearButtonEnabled(True)
        self.description_edit = QPlainTextEdit()
        self.description_edit.setPlainText(project.get("description") or "")
        self.description_edit.setPlaceholderText("项目说明（可选）")
        self.description_edit.setMinimumHeight(90)
        self.status_combo = QComboBox()
        self.status_combo.addItems(PROJECT_STATUS)
        status = project.get("status") or PROJECT_STATUS[0]
        if self.status_combo.findText(status) < 0:
            self.status_combo.addItem(status)
        self.status_combo.setCurrentText(status)
        form.addRow("项目名称", self.name_edit)
        form.addRow("项目说明", self.description_edit)
        form.addRow("项目状态", self.status_combo)
        layout.addLayout(form)
        self.error_label = QLabel(""); self.error_label.setObjectName("validationError")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.save_button = buttons.button(QDialogButtonBox.Save)
        self.save_button.setText("保存修改"); self.save_button.setProperty("primary", True)
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.name_edit.textChanged.connect(self._validate_name)
        self._validate_name()
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _validate_name(self) -> bool:
        valid = bool(self.name_edit.text().strip())
        self.save_button.setEnabled(valid)
        self.error_label.setText("" if valid else "项目名称不能为空。")
        return valid

    def project_values(self) -> dict:
        return {"name": self.name_edit.text().strip(),
                "description": self.description_edit.toPlainText(),
                "status": self.status_combo.currentText()}

    def _save(self) -> None:
        if self._validate_name():
            self.accept()
        else:
            self.name_edit.setFocus()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("实验记录")
        self.resize(1280, 800)
        self._current_project_id: str | None = None
        self._current_event_type: str | None = None

        self._build_toolbar()
        central = QWidget()
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(22, 16, 22, 12)
        root_layout.setSpacing(14)
        heading = QHBoxLayout()
        brand = QLabel("PAPER  /  实验记录"); brand.setObjectName("brandTitle")
        heading.addWidget(brand); heading.addStretch()
        self.overview = QLabel("记录过程 · 保留证据 · 形成结论"); self.overview.setObjectName("muted")
        heading.addWidget(self.overview)
        root_layout.addLayout(heading)

        # 左侧：项目列表 + 事件类型筛选 + 搜索
        left = QWidget(); left.setObjectName("sidebar")
        left.setMinimumWidth(220); left.setMaximumWidth(340)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(10)
        section = QLabel("工作空间"); section.setObjectName("sectionTitle")
        section_row = QHBoxLayout()
        section_row.addWidget(section); section_row.addStretch()
        self.edit_project_button = QPushButton("编辑")
        self.edit_project_button.setProperty("compact", True)
        self.edit_project_button.setToolTip("编辑选中的项目（双击项目或按 F2）")
        self.edit_project_button.setEnabled(False)
        self.edit_project_button.clicked.connect(self.edit_selected_project)
        section_row.addWidget(self.edit_project_button)
        left_layout.addLayout(section_row)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("全文检索（标题/正文/标签，Enter 搜索）")
        self.search_edit.returnPressed.connect(self.refresh_records)
        left_layout.addWidget(self.search_edit)
        clear_btn = QPushButton("清除搜索")
        clear_btn.clicked.connect(self._clear_search)
        left_layout.addWidget(clear_btn)
        self.event_filter = QComboBox()
        self.event_filter.addItem("全部事件类型", None)
        self.event_filter.currentIndexChanged.connect(self._on_event_filter)
        left_layout.addWidget(self.event_filter)
        self.project_list = QListWidget()
        self.project_list.setObjectName("projectList")
        self.project_list.setToolTip("双击项目、右键编辑，或选中后按 F2")
        self.project_list.currentItemChanged.connect(self._on_project_selected)
        self.project_list.itemDoubleClicked.connect(self._edit_project_item)
        self.project_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.project_list.customContextMenuRequested.connect(self._project_context_menu)
        self.edit_project_shortcut = QShortcut(QKeySequence("F2"), self.project_list)
        self.edit_project_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.edit_project_shortcut.activated.connect(self.edit_selected_project)
        self.edit_project_shortcut.setEnabled(False)
        left_layout.addWidget(self.project_list, 1)

        # 中部：页签
        self.tabs = QTabWidget()
        self.record_tree = self._make_record_tree()
        self.timeline_tree = self._make_timeline_tree()
        self.tabs.addTab(self._wrap_record_page(), "记录")
        self.tabs.addTab(self.timeline_tree, "时间线")
        self.stats_page = self._make_stats_page()
        self.tabs.addTab(self.stats_page, "统计")
        self.tabs.addTab(self._make_event_type_page(), "事件类型")
        self.tabs.addTab(self._make_settings_page(), "设置")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 1020])
        root_layout.addWidget(splitter, 1)
        self.setCentralWidget(central)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.refresh_all()

    # ---------- 构建 ----------

    def _build_toolbar(self) -> None:
        bar = self.addToolBar("main")
        bar.setObjectName("mainToolbar")
        bar.setIconSize(QSize(16, 16))
        # 提权目的：工具栏增加"批量回收选中记录"入口，配合多选实现批量操作。
        actions = [
            ("新建项目", self.new_project),
            ("新建记录", self.new_record),
            ("编辑选中记录", self.edit_selected_record),
            ("批量回收选中记录", self.batch_trash_selected),
            ("导出当前项目", self.export_current_project),
            ("回收站", self.open_trash),
            ("按时间排序", self.reset_record_order),
            ("刷新", self.refresh_all),
        ]
        for text, slot in actions:
            action = bar.addAction(text)
            action.triggered.connect(slot)
            action.setToolTip(text)
            if text == "新建记录":
                action.setShortcut(QKeySequence("Ctrl+N"))
                bar.widgetForAction(action).setProperty("primary", True)
            if text in {"新建记录", "导出当前项目", "按时间排序"}:
                bar.addSeparator()

    def _make_record_tree(self) -> "RecordTree":
        tree = RecordTree()
        tree.setAlternatingRowColors(True)
        tree.setUniformRowHeights(True)
        tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        tree.setColumnWidth(1, 110); tree.setColumnWidth(3, 160); tree.setColumnWidth(4, 130)
        tree.itemDoubleClicked.connect(lambda *_: self.edit_selected_record())
        tree.orderChanged.connect(self._on_record_order_changed)
        return tree

    def _on_record_order_changed(self, record_ids: list[str]) -> None:
        """拖拽排序落位后写回数据库。"""
        ids = [record_id for record_id in record_ids if record_id]
        if not ids:
            return
        try:
            changed = db.reorder_visible_records(ids)
        except ValueError:
            self.refresh_records()
            self.status.showMessage("记录列表已变化，请重新拖拽", 5000)
            return
        if changed:
            self.refresh_timeline()
            self.status.showMessage("已保存拖拽排序；工具栏「按时间排序」可恢复时间顺序", 6000)

    def reset_record_order(self) -> None:
        """清除手动排序，恢复按发生时间倒序。"""
        db.clear_record_order()
        self.refresh_all()
        self.status.showMessage("已恢复按发生时间排序", 4000)

    def _make_timeline_tree(self) -> QTreeWidget:
        tree = QTreeWidget()
        tree.setColumnCount(3)
        tree.setHeaderLabels(["日期", "事件类型", "标题"])
        tree.itemDoubleClicked.connect(self._open_timeline_record)
        return tree

    def _wrap_record_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("记录库"); title.setObjectName("sectionTitle"); layout.addWidget(title)
        self.empty_label = QLabel("还没有匹配的记录。新建记录，或调整左侧搜索与筛选。")
        self.empty_label.setObjectName("helper"); self.empty_label.setWordWrap(True)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.record_tree)
        return page

    def _make_stats_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.stats_mode = QComboBox()
        self.stats_mode.addItem("按事件类型", "type")
        self.stats_mode.addItem("按月统计", "month")
        self.stats_mode.currentIndexChanged.connect(self._render_stats)
        layout.addWidget(self.stats_mode)
        # 画布按需创建：matplotlib 首次导入约 5 秒，推迟到第一次打开"统计"页时再做。
        self.stats_canvas = None
        self.stats_placeholder = QLabel("图表将在首次打开本页时加载…")
        self.stats_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.stats_placeholder, 1)
        return page

    def _make_event_type_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.event_tree = QTreeWidget()
        self.event_tree.setColumnCount(2)
        self.event_tree.setHeaderLabels(["事件类型", "来源"])
        layout.addWidget(self.event_tree, 1)
        buttons = QHBoxLayout()
        add_btn = QPushButton("添加类型")
        add_btn.clicked.connect(self._add_event_type)
        rename_btn = QPushButton("重命名自定义类型")
        rename_btn.clicked.connect(self._rename_event_type)
        delete_btn = QPushButton("删除自定义类型")
        delete_btn.clicked.connect(self._delete_event_type)
        for btn in (add_btn, rename_btn, delete_btn):
            buttons.addWidget(btn)
        layout.addLayout(buttons)
        return page

    def _make_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.theme_combo = QComboBox()
        for name in available_themes():
            self.theme_combo.addItem(theme_label(name), name)
        current = db.get_setting("theme", THEME_SCIENTIFIC)
        index = self.theme_combo.findData(current)
        self.theme_combo.setCurrentIndex(index if index >= 0 else 0)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        form.addRow("主题", self.theme_combo)
        layout.addLayout(form)
        layout.addWidget(QLabel("主题选择立即生效并自动保存；“默认经典”为 Qt 原生外观，可随时切回。"))
        path_label = QLabel(f"数据库位置：{db.DB_PATH}"); path_label.setWordWrap(True); path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); layout.addWidget(path_label)
        attachment_label = QLabel(f"附件目录：{db.ATTACHMENT_ROOT}"); attachment_label.setWordWrap(True); layout.addWidget(attachment_label)
        model_title = QLabel("在线自动识别"); model_title.setObjectName("sectionTitle"); layout.addWidget(model_title)
        self.model_source = QLineEdit(db.get_setting("recognition.database_path", ""))
        self.model_source.setPlaceholderText("留空自动查找原 Workbench；也可填写 workbench.db 完整路径")
        layout.addWidget(self.model_source)
        model_buttons = QHBoxLayout()
        save_source = QPushButton("保存来源并刷新状态")
        def refresh_model():
            source = self.model_source.text().strip()
            db.set_setting("recognition.database_path", source)
            try:
                profile = read_profile(Path(source) if source else None)
                self.model_status.setText(f"已配置：{profile.name} / {profile.model}\n{profile.base_url}\n每次发送前预览确认；此处不读取密钥或测试联网。")
            except RecognitionError as exc:
                self.model_status.setText(str(exc))
        save_source.clicked.connect(refresh_model); model_buttons.addWidget(save_source); model_buttons.addStretch()
        layout.addLayout(model_buttons)
        self.model_status = QLabel("新建记录 → 自动识别：确认后才读取凭据并发送所选原文。")
        self.model_status.setWordWrap(True); self.model_status.setObjectName("helper"); layout.addWidget(self.model_status)
        layout.addStretch(1)
        return page

    def _on_theme_changed(self) -> None:
        name = self.theme_combo.currentData()
        apply_theme(name)
        db.set_setting("theme", name)
        self.status.showMessage(f"主题已切换为 {theme_label(name)}", 5000)
        self._render_stats()

    # ---------- 数据刷新 ----------

    def refresh_all(self) -> None:
        db.init_db()
        self.refresh_projects()
        self.refresh_event_filter()
        self.refresh_records()
        self.refresh_timeline()
        self._refresh_event_type_page()
        self._render_stats()
        counts = db.overall_counts()
        self.overview.setText(f"{counts['projects']} 个项目   ·   {counts['records']} 条记录   ·   {counts['attachments']} 份附件")
        self.status.showMessage(
            f"项目 {counts['projects']} · 记录 {counts['records']} · 附件 {counts['attachments']}"
            f" · 数据位置 {db.DB_PATH}"
        )

    def refresh_projects(self) -> None:
        selected_id = self._current_project_id
        self.project_list.blockSignals(True)
        self.project_list.clear()
        all_item = QListWidgetItem("全部项目")
        all_item.setData(Qt.UserRole, None)
        self.project_list.addItem(all_item)
        for project in db.list_projects():
            item = QListWidgetItem(f"{project['name']}（{project['status']}）")
            item.setData(Qt.UserRole, project["id"])
            item.setToolTip(f"{project['name']}（{project['status']}）\n{project.get('description') or ''}")
            self.project_list.addItem(item)
        self.project_list.blockSignals(False)
        row = next((i for i in range(self.project_list.count())
                    if self.project_list.item(i).data(Qt.UserRole) == selected_id), 0)
        self.project_list.setCurrentRow(row)

    def refresh_event_filter(self) -> None:
        current = self.event_filter.currentData()
        self.event_filter.blockSignals(True)
        self.event_filter.clear()
        self.event_filter.addItem("全部事件类型", None)
        for event_type in db.list_event_types():
            self.event_filter.addItem(event_type["name"], event_type["id"])
        index = self.event_filter.findData(current)
        self.event_filter.setCurrentIndex(index if index >= 0 else 0)
        self.event_filter.blockSignals(False)

    def refresh_records(self) -> None:
        rows = db.list_records(
            project_id=self._current_project_id,
            event_type_id=self._current_event_type,
            query=self.search_edit.text() or None,
        )
        self.record_tree.clear()
        for row in rows:
            item = QTreeWidgetItem(
                [
                    row["occurred_at"],
                    row["event_type_name"],
                    row["title"],
                    row["project_name"],
                    "、".join(row["tags"]),
                ]
            )
            item.setData(0, Qt.UserRole, row["id"])
            if row.get("sensitive"):
                item.setText(2, "⚠ " + row["title"])
            self.record_tree.addTopLevelItem(item)
        self.empty_label.setVisible(not rows)
        self.record_tree.resizeColumnToContents(0)

    def refresh_timeline(self) -> None:
        rows = db.list_records(
            project_id=self._current_project_id,
            event_type_id=self._current_event_type,
        )
        # 时间线始终按发生时间分组；记录页的手动排序不应拆散同一天的分组。
        rows.sort(key=lambda row: row["occurred_at"], reverse=True)
        self.timeline_tree.clear()
        current_day = None
        day_item = None
        for row in rows:
            day = str(row["occurred_at"])[:10]
            if day != current_day:
                current_day = day
                day_item = QTreeWidgetItem([day, "", ""])
                self.timeline_tree.addTopLevelItem(day_item)
                day_item.setExpanded(True)
            child = QTreeWidgetItem(["", row["event_type_name"], row["title"]])
            child.setData(0, Qt.UserRole, row["id"])
            day_item.addChild(child)

    def _refresh_event_type_page(self) -> None:
        self.event_tree.clear()
        for event_type in db.list_event_types():
            item = QTreeWidgetItem([event_type["name"], "内置" if event_type["builtin"] else "自定义"])
            item.setData(0, Qt.UserRole, event_type["id"])
            self.event_tree.addTopLevelItem(item)

    # ---------- 交互 ----------

    def _clear_search(self) -> None:
        self.search_edit.clear()
        self.refresh_records()

    def _on_project_selected(self, current, _previous) -> None:
        self._current_project_id = current.data(Qt.UserRole) if current else None
        self.edit_project_button.setEnabled(bool(self._current_project_id))
        self.edit_project_shortcut.setEnabled(bool(self._current_project_id))
        self.refresh_records()
        self.refresh_timeline()
        self._render_stats()

    def _on_event_filter(self) -> None:
        self._current_event_type = self.event_filter.currentData()
        self.refresh_records()
        self.refresh_timeline()

    def _open_timeline_record(self, item: QTreeWidgetItem, _column: int) -> None:
        record_id = item.data(0, Qt.UserRole)
        if record_id:
            self._open_record_dialog(record_id)

    def _selected_record_id(self) -> str | None:
        item = self.record_tree.currentItem()
        return item.data(0, Qt.UserRole) if item else None

    def _edit_project_item(self, item) -> None:
        if item is not None and item.data(Qt.UserRole):
            self.project_list.setCurrentItem(item)
            self.edit_selected_project()

    def _project_context_menu(self, position) -> None:
        item = self.project_list.itemAt(position)
        if item is None or not item.data(Qt.UserRole):
            return
        self.project_list.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction("编辑项目", self.edit_selected_project)
        menu.exec(self.project_list.viewport().mapToGlobal(position))
        menu.deleteLater()

    def edit_selected_project(self) -> None:
        project_id = self._current_project_id
        if not project_id:
            return
        project = db.fetch_one("SELECT * FROM projects WHERE id=? AND trashed_at IS NULL", (project_id,))
        if not project:
            self.refresh_projects()
            return
        dialog = ProjectDialog(project, self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.project_values()
        if all(values[key] == project[key] for key in values):
            return
        try:
            db.update_project(project_id, **values, expected_version=db.row_version(project))
        except db.ConcurrentEditError as exc:
            QMessageBox.warning(self, "项目保存冲突", str(exc))
            return
        self.refresh_projects()
        self.status.showMessage(f"已更新项目 {values['name']}", 5000)

    def new_project(self) -> None:
        name, ok = QInputDialog.getText(self, "新建项目", "项目名称：")
        if not ok or not name.strip():
            return
        project = db.create_project(name)
        self.refresh_projects()
        self._select_project(project["id"])
        self.status.showMessage(f"已创建项目 {project['name']}", 5000)

    def _select_project(self, project_id: str) -> None:
        for index in range(self.project_list.count()):
            if self.project_list.item(index).data(Qt.UserRole) == project_id:
                self.project_list.setCurrentRow(index)
                return

    def new_record(self) -> None:
        if not db.list_projects():
            if QMessageBox.question(self, "需要项目", "还没有任何项目，先创建一个项目？") == QMessageBox.Yes:
                self.new_project()
            if not db.list_projects():
                return
        self._open_record_dialog(None)

    def edit_selected_record(self) -> None:
        record_id = self._selected_record_id()
        if record_id:
            self._open_record_dialog(record_id)
        else:
            self.status.showMessage("先在记录列表中选中一条记录", 5000)

    # 提权目的：新增批量回收方法，把多选记录一次移入回收站，实现批量事件管理。
    def batch_trash_selected(self) -> None:
        """批量回收当前多选的记录（Ctrl/Shift 选择），可从回收站恢复。"""
        record_ids = {item.data(0, Qt.UserRole) for item in self.record_tree.selectedItems()}
        record_ids.discard(None)
        if not record_ids:
            self.status.showMessage("请先用 Ctrl/Shift 选中多条记录", 5000)
            return
        answer = QMessageBox.question(
            self, "批量回收", f"把选中的 {len(record_ids)} 条记录移入回收站？（可随时在回收站恢复）"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for record_id in record_ids:
            db.trash_record(record_id)
        self.refresh_all()
        self.status.showMessage(f"已回收 {len(record_ids)} 条记录", 5000)

    def _open_record_dialog(self, record_id: str | None) -> None:
        dialog = RecordDialog(record_id, parent=self)
        if dialog.exec() and dialog.saved:
            self.refresh_all()

    # ---------- 事件类型管理 ----------

    def _add_event_type(self) -> None:
        name, ok = QInputDialog.getText(self, "添加事件类型", "类型名称：")
        if not ok or not name.strip():
            return
        db.create_event_type(name, "#4C78A8")
        self.refresh_event_filter()
        self._refresh_event_type_page()

    def _rename_event_type(self) -> None:
        item = self.event_tree.currentItem()
        if not item:
            return
        type_id = item.data(0, Qt.UserRole)
        row = db.fetch_one("SELECT builtin, name FROM event_types WHERE id=?", (type_id,))
        if not row or row["builtin"]:
            self.status.showMessage("内置类型不可重命名", 5000)
            return
        name, ok = QInputDialog.getText(self, "重命名事件类型", "新名称：", text=row["name"])
        if ok and name.strip():
            db.rename_event_type(type_id, name)
            self.refresh_event_filter()
            self._refresh_event_type_page()

    def _delete_event_type(self) -> None:
        item = self.event_tree.currentItem()
        if not item:
            return
        type_id = item.data(0, Qt.UserRole)
        if not db.delete_event_type(type_id):
            QMessageBox.warning(self, "无法删除", "该类型被记录引用或为内置类型。")
            return
        self.refresh_event_filter()
        self._refresh_event_type_page()

    # ---------- 统计 ----------

    def _on_tab_changed(self, index: int) -> None:
        if self.tabs.widget(index) is self.stats_page:
            self._render_stats()

    def _ensure_stats_canvas(self) -> None:
        """首次打开统计页时才导入 matplotlib 并创建画布（导入约 5 秒）。"""
        if self.stats_canvas is not None:
            return
        FigureCanvasQTAgg, Figure = _load_matplotlib()
        self.stats_canvas = FigureCanvasQTAgg(Figure(figsize=(8, 4.5), dpi=100))
        self.stats_page.layout().replaceWidget(self.stats_placeholder, self.stats_canvas)
        self.stats_placeholder.deleteLater()
        self.stats_placeholder = None

    def _render_stats(self) -> None:
        if self.stats_canvas is None:
            # 未打开统计页时保持惰性，避免启动阶段导入 matplotlib。
            if self.tabs.currentWidget() is not self.stats_page:
                return
            self._ensure_stats_canvas()
        figure = self.stats_canvas.figure
        figure.clear()
        axis = figure.add_subplot(111)
        mode = self.stats_mode.currentData() if hasattr(self, "stats_mode") else "type"
        if mode == "month":
            rows = db.monthly_counts(self._current_project_id)
            axis.bar([r["label"] or "未知" for r in rows], [r["total"] for r in rows], color="#4C78A8")
            axis.set_title("每月记录数")
        else:
            rows = db.event_type_counts(self._current_project_id)
            axis.bar([r["label"] for r in rows], [r["total"] for r in rows], color=[r["color"] for r in rows])
            axis.set_title("各事件类型记录数")
        colors = palette()
        figure.set_facecolor(colors["surface"]); axis.set_facecolor(colors["surface"])
        axis.title.set_color(colors["ink"])
        axis.tick_params(colors=colors["muted"])
        for spine in axis.spines.values(): spine.set_color(colors["line"])
        axis.set_axisbelow(True); axis.grid(axis="y", color=colors["line"], alpha=.45)
        if not rows:
            axis.text(.5,.5,"暂无统计数据，先创建一条记录",ha="center",va="center",transform=axis.transAxes,color=colors["muted"])
            axis.set_xticks([]); axis.set_yticks([])
        axis.tick_params(axis="x", rotation=30)
        figure.tight_layout()
        self.stats_canvas.draw_idle()

    # ---------- 导出与回收站 ----------

    def export_current_project(self) -> None:
        if not self._current_project_id:
            self.status.showMessage("请先在左侧选择一个项目", 5000)
            return
        projects = {p["id"]: p for p in db.list_projects()}
        project = projects.get(self._current_project_id)
        if not project:
            return
        # full=True 取回全部正文字段；逐条挂上附件清单。
        records = []
        for row in db.list_records(project_id=project["id"], full=True):
            row["attachments"] = db.list_attachments(row["id"])
            records.append(row)
        text = export_service.project_to_markdown(project, records)
        default = export_service.default_export_dir() / (
            f"{safe_filename(project['name'])}_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
        )
        target, _ = QFileDialog.getSaveFileName(self, "导出项目记录", str(default), "Markdown (*.md)")
        if not target:
            return
        Path(target).write_text(text, encoding="utf-8")
        self.status.showMessage(f"已导出 {len(records)} 条记录到 {target}", 8000)

    def open_trash(self) -> None:
        TrashDialog(self).exec()
        self.refresh_all()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._auto_copies()
        super().closeEvent(event)

    def _auto_copies(self) -> None:
        """退出时自动备份数据库并导出人类可读快照；任何失败都不阻塞退出。"""
        try:
            from ..services import backup, snapshot

            if not backup.auto_enabled():
                return
            backup.create_backup_if_due(min_minutes=30, note="退出时自动备份")
            snapshot.export_snapshot(note="退出时自动快照")
        except Exception as exc:  # noqa: BLE001
            self.status.showMessage(f"自动备份失败：{exc}", 8000)


# 提权目的：新增可调高度输入框组件（ResizableBox/SizeGrip），仿窗口边框拖拽调整每个文本框高度并记忆设置。
class ResizableBox(QWidget):
    """带下沿拖把的多行输入框容器：拖动抓手调整输入框高度，双击恢复默认，高度持久化。

    fill=True 时（如"补充记录"）高度为最小值，随窗口拉伸继续变大；
    默认为固定高度模式（结构化字段）。
    """

    def __init__(
        self,
        edit: "ToolTextEdit",
        settings_key: str,
        default_height: int = 64,
        min_height: int = 40,
        max_height: int = 2000,
        fill: bool = False,
    ) -> None:
        super().__init__()
        self.edit = edit
        self.settings_key = settings_key
        self.default_height = default_height
        self.min_height = min_height
        self.max_height = max_height
        self.fill = fill
        self.height_value = default_height
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(edit)
        self.grip = SizeGrip(self)
        layout.addWidget(self.grip)
        saved = db.get_setting(settings_key)
        self.set_edit_height(int(saved) if saved and saved.isdigit() else default_height)

    def set_edit_height(self, height: int) -> None:
        height = max(self.min_height, min(self.max_height, int(height)))
        self.height_value = height
        if self.fill:
            self.edit.setMinimumHeight(height)
        else:
            self.edit.setFixedHeight(height)
        self.edit.resize(self.edit.width(), height)

    def persist_height(self) -> None:
        db.set_setting(self.settings_key, str(self.height_value))


class SizeGrip(QWidget):
    """输入框底部的拖拽边：悬停高亮、上下箭头光标、按住拖动改高度、双击恢复默认。"""

    def __init__(self, box: ResizableBox) -> None:
        super().__init__(box)
        self.box = box
        self.setFixedHeight(7)
        self.setCursor(Qt.CursorShape.SplitVCursor)
        self.setToolTip("拖动调整输入框高度；双击恢复默认")
        self._hover = False
        self._pressed = False
        self._start_global_y = 0.0
        self._start_height = 0

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        color = QColor("#1F4E79") if (self._hover or self._pressed) else QColor("#B6C2D2")
        painter.setPen(QPen(color, 2))
        center_x = self.width() / 2
        center_y = self.height() / 2
        for offset in (-24, 0, 24):
            painter.drawLine(QPointF(center_x + offset - 8, center_y), QPointF(center_x + offset + 8, center_y))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self._start_global_y = event.globalPosition().y()
            self._start_height = self.box.height_value
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._pressed:
            delta = round(event.globalPosition().y() - self._start_global_y)
            self.box.set_edit_height(self._start_height + delta)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._pressed:
            self._pressed = False
            self.box.persist_height()
            event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.box.set_edit_height(self.box.default_height)
        self.box.persist_height()
        event.accept()


# 提权目的：新增附件拖拽列表组件，实现把文件直接拖入附件区导入的功能。
class AttachmentDropList(QListWidget):
    """支持把本地文件拖入列表导入附件；仅接受包含本地文件的拖放。"""

    def __init__(self, on_files_dropped, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_files_dropped = on_files_dropped
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setIconSize(QSize(20, 20))

    def _local_files(self, mime) -> list[Path] | None:
        if not mime.hasUrls():
            return None
        files = [Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]
        files = [path for path in files if path.is_file()]
        return files or None

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._local_files(event.mimeData()) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self._local_files(event.mimeData()) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802
        files = self._local_files(event.mimeData())
        if files is not None:
            event.acceptProposedAction()
            self._on_files_dropped(files)
        else:
            event.ignore()


class RecordDialog(QDialog):
    def __init__(self, record_id: str | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.record_id = record_id
        self.saved = False
        self.setWindowTitle("编辑记录" if record_id else "新建记录")
        area = QApplication.primaryScreen().availableGeometry() if QApplication.primaryScreen() else None
        self.resize(min(1050, area.width()-70) if area else 1050, min(820, area.height()-80) if area else 760)
        self.recognition_stack = QUndoStack(self)
        self._build()
        self._load()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22,18,22,16); outer.setSpacing(12)
        heading = QHBoxLayout()
        title = QLabel("记录实验，留下完整的思考"); title.setObjectName("pageTitle"); heading.addWidget(title); heading.addStretch()
        outer.addLayout(heading)
        assist = QHBoxLayout()
        self.recognize_button = QPushButton("自动识别…"); self.recognize_button.setProperty("primary",True)
        self.recognize_button.clicked.connect(self._open_recognition); assist.addWidget(self.recognize_button)
        self.recognition_undo = QPushButton("撤销识别填入"); self.recognition_undo.setEnabled(False)
        self.recognition_undo.clicked.connect(self._undo_recognition); assist.addWidget(self.recognition_undo)
        self.recognition_redo = QPushButton("重做识别填入"); self.recognition_redo.setEnabled(False)
        self.recognition_redo.clicked.connect(self._redo_recognition); assist.addWidget(self.recognition_redo); assist.addStretch()
        outer.addLayout(assist)
        self.recognition_hint = QLabel("在线识别会先展示发送预览；填入后仍需你保存记录。")
        self.recognition_hint.setWordWrap(True); self.recognition_hint.setObjectName("helper"); outer.addWidget(self.recognition_hint)
        self.recognition_stack.canUndoChanged.connect(self.recognition_undo.setEnabled)
        self.recognition_stack.canRedoChanged.connect(self.recognition_redo.setEnabled)
        QShortcut(QKeySequence("Ctrl+Alt+Z"), self, activated=self._undo_recognition)
        QShortcut(QKeySequence("Ctrl+Alt+Y"), self, activated=self._redo_recognition)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        body = QWidget(); body.setObjectName("recordBody"); layout = QVBoxLayout(body); layout.setContentsMargins(8,4,14,8); layout.setSpacing(12)
        scroll.setWidget(body); outer.addWidget(scroll,1)
        section = QLabel("基本信息与结构化内容"); section.setObjectName("sectionTitle"); layout.addWidget(section)
        form = QFormLayout(); form.setVerticalSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.title_edit = QLineEdit()
        form.addRow("标题", self.title_edit)
        self.project_combo = QComboBox()
        for project in db.list_projects():
            self.project_combo.addItem(project["name"], project["id"])
        form.addRow("所属项目", self.project_combo)
        self.type_combo = QComboBox()
        for event_type in db.list_event_types():
            self.type_combo.addItem(event_type["name"], event_type["id"])
        form.addRow("事件类型", self.type_combo)
        self.occurred_edit = QDateTimeEdit(QDateTime.currentDateTime())
        self.occurred_edit.setDisplayFormat(OCCURRED_FORMAT)
        self.occurred_edit.setCalendarPopup(True)
        form.addRow("发生时间", self.occurred_edit)
        span_row = QHBoxLayout()
        self.has_span = QCheckBox("记录起止时间")
        self.has_span.toggled.connect(self._toggle_span)
        self.start_edit = QDateTimeEdit(QDateTime.currentDateTime())
        self.end_edit = QDateTimeEdit(QDateTime.currentDateTime())
        for edit in (self.start_edit, self.end_edit):
            edit.setDisplayFormat(OCCURRED_FORMAT)
            edit.setCalendarPopup(True)
            edit.setEnabled(False)
        span_row.addWidget(self.has_span)
        span_row.addWidget(self.start_edit)
        span_row.addWidget(self.end_edit)
        form.addRow("耗时", span_row)
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("逗号分隔，如：热失控, 能量密度")
        form.addRow("标签", self.tags_edit)
        layout.addLayout(form)

        # 提权目的：结构化字段接入可调高度容器（ResizableBox），每个文本框底部有拖拽边。
        self.field_edits: dict[str, ToolTextEdit] = {}
        self.field_boxes: dict[str, ResizableBox] = {}
        for field, label in RECORD_FIELDS:
            edit = ToolTextEdit()
            self.field_edits[field] = edit
            box = ResizableBox(edit, f"edit_height.{field}", default_height=64)
            self.field_boxes[field] = box
            form.addRow(label, box)

        tools_row = QHBoxLayout()
        self.sup_btn = QPushButton("上标 x²")
        self.sup_btn.setFocusPolicy(Qt.NoFocus)
        self.sup_btn.clicked.connect(lambda: self._convert_selection("sup"))
        self.sub_btn = QPushButton("下标 x₂")
        self.sub_btn.setFocusPolicy(Qt.NoFocus)
        self.sub_btn.clicked.connect(lambda: self._convert_selection("sub"))
        symbol_btn = QPushButton("符号面板…")
        symbol_btn.setFocusPolicy(Qt.NoFocus)
        symbol_btn.clicked.connect(self._open_symbols)
        formula_btn = QPushButton("公式编辑器…")
        formula_btn.setFocusPolicy(Qt.NoFocus)
        formula_btn.clicked.connect(self._open_formula)
        self.mode_label = QLabel("先选中文字，再点上标/下标按钮转换（数字与数学符号）")
        for widget in (self.sup_btn, self.sub_btn, symbol_btn, formula_btn):
            tools_row.addWidget(widget)
        tools_row.addStretch(1)
        layout.addLayout(tools_row)
        self.mode_label.setWordWrap(True)
        layout.addWidget(self.mode_label)

        # 提权目的：补充记录框同样接入可调高度容器（最小高度模式，随窗口拉伸填充）。
        self.content_edit = ToolTextEdit()
        self.content_edit.setPlaceholderText("自由补充（Markdown）；公式用公式编辑器插入为 $…$ / $$…$$")
        layout.addWidget(QLabel("补充记录（Markdown）"))
        self.content_box = ResizableBox(
            self.content_edit, "edit_height.content", default_height=180, fill=True
        )
        layout.addWidget(self.content_box, 1)

        self.sensitive_label = QLabel("")
        self.sensitive_label.setWordWrap(True)
        layout.addWidget(self.sensitive_label)

        # 提权目的：重构附件区——按钮行 + 拖拽提示在上，下方为"文件名+格式图标"附件总览列表，并挂接图片自动保存回调。
        attach_bar = QHBoxLayout()
        add_btn = QPushButton("添加附件")
        add_btn.clicked.connect(self._add_attachment)
        open_btn = QPushButton("打开附件目录")
        open_btn.clicked.connect(self._open_attachment_dir)
        self.attach_hint = QLabel("支持把文件直接拖入下方列表导入；正文支持拖入/粘贴图片")
        attach_bar.addWidget(add_btn)
        attach_bar.addWidget(open_btn)
        attach_bar.addSpacing(16)
        self.attach_hint.setWordWrap(True)
        attach_bar.addStretch(1)
        layout.addLayout(attach_bar)
        layout.addWidget(self.attach_hint)
        self.attachment_list = AttachmentDropList(self._add_attachment_files, self)
        self.attachment_list.setMinimumHeight(100)
        self.attachment_list.setMaximumHeight(180)
        layout.addWidget(self.attachment_list, 1)

        for edit in [*self.field_edits.values(), self.content_edit]:
            edit.attachment_file_saver = self._save_attachment_file
            edit.attachment_image_saver = self._save_clipboard_image

        bottom = QHBoxLayout()
        revision_btn = QPushButton("修订历史…")
        revision_btn.clicked.connect(self._open_revisions)
        bottom.addWidget(revision_btn)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存记录")
        buttons.button(QDialogButtonBox.Save).setProperty("primary", True)
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)
        outer.addLayout(bottom)

    def _open_recognition(self) -> None:
        dialog = RecognitionDialog(self, db.get_setting("recognition.database_path", ""))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._apply_recognition(dialog.choices)

    def _apply_recognition(self, choices) -> bool:
        command = FillCommand(self, choices)
        if not command.before:
            self.recognition_hint.setText("没有可填入的区块。已有内容默认保留，可在预览中选择追加或替换。")
            return False
        self.recognition_stack.push(command)
        self.recognition_hint.setText(f"已填入 {len(command.before)} 个区块；尚未保存。可撤销/重做本次识别填入。")
        return True

    def _undo_recognition(self) -> None:
        if not self.recognition_stack.canUndo(): return
        command = self.recognition_stack.command(self.recognition_stack.index()-1)
        if not command.matches(command.after):
            QMessageBox.information(self, "保留手工修改", "识别填入后部分内容已被手工编辑。为保留新输入，本次未撤销；可先在相应编辑框撤销手工编辑，再重试。")
            return
        self.recognition_stack.undo()
        self.recognition_hint.setText("已撤销整次识别填入；没有请求模型或修改数据库。")

    def _redo_recognition(self) -> None:
        if not self.recognition_stack.canRedo(): return
        command = self.recognition_stack.command(self.recognition_stack.index())
        if not command.matches(command.before):
            QMessageBox.information(self, "保留手工修改", "撤销后内容已被编辑，本次未重做，以免覆盖手工修改。")
            return
        self.recognition_stack.redo()
        self.recognition_hint.setText("已重做识别填入；尚未保存记录。")

    def _toggle_span(self, checked: bool) -> None:
        self.start_edit.setEnabled(checked)
        self.end_edit.setEnabled(checked)

    # ---------- 文本工具（上下标 / 符号 / 公式） ----------

    def _current_tool_edit(self) -> ToolTextEdit:
        widget = QApplication.focusWidget()
        if isinstance(widget, ToolTextEdit):
            return widget
        return self.content_edit

    def _convert_selection(self, mode: str) -> None:
        edit = self._current_tool_edit()
        verb = "上标" if mode == "sup" else "下标"
        if edit.convert_selection(mode):
            self.mode_label.setText(f"已把选中的数字与数学符号转换为{verb}")
        else:
            self.mode_label.setText(f"请先用鼠标选中要{verb}化的内容，再点击按钮")

    def _open_symbols(self) -> None:
        SymbolPaletteDialog(self._current_tool_edit(), self).exec()

    def _open_formula(self) -> None:
        FormulaDialog(self._current_tool_edit(), self).exec()

    def _load(self) -> None:
        self._loaded_version = None
        self._refresh_attachments()
        if not self.record_id:
            return
        record = db.get_record(self.record_id)
        if not record:
            return
        self._loaded_version = db.row_version(record)
        self.title_edit.setText(record["title"])
        index = self.project_combo.findData(record["project_id"])
        self.project_combo.setCurrentIndex(index if index >= 0 else 0)
        index = self.type_combo.findData(record["event_type_id"])
        self.type_combo.setCurrentIndex(index if index >= 0 else 0)
        dt = QDateTime.fromString(record["occurred_at"], OCCURRED_FORMAT)
        if dt.isValid():
            self.occurred_edit.setDateTime(dt)
        if record.get("started_at") and record.get("ended_at"):
            self.has_span.setChecked(True)
            self.start_edit.setDateTime(QDateTime.fromString(record["started_at"], OCCURRED_FORMAT))
            self.end_edit.setDateTime(QDateTime.fromString(record["ended_at"], OCCURRED_FORMAT))
        # 提权目的：正文改为 Markdown 装载并注册图片资源，实现拖入图片后重新打开仍直接显示为图片。
        self.tags_edit.setText("、".join(record["tags"]))
        attachments = db.list_attachments(self.record_id)
        for field, _label in RECORD_FIELDS:
            self.field_edits[field].load_markdown(record.get(field) or "", attachments)
        self.content_edit.load_markdown(record.get("content") or "", attachments)

    def _refresh_attachments(self) -> None:
        """附件总览：仅显示文件名与格式图标，详细信息放悬停提示。"""
        self.attachment_list.clear()
        if not self.record_id:
            return
        provider = QFileIconProvider()
        for attachment in db.list_attachments(self.record_id):
            icon = provider.icon(QFileInfo(attachment["local_path"]))
            item = QListWidgetItem(icon, attachment["filename"])
            item.setToolTip(
                f"{attachment['filename']}\n{attachment['size_bytes']} bytes\n"
                f"SHA-256 {attachment['sha256'][:16]}…"
            )
            self.attachment_list.addItem(item)

    # 提权目的：新增附件拖拽/批量导入与图片自动保存方法，支撑拖拽导入附件与拖图入文功能。
    def _ensure_saved_for_attachment(self) -> bool:
        """拖入/添加附件前确保记录已保存（未保存时尝试静默保存，需至少有标题）。"""
        if self.record_id:
            return True
        payload = self._collect_payload()
        if payload is None:
            QMessageBox.warning(
                self, "请先填写标题", "拖入或添加附件前请至少填写标题，系统会自动保存记录。"
            )
            return False
        return self._persist(payload)

    def _add_attachment(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择附件")
        if path:
            self._add_attachment_files([Path(path)])

    def _add_attachment_files(self, paths: list[Path]) -> None:
        """拖拽/文件对话框导入附件的统一入口；逐个签名校验，失败项汇总提示。"""
        if not self._ensure_saved_for_attachment():
            return
        added, errors = 0, []
        for path in paths:
            try:
                db.add_attachment(self.record_id, path)
                added += 1
            except ValueError as exc:
                errors.append(f"{path.name}：{exc}")
        self._refresh_attachments()
        if errors:
            QMessageBox.warning(self, "部分附件被拒绝", "\n".join(errors))
        elif added:
            self.attach_hint.setText(f"已导入 {added} 个附件")

    def _save_attachment_file(self, path: Path) -> dict | None:
        """正文拖入图片文件：存为附件并返回记录（供内嵌显示）。"""
        if not self._ensure_saved_for_attachment():
            return None
        try:
            attachment = db.add_attachment(self.record_id, path)
        except ValueError as exc:
            QMessageBox.warning(self, "附件被拒绝", str(exc))
            return None
        self._refresh_attachments()
        self.attach_hint.setText(f"已插入图片 {attachment['filename']}")
        return attachment

    def _save_clipboard_image(self, image: QImage, name: str) -> dict | None:
        """正文粘贴截图：把 QImage 编码为 PNG 存为附件。"""
        if not self._ensure_saved_for_attachment():
            return None
        buffer = QBuffer()
        buffer.open(QBuffer.OpenModeFlag.ReadWrite)
        image.save(buffer, "PNG")
        descriptor, temp_name = tempfile.mkstemp(suffix=".png")
        os.close(descriptor)
        temp_path = Path(temp_name)
        try:
            temp_path.write_bytes(bytes(buffer.data()))
            attachment = db.add_attachment(self.record_id, temp_path, display_name=name)
        except ValueError as exc:
            QMessageBox.warning(self, "附件被拒绝", str(exc))
            return None
        finally:
            temp_path.unlink(missing_ok=True)
        self._refresh_attachments()
        self.attach_hint.setText(f"已插入图片 {attachment['filename']}")
        return attachment

    def _open_attachment_dir(self) -> None:
        if not self.record_id:
            return
        os.startfile(str(db.attachment_dir(self.record_id)))  # noqa: S606

    def _open_revisions(self) -> None:
        if not self.record_id:
            return
        if RevisionDialog(self.record_id, self, self._loaded_version).exec():
            self._load()

    # 提权目的：拆分保存逻辑（收集/持久化/确认），使拖入图片附件时可静默保存记录，用户无感。
    def _collect_payload(self) -> dict | None:
        title = self.title_edit.text().strip()
        if not title:
            QMessageBox.warning(self, "缺少标题", "记录必须包含标题。")
            return None
        if self.project_combo.currentData() is None:
            QMessageBox.warning(self, "缺少项目", "请先创建并选择项目。")
            return None
        payload = {
            "project_id": self.project_combo.currentData(),
            "event_type_id": self.type_combo.currentData(),
            "title": title,
            "occurred_at": self.occurred_edit.dateTime().toString(OCCURRED_FORMAT),
            "started_at": self.start_edit.dateTime().toString(OCCURRED_FORMAT) if self.has_span.isChecked() else None,
            "ended_at": self.end_edit.dateTime().toString(OCCURRED_FORMAT) if self.has_span.isChecked() else None,
            "tags": [tag.strip() for tag in self.tags_edit.text().replace("，", ",").split(",") if tag.strip()],
        }
        for field, _label in RECORD_FIELDS:
            payload[field] = self.field_edits[field].toMarkdown()
        payload["content"] = self.content_edit.toMarkdown()
        return payload

    def _persist(self, payload: dict) -> bool:
        receipt = {}
        try:
            self.record_id = db.save_record(payload, record_id=self.record_id, expected_version=getattr(self, "_loaded_version", None), version_receipt=receipt)
        except (db.ConcurrentEditError, ValueError) as exc:
            QMessageBox.warning(self, "记录未保存", str(exc))
            return False
        self._loaded_version = receipt["version"]
        return True

    def _save(self) -> None:
        payload = self._collect_payload()
        if payload is None:
            return
        if not self._persist(payload):
            return
        self.saved = True
        self.accept()


class RevisionDialog(QDialog):
    def __init__(self, record_id: str, parent: QWidget | None = None, expected_version: str | None = None) -> None:
        super().__init__(parent)
        self.record_id = record_id
        self.expected_version = expected_version
        self.setWindowTitle("修订历史")
        self.resize(720, 480)
        layout = QVBoxLayout(self)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["时间（UTC）", "原因", "标题"])
        layout.addWidget(self.tree, 1)
        buttons = QHBoxLayout()
        restore_btn = QPushButton("恢复到选中修订")
        restore_btn.clicked.connect(self._restore)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(restore_btn)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        for revision in db.list_revisions(record_id):
            snapshot = revision["snapshot"]
            item = QTreeWidgetItem([revision["created_at"], revision["reason"], snapshot.get("title", "")])
            item.setData(0, Qt.UserRole, revision["id"])
            self.tree.addTopLevelItem(item)

    def _restore(self) -> None:
        item = self.tree.currentItem()
        if not item:
            return
        if QMessageBox.question(self, "恢复修订", "恢复前会先保存当前内容为新修订，确定继续？") == QMessageBox.Yes:
            try:
                db.restore_revision(item.data(0, Qt.UserRole), expected_version=self.expected_version)
            except (db.ConcurrentEditError, ValueError) as exc:
                QMessageBox.warning(self, "恢复失败", str(exc))
                return
            self.accept()


class TrashDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("回收站")
        self.resize(720, 480)
        layout = QVBoxLayout(self)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["类型", "名称", "删除时间（UTC）"])
        layout.addWidget(self.tree, 1)
        buttons = QHBoxLayout()
        restore_btn = QPushButton("恢复选中")
        restore_btn.clicked.connect(self._restore)
        purge_btn = QPushButton("彻底删除选中")
        purge_btn.clicked.connect(self._purge)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(restore_btn)
        buttons.addWidget(purge_btn)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        for project in db.list_projects(include_trashed=True):
            item = QTreeWidgetItem(["项目", project["name"], project["trashed_at"] or ""])
            item.setData(0, Qt.UserRole, ("project", project["id"]))
            self.tree.addTopLevelItem(item)
        for record in db.list_records(include_trashed=True):
            item = QTreeWidgetItem(["记录", record["title"], record["updated_at"]])
            item.setData(0, Qt.UserRole, ("record", record["id"]))
            self.tree.addTopLevelItem(item)

    def _selected(self) -> tuple[str, str] | None:
        item = self.tree.currentItem()
        return item.data(0, Qt.UserRole) if item else None

    def _restore(self) -> None:
        selected = self._selected()
        if not selected:
            return
        kind, target_id = selected
        (db.restore_project if kind == "project" else db.restore_record)(target_id)
        self._reload()

    def _purge(self) -> None:
        selected = self._selected()
        if not selected:
            return
        kind, target_id = selected
        if QMessageBox.question(self, "彻底删除", "彻底删除后不可恢复，确定？") == QMessageBox.Yes:
            (db.purge_project if kind == "project" else db.purge_record)(target_id)
            self._reload()

    def _reload(self) -> None:
        self.tree.clear()
        for project in db.list_projects(include_trashed=True):
            item = QTreeWidgetItem(["项目", project["name"], project["trashed_at"] or ""])
            item.setData(0, Qt.UserRole, ("project", project["id"]))
            self.tree.addTopLevelItem(item)
        for record in db.list_records(include_trashed=True):
            item = QTreeWidgetItem(["记录", record["title"], record["updated_at"]])
            item.setData(0, Qt.UserRole, ("record", record["id"]))
            self.tree.addTopLevelItem(item)


def _close_splash() -> None:
    """打包版若带 PyInstaller 启动画面，主窗口就绪后关闭它；源码运行时该模块不存在。"""
    try:
        import pyi_splash  # type: ignore

        pyi_splash.close()
    except Exception:
        pass


def run() -> None:
    import sys

    from PySide6.QtWidgets import QApplication, QMessageBox

    from ..services import backup

    # 启动前先做完整性检查：损坏时不覆盖原文件，改为复制最近备份成新库。
    status, detail = db.integrity_status()
    recovered: Path | None = None
    if status == "corrupt":
        recovered = backup.recover_to_new_db()
        if recovered is not None:
            db.set_db_path(recovered)

    db.init_db()
    app = QApplication(sys.argv)
    app.setApplicationName("实验记录")
    apply_theme(db.get_setting("theme", THEME_SCIENTIFIC))
    window = MainWindow()
    window.show()
    _close_splash()
    if status == "corrupt":
        if recovered is not None:
            QMessageBox.warning(
                window,
                "数据库已损坏",
                f"检测到数据库损坏，已改用最近一份备份的副本继续运行：\n{recovered}\n\n原文件保留在原处，未删除。",
            )
        else:
            QMessageBox.critical(
                window,
                "数据库已损坏",
                f"数据库损坏且没有可用备份：\n{detail}\n\n原文件保留在原处，未删除。",
            )
    try:
        if backup.auto_enabled():
            backup.create_backup_if_due(min_minutes=30, note="启动时自动备份")
    except Exception:  # noqa: BLE001 - 备份失败不应阻止使用
        pass
    sys.exit(app.exec())
