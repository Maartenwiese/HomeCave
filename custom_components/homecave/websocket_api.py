"""WebSocket API commands for the HomeCave frontend cards."""

from __future__ import annotations

import logging
from typing import Any

try:
    import voluptuous as vol
    from homeassistant.components import websocket_api
    from homeassistant.core import HomeAssistant
except ImportError:  # pragma: no cover - allows standalone unit test imports
    try:
        import voluptuous as vol  # type: ignore[no-redef]
    except ImportError:
        class _StubVol:
            @staticmethod
            def Required(key: Any, **kwargs: Any) -> Any:
                return key

            @staticmethod
            def Optional(key: Any, **kwargs: Any) -> Any:
                return key

            @staticmethod
            def In(values: Any) -> Any:
                return values

            @staticmethod
            def Any(*args: Any) -> Any:
                return args

            @staticmethod
            def Coerce(typ: Any) -> Any:
                return typ

            @staticmethod
            def All(*args: Any) -> Any:
                return args[0] if args else None

            @staticmethod
            def Length(**kwargs: Any) -> Any:
                return None

            @staticmethod
            def Range(**kwargs: Any) -> Any:
                return None

            @staticmethod
            def Match(pattern: Any, **kwargs: Any) -> Any:
                return str

            Invalid = ValueError

        vol = _StubVol()  # type: ignore[assignment]
    HomeAssistant = Any  # type: ignore[assignment,misc]

    class _StubWebsocketApi:
        ActiveConnection = Any

        @staticmethod
        def websocket_command(schema: Any):
            def _decorator(func: Any) -> Any:
                return func
            return _decorator

        @staticmethod
        def async_response(func: Any) -> Any:
            return func

        @staticmethod
        def require_admin(func: Any) -> Any:
            return func

        @staticmethod
        def async_register_command(hass: Any, handler: Any) -> None:
            pass

    websocket_api = _StubWebsocketApi()  # type: ignore[assignment]

from .const import (
    DOMAIN,
    MAX_IMAGE_DATA_URI_CHARS,
    MAX_IMPORT_PAYLOAD_CHARS,
    MAX_SHORT_TEXT_CHARS,
    MAX_SOMMELIER_MESSAGE_CHARS,
    MAX_TEXT_FIELD_CHARS,
)
from .coordinator import WineInventoryCoordinator
from .covers import get_cover_signer, sign_cover_urls
from .scanner import LLMScannerError

_LOGGER = logging.getLogger(__name__)


def _bounded_str(max_len: int) -> Any:
    """Voluptuous validator for a string with a maximum length (falls back to str in stubs)."""
    if hasattr(vol, "All") and hasattr(vol, "Length"):
        return vol.All(str, vol.Length(max=max_len))
    return str


_SHORT_STR = _bounded_str(MAX_SHORT_TEXT_CHARS)
_TEXT_STR = _bounded_str(MAX_TEXT_FIELD_CHARS)
_URL_STR = _bounded_str(2048)
_MESSAGE_STR = _bounded_str(MAX_SOMMELIER_MESSAGE_CHARS)
_IMAGE_STR = _bounded_str(MAX_IMAGE_DATA_URI_CHARS)
_ARCHIVE_STR = _bounded_str(MAX_IMPORT_PAYLOAD_CHARS)
_WINE_ID = (
    vol.All(int, vol.Range(min=1)) if hasattr(vol, "All") and hasattr(vol, "Range") else int
)

_USER_FACING_ERRORS: tuple[type[BaseException], ...] = (
    ValueError,
    PermissionError,
    FileNotFoundError,
    LLMScannerError,
)


def _ws_error_message(err: BaseException) -> str:
    """Return a client-safe error message; unexpected exceptions stay in the HA log."""
    if isinstance(err, _USER_FACING_ERRORS):
        return " ".join(str(err).split())[:300]
    _LOGGER.exception("HomeCave WebSocket command failed", exc_info=err)
    return "Request failed. See the Home Assistant logs for details."


def _bounded_updates(value: Any) -> Any:
    """Reject oversized update payloads (field count, string length, image data size)."""
    if not isinstance(value, dict):
        raise vol.Invalid("updates must be an object")
    if len(value) > 60:
        raise vol.Invalid("too many fields in updates")
    for key, item in value.items():
        if not isinstance(key, str) or len(key) > 64:
            raise vol.Invalid("invalid update field name")
        limit = MAX_IMAGE_DATA_URI_CHARS if key == "image_data" else MAX_TEXT_FIELD_CHARS
        if isinstance(item, str) and len(item) > limit:
            raise vol.Invalid(f"field '{key}' is too long")
        if isinstance(item, (list, dict)) and len(item) > 100:
            raise vol.Invalid(f"field '{key}' has too many items")
        if item is not None and not isinstance(item, (str, int, float, bool, list)):
            raise vol.Invalid(f"field '{key}' has an unsupported type")
    return value


