"""Config, options, reconfigure flows for TankPuls."""

from __future__ import annotations

import math
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    LocationSelector,
    LocationSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .api import (
    KEY_STATUS_OK,
    Station,
    TankpulsClient,
    TankpulsError,
    TankpulsNotFoundError,
)
from .const import (
    CONF_API_KEY,
    CONF_CHANGE_STATIONS,
    CONF_LOCATION,
    CONF_POSTALCODE,
    CONF_SCAN_INTERVAL,
    CONF_SHOW_ON_MAP,
    CONF_STATIONS,
    DEFAULT_RADIUS_KM,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_RADIUS_KM,
    MAX_SCAN_INTERVAL,
    MAX_SEARCH_RESULTS,
    MIN_SCAN_INTERVAL,
)


async def validate_key(client: TankpulsClient) -> str | None:
    """Return an error key if the API key is not accepted, else None."""
    status = await client.check_key()
    if client.has_api_key and status != KEY_STATUS_OK:
        return "invalid_key"
    return None


def _label(station: Station) -> str:
    parts = [station.name]
    if station.street:
        parts.append(station.street)
    if station.city:
        parts.append(station.city)
    label = ", ".join(parts)
    if station.distance_m is not None:
        label += f" – {station.distance_m / 1000:.1f} km"
    if station.price is not None:
        label += f" – E10 {station.price:.3f} €"
    return label


def _location_schema(hass_lat: float, hass_lon: float) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_LOCATION,
                default={
                    "latitude": hass_lat,
                    "longitude": hass_lon,
                    "radius": DEFAULT_RADIUS_KM * 1000,
                },
            ): LocationSelector(LocationSelectorConfig(radius=True)),
            vol.Optional(CONF_POSTALCODE): TextSelector(),
        }
    )


class _StationSearchMixin:
    """Shared location and station selection steps."""

    hass: Any
    _client: TankpulsClient
    _found: dict[str, Station]

    def _preselected(self) -> list[str]:
        return []

    def _known_labels(self) -> dict[str, str]:
        return {}

    def _finish(self, station_ids: list[str]) -> ConfigFlowResult:
        raise NotImplementedError

    async def async_step_location(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Search stations by position/radius or postal code."""
        errors: dict[str, str] = {}
        if user_input is not None:
            location = user_input[CONF_LOCATION]
            radius_km = min(
                MAX_RADIUS_KM,
                max(1, math.ceil(float(location.get("radius", 2000)) / 1000)),
            )
            postalcode = (user_input.get(CONF_POSTALCODE) or "").strip()
            try:
                if postalcode:
                    stations = await self._client.search_by_postalcode(
                        postalcode, radius_km, fuel="e10", limit=MAX_SEARCH_RESULTS
                    )
                else:
                    stations = await self._client.search_stations(
                        location["latitude"],
                        location["longitude"],
                        radius_km,
                        fuel="e10",
                        limit=MAX_SEARCH_RESULTS,
                    )
            except TankpulsNotFoundError:
                errors["base"] = "postalcode_unknown"
            except TankpulsError:
                errors["base"] = "cannot_connect"
            else:
                if not stations and not self._preselected():
                    errors["base"] = "no_stations"
                else:
                    self._found = {s.id: s for s in stations}
                    return await self.async_step_stations()
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="location",
            data_schema=_location_schema(self.hass.config.latitude, self.hass.config.longitude),
            errors=errors,
        )

    async def async_step_stations(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the stations to monitor."""
        if user_input is not None:
            return self._finish(list(user_input[CONF_STATIONS]))
        options = {sid: _label(st) for sid, st in self._found.items()}
        # Keep previously selected stations selectable even if outside the new search.
        for sid, label in self._known_labels().items():
            options.setdefault(sid, label)
        default = [sid for sid in self._preselected() if sid in options]
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="stations",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STATIONS, default=default): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=k, label=v) for k, v in options.items()
                            ],
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )


class TankpulsConfigFlow(_StationSearchMixin, ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._api_key: str | None = None
        self._found = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the optional API key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            key = (user_input.get(CONF_API_KEY) or "").strip() or None
            self._client = TankpulsClient(async_get_clientsession(self.hass), key)
            try:
                if error := await validate_key(self._client):
                    errors["base"] = error
            except TankpulsError:
                errors["base"] = "cannot_connect"
            if not errors:
                self._api_key = key
                return await self.async_step_location()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Optional(CONF_API_KEY): TextSelector()}),
            errors=errors,
        )

    def _finish(self, station_ids: list[str]) -> ConfigFlowResult:
        return self.async_create_entry(
            title="TankPuls",
            data={CONF_API_KEY: self._api_key},
            options={
                CONF_STATIONS: station_ids,
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                CONF_SHOW_ON_MAP: False,
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change or remove the API key."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            key = (user_input.get(CONF_API_KEY) or "").strip() or None
            client = TankpulsClient(async_get_clientsession(self.hass), key)
            try:
                if error := await validate_key(client):
                    errors["base"] = error
            except TankpulsError:
                errors["base"] = "cannot_connect"
            if not errors:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_API_KEY: key})
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Optional(CONF_API_KEY): TextSelector()}),
                {CONF_API_KEY: entry.data.get(CONF_API_KEY)},
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> TankpulsOptionsFlow:
        """Return the options flow."""
        return TankpulsOptionsFlow()


class TankpulsOptionsFlow(_StationSearchMixin, OptionsFlow):
    """Change interval, map display and stations."""

    def __init__(self) -> None:
        """Initialize."""
        self._found = {}
        self._new_options: dict[str, Any] = {}

    def _preselected(self) -> list[str]:
        return list(self.config_entry.options.get(CONF_STATIONS, []))

    def _known_labels(self) -> dict[str, str]:
        coordinator = getattr(self.config_entry, "runtime_data", None)
        data = getattr(coordinator, "data", None) or {}
        return {sid: item.station.name for sid, item in data.items()}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Edit general options."""
        if user_input is not None:
            self._new_options = {
                **self.config_entry.options,
                CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                CONF_SHOW_ON_MAP: user_input[CONF_SHOW_ON_MAP],
            }
            if user_input[CONF_CHANGE_STATIONS]:
                self._client = TankpulsClient(
                    async_get_clientsession(self.hass),
                    self.config_entry.data.get(CONF_API_KEY),
                )
                return await self.async_step_location()
            return self.async_create_entry(data=self._new_options)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=1,
                            unit_of_measurement="min",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_SHOW_ON_MAP, default=options.get(CONF_SHOW_ON_MAP, False)
                    ): BooleanSelector(),
                    vol.Required(CONF_CHANGE_STATIONS, default=False): BooleanSelector(),
                }
            ),
        )

    def _finish(self, station_ids: list[str]) -> ConfigFlowResult:
        return self.async_create_entry(data={**self._new_options, CONF_STATIONS: station_ids})
