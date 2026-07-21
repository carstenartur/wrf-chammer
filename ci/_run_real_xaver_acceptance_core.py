#!/usr/bin/env python3
"""Run and verify one real Xaver job through the persistent product pipeline.

This command has no fixture or dry-run fallback. It requires a complete,
checksum-verified ERA5 plan, real WPS geography data and pinned local container
identities. Success means all eight persistent steps completed and the final
result index independently verifies every visualization product.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workbench.core.catalogue import build_job_config, load_catalogue
from workbench.domain_planner import plan_domain
from workbench.era5_service import Era5DataService
from workbench.pipeline_specification_service import PipelineSpecificationService
from workbench.simulation_store import SimulationStore
from workbench.simulation_worker import ExternalStepExecutor, SimulationWorker
from workbench.validate import validate_config

_SHA256_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_STEPS = (
    "input-data",
    "geogrid",
    "ungrib",
    "metgrid",
    "real",
    "wrf",
    "postprocessing",
    "result-indexing",
)
_RUNTIME_ENVIRONMENT = {
    "wps": ("WRF_CHAMMER_WPS_IMAGE_REFERENCE", "WRF_CHAMMER_WPS_IMAGE_IDENTITY"),
    "wrf": ("WRF_CHAMMER_WRF_IMAGE_REFERENCE", "WRF_CHAMMER_WRF_IMAGE_IDENTITY"),
    "postprocessing": (
        "WRF_CHAMMER_POSTPROCESSING_IMAGE_REFERENCE",
        "WRF_CHAMMER_POSTPROCESSING_IMAGE_IDENTITY",
    ),
}


class AcceptanceError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
        raise AcceptanceError(f"{label} is missing, empty or symbolic: {path}")
    return path


def require_directory(path: Path, label: str) -> Path:
    raw = Path(os.path.abspath(os.fspath(path)))
    if raw.is_symlink() or not raw.is_dir():
        raise AcceptanceError(f"{label} is unavailable or symbolic: {raw}")
    if not any(raw.iterdir()):
        raise AcceptanceError(f"{label} is empty: {raw}")
    return raw.resolve()


def current_revision(repo_root: Path) -> str:
    process = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    revision = process.stdout.strip()
    if process.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40,64}", revision):
        raise AcceptanceError("The repository revision cannot be resolved.")
    return revision


def require_clean_checkout(repo_root: Path) -> None:
    process = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        raise AcceptanceError("The repository status cannot be read.")
    if process.stdout.strip():
        raise AcceptanceError(
            "Real acceptance requires a clean checkout so provenance is reproducible."
        )


def inspect_runtime_images() -> dict[str, dict[str, str]]:
    runtime: dict[str, dict[str, str]] = {}
    for name, (reference_env, identity_env) in _RUNTIME_ENVIRONMENT.items():
        reference = os.environ.get(reference_env, "").strip()
        identity = os.environ.get(identity_env, "").strip()
        if not reference or not _SHA256_ID_RE.fullmatch(identity):
            raise AcceptanceError(
                f"Pinned {name} runtime is missing: set {reference_env} and {identity_env}."
            )
        process = subprocess.run(
            ["docker", "image", "inspect", reference],
            capture_output=True,
            text=True,
            check=False,
        )
        if process.returncode != 0:
            raise AcceptanceError(f"Runtime image is not available locally: {reference}")
        try:
            image = json.loads(process.stdout)[0]
        except (json.JSONDecodeError, IndexError, TypeError) as exc:
            raise AcceptanceError(f"Invalid Docker metadata for {reference}") from exc
        image_id = image.get("Id") if isinstance(image, dict) else None
        repo_digests = image.get("RepoDigests", []) if isinstance(image, dict) else []
        matched = image_id == identity or any(
            isinstance(value, str) and value.endswith(f"@{identity}")
            for value in repo_digests
        )
        if not matched:
            raise AcceptanceError(
                f"Local {name} image does not match pinned identity {identity}."
            )
        runtime[name] = {"reference": reference, "identity": identity}
    return runtime


def build_preview(args: argparse.Namespace, repo_root: Path) -> dict[str, Any]:
    plan = plan_domain(
        {
            "label": "Xaver North Sea real acceptance",
            "bounds": [args.west, args.south, args.east, args.north],
            "period": {"start": args.start, "end": args.end},
            "quality_profile": args.quality_profile,
        }
    )
    config = build_job_config(
        "xaver",
        mode="real-data",
        job_id=args.job_id,
        catalogue=load_catalogue(repo_root),
    )
    config["period"] = {
        "start": plan["period"]["start"],
        "end": plan["period"]["end"],
    }
    domain = plan["domain"]
    config["domain"] = {
        "label": domain["label"],
        "center_lat": domain["center_lat"],
        "center_lon": domain["center_lon"],
        "dx_km": domain["dx_km"],
        "dy_km": domain["dy_km"],
        "e_we": domain["e_we"],
        "e_sn": domain["e_sn"],
    }
    metadata = config.setdefault("metadata", {})
    metadata.update(
        {
            "domain_source": "real-xaver-acceptance",
            "domain_bounds": domain["bounds"],
            "quality_profile": plan["quality_profile"]["id"],
            "resource_estimate": plan["resources"],
            "acceptance_workflow": True,
        }
    )
    errors = validate_config(config)
    if errors:
        raise AcceptanceError("Invalid Xaver job configuration: " + "; ".join(errors))
    return {
        "ok": True,
        "valid": True,
        "errors": [],
        "warnings": plan["warnings"],
        "plan": plan,
        "config": config,
    }


def verify_real_plan(
    data_service: Era5DataService,
    plan_key: str,
    preview: dict[str, Any],
) -> dict[str, Any]:
    plan_directory = data_service.plan_directory(plan_key).resolve()
    require_directory(plan_directory, "ERA5 plan directory")
    plan = json.loads(
        require_regular_file(plan_directory / "era5-plan.json", "ERA5 plan").read_text(
            encoding="utf-8"
        )
    )
    checksums = json.loads(
        require_regular_file(plan_directory / "checksums.json", "ERA5 checksums").read_text(
            encoding="utf-8"
        )
    )
    provenance = json.loads(
        require_regular_file(plan_directory / "provenance.json", "ERA5 provenance").read_text(
            encoding="utf-8"
        )
    )
    if plan.get("plan_key") != plan_key:
        raise AcceptanceError("ERA5 plan key does not match its cache directory.")
    if plan.get("cache", {}).get("status") != "complete":
        raise AcceptanceError("ERA5 plan is not complete.")
    if provenance.get("artificial_weather_data") is not False:
        raise AcceptanceError("ERA5 provenance is not explicitly real-data provenance.")
    if provenance.get("plan_key") not in (None, plan_key):
        raise AcceptanceError("ERA5 provenance references another plan.")
    files = checksums.get("files") if isinstance(checksums, dict) else None
    if not isinstance(files, dict) or not files:
        raise AcceptanceError("ERA5 checksums contain no files.")
    for relative, metadata in sorted(files.items()):
        if not isinstance(metadata, dict):
            raise AcceptanceError(f"Invalid checksum metadata: {relative}")
        digest = metadata.get("sha256")
        size = metadata.get("size_bytes")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise AcceptanceError(f"Invalid ERA5 checksum: {relative}")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise AcceptanceError(f"Invalid ERA5 file size: {relative}")
        target = (plan_directory / relative).resolve()
        if plan_directory not in target.parents:
            raise AcceptanceError(f"ERA5 file escaped the plan directory: {relative}")
        require_regular_file(target, f"ERA5 input {relative}")
        if target.stat().st_size != size or sha256_file(target) != digest:
            raise AcceptanceError(f"ERA5 file no longer matches its checksum: {relative}")
    requested = preview["config"]
    if plan.get("period") != requested.get("period"):
        raise AcceptanceError("ERA5 plan period does not match the Xaver preview.")
    return {
        "plan": plan,
        "checksums": checksums,
        "provenance": provenance,
        "plan_directory": plan_directory,
    }


def verify_result_index(
    repo_root: Path,
    job: dict[str, Any],
    specification: dict[str, Any],
) -> dict[str, Any]:
    artifacts = [
        artifact
        for artifact in job.get("artifacts", [])
        if artifact.get("kind") == "result-index"
    ]
    if len(artifacts) != 1:
        raise AcceptanceError("Successful job must expose exactly one result index.")
    run_root = repo_root / "workbench-runs" / "simulations" / job["id"]
    index_path = (run_root / artifacts[0]["relative_path"]).resolve()
    if run_root.resolve() not in index_path.parents:
        raise AcceptanceError("Result index escaped the managed job directory.")
    index = json.loads(require_regular_file(index_path, "Result index").read_text(encoding="utf-8"))
    identity = specification["identity"]
    if index.get("specification_key") != specification.get("specification_key"):
        raise AcceptanceError("Result index references another specification.")
    if index.get("source_revision") != identity.get("source", {}).get("repository_revision"):
        raise AcceptanceError("Result index source revision is missing or inconsistent.")
    if index.get("era5_plan_key") != identity.get("era5_input", {}).get("plan_key"):
        raise AcceptanceError("Result index references another ERA5 plan.")
    if index.get("artificial_weather_data") is not False:
        raise AcceptanceError("Result index does not preserve the real-data marker.")
    provenance = index.get("visualization_provenance")
    if not isinstance(provenance, dict) or provenance.get("mode") != "wrf":
        raise AcceptanceError("Visualization provenance is not real WRF input mode.")
    if not provenance.get("wrfout_files"):
        raise AcceptanceError("Visualization provenance has no WRF output files.")
    products = index.get("products")
    if not isinstance(products, list) or not products:
        raise AcceptanceError("Result index has no products.")
    verified_products = []
    for product in products:
        relative = product.get("path")
        digest = product.get("sha256")
        size = product.get("size_bytes")
        if not isinstance(relative, str) or not isinstance(digest, str):
            raise AcceptanceError("Result product metadata is invalid.")
        target = (run_root / relative).resolve()
        if run_root.resolve() not in target.parents:
            raise AcceptanceError(f"Result product escaped the job directory: {relative}")
        require_regular_file(target, f"Result product {relative}")
        if target.stat().st_size != size or sha256_file(target) != digest:
            raise AcceptanceError(f"Result product checksum mismatch: {relative}")
        verified_products.append({"path": relative, "sha256": digest, "size_bytes": size})
    return {
        "index_path": index_path.relative_to(repo_root).as_posix(),
        "wrfout_files": provenance["wrfout_files"],
        "products": verified_products,
    }


def write_report(
    path: Path,
    *,
    job: dict[str, Any],
    specification: dict[str, Any],
    preview: dict[str, Any],
    runtime: dict[str, dict[str, str]],
    result: dict[str, Any],
) -> None:
    resources = job.get("resource_measurements", [])
    report = {
        "version": 1,
        "accepted": True,
        "created_at": utc_now(),
        "job_id": job["id"],
        "status": job["status"],
        "specification_key": specification["specification_key"],
        "source_revision": specification["identity"]["source"]["repository_revision"],
        "period": preview["config"]["period"],
        "domain": preview["config"]["domain"],
        "era5_plan_key": specification["identity"]["era5_input"]["plan_key"],
        "runtime": runtime,
        "steps": [
            {
                "id": step["id"],
                "status": step["status"],
                "attempt": step["attempt"],
                "started_at": step["started_at"],
                "finished_at": step["finished_at"],
                "progress": step["progress"],
            }
            for step in job["steps"]
        ],
        "resource_measurements": resources,
        "artifact_count": len(job.get("artifacts", [])),
        "result": result,
    }
    atomic_json(path, report)
    markdown = [
        "# Real Xaver persistent acceptance",
        "",
        f"- Accepted: **yes**",
        f"- Job: `{job['id']}`",
        f"- Specification: `{specification['specification_key']}`",
        f"- Source revision: `{report['source_revision']}`",
        f"- ERA5 plan: `{report['era5_plan_key']}`",
        f"- Period: `{report['period']['start']}` to `{report['period']['end']}`",
        f"- Indexed products: **{len(result['products'])}**",
        f"- WRF output files: **{len(result['wrfout_files'])}**",
        "",
        "## Steps",
        "",
        "| Step | Status | Attempt |",
        "|---|---:|---:|",
    ]
    markdown.extend(
        f"| `{step['id']}` | {step['status']} | {step['attempt']} |"
        for step in job["steps"]
    )
    path.with_suffix(".md").write_text("\n".join(markdown) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a real persistent Xaver acceptance job")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--plan-key", required=True)
    parser.add_argument("--geog-root", type=Path, required=True)
    parser.add_argument("--job-id", default="xaver-real-acceptance")
    parser.add_argument("--start", default="2013-12-05T12:00:00Z")
    parser.add_argument("--end", default="2013-12-06T06:00:00Z")
    parser.add_argument("--west", type=float, default=2.0)
    parser.add_argument("--south", type=float, default=51.0)
    parser.add_argument("--east", type=float, default=13.0)
    parser.add_argument("--north", type=float, default=58.0)
    parser.add_argument("--quality-profile", default="balanced")
    parser.add_argument("--pipeline-profile", default="small-real-data-demo")
    parser.add_argument("--report", type=Path, default=Path("workbench-runs/acceptance/xaver/report.json"))
    parser.add_argument("--allow-dirty-checkout", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    try:
        if not args.allow_dirty_checkout:
            require_clean_checkout(repo_root)
        revision = current_revision(repo_root)
        geog_root = require_directory(args.geog_root, "WPS geography directory")
        os.environ["WRF_CHAMMER_WPS_GEOG_ROOT"] = str(geog_root)
        os.environ["WRF_CHAMMER_ERA5_CACHE_ROOT"] = str(args.cache_root.resolve())
        runtime = inspect_runtime_images()
        preview = build_preview(args, repo_root)
        data_service = Era5DataService(repo_root, args.cache_root.resolve())
        real_plan = verify_real_plan(data_service, args.plan_key, preview)
        specification_service = PipelineSpecificationService(repo_root, data_service)
        specification = specification_service.create(
            {
                "plan_key": args.plan_key,
                "profile": args.pipeline_profile,
                "runtime": runtime,
                "source_revision": revision,
            },
            preview,
        )
        specification_directory = specification_service.root / specification["specification_key"]
        require_regular_file(
            specification_directory / "run-specification.json",
            "Canonical immutable run specification",
        )
        report_path = args.report if args.report.is_absolute() else repo_root / args.report
        report_path = report_path.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        store = SimulationStore(
            repo_root,
            specification_service,
            database_path=report_path.parent / "acceptance.sqlite3",
        )
        job = store.create_job(specification["specification_key"])
        job = store.enqueue_job(job["id"])
        worker = SimulationWorker(
            repo_root,
            store,
            data_service,
            specification_service,
            worker_id="real-xaver-acceptance",
            executor=ExternalStepExecutor(
                repo_root / "workbench" / "pipeline_container_executor.py",
                poll_seconds=1.0,
                cancel_grace_seconds=30.0,
            ),
            poll_seconds=0.5,
        )
        worker.run(once=True)
        job = store.get_job(job["id"])
        if job["status"] != "SUCCEEDED":
            error = job.get("error") or {}
            raise AcceptanceError(
                f"Xaver job ended as {job['status']}: {error.get('code')} {error.get('message')}"
            )
        step_ids = tuple(step["id"] for step in job["steps"])
        if step_ids != _EXPECTED_STEPS:
            raise AcceptanceError(f"Unexpected step order: {step_ids}")
        if any(step["status"] != "SUCCEEDED" for step in job["steps"]):
            raise AcceptanceError("At least one persistent step did not succeed.")
        result = verify_result_index(repo_root, job, specification)
        write_report(
            report_path,
            job=job,
            specification=specification,
            preview=preview,
            runtime=runtime,
            result=result,
        )
        print(f"Real Xaver acceptance succeeded: {report_path}")
        print(f"ERA5 files verified: {len(real_plan['checksums']['files'])}")
        print(f"Indexed products verified: {len(result['products'])}")
        return 0
    except Exception as exc:
        print(f"Real Xaver acceptance failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
