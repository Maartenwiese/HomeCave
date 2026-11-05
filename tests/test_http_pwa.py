"""Unit tests for the standalone HomeCave Mobile PWA HTTP API and card editors."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from custom_components.homecave.const import DOMAIN, MOBILE_APP_URL_BASE, VERSION

# Admin-only PWA actions (backup_create, import, delete) require an admin user since v0.9.3
_ADMIN_USER = SimpleNamespace(id="admin-user", name="Admin", is_admin=True, permissions=None)
from custom_components.homecave.coordinator import WineInventoryCoordinator
from custom_components.homecave.http_api import (
    WineInventoryMobileAssetView,
    WineInventoryPwaApiView,
    async_handle_pwa_get,
    async_handle_pwa_post,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
INTEGRATION_DIR = REPO_ROOT / "custom_components" / "homecave"
MOBILE_DIR = INTEGRATION_DIR / "frontend" / "mobile"


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


class TestMobilePwaAndCards(unittest.IsolatedAsyncioTestCase):
    """Test the standalone mobile PWA REST backend, manifest, and card registrations."""

    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.hass = _StubHass(self._tmpdir.name)
        self.coordinator = WineInventoryCoordinator(
            hass=self.hass,
            entry_id="test_entry",
            config={"cellar_name": "Test Cellar", "currency": "EUR"},
        )
        await self.coordinator.async_initialize()
        self.hass.data[DOMAIN] = {"test_entry": {"coordinator": self.coordinator}}

    async def asyncTearDown(self) -> None:
        self._tmpdir.cleanup()

    async def test_pwa_lifecycle_endpoints(self) -> None:
        """Verify state, add, detail, adjust, update, unlink_vivino, and delete via HTTP API."""
        status, state = await async_handle_pwa_get(self.hass, "state", {})
        self.assertEqual(status, 200)
        self.assertEqual(state["version"], VERSION)
        self.assertEqual(state["cellar_name"], "Test Cellar")
        self.assertEqual(state["statistics"]["total_bottles"], 0)
        self.assertIn("HomeCave", state["areas"])
        self.assertIn("HomeCave Fridge", state["areas"])

        # Add a bottle via POST /api/homecave/pwa/add
        status, added = await async_handle_pwa_post(
            self.hass,
            "add",
            {
                "winery": "Domaine Tempier",
                "name": "Bandol Rosé",
                "vintage": 2023,
                "wine_type": "rose",
                "varietal": "Mourvèdre, Grenache, Cinsault",
                "country": "France",
                "region": "Provence",
                "quantity": 3,
                "storage_location": "HomeCave Fridge",
                "purchase_location": "Chabrol Wines",
                "purchase_price": 38.5,
                "vivino_rating": 4.3,
                "vivino_url": "https://www.vivino.com/w/12345?year=2023",
            },
        )
        self.assertEqual(status, 200)
        wine_id = added["wine"]["id"]
        self.assertEqual(added["statistics"]["total_bottles"], 3)

        # Fetch detail via GET /api/homecave/pwa/detail
        status, detail = await async_handle_pwa_get(
            self.hass, "detail", {"wine_id": str(wine_id)}
        )
        self.assertEqual(status, 200)
        self.assertEqual(detail["wine"]["name"], "Bandol Rosé")
        self.assertEqual(detail["wine"]["quantity"], 3)

        # Adjust bottle count (-1) via POST /api/homecave/pwa/adjust
        status, adjusted = await async_handle_pwa_post(
            self.hass, "adjust", {"wine_id": wine_id, "delta": -1}
        )
        self.assertEqual(status, 200)
        self.assertEqual(adjusted["wine"]["quantity"], 2)

        # Update metadata via POST /api/homecave/pwa/update
        status, updated = await async_handle_pwa_post(
            self.hass,
            "update",
            {
                "wine_id": wine_id,
                "updates": {
                    "storage_location": "HomeCave",
                    "purchase_price": 41.0,
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["wine"]["storage_location"], "HomeCave")
        self.assertEqual(updated["wine"]["purchase_price"], 41.0)

        # Unlink Vivino via POST /api/homecave/pwa/unlink_vivino
        status, unlinked = await async_handle_pwa_post(
            self.hass, "unlink_vivino", {"wine_id": wine_id}
        )
        self.assertEqual(status, 200)
        self.assertEqual(unlinked["wine"]["vivino_url"], "")
        self.assertIsNone(unlinked["wine"]["vivino_rating"])

        # Delete wine via POST /api/homecave/pwa/delete
        status, deleted = await async_handle_pwa_post(
            self.hass, "delete", {"wine_id": wine_id}, user=_ADMIN_USER
        )
        self.assertEqual(status, 200)
        self.assertTrue(deleted["deleted"])
        self.assertEqual(deleted["statistics"]["total_bottles"], 0)

    def test_mobile_pwa_manifest_and_assets_compliance(self) -> None:
        """Verify PWA manifest and assets meet installable standalone standards."""
        self.assertEqual(MOBILE_APP_URL_BASE, "/homecave")
        manifest_path = MOBILE_DIR / "manifest.webmanifest"
        self.assertTrue(manifest_path.exists())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["id"], "/homecave/")
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["start_url"], "/homecave/index.html")
        self.assertEqual(manifest["scope"], "/homecave/")
        self.assertLessEqual(len(manifest["short_name"]), 12)

        icon_sizes = {i["sizes"] for i in manifest["icons"]}
        self.assertIn("192x192", icon_sizes)
        self.assertIn("512x512", icon_sizes)
        purposes = {i["purpose"] for i in manifest["icons"]}
        self.assertIn("any", purposes)
        self.assertIn("maskable", purposes)

        for asset in (
            "index.html",
            "app.js",
            "styles.css",
            "sw.js",
            "icon-192.png",
            "icon-512.png",
            "icon-maskable-512.png",
            "apple-touch-icon.png",
            "icon.svg",
        ):
            self.assertTrue(
                (MOBILE_DIR / asset).exists(),
                f"Expected mobile PWA asset missing: {asset}",
            )

        html = (MOBILE_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('rel="manifest" href="/homecave/manifest.webmanifest"', html)
        self.assertIn('rel="apple-touch-icon" href="/homecave/apple-touch-icon.png"', html)
        self.assertIn('name="apple-mobile-web-app-capable" content="yes"', html)

    def test_lovelace_cards_and_detailed_view_text_sizing(self) -> None:
        """Verify custom:wine-inventory-card and custom:wine-cellar-summary-card registrations and detail scaling."""
        card_js = (INTEGRATION_DIR / "frontend" / "homecave-card.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("static getStubConfig()", card_js)
        self.assertIn("static getConfigElement()", card_js)
        self.assertIn("wine-inventory-card-editor", card_js)
        self.assertIn("wine-cellar-summary-card", card_js)
        self.assertIn("wine-cellar-summary-card-editor", card_js)
        self.assertIn("btn-detail-font-scale", card_js)
        self.assertIn(".modal-inner", card_js)
        self.assertIn("@container lsmodal", card_js)
        # v0.9.3: the card must no longer copy HA auth tokens into localStorage
        self.assertNotIn('setItem("homecave_ha_tokens"', card_js)
        self.assertNotIn("_syncHaAuthTokens", card_js)
        self.assertIn("bottle-library-input", card_js)

    async def test_v031_mobile_auth_font_list_scroll_and_library(self) -> None:
        """Verify v0.3.1 HA authentication inheritance, L default font, list icons, scroll fix, and library upload."""
        self.assertTrue(WineInventoryPwaApiView.requires_auth)
        self.assertEqual(WineInventoryMobileAssetView.url, "/homecave/{filename}")
        for route in (
            "/homecave",
            "/homecave/",
            "/api/homecave/mobile",
            "/api/homecave/mobile/",
            "/api/homecave/mobile/{filename}",
        ):
            self.assertIn(route, WineInventoryMobileAssetView.extra_urls)

        status, state = await async_handle_pwa_get(
            self.hass,
            "state",
            {},
            authenticated_user={"id": "ha_user_1", "name": "Maarten"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(state["authenticated_user"]["name"], "Maarten")

        app_js = (MOBILE_DIR / "app.js").read_text(encoding="utf-8")
        styles_css = (MOBILE_DIR / "styles.css").read_text(encoding="utf-8")

        # 1. Home Assistant authentication inheritance
        self.assertIn("hassTokens", app_js)
        self.assertIn("homecave_ha_tokens", app_js)
        self.assertIn("/auth/token", app_js)
        self.assertIn("/auth/authorize", app_js)
        self.assertIn("Bearer ${token}", app_js)

        # 2. Default font size L (1.15)
        self.assertIn("--ls-app-scale: 1.15;", styles_css)
        self.assertIn("homecave_mobile_font_scale", app_js)
        self.assertIn("val >= 0.8 && val <= 1.5 ? val : 1.15;", app_js)

        # 3. Richer mobile list view with grape and bought date icons
        self.assertIn("bottle-detail-chips", app_js)
        self.assertIn("🍇 ${esc(w.varietal)}", app_js)
        self.assertIn("🗓️ ${esc(boughtDateLabel)}", app_js)
        self.assertIn(".bottle-detail-chips", styles_css)
        self.assertIn(".meta-chip", styles_css)

        # 4. Scrollable Detailed sheet structure (.sheet-body wraps .scaled-zone with min-height: 0)
        self.assertIn('id="detail-sheet-body"', app_js)
        self.assertIn("min-height: 0;", styles_css)
        self.assertIn("overflow-y: auto;", styles_css)
        self.assertIn("overscroll-behavior: contain;", styles_css)

        # 4b. Real asset view serves every mobile file (regression: HTTP 500 when
        # charset was passed inside content_type to aiohttp web.Response).
        from aiohttp import web as aio_web
        from aiohttp.test_utils import make_mocked_request

        asset_view = WineInventoryMobileAssetView(self.hass)
        for fname, mime in (
            ("index.html", "text/html"),
            ("index2.html", "text/html"),
            ("app.js", "application/javascript"),
            ("app2.js", "application/javascript"),
            ("styles.css", "text/css"),
            ("styles2.css", "text/css"),
            ("sw.js", "application/javascript"),
            ("manifest.webmanifest", "application/manifest+json"),
            ("icon-192.png", "image/png"),
        ):
            req = make_mocked_request("GET", f"/homecave/{fname}")
            resp = await asset_view.get(req, fname)
            self.assertEqual(resp.status, 200, fname)
            self.assertEqual(resp.content_type, mime, fname)
        with self.assertRaises(aio_web.HTTPFound):
            await asset_view.get(make_mocked_request("GET", "/homecave"), None)

        # PWA API view resolves hass from the view instance (no request.app["hass"]).
        api_view = WineInventoryPwaApiView(self.hass)
        api_resp = await api_view.get(
            make_mocked_request("GET", "/api/homecave/pwa/state"), "state"
        )
        self.assertEqual(api_resp.status, 200)

        # 5. Photo library selection alongside camera capture
        self.assertIn('id="mobile-scan-file"', app_js)
        self.assertIn('id="mobile-scan-library"', app_js)
        self.assertIn("Choose from Library", app_js)


    def test_hacs_json_follows_publish_guidance(self) -> None:
        """hacs.json uses only documented keys, has a name, and avoids the zip_release that broke installs."""
        hacs = json.loads((REPO_ROOT / "hacs.json").read_text(encoding="utf-8"))
        allowed = {
            "name",
            "content_in_root",
            "zip_release",
            "filename",
            "hide_default_branch",
            "country",
            "homeassistant",
            "hacs",
            "persistent_directory",
        }
        self.assertTrue(set(hacs).issubset(allowed), set(hacs) - allowed)
        self.assertEqual(hacs["name"], "HomeCave")
        self.assertFalse(hacs.get("content_in_root", False))
        self.assertNotIn("zip_release", hacs)
        self.assertNotIn("render_readme", hacs)
        self.assertRegex(hacs["homeassistant"], r"^\d{4}\.\d{1,2}\.\d+$")
        manifest = json.loads((INTEGRATION_DIR / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["codeowners"], ["@Maartenwiese"])
        for key in ("domain", "name", "categories", "version", "documentation", "issue_tracker", "codeowners"):
            self.assertIn(key, manifest)

    def test_v033_mobile_touch_first_stats_config_and_crop(self) -> None:
        """Stats/Config tabs, touch targets, touch crop, confirm sheet, toast, scroll preservation."""
        app_js = (MOBILE_DIR / "app.js").read_text(encoding="utf-8")
        css = (MOBILE_DIR / "styles.css").read_text(encoding="utf-8")

        # Navigation renamed (v0.5.0 replaced stats tab with sommai tab)
        self.assertTrue('data-nav="sommai"' in app_js or 'data-nav="stats"' in app_js)
        self.assertIn('data-nav="config"', app_js)
        self.assertTrue("<span>SommAI</span>" in app_js or "<span>Stats</span>" in app_js)
        self.assertIn("<span>Config</span>", app_js)
        self.assertNotIn("<span>Areas</span>", app_js)
        self.assertNotIn("<span>App</span>", app_js)
        self.assertIn("⚙️", app_js)

        # Expanded statistics
        for label in ("Avg. Price / Bottle", "Top Countries", "Top Grapes", "Top Stores",
                      "Drinking Window", "Highest rated", "Drink first", "Bottles added (30 days)"):
            self.assertIn(label, app_js)

        # Larger touch targets
        self.assertIn("min-height: 48px;", css)
        self.assertIn("touch-action: manipulation;", css)
        self.assertNotRegex(app_js, r"min-height:\s*[0-3]\dpx")

        # Touch-native crop & rotate
        for token in ('addEventListener("pointerdown"', 'addEventListener("pointermove"',
                      "setPointerCapture", 'id="crop-fine-slider"', "_cropGeometry", "devicePixelRatio"):
            self.assertIn(token, app_js)
        self.assertIn("touch-action: none; /* finger drags", css)

        # No blocking native dialogs; toast doesn't re-render
        self.assertNotIn("window.confirm(", app_js)
        self.assertNotIn("alert(", app_js)
        self.assertIn("confirmAction(", app_js)
        toast_body = app_js.split("showToast(msg, kind")[1].split("showError(")[0]
        self.assertNotIn("this.render()", toast_body)

        # Scroll preservation, form binding, numeric keyboards, pull-to-refresh
        self.assertIn("_lastRenderedSheet", app_js)
        self.assertIn("_bindFormState", app_js)
        self.assertIn('inputmode="numeric"', app_js)
        self.assertIn('inputmode="decimal"', app_js)
        self.assertIn("ptr-indicator", app_js)
        self.assertIn("calc(16px / var(--ls-app-scale", css)


    def test_v034_scan_contract_header_splash_and_back(self) -> None:
        """Mobile scan mapping uses the real coordinator response keys; header removed; splash; back nav."""
        import re

        app_js = (MOBILE_DIR / "app.js").read_text(encoding="utf-8")
        html = (MOBILE_DIR / "index.html").read_text(encoding="utf-8")
        coord = (INTEGRATION_DIR / "coordinator.py").read_text(encoding="utf-8")

        # Contract: every key the mobile app reads from the scan response must be
        # returned by coordinator.async_scan_bottle (regression: it read `recognized`).
        scan_fn = coord.split("async def async_scan_bottle")[1].split("async def ")[0]
        returned = set(re.findall(r'"([a-z_]+)":', scan_fn))
        mapper = app_js.split("_applyScanResult(res) {")[1].split("_selectScanCandidate(idx) {")[0]
        read_keys = set(re.findall(r"res\?\.([a-z_]+)", mapper))
        self.assertTrue(read_keys, "scan mapper reads no keys")
        self.assertTrue(read_keys.issubset(returned), read_keys - returned)
        self.assertIn("extracted", read_keys)
        self.assertNotIn("res.recognized", app_js)
        self.assertIn("this._applyScanResult(res)", app_js)

        # Header removed; Aa removed from sheets; back buttons instead of ✕
        self.assertNotIn('class="top-app-bar"', app_js)
        self.assertNotIn("btn-sheet-font-scale", app_js)
        self.assertNotIn("btn-top-font-scale\" title", app_js)
        self.assertIn("btn-back", app_js)
        self.assertNotRegex(app_js, r'id="btn-close-sheet"[^>]*>✕')
        self.assertIn('addEventListener("popstate"', app_js)
        self.assertIn("history.pushState", app_js)

        # Splash: static HomeCave logo + tagline, 2 seconds, once per session
        self.assertIn('id="hc-splash"', html)
        self.assertIn("/homecave/logo-512.png", html)
        self.assertIn("HomeCave - your local Wine Cellar", html)
        # v0.9.3: splash logic lives in external splash.js (CSP script-src 'self')
        splash_js = (MOBILE_DIR / "splash.js").read_text(encoding="utf-8")
        self.assertIn('src="/homecave/splash.js', html)
        self.assertIn("2000", splash_js)
        self.assertIn("sessionStorage", splash_js)

    async def test_v035_float_bug_splash_version_fullscreen_stepper_and_autosize(self) -> None:
        """v0.3.5: empty-string float bug fixed, splash shows version, full-screen pages, no cellar +/- stepper, auto-scaling notes."""
        # 1. Saving a wine from the mobile scan form with empty strings "" in numeric fields
        # must succeed AND all subsequent DB updates must succeed without ValueError.
        status, added = await async_handle_pwa_post(
            self.hass,
            "add",
            {
                "winery": "Cloudy Bay",
                "name": "Sauvignon Blanc",
                "vintage": "",
                "wine_type": "white",
                "quantity": 2,
                "vivino_rating": "",
                "vivino_price_estimate": "",
                "purchase_price": "",
                "drink_from_year": "",
                "drink_until_year": "",
            },
        )
        self.assertEqual(status, 200)
        wine_id = added["wine"]["id"]
        self.assertIsNone(added["wine"]["vivino_price_estimate"])
        self.assertIsNone(added["wine"]["purchase_price"])

        # Subsequent DB updates (update, adjust) must also succeed without float('') error
        status, updated = await async_handle_pwa_post(
            self.hass,
            "update",
            {
                "wine_id": wine_id,
                "updates": {
                    "general_notes": "Crisp passionfruit and lime.",
                    "purchase_price": "",
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["wine"]["general_notes"], "Crisp passionfruit and lime.")

        status, adjusted = await async_handle_pwa_post(
            self.hass, "adjust", {"wine_id": wine_id, "delta": 1}
        )
        self.assertEqual(status, 200)
        self.assertEqual(adjusted["wine"]["quantity"], 3)

        app_js = (MOBILE_DIR / "app.js").read_text(encoding="utf-8")
        html = (MOBILE_DIR / "index.html").read_text(encoding="utf-8")
        css = (MOBILE_DIR / "styles.css").read_text(encoding="utf-8")

        # 2. Splash screen shows version in small font
        self.assertIn("hc-splash-version", html)
        self.assertIn(f"v{VERSION}", html)

        # 3. Full-screen pages (no bottom-sheet overlay handle, 100dvh surface, border-radius: 0)
        self.assertNotIn('<div class="sheet-handle"></div>', app_js)
        self.assertIn("height: 100dvh;", css)
        self.assertIn("border-radius: 0;", css)

        # 4. Main Cellar list removed +/- stepper buttons, while Details still has +/- buttons
        bottle_list_fn = app_js.split("_renderBottleListItems(wines")[1].split("_renderCellarView() {")[0]
        self.assertNotIn("data-step-minus", bottle_list_fn)
        self.assertNotIn("data-step-plus", bottle_list_fn)
        self.assertIn("pill-qty", bottle_list_fn)
        self.assertIn('id="btn-detail-minus"', app_js)
        self.assertIn('id="btn-detail-plus"', app_js)

        # 5. Master Wine Notes textarea in Details scales automatically with text
        self.assertIn('id="detail-master-notes"', app_js)
        self.assertIn("_autoResizeTextarea", app_js)
        self.assertIn("_autoResizeTextareas", app_js)
        self.assertIn("_sanitizeWinePayload", app_js)

    async def test_v036_security_headers_sw_scope_admin_rbac_and_download_view(self) -> None:
        """v0.3.6: Verify CSP/security headers, narrowed SW scope, private download endpoint RBAC, and WS admin guards."""
        from aiohttp.test_utils import make_mocked_request
        from custom_components.homecave.http_api import WineInventoryDownloadView
        from custom_components.homecave.websocket_api import (
            ws_create_backup,
            ws_import_wines,
            ws_list_backups,
            ws_restore_backup,
        )

        # 1. Asset view sets CSP, X-Content-Type-Options, Referrer-Policy, X-Frame-Options, and narrowed SW scope
        asset_view = WineInventoryMobileAssetView(self.hass)
        idx_resp = await asset_view.get(make_mocked_request("GET", "/homecave/index.html"), "index.html")
        self.assertEqual(idx_resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(idx_resp.headers.get("Referrer-Policy"), "same-origin")
        self.assertEqual(idx_resp.headers.get("X-Frame-Options"), "SAMEORIGIN")
        self.assertIn("default-src 'self'", idx_resp.headers.get("Content-Security-Policy", ""))

        sw_resp = await asset_view.get(make_mocked_request("GET", "/homecave/sw.js"), "sw.js")
        self.assertEqual(sw_resp.headers.get("Service-Worker-Allowed"), "/homecave/")

        # 2. Create backup and verify download view enforces admin for backups and blocks traversal
        backup_meta = await self.coordinator.async_create_backup()
        dl_view = WineInventoryDownloadView(self.hass)
        self.assertTrue(dl_view.requires_auth)

        class _StubUser:
            def __init__(self, is_admin: bool) -> None:
                self.is_admin = is_admin
                self.id = "u1"
                self.name = "Tester"

        # Non-admin denied backup download (HTTP 403)
        req_non_admin = make_mocked_request("GET", backup_meta["download_url"])
        req_non_admin["hass_user"] = _StubUser(is_admin=False)
        resp_forbidden = await dl_view.get(req_non_admin, "backup", backup_meta["filename"])
        self.assertEqual(resp_forbidden.status, 403)

        # Admin allowed backup download (HTTP 200 with Content-Disposition attachment)
        req_admin = make_mocked_request("GET", backup_meta["download_url"])
        req_admin["hass_user"] = _StubUser(is_admin=True)
        resp_ok = await dl_view.get(req_admin, "backup", backup_meta["filename"])
        self.assertEqual(resp_ok.status, 200)
        self.assertIn("attachment", resp_ok.headers.get("Content-Disposition", ""))

        # Path traversal attempt rejected (HTTP 403)
        resp_traversal = await dl_view.get(req_admin, "backup", "../homecave.db")
        self.assertEqual(resp_traversal.status, 403)

        # 3. WebSocket backup/restore/import commands reject non-admin users
        class _StubWSConn:
            def __init__(self, is_admin: bool) -> None:
                self.user = _StubUser(is_admin=is_admin)
                self.errors: list[tuple[int, str, str]] = []
                self.results: list[tuple[int, dict]] = []

            def send_error(self, msg_id: int, code: str, message: str) -> None:
                self.errors.append((msg_id, code, message))

            def send_result(self, msg_id: int, payload: dict) -> None:
                self.results.append((msg_id, payload))

        non_admin_conn = _StubWSConn(is_admin=False)
        await ws_create_backup(self.hass, non_admin_conn, {"id": 1, "type": "homecave/create_backup"})
        await ws_restore_backup(
            self.hass,
            non_admin_conn,
            {"id": 2, "type": "homecave/restore_backup", "backup_filename": backup_meta["filename"]},
        )
        await ws_import_wines(
            self.hass,
            non_admin_conn,
            {"id": 3, "type": "homecave/import_wines", "payload": [], "format_type": "json"},
        )
        await ws_list_backups(self.hass, non_admin_conn, {"id": 4, "type": "homecave/list_backups"})
        self.assertEqual(len(non_admin_conn.errors), 4)
        for _, err_code, _ in non_admin_conn.errors:
            self.assertEqual(err_code, "unauthorized")

        # 4. Frontend SW and URL sanitizers
        sw_js = (MOBILE_DIR / "sw.js").read_text(encoding="utf-8")
        app_js = (MOBILE_DIR / "app.js").read_text(encoding="utf-8")
        card_js = (INTEGRATION_DIR / "frontend" / "homecave-card.js").read_text(encoding="utf-8")
        self.assertNotIn("cache.put(event.request, clone)", sw_js.split('url.pathname.startsWith("/api/")')[1].split("if (")[0])
        self.assertIn("safeExternalUrl", app_js)
        self.assertIn("safeImageUrl", app_js)
        self.assertIn("safeExternalUrl", card_js)
        self.assertIn("safeImageUrl", card_js)

    async def test_v038_mobile_and_ha_tasted_section_and_scan_to_tasted(self) -> None:
        """v0.3.8: Verify Mobile App Tasted nav & theme, Scan-to-Tasted API & toggle, and HA Card Tasted view."""
        # 1. Scan / add a wine directly to Tasted via Mobile PWA API (as_tasted=True)
        status, res = await async_handle_pwa_post(
            self.hass,
            "add",
            {
                "winery": "Château Palmer",
                "name": "Alter Ego de Palmer",
                "vintage": 2019,
                "wine_type": "red",
                "varietal": "Merlot, Cabernet Sauvignon",
                "as_tasted": True,
                "personal_rating": 4.5,
                "batch_notes": "Tasted at wine bar",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(res["wine"]["quantity"], 0)
        self.assertTrue(res["wine"]["is_tasted"])
        self.assertEqual(res["wine"]["tasted_count"], 1)
        self.assertGreaterEqual(res["statistics"]["tasted_wines"], 1)

        # 2. Verify Mobile App replaces Add icon with Tasted icon, applies .tasted-page-theme, and has Scan destination toggle
        app_js = (MOBILE_DIR / "app.js").read_text(encoding="utf-8")
        css = (MOBILE_DIR / "styles.css").read_text(encoding="utf-8")
        card_js = (INTEGRATION_DIR / "frontend" / "homecave-card.js").read_text(encoding="utf-8")

        self.assertIn('data-nav="tasted"', app_js)
        self.assertNotIn('data-nav="add"', app_js)
        self.assertIn("_renderTastedView()", app_js)
        self.assertIn("tasted-page-theme", app_js)
        self.assertIn(".tasted-page-theme", css)
        self.assertIn('data-scan-dest="tasted"', app_js)
        self.assertIn('data-scan-dest="cellar"', app_js)

        # 3. Verify Home Assistant Card has Tasted tab, _isWineTasted, and Scan destination toggle
        self.assertIn('id: "tasted"', card_js)
        self.assertIn("_isWineTasted(", card_js)
        self.assertIn('data-ha-scan-dest="tasted"', card_js)
        self.assertIn("tasted-card-theme", card_js)

    async def test_v039_mobile_detailed_view_and_vivino_import(self) -> None:
        """v0.3.9: Verify Mobile App Large font default, status-strip removal, detail hero, personal rating slider, and backup/vivino."""
        app_js = (MOBILE_DIR / "app.js").read_text(encoding="utf-8")
        css = (MOBILE_DIR / "styles.css").read_text(encoding="utf-8")
        card_js = (INTEGRATION_DIR / "frontend" / "homecave-card.js").read_text(encoding="utf-8")
        manifest_json = (INTEGRATION_DIR / "manifest.json").read_text(encoding="utf-8")
        hacs_json = (REPO_ROOT / "hacs.json").read_text(encoding="utf-8")

        # 1. Version bumps
        self.assertTrue(
            '"version": "0.3.9"' in manifest_json
            or '"version": "0.4.0"' in manifest_json
            or '"version": "0.5.0"' in manifest_json
            or '"version": "0.5.1"' in manifest_json
            or '"version": "0.5.2"' in manifest_json
            or '"version": "0.5.3"' in manifest_json
            or '"version": "0.5.4"' in manifest_json
            or '"version": "0.5.5"' in manifest_json
            or '"version": "0.5.6"' in manifest_json
            or '"version": "0.5.7"' in manifest_json
            or '"version": "0.9.1"' in manifest_json
            or '"version": "0.9.2"' in manifest_json
            or '"version": "0.9.3"' in manifest_json
            or '"version": "0.9.4"' in manifest_json
            or '"version": "0.9.5"' in manifest_json
            or '"version": "0.9.6"' in manifest_json
            or '"version": "0.9.7"' in manifest_json
        )
        self.assertTrue(
            "v0.3.9" in app_js
            or "v0.4.0" in app_js
            or "v0.5.0" in app_js
            or "v0.5.1" in app_js
            or "v0.5.2" in app_js
            or "v0.5.3" in app_js
            or "v0.5.4" in app_js
            or "v0.5.5" in app_js
            or "v0.5.6" in app_js
            or "v0.5.7" in app_js
            or "v0.9.1" in app_js
            or "v0.9.2" in app_js
            or "v0.9.3" in app_js
            or "v0.9.4" in app_js
            or "v0.9.5" in app_js
            or "v0.9.6" in app_js
            or "v0.9.7" in app_js
        )

        # 2. Font scale defaults to 1.15 (Large)
        self.assertIn("return val >= 0.8 && val <= 1.5 ? val : 1.15", app_js)

        # 3. Status strip removed from render() across all screens
        self.assertNotIn("status-strip", app_js)

        # 4. Detailed view hero layout and actions on single line
        self.assertIn(".detail-hero-top", css)
        self.assertIn(".detail-hero-actions", css)
        self.assertIn("detail-hero-badges", app_js)
        self.assertIn("stepper-compact", app_js)
        self.assertIn("detail-research-btn", app_js)
        self.assertIn("flex-wrap: nowrap", css)

        # 5. Sommelier & Producer AI Research moved inside form above action buttons
        self.assertIn('id="mobile-detail-form"', app_js)
        self.assertIn("detail-research-card", app_js)
        # Check that in _renderDetailSheet, AI research appears after rating slider and before form buttons
        detail_sheet_code = app_js[app_js.find("_renderDetailSheet("):app_js.find("_renderScanSheet(")]
        slider_idx = detail_sheet_code.find("renderPersonalRatingSlider")
        research_idx = detail_sheet_code.find("detail-research-card")
        submit_idx = detail_sheet_code.find('type="submit"')
        self.assertTrue(slider_idx != -1 and research_idx != -1 and submit_idx != -1)
        self.assertLess(slider_idx, research_idx)
        self.assertLess(research_idx, submit_idx)

        # 6. Personal rating slider 1.0 to 5.0 with 0.1 step and 5.0 = Excellent!
        self.assertIn('min="1.0"', app_js)
        self.assertIn('max="5.0"', app_js)
        self.assertIn('step="0.1"', app_js)
        self.assertIn("personalRatingMeta", app_js)
        self.assertIn("Excellent!", app_js)
        self.assertIn('step="0.1" min="1" max="5"', card_js)

        # 7. Brand assets populated
        for brand_dir in [INTEGRATION_DIR / "brand", REPO_ROOT / "brand"]:
            self.assertTrue((brand_dir / "icon.png").exists())
            self.assertTrue((brand_dir / "icon@2x.png").exists())
            self.assertTrue((brand_dir / "dark_icon.png").exists())
            self.assertTrue((brand_dir / "logo.png").exists())

        # 8. Mobile PWA backup creation & Vivino import API endpoints
        status_denied, _ = await async_handle_pwa_post(self.hass, "backup_create", {})
        self.assertEqual(status_denied, 403)
        status_backup, res_backup = await async_handle_pwa_post(
            self.hass, "backup_create", {}, user=_ADMIN_USER
        )
        self.assertEqual(status_backup, 200)
        self.assertTrue(res_backup.get("ok"))
        # v0.9.3: backups are served by the admin-only download view, never from /local/
        self.assertTrue(
            res_backup.get("download_url", "").startswith("/api/homecave/download/backup/")
        )
        self.assertNotIn("file_path", res_backup.get("backup", {}))

        csv_content = (
            "Wine Name,Winery / Producer,Vintage,Region,Country,My Rating,Review Text / Notes,Date Logged,Price Paid,Quantity In Cellar\n"
            "Château Latour,Château Latour,2010,Pauillac,France,4.9,Monumental Bordeaux,2026-01-01,800.0,3\n"
        )
        status_import, res_import = await async_handle_pwa_post(
            self.hass,
            "import",
            {"format_type": "csv", "payload": csv_content},
            user=_ADMIN_USER,
        )
        self.assertEqual(status_import, 200)
        self.assertEqual(res_import.get("imported_count"), 1)
        wines = self.coordinator.wines
        latour = next((w for w in wines if "Latour" in w["winery"]), None)
        self.assertIsNotNone(latour)
        self.assertEqual(latour["name"], "Château Latour")
        self.assertEqual(latour["vintage"], 2010)
        self.assertEqual(latour["quantity"], 3)
        self.assertEqual(latour["personal_rating"], 4.9)

    async def test_v050_sommai_mobile_and_import_progress(self) -> None:
        """v0.5.0: Verify SommAI food-wine pairing endpoint, import progress polling, and UI elements."""
        from custom_components.homecave.http_api import async_handle_pwa_get, async_handle_pwa_post

        # 1. GET /api/homecave/pwa/import_progress
        status_prog, res_prog = await async_handle_pwa_get(self.hass, "import_progress", {})
        self.assertEqual(status_prog, 200)
        self.assertIn("running", res_prog)
        self.assertIn("progress_pct", res_prog)

        # 2. POST /api/homecave/pwa/sommai (mocking coordinator async_sommelier_chat)
        async def _mock_somm_chat(query_text: str = "", image_data: Any = None, conversation_history: Any = None) -> dict[str, Any]:
            return {
                "ok": True,
                "dish_name": "Dry-Aged T-Bone Steak",
                "dish_analysis": "Rich umami, charred exterior, intense beef marbling.",
                "pairing_reasoning": "High-tannin Cabernet Sauvignon pairs harmoniously.",
                "recommended_wines": [
                    {
                        "wine_id": 1,
                        "wine_title": "Tenuta San Guido Sassicaia 2018",
                        "match_type": "best",
                        "sommelier_notes": "Firm tannins cut fat cleanly.",
                        "serving_temp_c": "16-18°C",
                        "decant_minutes": 60,
                    }
                ],
                "conversational_response": "Open your Sassicaia 2018 for this steak.",
                "cellar_bottles_considered": 1,
            }

        self.coordinator.async_sommelier_chat = _mock_somm_chat  # type: ignore[assignment]
        status_somm, res_somm = await async_handle_pwa_post(
            self.hass,
            "sommai",
            {"message": "What goes with T-Bone?", "history": []},
        )
        self.assertEqual(status_somm, 200)
        self.assertEqual(res_somm["dish_name"], "Dry-Aged T-Bone Steak")
        self.assertEqual(len(res_somm["recommended_wines"]), 1)

        # 3. Mobile App JS and CSS checks
        app_js = (MOBILE_DIR / "app.js").read_text(encoding="utf-8")
        css = (MOBILE_DIR / "styles.css").read_text(encoding="utf-8")
        card_js = (INTEGRATION_DIR / "frontend" / "homecave-card.js").read_text(encoding="utf-8")
        dashboard_yaml = (REPO_ROOT / "dashboards" / "homecave-dashboard.yaml").read_text(encoding="utf-8")

        # SommAI bottom navigation and view
        self.assertIn('data-nav="sommai"', app_js)
        self.assertIn("<span>SommAI</span>", app_js)
        self.assertIn("_renderSommAIView", app_js)
        self.assertIn(".sommai-view", css)
        self.assertIn(".sommai-chip", css)

        # Tasted view: Scan Tasted button removed from banner
        tasted_banner_code = app_js[app_js.find("_renderTastedView("):app_js.find("search-filter-dock")]
        self.assertNotIn('data-open-scan="tasted"', tasted_banner_code)

        # Statistics embedded in Config view
        config_view_code = app_js[app_js.find("_renderConfigView("):app_js.find("_renderSommAIView(")]
        self.assertIn("_renderStatsSection", config_view_code)

        # Wine thumbnail click navigates to Detailed view
        self.assertIn('data-detail-id="${num(w.id)}"', app_js)
        self.assertNotIn('data-preview-id="${w.id}"', app_js)

        # Sommelier & Producer AI Research font size bumped by +1
        self.assertIn("font-size:14.5px; font-style:italic;", app_js)
        self.assertIn("font-size:13.5px; line-height:1.5;", app_js)

        # Vivino import warning modal & progress bar
        self.assertIn("_renderImportWarningSheet", app_js)
        self.assertIn("_renderImportProgressSheet", app_js)
        self.assertIn(".import-progress-track", css)
        self.assertIn(".import-progress-fill", css)

        # Home Assistant Card & Dashboard
        self.assertIn("wine-sommai-card", card_js)
        self.assertIn("WineSommAICard", card_js)
        self.assertIn("custom:homecave-sommai-card", dashboard_yaml)
        self.assertIn("path: sommai", dashboard_yaml)

    def test_v053_mobile_and_card_features(self) -> None:
        """v0.5.3: Verify photo upload, Keep & Soon labels, invert sort toggles, and all-details search."""
        app_js = (MOBILE_DIR / "app.js").read_text(encoding="utf-8")
        card_js = (INTEGRATION_DIR / "frontend" / "homecave-card.js").read_text(encoding="utf-8")

        # 1. Drink Window Labels: Keep and Soon
        self.assertIn('hold: { label: "Keep", color: "#0277bd" }', app_js)
        self.assertIn('drink_soon: { label: "Soon", color: "#ef6c00" }', app_js)
        self.assertIn('ready: { label: "Ready", color: "#2e7d32" }', app_js)
        self.assertIn('{ id: "keep", label: `Keep (${num(stats.keep_bottles || stats.hold_bottles)})` }', app_js)

        self.assertIn('hold: { label: "Keep", color: "var(--info-color, #0288d1)" }', card_js)
        self.assertIn('drink_soon: { label: "Soon", color: "var(--warning-color, #ef6c00)" }', card_js)
        self.assertIn('{ id: "keep", label: `Keep (${num(stats.keep_bottles || stats.hold_bottles)})` }', card_js)

        # 2. Invert Sort Buttons & Logic
        self.assertIn("id=\"btn-mobile-invert-sort\"", app_js)
        self.assertIn("id=\"btn-mobile-tasted-invert-sort\"", app_js)
        self.assertIn("sortInverted: false", app_js)
        self.assertIn("tastedSortInverted: false", app_js)
        self.assertIn("const inv = this.state.sortInverted ? -1 : 1;", app_js)

        self.assertIn("id=\"btn-card-invert-sort\"", card_js)
        self.assertIn("this._sortInverted = false;", card_js)
        self.assertIn("const inv = this._sortInverted ? -1 : 1;", card_js)

        # 3. All-Details Search Engine
        self.assertIn("_getWineSearchText(w)", app_js)
        self.assertIn("w.food_pairings", app_js)
        self.assertIn("w.research_notes", app_js)
        self.assertIn("w.batches", app_js)
        self.assertIn("w.timeline", app_js)
        self.assertIn("terms.every((t) => hay.includes(t))", app_js)

        self.assertIn("_getWineSearchText(w)", card_js)
        self.assertIn("w.research_notes", card_js)
        self.assertIn("terms.every((t) => haystack.includes(t))", card_js)

        # 4. Detailed View Photo Upload
        self.assertIn("id=\"detail-bottle-photo-input\"", app_js)
        self.assertIn("id=\"btn-detail-upload-photo\"", app_js)
        self.assertIn("id=\"btn-preview-upload-photo\"", app_js)
        self.assertIn("id=\"preview-bottle-photo-input\"", app_js)
        self.assertIn("_handlePhotoUpload(file, wine)", app_js)
        self.assertIn("updates: { image_data: dataUrl }", app_js)


    def test_v091_homecave_mobile_splash_cards_and_manifest(self) -> None:
        """v0.9.1: Verify HomeCave tagline, splash logo, card custom elements, and PWA manifest."""
        manifest = json.loads((MOBILE_DIR / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "HomeCave - your local Wine Cellar")
        self.assertEqual(manifest["short_name"], "HomeCave")
        self.assertEqual(manifest["theme_color"], "#621322")

        html = (MOBILE_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("<title>HomeCave - your local Wine Cellar</title>", html)
        self.assertIn('src="/homecave/logo-512.png"', html)
        self.assertIn("HomeCave - your local Wine Cellar", html)
        self.assertIn('id="homecave-mobile-root"', html)
        self.assertTrue((MOBILE_DIR / "logo-512.png").exists())

        app_js = (MOBILE_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn("class HomeCaveMobileApp", app_js)
        self.assertIn("HomeCave - your local Wine Cellar", app_js)
        self.assertIn("/homecave/logo-512.png", app_js)

        card_js = (INTEGRATION_DIR / "frontend" / "homecave-card.js").read_text(encoding="utf-8")
        for tag in (
            "homecave-card",
            "homecave-card-editor",
            "homecave-summary-card",
            "homecave-summary-card-editor",
            "homecave-sommai-card",
            "homecave-sommai-card-editor",
            "wine-inventory-card",
            "wine-cellar-summary-card",
            "wine-sommai-card",
        ):
            self.assertIn(f'"{tag}"', card_js)
        self.assertIn('/homecave/icon-192.png', card_js)


if __name__ == "__main__":
    unittest.main()
