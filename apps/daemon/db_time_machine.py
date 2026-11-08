"""DB Time Machine (Toolkit tool 12, docs/ORION_TOOLKIT_PLAN.md): undo for the MySQL staging database.

Before a migration, an agent's approved database write (the MCP `mysql_query` write path, mcp_routes.py) or a
bulk change, Orion takes a consistent snapshot: one `mysqldump --single-transaction` of the affected tables (or the
whole database when it can't tell), plus a fingerprint of every row. Afterwards it says what changed in plain
words ("creature_loot_template: 12 rows changed, 3 added"). One tap on your phone rolls a table, or the whole
database, back to a snapshot; Orion snapshots again first, so a rollback can itself be undone.

Only databases you marked as staging work: the connections and `staging_hosts` from ~/.config/orion/mcp.json, the
same list the MCP tools use. Settings live there too, all optional:

    "mysql": {..., "time_machine": {"keep_last": 30, "keep_days": 14, "max_rows_per_table": 500000}}

Snapshots are stored in data_dir/db_time_machine/<connection>/<snapshot>/ (dump.sql.gz, rows.json.gz, meta.json).
Rolling back is a boardroom decision (`dbtm.rollback`, sensitive: it always asks).
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Protocol

log = logging.getLogger("orion.dbtm")

TASK_ROLLBACK = "dbtm.rollback"
KEEP_LAST = 30
KEEP_DAYS = 14
MAX_ROWS = 500_000
EXAMPLES = 5
STRUCT_MARK = re.compile(r"^-- Table structure for table `([^`]+)`", re.M)
FOOTER_MARK = re.compile(r"^/\*!40103 SET TIME_ZONE=@OLD_TIME_ZONE \*/;", re.M)

CURRENT: Optional["TimeMachine"] = None   # set by the daemon; used by the MCP write path in mcp_routes.py


class DbtmError(Exception):
    pass


class DbClient(Protocol):
    database: str

    def tables(self) -> list[str]: ...
    def primary_key(self, table: str) -> list[str]: ...
    def rows(self, table: str) -> tuple[list[str], Iterable[tuple]]: ...
    def dump(self, tables: Optional[list[str]], out: Path) -> None: ...
    def restore(self, sql_text: str) -> None: ...


# =====================================================================================  the real MySQL client
class MysqlClient:
    """mysqldump / mysql for snapshots and restores, PyMySQL for reading rows. The password goes to the
    command-line tools through a private temporary options file, never on the command line."""

    def __init__(self, conn: dict[str, Any]) -> None:
        if not conn.get("database"):
            raise DbtmError(f"Connection {conn.get('name')!r} has no database set, so there's nothing to snapshot.")
        self.conn = conn
        self.database = str(conn["database"])

    @staticmethod
    def available() -> tuple[bool, str]:
        if not shutil.which("mysqldump") or not shutil.which("mysql"):
            return False, "mysqldump isn't installed on this computer"
        try:
            import pymysql  # noqa: F401
        except ImportError:
            return False, "the PyMySQL package isn't installed"
        return True, ""

    def _db(self):
        import pymysql
        c = self.conn
        return pymysql.connect(host=c["host"], port=c["port"], user=c["user"], password=c["password"],
                               database=self.database, connect_timeout=8, read_timeout=600, charset="utf8mb4")

    def tables(self) -> list[str]:
        db = self._db()
        try:
            cur = db.cursor()
            cur.execute("SHOW FULL TABLES WHERE Table_type = 'BASE TABLE'")
            return [r[0] for r in cur.fetchall()]
        finally:
            db.close()

    def primary_key(self, table: str) -> list[str]:
        db = self._db()
        try:
            cur = db.cursor()
            cur.execute("SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=%s AND "
                        "TABLE_NAME=%s AND CONSTRAINT_NAME='PRIMARY' ORDER BY ORDINAL_POSITION",
                        (self.database, table))
            return [r[0] for r in cur.fetchall()]
        finally:
            db.close()

    def rows(self, table: str) -> tuple[list[str], Iterable[tuple]]:
        import pymysql
        db = self._db()
        cur = db.cursor(pymysql.cursors.SSCursor)
        cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
        cur.execute(f"SELECT * FROM `{table.replace('`', '``')}`")
        cols = [d[0] for d in cur.description or []]

        def gen() -> Iterable[tuple]:
            try:
                for r in cur:
                    yield r
            finally:
                try:
                    db.close()
                except Exception:
                    pass
        return cols, gen()

    def _options(self) -> str:
        fd, path = tempfile.mkstemp(prefix="orion-dbtm-", suffix=".cnf")
        with os.fdopen(fd, "w") as f:
            c = self.conn
            pw = str(c["password"]).replace("\\", "\\\\").replace('"', '\\"')
            f.write(f'[client]\nhost="{c["host"]}"\nport={int(c["port"])}\nuser="{c["user"]}"\npassword="{pw}"\n')
        os.chmod(path, 0o600)
        return path

    def dump(self, tables: Optional[list[str]], out: Path) -> None:
        opts = self._options()
        try:
            argv = ["mysqldump", f"--defaults-extra-file={opts}", "--single-transaction", "--quick", "--hex-blob",
                    "--skip-lock-tables", "--no-tablespaces", "--set-gtid-purged=OFF", self.database,
                    *(tables or [])]
            with open(out, "wb") as fh:
                proc = subprocess.run(argv, stdout=fh, stderr=subprocess.PIPE, timeout=3600)
            if proc.returncode != 0 and b"set-gtid-purged" in proc.stderr:   # MariaDB doesn't know that option
                argv.remove("--set-gtid-purged=OFF")
                with open(out, "wb") as fh:
                    proc = subprocess.run(argv, stdout=fh, stderr=subprocess.PIPE, timeout=3600)
            if proc.returncode != 0:
                raise DbtmError("mysqldump failed: " + proc.stderr.decode("utf-8", "replace").strip()[-300:])
        finally:
            os.unlink(opts)

    def restore(self, sql_text: str) -> None:
        opts = self._options()
        try:
            proc = subprocess.run(["mysql", f"--defaults-extra-file={opts}", self.database],
                                  input=sql_text.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=3600)
            if proc.returncode != 0:
                raise DbtmError("mysql couldn't load the snapshot: " +
                                proc.stderr.decode("utf-8", "replace").strip()[-300:])
        finally:
            os.unlink(opts)


# =====================================================================================  pure helpers
_TABLE = r"((?:`[^`]+`|\w+)(?:\.(?:`[^`]+`|\w+))?)"
WRITE_TARGETS = [
    re.compile(r"^\s*(?:INSERT|REPLACE)\s+(?:(?:LOW_PRIORITY|DELAYED|HIGH_PRIORITY|IGNORE)\s+)*(?:INTO\s+)?" + _TABLE, re.I),
    re.compile(r"^\s*UPDATE\s+(?:(?:LOW_PRIORITY|IGNORE)\s+)*" + _TABLE + r"\s+(?:AS\s+\w+\s+)?SET\b", re.I),
    re.compile(r"^\s*DELETE\s+(?:(?:LOW_PRIORITY|QUICK|IGNORE)\s+)*FROM\s+" + _TABLE + r"\s*(?:WHERE\b|$|;|ORDER\b|LIMIT\b)", re.I),
    re.compile(r"^\s*(?:ALTER|TRUNCATE|DROP)\s+TABLE\s+(?:IF\s+EXISTS\s+)?" + _TABLE + r"\s*(?:;|$|\s)", re.I),
    re.compile(r"^\s*TRUNCATE\s+" + _TABLE + r"\s*(?:;|$)", re.I),
]


def tables_touched(sql: str) -> Optional[list[str]]:
    """The one table a write statement changes, or None when Orion can't tell (then it snapshots everything)."""
    for rx in WRITE_TARGETS:
        m = rx.match(sql or "")
        if m:
            name = m.group(1).split(".")[-1].strip("`")
            return [name]
    return None


