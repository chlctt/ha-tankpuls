# Konzept: Home-Assistant-Integration `tankpuls`

Stand: 2026-10-20 · Status: **reviewt, freigegeben, umgesetzt (v0.1.0)** · API: TankPuls 2026.09 (`docs/tankpuls-openapi.json`)

## 1. Ziel

Funktionsgleicher Ersatz für die HA-Core-Integration `tankerkoenig`, aber auf Basis der
TankPuls-REST-API (`https://api.tankpuls.de`). Konfigurierte Tankstellen werden in
Intervallen abgefragt; je Station gibt es Preis-Sensoren (E5, E10, Diesel) und einen
Offen/Geschlossen-Status.

Nicht-Ziele (v1): Preisverläufe, Preis-Alarme/Push, Live-Preise per WebSocket, Core-Einreichung,
automatischer Import bestehender Tankerkönig-Einträge.

## 2. Relevante API-Fakten (verifiziert gegen Live-API und OpenAPI)

| Thema | Fakt |
|---|---|
| Auth | **Optional.** `Authorization: Bearer <id>\|<geheimnis>`. Ohne Key: anonyme Limits je IP. Ein ungültiger Key führt **nie** zu einem Fehler, die Anfrage läuft anonym weiter; Status steht im Header `x-api-key-status` (`ok`, `invalid_credentials:malformed\|unknown\|mismatch\|revoked\|ip_not_allowed`). |
| Limits anonym | Gruppe „Listen“ (`/api/stations*`-Listen): 60/min · Gruppe „Details“ (`/api/stations/{id}`, `/api/prices/{id}/current`): 120/min · je IP. Mit Key: Minutenlimit des Tarifs (Community 30/min, 10.000 Anfragen/Monat, kostenlos, manuelle Freischaltung). Header `x-ratelimit-*`, bei 429 `retry-after`. |
| Preise | Ganzzahl in Zehntel-Cent: `2029` = 2,029 €/l → `priceCents / 1000`. |
| Kraftstoffe | `e5`, `e10`, `diesel` |
| Stationssuche | `GET /api/stations/search?lat&lng&rad(≤50 km)&fuel&limit(≤500)` (mit `distanceM`), `GET /api/stations/postalcode?postalcode&rad&fuel`, `GET /api/stations?minLat…` (BBox). Liefert nur aktive Stationen. |
| Einzelstation | `GET /api/stations/{uuid}` → `StationResource` (inkl. `status`: `open`/`closed`/`no_prices`, `isActive`, Name, Marke, Adresse, lat/lng, `externalId` = Tankerkönig-ID). |
| Aktuelle Preise | `GET /api/prices/{uuid}/current` → `{items:[{stationId, fuel, priceCents, validFrom}]}` (alle Kraftstoffe der Station). |
| Batch | **Kein** Abruf mehrerer Stations-IDs in einem Request. Listen-Endpunkte (Radius/BBox) liefern nur **einen** Kraftstoff pro Request (`fuel`). |
| Zeitformate | `validFrom`/Events: ISO 8601 UTC. Stationsfelder (`priceTs`, `statusUpdatedAt`, `lastSeenAt`): PostgreSQL-Format `2026-10-20 07:10:07.326311+00`. |
| Datenfrische | Import alle ~2 min aus MTS-K; Verzögerung bis ca. 3 min. |
| Lizenz | Daten CC BY 4.0, Namensnennung Pflicht („Quelle: MTS-K über tankerkoenig.de, CC BY 4.0 · TankPuls“). Nur Verbraucherinformation; Mineralölwirtschaft ausgeschlossen (AGB). |

## 3. Architektur

Custom Component (HACS), Domain `tankpuls`, `iot_class: cloud_polling`, `config_flow: true`,
`single_config_entry: true`. Keine externe PyPI-Abhängigkeit: eigener schlanker Async-Client
auf Basis der HA-`aiohttp`-Session (`async_get_clientsession`).

```
custom_components/tankpuls/
  manifest.json, hacs.json (Repo-Root), const.py
  api.py            # TankpulsClient + Datenklassen + Exceptions (einzige Stelle mit API-Wissen)
  coordinator.py    # TankpulsCoordinator (DataUpdateCoordinator)
  config_flow.py    # user / reauth / reconfigure / options
  __init__.py       # Setup, runtime_data, Plattformen
  sensor.py         # Preis-Sensoren
  binary_sensor.py  # Station offen
  diagnostics.py, repairs (optional)
  strings.json, translations/de.json, en.json
tests/              # pytest-homeassistant-custom-component, Mock-Antworten
.github/workflows/  # ruff, pytest, hassfest, hacs/action
```

