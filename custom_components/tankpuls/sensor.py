"""Price and diagnostic sensors."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, CONF_SHOW_ON_MAP, DOMAIN, FUELS
from .coordinator import TankpulsConfigEntry, TankpulsCoordinator
from .entity import TankpulsStationEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TankpulsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up price sensors; fuels appearing later are added dynamically."""
    coordinator = entry.runtime_data
    show_on_map = bool(entry.options.get(CONF_SHOW_ON_MAP, False))
    known: set[tuple[str, str]] = set()

    def _add_new() -> None:
        new: list[SensorEntity] = []
        for station_id, item in (coordinator.data or {}).items():
            for fuel in FUELS:
                key = (station_id, fuel)
                if fuel in item.prices and key not in known:
                    known.add(key)
                    new.append(PriceSensor(coordinator, station_id, fuel, show_on_map))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))
    async_add_entities([LatestPriceReportSensor(coordinator, entry.entry_id)])


class PriceSensor(TankpulsStationEntity, SensorEntity):
    """Current price of one fuel at one station."""

    _attr_native_unit_of_measurement = "EUR"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:gas-station"
    _unrecorded_attributes = frozenset(
        {"brand", "station_name", "street", "postcode", "city", "external_id", "fuel_type"}
        | {CONF_LATITUDE, CONF_LONGITUDE}
    )

    def __init__(
        self,
        coordinator: TankpulsCoordinator,
        station_id: str,
        fuel: str,
        show_on_map: bool,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, station_id)
        self._fuel = fuel
        self._show_on_map = show_on_map
        self._attr_unique_id = f"{station_id}_{fuel}"
        self._attr_translation_key = fuel

    @property
    def native_value(self) -> float | None:
        """Return the price in EUR/l."""
        data = self.station_data
        price = data.prices.get(self._fuel) if data else None
        return price.price if price else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return station details."""
        data = self.station_data
        if data is None:
            return None
        station = data.station
        price = data.prices.get(self._fuel)
        attrs: dict[str, Any] = {
            "brand": station.brand,
            "fuel_type": self._fuel,
            "station_name": station.name,
            "street": station.street,
            "postcode": station.postcode,
            "city": station.city,
            "external_id": station.external_id,
            "price_valid_from": price.valid_from.isoformat()
            if price and price.valid_from
            else None,
        }
        if self._show_on_map:
            attrs[CONF_LATITUDE] = station.lat
            attrs[CONF_LONGITUDE] = station.lng
        return attrs


class LatestPriceReportSensor(CoordinatorEntity[TankpulsCoordinator], SensorEntity):
    """Diagnostic: newest price report over all stations (data freshness)."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    _attr_translation_key = "latest_price_report"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: TankpulsCoordinator, entry_id: str) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_latest_price_report"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="TankPuls",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> datetime | None:
        """Return the newest price timestamp."""
        return self.coordinator.latest_price_report
