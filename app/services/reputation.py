"""Lifetime reputation levels. Deliberately hard to earn.

Ranks are judged on ``points_lifetime`` — points never drop off — so the
leaderboard rewards sustained contribution rather than a single lucky burst.
Thresholds are intentionally high: there is exactly one member per rank at the
start, and each jump requires several months of approved contributions.
"""

from dataclasses import dataclass

from app.models.user import User


@dataclass(frozen=True)
class Level:
    name: str
    min_lifetime: int
    color: str  # Tailwind-safe hex, used inline in templates


# Ordered ascending by threshold. A member always gets the highest level they qualify for.
_LEVELS = [
    Level("Newcomer", 0, "#9ca3af"),
    Level("Contributor", 250, "#22d3ee"),
    Level("Expert", 1_000, "#60a5fa"),
    Level("Master", 5_000, "#a78bfa"),
    Level("Legend", 20_000, "#f472b6"),
    Level("Mythic", 100_000, "#f59e0b"),
]


def level_for(lifetime_points: int) -> Level:
    result = _LEVELS[0]
    for level in _LEVELS:
        if lifetime_points >= level.min_lifetime:
            result = level
    return result


def level_for_user(user: User) -> Level:
    return level_for(int(user.points_lifetime))


def next_level(lifetime_points: int) -> Level | None:
    """The next rank above the current one, or None when already Mythic."""
    current = level_for(lifetime_points)
    for level in _LEVELS:
        if level.min_lifetime > current.min_lifetime:
            return level
    return None


def level_progress(user: User) -> dict:
    """Percentage toward the next rank, for progress bars."""
    lifetime = int(user.points_lifetime)
    current = level_for(lifetime)
    nxt = next_level(lifetime)
    if nxt is None:
        return {"pct": 100, "level": current, "next": None, "remaining": 0}
    span = nxt.min_lifetime - current.min_lifetime
    done = lifetime - current.min_lifetime
    pct = max(0, min(100, round(done * 100 / span))) if span > 0 else 100
    return {"pct": pct, "level": current, "next": nxt, "remaining": max(0, nxt.min_lifetime - lifetime)}


def all_levels() -> list[Level]:
    return list(_LEVELS)
