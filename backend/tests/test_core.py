from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import httpx
import pandas as pd
import pytest

from backend import db, services
from backend.main import _extract_local_scientific_terms, _load_or_create_session_token
from backend.models import ChartSpec, ExportRequest
from backend.parsers import parse_text_source
from backend.security import ensure_within, redact, safe_filename, scan_sensitive
from backend.services import _public_remote_url, normalize_license, profile_dataset, render_chart, sanitize_svg, search_open_assets


def test_sensitive_scan_and_redaction():
    text = "内部资料，请联系 test@example.com 或 13800138000；姓名：刘同学 学号：2414011014；Name: Test Student Student ID: 2414011014"
    findings = scan_sensitive(text)
    assert {"email", "phone", "confidential_marker", "personal_name", "student_number"}.issubset(findings)
    clean = redact(text)
    assert "test@example.com" not in clean
    assert "13800138000" not in clean
    assert "刘同学" not in clean
    assert "2414011014" not in clean
    assert "Test Student" not in clean


def test_local_scientific_terms_capture_claim_relevant_phrases():
    text = (
        "锂离子电池的发展重点正由单纯提升可用性转向安全性和循环寿命。"
        "高能量密度设计会加剧热失控、气体释放和电极结构退化，退役电池还需要材料再生。"
    )
    terms = _extract_local_scientific_terms(text)
    assert {"锂离子电池", "能量密度", "热失控", "循环寿命", "材料再生"}.issubset(terms)


def test_safe_filename_and_path(tmp_path: Path):
    assert safe_filename("../../论文?.docx") == "论文_.docx"
    child = tmp_path / "safe" / "file.txt"
    child.parent.mkdir()
    assert ensure_within(tmp_path, child) == child.resolve()


