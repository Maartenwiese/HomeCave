"""Asynchronous Vivino lookup client with grounded, verify-then-link matching.

Design principles (v0.2.8):
- Only Vivino's real text search page (``/search/wines?q=`` -> SSR ``initialExploreResults``)
  is used as a candidate source. Vivino's "explore" JSON API is NOT used because
  it does not perform text search and returns popular, unrelated wines.
- Candidates must pass a deterministic, strict pre-filter (winery AND cuvée tokens).
- Nothing is ever estimated or invented: if no verified record exists, the wine stays unlinked.
- Canonical wine URLs use ``https://www.vivino.com/w/<wine_id>?year=<year>``. The legacy
  ``/wines/<id>`` form is a *vintage*-style route that redirects to unrelated wines.
"""

from __future__ import annotations

import html
import json
import logging
import re
import unicodedata
from typing import Any
from urllib.parse import quote_plus

try:
    import aiohttp
except ImportError:  # pragma: no cover - fallback when running standalone unit tests outside HA venv
    aiohttp = None  # type: ignore[assignment]

_LOGGER = logging.getLogger(__name__)

VIVINO_SEARCH_URL = "https://www.vivino.com/search/wines?q="
VIVINO_WINE_URL = "https://www.vivino.com/w/{wine_id}"

DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,nl;q=0.8",
}

# Generic words that say nothing about *which* wine it is.
WINE_STOP_WORDS: set[str] = {
    "wine", "wines", "winery", "wijn", "vin", "vino", "wein", "weingut",
    "estate", "estates", "vineyard", "vineyards", "cellar", "cellars",
    "chateau", "domaine", "domain", "bodega", "bodegas", "cantina", "tenuta",
    "casa", "quinta", "maison", "clos", "weinkellerei", "family", "wines",
    "de", "la", "le", "les", "del", "della", "di", "da", "do", "dos", "el", "du", "des",
    "van", "von", "der", "den", "und", "and", "et", "e", "y", "the", "of",
    "doc", "docg", "igt", "aop", "aoc", "dop", "do", "ava", "wo", "qba",
    "red", "white", "rose", "rouge", "blanc", "rosso", "bianco", "tinto", "blanco",
    "vintage", "bottle", "ml", "cl",
}

# Colour words are meaningful for cuvée disambiguation (Pavillon Rouge vs Blanc), so they
# are removed from the stop-list when comparing *cuvée names*.
COLOUR_WORDS: set[str] = {"red", "white", "rose", "rouge", "blanc", "rosso", "bianco", "tinto", "blanco"}

COMMON_VARIETALS: set[str] = {
    "cabernet sauvignon", "cabernet franc", "merlot", "pinot noir", "pinot grigio", "pinot gris",
    "pinot blanc", "pinotage", "syrah", "shiraz", "grenache", "garnacha", "tempranillo",
    "sangiovese", "nebbiolo", "barbera", "malbec", "carmenere", "zinfandel", "primitivo",
    "chardonnay", "sauvignon blanc", "chenin blanc", "riesling", "gewurztraminer",
    "viognier", "albarino", "gruner veltliner", "semillon", "muscat", "moscato",
    "verdejo", "vermentino", "torrontes", "mourvedre", "monastrell", "petit verdot",
    "touriga nacional", "gamay", "montepulciano", "nero d avola", "aglianico", "corvina",
}

# Grape words on a Vivino name (e.g. "Bin 389 Cabernet - Shiraz") describe the blend, not a
# different cuvée, so they don't count as "extra" tokens when deciding exactness.
VARIETAL_TOKENS: set[str] = {tok for v in COMMON_VARIETALS for tok in v.split()} - {"blanc", "noir", "gris"}


def normalize_text(s: str) -> str:
    """Lowercase, strip accents, and collapse punctuation to spaces."""
    decomposed = unicodedata.normalize("NFKD", s or "")
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    ascii_only = ascii_only.replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", " ", ascii_only.lower()).strip()


def clean_tokens(s: str) -> list[str]:
    """Extract normalized alphanumeric words with length > 1."""
    return [t for t in normalize_text(s).split() if len(t) > 1]


def _is_year_token(t: str) -> bool:
    return len(t) == 4 and t.isdigit() and 1800 <= int(t) <= 2100


