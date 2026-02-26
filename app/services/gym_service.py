from __future__ import annotations

import json
import logging
from decimal import Decimal

from app.database import get_pool

logger = logging.getLogger(__name__)


async def log_exercises(user_id: int, exercises: list[dict]) -> list[dict]:
    """Log gym exercises to DB. Returns list with previous entry for comparison."""
    pool = await get_pool()
    logged = []

    for ex in exercises:
        name = ex.get("name_original", ex.get("name_en", ""))
        key = ex.get("exercise_key", "")
        weight = ex.get("weight_kg")
        sets = ex.get("sets")
        reps = ex.get("reps")
        rpe = ex.get("rpe")
        notes = ex.get("notes")
        set_details = ex.get("set_details")

        await pool.execute(
            """INSERT INTO gym_exercises
                   (user_id, exercise_name, exercise_key, weight_kg, sets, reps,
                    rpe, notes, set_details)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
            user_id,
            name,
            key,
            Decimal(str(weight)) if weight is not None else None,
            sets,
            reps,
            Decimal(str(rpe)) if rpe is not None else None,
            notes,
            json.dumps(set_details) if set_details else None,
        )

        # Fetch previous entry for the same exercise (the one before the just-inserted one)
        prev_row = await pool.fetchrow(
            """SELECT exercise_name, weight_kg, sets, reps, rpe, created_at
               FROM gym_exercises
               WHERE user_id = $1 AND exercise_key = $2
               ORDER BY created_at DESC
               OFFSET 1 LIMIT 1""",
            user_id,
            key,
        )
        prev = None
        if prev_row:
            prev = {
                "weight_kg": float(prev_row["weight_kg"]) if prev_row["weight_kg"] else None,
                "sets": prev_row["sets"],
                "reps": prev_row["reps"],
                "rpe": float(prev_row["rpe"]) if prev_row["rpe"] else None,
                "date": prev_row["created_at"].strftime("%d.%m"),
            }

        logged.append({
            "name": name,
            "weight_kg": weight,
            "sets": sets,
            "reps": reps,
            "prev": prev,
        })

        logger.info(
            "Gym exercise logged: user_id=%s key=%s weight=%s sets=%s reps=%s",
            user_id, key, weight, sets, reps,
        )

    return logged


async def get_last_exercise(user_id: int, exercise_key: str) -> dict | None:
    """Get the most recent entry for a specific exercise."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """SELECT exercise_name, exercise_key, weight_kg, sets, reps, rpe,
                  notes, set_details, created_at
           FROM gym_exercises
           WHERE user_id = $1 AND exercise_key = $2
           ORDER BY created_at DESC LIMIT 1""",
        user_id,
        exercise_key,
    )
    if not row:
        return None
    return {
        "name": row["exercise_name"],
        "exercise_key": row["exercise_key"],
        "weight_kg": float(row["weight_kg"]) if row["weight_kg"] else None,
        "sets": row["sets"],
        "reps": row["reps"],
        "rpe": float(row["rpe"]) if row["rpe"] else None,
        "notes": row["notes"],
        "set_details": json.loads(row["set_details"]) if row["set_details"] else None,
        "date": row["created_at"].strftime("%d.%m"),
    }


async def get_exercise_progress(user_id: int, exercise_key: str, limit: int = 10) -> list[dict]:
    """Get last N entries for an exercise, returned oldest-first for progression view."""
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT exercise_name, weight_kg, sets, reps, rpe, created_at
           FROM gym_exercises
           WHERE user_id = $1 AND exercise_key = $2
           ORDER BY created_at DESC LIMIT $3""",
        user_id,
        exercise_key,
        limit,
    )
    # Reverse to oldest-first for progression display
    return [
        {
            "name": r["exercise_name"],
            "weight_kg": float(r["weight_kg"]) if r["weight_kg"] else None,
            "sets": r["sets"],
            "reps": r["reps"],
            "rpe": float(r["rpe"]) if r["rpe"] else None,
            "created_at": r["created_at"],
        }
        for r in reversed(rows)
    ]
