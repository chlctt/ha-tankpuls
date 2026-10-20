"""Tests for setup, sensors and repairs."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tankpuls.const import (
    CONF_API_KEY,
    CONF_STATIONS,
    DOMAIN,
    ISSUE_INVALID_KEY,
    ISSUE_STALE_DATA,
    ISSUE_STATION_GONE,
)

from .conftest import BASE, ID_A, ID_B, hours_ago, mock_api, prices_json, station_json


async def _next_update(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done(wait_background_tasks=True)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_entities(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """Price sensors, status and diagnostic sensor are created."""
    mock_api(aioclient_mock)
    await _setup(hass, entry)

    state = hass.states.get("sensor.esso_tankstelle_super_e10")
    assert state is not None
    assert state.state == "2.049"
    assert state.attributes["unit_of_measurement"] == "EUR"
    assert state.attributes["state_class"] == "measurement"
    assert "device_class" not in state.attributes
    assert state.attributes["brand"] == "Esso"
    assert state.attributes["latitude"] == 53.93  # show_on_map
    assert hass.states.get("sensor.esso_tankstelle_super_e5").state == "2.109"
    assert hass.states.get("sensor.esso_tankstelle_diesel").state == "2.159"
    assert hass.states.get("binary_sensor.esso_tankstelle_open").state == "on"
    assert (
        hass.states.get("binary_sensor.esso_tankstelle_open").attributes["device_class"] == "door"
    )
    assert hass.states.get("sensor.tankpuls_latest_price_report").state != "unknown"


async def test_closed_and_no_prices(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """Status mapping: closed -> off, no_prices -> unknown."""
    mock_api(aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/api/prices/{ID_A}/current", json=prices_json())
    aioclient_mock.get(f"{BASE}/api/stations/{ID_A}", json=station_json(status="closed"))
    await _setup(hass, entry)
    assert hass.states.get("binary_sensor.esso_tankstelle_open").state == "off"


async def test_partial_failure_keeps_other_station(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A failing station becomes unavailable, the other keeps its data."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_API_KEY: None}, options={CONF_STATIONS: [ID_A, ID_B]}
    )
    mock_api(aioclient_mock, (ID_A, ID_B))
    await _setup(hass, entry)
    assert hass.states.get("sensor.esso_tankstelle_super_e10").state == "2.049"

    aioclient_mock.clear_requests()
    mock_api(aioclient_mock, (ID_A,))
    aioclient_mock.get(f"{BASE}/api/prices/{ID_B}/current", status=500)
    await _next_update(hass, freezer)

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    states = {e.entity_id: hass.states.get(e.entity_id).state for e in entities}
    assert any(v == "unavailable" for v in states.values())
    assert any(v == "2.049" for v in states.values())
    assert entry.state is ConfigEntryState.LOADED


async def test_all_fail_first_refresh_retries(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """If everything fails at startup the entry retries later."""
    aioclient_mock.get(f"{BASE}/api/prices/{ID_A}/current", exc=TimeoutError)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_rate_limit_retry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """A 429 leads to a retry instead of a crash."""
    aioclient_mock.get(
        f"{BASE}/api/prices/{ID_A}/current", status=429, headers={"retry-after": "30"}
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_station_gone_issue(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """Empty price list + isActive=false creates a fixable repair issue."""
    aioclient_mock.get(f"{BASE}/api/prices/{ID_A}/current", json={"items": []})
    aioclient_mock.get(
        f"{BASE}/api/stations/{ID_A}",
        json=station_json(is_active=False, status="no_prices", price_cents=None),
    )
    await _setup(hass, entry)
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"{ISSUE_STATION_GONE}_{entry.entry_id}_{ID_A}"
    )
    assert issue is not None
    assert issue.is_fixable


async def test_stale_data_issue(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """No price report for hours during the day creates a warning."""
    freezer.move_to(dt_util.now().replace(hour=13, minute=0))
    mock_api(aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"{BASE}/api/prices/{ID_A}/current", json=prices_json(valid_from=hours_ago(5))
    )
    aioclient_mock.get(f"{BASE}/api/stations/{ID_A}", json=station_json())
    await _setup(hass, entry)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_STALE_DATA}_{entry.entry_id}")


async def test_invalid_key_issue_after_three_cycles(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A rejected key only raises a repair issue after repeated cycles."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: "1|secret"},
        options={CONF_STATIONS: [ID_A]},
    )
    mock_api(aioclient_mock, headers={"x-api-key-status": "invalid_credentials:revoked"})
    await _setup(hass, entry)
    issue_id = f"{ISSUE_INVALID_KEY}_{entry.entry_id}"
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, issue_id) is None
    for _ in range(2):
        await _next_update(hass, freezer)
    assert registry.async_get_issue(DOMAIN, issue_id) is not None

    aioclient_mock.clear_requests()
    mock_api(aioclient_mock, headers={"x-api-key-status": "ok"})
    await _next_update(hass, freezer)
    assert registry.async_get_issue(DOMAIN, issue_id) is None


async def test_deselected_station_device_removed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Removing a station from the options removes its device."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_API_KEY: None}, options={CONF_STATIONS: [ID_A, ID_B]}
    )
    mock_api(aioclient_mock, (ID_A, ID_B))
    await _setup(hass, entry)
    registry = dr.async_get(hass)
    assert registry.async_get_device(identifiers={(DOMAIN, ID_B)})

    hass.config_entries.async_update_entry(entry, options={CONF_STATIONS: [ID_A]})
    await hass.async_block_till_done()
    assert registry.async_get_device(identifiers={(DOMAIN, ID_B)}) is None
    assert registry.async_get_device(identifiers={(DOMAIN, ID_A)})


async def test_unload(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """The entry unloads cleanly."""
    mock_api(aioclient_mock)
    await _setup(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
