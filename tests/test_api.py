"""Tests for the API client."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tankpuls.api import (
    TankpulsApiError,
    TankpulsClient,
    TankpulsConnectionError,
    TankpulsNotFoundError,
    TankpulsRateLimitError,
    cents_to_eur,
    parse_timestamp,
)

from .conftest import BASE, ID_A, prices_json, station_json


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-20 07:10:07.326311+00", datetime(2026, 10, 20, 7, 10, 7, 326311, tzinfo=UTC)),
        ("2026-10-20 07:10:07.289+00", datetime(2026, 10, 20, 7, 10, 7, 289000, tzinfo=UTC)),
        ("2026-09-30T08:15:02.000Z", datetime(2026, 9, 30, 8, 15, 2, tzinfo=UTC)),
        ("2026-10-20 09:10:07+0200", datetime(2026, 10, 20, 7, 10, 7, tzinfo=UTC)),
        ("2026-10-20T07:10:07", datetime(2026, 10, 20, 7, 10, 7, tzinfo=UTC)),
        (None, None),
        ("", None),
        ("garbage", None),
    ],
)
def test_parse_timestamp(raw: str | None, expected: datetime | None) -> None:
    """Both documented timestamp formats are understood."""
    assert parse_timestamp(raw) == expected


@pytest.mark.parametrize(
    ("cents", "eur"), [(2029, 2.029), (1799, 1.799), (0, None), (None, None), ("x", None)]
)
def test_cents_to_eur(cents, eur) -> None:
    """Prices are tenths of a cent."""
    assert cents_to_eur(cents) == eur


async def test_search_and_prices(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Search results and prices are parsed."""
    aioclient_mock.get(
        f"{BASE}/api/stations/search",
        json={"count": 1, "items": [station_json()]},
    )
    aioclient_mock.get(f"{BASE}/api/prices/{ID_A}/current", json=prices_json())
    client = TankpulsClient(async_get_clientsession(hass), "1|secret")

    [station] = await client.search_stations(53.9, 9.5, 2, fuel="e10")
    assert station.street == "SANDBERG 142 A"
    assert station.city == "Itzehoe"
    assert station.price == 2.049
    prices = await client.get_prices(ID_A)
    assert set(prices) == {"e5", "e10", "diesel"}
    assert prices["diesel"].price == 2.159

    request = aioclient_mock.mock_calls[0]
    assert request[3]["Authorization"] == "Bearer 1|secret"
    assert request[3]["User-Agent"].startswith("ha-tankpuls/")


async def test_empty_prices_for_unknown_station(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The API answers 200 with an empty list for unknown stations."""
    aioclient_mock.get(f"{BASE}/api/prices/{ID_A}/current", json={"items": []})
    client = TankpulsClient(async_get_clientsession(hass))
    assert await client.get_prices(ID_A) == {}


async def test_errors(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """HTTP errors map to exceptions."""
    client = TankpulsClient(async_get_clientsession(hass))
    aioclient_mock.get(f"{BASE}/api/stations/{ID_A}", status=404, json={"error": "not_found"})
    with pytest.raises(TankpulsNotFoundError):
        await client.get_station(ID_A)

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/api/stations/{ID_A}", status=500)
    with pytest.raises(TankpulsApiError):
        await client.get_station(ID_A)

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/api/stations/{ID_A}", exc=TimeoutError)
    with pytest.raises(TankpulsConnectionError):
        await client.get_station(ID_A)

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/api/stations/{ID_A}", status=429, headers={"retry-after": "17"})
    with pytest.raises(TankpulsRateLimitError) as err:
        await client.get_station(ID_A)
    assert err.value.retry_after == 17


async def test_budget_blocks_request(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """With an exhausted window no request is sent."""
    aioclient_mock.get(
        f"{BASE}/health",
        json={"ok": True},
        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "30"},
    )
    client = TankpulsClient(async_get_clientsession(hass))
    await client.check_key()
    with pytest.raises(TankpulsRateLimitError):
        await client.check_key()
    assert aioclient_mock.call_count == 1


async def test_key_status_header(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """The key status is exposed; a missing header means anonymous."""
    client = TankpulsClient(async_get_clientsession(hass))
    aioclient_mock.get(f"{BASE}/health", json={"ok": True})
    assert await client.check_key() is None
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"{BASE}/health",
        json={"ok": True},
        headers={"x-api-key-status": "ok", "x-quota-used": "5", "x-quota-limit": "10000"},
    )
    assert await client.check_key() == "ok"
    assert client.meta.quota_limit == 10000
