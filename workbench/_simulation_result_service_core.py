#!/usr/bin/env python3
"""Secure access to checksum-indexed visualization products of one simulation."""

from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_JOB_ID_RE = re.compile(r"^sim-[0-9a-f]{12}-[0-9a-f]{12}$")
_RESULT_INDEX_PATH = "results/index.json"


class SimulationResultError(RuntimeError):
    """A classified integrated-result-viewer error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class IndexedProduct:
    request_path: str
    relative_path: str
    sha256: str
    size_bytes: int
    content_type: str
    body: bytes


class SimulationResultService:
    """Resolve only verified products below one managed simulation directory."""

    def __init__(self, repo_root: Path, store: Any):
        self.repo_root = repo_root.resolve()
        self.store = store
        self.runs_root = (self.repo_root / "workbench-runs" / "simulations").resolve()
        self.viewer_path = self.repo_root / "visualization" / "web" / "index.html"

    def viewer_html(self, job_id: str) -> bytes:
        manifest = self.manifest(job_id)
        if self.viewer_path.is_symlink() or not self.viewer_path.is_file():
            raise SimulationResultError(
                "viewer_unavailable", "The integrated WRF result viewer is unavailable."
            )
        html = self.viewer_path.read_text(encoding="utf-8")
        escaped_job = self._html_escape(manifest["job_id"])
        escaped_specification = self._html_escape(manifest["specification_key"])
        base = f'<base href="/jobs/{escaped_job}/results/">'
        notice = (
            '<div id="workbench-model-notice" role="note" '
            'style="padding:8px 20px;background:#3a2f16;color:#ffe8a3;'
            'font-size:.8rem;border-bottom:1px solid #66551f">'
            'Model result, not an observation. '
            f'Job {escaped_job} · {escaped_specification}'
            '</div>'
        )
        if "<head>" not in html or "<body>" not in html:
            raise SimulationResultError(
                "viewer_unavailable", "The integrated WRF result viewer is invalid."
            )
        html = html.replace("<head>", f"<head>\n{base}", 1)
        html = html.replace("<body>", f"<body>\n{notice}", 1)
        return html.encode("utf-8")

    def manifest(self, job_id: str) -> dict[str, Any]:
        job, _run_root, index, products = self._result_context(job_id)
        metadata_product = products.get("metadata.json")
        if metadata_product is None:
            raise SimulationResultError(
                "result_integrity_error", "The indexed visualization metadata is missing."
            )
        metadata = self._decode_json_bytes(
            metadata_product.body, "Visualization metadata"
        )
        metadata_provenance = metadata.get("provenance")
        index_provenance = index["visualization_provenance"]
        if (
            not isinstance(metadata_provenance, dict)
            or metadata_provenance.get("mode") != "wrf"
            or not isinstance(metadata_provenance.get("wrfout_files"), list)
            or not metadata_provenance["wrfout_files"]
            or metadata_provenance.get("wrfout_files")
            != index_provenance.get("wrfout_files")
        ):
            raise SimulationResultError(
                "result_integrity_error",
                "Visualization metadata is not tied to the indexed WRF outputs.",
            )
        metadata_job = metadata.get("job_id")
        if metadata_job is not None and metadata_job != job_id:
            raise SimulationResultError(
                "result_integrity_error",
                "Visualization metadata references another simulation job.",
            )
        ordered_products = [products[key] for key in sorted(products)]
        return {
            "job_id": job_id,
            "status": job["status"],
            "specification_key": job["specification_key"],
            "viewer_url": f"/jobs/{job_id}/results/",
            "source_revision": index.get("source_revision"),
            "era5_plan_key": index.get("era5_plan_key"),
            "artificial_weather_data": False,
            "provenance": index_provenance,
            "metadata": metadata,
            "products": [
                {
                    "path": product.request_path,
                    "sha256": product.sha256,
                    "size_bytes": product.size_bytes,
                    "content_type": product.content_type,
                    "url": f"/jobs/{job_id}/results/{product.request_path}",
                }
                for product in ordered_products
            ],
        }

    def product(self, job_id: str, request_path: str) -> IndexedProduct:
        product, _body = self.read_product(job_id, request_path)
        return product

    def read_product(self, job_id: str, request_path: str) -> tuple[IndexedProduct, bytes]:
        """Return bytes already read from a fixed-root filesystem allowlist."""

        _job, _run_root, _index, products = self._result_context(job_id)
        normalized = self._safe_request_path(request_path)
        product = products.get(normalized)
        if product is None:
            raise SimulationResultError(
                "result_not_found", "The requested result product is not indexed."
            )
        return product, product.body

    def _result_context(
        self, job_id: str
    ) -> tuple[dict[str, Any], Path, dict[str, Any], dict[str, IndexedProduct]]:
        job_id = self._validate_job_id(job_id)
        job = self.store.get_job(job_id)
        if job.get("status") != "SUCCEEDED":
            raise SimulationResultError(
                "results_not_ready",
                "Simulation results are available only after the job succeeds.",
            )
        run_root = self._run_root(job_id)
        index_path, index_artifact = self._result_index_path(run_root, job)
        index = self._decode_json_bytes(
            self._read_verified_file(
                index_path,
                expected_sha256=index_artifact["sha256"],
                expected_size=index_artifact["size_bytes"],
                label="Result index",
            ),
            "Result index",
        )
        if index.get("specification_key") != job.get("specification_key"):
            raise SimulationResultError(
                "result_integrity_error",
                "The result index references another immutable specification.",
            )
        if index.get("artificial_weather_data") is not False:
            raise SimulationResultError(
                "result_integrity_error",
                "The result index does not prove real-data processing.",
            )
        provenance = index.get("visualization_provenance")
        if (
            not isinstance(provenance, dict)
            or provenance.get("mode") != "wrf"
            or not isinstance(provenance.get("wrfout_files"), list)
            or not provenance["wrfout_files"]
            or not all(
                isinstance(value, str) and value for value in provenance["wrfout_files"]
            )
        ):
            raise SimulationResultError(
                "result_integrity_error",
                "Visualization provenance is not tied to WRF output files.",
            )
        available = self._visualization_files(run_root)
        return job, run_root, index, self._indexed_products(index, available)

    def _indexed_products(
        self, index: dict[str, Any], available: dict[str, Path]
    ) -> dict[str, IndexedProduct]:
        raw_products = index.get("products")
        if not isinstance(raw_products, list) or not raw_products:
            raise SimulationResultError(
                "result_integrity_error", "The result index has no products."
            )
        products: dict[str, IndexedProduct] = {}
        for raw in raw_products:
            if not isinstance(raw, dict):
                raise SimulationResultError(
                    "result_integrity_error", "Result product metadata is invalid."
                )
            relative = raw.get("path")
            digest = raw.get("sha256")
            size = raw.get("size_bytes")
            if not isinstance(relative, str):
                raise SimulationResultError(
                    "result_integrity_error", "Result product path is invalid."
                )
            safe_relative = self._safe_relative_path(relative)
            if not safe_relative.startswith("visualizations/"):
                continue
            if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
                raise SimulationResultError(
                    "result_integrity_error", "Result product SHA-256 is invalid."
                )
            if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
                raise SimulationResultError(
                    "result_integrity_error", "Result product size is invalid."
                )
            path = available.get(safe_relative)
            if path is None:
                raise SimulationResultError(
                    "result_integrity_error", "An indexed result product is missing."
                )
            request_path = safe_relative.removeprefix("visualizations/")
            if not request_path or request_path in products:
                raise SimulationResultError(
                    "result_integrity_error",
                    "Result index contains an invalid or duplicate product.",
                )
            body = self._read_verified_file(
                path,
                expected_sha256=digest,
                expected_size=size,
                label="Indexed result product",
            )
            products[request_path] = IndexedProduct(
                request_path=request_path,
                relative_path=safe_relative,
                sha256=digest,
                size_bytes=size,
                content_type=mimetypes.guess_type(request_path)[0]
                or "application/octet-stream",
                body=body,
            )
        if not products:
            raise SimulationResultError(
                "result_integrity_error",
                "The result index contains no visualization products.",
            )
        return products

    def _visualization_files(self, run_root: Path) -> dict[str, Path]:
        visualization_root = run_root / "visualizations"
        if visualization_root.is_symlink() or not visualization_root.is_dir():
            raise SimulationResultError(
                "result_integrity_error", "Visualization directory is missing or unsafe."
            )
        available: dict[str, Path] = {}
        for root_text, directories, filenames in os.walk(
            visualization_root, topdown=True, followlinks=False
        ):
            root = Path(root_text)
            for directory in list(directories):
                candidate = root / directory
                if candidate.is_symlink():
                    raise SimulationResultError(
                        "result_integrity_error",
                        "Visualization directory contains a symbolic link.",
                    )
            for filename in filenames:
                candidate = root / filename
                if candidate.is_symlink() or not candidate.is_file():
                    raise SimulationResultError(
                        "result_integrity_error",
                        "Visualization directory contains an unsafe product.",
                    )
                relative = candidate.relative_to(run_root).as_posix()
                available[relative] = candidate
        return available

    def _result_index_path(
        self, run_root: Path, job: dict[str, Any]
    ) -> tuple[Path, dict[str, Any]]:
        artifacts = [
            artifact
            for artifact in job.get("artifacts", [])
            if isinstance(artifact, dict) and artifact.get("kind") == "result-index"
        ]
        if len(artifacts) != 1:
            raise SimulationResultError(
                "result_integrity_error", "The job must expose exactly one result index."
            )
        artifact = artifacts[0]
        relative = artifact.get("relative_path")
        digest = artifact.get("sha256")
        size = artifact.get("size_bytes")
        if relative != _RESULT_INDEX_PATH:
            raise SimulationResultError(
                "result_integrity_error",
                "The result index is not at the canonical managed path.",
            )
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise SimulationResultError(
                "result_integrity_error", "Result index SHA-256 is invalid."
            )
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise SimulationResultError(
                "result_integrity_error", "Result index size is invalid."
            )
        results_root = run_root / "results"
        path = results_root / "index.json"
        if (
            results_root.is_symlink()
            or not results_root.is_dir()
            or path.is_symlink()
            or not path.is_file()
        ):
            raise SimulationResultError(
                "result_integrity_error", "Result index is missing or unsafe."
            )
        return path, artifact

    def _run_root(self, job_id: str) -> Path:
        if self.runs_root.is_symlink() or not self.runs_root.is_dir():
            raise SimulationResultError(
                "result_integrity_error", "Simulation run storage is unavailable."
            )
        for candidate in self.runs_root.iterdir():
            if candidate.name != job_id:
                continue
            if candidate.is_symlink() or not candidate.is_dir():
                raise SimulationResultError(
                    "result_integrity_error", "Simulation run directory is unsafe."
                )
            resolved = candidate.resolve()
            if resolved.parent != self.runs_root:
                raise SimulationResultError(
                    "result_integrity_error", "Simulation run directory escaped storage."
                )
            return resolved
        raise SimulationResultError(
            "result_not_found", "Simulation result directory was not found."
        )

    @staticmethod
    def _read_verified_file(
        path: Path, *, expected_sha256: str, expected_size: int, label: str
    ) -> bytes:
        if path.is_symlink() or not path.is_file():
            raise SimulationResultError(
                "result_integrity_error", f"{label} is missing or unsafe."
            )
        try:
            body = path.read_bytes()
        except OSError as exc:
            raise SimulationResultError(
                "result_integrity_error", f"{label} cannot be read."
            ) from exc
        if len(body) != expected_size:
            raise SimulationResultError(
                "result_integrity_error", f"{label} size changed."
            )
        digest = hashlib.sha256(body).hexdigest()
        if not hmac.compare_digest(digest, expected_sha256):
            raise SimulationResultError(
                "result_integrity_error", f"{label} checksum changed."
            )
        return body

    @staticmethod
    def _decode_json_bytes(body: bytes, label: str) -> dict[str, Any]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SimulationResultError(
                "result_integrity_error", f"{label} is not valid JSON."
            ) from exc
        if not isinstance(payload, dict):
            raise SimulationResultError(
                "result_integrity_error", f"{label} must be a JSON object."
            )
        return payload

    @staticmethod
    def _validate_job_id(job_id: str) -> str:
        if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
            raise SimulationResultError(
                "result_not_found", "Simulation job was not found."
            )
        return job_id

    @staticmethod
    def _safe_request_path(value: str) -> str:
        return SimulationResultService._safe_relative_path(value)

    @staticmethod
    def _safe_relative_path(value: str) -> str:
        if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
            raise SimulationResultError("result_not_found", "Result path is invalid.")
        pure = PurePosixPath(value)
        parts = value.split("/")
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in parts):
            raise SimulationResultError("result_not_found", "Result path is invalid.")
        return "/".join(parts)

    @staticmethod
    def _html_escape(value: Any) -> str:
        return (
            str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
        )


__all__ = ["IndexedProduct", "SimulationResultError", "SimulationResultService"]
