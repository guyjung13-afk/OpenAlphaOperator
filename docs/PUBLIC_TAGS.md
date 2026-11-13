# Public tags

The Real Time Desk reads public feeds into one naming scheme. The map lives in
`spire_reactor/tags/schema.py`. Nothing here is a private PI catalog, a plant
list, or a warehouse extract.

## Name

```
{FAMILY}.{ENTITY}.{VARIABLE}
```

Segments are uppercase. Spaces and punctuation become `_`.

| Pattern | Example | Unit | What it is |
| --- | --- | --- | --- |
| `WX.{SITE}.{VAR}` | `WX.HOUSTON.TEMP_C` | `degC` | Open-Meteo temperature at the site label |
| `WX.{SITE}.{VAR}` | `WX.HOUSTON.WIND_KMH` | `km/h` | Open-Meteo wind |
| `WX.{SITE}.{VAR}` | `WX.HOUSTON.RH_PCT` | `%` | Open-Meteo relative humidity |
| `EIA.{SERIES}.{ID}` | `EIA.NG.RNGWHHD` | `USD/MMBtu` | EIA series `RNGWHHD` (Henry Hub daily spot) |
| `{ISO}.{NODE}.LMP` | `ERCOT.HB_HOUSTON.LMP` | `USD/MWh` | ERCOT hub or load-zone settlement point price |
| `{ISO}.{NODE}.LMP` | `PJM.WESTERN_HUB.LMP` | `USD/MWh` | PJM trading-hub LMP |

`WX_SITE` (default `HOUSTON`) is only a label for `DEMO_LAT` / `DEMO_LON`.
ERCOT names are the public hubs and load zones (`HB_HOUSTON`, `LZ_HOUSTON`, …).
PJM names are the public trading hubs (`WESTERN HUB`, `EASTERN HUB`,
`AEP-DAYTON HUB`, `DOMINION HUB`). Resource-node and bus-level names are not
stored.

## Time

- Samples are stored in UTC (`2026-11-13T23:30:00+00:00`).
- The desk displays that instant in `DESK_TIMEZONE` (default `America/Chicago`).
- EIA daily points use the series period date. The stored instant is
  `00:00:00Z` on that date, and the desk shows the date, not a shifted clock time.
- ERCOT dashboard timestamps are the interval ending, converted to UTC.
- A missing or failed source writes no number. The desk shows `source unavailable`.

## Quality

| Quality | Meaning |
| --- | --- |
| `good` | Value returned by the public feed |
| `unavailable` | Feed missing, blocked, or empty. Value is null |
| `synthetic` | Offline sample from the Synthetic button. Labeled in the UI and excluded from model queries |

## Query tools

`query_pi_tag(tag, start, end, agg)` and `list_tags(filter)` read this store.
`agg` is `raw`, `avg`, `min`, `max`, or `last`. `start` / `end` are UTC
ISO-8601, `now`, `-1h`, or `last 1 hour`. Naive timestamps are UTC.
