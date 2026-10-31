"""实验记录 - 数据库备份。

设计要点（对应 2026-10-26 数据丢失事故）：

* 备份写到**独立物理盘**（默认 `D:\\实验记录备份`），与数据盘分离；
* 每份备份是一个独立目录 `\\<YYYYMMDD-HHMMSS>\\`，内部文件名与原库一致
  （`experiment.db`），并附 `.sha256` 与 `manifest.json`，便于人工核对、直接复制回数据目录；
* 使用 SQLite 在线备份 API，备份包含 WAL 中已提交的内容，不需要停应用；
* **默认不删除任何旧备份**；只有显式设置 `PAPER_BACKUP_KEEP` 为正整数时，
  才在每次备份后按数量轮转（仍会保留最新的 3 份）。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .. import db, paths

DB_NAME = "experiment.db"
MANIFEST_NAME = "manifest.json"
AUDIT_NAME = "audit.log"


def root() -> Path:
    return paths.backup_root()


def auto_enabled() -> bool:
    """自动备份开关；测试或便携场景可用 PAPER_DISABLE_AUTO_COPY=1 关闭。"""
    return os.environ.get("PAPER_DISABLE_AUTO_COPY", "") not in {"1", "true", "True"}


def _stamp() -> str:
    now = datetime.now()
    return now.strftime("%Y%m%d-%H%M%S") + f"-{now.microsecond // 1000:03d}"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit(event: str, **fields: Any) -> None:
    """追加写入备份审计日志（JSON Lines，不覆盖历史）。"""
    try:
        root().mkdir(parents=True, exist_ok=True)
        record = {"at": datetime.now().isoformat(timespec="seconds"), "event": event, **fields}
        with (root() / AUDIT_NAME).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _new_dir() -> Path:
    base = root()
    base.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    target = base / stamp
    index = 2
    while target.exists():
        target = base / f"{stamp}-{index}"
        index += 1
    target.mkdir(parents=True)
    return target


def _quick_check(path: Path) -> bool:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False
    return bool(row) and str(row[0]).lower() == "ok"


def create_backup(note: str = "") -> dict[str, Any]:
    """创建一份备份并校验；返回 manifest 内容。"""
    source = db.db_path()
    if not source.exists():
        raise FileNotFoundError(f"数据库不存在：{source}")
    target_dir = _new_dir()
    target = target_dir / DB_NAME
    src = sqlite3.connect(str(source), timeout=15)
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
            # 备份会把源库头部的 WAL 标记一并复制过来，这里切回单文件模式并清空旁文件。
            dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            dst.execute("PRAGMA journal_mode = DELETE")
        finally:
            dst.close()
    finally:
        src.close()
    digest = sha256_of(target)
    (target_dir / (DB_NAME + ".sha256")).write_text(f"{digest}  {DB_NAME}\n", encoding="utf-8")
    valid = _quick_check(target)
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source": str(source),
        "note": note,
        "file": str(target),
        "size_bytes": target.stat().st_size,
        "sha256": digest,
        "quick_check": "ok" if valid else "failed",
    }
    (target_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    audit("backup", **{k: manifest[k] for k in ("created_at", "source", "file", "size_bytes", "sha256")}, valid=valid, note=note)
    if not valid:
        raise RuntimeError(f"备份校验失败：{target}")
    try:
        maybe_prune()
    except Exception:  # noqa: BLE001 - 轮转失败不影响本次备份
        pass
    return manifest


def list_backups() -> list[dict[str, Any]]:
    """按时间倒序列出可用备份（读取 manifest，跳过损坏条目）。"""
    base = root()
    if not base.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for entry in base.iterdir():
        manifest = entry / MANIFEST_NAME
        if not entry.is_dir() or not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (entry / DB_NAME).is_file():
            items.append(data)
    items.sort(
        key=lambda item: (
            Path(item.get("file", "")).parent.name,
            item.get("created_at", ""),
        ),
        reverse=True,
    )
    return items


def latest_valid() -> Path | None:
    """返回最近一份通过哈希与 quick_check 双重校验的备份文件。"""
    for item in list_backups():
        path = Path(item.get("file", ""))
        if not path.is_file():
            continue
        if item.get("sha256") and sha256_of(path) != item["sha256"]:
            audit("verify_failed", file=str(path), reason="sha256")
            continue
        if not _quick_check(path):
            audit("verify_failed", file=str(path), reason="quick_check")
            continue
        return path
    return None


def recover_to_new_db() -> Path | None:
    """把最近一份有效备份复制成新的数据库文件（不覆盖、不删除原文件）。"""
    source = latest_valid()
    if source is None:
        return None
    target = db.db_path().parent / f"experiment-recovered-{_stamp()}{source.suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
    if sha256_of(target) != sha256_of(source) or not _quick_check(target):
        return None
    audit("recover", source=str(source), target=str(target))
    return target


def create_backup_if_due(min_minutes: int = 30, note: str = "") -> dict[str, Any] | None:
    """距上次备份超过 ``min_minutes`` 分钟才备份，避免每次启动都堆副本。"""
    backups = list_backups()
    if backups:
        try:
            last = datetime.fromisoformat(backups[0]["created_at"])
        except (KeyError, ValueError):
            last = None
        if last and datetime.now() - last < timedelta(minutes=min_minutes):
            return None
    return create_backup(note=note)


def keep_limit() -> int:
    """保留份数：环境变量 > 数据库设置 > 默认 30。

    返回 0 表示不轮转（全部保留）。轮转只会在**新建备份之后**删除最旧的几份，
    并且始终保留最新 3 份；每次删除都写入 audit.log。
    """
    raw = os.environ.get("PAPER_BACKUP_KEEP")
    if raw is not None:
        try:
            return int(raw)
        except ValueError:
            return 0
    try:
        stored = db.get_setting("backup_keep")
    except Exception:  # noqa: BLE001 - 数据库不可用时退回"不删除"
        return 0
    if stored:
        try:
            return int(stored)
        except ValueError:
            return 0
    return 30


def maybe_prune() -> int:
    """按 keep_limit() 轮转旧备份；返回删除数量。"""
    keep = keep_limit()
    if keep <= 0:
        return 0
    keep = max(keep, 3)
    backups = list_backups()
    removed = 0
    for item in backups[keep:]:
        path = Path(item.get("file", ""))
        directory = path.parent
        if not directory.is_dir() or directory.parent != root():
            continue
        try:
            for child in directory.iterdir():
                child.unlink()
            directory.rmdir()
            removed += 1
        except OSError:
            continue
    if removed:
        audit("prune", removed=removed, keep=keep)
    return removed