def _read_backup_if_small(zip_path: Any, limit: int) -> bytes | None:
    """Executor helper: return backup bytes when the file exists and is at most ``limit`` bytes."""
    if not str(zip_path) or not zip_path.is_file() or zip_path.stat().st_size > limit:
        return None
    return zip_path.read_bytes()


def _send_result(hass: Any, connection: Any, msg_id: int, payload: Any) -> None:
    """Send a WebSocket result with private cover ``image_url`` values signed for this session."""
    payload = sign_cover_urls(
        payload,
        get_cover_signer(hass),
        getattr(connection, "refresh_token_id", None),
    )
    connection.send_result(msg_id, payload)


def _require_admin_user(connection: Any, msg: dict[str, Any]) -> bool:
    """Verify the active WebSocket connection belongs to a Home Assistant administrator."""
    user = getattr(connection, "user", None)
    if user is None or not getattr(user, "is_admin", False):
        connection.send_error(
            msg["id"],
            "unauthorized",
            "Administrator privileges are required for backup, restore, and bulk import operations.",
        )
        return False
    return True


def _get_coordinator(
    hass: HomeAssistant, entry_id: str | None = None
) -> WineInventoryCoordinator:
    """Resolve the active WineInventoryCoordinator instance."""
    domain_data = hass.data.get(DOMAIN) or {}
    if entry_id and entry_id in domain_data:
        return domain_data[entry_id]["coordinator"]
    for val in domain_data.values():
        if isinstance(val, dict) and "coordinator" in val:
            return val["coordinator"]
    raise ValueError("No active HomeCave integration instance found.")


