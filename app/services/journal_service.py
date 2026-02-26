from __future__ import annotations

import logging

from app.database import get_pool

logger = logging.getLogger(__name__)

# Predefined tags for mood/state categorization
VALID_TAGS = {"stress", "energy", "social", "work", "health", "gratitude", "achievement"}


async def save_journal_entry(
    user_id: int,
    content: str,
    mood_score: int | None = None,
    energy_level: int | None = None,
    tags: list[str] | None = None,
) -> dict:
    """Save a journal entry with extracted mood/energy/tags."""
    pool = await get_pool()

    # Filter to valid tags only
    clean_tags = [t for t in (tags or []) if t in VALID_TAGS] or None

    row = await pool.fetchrow(
        """INSERT INTO journal_entries
               (user_id, content, mood_score, energy_level, tags)
           VALUES ($1, $2, $3, $4, $5)
           RETURNING id, created_at""",
        user_id,
        content,
        mood_score,
        energy_level,
        clean_tags,
    )

    logger.info(
        "Journal entry saved: user_id=%s mood=%s energy=%s tags=%s",
        user_id, mood_score, energy_level, clean_tags,
    )

    return {
        "id": row["id"],
        "mood_score": mood_score,
        "energy_level": energy_level,
        "tags": clean_tags,
        "created_at": row["created_at"],
    }


async def get_journal_history(user_id: int, days: int = 7, limit: int = 20) -> list[dict]:
    """Get recent journal entries for a user."""
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT content, mood_score, energy_level, tags, created_at
           FROM journal_entries
           WHERE user_id = $1
             AND created_at > NOW() - make_interval(days => $2)
           ORDER BY created_at DESC
           LIMIT $3""",
        user_id,
        days,
        limit,
    )
    return [
        {
            "content": r["content"],
            "mood_score": r["mood_score"],
            "energy_level": r["energy_level"],
            "tags": r["tags"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


async def get_journal_summary_data(user_id: int, days: int = 7) -> dict:
    """Get aggregated journal data for GPT summary generation."""
    pool = await get_pool()

    entries = await get_journal_history(user_id, days=days, limit=50)

    if not entries:
        return {"entries_count": 0, "entries": [], "avg_mood": None, "avg_energy": None}

    moods = [e["mood_score"] for e in entries if e["mood_score"]]
    energies = [e["energy_level"] for e in entries if e["energy_level"]]

    # Collect all tags with frequency
    tag_counts: dict[str, int] = {}
    for e in entries:
        for tag in (e["tags"] or []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    return {
        "entries_count": len(entries),
        "entries": entries,
        "avg_mood": round(sum(moods) / len(moods), 1) if moods else None,
        "avg_energy": round(sum(energies) / len(energies), 1) if energies else None,
        "top_tags": sorted(tag_counts.items(), key=lambda x: -x[1])[:5],
        "days": days,
    }
