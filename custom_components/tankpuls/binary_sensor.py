"""Station open/closed binary sensor."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import TankpulsConfigEntry, TankpulsCoordinator
from .entity import TankpulsStationEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TankpulsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one status sensor per station; stations appearing later are added dynamically."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    def _add_new() -> None:
        new_ids = [sid for sid in (coordinator.data or {}) if sid not in known]
        if new_ids:
            known.update(new_ids)
            async_add_entities(StationOpenSensor(coordinator, sid) for sid in new_ids)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class StationOpenSensor(TankpulsStationEntity, BinarySensorEntity):
    """On while the station is open."""

    _attr_device_class = BinarySensorDeviceClass.DOOR
    _attr_translation_key = "status"

    def __init__(self, coordinator: TankpulsCoordinator, station_id: str) -> None:
        """Initialize."""
        super().__init__(coordinator, station_id)
        self._attr_unique_id = f"{station_id}_status"

    @property
    def is_on(self) -> bool | None:
        """Return True if open, None when no prices are reported."""
        data = self.station_data
        if data is None:
            return None
        return {"open": True, "closed": False}.get(data.station.status)