def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Register all HomeCave WebSocket commands with Home Assistant."""
    websocket_api.async_register_command(hass, ws_list_wines)
    websocket_api.async_register_command(hass, ws_get_wine_detail)
    websocket_api.async_register_command(hass, ws_scan_bottle)
    websocket_api.async_register_command(hass, ws_add_wine)
    websocket_api.async_register_command(hass, ws_adjust_quantity)
    websocket_api.async_register_command(hass, ws_update_wine)
    websocket_api.async_register_command(hass, ws_delete_wine)
    websocket_api.async_register_command(hass, ws_unlink_vivino)
    websocket_api.async_register_command(hass, ws_link_vivino_url)
    websocket_api.async_register_command(hass, ws_research_wine)
    websocket_api.async_register_command(hass, ws_refresh_vivino)
    websocket_api.async_register_command(hass, ws_create_backup)
    websocket_api.async_register_command(hass, ws_restore_backup)
    websocket_api.async_register_command(hass, ws_import_wines)
    websocket_api.async_register_command(hass, ws_list_backups)
    websocket_api.async_register_command(hass, ws_sommelier_chat)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/list",
        vol.Optional("entry_id"): _SHORT_STR,
        vol.Optional("include_out_of_stock", default=True): bool,
        vol.Optional("wine_type"): vol.Any(_SHORT_STR, None),
        vol.Optional("search"): vol.Any(_SHORT_STR, None),
    }
)
@websocket_api.async_response
async def ws_list_wines(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return full wine list, cellar statistics, and autocomplete metadata."""
    try:
        coordinator = _get_coordinator(hass, msg.get("entry_id"))
        wines = await hass.async_add_executor_job(
            lambda: coordinator.db.list_wines(
                include_out_of_stock=bool(msg.get("include_out_of_stock", True)),
                wine_type=msg.get("wine_type"),
                search=msg.get("search"),
            )
        )
        _send_result(hass, connection, 
            msg["id"],
            {
                "cellar_name": coordinator.cellar_name,
                "currency": coordinator.currency,
                "temperature_entity": coordinator.temperature_entity,
                "humidity_entity": coordinator.humidity_entity,
                "statistics": coordinator.statistics,
                "wines": wines,
            },
        )
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "list_failed", _ws_error_message(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/get_detail",
        vol.Required("wine_id"): _WINE_ID,
        vol.Optional("entry_id"): _SHORT_STR,
    }
)
@websocket_api.async_response
async def ws_get_wine_detail(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return single wine detail with purchase batches and notes timeline."""
    try:
        coordinator = _get_coordinator(hass, msg.get("entry_id"))
        detail = await hass.async_add_executor_job(
            coordinator.db.get_wine_detail,
            int(msg["wine_id"]),
        )
        if detail is None:
            connection.send_error(msg["id"], "not_found", "Wine not found")
            return
        _send_result(hass, connection, msg["id"], {"wine": detail})
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "detail_failed", _ws_error_message(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/scan_bottle",
        vol.Optional("image_data"): vol.Any(None, _IMAGE_STR),
        vol.Optional("camera_entity_id"): _TEXT_STR,
        vol.Optional("entry_id"): _SHORT_STR,
    }
)
@websocket_api.async_response
async def ws_scan_bottle(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Scan a wine bottle photo via Gemini Flash-Lite + Vivino lookup."""
    try:
        coordinator = _get_coordinator(hass, msg.get("entry_id"))
        result = await coordinator.async_scan_bottle(
            image_data=msg.get("image_data"),
            camera_entity_id=msg.get("camera_entity_id"),
            user=getattr(connection, "user", None),
        )
        _send_result(hass, connection, msg["id"], result)
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "scan_failed", _ws_error_message(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/add_wine",
        vol.Optional("wine_id"): vol.Any(int, None),
        vol.Required("winery"): _SHORT_STR,
        vol.Required("name"): _SHORT_STR,
        vol.Optional("vintage"): vol.Any(int, None),
        vol.Optional("wine_type", default="red"): _SHORT_STR,
        vol.Optional("varietal", default=""): _SHORT_STR,
        vol.Optional("region", default=""): _SHORT_STR,
        vol.Optional("country", default=""): _SHORT_STR,
        vol.Optional("alcohol_pct"): vol.Any(float, int, None),
        vol.Optional("bottle_size_ml", default=750): vol.All(int, vol.Range(min=50, max=30000)),
        vol.Optional("vivino_rating"): vol.Any(float, int, None),
        vol.Optional("vivino_ratings_count"): vol.Any(int, None),
        vol.Optional("vivino_url", default=""): _URL_STR,
        vol.Optional("vivino_price_estimate"): vol.Any(float, int, None),
        vol.Optional("rating_source", default=""): _SHORT_STR,
        vol.Optional("vivino_wine_id"): vol.Any(int, None),
        vol.Optional("vivino_match_confidence", default=""): _SHORT_STR,
        vol.Optional("drink_from_year"): vol.Any(int, None),
        vol.Optional("drink_until_year"): vol.Any(int, None),
        vol.Optional("drink_window_note", default=""): _TEXT_STR,
        vol.Optional("food_pairings"): vol.Any(vol.All(list, vol.Length(max=50)), _TEXT_STR, None),
        vol.Optional("general_notes", default=""): _TEXT_STR,
        vol.Optional("personal_rating"): vol.Any(float, int, None),
        vol.Optional("image_url", default=""): _URL_STR,
        vol.Optional("quantity", default=1): vol.All(int, vol.Range(min=0, max=10000)),
        vol.Optional("added_at"): vol.Any(_SHORT_STR, None),
        vol.Optional("purchase_location", default=""): _SHORT_STR,
        vol.Optional("purchase_price"): vol.Any(float, int, None),
        vol.Optional("storage_location", default=""): _SHORT_STR,
        vol.Optional("batch_notes", default=""): _TEXT_STR,
        vol.Optional("as_tasted", default=False): bool,
        vol.Optional("entry_id"): _SHORT_STR,
    }
)
@websocket_api.async_response
async def ws_add_wine(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Add a new wine or add a batch to an existing wine."""
    try:
        coordinator = _get_coordinator(hass, msg.get("entry_id"))
        wine = await coordinator.async_add_wine(msg)
        _send_result(hass, connection, 
            msg["id"],
            {"wine": wine, "statistics": coordinator.statistics},
        )
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "add_failed", _ws_error_message(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/adjust_quantity",
        vol.Required("wine_id"): _WINE_ID,
        vol.Required("delta"): vol.All(int, vol.Range(min=-1000, max=1000)),
        vol.Optional("batch_id"): vol.Any(int, None),
        vol.Optional("added_at"): vol.Any(_SHORT_STR, None),
        vol.Optional("purchase_location", default=""): _SHORT_STR,
        vol.Optional("purchase_price"): vol.Any(float, int, None),
        vol.Optional("storage_location", default=""): _SHORT_STR,
        vol.Optional("drink_window_note", default=""): _TEXT_STR,
        vol.Optional("notes", default=""): _TEXT_STR,
        vol.Optional("personal_rating"): vol.Any(float, int, None),
        vol.Optional("entry_id"): _SHORT_STR,
    }
)
@websocket_api.async_response
async def ws_adjust_quantity(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Adjust bottle count (+N adds a batch, -N consumes a bottle with optional tasting note)."""
    try:
        coordinator = _get_coordinator(hass, msg.get("entry_id"))
        wine = await coordinator.async_adjust_quantity(msg)
        _send_result(hass, connection, 
            msg["id"],
            {"wine": wine, "statistics": coordinator.statistics},
        )
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "adjust_failed", _ws_error_message(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/update_wine",
        vol.Required("wine_id"): _WINE_ID,
        vol.Required("updates"): vol.All(dict, _bounded_updates),
        vol.Optional("entry_id"): _SHORT_STR,
    }
)
@websocket_api.async_response
async def ws_update_wine(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Update wine fields (notes overview, drink window, Vivino fields, etc.)."""
    try:
        coordinator = _get_coordinator(hass, msg.get("entry_id"))
        wine = await coordinator.async_update_wine(
            int(msg["wine_id"]),
            dict(msg["updates"]),
        )
        _send_result(hass, connection, 
            msg["id"],
            {"wine": wine, "statistics": coordinator.statistics},
        )
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "update_failed", _ws_error_message(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/delete_wine",
        vol.Required("wine_id"): _WINE_ID,
        vol.Optional("entry_id"): _SHORT_STR,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_delete_wine(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Delete a wine record permanently (Home Assistant admins only)."""
    if not _require_admin_user(connection, msg):
        return
    try:
        coordinator = _get_coordinator(hass, msg.get("entry_id"))
        deleted = await coordinator.async_delete_wine(int(msg["wine_id"]))
        _send_result(hass, connection, 
            msg["id"],
            {"deleted": deleted, "statistics": coordinator.statistics},
        )
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "delete_failed", _ws_error_message(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/unlink_vivino",
        vol.Required("wine_id"): _WINE_ID,
        vol.Optional("entry_id"): _SHORT_STR,
    }
)
@websocket_api.async_response
async def ws_unlink_vivino(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Remove Vivino link, ratings, and estimates from a wine."""
    try:
        coordinator = _get_coordinator(hass, msg.get("entry_id"))
        wine = await coordinator.async_unlink_vivino(int(msg["wine_id"]))
        _send_result(hass, connection, 
            msg["id"],
            {"wine": wine, "statistics": coordinator.statistics},
        )
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "unlink_failed", _ws_error_message(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/link_vivino_url",
        vol.Required("wine_id"): _WINE_ID,
        vol.Required("url"): _URL_STR,
        vol.Optional("entry_id"): _SHORT_STR,
    }
)
@websocket_api.async_response
async def ws_link_vivino_url(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Link a wine to an exact Vivino wine page URL pasted by the user."""
    try:
        coordinator = _get_coordinator(hass, msg.get("entry_id"))
        result = await coordinator.async_link_vivino_url(int(msg["wine_id"]), str(msg["url"]))
        _send_result(hass, connection, msg["id"], {**result, "statistics": coordinator.statistics})
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "link_failed", _ws_error_message(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/research_wine",
        vol.Required("wine_id"): _WINE_ID,
        vol.Optional("entry_id"): _SHORT_STR,
    }
)
@websocket_api.async_response
async def ws_research_wine(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Perform deep Gemini AI research scan on wine producer and cuvée history."""
    try:
        coordinator = _get_coordinator(hass, msg.get("entry_id"))
        result = await coordinator.async_research_wine(int(msg["wine_id"]))
        _send_result(hass, connection, msg["id"], result)
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "research_failed", _ws_error_message(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/refresh_vivino",
        vol.Optional("wine_id"): vol.Any(int, None),
        vol.Optional("entry_id"): _SHORT_STR,
    }
)
@websocket_api.async_response
async def ws_refresh_vivino(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Refresh Vivino rating and price estimate for one or all wines."""
    try:
        coordinator = _get_coordinator(hass, msg.get("entry_id"))
        res = await coordinator.async_refresh_vivino(msg.get("wine_id"))
        _send_result(hass, connection, msg["id"], res)
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "vivino_refresh_failed", _ws_error_message(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/create_backup",
        vol.Optional("entry_id"): _SHORT_STR,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_create_backup(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create a complete .zip backup containing database, metadata, and images."""
    if not _require_admin_user(connection, msg):
        return
    try:
        import base64
        from pathlib import Path

        coordinator = _get_coordinator(hass, msg.get("entry_id"))
        res = dict(await coordinator.async_create_backup())
        zip_path = Path(str(res.get("file_path") or ""))
        # is_file() + stat() + read run together in one executor job (never on the event loop)
        raw_bytes = await hass.async_add_executor_job(
            _read_backup_if_small, zip_path, 20 * 1024 * 1024
        )
        if raw_bytes is not None:
            res["archive_base64"] = base64.b64encode(raw_bytes).decode("ascii")
        _send_result(hass, connection, msg["id"], res)
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "backup_failed", _ws_error_message(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/restore_backup",
        vol.Optional("file_path"): _SHORT_STR,
        vol.Optional("backup_filename"): _SHORT_STR,
        vol.Optional("zip_data"): _ARCHIVE_STR,
        vol.Optional("archive_base64"): _ARCHIVE_STR,
        vol.Optional("entry_id"): _SHORT_STR,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_restore_backup(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Restore database and images from a backup .zip file path or base64 data."""
    if not _require_admin_user(connection, msg):
        return
    try:
        coordinator = _get_coordinator(hass, msg.get("entry_id"))
        zip_data = msg.get("zip_data") or msg.get("archive_base64")
        file_path = msg.get("file_path") or msg.get("backup_filename")
        if zip_data:
            import base64

            if "," in zip_data:
                zip_data = zip_data.split(",", 1)[1]
            raw_bytes = base64.b64decode(zip_data)
            res = await coordinator.async_restore_backup(raw_bytes)
        elif file_path:
            res = await coordinator.async_restore_backup(file_path)
        else:
            raise ValueError("Either 'file_path' or 'zip_data' must be provided.")

        _send_result(hass, connection, msg["id"], res)
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "restore_failed", _ws_error_message(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/import_wines",
        vol.Optional("source"): _ARCHIVE_STR,
        vol.Optional("file_data"): _ARCHIVE_STR,
        vol.Optional("wines"): vol.All(list, vol.Length(max=20000)),
        vol.Optional("payload"): vol.Any(_ARCHIVE_STR, vol.All(list, vol.Length(max=20000)), dict),
        vol.Optional("format_type"): _SHORT_STR,
        vol.Optional("entry_id"): _SHORT_STR,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_import_wines(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Import wines from file path, base64 archive, or JSON list."""
    if not _require_admin_user(connection, msg):
        return
    try:
        coordinator = _get_coordinator(hass, msg.get("entry_id"))
        file_data = msg.get("file_data")
        source = msg.get("source")
        wines_list = msg.get("wines")
        payload = msg.get("payload")
        format_type = str(msg.get("format_type") or "auto")

        if file_data:
            import base64

            if "," in file_data:
                file_data = file_data.split(",", 1)[1]
            raw_bytes = base64.b64decode(file_data)
            res = await coordinator.async_import_wines(raw_bytes, format_type)
        elif payload is not None:
            if format_type == "zip" and isinstance(payload, str):
                import base64

                raw_str = payload.split(",", 1)[1] if "," in payload else payload
                res = await coordinator.async_import_wines(
                    base64.b64decode(raw_str), "zip"
                )
            else:
                res = await coordinator.async_import_wines(payload, format_type)
        elif source:
            res = await coordinator.async_import_wines(source, format_type)
        elif wines_list is not None:
            res = await coordinator.async_import_wines(wines_list, format_type)
        else:
            raise ValueError("No import payload provided.")

        _send_result(hass, connection, msg["id"], res)
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "import_failed", _ws_error_message(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/list_backups",
        vol.Optional("entry_id"): _SHORT_STR,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_list_backups(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List available backups on disk."""
    if not _require_admin_user(connection, msg):
        return
    try:
        coordinator = _get_coordinator(hass, msg.get("entry_id"))
        res = await coordinator.async_list_backups()
        _send_result(hass, connection, msg["id"], {"backups": res})
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "list_backups_failed", _ws_error_message(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/sommelier_chat",
        vol.Optional("entry_id"): _SHORT_STR,
        vol.Optional("message"): _MESSAGE_STR,
        vol.Optional("query"): _MESSAGE_STR,
        vol.Optional("image_data"): vol.Any(None, _IMAGE_STR),
        vol.Optional("history"): vol.All(list, vol.Length(max=10)),
    }
)
@websocket_api.async_response
async def ws_sommelier_chat(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Ask SommAI for food-wine pairing advice."""
    try:
        coordinator = _get_coordinator(hass, msg.get("entry_id"))
        query_text = msg.get("message") or msg.get("query") or ""
        image_data = msg.get("image_data")
        history = msg.get("history") or []
        res = await coordinator.async_sommelier_chat(
            query_text=query_text,
            image_data=image_data,
            conversation_history=history,
        )
        _send_result(hass, connection, msg["id"], res)
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "sommelier_chat_failed", _ws_error_message(err))
