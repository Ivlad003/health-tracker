"""History-first food resolution (plan §4 FR-01..FR-06, FR-15, §14).

Order: explicit product/barcode → compatible pinned (manual) default →
confirmed compatible history (learned aliases, confirmed My Products) →
imported/recent history → external search. Explicit current attributes
(barcode, brand, fat %, raw/cooked) are checked first: a historical favourite
or a default can never override an incompatible explicit attribute.

Auto-selection (FR-05) happens only for an explicit choice, a compatible
pinned default, or a single unambiguous previously *confirmed* match — and
only when the user has not chosen "review every entry". Model confidence is
never an auto-commit criterion.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from app.services.food_catalog import display_label, extract_fat_pct, normalize_alias, tokens

logger = logging.getLogger(__name__)

TIER_EXPLICIT = 0
TIER_DEFAULT = 1
TIER_LEARNED = 2
TIER_CONFIRMED = 3
TIER_HISTORY = 4
TIER_SEARCH = 5

SOURCE_BY_TIER = {
    TIER_EXPLICIT: "explicit",
    TIER_DEFAULT: "default_rule",
    TIER_LEARNED: "learned_alias",
    TIER_CONFIRMED: "confirmed_history",
    TIER_HISTORY: "history",
    TIER_SEARCH: "search",
}

_PREP_WORDS = {
    "cooked": ("варен", "відвар", "cooked", "boiled", "готов", "запеч", "смаж", "тушк",
               "baked", "fried", "grilled", "steamed", "roasted", "на пару"),
    "raw": ("сир", "raw", "uncooked", "dry", "сух", "крупа"),
}


@dataclass
class FoodQuery:
    text: str
    name_en: str = ""
    brand: Optional[str] = None
    fat_pct: Optional[Decimal] = None
    preparation: Optional[str] = None
    barcode: Optional[str] = None
    product_id: Optional[int] = None

    @classmethod
    def from_item(cls, item: dict) -> "FoodQuery":
        text = str(item.get("name_original") or item.get("name_en") or "").strip()
        prep = item.get("preparation")
        if prep not in ("raw", "cooked", "as_sold", "prepared"):
            prep = infer_preparation(text) or infer_preparation(item.get("name_en"))
        fat = item.get("fat_pct")
        try:
            fat_d = Decimal(str(fat)) if fat not in (None, "") else extract_fat_pct(text)
        except Exception:
            fat_d = extract_fat_pct(text)
        return cls(
            text=text,
            name_en=str(item.get("name_en") or "").strip(),
            brand=(str(item.get("brand")).strip() or None) if item.get("brand") else None,
            fat_pct=fat_d,
            preparation=prep,
            barcode=item.get("barcode"),
            product_id=item.get("product_id"),
        )


def infer_preparation(text: Optional[str]) -> Optional[str]:
    norm = normalize_alias(text)
    if not norm:
        return None
    words = norm.split()
    for prep, stems in _PREP_WORDS.items():
        for stem in stems:
            if " " in stem and stem in norm:
                return prep
            if any(w.startswith(stem) for w in words):
                # "сир" (cheese) must not mean raw: require exact "сирий/сира/сире".
                if stem == "сир" and not any(w in ("сирий", "сира", "сире", "сирі", "сиру") for w in words):
                    continue
                return prep
    return None


@dataclass
class Candidate:
    tier: int
    product_id: Optional[int]
    provider: str
    external_id: Optional[str]
    label: str
    brand: Optional[str] = None
    preparation: Optional[str] = None
    barcode: Optional[str] = None
    serving_id: Optional[str] = None
    rule_id: Optional[int] = None
    confirmed_count: int = 0
    history_count: int = 0
    last_used_at: Optional[datetime] = None
    match: float = 0.0
    exact: bool = False
    uncertain: list[str] = field(default_factory=list)
    kcal_per_100g: Optional[Decimal] = None
    suggested_portion_g: Optional[Decimal] = None
    description: Optional[str] = None

    @property
    def source(self) -> str:
        return SOURCE_BY_TIER[self.tier]

    def to_json(self) -> dict:
        data = asdict(self)
        data["source"] = self.source
        for key in ("kcal_per_100g", "suggested_portion_g"):
            if data.get(key) is not None:
                data[key] = str(data[key])
        if data.get("last_used_at") is not None:
            data["last_used_at"] = data["last_used_at"].isoformat()
        return data


@dataclass
class Resolution:
    decision: str  # "auto" | "choose" | "search"
    candidates: list[Candidate]
    reason: str

    @property
    def selected(self) -> Optional[Candidate]:
        return self.candidates[0] if self.decision == "auto" and self.candidates else None


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------

def _norm_brand(value: Optional[str]) -> str:
    return normalize_alias(value).replace(" ", "")


def compatibility(query: FoodQuery, cand: Candidate) -> tuple[bool, list[str]]:
    """Return (compatible, uncertain_attributes)."""
    uncertain: list[str] = []
    if query.barcode:
        if cand.barcode and cand.barcode != query.barcode:
            return False, []
        if not cand.barcode:
            uncertain.append("barcode")
    if query.preparation in ("raw", "cooked"):
        cand_prep = cand.preparation if cand.preparation in ("raw", "cooked") else infer_preparation(cand.label)
        if cand_prep in ("raw", "cooked") and cand_prep != query.preparation:
            return False, []
        if cand_prep not in ("raw", "cooked"):
            uncertain.append("preparation")
    if query.brand:
        qb, cb = _norm_brand(query.brand), _norm_brand(cand.brand)
        if cb and qb and qb not in cb and cb not in qb:
            return False, []
        if not cb:
            label_norm = normalize_alias(cand.label).replace(" ", "")
            if qb not in label_norm:
                uncertain.append("brand")
    if query.fat_pct is not None:
        cand_fat = extract_fat_pct(cand.label) or extract_fat_pct(cand.brand)
        if cand_fat is not None and cand_fat != query.fat_pct:
            return False, []
        if cand_fat is None:
            uncertain.append("fat_pct")
    return True, uncertain


def _stem(token: str) -> str:
    return token[:5] if len(token) > 5 else token


def match_score(query_text: str, *names: Optional[str]) -> tuple[float, bool]:
    """Share of query tokens found (prefix-stem match) in any candidate name,
    and whether the normalized alias equals a name exactly."""
    q_norm = normalize_alias(query_text)
    q_tokens = [t for t in tokens(query_text) if not t.isdigit()]
    if not q_tokens:
        return 0.0, False
    best = 0.0
    exact = False
    for name in names:
        if not name:
            continue
        n_norm = normalize_alias(name)
        if n_norm and n_norm == q_norm:
            exact = True
        n_stems = {_stem(t) for t in tokens(name)}
        if not n_stems:
            continue
        hits = sum(1 for t in q_tokens if _stem(t) in n_stems)
        best = max(best, hits / len(q_tokens))
    return best, exact


def _sort_key(c: Candidate) -> tuple:
    last = c.last_used_at.timestamp() if c.last_used_at else 0.0
    return (c.tier, not c.exact, -c.match, len(c.uncertain), -c.confirmed_count, -last, -c.history_count)


def rank(query: FoodQuery, candidates: list[Candidate]) -> list[Candidate]:
    """Drop incompatible candidates, dedupe by product, order by tier then
    exactness; recency/frequency only break ties (FR-04)."""
    kept: dict[Any, Candidate] = {}
    for cand in candidates:
        ok, uncertain = compatibility(query, cand)
        if not ok:
            continue
        cand.uncertain = uncertain
        key = cand.product_id or (cand.provider, cand.external_id)
        prev = kept.get(key)
        if prev is None or _sort_key(cand) < _sort_key(prev):
            kept[key] = cand
    return sorted(kept.values(), key=_sort_key)


def decide(query: FoodQuery, ranked: list[Candidate], *, review_all: bool = False) -> Resolution:
    if not ranked:
        return Resolution("search", [], "no_history_match")
    top = ranked[0]
    if top.tier == TIER_EXPLICIT:
        return Resolution("auto", ranked[:1], "explicit")
    if review_all:
        return Resolution("choose", ranked[:3], "review_all")
    if top.tier == TIER_DEFAULT and not _explicit_attr_uncertain(query, top):
        return Resolution("auto", [top], "default_rule")
    if top.tier in (TIER_LEARNED, TIER_CONFIRMED) and top.exact and not _explicit_attr_uncertain(query, top):
        rivals = [
            c for c in ranked[1:]
            if c.tier in (TIER_LEARNED, TIER_CONFIRMED) and c.exact
        ]
        if not rivals:
            return Resolution("auto", [top], "confirmed_unique")
        return Resolution("choose", ranked[:3], "confirmed_ambiguous")
    return Resolution("choose", ranked[:3], "needs_selection")


def _explicit_attr_uncertain(query: FoodQuery, cand: Candidate) -> bool:
    """A stated brand / fat % / barcode not confirmed by the candidate blocks
    auto-selection; preparation uncertainty is accepted when the user's own
    alias matched exactly (they chose it for this very phrase before)."""
    blocking = {"brand", "fat_pct", "barcode"}
    if not cand.exact:
        blocking.add("preparation")
    return any(u in blocking for u in cand.uncertain)


# ---------------------------------------------------------------------------
# DB retrieval
# ---------------------------------------------------------------------------

async def gather_candidates(conn: Any, user_id: int, query: FoodQuery, *, limit: int = 2000) -> list[Candidate]:
    """Bounded per-user shortlist: rules + My Products (never other users')."""
    out: list[Candidate] = []
    alias = normalize_alias(query.text)

    if query.product_id:
        row = await conn.fetchrow(
            """SELECT p.*, m.display_name, m.confirmed_count, m.history_count, m.last_used_at,
                      m.preferred_serving_id
               FROM food_products p
               LEFT JOIN user_product_memberships m ON m.product_id = p.id AND m.user_id = $2
               WHERE p.id = $1 AND (p.owner_user_id IS NULL OR p.owner_user_id = $2)""",
            query.product_id, user_id,
        )
        if row:
            out.append(_cand_from_row(TIER_EXPLICIT, dict(row), match=1.0, exact=True))
        return out

    rule_rows = await conn.fetch(
        """SELECT r.id AS rule_id, r.alias_normalized, r.alias_display, r.origin AS rule_origin,
                  r.serving_id AS rule_serving_id, r.preparation AS rule_preparation,
                  r.suggested_portion_g, r.use_count,
                  p.*, m.display_name, m.confirmed_count, m.history_count, m.last_used_at,
                  m.preferred_serving_id
           FROM food_default_rules r
           JOIN food_products p ON p.id = r.product_id
           LEFT JOIN user_product_memberships m ON m.product_id = p.id AND m.user_id = r.user_id
           WHERE r.user_id = $1 AND r.enabled
             AND (p.owner_user_id IS NULL OR p.owner_user_id = $1)
             AND p.status = 'active'
             AND COALESCE(m.state, 'active') = 'active'""",
        user_id,
    )
    for row in rule_rows:
        data = dict(row)
        score, exact = match_score(query.text, data["alias_display"], data["alias_normalized"])
        if query.name_en:
            en_score, en_exact = match_score(query.name_en, data["alias_display"])
            score = max(score, en_score * 0.9)
        exact = exact or data["alias_normalized"] == alias
        if data["rule_origin"] == "manual":
            # A manual default applies to its alias (exact) or to a phrase
            # that contains the alias as a whole-word subset.
            alias_tokens = {_stem(t) for t in data["alias_normalized"].split()}
            q_tokens = {_stem(t) for t in alias.split()}
            if not (exact or (alias_tokens and alias_tokens <= q_tokens)):
                continue
            tier = TIER_DEFAULT
        else:
            if score < 1.0 and not exact:
                continue
            tier = TIER_LEARNED
        cand = _cand_from_row(tier, data, match=max(score, 1.0 if exact else score), exact=exact)
        cand.rule_id = data["rule_id"]
        cand.serving_id = data["rule_serving_id"] or cand.serving_id
        if data.get("rule_preparation"):
            cand.preparation = data["rule_preparation"]
        cand.suggested_portion_g = data.get("suggested_portion_g")
        out.append(cand)

    member_rows = await conn.fetch(
        """SELECT p.*, m.display_name, m.confirmed_count, m.history_count, m.last_used_at,
                  m.preferred_serving_id, m.preparation AS member_preparation, m.usual_portion_g
           FROM user_product_memberships m
           JOIN food_products p ON p.id = m.product_id
           WHERE m.user_id = $1 AND m.state = 'active' AND p.status = 'active'
           ORDER BY m.confirmed_count DESC, m.last_used_at DESC NULLS LAST
           LIMIT $2""",
        user_id, limit,
    )
    for row in member_rows:
        data = dict(row)
        label = display_label(data)
        score, exact = match_score(query.text, data.get("display_name"), data.get("name"), label)
        if query.name_en:
            en_score, _ = match_score(query.name_en, label, data.get("name"))
            score = max(score, en_score * 0.9)
        if score < 0.5:
            continue
        tier = TIER_CONFIRMED if (data.get("confirmed_count") or 0) > 0 else TIER_HISTORY
        cand = _cand_from_row(tier, data, match=score, exact=exact and score >= 1.0)
        if data.get("member_preparation"):
            cand.preparation = data["member_preparation"]
        cand.suggested_portion_g = data.get("usual_portion_g")
        out.append(cand)

    if query.barcode:
        rows = await conn.fetch(
            """SELECT p.*, m.display_name, m.confirmed_count, m.history_count, m.last_used_at,
                      m.preferred_serving_id
               FROM food_products p
               LEFT JOIN user_product_memberships m ON m.product_id = p.id AND m.user_id = $2
               WHERE p.barcode = $1 AND p.status = 'active'
                 AND (p.owner_user_id IS NULL OR p.owner_user_id = $2)
                 AND COALESCE(m.state, 'active') = 'active'""",
            query.barcode, user_id,
        )
        for row in rows:
            data = dict(row)
            # Exact barcode identity overrides history frequency (FR-03).
            out.append(_cand_from_row(TIER_EXPLICIT, data, match=1.0, exact=True))
    return out


def _cand_from_row(tier: int, data: dict, *, match: float, exact: bool) -> Candidate:
    return Candidate(
        tier=tier,
        product_id=data["id"],
        provider=data["provider"],
        external_id=data.get("external_id"),
        label=display_label(data),
        brand=data.get("brand") or data.get("provider_brand"),
        preparation=data.get("preparation"),
        barcode=data.get("barcode"),
        serving_id=data.get("preferred_serving_id"),
        confirmed_count=data.get("confirmed_count") or 0,
        history_count=data.get("history_count") or 0,
        last_used_at=data.get("last_used_at"),
        match=match,
        exact=exact,
    )


async def search_candidates(query: FoodQuery, *, max_results: int = 5) -> list[Candidate]:
    """External text search (FatSecret) as the last resort."""
    from app.services.fatsecret_api import search_food

    term = query.name_en or query.text
    if query.brand and query.brand.lower() not in term.lower():
        term = f"{query.brand} {term}"
    try:
        result = await search_food(term, max_results=max_results)
    except Exception:
        logger.warning("FatSecret search failed for resolver", exc_info=True)
        return []
    out = []
    for item in result.get("results", []):
        if not item.get("food_id"):
            continue
        brand = item.get("brand")
        out.append(Candidate(
            tier=TIER_SEARCH,
            product_id=None,
            provider="fatsecret",
            external_id=str(item["food_id"]),
            label=item.get("name") or term,
            brand=None if brand in (None, "", "Generic") else brand,
            description=item.get("description"),
            match=match_score(term, item.get("name"))[0],
        ))
    return out


async def resolve(
    conn: Any,
    user_id: int,
    query: FoodQuery,
    *,
    review_all: bool = False,
    allow_search: bool = True,
    history_enabled: bool = True,
) -> Resolution:
    candidates: list[Candidate] = []
    if history_enabled or query.product_id or query.barcode:
        candidates = await gather_candidates(conn, user_id, query)
        if not history_enabled:
            candidates = [c for c in candidates if c.tier == TIER_EXPLICIT]
    ranked = rank(query, candidates)
    resolution = decide(query, ranked, review_all=review_all)
    if resolution.decision == "search" and allow_search and not query.barcode:
        found = rank(query, await search_candidates(query))
        if found:
            return Resolution("choose", found[:3], "external_search")
    return resolution
