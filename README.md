# TankPuls für Home Assistant

Kraftstoffpreise (Super E5, Super E10, Diesel) und Öffnungsstatus deutscher Tankstellen über die
[TankPuls-API](https://api.tankpuls.de/docs). Funktional an die Core-Integration `tankerkoenig`
angelehnt, aber unabhängig von der Tankerkönig-API.

## Installation (HACS)

1. HACS → Benutzerdefinierte Repositories → `https://github.com/chlctt/ha-tankpuls` (Kategorie *Integration*).
2. TankPuls installieren, Home Assistant neu starten.
3. Einstellungen → Geräte & Dienste → Integration hinzufügen → **TankPuls**.

## Einrichtung

1. **API-Schlüssel (optional).** Standard ist *anonym*: TankPuls begrenzt dann pro IP-Adresse
   (Listen 60/min, Details 120/min), was für Home Assistant reicht. Der kostenlose
   *Community*-Tarif (30 Anfragen/min, 10.000/Monat) ist meist **schlechter** als anonym; ein Key lohnt
   sich nur bei geteilter IP (z. B. CGNAT). TankPuls ignoriert ungültige Schlüssel still und behandelt die
   Anfrage anonym – die Integration prüft das und meldet es als Reparatur-Hinweis.
2. **Standort/Radius** (bis 50 km) oder Postleitzahl, danach die Tankstellen auswählen.
3. Optionen: Abfrageintervall (5–60 min, Standard 10), Tankstellen auf der Karte anzeigen, Tankstellen ändern.
   Mit API-Key wird das Intervall automatisch so erhöht, dass höchstens 90 % des Monatskontingents verbraucht werden.

## Entitäten

Pro Tankstelle ein Gerät mit:

| Entität | Beschreibung |
|---|---|
| `sensor.<station>_super_e5` / `_super_e10` / `_diesel` | Preis in EUR/l (3 Nachkommastellen), nur für Kraftstoffe, die die Station meldet. Attribute: `brand`, `station_name`, `street`, `postcode`, `city`, `fuel_type`, `price_valid_from`, `external_id` (Tankerkönig-ID), bei „auf Karte anzeigen“ `latitude`/`longitude`. |
| `binary_sensor.<station>_open` | an = geöffnet, aus = geschlossen, unbekannt = keine Preismeldungen |

Zusätzlich ein Diagnose-Sensor **„Neueste Preismeldung“** (Zeitstempel der aktuellsten Preisänderung über alle
Stationen). Meldet tagsüber (6–22 Uhr) seit über 3 Stunden keine Station einen neuen Preis, erscheint ein
Reparatur-Hinweis „Keine aktuellen Preismeldungen“.

## Wichtig zur Datenquelle

TankPuls bezieht die MTS-K-Preise laut eigener Dokumentation **über Tankerkönig**. Bei einem Ausfall der
Tankerkönig-*API* waren die TankPuls-Preise im Test (2026-10-20) trotzdem aktuell; eine Zusicherung dafür gibt es
nicht. Der Diagnose-Sensor und der Reparatur-Hinweis machen eine veraltete Quelle sichtbar.

## Migration von `tankerkoenig`

Entity-IDs und Statistiken hängen an der `entity_id`:

1. Notiere die `entity_id`s der alten Tankerkönig-Sensoren (Preise werden dort ebenfalls in `EUR` mit
   `state_class: measurement` geführt, daher laufen Langzeitstatistiken weiter).
2. Entferne die Tankerkönig-Integration.
3. Richte TankPuls ein und benenne die neuen Entitäten auf die alten `entity_id`s um.

Das Attribut `external_id` enthält die Tankerkönig-Stations-ID; ein automatischer Import ist für eine spätere Version geplant.
Abweichung: Es gibt genau **einen** Eintrag (Key, Intervall) mit mehreren Tankstellen statt mehrerer Einträge.

## Hinweise zu Lizenz und Nutzung

- Preisdaten: Markttransparenzstelle für Kraftstoffe (MTS-K), CC BY 4.0 –
  „Quelle: MTS-K über [tankerkoenig.de](https://www.tankerkoenig.de), CC BY 4.0 · TankPuls“ (steht als Attribution an allen Entitäten).
- Nur zur Verbraucherinformation; Nutzung durch die Mineralölwirtschaft und Weiterveräußerung der Rohdaten sind
  laut [AGB](https://tankpuls.de/agb.html#api) ausgeschlossen.
- Inoffizielle Integration, nicht von TankPuls entwickelt.
- Der Code steht unter der [MIT-Lizenz](LICENSE); sie gilt nicht für die abgerufenen Preisdaten (siehe oben).

## Entwicklung

```bash
pip install -r requirements_test.txt
ruff check . && ruff format --check . && pytest
```

Konzept und Review: [`docs/KONZEPT.md`](docs/KONZEPT.md), API-Spezifikation: [`docs/tankpuls-openapi.json`](docs/tankpuls-openapi.json).
