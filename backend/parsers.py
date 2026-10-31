from __future__ import annotations

import hashlib
import csv
import io
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import fitz
from docx import Document as DocxDocument
from docx.text.paragraph import Paragraph
from PIL import Image

from .security import scan_sensitive


@dataclass
class ParsedAnchor:
    section: str
    page: int | None
    paragraph_index: int
    text: str
    bbox: list[float] | None = None
    ocr_confidence: float | None = None
    ocr_rotation: int | None = None
    ocr_confirmed: bool = False

    @property
    def text_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    @property
    def sensitivity(self) -> list[str]:
        return scan_sensitive(self.text)


@dataclass
class SourceBlock:
    section: str
    start_line: int
    end_line: int
    text: str


def _is_heading(style: str, text: str) -> bool:
    normalized = text.strip()
    if not style.lower().startswith("heading") and re.match(
        r"^\d+(?:\.\d+)?\s*(?:mol|mm|cm|kg|g|kw|w|mpa|pa|nm³|nm3|℃|°c)\b",
        normalized,
        re.I,
    ):
        return False
    named_section = re.fullmatch(
        r"(?:参考文献|References|Bibliography|致谢|Acknowledg(?:e)?ments?|附录(?:\s*[A-Z0-9一二三四五六七八九十]+)?)",
        normalized,
        re.I,
    )
    numbered_section = re.fullmatch(r"(?:\d+(?:\.\d+)*)[.、]?\s+\S.{0,70}", normalized)
    compact_numbered_section = re.fullmatch(
        r"(?:[1-9]\d?)(?:\.\d{1,2}){1,3}[.、]?[\u4e00-\u9fff]\S{0,70}",
        normalized,
    )
    return (
        style.lower().startswith("heading")
        or bool(named_section)
        or bool(numbered_section)
        or bool(compact_numbered_section)
    )


def document_paragraphs(document) -> list[Paragraph]:
    """Keep existing body indices stable, then append unique table paragraphs."""
    body = list(document.paragraphs)
    return body + [Paragraph(element, document._body) for element in document.element.body.xpath(".//w:tbl//w:p")]


def parse_docx(path: Path) -> list[ParsedAnchor]:
    doc = DocxDocument(path)
    anchors: list[ParsedAnchor] = []
    section = "正文"
    for index, paragraph in enumerate(document_paragraphs(doc)):
        text = paragraph.text.strip()
        if not text:
            continue
        if index < len(doc.paragraphs) and _is_heading(paragraph.style.name if paragraph.style else "", text):
            section = text
            continue
        anchors.append(ParsedAnchor("表格" if index >= len(doc.paragraphs) else section, None, index, text))
    return anchors


def _tesseract_path() -> str:
    candidates = [Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"), Path("tesseract.exe")]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    raise RuntimeError("Tesseract OCR is not installed")


def _detect_orientation(image_path: Path) -> int:
    try:
        result = subprocess.run(
            [_tesseract_path(), str(image_path), "stdout", "-l", "osd", "--psm", "0"],
            check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        match = re.search(r"Rotate:\s*(0|90|180|270)", f"{result.stdout}\n{result.stderr}")
        return int(match.group(1)) if match else 0
    except (OSError, subprocess.CalledProcessError):
        return 0


def _ocr_page(page: fitz.Page) -> tuple[str, float, int]:
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    with tempfile.TemporaryDirectory() as temp:
        image_path = Path(temp) / "page.png"
        image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        image.save(image_path)
        rotation = _detect_orientation(image_path)
        if rotation:
            rotated_path = Path(temp) / "page-upright.png"
            rotated = image.rotate(-rotation, expand=True)
            rotated.save(rotated_path)
            rotated.close()
            image_path = rotated_path
        image.close()
        result = subprocess.run(
            [_tesseract_path(), str(image_path), "stdout", "-l", "chi_sim+eng", "--psm", "6", "tsv"],
            check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        lines: dict[tuple[str, str, str], list[str]] = {}
        confidences: list[float] = []
        for row in csv.DictReader(io.StringIO(result.stdout), delimiter="\t"):
            word = (row.get("text") or "").strip()
            if not word: continue
            key = (row.get("block_num", "0"), row.get("par_num", "0"), row.get("line_num", "0"))
            lines.setdefault(key, []).append(word)
            try:
                confidence = float(row.get("conf", "-1"))
                if confidence >= 0: confidences.append(confidence)
            except ValueError:
                pass
        text = "\n".join(" ".join(words) for words in lines.values())
        confidence = round(sum(confidences) / len(confidences) / 100, 4) if confidences else 0.0
        return text, confidence, rotation


def parse_pdf(path: Path) -> list[ParsedAnchor]:
    anchors: list[ParsedAnchor] = []
    with fitz.open(path) as document:
        for page_index, page in enumerate(document):
            blocks = page.get_text("blocks")
            text_length = sum(len((block[4] or "").strip()) for block in blocks)
            if not text_length or (text_length < 60 and page.get_images()):
                text, confidence, rotation = _ocr_page(page)
                chunks = [part.strip() for part in text.splitlines() if part.strip()]
                for chunk in chunks:
                    anchors.append(ParsedAnchor("OCR 文本", page_index + 1, len(anchors), chunk, None, confidence, rotation, False))
                if chunks:
                    continue
            for block in blocks:
                text = " ".join((block[4] or "").split())
                if text:
                    anchors.append(ParsedAnchor("正文", page_index + 1, len(anchors), text, [float(v) for v in block[:4]], None))
    return anchors


def source_blocks(text: str, suffix: str) -> list[SourceBlock]:
    """Return editable prose blocks while preserving source line locations."""
    lines = text.splitlines()
    blocks: list[SourceBlock] = []
    section = "正文"
    buffer: list[str] = []
    start_line: int | None = None
    in_fence = False

    def flush(end_line: int) -> None:
        nonlocal buffer, start_line
        chunk = " ".join(buffer).strip()
        if chunk and start_line is not None:
            blocks.append(SourceBlock(section, start_line, end_line, chunk))
        buffer = []
        start_line = None

    structural_tex = ("\\documentclass", "\\usepackage", "\\begin", "\\end", "\\label", "\\bibliography", "\\bibliographystyle", "\\maketitle")
    for line_index, line in enumerate(lines):
        stripped = line.strip()
        if suffix in {".md", ".markdown"} and stripped.startswith("```"):
            flush(line_index - 1); in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = stripped.startswith("#") or stripped.startswith("\\section") or stripped.startswith("\\subsection")
        if heading:
            flush(line_index - 1)
            section = stripped.lstrip("# ").replace("\\subsection", "").replace("\\section", "").strip("{} ")
        elif not stripped:
            flush(line_index - 1)
        elif stripped.startswith("%") or (suffix == ".tex" and stripped.startswith(structural_tex)):
            flush(line_index - 1)
        else:
            if start_line is None: start_line = line_index
            buffer.append(stripped)
    flush(len(lines) - 1)
    return blocks


def parse_text_source(path: Path) -> list[ParsedAnchor]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return [
        ParsedAnchor(block.section, None, index, block.text, [float(block.start_line), float(block.end_line)])
        for index, block in enumerate(source_blocks(text, path.suffix.lower()))
    ]


def parse_document(path: Path) -> tuple[str, list[ParsedAnchor]]:
    suffix = path.suffix.lower()
    if suffix == ".docx": return "python-docx", parse_docx(path)
    if suffix == ".pdf": return "pymupdf+tesseract", parse_pdf(path)
    if suffix in {".md", ".markdown", ".tex"}: return "source-parser", parse_text_source(path)
    raise ValueError(f"Unsupported document type: {suffix}")
