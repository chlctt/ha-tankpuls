"""Minimal async client for the TankPuls API (https://api.tankpuls.de/docs).

This module is the only place that knows the wire format of the API and does
not depend on Home Assistant.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import aiohttp

BASE_URL = "https://api.tankpuls.de"
REQUEST_TIMEOUT = 15
USER_AGENT = "ha-tankpuls/{version} (+https://github.com/chlctt/ha-tankpuls)"

KEY_STATUS_OK = "ok"


class TankpulsError(Exception):
    """Base error."""


class TankpulsConnectionError(TankpulsError):
    """Network problem or timeout."""


class TankpulsApiError(TankpulsError):
    """Unexpected API response."""


class TankpulsNotFoundError(TankpulsError):
    """Station or postal code unknown."""


class TankpulsRateLimitError(TankpulsError):
    """Rate limit hit."""

    def __init__(self, retry_after: int) -> None:
        """Store the seconds until the next window."""
        super().__init__(f"Rate limit exceeded, retry after {retry_after}s")
        self.retry_after = retry_after


@dataclass(slots=True)
class ApiMeta:
    """Information taken from the response headers."""

    key_status: str | None = None
    ratelimit_remaining: int | None = None
    reset_at: float = 0.0  # time.monotonic()
    quota_used: int | None = None
    quota_limit: int | None = None


@dataclass(slots=True)
class Price:
    """Current price of one fuel."""

    fuel: str
    price: float  # EUR per liter
    valid_from: datetime | None


@dataclass(slots=True)
class Station:
    """Station master data."""

    id: str
    name: str
    brand: str
    street: str | None
    postcode: str | None
    city: str | None
    lat: float
    lng: float
    status: str  # open | closed | no_prices
    is_active: bool = True
    is_open_24h: bool = False
    external_id: str | None = None
    distance_m: float | None = None
    price: float | None = None
    price_ts: datetime | None = None
    prices: dict[str, Price] = field(default_factory=dict)


_FRACTION = re.compile(r"\.(\d+)")
_SHORT_OFFSET = re.compile(r"([+-]\d{2})$")
_COMPACT_OFFSET = re.compile(r"([+-]\d{2})(\d{2})$")


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse ISO 8601 and PostgreSQL style timestamps into aware UTC."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace(" ", "T", 1)
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    elif _COMPACT_OFFSET.search(text) and "T" in text and ":" not in text[-5:]:
        text = _COMPACT_OFFSET.sub(r"\1:\2", text)
    elif _SHORT_OFFSET.search(text.split("T", 1)[-1]):
        text = _SHORT_OFFSET.sub(r"\1:00", text)
    text = _FRACTION.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), text, count=1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def cents_to_eur(price_cents: Any) -> float | None:
    """Convert tenths of a cent to euro per liter."""
    cents = _to_int(price_cents)
    if cents is None or cents <= 0:
        return None
    return cents / 1000


def parse_station(raw: dict[str, Any]) -> Station:
    """Build a Station from a StationResource."""
    return Station(
        id=str(raw["id"]),
        name=raw.get("name") or raw.get("brandName") or "Tankstelle",
        brand=raw.get("brandName") or "",
        street=(raw.get("street") or "").strip() or None,
        postcode=raw.get("postcode"),
        city=(raw.get("city") or "").strip().title() or None,
        lat=float(raw.get("lat") or 0),
        lng=float(raw.get("lng") or 0),
        status=raw.get("status") or "no_prices",
        is_active=raw.get("isActive", True) is not False,
        is_open_24h=bool(raw.get("isOpen24h")),
        external_id=raw.get("externalId"),
        distance_m=raw.get("distanceM"),
        price=cents_to_eur(raw.get("priceCents")),
        price_ts=parse_timestamp(raw.get("priceTs")),
    )


class TankpulsClient:
    """Client for the TankPuls REST API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str | None = None,
        *,
        version: str = "0",
        base_url: str = BASE_URL,
    ) -> None:
        """Initialize."""
        self._session = session
        self._api_key = api_key or None
        self._base_url = base_url.rstrip("/")
        self._user_agent = USER_AGENT.format(version=version)
        self.meta = ApiMeta()

    @property
    def has_api_key(self) -> bool:
        """Return True when an API key is configured."""
        return self._api_key is not None

    def _check_budget(self) -> None:
        """Refuse to send a request that would hit an exhausted rate limit."""
        meta = self.meta
        now = time.monotonic()
        if (
            meta.ratelimit_remaining is not None
            and meta.ratelimit_remaining <= 0
            and now < meta.reset_at
        ):
            raise TankpulsRateLimitError(math.ceil(meta.reset_at - now))

    def _update_meta(self, headers: Any) -> None:
        meta = self.meta
        meta.key_status = headers.get("x-api-key-status")
        remaining = _to_int(headers.get("x-ratelimit-remaining"))
        if remaining is not None:
            meta.ratelimit_remaining = remaining
            reset = _to_int(headers.get("x-ratelimit-reset"))
            if reset is not None:
                meta.reset_at = time.monotonic() + reset
        meta.quota_used = _to_int(headers.get("x-quota-used"))
        meta.quota_limit = _to_int(headers.get("x-quota-limit"))

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        headers = {"Accept": "application/json", "User-Agent": self._user_agent}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        self._check_budget()
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.get(
                    f"{self._base_url}{path}", params=params, headers=headers
                ) as resp:
                    self._update_meta(resp.headers)
                    if resp.status == 429:
                        raise TankpulsRateLimitError(_to_int(resp.headers.get("retry-after")) or 60)
                    if resp.status == 404:
                        raise TankpulsNotFoundError(path)
                    if resp.status >= 400:
                        raise TankpulsApiError(f"HTTP {resp.status} for {path}")
                    return await resp.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError) as err:
            raise TankpulsConnectionError(str(err) or type(err).__name__) from err
        except ValueError as err:
            raise TankpulsApiError(f"Invalid JSON for {path}") from err

    async def check_key(self) -> str | None:
        """Query /health and return the x-api-key-status header (None = absent)."""
        await self._request("/health")
        return self.meta.key_status

    async def search_stations(
        self,
        lat: float,
        lng: float,
        radius_km: float,
        *,
        fuel: str | None = None,
        limit: int = 100,
    ) -> list[Station]:
        """Return active stations around a point, nearest first."""
        params: dict[str, Any] = {"lat": lat, "lng": lng, "rad": radius_km, "limit": limit}
        if fuel:
            params["fuel"] = fuel
        data = await self._request("/api/stations/search", params)
        return self._stations(data)

    async def search_by_postalcode(
        self,
        postalcode: str,
        radius_km: float,
        *,
        fuel: str | None = None,
        limit: int = 100,
    ) -> list[Station]:
        """Return active stations around a postal code."""
        params: dict[str, Any] = {
            "postalcode": postalcode,
            "rad": radius_km,
            "limit": limit,
        }
        if fuel:
            params["fuel"] = fuel
        data = await self._request("/api/stations/postalcode", params)
        return self._stations(data)

    async def get_station(self, station_id: str) -> Station:
        """Return the master data of one station (also inactive ones)."""
        data = await self._request(f"/api/stations/{station_id}")
        if not isinstance(data, dict) or "id" not in data:
            raise TankpulsApiError("Unexpected station payload")
        return parse_station(data)

    async def get_prices(self, station_id: str) -> dict[str, Price]:
        """Return the current prices by fuel; empty for unknown stations."""
        data = await self._request(f"/api/prices/{station_id}/current")
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise TankpulsApiError("Unexpected price payload")
        prices: dict[str, Price] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            value = cents_to_eur(item.get("priceCents"))
            fuel = item.get("fuel")
            if value is None or not fuel:
                continue
            prices[fuel] = Price(fuel, value, parse_timestamp(item.get("validFrom")))
        return prices

    @staticmethod
    def _stations(data: Any) -> list[Station]:
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise TankpulsApiError("Unexpected station list payload")
        return [parse_station(item) for item in items if isinstance(item, dict)]
