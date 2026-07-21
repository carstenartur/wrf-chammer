const { test, expect } = require('@playwright/test');
const { renderDocumentationTile } = require('./documentation-tile-provider');

function tileFor(lon, lat, zoom) {
  const count = 2 ** zoom;
  const latitude = Math.max(-85.05112878, Math.min(85.05112878, lat));
  const sinLatitude = Math.sin((latitude * Math.PI) / 180);
  return {
    x: Math.floor(((lon + 180) / 360) * count),
    y: Math.floor(
      (0.5 - Math.log((1 + sinLatitude) / (1 - sinLatitude)) / (4 * Math.PI))
      * count,
    ),
  };
}

test('render projected Natural Earth geography for the Xaver region', () => {
  const zoom = 5;
  const tile = tileFor(10.2, 52.2, zoom);
  const rendered = renderDocumentationTile(
    `https://a.tile.openstreetmap.org/${zoom}/${tile.x}/${tile.y}.png`,
  );

  expect(rendered).not.toBeNull();
  expect(rendered.pathCount).toBeGreaterThan(0);
  expect(rendered.labelCount).toBeGreaterThan(0);
  expect(rendered.body).toContain('data-country="Germany"');
  expect(rendered.body).toContain('Germany');
  expect(rendered.body).toContain('#d9eef7');
  expect(rendered.body).toContain('#e8e2cf');
});

test('reject non-OpenStreetMap tile paths', () => {
  expect(renderDocumentationTile('https://example.invalid/not-a-tile')).toBeNull();
});
