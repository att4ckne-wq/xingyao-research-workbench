from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
APP_HOME = Path(os.environ.get("NATURE_WORKBENCH_HOME", Path(os.environ.get("LOCALAPPDATA", ROOT / "data")) / "NatureWorkbench"))
PROJECT_ROOT_CONFIG = APP_HOME / "projects-root.txt"


def configured_projects_root() -> Path:
    environment = os.environ.get("NATURE_PROJECTS_ROOT")
    if environment: return Path(environment)
    try:
        configured = PROJECT_ROOT_CONFIG.read_text(encoding="utf-8").strip()
        if configured: return Path(configured)
    except OSError:
        pass
    return ROOT / "projects"


PROJECTS_ROOT = configured_projects_root()
DB_PATH = APP_HOME / "workbench.db"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    APP_HOME.mkdir(parents=True, exist_ok=True)
    PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    statements = [
        """CREATE TABLE IF NOT EXISTS projects (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, quality_mode TEXT NOT NULL,
          status TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, trashed_at TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS documents (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          filename TEXT NOT NULL, media_type TEXT NOT NULL, size_bytes INTEGER NOT NULL,
          sha256 TEXT NOT NULL, parser TEXT NOT NULL, language TEXT NOT NULL,
          local_path TEXT NOT NULL, created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS anchors (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
          section TEXT NOT NULL, page INTEGER, paragraph_index INTEGER NOT NULL,
          text TEXT NOT NULL, text_hash TEXT NOT NULL, bbox_json TEXT,
          ocr_confidence REAL, ocr_rotation INTEGER, ocr_confirmed INTEGER NOT NULL DEFAULT 0,
          sensitivity_json TEXT NOT NULL DEFAULT '[]'
        )""",
        """CREATE TABLE IF NOT EXISTS assets (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          anchor_id TEXT NOT NULL REFERENCES anchors(id) ON DELETE CASCADE,
          title TEXT NOT NULL, provider TEXT NOT NULL, source_url TEXT NOT NULL,
          preview_url TEXT, author TEXT, license_json TEXT NOT NULL, accessed_at TEXT NOT NULL,
          score REAL NOT NULL, reuse_status TEXT NOT NULL, checksum TEXT, rationale TEXT NOT NULL,
          local_path TEXT, UNIQUE(anchor_id, source_url)
        )""",
        """CREATE TABLE IF NOT EXISTS datasets (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          filename TEXT NOT NULL, local_path TEXT NOT NULL, profile_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS charts (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
          spec_json TEXT NOT NULL, outputs_json TEXT NOT NULL, warnings_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS compositions (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          spec_json TEXT NOT NULL, outputs_json TEXT NOT NULL DEFAULT '{}',
          warnings_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS assistant_messages (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          anchor_id TEXT REFERENCES anchors(id) ON DELETE SET NULL,
          role TEXT NOT NULL, content TEXT NOT NULL, suggestions_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS anchor_revisions (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          anchor_id TEXT NOT NULL REFERENCES anchors(id) ON DELETE CASCADE,
          old_text TEXT NOT NULL, new_text TEXT NOT NULL, reason TEXT NOT NULL,
          created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS document_revisions (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          old_content_json TEXT NOT NULL, new_content_json TEXT NOT NULL,
          reason TEXT NOT NULL, action TEXT NOT NULL, created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          kind TEXT NOT NULL, state TEXT NOT NULL, stage TEXT NOT NULL, progress INTEGER NOT NULL,
          message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          payload_json TEXT NOT NULL DEFAULT '{}', result_json TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS decisions (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          target_type TEXT NOT NULL, target_id TEXT NOT NULL, action TEXT NOT NULL,
          reason TEXT, warnings_json TEXT NOT NULL, created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS checkpoints (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          label TEXT NOT NULL, manifest_path TEXT NOT NULL, created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS exports (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          archive_path TEXT NOT NULL, formats_json TEXT NOT NULL, warnings_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS model_profiles (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
          base_url TEXT NOT NULL, model TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
          password_salt TEXT NOT NULL, password_hash TEXT NOT NULL,
          preferences_json TEXT NOT NULL DEFAULT '{}', must_change_password INTEGER NOT NULL DEFAULT 1,
          failed_attempts INTEGER NOT NULL DEFAULT 0, locked_until TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS account_sessions (
          token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          expires_at TEXT NOT NULL, created_at TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_anchors_project ON anchors(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_assets_anchor ON assets(anchor_id)",
        "CREATE INDEX IF NOT EXISTS idx_anchor_revisions_anchor ON anchor_revisions(anchor_id)",
        "CREATE INDEX IF NOT EXISTS idx_document_revisions_project ON document_revisions(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_model_profiles_single_enabled ON model_profiles(enabled) WHERE enabled=1",
        "CREATE INDEX IF NOT EXISTS idx_account_sessions_user ON account_sessions(user_id)",
    ]
    statements.append("""CREATE TABLE IF NOT EXISTS project_literature (
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      anchor_id TEXT, title TEXT NOT NULL, provider TEXT NOT NULL, source_url TEXT NOT NULL,
      preview_url TEXT, author TEXT, license_json TEXT NOT NULL, accessed_at TEXT NOT NULL,
      score REAL NOT NULL, reuse_status TEXT NOT NULL CHECK(reuse_status='citation_only'),
      checksum TEXT, rationale TEXT NOT NULL, local_path TEXT,
      UNIQUE(project_id,source_url), CHECK(anchor_id IS NULL), CHECK(local_path IS NULL))""")
    statements.extend([
        "CREATE TABLE IF NOT EXISTS project_manuscripts (project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE)",
        "CREATE TABLE IF NOT EXISTS document_roles (document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, role TEXT NOT NULL CHECK(role IN ('manuscript','supplement')), status TEXT NOT NULL DEFAULT 'pending', error TEXT)",
        "CREATE TABLE IF NOT EXISTS citation_placements (project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, anchor_id TEXT NOT NULL REFERENCES anchors(id) ON DELETE CASCADE, reference_id TEXT NOT NULL, action TEXT NOT NULL CHECK(action IN ('accept','reject')), accepted_at TEXT NOT NULL, PRIMARY KEY(project_id,anchor_id,reference_id))"
    ])
    with connect() as conn:
        for statement in statements:
            conn.execute(statement)
        composition_columns = {row[1] for row in conn.execute("PRAGMA table_info(compositions)").fetchall()}
        if "outputs_json" not in composition_columns:
            conn.execute("ALTER TABLE compositions ADD COLUMN outputs_json TEXT NOT NULL DEFAULT '{}'")
        if "warnings_json" not in composition_columns:
            conn.execute("ALTER TABLE compositions ADD COLUMN warnings_json TEXT NOT NULL DEFAULT '[]'")
        anchor_columns = {row[1] for row in conn.execute("PRAGMA table_info(anchors)").fetchall()}
        if "ocr_rotation" not in anchor_columns:
            conn.execute("ALTER TABLE anchors ADD COLUMN ocr_rotation INTEGER")
        if "ocr_confirmed" not in anchor_columns:
            conn.execute("ALTER TABLE anchors ADD COLUMN ocr_confirmed INTEGER NOT NULL DEFAULT 0")
        job_columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if "payload_json" not in job_columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'")
        if "result_json" not in job_columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN result_json TEXT")
        # Older data-only projects retained the creation status because project
        # progress originally advanced only through document analysis.
        conn.execute(
            "UPDATE projects SET status='已完成数据制图', "
            "progress=MAX(progress,86), updated_at=? "
            "WHERE status='等待上传' AND EXISTS "
            "(SELECT 1 FROM charts WHERE charts.project_id=projects.id)",
            (now(),),
        )
        conn.execute(
            "UPDATE projects SET status='数据已分析，可继续制图', "
            "progress=MAX(progress,72), updated_at=? "
            "WHERE status='等待上传' AND EXISTS "
            "(SELECT 1 FROM datasets WHERE datasets.project_id=projects.id)",
            (now(),),
        )


def fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


REFERENCE_SOURCE = '(SELECT * FROM assets UNION ALL SELECT * FROM project_literature) AS assets'


def reference_table(reference_id: str) -> str:
    """Only fixed internal table names can reach update statements."""
    return 'project_literature' if fetch_one('SELECT id FROM project_literature WHERE id=?', (reference_id,)) else 'assets'


def fetch_one(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(query, params).fetchone()
        return dict(row) if row else None


def execute(query: str, params: tuple[Any, ...] = ()) -> None:
    with connect() as conn:
        conn.execute(query, params)


def execute_transaction(statements: list[tuple[str, tuple[Any, ...]]]) -> None:
    with connect() as conn:
        for query, params in statements:
            conn.execute(query, params)


def project_dir(project_id: str) -> Path:
    path = PROJECTS_ROOT / project_id
    for name in ("source", "datasets", "assets", "charts", "exports", "checkpoints"):
        (path / name).mkdir(parents=True, exist_ok=True)
    return path


def decode_json_rows(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    for row in rows:
        for field in fields:
            if row.get(field):
                row[field.removesuffix("_json")] = json.loads(row[field])
    return rows