def test_local_session_token_survives_restart(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(db, "APP_HOME", tmp_path / "appdata")
    first = _load_or_create_session_token()
    second = _load_or_create_session_token()
    assert first == second
    assert len(first) >= 32
    assert (db.APP_HOME / "session.token").read_text(encoding="utf-8") == first


def test_license_normalization():
    license_data, status = normalize_license("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/")
    assert status == "attribution_required"
    assert license_data["verified"] is True
    assert license_data["verification_source"] == "provider_metadata"
    assert license_data["restrictions"] == []
    openverse_by, openverse_status = normalize_license("by", "https://creativecommons.org/licenses/by/4.0/")
    assert openverse_status == "attribution_required"
    assert openverse_by["verified"] is True
    public_domain, public_domain_status = normalize_license("pdm", "https://creativecommons.org/publicdomain/mark/1.0/")
    assert public_domain_status == "safe_open"
    assert public_domain["attribution_required"] is False
    assert public_domain["verification_source"] == "provider_metadata"
    for restricted_license, restriction in (
        ("CC BY-NC 4.0", "noncommercial"),
        ("CC BY-ND 4.0", "no_derivatives"),
        ("CC BY-SA 4.0", "share_alike"),
        ("by-sa", "share_alike"),
    ):
        restricted, restricted_status = normalize_license(restricted_license)
        assert restricted_status == "citation_only"
        assert restriction in restricted["restrictions"]
    _, unknown = normalize_license(None)
    assert unknown == "unknown"


def test_remote_preview_security_and_svg_sanitization():
    assert _public_remote_url("http://127.0.0.1/private.png") is False
    unsafe = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(1)</script><image href="https://evil.test/x.png"/><rect width="2" height="2"/></svg>'
    cleaned = sanitize_svg(unsafe).decode("utf-8")
    assert "script" not in cleaned
    assert "onload" not in cleaned
    assert "evil.test" not in cleaned
    assert "rect" in cleaned


def test_markdown_anchors(tmp_path: Path):
    source = tmp_path / "paper.md"
    source.write_text("# 摘要\n\n这是第一段足够长的研究说明。\n\n## 方法\n\n这是第二段方法说明。", encoding="utf-8")
    anchors = parse_text_source(source)
    assert [a.section for a in anchors] == ["摘要", "方法"]
    assert anchors[0].text_hash != anchors[1].text_hash


def test_dataset_profile_and_chart_export(tmp_path: Path, monkeypatch):
    dataset = tmp_path / "data.csv"
    frame = pd.DataFrame({"group":["A","A","=2+2","=2+2"],"value":[1.0,1.3,2.0,2.2]})
    frame.to_csv(dataset, index=False)
    profile = profile_dataset(dataset)
    assert profile["rows"] == 4
    assert "grouped_bar" in profile["recommended_charts"]
    xlsx_dataset = tmp_path / "data.xlsx"
    frame.to_excel(xlsx_dataset, index=False)
    xlsx_profile = profile_dataset(xlsx_dataset)
    assert xlsx_profile["rows"] == profile["rows"]
    assert xlsx_profile["recommended_charts"] == profile["recommended_charts"]
    monkeypatch.setattr(db, "PROJECTS_ROOT", tmp_path / "projects")
    spec = ChartSpec(dataset_id="d", chart_type="grouped_bar", x="group", y="value", title="Test", width_mm=89, height_mm=65, font_family="Calibri", grid_mode="y", legend_position="bottom", palette="office", show_data_labels=True)
    outputs, warnings, _ = render_chart("project", dataset, spec)
    assert {"svg","pdf","png","tiff","xlsx","package"} == set(outputs)
    assert all(Path(path).exists() for path in outputs.values())
    from openpyxl import load_workbook
    workbook = load_workbook(outputs["xlsx"], data_only=False)
    assert workbook.sheetnames == ["Raw Data", "README", "Editable Chart"]
    assert len(workbook["Editable Chart"]._charts) == 1
    chart_anchor = workbook["Editable Chart"]._charts[0].anchor
    assert chart_anchor.ext.cx / 360000 == pytest.approx(8.9)
    assert chart_anchor.ext.cy / 360000 == pytest.approx(6.5)
    assert workbook["Raw Data"]["A4"].value == "=2+2"
    assert workbook["Raw Data"]["A4"].data_type == "s"
    assert workbook["Editable Chart"]["A3"].value == "=2+2"
    assert workbook["Editable Chart"]["A3"].data_type == "s"
    import zipfile
    with zipfile.ZipFile(outputs["package"]) as archive:
        assert {"figure.svg","figure.pdf","figure.png","figure.tiff","editable-office-chart.xlsx","chart-spec.json","reproduce.py","source-data.csv","README.md"}.issubset(archive.namelist())
        assert b"unchanged provenance copy" in archive.read("README.md")
    with zipfile.ZipFile(outputs["xlsx"]) as office_archive:
        office_names = office_archive.namelist()
        assert not any("externalLinks/" in name or name.endswith("vbaProject.bin") for name in office_names)
    assert '"width_mm": 89.0' in (Path(outputs["xlsx"]).parent / "chart-spec.json").read_text(encoding="utf-8")
    assert 'SPEC["grid_mode"]' in (Path(outputs["xlsx"]).parent / "reproduce.py").read_text(encoding="utf-8")
    assert "未定义独立样本单位 n" in warnings


def test_chart_data_labels_keep_ordinary_measurements_out_of_exponent_notation():
    assert services._format_chart_data_label(1282.37) == "1282.37"
    assert services._format_chart_data_label(1343.8) == "1343.80"
    assert services._format_chart_data_label(0.25) == "0.250"
    assert services._format_chart_data_label(2_500_000) == "2.50e+06"


def test_dataset_and_chart_input_validation_stops_before_rendering(tmp_path: Path, monkeypatch):
    empty = tmp_path / "empty.csv"
    empty.write_text("",encoding="utf-8")
    with pytest.raises(ValueError,match="无法读取"):
        profile_dataset(empty)

    dataset = tmp_path / "invalid-chart-data.csv"
    pd.DataFrame({
        "category":["A","B","C"],
        "time":[0.0,1.0,2.0],
        "response":[1.0,2.0,3.0],
        "text_response":["low","medium","high"],
        "constant":[1.0,1.0,1.0],
    }).to_csv(dataset,index=False)
    monkeypatch.setattr(db,"PROJECTS_ROOT",tmp_path / "projects")
    invalid_specs = [
        (ChartSpec(dataset_id="d",chart_type="grouped_bar",x="missing",y="response"),"缺少所选字段"),
        (ChartSpec(dataset_id="d",chart_type="grouped_bar",x="category",y="text_response"),"响应变量必须是数值字段"),
        (ChartSpec(dataset_id="d",chart_type="scatter",x="category",y="response"),"散点图的 X 字段必须是数值字段"),
        (ChartSpec(dataset_id="d",chart_type="line",x="time",y="response",x_scale="log"),"严格大于 0"),
    ]
    for index,(spec,message) in enumerate(invalid_specs):
        with pytest.raises(ValueError,match=message):
            render_chart(f"invalid-{index}",dataset,spec)
        assert not any((tmp_path / "projects" / f"invalid-{index}" / "charts").glob("*"))

    heatmap_data = tmp_path / "invalid-heatmap.csv"
    pd.DataFrame({"category":["A","B"],"constant":[1.0,1.0]}).to_csv(heatmap_data,index=False)
    with pytest.raises(ValueError,match="至少需要两个具有变化的数值字段"):
        render_chart(
            "invalid-heatmap",
            heatmap_data,
            ChartSpec(dataset_id="d",chart_type="heatmap",x="category",y="constant"),
        )
    assert not any((tmp_path / "projects" / "invalid-heatmap" / "charts").glob("*"))


def test_chinese_csv_encoding_compatibility(tmp_path: Path, monkeypatch):
    dataset = tmp_path / "中文数据.csv"
    dataset.write_bytes(
        "条件,响应值\n对照,1.2\n对照,1.4\n改性,2.1\n改性,2.4\n".encode("gb18030")
    )
    profile = profile_dataset(dataset)
    assert [column["name"] for column in profile["columns"]] == ["条件","响应值"]
    assert profile["rows"] == 4
    monkeypatch.setattr(db,"PROJECTS_ROOT",tmp_path / "projects")
    outputs,warnings,_ = render_chart(
        "chinese-csv",
        dataset,
        ChartSpec(
            dataset_id="d",chart_type="grouped_bar",x="条件",y="响应值",
            title="中文 CSV 制图",independent_unit="两次独立观测",
            error_bar="none",statistical_test="描述性展示",
        ),
    )
    assert warnings == []
    assert all(Path(outputs[extension]).stat().st_size > 0 for extension in ("svg","pdf","png","tiff","xlsx","package"))
    script = Path(outputs["xlsx"]).parent / "reproduce.py"
    reproduced = subprocess.run(
        [sys.executable,str(script)],
        cwd=script.parent,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert reproduced.returncode == 0, reproduced.stderr
    assert all((script.parent / f"reproduced.{extension}").stat().st_size > 0 for extension in ("svg","pdf","png","tiff"))


def test_multisheet_xlsx_profile_selection_and_portable_reproduction(tmp_path: Path, monkeypatch):
    dataset = tmp_path / "多工作表.xlsx"
    with pd.ExcelWriter(dataset) as writer:
        pd.DataFrame().to_excel(writer,sheet_name="说明",index=False)
        pd.DataFrame({"条件":["A","B"],"响应":[1.0,2.0]}).to_excel(writer,sheet_name="实验一",index=False)
        pd.DataFrame({"时间":[1.0,2.0,3.0],"电流":[2.0,3.5,5.0]}).to_excel(writer,sheet_name="实验二",index=False)
    profile = profile_dataset(dataset)
    assert profile["selected_sheet"] == "实验一"
    assert [sheet["name"] for sheet in profile["sheets"]] == ["实验一","实验二"]
    assert [column["name"] for column in profile["sheets"][1]["columns"]] == ["时间","电流"]

    monkeypatch.setattr(db,"PROJECTS_ROOT",tmp_path / "projects")
    outputs,warnings,_ = render_chart(
        "multisheet",
        dataset,
        ChartSpec(
            dataset_id="d",sheet_name="实验二",chart_type="line",x="时间",y="电流",
            title="工作表选择",independent_unit="三次独立观测",
            error_bar="none",statistical_test="描述性展示",
        ),
    )
    assert warnings == []
    from openpyxl import load_workbook
    office = load_workbook(outputs["xlsx"],data_only=False)
    assert [office["Raw Data"].cell(1,column).value for column in (1,2)] == ["时间","电流"]
    readme_values = {
        office["README"].cell(row,1).value:office["README"].cell(row,2).value
        for row in range(2,office["README"].max_row+1)
    }
    assert readme_values["Source worksheet"] == "实验二"
    script = Path(outputs["xlsx"]).parent / "reproduce.py"
    reproduced = subprocess.run(
        [sys.executable,str(script)],
        cwd=script.parent,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert reproduced.returncode == 0, reproduced.stderr
    assert all((script.parent / f"reproduced.{extension}").stat().st_size > 0 for extension in ("svg","pdf","png","tiff"))

    with pytest.raises(ValueError,match="不存在工作表"):
        render_chart(
            "missing-sheet",dataset,
            ChartSpec(dataset_id="d",sheet_name="不存在",chart_type="line",x="时间",y="电流"),
        )
    assert not any((tmp_path / "projects" / "missing-sheet" / "charts").glob("*"))


def test_chart_spec_rejects_invalid_ranges():
    with pytest.raises(ValueError):
        ChartSpec(dataset_id="d", chart_type="line", x="x", y="y", x_min=10, x_max=1)
    with pytest.raises(ValueError):
        ChartSpec(dataset_id="d", chart_type="line", x="x", y="y", y_scale="log", y_min=0)


def test_office_scatter_preserves_axis_and_marker_controls(tmp_path: Path, monkeypatch):
    dataset = tmp_path / "scatter.csv"
    pd.DataFrame({"dose":[1.0,10.0,100.0],"response":[2.0,5.0,9.0]}).to_csv(dataset, index=False)
    monkeypatch.setattr(db, "PROJECTS_ROOT", tmp_path / "projects")
    spec = ChartSpec(
        dataset_id="d", chart_type="scatter", x="dose", y="response",
        title="Dose response", x_scale="log", x_min=1, x_max=100,
        y_min=0, y_max=10, tick_count=6, grid_mode="both",
        show_minor_ticks=True, marker="triangle", line_width=2,
    )
    outputs, _, _ = render_chart("project", dataset, spec)
    from openpyxl import load_workbook
    workbook = load_workbook(outputs["xlsx"], data_only=False)
    chart = workbook["Editable Chart"]._charts[0]
    assert chart.x_axis.scaling.min == 1
    assert chart.x_axis.scaling.max == 100
    assert chart.x_axis.scaling.logBase == 10
    assert chart.y_axis.scaling.min == 0
    assert chart.y_axis.scaling.max == 10
    assert chart.y_axis.majorUnit == pytest.approx(2)
    assert chart.x_axis.majorGridlines is not None
    assert chart.y_axis.majorGridlines is not None
    assert chart.x_axis.minorTickMark == "out"
    assert chart.y_axis.minorTickMark == "out"
    assert chart.series[0].marker.symbol == "triangle"
    readme = workbook["README"]
    readme_values = {readme.cell(row,1).value:readme.cell(row,2).value for row in range(2,readme.max_row+1)}
    assert readme_values["X scale"] == "log"
    assert readme_values["Y bounds"] == "0.0 … 10.0"
    assert readme_values["Minor ticks"] == "shown"


def test_office_numeric_line_preserves_xy_scale_groups_labels_and_fonts(tmp_path: Path, monkeypatch):
    dataset = tmp_path / "numeric-line.csv"
    pd.DataFrame({
        "condition":["Control"]*3+["Modified"]*3,
        "time":[1.0,10.0,100.0,1.0,10.0,100.0],
        "response":[2.0,4.0,8.0,2.5,5.0,9.5],
    }).to_csv(dataset,index=False)
    monkeypatch.setattr(db,"PROJECTS_ROOT",tmp_path / "projects")
    spec = ChartSpec(
        dataset_id="d",chart_type="line",x="time",y="response",group="condition",
        title="Numeric line",font_family="Calibri",font_size_pt=9,title_size_pt=13,
        x_scale="log",x_min=1,x_max=100,y_min=0,y_max=10,tick_count=6,
        legend_position="inside_upper_right",marker="square",show_data_labels=True,
    )
    outputs,_,_ = render_chart("numeric-line",dataset,spec)
    from openpyxl import load_workbook
    workbook = load_workbook(outputs["xlsx"],data_only=False)
    chart = workbook["Editable Chart"]._charts[0]
    assert chart.__class__.__name__ == "ScatterChart"
    assert chart.scatterStyle == "lineMarker"
    assert len(chart.series) == 2
    assert [series.tx.v for series in chart.series] == ["Control","Modified"]
    assert chart.x_axis.scaling.logBase == 10
    assert chart.x_axis.scaling.min == 1
    assert chart.x_axis.scaling.max == 100
    assert chart.x_axis.axPos == "b"
    assert chart.y_axis.axPos == "l"
    assert chart.legend.position == "tr"
    assert chart.legend.overlay is True
    assert all(series.marker.symbol == "square" for series in chart.series)
    assert chart.dLbls.showVal is True
    assert chart.dLbls.numFmt == "0.00"
    title_run = chart.title.tx.rich.p[0].r[0].rPr
    assert title_run.latin.typeface == "Calibri"
    assert title_run.sz == 1300
    axis_properties = chart.x_axis.txPr.p[0].pPr.defRPr
    assert axis_properties.latin.typeface == "Calibri"
    assert axis_properties.sz == 900
    readme = workbook["README"]
    readme_values = {readme.cell(row,1).value:readme.cell(row,2).value for row in range(2,readme.max_row+1)}
    assert readme_values["Native chart mode"] == "native XY line chart"
    assert readme_values["Resolved Office font"] == "Calibri"


def test_office_grouped_scatter_keeps_one_editable_series_per_group(tmp_path: Path, monkeypatch):
    dataset = tmp_path / "grouped-scatter.csv"
    pd.DataFrame({
        "condition":["Control"]*3+["Modified"]*3,
        "dose":[1.0,2.0,3.0,1.0,2.0,3.0],
        "response":[2.0,2.8,3.4,2.4,3.5,4.7],
    }).to_csv(dataset,index=False)
    monkeypatch.setattr(db,"PROJECTS_ROOT",tmp_path / "projects")
    spec = ChartSpec(
        dataset_id="d",chart_type="scatter",x="dose",y="response",group="condition",
        title="Grouped scatter",marker="none",palette="colorblind",
    )
    outputs,_,_ = render_chart("grouped-scatter",dataset,spec)
    from openpyxl import load_workbook
    workbook = load_workbook(outputs["xlsx"],data_only=False)
    chart = workbook["Editable Chart"]._charts[0]
    assert chart.scatterStyle == "marker"
    assert len(chart.series) == 2
    assert [series.tx.v for series in chart.series] == ["Control","Modified"]
    assert all(series.graphicalProperties.line.noFill is True for series in chart.series)
    assert all(series.marker.symbol == "circle" for series in chart.series)
    headers = [workbook["Editable Chart"].cell(1,column).value for column in range(1,5)]
    assert headers == [
        "Control · dose","Control · response",
        "Modified · dose","Modified · response",
    ]
    readme = workbook["README"]
    readme_values = {readme.cell(row,1).value:readme.cell(row,2).value for row in range(2,readme.max_row+1)}
    assert readme_values["Native chart mode"] == "native XY scatter chart; one series per group"


def test_draft_chart_does_not_invent_confidence_intervals(tmp_path: Path, monkeypatch):
    dataset = tmp_path / "draft.csv"
    pd.DataFrame({"group":["A","A","B","B"],"value":[1.0,1.2,2.0,2.2]}).to_csv(dataset,index=False)
    monkeypatch.setattr(db, "PROJECTS_ROOT", tmp_path / "projects")
    captured: dict[str, object] = {}

    def capture_barplot(*_args, **kwargs):
        captured["errorbar"] = kwargs.get("errorbar")
        return kwargs["ax"]

    monkeypatch.setattr(services.sns, "barplot", capture_barplot)
    spec = ChartSpec(
        dataset_id="d", chart_type="grouped_bar", x="group", y="value",
        title="Draft without declared uncertainty",
    )
    _, warnings, _ = render_chart("project", dataset, spec)
    assert captured["errorbar"] is None
    assert "未定义误差线或不确定性表达" in warnings


def test_all_chart_types_export_complete_portable_packages(tmp_path: Path, monkeypatch):
    dataset = tmp_path / "matrix.csv"
    pd.DataFrame({
        "condition":["Control"]*3+["Modified"]*3,
        "time":[1.0,2.0,3.0,1.0,2.0,3.0],
        "response":[2.0,2.8,3.7,2.5,3.6,4.8],
        "signal":[8.0,7.2,6.4,8.5,7.4,6.1],
    }).to_csv(dataset,index=False)
    monkeypatch.setattr(db, "PROJECTS_ROOT", tmp_path / "projects")
    expected_package_files = {
        "figure.svg","figure.pdf","figure.png","figure.tiff",
        "editable-office-chart.xlsx","chart-spec.json","reproduce.py",
        "source-data.csv","README.md",
    }
    native_types = {"grouped_bar","line","scatter"}
    specs = {
        "grouped_bar":ChartSpec(dataset_id="d",chart_type="grouped_bar",x="condition",y="response",title="Grouped response",error_bar="none"),
        "line":ChartSpec(dataset_id="d",chart_type="line",x="time",y="response",group="condition",title="Response over time",error_bar="none"),
        "scatter":ChartSpec(dataset_id="d",chart_type="scatter",x="time",y="response",group="condition",title="Time response"),
        "box":ChartSpec(dataset_id="d",chart_type="box",x="condition",y="response",title="Response distribution",error_bar="none"),
        "heatmap":ChartSpec(dataset_id="d",chart_type="heatmap",x="time",y="response",title="Numeric correlation"),
    }
    from openpyxl import load_workbook
    import zipfile

    for chart_type, spec in specs.items():
        spec = spec.model_copy(update={
            "independent_unit":"three independent observations per condition",
            "statistical_test":"descriptive visualization; no inference",
        })
        outputs, warnings, _ = render_chart("project", dataset, spec)
        assert set(outputs) == {"svg","pdf","png","tiff","xlsx","package"}
        assert warnings == []
        assert all(Path(path).is_file() and Path(path).stat().st_size > 0 for path in outputs.values())
        workbook = load_workbook(outputs["xlsx"], data_only=False)
        editable = workbook["Editable Chart"]
        if chart_type in native_types:
            assert len(editable._charts) == 1
            assert len(editable._images) == 0
        else:
            assert len(editable._charts) == 0
            assert len(editable._images) == 1
            assert "editable source table" in editable["A1"].value
        with zipfile.ZipFile(outputs["package"]) as archive:
            assert expected_package_files.issubset(archive.namelist())
        script = Path(outputs["xlsx"]).parent / "reproduce.py"
        reproduced = subprocess.run(
            [sys.executable,str(script)],
            cwd=script.parent,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert reproduced.returncode == 0, f"{chart_type}: {reproduced.stderr}"
        assert all((script.parent / f"reproduced.{extension}").stat().st_size > 0 for extension in ("svg","pdf","png","tiff"))


def test_forced_export_requires_specific_audit_reason():
    with pytest.raises(ValueError):
        ExportRequest(override_statistics=True, override_reason="确认")
    valid = ExportRequest(override_statistics=True, override_reason="作者确认仅展示描述性结果")
    assert valid.override_reason == "作者确认仅展示描述性结果"


def test_open_asset_search_reports_partial_and_total_provider_failure(monkeypatch):
    async def partial_get(_client, url, **_kwargs):
        request = httpx.Request("GET", url)
        if "openverse.org" in url:
            return httpx.Response(
                200,
                json={"results":[{
                    "title":"Open catalyst image",
                    "foreign_landing_url":"https://example.test/catalyst",
                    "thumbnail":None,
                    "creator":"Researcher",
                    "license":"CC BY 4.0",
                    "license_url":"https://creativecommons.org/licenses/by/4.0/",
                }]},
                request=request,
            )
        raise httpx.ConnectError("provider offline", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", partial_get)
    results, status = asyncio.run(search_open_assets("porous catalyst"))
    assert len(results) == 1
    assert status["successful_providers"] == ["Openverse"]
    assert status["failed_providers"] == ["Wikimedia Commons", "Europe PMC"]
    assert status["partial"] is True

    async def offline_get(_client, url, **_kwargs):
        raise httpx.ConnectError("offline", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", offline_get)
    results, status = asyncio.run(search_open_assets("porous catalyst"))
    assert results == []
    assert status["successful_providers"] == []
    assert status["failed_providers"] == ["Openverse", "Wikimedia Commons", "Europe PMC"]
