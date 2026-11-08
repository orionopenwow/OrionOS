"""Get apps, daemon side (docs/ORIONOS_COMPLETE_PLAN.md Part 1 "App store").

Installing an app is the owner's action: a click in the Get apps window (os/getapps). An agent can only *ask*:
`apps_install` (MCP) or POST /apps/install-request puts an "Install X?" card in the approval queue, and nothing is
installed unless the owner approves it there. Approving runs `orion-apps install <id> --json` (Flathub only, the
same command the window uses, so it lands in the same change history).

HTTP routes (local only, like the rest of the daemon):

    POST /apps/install-request   {app_id, why?, requested_by?}  -> {request: {id, status, ...}}
    GET  /apps/install-request/{id}
    POST /apps/notify            {title, body}   used by `orion-apps` to tell your phone what the weekly update did
"""

import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("orion.getapps")

KIND = "app_install"
APP_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+){2,}$")
INSTALL_TIMEOUT_S = 3600


def valid_app_id(app_id: str) -> bool:
    return bool(app_id) and len(app_id) <= 255 and bool(APP_ID_RE.match(app_id))


def cli() -> Optional[str]:
    env = os.environ.get("ORION_APPS_CLI")
    if env:
        return env
    for p in ("/usr/local/bin/orion-apps", "/opt/orion/bin/orion-apps"):
        if os.access(p, os.X_OK):
            return p
    return shutil.which("orion-apps")


def _catalog() -> dict[str, Any]:
    from config import settings
    for p in (Path("/opt/orion/getapps/catalog.json"), Path(settings.repo_root) / "os/getapps/catalog.json"):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return {}


def describe(app_id: str) -> dict[str, Any]:
    cat = _catalog()
    for p in cat.get("picks", []):
        if p.get("id") == app_id:
            return {"name": p["name"], "summary": p.get("summary", ""), "pick": True,
                    "open_source": p.get("open_source")}
    return {"name": app_id, "summary": "", "pick": False}


def _public(item: dict[str, Any]) -> dict[str, Any]:
    return {k: item.get(k) for k in ("id", "status", "app_id", "name", "requested_by", "why", "result", "error",
                                     "created_at", "resolved_at")}


async def request_install(daemon: Any, app_id: str, why: str = "", requested_by: str = "") -> dict[str, Any]:
    """Put an "Install X?" card in the queue (or return the one already waiting). Never installs anything."""
    app_id = (app_id or "").strip()
    if not valid_app_id(app_id):
        raise ValueError(f"“{app_id[:80]}” isn't a Flathub app id (like com.spotify.Client).")
    who = " ".join((requested_by or "An agent").split())[:60]
    why = " ".join((why or "").split())[:400]
    for item in daemon.approvals.pending():
        if item.get("kind") == KIND and item.get("app_id") == app_id:
            return _public(item)
    info = describe(app_id)
    name = info["name"]
    lines = [f"{who} asks to install {name} ({app_id}) from Flathub, for everyone on this computer."]
    if info.get("summary"):
        lines.append(f"What it is: {info['summary']}")
    lines.append("It's one of Orion's picks." if info.get("pick") else
                 "It isn't one of Orion's picks: check it's the app you expect.")
    if why:
        lines.append(f"Why: {why}")
    item = daemon.approvals.add(
        KIND, platform="Get apps", channel="Get apps", author=who, category="normal",
        title=f"Install {name}?", original="\n".join(lines),
        draft="Approve = install it from Flathub now (sandboxed; you can remove it in Get apps). "
              "Reject = don't install it.",
        app_id=app_id, name=name, requested_by=who, why=why)
    await daemon.bus.publish({"event": "APPROVAL_REQUIRED", "item": item})
    try:
        import alerts
        alerts.emit("approval", f"Install {name}?", f"{who} asks to install {name}. Approve or reject in Orion.",
                    ref=item["id"])
    except Exception:
        pass
    try:
        import audio_cues
        await audio_cues.emit("approval_required", detail=f"Install {name}")
    except Exception:
        pass
    return _public(item)


async def run_install(app_id: str) -> dict[str, Any]:
    exe = cli()
    if not exe:
        return {"ok": False, "message": "Get apps isn't installed on this computer (orion-apps is missing)."}
    try:
        proc = await asyncio.create_subprocess_exec(exe, "install", app_id, "--json", stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), timeout=INSTALL_TIMEOUT_S)
    except asyncio.TimeoutError:
        return {"ok": False, "message": "The install took too long and was stopped."}
    except OSError as exc:
        return {"ok": False, "message": f"orion-apps couldn't be started: {exc}"}
    try:
        data = json.loads(out.decode("utf-8", "replace"))
        return data if isinstance(data, dict) else {"ok": False, "message": "orion-apps gave no answer."}
    except ValueError:
        tail = (err or out).decode("utf-8", "replace").strip()[-300:]
        return {"ok": proc.returncode == 0, "message": tail or "orion-apps gave no answer."}


