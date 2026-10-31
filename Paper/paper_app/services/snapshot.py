"""实验记录 - 人类可读快照。

即使 SQLite 库彻底损坏，也能凭这些文件重建内容：

* `projects.json` / `records.json` / `attachments.json`：全部结构化字段与附件哈希；
* `<项目名>.md`：与"导出当前项目"一致的 Markdown；
* `manifest.json`：快照元数据与校验和。

快照写到备份根下的 `snapshots\\<日期>\\<时间>\\`，每次运行一个独立目录，不覆盖历史。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import db, paths
from . import backup as backup_service
from . import export as export_service

DEFAULT_KEEP = 60


def root() -> Path:
    return paths.backup_root() / "snapshots"


def _dir() -> Path:
    now = datetime.now()
    base = root() / now.strftime("%Y-%m-%d") / now.strftime("%H%M%S")
    index = 2
    while base.exists():
        base = base.parent / f"{now.strftime('%H%M%S')}-{index}"
        index += 1
    base.mkdir(parents=True)
    return base


def _write_json(path: Path, payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def export_snapshot(note: str = "") -> dict[str, Any]:
    """导出全部项目、记录与附件清单；返回 manifest。"""
    target = _dir()
    # 注意：db.list_projects/list_records 的 include_trashed=True 表示"只看回收站"，
    # 因此活动项与回收站项分开取再合并。
    projects = db.list_projects() + db.list_projects(include_trashed=True)
    all_records: list[dict[str, Any]] = []
    per_project: dict[str, list[dict[str, Any]]] = {}
    attachments: list[dict[str, Any]] = []
    for project in projects:
        live = db.list_records(project_id=project["id"], full=True)
        trashed = db.list_records(project_id=project["id"], include_trashed=True, full=True)
        records = live + trashed
        for record in records:
            items = db.list_attachments(record["id"])
            record["attachments"] = items
            for item in items:
                attachments.append(
                    {
                        "record_id": record["id"],
                        "project_id": project["id"],
                        "id": item.get("id"),
                        "filename": item.get("filename"),
                        "sha256": item.get("sha256"),
                        "size_bytes": item.get("size_bytes"),
                        "local_path": item.get("local_path"),
                    }
                )
        per_project[project["id"]] = records
        all_records.extend(records)
        markdown = export_service.project_to_markdown(project, records)
        name = (project.get("name") or project["id"]).strip() or project["id"]
        safe = "".join(ch if ch not in '\\/:*?"<>|' else "_" for ch in name)[:60]
        (target / f"{safe}.md").write_text(markdown, encoding="utf-8")

    digests = {
        "projects.json": _write_json(target / "projects.json", projects),
        "records.json": _write_json(target / "records.json", all_records),
        "attachments.json": _write_json(target / "attachments.json", attachments),
    }
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "note": note,
        "dir": str(target),
        "project_count": len(projects),
        "record_count": len(all_records),
        "attachment_count": len(attachments),
        "files": digests,
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    try:
        maybe_prune()
    except Exception:  # noqa: BLE001 - 轮转失败不影响本次快照
        pass
    return manifest


def keep_limit() -> int:
    """保留份数：`PAPER_SNAPSHOT_KEEP` 环境变量优先，默认 60；0 表示全部保留。"""
    raw = os.environ.get("PAPER_SNAPSHOT_KEEP")
    if raw is None:
        return DEFAULT_KEEP
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_KEEP


def maybe_prune() -> int:
    """删除最旧的快照目录，直到数量不超过 keep_limit()。"""
    keep = keep_limit()
    if keep <= 0:
        return 0
    keep = max(keep, 3)
    items = list_snapshots()
    removed = 0
    for directory in items[keep:]:
        try:
            for child in directory.iterdir():
                child.unlink()
            directory.rmdir()
            parent = directory.parent
            if parent != root() and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
            removed += 1
        except OSError:
            continue
    if removed:
        backup_service.audit("snapshot_prune", removed=removed, keep=keep)
    return removed


def list_snapshots() -> list[Path]:
    base = root()
    if not base.is_dir():
        return []
    found = [p for p in base.rglob("manifest.json") if p.is_file()]
    return sorted((p.parent for p in found), reverse=True)
