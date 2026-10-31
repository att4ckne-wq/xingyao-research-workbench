# 提权目的：重写 text_tools.py，把文本框升级为支持拖入/粘贴图片并内嵌显示的 Markdown 编辑器，实现用户要求的图片直插正文功能。
"""实验记录 - 文本编辑工具组件：Markdown 编辑 + 内嵌图片、选中角标化、符号面板、公式编辑器。

图片内嵌方案：拖入/粘贴的图片自动保存为记录附件，正文中以 `![图片](att://<附件ID>)`
引用并直接渲染显示；Markdown 存储，导出时把 att:// 链接还原为附件文件路径。
"""

# 提权目的：更新导入与常量（尺寸解析正则、自动缩放宽度和光标类），支撑图片调整大小功能。
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QImage, QTextCursor, QTextDocument, QTextImageFormat
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..services.symbols import SYMBOL_TABS, convert_to_subscript, convert_to_superscript

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}

# 图片引用名：att://<附件ID> 或带尺寸 att://<附件ID>#<宽>x<高>（尺寸随 Markdown 持久化）
ATT_NAME_RE = re.compile(r"^att://([0-9a-fA-F]+)(?:#(\d+)x(\d+))?$")
# 拖入/粘贴的超大图自动缩放到该宽度（可右键"重置为原始大小"还原）
AUTO_FIT_WIDTH = 640


