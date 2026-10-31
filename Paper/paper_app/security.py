"""实验记录 - 安全组件。

敏感信息扫描/脱敏与文件签名校验改编自 Nature Workbench `backend/security.py`，
附件场景补充了 PNG/JPEG/TIFF 图片签名。
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path


SENSITIVE_PATTERNS = {
    "email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    "phone": re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"),
    "identity_number": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    "personal_name": re.compile(
        r"(?:姓名|作者|负责人|经办人)\s*[:：]\s*[\u4e00-\u9fff·]{2,12}"
        r"(?=\s|[，。、,;；!！?？]|学\s*号|$)"
        r"|\bName\s*:\s*[A-Za-z][A-Za-z .'-]{1,60}(?=\s+Student\s+(?:ID|Number)\b|$)",
        re.I,
    ),
    "student_number": re.compile(
        r"(?:学\s*号|student\s*(?:id|number))\s*[:：]?\s*[A-Za-z]?\d{6,20}\b",
        re.I,
    ),
    "confidential_marker": re.compile(r"机密|保密|未公开|内部资料|confidential|not for distribution", re.I),
    "api_key": re.compile(r"\b(?:sk-|key[-_])[-A-Za-z0-9_]{16,}\b", re.I),
}


def scan_sensitive(text: str) -> list[str]:
    """返回命中的敏感信息类型列表，用于保存时提示与审计留痕。"""
    return [name for name, pattern in SENSITIVE_PATTERNS.items() if pattern.search(text)]


def redact(text: str) -> str:
    output = text
    for name, pattern in SENSITIVE_PATTERNS.items():
        output = pattern.sub(f"[{name.upper()}]", output)
    return output


def safe_filename(name: str) -> str:
    clean = re.sub(r"[^\w.\-()\u4e00-\u9fff ]+", "_", Path(name).name, flags=re.UNICODE).strip(" .")
    return clean[:180] or "attachment.bin"


def ensure_within(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError("Unsafe path")
    return resolved


def validate_file_signature(path: Path, suffix: str) -> str:
    """按文件体真实签名校验附件，不信任扩展名与浏览器/对话框提供的信息。"""
    suffix = suffix.lower()
    head = path.read_bytes()[:8192]
    if suffix == ".pdf":
        if not head.startswith(b"%PDF-"):
            raise ValueError("PDF 签名与文件名不匹配")
        return "application/pdf"
    if suffix in {".docx", ".xlsx"}:
        if not head.startswith(b"PK\x03\x04"):
            raise ValueError("Office Open XML 签名与文件名不匹配")
        try:
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                names = {entry.filename.replace("\\", "/") for entry in entries}
                required = "word/document.xml" if suffix == ".docx" else "xl/workbook.xml"
                if "[Content_Types].xml" not in names or required not in names:
                    raise ValueError(f"该文件不是有效的 {suffix[1:].upper()} 文档")
                if any(Path(name).is_absolute() or ".." in Path(name).parts for name in names):
                    raise ValueError("Office 压缩包含不安全路径")
                compressed = max(1, sum(entry.compress_size for entry in entries))
                expanded = sum(entry.file_size for entry in entries)
                if expanded > 1024 * 1024 * 1024 or expanded / compressed > 150:
                    raise ValueError("Office 压缩包展开比例不安全")
                if any(entry.flag_bits & 0x1 for entry in entries):
                    raise ValueError("不支持加密的 Office 文档")
        except zipfile.BadZipFile as exc:
            raise ValueError("Office 文档不是有效的 ZIP 容器") from exc
        return (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if suffix == ".docx"
            else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    if suffix == ".png":
        if not head.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("PNG 签名与文件名不匹配")
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        if not head.startswith(b"\xff\xd8\xff"):
            raise ValueError("JPEG 签名与文件名不匹配")
        return "image/jpeg"
    if suffix in {".tif", ".tiff"}:
        if not head.startswith((b"II*\x00", b"MM\x00*")):
            raise ValueError("TIFF 签名与文件名不匹配")
        return "image/tiff"
    if suffix in {".md", ".markdown", ".csv", ".txt"}:
        encodings = ("utf-16",) if head.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "gb18030")
        if b"\x00" in head and encodings != ("utf-16",):
            raise ValueError("文本附件包含二进制 NUL 字节")
        for encoding in encodings:
            try:
                head.decode(encoding)
                return "text/csv" if suffix == ".csv" else "text/markdown"
            except UnicodeDecodeError:
                continue
        raise ValueError("文本附件编码不受支持")
    raise ValueError("不支持的附件类型")
