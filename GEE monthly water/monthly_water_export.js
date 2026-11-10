// ============================================================
// PAKISTAN: MONTHLY SURFACE WATER, MARCH 1984 TO DECEMBER 2021
// Google Earth Engine - JavaScript
// Source: JRC Monthly Water History v1.4 (Landsat, 30 m)
//
// For the portal's timeline. Each export holds two bands per month, as a percentage of each
// grid cell (about 280 m):
//   w_YYYY_MM  share of the cell seen as water that month
//   s_YYYY_MM  share of the cell the satellite could see that month
// A low s_ value means cloud or no image, so the month is unknown there, not dry.
//
// HOW TO RUN
//   1. Paste into https://code.earthengine.google.com and press Run.
//   2. Open the Tasks tab and press RUN on each export. There is one per block of years.
//   3. When they finish, copy the .tif files from the Google Drive folder below into
//      "GEE monthly water" in the project folder, and tell Claude.
//   If an export fails with a memory error, lower YEARS_PER_FILE to 2 and run again.
// ============================================================

var FIRST_YEAR = 1984;
var FIRST_MONTH = 3;      // the record starts in March 1984
var LAST_YEAR = 2021;
var YEARS_PER_FILE = 5;
var DRIVE_FOLDER = 'NDMA_water_monthly';

// The grid lines up with the portal's 30 m water data: ten JRC pixels to a cell, from 60 E, 40 N.
var CELL = 0.0025;
var CRS_TRANSFORM = [CELL, 0, 60, 0, -CELL, 40];
var REGION = ee.Geometry.Rectangle([60.75, 23.75, 79.5, 37.25], null, false);

var history = ee.ImageCollection('JRC/GSW1_4/MonthlyHistory');
var native30m = history.first().projection();

function pad(n) {
  return (n < 10 ? '0' : '') + n;
}

// Band "water": 0 no data, 1 not water, 2 water
function monthShares(year, month) {
  var start = ee.Date.fromYMD(year, month, 1);
  var water = ee.Image(history.filterDate(start, start.advance(1, 'month')).first())
    .select('water')
    .unmask(0);
  var label = year + '_' + pad(month);
  return water.eq(2).rename('w_' + label)
    .addBands(water.gt(0).rename('s_' + label))
    .float()
    .setDefaultProjection(native30m)
    .reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 256})
    .multiply(100)
    .round()
    .toByte();
}

function monthsOfYear(year) {
  var months = [];
  for (var m = (year === FIRST_YEAR ? FIRST_MONTH : 1); m <= 12; m++) {
    months.push(m);
  }
  return months;
}

var totalMonths = 0;
for (var y0 = FIRST_YEAR; y0 <= LAST_YEAR; y0 += YEARS_PER_FILE) {
  var y1 = Math.min(y0 + YEARS_PER_FILE - 1, LAST_YEAR);
  var stack = [];
  for (var y = y0; y <= y1; y++) {
    var months = monthsOfYear(y);
    for (var i = 0; i < months.length; i++) {
      stack.push(monthShares(y, months[i]));
      totalMonths++;
    }
  }
  var name = 'water_monthly_' + y0 + '_' + y1;
  Export.image.toDrive({
    image: ee.Image.cat(stack),
    description: name,
    folder: DRIVE_FOLDER,
    fileNamePrefix: name,
    region: REGION,
    crs: 'EPSG:4326',
    crsTransform: CRS_TRANSFORM,
    maxPixels: 1e10,
    fileFormat: 'GeoTIFF'
  });
}
print('Months in the exports (expect 454):', totalMonths);

// A quick look before exporting: August 2010, during the 2010 floods.
Map.setCenter(69.5, 28, 6);
var preview = monthShares(2010, 8);
Map.addLayer(preview.select('w_2010_08').selfMask(), {min: 0, max: 100, palette: ['#7dd3fc', '#1d4ed8']}, 'Water, August 2010');
Map.addLayer(preview.select('s_2010_08').lt(50).selfMask(), {palette: ['#9ca3af']}, 'Mostly unseen, August 2010', false);
