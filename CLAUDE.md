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

| Path | What it is | In git? |
|---|---|---|
| `portal/` | The static site: `index.html`, `js/app.js` (all logic), `js/config.js` (layers, text, Mapbox token, buildings server), `css/style.css`, `data/` (JSON/GeoJSON), `tiles/<layer>/{z}/{x}/{y}.png` | Yes, except `tiles/monthly/` and `tiles/source/` |
| `portal/tiles/source/<river\|flood\|lakes>/` | Per-water-source copies of extent, occurrence, recurrence, change, transitions (~800 MB) | No: `build_tiles.py --group` |
| `portal/tiles/monthly/` | Seasonality layer tiles, 3 months packed per PNG as RGB (~600 MB) | No: `prepare_timeline.py` |
| `portal/tiles/yearly/` | Yearly Water tiles (layer switched off) | Yes |
| `scripts/` | Python pipeline (see README "Rebuild the data" for order) | Yes |
| `serve.py` | Local server, large connection queue, gzips JSON. `--lan` shares on the network | Yes |
| `Run Portal.bat`, `Share Portal on Network.bat` | Double-click launchers (this PC only / whole network) | Yes |
| `extent/ occurrence/ recurrence/ seasonality/ transitions/ change/` | JRC GSW v1.5 source granules, 60–80 E, 20–40 N | No |
| `erosion/` | Derived change raster written by `analyse_floodplain.py` | Yes |
| `watertype/` | Derived water-type raster written by `prepare_water_types.py` | No |
| `boundries/` (sic) | Provinces, districts, tehsils from the user (~600 MB). Name is misspelt on purpose: scripts use it | No |
| `boundaries_src/` | Official national boundary, waterways | Yes |
| `osm_src/` | GeoNames PK.txt, Geofabrik OSM shapefiles | No |
| `ndma water monthly/` | User's Earth Engine exports of **JRC Monthly History v1.4** (not NDMA data despite the name), 1984-03 to 2021-12 | No |
| `GEE monthly water/`, `GEE indus erosion/` | Earth Engine scripts that produced those exports; the erosion folder also holds the 20 m Sukkur exports | Yes |
| `build/` | Caches: `floodplain/` (DEM, WorldCover, buildings.npz, erosion_cells.npz), `timeline/` (cleaned monthly shares), `raw/` (intermediate tiles, safe to delete) | No |
| `vercel.json` | Points Vercel at `portal/` | Yes |

## Running it

`python serve.py` → http://127.0.0.1:8765. The page must come from a web server, not `file://`.
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
- **Planned:** move `portal/` to an NDMA VM (it also runs Postgres) behind its web server. Postgres
  is **not** needed: the portal is static files. Waiting on the VM's address, OS and upload method.

## Data and method decisions (don't undo without asking)

- **Source data:** JRC Global Surface Water v1.5, 30 m, 1984–2024, read from local GeoTIFFs.
- **Erosion must be sustained:** JRC transitions alone overstate new water, because 1984 is thinly
  observed. Eroded/regained land must also pass the occurrence-change layer by ≥ 25 points
  (`SUSTAINED` in `analyse_floodplain.py`).
- **Water sources** (`WATER_GROUPS` in `scripts/common.py`): river = types 1, 3; floods and rain = 4;
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
- **Building counts** in popups come from NDMA's tile server (`http://172.18.1.142:8081`, Google Open
  Buildings + OSM), cached in `build/floodplain/buildings.npz`. Popups say *exposed, not destroyed*
  and split buildings into ground now wet all year vs flooded part of the year.
- **Mapbox borders are always hidden** so they can't contradict the official national boundary.
  Records containing "Occupied" are excluded from admin boundaries (`read_admin` in `common.py`).
- The user rejected a change that faded flood/rain water to make rivers stand out: don't reintroduce it.

## Gotchas

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
- Union council boundaries: only public set is Alhasan Systems on HDX (2017, unofficial); the user has
  to download it in a browser into `osm_src/union_councils/`, then re-run `prepare_admin_units.py`,
  `analyse_floodplain.py`, `prepare_land_change.py`. Tehsils stand in for now.
- Boundary credit: the source of `boundries/` isn't stated yet, so the page credit is neutral.
- NDMA flood maps (2010, 2022, 2025) and rainfall records may come from the user into `flood_events/`.
- Re-enable Yearly Water later; cropland on wet ground isn't split by permanent/seasonal yet (needs a
  full `analyse_floodplain.py` re-run).
- Possibly unused files to delete (ask first): `boundaries_src/pak_ADM*`, `osm_src/tehsils/pak_adm3.geojson`, `rivers/`.
