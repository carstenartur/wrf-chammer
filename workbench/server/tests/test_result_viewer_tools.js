#!/usr/bin/env node

const assert = require('node:assert/strict');

const {
  buildLayerGeoJson,
  buildPointCsv,
  canvasToCoordinate,
  coordinateToCanvas,
  haversineKm,
  normalizeBounds,
  northUpGridCell,
  parseJobId,
  timeLabel,
} = require('../../web/result-viewer-tools.js');

const bounds = { west: 2, south: 51, east: 14, north: 59 };

assert.deepEqual(normalizeBounds({ bounds: [2, 51, 14, 59] }), bounds);
assert.deepEqual(normalizeBounds({ bounds }), bounds);
assert.equal(normalizeBounds({ bounds: [14, 51, 2, 59] }), null);

assert.deepEqual(northUpGridCell(1, 1, 100, 100, 10, 10), { i: 0, j: 9 });
assert.deepEqual(northUpGridCell(99, 99, 100, 100, 10, 10), { i: 9, j: 0 });
assert.deepEqual(coordinateToCanvas(2, 59, bounds, 1200, 800), { x: 0, y: 0 });
assert.deepEqual(coordinateToCanvas(14, 51, bounds, 1200, 800), { x: 1200, y: 800 });
assert.deepEqual(canvasToCoordinate(600, 400, bounds, 1200, 800), { lon: 8, lat: 55 });
assert.ok(haversineKm({ lat: 55, lon: 8 }, { lat: 55, lon: 8.1 }) > 6);

const layer = {
  id: 'wind10m',
  type: 'raster-time-series',
  unit: 'm s-1',
  times: ['2013-12-05T12:00:00Z', '2013-12-05T13:00:00Z'],
  frames: [
    [[1, 2], [3, 4]],
    [[5, 6], [7, 8]],
  ],
};
const selection = {
  i: 1,
  j: 1,
  gridLat: 55,
  gridLon: 8,
  clickLat: 55.01,
  clickLon: 8.01,
  distanceKm: 1.28,
};
const csv = buildPointCsv({
  jobId: 'sim-aaaaaaaaaaaa-bbbbbbbbbbbb',
  layer,
  layerMeta: { label: '10 m wind speed', unit: 'm s-1' },
  selection,
  coordinateSource: 'model-coordinate-layer',
});
assert.match(csv, /2013-12-05T12:00:00Z/);
assert.match(csv, /2013-12-05T13:00:00Z/);
assert.match(csv, /,4,true,false/);
assert.match(csv, /,8,true,false/);
assert.match(csv, /model-coordinate-layer/);

const geojson = buildLayerGeoJson({
  jobId: 'sim-aaaaaaaaaaaa-bbbbbbbbbbbb',
  layer,
  layerMeta: { label: '10 m wind speed', unit: 'm s-1' },
  timeIdx: 1,
  bounds,
  coordinateAt(i, j) {
    return { lon: 2 + i, lat: 51 + j };
  },
  coordinateSource: 'linear-domain-bounds',
});
assert.equal(geojson.type, 'FeatureCollection');
assert.equal(geojson.features.length, 4);
assert.deepEqual(geojson.features[3].geometry.coordinates, [3, 52]);
assert.equal(geojson.features[3].properties.value, 8);
assert.equal(geojson.features[3].properties.time, '2013-12-05T13:00:00Z');
assert.equal(geojson.features[3].properties.observation, false);

const maximum = { id: 'max_wind10m', type: 'raster-max', data: [[12]] };
assert.equal(timeLabel(maximum, 0), 'maximum-over-simulation-period');
assert.throws(
  () => buildLayerGeoJson({
    jobId: 'sim-aaaaaaaaaaaa-bbbbbbbbbbbb',
    layer: { ...layer, frames: [Array.from({ length: 400 }, () => Array(400).fill(1))] },
    layerMeta: {},
    timeIdx: 0,
    bounds,
    coordinateAt() { return { lat: 55, lon: 8 }; },
  }),
  /limited/,
);

assert.equal(
  parseJobId('/jobs/sim-aaaaaaaaaaaa-bbbbbbbbbbbb/results/'),
  'sim-aaaaaaaaaaaa-bbbbbbbbbbbb',
);
assert.equal(parseJobId('/jobs/other/results/'), '');

console.log('Result viewer geography and export tests passed');
