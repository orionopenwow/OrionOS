"""DB Time Machine (Toolkit 12): snapshot before writes, plain-words row diff after, rollback per table or whole DB
only after approval, staging hosts only. A sqlite-backed fake stands in for MySQL (no MySQL server here)."""
import asyncio
import sqlite3
import time

import pytest

import boardroom as br
import db_time_machine as dbtm


def q(v):
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


class SqliteClient:
    """Speaks the DbClient protocol; its dump looks like mysqldump's (same table markers)."""

    def __init__(self, path):
        self.path, self.database = str(path), "world"

    def _db(self):
        return sqlite3.connect(self.path)

    def tables(self):
        with self._db() as db:
            return [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                             "AND name NOT LIKE 'sqlite_%' ORDER BY name")]

    def primary_key(self, t):
        with self._db() as db:
            cols = [(r[5], r[1]) for r in db.execute(f'PRAGMA table_info("{t}")') if r[5]]
        return [c for _, c in sorted(cols)]

    def rows(self, t):
        db = self._db()
        cur = db.execute(f'SELECT * FROM "{t}"')
        return [d[0] for d in cur.description], cur.fetchall()

    def dump(self, tables, out):
        parts = ["-- MySQL dump (fake)\n/*!40101 SET NAMES utf8mb4 */;\n"]
        with self._db() as db:
            for t in tables or self.tables():
                ddl = db.execute("SELECT sql FROM sqlite_master WHERE name=?", (t,)).fetchone()[0]
                parts.append(f"\n--\n-- Table structure for table `{t}`\n--\n\nDROP TABLE IF EXISTS \"{t}\";\n{ddl};\n")
                parts.append(f"\n--\n-- Dumping data for table `{t}`\n--\n\n")
                for r in db.execute(f'SELECT * FROM "{t}"'):
                    parts.append(f"INSERT INTO \"{t}\" VALUES ({','.join(q(v) for v in r)});\n")
        parts.append("/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;\n-- Dump completed\n")
        out.write_text("".join(parts))

    def restore(self, sql_text):
        with self._db() as db:
            db.executescript(sql_text)


