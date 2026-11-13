"""
RPA Booking SMS - Cancel bookings (loop)

  Application -> Vendor Booking -> fill PO -> Apply -> read the Status column
  For every booking whose status is "New":
     open it
     - wizard page  : click "Preview" (left sidebar) -> click "Cancel" (top) -> click OK in the pop-up
     - plain page without Preview / Cancel (view only) -> cannot cancel here: skip it
     the page must now show "Canceled"
  then go back (Application -> Vendor Booking -> PO -> Apply) and open the NEXT "New" booking,
  until no "New" booking is left.

Run:
  python step_cancel_booking.py 4502402036            (asks YES before cancelling anything)
  optional: --dry-run          only list the bookings, cancel nothing
            --yes              do not ask for confirmation
            --status New       status to cancel (default New)
            --max 100          stop after cancelling this many
"""
import argparse
import re
import sys
import time
import traceback

from playwright.sync_api import sync_playwright, Error as PWError

import step1_login as s1
import step2_create_booking as s2
import step3_select_plan as s3
import step4_identification as s4

SHOT_DIR = s1.SHOT_DIR

# every visible element that has its own text, with its position
LIST_JS = r"""
() => {
  const out = [];
  const rectOf = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return null;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) < 0.05) return null;
    return r;
  };
  for (const el of document.querySelectorAll('body *')) {
    if (out.length > 2500) break;
    let t = '';
    for (const n of el.childNodes) if (n.nodeType === 3) t += n.nodeValue;
    t = t.replace(/\s+/g, ' ').trim();
    if (!t) continue;
    const r = rectOf(el);
    if (!r) continue;
    out.push({t: t, x: r.x, y: r.y, w: r.width, h: r.height, tag: el.tagName});
  }
  return out;
}
"""


# ---------- 1-2) Applications -> Vendor Booking ------------------------------
def open_vendor_booking(page) -> None:
    apps = s2.first_visible(
        page, lambda f: f.get_by_text(re.compile(r"^\s*Applications\s*$", re.I)), timeout=20_000)
    if apps is None:
        s3.fail(page, "applications_not_found", "APPLICATIONS menu not found.")
    apps.click()
    print("Clicked APPLICATIONS")

    marker = lambda f: f.get_by_text(re.compile(r"^\s*Ticket & Label Orders\s*$", re.I))
    anchor = s2.first_visible(page, marker, timeout=5_000)
    if anchor is None:                                  # menu may open on hover
        apps.hover()
        anchor = s2.first_visible(page, marker, timeout=5_000)

    pat = re.compile(r"^\s*Vendor Booking\s*$", re.I)
    cands = []
    for fr in page.frames:
        try:
            loc = fr.get_by_text(pat)
            for i in range(min(loc.count(), 20)):
                el = loc.nth(i)
                if el.is_visible() and el.bounding_box():
                    cands.append(el)
        except PWError:
            continue
    if not cands:
        s3.fail(page, "vendor_booking_menu_not_found", "'Vendor Booking' not found in the Applications menu.")
    if anchor is not None and len(cands) > 1:           # the menu item nearest to 'Ticket & Label Orders'
        ab = anchor.bounding_box()
        cands.sort(key=lambda e: abs(e.bounding_box()["x"] - ab["x"]) + abs(e.bounding_box()["y"] - ab["y"]))
    cands[0].click()
    print("Clicked Vendor Booking")
    s1.wait_until_idle(page, max_s=90, label="loading Vendor Booking ")


# ---------- 3-4) PO filter + Apply -------------------------------------------
def fill_po_filter(page, po: str) -> None:
    xp = "xpath=//*[text()[starts-with(normalize-space(.),'Po Number')]]"
    label = s2.first_visible(page, lambda f: f.locator(xp), timeout=40_000)
    if label is None:
        s3.fail(page, "po_filter_not_found", "'Po Number(s)' filter not found (is the Filter panel open?).")
    box = label.locator(
        "xpath=following::input[not(@type='hidden') and not(@type='checkbox') and not(@readonly)][1]").first
    box.click()
    box.fill(po)
    if box.input_value() != po:
        box.press("Control+A")
        box.press_sequentially(po, delay=50)
    got = box.input_value()
    print(f"Po Number(s) filter = {got}" + ("" if got == po else "   <-- WARNING: expected " + po))