def clean_distinctive_tokens(s: str, *, keep_colours: bool = False) -> list[str]:
    """Extract normalized tokens excluding generic wine stop words and vintage years.

    Non-year numbers are kept because they identify cuvées (e.g. "Bin 389" vs "Bin 707").
    """
    stop = WINE_STOP_WORDS - COLOUR_WORDS if keep_colours else WINE_STOP_WORDS
    tokens = [t for t in normalize_text(s).split() if len(t) > 1 or t.isdigit()]
    return [t for t in tokens if t not in stop and not _is_year_token(t)]


def is_varietal_name(name: str) -> bool:
    """Return True when a wine name is essentially just a grape varietal."""
    norm = normalize_text(name)
    return norm in COMMON_VARIETALS


def build_wine_url(wine_id: int | str | None, year: int | None = None) -> str:
    """Build the canonical Vivino wine URL (``/w/<id>``)."""
    if not wine_id:
        return ""
    url = VIVINO_WINE_URL.format(wine_id=wine_id)
    return f"{url}?year={year}" if year else url


def parse_vivino_wine_id(url_or_id: str | int | None) -> int | None:
    """Extract a Vivino *wine* id from a ``/w/<id>`` URL or a bare numeric id."""
    if url_or_id is None:
        return None
    raw = str(url_or_id).strip()
    if raw.isdigit():
        return int(raw)
    match = re.search(r"/w/(\d+)", raw)
    if match:
        return int(match.group(1))
    return None


def parse_year_from_url(url: str) -> int | None:
    """Extract ``?year=YYYY`` from a Vivino URL."""
    match = re.search(r"[?&]year=(\d{4})", url or "")
    return int(match.group(1)) if match else None


def winery_matches(target_winery: str, cand_winery: str, cand_name: str = "") -> bool:
    """Strict producer check: every distinctive target winery token appears in candidate
    winery (or in the candidate's name, for wines where the producer is part of the title)."""
    t_tokens = set(clean_distinctive_tokens(target_winery))
    if not t_tokens:
        # Winery consisted only of generic words (e.g. "Château") -> fall back to all tokens.
        t_tokens = set(clean_tokens(target_winery))
    if not t_tokens:
        return False
    cand_tokens = set(clean_tokens(cand_winery)) | set(clean_tokens(cand_name))
    return t_tokens.issubset(cand_tokens)


def cuvee_matches(target_name: str, cand_name: str, cand_winery: str = "") -> bool:
    """Strict cuvée check: every distinctive target name token (colours kept) appears in the
    candidate name (or winery, when the scanned 'name' is actually part of the producer)."""
    t_tokens = set(clean_distinctive_tokens(target_name, keep_colours=True))
    if not t_tokens:
        t_tokens = set(clean_tokens(target_name))
    if not t_tokens:
        return False
    cand_tokens = set(clean_tokens(cand_name)) | set(clean_tokens(cand_winery))
    return t_tokens.issubset(cand_tokens)


def evaluate_candidate(
    candidate: dict[str, Any],
    target_winery: str,
    target_name: str,
) -> dict[str, Any]:
    """Deterministic pre-filter. Returns {'passes': bool, 'exact': bool, 'reason': str}.

    - With winery AND name: both must match strictly.
    - With only one of them: that one must match strictly (never auto-linked as exact).
    - 'exact' means the candidate's distinctive cuvée tokens add nothing beyond the
      target (i.e. not a different cuvée that merely contains our words).
    """
    tw = (target_winery or "").strip()
    tn = (target_name or "").strip()
    cw = str(candidate.get("winery") or "")
    cn = str(candidate.get("name") or "")

    if not tw and not tn:
        return {"passes": False, "exact": False, "reason": "no_target"}

    w_ok = winery_matches(tw, cw, cn) if tw else False
    n_ok = cuvee_matches(tn, cn, cw) if tn else False

    if tw and tn:
        passes = w_ok and n_ok
    elif tw:
        passes = w_ok
    else:
        passes = n_ok

    if not passes:
        reason = []
        if tw and not w_ok:
            reason.append("winery_mismatch")
        if tn and not n_ok:
            reason.append("cuvee_mismatch")
        return {"passes": False, "exact": False, "reason": ",".join(reason) or "mismatch"}

    exact = False
    if tw and tn:
        target_all = set(clean_distinctive_tokens(f"{tw} {tn}", keep_colours=True))
        cand_name_tokens = set(clean_distinctive_tokens(cn, keep_colours=True))
        cand_winery_tokens = set(clean_distinctive_tokens(cw))
        extra = cand_name_tokens - target_all - cand_winery_tokens - VARIETAL_TOKENS
        exact = len(extra) == 0
    return {"passes": True, "exact": exact, "reason": "ok"}


