from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import uuid4

from app.bulk.excel import build_bulk_workbook
from app.bulk.ingest import ingest_asin_file
from app.bulk.processor import BulkProcessor, build_attention_and_summary
from app.core.config import get_settings
from app.core.exceptions import BulkLiveProviderForbiddenError
from app.models.bulk import (
    BulkJobOptions,
    BulkJobProgress,
    BulkJobResponse,
    BulkJobStatus,
    BulkUsageStats,
)

Runner = Callable[[], Coroutine[Any, Any, None] | None]


class JobBackend:
    """Swap later for Redis/Celery/RQ/Temporal without changing routes."""

    def submit(self, runner: Runner) -> None:
        raise NotImplementedError


class InProcessJobBackend(JobBackend):
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    def submit(self, runner: Runner) -> None:
        loop = asyncio.get_running_loop()

        async def wrapped() -> None:
            result = runner()
            if asyncio.iscoroutine(result):
                await result

        task = loop.create_task(wrapped())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, BulkJobResponse] = {}
        self._lock = Lock()

    def clear(self) -> None:
        with self._lock:
            self._jobs.clear()

    def put(self, job: BulkJobResponse) -> None:
        with self._lock:
            self._jobs[job.job_id] = job

    def get(self, job_id: str) -> BulkJobResponse | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job is not None else None

    def update(self, job_id: str, **changes: object) -> BulkJobResponse | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            payload = job.model_dump()
            payload.update(changes)
            payload["updated_at"] = datetime.now(UTC)
            updated = BulkJobResponse.model_validate(payload)
            self._jobs[job_id] = updated
            return updated.model_copy(deep=True)


class BulkJobService:
    def __init__(
        self,
        store: InMemoryJobStore,
        backend: JobBackend,
        processor_factory: Callable[[], BulkProcessor],
    ) -> None:
        self._store = store
        self._backend = backend
        self._processor_factory = processor_factory
        self._inputs: dict[str, tuple[str, bytes]] = {}

    def preview(self, filename: str, data: bytes):
        return ingest_asin_file(filename, data)

    def create_job(self, filename: str, data: bytes, options: BulkJobOptions) -> BulkJobResponse:
        stats, unique, ingest_failures = ingest_asin_file(filename, data)
        now = datetime.now(UTC)
        settings = get_settings()
        job = BulkJobResponse(
            job_id=uuid4().hex,
            status="queued",
            options=options,
            ingest=stats,
            progress=BulkJobProgress(total=len(unique)),
            usage=BulkUsageStats(
                product_provider=settings.bulk_product_provider,
                ai_provider=settings.bulk_ai_provider if options.analysis_mode == "deep_ai" else None,
                paid_api_usage=False,
                note="Mock provider — no paid API usage",
                requested_asins=len(unique),
            ),
            failures=ingest_failures,
            created_at=now,
            updated_at=now,
            live_providers_enabled=settings.bulk_live_provider_calls_enabled,
        )
        self._store.put(job)
        self._inputs[job.job_id] = (filename, data)
        self._backend.submit(lambda: self._run(job.job_id, unique, ingest_failures, options))
        return self._store.get(job.job_id) or job

    def get_job(self, job_id: str) -> BulkJobResponse | None:
        return self._store.get(job_id)

    async def _run(
        self,
        job_id: str,
        unique: list[str],
        ingest_failures: list,
        options: BulkJobOptions,
    ) -> None:
        self._store.update(job_id, status="running")

        def on_progress(**kwargs: int) -> None:
            current = self._store.get(job_id)
            if current is None:
                return
            progress = current.progress.model_copy(update=kwargs)
            self._store.update(job_id, progress=progress)

        try:
            processor = self._processor_factory()
            results, failures, usage = await processor.process(
                unique,
                marketplace=options.marketplace,
                options=options,
                ingest_failures=ingest_failures,
                on_progress=on_progress,
            )
        except BulkLiveProviderForbiddenError as exc:
            self._store.update(job_id, status="failed", error=str(exc))
            self._persist_job(job_id)
            return
        except Exception:
            self._store.update(
                job_id,
                status="failed",
                error="Bulk analysis failed before a report could be produced.",
            )
            self._persist_job(job_id)
            return

        attention, summary = build_attention_and_summary(unique, results, failures)
        lookup_failed = [item for item in failures if item.kind != "invalid"]
        status: BulkJobStatus = "completed_with_errors" if lookup_failed or ingest_failures else "completed"
        progress = BulkJobProgress(
            total=len(unique),
            processed=len(unique),
            successful=len(results),
            failed=len(lookup_failed),
            cache_hits=usage.cache_hits,
            provider_calls=usage.provider_calls,
        )
        self._store.update(
            job_id,
            status=status,
            results=results,
            failures=failures,
            attention=attention,
            summary=summary,
            usage=usage,
            progress=progress,
            error=None,
        )
        self._persist_job(job_id, with_excel=True)

    def _persist_job(self, job_id: str, with_excel: bool = False) -> None:
        finished = self._store.get(job_id)
        if finished is None:
            return
        from app.services.artifact_persistence_service import get_artifact_service

        artifacts = get_artifact_service()
        upload = self._inputs.pop(job_id, None)
        artifacts.save_bulk_job(finished, upload[1] if upload else None, upload[0] if upload else None)
        if with_excel:
            artifacts.save_generated_excel(finished, build_bulk_workbook(finished))
