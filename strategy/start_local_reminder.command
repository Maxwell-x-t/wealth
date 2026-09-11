#!/bin/zsh
set -eu
ROOT="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$ROOT/../backend/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  print "请先在 Wealth 根目录运行 bash start.sh 完成依赖安装。"
  exit 1
fi
if [[ ! -f "$ROOT/.env.local" && ! -f "$HOME/Library/Application Support/dividend-grid-reminder/.env.local" ]]; then
  cp "$ROOT/.env.local.example" "$ROOT/.env.local"
  print "请填写 $ROOT/.env.local 后重新运行。"
  exit 1
fi
"$PYTHON" "$ROOT/scripts/install_reminder.py"
read -r "REPLY?按回车键退出..."
