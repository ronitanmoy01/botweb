"""Background worker that drains the email outbox.

Runs as an in-process asyncio task started from the FastAPI lifespan hook, so no
Redis/Celery is required. Claiming uses ``FOR UPDATE SKIP LOCKED`` on PostgreSQL
(one row can only be owned by one worker) and a plain status flip on SQLite, so a
single dev process and a multi-worker production deploy both stay correct.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.email import EmailOutbox, EmailStatus
from app.services.email import is_permanent_failure, parse_template_data, render_email, send_message

logger = logging.getLogger(__name__)

_BATCH = 10
# A job stuck in "processing" for longer than this (e.g. the worker died between
# claiming and sending) is returned to pending so mail is not lost on restart.
_STALE_AFTER = timedelta(minutes=10)
_BACKOFF_MINUTES = (1, 2, 4)


def _utcnow() -> datetime:
    """Naive UTC now, matching the naive ``func.now()`` columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_postgres() -> bool:
    return get_settings().database_url.startswith(("postgresql", "postgres"))


def _backoff(minutes_index: int) -> timedelta:
    return timedelta(minutes=_BACKOFF_MINUTES[min(minutes_index, len(_BACKOFF_MINUTES) - 1)])


async def _claim_batch(session: AsyncSession) -> list[EmailOutbox]:
    """Select up to ``_BATCH`` sendable jobs and mark them processing."""
    now = _utcnow()
    stmt = (
        select(EmailOutbox)
        .where(
            EmailOutbox.status == EmailStatus.PENDING,
            (EmailOutbox.next_retry_at.is_(None)) | (EmailOutbox.next_retry_at <= now),
        )
        .order_by(EmailOutbox.created_at)
        .limit(_BATCH)
    )
    if _is_postgres():
        stmt = stmt.with_for_update(skip_locked=True)
    jobs = list((await session.scalars(stmt)).all())
    for job in jobs:
        job.status = EmailStatus.PROCESSING
    await session.commit()
    return jobs


async def _reset_stale_processing(session: AsyncSession) -> None:
    """Re-queue jobs whose previous sender never reported back."""
    await session.execute(
        update(EmailOutbox)
        .where(
            EmailOutbox.status == EmailStatus.PROCESSING,
            EmailOutbox.created_at < _utcnow() - _STALE_AFTER,
        )
        .values(status=EmailStatus.PENDING)
    )
    await session.commit()


async def _process_job(session: AsyncSession, job: EmailOutbox) -> None:
    job.attempt_count += 1
    try:
        html, text = render_email(job.template_name, parse_template_data(job.template_data))
        await send_message(job.recipient, job.subject, html, text)
    except Exception as exc:  # noqa: BLE001 - one bad email must not kill the loop
        detail = f"{type(exc).__name__}: {exc}"
        if is_permanent_failure(exc) or job.attempt_count >= job.max_attempts:
            job.status = EmailStatus.FAILED
            job.last_error = detail
            logger.warning("email %s permanently failed: %s", job.id, detail)
        else:
            job.status = EmailStatus.PENDING
            job.last_error = detail
            job.next_retry_at = _utcnow() + _backoff(job.attempt_count - 1)
            logger.warning("email %s failed, retry #%d later: %s", job.id, job.attempt_count, detail)
    else:
        job.status = EmailStatus.SENT
        job.sent_at = _utcnow()
    await session.commit()


async def drain_once() -> int:
    """Process one batch of pending email. Returns how many were attempted."""
    async with SessionLocal() as session:
        await _reset_stale_processing(session)
        jobs = await _claim_batch(session)
        for job in jobs:
            await _process_job(session, job)
        return len(jobs)


async def worker_loop(stop: asyncio.Event) -> None:
    """Poll until ``stop`` is set, draining whatever is queued."""
    interval = get_settings().email_worker_interval_seconds
    while not stop.is_set():
        try:
            await drain_once()
        except Exception:  # noqa: BLE001 - log and keep polling; never take the app down
            logger.exception("email worker drain failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


def worker_available() -> bool:
    """True when the worker should run at all."""
    settings = get_settings()
    return settings.mail_enabled and settings.email_configured
