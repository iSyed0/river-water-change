# Pakistan Surface Water Observatory: context for coding agents

Read this first, then `README.md`. The README has the full rebuild commands, the layout of every
script and the methods behind each figure. This file covers what the README doesn't: who the
work is for, the current state, the decisions not to undo, and the gotchas.

## What this is and who it's for

An interactive Mapbox GL globe portal for **NDMA Pakistan** (National Disaster Management
Authority). It shows where surface water is found across Pakistan and how it changed over
1984–2024, with river erosion along the Indus and what stands on newly wet ground. The audience
is a large organisation, so **visual polish and the accuracy of the figures both matter**. Keep
the dark-glass design language. Wording in the portal is plain English for non-specialists.
Every layer, tab and figure states the period it covers.

The user works at NDMA and is not a GIS specialist. Explain results in plain terms, flag what a
figure does *not* show (e.g. "buildings on wet ground" means exposed, not destroyed), and keep
answers short. **Commit or push only when asked.** Commit messages end with a
`Co-Authored-By:` line for the agent.

## Folder map

Reorganised on 2026-11-19 into frontend / backend / data / docs. Every pipeline path is defined once in
`backend/pipeline/common.py` (`ROOT`, `FRONTEND`, `DATA`, `JRC`, `DERIVED_DIR`, `CACHE_ROOT`, `OSM`, `DSDI_SRC`,
`EARTH_ENGINE`, `BOUNDARIES`, `ADMIN`, `UNION_COUNCILS`); use those, never a literal folder name.

