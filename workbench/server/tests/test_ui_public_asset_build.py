#!/usr/bin/env python3
"""Guard the Vite public-asset contract used by ERA5 custom elements."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    index = (REPO_ROOT / "workbench" / "ui" / "index.html").read_text(encoding="utf-8")
    assert '<script src="/era5-download-control.js"></script>' in index
    assert '<script src="/era5-cache-management.js"></script>' in index
    assert '%BASE_URL%era5-download-control.js' not in index
    assert '%BASE_URL%era5-cache-management.js' not in index
    assert (REPO_ROOT / "workbench" / "ui" / "public" / "era5-download-control.js").is_file()
    assert (REPO_ROOT / "workbench" / "ui" / "public" / "era5-cache-management.js").is_file()
    print("Workbench Vite public-asset contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
