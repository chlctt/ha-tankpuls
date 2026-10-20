"""Base entity for TankPuls stations."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import StationData, TankpulsCoordinator


class TankpulsStationEntity(CoordinatorEntity[TankpulsCoordinator]):
    """Entity belonging to one station device."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: TankpulsCoordinator, station_id: str) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._station_id = station_id
        station = coordinator.data[station_id].station
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, station_id)},
            name=station.name,
            manufacturer=station.brand or None,
            model="Tankstelle",
        )

    @property
    def station_data(self) -> StationData | None:
        """Return the latest data of the station."""
        return (self.coordinator.data or {}).get(self._station_id)

    @property
    def available(self) -> bool:
        """Return True when the station data is current."""
        data = self.station_data
        return super().available and data is not None and data.available and not data.is_gone
