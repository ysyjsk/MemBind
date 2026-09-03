#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# The live preflight includes an authenticated Bolt query.  Use the same full
# experiment environment as construction runners, not only the path/profile
# subset from local_env.sh.
source "$SCRIPT_DIR/activate.sh" >/dev/null

preflight_file="$MEMBIND_RUN_ROOT/refresh-live-preflight.json"
manifest_result="$MEMBIND_RUN_ROOT/refresh-platform-manifest-result.json"
status_file="$MEMBIND_RUN_ROOT/background-setup.status"

"$MEMBIND_ENV/bin/python" "$SCRIPT_DIR/preflight.py" \
  --mode live \
  --timeout 120 \
  --output "$preflight_file" >/dev/null
"$MEMBIND_ENV/bin/python" "$SCRIPT_DIR/write_profile_manifest.py" \
  --preflight "$preflight_file" >"$manifest_result"

manifest_path="$(jq -r '.path' "$manifest_result")"
manifest_sha="$(jq -r '.payload_sha256' "$manifest_result")"
temporary="$status_file.tmp.$$"
printf 'READY profile=%s manifest=%s payload_sha256=%s time=%s\n' \
  "$MEMBIND_PROFILE_ID" "$manifest_path" "$manifest_sha" "$(date --iso-8601=seconds)" \
  >"$temporary"
mv "$temporary" "$status_file"
echo "READY: $MEMBIND_PROFILE_ID"
echo "Manifest: $manifest_path"
echo "Payload SHA-256: $manifest_sha"
