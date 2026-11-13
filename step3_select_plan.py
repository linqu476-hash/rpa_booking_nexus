"""
RPA Booking SMS - Step 3: fill Packing Plan Ref -> Apply -> select row -> Next
                         (-> click YES if the "already linked" pop-up appears)

Run:  python step3_select_plan.py 609537759987
  or  set PACKING_PLAN_REF in .env and run: python step3_select_plan.py
This script runs steps 1-2 as needed (login + CREATE -> Vendor Booking).
"""
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright, Error as PWError

import step1_login as s1
import step2_create_booking as s2

SHOT_DIR = s1.SHOT_DIR


# ---------- helpers ---------------------------------------------------------
def fail(page, name: str, msg: str):
    s1.dump_debug(page, name)
    raise RuntimeError(f"{msg} See screenshots/{name}.png")


def find_ref_input(page):
    """Locate the 'Packing Plan Ref Number' filter text box."""
    label = "Packing Plan Ref Number"

    def make(frame):
        # inputs that follow the label text; we pick the first editable, visible one below
        return frame.locator(
            f"xpath=(//*[text()[starts-with(normalize-space(.),'{label}')]]"
            f"/following::input[not(@type='hidden') and not(@readonly)])[1]"
        )

    box = s2.first_visible(page, make, timeout=20_000)
    if box is not None:
        return box

    # fallback: 2nd visible text input in the filter panel (1st is Create Date)
    def make_nth(frame):
        return frame.locator(
            "input:visible:not([type='hidden']):not([type='checkbox'])"
            ":not([type='radio']):not([type='password'])"
        ).nth(1)

    return s2.first_visible(page, make_nth, timeout=5_000)


def click_button(page, text: str, timeout=15_000) -> bool:
    pattern = re.compile(rf"^\s*{re.escape(text)}\s*$", re.I)
    btn = s2.first_visible(
        page,
        lambda f: f.get_by_role("button", name=pattern),
        timeout=timeout,
    ) or s2.first_visible(page, lambda f: f.get_by_text(pattern), timeout=3_000)
    if btn is None:
        return False
    btn.click()
    return True


def is_selected(page, timeout=3_000) -> bool:
    return s2.first_visible(
        page, lambda f: f.get_by_text(re.compile(r"^\s*1 Selected")), timeout=timeout
    ) is not None


def tick_row(page, plan_ref: str) -> bool:
    """Tick the checkbox of the row for plan_ref. Tries several methods."""
    link = s2.first_visible(page, lambda f: f.get_by_role("link", name=plan_ref), timeout=10_000) \
        or s2.first_visible(page, lambda f: f.get_by_text(plan_ref), timeout=5_000)
    if link is None:
        return False
    row = link.locator("xpath=ancestor::*[self::tr or @role='row'][1]")

    # method 1: real/aria checkbox inside the row
    try:
        cb = row.locator("input[type='checkbox'], [role='checkbox'], [class*='checkbox' i]").first
        cb.click(force=True, timeout=4_000)
        if is_selected(page):
            return True
    except PWError:
        pass

    # method 2: click the row's first cell
    try:
        row.locator("td, [role='gridcell']").first.click(timeout=4_000)
        if is_selected(page):
            return True
    except PWError:
        pass

    # method 3: raw mouse click where the checkbox is drawn (left edge of the row)
    try:
        box = row.bounding_box()
        if box:
            for dx, dy in ((20, 14), (20, 20), (18, 10), (24, 16)):
                page.mouse.click(box["x"] + dx, box["y"] + dy)
                if is_selected(page, timeout=1_500):
                    return True
    except PWError:
        pass

    # method 4: header 'select all' checkbox (only 1 row is shown after filtering)
    try:
        hdr = s2.first_visible(
            page,
            lambda f: f.locator("th input[type='checkbox'], th [role='checkbox'], "
                                "[role='columnheader'] [role='checkbox']"),
            timeout=3_000,
        )
        if hdr is not None:
            hdr.click(force=True)
            return is_selected(page)
    except PWError:
        pass
    return False


def _yes_button(page):
    pat = re.compile(r"^\s*yes\s*$", re.I)
    return (
        s2.first_visible(page, lambda f: f.get_by_role("button", name=pat), timeout=400)
        or s2.first_visible(page, lambda f: f.get_by_text(pat), timeout=400)
    )


