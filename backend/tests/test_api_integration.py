from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import fitz
from fastapi.testclient import TestClient
from docx import Document as DocxDocument
from openpyxl import Workbook

from backend import db, main as main_module
from backend.main import _analyze, app, run_persisted_job
from backend.worker import claim_next_job, recover_interrupted_jobs


def configure_storage(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(db, "APP_HOME", root / "appdata")
    monkeypatch.setattr(db, "PROJECTS_ROOT", root / "projects")
    monkeypatch.setattr(db, "PROJECT_ROOT_CONFIG", root / "appdata" / "projects-root.txt")
    monkeypatch.setattr(db, "DB_PATH", root / "appdata" / "workbench.db")


def authenticate(client: TestClient) -> dict:
    assert client.get("/session").status_code == 200
    response = client.post("/account/login", json={"username":"attack","password":"123456"})
    assert response.status_code == 200, response.text
    return response.json()["account"]


def test_legacy_data_only_project_status_backfill(tmp_path: Path, monkeypatch):
    configure_storage(monkeypatch,tmp_path / "status-backfill")
    db.init_db()
    timestamp = db.now()
    for project_id,dataset_id in (("dataset-only","dataset-one"),("chart-ready","dataset-two")):
        db.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?,?,?,?)",
            (project_id,project_id,"quality","等待上传",0,timestamp,timestamp,None),
        )
        db.execute(
            "INSERT INTO datasets VALUES (?,?,?,?,?,?)",
            (dataset_id,project_id,"data.csv",str(tmp_path / f"{dataset_id}.csv"),"{}",timestamp),
        )
    db.execute(
        "INSERT INTO charts VALUES (?,?,?,?,?,?,?)",
        ("chart-one","chart-ready","dataset-two","{}","{}","[]",timestamp),
    )
    db.init_db()
    dataset_project = db.fetch_one("SELECT status,progress FROM projects WHERE id='dataset-only'")
    chart_project = db.fetch_one("SELECT status,progress FROM projects WHERE id='chart-ready'")
    assert dataset_project == {"status":"数据已分析，可继续制图","progress":72}
    assert chart_project == {"status":"已完成数据制图","progress":86}


