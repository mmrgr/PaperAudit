"""Small in-process job runner used by the local control panel.

The panel is intentionally local and dependency-free.  A job gives the UI a
stable id to poll while the existing synchronous paper functions keep their
CLI behaviour and remain easy for Coding Agents to call directly.
"""

from __future__ import annotations

import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from paperaudit.collaboration import append_trace


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def start(
        self,
        kind: str,
        run_dir: str | Path,
        work: Callable[[Callable[..., None]], Any],
    ) -> str:
        job_id = uuid.uuid4().hex[:12]
        run = Path(run_dir).resolve()
        record = {
            "job_id": job_id,
            "kind": kind,
            "run_dir": str(run),
            "status": "queued",
            "stage": "queued",
            "message": "等待执行",
            "progress": 0,
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
        }
        with self._lock:
            self._jobs[job_id] = record

        def runner() -> None:
            run.mkdir(parents=True, exist_ok=True)
            self.update(job_id, status="running", stage="starting", message="启动任务", progress=1)
            try:
                result = work(lambda *args, **kwargs: self.progress(job_id, *args, **kwargs))
                self.update(
                    job_id,
                    status="completed",
                    stage="complete",
                    message="任务完成",
                    progress=100,
                    result=str(result) if isinstance(result, Path) else result,
                    finished_at=_now(),
                )
            except Exception as exc:  # pragma: no cover - surfaced through the API
                self.update(
                    job_id,
                    status="failed",
                    stage="failed",
                    message="任务失败",
                    error=f"{type(exc).__name__}: {exc}",
                    finished_at=_now(),
                )

        threading.Thread(target=runner, name=f"paperaudit-{kind}-{job_id}", daemon=True).start()
        return job_id

    def progress(self, job_id: str, stage: str, message: str, progress: int | float | None = None, **payload: Any) -> None:
        fields: dict[str, Any] = {"stage": str(stage), "message": str(message), **payload}
        if progress is not None:
            fields["progress"] = max(0, min(100, int(progress)))
        self.update(job_id, **fields)
        with self._lock:
            run = Path(self._jobs.get(job_id, {}).get("run_dir", ""))
        if run:
            try:
                run.mkdir(parents=True, exist_ok=True)
                append_trace(run, "job_progress", job_id=job_id, **fields)
            except OSError:
                pass

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return
            if record.get("started_at") is None and fields.get("status") == "running":
                fields.setdefault("started_at", _now())
            record.update(fields)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._jobs.get(job_id)
            return deepcopy(record) if record else None


JOBS = JobManager()
