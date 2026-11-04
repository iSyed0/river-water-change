# Pakistan Surface Water Observatory

An interactive globe portal for NDMA showing where surface water is found across
Pakistan and how it has changed over 1984–2024. It is built on the 30 m JRC Global
Surface Water v1.5 rasters in this folder.

## Run it

```bash
python serve.py
```

Open http://127.0.0.1:8765. The page has to come from a web server; opening
`index.html` directly from disk won't load the data.

## What's in the portal

- **Globe with an opening fly-in**, night, satellite and terrain basemaps, and optional 3D terrain.
- **Six water layers:** maximum extent, occurrence, seasonality, recurrence, occurrence change, and transitions.
- **Pixel inspector:** click anywhere to read the 30 m values of all six layers. A click on dry ground snaps to the nearest water pixel and shows how far away it is.
- **Insights panel:** national, province and district figures for maximum extent, permanent, seasonal, gained and lost water. It also shows the 1984→2024 transition mix and a sortable ranking.
- **Overlays:** focus mask outside Pakistan, province and district boundaries, a district water-index choropleth, and labels.
- **Rivers and canals** from `WaterWays_Pakistan.geojson`: rivers, canals, nalas/streams, qanats
  and the unnamed network, each styled differently and labelled along their course.
  Hovering or clicking shows the waterway's name.
- **Indus floodplain flythrough** (the *Indus 3D* button): one click switches to satellite
  imagery with 3D terrain and the erosion layer, dims everything outside the floodplain, and
  flies down it from Kalabagh to the Arabian Sea. The slider also reaches the mountain reach
  upstream. Beside the flight:
  - **Cross-section** every 5 km, drawn on the map and charted: ground profile, water in
    1984–99 and 2000–24, what changed, land cover, and the roads, railways and villages on the
    line. Bank movement is shown for each side. Click a line on the map to read it.
  - **Area table** for the 20 km reach under the camera, the district on each bank and the
    whole river: erosion, farmland, settlements and infrastructure. *All areas* lists every
    district or reach and downloads as CSV.
  - **Infrastructure on the map:** main roads, railways, bridges, dams and weirs, schools and
    health facilities. Dragging the map pauses the flight. Keys: Space fly/pause, ←/→
    landmarks, O orbit, Esc exit.
- **Erosion layer** in the layer list: the same change map, usable outside the flythrough.
- **Villages, bastis and towns:** about 153,000 named settlements, each tagged with its
  district. Cities show from the national view, towns from zoom 7, and villages and bastis
  from zoom 10. Clicking the map also names the nearest settlement and its distance.
- **Buildings** (about 69 million footprints, Google Open Buildings + OSM) from NDMA's
  TileServer GL at `http://172.18.1.142:8081`, which has one tileset per district. They
  are drawn from zoom 12 (neighbourhood level) as 3D blocks at their estimated height,
  coloured by height. Footprints with no usable height are drawn as one storey (3 m).
  Picking a village in search tilts the map so the heights are visible. Clicking a
  building shows its estimated height and source. The portal reads the
  server's `data.json`, so districts added to the server appear without code changes.
- **Search** across all 7 provinces/territories, 126 districts, every settlement and every
  named river and canal. Press `/` to jump to it. Search ignores spelling differences
  between romanised names: *Basti Kanjal* finds *Basti Kunjal*. Adding the district name
  (`kanjal layyah`) narrows the results. Picking a village pins it, flies there and opens
  its district's figures.
- **Mobile layout** with Layers and Insights drawers.

## Layout

```
portal/                 static site (deploy this folder)
  index.html
  css/style.css
  js/config.js          Mapbox token, basemaps, layer text
  js/app.js
  data/                 boundaries, stats.json, palettes.json
  tiles/<layer>/{z}/{x}/{y}.png   z0–12, Pakistan only (~570 MB)
scripts/
  common.py             layer list, colour palettes, source grid
  prepare_boundaries.py boundaries_src/*.geojson -> portal/data/*.geojson
  build_tiles.py        GeoTIFFs -> portal/tiles (+ build/raw intermediates)
  compute_stats.py      district/province areas -> portal/data/stats.json
  prepare_rivers.py     WaterWays_Pakistan.geojson -> portal/data/waterways_*.geojson
  prepare_indus.py      Indus main stem -> portal/data/indus_route.json (3D flythrough)
  prepare_places.py     GeoNames + OSM settlements -> portal/data/places.json, places_major.geojson
  prepare_floodplain.py cross-section lines and floodplain edges (downloads the DEM)
  analyse_floodplain.py erosion, land cover and infrastructure per reach and district
  floodplain_common.py  readers for the JRC mosaic, Copernicus DEM and WorldCover
serve.py                local server (large connection queue, gzip for GeoJSON)
extent/ occurrence/ ... JRC source granules (60–80 E, 20–40 N)
erosion/                derived change raster, written by analyse_floodplain.py
boundaries_src/         Pakistan_National.geojson (official national boundary)
                        WaterWays_Pakistan.geojson (rivers, canals, streams)
                        + geoBoundaries gbOpen PAK ADM1/2 (provinces, districts)
osm_src/                settlement sources for prepare_places.py:
  geonames/PK.txt       https://download.geonames.org/export/dump/PK.zip
  places/gis_osm_places*_free_1.shp   from Geofabrik pakistan-latest-free.shp.zip
```

