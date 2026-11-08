"""orion-rescue: the Rescue panel and its helpers.

    orion-rescue                      open the Rescue panel
    orion-rescue --after-crash        ... worded for "the Orion screen keeps stopping" (orion-hud uses this)
    orion-rescue --session            ... worded for the Rescue desktop
    orion-rescue record-crash SECONDS the HUD ran for SECONDS and stopped; exit 10 = open the panel now
    orion-rescue reset-crashes
    orion-rescue plan-undo [update|change]   what "Undo the last ..." would do (plain words, or --json)
    orion-rescue report [--send]      print (or send) the report
"""
from __future__ import annotations

import json
import sys

OPEN_PANEL = 10


def main(argv: list[str]) -> int:
    from . import crashes
    cmd = argv[0] if argv else ""
    if cmd == "record-crash":
        try:
            ran = float(argv[1])
        except (IndexError, ValueError):
            ran = 0.0
        return OPEN_PANEL if crashes.record(ran) else 0
    if cmd == "reset-crashes":
        crashes.reset()
        return 0
    if cmd == "plan-undo":
        from . import undo
        which = argv[1] if len(argv) > 1 and not argv[1].startswith("-") else "update"
        plan = undo.plan_last_change() if which == "change" else undo.plan_last_update()
        if "--json" in argv:
            print(json.dumps(plan.__dict__))
        else:
            print(f"{plan.title}\n{plan.detail}")
        return 0 if plan.possible else 1
    if cmd == "report":
        from . import report
        if "--send" in argv:
            ok, msg = report.send()
            print(msg)
            return 0 if ok else 1
        print(report.build())
        return 0
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    from . import panel
    mode = "crash" if "--after-crash" in argv else "session" if "--session" in argv else ""
    return panel.main(mode)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
