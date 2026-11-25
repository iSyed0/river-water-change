# Pakistan Surface Water Observatory

An interactive globe portal for NDMA showing where surface water is found across
Pakistan and how it has changed over 1984–2024. It is built on the 30 m JRC Global
Surface Water v1.5 rasters in this folder.

## Run it

```bash
python backend/serve.py
```

Open http://127.0.0.1:8765. The page has to come from a web server; opening
`index.html` directly from disk won't load the data.

### Share it on the NDMA network

Double-click `Share Portal on Network.bat` (or run `python backend/serve.py --lan`). Other PCs on the
network open the address it prints, e.g. http://172.18.1.117:8765, while that window stays open.
Windows Firewall must allow inbound TCP 8765 once, from an administrator prompt:

```
netsh advfirewall firewall add rule name="Pakistan Surface Water Observatory (TCP 8765)" dir=in action=allow protocol=TCP localport=8765 profile=domain,private
```

## What's in the portal

- **Globe with an opening fly-in**, night, satellite and terrain basemaps, and optional 3D terrain.
- **Water layers:** maximum extent, seasonality (month by month), occurrence, recurrence and occurrence change. The
  one-year Months of Water and the Yearly Water layers are switched off with `off: true` in `js/config.js`; Water
  Transitions has no card (`card: false`) but its tiles feed the Insights tabs.
- **Click a district** for its river-change report, laid out like NDMA's printed district reports (Rajanpur
  sample in `docs/sample_report_rajanpur/`): headline figures, the 15 most affected villages with bars for the share of each village
  lost since the 1980s and cut in 2016–2020, numbered pins on the map, and *Download data (CSV)*. *Full district
  report* opens a window with In short, land cut each year (chart), villages by risk, by tehsil, every village at
  risk, and places in the river's path, each with a CSV button, plus *Print or save as PDF*. Click a village or
  place to fly to it on the erosion layer. Districts away from the Indus say they have no report. The 30 m water
  values at the spot fold out below; a click on dry ground snaps to the nearest water pixel. See *District
  reports* below.
- **Insights panel:** national, province and district figures for maximum extent, permanent, seasonal, gained and lost water. It also has the water source switch and a sortable ranking.
- **Seasonality layer (monthly water):** any month from March 1984 to December 2021, picked with the slider under the
  map. Water is marked as usual for that calendar month or well above usual (the likely flood signal), and ground the
  satellite could not see is grey. The Insights panel charts the selected region's water month by month against its
  usual level. See *Timeline data* below.
- **Yearly Water layer (prototype; switched off for now with `off: true` in `js/config.js`):** any year from 1984 to 2021, sorted the way JRC's yearly classification sorts water:
  permanent water (wet in nearly every month it was seen: rivers, canals, lakes) bright and strong, seasonal water
  (floods, rain ponding, irrigation) pale, and seasonal water well above usual in two or more months of the year in
  orange, the likely flood. The Insights panel shows stacked bars per year for the selected region.
- **Water source** (Insights panel): All water, River system, Floods and rain, or Lakes and coast. Picking one shows
  only that water on Extent, Occurrence, Recurrence and Occurrence Change, and switches the figures, tabs and rankings
  to it. River system is river channels and canals with the seasonal water of their beds (in the Indus floodplain or
  within 5 km of a mapped river); Floods and rain is seasonal water away from rivers. Each of those layers has a copy
  per source in `frontend/tiles/source/<source>/`, written by `build_tiles.py --group`.
- **Water type in the popup:** clicking the map names the likely water type there (river or canal, lake or
  reservoir, seasonal water near a river, seasonal water away from rivers, coast and tidal water). These are inferred,
  not observed: see *Water types* below.
- **Time covered:** every layer, tab and figure says what period it covers, and a label above the status bar names
  what the map shows now.
- **Map highlight:** selecting a province or district, or a tab in the Insights panel (Extent, Permanent, Seasonal,
  Gained, Lost), shows that tab's water on the map and dims everything outside the region. Picking a data layer
  or *Back to data layer* returns to the layer.
