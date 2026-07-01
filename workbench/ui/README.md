# Modern Workbench UI

This directory contains the modular browser UI foundation for the local WRF
Workbench.

The first implementation intentionally mirrors the existing Xaver dry-run flow
while moving the code to a modern frontend structure:

```text
workbench/ui/
  src/
    features/
      events/
    shared/
      api/
    App.ts
    main.tsx
    styles.css
```

## Development

Start the Python Workbench API in one terminal:

```bash
python3 -m workbench.server.server --host 127.0.0.1 --port 8080
```

Start Vite in another terminal:

```bash
cd workbench/ui
npm install
npm run dev
```

Vite proxies `/api` to the local Python server.

## Production/local-server build

Build the modern UI into the static directory served by `workbench.server.server`:

```bash
cd workbench/ui
npm install
npm run build
```

The build writes `index.html`, `app.js` and `styles.css` into `workbench/web/`,
which keeps the existing same-origin Python server contract intact.

## Architecture rules

- The browser collects user intent and renders API responses.
- Event lookup, preset resolution, validation and job-config generation remain in
  `workbench.core` and the local API.
- WRF/WPS/ERA5 domain rules must not be duplicated in React components.
- New workflow features should be implemented under `src/features/<feature>/` and
  shared code under `src/shared/`.
