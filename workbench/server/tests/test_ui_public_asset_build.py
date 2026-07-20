#!/usr/bin/env python3
"""Guard the Vite public-asset contract used by ERA5 custom elements."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    index = (REPO_ROOT / "workbench" / "ui" / "index.html").read_text(encoding="utf-8")
    for filename in (
        "era5-credential-validation.js",
        "era5-download-control.js",
        "era5-cache-management.js",
    ):
        assert f'<script src="/{filename}"></script>' in index
        assert f'%BASE_URL%{filename}' not in index
        assert (REPO_ROOT / "workbench" / "ui" / "public" / filename).is_file()
    print("Workbench Vite public-asset contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
