const TILE_SIZE = 256;
const MAX_LATITUDE = 85.05112878;

// Reduced Natural Earth 1:110m Admin-0 geometry for the Xaver documentation
// region. Natural Earth data is public domain: https://www.naturalearthdata.com/
const COUNTRIES = [
  {
    name: 'Belgium',
    polygons: [[
      [6.156658, 50.803721], [6.043073, 50.128052], [5.782417, 50.090328],
      [5.674052, 49.529484], [4.799222, 49.985373], [4.286023, 49.907497],
      [3.588184, 50.378992], [3.123252, 50.780363], [2.658422, 50.796848],
      [2.513573, 51.148506], [3.314971, 51.345781], [4.047071, 51.267259],
      [4.973991, 51.475024], [5.606976, 51.037298], [6.156658, 50.803721],
    ]],
  },
  {
    name: 'Netherlands',
    polygons: [[
      [6.90514, 53.482162], [7.092053, 53.144043], [6.84287, 52.22844],
      [6.589397, 51.852029], [5.988658, 51.851616], [6.156658, 50.803721],
      [5.606976, 51.037298], [4.973991, 51.475024], [4.047071, 51.267259],
      [3.314971, 51.345755], [3.830289, 51.620545], [4.705997, 53.091798],
      [6.074183, 53.510403], [6.90514, 53.482162],
    ]],
  },
  {
    name: 'Germany',
    polygons: [[
      [14.119686, 53.757029], [14.353315, 53.248171], [14.074521, 52.981263],
      [14.4376, 52.62485], [14.685026, 52.089947], [14.607098, 51.745188],
      [15.016996, 51.106674], [14.570718, 51.002339], [14.307013, 51.117268],
      [14.056228, 50.926918], [13.338132, 50.733234], [12.240111, 50.266338],
      [12.521024, 49.547415], [13.595946, 48.877172], [13.243357, 48.416115],
      [12.884103, 48.289146], [12.932627, 47.467646], [12.62076, 47.672388],
      [11.426414, 47.523766], [10.544504, 47.566399], [10.402084, 47.302488],
      [9.896068, 47.580197], [8.522612, 47.830828], [8.317301, 47.61358],
      [7.466759, 47.620582], [7.593676, 48.333019], [8.099279, 49.017784],
      [6.65823, 49.201958], [6.18632, 49.463803], [6.242751, 49.902226],
      [6.043073, 50.128052], [6.156658, 50.803721], [5.988658, 51.851616],
      [6.589397, 51.852029], [6.84287, 52.22844], [7.092053, 53.144043],
      [6.90514, 53.482162], [7.100425, 53.693932], [7.936239, 53.748296],
      [8.121706, 53.527792], [8.800734, 54.020786], [8.572118, 54.395646],
      [8.526229, 54.962744], [9.282049, 54.830865], [9.921906, 54.983104],
      [9.93958, 54.596642], [10.950112, 54.363607], [10.939467, 54.008693],
      [11.956252, 54.196486], [12.51844, 54.470371], [13.647467, 54.075511],
      [14.119686, 53.757029],
    ]],
  },
  {
    name: 'Denmark',
    polygons: [
      [
        [9.921906, 54.983104], [9.282049, 54.830865], [8.526229, 54.962744],
        [8.120311, 55.517723], [8.089977, 56.540012], [8.256582, 56.809969],
        [8.543438, 57.110003], [9.424469, 57.172066], [9.775559, 57.447941],
        [10.580006, 57.730017], [10.546106, 57.215733], [10.25, 56.890016],
        [10.369993, 56.609982], [10.912182, 56.458621], [10.667804, 56.081383],
        [10.369993, 56.190007], [9.649985, 55.469999], [9.921906, 54.983104],
      ],
      [
        [12.370904, 56.111407], [12.690006, 55.609991], [12.089991, 54.800015],
        [11.043543, 55.364864], [10.903914, 55.779955], [12.370904, 56.111407],
      ],
    ],
  },
  {
    name: 'Sweden',
    polygons: [[
      [11.027369, 58.856149], [11.468272, 59.432393], [12.300366, 60.117933],
      [12.631147, 61.293572], [11.992064, 61.800362], [11.930569, 63.128318],
      [13.571916, 64.049114], [13.919905, 64.445421], [15.108411, 66.193867],
      [16.768879, 68.013937], [17.993868, 68.567391], [19.87856, 68.407194],
      [20.645593, 69.106247], [21.978535, 68.616846], [23.539473, 67.936009],
      [23.56588, 66.396051], [23.903379, 66.006927], [22.183173, 65.723741],
      [21.213517, 65.026005], [19.778876, 63.609554], [17.847779, 62.7494],
      [17.119555, 61.341166], [18.787722, 60.081914], [17.869225, 58.953766],
      [16.829185, 58.719827], [16.44771, 57.041118], [15.879786, 56.104302],
      [14.666681, 56.200885], [14.100721, 55.407781], [12.942911, 55.361737],
      [12.625101, 56.30708], [11.787942, 57.441817], [11.027369, 58.856149],
    ]],
  },
];

