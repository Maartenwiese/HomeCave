"""Unit tests for the WineDatabase SQLite engine."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from custom_components.homecave.const import (
    DRINK_STATUS_DRINK_SOON,
    DRINK_STATUS_HOLD,
    DRINK_STATUS_PAST_PEAK,
    DRINK_STATUS_READY,
    DRINK_STATUS_UNKNOWN,
)
from custom_components.homecave.database import WineDatabase, compute_drink_status


class TestWineDatabase(unittest.TestCase):
    """Test cases for SQLite database schema, batch additions, FIFO consumption, and notes."""

    def test_compute_drink_status(self) -> None:
        """Verify drinking window readiness transitions across years."""
        self.assertEqual(
            compute_drink_status(None, None, reference_year=2026),
            DRINK_STATUS_UNKNOWN,
        )
        self.assertEqual(
            compute_drink_status(2028, 2035, reference_year=2026),
            DRINK_STATUS_HOLD,
        )
        self.assertEqual(
            compute_drink_status(2024, 2030, reference_year=2026),
            DRINK_STATUS_READY,
        )
        self.assertEqual(
            compute_drink_status(2020, 2027, reference_year=2026),
            DRINK_STATUS_DRINK_SOON,
        )
        self.assertEqual(
            compute_drink_status(2018, 2024, reference_year=2026),
            DRINK_STATUS_PAST_PEAK,
        )

    def test_add_duplicate_batches_and_consume_fifo(self) -> None:
        """Verify master wine creation, duplicate batch increment, and FIFO/batch consumption."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = WineDatabase(Path(tmpdir) / "homecave.db")
            db.initialize()

            # 1. Add initial 2 bottles bought at Store A
            wine1 = db.add_or_increment_wine(
                winery="Château Margaux",
                name="Pavillon Rouge",
                vintage=2018,
                wine_type="red",
                varietal="Cabernet Sauvignon, Merlot",
                region="Margaux",
                country="France",
                vivino_rating=4.3,
                vivino_ratings_count=3200,
                vivino_url="https://www.vivino.com/wines/12345",
                drink_from_year=2024,
                drink_until_year=2035,
                drink_window_note="Decant 2 hours",
                food_pairings=["Beef", "Lamb"],
                general_notes="Rich cedar and cassis bouquet.",
                quantity=2,
                added_at="2026-10-20T18:00:00+00:00",
                purchase_location="Gall & Gall Amsterdam",
                purchase_price=165.0,
                storage_location="Basement Cellar - Rack A1",
                batch_notes="Bought for anniversary",
            )

            self.assertEqual(wine1["id"], 1)
            self.assertEqual(wine1["quantity"], 2)
            self.assertEqual(wine1["last_purchase_location"], "Gall & Gall Amsterdam")
            self.assertEqual(wine1["last_storage_location"], "Basement Cellar - Rack A1")
            self.assertEqual(len(wine1["batches"]), 1)
            self.assertEqual(len(wine1["timeline"]), 1)

            # 2. Check duplicate match lookup on scan
            matched = db.find_matching_wine("château margaux", "Pavillon Rouge", 2018)
            self.assertIsNotNone(matched)
            assert matched is not None
            self.assertEqual(matched["id"], wine1["id"])

            # 3. Add 1 more bottle of the same wine bought at Store B (stored in Kitchen Fridge)
            wine2 = db.add_or_increment_wine(
                winery="Château Margaux",
                name="Pavillon Rouge",
                vintage=2018,
                wine_type="red",
                quantity=1,
                added_at="2026-10-23T12:00:00+00:00",
                purchase_location="Bordeaux Winery Direct",
                purchase_price=155.0,
                storage_location="Kitchen Wine Fridge - Shelf 2",
                batch_notes="Picked up on vacation",
            )

            self.assertEqual(wine2["id"], wine1["id"])
            self.assertEqual(wine2["quantity"], 3)
            self.assertEqual(len(wine2["batches"]), 2)
            self.assertEqual(
                set(wine2["active_storage_locations"]),
                {"Basement Cellar - Rack A1", "Kitchen Wine Fridge - Shelf 2"},
            )

            # 4. Consume 1 bottle (-1) with personal rating and tasting note
            after_consume = db.adjust_quantity(
                wine_id=wine1["id"],
                delta=-1,
                notes="Opened with ribeye steak; velvet tannins.",
                personal_rating=4.5,
            )
            self.assertEqual(after_consume["quantity"], 2)
            self.assertEqual(after_consume["personal_rating"], 4.5)
            self.assertEqual(len(after_consume["timeline"]), 3)
            self.assertEqual(after_consume["timeline"][0]["event_type"], "consumed")
            self.assertEqual(after_consume["timeline"][0]["delta"], -1)
            self.assertIn("ribeye", after_consume["timeline"][0]["tasting_notes"])

            # 5. Consume remaining 2 bottles so stock hits 0 -> moves to Tasted Archive
            after_empty = db.adjust_quantity(
                wine_id=wine1["id"],
                delta=-2,
                notes="Finished last 2 bottles at dinner party.",
            )
            self.assertEqual(after_empty["quantity"], 0)

            stats = db.get_statistics()
            self.assertEqual(stats["total_bottles"], 0)
            self.assertEqual(stats["unique_wines"], 0)
            self.assertEqual(stats["archived_wines"], 1)
            self.assertIn("Gall & Gall Amsterdam", stats["known_purchase_locations"])
            self.assertIn("Kitchen Wine Fridge - Shelf 2", stats["known_storage_locations"])

    def test_update_wine_and_checkpoint(self) -> None:
        """Verify updating wine notes overview, drinking window, and WAL checkpointing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = WineDatabase(Path(tmpdir) / "homecave.db")
            db.initialize()

            wine = db.add_or_increment_wine(
                winery="Cloudy Bay",
                name="Sauvignon Blanc",
                vintage=2025,
                wine_type="white",
                quantity=4,
                drink_from_year=2025,
                drink_until_year=2027,
                purchase_price=28.50,
            )

            updated = db.update_wine(
                wine["id"],
                {
                    "general_notes": "Crisp passionfruit and lime zest.",
                    "drink_window_note": "Serve chilled at 8°C",
                    "vivino_rating": 4.2,
                },
            )
            self.assertEqual(updated["general_notes"], "Crisp passionfruit and lime zest.")
            self.assertEqual(updated["drink_window_note"], "Serve chilled at 8°C")
            self.assertEqual(updated["vivino_rating"], 4.2)

            stats = db.get_statistics()
            self.assertEqual(stats["total_bottles"], 4)
            self.assertEqual(stats["by_type"]["white"], 4)
            self.assertEqual(stats["estimated_value"], 114.0)

            db.checkpoint_wal()

    def test_storage_location_and_research_notes(self) -> None:
        """Verify storage location updates on wine and batches, and research notes persistence."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = WineDatabase(Path(tmpdir) / "homecave.db")
            db.initialize()

            wine = db.add_or_increment_wine(
                winery="Tenuta San Guido",
                name="Sassicaia",
                vintage=2018,
                wine_type="red",
                varietal="Cabernet Sauvignon, Cabernet Franc",
                region="Bolgheri",
                country="Italy",
                quantity=3,
                storage_location="Wine Fridge Rack 2",
            )
            self.assertEqual(wine["storage_location"], "Wine Fridge Rack 2")
            self.assertEqual(wine["country"], "Italy")
            self.assertEqual(wine["varietal"], "Cabernet Sauvignon, Cabernet Franc")

            research_payload = json.dumps({
                "producer_history": "Pioneering Bolgheri estate founded by Mario Incisa della Rocchetta.",
                "wine_and_terroir": "Gravelly soils similar to Graves in Bordeaux.",
                "summary": "Legendary Super Tuscan icon.",
            })
            updated = db.update_wine(
                wine["id"],
                {
                    "storage_location": "Underground Cellar Bin 4",
                    "research_notes": research_payload,
                },
            )
            self.assertEqual(updated["storage_location"], "Underground Cellar Bin 4")
            self.assertIn("Pioneering Bolgheri estate", updated["research_notes"])

            detail = db.get_wine_detail(wine["id"])
            self.assertIsNotNone(detail)
            assert detail is not None
            self.assertEqual(detail["storage_location"], "Underground Cellar Bin 4")
            self.assertEqual(detail["batches"][0]["storage_location"], "Underground Cellar Bin 4")

            # 3. Test editable purchase location and purchase price
            updated_purchase = db.update_wine(
                wine["id"],
                {
                    "purchase_location": "Boutique Wine Merchant",
                    "purchase_price": 285.50,
                },
            )
            self.assertEqual(updated_purchase["purchase_location"], "Boutique Wine Merchant")
            self.assertEqual(updated_purchase["purchase_price"], 285.50)

            detail_after_price = db.get_wine_detail(wine["id"])
            assert detail_after_price is not None
            self.assertEqual(detail_after_price["purchase_location"], "Boutique Wine Merchant")
            self.assertEqual(detail_after_price["purchase_price"], 285.50)
            self.assertEqual(detail_after_price["batches"][0]["purchase_location"], "Boutique Wine Merchant")
            self.assertEqual(detail_after_price["batches"][0]["purchase_price"], 285.50)

            # 4. Test Unlink Vivino
            db.update_wine(
                wine["id"],
                {
                    "vivino_rating": 4.6,
                    "vivino_ratings_count": 1250,
                    "vivino_url": "https://www.vivino.com/wines/sassicaia-2018",
                    "vivino_price_estimate": 290.0,
                    "rating_source": "vivino",
                },
            )
            detail_with_vivino = db.get_wine_detail(wine["id"])
            assert detail_with_vivino is not None
            self.assertEqual(detail_with_vivino["vivino_rating"], 4.6)
            self.assertEqual(detail_with_vivino["vivino_url"], "https://www.vivino.com/wines/sassicaia-2018")

            unlinked = db.unlink_vivino(wine["id"])
            self.assertIsNone(unlinked["vivino_rating"])
            self.assertIsNone(unlinked["vivino_ratings_count"])
            self.assertEqual(unlinked["vivino_url"], "")
            self.assertIsNone(unlinked["vivino_price_estimate"])
            self.assertEqual(unlinked["rating_source"], "")

            detail_unlinked = db.get_wine_detail(wine["id"])
            assert detail_unlinked is not None
            self.assertIsNone(detail_unlinked["vivino_rating"])
            self.assertEqual(detail_unlinked["vivino_url"], "")

    def test_vivino_grounding_migration_clears_untrusted_links(self) -> None:
        """Gemini-estimated ratings and legacy search links are cleared on startup."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = WineDatabase(Path(tmpdir) / "homecave.db")
            db.initialize()
            est = db.add_or_increment_wine(
                winery="Estate A", name="Cuvee A", vivino_rating=4.1, rating_source="gemini_estimate"
            )
            legacy = db.add_or_increment_wine(
                winery="Antinori", name="Tignanello", vintage=2019, vivino_rating=4.6,
                vivino_url="https://www.vivino.com/search/wines?q=tignanello", rating_source="vivino",
            )
            good = db.add_or_increment_wine(
                winery="Penfolds", name="Grange", vintage=2018, vivino_rating=4.7,
                vivino_url="https://www.vivino.com/wines/160128212", rating_source="vivino",
                vivino_wine_id=160128212, vivino_match_confidence="high",
            )
            db.initialize()  # simulate restart after upgrade

            e = db.get_wine_detail(est["id"]); l = db.get_wine_detail(legacy["id"]); g = db.get_wine_detail(good["id"])
            assert e and l and g
            self.assertIsNone(e["vivino_rating"])
            self.assertEqual(e["rating_source"], "")
            self.assertEqual(l["vivino_url"], "")
            self.assertIsNone(l["vivino_rating"])
            self.assertEqual(g["vivino_url"], "https://www.vivino.com/wines/160128212")
            self.assertEqual(g["vivino_rating"], 4.7)
            self.assertEqual(g["vivino_wine_id"], 160128212)
            self.assertEqual(g["vivino_match_confidence"], "high")

    def test_empty_string_numeric_fields_and_legacy_db_self_healing(self) -> None:
        """v0.3.5 regression: empty strings '' in numeric fields never raise ValueError in float()."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = WineDatabase(Path(tmpdir) / "homecave.db")
            db.initialize()

            # 1. Saving a wine with empty strings "" in numeric fields (like an unlinked scan form)
            wine = db.add_or_increment_wine(
                winery="Bodegas Muga",
                name="Reserva",
                vintage="",  # type: ignore[arg-type]
                vivino_rating="",  # type: ignore[arg-type]
                vivino_price_estimate="",  # type: ignore[arg-type]
                purchase_price="",  # type: ignore[arg-type]
                personal_rating="",  # type: ignore[arg-type]
                alcohol_pct="",  # type: ignore[arg-type]
                drink_from_year="",  # type: ignore[arg-type]
                drink_until_year="",  # type: ignore[arg-type]
                quantity=2,
            )
            self.assertIsNone(wine["vintage"])
            self.assertIsNone(wine["vivino_rating"])
            self.assertIsNone(wine["vivino_price_estimate"])
            self.assertIsNone(wine["purchase_price"])
            stats = db.get_statistics()
            self.assertEqual(stats["total_bottles"], 2)
            self.assertEqual(stats["estimated_value"], 0.0)

            # 2. Updating a wine with empty strings "" in numeric fields
            updated = db.update_wine(
                wine["id"],
                {
                    "vintage": "",
                    "purchase_price": "",
                    "vivino_rating": "",
                    "vivino_price_estimate": "",
                    "drink_from_year": "",
                    "drink_until_year": "",
                    "personal_rating": "",
                },
            )
            self.assertIsNone(updated["purchase_price"])
            self.assertIsNone(updated["vivino_price_estimate"])
            self.assertEqual(db.get_statistics()["estimated_value"], 0.0)

            # 3. Simulate a pre-v0.3.5 database row that already has '' stored in REAL/INTEGER columns
            with db._connect() as conn, conn:
                conn.execute(
                    """
                    UPDATE wines
                    SET vivino_price_estimate = '',
                        vivino_rating = '',
                        purchase_price = '',
                        drink_from_year = '',
                        drink_until_year = ''
                    WHERE id = ?
                    """,
                    (wine["id"],),
                )
            # Even before re-running initialize(), list_wines() and get_statistics() must not fail
            stats_before_init = db.get_statistics()
            self.assertEqual(stats_before_init["total_bottles"], 2)
            self.assertEqual(stats_before_init["estimated_value"], 0.0)

            # Running initialize() heals the raw SQLite columns back to NULL
            db.initialize()
            with db._connect() as conn:
                raw_row = conn.execute(
                    "SELECT vivino_price_estimate, vivino_rating, purchase_price, drink_from_year FROM wines WHERE id = ?",
                    (wine["id"],),
                ).fetchone()
                self.assertIsNone(raw_row["vivino_price_estimate"])
                self.assertIsNone(raw_row["vivino_rating"])
                self.assertIsNone(raw_row["purchase_price"])
                self.assertIsNone(raw_row["drink_from_year"])

    def test_v036_url_scheme_sanitization(self) -> None:
        """v0.3.6: Verify unsafe URI schemes (javascript:, data:text/html, non-vivino hosts) are stripped from vivino_url and image_url."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = WineDatabase(Path(tmpdir) / "homecave.db")
            db.initialize()

            # 1. Attempting to add a wine with javascript: in vivino_url and image_url strips them to ''
            bad = db.add_or_increment_wine(
                winery="Evil Winery",
                name="XSS Cuvee",
                vivino_url="javascript:alert(document.cookie)",
                image_url="javascript:alert(1)",
            )
            self.assertEqual(bad["vivino_url"], "")
            self.assertEqual(bad["image_url"], "")

            # 2. Attempting to update vivino_url to a non-vivino domain or non-https strips it to ''
            updated_bad = db.update_wine(
                bad["id"],
                {
                    "vivino_url": "https://evil.example.com/w/1234",
                    "image_url": "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
                },
            )
            self.assertEqual(updated_bad["vivino_url"], "")
            self.assertEqual(updated_bad["image_url"], "")

            # 3. Valid https://www.vivino.com/... preserved; legacy /local/ covers canonicalised (v0.9.5)
            updated_good = db.update_wine(
                bad["id"],
                {
                    "vivino_url": "https://www.vivino.com/w/1652?year=2019",
                    "image_url": "/local/homecave/covers/cover_1.jpg",
                },
            )
            self.assertEqual(updated_good["vivino_url"], "https://www.vivino.com/w/1652?year=2019")
            self.assertEqual(updated_good["image_url"], "/api/homecave/cover/cover_1.jpg")

    def test_v038_tasted_section_and_consumed_from_cellar(self) -> None:
        """v0.3.8: Verify scanning/adding directly to Tasted (0 stock) and cellar wines consumed by 1+ showing in Tasted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = WineDatabase(Path(tmpdir) / "homecave.db")
            db.initialize()

            # 1. Scan / add a wine directly to Tasted (as_tasted=True)
            tasted_only = db.add_or_increment_wine(
                winery="Domaine Weinbach",
                name="Riesling Schlossberg Grand Cru",
                vintage=2021,
                wine_type="white",
                varietal="Riesling",
                region="Alsace",
                country="France",
                personal_rating=4.7,
                as_tasted=True,
                batch_notes="Drank at restaurant in Colmar — electric minerality!",
            )
            self.assertEqual(tasted_only["quantity"], 0)
            self.assertTrue(tasted_only["is_tasted"])
            self.assertEqual(tasted_only["tasted_count"], 1)
            self.assertEqual(tasted_only["tasted_bottles"], 1)
            self.assertEqual(tasted_only["personal_rating"], 4.7)
            self.assertEqual(len(tasted_only["batches"]), 0)
            self.assertEqual(len(tasted_only["timeline"]), 1)
            self.assertEqual(tasted_only["timeline"][0]["event_type"], "tasted")

            # 2. Add a wine to Cellar with 3 bottles — initially NOT tasted
            cellar_wine = db.add_or_increment_wine(
                winery="Vega Sicilia",
                name="Valbuena 5°",
                vintage=2018,
                wine_type="red",
                quantity=3,
            )
            self.assertEqual(cellar_wine["quantity"], 3)
            self.assertFalse(cellar_wine["is_tasted"])
            self.assertEqual(cellar_wine["tasted_count"], 0)

            # 3. Consume 1 bottle from the cellar (3 -> 2): now in BOTH Cellar (quantity=2) and Tasted (is_tasted=True)!
            after_consume = db.adjust_quantity(
                wine_id=cellar_wine["id"],
                delta=-1,
                personal_rating=4.8,
                notes="Opened for Sunday roast",
            )
            self.assertEqual(after_consume["quantity"], 2)
            self.assertTrue(after_consume["is_tasted"])
            self.assertEqual(after_consume["tasted_count"], 1)
            self.assertEqual(after_consume["tasted_bottles"], 1)

            stats = db.get_statistics()
            self.assertEqual(stats["total_bottles"], 2)
            self.assertEqual(stats["unique_wines"], 1)
            self.assertEqual(stats["tasted_wines"], 2)
            self.assertEqual(stats["tasted_bottles"], 2)

    def test_v053_compute_drink_status_soon_and_keep(self) -> None:
        """v0.5.3: Verify Soon label strictly includes close Until dates, and Keep for future wines."""
        # 1. Keep / Hold: drink_from_year is in the future (> current_year)
        self.assertEqual(
            compute_drink_status(2028, 2035, reference_year=2026),
            DRINK_STATUS_HOLD,
        )
        self.assertEqual(
            compute_drink_status(2027, None, reference_year=2026),
            DRINK_STATUS_HOLD,
        )

        # 2. Soon: drink_until_year is close (0 or 1 years away)
        self.assertEqual(
            compute_drink_status(2020, 2026, reference_year=2026),
            DRINK_STATUS_DRINK_SOON,
        )
        self.assertEqual(
            compute_drink_status(2020, 2027, reference_year=2026),
            DRINK_STATUS_DRINK_SOON,
        )

        # 3. Ready: within window, until_year is comfortably in the future (> 1 year away)
        self.assertEqual(
            compute_drink_status(2020, 2030, reference_year=2026),
            DRINK_STATUS_READY,
        )

        # 4. Past Peak: until_year is in the past (< current_year)
        self.assertEqual(
            compute_drink_status(2015, 2024, reference_year=2026),
            DRINK_STATUS_PAST_PEAK,
        )

        # 5. Cellar statistics include keep_bottles
        with tempfile.TemporaryDirectory() as tmpdir:
            db = WineDatabase(Path(tmpdir) / "homecave.db")
            db.initialize()
            # Keep wine
            db.add_or_increment_wine(
                winery="Tenuta San Guido",
                name="Sassicaia",
                vintage=2022,
                wine_type="red",
                drink_from_year=2030,
                drink_until_year=2045,
                quantity=3,
            )
            # Soon wine (e.g. current year 2026, until 2027)
            db.add_or_increment_wine(
                winery="Louis Jadot",
                name="Chablis",
                vintage=2021,
                wine_type="white",
                drink_from_year=2022,
                drink_until_year=datetime.now(timezone.utc).year,
                quantity=2,
            )
            stats = db.get_statistics()
            self.assertEqual(stats["hold_bottles"], 3)
            self.assertEqual(stats["keep_bottles"], 3)
            self.assertEqual(stats["drink_soon_bottles"], 2)

    def test_v056_database_is_imported_flag_and_migration(self) -> None:
        """v0.5.6: Verify is_imported column, default 0, True when imported, and migration backfill."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "homecave.db"
            db = WineDatabase(db_path)
            db.initialize()

            # 1. Normal wine addition defaults to is_imported=False
            normal_wine = db.add_or_increment_wine(
                winery="Domaine Dujac",
                name="Morey-Saint-Denis",
                vintage=2020,
                wine_type="red",
                quantity=2,
            )
            detail_normal = db.get_wine_detail(normal_wine["id"])
            self.assertIsNotNone(detail_normal)
            assert detail_normal is not None
            self.assertFalse(detail_normal["is_imported"])
            self.assertIsInstance(detail_normal["is_imported"], bool)

            # 2. Wine added with is_imported=True
            imported_wine = db.add_or_increment_wine(
                winery="Domaine des Chênes",
                name="Razungles",
                vintage=2019,
                wine_type="red",
                quantity=1,
                is_imported=True,
            )
            detail_imported = db.get_wine_detail(imported_wine["id"])
            self.assertIsNotNone(detail_imported)
            assert detail_imported is not None
            self.assertTrue(detail_imported["is_imported"])

            # 3. update_wine can toggle or set is_imported
            updated = db.update_wine(normal_wine["id"], {"is_imported": True})
            self.assertTrue(updated["is_imported"])

        # 4. Migration backfill on legacy database lacking is_imported column
        with tempfile.TemporaryDirectory() as tmpdir:
            legacy_db_path = Path(tmpdir) / "legacy_homecave.db"
            # Create a raw SQLite schema without is_imported
            with sqlite3.connect(legacy_db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE wines (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        winery TEXT NOT NULL,
                        name TEXT NOT NULL,
                        vintage INTEGER NULL,
                        wine_type TEXT NOT NULL DEFAULT 'red',
                        varietal TEXT NOT NULL DEFAULT '',
                        region TEXT NOT NULL DEFAULT '',
                        country TEXT NOT NULL DEFAULT '',
                        alcohol_pct REAL NULL,
                        bottle_size_ml INTEGER NOT NULL DEFAULT 750,
                        vivino_rating REAL NULL,
                        vivino_ratings_count INTEGER NULL,
                        vivino_url TEXT NOT NULL DEFAULT '',
                        vivino_price_estimate REAL NULL,
                        rating_source TEXT NOT NULL DEFAULT '',
                        vivino_wine_id INTEGER NULL,
                        vivino_match_confidence TEXT NOT NULL DEFAULT '',
                        drink_from_year INTEGER NULL,
                        drink_until_year INTEGER NULL,
                        drink_window_note TEXT NOT NULL DEFAULT '',
                        food_pairings TEXT NOT NULL DEFAULT '[]',
                        general_notes TEXT NOT NULL DEFAULT '',
                        personal_rating REAL NULL,
                        image_url TEXT NOT NULL DEFAULT '',
                        quantity INTEGER NOT NULL DEFAULT 0,
                        storage_location TEXT NOT NULL DEFAULT '',
                        purchase_location TEXT NOT NULL DEFAULT '',
                        purchase_price REAL NULL,
                        research_notes TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    """
                )
                # Insert a legacy wine with vivino image
                conn.execute(
                    """
                    INSERT INTO wines (winery, name, vintage, image_url, rating_source, created_at, updated_at)
                    VALUES ('Château Palmer', 'Alter Ego', 2015, '/local/wine_inventory/covers/vivino_palmer.jpg', 'vivino', '2026-01-01', '2026-01-01');
                    """
                )
                # Insert a legacy wine with rating_source='vivino_import'
                conn.execute(
                    """
                    INSERT INTO wines (winery, name, vintage, image_url, rating_source, created_at, updated_at)
                    VALUES ('Penfolds', 'Grange', 2012, '', 'vivino_import', '2026-01-01', '2026-01-01');
                    """
                )
                # Insert a regular wine
                conn.execute(
                    """
                    INSERT INTO wines (winery, name, vintage, image_url, rating_source, created_at, updated_at)
                    VALUES ('Local Winery', 'House Red', 2021, '', '', '2026-01-01', '2026-01-01');
                    """
                )
                conn.commit()

            # Initialize WineDatabase over legacy DB
            migrated_db = WineDatabase(legacy_db_path)
            migrated_db.initialize()

            wines = migrated_db.list_wines(include_out_of_stock=True)
            self.assertEqual(len(wines), 3)
            palmer = next(w for w in wines if w["name"] == "Alter Ego")
            grange = next(w for w in wines if w["name"] == "Grange")
            house = next(w for w in wines if w["name"] == "House Red")

            self.assertTrue(palmer["is_imported"])
            self.assertEqual(palmer["image_url"], "/api/homecave/cover/vivino_palmer.jpg")
            self.assertTrue(grange["is_imported"])
            self.assertFalse(house["is_imported"])


if __name__ == "__main__":
    unittest.main()
