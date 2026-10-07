"""Persistence, cooperative cancellation, and resume smoke checks."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paperrevamper.jobs import JobManager  # noqa: E402


def wait_for(manager: JobManager, job_id: str, statuses: set[str]) -> dict:
    deadline = time.time() + 5
    while time.time() < deadline:
        record = manager.get(job_id)
        if record and record.get("status") in statuses:
            return record
        time.sleep(0.01)
    raise AssertionError(f"job did not reach {statuses}: {manager.get(job_id)}")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "runs"
        run = root / "ok"
        manager = JobManager()
        manager.register_root(root)
        job_id = manager.start("test", run, lambda progress: "done")
        finished = wait_for(manager, job_id, {"completed"})
        assert finished["result"] == "done"
        assert any(item["job_id"] == job_id for item in manager.list_jobs(root))

        live_id = manager.start("test", root / "live", lambda progress: _slow_work(progress))
        time.sleep(0.03)
        assert next(item for item in manager.list_jobs(root) if item["job_id"] == live_id)["status"] in {"running", "cancelling"}
        manager.cancel(live_id)
        wait_for(manager, live_id, {"cancelled"})

        restarted = JobManager()
        restarted.register_root(root)
        assert restarted.get(job_id)["status"] == "completed"
        legacy_run = root / "legacy"
        legacy_dir = legacy_run / ".paperaudit" / "jobs"
        legacy_dir.mkdir(parents=True)
        legacy_id = "legacy123456"
        (legacy_dir / f"{legacy_id}.json").write_text(
            json.dumps(
                {
                    "job_id": legacy_id,
                    "kind": "test",
                    "run_dir": str(legacy_run),
                    "status": "completed",
                    "stage": "complete",
                    "message": "旧项目任务",
                    "progress": 100,
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "finished_at": "2026-01-01T00:00:01+00:00",
                    "result": "legacy",
                    "error": None,
                    "payload": {},
                }
            ),
            encoding="utf-8",
        )
        assert restarted.get(legacy_id)["result"] == "legacy"
        hidden = root / "nested" / "deeper" / ".paperrevamper" / "jobs"
        hidden.mkdir(parents=True)
        (hidden / "unrelated.json").write_text(json.dumps({"job_id": "unrelated"}), encoding="utf-8")
        assert not any(item["job_id"] == "unrelated" for item in restarted.list_jobs(root))

        cancel_run = root / "cancel"
        cancel_id = manager.start(
            "test",
            cancel_run,
            lambda progress: _slow_work(progress),
        )
        time.sleep(0.03)
        manager.cancel(cancel_id)
        assert wait_for(manager, cancel_id, {"cancelled"})["status"] == "cancelled"

        recover_run = root / "recover"
        recover_dir = recover_run / ".paperrevamper" / "jobs"
        recover_dir.mkdir(parents=True)
        recover_id = "recover123456"
        (recover_dir / f"{recover_id}.json").write_text(
            json.dumps(
                {
                    "job_id": recover_id,
                    "kind": "test",
                    "run_dir": str(recover_run),
                    "status": "running",
                    "stage": "working",
                    "message": "旧进程中断",
                    "progress": 40,
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "finished_at": None,
                    "result": None,
                    "error": None,
                    "payload": {},
                }
            ),
            encoding="utf-8",
        )
        recovered = restarted.get(recover_id)
        assert recovered["status"] == "interrupted"
        resumed = restarted.resume(recover_id, lambda progress: "resumed")
        assert resumed["status"] == "queued"
        assert wait_for(restarted, recover_id, {"completed"})["result"] == "resumed"

    print({"status": "ok", "persistent": True, "cancel": True, "resume": True})


def _slow_work(progress) -> str:
    for index in range(100):
        progress("working", "running", index)
        time.sleep(0.01)
    return "unexpected"


if __name__ == "__main__":
    main()