def click_apply(page) -> None:
    if not s3.click_button(page, "Apply"):
        s3.fail(page, "apply_not_found", "Apply button not found.")
    print("Clicked Apply")
    s1.wait_until_idle(page, max_s=90, label="after Apply ")


# ---------- 5) read the results: Request Ref Number + Status ------------------
def read_bookings(page):
    """[(request_ref, status), ...] from the results table, top to bottom."""
    for fr in page.frames:
        try:
            items = fr.evaluate(LIST_JS)
        except PWError:
            continue
        hdrs = [i for i in items if i["t"] == "Status"]
        if not hdrs:
            continue
        hdr = max(hdrs, key=lambda i: i["x"])           # table header (the filter label is further left)
        cells = [i for i in items
                 if hdr["x"] - 30 <= i["x"] <= hdr["x"] + hdr["w"] + 60
                 and i["y"] > hdr["y"] + 10 and len(i["t"]) <= 20]
        first_by_ref = {}                               # Request Ref = leftmost copy of each number
        for i in items:
            if re.fullmatch(r"\d{10,13}", i["t"]) and i["y"] > hdr["y"]:
                if i["t"] not in first_by_ref or i["x"] < first_by_ref[i["t"]]["x"]:
                    first_by_ref[i["t"]] = i
        rows = []
        for ref, r in sorted(first_by_ref.items(), key=lambda kv: kv[1]["y"]):
            near = sorted((c for c in cells if abs(c["y"] - r["y"]) < 30), key=lambda c: abs(c["y"] - r["y"]))
            rows.append((ref, near[0]["t"] if near else "?"))
        if rows:
            return rows
    return []


def wait_for_results(page, timeout_s: int = 40):
    end = time.time() + timeout_s
    while time.time() < end:
        rows = read_bookings(page)
        if rows:
            return rows
        page.wait_for_timeout(700)
    return []


def settle(page, label: str = "") -> None:
    """Wait for the page to calm down. 'networkidle' may never come on Infor pages -> ignore its timeout."""
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PWError:
        pass
    s1.wait_until_idle(page, max_s=120, label=label)


# ---------- 6) open the booking ---------------------------------------------
def open_booking(page, ref: str) -> None:
    pat = re.compile(rf"^\s*{ref}\s*$")
    cands = []
    for fr in page.frames:
        try:
            loc = fr.get_by_text(pat)
            for i in range(min(loc.count(), 10)):
                el = loc.nth(i)
                b = el.bounding_box() if el.is_visible() else None
                if b:
                    cands.append((b["x"], el))
        except PWError:
            continue
    if not cands:
        s3.fail(page, "booking_link_not_found", f"Booking {ref} not found on the page.")
    cands.sort(key=lambda c: c[0])                      # leftmost = the Request Ref Number link
    cands[0][1].click()
    print(f"Clicked booking {ref}")
    settle(page, "opening booking ")



# ---------- booking page: wizard / view only ---------------------------------
def _find_text(page, pattern, accept):
    """First visible element whose exact text matches `pattern` and whose box passes accept(box)."""
    for fr in page.frames:
        try:
            loc = fr.get_by_text(pattern)
            for i in range(min(loc.count(), 20)):
                el = loc.nth(i)
                if not el.is_visible():
                    continue
                b = el.bounding_box()
                if b and accept(b):
                    return el
        except PWError:
            continue
    return None


def sidebar_preview(page):
    """'Preview' step in the left sidebar of the wizard."""
    return _find_text(page, re.compile(r"^\s*Preview\s*$"), lambda b: b["x"] < 450 and b["y"] > 150)


def header_cancel(page):
    """'Cancel' action in the top header bar (not the Cancel button of a pop-up)."""
    return _find_text(page, re.compile(r"^\s*Cancel\s*$"), lambda b: b["y"] < 200)