def test_data_only_multisheet_project_safe_export(tmp_path: Path, monkeypatch):
    configure_storage(monkeypatch,tmp_path / "data-only-export")
    workbook = Workbook()
    workbook.active.title = "说明"
    experiment = workbook.create_sheet("实验数据")
    experiment.append(["时间_h","电流_A"])
    for row in ((1,2.0),(2,3.5),(3,5.0)):
        experiment.append(row)
    payload = io.BytesIO()
    workbook.save(payload)

    with TestClient(app) as client:
        authenticate(client)
        project = client.post("/projects",json={"name":"纯数据导出","quality_mode":"quality"}).json()
        project_id = project["id"]
        dataset_response = client.post(
            f"/projects/{project_id}/datasets",
            files={"file":("multisheet.xlsx",payload.getvalue(),"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert dataset_response.status_code == 200,dataset_response.text
        dataset = dataset_response.json()
        assert dataset["selected_sheet"] == "实验数据"
        assert [sheet["name"] for sheet in dataset["sheets"]] == ["实验数据"]
        assert client.get(f"/projects/{project_id}").json()["status"] == "数据已分析，可继续制图"

        chart_job = client.post(
            f"/projects/{project_id}/chart-jobs",
            json={
                "dataset_id":dataset["id"],"sheet_name":"实验数据",
                "chart_type":"scatter","x":"时间_h","y":"电流_A",
                "title":"纯数据项目散点图","independent_unit":"三个独立时间点",
                "statistical_test":"描述性展示，不进行推断检验",
            },
        )
        assert chart_job.status_code == 202,chart_job.text
        claimed = claim_next_job()
        assert claimed and claimed["id"] == chart_job.json()["job_id"]
        run_persisted_job(claimed)
        chart_status = client.get(f"/jobs/{claimed['id']}").json()
        assert chart_status["state"] == "completed"
        chart = chart_status["result"]

        export_job = client.post(f"/projects/{project_id}/export-jobs",json={"override_statistics":False})
        assert export_job.status_code == 202,export_job.text
        claimed = claim_next_job()
        assert claimed and claimed["id"] == export_job.json()["job_id"]
        run_persisted_job(claimed)
        export_status = client.get(f"/jobs/{claimed['id']}").json()
        assert export_status["state"] == "completed",export_status
        assert set(export_status["result"]["formats"]) == {"svg","pdf","png","tiff","xlsx","chart-package"}
        exported_project = client.get(f"/projects/{project_id}").json()
        assert exported_project["status"] == "已完成安全导出"
        assert exported_project["progress"] == 100

        archive = client.get(export_status["result"]["download_url"])
        assert archive.status_code == 200
        with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
            names = set(bundle.namelist())
            assert f"data/{dataset['id']}.xlsx" in names
            assert f"figures/{chart['id']}.svg" in names
            assert f"figures/{chart['id']}-chart-package.zip" in names
            assert f"scripts/{chart['id']}_reproduce.py" in names
            assert not any(name.endswith(("_illustrated.md","_illustrated.tex","_配图版.docx")) for name in names)
            manifest = json.loads(bundle.read("manifest.json"))
            assert manifest["documents"] == []
            assert json.loads(manifest["charts"][0]["spec_json"])["sheet_name"] == "实验数据"
            assert "无可回填图件" in bundle.read("audit-report.md").decode("utf-8")


def test_upload_analyze_chart_export_and_portable_import(tmp_path: Path, monkeypatch):
    configure_storage(monkeypatch, tmp_path / "source-workspace")
    paper = (
        "# 多孔催化材料\n\n"
        "## 背景\n\n多级孔结构可改善传质与活性位点暴露。\n\n"
        "## 结果\n\n在三批独立样品中，改性材料转化率明显提高。\n\n"
        "联系 researcher@example.edu 获取内部样品 CAT-2026-017。\n\n"
        "## 结论\n\n开放许可图像与数据图应分别标注观察事实和机制推断。"
    )
    csv_data = (
        "temperature,conversion,material,batch\n"
        "250,52,Control,1\n300,61,Control,2\n350,68,Control,3\n"
        "250,70,Modified,1\n300,84,Modified,2\n350,89,Modified,3\n"
    )

    with TestClient(app) as client:
        assert client.get("/projects").status_code == 401
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["version"] == "0.1.0"
        assert set(health.json()["ocr"]) >= {"available","languages","required_ready","message"}
        account = authenticate(client)
        assert account["username"] == "attack" and account["must_change_password"] is True
        local_one = client.post("/settings/models", json={"name":"Local One","kind":"local","base_url":"http://127.0.0.1:1234/v1","model":"local-one"})
        assert local_one.status_code == 200 and local_one.json()["enabled"] is True
        local_two = client.post("/settings/models", json={"name":"Local Two","kind":"local","base_url":"http://localhost:11434/v1","model":"local-two"})
        assert local_two.status_code == 200 and local_two.json()["enabled"] is False
        activated = client.post(f"/settings/models/{local_two.json()['id']}/activate")
        assert activated.status_code == 200 and activated.json()["enabled"] is True
        profiles = client.get("/settings/models").json()
        assert sum(profile["enabled"] for profile in profiles) == 1
        assert next(profile for profile in profiles if profile["enabled"])["id"] == local_two.json()["id"]
        assert client.post(f"/settings/models/{local_two.json()['id']}/deactivate").status_code == 200
        assert not any(profile["enabled"] for profile in client.get("/settings/models").json())
        assert client.delete(f"/settings/models/{local_one.json()['id']}").status_code == 200
        project = client.post("/projects", json={"name":"闭环验收项目","quality_mode":"quality"}).json()
        project_id = project["id"]
        balanced = client.patch(
            f"/projects/{project_id}/quality-mode",
            json={"quality_mode":"balanced"},
        )
        assert balanced.status_code == 200
        assert balanced.json()["quality_mode"] == "balanced"
        assert any(
            checkpoint["label"] == "切换为平衡模式"
            for checkpoint in client.get(f"/projects/{project_id}/checkpoints").json()
        )
        quality = client.patch(
            f"/projects/{project_id}/quality-mode",
            json={"quality_mode":"quality"},
        )
        assert quality.status_code == 200
        assert quality.json()["quality_mode"] == "quality"

        upload = client.post(
            f"/projects/{project_id}/documents",
            files={"file": ("paper.md", paper.encode("utf-8"), "text/markdown")},
        )
        assert upload.status_code == 200, upload.text
        job_id = client.post(f"/projects/{project_id}/analyze").json()["job_id"]
        claimed = claim_next_job()
        assert claimed and claimed["id"] == job_id
        _analyze(project_id, job_id)

        assert client.patch(f"/jobs/{job_id}", json={"action":"pause"}).status_code == 409
        controllable_job = client.post(f"/projects/{project_id}/analyze").json()["job_id"]
        assert client.patch(f"/jobs/{controllable_job}", json={"action":"pause"}).json()["state"] == "paused"
        assert client.patch(f"/jobs/{controllable_job}", json={"action":"resume"}).json()["state"] == "queued"
        assert client.patch(f"/jobs/{controllable_job}", json={"action":"cancel"}).json()["state"] == "cancelled"
        assert client.patch(f"/jobs/{controllable_job}", json={"action":"retry"}).json()["state"] == "queued"
        assert client.patch(f"/jobs/{controllable_job}", json={"action":"cancel"}).json()["state"] == "cancelled"
        assert any(job["project_name"] == "闭环验收项目" for job in client.get("/jobs").json())

        anchors = client.get(f"/projects/{project_id}/anchors").json()
        assert len(anchors) >= 4
        assert "email" in next(anchor["sensitivity"] for anchor in anchors if "researcher@" in anchor["text"])
        db.execute("UPDATE anchors SET ocr_confidence=?, ocr_rotation=?, ocr_confirmed=0 WHERE id=?", (0.61, 90, anchors[0]["id"]))
        blank_ocr = client.post(
            f"/anchors/{anchors[0]['id']}/ocr-confirmation",
            json={"text":"   ","reason":"reject whitespace OCR"},
        )
        assert blank_ocr.status_code == 422
        ocr_confirmation = client.post(
            f"/anchors/{anchors[0]['id']}/ocr-confirmation",
            json={"reason":"integration OCR review"},
        )
        assert ocr_confirmation.status_code == 200, ocr_confirmation.text
        assert ocr_confirmation.json()["ocr_confirmed"] is True
        assert ocr_confirmation.json()["ocr_rotation"] == 90
        anchors = client.get(f"/projects/{project_id}/anchors").json()
        local_terms = client.get(f"/anchors/{anchors[0]['id']}/local-search-terms").json()
        assert local_terms["uses_ai"] is False and local_terms["uses_network"] is False
        assert local_terms["query"]

        original_text = anchors[0]["text"]
        incomplete_save = client.patch(
            f"/projects/{project_id}/document-content",
            json={"anchors":[{"id":anchors[0]["id"],"text":original_text}],"reason":"incomplete full document"},
        )
        assert incomplete_save.status_code == 409
        first_batch = [{"id":anchor["id"],"text":anchor["text"]} for anchor in anchors]
        first_batch[0]["text"] += " 本段已在全文编辑器修订。"
        first_batch[1]["text"] += " 第二段同步修订。"
        edited = client.patch(
            f"/projects/{project_id}/document-content",
            json={"anchors":first_batch,"reason":"integration full document edit"},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["changed_count"] == 2
        document_revisions = client.get(f"/projects/{project_id}/document-revisions").json()
        assert document_revisions and document_revisions[0]["changed_count"] == 2
        restored = client.post(f"/document-revisions/{document_revisions[0]['id']}/restore")
        assert restored.status_code == 200, restored.text
        assert restored.json()["anchors"][0]["text"] == original_text
        final_batch = [{"id":anchor["id"],"text":anchor["text"]} for anchor in restored.json()["anchors"]]
        final_batch[0]["text"] += " 最终修订版本。"
        final_edit = client.patch(
            f"/projects/{project_id}/document-content",
            json={"anchors":final_batch,"reason":"final full document edit"},
        )
        assert final_edit.status_code == 200, final_edit.text

        preview = client.post("/privacy/preview", json={"text":paper}).json()
        assert "researcher@example.edu" not in preview["redacted"]
        not_confirmed = client.post("/model/suggestions", json={"text":"结果段","confirmed":False})
        assert not_confirmed.status_code == 409

        asset_id = "a" * 32
        db.execute(
            "INSERT INTO assets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (asset_id, project_id, anchors[0]["id"], "Original title", "Openverse", "https://example.org/original", None, "Author", '{"name":"CC BY 4.0","verified":true}', db.now(), 0.8, "attribution_required", None, "test asset", None),
        )
        source_edit = client.patch(
            f"/assets/{asset_id}",
            json={"source_url":"https://example.org/canonical","author":"Correct Author","license_name":"CC BY 4.0","reuse_status":"safe_open","reason":"replace with canonical source"},
        )
        assert source_edit.status_code == 200, source_edit.text
        assert source_edit.json()["source_url"].endswith("/canonical")
        assert source_edit.json()["reuse_status"] == "unknown"
        assert source_edit.json()["license"]["verified"] is False
        unsafe_accept = client.post(
            "/decisions",
            json={"project_id":project_id,"target_type":"asset","target_id":asset_id,"action":"accept","reason":"unsafe direct API attempt","warnings":[]},
        )
        assert unsafe_accept.status_code == 409
        invalid_verification = client.patch(
            f"/assets/{asset_id}",
            json={
                "reuse_status":"attribution_required",
                "license_verified":True,
                "reason":"checked source but no canonical license URL",
            },
        )
        assert invalid_verification.status_code == 422
        verified_source = client.patch(
            f"/assets/{asset_id}",
            json={
                "license_name":"CC BY 4.0",
                "license_url":"https://creativecommons.org/licenses/by/4.0/",
                "reuse_status":"attribution_required",
                "license_verified":True,
                "reason":"checked canonical work and license pages",
            },
        )
        assert verified_source.status_code == 200, verified_source.text
        assert verified_source.json()["reuse_status"] == "attribution_required"
        assert verified_source.json()["license"]["verified"] is True
        assert "manual_license_verification" in client.get(f"/projects/{project_id}/decisions").json()[0]["warnings"]
        safe_accept = client.post(
            "/decisions",
            json={"project_id":project_id,"target_type":"asset","target_id":asset_id,"action":"accept","reason":"verified source accepted","warnings":[]},
        )
        assert safe_accept.status_code == 200
        pending_asset_ids = ["b" * 32, "c" * 32]
        for index, pending_asset_id in enumerate(pending_asset_ids):
            db.execute(
                "INSERT INTO assets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    pending_asset_id, project_id, anchors[0]["id"], f"Pending candidate {index + 1}",
                    "Europe PMC", f"https://example.org/pending-{index + 1}", None, "Author",
                    '{"name":"Citation only","verified":false}', db.now(), 0.7 - index * 0.1,
                    "citation_only", None, "pending candidate", None,
                ),
            )
        bulk_reject = client.post(
            f"/projects/{project_id}/anchors/{anchors[0]['id']}/candidates/reject-pending",
            json={"reason":"reject all remaining candidates"},
        )
        assert bulk_reject.status_code == 200, bulk_reject.text
        assert bulk_reject.json()["rejected_count"] == 2
        assert set(bulk_reject.json()["rejected_ids"]) == set(pending_asset_ids)
        assert bulk_reject.json()["preserved_accepted_count"] == 1
        candidate_actions = {
            item["id"]:item["decision_action"]
            for item in client.get(f"/anchors/{anchors[0]['id']}/candidates").json()
        }
        assert candidate_actions[asset_id] == "accept"
        assert all(candidate_actions[pending_id] == "reject" for pending_id in pending_asset_ids)
        assert "bulk_candidate_rejection" in client.get(f"/projects/{project_id}/decisions").json()[0]["warnings"]
        assistant = client.post(
            "/assistant/chat",
            json={"project_id":project_id,"anchor_id":anchors[0]["id"],"message":"请核验当前来源许可","use_cloud":False},
        )
        assert assistant.status_code == 200, assistant.text
        assert assistant.json()["local_only"] is True
        assert len(client.get(f"/projects/{project_id}/assistant/messages").json()) == 2

        dataset = client.post(
            f"/projects/{project_id}/datasets",
            files={"file": ("data.csv", csv_data.encode("utf-8"), "text/csv")},
        ).json()
        assert dataset["rows"] == 6
        assert client.get(f"/projects/{project_id}/datasets").json()[0]["id"] == dataset["id"]
        dataset_project = client.get(f"/projects/{project_id}").json()
        assert dataset_project["status"] == "数据已分析，可继续制图"
        assert dataset_project["progress"] >= 72
        chart_job = client.post(
            f"/projects/{project_id}/chart-jobs",
            json={
                "dataset_id":dataset["id"], "chart_type":"grouped_bar",
                "anchor_id":anchors[0]["id"],
                "x":"temperature", "y":"conversion", "group":"material",
                "title":"Temperature-dependent conversion",
                "independent_unit":"independent synthesis batch",
                "error_bar":"95% CI", "statistical_test":"descriptive comparison",
            },
        )
        assert chart_job.status_code == 202, chart_job.text
        claimed = claim_next_job(); assert claimed and claimed["id"] == chart_job.json()["job_id"]
        run_persisted_job(claimed)
        chart_status = client.get(f"/jobs/{claimed['id']}").json()
        assert chart_status["state"] == "completed"
        chart = chart_status["result"]
        assert chart["submission_ready"] is True
        chart_project = client.get(f"/projects/{project_id}").json()
        assert chart_project["status"] == "已完成数据制图"
        assert chart_project["progress"] >= 86
        restored_charts = client.get(f"/projects/{project_id}/charts").json()
        assert restored_charts[0]["id"] == chart["id"]
        assert restored_charts[0]["dataset_id"] == dataset["id"]
        chart_preview = client.get(chart["preview_url"])
        assert chart_preview.status_code == 200
        assert chart_preview.headers["content-type"].startswith("image/png")

        decision = client.post(
            "/decisions",
            json={"project_id":project_id,"target_type":"anchor","target_id":anchors[0]["id"],"action":"accept","reason":"integration test","warnings":[]},
        )
        assert decision.status_code == 200, decision.text
        assert client.get(f"/projects/{project_id}/decisions").json()[0]["id"] == decision.json()["id"]
        composition_job = client.post(
            f"/projects/{project_id}/composition-jobs",
            json={"anchor_id":anchors[0]["id"],"caption":"改性材料在不同温度下表现出更高转化率。","template":"2x2","chart_ids":[chart["id"]],"panels":[{"kind":"chart","source_id":chart["id"],"label":"a","crop_zoom":1.0}],"labels":["a"],"width_mm":89,"dpi":600},
        )
        assert composition_job.status_code == 202, composition_job.text
        claimed = claim_next_job(); assert claimed and claimed["id"] == composition_job.json()["job_id"]
        run_persisted_job(claimed)
        composition_status = client.get(f"/jobs/{claimed['id']}").json()
        assert composition_status["state"] == "completed"
        composition = composition_status["result"]
        assert set(composition["download_urls"]) == {"svg","pdf","png","tiff"}
        assert not any("源图分辨率低于 600 dpi" in warning for warning in composition["warnings"])
        composition_preview = client.get(composition["preview_url"])
        assert composition_preview.status_code == 200 and composition_preview.headers["content-type"].startswith("image/png")
        assert client.get(f"/projects/{project_id}/compositions").json()[0]["template"] == "2x2"
        checkpoints = client.get(f"/projects/{project_id}/checkpoints").json()
        assert checkpoints
        checkpoint_anchor_text = client.get(f"/projects/{project_id}/anchors").json()[0]["text"]
        db.execute("UPDATE anchors SET text='temporary mutation' WHERE id=?", (anchors[0]["id"],))
        db.execute("DELETE FROM compositions WHERE id=?", (composition["id"],))
        restored_checkpoint = client.post(f"/checkpoints/{checkpoints[0]['id']}/restore")
        assert restored_checkpoint.status_code == 200, restored_checkpoint.text
        assert restored_checkpoint.json()["restore_scope"] == "full_workspace"
        assert client.get(f"/projects/{project_id}/anchors").json()[0]["text"] == checkpoint_anchor_text
        assert client.get(f"/projects/{project_id}/compositions").json()[0]["id"] == composition["id"]

        export_job = client.post(f"/projects/{project_id}/export-jobs", json={"override_statistics":False})
        assert export_job.status_code == 202, export_job.text
        claimed = claim_next_job(); assert claimed and claimed["id"] == export_job.json()["job_id"]
        run_persisted_job(claimed)
        export_status = client.get(f"/jobs/{claimed['id']}").json()
        assert export_status["state"] == "completed"
        archive = client.get(export_status["result"]["download_url"])
        assert archive.status_code == 200 and len(archive.content) > 1000
        export_history = client.get(f"/projects/{project_id}/exports").json()
        assert export_history[0]["id"] == export_status["result"]["id"]
        assert set(export_history[0]["formats"]) == {"svg","pdf","png","tiff","xlsx","chart-package"}
        with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
            assert {"manifest.json", "audit-report.md", "asset-license-manifest.csv", "placement-report.csv", "figure-placement-report.csv", "document-revisions.csv", "full-document-revisions.json", "paper_illustrated.md"}.issubset(bundle.namelist())
            assert str(db.PROJECTS_ROOT.resolve()) not in bundle.read("manifest.json").decode("utf-8")
            license_manifest = bundle.read("asset-license-manifest.csv").decode("utf-8-sig")
            assert "license_url" in license_manifest
            assert "verification_source" in license_manifest
            assert "https://creativecommons.org/licenses/by/4.0/" in license_manifest
            assert "accept" in license_manifest and "reject" in license_manifest
            illustrated = bundle.read("paper_illustrated.md").decode("utf-8")
            assert "最终修订版本" in illustrated
            assert "![图 1｜改性材料在不同温度下表现出更高转化率。](figures/composition-" in illustrated
            assert "改性材料在不同温度下表现出更高转化率" in bundle.read("figure-placement-report.csv").decode("utf-8-sig")
            assert any(name.startswith("data/") and name.endswith(".csv") for name in bundle.namelist())
            assert any(name.startswith("figures/") and name.endswith(".svg") for name in bundle.namelist())
            assert any(name.startswith("figures/composition-") and name.endswith(".tiff") for name in bundle.namelist())
            reproduction_scripts = [name for name in bundle.namelist() if name.startswith("scripts/") and name.endswith("_reproduce.py")]
            assert len(reproduction_scripts) == 1
            assert "DATA_PATH = ROOT / \"data\"" in bundle.read(reproduction_scripts[0]).decode("utf-8")

        portable_pending_job = client.post(f"/projects/{project_id}/analyze").json()["job_id"]
        source_checkpoints = client.get(f"/projects/{project_id}/checkpoints").json()
        assert source_checkpoints
        source_checkpoint_id = source_checkpoints[0]["id"]
        source_root_text = str(db.PROJECTS_ROOT.resolve())
        portable = client.get(f"/projects/{project_id}/package")
        assert portable.status_code == 200
        portable_bytes = portable.content
        with zipfile.ZipFile(io.BytesIO(portable_bytes)) as package:
            manifest_text = package.read("project-manifest.json").decode("utf-8")
            assert source_root_text not in manifest_text
            for name in package.namelist():
                if name.startswith("checkpoints/") and name.endswith(".json"):
                    assert source_root_text not in package.read(name).decode("utf-8")
        assert client.get("/settings/update").json()["configured"] is False

    configure_storage(monkeypatch, tmp_path / "destination-workspace")
    with TestClient(app) as destination:
        authenticate(destination)
        imported = destination.post(
            "/projects/import",
            files={"file": ("portable.nfw-project.zip", portable_bytes, "application/zip")},
        )
        assert imported.status_code == 200, imported.text
        restored = destination.get(f"/projects/{project_id}").json()
        assert restored["name"] == "闭环验收项目"
        assert len(restored["anchors"]) >= 4
        assert destination.get(f"/projects/{project_id}/assistant/messages").json()
        assert destination.get(f"/anchors/{restored['anchors'][0]['id']}/revisions").json()
        assert destination.get(f"/projects/{project_id}/document-revisions").json()
        imported_compositions = destination.get(f"/projects/{project_id}/compositions").json()
        assert imported_compositions and destination.get(imported_compositions[0]["preview_url"]).status_code == 200
        imported_jobs = destination.get(f"/projects/{project_id}/jobs").json()
        stopped_job = next(job for job in imported_jobs if job["id"] == portable_pending_job)
        assert stopped_job["state"] == "cancelled"
        assert stopped_job["stage"] == "便携包导入时停止"
        imported_checkpoints = destination.get(f"/projects/{project_id}/checkpoints").json()
        assert any(checkpoint["id"] == source_checkpoint_id for checkpoint in imported_checkpoints)
        restored_checkpoint = destination.post(f"/checkpoints/{source_checkpoint_id}/restore")
        assert restored_checkpoint.status_code == 200, restored_checkpoint.text
        imported_exports = destination.get(f"/projects/{project_id}/exports").json()
        assert any(item["id"] == export_status["result"]["id"] for item in imported_exports)
        imported_export = next(item for item in imported_exports if item["id"] == export_status["result"]["id"])
        assert destination.get(imported_export["download_url"]).status_code == 200


def test_project_import_rejects_path_traversal(tmp_path: Path, monkeypatch):
    configure_storage(monkeypatch, tmp_path)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("../escape.txt", "unsafe")
        archive.writestr("project-manifest.json", "{}")
    with TestClient(app) as client:
        authenticate(client)
        response = client.post(
            "/projects/import",
            files={"file": ("unsafe.zip", payload.getvalue(), "application/zip")},
        )
        assert response.status_code == 400
        assert not (tmp_path.parent / "escape.txt").exists()


def test_export_download_rejects_database_path_outside_project(tmp_path: Path, monkeypatch):
    configure_storage(monkeypatch, tmp_path / "workspace")
    outside = tmp_path / "outside.zip"
    outside.write_bytes(b"not a project export")
    with TestClient(app) as client:
        authenticate(client)
        project_id = client.post("/projects", json={"name":"Export path safety","quality_mode":"quality"}).json()["id"]
        export_id = "e" * 32
        db.execute(
            "INSERT INTO exports VALUES (?,?,?,?,?,?)",
            (export_id, project_id, str(outside), "[]", "[]", db.now()),
        )
        response = client.get(f"/exports/{export_id}/download")
        assert response.status_code == 400
        assert outside.read_bytes() == b"not a project export"


def test_project_import_rejects_expanded_size_and_duplicate_paths(tmp_path: Path, monkeypatch):
    configure_storage(monkeypatch, tmp_path)
    project_id = "b" * 32
    manifest = json.dumps({"project":{"id":project_id,"name":"Safety test"},"state":{}})

    oversized = io.BytesIO()
    with zipfile.ZipFile(oversized, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project-manifest.json", manifest)
        archive.writestr("assets/repeated.txt", b"x" * 64)
    monkeypatch.setattr(main_module, "PACKAGE_EXPANDED_LIMIT", 32)
    with TestClient(app) as client:
        authenticate(client)
        response = client.post(
            "/projects/import",
            files={"file":("oversized.zip",oversized.getvalue(),"application/zip")},
        )
        assert response.status_code == 413
        assert not (db.PROJECTS_ROOT / project_id).exists()

    duplicate = io.BytesIO()
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("project-manifest.json", manifest)
        archive.writestr("assets/figure.png", b"one")
        archive.writestr("Assets/figure.png", b"two")
    monkeypatch.setattr(main_module, "PACKAGE_EXPANDED_LIMIT", 2 * 1024 * 1024 * 1024)
    with TestClient(app) as client:
        authenticate(client)
        response = client.post(
            "/projects/import",
            files={"file":("duplicate.zip",duplicate.getvalue(),"application/zip")},
        )
        assert response.status_code == 400
        assert "Duplicate path" in response.text
        assert not (db.PROJECTS_ROOT / project_id).exists()


def test_project_import_rolls_back_partial_database_and_files(tmp_path: Path, monkeypatch):
    configure_storage(monkeypatch, tmp_path)
    project_id = "c" * 32
    payload = io.BytesIO()
    manifest = {
        "project":{"id":project_id,"name":"Rollback test","quality_mode":"quality"},
        "state":{"documents":[{"id":"d" * 32}]},
    }
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("project-manifest.json", json.dumps(manifest))
        archive.writestr("source/paper.md", "# incomplete")
    with TestClient(app, raise_server_exceptions=False) as client:
        authenticate(client)
        response = client.post(
            "/projects/import",
            files={"file":("rollback.zip",payload.getvalue(),"application/zip")},
        )
        assert response.status_code == 500
        assert db.fetch_one("SELECT id FROM projects WHERE id=?", (project_id,)) is None
        assert not (db.PROJECTS_ROOT / project_id).exists()


def test_upload_rejects_spoofed_file_signatures(tmp_path: Path, monkeypatch):
    configure_storage(monkeypatch, tmp_path)
    with TestClient(app) as client:
        authenticate(client)
        project_id = client.post("/projects", json={"name":"signature test","quality_mode":"quality"}).json()["id"]
        fake_pdf = client.post(
            f"/projects/{project_id}/documents",
            files={"file": ("spoofed.pdf", b"This is not a PDF", "application/pdf")},
        )
        assert fake_pdf.status_code == 415
        fake_docx = client.post(
            f"/projects/{project_id}/documents",
            files={"file": ("spoofed.docx", b"PK\x03\x04not-a-real-office-archive", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        assert fake_docx.status_code == 415
        binary_csv = client.post(
            f"/projects/{project_id}/datasets",
            files={"file": ("binary.csv", b"name,value\x00hidden", "text/csv")},
        )
        assert binary_csv.status_code == 415
        assert not list((db.PROJECTS_ROOT / project_id / "source").iterdir())
        assert not list((db.PROJECTS_ROOT / project_id / "datasets").iterdir())


def test_docx_export_inserts_anchored_figure_and_caption(tmp_path: Path, monkeypatch):
    configure_storage(monkeypatch, tmp_path)
    document = DocxDocument()
    document.add_heading("催化材料研究", level=1)
    document.add_paragraph("多级孔结构可改善传质并提高活性位点可达性。")
    document.add_paragraph("改性材料在独立合成批次中表现出更高转化率。")
    source = io.BytesIO(); document.save(source)
    data = b"temperature,conversion,material\n250,52,Control\n300,61,Control\n250,70,Modified\n300,84,Modified\n"
    with TestClient(app) as client:
        authenticate(client)
        project_id = client.post("/projects", json={"name":"DOCX backfill","quality_mode":"quality"}).json()["id"]
        assert client.post(f"/projects/{project_id}/documents", files={"file":("paper.docx",source.getvalue(),"application/vnd.openxmlformats-officedocument.wordprocessingml.document")}).status_code == 200
        job_id = client.post(f"/projects/{project_id}/analyze").json()["job_id"]
        claimed = claim_next_job(); assert claimed and claimed["id"] == job_id
        _analyze(project_id, job_id)
        anchor = client.get(f"/projects/{project_id}/anchors").json()[0]
        dataset = client.post(f"/projects/{project_id}/datasets", files={"file":("data.csv",data,"text/csv")}).json()
        chart = client.post(f"/projects/{project_id}/charts", json={
            "dataset_id":dataset["id"], "anchor_id":anchor["id"], "chart_type":"grouped_bar",
            "x":"temperature", "y":"conversion", "group":"material", "title":"温度依赖的材料转化率",
            "independent_unit":"独立合成批次", "error_bar":"none", "statistical_test":"描述性比较，不进行推断检验",
        })
        assert chart.status_code == 200, chart.text
        exported = client.post(f"/projects/{project_id}/exports", json={"override_statistics":False})
        assert exported.status_code == 200, exported.text
        archive = client.get(exported.json()["download_url"])
        with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
            output_name = next(name for name in bundle.namelist() if name.endswith("_配图版.docx"))
            illustrated = DocxDocument(io.BytesIO(bundle.read(output_name)))
            assert len(illustrated.inline_shapes) == 1
            assert any("图 1｜温度依赖的材料转化率" in paragraph.text for paragraph in illustrated.paragraphs)
            assert "figure-placement-report.csv" in bundle.namelist()


def test_projects_root_migration_requires_confirmation_and_moves_projects(tmp_path: Path, monkeypatch):
    configure_storage(monkeypatch, tmp_path / "source")
    target = tmp_path / "destination" / "projects"
    csv_data = b"temperature,conversion,material\n250,52,Control\n300,61,Control\n250,70,Modified\n300,84,Modified\n"
    with TestClient(app) as client:
        authenticate(client)
        project = client.post("/projects", json={"name":"Move me","quality_mode":"quality"}).json()
        project_id = project["id"]
        dataset = client.post(
            f"/projects/{project_id}/datasets",
            files={"file":("migration.csv",csv_data,"text/csv")},
        ).json()
        chart = client.post(f"/projects/{project_id}/charts", json={
            "dataset_id":dataset["id"], "chart_type":"grouped_bar",
            "x":"temperature", "y":"conversion", "group":"material",
            "title":"Migration-safe chart",
            "independent_unit":"independent synthesis batch",
            "error_bar":"none", "statistical_test":"descriptive comparison",
        })
        assert chart.status_code == 200, chart.text
        chart = chart.json()
        old_root = db.PROJECTS_ROOT.resolve()
        assert Path(db.fetch_one("SELECT local_path FROM datasets WHERE id=?", (dataset["id"],))["local_path"]).is_relative_to(old_root)
        without_files = client.post(
            "/settings/projects-root",
            json={"path":str(target),"migrate_existing":False,"confirmed":True},
        )
        assert without_files.status_code == 409
        assert without_files.json()["detail"]["code"] == "migration_required"
        preview = client.post("/settings/projects-root", json={"path":str(target),"confirmed":False})
        assert preview.status_code == 409
        migrated = client.post("/settings/projects-root", json={"path":str(target),"confirmed":True})
        assert migrated.status_code == 200, migrated.text
        assert migrated.json()["migrated"] == 1
        assert migrated.json()["migrated_bytes"] > 0
        assert (target / project_id).is_dir()
        assert db.PROJECT_ROOT_CONFIG.read_text(encoding="utf-8") == str(target.resolve())
        dataset_path = Path(db.fetch_one("SELECT local_path FROM datasets WHERE id=?", (dataset["id"],))["local_path"])
        assert dataset_path.is_relative_to(target.resolve()) and dataset_path.exists()
        output_paths = json.loads(db.fetch_one("SELECT outputs_json FROM charts WHERE id=?", (chart["id"],))["outputs_json"])
        assert all(Path(path).is_relative_to(target.resolve()) and Path(path).exists() for path in output_paths.values())
        checkpoint_rows = db.fetch_all("SELECT manifest_path FROM checkpoints WHERE project_id=?", (project_id,))
        assert checkpoint_rows
        for checkpoint in checkpoint_rows:
            manifest_path = Path(checkpoint["manifest_path"])
            assert manifest_path.is_relative_to(target.resolve()) and manifest_path.exists()
            assert str(old_root) not in manifest_path.read_text(encoding="utf-8")
        restored_preview = client.get(chart["preview_url"])
        assert restored_preview.status_code == 200
        assert restored_preview.headers["content-type"].startswith("image/png")
        portable = client.get(f"/projects/{project_id}/package")
        assert portable.status_code == 200 and len(portable.content) > 1000


def test_update_check_is_manual_and_uses_validated_manifest(tmp_path: Path, monkeypatch):
    configure_storage(monkeypatch, tmp_path)
    manifest_url = "https://updates.example.test/nature-workbench.json"
    monkeypatch.setenv("NATURE_UPDATE_MANIFEST_URL", manifest_url)
    request_count = 0

    async def fake_get(_client, url, **_kwargs):
        nonlocal request_count
        request_count += 1
        assert url == manifest_url
        return httpx.Response(
            200,
            json={
                "version":"0.2.0",
                "release_url":"https://updates.example.test/releases/0.2.0",
                "published_at":"2026-07-24T08:00:00Z",
                "notes":"新增本地科研流程验收。",
            },
            request=httpx.Request("GET", manifest_url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    with TestClient(app) as client:
        authenticate(client)
        status = client.get("/settings/update")
        assert status.status_code == 200
        assert status.json()["configured"] is True
        assert request_count == 0
        checked = client.post("/settings/update/check")
        assert checked.status_code == 200, checked.text
        assert checked.json()["update_available"] is True
        assert checked.json()["latest_version"] == "0.2.0"
        assert request_count == 1


def test_trash_purge_removes_only_projects_older_than_30_days(tmp_path: Path, monkeypatch):
    configure_storage(monkeypatch, tmp_path)
    with TestClient(app) as client:
        authenticate(client)
        expired = client.post("/projects", json={"name":"Expired","quality_mode":"quality"}).json()
        recent = client.post("/projects", json={"name":"Recent","quality_mode":"quality"}).json()
        client.post(f"/projects/{expired['id']}/trash")
        client.post(f"/projects/{recent['id']}/trash")
        expired_at = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        db.execute("UPDATE projects SET trashed_at=? WHERE id=?", (expired_at, expired["id"]))

        purged = client.delete("/trash/purge")
        assert purged.status_code == 200
        assert purged.json()["purged"] == 1
        all_projects = client.get("/projects?include_trash=true").json()
        assert not any(project["id"] == expired["id"] for project in all_projects)
        assert any(project["id"] == recent["id"] for project in all_projects)
        assert not (db.PROJECTS_ROOT / expired["id"]).exists()
        assert (db.PROJECTS_ROOT / recent["id"]).exists()


def test_local_account_persists_preferences_and_hashes_password(tmp_path: Path, monkeypatch):
    configure_storage(monkeypatch, tmp_path)
    with TestClient(app) as client:
        assert client.get("/session").status_code == 200
        assert client.get("/account/status").json()["authenticated"] is False
        assert client.get("/projects").status_code == 401
        account = authenticate(client)
        assert account["preferences"]["annotations_enabled"] is True

        updated = client.patch(
            "/account/preferences",
            json={"annotations_enabled":False,"last_screen":"settings"},
        )
        assert updated.status_code == 200
        assert updated.json()["preferences"] == {"annotations_enabled":False,"last_screen":"settings"}
        user_row = db.fetch_one("SELECT * FROM users WHERE username='attack'")
        assert user_row and user_row["password_hash"] != "123456"
        assert len(user_row["password_salt"]) == 32

        changed = client.post(
            "/account/password",
            json={"current_password":"123456","new_password":"LocalOnly-2026"},
        )
        assert changed.status_code == 200
        assert changed.json()["must_change_password"] is False
        assert client.post("/account/logout").status_code == 200
        assert client.get("/projects").status_code == 401
        assert client.post("/account/login", json={"username":"attack","password":"123456"}).status_code == 401
        assert client.post("/account/login", json={"username":"attack","password":"LocalOnly-2026"}).status_code == 200


def test_project_read_endpoints_anchor_restore_and_trash_restore(tmp_path: Path, monkeypatch):
    configure_storage(monkeypatch, tmp_path)
    paper = "# Read contract\n\n## Results\n\nThe first measured response remains traceable."
    data = "sample,value\nA,1\nB,2\n"

    with TestClient(app) as client:
        authenticate(client)
        project = client.post(
            "/projects",
            json={"name":"Read and restore contract","quality_mode":"quality"},
        ).json()
        project_id = project["id"]
        uploaded = client.post(
            f"/projects/{project_id}/documents",
            files={"file":("contract.md",paper.encode("utf-8"),"text/markdown")},
        )
        assert uploaded.status_code == 200, uploaded.text
        documents = client.get(f"/projects/{project_id}/documents")
        assert documents.status_code == 200
        assert documents.json()[0]["filename"] == "contract.md"

        job_id = client.post(f"/projects/{project_id}/analyze").json()["job_id"]
        claimed = claim_next_job()
        assert claimed and claimed["id"] == job_id
        _analyze(project_id, job_id)

        detail = client.get(f"/projects/{project_id}")
        assert detail.status_code == 200
        assert detail.json()["documents"][0]["id"] == uploaded.json()["id"]
        assert detail.json()["anchors"]
        content = client.get(f"/projects/{project_id}/document-content")
        assert content.status_code == 200
        assert content.json()["anchors"] == detail.json()["anchors"]

        anchor = content.json()["anchors"][0]
        empty_edit = client.patch(
            f"/anchors/{anchor['id']}",
            json={"text":"   ","reason":"reject whitespace-only edit"},
        )
        assert empty_edit.status_code == 422
        edited = client.patch(
            f"/anchors/{anchor['id']}",
            json={"text":f"{anchor['text']} Revised.","reason":"legacy endpoint audit"},
        )
        assert edited.status_code == 200, edited.text
        revisions = client.get(f"/anchors/{anchor['id']}/revisions")
        assert revisions.status_code == 200 and revisions.json()
        restored = client.post(f"/anchor-revisions/{revisions.json()[0]['id']}/restore")
        assert restored.status_code == 200, restored.text
        assert restored.json()["text"] == anchor["text"]

        dataset = client.post(
            f"/projects/{project_id}/datasets",
            files={"file":("contract.csv",data.encode("utf-8"),"text/csv")},
        )
        assert dataset.status_code == 200, dataset.text
        dataset_detail = client.get(f"/datasets/{dataset.json()['id']}")
        assert dataset_detail.status_code == 200
        assert dataset_detail.json()["rows"] == 2

        trashed = client.post(f"/projects/{project_id}/trash")
        assert trashed.status_code == 200
        assert project_id not in {item["id"] for item in client.get("/projects").json()}
        restored_project = client.post(f"/projects/{project_id}/restore")
        assert restored_project.status_code == 200
        assert restored_project.json()["status"] == "已恢复"
        assert project_id in {item["id"] for item in client.get("/projects").json()}

        assert client.get("/projects/missing-project/jobs").status_code == 404


def test_upload_limits_remove_partial_files(tmp_path: Path, monkeypatch):
    configure_storage(monkeypatch, tmp_path)
    monkeypatch.setattr(main_module, "DOC_LIMIT", 8)
    monkeypatch.setattr(main_module, "DATA_LIMIT", 8)

    with TestClient(app) as client:
        authenticate(client)
        project_id = client.post(
            "/projects",
            json={"name":"Upload limit contract","quality_mode":"quality"},
        ).json()["id"]
        oversized_document = client.post(
            f"/projects/{project_id}/documents",
            files={"file":("too-large.md",b"123456789","text/markdown")},
        )
        oversized_dataset = client.post(
            f"/projects/{project_id}/datasets",
            files={"file":("too-large.csv",b"123456789","text/csv")},
        )
        assert oversized_document.status_code == 413
        assert oversized_dataset.status_code == 413
        project_root = db.project_dir(project_id)
        assert not list((project_root / "source").glob("*"))
        assert not list((project_root / "datasets").glob("*"))


def test_invalid_dataset_and_chart_return_422_without_artifacts(tmp_path: Path, monkeypatch):
    configure_storage(monkeypatch,tmp_path)

    with TestClient(app) as client:
        authenticate(client)
        project_id = client.post(
            "/projects",
            json={"name":"Dataset validation contract","quality_mode":"quality"},
        ).json()["id"]
        empty_dataset = client.post(
            f"/projects/{project_id}/datasets",
            files={"file":("empty.csv",b"x,y\n","text/csv")},
        )
        assert empty_dataset.status_code == 422
        assert db.fetch_one("SELECT id FROM datasets WHERE project_id=?",(project_id,)) is None
        project_root = db.project_dir(project_id)
        assert not list((project_root / "datasets").glob("*"))

        uploaded = client.post(
            f"/projects/{project_id}/datasets",
            files={"file":("text-response.csv",b"category,response\nA,low\nB,high\n","text/csv")},
        )
        assert uploaded.status_code == 200,uploaded.text
        invalid_chart = client.post(
            f"/projects/{project_id}/charts",
            json={
                "dataset_id":uploaded.json()["id"],
                "chart_type":"grouped_bar",
                "x":"category",
                "y":"response",
                "title":"Invalid numeric response",
            },
        )
        assert invalid_chart.status_code == 422
        assert "数值字段" in invalid_chart.json()["detail"]
        assert db.fetch_one("SELECT id FROM charts WHERE project_id=?",(project_id,)) is None
        assert not list((project_root / "charts").glob("*"))


def test_failed_job_messages_redact_sensitive_details(tmp_path: Path, monkeypatch):
    configure_storage(monkeypatch, tmp_path)

    with TestClient(app) as client:
        authenticate(client)
        project_id = client.post(
            "/projects",
            json={"name":"Failure redaction contract","quality_mode":"quality"},
        ).json()["id"]
        uploaded = client.post(
            f"/projects/{project_id}/documents",
            files={"file":("paper.md",b"# Results\n\nSafe source text.","text/markdown")},
        )
        assert uploaded.status_code == 200, uploaded.text
        job_id = client.post(f"/projects/{project_id}/analyze").json()["job_id"]
        claimed = claim_next_job()
        assert claimed and claimed["id"] == job_id

        def fail_with_sensitive_details(_path):
            raise RuntimeError("contact test@example.edu or 13800138000")

        monkeypatch.setattr(main_module, "parse_document", fail_with_sensitive_details)
        _analyze(project_id, job_id)
        failed = client.get(f"/jobs/{job_id}").json()
        assert failed["state"] == "failed"
        assert "test@example.edu" not in failed["message"]
        assert "13800138000" not in failed["message"]
        assert "[EMAIL]" in failed["message"]
        assert "[PHONE]" in failed["message"]


def test_local_security_rejects_untrusted_host_origin_and_locks_repeated_login(tmp_path: Path, monkeypatch):
    configure_storage(monkeypatch, tmp_path)
    with TestClient(app) as client:
        assert client.get("/session", headers={"host":"untrusted.example"}).status_code == 400
        assert client.get("/session", headers={"origin":"https://untrusted.example"}).status_code == 403
        assert client.get("/session").status_code == 200
        for _ in range(5):
            failed = client.post("/account/login", json={"username":"attack","password":"incorrect"})
            assert failed.status_code == 401
        locked = client.post("/account/login", json={"username":"attack","password":"123456"})
        assert locked.status_code == 429
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        db.execute("UPDATE users SET locked_until=? WHERE username='attack'", (past,))
        assert client.post("/account/login", json={"username":"attack","password":"123456"}).status_code == 200


def test_worker_requeues_interrupted_jobs_after_restart(tmp_path: Path, monkeypatch):
    configure_storage(monkeypatch, tmp_path)
    with TestClient(app) as client:
        authenticate(client)
        project_id = client.post("/projects", json={"name":"Restart recovery","quality_mode":"quality"}).json()["id"]
        uploaded = client.post(
            f"/projects/{project_id}/documents",
            files={"file":("restart-recovery.md",b"# Results\n\nSynthetic material for worker restart recovery.","text/markdown")},
        )
        assert uploaded.status_code == 200, uploaded.text
        queued = client.post(f"/projects/{project_id}/analyze")
        assert queued.status_code == 200, queued.text
        assert queued.json().get("job_id"), queued.text
        job_id = queued.json()["job_id"]
        claimed = claim_next_job()
        assert claimed and claimed["id"] == job_id and claimed["state"] == "running"
        assert recover_interrupted_jobs() == 1
        recovered = client.get(f"/jobs/{job_id}").json()
        assert recovered["state"] == "queued"
        assert recovered["stage"] == "应用重启后恢复"


def test_open_search_distinguishes_total_failure_and_partial_success(tmp_path: Path, monkeypatch):
    configure_storage(monkeypatch, tmp_path)
    paper = b"# Results\n\nPorous catalyst morphology supports improved mass transfer and active-site exposure."
    with TestClient(app) as client:
        authenticate(client)
        project_id = client.post("/projects", json={"name":"Search recovery","quality_mode":"quality"}).json()["id"]
        assert client.post(
            f"/projects/{project_id}/documents",
            files={"file":("paper.md",paper,"text/markdown")},
        ).status_code == 200
        job_id = client.post(f"/projects/{project_id}/analyze").json()["job_id"]
        claimed = claim_next_job()
        assert claimed and claimed["id"] == job_id
        _analyze(project_id, job_id)
        anchor_id = client.get(f"/projects/{project_id}/anchors").json()[0]["id"]

        async def all_failed(_query):
            return [], {
                "successful_providers":[],
                "failed_providers":["Openverse","Wikimedia Commons","Europe PMC"],
                "partial":False,
            }

        monkeypatch.setattr(main_module, "search_open_assets", all_failed)
        unavailable = client.post(f"/anchors/{anchor_id}/search", json={"query":"porous catalyst"})
        assert unavailable.status_code == 503
        assert unavailable.json()["detail"]["code"] == "open_search_unavailable"
        assert unavailable.json()["detail"]["retryable"] is True

        async def partial(_query):
            return [{
                "title":"Open catalyst image",
                "provider":"Openverse",
                "source_url":"https://example.test/catalyst",
                "preview_url":None,
                "author":"Researcher",
                "license":{"name":"CC BY 4.0","url":None,"attribution_required":True,"verified":True,"restrictions":[]},
                "reuse_status":"attribution_required",
                "score":0.92,
                "rationale":"Open license metadata available.",
            }], {
                "successful_providers":["Openverse"],
                "failed_providers":["Wikimedia Commons","Europe PMC"],
                "partial":True,
            }

        monkeypatch.setattr(main_module, "search_open_assets", partial)
        recovered = client.post(f"/anchors/{anchor_id}/search", json={"query":"porous catalyst"})
        assert recovered.status_code == 200, recovered.text
        assert len(recovered.json()) == 1
        assert recovered.headers["X-NFW-Search-Providers"] == "Openverse"
        assert "Wikimedia Commons" in recovered.headers["X-NFW-Search-Warning"]


def test_pdf_export_always_contains_exact_position_report_without_modifying_source(tmp_path: Path, monkeypatch):
    configure_storage(monkeypatch, tmp_path)
    source = fitz.open()
    page = source.new_page()
    page.insert_textbox(
        fitz.Rect(72, 72, 510, 190),
        "Catalyst conversion increased across independent synthesis batches. "
        "This text PDF contains enough embedded content to retain page and block coordinates.",
        fontsize=11,
    )
    pdf_bytes = source.tobytes()
    source.close()

    with TestClient(app) as client:
        authenticate(client)
        project_id = client.post("/projects", json={"name":"PDF placement","quality_mode":"quality"}).json()["id"]
        uploaded = client.post(
            f"/projects/{project_id}/documents",
            files={"file":("paper.pdf",pdf_bytes,"application/pdf")},
        )
        assert uploaded.status_code == 200, uploaded.text
        job_id = client.post(f"/projects/{project_id}/analyze").json()["job_id"]
        claimed = claim_next_job()
        assert claimed and claimed["id"] == job_id
        _analyze(project_id, job_id)
        document_row = db.fetch_one("SELECT local_path,sha256 FROM documents WHERE project_id=?", (project_id,))
        assert document_row
        source_path = Path(document_row["local_path"])
        before = source_path.read_bytes()

        missing_reason = client.post(
            f"/projects/{project_id}/export-jobs",
            json={"override_statistics":True,"override_reason":"确认"},
        )
        assert missing_reason.status_code == 422
        exported = client.post(f"/projects/{project_id}/exports", json={"override_statistics":False})
        assert exported.status_code == 200, exported.text
        assert exported.json()["formats"] == []
        archive = client.get(exported.json()["download_url"])
        with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
            assert "PDF-document-and-figure-placement.md" in bundle.namelist()
            report = bundle.read("PDF-document-and-figure-placement.md").decode("utf-8")
            assert "原 PDF 未修改" in report
            assert "页 1" in report
            assert "坐标 [" in report
        assert source_path.read_bytes() == before == pdf_bytes


def test_latex_export_creates_new_source_with_figure_and_escaped_caption(tmp_path: Path, monkeypatch):
    configure_storage(monkeypatch, tmp_path)
    tex = (
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\section{Results}\n"
        "Catalyst conversion increased across independent batches and remained stable during cycling.\n"
        "\\end{document}\n"
    ).encode("utf-8")
    data = b"group,value\nControl,52\nControl,61\nModified,70\nModified,84\n"
    with TestClient(app) as client:
        authenticate(client)
        project_id = client.post("/projects", json={"name":"LaTeX export","quality_mode":"quality"}).json()["id"]
        assert client.post(
            f"/projects/{project_id}/documents",
            files={"file":("paper.tex",tex,"application/x-tex")},
        ).status_code == 200
        job_id = client.post(f"/projects/{project_id}/analyze").json()["job_id"]
        claimed = claim_next_job()
        assert claimed and claimed["id"] == job_id
        _analyze(project_id, job_id)
        anchor = client.get(f"/projects/{project_id}/anchors").json()[0]
        dataset = client.post(
            f"/projects/{project_id}/datasets",
            files={"file":("data.csv",data,"text/csv")},
        ).json()
        chart = client.post(f"/projects/{project_id}/charts", json={
            "dataset_id":dataset["id"], "anchor_id":anchor["id"],
            "chart_type":"grouped_bar", "x":"group", "y":"value",
            "title":"Catalyst A&B_1 response",
            "independent_unit":"independent synthesis batch",
            "error_bar":"none",
            "statistical_test":"descriptive comparison only",
        })
        assert chart.status_code == 200, chart.text
        exported = client.post(f"/projects/{project_id}/exports", json={"override_statistics":False})
        assert exported.status_code == 200, exported.text
        assert set(exported.json()["formats"]) == {"svg","pdf","png","tiff","xlsx","chart-package"}
        archive = client.get(exported.json()["download_url"])
        with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
            assert "paper_illustrated.tex" in bundle.namelist()
            illustrated = bundle.read("paper_illustrated.tex").decode("utf-8")
            assert "\\usepackage{graphicx}" in illustrated
            assert "\\includegraphics[width=\\linewidth]{figures/" in illustrated
            assert "Catalyst A\\&B\\_1 response" in illustrated
        assert tex == Path(db.fetch_one("SELECT local_path FROM documents WHERE project_id=?", (project_id,))["local_path"]).read_bytes()