class ToolTextEdit(QTextEdit):
    """QTextEdit 扩展：
    - 选中内容角标化（数字与数学符号）；
    - 拖入/粘贴图片自动存为记录附件，并在光标处内嵌显示；
    - 内容以 Markdown 读写（load_markdown / toMarkdown）。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.attachment_file_saver: Callable[[Path], dict | None] | None = None
        self.attachment_image_saver: Callable[[QImage, str], dict | None] | None = None

    # ---------- 角标化 ----------

    def convert_selection(self, mode: str) -> bool:
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return False
        converter = convert_to_superscript if mode == "sup" else convert_to_subscript
        cursor.insertText(converter(cursor.selectedText(), letters=False))
        return True

    # ---------- 图片拖入 / 粘贴 ----------

    def _mime_images(self, source) -> list:
        """从 MIME 数据中提取可插入的图片项：(file, Path, 名称) 或 (image, QImage, 名称)。"""
        items: list = []
        if source.hasUrls():
            for url in source.urls():
                if url.isLocalFile():
                    path = Path(url.toLocalFile())
                    if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
                        items.append(("file", path, path.name))
        if source.hasImage():
            image = source.imageData()
            if isinstance(image, QImage) and not image.isNull():
                name = f"粘贴图片_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                items.append(("image", image, name))
        return items

    def _insert_images_from_mime(self, source) -> bool:
        """把 MIME 中的图片存为附件并插入光标处；返回 True 表示已拦截该次粘贴/拖放。"""
        items = self._mime_images(source)
        if not items:
            return False
        handled = False
        for kind, value, name in items:
            attachment = None
            image = value if kind == "image" else None
            if kind == "file" and self.attachment_file_saver is not None:
                attachment = self.attachment_file_saver(value)
                if attachment:
                    image = QImage(str(attachment["local_path"]))
            elif kind == "image" and self.attachment_image_saver is not None:
                attachment = self.attachment_image_saver(value, name)
            if attachment and image is not None and not image.isNull():
                self._insert_attachment_image(attachment, image)
                handled = True
        # 有图片数据但保存失败（如未保存记录）时也拦截，避免把文件路径当文本插入
        return handled or bool(items)

    # 提权目的：插入图片时按自动缩放宽度计算尺寸并写入引用名，超大图不再撑爆正文。
    def _insert_attachment_image(self, attachment: dict, image: QImage) -> None:
        base = f"att://{attachment['id']}"
        width = height = None
        if image.width() > AUTO_FIT_WIDTH:
            width = AUTO_FIT_WIDTH
            height = max(1, round(image.height() * AUTO_FIT_WIDTH / image.width()))
        name = f"{base}#{width}x{height}" if width else base
        self.document().addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), image)
        fmt = QTextImageFormat()
        fmt.setName(name)
        if width:
            fmt.setWidth(width)
            fmt.setHeight(height)
        self.textCursor().insertImage(fmt)

    # ---------- 图片尺寸调整 ----------

    # 提权目的：修复 charFormat 需显式转换为 QTextImageFormat 才能访问 name/width 的问题。
    def image_formats(self) -> list[tuple[int, QTextImageFormat]]:
        """返回文档中全部图片的 (字符位置, 图片格式)。"""
        result: list[tuple[int, QTextImageFormat]] = []
        document = self.document()
        block = document.firstBlock()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                fmt = fragment.charFormat()
                if fmt.isImageFormat():
                    result.append((fragment.position(), QTextImageFormat(fmt)))
                iterator += 1
            block = block.next()
        return result

    def _image_resource(self, name: str) -> QImage | None:
        match = ATT_NAME_RE.match(name)
        candidates = [name] + ([f"att://{match.group(1)}"] if match else [])
        for candidate in candidates:
            image = self.document().resource(QTextDocument.ResourceType.ImageResource, QUrl(candidate))
            if isinstance(image, QImage) and not image.isNull():
                return image
        return None

    def _apply_image_size(self, position: int, old_fmt: QTextImageFormat, width: int | None, height: int | None) -> None:
        """把 position 处的图片改为指定显示尺寸；尺寸写入引用名以随 Markdown 持久化。"""
        match = ATT_NAME_RE.match(old_fmt.name())
        if not match:
            return
        document = self.document()
        base = f"att://{match.group(1)}"
        image = self._image_resource(old_fmt.name())
        new_name = f"{base}#{width}x{height}" if width and height else base
        if image is not None:
            document.addResource(QTextDocument.ResourceType.ImageResource, QUrl(new_name), image)
        new_fmt = QTextImageFormat()
        new_fmt.setName(new_name)
        if width and height:
            new_fmt.setWidth(width)
            new_fmt.setHeight(height)
        cursor = QTextCursor(document)
        cursor.setPosition(position)
        cursor.setPosition(position + 1, QTextCursor.MoveMode.KeepAnchor)
        cursor.setCharFormat(new_fmt)
        document.markContentsDirty(0, document.characterCount())

    def resize_image_at(self, position: int) -> None:
        """打开尺寸对话框调整 position 处的图片。"""
        for image_position, fmt in self.image_formats():
            if image_position != position:
                continue
            image = self._image_resource(fmt.name())
            match = ATT_NAME_RE.match(fmt.name())
            if image is not None:
                natural = (image.width(), image.height())
            else:
                natural = (int(fmt.width()) or 1, int(fmt.height()) or 1)
            current = (int(fmt.width()) or natural[0], int(fmt.height()) or natural[1])
            dialog = ImageResizeDialog(natural, current, self)
            if dialog.exec():
                if dialog.reset_requested:
                    self._apply_image_size(position, fmt, None, None)
                else:
                    self._apply_image_size(position, fmt, dialog.chosen_width, dialog.chosen_height)
            return

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        """右键菜单：光标落在图片上时提供调整大小/重置原始尺寸。"""
        menu = self.createStandardContextMenu()
        cursor = self.cursorForPosition(event.pos())
        target = None
        for image_position, fmt in self.image_formats():
            if image_position in (cursor.position() - 1, cursor.position()):
                target = (image_position, fmt)
                break
        if target is not None:
            position, fmt = target
            match = ATT_NAME_RE.match(fmt.name())
            menu.addSeparator()
            action_resize = menu.addAction("调整图片大小…")
            action_resize.triggered.connect(lambda _=False: self.resize_image_at(position))
            if match and match.group(2):
                action_reset = menu.addAction("重置为原始大小")
                action_reset.triggered.connect(
                    lambda _=False: self._apply_image_size(position, fmt, None, None)
                )
        menu.exec(event.globalPos())

    # ---------- Qt 事件 ----------

    def insertFromMimeData(self, source) -> None:  # noqa: N802 (Qt 命名)
        if self._insert_images_from_mime(source):
            return
        super().insertFromMimeData(source)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._mime_images(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self._mime_images(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        if self._insert_images_from_mime(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    # ---------- Markdown 装载 ----------

    # 提权目的：load_markdown 解析引用名中的尺寸参数并注册对应资源，重新打开时恢复用户设置的图片大小。
    def load_markdown(self, markdown: str, attachments: list[dict]) -> None:
        """载入 Markdown 并为 att:// 引用的图片注册资源，实现内嵌显示与尺寸恢复。"""
        self.setMarkdown(markdown)
        document = self.document()
        images_by_id: dict[str, QImage] = {}
        for attachment in attachments:
            if str(attachment.get("media_type", "")).startswith("image/"):
                path = Path(attachment["local_path"])
                if path.exists():
                    image = QImage(str(path))
                    if not image.isNull():
                        images_by_id[attachment["id"]] = image
                        document.addResource(
                            QTextDocument.ResourceType.ImageResource,
                            QUrl(f"att://{attachment['id']}"),
                            image,
                        )
        # 提权目的：load_markdown 中同样显式转换为 QTextImageFormat，修复尺寸参数应用的类型错误。
        if images_by_id:
            block = document.firstBlock()
            while block.isValid():
                iterator = block.begin()
                while not iterator.atEnd():
                    fragment = iterator.fragment()
                    char_fmt = fragment.charFormat()
                    if char_fmt.isImageFormat():
                        fmt = QTextImageFormat(char_fmt)
                        match = ATT_NAME_RE.match(fmt.name())
                        if match and match.group(1) in images_by_id:
                            image = images_by_id[match.group(1)]
                            if match.group(2):
                                width, height = int(match.group(2)), int(match.group(3))
                                fmt.setWidth(width)
                                fmt.setHeight(height)
                                document.addResource(
                                    QTextDocument.ResourceType.ImageResource, QUrl(fmt.name()), image
                                )
                                cursor = QTextCursor(document)
                                cursor.setPosition(fragment.position())
                                cursor.setPosition(fragment.position() + 1, QTextCursor.MoveMode.KeepAnchor)
                                cursor.setCharFormat(fmt)
                            else:
                                document.addResource(
                                    QTextDocument.ResourceType.ImageResource, QUrl(fmt.name()), image
                                )
                    iterator += 1
                block = block.next()
        document.markContentsDirty(0, document.characterCount())


