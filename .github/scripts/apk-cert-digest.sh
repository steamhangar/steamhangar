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
#   apk-cert-digest.sh pin PINFILE DIGEST
#       WP CI-SIGN-PIN-1: compares DIGEST (output of `digest`) with the
#       release-certificate pin in PINFILE (app/signing-cert.sha256: '#'
#       comment lines plus at most one line of 64 hex characters).
#       Prints `match` or `unset` on stdout and exits 0; exits 1 with an
#       `::error::` on stderr on a mismatch, a malformed pin file, or an
#       unset pin while APK_PIN_STRICT=true (tag runs). An unset pin on any
#       other run prints a `::warning::` and `unset`, so no branch run
#       breaks before the operator sets the pin.
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
  echo "usage: $0 digest FILE | $0 pin PINFILE DIGEST | $0 annotate FILE..." >&2
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

pin() {
  pinfile=$1
  actual=$(printf '%s' "$2" | tr 'A-F' 'a-f')
  if [ ! -r "$pinfile" ]; then
    echo "::error::apk-cert-digest: pin file $pinfile is missing or unreadable" >&2
    exit 1
  fi
  # Value lines: everything that is not blank and not a '#' comment.
  values=$(tr -d '\r' < "$pinfile" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
    | grep -v -e '^#' -e '^$' || true)
  if [ -z "$values" ]; then
    if [ "${APK_PIN_STRICT:-false}" = "true" ]; then
      echo "::error::apk-cert-digest: pin not set -- $pinfile has no certificate SHA-256; a release run refuses to publish without it (app/README.md, \"Pinned release certificate\")" >&2
      exit 1
    fi
    echo "::warning::apk-cert-digest: pin not set -- $pinfile has no certificate SHA-256; tag runs will fail until it is set (app/README.md, \"Pinned release certificate\")" >&2
    echo unset
    return 0
  fi
  count=$(printf '%s\n' "$values" | wc -l | tr -d ' ')
  expected=$(printf '%s' "$values" | tr 'A-F' 'a-f')
  if [ "$count" -ne 1 ] || ! printf '%s' "$expected" | grep -qx '[0-9a-f]\{64\}'; then
    echo "::error::apk-cert-digest: pin file $pinfile is malformed -- expected one line of 64 hex characters" >&2
    exit 1
  fi
  if ! printf '%s' "$actual" | grep -qx '[0-9a-f]\{64\}'; then
    echo "::error::apk-cert-digest: '$actual' is not a certificate SHA-256" >&2
    exit 1
  fi
  if [ "$actual" != "$expected" ]; then
    echo "::error::apk-cert-digest: signing certificate SHA-256 $actual does not match the pinned release certificate $expected ($pinfile) -- a different key signed this APK; Android would refuse it as an update" >&2
    exit 1
  fi
  echo match
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
  pin)
    [ $# -eq 2 ] || usage
    pin "$1" "$2"
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
