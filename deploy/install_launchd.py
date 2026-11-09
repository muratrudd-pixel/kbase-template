"""macOS: write one launchd agent per job in deploy/jobs.yaml to ~/Library/LaunchAgents and load it.

  python deploy/install_launchd.py            install / update all
  python deploy/install_launchd.py --remove   unload and delete them

Labels are <prefix>.<job> (prefix from KB_LAUNCHD_PREFIX in .env, default com.example.kbase). Paths are resolved from
where this repo lives, so nothing is hard-coded.
"""
import os
import plistlib
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
AGENTS = Path.home() / "Library" / "LaunchAgents"
KEYS = {"minute": "Minute", "hour": "Hour", "weekday": "Weekday"}


def prefix():
    env = ROOT / ".env"
    for line in env.read_text().splitlines() if env.exists() else []:
        if line.startswith("KB_LAUNCHD_PREFIX="):
            return line.split("=", 1)[1].strip()
    return "com.example.kbase"


def main():
    jobs = yaml.safe_load((ROOT / "deploy" / "jobs.yaml").read_text())
    uid = os.getuid()
    AGENTS.mkdir(parents=True, exist_ok=True)
    (ROOT / "logs").mkdir(exist_ok=True)
    for name, j in jobs.items():
        label = f"{prefix()}.{name}"
        path = AGENTS / f"{label}.plist"
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True)
        if "--remove" in sys.argv:
            path.unlink(missing_ok=True)
            print(f"removed {label}")
            continue
        plist = {
            "Label": label,
            "ProgramArguments": [str(ROOT / "bin" / "kb")] + j["cmd"].split(),
            "WorkingDirectory": str(ROOT),
            "StartCalendarInterval": {KEYS[k]: v for k, v in j["schedule"].items()},
            "StandardOutPath": str(ROOT / "logs" / f"{name}.log"),
            "StandardErrorPath": str(ROOT / "logs" / f"{name}.log"),
            "EnvironmentVariables": {"PATH": f"{Path.home()}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
        }
        path.write_bytes(plistlib.dumps(plist))
        subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(path)], check=True)
        print(f"loaded {label}")


if __name__ == "__main__":
    main()
