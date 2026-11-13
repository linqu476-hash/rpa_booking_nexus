"""
RPA Booking SMS - Step 4: fill the Identification page, then Next -> Edit Items
  1. tick "Factory Is Shipper?"
  2-3. type the Comments text (e.g. the invoice number)
  4. Dates -> "Add..." dropdown -> type "ex" -> pick "Ex-Factory Date"
  5. fill Estimated Cargo Ready Date and Ex-Factory Date
  7. Shipment Load Type -> open dropdown -> pick LCL

Run:
  python step4_identification.py 609537759987 --comment "Inv.no 12345" --date 2026-10-19
(runs steps 1-3 first; it stops after filling - it does NOT click Next)
"""
import argparse
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright, Error as PWError

import step1_login as s1
import step2_create_booking as s2
import step3_select_plan as s3

SHOT_DIR = s1.SHOT_DIR


# ---------- helpers ---------------------------------------------------------
def visible_anchor(page, text: str):
    """First VISIBLE element whose own text starts with `text` (ignores error messages)."""
    xp = (f"xpath=//*[text()[starts-with(normalize-space(.), '{text}') "
          f"and not(contains(., 'missing'))]]")
    for fr in page.frames:
        try:
            loc = fr.locator(xp)
            for i in range(min(loc.count(), 30)):
                if loc.nth(i).is_visible():
                    return loc.nth(i)
        except PWError:
            continue
    return None


def input_after_label(page, label: str, tag="input"):
    a = visible_anchor(page, label)
    if a is None:
        return None
    if tag == "input":
        xp = "xpath=following::input[not(@type='hidden') and not(@type='checkbox')][1]"
    else:
        xp = f"xpath=following::{tag}[1]"
    loc = a.locator(xp)
    return loc.first if loc.count() else None


def last_visible(page, make_locator, timeout=6_000):
    """Last visible match (dropdown options are usually at the end of the DOM)."""
    import time
    end = time.time() + timeout / 1000
    while time.time() < end:
        found = None
        for fr in page.frames:
            try:
                loc = make_locator(fr)
                for i in range(min(loc.count(), 40)):
                    if loc.nth(i).is_visible():
                        found = loc.nth(i)
            except PWError:
                continue
        if found is not None:
            return found
        page.wait_for_timeout(250)
    return None


# ---------- individual actions ---------------------------------------------
def tick_factory_is_shipper(page) -> None:
    anchor = visible_anchor(page, "Factory Is Shipper")
    if anchor is None:
        s3.fail(page, "shipper_label_not_found", "'Factory Is Shipper?' label not found.")
    cbs = anchor.locator(
        "xpath=following::*[(self::input and @type='checkbox') or @role='checkbox'][1]")
    if cbs.count() == 0:
        s3.fail(page, "shipper_cb_not_found", "'Factory Is Shipper?' checkbox not found.")
    cb = cbs.first

    def checked() -> bool:
        try:
            return cb.is_checked()
        except PWError:
            return (cb.get_attribute("aria-checked") or "").lower() == "true"

    if checked():
        print("Factory Is Shipper? already ticked")
        return

    attempts = [
        lambda: cb.click(force=True, timeout=3_000),
        lambda: cb.locator("xpath=..").click(timeout=3_000),
        lambda: (lambda b: page.mouse.click(b["x"] + 11, b["y"] + b["height"] / 2 + 36))(
            anchor.bounding_box()),
    ]
    for act in attempts:
        try:
            act()
        except PWError:
            continue
        page.wait_for_timeout(400)
        if checked():
            print("Ticked: Factory Is Shipper?")
            return
    s3.fail(page, "shipper_not_ticked", "Could not tick 'Factory Is Shipper?'.")


def fill_comment(page, text: str) -> None:
    ta = None
    a = visible_anchor(page, "Comments")
    if a is not None:
        loc = a.locator("xpath=following::textarea[1]")
        ta = loc.first if loc.count() else None
    if ta is None:
        ta = s2.first_visible(page, lambda f: f.locator("textarea"), timeout=5_000)
    if ta is None:
        s3.fail(page, "comment_not_found", "Comments box not found.")
    ta.click()
    ta.fill(text)
    ta.press("Tab")
    print(f"Comment filled: {text}")


def add_ex_factory_date(page) -> None:
    if input_after_label(page, "Ex-Factory Date") is not None:
        print("Ex-Factory Date field already present")
        return

    dd = s2.first_visible(page, lambda f: f.get_by_placeholder("Add..."), timeout=5_000) \
        or s2.first_visible(page, lambda f: f.get_by_text(re.compile(r"^\s*Add\.\.\.\s*$")),
                            timeout=5_000)
    if dd is None:
        s3.fail(page, "add_dropdown_not_found", "Dates 'Add...' dropdown not found.")
    dd.click()
    page.keyboard.type("ex", delay=120)

    opt = last_visible(
        page, lambda f: f.get_by_text(re.compile(r"^\s*Ex-Factory Date\s*$", re.I)))
    if opt is not None:
        opt.click()
    else:                                   # keyboard fallback
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
    page.wait_for_timeout(800)

    if input_after_label(page, "Ex-Factory Date") is None:
        s3.fail(page, "ex_factory_not_added", "Ex-Factory Date field did not appear.")
    print("Added: Ex-Factory Date")


