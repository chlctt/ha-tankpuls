"""Constants for the TankPuls integration."""

from __future__ import annotations

import logging
from datetime import timedelta

DOMAIN = "tankpuls"
LOGGER = logging.getLogger(__package__)

CONF_API_KEY = "api_key"
CONF_STATIONS = "station_ids"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_SHOW_ON_MAP = "show_on_map"
CONF_LOCATION = "location"
CONF_POSTALCODE = "postalcode"
CONF_CHANGE_STATIONS = "change_stations"

FUELS = ("e5", "e10", "diesel")

DEFAULT_SCAN_INTERVAL = 10  # minutes
MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 60
DEFAULT_RADIUS_KM = 2
MAX_RADIUS_KM = 50
MAX_SEARCH_RESULTS = 100

# The station master data (status, address) is refreshed every Nth cycle.
STATION_REFRESH_CYCLES = 3
# Concurrent requests per update cycle.
MAX_CONCURRENT_REQUESTS = 4
# Share of the monthly quota the integration may use when an API key is set.
QUOTA_SHARE = 0.9

# Stale-data detection: no price report within this time during the day.
STALE_AFTER = timedelta(hours=3)
STALE_DAY_START_HOUR = 6
STALE_DAY_END_HOUR = 22
# Consecutive cycles with a rejected API key before a repair issue is raised.
KEY_ISSUE_AFTER_CYCLES = 3

ATTRIBUTION = "Quelle: MTS-K über tankerkoenig.de, CC BY 4.0 · TankPuls"

ISSUE_INVALID_KEY = "invalid_api_key"
ISSUE_STATION_GONE = "station_gone"
ISSUE_STALE_DATA = "stale_data"
