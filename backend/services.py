from __future__ import annotations

import asyncio
import csv
import hashlib
import html
from html.parser import HTMLParser
import ipaddress
import json
import re
import shutil
import socket
import textwrap
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx
import fitz
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import MaxNLocator
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference, ScatterChart, Series
from openpyxl.chart.axis import ChartLines
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.series import SeriesLabel
from openpyxl.drawing.image import Image as OpenpyxlImage
from openpyxl.chart.text import RichText
from openpyxl.drawing.text import (
    CharacterProperties,
    Font as DrawingFont,
    Paragraph as DrawingParagraph,
    ParagraphProperties,
)
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.worksheet.table import Table, TableStyleInfo
import pandas as pd
import seaborn as sns
from docx import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches
from docx.text.paragraph import Paragraph
from PIL import Image, ImageOps

from . import db
from .models import ChartSpec, FigureComposition
from .parsers import source_blocks, document_paragraphs


CHECKPOINT_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "documents": ("id","project_id","filename","media_type","size_bytes","sha256","parser","language","local_path","created_at"),
    "anchors": ("id","project_id","document_id","section","page","paragraph_index","text","text_hash","bbox_json","ocr_confidence","ocr_rotation","ocr_confirmed","sensitivity_json"),
    "anchor_revisions": ("id","project_id","anchor_id","old_text","new_text","reason","created_at"),
    "document_revisions": ("id","project_id","old_content_json","new_content_json","reason","action","created_at"),
    "assets": ("id","project_id","anchor_id","title","provider","source_url","preview_url","author","license_json","accessed_at","score","reuse_status","checksum","rationale","local_path"),
    "project_literature": ("id","project_id","anchor_id","title","provider","source_url","preview_url","author","license_json","accessed_at","score","reuse_status","checksum","rationale","local_path"),
    "datasets": ("id","project_id","filename","local_path","profile_json","created_at"),
    "charts": ("id","project_id","dataset_id","spec_json","outputs_json","warnings_json","created_at"),
    "compositions": ("id","project_id","spec_json","outputs_json","warnings_json","created_at"),
    "assistant_messages": ("id","project_id","anchor_id","role","content","suggestions_json","created_at"),
    "decisions": ("id","project_id","target_type","target_id","action","reason","warnings_json","created_at"),
    "project_manuscripts": ("project_id","document_id"),
    "document_roles": ("document_id","project_id","role","status","error"),
    "citation_placements": ("project_id","anchor_id","reference_id","action","accepted_at"),
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_license(name: str | None, url: str | None = None) -> tuple[dict[str, Any], str]:
    raw = (name or "unknown").strip()
    lower = re.sub(r"[_\s]+", " ", raw.lower())
    url_lower = (url or "").lower()
    license_context = f"{lower} {url_lower}"
    if lower in {"cc0", "pdm"} or any(token in license_context for token in ("cc0", "public domain", "publicdomain")):
        return {
            "name":raw, "url":url, "attribution_required":False,
            "verified":True, "restrictions":[], "verification_source":"provider_metadata",
        }, "safe_open"
    restrictions: list[str] = []
    if re.search(r"(?:^|[\s-])nc(?:[\s-]|$)|noncommercial|non-commercial", lower):
        restrictions.append("noncommercial")
    if re.search(r"(?:^|[\s-])nd(?:[\s-]|$)|no derivatives|no-derivatives", lower):
        restrictions.append("no_derivatives")
    if re.search(r"(?:^|[\s-])sa(?:[\s-]|$)|sharealike|share-alike", lower):
        restrictions.append("share_alike")
    is_creative_commons = (
        "cc " in lower
        or "creative commons" in lower
        or "creativecommons.org" in url_lower
        or lower in {"by", "by-sa", "by-nc", "by-nd", "by-nc-sa", "by-nc-nd"}
    )
    if restrictions and is_creative_commons:
        return {
            "name":raw, "url":url, "attribution_required":True,
            "verified":True, "restrictions":restrictions, "verification_source":"provider_metadata",
        }, "citation_only"
    if (
        lower == "by"
        or any(token in lower for token in ("cc by", "cc-by", "creative commons attribution"))
        or "/licenses/by/" in url_lower
    ):
        return {
            "name":raw, "url":url, "attribution_required":True,
            "verified":True, "restrictions":[], "verification_source":"provider_metadata",
        }, "attribution_required"
    if "open access" in lower:
        return {
            "name":raw, "url":url, "attribution_required":True,
            "verified":False, "restrictions":["license_not_specified"], "verification_source":None,
        }, "citation_only"
    return {
        "name":raw, "url":url, "attribution_required":True,
        "verified":False, "restrictions":["unknown_license"], "verification_source":None,
    }, "unknown"


def _public_remote_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname: return False
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return bool(addresses)
    except (OSError, ValueError):
        return False


def sanitize_svg(payload: bytes) -> bytes:
    root = ElementTree.fromstring(payload)
    forbidden = {"script", "foreignObject", "iframe", "object", "embed", "audio", "video"}
    for parent in list(root.iter()):
        for child in list(parent):
            if child.tag.rsplit("}", 1)[-1] in forbidden: parent.remove(child)
        for name in list(parent.attrib):
            value = parent.attrib[name]
            local_name = name.rsplit("}", 1)[-1].lower()
            if local_name.startswith("on") or (local_name in {"href", "src"} and not value.startswith(("#", "data:image/"))) or (local_name == "style" and re.search(r"url\s*\(", value, re.I)):
                del parent.attrib[name]
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


async def cache_candidate_preview(project_id: str, asset_id: str, url: str | None) -> tuple[str | None, str | None]:
    if not url or not await asyncio.to_thread(_public_remote_url, url): return None, None
    allowed = {"image/png":"png", "image/jpeg":"jpg", "image/webp":"webp", "image/gif":"gif", "image/svg+xml":"svg"}
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers={"User-Agent":"NatureFigureWorkbench/0.1"}) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                if not await asyncio.to_thread(_public_remote_url, str(response.url)): return None, None
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                extension = allowed.get(content_type)
                if not extension: return None, None
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 12 * 1024 * 1024: return None, None
        payload = sanitize_svg(bytes(data)) if extension == "svg" else bytes(data)
        digest = hashlib.sha256(payload).hexdigest()
        target = db.project_dir(project_id) / "assets" / f"{asset_id}.{extension}"
        target.write_bytes(payload)
        return str(target), digest
    except (httpx.HTTPError, OSError, ElementTree.ParseError):
        return None, None


def clean_source_title(value: str) -> str:
    class TextOnly(HTMLParser):
        def __init__(self):
            super().__init__(); self.parts = []
        def handle_data(self, data):
            self.parts.append(data)
    for _ in range(3):
        decoded = html.unescape(value)
        if decoded == value: break
        value = decoded
    parser = TextOnly(); parser.feed(value)
    return " ".join("".join(parser.parts).split())


async def search_open_assets(query: str, limit: int = 12) -> tuple[list[dict[str, Any]], dict[str, list[str] | bool]]:
    timeout = httpx.Timeout(12.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "NatureFigureWorkbench/0.1"}) as client:
        async def openverse() -> list[dict[str, Any]]:
            provider_results: list[dict[str, Any]] = []
            response = await client.get("https://api.openverse.org/v1/images/", params={"q": query, "page_size": min(limit, 10), "license_type": "commercial"})
            response.raise_for_status()
            for item in response.json().get("results", []):
                license_data, reuse = normalize_license(item.get("license"), item.get("license_url"))
                provider_results.append({"title": item.get("title") or query, "provider": "Openverse", "source_url": item.get("foreign_landing_url") or item.get("url"), "preview_url": item.get("thumbnail"), "author": item.get("creator"), "license": license_data, "reuse_status": reuse})
            return provider_results
        async def wikimedia() -> list[dict[str, Any]]:
            provider_results: list[dict[str, Any]] = []
            response = await client.get("https://commons.wikimedia.org/w/api.php", params={"action":"query","generator":"search","gsrsearch":query,"gsrnamespace":6,"gsrlimit":min(limit,8),"prop":"imageinfo","iiprop":"url|extmetadata","iiurlwidth":640,"format":"json","origin":"*"})
            response.raise_for_status()
            for page in response.json().get("query", {}).get("pages", {}).values():
                info = (page.get("imageinfo") or [{}])[0]; meta = info.get("extmetadata") or {}
                license_name = (meta.get("LicenseShortName") or {}).get("value")
                license_url = (meta.get("LicenseUrl") or {}).get("value")
                license_data, reuse = normalize_license(license_name, license_url)
                provider_results.append({"title": page.get("title", "").removeprefix("File:"), "provider":"Wikimedia Commons", "source_url": info.get("descriptionurl") or info.get("url"), "preview_url": info.get("thumburl") or info.get("url"), "author": (meta.get("Artist") or {}).get("value"), "license":license_data, "reuse_status":reuse})
            return provider_results
        async def europe_pmc() -> list[dict[str, Any]]:
            provider_results: list[dict[str, Any]] = []
            response = await client.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search", params={"query":query,"format":"json","pageSize":min(limit,5)})
            response.raise_for_status()
            for item in response.json().get("resultList", {}).get("result", []):
                source_url = f"https://europepmc.org/article/{item.get('source','MED')}/{item.get('id','')}"
                license_data, reuse = normalize_license("Open access" if item.get("isOpenAccess") == "Y" else "Citation only")
                provider_results.append({"title":clean_source_title(item.get("title") or query),"provider":"Europe PMC","source_url":source_url,"preview_url":None,"author":item.get("authorString"),"license":license_data,"reuse_status":"citation_only" if reuse != "safe_open" else reuse})
            return provider_results
        providers = ("Openverse", "Wikimedia Commons", "Europe PMC")
        provider_payloads = await asyncio.gather(openverse(), wikimedia(), europe_pmc(), return_exceptions=True)
    successful = [provider for provider,payload in zip(providers, provider_payloads) if not isinstance(payload, Exception)]
    failed = [provider for provider,payload in zip(providers, provider_payloads) if isinstance(payload, Exception)]
    results = [
        item
        for payload in provider_payloads
        if isinstance(payload, list)
        for item in payload
    ]
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(results):
        url = item.get("source_url")
        if not url or url in seen: continue
        seen.add(url)
        item["score"] = max(0.55, 0.96 - index * 0.025)
        item["rationale"] = f"与检索词“{query}”相关；许可状态已按来源元数据归一化。"
        deduped.append(item)
    return deduped[:limit], {
        "successful_providers":successful,
        "failed_providers":failed,
        "partial":bool(successful and failed),
    }