### 3.1 API-Client (`api.py`)
- `async search_stations(lat, lng, rad, fuel=None, limit=100)`, `async search_by_postalcode(...)`
- `async get_station(id) -> Station`, `async get_prices(id) -> dict[fuel, Price]`
- Setzt den Key-Header, liest `x-api-key-status`, `x-ratelimit-*` und `retry-after`.
- Exceptions: `TankpulsConnectionError`, `TankpulsRateLimitError(retry_after)`,
  `TankpulsNotFoundError`, `TankpulsApiError`. Timeout 10 s.
- Parser: `priceCents → Decimal/float €`, PG-Zeitstempel → `datetime` (UTC).
- Das gesamte Parsing tolerant: fehlende/`null`-Felder → `None`, nie Exception.

### 3.2 Coordinator
- Intervall: Default **10 min**, Option 5–60 min (min. 5, um die Importfrequenz von 2 min und die Limits nicht auszureizen).
- Pro Zyklus je konfigurierter Station:
  1. `GET /api/prices/{id}/current` (alle Kraftstoffe),
  2. `GET /api/stations/{id}` für `status` (und aktuelle Stammdaten).
  → 2 Requests/Station/Zyklus. Parallel mit `asyncio.Semaphore(4)`; bei N=10 Stationen: 20 Requests alle 10 min, weit unter 60/120 pro Minute.
  Stammdaten ändern sich selten; **Optimierung** (Review-Frage): `get_station` nur jeden 3. Zyklus, dazwischen Status aus Cache.
- Ergebnis: `dict[station_id, StationData(station, prices: dict[fuel, Price], status)]`.
- Teilausfall: schlägt eine Station fehl (404/Fehler), behalten die anderen ihre Daten; die betroffene Station wird `unavailable`. Schlägt **alles** fehl → `UpdateFailed`.
- 429 → `UpdateFailed(retry_after=…)`; Netzwerkfehler → `UpdateFailed`.
- 404 bei einer Station dauerhaft (`isActive=false`/gelöscht) → Repair-Issue „Station nicht mehr verfügbar“.

### 3.3 Authentifizierung / Key-Handling
Da der Key optional ist und falsche Keys still anonym weiterlaufen, gilt:
- Config Flow validiert den Key mit einer Probe-Suche und prüft `x-api-key-status == ok`; sonst Fehlermeldung je Status (malformed/unknown/revoked/ip_not_allowed) mit Hinweis, das Feld leer zu lassen.
- Im Betrieb: konfigurierter Key + Status ≠ `ok` → **Repair-Issue** (nicht blockierend, Daten fließen weiter anonym) mit Link zum Kundenportal; Reauth-Flow zum Austauschen/Entfernen des Keys.

### 3.4 Config Flow
1. **user**: optionaler API-Key → Validierung.
2. **location**: Radius-Suche (Default: HA-Heimat-Koordinaten, 2 km, max. 50 km) *oder* Postleitzahl + Radius.
3. **stations**: Mehrfachauswahl aus Treffern (sortiert nach Entfernung, Label: „Marke, Straße, Ort – Entfernung – E10-Preis“). Suche mit `fuel=e10` nur für die Preisanzeige im Label.
4. Entry-Daten: `api_key`, `station_ids`; Options: `scan_interval`, `show_on_map`, Stationsliste (Options-Flow wiederholt Schritt 2–3).
- `single_config_entry`; Reauth + Reconfigure für den Key.

### 3.5 Entitäten
- **Device je Station** (`identifiers=(DOMAIN, station_id)`, Name = Stationsname, Hersteller = Marke, `configuration_url=None`).
- **Preis-Sensor** je Station × Kraftstoff: `device_class=MONETARY`, Einheit EUR, `state_class=MEASUREMENT`, Präzision 3 Nachkommastellen, `translation_key` (`e5`→„Super E5“, `e10`→„Super E10“, `diesel`→„Diesel“), unique_id `<station_id>_<fuel>`. Attribute: `price_valid_from`, `brand`, `street`, `postcode`, `city`, bei `show_on_map` `latitude`/`longitude`, `external_id` (Tankerkönig-ID), `attribution`.
  Kraftstoff ohne Preis → `unknown`. Neu auftauchende Kraftstoffe werden dynamisch als Entität ergänzt.
- **Binary-Sensor „Geöffnet“**: ohne device_class, `on` = geöffnet: `status=open → on`, `closed → off`, `no_prices → unknown`.
- Attribution an allen Entitäten: „Quelle: MTS-K über tankerkoenig.de, CC BY 4.0 · TankPuls“.
- Entity-Naming so nah wie möglich an Tankerkönig, um Dashboards zu erleichtern (kein Auto-Import in v1; `externalId` als Hilfe für spätere Migration).

