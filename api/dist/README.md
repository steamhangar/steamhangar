# api/dist -- release downloads baked into the vault-api image

Placeholder directory (WP DIST-1a). In a checkout it holds only this file.

The release pipeline (`.github/workflows/publish.yml`, `publish` job,
vault-api leg) downloads the signed APK and the vault-agent release files
of the same run into this directory before it builds the image, and
`api/Dockerfile` copies the directory to `/app/dist`. vault-api serves the
files under `/dist/<name>` and describes them at `GET /v1/dist`
(`api/vault_api/dist.py`, api/README.md "Release downloads").

A local build (`docker compose build`, the dev and verify stacks) bakes
nothing but this README, which is never served: only the exact release
names for the server's own version are. Everything else in this directory
is gitignored.
