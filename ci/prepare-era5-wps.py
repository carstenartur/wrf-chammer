#!/usr/bin/env python3

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Run WPS geogrid/ungrib/metgrid for downloaded ERA5 files.")
    parser.add_argument("--manifest", required=True, help="Path to the ERA5 manifest JSON file.")
    parser.add_argument("--workdir", required=True, help="WPS working directory containing namelist.wps.")
    parser.add_argument("--wps-dir", default="/opt/wps", help="Directory containing WPS executables.")
    parser.add_argument(
        "--wps-assets-dir",
        default="/opt/wps-assets",
        help="Directory containing WPS tables and Variable_Tables.",
    )
    parser.add_argument(
        "--vtable",
        default="/opt/wps-assets/Variable_Tables/Vtable.ERA-interim.pl",
        help="Vtable to use for ERA5 ungrib runs.",
    )
    parser.add_argument("--run-geogrid", action="store_true", help="Run geogrid.exe before ungrib.")
    parser.add_argument("--skip-metgrid", action="store_true", help="Skip metgrid.exe.")
    return parser.parse_args()


def replace_assignment(text, key, value):
    pattern = re.compile(rf"(^\s*{re.escape(key)}\s*=).*$", flags=re.MULTILINE)
    replacement = rf"\1 {value}"
    if not pattern.search(text):
        raise SystemExit(f"Could not find '{key}' assignment in namelist.wps.")
    return pattern.sub(replacement, text)


def symlink_force(src, dest):
    if dest.is_symlink() or dest.exists():
        dest.unlink()
    dest.symlink_to(src)


def run_checked(command, cwd):
    subprocess.run(command, cwd=cwd, check=True)


def clear_matches(workdir, pattern):
    for path in workdir.glob(pattern):
        path.unlink()


def main():
    args = parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    outputs = manifest.get("outputs", [])
    if not outputs:
        raise SystemExit("Manifest contains no outputs.")

    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    namelist_path = workdir / "namelist.wps"
    if not namelist_path.exists():
        raise SystemExit(f"Missing {namelist_path}")

    wps_dir = Path(args.wps_dir).resolve()
    wps_assets_dir = Path(args.wps_assets_dir).resolve()
    vtable_path = Path(args.vtable).resolve()

    for exe_name in ("geogrid.exe", "ungrib.exe", "metgrid.exe"):
        symlink_force(wps_dir / exe_name, workdir / exe_name)

    shutil.copyfile(wps_assets_dir / "run" / "GEOGRID.TBL.ARW", workdir / "GEOGRID.TBL")
    shutil.copyfile(wps_assets_dir / "run" / "METGRID.TBL.ARW", workdir / "METGRID.TBL")

    namelist_text = namelist_path.read_text(encoding="utf-8")
    prefixes = []

    if args.run_geogrid:
        run_checked(["./geogrid.exe"], cwd=workdir)
        if not list(workdir.glob("geo_em.d*.nc")):
            raise SystemExit("geogrid.exe did not produce geo_em.d*.nc files.")

    for output in outputs:
        prefix = output.get("ungrib_prefix", "FILE")
        target = Path(output["target"]).resolve()
        if not target.exists() or target.stat().st_size <= 0:
            raise SystemExit(f"Missing downloaded ERA5 file: {target}")

        clear_matches(workdir, "GRIBFILE.*")
        symlink_force(vtable_path, workdir / "Vtable")
        symlink_force(target, workdir / "GRIBFILE.AAA")

        namelist_text = replace_assignment(namelist_text, "prefix", f"'{prefix}',")
        namelist_path.write_text(namelist_text, encoding="utf-8")
        run_checked(["./ungrib.exe"], cwd=workdir)

        if not list(workdir.glob(f"{prefix}:*")):
            raise SystemExit(f"ungrib.exe did not produce {prefix}:* files.")
        if prefix not in prefixes:
            prefixes.append(prefix)

    if not args.skip_metgrid:
        fg_name_value = ",".join(f"'{prefix}'" for prefix in prefixes) + ","
        namelist_text = replace_assignment(namelist_text, "fg_name", fg_name_value)
        namelist_path.write_text(namelist_text, encoding="utf-8")
        run_checked(["./metgrid.exe"], cwd=workdir)

        if not list(workdir.glob("met_em.d*")):
            raise SystemExit("metgrid.exe did not produce met_em.d* files.")


if __name__ == "__main__":
    main()
