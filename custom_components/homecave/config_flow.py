"""Config flow and Options flow for the HomeCave integration."""

from __future__ import annotations

import logging
from typing import Any

try:
    import voluptuous as vol
except ImportError:  # pragma: no cover - fallback for standalone unit tests
    class _StubVol:
        class Schema:
            def __init__(self, schema: Any) -> None:
                self.schema = schema

        @staticmethod
        def Required(key: Any, **kwargs: Any) -> Any:
            return key

        @staticmethod
        def Optional(key: Any, **kwargs: Any) -> Any:
            return key

        @staticmethod
        def All(*args: Any) -> Any:
            return args

        @staticmethod
        def Coerce(typ: Any) -> Any:
            return typ

        @staticmethod
        def Range(**kwargs: Any) -> Any:
            return kwargs

        @staticmethod
        def In(values: Any) -> Any:
            return values

    vol = _StubVol()  # type: ignore[assignment]

try:
    from homeassistant import config_entries
    from homeassistant.core import callback
    from homeassistant.helpers.selector import (
        TextSelector,
        TextSelectorConfig,
        TextSelectorType,
    )
except ImportError:  # pragma: no cover - fallback for standalone unit tests
    class _StubConfigFlow:
        def __init_subclass__(cls, **kwargs: Any) -> None:
            super().__init_subclass__()

        def async_show_form(self, **kwargs: Any) -> dict[str, Any]:
            return {"type": "form", **kwargs}

        def async_create_entry(self, **kwargs: Any) -> dict[str, Any]:
            return {"type": "create_entry", **kwargs}

    class _StubOptionsFlow(_StubConfigFlow):
        def async_show_menu(self, **kwargs: Any) -> dict[str, Any]:
            return {"type": "menu", **kwargs}

    class _StubConfigEntries:
        ConfigFlow = _StubConfigFlow
        OptionsFlow = _StubOptionsFlow
        ConfigEntry = Any
        FlowResult = dict[str, Any]

    config_entries = _StubConfigEntries()  # type: ignore[assignment]

    def callback(func: Any) -> Any:  # type: ignore[no-redef]
        return func

    TextSelector = None  # type: ignore[assignment,misc]
    TextSelectorConfig = None  # type: ignore[assignment,misc]
    TextSelectorType = None  # type: ignore[assignment,misc]

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
    DEFAULT_CELLAR_NAME,
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_CURRENCY,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_LOW_STOCK_THRESHOLD,
    DEFAULT_VIVINO_COUNTRY_CODE,
    DOMAIN,
    LLM_PROVIDER_CLAUDE,
    LLM_PROVIDER_GEMINI,
)

_LOGGER = logging.getLogger(__name__)


