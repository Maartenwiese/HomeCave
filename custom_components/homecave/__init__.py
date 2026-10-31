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
    MAX_IMAGE_DATA_URI_CHARS,
    MAX_IMPORT_PAYLOAD_CHARS,
    MAX_TEXT_FIELD_CHARS,
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

# Services that overwrite, bulk-load, or export the whole cellar: Home Assistant admins only.
ADMIN_SERVICES: frozenset[str] = frozenset(
    {
        SERVICE_CREATE_BACKUP,
        SERVICE_RESTORE_BACKUP,
        SERVICE_IMPORT_WINES,
        SERVICE_EXPORT_INVENTORY,
    }
)


def _bounded_service_data(value: Any) -> Any:
    """Voluptuous validator: reject oversized strings/collections in free-form service data."""
    if vol is None:  # pragma: no cover - standalone tests without voluptuous
        return value
    if not isinstance(value, dict):
        raise vol.Invalid("Expected a mapping of service data")
    if len(value) > 80:
        raise vol.Invalid("Too many fields in service data")
    for key, item in value.items():
        if key == "image_data":
            limit = MAX_IMAGE_DATA_URI_CHARS
        elif key == "source":
            limit = MAX_IMPORT_PAYLOAD_CHARS
        else:
            limit = MAX_TEXT_FIELD_CHARS
        if isinstance(item, str) and len(item) > limit:
            raise vol.Invalid(f"Field '{key}' is too long (max {limit} characters)")
        if isinstance(item, (list, tuple)) and len(item) > 500:
            raise vol.Invalid(f"Field '{key}' has too many items")
    return value


async def _async_resolve_call_user(hass: HomeAssistant, call: ServiceCall) -> Any | None:
    """Return the HA user behind a service call (None for system/automation calls)."""
    user_id = getattr(getattr(call, "context", None), "user_id", None)
    if not user_id:
        return None
    return await hass.auth.async_get_user(user_id)


def _admin_only(hass: HomeAssistant, handler: Any) -> Any:
    """Wrap a service handler so only admins (or system-originated calls) may run it.

    Mirrors ``homeassistant.helpers.service.async_register_admin_service`` semantics while
    preserving ``supports_response`` (which that helper does not accept).
    """

    async def _wrapper(call: ServiceCall) -> ServiceResponse:
        user_id = getattr(getattr(call, "context", None), "user_id", None)
        if user_id:
            from homeassistant.exceptions import Unauthorized, UnknownUser

            user = await hass.auth.async_get_user(user_id)
            if user is None:
                raise UnknownUser(context=call.context)
            if not user.is_admin:
                raise Unauthorized(context=call.context)
        _LOGGER.warning(
            "HomeCave audit: service %s.%s called by user %s",
            DOMAIN,
            call.service,
            user_id or "system",
        )
        return await handler(call)

    return _wrapper


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
        user = await _async_resolve_call_user(hass, call)
        return await coordinator.async_scan_bottle(
            image_data=call.data.get("image_data"),
            camera_entity_id=call.data.get("camera_entity_id"),
            user=user,
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

    entry_id_field = {vol.Optional("entry_id"): vol.All(str, vol.Length(max=64))}
    free_form_schema = vol.All(
        vol.Schema(entry_id_field, extra=vol.ALLOW_EXTRA), _bounded_service_data
    )
    file_path_field = vol.All(str, vol.Length(min=1, max=512))

    hass.services.async_register(
        DOMAIN,
        SERVICE_SCAN_BOTTLE,
        handle_scan_bottle,
        schema=vol.Schema(
            {
                vol.Optional("image_data"): vol.Any(
                    None, vol.All(str, vol.Length(max=MAX_IMAGE_DATA_URI_CHARS))
                ),
                vol.Optional("camera_entity_id"): vol.All(
                    str, vol.Match(r"^(camera|image)\.[a-z0-9_]+$")
                ),
                **entry_id_field,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_WINE,
        handle_add_wine,
        schema=free_form_schema,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADJUST_QUANTITY,
        handle_adjust_quantity,
        schema=vol.Schema(
            {
                vol.Required("wine_id"): vol.All(vol.Coerce(int), vol.Range(min=1)),
                vol.Required("delta"): vol.All(vol.Coerce(int), vol.Range(min=-1000, max=1000)),
                **entry_id_field,
            },
            extra=vol.ALLOW_EXTRA,
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_WINE,
        handle_update_wine,
        schema=vol.All(
            vol.Schema(
                {
                    vol.Required("wine_id"): vol.All(vol.Coerce(int), vol.Range(min=1)),
                    **entry_id_field,
                },
                extra=vol.ALLOW_EXTRA,
            ),
            _bounded_service_data,
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_VIVINO,
        handle_refresh_vivino,
        schema=vol.Schema(
            {
                vol.Optional("wine_id"): vol.Any(
                    None, vol.All(vol.Coerce(int), vol.Range(min=1))
                ),
                **entry_id_field,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_INVENTORY,
        _admin_only(hass, handle_export_inventory),
        schema=vol.Schema(
            {vol.Optional("format", default="csv"): vol.In(["csv", "json"]), **entry_id_field}
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_BACKUP,
        _admin_only(hass, handle_create_backup),
        schema=vol.Schema(entry_id_field),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESTORE_BACKUP,
        _admin_only(hass, handle_restore_backup),
        schema=vol.Schema({vol.Required("file_path"): file_path_field, **entry_id_field}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT_WINES,
        _admin_only(hass, handle_import_wines),
        schema=vol.Schema(
            {
                vol.Required("source"): vol.Any(
                    vol.All(str, vol.Length(max=MAX_IMPORT_PAYLOAD_CHARS)),
                    vol.All(list, vol.Length(max=20000)),
                    dict,
                ),
                vol.Optional("format", default="auto"): vol.In(
                    ["auto", "json", "csv", "zip"]
                ),
                **entry_id_field,
            }
        ),
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

    # Security: remove any backup archives that older versions staged into the
    # unauthenticated <config>/www/homecave/backups/ folder.
    from .backup import purge_public_backup_copies

    removed = await hass.async_add_executor_job(
        purge_public_backup_copies, Path(hass.config.config_dir)
    )
    if removed:
        _LOGGER.warning(
            "HomeCave removed %d publicly exposed backup copies from www/homecave/backups",
            removed,
        )

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
