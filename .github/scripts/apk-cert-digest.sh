#!/bin/sh
# WP CI-FIX-3: helpers for publish.yml's "Verify APK signature (apksigner)"
# step. POSIX sh, no dependencies beyond sed/tr/sort/wc.
#
#   apk-cert-digest.sh digest FILE
#       FILE is an `apksigner verify --print-certs` transcript. Prints the
#       signing certificate's SHA-256 (lowercase hex) when exactly one
#       distinct one is listed; otherwise prints an `::error::` workflow
#       command to stderr and exits 1 (fail-closed: zero or several
#       certificates never publish).
#
#   apk-cert-digest.sh annotate FILE...
#       Emits one `::notice::` workflow command per FILE carrying the
#       apksigner lines that explain a failure (verdict, scheme summary,
#       signer lines, ERROR/WARNING). Check-run annotations are readable
#       through the public API without a token; the job log is not. Those
#       lines hold only the public certificate DN and fingerprints.
#
# The label in front of the digest varies by build-tools version and
# signature scheme; all of these are accepted:
#   Signer #1 certificate SHA-256 digest: <hex>
#   Signer (minSdkVersion=28, maxSdkVersion=2147483647) certificate SHA-256 digest: <hex>
#   V2 Signer: certificate SHA-256 digest: <hex>
#   V3.1 Signer: certificate SHA-256 digest: <hex>
# A v3.x block lists the same certificate again, so digests are
# de-duplicated before counting. "Source Stamp Signer" lines are NOT a
# signing certificate and are deliberately not matched; neither are the
# SHA-1 / MD5 lines.

set -eu

# Annotation lines kept per file: enough for the full scheme summary and
# several signer blocks, small enough to stay one readable annotation.
MAX_LINES=40

usage() {
  echo "usage: $0 digest FILE | $0 annotate FILE..." >&2
  exit 2
}

digest() {
  file=$1
  if [ ! -r "$file" ]; then
    echo "::error::apk-cert-digest: transcript $(basename "$file") is missing or unreadable" >&2
    exit 1
  fi
  digests=$(tr -d '\r' < "$file" \
    | sed -n 's/^\(V[0-9][0-9.]* \)\{0,1\}Signer.* certificate SHA-256 digest: \([0-9a-fA-F]\{64\}\)[[:space:]]*$/\2/p' \
    | tr 'A-F' 'a-f' | sort -u)
  if [ -z "$digests" ]; then
    count=0
  else
    count=$(printf '%s\n' "$digests" | wc -l | tr -d ' ')
  fi
  if [ "$count" -ne 1 ]; then
    echo "::error::apk-cert-digest: expected exactly one signing certificate (SHA-256), found $count" >&2
    exit 1
  fi
  printf '%s\n' "$digests"
}

# Workflow-command data escaping (actions/toolkit escapeData): % first,
# then CR/LF, so a transcript line can never end the command and start a
# new one. Lines are joined with an escaped LF into one annotation.
annotate_one() {
  file=$1
  if [ ! -s "$file" ]; then
    echo "::notice title=apksigner output::$(basename "$file"): not produced or empty"
    return 0
  fi
  # shellcheck disable=SC2016  # `$` is sed's last-line address, not a shell expansion
  body=$(tr -d '\r' < "$file" \
    | grep -E '^(Verifies|DOES NOT VERIFY|Verified (using|for) |Number of signers|ERROR|WARNING|(V[0-9][0-9.]* )?Signer|Source Stamp)' \
    | head -n "$MAX_LINES" \
    | sed 's/%/%25/g' \
    | sed -n 'H;${x;s/^\n//;s/\n/%0A/g;p;}') || true
  if [ -z "$body" ]; then
    body="(no apksigner verdict or signer lines in the transcript)"
  fi
  echo "::notice title=apksigner output::$(basename "$file"):%0A$body"
}

[ $# -ge 2 ] || usage
mode=$1
shift
case "$mode" in
  digest)
    [ $# -eq 1 ] || usage
    digest "$1"
    ;;
  annotate)
    for f in "$@"; do
      annotate_one "$f"
    done
    ;;
  *)
    usage
    ;;
esac
