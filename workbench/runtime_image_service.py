#!/usr/bin/env python3
"""Validate, pull, activate, and inspect digest-pinned runtime image releases."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

FORMAT_NAME = "wrf-chammer-runtime-release"
FORMAT_VERSION = 1
ACTIVATION_NAME = "wrf-chammer-runtime-activation"
ACTIVATION_VERSION = 1
COMPONENTS = ("wps", "wrf", "postprocessing")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^[0-9a-f]{40,64}$")
_RELEASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")


class RuntimeImageError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def activation_path(repo_root: Path) -> Path:
    configured = os.environ.get("WRF_CHAMMER_RUNTIME_ACTIVATION")
    if configured:
        candidate = Path(configured).expanduser()
        return (candidate if candidate.is_absolute() else repo_root / candidate).resolve()
    return (repo_root / "workbench-runs" / ".runtime" / "runtime-images.json").resolve()


def default_manifest_path(repo_root: Path) -> Path:
    configured = os.environ.get("WRF_CHAMMER_RELEASE_MANIFEST")
    if configured:
        candidate = Path(configured).expanduser()
        return (candidate if candidate.is_absolute() else repo_root / candidate).resolve()
    return (repo_root / "runtime" / "release-manifest.json").resolve()


def _read_json(path: Path, *, code: str, message: str) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("not a regular file")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeImageError(code, message) from exc
    if not isinstance(value, dict):
        raise RuntimeImageError(code, message)
    return value


def _validate_reference(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise RuntimeImageError("invalid_runtime_manifest", f"{field} must be a string.")
    reference = value.strip()
    if (
        not reference
        or any(character.isspace() for character in reference)
        or reference.startswith("-")
        or "@" in reference
    ):
        raise RuntimeImageError(
            "invalid_runtime_manifest",
            f"{field} must be a tag or repository reference without a digest.",
        )
    return reference


def load_release_manifest(path: Path) -> dict[str, Any]:
    resolved_path = path.resolve()
    payload = _read_json(
        resolved_path,
        code="runtime_manifest_unavailable",
        message="A runtime release manifest is not available.",
    )
    if payload.get("format") != {"name": FORMAT_NAME, "version": FORMAT_VERSION}:
        raise RuntimeImageError(
            "invalid_runtime_manifest", "Unsupported runtime release manifest format."
        )
    release = payload.get("release")
    source_revision = payload.get("product_source_revision")
    if not isinstance(release, str) or not _RELEASE_RE.fullmatch(release):
        raise RuntimeImageError(
            "invalid_runtime_manifest", "Runtime release identifier is invalid."
        )
    if not isinstance(source_revision, str) or not _REVISION_RE.fullmatch(source_revision):
        raise RuntimeImageError(
            "invalid_runtime_manifest", "Product source revision must be a full Git SHA."
        )
    raw_images = payload.get("images")
    if not isinstance(raw_images, dict) or set(raw_images) != set(COMPONENTS):
        raise RuntimeImageError(
            "invalid_runtime_manifest",
            "Runtime manifest must define exactly WPS, WRF, and postprocessing images.",
        )
    images: dict[str, dict[str, str]] = {}
    for component in COMPONENTS:
        entry = raw_images.get(component)
        if not isinstance(entry, dict):
            raise RuntimeImageError(
                "invalid_runtime_manifest", f"Runtime image {component} is invalid."
            )
        reference = _validate_reference(
            entry.get("reference"), f"images.{component}.reference"
        )
        digest = entry.get("digest")
        if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
            raise RuntimeImageError(
                "invalid_runtime_manifest",
                f"images.{component}.digest must be a lowercase SHA-256 digest.",
            )
        images[component] = {
            "reference": reference,
            "digest": digest,
            "selector": f"{reference}@{digest}",
        }
    normalized = {
        "format": {"name": FORMAT_NAME, "version": FORMAT_VERSION},
        "release": release,
        "product_source_revision": source_revision.lower(),
        "images": images,
    }
    return {
        **normalized,
        "manifest_sha256": _sha256_json(normalized),
        "manifest_path": str(resolved_path),
    }


def _container_engine(configured: str | None = None) -> str:
    name = configured or os.environ.get("WRF_CHAMMER_CONTAINER_ENGINE", "docker")
    executable = shutil.which(name)
    if not executable:
        raise RuntimeImageError(
            "container_engine_unavailable", f"Container engine {name!r} is not available."
        )
    return executable


def _current_source_revision(repo_root: Path) -> str | None:
    configured = os.environ.get("WRF_CHAMMER_SOURCE_REVISION")
    if configured and _REVISION_RE.fullmatch(configured.strip().lower()):
        return configured.strip().lower()
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        stdin=subprocess.DEVNULL,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
    )
    revision = completed.stdout.strip().lower() if completed.returncode == 0 else ""
    return revision if _REVISION_RE.fullmatch(revision) else None


def _inspect_image(engine: str, selector: str, digest: str) -> dict[str, Any]:
    completed = subprocess.run(
        [engine, "image", "inspect", selector],
        stdin=subprocess.DEVNULL,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeImageError(
            "runtime_image_unavailable", f"Runtime image {selector} is not available locally."
        )
    try:
        value = json.loads(completed.stdout)
        image = value[0]
    except (json.JSONDecodeError, IndexError, TypeError) as exc:
        raise RuntimeImageError(
            "runtime_image_inspection_failed", "Container image inspection returned invalid JSON."
        ) from exc
    if not isinstance(image, dict):
        raise RuntimeImageError(
            "runtime_image_inspection_failed", "Container image inspection returned invalid metadata."
        )
    image_id = image.get("Id")
    repo_digests = image.get("RepoDigests")
    matches = image_id == digest or (
        isinstance(repo_digests, list)
        and any(
            isinstance(value, str) and value.endswith(f"@{digest}")
            for value in repo_digests
        )
    )
    if not matches:
        raise RuntimeImageError(
            "runtime_image_digest_mismatch",
            f"Local runtime image {selector} does not match the release digest.",
        )
    return {
        "selector": selector,
        "identity": digest,
        "image_id": image_id,
        "repo_digests": [
            value for value in repo_digests or [] if isinstance(value, str)
        ],
    }


def pull_release(
    repo_root: Path,
    manifest_path: Path | None = None,
    *,
    engine_name: str | None = None,
) -> dict[str, Any]:
    root = repo_root.resolve()
    manifest = load_release_manifest(manifest_path or default_manifest_path(root))
    current_revision = _current_source_revision(root)
    if current_revision and current_revision != manifest["product_source_revision"]:
        raise RuntimeImageError(
            "runtime_release_product_mismatch",
            "Runtime release manifest belongs to a different Workbench source revision.",
        )
    engine = _container_engine(engine_name)
    inspected: dict[str, dict[str, Any]] = {}
    for component in COMPONENTS:
        entry = manifest["images"][component]
        selector = entry["selector"]
        completed = subprocess.run(
            [engine, "pull", selector],
            stdin=subprocess.DEVNULL,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeImageError(
                "runtime_image_pull_failed", f"Could not pull runtime image {selector}."
            )
        inspected[component] = _inspect_image(engine, selector, entry["digest"])

    activation_core = {
        "format": {"name": ACTIVATION_NAME, "version": ACTIVATION_VERSION},
        "release": manifest["release"],
        "product_source_revision": manifest["product_source_revision"],
        "manifest_sha256": manifest["manifest_sha256"],
        "images": {
            component: {
                "reference": manifest["images"][component]["selector"],
                "identity": manifest["images"][component]["digest"],
                "image_id": inspected[component]["image_id"],
            }
            for component in COMPONENTS
        },
    }
    activation = {
        **activation_core,
        "activation_sha256": _sha256_json(activation_core),
    }
    _atomic_json(activation_path(root), activation)
    return activation


def load_activation(repo_root: Path, *, required: bool = False) -> dict[str, Any] | None:
    path = activation_path(repo_root.resolve())
    if not path.exists():
        if required:
            raise RuntimeImageError(
                "runtime_activation_unavailable",
                "No digest-pinned runtime release has been activated.",
            )
        return None
    payload = _read_json(
        path,
        code="runtime_activation_invalid",
        message="The active runtime image record is invalid.",
    )
    if payload.get("format") != {"name": ACTIVATION_NAME, "version": ACTIVATION_VERSION}:
        raise RuntimeImageError(
            "runtime_activation_invalid", "Unsupported runtime activation format."
        )
    release = payload.get("release")
    source_revision = payload.get("product_source_revision")
    manifest_sha256 = payload.get("manifest_sha256")
    activation_sha256 = payload.get("activation_sha256")
    images = payload.get("images")
    if not isinstance(release, str) or not _RELEASE_RE.fullmatch(release):
        raise RuntimeImageError(
            "runtime_activation_invalid", "Active runtime release identifier is invalid."
        )
    if not isinstance(source_revision, str) or not _REVISION_RE.fullmatch(source_revision):
        raise RuntimeImageError(
            "runtime_activation_invalid", "Active product source revision is invalid."
        )
    if not isinstance(manifest_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
        raise RuntimeImageError(
            "runtime_activation_invalid", "Active runtime manifest checksum is invalid."
        )
    if not isinstance(images, dict) or set(images) != set(COMPONENTS):
        raise RuntimeImageError(
            "runtime_activation_invalid", "Active runtime image set is incomplete."
        )
    for component in COMPONENTS:
        entry = images.get(component)
        if not isinstance(entry, dict):
            raise RuntimeImageError(
                "runtime_activation_invalid", f"Active {component} runtime is invalid."
            )
        reference = entry.get("reference")
        identity = entry.get("identity")
        if (
            not isinstance(identity, str)
            or not _DIGEST_RE.fullmatch(identity)
            or not isinstance(reference, str)
            or not reference.endswith(f"@{identity}")
        ):
            raise RuntimeImageError(
                "runtime_activation_invalid",
                f"Active {component} runtime is not digest-pinned.",
            )
    activation_core = {
        key: value for key, value in payload.items() if key != "activation_sha256"
    }
    if (
        not isinstance(activation_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", activation_sha256)
        or _sha256_json(activation_core) != activation_sha256
    ):
        raise RuntimeImageError(
            "runtime_activation_invalid", "Active runtime record failed its integrity check."
        )
    return payload


def runtime_environment(repo_root: Path) -> dict[str, str]:
    activation = load_activation(repo_root, required=False)
    if activation is None:
        return {}
    environment = {
        "WRF_CHAMMER_SOURCE_REVISION": activation["product_source_revision"]
    }
    for component in COMPONENTS:
        prefix = f"WRF_CHAMMER_{component.upper()}_RUNTIME"
        entry = activation["images"][component]
        environment[f"{prefix}_REFERENCE"] = entry["reference"]
        environment[f"{prefix}_IDENTITY"] = entry["identity"]
    return environment
