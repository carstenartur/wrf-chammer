#!/bin/sh
# ci/test-event-catalogue.sh — validate Workbench event and preset catalogues.
#
# Keep this wrapper intentionally small.  Catalogue semantics live in
# workbench/core/catalogue.py so the CLI, future local API and web UI can share
# the same behavior instead of reimplementing it in shell or JavaScript.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)

cd "${REPO_ROOT}"
python3 -m workbench.core.catalogue validate
