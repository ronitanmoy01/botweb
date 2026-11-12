import hmac
import secrets
import time
from collections import defaultdict, deque

from pwdlib import PasswordHash

from app.core.config import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH

_hasher = PasswordHash.recommended()  # Argon2id
# Verified against when the email is unknown, so response timing doesn't reveal which emails exist.
DUMMY_PASSWORD_HASH = _hasher.hash(secrets.token_urlsafe(16))

LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 5
# shortcut: in-memory and per-process, so lockouts reset on restart and don't span workers; move to a shared store before multi-worker deploys
_login_failures: dict[str, deque[float]] = defaultdict(deque)


def login_locked(key: str) -> bool:
    attempts = _login_failures[key]
    cutoff = time.monotonic() - LOGIN_WINDOW_SECONDS
    while attempts and attempts[0] < cutoff:
        attempts.popleft()
    return len(attempts) >= LOGIN_MAX_FAILURES


def record_login_failure(key: str) -> None:
    _login_failures[key].append(time.monotonic())


def clear_login_failures(key: str) -> None:
    _login_failures.pop(key, None)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _hasher.verify(password, password_hash)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_matches(sent: str, expected: str) -> bool:
    return bool(sent) and bool(expected) and hmac.compare_digest(sent, expected)


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Password must be at most {MAX_PASSWORD_LENGTH} characters."
    return None
