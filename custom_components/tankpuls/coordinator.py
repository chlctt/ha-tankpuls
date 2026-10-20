"""Data update coordinator for TankPuls."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    KEY_STATUS_OK,
    Price,
    Station,
    TankpulsClient,
    TankpulsError,
    TankpulsNotFoundError,
    TankpulsRateLimitError,
)
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_STATIONS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ISSUE_INVALID_KEY,
    ISSUE_STALE_DATA,
    ISSUE_STATION_GONE,
    KEY_ISSUE_AFTER_CYCLES,
    LOGGER,
    MAX_CONCURRENT_REQUESTS,
    MIN_SCAN_INTERVAL,
    QUOTA_SHARE,
    STALE_AFTER,
    STALE_DAY_END_HOUR,
    STALE_DAY_START_HOUR,
    STATION_REFRESH_CYCLES,
)

type TankpulsConfigEntry = ConfigEntry[TankpulsCoordinator]


@dataclass(slots=True)
class StationData:
    """Latest data of one station."""

    station: Station
    prices: dict[str, Price] = field(default_factory=dict)
    available: bool = True

    @property
    def is_gone(self) -> bool:
        """Return True when the station was decommissioned or removed."""
        return not self.station.is_active


class TankpulsCoordinator(DataUpdateCoordinator[dict[str, StationData]]):
    """Fetch prices and status of all configured stations."""

    config_entry: TankpulsConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: TankpulsConfigEntry,
        client: TankpulsClient,
    ) -> None:
        """Initialize."""
        self.client = client
        self.station_ids: list[str] = list(entry.options.get(CONF_STATIONS, []))
        self._base_interval = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        self._stations: dict[str, Station] = {}
        self._cycle = 0
        self._bad_key_cycles = 0
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=self._base_interval),
        )

    @property
    def latest_price_report(self) -> datetime | None:
        """Return the newest price timestamp over all stations."""
        stamps = [
            price.valid_from
            for data in (self.data or {}).values()
            for price in data.prices.values()
            if price.valid_from
        ]
        return max(stamps) if stamps else None

    async def _async_update_data(self) -> dict[str, StationData]:
        self._cycle += 1
        refresh_all = (self._cycle - 1) % STATION_REFRESH_CYCLES == 0
        previous = self.data or {}

        results = await asyncio.gather(
            *(
                self._update_station(sid, refresh_all, previous.get(sid))
                for sid in self.station_ids
            ),
            return_exceptions=True,
        )

        data: dict[str, StationData] = {}
        errors: list[BaseException] = []
        for sid, result in zip(self.station_ids, results, strict=True):
            if isinstance(result, TankpulsRateLimitError):
                # Keep old data and wait for the next window.
                raise UpdateFailed(
                    f"Rate limit exceeded, retry in {result.retry_after}s",
                    retry_after=float(result.retry_after),
                ) from result
            if isinstance(result, BaseException):
                if not isinstance(result, TankpulsError):
                    raise result
                LOGGER.debug("Update of station %s failed: %s", sid, result)
                errors.append(result)
                old = previous.get(sid)
                if old is not None:
                    data[sid] = StationData(old.station, old.prices, available=False)
                continue
            data[sid] = result

        if self.station_ids and len(errors) == len(self.station_ids):
            raise UpdateFailed(f"Could not reach TankPuls: {errors[0]}") from errors[0]

        self._adapt_interval()
        self._check_key_status()
        self._check_stations(data)
        self._check_stale(data)
        return data

    async def _update_station(
        self, station_id: str, refresh: bool, previous: StationData | None
    ) -> StationData:
        async with self._semaphore:
            prices = await self.client.get_prices(station_id)
            station = self._stations.get(station_id)
            # Master data/status every Nth cycle, on first sight and whenever the
            # API answers with an empty price list (unknown or retired station).
            if station is None or refresh or not prices:
                try:
                    station = await self.client.get_station(station_id)
                except TankpulsNotFoundError:
                    station = self._stations.get(station_id) or Station(
                        id=station_id,
                        name=previous.station.name if previous else station_id,
                        brand="",
                        street=None,
                        postcode=None,
                        city=None,
                        lat=0,
                        lng=0,
                        status="no_prices",
                    )
                    station.is_active = False
                self._stations[station_id] = station
        return StationData(station, prices)

    def _adapt_interval(self) -> None:
        """Adapt the interval to the monthly quota when an API key is used."""
        interval = self._base_interval
        quota = self.client.meta.quota_limit
        if self.client.has_api_key and quota:
            requests_per_cycle = len(self.station_ids) * (1 + 1 / STATION_REFRESH_CYCLES)
            minutes_per_month = 31 * 24 * 60
            needed = math.ceil(minutes_per_month * requests_per_cycle / (quota * QUOTA_SHARE))
            interval = max(interval, needed)
        interval = max(MIN_SCAN_INTERVAL, interval)
        new = timedelta(minutes=interval)
        if self.update_interval != new:
            LOGGER.debug("Update interval set to %s", new)
            self.update_interval = new

    def _check_key_status(self) -> None:
        """Raise a repair issue when a configured key is not accepted."""
        issue_id = f"{ISSUE_INVALID_KEY}_{self.config_entry.entry_id}"
        status = self.client.meta.key_status
        if not self.client.has_api_key or status in (None, KEY_STATUS_OK):
            self._bad_key_cycles = 0
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        self._bad_key_cycles += 1
        if self._bad_key_cycles >= KEY_ISSUE_AFTER_CYCLES:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_INVALID_KEY,
                translation_placeholders={"status": status},
                data={"entry_id": self.config_entry.entry_id},
            )

    def _check_stations(self, data: dict[str, StationData]) -> None:
        for sid, item in data.items():
            issue_id = f"{ISSUE_STATION_GONE}_{self.config_entry.entry_id}_{sid}"
            if item.is_gone:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=True,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=ISSUE_STATION_GONE,
                    translation_placeholders={"station": item.station.name},
                    data={"entry_id": self.config_entry.entry_id, "station_id": sid},
                )
            else:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def _check_stale(self, data: dict[str, StationData]) -> None:
        """Warn when no station reported a price for hours during the day."""
        issue_id = f"{ISSUE_STALE_DATA}_{self.config_entry.entry_id}"
        stamps = [
            p.valid_from for item in data.values() for p in item.prices.values() if p.valid_from
        ]
        now = dt_util.now()
        daytime = STALE_DAY_START_HOUR <= now.hour < STALE_DAY_END_HOUR
        stale = bool(stamps) and daytime and dt_util.utcnow() - max(stamps) > STALE_AFTER
        if stale:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_STALE_DATA,
                translation_placeholders={"hours": str(int(STALE_AFTER.total_seconds() // 3600))},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
