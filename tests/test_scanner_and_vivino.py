"""Unit tests for Gemini label scanner, grounded Vivino matching, and Coordinator.

Vivino fixtures in tests/fixtures/vivino_search_fixtures.json were captured from the live
https://www.vivino.com/search/wines?q=<query> page (data-ssr-props records, trimmed) on
2026-10-25, so the matcher is exercised against real Vivino result sets.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import html
import json
from pathlib import Path
import tempfile
from typing import Any
import unittest
from unittest.mock import MagicMock, patch

from custom_components.homecave.coordinator import WineInventoryCoordinator
from custom_components.homecave.scanner import (
    ClaudeScannerError,
    GeminiScannerError,
    LLMScannerError,
    _extract_json_from_text,
    _send_claude_messages_request,
    async_enrich_vivino_bottle,
    async_enrich_vivino_bottle_claude,
    async_judge_vivino_candidates,
    async_judge_vivino_candidates_claude,
    async_research_wine,
    async_research_wine_with_claude,
    async_scan_bottle_label,
    async_scan_bottle_label_claude,
    async_sommelier_dish_pairing,
    async_sommelier_dish_pairing_claude,
    grounded_search_fields,
    parse_image_data_uri,
    save_bottle_cover_image,
)
from custom_components.homecave.vivino import (
    build_search_queries,
    evaluate_candidate,
    filter_and_rank_records,
    parse_vivino_search_html,
    parse_vivino_wine_id,
    parse_vivino_wine_page,
)

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "vivino_search_fixtures.json").read_text(encoding="utf-8")
)["queries"]


def _search_html(records: list[dict[str, Any]]) -> str:
    """Wrap records in the same SSR envelope the live Vivino search page uses."""
    props = html.escape(json.dumps({"initialExploreResults": {"records": records}}))
    return f'<html><body><div data-ssr-props="{props}"></div></body></html>'


def _gemini(payload: dict[str, Any]) -> dict[str, Any]:
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]}


class _StubHttpResponse:
    """Async HTTP response helper."""

    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body if isinstance(self._body, str) else json.dumps(self._body)

    async def json(self, content_type: str | None = None) -> Any:
        return self._body

    async def __aenter__(self) -> _StubHttpResponse:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _StubHttpSession:
    """Routes Gemini POSTs (label extraction vs. candidate judge) and Vivino GETs."""

    def __init__(
        self,
        gemini_payload: dict[str, Any],
        vivino_status: int = 200,
        vivino_html: str = "",
        judge_payload: dict[str, Any] | None = None,
    ) -> None:
        self.gemini_payload = gemini_payload
        self.vivino_status = vivino_status
        self.vivino_html = vivino_html
        self.judge_payload = judge_payload
        self.get_urls: list[str] = []
        self.judge_prompts: list[str] = []
        self.post_headers: list[dict[str, Any]] = []
        self.post_params: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _StubHttpResponse:
        self.post_headers.append(dict(kwargs.get("headers") or {}))
        self.post_params.append(dict(kwargs.get("params") or {}))
        body = kwargs.get("json") or {}
        text = "".join(
            str(p.get("text") or "")
            for c in body.get("contents", [])
            for p in c.get("parts", [])
        )
        if "listed Vivino database records" in text:
            self.judge_prompts.append(text)
            if self.judge_payload is None:
                return _StubHttpResponse(500, "judge unavailable")
            return _StubHttpResponse(200, _gemini(self.judge_payload))
        return _StubHttpResponse(200, self.gemini_payload)

    def get(self, url: str, **kwargs: Any) -> _StubHttpResponse:
        self.get_urls.append(url)
        return _StubHttpResponse(self.vivino_status, self.vivino_html)


class _StubHassConfig:
    def __init__(self, config_dir: Path) -> None:
        self.config_dir = str(config_dir)


class _StubHass:
    def __init__(self, config_dir: Path) -> None:
        self.config = _StubHassConfig(config_dir)

    async def async_add_executor_job(self, func: Any, *args: Any) -> Any:
        return func(*args)


TIGNANELLO_LABEL = {
    "winery": "Antinori",
    "name": "Tignanello",
    "vintage": 2019,
    "wine_type": "red",
    "varietal": "Sangiovese, Cabernet Sauvignon",
    "region": "Toscana",
    "country": "Italy",
    "alcohol_pct": 14.0,
    "bottle_size_ml": 750,
    "drink_from_year": 2024,
    "drink_until_year": 2036,
    "drink_window_note": "Decant 90 minutes before serving",
    "food_pairings": ["Bistecca alla Fiorentina"],
    "label_summary": "Super Tuscan blend.",
    "label_text_verbatim": "TIGNANELLO\nToscana IGT\n2019\nMARCHESI ANTINORI",
    "winery_printed": True,
    "name_printed": True,
    "vintage_printed": True,
}


class TestScannerVivinoAndCoordinator(unittest.TestCase):
    """Image parsing, grounded Vivino matching, and coordinator workflows."""

    def test_parse_data_uri_and_save_cover(self) -> None:
        """Verify base64 data URI parsing and cover thumbnail saving."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_bytes = b"\xff\xd8\xff\xe0JFIF-test-jpeg"
            b64_uri = "data:image/jpeg;base64," + base64.b64encode(sample_bytes).decode(
                "ascii"
            )
            mime, raw = parse_image_data_uri(b64_uri)
            self.assertEqual(mime, "image/jpeg")
            self.assertEqual(raw, sample_bytes)

            local_url = save_bottle_cover_image(Path(tmpdir), raw, mime)
            self.assertTrue(local_url.startswith("/local/homecave/covers/bottle_"))
            self.assertTrue(local_url.endswith(".jpg"))

    # ---------- deterministic pre-filter against real Vivino result sets ----------

    def test_prefilter_exact_match_tignanello(self) -> None:
        cands = filter_and_rank_records(
            FIXTURES["Antinori Tignanello"], target_winery="Antinori", target_name="Tignanello", target_vintage=2019
        )
        self.assertTrue(cands)
        top = cands[0]
        self.assertEqual((top["winery"], top["name"], top["vintage"]), ("Antinori", "Tignanello", 2019))
        self.assertTrue(top["prefilter_exact"])
        self.assertEqual(top["vivino_url"], "https://www.vivino.com/w/1652?year=2019")
        # Other Antinori cuvées containing "Tignanello" (e.g. Solaia) are NOT exact.
        self.assertEqual(sum(1 for c in cands if c["prefilter_exact"]), 1)

    def test_prefilter_rejects_different_cuvee_same_producer(self) -> None:
        # Real Penfolds result set contains Grange but no Bin 389 -> must return nothing.
        self.assertEqual(
            filter_and_rank_records(FIXTURES["Penfolds Grange"], target_winery="Penfolds", target_name="Bin 389"),
            [],
        )
        # Pavillon Rouge must not match Pavillon Blanc; accents are normalized.
        cands = filter_and_rank_records(
            FIXTURES["Chateau Margaux Pavillon Rouge"], target_winery="Château Margaux", target_name="Pavillon Rouge"
        )
        self.assertEqual([c["name"] for c in cands], ["Pavillon Rouge du Château Margaux"])

    def test_prefilter_numbered_cuvees(self) -> None:
        # Real Penfolds names seen on live Vivino search ("penfolds bin") on 2026-10-25.
        live_names = [
            "Reserve Bin A Chardonnay",
            "Bin 707 Cabernet Sauvignon",
            "Bin 169 Cabernet Sauvignon",
            "Yattarna Chardonnay (BIN 144)",
            "Bin 389 Cabernet - Shiraz",
        ]
        passing = [
            n for n in live_names
            if evaluate_candidate({"winery": "Penfolds", "name": n}, "Penfolds", "Bin 389")["passes"]
        ]
        self.assertEqual(passing, ["Bin 389 Cabernet - Shiraz"])

    def test_prefilter_rejects_unrelated_popular_wine(self) -> None:
        self.assertEqual(
            filter_and_rank_records(FIXTURES["Antinori Tignanello"], target_winery="Casella", target_name="Yellow Tail"),
            [],
        )
        self.assertFalse(
            evaluate_candidate({"winery": "Casella Wines", "name": "Yellow Tail Shiraz"}, "Antinori", "Tignanello")["passes"]
        )

    def test_prefilter_varietal_name_is_ambiguous(self) -> None:
        cands = filter_and_rank_records(
            FIXTURES["Kleine Zalze Chenin Blanc"], target_winery="Kleine Zalze", target_name="Chenin Blanc"
        )
        # Several Kleine Zalze Chenin cuvées, none exact -> must require user confirmation.
        self.assertGreater(len(cands), 1)
        self.assertFalse(any(c["prefilter_exact"] for c in cands))
        self.assertTrue(all(c["winery"] == "Kleine Zalze" for c in cands))

    def test_empty_vivino_result_and_query_building(self) -> None:
        self.assertEqual(FIXTURES["zzqx nonexistent wine 9999"], [])
        self.assertEqual(parse_vivino_search_html(_search_html([]), target_winery="X", target_name="Y"), [])
        self.assertEqual(build_search_queries("Château Margaux", "Pavillon Rouge")[0], "Château Margaux Pavillon Rouge")
        self.assertEqual(build_search_queries("", ""), [])

    def test_wine_id_and_wine_page_parsing(self) -> None:
        self.assertEqual(parse_vivino_wine_id("https://www.vivino.com/nl/antinori-tuscany-tignanello/w/1652?year=2019"), 1652)
        self.assertEqual(parse_vivino_wine_id("1652"), 1652)
        self.assertIsNone(parse_vivino_wine_id("https://www.vivino.com/wines/1652"))
        # JSON-LD block shape as served on the live Tignanello page (2026-10-25).
        ld = {
            "@context": "https://schema.org/", "@type": "Product", "name": "2019 Antinori Tignanello",
            "url": "https://www.vivino.com/nl/antinori-tuscany-tignanello/w/1652?year=2019",
            "image": ["https://images.vivino.com/thumbs/LtqnB-H2QFmN6ixL3FQcdw_pl_375x500.png"],
            "brand": {"@type": "Brand", "name": "Antinori"},
            "offers": {"@type": "Offer", "priceCurrency": "EUR", "price": "286.21"},
            "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.6", "ratingCount": "8672"},
        }
        page = parse_vivino_wine_page(f'<script type="application/ld+json">{json.dumps(ld)}</script>')
        assert page is not None
        self.assertEqual((page["winery"], page["name"], page["vintage"]), ("Antinori", "Tignanello", 2019))
        self.assertEqual(page["vivino_rating"], 4.6)
        self.assertEqual(page["vivino_url"], "https://www.vivino.com/w/1652?year=2019")

    def test_grounded_search_fields_drop_inferred_values(self) -> None:
        g = grounded_search_fields(TIGNANELLO_LABEL)
        self.assertEqual((g["winery"], g["name"], g["vintage"]), ("Antinori", "Tignanello", 2019))
        inferred = dict(TIGNANELLO_LABEL, name="Riserva Speciale", name_printed=True)
        self.assertEqual(grounded_search_fields(inferred)["name"], "")  # not in transcription
        flagged = dict(TIGNANELLO_LABEL, winery_printed=False, vintage_printed=False)
        g2 = grounded_search_fields(flagged)
        self.assertEqual(g2["winery"], "")
        self.assertIsNone(g2["vintage"])

    # ---------- coordinator tiers ----------

    def _make_coordinator(self, tmp_path: Path, session: _StubHttpSession) -> WineInventoryCoordinator:
        return WineInventoryCoordinator(
            hass=_StubHass(tmp_path),
            entry_id="test_entry",
            config={"gemini_api_key": "test-key", "gemini_model": "gemini-flash-lite", "currency": "EUR"},
            session_getter=lambda _h: session,
        )

    def _scan(self, session: _StubHttpSession) -> tuple[dict[str, Any], WineInventoryCoordinator, Path]:
        result: dict[str, Any] = {}

        async def _run() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                coord = self._make_coordinator(Path(tmpdir), session)
                await coord.async_initialize()
                img = "data:image/jpeg;base64," + base64.b64encode(
                    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"
                ).decode("ascii")
                result["scan"] = await coord.async_scan_bottle(image_data=img)
                if result["scan"]["vivino_confidence"] == "high":
                    saved = await coord.async_add_wine({**result["scan"]["extracted"], "quantity": 2})
                    result["saved"] = saved
                    result["export"] = await coord.async_export_inventory("csv")

        asyncio.run(_run())
        return result, None, None  # type: ignore[return-value]

    def test_tier_high_auto_links_only_when_judge_agrees(self) -> None:
        session = _StubHttpSession(
            _gemini(TIGNANELLO_LABEL),
            vivino_html=_search_html(FIXTURES["Antinori Tignanello"]),
            judge_payload={"choice": "0", "reason": "Label reads Tignanello by Antinori."},
        )
        res, _, _ = self._scan(session)
        scan = res["scan"]
        self.assertEqual(scan["vivino_confidence"], "high")
        self.assertEqual(scan["extracted"]["vivino_url"], "https://www.vivino.com/w/1652?year=2019")
        self.assertEqual(scan["extracted"]["vivino_wine_id"], 1652)
        self.assertEqual(scan["extracted"]["rating_source"], "vivino")
        self.assertEqual(res["saved"]["vivino_match_confidence"], "high")
        self.assertEqual(res["saved"]["vivino_wine_id"], 1652)
        self.assertEqual(res["export"]["exported_wines"], 1)
        self.assertTrue(session.get_urls[0].startswith("https://www.vivino.com/search/wines?q="))
        self.assertFalse(any("/api/explore" in u for u in session.get_urls))

    def test_tier_none_when_judge_rejects_all(self) -> None:
        session = _StubHttpSession(
            _gemini(TIGNANELLO_LABEL),
            vivino_html=_search_html(FIXTURES["Antinori Tignanello"]),
            judge_payload={"choice": "NONE", "reason": "Label does not match."},
        )
        scan = self._scan(session)[0]["scan"]
        self.assertEqual(scan["vivino_confidence"], "none")
        self.assertIsNone(scan["extracted"]["vivino_rating"])
        self.assertEqual(scan["extracted"]["vivino_url"], "")

    def test_tier_medium_when_judge_unavailable_or_ambiguous(self) -> None:
        # Judge HTTP failure -> never auto-link.
        session = _StubHttpSession(
            _gemini(TIGNANELLO_LABEL), vivino_html=_search_html(FIXTURES["Antinori Tignanello"]), judge_payload=None
        )
        scan = self._scan(session)[0]["scan"]
        self.assertEqual(scan["vivino_confidence"], "medium")
        self.assertIsNone(scan["extracted"]["vivino_rating"])
        self.assertTrue(scan["vivino_candidates"])

        # Varietal-only name (several cuvées) -> medium even if the judge picks one.
        kz = dict(
            TIGNANELLO_LABEL, winery="Kleine Zalze", name="Chenin Blanc", vintage=2023,
            label_text_verbatim="KLEINE ZALZE\nCHENIN BLANC\n2023\nStellenbosch",
        )
        session2 = _StubHttpSession(
            _gemini(kz), vivino_html=_search_html(FIXTURES["Kleine Zalze Chenin Blanc"]),
            judge_payload={"choice": "1", "reason": "Probably cellar selection."},
        )
        scan2 = self._scan(session2)[0]["scan"]
        self.assertEqual(scan2["vivino_confidence"], "medium")
        self.assertEqual(scan2["vivino_selected_index"], 1)
        self.assertIsNone(scan2["extracted"]["vivino_rating"])

    def test_vivino_blocked_never_estimates(self) -> None:
        session = _StubHttpSession(_gemini(TIGNANELLO_LABEL), vivino_status=429, vivino_html="")
        scan = self._scan(session)[0]["scan"]
        self.assertEqual(scan["vivino_status"], "http_429")
        self.assertEqual(scan["vivino_confidence"], "none")
        self.assertIsNone(scan["extracted"]["vivino_rating"])
        self.assertEqual(scan["extracted"]["rating_source"], "")
        self.assertEqual(session.judge_prompts, [])

    def test_no_gemini_rating_estimation_code_path(self) -> None:
        """Static guard: no production module may produce Gemini-sourced ratings."""
        root = Path(__file__).resolve().parents[1] / "custom_components" / "homecave"
        for py in root.glob("*.py"):
            src = py.read_text(encoding="utf-8")
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.assertNotIn("estimate_rating", node.name, f"{py.name}: {node.name}")
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertNotIn(node.value, {"gemini_estimate"}, f"{py.name} emits gemini_estimate")
            self.assertNotIn("/api/explore/explore", src, f"{py.name} still uses the explore endpoint")

    def test_gemini_scanner_error_reporting_on_http_failures(self) -> None:
        """Verify that GeminiScannerError reports descriptive, user-friendly messages for HTTP 400, 403, 429."""
        from custom_components.homecave.scanner import (
            GeminiScannerError,
            async_scan_bottle_label,
        )

        class _ErrorSession:
            def __init__(self, status: int, text: str) -> None:
                self.status = status
                self.msg_text = text

            def post(self, url: str, **kwargs: Any) -> Any:
                class _Resp:
                    def __init__(self, s: int, t: str) -> None:
                        self.status = s
                        self.t = t

                    async def text(self) -> str:
                        return self.t

                    async def __aenter__(self) -> Any:
                        return self

                    async def __aexit__(self, *args: Any) -> None:
                        pass

                return _Resp(self.status, self.msg_text)

        async def _run() -> None:
            # 1. HTTP 400 - Model ID error
            with self.assertRaises(GeminiScannerError) as ctx_400:
                await async_scan_bottle_label(
                    _ErrorSession(400, "models/invalid not found"),  # type: ignore[arg-type]
                    api_key="valid-key",
                    image_bytes=b"dummy",
                )
            self.assertIn("HTTP 400", str(ctx_400.exception))
            self.assertIn("Check your configured Gemini Model ID", str(ctx_400.exception))

            # 2. HTTP 403 - Permission Denied / Key issue
            with self.assertRaises(GeminiScannerError) as ctx_403:
                await async_scan_bottle_label(
                    _ErrorSession(403, "API_KEY_INVALID"),  # type: ignore[arg-type]
                    api_key="bad-key",
                    image_bytes=b"dummy",
                )
            self.assertIn("HTTP 403", str(ctx_403.exception))
            self.assertIn("Google Gemini API key", str(ctx_403.exception))

            # 3. HTTP 429 - Rate limit / Quota
            with self.assertRaises(GeminiScannerError) as ctx_429:
                await async_scan_bottle_label(
                    _ErrorSession(429, "RESOURCE_EXHAUSTED"),  # type: ignore[arg-type]
                    api_key="valid-key",
                    image_bytes=b"dummy",
                )
            self.assertIn("HTTP 429", str(ctx_429.exception))
            self.assertIn("Rate Limit", str(ctx_429.exception))

        asyncio.run(_run())

    def test_gemini_wine_research_scan(self) -> None:
        """Verify Gemini AI deep research scan on wine producer and cuvée history."""
        from custom_components.homecave.scanner import async_research_wine_with_gemini

        async def _run() -> None:
            research_payload = {
                "producer_history": "Founded in 1385, Marchesi Antinori is one of Italy's oldest wine dynasties.",
                "wine_and_terroir": "Born in 1971 from the Tignanello vineyard in the heart of Chianti Classico.",
                "tasting_profile": "Intense notes of ripe red berries, vanilla, dark chocolate, and silky fine tannins.",
                "serving_and_pairing": "Serve at 16-18°C after decanting 1 hour. Ideal with roasted venison and aged pecorino.",
                "fun_facts": "The wine that ignited the Super Tuscan revolution by defying traditional DOC rules.",
                "summary": "Tignanello is a landmark Italian masterpiece celebrating Tuscan Sangiovese with Bordeaux varieties.",
                "taste_profile": {
                    "soft_vs_acidic": 68,
                    "light_vs_bold": 78,
                    "dry_vs_sweet": 8,
                    "soft_vs_acidic_label": "Crisp & Lively",
                    "light_vs_bold_label": "Bold & Full",
                    "dry_vs_sweet_label": "Bone Dry",
                },
            }
            gemini_resp = {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": json.dumps(research_payload)}],
                        }
                    }
                ]
            }
            session = _StubHttpSession(gemini_resp)
            wine_data = {
                "winery": "Antinori",
                "name": "Tignanello",
                "vintage": 2019,
                "varietal": "Sangiovese, Cabernet Sauvignon",
                "region": "Toscana",
                "country": "Italy",
                "wine_type": "red",
                "general_notes": "Cellared in bottle at 14°C",
            }
            result = await async_research_wine_with_gemini(
                session,  # type: ignore[arg-type]
                api_key="test-api-key",
                wine_data=wine_data,
            )
            self.assertEqual(result["status"], "ok")
            res = result["research"]
            self.assertIn("1385", res["producer_history"])
            self.assertIn("Tignanello", res["wine_and_terroir"])
            self.assertIn("Super Tuscan", res["fun_facts"])
            self.assertEqual(res["taste_profile"]["soft_vs_acidic"], 68)
            self.assertEqual(res["taste_profile"]["light_vs_bold"], 78)
            self.assertEqual(res["taste_profile"]["dry_vs_sweet"], 8)
            self.assertEqual(res["taste_profile"]["soft_vs_acidic_label"], "Crisp & Lively")
            self.assertEqual(session.post_headers[-1].get("x-goog-api-key"), "test-api-key")
            self.assertNotIn("key", session.post_params[-1])

        asyncio.run(_run())

    def test_v036_security_gemini_header_image_magic_and_rate_limit(self) -> None:
        """v0.3.6: Gemini API key passed via x-goog-api-key header, image magic bytes verified, and 10/min rate limit enforced."""
        from custom_components.homecave.scanner import GeminiScannerError

        # 1. Non-image bytes (e.g. HTML/JS disguised as base64) rejected by parse_image_data_uri
        bad_payload = "data:image/jpeg;base64," + base64.b64encode(b"<script>alert(1)</script>").decode("ascii")
        with self.assertRaises(GeminiScannerError) as bad_ctx:
            parse_image_data_uri(bad_payload)
        self.assertIn("JPEG, PNG, or WebP", str(bad_ctx.exception))

        # 2. Valid JPEG magic bytes accepted and mime normalized
        valid_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01TEST"
        valid_uri = "data:image/png;base64," + base64.b64encode(valid_jpeg).decode("ascii")
        detected_mime, decoded_bytes = parse_image_data_uri(valid_uri)
        self.assertEqual(decoded_bytes, valid_jpeg)
        self.assertEqual(detected_mime, "image/jpeg")

        # 3. Coordinator rate limit blocks the 11th Gemini call within 60 seconds
        async def _run_rate_limit() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                session = _StubHttpSession(_gemini(TIGNANELLO_LABEL), vivino_status=404)
                coord = self._make_coordinator(Path(tmpdir), session)
                await coord.async_initialize()
                for _ in range(10):
                    coord._check_gemini_rate_limit()
                with self.assertRaises(GeminiScannerError) as rl_ctx:
                    coord._check_gemini_rate_limit()
                self.assertIn("rate limit", str(rl_ctx.exception).lower())

        asyncio.run(_run_rate_limit())

    def test_v050_sommelier_dish_pairing(self) -> None:
        """v0.5.0: Verify SommAI food-wine pairing grounded in cellar inventory."""
        from custom_components.homecave.scanner import async_sommelier_dish_pairing

        async def _run() -> None:
            pairing_payload = {
                "dish_name": "Pan-Seared Ribeye with Rosemary Butter",
                "dish_analysis": "Rich, fatty beef with savory umami and herbal rosemary.",
                "pairing_reasoning": "High-tannin structured reds cut through the beef fat.",
                "recommended_wines": [
                    {
                        "wine_id": 1,
                        "wine_title": "Tenuta San Guido Sassicaia 2018",
                        "match_type": "best",
                        "sommelier_notes": "Bold Cabernet tannins beautifully slice through the ribeye's marbling.",
                        "serving_temp_c": "16-18°C",
                        "decant_minutes": 60,
                    }
                ],
                "conversational_response": "I recommend opening your Sassicaia 2018 for this magnificent ribeye.",
            }
            gemini_resp = {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": json.dumps(pairing_payload)}],
                        }
                    }
                ]
            }

            class _SommSession:
                def post(self, url: str, **kwargs: Any) -> Any:
                    class _Resp:
                        status = 200

                        async def text(self) -> str:
                            return json.dumps(gemini_resp)

                        async def __aenter__(self) -> Any:
                            return self

                        async def __aexit__(self, *args: Any) -> None:
                            pass

                    return _Resp()

            cellar_wines = [
                {
                    "id": 1,
                    "winery": "Tenuta San Guido",
                    "name": "Sassicaia",
                    "vintage": 2018,
                    "wine_type": "red",
                    "varietal": "Cabernet Sauvignon, Cabernet Franc",
                    "quantity": 2,
                    "drink_status": "ready",
                }
            ]

            res = await async_sommelier_dish_pairing(
                _SommSession(),  # type: ignore[arg-type]
                api_key="test-key",
                cellar_wines=cellar_wines,
                dish_description="What should I drink with a medium-rare ribeye?",
                image_bytes=b"\xff\xd8\xff\xe0test",
                mime_type="image/jpeg",
                conversation_history=[{"role": "user", "text": "Hi"}],
            )
            pairing = res["pairing"]
            self.assertEqual(pairing["dish_name"], "Pan-Seared Ribeye with Rosemary Butter")
            self.assertEqual(len(pairing["recommended_wines"]), 1)
            self.assertEqual(pairing["recommended_wines"][0]["wine_id"], 1)
            self.assertEqual(pairing["recommended_wines"][0]["match_type"], "best")
            self.assertEqual(pairing["recommended_wines"][0]["decant_minutes"], 60)
            self.assertIn("Sassicaia", pairing["conversational_response"])

        asyncio.run(_run())

    def test_v050_enrich_vivino_bottle(self) -> None:
        """v0.5.0: Verify Gemini AI enrichment for missing Vivino bottle details."""
        from custom_components.homecave.scanner import async_enrich_vivino_bottle

        async def _run() -> None:
            enrichment_payload = {
                "varietal": "Chardonnay",
                "drink_from_year": 2024,
                "drink_until_year": 2030,
                "abv": 13.5,
                "taste_profile": {
                    "soft_vs_acidic": 55,
                    "light_vs_bold": 65,
                    "dry_vs_sweet": 10,
                    "soft_vs_acidic_label": "Crisp & Mineral",
                    "light_vs_bold_label": "Medium-Full",
                    "dry_vs_sweet_label": "Bone Dry",
                },
                "summary": "Classic Premier Cru Chablis displaying crisp minerality and flinty lemon aromas.",
                "producer_history": "Domaine William Fèvre was established in 1959.",
                "wine_and_terroir": "Kimmeridgian limestone clay soils rich in fossilized oysters.",
                "tasting_profile": "White flowers, citrus zest, and stony mineral core.",
                "serving_and_pairing": "Chill to 10-12°C. Excellent with fresh oysters and grilled sea bass.",
            }
            gemini_resp = {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": json.dumps(enrichment_payload)}],
                        }
                    }
                ]
            }

            class _EnrichSession:
                def post(self, url: str, **kwargs: Any) -> Any:
                    class _Resp:
                        status = 200

                        async def text(self) -> str:
                            return json.dumps(gemini_resp)

                        async def __aenter__(self) -> Any:
                            return self

                        async def __aexit__(self, *args: Any) -> None:
                            pass

                    return _Resp()

            bottle = {
                "winery": "William Fèvre",
                "name": "Chablis Premier Cru Montmains",
                "vintage": 2020,
                "wine_type": "white",
                "country": "France",
                "region": "Burgundy",
            }

            res = await async_enrich_vivino_bottle(
                _EnrichSession(),  # type: ignore[arg-type]
                api_key="test-key",
                wine_data=bottle,
            )
            enrichment = res["enrichment"]
            self.assertEqual(enrichment["varietal"], "Chardonnay")
            self.assertEqual(enrichment["drink_from_year"], 2024)
            self.assertEqual(enrichment["drink_until_year"], 2030)
            self.assertEqual(enrichment["taste_profile"]["soft_vs_acidic"], 55)
            self.assertIn("William Fèvre", enrichment["producer_history"])

        asyncio.run(_run())

    def test_v050_sommai_list_wines_positional_and_progress_reporting(self) -> None:
        """v0.5.0 regression: verify WineDatabase.list_wines accepts positional args, sommelier chat executes, and progress reports running."""
        import tempfile
        from pathlib import Path
        from custom_components.homecave.database import WineDatabase
        from custom_components.homecave.coordinator import WineInventoryCoordinator

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test.db"
            db = WineDatabase(db_path)
            db.initialize()

            # Add one in-stock wine and one out-of-stock tasted wine
            w1 = db.add_or_increment_wine(winery="Antinori", name="Tignanello", vintage=2020, quantity=3)
            w2 = db.add_or_increment_wine(winery="Gaja", name="Barbaresco", vintage=2019, quantity=0, as_tasted=True)

            # 1. Positional calls to list_wines must not raise TypeError
            in_stock = db.list_wines(False)
            self.assertEqual(len(in_stock), 1)
            self.assertEqual(in_stock[0]["id"], w1["id"])

            all_wines = db.list_wines(True)
            self.assertEqual(len(all_wines), 2)

            # 2. Mock session and Coordinator setup
            pairing_payload = {
                "dish_name": "Tuscan Bistecca",
                "dish_analysis": {"richness": "High", "acidity": "Medium", "sweetness": "Low", "spice": "Low", "flavor_notes": ["grilled beef"]},
                "pairing_reasoning": "Tignanello has bold tannins and acidity to cut through rich beef.",
                "recommended_wines": [
                    {
                        "wine_id": w1["id"],
                        "match_type": "best",
                        "vintage": 2020,
                        "wine_name": "Tignanello",
                        "producer": "Antinori",
                        "serving_temp": "16-18°C",
                        "decant_minutes": 60,
                        "pairing_notes": "Perfect match",
                    }
                ],
                "conversational_response": "I recommend opening your bottle of 2020 Antinori Tignanello.",
            }
            gemini_resp = {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": json.dumps(pairing_payload)}],
                        }
                    }
                ]
            }

            class _SommSession:
                def post(self, url: str, **kwargs: Any) -> Any:
                    class _Resp:
                        status = 200
                        async def text(self) -> str:
                            return json.dumps(gemini_resp)
                        async def __aenter__(self) -> Any:
                            return self
                        async def __aexit__(self, *args: Any) -> None:
                            pass
                    return _Resp()

            coord = WineInventoryCoordinator(
                hass=_StubHass(Path(tmp_dir)),
                entry_id="test_entry",
                config={"gemini_api_key": "test-key"},
                session_getter=lambda h: _SommSession(),
            )
            coord.db = db

            # Progress tracking must report running=True when status is 'running'
            coord._import_progress = {
                "status": "running",
                "percent": 30,
                "current": 10,
                "total": 50,
                "message": "Downloading bottle covers...",
                "wine_name": "",
            }
            prog = coord.get_import_progress()
            self.assertTrue(prog["running"])
            self.assertEqual(prog["progress_pct"], 30)
            self.assertEqual(prog["total_bottles"], 50)
            self.assertEqual(prog["current_bottle"], 10)
            self.assertEqual(prog["status_message"], "Downloading bottle covers...")

            # 3. Test sommelier chat doesn't raise TypeError and returns unpacked top-level keys
            async def _run_somm() -> None:
                chat_res = await coord.async_sommelier_chat(query_text="What wine pairs with bistecca?")
                self.assertEqual(chat_res["status"], "ok")
                # Top-level keys unpacked alongside nested pairing
                self.assertEqual(chat_res["dish_name"], "Tuscan Bistecca")
                self.assertEqual(chat_res["conversational_response"], "I recommend opening your bottle of 2020 Antinori Tignanello.")
                self.assertEqual(chat_res["recommended_wines"][0]["wine_id"], w1["id"])
                pairing = chat_res.get("pairing") or {}
                self.assertEqual(pairing["dish_name"], "Tuscan Bistecca")
                self.assertEqual(pairing["recommended_wines"][0]["wine_id"], w1["id"])

            asyncio.run(_run_somm())

    def test_v052_cover_image_retry_and_vivino_wines_link(self) -> None:
        """v0.5.2: Verify 3s and 5s retry backoff for cover downloads, and https://www.vivino.com/wines/<id> preservation."""
        from unittest.mock import patch, MagicMock
        from custom_components.homecave.database import WineDatabase
        from custom_components.homecave.backup import (
            download_and_save_cover_image,
            import_vivino_full_wine_list_csv,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            config_dir = Path(tmp_dir)
            db_path = config_dir / "homecave.db"
            db = WineDatabase(db_path)
            db.initialize()

            # 1. Test image retry sequence: attempt 1 fails, attempt 2 fails (after 3s), attempt 3 succeeds (after 5s)
            valid_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01COVER_TEST"
            attempts = 0

            def _fake_urlopen(req: Any, timeout: float = 6.0) -> Any:
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise urllib.error.URLError("temporary network timeout")
                resp = MagicMock()
                resp.read.return_value = valid_jpeg
                resp.__enter__.return_value = resp
                return resp

            with patch("urllib.request.urlopen", side_effect=_fake_urlopen), patch("time.sleep") as mock_sleep:
                saved_url = download_and_save_cover_image(
                    config_dir,
                    "https://images.vivino.com/labels/retry_wine.jpg",
                )
                self.assertEqual(attempts, 3)
                self.assertEqual(mock_sleep.call_count, 2)
                mock_sleep.assert_any_call(3)
                mock_sleep.assert_any_call(5)
                self.assertTrue(saved_url.startswith("/local/homecave/covers/"))
                cover_file = config_dir / "www" / "homecave" / "covers" / "vivino_retry_wine.jpg"
                self.assertTrue(cover_file.exists())
                self.assertEqual(cover_file.read_bytes(), valid_jpeg)

            # 2. Test Vivino URL format preservation (https://www.vivino.com/wines/<id>) without rewriting to /w/
            csv_content = (
                "Winery,Wine name,Vintage,Region,Country,Regional wine style,Average rating,Wine ratings count,Your rating,Your review,Personal Note,Wine type,Drinking Window,Link to wine,Label image\n"
                'Marchesi Antinori,Tignanello,2019,Tuscany,Italy,Tuscan Red,4.6,8400,4.8,"Masterpiece","Cellar gem",Red,2024-2040,https://www.vivino.com/wines/160128212,https://images.vivino.com/labels/tig.jpg\n'
            )
            res = import_vivino_full_wine_list_csv(config_dir, db, csv_content, download_images=False)
            self.assertEqual(res["tasted_imported"], 1)

            wines = db.list_wines()
            tig = [w for w in wines if "Tignanello" in w["name"]][0]
            self.assertEqual(tig["vivino_url"], "https://www.vivino.com/wines/160128212")
            self.assertEqual(tig["vivino_wine_id"], 160128212)

            # 3. Verify db.initialize() migration does NOT wipe https://www.vivino.com/wines/<id>
            db.initialize()
            tig_after_init = [w for w in db.list_wines() if "Tignanello" in w["name"]][0]
            self.assertEqual(tig_after_init["vivino_url"], "https://www.vivino.com/wines/160128212")
            self.assertEqual(tig_after_init["vivino_wine_id"], 160128212)

    def test_v053_scanner_wine_guardrail_rejection_and_acceptance(self) -> None:
        """v0.5.3: Verify wine bottle/label guardrail rejects non-wine images and accepts bottles."""
        from custom_components.homecave.scanner import (
            GeminiScannerError,
            async_scan_bottle_label,
        )

        class _MockSession:
            def __init__(self, response_dict: dict[str, Any]) -> None:
                self.resp_payload = {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"text": json.dumps(response_dict)}
                                ]
                            }
                        }
                    ]
                }

            def post(self, url: str, **kwargs: Any) -> Any:
                class _Resp:
                    def __init__(self, payload: dict[str, Any]) -> None:
                        self.status = 200
                        self.payload = payload

                    async def text(self) -> str:
                        return json.dumps(self.payload)

                    async def __aenter__(self) -> Any:
                        return self

                    async def __aexit__(self, *args: Any) -> None:
                        pass

                return _Resp(self.resp_payload)

        async def _run() -> None:
            # 1. Non-wine image (e.g. food dish or pet): is_wine_bottle_or_label is False
            session_non_wine = _MockSession({
                "is_wine_bottle_or_label": False,
                "winery": "",
                "name": "",
                "wine_type": "red",
                "varietal": "",
                "region": "",
                "country": "",
                "drink_window_note": "",
                "food_pairings": [],
                "label_summary": "Photo of a plate of pasta with tomato sauce.",
                "label_text_verbatim": "",
                "winery_printed": False,
                "name_printed": False,
                "vintage_printed": False,
            })
            with self.assertRaises(GeminiScannerError) as ctx:
                await async_scan_bottle_label(
                    session_non_wine,  # type: ignore[arg-type]
                    api_key="valid-key",
                    image_bytes=b"dummy",
                )
            self.assertIn("This is not a wine bottle or label", str(ctx.exception))
            self.assertIn("Please only scan wine bottles and labels", str(ctx.exception))

            # 2. Non-wine image with no identifying wine text even if flag missing
            session_empty_clues = _MockSession({
                "is_wine_bottle_or_label": False,
                "winery": "",
                "name": "",
                "label_text_verbatim": "",
            })
            with self.assertRaises(GeminiScannerError) as ctx_empty:
                await async_scan_bottle_label(
                    session_empty_clues,  # type: ignore[arg-type]
                    api_key="valid-key",
                    image_bytes=b"dummy",
                )
            self.assertIn("This is not a wine bottle or label", str(ctx_empty.exception))

            # 3. Valid wine bottle / label: passes guardrail and normalizes cleanly
            session_valid_wine = _MockSession({
                "is_wine_bottle_or_label": True,
                "winery": "Château Margaux",
                "name": "Premier Grand Cru Classé",
                "vintage": 2015,
                "wine_type": "red",
                "varietal": "Cabernet Sauvignon Blend",
                "region": "Margaux, Bordeaux",
                "country": "France",
                "drink_window_note": "Peak 2025-2045",
                "food_pairings": ["Roasted lamb", "Prime rib"],
                "label_summary": "Château Margaux 2015 Premier Grand Cru Classé",
                "label_text_verbatim": "CHATEAU MARGAUX 2015 PREMIER GRAND CRU CLASSE",
                "winery_printed": True,
                "name_printed": True,
                "vintage_printed": True,
            })
            extracted = await async_scan_bottle_label(
                session_valid_wine,  # type: ignore[arg-type]
                api_key="valid-key",
                image_bytes=b"dummy",
            )
            self.assertTrue(extracted.get("is_wine_bottle_or_label"))
            self.assertEqual(extracted["winery"], "Château Margaux")
            self.assertEqual(extracted["vintage"], 2015)

        asyncio.run(_run())

    def test_v053_coordinator_cover_cleanup_and_photo_upload(self) -> None:
        """v0.5.3: Verify cover cleanup on scan error, and async_update_wine photo upload."""
        from custom_components.homecave.coordinator import WineInventoryCoordinator
        from custom_components.homecave.database import WineDatabase
        from custom_components.homecave.scanner import GeminiScannerError

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            db = WineDatabase(config_dir / "homecave.db")
            db.initialize()

            class _FakeHass:
                def __init__(self, cdir: Path) -> None:
                    self.config = MagicMock()
                    self.config.config_dir = str(cdir)
                    self.bus = MagicMock()

                async def async_add_executor_job(self, fn: Any, *args: Any) -> Any:
                    return fn(*args)

            hass = _FakeHass(config_dir)
            entry = MagicMock()
            entry.entry_id = "test-entry"
            entry.options = {
                "gemini_api_key": "test-key",
                "gemini_model": "gemini-3.8-flash-lite",
            }

            coordinator = WineInventoryCoordinator(
                hass,
                entry_id="test-entry",
                config={
                    "gemini_api_key": "test-key",
                    "gemini_model": "gemini-3.8-flash-lite",
                },
                session_getter=lambda h: MagicMock(),
            )
            coordinator.db = db

            # 1. Test cover file cleanup when scanning fails guardrail
            jpeg_header = b"\xff\xd8\xff\xe0" + b"\x00" * 32
            b64_img = f"data:image/jpeg;base64,{base64.b64encode(jpeg_header).decode('ascii')}"

            async def _scan_runner() -> None:
                with patch("custom_components.homecave.coordinator.async_scan_bottle_label", side_effect=GeminiScannerError("This is not a wine bottle or label. Please only scan wine bottles and labels.")):
                    with self.assertRaises(GeminiScannerError):
                        await coordinator.async_scan_bottle(image_data=b64_img)

                # Ensure covers dir exists but has no orphaned files from failed scan
                covers_dir = config_dir / "www" / "homecave" / "covers"
                if covers_dir.exists():
                    self.assertEqual(len(list(covers_dir.glob("*.jpg"))), 0)

            asyncio.run(_scan_runner())

            # 2. Test async_update_wine with image_data upload
            wine = db.add_or_increment_wine(
                winery="Domaine Dujac",
                name="Morey-Saint-Denis",
                vintage=2020,
                wine_type="red",
                quantity=2,
            )
            wine_id = wine["id"]
            self.assertEqual(wine.get("image_url"), "")

            async def _update_runner() -> None:
                updated = await coordinator.async_update_wine(
                    wine_id,
                    {"image_data": b64_img, "personal_rating": 4.8},
                )
                self.assertTrue(updated["image_url"].startswith("/local/homecave/covers/"))
                self.assertEqual(updated["personal_rating"], 4.8)

                # Check on disk
                cover_filename = Path(updated["image_url"]).name
                disk_file = config_dir / "www" / "homecave" / "covers" / cover_filename
                self.assertTrue(disk_file.exists())
                self.assertEqual(disk_file.read_bytes(), jpeg_header)

                # Verify in database
                fetched = db.get_wine_detail(wine_id)
                self.assertIsNotNone(fetched)
                self.assertEqual(fetched["image_url"], updated["image_url"])
                self.assertEqual(fetched["personal_rating"], 4.8)

            asyncio.run(_update_runner())

    def test_v054_claude_scanner_error_hierarchy(self) -> None:
        """v0.5.4: Verify ClaudeScannerError and GeminiScannerError inherit from LLMScannerError."""
        self.assertTrue(issubclass(ClaudeScannerError, GeminiScannerError))
        self.assertTrue(issubclass(ClaudeScannerError, LLMScannerError))
        self.assertTrue(issubclass(GeminiScannerError, LLMScannerError))

        err = ClaudeScannerError("Claude test failure")
        self.assertIsInstance(err, GeminiScannerError)
        self.assertIsInstance(err, LLMScannerError)

    def test_v054_extract_json_from_text(self) -> None:
        """v0.5.4: Verify _extract_json_from_text handles clean JSON, markdown code fences, and text padding."""
        # Pure JSON
        data = _extract_json_from_text('{"winery": "Château Latour", "vintage": 2010}')
        self.assertEqual(data["winery"], "Château Latour")
        self.assertEqual(data["vintage"], 2010)

        # Markdown fenced with json tag
        fenced_json = "```json\n{\"winery\": \"Opus One\", \"vintage\": 2018}\n```"
        data_fenced = _extract_json_from_text(fenced_json)
        self.assertEqual(data_fenced["winery"], "Opus One")

        # Markdown fenced without tag
        fenced_plain = "```\n{\"winery\": \"Sassicaia\", \"vintage\": 2015}\n```"
        data_plain = _extract_json_from_text(fenced_plain)
        self.assertEqual(data_plain["winery"], "Sassicaia")

        # Text wrapper around JSON object
        padded = "Here is the extracted information:\n{\"name\": \"Grange\", \"producer\": \"Penfolds\"}\nHope this helps!"
        data_padded = _extract_json_from_text(padded)
        self.assertEqual(data_padded["name"], "Grange")
        self.assertEqual(data_padded["producer"], "Penfolds")

        # Invalid JSON text raises ValueError
        with self.assertRaises(ValueError):
            _extract_json_from_text("Sorry, I could not detect any wine bottle or label here.")

    def test_v054_claude_bottle_label_scan_success_and_guardrail(self) -> None:
        """v0.5.4: Test Claude label scan success, guardrail rejection, and HTTP errors."""
        class _ClaudeMockSession:
            def __init__(self, response_body: Any, status: int = 200) -> None:
                self.response_body = response_body
                self.status = status
                self.last_headers: dict[str, str] = {}
                self.last_json: dict[str, Any] = {}

            def post(self, url: str, **kwargs: Any) -> Any:
                self.last_headers = dict(kwargs.get("headers") or {})
                self.last_json = dict(kwargs.get("json") or {})

                class _Resp:
                    def __init__(self, status: int, body: Any) -> None:
                        self.status = status
                        self._body = body

                    async def json(self, content_type: str | None = None) -> Any:
                        return self._body

                    async def text(self) -> str:
                        return self._body if isinstance(self._body, str) else json.dumps(self._body)

                    async def __aenter__(self) -> Any:
                        return self

                    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
                        return None

                return _Resp(self.status, self.response_body)

        async def _run() -> None:
            # 1. Success case
            claude_success_reply = {
                "id": "msg_01",
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({
                            "is_wine_bottle_or_label": True,
                            "winery": "Château Margaux",
                            "name": "Premier Grand Cru Classé",
                            "vintage": 2015,
                            "wine_type": "red",
                            "varietal": "Cabernet Sauvignon Blend",
                            "region": "Margaux, Bordeaux",
                            "country": "France",
                            "drink_window_note": "Drink 2025-2045",
                            "food_pairings": ["Roast Lamb", "Ribeye"],
                            "label_summary": "Château Margaux 2015 Premier Grand Cru Classé",
                            "label_text_verbatim": "CHATEAU MARGAUX 2015",
                            "winery_printed": True,
                            "name_printed": True,
                            "vintage_printed": True,
                        }),
                    }
                ],
            }
            session_success = _ClaudeMockSession(claude_success_reply)
            result = await async_scan_bottle_label_claude(
                session_success,  # type: ignore[arg-type]
                api_key="sk-ant-test-key",
                image_bytes=b"dummy_image_bytes",
                mime_type="image/jpeg",
                model="claude-haiku-5-5",
            )
            self.assertTrue(result["is_wine_bottle_or_label"])
            self.assertEqual(result["winery"], "Château Margaux")
            self.assertEqual(result["vintage"], 2015)
            self.assertEqual(session_success.last_headers.get("x-api-key"), "sk-ant-test-key")
            self.assertEqual(session_success.last_headers.get("anthropic-version"), "2023-06-01")
            self.assertEqual(session_success.last_json.get("model"), "claude-haiku-5-5")
            self.assertEqual(len(session_success.last_json.get("messages", [])), 1)

            # 2. Guardrail failure: is_wine_bottle_or_label is False
            claude_guardrail_reply = {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({
                            "is_wine_bottle_or_label": False,
                            "label_summary": "A close-up photo of a wristwatch.",
                        }),
                    }
                ]
            }
            session_guardrail = _ClaudeMockSession(claude_guardrail_reply)
            with self.assertRaises(ClaudeScannerError) as ctx:
                await async_scan_bottle_label_claude(
                    session_guardrail,  # type: ignore[arg-type]
                    api_key="sk-ant-test-key",
                    image_bytes=b"dummy",
                )
            self.assertIn("This is not a wine bottle or label", str(ctx.exception))

            # 3. HTTP 400 Bad Request
            session_400 = _ClaudeMockSession({"error": {"message": "Invalid model parameter"}}, status=400)
            with self.assertRaises(ClaudeScannerError) as ctx_400:
                await async_scan_bottle_label_claude(
                    session_400,  # type: ignore[arg-type]
                    api_key="sk-ant-test-key",
                    image_bytes=b"dummy",
                )
            self.assertIn("400", str(ctx_400.exception))

            # 4. HTTP 401 Authentication Denied
            session_401 = _ClaudeMockSession({"error": {"message": "Invalid API Key"}}, status=401)
            with self.assertRaises(ClaudeScannerError) as ctx_401:
                await async_scan_bottle_label_claude(
                    session_401,  # type: ignore[arg-type]
                    api_key="bad-key",
                    image_bytes=b"dummy",
                )
            self.assertIn("401", str(ctx_401.exception))

            # 5. HTTP 429 Rate Limit
            session_429 = _ClaudeMockSession({"error": {"message": "Too Many Requests"}}, status=429)
            with self.assertRaises(ClaudeScannerError) as ctx_429:
                await async_scan_bottle_label_claude(
                    session_429,  # type: ignore[arg-type]
                    api_key="sk-ant-test-key",
                    image_bytes=b"dummy",
                )
            self.assertIn("429", str(ctx_429.exception))

            # 6. Missing API key
            with self.assertRaises(ClaudeScannerError) as ctx_missing:
                await async_scan_bottle_label_claude(
                    session_success,  # type: ignore[arg-type]
                    api_key="",
                    image_bytes=b"dummy",
                )
            self.assertIn("Anthropic Claude API key", str(ctx_missing.exception))

        asyncio.run(_run())

    def test_v054_claude_judge_vivino_candidates(self) -> None:
        """v0.5.4: Test Claude Vivino candidate matching judgment."""
        class _ClaudeMockSession:
            def __init__(self, reply: Any) -> None:
                self.reply = reply

            def post(self, url: str, **kwargs: Any) -> Any:
                class _Resp:
                    def __init__(self, body: Any) -> None:
                        self.status = 200
                        self._body = body

                    async def json(self, content_type: str | None = None) -> Any:
                        return self._body

                    async def text(self) -> str:
                        return self._body if isinstance(self._body, str) else json.dumps(self._body)

                    async def __aenter__(self) -> Any:
                        return self

                    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
                        return None

                return _Resp(self.reply)

        async def _run() -> None:
            # 1. Matches candidate at index 1
            claude_reply = {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"choice": "1", "reason": "Vintage and winery match"}),
                    }
                ]
            }
            session = _ClaudeMockSession(claude_reply)
            candidates = [
                {"name": "Margaux 2014", "link": "https://vivino.com/wines/1"},
                {"name": "Margaux 2015", "link": "https://vivino.com/wines/2"},
            ]
            resp = await async_judge_vivino_candidates_claude(
                session,  # type: ignore[arg-type]
                api_key="sk-ant-test",
                image_bytes=b"dummy",
                mime_type="image/jpeg",
                label_text_verbatim="Margaux 2015",
                candidates=candidates,
            )
            self.assertEqual(resp.get("choice"), 1)

            # 2. Out of bounds or None choice returns None
            claude_none_reply = {
                "content": [{"type": "text", "text": json.dumps({"choice": "NONE", "reason": "no match"})}]
            }
            session_none = _ClaudeMockSession(claude_none_reply)
            resp_none = await async_judge_vivino_candidates_claude(
                session_none,  # type: ignore[arg-type]
                api_key="sk-ant-test",
                image_bytes=None,
                mime_type="image/jpeg",
                label_text_verbatim="Margaux 2015",
                candidates=candidates,
            )
            self.assertIsNone(resp_none.get("choice"))

        asyncio.run(_run())

    def test_v054_claude_wine_research_and_sommelier_pairing(self) -> None:
        """v0.5.4: Test Claude wine research and SommAI dish pairing."""
        class _ClaudeMockSession:
            def __init__(self, reply: Any) -> None:
                self.reply = reply
                self.last_json: dict[str, Any] = {}

            def post(self, url: str, **kwargs: Any) -> Any:
                self.last_json = dict(kwargs.get("json") or {})

                class _Resp:
                    def __init__(self, body: Any) -> None:
                        self.status = 200
                        self._body = body

                    async def json(self, content_type: str | None = None) -> Any:
                        return self._body

                    async def text(self) -> str:
                        return self._body if isinstance(self._body, str) else json.dumps(self._body)

                    async def __aenter__(self) -> Any:
                        return self

                    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
                        return None

                return _Resp(self.reply)

        async def _run() -> None:
            # 1. Claude Wine Research
            research_payload = {
                "summary": "Historic Bordeaux estate producing benchmark Cabernet blends.",
                "producer_profile": "Premier Grand Cru Classé in the 1855 classification.",
                "terroir_and_appellation": "Gravel mounds in Margaux near the Gironde estuary.",
                "tasting_notes": "Blackcurrant, violet, cigar box, fine tannins.",
                "food_pairings": ["Roast rack of lamb", "Dry-aged ribeye"],
                "drinking_window": "2025 - 2045",
                "bold_light_slider": 78,
                "tannic_smooth_slider": 82,
                "sweet_dry_slider": 5,
                "soft_acidic_slider": 65,
            }
            session_research = _ClaudeMockSession({
                "content": [{"type": "text", "text": json.dumps(research_payload)}]
            })
            res = await async_research_wine_with_claude(
                session_research,  # type: ignore[arg-type]
                api_key="sk-ant-test",
                wine_data={"winery": "Château Margaux", "vintage": 2015, "wine_type": "red"},
            )
            self.assertEqual(res["research"]["summary"], research_payload["summary"])
            self.assertEqual(res["research"]["bold_light_slider"], 78)
            self.assertEqual(res["research"]["tannic_smooth_slider"], 82)

            # 2. Claude SommAI Dish Pairing (with multimodal dish photo)
            somm_payload = {
                "response": "For this pan-seared duck breast with cherry reduction, I recommend a medium-bodied red with high acidity.",
                "pairing_recommendations": [
                    {
                        "wine_id": 42,
                        "winery": "Domaine Dujac",
                        "wine_name": "Morey-Saint-Denis",
                        "vintage": 2020,
                        "pairing_reason": "Bright red fruit and earthiness cut through the rich duck fat.",
                        "confidence": "high",
                    }
                ],
                "recommended_wine_style": "Red Burgundy / Pinot Noir",
            }
            session_somm = _ClaudeMockSession({
                "content": [{"type": "text", "text": json.dumps(somm_payload)}]
            })
            cellar_wines = [
                {"id": 42, "winery": "Domaine Dujac", "name": "Morey-Saint-Denis", "vintage": 2020, "quantity": 2, "wine_type": "red"},
            ]
            history = [
                {"role": "user", "content": "What wine goes best with duck?"},
                {"role": "model", "content": "Duck pairs wonderfully with Pinot Noir or Syrah."},
            ]
            somm_res = await async_sommelier_dish_pairing_claude(
                session_somm,  # type: ignore[arg-type]
                api_key="sk-ant-test",
                dish_description="I have pan-seared duck breast with cherry sauce.",
                cellar_wines=cellar_wines,
                image_bytes=b"dummy_dish_image",
                mime_type="image/jpeg",
                conversation_history=history,
            )
            self.assertIn("pan-seared duck", somm_res["pairing"]["response"])
            self.assertEqual(len(somm_res["pairing"]["pairing_recommendations"]), 1)
            self.assertEqual(somm_res["pairing"]["pairing_recommendations"][0]["wine_id"], 42)

            # Verify Claude message format & role translation
            sent_messages = session_somm.last_json.get("messages", [])
            self.assertEqual(len(sent_messages), 3)
            # Check role mapping: "model" -> "assistant"
            self.assertEqual(sent_messages[0]["role"], "user")
            self.assertEqual(sent_messages[1]["role"], "assistant")
            self.assertEqual(sent_messages[2]["role"], "user")
            # Verify multimodal content in latest user message
            last_content = sent_messages[2]["content"]
            self.assertEqual(len(last_content), 2)
            self.assertEqual(last_content[0]["type"], "image")
            self.assertEqual(last_content[1]["type"], "text")

            # 3. Claude Vivino Bottle Enrichment
            enrich_payload = {
                "grapes": ["Pinot Noir"],
                "alcohol_pct": 13.5,
                "region": "Burgundy",
                "country": "France",
                "wine_type": "red",
                "drink_from_year": 2024,
                "drink_until_year": 2038,
                "general_notes": "Silky, fragrant, structured.",
                "food_pairings": ["Duck", "Roast chicken"],
            }
            session_enrich = _ClaudeMockSession({
                "content": [{"type": "text", "text": json.dumps(enrich_payload)}]
            })
            enriched = await async_enrich_vivino_bottle_claude(
                session_enrich,  # type: ignore[arg-type]
                api_key="sk-ant-test",
                wine_data={"winery": "Domaine Dujac", "name": "Morey-Saint-Denis", "vintage": 2020},
            )
            self.assertEqual(enriched["enrichment"]["alcohol_pct"], 13.5)
            self.assertEqual(enriched["enrichment"]["drink_until_year"], 2038)

        asyncio.run(_run())

    def test_v054_scanner_unified_dispatchers(self) -> None:
        """v0.5.4: Test unified dispatchers route to correct provider (Gemini vs Claude)."""
        async def _run() -> None:
            mock_session = MagicMock()

            # Test scan dispatch
            with patch("custom_components.homecave.scanner.async_scan_bottle_label_claude", return_value={"provider": "claude"}) as mock_claude_scan:
                res_claude = await async_scan_bottle_label(
                    mock_session,
                    api_key="test-key",
                    image_bytes=b"dummy",
                    provider="claude",
                )
                self.assertEqual(res_claude["provider"], "claude")
                mock_claude_scan.assert_called_once()

            with patch("custom_components.homecave.scanner.async_scan_bottle_label_gemini", return_value={"provider": "gemini"}) as mock_gemini_scan:
                res_gemini = await async_scan_bottle_label(
                    mock_session,
                    api_key="test-key",
                    image_bytes=b"dummy",
                    provider="gemini",
                )
                self.assertEqual(res_gemini["provider"], "gemini")
                mock_gemini_scan.assert_called_once()

            # Test research dispatch
            with patch("custom_components.homecave.scanner.async_research_wine_with_claude", return_value={"research": "claude"}) as mock_claude_res:
                res_r = await async_research_wine(
                    mock_session,
                    api_key="test-key",
                    wine_data={},
                    provider="claude",
                )
                self.assertEqual(res_r["research"], "claude")
                mock_claude_res.assert_called_once()

            # Test sommelier dispatch
            with patch("custom_components.homecave.scanner.async_sommelier_dish_pairing_claude", return_value={"somm": "claude"}) as mock_claude_somm:
                res_s = await async_sommelier_dish_pairing(
                    mock_session,
                    api_key="test-key",
                    dish_description="Steak",
                    cellar_wines=[],
                    provider="claude",
                )
                self.assertEqual(res_s["somm"], "claude")
                mock_claude_somm.assert_called_once()

            # Test enrich dispatch
            with patch("custom_components.homecave.scanner.async_enrich_vivino_bottle_claude", return_value={"enrich": "claude"}) as mock_claude_enr:
                res_e = await async_enrich_vivino_bottle(
                    mock_session,
                    api_key="test-key",
                    wine_data={},
                    provider="claude",
                )
                self.assertEqual(res_e["enrich"], "claude")
                mock_claude_enr.assert_called_once()

        asyncio.run(_run())

    def test_v054_coordinator_claude_provider_routing(self) -> None:
        """v0.5.4: Test coordinator properly routes requests when configured with Claude."""
        from custom_components.homecave.coordinator import WineInventoryCoordinator
        from custom_components.homecave.database import WineDatabase

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            db = WineDatabase(config_dir / "homecave.db")
            db.initialize()

            class _FakeHass:
                def __init__(self, cdir: Path) -> None:
                    self.config = MagicMock()
                    self.config.config_dir = str(cdir)
                    self.bus = MagicMock()

                async def async_add_executor_job(self, fn: Any, *args: Any) -> Any:
                    return fn(*args)

            hass = _FakeHass(config_dir)
            entry = MagicMock()
            entry.entry_id = "test-claude-entry"
            entry.options = {
                "llm_provider": "claude",
                "claude_api_key": "sk-ant-active-key",
                "claude_model": "claude-haiku-5-5",
                "gemini_api_key": "some-other-gemini-key",
            }

            coordinator = WineInventoryCoordinator(
                hass,
                entry_id="test-claude-entry",
                config=entry.options,
                session_getter=lambda h: MagicMock(),
            )
            coordinator.db = db

            self.assertEqual(coordinator.llm_provider, "claude")
            self.assertEqual(coordinator.active_llm_api_key, "sk-ant-active-key")
            self.assertEqual(coordinator.active_llm_model, "claude-haiku-5-5")

            async def _coordinator_run() -> None:
                # 1. async_scan_bottle passes provider='claude' and claude key/model
                jpeg_header = b"\xff\xd8\xff\xe0" + b"\x00" * 32
                b64_img = f"data:image/jpeg;base64,{base64.b64encode(jpeg_header).decode('ascii')}"

                with patch("custom_components.homecave.coordinator.async_scan_bottle_label", return_value={"winery": "Test Estate", "is_wine_bottle_or_label": True}) as mock_scan:
                    scan_res = await coordinator.async_scan_bottle(image_data=b64_img)
                    self.assertEqual(scan_res["extracted"]["winery"], "Test Estate")
                    mock_scan.assert_called_once()
                    _, kwargs = mock_scan.call_args
                    self.assertEqual(kwargs.get("provider"), "claude")
                    self.assertEqual(kwargs.get("api_key"), "sk-ant-active-key")
                    self.assertEqual(kwargs.get("model"), "claude-haiku-5-5")

                # 2. async_research_wine passes provider='claude'
                wine = db.add_or_increment_wine(
                    winery="Test Estate",
                    name="Reserve",
                    vintage=2020,
                    wine_type="red",
                    quantity=1,
                )
                with patch("custom_components.homecave.coordinator.async_research_wine", return_value={"status": "ok", "research": {"summary": "Detailed research"}}) as mock_res:
                    res = await coordinator.async_research_wine(wine["id"])
                    self.assertEqual(res["status"], "ok")
                    mock_res.assert_called_once()
                    _, kwargs_res = mock_res.call_args
                    self.assertEqual(kwargs_res.get("provider"), "claude")
                    self.assertEqual(kwargs_res.get("api_key"), "sk-ant-active-key")

                # 3. async_sommelier_chat passes provider='claude'
                with patch("custom_components.homecave.coordinator.async_sommelier_dish_pairing", return_value={"status": "ok", "pairing": {"response": "Pairing advice"}, "response": "Pairing advice"}) as mock_somm:
                    somm_res = await coordinator.async_sommelier_chat(query_text="Lamb chops")
                    self.assertEqual(somm_res["response"], "Pairing advice")
                    mock_somm.assert_called_once()
                    _, kwargs_somm = mock_somm.call_args
                    self.assertEqual(kwargs_somm.get("provider"), "claude")
                    self.assertEqual(kwargs_somm.get("api_key"), "sk-ant-active-key")

                # 4. async_enrich_bottle passes provider='claude'
                with patch("custom_components.homecave.coordinator.async_enrich_vivino_bottle", return_value={"status": "ok", "enrichment": {"alcohol_pct": 14.0}}) as mock_enrich:
                    enrich_res = await coordinator.async_enrich_bottle(wine["id"])
                    self.assertEqual(enrich_res["status"], "ok")
                    mock_enrich.assert_called_once()
                    _, kwargs_enrich = mock_enrich.call_args
                    self.assertEqual(kwargs_enrich.get("provider"), "claude")

            asyncio.run(_coordinator_run())

    def test_v055_claude_temperature_and_sampling_params_stripped(self) -> None:
        """v0.5.5: Verify deprecated sampling parameters (temperature, top_p, top_k) are stripped from Claude requests."""
        class _CapturingSession:
            def __init__(self) -> None:
                self.captured_json: dict[str, Any] = {}

            def post(self, url: str, **kwargs: Any) -> Any:
                self.captured_json = dict(kwargs.get("json") or {})

                class _Resp:
                    async def text(self) -> str:
                        return json.dumps({
                            "content": [{"type": "text", "text": '{"winery": "Latour", "is_wine_bottle_or_label": true, "summary": "ok", "choice": "NONE", "pairing": {}, "alcohol_pct": 13.0}'}]
                        })
                    @property
                    def status(self) -> int:
                        return 200
                    async def json(self, content_type: str | None = None) -> Any:
                        return json.loads(await self.text())
                    async def __aenter__(self) -> Any:
                        return self
                    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
                        return None

                return _Resp()

        async def _run() -> None:
            # 1. Direct _send_claude_messages_request with caller-provided sampling parameters
            session_direct = _CapturingSession()
            raw_payload = {
                "model": "claude-haiku-5-5",
                "max_tokens": 1000,
                "temperature": 0.2,
                "top_p": 0.95,
                "top_k": 50,
                "messages": [{"role": "user", "content": "Hello"}],
            }
            await _send_claude_messages_request(
                session_direct,  # type: ignore[arg-type]
                api_key="sk-ant-test",
                payload=raw_payload,
            )
            self.assertNotIn("temperature", session_direct.captured_json)
            self.assertNotIn("top_p", session_direct.captured_json)
            self.assertNotIn("top_k", session_direct.captured_json)
            self.assertEqual(session_direct.captured_json["model"], "claude-haiku-5-5")

            # 2. async_scan_bottle_label_claude
            session_scan = _CapturingSession()
            await async_scan_bottle_label_claude(
                session_scan,  # type: ignore[arg-type]
                api_key="sk-ant-test",
                image_bytes=b"dummy",
                model="claude-haiku-5-5",
            )
            self.assertNotIn("temperature", session_scan.captured_json)
            self.assertNotIn("top_p", session_scan.captured_json)
            self.assertNotIn("top_k", session_scan.captured_json)

            # 3. async_judge_vivino_candidates_claude
            session_judge = _CapturingSession()
            await async_judge_vivino_candidates_claude(
                session_judge,  # type: ignore[arg-type]
                api_key="sk-ant-test",
                image_bytes=b"dummy",
                mime_type="image/jpeg",
                label_text_verbatim="Latour",
                candidates=[{"winery": "Latour", "name": "Grand Vin"}],
                model="claude-haiku-5-5",
            )
            self.assertNotIn("temperature", session_judge.captured_json)

            # 4. async_research_wine_with_claude
            session_res = _CapturingSession()
            await async_research_wine_with_claude(
                session_res,  # type: ignore[arg-type]
                api_key="sk-ant-test",
                wine_data={"winery": "Latour"},
                model="claude-haiku-5-5",
            )
            self.assertNotIn("temperature", session_res.captured_json)

            # 5. async_sommelier_dish_pairing_claude
            session_somm = _CapturingSession()
            await async_sommelier_dish_pairing_claude(
                session_somm,  # type: ignore[arg-type]
                api_key="sk-ant-test",
                dish_description="Steak",
                cellar_wines=[],
                model="claude-haiku-5-5",
            )
            self.assertNotIn("temperature", session_somm.captured_json)

            # 6. async_enrich_vivino_bottle_claude
            session_enrich = _CapturingSession()
            await async_enrich_vivino_bottle_claude(
                session_enrich,  # type: ignore[arg-type]
                api_key="sk-ant-test",
                wine_data={"winery": "Latour"},
                model="claude-haiku-5-5",
            )
            self.assertNotIn("temperature", session_enrich.captured_json)

        asyncio.run(_run())

    def test_v056_claude_json_parsing_and_token_limits(self) -> None:
        """v0.5.6: Verify Claude JSON extraction handles unescaped newlines, trailing commas, and max_tokens=4096."""
        # 1. Direct _extract_json_from_text unit tests
        # Test case A: Unescaped literal newlines in multi-line narrative string (Claude research format)
        raw_research_text = '{\n  "producer_history": "Verification caveat: I could not confirm a documented history for a producer named Domaine des Chênes in Vingrau.\n\nHere is additional context on the producer and terroir.",\n  "summary": "Rich southern French red."\n}'
        parsed_research = _extract_json_from_text(raw_research_text)
        self.assertIn("Domaine des Chênes", parsed_research["producer_history"])
        self.assertEqual(parsed_research["summary"], "Rich southern French red.")

        # Test case B: Unescaped literal newlines in dish analysis with markdown code fences and trailing commas
        raw_somm_text = '```json\n{\n  "dish_name": "Grilled Ribeye Steak with Garlic Herb Butter",\n  "dish_analysis": "A well-marbled ribeye grilled over high heat gives a deeply savory crust with Maillard and char notes,\nwhile the interior remains juicy and rich.",\n  "recommended_bottles": [\n    {"wine_name": "Cabernet Sauvignon", "reason": "Tannins cut through the rich marbling.", },\n  ],\n}\n```'
        parsed_somm = _extract_json_from_text(raw_somm_text)
        self.assertEqual(parsed_somm["dish_name"], "Grilled Ribeye Steak with Garlic Herb Butter")
        self.assertIn("Maillard", parsed_somm["dish_analysis"])
        self.assertEqual(len(parsed_somm["recommended_bottles"]), 1)

        # 2. Async Claude methods with multi-line narrative responses and max_tokens=4096
        class _CapturingClaudeSession:
            def __init__(self, response_text: str) -> None:
                self.response_text = response_text
                self.captured_json: dict[str, Any] = {}

            def post(self, url: str, **kwargs: Any) -> Any:
                self.captured_json = dict(kwargs.get("json") or {})

                class _Resp:
                    def __init__(self, text_payload: str) -> None:
                        self.text_payload = text_payload
                    async def text(self) -> str:
                        return json.dumps({
                            "content": [{"type": "text", "text": self.text_payload}]
                        })
                    @property
                    def status(self) -> int:
                        return 200
                    async def json(self, content_type: str | None = None) -> Any:
                        return json.loads(await self.text())
                    async def __aenter__(self) -> Any:
                        return self
                    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
                        return None

                return _Resp(self.response_text)

        async def _run() -> None:
            # Verify research wine with multi-line Claude output
            session_res = _CapturingClaudeSession(raw_research_text)
            res = await async_research_wine_with_claude(
                session_res,  # type: ignore[arg-type]
                api_key="sk-ant-test",
                wine_data={"winery": "Domaine des Chênes", "name": "Razungles"},
                model="claude-haiku-5-5",
            )
            self.assertEqual(res["status"], "ok")
            self.assertEqual(session_res.captured_json["max_tokens"], 4096)
            self.assertIn("producer_history", res["research"])

            # Verify sommelier pairing with multi-line Claude output
            session_somm = _CapturingClaudeSession(raw_somm_text)
            somm_res = await async_sommelier_dish_pairing_claude(
                session_somm,  # type: ignore[arg-type]
                api_key="sk-ant-test",
                dish_description="Ribeye Steak",
                cellar_wines=[{"id": 1, "winery": "Penfolds", "name": "Bin 389", "vintage": 2018}],
                model="claude-haiku-5-5",
            )
            self.assertEqual(somm_res["status"], "ok")
            self.assertEqual(session_somm.captured_json["max_tokens"], 4096)
            self.assertEqual(somm_res["pairing"]["dish_name"], "Grilled Ribeye Steak with Garlic Herb Butter")

        asyncio.run(_run())

    def test_v091_sommelier_prompt_and_scanner_branding(self) -> None:
        """v0.9.1: Verify SommAI prompt uses 'HomeCave - your local Wine Cellar' and error hints say HomeCave."""
        from custom_components.homecave.scanner import SOMMELIER_PAIRING_PROMPT
        import inspect
        import custom_components.homecave.scanner as scanner_mod

        self.assertIn("HomeCave - your local Wine Cellar", SOMMELIER_PAIRING_PROMPT)
        scanner_src = inspect.getsource(scanner_mod)
        self.assertIn("in HomeCave options.", scanner_src)


if __name__ == "__main__":
    unittest.main()
