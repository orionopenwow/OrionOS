#!/usr/bin/env python3
"""A stand-in `flatpak` for the Get apps tests. It keeps its whole world in the JSON file named by
FAKE_FLATPAK_STATE and logs every call there, so the tests can check what Get apps asked for.

State keys:
  apps        {id: {name, version, commit, runtime}}         installed apps (runtime = "runtime/ID/ARCH/BRANCH")
  runtimes    {ref: commit}                                  installed runtimes
  remote      {id: {name, version, commit, summary, runtime}} what Flathub offers (latest)
  remote_runtimes {ref: commit}
  broken      [commit, ...]   app or runtime commits that make an app fail to start
  fail_update [id, ...]       updates of these ids fail
  masked      [pattern, ...]
  remotes     [[name, url]]
  calls       [[argv...]]     appended on every call
"""
import json
import os
import sys

path = os.environ["FAKE_FLATPAK_STATE"]
with open(path) as fh:
    S = json.load(fh)
for k, v in (("apps", {}), ("runtimes", {}), ("remote", {}), ("remote_runtimes", {}), ("broken", []),
             ("fail_update", []), ("masked", []), ("remotes", [["flathub", "https://dl.flathub.org/repo/"]]),
             ("calls", [])):
    S.setdefault(k, v)
argv = sys.argv[1:]
S["calls"].append(argv)


def save():
    with open(path, "w") as fh:
        json.dump(S, fh)


def out(s=""):
    if s:
        sys.stdout.write(s + "\n")


def done(code=0, err=""):
    save()
    if err:
        sys.stderr.write(err + "\n")
    sys.exit(code)


def pattern(ref):
    p = ref.split("/")
    return "/".join(p[1:]) if len(p) == 4 else ref


def is_masked(ref):
    return ref in S["masked"] or pattern(ref) in S["masked"]


def healthy(app):
    a = S["apps"].get(app)
    if not a:
        return False
    if a["commit"] in S["broken"]:
        return False
    rt = a.get("runtime")
    return not (rt and S["runtimes"].get(rt) in S["broken"])


def update_one(ref, commit=None):
    if ref in S["fail_update"]:
        return False
    if ref in S["apps"]:
        a = S["apps"][ref]
        if commit:
            a["commit"] = commit
            return True
        r = S["remote"].get(ref)
        if r and r["commit"] != a["commit"]:
            a.update({k: r[k] for k in ("commit", "version") if k in r})
            rt = r.get("runtime") or a.get("runtime")
            if rt and rt in S["remote_runtimes"] and not is_masked(rt):
                S["runtimes"][rt] = S["remote_runtimes"][rt]
        return True
    if ref in S["runtimes"]:
        S["runtimes"][ref] = commit or S["remote_runtimes"].get(ref, S["runtimes"][ref])
        return True
    return False


cmd = argv[0] if argv else ""
args = [a for a in argv[1:] if not a.startswith("-")]
opts = [a for a in argv[1:] if a.startswith("-")]

if S.get("offline") and cmd in ("remote-ls", "remote-info", "install", "update"):
    done(1, "error: Unable to load summary from remote flathub: While fetching https://dl.flathub.org/repo/"
            "summary.idx: [6] Could not resolve hostname")

if cmd == "remotes":
    for name, url in S["remotes"]:
        out(f"{name}\t{url}\t{'system'}")
    done()
if cmd == "list":
    if "--runtime" in opts:
        for ref in S["runtimes"]:
            out(f"{ref.split('/')[1]}\t\t\t{ref.split('/')[3]}\tflathub\t{ref}\t1 GB")
    else:
        for i, a in S["apps"].items():
            out(f"{i}\t{a['name']}\t{a.get('version', '')}\tstable\tflathub\tapp/{i}/x86_64/stable\t100 MB")
    done()
if cmd == "remote-ls":
    if "--runtime" in opts:
        for ref, c in S["remote_runtimes"].items():
            if ref in S["runtimes"] and S["runtimes"][ref] != c and not is_masked(ref):
                out(f"{ref.split('/')[1]}\t{ref.split('/')[1]}\t\t{ref.split('/')[3]}\tflathub\t{ref}\t50 MB")
    else:
        for i, a in S["apps"].items():
            r = S["remote"].get(i)
            if r and r["commit"] != a["commit"] and not is_masked(i):
                out(f"{i}\t{a['name']}\t{r.get('version', '')}\tstable\tflathub\tapp/{i}/x86_64/stable\t20 MB")
    done()
if cmd == "info":
    ref = args[0]
    if "--show-commit" in opts:
        if ref in S["apps"]:
            out(S["apps"][ref]["commit"])
            done()
        if ref in S["runtimes"]:
            out(S["runtimes"][ref])
            done()
        done(1, f"error: {ref} not installed")
    if "--show-runtime" in opts:
        if ref in S["apps"] and S["apps"][ref].get("runtime"):
            out(S["apps"][ref]["runtime"].split("/", 1)[1])
            done()
        done(1, "error: not installed")
    done(0 if ref in S["apps"] else 1)
if cmd == "mask":
    if not args:
        if S["masked"]:
            out("Masked patterns:")
            for m in S["masked"]:
                out(f"  {m}")
        done()
    if "--remove" in opts:
        if args[0] not in S["masked"]:
            done(1, f"error: No current mask matching {args[0]}")
        S["masked"].remove(args[0])
        done()
    if args[0] not in S["masked"]:
        S["masked"].append(args[0])
    done()
if cmd == "search":
    words = " ".join(args).lower()
    for i, r in S["remote"].items():
        if words in (i + " " + r.get("name", "") + " " + r.get("summary", "")).lower():
            out(f"{i}\t{r.get('name', '')}\t{r.get('summary', '')}\t{r.get('version', '')}\tflathub")
    done()
if cmd == "remote-info":
    done(0 if args[-1] in S["remote"] else 1, "" if args[-1] in S["remote"] else "error: not found")
if cmd == "install":
    if "--bundle" in opts:
        done(0)
    app = args[-1]
    r = S["remote"].get(app)
    if not r or app in S["fail_update"]:
        done(1, f"error: {app} not found")
    for pct in (5, 40, 100):
        sys.stdout.write(f"Installing {app}… {pct}%\r")
    sys.stdout.write("\nInstallation complete.\n")
    S["apps"][app] = {"name": r.get("name", app), "version": r.get("version", ""), "commit": r["commit"],
                      "runtime": r.get("runtime", "")}
    if r.get("runtime"):
        S["runtimes"].setdefault(r["runtime"], S["remote_runtimes"].get(r["runtime"], "rt0"))
    done()
if cmd == "uninstall":
    if args[-1] not in S["apps"]:
        done(1, "error: not installed")
    del S["apps"][args[-1]]
    done()
if cmd == "update":
    commit = next((o.split("=", 1)[1] for o in opts if o.startswith("--commit=")), None)
    if args:
        done(0 if update_one(args[0], commit) else 1, "" if True else "")
    ok = True
    for ref in list(S["runtimes"]):
        if not is_masked(ref):
            ok = update_one(ref) and ok
    for app in list(S["apps"]):
        if not is_masked(app):
            ok = update_one(app) and ok
    done(0 if ok else 1)
if cmd == "run":
    app = args[-1]
    done(0 if healthy(app) else 1, "" if healthy(app) else "bwrap: app failed to start")
done(2, f"fake flatpak: unknown command {argv}")
