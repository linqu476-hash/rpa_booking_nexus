"""
RPA Booking SMS - Step 6: fill  UNISEX T-SHIRT  for every PO that is missing a Commodity Description
  (runs steps 1-3 of step 5 first: identification -> Edit Items -> first PO filled)

  A. click the error message (red badge next to "New") ONCE and read every
     "Order <PO> Item <n> , <buyer item>: Commodity Description is missing" message
     -> keep the PO numbers in a list, click OK
  B. for each PO in the list:
        paste the PO in the Line Items search box -> click the PO -> Commodity Description = UNISEX T-SHIRT
  C. open the error message again; if new "Commodity Description is missing" POs (not filled yet)
     show up, do B for them (max 3 rounds), then finish

Run:
  python step6_fill_loop.py 609537759987 --comment "Inv number" --date 2026-10-19
  optional: --loop-commodity "UNISEX T-SHIRT"  --max-orders 300
"""
import os
import sys

from playwright.sync_api import sync_playwright, Error as PWError

import step1_login as s1
import step3_select_plan as s3
import step4_identification as s4
import step5_edit_items as s5

SHOT_DIR = s1.SHOT_DIR
MAX_ROUNDS = 3
MAX_IN_A_ROW_FAILED = 3


def read_missing_orders(page, done):
    """A. open the error list, return [(po, item, buyer), ...] of every 'Commodity Description is
    missing' message that is not in `done`, then click OK. None = no error list at all."""
    lines = s5.open_errors(page)
    if lines is None:
        return None
    page.screenshot(path=SHOT_DIR / "step6_error_list.png", full_page=True)
    found, seen = [], set(done)
    for m in s5.MISSING_RE.finditer(" ".join(lines)):
        key = (m.group(1), m.group(2))
        if key in seen:
            continue
        seen.add(key)
        found.append((m.group(1), m.group(2), m.group(3)))
    s5.close_errors(page)
    return found


def fill_one(page, po, item, buyer, text):
    """B. search the PO, click it, fill the commodity. Raises RuntimeError on any failure."""
    s5.search_po(page, po)
    if not s5.click_po(page, po, buyer):
        s3.fail(page, f"po_not_opened_{po}", f"Could not open the details of PO {po} item {item}.")
    s1.wait_until_idle(page, label="after opening PO ")
    if not s5.fill_commodity(page, text):
        s3.fail(page, f"commodity_not_set_{po}", f"PO {po} item {item}: Commodity Description is not {text!r}.")
    s1.wait_until_idle(page, label="after commodity ")
    s4.dismiss_ok_popups(page)


def fix_all(page, text, max_orders):
    done, failed = set(), []
    for rnd in range(1, MAX_ROUNDS + 1):
        todo = read_missing_orders(page, done)
        if todo is None:
            print("No error list shown -> no errors left.")
            break
        if not todo:
            print("No PO missing a Commodity Description is left in the error message.")
            break
        todo = todo[: max(0, max_orders - len(done))]
        print(f"Round {rnd}: {len(todo)} PO(s) missing a Commodity Description:")
        for po, item, buyer in todo:
            print(f"   {po}  item {item}  {buyer}")
        in_a_row = 0
        for i, (po, item, buyer) in enumerate(todo, 1):
            print(f"[{len(done) + 1}] ({i}/{len(todo)}) PO {po} item {item}")
            try:
                fill_one(page, po, item, buyer, text)
                done.add((po, item))
                in_a_row = 0
            except RuntimeError as e:
                print("  FAILED:", e)
                failed.append(f"{po}/{item}")
                done.add((po, item))                 # do not retry it in the next round
                in_a_row += 1
                s4.dismiss_ok_popups(page)
                if in_a_row >= MAX_IN_A_ROW_FAILED:
                    print(f"Stopped: {MAX_IN_A_ROW_FAILED} failures in a row.")
                    return done, failed
        if len(done) >= max_orders:
            print(f"Stopped: reached --max-orders {max_orders}.")
            break
    return done, failed


def main() -> int:
    print("step6 version 3 (read the PO list from the error message once, fill each, re-check)")
    ap = s5.build_parser()
    ap.add_argument("--loop-commodity", default=os.getenv("LOOP_COMMODITY", "UNISEX T-SHIRT"),
                    help="Commodity Description for every PO found in the error list")
    ap.add_argument("--max-orders", type=int, default=int(os.getenv("MAX_ORDERS", "300")),
                    help="safety limit")
    args = ap.parse_args()
    if not s4.args_ok(args):
        return 1

    code = 0
    with sync_playwright() as p:
        browser, context, page = s4.open_session(p, args)
        try:                                            # let the page read/write the clipboard
            context.grant_permissions(["clipboard-read", "clipboard-write"])
        except PWError:
            pass

        s5.run_to_commodity(page, args)                 # identification -> Edit Items -> first PO
        done, failed = fix_all(page, args.loop_commodity, args.max_orders)

        ok = [f"{po}/{item}" for po, item in sorted(done) if f"{po}/{item}" not in failed]
        print(f"Filled {len(ok)} PO(s) with {args.loop_commodity!r}: {', '.join(ok)}")
        if failed:
            print(f"FAILED {len(failed)} PO(s): {', '.join(failed)}")
            code = 1
        (SHOT_DIR / "step6_result.txt").write_text(
            "filled: " + ", ".join(ok) + "\nfailed: " + ", ".join(failed) + "\n", encoding="utf-8")

        page.screenshot(path=SHOT_DIR / "after_step6.png", full_page=True)
        print("Step 6 done -> screenshots/after_step6.png")
        context.storage_state(path=s1.STATE_FILE)
        if not s1.HEADLESS:
            input("Press Enter to close the browser...")
        browser.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
