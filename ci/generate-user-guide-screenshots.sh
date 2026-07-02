#!/bin/sh
# ci/generate-user-guide-screenshots.sh — generate user-guide screenshots from the real local UI flow.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)
UI_DIR="${REPO_ROOT}/workbench/ui"
E2E_DIR="${REPO_ROOT}/workbench/e2e"
SCREENSHOT_DIR="${REPO_ROOT}/doc/user-guide/screenshots"

mkdir -p "${SCREENSHOT_DIR}"

printf 'Building modern Workbench UI...\n'
cd "${UI_DIR}"
if [ ! -d node_modules ]; then
    npm install
fi
npm run build

printf '\nPreparing browser automation...\n'
cd "${E2E_DIR}"
if [ -f xaver-weather-map-runner.b64 ]; then
    base64 -d xaver-weather-map-runner.b64 > xaver-weather-map.spec.js
fi
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