| Path | What it is | In git? |
|---|---|---|
| `frontend/` | The website: `index.html`, `js/app.js` (all logic), `js/config.js` (layers, text, Mapbox token, buildings server, DSDI settings), `css/style.css`, `data/` (JSON/GeoJSON), `tiles/<layer>/{z}/{x}/{y}.png` | Yes, except `tiles/monthly/` and `tiles/source/` |
| `frontend/tiles/source/<river\|flood\|lakes>/` | Per-water-source copies of extent, occurrence, recurrence, change, transitions (~800 MB) | No: `build_tiles.py --group` |
| `frontend/tiles/monthly/` | Seasonality layer tiles, 3 months packed per PNG as RGB (~600 MB) | No: `prepare_timeline.py` |
| `frontend/tiles/yearly/` | Yearly Water tiles (layer switched off) | Yes |
| `backend/serve.py` | Portal server: serves `frontend/`, gzips JSON, relays NDMA DSDI under `/dsdi/`, runs layer downloads under `/api/export/`. `--lan` shares on the network | Yes |
| `backend/exports.py` | Layer downloads (GeoTIFF / GeoJSON / shapefile of any layer's parts for an area), cut from `data/`; also a CLI. Output cached in `data/cache/exports/` | Yes |
| `backend/pipeline/` | Python pipeline (see README "Rebuild the data" for order); run as `python backend/pipeline/<script>.py` | Yes |
| `backend/earth_engine/` | Earth Engine scripts: monthly water, 30 m yearly water, Sukkur Sentinel-2 land change | Yes |
| `Run Portal.bat`, `Share Portal on Network.bat` | Double-click launchers (this PC only / whole network); they run `backend\serve.py` | Yes |
| `data/jrc/{extent,occurrence,recurrence,seasonality,transitions,change}/` | JRC GSW v1.5 source granules, 60–80 E, 20–40 N | No |
| `data/derived/erosion/` | Derived change raster written by `analyse_floodplain.py` | Yes |
| `data/derived/watertype/` | Derived water-type raster written by `prepare_water_types.py` | No |
| `data/derived/occurrence_change/` | Whole-country Occurrence Change gain (GeoJSON, GeoTIFF), hard links to files in `data/cache/exports/` | No |
| `data/boundaries/official/` | Official national boundary, waterways | Yes |
| `data/boundaries/admin/` | Provinces, districts, tehsils from the user (~600 MB; was the misspelt `boundries/`) | No |
| `data/boundaries/union_councils/` | Union council shapefile from the user (PCO + Alhasan, ~2017), read by `prepare_admin_units.py` | No |
| `data/osm/` | GeoNames PK.txt, Geofabrik OSM shapefiles | No |
| `data/dsdi/` | NDMA DSDI schools, health facilities, embankments, districts, fetched by `dsdi_data.py` | No |
| `data/earth_engine/monthly/` | User's Earth Engine exports of **JRC Monthly History v1.4** (was `ndma water monthly/`; not NDMA data), 1984-03 to 2021-12 | No |
| `data/earth_engine/indus_erosion/` | The 20 m Sukkur Sentinel-2 exports | Yes |
| `data/earth_engine/yearly/` | For the 30 m yearly export, empty until the user runs it | No |
| `data/cache/` | `floodplain/` (DEM, WorldCover, buildings.npz, erosion_cells.npz), `timeline/` (cleaned monthly shares), `raw/` (intermediate tiles, safe to delete) | No |
| `data/unused/` | Old files the portal doesn't use (`rivers/`) | No |
| `docs/` | `sample_report_rajanpur/` (the user's printed report the district popup and report window copy), the portal PDF | Partly |
| `private/` | DSDI credentials | No: ignored |
| `vercel.json` | Points Vercel at `frontend/` | Yes |

## Running it

`python backend/serve.py` (or double-click `Run Portal.bat`) → http://127.0.0.1:8765. The page must come from a web server, not `file://`.
Python 3 with rasterio, numpy, pillow, shapely, pyproj, scipy, pyogrio is needed only to rebuild data.

## Where it's hosted (as of 2026-11-12)

- **GitHub:** `iSyed0/river-water-change` (public). `main` is the stable version; branch
  `monthly-yearly-prototype` is identical to it.
- **Vercel:** https://river-water-change.vercel.app, auto-deploys on push to `main`. Missing there:
  the Water source map tiles and Seasonality tiles (not in git, ~1.4 GB), and Buildings (private
  `http://` server, blocked on an https page). Options discussed with the user: pack tiles into a few
  large files (PMTiles-style) so they fit, or hide those features online. Not decided.
- **NDMA network:** `serve.py --lan` on the user's PC works, but Windows Firewall blocks inbound
  8765 and the user isn't an administrator. IT has to add the rule (command in README).
- **Planned:** move `frontend/` to an NDMA VM (it also runs Postgres) behind its web server. Postgres
  is **not** needed: the portal is static files, except the DSDI layers, which need `backend/serve.py`'s `/dsdi/`
  relay (run it there or reproduce the routes). Waiting on the VM's address, OS and upload method.
- **DSDI layers** (added 2026-11-18): NDMA's DSDI GeoServer catalogue (`172.18.7.13:8080/dsdi`, guest login
  `guest`/`guest`, GeoServer `172.18.7.35:8080`) is relayed by `backend/serve.py` because DSDI blocks other origins (CORS).
  The DSDI API relays GetMap only; legends and click info go to GeoServer directly. See README "NDMA DSDI layers".

## Data and method decisions (don't undo without asking)

- **Source data:** JRC Global Surface Water v1.5, 30 m, 1984–2024, read from local GeoTIFFs.
- **Erosion must be sustained:** JRC transitions alone overstate new water, because 1984 is thinly
  observed. Eroded/regained land must also pass the occurrence-change layer by ≥ 25 points
  (`SUSTAINED` in `analyse_floodplain.py`).
- **"Land lost" = erosion class 1 only (user's decision, 2026-11-20):** dry in the 1980s, under water all year now,
  green (≥ +25) in Occurrence Change. Class 2 (now flooded part of the year) is shown beside it as "flooded part of the
  year, not counted as lost", never added in. District reports, slides and their buildings, cropland, union council,
  tehsil and embankment figures all follow this; "gave back" is the mirror (class 3 only). The 2016–2020 monthly figure
  uses the all-seen-months share for the same reason. The erosion dots and Indus 3D tables still show both kinds,
  labelled separately. Counting any green pixel (> 0) was considered and rejected: it raises the figures.
- **Water sources** (`WATER_GROUPS` in `backend/pipeline/common.py`): river = types 1, 3; floods and rain = 4;
  lakes and coast = 2, 5. Types are *inferred* from location (Indus floodplain or within 5 km of a
  mapped river) and wetness, not observed causes. The three sources sum exactly to the totals.
- **Client-side class filtering** uses Mapbox `raster-color` / `raster-color-mix` on palette tiles
  (`CLASS_MIX` in `app.js`); per-source tiles are swapped with `source.setTiles`.
- **Layer flags in `config.js`:** `off` (removed at load), `card: false` (data kept, no card),
  `sourced` (has per-source tiles), `timeline: 'month'|'year'`, `prototype` (badge), `overlay`.
- **Layers now shown:** Maximum Extent, Seasonality (monthly), Occurrence, Recurrence, Occurrence
  Change, River Erosion & Accretion, Land Loss & Deposition. Removed at the user's request: Water
  Transitions card (data kept for the Insights tabs), one-year Seasonality, Basemap borders, District
  water index, Water dynamics, water-type overlay switches. Yearly Water is `off: true`, to return later.
- **Monthly data is reliable only from about 2000**; earlier months are sparsely observed.
- **District click = district report** (`prepare_district_reports.py` -> `data/district_reports.json`), laid out like
  the user's printed Rajanpur report (`docs/sample_report_rajanpur/*.jpg`). It replaced the old tehsil effects table in the popup.
  Villages are named places with nearest-place ground (no mouza boundaries); risk rules are `RISK_RULES`. Projected
  erosion by 2035 is not available and is stated as missing, not estimated. Embankments come from DSDI protection bands.
- **Building counts** in popups come from NDMA's tile server (`http://172.18.1.142:8081`, Google Open
  Buildings + OSM), cached in `data/cache/floodplain/buildings.npz`. Popups say *exposed, not destroyed*
  and split buildings into ground now wet all year vs flooded part of the year.
- **Mapbox borders are always hidden** so they can't contradict the official national boundary.
  Records containing "Occupied" are excluded from admin boundaries (`read_admin` in `common.py`).
- The user rejected a change that faded flood/rain water to make rivers stand out: don't reintroduce it.

- **District reports (2026-11-18):** under 1 acre counts as no loss and shows as 0, so counts match tables; the
  2016–2020 figure is a net change per cell (summing yearly cuts double-counted a shifting river); a "Land lost by
  union council" table is measured inside UC polygons and adds up to the district totals. No public mouza boundary
  source exists (PLRA / Boards of Revenue / PBS hold them); don't invent one. Schools and health facilities come from
  DSDI registers (Schools_QC, Medical_Facilities_QC; OSM only as fallback), each report lists embankments the river
  has reached, and notes where DSDI's district line moves acres between districts. The DSDI download route caps at
  50,000 features, so `dsdi_data.py` pages GeoServer WFS for larger layers.

- **River reports (2026-11-23):** Insights has a "River reports" tab (`prepare_river_reports.py` -> `data/river_reports.json`).
  The Indus = sum of the district reports. Other rivers = ground within 5 km of the mapped line, outside the Indus
  floodplain, nearest river wins, canals (90 m) and reservoirs (water type 2) left out; reservoirs reported on their own.
  Lower Jhelum traced along JRC occurrence to Trimmu (unnamed in WaterWays). Tributary buildings are `null` until the
  NDMA building server is reachable (never estimated from OSM); cached in `data/cache/rivers/buildings.npz` since 2026-11-23.
- **DSDI facilities (2026-11-23):** "in the river's path" (district and river reports) uses DSDI Schools_QC,
  Medical_Facilities_QC, Police_Stations_QC, Rescue_Stations_QC, Railway_Stations_QC. Checked and not used: DSDI
  settlements (same NGA gazetteer as GeoNames), osm_roads (OSM itself), Roads_NHA_QC (national highways only),
  Bridges_QC (a bridge point always sits over water, so "in the path" would mislead).
- **Layer downloads (2026-11-23):** every layer card has a download panel (`backend/exports.py` via `serve.py`). Occurrence
  Change parts are gain (green) and loss (red), with a "25 points or more" option; vectors are capped at 1.5 M polygons.

- **Erosion outlook (2026-11-23, in testing):** `prepare_erosion_outlook.py`, an empirical 10-year chance of loss per
  pixel (features: distance to permanent water, distance to water of 2 of 3 years, recent activity), total scaled to the
  long-run decade rate. Hindcast AUC 0.86, total +11%. Not in the portal yet: waiting for the user's Earth Engine export
  `recent_water_yearly_export.js` (2021–2025) so the outlook runs 2025→2035; its 2021 must first be compared with JRC
  2021. Word it as "at risk if the river keeps behaving as it has", never a forecast.

## Gotchas

- `backend/serve.py` refuses to start if its port is taken (Windows SO_REUSEADDR let several copies share 8765 and the
  oldest kept answering with old code). If the portal seems stale, end every `python.exe` and start it once.

- Windows PC. Many files use CRLF: preserve line endings when patching. Bash heredocs with quotes
  are fragile here; writing a small Python patch script is more reliable.
- Memory: `prepare_water_types.py` ran out of memory with 5 workers; use 3. `prepare_timeline.py`
  processes in strips for the same reason.
- A PostGIS install sets `PROJ_LIB` to an old database; `floodplain_common.py` clears it.
- The NDMA building server drops off the network at times. `analyse_floodplain.py` then falls back to
  OSM footprints (~2,200 vs ~902,000); re-run it when the server is back.
- Overpass API is blocked on the NDMA network; Geofabrik and GeoNames downloads work. HDX blocks
  scripted downloads (WAF challenge).
- Missing tiles return 404 by design: tiles exist only where there is water.
- Headless Chrome tests need `--use-angle=swiftshader --enable-unsafe-swiftshader`; clicks on the map
  need a `mouseMoved` event first.

## Open items

- Hosting: VM move, or pack tiles for Vercel (see above).
- Union councils are in (from the user's `data/boundaries/union_councils/`, PCO + Alhasan, ~2017, 4 provinces only) as their own
  overlay + popup name + village column. `admin_units.geojson` stays tehsils on purpose: unit_effects, the reports'
  tehsil tables and the tehsil overlay use it. An official NDMA union council file would be better if one exists.
- Boundary credit: the source of `data/boundaries/admin/` isn't stated yet, so the page credit is neutral.
- NDMA flood maps (2010, 2022, 2025) and rainfall records may come from the user into `data/flood_events/`.
- Re-enable Yearly Water later; cropland on wet ground isn't split by permanent/seasonal yet (needs a
  full `analyse_floodplain.py` re-run).
- Possibly unused files to delete (ask first): `data/boundaries/official/pak_ADM*`, `data/osm/tehsils/pak_adm3.geojson`, `data/unused/rivers/`.
