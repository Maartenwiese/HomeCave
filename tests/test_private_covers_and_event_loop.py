"""v0.9.5 regression tests: private cover storage + signed URLs, and no filesystem calls on the event loop."""

from __future__ import annotations

import ast
import asyncio
import base64
import inspect
from pathlib import Path
import sqlite3
import tempfile
import textwrap
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aiohttp.test_utils import make_mocked_request

from custom_components.homecave import coordinator as coordinator_module
from custom_components.homecave import http_api as http_api_module
from custom_components.homecave import websocket_api as websocket_api_module
from custom_components.homecave.const import DOMAIN
from custom_components.homecave.coordinator import WineInventoryCoordinator
from custom_components.homecave.covers import (
    COVER_URL_PREFIX,
    CoverUrlSigner,
    get_cover_signer,
    migrate_public_covers,
    normalize_cover_ref,
    sign_cover_urls,
)
from custom_components.homecave.database import WineDatabase
from custom_components.homecave.http_api import (
    WineInventoryCoverView,
    WineInventoryDownloadView,
    WineInventoryMobileAssetView,
    _sign_for_request,
)
from custom_components.homecave.scanner import save_bottle_cover_image

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "custom_components" / "homecave" / "frontend"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01PRIVATE_COVER_TEST"
JPEG_DATA_URI = "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()
ADMIN = SimpleNamespace(id="admin", name="Admin", is_admin=True, permissions=None)


class _ThreadedHass:
    """Stub hass whose executor jobs really run on a worker thread (like Home Assistant)."""

    def __init__(self, root: str) -> None:
        self.config = SimpleNamespace(
            config_dir=root, path=lambda *parts: str(Path(root).joinpath(*parts))
        )
        self.bus = SimpleNamespace(async_fire=lambda *a, **k: None)
        self.states: dict = {}
        self.data: dict = {}

    async def async_add_executor_job(self, func, *args):
        return await asyncio.get_running_loop().run_in_executor(None, func, *args)


class _LoopGuard:
    """Patch blocking Path methods so they raise when called on the event-loop thread."""

    METHODS = ("is_file", "exists", "stat", "read_bytes", "read_text", "unlink", "resolve")

    def __init__(self) -> None:
        self.loop_thread: threading.Thread | None = None
        self._patches = []

    def __enter__(self) -> "_LoopGuard":
        self.loop_thread = threading.current_thread()
        for name in self.METHODS:
            real = getattr(Path, name)

            def _guarded(path_self, *args, __real=real, __name=name, **kwargs):
                if threading.current_thread() is self.loop_thread:
                    raise AssertionError(f"blocking Path.{__name}() on the event loop: {path_self}")
                return __real(path_self, *args, **kwargs)

            p = patch.object(Path, name, _guarded)
            p.start()
            self._patches.append(p)
        return self

    def __exit__(self, *exc) -> None:
        for p in reversed(self._patches):
            p.stop()


def _fake_sign_factory():
    counter = {"n": 0}

    def _fake_sign(self, path, refresh_token_id):
        counter["n"] += 1
        return f"{path}?authSig=sig-{refresh_token_id}-{counter['n']}"

    return _fake_sign, counter