def is_vivino_candidate_similar(
    candidate: dict[str, Any],
    target_winery: str,
    target_name: str,
) -> bool:
    """Backward-compatible boolean wrapper around the strict pre-filter."""
    return bool(evaluate_candidate(candidate, target_winery, target_name)["passes"])


def _parse_record(match: dict[str, Any], target_vintage: int | None = None) -> dict[str, Any] | None:
    """Convert a Vivino SSR explore record into a normalized candidate dict."""
    if not isinstance(match, dict):
        return None
    vintage_obj = match.get("vintage") or {}
    wine_obj = vintage_obj.get("wine") or match.get("wine") or {}
    winery_obj = wine_obj.get("winery") or {}
    stats_obj = vintage_obj.get("statistics") or {}
    wine_stats = wine_obj.get("statistics") or {}
    region_obj = wine_obj.get("region") or {}
    country_obj = region_obj.get("country") or {}
    image_obj = vintage_obj.get("image") or wine_obj.get("image") or {}
    price_obj = match.get("price") or {}

    wine_id = wine_obj.get("id")
    if not wine_id:
        return None

    year_raw = vintage_obj.get("year")
    parsed_year: int | None = None
    if year_raw and str(year_raw).isdigit():
        y_val = int(year_raw)
        if 1800 <= y_val <= 2100:
            parsed_year = y_val

    # Vintage-specific rating when present; otherwise the wine-level aggregate.
    rating_raw = stats_obj.get("ratings_average")
    count_raw = stats_obj.get("ratings_count")
    if not rating_raw:
        rating_raw = stats_obj.get("wine_ratings_average") or wine_stats.get("ratings_average")
        count_raw = stats_obj.get("wine_ratings_count") or wine_stats.get("ratings_count")
    rating_val = round(float(rating_raw), 2) if rating_raw else None
    ratings_count = int(count_raw) if count_raw is not None else None

    price_raw = price_obj.get("amount") if isinstance(price_obj, dict) else None
    price_val = round(float(price_raw), 2) if price_raw is not None else None

    img_location = str(
        (image_obj.get("variations") or {}).get("bottle_medium")
        or image_obj.get("location")
        or ""
    ).strip()
    if img_location.startswith("//"):
        img_location = f"https:{img_location}"

    return {
        "vivino_wine_id": int(wine_id),
        "winery": str(winery_obj.get("name") or "").strip(),
        "name": str(wine_obj.get("name") or "").strip(),
        "vintage": parsed_year,
        "region": str(region_obj.get("name") or "").strip(),
        "country": str(country_obj.get("name") or "").strip(),
        "vivino_rating": rating_val,
        "vivino_ratings_count": ratings_count,
        "vivino_url": build_wine_url(wine_id, parsed_year),
        "vivino_price_estimate": price_val,
        "vivino_image_url": img_location,
        "rating_source": "vivino",
    }


def _rank_key(cand: dict[str, Any], target_vintage: int | None) -> tuple:
    vintage_match = 1 if target_vintage and cand.get("vintage") == target_vintage else 0
    return (
        1 if cand.get("prefilter_exact") else 0,
        vintage_match,
        cand.get("vivino_ratings_count") or 0,
    )


def filter_and_rank_records(
    records: list[dict[str, Any]],
    *,
    target_winery: str = "",
    target_name: str = "",
    target_vintage: int | None = None,
    max_candidates: int = 5,
) -> list[dict[str, Any]]:
    """Parse, strictly pre-filter, de-duplicate per wine id, and rank Vivino records.

    One candidate per Vivino wine id is kept, preferring the record whose vintage equals
    the scanned vintage.
    """
    by_wine: dict[int, dict[str, Any]] = {}
    for rec in records:
        cand = _parse_record(rec, target_vintage)
        if cand is None:
            continue
        verdict = evaluate_candidate(cand, target_winery, target_name)
        if not verdict["passes"]:
            _LOGGER.debug(
                "Vivino pre-filter rejected '%s - %s' (%s)",
                cand["winery"], cand["name"], verdict["reason"],
            )
            continue
        cand["prefilter_exact"] = verdict["exact"]
        wid = cand["vivino_wine_id"]
        existing = by_wine.get(wid)
        if existing is None:
            by_wine[wid] = cand
        elif target_vintage and cand.get("vintage") == target_vintage and existing.get("vintage") != target_vintage:
            by_wine[wid] = cand

    # When the scanned vintage is known but the kept record is another vintage, point the
    # URL at the scanned year and do not claim a vintage-specific rating.
    for cand in by_wine.values():
        if target_vintage and cand.get("vintage") != target_vintage:
            cand["vivino_url"] = build_wine_url(cand["vivino_wine_id"], target_vintage)

    ranked = sorted(by_wine.values(), key=lambda c: _rank_key(c, target_vintage), reverse=True)
    return ranked[:max_candidates]


