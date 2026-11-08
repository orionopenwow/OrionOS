"""orion-settings-app [--page KEY] [--demo] [--solid|--glass]

  --page KEY   open on one page: network bluetooth sound displays printers power time users privacy updates
               appearance orion permissions
  --demo       show made-up sample data instead of this computer's (for screenshots and tests; changes nothing)
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="orion-settings-app", add_help=True)
    ap.add_argument("--page", default=None)
    ap.add_argument("--demo", action="store_true")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--solid", action="store_true")
    g.add_argument("--glass", action="store_true")
    args, _rest = ap.parse_known_args(argv)

    from .backend import System
    if args.demo or os.environ.get("ORION_SETTINGS_DEMO") == "1":
        from .fake import FakeRunner
        home = tempfile.mkdtemp(prefix="orion-settings-demo-")
        system = System(FakeRunner(), home=home)
    else:
        system = System()
    from .ui import SettingsApp
    solid = True if args.solid else False if args.glass else None
    app = SettingsApp(system, first=args.page, solid=solid)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
