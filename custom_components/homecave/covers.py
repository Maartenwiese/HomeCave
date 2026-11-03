"""Private bottle/cover image storage, migration, and signed-URL helpers for HomeCave.

Cover photos (label scans, uploaded bottle photos, Vivino label images, restored covers) are
stored in the private ``<config>/homecave/covers/`` folder, which Home Assistant never serves
publicly. They are delivered only through the authenticated ``/api/homecave/cover/<file>`` view.

The database stores the *canonical, unsigned* path ``/api/homecave/cover/<file>``. Signed URLs
(``?authSig=...`` from Home Assistant's ``async_sign_path``) are added only at the response
boundary (WebSocket results and mobile-app JSON), so ``<img>`` tags can load covers without an
Authorization header. Signatures are cached per (refresh token, path) so URLs stay stable and
browser-cacheable for half of their lifetime.
"""

from __future__ import annotations

from datetime import timedelta
import logging
from pathlib import Path
import re
import shutil
import time
from typing import Any, Final

from .const import (
    COVER_URL_TTL_SECONDS,
    COVERS_SUBDIR,
    DOMAIN,
    LEGACY_PUBLIC_COVERS_SUBDIR,
)

_LOGGER = logging.getLogger(__name__)

COVER_URL_PREFIX: Final = "/api/homecave/cover/"
COVER_NAME_PATTERN: Final = r"[A-Za-z0-9_-]{1,80}\.(?:jpg|jpeg|png|webp)"
_COVER_NAME_RE = re.compile(rf"^{COVER_NAME_PATTERN}$")
LEGACY_PUBLIC_PREFIXES: Final = ("/local/homecave/covers/", "/local/wine_inventory/covers/")
LEGACY_WINE_INVENTORY_PUBLIC_SUBDIR: Final = "www/wine_inventory/covers"
MAX_SIGNATURE_CACHE_ENTRIES: Final = 5000
_SIGNER_DATA_KEY: Final = "_cover_url_signer"


def is_valid_cover_name(filename: str) -> bool:
    """Return True for a plain cover filename (no paths, allowed raster extension)."""
    return bool(filename) and bool(_COVER_NAME_RE.match(filename))


def cover_url(filename: str) -> str:
    """Return the canonical unsigned URL path for a private cover file."""
    return f"{COVER_URL_PREFIX}{filename}"


