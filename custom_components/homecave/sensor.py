"""Aggregate sensor entities for the Wine Inventory integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    VERSION,
    WINE_TYPE_DESSERT,
    WINE_TYPE_FORTIFIED,
    WINE_TYPE_RED,
    WINE_TYPE_ROSE,
    WINE_TYPE_SPARKLING,
    WINE_TYPE_WHITE,
)
from .coordinator import WineInventoryCoordinator


@dataclass(frozen=True, kw_only=True)
class WineSensorDescription(SensorEntityDescription):
    """Describes a Wine Inventory summary sensor."""

    value_fn: Callable[[WineInventoryCoordinator], Any]
    attr_fn: Callable[[WineInventoryCoordinator], dict[str, Any]] | None = None


SENSOR_DESCRIPTIONS: tuple[WineSensorDescription, ...] = (
    WineSensorDescription(
        key="total_bottles",
        name="Total Bottles",
        icon="mdi:glass-wine",
        native_unit_of_measurement="bottles",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c.statistics.get("total_bottles", 0),
        attr_fn=lambda c: {
            "unique_wines": c.statistics.get("unique_wines", 0),
            "archived_wines": c.statistics.get("archived_wines", 0),
            "known_purchase_locations": c.statistics.get("known_purchase_locations", []),
            "known_storage_locations": c.statistics.get("known_storage_locations", []),
            "temperature_entity": c.temperature_entity,
            "humidity_entity": c.humidity_entity,
        },
    ),
    WineSensorDescription(
        key="unique_wines",
        name="Unique Wines",
        icon="mdi:glass-wine",
        native_unit_of_measurement="wines",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c.statistics.get("unique_wines", 0),
    ),
    WineSensorDescription(
        key="ready_to_drink",
        name="Ready to Drink",
        icon="mdi:check-decagram",
        native_unit_of_measurement="bottles",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c.statistics.get("ready_to_drink_bottles", 0),
    ),
    WineSensorDescription(
        key="drink_soon",
        name="Drink Soon",
        icon="mdi:clock-alert-outline",
        native_unit_of_measurement="bottles",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c.statistics.get("drink_soon_bottles", 0),
        attr_fn=lambda c: {
            "past_peak_bottles": c.statistics.get("past_peak_bottles", 0),
            "hold_bottles": c.statistics.get("hold_bottles", 0),
        },
    ),
    WineSensorDescription(
        key="estimated_value",
        name="Estimated Value",
        icon="mdi:cash-multiple",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c.statistics.get("estimated_value", 0.0),
    ),
    WineSensorDescription(
        key="red_bottles",
        name="Red Wine Bottles",
        icon="mdi:glass-wine",
        native_unit_of_measurement="bottles",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c.statistics.get("by_type", {}).get(WINE_TYPE_RED, 0),
    ),
    WineSensorDescription(
        key="white_bottles",
        name="White Wine Bottles",
        icon="mdi:bottle-wine-outline",
        native_unit_of_measurement="bottles",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c.statistics.get("by_type", {}).get(WINE_TYPE_WHITE, 0),
    ),
    WineSensorDescription(
        key="rose_bottles",
        name="Rosé Wine Bottles",
        icon="mdi:glass-wine",
        native_unit_of_measurement="bottles",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c.statistics.get("by_type", {}).get(WINE_TYPE_ROSE, 0),
    ),
    WineSensorDescription(
        key="sparkling_bottles",
        name="Sparkling Wine Bottles",
        icon="mdi:glass-flute",
        native_unit_of_measurement="bottles",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c.statistics.get("by_type", {}).get(WINE_TYPE_SPARKLING, 0),
    ),
    WineSensorDescription(
        key="dessert_fortified_bottles",
        name="Dessert & Fortified Bottles",
        icon="mdi:glass-cocktail",
        native_unit_of_measurement="bottles",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: (
            c.statistics.get("by_type", {}).get(WINE_TYPE_DESSERT, 0)
            + c.statistics.get("by_type", {}).get(WINE_TYPE_FORTIFIED, 0)
        ),
    ),
    WineSensorDescription(
        key="keep_bottles",
        name="Keep Wine Bottles",
        icon="mdi:archive-clock-outline",
        native_unit_of_measurement="bottles",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c.statistics.get("keep_bottles", c.statistics.get("hold_bottles", 0)),
    ),
    WineSensorDescription(
        key="past_peak_bottles",
        name="Past Peak Wine Bottles",
        icon="mdi:alert-outline",
        native_unit_of_measurement="bottles",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c.statistics.get("past_peak_bottles", 0),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Wine Inventory sensors from a config entry."""
    coordinator: WineInventoryCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities = [
        WineInventorySensor(coordinator, description)
        for description in SENSOR_DESCRIPTIONS
    ]
    async_add_entities(entities)


class WineInventorySensor(SensorEntity):
    """Representation of a Wine Inventory aggregate sensor."""

    _attr_has_entity_name = True
    entity_description: WineSensorDescription

    def __init__(
        self,
        coordinator: WineInventoryCoordinator,
        description: WineSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        self.coordinator = coordinator
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry_id)},
            name=coordinator.cellar_name,
            manufacturer="HomeCave",
            model="Local SQLite Cellar & AI Scanner",
            sw_version=VERSION,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return unit of measurement, using dynamic currency for estimated_value."""
        if self.entity_description.key == "estimated_value":
            return self.coordinator.currency
        return self.entity_description.native_unit_of_measurement

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return optional extra state attributes."""
        if self.entity_description.attr_fn is not None:
            return self.entity_description.attr_fn(self.coordinator)
        return None

    async def async_added_to_hass(self) -> None:
        """Register state update callback when entity is added to Home Assistant."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
