"""Tests for the config, options and reconfigure flows."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tankpuls.const import (
    CONF_API_KEY,
    CONF_CHANGE_STATIONS,
    CONF_LOCATION,
    CONF_POSTALCODE,
    CONF_SCAN_INTERVAL,
    CONF_SHOW_ON_MAP,
    CONF_STATIONS,
    DOMAIN,
)

from .conftest import BASE, ID_A, ID_B, mock_api, station_json

LOCATION = {"latitude": 53.93, "longitude": 9.53, "radius": 2000}


def _search(aioclient_mock: AiohttpClientMocker, items: list[dict]) -> None:
    aioclient_mock.get(f"{BASE}/api/stations/search", json={"count": len(items), "items": items})


async def test_full_flow_anonymous(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Setup without key, search by location, pick a station."""
    mock_api(aioclient_mock)
    _search(aioclient_mock, [station_json(ID_A), station_json(ID_B, name="Aral", distance_m=1500)])

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "location"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION: LOCATION}
    )
    assert result["step_id"] == "stations"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_STATIONS: [ID_A]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_API_KEY: None}
    assert result["options"][CONF_STATIONS] == [ID_A]
    search = next(c for c in aioclient_mock.mock_calls if c[1].path == "/api/stations/search")
    query = search[1].query
    assert query["rad"] == "2"
    assert query["fuel"] == "e10"


async def test_invalid_key(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A key the API silently downgrades to anonymous is rejected."""
    mock_api(aioclient_mock, headers={"x-api-key-status": "invalid_credentials:unknown"})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "1|abc"}
    )
    assert result["errors"] == {"base": "invalid_key"}


async def test_valid_key(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A valid key is accepted."""
    mock_api(aioclient_mock, headers={"x-api-key-status": "ok"})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: " 1|abc "}
    )
    assert result["step_id"] == "location"


async def test_cannot_connect(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Connection problems are reported."""
    aioclient_mock.get(f"{BASE}/health", exc=TimeoutError)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "cannot_connect"}


async def test_no_stations_and_unknown_postalcode(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Empty results and unknown postal codes stay on the form."""
    mock_api(aioclient_mock)
    _search(aioclient_mock, [])
    aioclient_mock.get(
        f"{BASE}/api/stations/postalcode", status=404, json={"error": "postalcode_unknown"}
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION: LOCATION}
    )
    assert result["errors"] == {"base": "no_stations"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION: LOCATION, CONF_POSTALCODE: "00000"}
    )
    assert result["errors"] == {"base": "postalcode_unknown"}


async def test_single_instance(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Only one entry is allowed."""
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_keep_selected_station(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """Re-searching keeps the existing selection selectable and preselected."""
    mock_api(aioclient_mock)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    aioclient_mock.clear_requests()
    _search(aioclient_mock, [station_json(ID_B, name="Aral")])

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SCAN_INTERVAL: 15, CONF_SHOW_ON_MAP: False, CONF_CHANGE_STATIONS: True},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_LOCATION: LOCATION}
    )
    assert result["step_id"] == "stations"
    values = {o["value"] for o in result["data_schema"].schema[CONF_STATIONS].config["options"]}
    assert values == {ID_A, ID_B}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_STATIONS: [ID_A, ID_B]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_STATIONS] == [ID_A, ID_B]
    assert result["data"][CONF_SCAN_INTERVAL] == 15


async def test_reconfigure_key(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """The key can be changed."""
    mock_api(aioclient_mock, headers={"x-api-key-status": "ok"})
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "2|def"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_API_KEY] == "2|def"
