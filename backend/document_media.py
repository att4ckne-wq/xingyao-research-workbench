"""Bounded, local-only document media. Never modifies the source or existing assets.

Caller supplies a dedicated per-document destination and attaches document_id to
the returned descriptors. Filenames are relative basenames, never source paths.
The maximum is 24 descriptors / 60 MiB, with 20 MiB per original image.
Raster images over 24M pixels are preserved for download without decoding,
previewing, or OCR; the pixel limit remains enforced for local processing.
PDF previews cover at most the first 80 pages and 8 pages containing vectors. They are
explicitly page previews, not inferred figures. DOCX page numbers are unknown.
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import math
import posixpath
import re
import shutil
import subprocess
import tempfile
import warnings
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import fitz
from PIL import Image

MAX_IMAGES = 24
MAX_TOTAL_BYTES = 60 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 24_000_000
MAX_PAGES_SCANNED = 80
MAX_PAGE_PREVIEWS = 8
MAX_PREVIEW_EDGE = 1600
LOG = logging.getLogger(__name__)
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "v": "urn:schemas-microsoft-com:vml"}
CAPTION = re.compile(r"^(?:图\s*\d|fig(?:ure)?\.?\s*\d)", re.I)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _save_new(destination: Path, filename: str, raw: bytes) -> None:
    """Exclusive creation, or content-verified reuse; conflicting files fail closed."""
    target = destination / filename
    if target.is_symlink():
        raise ValueError("Media destination contains a symbolic link")
    try:
        with target.open("xb") as stream:
            stream.write(raw)
    except FileExistsError:
        if not target.is_file() or target.stat().st_size != len(raw):
            raise ValueError("Existing media asset conflicts with extracted content") from None
        if _digest(target.read_bytes()) != _digest(raw):
            raise ValueError("Existing media asset conflicts with extracted content") from None


class _ImagePixelLimit(ValueError):
    def __init__(self, width: int, height: int):
        super().__init__("Image exceeds pixel limit")
        self.width = width
        self.height = height


def _image_info(raw: bytes) -> tuple[int, int, str]:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            if width <= 0 or height <= 0:
                raise ValueError("Image has invalid dimensions")
            if width * height > MAX_PIXELS:
                raise _ImagePixelLimit(width, height)
            return width, height, image.format or ""


class _Collector:
    def __init__(self, destination: Path):
        self.destination = destination
        self.items: list[dict] = []
        self.total = 0
        self.originals_count = 0
        self.warnings: list[str] = []

    def add(self, raw: bytes, extension: str, *, page: int | None,
            kind: str = "embedded", caption: str | None = None,
            occurrences: list[dict] | None = None) -> dict | None:
        if len(self.items) >= MAX_IMAGES or len(raw) > MAX_IMAGE_BYTES:
            self.warnings.append("已达到图片数量或单图大小限制，部分图片未提取")
            return None
        extension = extension.lower().lstrip(".")
        if extension not in {"png", "jpg", "jpeg", "gif", "webp", "tif", "tiff", "bmp", "wmf", "emf", "jpx", "jp2", "pbm", "ppm", "pnm"}:
            LOG.warning("Unsupported document media format skipped")
            self.warnings.append("部分图片格式暂不支持，未提取")
            return None
        original = None
        preview_available = True
        warning = None
        try:
            width, height, fmt = _image_info(raw)
            if fmt not in {"PNG", "JPEG", "GIF", "WEBP"}:
                if extension in {"emf", "wmf"}:
                    raise OSError("Vector media has no safe local renderer")
                original = (raw, extension)
                with Image.open(io.BytesIO(raw)) as image:
                    image.seek(0)
                    output = io.BytesIO()
                    image.convert("RGB").save(output, format="PNG")
                    raw, extension = output.getvalue(), "png"
                warning = "仅预览首帧；原始图片可单独下载" if fmt == "TIFF" else None
        except _ImagePixelLimit as error:
            width, height = error.width, error.height
            preview_available = False
            warning = "超过本地预览像素限制，原图可下载"
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            # Pillow may reject extreme dimensions while reading the header.
            # Preserve only the bounded original bytes; never load its pixels.
            width, height = None, None
            preview_available = False
            warning = "超过本地预览像素限制，原图可下载"
        except (OSError, ValueError):
            if extension not in {"emf", "wmf"}:
                LOG.warning("Invalid or oversized document image skipped")
                self.warnings.append("部分图片无法读取或超过像素限制，未提取")
                return None
            width, height = None, None
            preview_available = False
            warning = "此矢量图片暂不支持浏览器预览，可下载原图"
        payload_size = len(raw) + (len(original[0]) if original else 0)
        if payload_size > MAX_IMAGE_BYTES * 2 or self.total + payload_size > MAX_TOTAL_BYTES:
            self.warnings.append("已达到图片存储大小限制，部分图片未提取")
            return None
        digest = _digest(raw)
        identifier = f"{kind}-{digest[:32]}"
        existing = next((item for item in self.items if item["id"] == identifier), None)
        if existing:
            existing["occurrences"].extend(occurrences or [{"page": page}])
            if original:
                original_filename = f"original-{_digest(original[0])[:32]}.{original[1]}"
                known_originals = [existing.get("original_filename"), *existing.get("original_variants", [])]
                if original_filename not in known_originals:
                    if self.originals_count >= MAX_IMAGES:
                        self.warnings.append("已达到原格式图片数量限制，部分原格式未保留")
                        return existing
                    # The preview has already been counted. Charge the distinct
                    # original bytes only; the conservative payload gate above
                    # still applies and no existing file is ever overwritten.
                    _save_new(self.destination, original_filename, original[0])
                    self.total += len(original[0])
                    self.originals_count += 1
                    if not existing.get("original_filename"):
                        existing["original_filename"] = original_filename
                    else:
                        existing.setdefault("original_variants", []).append(original_filename)
                if warning and not existing.get("warning"):
                    existing["warning"] = warning
            return existing
        if original and self.originals_count >= MAX_IMAGES:
            self.warnings.append("已达到原格式图片数量限制，部分图片未提取")
            return None
        filename = f"{identifier}.{extension}"
        _save_new(self.destination, filename, raw)
        item = {"id": identifier, "filename": filename, "page": page,
                "caption": caption, "width": width, "height": height,
                "kind": kind, "preview_available": preview_available,
                "occurrences": occurrences or [{"page": page}]}
        if original:
            original_filename = f"original-{_digest(original[0])[:32]}.{original[1]}"
            _save_new(self.destination, original_filename, original[0])
            item["original_filename"] = original_filename
            self.originals_count += 1
        if warning:
            item["warning"] = warning
        self.total += payload_size
        self.items.append(item)
        return item


def _bounded_xml(archive: zipfile.ZipFile, name: str) -> ET.Element:
    info = archive.getinfo(name)
    if info.file_size > 8 * 1024 * 1024:
        raise ValueError("Document XML exceeds extraction limit")
    raw = archive.read(info)
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("Document XML entities are unsupported")
    return ET.fromstring(raw)


def _docx_images(source: Path, collector: _Collector) -> None:
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        if len(infos) > 4096 or sum(info.file_size for info in infos) > 100 * 1024 * 1024:
            raise ValueError("Document archive exceeds media extraction limit")
        if len({info.filename for info in infos}) != len(infos):
            raise ValueError("Duplicate document archive entries")
        root = _bounded_xml(archive, "word/document.xml")
        rels = _bounded_xml(archive, "word/_rels/document.xml.rels")
        relation_targets = {}
        for rel in rels:
            if rel.get("TargetMode", "").lower() == "external":
                continue
            target = rel.get("Target", "")
            if "\\" in target or ":" in target or target.startswith("/"):
                continue
            normalized = posixpath.normpath(posixpath.join("word", target))
            if normalized.startswith("word/media/"):
                relation_targets[rel.get("Id")] = normalized
        paragraphs = list(root.iter(f"{{{NS['w']}}}p"))
        texts = ["".join(t.text or "" for t in p.findall(".//w:t", NS)).strip()[:1200]
                 for p in paragraphs]
        occurrences: dict[str, list[dict]] = {}
        captions = {}
        for index, paragraph in enumerate(paragraphs):
            ids = [node.get(f"{{{NS['r']}}}embed") for node in paragraph.findall(".//a:blip", NS)]
            ids += [node.get(f"{{{NS['r']}}}id") for node in paragraph.findall(".//v:imagedata", NS)]
            for relation_id in ids:
                target = relation_targets.get(relation_id)
                if not target:
                    continue
                occurrences.setdefault(target, []).append({"page": None, "paragraph_index": index})
                for adjacent in (index, index + 1):
                    if adjacent >= len(paragraphs):
                        continue
                    style = paragraphs[adjacent].find("./w:pPr/w:pStyle", NS)
                    is_caption = style is not None and style.get(f"{{{NS['w']}}}val", "").lower() in {"caption", "图注"}
                    if texts[adjacent] and (is_caption or CAPTION.match(texts[adjacent])):
                        captions.setdefault(target, texts[adjacent])
                        break
        for target, positions in occurrences.items():
            if len(collector.items) >= MAX_IMAGES:
                collector.warnings.append("已达到24张图片上限，后续图片未提取")
                break
            try:
                info = archive.getinfo(target)
                if info.file_size > MAX_IMAGE_BYTES or info.file_size > max(1, info.compress_size) * 250:
                    collector.warnings.append("部分图片超过大小或压缩比例限制，未提取")
                    LOG.warning("Oversized compressed document image skipped")
                    continue
                collector.add(archive.read(info), Path(target).suffix, page=None,
                              caption=captions.get(target), occurrences=positions)
            except (KeyError, zipfile.BadZipFile, OSError):
                LOG.warning("Unreadable embedded document image skipped")


def _pdf_images(source: Path, collector: _Collector) -> None:
    previews = 0
    seen_xrefs = set()
    with fitz.open(source) as document:
        if document.needs_pass:
            raise ValueError("Encrypted PDF needs a password")
        if len(document) > MAX_PAGES_SCANNED:
            collector.warnings.append("仅扫描PDF前80页中的图片")
        for page_index in range(min(len(document), MAX_PAGES_SCANNED)):
            if len(collector.items) >= MAX_IMAGES:
                collector.warnings.append("已达到24张图片上限，后续图片未提取")
                break
            page = document[page_index]
            images = page.get_images(full=True)
            for entry in images[:MAX_IMAGES * 4]:
                xref, _, width, height = entry[:4]
                if xref in seen_xrefs:
                    for item in collector.items:
                        if item.get("xref") == xref:
                            item["occurrences"].append({"page": page_index + 1})
                    continue
                seen_xrefs.add(xref)
                if not xref or width <= 0 or height <= 0 or width * height > MAX_PIXELS:
                    continue
                # Bound compressed stream before MuPDF decodes it.
                if len(document.xref_stream_raw(xref) or b"") > MAX_IMAGE_BYTES:
                    continue
                try:
                    extracted = document.extract_image(xref)
                    if extracted:
                        item = collector.add(extracted["image"], extracted["ext"], page=page_index + 1)
                        if item:
                            item["xref"] = xref
                except (RuntimeError, ValueError):
                    LOG.warning("Unreadable embedded PDF image skipped")
            if previews >= MAX_PAGE_PREVIEWS:
                collector.warnings.append("页面预览最多8页，后续矢量内容未预览")
            if previews >= MAX_PAGE_PREVIEWS or len(collector.items) >= MAX_IMAGES:
                continue
            # Vector figures cannot be identified reliably without semantic analysis.
            # A labelled page preview preserves source context and never asserts a figure caption.
            if not page.get_drawings():
                continue
            edge = max(page.rect.width, page.rect.height)
            if not math.isfinite(edge) or edge <= 0:
                continue
            try:
                scale = min(1.5, MAX_PREVIEW_EDGE / edge)
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                collector.add(pix.tobytes("png"), "png", page=page_index + 1, kind="page_preview")
                previews += 1
            except (RuntimeError, ValueError):
                LOG.warning("PDF page preview unavailable")
        for item in collector.items:
            item.pop("xref", None)


def extract_document_images(source: Path, destination: Path) -> list[dict]:
    """Return bounded image descriptors. Dedicated destination may be reused safely.

    DOCX uses main document relationships only (including tables), not headers or
    floating external links. Invalid images are skipped; structural errors raise
    ValueError. Caller should display the extraction limits in its reader UI.
    """
    source, destination = Path(source), Path(destination)
    if source.suffix.lower() not in {".docx", ".pdf"}:
        return []
    for directory in [destination, *destination.parents]:
        if directory.is_symlink():
            raise ValueError("Media destination must not traverse symbolic links")
    destination.mkdir(parents=True, exist_ok=True)
    collector = _Collector(destination)
    try:
        if source.suffix.lower() == ".docx":
            _docx_images(source, collector)
        else:
            _pdf_images(source, collector)
    except (zipfile.BadZipFile, ET.ParseError, KeyError, fitz.FileDataError) as error:
        raise ValueError("Document media could not be parsed") from error
    if collector.warnings and collector.items:
        collector.items[-1]["extraction_warnings"] = list(dict.fromkeys(collector.warnings))
    return collector.items


def ocr_document_image(image_path: Path, *, temp_root: Path | None = None) -> dict:
    """Explicit local OCR for one stored raster image, confidence in [0,1].

    temp_root must be an authorized temporary directory. No model/network calls,
    no source rewrites. Temporary working directory is removed on completion.
    """
    image_path = Path(image_path).resolve(strict=True)
    if image_path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("OCR image exceeds size limit")
    _image_info(image_path.read_bytes())
    executable = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    command = str(executable) if executable.is_file() else shutil.which("tesseract")
    if not command:
        return {"text": "", "confidence": 0.0, "language": "chi_sim+eng", "status": "unavailable"}
    if temp_root is not None:
        Path(temp_root).mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="document-ocr-", dir=temp_root) as directory:
            result = subprocess.run([command, str(image_path), "stdout", "-l", "chi_sim+eng", "--psm", "6", "tsv"],
                                    cwd=directory, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=45, check=True)
    except (OSError, subprocess.SubprocessError):
        return {"text": "", "confidence": 0.0, "language": "chi_sim+eng", "status": "failed"}
    lines: dict[tuple, list[str]] = {}
    confidences = []
    for row in csv.DictReader(io.StringIO(result.stdout), delimiter="\t"):
        word = (row.get("text") or "").strip()
        if not word:
            continue
        key = tuple(row.get(name, "0") for name in ("page_num", "block_num", "par_num", "line_num"))
        lines.setdefault(key, []).append(word)
        try:
            confidence = float(row.get("conf", "-1"))
            if math.isfinite(confidence) and 0 <= confidence <= 100:
                confidences.append(confidence / 100)
        except ValueError:
            pass
    return {"text": "\n".join(" ".join(words) for words in lines.values()),
            "confidence": round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
            "language": "chi_sim+eng", "status": "completed"}
