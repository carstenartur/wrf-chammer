# Generated user-guide screenshots

This directory is populated by:

```bash
sh ci/generate-user-guide-screenshots.sh
```

CI uploads the generated PNG files as a workflow artifact named
`xaver-user-guide-screenshots`.

Commit refreshed PNG screenshots only when the visible user-guide flow changes
intentionally.
