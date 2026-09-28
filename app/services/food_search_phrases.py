"""Alternate FatSecret search phrases. The model names foods; it does not invent calories."""
from __future__ import annotations

import json
import logging
from typing import Optional

from openai import AsyncOpenAI

from app.config import settings
from app.services.food_catalog import normalize_alias

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(api_key=settings.openai_api_key)
_MAX_PHRASES = 4


def parse_search_phrases(raw: str, original: str) -> list[str]:
    """Up to four short names, without the phrase we already searched."""
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    items = payload.get("phrases") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    original_norm = normalize_alias(original)
    out: list[str] = []
    seen = {original_norm} if original_norm else set()
    for item in items:
        if not isinstance(item, str):
            continue
        phrase = " ".join(item.split())
        if not phrase or len(phrase) > 60:
            continue
        key = normalize_alias(phrase)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(phrase)
        if len(out) >= _MAX_PHRASES:
            break
    return out


async def expand_search_phrases(term: str, language: Optional[str]) -> list[str]:
    """Other names to search in FatSecret when the user's words found little."""
    if not settings.openai_api_key or not term.strip():
        return []
    prompt = (
        "The user is logging a food in a FatSecret-style catalogue. "
        "Reply with JSON only: {\"phrases\": [string, ...]}. "
        "Give 1 to 4 short search phrases a person would type to find this dish. "
        "Include the common Ukrainian name and the common Russian name when the food is Eastern European, "
        "and one English name. Do not repeat the original phrase. "
        "Do not include grams, calories, brands, or explanations.\n"
        f"Language hint: {language or 'uk'}. Food: {term}"
    )
    try:
        response = await _client.chat.completions.create(
            model=settings.openai_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=120,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or ""
    except Exception:
        logger.warning("Search phrase expansion failed", exc_info=True)
        return []
    return parse_search_phrases(raw, term)
