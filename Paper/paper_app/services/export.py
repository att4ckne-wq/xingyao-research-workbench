# 提权目的：导出时把正文中的 att:// 图片引用还原为附件文件路径，保证导出的 Markdown 图片链接可用。
"""实验记录 - 导出服务：Markdown 与 PDF。"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from ..db import ROOT
from ..models import RECORD_FIELDS

EXPORT_DIR = ROOT / "exports"

ATTACHMENT_LINK = re.compile(r"\]\(att://([0-9a-fA-F]+)\)")


def _resolve_attachment_links(text: str, attachments: list[dict] | None) -> str:
    """把 `![图](att://<附件ID>)` 的链接目标改写为附件的绝对文件路径（POSIX 斜杠）。"""
    if not attachments:
        return text
    mapping: dict[str, str] = {}
    for attachment in attachments:
        try:
            mapping[attachment["id"]] = Path(attachment["local_path"]).resolve().as_posix()
        except OSError:
            continue
    if not mapping:
        return text
    return ATTACHMENT_LINK.sub(
        lambda match: f"]({mapping.get(match.group(1), 'att://' + match.group(1))})", text
    )


def _duration_text(record: dict) -> str:
    start, end = record.get("started_at"), record.get("ended_at")
    if start and end:
        return f"（{start} → {end}）"
    if start:
        return f"（开始于 {start}）"
    return ""


def record_to_markdown(record: dict, attachments: list[dict] | None = None) -> str:
    if attachments is None:
        attachments = record.get("attachments")
    lines = [
        f"### {record['title']}",
        "",
        f"- 发生时间：{record['occurred_at']} {_duration_text(record)}",
        f"- 事件类型：{record.get('event_type_name', '')}",
        f"- 所属项目：{record.get('project_name', '')}",
        f"- 标签：{('、'.join(record.get('tags') or [])) or '无'}",
        "",
    ]
    # 提权目的：正文各字段导出时还原 att:// 图片链接为附件文件路径，保证导出图片可显示。
    for field, label in RECORD_FIELDS:
        value = _resolve_attachment_links((record.get(field) or "").strip(), attachments)
        if value:
            lines += [f"## {label}", "", value, ""]
    content = _resolve_attachment_links((record.get("content") or "").strip(), attachments)
    if content:
        lines += ["## 补充记录", "", content, ""]
    if attachments:
        lines += ["## 附件", ""]
        lines += [f"- {a['filename']}（SHA-256 `{a['sha256'][:16]}…`）" for a in attachments]
        lines.append("")
    return "\n".join(lines)


def project_to_markdown(project: dict, records: list[dict]) -> str:
    lines = [
        f"# {project['name']} — 实验记录导出",
        "",
        f"- 导出时间：{datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M')}",
        f"- 项目状态：{project['status']}",
        f"- 记录条数：{len(records)}",
        "",
    ]
    if project.get("description"):
        lines += ["## 项目说明", "", project["description"], ""]
    current_day = None
    for record in records:
        day = str(record["occurred_at"])[:10]
        if day != current_day:
            current_day = day
            lines += [f"## {day}", ""]
        lines += [record_to_markdown(record), "---", ""]
    return "\n".join(lines)


def markdown_to_pdf(markdown_text: str, target: Path) -> Path:
    """用 Qt QTextDocument 把 Markdown 渲染为 PDF（不调用 Microsoft Word）。"""
    from PySide6.QtGui import QTextDocument
    from PySide6.QtPrintSupport import QPrinter

    document = QTextDocument()
    document.setMarkdown(markdown_text)
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(target))
    printer_method = getattr(document, "print_", None) or getattr(document, "print")
    printer_method(printer)
    return target


def default_export_dir() -> Path:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    return EXPORT_DIR
