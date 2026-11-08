#!/usr/bin/env python3
"""Refresh Package Reality Check's list of popular packages (apps/daemon/package_top_lists.json).

Package Reality Check (toolkit tool 7) compares every new dependency with this list to spot look-alike names
("reqeusts" next to "requests"). The list ships with Orion so the check also works offline; this script
rebuilds it from the registries' own public APIs:

  npm     registry.npmjs.org search (sorted by weekly downloads, over a set of common keywords)
  crates  crates.io /api/v1/crates?sort=downloads
  PyPI    PyPI has no public "most downloaded" list, so a hand-made list (below) is used and checked against
          pypi.org: names that no longer exist are dropped.

Only the package registries are contacted. Standard library only, so any Python 3.9+ can run it:

    python3 scripts/package-check/update_top_lists.py            # all three, about 2-4 minutes
    python3 scripts/package-check/update_top_lists.py --only npm --limit 1500
    python3 scripts/package-check/update_top_lists.py --offline   # rewrite the file from the hand-made lists only
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "apps" / "daemon" / "package_top_lists.json"
UA = "orion-package-reality-check/1.0 (list updater)"

NPM_KEYWORDS = ["javascript", "typescript", "react", "node", "nodejs", "cli", "css", "test", "testing", "webpack",
                "babel", "eslint", "vue", "express", "util", "utility", "array", "string", "object", "promise",
                "stream", "http", "fs", "parser", "json", "polyfill", "ecmascript", "types", "build", "plugin",
                "api", "aws", "angular", "vite", "rollup", "postcss", "lint", "date", "color", "path", "async",
                "crypto", "logger", "server", "browser", "framework", "database", "graphql", "markdown"]

# Hand-made: widely used PyPI packages (names as PyPI lists them).
PYPI_HAND = """
boto3 botocore urllib3 requests setuptools certifi charset-normalizer idna typing-extensions python-dateutil
s3transfer packaging six aiobotocore numpy pyyaml grpcio-status fsspec s3fs pip cryptography pydantic cffi
attrs pycparser pandas importlib-metadata google-api-core jmespath zipp rsa pyasn1 protobuf click platformdirs
pytz markupsafe jinja2 wheel colorama tomli filelock pluggy pyjwt virtualenv wrapt pytest googleapis-common-protos
cachetools pydantic-core jsonschema annotated-types aiohttp pyarrow sqlalchemy multidict yarl frozenlist
aiosignal psutil tzdata pyasn1-modules google-auth requests-oauthlib oauthlib docutils exceptiongroup iniconfig
pyparsing scipy greenlet soupsieve beautifulsoup4 werkzeug tomlkit isodate decorator pygments pillow h11 anyio
sniffio httpx httpcore grpcio openpyxl et-xmlfile distlib lxml tqdm more-itertools rich markdown-it-py mdurl
flask itsdangerous blinker matplotlib kiwisolver fonttools cycler contourpy regex coverage pytest-cov mock
requests-toolbelt websocket-client msgpack azure-core azure-storage-blob azure-identity msal msal-extensions
portalocker google-cloud-storage google-cloud-core google-resumable-media google-crc32c proto-plus
google-cloud-bigquery google-auth-oauthlib gitpython gitdb smmap scikit-learn joblib threadpoolctl networkx
sympy mpmath tabulate wcwidth prompt-toolkit ipython traitlets jedi parso pexpect ptyprocess matplotlib-inline
asttokens executing pure-eval stack-data comm debugpy ipykernel jupyter-core jupyter-client tornado pyzmq
nest-asyncio notebook jupyterlab nbformat nbconvert nbclient mistune bleach webencodings tinycss2 pandocfilters
defusedxml fastjsonschema jsonschema-specifications referencing rpds-py terminado send2trash argon2-cffi
prometheus-client pyopenssl paramiko bcrypt pynacl invoke fabric toml black isort flake8 pyflakes pycodestyle
mccabe mypy mypy-extensions pylint astroid tomli-w ruff pre-commit identify nodeenv cfgv tox django
djangorestframework asgiref sqlparse gunicorn uvicorn starlette fastapi python-multipart python-dotenv
email-validator dnspython orjson ujson simplejson uvloop httptools watchfiles websockets celery kombu vine
amqp billiard redis pymongo psycopg2 psycopg2-binary psycopg pymysql mysqlclient alembic mako peewee
sqlalchemy-utils marshmallow apispec pyrsistent tensorflow tensorboard keras torch torchvision torchaudio
transformers tokenizers huggingface-hub safetensors datasets accelerate sentencepiece tiktoken openai anthropic
langchain langchain-core langchain-community langsmith llama-index google-generativeai google-genai
xgboost lightgbm catboost statsmodels patsy seaborn plotly dash bokeh altair streamlit gradio opencv-python
opencv-python-headless imageio scikit-image tifffile pywavelets shapely pyproj fiona geopandas h5py tables
numba llvmlite cython pybind11 cmake ninja scikit-build meson poetry poetry-core hatchling flit-core pdm
build twine readme-renderer keyring jaraco-classes secretstorage jeepney pkginfo rfc3986 requests-file
selenium playwright pyee trio trio-websocket outcome wsproto sortedcontainers hypothesis faker factory-boy
freezegun responses pytest-mock pytest-asyncio pytest-xdist execnet pytest-timeout pytest-django
pytest-benchmark nose sentry-sdk structlog loguru python-json-logger humanfriendly coloredlogs termcolor
tenacity backoff retrying cachecontrol requests-cache diskcache pyrsistent arrow pendulum babel croniter
python-slugify text-unidecode unidecode chardet ftfy emoji nltk spacy gensim textblob jieba sacremoses
docker kubernetes ansible ansible-core jsonpatch jsonpointer pywin32 pywin32-ctypes comtypes pyautogui
pynput keyboard mouse pyperclip pyinstaller pyinstaller-hooks-contrib altgraph pefile cx-freeze nuitka
setuptools-scm mkdocs mkdocs-material sphinx sphinx-rtd-theme alabaster snowballstemmer imagesize
docstring-parser typer shellingham click-plugins cliff cmd2 fire docopt argcomplete configparser
configobj python-decouple dynaconf hydra-core omegaconf jsonpickle dill cloudpickle pickleshare zstandard
lz4 brotli python-snappy xxhash mmh3 pycryptodome pycryptodomex ecdsa passlib itsdangerous authlib
python-jose oauth2client google-api-python-client httplib2 uritemplate slack-sdk discord-py python-telegram-bot
telethon tweepy praw pyrogram twilio sendgrid stripe paypalrestsdk boto pysftp pyodbc cx-oracle oracledb
snowflake-connector-python databricks-sql-connector pyspark py4j findspark delta-spark polars duckdb
pyiceberg dask distributed toolz cytoolz partd locket bokeh xarray netcdf4 cftime zarr numcodecs
faster-whisper openai-whisper ctranslate2 onnx onnxruntime sounddevice soundfile pyaudio librosa audioread
resampy pydub mss pytesseract easyocr lancedb chromadb faiss-cpu qdrant-client pinecone-client weaviate-client
sentence-transformers ollama mcp playwright-stealth pyobjc pygobject dbus-python pyudev evdev
""".split()


def get_json(url: str, timeout: float = 20.0):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 (package registries only)
        return json.loads(r.read().decode("utf-8"))


def npm_top(limit: int) -> list[str]:
    seen: dict[str, int] = {}
    for kw in NPM_KEYWORDS:
        for start in range(0, 1000, 250):
            url = ("https://registry.npmjs.org/-/v1/search?" +
                   urllib.parse.urlencode({"text": f"keywords:{kw}", "size": 250, "from": start}))
            try:
                data = get_json(url)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                print(f"  npm: {kw} page {start // 250 + 1} failed ({exc}); skipped", file=sys.stderr)
                break
            objs = data.get("objects") or []
            for o in objs:
                name = (o.get("package") or {}).get("name")
                weekly = int((o.get("downloads") or {}).get("weekly") or 0)
                if name:
                    seen[name] = max(seen.get(name, 0), weekly)
            if len(objs) < 250:
                break
            time.sleep(0.5)   # stay well under the registry's rate limit
        print(f"  npm: {kw}: {len(seen)} names so far", file=sys.stderr)
    return [n for n, _ in sorted(seen.items(), key=lambda kv: -kv[1])][:limit]


def crates_top(limit: int) -> list[str]:
    out: list[str] = []
    page = 1
    while len(out) < limit:
        try:
            data = get_json(f"https://crates.io/api/v1/crates?sort=downloads&per_page=100&page={page}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"  crates: page {page} failed ({exc}); stopping", file=sys.stderr)
            break
        rows = data.get("crates") or []
        out += [c["name"] for c in rows if c.get("name")]
        if len(rows) < 100:
            break
        page += 1
        time.sleep(1.0)   # crates.io asks for at most one request a second
    return out[:limit]


def pypi_checked(names: list[str]) -> list[str]:
    keep = []
    for n in names:
        try:
            get_json(f"https://pypi.org/pypi/{urllib.parse.quote(n)}/json", timeout=10)
            keep.append(n)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                print(f"  pypi: {n} no longer exists; dropped", file=sys.stderr)
            else:
                keep.append(n)
        except (urllib.error.URLError, OSError, ValueError):
            keep.append(n)   # offline: keep it
    return keep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=["npm", "pypi", "crates"], action="append")
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--offline", action="store_true", help="don't contact the registries")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    out_path = Path(a.out)
    try:
        current = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = {}
    lists = dict(current.get("lists") or {})
    sources = dict(current.get("sources") or {})
    which = a.only or ["npm", "pypi", "crates"]
    hand = sorted(dict.fromkeys(PYPI_HAND))
    if "pypi" in which:
        print("PyPI: hand-made list" + ("" if a.offline else ", checking each name on pypi.org"), file=sys.stderr)
        lists["pypi"] = hand if a.offline else pypi_checked(hand)
        sources["pypi"] = "hand-made list (PyPI publishes no download ranking)"
    if not a.offline and "npm" in which:
        print("npm: asking registry.npmjs.org", file=sys.stderr)
        got = npm_top(a.limit)
        if got:
            lists["npm"], sources["npm"] = got, "registry.npmjs.org search, by weekly downloads"
    if not a.offline and "crates" in which:
        print("crates: asking crates.io", file=sys.stderr)
        got = crates_top(a.limit)
        if got:
            lists["crates"], sources["crates"] = got, "crates.io, by all-time downloads"
    data = {"about": "Popular package names for Package Reality Check's look-alike test. Rebuild with "
                     "scripts/package-check/update_top_lists.py.",
            "updated": time.strftime("%Y-%m-%d"), "sources": sources,
            "lists": {k: lists[k] for k in sorted(lists)}}
    out_path.write_text(json.dumps(data, indent=0, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}: " + ", ".join(f"{k} {len(v)}" for k, v in sorted(lists.items())), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
