"""Tiny synthetic fixtures only. No user files, model calls, or expensive exports."""
import hashlib
import io
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from PIL import Image

from backend import document_media as media


def raster(fmt="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (30, 20), "white").save(stream, format=fmt)
    return stream.getvalue()


def docx(path, raw=None, extension="png", target="media/image.png", external=False):
    raw = raw if raw is not None else raster()
    with zipfile.ZipFile(path, "x") as archive:
        archive.writestr("word/document.xml", '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body><w:p><w:r><a:blip r:embed="rId1"/></w:r></w:p><w:p><w:pPr><w:pStyle w:val="Caption"/></w:pPr><w:r><w:t>图1 合成示意</w:t></w:r></w:p></w:body></w:document>''')
        mode = ' TargetMode="External"' if external else ""
        archive.writestr("word/_rels/document.xml.rels", f'<Relationships><Relationship Id="rId1" Target="{target}"{mode}/></Relationships>')
        archive.writestr(f"word/media/image.{extension}", raw)


def test_docx_caption_idempotence_and_source_unchanged(tmp_path):
    source = tmp_path / "tiny.docx"
    docx(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    destination = tmp_path / "media"
    result = media.extract_document_images(source, destination)
    assert len(result) == 1
    item = result[0]
    assert (item["width"], item["height"], item["page"]) == (30, 20, None)
    assert item["caption"] == "图1 合成示意"
    assert Path(item["filename"]).name == item["filename"]
    asset = destination / item["filename"]
    modified = asset.stat().st_mtime_ns
    assert media.extract_document_images(source, destination) == result
    assert asset.stat().st_mtime_ns == modified
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("target,external", [("../../outside.png", False), ("https://example.invalid/image.png", True)])
def test_docx_ignores_external_and_traversal(tmp_path, target, external):
    source = tmp_path / "tiny.docx"
    docx(source, target=target, external=external)
    assert media.extract_document_images(source, tmp_path / "media") == []
    assert not list((tmp_path / "media").iterdir())


def test_tiff_first_frame_and_unsupported_vector(tmp_path):
    source = tmp_path / "tiny.docx"
    docx(source, raster("TIFF"), "tiff", "media/image.tiff")
    result = media.extract_document_images(source, tmp_path / "media")
    assert result[0]["filename"].endswith(".png")
    assert result[0]["original_filename"].endswith(".tiff")
    vector = tmp_path / "vector.docx"
    docx(vector, b"unsupported vector fixture", "emf", "media/image.emf")
    result = media.extract_document_images(vector, tmp_path / "vector-media")
    assert result[0]["preview_available"] is False
    assert result[0]["warning"]


def test_docx_matching_png_tiff_retains_original_without_duplicate_preview(tmp_path):
    source = tmp_path / "matching.docx"
    png, tiff = raster(), raster("TIFF")
    with zipfile.ZipFile(source, "x") as archive:
        archive.writestr("word/document.xml", '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body><w:p><a:blip r:embed="rId1"/></w:p><w:p><a:blip r:embed="rId2"/></w:p></w:body></w:document>''')
        archive.writestr("word/_rels/document.xml.rels", '<Relationships><Relationship Id="rId1" Target="media/image.png"/><Relationship Id="rId2" Target="media/image.tiff"/></Relationships>')
        archive.writestr("word/media/image.png", png)
        archive.writestr("word/media/image.tiff", tiff)
    source_before = source.read_bytes()
    destination = tmp_path / "media"
    items = media.extract_document_images(source, destination)
    assert len(items) == 1
    assert len(items[0]["occurrences"]) == 2
    assert (destination / items[0]["filename"]).read_bytes() == png
    assert (destination / items[0]["original_filename"]).read_bytes() == tiff
    assert len(list(destination.iterdir())) == 2
    mtimes = {path.name: path.stat().st_mtime_ns for path in destination.iterdir()}
    assert media.extract_document_images(source, destination) == items
    assert {path.name: path.stat().st_mtime_ns for path in destination.iterdir()} == mtimes
    assert source.read_bytes() == source_before


def test_duplicate_preview_original_variants_and_budget(tmp_path, monkeypatch):
    collector = media._Collector(tmp_path)
    png, tiff = raster(), raster("TIFF")
    item = collector.add(png, "png", page=None)
    collector.add(tiff, "tiff", page=None)
    original_name = item["original_filename"]
    original_mtime = (tmp_path / original_name).stat().st_mtime_ns
    assert collector.total == len(png) + len(tiff)
    collector.add(tiff, "tiff", page=None)
    assert collector.total == len(png) + len(tiff)
    assert (tmp_path / original_name).stat().st_mtime_ns == original_mtime
    variant = io.BytesIO()
    Image.new("RGB", (30, 20), "white").save(variant, format="TIFF", tiffinfo={270: "distinct tiny original"})
    variant_raw = variant.getvalue()
    collector.add(variant_raw, "tiff", page=None)
    assert item["original_filename"] == original_name
    assert len(item["original_variants"]) == 1
    assert (tmp_path / item["original_variants"][0]).read_bytes() == variant_raw
    assert collector.total == len(png) + len(tiff) + len(variant_raw)
    assert len(collector.items) == 1
    limited = tmp_path / "limited"
    limited.mkdir()
    bounded = media._Collector(limited)
    bounded.add(png, "png", page=None)
    monkeypatch.setattr(media, "MAX_TOTAL_BYTES", len(png) + len(tiff) - 1)
    assert bounded.add(tiff, "tiff", page=None) is None
    assert bounded.total == len(png)
    assert len(list(limited.iterdir())) == 1


def test_conflicting_asset_never_overwritten(tmp_path):
    source = tmp_path / "tiny.docx"
    docx(source)
    destination = tmp_path / "media"
    destination.mkdir()
    raw = raster()
    filename = f"embedded-{hashlib.sha256(raw).hexdigest()[:32]}.png"
    asset = destination / filename
    asset.write_bytes(b"different pre-existing data")
    with pytest.raises(ValueError, match="conflicts"):
        media.extract_document_images(source, destination)
    assert asset.read_bytes() == b"different pre-existing data"


def test_pdf_embedded_vector_preview_and_no_invented_captions(tmp_path):
    source = tmp_path / "tiny.pdf"
    with fitz.open() as document:
        page = document.new_page(width=200, height=200)
        page.insert_image(fitz.Rect(10, 10, 60, 50), stream=raster())
        page = document.new_page(width=200, height=200)
        page.draw_rect(fitz.Rect(10, 10, 100, 80), color=(0, 0, 0))
        document.save(source)
    before = source.read_bytes()
    result = media.extract_document_images(source, tmp_path / "media")
    assert [item["kind"] for item in result] == ["embedded", "page_preview"]
    assert [item["page"] for item in result] == [1, 2]
    assert all(item["caption"] is None for item in result)
    assert max(result[1]["width"], result[1]["height"]) <= media.MAX_PREVIEW_EDGE
    assert source.read_bytes() == before


def test_image_cap_and_oversized_pixels(tmp_path, monkeypatch):
    source = tmp_path / "tiny.docx"
    docx(source)
    monkeypatch.setattr(media, "MAX_PIXELS", 100)
    result = media.extract_document_images(source, tmp_path / "media")
    assert len(result) == 1
    assert result[0]["preview_available"] is False
    assert result[0]["warning"] == "超过本地预览像素限制，原图可下载"
    monkeypatch.setattr(media, "MAX_PIXELS", 1000)
    monkeypatch.setattr(media, "MAX_IMAGES", 0)
    assert media.extract_document_images(source, tmp_path / "capped") == []


def test_oversized_raster_preserves_bytes_without_decode_or_ocr(tmp_path, monkeypatch):
    source = tmp_path / "tiny.docx"
    raw = raster()
    docx(source, raw)
    original_document = source.read_bytes()
    monkeypatch.setattr(media, "MAX_PIXELS", 100)
    def forbidden_decode(*args, **kwargs):
        raise AssertionError("Oversized raster must not be decoded or sent to OCR")
    monkeypatch.setattr(Image.Image, "load", forbidden_decode)
    monkeypatch.setattr(media.subprocess, "run", forbidden_decode)
    destination = tmp_path / "media"
    items = media.extract_document_images(source, destination)
    assert len(items) == 1
    item = items[0]
    asset = destination / item["filename"]
    assert asset.read_bytes() == raw
    assert (item["width"], item["height"]) == (30, 20)
    assert item["preview_available"] is False
    assert item["warning"] == "超过本地预览像素限制，原图可下载"
    modified = asset.stat().st_mtime_ns
    assert media.extract_document_images(source, destination) == items
    assert asset.stat().st_mtime_ns == modified
    assert source.read_bytes() == original_document
    with pytest.raises(ValueError, match="pixel limit"):
        media.ocr_document_image(asset, temp_root=tmp_path / "ocr-temp")
    assert not (tmp_path / "ocr-temp").exists()


def test_pdf_mixed_page_and_visible_limit_warning(tmp_path, monkeypatch):
    source = tmp_path / "mixed.pdf"
    with fitz.open() as document:
        for index in range(3):
            page = document.new_page(width=200, height=200)
            page.draw_rect(fitz.Rect(10, 10, 100 + index, 80), color=(0, 0, 0))
            if index == 0:
                page.insert_image(fitz.Rect(10, 90, 60, 130), stream=raster())
        document.save(source)
    monkeypatch.setattr(media, "MAX_IMAGES", 2)
    result = media.extract_document_images(source, tmp_path / "media")
    assert len(result) == 2
    assert result[0]["kind"] == "embedded"
    assert result[1]["kind"] == "page_preview"
    assert result[1]["page"] == 1
    assert result[-1]["extraction_warnings"]


def test_explicit_local_ocr_parses_confidence_and_timeout(tmp_path, monkeypatch):
    image = tmp_path / "tiny.png"
    image.write_bytes(raster())
    before = image.stat().st_mtime_ns
    calls = []
    monkeypatch.setattr(media.shutil, "which", lambda _: "tesseract")
    tsv = "page_num\tblock_num\tpar_num\tline_num\tconf\ttext\n1\t1\t1\t1\t90\tTest\n1\t1\t1\t1\t70\timage\n"
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=tsv)
    monkeypatch.setattr(media.subprocess, "run", run)
    result = media.ocr_document_image(image, temp_root=tmp_path / "ocr-temp")
    assert result["text"] == "Test image"
    assert result["confidence"] == 0.8
    assert result["status"] == "completed"
    assert "chi_sim+eng" in calls[0][0]
    assert calls[0][1]["timeout"] == 45
    assert image.stat().st_mtime_ns == before
    assert not list((tmp_path / "ocr-temp").iterdir())
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("tesseract", 45)
    monkeypatch.setattr(media.subprocess, "run", timeout)
    assert media.ocr_document_image(image, temp_root=tmp_path / "ocr-temp")["status"] == "failed"
