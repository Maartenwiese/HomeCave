"""Unit tests for database backup, restore, import, and dashboard multi-view configs."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import tempfile
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest.mock import MagicMock
import zipfile
import yaml

from custom_components.homecave.backup import (
    create_backup_archive,
    import_vivino_history_csv,
    import_wines_archive,
    list_available_backups,
    restore_backup_archive,
    stage_backup_for_download,
)
from custom_components.homecave.config_flow import (
    WineInventoryConfigFlow,
    WineInventoryOptionsFlowHandler,
)
from custom_components.homecave.const import (
    CONF_CELLAR_NAME,
    CONF_CLAUDE_API_KEY,
    CONF_CLAUDE_MODEL,
    CONF_GEMINI_API_KEY,
    CONF_GEMINI_MODEL,
    CONF_LLM_PROVIDER,
    DEFAULT_CELLAR_NAME,
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_GEMINI_MODEL,
    DOMAIN,
    LLM_PROVIDER_CLAUDE,
    LLM_PROVIDER_GEMINI,
    VERSION,
)
from custom_components.homecave.database import WineDatabase


class TestBackupRestoreAndViews(unittest.TestCase):
    """Test suite verifying ZIP backup generation, restore, multi-format import, and dashboard YAML."""

    def test_create_and_restore_backup_archive(self) -> None:
        """Verify full ZIP backup creation and lossless restoration of DB, covers, and metadata."""
        with tempfile.TemporaryDirectory() as src_dir, tempfile.TemporaryDirectory() as dest_dir:
            src_path = Path(src_dir)
            dest_path = Path(dest_dir)

            # 1. Setup source DB and covers
            db_file = src_path / "homecave.db"
            db = WineDatabase(db_file)
            db.initialize()

            w1 = db.add_or_increment_wine(
                winery="Domaine de la Romanée-Conti",
                name="Romanée-Conti Grand Cru",
                vintage=2015,
                wine_type="red",
                varietal="Pinot Noir",
                region="Vosne-Romanée",
                country="France",
                quantity=3,
                purchase_location="Berry Bros & Rudd",
                purchase_price=12500.0,
                storage_location="Rack A1",
                vivino_rating=4.9,
            )

            # Create dummy cover image
            covers_dir = src_path / "covers"
            covers_dir.mkdir(parents=True, exist_ok=True)
            dummy_cover = covers_dir / f"cover_{w1['id']}.jpg"
            dummy_cover.write_bytes(b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01DUMMY_IMAGE_DATA")

            # Update DB with image path
            db.update_wine(w1["id"], {"image_url": f"/local/homecave/covers/{dummy_cover.name}"})

            # 2. Create full backup archive
            backup_meta = create_backup_archive(src_path, db)
            backup_file_path = Path(backup_meta["file_path"])
            self.assertTrue(backup_file_path.exists())
            self.assertEqual(backup_meta["wines_count"], 1)
            self.assertGreater(backup_meta["size_bytes"], 0)

            # Verify contents of generated ZIP
            with zipfile.ZipFile(backup_file_path, "r") as zf:
                namelist = zf.namelist()
                self.assertIn("homecave.db", namelist)
                self.assertIn("metadata.json", namelist)
                self.assertIn(f"covers/{dummy_cover.name}", namelist)

                metadata_bytes = zf.read("metadata.json")
                meta = json.loads(metadata_bytes.decode("utf-8"))
                self.assertEqual(meta["version"], VERSION)
                self.assertEqual(meta["wines_count"], 1)
                self.assertEqual(meta["total_bottles"], 3)

            # Verify backup shows up in list_available_backups
            backups_list = list_available_backups(src_path)
            self.assertGreaterEqual(len(backups_list), 1)
            self.assertEqual(backups_list[0]["filename"], backup_meta["filename"])

            # 3. Restore backup into destination directory
            dest_db_file = dest_path / "homecave.db"
            dest_db = WineDatabase(dest_db_file)

            with open(backup_file_path, "rb") as bf:
                restore_meta = restore_backup_archive(dest_path, dest_db, bf.read())

            self.assertEqual(restore_meta["wines_count"], 1)
            self.assertEqual(restore_meta["restored_images"], 1)
            self.assertTrue(dest_db_file.exists())
            self.assertTrue((dest_path / "covers" / dummy_cover.name).exists())

            # Validate destination DB query
            restored_wines = dest_db.list_wines()
            self.assertEqual(len(restored_wines), 1)
            self.assertEqual(restored_wines[0]["name"], "Romanée-Conti Grand Cru")
            self.assertEqual(restored_wines[0]["quantity"], 3)

    def test_import_wines_json_csv_and_zip(self) -> None:
        """Verify bulk wine import from JSON array, CSV text, and ZIP archive without wiping cellar."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            db_file = tmp_path / "homecave.db"
            db = WineDatabase(db_file)
            db.initialize()

            # 1. Import from JSON array
            json_payload = [
                {
                    "winery": "Château Margaux",
                    "name": "Pavillon Rouge",
                    "vintage": 2018,
                    "wine_type": "red",
                    "varietal": "Cabernet Sauvignon",
                    "quantity": 2,
                    "purchase_location": "Bordeaux Primeurs",
                    "purchase_price": 210.0,
                },
                {
                    "winery": "Kistler",
                    "name": "Les Noisetiers Chardonnay",
                    "vintage": 2021,
                    "wine_type": "white",
                    "varietal": "Chardonnay",
                    "quantity": 4,
                    "purchase_location": "Napa Valley",
                    "purchase_price": 75.0,
                },
            ]
            json_res = import_wines_archive(tmp_path, db, json_payload, format_type="json")
            self.assertEqual(json_res["imported_wines"], 2)

            # 2. Import from CSV string
            csv_payload = (
                "winery,name,vintage,wine_type,varietal,quantity,purchase_price\n"
                "Dom Pérignon,Vintage Champagne,2012,sparkling,Chardonnay Pinot Noir,3,240.0\n"
                "Château d'Yquem,Sauternes,2009,dessert,Semillon Sauvignon Blanc,1,450.0\n"
            )
            csv_res = import_wines_archive(tmp_path, db, csv_payload, format_type="csv")
            self.assertEqual(csv_res["imported_wines"], 2)

            # Total wines should now be 4, total bottles 10
            stats = db.get_statistics()
            self.assertEqual(stats["unique_wines"], 4)
            self.assertEqual(stats["total_bottles"], 10)

    def test_dashboard_views_configuration(self) -> None:
        """Verify dashboards/homecave-dashboard.yaml specifies multiple views including list-view."""
        dash_path = Path(__file__).resolve().parent.parent / "dashboards" / "homecave-dashboard.yaml"
        self.assertTrue(dash_path.exists(), f"Missing dashboard file at {dash_path}")

        with open(dash_path, "r", encoding="utf-8") as f:
            dash = yaml.safe_load(f)

        self.assertIn("views", dash)
        views = dash["views"]
        self.assertGreaterEqual(len(views), 4, "Dashboard should define at least 4 views")

        view_paths = [v.get("path") for v in views]
        self.assertIn("overview", view_paths)
        self.assertIn("tasting-archive", view_paths)
        self.assertIn("sommai", view_paths)
        self.assertIn("statistics", view_paths)

        # Verify overview view uses default_view: list
        overview_view = next(v for v in views if v.get("path") == "overview")
        self.assertEqual(overview_view.get("icon"), "mdi:view-dashboard")
        card_types = [
            c.get("type")
            for sec in overview_view.get("sections", [])
            for c in sec.get("cards", [])
        ]
        self.assertIn("custom:homecave-card", card_types)

        # Verify custom card has default_view: list
        card = next(
            c
            for sec in overview_view.get("sections", [])
            for c in sec.get("cards", [])
            if c.get("type") == "custom:homecave-card"
        )
        self.assertEqual(card.get("default_view"), "list")

        # Verify Tasted view is titled 'Tasted' and configures default_tab: tasted
        tasted_view = next(v for v in views if v.get("path") == "tasting-archive")
        self.assertEqual(tasted_view.get("title"), "Tasted")
        tasted_card = next(
            c
            for sec in tasted_view.get("sections", [])
            for c in sec.get("cards", [])
            if c.get("type") == "custom:homecave-card"
        )
        self.assertEqual(tasted_card.get("default_tab"), "tasted")

        # Verify SommAI view has custom:homecave-sommai-card
        sommai_view = next(v for v in views if v.get("path") == "sommai")
        self.assertEqual(sommai_view.get("title"), "SommAI")
        sommai_card = next(
            c
            for sec in sommai_view.get("sections", [])
            for c in sec.get("cards", [])
            if c.get("type") == "custom:homecave-sommai-card"
        )
        self.assertIsNotNone(sommai_card)

    def test_database_delete_wine_permanently(self) -> None:
        """Verify full permanent deletion of wine, batches, and timeline from SQLite database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = Path(tmpdir) / "homecave.db"
            db = WineDatabase(db_file)
            db.initialize()

            # Add wine with initial batch
            wine = db.add_or_increment_wine(
                winery="Château Cheval Blanc",
                name="Premier Grand Cru Classé A",
                vintage=2010,
                wine_type="red",
                quantity=2,
                purchase_location="Bordeaux",
            )
            wine_id = wine["id"]
            self.assertEqual(len(db.list_wines()), 1)

            # Consume 1 bottle to create tasting history
            db.adjust_quantity(wine_id=wine_id, delta=-1, personal_rating=4.8, notes="Superb structure")
            detail = db.get_wine_detail(wine_id)
            self.assertIsNotNone(detail)
            self.assertEqual(len(detail["timeline"]), 2)  # Added + Consumed

            # Now delete wine permanently
            deleted = db.delete_wine(wine_id)
            self.assertTrue(deleted)
            self.assertEqual(len(db.list_wines()), 0)
            self.assertIsNone(db.get_wine_detail(wine_id))
            stats = db.get_statistics()
            self.assertEqual(stats["total_bottles"], 0)
            self.assertEqual(stats["unique_wines"], 0)

    def test_v036_backup_security_sandboxing_zipslip_and_sqlite_header(self) -> None:
        """v0.3.6: Verify private storage migration, path traversal sandboxing, Zip-Slip rejection, and SQLite header checks."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            db_file = config_dir / "homecave.db"
            db = WineDatabase(db_file)
            db.initialize()

            # 1. Backup created in private homecave/backups, not www/
            meta = create_backup_archive(config_dir, db)
            self.assertTrue(meta["download_url"].startswith("/api/homecave/download/backup/"))
            self.assertNotIn("/local/", meta["download_url"])

            # 2. Path traversal outside wine_inventory/backups is blocked
            with self.assertRaises(ValueError):
                restore_backup_archive(config_dir, db, "../../etc/passwd")
            with self.assertRaises(ValueError):
                import_wines_archive(config_dir, db, "../../etc/passwd", format_type="json")

            # 3. ZIP containing a fake/non-SQLite homecave.db is rejected
            bad_db_buf = io.BytesIO()
            with zipfile.ZipFile(bad_db_buf, "w") as zf:
                zf.writestr("homecave.db", b"NOT_A_SQLITE_FILE_HEADER_AT_ALL")
            with self.assertRaises(ValueError) as ctx_bad_db:
                restore_backup_archive(config_dir, db, bad_db_buf.getvalue())
            self.assertIn("SQLite", str(ctx_bad_db.exception))

            # 4. ZIP containing Zip-Slip traversal entry (../evil.sh) is rejected
            zip_slip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_slip_buf, "w") as zf:
                zf.writestr("../evil.sh", b"#!/bin/sh\necho pwned\n")
            with self.assertRaises(ValueError) as ctx_slip:
                restore_backup_archive(config_dir, db, zip_slip_buf.getvalue())
            self.assertIn("unsafe member path", str(ctx_slip.exception))

    def test_v037_options_flow_backup_restore_and_import(self) -> None:
        """v0.3.7: Verify OptionsFlow 'Create Full Database Backup (.zip)', Restore, and Import complete without NameError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            db_file = config_dir / "homecave.db"
            db = WineDatabase(db_file)
            db.initialize()
            db.add_or_increment_wine(
                winery="Château Margaux",
                name="Premier Grand Cru Classé",
                vintage=2015,
                wine_type="red",
                quantity=2,
            )

            class _FakeCoordinator:
                def __init__(self) -> None:
                    self.fail_backup = False

                async def async_create_backup(self) -> dict:
                    if self.fail_backup:
                        raise RuntimeError("disk full")
                    return create_backup_archive(config_dir, db)

                async def async_list_backups(self) -> list[dict]:
                    return list_available_backups(config_dir)

                async def async_restore_backup(self, file_path: str) -> dict:
                    return restore_backup_archive(config_dir, db, file_path)

                async def async_import_wines(self, payload: str, format_type: str = "auto") -> dict:
                    return import_wines_archive(config_dir, db, payload, format_type)

            coord = _FakeCoordinator()
            entry = SimpleNamespace(
                entry_id="entry_test_037",
                data={"cellar_name": "HomeCave", "currency": "EUR"},
                options=MappingProxyType({"low_stock_threshold": 3}),
            )
            fake_hass = SimpleNamespace(
                config=SimpleNamespace(path=lambda *parts: str(config_dir.joinpath(*parts))),
                data={DOMAIN: {"entry_test_037": {"coordinator": coord}}},
            )

            flow = WineInventoryOptionsFlowHandler(entry)
            flow.hass = fake_hass

            # 1. Initial menu step renders menu options
            menu_res = asyncio.run(flow.async_step_init(None))
            self.assertEqual(menu_res["type"], "menu")
            self.assertIn("backup", menu_res["menu_options"])

            # 2. Backup confirmation form and execution
            backup_form = asyncio.run(flow.async_step_backup(None))
            self.assertEqual(backup_form["type"], "form")
            backup_done = asyncio.run(flow.async_step_backup({}))
            self.assertEqual(backup_done["type"], "create_entry")
            self.assertIsInstance(backup_done["data"], dict)
            self.assertEqual(backup_done["data"]["low_stock_threshold"], 3)

            backups = list_available_backups(config_dir)
            self.assertEqual(len(backups), 1)
            backup_filename = backups[0]["filename"]

            # 3. Backup error path logs cleanly without NameError and shows form error
            coord.fail_backup = True
            backup_err = asyncio.run(flow.async_step_backup({}))
            self.assertEqual(backup_err["type"], "form")
            self.assertEqual(backup_err["errors"].get("base"), "backup_failed")
            coord.fail_backup = False

            # 4. Restore execution via OptionsFlow
            restore_done = asyncio.run(
                flow.async_step_restore({"backup_file": backup_filename})
            )
            self.assertEqual(restore_done["type"], "create_entry")

            # 5. Import execution via OptionsFlow
            import_payload = json.dumps(
                [{"winery": "Krug", "name": "Grande Cuvée", "vintage": 2012, "wine_type": "sparkling", "quantity": 1}]
            )
            import_done = asyncio.run(
                flow.async_step_import_wines(
                    {"import_source": import_payload, "import_format": "json"}
                )
            )
            self.assertEqual(import_done["type"], "create_entry")
            self.assertEqual(len(db.list_wines()), 2)

    def test_import_vivino_history_csv_lossless(self) -> None:
        """Verify importing the user's Vivino history CSV with ratings, notes, and cellar quantities."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "homecave.db"
            db = WineDatabase(db_path)
            db.initialize()

            # Exact sample attached by the user plus a 0-quantity tasted row
            csv_sample = (
                "Wine Name,Winery / Producer,Vintage,Region,Country,My Rating,Review Text / Notes,Date Logged,Price Paid,Quantity In Cellar\n"
                'Sassicaia,Tenuta San Guido,2018,Bolgheri,Italy,4.8,"Incredible structure, silky tannins, dark cherry and tobacco.",2025-05-14,250.0,2\n'
                'Chateau Margaux,Château Margaux,2015,Margaux,France,4.9,"A masterpiece. Velvety, elegant, perfect balance.",2025-11-20,600.0,1\n'
                'Dom Perignon Brut Champagne,Dom Pérignon,2012,Champagne,France,4.7,"Toasty brioche, crisp acidity, beautiful fine bubbles.",2025-12-31,220.0,3\n'
                'Tignanello,Marchesi Antinori,2020,Toscana,Italy,4.6,"Classic Sangiovese blend, vibrant acidity, oak notes.",2026-03-15,140.0,4\n'
                'Silver Oak Alexander Valley Cabernet Sauvignon,Silver Oak,2019,Alexander Valley,United States,4.5,"Classic bold California Cab, vanilla and rich blackberry.",2026-09-02,95.0,2\n'
                'Barolo Monfortino,Giacomo Conterno,2010,Piemonte,Italy,5.0,"Legendary wine, drank at special anniversary dinner.",2024-10-10,1200.0,0\n'
            )

            res = import_vivino_history_csv(db, csv_sample)
            self.assertTrue(res["success"])
            self.assertEqual(res["imported_count"], 6)
            self.assertEqual(res["cellar_count"], 5)
            self.assertEqual(res["tasted_count"], 1)

            wines = db.list_wines(include_out_of_stock=True)
            self.assertEqual(len(wines), 6)

            # Check Dom Perignon Brut Champagne -> sparkling
            dom = next(w for w in wines if "Dom Perignon" in w["name"])
            self.assertEqual(dom["wine_type"], "sparkling")
            self.assertEqual(dom["vintage"], 2012)
            self.assertEqual(dom["personal_rating"], 4.7)
            self.assertEqual(dom["quantity"], 3)

            # Check Sassicaia -> personal rating 4.8 and review notes
            sass = next(w for w in wines if "Sassicaia" in w["name"])
            self.assertEqual(sass["winery"], "Tenuta San Guido")
            self.assertEqual(sass["personal_rating"], 4.8)
            self.assertIn("silky tannins", sass["general_notes"])
            self.assertEqual(sass["quantity"], 2)

            # Check Barolo Monfortino -> 0 quantity in cellar, stored with personal_rating=5.0 and notes in tasted
            barolo = next(w for w in wines if "Monfortino" in w["name"])
            self.assertEqual(barolo["quantity"], 0)
            self.assertEqual(barolo["personal_rating"], 5.0)

            # Also verify _import_wines_from_csv auto-detects Vivino CSV headers
            with tempfile.TemporaryDirectory() as tmp_dir2:
                db2 = WineDatabase(Path(tmp_dir2) / "homecave.db")
                db2.initialize()
                cnt = import_wines_archive(Path(tmp_dir2), db2, csv_sample)
                self.assertEqual(cnt.get("imported_wines"), 6)
                self.assertEqual(len(db2.list_wines(include_out_of_stock=True)), 6)

    def test_stage_backup_for_download(self) -> None:
        """Verify staging backup archive into <config>/www/wine_inventory/backups/ for direct HTTP download."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_dir = Path(tmp_dir)
            db = WineDatabase(config_dir / "homecave.db")
            db.initialize()
            db.add_or_increment_wine(winery="Test Winery", name="Test Wine", quantity=1)

            meta = create_backup_archive(config_dir, db)
            filename = meta["filename"]

            url = stage_backup_for_download(config_dir, filename)
            self.assertEqual(url, f"/local/homecave/backups/{filename}")
            staged_path = config_dir / "www" / "homecave" / "backups" / filename
            self.assertTrue(staged_path.exists())
            self.assertEqual(staged_path.stat().st_size, meta["size_bytes"])

    def test_options_flow_download_and_upload_vivino(self) -> None:
        """Verify OptionsFlow download_backup and upload_vivino menu options."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_dir = Path(tmp_dir)
            db = WineDatabase(config_dir / "homecave.db")
            db.initialize()

            class _FakeCoordinator:
                def __init__(self) -> None:
                    self.db = db

                async def async_create_backup(self) -> dict:
                    return create_backup_archive(config_dir, db)

                async def async_list_backups(self) -> list:
                    return list_available_backups(config_dir)

                async def async_stage_backup_for_download(self, filename: str) -> str:
                    return stage_backup_for_download(config_dir, filename)

                async def async_import_vivino_csv(self, csv_text: str) -> dict:
                    return import_vivino_history_csv(db, csv_text)

            coord = _FakeCoordinator()
            entry = SimpleNamespace(
                entry_id="entry_test_039",
                data={"cellar_name": "HomeCave"},
                options=MappingProxyType({"low_stock_threshold": 3}),
            )
            fake_hass = SimpleNamespace(
                config=SimpleNamespace(path=lambda *parts: str(config_dir.joinpath(*parts))),
                data={DOMAIN: {"entry_test_039": {"coordinator": coord}}},
            )

            flow = WineInventoryOptionsFlowHandler(entry)
            flow.hass = fake_hass

            # Check menu options include download_backup and upload_vivino
            menu_res = asyncio.run(flow.async_step_init(None))
            self.assertIn("download_backup", menu_res["menu_options"])
            self.assertIn("upload_vivino", menu_res["menu_options"])

            # Create backup first so download_backup has files
            asyncio.run(flow.async_step_backup({}))
            backups = list_available_backups(config_dir)
            self.assertEqual(len(backups), 1)
            filename = backups[0]["filename"]

            # Download backup step
            dl_res = asyncio.run(flow.async_step_download_backup({"backup_file": filename}))
            self.assertEqual(dl_res["type"], "form")
            self.assertIn("download_url", dl_res.get("description_placeholders", {}))
            self.assertEqual(
                dl_res["description_placeholders"]["download_url"],
                f"/local/homecave/backups/{filename}",
            )

            # Upload Vivino history step
            csv_content = (
                "Wine Name,Winery / Producer,Vintage,Region,Country,My Rating,Review Text / Notes,Date Logged,Price Paid,Quantity In Cellar\n"
                'Sassicaia,Tenuta San Guido,2018,Bolgheri,Italy,4.8,"Incredible structure.",2025-05-14,250.0,2\n'
            )
            vivino_res = asyncio.run(flow.async_step_upload_vivino({"csv_content": csv_content}))
            self.assertEqual(vivino_res["type"], "create_entry")
            self.assertEqual(len(db.list_wines()), 1)

    def test_v040_vivino_export_zip_import(self) -> None:
        """v0.4.0: Verify Vivino ZIP archive import parses full_wine_list.csv as Tasted, cellar.csv into cellar stock, and downloads covers."""
        from custom_components.homecave.backup import (
            is_vivino_export_zip,
            import_vivino_export_zip,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            config_dir = Path(tmp_dir)
            db_path = config_dir / "homecave.db"
            db = WineDatabase(db_path)
            db.initialize()

            # Test with a synthetic zip first
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(
                    "vivino_data/full_wine_list.csv",
                    "Winery,Wine name,Vintage,Region,Country,Regional wine style,Average rating,Wine ratings count,Your rating,Your review,Personal Note,Wine type,Drinking Window,Link to wine,Label image\n"
                    'Tenuta San Guido,Sassicaia,2018,Bolgheri,Italy,Tuscan Red,4.7,12500,4.8,"Incredible structure","Cellar favorite",Red,2025-2045,https://www.vivino.com/wines/12345,https://images.vivino.com/labels/sassicaia2018.jpg\n'
                    'Château Margaux,Margaux Premier Grand Cru Classé,2015,Margaux,France,Bordeaux Red,4.8,8900,4.9,"Velvety perfection","",Red,2024-2050,https://www.vivino.com/wines/67890,https://images.vivino.com/labels/margaux2015.jpg\n'
                )
                zf.writestr(
                    "vivino_data/cellar.csv",
                    "Winery,Wine name,Vintage,Region,Country,Regional wine style,Average rating,Wine ratings count,User cellar count,Link to wine\n"
                    "Tenuta San Guido,Sassicaia,2018,Bolgheri,Italy,Tuscan Red,4.7,12500,2,https://www.vivino.com/wines/12345\n"
                )

            zip_bytes = zip_buffer.getvalue()
            self.assertTrue(is_vivino_export_zip(zip_bytes, config_dir=config_dir))

            # Run import without downloading images over network in unit test
            res = import_vivino_export_zip(config_dir, db, zip_bytes, download_images=False)
            self.assertTrue(res["success"])
            self.assertEqual(res["cellar_imported"], 1)
            self.assertEqual(res["cellar_bottles"], 2)
            self.assertEqual(res["tasted_imported"], 2)

            wines = db.list_wines()
            self.assertEqual(len(wines), 2)
            sassicaia = [w for w in wines if "Sassicaia" in w["name"]][0]
            self.assertEqual(sassicaia["quantity"], 2)
            self.assertEqual(sassicaia["personal_rating"], 4.8)
            self.assertIn("Incredible structure", sassicaia["general_notes"])
            self.assertEqual(sassicaia["drink_from_year"], 2025)
            self.assertEqual(sassicaia["drink_until_year"], 2045)
            self.assertEqual(sassicaia["vivino_url"], "https://www.vivino.com/wines/12345")
            self.assertEqual(sassicaia["vivino_wine_id"], 12345)
            self.assertEqual(sassicaia["vivino_match_confidence"], "confirmed")
            self.assertTrue(sassicaia["is_imported"])

            # Verify that database initialize / restart does not wipe canonical Vivino links
            db.initialize()
            sassicaia_reloaded = [w for w in db.list_wines() if "Sassicaia" in w["name"]][0]
            self.assertEqual(sassicaia_reloaded["vivino_url"], "https://www.vivino.com/wines/12345")
            self.assertEqual(sassicaia_reloaded["vivino_wine_id"], 12345)

            margaux = [w for w in wines if "Margaux" in w["name"]][0]
            self.assertEqual(margaux["quantity"], 0)
            self.assertEqual(margaux["personal_rating"], 4.9)
            self.assertEqual(margaux["vivino_url"], "https://www.vivino.com/wines/67890")

            stats = db.get_statistics()
            self.assertEqual(stats["total_bottles"], 2)
            self.assertEqual(stats["unique_wines"], 1)
            self.assertEqual(stats["tasted_wines"], 2)

            # Also verify with the actual uploaded user zip if present on disk
            real_zip_path = Path("/usr/local/google/home/maartenw/.gemini/jetski/brain/d789ffa3-5b47-4a4d-bf20-6fc7cecfef15/.user_uploaded/media_1793127093064_fb972978.zip")
            if real_zip_path.exists():
                self.assertTrue(is_vivino_export_zip(real_zip_path, config_dir=config_dir))
                # Create a fresh db to test real zip full extraction
                db2_path = config_dir / "homecave_real.db"
                db2 = WineDatabase(db2_path)
                db2.initialize()
                real_res = import_vivino_export_zip(config_dir, db2, real_zip_path, download_images=False)
                self.assertTrue(real_res["success"])
                self.assertEqual(real_res["cellar_imported"], 54)
                self.assertEqual(real_res["cellar_bottles"], 77)
                self.assertGreaterEqual(real_res["tasted_imported"], 430)

    def test_v054_config_and_options_flow_llm_provider_validation(self) -> None:
        """v0.5.4: Test ConfigFlow and OptionsFlow multi-model provider dropdown and conditional key validation."""
        # 1. Test ConfigFlow initial form schema
        flow = WineInventoryConfigFlow()
        flow.hass = MagicMock()
        async def _fake_set_uid(uid: str) -> None:
            pass
        flow.async_set_unique_id = _fake_set_uid  # type: ignore[assignment]
        flow._abort_if_unique_id_configured = MagicMock()

        form_initial = asyncio.run(flow.async_step_user(None))
        self.assertEqual(form_initial["type"], "form")
        schema_keys = [k.schema if hasattr(k, "schema") else k for k in form_initial["data_schema"].schema.keys()]
        self.assertIn(CONF_LLM_PROVIDER, schema_keys)
        self.assertIn(CONF_GEMINI_API_KEY, schema_keys)
        self.assertIn(CONF_GEMINI_MODEL, schema_keys)
        self.assertIn(CONF_CLAUDE_API_KEY, schema_keys)
        self.assertIn(CONF_CLAUDE_MODEL, schema_keys)

        # 2. ConfigFlow with Gemini selected but empty gemini_api_key -> fails with error
        form_missing_gemini = asyncio.run(
            flow.async_step_user({
                CONF_CELLAR_NAME: "My Cellar",
                CONF_LLM_PROVIDER: LLM_PROVIDER_GEMINI,
                CONF_GEMINI_API_KEY: "",
                CONF_GEMINI_MODEL: DEFAULT_GEMINI_MODEL,
                CONF_CLAUDE_API_KEY: "sk-ant-not-needed",
                CONF_CLAUDE_MODEL: DEFAULT_CLAUDE_MODEL,
            })
        )
        self.assertEqual(form_missing_gemini["type"], "form")
        self.assertEqual(form_missing_gemini["errors"].get("base"), "missing_gemini_api_key")

        # 3. ConfigFlow with Claude selected but empty claude_api_key -> fails with error
        form_missing_claude = asyncio.run(
            flow.async_step_user({
                CONF_CELLAR_NAME: "My Cellar",
                CONF_LLM_PROVIDER: LLM_PROVIDER_CLAUDE,
                CONF_GEMINI_API_KEY: "gemini-key-not-active",
                CONF_GEMINI_MODEL: DEFAULT_GEMINI_MODEL,
                CONF_CLAUDE_API_KEY: "   ",
                CONF_CLAUDE_MODEL: DEFAULT_CLAUDE_MODEL,
            })
        )
        self.assertEqual(form_missing_claude["type"], "form")
        self.assertEqual(form_missing_claude["errors"].get("base"), "missing_claude_api_key")

        # 4. ConfigFlow with Claude selected and valid claude_api_key -> succeeds
        entry_created = asyncio.run(
            flow.async_step_user({
                CONF_CELLAR_NAME: "Grand Cru Cellar",
                CONF_LLM_PROVIDER: LLM_PROVIDER_CLAUDE,
                CONF_CLAUDE_API_KEY: "sk-ant-valid-key",
                CONF_CLAUDE_MODEL: "claude-haiku-5-5",
                CONF_GEMINI_API_KEY: "",
                CONF_GEMINI_MODEL: DEFAULT_GEMINI_MODEL,
            })
        )
        self.assertEqual(entry_created["type"], "create_entry")
        self.assertEqual(entry_created["title"], "Grand Cru Cellar")
        self.assertEqual(entry_created["data"][CONF_LLM_PROVIDER], LLM_PROVIDER_CLAUDE)
        self.assertEqual(entry_created["data"][CONF_CLAUDE_API_KEY], "sk-ant-valid-key")
        self.assertEqual(entry_created["data"][CONF_CLAUDE_MODEL], "claude-haiku-5-5")

        # 5. OptionsFlow settings validation
        entry = MagicMock()
        entry.entry_id = "test_opt_054"
        entry.data = {
            CONF_LLM_PROVIDER: LLM_PROVIDER_GEMINI,
            CONF_GEMINI_API_KEY: "initial-key",
            CONF_GEMINI_MODEL: DEFAULT_GEMINI_MODEL,
            CONF_CLAUDE_API_KEY: "",
            CONF_CLAUDE_MODEL: DEFAULT_CLAUDE_MODEL,
        }
        entry.options = dict(entry.data)
        opts_flow = WineInventoryOptionsFlowHandler(entry)

        # OptionsFlow switch to Claude without key -> error
        opts_missing_claude = asyncio.run(
            opts_flow.async_step_settings({
                CONF_LLM_PROVIDER: LLM_PROVIDER_CLAUDE,
                CONF_CLAUDE_API_KEY: "",
                CONF_CLAUDE_MODEL: "claude-haiku-5-5",
                CONF_GEMINI_API_KEY: "initial-key",
                CONF_GEMINI_MODEL: DEFAULT_GEMINI_MODEL,
            })
        )
        self.assertEqual(opts_missing_claude["type"], "form")
        self.assertEqual(opts_missing_claude["errors"].get("base"), "missing_claude_api_key")

        # OptionsFlow switch to Claude with valid key -> succeeds
        opts_success = asyncio.run(
            opts_flow.async_step_settings({
                CONF_LLM_PROVIDER: LLM_PROVIDER_CLAUDE,
                CONF_CLAUDE_API_KEY: "sk-ant-new-key",
                CONF_CLAUDE_MODEL: "claude-haiku-5-5",
                CONF_GEMINI_API_KEY: "initial-key",
                CONF_GEMINI_MODEL: DEFAULT_GEMINI_MODEL,
            })
        )
        self.assertEqual(opts_success["type"], "create_entry")
        self.assertEqual(opts_success["data"][CONF_LLM_PROVIDER], LLM_PROVIDER_CLAUDE)
        self.assertEqual(opts_success["data"][CONF_CLAUDE_API_KEY], "sk-ant-new-key")

    def test_v057_dashboard_cellar_name_and_sommai_schema(self) -> None:
        """v0.5.7: Test default cellar name 'Wine Cellar', sommai image_data null schema, and dashboard stats."""
        from custom_components.homecave.const import DEFAULT_CELLAR_NAME

        # 1. Default cellar name is "Wine Cellar"
        self.assertEqual(DEFAULT_CELLAR_NAME, "HomeCave")

        # 2. sensor.py contains keep_bottles and past_peak_bottles descriptions
        sensor_file = Path(__file__).resolve().parent.parent / "custom_components" / "homecave" / "sensor.py"
        sensor_code = sensor_file.read_text(encoding="utf-8")
        self.assertIn('key="keep_bottles"', sensor_code)
        self.assertIn('key="past_peak_bottles"', sensor_code)

        # 3. Source check for image_data vol.Any(None, str) in websocket_api.py
        ws_file = Path(__file__).resolve().parent.parent / "custom_components" / "homecave" / "websocket_api.py"
        ws_code = ws_file.read_text(encoding="utf-8")
        self.assertIn('vol.Optional("image_data"): vol.Any(None, str)', ws_code)

        # 4. Dashboard YAML entities match sensor.homecave_*
        dash_path = Path(__file__).resolve().parent.parent / "dashboards" / "homecave-dashboard.yaml"
        with open(dash_path, "r", encoding="utf-8") as f:
            dash_text = f.read()
        self.assertIn("sensor.homecave_total_bottles", dash_text)
        self.assertIn("sensor.homecave_unique_wines", dash_text)
        self.assertIn("sensor.homecave_ready_to_drink", dash_text)
        self.assertIn("sensor.homecave_drink_soon", dash_text)
        self.assertIn("sensor.homecave_estimated_value", dash_text)
        self.assertIn("sensor.homecave_keep_bottles", dash_text)
        self.assertIn("sensor.homecave_past_peak_bottles", dash_text)

    def test_v091_legacy_wine_inventory_backup_and_storage_migration(self) -> None:
        """v0.9.1: Verify legacy wine_inventory.db backups and on-disk storage migrate cleanly to homecave."""
        from custom_components.homecave.coordinator import WineInventoryCoordinator

        # 1. Legacy on-disk <config>/wine_inventory/wine_inventory.db + covers auto-migrate to <config>/homecave/
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir)
            legacy_db_dir = cfg / ("wine_" + "inventory")
            legacy_db_dir.mkdir(parents=True)
            legacy_db = WineDatabase(legacy_db_dir / ("wine_" + "inventory.db"))
            legacy_db.initialize()
            added = legacy_db.add_or_increment_wine(
                winery="Château Haut-Brion",
                name="Premier Grand Cru Classé",
                vintage=2016,
                wine_type="red",
                quantity=2,
            )
            legacy_covers = cfg / "www" / ("wine_" + "inventory") / "covers"
            legacy_covers.mkdir(parents=True)
            img_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01LEGACY_COVER"
            (legacy_covers / "bottle_legacy.jpg").write_bytes(img_bytes)
            legacy_db.update_wine(
                added["id"],
                {"image_url": "/local/" + "wine_inventory/covers/bottle_legacy.jpg"},
            )
            legacy_db.checkpoint_wal()

            fake_hass = SimpleNamespace(
                config=SimpleNamespace(config_dir=str(cfg), path=lambda *parts: str(cfg.joinpath(*parts))),
                bus=SimpleNamespace(async_fire=lambda *a, **kw: None),
                async_add_executor_job=lambda fn, *args: asyncio.sleep(0, result=fn(*args)),
            )
            coord = WineInventoryCoordinator(fake_hass, "entry_mig", {"cellar_name": "HomeCave"})
            asyncio.run(coord.async_initialize())

            self.assertTrue((cfg / "homecave" / "homecave.db").exists())
            self.assertTrue((cfg / "www" / "homecave" / "covers" / "bottle_legacy.jpg").exists())
            self.assertEqual(len(coord.wines), 1)
            self.assertEqual(coord.wines[0]["image_url"], "/local/homecave/covers/bottle_legacy.jpg")

            # 2. Restoring a legacy backup ZIP containing wine_inventory.db restores into homecave.db
            legacy_zip_buf = io.BytesIO()
            with zipfile.ZipFile(legacy_zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(legacy_db.db_path, arcname="wine_" + "inventory.db")
                zf.writestr("covers/bottle_legacy.jpg", img_bytes)

            with tempfile.TemporaryDirectory() as restore_dir:
                rcfg = Path(restore_dir)
                rdb = WineDatabase(rcfg / "homecave" / "homecave.db")
                res = restore_backup_archive(rcfg, rdb, legacy_zip_buf.getvalue())
                self.assertEqual(res["wines_count"], 1)
                self.assertEqual(res["restored_images"], 1)
                restored_wines = rdb.list_wines()
                self.assertEqual(restored_wines[0]["image_url"], "/local/homecave/covers/bottle_legacy.jpg")


if __name__ == "__main__":
    unittest.main()
