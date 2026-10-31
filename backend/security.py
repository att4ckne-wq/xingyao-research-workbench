from __future__ import annotations

import re
import zipfile
from pathlib import Path


SENSITIVE_PATTERNS = {
    "email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    "phone": re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"),
    "identity_number": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    "personal_name": re.compile(
        r"(?:姓名|作者)\s*[:：]\s*[\u4e00-\u9fff·]{2,12}(?=\s|学\s*号|$)"
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
    return [name for name, pattern in SENSITIVE_PATTERNS.items() if pattern.search(text)]


def redact(text: str) -> str:
    output = text
    for name, pattern in SENSITIVE_PATTERNS.items():
        output = pattern.sub(f"[{name.upper()}]", output)
    return output


def safe_filename(name: str) -> str:
    clean = re.sub(r"[^\w.\-()\u4e00-\u9fff ]+", "_", Path(name).name, flags=re.UNICODE).strip(" .")
    return clean[:180] or "upload.bin"


def ensure_within(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError("Unsafe path")
    return resolved


def validate_file_signature(path: Path, suffix: str) -> str:
    """Validate the file body independently of the browser-provided MIME type."""
    suffix = suffix.lower()
    head = path.read_bytes()[:8192]
    if suffix == ".pdf":
        if not head.startswith(b"%PDF-"): raise ValueError("PDF signature does not match the filename")
        return "application/pdf"
    if suffix in {".docx", ".xlsx"}:
        if not head.startswith(b"PK\x03\x04"): raise ValueError("Office Open XML signature does not match the filename")
        try:
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                names = {entry.filename.replace("\\", "/") for entry in entries}
                required = "word/document.xml" if suffix == ".docx" else "xl/workbook.xml"
                if "[Content_Types].xml" not in names or required not in names:
                    raise ValueError(f"The archive is not a valid {suffix[1:].upper()} document")
                if any(Path(name).is_absolute() or ".." in Path(name).parts for name in names):
                    raise ValueError("Office archive contains an unsafe path")
                compressed = max(1, sum(entry.compress_size for entry in entries))
                expanded = sum(entry.file_size for entry in entries)
                if expanded > 1024 * 1024 * 1024 or expanded / compressed > 150:
                    raise ValueError("Office archive expansion ratio is unsafe")
                if any(entry.flag_bits & 0x1 for entry in entries):
                    raise ValueError("Encrypted Office documents are not supported")
        except zipfile.BadZipFile as exc:
            raise ValueError("Office document is not a valid ZIP container") from exc
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if suffix == ".docx" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if suffix in {".md", ".markdown", ".tex", ".csv"}:
        encodings = ("utf-16",) if head.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "gb18030")
        if b"\x00" in head and encodings != ("utf-16",):
            raise ValueError("Text upload contains binary NUL bytes")
        for encoding in encodings:
            try:
                head.decode(encoding)
                return {".csv":"text/csv", ".tex":"application/x-tex"}.get(suffix, "text/markdown")
            except UnicodeDecodeError:
                continue
        raise ValueError("Text upload encoding is not supported")
    raise ValueError("Unsupported file signature")
