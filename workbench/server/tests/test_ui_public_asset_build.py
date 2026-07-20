#!/usr/bin/env python3
"""Guard the Vite public-asset contract used by Workbench custom elements."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ASSETS = (
    "era5-credential-validation.js",
    "era5-download-control.js",
    "era5-cache-management.js",
    "real-pipeline-specification.js",
)


def main() -> int:
    index = (REPO_ROOT / "workbench" / "ui" / "index.html").read_text(encoding="utf-8")
    for filename in ASSETS:
        assert f'<script src="/{filename}"></script>' in index
        assert f"%BASE_URL%{filename}" not in index
        canonical = REPO_ROOT / "workbench" / "web" / filename
        public = REPO_ROOT / "workbench" / "ui" / "public" / filename
        assert canonical.is_file(), canonical
        assert public.is_file(), public
        assert public.read_text(encoding="utf-8") == canonical.read_text(encoding="utf-8"), (
            f"{public.relative_to(REPO_ROOT)} differs from canonical "
            f"{canonical.relative_to(REPO_ROOT)}; run ci/sync-workbench-public-assets.py"
        )
    print("Workbench Vite public assets match their canonical web sources")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
