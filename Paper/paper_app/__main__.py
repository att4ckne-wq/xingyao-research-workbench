"""实验记录 - 程序入口：`python -m paper_app`、`pythonw -m paper_app` 或双击 exe。

启动顺序：先挑一个真正可写的数据目录（AppData 优先，失败自动退让），再开库。
无控制台模式（pythonw/exe）下任何启动异常都会写日志（`%TEMP%\\paper-startup.log` 等）
并弹窗提示，避免"双击没反应、也查不到原因"。
"""

from __future__ import annotations

import os
import traceback
from datetime import datetime

from . import db, paths

_ATTEMPTS: list[str] = []


def _report_fatal(message: str) -> None:
    detail = "\n".join(
        [
            "实验记录启动失败",
            f"时间: {datetime.now().isoformat(timespec='seconds')}",
            f"数据库: {db.db_path()}",
            f"LOCALAPPDATA: {os.environ.get('LOCALAPPDATA')}",
            f"TEMP: {os.environ.get('TEMP')}",
            "",
            "数据目录尝试记录:",
            *(_ATTEMPTS or ["(未执行)"]),
            "",
            message,
        ]
    )
    log_path = paths.write_startup_log(detail)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication([])
        text = detail if len(detail) <= 1800 else detail[-1800:]
        box = QMessageBox(QMessageBox.Critical, "实验记录启动失败", f"{text}\n\n日志: {log_path}")
        box.exec()
    except Exception:
        pass


def main() -> None:
    global _ATTEMPTS
    try:
        root, attempts = paths.ensure_usable_data_root()
        _ATTEMPTS = attempts
        db.configure(root)
        paths.write_startup_log(
            "启动检查\n" + "\n".join(attempts) + f"\n选用数据目录: {root}"
        )
        db.init_db()
        from .ui.main_window import run

        run()
    except SystemExit:
        raise
    except Exception:
        _report_fatal(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
