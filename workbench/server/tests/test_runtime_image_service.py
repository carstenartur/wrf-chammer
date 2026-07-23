#!/usr/bin/env python3
"""Tests for digest-pinned runtime release validation, activation, and provenance."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workbench.runtime_image_service import (
    COMPONENTS,
    RuntimeImageError,
    load_activation,
    load_release_manifest,
    pull_release,
    runtime_environment,
)

SOURCE_REVISION = "1" * 40
DIGESTS = {
    "wps": "sha256:" + "a" * 64,
    "wrf": "sha256:" + "b" * 64,
    "postprocessing": "sha256:" + "c" * 64,
}


def manifest() -> dict:
    return {
        "format": {"name": "wrf-chammer-runtime-release", "version": 1},
        "release": "0.1.0-test",
        "product_source_revision": SOURCE_REVISION,
        "images": {
            component: {
                "reference": f"ghcr.io/carstenartur/wrf-chammer-{component}:0.1.0-test",
                "digest": DIGESTS[component],
            }
            for component in COMPONENTS
        },
    }


class RuntimeImageServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.previous)

    def write_manifest(self, root: Path, payload: dict | None = None) -> Path:
        path = root / "release-manifest.json"
        path.write_text(json.dumps(payload or manifest()), encoding="utf-8")
        return path

    def test_manifest_requires_exact_components_and_digests(self) -> None:
        with tempfile.TemporaryDirectory(prefix="runtime-manifest-") as temporary:
            root = Path(temporary)
            loaded = load_release_manifest(self.write_manifest(root))
            self.assertEqual("0.1.0-test", loaded["release"])
            self.assertEqual(
                f"ghcr.io/carstenartur/wrf-chammer-wrf:0.1.0-test@{DIGESTS['wrf']}",
                loaded["images"]["wrf"]["selector"],
            )

            missing = manifest()
            missing["images"].pop("wps")
            with self.assertRaises(RuntimeImageError) as context:
                load_release_manifest(self.write_manifest(root, missing))
            self.assertEqual("invalid_runtime_manifest", context.exception.code)

            mutable = manifest()
            mutable["images"]["wrf"]["reference"] += "@" + DIGESTS["wrf"]
            with self.assertRaises(RuntimeImageError):
                load_release_manifest(self.write_manifest(root, mutable))

    def test_pull_verifies_digests_and_writes_integrity_checked_activation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="runtime-pull-") as temporary:
            root = Path(temporary)
            activation_path = root / "activation.json"
            os.environ["WRF_CHAMMER_RUNTIME_ACTIVATION"] = str(activation_path)
            os.environ["WRF_CHAMMER_SOURCE_REVISION"] = SOURCE_REVISION
            manifest_path = self.write_manifest(root)

            def completed(command, **_kwargs):
                if command[1] == "pull":
                    return subprocess.CompletedProcess(command, 0, "", "")
                if command[1:3] == ["image", "inspect"]:
                    selector = command[3]
                    digest = selector.rsplit("@", 1)[1]
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        json.dumps(
                            [
                                {
                                    "Id": "sha256:" + "d" * 64,
                                    "RepoDigests": [selector],
                                }
                            ]
                        ),
                        "",
                    )
                raise AssertionError(command)

            with mock.patch(
                "workbench.runtime_image_service.shutil.which",
                return_value="/usr/bin/docker",
            ), mock.patch(
                "workbench.runtime_image_service.subprocess.run",
                side_effect=completed,
            ):
                activation = pull_release(root, manifest_path)

            self.assertTrue(activation_path.is_file())
            self.assertEqual(SOURCE_REVISION, activation["product_source_revision"])
            self.assertEqual(DIGESTS["wps"], activation["images"]["wps"]["identity"])
            loaded = load_activation(root, required=True)
            self.assertEqual(activation, loaded)
            environment = runtime_environment(root)
            self.assertEqual(DIGESTS["wrf"], environment["WRF_CHAMMER_WRF_RUNTIME_IDENTITY"])
            self.assertTrue(
                environment["WRF_CHAMMER_WRF_RUNTIME_REFERENCE"].endswith(
                    "@" + DIGESTS["wrf"]
                )
            )

            tampered = copy.deepcopy(activation)
            tampered["release"] = "tampered"
            activation_path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaises(RuntimeImageError) as context:
                load_activation(root, required=True)
            self.assertEqual("runtime_activation_invalid", context.exception.code)

    def test_product_revision_mismatch_fails_before_pull(self) -> None:
        with tempfile.TemporaryDirectory(prefix="runtime-revision-") as temporary:
            root = Path(temporary)
            os.environ["WRF_CHAMMER_SOURCE_REVISION"] = "2" * 40
            with mock.patch(
                "workbench.runtime_image_service.shutil.which",
                return_value="/usr/bin/docker",
            ), mock.patch(
                "workbench.runtime_image_service.subprocess.run"
            ) as run:
                with self.assertRaises(RuntimeImageError) as context:
                    pull_release(root, self.write_manifest(root))
            self.assertEqual("runtime_release_product_mismatch", context.exception.code)
            run.assert_not_called()

    def test_digest_mismatch_never_activates_release(self) -> None:
        with tempfile.TemporaryDirectory(prefix="runtime-digest-") as temporary:
            root = Path(temporary)
            activation_path = root / "activation.json"
            os.environ["WRF_CHAMMER_RUNTIME_ACTIVATION"] = str(activation_path)
            os.environ["WRF_CHAMMER_SOURCE_REVISION"] = SOURCE_REVISION

            def completed(command, **_kwargs):
                if command[1] == "pull":
                    return subprocess.CompletedProcess(command, 0, "", "")
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps(
                        [
                            {
                                "Id": "sha256:" + "e" * 64,
                                "RepoDigests": ["ghcr.io/example/other@sha256:" + "f" * 64],
                            }
                        ]
                    ),
                    "",
                )

            with mock.patch(
                "workbench.runtime_image_service.shutil.which",
                return_value="/usr/bin/docker",
            ), mock.patch(
                "workbench.runtime_image_service.subprocess.run",
                side_effect=completed,
            ):
                with self.assertRaises(RuntimeImageError) as context:
                    pull_release(root, self.write_manifest(root))
            self.assertEqual("runtime_image_digest_mismatch", context.exception.code)
            self.assertFalse(activation_path.exists())


if __name__ == "__main__":
    unittest.main()