# 提权目的：新增图片尺寸调整对话框（宽度输入、保持纵横比、重置原始尺寸），实现用户要求的图片调整大小功能。
class ImageResizeDialog(QDialog):
    """图片尺寸对话框：输入新宽度（默认保持纵横比），或一键重置为原始尺寸。"""

    def __init__(self, natural: tuple[int, int], current: tuple[int, int], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("调整图片大小")
        self.reset_requested = False
        self.chosen_width = 0
        self.chosen_height = 0
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"原始尺寸：{natural[0]} × {natural[1]} px　当前：{current[0]} × {current[1]} px"))

        form = QFormLayout()
        self.width_spin = QSpinBox()
        self.width_spin.setRange(16, 8192)
        self.width_spin.setValue(current[0])
        form.addRow("新宽度（px）", self.width_spin)
        self.keep_ratio = QCheckBox("保持纵横比")
        self.keep_ratio.setChecked(True)
        form.addRow("", self.keep_ratio)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(16, 8192)
        self.height_spin.setValue(current[1])
        self.height_spin.setEnabled(False)
        form.addRow("新高度（px）", self.height_spin)
        layout.addLayout(form)

        buttons_row = QHBoxLayout()
        reset_btn = QPushButton("重置为原始大小")
        reset_btn.clicked.connect(self._reset)
        buttons_row.addWidget(reset_btn)
        buttons_row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("应用")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        buttons_row.addWidget(buttons)
        layout.addLayout(buttons_row)

        self._ratio = (natural[1] / natural[0]) if natural[0] else 1.0
        self.width_spin.valueChanged.connect(self._sync_height)
        self.keep_ratio.toggled.connect(self._toggle_ratio)

    def _toggle_ratio(self, checked: bool) -> None:
        self.height_spin.setEnabled(not checked)
        if checked:
            self._sync_height(self.width_spin.value())

    def _sync_height(self, width: int) -> None:
        if self.keep_ratio.isChecked():
            self.height_spin.setValue(max(16, round(width * self._ratio)))

    def _reset(self) -> None:
        self.reset_requested = True
        self.accept()

    def _apply(self) -> None:
        self.chosen_width = self.width_spin.value()
        self.chosen_height = self.height_spin.value()
        self.accept()