## What's in the repository

A fresh clone runs as it is: `portal/` includes its data and map tiles. The inputs for rebuilding
are not committed, because they are large public downloads: the JRC rasters (`change/`, `extent/`,
`occurrence/`, `recurrence/`, `seasonality/`, `transitions/`) and `osm_src/`. To rebuild, download
them to the paths under Layout first. `build/` is a cache that the scripts recreate.

## Rebuild the data

Run these whenever the source rasters, boundaries or colours change. They need Python
with `rasterio`, `numpy`, `pillow`, `shapely` and `pyproj`.

```bash
python scripts/prepare_boundaries.py   # seconds
python scripts/build_tiles.py          # ~3 min, 8 workers
python scripts/compute_stats.py        # ~15 s
python scripts/prepare_rivers.py       # ~10 s
python scripts/prepare_indus.py        # seconds, after prepare_rivers.py
python scripts/prepare_places.py       # ~15 s, after prepare_boundaries.py
python scripts/prepare_floodplain.py   # ~3 min first time, after prepare_indus.py
python scripts/analyse_floodplain.py   # ~5 min first time, after prepare_floodplain.py, prepare_places.py
python scripts/build_tiles.py --layers erosion   # after analyse_floodplain.py
```

The two floodplain scripts need internet access: they read the Copernicus DEM and ESA
WorldCover directly from their public buckets and cache them in `build/floodplain/`. Keep
that folder to make re-runs quick. Running `build_tiles.py` for all layers needs the
`erosion/` rasters, so do the floodplain scripts first.

Building counts come from the NDMA tile server when it answers during
`analyse_floodplain.py`. If it doesn't, the script falls back to OSM footprints, which cover
only a small fraction of real buildings, and the portal shows a warning beside the figures.
Re-run the script once the server is back; the result is then cached in
`build/floodplain/buildings.npz`.

`prepare_places.py` takes GeoNames as the main list of villages and bastis. It merges in
OpenStreetMap places, which supply more precise positions, the city and town grading, and
places GeoNames lacks. Same-name points within 2.5 km (8 km for towns) are counted as one
place. To refresh, download both sources again into `osm_src/` (see Layout) and re-run the
script. If a village is missing or misspelt, the lasting fix is to add or correct it in
OpenStreetMap; it will come through on the next refresh.

`prepare_indus.py` stitches the Indus pieces into one main stem and snaps the tour
landmarks onto it. Landmarks, their camera zoom/pitch and their one-line notes are in the
`STOPS` table at the top of the script.

The waterways file only carries a `name`, so `prepare_rivers.py` classes each line
from its name (River / Canal, Branch, Feeder, Wah / Nala, Khwar, Nadi / Qanat). To
reclassify a waterway, edit the `RIVERS` or `RENAME` tables at the top of the script.

`build_tiles.py --layers extent occurrence` rebuilds selected layers only. The
`build/` folder holds intermediate tiles and can be deleted once the build finishes.

## How the numbers are produced

- Areas are counted from 30 m pixels, each weighted by its true area at its latitude.
- The map follows the official boundary in `boundaries_src/Pakistan_National.geojson`
  exactly. The outer outline, the dimming outside it, and the water clipping use all
  features. The Pakistan–IIOJK internal border is dashed and IIOJK is labelled as
  disputed territory.
- National figures count only the features named `Pakistan` (field `Admin01_Na`), so
  water inside IIOJK is shown but not counted.
- Provinces and districts are geoBoundaries units clipped to the national boundary.
  The unclipped units run offshore and would count sea as water. Provinces add up to
  about 98% of the national water extent; the rest is national land with no
  geoBoundaries district, mostly in the far northeast.
