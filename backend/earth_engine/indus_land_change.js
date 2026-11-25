// ============================================================
// INDUS RIVER: LAND LOSS AND RIVERBANK EROSION DETECTION
// Google Earth Engine - JavaScript
// Satellite: Sentinel-2 Surface Reflectance
// Method: MNDWI-based water classification and change detection
// ============================================================

// 1. STUDY AREA
// Example area: Indus River near Sukkur, Sindh, Pakistan.
// Draw your own polygon in the GEE Code Editor if preferred.

var roi = ee.Geometry.Rectangle([
  68.45, 27.45,
  69.15, 27.95
]);

Map.centerObject(roi, 10);
Map.addLayer(roi, {color: 'yellow'}, 'Study Area');

// 2. ANALYSIS PERIOD
// Use the same months in both years to reduce seasonal effects.

var year1 = 2018;
var year2 = 2025;

// Example: November to February
var startMonth = 11;
var endMonth = 2;

// MNDWI threshold. Adjust after inspecting the imagery.
var waterThreshold = 0.05;

// 3. SENTINEL-2 CLOUD MASKING

function maskS2(image) {
  var scl = image.select('SCL');

  // Remove cloud shadows, clouds, cirrus and snow.
  var mask = scl.neq(3)
    .and(scl.neq(8))
    .and(scl.neq(9))
    .and(scl.neq(10))
    .and(scl.neq(11));

  return image.updateMask(mask);
}

// 4. CREATE SEASONAL COMPOSITE

function getComposite(year) {

  var startDate = ee.Date.fromYMD(year, startMonth, 1);

  // Handle November-February crossing calendar years.
  var endDate = ee.Date.fromYMD(year + 1, endMonth, 1);

  var collection = ee.ImageCollection(
    'COPERNICUS/S2_SR_HARMONIZED'
  )
    .filterBounds(roi)
    .filterDate(startDate, endDate)
    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 40))
    .map(maskS2);

  print('Image count for ' + year, collection.size());

  var composite = collection.median().clip(roi);

  // MNDWI = (Green - SWIR) / (Green + SWIR)
  // Sentinel-2 Green = B3; SWIR1 = B11.

  var mndwi = composite.normalizedDifference(
    ['B3', 'B11']
  ).rename('MNDWI');

  return composite.addBands(mndwi);
}

// 5. GENERATE COMPOSITES

var image1 = getComposite(year1);
var image2 = getComposite(year2);

// 6. CLASSIFY RIVER WATER

var water1 = image1.select('MNDWI')
  .gt(waterThreshold)
  .rename('Water_' + year1);

var water2 = image2.select('MNDWI')
  .gt(waterThreshold)
  .rename('Water_' + year2);

// 7. DETECT LAND LOSS (EROSION)

// Land in year 1 which became water in year 2.

var erosion = water1.not()
  .and(water2)
  .selfMask()
  .rename('Land_Loss');

// 8. DETECT LAND DEPOSITION

// Water in year 1 which became land in year 2.

var deposition = water1
  .and(water2.not())
  .selfMask()
  .rename('Land_Deposition');

// 9. CALCULATE AREA

var pixelArea = ee.Image.pixelArea();

var erosionArea = pixelArea
  .updateMask(erosion)
  .reduceRegion({
    reducer: ee.Reducer.sum(),
    geometry: roi,
    scale: 20,
    maxPixels: 1e13,
    tileScale: 4
  });

var depositionArea = pixelArea
  .updateMask(deposition)
  .reduceRegion({
    reducer: ee.Reducer.sum(),
    geometry: roi,
    scale: 20,
    maxPixels: 1e13,
    tileScale: 4
  });

var erosionSqKm = ee.Number(
  erosionArea.get('area')
).divide(1e6);

var erosionHa = ee.Number(
  erosionArea.get('area')
).divide(10000);

var depositionSqKm = ee.Number(
  depositionArea.get('area')
).divide(1e6);

var depositionHa = ee.Number(
  depositionArea.get('area')
).divide(10000);

print('================================');
print('INDUS RIVER CHANGE ANALYSIS');
print('Comparison:', year1 + ' to ' + year2);
print('Land lost (hectares):', erosionHa);
print('Land lost (sq. km):', erosionSqKm);
print('Land deposited (hectares):', depositionHa);
print('Land deposited (sq. km):', depositionSqKm);
print('================================');

// 10. VISUALIZATION

var rgbVis = {
  bands: ['B4', 'B3', 'B2'],
  min: 0,
  max: 3000,
  gamma: 1.2
};

Map.addLayer(
  image1,
  rgbVis,
  'Sentinel-2 RGB ' + year1,
  false
);

Map.addLayer(
  image2,
  rgbVis,
  'Sentinel-2 RGB ' + year2,
  true
);

Map.addLayer(
  water1.selfMask(),
  {palette: ['0000FF']},
  'River Water ' + year1,
  false
);

Map.addLayer(
  water2.selfMask(),
  {palette: ['00BFFF']},
  'River Water ' + year2,
  true
);

Map.addLayer(
  erosion,
  {palette: ['FF0000']},
  'LAND LOSS / EROSION',
  true
);

Map.addLayer(
  deposition,
  {palette: ['00FF00']},
  'LAND DEPOSITION',
  true
);

// 11. EXPORT LAND LOSS MAP TO GOOGLE DRIVE

Export.image.toDrive({
  image: erosion.unmask(0).toByte(),
  description: 'Indus_River_Land_Loss_' + year1 + '_' + year2,
  folder: 'GEE_Indus_Erosion',
  fileNamePrefix: 'Indus_Land_Loss',
  region: roi,
  scale: 20,
  maxPixels: 1e13,
  crs: 'EPSG:32642'
});

// 12. EXPORT LAND DEPOSITION MAP

Export.image.toDrive({
  image: deposition.unmask(0).toByte(),
  description: 'Indus_River_Deposition_' + year1 + '_' + year2,
  folder: 'GEE_Indus_Erosion',
  fileNamePrefix: 'Indus_Land_Deposition',
  region: roi,
  scale: 20,
  maxPixels: 1e13,
  crs: 'EPSG:32642'
});

// 13. EXPORT CURRENT WATER EXTENT

Export.image.toDrive({
  image: water2.unmask(0).toByte(),
  description: 'Indus_River_Water_' + year2,
  folder: 'GEE_Indus_Erosion',
  fileNamePrefix: 'Indus_Water_Extent',
  region: roi,
  scale: 20,
  maxPixels: 1e13,
  crs: 'EPSG:32642'
});
