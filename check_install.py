"""Run:  python check_install.py   -> tells you if every script is the latest version."""
from pathlib import Path

CHECKS = {
    "step1_login.py":         (["def wait_until_idle", "Re-?Activate"], []),
    "step2_create_booking.py": (["min(loc.count(), 40)"], []),
    "step3_select_plan.py":   (["step3 version 4", "def wait_popup_or_next_page"], []),
    "step4_identification.py": (["step4 version 5", "def next_to_edit_items", "import time\n"], []),
    "step5_edit_items.py":    (["step5 version 10", "def open_first_missing_order", "def search_po", "def click_po"],
                               ["def check_errors", "def click_next", "def fix_missing_commodities"]),
}

ok = True
for name, (must, must_not) in CHECKS.items():
    p = Path(__file__).with_name(name)
    if not p.exists():
        print(f"MISSING  {name}")
        ok = False
        continue
    txt = p.read_text(encoding="utf-8")
    bad = [m for m in must if m not in txt] + [f"(old code still present: {m})" for m in must_not if m in txt]
    if bad:
        ok = False
        print(f"OLD      {name}   <- replace with the file from the latest zip   {bad}")
    else:
        print(f"OK       {name}")

print("\nAll files are up to date." if ok else
      "\nSome files are OLD/missing: unzip the latest zip over this folder (keep your .env),\n"
      "delete the __pycache__ folder, then run this check again.")
