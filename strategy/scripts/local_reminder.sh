#!/bin/zsh

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

if [[ -f "$ROOT/.env.local" ]]; then
  set -a
  source "$ROOT/.env.local"
  set +a
elif [[ -f "$HOME/Library/Application Support/dividend-grid-reminder/.env.local" ]]; then
  set -a
  source "$HOME/Library/Application Support/dividend-grid-reminder/.env.local"
  set +a
fi

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

hour="$(date +%H)"
minute="$(date +%M)"
current_minutes=$((10#$hour * 60 + 10#$minute))
if (( current_minutes >= 535 && current_minutes <= 545 )); then
  session="morning"
elif (( current_minutes >= 565 && current_minutes <= 575 )); then
  session="midday"
elif (( current_minutes >= 835 && current_minutes <= 845 )); then
  session="midday"
elif (( current_minutes >= 900 && current_minutes <= 910 )); then
  session="close"
else
  print -u2 "local_reminder.sh 只应在 09:00、09:30、14:00 或 15:05 的 ±5 分钟内运行，当前 ${hour}:${minute}"
  exit 2
fi

PYTHON="$ROOT/../backend/.venv/bin/python"
grid_status=0
"$PYTHON" scripts/grid_calculator.py \
  --source akshare \
  --stocks-file watchlist.json \
  --notify wecom \
  --notify-on always || grid_status=$?

rsi_status=0
"$PYTHON" scripts/rsi6_calculator.py \
  --session "$session" \
  --notify wecom \
  --notify-on always || rsi_status=$?

if (( grid_status != 0 )); then
  exit "$grid_status"
fi
exit "$rsi_status"
