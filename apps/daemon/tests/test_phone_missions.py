"""Send work to the PC from the phone: /phone/missions, /phone/mission and the act routes (with a fake missions)."""
import json
import types

import pytest
from fastapi.testclient import TestClient

import boardroom as br
import freeze
import phone_api
import phone_missions as pm
from tests.test_phone_v2 import FakeHub
from tests.test_phones import FakePhone, paired


SECRET = "sk-" + "proj-" + "Q7f3Kp9Zx2Lm8Vt4Rb6Nc1Hd5Wy0Js"  # built at runtime: not a real key


class FakeMissions:
    def __init__(self):
        self.made = []
        self.acts = []
        self.repos_checked = []
        self.items = [
            {"id": "m1", "title": "Fix the login page", "repo": "orion", "agent": "claude", "state": "running",
             "cost_usd": 1.234567, "budget_usd": 5, "started": 1_800_000_000, "branch": "mission/m1",
             "summary": "Working on it. key " + SECRET, "extra": "not sent"},
            {"id": "m2", "title": "Old one", "repo": None, "agent": "codex", "state": "done", "cost_usd": 0.5,
             "budget_usd": None, "started": None, "branch": "", "summary": ""},
        ]

    def list_missions(self):
        return self.items

    def allowed_repo(self, repo):
        self.repos_checked.append(repo)
        if repo == "outside":
            raise ValueError("outside isn't inside a folder Orion lets agents use.")
        return repo

    def create_mission(self, title, repo=None, agent=None, budget_usd=None, race=1, source="hud", phone_key=None):
        self.made.append(dict(title=title, repo=repo, agent=agent, budget_usd=budget_usd, race=race, source=source,
                              phone_key=phone_key))
        return {"id": "m3", "title": title, "repo": repo or "", "agent": agent or "claude", "state": "queued",
                "cost_usd": 0, "budget_usd": budget_usd, "started": None, "branch": "", "summary": ""}

    def act(self, mission_id, op, phone_key=None):
        if mission_id == "gone":
            raise KeyError(mission_id)
        self.acts.append((mission_id, op))
        return {"ok": True, "mission": {**self.items[0], "state": {"pause": "paused"}.get(op, "running")}}


@pytest.fixture()
def lab(tmp_path):
    fake = FakeMissions()
    pm.set_module(fake)
    freeze.set_dir(tmp_path / "freeze")
    board = br.Boardroom(tmp_path / "board")
    board.remote_hooks.append(phone_api.register(FakeHub(tmp_path)))
    client = TestClient(br.build_remote_app(board))
    phone = FakePhone()
    paired(board, client, phone)
    yield fake, client, phone
    pm.set_module(None)
    freeze.set_dir(None)


def post(client, phone, key, path, data=None):
    body = json.dumps(data or {}).encode()
    return client.post(path, content=body, headers=phone.headers(key, "POST", path, body))


def test_unsigned_gets_nothing(lab):
    fake, client, phone = lab
    assert client.get("/phone/missions").status_code == 401
    assert client.post("/phone/mission", json={"title": "x"}).status_code == 401
    assert fake.made == []


def test_list_shows_board_states_and_cost_only_known_fields(lab):
    fake, client, phone = lab
    r = client.get("/phone/missions", headers=phone.headers(phone.read, "GET", "/phone/missions"))
    assert r.status_code == 200
    j = r.json()
    assert j["available"] is True and [m["id"] for m in j["missions"]] == ["m1", "m2"]
    m = j["missions"][0]
    assert set(m) == set(pm.KEYS) and m["cost_usd"] == pytest.approx(1.2346) and m["budget_usd"] == 5
    assert SECRET not in m["summary"] and m["summary"].startswith("Working on it")       # secret-looking text is hidden
    assert j["missions"][1]["repo"] == "" and j["missions"][1]["budget_usd"] is None


def test_create_from_phone_is_queued_with_source_phone(lab):
    fake, client, phone = lab
    r = post(client, phone, phone.read, "/phone/mission", {"title": "  Build a landing page ", "repo": "site",
                                                            "budget_usd": "3.5"})
    assert r.status_code == 200, r.text
    assert fake.made == [dict(title="Build a landing page", repo="site", agent=None, budget_usd=3.5, race=1,
                              source="phone", phone_key="read")]
    assert r.json()["mission"]["state"] == "queued" and "queued" in r.json()["message"]
    assert post(client, phone, phone.read, "/phone/mission", {"title": " "}).status_code == 400
    assert post(client, phone, phone.read, "/phone/mission", {"title": "x", "budget_usd": -1}).status_code == 400
    assert post(client, phone, phone.read, "/phone/mission", {"title": "x", "budget_usd": "lots"}).status_code == 400
    assert len(fake.made) == 1