@pytest.fixture()
def world(tmp_path):
    path = tmp_path / "world.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE creature_loot_template (entry INTEGER, item INTEGER, chance REAL, PRIMARY KEY(entry, item));
            CREATE TABLE quest_template (id INTEGER PRIMARY KEY, title TEXT);
            CREATE TABLE log_lines (line TEXT);
        """)
        db.executemany("INSERT INTO creature_loot_template VALUES (?,?,?)",
                       [(1, i, 0.5) for i in range(1, 21)])
        db.executemany("INSERT INTO quest_template VALUES (?,?)", [(i, f"Quest {i}") for i in range(1, 6)])
        db.executemany("INSERT INTO log_lines VALUES (?)", [("a",), ("b",)])
    return path


def settings_for(path, staging=True):
    def settings(name):
        if not staging:
            raise dbtm.DbtmError("Connection 'world' points at prod.example, which isn't in your staging_hosts list.")
        return {"name": "world", "host": "staging.lan", "port": 3306, "user": "u", "password": "", "database": "world"}
    return settings


def machine(tmp_path, world, board=None, opts=None, staging=True):
    notes = []
    tm = dbtm.TimeMachine(tmp_path / "data", board=board, settings=settings_for(world, staging),
                          client_factory=lambda conn: SqliteClient(world), options=lambda: opts or {},
                          notify=lambda t, b: notes.append((t, b)))
    tm.notes = notes
    return tm


def run(c):
    return asyncio.run(c)


def test_tables_touched():
    assert dbtm.tables_touched("UPDATE creature_loot_template SET chance=1 WHERE entry=1") == ["creature_loot_template"]
    assert dbtm.tables_touched("insert ignore into `world`.`quest_template` (id) values (9)") == ["quest_template"]
    assert dbtm.tables_touched("DELETE FROM log_lines WHERE line='a'") == ["log_lines"]
    assert dbtm.tables_touched("ALTER TABLE quest_template ADD COLUMN x INT") == ["quest_template"]
    assert dbtm.tables_touched("UPDATE a JOIN b ON a.id=b.id SET a.x=1") is None      # can't tell: snapshot all
    assert dbtm.tables_touched("CALL do_everything()") is None


def test_split_dump_keeps_each_table_restorable(tmp_path, world):
    out = tmp_path / "d.sql"
    SqliteClient(world).dump(None, out)
    header, parts, footer = dbtm.split_dump(out.read_text())
    assert set(parts) == {"creature_loot_template", "quest_template", "log_lines"}
    assert "SET NAMES" in header and footer.startswith("/*!40103")
    assert "quest_template" not in parts["log_lines"] and "INSERT INTO \"log_lines\"" in parts["log_lines"]


def test_snapshot_then_plain_words_diff(tmp_path, world):
    tm = machine(tmp_path, world)
    meta = tm.snapshot_sync("world", reason="before the loot change")
    assert meta["whole"] and meta["rows"]["creature_loot_template"] == 20
    with sqlite3.connect(world) as db:
        db.execute("UPDATE creature_loot_template SET chance=0.9 WHERE item <= 12")
        db.executemany("INSERT INTO creature_loot_template VALUES (2,?,0.1)", [(1,), (2,), (3,)])
        db.execute("DELETE FROM quest_template WHERE id=5")
        db.execute("INSERT INTO log_lines VALUES ('c')")
        db.execute("CREATE TABLE new_stuff (id INTEGER PRIMARY KEY)")
    res = tm.compare_sync(meta["id"])
    assert res["tables"]["creature_loot_template"]["changed"] == 12
    assert res["text"] == ("creature_loot_template: 12 rows changed, 3 added; log_lines: 1 row added; "
                           "new_stuff: new table; quest_template: 1 row removed")
    assert res["tables"]["quest_template"]["examples"]["removed"] == ["[5]"]


def test_only_staging_hosts(tmp_path, world):
    tm = machine(tmp_path, world, staging=False)
    with pytest.raises(dbtm.DbtmError):
        tm.snapshot_sync("world")
    assert tm.snapshots() == []


def test_rollback_one_table_waits_for_approval_and_can_be_undone(tmp_path, world):
    board = br.Boardroom(tmp_path / "board")
    tm = machine(tmp_path, world, board=board)
    jobs = []
    board.executors["dbtm.rollback"] = lambda d: tm.execute_rollback(d, jobs.append)
    meta = tm.snapshot_sync("world")
    with sqlite3.connect(world) as db:
        db.execute("UPDATE creature_loot_template SET chance=0")
        db.execute("UPDATE quest_template SET title='changed'")
    res = run(tm.request_rollback(meta["id"], "creature_loot_template"))
    assert res["state"] == "waiting"
    d = board.pending()[0]
    assert d["task_type"] == "dbtm.rollback" and br.TASK_TYPES["dbtm.rollback"].max_level == 1
    with sqlite3.connect(world) as db:                       # nothing changed before the approval
        assert db.execute("SELECT MAX(chance) FROM creature_loot_template").fetchone()[0] == 0

    async def approve():
        await board.verdict(d["id"], "approve", br.packet_hash(d["packet"]))
        for j in jobs:
            await j
    run(approve())
    with sqlite3.connect(world) as db:
        assert db.execute("SELECT MIN(chance) FROM creature_loot_template").fetchone()[0] == 0.5     # back
        assert db.execute("SELECT DISTINCT title FROM quest_template").fetchall() == [("changed",)]  # untouched
    assert tm.notes[-1][0] == "Rollback done"
    safety = [m for m in tm.snapshots("world") if m["kind"] == "before_rollback"]
    assert len(safety) == 1 and safety[0]["tables"] == ["creature_loot_template"]


def test_whole_database_rollback(tmp_path, world):
    tm = machine(tmp_path, world)
    meta = tm.snapshot_sync("world")
    with sqlite3.connect(world) as db:
        db.execute("DELETE FROM quest_template")
        db.execute("DELETE FROM log_lines")
    res = tm.rollback_sync(meta["id"])
    assert "Undo point" in res["message"]
    assert tm.compare_sync(meta["id"])["text"] == "nothing changed"


def test_retention(tmp_path, world):
    tm = machine(tmp_path, world, opts={"keep_last": 2, "keep_days": 0.0000001})
    for _ in range(4):
        tm.snapshot_sync("world", ["log_lines"])
        time.sleep(0.01)
    assert len(tm.snapshots("world")) == 2


def test_mcp_write_path_snapshots_first(tmp_path, world, monkeypatch):
    import mcp_routes
    tm = machine(tmp_path, world)
    monkeypatch.setattr(dbtm, "CURRENT", tm)
    monkeypatch.setattr(mcp_routes, "mysql_connection_settings", lambda cfg, name: settings_for(world)(name))

    def write(conn, sql):
        assert tm.snapshots("world"), "the snapshot must exist before the write"
        with sqlite3.connect(world) as db:
            return {"rows_affected": db.execute(sql).rowcount}
    monkeypatch.setattr(mcp_routes, "mysql_write", write)
    d = {"packet": {"details": {"connection": "world",
                                "sql": "UPDATE creature_loot_template SET chance=1 WHERE item<=3"}}}
    res = run(mcp_routes._exec_db_write(d))
    assert res["rows_affected"] == 3 and res["changes"] == "creature_loot_template: 3 rows changed"
    snap = tm.get(res["snapshot"])
    assert snap["tables"] == ["creature_loot_template"] and not snap["whole"]
    assert snap["diff_text"] == "creature_loot_template: 3 rows changed"
    assert tm.notes and "3 rows changed" in tm.notes[-1][1]


def test_failed_snapshot_stops_the_write(tmp_path, world, monkeypatch):
    import mcp_routes
    from mcp_tools import ToolError

    class Broken(SqliteClient):
        def dump(self, tables, out):
            raise OSError("disk full")
    tm = dbtm.TimeMachine(tmp_path / "data", settings=settings_for(world), client_factory=lambda c: Broken(world),
                          options=lambda: {})
    monkeypatch.setattr(dbtm, "CURRENT", tm)
    monkeypatch.setattr(mcp_routes, "mysql_connection_settings", lambda cfg, name: settings_for(world)(name))
    wrote = []
    monkeypatch.setattr(mcp_routes, "mysql_write", lambda c, s: wrote.append(s))
    with pytest.raises(ToolError) as e:
        run(mcp_routes._exec_db_write({"packet": {"details": {"connection": "world", "sql": "DELETE FROM log_lines"}}}))
    assert "Nothing was changed" in str(e.value) and wrote == []
    assert tm.snapshots() == []                              # no half-written snapshot left behind


def test_no_dump_tool_means_no_snapshot_but_the_approved_write_still_runs(tmp_path, world, monkeypatch):
    tm = dbtm.TimeMachine(tmp_path / "data", settings=settings_for(world), options=lambda: {},
                          available=lambda: (False, "mysqldump isn't installed on this computer"))
    guard = run(tm.before_write("world", "DELETE FROM log_lines"))
    assert guard == {"skipped": "No snapshot was taken: mysqldump isn't installed on this computer."}
    assert run(tm.after_write(guard)) == {"time_machine": guard["skipped"]}


def test_what_changed_today(tmp_path, world, monkeypatch):
    tm = machine(tmp_path, world)
    monkeypatch.setattr(tm, "connections", lambda: [{"name": "world", "database": "world", "host": "staging.lan"},
                                                    {"name": "chars", "database": "characters", "host": "staging.lan"}])
    assert tm.find_connection("world") == "world" and tm.find_connection("characters") == "chars"
    assert "hasn't taken a snapshot" in run(tm.changes_today("world"))
    tm.snapshot_sync("world")
    with sqlite3.connect(world) as db:
        db.execute("INSERT INTO quest_template VALUES (9, 'New quest')")
    text = run(tm.changes_today("world"))
    assert text.startswith("Since ") and "quest_template: 1 row added" in text
