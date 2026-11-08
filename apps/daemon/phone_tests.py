"""The Test Center on the phone (Orion Mobile), on the lab's phone port 9121. Registered from phone_api.register.

    GET  /phone/tests                 (read key)  same shape as GET /tests, only items the phone takes part in
                                                  (where = "phone" or "both"), with the phone's own "next"
    POST /phone/tests/{id}/result     (read key)  {"status": "pass"|"fail"|"skip"|"todo", "note"?, "facts"?: {}}

Reporting a result never approves anything: the fingerprint test card is approved through the normal signed
verdict route, and the Test Center only sees that it happened. A phone that can call these routes has reached
the PC, so "Your phone reaches this PC" is marked passed as a side effect.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

log = logging.getLogger("orion.phone_tests")

BODY_MAX = 8 * 1024


def _reached(tc: Any) -> None:
    r = tc.state["results"].get("phone_reaches") or {}
    if r.get("status") != "pass":
        tc._set("phone_reaches", "pass", "Your phone reached this PC just now.", by="phone")
        tc.save()


def hook(hub: Any, app: Any, phone_auth: Any, json_body: Any) -> None:
    import test_center

    def tc() -> Any:
        return test_center.center(hub)

    @app.get("/phone/tests")
    async def phone_tests(request: Request):
        who, err = await phone_auth(request, "read")
        if err is not None:
            return err
        center = tc()
        _reached(center)
        return await center.view_fresh(where="phone")

    @app.post("/phone/tests/{item_id}/result")
    async def phone_test_result(item_id: str, request: Request):
        who, err = await phone_auth(request, "read", BODY_MAX)
        if err is not None:
            return err
        data = await json_body(request)
        if data is None:
            return JSONResponse({"error": "bad request"}, status_code=400)
        center = tc()
        _reached(center)
        it = center.by_id.get(item_id)
        if it is None or it.where not in ("phone", "both"):
            return JSONResponse({"error": "There's no phone test with that name."}, status_code=404)
        try:
            item = center.set_result(item_id, str(data.get("status") or ""), data.get("note"), by="phone",
                                     facts=data.get("facts"))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"item": item}
