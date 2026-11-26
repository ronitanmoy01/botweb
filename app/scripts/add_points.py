"""Add points to a user's account by email.

Usage:
    uv run python -m app.scripts.add_points user@example.com 100 "Manual adjustment"
    uv run python -m app.scripts.add_points user@example.com 50 "Contest reward"

The reason is optional — defaults to "Manual credit".
"""

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.database import SessionLocal, engine
from app.models.community import PointsReason
from app.models.user import User
from app.services import points


async def add_points(email: str, amount: int, reason: str) -> str:
    """Credit points to one user, automatically fixing any negative available balance."""
    email = email.strip().lower()
    if amount <= 0:
        return f"Amount must be positive, got {amount}"

    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == email))
        if user is None:
            return f"No user found with email: {email}"

        old_balance = int(user.points_balance)
        old_reserved = int(user.points_reserved)
        old_available = old_balance - old_reserved

        # ALWAYS fix negative available BEFORE adding points. If reserved > balance,
        # reduce reserved to match balance so available becomes 0 (not negative).
        # This ensures the new points will actually be spendable.
        fix_note = ""
        if old_reserved > old_balance:
            freed = old_reserved - old_balance
            user.points_reserved = old_balance
            fix_note = f" (auto-fixed: freed {freed} over-reserved points)"

        # Now credit the new points
        key = f"manual:credit:{email}:{amount}:{reason}"
        credited = await points.credit(
            session,
            user,
            amount,
            PointsReason.CONTRIBUTION_REWARD,
            key,
            reference_type="manual",
        )

        if not credited:
            # Key was already used, but still commit any fix we made above
            await session.commit()
            return f"Points already credited with this key (duplicate). No change to balance.{fix_note if fix_note else ''}"

        await session.commit()

        # Final state - guaranteed to be non-negative
        final_balance = int(user.points_balance)
        final_reserved = int(user.points_reserved)
        final_available = final_balance - final_reserved

        return f"✓ Credited {amount} points to {user.display_name} ({email}). Balance: {final_balance}, Reserved: {final_reserved}, Available: {final_available}{fix_note}"


async def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Add points to a user's account by email.")
    parser.add_argument("email", help="User's email address")
    parser.add_argument("amount", type=int, help="Points to add (positive integer)")
    parser.add_argument("reason", nargs="?", default="Manual credit", help="Reason for the credit")
    args = parser.parse_args(argv)

    try:
        message = await add_points(args.email, args.amount, args.reason)
        print(message)
        return 0 if "✓" in message else 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
