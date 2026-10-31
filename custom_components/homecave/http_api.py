"""HTTP REST API and Asset Views for the Standalone HomeCave Mobile Web App (PWA)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from aiohttp import web

try:
    from homeassistant.components.http import HomeAssistantView
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import area_registry as ar
except ImportError:  # pragma: no cover - allows standalone unit test imports
    HomeAssistant = Any  # type: ignore[assignment,misc]
    ar = None  # type: ignore[assignment]

    class HomeAssistantView:  # type: ignore[no-redef]
        """Minimal stub base class when homeassistant runtime is absent."""

        url: str = ""
        extra_urls: list[str] = []
        name: str = ""
        requires_auth: bool = True

        @staticmethod
        def json(
            result: Any,
            status_code: int = 200,
        ) -> web.Response:
            return web.Response(
                text=json.dumps(result),
                status=status_code,
                content_type="application/json",
            )

from .backup import resolve_safe_storage_path
from .const import (
    BACKUPS_SUBDIR,
    DOMAIN,
    EXPORTS_SUBDIR,
    MAX_IMAGE_DATA_URI_CHARS,
    MAX_IMPORT_PAYLOAD_CHARS,
    MAX_SOMMELIER_MESSAGE_CHARS,
    MOBILE_APP_URL_BASE,
    VERSION,
)
from .coordinator import WineInventoryCoordinator
from .scanner import LLMScannerError

_LOGGER = logging.getLogger(__name__)

# Actions that change or expose the whole cellar are restricted to Home Assistant admins
# (matches the admin-only WebSocket commands and services).
ADMIN_POST_ACTIONS: frozenset[str] = frozenset({"backup_create", "import", "delete"})
# Exceptions whose messages are authored by HomeCave and safe to show to the user.
USER_FACING_ERRORS: tuple[type[BaseException], ...] = (
    ValueError,
    PermissionError,
    FileNotFoundError,
    KeyError,
    LLMScannerError,
)
MAX_ACTION_NAME_CHARS = 40


def _is_admin(user: Any | None) -> bool:
    return bool(user is not None and getattr(user, "is_admin", False))


def _public_error_message(err: BaseException) -> str:
    """Return a short client-safe error message (internals stay in the HA log)."""
    if isinstance(err, KeyError):
        return f"Missing required field: {str(err)[:60]}"
    if isinstance(err, USER_FACING_ERRORS):
        return " ".join(str(err).split())[:300]
    return "Request failed. See the Home Assistant logs for details."


def _safe_action(action: Any) -> str:
    """Sanitize a URL path segment before logging it (prevents log injection)."""
    return "".join(ch for ch in str(action)[:MAX_ACTION_NAME_CHARS] if ch.isalnum() or ch in "_-")


def _too_long(value: Any, limit: int) -> bool:
    return isinstance(value, str) and len(value) > limit

MOBILE_FRONTEND_DIR = Path(__file__).parent / "frontend" / "mobile"

# (mime type, charset). aiohttp rejects "charset=" inside content_type, so the
# charset must be passed separately to web.Response.
CONTENT_TYPES: dict[str, tuple[str, str | None]] = {
    ".html": ("text/html", "utf-8"),
    ".js": ("application/javascript", "utf-8"),
    ".css": ("text/css", "utf-8"),
    ".webmanifest": ("application/manifest+json", "utf-8"),
    ".json": ("application/json", "utf-8"),
    ".svg": ("image/svg+xml", None),
    ".png": ("image/png", None),
}

DEFAULT_STORAGE_AREAS = [
    "HomeCave",
    "HomeCave Fridge",
    "Kitchen Rack",
    "Basement",
    "Dining Room",
    "Pantry",
    "Garage",
]


def _resolve_coordinator(
    hass: HomeAssistant, entry_id: str | None = None
) -> WineInventoryCoordinator:
    """Resolve active WineInventoryCoordinator from hass.data."""
    domain_data = getattr(hass, "data", {}).get(DOMAIN) or {}
    if entry_id and entry_id in domain_data:
        return domain_data[entry_id]["coordinator"]
    for val in domain_data.values():
        if isinstance(val, dict) and "coordinator" in val:
            return val["coordinator"]
    raise ValueError("No active HomeCave integration instance found.")


def _resolve_available_areas(
    hass: HomeAssistant, coordinator: WineInventoryCoordinator
) -> list[str]:
    """Combine Home Assistant areas, database storage locations, and default cellar areas."""
    area_set: set[str] = set()
    try:
        if ar is not None:
            reg = ar.async_get(hass)
            for area in reg.async_list_areas():
                if getattr(area, "name", None):
                    area_set.add(str(area.name).strip())
    except Exception:  # noqa: BLE001
        pass

    for loc in (coordinator.statistics or {}).get("known_storage_locations") or []:
        if loc and str(loc).strip():
            area_set.add(str(loc).strip())

    for default_loc in DEFAULT_STORAGE_AREAS:
        area_set.add(default_loc)

    return sorted(area_set, key=str.lower)


def _resolve_sensor_display(hass: HomeAssistant, entity_id: str | None) -> str | None:
    """Read formatted state + unit from hass.states if configured."""
    if not entity_id or not hasattr(hass, "states") or hass.states is None:
        return None
    st = hass.states.get(entity_id)
    if not st or st.state in ("unknown", "unavailable", None):
        return None
    unit = (getattr(st, "attributes", {}) or {}).get("unit_of_measurement", "")
    return f"{st.state} {unit}".strip()


async def async_handle_pwa_get(
    hass: HomeAssistant,
    action: str,
    query: dict[str, Any],
    *,
    authenticated_user: str | None = None,
    user: Any | None = None,
) -> tuple[int, dict[str, Any]]:
    """Execute GET actions for the standalone mobile PWA.

    ``user`` is the authenticated Home Assistant user. Backup listings (filenames and signed
    download links) are only returned to admins; absolute server paths are never returned.
    """
    coordinator = _resolve_coordinator(hass, query.get("entry_id"))

    if action == "state":
        include_out_of_stock = str(query.get("include_out_of_stock", "true")).lower() != "false"
        wine_type = query.get("wine_type") or None
        search = query.get("search") or None

        if hasattr(hass, "async_add_executor_job"):
            wines = await hass.async_add_executor_job(
                lambda: coordinator.db.list_wines(
                    include_out_of_stock=include_out_of_stock,
                    wine_type=wine_type,
                    search=search,
                )
            )
        else:
            wines = coordinator.db.list_wines(
                include_out_of_stock=include_out_of_stock,
                wine_type=wine_type,
                search=search,
            )

        backups: list[dict[str, Any]] = []
        if _is_admin(user):
            backups = [
                {k: v for k, v in b.items() if k != "file_path"}
                for b in await coordinator.async_list_backups()
            ]

        return 200, {
            "version": VERSION,
            "authenticated_user": authenticated_user or "",
            "is_admin": _is_admin(user),
            "cellar_name": coordinator.cellar_name,
            "currency": coordinator.currency,
            "temperature_entity": coordinator.temperature_entity,
            "humidity_entity": coordinator.humidity_entity,
            "temperature_display": _resolve_sensor_display(
                hass, coordinator.temperature_entity
            ),
            "humidity_display": _resolve_sensor_display(
                hass, coordinator.humidity_entity
            ),
            "areas": _resolve_available_areas(hass, coordinator),
            "statistics": coordinator.statistics,
            "wines": wines,
            "backups": backups,
        }

    if action == "detail":
        raw_id = query.get("wine_id")
        if not raw_id:
            return 400, {"error": "Missing required query parameter: wine_id"}
        wine_id = int(raw_id)
        if hasattr(hass, "async_add_executor_job"):
            detail = await hass.async_add_executor_job(
                coordinator.db.get_wine_detail, wine_id
            )
        else:
            detail = coordinator.db.get_wine_detail(wine_id)
        if detail is None:
            return 404, {"error": "Wine not found"}
        return 200, {"wine": detail}

    if action == "import_progress":
        return 200, coordinator.get_import_progress()

    return 404, {"error": f"Unknown GET action: {_safe_action(action)}"}


async def async_handle_pwa_post(
    hass: HomeAssistant,
    action: str,
    payload: dict[str, Any],
    *,
    user: Any | None = None,
) -> tuple[int, dict[str, Any]]:
    """Execute POST actions for the standalone mobile PWA.

    ``user`` is the authenticated Home Assistant user making the request. Destructive or
    whole-cellar actions (ADMIN_POST_ACTIONS) require an admin, mirroring the WebSocket API.
    """
    if action in ADMIN_POST_ACTIONS and not _is_admin(user):
        return 403, {"error": "Administrator privileges are required for this action."}

    # Request-size guards (memory / LLM-cost abuse)
    if _too_long(payload.get("image_data"), MAX_IMAGE_DATA_URI_CHARS):
        return 413, {"error": "Image is too large (maximum 10 MB)."}
    for key in ("message", "query", "dish_description"):
        if _too_long(payload.get(key), MAX_SOMMELIER_MESSAGE_CHARS):
            return 413, {"error": f"Message is too long (maximum {MAX_SOMMELIER_MESSAGE_CHARS} characters)."}
    for key in ("content", "payload", "source"):
        if _too_long(payload.get(key), MAX_IMPORT_PAYLOAD_CHARS):
            return 413, {"error": "Import file is too large (maximum 50 MB)."}

    coordinator = _resolve_coordinator(hass, payload.get("entry_id"))

    if action == "scan":
        res = await coordinator.async_scan_bottle(
            image_data=payload.get("image_data"),
            camera_entity_id=payload.get("camera_entity_id"),
            user=user,
        )
        return 200, res

    if action == "add":
        wine = await coordinator.async_add_wine(dict(payload))
        return 200, {"wine": wine, "statistics": coordinator.statistics}

    if action == "adjust":
        wine = await coordinator.async_adjust_quantity(dict(payload))
        return 200, {"wine": wine, "statistics": coordinator.statistics}

    if action == "update":
        wine_id = int(payload["wine_id"])
        updates = dict(payload.get("updates") or {})
        wine = await coordinator.async_update_wine(wine_id, updates)
        return 200, {"wine": wine, "statistics": coordinator.statistics}

    if action == "delete":
        wine_id = int(payload["wine_id"])
        deleted = await coordinator.async_delete_wine(wine_id)
        _LOGGER.warning(
            "HomeCave audit: wine %s deleted via mobile app by user %s",
            wine_id,
            getattr(user, "id", "unknown"),
        )
        return 200, {"deleted": deleted, "statistics": coordinator.statistics}

    if action == "research":
        wine_id = int(payload["wine_id"])
        res = await coordinator.async_research_wine(wine_id)
        return 200, res

    if action == "link_vivino":
        wine_id = int(payload["wine_id"])
        url = str(payload.get("url") or "")
        res = await coordinator.async_link_vivino_url(wine_id, url)
        return 200, {**res, "statistics": coordinator.statistics}

    if action == "unlink_vivino":
        wine_id = int(payload["wine_id"])
        wine = await coordinator.async_unlink_vivino(wine_id)
        return 200, {"wine": wine, "statistics": coordinator.statistics}

    if action == "refresh_vivino":
        raw_id = payload.get("wine_id")
        wine_id = int(raw_id) if raw_id is not None else None
        res = await coordinator.async_refresh_vivino(wine_id)
        return 200, res

    if action == "backup_create":
        res = await coordinator.async_create_backup()
        _LOGGER.warning(
            "HomeCave audit: backup created via mobile app by user %s",
            getattr(user, "id", "unknown"),
        )
        res = {k: v for k, v in res.items() if k != "file_path"}
        return 200, {
            "ok": True,
            "backup": res,
            "download_url": res.get("download_url", ""),
            "statistics": coordinator.statistics,
        }

    if action == "sommai":
        query_text = (
            payload.get("message")
            or payload.get("query")
            or payload.get("dish_description")
            or ""
        )
        image_data = payload.get("image_data")
        history = payload.get("history") or []
        if not isinstance(history, list):
            history = []
        res = await coordinator.async_sommelier_chat(
            query_text=str(query_text),
            image_data=image_data,
            conversation_history=history[-10:],
        )
        return 200, res

    if action == "import":
        _LOGGER.warning(
            "HomeCave audit: cellar import via mobile app by user %s",
            getattr(user, "id", "unknown"),
        )
        fmt = str(payload.get("format") or payload.get("format_type") or "auto").lower()
        content = payload.get("content") or payload.get("payload") or payload.get("source") or ""
        enrich_ai = bool(payload.get("enrich_ai", True))
        if isinstance(content, str):
            if content.startswith("data:") and ";base64," in content:
                import base64

                content = base64.b64decode(content.split(";base64,", 1)[1])
            elif fmt == "zip" and not content.endswith(".zip") and len(content) > 100:
                import base64

                try:
                    content = base64.b64decode(content)
                except Exception:
                    pass

        if (isinstance(content, bytes) and content.startswith(b"PK")) or (
            isinstance(content, str) and content.endswith(".zip")
        ):
            from .backup import is_vivino_export_zip

            config_dir = Path(getattr(hass.config, "config_dir", "."))
            if is_vivino_export_zip(content, config_dir=config_dir):
                res = await coordinator.async_import_vivino_zip(
                    content, download_images=True, enrich_ai=enrich_ai
                )
                return 200, {**res, "statistics": coordinator.statistics}

        if fmt == "csv" and isinstance(content, str):
            res = await coordinator.async_import_vivino_csv(content, enrich_ai=enrich_ai)
            return 200, {**res, "statistics": coordinator.statistics}

        res = await coordinator.async_import_wines(content, format_type=fmt)
        return 200, {**res, "statistics": coordinator.statistics}

    return 404, {"error": f"Unknown POST action: {_safe_action(action)}"}


class WineInventoryMobileAssetView(HomeAssistantView):
    """Serve standalone Mobile PWA static assets at /homecave and /api/homecave/mobile."""

    url = f"{MOBILE_APP_URL_BASE}/{{filename}}"
    extra_urls = [
        MOBILE_APP_URL_BASE,
        f"{MOBILE_APP_URL_BASE}/",
        "/api/homecave/mobile",
        "/api/homecave/mobile/",
        "/api/homecave/mobile/{filename}",
    ]
    name = "api:homecave:mobile_assets"
    requires_auth = False

    def __init__(self, hass: HomeAssistant | None = None) -> None:
        """Keep a direct hass reference (request.app["hass"] is not reliable)."""
        self.hass = hass

    async def get(
        self, request: web.Request, filename: str | None = None
    ) -> web.StreamResponse:
        """Serve the requested mobile asset or redirect bare paths to index.html."""
        if not filename:
            prefix = (
                "/api/homecave/mobile"
                if request.path.startswith("/api/homecave/mobile")
                else MOBILE_APP_URL_BASE
            )
            qs = f"?{request.query_string}" if request.query_string else ""
            raise web.HTTPFound(f"{prefix}/index.html{qs}")

        clean_name = Path(filename).name
        if clean_name != filename or ".." in filename:
            return web.Response(status=403, text="Forbidden")

        file_path = MOBILE_FRONTEND_DIR / clean_name
        if not file_path.is_file():
            return web.Response(status=404, text="Not Found")

        content_type, charset = CONTENT_TYPES.get(
            file_path.suffix.lower(), ("application/octet-stream", None)
        )
        hass = self.hass
        if hass is not None and hasattr(hass, "async_add_executor_job"):
            body = await hass.async_add_executor_job(file_path.read_bytes)
        else:
            body = file_path.read_bytes()

        headers: dict[str, str] = {
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "same-origin",
            "X-Frame-Options": "SAMEORIGIN",
        }
        if file_path.suffix.lower() == ".html":
            headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "img-src 'self' data: blob: https://images.vivino.com; "
                "style-src 'self' 'unsafe-inline'; "
                "script-src 'self'; "
                "connect-src 'self'; "
                "object-src 'none'; "
                "frame-ancestors 'self'; "
                "base-uri 'self'; "
                "form-action 'self'"
            )
        if clean_name == "sw.js":
            headers["Service-Worker-Allowed"] = "/homecave/"
        return web.Response(
            body=body,
            headers=headers,
            content_type=content_type,
            charset=charset,
        )


# Keep alias for backwards compatibility in imports
WineInventoryMobileRedirectView = WineInventoryMobileAssetView


class WineInventoryPwaApiView(HomeAssistantView):
    """Authenticated REST endpoint for the standalone HomeCave Mobile PWA."""

    url = "/api/homecave/pwa/{action}"
    name = "api:homecave:pwa_api"
    requires_auth = True

    def __init__(self, hass: HomeAssistant | None = None) -> None:
        """Keep a direct hass reference (request.app["hass"] is not reliable)."""
        self.hass = hass

    def _get_hass(self, request: web.Request) -> HomeAssistant:
        """Return hass from the view, falling back to the aiohttp app."""
        if self.hass is not None:
            return self.hass
        try:
            from homeassistant.helpers.http import KEY_HASS  # noqa: PLC0415

            return request.app[KEY_HASS]
        except (ImportError, KeyError):
            return request.app["hass"]

    def _json_no_store(self, data: Any, status_code: int) -> web.Response:
        """Return JSON with Cache-Control: no-store (authenticated, user-specific data)."""
        response = self.json(data, status_code=status_code)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    async def get(self, request: web.Request, action: str) -> web.Response:
        """Handle authenticated GET requests from the mobile PWA."""
        try:
            hass = self._get_hass(request)
            hass_user = request.get("hass_user")
            user_name = getattr(hass_user, "name", None) if hass_user else None
            status, data = await async_handle_pwa_get(
                hass,
                action,
                dict(request.query),
                authenticated_user=user_name,
                user=hass_user,
            )
            return self._json_no_store(data, status)
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("Mobile PWA GET %r failed", _safe_action(action))
            status = 400 if isinstance(err, USER_FACING_ERRORS) else 500
            return self._json_no_store({"error": _public_error_message(err)}, status)

    async def post(self, request: web.Request, action: str) -> web.Response:
        """Handle authenticated POST requests from the mobile PWA."""
        try:
            hass = self._get_hass(request)
            hass_user = request.get("hass_user")
            payload = await request.json() if request.can_read_body else {}
            if not isinstance(payload, dict):
                return self._json_no_store({"error": "JSON object body required."}, 400)
            status, data = await async_handle_pwa_post(
                hass, action, dict(payload or {}), user=hass_user
            )
            return self._json_no_store(data, status)
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("Mobile PWA POST %r failed", _safe_action(action))
            status = 400 if isinstance(err, USER_FACING_ERRORS) else 500
            if isinstance(err, PermissionError):
                status = 403
            return self._json_no_store({"error": _public_error_message(err)}, status)


class WineInventoryDownloadView(HomeAssistantView):
    """Authenticated endpoint for downloading private cellar backups and CSV/JSON exports."""

    url = "/api/homecave/download/{category}/{filename}"
    name = "api:homecave:download"
    requires_auth = True

    def __init__(self, hass: HomeAssistant | None = None) -> None:
        """Keep a direct hass reference."""
        self.hass = hass

    def _get_hass(self, request: web.Request) -> HomeAssistant:
        if self.hass is not None:
            return self.hass
        try:
            from homeassistant.helpers.http import KEY_HASS  # noqa: PLC0415

            return request.app[KEY_HASS]
        except (ImportError, KeyError):
            return request.app["hass"]

    async def get(
        self, request: web.Request, category: str, filename: str
    ) -> web.StreamResponse:
        """Serve a validated backup .zip (admin required) or inventory export (authenticated user)."""
        hass = self._get_hass(request)
        hass_user = request.get("hass_user")
        clean_name = Path(filename).name
        if clean_name != filename or ".." in filename or not clean_name:
            return self.json({"error": "Forbidden"}, status_code=403)

        if category == "backup":
            if hass_user is None or not getattr(hass_user, "is_admin", False):
                return self.json(
                    {"error": "Administrator privileges are required to download backups."},
                    status_code=403,
                )
            allowed = (BACKUPS_SUBDIR,)
        elif category == "export":
            allowed = (EXPORTS_SUBDIR,)
        else:
            return self.json({"error": "Not Found"}, status_code=404)

        config_dir = Path(getattr(hass.config, "config_dir", "."))
        try:
            safe_file = resolve_safe_storage_path(
                config_dir, clean_name, allowed_subdirs=allowed
            )
        except ValueError:
            return self.json({"error": "Forbidden"}, status_code=403)

        if not safe_file.exists() or not safe_file.is_file():
            return self.json({"error": "Not Found"}, status_code=404)

        if hasattr(hass, "async_add_executor_job"):
            body = await hass.async_add_executor_job(safe_file.read_bytes)
        else:
            body = safe_file.read_bytes()

        suffix = safe_file.suffix.lower()
        content_type = (
            "application/zip"
            if suffix == ".zip"
            else "text/csv"
            if suffix == ".csv"
            else "application/json"
            if suffix == ".json"
            else "application/octet-stream"
        )
        return web.Response(
            body=body,
            headers={
                "Content-Disposition": f'attachment; filename="{clean_name}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
            content_type=content_type,
        )


def async_register_http_views(hass: HomeAssistant) -> None:
    """Register HTTP views for the standalone mobile PWA and authenticated downloads."""
    if not hasattr(hass, "http") or hass.http is None:
        return
    hass.http.register_view(WineInventoryMobileAssetView(hass))
    hass.http.register_view(WineInventoryPwaApiView(hass))
    hass.http.register_view(WineInventoryDownloadView(hass))
