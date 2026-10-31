"""实验记录 - 运行路径解析。

数据与备份都必须离开构建产物目录，避免重建/覆盖安装 exe 时被连带清除：

* 数据：项目根目录下的 `data\\`（可用环境变量 `PAPER_DATA_ROOT` 覆盖）
* 备份：`D:\\实验记录备份`（可用 `PAPER_BACKUP_ROOT` 覆盖；D: 不可用时回退到本地 AppData）
* 快照：备份根下的 `snapshots\\<日期>\\<时间>\\`

数据根固定在项目代码目录（而不是 `%LOCALAPPDATA%`），原因是：某些启动方式
（受沙箱限制的进程、被安全软件降权的进程）无法在 AppData 建库，会导致应用
悄悄改用别的目录、出现"两份数据"。固定路径可以让"源码启动"和"外壳 exe 启动"
看到同一份数据。
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

APP_DIR_NAME = "实验记录"
BACKUP_DIR_NAME = "实验记录备份"
FALLBACK_DIR_NAME = "PaperApp"
DATA_DIR_NAME = "data"


def _local_appdata() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base)
    return Path.home() / "AppData" / "Local"


def code_root() -> Path:
    """业务代码所在的项目根目录（源码模式与外壳模式都指向同一个目录）。"""
    here = Path(__file__).resolve()
    best: Path | None = None
    for parent in here.parents:
        if (parent / "paper_app").is_dir():
            best = parent
    if best is not None:
        return best
    return here.parents[1]


def _program_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return code_root()


def data_root() -> Path:
    """数据根目录（不做可用性检查）。"""
    override = os.environ.get("PAPER_DATA_ROOT")
    if override:
        return Path(override).expanduser()
    return code_root() / DATA_DIR_NAME


def data_root_candidates() -> list[Path]:
    """按优先级返回候选数据目录（固定根优先，最后才是历史 AppData 位置）。"""
    candidates: list[Path] = []
    override = os.environ.get("PAPER_DATA_ROOT")
    if override:
        candidates.append(Path(override).expanduser())
    candidates.append(code_root() / DATA_DIR_NAME)
    unique: list[Path] = []
    for item in candidates:
        if item not in unique:
            unique.append(item)
    return unique


def default_backup_root() -> Path:
    """默认备份根：优先独立物理盘 D:，不可用时回退。"""
    override = os.environ.get("PAPER_BACKUP_ROOT")
    if override:
        return Path(override).expanduser()
    if Path("D:/").is_dir():
        return Path("D:/") / BACKUP_DIR_NAME
    return _local_appdata() / APP_DIR_NAME / "backups"


def backup_root() -> Path:
    return default_backup_root()


def exports_root() -> Path:
    """导出默认目录，与数据同级，不在程序目录内。"""
    return data_root().parent / "exports"


def _usable(directory: Path) -> tuple[bool, str]:
    """实际建库试一次：能创建、能切 WAL、能写入即视为可用。"""
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"mkdir 失败：{exc}"
    probe = directory / ".write-probe.db"
    try:
        conn = sqlite3.connect(probe)
        try:
            row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        finally:
            conn.close()
        mode = (row[0] if row else "") or ""
        if mode.lower() != "wal":
            return False, f"journal_mode 返回 {mode!r}"
        return True, "ok"
    except sqlite3.Error as exc:
        return False, f"sqlite: {exc}"
    except OSError as exc:
        return False, f"IO: {exc}"
    finally:
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(str(probe) + suffix).unlink(missing_ok=True)
            except OSError:
                pass


def ensure_usable_data_root() -> tuple[Path, list[str]]:
    """返回第一个真正可用的数据目录，以及全部尝试记录。"""
    attempts: list[str] = []
    for candidate in data_root_candidates():
        ok, detail = _usable(candidate)
        attempts.append(f"{candidate} -> {'可用' if ok else '不可用（' + detail + '）'}")
        if ok:
            return candidate, attempts
    return data_root(), attempts


def write_startup_log(text: str) -> Path | None:
    """把启动诊断写入最容易找到的位置（%TEMP% 优先，其次数据目录与程序目录）。"""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = f"[{stamp}]\n{text}\n"
    targets = []
    temp = os.environ.get("TEMP") or os.environ.get("TMP")
    if temp:
        targets.append(Path(temp) / "paper-startup.log")
    targets.append(_local_appdata() / APP_DIR_NAME / "paper-startup.log")
    targets.append(_program_dir() / "paper-startup.log")
    for target in targets:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as fh:
                fh.write(payload)
            return target
        except OSError:
            continue
    return None
