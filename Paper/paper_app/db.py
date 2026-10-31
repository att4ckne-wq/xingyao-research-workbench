"""实验记录 - SQLite 数据层。

沿用工作台 `backend/db.py` 验证过的模式：
WAL 连接、外键约束、CREATE TABLE IF NOT EXISTS + 缺列补列迁移、
JSON 列解码。全文检索使用 FTS5 trigram 分词（支持中文子串匹配），
短查询回退 LIKE。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import paths
from .models import BUILTIN_EVENT_TYPES
from .security import safe_filename, validate_file_signature

# 程序目录只放程序；数据、备份、导出都不在 exe 旁边（避免重建/覆盖安装时被连带删除）。
if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = paths.data_root()
DB_PATH = DATA_ROOT / "experiment.db"
ATTACHMENT_ROOT = DATA_ROOT / "attachments"

FTS_COLUMNS = ("title", "purpose", "conditions", "steps", "result", "conclusion", "content", "tags")
ATTACHMENT_SUFFIXES = {".pdf", ".docx", ".xlsx", ".csv", ".md", ".markdown", ".txt", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


class ConcurrentEditError(RuntimeError):
    """The caller edited an older snapshot; preserve both versions."""


def row_version(row: dict[str, Any]) -> str:
    omitted = {"project_name", "event_type_name", "event_type_color", "sensitive", "_version", "attachments"}
    values = {k: v for k, v in row.items() if k not in omitted}
    if isinstance(values.get("tags"), str):
        values["tags"] = json.loads(values["tags"])
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


def configure(data_root: Path) -> None:
    """重定向存储位置（测试与便携运行使用；须在 init_db 之前调用）。"""
    global DATA_ROOT, DB_PATH, ATTACHMENT_ROOT
    DATA_ROOT = Path(data_root)
    DB_PATH = DATA_ROOT / "experiment.db"
    ATTACHMENT_ROOT = DATA_ROOT / "attachments"


def db_path() -> Path:
    """当前数据库文件路径（备份与完整性检查使用）。"""
    return DB_PATH


def set_db_path(path: Path) -> None:
    """切换数据库文件（仅用于从备份恢复到新文件；不会删除原文件）。"""
    global DB_PATH
    DB_PATH = Path(path)


def integrity_status() -> tuple[str, str]:
    """检查数据库完整性，返回 (状态, 说明)。

    状态：``missing`` 尚未创建、``ok`` 正常、``corrupt`` 损坏或无法打开。
    只读打开，不会创建或修改文件。
    """
    if not DB_PATH.exists():
        return "missing", f"数据库尚未创建：{DB_PATH}"
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        return "corrupt", f"{type(exc).__name__}: {exc}"
    detail = (row[0] if row else "") or ""
    if detail.lower() == "ok":
        return "ok", "ok"
    return "corrupt", detail


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    # 单机桌面应用，用少许写入放大换掉电/崩溃时的可靠性。
    conn.execute("PRAGMA synchronous = FULL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def fetch_one(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(query, params).fetchone()
        return dict(row) if row else None


def execute(query: str, params: tuple[Any, ...] = ()) -> None:
    with connect() as conn:
        conn.execute(query, params)


def decode_json_rows(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    for row in rows:
        for field in fields:
            if row.get(field):
                row[field.removesuffix("_json")] = json.loads(row[field])
    return rows


def get_setting(key: str, default: str | None = None) -> str | None:
    row = fetch_one("SELECT value FROM app_settings WHERE key=?", (key,))
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def init_db() -> None:
    statements = [
        """CREATE TABLE IF NOT EXISTS projects (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT '进行中',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, trashed_at TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS event_types (
          id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, color TEXT NOT NULL DEFAULT '#4C78A8',
          sort INTEGER NOT NULL DEFAULT 0, builtin INTEGER NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS records (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          event_type_id TEXT NOT NULL REFERENCES event_types(id),
          title TEXT NOT NULL,
          purpose TEXT NOT NULL DEFAULT '',
          conditions TEXT NOT NULL DEFAULT '',
          steps TEXT NOT NULL DEFAULT '',
          result TEXT NOT NULL DEFAULT '',
          conclusion TEXT NOT NULL DEFAULT '',
          content TEXT NOT NULL DEFAULT '',
          tags TEXT NOT NULL DEFAULT '[]',
          occurred_at TEXT NOT NULL,
          started_at TEXT, ended_at TEXT,
          sort_order INTEGER,
          sensitive_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, trashed_at TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS record_revisions (
          id TEXT PRIMARY KEY, record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
          snapshot_json TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS attachments (
          id TEXT PRIMARY KEY, record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
          filename TEXT NOT NULL, local_path TEXT NOT NULL, size_bytes INTEGER NOT NULL,
          sha256 TEXT NOT NULL, media_type TEXT NOT NULL, created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_records_project ON records(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_records_occurred ON records(occurred_at)",
        "CREATE INDEX IF NOT EXISTS idx_revisions_record ON record_revisions(record_id)",
        "CREATE INDEX IF NOT EXISTS idx_attachments_record ON attachments(record_id)",
        f"""CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
          {', '.join(FTS_COLUMNS)}, tokenize='trigram', content='records', content_rowid='rowid'
        )""",
        f"""CREATE TRIGGER IF NOT EXISTS records_fts_ai AFTER INSERT ON records BEGIN
          INSERT INTO records_fts(rowid, {', '.join(FTS_COLUMNS)})
          VALUES (new.rowid, {', '.join('new.' + c for c in FTS_COLUMNS)});
        END""",
        f"""CREATE TRIGGER IF NOT EXISTS records_fts_ad AFTER DELETE ON records BEGIN
          INSERT INTO records_fts(records_fts, rowid, {', '.join(FTS_COLUMNS)})
          VALUES ('delete', old.rowid, {', '.join('old.' + c for c in FTS_COLUMNS)});
        END""",
        f"""CREATE TRIGGER IF NOT EXISTS records_fts_au AFTER UPDATE ON records BEGIN
          INSERT INTO records_fts(records_fts, rowid, {', '.join(FTS_COLUMNS)})
          VALUES ('delete', old.rowid, {', '.join('old.' + c for c in FTS_COLUMNS)});
          INSERT INTO records_fts(rowid, {', '.join(FTS_COLUMNS)})
          VALUES (new.rowid, {', '.join('new.' + c for c in FTS_COLUMNS)});
        END""",
    ]
    with connect() as conn:
        for statement in statements:
            conn.execute(statement)
        # 缺列补列迁移模式：新增字段时在此追加，旧数据库自动补齐。
        record_columns = {row[1] for row in conn.execute("PRAGMA table_info(records)").fetchall()}
        if "started_at" not in record_columns:
            conn.execute("ALTER TABLE records ADD COLUMN started_at TEXT")
        if "ended_at" not in record_columns:
            conn.execute("ALTER TABLE records ADD COLUMN ended_at TEXT")
        # 手动拖拽排序：NULL 表示未排序（按发生时间倒序），有值则按该值升序。
        if "sort_order" not in record_columns:
            conn.execute("ALTER TABLE records ADD COLUMN sort_order INTEGER")

        def _repair_distorted_time(column: str) -> None:
            """修复 0.1.0 首版把 Qt 格式串误写成 'YYYY-MM-DD HH:mm' 造成的两种失真：
            形态 A "YYYY-MM-DD HH:mm"（日仍在字符串中，DD 为字面）；
            形态 B "YYYY-MM-DD HH:mm" 中日字段不可编辑而丢失，存为 "YYYY-MM-DD HH:mm"（仅月与时间）。
            形态 B 优先从标题中提取日期（用户常在标题写日期），否则用记录创建日期兜底。"""
            rows = conn.execute(
                f"SELECT id, {column} AS value, title, created_at FROM records WHERE {column} LIKE 'YYYY-%'"
            ).fetchall()
            for row in rows:
                value = str(row["value"] or "")
                created = str(row["created_at"] or "")
                fallback_date = (
                    created[:10]
                    if len(created) >= 10 and created[4] == "-"
                    else datetime.now(timezone.utc).strftime("%Y-%m-%d")
                )
                repaired = None
                if len(value) >= 19 and value[10:13] == " DD" and value[5:10].count("-") == 1:
                    repaired = f"{fallback_date[:4]}-{value[5:10]} {value[14:19]}"
                else:
                    time_part = value[11:16] if len(value) >= 16 and value[11:13].isdigit() else ""
                    match = re.search(r"(\d{4})[.．/年\-]\s*(\d{1,2})[.．/月\-]\s*(\d{1,2})", row["title"] or "")
                    if match:
                        repaired = f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
                        if time_part:
                            repaired += f" {time_part}"
                    else:
                        repaired = fallback_date + (f" {time_part}" if time_part else "")
                conn.execute(f"UPDATE records SET {column}=? WHERE id=?", (repaired, row["id"]))

        for _column in ("occurred_at", "started_at", "ended_at"):
            _repair_distorted_time(_column)

        if conn.execute("SELECT COUNT(*) FROM event_types").fetchone()[0] == 0:
            for sort, (name, color) in enumerate(BUILTIN_EVENT_TYPES):
                conn.execute(
                    "INSERT INTO event_types (id, name, color, sort, builtin) VALUES (?, ?, ?, ?, 1)",
                    (new_id(), name, color, sort),
                )


# ---------- 项目 ----------


def list_projects(include_trashed: bool = False) -> list[dict[str, Any]]:
    rows = fetch_all(
        "SELECT * FROM projects WHERE trashed_at IS NULL ORDER BY updated_at DESC"
        if not include_trashed
        else "SELECT * FROM projects WHERE trashed_at IS NOT NULL ORDER BY trashed_at DESC"
    )
    return rows


def create_project(name: str, description: str = "") -> dict[str, Any]:
    project = {
        "id": new_id(),
        "name": name.strip() or "未命名项目",
        "description": description,
        "status": "进行中",
        "created_at": now(),
        "updated_at": now(),
        "trashed_at": None,
    }
    execute(
        "INSERT INTO projects (id, name, description, status, created_at, updated_at, trashed_at) "
        "VALUES (:id, :name, :description, :status, :created_at, :updated_at, :trashed_at)",
        project,
    )
    return project


def update_project(project_id: str, name: str, description: str, status: str, expected_version: str | None = None) -> None:
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if expected_version is not None and (row is None or row_version(dict(row)) != expected_version):
            raise ConcurrentEditError("项目已被其他窗口修改，请重新载入后比较。")
        conn.execute("UPDATE projects SET name=?, description=?, status=?, updated_at=? WHERE id=?",
                     (name.strip() or "未命名项目", description, status, now(), project_id))


def trash_project(project_id: str) -> None:
    execute("UPDATE projects SET trashed_at=?, updated_at=? WHERE id=?", (now(), now(), project_id))


def restore_project(project_id: str) -> None:
    execute("UPDATE projects SET trashed_at=NULL, updated_at=? WHERE id=?", (now(), project_id))


def purge_project(project_id: str) -> None:
    execute("DELETE FROM projects WHERE id=?", (project_id,))


# ---------- 事件类型 ----------


def list_event_types() -> list[dict[str, Any]]:
    return fetch_all("SELECT * FROM event_types ORDER BY sort, name")


def create_event_type(name: str, color: str) -> dict[str, Any]:
    row = fetch_one("SELECT MAX(sort) AS m FROM event_types")
    event_type = {"id": new_id(), "name": name.strip(), "color": color, "sort": (row["m"] or 0) + 1, "builtin": 0}
    execute(
        "INSERT INTO event_types (id, name, color, sort, builtin) VALUES (:id, :name, :color, :sort, :builtin)",
        event_type,
    )
    return event_type


def rename_event_type(type_id: str, name: str) -> None:
    execute("UPDATE event_types SET name=? WHERE id=? AND builtin=0", (name.strip(), type_id))


def delete_event_type(type_id: str) -> bool:
    row = fetch_one("SELECT builtin FROM event_types WHERE id=?", (type_id,))
    if not row or row["builtin"]:
        return False
    try:
        execute("DELETE FROM event_types WHERE id=?", (type_id,))
        return True
    except sqlite3.IntegrityError:
        return False


# ---------- 记录 ----------

RECORD_TEXT_FIELDS = ("title", "purpose", "conditions", "steps", "result", "conclusion", "content")


def _record_row(record_id: str) -> dict[str, Any] | None:
    return fetch_one(
        "SELECT r.*, e.name AS event_type_name, e.color AS event_type_color, p.name AS project_name "
        "FROM records r JOIN event_types e ON e.id=r.event_type_id "
        "JOIN projects p ON p.id=r.project_id WHERE r.id=?",
        (record_id,),
    )


def get_record(record_id: str) -> dict[str, Any] | None:
    row = _record_row(record_id)
    if row:
        row["tags"] = json.loads(row["tags"])
        row["sensitive"] = json.loads(row["sensitive_json"])
    return row


def list_records(
    project_id: str | None = None,
    event_type_id: str | None = None,
    query: str | None = None,
    include_trashed: bool = False,
    full: bool = False,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if not include_trashed:
        clauses.append("r.trashed_at IS NULL")
    else:
        clauses.append("r.trashed_at IS NOT NULL")
    if project_id:
        clauses.append("r.project_id=?")
        params.append(project_id)
    if event_type_id:
        clauses.append("r.event_type_id=?")
        params.append(event_type_id)
    if query:
        cleaned = query.strip()
        if len(cleaned) >= 3:
            # trigram 分词支持中文子串；短语查询需整体加引号。
            fts_clause = (
                "rowid IN (SELECT rowid FROM records_fts WHERE records_fts MATCH ?)"
            )
            like_clause = (
                "rowid IN (SELECT id FROM records WHERE "
                "title LIKE ? OR purpose LIKE ? OR conditions LIKE ? OR steps LIKE ? "
                "OR result LIKE ? OR conclusion LIKE ? OR content LIKE ?)"
            )
            sub = fetch_all(
                f"SELECT id FROM records WHERE {fts_clause}",
                ('"' + cleaned.replace('"', '""') + '"',),
            )
            rows = sub if sub else fetch_all(
                f"SELECT id FROM records WHERE {like_clause}",
                tuple(f"%{cleaned}%" for _ in range(7)),
            )
            ids = [row["id"] for row in rows] or ["-"]
            clauses.append(f"r.id IN ({','.join('?' for _ in ids)})")
            params.extend(ids)
        else:
            clauses.append(
                "(r.title LIKE ? OR r.content LIKE ? OR r.tags LIKE ?)"
            )
            like = f"%{cleaned}%"
            params.extend([like, like, like])
    # 精简列用于列表展示；导出等场景需要 full=True 取回全部正文字段。
    columns = (
        "r.*, e.name AS event_type_name, e.color AS event_type_color, p.name AS project_name"
        if full
        else (
            "r.id, r.project_id, r.event_type_id, r.title, r.occurred_at, r.tags, r.updated_at, "
            "r.sensitive_json, e.name AS event_type_name, e.color AS event_type_color, p.name AS project_name"
        )
    )
    sql = (
        f"SELECT {columns} FROM records r JOIN event_types e ON e.id=r.event_type_id "
        "JOIN projects p ON p.id=r.project_id "
        + ("WHERE " + " AND ".join(clauses) if clauses else "")
        + " ORDER BY (r.sort_order IS NULL), r.sort_order, r.occurred_at DESC, r.created_at DESC"
    )
    return decode_json_rows(fetch_all(sql, tuple(params)), ("tags", "sensitive_json"))


def set_record_order(record_ids: list[str]) -> None:
    """按传入顺序写入 sort_order（记录列表拖拽排序用）。"""
    with connect() as conn:
        for index, record_id in enumerate(record_ids):
            conn.execute("UPDATE records SET sort_order=? WHERE id=?", (index, record_id))


def reorder_visible_records(visible_ids: list[str]) -> bool:
    """重排筛选结果，并保持其他记录的相对位置；无变化时不写库。"""
    if not visible_ids or len(visible_ids) != len(set(visible_ids)):
        raise ValueError("Visible record IDs must be nonempty and unique")
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, sort_order FROM records WHERE trashed_at IS NULL "
            "ORDER BY (sort_order IS NULL), sort_order, occurred_at DESC, created_at DESC"
        ).fetchall()
        current_ids = [row["id"] for row in rows]
        visible_set = set(visible_ids)
        if not visible_set.issubset(current_ids):
            raise ValueError("Visible records no longer match the database")
        replacement = iter(visible_ids)
        merged_ids = [next(replacement) if record_id in visible_set else record_id for record_id in current_ids]
        if merged_ids == current_ids:
            return False
        previous_sort = {row["id"]: row["sort_order"] for row in rows}
        updates = [
            (index, record_id)
            for index, record_id in enumerate(merged_ids)
            if previous_sort[record_id] != index
        ]
        conn.executemany("UPDATE records SET sort_order=? WHERE id=?", updates)
    return True


def clear_record_order() -> None:
    """清除手动排序，恢复按发生时间倒序。"""
    with connect() as conn:
        conn.execute("UPDATE records SET sort_order=NULL")


def save_record(payload: dict[str, Any], record_id: str | None = None, reason: str = "编辑保存", expected_version: str | None = None, version_receipt: dict | None = None) -> str:
    """创建或更新记录；更新时自动保存修改前快照到修订历史。"""
    from .security import scan_sensitive

    combined = "\n".join(str(payload.get(field, "")) for field in RECORD_TEXT_FIELDS)
    sensitive = scan_sensitive(combined)
    tags = payload.get("tags") or []
    tags_json = json.dumps(list(tags), ensure_ascii=False)
    record_id = record_id or new_id()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
        if expected_version is not None and (current is None or row_version(dict(current)) != expected_version):
            raise ConcurrentEditError("记录已被其他窗口修改，请保留草稿并重新载入后比较。")
        if not conn.execute("SELECT 1 FROM projects WHERE id=? AND trashed_at IS NULL", (payload["project_id"],)).fetchone():
            raise ValueError("所属项目不可用，请重新选择项目。")
        if record_id and current is not None:
            old = conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
            conn.execute(
                "INSERT INTO record_revisions (id, record_id, snapshot_json, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    new_id(),
                    record_id,
                    json.dumps({k: old[k] for k in old.keys()}, ensure_ascii=False),
                    reason,
                    now(),
                ),
            )
            conn.execute(
                "UPDATE records SET project_id=?, event_type_id=?, title=?, purpose=?, conditions=?, "
                "steps=?, result=?, conclusion=?, content=?, tags=?, occurred_at=?, started_at=?, "
                "ended_at=?, sensitive_json=?, updated_at=? WHERE id=?",
                (
                    payload["project_id"], payload["event_type_id"], payload["title"],
                    payload.get("purpose", ""), payload.get("conditions", ""), payload.get("steps", ""),
                    payload.get("result", ""), payload.get("conclusion", ""), payload.get("content", ""),
                    tags_json, payload["occurred_at"], payload.get("started_at"), payload.get("ended_at"),
                    json.dumps(sensitive, ensure_ascii=False), now(), record_id,
                ),
            )
        else:
            conn.execute(
                "INSERT INTO records (id, project_id, event_type_id, title, purpose, conditions, steps, "
                "result, conclusion, content, tags, occurred_at, started_at, ended_at, sensitive_json, "
                "created_at, updated_at, trashed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                (
                    record_id, payload["project_id"], payload["event_type_id"], payload["title"],
                    payload.get("purpose", ""), payload.get("conditions", ""), payload.get("steps", ""),
                    payload.get("result", ""), payload.get("conclusion", ""), payload.get("content", ""),
                    tags_json, payload["occurred_at"], payload.get("started_at"), payload.get("ended_at"),
                    json.dumps(sensitive, ensure_ascii=False), now(), now(),
                ),
            )
        if version_receipt is not None:
            saved_row = conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
            version_receipt["version"] = row_version(dict(saved_row))
    return record_id


def trash_record(record_id: str) -> None:
    execute("UPDATE records SET trashed_at=?, updated_at=? WHERE id=?", (now(), now(), record_id))


def restore_record(record_id: str) -> None:
    execute("UPDATE records SET trashed_at=NULL, updated_at=? WHERE id=?", (now(), record_id))


def purge_record(record_id: str) -> None:
    execute("DELETE FROM records WHERE id=?", (record_id,))


def list_revisions(record_id: str) -> list[dict[str, Any]]:
    rows = fetch_all(
        "SELECT id, record_id, reason, created_at, snapshot_json FROM record_revisions "
        "WHERE record_id=? ORDER BY created_at DESC",
        (record_id,),
    )
    return decode_json_rows(rows, ("snapshot_json",))


def restore_revision(revision_id: str, expected_version: str | None = None) -> str | None:
    """把记录恢复到指定修订快照；恢复前先保存当前内容为新修订。"""
    row = fetch_one("SELECT * FROM record_revisions WHERE id=?", (revision_id,))
    if not row:
        return None
    snapshot = json.loads(row["snapshot_json"])
    return save_record(
        {
            "project_id": snapshot["project_id"],
            "event_type_id": snapshot["event_type_id"],
            "title": snapshot["title"],
            "purpose": snapshot["purpose"],
            "conditions": snapshot["conditions"],
            "steps": snapshot["steps"],
            "result": snapshot["result"],
            "conclusion": snapshot["conclusion"],
            "content": snapshot["content"],
            "tags": json.loads(snapshot["tags"]),
            "occurred_at": snapshot["occurred_at"],
            "started_at": snapshot["started_at"],
            "ended_at": snapshot["ended_at"],
        },
        record_id=row["record_id"],
        reason="恢复历史修订",
        expected_version=expected_version,
    )


# ---------- 附件 ----------


def attachment_dir(record_id: str) -> Path:
    path = ATTACHMENT_ROOT / record_id
    path.mkdir(parents=True, exist_ok=True)
    return path


# 提权目的：add_attachment 增加可选显示名参数，粘贴截图入库时可命名为"粘贴图片_时间.png"而非临时文件名。
def add_attachment(record_id: str, source: Path, display_name: str | None = None) -> dict[str, Any]:
    suffix = source.suffix.lower()
    if suffix not in ATTACHMENT_SUFFIXES:
        raise ValueError(f"不支持的附件类型：{suffix}")
    media_type = validate_file_signature(source, suffix)
    target_dir = attachment_dir(record_id)
    filename = safe_filename(display_name if display_name else source.name)
    target = target_dir / f"{new_id()[:8]}_{filename}"
    shutil.copy2(source, target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    attachment = {
        "id": new_id(),
        "record_id": record_id,
        "filename": filename,
        "local_path": str(target),
        "size_bytes": target.stat().st_size,
        "sha256": digest,
        "media_type": media_type,
        "created_at": now(),
    }
    execute(
        "INSERT INTO attachments (id, record_id, filename, local_path, size_bytes, sha256, media_type, created_at) "
        "VALUES (:id, :record_id, :filename, :local_path, :size_bytes, :sha256, :media_type, :created_at)",
        attachment,
    )
    return attachment


def list_attachments(record_id: str) -> list[dict[str, Any]]:
    return fetch_all("SELECT * FROM attachments WHERE record_id=? ORDER BY created_at", (record_id,))


# ---------- 统计 ----------


def event_type_counts(project_id: str | None = None) -> list[dict[str, Any]]:
    where = "WHERE r.trashed_at IS NULL" + (" AND r.project_id=?" if project_id else "")
    params = (project_id,) if project_id else ()
    return fetch_all(
        "SELECT e.name AS label, e.color AS color, COUNT(r.id) AS total "
        f"FROM event_types e LEFT JOIN records r ON r.event_type_id=e.id {where} "
        "GROUP BY e.id ORDER BY e.sort",
        params,
    )


def monthly_counts(project_id: str | None = None) -> list[dict[str, Any]]:
    where = "WHERE r.trashed_at IS NULL" + (" AND r.project_id=?" if project_id else "")
    params = (project_id,) if project_id else ()
    return fetch_all(
        "SELECT substr(r.occurred_at, 1, 7) AS label, COUNT(*) AS total "
        f"FROM records r {where} GROUP BY label ORDER BY label",
        params,
    )


def overall_counts() -> dict[str, int]:
    return {
        "projects": fetch_one("SELECT COUNT(*) AS c FROM projects WHERE trashed_at IS NULL")["c"],
        "records": fetch_one("SELECT COUNT(*) AS c FROM records WHERE trashed_at IS NULL")["c"],
        "attachments": fetch_one("SELECT COUNT(*) AS c FROM attachments")["c"],
    }
