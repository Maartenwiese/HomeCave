"""Local SQLite database engine for Wine Inventory."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any
from urllib.parse import urlparse

from .const import (
    DRINK_STATUS_DRINK_SOON,
    DRINK_STATUS_HOLD,
    DRINK_STATUS_PAST_PEAK,
    DRINK_STATUS_READY,
    DRINK_STATUS_UNKNOWN,
    WINE_TYPE_RED,
    WINE_TYPES,
)
from .covers import normalize_cover_ref


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _opt_float(val: Any) -> float | None:
    """Safely convert a value to float, returning None for None, empty string, or non-numeric."""
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    text = str(val).strip()
    if not text:
        return None
    try:
        return float(text)
    except (ValueError, TypeError):
        return None


def _opt_int(val: Any) -> int | None:
    """Safely convert a value to int, returning None for None, empty string, or non-numeric."""
    num = _opt_float(val)
    if num is None:
        return None
    try:
        return int(num)
    except (ValueError, TypeError):
        return None


def _sanitize_vivino_url(url: Any) -> str:
    """Allow only empty string or canonical HTTPS Vivino URLs (rejects javascript: and other origins)."""
    raw = str(url or "").strip()
    if not raw or any(ch in raw for ch in ("\r", "\n", "\x00", "<", ">", '"', "'")):
        return ""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return ""
    if parsed.scheme.lower() != "https":
        return ""
    host = (parsed.hostname or "").lower()
    if host == "vivino.com" or host.endswith(".vivino.com"):
        return raw
    return ""


def _sanitize_image_url(url: Any) -> str:
    """Allow only private HomeCave cover paths, Vivino's image CDN, or raster data URIs.

    Private covers are always stored in canonical, unsigned form ``/api/homecave/cover/<file>``:
    any ``?authSig=...`` echoed back by a client is stripped, and legacy public
    ``/local/homecave/covers/`` / ``/local/wine_inventory/covers/`` paths are mapped to it.
    Arbitrary external hosts are rejected so a stored image URL can never act as a tracking
    pixel (or other third-party request) inside the Home Assistant origin.
    """
    raw = str(url or "").strip()
    if not raw or any(ch in raw for ch in ("\r", "\n", "\x00", "<", ">", '"', "'", "\\")):
        return ""
    cover_ref = normalize_cover_ref(raw)
    if cover_ref is not None:
        return cover_ref  # canonical path, or "" for an invalid cover filename
    if re.match(r"^data:image/(jpeg|jpg|png|webp);base64,[A-Za-z0-9+/=\s]+$", raw, re.IGNORECASE):
        return raw
    try:
        parsed = urlparse(raw)
    except ValueError:
        return ""
    if parsed.scheme.lower() == "https" and (parsed.hostname or "").lower() == "images.vivino.com":
        return raw
    return ""


def compute_drink_status(
    drink_from_year: int | str | None,
    drink_until_year: int | str | None,
    reference_year: int | None = None,
) -> str:
    """Compute drinking readiness status from structured drink window years."""
    current_year = reference_year or datetime.now(timezone.utc).year
    from_year = _opt_int(drink_from_year)
    until_year = _opt_int(drink_until_year)
    if from_year is None and until_year is None:
        return DRINK_STATUS_UNKNOWN

    if from_year is not None and current_year < from_year:
        return DRINK_STATUS_HOLD

    if until_year is not None:
        if current_year > until_year:
            return DRINK_STATUS_PAST_PEAK
        if (until_year - current_year) <= 1:
            return DRINK_STATUS_DRINK_SOON

    return DRINK_STATUS_READY


class WineDatabase:
    """SQLite database manager for the Home Assistant Wine Inventory."""

    def __init__(self, db_path: str | Path) -> None:
        """Initialize the database manager with a file path."""
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        """Create a SQLite connection configured with Row factory and WAL mode."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def initialize(self) -> None:
        """Create schema tables and indexes if they do not exist."""
        with closing(self._connect()) as conn, conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS wines (
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
                    is_imported INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_wines_lookup
                    ON wines (LOWER(winery), LOWER(name), IFNULL(vintage, 0));

                CREATE INDEX IF NOT EXISTS idx_wines_type_qty
                    ON wines (wine_type, quantity);

                CREATE TABLE IF NOT EXISTS inventory_additions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wine_id INTEGER NOT NULL REFERENCES wines(id) ON DELETE CASCADE,
                    quantity_added INTEGER NOT NULL,
                    quantity_remaining INTEGER NOT NULL,
                    added_at TEXT NOT NULL,
                    purchase_location TEXT NOT NULL DEFAULT '',
                    purchase_price REAL NULL,
                    storage_location TEXT NOT NULL DEFAULT '',
                    drink_window_note TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_additions_wine_id
                    ON inventory_additions (wine_id, added_at DESC);

                CREATE TABLE IF NOT EXISTS consumption_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wine_id INTEGER NOT NULL REFERENCES wines(id) ON DELETE CASCADE,
                    batch_id INTEGER NULL REFERENCES inventory_additions(id) ON DELETE SET NULL,
                    event_type TEXT NOT NULL,
                    delta INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    purchase_location TEXT NOT NULL DEFAULT '',
                    storage_location TEXT NOT NULL DEFAULT '',
                    tasting_notes TEXT NOT NULL DEFAULT '',
                    personal_rating REAL NULL
                );

                CREATE INDEX IF NOT EXISTS idx_history_wine_id
                    ON consumption_history (wine_id, timestamp DESC);
                """
            )
            # Automatic schema migration for existing databases
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(wines)").fetchall()}
            if "storage_location" not in cols:
                conn.execute("ALTER TABLE wines ADD COLUMN storage_location TEXT NOT NULL DEFAULT '';")
            if "purchase_location" not in cols:
                conn.execute("ALTER TABLE wines ADD COLUMN purchase_location TEXT NOT NULL DEFAULT '';")
            if "purchase_price" not in cols:
                conn.execute("ALTER TABLE wines ADD COLUMN purchase_price REAL NULL;")
            if "research_notes" not in cols:
                conn.execute("ALTER TABLE wines ADD COLUMN research_notes TEXT NOT NULL DEFAULT '';")
            if "vivino_wine_id" not in cols:
                conn.execute("ALTER TABLE wines ADD COLUMN vivino_wine_id INTEGER NULL;")
            if "vivino_match_confidence" not in cols:
                conn.execute("ALTER TABLE wines ADD COLUMN vivino_match_confidence TEXT NOT NULL DEFAULT '';")
            if "is_imported" not in cols:
                conn.execute("ALTER TABLE wines ADD COLUMN is_imported INTEGER NOT NULL DEFAULT 0;")
                conn.execute(
                    "UPDATE wines SET is_imported = 1 WHERE image_url LIKE '%/covers/vivino_%' OR rating_source = 'vivino_import';"
                )
            self._migrate_vivino_grounding(conn)
            self._migrate_empty_numeric_strings(conn)

    @staticmethod
    def _migrate_empty_numeric_strings(conn: sqlite3.Connection) -> None:
        """Clean up any empty strings '' stored in numeric SQLite columns (idempotent)."""
        numeric_cols = (
            "vintage",
            "alcohol_pct",
            "vivino_rating",
            "vivino_ratings_count",
            "vivino_price_estimate",
            "vivino_wine_id",
            "drink_from_year",
            "drink_until_year",
            "personal_rating",
            "purchase_price",
        )
        for col in numeric_cols:
            conn.execute(f"UPDATE wines SET {col} = NULL WHERE {col} = '';")
        conn.execute(
            "UPDATE inventory_additions SET purchase_price = NULL WHERE purchase_price = '';"
        )
        conn.execute(
            "UPDATE consumption_history SET personal_rating = NULL WHERE personal_rating = '';"
        )

    @staticmethod
    def _migrate_vivino_grounding(conn: sqlite3.Connection) -> None:
        """One-time v0.2.8 cleanup of unverifiable Vivino data (idempotent).

        1. Ratings invented by the removed Gemini estimate fallback are cleared.
        2. Legacy ``https://www.vivino.com/wines/<id>`` links (a route that redirects to
           unrelated wines) are cleared together with their ratings, because the stored id
           cannot be trusted.
        3. Search-page URLs stored for unlinked wines are cleared.
        """
        conn.execute(
            """
            UPDATE wines
            SET vivino_rating = NULL, vivino_ratings_count = NULL,
                vivino_price_estimate = NULL, rating_source = '',
                vivino_match_confidence = 'none'
            WHERE rating_source IN ('gemini', 'gemini_estimate')
            """
        )
        conn.execute(
            """
            UPDATE wines
            SET vivino_url = '', vivino_rating = NULL, vivino_ratings_count = NULL,
                vivino_price_estimate = NULL, rating_source = '', vivino_wine_id = NULL,
                vivino_match_confidence = 'none'
            WHERE vivino_url LIKE '%vivino.com/search/%'
            """
        )
        conn.execute(
            """
            UPDATE wines
            SET image_url = REPLACE(image_url, '/local/wine_inventory/covers/', '/local/homecave/covers/')
            WHERE image_url LIKE '/local/wine_inventory/covers/%'
            """
        )
        # v0.9.5: covers moved from public www/ to private storage served by an authenticated view
        conn.execute(
            """
            UPDATE wines
            SET image_url = REPLACE(image_url, '/local/homecave/covers/', '/api/homecave/cover/')
            WHERE image_url LIKE '/local/homecave/covers/%'
            """
        )

    def checkpoint_wal(self) -> None:
        """Flush the SQLite Write-Ahead Log before a Home Assistant backup."""
        if not self.db_path.exists():
            return
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

    def _serialize_wine_row(
        self,
        row: sqlite3.Row,
        batches: list[dict[str, Any]] | None = None,
        timeline: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Convert a sqlite3.Row from wines into an enriched dict."""
        data = dict(row)
        try:
            pairings = json.loads(data.get("food_pairings") or "[]")
            if not isinstance(pairings, list):
                pairings = [str(pairings)]
        except (ValueError, TypeError):
            raw = data.get("food_pairings") or ""
            pairings = [p.strip() for p in str(raw).split(",") if p.strip()]

        data["food_pairings"] = pairings
        for int_col in (
            "vintage",
            "vivino_ratings_count",
            "vivino_wine_id",
            "drink_from_year",
            "drink_until_year",
        ):
            data[int_col] = _opt_int(data.get(int_col))
        for float_col in (
            "alcohol_pct",
            "vivino_rating",
            "vivino_price_estimate",
            "personal_rating",
            "purchase_price",
        ):
            data[float_col] = _opt_float(data.get(float_col))
        if batches is not None:
            for b in batches:
                b["purchase_price"] = _opt_float(b.get("purchase_price"))
                # Defense in depth: a crafted/restored DB must never yield non-numeric values here
                for int_col in ("id", "wine_id", "quantity_added", "quantity_remaining"):
                    b[int_col] = _opt_int(b.get(int_col)) or 0
        if timeline is not None:
            for h in timeline:
                h["personal_rating"] = _opt_float(h.get("personal_rating"))
                for int_col in ("id", "wine_id", "delta"):
                    h[int_col] = _opt_int(h.get(int_col)) or 0
                h["batch_id"] = _opt_int(h.get("batch_id"))
        for int_col in ("id", "quantity", "bottle_size_ml"):
            data[int_col] = _opt_int(data.get(int_col)) or 0

        data["drink_status"] = compute_drink_status(
            data.get("drink_from_year"),
            data.get("drink_until_year"),
        )
        data["is_imported"] = bool(data.get("is_imported", 0))
        data["vivino_url"] = _sanitize_vivino_url(data.get("vivino_url"))
        data["image_url"] = _sanitize_image_url(data.get("image_url"))
        data["storage_location"] = (data.get("storage_location") or "").strip()
        data["purchase_location"] = (
            data.get("purchase_location")
            or data.get("last_purchase_location")
            or ""
        ).strip()
        data["research_notes"] = data.get("research_notes") or ""
        if batches is not None:
            data["batches"] = batches
            data["last_purchase_location"] = next(
                (b["purchase_location"] for b in batches if b.get("purchase_location")),
                "",
            )
            data["last_storage_location"] = next(
                (b["storage_location"] for b in batches if b.get("storage_location")),
                "",
            )
            if not data["storage_location"] and data["last_storage_location"]:
                data["storage_location"] = data["last_storage_location"]
            if not data["purchase_location"] and data["last_purchase_location"]:
                data["purchase_location"] = data["last_purchase_location"]
            data["last_purchase_price"] = next(
                (
                    b["purchase_price"]
                    for b in batches
                    if b.get("purchase_price") is not None
                ),
                None,
            )
            active_locations = sorted(
                {
                    b["storage_location"]
                    for b in batches
                    if b.get("storage_location") and b.get("quantity_remaining", 0) > 0
                }
            )
            data["active_storage_locations"] = active_locations
            data["latest_added_at"] = batches[0]["added_at"] if batches else data["created_at"]
        data["purchase_price"] = (
            data.get("purchase_price")
            if data.get("purchase_price") is not None
            else data.get("last_purchase_price")
        )
        if timeline is not None:
            data["timeline"] = timeline
            consumed_events = [
                h
                for h in timeline
                if h.get("event_type") in ("consumed", "tasted")
                or (_opt_int(h.get("delta")) or 0) < 0
            ]
            qty_now = _opt_int(data.get("quantity")) or 0
            is_tasted = bool(consumed_events) or qty_now == 0
            tasted_bottles = sum(
                max(1, abs(_opt_int(h.get("delta")) or 1))
                for h in consumed_events
            )
            if is_tasted and tasted_bottles == 0:
                tasted_bottles = 1
            data["is_tasted"] = is_tasted
            data["tasted_count"] = len(consumed_events) or (1 if is_tasted else 0)
            data["tasted_bottles"] = tasted_bottles
            data["last_tasted_at"] = (
                consumed_events[0]["timestamp"]
                if consumed_events
                else (data.get("updated_at") if is_tasted else None)
            )
        return data

    def find_matching_wine(
        self,
        winery: str,
        name: str,
        vintage: int | str | None,
    ) -> dict[str, Any] | None:
        """Find an existing wine by normalized (winery, name, vintage)."""
        norm_winery = (winery or "").strip().lower()
        norm_name = (name or "").strip().lower()
        v_int = _opt_int(vintage)
        norm_vintage = v_int if v_int and v_int > 0 else 0
        if not norm_winery and not norm_name:
            return None

        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT * FROM wines
                WHERE LOWER(TRIM(winery)) = ?
                  AND LOWER(TRIM(name)) = ?
                  AND IFNULL(vintage, 0) = ?
                LIMIT 1
                """,
                (norm_winery, norm_name, norm_vintage),
            ).fetchone()
            if row is None:
                return None
            return self.get_wine_detail(row["id"], _conn=conn)

    def get_wine_detail(
        self,
        wine_id: int,
        _conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        """Retrieve a single wine with its batches and chronological notes timeline."""
        if _conn is not None:
            return self._get_wine_detail_with_conn(_conn, wine_id)
        with closing(self._connect()) as conn:
            return self._get_wine_detail_with_conn(conn, wine_id)

    def _get_wine_detail_with_conn(
        self,
        conn: sqlite3.Connection,
        wine_id: int,
    ) -> dict[str, Any] | None:
        row = conn.execute("SELECT * FROM wines WHERE id = ?", (wine_id,)).fetchone()
        if row is None:
            return None
        batch_rows = conn.execute(
            """
            SELECT * FROM inventory_additions
            WHERE wine_id = ?
            ORDER BY added_at DESC, id DESC
            """,
            (wine_id,),
        ).fetchall()
        history_rows = conn.execute(
            """
            SELECT * FROM consumption_history
            WHERE wine_id = ?
            ORDER BY timestamp DESC, id DESC
            """,
            (wine_id,),
        ).fetchall()
        batches = [dict(b) for b in batch_rows]
        timeline = [dict(h) for h in history_rows]
        return self._serialize_wine_row(row, batches=batches, timeline=timeline)

    def add_or_increment_wine(
        self,
        *,
        winery: str,
        name: str,
        vintage: int | None = None,
        wine_type: str = WINE_TYPE_RED,
        varietal: str = "",
        region: str = "",
        country: str = "",
        alcohol_pct: float | None = None,
        bottle_size_ml: int = 750,
        vivino_rating: float | None = None,
        vivino_ratings_count: int | None = None,
        vivino_url: str = "",
        vivino_price_estimate: float | None = None,
        rating_source: str = "",
        vivino_wine_id: int | None = None,
        vivino_match_confidence: str = "",
        drink_from_year: int | None = None,
        drink_until_year: int | None = None,
        drink_window_note: str = "",
        food_pairings: list[str] | str | None = None,
        general_notes: str = "",
        personal_rating: float | None = None,
        image_url: str = "",
        quantity: int = 1,
        added_at: str | None = None,
        purchase_location: str = "",
        purchase_price: float | None = None,
        storage_location: str = "",
        batch_notes: str = "",
        wine_id: int | None = None,
        as_tasted: bool = False,
        is_imported: bool = False,
    ) -> dict[str, Any]:
        """Add a new wine, add a purchase batch, or log a wine directly as Tasted (0 cellar stock added)."""
        raw_qty = _opt_int(quantity)
        is_tasted_entry = bool(as_tasted) or (raw_qty is not None and raw_qty == 0)
        qty = max(1, raw_qty if raw_qty and raw_qty > 0 else 1)
        cellar_qty_delta = 0 if is_tasted_entry else qty
        timestamp = (added_at or "").strip() or utc_now_iso()
        normalized_type = (
            wine_type.lower().strip()
            if wine_type and wine_type.lower().strip() in WINE_TYPES
            else WINE_TYPE_RED
        )
        v_int = _opt_int(vintage)
        normalized_vintage = v_int if v_int and v_int > 0 else None
        alcohol_pct = _opt_float(alcohol_pct)
        bottle_size_ml = _opt_int(bottle_size_ml) or 750
        vivino_rating = _opt_float(vivino_rating)
        vivino_ratings_count = _opt_int(vivino_ratings_count)
        vivino_price_estimate = _opt_float(vivino_price_estimate)
        vivino_wine_id = _opt_int(vivino_wine_id)
        drink_from_year = _opt_int(drink_from_year)
        drink_until_year = _opt_int(drink_until_year)
        personal_rating = _opt_float(personal_rating)
        purchase_price = _opt_float(purchase_price)
        wine_id = _opt_int(wine_id)
        vivino_url = _sanitize_vivino_url(vivino_url)
        image_url = _sanitize_image_url(image_url)

        if isinstance(food_pairings, list):
            pairings_json = json.dumps([str(p).strip() for p in food_pairings if str(p).strip()])
        elif isinstance(food_pairings, str) and food_pairings.strip():
            pairings_json = json.dumps(
                [p.strip() for p in food_pairings.split(",") if p.strip()]
            )
        else:
            pairings_json = "[]"

        with closing(self._connect()) as conn, conn:
            existing_row = None
            if wine_id is not None:
                existing_row = conn.execute(
                    "SELECT * FROM wines WHERE id = ?", (int(wine_id),)
                ).fetchone()
            if existing_row is None:
                existing_row = conn.execute(
                    """
                    SELECT * FROM wines
                    WHERE LOWER(TRIM(winery)) = ?
                      AND LOWER(TRIM(name)) = ?
                      AND IFNULL(vintage, 0) = ?
                    LIMIT 1
                    """,
                    (
                        winery.strip().lower(),
                        name.strip().lower(),
                        normalized_vintage or 0,
                    ),
                ).fetchone()

            is_imported_int = 1 if is_imported else 0
            if existing_row is None:
                cursor = conn.execute(
                    """
                    INSERT INTO wines (
                        winery, name, vintage, wine_type, varietal, region, country,
                        alcohol_pct, bottle_size_ml, vivino_rating, vivino_ratings_count,
                        vivino_url, vivino_price_estimate, rating_source,
                        vivino_wine_id, vivino_match_confidence,
                        drink_from_year, drink_until_year, drink_window_note,
                        food_pairings, general_notes, personal_rating, image_url,
                        quantity, storage_location, purchase_location, purchase_price,
                        research_notes, is_imported, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        winery.strip(),
                        name.strip(),
                        normalized_vintage,
                        normalized_type,
                        varietal.strip(),
                        region.strip(),
                        country.strip(),
                        alcohol_pct,
                        int(bottle_size_ml or 750),
                        vivino_rating,
                        vivino_ratings_count,
                        vivino_url.strip(),
                        vivino_price_estimate,
                        rating_source.strip(),
                        vivino_wine_id,
                        (vivino_match_confidence or "").strip(),
                        drink_from_year,
                        drink_until_year,
                        drink_window_note.strip(),
                        pairings_json,
                        general_notes.strip(),
                        personal_rating,
                        image_url.strip(),
                        cellar_qty_delta,
                        storage_location.strip(),
                        purchase_location.strip(),
                        purchase_price,
                        "",
                        is_imported_int,
                        timestamp,
                        timestamp,
                    ),
                )
                target_wine_id = int(cursor.lastrowid)
            else:
                target_wine_id = int(existing_row["id"])
                new_qty = int(existing_row["quantity"]) + cellar_qty_delta
                updated_general_notes = (
                    general_notes.strip()
                    if general_notes.strip()
                    else existing_row["general_notes"]
                )
                conn.execute(
                    """
                    UPDATE wines
                    SET quantity = ?,
                        storage_location = CASE WHEN ? != '' THEN ? ELSE storage_location END,
                        purchase_location = CASE WHEN ? != '' THEN ? ELSE purchase_location END,
                        purchase_price = COALESCE(?, purchase_price),
                        vivino_rating = COALESCE(?, vivino_rating),
                        vivino_ratings_count = COALESCE(?, vivino_ratings_count),
                        vivino_url = CASE WHEN ? != '' THEN ? ELSE vivino_url END,
                        vivino_price_estimate = COALESCE(?, vivino_price_estimate),
                        rating_source = CASE WHEN ? != '' THEN ? ELSE rating_source END,
                        vivino_wine_id = COALESCE(?, vivino_wine_id),
                        vivino_match_confidence = CASE WHEN ? != '' THEN ? ELSE vivino_match_confidence END,
                        drink_from_year = COALESCE(?, drink_from_year),
                        drink_until_year = COALESCE(?, drink_until_year),
                        drink_window_note = CASE WHEN ? != '' THEN ? ELSE drink_window_note END,
                        general_notes = ?,
                        personal_rating = COALESCE(?, personal_rating),
                        image_url = CASE WHEN ? != '' THEN ? ELSE image_url END,
                        is_imported = CASE WHEN ? = 1 THEN 1 ELSE is_imported END,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        new_qty,
                        storage_location.strip(),
                        storage_location.strip(),
                        purchase_location.strip(),
                        purchase_location.strip(),
                        purchase_price,
                        vivino_rating,
                        vivino_ratings_count,
                        vivino_url.strip(),
                        vivino_url.strip(),
                        vivino_price_estimate,
                        rating_source.strip(),
                        rating_source.strip(),
                        vivino_wine_id,
                        (vivino_match_confidence or "").strip(),
                        (vivino_match_confidence or "").strip(),
                        drink_from_year,
                        drink_until_year,
                        drink_window_note.strip(),
                        drink_window_note.strip(),
                        updated_general_notes,
                        personal_rating,
                        image_url.strip(),
                        image_url.strip(),
                        is_imported_int,
                        timestamp,
                        target_wine_id,
                    ),
                )

            if is_tasted_entry:
                tasting_note_text = batch_notes.strip() or general_notes.strip()
                conn.execute(
                    """
                    INSERT INTO consumption_history (
                        wine_id, batch_id, event_type, delta, timestamp,
                        purchase_location, storage_location, tasting_notes, personal_rating
                    ) VALUES (?, NULL, 'tasted', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target_wine_id,
                        -qty,
                        timestamp,
                        purchase_location.strip(),
                        storage_location.strip(),
                        tasting_note_text,
                        personal_rating,
                    ),
                )
            else:
                batch_cursor = conn.execute(
                    """
                    INSERT INTO inventory_additions (
                        wine_id, quantity_added, quantity_remaining, added_at,
                        purchase_location, purchase_price, storage_location,
                        drink_window_note, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target_wine_id,
                        qty,
                        qty,
                        timestamp,
                        purchase_location.strip(),
                        purchase_price,
                        storage_location.strip(),
                        drink_window_note.strip(),
                        batch_notes.strip(),
                    ),
                )
                batch_id = int(batch_cursor.lastrowid)

                conn.execute(
                    """
                    INSERT INTO consumption_history (
                        wine_id, batch_id, event_type, delta, timestamp,
                        purchase_location, storage_location, tasting_notes, personal_rating
                    ) VALUES (?, ?, 'added', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target_wine_id,
                        batch_id,
                        qty,
                        timestamp,
                        purchase_location.strip(),
                        storage_location.strip(),
                        batch_notes.strip(),
                        personal_rating,
                    ),
                )

            detail = self._get_wine_detail_with_conn(conn, target_wine_id)
            assert detail is not None
            return detail

    def adjust_quantity(
        self,
        *,
        wine_id: int,
        delta: int,
        batch_id: int | None = None,
        added_at: str | None = None,
        purchase_location: str = "",
        purchase_price: float | None = None,
        storage_location: str = "",
        drink_window_note: str = "",
        notes: str = "",
        personal_rating: float | None = None,
    ) -> dict[str, Any]:
        """Adjust bottle quantity (+N adds a batch; -N consumes from batch or FIFO)."""
        purchase_price = _opt_float(purchase_price)
        personal_rating = _opt_float(personal_rating)
        batch_id = _opt_int(batch_id)
        if delta == 0:
            detail = self.get_wine_detail(wine_id)
            if detail is None:
                raise ValueError(f"Wine ID {wine_id} not found")
            return detail

        if delta > 0:
            existing = self.get_wine_detail(wine_id)
            if existing is None:
                raise ValueError(f"Wine ID {wine_id} not found")
            return self.add_or_increment_wine(
                wine_id=wine_id,
                winery=existing["winery"],
                name=existing["name"],
                vintage=existing["vintage"],
                wine_type=existing["wine_type"],
                quantity=delta,
                added_at=added_at,
                purchase_location=purchase_location or existing.get("last_purchase_location", ""),
                purchase_price=purchase_price,
                storage_location=storage_location or existing.get("last_storage_location", ""),
                drink_window_note=drink_window_note,
                batch_notes=notes,
                personal_rating=personal_rating,
            )

        # Consuming (-N) bottles
        timestamp = (added_at or "").strip() or utc_now_iso()
        to_consume = abs(int(delta))

        with closing(self._connect()) as conn, conn:
            row = conn.execute("SELECT * FROM wines WHERE id = ?", (wine_id,)).fetchone()
            if row is None:
                raise ValueError(f"Wine ID {wine_id} not found")

            current_qty = int(row["quantity"])
            actual_consumed = min(current_qty, to_consume) if current_qty > 0 else 0
            new_qty = max(0, current_qty - to_consume)

            used_location = storage_location.strip()
            used_store = purchase_location.strip()
            resolved_batch_id = batch_id

            if batch_id is not None:
                b_row = conn.execute(
                    "SELECT * FROM inventory_additions WHERE id = ? AND wine_id = ?",
                    (batch_id, wine_id),
                ).fetchone()
                if b_row is not None:
                    rem = max(0, int(b_row["quantity_remaining"]) - to_consume)
                    conn.execute(
                        "UPDATE inventory_additions SET quantity_remaining = ? WHERE id = ?",
                        (rem, batch_id),
                    )
                    used_location = used_location or b_row["storage_location"]
                    used_store = used_store or b_row["purchase_location"]
            else:
                # FIFO across batches with remaining quantity
                remaining_to_deduct = actual_consumed
                fifo_batches = conn.execute(
                    """
                    SELECT * FROM inventory_additions
                    WHERE wine_id = ? AND quantity_remaining > 0
                    ORDER BY added_at ASC, id ASC
                    """,
                    (wine_id,),
                ).fetchall()
                for b_row in fifo_batches:
                    if remaining_to_deduct <= 0:
                        break
                    avail = int(b_row["quantity_remaining"])
                    take = min(avail, remaining_to_deduct)
                    conn.execute(
                        "UPDATE inventory_additions SET quantity_remaining = ? WHERE id = ?",
                        (avail - take, b_row["id"]),
                    )
                    if resolved_batch_id is None:
                        resolved_batch_id = int(b_row["id"])
                    if not used_location and b_row["storage_location"]:
                        used_location = b_row["storage_location"]
                    if not used_store and b_row["purchase_location"]:
                        used_store = b_row["purchase_location"]
                    remaining_to_deduct -= take

            conn.execute(
                """
                UPDATE wines
                SET quantity = ?,
                    personal_rating = COALESCE(?, personal_rating),
                    updated_at = ?
                WHERE id = ?
                """,
                (new_qty, personal_rating, timestamp, wine_id),
            )

            conn.execute(
                """
                INSERT INTO consumption_history (
                    wine_id, batch_id, event_type, delta, timestamp,
                    purchase_location, storage_location, tasting_notes, personal_rating
                ) VALUES (?, ?, 'consumed', ?, ?, ?, ?, ?, ?)
                """,
                (
                    wine_id,
                    resolved_batch_id,
                    -actual_consumed if actual_consumed > 0 else delta,
                    timestamp,
                    used_store,
                    used_location,
                    notes.strip(),
                    personal_rating,
                ),
            )

            detail = self._get_wine_detail_with_conn(conn, wine_id)
            assert detail is not None
            return detail

    def update_wine(
        self,
        wine_id: int,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """Update editable metadata on a wine record."""
        allowed_fields = {
            "winery",
            "name",
            "vintage",
            "wine_type",
            "varietal",
            "region",
            "country",
            "alcohol_pct",
            "bottle_size_ml",
            "vivino_rating",
            "vivino_ratings_count",
            "vivino_url",
            "vivino_price_estimate",
            "rating_source",
            "vivino_wine_id",
            "vivino_match_confidence",
            "drink_from_year",
            "drink_until_year",
            "drink_window_note",
            "food_pairings",
            "general_notes",
            "personal_rating",
            "image_url",
            "storage_location",
            "purchase_location",
            "purchase_price",
            "research_notes",
            "is_imported",
        }
        float_fields = {
            "alcohol_pct",
            "vivino_rating",
            "vivino_price_estimate",
            "personal_rating",
            "purchase_price",
        }
        int_fields = {
            "vintage",
            "bottle_size_ml",
            "vivino_ratings_count",
            "vivino_wine_id",
            "drink_from_year",
            "drink_until_year",
        }
        set_clauses: list[str] = []
        values: list[Any] = []

        for key, val in updates.items():
            if key not in allowed_fields:
                continue
            if key == "food_pairings":
                if isinstance(val, list):
                    val = json.dumps([str(x).strip() for x in val if str(x).strip()])
                elif isinstance(val, str):
                    val = json.dumps([x.strip() for x in val.split(",") if x.strip()])
            elif key in float_fields:
                val = _opt_float(val)
            elif key == "is_imported":
                val = 1 if val else 0
            elif key in int_fields:
                val = _opt_int(val)
                if key == "vintage" and val is not None and val <= 0:
                    val = None
                elif key == "bottle_size_ml" and val is None:
                    val = 750
            elif key == "vivino_url":
                val = _sanitize_vivino_url(val)
            elif key == "image_url":
                val = _sanitize_image_url(val)
            set_clauses.append(f"{key} = ?")
            values.append(val)

        if not set_clauses:
            detail = self.get_wine_detail(wine_id)
            if detail is None:
                raise ValueError(f"Wine ID {wine_id} not found")
            return detail

        set_clauses.append("updated_at = ?")
        values.append(utc_now_iso())
        values.append(wine_id)

        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                f"UPDATE wines SET {', '.join(set_clauses)} WHERE id = ?",
                values,
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Wine ID {wine_id} not found")

            if "storage_location" in updates:
                clean_loc = str(updates["storage_location"]).strip()
                conn.execute(
                    "UPDATE inventory_additions SET storage_location = ? WHERE wine_id = ? AND quantity_remaining > 0",
                    (clean_loc, wine_id),
                )

            if "purchase_location" in updates or "purchase_price" in updates:
                clean_store = (
                    str(updates["purchase_location"]).strip()
                    if "purchase_location" in updates
                    else None
                )
                price_val = None
                if "purchase_price" in updates:
                    raw_p = updates["purchase_price"]
                    try:
                        price_val = float(raw_p) if raw_p is not None and str(raw_p).strip() != "" else None
                    except (ValueError, TypeError):
                        price_val = None

                latest_batch = conn.execute(
                    "SELECT id FROM inventory_additions WHERE wine_id = ? ORDER BY added_at DESC, id DESC LIMIT 1",
                    (wine_id,),
                ).fetchone()
                if latest_batch:
                    if clean_store is not None and "purchase_price" in updates:
                        conn.execute(
                            "UPDATE inventory_additions SET purchase_location = ?, purchase_price = ? WHERE id = ?",
                            (clean_store, price_val, latest_batch["id"]),
                        )
                    elif clean_store is not None:
                        conn.execute(
                            "UPDATE inventory_additions SET purchase_location = ? WHERE id = ?",
                            (clean_store, latest_batch["id"]),
                        )
                    elif "purchase_price" in updates:
                        conn.execute(
                            "UPDATE inventory_additions SET purchase_price = ? WHERE id = ?",
                            (price_val, latest_batch["id"]),
                        )

            detail = self._get_wine_detail_with_conn(conn, wine_id)
            assert detail is not None
            return detail

    def unlink_vivino(self, wine_id: int) -> dict[str, Any]:
        """Remove Vivino link, ratings, and estimates from a wine record."""
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                """
                UPDATE wines
                SET vivino_rating = NULL,
                    vivino_ratings_count = NULL,
                    vivino_url = '',
                    vivino_price_estimate = NULL,
                    rating_source = '',
                    vivino_wine_id = NULL,
                    vivino_match_confidence = 'none',
                    updated_at = ?
                WHERE id = ?
                """,
                (utc_now_iso(), wine_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Wine ID {wine_id} not found")
            detail = self._get_wine_detail_with_conn(conn, wine_id)
            assert detail is not None
            return detail

    def delete_wine(self, wine_id: int) -> bool:
        """Delete a wine record and its cascading batches and history."""
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute("DELETE FROM wines WHERE id = ?", (wine_id,))
            return cursor.rowcount > 0

    def list_wines(
        self,
        include_out_of_stock: bool = True,
        wine_type: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """List all wines with batches and timeline metadata."""
        clauses: list[str] = []
        params: list[Any] = []

        if not include_out_of_stock:
            clauses.append("quantity > 0")
        if wine_type and wine_type.lower() in WINE_TYPES:
            clauses.append("LOWER(wine_type) = ?")
            params.append(wine_type.lower())
        if search and search.strip():
            q = f"%{search.strip().lower()}%"
            clauses.append(
                "("
                "LOWER(winery) LIKE ? OR "
                "LOWER(name) LIKE ? OR "
                "LOWER(varietal) LIKE ? OR "
                "LOWER(region) LIKE ? OR "
                "LOWER(country) LIKE ? OR "
                "LOWER(general_notes) LIKE ?"
                ")"
            )
            params.extend([q, q, q, q, q, q])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT * FROM wines {where_sql} ORDER BY updated_at DESC, id DESC",
                params,
            ).fetchall()
            if not rows:
                return []

            wine_ids = [int(r["id"]) for r in rows]
            batch_rows: list[sqlite3.Row] = []
            history_rows: list[sqlite3.Row] = []
            # Chunk IN (...) lists to stay well under SQLite's bound-parameter limit
            for start in range(0, len(wine_ids), 500):
                chunk = wine_ids[start : start + 500]
                placeholders = ",".join("?" for _ in chunk)
                batch_rows.extend(
                    conn.execute(
                        f"""
                        SELECT * FROM inventory_additions
                        WHERE wine_id IN ({placeholders})
                        ORDER BY added_at DESC, id DESC
                        """,
                        chunk,
                    ).fetchall()
                )
                history_rows.extend(
                    conn.execute(
                        f"""
                        SELECT * FROM consumption_history
                        WHERE wine_id IN ({placeholders})
                        ORDER BY timestamp DESC, id DESC
                        """,
                        chunk,
                    ).fetchall()
                )

            batches_by_wine: dict[int, list[dict[str, Any]]] = {wid: [] for wid in wine_ids}
            for b in batch_rows:
                batches_by_wine[int(b["wine_id"])].append(dict(b))

            history_by_wine: dict[int, list[dict[str, Any]]] = {wid: [] for wid in wine_ids}
            for h in history_rows:
                history_by_wine[int(h["wine_id"])].append(dict(h))

            return [
                self._serialize_wine_row(
                    r,
                    batches=batches_by_wine[int(r["id"])],
                    timeline=history_by_wine[int(r["id"])],
                )
                for r in rows
            ]

    def get_statistics(self) -> dict[str, Any]:
        """Compute aggregate cellar metrics for Home Assistant sensors and UI."""
        wines = self.list_wines(include_out_of_stock=True)
        in_stock = [w for w in wines if int(w["quantity"]) > 0]
        archived = [w for w in wines if int(w["quantity"]) == 0]
        tasted = [w for w in wines if w.get("is_tasted")]
        tasted_bottles = sum(int(w.get("tasted_bottles") or 0) for w in tasted)

        total_bottles = sum(int(w["quantity"]) for w in in_stock)
        unique_wines_in_stock = len(in_stock)

        by_type: dict[str, int] = {t: 0 for t in WINE_TYPES}
        ready_to_drink_bottles = 0
        drink_soon_bottles = 0
        hold_bottles = 0
        past_peak_bottles = 0
        estimated_value = 0.0

        for w in in_stock:
            qty = int(w["quantity"])
            wtype = (w.get("wine_type") or WINE_TYPE_RED).lower()
            if wtype in by_type:
                by_type[wtype] += qty
            else:
                by_type[WINE_TYPE_RED] += qty

            status = w.get("drink_status")
            if status in (DRINK_STATUS_READY, DRINK_STATUS_DRINK_SOON):
                ready_to_drink_bottles += qty
            if status == DRINK_STATUS_DRINK_SOON:
                drink_soon_bottles += qty
            elif status == DRINK_STATUS_HOLD:
                hold_bottles += qty
            elif status == DRINK_STATUS_PAST_PEAK:
                past_peak_bottles += qty

            unit_price = _opt_float(w.get("last_purchase_price"))
            if unit_price is None:
                unit_price = _opt_float(w.get("purchase_price"))
            if unit_price is None:
                unit_price = _opt_float(w.get("vivino_price_estimate"))
            if unit_price is not None:
                estimated_value += unit_price * qty

        purchase_locations = sorted(
            {
                b["purchase_location"]
                for w in wines
                for b in w.get("batches", [])
                if b.get("purchase_location")
            }
        )
        storage_locations = sorted(
            {
                b["storage_location"]
                for w in wines
                for b in w.get("batches", [])
                if b.get("storage_location")
            }
        )

        return {
            "total_bottles": total_bottles,
            "unique_wines": unique_wines_in_stock,
            "archived_wines": len(archived),
            "tasted_wines": len(tasted),
            "tasted_bottles": tasted_bottles,
            "ready_to_drink_bottles": ready_to_drink_bottles,
            "drink_soon_bottles": drink_soon_bottles,
            "hold_bottles": hold_bottles,
            "keep_bottles": hold_bottles,
            "past_peak_bottles": past_peak_bottles,
            "estimated_value": round(estimated_value, 2),
            "by_type": by_type,
            "known_purchase_locations": purchase_locations,
            "known_storage_locations": storage_locations,
        }
