"""
RPA Booking SMS - Step 5: Edit Items page
  (runs steps 1-4 first; step 4 ends by clicking Next -> Edit Items)

  1. click OK on any pop-up
  2. click the FIRST PO number (under "Order #") -> its details open below the table
  3. change that PO's Commodity Description to:
        "<invoice> DD:<dd-MMM-yyyy> SEASON:<season> <product>"
     e.g.  Inv number DD:19-OCT-2026 SEASON:FW27-SMS WOMEN'S T-SHIRT
  4. click the error message (red badge next to "New"), copy the PO number of the first
     "Commodity Description is missing" message, click OK, paste the PO in the Line Items
     search box, and when the PO shows in 1 line click the PO by text

Run:
  python step5_edit_items.py 609537759987 --comment "Inv number" --date 2026-10-19
  optional: --invoice "Inv number"  --season FW27-SMS  --product "WOMEN'S T-SHIRT"
(the loop that fills the other POs is step 6: step6_fill_loop.py)
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
import step4_identification as s4

SHOT_DIR = s1.SHOT_DIR
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
PO_RE = re.compile(r"^\s*\d{10}\s*$")


def dd_text(date: str) -> str:
    """2026-10-19 -> 19-OCT-2026"""
    y, m, d = date.split("-")
    return f"{int(d):02d}-{MONTHS[int(m) - 1]}-{y}"


def _lines(page):
    out = []
    for fr in page.frames:
        try:
            txt = fr.evaluate("document.body ? document.body.innerText : ''")
        except PWError:
            continue
        out += [l.strip() for l in txt.splitlines() if l.strip()]
    return out


# ---------- 2) click the first PO (number under "Order #") -------------------
def find_first_po_cell(page, timeout_s: int = 30):
    """Top-most visible text that is exactly a 10-digit number = first PO in the table."""
    end = time.time() + timeout_s
    while time.time() < end:
        best = None
        for fr in page.frames:
            try:
                loc = fr.get_by_text(PO_RE)
                for i in range(min(loc.count(), 80)):
                    el = loc.nth(i)
                    if not el.is_visible():
                        continue
                    b = el.bounding_box()
                    if not b:
                        continue
                    key = (round(b["y"]), b["x"])
                    if best is None or key < best[0]:
                        best = (key, el)
            except PWError:
                continue
        if best is not None:
            return best[1]
        page.wait_for_timeout(400)
    return None


def details_open(page, po=None, timeout: int = 4_000) -> bool:
    """Details of the clicked PO are showing: the Pack Line heading '<PO> / <line> / <item>'
    (for a given PO), otherwise row selected ('1 Selected') or the 'Pack Line' heading."""
    if po:
        return s2.first_visible(
            page, lambda f: f.get_by_text(re.compile(rf"{po}\s*/\s*\d+")), timeout=timeout
        ) is not None
    if s3.is_selected(page, timeout):
        return True
    return s2.first_visible(
        page, lambda f: f.get_by_text(re.compile(r"^\s*Pack Line\s*$")), timeout=500
    ) is not None


def click_first_po(page):
    """Click the first PO number. Returns the PO, None if none found, '' if details did not open."""
    cell = find_first_po_cell(page)
    if cell is None:
        return None
    po = cell.inner_text().strip()
    print("First PO:", po)

    # a) click the PO number itself
    try:
        cell.click(timeout=5_000)
        if details_open(page):
            return po
    except PWError:
        pass
    # b) the checkbox in the same row
    try:
        row = cell.locator("xpath=ancestor::*[self::tr or @role='row' or contains(@class,'row')][1]")
        row.locator("input[type='checkbox'], [role='checkbox'], [class*='checkbox' i]") \
           .first.click(force=True, timeout=4_000)
        if details_open(page):
            return po
    except PWError:
        pass
    # c) mouse click where the checkbox is drawn (left of the PO number)
    try:
        b = cell.bounding_box()
        if b:
            page.mouse.click(b["x"] - 40, b["y"] + b["height"] / 2)
            if details_open(page):
                return po
    except PWError:
        pass
    return ""


# ---------- 3) Commodity Description ----------------------------------------
def fill_commodity(page, text: str) -> bool:
    xp = "xpath=//*[normalize-space(text())='Commodity Description']"   # exact, not '(Local Language)'
    anchor = s2.first_visible(page, lambda f: f.locator(xp), timeout=25_000)
    if anchor is None:
        s3.fail(page, "commodity_label_not_found",
                "'Commodity Description' not found (is the PO row open?).")
    field = anchor.locator(
        "xpath=following::*[(self::input and not(@type='hidden') and not(@type='checkbox')) "
        "or self::textarea][1]").first
    print("Old Commodity Description =", repr(field.input_value()))
    field.click()
    field.press("Control+A")
    field.fill(text)
    field.press("Tab")
    page.wait_for_timeout(400)
    if field.input_value() != text:                 # retry key by key
        field.click()
        field.press("Control+A")
        field.press_sequentially(text, delay=30)
        field.press("Tab")
        page.wait_for_timeout(400)
    got = field.input_value()
    print("New Commodity Description =", got
          + ("" if got == text else "   <-- WARNING: expected: " + text))
    return got == text


# ---------- 4) error list -> copy the first Order missing Commodity Description ----
MISSING_RE = re.compile(
    r"Order\s+(\d{10})\s+Item\s+(\d+)\s*,\s*([A-Za-z0-9\-]+)\s*:\s*"
    r"Commodity Description is missing", re.I)
CAND_EXTRA = ", [contenteditable='true'], [contenteditable=''], [role='searchbox'], [role='textbox']"
INPUT_SEL = ("input:not([type='hidden']):not([type='checkbox']):not([type='radio'])"
             ":not([name='pager-pageno']):not(#navbarsearch)")


def find_error_badge(page):
    """The red badge with the error count, next to the 'New' status pill under the title."""
    new_pill = s2.first_visible(page, lambda f: f.locator("xpath=//*[normalize-space(text())='New']"),
                                timeout=8_000)
    if new_pill is None:
        return None, None
    nb = new_pill.bounding_box()
    badge = new_pill.locator(
        "xpath=following::*[string-length(normalize-space(.))>0 and "
        "translate(normalize-space(.),'0123456789','')=''][1]")
    if badge.count():
        bb = badge.first.bounding_box()
        if bb and nb and abs(bb["y"] - nb["y"]) < 40 and abs(bb["x"] - nb["x"]) < 200:
            return badge.first, nb
    return None, nb


def open_errors(page):
    """1. click on the error message badge. Returns the page text lines while the error list
    is open, or None when no error list appears."""
    s1.wait_until_idle(page, label="before opening errors ")
    for method in ("badge", "position"):
        badge, nb = find_error_badge(page)
        if badge is None and nb is None:
            return None
        try:
            if method == "badge" and badge is not None:
                badge.click(timeout=5_000)
            elif nb is not None:
                page.mouse.click(nb["x"] + nb["width"] + 35, nb["y"] + nb["height"] / 2)
            else:
                continue
        except PWError:
            continue
        print(f"Clicked the error badge ({method})")
        end = time.time() + 8
        while time.time() < end:
            if s4.ok_button(page) is not None:
                page.wait_for_timeout(500)
                return _lines(page)
            page.wait_for_timeout(300)
    return None


def first_missing_order(lines, skip=()):
    """2. copy the PO number from the first 'Commodity Description is missing' message.
    `skip` = set of (po, item) already filled in this run: the error list can still show them
    (it is not refreshed after a fill), so the next PO that is NOT in `skip` is returned."""
    for m in MISSING_RE.finditer(" ".join(lines)):
        if (m.group(1), m.group(2)) not in skip:
            return (m.group(1), m.group(2), m.group(3))
    return None


SELECT_JS = """
(po) => {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    const t = n.nodeValue || '';
    const i = t.indexOf(po);
    if (i < 0) continue;
    const host = n.parentElement ? n.parentElement.textContent : '';
    if (!/Commodity Description is missing/i.test(host)) continue;
    if (n.parentElement && n.parentElement.scrollIntoView) n.parentElement.scrollIntoView({block: 'center'});
    const r = document.createRange();
    r.setStart(n, i); r.setEnd(n, i + po.length);
    const b = r.getBoundingClientRect();
    if (b.width > 0 && b.height > 0 && b.top >= 0 && b.bottom <= window.innerHeight)
      return {x: b.x + b.width / 2, y: b.y + b.height / 2};
  }
  return null;
}
"""


def clipboard_text(page):
    try:
        return page.evaluate("navigator.clipboard.readText()")
    except PWError:
        return None


def select_po_in_error_message(page, po: str) -> bool:
    """2. double-click the PO number inside the error message, copy it (Ctrl+C) and make sure the
    clipboard really holds it (otherwise write it there). True when the clipboard == PO."""
    try:
        pt = page.main_frame.evaluate(SELECT_JS, po)
    except PWError:
        pt = None
    if pt:
        page.mouse.dblclick(pt["x"], pt["y"])
        page.wait_for_timeout(300)
        page.keyboard.press("Control+C")
        page.wait_for_timeout(200)
        print(f"Clicked on the error message: selected + copied PO {po}")
    else:
        print("  (could not locate the PO text in the message to highlight it)")
    got = clipboard_text(page)
    if got is not None and got.strip() == po:
        print(f"  clipboard holds {po}")
        return True
    try:                                                # copy failed -> put the PO in the clipboard
        page.evaluate("t => navigator.clipboard.writeText(t)", po)
        got = clipboard_text(page)
    except PWError:
        got = None
    ok = got is not None and got.strip() == po
    print("  clipboard set to the PO" if ok else "  clipboard not available -> will type the PO instead")
    return ok


def close_errors(page) -> None:
    """3. click OK and make sure the error list is really closed."""
    for _ in range(3):
        ob = s4.ok_button(page)
        if ob is None:
            break
        try:
            ob.click(timeout=5_000)
            print("Clicked OK (closed error list)")
        except PWError:
            pass
        page.wait_for_timeout(600)
        s1.wait_until_idle(page, label="after closing errors ")
        end = time.time() + 4
        while time.time() < end and s4.ok_button(page) is not None:
            page.wait_for_timeout(300)
        if s4.ok_button(page) is None:
            break


# ---------- Line Items search box (safe: never types into the grid) ----------
def _all_visible(page, make_locator, limit=40):
    """EVERY visible match in every frame (first_visible only returns the first one)."""
    out = []
    for fr in page.frames:
        try:
            loc = make_locator(fr)
            for i in range(min(loc.count(), limit)):
                el = loc.nth(i)
                if el.is_visible():
                    out.append(el)
        except PWError:
            continue
    return out


def all_po_ys(page, min_y=120):
    """y of every visible 10-digit PO text below min_y."""
    ys = []
    for el in _all_visible(page, lambda f: f.get_by_text(PO_RE), limit=30):
        b = el.bounding_box()
        if b and b["y"] > min_y:
            ys.append(b["y"])
    return sorted(ys)


def first_po_y(page):
    """Top y of the first visible PO number (10 digits) = first table row."""
    ys = all_po_ys(page)
    return ys[0] if ys else None


def _header_top(page):
    """Limit for the search box: it must end ABOVE this y.
    v12: only PO numbers BELOW the 'Line Items' heading count, so a PO shown in the order
    header (above the toolbar) can no longer push the limit above the search box."""
    pos = all_po_ys(page)
    heads = sorted(b["y"] for b in _toolbar_boxes(page)[0] if b["y"] > 120)
    for h in reversed(heads):                       # lowest heading that still has PO rows under it
        below = [y for y in pos if y > h + 10]
        if below:
            return below[0] - 12
    if pos:
        return pos[0] - 12
    ys = []
    for h in _all_visible(page, lambda f: f.locator("xpath=//*[normalize-space(text())='Order #']")):
        b = h.bounding_box()
        if b and b["y"] > 120:
            ys.append(b["y"])
    return min(ys) if ys else None


def _is_above_grid(page, el) -> bool:
    hy = _header_top(page)
    b = el.bounding_box()
    if hy is None or not b:
        return True
    return b["y"] + b["height"] <= hy + 2


def describe_inputs(page) -> None:
    print("DEBUG visible inputs (x, y, w, h | type | class | placeholder):")
    for fr in page.frames:
        try:
            loc = fr.locator("input")
            for i in range(min(loc.count(), 60)):
                el = loc.nth(i)
                if not el.is_visible():
                    continue
                b = el.bounding_box() or {}
                print("  ", round(b.get("x", -1)), round(b.get("y", -1)), round(b.get("width", -1)),
                      round(b.get("height", -1)), "|", el.get_attribute("type"), "|",
                      (el.get_attribute("class") or "")[:40], "|", el.get_attribute("placeholder"))
        except PWError:
            continue


def _toolbar_boxes(page):
    """Bounding boxes of EVERY visible 'Line Items' / 'Show sublines' text and '+ Filter' button.
    (v11 trusted only the first match; if that was some other 'Line Items' text, no input matched.)"""
    left, filt = [], []
    for el in _all_visible(page, lambda f: f.locator(
            "xpath=//*[normalize-space(text())='Line Items' or starts-with(normalize-space(text()),'Show sublines')]")):
        b = el.bounding_box()
        if b:
            left.append(b)
    for el in _all_visible(page, lambda f: f.locator(
            "xpath=//button[contains(normalize-space(.),'Filter')] | //*[@role='button'][contains(normalize-space(.),'Filter')]")):
        b = el.bounding_box()
        if b:
            filt.append(b)
    return left, filt


def _candidate_inputs(page, hy):
    """Visible text inputs above the table header (never a grid cell)."""
    out = []
    for fr in page.frames:
        try:
            loc = fr.locator(INPUT_SEL + CAND_EXTRA)
            for i in range(min(loc.count(), 120)):
                el = loc.nth(i)
                if not el.is_visible():
                    continue
                b = el.bounding_box()
                if not b or b["width"] < 40 or b["y"] <= 110:      # <= 110: app bars / global search
                    if b:
                        print(f"    skip input x={round(b['x'])} y={round(b['y'])} w={round(b['width'])} (app bar / too narrow)")
                    continue
                if hy is not None and b["y"] + b["height"] > hy + 2:
                    print(f"    skip input x={round(b['x'])} y={round(b['y'])} w={round(b['width'])} (below grid limit {round(hy)})")
                    continue
                out.append((b, el))
        except PWError:
            continue
    return out


def _focused_input_above_grid(page):
    """After a mouse click: the focused <input>, if it is above the table."""
    for fr in page.frames:
        try:
            el = fr.locator("input:focus").first
            if el.count() and el.is_visible() and _is_above_grid(page, el):
                return el
        except PWError:
            continue
    return None


def find_search_box(page, timeout_s: int = 15):
    """The Line Items search input. Tries, in order:
       1) an input on the same row as ANY 'Line Items' / 'Show sublines' text or '+ Filter' button,
       2) an input with search-like type/placeholder/aria-label/class above the table,
       3) the input nearest above the table header,
       4) click where the pill is drawn (left of '+ Filter') and use the focused input.
    Never returns anything at/below the table header."""
    end = time.time() + timeout_s
    while time.time() < end:
        hy = _header_top(page)
        cands = _candidate_inputs(page, hy)
        left, filt = _toolbar_boxes(page)

        def cy(b):
            return b["y"] + b["height"] / 2

        # 1) same row as a toolbar landmark
        best = None
        for b, el in cands:
            score = None
            for f in filt:                                   # pill sits just LEFT of '+ Filter'
                if abs(cy(b) - cy(f)) < 30 and 0 <= f["x"] - (b["x"] + b["width"]) < 120:
                    score = 0
            for l in left:                                   # ... and to the RIGHT of the heading
                if abs(cy(b) - cy(l)) < 40 and b["x"] >= l["x"]:
                    score = 1 if score is None else score
            if score is not None and (best is None or (score, -b["y"]) < best[0]):
                best = ((score, -b["y"]), el)
        if best:
            return best[1]

        # 2) search-like attributes
        for b, el in cands:
            try:
                hint = " ".join((el.get_attribute(a) or "") for a in ("type", "placeholder", "aria-label", "class", "id", "name")).lower()
            except PWError:
                continue
            if "search" in hint or "filter" in hint:
                return el

        # 3) nearest input above the header (below the app bars, y > 100)
        if cands and hy is not None:
            low = [c for c in cands if c[0]["y"] > 100]
            if low:
                low.sort(key=lambda c: hy - (c[0]["y"] + c[0]["height"]))
                return low[0][1]

        # 4) click the pill by position
        if filt:
            f = sorted(filt, key=lambda b: b["y"])[0]
            page.mouse.click(f["x"] - 60, cy(f))
            page.wait_for_timeout(300)
            el = _focused_input_above_grid(page)
            if el is not None:
                return el
        page.wait_for_timeout(500)
    return None


def po_cells(page):
    """Texts of all visible cells that are exactly a 10-digit number (the Order # column)."""
    out = []
    for fr in page.frames:
        try:
            loc = fr.get_by_text(PO_RE)
            for i in range(min(loc.count(), 80)):
                el = loc.nth(i)
                if el.is_visible():
                    out.append(el.inner_text().strip())
        except PWError:
            continue
    return out


def only_po_shown(page, po: str, timeout_s: int = 8) -> bool:
    """True once the table shows only rows of this PO (the PO 'in 1 line')."""
    end = time.time() + timeout_s
    while time.time() < end:
        cells = po_cells(page)
        if cells and po in cells and all(c == po for c in cells):
            return True
        page.wait_for_timeout(300)
    return False


def get_val(box) -> str:
    try:
        return (box.input_value(timeout=2_000) or "").strip()
    except PWError:
        try:
            return (box.evaluate("e => (e.value !== undefined ? e.value : e.innerText) || ''") or "").strip()
        except PWError:
            return ""


def focus_box(box) -> bool:
    """Put the cursor in the search box. Tries a normal click, a forced click, then focus()."""
    for how in ("click", "forced click", "focus"):
        try:
            if how == "click":
                box.click(timeout=8_000)
            elif how == "forced click":
                box.click(force=True, timeout=3_000)
            else:
                box.focus(timeout=3_000)
            return True
        except PWError as e:
            print(f"    {how} on the search box failed: {str(e).splitlines()[0][:110]}")
    return False


def put_text(page, box, po: str, n: int) -> str:
    """Get the PO into the box. Returns how it worked ('' = failed). Attempts 1 and 3 paste
    (Ctrl+V); the others type. Falls back to fill() and finally to setting the value by script."""
    def val():
        return get_val(box)
    try:
        page.keyboard.press("Control+A")
        if n in (1, 3):
            page.keyboard.press("Control+V")
            if val() == po:
                return "pasted (Ctrl+V)"
        else:
            page.keyboard.type(po, delay=60)
            if val() == po:
                return "typed"
    except PWError as e:
        print(f"    keyboard failed: {str(e).splitlines()[0][:110]}")
    try:
        box.fill(po, timeout=4_000)
        if val() == po:
            return "filled"
    except PWError as e:
        print(f"    fill failed: {str(e).splitlines()[0][:110]}")
    try:                                                # last resort: set the value by script
        box.evaluate(
            "(el, v) => { const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;"
            " set.call(el, v); el.dispatchEvent(new Event('input', {bubbles: true}));"
            " el.dispatchEvent(new Event('change', {bubbles: true})); }", po)
        if val() == po:
            return "set by script"
    except PWError as e:
        print(f"    script set failed: {str(e).splitlines()[0][:110]}")
    return ""


def blind_search(page, po: str) -> bool:
    """No usable element found: click where the search pill is drawn, paste / type the PO with the
    keyboard, press Enter, and check the table shows only that PO.
    v12: tries several x offsets left of '+ Filter'; if no '+ Filter' is found it uses the
    'Line Items' heading row instead."""
    left, filt = _toolbar_boxes(page)
    filt = [f for f in filt if f["y"] > 120]
    spots = []
    if filt:
        if left:
            filt.sort(key=lambda f: min(abs((f["y"] + f["height"] / 2) - (l["y"] + l["height"] / 2)) for l in left))
        else:
            filt.sort(key=lambda f: f["y"])
        f = filt[0]
        y = f["y"] + f["height"] / 2
        spots = [(f["x"] - dx, y) for dx in (80, 40, 120, 160, 200)]
    else:
        heads = [l for l in left if l["y"] > 120]
        if heads:
            l = max(heads, key=lambda b: b["y"])
            y = l["y"] + l["height"] / 2
            spots = [(l["x"] + l["width"] + dx, y) for dx in (200, 300, 400, 500)]
    if not spots:
        print("  blind search: neither '+ Filter' nor 'Line Items' heading found")
        return False
    for x, y in spots:
        for how in ("paste", "type"):
            try:
                page.mouse.click(x, y)
                page.wait_for_timeout(300)
                page.keyboard.press("Control+A")
                if how == "paste":
                    page.keyboard.press("Control+V")
                else:
                    page.keyboard.type(po, delay=60)
                page.keyboard.press("Enter")
            except PWError as e:
                print(f"    position click/{how} failed: {str(e).splitlines()[0][:100]}")
                continue
            print(f"  clicked at x={round(x)} y={round(y)} and {how}d the PO + Enter")
            s1.wait_until_idle(page, label="after PO search ")
            if only_po_shown(page, po, 8):
                print(f"  PO {po} is shown ({po_cells(page).count(po)} line(s))")
                return True
    return False


def dump_toolbar(page) -> None:
    """Print what the toolbar around '+ Filter' really is, so the search box can be identified."""
    print("DEBUG toolbar around '+ Filter':")
    for fr in page.frames:
        try:
            loc = fr.locator("xpath=//button[contains(normalize-space(.),'Filter')]")
            for i in range(min(loc.count(), 3)):
                el = loc.nth(i)
                if not el.is_visible():
                    continue
                html = el.evaluate("e => { let n = e; for (let k = 0; k < 3 && n.parentElement; k++) n = n.parentElement; return n.outerHTML; }")
                print("  ", html[:1800].replace("\n", " "))
                break
        except PWError:
            continue
    print("DEBUG elements with 'search' in class/id/placeholder/aria-label:")
    for fr in page.frames:
        try:
            loc = fr.locator("[class*='search' i], [id*='search' i], [placeholder*='search' i], [aria-label*='search' i]")
            for i in range(min(loc.count(), 15)):
                el = loc.nth(i)
                b = el.bounding_box() or {}
                print("  ", el.evaluate("e => e.tagName"), "|", (el.get_attribute("class") or "")[:50], "|",
                      el.get_attribute("id"), "| visible", el.is_visible(), "|",
                      round(b.get("x", -1)), round(b.get("y", -1)), round(b.get("width", -1)))
        except PWError:
            continue


def search_po(page, po: str, attempts: int = 4) -> None:
    """4. paste the PO number into the search box; retry until the PO shows alone in the table."""
    print(f"Step 4: putting PO {po} in the Line Items search box")
    s1.wait_until_idle(page, label="before PO search ")
    for n in range(1, attempts + 1):
        print(f"  attempt {n}: looking for the search box ...")
        box = find_search_box(page, timeout_s=8)        # look it up again every time
        if box is None:
            print("  search box element not found -> trying by position (click left of '+ Filter')")
            if blind_search(page, po):
                return
            if n == attempts:
                print("DEBUG grid limit y:", _header_top(page), "| toolbar boxes:", _toolbar_boxes(page))
                describe_inputs(page)
                dump_toolbar(page)
                s3.fail(page, "po_search_not_found", "Line Items search box not found.")
            page.wait_for_timeout(1_000)
            continue
        if not _is_above_grid(page, box):
            describe_inputs(page)
            s3.fail(page, "po_search_in_grid", "Refusing to type: search box candidate is not above the table.")
        try:
            bb = box.bounding_box() or {}
            print(f"  search box found at x={round(bb.get('x', -1))} y={round(bb.get('y', -1))} "
                  f"w={round(bb.get('width', -1))}")
        except PWError:
            pass

        if not focus_box(box):
            print("  could not put the cursor in the search box, retrying")
            page.wait_for_timeout(1_000)
            continue
        how = put_text(page, box, po, n)
        if not how:
            print(f"  attempt {n}: could not get {po} into the box, retrying")
            page.wait_for_timeout(800)
            continue
        try:
            page.keyboard.press("Enter")
        except PWError:
            box.press("Enter")
        print(f"  PO {po} {how} in the search box" + (f" (attempt {n})" if n > 1 else ""))
        page.wait_for_timeout(800)
        s1.wait_until_idle(page, label="after PO search ")

        if only_po_shown(page, po, 8):
            print(f"  PO {po} is shown ({po_cells(page).count(po)} line(s))")
            return
        print(f"  attempt {n}: table does not show only PO {po} yet")
        page.wait_for_timeout(1_000)

    box = find_search_box(page, timeout_s=3)
    try:
        print("DEBUG search box value:", repr(get_val(box)) if box else None)
    except PWError:
        pass
    print("DEBUG visible PO cells:", po_cells(page))
    s3.fail(page, "po_not_in_results", f"PO {po} not shown alone in the table after {attempts} searches.")


def find_po_cell(page, po: str, buyer=None, timeout_s: int = 15):
    """Visible text that is exactly this PO number; with several rows prefer the buyer item's row."""
    pat = re.compile(rf"^\s*{po}\s*$")
    end = time.time() + timeout_s
    while time.time() < end:
        cands = []
        for fr in page.frames:
            try:
                loc = fr.get_by_text(pat)
                for i in range(min(loc.count(), 80)):
                    el = loc.nth(i)
                    if not el.is_visible():
                        continue
                    b = el.bounding_box()
                    if b:
                        cands.append((round(b["y"]), b["x"], el))
            except PWError:
                continue
        if cands:
            cands.sort(key=lambda c: (c[0], c[1]))
            if buyer:
                for _, _, el in cands:
                    try:
                        row = el.locator("xpath=ancestor::*[self::tr or @role='row' or contains(@class,'row')][1]")
                        if buyer in row.inner_text():
                            return el
                    except PWError:
                        pass
            return cands[0][2]
        page.wait_for_timeout(400)
    return None


def click_po(page, po: str, buyer=None) -> bool:
    """5. click the PO by text; True when its Pack Line details ('<PO> / <line> / <item>') open."""
    cell = find_po_cell(page, po, buyer)
    if cell is None:
        return False
    try:                                                # a) click the PO number itself
        cell.click(timeout=5_000)
        if details_open(page, po):
            return True
    except PWError:
        pass
    try:                                                # b) the checkbox in the same row
        row = cell.locator("xpath=ancestor::*[self::tr or @role='row' or contains(@class,'row')][1]")
        row.locator("input[type='checkbox'], [role='checkbox'], [class*='checkbox' i]") \
           .first.click(force=True, timeout=4_000)
        if details_open(page, po):
            return True
    except PWError:
        pass
    try:                                                # c) mouse click left of the PO number
        b = cell.bounding_box()
        if b:
            page.mouse.click(b["x"] - 40, b["y"] + b["height"] / 2)
            if details_open(page, po):
                return True
    except PWError:
        pass
    return False


def open_first_missing_order(page, skip=()):
    """Items 1-5: error badge -> copy PO -> OK -> paste in search box -> click PO by text.
    `skip` = (po, item) pairs to pass over (already filled; the error list may still show them).
    Returns (po, item, buyer) when the PO's details are open, None when nothing is left to open."""
    lines = open_errors(page)                                        # 1. click on error message
    if lines is None:
        print("No error list shown (no errors) - nothing to open.")
        return None
    page.screenshot(path=SHOT_DIR / "after_step5_errors.png", full_page=True)
    found = first_missing_order(lines, skip)                         # 2. copy PO number
    if found is None:
        print("No (further) Order is missing a Commodity Description in the error list.")
        close_errors(page)
        return None
    po, item, buyer = found
    print(f"PO number in the error message: {po}  (Item {item}, {buyer})")
    select_po_in_error_message(page, po)                             # 2. click on message, copy PO
    close_errors(page)                                               # 3. click OK
    search_po(page, po)                                              # 4. paste PO in search box
    if not click_po(page, po, buyer):                                # 5. click PO by text
        s3.fail(page, "po_details_not_open", f"Could not open the details of PO {po} (click on the PO number).")
    print(f"Clicked PO {po} -> details opened")
    s1.wait_until_idle(page, label="after opening PO ")
    return found


# ---------- main -------------------------------------------------------------
def build_parser():
    ap = argparse.ArgumentParser()
    s4.add_common_args(ap)
    ap.add_argument("--invoice", default=os.getenv("INVOICE_TEXT", ""),
                    help="text at the start of Commodity Description (default: --comment)")
    ap.add_argument("--season", default=os.getenv("SEASON", "FW27-SMS"))
    ap.add_argument("--product", default=os.getenv("PRODUCT_DESC", "WOMEN'S T-SHIRT"))
    return ap


def run_to_commodity(page, args):
    """Steps 1-3 of this file on an open session: identification -> Edit Items -> click the first PO
    -> fill its Commodity Description (long text). Step 6 starts from here."""
    invoice = args.invoice or args.comment
    commodity = f"{invoice} DD:{dd_text(args.date)} SEASON:{args.season} {args.product}"
    print("Commodity text:", commodity)

    s4.fill_identification(page, args)
    s4.next_to_edit_items(page)                     # Next -> Edit Items, OK on pop-ups

    # 1) any remaining OK pop-up
    s4.dismiss_ok_popups(page)
    s1.wait_until_idle(page, label="Edit Items ")

    # 2) click the first PO
    po = click_first_po(page)
    if po is None:
        print("DEBUG visible text sample:", _lines(page)[:60])
        s3.fail(page, "po_not_found", "No PO number (10 digits under Order #) found on the page.")
    if po == "":
        s3.fail(page, "po_not_opened", "Clicked the first PO but its details did not open.")
    print(f"Clicked PO {po} -> details opened")
    s1.wait_until_idle(page, label="after selecting PO ")
    s4.dismiss_ok_popups(page)

    # 3) change Commodity Description
    fill_commodity(page, commodity)
    s1.wait_until_idle(page, label="after commodity ")
    s4.dismiss_ok_popups(page)



def run_to_step5(page, args):
    """Steps 1-4 of this file on an open session. Returns what open_first_missing_order returns
    ((po, item, buyer) of the opened PO, or None)."""
    run_to_commodity(page, args)
    # 4) error message -> copy PO -> OK -> paste in search box -> click PO by text
    return open_first_missing_order(page)


def main() -> int:
    print("step5 version 18 (search box found by PO-row limit, any element type, or by position)")
    args = build_parser().parse_args()
    if not s4.args_ok(args):
        return 1

    with sync_playwright() as p:
        browser, context, page = s4.open_session(p, args)
        try:                                            # let the page read/write the clipboard
            context.grant_permissions(["clipboard-read", "clipboard-write"])
        except PWError:
            pass
        run_to_step5(page, args)

        page.screenshot(path=SHOT_DIR / "after_step5.png", full_page=True)
        print("Step 5 done -> screenshots/after_step5.png")
        context.storage_state(path=s1.STATE_FILE)
        if not s1.HEADLESS:
            input("Press Enter to close the browser...")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