const LABELS = [
  ['North Sea', 4.8, 55.2, true],
  ['Netherlands', 5.3, 52.35, false],
  ['Germany', 10.2, 52.2, false],
  ['Denmark', 9.5, 56.25, false],
  ['Sweden', 14.8, 57.3, false],
];

function project(lon, lat, zoom) {
  const scale = TILE_SIZE * 2 ** zoom;
  const clampedLatitude = Math.max(-MAX_LATITUDE, Math.min(MAX_LATITUDE, lat));
  const sinLatitude = Math.sin((clampedLatitude * Math.PI) / 180);
  return [
    ((lon + 180) / 360) * scale,
    (0.5 - Math.log((1 + sinLatitude) / (1 - sinLatitude)) / (4 * Math.PI)) * scale,
  ];
}

function tileCoordinates(url) {
  const pathname = new URL(url).pathname;
  const match = pathname.match(/\/(\d+)\/(\d+)\/(\d+)\.png$/);
  if (!match) return null;
  return { zoom: Number(match[1]), x: Number(match[2]), y: Number(match[3]) };
}

function escapeXml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&apos;');
}

function renderDocumentationTile(url) {
  const coordinates = tileCoordinates(url);
  if (!coordinates) return null;
  const { zoom, x, y } = coordinates;
  const originX = x * TILE_SIZE;
  const originY = y * TILE_SIZE;
  const paths = [];

  for (const country of COUNTRIES) {
    for (const ring of country.polygons) {
      const commands = ring.map(([lon, lat], index) => {
        const [globalX, globalY] = project(lon, lat, zoom);
        const localX = (globalX - originX).toFixed(2);
        const localY = (globalY - originY).toFixed(2);
        return `${index === 0 ? 'M' : 'L'}${localX} ${localY}`;
      });
      paths.push(`<path d="${commands.join(' ')} Z" data-country="${escapeXml(country.name)}"/>`);
    }
  }

  const labels = [];
  for (const [name, lon, lat, sea] of LABELS) {
    const [globalX, globalY] = project(lon, lat, zoom);
    const localX = globalX - originX;
    const localY = globalY - originY;
    if (localX < -40 || localX > TILE_SIZE + 40 || localY < -20 || localY > TILE_SIZE + 20) continue;
    labels.push(
      `<text x="${localX.toFixed(2)}" y="${localY.toFixed(2)}" class="${sea ? 'sea' : 'land'}">${escapeXml(name)}</text>`,
    );
  }

  const body = `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
  <rect width="256" height="256" fill="#d9eef7"/>
  <g fill="#e8e2cf" stroke="#5b6f80" stroke-width="1.3" stroke-linejoin="round">${paths.join('')}</g>
  <g font-family="sans-serif" font-size="12" font-weight="700" text-anchor="middle" paint-order="stroke" stroke="#fff" stroke-width="3" stroke-linejoin="round" fill="#34495a">${labels.join('')}</g>
</svg>`;
  return { body, pathCount: paths.length, labelCount: labels.length };
}

async function installDocumentationTileProvider(page) {
  const stats = { requests: 0, paths: 0, labels: 0 };
  await page.route('https://*.tile.openstreetmap.org/**', async (route) => {
    const rendered = renderDocumentationTile(route.request().url());
    if (!rendered) {
      await route.abort();
      return;
    }
    stats.requests += 1;
    stats.paths += rendered.pathCount;
    stats.labels += rendered.labelCount;
    await route.fulfill({
      status: 200,
      contentType: 'image/svg+xml',
      headers: {
        'Access-Control-Allow-Origin': '*',
        'Cache-Control': 'no-store',
      },
      body: rendered.body,
    });
  });
  return stats;
}

module.exports = {
  installDocumentationTileProvider,
  renderDocumentationTile,
};
