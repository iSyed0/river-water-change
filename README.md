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
- **Click any area** for its tehsil or union council and the effect of river change there: land taken and
  regained, plus buildings, villages, cropland, roads, railway, schools, health facilities and bridges in the
  floodplain, on eroded ground and near the river. The 30 m water values at that spot fold out below; a click on
  dry ground snaps to the nearest water pixel and shows how far away it is.
- **Insights panel:** national, province and district figures for maximum extent, permanent, seasonal, gained and lost water. It also shows the 1984→2024 transition mix and a sortable ranking.
- **Monthly Water layer (timeline, prototype):** any month from March 1984 to December 2021, picked with the slider under the
  map. Water is marked as usual for that calendar month or well above usual (the likely flood signal), and ground the
  satellite could not see is grey. The Insights panel charts the selected region's water month by month against its
  usual level. See *Timeline data* below.
- **Yearly Water layer (prototype):** any year from 1984 to 2021, sorted the way JRC's yearly classification sorts water:
  permanent water (wet in nearly every month it was seen: rivers, canals, lakes) bright and strong, seasonal water
  (floods, rain ponding, irrigation) pale, and seasonal water well above usual in two or more months of the year in
  orange, the likely flood. The Insights panel shows stacked bars per year for the selected region.
- **Water type overlays:** one switch each for river or canal, lake or reservoir, seasonal water near a river,
  seasonal water away from rivers, and coast and tidal water, shown over whichever layer is open. These are inferred,
  not observed: see *Water types* below. The Insights panel gives each type's area for the selected region.
- **Time covered:** every layer, tab and figure says what period it covers, and a label above the status bar names
  what the map shows now.
- **Map highlight:** selecting a province or district, or a tab in the Insights panel (Extent, Permanent, Seasonal,
  Gained, Lost), shows that tab's water on the map and dims everything outside the region. Picking a data layer
  or *Back to data layer* returns to the layer.
- **Overlays:** focus mask outside Pakistan, province, district and tehsil boundaries, a district water-index choropleth, and labels. Tehsil lines load from zoom 7.5 and their names show from zoom 9.
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
- **Blinking erosion dots** (an overlay, on by default): land eroded or newly wet, grouped so it can be seen
  when zoomed out. Bigger dots mean more area, and each label names the place and counts the buildings and cropland
  on that ground. A click lists the villages and infrastructure and pins the villages on the map. The
  dots fade out from zoom 8.5 to 10.
- **Land Loss & Deposition layer** (Sukkur reach, 20 m): sharper detail from Earth Engine exports in
  `GEE indus erosion/`, for about 100 km of river. The flight switches to it there, adds a strip to
  the cross-section, and puts its figures at the top of the table.
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
- **Search** across all 7 provinces/territories, 166 districts, every settlement and every
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
  prepare_erosion_dots.py eroded and newly wet ground with what stands on it -> portal/data/erosion_dots.geojson
  prepare_land_change.py Earth Engine exports -> portal/tiles/landloss, portal/data/land_change.json
  prepare_admin_units.py union councils or tehsils -> portal/data/admin_units.geojson
serve.py                local server (large connection queue, gzip for GeoJSON)
extent/ occurrence/ ... JRC source granules (60–80 E, 20–40 N)
erosion/                derived change raster, written by analyse_floodplain.py
boundaries_src/         Pakistan_National.geojson (official national boundary)
                        WaterWays_Pakistan.geojson (rivers, canals, streams)
boundries/              Pakistan_Provinces, _Districts, _Tehsils.geojson: full detail, about 600 MB, not in git
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
python scripts/prepare_boundaries.py   # ~1 min
python scripts/build_tiles.py          # ~3 min, 8 workers
python scripts/compute_stats.py        # ~15 s
python scripts/prepare_rivers.py       # ~10 s
python scripts/prepare_indus.py        # seconds, after prepare_rivers.py
python scripts/prepare_places.py       # ~15 s, after prepare_boundaries.py
python scripts/prepare_admin_units.py  # ~1 min, after prepare_boundaries.py
python scripts/prepare_timeline.py     # ~30 min, after prepare_boundaries.py; needs ndma water monthly/
python scripts/prepare_water_types.py  # after prepare_rivers.py, analyse_floodplain.py; then build_tiles.py --layers watertype
python scripts/prepare_floodplain.py   # ~3 min first time, after prepare_indus.py
python scripts/analyse_floodplain.py   # ~5 min first time, after prepare_floodplain.py, prepare_places.py
python scripts/build_tiles.py --layers erosion   # after analyse_floodplain.py
python scripts/prepare_erosion_dots.py # ~1 min, after analyse_floodplain.py
python scripts/prepare_land_change.py  # ~1 min, after analyse_floodplain.py
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
- Provinces, districts and tehsils come from `boundries/`, simplified for the browser with shared borders
  kept on one line. IIOJK is a unit in those files too; the portal leaves it out of them and draws it as
  disputed territory from the national boundary. A district's province and a tehsil's district are worked out
  from where they lie, because the labels in the files are inconsistent (`PUNJAB`, `Bahwalpur`, `na`).
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

## Water types

Satellites record that a pixel was wet, not why, so the JRC layers mix rivers, canals, lakes, floods, rain ponding and
flooded fields. `prepare_water_types.py` sorts water by location and by how often it is wet, across all of 1984-2024:

