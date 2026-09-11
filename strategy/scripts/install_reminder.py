"""Install the existing reminder schedule using paths in the consolidated checkout."""
from datetime import datetime
from pathlib import Path
import os
import plistlib
import shutil
import subprocess


def main():
    root = Path(__file__).resolve().parents[1]
    agent = Path.home() / "Library/LaunchAgents/com.xtboss.dividend-grid-reminder.plist"
    agent.parent.mkdir(parents=True, exist_ok=True)
    if agent.exists():
        backup_dir = Path.home() / "Library/Application Support/dividend-grid-reminder"
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(agent, backup_dir / f"launch-agent-before-merge-{datetime.now():%Y%m%d%H%M%S}.plist")
    payload = dict(Label="com.xtboss.dividend-grid-reminder", WorkingDirectory=str(root),
        ProgramArguments=["/bin/zsh", str(root / "scripts/local_reminder.sh")],
        StartCalendarInterval=[dict(Hour=9, Minute=0), dict(Hour=9, Minute=30),
                               dict(Hour=14, Minute=0), dict(Hour=15, Minute=5)],
        StandardOutPath="/tmp/dividend-grid-reminder.log", StandardErrorPath="/tmp/dividend-grid-reminder.error.log")
    with agent.open("wb") as handle:
        plistlib.dump(payload, handle)
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/com.xtboss.dividend-grid-reminder"], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(agent)], check=True)
    print(f"提醒已指向 {root}；未立即执行推送")


if __name__ == "__main__":
    main()
