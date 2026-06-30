# Workbench browser screenshots

This directory contains Playwright tests for generating user-guide screenshots
from the real local Workbench browser flow.

Run from the repository root:

```bash
sh ci/generate-user-guide-screenshots.sh
```

The script starts the local Workbench server through Playwright's `webServer`
configuration, opens the UI, executes the Xaver dry-run workflow and writes PNG
screenshots to:

```text
doc/user-guide/screenshots/
```

CI uploads these generated screenshots as the `xaver-user-guide-screenshots`
artifact.  Commit refreshed PNG screenshots only when the visible documented
workflow changes intentionally.

This intentionally uses Playwright instead of a heavy Testcontainers setup.  The
current documented flow depends on a local Python Workbench server and browser
state, not on multiple long-running infrastructure containers.  Testcontainers
can be added later when the full containerized WPS/WRF stack needs to be tested
as a service topology.