| Type | Rule, in order |
| --- | --- |
| Lake or reservoir | Inside an OpenStreetMap lake or reservoir outline |
| Coast and tidal water | Within 10 km of the sea, or in a mapped tidal flat, salt marsh or mangrove |
| River or canal | In a mapped riverbank, or wet at least 50% of the time and joined to a mapped river, canal, stream or drain |
| Lake or reservoir | Any other water wet at least 50% of the time |
| Seasonal water near a river | In the Indus floodplain or within 5 km of a mapped river |
| Seasonal water away from rivers | Everything else |

It has no dates, so it can't separate floods from rain ponding or flooded fields. That needs dated data: the timeline
and flood event layers.

## Timeline data

`GEE monthly water/monthly_water_export.js` exports JRC Monthly Water History v1.4 (Landsat, March 1984 to December
2021) from Earth Engine: for each month and each 0.0025 degree cell (about 280 m), the share seen as water and the share
seen at all. Put the exports in `ndma water monthly/` and run `prepare_timeline.py`:

1. **clean** joins the east and west halves Earth Engine wrote, crops to Pakistan, and keeps the water share of the part
   of each cell that was seen. A cell under half seen (cloud, no image) is *unseen*, never dry.
2. **normal** works out each cell's usual water share for each calendar month: the median of 2000-2021, from the years
   it was seen.
3. **yearly** classes each cell for each year: permanent when wet (at least 10% of the cell) in 90% or more of the
   months it was seen, seasonal otherwise, and seasonal above usual when at least 40 points over the usual share in two
   or more months. A cell seen in fewer than three months that year is unseen. Looser flood rules marked most years;
   this one finds 5.7 times as much in 2010 as in 2014. -> `portal/tiles/yearly/`, `portal/data/timeline_years.json`
4. **publish** writes the map tiles (`portal/tiles/monthly/`, three months per tile, one per colour channel) and
   `portal/data/timeline.json` (water seen, usual water on the same ground, water above usual, and the share seen, per
   month for Pakistan, each province and each district).

Water counts as *above usual* where a month's share beats the usual share for that calendar month by 25 points or more.
That is the likely flood signal, but rain ponding and flooded fields can look the same; rainfall records and NDMA's flood
maps are needed to tell them apart.

How much of Pakistan the satellite saw, on average per month:

| Years | Seen | Use |
| --- | --- | --- |
| 1984-1992 | 0-36%, many months unseen | Too thin for monthly maps |
| 1993-1999 | 12-68%, uneven | Some months only |
| 2000-2008 | 46-79% | Usable, with gaps |
| 2009-2021 | 72-87%, monsoon months mostly 82-97% | Good |

The record ends in December 2021, so the 2022 and 2025 floods are not in it.

## Union councils

The popup names a union council when `osm_src/union_councils/` holds a shapefile, and a tehsil otherwise
(`boundries/Pakistan_Tehsils.geojson`). The only complete public union council set is from
[Alhasan Systems on HDX](https://data.humdata.org/dataset/pakistan-union-council-boundaries-along-with-other-admin-boundaries-dataset).
It dates from 2017, was compiled from public sources, and isn't official; its publisher says it may not suit
legal, engineering or survey use. An official NDMA or Bureau of Statistics file is better if one is available.
After adding a file, re-run `prepare_admin_units.py`, `analyse_floodplain.py` and `prepare_land_change.py`.

## The Sukkur 20 m layer

`Indus_Land_Loss.tif`, `Indus_Land_Deposition.tif` and `Indus_Water_Extent.tif` are yes/no masks
exported from Google Earth Engine by `GEE indus erosion/indus_land_change.js`. It builds Sentinel-2
median composites for November–January of 2018–19 and 2025–26, and counts water at MNDWI above 0.05.
Land becoming water is loss; water becoming land is deposition. In the script, "2018" means the
winter starting that year, and `endMonth = 2` ends on 1 February, so February isn't included.

`prepare_land_change.py` keeps only the floodplain and drops patches under 0.2 ha. It works from the
rasters' own extent, so a larger export can replace them. If the dates or area change, update
`period`, `years` and `focus` on the `landloss` layer in `js/config.js`.

How to read it:
- Using the same low-flow months in both periods keeps monsoon floods out, but a different winter
  river level, which barrage operation also affects, still shows up as loss or deposition.
- Sand bars count as land, so loss includes bars in the active channel, not only farmland or banks.
- Seasonal channels that are dry in winter aren't counted as water in either period.
- The figures are not added to the JRC erosion totals, since the two cover different periods.

## Before going live

- **Mapbox token:** in the Mapbox account, restrict the token in `js/config.js` to the portal's domain.
- **Boundaries:** to change provinces, districts or tehsils, replace the files in `boundries/` (fields
  `Province`, `Districts`, `name`) and re-run the scripts from `prepare_boundaries.py` on.
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
Boundaries: official Pakistan national boundary; provinces, districts and tehsils from the project's boundary files.
Buildings: Google Open Buildings (CC BY 4.0) and © OpenStreetMap contributors (ODbL), served by NDMA.
Settlements: GeoNames (CC BY 4.0) and © OpenStreetMap contributors (ODbL), via Geofabrik.
Land cover: ESA WorldCover 2021 v200 (CC BY 4.0). Terrain: Copernicus DEM GLO-30 (© DLR, Airbus; ESA).
Infrastructure: © OpenStreetMap contributors (ODbL), via Geofabrik.
