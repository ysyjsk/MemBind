#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local_env.sh"

model_id="Qwen/Qwen3-8B-AWQ"
revision="4da05a8edb55c6046cce958586c33b61da07bb79"
model_dir="$MEMBIND_MODEL_ROOT/Qwen3-8B-AWQ"
run_dir="$MEMBIND_DATA_ROOT/run/membind-local"
status_file="$run_dir/qwen3-8b-awq-download.status"
pidfile="$run_dir/qwen3-8b-awq-download.pid"

mkdir -p "$model_dir" "$run_dir"
echo $$ >"$pidfile"
printf 'RUNNING model=%s revision=%s started=%s\n' \
  "$model_id" "$revision" "$(date --iso-8601=seconds)" >"$status_file"

cleanup() {
  local code=$?
  trap - EXIT
  rm -f "$pidfile"
  if [[ $code -eq 0 ]]; then
    printf 'READY model=%s revision=%s path=%s completed=%s\n' \
      "$model_id" "$revision" "$model_dir" "$(date --iso-8601=seconds)" >"$status_file"
  else
    printf 'FAILED exit_code=%d model=%s revision=%s time=%s\n' \
      "$code" "$model_id" "$revision" "$(date --iso-8601=seconds)" >"$status_file"
  fi
  exit "$code"
}
trap cleanup EXIT

echo "[$(date --iso-8601=seconds)] downloading $model_id@$revision to $model_dir"
local_hf download "$model_id" \
  --revision "$revision" \
  --local-dir "$model_dir"

local_python - "$model_dir" "$model_id" "$revision" <<'PY'
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
model_id = sys.argv[2]
revision = sys.argv[3]
if not (root / "config.json").is_file():
    raise SystemExit(f"missing config.json in {root}")

weights = sorted(root.glob("*.safetensors"))
if not weights:
    raise SystemExit(f"no safetensors weights found in {root}")

files = sorted(
    path
    for path in root.rglob("*")
    if path.is_file()
    and path.name != ".membind-model-manifest.json"
    and ".cache" not in path.relative_to(root).parts
)
digest = hashlib.sha256()
total = 0
for path in files:
    relative = path.relative_to(root).as_posix().encode()
    size = path.stat().st_size
    file_digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            file_digest.update(chunk)
    digest.update(relative + b"\0" + str(size).encode() + b"\0" + file_digest.digest())
    total += size

manifest = {
    "source_model": model_id,
    "revision": revision,
    "path": str(root),
    "files": len(files),
    "weight_files": len(weights),
    "bytes": total,
    "sha256": digest.hexdigest(),
}
(root / ".membind-model-manifest.json").write_text(
    json.dumps(manifest, indent=2) + "\n",
    encoding="utf-8",
)
print(json.dumps(manifest, sort_keys=True))
PY

echo "[$(date --iso-8601=seconds)] download and validation completed"