def fill_date(page, label: str, value: str) -> None:
    inp = input_after_label(page, label)
    if inp is None:
        s3.fail(page, "date_not_found", f"Date field '{label}' not found.")
    inp.click()
    inp.fill(value)
    inp.press("Tab")
    page.wait_for_timeout(400)
    if inp.input_value() != value:          # retry typing key by key
        inp.click()
        inp.press("Control+A")
        inp.press_sequentially(value, delay=50)
        inp.press("Tab")
        page.wait_for_timeout(400)
    got = inp.input_value()
    print(f"{label} = {got}" + ("" if got == value else "   <-- WARNING: expected " + value))


def select_load_type(page, wanted: str = "LCL") -> None:
    """Shipment Load Type is a typeahead: type the text, then click the option that appears."""
    a = visible_anchor(page, "Shipment Load Type")
    if a is None:
        s3.fail(page, "load_type_label_not_found", "'Shipment Load Type' label not found.")

    # the REAL input (tt-input / combobox), not the background 'hint' input behind it
    combo = a.locator(
        "xpath=following::input[@role='combobox' or contains(@class,'tt-input')][1]")
    if combo.count() == 0:
        s3.fail(page, "load_type_input_not_found", "Shipment Load Type combobox not found.")
    combo = combo.first

    def open_and_type():
        try:
            combo.click(timeout=5_000)
        except PWError:                                  # something overlaps -> click by position
            b = combo.bounding_box()
            if b:
                page.mouse.click(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
        page.keyboard.type(wanted, delay=120)            # real key presses -> triggers typeahead
        page.wait_for_timeout(500)

    def find_option():
        # typeahead suggestions first, then any visible text containing LCL
        return (
            last_visible(page, lambda f: f.locator(".tt-suggestion, [role='option']")
                         .filter(has_text=re.compile(wanted, re.I)), timeout=4_000)
            or last_visible(page, lambda f: f.get_by_text(re.compile(wanted, re.I)),
                            timeout=2_000)
        )

    open_and_type()
    opt = find_option()
    if opt is None:                                      # list closed? open it again via arrow
        b = combo.bounding_box()
        if b:
            page.mouse.click(b["x"] + b["width"] - 14, b["y"] + b["height"] / 2)
        opt = find_option()
    if opt is None:
        s3.fail(page, "lcl_option_not_found", f"Option containing '{wanted}' not found.")

    try:
        opt.click(timeout=5_000)
    except PWError:
        opt.click(force=True)
    page.wait_for_timeout(600)

    val = ""
    try:
        val = combo.input_value()
    except PWError:
        pass
    if wanted.lower() not in val.lower() and "cfs" not in val.lower():
        # typed text stayed but nothing was chosen -> pick first suggestion with the keyboard
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        page.wait_for_timeout(600)
        try:
            val = combo.input_value()
        except PWError:
            pass
    print("Shipment Load Type =", val or "(see screenshot)")


# ---------- Next -> Edit Items ---------------------------------------------
def ok_button(page):
    """Visible OK button of a pop-up (buttons / dialog text only, so table cells never match)."""
    pat = re.compile(r"^\s*ok\s*$", re.I)
    return (
        s2.first_visible(page, lambda f: f.get_by_role("button", name=pat), timeout=400)
        or s2.first_visible(
            page,
            lambda f: f.locator(
                "[role='dialog'], [role='alertdialog'], .modal, .modal-content, .popup"
            ).get_by_text(pat),
            timeout=400,
        )
    )


def edit_items_loaded(page) -> bool:
    return s2.first_visible(
        page, lambda f: f.get_by_text(re.compile(r"Show sublines", re.I)), timeout=400
    ) is not None


def wait_ok_or_ready(page, ready_fn, idle_s: int = 20, hard_cap_s: int = 240):
    """Poll for an OK pop-up ('popup') or for ready_fn() ('ready').
    The idle_s countdown restarts while the loading spinner is showing."""
    start = time.time()
    end = start + idle_s
    while time.time() < end and time.time() - start < hard_cap_s:
        ob = ok_button(page)
        if ob is not None:
            return "popup", ob
        if s1.is_busy(page):
            end = time.time() + idle_s
        elif ready_fn(page):
            return "ready", None
        page.wait_for_timeout(300)
    return "none", None


def dismiss_ok_popups(page, rounds: int = 3) -> None:
    """If an OK pop-up is showing, click OK (repeat for follow-up pop-ups)."""
    for _ in range(rounds):
        ob = ok_button(page)
        if ob is None:
            return
        ob.click()
        print("Pop-up: clicked OK")
        s1.wait_until_idle(page, label="after OK ")


def next_to_edit_items(page) -> None:
    """Step 4 / item 7: click Next on Identification -> Edit Items (click OK on any pop-up)."""
    s1.wait_until_idle(page, label="before Next ")
    if not s3.click_button(page, "Next"):
        s3.fail(page, "next_identification_not_found", "Next button not found on Identification page.")
    print("Clicked Next (Identification -> Edit Items)")

    kind, ob = wait_ok_or_ready(page, edit_items_loaded, 20)
    if kind == "none":
        print("Nothing happened after Next - clicking Next once more")
        s3.click_button(page, "Next")
        kind, ob = wait_ok_or_ready(page, edit_items_loaded, 30)

    for _ in range(3):
        if kind != "popup":
            break
        print("Pop-up detected")
        ob.click()
        print("Pop-up: clicked OK")
        kind, ob = wait_ok_or_ready(page, edit_items_loaded, 30)

    if kind != "ready":
        s3.fail(page, "edit_items_not_loaded",
                "Edit Items page did not open (validation errors on Identification?).")
    s1.wait_until_idle(page, max_s=120, label="loading Edit Items ")
    print("Edit Items page opened")


# ---------- session / args / main ------------------------------------------
def add_common_args(ap) -> None:
    ap.add_argument("plan_ref", nargs="?", default=os.getenv("PACKING_PLAN_REF", ""))
    ap.add_argument("--comment", default=os.getenv("BOOKING_COMMENT", ""))
    ap.add_argument("--date", default=os.getenv("CARGO_READY_DATE", ""),
                    help="yyyy-MM-dd, used for Cargo Ready + Ex-Factory")
    ap.add_argument("--load-type", default="LCL")


def args_ok(args) -> bool:
    if not args.plan_ref or not args.comment or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
        print('Usage: python step4_identification.py <plan_ref> --comment "Inv.no 123" --date 2026-10-19')
        return False
    if not (s1.USERNAME and s1.PASSWORD and s1.URL):
        print("Missing INFOR_USERNAME / INFOR_PASSWORD / INFOR_URL in .env")
        return False
    return True


def open_session(p, args):
    """Steps 1-3: login, CREATE -> Vendor Booking, select plan, Next. Returns browser, context, page."""
    browser = p.chromium.launch(headless=s1.HEADLESS)
    ctx_args = {"viewport": {"width": 1600, "height": 900}}
    if s1.STATE_FILE.exists():
        ctx_args["storage_state"] = str(s1.STATE_FILE)
    context = browser.new_context(**ctx_args)
    page = context.new_page()

    s2.ensure_logged_in(page)
    before = len(context.pages)
    s2.click_create_menu(page)
    page.wait_for_timeout(1500)
    if len(context.pages) > before:
        page = context.pages[-1]
    page.wait_for_load_state("networkidle")
    s3.select_plan_and_next(page, args.plan_ref)

    if s2.first_visible(page, lambda f: f.get_by_text("Factory Is Shipper"),
                        timeout=60_000) is None:
        s3.fail(page, "identification_not_loaded", "Identification page did not load.")
    s1.wait_until_idle(page, max_s=120, label="loading Identification ")
    return browser, context, page


def fill_identification(page, args) -> None:
    """Step 4 items 1-7 (waits for the system after every action)."""
    tick_factory_is_shipper(page)
    s1.wait_until_idle(page, label="after shipper tick ")
    fill_comment(page, args.comment)
    s1.wait_until_idle(page, label="after comment ")
    add_ex_factory_date(page)
    s1.wait_until_idle(page, label="after adding Ex-Factory Date ")
    fill_date(page, "Estimated Cargo Ready Date", args.date)
    s1.wait_until_idle(page, label="after Cargo Ready date ")
    fill_date(page, "Ex-Factory Date", args.date)
    s1.wait_until_idle(page, label="after Ex-Factory date ")
    select_load_type(page, args.load_type)
    s1.wait_until_idle(page, label="after load type ")


def main() -> int:
    print("step4 version 5 (Next -> Edit Items)")
    ap = argparse.ArgumentParser()
    add_common_args(ap)
    args = ap.parse_args()
    if not args_ok(args):
        return 1

    with sync_playwright() as p:
        browser, context, page = open_session(p, args)
        fill_identification(page, args)
        next_to_edit_items(page)

        page.screenshot(path=SHOT_DIR / "after_step4.png", full_page=True)
        print("Step 4 done -> screenshots/after_step4.png")
        context.storage_state(path=s1.STATE_FILE)
        if not s1.HEADLESS:
            input("Press Enter to close the browser...")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