### 3.6 Fehler, Diagnose
- `diagnostics.py` schwärzt Key; enthält Stationsliste, Header-Status, letzte Update-Dauer.
- Logging: Rate-Limit-Header bei Debug.

## 4. Tests & CI
- `pytest-homeassistant-custom-component`; Mock-HTTP via `aioclient_mock` mit echten Beispielantworten (siehe Live-Abfrage).
- Fälle: Config Flow (Key ok/ungültig/leer, PLZ unbekannt 404, keine Treffer), Options-Flow, Coordinator (Erfolg, Teilausfall, 429 mit retry-after, Timeout), Sensor-Werte (Zehntel-Cent-Umrechnung, fehlender Preis), Binary-Sensor-Stati, Key-Status-Repair, Diagnostics.
- CI: `ruff`, `pytest`, `hassfest`, `hacs/action`.

## 5. Risiken / offene Punkte
1. **Upstream-Abhängigkeit:** TankPuls bezieht die Daten laut Doku über **Tankerkönig**. Fällt die Quelle länger aus, werden TankPuls-Preise veraltet, ohne dass die API Fehler liefert. Gegenmaßnahme: Attribute `price_valid_from`/`last_seen` sichtbar machen; optionaler Diagnose-Sensor „Datenalter“ (Review-Frage: v1 oder später?).
2. **Limit-Teilung / Kontingent:** Anonyme Limits gelten je IP; mehrere HA-Instanzen hinter derselben IP teilen sie. Mit Community-Key (30/min, 10.000 Anfragen/Monat) kostet **eine** Station bei 2 Requests alle 10 min ≈ 8.900 Anfragen/Monat; ab 2 Stationen ist das Kontingent überschritten (die API antwortet laut Doku trotzdem weiter und erfasst Mehrverbrauch). Das beeinflusst Default-Intervall und Request-Strategie (siehe Alternative A).
3. **Alternative A (weniger Requests):** Statt pro Station einen Radius-/BBox-Request je Kraftstoff (`fuel=e5|e10|diesel`) um alle gewählten Stationen: konstant 3 Requests/Zyklus, enthält `status` und Preis; Nachteil: lädt ggf. hunderte unbeteiligte Stationen und funktioniert nur, wenn die Stationen geografisch eng liegen. Frage: Hybrid (BBox wenn eng beieinander, sonst Einzelabfragen)?
4. **Key ist optional, aber Kontingent nur mit Key** – Verhalten ohne Key bzgl. Monatskontingent ist in der Doku nicht beschrieben (nur Minutenlimits).
5. **ToS:** Nutzung nur Verbraucherinformation; Hinweis in README.
6. Mindest-HA-Version: aktuelle Stable (`entry.runtime_data`, `UpdateFailed(retry_after)`); genaue Untergrenze bei Implementierung festlegen.

---

## 6. Revision nach unabhängigem Review (verbindlich; überschreibt widersprechende Stellen oben)

Review durch einen separaten Agenten (anderes Modell), geprüft gegen OpenAPI und Live-API am 2026-10-20. Alle API-Angaben aus Abschnitt 2 wurden bestätigt. Folgende Änderungen:

### Blocker
- **B1 Key-Strategie:** Standard ist **anonym** (Listen 60/min, Details 120/min je IP, kein dokumentiertes Monatskontingent). Der Key ist eine „erweiterte“ Option (z. B. geteilte IP). Der Community-Tarif ist mit 30/min und 10.000/Monat meist *schlechter* als anonym. Mit Key liest der Coordinator `x-quota-used`/`x-quota-limit` und begrenzt das Intervall automatisch auf ≤ 90 % des Kontingents. Der Client sendet einen eigenen `User-Agent` (Integrationsversion). Vor dem Release beim Betreiber klären: Kosten von Mehrverbrauch im Community-Tarif; ist eine öffentliche HACS-Integration mit vielen anonymen Installationen erwünscht; schriftliche Bestätigung, dass anonym kein Monatskontingent gilt.
- **B2 Preis-Sensor:** **keine** `device_class` (MONETARY erlaubt nur `TOTAL`), `state_class=MEASUREMENT`, Einheit `EUR`, `suggested_display_precision=3`. So bleiben Langzeitstatistiken wie bei Tankerkönig erhalten.
- **B3 Stationsstatus:** `/api/prices/{unbekannte-id}/current` liefert `200 {"items":[]}`, kein 404; stillgelegte Stationen liefern bei `/api/stations/{id}` `200` mit `isActive:false`. Eine Station gilt als „weg“ bei 404 **oder** `isActive=false` (festgestellt über `get_station`). Bei leerer Preisliste wird `get_station` sofort nachgezogen. Das Repair-Issue ist *fixable*: der Fix-Flow entfernt die Station samt Device und Entities.

