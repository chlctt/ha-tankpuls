"""Shared fixtures."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tankpuls.const import (
    CONF_API_KEY,
    CONF_SCAN_INTERVAL,
    CONF_SHOW_ON_MAP,
    CONF_STATIONS,
    DOMAIN,
)

BASE = "https://api.tankpuls.de"
ID_A = "1797748d-6f33-4da5-bc00-901725244da2"
ID_B = "14994bc2-0b1e-453d-abb6-13bc79a5380f"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading of custom integrations in all tests."""


def station_json(
    station_id: str = ID_A,
    *,
    name: str = "Esso Tankstelle",
    status: str = "open",
    is_active: bool = True,
    price_cents: int | None = 2049,
    distance_m: float | None = 100.0,
) -> dict:
    """Return a StationResource as sent by the API."""
    return {
        "id": station_id,
        "name": name,
        "brandSlug": "esso",
        "brandName": "Esso",
        "street": "SANDBERG 142 A ",
        "postcode": "25524",
        "city": "ITZEHOE",
        "state": "SH",
        "isOpen24h": False,
        "status": status,
        "isActive": is_active,
        "lat": 53.93,
        "lng": 9.53,
        "priceCents": price_cents,
        "priceTs": "2026-10-20 07:42:06.243+00" if price_cents else None,
        "distanceM": distance_m,
        "openingHours": [],
        "phone": None,
        "website": None,
        "externalId": "94e70fc4-b22f-4e5a-877f-bc1082cdae81",
        "nuts1": None,
        "nuts2": None,
        "nuts3": None,
        "statusUpdatedAt": "2026-05-10 17:37:06.439208+00",
        "lastSeenAt": "2026-10-20 03:30:09.670666+00",
    }


def prices_json(station_id: str = ID_A, valid_from: str | None = None) -> dict:
    """Return a /prices/{id}/current payload."""
    ts = valid_from or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {
        "items": [
            {"stationId": station_id, "fuel": "e5", "priceCents": 2109, "validFrom": ts},
            {"stationId": station_id, "fuel": "e10", "priceCents": 2049, "validFrom": ts},
            {"stationId": station_id, "fuel": "diesel", "priceCents": 2159, "validFrom": ts},
        ]
    }


def mock_api(
    aioclient_mock: AiohttpClientMocker,
    station_ids: tuple[str, ...] = (ID_A,),
    headers: dict[str, str] | None = None,
) -> None:
    """Register default responses for the given stations."""
    aioclient_mock.get(f"{BASE}/health", json={"ok": True}, headers=headers)
    for sid in station_ids:
        aioclient_mock.get(
            f"{BASE}/api/prices/{sid}/current", json=prices_json(sid), headers=headers
        )
        aioclient_mock.get(f"{BASE}/api/stations/{sid}", json=station_json(sid), headers=headers)


@pytest.fixture
def entry() -> MockConfigEntry:
    """Return a config entry for station A."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="TankPuls",
        data={CONF_API_KEY: None},
        options={
            CONF_STATIONS: [ID_A],
            CONF_SCAN_INTERVAL: 10,
            CONF_SHOW_ON_MAP: True,
        },
    )


def hours_ago(hours: float) -> str:
    """ISO timestamp `hours` ago."""
    return (datetime.now(UTC) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


__all__ = ["BASE", "ID_A", "ID_B", "hours_ago", "mock_api", "prices_json", "station_json"]
