"""
RPA Booking SMS - Step 1: Log in to Infor Nexus
Run:  python step1_login.py
"""
import os
import re
import sys
import time
from pathlib import Path
import secure_creds

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Error as PWError, TimeoutError as PWTimeout

load_dotenv()

USERNAME, PASSWORD = secure_creds.get_credentials()
URL = os.getenv("INFOR_URL")
HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"

STATE_FILE = Path("auth_state.json")   # saved session, reused by later steps
SHOT_DIR = Path("screenshots")
SHOT_DIR.mkdir(exist_ok=True)

PWD_SEL = "input[type='password']"
USER_SEL = (
    "input:visible:not([type='password']):not([type='hidden'])"
    ":not([type='checkbox']):not([type='submit']):not([type='button'])"
)


BUSY_JS = r"""
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const cs = getComputedStyle(el);
    return cs.display !== 'none' && cs.visibility !== 'hidden' && parseFloat(cs.opacity) > 0.05;
  };
  const name = (el) => String(
    (el.className && el.className.baseVal !== undefined) ? el.className.baseVal : (el.className || '')
  ) + '#' + (el.id || '');
  try {
    for (const a of document.getAnimations()) {            // spinner = endless running animation
      if (a.playState !== 'running') continue;
      const t = a.effect && a.effect.target;
      if (!t || !visible(t)) continue;
      if (a.effect.getComputedTiming().iterations === Infinity) return 'animation:' + name(t);
    }
  } catch (e) {}
  const re = /busy-indicator|loading-indicator|spinner|loader|loading-mask|please-wait/i;
  for (const el of document.querySelectorAll('[class],[id]')) {
    const n = name(el);
    if (re.test(n) && visible(el)) return 'class:' + n;
  }
  return null;
}
"""


def is_busy(page):
    """Return a description of the visible loader/spinner, or None when the page is idle."""
    for fr in page.frames:
        try:
            r = fr.evaluate(BUSY_JS)
            if r:
                return r
        except PWError:
            continue
    return None


def wait_until_idle(page, max_s: int = 90, stable_ms: int = 800, label: str = "") -> None:
    """Wait until the loading spinner has been gone for `stable_ms` in a row."""
    end = time.time() + max_s
    idle_since = None
    said = False
    while time.time() < end:
        busy = is_busy(page)
        if busy:
            idle_since = None
            if not said:
                print(f"  ...waiting for system to finish {label}({busy[:60]})")
                said = True
        else:
            idle_since = idle_since or time.time()
            if (time.time() - idle_since) * 1000 >= stable_ms:
                return
        page.wait_for_timeout(250)
    print(f"  WARNING: still busy after {max_s}s ({is_busy(page)}); continuing anyway")


def dump_debug(page, name: str) -> None:
    """Save a screenshot + info about frames/inputs to help diagnose problems."""
    page.screenshot(path=SHOT_DIR / f"{name}.png", full_page=True)
    print(f"\n--- DEBUG ({name}) ---")
    print("URL:", page.url)
    print("Title:", page.title())
    for i, fr in enumerate(page.frames):
        print(f"[frame {i}] {fr.url}")
        try:
            for el in fr.locator("input").all():
                print("   input:",
                      "type=", el.get_attribute("type"),
                      "name=", el.get_attribute("name"),
                      "id=", el.get_attribute("id"),
                      "visible=", el.is_visible())
        except PWError:
            pass
    print(f"Screenshot saved: {SHOT_DIR / (name + '.png')}\n")


def find_login_frame(page):
    """Return the frame (or main page) that holds the visible password box."""
    for fr in page.frames:
        try:
            if fr.locator(PWD_SEL + ":visible").count() > 0:
                return fr
        except PWError:
            continue
    return None


def login(page) -> None:
    page.goto(URL, wait_until="domcontentloaded")

    # 1) wait until a password box is visible anywhere on the page
    try:
        page.wait_for_selector(PWD_SEL, state="visible", timeout=30_000)
    except PWTimeout:
        dump_debug(page, "login_form_not_found")
        raise RuntimeError(
            "Login form not found. See screenshots/login_form_not_found.png "
            "and the DEBUG output above."
        )

    frame = find_login_frame(page)
    if frame is None:
        dump_debug(page, "login_frame_not_found")
        raise RuntimeError("Could not locate the frame containing the login form.")

    # Detect the "session timed out -> Re-Activate" variant of the page
    reactivate = (
        frame.get_by_role("button", name=re.compile(r"Re-?Activate", re.I)).count() > 0
        or frame.get_by_text(re.compile(r"has timed out", re.I)).count() > 0
    )
    if reactivate:
        print("Session-timeout page detected -> using Re-Activate (password only)")
    else:
        # 2) username (first visible text-like input)
        frame.locator(USER_SEL).first.fill(USERNAME)

    # 3) password (Access Code is left empty)
    pwd_box = frame.locator(PWD_SEL).first
    pwd_box.fill(PASSWORD)

    # 4) click "Log In" or "Re-Activate"
    btn = frame.get_by_role("button", name=re.compile(r"^\s*(Log ?In|Re-?Activate)\s*$", re.I))
    if btn.count() == 0:
        btn = frame.locator("input[type='submit'], button[type='submit']")
    btn.first.click()

    # 5) success = password box goes away
    try:
        pwd_box.wait_for(state="hidden", timeout=30_000)
    except PWTimeout:
        dump_debug(page, "login_failed")
        raise RuntimeError(
            "Login did not complete. Check credentials, or whether an "
            "Access Code is required. See screenshots/login_failed.png"
        )
    except PWError:
        pass  # frame navigated away after login = success

    page.wait_for_load_state("networkidle")
    wait_until_idle(page, label="after login ")


def main() -> int:
    if not (USERNAME and PASSWORD and URL):
        print("Missing INFOR_USERNAME / INFOR_PASSWORD / INFOR_URL in .env")
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        login(page)
        print("Login successful ->", page.url)

        page.screenshot(path=SHOT_DIR / "after_login.png")
        context.storage_state(path=STATE_FILE)
        print(f"Session saved to {STATE_FILE}")

        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
