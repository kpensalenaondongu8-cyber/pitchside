"""Helpers for grouping matches by competition (a league or a cup)."""

import re
import unicodedata

# Competitions shown first on the home page, in this order.
# Each entry lists every spelling the providers use for the same competition,
# because football-data.org and API-Football name them differently.
TOP_COMPETITIONS = [
    ["uefa champions league"],
    ["premier league", "england · premier league"],
    ["la liga", "spain · la liga", "primera division"],
    ["serie a", "italy · serie a"],
    ["bundesliga", "germany · bundesliga"],
    ["ligue 1", "france · ligue 1"],
]

LIVE_STATUSES = ("IN_PLAY", "PAUSED")
UPCOMING_STATUSES = ("TIMED", "SCHEDULED")


def slugify(name: str) -> str:
    """Turn a competition name into a URL-friendly id.

    "England · Premier League" -> "england-premier-league"
    """
    # Strip accents first: "Série A" -> "Serie A"
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")
    return slug or "other"


def rank(name: str) -> int:
    """Position in the top list. Competitions not in the list come after all of them."""
    lowered = name.lower()
    for position, spellings in enumerate(TOP_COMPETITIONS):
        if lowered in spellings:
            return position
    return len(TOP_COMPETITIONS)


def sort_matches(matches: list[dict]) -> list[dict]:
    """Put each competition's matches together: top competitions first, then A to Z, then by kickoff."""
    return sorted(matches, key=lambda m: (rank(m["competition"]), m["competition"], m["kickoff"]))


def group_by_competition(matches: list[dict]) -> list[dict]:
    """Count matches per competition. Top competitions first, the rest A to Z."""
    groups = {}
    for match in matches:
        slug = slugify(match["competition"])
        group = groups.setdefault(
            slug,
            {"slug": slug, "name": match["competition"], "total": 0, "live": 0, "finished": 0, "upcoming": 0},
        )
        group["total"] += 1
        if match["status"] in LIVE_STATUSES:
            group["live"] += 1
        elif match["status"] == "FINISHED":
            group["finished"] += 1
        elif match["status"] in UPCOMING_STATUSES:
            group["upcoming"] += 1
    return sorted(groups.values(), key=lambda g: (rank(g["name"]), g["name"]))
