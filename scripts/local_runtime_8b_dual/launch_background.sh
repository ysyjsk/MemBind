#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local_env.sh"

session="${MEMBIND_PROFILE_ID}-setup"
if tmux has-session -t "=$session" 2>/dev/null; then
  echo "Background setup tmux session already exists: $session" >&2
  exit 3
fi
tmux new-session -d -s "$session" -n setup "$SCRIPT_DIR/background_setup.sh"
echo "Started $session; inspect $MEMBIND_LOG_ROOT/background-setup.log"
