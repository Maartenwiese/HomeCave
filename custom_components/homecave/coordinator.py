"""Coordinator for HomeCave database, AI scanner, SommAI pairing, and Vivino lookups."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import csv
import functools
import json
import logging
from pathlib import Path
import time
from typing import Any

from .const import (
    CONF_CELLAR_NAME,
    CONF_CLAUDE_API_KEY,
    CONF_CLAUDE_MODEL,
    CONF_CURRENCY,
    CONF_GEMINI_API_KEY,
    CONF_GEMINI_MODEL,
    CONF_HUMIDITY_ENTITY,
    CONF_LLM_PROVIDER,
    CONF_LOW_STOCK_THRESHOLD,
    CONF_TEMPERATURE_ENTITY,
    CONF_VIVINO_COUNTRY_CODE,
    DB_FILENAME,
    DEFAULT_CELLAR_NAME,
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_CURRENCY,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_LOW_STOCK_THRESHOLD,
    DEFAULT_VIVINO_COUNTRY_CODE,
    EVENT_BOTTLE_ADDED,
    EVENT_BOTTLE_CONSUMED,
    EVENT_DRINK_WINDOW_ALERT,
    EVENT_QUANTITY_ADJUSTED,
    EVENT_STOCK_LOW,
    EVENT_WINE_UPDATED,
    EXPORTS_SUBDIR,
    GEMINI_RATE_WINDOW_SECONDS,
    LLM_PROVIDER_CLAUDE,
    LLM_PROVIDER_GEMINI,
    MAX_GEMINI_CALLS_PER_MINUTE,
    STORAGE_DIR_NAME,
)
from .database import WineDatabase, utc_now_iso
from .scanner import (
    ClaudeScannerError,
    GeminiScannerError,
    LLMScannerError,
    async_enrich_vivino_bottle,
    async_judge_vivino_candidates,
    async_research_wine,
    async_research_wine_with_gemini,
    async_scan_bottle_label,
    async_sommelier_dish_pairing,
    grounded_search_fields,
    parse_image_data_uri,
    save_bottle_cover_image,
)
from .vivino import async_fetch_vivino_wine, async_lookup_vivino, build_wine_url

_LOGGER = logging.getLogger(__name__)


class WineInventoryCoordinator:
    """Manages SQLite persistence, Gemini scanning, Vivino enrichment, and HA state updates."""

    def __init__(
        self,
        hass: Any,
        entry_id: str,
        config: dict[str, Any],
        session_getter: Callable[[Any], Any] | None = None,
    ) -> None:
        """Initialize the Wine Inventory coordinator."""
        self.hass = hass
        self.entry_id = entry_id
        self.config = dict(config)
        self._session_getter = session_getter

        config_dir = Path(getattr(hass.config, "config_dir", "."))
        self.db_path = config_dir / STORAGE_DIR_NAME / DB_FILENAME
        self._migrate_legacy_storage_if_needed(config_dir)
        self.db = WineDatabase(self.db_path)

        self.statistics: dict[str, Any] = {}
        self.wines: list[dict[str, Any]] = []
        self._listeners: list[Callable[[], None]] = []
        self._event_callbacks: list[Callable[[str, dict[str, Any]], None]] = []
        self._gemini_call_timestamps: list[float] = []
        self._import_progress: dict[str, Any] = {
            "status": "idle",
            "percent": 0,
            "current": 0,
            "total": 0,
            "message": "",
            "wine_name": "",
        }

    def _migrate_legacy_storage_if_needed(self, config_dir: Path) -> None:
        """One-time seamless migration from legacy wine_inventory storage if homecave.db is absent."""
        import shutil

        try:
            if not self.db_path.exists():
                legacy_db = config_dir / "wine_inventory" / "wine_inventory.db"
                if legacy_db.is_file() and not legacy_db.is_symlink():
                    self.db_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(legacy_db, self.db_path)
            legacy_covers = config_dir / "www" / "wine_inventory" / "covers"
            new_covers = config_dir / "www" / "homecave" / "covers"
            if legacy_covers.is_dir():
                new_covers.mkdir(parents=True, exist_ok=True)
                for img in legacy_covers.iterdir():
                    if img.is_file() and not img.is_symlink():
                        dst = new_covers / img.name
                        if not dst.exists():
                            shutil.copy2(img, dst)
        except OSError as err:
            _LOGGER.debug("Legacy storage migration skipped: %s", err)

    def _check_gemini_rate_limit(self) -> None:
        """Enforce a per-coordinator sliding-window rate limit on LLM API calls."""
        now = time.monotonic()
        cutoff = now - GEMINI_RATE_WINDOW_SECONDS
        self._gemini_call_timestamps = [
            ts for ts in self._gemini_call_timestamps if ts > cutoff
        ]
        if len(self._gemini_call_timestamps) >= MAX_GEMINI_CALLS_PER_MINUTE:
            provider_label = "Claude" if self.llm_provider == LLM_PROVIDER_CLAUDE else "Gemini"
            raise GeminiScannerError(
                f"Rate limit exceeded: maximum {MAX_GEMINI_CALLS_PER_MINUTE} {provider_label} AI requests per minute. Please wait a moment and try again."
            )
        self._gemini_call_timestamps.append(now)

    @property
    def cellar_name(self) -> str:
        """Return configured cellar name."""
        return str(self.config.get(CONF_CELLAR_NAME) or DEFAULT_CELLAR_NAME)

    @property
    def currency(self) -> str:
        """Return configured currency code."""
        return str(self.config.get(CONF_CURRENCY) or DEFAULT_CURRENCY)

    @property
    def llm_provider(self) -> str:
        """Return configured LLM provider (gemini or claude)."""
        provider = str(self.config.get(CONF_LLM_PROVIDER) or DEFAULT_LLM_PROVIDER).lower().strip()
        return LLM_PROVIDER_CLAUDE if provider == LLM_PROVIDER_CLAUDE else LLM_PROVIDER_GEMINI

    @property
    def gemini_api_key(self) -> str:
        """Return configured Gemini API key."""
        return str(self.config.get(CONF_GEMINI_API_KEY) or "")

    @property
    def gemini_model(self) -> str:
        """Return configured Gemini model identifier."""
        return str(self.config.get(CONF_GEMINI_MODEL) or DEFAULT_GEMINI_MODEL)

    @property
    def claude_api_key(self) -> str:
        """Return configured Anthropic Claude API key."""
        return str(self.config.get(CONF_CLAUDE_API_KEY) or "")

    @property
    def claude_model(self) -> str:
        """Return configured Claude model identifier."""
        return str(self.config.get(CONF_CLAUDE_MODEL) or DEFAULT_CLAUDE_MODEL)

    @property
    def active_llm_api_key(self) -> str:
        """Return the API key for the currently configured provider."""
        return self.claude_api_key if self.llm_provider == LLM_PROVIDER_CLAUDE else self.gemini_api_key

    @property
    def active_llm_model(self) -> str:
        """Return the model identifier for the currently configured provider."""
        return self.claude_model if self.llm_provider == LLM_PROVIDER_CLAUDE else self.gemini_model

    @property
    def vivino_country_code(self) -> str:
        """Return configured Vivino country code."""
        return str(self.config.get(CONF_VIVINO_COUNTRY_CODE) or DEFAULT_VIVINO_COUNTRY_CODE)

    @property
    def low_stock_threshold(self) -> int:
        """Return configured low-stock bottle threshold."""
        return int(self.config.get(CONF_LOW_STOCK_THRESHOLD, DEFAULT_LOW_STOCK_THRESHOLD))

    @property
    def temperature_entity(self) -> str:
        """Return optional linked cellar temperature sensor entity_id."""
        return str(self.config.get(CONF_TEMPERATURE_ENTITY) or "")

    @property
    def humidity_entity(self) -> str:
        """Return optional linked cellar humidity sensor entity_id."""
        return str(self.config.get(CONF_HUMIDITY_ENTITY) or "")

    def update_config(self, new_config: dict[str, Any]) -> None:
        """Update runtime configuration from ConfigEntry data + options."""
        self.config = dict(new_config)
        self._notify_listeners()

    def async_add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        """Register an entity state update listener."""
        self._listeners.append(update_callback)

        def _remove() -> None:
            if update_callback in self._listeners:
                self._listeners.remove(update_callback)

        return _remove

    def async_add_event_listener(
        self, event_callback: Callable[[str, dict[str, Any]], None]
    ) -> Callable[[], None]:
        """Register a listener for native Home Assistant EventEntity triggers."""
        self._event_callbacks.append(event_callback)

        def _remove() -> None:
            if event_callback in self._event_callbacks:
                self._event_callbacks.remove(event_callback)

        return _remove

    def _notify_listeners(self) -> None:
        """Notify all attached sensor entities of updated inventory state."""
        for callback in list(self._listeners):
            callback()

    def _emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Emit an event to registered EventEntity instances."""
        for callback in list(self._event_callbacks):
            callback(event_type, data)

    def _get_http_session(self) -> Any:
        """Obtain the shared Home Assistant aiohttp ClientSession."""
        if self._session_getter is not None:
            return self._session_getter(self.hass)
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        return async_get_clientsession(self.hass)

    async def async_initialize(self) -> None:
        """Initialize the SQLite schema and load initial statistics."""
        await self.hass.async_add_executor_job(self.db.initialize)
        await self.async_refresh()

    async def async_refresh(self) -> None:
        """Reload wine list and aggregate statistics from SQLite."""
        self.wines = await self.hass.async_add_executor_job(self.db.list_wines)
        self.statistics = await self.hass.async_add_executor_job(self.db.get_statistics)
        self._notify_listeners()

    async def async_checkpoint_database(self) -> None:
        """Flush SQLite WAL checkpoint for safe Home Assistant backups."""
        await self.hass.async_add_executor_job(self.db.checkpoint_wal)

    async def _async_resolve_camera_image(
        self, camera_entity_id: str
    ) -> tuple[str, bytes]:
        """Grab a still frame from a Home Assistant camera.* or image.* entity."""
        domain = camera_entity_id.split(".", 1)[0].lower()
        if domain == "camera":
            from homeassistant.components.camera import async_get_image

            image = await async_get_image(self.hass, camera_entity_id)
            return image.content_type or "image/jpeg", image.content
        if domain == "image":
            from homeassistant.components.image import async_get_image as async_get_image_entity

            image = await async_get_image_entity(self.hass, camera_entity_id)
            return image.content_type or "image/jpeg", image.content
        raise ValueError(
            f"Unsupported entity '{camera_entity_id}'. Expected a camera.* or image.* entity."
        )

    async def async_scan_bottle(
        self,
        *,
        image_data: str | None = None,
        camera_entity_id: str | None = None,
    ) -> dict[str, Any]:
        """Scan a bottle image with Gemini Flash-Lite, enrich via Vivino, and check duplicates."""
        self._check_gemini_rate_limit()
        if image_data:
            mime_type, raw_bytes = parse_image_data_uri(image_data)
        elif camera_entity_id:
            mime_type, raw_bytes = await self._async_resolve_camera_image(camera_entity_id)
        else:
            raise ValueError("Either 'image_data' or 'camera_entity_id' must be provided.")

        config_dir = Path(getattr(self.hass.config, "config_dir", "."))
        saved_image_url = await self.hass.async_add_executor_job(
            save_bottle_cover_image,
            config_dir,
            raw_bytes,
            mime_type,
        )

        session = self._get_http_session()
        try:
            extracted = await async_scan_bottle_label(
                session,
                api_key=self.active_llm_api_key,
                image_bytes=raw_bytes,
                mime_type=mime_type,
                model=self.active_llm_model,
                provider=self.llm_provider,
            )
        except Exception:
            if saved_image_url:
                try:
                    cover_filename = Path(saved_image_url).name
                    cover_path = config_dir / "www" / "homecave" / "covers" / cover_filename
                    if cover_path.exists():
                        cover_path.unlink()
                except Exception:
                    pass
            raise

        extracted["image_url"] = saved_image_url

        match = await self.async_match_vivino(
            session,
            extracted=extracted,
            image_bytes=raw_bytes,
            mime_type=mime_type,
        )
        extracted.update(match["fields"])

        existing_match = await self.hass.async_add_executor_job(
            self.db.find_matching_wine,
            extracted.get("winery", ""),
            extracted.get("name", ""),
            extracted.get("vintage"),
        )

        return {
            "extracted": extracted,
            "vivino_status": match["status"],
            "vivino_confidence": match["confidence"],
            "vivino_selected_index": match["selected_index"],
            "vivino_judge_reason": match["judge_reason"],
            "vivino_candidates": match["candidates"],
            "existing_wine": existing_match,
            "suggested_added_at": utc_now_iso(),
        }

    async def async_match_vivino(
        self,
        session: Any,
        *,
        extracted: dict[str, Any],
        image_bytes: bytes | None,
        mime_type: str,
    ) -> dict[str, Any]:
        """Grounded Vivino matching: retrieve -> strict pre-filter -> constrained judge -> tier.

        Tiers:
        - ``high``: exactly one pre-filter-exact candidate AND the Gemini judge picked it.
          Fields are auto-filled.
        - ``medium``: candidates exist but certainty is not high. Nothing is auto-filled;
          the user must confirm (default selection in the UI is "Do not link").
        - ``none``: no verified candidate. The wine stays unlinked.
        A rating is never estimated or invented.
        """
        grounded = grounded_search_fields(extracted)
        empty_fields = {
            "vivino_rating": None,
            "vivino_ratings_count": None,
            "vivino_url": "",
            "vivino_price_estimate": None,
            "rating_source": "",
            "vivino_wine_id": None,
            "vivino_match_confidence": "none",
        }
        result: dict[str, Any] = {
            "status": "empty_query",
            "confidence": "none",
            "selected_index": None,
            "judge_reason": "",
            "candidates": [],
            "fields": dict(empty_fields),
            "grounded_query": grounded,
        }
        if not grounded["winery"] and not grounded["name"]:
            result["judge_reason"] = "No producer or cuvée text could be verified as printed on the label."
            return result

        lookup = await async_lookup_vivino(
            session,
            winery=grounded["winery"],
            name=grounded["name"],
            vintage=grounded["vintage"],
            country_code=self.vivino_country_code,
            currency_code=self.currency,
        )
        candidates = lookup.get("candidates") or []
        result["status"] = lookup.get("status", "no_match")
        result["candidates"] = candidates
        if not candidates:
            return result

        judge = await async_judge_vivino_candidates(
            session,
            api_key=self.active_llm_api_key,
            image_bytes=image_bytes,
            mime_type=mime_type,
            label_text_verbatim=str(extracted.get("label_text_verbatim") or ""),
            candidates=candidates,
            model=self.active_llm_model,
            provider=self.llm_provider,
        )
        result["judge_reason"] = judge.get("reason", "")
        choice = judge.get("choice")
        exact_indices = [i for i, c in enumerate(candidates) if c.get("prefilter_exact")]

        if judge.get("status") == "ok" and choice is None:
            # Judge looked at real records and rejected all of them.
            result["confidence"] = "none"
            return result

        if (
            judge.get("status") == "ok"
            and choice is not None
            and grounded["winery"]
            and grounded["name"]
            and len(exact_indices) == 1
            and exact_indices[0] == choice
        ):
            chosen = candidates[choice]
            result["confidence"] = "high"
            result["selected_index"] = choice
            result["fields"] = {
                "vivino_rating": chosen.get("vivino_rating"),
                "vivino_ratings_count": chosen.get("vivino_ratings_count"),
                "vivino_url": chosen.get("vivino_url", ""),
                "vivino_price_estimate": chosen.get("vivino_price_estimate"),
                "rating_source": "vivino",
                "vivino_wine_id": chosen.get("vivino_wine_id"),
                "vivino_match_confidence": "high",
            }
            return result

        # Medium: show candidates for user confirmation; suggest the judge's pick if any.
        result["confidence"] = "medium"
        result["selected_index"] = choice
        return result

    async def async_link_vivino_url(self, wine_id: int, url: str) -> dict[str, Any]:
        """Link a wine to an exact Vivino page pasted by the user (no matching involved)."""
        wine = await self.hass.async_add_executor_job(self.db.get_wine_detail, int(wine_id))
        if not wine:
            raise ValueError(f"Wine ID {wine_id} not found")
        page = await async_fetch_vivino_wine(
            self._get_http_session(), url_or_id=url, vintage=wine.get("vintage")
        )
        updates = {
            "vivino_rating": page.get("vivino_rating"),
            "vivino_ratings_count": page.get("vivino_ratings_count"),
            "vivino_url": page.get("vivino_url", ""),
            "vivino_price_estimate": page.get("vivino_price_estimate"),
            "rating_source": "vivino",
            "vivino_wine_id": page.get("vivino_wine_id"),
            "vivino_match_confidence": "confirmed",
        }
        updated = await self.hass.async_add_executor_job(self.db.update_wine, int(wine_id), updates)
        await self.async_refresh()
        return {"wine": updated, "vivino": page}

    async def async_add_wine(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Add a wine or increment an existing wine with batch metadata (or record as tasted)."""
        raw_qty = payload.get("quantity")
        as_tasted = bool(payload.get("as_tasted", False)) or (
            raw_qty is not None and str(raw_qty).strip() == "0"
        )
        qty = 0 if as_tasted else int(raw_qty if raw_qty not in (None, "") else 1)
        wine = await self.hass.async_add_executor_job(
            lambda: self.db.add_or_increment_wine(
                wine_id=payload.get("wine_id"),
                winery=str(payload.get("winery") or "Unknown Winery"),
                name=str(payload.get("name") or "Unnamed Wine"),
                vintage=payload.get("vintage"),
                wine_type=str(payload.get("wine_type") or "red"),
                varietal=str(payload.get("varietal") or ""),
                region=str(payload.get("region") or ""),
                country=str(payload.get("country") or ""),
                alcohol_pct=payload.get("alcohol_pct"),
                bottle_size_ml=int(payload.get("bottle_size_ml") or 750),
                vivino_rating=payload.get("vivino_rating"),
                vivino_ratings_count=payload.get("vivino_ratings_count"),
                vivino_url=str(payload.get("vivino_url") or ""),
                vivino_price_estimate=payload.get("vivino_price_estimate"),
                rating_source=str(payload.get("rating_source") or ""),
                vivino_wine_id=payload.get("vivino_wine_id"),
                vivino_match_confidence=str(payload.get("vivino_match_confidence") or ""),
                drink_from_year=payload.get("drink_from_year"),
                drink_until_year=payload.get("drink_until_year"),
                drink_window_note=str(payload.get("drink_window_note") or ""),
                food_pairings=payload.get("food_pairings"),
                general_notes=str(payload.get("general_notes") or ""),
                personal_rating=payload.get("personal_rating"),
                image_url=str(payload.get("image_url") or ""),
                quantity=qty,
                added_at=payload.get("added_at"),
                purchase_location=str(payload.get("purchase_location") or ""),
                purchase_price=payload.get("purchase_price"),
                storage_location=str(payload.get("storage_location") or ""),
                batch_notes=str(payload.get("batch_notes") or payload.get("notes") or ""),
                as_tasted=as_tasted,
            )
        )
        await self.async_refresh()
        self._emit_event(
            EVENT_BOTTLE_ADDED,
            {
                "wine_id": wine["id"],
                "winery": wine["winery"],
                "name": wine["name"],
                "vintage": wine["vintage"],
                "quantity_added": qty,
                "as_tasted": as_tasted,
                "total_quantity": wine["quantity"],
                "purchase_location": payload.get("purchase_location") or "",
                "storage_location": payload.get("storage_location") or "",
            },
        )
        if wine.get("drink_status") == "drink_soon":
            self._emit_event(
                EVENT_DRINK_WINDOW_ALERT,
                {
                    "wine_id": wine["id"],
                    "winery": wine["winery"],
                    "name": wine["name"],
                    "drink_until_year": wine.get("drink_until_year"),
                },
            )
        return wine

    async def async_adjust_quantity(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Adjust bottle quantity (+N or -N) and emit corresponding events."""
        wine_id = int(payload["wine_id"])
        delta = int(payload["delta"])
        wine = await self.hass.async_add_executor_job(
            lambda: self.db.adjust_quantity(
                wine_id=wine_id,
                delta=delta,
                batch_id=payload.get("batch_id"),
                added_at=payload.get("added_at"),
                purchase_location=str(payload.get("purchase_location") or ""),
                purchase_price=payload.get("purchase_price"),
                storage_location=str(payload.get("storage_location") or ""),
                drink_window_note=str(payload.get("drink_window_note") or ""),
                notes=str(payload.get("notes") or ""),
                personal_rating=payload.get("personal_rating"),
            )
        )
        await self.async_refresh()

        event_type = (
            EVENT_BOTTLE_ADDED
            if delta > 0
            else EVENT_BOTTLE_CONSUMED
            if delta < 0
            else EVENT_QUANTITY_ADJUSTED
        )
        self._emit_event(
            event_type,
            {
                "wine_id": wine["id"],
                "winery": wine["winery"],
                "name": wine["name"],
                "vintage": wine["vintage"],
                "delta": delta,
                "total_quantity": wine["quantity"],
            },
        )
        if delta < 0 and 0 < int(wine["quantity"]) <= self.low_stock_threshold:
            self._emit_event(
                EVENT_STOCK_LOW,
                {
                    "wine_id": wine["id"],
                    "winery": wine["winery"],
                    "name": wine["name"],
                    "remaining_quantity": wine["quantity"],
                },
            )
        return wine

    async def async_update_wine(
        self,
        wine_id: int,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """Update wine metadata and refresh state."""
        updates_copy = dict(updates)
        if "image_data" in updates_copy and updates_copy["image_data"]:
            mime_type, raw_bytes = parse_image_data_uri(updates_copy.pop("image_data"))
            config_dir = Path(getattr(self.hass.config, "config_dir", "."))
            saved_image_url = await self.hass.async_add_executor_job(
                save_bottle_cover_image,
                config_dir,
                raw_bytes,
                mime_type,
            )
            updates_copy["image_url"] = saved_image_url

        wine = await self.hass.async_add_executor_job(
            self.db.update_wine,
            int(wine_id),
            updates_copy,
        )
        await self.async_refresh()
        self._emit_event(
            EVENT_WINE_UPDATED,
            {
                "wine_id": wine["id"],
                "winery": wine["winery"],
                "name": wine["name"],
            },
        )
        return wine

    async def async_delete_wine(self, wine_id: int) -> bool:
        """Delete a wine from the database and refresh state."""
        deleted = await self.hass.async_add_executor_job(
            self.db.delete_wine,
            int(wine_id),
        )
        await self.async_refresh()
        return deleted

    async def async_unlink_vivino(self, wine_id: int) -> dict[str, Any]:
        """Remove Vivino link, ratings, and estimates from a wine record."""
        wine = await self.hass.async_add_executor_job(
            self.db.unlink_vivino,
            int(wine_id),
        )
        await self.async_refresh()
        self._emit_event(
            EVENT_WINE_UPDATED,
            {
                "wine_id": wine["id"],
                "winery": wine["winery"],
                "name": wine["name"],
            },
        )
        return wine

    async def async_research_wine(self, wine_id: int) -> dict[str, Any]:
        """Perform deep Gemini AI research on producer history and wine details."""
        self._check_gemini_rate_limit()
        wine = await self.hass.async_add_executor_job(
            self.db.get_wine_detail, int(wine_id)
        )
        if not wine:
            raise ValueError(f"Wine ID {wine_id} not found")

        session = self._get_http_session()
        result = await async_research_wine(
            session,
            api_key=self.active_llm_api_key,
            wine_data=wine,
            model=self.active_llm_model,
            provider=self.llm_provider,
        )
        research_data = result.get("research") or {}
        research_json = json.dumps(research_data)
        updated_wine = await self.hass.async_add_executor_job(
            self.db.update_wine,
            int(wine_id),
            {"research_notes": research_json},
        )
        await self.async_refresh()
        return {
            "status": "ok",
            "wine": updated_wine,
            "research": research_data,
            "model_used": result.get("model_used"),
        }

    async def async_refresh_vivino(self, wine_id: int | None = None) -> dict[str, Any]:
        """Re-fetch Vivino ratings for verified links only (high / user-confirmed).

        Lookups go straight to the linked Vivino wine id; no re-searching, no estimation.
        Wines without a verified link are skipped.
        """
        session = self._get_http_session()
        if wine_id is not None:
            detail = await self.hass.async_add_executor_job(
                self.db.get_wine_detail, int(wine_id)
            )
            targets = [detail] if detail else []
        else:
            targets = [w for w in self.wines if int(w.get("quantity", 0)) > 0]

        updated_count = 0
        skipped_unverified = 0
        errors: list[str] = []
        for item in targets:
            if not item:
                continue
            if item.get("vivino_match_confidence") not in ("high", "confirmed") or not item.get("vivino_wine_id"):
                skipped_unverified += 1
                continue
            try:
                page = await async_fetch_vivino_wine(
                    session,
                    url_or_id=item["vivino_wine_id"],
                    vintage=item.get("vintage"),
                )
            except Exception as err:  # noqa: BLE001
                errors.append(f"{item.get('winery')} {item.get('name')}: {err}")
                continue
            updates: dict[str, Any] = {
                "vivino_rating": page.get("vivino_rating"),
                "vivino_ratings_count": page.get("vivino_ratings_count"),
                "vivino_url": build_wine_url(item["vivino_wine_id"], item.get("vintage")),
                "vivino_price_estimate": page.get("vivino_price_estimate"),
                "rating_source": "vivino",
            }
            await self.hass.async_add_executor_job(
                self.db.update_wine,
                int(item["id"]),
                updates,
            )
            updated_count += 1

        await self.async_refresh()
        return {
            "updated_count": updated_count,
            "checked_count": len(targets),
            "skipped_unverified": skipped_unverified,
            "errors": errors,
        }

    async def async_export_inventory(self, export_format: str = "csv") -> dict[str, Any]:
        """Export full HomeCave inventory to <config>/homecave/exports/."""
        config_dir = Path(getattr(self.hass.config, "config_dir", "."))
        exports_dir = config_dir / EXPORTS_SUBDIR
        fmt = export_format.lower().strip()
        wines = await self.hass.async_add_executor_job(self.db.list_wines)

        def _write_export() -> tuple[str, str]:
            exports_dir.mkdir(parents=True, exist_ok=True)
            if fmt == "json":
                filename = "homecave_export.json"
                target = exports_dir / filename
                target.write_text(json.dumps(wines, indent=2), encoding="utf-8")
            else:
                filename = "homecave_export.csv"
                target = exports_dir / filename
                fieldnames = [
                    "id",
                    "winery",
                    "name",
                    "vintage",
                    "wine_type",
                    "varietal",
                    "region",
                    "country",
                    "quantity",
                    "vivino_rating",
                    "vivino_ratings_count",
                    "personal_rating",
                    "drink_from_year",
                    "drink_until_year",
                    "drink_status",
                    "last_purchase_location",
                    "last_storage_location",
                    "last_purchase_price",
                    "general_notes",
                    "vivino_url",
                ]
                with target.open("w", newline="", encoding="utf-8") as csvfile:
                    writer = csv.DictWriter(
                        csvfile, fieldnames=fieldnames, extrasaction="ignore"
                    )
                    writer.writeheader()
                    for w in wines:
                        writer.writerow(w)
            return str(target), f"/api/homecave/download/export/{filename}"

        file_path, download_url = await self.hass.async_add_executor_job(_write_export)
        return {
            "file_path": file_path,
            "download_url": download_url,
            "exported_wines": len(wines),
        }

    async def async_create_backup(self) -> dict[str, Any]:
        """Create a full .zip backup of SQLite db, metadata.json, and cover images."""
        from .backup import create_backup_archive

        config_dir = Path(getattr(self.hass.config, "config_dir", "."))
        res = await self.hass.async_add_executor_job(
            create_backup_archive,
            config_dir,
            self.db,
        )
        filename = res.get("filename")
        if filename:
            try:
                res["download_url"] = await self.async_stage_backup_for_download(filename)
            except Exception:
                pass
        return res

    async def async_restore_backup(self, zip_source: Path | bytes | str) -> dict[str, Any]:
        """Restore database and cover images from a .zip backup archive."""
        from .backup import restore_backup_archive

        config_dir = Path(getattr(self.hass.config, "config_dir", "."))
        res = await self.hass.async_add_executor_job(
            restore_backup_archive,
            config_dir,
            self.db,
            zip_source,
        )
        await self.async_refresh()
        return res

    async def async_import_wines(
        self,
        payload: list[dict[str, Any]] | dict[str, Any] | bytes | str,
        format_type: str = "auto",
    ) -> dict[str, Any]:
        """Import wines from JSON list, CSV, or ZIP archive."""
        from .backup import import_wines_archive

        config_dir = Path(getattr(self.hass.config, "config_dir", "."))
        res = await self.hass.async_add_executor_job(
            import_wines_archive,
            config_dir,
            self.db,
            payload,
            format_type,
        )
        await self.async_refresh()
        return res

    async def async_stage_backup_for_download(self, filename: str) -> str:
        """Stage a backup archive for public HTTP download under /local/homecave/backups/."""
        from .backup import stage_backup_for_download

        config_dir = Path(getattr(self.hass.config, "config_dir", "."))
        return await self.hass.async_add_executor_job(
            stage_backup_for_download,
            config_dir,
            filename,
        )

    def get_import_progress(self) -> dict[str, Any]:
        """Return the current import and enrichment progress."""
        res = dict(self._import_progress)
        status = str(self._import_progress.get("status", "")).lower()
        res["running"] = status in ("running", "importing", "enriching", "parsing", "downloading")
        res["progress_pct"] = self._import_progress.get("percent", 0)
        res["percent"] = self._import_progress.get("percent", 0)
        res["status_message"] = self._import_progress.get("message", "")
        res["message"] = self._import_progress.get("message", "")
        res["stage"] = status or "running"
        res["total_bottles"] = self._import_progress.get("total", 0)
        res["current_bottle"] = self._import_progress.get("current", 0)
        return res

    async def async_sommelier_chat(
        self,
        *,
        query_text: str | None = None,
        image_data: str | None = None,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Ask SommAI for food and wine pairing advice grounded in active cellar inventory."""
        self._check_gemini_rate_limit()

        image_bytes: bytes | None = None
        mime_type: str | None = None
        if image_data:
            mime_type, image_bytes = parse_image_data_uri(image_data)

        cellar_wines = await self.hass.async_add_executor_job(
            functools.partial(self.db.list_wines, include_out_of_stock=False)
        )

        session = self._get_http_session()
        result = await async_sommelier_dish_pairing(
            session,
            api_key=self.active_llm_api_key,
            cellar_wines=cellar_wines,
            dish_description=query_text,
            image_bytes=image_bytes,
            mime_type=mime_type,
            conversation_history=conversation_history,
            model=self.active_llm_model,
            provider=self.llm_provider,
        )
        pairing = result.get("pairing") or {}
        return {
            "status": "ok",
            "pairing": pairing,
            **pairing,
            "model_used": result.get("model_used"),
        }

    async def async_enrich_bottle(
        self,
        wine_id: int,
        refresh: bool = True,
        bypass_rate_limit: bool = False,
    ) -> dict[str, Any]:
        """Call Gemini to enrich missing cataloguing and sommelier details for a wine."""
        if not bypass_rate_limit:
            self._check_gemini_rate_limit()
        wine = await self.hass.async_add_executor_job(
            self.db.get_wine_detail, int(wine_id)
        )
        if not wine:
            raise ValueError(f"Wine ID {wine_id} not found")

        session = self._get_http_session()
        result = await async_enrich_vivino_bottle(
            session,
            api_key=self.active_llm_api_key,
            wine_data=wine,
            model=self.active_llm_model,
            provider=self.llm_provider,
        )
        enr = result.get("enrichment") or {}
        updates: dict[str, Any] = {}
        if enr.get("varietal") and (not wine.get("varietal") or wine.get("varietal") in ("unspecified", "traditional varieties")):
            updates["varietal"] = enr["varietal"]
        if enr.get("wine_type") and not wine.get("wine_type"):
            updates["wine_type"] = enr["wine_type"]
        if enr.get("alcohol_pct") and wine.get("alcohol_pct") is None:
            updates["alcohol_pct"] = enr["alcohol_pct"]
        if enr.get("drink_from_year") and wine.get("drink_from_year") is None:
            updates["drink_from_year"] = enr["drink_from_year"]
        if enr.get("drink_until_year") and wine.get("drink_until_year") is None:
            updates["drink_until_year"] = enr["drink_until_year"]
        if enr.get("drink_window_note") and not wine.get("drink_window_note"):
            updates["drink_window_note"] = enr["drink_window_note"]

        research_payload = {
            "summary": enr.get("summary", ""),
            "producer_history": enr.get("producer_history", ""),
            "tasting_profile": enr.get("tasting_profile", ""),
            "taste_profile": enr.get("taste_profile", {}),
            "serving_and_pairing": ", ".join(enr.get("food_pairings", [])),
            "food_pairings": enr.get("food_pairings", []),
            "model_used": result.get("model_used"),
        }
        updates["research_notes"] = json.dumps(research_payload)
        updated_wine = await self.hass.async_add_executor_job(
            self.db.update_wine, int(wine_id), updates
        )
        if refresh:
            await self.async_refresh()
        return {
            "status": "ok",
            "wine": updated_wine,
            "enrichment": enr,
            "model_used": result.get("model_used"),
        }

    async def async_import_vivino_csv(
        self,
        csv_text: str,
        enrich_ai: bool = True,
    ) -> dict[str, Any]:
        """Import wines and tasted history from a Vivino CSV export with optional LLM enrichment."""
        from .backup import _import_wines_from_csv, import_vivino_history_csv

        config_dir = Path(getattr(self.hass.config, "config_dir", "."))
        self._import_progress = {
            "status": "running",
            "percent": 10,
            "current": 0,
            "total": 0,
            "message": "Parsing Vivino CSV records...",
            "wine_name": "",
        }

        first_line = csv_text.splitlines()[0].lower() if csv_text.strip() else ""
        if "label image" in first_line or "user cellar count" in first_line:
            res_count = await self.hass.async_add_executor_job(
                _import_wines_from_csv,
                self.db,
                csv_text,
                config_dir,
            )
            res = {
                "success": True,
                "imported_count": res_count,
                "imported_wines": res_count,
            }
        else:
            res = await self.hass.async_add_executor_job(
                import_vivino_history_csv,
                self.db,
                csv_text,
            )

        enriched_count = 0
        if enrich_ai and self.active_llm_api_key:
            all_wines = await self.hass.async_add_executor_job(
                functools.partial(self.db.list_wines, include_out_of_stock=True)
            )
            targets = [w for w in all_wines if not w.get("research_notes")]
            total_targets = len(targets)
            if total_targets > 0:
                self._import_progress = {
                    "status": "enriching",
                    "percent": 50,
                    "current": 0,
                    "total": total_targets,
                    "message": f"Starting Sommelier AI enrichment on {total_targets} bottles...",
                    "wine_name": "",
                }
                for idx, w in enumerate(targets, 1):
                    wine_name = f"{w.get('winery', '')} {w.get('name', '')}".strip()
                    pct = 50 + int((idx / total_targets) * 48)
                    self._import_progress = {
                        "status": "enriching",
                        "percent": pct,
                        "current": idx,
                        "total": total_targets,
                        "wine_name": wine_name,
                        "message": f"Enriching bottle {idx}/{total_targets} ({wine_name})...",
                    }
                    try:
                        await self.async_enrich_bottle(
                            int(w["id"]), refresh=False, bypass_rate_limit=True
                        )
                        enriched_count += 1
                        await asyncio.sleep(0.3)
                    except Exception as err:
                        _LOGGER.warning("Enrichment skipped for %s: %s", wine_name, err)

        res["enriched_count"] = enriched_count
        self._import_progress = {
            "status": "completed",
            "percent": 100,
            "current": res.get("imported_count", 0),
            "total": res.get("imported_count", 0),
            "wine_name": "",
            "message": f"Import complete! {res.get('imported_count', 0)} wines processed, {enriched_count} enriched with AI.",
            "result": res,
        }
        await self.async_refresh()
        return res

    async def async_import_vivino_zip(
        self,
        zip_source: Path | bytes | str,
        download_images: bool = True,
        enrich_ai: bool = True,
    ) -> dict[str, Any]:
        """Import wines, cellar inventory, and cover images from a Vivino ZIP export archive with LLM enrichment."""
        from .backup import import_vivino_export_zip

        config_dir = Path(getattr(self.hass.config, "config_dir", "."))
        self._import_progress = {
            "status": "running",
            "percent": 5,
            "current": 0,
            "total": 0,
            "message": "Extracting Vivino export archive...",
            "wine_name": "",
        }

        def _on_progress(p: dict[str, Any]) -> None:
            self._import_progress.update(p)

        res = await self.hass.async_add_executor_job(
            import_vivino_export_zip,
            config_dir,
            self.db,
            zip_source,
            download_images,
            _on_progress,
        )

        enriched_count = 0
        if enrich_ai and self.active_llm_api_key:
            # Target in-stock cellar wines first, then wines missing research
            all_wines = await self.hass.async_add_executor_job(
                functools.partial(self.db.list_wines, include_out_of_stock=True)
            )
            cellar_targets = [
                w for w in all_wines
                if (w.get("quantity") or 0) > 0 and (not w.get("research_notes") or not w.get("varietal"))
            ]
            tasted_targets = [
                w for w in all_wines
                if (w.get("quantity") or 0) <= 0 and (not w.get("research_notes") or not w.get("varietal"))
            ][:50]  # Cap tasted wines batch to preserve quota
            targets = cellar_targets + tasted_targets
            total_targets = len(targets)

            if total_targets > 0:
                self._import_progress = {
                    "status": "enriching",
                    "percent": 50,
                    "current": 0,
                    "total": total_targets,
                    "message": f"Starting Sommelier AI enrichment on {total_targets} bottles...",
                    "wine_name": "",
                }
                for idx, w in enumerate(targets, 1):
                    wine_name = f"{w.get('winery', '')} {w.get('name', '')} {w.get('vintage') or ''}".strip()
                    pct = 50 + int((idx / total_targets) * 48)
                    self._import_progress = {
                        "status": "enriching",
                        "percent": pct,
                        "current": idx,
                        "total": total_targets,
                        "wine_name": wine_name,
                        "message": f"Enriching bottle {idx}/{total_targets} with Sommelier AI ({wine_name})...",
                    }
                    try:
                        await self.async_enrich_bottle(
                            int(w["id"]), refresh=False, bypass_rate_limit=True
                        )
                        enriched_count += 1
                        await asyncio.sleep(0.3)
                    except Exception as err:
                        _LOGGER.warning("Enrichment skipped for bottle %s: %s", wine_name, err)

        res["enriched_count"] = enriched_count
        self._import_progress = {
            "status": "completed",
            "percent": 100,
            "current": res.get("imported_count", 0),
            "total": res.get("imported_count", 0),
            "wine_name": "",
            "message": f"Import complete! {res.get('cellar_imported', 0)} cellar ({res.get('cellar_bottles', 0)} bottles), {res.get('tasted_imported', 0)} tasted, {enriched_count} enriched with AI.",
            "result": res,
        }
        await self.async_refresh()
        return res

    async def async_list_backups(self) -> list[dict[str, Any]]:
        """List available backups on disk."""
        from .backup import list_available_backups

        config_dir = Path(getattr(self.hass.config, "config_dir", "."))
        return await self.hass.async_add_executor_job(
            list_available_backups,
            config_dir,
        )
