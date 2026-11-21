# WP 1.7 MVP results (historical evidence)

The two `RESULTS-*.md` files here are the recorded runs of the WP 1.7 MVP
end-to-end test (2026-08-05: prefill one app via the API, read its size,
delete it). They stay because vault-api's prefill summary parsing was
built from their verbatim SteamPrefill output (`api/vault_api/prefill.py`,
`api/vault_api/prefill_summary.py` and their tests cite them).

The script that produced them, `run-mvp.ps1`, ran vault-core natively on
Windows with the Phase-0 nginx binary from `poc/` and was retired by
NGINX-DUP-1 (rc23) together with the native config `core/nginx/nginx.conf`.
Nothing re-runs a real-account prefill automatically: verify-stack never
enters Steam credentials. The API test suite covers the prefill, size and
delete logic with this recorded output; a real-account run is a manual
step on a deployed stack.
