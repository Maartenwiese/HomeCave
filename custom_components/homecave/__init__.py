"""The HomeCave - your local Wine Cellar integration for Home Assistant."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

try:
    import voluptuous as vol
    from homeassistant.components.http import StaticPathConfig
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.const import Platform
    from homeassistant.core import (
        HomeAssistant,
        ServiceCall,
        ServiceResponse,
        SupportsResponse,
    )
    from .websocket_api import _get_coordinator, async_register_websocket_commands

    PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.EVENT]
except ImportError:  # pragma: no cover - allows importing package in standalone unit tests
    vol = None  # type: ignore[assignment]
    StaticPathConfig = None  # type: ignore[assignment]
    ConfigEntry = Any  # type: ignore[assignment,misc]
    HomeAssistant = Any  # type: ignore[assignment,misc]
    ServiceCall = Any  # type: ignore[assignment,misc]
    ServiceResponse = Any  # type: ignore[assignment,misc]
    SupportsResponse = None  # type: ignore[assignment]
    PLATFORMS = ["sensor", "event"]  # type: ignore[assignment]

from .const import (
    CARD_JS_FILENAME,
    CARD_RESOURCE_URL,
    DOMAIN,
    FRONTEND_URL_BASE,
    MOBILE_APP_URL_BASE,
    SERVICE_ADD_WINE,
    SERVICE_ADJUST_QUANTITY,
    SERVICE_CREATE_BACKUP,
    SERVICE_EXPORT_INVENTORY,
    SERVICE_IMPORT_WINES,
    SERVICE_REFRESH_VIVINO,
    SERVICE_RESTORE_BACKUP,
    SERVICE_SCAN_BOTTLE,
    SERVICE_UPDATE_WINE,
)
from .coordinator import WineInventoryCoordinator
from .http_api import async_register_http_views

_LOGGER = logging.getLogger(__name__)


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Serve homecave-card.js and standalone mobile PWA (/homecave) via hass.http."""
    domain_meta = hass.data.setdefault(DOMAIN, {})
    frontend_dir = Path(__file__).parent / "frontend"
    card_js_path = frontend_dir / CARD_JS_FILENAME
    mobile_dir = frontend_dir / "mobile"

    if not domain_meta.get("_frontend_registered"):
        if hasattr(hass, "http") and hass.http is not None:
            try:
                await hass.http.async_register_static_paths(
                    [
                        StaticPathConfig(
                            f"{FRONTEND_URL_BASE}/{CARD_JS_FILENAME}",
                            str(card_js_path),
                            cache_headers=True,
                        )
                    ]
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Static path already registered or failed: %s", err)
            try:
                async_register_http_views(hass)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("HTTP views already registered or failed: %s", err)
        domain_meta["_frontend_registered"] = True

    # Auto-register or update version in Lovelace storage resources if available
    try:
        lovelace = hass.data.get("lovelace")
        resources = getattr(lovelace, "resources", None)
        if resources is not None and getattr(resources, "loaded", False):
            existing = [
                r
                for r in resources.async_items()
                if str(r.get("url", "")).startswith(
                    f"{FRONTEND_URL_BASE}/{CARD_JS_FILENAME}"
                )
            ]
            if not existing:
                await resources.async_create_item(
                    {"res_type": "module", "url": CARD_RESOURCE_URL}
                )
                _LOGGER.info(
                    "Auto-registered Lovelace resource %s", CARD_RESOURCE_URL
                )
            elif existing[0].get("url") != CARD_RESOURCE_URL and existing[0].get("id"):
                await resources.async_update_item(
                    existing[0]["id"],
                    {"res_type": "module", "url": CARD_RESOURCE_URL},
                )
                _LOGGER.info(
                    "Updated Lovelace resource URL to %s", CARD_RESOURCE_URL
                )
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug(
            "Could not auto-register Lovelace storage resource (add %s manually if in YAML mode): %s",
            CARD_RESOURCE_URL,
            err,
        )


def _async_register_services(hass: HomeAssistant) -> None:
    """Register native Home Assistant service actions with SupportsResponse."""
    if hass.services.has_service(DOMAIN, SERVICE_SCAN_BOTTLE):
        return

    async def handle_scan_bottle(call: ServiceCall) -> ServiceResponse:
        coordinator = _get_coordinator(hass, call.data.get("entry_id"))
        return await coordinator.async_scan_bottle(
            image_data=call.data.get("image_data"),
            camera_entity_id=call.data.get("camera_entity_id"),
        )

    async def handle_add_wine(call: ServiceCall) -> ServiceResponse:
        coordinator = _get_coordinator(hass, call.data.get("entry_id"))
        wine = await coordinator.async_add_wine(dict(call.data))
        return {"wine": wine}

    async def handle_adjust_quantity(call: ServiceCall) -> ServiceResponse:
        coordinator = _get_coordinator(hass, call.data.get("entry_id"))
        wine = await coordinator.async_adjust_quantity(dict(call.data))
        return {"wine": wine}

    async def handle_update_wine(call: ServiceCall) -> ServiceResponse:
        coordinator = _get_coordinator(hass, call.data.get("entry_id"))
        data = dict(call.data)
        wine_id = int(data.pop("wine_id"))
        data.pop("entry_id", None)
        wine = await coordinator.async_update_wine(wine_id, data)
        return {"wine": wine}

    async def handle_refresh_vivino(call: ServiceCall) -> ServiceResponse:
        coordinator = _get_coordinator(hass, call.data.get("entry_id"))
        return await coordinator.async_refresh_vivino(call.data.get("wine_id"))

    async def handle_export_inventory(call: ServiceCall) -> ServiceResponse:
        coordinator = _get_coordinator(hass, call.data.get("entry_id"))
        return await coordinator.async_export_inventory(
            call.data.get("format", "csv")
        )

    async def handle_create_backup(call: ServiceCall) -> ServiceResponse:
        coordinator = _get_coordinator(hass, call.data.get("entry_id"))
        return await coordinator.async_create_backup()

    async def handle_restore_backup(call: ServiceCall) -> ServiceResponse:
        coordinator = _get_coordinator(hass, call.data.get("entry_id"))
        return await coordinator.async_restore_backup(call.data.get("file_path"))

    async def handle_import_wines(call: ServiceCall) -> ServiceResponse:
        coordinator = _get_coordinator(hass, call.data.get("entry_id"))
        return await coordinator.async_import_wines(
            payload=call.data.get("source"),
            format_type=call.data.get("format", "auto"),
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SCAN_BOTTLE,
        handle_scan_bottle,
        schema=vol.Schema(
            {
                vol.Optional("image_data"): vol.Any(None, str),
                vol.Optional("camera_entity_id"): str,
                vol.Optional("entry_id"): str,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_WINE,
        handle_add_wine,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADJUST_QUANTITY,
        handle_adjust_quantity,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_WINE,
        handle_update_wine,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_VIVINO,
        handle_refresh_vivino,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_INVENTORY,
        handle_export_inventory,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_BACKUP,
        handle_create_backup,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESTORE_BACKUP,
        handle_restore_backup,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT_WINES,
        handle_import_wines,
        supports_response=SupportsResponse.OPTIONAL,
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Wine Inventory from a config entry."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    merged_config: dict[str, Any] = {**entry.data, **entry.options}

    coordinator = WineInventoryCoordinator(
        hass=hass,
        entry_id=entry.entry_id,
        config=merged_config,
    )
    await coordinator.async_initialize()

    domain_data[entry.entry_id] = {"coordinator": coordinator}

    if not domain_data.get("_ws_registered"):
        async_register_websocket_commands(hass)
        domain_data["_ws_registered"] = True

    await _async_register_frontend(hass)
    _async_register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if entry_data and "coordinator" in entry_data:
        coordinator: WineInventoryCoordinator = entry_data["coordinator"]
        coordinator.update_config({**entry.data, **entry.options})


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Wine Inventory config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok
