/* Pakistan Surface Water Observatory — map + UI */
(() => {
  'use strict';

  const CFG = window.PORTAL_CONFIG;
  CFG.layers = CFG.layers.filter((L) => !L.off);
  mapboxgl.accessToken = CFG.mapboxToken;

  // ------------------------------------------------------------------ helpers
  const $ = (sel, root = document) => root.querySelector(sel);
  const el = (tag, attrs = {}, html = '') => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (html) n.innerHTML = html;
    return n;
  };
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const rgb = (c) => `rgb(${c[0]},${c[1]},${c[2]})`;
  const pct = (a, b) => (b > 0 ? (a / b) * 100 : 0);
  const isMobile = () => matchMedia('(max-width: 900px)').matches;
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

  const BASE = location.href.replace(/[?#].*$/, '').replace(/[^/]*$/, '');
  // A water source other than 'all' reads that source's copy of the layer, if it has one
  const tileUrl = (layer, z = '{z}', x = '{x}', y = '{y}', source = 'all') => {
    const L = CFG.layers.find((l) => l.id === layer);
    const dir = source !== 'all' && L?.sourced ? `source/${source}/${layer}` : layer;
    return `${BASE}${CFG.tilesPath}/${dir}/${z}/${x}/${y}.png`;
  };
  const EMPTY = { type: 'FeatureCollection', features: [] };

  function fmtArea(v) {
    if (v >= 1000) return Math.round(v).toLocaleString('en-US');
    if (v >= 100) return v.toFixed(0);
    if (v >= 10) return v.toFixed(1);
    return v.toFixed(2);
  }
  const fmtPct = (p) => (p >= 10 ? p.toFixed(0) : p >= 1 ? p.toFixed(1) : p.toFixed(2)) + '%';

  // ------------------------------------------------------------------ state
  const state = {
    layer: 'extent',
    opacity: 0.9,
    basemap: 'night',
    terrain: false,
    spin: false,
    overlays: { focus: true, rivers: true, erosionDots: true, buildings: true, provinces: true, districts: true, tehsils: true, ucs: true, choropleth: false, labels: true, borders: false },
    scope: { level: 'national' }, // | { level: 'province', name } | { level: 'district', id }
    river: null, // name of a waterway picked from search
    place: null, // village/town picked from search, pinned on the map
    metric: 'extent_km2',
    highlight: false, // the map shows the metric above, in the selected region, instead of the data layer
    source: 'all', // water source the sourced layers and the Insights figures show (CFG.sources)
    month: '2010_08', // shown by the Seasonality (monthly water) layer
    year: '2010', // shown by the Yearly Water layer
  };

  const OVERLAYS = [
    { id: 'focus', label: 'Focus on Pakistan', icon: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>' },
    { id: 'rivers', label: 'Rivers & canals', icon: '<path d="M3 8c3-2 5 2 9 0s6-2 9 0M3 14c3-2 5 2 9 0s6-2 9 0"/>' },
    { id: 'erosionDots', label: 'Eroded or newly wet', icon: '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="8"/>' },
    { id: 'buildings', label: 'Buildings', icon: '<path d="M4 20V9l6-4 6 4v11M16 20v-7h4v7M3 20h18M8 12h4M8 16h4"/>' },
    { id: 'provinces', label: 'Province boundaries', icon: '<path d="M4 6l5-2 6 2 5-2v14l-5 2-6-2-5 2z"/><path d="M9 4v14M15 6v14"/>' },
    { id: 'districts', label: 'District boundaries', icon: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 12h16M12 4v16"/>' },
    { id: 'tehsils', label: 'Tehsil boundaries', icon: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 9.3h16M4 14.6h16M9.3 4v16M14.6 4v16"/>' },
    { id: 'ucs', label: 'Union council boundaries', icon: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 8h16M4 12h16M4 16h16M8 4v16M12 4v16M16 4v16"/>' },
    { id: 'labels', label: 'Place labels', icon: '<path d="M5 7V5h14v2M9 19h6M12 5v14"/>' },
  ];

  const DATA_LAYERS = CFG.layers.filter((L) => !L.overlay);
  const CARD_LAYERS = DATA_LAYERS.filter((L) => L.card !== false);
  const PIXEL_LAYERS = CFG.layers.filter((L) => !L.timeline && L.card !== false); // their tiles encode values as palette colours
  const SOURCES = new Map(CFG.sources.map((s) => [s.id, s]));

  // Monthly Water: tile channel values are 1 not seen, 2 dry, 3-102 usual water, 103-202 water above usual (2 + or 102 + the % of the cell)
  const MONTHLY = { usual: '#38bdf8', above: '#f97316', unseen: 'rgba(148, 163, 184, 0.55)' };
  const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  const MONTHLY_PLOT_SEEN = 50; // % of a region seen before a month or year is charted
  const YEARLY = { permanent: '#258cff', seasonal: '#a5b4dc', above: '#f97316', unseen: 'rgba(148, 163, 184, 0.55)' };
  const yearlyUrl = () => `${BASE}${CFG.tilesPath}/yearly/${state.year}/{z}/{x}/{y}.png`;
  const monthLabel = (key) => `${MONTH_NAMES[Number(key.slice(5)) - 1]} ${key.slice(0, 4)}`;
  const monthlyUrl = () => {
    const [y, m] = state.month.split('_').map(Number);
    return `${BASE}${CFG.tilesPath}/monthly/${y}_Q${Math.ceil(m / 3)}/{z}/{x}/{y}.png`;
  };
  const monthChannel = () => [0, 1, 2].map((c) => Number(c === (Number(state.month.slice(5)) - 1) % 3)).concat(0);
  function monthlyColorMap() {
    const v = (byte) => byte / 255;
    const clear = 'rgba(0, 0, 0, 0)';
    return ['interpolate', ['linear'], ['raster-value'],
      0, clear, v(1), MONTHLY.unseen, v(2), clear,
      v(3), 'rgba(56, 189, 248, 0.4)', v(102), 'rgba(56, 189, 248, 1)',
      v(103), 'rgba(249, 115, 22, 0.55)', v(202), 'rgba(249, 115, 22, 1)', v(203), clear];
  }

  const METRICS = [
    { key: 'extent_km2', label: 'Extent', name: 'Maximum water extent', color: '#22d3ee', when: 'Wet at least once at any time in 1984–2024' },
    { key: 'permanent_km2', label: 'Permanent', name: 'Permanent water', color: '#3b82f6', derived: 'permanent', when: 'Water all year in 2024' },
    { key: 'seasonal_km2', label: 'Seasonal', name: 'Seasonal water', color: '#7dd3fc', derived: 'seasonal', when: 'Water for part of 2024' },
    { key: 'gained_km2', label: 'Gained', name: 'Water gained', color: '#34d399', derived: 'gained', when: 'No water in 1984, water in 2024' },
    { key: 'lost_km2', label: 'Lost', name: 'Water lost', color: '#fb7185', derived: 'lost', when: 'Water in 1984, no water in 2024' },
  ];

  // Transition and water type tiles hold each class as a colour. This mix turns each palette's colours into values
  // at least 0.046 apart, and semi-transparent pixels round by less than 0.014, so a band of 0.02 finds one class only.
  const CLASS_MIX = [-0.1, 0.3, 0.75];
  const CLASS_BAND = 0.02;
  function classColorMap(classColors, palette = D.palettes.transitions) {
    const clear = 'rgba(0, 0, 0, 0)';
    const bands = classColors.map(([c, color]) => [palette[c].reduce((sum, v, i) => sum + (v / 255) * CLASS_MIX[i], 0), color]).sort((a, b) => a[0] - b[0]);
    return ['step', ['raster-value'], clear, ...bands.flatMap(([v, color]) => [v - CLASS_BAND, color, v + CLASS_BAND, clear])];
  }

  // Waterway classes from backend/pipeline/prepare_rivers.py
  const WATERWAYS = {
    river: { label: 'River', legend: 'Rivers', color: '#dbeafe' },
    canal: { label: 'Canal', legend: 'Canals', color: '#2dd4bf' },
    stream: { label: 'Nala / stream', legend: 'Nalas & streams', color: '#93c5fd' },
    qanat: { label: 'Qanat (karez)', legend: 'Qanats', color: '#d6b98c' },
    drain: { label: 'Drain', legend: 'Drains', color: '#94a3b8' },
    unnamed: { label: 'Waterway', legend: 'Other waterways', color: '#7dd3fc' },
  };
  const wwMatch = (prop) => ['match', ['get', 'kind'], ...Object.entries(WATERWAYS).flatMap(([k, v]) => [k, v[prop]]), '#7dd3fc'];

  const CHORO = [[0, '#0b1730'], [1, '#123b6b'], [2, '#0e6fa8'], [5, '#0ea5e9'], [10, '#22d3ee'], [20, '#a5f3fc']];

  // ------------------------------------------------------------------ data
  const D = {};

  async function getJSON(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
    return r.json();
  }

  function ringArea(r) {
    let a = 0;
    for (let i = 0, j = r.length - 1; i < r.length; j = i++) a += (r[j][0] - r[i][0]) * (r[j][1] + r[i][1]);
    return a;
  }

  // World polygon with Pakistan cut out, used to dim everything outside the country.
  function worldMask(fc) {
    const outer = [[-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]];
    const sign = Math.sign(ringArea(outer));
    const holes = [];
    for (const f of fc.features) {
      const g = f.geometry;
      for (const poly of g.type === 'Polygon' ? [g.coordinates] : g.coordinates) {
        const ring = poly[0].slice();
        if (Math.sign(ringArea(ring)) === sign) ring.reverse();
        holes.push(ring);
      }
    }
    return { type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [outer, ...holes] } }] };
  }

  const labelPoints = (fc) => ({
    type: 'FeatureCollection',
    features: fc.features.map((f) => ({ type: 'Feature', id: f.id, properties: f.properties, geometry: { type: 'Point', coordinates: [f.properties.lx, f.properties.ly] } })),
  });

  // Named rivers, canals and streams grouped by name, with their combined bounding box
  function riverIndex(fc) {
    const out = new Map();
    for (const f of fc.features) {
      const { name, kind } = f.properties;
      if (kind === 'qanat' || kind === 'drain') continue;
      let r = out.get(name);
      if (!r) out.set(name, (r = { name, kind, features: [], bbox: [180, 90, -180, -90] }));
      r.features.push(f);
      const lines = f.geometry.type === 'LineString' ? [f.geometry.coordinates] : f.geometry.coordinates;
      for (const line of lines) {
        for (const [x, y] of line) {
          r.bbox[0] = Math.min(r.bbox[0], x); r.bbox[1] = Math.min(r.bbox[1], y);
          r.bbox[2] = Math.max(r.bbox[2], x); r.bbox[3] = Math.max(r.bbox[3], y);
        }
      }
    }
    return out;
  }

  // Palette colour -> raw JRC value, for reading values back out of tile pixels.
  function buildLookups(palettes) {
    const out = {};
    for (const [layer, pal] of Object.entries(palettes)) {
      const entries = Object.entries(pal).map(([v, c]) => [Number(v), c]);
      const exact = new Map();
      for (const [v, c] of entries) {
        const k = (c[0] << 16) | (c[1] << 8) | c[2];
        if (!exact.has(k)) exact.set(k, v);
      }
      out[layer] = { entries, exact };
    }
    return out;
  }

  const dataReady = Promise.all([
    getJSON('data/palettes.json'),
    getJSON('data/stats.json'),
    getJSON('data/pakistan.geojson'),
    getJSON('data/provinces.geojson'),
    getJSON('data/districts.geojson'),
    getJSON('data/disputed.geojson'),
    getJSON('data/disputed_border.geojson'),
    getJSON('data/outline_lowres.geojson'),
    getJSON('data/waterways_major.geojson'),
    getJSON('data/waterways_labels.geojson'),
    getJSON('data/places_major.geojson'),
    getJSON(`${CFG.tilesPath}/manifest.json`),
    getJSON('data/erosion_dots.geojson').catch(() => null),
    getJSON('data/water_types.json'),
  ]).then(([palettes, stats, pakistan, provinces, districts, disputed, disputedBorder, outlineLowres, waterways, waterwayLabels, placesMajor, manifest, erosionDots, waterTypes]) => {
    Object.assign(D, { palettes, stats, pakistan, provinces, districts, disputed, disputedBorder, outlineLowres, waterways, waterwayLabels, placesMajor, manifest, erosionDots });
    D.rivers = riverIndex(waterways);
    D.provStats = new Map(stats.provinces.map((p) => [p.name, p]));
    D.distStats = new Map(stats.districts.map((d) => [d.id, d]));
    for (const f of districts.features) {
      const s = D.distStats.get(f.properties.id);
      if (s) f.properties.water_pct = pct(s.extent_km2, s.area_km2);
    }
    D.provFeat = new Map(provinces.features.map((f) => [f.properties.name, f]));
    D.distFeat = new Map(districts.features.map((f) => [f.properties.id, f]));
    D.mask = worldMask(pakistan);
    D.provLabels = labelPoints(provinces);
    D.distLabels = labelPoints(districts);
    D.disputedLabels = labelPoints(disputed);
    D.lookup = buildLookups(palettes);
    D.waterTypes = waterTypes;
    D.dotInfo = new Map((erosionDots?.features || []).map((f) => [f.properties.id, f.properties]));
    return D;
  });

  // Floodplain infrastructure marks, on the map and on the cross-section chart
  const FP_MARKS = { road: '#f8fafc', rail: '#fde047', bridge: '#f8fafc', dam: '#9085e9', school: '#c98500', health: '#d55181' };

  // Erosion dots: area sets the size, so radius follows the square root of km². Fades out between these zooms.
  // The dots are redrawn for each whole zoom from DOT_LEVELS[0] to DOT_LEVELS[1] (prepare_erosion_dots.py).
  const DOTS_ZOOM = [8.5, 10];
  const DOT_LEVELS = [4, 9];
  const DOT_COLOR = '#d95926';
  const DOT_BLINK_MS = 1600;     // matches dot-blink in style.css
  const DOT_RADIUS = [[0, 4], [5, 9], [30, 20]];    // sqrt(km²) -> px
  const dotRadiusAt = (km2) => {
    const s = Math.sqrt(km2);
    const i = s < DOT_RADIUS[1][0] ? 0 : 1;
    const [[x0, y0], [x1, y1]] = [DOT_RADIUS[i], DOT_RADIUS[i + 1]];
    return y0 + (Math.min(s, x1) - x0) * (y1 - y0) / (x1 - x0);
  };

  // ------------------------------------------------------------------ places
  const PLACE_KINDS = { city: 'City', town: 'Town', village: 'Village', hamlet: 'Hamlet', locality: 'Locality' };
  // rank = index in prepare_places.KINDS; z = zoom a tier appears at
  const PLACE_TIERS = [
    { id: 'city', ranks: [0], z: 4.5, size: [10.5, 15] },
    { id: 'town', ranks: [1], z: 7, size: [10.5, 13.5] },
    { id: 'village', ranks: [2], z: 10, size: [10.5, 12.5] },
    { id: 'minor', ranks: [3, 4], z: 11.5, size: [10, 12] },
  ];
  const PLACES_ZOOM = 8.5; // full place list is fetched from here

  // Spelling-insensitive key for romanised names (Kanjal / Kunjal -> "knjl"); mirrors skeleton() in prepare_places.py
  const placeKey = (s) => s.normalize('NFKD').toLowerCase().replace(/[^a-z]/g, '')
    .replace(/ph/g, 'f').replace(/q/g, 'k').replace(/v/g, 'w').replace(/[aeiouyh]/g, '').replace(/(.)\1+/g, '$1');

  // Every village/town is fetched once: on zooming in with labels on, or when search is used.
  let placesReq = null;
  function ensurePlaces() {
    if (!placesReq) {
      placesReq = getJSON('data/places.json').then((c) => {
        const list = c.n.map((name, i) => {
          const d = c.d[i] >= 0 ? D.distStats.get(c.d[i]) : null;
          return {
            i, name, alt: c.alt[i] || null, lng: c.x[i] / 1e5, lat: c.y[i] / 1e5, rank: c.k[i], kind: c.kinds[c.k[i]],
            district: d, province: d ? d.province : c.provinces[c.p[i]] || '',
          };
        });
        D.places = list;
        D.placesFC = {
          type: 'FeatureCollection',
          features: list.map((p, i) => ({ type: 'Feature', id: i, geometry: { type: 'Point', coordinates: [p.lng, p.lat] }, properties: { name: p.name, rank: p.rank } })),
        };
        // ~5 km grid for nearest-place lookups from the pixel inspector
        D.placeGrid = new Map();
        list.forEach((p) => {
          const k = `${Math.floor(p.lng / 0.05)},${Math.floor(p.lat / 0.05)}`;
          if (!D.placeGrid.has(k)) D.placeGrid.set(k, []);
          D.placeGrid.get(k).push(p);
        });
        map.getSource('places')?.setData(D.placesFC);
        return list;
      }).catch((err) => { placesReq = null; throw err; });
    }
    return placesReq;
  }

  function nearestPlace(lng, lat, maxM = 5000) {
    if (!D.placeGrid) return null;
    const cx = Math.floor(lng / 0.05), cy = Math.floor(lat / 0.05);
    const kx = 111320 * Math.cos((lat * Math.PI) / 180);
    let best = null, bd = maxM;
    for (let i = cx - 1; i <= cx + 1; i++) {
      for (let j = cy - 1; j <= cy + 1; j++) {
        for (const p of D.placeGrid.get(`${i},${j}`) || []) {
          const d = Math.hypot((p.lng - lng) * kx, (p.lat - lat) * 110540);
          if (d < bd) { bd = d; best = p; }
        }
      }
    }
    return best && { place: best, m: bd };
  }

  // ------------------------------------------------------------------ map
  const basemap = (id) => CFG.basemaps.find((b) => b.id === id);

  // Basemap names in Urdu script need this to join their letters and read right to left
  if (mapboxgl.getRTLTextPluginStatus() === 'unavailable') {
    mapboxgl.setRTLTextPlugin('https://api.mapbox.com/mapbox-gl-js/plugins/mapbox-gl-rtl-text/v0.2.3/mapbox-gl-rtl-text.js', null, true);
  }

  const map = new mapboxgl.Map({
    container: 'map',
    style: basemap(state.basemap).style,
    projection: 'globe',
    ...CFG.start,
    maxPitch: 75,
    attributionControl: false,
    logoPosition: 'bottom-right',
  });
  map.addControl(new mapboxgl.AttributionControl({ compact: true }), 'bottom-right');

  map.on('error', (e) => {
    // Tiles exist only where water was found, so 404s from the water sources are expected.
    if (e.sourceId && e.sourceId.startsWith('gsw-')) return;
    if (e.sourceId && e.sourceId.startsWith('dsdi-')) return onDsdiError(e);
    console.error(e.error || e);
  });

  const OWN = new Set();
  const TERRAIN_EXAG = 1.6;
  const below = () => map.getStyle().layers.find((l) => l.type === 'symbol' && !OWN.has(l.id))?.id;

  function add(layer, before) {
    OWN.add(layer.id);
    map.addLayer(layer, before);
  }

  function applyFog() {
    const light = state.basemap === 'terrain';
    map.setFog({
      range: [0.8, 8],
      color: light ? 'rgb(190, 214, 240)' : state.basemap === 'satellite' ? 'rgb(24, 36, 60)' : 'rgb(10, 18, 38)',
      'high-color': light ? '#3b82f6' : '#1d4ed8',
      'horizon-blend': 0.05,
      'space-color': 'rgb(3, 5, 14)',
      'star-intensity': 0.7,
    });
  }

  function addLayers() {
    if (map.getSource('mask')) return;
    const before = below();
    const bounds = D.manifest.bounds;

    map.addSource('mask', { type: 'geojson', data: D.mask });
    add({ id: 'focus-mask', type: 'fill', source: 'mask', paint: { 'fill-color': '#020611', 'fill-opacity': 0, 'fill-opacity-transition': { duration: 600 } } }, before);

    map.addSource('districts', { type: 'geojson', data: D.districts });
    add({
      id: 'districts-fill', type: 'fill', source: 'districts',
      paint: {
        'fill-color': ['interpolate', ['linear'], ['coalesce', ['get', 'water_pct'], 0], ...CHORO.flat()],
        'fill-opacity': 0,
        'fill-opacity-transition': { duration: 500 },
      },
    }, before);

    map.addSource('selection', { type: 'geojson', data: EMPTY });
    add({ id: 'selection-fill', type: 'fill', source: 'selection', paint: { 'fill-color': '#fde68a', 'fill-opacity': 0.05 } }, before);

    for (const L of CFG.layers) {
      if (L.timeline) {
        const monthly = L.timeline === 'month';
        map.addSource(`gsw-${L.id}`, {
          type: 'raster', tiles: [monthly ? monthlyUrl() : yearlyUrl()], tileSize: 256, minzoom: 4, maxzoom: 9, bounds,
          attribution: 'Water by month and year: EC JRC / Google Global Surface Water Monthly History v1.4',
        });
        const paint = { 'raster-opacity': state.opacity, 'raster-fade-duration': 0, 'raster-resampling': 'nearest' };
        if (monthly) Object.assign(paint, { 'raster-color-mix': monthChannel(), 'raster-color-range': [0, 1], 'raster-color': monthlyColorMap() });
        add({ id: `gsw-${L.id}`, type: 'raster', source: `gsw-${L.id}`, layout: { visibility: 'none' }, paint }, before);
        continue;
      }
      map.addSource(`gsw-${L.id}`, {
        type: 'raster', tiles: [tileUrl(L.id, '{z}', '{x}', '{y}', state.source)], tileSize: 256, minzoom: 0, maxzoom: L.maxzoom || CFG.tileMaxZoom, bounds,
        attribution: 'Water: EC JRC / Google Global Surface Water v1.5',
      });
      if (L.overlay) continue;
      add({
        id: `gsw-${L.id}`, type: 'raster', source: `gsw-${L.id}`, layout: { visibility: 'none' },
        paint: { 'raster-opacity': state.opacity, 'raster-fade-duration': 200, 'raster-resampling': resampling() },
      }, before);
    }

    // One tab of the insights panel on the map. Nearest resampling and no fade: blending two classes would read as a third.
    const highlightPaint = { 'raster-opacity': state.opacity, 'raster-fade-duration': 0, 'raster-resampling': 'nearest' };
    add({ id: 'highlight-extent', type: 'raster', source: 'gsw-extent', layout: { visibility: 'none' }, paint: highlightPaint }, before);
    add({
      id: 'highlight-classes', type: 'raster', source: 'gsw-transitions', layout: { visibility: 'none' },
      paint: { ...highlightPaint, 'raster-color-mix': [...CLASS_MIX, 0], 'raster-color-range': [0, 1], 'raster-color': classColorMap([[1, '#fff']]) },
    }, before);

    // Waterways sit above the water layers so channels stay readable over flooded ground
    map.addSource('waterways-minor', { type: 'geojson', data: D.minor || EMPTY });
    add({
      id: 'ww-minor', type: 'line', source: 'waterways-minor', minzoom: 7,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': WATERWAYS.unnamed.color,
        'line-opacity': ['interpolate', ['linear'], ['zoom'], 7, 0.2, 11, 0.55],
        'line-width': ['interpolate', ['linear'], ['zoom'], 7, 0.4, 12, 1.2],
      },
    }, before);
    map.addSource('waterways', { type: 'geojson', data: D.waterways });
    // Rivers: pale core with a blue glow, so they read on top of the cyan water layers
    const lightBase = state.basemap === 'terrain';
    add({
      id: 'ww-glow', type: 'line', source: 'waterways', filter: ['==', ['get', 'kind'], 'river'],
      paint: { 'line-color': '#2563eb', 'line-opacity': lightBase ? 0.25 : 0.55, 'line-blur': 2.5, 'line-width': ['interpolate', ['linear'], ['zoom'], 4, 3, 10, 9] },
    }, before);
    add({
      id: 'ww-major', type: 'line', source: 'waterways', filter: ['!=', ['get', 'kind'], 'qanat'],
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': lightBase ? ['match', ['get', 'kind'], 'river', '#1d4ed8', 'canal', '#0f766e', wwMatch('color')] : wwMatch('color'),
        // rivers from the start; canals fade in by z5.5, streams and drains by z7
        'line-opacity': ['interpolate', ['linear'], ['zoom'],
          4, ['match', ['get', 'kind'], 'river', 0.9, 0],
          5.5, ['match', ['get', 'kind'], 'river', 0.95, 'canal', 0.85, 0],
          7, ['match', ['get', 'kind'], 'river', 0.95, 'canal', 0.9, 0.75]],
        'line-width': ['interpolate', ['linear'], ['zoom'],
          4, ['match', ['get', 'kind'], 'river', 1.1, 'canal', 0.7, 0.5],
          10, ['match', ['get', 'kind'], 'river', 3.2, 'canal', 2.2, 1.3]],
      },
    }, before);
    add({
      id: 'ww-qanat', type: 'line', source: 'waterways', minzoom: 7, filter: ['==', ['get', 'kind'], 'qanat'],
      paint: { 'line-color': WATERWAYS.qanat.color, 'line-opacity': 0.85, 'line-dasharray': [1, 1.5], 'line-width': ['interpolate', ['linear'], ['zoom'], 7, 0.8, 12, 1.8] },
    }, before);

    add({
      id: 'districts-line', type: 'line', source: 'districts',
      paint: { 'line-color': '#93c5fd', 'line-opacity': 0.3, 'line-width': ['interpolate', ['linear'], ['zoom'], 5, 0.4, 9, 1.2] },
    }, before);
    add({
      id: 'districts-hover', type: 'line', source: 'districts',
      paint: { 'line-color': '#e0f2fe', 'line-width': 1.6, 'line-opacity': ['case', ['boolean', ['feature-state', 'hover'], false], 0.85, 0] },
    }, before);

    map.addSource('highlight-mask', { type: 'geojson', data: EMPTY });
    add({ id: 'highlight-dim', type: 'fill', source: 'highlight-mask', layout: { visibility: 'none' }, paint: { 'fill-color': '#020611', 'fill-opacity': 0.78 } }, before);

    map.addSource('ucs', { type: 'geojson', data: D.ucs || EMPTY });
    add({
      id: 'ucs-line', type: 'line', source: 'ucs', minzoom: UC_ZOOM,
      paint: { 'line-color': '#ddd6fe', 'line-opacity': ['interpolate', ['linear'], ['zoom'], UC_ZOOM, 0.3, 12, 0.65], 'line-width': ['interpolate', ['linear'], ['zoom'], UC_ZOOM, 0.6, 13, 1.4], 'line-dasharray': [1, 1.5] },
    }, before);

    map.addSource('tehsils', { type: 'geojson', data: D.units || EMPTY });
    add({
      id: 'tehsils-line', type: 'line', source: 'tehsils', minzoom: TEHSIL_ZOOM,
      paint: { 'line-color': '#93c5fd', 'line-opacity': 0.32, 'line-width': ['interpolate', ['linear'], ['zoom'], 7.5, 0.5, 12, 1.1], 'line-dasharray': [1, 1.6] },
    }, before);

    map.addSource('provinces', { type: 'geojson', data: D.provinces });
    add({
      id: 'provinces-line', type: 'line', source: 'provinces',
      paint: { 'line-color': '#a5b4fc', 'line-opacity': 0.55, 'line-width': ['interpolate', ['linear'], ['zoom'], 4, 0.8, 9, 2], 'line-dasharray': [3, 2] },
    }, before);

    map.addSource('disputed', { type: 'geojson', data: D.disputed });
    add({ id: 'disputed-fill', type: 'fill', source: 'disputed', paint: { 'fill-color': '#fbbf24', 'fill-opacity': 0.05 } }, before);
    map.addSource('pakistan', { type: 'geojson', data: D.pakistan });
    add({
      id: 'pak-glow', type: 'line', source: 'pakistan',
      paint: {
        'line-color': '#22d3ee', 'line-opacity': 0.35,
        'line-width': ['interpolate', ['linear'], ['zoom'], 2, 4, 8, 16],
        'line-blur': ['interpolate', ['linear'], ['zoom'], 2, 4, 8, 14],
      },
    }, before);
    add({
      id: 'pak-line', type: 'line', source: 'pakistan',
      paint: { 'line-color': '#a5f3fc', 'line-opacity': 0.9, 'line-width': ['interpolate', ['linear'], ['zoom'], 2, 0.8, 8, 2.2] },
    }, before);
    // Internal Pakistan / disputed-territory border from the official boundary file, dashed
    map.addSource('disputed-border', { type: 'geojson', data: D.disputedBorder });
    add({
      id: 'disputed-line', type: 'line', source: 'disputed-border',
      paint: { 'line-color': '#fcd34d', 'line-opacity': 0.9, 'line-dasharray': [2, 1.5], 'line-width': ['interpolate', ['linear'], ['zoom'], 3, 0.9, 8, 2] },
    }, before);
    add({
      id: 'selection-line', type: 'line', source: 'selection',
      paint: { 'line-color': '#fde68a', 'line-width': ['interpolate', ['linear'], ['zoom'], 4, 1.6, 10, 3], 'line-opacity': 0.95 },
    }, before);

    // Floodplain view for the flythrough: everything outside it dims; cross-sections and infrastructure on top
    map.addSource('fp-mask', { type: 'geojson', data: D.fpMask || EMPTY });
    add({ id: 'fp-dim', type: 'fill', source: 'fp-mask', layout: { visibility: 'none' }, paint: { 'fill-color': '#020611', 'fill-opacity': 0.5 } }, before);
    map.addSource('fp-outline', { type: 'geojson', data: D.fpOutline || EMPTY });
    add({
      id: 'fp-edge', type: 'line', source: 'fp-outline', layout: { visibility: 'none', 'line-join': 'round' },
      paint: { 'line-color': '#f8fafc', 'line-opacity': 0.85, 'line-width': ['interpolate', ['linear'], ['zoom'], 7, 0.8, 11, 1.8] },
    }, before);
    map.addSource('fp-transects', { type: 'geojson', data: D.fpTransects || EMPTY });
    add({
      id: 'fp-transects', type: 'line', source: 'fp-transects', minzoom: 8.5, layout: { visibility: 'none' },
      paint: { 'line-color': '#f8fafc', 'line-opacity': 0.28, 'line-width': 1 },
    }, before);
    add({
      id: 'fp-transect-cur', type: 'line', source: 'fp-transects', filter: ['==', ['get', 'km'], -1], layout: { visibility: 'none', 'line-cap': 'round' },
      paint: { 'line-color': '#fde047', 'line-width': 3.5 },
    }, before);
    map.addSource('fp-infra', { type: 'geojson', data: D.fpInfra || EMPTY });
    add({
      id: 'fp-road-case', type: 'line', source: 'fp-infra', filter: ['all', ['==', ['geometry-type'], 'LineString'], ['!=', ['get', 'kind'], 'rail']],
      layout: { visibility: 'none', 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': '#020611', 'line-opacity': 0.75, 'line-width': ['interpolate', ['linear'], ['zoom'], 8, 2, 12, ['match', ['get', 'kind'], 'motorway', 6, 'trunk', 5, 'primary', 4.5, 3.5]] },
    }, before);
    add({
      id: 'fp-road', type: 'line', source: 'fp-infra', filter: ['all', ['==', ['geometry-type'], 'LineString'], ['!=', ['get', 'kind'], 'rail']],
      layout: { visibility: 'none', 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': FP_MARKS.road, 'line-width': ['interpolate', ['linear'], ['zoom'], 8, 0.8, 12, ['match', ['get', 'kind'], 'motorway', 3.5, 'trunk', 3, 'primary', 2.5, 1.8]] },
    }, before);
    add({
      id: 'fp-rail', type: 'line', source: 'fp-infra', filter: ['all', ['==', ['geometry-type'], 'LineString'], ['==', ['get', 'kind'], 'rail']],
      layout: { visibility: 'none' },
      paint: { 'line-color': FP_MARKS.rail, 'line-width': ['interpolate', ['linear'], ['zoom'], 8, 1.2, 12, 2.6], 'line-dasharray': [2.2, 1.4] },
    }, before);

    // Eroded or newly wet ground as blinking dots while its pixels are too small to see
    map.addSource('erosion-dots', { type: 'geojson', data: D.erosionDots || EMPTY });
    // Steady and dim on the map: the blink is an element on top (syncPulses), so the map need not redraw for it
    const dotOpacity = ['interpolate', ['linear'], ['zoom'], DOTS_ZOOM[0], reducedMotion ? 1 : 0.4, DOTS_ZOOM[1], 0];
    add({
      id: 'erosion-dot', type: 'circle', source: 'erosion-dots', maxzoom: DOTS_ZOOM[1],
      paint: {
        'circle-color': DOT_COLOR, 'circle-stroke-color': '#f8fafc', 'circle-stroke-width': 1.2, 'circle-opacity': dotOpacity, 'circle-stroke-opacity': dotOpacity,
        'circle-radius': ['interpolate', ['linear'], ['sqrt', ['get', 'km2']], ...DOT_RADIUS.flat()],
      },
    }, before);

    const textPaint = { 'text-color': '#dbe4f3', 'text-halo-color': 'rgba(2, 6, 17, 0.85)', 'text-halo-width': 1.4 };
    const font = ['DIN Pro Medium', 'Arial Unicode MS Regular'];
    map.addSource('prov-labels', { type: 'geojson', data: D.provLabels });
    add({
      id: 'prov-labels', type: 'symbol', source: 'prov-labels', maxzoom: 8,
      layout: {
        'text-field': ['upcase', ['get', 'name']], 'text-font': font, 'text-max-width': 8, 'text-letter-spacing': 0.18,
        'text-size': ['interpolate', ['linear'], ['zoom'], 4, 9.5, 7, 13],
      },
      paint: { ...textPaint, 'text-opacity': 0.85 },
    });
    map.addSource('disputed-labels', { type: 'geojson', data: D.disputedLabels });
    add({
      id: 'disputed-labels', type: 'symbol', source: 'disputed-labels', minzoom: 4.5,
      layout: {
        'text-field': ['format', ['get', 'name'], {}, '\n', {}, 'Disputed territory', { 'font-scale': 0.85 }],
        'text-font': font, 'text-size': ['interpolate', ['linear'], ['zoom'], 4.5, 9.5, 8, 12.5], 'text-max-width': 10,
      },
      paint: { ...textPaint, 'text-color': '#fde68a', 'text-opacity': 0.9 },
    });
    map.addSource('dist-labels', { type: 'geojson', data: D.distLabels });
    add({
      id: 'dist-labels', type: 'symbol', source: 'dist-labels', minzoom: 6.2,
      layout: { 'text-field': ['get', 'name'], 'text-font': font, 'text-size': 11, 'text-max-width': 7 },
      paint: textPaint,
    });
    map.addSource('waterway-labels', { type: 'geojson', data: D.waterwayLabels });
    const wwLabel = (id, minzoom, maxzoom, band, kinds) => add({
      id, type: 'symbol', source: 'waterway-labels', minzoom, maxzoom,
      filter: ['all', ['==', ['get', 'band'], band], ['in', ['get', 'kind'], ['literal', kinds]]],
      layout: {
        'symbol-placement': 'line', 'text-field': ['get', 'name'], 'text-font': ['DIN Pro Italic', 'Arial Unicode MS Regular'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 6, 11, 11, 14], 'text-letter-spacing': 0.06,
        'symbol-spacing': 350, 'text-max-angle': 50,
      },
      paint: { 'text-color': ['match', ['get', 'kind'], 'canal', '#99f6e4', '#bfdbfe'], 'text-halo-color': 'rgba(2, 6, 17, 0.85)', 'text-halo-width': 1.3 },
    });
    // Strongly smoothed lines while zoomed out, a closer fit from z8.5 so labels stay on the river
    wwLabel('ww-labels', 5.5, 8.5, 'low', ['river', 'canal']);
    wwLabel('ww-labels-near', 8.5, 24, 'high', ['river', 'canal']);
    wwLabel('ww-labels-minor', 9, 24, 'high', ['stream']);

    // Settlements: cities, then towns, then villages/bastis and hamlets as you zoom in
    const fade = (on) => {
      const stops = [...new Set(PLACE_TIERS.flatMap((t) => [t.z, t.z + 0.5]))].sort((a, b) => a - b);
      return ['interpolate', ['linear'], ['zoom'], ...stops.flatMap((z) => [z,
        ['match', ['get', 'rank'], ...PLACE_TIERS.flatMap((t) => t.ranks.flatMap((r) => [r, z >= t.z + 0.5 ? on : 0])), 0]])];
    };
    map.addSource('places', { type: 'geojson', data: D.placesFC || D.placesMajor, attribution: 'Places: GeoNames, © OpenStreetMap contributors' });
    add({
      id: 'place-dots', type: 'circle', source: 'places', minzoom: PLACE_TIERS[0].z,
      paint: {
        'circle-color': lightBase ? '#334155' : '#e2e8f0',
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 5, ['match', ['get', 'rank'], 0, 2.6, 1, 2, 1.4], 13, ['match', ['get', 'rank'], 0, 4, 1, 3.4, 2.6]],
        'circle-stroke-color': lightBase ? '#ffffff' : 'rgba(2, 6, 17, 0.85)',
        'circle-stroke-width': 1,
        'circle-opacity': fade(0.85),
        'circle-stroke-opacity': fade(0.85),
      },
    });
    const placePaint = lightBase
      ? { 'text-color': '#1e293b', 'text-halo-color': 'rgba(255, 255, 255, 0.9)', 'text-halo-width': 1.4 }
      : textPaint;
    // Added from least to most important: later layers win label collisions
    for (const t of [...PLACE_TIERS].reverse()) {
      add({
        id: `place-labels-${t.id}`, type: 'symbol', source: 'places', minzoom: t.z,
        filter: ['in', ['get', 'rank'], ['literal', t.ranks]],
        layout: {
          'text-field': ['get', 'name'], 'text-font': t.ranks[0] <= 1 ? font : ['DIN Pro Regular', 'Arial Unicode MS Regular'],
          'text-size': ['interpolate', ['linear'], ['zoom'], t.z, t.size[0], t.z + 4, t.size[1]],
          'text-variable-anchor': ['left', 'right', 'top', 'bottom'], 'text-radial-offset': 0.6, 'text-justify': 'auto',
          'text-max-width': 8, 'symbol-sort-key': ['get', 'rank'],
        },
        paint: { ...placePaint, 'text-opacity': t.ranks[0] >= 2 ? 0.9 : 1 },
      });
    }

    map.addSource('uc-labels', { type: 'geojson', data: D.ucs ? labelPoints(D.ucs) : EMPTY });
    add({
      id: 'uc-labels', type: 'symbol', source: 'uc-labels', minzoom: UC_LABEL_ZOOM,
      layout: { 'text-field': ['case', ['in', 'UC', ['get', 'name']], ['get', 'name'], ['concat', ['get', 'name'], ' UC']], 'text-font': font, 'text-size': 10.5, 'text-max-width': 8 },
      paint: { ...textPaint, 'text-color': '#ddd6fe', 'text-opacity': 0.85 },
    });

    // Above the village names, which would otherwise leave no room for a tehsil name
    map.addSource('tehsil-labels', { type: 'geojson', data: D.units ? labelPoints(D.units) : EMPTY });
    add({
      id: 'tehsil-labels', type: 'symbol', source: 'tehsil-labels', minzoom: 9,
      layout: { 'text-field': ['concat', ['get', 'name'], ' tehsil'], 'text-font': font, 'text-size': 11.5, 'text-max-width': 9 },
      paint: { ...textPaint, 'text-color': '#bfdbfe' },
    });

    add({
      id: 'erosion-label', type: 'symbol', source: 'erosion-dots', maxzoom: DOTS_ZOOM[1],
      layout: {
        'text-field': ['format', ['get', 'name'], {}, '\n', {}, ['get', 'sub'], { 'font-scale': 0.86 }],
        'text-font': font, 'text-size': 12, 'text-max-width': 24, 'symbol-sort-key': ['-', 0, ['get', 'bld']],
        // A fixed anchor: variable anchors do not draw while the map is still a globe, below about zoom 6
        'text-anchor': 'left', 'text-justify': 'left', 'text-radial-offset': ['interpolate', ['linear'], ['sqrt', ['get', 'km2']], 0, 0.6, 30, 1.9],
      },
      paint: {
        'text-color': '#f8fafc', 'text-halo-color': 'rgba(2, 6, 17, 0.92)', 'text-halo-width': 1.5,
        'text-opacity': ['interpolate', ['linear'], ['zoom'], DOTS_ZOOM[0], 1, DOTS_ZOOM[1], 0],
      },
    });

    map.addSource('dot-pins', { type: 'geojson', data: EMPTY });
    add({
      id: 'dot-pin', type: 'circle', source: 'dot-pins',
      paint: { 'circle-radius': 5, 'circle-color': '#fde047', 'circle-stroke-color': '#020611', 'circle-stroke-width': 1.5 },
    });
    add({
      id: 'dot-pin-label', type: 'symbol', source: 'dot-pins',
      layout: {
        'text-field': ['get', 'label'], 'text-font': font, 'text-size': 12, 'text-max-width': 18,
        'text-anchor': 'left', 'text-justify': 'left', 'text-radial-offset': 0.7,
      },
      paint: { 'text-color': '#fde047', 'text-halo-color': 'rgba(2, 6, 17, 0.95)', 'text-halo-width': 1.6 },
    });

    map.addSource('unit-outline', { type: 'geojson', data: EMPTY });
    add({
      id: 'unit-line', type: 'line', source: 'unit-outline', layout: { 'line-join': 'round' },
      paint: { 'line-color': '#fde047', 'line-width': 2.2, 'line-opacity': 0.95 },
    });

    map.addSource('place-pin', { type: 'geojson', data: EMPTY });
    add({
      id: 'place-pin-halo', type: 'circle', source: 'place-pin',
      paint: { 'circle-radius': 16, 'circle-color': '#fde68a', 'circle-opacity': 0.18, 'circle-stroke-color': '#fde68a', 'circle-stroke-width': 1.5, 'circle-stroke-opacity': 0.7 },
    });
    add({
      id: 'place-pin', type: 'circle', source: 'place-pin',
      paint: { 'circle-radius': 5, 'circle-color': '#fde68a', 'circle-stroke-color': '#020611', 'circle-stroke-width': 2 },
    });
    add({
      id: 'place-pin-label', type: 'symbol', source: 'place-pin',
      layout: {
        'text-field': ['get', 'name'], 'text-font': font, 'text-size': 15, 'text-anchor': 'bottom', 'text-offset': [0, -1.5],
        'text-allow-overlap': true, 'text-ignore-placement': true,
      },
      paint: { 'text-color': '#fde68a', 'text-halo-color': 'rgba(2, 6, 17, 0.9)', 'text-halo-width': 1.6 },
    });

    add({
      id: 'fp-point', type: 'circle', source: 'fp-infra', filter: ['==', ['geometry-type'], 'Point'], minzoom: 9, layout: { visibility: 'none' },
      paint: {
        'circle-color': ['match', ['get', 'kind'], 'bridge', FP_MARKS.bridge, 'dam', FP_MARKS.dam, 'health', FP_MARKS.health, FP_MARKS.school],
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, ['match', ['get', 'kind'], 'bridge', 3.2, 'dam', 4, 2.2], 13, ['match', ['get', 'kind'], 'bridge', 5.5, 'dam', 6.5, 4.5]],
        'circle-stroke-color': '#020611', 'circle-stroke-width': 1,
      },
    });
    add({
      id: 'fp-point-label', type: 'symbol', source: 'fp-infra', minzoom: 10,
      filter: ['all', ['==', ['geometry-type'], 'Point'], ['in', ['get', 'kind'], ['literal', ['bridge', 'dam']]], ['!=', ['get', 'name'], '']],
      layout: {
        visibility: 'none', 'text-field': ['get', 'name'], 'text-font': font, 'text-size': 11.5, 'text-max-width': 9,
        'text-variable-anchor': ['top', 'bottom', 'left', 'right'], 'text-radial-offset': 0.7,
      },
      paint: { 'text-color': '#f8fafc', 'text-halo-color': 'rgba(2, 6, 17, 0.9)', 'text-halo-width': 1.4 },
    });
    add({
      id: 'fp-transect-label', type: 'symbol', source: 'fp-transects', filter: ['==', ['get', 'km'], -1],
      layout: {
        visibility: 'none', 'symbol-placement': 'line-center', 'text-field': ['get', 'label'], 'text-font': font, 'text-size': 13,
        'text-allow-overlap': true, 'text-ignore-placement': true, 'text-offset': [0, -0.9],
      },
      paint: { 'text-color': '#fde047', 'text-halo-color': 'rgba(2, 6, 17, 0.95)', 'text-halo-width': 1.8 },
    });
  }

  // Basemap state/region labels inside the official outline would contradict it; the portal draws its own.
  function hideBasemapLabelsInside() {
    const inside = ['within', D.outlineLowres.features[0]];
    for (const l of map.getStyle().layers) {
      if (OWN.has(l.id) || l.type !== 'symbol' || !/state-label|settlement-(subdivision-|minor-|major-)?label/.test(l.id)) continue;
      const f = map.getFilter(l.id);
      map.setFilter(l.id, f ? ['all', f, ['!', inside]] : ['!', inside]);
    }
  }

  const resampling = () => (map.getZoom() >= 11 ? 'nearest' : 'linear');

  // The blink: one element per dot in view, animated by the browser alone. Redrawing the map for it kept the whole
  // map busy, even when nothing moved.
  const dotsOn = () => state.overlays.erosionDots && !tour.on && map.getZoom() < DOTS_ZOOM[1] && !!map.getLayer('erosion-dot');
  const dotPulses = new Map();
  let dotLevel = null;

  function syncPulses() {
    const want = new Map();
    if (!reducedMotion && dotsOn()) {
      const b = map.getBounds();
      for (const f of D.erosionDots?.features || []) {
        if (f.properties.z === dotLevel && b.contains(f.geometry.coordinates)) want.set(f.properties.id, f);
      }
    }
    for (const [id, marker] of dotPulses) {
      if (!want.has(id)) { marker.remove(); dotPulses.delete(id); }
    }
    for (const [id, f] of want) {
      if (dotPulses.has(id)) continue;
      const r = dotRadiusAt(f.properties.km2);
      const node = el('div', { class: 'dot-pulse', 'aria-hidden': 'true' },
        `<span><i style="--d:${(2 * r + 2.4).toFixed(1)}px;--h:${(0.6 * r).toFixed(1)}px;animation-delay:${-(performance.now() % DOT_BLINK_MS).toFixed(0)}ms"></i></span>`);
      dotPulses.set(id, new mapboxgl.Marker({ element: node, occludedOpacity: 0 }).setLngLat(f.geometry.coordinates).addTo(map));
    }
  }

  function syncDots() {
    if (!dotsOn()) {
      if (dotPulses.size) syncPulses();
      return;
    }
    map.getContainer().style.setProperty('--dot-fade', Math.min(1, (DOTS_ZOOM[1] - map.getZoom()) / (DOTS_ZOOM[1] - DOTS_ZOOM[0])).toFixed(3));
    const level = Math.max(DOT_LEVELS[0], Math.min(DOT_LEVELS[1], Math.floor(map.getZoom())));
    if (level === dotLevel) return;
    dotLevel = level;
    for (const id of ['erosion-dot', 'erosion-label']) map.setFilter(id, ['==', ['get', 'z'], level]);
    syncPulses();
  }

  function setVis(id, on) {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
  }

  function applyLayer() {
    const on = state.highlight && !tour.on;
    const m = METRICS.find((x) => x.key === state.metric);
    for (const L of DATA_LAYERS) setVis(`gsw-${L.id}`, !on && L.id === state.layer);
    const kind = !on && !tour.on ? tlKind() : null;
    $('#timebar').hidden = !kind;
    document.body.classList.toggle('timeline-on', !!kind);
    $('#monthly-section').hidden = !(kind && D.tl[kind]);
    if (kind) {
      ensureTimeline(kind).then(() => {
        if (tlKind() !== kind) return;
        $('#monthly-section').hidden = false;
        buildTimebar();
        renderTimebar();
        renderTimelineChart();
        renderTimeChip();
      });
    }
    setVis('highlight-extent', on && !m.derived);
    setVis('highlight-classes', on && !!m.derived);
    setVis('highlight-dim', on && !!scopeFeature());
    if (on && m.derived && map.getLayer('highlight-classes')) map.setPaintProperty('highlight-classes', 'raster-color', classColorMap(D.stats.derived[m.derived].map((c) => [c, m.color])));
    for (const c of $('#layer-list').children) c.setAttribute('aria-checked', String(!on && c.dataset.id === state.layer));
    renderTimeChip();

    const note = $('#highlight-note');
    note.hidden = !on;
    if (!on) return;
    const rec = scopeRecord();
    note.innerHTML = `<i style="background:${m.color}"></i><span><b>${esc(m.name)}</b> in ${esc(rec.name)}: ${fmtArea(rec[m.key])} km²</span><button type="button">Back to data layer</button>`;
    note.querySelector('button').addEventListener('click', () => { state.highlight = false; applyLayer(); });
  }

  // What the map shows now, and the period it covers
  function renderTimeChip() {
    const shown = state.highlight && !tour.on ? METRICS.find((x) => x.key === state.metric) : CFG.layers.find((l) => l.id === state.layer);
    const line = (L) => `<div><b>${esc(L.name)}</b> ${esc(L.timeline ? timeWhen(L.timeline) : L.when)}</div>`;
    const source = SOURCES.get(state.source);
    const sourced = state.highlight || !!CFG.layers.find((l) => l.id === state.layer)?.sourced;
    $('#time-chip').innerHTML = line(shown)
      + (state.source !== 'all' ? `<div><b>Water source</b> ${esc(sourced ? source.name : `${source.name}: not available for this layer, which shows all water`)}</div>` : '');
  }

  // ------------------------------------------------------------------ monthly and yearly water
  // The two timeline layers share the bar under the map and the chart in the Insights panel.
  const TIMELINES = {
    month: {
      layer: 'monthly', file: 'data/timeline.json', keys: (t) => t.months, label: monthLabel, stateKey: 'month',
      tick: (y) => `${y}_01`, unit: 'month', title: 'Monthly water',
      key: [[MONTHLY.usual, 'Usual'], [MONTHLY.above, 'Above usual'], [MONTHLY.unseen, 'Not seen']],
      legend: [['line', MONTHLY.usual, 'Water seen'], ['dash', '#94a3b8', 'Usual for the month'], ['bar', MONTHLY.above, 'Above usual']],
      hint: 'Drawn only for months when at least half the area was seen; gaps are cloud or missing images. Usual is the median for the same calendar month in 2000–2021, on the ground seen that month. Click the chart to show that month on the map.',
    },
    year: {
      layer: 'yearly', file: 'data/timeline_years.json', keys: (t) => t.years.map(String), label: (k) => k, stateKey: 'year',
      tick: (y) => String(y), unit: 'year', title: 'Yearly water',
      key: [[YEARLY.permanent, 'Permanent'], [YEARLY.seasonal, 'Seasonal'], [YEARLY.above, 'Above usual'], [YEARLY.unseen, 'Not seen']],
      legend: [['bar', YEARLY.permanent, 'Permanent: rivers, canals, lakes'], ['bar', YEARLY.seasonal, 'Seasonal'], ['bar', YEARLY.above, 'Seasonal, well above usual']],
      hint: 'Water area as ground under water in its wet months. Years when less than half the area was seen in three or more months are faint. Click a bar to show that year on the map.',
    },
  };
  const tlKind = () => CFG.layers.find((l) => l.id === state.layer)?.timeline || null;
  D.tl = {};

  const timelineReq = {};
  function ensureTimeline(kind) {
    const T = TIMELINES[kind];
    timelineReq[kind] ||= getJSON(T.file).then((data) => {
      const keys = T.keys(data);
      D.tl[kind] = { data, keys, index: new Map(keys.map((k, i) => [k, i])) };
      if (!D.tl[kind].index.has(state[T.stateKey])) state[T.stateKey] = keys[keys.length - 1];
    }).catch((err) => { timelineReq[kind] = null; console.error(err); });
    return timelineReq[kind];
  }

  function scopeTimeline(kind) {
    const t = D.tl[kind].data;
    const s = state.scope;
    if (s.level === 'province') return t.provinces[s.name];
    if (s.level === 'district') return t.districts[s.id];
    return t.national;
  }

  function timeWhen(kind) {
    const T = TIMELINES[kind];
    const key = state[T.stateKey];
    const tl = D.tl[kind];
    const i = tl?.index.get(key);
    const seen = i === undefined ? null : tl.data.national[i][3];
    if (kind === 'month') return `${monthLabel(key)} only.${seen === null ? '' : ` ${Math.round(seen)}% of Pakistan seen by the satellite.`}`;
    return `${key} only.${seen === null ? '' : ` ${Math.round(seen)}% of Pakistan seen in three or more months.`}`;
  }

  function setTime(kind, key) {
    const T = TIMELINES[kind];
    if (!D.tl[kind]?.index.has(key)) return;
    if (kind === 'month') {
      const before = monthlyUrl();
      state.month = key;
      if (monthlyUrl() !== before) map.getSource('gsw-monthly')?.setTiles([monthlyUrl()]);
      if (map.getLayer('gsw-monthly')) map.setPaintProperty('gsw-monthly', 'raster-color-mix', monthChannel());
    } else {
      state[T.stateKey] = key;
      map.getSource('gsw-yearly')?.setTiles([yearlyUrl()]);
    }
    renderTimebar();
    markTime();
    renderTimeChip();
  }

  // The bar under the map: a slider over a strip of national water for each month or year
  let barKind = null;
  function buildTimebar() {
    const kind = tlKind();
    if (barKind === kind) return;
    barKind = kind;
    const T = TIMELINES[kind];
    const { keys } = D.tl[kind];
    $('#tb-range').max = keys.length - 1;
    $('#tb-prev').setAttribute('aria-label', `Previous ${T.unit}`);
    $('#tb-next').setAttribute('aria-label', `Next ${T.unit}`);
    $('#tb-range').setAttribute('aria-label', T.unit[0].toUpperCase() + T.unit.slice(1));
    $('#tb-key').innerHTML = T.key.map(([c, name]) => `<i style="background:${c}"></i>${esc(name)}`).join('');
    $('#tb-years').innerHTML = [1985, 1990, 1995, 2000, 2005, 2010, 2015, 2020]
      .map((y) => `<span style="left:${(D.tl[kind].index.get(T.tick(y)) / (keys.length - 1)) * 100}%">${y}</span>`).join('');
    drawStrip();
  }

  let pendingTime = null;
  function onRange() {
    const kind = tlKind();
    if (!kind) return;
    clearTimeout(pendingTime);
    const key = D.tl[kind].keys[Number($('#tb-range').value)];
    $('#tb-month').textContent = TIMELINES[kind].label(key);
    pendingTime = setTimeout(() => setTime(kind, key), 120);
  }

  function stepTime(d) {
    const kind = tlKind();
    if (!kind) return;
    const { keys, index } = D.tl[kind];
    setTime(kind, keys[Math.max(0, Math.min(keys.length - 1, index.get(state[TIMELINES[kind].stateKey]) + d))]);
  }

  function drawStrip() {
    const kind = tlKind();
    if (!kind || !D.tl[kind]) return;
    const rows = D.tl[kind].data.national;
    const canvas = $('#tb-strip');
    const dpr = devicePixelRatio || 1;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w) return;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    const bw = w / rows.length;
    const gap = kind === 'year' ? Math.min(2, bw * 0.2) : 0.3;
    const total = (r) => (kind === 'year' ? r[0] + r[1] + r[2] : r[0]);
    const max = Math.max(...rows.filter((r) => r[3] >= MONTHLY_PLOT_SEEN).map(total));
    rows.forEach((r, i) => {
      const x = i * bw;
      const width = Math.max(bw - gap, 0.6);
      if (r[3] < MONTHLY_PLOT_SEEN) {
        ctx.fillStyle = 'rgba(148, 163, 184, 0.28)';
        ctx.fillRect(x, h - 3, width, 3);
        return;
      }
      // Bottom to top: the water that matters most sits on the baseline
      const parts = kind === 'year'
        ? [[r[0], YEARLY.permanent], [r[2], YEARLY.above], [r[1], YEARLY.seasonal]]
        : [[r[2], MONTHLY.above], [r[0] - r[2], MONTHLY.usual]];
      let top = h;
      for (const [v, c] of parts) {
        const ph = (Math.max(v, 0) / max) * (h - 2);
        ctx.fillStyle = c;
        ctx.fillRect(x, top - ph, width, ph);
        top -= ph;
      }
    });
  }
  addEventListener('resize', drawStrip);

  function renderTimebar() {
    const kind = tlKind();
    if (!kind || !D.tl[kind] || $('#timebar').hidden) return;
    const T = TIMELINES[kind];
    const { keys, index, data } = D.tl[kind];
    const i = index.get(state[T.stateKey]);
    $('#tb-range').value = i;
    $('#tb-month').textContent = T.label(state[T.stateKey]);
    const seen = data.national[i][3];
    $('#tb-seen').textContent = kind === 'month'
      ? `${Math.round(seen)}% of Pakistan seen${seen < MONTHLY_PLOT_SEEN ? ': mostly cloud or no image' : ''}`
      : `${Math.round(seen)}% of Pakistan seen in 3+ months${seen < MONTHLY_PLOT_SEEN ? ': much of it unseen' : ''}`;
    $('#tb-prev').disabled = i === 0;
    $('#tb-next').disabled = i === keys.length - 1;
    if (!$('#tb-strip').width) drawStrip();
  }

  // Insights chart for the selected region. Months: water seen, the usual water on the same ground, water above usual.
  // Years: stacked bars of permanent, above-usual and other seasonal water.
  function renderTimelineChart() {
    const kind = tlKind();
    if (!kind || !D.tl[kind] || $('#monthly-section').hidden) return;
    const T = TIMELINES[kind];
    const { keys } = D.tl[kind];
    const rows = scopeTimeline(kind);
    const svg = $('#mc-svg');
    const W = svg.clientWidth || 330, H = 150, L = 40, R = 6, T0 = 8, B = 18;
    const n = rows.length;
    const yearly = kind === 'year';
    const slot = (W - L - R) / (yearly ? n : n - 1);
    const x = (i) => L + (yearly ? (i + 0.5) * slot : i * slot);
    const ok = rows.map((r) => r[3] >= MONTHLY_PLOT_SEEN);
    const total = (r) => (yearly ? r[0] + r[1] + r[2] : Math.max(r[0], r[1]));
    const peak = Math.max(1, ...rows.filter((_, i) => yearly || ok[i]).map(total));
    const unit = niceNum(peak / 2);
    const top = Math.ceil(peak / unit) * unit;
    const y = (v) => T0 + (1 - v / top) * (H - T0 - B);
    const ticks = Array.from({ length: Math.round(top / unit) + 1 }, (_, k) => k * unit);
    let marks;
    if (yearly) {
      const bw = Math.max(slot - 2, 1);
      marks = rows.map((r, i) => {
        let base = 0;
        return [[r[0], YEARLY.permanent], [r[2], YEARLY.above], [r[1], YEARLY.seasonal]].map(([v, c]) => {
          const y0 = y(base), y1 = y(base + v);
          base += v;
          return `<rect x="${(x(i) - bw / 2).toFixed(1)}" y="${y1.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(y0 - y1, 0).toFixed(1)}" fill="${c}" opacity="${ok[i] ? 1 : 0.3}"/>`;
        }).join('');
      }).join('');
    } else {
      const path = (k) => rows.map((r, i) => (ok[i] ? `${i && ok[i - 1] ? 'L' : 'M'}${x(i).toFixed(1)},${y(r[k]).toFixed(1)}` : '')).join('');
      const area = rows.map((r, i) => (ok[i] && r[2] > 0 ? `M${(x(i) - 0.6).toFixed(1)},${y(0).toFixed(1)}V${y(r[2]).toFixed(1)}h1.2V${y(0).toFixed(1)}Z` : '')).join('');
      marks = `<path d="${area}" fill="${MONTHLY.above}" opacity="0.85"/>
        <path d="${path(1)}" fill="none" stroke="#94a3b8" stroke-width="1.2" stroke-dasharray="3 2"/>
        <path d="${path(0)}" fill="none" stroke="${MONTHLY.usual}" stroke-width="1.5" stroke-linejoin="round"/>`;
    }
    const years = [1990, 2000, 2010, 2020];
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.innerHTML = `
      <g class="mc-grid">${ticks.map((v) => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/>`).join('')}</g>
      <g class="mc-axis">${ticks.map((v) => `<text x="${L - 6}" y="${y(v) + 3}" text-anchor="end">${fmtKm(v)}</text>`).join('')}
        ${years.map((yr) => `<text x="${x(D.tl[kind].index.get(T.tick(yr)))}" y="${H - 4}" text-anchor="middle">${yr}</text>`).join('')}</g>
      ${marks}
      <line id="mc-cur" y1="${T0}" y2="${H - B}" stroke="#fde047" stroke-width="1.5"/>
      <line id="mc-hover" y1="${T0}" y2="${H - B}" stroke="#f8fafc" stroke-opacity="0.5" visibility="hidden"/>
      <rect id="mc-hit" x="${L}" y="${T0}" width="${W - L - R}" height="${H - T0 - B}" fill="transparent"/>`;
    $('#mc-kind').textContent = T.title;
    $('#mc-scope').textContent = state.scope.level === 'national' ? 'Pakistan' : scopeRecord().name;
    $('#mc-legend').innerHTML = T.legend.map(([shape, c, name]) => `<span><i class="${shape}" style="--c:${c}"></i>${esc(name)}</span>`).join('');
    $('#mc-hint').textContent = T.hint;
    const tip = $('#mc-tip');
    const at = (e) => {
      const r = svg.getBoundingClientRect();
      const px = ((e.clientX - r.left) / r.width) * W;
      const i = yearly ? Math.floor((px - L) / slot) : Math.round((px - L) / slot);
      return Math.max(0, Math.min(n - 1, i));
    };
    const hit = svg.querySelector('#mc-hit');
    hit.addEventListener('mousemove', (e) => {
      const i = at(e);
      const r = rows[i];
      const hv = svg.querySelector('#mc-hover');
      hv.setAttribute('x1', x(i));
      hv.setAttribute('x2', x(i));
      hv.setAttribute('visibility', 'visible');
      tip.innerHTML = yearly
        ? `<b>${keys[i]}</b> · ${Math.round(r[3])}% seen in 3+ months<br>Permanent ${fmtArea(r[0])} km² · seasonal ${fmtArea(r[1] + r[2])}<br>Of which well above usual ${fmtArea(r[2])} km²`
        : `<b>${monthLabel(keys[i])}</b> · ${Math.round(r[3])}% seen<br>${r[3] < MONTHLY_PLOT_SEEN ? 'Too little seen to compare' : `Water seen ${fmtArea(r[0])} km² · usual ${fmtArea(r[1])}<br>Above usual ${fmtArea(r[2])} km²`}`;
      tip.hidden = false;
      const left = (x(i) / W) * svg.clientWidth;
      tip.style.left = `${Math.min(Math.max(left - tip.offsetWidth / 2, 0), svg.clientWidth - tip.offsetWidth)}px`;
    });
    hit.addEventListener('mouseleave', () => { tip.hidden = true; svg.querySelector('#mc-hover').setAttribute('visibility', 'hidden'); });
    hit.addEventListener('click', (e) => setTime(kind, keys[at(e)]));
    markTime();
  }

  function markTime() {
    const kind = tlKind();
    const cur = $('#mc-svg')?.querySelector('#mc-cur');
    if (!kind || !cur || !D.tl[kind]) return;
    const W = Number($('#mc-svg').getAttribute('viewBox').split(' ')[2]);
    const n = D.tl[kind].keys.length;
    const i = D.tl[kind].index.get(state[TIMELINES[kind].stateKey]);
    const slot = (W - 46) / (kind === 'year' ? n : n - 1);
    const xx = 40 + (kind === 'year' ? (i + 0.5) * slot : i * slot);
    cur.setAttribute('x1', xx);
    cur.setAttribute('x2', xx);
  }

  function applyOpacity() {
    for (const id of [...DATA_LAYERS.map((L) => `gsw-${L.id}`), 'highlight-extent', 'highlight-classes']) {
      if (map.getLayer(id)) map.setPaintProperty(id, 'raster-opacity', state.opacity);
    }
  }

  // The portal's own boundary overlays a switched-on DSDI layer stands in for (CFG.dsdi.replaces)
  function replacedByDsdi() {
    const out = new Map();
    for (const n of dsdi.on) {
      const k = CFG.dsdi?.replaces?.[n];
      if (k) out.set(k, dsdi.byName.get(n)?.title || n);
    }
    return out;
  }

  function applyOverlays() {
    if (!map.getLayer('focus-mask')) return;
    const replaced = replacedByDsdi();
    const o = replaced.size ? Object.fromEntries(Object.entries(state.overlays).map(([k, v]) => [k, v && !replaced.has(k)])) : state.overlays;
    for (const b of document.querySelectorAll('#overlays .toggle[data-id]')) {
      const by = replaced.get(b.dataset.id);
      b.classList.toggle('replaced', !!by);
      b.title = by ? `Hidden while the DSDI layer "${by}" is on, so only one set of lines shows` : '';
    }
    const note = $('#overlay-note');
    const words = { provinces: 'province', districts: 'district', tehsils: 'tehsil', ucs: 'union council' };
    note.hidden = !replaced.size;
    note.textContent = replaced.size ? `Showing NDMA DSDI's ${[...replaced.keys()].map((k) => words[k]).join(', ')} boundaries; the portal's own are hidden until you switch those off. Reports and popups still use the portal's boundaries.` : '';
    const districtsOn = o.districts || o.choropleth;
    map.setPaintProperty('focus-mask', 'fill-opacity', o.focus ? basemap(state.basemap).mask : 0);
    map.setPaintProperty('districts-fill', 'fill-opacity',
      o.choropleth ? ['case', ['boolean', ['feature-state', 'hover'], false], 0.85, 0.62] : 0);
    setVis('provinces-line', o.provinces);
    setVis('prov-labels', o.provinces && o.labels);
    setVis('districts-line', districtsOn);
    setVis('dist-labels', districtsOn && o.labels);
    setVis('tehsils-line', o.tehsils && !tour.on);
    setVis('tehsil-labels', o.tehsils && o.labels && !tour.on);
    ensureTehsils();
    setVis('ucs-line', o.ucs && !tour.on);
    setVis('uc-labels', o.ucs && o.labels && !tour.on);
    ensureUcLayer();
    setVis('disputed-labels', o.labels);
    for (const id of ['ww-minor', 'ww-glow', 'ww-major', 'ww-qanat']) setVis(id, o.rivers);
    setVis('ww-labels', o.rivers && o.labels);
    setVis('ww-labels-near', o.rivers && o.labels);
    setVis('ww-labels-minor', o.rivers && o.labels);
    setVis('place-dots', o.labels);
    // Village names would crowd the flythrough; they come back on pause
    for (const t of PLACE_TIERS) setVis(`place-labels-${t.id}`, o.labels && !(tour.playing && t.ranks[0] >= 2));
    applyFloodplainVis();
    for (const id of ['erosion-dot', 'erosion-label']) setVis(id, o.erosionDots && !tour.on);
    renderTimeChip();
    syncDots();
    syncPulses();
    $('#dots-legend').hidden = !o.erosionDots;
    $('#ww-legend').hidden = !o.rivers;
    ensureMinor();
    maybePlaces();
    syncBuildings();
    for (const l of map.getStyle().layers) {
      if (OWN.has(l.id)) continue;
      if (l['source-layer'] === 'admin') map.setLayoutProperty(l.id, 'visibility', o.borders ? 'visible' : 'none');
      // the basemap's own OSM buildings would double up with the NDMA footprints
      else if (l['source-layer'] === 'building') map.setLayoutProperty(l.id, 'visibility', o.buildings ? 'none' : 'visible');
      else if (l.type === 'symbol') map.setLayoutProperty(l.id, 'visibility', o.labels ? 'visible' : 'none');
    }
  }

  function applyTerrain() {
    if (state.terrain) {
      if (!map.getSource('mapbox-dem')) {
        map.addSource('mapbox-dem', { type: 'raster-dem', url: 'mapbox://mapbox.mapbox-terrain-dem-v1', tileSize: 512, maxzoom: 14 });
      }
      map.setTerrain({ source: 'mapbox-dem', exaggeration: TERRAIN_EXAG });
    } else {
      map.setTerrain(null);
    }
    syncBuildings();
  }

  function applySelection() {
    const r = state.river && D.rivers.get(state.river);
    const f = scopeFeature();
    map.getSource('selection')?.setData(r ? { type: 'FeatureCollection', features: r.features }
      : f ? { type: 'FeatureCollection', features: [f] } : EMPTY);
    map.getSource('highlight-mask')?.setData(f ? worldMask({ features: [f] }) : EMPTY);
    const p = state.place;
    map.getSource('place-pin')?.setData(p ? {
      type: 'FeatureCollection', features: [{ type: 'Feature', geometry: { type: 'Point', coordinates: [p.lng, p.lat] }, properties: { name: p.name } }],
    } : EMPTY);
    // the pin carries its own label
    for (const t of PLACE_TIERS) {
      const base = ['in', ['get', 'rank'], ['literal', t.ranks]];
      if (map.getLayer(`place-labels-${t.id}`)) map.setFilter(`place-labels-${t.id}`, p ? ['all', base, ['!=', ['id'], p.i]] : base);
    }
  }

  // The unnamed network is large, so it is fetched only once someone zooms in with rivers on.
  let minorReq = null;
  // Tehsil lines come with the units the popup uses, once zoomed in far enough to show them
  const TEHSIL_ZOOM = 7.5;
  let tehsilReq = null;
  function ensureTehsils() {
    if (tehsilReq || !state.overlays.tehsils || map.getZoom() < TEHSIL_ZOOM) return;
    tehsilReq = ensureUnits().then(() => {
      if (!D.units) { tehsilReq = null; return; }
      map.getSource('tehsils')?.setData(D.units);
      map.getSource('tehsil-labels')?.setData(labelPoints(D.units));
    });
  }

  // Union councils (prepare_admin_units.py): about 2 MB, fetched once zoomed in with the overlay on, or on a click
  const UC_ZOOM = 9, UC_LABEL_ZOOM = 10.5;
  let ucReq = null;
  function ensureUcs() {
    ucReq ||= getJSON('data/union_councils.geojson')
      .then((fc) => { D.ucs = fc; })
      .catch(() => { D.ucs = null; });  // optional: the portal works without it
    return ucReq;
  }
  function ensureUcLayer() {
    if (!state.overlays.ucs || map.getZoom() < UC_ZOOM - 0.5) return;
    ensureUcs().then(() => {
      if (!D.ucs || map.getSource('ucs')._data?.features?.length) return;
      map.getSource('ucs')?.setData(D.ucs);
      map.getSource('uc-labels')?.setData(labelPoints(D.ucs));
    });
  }

  function ensureMinor() {
    if (minorReq || !state.overlays.rivers || map.getZoom() < 6.5) return;
    minorReq = getJSON('data/waterways_minor.geojson')
      .then((fc) => { D.minor = fc; map.getSource('waterways-minor')?.setData(fc); })
      .catch((err) => { minorReq = null; console.error(err); });
  }

  // ------------------------------------------------------------------ buildings
  // The tile server has one tileset per district; only those overlapping the view get a source,
  // so the map never carries all ~160 at once.
  const BLD = CFG.buildings;
  const BLD_SOURCES = { google: 'Google Open Buildings', osm: 'OpenStreetMap' };
  const bldActive = new Set();
  let bldCatalog = null; // [{ id, bounds }] once data.json is read; [] if the server is unreachable
  let bldReq = null;

  function loadBuildingCatalog() {
    bldReq ??= fetch(`${BLD.server}/data.json`)
      .then((r) => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); })
      .then((list) => {
        bldCatalog = list
          .filter((t) => t.format === 'pbf' && t.vector_layers?.some((l) => l.id === BLD.layer))
          .map((t) => ({ id: t.id, bounds: t.bounds }));
      })
      .catch((err) => { bldCatalog = []; bldCatalog.failed = true; console.warn('Building tile server unreachable', err); });
    return bldReq;
  }

  // Height ramp (m): single-storey homes pale amber, taller buildings deep orange
  const BLD_RAMP = { dark: [[3, '#f8dcb4'], [6, '#f6b871'], [10, '#f08c3a'], [16, '#dc4f22']],
    light: [[3, '#e7a36a'], [6, '#d97a35'], [10, '#c2410c'], [16, '#8a2c06']] };
  const bldRamp = () => BLD_RAMP[state.basemap === 'terrain' ? 'light' : 'dark'];
  // Many footprints carry no usable height; give them a single storey
  const BLD_HEIGHT = ['max', ['coalesce', ['get', 'height'], 0], 3];

  function addBuildingSource(id) {
    const t = bldCatalog.find((c) => c.id === id);
    map.addSource(`bld-${id}`, {
      type: 'vector', tiles: [`${BLD.server}/data/${id}/{z}/{x}/{y}.pbf`], bounds: t.bounds,
      minzoom: BLD.minzoom, maxzoom: BLD.maxzoom, attribution: 'Buildings: Google Open Buildings, © OpenStreetMap contributors',
    });
    // Above the water layers so footprints read on flooded ground, below waterways and labels
    const before = map.getLayer('ww-minor') ? 'ww-minor' : undefined;
    add({
      id: `bld-${id}`, type: 'fill-extrusion', source: `bld-${id}`, 'source-layer': BLD.layer, minzoom: BLD.minzoom,
      paint: {
        'fill-extrusion-color': ['interpolate', ['linear'], BLD_HEIGHT, ...bldRamp().flat()],
        // buildings rise out of the ground over the first zoom level they appear at
        'fill-extrusion-height': ['interpolate', ['linear'], ['zoom'], BLD.minzoom, 0, BLD.minzoom + 1, BLD_HEIGHT],
        'fill-extrusion-opacity': ['interpolate', ['linear'], ['zoom'], BLD.minzoom, 0.8, 15, 0.95],
        'fill-extrusion-vertical-gradient': true,
        'fill-extrusion-ambient-occlusion-intensity': 0.35,
      },
    }, before);
    bldActive.add(id);
  }

  function removeBuildingSource(id) {
    if (map.getLayer(`bld-${id}`)) { map.removeLayer(`bld-${id}`); OWN.delete(`bld-${id}`); }
    if (map.getSource(`bld-${id}`)) map.removeSource(`bld-${id}`);
    bldActive.delete(id);
  }

  function syncBuildings() {
    if (!BLD || !map.getLayer('focus-mask') || tour.playing) return; // the flight moves the camera every frame
    const on = state.overlays.buildings;
    const close = map.getZoom() >= BLD.minzoom - 0.5;
    if (on && close && !bldCatalog) { loadBuildingCatalog().then(syncBuildings); return; }

    const want = new Set();
    if (on && close && bldCatalog) {
      const b = map.getBounds();
      for (const t of bldCatalog) {
        const [w, s, e, n] = t.bounds;
        if (w <= b.getEast() && e >= b.getWest() && s <= b.getNorth() && n >= b.getSouth()) want.add(t.id);
      }
    }
    for (const id of [...bldActive]) if (!want.has(id)) removeBuildingSource(id);
    for (const id of want) if (!bldActive.has(id)) addBuildingSource(id);

    $('#bld-legend').hidden = !on;
    const hint = $('#bld-hint');
    hint.hidden = !on || (close && !bldCatalog?.failed);
    hint.textContent = bldCatalog?.failed ? `Building server not reachable (${BLD.server.replace(/^https?:\/\//, '')})` : 'Zoom in to neighbourhood level to see buildings';
  }

  function buildingAt(point) {
    const layers = [...bldActive].map((id) => `bld-${id}`).filter((l) => map.getLayer(l));
    return layers.length ? map.queryRenderedFeatures(point, { layers })[0] || null : null;
  }

  // Resolved once a (re)loaded style has all portal layers
  let styled = [];
  const whenStyled = () => new Promise((r) => styled.push(r));

  map.on('style.load', async () => {
    await dataReady;
    bldActive.clear(); // a new style drops the building sources
    dotLevel = null;
    applyFog();
    addLayers();
    hideBasemapLabelsInside();
    applyLayer();
    applyOverlays();
    applyTerrain();
    applySelection();
    syncDsdi();
    lastResampling = null;
    applyResampling();
    for (const r of styled.splice(0)) r();
  });

  let lastResampling = null;
  function applyResampling() {
    const mode = resampling();
    if (mode === lastResampling || !map.getLayer('gsw-extent')) return;
    lastResampling = mode;
    for (const L of DATA_LAYERS) if (!L.timeline) map.setPaintProperty(`gsw-${L.id}`, 'raster-resampling', mode);
  }

  function maybePlaces() {
    if (state.overlays.labels && map.getZoom() >= PLACES_ZOOM) ensurePlaces().catch(console.error);
  }

  map.on('zoom', syncDots);
  map.on('moveend', syncPulses);

  map.on('zoomend', () => {
    ensureMinor();
    ensureTehsils();
    ensureUcLayer();
    maybePlaces();
    applyResampling();
  });

  // ------------------------------------------------------------------ left panel
  // A ramp's end colour, lifted so its label reads well on the dark glass
  function legendInk(color) {
    const [r, g, b] = color.match(/\d+/g).map(Number);
    const lift = (v) => Math.round(v + (255 - v) * 0.25);
    return `rgb(${lift(r)}, ${lift(g)}, ${lift(b)})`;
  }

  function legendHTML(L) {
    const swatches = (list) => `<ul class="classes">${list.map(([c, name]) => `<li><span class="swatch" style="background:${c}"></span>${esc(name)}</li>`).join('')}</ul>`;
    if (L.legend.type === 'monthly') {
      return swatches([[MONTHLY.usual, 'Usual water for the month'], [MONTHLY.above, 'Water well above usual: likely flood'], [MONTHLY.unseen, 'Not seen: cloud or no image']]);
    }
    if (L.legend.type === 'yearly') {
      return swatches([[YEARLY.permanent, 'Permanent water: rivers, canals, lakes'], [YEARLY.seasonal, 'Seasonal water: floods, rain, irrigation'],
        [YEARLY.above, 'Seasonal, well above usual: likely flood'], [YEARLY.unseen, 'Not seen: under 3 months']]);
    }
    const pal = D.palettes[L.id];
    const c = (v) => rgb(pal[v]);
    const g = L.legend;
    if (g.type === 'single') {
      return `<div class="single"><span class="swatch" style="background:${c(1)}"></span>${esc(g.label)}</div>`;
    }
    if (g.type === 'ramp') {
      const stops = Array.from({ length: 11 }, (_, i) => `${c(Math.round(g.min + ((g.max - g.min) * i) / 10))} ${i * 10}%`);
      return `<div class="ramp" style="background:linear-gradient(90deg,${stops})"></div>
        <div class="ramp-labels mono">${g.labels.map((s) => `<span>${s}</span>`).join('')}</div>
        ${g.ends ? `<div class="ends"><span style="--c:${legendInk(c(g.min))}">${g.ends[0]}</span><span style="--c:${legendInk(c(g.max))}">${g.ends[1]}</span></div>` : ''}`;
    }
    if (g.type === 'steps') {
      const steps = [];
      for (let v = g.min; v <= g.max; v++) steps.push(`<span style="background:${c(v)}" title="${v} months"></span>`);
      return `<div class="steps">${steps.join('')}</div><div class="ramp-labels mono">${g.labels.map((s) => `<span>${s}</span>`).join('')}</div>`;
    }
    return `<ul class="classes">${Object.entries(g.classes || CFG.classes)
      .map(([k, name]) => `<li><span class="swatch" style="background:${c(k)}"></span>${esc(name)}</li>`).join('')}</ul>`;
  }

  function thumbBG(L) {
    if (L.timeline === 'month') return `linear-gradient(135deg, ${MONTHLY.usual} 0 55%, ${MONTHLY.above} 55% 100%)`;
    if (L.timeline) return `linear-gradient(135deg, ${YEARLY.permanent} 0 40%, ${YEARLY.seasonal} 40% 72%, ${YEARLY.above} 72% 100%)`;
    const pal = D.palettes[L.id];
    const vals = Object.keys(pal).map(Number);
    if (vals.length === 1) return `radial-gradient(circle at 30% 30%, #a5f3fc, ${rgb(pal[vals[0]])} 45%, #0e7490)`;
    if (L.legend.type === 'classes') {
      const n = vals.length;
      return `conic-gradient(${vals.map((v, i) => `${rgb(pal[v])} ${(i / n) * 360}deg ${((i + 1) / n) * 360}deg`)})`;
    }
    const lo = vals[0], hi = vals[vals.length - 1];
    const stops = [0, 0.25, 0.5, 0.75, 1].map((t) => rgb(pal[Math.round(lo + (hi - lo) * t)]));
    return `linear-gradient(135deg, ${stops})`;
  }

  function buildLayerCards() {
    const list = $('#layer-list');
    list.setAttribute('role', 'radiogroup');
    for (const L of CARD_LAYERS) {
      const card = el('div', { class: 'layer-card', role: 'radio', tabindex: '0', 'aria-checked': String(L.id === state.layer), 'data-id': L.id }, `
        <div class="top">
          <span class="thumb" style="background:${thumbBG(L)}"></span>
          <div><div class="name">${esc(L.name)}${L.prototype ? '<span class="proto" title="Built from Earth Engine exports; methods still being checked">Prototype</span>' : ''}</div><div class="tag">${esc(L.tag)}</div></div>
          <span class="radio"></span>
        </div>
        <div class="more"><div><p class="desc">${esc(L.desc)}</p><p class="when"><b>Time covered:</b> ${esc(L.when)}</p><div class="legend">${legendHTML(L)}</div><div class="dl"></div></div></div>`);
      layerDownload(card.querySelector('.dl'), L);
      const pick = () => {
        setLayer(L.id);
        if (L.focus) map.fitBounds(L.focus, { padding: panelPadding(), pitch: state.terrain ? 55 : 20, bearing: 0, duration: 2400, essential: true });
      };
      card.addEventListener('click', pick);
      card.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
      list.append(card);
    }
  }

  // ------------------------------------------------------------------ layer downloads
  // backend/exports.py, through backend/serve.py: a layer's parts for an area as GeoTIFF, GeoJSON or a zipped shapefile,
  // cut from the source data rather than the map tiles. A host without that server (Vercel) has no downloads.
  const exportCatalogue = fetch(`${BASE}api/export/catalogue`)
    .then((r) => (r.ok && (r.headers.get('Content-Type') || '').includes('json') ? r.json() : null)).catch(() => null);
  const DL_FORMATS = [['tif', 'GeoTIFF'], ['geojson', 'GeoJSON'], ['shp', 'Shapefile']];
  const DL_MIN = [['0', 'Every pixel'], ['0.5', '0.5 ha'], ['1', '1 ha'], ['5', '5 ha']];
  const DL_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11M7 10l5 5 5-5M5 20h14"/></svg>';
  // Quick picks for layers whose parts fall in two families
  const DL_PRESETS = { erosion: [['Land lost or newly wet', /^[12]$/], ['Land regained', /^[34]$/]] };

  function partColor(L, id) {
    if (L.id === 'monthly') return { usual: MONTHLY.usual, above: MONTHLY.above, unseen: MONTHLY.unseen }[id];
    const pal = D.palettes[L.id];
    if (!pal) return null;
    const bin = /^p(\d+)_(\d+)$/.exec(id);
    const v = { gain: 175, loss: 25 }[id]
      ?? (bin ? Math.round((+bin[1] + +bin[2]) / 2) : /^\d+$/.test(id) ? +id : 1);
    return pal[v] ? rgb(pal[v]) : null;
  }

  function dlScope() {
    const s = state.scope;
    if (s.level === 'district') {
      const d = D.distStats.get(s.id);
      return d && { key: `district:${s.id}`, label: `${d.name} district` };
    }
    if (s.level === 'province') return { key: `province:${s.name}`, label: s.name };
    return null;
  }

  function layerDownload(box, L) {
    box.addEventListener('click', (e) => e.stopPropagation());
    box.addEventListener('keydown', (e) => e.stopPropagation());
    const open = el('button', { type: 'button', class: 'dl-open', 'aria-expanded': 'false' }, `${DL_ICON}<span>Download this layer</span><svg class="chev" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>`);
    const form = el('div', { class: 'dl-form', hidden: '' }, '<div class="hint">Checking downloads…</div>');
    box.append(open, form);
    let built = false;
    open.addEventListener('click', async () => {
      const on = open.getAttribute('aria-expanded') !== 'true';
      open.setAttribute('aria-expanded', String(on));
      form.hidden = !on;
      if (on && !built) {
        built = true;
        fillDownload(form, L, await exportCatalogue);
      }
      if (on) refreshDownload(form, L);
    });
  }

  function fillDownload(form, L, cat) {
    const spec = cat?.layers?.[L.id];
    if (!spec) {
      form.innerHTML = `<div class="dl-off">${cat ? 'This layer has no download yet.'
        : 'Downloads are cut from the source data by the portal server, so they work when the portal is opened from <b>backend/serve.py</b> (Run Portal, on this PC or the NDMA network), not on the public web copy.'}</div>`;
      return;
    }
    const presets = DL_PRESETS[L.id] || [];
    const seg = (cls, label, opts, pick) => `<div class="seg dl-seg ${cls}" role="radiogroup" aria-label="${label}">${opts.map(([k, t]) => `<button type="button" role="radio" aria-checked="${k === pick}" data-v="${esc(k)}">${esc(t)}</button>`).join('')}</div>`;
    form.innerHTML = `
      <div class="dl-h">What to download</div>
      ${presets.length ? `<div class="dl-quick">${presets.map(([label], i) => `<button type="button" data-preset="${i}">${esc(label)}</button>`).join('')}<button type="button" data-preset="all">All</button></div>` : ''}
      <div class="dl-parts">${spec.parts.map(([id, label]) => {
        const c = partColor(L, id);
        return `<label class="dl-part"><input type="checkbox" value="${esc(id)}" checked>${c ? `<span class="swatch" style="background:${c}"></span>` : '<span class="swatch none"></span>'}<span>${esc(label)}</span></label>`;
      }).join('')}</div>
      ${spec.lasting ? `<div class="dl-h">How much change</div>${seg('dl-lasting', 'How much change', [['0', 'Any change'], ['1', '25 points or more']], '0')}
        <p class="dl-sub">25 points or more is a lasting change: the rule behind "land lost" in the reports.</p>` : ''}
      <div class="dl-h">Area</div>
      <div class="seg dl-seg dl-area" role="radiogroup" aria-label="Area"></div>
      <div class="dl-h">Format</div>
      ${seg('dl-fmt', 'Format', DL_FORMATS, 'tif')}
      <div class="dl-h">Leave out patches smaller than</div>
      ${seg('dl-min', 'Smallest patch', DL_MIN, '0')}
      <p class="dl-note"></p>
      <button type="button" class="dl-go">${DL_ICON}<span>Download</span></button>
      <div class="dl-status" hidden><div class="dl-bar"><i></i></div><span class="dl-msg"></span></div>`;
    form.dataset.ready = '1';
    form.querySelectorAll('[data-preset]').forEach((b) => b.addEventListener('click', () => {
      const p = b.dataset.preset === 'all' ? /./ : presets[+b.dataset.preset][1];
      form.querySelectorAll('.dl-part input').forEach((i) => { i.checked = p.test(i.value); });
    }));
    form.querySelectorAll('.dl-seg').forEach((g) => g.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      g.querySelectorAll('button').forEach((x) => x.setAttribute('aria-checked', String(x === b)));
      refreshDownload(form, L);
    }));
    form.querySelector('.dl-go').addEventListener('click', () => runDownload(form, L));
  }

  // The area choices follow the region picked in Insights; the note says what else the file follows
  function refreshDownload(form, L) {
    if (!form.dataset.ready) return;
    const areaSeg = form.querySelector('.dl-area');
    const was = areaSeg.querySelector('[aria-checked="true"]')?.dataset.v;
    const scope = dlScope();
    const opts = [['view', 'Map view'], ...(scope ? [[scope.key, scope.label]] : []), ['pakistan', 'Pakistan']];
    const pick = opts.some(([k]) => k === was) ? was : scope ? scope.key : 'view';
    areaSeg.innerHTML = opts.map(([k, label]) => `<button type="button" role="radio" aria-checked="${k === pick}" data-v="${esc(k)}" title="${esc(label)}">${esc(label)}</button>`).join('');
    const fmt = form.querySelector('.dl-fmt [aria-checked="true"]').dataset.v;
    const cell = L.timeline ? '280 m cells' : L.id === 'landloss' ? '20 m pixels' : '30 m pixels';
    const notes = [];
    if (L.sourced) notes.push(`Water source: <b>${esc(SOURCES.get(state.source).name)}</b>, as on the map (change it under Water source in Insights).`);
    if (L.timeline === 'month') notes.push(`Month: <b>${esc(monthLabel(state.month))}</b>, the month on the map.`);
    if (L.id === 'erosion') notes.push('Indus floodplain only.');
    if (L.id === 'landloss') notes.push('Sukkur reach only. The GeoTIFF keeps the 20 m UTM zone 42N grid; vector files are in WGS 84.');
    notes.push(fmt === 'tif' ? `GeoTIFF: the data's own values on its ${cell}, the smallest file.`
      : `Polygons follow the ${cell}; each has its part and its area in hectares.`);
    if (fmt !== 'tif' && pick === 'pakistan' && !L.timeline && L.id !== 'landloss' && L.id !== 'erosion') {
      notes.push('<b>For all of Pakistan a vector file can run to hundreds of MB and take several minutes.</b> Leaving out small patches, a smaller area or a GeoTIFF is quicker.');
    }
    form.querySelector('.dl-note').innerHTML = notes.join(' ');
  }

  async function runDownload(form, L) {
    const parts = [...form.querySelectorAll('.dl-part input:checked')].map((i) => i.value);
    const status = form.querySelector('.dl-status');
    const msg = form.querySelector('.dl-msg');
    const bar = form.querySelector('.dl-bar i');
    const go = form.querySelector('.dl-go');
    const say = (text, kind = '') => { status.hidden = false; status.className = `dl-status ${kind}`; msg.innerHTML = text; };
    if (!parts.length) return say('Tick at least one part.', 'err');
    let area = form.querySelector('.dl-area [aria-checked="true"]').dataset.v;
    if (area === 'view') {
      const b = map.getBounds();
      area = `view:${[b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => v.toFixed(4)).join(',')}`;
    }
    const q = { layer: L.id, parts: parts.join(','), area, format: form.querySelector('.dl-fmt [aria-checked="true"]').dataset.v,
      min_ha: form.querySelector('.dl-min [aria-checked="true"]').dataset.v };
    if (L.sourced) q.source = state.source;
    if (L.timeline === 'month') q.month = state.month;
    const lasting = form.querySelector('.dl-lasting [aria-checked="true"]');
    if (lasting) q.lasting = lasting.dataset.v;
    go.disabled = true;
    bar.style.width = '0%';
    say('Starting…');
    try {
      const ask = async (url) => {
        const r = await fetch(url);
        const j = await r.json().catch(() => null);
        if (!j) throw new Error(`The portal server did not answer (${r.status})`);
        return j;
      };
      let job = await ask(`${BASE}api/export/start?${new URLSearchParams(q)}`);
      const t0 = Date.now();
      while (!job.error && job.state !== 'done') {
        const pc = Math.round((job.progress || 0) * 100);
        bar.style.width = `${pc}%`;
        const secs = Math.round((Date.now() - t0) / 1000);
        say(`${job.state === 'queued' ? 'Waiting for another download to finish' : 'Cutting the data'}… ${pc}%${secs >= 5 ? ` · ${secs} s` : ''}`);
        await new Promise((r) => setTimeout(r, 1000));
        job = await ask(`${BASE}api/export/status?id=${encodeURIComponent(job.id)}`);
      }
      if (job.error) throw new Error(job.error);
      bar.style.width = '100%';
      const a = el('a', { href: `${BASE}api/export/file?id=${encodeURIComponent(job.id)}`, download: job.name });
      document.body.append(a);
      a.click();
      a.remove();
      const size = job.size >= 1e6 ? `${(job.size / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(job.size / 1e3))} KB`;
      say(`Saved <b>${esc(job.name)}</b> · ${size}`, 'ok');
    } catch (err) {
      say(esc(err.message || 'The download failed'), 'err');
    } finally {
      go.disabled = false;
    }
  }

  function setLayer(id) {
    state.layer = id;
    state.highlight = false;
    applyLayer();
  }

  function buildOpacity() {
    const input = $('#opacity');
    const sync = () => {
      state.opacity = input.value / 100;
      input.style.setProperty('--p', `${input.value}%`);
      $('#opacity-val').textContent = `${input.value}%`;
      applyOpacity();
    };
    input.addEventListener('input', sync);
    sync();
  }

  function bindTimebar() {
    $('#tb-range').addEventListener('input', onRange);
    $('#tb-prev').addEventListener('click', () => stepTime(-1));
    $('#tb-next').addEventListener('click', () => stepTime(1));
  }

  function buildOverlays() {
    const box = $('#overlays');
    for (const o of OVERLAYS) {
      const b = el('button', { class: 'toggle', role: 'switch', 'aria-checked': String(state.overlays[o.id]), 'data-id': o.id },
        `<span class="lbl"><svg viewBox="0 0 24 24">${o.icon}</svg>${o.label}</span><span class="sw"></span>`);
      b.addEventListener('click', () => {
        state.overlays[o.id] = !state.overlays[o.id];
        b.setAttribute('aria-checked', String(state.overlays[o.id]));
        applyOverlays();
      });
      box.append(b);
    }
    $('#ww-legend').innerHTML = Object.values(WATERWAYS).filter((k) => k !== WATERWAYS.drain)
      .map((k) => `<span><i class="line-swatch${k === WATERWAYS.qanat ? ' dotted' : ''}" style="--c:${k.color}"></i>${k.legend}</span>`).join('');
    $('#bld-legend .ramp').style.background = `linear-gradient(90deg, ${BLD_RAMP.dark.map(([, c]) => c).join(', ')})`;
  }

  // ------------------------------------------------------------------ NDMA DSDI layers
  // GeoServer layers from NDMA's Disaster Spatial Data Infrastructure, through the backend/serve.py relay (CFG.dsdi.relay).
  // A switched-on layer is a WMS raster drawn above the water layers and below the portal's own labels.
  const DS = CFG.dsdi;
  const dsdi = {
    layers: [], // [{ name, title, id, group, dup }] from the catalogue
    byName: new Map(),
    on: [], // names switched on, in drawing order (last on top)
    opacity: new Map(),
    detail: new Map(), // name -> Promise of { bounds, type }
    failed: new Map(), // source id -> tiles that did not load
    open: new Set(), // groups left open in the list
    lift: new Set(), // names drawn with their dark colours lightened
    outline: new Set(), // area layers drawn as outlines only (backend/serve.py OUTLINE_SLD)
    areas: new Set(), // names known to be area (polygon) layers, which can be drawn as outlines
  };
  const DSDI_OPACITY = 0.85;
  const dsdiStyle = (name) => (dsdi.outline.has(name) ? { style: 'outline' } : {});
  const dsdiLift = (name) => (dsdi.lift.has(name) ? 0.75 : 0);
  const dsdiUrl = (route, params) => `${BASE}${DS.relay}/${route}?${new URLSearchParams(params)}`;
  // {bbox-epsg-3857} must reach Mapbox unencoded, so this one is built by hand
  const dsdiTiles = (name) => `${BASE}${DS.relay}/tile?layer=${encodeURIComponent(name)}${dsdi.outline.has(name) ? '&style=outline' : ''}&bbox={bbox-epsg-3857}`;

  function dsdiDetail(name) {
    if (!dsdi.detail.has(name)) {
      dsdi.detail.set(name, fetch(dsdiUrl('detail', { layer: name })).then((r) => (r.ok ? r.json() : null)).catch(() => null));
    }
    return dsdi.detail.get(name);
  }

  async function loadDsdi() {
    const list = $('#dsdi-list');
    try {
      const r = await fetch(`${BASE}${DS.relay}/layers`);
      // On a host without the relay (Vercel) this is a 404 or an HTML page, not JSON
      if (!(r.headers.get('Content-Type') || '').includes('json')) throw Object.assign(new Error('no relay'), { offline: true });
      const d = await r.json();
      if (!d?.layers) throw new Error(d?.error || `${r.status}`);
      // DSDI publishes many layers twice, as X and X_ with the same features: list one of each (a quick pick if either is)
      const all = new Set(d.layers.map((l) => l.name));
      const picked = new Set((DS.featured || []).map((f) => f.name));
      const copy = (n) => (n.endsWith('_') && all.has(n.replace(/_+$/, '')) && !picked.has(n)) || (picked.has(`${n}_`) && !picked.has(n));
      const layers = d.layers.filter((l) => !copy(l.name));
      const titles = new Map();
      for (const l of layers) titles.set(l.title.toLowerCase(), (titles.get(l.title.toLowerCase()) || 0) + 1);
      dsdi.layers = layers
        .map((l, i) => ({ ...l, id: `dsdi-${i}`, group: DS.groups.find((g) => g.match.test(l.title)).id, dup: titles.get(l.title.toLowerCase()) > 1 }))
        .sort((a, b) => a.title.localeCompare(b.title, 'en', { sensitivity: 'base' }));
      for (const l of dsdi.layers) dsdi.byName.set(l.name, l);
      renderDsdiList();
      renderDsdiActive();
    } catch (err) {
      console.warn('DSDI catalogue unavailable', err);
      list.innerHTML = `<div class="hint dsdi-off">${err.offline
        ? 'DSDI layers open only when the portal runs on the NDMA network from <b>Run Portal.bat</b> (backend/serve.py).'
        : `DSDI server not reachable just now (${esc(err.message)}). Try again later.`}</div>`;
      $('#dsdi-q').disabled = true;
    }
  }

  function dsdiRow(l, label) {
    const on = dsdi.on.includes(l.name);
    const b = el('button', { class: 'toggle dsdi-item', role: 'switch', 'aria-checked': String(on), title: l.name },
      `<span class="lbl"><span class="t">${esc(label || l.title)}${l.dup && !label ? `<small class="mono">${esc(l.name.split(':')[1])}</small>` : ''}</span></span><span class="sw"></span>`);
    b.addEventListener('click', () => toggleDsdi(l.name));
    return b;
  }

  function renderDsdiList() {
    const list = $('#dsdi-list');
    const q = $('#dsdi-q').value.trim().toLowerCase();
    list.replaceChildren();
    $('#dsdi-count').textContent = `${dsdi.layers.length} layers`;
    if (q) {
      const words = q.split(/\s+/);
      const hits = dsdi.layers.filter((l) => words.every((w) => `${l.title} ${l.name}`.toLowerCase().includes(w)));
      if (!hits.length) list.innerHTML = '<div class="hint">No DSDI layer matches.</div>';
      for (const l of hits.slice(0, 80)) list.append(dsdiRow(l));
      if (hits.length > 80) list.insertAdjacentHTML('beforeend', `<div class="hint">${hits.length - 80} more: type more of the name.</div>`);
      return;
    }
    const picks = (DS.featured || []).filter((f) => dsdi.byName.has(f.name));
    if (picks.length) {
      const box = el('div', { class: 'dsdi-picks' }, '<div class="dsdi-picks-h">Quick picks</div>');
      for (const f of picks) box.append(dsdiRow(dsdi.byName.get(f.name), f.label));
      list.append(box);
    }
    for (const g of DS.groups) {
      const items = dsdi.layers.filter((l) => l.group === g.id);
      if (!items.length) continue;
      const on = items.filter((l) => dsdi.on.includes(l.name)).length;
      const box = el('details', { class: 'dsdi-group' }, `<summary><span>${esc(g.label)}</span><span class="mono">${on ? `<b>${on} on</b> · ` : ''}${items.length}</span></summary>`);
      box.open = dsdi.open.has(g.id);
      box.addEventListener('toggle', () => {
        if (box.open) {
          dsdi.open.add(g.id);
          if (box.children.length === 1) for (const l of items) box.append(dsdiRow(l)); // rows are built when first opened
        } else dsdi.open.delete(g.id);
      });
      if (box.open) for (const l of items) box.append(dsdiRow(l));
      list.append(box);
    }
  }

  function renderDsdiActive() {
    const box = $('#dsdi-active');
    box.replaceChildren();
    $('#dsdi-count').textContent = dsdi.on.length ? `${dsdi.on.length} on · ${dsdi.layers.length} layers` : `${dsdi.layers.length} layers`;
    $('#dsdi-dock').hidden = !dsdi.on.length;
    $('#dsdi-dock-count').textContent = dsdi.on.length;
    for (const name of [...dsdi.on].reverse()) { // top of the map first
      const l = dsdi.byName.get(name);
      const op = Math.round((dsdi.opacity.get(name) ?? DSDI_OPACITY) * 100);
      const failed = dsdi.failed.get(l.id) || 0;
      const row = el('div', { class: 'dsdi-on' }, `
        <div class="row-between">
          <span class="t" title="${esc(name)}">${esc(l.title)}</span>
          <span class="dsdi-tools">
            ${dsdi.areas.has(name) ? `<button class="mini" data-act="outline" title="Outlines only: no fill, so the water shows through" aria-pressed="${dsdi.outline.has(name)}" aria-label="Outlines only for ${esc(l.title)}"><svg viewBox="0 0 24 24"><path d="M5 5h14v14H5z"/></svg></button>` : ''}
            <button class="mini" data-act="lift" ${dsdi.outline.has(name) ? 'hidden ' : ''}title="Lighten dark colours: for layers drawn in black, which vanish on the dark map" aria-pressed="${dsdi.lift.has(name)}" aria-label="Lighten ${esc(l.title)}"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg></button>
            <button class="mini" data-act="legend" title="Show legend" aria-label="Show legend for ${esc(l.title)}"><svg viewBox="0 0 24 24"><path d="M4 6h3M4 12h3M4 18h3M10 6h10M10 12h10M10 18h10"/></svg></button>
            <button class="mini" data-act="zoom" title="Zoom to layer" aria-label="Zoom to ${esc(l.title)}"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="m20 20-4.5-4.5M11 8v6M8 11h6"/></svg></button>
            <button class="mini" data-act="off" title="Remove from map" aria-label="Remove ${esc(l.title)}"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg></button>
          </span>
        </div>
        <input type="range" min="0" max="100" value="${op}" aria-label="Opacity of ${esc(l.title)}" style="--p:${op}%">
        ${failed ? '<div class="hint dsdi-warn">Some map images did not load from DSDI.</div>' : ''}
        <div class="dsdi-legend" hidden><img alt="Legend for ${esc(l.title)}" src="${dsdiUrl('legend', { layer: name, ...dsdiStyle(name) })}"></div>`);
      row.querySelector('[data-act="outline"]')?.addEventListener('click', () => {
        if (dsdi.outline.has(name)) dsdi.outline.delete(name); else dsdi.outline.add(name);
        map.getSource(l.id)?.setTiles([dsdiTiles(name)]);
        renderDsdiActive();
      });
      row.querySelector('input').addEventListener('input', (e) => {
        const v = e.target.value / 100;
        e.target.style.setProperty('--p', `${e.target.value}%`);
        dsdi.opacity.set(name, v);
        if (map.getLayer(l.id)) map.setPaintProperty(l.id, 'raster-opacity', v);
      });
      row.querySelector('[data-act="lift"]').addEventListener('click', (e) => {
        if (dsdi.lift.has(name)) dsdi.lift.delete(name); else dsdi.lift.add(name);
        e.currentTarget.setAttribute('aria-pressed', String(dsdi.lift.has(name)));
        if (map.getLayer(l.id)) map.setPaintProperty(l.id, 'raster-brightness-min', dsdiLift(name));
      });
      row.querySelector('[data-act="legend"]').addEventListener('click', (e) => {
        const lg = row.querySelector('.dsdi-legend');
        lg.hidden = !lg.hidden;
        e.currentTarget.setAttribute('aria-pressed', String(!lg.hidden));
      });
      row.querySelector('[data-act="zoom"]').addEventListener('click', async () => {
        const b = (await dsdiDetail(name))?.bounds;
        if (b) map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: panelPadding(), maxZoom: 12, duration: reducedMotion ? 0 : 1600 });
      });
      row.querySelector('[data-act="off"]').addEventListener('click', () => toggleDsdi(name));
      box.append(row);
    }
  }

  function toggleDsdi(name) {
    const i = dsdi.on.indexOf(name);
    if (i >= 0) dsdi.on.splice(i, 1);
    else {
      dsdi.on.push(name);
      setDsdiDock(true); // show the layer just switched on
    }
    syncDsdi();
    renderDsdiActive();
    renderDsdiList();
    applyOverlays(); // a DSDI boundary layer stands in for the portal's own lines of that level
  }

  // Adds the switched-on layers missing from the map (also after a basemap change) and removes the rest
  async function syncDsdi() {
    if (!map.getLayer('focus-mask')) return;
    const want = new Set(dsdi.on.map((n) => dsdi.byName.get(n)?.id));
    for (const l of dsdi.layers) {
      if (want.has(l.id) || !map.getSource(l.id)) continue;
      if (map.getLayer(l.id)) map.removeLayer(l.id);
      OWN.delete(l.id);
      map.removeSource(l.id);
    }
    for (const name of [...dsdi.on]) {
      const l = dsdi.byName.get(name);
      if (map.getSource(l.id)) continue;
      const det = await dsdiDetail(name); // bounds keep the map from asking for tiles outside the layer
      if (!dsdi.on.includes(name) || map.getSource(l.id)) continue; // switched off, or added, while waiting
      // Area layers can be outlines only; boundaries start that way, so they don't cover the water
      if (/polygon/i.test(det?.geometry || '') && !dsdi.areas.has(name)) {
        dsdi.areas.add(name);
        if (l.group === 'boundary') dsdi.outline.add(name);
        renderDsdiActive();
      }
      const b = det?.bounds;
      map.addSource(l.id, {
        type: 'raster', tiles: [dsdiTiles(name)], tileSize: 256,
        ...(b && b.every(Number.isFinite) ? { bounds: [Math.max(b[0], -180), Math.max(b[1], -85), Math.min(b[2], 180), Math.min(b[3], 85)] } : {}),
        attribution: 'NDMA DSDI',
      });
      add({ id: l.id, type: 'raster', source: l.id, paint: {
        'raster-opacity': dsdi.opacity.get(name) ?? DSDI_OPACITY, 'raster-fade-duration': 150, 'raster-brightness-min': dsdiLift(name),
      } }, dsdiBefore());
    }
    // keep the drawing order of the list: later layers on top
    for (const name of dsdi.on) {
      const id = dsdi.byName.get(name).id;
      if (map.getLayer(id)) map.moveLayer(id, dsdiBefore());
    }
  }
  const dsdiBefore = () => map.getStyle().layers.find((l) => l.type === 'symbol' && OWN.has(l.id))?.id;

  function onDsdiError(e) {
    const l = dsdi.layers.find((x) => x.id === e.sourceId);
    if (!l) return;
    const n = (dsdi.failed.get(l.id) || 0) + 1;
    dsdi.failed.set(l.id, n);
    if (n === 1) renderDsdiActive();
  }

  // Click info: what each switched-on DSDI layer holds at the spot, from GeoServer
  function dsdiInfo(box, lngLat) {
    const names = [...dsdi.on].reverse();
    if (!names.length) return;
    const r = 360 / (512 * 2 ** map.getZoom()); // degrees of longitude per screen pixel
    const h = 4.5 * r, v = 4.5 * r * Math.cos((lngLat.lat * Math.PI) / 180);
    const bbox = [lngLat.lng - h, lngLat.lat - v, lngLat.lng + h, lngLat.lat + v].map((x) => x.toFixed(6)).join(',');
    box.innerHTML = '<div class="fx-title">What NDMA DSDI layers show here</div><div class="loading"><span class="pulse"></span>Asking DSDI…</div>';
    const label = (k) => k.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
    const value = (x) => (typeof x === 'number' ? x.toLocaleString('en-US', { maximumFractionDigits: 3 }) : String(x).length > 140 ? `${String(x).slice(0, 140)}…` : String(x));
    Promise.all(names.map((name) => fetch(dsdiUrl('info', { layer: name, bbox })).then((res) => (res.ok ? res.json() : null)).catch(() => null)))
      .then((answers) => {
        if (!box.isConnected) return;
        const parts = answers.map((a, i) => {
          const l = dsdi.byName.get(names[i]);
          if (!a) return `<div class="dsdi-hit"><b>${esc(l.title)}</b><span class="none">DSDI did not answer</span></div>`;
          if (!a.features.length) return '';
          return `<div class="dsdi-hit"><b>${esc(l.title)}</b>${[...new Set(a.features.map((f) => {
            const rows = Object.entries(f).slice(0, 12).map(([k, x]) => `<tr><td>${esc(label(k))}</td><td>${esc(value(x))}</td></tr>`).join('');
            return rows ? `<table class="fx-table">${rows}</table>` : '<span class="none">Present at this spot (no details recorded)</span>';
          }))].join('')}</div>`;
        }).filter(Boolean);
        box.innerHTML = `<div class="fx-title">What NDMA DSDI layers show here</div><div class="fx-note dsdi-src">A second source: its names and lines can differ from the portal's own boundaries, which the portal's figures use.</div>${parts.length ? parts.join('')
          : `<div class="fx-none">Nothing from the ${names.length > 1 ? `${names.length} switched-on DSDI layers` : 'switched-on DSDI layer'} at this spot.</div>`}`;
        if (popup) requestAnimationFrame(() => keepPopupInView(popup));
      });
  }

  function setDsdiDock(open) {
    $('#dsdi-dock').classList.toggle('collapsed', !open);
    $('#dsdi-dock-toggle').setAttribute('aria-expanded', String(open));
  }

  function buildDsdi() {
    if (!DS) { $('#dsdi').hidden = true; return; }
    $('#dsdi-dock-toggle').addEventListener('click', () => setDsdiDock($('#dsdi-dock').classList.contains('collapsed')));
    $('#dsdi-q').addEventListener('input', renderDsdiList);
    loadDsdi();
  }

  // ------------------------------------------------------------------ top bar tools
  function buildBasemaps() {
    const seg = $('#basemaps');
    for (const b of CFG.basemaps) {
      const btn = el('button', { role: 'radio', 'aria-checked': String(b.id === state.basemap), 'data-id': b.id }, b.label);
      btn.addEventListener('click', () => setBasemap(b.id));
      seg.append(btn);
    }
  }

  function setBasemap(id) {
    if (id === state.basemap) return;
    state.basemap = id;
    for (const b of $('#basemaps').children) b.setAttribute('aria-checked', String(b.dataset.id === id));
    OWN.clear();
    map.setStyle(basemap(id).style, { diff: false });
  }

  function pressed(btn, on) { btn.setAttribute('aria-pressed', String(on)); }

  function buildTools() {
    const b3d = $('#btn-3d');
    b3d.addEventListener('click', () => {
      state.terrain = !state.terrain;
      pressed(b3d, state.terrain);
      applyTerrain();
      map.easeTo({ pitch: state.terrain ? 62 : 32, duration: 1400 });
    });

    const bSpin = $('#btn-spin');
    bSpin.addEventListener('click', () => {
      state.spin = !state.spin;
      pressed(bSpin, state.spin);
      if (state.spin) map.easeTo({ zoom: Math.min(map.getZoom(), 2.3), pitch: 0, bearing: 0, duration: 1800 });
    });

    $('#btn-home').addEventListener('click', () => setScope({ level: 'national' }));

    $('#btn-full').addEventListener('click', () => {
      if (document.fullscreenElement) document.exitFullscreen();
      else document.documentElement.requestFullscreen?.();
    });

    $('#zoom-in').addEventListener('click', () => map.zoomIn());
    $('#zoom-out').addEventListener('click', () => map.zoomOut());
    $('#compass').addEventListener('click', () => map.easeTo({ bearing: 0, pitch: 0, duration: 800 }));

    for (const btn of document.querySelectorAll('.collapse')) {
      btn.addEventListener('click', () => $(`#${btn.dataset.panel}`).classList.toggle('collapsed'));
    }
    for (const btn of document.querySelectorAll('.mobile-dock button')) {
      btn.addEventListener('click', () => {
        const target = $(`#${btn.dataset.open}`);
        const open = !target.classList.contains('open');
        for (const p of document.querySelectorAll('.panel')) p.classList.remove('open');
        for (const b of document.querySelectorAll('.mobile-dock button')) pressed(b, false);
        target.classList.toggle('open', open);
        pressed(btn, open);
      });
    }
  }

  // Slow globe rotation while zoomed out
  let interacting = false;
  function spin() {
    if (!state.spin || interacting || map.getZoom() > 5) return;
    const c = map.getCenter();
    c.lng -= 6;
    map.easeTo({ center: c, duration: 1000, easing: (n) => n });
  }
  map.on('mousedown', () => { interacting = true; });
  map.on('touchstart', () => { interacting = true; });
  map.on('mouseup', () => { interacting = false; spin(); });
  map.on('touchend', () => { interacting = false; spin(); });
  map.on('moveend', spin);
  map.on('moveend', syncBuildings);

  // ------------------------------------------------------------------ insights
  // A region's figures for the chosen water source: the same fields as stats.json, from water_types.json
  function sourced(rec, group, key) {
    if (state.source === 'all' || !rec) return rec;
    const all = D.waterTypes.sources[state.source];
    return { ...rec, ...(group ? all[group][key] : all.national) };
  }

  function scopeRecord() {
    const s = state.scope;
    if (s.level === 'province') return sourced(D.provStats.get(s.name), 'provinces', s.name);
    if (s.level === 'district') return sourced(D.distStats.get(s.id), 'districts', String(s.id));
    return sourced(D.stats.national);
  }

  function setSource(id) {
    state.source = id;
    for (const L of CFG.layers) if (L.sourced) map.getSource(`gsw-${L.id}`)?.setTiles([tileUrl(L.id, '{z}', '{x}', '{y}', id)]);
    renderInsights();
    applyLayer();
  }

  function scopeFeature() {
    const s = state.scope;
    if (s.level === 'province') return D.provFeat.get(s.name);
    if (s.level === 'district') return D.distFeat.get(s.id);
    return null;
  }

  function countUp(node, to) {
    if (reducedMotion) { node.textContent = fmtArea(to); return; }
    const t0 = performance.now();
    const step = (t) => {
      const k = Math.min(1, (t - t0) / 900);
      node.textContent = fmtArea(to * (1 - (1 - k) ** 3));
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  const WAVE = `<svg class="wave" viewBox="0 0 150 60" aria-hidden="true">
    <path d="M0 38q19-12 37.5 0t37.5 0 37.5 0 37.5 0V60H0z" fill="rgba(34,211,238,.16)"/>
    <path d="M0 46q19-10 37.5 0t37.5 0 37.5 0 37.5 0V60H0z" fill="rgba(34,211,238,.22)"/></svg>`;

  function renderInsights() {
    const s = state.scope;
    const rec = scopeRecord();

    const crumbs = $('#crumbs');
    crumbs.innerHTML = '';
    const parts = [{ label: 'Pakistan', scope: { level: 'national' } }];
    if (s.level !== 'national') {
      const prov = s.level === 'province' ? s.name : rec.province;
      parts.push({ label: prov, scope: { level: 'province', name: prov } });
    }
    if (s.level === 'district') parts.push({ label: rec.name, scope: s });
    parts.forEach((p, i) => {
      if (i) crumbs.append(el('span', { class: 'chev' }, '›'));
      const last = i === parts.length - 1;
      const node = el(last ? 'span' : 'button', last ? { class: 'cur' } : {}, esc(p.label));
      if (!last) node.addEventListener('click', () => setScope(p.scope));
      crumbs.append(node);
    });

    // On the map too, so the way back is there even with the Insights panel closed
    const chip = $('#scope-chip');
    chip.hidden = s.level === 'national';
    if (!chip.hidden) {
      const district = s.level === 'district';
      chip.innerHTML = `<span>Focused on <b>${esc(rec.name)}</b> ${district ? 'district' : 'province'}</span>
        ${district ? `<button type="button" data-up title="Show all of ${esc(rec.province)}">All ${esc(rec.province)}</button>` : ''}
        <button type="button" data-home>← Back to all Pakistan</button>`;
      chip.querySelector('[data-home]').addEventListener('click', () => setScope({ level: 'national' }));
      chip.querySelector('[data-up]')?.addEventListener('click', () => setScope({ level: 'province', name: rec.province }));
    }

    const net = rec.gained_km2 - rec.lost_km2;
    $('#kpi-hero').innerHTML = `
      <div class="label"><span class="dot" style="background:var(--accent);box-shadow:0 0 8px var(--accent)"></span>Maximum water extent · 1984–2024${state.source !== 'all' ? ` · ${esc(SOURCES.get(state.source).name)}` : ''}</div>
      <div class="value"><span data-count="${rec.extent_km2}">0</span><small>km²</small></div>
      <div class="sub">
        <span class="chip">${fmtPct(pct(rec.extent_km2, rec.area_km2))} of ${fmtArea(rec.area_km2)} km²</span>
        <span class="chip ${net >= 0 ? 'up' : 'down'}" title="Water gained minus water lost">${net >= 0 ? '+' : '−'}${fmtArea(Math.abs(net))} km² net</span>
      </div>${WAVE}`;

    const kpis = [
      ['Permanent water', rec.permanent_km2, '#3b82f6', 'in 2024'],
      ['Seasonal water', rec.seasonal_km2, '#7dd3fc', 'in 2024'],
      ['Water gained', rec.gained_km2, '#34d399', '1984 to 2024'],
      ['Water lost', rec.lost_km2, '#fb7185', '1984 to 2024'],
    ];
    $('#kpi-grid').innerHTML = kpis.map(([label, v, c, when]) => `
      <div class="kpi"><div class="label"><span class="dot" style="background:${c}"></span>${label}</div>
      <div class="value"><span data-count="${v}">0</span><small>km²</small></div><div class="kpi-when">${when}</div></div>`).join('');
    for (const n of document.querySelectorAll('#panel-insights [data-count]')) countUp(n, Number(n.dataset.count));

    renderSources();
    renderTimelineChart();
    renderRanking();
  }

  // Water sources in this region, and the switch between them: a click filters the sourced layers and the figures above
  function renderSources() {
    const s = state.scope;
    const all = D.waterTypes.sources;
    const extentOf = (id) => {
      if (id === 'all') return (s.level === 'province' ? D.provStats.get(s.name) : s.level === 'district' ? D.distStats.get(s.id) : D.stats.national).extent_km2;
      const src = all[id];
      return (s.level === 'province' ? src.provinces[s.name] : s.level === 'district' ? src.districts[String(s.id)] : src.national)?.extent_km2 || 0;
    };
    const parts = CFG.sources.filter((x) => x.id !== 'all').map((x) => [x, extentOf(x.id)]);
    const total = extentOf('all');
    $('#source-bar').innerHTML = parts.map(([x, v]) => `<span style="flex-grow:${v};background:${x.color}" title="${esc(x.name)}: ${fmtArea(v)} km²"></span>`).join('');
    const ul = $('#source-list');
    ul.innerHTML = '';
    for (const x of CFG.sources) {
      const v = extentOf(x.id);
      const li = el('li', {}, `<button type="button" role="radio" aria-checked="${state.source === x.id}" title="${esc(x.about)}">
        <span class="swatch" style="background:${x.color}"></span><span>${esc(x.name)}</span>
        <span class="v">${fmtArea(v)}</span><span class="p">${x.id === 'all' ? '' : fmtPct(pct(v, total))}</span></button>`);
      li.firstElementChild.addEventListener('click', () => setSource(x.id));
      ul.append(li);
    }
    $('#source-about').textContent = SOURCES.get(state.source).about + '.';
  }

  function rankingItems() {
    const s = state.scope;
    if (s.level === 'national') {
      return {
        title: 'Provinces & territories',
        items: D.stats.provinces.map((p) => ({ label: p.name, rec: sourced(p, 'provinces', p.name), scope: { level: 'province', name: p.name } })),
      };
    }
    const prov = s.level === 'province' ? s.name : D.distStats.get(s.id).province;
    return {
      title: `Districts · ${prov}`,
      items: D.stats.districts.filter((d) => d.province === prov).map((d) => ({
        label: d.name, rec: sourced(d, 'districts', String(d.id)), scope: { level: 'district', id: d.id }, cur: s.level === 'district' && s.id === d.id,
      })),
    };
  }

  function buildMetricTabs() {
    const tabs = $('#metric-tabs');
    for (const m of METRICS) {
      const b = el('button', { role: 'tab', 'aria-selected': String(m.key === state.metric), 'data-key': m.key }, m.label);
      b.addEventListener('click', () => {
        state.metric = m.key;
        state.highlight = true;
        for (const t of tabs.children) t.setAttribute('aria-selected', String(t.dataset.key === m.key));
        renderRanking();
        applyLayer();
      });
      tabs.append(b);
    }
  }

  function renderRanking() {
    const { title, items } = rankingItems();
    const m = METRICS.find((x) => x.key === state.metric);
    $('#rank-title').textContent = title;
    items.sort((a, b) => b.rec[m.key] - a.rec[m.key]);
    const max = Math.max(...items.map((i) => i.rec[m.key]), 1e-9);
    const ol = $('#ranking');
    ol.innerHTML = '';
    const bars = [];
    for (const it of items) {
      const li = el('li', { class: it.cur ? 'cur' : '', style: `--c:${m.color}` },
        `<span class="n">${esc(it.label)}</span><span class="v">${fmtArea(it.rec[m.key])} km²</span><span class="bar"><i></i></span>`);
      li.addEventListener('click', () => setScope(it.scope));
      ol.append(li);
      bars.push([li.querySelector('i'), (it.rec[m.key] / max) * 100]);
    }
    requestAnimationFrame(() => requestAnimationFrame(() => { for (const [i, w] of bars) i.style.width = `${w}%`; }));
  }

  function panelPadding() {
    if (isMobile()) return { top: 140, bottom: 90, left: 30, right: 30 };
    const side = (id) => ($(`#${id}`).classList.contains('collapsed') ? 80 : 390);
    return { top: 120, bottom: 90, left: side('panel-layers'), right: side('panel-insights') };
  }

  // Desktop uses the configured home view; narrow screens fit the whole country.
  function homeCamera() {
    if (!isMobile()) return { ...CFG.home, pitch: state.terrain ? 55 : CFG.home.pitch };
    const [w, s, e, n] = D.manifest.bounds;
    return { ...map.cameraForBounds([[w, s], [e, n]], { padding: { top: 150, bottom: 80, left: 12, right: 12 } }), pitch: 0, bearing: 0 };
  }

  function focusRiver(name) {
    const r = D.rivers.get(name);
    state.river = name;
    state.place = null;
    state.spin = false;
    pressed($('#btn-spin'), false);
    applySelection();
    const [w, s, e, n] = r.bbox;
    map.fitBounds([[w, s], [e, n]], { padding: panelPadding(), pitch: state.terrain ? 55 : 20, bearing: 0, maxZoom: 11, duration: 2400, essential: true });
  }

  // On Monthly or Yearly Water a region's chart is the point, so its map stays
  function setScope(scope, { fly = true, highlight = scope.level !== 'national' && !tlKind() } = {}) {
    state.scope = scope;
    state.highlight = highlight;
    state.river = null;
    state.place = null;
    state.spin = false;
    pressed($('#btn-spin'), false);
    renderInsights();
    applySelection();
    applyLayer();
    if (!fly) return;
    const f = scopeFeature();
    if (f) {
      const [w, s, e, n] = f.properties.bbox;
      map.fitBounds([[w, s], [e, n]], { padding: panelPadding(), pitch: state.terrain ? 55 : 28, bearing: 0, maxZoom: 10.5, duration: 2400, essential: true });
    } else {
      map.flyTo({ ...homeCamera(), duration: 2400, essential: true });
    }
  }

  // Pins a village/town, opens its district's insights and flies to it
  function focusPlace(p) {
    const d = p.district;
    setScope(d ? { level: 'district', id: d.id } : p.province && D.provStats.has(p.province) ? { level: 'province', name: p.province } : { level: 'national' }, { fly: false, highlight: false });
    state.place = p;
    applySelection();
    map.flyTo({ center: [p.lng, p.lat], zoom: p.rank <= 1 ? 11.5 : 15, pitch: state.terrain ? 55 : 50, bearing: 0, padding: panelPadding(), duration: 2600, essential: true });
  }

  // ------------------------------------------------------------------ search
  function buildSearch() {
    // order breaks ties between equally good matches: provinces, districts, cities, rivers, towns, villages
    const entry = (label, tag, order, extra = {}) => ({ label, tag, order, lower: label.toLowerCase(), key: placeKey(label), ...extra });
    const index = [
      ...D.stats.provinces.map((p) => entry(p.name, 'Province', 0, { scope: { level: 'province', name: p.name } })),
      ...D.stats.districts.map((d) => entry(d.name, `District · ${d.province}`, 1, { scope: { level: 'district', id: d.id }, ctx: d.province.toLowerCase() })),
      ...[...D.rivers.values()].map((r) => entry(r.name, WATERWAYS[r.kind].label, 3, { river: r.name })),
    ];
    let placesState = 'idle';
    const loadPlaces = () => {
      if (placesState !== 'idle') return;
      placesState = 'loading';
      ensurePlaces().then((places) => {
        for (const p of places) {
          const where = p.district ? `${p.district.name}, ${p.province}` : p.province;
          index.push(entry(p.name, `${PLACE_KINDS[p.kind]}${where ? ` · ${where}` : ''}`, p.rank === 0 ? 2 : p.rank === 1 ? 4 : 5, {
            place: p, ctx: where.toLowerCase(), altLower: p.alt && p.alt.toLowerCase(), altKey: p.alt && placeKey(p.alt),
          }));
        }
        placesState = 'ready';
        if (input.value.trim()) search();
      }).catch((err) => { placesState = 'idle'; console.error(err); });
    };

    // 0-3: the whole query is in the name; 4+: every word matches, some only by sound (Kanjal ~ Kunjal)
    // or by the district/province name, e.g. "kanjal layyah"
    const score = (r, q, words) => {
      for (const s of [r.lower, r.altLower]) {
        if (!s) continue;
        if (s === q) return 0;
        if (s.startsWith(q)) return 1;
        if (s.includes(` ${q}`)) return 2;
        if (s.includes(q)) return 3;
      }
      let named = false, fuzzy = false, ctx = false;
      for (const w of words) {
        if (r.lower.includes(w.s) || r.altLower?.includes(w.s)) named = true;
        else if (w.k.length >= 2 && (r.key.includes(w.k) || r.altKey?.includes(w.k))) named = fuzzy = true;
        else if (r.ctx?.includes(w.s)) ctx = true;
        else return -1;
      }
      return named ? 4 + fuzzy + 2 * ctx : -1;
    };

    const input = $('#search');
    const list = $('#search-results');
    let results = [];
    let active = 0;

    const search = () => {
      const q = input.value.trim().toLowerCase().replace(/\s+/g, ' ');
      if (!q) { results = []; render(); return; }
      const words = q.split(' ').map((s) => ({ s, k: placeKey(s) }));
      const hits = [];
      for (const r of index) {
        const sc = score(r, q, words);
        if (sc >= 0) hits.push([sc, r]);
      }
      results = hits
        .sort(([a, r], [b, s]) => a - b || r.order - s.order || r.label.length - s.label.length || r.label.localeCompare(s.label))
        .slice(0, 10).map(([, r]) => r);
      active = 0;
      render();
    };

    const render = () => {
      list.innerHTML = '';
      if (!input.value.trim()) { list.classList.remove('open'); return; }
      if (!results.length) list.append(el('li', { class: 'empty' }, placesState === 'loading' ? 'Loading villages and towns…' : 'No matching place or waterway'));
      results.forEach((r, i) => {
        const li = el('li', { role: 'option', 'aria-selected': String(i === active) }, `<span>${esc(r.label)}</span><span class="tag">${esc(r.tag)}</span>`);
        li.addEventListener('mousedown', (e) => { e.preventDefault(); choose(r); });
        list.append(li);
      });
      list.classList.add('open');
    };
    const choose = (r) => {
      input.value = '';
      list.classList.remove('open');
      input.blur();
      if (r.place) focusPlace(r.place);
      else if (r.river) focusRiver(r.river);
      else setScope(r.scope);
    };

    input.addEventListener('input', () => { loadPlaces(); search(); });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { active = Math.min(active + 1, results.length - 1); render(); e.preventDefault(); }
      else if (e.key === 'ArrowUp') { active = Math.max(active - 1, 0); render(); e.preventDefault(); }
      else if (e.key === 'Enter' && results[active]) choose(results[active]);
      else if (e.key === 'Escape') { input.value = ''; render(); input.blur(); }
    });
    input.addEventListener('blur', () => list.classList.remove('open'));
    input.addEventListener('focus', () => { loadPlaces(); render(); });
    document.addEventListener('keydown', (e) => {
      if (e.key === '/' && document.activeElement !== input) { e.preventDefault(); input.focus(); }
    });
  }

  // ------------------------------------------------------------------ pixel probe
  // Reads the max-zoom (~30 m) tile under the click and maps the pixel colour back to its JRC value.
  const tileCache = new Map();

  function tilePixels(layer, z, x, y) {
    const key = `${layer}/${z}/${x}/${y}`;
    if (!tileCache.has(key)) {
      tileCache.set(key, fetch(tileUrl(layer, z, x, y)).then(async (r) => {
        if (!r.ok) return null;
        const bmp = await createImageBitmap(await r.blob(), { premultiplyAlpha: 'none', colorSpaceConversion: 'none' });
        const cv = document.createElement('canvas');
        cv.width = cv.height = 256;
        const ctx = cv.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(bmp, 0, 0);
        return ctx.getImageData(0, 0, 256, 256).data;
      }).catch(() => null));
      if (tileCache.size > 120) tileCache.delete(tileCache.keys().next().value);
    }
    return tileCache.get(key);
  }

  function colorToValue(layer, r, g, b) {
    const L = D.lookup[layer];
    const hit = L.exact.get((r << 16) | (g << 8) | b);
    if (hit !== undefined) return hit;
    let best = null, bd = Infinity;
    for (const [v, c] of L.entries) {
      const d = (c[0] - r) ** 2 + (c[1] - g) ** 2 + (c[2] - b) ** 2;
      if (d < bd) { bd = d; best = v; }
    }
    return bd < 300 ? best : null;
  }

  // Global pixel coordinates at the max (~30 m) zoom
  function globalPixel(lng, lat) {
    const n = 2 ** CFG.tileMaxZoom * 256;
    const r = (lat * Math.PI) / 180;
    return {
      gx: Math.floor(((lng + 180) / 360) * n),
      gy: Math.floor(((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * n),
    };
  }

  async function readPixel(layer, gx, gy) {
    const data = await tilePixels(layer, CFG.tileMaxZoom, gx >> 8, gy >> 8);
    if (!data) return null;
    const i = ((gy & 255) * 256 + (gx & 255)) * 4;
    if (data[i + 3] < 128) return null;
    const value = colorToValue(layer, data[i], data[i + 1], data[i + 2]);
    return value === null ? null : { value, color: [data[i], data[i + 1], data[i + 2]] };
  }

  // Values at the clicked pixel. A dry click snaps to the nearest water pixel within
  // about eight screen pixels, since zoomed-out tiles show water that a 30 m pixel may miss.
  async function sampleAll(lng, lat) {
    let { gx, gy } = globalPixel(lng, lat);
    let snapped = 0;
    const R = Math.round(Math.min(96, Math.max(3, 8 * 2 ** (CFG.tileMaxZoom - 1 - map.getZoom()))));
    const keys = new Set();
    for (const tx of [(gx - R) >> 8, (gx + R) >> 8]) for (const ty of [(gy - R) >> 8, (gy + R) >> 8]) keys.add(`${tx}/${ty}`);
    const ext = new Map(await Promise.all([...keys].map(async (k) => {
      const [tx, ty] = k.split('/').map(Number);
      return [k, await tilePixels('extent', CFG.tileMaxZoom, tx, ty)];
    })));
    const wet = (x, y) => {
      const d = ext.get(`${x >> 8}/${y >> 8}`);
      return !!d && d[((y & 255) * 256 + (x & 255)) * 4 + 3] >= 128;
    };
    if (!wet(gx, gy)) {
      let best = null;
      for (let dy = -R; dy <= R; dy++) {
        for (let dx = -R; dx <= R; dx++) {
          const d = dx * dx + dy * dy;
          if (d <= R * R && (!best || d < best.d) && wet(gx + dx, gy + dy)) best = { dx, dy, d };
        }
      }
      if (best) {
        gx += best.dx; gy += best.dy;
        const mpp = (40075016.686 * Math.cos((lat * Math.PI) / 180)) / (256 * 2 ** CFG.tileMaxZoom);
        snapped = Math.sqrt(best.d) * mpp;
      }
    }
    const values = await Promise.all(PIXEL_LAYERS.map((L) => readPixel(L.id, gx, gy)));
    return { values, snapped };
  }

  function describe(layer, v) {
    switch (layer) {
      case 'extent': return 'Water detected';
      case 'occurrence': return `${v}% of the time`;
      case 'seasonality': return `${v} month${v > 1 ? 's' : ''} in 2024`;
      case 'recurrence': return `${v}% of years`;
      case 'change': {
        const d = v - 100;
        return d === 0 ? 'No change' : `${d > 0 ? '+' : '−'}${Math.abs(d)}% occurrence`;
      }
      case 'erosion': case 'landloss': case 'watertype': return CFG.layers.find((l) => l.id === layer).legend.classes[v] || '—';
      default: return CFG.classes[v] || '—';
    }
  }

  function waterwayAt(point) {
    const layers = ['ww-major', 'ww-qanat', 'ww-minor'].filter((id) => map.getLayer(id));
    if (!layers.length || !state.overlays.rivers) return null;
    const hits = map.queryRenderedFeatures([[point.x - 5, point.y - 5], [point.x + 5, point.y + 5]], { layers });
    return hits.find((f) => f.properties.name) || hits[0] || null;
  }

  function waterwayLine(w) {
    const k = WATERWAYS[w.properties.kind] || WATERWAYS.unnamed;
    const name = w.properties.name || 'Unnamed waterway';
    return `<span class="line-swatch" style="--c:${k.color}"></span>${esc(name)}${w.properties.name ? ` <span class="kind">${esc(k.label)}</span>` : ''}`;
  }

  // ------------------------------------------------------------------ administrative units
  // Union councils or tehsils, from prepare_admin_units.py. Loaded on the first click.
  let unitsReq = null;
  function ensureUnits() {
    unitsReq ||= getJSON('data/admin_units.geojson')
      .then((units) => { D.units = units; })
      .catch((err) => { unitsReq = null; console.error(err); });
    return unitsReq;
  }

  function inRing(x, y, ring) {
    let c = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i], [xj, yj] = ring[j];
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c;
    }
    return c;
  }

  function inFeature(f, x, y) {
    const g = f.geometry;
    return (g.type === 'Polygon' ? [g.coordinates] : g.coordinates).some((poly) => inRing(x, y, poly[0]) && !poly.slice(1).some((h) => inRing(x, y, h)));
  }

  function unitAt(x, y, fc = D.units) {
    for (const f of fc?.features || []) {
      const [w, s, e, n] = f.properties.bbox;
      if (x >= w && x <= e && y >= s && y <= n && inFeature(f, x, y)) return f;
    }
    return null;
  }

  // ------------------------------------------------------------------ district report
  // From prepare_district_reports.py: per district, the villages near the Indus, the land each lost, their risk of losing
  // more, land cut year by year and the places standing in the river's path. Loaded on the first district click.
  let reportsReq = null;
  function ensureReports() {
    reportsReq ||= getJSON('data/district_reports.json')
      .then((r) => { D.reports = r; })
      .catch((err) => { reportsReq = null; console.error(err); });
    return reportsReq;
  }

  const RISK_COLORS = ['#c42b31', '#ef5350', '#f39a52', '#f7c99c', '#3a4559'];
  const RISK_INK = ['#fff', '#fff', '#1d1006', '#1d1006', '#cbd5e1'];
  const acres = (v) => Math.round(v).toLocaleString('en-US');
  const shareOf = (a, b) => (b > 0 ? (100 * a) / b : 0);
  const pctText = (p) => (p >= 1 ? `${Math.round(p)}%` : p > 0 ? '<1%' : '–');
  // lost: land lost (dry in the 1980s, under water all year now); part: ground now flooded only part of the year, not lost
  const village = ([name, tehsil, kind, area, lost, part, recent, bld, bldPart, risk, lng, lat, uc = '']) =>
    ({ name, tehsil, kind, area, lost, part, recent, bld, bldPart, risk, lng, lat, uc });
  const placeRow = ([name, kind, where, cls, lng, lat, uc = '']) => ({ name, kind, where, cls, lng, lat, uc });
  const hasUcs = () => !!D.reports?.meta.union_councils;
  const recentYears = () => D.reports.meta.recent.join('–');
  const riskBadge = (k) => `<span class="rp-risk" style="--c:${RISK_COLORS[k]};--ink:${RISK_INK[k]}">${esc(D.reports.meta.risk[k])}</span>`;
  const barCell = (p, kind) => `<td class="rp-bc"><span class="rp-bar ${kind}"><i style="width:${Math.min(100, p).toFixed(1)}%"></i></span><em>${pctText(p)}</em></td>`;

  const between = (R) => (R.stretch[0] && R.stretch[1] && R.stretch[0] !== R.stretch[1] ? `${esc(R.stretch[0])} → ${esc(R.stretch[1])}` : '');
  const stretchText = (R) => `about ${fmtKm(R.km[1] - R.km[0])} km of the Indus${between(R) ? `, ${between(R)}` : ''}`;

  // A popup can be dragged by its grip or its title. It keeps to the spot clicked plus the distance dragged, so it
  // still moves with the map; its tip hides once moved, as it no longer points at the spot.
  function movable(p) {
    const box = p.getElement();
    const content = box?.querySelector('.mapboxgl-popup-content');
    if (!content) return p;
    content.prepend(el('div', { class: 'pop-grip', title: 'Drag to move' }, '<i></i>'));
    let drag = null;
    content.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 || !e.target.closest('.pop-grip, h4') || e.target.closest('button, a, input')) return;
      e.preventDefault();
      e.stopPropagation();
      if (!p.moved) {
        // Swap the anchor's own offset for an equal one in pixels, so the popup does not jump
        const before = box.getBoundingClientRect();
        p.setOffset([0, 0]);
        const after = box.getBoundingClientRect();
        p.moved = [before.left - after.left, before.top - after.top];
        p.setOffset(p.moved);
        p.addClassName('moved');
      }
      drag = { x: e.clientX, y: e.clientY, from: p.moved };
      content.setPointerCapture(e.pointerId);
      p.addClassName('dragging');
    });
    content.addEventListener('pointermove', (e) => {
      if (!drag) return;
      p.moved = [drag.from[0] + e.clientX - drag.x, drag.from[1] + e.clientY - drag.y];
      p.setOffset(p.moved);
      // Not under the side panels or off screen: the top must stay in view so it can be dragged back
      const r = box.getBoundingClientRect();
      const { L, R, T, B } = freeArea();
      const dx = fitInto(L, R, r.left, r.right);
      const dy = r.top < T ? T - r.top : r.top > B - 60 ? B - 60 - r.top : 0;
      if (dx || dy) {
        p.moved = [p.moved[0] + dx, p.moved[1] + dy];
        p.setOffset(p.moved);
      }
    });
    const end = (e) => {
      if (!drag) return;
      drag = null;
      p.removeClassName('dragging');
      if (content.hasPointerCapture?.(e.pointerId)) content.releasePointerCapture(e.pointerId);
    };
    content.addEventListener('pointerup', end);
    content.addEventListener('pointercancel', end);
    return p;
  }

  // Moves the map so a large popup sits clear of the header, the side panels and the status bar
  // The part of the screen the map shows between the header, the open side panels and the status bar
  function freeArea() {
    const mobile = isMobile();  // panels there are sheets over the map, not columns beside it
    const shown = (sel) => {
      if (mobile) return null;
      const n = $(sel);
      const r = n?.getBoundingClientRect();
      return r && r.width && r.right > 60 && r.left < innerWidth - 60 && !n.classList.contains('collapsed') ? r : null;
    };
    const lp = shown('#panel-layers'), rp = shown('#panel-insights'), dk = shown('#dsdi-dock');
    const L = Math.max(lp ? lp.right : 0, dk ? dk.right : 0) + 12, R = (rp ? rp.left : innerWidth) - 12;
    const bottomBar = $(mobile ? '.mobile-dock' : '.statusbar')?.getBoundingClientRect();
    const chip = $('#scope-chip');
    const T = Math.max($('.topbar')?.getBoundingClientRect().bottom || 0, chip.hidden ? 0 : chip.getBoundingClientRect().bottom) + 10, B = (bottomBar?.height ? bottomBar.top : innerHeight) - 10;
    return { L, R, T, B };
  }

  const fitInto = (lo, hi, a, b) => (b - a > hi - lo || a < lo ? lo - a : b > hi ? hi - b : 0);

  function keepPopupInView(p) {
    if (p?.moved) return;  // the user has placed it
    const box = p?.getElement()?.getBoundingClientRect();
    if (!box) return;
    const { L, R, T, B } = freeArea();
    const fit = fitInto;
    const dx = fit(L, R, box.left, box.right), dy = fit(T, B, box.top, box.bottom);
    if (Math.abs(dx) > 2 || Math.abs(dy) > 2) map.panBy([-dx, -dy], { duration: reducedMotion ? 0 : 450 });
  }

  function reportTiles(R, full) {
    const T = R.totals;
    const osm = D.reports.meta.buildings === 'osm' ? ' (OSM only)' : '';
    const t = (v, label, hot) => `<div class="rp-tile${hot ? ' hot' : ''}"><b>${v}</b><span>${label}</span></div>`;
    return `<div class="rp-tiles${full ? ' full' : ''}">
      ${t(acres(T.acres), 'acres of land lost: now under water all year', true)}
      ${t(fmtKm(T.villages_lost), 'villages lost land')}
      ${t(fmtKm(T.villages_high), 'villages at high or very high risk', true)}
      ${full && T.acres_part != null ? t(acres(T.acres_part), 'more acres now flood part of the year (not counted as lost)') : ''}
      ${full ? t(acres(T.recent_acres), `acres newly under water all year in ${recentYears()} (estimate)`, true) : ''}
      ${t(fmtKm(T.buildings), `buildings on land lost${osm}`)}
      ${t(fmtKm(T.in_path), 'settlements, schools, health centres and stations in its path')}
      ${full && T.emb_n != null ? t(`${T.emb_km}`, 'km of embankment on land lost') : ''}
    </div>`;
  }

  function topVillages(R, n = 15) {
    const rows = R.villages.slice(0, n).map(village);
    if (!rows.length) return '<div class="fx-none">No village near the river here has lost land since the 1980s.</div>';
    return `<table class="rp-tbl rp-top"><thead><tr><th>#</th><th>Village</th><th class="rp-teh">Tehsil</th><th>Area<br>(acres)</th>
        <th>Land lost to the river<br>since the 1980s (% of village)</th><th>Cut by the river<br>${recentYears()} (% of village)</th></tr></thead>
      <tbody>${rows.map((v, i) => `<tr data-i="${i}" title="Show ${esc(v.name)} on the map"><td><span class="rp-num">${i + 1}</span></td><td class="rp-name">${esc(v.name)}</td>
        <td class="rp-teh rp-dim">${esc(v.tehsil)}</td><td>${acres(v.area)}</td>${barCell(shareOf(v.lost, v.area), 'lost')}${barCell(shareOf(v.recent, v.area), 'recent')}</tr>`).join('')}</tbody></table>`;
  }

  // Numbered pins for the most affected villages, as on the printed district map
  function reportPins(R, n = 15) {
    map.getSource('dot-pins')?.setData({ type: 'FeatureCollection', features: R.villages.slice(0, n).map(village).map((v, i) => (
      { type: 'Feature', geometry: { type: 'Point', coordinates: [v.lng, v.lat] }, properties: { label: `${i + 1} ${v.name}` } })) });
  }

  function flyToPlace(name, lng, lat, layer = 'erosion') {
    $('#dr').hidden = true;
    popup?.remove();
    if (state.layer !== layer) setLayer(layer);
    map.getSource('dot-pins')?.setData({ type: 'FeatureCollection', features: [{ type: 'Feature', geometry: { type: 'Point', coordinates: [lng, lat] }, properties: { label: name } }] });
    map.flyTo({ center: [lng, lat], zoom: 12.6, duration: reducedMotion ? 0 : 1800, essential: true });
  }

  // The district card in the map popup: the headline figures and the 15 most affected villages
  function districtCard(R, distId) {
    const node = el('div', { class: 'fx rp' }, `
      <div class="fx-title">Indus river change and land-loss risk to villages · ${esc(D.reports.meta.period)}</div>
      <div class="rp-meta">${esc(R.province)} · ${stretchText(R)}</div>
      ${reportTiles(R, false)}
      <div class="rp-h">The 15 most affected villages</div>
      ${topVillages(R)}
      <div class="fx-note">Villages are named places; each is given the ground within ${D.reports.meta.place_km} km that is closer to it than to any other named place (no official mouza boundaries are available). Land lost: ground that was dry in the 1980s and is now under water all year (a lasting rise in the Occurrence Change layer). Ground now flooded only part of the year is not counted as lost. Ranked by risk of losing more land, then by land lost. Click a village to see it on the map.</div>`);
    const actions = el('div', { class: 'rp-actions' });
    const full = el('button', { class: 'go' }, 'Full district report →');
    full.addEventListener('click', () => openReport(distId));
    const csv = el('button', { class: 'rp-ghost' }, 'Download data (CSV)');
    csv.addEventListener('click', () => downloadReport(distId, 'villages'));
    actions.append(full, csv);
    node.append(actions);
    node.querySelectorAll('.rp-top tbody tr').forEach((tr) => tr.addEventListener('click', () => {
      const v = village(R.villages[+tr.dataset.i]);
      flyToPlace(`${+tr.dataset.i + 1} ${v.name}`, v.lng, v.lat);
    }));
    return node;
  }

  // ---- downloads
  function saveCSV(file, lines) {
    const q = (v) => (typeof v === 'number' ? String(v) : `"${String(v ?? '').replace(/"/g, '""')}"`);
    const text = lines.map((r) => r.map(q).join(',')).join('\r\n');
    const url = URL.createObjectURL(new Blob(['﻿', text], { type: 'text/csv' }));
    el('a', { href: url, download: file }).click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  const REPORT_TABLES = {
    summary: {
      title: 'Summary',
      head: () => ['Measure', 'Value'],
      rows: (R) => {
        const T = R.totals;
        return [
          ['Province', R.province], ['Indus river km (from the source)', `${R.km[0]}-${R.km[1]}`], ['Stretch', R.stretch.filter(Boolean).join(' to ')],
          ['Indus floodplain in the district (acres)', T.fp_acres],
          ['Land lost since the 1980s: dry then, under water all year now (acres)', T.acres],
          ['Ground now flooded part of the year, not counted as lost (acres)', T.acres_part ?? ''],
          ['Land the river gave back: under water all year then, dry now (acres)', T.acres_back], ['Cropland on land lost (acres)', T.crop_acres],
          [`Land newly under water all year ${recentYears()} (acres, net change, estimate)`, T.recent_acres],
          ['Union councils that lost an acre or more', T.ucs_lost ?? ''],
          ['Villages within 3 km of the floodplain', T.villages_near], ['Villages that lost land', T.villages_lost],
          ['Villages at high or very high risk', T.villages_high], ['Buildings on land lost', T.buildings],
          ['Buildings on ground flooded part of the year', T.buildings_part ?? ''],
          ['Buildings within 1 km of the river', T.buildings_near], ['Settlements, schools, health centres and stations in the river\'s path', T.in_path],
        ];
      },
    },
    years: {
      title: 'Land cut by the river each year',
      head: () => ['Year', 'Acres cut (estimate)', 'Confirmed by the year after'],
      rows: (R) => D.reports.meta.years.map((y, i) => [y, R.years[i], y >= D.reports.meta.provisional_from ? 'no' : 'yes']),
    },
    risk: {
      title: 'Villages by risk of losing more land',
      head: () => ['Risk', 'Villages', 'Meaning'],
      rows: (R) => D.reports.meta.risk.map((k, i) => [k, R.risk[i], D.reports.meta.risk_text[i]]),
    },
    tehsils: {
      title: 'By tehsil',
      head: () => ['Tehsil', 'Villages near the river', 'Villages that lost land', 'Acres lost (now under water all year)', 'Villages at high or very high risk', 'Buildings on land lost'],
      rows: (R) => R.tehsils,
    },
    ucs: {
      title: 'Land lost by union council',
      head: () => ['Union council', 'Tehsil', 'Acres lost (now under water all year)', 'Acres flooded part of the year (not lost)', 'Buildings on land lost', 'Villages in it that lost land'],
      rows: (R) => R.ucs || [],
    },
    embankments: {
      title: 'Embankments the river has reached',
      head: () => ['Embankment', 'Kind', 'River', 'km on land lost (now under water all year)', 'km on ground flooded part of the year', 'km in the wet belt', 'km in the floodplain', 'Longitude', 'Latitude'],
      rows: (R) => R.embankments || [],
    },
    villages: {
      title: 'Villages that lost land',
      head: () => ['Rank', 'Village', 'Union council', 'Tehsil', 'Kind of place', 'Village ground (acres)', 'Land lost since the 1980s: now under water all year (acres)', 'Lost (% of village)',
        'Ground flooded part of the year, not lost (acres)', `Cut by the river ${recentYears()} (acres)`, 'Cut recently (% of village)',
        'Buildings on land lost', 'Buildings on ground flooded part of the year', 'Risk', 'Longitude', 'Latitude'],
      rows: (R) => R.villages.map(village).map((v, i) => [i + 1, v.name, v.uc, v.tehsil, v.kind, v.area, v.lost, +shareOf(v.lost, v.area).toFixed(1), v.part,
        v.recent, +shareOf(v.recent, v.area).toFixed(1), v.bld, v.bldPart, D.reports.meta.risk[v.risk], v.lng, v.lat]),
    },
    path: {
      title: 'Places in the river\'s path',
      head: () => ['Name', 'Kind', 'Union council', 'Tehsil or nearest village', 'Where it stands', 'Longitude', 'Latitude'],
      rows: (R) => R.path.map(placeRow).map((p) => [p.name, p.kind, p.uc, p.where, D.reports.meta.where[p.cls], p.lng, p.lat]),
    },
  };

  function downloadReport(distId, which) {
    const R = D.reports?.districts[distId];
    if (!R) return;
    const base = `${R.name.toLowerCase().replace(/[^a-z0-9]+/g, '_')}_district`;
    const note = [`${R.name} district, ${R.province}. Pakistan Surface Water Observatory (NDMA), built ${D.reports.meta.built} from JRC Global Surface Water ${D.reports.meta.period}.`];
    if (which !== 'all') {
      const T = REPORT_TABLES[which];
      saveCSV(`${base}_${which}.csv`, [T.head(), ...T.rows(R)]);
      return;
    }
    const lines = [note, []];
    for (const T of Object.values(REPORT_TABLES)) lines.push([T.title.toUpperCase()], T.head(), ...T.rows(R), []);
    saveCSV(`${base}_report.csv`, lines);
  }

  // ---- the full report window
  function reportStep(max, n = 4) {
    const raw = max / n;
    const p = 10 ** Math.floor(Math.log10(raw || 1));
    return [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw) || p * 10;
  }

  function yearChart(R) {
    const M = D.reports.meta;
    const W = 680, H = 230, L = 50, Rt = 8, T = 12, B = 26;
    const vals = R.years;
    const step = reportStep(Math.max(...vals, 1));
    const top = Math.ceil(Math.max(...vals, 1) / step) * step;
    const y = (v) => T + (H - T - B) * (1 - v / top);
    const bw = (W - L - Rt) / vals.length;
    const w = Math.max(3, bw * 0.62);
    let grid = '';
    for (let v = 0; v <= top + 1e-9; v += step) {
      grid += `<line x1="${L}" x2="${W - Rt}" y1="${y(v)}" y2="${y(v)}" class="${v ? 'g' : 'base'}"/><text x="${L - 7}" y="${y(v) + 3.5}" text-anchor="end">${fmtKm(v)}</text>`;
    }
    const bars = vals.map((v, i) => {
      const x = L + i * bw + (bw - w) / 2;
      const y0 = y(0), y1 = y(v), r = Math.min(4, w / 2, y0 - y1);
      const d = v > 0 ? `M${x},${y0}V${y1 + r}Q${x},${y1} ${x + r},${y1}H${x + w - r}Q${x + w},${y1} ${x + w},${y1 + r}V${y0}Z` : '';
      const prov = M.years[i] >= M.provisional_from;
      return `<path d="${d}" class="${prov ? 'prov' : 'bar'}"/>`;
    }).join('');
    const labels = M.years.map((yr, i) => (yr % 5 === 0 || i === M.years.length - 1 ? `<text x="${L + i * bw + bw / 2}" y="${H - 8}" text-anchor="middle">${yr}</text>` : '')).join('');
    const hits = vals.map((v, i) => `<rect x="${L + i * bw}" y="${T}" width="${bw}" height="${H - T - B}" data-i="${i}" class="hit"/>`).join('');
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Acres the river cut each year, ${M.years[0]} to ${M.years.at(-1)}">
      <text x="12" y="${T + (H - T - B) / 2}" transform="rotate(-90 12 ${T + (H - T - B) / 2})" text-anchor="middle" class="axis">acres</text>
      ${grid}${bars}${labels}${hits}</svg>`;
  }

  function riskBars(R) {
    const M = D.reports.meta;
    const max = Math.max(...R.risk, 1);
    return `<div class="rp-riskbars">${M.risk.map((k, i) => `<div class="rp-rb" title="${esc(M.risk_text[i])}"><span>${esc(k)}</span>
      <div class="rp-rb-track"><i style="width:${((100 * R.risk[i]) / max).toFixed(1)}%;background:${RISK_COLORS[i]}"></i></div><b>${fmtKm(R.risk[i])}</b></div>`).join('')}</div>`;
  }

  function inShort(R) {
    const M = D.reports.meta;
    const T = R.totals;
    const worst = R.villages.slice(0, 3).map((r) => esc(r[0]));
    const years = M.years.map((y, i) => [y, R.years[i]]).filter(([y]) => y < M.provisional_from).sort((a, b) => b[1] - a[1]).slice(0, 3);
    const items = [
      `The Indus runs along ${esc(R.name)} for about ${fmtKm(R.km[1] - R.km[0])} km${between(R) ? ` (${between(R)})` : ''}.`,
      `About <b>${acres(T.acres)} acres</b> of land here have been lost to the river since the 1980s: dry land then, under water all year now. Another ${acres(T.acres_part)} acres now flood part of the year; they are not counted as lost. The river also gave back about ${acres(T.acres_back)} acres that were under water all year in the 1980s.`,
      `<b>${fmtKm(T.villages_lost)}</b> of the ${fmtKm(T.villages_near)} villages near the river lost land. <b>${fmtKm(T.villages_high)}</b> are at high or very high risk of losing more.`,
      worst.length ? `Worst hit villages: ${worst.join(', ')}.` : '',
      `In ${recentYears()} about <b>${acres(T.recent_acres)} acres</b> here came under water all year and were still under water a year later (an estimate from coarse cells, after taking away ground the river left again). Some of it was river in the 1980s too, so it is not all in the figure above.`,
      `${fmtKm(T.buildings)} buildings stand on land lost${T.buildings_part != null ? ` and ${fmtKm(T.buildings_part)} more on ground flooded part of the year` : ''}. ${fmtKm(T.in_path)} settlements, schools, health centres and stations stand in the river's path${T.path_schools != null ? ` (${fmtKm(T.path_schools)} school${T.path_schools === 1 ? '' : 's'}, ${fmtKm(T.path_health)} health facilit${T.path_health === 1 ? 'y' : 'ies'}${T.path_stations ? `, ${fmtKm(T.path_stations)} police, rescue or railway station${T.path_stations === 1 ? '' : 's'}` : ''})` : ''}.`,
      T.emb_n ? `${fmtKm(T.emb_n)} embankment ${T.emb_n === 1 ? 'stretch' : 'stretches'} here, about ${T.emb_km} km, ${T.emb_n === 1 ? 'stands' : 'stand'} on land lost to the river.` : '',
      borderNote(R),
      years.length ? `Worst years for land cut: ${years.map(([y, v]) => `${y} (about ${acres(v)} acres)`).join(', ')}.` : '',
    ].filter(Boolean);
    return `<ul class="rp-short">${items.map((t) => `<li>${t}</li>`).join('')}</ul>`;
  }

  function villageTable(R, all) {
    const rows = R.villages.map(village).map((v, i) => ({ ...v, i })).filter((v) => all || v.risk <= 2);
    const uc = hasUcs();
    return `<table class="rp-tbl rp-full${uc ? ' has-uc' : ''}"><thead><tr><th>#</th><th>Village</th>${uc ? '<th>Union council</th>' : ''}<th>Tehsil</th><th>Area<br>(acres)</th><th>Lost since the 1980s<br>(acres · % of village)</th>
        <th>Flooded part of<br>the year (acres)</th><th>Cut ${recentYears()}<br>(acres · % of village)</th><th>Buildings on<br>land lost</th><th>Risk</th></tr></thead>
      <tbody>${rows.map((v) => `<tr data-i="${v.i}"><td>${v.i + 1}</td><td class="rp-name">${esc(v.name)}</td>${uc ? `<td class="rp-dim">${esc(v.uc)}</td>` : ''}<td class="rp-dim">${esc(v.tehsil)}</td><td>${acres(v.area)}</td>
        <td>${acres(v.lost)} · ${pctText(shareOf(v.lost, v.area))}</td><td>${acres(v.part)}</td><td>${acres(v.recent)} · ${pctText(shareOf(v.recent, v.area))}</td>
        <td>${fmtKm(v.bld)}</td><td>${riskBadge(v.risk)}</td></tr>`).join('')}</tbody></table>`;
  }

  function pathTable(R) {
    const M = D.reports.meta;
    if (!R.path.length) return '<p class="rp-p">No mapped settlement, school or health facility stands in the river\'s path here.</p>';
    const uc = hasUcs();
    return `<table class="rp-tbl rp-full rp-path"><thead><tr><th>Name</th><th>Kind</th>${uc ? '<th>Union council</th>' : ''}<th>Tehsil or nearest village</th><th>Where it stands</th></tr></thead>
      <tbody>${R.path.map(placeRow).map((p, i) => `<tr data-i="${i}" class="w${p.cls}"><td class="rp-name">${esc(p.name)}</td><td>${esc(p.kind)}</td>${uc ? `<td class="rp-dim">${esc(p.uc)}</td>` : ''}<td class="rp-dim">${esc(p.where)}</td>
        <td>${esc(M.where[p.cls])}</td></tr>`).join('')}</tbody></table>`;
  }

  // DSDI protection bands standing on land the river took since the 1980s: the stretches to inspect first
  function embankmentTable(R) {
    const rows = R.embankments || [];
    if (!rows.length) return `<p class="rp-p">No embankment in NDMA DSDI's protection band layer stands on land lost to the river in ${esc(R.name)}.</p>`;
    const km = (v) => (v >= 10 ? v.toFixed(1) : v.toFixed(2));
    return `<p class="rp-p">${fmtKm(rows.length)} embankment stretches in ${esc(R.name)} stand on land lost to the river since the 1980s (now under water all year): about ${km(R.totals.emb_km)} km in all. The river has reached these lines, so they are the ones to check first. From NDMA DSDI's protection band layer (bunds, spurs and studs); many have no name there. Click a row to see it on the map.</p>
      <table class="rp-tbl rp-full rp-emb"><thead><tr><th>Embankment</th><th>Kind</th><th>River</th><th>km on<br>land lost</th><th>km flooded part<br>of the year</th><th>km in the<br>wet belt</th><th>km in the<br>floodplain</th></tr></thead>
      <tbody>${rows.map((e, i) => `<tr data-i="${i}" title="Show it on the map"><td class="rp-name">${esc(e[0])}</td><td>${esc(e[1])}</td><td class="rp-dim">${esc(e[2] || '–')}</td><td>${km(e[3])}</td><td>${km(e[4])}</td><td>${km(e[5])}</td><td>${km(e[6])}</td></tr>`).join('')}</tbody></table>`;
  }

  // Where a district border follows the river, the line decides which district the land the river took is counted in
  function borderNote(R) {
    const b = R.border;
    if (!b || !D.reports.meta.dsdi_districts) return '';
    const T = R.totals;
    if (b.match < 0.9) {
      return `NDMA DSDI's district map has no ${esc(R.name)} district: there this land is part of ${esc(b.name)}.`;
    }
    const diff = b.acres - T.acres;
    if (Math.abs(diff) < Math.max(500, 0.02 * T.acres)) return '';
    return `Part of this district's border follows the river, so the line decides where the land lost to the river is counted. With NDMA DSDI's district map, ${esc(R.name)} has <b>${acres(b.acres)} acres</b> instead of ${acres(T.acres)} (${diff > 0 ? '+' : '−'}${acres(Math.abs(diff))}); the difference is counted in the neighbouring district. The total along the river is the same either way.`;
  }

  function section(id, title, body, csv = true) {
    return `<section class="rp-sec" id="dr-${id}"><div class="rp-sec-head"><h3>${title}</h3>${csv ? `<button class="fp-btn" data-csv="${csv === true ? id : csv}">CSV</button>` : ''}</div>${body}</section>`;
  }

  let reportId = null;
  function openReport(distId) {
    const R = D.reports?.districts[distId];
    if (!R) return;
    reportId = distId;
    reportKind = 'district';
    $('#dr-eyebrow').textContent = 'District report · Indus river change';
    const M = D.reports.meta;
    const T = R.totals;
    const nAll = R.villages.length;
    const nShown = R.villages.filter((r) => r[9] <= 2).length;
    $('#dr-title').textContent = `${R.name} District`;
    $('#dr-sub').innerHTML = `Indus river change and land-loss risk to villages, ${esc(M.period)} · ${esc(R.province)} · ${stretchText(R)}`;
    $('#dr-body').innerHTML = `
      ${reportTiles(R, true)}
      ${section('short', 'In short', inShort(R), 'summary')}
      <div class="rp-two">
        ${section('years', 'Land cut by the river each year', `<p class="rp-p">Acres of fields, bushes and homes in the ${esc(R.name)} floodplain that came under water all year, counted only when they stayed under water the year after. Lighter bar: ${M.provisional_from}, not yet confirmed by the year after.</p>
          <div class="rp-chart">${yearChart(R)}<div class="rp-tip" hidden></div></div>
          <p class="rp-small">Estimated from the JRC monthly water record in cells of about 275 m, so it shows which years were worst; the acres are approximate. The years can't be added up: ground the river leaves and takes again counts in each year it is taken. The ${recentYears()} figure above is the change over those years as a whole. The record ends in December ${M.provisional_from}.</p>`)}
        ${section('risk', 'Villages by risk of losing more land', `${riskBars(R)}
          <p class="rp-small">Risk combines the share of a village's ground already lost since the 1980s and the share the river cut in ${recentYears()}. Very high: ${esc(M.risk_text[0])}. A guide from the satellite record, not a forecast. Hover a bar for its rule.</p>`)}
      </div>
      ${section('tehsils', 'By tehsil', R.tehsils.length ? `<table class="rp-tbl rp-full"><thead><tr><th>Tehsil</th><th>Villages<br>near river</th><th>Lost land</th><th>Acres lost</th><th>High / very<br>high risk</th><th>Buildings on<br>land lost</th></tr></thead>
          <tbody>${R.tehsils.map((t) => `<tr><td class="rp-name">${esc(t[0])}</td><td>${fmtKm(t[1])}</td><td>${fmtKm(t[2])}</td><td>${t[3] == null ? '–' : acres(t[3])}</td><td>${fmtKm(t[4])}</td><td>${t[5] == null ? '–' : fmtKm(t[5])}</td></tr>`).join('')}</tbody></table>` : '<p class="rp-p">No tehsil figures.</p>')}
      ${R.ucs?.length ? section('ucs', 'Land lost by union council', `<p class="rp-p">Measured inside each union council's boundary, so unlike the village figures no nearest-place step is needed. ${fmtKm(T.ucs_lost)} union councils of ${esc(R.name)} lost an acre or more since the 1980s (now under water all year); the rows add up to the district's ${acres(T.acres)} acres. Ground flooded only part of the year is shown beside it, not counted as lost. Union council map: PCO and Alhasan, about 2017; Punjab, Sindh, Khyber Pakhtunkhwa and Balochistan.</p>
          <table class="rp-tbl rp-full"><thead><tr><th>Union council</th><th class="rp-teh">Tehsil</th><th>Acres<br>lost</th><th>Acres flooded part<br>of the year (not lost)</th><th>Buildings on<br>land lost</th><th>Villages in it<br>that lost land</th></tr></thead>
          <tbody>${R.ucs.map((u) => `<tr><td class="rp-name">${esc(u[0])}</td><td class="rp-teh rp-dim">${esc(u[1])}</td><td>${acres(u[2])}</td><td>${acres(u[3])}</td><td>${fmtKm(u[4])}</td><td>${u[5] == null ? '–' : fmtKm(u[5])}</td></tr>`).join('')}</tbody></table>`) : ''}
      ${section('villages', 'Villages at risk', `<p class="rp-p">${nShown ? `The ${fmtKm(nShown)} villages of ${esc(R.name)} near the river with moderate, high or very high risk, most at risk first.` : 'No village here has moderate or higher risk.'}
          Lost: ground of the village that was dry in the 1980s and is now under water all year. Flooded part of the year: ground that now floods in some months; not counted as lost. Cut: ground the river cut into in ${recentYears()}. Numbers 1–15 match the map. Click a row to see the village on the map.</p>
          <div class="rp-villages">${villageTable(R, false)}</div>
          ${nAll > nShown ? `<button class="fp-btn rp-more">Show all ${fmtKm(nAll)} villages that lost land</button>` : ''}`)}
      ${section('path', 'Places in the river\'s path', `<p class="rp-p">${fmtKm(T.in_path)} settlements, schools, health facilities and police, rescue or railway stations in ${esc(R.name)} stand on land lost to the river since the 1980s, on ground that now floods part of the year, or in its wet belt (within 30 m). Settlements are mapped named places. ${M.facilities === 'dsdi'
        ? 'Schools, health facilities and stations are NDMA DSDI registers (Schools_QC, Medical_Facilities_QC, Police_Stations_QC, Rescue_Stations_QC, Railway_Stations_QC). One standing on ground now under water all year may be lost or its location wrong: check it on the ground.'
        : 'Schools and health facilities come from OpenStreetMap, which misses many.'} Click a row to see it on the map.</p>${pathTable(R)}`)}
      ${M.embankments ? section('embankments', 'Embankments the river has reached', embankmentTable(R)) : ''}
      ${section('missing', 'Not in this report yet', `<p class="rp-p"><b>Projected erosion by 2035</b> needs a bank-erosion forecast, which this portal does not have yet. It can be added once one is available.</p>`, false)}
      ${section('method', 'How these figures are made', `<p class="rp-small">Water: JRC Global Surface Water ${esc(M.period)}, Landsat, 30 m. Land counts as lost only when it was dry in the 1980s, is under water all year in 2024 (JRC "new permanent water"), and the Occurrence Change layer shows a lasting rise of 25 points or more (green there), so one-off floods do not count. Ground that now floods only part of the year (JRC "new seasonal water") is reported beside it and is not counted as lost. Villages are named places from GeoNames and OpenStreetMap; with no official mouza boundaries, each village's ground is the land within ${M.place_km} km closer to it than to any other named place, so village areas and shares are approximate. ${M.union_councils ? 'Union councils: census (PCO) and Alhasan Systems boundaries of about 2017, not an official NDMA set; they cover Punjab, Sindh, Khyber Pakhtunkhwa and Balochistan. ' : ''}Buildings: ${M.buildings === 'osm' ? 'OpenStreetMap footprints only, a minimum' : 'NDMA building server (Google Open Buildings and OpenStreetMap)'}; a building on land the river took is exposed, not necessarily destroyed. ${M.facilities === 'dsdi' ? 'Schools and health facilities: NDMA DSDI registers. ' : ''}${M.embankments ? 'Embankments: NDMA DSDI protection bands, measured in 15 m steps against the 30 m water record. ' : ''}${M.dsdi_districts ? 'Districts: the project boundary files; the district check uses NDMA DSDI\'s updated district map. ' : ''}Year-by-year cuts: JRC Monthly Water History ${M.years[0] - 1}–${M.provisional_from}, cells of about 275 m. Built ${esc(M.built)}.</p>`, false)}`;

    const body = $('#dr-body');
    body.scrollTop = 0;
    const bindRows = () => body.querySelectorAll('.rp-villages tbody tr').forEach((tr) => tr.addEventListener('click', () => {
      const i = +tr.dataset.i;
      const v = village(R.villages[i]);
      flyToPlace(`${i + 1} ${v.name}`, v.lng, v.lat);
    }));
    bindRows();
    body.querySelector('.rp-more')?.addEventListener('click', (e) => {
      body.querySelector('.rp-villages').innerHTML = villageTable(R, true);
      e.target.remove();
      bindRows();
    });
    body.querySelectorAll('.rp-emb tbody tr').forEach((tr) => tr.addEventListener('click', () => {
      const e = R.embankments[+tr.dataset.i];
      flyToPlace(e[0], e[7], e[8]);
    }));
    body.querySelectorAll('.rp-path tbody tr').forEach((tr) => tr.addEventListener('click', () => {
      const p = placeRow(R.path[+tr.dataset.i]);
      flyToPlace(p.name, p.lng, p.lat);
    }));
    body.querySelectorAll('[data-csv]').forEach((b) => b.addEventListener('click', () => downloadReport(distId, b.dataset.csv)));

    // Hover a year for its figure
    const svg = body.querySelector('.rp-chart svg');
    const tipEl = body.querySelector('.rp-tip');
    svg.addEventListener('mousemove', (e) => {
      const r = e.target.closest('.hit');
      svg.querySelectorAll('path.on').forEach((p) => p.classList.remove('on'));
      if (!r) { tipEl.hidden = true; return; }
      const i = +r.dataset.i;
      svg.querySelectorAll('path')[i]?.classList.add('on');
      const yr = M.years[i];
      tipEl.innerHTML = `<b>${yr}</b>: about ${acres(R.years[i])} acres${yr >= M.provisional_from ? '<br>not yet confirmed by the year after' : ''}`;
      const box = svg.getBoundingClientRect();
      const rb = r.getBoundingClientRect();
      tipEl.style.left = `${rb.left - box.left + rb.width / 2}px`;
      tipEl.hidden = false;
    });
    svg.addEventListener('mouseleave', () => { tipEl.hidden = true; svg.querySelectorAll('path.on').forEach((p) => p.classList.remove('on')); });

    $('#dr').hidden = false;
    $('#dr-close').focus({ preventScroll: true });
  }

  function buildReport() {
    const close = () => { $('#dr').hidden = true; };
    $('#dr-close').addEventListener('click', close);
    $('#dr').addEventListener('click', (e) => { if (e.target.id === 'dr') close(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('#dr').hidden) close(); });
    $('#dr-csv').addEventListener('click', () => {
      if (reportKind === 'river') downloadRiver(riverId, 'all');
      else if (reportId !== null) downloadReport(reportId, 'all');
    });
    $('#dr-print').addEventListener('click', () => {
      document.body.classList.add('dr-print');
      window.print();
    });
    window.addEventListener('afterprint', () => document.body.classList.remove('dr-print'));
  }

  // ------------------------------------------------------------------ river reports (Insights tab)
  // From prepare_river_reports.py: per river, the land it took since the 1980s, district by district. The Indus adds up
  // the district reports (its floodplain); the other rivers are measured within a few km of their mapped line.
  let riversReq = null;
  function ensureRivers() {
    riversReq ||= getJSON('data/river_reports.json')
      .then((r) => { D.riverReports = r; })
      .catch((err) => { riversReq = null; console.error(err); });
    return riversReq;
  }
  let reportKind = 'district';
  let riverId = null;
  const riverTitle = (r) => `${r.name} River`;
  const orDash = (v, f = fmtKm) => (v == null ? '–' : f(v));
  const acresWord = (v) => (Math.round(v) === 1 ? 'acre' : 'acres');

  function buildInsightTabs() {
    $('#ins-tabs').addEventListener('click', (e) => {
      const b = e.target.closest('[data-tab]');
      if (b) showInsightTab(b.dataset.tab);
    });
    $('#indus-sort').addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      $('#indus-sort').querySelectorAll('button').forEach((x) => x.setAttribute('aria-checked', String(x === b)));
      renderIndusDistricts();
    });
  }

  async function showInsightTab(tab) {
    for (const b of $('#ins-tabs').children) b.setAttribute('aria-selected', String(b.dataset.tab === tab));
    $('#ins-water').hidden = tab !== 'water';
    $('#ins-rivers').hidden = tab !== 'rivers';
    if (tab !== 'rivers') return;
    await Promise.all([ensureRivers(), ensureReports()]);
    renderRiverList();
    renderIndusDistricts();
  }

  function listRow(attrs, html, open) {
    const li = el('li', { tabindex: '0', role: 'button', ...attrs }, html);
    li.addEventListener('click', open);
    li.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
    return li;
  }

  function renderRiverList() {
    const ol = $('#river-list');
    const RR = D.riverReports;
    if (!RR) { ol.innerHTML = '<li class="hint">The river reports could not be loaded.</li>'; return; }
    const shown = RR.rivers.filter((r) => r.totals.acres + r.totals.acres_part > 0)
      .sort((a, b) => b.totals.acres - a.totals.acres || b.totals.acres_part - a.totals.acres_part);
    const none = RR.rivers.filter((r) => !shown.includes(r));
    const max = Math.max(...shown.map((r) => r.totals.acres + r.totals.acres_part), 1);
    ol.innerHTML = '';
    for (const r of shown) {
      const T = r.totals;
      const nd = r.districts.length;
      ol.append(listRow({ title: `Open the ${riverTitle(r)} report` }, `
        <span class="rr-n">${esc(r.name)}</span><span class="rr-v"><b>${acres(T.acres)}</b> ${acresWord(T.acres)}</span>
        <span class="rr-bar"><i class="lost" style="width:${(100 * T.acres / max).toFixed(2)}%"></i><i class="part" style="width:${(100 * T.acres_part / max).toFixed(2)}%"></i></span>
        <span class="rr-sub">${nd} district${nd === 1 ? '' : 's'} · ${acres(T.acres_part)} more acres flood part of the year</span>`, () => openRiverReport(r.id)));
    }
    $('#river-note').innerHTML = `Indus: its floodplain, as in the district reports. Other rivers: ground within ${RR.meta.corridor_km} km of the river's line on the map, leaving out reservoirs and canals.${none.length ? ` No land lost or newly wet along the ${none.map((r) => esc(r.name)).join(', ')}.` : ''}`;
  }

  function renderIndusDistricts() {
    const ol = $('#indus-districts');
    if (!D.reports) { ol.innerHTML = '<li class="hint">The district reports could not be loaded.</li>'; return; }
    const by = $('#indus-sort [aria-checked="true"]').dataset.v;
    const rows = Object.entries(D.reports.districts).map(([id, R]) => ({ id: +id, R }));
    rows.sort(by === 'lost' ? (a, b) => b.R.totals.acres - a.R.totals.acres : (a, b) => a.R.km[0] - b.R.km[0]);
    const max = Math.max(...rows.map((x) => x.R.totals.acres), 1);
    ol.innerHTML = '';
    rows.forEach(({ id, R }, i) => {
      const high = R.totals.villages_high;
      ol.append(listRow({ title: `Open the ${R.name} district report` }, `
        <span class="rr-i">${i + 1}</span><span class="rr-n">${esc(R.name)}</span><span class="rr-v"><b>${acres(R.totals.acres)}</b> ${acresWord(R.totals.acres)}</span>
        <span class="rr-bar"><i class="lost" style="width:${(100 * R.totals.acres / max).toFixed(2)}%"></i></span>
        <span class="rr-sub">${esc(R.province)} · river km ${fmtKm(R.km[0])}–${fmtKm(R.km[1])}${high ? ` · <em>${fmtKm(high)} village${high === 1 ? '' : 's'} at high risk</em>` : ''}</span>`, () => openDistrictReport(id)));
    });
  }

  // From a list: the map goes to the district behind the report window
  function openDistrictReport(id) {
    const f = D.distFeat.get(id);
    if (f) {
      const [w, s, e, n] = f.properties.bbox;
      map.fitBounds([[w, s], [e, n]], { padding: panelPadding(), pitch: state.terrain ? 55 : 28, bearing: 0, maxZoom: 10.5, duration: reducedMotion ? 0 : 1600, essential: true });
      map.getSource('unit-outline')?.setData({ type: 'FeatureCollection', features: [f] });
    }
    if (D.reports?.districts[id]) {
      reportPins(D.reports.districts[id]);
      openReport(id);
    }
  }

  function riverTiles(r) {
    const T = r.totals;
    const t = (v, label, hot) => `<div class="rp-tile${hot ? ' hot' : ''}"><b>${v}</b><span>${label}</span></div>`;
    return `<div class="rp-tiles full">
      ${t(acres(T.acres), 'acres of land lost: now under water all year', true)}
      ${t(acres(T.acres_part), 'more acres now flood part of the year (not counted as lost)')}
      ${t(acres(T.acres_back), 'acres given back: under water all year in the 1980s, dry now')}
      ${t(fmtKm(T.villages_lost), 'villages lost land', true)}
      ${t(orDash(T.buildings), T.buildings == null ? 'buildings on land lost: not counted yet' : 'buildings on land lost')}
      ${t(fmtKm(T.path_schools + T.path_health + (T.path_stations || 0)), 'schools, health facilities and police, rescue or railway stations on land lost or newly flooded')}
      ${r.method === 'floodplain' && T.emb_n ? t(fmtKm(T.emb_n), `embankment stretches (about ${T.emb_km} km) on land lost`) : ''}
      ${T.acres_reservoir ? t(acres(T.acres_reservoir), 'acres under reservoirs (not counted as lost)') : ''}
    </div>`;
  }

  function riverShort(r) {
    const RR = D.riverReports;
    const T = r.totals;
    const top = [...r.districts].sort((a, b) => b[3] - a[3]).filter((d) => d[3] > 0).slice(0, 3);
    const items = [
      `${r.method === 'floodplain' ? `The Indus runs about ${fmtKm(r.km)} km through ${r.districts.length} districts.` : `The ${esc(riverTitle(r))} flows through ${r.districts.length} district${r.districts.length === 1 ? '' : 's'} on the map${r.traced ? ' (its lower course has no name in the waterways map, so it was traced along the water to the Chenab)' : ''}.`}`,
      `About <b>${acres(T.acres)} acres</b> of land have been lost to the river since the 1980s: dry land then, under water all year now. Another ${acres(T.acres_part)} acres now flood part of the year; they are not counted as lost. The river gave back about ${acres(T.acres_back)} acres that were under water all year in the 1980s.`,
      top.length ? `Most land lost: ${top.map((d) => `${esc(d[1])} (${acres(d[3])} acres)`).join(', ')}.` : '',
      `<b>${fmtKm(T.villages_lost)}</b> named villages and towns near the river lost land.${r.villages[0] ? ` Worst hit: ${r.villages.slice(0, 3).map((v) => esc(v[0])).join(', ')}.` : ''}`,
      T.buildings == null ? 'Buildings on this ground are not counted yet: they need the NDMA building server, which could not be reached when this report was made.'
        : `${fmtKm(T.buildings)} buildings stand on land lost and ${fmtKm(T.buildings_part)} more on ground flooded part of the year. Exposed, not necessarily destroyed.`,
      T.path_schools + T.path_health + (T.path_stations || 0) ? `${fmtKm(T.path_schools)} school${T.path_schools === 1 ? '' : 's'}${T.path_stations ? ', ' : ' and '}${fmtKm(T.path_health)} health facilit${T.path_health === 1 ? 'y' : 'ies'}${T.path_stations ? ` and ${fmtKm(T.path_stations)} police, rescue or railway station${T.path_stations === 1 ? '' : 's'}` : ''} stand on land lost or newly flooded (NDMA DSDI registers).` : '',
      T.acres_reservoir ? `${acres(T.acres_reservoir)} acres went under reservoirs; that is shown on its own, not counted as land lost to the river.` : '',
      r.method === 'floodplain' ? 'Each district below has its own full report: click a row.' : `Measured on ground within ${RR.meta.corridor_km} km of the river.`,
    ].filter(Boolean);
    return `<ul class="rp-short">${items.map((x) => `<li>${x}</li>`).join('')}</ul>`;
  }

  function riverDistrictTable(r) {
    const indus = r.method === 'floodplain';
    const bld = r.totals.buildings != null;
    return `<table class="rp-tbl rp-full rr-dist"><thead><tr><th>#</th><th>District</th><th class="rp-teh">Province</th>${indus ? '<th>River km</th>' : ''}<th>Acres<br>lost</th><th>Acres flooded part<br>of the year (not lost)</th><th>Acres<br>given back</th>${bld ? '<th>Buildings on<br>land lost</th>' : ''}<th></th></tr></thead>
      <tbody>${r.districts.map((d, i) => {
        const report = !!D.reports?.districts[d[0]];
        return `<tr data-i="${i}" title="${report ? `Open the ${esc(d[1])} district report` : `Show ${esc(d[1])} on the map`}"><td>${i + 1}</td><td class="rp-name">${esc(d[1])}</td><td class="rp-teh rp-dim">${esc(d[2])}</td>${indus ? `<td class="rp-dim">${fmtKm(d[8][0])}–${fmtKm(d[8][1])}</td>` : ''}
          <td>${acres(d[3])}</td><td>${acres(d[4])}</td><td>${acres(d[5])}</td>${bld ? `<td>${orDash(d[6])}</td>` : ''}<td class="rr-go">${report ? 'Report →' : 'Map →'}</td></tr>`;
      }).join('')}</tbody></table>`;
  }

  function riverVillageTable(r) {
    if (!r.villages.length) return '<p class="rp-p">No named village near the river lost land or newly floods.</p>';
    return `<table class="rp-tbl rp-full rr-vill"><thead><tr><th>#</th><th>Village or town</th><th>District</th><th>Acres<br>lost</th><th>Acres flooded part<br>of the year (not lost)</th></tr></thead>
      <tbody>${r.villages.map((v, i) => `<tr data-i="${i}" title="Show ${esc(v[0])} on the map"><td>${i + 1}</td><td class="rp-name">${esc(v[0])}</td><td class="rp-dim">${esc(v[1])}</td><td>${acres(v[3])}</td><td>${acres(v[4])}</td></tr>`).join('')}</tbody></table>`;
  }

  function riverPathTable(r) {
    if (!r.path.length) return '<p class="rp-p">No school, health facility or police, rescue or railway station in NDMA DSDI\'s registers stands on land lost or newly flooded along this river.</p>';
    return `<p class="rp-p">From NDMA DSDI's registers (Schools_QC, Medical_Facilities_QC, Police_Stations_QC, Rescue_Stations_QC, Railway_Stations_QC). One standing on ground now under water all year may be lost, or its recorded location may be wrong: check it on the ground. Click a row to see it on the map.</p>
      <table class="rp-tbl rp-full rr-path"><thead><tr><th>Name</th><th>Kind</th><th>District</th><th>Where it stands</th></tr></thead>
      <tbody>${r.path.map((p, i) => `<tr data-i="${i}" class="w${p[3]}"><td class="rp-name">${esc(p[0])}</td><td>${esc(p[1])}</td><td class="rp-dim">${esc(p[2])}</td><td>${p[3] === 1 ? 'On land lost: now under water all year' : 'On ground now flooded part of the year'}</td></tr>`).join('')}</tbody></table>`;
  }

  const RIVER_TABLES = {
    summary: { title: 'Summary', head: () => ['Figure', 'Value'], rows: (r) => Object.entries(r.totals).filter(([, v]) => v != null).map(([k, v]) => [k, v]) },
    districts: {
      title: 'By district',
      head: () => ['District', 'Province', 'Acres lost (under water all year now)', 'Acres flooded part of the year (not lost)', 'Acres given back', 'Buildings on land lost', 'Buildings on ground flooded part of the year'],
      rows: (r) => r.districts.map((d) => [d[1], d[2], d[3], d[4], d[5], d[6] ?? '', d[7] ?? '']),
    },
    villages: {
      title: 'Most affected villages',
      head: () => ['Village or town', 'District', 'Kind', 'Acres lost', 'Acres flooded part of the year (not lost)', 'Longitude', 'Latitude'],
      rows: (r) => r.villages.map((v) => [v[0], v[1], v[2], v[3], v[4], v[5], v[6]]),
    },
    path: {
      title: 'Schools, health facilities and stations on land lost or newly flooded',
      head: () => ['Name', 'Kind', 'District', 'Where it stands', 'Longitude', 'Latitude'],
      rows: (r) => r.path.map((p) => [p[0], p[1], p[2], p[3] === 1 ? 'land lost' : 'flooded part of the year', p[4], p[5]]),
    },
  };

  function downloadRiver(id, which) {
    const r = D.riverReports?.rivers.find((x) => x.id === id);
    if (!r) return;
    const base = `${r.id}_river`;
    if (which !== 'all') {
      const T = RIVER_TABLES[which];
      saveCSV(`${base}_${which}.csv`, [T.head(), ...T.rows(r)]);
      return;
    }
    const lines = [[`${riverTitle(r)}. Pakistan Surface Water Observatory (NDMA), built ${D.riverReports.meta.built} from JRC Global Surface Water ${D.riverReports.meta.period}.`], []];
    for (const T of Object.values(RIVER_TABLES)) lines.push([T.title.toUpperCase()], T.head(), ...T.rows(r), []);
    saveCSV(`${base}_report.csv`, lines);
  }

  function openRiverReport(id) {
    const RR = D.riverReports;
    const r = RR?.rivers.find((x) => x.id === id);
    if (!r) return;
    reportKind = 'river';
    riverId = id;
    const M = RR.meta;
    const indus = r.method === 'floodplain';
    $('#dr-eyebrow').textContent = 'River report · change since the 1980s';
    $('#dr-title').textContent = riverTitle(r);
    $('#dr-sub').innerHTML = `Land lost to the river and ground newly flooded, ${esc(M.period)} · ${indus ? 'inside the Indus floodplain, as in the district reports' : `ground within ${M.corridor_km} km of the river`} · ${r.districts.length} districts`;
    const csv = (which) => `<button class="fp-btn" data-rcsv="${which}">CSV</button>`;
    const sec = (key, title, body, button = true) => `<section class="rp-sec"><div class="rp-sec-head"><h3>${title}</h3>${button ? csv(key) : ''}</div>${body}</section>`;
    $('#dr-body').innerHTML = `
      ${riverTiles(r)}
      ${sec('summary', 'In short', `${riverShort(r)}<button class="fp-btn rr-show">Show the river on the map</button>`)}
      ${sec('districts', indus ? 'District by district, down the river' : 'District by district, down the river', `<p class="rp-p">${indus ? 'Click a district for its full report.' : 'Click a district to see it on the map; districts on the Indus also have a full report.'} Land lost: dry in the 1980s, under water all year now (a lasting rise in the Occurrence Change layer). Flooded part of the year: shown beside it, not counted as lost.</p>${riverDistrictTable(r)}`)}
      ${sec('villages', `The ${r.villages.length} most affected villages and towns`, `<p class="rp-p">Named places from GeoNames and OpenStreetMap; each pixel of land lost goes to the nearest one within ${M.place_km} km, so the figures are approximate (there are no official mouza boundaries). Click a row to see the place on the map.</p>${riverVillageTable(r)}`)}
      ${indus ? '' : sec('path', 'Schools, health facilities and stations in its path', riverPathTable(r))}
      ${sec('method', 'How these figures are made', `<p class="rp-small">Water: JRC Global Surface Water ${esc(M.period)}, Landsat, 30 m. Land counts as lost only when it was dry in the 1980s, is under water all year in 2024 (JRC "new permanent water") and the Occurrence Change layer shows a lasting rise of 25 points or more, so one-off floods do not count; the same rule as the district reports. ${indus ? 'The Indus figures add up the district reports, measured inside the Indus floodplain.'
        : `Other rivers are measured on ground within ${M.corridor_km} km of the river's line in the waterways map (the reach of the map's "River system" water source), outside the Indus floodplain; where two rivers' reaches meet, each pixel goes to the nearer river. Left out: ground within ${M.canal_m} m of a named canal, and reservoirs (OpenStreetMap lake and reservoir outlines), whose land is shown on its own.`} ${M.buildings === 'ndma' ? 'Buildings: NDMA building server (Google Open Buildings and OpenStreetMap); a building on land the river took is exposed, not necessarily destroyed.' : indus ? 'Buildings: NDMA building server, as in the district reports.' : 'Buildings: not counted yet for this river; they need the NDMA building server.'} On the map, the Occurrence Change layer shows this ground in green. Built ${esc(M.built)}.</p>`, false)}`;

    const body = $('#dr-body');
    body.scrollTop = 0;
    const go = (lng, lat, name) => flyToPlace(name, lng, lat, indus ? 'erosion' : 'change');
    body.querySelectorAll('.rr-dist tbody tr').forEach((tr) => tr.addEventListener('click', () => {
      const d = r.districts[+tr.dataset.i];
      if (D.reports?.districts[d[0]]) return openDistrictReport(d[0]);
      $('#dr').hidden = true;
      openDistrictReport(d[0]);
    }));
    body.querySelectorAll('.rr-vill tbody tr').forEach((tr) => tr.addEventListener('click', () => {
      const v = r.villages[+tr.dataset.i];
      go(v[5], v[6], v[0]);
    }));
    body.querySelectorAll('.rr-path tbody tr').forEach((tr) => tr.addEventListener('click', () => {
      const p = r.path[+tr.dataset.i];
      go(p[4], p[5], p[0]);
    }));
    body.querySelector('.rr-show').addEventListener('click', () => {
      $('#dr').hidden = true;
      if (!indus && state.layer !== 'change') setLayer('change');
      if (D.rivers.has(r.map_name)) focusRiver(r.map_name);
      else if (r.bbox) map.fitBounds([[r.bbox[0], r.bbox[1]], [r.bbox[2], r.bbox[3]]], { padding: panelPadding(), duration: 2000 });
    });
    body.querySelectorAll('[data-rcsv]').forEach((b) => b.addEventListener('click', () => downloadRiver(id, b.dataset.rcsv)));
    $('#dr').hidden = false;
    $('#dr-close').focus({ preventScroll: true });
  }

  let popup = null;

  // Opens towards the middle of the screen, so a tall popup stays clear of the header and side panels
  function popupAnchor(lngLat) {
    const { x, y } = map.project(lngLat);
    const c = map.getCanvas().getBoundingClientRect();
    const v = y < c.height * 0.38 ? 'top-' : y > c.height * 0.66 ? 'bottom-' : '';
    return v + (x < c.width / 2 ? 'left' : 'right');
  }

  // A dot: what stands on its eroded or newly wet ground, and the named places around it, pinned on the map
  function showDot(P, lngLat) {
    tip.hidden = true;
    const area = (a) => (a === 0 ? '0' : a < 0.05 ? '<0.1' : a < 100 ? a.toFixed(1) : fmtKm(a));
    const n = (a) => fmtKm(a);
    const row = (label, v) => `<tr><td>${label}</td><td>${v}</td></tr>`;
    const others = P.bld_named - P.places.reduce((a, p) => a + p[3], 0);
    const noPlace = Math.round(P.bld) - P.bld_named;
    const places = P.places.length ? `<div class="fx-sub">Named places nearest those buildings</div>
      <table class="fx-table fx-dot">${P.places.map(([name, , , c]) => row(esc(name), n(c))).join('')}
      ${others > 0 ? row(`${n(P.n_places - P.places.length)} more named places`, n(others)) : ''}
      ${noPlace > 0 ? row('No named place within 3 km', n(noPlace)) : ''}</table>` : '';
    const node = el('div', {}, `
      <h4>${P.z <= 6 || !P.places.length ? esc(P.name) : `Around ${esc(P.name)}`}</h4>
      <div class="where">${P.districts.map(esc).join(', ')}${P.districts.length ? ` district${P.districts.length > 1 ? 's' : ''} · ` : ''}Indus floodplain</div>
      <div class="fx">
        <div class="fx-title">Buildings and land now on wet ground</div>
        <p class="fx-sentence">Since the 1980s about <b>${area(P.km2)} km²</b> of land here has turned to water: ${area(P.perm)} km² is now wet all year and ${area(P.km2 - P.perm)} km² for part of the year. These buildings stand on it today.</p>
        <table class="fx-table fx-dot">
          ${row('Buildings', n(P.bld))}
          ${P.bld_perm != null ? `<tr class="fx-split"><td>on ground now wet all year</td><td>${n(P.bld_perm)}</td></tr><tr class="fx-split"><td>on ground flooded part of the year</td><td>${n(Math.max(0, Math.round(P.bld) - P.bld_perm))}</td></tr>` : ''}
          ${row('Cropland, km²', area(P.crop))}${row('Built-up, km²', area(P.built))}
          ${row('Main roads, km', area(P.major))}${row('Other roads, km', area(P.minor))}${row('Railway, km', area(P.rail))}
          ${row('Schools', n(P.edu))}${row('Health facilities', n(P.health))}${row('Bridges', n(P.bridge))}
        </table>
        ${places}
        <div class="fx-note">Buildings are counted, not lost. Those on ground now wet all year sit in or at the edge of the river channel: likely lost since they were mapped, or at immediate risk. Those on ground flooded part of the year are exposed to floods and high water. A building is listed under the nearest named place, but may not belong to it. ${D.erosionDots?.meta?.schools === 'dsdi' ? 'Schools and health facilities: NDMA DSDI registers. Roads and bridges come from OpenStreetMap and are minimums.' : 'Roads, schools, health facilities and bridges come from OpenStreetMap and are minimums.'}</div>
      </div>`);
    const go = el('button', { class: 'go' }, 'Zoom to this ground →');
    go.addEventListener('click', () => {
      setLayer('erosion');
      const [w, s, e, nn] = P.bbox;
      map.once('moveend', () => { if (node.isConnected) showDot(P, lngLat); });
      map.fitBounds([[w, s], [e, nn]], { padding: 90, maxZoom: 12.5, duration: reducedMotion ? 0 : 1400 });
    });
    node.append(go);
    popup?.remove();
    popup = movable(new mapboxgl.Popup({ className: 'probe', maxWidth: '340px', offset: 12, focusAfterOpen: false, anchor: popupAnchor(lngLat) }).setLngLat(lngLat).setDOMContent(node).addTo(map));
    popup.on('close', () => map.getSource('dot-pins')?.setData(EMPTY));
    map.getSource('dot-pins')?.setData({ type: 'FeatureCollection', features: P.places.map(([name, lng, lat, c]) => (
      { type: 'Feature', geometry: { type: 'Point', coordinates: [lng, lat] }, properties: { label: `${name} · ${n(c)}` } }
    )) });
  }

  async function probe(e) {
    if (dotsOn()) {
      const { x, y } = e.point;
      const hit = map.queryRenderedFeatures([[x - 4, y - 4], [x + 4, y + 4]], { layers: ['erosion-dot'] })[0];
      if (hit) return showDot(D.dotInfo.get(hit.properties.id), hit.geometry.coordinates);
    }
    if (tour.on && map.getLayer('fp-transects')) {
      const { x, y } = e.point;
      const line = map.queryRenderedFeatures([[x - 6, y - 6], [x + 6, y + 6]], { layers: ['fp-transects'] })[0];
      if (line) { tour.km = line.properties.km; updateTourHud(); }
    }
    const { lng, lat } = e.lngLat;
    const hit = map.getLayer('districts-fill') && map.queryRenderedFeatures(e.point, { layers: ['districts-fill'] })[0];
    const dist = hit ? D.distStats.get(hit.id) : null;
    const disputed = dist ? null : D.disputed.features.find((f) => inFeature(f, lng, lat));
    const ww = waterwayAt(e.point);
    const bld = buildingAt(e.point);
    const bh = bld && Number(bld.properties.height);
    const node = el('div', {}, `
      <h4>${dist ? `${esc(dist.name)} district` : disputed ? esc(disputed.properties.name) : 'Outside Pakistan'}</h4>
      <div class="where">${dist ? `${esc(dist.province)} · ` : disputed ? 'Disputed territory · ' : ''}${lat.toFixed(4)}° N  ${lng.toFixed(4)}° E</div>
      ${ww ? `<div class="ww">${waterwayLine(ww)}</div>` : ''}
      ${bld ? `<div class="bld"><span class="bld-swatch"></span>Building${bh >= 1 ? ` · ~${bh.toFixed(1)} m tall` : ''}
        <span class="kind">${esc(BLD_SOURCES[bld.properties.source] || bld.properties.source || '')}</span></div>` : ''}
      <div class="loading"><span class="pulse"></span>Reading this area…</div>`);
    if (dsdi.on.length) {
      const box = el('div', { class: 'fx dsdi-info' });
      node.querySelector('.loading').before(box);
      dsdiInfo(box, e.lngLat);
    }
    popup?.remove();
    popup = movable(new mapboxgl.Popup({ className: `probe${dist ? ' wide' : ''}`, maxWidth: dist ? '600px' : '390px', offset: 12, focusAfterOpen: false, anchor: popupAnchor(e.lngLat) })
      .setLngLat(e.lngLat).setDOMContent(node).addTo(map));
    popup.on('close', () => { map.getSource('unit-outline')?.setData(EMPTY); map.getSource('dot-pins')?.setData(EMPTY); });

    const [{ values, snapped }] = await Promise.all([sampleAll(lng, lat), dist ? ensurePlaces().catch(() => null) : null, dist ? ensureUnits() : null, dist ? ensureReports() : null, dist ? ensureUcs() : null]);
    if (!node.isConnected) return;
    node.querySelector(':scope > .loading').remove();
    const near = dist && nearestPlace(lng, lat);
    if (near) {
      const d = near.m < 1000 ? `${Math.round(near.m / 10) * 10} m` : `${(near.m / 1000).toFixed(1)} km`;
      node.querySelector('.where').insertAdjacentHTML('afterend',
        `<div class="near">Near <b>${esc(near.place.name)}</b> <span>${esc(PLACE_KINDS[near.place.kind].toLowerCase())} · ${d}</span></div>`);
    }
    // The district's river report; the tehsil clicked in is named on the line below the title
    const unit = dist && D.units ? unitAt(lng, lat) : null;
    const uc = dist && D.ucs ? unitAt(lng, lat, D.ucs) : null;
    if (unit || uc) {
      const where = [uc && `${uc.properties.name} union council`, unit && `${unit.properties.name} ${D.units.level.toLowerCase()}`, dist.province].filter(Boolean);
      node.querySelector('.where').textContent = `${where.join(' · ')} · ${lat.toFixed(4)}° N  ${lng.toFixed(4)}° E`;
    }
    const report = dist ? D.reports?.districts[dist.id] : null;
    if (dist) map.getSource('unit-outline')?.setData({ type: 'FeatureCollection', features: [D.distFeat.get(dist.id)].filter(Boolean) });
    if (report) {
      node.append(districtCard(report, dist.id));
      reportPins(report);
      requestAnimationFrame(() => keepPopupInView(popup));
    } else if (dist && D.reports) {
      node.insertAdjacentHTML('beforeend', `<div class="fx"><div class="fx-none">The Indus floodplain does not reach ${esc(dist.name)}, so it has no river-change report. Reports cover the ${Object.keys(D.reports.districts).length} districts along the Indus.</div></div>`);
    }

    const pixel = el('details', { class: 'fx-pixel' }, `<summary>Water values at this spot</summary>`);
    if (snapped) pixel.insertAdjacentHTML('beforeend', `<div class="snap">Nearest water pixel · ${Math.round(snapped / 10) * 10} m away</div>`);
    if (values.some(Boolean)) {
      const rows = PIXEL_LAYERS.map((L, i) => {
        const v = values[i];
        const cell = v ? `<span class="swatch" style="background:${rgb(v.color)}"></span>${esc(describe(L.id, v.value))}` : '<span class="none">—</span>';
        return `<tr><td>${esc(L.short)}</td><td>${cell}</td></tr>`;
      });
      pixel.insertAdjacentHTML('beforeend', `<table>${rows.join('')}</table>`);
    } else {
      pixel.insertAdjacentHTML('beforeend', `<div class="loading">${dist || disputed ? 'No surface water recorded here, 1984–2024.' : 'Data covers Pakistan only.'}</div>`);
    }
    if (!report) pixel.open = true; // nothing else to show first
    node.append(pixel);
    if (dist) {
      const go = el('button', { class: 'go' }, `${esc(dist.name)} district insights →`);
      go.addEventListener('click', () => { popup.remove(); setScope({ level: 'district', id: dist.id }); });
      node.append(go);
    }
  }
  map.on('click', probe);

  // ------------------------------------------------------------------ hover + status bar
  let hoverId = null;
  const tip = $('#hover-tip');
  const fmtDotKm2 = (v) => (v < 0.1 ? '<0.1' : v.toLocaleString('en-US', { maximumFractionDigits: v < 10 ? 1 : 0 }));

  function setHover(id) {
    if (id === hoverId) return;
    if (hoverId !== null && map.getSource('districts')) map.setFeatureState({ source: 'districts', id: hoverId }, { hover: false });
    hoverId = id;
    if (id !== null) map.setFeatureState({ source: 'districts', id }, { hover: true });
  }

  map.on('mousemove', (e) => {
    $('#coords').innerHTML = `${Math.abs(e.lngLat.lat).toFixed(4)}° ${e.lngLat.lat >= 0 ? 'N' : 'S'} &nbsp; ${Math.abs(e.lngLat.lng).toFixed(4)}° ${e.lngLat.lng >= 0 ? 'E' : 'W'}`;
    if (!map.getLayer('districts-fill') || !D.distStats) return;
    const f = map.queryRenderedFeatures(e.point, { layers: ['districts-fill'] })[0];
    setHover(f ? f.id : null);
    const dot = dotsOn() ? map.queryRenderedFeatures(e.point, { layers: ['erosion-dot'] })[0] : null;
    if (dot && !isMobile()) {
      const { name, sub, km2 } = dot.properties;
      tip.innerHTML = `<div class="t">${esc(name)}</div><div class="s">${esc(sub)} on ${fmtDotKm2(km2)} km² eroded or newly wet · click for the villages</div>`;
      tip.style.left = `${e.originalEvent.clientX}px`;
      tip.style.top = `${e.originalEvent.clientY}px`;
      tip.hidden = false;
      return;
    }
    const w = waterwayAt(e.point);
    const named = w && w.properties.name ? w : null;
    if ((f || named) && !isMobile()) {
      const s = f && D.distStats.get(f.id);
      tip.innerHTML = named
        ? `<div class="t">${esc(named.properties.name)}</div><div class="s">${esc(WATERWAYS[named.properties.kind].label)}${s ? ` · ${esc(s.name)}, ${esc(s.province)}` : ''}</div>`
        : `<div class="t">${esc(s.name)}</div><div class="s">${esc(s.province)} · ${fmtArea(s.extent_km2)} km² max water · ${fmtPct(pct(s.extent_km2, s.area_km2))}</div>`;
      tip.style.left = `${e.originalEvent.clientX}px`;
      tip.style.top = `${e.originalEvent.clientY}px`;
      tip.hidden = false;
    } else {
      tip.hidden = true;
    }
  });
  map.getCanvas().addEventListener('mouseleave', () => { tip.hidden = true; setHover(null); });

  function niceNum(v) {
    const p = 10 ** Math.floor(Math.log10(v));
    const d = v / p;
    return (d >= 5 ? 5 : d >= 2 ? 2 : 1) * p;
  }

  function updateView() {
    $('#zoom-level').textContent = `z ${map.getZoom().toFixed(1)}`;
    $('#compass svg').style.transform = `rotate(${-map.getBearing()}deg)`;
    const mpp = (40075016.686 * Math.cos((map.getCenter().lat * Math.PI) / 180)) / (512 * 2 ** map.getZoom());
    const nice = niceNum(mpp * 90);
    $('#scale-bar').style.width = `${nice / mpp}px`;
    $('#scale-label').textContent = nice >= 1000 ? `${(nice / 1000).toLocaleString('en-US')} km` : `${nice} m`;
  }
  map.on('move', updateView);

  // ------------------------------------------------------------------ Indus 3D flythrough
  // One click: satellite imagery, 3D terrain and the Indus highlighted, with a camera that
  // follows the river from its upstream end to the sea (route from backend/pipeline/prepare_indus.py).
  // Landmarks get a 360° orbit. Touching the map hands control back to the user.
  const TOUR_KMPS = 2.5; // river km per second at 1x when zoom is 11; scales with the view
  const ORBIT_S = 30;    // seconds per full orbit
  const SPEEDS = [0.5, 1, 2, 4];
  const FLIGHT_PITCH = 60;
  const CAMERA_FOV = 0.6435011087932844; // Mapbox's fixed vertical field of view, radians
  const tour = {
    on: false, playing: false, orbiting: false, km: 0, speed: 1, bearing: 0, turned: 0,
    last: 0, raf: 0, flight: 0, hud: 0, route: null, saved: null, dt: 1 / 60, blend: null,
  };
  let routeReq = null;

  const lerp = (a, b, t) => a + (b - a) * t;
  const angleDiff = (a, b) => ((a - b + 540) % 360) - 180;
  const fmtKm = (v) => Math.round(v).toLocaleString('en-US');

  function routePoint(km) {
    const c = tour.route.coords;
    const f = Math.max(0, Math.min(c.length - 1, km / tour.route.step_km));
    const i = Math.min(Math.floor(f), c.length - 2);
    return [lerp(c[i][0], c[i + 1][0], f - i), lerp(c[i][1], c[i + 1][1], f - i)];
  }

  // The camera flies a track from analyse_floodplain.py, one point per km: the middle of the floodplain,
  // heavily smoothed, with a zoom that frames its width and a height that ignores individual ridges.
  function trackValue(arr, km) {
    const f = Math.max(0, Math.min(arr.length - 1, km));
    const i = Math.min(Math.floor(f), arr.length - 2);
    return lerp(arr[i], arr[i + 1], f - i);
  }

  function camPoint(km) {
    const c = D.fp.camera.coords;
    const f = Math.max(0, Math.min(c.length - 1, km));
    const i = Math.min(Math.floor(f), c.length - 2);
    return [lerp(c[i][0], c[i + 1][0], f - i), lerp(c[i][1], c[i + 1][1], f - i)];
  }

  // Direction of travel, looking ahead so the camera turns into bends early
  function routeHeading(km) {
    const a = Math.max(0, Math.min(km - 8, tour.route.length_km - 30));
    const [x0, y0] = camPoint(a);
    const [x1, y1] = camPoint(a + 30);
    return (Math.atan2((x1 - x0) * Math.cos((y0 * Math.PI) / 180), y1 - y0) * 180) / Math.PI;
  }

  const flightPose = (km, bearing = routeHeading(km)) => ({
    center: camPoint(km), zoom: trackValue(D.fp.camera.zoom, km), alt: trackValue(D.fp.camera.alt, km), pitch: FLIGHT_PITCH, bearing,
  });

  // Where the camera sits for a pose. Setting this directly, rather than a map centre, keeps the camera
  // height off the terrain under the centre, which is what shook the view over rough ground.
  function poseCamera({ center: [lng, lat], zoom, alt, pitch, bearing }) {
    const cosLat = Math.cos((lat * Math.PI) / 180);
    const px = (0.5 * map.getCanvas().clientHeight) / Math.tan(CAMERA_FOV / 2);
    const dist = px * ((40075016.686 * cosLat) / (512 * 2 ** zoom));
    const p = (pitch * Math.PI) / 180, b = (bearing * Math.PI) / 180;
    const back = dist * Math.sin(p);
    const at = mapboxgl.MercatorCoordinate.fromLngLat(
      { lng: lng - (Math.sin(b) * back) / (111320 * cosLat), lat: lat - (Math.cos(b) * back) / 110574 },
      alt * TERRAIN_EXAG + dist * Math.cos(p),
    );
    return { x: at.x, y: at.y, z: at.z, pitch, bearing };
  }

  // Flies from wherever the camera is now into the track, instead of snapping onto it
  function startBlend(seconds = 2) {
    const { position: q } = map.getFreeCameraOptions();
    tour.blend = { x: q.x, y: q.y, z: q.z, pitch: map.getPitch(), bearing: map.getBearing(), t: 0, seconds };
  }

  function setPose(pose) {
    const c = poseCamera(pose);
    const B = tour.blend;
    if (B) {
      const u = Math.min(1, B.t / B.seconds);
      const k = u * u * (3 - 2 * u);
      c.x = lerp(B.x, c.x, k); c.y = lerp(B.y, c.y, k); c.z = lerp(B.z, c.z, k);
      c.pitch = lerp(B.pitch, c.pitch, k);
      c.bearing = B.bearing + angleDiff(c.bearing, B.bearing) * k;
      if (u >= 1) tour.blend = null;
    }
    const cam = map.getFreeCameraOptions();
    cam.position = new mapboxgl.MercatorCoordinate(c.x, c.y, c.z);
    cam.setPitchBearing(c.pitch, c.bearing);
    map.setFreeCameraOptions(cam);
  }

  function tourFrame(t) {
    // Averaging the frame time stops a slow frame turning into a visible lurch forward
    tour.dt += (Math.min(0.1, (t - tour.last) / 1000) - tour.dt) * 0.08;
    const dt = tour.dt;
    tour.last = t;
    if (tour.playing) {
      const zoom = trackValue(D.fp.camera.zoom, tour.km);
      tour.km = Math.min(tour.route.length_km, tour.km + TOUR_KMPS * tour.speed * 2 ** (11 - zoom) * dt);
      tour.bearing += angleDiff(routeHeading(tour.km), tour.bearing) * (1 - Math.exp(-dt * 0.5 * Math.max(1, tour.speed)));
      if (tour.blend) tour.blend.t += dt;
      setPose(flightPose(tour.km, tour.bearing));
      if (tour.km >= tour.route.length_km) startOrbit();
    } else if (tour.orbiting) {
      const step = (360 * dt) / ORBIT_S;
      tour.turned += step;
      map.setBearing(map.getBearing() + step);
      if (tour.turned >= 360) setOrbit(false);
    }
    if (t - tour.hud > 250) { tour.hud = t; updateTourHud(); }
    tour.raf = tour.playing || tour.orbiting ? requestAnimationFrame(tourFrame) : 0;
  }

  function kick() {
    if (tour.raf) return;
    tour.last = performance.now();
    tour.raf = requestAnimationFrame(tourFrame);
  }

  function setPlaying(on) {
    tour.playing = on;
    pressed($('#tour-play'), on);
    $('#tour-play span').textContent = on ? 'Pause' : 'Fly';
    $('#tour').classList.toggle('playing', on);
    applyOverlays();
    if (on) { setOrbit(false); kick(); } else syncBuildings();
  }

  function setOrbit(on) {
    tour.orbiting = on;
    tour.turned = 0;
    pressed($('#tour-orbit'), on);
    if (on) kick();
  }

  function startOrbit() {
    if (tour.playing) setPlaying(false);
    setOrbit(true);
  }

  // Stops everything the tour is doing, including camera moves still waiting to start
  function pauseTour() {
    tour.flight++;
    setPlaying(false);
    setOrbit(false);
  }

  // Run fn after a camera move of `ms`, unless the tour was paused or left in the meantime
  function after(ms, fn) {
    const id = tour.flight;
    setTimeout(() => { if (tour.on && id === tour.flight) fn(); }, ms + 50);
  }

  const floodStart = () => tour.route.stops.find((s) => s.name.startsWith('Kalabagh'))?.km ?? 0;

  function play() {
    pauseTour();
    if (tour.km >= tour.route.length_km - 1) tour.km = floodStart();
    tour.bearing = routeHeading(tour.km);
    startBlend(reducedMotion ? 0.01 : 2.4);
    setPlaying(true);
  }

  function goToStop(i) {
    const s = tour.route.stops[i];
    pauseTour();
    tour.km = s.km;
    const ms = reducedMotion ? 0 : 4000;
    map.flyTo({ center: routePoint(s.km), zoom: s.zoom + 0.5, pitch: s.pitch, bearing: routeHeading(s.km), duration: ms, essential: true });
    if (!reducedMotion) after(ms, startOrbit);
    updateTourHud();
  }

  function stepStop(dir) {
    const S = tour.route.stops;
    const i = dir > 0 ? S.findIndex((s) => s.km > tour.km + 1) : S.findLastIndex((s) => s.km < tour.km - 1);
    if (i >= 0) goToStop(i);
  }

  function updateTourHud() {
    if (!tour.on || !tour.route) return;
    const { km } = tour;
    const S = tour.route.stops;
    const len = tour.route.length_km;
    const near = S.reduce((b, s) => (Math.abs(s.km - km) < Math.abs(b.km - km) ? s : b));
    if (Math.abs(near.km - km) <= 25) {
      $('#tour-place').textContent = near.name;
      $('#tour-note').textContent = near.note;
    } else {
      const prev = S.findLast((s) => s.km < km);
      const next = S.find((s) => s.km > km);
      $('#tour-place').textContent = `${prev ? prev.name : 'Upper Indus'} → ${next ? next.name : 'Arabian Sea'}`;
      $('#tour-note').textContent = next ? `Next landmark in ${fmtKm(next.km - km)} km` : '';
    }
    $('#tour-km').textContent = `${fmtKm(km)} / ${fmtKm(len)} km`;

    const sec = sectionAt(km);
    $('#tour-elev').textContent = sec.ch == null ? '—' : `${fmtKm(Math.max(0, sec.ch))} m a.s.l.`;
    const left = distName(sec.ld), right = distName(sec.rd);
    $('#tour-dist').textContent = left === right ? left : `${left} · ${right}`;
    $('#tour-dist').title = $('#tour-dist').textContent;
    $('#tour-water').textContent = `${((sec.fpl + sec.fpr) / 1000).toFixed(1)} km`;
    const inLand = !!D.land && km >= D.land.meta.km0 && km <= D.land.meta.km1;
    const wantLayer = inLand ? 'landloss' : 'erosion';
    if (state.layer !== wantLayer) setLayer(wantLayer);
    showSection(sec);
    showTable(km, sec);

    const input = $('#tour-progress');
    input.value = Math.round(km);
    input.style.setProperty('--p', `${(km / len) * 100}%`);
    for (const [i, b] of [...$('#tour-ticks').children].entries()) {
      b.classList.toggle('past', S[i].km <= km);
      b.classList.toggle('cur', S[i] === near && Math.abs(near.km - km) <= 25);
    }
  }

  function buildTourTrack() {
    const { stops, length_km: len } = tour.route;
    $('#tour-progress').max = len;
    const ticks = $('#tour-ticks');
    ticks.innerHTML = '';
    stops.forEach((s, i) => {
      const b = el('button', { title: `${s.name} · km ${fmtKm(s.km)}`, 'aria-label': `Fly to ${s.name}`, style: `left:${(s.km / len) * 100}%` });
      b.addEventListener('click', () => goToStop(i));
      ticks.append(b);
    });
  }

  async function enterTour() {
    if (tour.on) return;
    tour.on = true;
    pressed($('#btn-indus'), true);
    popup?.remove();
    state.spin = false;
    pressed($('#btn-spin'), false);
    const panels = ['panel-layers', 'panel-insights'];
    tour.saved = {
      basemap: state.basemap, terrain: state.terrain, river: state.river, layer: state.layer, highlight: state.highlight, opacity: state.opacity, districts: state.overlays.districts,
      camera: { center: map.getCenter(), zoom: map.getZoom(), pitch: map.getPitch(), bearing: map.getBearing() },
      collapsed: panels.filter((id) => $(`#${id}`).classList.contains('collapsed')),
    };
    for (const id of panels) $(`#${id}`).classList.add('collapsed');
    for (const p of document.querySelectorAll('.panel')) p.classList.remove('open');
    for (const b of document.querySelectorAll('.mobile-dock button')) pressed(b, false);
    document.body.classList.add('touring');
    $('#tour').hidden = false;
    $('#tour-place').textContent = 'Loading route…';

    try {
      tour.route ||= await (routeReq ||= getJSON('data/indus_route.json'));
      await loadFloodplain();
    } catch (err) {
      routeReq = null;
      fpReq = null;
      console.error(err);
      $('#tour-place').textContent = 'Could not load the floodplain data: see README.md';
      return;
    }
    if (!tour.on) return;
    buildTourTrack();
    setLayer('erosion');
    state.opacity = 0.72;
    applyOpacity();
    state.overlays.districts = true;
    tour.showSec = tour.showTab = innerWidth > 1240;
    applyFpPanels();

    state.terrain = true;
    pressed($('#btn-3d'), true);
    state.river = tour.route.name;
    if (state.basemap !== 'satellite') {
      const ready = whenStyled();
      setBasemap('satellite');
      await ready;
      if (!tour.on) return;
    } else {
      applyTerrain();
      applySelection();
    }

    applyOverlays();
    tour.km = floodStart();
    tour.bearing = routeHeading(tour.km);
    updateTourHud();
    const ms = reducedMotion ? 0 : 6000;
    const { center, zoom, pitch, bearing } = flightPose(tour.km, tour.bearing);
    map.flyTo({ center, zoom, pitch, bearing, duration: ms, curve: 1.6, essential: true });
    if (!reducedMotion) after(ms, () => { startBlend(1.5); setPlaying(true); });
  }

  function exitTour() {
    if (!tour.on) return;
    pauseTour();
    tour.on = false;
    pressed($('#btn-indus'), false);
    $('#tour').hidden = true;
    $('#fp-section').hidden = $('#fp-table').hidden = $('#fp-all').hidden = true;
    document.documentElement.style.setProperty('--fp-left', '0px');
    document.documentElement.style.setProperty('--fp-right', '0px');
    document.body.classList.remove('touring');
    map.setPadding({ top: 0, bottom: 0, left: 0, right: 0 });
    const s = tour.saved;
    if (s.layer) setLayer(s.layer);
    state.highlight = s.highlight;
    applyLayer();
    state.opacity = s.opacity;
    applyOpacity();
    state.overlays.districts = s.districts;
    applyOverlays();
    for (const id of ['panel-layers', 'panel-insights']) $(`#${id}`).classList.toggle('collapsed', s.collapsed.includes(id));
    state.river = s.river;
    state.terrain = s.terrain;
    pressed($('#btn-3d'), s.terrain);
    if (s.basemap !== state.basemap) setBasemap(s.basemap);
    else { applyTerrain(); applySelection(); }
    map.flyTo({ ...s.camera, duration: reducedMotion ? 0 : 3000, essential: true });
  }

  function buildTour() {
    $('#btn-indus').addEventListener('click', () => (tour.on ? exitTour() : enterTour()));
    $('#tour-exit').addEventListener('click', exitTour);
    $('#tour-play').addEventListener('click', () => { if (tour.route) (tour.playing ? pauseTour() : play()); });
    $('#tour-orbit').addEventListener('click', () => (tour.orbiting ? setOrbit(false) : startOrbit()));
    $('#tour-prev').addEventListener('click', () => tour.route && stepStop(-1));
    $('#tour-next').addEventListener('click', () => tour.route && stepStop(1));
    $('#tour-progress').addEventListener('input', (e) => {
      if (!tour.route) return;
      pauseTour();
      tour.km = Number(e.target.value);
      tour.bearing = routeHeading(tour.km);
      tour.blend = null;
      setPose(flightPose(tour.km, tour.bearing));
      updateTourHud();
    });

    const toggleFp = (key, other) => {
      tour[key] = !tour[key];
      if (tour[key] && innerWidth <= 1240) tour[other] = false; // no room for both side by side
      applyFpPanels();
    };
    $('#tour-sec').addEventListener('click', () => toggleFp('showSec', 'showTab'));
    $('#tour-tab').addEventListener('click', () => toggleFp('showTab', 'showSec'));
    addEventListener('resize', () => { if (tour.on) applyFpPanels(); });
    buildAllAreas();

    const seg = $('#tour-speed');
    for (const v of SPEEDS) {
      const b = el('button', { role: 'radio', 'aria-checked': String(v === tour.speed), title: `${v}× speed` }, `${v}×`);
      b.addEventListener('click', () => {
        tour.speed = v;
        for (const x of seg.children) x.setAttribute('aria-checked', String(x === b));
      });
      seg.append(b);
    }

    // Any hands-on map gesture pauses the flight so the user can look around
    for (const ev of ['mousedown', 'touchstart', 'wheel']) {
      map.on(ev, () => { if (tour.on && (tour.playing || tour.orbiting)) pauseTour(); });
    }
    map.on('idle', () => { if (tour.on && D.fpSections) updateTourHud(); });

    document.addEventListener('keydown', (e) => {
      if (!tour.on || !tour.route || e.target.closest?.('input, textarea')) return;
      if ((e.key === ' ' || e.key === 'Enter') && e.target.closest?.('button')) return; // the button handles it
      if (e.key === 'Escape') { if (!$('#fp-all').hidden) $('#fp-all').hidden = true; else exitTour(); }
      else if (e.key === ' ') { e.preventDefault(); tour.playing ? pauseTour() : play(); }
      else if (e.key === 'ArrowRight') stepStop(1);
      else if (e.key === 'ArrowLeft') stepStop(-1);
      else if (e.key === 'o' || e.key === 'O') (tour.orbiting ? setOrbit(false) : startOrbit());
    });
  }

  // ------------------------------------------------------------------ floodplain: data
  // Built by analyse_floodplain.py. Loaded on entering the flythrough.
  let fpReq = null;
  function loadFloodplain() {
    fpReq ||= Promise.all([
      getJSON('data/floodplain.json'), getJSON('data/fp_sections.json'), getJSON('data/floodplain.geojson'),
      getJSON('data/fp_transects.geojson'), getJSON('data/fp_infra.geojson'), getJSON('data/land_change.json').catch(() => null),
    ]).then(([fp, secs, outline, transects, infra, land]) => {
      if (land) {
        D.land = land;
        D.landI = Object.fromEntries(land.meta.metrics.map((m, i) => [m, i]));
        D.landReach = new Map(land.reaches.map((r) => [r.r, r]));
        D.landArea = new Map(land.areas.map((a) => [a.id, a]));
      }
      D.fp = fp;
      D.fpI = Object.fromEntries(fp.meta.metrics.map((m, i) => [m, i]));
      D.fpSections = secs.sections;
      D.fpOutline = outline;
      D.fpMask = worldMask(outline);
      D.fpTransects = transects;
      D.fpInfra = infra;
      D.fpReach = new Map(fp.reaches.map((r) => [r.r, r]));
      D.fpArea = new Map(fp.areas.map((a) => [a.id, a]));
      map.getSource('fp-mask')?.setData(D.fpMask);
      map.getSource('fp-outline')?.setData(outline);
      map.getSource('fp-transects')?.setData(transects);
      map.getSource('fp-infra')?.setData(infra);
    });
    return fpReq;
  }

  function applyFloodplainVis() {
    const on = tour.on && !!D.fp;
    for (const id of ['fp-dim', 'fp-edge', 'fp-transects', 'fp-transect-cur', 'fp-transect-label', 'fp-road-case', 'fp-road', 'fp-rail', 'fp-point', 'fp-point-label']) setVis(id, on);
  }

  const sectionAt = (km) => D.fpSections[Math.max(0, Math.min(D.fpSections.length - 1, Math.round(km / D.fp.meta.sec_km)))];
  const distName = (id) => D.distStats.get(id)?.name || 'No district data';

  function applyFpPanels() {
    const wide = innerWidth > 1240;
    const side = 420 + 2 * 16;
    $('#fp-section').hidden = !tour.showSec;
    $('#fp-table').hidden = !tour.showTab;
    pressed($('#tour-sec'), !!tour.showSec);
    pressed($('#tour-tab'), !!tour.showTab);
    const left = wide && tour.showSec ? side : 0, right = wide && tour.showTab ? side : 0;
    document.documentElement.style.setProperty('--fp-left', `${left}px`);
    document.documentElement.style.setProperty('--fp-right', `${right}px`);
    document.body.classList.toggle('fp-open', !!(tour.showSec || tour.showTab));
    const bottom = $('#tour').offsetHeight + 24;
    document.documentElement.style.setProperty('--tour-h', `${bottom}px`);
    map.setPadding({ top: 90, bottom, left, right });
    if (tour.route && D.fp) { FP.sec = -1; FP.tableKey = ''; updateTourHud(); }
  }

  // ------------------------------------------------------------------ floodplain: cross-section chart
  // Colours are the validated dark palette; land cover keeps "bare" neutral as the background class.
  const LC_COLORS = { 1: '#c98500', 2: '#d55181', 3: '#008300', 4: '#7b8494', 5: '#3987e5' };
  const LC_NAMES = { 1: 'Cropland', 2: 'Built-up', 3: 'Trees, shrub, grass', 4: 'Bare ground', 5: 'Water', 0: 'No data' };
  const CHANGE_NAMES = {
    0: 'No water recorded', 1: 'Land lost to permanent water', 2: 'Land turned seasonally wet', 3: 'Regained from permanent water',
    4: 'Regained from seasonal water', 5: 'Water in both years', 6: 'Wet at times, no lasting change',
  };
  const FP = { sec: -1, tableKey: '', cur: null };

  const landLayer = () => CFG.layers.find((l) => l.id === 'landloss');
  const LAND_NAMES = () => landLayer().legend.classes;

  function unRle(a) {
    let n = 0;
    for (let i = 0; i < a.length; i += 2) n += a[i];
    const out = new Uint8Array(n);
    let at = 0;
    for (let i = 0; i < a.length; i += 2) { out.fill(a[i + 1], at, at + a[i]); at += a[i]; }
    return out;
  }

  function niceStep(range, target) {
    const raw = range / target, p = 10 ** Math.floor(Math.log10(raw)), f = raw / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  }

  const signedM = (v) => (v == null ? '—' : v === 0 ? '0 m' : `${v > 0 ? '+' : '−'}${fmtKm(Math.abs(v))} m`);
  const bankNote = (v) => (v == null ? 'no main channel here' : v > 0 ? 'moved into the land' : v < 0 ? 'moved back' : 'no change');

  function showSection(sec) {
    if (FP.sec === sec.km || $('#fp-section').hidden) {
      if (FP.cur !== sec.km) markTransect(sec);
      return;
    }
    FP.sec = sec.km;
    markTransect(sec);
    const left = distName(sec.ld), right = distName(sec.rd);
    $('#fp-sec-title').textContent = `km ${fmtKm(sec.km)} · ${left === right ? left : `${left} | ${right}`}`;
    $('#fp-sec-sub').textContent = `Floodplain ${((sec.fpl + sec.fpr) / 1000).toFixed(1)} km wide${sec.ch != null ? ` · river level ~${fmtKm(Math.max(0, sec.ch))} m` : ''}`;

    const cls = unRle(sec.cls), wet = unRle(sec.wet), lc = unRle(sec.lc);
    const n = cls.length, st = 30;
    const x0 = sec.x0, x1 = x0 + (n - 1) * st;
    const W = 400, ML = 50, MR = 10, top = 22, PH = 112;
    const sx = (x) => ML + ((x - x0) / (x1 - x0 || 1)) * (W - ML - MR);

    // The surface model includes tree tops and houses: a 3-point median (450 m) keeps the land's shape
    const zLine = sec.z.map((v, i) => {
      const near = [sec.z[i - 1], v, sec.z[i + 1]].filter((q) => q != null);
      return v == null ? null : near.sort((a, b) => a - b)[near.length >> 1];
    });
    const zs = zLine.filter((v) => v != null);
    let zMin = zs.length ? Math.min(...zs) : 0, zMax = zs.length ? Math.max(...zs) : 1;
    if (zMax - zMin < 8) { const m = (zMax + zMin) / 2; zMin = m - 4; zMax = m + 4; }
    const yStep = niceStep(zMax - zMin, 3);
    const yLo = Math.floor(zMin / yStep) * yStep, yHi = Math.ceil(zMax / yStep) * yStep;
    const sy = (z) => top + PH - ((z - yLo) / (yHi - yLo)) * PH;

    let grid = '';
    for (let v = yLo; v <= yHi + 1e-6; v += yStep) {
      grid += `<line x1="${ML}" x2="${W - MR}" y1="${sy(v)}" y2="${sy(v)}" stroke="rgba(148,178,255,.13)"/>`
        + `<text x="${ML - 5}" y="${sy(v) + 3.5}" text-anchor="end">${fmtKm(v)}</text>`;
    }

    let line = '', area = '', open = false, first = 0, last = 0;
    zLine.forEach((v, i) => {
      const x = sx(x0 + i * 5 * st);
      if (v == null) { open = false; return; }
      const y = sy(v);
      line += `${open ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`;
      if (!open && !area) first = x;
      last = x;
      open = true;
    });
    area = line ? `${line}L${last.toFixed(1)} ${top + PH}L${first.toFixed(1)} ${top + PH}Z` : '';

    // Flood band, ground, then infrastructure and places on the ground line
    const bandL = sx(-sec.fpl), bandR = sx(sec.fpr);
    const band = `<rect x="${bandL}" y="${top}" width="${bandR - bandL}" height="${PH}" fill="rgba(34,211,238,.07)"/>`
      + `<line x1="${bandL}" x2="${bandL}" y1="${top}" y2="${top + PH}" stroke="rgba(34,211,238,.45)"/>`
      + `<line x1="${bandR}" x2="${bandR}" y1="${top}" y2="${top + PH}" stroke="rgba(34,211,238,.45)"/>`;

    const groundAt = (x) => {
      const v = zLine[Math.max(0, Math.min(zLine.length - 1, Math.round((x - x0) / (5 * st))))];
      return v == null ? yLo : v;
    };
    let marks = '';
    for (const [x, kind] of sec.xs) {
      const px = sx(x), gy = sy(groundAt(x));
      marks += kind === 'rail'
        ? `<rect x="${px - 3.5}" y="${gy - 3.5}" width="7" height="7" transform="rotate(45 ${px} ${gy})" fill="${FP_MARKS.rail}" stroke="#0a1224" stroke-width="2"/>`
        : `<rect x="${px - 3.5}" y="${gy - 3.5}" width="7" height="7" rx="1" fill="${FP_MARKS.road}" stroke="#0a1224" stroke-width="2"/>`;
    }
    // Only as many place names as fit; the rest are in the tooltip
    let labels = '', endX = -1e9;
    for (const [x, name] of sec.pl) {
      const px = sx(x), wText = name.length * 5.2 + 8;
      const gy = sy(groundAt(x));
      marks += `<circle cx="${px}" cy="${gy}" r="3" fill="#f8fafc" stroke="#0a1224" stroke-width="2"/>`;
      if (px > endX + 6 && px + wText < W - MR) {
        labels += `<text class="place" x="${px}" y="${Math.max(top + 9, gy - 8)}" text-anchor="start">${esc(name)}</text>`;
        endX = px + wText;
      }
    }

    // Strips: water then, water now, what changed, land cover
    const pal = D.palettes.erosion;
    const stripRow = (label, y, colorOf, arr) => {
      let out = `<text class="lbl" x="${ML - 5}" y="${y + 9}" text-anchor="end">${label}</text><rect x="${ML}" y="${y}" width="${W - ML - MR}" height="12" fill="rgba(255,255,255,.04)"/>`;
      let i = 0;
      while (i < n) {
        let j = i + 1;
        while (j < n && arr[j] === arr[i]) j++;
        const c = colorOf(arr[i]);
        if (c) out += `<rect x="${sx(x0 + i * st).toFixed(1)}" y="${y}" width="${Math.max(0.6, sx(x0 + j * st) - sx(x0 + i * st)).toFixed(1)}" height="12" fill="${c}"/>`;
        i = j;
      }
      return out;
    };
    const water = `rgb(${pal[5].join(',')})`;
    const sT = top + PH + 22;
    const strips = stripRow('1984–99', sT, (v) => (v & 1 ? water : null), wet)
      + stripRow('2000–24', sT + 15, (v) => (v & 2 ? water : null), wet)
      + stripRow('Change', sT + 30, (v) => (v >= 1 && v <= 4 ? `rgb(${pal[v].join(',')})` : null), cls)
      + stripRow('Land', sT + 45, (v) => LC_COLORS[v] || null, lc);

    // Distance axis, kilometres from the centre line
    const xStep = niceStep((x1 - x0) / 1000, 4) * 1000;
    let axis = '';
    for (let x = Math.ceil(x0 / xStep) * xStep; x <= x1; x += xStep) {
      axis += `<line x1="${sx(x)}" x2="${sx(x)}" y1="${top + PH}" y2="${top + PH + 4}" stroke="rgba(148,178,255,.3)"/>`
        + `<text x="${sx(x)}" y="${top + PH + 15}" text-anchor="middle">${x === 0 ? '0 km' : `${x < 0 ? '−' : '+'}${Math.abs(x / 1000)}`}</text>`;
    }
    const landLine = D.land?.sections[sec.km];
    const landCls = landLine ? unRle(landLine) : null;
    const LAND_C = D.palettes.landloss;
    const landStrip = landCls ? stripRow('2018–25', sT + 60, (v) => (LAND_C[v] ? `rgb(${LAND_C[v].join(',')})` : null), landCls) : '';
    const H = sT + (landCls ? 75 : 60);
    const box = $('#fp-chart');
    box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Ground profile across the Indus at km ${fmtKm(sec.km)}. Water width was ${sec.tw} km in 1984 to 1999 and ${sec.nw} km in 2000 to 2024.">
      <text class="lbl" x="${ML}" y="12">Left bank</text><text class="lbl" x="${W - MR}" y="12" text-anchor="end">Right bank</text>
      ${band}${grid}
      ${area ? `<path d="${area}" fill="rgba(148,178,255,.1)"/><path d="${line}" fill="none" stroke="#dbe4f3" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>` : ''}
      ${axis}${marks}${labels}${strips}${landStrip}
      <line id="fp-cross" x1="0" x2="0" y1="${top}" y2="${H - 3}" stroke="#f8fafc" stroke-opacity=".6" visibility="hidden"/>
      <rect id="fp-hit" x="${ML}" y="${top}" width="${W - ML - MR}" height="${H - top}" fill="transparent"/>
    </svg>`;

    const tip = $('#fp-chart-tip');
    const hit = box.querySelector('#fp-hit'), cross = box.querySelector('#fp-cross');
    tip.hidden = true;
    const move = (e) => {
      const r = box.querySelector('svg').getBoundingClientRect();
      const px = ((e.clientX - r.left) / r.width) * W;
      const x = Math.max(x0, Math.min(x1, x0 + ((px - ML) / (W - ML - MR)) * (x1 - x0)));
      const i = Math.max(0, Math.min(n - 1, Math.round((x - x0) / st)));
      cross.setAttribute('x1', sx(x)); cross.setAttribute('x2', sx(x)); cross.setAttribute('visibility', 'visible');
      const near = [...sec.xs.filter(([a]) => Math.abs(a - x) < 400).map(([, k, nm]) => `${k === 'rail' ? 'Railway' : 'Road'}${nm ? `: ${nm}` : ''}`),
        ...sec.pl.filter(([a]) => Math.abs(a - x) < 400).map(([, nm]) => nm)];
      const z = groundAt(x);
      tip.innerHTML = `<b>${x === 0 ? 'Centre line' : `${(Math.abs(x) / 1000).toFixed(1)} km ${x < 0 ? 'left' : 'right'} of centre`}</b><br>`
        + `Ground ${fmtKm(z)} m<br>${esc(CHANGE_NAMES[cls[i]])}<br>${esc(LC_NAMES[lc[i]])} (2021)`
        + (landCls && landCls[i] ? `<br>2018–25: ${esc(LAND_NAMES()[landCls[i]])}` : '')
        + (near.length ? `<br>${near.map(esc).join('<br>')}` : '');
      tip.style.left = `${e.clientX}px`;
      tip.style.top = `${e.clientY}px`;
      tip.hidden = false;
    };
    hit.addEventListener('pointermove', move);
    hit.addEventListener('pointerleave', () => { tip.hidden = true; cross.setAttribute('visibility', 'hidden'); });

    const tile = (k, v, note = '') => `<div><div class="k">${k}</div><div class="v mono">${v}</div>${note ? `<div class="s">${note}</div>` : ''}</div>`;
    const dW = sec.nw - sec.tw;
    $('#fp-sec-stats').innerHTML = tile('Water width on this line', `${sec.tw.toFixed(1)} → ${sec.nw.toFixed(1)} km`, `${dW >= 0 ? '+' : '−'}${Math.abs(dW).toFixed(1)} km, 1984–99 to 2000–24`)
      + tile('Eroded · regained', `${(sec.el + sec.er).toFixed(2)} · ${(sec.al + sec.ar).toFixed(2)} km`, `left ${sec.el.toFixed(2)} · right ${sec.er.toFixed(2)} km eroded`)
      + tile('Left bank of main channel', signedM(sec.shl), bankNote(sec.shl))
      + tile('Right bank of main channel', signedM(sec.shr), bankNote(sec.shr));
  }

  function markTransect(sec) {
    FP.cur = sec.km;
    for (const id of ['fp-transect-cur', 'fp-transect-label']) if (map.getLayer(id)) map.setFilter(id, ['==', ['get', 'km'], sec.km]);
  }

  // ------------------------------------------------------------------ floodplain: tables
  const fpV = (row, key) => row.v[D.fpI[key]];
  // Group rows have no key. A function key derives a value from the record.
  const FP_ROWS = [
    { group: 'River' },
    { label: 'Floodplain area', unit: 'km²', key: 'fp' },
    { label: 'Regularly wet, 1984–99', unit: 'km²', key: 'water_then' },
    { label: 'Regularly wet, 2000–24', unit: 'km²', key: 'water_now' },
    { group: 'Erosion' },
    { label: 'Land lost to permanent water', unit: 'km²', key: 'ero_perm' },
    { label: 'Land turned seasonally wet', unit: 'km²', key: 'ero_seas' },
    { label: 'Land regained from water', unit: 'km²', key: (r) => fpV(r, 'acc_perm') + fpV(r, 'acc_seas') },
    { label: 'Net change in dry land', unit: 'km²', key: (r) => fpV(r, 'acc_perm') + fpV(r, 'acc_seas') - fpV(r, 'ero_perm') - fpV(r, 'ero_seas'), signed: true },
    { label: 'Wet at times, no lasting change', unit: 'km²', key: 'passing' },
    { group: 'Farmland' },
    { label: 'Cropland in floodplain', unit: 'km²', key: 'crop_fp' },
    { label: 'Cropland on eroded or newly wet ground', unit: 'km²', key: 'crop_ero' },
    { label: 'Cropland within 1 km of the river', unit: 'km²', key: 'crop_near' },
    { group: 'Settlements' },
    { label: 'Built-up in floodplain', unit: 'km²', key: 'built_fp' },
    { label: 'Built-up on eroded or newly wet ground', unit: 'km²', key: 'built_ero' },
    { label: 'Built-up within 1 km of the river', unit: 'km²', key: 'built_near' },
    { label: 'Villages and towns in floodplain', key: 'place_fp', count: true },
    { label: 'Villages and towns on eroded or newly wet ground', key: 'place_ero', count: true },
    { label: 'Villages and towns within 1 km of the river', key: 'place_near', count: true },
    { label: 'Buildings in floodplain', key: 'bldg_fp', count: true },
    { label: 'Buildings on eroded or newly wet ground', key: 'bldg_ero', count: true },
    { label: 'Buildings within 1 km of the river', key: 'bldg_near', count: true },
    { group: 'Infrastructure' },
    { label: 'Main roads in floodplain', unit: 'km', key: 'major_fp' },
    { label: 'Main roads on eroded or newly wet ground', unit: 'km', key: 'major_ero' },
    { label: 'Main roads within 1 km of the river', unit: 'km', key: 'major_near' },
    { label: 'Other roads in floodplain', unit: 'km', key: 'minor_fp' },
    { label: 'Other roads on eroded or newly wet ground', unit: 'km', key: 'minor_ero' },
    { label: 'Railway in floodplain', unit: 'km', key: 'rail_fp' },
    { label: 'Railway on eroded or newly wet ground', unit: 'km', key: 'rail_ero' },
    { label: 'Bridges in floodplain', key: 'bridge_fp', count: true },
    { label: 'Schools in floodplain', key: 'edu_fp', count: true },
    { label: 'Schools within 1 km of the river', key: 'edu_near', count: true },
    { label: 'Health facilities in floodplain', key: 'health_fp', count: true },
    { label: 'Health facilities within 1 km of the river', key: 'health_near', count: true },
    { label: 'Dams and weirs', key: 'dam_fp', count: true },
  ];

  // Detailed 20 m data, where the Earth Engine exports reach
  const landV = (r, k) => r.v[D.landI[k]];
  const LAND_ROWS = () => [
    { label: 'Land lost to the river', unit: 'km²', key: (r) => landV(r, 'loss') },
    { label: 'Land deposited', unit: 'km²', key: (r) => landV(r, 'deposit') },
    { label: 'Net change in dry land', unit: 'km²', key: (r) => landV(r, 'deposit') - landV(r, 'loss'), signed: true },
    { label: 'Land lost per year', unit: 'km²', key: (r) => landV(r, 'loss') / landLayer().years },
    { label: 'Land deposited per year', unit: 'km²', key: (r) => landV(r, 'deposit') / landLayer().years },
    { label: 'Lost land that was cropland in 2021', unit: 'km²', key: (r) => landV(r, 'crop') },
    { label: 'Lost land that was built-up in 2021', unit: 'km²', key: (r) => landV(r, 'built') },
    { label: 'Lost land already water in 2021', unit: 'km²', key: (r) => landV(r, 'water21') },
    { label: 'Buildings on lost land', key: (r) => landV(r, 'bldg'), count: true },
    { label: `Buildings within ${D.land.meta.near_m} m of lost land`, key: (r) => landV(r, 'bldg_near'), count: true },
    { label: 'Villages and towns on lost land', key: (r) => landV(r, 'place'), count: true },
    { label: `Villages and towns within ${D.land.meta.near_m} m`, key: (r) => landV(r, 'place_near'), count: true },
    { label: 'Main roads on lost land', unit: 'km', key: (r) => landV(r, 'major') },
    { label: `Main roads within ${D.land.meta.near_m} m`, unit: 'km', key: (r) => landV(r, 'major_near') },
    { label: 'Other roads on lost land', unit: 'km', key: (r) => landV(r, 'minor') },
    { label: 'Railway on lost land', unit: 'km', key: (r) => landV(r, 'rail') },
    { label: `Railway within ${D.land.meta.near_m} m`, unit: 'km', key: (r) => landV(r, 'rail_near') },
    { label: 'Schools on lost land', key: (r) => landV(r, 'edu'), count: true },
    { label: 'Health facilities on lost land', key: (r) => landV(r, 'health'), count: true },
  ];

  const rowLabel = (d) => `${typeof d.key === 'string' && d.key.startsWith('bldg') && D.fp.meta.buildings === 'osm' ? `${d.label} (OSM only)` : d.label}${d.unit ? ` (${d.unit})` : ''}`;
  const rowValue = (def, rec) => (typeof def.key === 'function' ? def.key(rec) : fpV(rec, def.key));
  function fmtFp(def, v) {
    if (v == null) return '—';
    if (def.count) return fmtKm(v);
    const a = Math.abs(v);
    const txt = a === 0 ? '0' : a < 0.05 ? '<0.1' : a < 100 ? a.toFixed(1) : fmtKm(a);
    return def.signed ? `${v > 0 ? '+' : v < 0 ? '−' : ''}${txt}` : txt;
  }

  const buildingNote = () => (D.fp.meta.buildings === 'osm'
    ? '<span class="fp-warn">Buildings count OSM footprints only, which cover a small fraction of real buildings: the NDMA building server could not be reached when this was built. Treat as a minimum.</span> '
    : '');

  function showTable(km, sec) {
    const r = Math.floor(km / D.fp.meta.reach_km);
    const reach = D.fpReach.get(r) || D.fpReach.get(r - 1);
    if (!reach || $('#fp-table').hidden) return;
    const landOn = !!D.land && km >= D.land.meta.km0 && km <= D.land.meta.km1;
    const key = `${r}|${sec.ld}|${sec.rd}|${landOn}`;
    if (key === FP.tableKey) return;
    FP.tableKey = key;
    const cols = [{ name: 'This reach', rec: reach, title: `River km ${reach.km0}–${reach.km1}` }];
    const a = D.fpArea.get(sec.ld), b = D.fpArea.get(sec.rd);
    if (a) cols.push({ name: a.name, rec: a, title: `${a.name}: all floodplain in the district` });
    if (b && sec.rd !== sec.ld) cols.push({ name: b.name, rec: b, title: `${b.name}: all floodplain in the district` });
    cols.push({ name: 'All Indus', rec: { v: D.fp.total }, title: 'The whole floodplain' });

    $('#fp-tab-title').textContent = `River km ${fmtKm(reach.km0)}–${fmtKm(reach.km1)}`;
    const head = `<tr><th>Measure</th>${cols.map((c) => `<th title="${esc(c.title)}">${esc(c.name)}</th>`).join('')}</tr>`;
    const body = FP_ROWS.map((d) => (d.group
      ? `<tr class="grp"><td colspan="${cols.length + 1}">${d.group}</td></tr>`
      : `<tr><td>${rowLabel(d)}</td>${cols.map((c) => `<td>${fmtFp(d, rowValue(d, c.rec))}</td>`).join('')}</tr>`)).join('');
    // Detailed block first where it exists: it answers "what did the river take here"
    let land = '';
    if (landOn) {
      const lr = D.landReach.get(r);
      const lc = [{ name: 'This reach', rec: lr }];
      if (D.landArea.has(sec.ld)) lc.push({ name: a?.name || distName(sec.ld), rec: D.landArea.get(sec.ld) });
      if (sec.rd !== sec.ld && D.landArea.has(sec.rd)) lc.push({ name: b?.name || distName(sec.rd), rec: D.landArea.get(sec.rd) });
      lc.push({ name: 'Window', rec: { v: D.land.total } });
      const cells = (d) => lc.map((c) => `<td>${c.rec ? fmtFp(d, d.key(c.rec)) : '—'}</td>`).join('');
      land = `<thead><tr><th>Sukkur reach, 20 m · ${esc(landLayer().period)}</th>${lc.map((c) => `<th>${esc(c.name)}</th>`).join('')}</tr></thead>
        <tbody>${LAND_ROWS().map((d) => `<tr><td>${rowLabel(d)}</td>${cells(d)}</tr>`).join('')}</tbody>`;
    }
    const wrap = $('#fp-table-wrap');
    const top = wrap.scrollTop;
    wrap.innerHTML = `${land ? `<table class="fp-tbl fp-land">${land}</table>` : ''}<table class="fp-tbl"><thead>${head}</thead><tbody>${body}</tbody></table>
      <div class="fp-foot">${buildingNote()}Districts count the floodplain only, not the whole district. Eroded or newly wet ground became permanent or seasonal water since the 1980s, confirmed as a sustained change. Seasonally wet land is not necessarily lost.</div>`;
    wrap.scrollTop = top;
  }

  let allMode = 'district';
  const allDefs = () => (allMode === 'land' ? LAND_ROWS() : FP_ROWS.filter((d) => !d.group));
  const allRows = () => (allMode === 'land'
    ? D.land.areas.map((a) => ({ name: a.name, sub: a.province, km: `${D.land.meta.km0}–${D.land.meta.km1}`, rec: a, id: a.id }))
    : allMode === 'district'
    ? [...D.fp.areas].map((a) => ({ name: a.name, sub: a.province, km: `${a.km0}–${a.km1}`, rec: a, id: a.id }))
    : D.fp.reaches.map((r) => ({ name: `km ${r.km0}–${r.km1}`, sub: r.d.map(distName).join(', '), km: `${r.km0}–${r.km1}`, rec: r, r: r.r })));

  function renderAllAreas() {
    const defs = allDefs();
    const sec = tour.route ? sectionAt(tour.km) : null;
    const curR = Math.floor(tour.km / D.fp.meta.reach_km);
    const head = `<tr><th>${allMode === 'reach' ? 'Reach' : 'District'}</th><th>River km</th>${defs.map((d) => `<th>${rowLabel(d)}</th>`).join('')}</tr>`;
    const rows = allRows().map((a) => {
      const now = allMode === 'reach' ? a.r === curR : sec && (a.id === sec.ld || a.id === sec.rd);
      return `<tr${now ? ' class="now"' : ''}><td title="${esc(a.sub)}">${esc(a.name)}</td><td>${a.km}</td>${defs.map((d) => `<td>${fmtFp(d, rowValue(d, a.rec))}</td>`).join('')}</tr>`;
    }).join('');
    const whole = { v: allMode === 'land' ? D.land.total : D.fp.total };
    const total = `<tr class="now"><td>${allMode === 'land' ? 'Whole window' : 'All Indus'}</td><td></td>${defs.map((d) => `<td>${fmtFp(d, rowValue(d, whole))}</td>`).join('')}</tr>`;
    $('#fp-all-wrap').innerHTML = `<table class="fp-tbl"><thead>${head}</thead><tbody>${rows}${total}</tbody></table>
      <div class="fp-foot">${buildingNote()}Highlighted rows are where the camera is now. Reaches and districts are different cuts of the same floodplain.</div>`;
  }

  function downloadAllAreas() {
    const defs = allDefs();
    const q = (v) => `"${String(v).replace(/"/g, '""')}"`;
    const lines = [[allMode === 'reach' ? 'Reach' : 'District', allMode === 'reach' ? 'Districts' : 'Province', 'River km', ...defs.map(rowLabel)].map(q).join(',')];
    for (const a of allRows()) lines.push([q(a.name), q(a.sub), q(a.km), ...defs.map((d) => (rowValue(d, a.rec) ?? '')).map((v) => (typeof v === 'number' ? +v.toFixed(3) : v))].join(','));
    const url = URL.createObjectURL(new Blob(['\ufeff', lines.join('\n')], { type: 'text/csv' }));
    const link = el('a', { href: url, download: `indus_floodplain_by_${allMode}.csv` });
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  function buildAllAreas() {
    $('#fp-all-open').addEventListener('click', () => {
      $('#fp-all').hidden = false;
      $('#fp-all-mode').children[2].hidden = !D.land;
      renderAllAreas();
    });
    $('#fp-all-close').addEventListener('click', () => { $('#fp-all').hidden = true; });
    $('#fp-all').addEventListener('click', (e) => { if (e.target.id === 'fp-all') $('#fp-all').hidden = true; });
    $('#fp-all-csv').addEventListener('click', downloadAllAreas);
    const seg = $('#fp-all-mode');
    for (const [id, label] of [['district', 'By district'], ['reach', 'By 20 km reach'], ['land', 'Sukkur 20 m']]) {
      const b = el('button', { role: 'radio', 'aria-checked': String(id === allMode) }, label);
      b.addEventListener('click', () => {
        allMode = id;
        for (const x of seg.children) x.setAttribute('aria-checked', String(x === b));
        renderAllAreas();
      });
      seg.append(b);
    }
    const sw = (c, cls = '') => `<i class="${cls}" style="background:${c}"></i>`;
    const P = '#d95926,#c98500,#199e70,#3987e5'.split(',');
    const m = FP_MARKS;
    $('#fp-legend').innerHTML = `
      <div class="row"><span class="h">Change, and the erosion map layer</span>
        <span>${sw(P[0])}Lost to permanent water</span><span>${sw(P[1])}Turned seasonally wet</span><span>${sw(P[2])}Regained</span><span>${sw(P[3])}Water in both years</span><span>${sw('#7b8494')}No lasting change (map only)</span></div>
      <div class="row"><span class="h">Sukkur reach, 20 m detail (replaces the layer above there)</span>
        <span>${sw(P[0])}Lost to the river</span><span>${sw(P[2])}Deposited</span><span>${sw(P[3])}Water in both</span></div>
      <div class="row"><span class="h">Land cover, ESA WorldCover 2021</span>
        ${Object.entries(LC_COLORS).map(([k, c]) => `<span>${sw(c)}${LC_NAMES[k]}</span>`).join('')}</div>
      <div class="row"><span class="h">On the map and the profile</span>
        <span>${sw(m.road, 'line')}Main road</span><span>${sw(m.rail, 'line')}Railway</span><span>${sw(m.bridge, 'dot')}Bridge</span><span>${sw(m.dam, 'dot')}Dam, weir</span>
        <span>${sw(m.school, 'dot')}School</span><span>${sw(m.health, 'dot')}Health</span><span>${sw('#fde047', 'line')}This cross-section</span></div>`;
    $('#fp-notes-text').textContent = 'Built from free satellite products, not field surveys. Water is the JRC Landsat record, 1984–2024. Land counts as eroded or regained only if it changed between the first year of the record and 2024 '
      + 'and the same months in 1984–99 and 2000–24 confirm it: this filters out the thinly observed first year and one-off floods, but flooding that recurs for years, ponding and irrigation can still pass. '
      + 'The 1984–99 and 2000–24 strips are estimates from JRC overall occurrence and occurrence change, counting a pixel wet at 25% or more. '
      + 'The profile line is the Copernicus surface model, which includes trees and buildings and cannot see the river bed, so it shows the shape of the land, not channel depth. '
      + 'Bank movement compares the outer edges of the main water belt and can swing widely where the river is braided. '
      + 'Land cover is from 2021, so land already under permanent water shows as water and what it used to be is not known. '
      + 'Near Sukkur, the 20 m layer compares water in Nov–Jan of 2018–19 and 2025–26, so a different winter river level also shows up as loss or deposition, and sand bars in the channel count as land.';
  }

  // ------------------------------------------------------------------ boot
  buildBasemaps();
  buildTools();
  buildOverlays();
  buildTour();
  buildReport();

  if (location.protocol === 'file:') {
    $('#intro-status').textContent = 'Open this portal through a web server: see README.md';
    return;
  }
  buildDsdi();

  dataReady.then(() => {
    buildLayerCards();
    bindTimebar();
    buildOpacity();
    buildMetricTabs();
    buildInsightTabs();
    buildSearch();
    renderInsights();
    $('#intro-status').textContent = 'Loading water layers';
  }).catch((err) => {
    console.error(err);
    $('#intro-status').textContent = 'Could not load portal data. Run the scripts in README.md first.';
  });

  map.on('load', async () => {
    await dataReady;
    updateView();
    $('#intro').classList.add('done');
    setTimeout(() => map.flyTo({ ...homeCamera(), duration: reducedMotion ? 0 : 6500, curve: 1.25, essential: true }), 400);
  });
})();
