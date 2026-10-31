from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import fitz
import pytest

from backend import parsers


class _Pixmap:
    width = 24
    height = 16
    samples = bytes([255, 255, 255] * width * height)


class _Page:
    def get_pixmap(self, **_kwargs):
        return _Pixmap()


def test_heading_detection_recognizes_reference_sections_and_numbered_titles():
    assert parsers._is_heading("Normal", "参考文献")
    assert parsers._is_heading("Normal", "References")
    assert parsers._is_heading("Normal", "3.2 钠离子电池与超级电容器")
    assert parsers._is_heading("Normal", "2.3反应器设计")
    assert parsers._is_heading("Normal", "5.2应用推广")
    assert not parsers._is_heading("Normal", "2025年装机规模达到1.36亿千瓦")
    assert not parsers._is_heading("Normal", "3.14 mol 催化剂参与反应")


def test_ocr_uses_osd_rotation_and_word_confidence(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if "osd" in command:
            return SimpleNamespace(stdout="Orientation in degrees: 270\nRotate: 90\n", stderr="")
        return SimpleNamespace(
            stdout=(
                "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
                "5\t1\t1\t1\t1\t1\t0\t0\t10\t8\t96\tCatalyst\n"
                "5\t1\t1\t1\t1\t2\t12\t0\t10\t8\t84\t活性\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(parsers, "_tesseract_path", lambda: "tesseract")
    monkeypatch.setattr(parsers.subprocess, "run", fake_run)
    text, confidence, rotation = parsers._ocr_page(_Page())

    assert text == "Catalyst 活性"
    assert confidence == pytest.approx(0.9)
    assert rotation == 90
    assert any("osd" in call for call in calls)
    assert any(call[-1] == "tsv" for call in calls)


def test_text_pdf_preserves_page_and_block_coordinates(tmp_path: Path):
    path = tmp_path / "paper.pdf"
    document = fitz.open()
    page = document.new_page()
    text = (
        "Catalyst conversion increased across independent synthesis batches. "
        "The paragraph is deliberately long enough to use embedded PDF text."
    )
    page.insert_textbox(fitz.Rect(72, 72, 500, 180), text, fontsize=11)
    document.save(path)
    document.close()

    parser_name, anchors = parsers.parse_document(path)
    assert parser_name == "pymupdf+tesseract"
    assert len(anchors) == 1
    assert anchors[0].page == 1
    assert anchors[0].ocr_confidence is None
    assert anchors[0].bbox and len(anchors[0].bbox) == 4
    assert "independent synthesis batches" in anchors[0].text


def test_latex_source_parser_keeps_sections_and_ignores_structure(tmp_path: Path):
    path = tmp_path / "paper.tex"
    path.write_text(
        "\\documentclass{article}\n"
        "\\usepackage{graphicx}\n"
        "\\begin{document}\n"
        "\\section{Results}\n"
        "Catalyst conversion increased across independent batches.\n\n"
        "\\subsection{Discussion}\n"
        "The response remained stable after repeated cycling.\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    parser_name, anchors = parsers.parse_document(path)
    assert parser_name == "source-parser"
    assert [anchor.section for anchor in anchors] == ["Results", "Discussion"]
    assert all(anchor.bbox and len(anchor.bbox) == 2 for anchor in anchors)
