/* Portal configuration. Colours live in data/palettes.json (written by scripts/build_tiles.py). */
window.PORTAL_CONFIG = {
  // Public Mapbox token. Restrict it to your portal's domain in the Mapbox account settings.
  mapboxToken: 'pk.eyJ1IjoiemVlc2hhbjEwIiwiYSI6ImNtMXN0YXVhbTBhYnIybHNhOHRheHRwOWoifQ.vgmSlaE3lAnZPy59Ni7SkQ',

  tilesPath: 'tiles',
  tileMaxZoom: 12,

  // Building footprints (Google Open Buildings + OSM) from NDMA's TileServer GL: one vector tileset per
  // district, each with a `buildings` layer. Tilesets are listed from the server's data.json at runtime.
  // Tiles below z12 run to ~450 KB, so buildings are drawn from minzoom.
  buildings: { server: 'http://172.18.1.142:8081', layer: 'buildings', minzoom: 12, maxzoom: 17 },

  // Opening globe position, then the view the intro flies to.
  start: { center: [38, 22], zoom: 1.3, pitch: 0, bearing: 0 },
  home: { center: [69.6, 30.1], zoom: 4.85, pitch: 32, bearing: -6 },

  basemaps: [
    { id: 'night', label: 'Night', style: 'mapbox://styles/mapbox/dark-v11', mask: 0.6 },
    { id: 'satellite', label: 'Satellite', style: 'mapbox://styles/mapbox/satellite-streets-v12', mask: 0.5 },
    { id: 'terrain', label: 'Terrain', style: 'mapbox://styles/mapbox/outdoors-v12', mask: 0.45 },
  ],

  layers: [
    {
      id: 'extent', short: 'Extent', name: 'Maximum Water Extent', tag: 'Ever observed · 1984–2024',
      desc: 'Every location where surface water was detected at least once in four decades of Landsat imagery: the outer envelope of rivers, lakes, reservoirs and floods.',
      legend: { type: 'single', label: 'Water detected at least once' },
    },
    {
      id: 'occurrence', short: 'Occurrence', name: 'Water Occurrence', tag: 'Frequency · % of observations',
      desc: 'How often water was present across all valid observations. Low values mark flood-prone ground; 100% marks permanent rivers, lakes and reservoirs.',
      legend: { type: 'ramp', min: 1, max: 100, labels: ['1%', '50%', '100%'], ends: ['Rarely wet', 'Always wet'] },
    },
    {
      id: 'seasonality', short: 'Seasonality', name: 'Seasonality', tag: 'Months of water · 2024',
      desc: 'Number of months water was present during the most recent year. Short durations show monsoon and flood-season water.',
      legend: { type: 'steps', min: 1, max: 12, labels: ['1 mo', '6', '12 months'] },
    },
    {
      id: 'recurrence', short: 'Recurrence', name: 'Recurrence', tag: 'Inter-annual return · %',
      desc: 'How reliably water returns from year to year. High recurrence with low occurrence points to areas flooded every monsoon.',
      legend: { type: 'ramp', min: 1, max: 100, labels: ['1%', '50%', '100%'], ends: ['Irregular', 'Every year'] },
    },
    {
      id: 'change', short: 'Change', name: 'Occurrence Change', tag: '1984–99 vs 2000–24',
      desc: 'Change in water occurrence between the two epochs. Red shows drying or loss; green shows new or more frequent water.',
      legend: { type: 'ramp', min: 0, max: 200, labels: ['−100%', '0', '+100%'], ends: ['Loss', 'Gain'] },
    },
    {
      id: 'transitions', short: 'Transition', name: 'Water Transitions', tag: 'Class change · 1984 → 2024',
      desc: 'How each pixel changed between the first and last year: permanent, seasonal, new, lost and ephemeral water.',
      legend: { type: 'classes' },
    },
    {
      id: 'erosion', short: 'Erosion', name: 'River Erosion & Accretion', tag: 'Indus floodplain · 1980s → 2024',
      desc: 'Where the Indus took land and gave it back since the 1980s. Only sustained change counts, confirmed by comparing the same months in 1984–99 and 2000–24. Shown inside the Indus floodplain only.',
      legend: { type: 'classes', classes: {
        1: 'Land lost to permanent water', 2: 'Land turned seasonally wet', 3: 'Regained from permanent water',
        4: 'Regained from seasonal water', 5: 'Water in both years', 6: 'Wet at times, no lasting change',
      } },
    },
  ],

  // Transition class names, mirrors scripts/common.py
  classes: {
    1: 'Permanent', 2: 'New permanent', 3: 'Lost permanent', 4: 'Seasonal', 5: 'New seasonal',
    6: 'Lost seasonal', 7: 'Seasonal → permanent', 8: 'Permanent → seasonal',
    9: 'Ephemeral permanent', 10: 'Ephemeral seasonal',
  },
};
