#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local_env.sh"

echo "profile=$MEMBIND_PROFILE_ID"
declared_ready=false
declared_manifest=""
declared_payload_sha256=""
if [[ -f "$MEMBIND_RUN_ROOT/background-setup.status" ]]; then
  status_line="$(cat "$MEMBIND_RUN_ROOT/background-setup.status")"
  echo "status=$status_line"
  if [[ "$status_line" == READY\ * ]]; then
    declared_ready=true
    declared_manifest="$(sed -n 's/.* manifest=\([^ ]*\).*/\1/p' <<<"$status_line")"
    declared_payload_sha256="$(sed -n 's/.* payload_sha256=\([^ ]*\).*/\1/p' <<<"$status_line")"
  fi
else
  echo "status=NOT_STARTED"
fi

live_components=0
for item in \
  "native:$MEMBIND_NATIVE_LLM_TMUX_SESSION:$MEMBIND_NATIVE_LLM_PORT:native-llm.pid" \
  "prepare:$MEMBIND_PREPARE_LLM_TMUX_SESSION:$MEMBIND_PREPARE_LLM_PORT:prepare-llm.pid" \
  "embedding:$MEMBIND_EMBED_TMUX_SESSION:$MEMBIND_EMBED_PORT:embedding.pid"; do
  IFS=: read -r name session port pidname <<<"$item"
  session_state=DOWN
  port_state=DOWN
  pid_state=DOWN
  tmux has-session -t "=$session" 2>/dev/null && session_state=UP
  ss -ltn | rg -q ":${port}\\b" && port_state=UP
  if [[ -s "$MEMBIND_RUN_ROOT/$pidname" ]] && kill -0 "$(cat "$MEMBIND_RUN_ROOT/$pidname")" 2>/dev/null; then
    pid_state="UP($(cat "$MEMBIND_RUN_ROOT/$pidname"))"
  fi
  if [[ "$session_state" == UP && "$port_state" == UP && "$pid_state" == UP* ]]; then
    live_components=$((live_components + 1))
  fi
  echo "$name tmux=$session_state port=$port_state pid=$pid_state"
done

neo4j_state=DOWN
if ss -ltn | rg -q ':7687\b' && \
   "$MEMBIND_REPO_ROOT/membind-validation/runtime/neo4j/neo4j-community-5.26.0/bin/neo4j" status \
     >/dev/null 2>&1; then
  neo4j_state=UP
fi
echo "neo4j process_and_bolt=$neo4j_state"

if [[ -f "$MEMBIND_PROFILE_ROOT/latest.json" ]]; then
  latest_manifest="$(jq -r '.manifest_path' "$MEMBIND_PROFILE_ROOT/latest.json")"
  latest_payload_sha256="$(jq -r '.payload_sha256' "$MEMBIND_PROFILE_ROOT/latest.json")"
  echo "latest_manifest=$latest_manifest"
  echo "latest_payload_sha256=$latest_payload_sha256"
fi

if [[ "$declared_ready" == true && ( "$live_components" -ne 3 || "$neo4j_state" != UP ) ]]; then
  echo "effective_status=STALE_READY live_components=$live_components/3 neo4j=$neo4j_state"
  exit 4
fi
if [[ "$declared_ready" == true && \
      ( "$declared_manifest" != "${latest_manifest:-}" || \
        "$declared_payload_sha256" != "${latest_payload_sha256:-}" ) ]]; then
  echo "effective_status=STALE_PLATFORM_DECLARATION"
  exit 5
fi