def normalize_cover_ref(value: Any) -> str | None:
    """Map any cover reference to the canonical unsigned ``/api/homecave/cover/<file>`` path.

    - Strips query strings (e.g. an ``?authSig=`` echoed back by the client).
    - Maps legacy public ``/local/homecave/covers/X`` and ``/local/wine_inventory/covers/X``.
    Returns ``None`` when the value is not a cover reference, or ``""`` when it looks like a
    cover reference but the filename is invalid (so callers can reject it).
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    path_part = raw.split("?", 1)[0].split("#", 1)[0]
    for prefix in (COVER_URL_PREFIX, *LEGACY_PUBLIC_PREFIXES):
        if path_part.startswith(prefix):
            name = path_part[len(prefix):]
            return cover_url(name) if is_valid_cover_name(name) else ""
    return None


def private_covers_dir(config_dir: str | Path) -> Path:
    """Return the private covers directory (<config>/homecave/covers)."""
    return Path(config_dir) / COVERS_SUBDIR


def private_cover_path(config_dir: str | Path, filename: str) -> Path | None:
    """Resolve a cover filename strictly inside the private covers directory."""
    if not is_valid_cover_name(filename):
        return None
    covers_dir = private_covers_dir(config_dir).resolve()
    candidate = (covers_dir / filename).resolve()
    if candidate.parent != covers_dir:
        return None
    return candidate


def read_private_cover(config_dir: str | Path, filename: str) -> bytes | None:
    """Executor helper: validate, check, and read a private cover in a single blocking job."""
    path = private_cover_path(config_dir, filename)
    if path is None or path.is_symlink() or not path.is_file():
        return None
    return path.read_bytes()


def delete_private_cover(config_dir: str | Path, cover_ref: str) -> bool:
    """Executor helper: delete a private cover referenced by URL or filename (if it exists)."""
    canonical = normalize_cover_ref(cover_ref)
    name = canonical[len(COVER_URL_PREFIX):] if canonical else Path(str(cover_ref or "")).name
    path = private_cover_path(config_dir, name)
    if path is None:
        return False
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError as err:
        _LOGGER.debug("Could not delete cover %s: %s", name, err)
        return False


def migrate_public_covers(config_dir: str | Path) -> int:
    """Move cover images out of public ``<config>/www/...`` folders into private storage.

    Returns the number of files moved. Public source folders are removed when empty.
    """
    config_path = Path(config_dir)
    target_dir = private_covers_dir(config_path)
    moved = 0
    for public_subdir in (LEGACY_PUBLIC_COVERS_SUBDIR, LEGACY_WINE_INVENTORY_PUBLIC_SUBDIR):
        public_dir = config_path / public_subdir
        try:
            if not public_dir.is_dir() or public_dir.is_symlink():
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            for item in list(public_dir.iterdir()):
                if item.is_symlink() or not item.is_file():
                    continue
                dest = target_dir / item.name
                if dest.exists():
                    item.unlink()
                else:
                    shutil.move(str(item), str(dest))
                    moved += 1
            if not any(public_dir.iterdir()):
                public_dir.rmdir()
        except OSError as err:
            _LOGGER.warning("Could not move covers out of public folder %s: %s", public_dir, err)
    return moved


class CoverUrlSigner:
    """Cache Home Assistant signed paths per (refresh_token_id, path).

    ``async_sign_path`` embeds the issue time, so signing on every response would change every
    image URL and defeat browser caching. A signature is reused while at least half of its
    lifetime remains, so URLs stay identical (and cacheable) for ``ttl / 2``.
    """

    def __init__(self, hass: Any, ttl_seconds: int = COVER_URL_TTL_SECONDS) -> None:
        self.hass = hass
        self.ttl_seconds = int(ttl_seconds)
        self._cache: dict[tuple[str, str], tuple[str, float]] = {}

    def _sign(self, path: str, refresh_token_id: str) -> str | None:
        try:
            from homeassistant.components.http.auth import async_sign_path
        except ImportError:  # pragma: no cover - only outside Home Assistant (unit tests)
            return None
        return async_sign_path(
            self.hass,
            path,
            timedelta(seconds=self.ttl_seconds),
            refresh_token_id=refresh_token_id,
        )

    def sign(self, path: str, refresh_token_id: str | None) -> str:
        """Return a cached or fresh signed URL; the unsigned path when signing is unavailable."""
        if not refresh_token_id:
            return path
        now = time.time()
        key = (refresh_token_id, path)
        cached = self._cache.get(key)
        if cached and cached[1] - now > self.ttl_seconds / 2:
            return cached[0]
        try:
            signed = self._sign(path, refresh_token_id)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not sign cover path %s: %s", path, err)
            signed = None
        if not signed:
            return path
        if len(self._cache) >= MAX_SIGNATURE_CACHE_ENTRIES:
            self._prune(now)
        self._cache[key] = (signed, now + self.ttl_seconds)
        return signed

    def _prune(self, now: float) -> None:
        expired = [k for k, (_, exp) in self._cache.items() if exp - now <= self.ttl_seconds / 2]
        for k in expired:
            self._cache.pop(k, None)
        while len(self._cache) >= MAX_SIGNATURE_CACHE_ENTRIES:
            self._cache.pop(next(iter(self._cache)))


def get_cover_signer(hass: Any) -> CoverUrlSigner | None:
    """Return the shared per-Home-Assistant signer (stored in hass.data[DOMAIN])."""
    data = getattr(hass, "data", None)
    if not isinstance(data, dict):
        return None
    domain_data = data.setdefault(DOMAIN, {})
    signer = domain_data.get(_SIGNER_DATA_KEY)
    if not isinstance(signer, CoverUrlSigner):
        signer = CoverUrlSigner(hass)
        domain_data[_SIGNER_DATA_KEY] = signer
    return signer


def sign_cover_urls(
    payload: Any,
    signer: CoverUrlSigner | None,
    refresh_token_id: str | None,
) -> Any:
    """Return a copy of ``payload`` where every private cover ``image_url`` is signed.

    Only dict values under the key ``image_url`` that start with ``/api/homecave/cover/`` are
    rewritten. Containers are rebuilt (never mutated), so cached coordinator data stays clean.
    """
    if signer is None or not refresh_token_id:
        return payload

    def _walk(value: Any) -> Any:
        if isinstance(value, dict):
            out: dict[Any, Any] = {}
            for key, item in value.items():
                if (
                    key == "image_url"
                    and isinstance(item, str)
                    and item.startswith(COVER_URL_PREFIX)
                    and "?" not in item
                ):
                    out[key] = signer.sign(item, refresh_token_id)
                else:
                    out[key] = _walk(item)
            return out
        if isinstance(value, list):
            return [_walk(item) for item in value]
        if isinstance(value, tuple):
            return tuple(_walk(item) for item in value)
        return value

    return _walk(payload)
