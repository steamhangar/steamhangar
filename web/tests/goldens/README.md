# web/tests/goldens

Screenshot goldens of the web UI, `<page>-<width>.png`, recorded by CI only
(`web-goldens-record` + `web-goldens-push` on a `wp/<name>-goldens` branch)
and verified by the `web / goldens` job on every push and pull request.
Never commit a locally rendered PNG here: how to record, compare and
review them is in `tools/README.md`. This file is the only thing in the
directory that survives a refresh.
