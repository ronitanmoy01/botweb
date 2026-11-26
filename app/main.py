import asyncio
import logging

from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.core.config import BASE_DIR, get_settings
from app.core.templating import templates
from app.dependencies.auth import get_current_user
from app.routers import health, seo
from app.routers.admin import analytics as admin_analytics, applications as admin_applications, audit as admin_audit, contributions as admin_contributions, dashboard, email as admin_email, mail_log, moderation, movies as admin_movies, series as admin_series, taxonomies as admin_taxonomies, users as admin_users, vault as admin_vault
from app.routers.auth import accounts, email_verification, password_reset, profile
from app.routers.community import leaderboard, public_profile
from app.routers.member import community as member_community, features as member_features, social as member_social
from app.routers.public import catalog, home, person
from app.services import email_worker

SESSION_MAX_AGE_SECONDS = 14 * 24 * 60 * 60
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    hide_api_docs = settings.environment == "production"  # the schema maps every admin route, so keep it off in prod
    app = FastAPI(
        title=settings.site_name,
        docs_url=None if hide_api_docs else "/docs",
        openapi_url=None if hide_api_docs else "/openapi.json",
        redoc_url=None,
        dependencies=[Depends(get_current_user)],  # resolves the user once per request for the header
    )

    _email_worker_stop: asyncio.Event | None = None
    _email_worker_task: asyncio.Task | None = None

    @app.on_event("startup")
    async def _start_email_worker() -> None:
        """Drain the email outbox on a background task. Never crashes the app if SMTP is misconfigured."""
        nonlocal _email_worker_stop, _email_worker_task
        if not email_worker.worker_available():
            if settings.mail_enabled and not settings.email_configured:
                logger.warning("Email is enabled but SMTP is not fully configured; the worker will not start.")
            return
        _email_worker_stop = asyncio.Event()
        _email_worker_task = asyncio.create_task(email_worker.worker_loop(_email_worker_stop))
        logger.info("Email worker started")

    @app.on_event("shutdown")
    async def _stop_email_worker() -> None:
        nonlocal _email_worker_stop, _email_worker_task
        if _email_worker_stop is not None:
            _email_worker_stop.set()
        if _email_worker_task is not None:
            try:
                await asyncio.wait_for(_email_worker_task, timeout=30)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001 - shutdown must not hang
                _email_worker_task.cancel()

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        same_site="lax",
        https_only=settings.environment == "production",
        max_age=SESSION_MAX_AGE_SECONDS,
    )
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        # Templates use inline <script>/<style>/onclick and the Tailwind + htmx CDNs, so script and style need 'unsafe-inline'.
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://unpkg.com; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; media-src 'self' https:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if settings.environment == "production":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    uploads_dir = get_settings().uploads_dir
    if uploads_dir.exists():
        app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")
    app.include_router(home.router)
    app.include_router(catalog.router)
    app.include_router(person.router)
    app.include_router(seo.router)
    app.include_router(health.router)
    app.include_router(accounts.router)
    app.include_router(profile.router)
    app.include_router(password_reset.router)
    app.include_router(email_verification.router)
    app.include_router(member_community.router)
    app.include_router(member_features.router)
    app.include_router(member_social.router)
    app.include_router(leaderboard.router)
    app.include_router(public_profile.router)
    app.include_router(admin_email.router)
    app.include_router(mail_log.router)
    app.include_router(admin_applications.router)
    app.include_router(admin_contributions.router)
    app.include_router(admin_vault.router)
    app.include_router(dashboard.router)
    app.include_router(admin_movies.router)
    app.include_router(admin_series.router)
    app.include_router(admin_taxonomies.router)
    app.include_router(moderation.router)
    app.include_router(admin_users.router)
    app.include_router(admin_audit.router)
    app.include_router(admin_analytics.router)

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        return templates.TemplateResponse(request, "errors/404.html", {"page_title": "Not found"}, status_code=404)

    return app


app = create_app()