def extract_search_records(html_text: str) -> list[dict[str, Any]]:
    """Extract raw ``initialExploreResults.records`` from Vivino search page HTML."""
    for prop_str in re.findall(r'data-ssr-props="([^"]+)"', html_text or ""):
        try:
            data = json.loads(html.unescape(prop_str))
        except (ValueError, TypeError) as err:
            _LOGGER.debug("Failed parsing SSR props chunk: %s", err)
            continue
        if not isinstance(data, dict):
            continue
        results = data.get("initialExploreResults") or data.get("exploreRecords") or {}
        records = results.get("records") or results.get("matches") or []
        if records:
            return list(records)
    return []


def parse_vivino_search_html(
    html_text: str,
    *,
    target_winery: str = "",
    target_name: str = "",
    target_vintage: int | None = None,
    max_candidates: int = 5,
) -> list[dict[str, Any]]:
    """Parse and strictly filter server-side rendered Vivino search results."""
    return filter_and_rank_records(
        extract_search_records(html_text),
        target_winery=target_winery,
        target_name=target_name,
        target_vintage=target_vintage,
        max_candidates=max_candidates,
    )


def parse_vivino_explore_payload(
    payload: dict[str, Any],
    *,
    target_winery: str = "",
    target_name: str = "",
    target_vintage: int | None = None,
    max_candidates: int = 5,
) -> list[dict[str, Any]]:
    """Parse a records/matches payload (same record shape as the search SSR)."""
    records = (
        payload.get("records")
        or (payload.get("explore_vintage") or {}).get("matches")
        or payload.get("matches")
        or []
    )
    return filter_and_rank_records(
        records,
        target_winery=target_winery,
        target_name=target_name,
        target_vintage=target_vintage,
        max_candidates=max_candidates,
    )


def build_search_queries(winery: str, name: str, vintage: int | None = None) -> list[str]:
    """Build up to three search query variants from *printed* label fields."""
    w = (winery or "").strip()
    n = (name or "").strip()
    queries: list[str] = []

    def _add(q: str) -> None:
        q = " ".join(q.split())
        if q and q.lower() not in {x.lower() for x in queries}:
            queries.append(q)

    if w and n:
        if normalize_text(w) in normalize_text(n):
            _add(n)
        elif normalize_text(n) in normalize_text(w):
            _add(w)
        else:
            _add(f"{w} {n}")
        distinct = " ".join(clean_distinctive_tokens(f"{w} {n}", keep_colours=True))
        _add(distinct)
    elif w:
        _add(w)
    elif n:
        _add(n)
    return queries[:3]


def parse_vivino_wine_page(html_text: str) -> dict[str, Any] | None:
    """Parse a Vivino wine page's schema.org Product JSON-LD (brand, name, rating, price)."""
    for block in re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html_text or "", re.S
    ):
        try:
            data = json.loads(block.strip())
        except (ValueError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict) or item.get("@type") != "Product":
                continue
            brand = item.get("brand") or {}
            agg = item.get("aggregateRating") or {}
            offers = item.get("offers") or {}
            url = str(item.get("url") or "")
            full_name = str(item.get("name") or "").strip()
            year = parse_year_from_url(url)
            winery = str(brand.get("name") or "").strip() if isinstance(brand, dict) else ""
            # JSON-LD name is "<year> <winery> <cuvée>" - strip the year and winery prefix.
            cuvee = re.sub(r"^\d{4}\s+", "", full_name)
            if winery and cuvee.lower().startswith(winery.lower()):
                cuvee = cuvee[len(winery):].strip()
            try:
                rating = round(float(agg.get("ratingValue")), 2) if agg.get("ratingValue") else None
            except (ValueError, TypeError):
                rating = None
            try:
                count = int(agg.get("ratingCount")) if agg.get("ratingCount") else None
            except (ValueError, TypeError):
                count = None
            try:
                price = round(float(offers.get("price")), 2) if isinstance(offers, dict) and offers.get("price") else None
            except (ValueError, TypeError):
                price = None
            images = item.get("image") or []
            image = images[0] if isinstance(images, list) and images else (images if isinstance(images, str) else "")
            wine_id = parse_vivino_wine_id(url)
            return {
                "vivino_wine_id": wine_id,
                "winery": winery,
                "name": cuvee,
                "vintage": year,
                "vivino_rating": rating,
                "vivino_ratings_count": count,
                "vivino_price_estimate": price,
                "vivino_image_url": image,
                "vivino_url": build_wine_url(wine_id, year) if wine_id else "",
                "rating_source": "vivino",
            }
    return None