async def decide_card(daemon: Any, item: dict[str, Any], decision: str) -> None:
    """Approve (or Edit) = install now; Reject = nothing happens."""
    item_id = item["id"]
    if decision == "reject":
        claimed = daemon.approvals.claim(item_id, "rejected")
        if claimed is not None:
            updated = daemon.approvals.update(item_id, status="rejected")
            await daemon.bus.publish({"event": "APPROVAL_RESOLVED", "item": updated})
        return
    claimed = daemon.approvals.claim(item_id, "sending")   # one decision only, even on a double click
    if claimed is None:
        await daemon.bus.log(f"Approval {item_id} is no longer pending", "warn")
        return
    await daemon.bus.publish({"event": "APPROVAL_RESOLVED", "item": claimed})   # shows "installing"
    res = await run_install(claimed["app_id"])
    ok = bool(res.get("ok"))
    updated = daemon.approvals.update(item_id, status="installed" if ok else "failed",
                                      result=res.get("message", ""), error=None if ok else res.get("message", ""))
    await daemon.bus.publish({"event": "APPROVAL_RESOLVED", "item": updated})
    await daemon.bus.log(res.get("message") or ("Installed." if ok else "Install failed."), "info" if ok else "warn")


def make_router(get_daemon: Callable[[], Any]):
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse

    router = APIRouter(prefix="/apps", tags=["get-apps"])

    async def body_of(request: Request) -> dict[str, Any]:
        try:
            b = await request.json()
        except ValueError:
            return {}
        return b if isinstance(b, dict) else {}

    @router.post("/install-request")
    async def install_request(request: Request) -> Any:
        b = await body_of(request)
        try:
            item = await request_install(get_daemon(), str(b.get("app_id") or ""), str(b.get("why") or ""),
                                         str(b.get("requested_by") or ""))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"request": item}

    @router.get("/install-request/{item_id}")
    async def install_request_status(item_id: str) -> Any:
        item = get_daemon().approvals.get(item_id)
        if item is None or item.get("kind") != KIND:
            return JSONResponse({"error": "No such install request."}, status_code=404)
        return {"request": _public(item)}

    @router.post("/notify")
    async def notify(request: Request) -> Any:
        b = await body_of(request)
        title = " ".join(str(b.get("title") or "").split())[:120]
        body = " ".join(str(b.get("body") or "").split())[:600]
        if not title:
            return JSONResponse({"error": "title is required"}, status_code=400)
        try:
            import alerts
            alerts.emit("system", title, body, ref="get-apps")
        except Exception:
            pass
        d = get_daemon()
        try:
            await d.bus.log(f"Get apps: {title}. {body}")
        except Exception:
            pass
        return {"ok": True}

    return router


# =====================================================================================  MCP tools
def mcp_tools(tools: Any) -> list[Any]:
    """Tool definitions for mcp_tools.OrionTools (imported lazily there)."""
    from mcp_tools import DaemonUnavailable, Tool, ToolError, _schema

    S = {"type": "string"}

    async def apps_install(a: dict[str, Any]) -> str:
        app_id = a["app_id"].strip()
        if not valid_app_id(app_id):
            raise ToolError("app_id must be a Flathub app id, like com.spotify.Client.")
        try:
            status, data = await tools.backend.call("POST", "/apps/install-request",
                                                    {"app_id": app_id, "why": a.get("why") or "",
                                                     "requested_by": a.get("requested_by") or "Coding agent (MCP)"})
        except DaemonUnavailable:
            raise ToolError("Installing an app needs the user's approval, but Orion isn't running so it can't ask. "
                            "Nothing was installed.")
        if status >= 400:
            raise ToolError(str(data.get("error") or f"Orion refused it ({status})."))
        r = data.get("request") or {}
        return (f"Asked the user: “Install {r.get('name') or app_id}?” (request {r.get('id')}). Nothing is installed "
                f"until they approve it in Orion. Check with apps_request_status(request_id=\"{r.get('id')}\").")

    async def apps_request_status(a: dict[str, Any]) -> str:
        try:
            status, data = await tools.backend.call("GET", f"/apps/install-request/{a['request_id']}")
        except DaemonUnavailable:
            raise ToolError("Orion isn't running, so the request can't be checked.")
        if status != 200:
            raise ToolError(str(data.get("error") or "Unknown request."))
        r = data.get("request") or {}
        words = {"pending": "still waiting for the user's approval", "sending": "approved, installing now",
                 "installed": "approved and installed", "failed": "approved, but the install failed",
                 "rejected": "declined by the user"}
        line = f"{r.get('name') or r.get('app_id')}: {words.get(r.get('status'), r.get('status'))}."
        return line + (f" {r['result']}" if r.get("result") else "")

    return [
        Tool("apps_install", "Ask to install an app",
             "Ask the user to install a desktop app from Flathub (Get apps). This only creates an approval card; "
             "nothing is installed unless the user approves it.", "approval",
             _schema({"app_id": {**S, "description": "Flathub app id, e.g. org.blender.Blender"},
                      "why": {**S, "description": "One sentence on why it's needed"},
                      "requested_by": {**S, "description": "Who is asking, e.g. Claude"}}, ("app_id",)),
             apps_install),
        Tool("apps_request_status", "Check an install request",
             "Whether an app install request was approved, installed, declined or is still waiting.", "read",
             _schema({"request_id": S}, ("request_id",)), apps_request_status),
    ]
