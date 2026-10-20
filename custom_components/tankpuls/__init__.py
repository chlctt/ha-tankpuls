"""The TankPuls integration."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.loader import async_get_integration

from .api import TankpulsClient
from .const import CONF_API_KEY, CONF_STATIONS, DOMAIN
from .coordinator import TankpulsConfigEntry, TankpulsCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: TankpulsConfigEntry) -> bool:
    """Set up TankPuls from a config entry."""
    integration = await async_get_integration(hass, DOMAIN)
    client = TankpulsClient(
        async_get_clientsession(hass),
        entry.data.get(CONF_API_KEY),
        version=str(integration.version or "0"),
    )
    coordinator = TankpulsCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    _remove_stale_devices(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TankpulsConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: TankpulsConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: TankpulsConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow removing devices of stations that are no longer configured."""
    configured = set(entry.options.get(CONF_STATIONS, []))
    return not any(domain == DOMAIN and ident in configured for domain, ident in device.identifiers)


def _remove_stale_devices(hass: HomeAssistant, entry: TankpulsConfigEntry) -> None:
    """Remove devices of stations that were deselected."""
    configured = set(entry.options.get(CONF_STATIONS, []))
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        idents = {i for d, i in device.identifiers if d == DOMAIN}
        if idents and not idents & (configured | {entry.entry_id}):
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)