def test_pause_stop_resume_with_read_key(lab):
    fake, client, phone = lab
    for op in ("pause", "stop", "resume"):
        r = post(client, phone, phone.read, f"/phone/mission/m1/{op}")
        assert r.status_code == 200, r.text
    assert fake.acts == [("m1", "pause"), ("m1", "stop"), ("m1", "resume")]
    assert post(client, phone, phone.read, "/phone/mission/m1/merge").status_code == 400
    assert post(client, phone, phone.read, "/phone/mission/gone/pause").status_code == 404


def test_approve_review_and_reject_need_the_fingerprint_key(lab):
    fake, client, phone = lab
    for op in ("approve_review", "reject"):
        assert post(client, phone, phone.read, f"/phone/mission/m1/{op}").status_code == 401
    assert fake.acts == []
    for op in ("approve_review", "reject"):
        r = post(client, phone, phone.sign, f"/phone/mission/m1/{op}")
        assert r.status_code == 200, r.text
    assert fake.acts == [("m1", "approve_review"), ("m1", "reject")]


def test_frozen_refuses_new_work_and_resume_but_allows_stop(lab):
    fake, client, phone = lab
    freeze._write({"frozen": True, "why": "test"})
    try:
        assert post(client, phone, phone.read, "/phone/mission", {"title": "Build it"}).status_code == 409
        assert post(client, phone, phone.read, "/phone/mission/m1/resume").status_code == 409
        assert post(client, phone, phone.read, "/phone/mission/m1/stop").status_code == 200
    finally:
        freeze._write({"frozen": False})
    assert fake.made == []


def test_no_mission_control_answers_plainly(lab, monkeypatch):
    fake, client, phone = lab
    pm.set_module(None)
    monkeypatch.setattr(pm.importlib, "import_module", lambda name: (_ for _ in ()).throw(ImportError(name)))
    r = client.get("/phone/missions", headers=phone.headers(phone.read, "GET", "/phone/missions"))
    assert r.status_code == 200 and r.json() == {"available": False, "missions": [], "message": pm.NOT_HERE}
    r = post(client, phone, phone.read, "/phone/mission", {"title": "Build it"})
    assert r.status_code == 503 and r.json()["error"] == pm.NOT_HERE
    assert post(client, phone, phone.sign, "/phone/mission/m1/approve_review").json()["error"] == pm.NOT_HERE
    assert pm.for_home() == []


def test_words_hand_work_to_the_pc(lab):
    fake, client, phone = lab
    assert pm.parse_words("Orion, have the PC build a landing page.") == "Build a landing page"
    assert pm.parse_words("get my computer to fix the tests") == "Fix the tests"
    assert pm.parse_words("send this to the PC: write release notes") == "Write release notes"
    assert pm.parse_words("what's the weather") is None
    assert pm.parse_words("have the PC") is None
    r = post(client, phone, phone.read, "/phone/chat", {"text": "Orion, have the PC build a landing page"})
    assert r.status_code == 200 and "Mission Control" in r.json()["reply"]
    assert fake.made[-1]["title"] == "Build a landing page" and fake.made[-1]["source"] == "phone"
    r = post(client, phone, phone.read, "/phone/chat", {"text": "status"})
    assert r.json()["reply"] == "you said status" and len(fake.made) == 1


def test_home_robots_get_running_missions_only(lab):
    fake, client, phone = lab
    home = pm.for_home()
    assert [m["id"] for m in home] == ["m1"] and home[0]["cost_usd"] == pytest.approx(1.2346)


def test_act_error_dict_becomes_409(lab):
    fake, client, phone = lab
    pm.set_module(types.SimpleNamespace(act=lambda i, op: {"ok": False, "error": "Not waiting for a review."}))
    r = post(client, phone, phone.sign, "/phone/mission/m1/approve_review")
    assert r.status_code == 409 and r.json()["error"] == "Not waiting for a review."


def test_big_budget_needs_the_fingerprint_key(lab):
    """Money rule: up to $5 the read key can queue work; a bigger budget is spending, so only the sign key."""
    fake, client, phone = lab
    r = post(client, phone, phone.read, "/phone/mission", {"title": "Big job", "budget_usd": 50})
    assert r.status_code == 401 and "fingerprint" in r.json()["error"]
    assert fake.made == []
    r = post(client, phone, phone.sign, "/phone/mission", {"title": "Big job", "budget_usd": 50})
    assert r.status_code == 200, r.text
    assert fake.made[0]["budget_usd"] == 50.0
    assert post(client, phone, phone.read, "/phone/mission", {"title": "Small", "budget_usd": 5}).status_code == 200


# ------------------------------------------------------------------ review fixes (2, 3, 4)
class BoardFake(FakeMissions):
    """Created missions land on the board, so the money total sees them."""

    def __init__(self):
        super().__init__()
        self.items = []
        self.n = 0

    def create_mission(self, title, repo=None, agent=None, budget_usd=None, race=1, source="hud", phone_key=None):
        super().create_mission(title, repo, agent, budget_usd, race, source, phone_key)
        self.n += 1
        m = {"id": f"p{self.n}", "title": title, "state": "queued", "budget_usd": budget_usd, "source": source,
             "phone_key": phone_key}
        self.items.append(m)
        return m

    def act(self, mission_id, op, phone_key=None):
        self.acts.append((mission_id, op, phone_key))
        m = next(x for x in self.items if x["id"] == mission_id)
        if op == "resume":
            m.update(state="queued", phone_key=phone_key or m.get("phone_key"))
        return {"ok": True, "mission": m}


