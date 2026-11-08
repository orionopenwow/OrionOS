"""Black hole shaders (after-1.0 plan §3): both tiers keep one uniform contract, so moods, voice, the agent-busy spin,
smart reactions and the boot timeline drive the ray-marched Ultra tier exactly like the painted tiers.

  python3 -m pytest os/theme/blackhole/tests

The contract checks are plain text. If Playwright's Chromium is installed (PLAYWRIGHT_BROWSERS_PATH, default
/opt/pw-browsers), both shaders are also really compiled and linked on its software GL (render.mjs --check).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
BH = HERE.parent
REPO = BH.parents[2]
FLAT = BH / "blackhole.frag"
RT = BH / "blackhole_rt.frag"
RENDERER = REPO / "apps" / "desktop" / "src" / "lib" / "blackhole" / "renderer.ts"


def code(p: Path) -> str:
    return re.sub(r"/\*.*?\*/", "", re.sub(r"//[^\n]*", "", p.read_text()), flags=re.S)


def uniforms(p: Path) -> dict[str, str]:
    return {m[2]: m[1] for m in re.finditer(r"^\s*uniform\s+(\w+)\s+(\w+)(?:\[\d+\])?\s*;", code(p), re.M)}


def renderer_names() -> list[str]:
    src = RENDERER.read_text()
    block = re.search(r"const UNIFORM_NAMES = \[(.*?)\] as const", src, re.S)
    assert block, "UNIFORM_NAMES not found in renderer.ts"
    return re.findall(r"'(u_\w+)'", block[1])


@pytest.mark.parametrize("path", [FLAT, RT], ids=["flat", "ultra"])
def test_glsl_es3_shape(path):
    src = path.read_text()
    assert src.startswith("#version 300 es\n"), "the version line must be first (WebGL2)"
    c = code(path)
    assert re.search(r"precision\s+highp\s+float;", c)
    assert re.search(r"\bout\s+vec4\s+fragColor;", c) and "void main()" in c
    assert "gl_FragColor" not in c and "texture2D" not in c and "varying" not in c   # GLSL ES 1 leftovers
    for a, b in ("{}", "()", "[]"):
        assert c.count(a) == c.count(b), f"unbalanced {a}{b} in {path.name}"


def test_both_tiers_share_one_uniform_contract():
    names = renderer_names()
    flat, rt = uniforms(FLAT), uniforms(RT)
    assert flat == rt, "the Ultra shader must declare exactly the same uniforms (same types) as blackhole.frag"
    missing = [n for n in names if n not in rt]
    assert not missing, f"renderer.ts sets uniforms the shaders don't declare: {missing}"
    for n in ("u_voice", "u_ripple", "u_spin", "u_flow", "u_boot", "u_temp", "u_hue", "u_bodies", "u_quality"):
        assert n in names


def test_ultra_is_a_real_ray_march():
    c = code(RT)
    assert re.search(r"for\s*\(\s*int\s+i\s*=\s*0;\s*i\s*<\s*\d+", c), "a bounded march loop"
    assert "1.5 * mass * h2" in c, "the Schwarzschild photon equation of motion"
    assert "doppler(" in c and "captured" in c
    # lookups inside the loop need an explicit mip level (no derivatives in non-uniform control flow)
    assert "textureLod(u_disk" in c and "texture(u_disk" not in c
    # the HUD reads these constants from the painted tier; keep the same screen geometry
    for k in ("RS = 0.082", "RIN = 0.092", "ROUT = 0.40"):
        assert k in c and k in code(FLAT)


def test_readme_documents_the_tiers():
    text = (BH / "README.md").read_text()
    assert "blackhole_rt.frag" in text and "Ultra" in text and "--ultra" in text


def _chromium_available() -> bool:
    if not shutil.which("node"):
        return False
    base = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
    return base.is_dir() and any(base.glob("chromium*"))


@pytest.mark.skipif(not _chromium_available(), reason="no headless Chromium (Playwright) here")
def test_shaders_compile_and_link_on_headless_gl():
    r = subprocess.run(["node", str(BH / "render.mjs"), "--check"], capture_output=True, text=True, timeout=180,
                       cwd=str(REPO))
    if r.returncode != 0 and "playwright not found" in (r.stderr + r.stdout):
        pytest.skip("Playwright isn't installed for node here")
    assert r.returncode == 0, r.stderr[-2000:] + r.stdout[-2000:]
    res = json.loads(r.stdout.strip().splitlines()[-1])
    assert res["flat"]["ok"] and res["ultra"]["ok"], res
    for n in renderer_names():
        if n == "u_resolution":
            continue
        assert n in res["ultra"]["uniforms"], f"{n} is optimised out of the Ultra shader: it would do nothing"