class TestPrivateCoverStorage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.hass = _ThreadedHass(self._tmp.name)
        self.coordinator = WineInventoryCoordinator(
            hass=self.hass, entry_id="cov", config={"cellar_name": "Cov"}
        )
        await self.coordinator.async_initialize()
        self.hass.data.setdefault(DOMAIN, {})["cov"] = {"coordinator": self.coordinator}

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    def test_scanned_photo_saved_privately(self) -> None:
        url = save_bottle_cover_image(self.root, JPEG, "image/jpeg")
        self.assertRegex(url, r"^/api/homecave/cover/bottle_[0-9a-f]{12}\.jpg$")
        self.assertTrue((self.root / "homecave" / "covers" / Path(url).name).exists())
        self.assertFalse((self.root / "www").exists())

    async def test_uploaded_photo_saved_privately(self) -> None:
        wine = await self.coordinator.async_add_wine({"winery": "W", "name": "N", "quantity": 1})
        updated = await self.coordinator.async_update_wine(wine["id"], {"image_data": JPEG_DATA_URI})
        self.assertTrue(updated["image_url"].startswith(COVER_URL_PREFIX))
        self.assertTrue((self.root / "homecave" / "covers" / Path(updated["image_url"]).name).exists())
        self.assertFalse((self.root / "www").exists())

    async def test_migration_moves_public_covers_and_rewrites_urls(self) -> None:
        public = self.root / "www" / "homecave" / "covers"
        public.mkdir(parents=True)
        (public / "bottle_public01.jpg").write_bytes(JPEG)
        legacy_public = self.root / "www" / "wine_inventory" / "covers"
        legacy_public.mkdir(parents=True)
        (legacy_public / "vivino_legacy.jpg").write_bytes(JPEG)
        wine = await self.coordinator.async_add_wine({"winery": "Old", "name": "Photo", "quantity": 1})
        # Simulate a pre-v0.9.5 database row (bypassing the sanitizer, as old versions stored it)
        with sqlite3.connect(self.coordinator.db_path) as conn:
            conn.execute(
                "UPDATE wines SET image_url = ? WHERE id = ?",
                ("/local/homecave/covers/bottle_public01.jpg", wine["id"]),
            )

        coord2 = WineInventoryCoordinator(hass=self.hass, entry_id="cov2", config={})
        await coord2.async_initialize()

        self.assertTrue((self.root / "homecave" / "covers" / "bottle_public01.jpg").exists())
        self.assertTrue((self.root / "homecave" / "covers" / "vivino_legacy.jpg").exists())
        self.assertFalse(public.exists())
        self.assertFalse(legacy_public.exists())
        with sqlite3.connect(coord2.db_path) as conn:
            stored = conn.execute("SELECT image_url FROM wines WHERE id = ?", (wine["id"],)).fetchone()[0]
        self.assertEqual(stored, "/api/homecave/cover/bottle_public01.jpg")
        self.assertEqual(migrate_public_covers(self.root), 0)  # idempotent

    def test_image_url_never_stores_signature_and_rejects_bad_refs(self) -> None:
        db = WineDatabase(self.root / "x.db")
        db.initialize()
        w = db.add_or_increment_wine(winery="W", name="N", quantity=1)
        updated = db.update_wine(w["id"], {"image_url": "/api/homecave/cover/bottle_ab.jpg?authSig=eyJ.x"})
        self.assertEqual(updated["image_url"], "/api/homecave/cover/bottle_ab.jpg")
        for bad in (
            "/api/homecave/cover/../secrets.yaml",
            "/api/homecave/cover/x.html",
            "/local/homecave/covers/../x.jpg",
            "/local/other/x.jpg",
            "/api/hassio/x.jpg",
        ):
            self.assertEqual(db.update_wine(w["id"], {"image_url": bad})["image_url"], "", bad)
        self.assertIsNone(normalize_cover_ref("https://images.vivino.com/x.jpg"))


class TestCoverView(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.hass = _ThreadedHass(self._tmp.name)
        covers = self.root / "homecave" / "covers"
        covers.mkdir(parents=True)
        (covers / "bottle_view01.jpg").write_bytes(JPEG)
        self.view = WineInventoryCoverView(self.hass)

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    def test_requires_auth(self) -> None:
        self.assertTrue(WineInventoryCoverView.requires_auth)
        self.assertEqual(WineInventoryCoverView.url, "/api/homecave/cover/{filename}")

    async def test_serves_image_with_safe_headers(self) -> None:
        req = make_mocked_request("GET", "/api/homecave/cover/bottle_view01.jpg")
        with _LoopGuard():
            resp = await self.view.get(req, "bottle_view01.jpg")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.body, JPEG)
        self.assertEqual(resp.content_type, "image/jpeg")
        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("private", resp.headers["Cache-Control"])
        self.assertIn("sandbox", resp.headers["Content-Security-Policy"])

    async def test_rejects_bad_or_missing_names(self) -> None:
        for name in ("../homecave.db", "x.html", "bottle_view01.jpg/../x", "nope.jpg", ""):
            req = make_mocked_request("GET", f"/api/homecave/cover/{name}")
            resp = await self.view.get(req, name)
            self.assertEqual(resp.status, 404, name)

    async def test_www_covers_not_served_by_view(self) -> None:
        public = self.root / "www" / "homecave" / "covers"
        public.mkdir(parents=True)
        (public / "only_public.jpg").write_bytes(JPEG)
        req = make_mocked_request("GET", "/api/homecave/cover/only_public.jpg")
        resp = await self.view.get(req, "only_public.jpg")
        self.assertEqual(resp.status, 404)