def _identification_loaded(page) -> bool:
    return s2.first_visible(
        page, lambda f: f.get_by_text("Factory Is Shipper"), timeout=400
    ) is not None


def wait_popup_or_next_page(page, idle_s: int, hard_cap_s: int = 240):
    """Poll for the YES pop-up ('popup') or the Identification page ('page').
    The idle_s countdown restarts while the loading spinner is showing."""
    start = time.time()
    end = start + idle_s
    while time.time() < end and time.time() - start < hard_cap_s:
        yb = _yes_button(page)
        if yb is not None:
            return "popup", yb
        if s1.is_busy(page):
            end = time.time() + idle_s          # system still working -> keep waiting
        elif _identification_loaded(page):
            return "page", None
        page.wait_for_timeout(300)
    return "none", None


# ---------- step 3 ----------------------------------------------------------
def select_plan_and_next(page, plan_ref: str) -> None:
    s1.wait_until_idle(page, label="before filtering ")
    # 3) fill packing plan ref
    box = find_ref_input(page)
    if box is None:
        fail(page, "ref_input_not_found", "Packing Plan Ref Number box not found.")
    box.click()
    box.fill(plan_ref)
    print(f"Filled Packing Plan Ref Number = {plan_ref}")

    # 4) Apply
    if not click_button(page, "Apply"):
        fail(page, "apply_not_found", "Apply button not found.")
    print("Clicked Apply")
    s1.wait_until_idle(page, label="after Apply ")

    # wait for the result row containing our plan number
    row_text = s2.first_visible(page, lambda f: f.get_by_text(plan_ref, exact=False),
                                timeout=30_000)
    if row_text is None:
        fail(page, "plan_not_in_results",
             f"Plan {plan_ref} did not appear in results (wrong number / older than 30 days?).")

    # 5a) tick the row checkbox
    if not tick_row(page, plan_ref):
        fail(page, "not_selected", "Could not tick the row checkbox ('1 Selected' never appeared).")
    print("Row selected")
    s1.wait_until_idle(page, label="after selecting row ")
    page.wait_for_timeout(500)

    # 5b) Next
    if not click_button(page, "Next"):
        fail(page, "next_not_found", "Next button not found.")
    print("Clicked Next")

    # ---- after Next: either the "already linked ... Continue?" pop-up or the next page ----
    kind, yb = wait_popup_or_next_page(page, 20)
    if kind == "none":
        print("Nothing happened after Next - clicking Next once more")
        click_button(page, "Next")
        kind, yb = wait_popup_or_next_page(page, 30)

    if kind == "popup":
        try:
            msg = s2.first_visible(page, lambda f: f.get_by_text(re.compile("already linked", re.I)),
                                   timeout=500)
            print("POP-UP:", msg.inner_text().strip().replace("\n", " ") if msg else "(YES/NO dialog)")
        except PWError:
            print("POP-UP detected")
        if os.getenv("AUTO_CONFIRM_POPUP", "true").lower() != "true":
            print("Pop-up left open (AUTO_CONFIRM_POPUP=false).")
            return
        # click YES (and any follow-up YES) until the Identification page opens
        for _ in range(3):
            yb.click()
            print("Pop-up: clicked YES")
            kind, yb = wait_popup_or_next_page(page, 40)
            if kind != "popup":
                break
    elif kind == "page":
        print("No pop-up; Identification page opened")

    if kind != "page":
        fail(page, "next_no_result",
             "Identification page did not open after Next / YES.")
    page.wait_for_load_state("networkidle")
    s1.wait_until_idle(page, max_s=120, label="loading Identification page ")


def main() -> int:
    print("step3 version 4 (waits for loader)")
    plan_ref = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("PACKING_PLAN_REF", "")).strip()
    if not plan_ref:
        print("Usage: python step3_select_plan.py <packing_plan_ref>")
        return 1
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

        s2.ensure_logged_in(page)

        pages_before = len(context.pages)
        s2.click_create_menu(page)
        page.wait_for_timeout(1500)
        if len(context.pages) > pages_before:
            page = context.pages[-1]
        page.wait_for_load_state("networkidle")

        select_plan_and_next(page, plan_ref)

        print("Now at ->", page.url)
        page.screenshot(path=SHOT_DIR / "after_step3.png", full_page=True)
        context.storage_state(path=s1.STATE_FILE)

        if not s1.HEADLESS:
            input("Press Enter to close the browser...")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