def _build_schema(defaults: dict[str, Any], include_cellar_name: bool = True) -> vol.Schema:
    """Build Voluptuous schema for Config Flow and Options Flow."""
    fields: dict[Any, Any] = {}
    if include_cellar_name:
        fields[
            vol.Required(
                CONF_CELLAR_NAME,
                default=str(defaults.get(CONF_CELLAR_NAME) or DEFAULT_CELLAR_NAME),
            )
        ] = str

    api_key_validator: Any = (
        TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
        if TextSelector is not None and TextSelectorConfig is not None and TextSelectorType is not None
        else str
    )

    provider_default = str(defaults.get(CONF_LLM_PROVIDER) or DEFAULT_LLM_PROVIDER)
    if provider_default not in (LLM_PROVIDER_GEMINI, LLM_PROVIDER_CLAUDE):
        provider_default = DEFAULT_LLM_PROVIDER

    raw_threshold = defaults.get(CONF_LOW_STOCK_THRESHOLD)
    default_threshold = (
        int(raw_threshold)
        if raw_threshold is not None and str(raw_threshold).strip() != ""
        else DEFAULT_LOW_STOCK_THRESHOLD
    )

    fields.update(
        {
            vol.Required(
                CONF_LLM_PROVIDER,
                default=provider_default,
            ): vol.In(
                {
                    LLM_PROVIDER_GEMINI: "Google Gemini",
                    LLM_PROVIDER_CLAUDE: "Anthropic Claude",
                }
            ),
            vol.Optional(
                CONF_GEMINI_API_KEY,
                default=str(defaults.get(CONF_GEMINI_API_KEY) or ""),
            ): api_key_validator,
            vol.Required(
                CONF_GEMINI_MODEL,
                default=str(defaults.get(CONF_GEMINI_MODEL) or DEFAULT_GEMINI_MODEL),
            ): str,
            vol.Optional(
                CONF_CLAUDE_API_KEY,
                default=str(defaults.get(CONF_CLAUDE_API_KEY) or ""),
            ): api_key_validator,
            vol.Required(
                CONF_CLAUDE_MODEL,
                default=str(defaults.get(CONF_CLAUDE_MODEL) or DEFAULT_CLAUDE_MODEL),
            ): str,
            vol.Required(
                CONF_CURRENCY,
                default=str(defaults.get(CONF_CURRENCY) or DEFAULT_CURRENCY),
            ): str,
            vol.Required(
                CONF_VIVINO_COUNTRY_CODE,
                default=str(
                    defaults.get(CONF_VIVINO_COUNTRY_CODE)
                    or DEFAULT_VIVINO_COUNTRY_CODE
                ),
            ): str,
            vol.Required(
                CONF_LOW_STOCK_THRESHOLD,
                default=default_threshold,
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=50)),
            vol.Optional(
                CONF_TEMPERATURE_ENTITY,
                default=str(defaults.get(CONF_TEMPERATURE_ENTITY) or ""),
            ): str,
            vol.Optional(
                CONF_HUMIDITY_ENTITY,
                default=str(defaults.get(CONF_HUMIDITY_ENTITY) or ""),
            ): str,
        }
    )
    return vol.Schema(fields)


class WineInventoryConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a UI config flow for HomeCave."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Handle the initial step configured by the user in Settings > Integrations."""
        errors: dict[str, str] = {}
        if user_input is not None:
            provider = str(user_input.get(CONF_LLM_PROVIDER) or LLM_PROVIDER_GEMINI)
            gemini_key = str(user_input.get(CONF_GEMINI_API_KEY) or "").strip()
            claude_key = str(user_input.get(CONF_CLAUDE_API_KEY) or "").strip()

            if provider == LLM_PROVIDER_GEMINI and not gemini_key:
                errors["base"] = "missing_gemini_api_key"
            elif provider == LLM_PROVIDER_CLAUDE and not claude_key:
                errors["base"] = "missing_claude_api_key"
            else:
                cellar_name = user_input.get(CONF_CELLAR_NAME, DEFAULT_CELLAR_NAME).strip()
                await self.async_set_unique_id(cellar_name.lower().replace(" ", "_"))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=cellar_name, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema(user_input or {}, include_cellar_name=True),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return WineInventoryOptionsFlowHandler(config_entry)


def _get_coordinator_for_entry(hass: Any, entry_id: str) -> Any:
    """Get coordinator from hass.data."""
    if not hasattr(hass, "data"):
        return None
    domain_data = hass.data.get(DOMAIN, {})
    entry_data = domain_data.get(entry_id)
    if isinstance(entry_data, dict) and "coordinator" in entry_data:
        return entry_data["coordinator"]
    for val in domain_data.values():
        if isinstance(val, dict) and "coordinator" in val:
            return val["coordinator"]
    return None


class WineInventoryOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle HomeCave options updates, backups, restores, and imports."""

    def __init__(self, config_entry: config_entries.ConfigEntry | None = None) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    @property
    def _entry(self) -> Any:
        """Return active config entry across HA versions."""
        return getattr(self, "_config_entry", None) or getattr(
            self, "config_entry", None
        )

    def _entry_options_dict(self) -> dict[str, Any]:
        """Return a mutable copy of the current config entry options."""
        entry = self._entry
        raw_opts = getattr(entry, "options", None) if entry is not None else None
        return dict(raw_opts) if raw_opts else {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Manage the HomeCave options menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "settings",
                "backup",
                "download_backup",
                "restore",
                "import_wines",
                "upload_vivino",
            ],
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Manage general cellar settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            provider = str(user_input.get(CONF_LLM_PROVIDER) or LLM_PROVIDER_GEMINI)
            gemini_key = str(user_input.get(CONF_GEMINI_API_KEY) or "").strip()
            claude_key = str(user_input.get(CONF_CLAUDE_API_KEY) or "").strip()

            if provider == LLM_PROVIDER_GEMINI and not gemini_key:
                errors["base"] = "missing_gemini_api_key"
            elif provider == LLM_PROVIDER_CLAUDE and not claude_key:
                errors["base"] = "missing_claude_api_key"
            else:
                return self.async_create_entry(title="", data=dict(user_input))

        entry = self._entry
        data_dict = dict(getattr(entry, "data", None) or {})
        merged = {**data_dict, **self._entry_options_dict(), **(user_input or {})}
        return self.async_show_form(
            step_id="settings",
            data_schema=_build_schema(merged, include_cellar_name=False),
            errors=errors,
        )

    async def async_step_backup(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Create a full database, metadata, and images .zip backup."""
        errors: dict[str, str] = {}
        if user_input is not None:
            entry = self._entry
            entry_id = getattr(entry, "entry_id", "") if entry is not None else ""
            coordinator = _get_coordinator_for_entry(self.hass, entry_id)
            if coordinator:
                try:
                    res = await coordinator.async_create_backup()
                    _LOGGER.info(
                        "Created backup %s with %s wines",
                        res.get("filename"),
                        res.get("wines_count"),
                    )
                    return self.async_create_entry(
                        title="", data=self._entry_options_dict()
                    )
                except Exception as err:  # noqa: BLE001
                    _LOGGER.exception("Backup failed: %s", err)
                    errors["base"] = "backup_failed"
            else:
                _LOGGER.error(
                    "Backup failed: no active coordinator found for entry %s",
                    entry_id,
                )
                errors["base"] = "backup_failed"

        return self.async_show_form(
            step_id="backup",
            data_schema=vol.Schema({}),
            errors=errors,
        )

    async def async_step_download_backup(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Download an existing cellar backup .zip archive."""
        errors: dict[str, str] = {}
        entry = self._entry
        entry_id = getattr(entry, "entry_id", "") if entry is not None else ""
        coordinator = _get_coordinator_for_entry(self.hass, entry_id)
        available_backups = (
            await coordinator.async_list_backups() if coordinator else []
        )
        backup_filenames = [b["filename"] for b in available_backups]

        if not backup_filenames:
            return self.async_show_form(
                step_id="download_backup",
                data_schema=vol.Schema({}),
                errors={"base": "no_backups_available"},
            )

        if user_input is not None:
            chosen = user_input.get("backup_file")
            if chosen and coordinator:
                try:
                    download_url = await coordinator.async_stage_backup_for_download(chosen)
                    return self.async_show_form(
                        step_id="download_backup",
                        data_schema=vol.Schema(
                            {
                                vol.Optional("backup_file", default=chosen): vol.In(
                                    backup_filenames
                                )
                            }
                        ),
                        description_placeholders={
                            "filename": chosen,
                            "download_url": download_url,
                        },
                    )
                except Exception as err:  # noqa: BLE001
                    _LOGGER.exception("Staging backup for download failed: %s", err)
                    errors["base"] = "download_staging_failed"

        schema_fields = {
            vol.Required("backup_file", default=backup_filenames[0]): vol.In(
                backup_filenames
            )
        }
        return self.async_show_form(
            step_id="download_backup",
            data_schema=vol.Schema(schema_fields),
            errors=errors,
        )

    async def async_step_upload_vivino(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Upload and import a Vivino ZIP export or CSV history."""
        errors: dict[str, str] = {}
        entry = self._entry
        entry_id = getattr(entry, "entry_id", "") if entry is not None else ""
        coordinator = _get_coordinator_for_entry(self.hass, entry_id)

        if user_input is not None:
            if not coordinator:
                errors["base"] = "import_failed"
            else:
                csv_content = (user_input.get("csv_content") or "").strip()
                file_path = (user_input.get("file_path") or user_input.get("csv_file_path") or "").strip()
                if file_path:
                    try:
                        config_dir = Path(getattr(self.hass.config, "config_dir", "."))
                        safe_path = config_dir / file_path
                        if not safe_path.is_file():
                            safe_path = Path(file_path)
                        if safe_path.is_file():
                            if safe_path.suffix.lower() == ".zip":
                                res = await coordinator.async_import_vivino_zip(safe_path)
                                _LOGGER.info(
                                    "Imported Vivino ZIP %s: %s",
                                    file_path,
                                    res,
                                )
                                return self.async_create_entry(
                                    title="", data=self._entry_options_dict()
                                )
                            csv_content = safe_path.read_text(encoding="utf-8")
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.warning("Could not read file_path %s: %s", file_path, err)
                if csv_content:
                    try:
                        res = await coordinator.async_import_vivino_csv(csv_content)
                        _LOGGER.info(
                            "Imported %s wines from Vivino CSV (%s cellar, %s tasted)",
                            res.get("imported_count"),
                            res.get("cellar_count"),
                            res.get("tasted_count"),
                        )
                        return self.async_create_entry(
                            title="", data=self._entry_options_dict()
                        )
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.exception("Vivino import failed: %s", err)
                        errors["base"] = "vivino_import_failed"
                else:
                    if not errors:
                        errors["base"] = "no_csv_provided"

        schema = vol.Schema(
            {
                vol.Optional("file_path", default=""): str,
                vol.Optional("csv_content", default=""): str,
            }
        )
        return self.async_show_form(
            step_id="upload_vivino",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_restore(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Restore database from an existing or custom .zip backup."""
        errors: dict[str, str] = {}
        entry = self._entry
        entry_id = getattr(entry, "entry_id", "") if entry is not None else ""
        coordinator = _get_coordinator_for_entry(self.hass, entry_id)
        try:
            available_backups = (
                await coordinator.async_list_backups() if coordinator else []
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not list available backups: %s", err)
            available_backups = []
        backup_filenames = [b["filename"] for b in available_backups]

        if user_input is not None:
            if not coordinator:
                errors["base"] = "restore_failed"
            else:
                chosen = user_input.get("backup_file")
                custom_path = (user_input.get("custom_path") or "").strip()
                target_path = custom_path or chosen
                if target_path:
                    try:
                        await coordinator.async_restore_backup(target_path)
                        return self.async_create_entry(
                            title="", data=self._entry_options_dict()
                        )
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.exception("Restore failed: %s", err)
                        errors["base"] = "restore_failed"
                else:
                    errors["base"] = "no_backup_selected"

        schema_fields: dict[Any, Any] = {}
        if backup_filenames:
            schema_fields[
                vol.Optional("backup_file", default=backup_filenames[0])
            ] = vol.In(backup_filenames)
        schema_fields[vol.Optional("custom_path", default="")] = str

        return self.async_show_form(
            step_id="restore",
            data_schema=vol.Schema(schema_fields),
            errors=errors,
        )

    async def async_step_import_wines(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Import wines from file path or JSON text."""
        errors: dict[str, str] = {}
        entry = self._entry
        entry_id = getattr(entry, "entry_id", "") if entry is not None else ""
        coordinator = _get_coordinator_for_entry(self.hass, entry_id)

        if user_input is not None:
            if not coordinator:
                errors["base"] = "import_failed"
            else:
                source = (user_input.get("import_source") or "").strip()
                if source:
                    try:
                        await coordinator.async_import_wines(source)
                        return self.async_create_entry(
                            title="", data=self._entry_options_dict()
                        )
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.exception("Import failed: %s", err)
                        errors["base"] = "import_failed"
                else:
                    errors["base"] = "no_import_source"

        return self.async_show_form(
            step_id="import_wines",
            data_schema=vol.Schema({vol.Required("import_source"): str}),
            errors=errors,
        )