class TestSignedCoverUrls(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.hass = _ThreadedHass(self._tmp.name)
        self.coordinator = WineInventoryCoordinator(hass=self.hass, entry_id="sig", config={})
        await self.coordinator.async_initialize()
        self.hass.data.setdefault(DOMAIN, {})["sig"] = {"coordinator": self.coordinator}
        wine = await self.coordinator.async_add_wine({"winery": "W", "name": "N", "quantity": 1})
        await self.coordinator.async_update_wine(wine["id"], {"image_data": JPEG_DATA_URI})

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    async def test_websocket_results_signed_and_cached(self) -> None:
        fake_sign, counter = _fake_sign_factory()
        sent: list = []
        conn = SimpleNamespace(
            refresh_token_id="rt-1",
            user=ADMIN,
            send_result=lambda msg_id, payload: sent.append(payload),
            send_error=lambda *a: sent.append(("error", a)),
        )
        with patch.object(CoverUrlSigner, "_sign", fake_sign):
            await websocket_api_module.ws_list_wines(self.hass, conn, {"id": 1, "type": "homecave/list"})
            await websocket_api_module.ws_list_wines(self.hass, conn, {"id": 2, "type": "homecave/list"})
        url1 = sent[0]["wines"][0]["image_url"]
        url2 = sent[1]["wines"][0]["image_url"]
        self.assertIn("?authSig=sig-rt-1-", url1)
        self.assertEqual(url1, url2)  # cached -> stable, browser-cacheable URL
        self.assertEqual(counter["n"], 1)
        # Coordinator data itself is never mutated
        self.assertNotIn("authSig", self.coordinator.wines[0]["image_url"])

    def test_pwa_responses_signed_per_session(self) -> None:
        fake_sign, _ = _fake_sign_factory()
        payload = {"wines": [dict(w) for w in self.coordinator.wines]}
        req_a = make_mocked_request("GET", "/api/homecave/pwa/state")
        req_a["hass_refresh_token_id"] = "rt-A"
        req_b = make_mocked_request("GET", "/api/homecave/pwa/state")
        req_b["hass_refresh_token_id"] = "rt-B"
        with patch.object(CoverUrlSigner, "_sign", fake_sign):
            signed_a = _sign_for_request(self.hass, req_a, payload)
            signed_b = _sign_for_request(self.hass, req_b, payload)
        self.assertIn("sig-rt-A", signed_a["wines"][0]["image_url"])
        self.assertIn("sig-rt-B", signed_b["wines"][0]["image_url"])
        self.assertNotIn("authSig", payload["wines"][0]["image_url"])

    def test_only_private_cover_urls_are_signed(self) -> None:
        fake_sign, _ = _fake_sign_factory()
        signer = get_cover_signer(self.hass)
        payload = {
            "a": {"image_url": "https://images.vivino.com/x.jpg"},
            "b": [{"image_url": "/api/homecave/cover/bottle_x.jpg"}],
            "c": {"note": "/api/homecave/cover/bottle_x.jpg"},
        }
        with patch.object(CoverUrlSigner, "_sign", fake_sign):
            out = sign_cover_urls(payload, signer, "rt-9")
        self.assertEqual(out["a"]["image_url"], "https://images.vivino.com/x.jpg")
        self.assertIn("authSig", out["b"][0]["image_url"])
        self.assertEqual(out["c"]["note"], "/api/homecave/cover/bottle_x.jpg")
        # Without a session token nothing is signed (services/automations keep the plain path)
        self.assertEqual(sign_cover_urls(payload, signer, None), payload)


class TestNoBlockingFilesystemCalls(unittest.IsolatedAsyncioTestCase):
    """The five reported call sites must only touch the filesystem inside executor jobs."""

    BLOCKING = {"is_file", "exists", "stat", "unlink", "read_bytes", "read_text", "resolve_safe_storage_path"}

    def _direct_calls(self, func) -> list[str]:
        tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                if name in self.BLOCKING:
                    found.append(name)
        return found

    def test_static_ast_guard(self) -> None:
        for func in (
            WineInventoryMobileAssetView.get,
            WineInventoryDownloadView.get,
            WineInventoryCoverView.get,
            websocket_api_module.ws_create_backup,
            WineInventoryCoordinator.async_get_backup_download_url,
            WineInventoryCoordinator.async_scan_bottle,
        ):
            self.assertEqual(self._direct_calls(func), [], func.__qualname__)

    async def test_runtime_loop_thread_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hass = _ThreadedHass(tmp)
            coord = WineInventoryCoordinator(hass=hass, entry_id="io", config={})
            await coord.async_initialize()
            hass.data.setdefault(DOMAIN, {})["io"] = {"coordinator": coord}
            await coord.async_add_wine({"winery": "W", "name": "N", "quantity": 1})
            backup = await coord.async_create_backup()

            with _LoopGuard():
                # 1. PWA asset view
                asset_view = WineInventoryMobileAssetView(hass)
                resp = await asset_view.get(make_mocked_request("GET", "/homecave/app.js"), "app.js")
                self.assertEqual(resp.status, 200)
                # 2. Download view (admin backup)
                dl_view = WineInventoryDownloadView(hass)
                req = make_mocked_request("GET", "/api/homecave/download/backup/x")
                req["hass_user"] = ADMIN
                resp = await dl_view.get(req, "backup", backup["filename"])
                self.assertEqual(resp.status, 200)
                # 3. Signed backup URL helper
                url = await coord.async_get_backup_download_url(backup["filename"])
                self.assertTrue(url.startswith("/api/homecave/download/backup/"))
                # 4. ws_create_backup
                sent: list = []
                conn = SimpleNamespace(
                    refresh_token_id=None,
                    user=ADMIN,
                    send_result=lambda msg_id, payload: sent.append(payload),
                    send_error=lambda *a: sent.append(("error", a)),
                )
                await websocket_api_module.ws_create_backup(hass, conn, {"id": 5, "type": "homecave/create_backup"})
                self.assertIn("archive_base64", sent[-1])
                # 5. Scan error path deletes the saved cover off-loop
                with patch.object(
                    coordinator_module,
                    "async_scan_bottle_label",
                    side_effect=coordinator_module.GeminiScannerError("not a wine bottle"),
                ), patch.object(coord, "_get_http_session", return_value=None):
                    with self.assertRaises(coordinator_module.GeminiScannerError):
                        await coord.async_scan_bottle(image_data=JPEG_DATA_URI)
            covers = Path(tmp) / "homecave" / "covers"
            self.assertEqual(list(covers.glob("bottle_*")) if covers.exists() else [], [])


class TestFrontendCoverAllowlist(unittest.TestCase):
    def test_safe_image_url_allows_signed_private_covers_only(self) -> None:
        for js in (FRONTEND_DIR / "homecave-card.js", FRONTEND_DIR / "mobile" / "app.js"):
            src = js.read_text(encoding="utf-8")
            self.assertIn("\\/api\\/homecave\\/cover\\/", src, js.name)
            self.assertIn("authSig", src, js.name)
            self.assertNotIn('raw.startsWith("/local/homecave/covers/")', src, js.name)

    def test_http_api_registers_cover_view(self) -> None:
        src = inspect.getsource(http_api_module.async_register_http_views)
        self.assertIn("WineInventoryCoverView", src)


if __name__ == "__main__":
    unittest.main()
