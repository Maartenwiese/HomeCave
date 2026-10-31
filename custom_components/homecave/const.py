"""Constants for the HomeCave integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "homecave"
NAME: Final = "HomeCave"
VERSION: Final = "0.9.1"

# Config & Options keys
CONF_CELLAR_NAME: Final = "cellar_name"
CONF_LLM_PROVIDER: Final = "llm_provider"
CONF_GEMINI_API_KEY: Final = "gemini_api_key"
CONF_GEMINI_MODEL: Final = "gemini_model"
CONF_CLAUDE_API_KEY: Final = "claude_api_key"
CONF_CLAUDE_MODEL: Final = "claude_model"
CONF_CURRENCY: Final = "currency"
CONF_VIVINO_COUNTRY_CODE: Final = "vivino_country_code"
CONF_TEMPERATURE_ENTITY: Final = "temperature_entity"
CONF_HUMIDITY_ENTITY: Final = "humidity_entity"
CONF_LOW_STOCK_THRESHOLD: Final = "low_stock_threshold"

# LLM Providers
LLM_PROVIDER_GEMINI: Final = "gemini"
LLM_PROVIDER_CLAUDE: Final = "claude"
LLM_PROVIDERS: Final[list[str]] = [
    LLM_PROVIDER_GEMINI,
    LLM_PROVIDER_CLAUDE,
]

# Defaults
DEFAULT_CELLAR_NAME: Final = "HomeCave"
DEFAULT_LLM_PROVIDER: Final = LLM_PROVIDER_GEMINI
DEFAULT_GEMINI_MODEL: Final = "gemini-3.5-flash-lite"
DEFAULT_CLAUDE_MODEL: Final = "claude-haiku-5-5"
DEFAULT_CURRENCY: Final = "EUR"
DEFAULT_VIVINO_COUNTRY_CODE: Final = "NL"
DEFAULT_LOW_STOCK_THRESHOLD: Final = 2

# API Endpoints
CLAUDE_API_BASE_URL: Final = "https://api.anthropic.com/v1/messages"

# Storage & Static Paths
STORAGE_DIR_NAME: Final = "homecave"
DB_FILENAME: Final = "homecave.db"
COVERS_SUBDIR: Final = "www/homecave/covers"
EXPORTS_SUBDIR: Final = "homecave/exports"
BACKUPS_SUBDIR: Final = "homecave/backups"
LEGACY_EXPORTS_SUBDIR: Final = "www/homecave/exports"
LEGACY_BACKUPS_SUBDIR: Final = "www/homecave/backups"
FRONTEND_URL_BASE: Final = "/homecave_frontend"
MOBILE_APP_URL_BASE: Final = "/homecave"
CARD_JS_FILENAME: Final = "homecave-card.js"
CARD_RESOURCE_URL: Final = f"{FRONTEND_URL_BASE}/{CARD_JS_FILENAME}?v={VERSION}"

# Security & Rate Limits
MAX_BACKUP_ARCHIVE_BYTES: Final = 50 * 1024 * 1024  # 50 MB max ZIP backup/import size
MAX_IMAGE_UPLOAD_BYTES: Final = 10 * 1024 * 1024  # 10 MB max bottle label image size
MAX_GEMINI_CALLS_PER_MINUTE: Final = 10
GEMINI_RATE_WINDOW_SECONDS: Final = 60.0

# Wine types
WINE_TYPE_RED: Final = "red"
WINE_TYPE_WHITE: Final = "white"
WINE_TYPE_ROSE: Final = "rose"
WINE_TYPE_SPARKLING: Final = "sparkling"
WINE_TYPE_DESSERT: Final = "dessert"
WINE_TYPE_FORTIFIED: Final = "fortified"

WINE_TYPES: Final[list[str]] = [
    WINE_TYPE_RED,
    WINE_TYPE_WHITE,
    WINE_TYPE_ROSE,
    WINE_TYPE_SPARKLING,
    WINE_TYPE_DESSERT,
    WINE_TYPE_FORTIFIED,
]

# Drinking window statuses
DRINK_STATUS_HOLD: Final = "hold"
DRINK_STATUS_READY: Final = "ready"
DRINK_STATUS_DRINK_SOON: Final = "drink_soon"
DRINK_STATUS_PAST_PEAK: Final = "past_peak"
DRINK_STATUS_UNKNOWN: Final = "unknown"

# Event types for EventEntity (homeassistant-ai/skills event.received pattern)
EVENT_BOTTLE_ADDED: Final = "bottle_added"
EVENT_BOTTLE_CONSUMED: Final = "bottle_consumed"
EVENT_QUANTITY_ADJUSTED: Final = "quantity_adjusted"
EVENT_WINE_UPDATED: Final = "wine_updated"
EVENT_STOCK_LOW: Final = "stock_low"
EVENT_DRINK_WINDOW_ALERT: Final = "drink_window_alert"

CELLAR_EVENT_TYPES: Final[list[str]] = [
    EVENT_BOTTLE_ADDED,
    EVENT_BOTTLE_CONSUMED,
    EVENT_QUANTITY_ADJUSTED,
    EVENT_WINE_UPDATED,
    EVENT_STOCK_LOW,
    EVENT_DRINK_WINDOW_ALERT,
]

# Service Names
SERVICE_SCAN_BOTTLE: Final = "scan_bottle"
SERVICE_ADD_WINE: Final = "add_wine"
SERVICE_ADJUST_QUANTITY: Final = "adjust_quantity"
SERVICE_UPDATE_WINE: Final = "update_wine"
SERVICE_REFRESH_VIVINO: Final = "refresh_vivino"
SERVICE_EXPORT_INVENTORY: Final = "export_inventory"
SERVICE_CREATE_BACKUP: Final = "create_backup"
SERVICE_RESTORE_BACKUP: Final = "restore_backup"
SERVICE_IMPORT_WINES: Final = "import_wines"
