#!/bin/sh
# ci/generate-user-guide-screenshots.sh — generate user-guide screenshots from the real local UI flow.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)
UI_DIR="${REPO_ROOT}/workbench/ui"
E2E_DIR="${REPO_ROOT}/workbench/e2e"
BASEMAP_MODE=${WRF_SCREENSHOT_BASEMAP:-openstreetmap}
SCREENSHOT_OUTPUT=${WRF_SCREENSHOT_OUTPUT_DIR:-doc/user-guide/screenshots}
SCREENSHOT_DIR="${REPO_ROOT}/${SCREENSHOT_OUTPUT}"

case "${BASEMAP_MODE}" in
    openstreetmap|offline-natural-earth) ;;
    *)
        printf 'Unsupported WRF_SCREENSHOT_BASEMAP: %s\n' "${BASEMAP_MODE}" >&2
        exit 2
        ;;
esac

if [ "${CI:-}" = "true" ] && [ "${BASEMAP_MODE}" = "openstreetmap" ] && [ "${WRF_ALLOW_LIVE_OSM_SCREENSHOTS:-}" != "1" ]; then
    printf '%s\n' 'Live OpenStreetMap screenshot generation is disabled in automated CI by default.' >&2
    printf '%s\n' 'Use offline-natural-earth for PR QA, or explicitly authorize a one-off human-requested capture.' >&2
    exit 2
fi

mkdir -p "${SCREENSHOT_DIR}"
export WRF_SCREENSHOT_BASEMAP="${BASEMAP_MODE}"
export WRF_SCREENSHOT_OUTPUT_DIR="${SCREENSHOT_OUTPUT}"

printf 'Building modern Workbench UI...\n'
cd "${UI_DIR}"
if [ ! -d node_modules ]; then
    npm install
fi
npm run build

printf '\nPreparing browser automation...\n'
printf 'Basemap mode: %s\n' "${BASEMAP_MODE}"
printf 'Screenshot output: %s\n' "${SCREENSHOT_DIR}"
cd "${E2E_DIR}"
if [ ! -d node_modules ]; then
    npm install
fi

if [ "${WORKBENCH_SKIP_PLAYWRIGHT_INSTALL:-}" != "1" ]; then
    if [ "${CI:-}" = "true" ]; then
        npx playwright install --with-deps chromium
    else
        npx playwright install chromium
    fi
else
    printf 'Skipping Playwright browser install; container image already provides browsers.\n'
fi

npm run screenshots

printf '\nScreenshots written to: %s\n' "${SCREENSHOT_DIR}"
ls -1 "${SCREENSHOT_DIR}"