@pytest.fixture()
def board_lab(lab):
    fake, client, phone = lab
    b = BoardFake()
    pm.set_module(b)
    return b, client, phone


def test_read_key_cannot_stop_work_waiting_for_review(board_lab):
    fake, client, phone = board_lab
    fake.items.append({"id": "r1", "title": "Done work", "state": "review", "budget_usd": 1, "source": "hud"})
    r = post(client, phone, phone.read, "/phone/mission/r1/stop")
    assert r.status_code == 409 and "fingerprint" in r.json()["error"]
    assert fake.acts == []   # missions.act (which would throw the work away) was never called
    assert post(client, phone, phone.sign, "/phone/mission/r1/reject").status_code == 200
    assert fake.acts == [("r1", "reject", None)]


def test_phone_mission_repo_goes_through_the_hud_folder_rule(board_lab):
    fake, client, phone = board_lab
    r = post(client, phone, phone.read, "/phone/mission", {"title": "Build it", "repo": "outside", "budget_usd": 1})
    assert r.status_code == 409 and "folder" in r.json()["error"]
    assert fake.made == [] and fake.repos_checked == ["outside"]
    # a missions module that can't check folders: a repo is refused rather than passed through
    pm.set_module(types.SimpleNamespace(list_missions=lambda: [], create_mission=lambda *a, **k: 1 / 0))
    assert pm.create("Build it", "/etc")[0] == 409


def test_read_key_money_total_can_not_be_beaten_by_count(board_lab):
    fake, client, phone = board_lab
    for _ in range(2):
        assert post(client, phone, phone.read, "/phone/mission", {"title": "Job", "budget_usd": 2.5}).status_code == 200
    r = post(client, phone, phone.read, "/phone/mission", {"title": "One more", "budget_usd": 1})
    assert r.status_code == 401 and r.json()["needs"] == "fingerprint"
    # chat asks for the router's usual budget: also counted (unknown default -> the read limit)
    r = post(client, phone, phone.read, "/phone/chat", {"text": "Orion, have the PC build a landing page"})
    assert "fingerprint" in r.json()["reply"] and len(fake.made) == 2
    # the fingerprint key can go over
    assert post(client, phone, phone.sign, "/phone/mission", {"title": "Signed", "budget_usd": 1}).status_code == 200
    assert fake.made[-1]["phone_key"] == "sign"
    # signed missions don't eat the read-key total; finished ones free it
    fake.items[0]["state"] = "stopped"
    assert pm.open_read_total(fake) == 2.5


def test_resume_over_the_total_needs_the_fingerprint(board_lab):
    fake, client, phone = board_lab
    assert post(client, phone, phone.read, "/phone/mission", {"title": "A", "budget_usd": 4}).status_code == 200
    fake.items.append({"id": "s1", "title": "Old", "state": "failed", "budget_usd": 4, "source": "hud"})
    r = post(client, phone, phone.read, "/phone/mission/s1/resume")
    assert r.status_code == 401 and r.json()["needs"] == "fingerprint"
    assert not any(a[0] == "s1" for a in fake.acts)
    r = post(client, phone, phone.sign, "/phone/mission/s1/resume")
    assert r.status_code == 200, r.text
    assert fake.acts[-1] == ("s1", "resume", "sign")
    # under the total, a read-key resume is counted from then on
    fake.items[0]["state"] = "stopped"
    fake.items[1]["state"] = "stopped"
    assert post(client, phone, phone.read, "/phone/mission/p1/resume").status_code == 200
    assert fake.acts[-1] == ("p1", "resume", "read") and pm.open_read_total(fake) == 4


def test_real_mission_control_records_the_phone_key_and_default_budget(tmp_path, monkeypatch):
    import missions as M

    mc = M.MissionControl(tmp_path / "d")
    monkeypatch.setattr(M, "_MC", mc)
    mc.missions["x"] = {"id": "x", "state": "stopped", "title": "t", "budget_usd": 1, "agent_label": "a",
                        "created": 1}
    monkeypatch.setattr(mc, "_poke", lambda: None)
    r = M.act("x", "resume", phone_key="read")
    assert r["ok"] and r["mission"]["phone_key"] == "read" and r["mission"]["state"] == "queued"
    assert "phone_key" in M.MissionControl.public({})


def test_phone_sees_only_the_folder_name():
    import phone_missions as pm
    m = pm.clean({"id": "m-1", "title": "t", "repo": "C:\\Users\\someone\\Orion Projects\\Star Catcher", "state": "queued"})
    assert m["repo"] == "Star Catcher"
    assert pm.clean({"id": "m-2", "repo": "/home/someone/code/orion/"})["repo"] == "orion"