- **Overlays:** focus mask outside Pakistan, province, district, tehsil and union council boundaries, and labels. Tehsil lines load from zoom 7.5 and their names show from zoom 9; union council lines from zoom 9 and their names from zoom 10.5. The popup names the union council and tehsil clicked in.
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
  `data/earth_engine/indus_erosion/`, for about 100 km of river. The flight switches to it there, adds a strip to
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
frontend/               the website (deploy this folder; Vercel serves it)
  index.html
  css/style.css
  js/config.js          Mapbox token, basemaps, layer text, DSDI settings
  js/app.js
  data/                 boundaries, stats.json, palettes.json, district reports ...
  tiles/<layer>/{z}/{x}/{y}.png   z0–12, Pakistan only (~570 MB)
backend/
  serve.py              the portal server: serves frontend/, relays NDMA DSDI under /dsdi/, cuts layer downloads under /api/export/
  exports.py            layer downloads: any layer's parts for an area as GeoTIFF, GeoJSON or shapefile (see "Layer downloads")
  pipeline/             Python that builds frontend/data and frontend/tiles from data/
    common.py             paths, layer list, colour palettes, source grid
    prepare_boundaries.py data/boundaries -> frontend/data/*.geojson
    build_tiles.py        GeoTIFFs -> frontend/tiles (+ data/cache/raw intermediates)
    compute_stats.py      district/province areas -> frontend/data/stats.json
    prepare_rivers.py     WaterWays_Pakistan.geojson -> frontend/data/waterways_*.geojson
    prepare_indus.py      Indus main stem -> frontend/data/indus_route.json (3D flythrough)
    prepare_places.py     GeoNames + OSM settlements -> frontend/data/places.json, places_major.geojson
    prepare_floodplain.py cross-section lines and floodplain edges (downloads the DEM)
    analyse_floodplain.py erosion, land cover and infrastructure per reach and district
    floodplain_common.py  readers for the JRC mosaic, Copernicus DEM and WorldCover
    dsdi_data.py          fetches NDMA DSDI schools, health facilities, embankments, districts -> data/dsdi/
    prepare_erosion_dots.py eroded and newly wet ground with what stands on it -> frontend/data/erosion_dots.geojson
    prepare_district_reports.py villages, union councils, risk, embankments -> frontend/data/district_reports.json
    prepare_river_reports.py land lost along each river, district by district -> frontend/data/river_reports.json
    prepare_land_change.py Earth Engine exports -> frontend/tiles/landloss, frontend/data/land_change.json
    prepare_admin_units.py tehsils -> frontend/data/admin_units.geojson; union councils -> frontend/data/union_councils.geojson
    prepare_timeline.py   monthly exports -> frontend/tiles/monthly, frontend/data/timeline.json
    prepare_water_types.py water types -> data/derived/watertype, frontend/data/water_types.json
  earth_engine/         Google Earth Engine scripts (paste into code.earthengine.google.com)
    monthly_water_export.js   JRC Monthly History -> data/earth_engine/monthly/
    yearly_water_export.js    JRC Yearly History, 30 m, floodplain -> data/earth_engine/yearly/
    indus_land_change.js      Sentinel-2 Sukkur reach -> data/earth_engine/indus_erosion/
data/                   inputs and caches (most of it not in git: see .gitignore)
  jrc/                  JRC source granules (60–80 E, 20–40 N): extent/ occurrence/ recurrence/ seasonality/ transitions/ change/
  boundaries/
    official/           Pakistan_National.geojson (official national boundary), WaterWays_Pakistan.geojson
    admin/              Pakistan_Provinces, _Districts, _Tehsils.geojson: full detail, about 600 MB
    union_councils/     Union_Council.shp from the user (PCO + Alhasan, ~2017)
  osm/                  geonames/PK.txt (https://download.geonames.org/export/dump/PK.zip),
                        places/ and infra/ from Geofabrik pakistan-latest-free.shp.zip
  dsdi/                 NDMA DSDI layers fetched by dsdi_data.py
  earth_engine/         exports of the Earth Engine scripts: monthly/, indus_erosion/, yearly/
  derived/              rasters the pipeline writes: erosion/ (in git), watertype/
  cache/                floodplain/ (DEM, WorldCover, buildings), timeline/, raw/: safe to delete, rebuilt
  unused/               old files the portal doesn't use
docs/                   the sample Rajanpur report the district reports follow, the portal PDF
private/                credentials (DSDI): never committed
Run Portal.bat          double-click: start the portal on this PC
Share Portal on Network.bat   double-click: share it on the NDMA network
```

## What's in the repository

A fresh clone runs as it is: `frontend/` includes its data and map tiles. The inputs for rebuilding
are not committed, because they are large public downloads: the JRC rasters (`data/jrc/`) and `data/osm/`.
To rebuild, download them to the paths under Layout first. `data/cache/` is a cache that the pipeline recreates.

## Rebuild the data

Run these whenever the source rasters, boundaries or colours change. They need Python
with `rasterio`, `numpy`, `pillow`, `shapely` and `pyproj`.

```bash
python backend/pipeline/prepare_boundaries.py   # ~1 min
python backend/pipeline/build_tiles.py          # ~3 min, 8 workers
python backend/pipeline/compute_stats.py        # ~15 s
python backend/pipeline/prepare_rivers.py       # ~10 s
python backend/pipeline/prepare_indus.py        # seconds, after prepare_rivers.py
python backend/pipeline/prepare_places.py       # ~15 s, after prepare_boundaries.py
python backend/pipeline/prepare_admin_units.py  # ~1 min, after prepare_boundaries.py; reads data/boundaries/union_councils/ if present
python backend/pipeline/prepare_timeline.py     # ~30 min, after prepare_boundaries.py; needs data/earth_engine/monthly/
python backend/pipeline/prepare_water_types.py  # after prepare_rivers.py, analyse_floodplain.py; then build_tiles.py --layers watertype
python backend/pipeline/build_tiles.py --group river --layers extent occurrence recurrence change transitions   # ~5 min each;
python backend/pipeline/build_tiles.py --group flood --layers extent occurrence recurrence change transitions   # after
python backend/pipeline/build_tiles.py --group lakes --layers extent occurrence recurrence change transitions   # prepare_water_types.py
python backend/pipeline/prepare_floodplain.py   # ~3 min first time, after prepare_indus.py
python backend/pipeline/analyse_floodplain.py   # ~5 min first time, after prepare_floodplain.py, prepare_places.py
python backend/pipeline/build_tiles.py --layers erosion   # after analyse_floodplain.py
python backend/pipeline/prepare_erosion_dots.py # ~1 min, after analyse_floodplain.py
python backend/pipeline/prepare_district_reports.py  # ~30 s, after analyse_floodplain.py, prepare_places.py, prepare_timeline.py
python backend/pipeline/prepare_river_reports.py     # ~2 min, after prepare_district_reports.py, prepare_water_types.py
python backend/pipeline/prepare_erosion_outlook.py   # ~3 min, the 2035 bank-erosion outlook (in testing, see below)
python backend/pipeline/prepare_land_change.py  # ~1 min, after analyse_floodplain.py
```

The two floodplain scripts need internet access: they read the Copernicus DEM and ESA
WorldCover directly from their public buckets and cache them in `data/cache/floodplain/`. Keep
that folder to make re-runs quick. Running `build_tiles.py` for all layers needs the
`data/derived/erosion/` rasters, so do the floodplain scripts first.

Building counts come from the NDMA tile server when it answers during
`analyse_floodplain.py`. If it doesn't, the script falls back to OSM footprints, which cover
only a small fraction of real buildings, and the portal shows a warning beside the figures.
Re-run the script once the server is back; the result is then cached in
`data/cache/floodplain/buildings.npz`.

`prepare_places.py` takes GeoNames as the main list of villages and bastis. It merges in
OpenStreetMap places, which supply more precise positions, the city and town grading, and
places GeoNames lacks. Same-name points within 2.5 km (8 km for towns) are counted as one
place. To refresh, download both sources again into `data/osm/` (see Layout) and re-run the
script. If a village is missing or misspelt, the lasting fix is to add or correct it in
OpenStreetMap; it will come through on the next refresh.

`prepare_indus.py` stitches the Indus pieces into one main stem and snaps the tour
landmarks onto it. Landmarks, their camera zoom/pitch and their one-line notes are in the
`STOPS` table at the top of the script.

The waterways file only carries a `name`, so `prepare_rivers.py` classes each line
from its name (River / Canal, Branch, Feeder, Wah / Nala, Khwar, Nadi / Qanat). To
reclassify a waterway, edit the `RIVERS` or `RENAME` tables at the top of the script.

`build_tiles.py --layers extent occurrence` rebuilds selected layers only. The
`data/cache/` folder holds intermediate tiles and can be deleted once the build finishes.

## How the numbers are produced

- Areas are counted from 30 m pixels, each weighted by its true area at its latitude.
- The map follows the official boundary in `data/boundaries/official/Pakistan_National.geojson`
  exactly. The outer outline, the dimming outside it, and the water clipping use all
  features. The Pakistan–IIOJK internal border is dashed and IIOJK is labelled as
  disputed territory.
- National figures count only the features named `Pakistan` (field `Admin01_Na`), so
  water inside IIOJK is shown but not counted.
- Provinces, districts and tehsils come from `data/boundaries/admin/`, simplified for the browser with shared borders
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

## District reports

`prepare_district_reports.py` builds the district popup and report window from data already in the project.
They follow the layout of the printed Rajanpur report, with these differences:

- **Villages are named places** (GeoNames and OpenStreetMap), not mouzas: there are no official village
  boundaries here. Each village's ground is the land within 3 km that is closer to it than to any other named
  place, so village areas and shares are approximate. Villages *near the river* are those within 3 km of the
  floodplain.
- **Land lost** is the erosion raster (sustained change since the 1980s, 30 m), each pixel given to the nearest
  village. District totals come from `floodplain.json`, so they match the rest of the portal.
- **Land cut each year** comes from the monthly record (`data/cache/timeline/share_*.tif`, ~275 m cells, 2000–2021):
  river share that is new in a year and still river the year after, inside the floodplain. It shows which years
  were worst; the acres are approximate. 2021 can't yet be confirmed by the year after and is drawn lighter.
  The years can't be added up, since ground the river leaves and retakes counts each time it is retaken.
- **Land newly under the river in 2016–2020** is the net change over the period, cell by cell: river at the end
  of 2020 (and still in 2021) that was not river in 2015. Same coarse cells, so an estimate.
- **Under 1 acre counts as no loss** and shows as 0, so the villages counted as having lost land are exactly the
  rows with land lost in the tables. Villages the district map places in a district but the tehsil map places in a
  neighbouring district's tehsil get their own row in the tehsil table, so its columns add up.
- **Land lost by union council** is measured inside the union council boundaries (`union_councils.geojson`), with
  no nearest-place step. Each district's pixels are given to districts exactly as `analyse_floodplain.py` does, so a
  district's rows (plus "Outside the union council map" and the councils under 1 acre) add up to its headline acres
  and buildings, to within rounding.
- **30 m yearly water:** `backend/earth_engine/yearly_water_export.js` exports JRC Yearly History (2000–2021, 30 m,
  floodplain only, on the portal's grid). Once its files are in `data/earth_engine/yearly/`, the yearly and 2016–2020
  figures can be measured at 30 m instead of estimated from 275 m cells.
- **Risk** uses the share of a village's ground lost since the 1980s and the share cut in 2016–2020. Very high:
  25% or more lost and 10% or more cut recently. High: 15% lost and 5% cut, or 40% lost, or 30% cut. Moderate: 5%
  either way. Low: any loss of an acre or more. The rules are `RISK_RULES` in the script. A guide, not a forecast.
- **Places in the river's path** are named places, and OSM schools and health facilities, whose point is on (or
  one pixel from) land the river took, or on ground wet in both periods.
- **Not included yet:** projected erosion by 2035 and embankments to check first. They need a bank-erosion
  forecast and an embankment map.

## River reports

The **River reports** tab of the Insights panel lists every main river by land lost since the 1980s, and the Indus
districts in order down the river; each opens a report. `prepare_river_reports.py` builds them with the same rules as
the district reports: land lost is erosion class 1 (dry in the 1980s, under water all year now, +25 points or more in
Occurrence Change); ground now flooded part of the year (class 2) is shown beside it, not counted as lost.

- **The Indus** adds up the district reports, so its figures are theirs (inside the Indus floodplain).
- **Other rivers** are measured on ground within 5 km of the river's line in the waterways map, the same reach as the
  "River system" water source, outside the Indus floodplain. Where two rivers' reaches meet, each pixel goes to the
  nearer river. Ground within 90 m of a named canal is left out, and land that went under a reservoir (OSM lake and
  reservoir outlines: Mangla, Mirani, Gomal Zam, Attabad) is shown on its own, not as land lost to the river.
- **The lower Jhelum** has no name in WaterWays_Pakistan below about 32.5° N, so its line is traced along the wettest
  ground of the JRC occurrence layer from there to Trimmu, where it meets the Chenab (`EXTEND` in the script).
- **Villages** are named places: each pixel goes to the nearest one within 3 km. Schools, health facilities and
  police, rescue and railway stations are the DSDI registers' points standing on land lost or newly flooded.
- **Buildings** for the other rivers need NDMA's building server (z15 tiles over the ground the rivers took, cached in
  `data/cache/rivers/buildings.npz`). When the server can't be reached the counts are left out and the report says so;
  re-run the script on the NDMA network to add them.
- On the map, the River Erosion layer covers the Indus floodplain only; the other rivers' reports open the Occurrence
  Change layer, where the same ground shows green.

## Bank-erosion outlook (in testing)

`prepare_erosion_outlook.py` estimates each dry floodplain pixel's chance of being lost to the Indus within 10 years,
from the yearly water record (`data/earth_engine/yearly/`: JRC Yearly History 2000–2021 from
`backend/earth_engine/yearly_water_export.js`; Landsat + Sentinel-2 2021–2025 from `recent_water_yearly_export.js`).
It is not a river model: it learns how often land was lost given its distance to the permanent channel, its distance
to water seen in 2 of the last 3 years, and how much land the river took nearby in the last 3 years, and applies those
rates to the land now. The total is set by the long-run rate (about 2.5% of the dry floodplain lost per decade, steady
since 2000); the features decide where.

Hindcast (learn 2003→2013, test 2011→2021): AUC 0.86; 62,330 acres expected against 56,201 lost; the riskiest 5% of
dry land (chance ≥ 12%) held 31% of the land lost, a sixth of it lost against 1 in 40 overall. Large districts within
about ±35%; it slightly over-predicts the mountain gorges (a few hundred acres where none were lost). Not yet in the
portal: it waits for the 2022–2025 export, so the outlook can start from 2025 and run to 2035.

## Layer downloads

Each layer card has **Download this layer**: choose its parts (Occurrence Change: gain in green, loss in red, and
whether only lasting change of 25 points or more; River Erosion: each class; Occurrence and Recurrence: four bands of
the share), the area (the map view, the district or province picked in Insights, or all of Pakistan), the format
(GeoTIFF, GeoJSON, zipped shapefile) and, optionally, to leave out patches under 0.5, 1 or 5 ha. Layers with a water
source follow the Water source filter; Seasonality uses the month on the map.

`backend/exports.py` cuts the file from the source data (JRC rasters at 30 m, the erosion raster, the monthly record at
~280 m, the Sukkur Sentinel-2 exports at 20 m), not from the map tiles, so values and resolution are the data's own.
GeoTIFFs keep the values (Occurrence Change as −100 to +100 points); polygons carry their part and their area in
hectares and are cut at 1° block edges. `backend/serve.py` runs each cut as a background job (`/api/export/start`,
`status`, `file`), two at a time, and keeps the files in `data/cache/exports/`, so the same cut again is instant.
Downloads need `backend/serve.py` and the source data in `data/`; the Vercel copy says they're unavailable. The same
cuts from the command line:

```
python backend/exports.py --list
python backend/exports.py change --parts gain --area pakistan --format geojson --min-ha 0.5
python backend/exports.py erosion --parts 1 --area district:85 --format shp
```

Whole-country Occurrence Change gain is in `data/derived/occurrence_change/` (not in git): every pixel (GeoJSON
404 MB), patches of 0.5 ha or more (262 MB), lasting gain of +25 points or more in patches of 0.5 ha or more (244 MB),
and a GeoTIFF (34 MB). A whole-country vector file of a common layer (Extent, Occurrence) can pass the 1.5 million
polygon limit; the panel then asks for a smaller area, small patches left out, or a GeoTIFF.

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

`backend/earth_engine/monthly_water_export.js` exports JRC Monthly Water History v1.4 (Landsat, March 1984 to December
2021) from Earth Engine: for each month and each 0.0025 degree cell (about 280 m), the share seen as water and the share
seen at all. Put the exports in `data/earth_engine/monthly/` and run `prepare_timeline.py`:

1. **clean** joins the east and west halves Earth Engine wrote, crops to Pakistan, and keeps the water share of the part
   of each cell that was seen. A cell under half seen (cloud, no image) is *unseen*, never dry.
2. **normal** works out each cell's usual water share for each calendar month: the median of 2000-2021, from the years
   it was seen.
3. **yearly** classes each cell for each year: permanent when wet (at least 10% of the cell) in 90% or more of the
   months it was seen, seasonal otherwise, and seasonal above usual when at least 40 points over the usual share in two
   or more months. A cell seen in fewer than three months that year is unseen. Looser flood rules marked most years;
   this one finds 5.7 times as much in 2010 as in 2014. -> `frontend/tiles/yearly/`, `frontend/data/timeline_years.json`
4. **publish** writes the map tiles (`frontend/tiles/monthly/`, three months per tile, one per colour channel) and
   `frontend/data/timeline.json` (water seen, usual water on the same ground, water above usual, and the share seen, per
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

Union council boundaries come from `data/boundaries/union_councils/Union_Council.shp` (5,884 polygons, supplied by the user;
`data/osm/union_councils/` is also searched). The file mixes census (PCO) and
[Alhasan Systems](https://data.humdata.org/dataset/pakistan-union-council-boundaries-along-with-other-admin-boundaries-dataset)
boundaries of about 2017, covers Punjab, Sindh, Khyber Pakhtunkhwa and Balochistan (not Gilgit-Baltistan, AJK or
Islamabad), and isn't an official NDMA set. `prepare_admin_units.py` writes them to `union_councils.geojson`
(5,460 units after merging pieces with the same name in one tehsil; ~2 MB gzipped, fetched only when zoomed in or on
a click). Each gets the district and tehsil of our boundary files that it mostly falls in. They are drawn as their own
overlay, named in the map popup, and given to each village and place in the district reports. The floodplain figures
and the reports' tehsil tables stay per tehsil. After replacing the file, re-run `prepare_admin_units.py` and
`prepare_district_reports.py`.

## The Sukkur 20 m layer

`Indus_Land_Loss.tif`, `Indus_Land_Deposition.tif` and `Indus_Water_Extent.tif` are yes/no masks
exported from Google Earth Engine by `backend/earth_engine/indus_land_change.js`. It builds Sentinel-2
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

## NDMA DSDI layers

The left panel's **NDMA DSDI layers** section lists the GeoServer layers of NDMA's Disaster Spatial Data
Infrastructure (http://172.18.7.13:8080/dsdi): about 460 layers in workspace `gcop`, such as flood extents
2010–2025, the 2026 river flood outlooks, protection bands, barrages, canals, boundaries and hazards. Viewers
search, or open a theme group, and switch layers on. Each switched-on layer has opacity, a "lighten" button for
layers drawn in black (they vanish on the dark map), a legend and zoom-to. Area (polygon) layers also get an
"outlines only" button: `backend/serve.py` then sends GeoServer its own style (`OUTLINE_SLD`, a thin pale line on a faint
dark edge) instead of the layer's fill. Layers in the Boundaries group start as outlines. Clicking the map adds what the
switched-on layers hold at that spot to the popup.

Browsers may not call DSDI from another site (CORS), so `backend/serve.py` relays it under `/dsdi/`:

| Route | Goes to |
|---|---|
| `dsdi/layers` | DSDI `/api/v1/geoserver/layers`, signed in as DSDI's public guest user (token renewed when it expires, list cached 10 min) |
| `dsdi/tile?layer=&bbox=` | DSDI `/api/v1/geoserver/wms-tile` (WMS GetMap, 256 px, EPSG:3857) |
| `dsdi/detail?layer=` | DSDI `/api/v1/geoserver/layers/detail`: bounds for zoom-to and to limit tile requests |
| `dsdi/legend?layer=`, `dsdi/info?layer=&bbox=` | GeoServer `http://172.18.7.35:8080/geoserver/wms` GetLegendGraphic / GetFeatureInfo directly: the DSDI API relays only GetMap |

Only layer names in the catalogue are accepted. The DSDI address and login can be changed with the environment
variables `DSDI_API`, `DSDI_USER` and `DSDI_PASSWORD`; the theme groups are `dsdi.groups` in `js/config.js`.

- DSDI is on the NDMA network only. On Vercel, or anywhere `backend/serve.py` isn't serving the page, the section says so
  and the rest of the portal works as normal.
- Dates and sources of DSDI layers are as their titles give them; the portal adds no figures from them.
- Some DSDI boundary layers may differ from the official boundary the portal uses.
- Many layers came from KML and carry no attributes; their click info says "present at this spot".

## Before going live

- **Mapbox token:** in the Mapbox account, restrict the token in `js/config.js` to the portal's domain.
- **Boundaries:** to change provinces, districts or tehsils, replace the files in `data/boundaries/admin/` (fields
  `Province`, `Districts`, `name`) and re-run the scripts from `prepare_boundaries.py` on.
- **Basemap borders:** Mapbox's own country borders, and its state labels inside the
  official outline, are always hidden so they can't contradict the official boundary.
- **Building server:** set in `buildings` in `js/config.js`. `172.18.1.142` is a private
  address, so buildings show only for viewers on the NDMA network; elsewhere the toggle
  says the server can't be reached and the rest of the portal works as normal. If the portal
  is served over **https**, browsers block tiles from this `http://` server (mixed
  content). The tile server then needs https too, e.g. behind the same reverse proxy.
- **Hosting:** `frontend/` is fully static, so any web server (IIS, nginx, Apache, S3/CDN) can serve it.
  Turn on gzip for `.geojson`: the unnamed waterway network is 6.9 MB raw and 1.4 MB compressed.
  Missing tiles are normal: tiles are only written where there is water. The one exception is the DSDI
  layers: they need the `/dsdi/` relay in `backend/serve.py`, so either run `backend/serve.py` behind the web server or
  reproduce its routes there (see "NDMA DSDI layers").

Data: EC JRC / Google, Global Surface Water v1.5 (Pekel et al., 2016, *Nature*).
Boundaries: official Pakistan national boundary; provinces, districts and tehsils from the project's boundary files.
Buildings: Google Open Buildings (CC BY 4.0) and © OpenStreetMap contributors (ODbL), served by NDMA.
Settlements: GeoNames (CC BY 4.0) and © OpenStreetMap contributors (ODbL), via Geofabrik.
Land cover: ESA WorldCover 2021 v200 (CC BY 4.0). Terrain: Copernicus DEM GLO-30 (© DLR, Airbus; ESA).
Infrastructure: © OpenStreetMap contributors (ODbL), via Geofabrik.
