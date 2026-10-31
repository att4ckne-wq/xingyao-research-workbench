from __future__ import annotations

import multiprocessing
import os
import signal
import time
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait

from . import db
from .main import run_persisted_job


running = True
MAX_HEAVY_WORKERS = max(1, min(2, int(os.environ.get("NATURE_HEAVY_WORKERS", "2"))))


def stop_worker(*_: object) -> None:
    global running
    running = False


def claim_next_job() -> dict | None:
    job = db.fetch_one(
        "SELECT * FROM jobs WHERE state='queued' ORDER BY created_at LIMIT 1"
    )
    if not job:
        return None
    db.execute(
        "UPDATE jobs SET state='running', stage='Worker 已接收', updated_at=? "
        "WHERE id=? AND state='queued'",
        (db.now(), job["id"]),
    )
    claimed = db.fetch_one("SELECT * FROM jobs WHERE id=?", (job["id"],))
    return claimed if claimed and claimed["state"] == "running" else None


def recover_interrupted_jobs() -> int:
    interrupted = db.fetch_all("SELECT id FROM jobs WHERE state='running'")
    if interrupted:
        db.execute(
            "UPDATE jobs SET state='queued', stage='应用重启后恢复', updated_at=? "
            "WHERE state='running'",
            (db.now(),),
        )
    return len(interrupted)


def main() -> None:
    signal.signal(signal.SIGINT, stop_worker)
    signal.signal(signal.SIGTERM, stop_worker)
    db.init_db()
    recover_interrupted_jobs()
    spawn_context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=MAX_HEAVY_WORKERS,
        mp_context=spawn_context,
    ) as process_pool:
        active: dict[Future[None], str] = {}
        while running:
            while len(active) < MAX_HEAVY_WORKERS:
                job = claim_next_job()
                if not job:
                    break
                active[process_pool.submit(run_persisted_job, job)] = job["id"]

            if not active:
                time.sleep(0.6)
                continue

            completed, _ = wait(
                set(active),
                timeout=0.6,
                return_when=FIRST_COMPLETED,
            )
            for future in completed:
                job_id = active.pop(future)
                try:
                    future.result()
                except Exception as exc:
                    db.execute(
                        "UPDATE jobs SET state='failed', stage='工作进程异常退出', "
                        "progress=100, message=?, updated_at=? WHERE id=?",
                        (type(exc).__name__, db.now(), job_id),
                    )


if __name__ == "__main__":
    main()
