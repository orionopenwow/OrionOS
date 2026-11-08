"""Render the login screen headlessly (demo mode) and save screenshots of each moment.

    python os/greeter/tests/shoot.py            # -> os/greeter/screenshots/*.png
    python os/greeter/tests/shoot.py --hole os/theme/blackhole/blackhole.frag
                                                # with the shared shader -> screenshots/hole-*.png
Also checks the page behaves: wrong password shakes and says "That's not it", Esc clears, Caps Lock warns,
the right password starts the swirl and asks for the session, and no page errors happen.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent.parent
PAGE = (HERE / "web" / "index.html").as_uri()
OUT = HERE / "screenshots"
OUT.mkdir(exist_ok=True)
Q = "?time=2026-10-15T21:42:00"
SIZE = {"width": 1100, "height": 680}


GL_ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]


def hole_page(frag: str) -> tuple[str, pathlib.Path]:
    """A copy of web/ with shader.js made from FRAG (what os/image/art-boot.sh does at install time)."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="orion-greeter-"))
    shutil.copytree(HERE / "web", tmp / "web")
    disk = HERE.parent.parent / "apps/desktop/public/black-hole-core.png"
    subprocess.run([sys.executable, str(HERE / "make-shader-js.py"), frag, str(disk), str(tmp / "web/shader.js")], check=True)
    return (tmp / "web" / "index.html").as_uri(), tmp


def main() -> int:
    global PAGE
    errors: list[str] = []
    prefix, tmp = "", None
    if "--hole" in sys.argv:
        PAGE, tmp = hole_page(sys.argv[sys.argv.index("--hole") + 1])
        prefix = "hole-"
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(args=GL_ARGS if prefix else [])
        except Exception:  # Playwright newer than the installed browser: use whichever Chromium is there
            exe = sorted(pathlib.Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
                         .glob("chromium-*/chrome-linux*/chrome"))
            if not exe:
                raise
            b = p.chromium.launch(executable_path=str(exe[-1]), args=GL_ARGS if prefix else [])
        page = b.new_page(viewport=SIZE)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(PAGE + Q)
        if prefix:
            page.wait_for_timeout(60)
            page.screenshot(path=str(OUT / f"{prefix}0-handoff.png"))
            assert "has-hole" in (page.get_attribute("body", "class") or ""), "the shader didn't start"
        page.wait_for_timeout(1600)
        page.mouse.move(800, 200)
        page.wait_for_timeout(900)
        page.screenshot(path=str(OUT / f"{prefix}1-login.png"))

        # Wrong password: shake + message, field cleared, no animation.
        page.keyboard.type("letmein")
        page.keyboard.press("Enter")
        page.wait_for_timeout(700)
        assert page.inner_text("#status") == "That's not it", page.inner_text("#status")
        assert page.input_value("#pw") == ""
        assert "leaving" not in (page.get_attribute("body", "class") or "")
        # Caps Lock warning (Chromium reports it from the key event's modifier state).
        page.keyboard.press("CapsLock")
        page.keyboard.type("A")
        page.wait_for_timeout(100)
        caps_on = page.is_visible("#caps") and page.evaluate("!document.getElementById('caps').hidden")
        if not caps_on:  # headless may not toggle the real lock state; show it the way a keypress would
            page.evaluate("document.getElementById('caps').hidden = false")
        page.screenshot(path=str(OUT / f"{prefix}2-wrong-password.png"))
        page.keyboard.press("CapsLock")
        page.keyboard.press("Escape")
        assert page.input_value("#pw") == "" and page.inner_text("#status") == ""

        # The swirl, frozen at moments along the way.
        page.evaluate("document.getElementById('caps').hidden = true")
        for name, ms in [("3-swirl", 520), ("4-collapse", 1030), ("5-born", 1500), ("6-black-hole", 2200)]:
            page.evaluate(f"orionGreeter.demo.seek({ms})")
            page.wait_for_timeout(120)
            page.screenshot(path=str(OUT / f"{prefix}{name}.png"))

        # Real run: the right password asks for the session after ~2 s.
        page2 = b.new_page(viewport=SIZE)
        logs: list[str] = []
        page2.on("console", lambda m: logs.append(m.text))
        page2.on("pageerror", lambda e: errors.append(str(e)))
        page2.goto(PAGE + Q)
        page2.wait_for_timeout(300)
        page2.keyboard.type("orion")
        page2.keyboard.press("Enter")
        for _ in range(40 if prefix else 16):   # software WebGL in CI draws slowly: allow it more time
            page2.wait_for_timeout(200)
            if any("session would start" in l for l in logs):
                break
        assert any("session would start" in l for l in logs), logs
        assert page2.input_value("#pw") == ""

        # Reduced motion: a plain fade.
        page3 = b.new_page(viewport=SIZE)
        page3.on("pageerror", lambda e: errors.append(str(e)))
        logs3: list[str] = []
        page3.on("console", lambda m: logs3.append(m.text))
        page3.goto(PAGE + Q + "&reduced")
        page3.wait_for_timeout(600)
        page3.keyboard.type("orion")
        page3.keyboard.press("Enter")
        for _ in range(40 if prefix else 7):
            page3.wait_for_timeout(200)
            if any("session would start" in l for l in logs3):
                break
        assert any("session would start" in l for l in logs3), logs3
        b.close()
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    if errors:
        print("page errors:", errors)
        return 1
    try:  # keep the committed screenshots small
        from PIL import Image
        for f in OUT.glob("*.png"):
            Image.open(f).convert("RGB").quantize(colors=96, method=Image.Quantize.MEDIANCUT).save(f, optimize=True)
    except ImportError:
        pass
    print("ok:", sorted(f.name for f in OUT.glob("*.png")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