def split_dump(text: str) -> tuple[str, dict[str, str], str]:
    """(header, {table: its part}, footer) of a mysqldump file, so one table can be restored on its own."""
    marks = list(STRUCT_MARK.finditer(text))
    if not marks:
        return text, {}, ""
    foot = FOOTER_MARK.search(text, marks[-1].start())
    end = foot.start() if foot else len(text)
    header = text[:marks[0].start()]
    parts = {}
    for i, m in enumerate(marks):
        stop = marks[i + 1].start() if i + 1 < len(marks) else end
        # the "--\n" line just above the marker belongs to this table's part
        parts[m.group(1)] = text[m.start():stop]
    return header, parts, text[end:]


def _row_hash(row: Iterable[Any]) -> str:
    return hashlib.sha1(json.dumps(list(row), default=str, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def fingerprint_table(client: DbClient, table: str, max_rows: int = MAX_ROWS) -> dict[str, Any]:
    """{key: row hash} for every row (key = the primary key, or the row itself when the table has none)."""
    pk = client.primary_key(table)
    cols, rows = client.rows(table)
    idx = [cols.index(c) for c in pk if c in cols]
    keyed = bool(idx) and len(idx) == len(pk)
    out: dict[str, Any] = {}
    n = 0
    for r in rows:
        n += 1
        if n > max_rows:
            return {"too_big": True, "count": n, "pk": pk}
        h = _row_hash(r)
        if keyed:
            out[json.dumps([r[i] for i in idx], default=str)] = h
        else:
            out[h] = out.get(h, 0) + 1
    return {"keyed": keyed, "pk": pk, "rows": out, "count": n}


def diff_fingerprints(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Row-level changes between two fingerprints of one table."""
    if before.get("too_big") or after.get("too_big"):
        return {"too_big": True, "before": before.get("count"), "after": after.get("count")}
    b, a = before.get("rows", {}), after.get("rows", {})
    if before.get("keyed") and after.get("keyed"):
        added = [k for k in a if k not in b]
        removed = [k for k in b if k not in a]
        changed = [k for k in a if k in b and a[k] != b[k]]
        return {"added": len(added), "removed": len(removed), "changed": len(changed),
                "examples": {"added": added[:EXAMPLES], "removed": removed[:EXAMPLES], "changed": changed[:EXAMPLES]},
                "key": before.get("pk") or after.get("pk")}
    added = sum(max(0, n - b.get(h, 0)) for h, n in a.items())
    removed = sum(max(0, n - a.get(h, 0)) for h, n in b.items())
    return {"added": added, "removed": removed, "changed": 0, "examples": {}, "key": []}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def describe_table_diff(table: str, d: dict[str, Any]) -> Optional[str]:
    if d.get("new_table"):
        return f"{table}: new table"
    if d.get("dropped"):
        return f"{table}: table removed"
    if d.get("too_big"):
        if d.get("before") == d.get("after"):
            return None
        return f"{table}: {d.get('before')} rows before, {d.get('after')} now (too big to compare row by row)"
    parts = []
    if d.get("changed"):
        parts.append(f"{_plural(d['changed'], 'row')} changed")
    if d.get("added"):
        parts.append(f"{d['added']} added" if parts else f"{_plural(d['added'], 'row')} added")
    if d.get("removed"):
        parts.append(f"{d['removed']} removed" if parts else f"{_plural(d['removed'], 'row')} removed")
    return f"{table}: {', '.join(parts)}" if parts else None


def describe_diff(diff: dict[str, dict[str, Any]]) -> str:
    lines = [s for t in sorted(diff) if (s := describe_table_diff(t, diff[t]))]
    return "; ".join(lines) if lines else "nothing changed"


# =====================================================================================  the time machine
class TimeMachine:
    def __init__(self, data_dir: Path, board: Any = None,
                 settings: Optional[Callable[[Optional[str]], dict[str, Any]]] = None,
                 client_factory: Optional[Callable[[dict[str, Any]], DbClient]] = None,
                 options: Optional[Callable[[], dict[str, Any]]] = None,
                 notify: Optional[Callable[[str, str], None]] = None,
                 available: Optional[Callable[[], tuple[bool, str]]] = None) -> None:
        self.dir = Path(data_dir) / "db_time_machine"
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.dir, 0o700)   # snapshots hold game data: owner-only
        except OSError:
            pass
        self.board = board
        self.settings = settings or _staging_settings
        self.client_factory = client_factory or MysqlClient
        self.options = options or _options
        self.notify = notify or (lambda t, b: None)
        self.available = available or (MysqlClient.available if client_factory is None else (lambda: (True, "")))

    # ------------------------------------------------------------ storage
    def _conn_dir(self, name: str) -> Path:
        if not re.fullmatch(r"[\w.\-]{1,80}", name or ""):
            raise DbtmError("That connection name isn't valid.")
        return self.dir / name

    def snapshots(self, connection: Optional[str] = None) -> list[dict[str, Any]]:
        out = []
        dirs = [self._conn_dir(connection)] if connection else [p for p in self.dir.iterdir() if p.is_dir()]
        for cd in dirs:
            if not cd.is_dir():
                continue
            for sd in cd.iterdir():
                try:
                    out.append(json.loads((sd / "meta.json").read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    continue
        return sorted(out, key=lambda m: m["created"], reverse=True)

    def get(self, snap_id: str) -> Optional[dict[str, Any]]:
        if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[a-f0-9]{6}", snap_id or ""):
            return None
        for m in self.snapshots():
            if m["id"] == snap_id:
                return m
        return None

    def _snap_dir(self, meta: dict[str, Any]) -> Path:
        return self._conn_dir(meta["connection"]) / meta["id"]

    def _save_meta(self, meta: dict[str, Any]) -> None:
        (self._snap_dir(meta) / "meta.json").write_text(json.dumps(meta, indent=1, default=str), encoding="utf-8")

    def fingerprints(self, meta: dict[str, Any]) -> dict[str, Any]:
        with gzip.open(self._snap_dir(meta) / "rows.json.gz", "rt", encoding="utf-8") as f:
            return json.load(f)

    def dump_text(self, meta: dict[str, Any]) -> str:
        with gzip.open(self._snap_dir(meta) / "dump.sql.gz", "rt", encoding="utf-8", errors="replace") as f:
            return f.read()

    # ------------------------------------------------------------ snapshot
    def _client(self, connection: Optional[str]) -> tuple[dict[str, Any], DbClient]:
        conn = self.settings(connection)          # raises unless the host is on your staging list
        return conn, self.client_factory(conn)

    def snapshot_sync(self, connection: Optional[str], tables: Optional[list[str]] = None, reason: str = "",
                      kind: str = "manual") -> dict[str, Any]:
        conn, client = self._client(connection)
        all_tables = client.tables()
        wanted = [t for t in (tables or []) if t in all_tables]
        scope = wanted or all_tables
        now = time.time()
        sid = f"{datetime.fromtimestamp(now).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        meta = {"id": sid, "connection": conn["name"], "host": conn["host"], "database": client.database,
                "created": now, "reason": " ".join((reason or "").split())[:300], "kind": kind,
                "tables": scope, "whole": not wanted, "all_tables": all_tables}
        sd = self._conn_dir(conn["name"]) / sid
        sd.mkdir(parents=True, exist_ok=False)
        try:
            raw = sd / "dump.sql"
            client.dump(None if not wanted else wanted, raw)
            with open(raw, "rb") as src, gzip.open(sd / "dump.sql.gz", "wb") as dst:
                shutil.copyfileobj(src, dst)
            raw.unlink()
            max_rows = int(self.options().get("max_rows_per_table") or MAX_ROWS)
            fps = {t: fingerprint_table(client, t, max_rows) for t in scope}
            with gzip.open(sd / "rows.json.gz", "wt", encoding="utf-8") as f:
                json.dump(fps, f)
            meta["rows"] = {t: fp.get("count", 0) for t, fp in fps.items()}
            meta["size"] = (sd / "dump.sql.gz").stat().st_size
            self._save_meta(meta)
        except Exception:
            shutil.rmtree(sd, ignore_errors=True)
            raise
        self.prune(conn["name"])
        return meta

    async def snapshot(self, connection: Optional[str], tables: Optional[list[str]] = None, reason: str = "",
                       kind: str = "manual") -> dict[str, Any]:
        return await asyncio.to_thread(self.snapshot_sync, connection, tables, reason, kind)

    def prune(self, connection: str) -> list[str]:
        opts = self.options()
        keep_last = int(opts.get("keep_last") or KEEP_LAST)
        keep_days = float(opts.get("keep_days") or KEEP_DAYS)
        snaps = self.snapshots(connection)
        cutoff = time.time() - keep_days * 86400
        gone = []
        for m in snaps[keep_last:]:
            if m["created"] < cutoff:
                shutil.rmtree(self._snap_dir(m), ignore_errors=True)
                gone.append(m["id"])
        return gone

    # ------------------------------------------------------------ compare
    def compare_sync(self, snap_id: str) -> dict[str, Any]:
        meta = self.get(snap_id)
        if meta is None:
            raise DbtmError("There's no snapshot with that id.")
        conn, client = self._client(meta["connection"])
        before = self.fingerprints(meta)
        now_tables = client.tables()
        max_rows = int(self.options().get("max_rows_per_table") or MAX_ROWS)
        diff: dict[str, dict[str, Any]] = {}
        for t in meta["tables"]:
            if t not in now_tables:
                diff[t] = {"dropped": True}
                continue
            diff[t] = diff_fingerprints(before.get(t, {}), fingerprint_table(client, t, max_rows))
        if meta.get("whole"):
            for t in now_tables:
                if t not in meta.get("all_tables", []):
                    diff[t] = {"new_table": True}
        return {"snapshot": meta["id"], "since": meta["created"], "connection": meta["connection"],
                "tables": diff, "text": describe_diff(diff)}

    async def compare(self, snap_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self.compare_sync, snap_id)

    # ------------------------------------------------------------ around a write
    async def before_write(self, connection: str, sql: str, who: str = "a coding agent") -> Optional[dict[str, Any]]:
        """Snapshot what a write will touch. Raises DbtmError if a snapshot was possible but failed."""
        if self.options().get("enabled") is False:
            return None
        ok, why = self.available()
        if not ok:
            return {"skipped": f"No snapshot was taken: {why}."}
        try:
            meta = await self.snapshot(connection, tables_touched(sql), reason=f"before {who}'s change: "
                                       f"{' '.join(sql.split())[:160]}", kind="before_write")
        except DbtmError:
            raise
        except Exception as exc:
            raise DbtmError(f"Couldn't take a safety snapshot first ({type(exc).__name__}: {exc})"[:400])
        return {"snapshot": meta["id"]}

    async def after_write(self, guard: Optional[dict[str, Any]]) -> dict[str, Any]:
        if not guard:
            return {}
        if guard.get("skipped"):
            return {"time_machine": guard["skipped"]}
        try:
            res = await self.compare(guard["snapshot"])
        except Exception as exc:
            log.warning("DB Time Machine compare failed: %s", exc)
            return {"snapshot": guard["snapshot"], "time_machine": "Snapshot taken; the comparison failed."}
        meta = self.get(guard["snapshot"])
        if meta is not None:
            meta["diff_after"] = res["tables"]
            meta["diff_text"] = res["text"]
            self._save_meta(meta)
        self.notify(f"{res['connection']} changed", res["text"][:300] + ". Roll back from Orion if it's wrong.")
        return {"snapshot": guard["snapshot"], "changes": res["text"]}

    # ------------------------------------------------------------ rollback
    async def request_rollback(self, snap_id: str, table: Optional[str] = None,
                               requested_by: str = "you") -> dict[str, Any]:
        meta = self.get(snap_id)
        if meta is None:
            raise DbtmError("There's no snapshot with that id.")
        if table and table not in meta["tables"]:
            raise DbtmError(f"That snapshot doesn't include the table {table}.")
        self.settings(meta["connection"])     # still a staging host?
        if self.board is None:
            raise DbtmError("Rolling back needs your approval, and Orion can't ask right now.")
        when = datetime.fromtimestamp(meta["created"]).strftime("%a %H:%M")
        what = f"the {table} table" if table else f"the whole {meta['database']} database"
        dec = await self.board.propose(
            TASK_ROLLBACK, f"Put {what} back to {when} ({meta['connection']})",
            {"snapshot": snap_id, "connection": meta["connection"], "host": meta["host"],
             "database": meta["database"], "table": table or "", "snapshot_reason": meta.get("reason", ""),
             "does": "Orion takes a fresh snapshot first (so this can be undone), then loads the old one.",
             "requested_by": requested_by, "why_it_asks": "it overwrites data"},
            proposer="DB Time Machine", risk="high", simulated=False)
        return {"state": "waiting", "decision_id": dec["id"],
                "message": f"Asked you to approve putting {what} back to {when}."}

    def rollback_sync(self, snap_id: str, table: Optional[str] = None) -> dict[str, Any]:
        meta = self.get(snap_id)
        if meta is None:
            raise DbtmError("The snapshot is gone.")
        tables = [table] if table else meta["tables"]
        safety = self.snapshot_sync(meta["connection"], tables if table else None,
                                    reason=f"before rolling back to {snap_id}", kind="before_rollback")
        text = self.dump_text(meta)
        if table:
            header, parts, footer = split_dump(text)
            if table not in parts:
                raise DbtmError(f"The snapshot has no saved copy of {table}.")
            text = header + parts[table] + footer
        _, client = self._client(meta["connection"])
        client.restore(text)
        what = table or f"{len(tables)} table(s)"
        new = [t for t in client.tables() if t not in meta.get("all_tables", [])] if not table else []
        msg = f"Put {what} in {meta['database']} back to snapshot {snap_id}. Undo point: {safety['id']}."
        if new:
            msg += f" Tables added since then were left alone: {', '.join(new[:8])}."
        return {"state": "done", "safety_snapshot": safety["id"], "message": msg}

    def execute_rollback(self, d: dict[str, Any], spawn: Callable[[Any], Any]) -> dict[str, Any]:
        det = d["packet"]["details"]

        async def go() -> None:
            try:
                res = await asyncio.to_thread(self.rollback_sync, det["snapshot"], det.get("table") or None)
                self.notify("Rollback done", res["message"])
            except Exception as exc:
                self.notify("Rollback failed", f"Nothing more was changed. {exc}"[:300])
        spawn(go())
        return {"state": "started"}

    # ------------------------------------------------------------ "what changed in the world DB today?"
    def find_connection(self, word: str) -> Optional[str]:
        conns = self.connections()
        w = re.sub(r"[^a-z0-9]", "", (word or "").lower())
        if not w:
            return conns[0]["name"] if len(conns) == 1 else None
        for c in conns:
            if w in (re.sub(r"[^a-z0-9]", "", c["name"].lower()), re.sub(r"[^a-z0-9]", "", str(c.get("database") or "").lower())):
                return c["name"]
        for c in conns:
            if w in c["name"].lower() or w in str(c.get("database") or "").lower():
                return c["name"]
        return None

    def connections(self) -> list[dict[str, Any]]:
        try:
            from mcp_tools import McpConfig
            cfg = McpConfig.load()
        except Exception:
            return []
        staging = {str(h).strip().lower() for h in cfg.mysql.get("staging_hosts", []) or []}
        return [{"name": n, "database": c.get("database"), "host": c.get("host"),
                 "staging": str(c.get("host") or "").strip().lower() in staging}
                for n, c in (cfg.mysql.get("connections") or {}).items()]

    async def changes_today(self, connection: Optional[str]) -> str:
        if connection is None:
            return "Which database? I couldn't match that to one of your staging connections."
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        today = [m for m in self.snapshots(connection) if m["created"] >= start]
        if not today:
            return (f"Orion hasn't taken a snapshot of {connection} today, so it can't say what changed. "
                    "Say “Orion, snapshot the world DB” to start one.")
        lines = []
        for m in reversed(today):
            if m.get("diff_text"):
                lines.append(f"{datetime.fromtimestamp(m['created']).strftime('%H:%M')}: {m['diff_text']}"
                             + (f" ({m['reason']})" if m.get("reason") else ""))
        first = today[-1]
        try:
            res = await self.compare(first["id"])
            since = datetime.fromtimestamp(first["created"]).strftime("%H:%M")
            head = f"Since {since} today in {connection}: {res['text']}."
        except Exception as exc:
            head = f"Couldn't compare with the live database right now ({exc})."[:200]
        return " ".join([head] + (["Changes Orion saw:"] + lines if lines else []))


def _staging_settings(name: Optional[str]) -> dict[str, Any]:
    from mcp_tools import McpConfig, ToolError, mysql_connection_settings
    try:
        return mysql_connection_settings(McpConfig.load(), name)
    except ToolError as exc:
        raise DbtmError(str(exc))


def _options() -> dict[str, Any]:
    try:
        from mcp_tools import McpConfig
        tm = McpConfig.load().mysql.get("time_machine") or {}
        return tm if isinstance(tm, dict) else {}
    except Exception:
        return {}


# =====================================================================================  MCP write path hook
async def before_write(connection: str, sql: str) -> Optional[dict[str, Any]]:
    """Called by mcp_routes before an approved write. Raises ToolError if the safety snapshot failed."""
    if CURRENT is None:
        return None
    try:
        return await CURRENT.before_write(connection, sql)
    except DbtmError as exc:
        from mcp_tools import ToolError
        raise ToolError(f"{exc}. Nothing was changed.")


async def after_write(guard: Optional[dict[str, Any]]) -> dict[str, Any]:
    if CURRENT is None or not guard:
        return {}
    return await CURRENT.after_write(guard)


def _register_task_types() -> None:
    import boardroom
    from boardroom import TaskType
    # reversible in effect (a fresh snapshot is taken first), but it overwrites data: sensitive, always asks
    boardroom.TASK_TYPES.setdefault(TASK_ROLLBACK, TaskType(TASK_ROLLBACK, "Roll a staging database back", "infra",
                                                            reversible=True, money=False, sensitive=True))


_register_task_types()