def canceled_pill(page):
    """Status pill 'Canceled' under the page title."""
    return _find_text(page, re.compile(r"^\s*Cancel(l)?ed\s*$", re.I),
                      lambda b: b["y"] < 260 and b["x"] < 800)


def wait_booking_ready(page, timeout_s: int = 45):
    """'wizard' (Preview step visible), 'view' (Cancel visible) or None (neither)."""
    start = time.time()
    end = start + timeout_s
    while time.time() < end and time.time() - start < 180:
        if s1.is_busy(page):
            end = time.time() + timeout_s            # still loading -> keep waiting
        else:
            if sidebar_preview(page) is not None:
                return "wizard"
            if header_cancel(page) is not None:
                return "view"
        page.wait_for_timeout(400)
    return None


def cancel_current_booking(page, ref: str) -> str:
    """Returns 'canceled' or 'cannot' (view-only page: no Preview / Cancel available)."""
    mode = wait_booking_ready(page)
    if mode is None:
        page.screenshot(path=SHOT_DIR / f"cancel_skip_{ref}.png", full_page=True)
        print(f"   {ref}: no Preview step and no Cancel button on this page -> cannot cancel here")
        return "cannot"

    if mode == "wizard":                                        # go to Preview
        kind = "none"
        try:
            sidebar_preview(page).click()
            print(f"   {ref}: clicked Preview")
            for _ in range(3):
                kind, ob = s4.wait_ok_or_ready(page, lambda p: header_cancel(p) is not None, 25)
                if kind == "popup":
                    ob.click()
                    print("   Pop-up: clicked OK")
                    continue
                break
        except PWError as e:
            print(f"   {ref}: problem on the way to Preview: {str(e).splitlines()[0]}")
        if kind != "ready":
            page.screenshot(path=SHOT_DIR / f"cancel_no_button_{ref}.png", full_page=True)
            print(f"   {ref}: Cancel button did not appear on the Preview page -> skipped")
            return "cannot"
        s1.wait_until_idle(page, label="Preview page ")

    btn = header_cancel(page)
    btn.click()                                                 # click Cancel
    print(f"   {ref}: clicked Cancel")

    kind, ob = s4.wait_ok_or_ready(page, lambda p: False, 15)   # "Are you sure ...?" pop-up
    if kind != "popup":
        s3.fail(page, "cancel_confirm_not_shown", f"No confirmation pop-up after clicking Cancel on {ref}.")
    msg = s2.first_visible(page, lambda f: f.get_by_text(re.compile(r"Are you sure", re.I)), timeout=800)
    print("   Pop-up:", msg.inner_text().strip() if msg else "(confirmation)")
    ob.click()                                                  # click OK
    print(f"   {ref}: clicked OK")
    s1.wait_until_idle(page, max_s=120, label="cancelling ")

    end = time.time() + 40
    while time.time() < end:
        if canceled_pill(page) is not None:
            page.screenshot(path=SHOT_DIR / f"canceled_{ref}.png", full_page=True)
            print(f"   {ref}: status is now Canceled")
            return "canceled"
        page.wait_for_timeout(500)
    s3.fail(page, f"cancel_not_confirmed_{ref}", f"Booking {ref}: status 'Canceled' did not appear after OK.")


# ---------- navigation: Application -> Vendor Booking -> PO -> Apply ---------
def go_to_list(page, po: str):
    try:
        open_vendor_booking(page)
    except RuntimeError:                                        # session may have timed out
        print("Could not open the menu - logging in again...")
        s2.ensure_logged_in(page)
        open_vendor_booking(page)
    fill_po_filter(page, po)
    click_apply(page)
    return wait_for_results(page)


def open_booking_page(page, ref: str):
    before = len(page.context.pages)
    open_booking(page, ref)
    if len(page.context.pages) > before:                        # opened in a new tab
        page = page.context.pages[-1]
        settle(page, "opening booking (new tab) ")
    return page


