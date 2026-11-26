"""Unit tests for SommAI A2UI block validation, DB hydration, cover signing, and frontend wiring."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from custom_components.homecave.const import DOMAIN, SOMMAI_A2UI_JS_FILENAME
from custom_components.homecave.coordinator import (
    WineInventoryCoordinator,
    validate_sommelier_pairing,
)
from custom_components.homecave.covers import CoverUrlSigner, sign_cover_urls

REPO_ROOT = Path(__file__).resolve().parent.parent
INTEGRATION_DIR = REPO_ROOT / "custom_components" / "homecave"
FRONTEND_DIR = INTEGRATION_DIR / "frontend"
MOBILE_DIR = FRONTEND_DIR / "mobile"


class _StubConfig:
    def __init__(self, root: str) -> None:
        self._root = Path(root)
        self.config_dir = str(self._root)

    def path(self, *parts: str) -> str:
        return str(self._root.joinpath(*parts))


class _StubBus:
    def async_fire(self, event_type: str, event_data: dict) -> None:
        pass


class _StubHass:
    def __init__(self, root: str) -> None:
        self.config = _StubConfig(root)
        self.bus = _StubBus()
        self.states: dict = {}
        self.data: dict = {}

    async def async_add_executor_job(self, func, *args):
        return func(*args)


class TestSommAIA2UI(unittest.IsolatedAsyncioTestCase):
    """Verify A2UI allowlist validation, DB hydration, fallback, and frontend integration."""

    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.hass = _StubHass(self._tmpdir.name)
        self.coordinator = WineInventoryCoordinator(
            hass=self.hass,
            entry_id="test_a2ui",
            config={"cellar_name": "Test Cellar", "currency": "EUR"},
        )
        await self.coordinator.async_initialize()
        self.hass.data[DOMAIN] = {"test_a2ui": {"coordinator": self.coordinator}}

    async def asyncTearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_validate_a2ui_blocks_allowlist_and_hydration(self) -> None:
        """Valid 7-block A2UI payload is sanitized, unknown types dropped, and bottle_card hydrated."""
        wines_by_id = {
            7: {
                "id": 7,
                "winery": "Giacomo Conterno",
                "name": "Barolo Cascina Francia",
                "vintage": 2018,
                "wine_type": "red",
                "varietal": "Nebbiolo",
                "region": "Piedmont",
                "country": "Italy",
                "quantity": 3,
                "storage_location": "Rack A1",
                "drink_status": "ready",
                "vivino_rating": 4.6,
                "image_url": "/api/homecave/cover/barolo_7.jpg",
            }
        }
        raw_pairing = {
            "dish_name": "Truffle Risotto",
            "conversational_response": "Pair your Barolo with the risotto.",
            "recommended_wines": [
                {
                    "wine_id": 7,
                    "wine_title": "Ignored LLM Title",
                    "match_type": "Best Match",
                    "sommelier_notes": "High acidity cuts through butter.",
                    "serving_temp_c": "16-18°C",
                    "decant_minutes": 60,
                }
            ],
            "a2ui_blocks": [
                {"type": "script_injection", "body": "<script>alert(1)</script>"},
                {"type": "header", "title": "Truffle Risotto Pairing", "subtitle": "Rich & earthy", "badge": "Dish Pairing"},
                {"type": "callout", "tone": "pairing", "title": "Strategy", "body": "Match earthy Nebbiolo."},
                {"type": "bottle_card", "wine_id": 7, "match_type": "Best Match", "reason": "Ideal acidity", "serving_temp_c": "16-18°C", "decant_minutes": 999},
                {"type": "bottle_card", "wine_id": 999, "wine_title": "Out of Stock Bottle", "reason": "Should unlink"},
                {"type": "comparison_table", "title": "Comparison", "columns": ["Attribute", "Barolo"], "rows": [{"label": "Vintage", "values": ["2018"]}]},
                {"type": "taste_match", "title": "Match", "metrics": [{"label": "Acidity", "dish_level": 150, "wine_level": -10, "note": "Crisp"}]},
                {"type": "serving_guide", "wine_id": 7, "serving_temp_c": "17°C", "decant_minutes": 45, "glassware": "Burgundy bowl", "steps": ["Decant 45m"]},
                {"type": "follow_up_chips", "chips": [{"label": "Lighter white", "prompt": "Suggest a lighter white"}]},
            ],
        }

        clean = validate_sommelier_pairing(raw_pairing, {7}, wines_by_id=wines_by_id)
        blocks = clean["a2ui_blocks"]
        types = [b["type"] for b in blocks]
        self.assertNotIn("script_injection", types)
        self.assertEqual(
            types,
            [
                "header",
                "callout",
                "bottle_card",
                "bottle_card",
                "comparison_table",
                "taste_match",
                "serving_guide",
                "follow_up_chips",
            ],
        )

        # Hydrated bottle_card uses DB ground truth and clamps decant_minutes
        hydrated_card = blocks[2]
        self.assertEqual(hydrated_card["wine_id"], 7)
        self.assertEqual(hydrated_card["wine_title"], "Giacomo Conterno – Barolo Cascina Francia (2018)")
        self.assertEqual(hydrated_card["storage_location"], "Rack A1")
        self.assertEqual(hydrated_card["quantity"], 3)
        self.assertEqual(hydrated_card["image_url"], "/api/homecave/cover/barolo_7.jpg")
        self.assertEqual(hydrated_card["decant_minutes"], 240)

        # Out-of-stock wine_id is stripped to None
        unlinked_card = blocks[3]
        self.assertIsNone(unlinked_card["wine_id"])

        # Taste match levels clamped to [0, 100]
        taste_metric = blocks[5]["metrics"][0]
        self.assertEqual(taste_metric["dish_level"], 100)
        self.assertEqual(taste_metric["wine_level"], 0)

    def test_validate_a2ui_fallback_when_blocks_missing(self) -> None:
        """Missing or invalid a2ui_blocks synthesizes a safe callout + bottle_card fallback."""
        clean = validate_sommelier_pairing(
            {
                "dish_name": "Steak",
                "conversational_response": "Open a bold Cabernet.",
                "recommended_wines": [
                    {"wine_id": None, "wine_title": "House Cab", "match_type": "Best Match", "sommelier_notes": "Bold"}
                ],
                "a2ui_blocks": "invalid",
            },
            set(),
            wines_by_id={},
        )
        self.assertEqual(len(clean["a2ui_blocks"]), 2)
        self.assertEqual(clean["a2ui_blocks"][0]["type"], "callout")
        self.assertEqual(clean["a2ui_blocks"][1]["type"], "bottle_card")

    def test_sign_cover_urls_signs_nested_a2ui_bottle_card(self) -> None:
        """Private cover image_url inside a2ui_blocks is recursively signed."""
        signer = CoverUrlSigner(self.hass)
        # Stub async_sign_path behavior via direct sign cache check
        payload = {
            "a2ui_blocks": [
                {"type": "bottle_card", "wine_id": 7, "image_url": "/api/homecave/cover/barolo_7.jpg"}
            ]
        }
        # Populate cache entry for refresh_token="rt-1"
        signer._cache[("rt-1", "/api/homecave/cover/barolo_7.jpg")] = (
            "/api/homecave/cover/barolo_7.jpg?authSig=test-sig",
            9999999999.0,
        )
        signed = sign_cover_urls(payload, signer, "rt-1")
        self.assertEqual(
            signed["a2ui_blocks"][0]["image_url"],
            "/api/homecave/cover/barolo_7.jpg?authSig=test-sig",
        )

    def test_frontend_a2ui_integration_files(self) -> None:
        """Verify shared sommai-a2ui.js exists and is wired into index.html, app.js, and homecave-card.js."""
        a2ui_js = (FRONTEND_DIR / SOMMAI_A2UI_JS_FILENAME).read_text(encoding="utf-8")
        self.assertIn("HomeCaveSommAIA2UI", a2ui_js)
        self.assertIn("renderBlocks", a2ui_js)
        self.assertIn("comparison_table", a2ui_js)
        self.assertIn("follow_up_chips", a2ui_js)

        html = (MOBILE_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("/homecave_frontend/sommai-a2ui.js", html)

        js_src = (MOBILE_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn("HomeCaveSommAIA2UI.renderBlocks", js_src)
        self.assertIn("data-a2ui-prompt", js_src)

        card_js = (FRONTEND_DIR / "homecave-card.js").read_text(encoding="utf-8")
        self.assertIn('import "./sommai-a2ui.js', card_js)
        self.assertIn("HomeCaveSommAIA2UI.renderBlocks", card_js)
        self.assertIn("data-a2ui-prompt", card_js)


if __name__ == "__main__":
    unittest.main()
