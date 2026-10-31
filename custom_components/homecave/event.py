"""Event entity for Wine Inventory activity (follows homeassistant-ai/skills event.received pattern)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CELLAR_EVENT_TYPES, DOMAIN, VERSION
from .coordinator import WineInventoryCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Wine Inventory activity EventEntity."""
    coordinator: WineInventoryCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([WineInventoryActivityEventEntity(coordinator)])


class WineInventoryActivityEventEntity(EventEntity):
    """Event entity emitting bottle_added, bottle_consumed, stock_low, and drink_window_alert."""

    _attr_has_entity_name = True
    _attr_name = "Activity"
    _attr_icon = "mdi:glass-wine"
    _attr_event_types = CELLAR_EVENT_TYPES

    def __init__(self, coordinator: WineInventoryCoordinator) -> None:
        """Initialize the cellar activity event entity."""
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.entry_id}_activity"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry_id)},
            name=coordinator.cellar_name,
            manufacturer="HomeCave",
            model="Local SQLite Cellar & AI Scanner",
            sw_version=VERSION,
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator events when added to Home Assistant."""
        self.async_on_remove(
            self.coordinator.async_add_event_listener(self._handle_cellar_event)
        )

    @callback
    def _handle_cellar_event(self, event_type: str, event_data: dict[str, Any]) -> None:
        """Trigger the native Home Assistant EventEntity state update."""
        self._trigger_event(event_type, event_data)
        self.async_write_ha_state()
