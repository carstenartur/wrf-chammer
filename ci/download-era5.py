#!/usr/bin/env python3

import argparse
import hashlib
import json
from pathlib import Path

def parse_args():
    parser = argparse.ArgumentParser(description="Download ERA5 requests described in a JSON file.")
    parser.add_argument("--config", required=True, help="Path to the JSON download configuration.")
    parser.add_argument("--output-dir", required=True, help="Directory where GRIB files will be stored.")
    parser.add_argument(
        "--manifest",
        help="Optional output manifest path. Defaults to <output-dir>/era5-manifest.json.",
    )
    return parser.parse_args()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_config(config_path):
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

    kwargs = {}
    return cdsapi.Client(**kwargs)


def main():
    args = parse_args()
    config_path = Path(args.config).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    config, config_text = load_config(config_path)
    manifest_path = Path(args.manifest).resolve() if args.manifest else output_dir / "era5-manifest.json"

    client = None
    outputs = []

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

        target_path = output_dir / target_name
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target_path.with_name(target_path.name + ".part")
        cached = target_path.exists() and target_path.stat().st_size > 0

        if not cached:
            if client is None:
                client = build_client()
            if temp_path.exists():
                temp_path.unlink()
            client.retrieve(dataset, request_body, str(temp_path))
            temp_path.replace(target_path)

        outputs.append(
            {
                "name": name,
                "dataset": dataset,
                "target": str(target_path),
                "ungrib_prefix": prefix,
                "cached": cached,
                "size_bytes": target_path.stat().st_size,
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
