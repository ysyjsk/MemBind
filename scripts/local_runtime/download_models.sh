#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local_env.sh"

mkdir -p "$MEMBIND_LLM_MODEL_DIR" "$MEMBIND_EMBED_MODEL_DIR"

download_weight() {
  local url="$1"
  local target="$2"
  local expected_size="$3"
  local expected_sha256="$4"
  local part="${target}.part"
  local actual_size actual_sha256

  if [[ -f "$target" ]]; then
    actual_size="$(stat -c '%s' "$target")"
    if [[ "$actual_size" == "$expected_size" ]]; then
      actual_sha256="$(sha256sum "$target" | awk '{print $1}')"
      if [[ "$actual_sha256" == "$expected_sha256" ]]; then
        echo "Verified existing weight: $target"
        return 0
      fi
    fi
    echo "Existing weight failed validation; downloading a clean replacement: $target" >&2
  fi

  local aria_args=(
    --continue=true
    --auto-file-renaming=false
    --allow-overwrite=false
    --dir="$(dirname "$target")"
    --out="$(basename "$part")"
    --file-allocation=none
    --max-connection-per-server=8
    --split=8
    --min-split-size=1M
    --max-tries=20
    --retry-wait=5
    --connect-timeout=30
    --timeout=120
    --summary-interval=30
  )
  if [[ -n "${HTTPS_PROXY:-}" ]]; then
    aria_args+=(--all-proxy="$HTTPS_PROXY")
  fi
  aria2c "${aria_args[@]}" "$url"

  actual_size="$(stat -c '%s' "$part")"
  actual_sha256="$(sha256sum "$part" | awk '{print $1}')"
  if [[ "$actual_size" != "$expected_size" || "$actual_sha256" != "$expected_sha256" ]]; then
    echo "Downloaded weight failed validation: $target" >&2
    echo "expected size=$expected_size sha256=$expected_sha256" >&2
    echo "actual   size=$actual_size sha256=$actual_sha256" >&2
    return 1
  fi
  mv -f "$part" "$target"
  echo "Installed verified weight: $target"
}

echo "Downloading Qwen3-14B-AWQ metadata into $MEMBIND_LLM_MODEL_DIR"
local_hf download Qwen/Qwen3-14B-AWQ \
  --local-dir "$MEMBIND_LLM_MODEL_DIR" \
  --exclude '*.safetensors'

download_weight \
  "https://huggingface.co/Qwen/Qwen3-14B-AWQ/resolve/main/model-00001-of-00002.safetensors" \
  "$MEMBIND_LLM_MODEL_DIR/model-00001-of-00002.safetensors" \
  4988339832 \
  668eb0f1356638310db286f4819b223c12e3916934123f1a81b2b2c0e148c6a2
download_weight \
  "https://huggingface.co/Qwen/Qwen3-14B-AWQ/resolve/main/model-00002-of-00002.safetensors" \
  "$MEMBIND_LLM_MODEL_DIR/model-00002-of-00002.safetensors" \
  4988350408 \
  c3c1625df80fe01211038bfa520629ebde6adf776556aa80cd49696d986d6657

echo "Downloading Qwen3-Embedding-0.6B metadata into $MEMBIND_EMBED_MODEL_DIR"
local_hf download Qwen/Qwen3-Embedding-0.6B \
  --local-dir "$MEMBIND_EMBED_MODEL_DIR" \
  --exclude '*.safetensors'

download_weight \
  "https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/resolve/main/model.safetensors" \
  "$MEMBIND_EMBED_MODEL_DIR/model.safetensors" \
  1191586416 \
  0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd

local_python - "$MEMBIND_LLM_MODEL_DIR" "$MEMBIND_EMBED_MODEL_DIR" <<'PY'
import hashlib
import json
import pathlib
import sys

for raw in sys.argv[1:]:
    root = pathlib.Path(raw)
    files = sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.name != ".membind-model-manifest.json"
        and ".cache" not in p.relative_to(root).parts
    )
    if not (root / "config.json").is_file():
        raise SystemExit(f"missing config.json in {root}")
    weight_files = [p for p in files if p.suffix == ".safetensors"]
    if not weight_files:
        raise SystemExit(f"no safetensors weights found in {root}")
    digest = hashlib.sha256()
    total = 0
    for path in files:
        rel = path.relative_to(root).as_posix().encode()
        size = path.stat().st_size
        file_digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                file_digest.update(chunk)
        digest.update(rel + b"\0" + str(size).encode() + b"\0" + file_digest.digest())
        total += size
    manifest = {"path": str(root), "files": len(files), "bytes": total, "sha256": digest.hexdigest()}
    (root / ".membind-model-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, sort_keys=True))
PY
