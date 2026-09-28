"""LLM search phrases are names only, and the original query is not repeated."""
import json

from app.services.food_search_phrases import parse_search_phrases


def test_parse_search_phrases_keeps_distinct_names():
    raw = json.dumps({"phrases": ["борщ зелений", "зелений борщ", "nettle soup", "", 3, "x" * 80]})
    assert parse_search_phrases(raw, "зелений борщ") == ["борщ зелений", "nettle soup"]


def test_parse_search_phrases_rejects_non_json():
    assert parse_search_phrases("борщ", "зелений борщ") == []