class SymbolPaletteDialog(QDialog):
    """分类符号面板：点击按钮即插入目标文本框光标处，不关闭面板。"""

    def __init__(self, target: QTextEdit, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.target = target
        self.setWindowTitle("符号面板")
        self.resize(560, 320)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        for title, symbols in SYMBOL_TABS:
            page = QWidget()
            grid = QGridLayout(page)
            per_row = 10
            for index, symbol in enumerate(symbols):
                button = QPushButton(symbol)
                button.setFixedSize(40, 32)
                button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                button.clicked.connect(lambda _=False, s=symbol: self._insert(s))
                grid.addWidget(button, index // per_row, index % per_row)
            tabs.addTab(page, title)
        layout.addWidget(tabs)
        hint = QLabel("点击符号即插入光标处；面板保持打开可连续插入。")
        layout.addWidget(hint)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        layout.addWidget(close_btn)

    def _insert(self, symbol: str) -> None:
        cursor = self.target.textCursor()
        cursor.insertText(symbol)
        self.target.setTextCursor(cursor)


class FormulaDialog(QDialog):
    """公式编辑器：LaTeX（matplotlib mathtext 语法）实时预览，以 $…$ / $$…$$ 插入。"""

    def __init__(self, target: QTextEdit, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.target = target
        self.setWindowTitle("公式编辑器（LaTeX）")
        self.resize(640, 480)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("输入公式（LaTeX/mathtext 语法，如 \\alpha + \\frac{a}{b}、E = mc^2）："))
        self.input = QTextEdit()
        self.input.setFixedHeight(72)
        self.input.setPlaceholderText("例：\\eta = \\frac{E_{out}}{E_{in}} \\times 100\\%")
        layout.addWidget(self.input)
        self.preview_label = QLabel("预览：")
        layout.addWidget(self.preview_label)

        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure

        self.figure = Figure(figsize=(6, 2.6), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas, 1)
        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #B23A3A;")
        layout.addWidget(self.error_label)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self.input.textChanged.connect(self._timer.start)
        self._timer.timeout.connect(self._update_preview)

        buttons_layout = QHBoxLayout()
        inline_btn = QPushButton("插入为行内 $…$")
        inline_btn.clicked.connect(lambda: self._insert(False))
        block_btn = QPushButton("插入为独立 $$…$$")
        block_btn.clicked.connect(lambda: self._insert(True))
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        buttons_layout.addWidget(inline_btn)
        buttons_layout.addWidget(block_btn)
        buttons_layout.addWidget(close_btn)
        layout.addLayout(buttons_layout)

        self.input.setPlainText("E = mc^2")
        self._update_preview()

    def _update_preview(self) -> None:
        tex = self.input.toPlainText().strip()
        self.figure.clf()
        self.error_label.setText("")
        if not tex:
            self.canvas.draw_idle()
            return
        self.figure.text(0.5, 0.5, f"${tex}$", ha="center", va="center", fontsize=15)
        try:
            self.canvas.draw()
        except (ValueError, TypeError) as exc:
            self.error_label.setText(f"公式语法有误：{exc}")

    def _insert(self, block: bool) -> None:
        tex = self.input.toPlainText().strip()
        if not tex:
            return
        wrapped = f"$${tex}$$" if block else f"${tex}$"
        cursor = self.target.textCursor()
        cursor.insertText(f" {wrapped} ")
        self.target.setTextCursor(cursor)
