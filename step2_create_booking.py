"""
RPA Booking SMS - Step 2: CREATE -> Vendor Booking - By Packing Plan
Run:  python step2_create_booking.py
(Requires step 1 to have been run once; falls back to a fresh login if the
saved session has expired.)
"""
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, Error as PWError, TimeoutError as PWTimeout

import step1_login as s1   # reuse config + login()

SHOT_DIR = s1.SHOT_DIR


def first_visible(page, make_locator, timeout=20_000):
    """Search the page and all frames; return the first VISIBLE match (checks every match)."""
    import time
    end = time.time() + timeout / 1000
    while time.time() < end:
        for fr in page.frames:
            try:
                loc = make_locator(fr)
                for i in range(min(loc.count(), 40)):
                    el = loc.nth(i)
                    if el.is_visible():
                        return el
            except PWError:
                continue
        page.wait_for_timeout(300)
    return None


def ensure_logged_in(page) -> None:
    page.goto(s1.URL, wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")
    s1.wait_until_idle(page, label="page load ")
    if s1.find_login_frame(page) is not None:      # login form showing -> session expired
        print("Session expired, logging in again...")
        s1.login(page)


def click_create_menu(page) -> None:
    create_btn = first_visible(
        page, lambda f: f.locator("text=/^\\s*\\+?\\s*Create\\s*$/i")
    )
    if create_btn is None:
        s1.dump_debug(page, "create_not_found")
        raise RuntimeError("CREATE button not found. See screenshots/create_not_found.png")
    create_btn.click()

    item_re = re.compile(r"Vendor Booking\s*[-–—]\s*By Packing Plan", re.I)
    item = first_visible(page, lambda f: f.get_by_text(item_re), timeout=8_000)
    if item is None:                               # menu may open on hover instead
        create_btn.hover()
        item = first_visible(page, lambda f: f.get_by_text(item_re), timeout=8_000)
    if item is None:
        s1.dump_debug(page, "menu_item_not_found")
        raise RuntimeError(
            "'Vendor Booking - By Packing Plan' not found in the menu. "
            "See screenshots/menu_item_not_found.png"
        )
    item.click()
    s1.wait_until_idle(page, label="after CREATE menu ")


def main() -> int:
    if not (s1.USERNAME and s1.PASSWORD and s1.URL):
        print("Missing INFOR_USERNAME / INFOR_PASSWORD / INFOR_URL in .env")
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=s1.HEADLESS)
        ctx_args = {"viewport": {"width": 1600, "height": 900}}
        if s1.STATE_FILE.exists():
            ctx_args["storage_state"] = str(s1.STATE_FILE)
        context = browser.new_context(**ctx_args)
        page = context.new_page()

        ensure_logged_in(page)

        pages_before = len(context.pages)
        click_create_menu(page)

        page.wait_for_timeout(1500)
        if len(context.pages) > pages_before:      # item opened a new tab
            page = context.pages[-1]
        page.wait_for_load_state("networkidle")

        print("Opened ->", page.url)
        page.screenshot(path=SHOT_DIR / "after_step2.png", full_page=True)
        context.storage_state(path=s1.STATE_FILE)

        if not HEADLESS_KEEP_OPEN(s1.HEADLESS):
            pass
        browser.close()
    return 0


def HEADLESS_KEEP_OPEN(headless: bool) -> bool:
    """When watching the browser, pause so you can see the result."""
    if not headless:
        input("Press Enter to close the browser...")
    return True


if __name__ == "__main__":
    sys.exit(main())