def main() -> int:
    print("cancel step version 3 (cancel loop, safer waits)")
    ap = argparse.ArgumentParser()
    ap.add_argument("po", help="PO number to search, e.g. 4502402036")
    ap.add_argument("--status", default="New")
    ap.add_argument("--dry-run", action="store_true", help="only list the bookings, cancel nothing")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--max", type=int, default=100, help="stop after cancelling this many bookings")
    args = ap.parse_args()
    if not re.fullmatch(r"\d{6,12}", args.po):
        print("Usage: python step_cancel_booking.py <PO number>")
        return 1
    if not (s1.USERNAME and s1.PASSWORD and s1.URL):
        print("Missing INFOR_USERNAME / INFOR_PASSWORD / INFOR_URL in .env")
        return 1

    canceled, skipped = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=s1.HEADLESS)
        ctx_args = {"viewport": {"width": 1600, "height": 900}}
        if s1.STATE_FILE.exists():
            ctx_args["storage_state"] = str(s1.STATE_FILE)
        context = browser.new_context(**ctx_args)
        page = context.new_page()
        s2.ensure_logged_in(page)                               # login (or re-activate)

        rows = go_to_list(page, args.po)
        page.screenshot(path=SHOT_DIR / "cancel_results.png", full_page=True)
        if not rows:
            print(f"No bookings found for PO {args.po} (see screenshots/cancel_results.png)")
            browser.close()
            return 2
        print(f"\nBookings containing PO {args.po}:")
        for ref, st in rows:
            print(f"   {ref}   {st}")
        todo = [ref for ref, st in rows if st.strip().lower() == args.status.lower()]
        print(f"\nBookings with status '{args.status}': {len(todo)}")
        if not todo:
            browser.close()
            return 0
        if args.dry_run:
            print("Dry run - nothing cancelled.")
            browser.close()
            return 0
        if not args.yes:
            ans = input(f"Cancel the {len(todo)} booking(s) above with status '{args.status}'? "
                        f"This cannot be undone. Type YES to continue: ")
            if ans.strip().upper() != "YES":
                print("Aborted - nothing cancelled.")
                browser.close()
                return 0

        # ----- loop: open the next 'New' booking, cancel it, go back -----
        while len(canceled) < args.max:
            candidates = [ref for ref, st in rows
                          if st.strip().lower() == args.status.lower()
                          and ref not in canceled and ref not in skipped]
            if not candidates:
                print("\nNo more bookings with status '%s' to cancel." % args.status)
                break
            ref = candidates[0]
            print(f"\n=== Booking {ref}  ({len(canceled)} cancelled, {len(skipped)} skipped so far) ===")
            try:
                page = open_booking_page(page, ref)
            except Exception as e:                              # opening failed: nothing was cancelled -> skip it
                print(f"   {ref}: could not open the booking -> skipped")
                print("   ERROR:", str(e).splitlines()[0] if str(e) else repr(e))
                traceback.print_exc()
                try:
                    page.screenshot(path=SHOT_DIR / f"cancel_open_error_{ref}.png", full_page=True)
                except PWError:
                    pass
                skipped.append(ref)
                rows = go_to_list(page, args.po)
                continue
            result = cancel_current_booking(page, ref)          # errors after Cancel is clicked stop the run
            (canceled if result == "canceled" else skipped).append(ref)
            rows = go_to_list(page, args.po)                    # back: Application -> Vendor Booking -> PO -> Apply
            if not rows:
                print("No bookings left in the list.")
                break

        print("\n===== SUMMARY =====")
        print(f"Cancelled ({len(canceled)}): {', '.join(canceled) or '-'}")
        print(f"Skipped - cannot cancel ({len(skipped)}): {', '.join(skipped) or '-'}")
        (SHOT_DIR / "cancel_summary.txt").write_text(
            "CANCELLED\n" + "\n".join(canceled) + "\n\nSKIPPED\n" + "\n".join(skipped), encoding="utf-8")
        context.storage_state(path=s1.STATE_FILE)
        if not s1.HEADLESS:
            input("Press Enter to close the browser...")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
