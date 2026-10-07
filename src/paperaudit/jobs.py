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
import json
from pathlib import Path
from typing import Any, Callable

from paperaudit.collaboration import append_trace


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobCancelled(Exception):
    """Raised inside a worker when the user requests cooperative cancellation."""


class JobManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._roots: set[Path] = set()

    def register_root(self, root: str | Path) -> None:
        """Register a panel run root so persisted jobs can be recovered."""

        with self._lock:
            self._roots.add(Path(root).resolve())

    @staticmethod
    def _record_path(record: dict[str, Any]) -> Path:
        run = Path(str(record.get("run_dir", "")))
        return run / ".paperaudit" / "jobs" / f"{record['job_id']}.json"

    @staticmethod
    def _job_files(root: Path):
        """Yield persisted job files without recursively scanning user data.

        Panel runs are created directly below the registered run root.  A
        bounded lookup keeps recovery responsive when the root also contains
        manuscript attachments or large unrelated directories.
        """

        directories = [root / ".paperaudit" / "jobs"]
        try:
            directories.extend(
                child / ".paperaudit" / "jobs"
                for child in root.iterdir()
                if child.is_dir() and not child.is_symlink() and child.name != ".paperaudit"
            )
        except OSError:
            return
        for directory in directories:
            try:
                if directory.is_symlink() or not directory.is_dir():
                    continue
                yield from (path for path in directory.glob("*.json") if not path.is_symlink())
            except OSError:
                continue

    def _persist(self, record: dict[str, Any]) -> None:
        path = self._record_path(record)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.tmp")
            temporary.write_text(
                json.dumps(record, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError:
            # A job must remain usable when a read-only output directory is
            # supplied; the in-memory state is still authoritative for it.
            pass

    def _load_persisted(self, job_id: str) -> dict[str, Any] | None:
        candidates: list[Path] = []
        for root in self._roots:
            candidates.extend(path for path in self._job_files(root) if path.name == f"{job_id}.json")
        for path in candidates:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            if isinstance(value, dict) and str(value.get("job_id")) == job_id:
                return value
        return None

    def _recover_if_stale(self, record: dict[str, Any]) -> dict[str, Any]:
        if record.get("status") in {"queued", "running", "cancelling"}:
            record["previous_status"] = record.get("status")
            record["status"] = "interrupted"
            record["stage"] = "interrupted"
            record["message"] = "服务重启后任务未自动恢复"
            record["error"] = "worker process was interrupted"
            record["finished_at"] = _now()
            self._persist(record)
        return record

    def _spawn(self, job_id: str, work: Callable[[Callable[..., None]], Any]) -> None:
        record = self._jobs[job_id]
        run = Path(record["run_dir"])

        def runner() -> None:
            run.mkdir(parents=True, exist_ok=True)
            try:
                if self._cancel_events[job_id].is_set():
                    raise JobCancelled()
                self.update(job_id, status="running", stage="starting", message="启动任务", progress=1)
                result = work(lambda *args, **kwargs: self.progress(job_id, *args, **kwargs))
                if self._cancel_events[job_id].is_set():
                    raise JobCancelled()
                self.update(
                    job_id,
                    status="completed",
                    stage="complete",
                    message="任务完成",
                    progress=100,
                    result=str(result) if isinstance(result, Path) else result,
                    finished_at=_now(),
                )
            except JobCancelled:
                self.update(
                    job_id,
                    status="cancelled",
                    stage="cancelled",
                    message="任务已取消",
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

        threading.Thread(target=runner, name=f"paperaudit-{record['kind']}-{job_id}", daemon=True).start()

    def start(
        self,
        kind: str,
        run_dir: str | Path,
        work: Callable[[Callable[..., None]], Any],
        payload: dict[str, Any] | None = None,
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
            "cancel_requested": False,
            "payload": payload or {},
            "created_at": _now(),
        }
        with self._lock:
            self._jobs[job_id] = record
            self._cancel_events[job_id] = threading.Event()
            self._persist(record)
            self._spawn(job_id, work)
        return job_id

    def progress(self, job_id: str, stage: str, message: str, progress: int | float | None = None, **payload: Any) -> None:
        with self._lock:
            event = self._cancel_events.get(job_id)
        if event is not None and event.is_set():
            raise JobCancelled()
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
            self._persist(record)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                record = self._load_persisted(job_id)
                if record is not None:
                    record = self._recover_if_stale(record)
            return deepcopy(record) if record else None

    def list_jobs(self, root: str | Path | None = None) -> list[dict[str, Any]]:
        """List current and persisted jobs for the panel recovery picker."""

        with self._lock:
            records: dict[str, dict[str, Any]] = {
                job_id: record for job_id, record in self._jobs.items()
            }
            roots = {Path(root).resolve()} if root else set(self._roots)
            for registered in roots:
                for path in self._job_files(registered):
                    try:
                        value = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, ValueError, TypeError):
                        continue
                    if isinstance(value, dict) and value.get("job_id"):
                        records.setdefault(str(value["job_id"]), value)
            values = [
                self._recover_if_stale(record) if job_id not in self._jobs else record
                for job_id, record in records.items()
            ]
            values.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
            return deepcopy(values)

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._jobs.get(job_id) or self._load_persisted(job_id)
            if record is None:
                return None
            if record.get("status") in {"completed", "failed", "cancelled", "interrupted"}:
                return deepcopy(record)
            event = self._cancel_events.setdefault(job_id, threading.Event())
            event.set()
            record["cancel_requested"] = True
            record["status"] = "cancelling" if record.get("status") == "running" else "cancelled"
            record["stage"] = "cancelling" if record["status"] == "cancelling" else "cancelled"
            record["message"] = "正在取消任务" if record["status"] == "cancelling" else "任务已取消"
            if record["status"] == "cancelled":
                record["finished_at"] = _now()
            self._jobs[job_id] = record
            self._persist(record)
            return deepcopy(record)

    def resume(
        self,
        job_id: str,
        work: Callable[[Callable[..., None]], Any],
    ) -> dict[str, Any] | None:
        """Resume a persisted failed/interrupted/cancelled job with a new worker."""

        with self._lock:
            record = self._jobs.get(job_id) or self._load_persisted(job_id)
            if record is None or record.get("status") not in {"failed", "interrupted", "cancelled"}:
                return deepcopy(record) if record else None
            record.update(
                {
                    "status": "queued",
                    "stage": "queued",
                    "message": "等待恢复执行",
                    "progress": 0,
                    "started_at": None,
                    "finished_at": None,
                    "result": None,
                    "error": None,
                    "cancel_requested": False,
                    "resumed_at": _now(),
                }
            )
            self._jobs[job_id] = record
            self._cancel_events[job_id] = threading.Event()
            self._persist(record)
            self._spawn(job_id, work)
            return deepcopy(record)


JOBS = JobManager()
