#!/bin/zsh

set -u

LABEL="com.xtboss.dividend-grid-reminder"
AGENT="$HOME/Library/LaunchAgents/${LABEL}.plist"

launchctl bootout "gui/$(id -u)/${LABEL}" >/dev/null 2>&1 || true

if [[ -f "$AGENT" ]]; then
  rm "$AGENT"
fi

print "本地定时提醒已停止。"
print "已取消每天 09:00、09:30、14:00、15:05 的自动运行。"
print "项目目录中的原始配置未删除。"
print
read -r "REPLY?按回车键退出..."
