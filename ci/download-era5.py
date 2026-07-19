#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def parse_args():
    parser = argparse.ArgumentParser(description="Download ERA5 requests described in a JSON file.")
    parser.add_argument("--config", required=True, help="Path to the JSON download configuration.")
    parser.add_argument("--output-dir", required=True, help="Directory where GRIB files will be stored.")
    parser.add_argument(
        "--manifest",
        help="Optional output manifest path. Defaults to <output-dir>/era5-manifest.json.",
    )
    return parser.parse_args()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_config(config_path: Path) -> tuple[dict[str, Any], str]:
    config_text = config_path.read_text(encoding="utf-8")
    config = json.loads(config_text)
    requests = config.get("requests")
    if not isinstance(requests, dict) or not requests:
        raise SystemExit("Configuration must contain a non-empty 'requests' object.")
    return config, config_text


def build_client():
    try:
        import cdsapi
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "cdsapi is required for uncached ERA5 downloads. "
            "Install it or pre-populate target files for offline runs."
        ) from exc

    return cdsapi.Client()


def resolve_target(output_dir: Path, target_name: Any) -> Path:
    if not isinstance(target_name, str) or not target_name.strip():
        raise SystemExit("ERA5 request target must be a non-empty relative path.")

    relative = Path(target_name)
    if relative.is_absolute() or ".." in relative.parts:
        raise SystemExit(f"ERA5 request target must stay inside the output directory: {target_name!r}")

    target = (output_dir / relative).resolve()
    if target != output_dir and output_dir not in target.parents:
        raise SystemExit(f"ERA5 request target escapes the output directory: {target_name!r}")
    return target


def main():
    args = parse_args()
    config_path = Path(args.config).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    config, config_text = load_config(config_path)
    manifest_path = Path(args.manifest).resolve() if args.manifest else output_dir / "era5-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    client = None
    outputs = []
    claimed_targets: set[Path] = set()

    for name, request_def in config["requests"].items():
        if not isinstance(request_def, dict):
            raise SystemExit(f"Request '{name}' must be an object.")

        dataset = request_def.get("dataset")
        request_body = request_def.get("request")
        target_name = request_def.get("target")
        prefix = request_def.get("ungrib_prefix", "FILE")

        if not dataset or not isinstance(request_body, dict) or not target_name:
            raise SystemExit(
                f"Request '{name}' must define 'dataset', 'request', and 'target'."
            )
        if not isinstance(prefix, str) or not prefix.strip():
            raise SystemExit(f"Request '{name}' must define a non-empty ungrib_prefix.")

        target_path = resolve_target(output_dir, target_name)
        if target_path in claimed_targets:
            raise SystemExit(f"Multiple ERA5 requests target the same file: {target_name!r}")
        claimed_targets.add(target_path)

        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target_path.with_name(target_path.name + ".part")
        cached = target_path.is_file() and target_path.stat().st_size > 0

        if not cached:
            if client is None:
                client = build_client()
            if temp_path.exists() or temp_path.is_symlink():
                temp_path.unlink()
            client.retrieve(dataset, request_body, str(temp_path))
            if not temp_path.is_file() or temp_path.stat().st_size <= 0:
                raise SystemExit(f"CDS download for request '{name}' produced an empty file.")
            temp_path.replace(target_path)

        outputs.append(
            {
                "name": name,
                "dataset": dataset,
                "target": str(target_path),
                "ungrib_prefix": prefix,
                "cached": cached,
                "size_bytes": target_path.stat().st_size,
                "sha256": sha256_file(target_path),
                "request_sha256": sha256_text(json.dumps(request_body, sort_keys=True)),
            }
        )

    manifest = {
        "config": str(config_path),
        "config_sha256": sha256_text(config_text),
        "outputs": outputs,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote ERA5 manifest to {manifest_path}")


if __name__ == "__main__":
    main()