### Wichtig
- **W1/W2 Key-Handling:** Prüfung über `GET /health` (liefert `x-api-key-status`, ohne Rate-Limit). Fehlender Header = anonym. **Kein Reauth.** Stattdessen Repair-Issue (WARNING, fixable: Key ersetzen/entfernen) erst nach 2–3 aufeinanderfolgenden Zyklen mit Status ≠ `ok`, automatisch gelöscht bei `ok`; `ip_not_allowed` wird getrennt gemeldet. Key änderbar per Reconfigure-Flow.
- **W3 Eintragsaufbau:** ein Config Entry (`single_config_entry`), Stationsliste **nur** in `options["station_ids"]` (eine Quelle der Wahrheit); Key und Intervall in `data`/`options`. Beim erneuten Suchen im Options-Flow bleiben bereits gewählte Stationen erhalten. Abweichung von Tankerkönig (mehrere Einträge) wird dokumentiert. Subentries als spätere Option.
- **W4 Aufräumen:** Abgewählte Stationen → Device und Entities entfernen; `async_remove_config_entry_device`; neue Kraftstoffe per Coordinator-Listener ergänzen; Entities nur für Kraftstoffe, die die Station anbietet.
- **W5 Request-Strategie v1:** Preise je Station und Zyklus (`/prices/{id}/current`), `get_station` nur jeden 3. Zyklus (Start und leere Preisliste: sofort). Pflicht: **clientseitiger Limiter** nach `x-ratelimit-remaining`/`-reset` statt nur Semaphore. Bei 429 den Zyklus abbrechen, alte Daten behalten, `UpdateFailed(retry_after)`. Rechnung: anonym reicht das für > 40 Stationen. BBox-Hybrid (Alternative A) frühestens v1.1 (Bedingungen: Gruppierung nach Nähe, nur genutzte Kraftstoffe, `count < limit` prüfen, Fallback auf Einzelabfragen).
- **W6 Datenalter (v1):** Diagnose-Sensor „Neueste Preismeldung“ = Maximum von `validFrom` über alle Stationen (nicht `lastSeenAt`: das ist ein Import-Zeitstempel, live bei allen Stationen identisch). Repair-Issue, wenn tagsüber (z. B. 06–22 Uhr) länger als 3 h keine Preismeldung eintrifft. Betreiber nach einem Aktualitätsfeld (z. B. in `/health`) fragen.
- **W7 Quelle:** README benennt offen, dass TankPuls laut Doku Tankerkönig als Datenquelle nutzt. Am 2026-10-20 waren die TankPuls-Preise aktuell (neueste `priceTs` ≈ Abfragezeit), obwohl die Tankerkönig-API ausfiel; das ist keine Zusicherung. W6 macht eine veraltete Quelle sichtbar.
- **W8 Migration:** Anleitung: Tankerkönig entfernen, neue Entities auf alte `entity_id` umbenennen (gleiche Einheit/`state_class` → Statistiken laufen weiter). Attributnamen wie Tankerkönig (`fuel_type`, `station_name`, `brand`, `street`, `house_number`, `postcode`, `city`; lat/lon nur bei `show_on_map`). Hilfsdienst per `externalId` in v1.1.
- **W9 Fakten ergänzt:** BBox-`limit` Default 200/Max 1000; `x-ratelimit-reset` = Sekunden bis neues Fenster; Auth-Präfix `ApiKey` alternativ; `/api/stats` hat ein weiteres Zeitformat (nicht genutzt).

### Nice-to-have (umsetzen, soweit aufwandsarm)
Zeit-Parsing via `dt_util.parse_datetime` (Tests mit 3/6 Nachkommastellen, `+00`, `Z`); `_attr_attribution`; `_unrecorded_attributes` für statische Attribute; Manifest `integration_type: service` + `loggers`; `hacs.json` mit HA-Mindestversion (2025.x wegen `UpdateFailed(retry_after)`); Tests mit syrupy-Snapshots und `freezer`, Fixtures aus Live-Antworten (inkl. `items:[]`, `isActive:false`, `no_prices`); Binary-Sensor-Device-Class beim Bau mit dem Quellcode von `tankerkoenig` abgleichen; Standardintervall 10 min (fest wählbar 5–60, mit Key auto-begrenzt).

### Gesamturteil des Reviews
Nicht freigabefähig in der Ursprungsform, freigabefähig mit den obigen Auflagen. Die Auflagen 1–6 sind oben umgesetzt; Auflage 7 (Rückfrage beim Betreiber) ist **nicht Teil der Entwicklung** und liegt bei dir.