- *Permanent* = permanent + new permanent + seasonal→permanent.
  *Seasonal* = seasonal + new seasonal + permanent→seasonal.
  *Gained* = new permanent + new seasonal. *Lost* = lost permanent + lost seasonal.
  All of these come from the JRC transitions layer (first year vs 2024).
- The map shows full detail from zoom 12 (~30 m). At lower zooms, a pixel's opacity
  reflects how much of it is water. That keeps rivers visible without overstating
  flooded area.

## How the erosion figures are produced

All inputs are free satellite-derived products. No raw scenes are classified here and nothing
is checked against field surveys, so treat the figures as a screening tool.

- **Floodplain:** on each cross-section, the JRC maximum water extent is followed outward from
  the main channel, allowing dry gaps of up to 1.5 km, until the ground rises 15 m above the
  channel or 30 km is reached. The edges are then smoothed along the river. This is the land the
  satellite record shows the river reaching, not a surveyed or legal boundary.
- **Eroded and regained land:** land counts as eroded if the JRC transitions layer shows it
  turning to water between the first year of the record and 2024, *and* the JRC occurrence-change
  layer shows occurrence up by at least 25 points. Regained land is the reverse. The second
  test matters: the first year is thinly observed, so its seasonal water is undercounted, and
  transitions alone made the channel appear to widen by ~750 m on both banks at once. About
  60% of apparent changes pass. The rest are reported as *wet at times, no lasting change*.
- **What can still pass that test:** flooding that recurs over many years, ponding and
  irrigation. "Land turned seasonally wet" means land now wet part of the year, not
  necessarily land washed away. "Land lost to permanent water" is the stronger evidence of
  erosion. "Eroded or newly wet ground" in the tables covers both.
- **1984–99 and 2000–24 water** is estimated as overall occurrence ∓ half the change, counting
  a pixel as wet at 25% or more. It is an estimate, because the two periods hold different
  numbers of images.
- **Bank movement** compares the outer edges of the main water belt in the two periods. It
  swings widely in braided reaches, so read it across several sections rather than from one.
- **Land cover** is ESA WorldCover 2021 at 10 m. Land already under permanent water by 2021
  shows as water, so what it used to be is unknown: the farmland figures for eroded ground
  mostly describe seasonally wet land.
- **Near the river** means within 1 km of water that was wet at least 50% of the time in 2000–24,
  leaving out features narrower than about 150 m so that canals and ponds don't count.
- **Infrastructure** comes from OSM, which is incomplete in rural areas: treat counts as
  minimums. Bridges are merged within ~300 m and include small canal bridges.
- **The cross-section chart is not a channel survey.** The Copernicus surface model includes
  trees and buildings and cannot see the river bed, so it shows the shape of the land, not depth.

## Before going live

- **Mapbox token:** in the Mapbox account, restrict the token in `js/config.js` to the portal's domain.
- **Boundaries:** the national boundary is official. Provinces and districts are still
  geoBoundaries open data. To use official ones, replace `pak_ADM1_full.geojson` /
  `pak_ADM2_full.geojson` (field `shapeName`) and re-run the three scripts.
- **Basemap borders:** Mapbox's own country borders, and its state labels inside the
  official outline, are hidden so they can't contradict the official boundary. The
  "Basemap borders" toggle brings the borders back.
- **Building server:** set in `buildings` in `js/config.js`. `172.18.1.142` is a private
  address, so buildings show only for viewers on the NDMA network; elsewhere the toggle
  says the server can't be reached and the rest of the portal works as normal. If the portal
  is served over **https**, browsers block tiles from this `http://` server (mixed
  content). The tile server then needs https too, e.g. behind the same reverse proxy.
- **Hosting:** `portal/` is fully static, so any web server (IIS, nginx, Apache, S3/CDN) can serve it.
  Turn on gzip for `.geojson`: the unnamed waterway network is 6.9 MB raw and 1.4 MB compressed.
  Missing tiles are normal: tiles are only written where there is water.

Data: EC JRC / Google, Global Surface Water v1.5 (Pekel et al., 2016, *Nature*).
Boundaries: official Pakistan national boundary; provinces/districts from geoBoundaries (CC BY 4.0).
Buildings: Google Open Buildings (CC BY 4.0) and © OpenStreetMap contributors (ODbL), served by NDMA.
Settlements: GeoNames (CC BY 4.0) and © OpenStreetMap contributors (ODbL), via Geofabrik.
Land cover: ESA WorldCover 2021 v200 (CC BY 4.0). Terrain: Copernicus DEM GLO-30 (© DLR, Airbus; ESA).
Infrastructure: © OpenStreetMap contributors (ODbL), via Geofabrik.