def _read_csv_frame(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path,encoding="utf-8-sig")
    except UnicodeDecodeError:
        # Common Windows/Chinese Excel CSV export encoding. Do not use latin-1
        # because it would silently turn invalid bytes into mojibake.
        return pd.read_csv(path,encoding="gb18030")


def _load_dataset_frame(path: Path, sheet_name: str | None = None) -> pd.DataFrame:
    try:
        if path.suffix.lower() == ".csv":
            if sheet_name:
                raise ValueError("CSV 数据文件不包含可选择的工作表")
            frame = _read_csv_frame(path)
        else:
            with pd.ExcelFile(path) as workbook:
                if sheet_name is not None and sheet_name not in workbook.sheet_names:
                    raise ValueError(f"工作簿中不存在工作表：{sheet_name}")
                frame = pd.read_excel(workbook,sheet_name=sheet_name if sheet_name is not None else 0)
    except (pd.errors.EmptyDataError, pd.errors.ParserError, ValueError, OSError, zipfile.BadZipFile) as exc:
        if isinstance(exc,ValueError) and (
            str(exc) == "CSV 数据文件不包含可选择的工作表"
            or str(exc).startswith("工作簿中不存在工作表：")
        ):
            raise
        raise ValueError("无法读取 CSV/XLSX 数据文件，请检查文件结构和编码") from exc
    if not len(frame.columns):
        raise ValueError("数据文件必须至少包含一个字段")
    if frame.empty:
        raise ValueError("数据文件必须至少包含一行观测数据")
    return frame


def _profile_dataset_frame(frame: pd.DataFrame) -> dict[str, Any]:
    columns = []
    for name in frame.columns:
        series = frame[name]
        columns.append({"name": str(name), "dtype": str(series.dtype), "non_null": int(series.notna().sum()), "unique": int(series.nunique(dropna=True)), "sample": [str(v) for v in series.dropna().head(3).tolist()]})
    numeric = [str(c) for c in frame.select_dtypes(include="number").columns]
    categorical = [str(c) for c in frame.columns if str(c) not in numeric]
    charts = []
    if numeric and categorical: charts += ["grouped_bar", "box"]
    if len(numeric) >= 2: charts += ["scatter", "heatmap"]
    if numeric: charts.append("line")
    return {"rows": int(len(frame)), "columns": columns, "recommended_charts": list(dict.fromkeys(charts)) or ["grouped_bar"]}


