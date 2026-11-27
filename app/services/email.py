"""Centralised transactional email: SMTP delivery plus the persistent outbox.

Nothing in a route handler touches SMTP directly — routes call ``enqueue_email``,
which only writes an outbox row into the caller's own transaction. A background
worker (``app.services.email_worker``) drains that queue, so an SMTP outage never
rolls back an already-committed business action.
"""

import json
import logging
from datetime import datetime, timezone
from email.message import EmailMessage

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import BASE_DIR, get_settings
from app.models.email import EmailOutbox

logger = logging.getLogger(__name__)

# HTML templates are auto-escaped; .txt twins render plain text unescaped.
_email_env = Environment(
    loader=FileSystemLoader(str(BASE_DIR / "templates" / "emails")),
    autoescape=select_autoescape(default=False, enabled_extensions=("html",)),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


def _utcnow() -> datetime:
    """Naive UTC now, matching the server-side ``func.now()`` columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _email_context(data: dict) -> dict:
    settings = get_settings()
    return {
        "base_url": settings.app_base_url.rstrip("/"),
        "site_name": settings.site_name,
        "now": _utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        **(data or {}),
    }


def render_email(template_name: str, data: dict | None = None) -> tuple[str, str]:
    """Render one email template into ``(html, text)``.

    A template is a pair: ``templates/emails/<name>.html`` and ``.txt``. The text
    twin is optional and falls back to empty when absent.
    """
    ctx = _email_context(data)
    html = _email_env.get_template(f"{template_name}.html").render(**ctx)
    try:
        text = _email_env.get_template(f"{template_name}.txt").render(**ctx)
    except Exception:  # noqa: BLE001 - a missing plain-text twin must not kill delivery
        text = ""
    return html, text


def _message(html: str, text: str, subject: str, recipient: str) -> EmailMessage:
    """Build an RFC-5322 message with UTF-8 HTML plus a plain-text alternative.

    Returns an ``EmailMessage`` object, not its ``str()`` form: aiosmtplib needs
    the real object to read the recipients for the SMTP envelope. A plain string
    has no ``To`` header aiosmtplib can use, which is why sends failed with
    "Recipients must be provided with raw messages".
    """
    from email.message import EmailMessage

    settings = get_settings()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{settings.mail_from_name} <{settings.mail_from_address}>"
    msg["To"] = recipient
    msg["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    msg.set_content(text or "This message requires an HTML-capable email client.", subtype="plain")
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def _send_kwargs() -> dict:
    """SMTP kwargs from settings. Credentials are never logged.

    aiosmtplib has no ``use_ssl`` argument — implicit TLS is ``use_tls=True`` — and
    it rejects STARTTLS and implicit TLS together. Implicit TLS only makes sense on
    port 465, so that is the one deciding case: ``MOVIEHUB_SMTP_USE_SSL=true`` on any
    other port is ignored rather than being sent as a TypeError to the mail server.
    """
    s = get_settings()
    implicit = bool(s.smtp_use_ssl) and s.smtp_port == 465
    return {
        "hostname": s.smtp_host,
        "port": s.smtp_port,
        "username": s.smtp_username,
        "password": s.smtp_password,
        "use_tls": implicit,
        "start_tls": (not implicit) and bool(s.smtp_use_tls),
        "timeout": s.smtp_timeout,
    }


async def send_message(recipient: str, subject: str, html: str, text: str) -> None:
    """Send one message over SMTP. Raises on failure; the caller decides whether
    the failure is transient or permanent.

    ``recipients`` must be passed explicitly: aiosmtplib ignores the ``To:`` header
    for envelope purposes, so without it the send fails with
    "Recipients must be provided with raw messages".
    """
    await aiosmtplib.send(
        _message(html, text, subject, recipient),
        sender=get_settings().mail_from_address,
        recipients=[recipient],
        **_send_kwargs(),
    )


def is_permanent_failure(exc: BaseException) -> bool:
    """Recipient-level rejections (unknown address, mailbox full) will not heal,
    so they must not be retried to exhaustion."""
    return isinstance(exc, aiosmtplib.SMTPRecipientsRefused)


async def enqueue_email(
    session: AsyncSession,
    *,
    recipient: str,
    subject: str,
    template_name: str,
    template_data: dict | None = None,
    dedupe_key: str | None = None,
    max_attempts: int | None = None,
) -> EmailOutbox | None:
    """Queue an email in the caller's transaction.

    The row is flushed with the caller's commit, so an email is only durable when
    the business action it announces has committed. Returns ``None`` (queues
    nothing) when ``dedupe_key`` already exists, so a retried operation cannot
    duplicate a notification.
    """
    recipient = (recipient or "").strip().lower()
    if not recipient or "@" not in recipient:
        logger.warning("enqueue_email skipped: invalid recipient")
        return None
    if dedupe_key:
        exists = await session.scalar(select(EmailOutbox.id).where(EmailOutbox.dedupe_key == dedupe_key))
        if exists is not None:
            return None
    job = EmailOutbox(
        recipient=recipient,
        subject=subject,
        template_name=template_name,
        template_data=json.dumps(template_data or {}, ensure_ascii=False),
        dedupe_key=dedupe_key,
        max_attempts=max_attempts if max_attempts is not None else get_settings().email_max_retries,
    )
    session.add(job)
    return job


def parse_template_data(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}
