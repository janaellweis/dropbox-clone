"""Manage the Dropbox Clone macOS LaunchAgent."""

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.dropboxclone.sync"
AGENTS = Path.home() / "Library" / "LaunchAgents"
LOGS = Path.home() / "Library" / "Logs" / "DropboxClone"
PLIST = AGENTS / f"{LABEL}.plist"
PROJECT = Path(__file__).resolve().parent.parent
DOMAIN = f"gui/{os.getuid()}"
SERVICE = f"{DOMAIN}/{LABEL}"


def launchctl(*args):
    return subprocess.run(["launchctl", *args], check=True)


def install(args):
    if sys.platform != "darwin":
        raise SystemExit("LaunchAgent installation requires macOS")

    if PLIST.exists():
        raise SystemExit("Agent already installed; uninstall it first")

    folder = Path(args.folder).expanduser().resolve()

    if not folder.is_dir():
        raise SystemExit(f"Sync folder does not exist: {folder}")

    AGENTS.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)

    config = {
        "Label": LABEL,
        "ProgramArguments": [
            sys.executable,
            "-m",
            "client",
            "--folder",
            str(folder),
            "--server",
            args.server,
            "--interval",
            str(args.interval),
        ],
        "WorkingDirectory": str(PROJECT),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": str(LOGS / "stdout.log"),
        "StandardErrorPath": str(LOGS / "stderr.log"),
    }

    with PLIST.open("wb") as file:
        plistlib.dump(config, file)

    try:
        launchctl("bootstrap", DOMAIN, str(PLIST))
    except subprocess.CalledProcessError:
        PLIST.unlink()
        raise

    print(f"Installed {LABEL}")
    print(f"Logs: {LOGS}")


def uninstall():
    if PLIST.exists():
        status = subprocess.run(
            ["launchctl", "print", SERVICE],
            capture_output=True,
        )
        if status.returncode == 0:
            launchctl("bootout", DOMAIN, str(PLIST))

        PLIST.unlink()

    print("Agent uninstalled. Synced files were not deleted.")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    installer = sub.add_parser("install")
    installer.add_argument("--folder", required=True)
    installer.add_argument("--server", default="http://127.0.0.1:8001")
    installer.add_argument("--interval", type=float, default=5.0)

    sub.add_parser("start")
    sub.add_parser("stop")
    sub.add_parser("status")
    sub.add_parser("uninstall")

    args = parser.parse_args()

    if args.command == "install":
        if args.interval <= 0:
            parser.error("--interval must be positive")
        install(args)
    elif args.command == "uninstall":
        uninstall()
    elif args.command == "start":
        if not PLIST.exists():
            raise SystemExit("Agent not installed")
        launchctl("bootstrap", DOMAIN, str(PLIST))
    elif args.command == "stop":
        if not PLIST.exists():
            raise SystemExit("Agent not installed")
        launchctl("bootout", DOMAIN, str(PLIST))
    elif args.command == "status":
        launchctl("print", SERVICE)


if __name__ == "__main__":
    main()