def profile_dataset(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".csv":
        return {**_profile_dataset_frame(_load_dataset_frame(path)),"selected_sheet":None,"sheets":[]}
    try:
        with pd.ExcelFile(path) as workbook:
            sheet_profiles = []
            for sheet_name in workbook.sheet_names:
                frame = pd.read_excel(workbook,sheet_name=sheet_name)
                if not len(frame.columns) or frame.empty:
                    continue
                sheet_profiles.append({"name":sheet_name,**_profile_dataset_frame(frame)})
    except (pd.errors.EmptyDataError, pd.errors.ParserError, ValueError, OSError, zipfile.BadZipFile) as exc:
        raise ValueError("无法读取 CSV/XLSX 数据文件，请检查文件结构和编码") from exc
    if not sheet_profiles:
        raise ValueError("Excel 工作簿中没有包含观测数据的工作表")
    selected = sheet_profiles[0]
    return {
        "rows":selected["rows"],
        "columns":selected["columns"],
        "recommended_charts":selected["recommended_charts"],
        "selected_sheet":selected["name"],
        "sheets":sheet_profiles,
    }


def _validate_chart_frame(frame: pd.DataFrame, spec: ChartSpec) -> None:
    required = [spec.x, spec.y, *([spec.group] if spec.group else [])]
    missing = [field for field in dict.fromkeys(required) if field not in frame.columns]
    if missing:
        raise ValueError(f"数据文件缺少所选字段：{', '.join(missing)}")
    if spec.chart_type != "heatmap" and frame[[spec.x, spec.y]].dropna().empty:
        raise ValueError("所选 X/Y 字段没有可绘制的成对非空观测")
    if spec.chart_type in {"grouped_bar", "line", "scatter", "box"} and not pd.api.types.is_numeric_dtype(frame[spec.y]):
        raise ValueError("所选响应变量必须是数值字段")
    if spec.chart_type == "scatter" and not pd.api.types.is_numeric_dtype(frame[spec.x]):
        raise ValueError("散点图的 X 字段必须是数值字段")
    for axis_name, field, scale in (("X", spec.x, spec.x_scale), ("Y", spec.y, spec.y_scale)):
        if scale != "log":
            continue
        if not pd.api.types.is_numeric_dtype(frame[field]):
            raise ValueError(f"{axis_name} 轴对数标尺需要数值字段")
        values = frame[field].dropna()
        if values.empty or (values <= 0).any():
            raise ValueError(f"{axis_name} 轴对数标尺要求所有有效数据严格大于 0")
    if spec.chart_type == "heatmap":
        numeric = frame.select_dtypes(include="number")
        varying = [column for column in numeric.columns if numeric[column].nunique(dropna=True) > 1]
        if len(varying) < 2:
            raise ValueError("相关性热图至少需要两个具有变化的数值字段")


def _chart_warnings(spec: ChartSpec) -> list[str]:
    warnings = []
    if not spec.independent_unit: warnings.append("未定义独立样本单位 n")
    if spec.chart_type in {"grouped_bar", "line", "box"} and not spec.error_bar: warnings.append("未定义误差线或不确定性表达")
    if not spec.statistical_test: warnings.append("未记录统计检验或不适用理由")
    return warnings


def _publication_font(*values: str | None) -> str:
    has_cjk = any(re.search(r"[\u3400-\u9fff]", value or "") for value in values)
    candidates = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Arial Unicode MS"] if has_cjk else ["Arial", "Helvetica"]
    for candidate in candidates:
        try:
            font_manager.findfont(candidate, fallback_to_default=False)
            return candidate
        except ValueError:
            continue
    return "DejaVu Sans"


CHART_PALETTES = {
    "nature": ["#0F4D92", "#8BCF8B", "#B64342", "#42949E", "#9A4D8E", "#767676"],
    "office": ["#4472C4", "#ED7D31", "#A5A5A5", "#FFC000", "#5B9BD5", "#70AD47"],
    "colorblind": ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00"],
    "grayscale": ["#222222", "#555555", "#888888", "#BBBBBB", "#DDDDDD", "#F0F0F0"],
}
MARKERS = {"auto":"o", "circle":"o", "square":"s", "triangle":"^", "none":None}


def _chart_font(spec: ChartSpec) -> str:
    has_cjk = any(re.search(r"[\u3400-\u9fff]", value or "") for value in (spec.title, spec.x_label, spec.y_label, spec.x, spec.y, spec.group))
    candidates = [spec.font_family]
    if has_cjk and spec.font_family != "Microsoft YaHei":
        candidates = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", spec.font_family]
    candidates += ["Arial", "Calibri", "DejaVu Sans"]
    for candidate in dict.fromkeys(candidates):
        try:
            font_manager.findfont(candidate, fallback_to_default=False)
            return candidate
        except ValueError:
            continue
    return "DejaVu Sans"


def _format_chart_data_label(value: Any) -> str:
    """Keep ordinary measurements readable and reserve exponent notation for extremes."""
    if pd.isna(value):
        return ""
    magnitude = abs(float(value))
    if magnitude >= 1_000_000 or (0 < magnitude < 0.001):
        return f"{value:.2e}"
    if magnitude >= 1:
        return f"{value:.2f}"
    return f"{value:.3f}"


def _apply_chart_axes(axis: Any, frame: pd.DataFrame, spec: ChartSpec) -> None:
    if spec.chart_type == "heatmap":
        axis.tick_params(labelsize=spec.font_size_pt)
        return
    if spec.x_scale == "log":
        if not pd.api.types.is_numeric_dtype(frame[spec.x]):
            raise ValueError("X 轴对数标尺需要数值字段")
        axis.set_xscale("log")
    if spec.y_scale == "log":
        if not pd.api.types.is_numeric_dtype(frame[spec.y]):
            raise ValueError("Y 轴对数标尺需要数值字段")
        axis.set_yscale("log")
    if spec.x_min is not None or spec.x_max is not None:
        axis.set_xlim(left=spec.x_min, right=spec.x_max)
    if spec.y_min is not None or spec.y_max is not None:
        axis.set_ylim(bottom=spec.y_min, top=spec.y_max)
    if spec.chart_type in {"line", "scatter"} and pd.api.types.is_numeric_dtype(frame[spec.x]):
        axis.xaxis.set_major_locator(MaxNLocator(nbins=spec.tick_count))
    axis.yaxis.set_major_locator(MaxNLocator(nbins=spec.tick_count))
    axis.minorticks_on() if spec.show_minor_ticks else axis.minorticks_off()
    axis.grid(False)
    if spec.grid_mode in {"x", "both"}:
        axis.xaxis.grid(True, color="#D9D9D9", linewidth=0.55, alpha=0.75)
    if spec.grid_mode in {"y", "both"}:
        axis.yaxis.grid(True, color="#D9D9D9", linewidth=0.55, alpha=0.75)
    axis.tick_params(axis="both", which="major", labelsize=spec.font_size_pt, width=0.8)
    axis.tick_params(axis="both", which="minor", width=0.55)


def _place_legend(axis: Any, spec: ChartSpec) -> None:
    legend = axis.get_legend()
    if not legend:
        return
    if spec.legend_position == "none":
        legend.remove()
        return
    positions = {
        "right": ("center left", (1.02, 0.5)),
        "left": ("center right", (-0.02, 0.5)),
        "top": ("lower center", (0.5, 1.02)),
        "bottom": ("upper center", (0.5, -0.14)),
        "inside_upper_right": ("upper right", None),
        "auto": ("best", None),
    }
    location, anchor = positions[spec.legend_position]
    legend.set_frame_on(False)
    legend.set_loc(location)
    if anchor is not None:
        legend.set_bbox_to_anchor(anchor)
    for text in legend.get_texts():
        text.set_fontsize(spec.font_size_pt)


def _office_value(value: Any) -> Any:
    if pd.isna(value):
        return None
    return value.item() if hasattr(value, "item") else value


def _keep_user_text_literal(cell: Any) -> None:
    """Prevent user-controlled text beginning with '=' from becoming an XLSX formula."""
    if isinstance(cell.value, str) and cell.value.startswith("="):
        cell.data_type = "s"


def _office_text_properties(font_family: str, font_size_pt: float, *, bold: bool = False) -> CharacterProperties:
    font = DrawingFont(typeface=font_family)
    return CharacterProperties(
        latin=font,
        ea=DrawingFont(typeface=font_family),
        cs=DrawingFont(typeface=font_family),
        sz=int(round(font_size_pt * 100)),
        b=bold,
    )


def _office_rich_text(font_family: str, font_size_pt: float, *, bold: bool = False) -> RichText:
    properties = _office_text_properties(font_family, font_size_pt, bold=bold)
    return RichText(
        p=[
            DrawingParagraph(
                pPr=ParagraphProperties(defRPr=properties),
                endParaRPr=_office_text_properties(font_family, font_size_pt, bold=bold),
            )
        ]
    )


def _style_office_title(title: Any, font_family: str, font_size_pt: float, *, bold: bool = False) -> None:
    if title is None or title.tx is None or title.tx.rich is None:
        return
    for paragraph in title.tx.rich.p:
        properties = _office_text_properties(font_family, font_size_pt, bold=bold)
        paragraph.pPr = paragraph.pPr or ParagraphProperties()
        paragraph.pPr.defRPr = properties
        paragraph.endParaRPr = _office_text_properties(font_family, font_size_pt, bold=bold)
        for run in paragraph.r:
            run.rPr = _office_text_properties(font_family, font_size_pt, bold=bold)


def create_office_chart_workbook(frame: pd.DataFrame, spec: ChartSpec, preview_path: Path, output_path: Path) -> None:
    """Create a portable XLSX with raw data, formula lineage, and a native chart when supported."""
    workbook = Workbook()
    raw_sheet = workbook.active
    raw_sheet.title = "Raw Data"
    raw_sheet.sheet_view.showGridLines = False
    raw_sheet.freeze_panes = "A2"
    safe_frame = frame.copy()
    safe_frame.columns = [str(column) for column in safe_frame.columns]
    for row in dataframe_to_rows(safe_frame, index=False, header=True):
        raw_sheet.append([_office_value(value) for value in row])
    for row in raw_sheet.iter_rows():
        for cell in row:
            _keep_user_text_literal(cell)
    header_fill = PatternFill("solid", fgColor="1F6F55")
    for cell in raw_sheet[1]:
        cell.fill = header_fill
        cell.font = Font(name="Calibri", bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="center")
    for index, column in enumerate(safe_frame.columns, 1):
        values = [str(column), *[str(value) for value in safe_frame[column].dropna().head(100)]]
        raw_sheet.column_dimensions[get_column_letter(index)].width = min(34, max(11, max(map(len, values)) + 2))
    if len(safe_frame):
        table = Table(displayName="RawResearchData", ref=f"A1:{get_column_letter(len(safe_frame.columns))}{len(safe_frame)+1}")
        table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium4", showRowStripes=True, showColumnStripes=False)
        raw_sheet.add_table(table)

    numeric_line = spec.chart_type == "line" and pd.api.types.is_numeric_dtype(safe_frame[spec.x])
    office_chart_mode = {
        "grouped_bar": "native clustered column chart",
        "line": "native XY line chart" if numeric_line else "native category line chart",
        "scatter": "native XY scatter chart; one series per group" if spec.group else "native XY scatter chart",
        "box": "editable source table plus publication preview",
        "heatmap": "editable source table plus publication preview",
    }[spec.chart_type]
    resolved_office_font = _chart_font(spec)
    notes = workbook.create_sheet("README")
    notes.sheet_view.showGridLines = False
    notes.column_dimensions["A"].width = 26
    notes.column_dimensions["B"].width = 80
    note_rows = [
        ("Purpose", "Editable Office companion for a Nature Workbench chart; Raw Data remains the source of truth."),
        ("Chart type", spec.chart_type),
        ("Title", spec.title),
        ("X field", spec.x),
        ("Y field", spec.y),
        ("Group field", spec.group or "None"),
        ("Source worksheet", spec.sheet_name or "CSV / first worksheet"),
        ("Canvas (mm)", f"{spec.width_mm:g} × {spec.height_mm:g}"),
        ("Font", f"{spec.font_family}, {spec.font_size_pt:g} pt"),
        ("Resolved Office font", resolved_office_font),
        ("Native chart mode", office_chart_mode),
        ("Grid", spec.grid_mode),
        ("X scale", spec.x_scale),
        ("Y scale", spec.y_scale),
        ("X bounds", f"{spec.x_min if spec.x_min is not None else 'auto'} … {spec.x_max if spec.x_max is not None else 'auto'}"),
        ("Y bounds", f"{spec.y_min if spec.y_min is not None else 'auto'} … {spec.y_max if spec.y_max is not None else 'auto'}"),
        ("Major ticks", str(spec.tick_count)),
        ("Minor ticks", "shown" if spec.show_minor_ticks else "hidden"),
        ("Legend", spec.legend_position),
        ("Palette", spec.palette),
        ("Line width", f"{spec.line_width:g} pt"),
        ("Marker", spec.marker),
        ("Data labels", "shown" if spec.show_data_labels else "hidden"),
        ("Statistics", f"n={spec.independent_unit or 'not defined'}; error={spec.error_bar or 'not defined'}; test={spec.statistical_test or 'not defined'}"),
        ("Portability", "Open in Microsoft Excel, LibreOffice Calc, or other OOXML-compatible software. SVG/PDF remain the submission-grade masters."),
    ]
    notes.append(["Field", "Value"])
    for key, value in note_rows:
        notes.append([key, value])
    for row in notes.iter_rows():
        for cell in row:
            _keep_user_text_literal(cell)
    for cell in notes[1]:
        cell.fill = header_fill
        cell.font = Font(name="Calibri", bold=True, color="FFFFFF")
    notes.freeze_panes = "A2"

    chart_sheet = workbook.create_sheet("Editable Chart")
    chart_sheet.sheet_view.showGridLines = False
    native_chart: Any | None = None
    series_titles: list[str] = []
    columns = list(safe_frame.columns)
    x_col = columns.index(spec.x) + 1
    y_col = columns.index(spec.y) + 1
    raw_last = len(safe_frame) + 1
    palette = CHART_PALETTES[spec.palette]

    if spec.chart_type in {"grouped_bar", "line"}:
        unique_x = list(dict.fromkeys(_office_value(value) for value in safe_frame[spec.x].dropna()))
        groups = list(dict.fromkeys(_office_value(value) for value in safe_frame[spec.group].dropna())) if spec.group else [spec.y]
        series_titles = [str(value) for value in groups]
        chart_sheet.append([spec.x, *groups])
        for cell in chart_sheet[1]:
            _keep_user_text_literal(cell)
        x_letter = get_column_letter(x_col)
        y_letter = get_column_letter(y_col)
        group_letter = get_column_letter(columns.index(spec.group) + 1) if spec.group else None
        for row_number, x_value in enumerate(unique_x, 2):
            chart_sheet.cell(row_number, 1, x_value)
            _keep_user_text_literal(chart_sheet.cell(row_number, 1))
            for group_index in range(len(groups)):
                formula = f"=AVERAGEIF('Raw Data'!${x_letter}$2:${x_letter}${raw_last},$A{row_number},'Raw Data'!${y_letter}$2:${y_letter}${raw_last})"
                if group_letter:
                    formula = f"=AVERAGEIFS('Raw Data'!${y_letter}$2:${y_letter}${raw_last},'Raw Data'!${x_letter}$2:${x_letter}${raw_last},$A{row_number},'Raw Data'!${group_letter}$2:${group_letter}${raw_last},{get_column_letter(group_index+2)}$1)"
                chart_sheet.cell(row_number, group_index + 2, formula)
        native_chart = BarChart() if spec.chart_type == "grouped_bar" else ScatterChart() if numeric_line else LineChart()
        if isinstance(native_chart, BarChart):
            native_chart.type = "col"
            native_chart.grouping = "clustered"
            native_chart.overlap = 0
        data = Reference(chart_sheet, min_col=2, max_col=len(groups)+1, min_row=1, max_row=len(unique_x)+1)
        categories = Reference(chart_sheet, min_col=1, min_row=2, max_row=len(unique_x)+1)
        if isinstance(native_chart, ScatterChart):
            native_chart.scatterStyle = "lineMarker"
            for group_index, group in enumerate(groups):
                values = Reference(
                    chart_sheet,
                    min_col=group_index + 2,
                    min_row=2,
                    max_row=len(unique_x) + 1,
                )
                native_chart.series.append(Series(values, categories, title=str(group)))
        else:
            native_chart.add_data(data, titles_from_data=True)
            native_chart.set_categories(categories)
    elif spec.chart_type == "scatter":
        groups = (
            list(dict.fromkeys(_office_value(value) for value in safe_frame[spec.group].dropna()))
            if spec.group
            else [spec.y]
        )
        series_titles = [str(value) for value in groups]
        headers: list[str] = []
        subsets: list[pd.DataFrame] = []
        for group in groups:
            subset = safe_frame[safe_frame[spec.group] == group] if spec.group else safe_frame
            subset = subset[[spec.x, spec.y]].dropna()
            subsets.append(subset)
            headers.extend([f"{group} · {spec.x}", f"{group} · {spec.y}"])
        chart_sheet.append(headers)
        for cell in chart_sheet[1]:
            _keep_user_text_literal(cell)
        for group_index, subset in enumerate(subsets):
            for row_offset, (_, row) in enumerate(subset.iterrows(), 2):
                chart_sheet.cell(row_offset, group_index * 2 + 1, _office_value(row[spec.x]))
                chart_sheet.cell(row_offset, group_index * 2 + 2, _office_value(row[spec.y]))
        native_chart = ScatterChart()
        native_chart.scatterStyle = "marker"
        for group_index, (group, subset) in enumerate(zip(groups, subsets, strict=True)):
            x_values = Reference(
                chart_sheet,
                min_col=group_index * 2 + 1,
                min_row=2,
                max_row=len(subset) + 1,
            )
            y_values = Reference(
                chart_sheet,
                min_col=group_index * 2 + 2,
                min_row=2,
                max_row=len(subset) + 1,
            )
            native_chart.series.append(Series(y_values, x_values, title=str(group)))

    if native_chart is not None:
        native_chart.title = spec.title
        native_chart.x_axis.title = spec.x_label or spec.x
        native_chart.y_axis.title = spec.y_label or spec.y
        native_chart.width = spec.width_mm / 10
        native_chart.height = spec.height_mm / 10
        native_chart.style = 10
        office_font = resolved_office_font
        _style_office_title(native_chart.title, office_font, spec.title_size_pt, bold=True)
        _style_office_title(native_chart.x_axis.title, office_font, spec.font_size_pt)
        _style_office_title(native_chart.y_axis.title, office_font, spec.font_size_pt)
        native_chart.x_axis.txPr = _office_rich_text(office_font, spec.font_size_pt)
        native_chart.y_axis.txPr = _office_rich_text(office_font, spec.font_size_pt)
        if isinstance(native_chart, ScatterChart):
            native_chart.x_axis.axPos = "b"
            native_chart.y_axis.axPos = "l"
        if spec.y_min is not None:
            native_chart.y_axis.scaling.min = spec.y_min
        if spec.y_max is not None:
            native_chart.y_axis.scaling.max = spec.y_max
        if spec.y_scale == "log":
            native_chart.y_axis.scaling.logBase = 10
        elif spec.y_min is not None and spec.y_max is not None:
            native_chart.y_axis.majorUnit = (spec.y_max - spec.y_min) / max(1, spec.tick_count - 1)
        if isinstance(native_chart, ScatterChart):
            if spec.x_min is not None:
                native_chart.x_axis.scaling.min = spec.x_min
            if spec.x_max is not None:
                native_chart.x_axis.scaling.max = spec.x_max
            if spec.x_scale == "log":
                native_chart.x_axis.scaling.logBase = 10
            elif spec.x_min is not None and spec.x_max is not None:
                native_chart.x_axis.majorUnit = (spec.x_max - spec.x_min) / max(1, spec.tick_count - 1)
        native_chart.x_axis.majorGridlines = ChartLines() if spec.grid_mode in {"x", "both"} else None
        native_chart.y_axis.majorGridlines = ChartLines() if spec.grid_mode in {"y", "both"} else None
        tick_mark = "out" if spec.show_minor_ticks else "none"
        native_chart.x_axis.minorTickMark = tick_mark
        native_chart.y_axis.minorTickMark = tick_mark
        if spec.legend_position == "none":
            native_chart.legend = None
        elif native_chart.legend:
            native_chart.legend.position = {"right":"r","left":"l","top":"t","bottom":"b","inside_upper_right":"tr","auto":"r"}[spec.legend_position]
            native_chart.legend.overlay = spec.legend_position == "inside_upper_right"
            native_chart.legend.txPr = _office_rich_text(office_font, spec.font_size_pt)
        for index, series in enumerate(native_chart.series):
            if index < len(series_titles):
                series.tx = SeriesLabel(v=series_titles[index])
            series.graphicalProperties.solidFill = palette[index % len(palette)].lstrip("#")
            series.graphicalProperties.line.solidFill = palette[index % len(palette)].lstrip("#")
            series.graphicalProperties.line.width = int(spec.line_width * 12700)
            if spec.chart_type == "scatter":
                series.graphicalProperties.line.noFill = True
            if hasattr(series, "marker"):
                series.marker.symbol = {
                    "auto":"circle", "circle":"circle", "square":"square",
                    "triangle":"triangle", "none":"circle" if spec.chart_type == "scatter" else "none",
                }[spec.marker]
        if spec.show_data_labels and spec.chart_type in {"grouped_bar", "line"}:
            native_chart.dLbls = DataLabelList()
            native_chart.dLbls.showVal = True
            native_chart.dLbls.numFmt = "0.00"
            native_chart.dLbls.txPr = _office_rich_text(office_font, max(5, spec.font_size_pt - 1))
        chart_sheet.add_chart(native_chart, "A1")
    else:
        chart_sheet["A1"] = "This chart type is preserved as an editable source table plus a publication preview."
        chart_sheet["A1"].font = Font(name="Calibri", bold=True, color="1F6F55")
        preview = OpenpyxlImage(str(preview_path))
        preview.width = round(spec.width_mm / 25.4 * 96)
        preview.height = round(spec.height_mm / 25.4 * 96)
        chart_sheet.add_image(preview, "A3")
    chart_sheet.column_dimensions["A"].width = 22
    for column in range(2, max(3, chart_sheet.max_column + 1)):
        chart_sheet.column_dimensions[get_column_letter(column)].width = 16
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    workbook.calculation.calcMode = "auto"
    workbook.save(output_path)


def render_chart(project_id: str, dataset_path: Path, spec: ChartSpec) -> tuple[dict[str, str], list[str], str]:
    frame = _load_dataset_frame(dataset_path,spec.sheet_name)
    _validate_chart_frame(frame, spec)
    chart_id = uuid.uuid4().hex
    output_dir = db.project_dir(project_id) / "charts" / chart_id
    output_dir.mkdir(parents=True, exist_ok=True)
    sns.set_theme(style="white")
    plot_font = _chart_font(spec)
    plt.rcParams.update({"font.family":"sans-serif","font.sans-serif":[plot_font,spec.font_family,"Arial","Microsoft YaHei","DejaVu Sans"],"font.size":spec.font_size_pt,"svg.fonttype":"none","pdf.fonttype":42,"axes.unicode_minus":False,"axes.spines.top":False,"axes.spines.right":False,"legend.frameon":False})
    figure, axis = plt.subplots(figsize=(spec.width_mm / 25.4, spec.height_mm / 25.4), constrained_layout=True)
    palette = CHART_PALETTES[spec.palette]
    hue_style = (
        {"hue": spec.group, "palette": sns.color_palette(palette, n_colors=max(1, int(frame[spec.group].nunique())))}
        if spec.group else {"color": palette[0]}
    )
    # A draft may be rendered before the author supplies statistical metadata,
    # but it must not silently imply a 95% confidence interval.
    errorbar = {"ci95": ("ci", 95), "95% CI": ("ci", 95), "sd": "sd", "se": "se", "none": None}.get(spec.error_bar or "")
    marker = MARKERS[spec.marker]
    if spec.chart_type == "grouped_bar": sns.barplot(data=frame, x=spec.x, y=spec.y, ax=axis, errorbar=errorbar, edgecolor="#272727", linewidth=max(0.4, spec.line_width * 0.55), **hue_style)
    elif spec.chart_type == "line": sns.lineplot(data=frame, x=spec.x, y=spec.y, marker=marker, linewidth=spec.line_width, errorbar=errorbar, ax=axis, **hue_style)
    elif spec.chart_type == "scatter": sns.scatterplot(data=frame, x=spec.x, y=spec.y, marker=marker or "o", linewidth=max(0.4, spec.line_width * 0.45), ax=axis, **hue_style)
    elif spec.chart_type == "box": sns.boxplot(data=frame, x=spec.x, y=spec.y, linewidth=spec.line_width, ax=axis, **hue_style)
    else:
        numeric = frame.select_dtypes(include="number")
        sns.heatmap(numeric.corr(), cmap=sns.diverging_palette(145, 25, as_cmap=True), center=0, ax=axis)
    axis.set_title(spec.title, loc="left", fontweight="bold", fontsize=spec.title_size_pt, pad=12)
    if spec.chart_type == "heatmap":
        axis.set_xlabel(spec.x_label or "", fontsize=spec.font_size_pt)
        axis.set_ylabel(spec.y_label or "", fontsize=spec.font_size_pt)
    else:
        axis.set_xlabel(spec.x_label or spec.x, fontsize=spec.font_size_pt)
        axis.set_ylabel(spec.y_label or spec.y, fontsize=spec.font_size_pt)
    _apply_chart_axes(axis, frame, spec)
    _place_legend(axis, spec)
    if spec.show_data_labels and spec.chart_type == "grouped_bar":
        for container in axis.containers:
            if hasattr(container, "patches"):
                labels = [_format_chart_data_label(value) for value in container.datavalues]
                axis.bar_label(container, labels=labels, padding=2, fontsize=max(5, spec.font_size_pt - 1))
    outputs: dict[str, str] = {}
    for ext, kwargs in (("svg", {}), ("pdf", {}), ("png", {"dpi":300}), ("tiff", {"dpi":600})):
        target = output_dir / f"figure.{ext}"
        figure.savefig(target, bbox_inches="tight", facecolor="white", **kwargs)
        outputs[ext] = str(target)
    plt.close(figure)
    script = output_dir / "reproduce.py"
    script.write_text(textwrap.dedent(f'''\
        import pandas as pd
        import seaborn as sns
        import matplotlib.pyplot as plt
        from matplotlib import font_manager

        data = pd.read_{'csv' if dataset_path.suffix.lower()=='.csv' else 'excel'}(r"{dataset_path}")
        sns.set_theme(style="whitegrid")
        candidates = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Arial Unicode MS"] if any("\\u3400" <= character <= "\\u9fff" for character in {spec.title!r}) else ["Arial", "Helvetica"]
        plot_font = "DejaVu Sans"
        for candidate in candidates:
            try:
                font_manager.findfont(candidate, fallback_to_default=False)
                plot_font = candidate
                break
            except ValueError:
                pass
        plt.rcParams.update({{"font.family":"sans-serif","font.sans-serif":[plot_font,"Arial","DejaVu Sans"],"svg.fonttype":"none","axes.unicode_minus":False}})
        ax = sns.{ {'grouped_bar':'barplot','line':'lineplot','scatter':'scatterplot','box':'boxplot'}.get(spec.chart_type,'heatmap') }(data=data, x="{spec.x}", y="{spec.y}"{f', hue="{spec.group}"' if spec.group else ''})
        ax.set_title({spec.title!r}, loc="left")
        plt.savefig("figure.svg", bbox_inches="tight")
    '''), encoding="utf-8")
    (output_dir / "chart-spec.json").write_text(json.dumps(spec.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    source_copy = output_dir / f"source-data{dataset_path.suffix.lower()}"
    shutil.copy2(dataset_path, source_copy)
    (output_dir / "README.md").write_text(
        "# Portable scientific chart package\n\n"
        "- `figure.svg` and `figure.pdf` are the editable-text publication masters.\n"
        "- `figure.png` and `figure.tiff` are raster delivery formats.\n"
        "- `editable-office-chart.xlsx` stores user text as literal OOXML strings and contains an editable native chart where supported.\n"
        "- `source-data.*` is an unchanged provenance copy. Treat it as untrusted input and inspect it before opening directly in spreadsheet software.\n"
        "- Run `reproduce.py` beside the source data to regenerate the four figure formats without a desktop graphics backend.\n",
        encoding="utf-8",
    )
    script.write_text(portable_chart_script(source_copy.name, spec, same_directory=True), encoding="utf-8")
    office_path = output_dir / "editable-office-chart.xlsx"
    create_office_chart_workbook(frame, spec, Path(outputs["png"]), office_path)
    outputs["xlsx"] = str(office_path)
    package_path = output_dir / "chart-package.zip"
    with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in output_dir.iterdir():
            if item.is_file() and item != package_path:
                archive.write(item, item.name)
    outputs["package"] = str(package_path)
    return outputs, _chart_warnings(spec), chart_id


def portable_chart_script(dataset_name: str, spec: ChartSpec, same_directory: bool = False) -> str:
    """Build a self-contained script that reads the dataset shipped in an export bundle."""
    spec_json = json.dumps(spec.model_dump(), ensure_ascii=False)
    root_setup = "" if same_directory else 'ROOT = Path(__file__).resolve().parents[1]\n'
    data_path_expression = (
        f"Path(__file__).with_name({dataset_name!r})"
        if same_directory
        else f'ROOT / "data" / {dataset_name!r}'
    )
    return textwrap.dedent(f'''\
        from pathlib import Path
        import json
        import pandas as pd
        import seaborn as sns
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        from matplotlib.ticker import MaxNLocator

        def format_data_label(value):
            if pd.isna(value):
                return ""
            magnitude = abs(float(value))
            if magnitude >= 1_000_000 or (0 < magnitude < 0.001):
                return f"{{value:.2e}}"
            if magnitude >= 1:
                return f"{{value:.2f}}"
            return f"{{value:.3f}}"

        {root_setup.rstrip()}
        DATA_PATH = {data_path_expression}
        SPEC = json.loads({spec_json!r})
        if DATA_PATH.suffix.lower() == ".csv":
            try:
                data = pd.read_csv(DATA_PATH, encoding="utf-8-sig")
            except UnicodeDecodeError:
                data = pd.read_csv(DATA_PATH, encoding="gb18030")
        else:
            data = pd.read_excel(DATA_PATH, sheet_name=SPEC.get("sheet_name") or 0)
        sns.set_theme(style="white")
        has_cjk = any("\\u3400" <= character <= "\\u9fff" for value in (SPEC.get("title"), SPEC.get("x"), SPEC.get("y"), SPEC.get("group")) for character in (value or ""))
        candidates = ([SPEC["font_family"]] if not has_cjk or SPEC["font_family"] == "Microsoft YaHei" else []) + (["Microsoft YaHei", "SimHei", "Noto Sans CJK SC"] if has_cjk else []) + [SPEC["font_family"], "Arial", "DejaVu Sans"]
        plot_font = "DejaVu Sans"
        for candidate in dict.fromkeys(candidates):
            try:
                font_manager.findfont(candidate, fallback_to_default=False)
                plot_font = candidate
                break
            except ValueError:
                pass
        plt.rcParams.update({{"font.family":"sans-serif","font.sans-serif":[plot_font,SPEC["font_family"],"Arial","Microsoft YaHei","DejaVu Sans"],"font.size":SPEC["font_size_pt"],"svg.fonttype":"none","pdf.fonttype":42,"axes.unicode_minus":False,"axes.spines.top":False,"axes.spines.right":False,"legend.frameon":False}})
        figure, axis = plt.subplots(figsize=(SPEC["width_mm"]/25.4, SPEC["height_mm"]/25.4), constrained_layout=True)
        palettes = {{
            "nature":["#0F4D92","#8BCF8B","#B64342","#42949E","#9A4D8E","#767676"],
            "office":["#4472C4","#ED7D31","#A5A5A5","#FFC000","#5B9BD5","#70AD47"],
            "colorblind":["#0072B2","#E69F00","#009E73","#CC79A7","#56B4E9","#D55E00"],
            "grayscale":["#222222","#555555","#888888","#BBBBBB","#DDDDDD","#F0F0F0"],
        }}
        palette = palettes[SPEC["palette"]]
        group_count = max(1, int(data[SPEC["group"]].nunique())) if SPEC.get("group") else 1
        hue_style = {{"hue": SPEC["group"], "palette": sns.color_palette(palette, n_colors=group_count)}} if SPEC.get("group") else {{"color": palette[0]}}
        errorbar = {{"ci95": ("ci", 95), "95% CI": ("ci", 95), "sd": "sd", "se": "se", "none": None}}.get(SPEC.get("error_bar"))
        marker = {{"auto":"o","circle":"o","square":"s","triangle":"^","none":None}}[SPEC["marker"]]
        if SPEC["chart_type"] == "grouped_bar":
            sns.barplot(data=data, x=SPEC["x"], y=SPEC["y"], errorbar=errorbar, edgecolor="#272727", linewidth=max(0.4, SPEC["line_width"]*0.55), ax=axis, **hue_style)
        elif SPEC["chart_type"] == "line":
            sns.lineplot(data=data, x=SPEC["x"], y=SPEC["y"], marker=marker, linewidth=SPEC["line_width"], errorbar=errorbar, ax=axis, **hue_style)
        elif SPEC["chart_type"] == "scatter":
            sns.scatterplot(data=data, x=SPEC["x"], y=SPEC["y"], marker=marker or "o", linewidth=max(0.4, SPEC["line_width"]*0.45), ax=axis, **hue_style)
        elif SPEC["chart_type"] == "box":
            sns.boxplot(data=data, x=SPEC["x"], y=SPEC["y"], linewidth=SPEC["line_width"], ax=axis, **hue_style)
        else:
            sns.heatmap(data.select_dtypes(include="number").corr(), cmap=sns.diverging_palette(145, 25, as_cmap=True), center=0, ax=axis)
        axis.set_title(SPEC["title"], loc="left", fontweight="bold", fontsize=SPEC["title_size_pt"], pad=12)
        if SPEC["chart_type"] != "heatmap":
            axis.set_xlabel(SPEC.get("x_label") or SPEC["x"], fontsize=SPEC["font_size_pt"])
            axis.set_ylabel(SPEC.get("y_label") or SPEC["y"], fontsize=SPEC["font_size_pt"])
            if SPEC["x_scale"] == "log":
                if (data[SPEC["x"]] <= 0).any(): raise ValueError("Logarithmic X scale requires strictly positive values")
                axis.set_xscale("log")
            if SPEC["y_scale"] == "log":
                if (data[SPEC["y"]] <= 0).any(): raise ValueError("Logarithmic Y scale requires strictly positive values")
                axis.set_yscale("log")
            if SPEC.get("x_min") is not None or SPEC.get("x_max") is not None: axis.set_xlim(left=SPEC.get("x_min"), right=SPEC.get("x_max"))
            if SPEC.get("y_min") is not None or SPEC.get("y_max") is not None: axis.set_ylim(bottom=SPEC.get("y_min"), top=SPEC.get("y_max"))
            if SPEC["chart_type"] in {{"line","scatter"}} and pd.api.types.is_numeric_dtype(data[SPEC["x"]]): axis.xaxis.set_major_locator(MaxNLocator(nbins=SPEC["tick_count"]))
            axis.yaxis.set_major_locator(MaxNLocator(nbins=SPEC["tick_count"]))
            axis.minorticks_on() if SPEC["show_minor_ticks"] else axis.minorticks_off()
            axis.grid(False)
            if SPEC["grid_mode"] in {{"x","both"}}: axis.xaxis.grid(True, color="#D9D9D9", linewidth=0.55, alpha=0.75)
            if SPEC["grid_mode"] in {{"y","both"}}: axis.yaxis.grid(True, color="#D9D9D9", linewidth=0.55, alpha=0.75)
        else:
            axis.set_xlabel(SPEC.get("x_label") or "", fontsize=SPEC["font_size_pt"])
            axis.set_ylabel(SPEC.get("y_label") or "", fontsize=SPEC["font_size_pt"])
        legend = axis.get_legend()
        if legend:
            positions = {{"right":("center left",(1.02,0.5)),"left":("center right",(-0.02,0.5)),"top":("lower center",(0.5,1.02)),"bottom":("upper center",(0.5,-0.14)),"inside_upper_right":("upper right",None),"auto":("best",None)}}
            if SPEC["legend_position"] == "none": legend.remove()
            else:
                location, anchor = positions[SPEC["legend_position"]]
                legend.set_loc(location)
                if anchor is not None: legend.set_bbox_to_anchor(anchor)
        if SPEC["show_data_labels"] and SPEC["chart_type"] == "grouped_bar":
            for container in axis.containers:
                if hasattr(container, "patches"):
                    labels = [format_data_label(value) for value in container.datavalues]
                    axis.bar_label(container, labels=labels, padding=2, fontsize=max(5, SPEC["font_size_pt"]-1))
        figure.savefig(Path(__file__).with_name("reproduced.svg"), bbox_inches="tight", facecolor="white")
        figure.savefig(Path(__file__).with_name("reproduced.pdf"), bbox_inches="tight", facecolor="white")
        figure.savefig(Path(__file__).with_name("reproduced.png"), dpi=300, bbox_inches="tight", facecolor="white")
        figure.savefig(Path(__file__).with_name("reproduced.tiff"), dpi=600, bbox_inches="tight", facecolor="white")
        plt.close(figure)
    ''')


def _load_panel_image(path: Path) -> Image.Image:
    if path.suffix.lower() == ".svg":
        document = fitz.open(path)
        try:
            page = document[0]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
            return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        finally:
            document.close()
    with Image.open(path) as source:
        source.seek(0)
        return ImageOps.exif_transpose(source).convert("RGB")


def render_composition(
    project_id: str,
    composition_id: str,
    spec: FigureComposition,
    panel_sources: list[dict[str, Any]],
) -> tuple[dict[str, str], list[str]]:
    """Render a traceable multi-panel figure using the selected Python backend."""
    panel_count = len(panel_sources)
    if panel_count < 1:
        raise ValueError("At least one panel is required")
    limits = {"1x2":2,"2x2":4,"hero_side":4,"vertical":6}
    if panel_count > limits[spec.template]:
        raise ValueError(f"Template {spec.template} accepts at most {limits[spec.template]} panels")

    width_in = spec.width_mm / 25.4
    automatic_ratios = {"1x2":2.0,"2x2":1.2,"hero_side":1.45,"vertical":0.68}
    ratio = {"1:1":1.0,"4:3":4/3,"16:9":16/9}.get(spec.aspect_ratio, automatic_ratios[spec.template])
    height_in = width_in / ratio
    plt.rcParams.update({
        "font.family":"sans-serif",
        "font.sans-serif":["Arial","Microsoft YaHei","DejaVu Sans"],
        "svg.fonttype":"none",
        "pdf.fonttype":42,
    })
    figure = plt.figure(figsize=(width_in, height_in), facecolor="white", layout=None)
    axes: list[Any] = []
    if spec.template == "1x2":
        grid = figure.add_gridspec(1, panel_count)
        axes = [figure.add_subplot(grid[0,index]) for index in range(panel_count)]
    elif spec.template == "2x2":
        rows = 1 if panel_count <= 2 else 2
        columns = min(2, panel_count)
        grid = figure.add_gridspec(rows, columns)
        axes = [figure.add_subplot(grid[index // columns,index % columns]) for index in range(panel_count)]
    elif spec.template == "vertical":
        grid = figure.add_gridspec(panel_count, 1)
        axes = [figure.add_subplot(grid[index,0]) for index in range(panel_count)]
    elif panel_count == 1:
        axes = [figure.add_subplot(111)]
    else:
        supporting = panel_count - 1
        grid = figure.add_gridspec(supporting, 2, width_ratios=[1.45,1.0])
        axes = [figure.add_subplot(grid[:,0])]
        axes.extend(figure.add_subplot(grid[index,1]) for index in range(supporting))

    spacing = min(0.42, spec.gap_px / 44)
    figure.subplots_adjust(left=0.015,right=0.985,top=0.985,bottom=0.015,wspace=spacing,hspace=spacing)
    figure.canvas.draw()
    warnings: list[str] = []
    for index, (axis, source) in enumerate(zip(axes, panel_sources)):
        path = Path(source["path"])
        image = _load_panel_image(path)
        zoom = float(spec.panels[index].crop_zoom if spec.panels else 1.0)
        if zoom > 1:
            crop_width = max(1, round(image.width / zoom)); crop_height = max(1, round(image.height / zoom))
            left = (image.width - crop_width) // 2; top = (image.height - crop_height) // 2
            image = image.crop((left, top, left + crop_width, top + crop_height))
        bounds = axis.get_window_extent()
        target_width = max(160, round(bounds.width * 2)); target_height = max(120, round(bounds.height * 2))
        required_width = round(bounds.width / figure.dpi * spec.dpi)
        required_height = round(bounds.height / figure.dpi * spec.dpi)
        if image.width < required_width * 0.75 or image.height < required_height * 0.75:
            warnings.append(f"面板 {index + 1} 的源图分辨率低于 {spec.dpi} dpi 输出需求")
        if source.get("kind") == "chart":
            contained = ImageOps.contain(image,(target_width,target_height),method=Image.Resampling.LANCZOS)
            fitted = Image.new("RGB",(target_width,target_height),"white")
            fitted.paste(contained,((target_width-contained.width)//2,(target_height-contained.height)//2))
        else:
            fitted = ImageOps.fit(image, (target_width,target_height), method=Image.Resampling.LANCZOS, centering=(0.5,0.5))
        axis.imshow(fitted, interpolation="lanczos", aspect="auto")
        axis.set_xticks([]); axis.set_yticks([])
        for spine in axis.spines.values(): spine.set_visible(False)
        label = spec.panels[index].label if spec.panels and spec.panels[index].label else (spec.labels[index] if index < len(spec.labels) else chr(97 + index))
        axis.text(0.018,0.975,label,transform=axis.transAxes,ha="left",va="top",fontsize=8 if spec.width_mm==180 else 7,fontweight="bold",color="#111111",bbox={"boxstyle":"round,pad=0.22","facecolor":"white","edgecolor":"none","alpha":0.9})

    output_dir = db.project_dir(project_id) / "compositions" / composition_id
    output_dir.mkdir(parents=True, exist_ok=True)
    svg_target = output_dir / "composition.svg"
    pdf_target = output_dir / "composition.pdf"
    png_target = output_dir / "composition.png"
    tiff_target = output_dir / "composition.tiff"
    figure.savefig(svg_target,format="svg",dpi=300,facecolor="white",bbox_inches=None)
    figure.savefig(pdf_target,format="pdf",dpi=300,facecolor="white",bbox_inches=None)
    figure.savefig(png_target,format="png",dpi=min(300,spec.dpi),facecolor="white",bbox_inches=None)
    figure.savefig(tiff_target,format="tiff",dpi=spec.dpi,facecolor="white",bbox_inches=None,pil_kwargs={"compression":"tiff_lzw"})
    outputs = {"svg":str(svg_target),"pdf":str(pdf_target),"png":str(png_target),"tiff":str(tiff_target)}
    plt.close(figure)
    return outputs, list(dict.fromkeys(warnings))


def create_checkpoint(project_id: str, label: str) -> str:
    checkpoint_id = uuid.uuid4().hex
    folder = db.project_dir(project_id) / "checkpoints"
    manifest = folder / f"{checkpoint_id}.json"
    tables = {
        table: db.fetch_all(f"SELECT {','.join(columns)} FROM {table} WHERE project_id=?", (project_id,))
        for table, columns in CHECKPOINT_TABLE_COLUMNS.items()
    }
    payload = {
        "version": 2, "id":checkpoint_id, "project_id":project_id, "label":label,
        "created_at":utcnow(), "project":db.fetch_one("SELECT * FROM projects WHERE id=?",(project_id,)),
        "tables": tables,
    }
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    db.execute("INSERT INTO checkpoints VALUES (?,?,?,?,?)", (checkpoint_id, project_id, label, str(manifest), payload["created_at"]))
    return checkpoint_id


def _latest_decision_actions(decisions: list[dict]) -> dict[tuple[str, str], str]:
    actions: dict[tuple[str, str], str] = {}
    for decision in decisions:
        actions[(decision["target_type"], decision["target_id"])] = decision["action"]
    return actions


def _asset_raster(asset: dict, figure_dir: Path, project_root: Path) -> Path | None:
    raw_path = asset.get("local_path")
    if not raw_path: return None
    source = Path(raw_path).resolve()
    if not source.exists() or not source.is_relative_to(project_root.resolve()): return None
    target = figure_dir / f"asset-{asset['id']}.png"
    image = _load_panel_image(source).convert("RGB")
    try: image.save(target, format="PNG", optimize=True)
    finally: image.close()
    return target


def _collect_figure_bindings(
    project_id: str,
    anchors: list[dict],
    assets: list[dict],
    charts: list[dict],
    compositions: list[dict],
    decisions: list[dict],
    figure_dir: Path,
) -> list[dict]:
    """Resolve one traceable figure stream per anchor for document backfill."""
    root = db.project_dir(project_id).resolve()
    anchor_by_id = {anchor["id"]: anchor for anchor in anchors}
    asset_by_id = {asset["id"]: asset for asset in assets}
    actions = _latest_decision_actions(decisions)
    latest_composition: dict[str, tuple[dict, dict]] = {}
    for composition in compositions:
        spec = json.loads(composition["spec_json"])
        anchor_id = spec.get("anchor_id")
        if anchor_id in anchor_by_id: latest_composition[anchor_id] = (composition, spec)
    latest_chart: dict[str, tuple[dict, dict]] = {}
    for chart in charts:
        spec = json.loads(chart["spec_json"])
        anchor_id = spec.get("anchor_id")
        if anchor_id in anchor_by_id: latest_chart[anchor_id] = (chart, spec)

    bindings: list[dict] = []
    for anchor in anchors:
        anchor_id = anchor["id"]
        if anchor_id in latest_composition:
            composition, spec = latest_composition[anchor_id]
            source = Path(json.loads(composition["outputs_json"]).get("png", "")).resolve()
            if source.exists() and source.is_relative_to(root):
                filename = f"composition-{composition['id']}.png"
                target = figure_dir / filename
                if source != target.resolve(): shutil.copy2(source, target)
                source_notes: list[str] = []
                for panel in spec.get("panels", []):
                    if panel.get("kind") != "asset": continue
                    asset = asset_by_id.get(panel.get("source_id"))
                    if asset:
                        license_name = json.loads(asset["license_json"]).get("name", "许可未知")
                        source_notes.append(f"{asset['title']}，{asset['provider']}，{license_name}，{asset['source_url']}")
                bindings.append({
                    "anchor_id":anchor_id, "kind":"composition", "figure_id":composition["id"],
                    "path":str(target), "relative_path":f"figures/{filename}",
                    "caption":spec.get("caption") or f"{anchor['section']}的多面板配图。",
                    "sources":source_notes,
                })
                continue
        if anchor_id in latest_chart:
            chart, spec = latest_chart[anchor_id]
            source = Path(json.loads(chart["outputs_json"]).get("png", "")).resolve()
            if source.exists() and source.is_relative_to(root):
                filename = f"{chart['id']}.png"; target = figure_dir / filename
                if source != target.resolve(): shutil.copy2(source, target)
                bindings.append({
                    "anchor_id":anchor_id, "kind":"chart", "figure_id":chart["id"],
                    "path":str(target), "relative_path":f"figures/{filename}",
                    "caption":spec.get("title") or f"{anchor['section']}的数据图。", "sources":[],
                })
                continue
        for asset in assets:
            if asset["anchor_id"] != anchor_id or actions.get(("asset", asset["id"])) != "accept": continue
            license_record = json.loads(asset["license_json"])
            if asset["reuse_status"] not in {"safe_open", "attribution_required"} or not license_record.get("verified"): continue
            target = _asset_raster(asset, figure_dir, root)
            if not target: continue
            bindings.append({
                "anchor_id":anchor_id, "kind":"asset", "figure_id":asset["id"],
                "path":str(target), "relative_path":f"figures/{target.name}",
                "caption":asset["title"],
                "sources":[f"{asset['provider']}，{asset.get('author') or '作者未标注'}，{license_record.get('name','许可未知')}，{asset['source_url']}"],
            })
    for number, binding in enumerate(bindings, 1): binding["number"] = number
    return bindings


def _insert_paragraph_after(paragraph: Paragraph) -> Paragraph:
    node = OxmlElement("w:p")
    paragraph._p.addnext(node)
    return Paragraph(node, paragraph._parent)


def _replace_paragraph_text(paragraph: Paragraph, text: str) -> None:
    if paragraph.text.strip() == text: return
    if paragraph.runs:
        paragraph.runs[0].text = text
        for run in paragraph.runs[1:]: run.text = ""
    else: paragraph.add_run(text)


def _write_illustrated_docx(source: Path, output: Path, anchors: list[dict], bindings: list[dict]) -> None:
    document = DocxDocument(source)
    original_paragraphs = document_paragraphs(document)
    anchor_by_id = {anchor["id"]: anchor for anchor in anchors}
    for anchor in anchors:
        index = int(anchor["paragraph_index"])
        if 0 <= index < len(original_paragraphs): _replace_paragraph_text(original_paragraphs[index], anchor["text"])
    grouped: dict[str, list[dict]] = {}
    for binding in bindings: grouped.setdefault(binding["anchor_id"], []).append(binding)
    for anchor in sorted(anchors, key=lambda item: int(item["paragraph_index"]), reverse=True):
        index = int(anchor["paragraph_index"])
        if not (0 <= index < len(original_paragraphs)): continue
        cursor = original_paragraphs[index]
        for binding in grouped.get(anchor["id"], []):
            image_paragraph = _insert_paragraph_after(cursor)
            image_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            image_paragraph.paragraph_format.keep_with_next = True
            section = document.sections[0]
            available_inches = max(1.0, (section.page_width - section.left_margin - section.right_margin) / 914400)
            inline = image_paragraph.add_run().add_picture(binding["path"], width=Inches(min(6.5, available_inches)))
            inline._inline.docPr.set("descr", binding["caption"][:250])
            inline._inline.docPr.set("title", f"Figure {binding['number']}")
            caption = _insert_paragraph_after(image_paragraph)
            try: caption.style = document.styles["Caption"]
            except KeyError: pass
            caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
            caption.paragraph_format.keep_together = True
            caption.add_run(f"图 {binding['number']}｜{binding['caption']}")
            if binding["sources"]: caption.add_run(" 来源：" + "；".join(binding["sources"])).italic = True
            cursor = caption
    document.add_heading("配图与来源审计附录", level=1)
    document.add_paragraph("本文件从原稿副本生成，已应用工作区全文修订并按段落锚点插入已确认图件；原始文件未被覆盖。")
    for binding in bindings:
        document.add_paragraph(f"图 {binding['number']}｜{binding['caption']}" + ((" — " + "；".join(binding["sources"])) if binding["sources"] else ""))
    for name in ['Normal','Title','Caption',*[f'Heading {i}' for i in range(1,10)]]:
        if name not in document.styles:continue
        style=document.styles[name];style.font.name='Times New Roman'
        fonts=style.element.get_or_add_rPr().get_or_add_rFonts()
        for slot,font in [('ascii','Times New Roman'),('hAnsi','Times New Roman'),('eastAsia','SimSun')]:fonts.set(qn('w:'+slot),font)
    for paragraph in document_paragraphs(document):
        for run in paragraph.runs:
            run.font.name='Times New Roman'
            run._element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),'SimSun')
    document.save(output)


def _latex_escape(text: str) -> str:
    replacements = {"\\":"\\textbackslash{}", "&":"\\&", "%":"\\%", "$":"\\$", "#":"\\#", "_":"\\_", "{":"\\{", "}":"\\}"}
    return "".join(replacements.get(character, character) for character in text)


def _figure_source_lines(binding: dict, tex: bool) -> list[str]:
    caption = f"图 {binding['number']}｜{binding['caption']}"
    if binding["sources"]: caption += " 来源：" + "；".join(binding["sources"])
    if tex:
        return ["\\begin{figure}[htbp]", "\\centering", f"\\includegraphics[width=\\linewidth]{{{binding['relative_path']}}}", f"\\caption{{{_latex_escape(caption)}}}", f"\\label{{fig:nfw-{binding['number']}}}", "\\end{figure}"]
    return [f"![{caption}]({binding['relative_path']})", "", f"*{caption}*"]


def _portable_audit_paths(value: object, root: Path) -> object:
    if isinstance(value, dict):
        output: dict[str, object] = {}
        for key,item in value.items():
            if key.endswith("_json") and isinstance(item, str):
                try:
                    decoded = json.loads(item)
                    output[key] = json.dumps(_portable_audit_paths(decoded, root), ensure_ascii=False)
                    continue
                except (TypeError, json.JSONDecodeError):
                    pass
            output[key] = _portable_audit_paths(item, root)
        return output
    if isinstance(value, list):
        return [_portable_audit_paths(item, root) for item in value]
    if isinstance(value, str):
        candidate = Path(value)
        if candidate.is_absolute():
            try: return candidate.resolve(strict=False).relative_to(root.resolve()).as_posix()
            except (OSError, ValueError): return value
    return value


def _write_illustrated_source(source: Path, output: Path, anchors: list[dict], bindings: list[dict]) -> None:
    raw = source.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()
    blocks = source_blocks(raw, source.suffix.lower())
    grouped: dict[str, list[dict]] = {}
    for binding in bindings: grouped.setdefault(binding["anchor_id"], []).append(binding)
    tex = source.suffix.lower() == ".tex"
    if len(blocks) == len(anchors):
        for block, anchor in reversed(list(zip(blocks, anchors, strict=True))):
            replacement = [anchor["text"]]
            for binding in grouped.get(anchor["id"], []): replacement += ["", *_figure_source_lines(binding, tex)]
            lines[block.start_line:block.end_line + 1] = replacement
    else:
        lines = []
        active_section = None
        for anchor in anchors:
            if anchor["section"] != active_section:
                active_section = anchor["section"]
                lines += [f"\\section{{{active_section}}}" if tex else f"## {active_section}", ""]
            lines += [anchor["text"], ""]
            for binding in grouped.get(anchor["id"], []): lines += [*_figure_source_lines(binding, tex), ""]
    if tex and bindings and not any("\\usepackage{graphicx}" in line for line in lines):
        document_class = next((index for index, line in enumerate(lines) if line.strip().startswith("\\documentclass")), None)
        lines.insert((document_class + 1) if document_class is not None else 0, "\\usepackage{graphicx}")
    output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def create_export(project_id: str, override: bool, override_reason: str | None) -> tuple[str, list[str]]:
    from . import manuscript, citations as citation_service
    project = db.fetch_one("SELECT * FROM projects WHERE id=?", (project_id,))
    if not project: raise FileNotFoundError(project_id)
    charts = db.fetch_all("SELECT * FROM charts WHERE project_id=?", (project_id,))
    compositions = db.fetch_all("SELECT * FROM compositions WHERE project_id=?", (project_id,))
    warnings = [warning for chart in charts for warning in json.loads(chart["warnings_json"])]
    warnings.extend(warning for composition in compositions for warning in json.loads(composition["warnings_json"]))
    warnings = list(dict.fromkeys(warnings))
    if warnings and not override: raise PermissionError("statistics_confirmation_required")
    if override:
        db.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)", (uuid.uuid4().hex, project_id, "export", project_id, "override", override_reason or "用户确认风险后导出", json.dumps(warnings, ensure_ascii=False), utcnow()))
    export_id = uuid.uuid4().hex
    root = db.project_dir(project_id); bundle = root / "exports" / export_id
    bundle.mkdir(parents=True, exist_ok=True)
    decisions = db.fetch_all("SELECT * FROM decisions WHERE project_id=? ORDER BY created_at, rowid", (project_id,))
    assets = db.fetch_all("SELECT * FROM (SELECT * FROM assets UNION ALL SELECT * FROM project_literature) AS assets WHERE project_id=? ORDER BY accessed_at", (project_id,))
    datasets = db.fetch_all("SELECT * FROM datasets WHERE project_id=? ORDER BY created_at", (project_id,))
    documents = db.fetch_all("SELECT * FROM documents WHERE project_id=?", (project_id,))
    anchors = manuscript.anchors(project_id)
    citation_view = citation_service.projection(project_id)
    selected_document=manuscript.document_id(project_id)
    export_documents=[d for d in documents if d["id"]==selected_document]
    revisions = db.fetch_all("SELECT * FROM anchor_revisions WHERE project_id=? ORDER BY created_at", (project_id,))
    document_revisions = db.fetch_all("SELECT * FROM document_revisions WHERE project_id=? ORDER BY created_at", (project_id,))
    manifest = _portable_audit_paths(
        {"export_id":export_id,"project":project,"documents":documents,"anchors":anchors,"anchor_revisions":revisions,"document_revisions":document_revisions,"assets":assets,"datasets":datasets,"charts":charts,"compositions":compositions,"decisions":decisions,"warnings":warnings,"created_at":utcnow()},
        root,
    )
    with (bundle / "placement-report.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle); writer.writerow(["anchor_id","section","page","paragraph_index","text_hash","current_text"])
        for anchor in anchors: writer.writerow([anchor["id"],anchor["section"],anchor["page"],anchor["paragraph_index"],anchor["text_hash"],anchor["text"]])
    with (bundle / "document-revisions.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle); writer.writerow(["revision_id","anchor_id","reason","created_at","old_text","new_text"])
        for revision in revisions: writer.writerow([revision["id"],revision["anchor_id"],revision["reason"],revision["created_at"],revision["old_text"],revision["new_text"]])
    (bundle / "full-document-revisions.json").write_text(json.dumps(document_revisions, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_asset_decisions: dict[str, str] = {}
    for decision in decisions:
        if decision["target_type"] == "asset":
            latest_asset_decisions[decision["target_id"]] = decision["action"]
    latest_citations: dict[str, str] = {}
    for decision in decisions:
        if decision["target_type"] == "citation":
            latest_citations[decision["target_id"]] = decision["action"]
    citations = [*citation_view['bibliography'],*citation_view.get('unplaced_references',[])]
    manifest['citation_projection'] = citation_view
    (bundle / "citation-projection.json").write_text(json.dumps(citation_view,ensure_ascii=False,indent=2),encoding='utf-8')
    rendered_anchors=citation_service.rendered(anchors,citation_view)
    (bundle / "manuscript.md").write_text("# "+project['name']+"\n\n"+"\n\n".join(a['text'] for a in rendered_anchors)+"\n",encoding='utf-8')
    manifest["citations"] = citations
    (bundle / "references.json").write_text(json.dumps(citations, ensure_ascii=False, indent=2), encoding="utf-8")
    references = ["# 已选引用来源", "", "以下条目是来源引用，不代表图片复用许可。", ""]
    for index, citation in enumerate(citations, 1):
        publication=" · ".join(str(citation[key]) for key in ("journal","year") if citation.get(key))
        label=f"[{citation['number']}]" if citation.get("number") is not None else "[未在当前正文定位]"
        references.append(f"{label} {citation.get('author') or '作者未标注'}. {citation['title']}. {publication} {citation['source_url']} (访问：{citation['accessed_at']})")
    (bundle / "references.md").write_text("\n".join(references), encoding="utf-8")
    bib_entries=[];ris_entries=[]
    def bib_value(value):
        return str(value).replace(chr(92),chr(92)+"textbackslash{} ").replace("{",chr(92)+"{").replace("}",chr(92)+"}").replace(chr(10)," ")
    for index,citation in enumerate(citations,1):
        fields={"title":citation["title"],"author":(citation.get("author") or "").replace(";"," and "),"year":citation.get("year"),"journal":citation.get("journal"),"doi":citation.get("doi"),"url":citation["source_url"]}
        bib_entries.append("@misc{"+citation["reference_key"]+",\n"+",\n".join("  "+key+" = {"+bib_value(value)+"}" for key,value in fields.items() if value)+"\n}")
        lines=["TY  - JOUR","TI  - "+citation["title"]]
        for author in (citation.get("author") or "").split(";"):
            if author.strip():lines.append("AU  - "+author.strip())
        for key,tag in (("year","PY"),("journal","JO"),("doi","DO"),("source_url","UR")):
            if citation.get(key):lines.append(tag+"  - "+str(citation[key]))
        ris_entries.append("\n".join(lines+["ER  - ",""]))
    (bundle/"references.bib").write_text("\n\n".join(bib_entries),encoding="utf-8")
    (bundle/"references.ris").write_text("\n".join(ris_entries),encoding="utf-8")
    with (bundle / "asset-license-manifest.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "asset_id", "anchor_id", "title", "provider", "author", "source_url",
            "license_name", "license_url", "license_verified", "verification_source",
            "restrictions", "reuse_status", "decision_action", "accessed_at", "sha256",
        ])
        for asset in assets:
            license_record = json.loads(asset["license_json"])
            verification_source = license_record.get("verification_source")
            if license_record.get("verified") and not verification_source:
                verification_source = "provider_metadata"
            writer.writerow([
                asset["id"], asset["anchor_id"], asset["title"], asset["provider"], asset.get("author"),
                asset["source_url"], license_record.get("name"), license_record.get("url"),
                bool(license_record.get("verified")), verification_source or "",
                " | ".join(license_record.get("restrictions") or []), asset["reuse_status"],
                latest_asset_decisions.get(asset["id"], "pending"), asset["accessed_at"], asset.get("checksum") or "",
            ])
    dataset_exports: dict[str, str] = {}
    data_dir = bundle / "data"; figure_dir = bundle / "figures"; script_dir = bundle / "scripts"
    data_dir.mkdir(exist_ok=True); figure_dir.mkdir(exist_ok=True); script_dir.mkdir(exist_ok=True)
    for dataset in datasets:
        source_path = Path(dataset["local_path"])
        export_name = f"{dataset['id']}{source_path.suffix.lower()}"
        if source_path.exists(): shutil.copy2(source_path, data_dir / export_name)
        dataset_exports[dataset["id"]] = export_name
    for chart in charts:
        for extension, path in json.loads(chart["outputs_json"]).items():
            source_path = Path(path)
            if source_path.exists():
                export_name = f"{chart['id']}-chart-package.zip" if extension == "package" else f"{chart['id']}.{extension}"
                shutil.copy2(source_path, figure_dir / export_name)
        dataset_name = dataset_exports.get(chart["dataset_id"])
        if dataset_name:
            spec = ChartSpec.model_validate_json(chart["spec_json"])
            (script_dir / f"{chart['id']}_reproduce.py").write_text(portable_chart_script(dataset_name, spec), encoding="utf-8")
    for composition in compositions:
        for extension, path in json.loads(composition["outputs_json"]).items():
            source_path = Path(path)
            if source_path.exists(): shutil.copy2(source_path, figure_dir / f"composition-{composition['id']}.{extension}")
    figure_bindings = _collect_figure_bindings(project_id, anchors, assets, charts, compositions, decisions, figure_dir)
    manifest["figure_bindings"] = [{key:value for key,value in binding.items() if key != "path"} for binding in figure_bindings]
    (bundle / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    report = [f"# {project['name']} — 配图审计报告", "", f"导出时间：{manifest['created_at']}", f"质量模式：{project['quality_mode']}", "", "## 统计与审核风险"] + ([f"- {w}" for w in warnings] or ["- 未发现阻止项"]) + ["", "## 素材来源"]
    for asset in assets:
        license_record = json.loads(asset["license_json"])
        report.append(
            f"- {asset['title']} — {asset['provider']} — {asset.get('author') or '作者未标注'}"
            f" — {license_record.get('name')} — {asset['reuse_status']}"
            f" — 决策：{latest_asset_decisions.get(asset['id'], 'pending')}"
            f" — 访问：{asset['accessed_at']} — SHA-256：{asset.get('checksum') or '未缓存文件'}"
            f" — {asset['source_url']}"
        )
    report += ["", "## 正文修订"] + ([f"- 段落 {revision['anchor_id']}：{revision['reason']}" for revision in revisions] or ["- 无正文修订"])
    report += ["", "## 图件回填"] + ([f"- 图 {binding['number']} → 锚点 {binding['anchor_id']}：{binding['caption']}" for binding in figure_bindings] or ["- 无可回填图件"])
    (bundle / "audit-report.md").write_text("\n".join(report), encoding="utf-8")
    anchor_by_id = {anchor["id"]:anchor for anchor in anchors}
    with (bundle / "figure-placement-report.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle); writer.writerow(["figure_number","figure_kind","figure_id","anchor_id","section","page","paragraph_index","bbox","resource_path","caption","sources"])
        for binding in figure_bindings:
            anchor = anchor_by_id[binding["anchor_id"]]
            writer.writerow([binding["number"],binding["kind"],binding["figure_id"],binding["anchor_id"],anchor["section"],anchor["page"],anchor["paragraph_index"],anchor.get("bbox_json"),binding["relative_path"],binding["caption"]," | ".join(binding["sources"])])
    if export_documents and Path(export_documents[0]["local_path"]).suffix.lower() == ".docx":
        source = Path(export_documents[0]["local_path"]); output = bundle / f"{Path(export_documents[0]['filename']).stem}_配图版.docx"
        _write_illustrated_docx(source, output, rendered_anchors, figure_bindings)
    if export_documents and Path(export_documents[0]["local_path"]).suffix.lower() in {".md", ".tex"}:
        source = Path(export_documents[0]["local_path"]); output = bundle / f"{Path(export_documents[0]['filename']).stem}_illustrated{source.suffix}"
        _write_illustrated_source(source, output, rendered_anchors, figure_bindings)
    if export_documents and Path(export_documents[0]["local_path"]).suffix.lower() == ".pdf":
        revision_report = [f"# {project['name']} — PDF 修订与图件位置报告", "", "原 PDF 未修改。以下内容按原页码、段落锚点和可用坐标导出：", ""]
        for anchor in rendered_anchors:
            placements = [binding for binding in figure_bindings if binding["anchor_id"] == anchor["id"]]
            revision_report += [f"## {anchor['section']} · 页 {anchor['page'] or '未知'} · 段落 {anchor['paragraph_index']} · 坐标 {anchor.get('bbox_json') or '未知'}", "", anchor["text"], ""]
            for binding in placements: revision_report += [f"- 插入图 {binding['number']}：{binding['relative_path']} — {binding['caption']}", ""]
        (bundle / "PDF-document-and-figure-placement.md").write_text("\n".join(revision_report), encoding="utf-8")
    archive = root / "exports" / f"{project_id}_{export_id}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in bundle.rglob("*"):
            if path.is_file(): zf.write(path, path.relative_to(bundle))
    available_formats: set[str] = set()
    for row in [*charts, *compositions]:
        outputs = json.loads(row.get("outputs_json") or "{}")
        available_formats.update("chart-package" if extension == "package" else extension for extension in outputs)
    format_order = ("svg", "pdf", "tiff", "png", "xlsx", "chart-package")
    formats = [item for item in format_order if item in available_formats]
    db.execute("INSERT INTO exports VALUES (?,?,?,?,?,?)", (export_id, project_id, str(archive), json.dumps(formats), json.dumps(warnings, ensure_ascii=False), manifest["created_at"]))
    db.execute(
        "UPDATE projects SET status=?, progress=100, updated_at=? WHERE id=?",
        ("已强制导出并留痕" if override else "已完成安全导出",db.now(),project_id),
    )
    create_checkpoint(project_id, "导出前检查点")
    return export_id, warnings