def _client_errors() -> tuple[type[BaseException], ...]:
    if aiohttp is not None:
        return (aiohttp.ClientError, ValueError, TimeoutError, OSError)
    return (ValueError, TimeoutError, OSError)


def _timeout(seconds: float) -> Any:
    return aiohttp.ClientTimeout(total=seconds) if aiohttp is not None else seconds


async def async_lookup_vivino(
    session: aiohttp.ClientSession,
    *,
    winery: str,
    name: str,
    vintage: int | None = None,
    country_code: str = "NL",
    currency_code: str = "EUR",
    max_candidates: int = 5,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    """Search Vivino (real text search only) and return strictly pre-filtered candidates.

    Returns ``{"status", "candidates", "search_url", "queries"}``. ``status`` is one of
    ``ok``, ``no_match``, ``empty_query``, ``http_<code>``, ``network_error``. This function
    never chooses a "best match"; the caller decides the confidence tier.
    """
    queries = build_search_queries(winery, name, vintage)
    search_url = f"{VIVINO_SEARCH_URL}{quote_plus(queries[0])}" if queries else ""
    if not queries:
        return {"status": "empty_query", "candidates": [], "search_url": "", "queries": []}

    all_records: list[dict[str, Any]] = []
    last_status = "no_match"
    errors = _client_errors()
    for query in queries:
        url = f"{VIVINO_SEARCH_URL}{quote_plus(query)}"
        try:
            async with session.get(url, headers=DEFAULT_HEADERS, timeout=_timeout(timeout_seconds)) as resp:
                if resp.status in (403, 429):
                    _LOGGER.info("Vivino search returned HTTP %s for '%s'", resp.status, query)
                    last_status = f"http_{resp.status}"
                    break
                if resp.status != 200:
                    last_status = f"http_{resp.status}"
                    continue
                all_records.extend(extract_search_records(await resp.text()))
        except errors as err:
            _LOGGER.debug("Vivino search error for '%s': %s", query, err)
            last_status = "network_error"
            continue
        # Stop early once the first query yields strictly matching candidates.
        if filter_and_rank_records(all_records, target_winery=winery, target_name=name, target_vintage=vintage):
            break

    candidates = filter_and_rank_records(
        all_records,
        target_winery=winery,
        target_name=name,
        target_vintage=vintage,
        max_candidates=max_candidates,
    )
    return {
        "status": "ok" if candidates else last_status,
        "candidates": candidates,
        "search_url": search_url,
        "queries": queries,
    }


async def async_fetch_vivino_wine(
    session: aiohttp.ClientSession,
    *,
    url_or_id: str | int,
    vintage: int | None = None,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    """Fetch an exact Vivino wine page by ``/w/<id>`` URL or wine id and parse it.

    Raises ValueError when the input is not a Vivino wine URL/id or the page can't be parsed.
    """
    wine_id = parse_vivino_wine_id(url_or_id)
    if wine_id is None:
        raise ValueError(
            "Not a Vivino wine link. Paste a URL containing '/w/<number>', e.g. https://www.vivino.com/w/1652"
        )
    year = vintage or parse_year_from_url(str(url_or_id))
    url = build_wine_url(wine_id, year)
    async with session.get(url, headers=DEFAULT_HEADERS, timeout=_timeout(timeout_seconds)) as resp:
        if resp.status != 200:
            raise ValueError(f"Vivino returned HTTP {resp.status} for {url}")
        page = parse_vivino_wine_page(await resp.text())
    if not page:
        raise ValueError(f"Could not read wine details from {url}")
    if page.get("vivino_wine_id") and page["vivino_wine_id"] != wine_id:
        raise ValueError(
            f"Vivino redirected wine {wine_id} to a different wine ({page['vivino_wine_id']}); not linking."
        )
    page["vivino_wine_id"] = wine_id
    page["vivino_url"] = build_wine_url(wine_id, page.get("vintage") or year)
    return page
