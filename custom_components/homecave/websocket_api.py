"""WebSocket API commands for the HomeCave frontend cards."""

from __future__ import annotations

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

from .const import DOMAIN
from .coordinator import WineInventoryCoordinator


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
        vol.Optional("entry_id"): str,
        vol.Optional("include_out_of_stock", default=True): bool,
        vol.Optional("wine_type"): vol.Any(str, None),
        vol.Optional("search"): vol.Any(str, None),
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
        connection.send_result(
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
        connection.send_error(msg["id"], "list_failed", str(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/get_detail",
        vol.Required("wine_id"): int,
        vol.Optional("entry_id"): str,
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
        connection.send_result(msg["id"], {"wine": detail})
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "detail_failed", str(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/scan_bottle",
        vol.Optional("image_data"): vol.Any(None, str),
        vol.Optional("camera_entity_id"): str,
        vol.Optional("entry_id"): str,
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
        )
        connection.send_result(msg["id"], result)
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "scan_failed", str(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/add_wine",
        vol.Optional("wine_id"): vol.Any(int, None),
        vol.Required("winery"): str,
        vol.Required("name"): str,
        vol.Optional("vintage"): vol.Any(int, None),
        vol.Optional("wine_type", default="red"): str,
        vol.Optional("varietal", default=""): str,
        vol.Optional("region", default=""): str,
        vol.Optional("country", default=""): str,
        vol.Optional("alcohol_pct"): vol.Any(float, int, None),
        vol.Optional("bottle_size_ml", default=750): int,
        vol.Optional("vivino_rating"): vol.Any(float, int, None),
        vol.Optional("vivino_ratings_count"): vol.Any(int, None),
        vol.Optional("vivino_url", default=""): str,
        vol.Optional("vivino_price_estimate"): vol.Any(float, int, None),
        vol.Optional("rating_source", default=""): str,
        vol.Optional("vivino_wine_id"): vol.Any(int, None),
        vol.Optional("vivino_match_confidence", default=""): str,
        vol.Optional("drink_from_year"): vol.Any(int, None),
        vol.Optional("drink_until_year"): vol.Any(int, None),
        vol.Optional("drink_window_note", default=""): str,
        vol.Optional("food_pairings"): vol.Any(list, str, None),
        vol.Optional("general_notes", default=""): str,
        vol.Optional("personal_rating"): vol.Any(float, int, None),
        vol.Optional("image_url", default=""): str,
        vol.Optional("quantity", default=1): int,
        vol.Optional("added_at"): vol.Any(str, None),
        vol.Optional("purchase_location", default=""): str,
        vol.Optional("purchase_price"): vol.Any(float, int, None),
        vol.Optional("storage_location", default=""): str,
        vol.Optional("batch_notes", default=""): str,
        vol.Optional("as_tasted", default=False): bool,
        vol.Optional("entry_id"): str,
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
        connection.send_result(
            msg["id"],
            {"wine": wine, "statistics": coordinator.statistics},
        )
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "add_failed", str(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/adjust_quantity",
        vol.Required("wine_id"): int,
        vol.Required("delta"): int,
        vol.Optional("batch_id"): vol.Any(int, None),
        vol.Optional("added_at"): vol.Any(str, None),
        vol.Optional("purchase_location", default=""): str,
        vol.Optional("purchase_price"): vol.Any(float, int, None),
        vol.Optional("storage_location", default=""): str,
        vol.Optional("drink_window_note", default=""): str,
        vol.Optional("notes", default=""): str,
        vol.Optional("personal_rating"): vol.Any(float, int, None),
        vol.Optional("entry_id"): str,
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
        connection.send_result(
            msg["id"],
            {"wine": wine, "statistics": coordinator.statistics},
        )
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "adjust_failed", str(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/update_wine",
        vol.Required("wine_id"): int,
        vol.Required("updates"): dict,
        vol.Optional("entry_id"): str,
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
        connection.send_result(
            msg["id"],
            {"wine": wine, "statistics": coordinator.statistics},
        )
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "update_failed", str(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/delete_wine",
        vol.Required("wine_id"): int,
        vol.Optional("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_delete_wine(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Delete a wine record permanently."""
    try:
        coordinator = _get_coordinator(hass, msg.get("entry_id"))
        deleted = await coordinator.async_delete_wine(int(msg["wine_id"]))
        connection.send_result(
            msg["id"],
            {"deleted": deleted, "statistics": coordinator.statistics},
        )
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "delete_failed", str(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/unlink_vivino",
        vol.Required("wine_id"): int,
        vol.Optional("entry_id"): str,
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
        connection.send_result(
            msg["id"],
            {"wine": wine, "statistics": coordinator.statistics},
        )
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "unlink_failed", str(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/link_vivino_url",
        vol.Required("wine_id"): int,
        vol.Required("url"): str,
        vol.Optional("entry_id"): str,
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
        connection.send_result(msg["id"], {**result, "statistics": coordinator.statistics})
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "link_failed", str(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/research_wine",
        vol.Required("wine_id"): int,
        vol.Optional("entry_id"): str,
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
        connection.send_result(msg["id"], result)
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "research_failed", str(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/refresh_vivino",
        vol.Optional("wine_id"): vol.Any(int, None),
        vol.Optional("entry_id"): str,
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
        connection.send_result(msg["id"], res)
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "vivino_refresh_failed", str(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/create_backup",
        vol.Optional("entry_id"): str,
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
        if zip_path.is_file() and zip_path.stat().st_size <= 20 * 1024 * 1024:
            raw_bytes = await hass.async_add_executor_job(zip_path.read_bytes)
            res["archive_base64"] = base64.b64encode(raw_bytes).decode("ascii")
        connection.send_result(msg["id"], res)
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "backup_failed", str(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/restore_backup",
        vol.Optional("file_path"): str,
        vol.Optional("backup_filename"): str,
        vol.Optional("zip_data"): str,
        vol.Optional("archive_base64"): str,
        vol.Optional("entry_id"): str,
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

        connection.send_result(msg["id"], res)
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "restore_failed", str(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/import_wines",
        vol.Optional("source"): str,
        vol.Optional("file_data"): str,
        vol.Optional("wines"): list,
        vol.Optional("payload"): vol.Any(str, list, dict),
        vol.Optional("format_type"): str,
        vol.Optional("entry_id"): str,
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

        connection.send_result(msg["id"], res)
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "import_failed", str(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/list_backups",
        vol.Optional("entry_id"): str,
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
        connection.send_result(msg["id"], {"backups": res})
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "list_backups_failed", str(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homecave/sommelier_chat",
        vol.Optional("entry_id"): str,
        vol.Optional("message"): str,
        vol.Optional("query"): str,
        vol.Optional("image_data"): vol.Any(None, str),
        vol.Optional("history"): list,
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
        connection.send_result(msg["id"], res)
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "sommelier_chat_failed", str(err))
