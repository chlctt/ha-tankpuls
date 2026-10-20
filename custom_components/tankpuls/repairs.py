"""Repair flows for TankPuls."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import data_entry_flow
from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import TextSelector

from .const import CONF_API_KEY, CONF_STATIONS, DOMAIN, ISSUE_INVALID_KEY, ISSUE_STATION_GONE


class InvalidKeyRepairFlow(RepairsFlow):
    """Replace or remove the rejected API key."""

    def __init__(self, entry_id: str) -> None:
        """Initialize."""
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> data_entry_flow.FlowResult:
        """Show the key form."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Store the new key (empty = anonymous)."""
        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            if entry is not None:
                key = (user_input.get(CONF_API_KEY) or "").strip() or None
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_API_KEY: key}
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({vol.Optional(CONF_API_KEY): TextSelector()}),
        )


class StationGoneRepairFlow(ConfirmRepairFlow):
    """Remove a retired station from the configuration."""

    def __init__(self, entry_id: str, station_id: str) -> None:
        """Initialize."""
        self._entry_id = entry_id
        self._station_id = station_id

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> data_entry_flow.FlowResult:
        """Remove the station and its device."""
        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            if entry is not None:
                stations = [
                    s for s in entry.options.get(CONF_STATIONS, []) if s != self._station_id
                ]
                registry = dr.async_get(self.hass)
                if device := registry.async_get_device(identifiers={(DOMAIN, self._station_id)}):
                    registry.async_remove_device(device.id)
                self.hass.config_entries.async_update_entry(
                    entry, options={**entry.options, CONF_STATIONS: stations}
                )
            return self.async_create_entry(data={})
        return await super().async_step_confirm(user_input)


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None] | None
) -> RepairsFlow:
    """Create the matching fix flow."""
    data = data or {}
    if issue_id.startswith(ISSUE_INVALID_KEY):
        return InvalidKeyRepairFlow(str(data["entry_id"]))
    if issue_id.startswith(ISSUE_STATION_GONE):
        return StationGoneRepairFlow(str(data["entry_id"]), str(data["station_id"]))
    return ConfirmRepairFlow()
