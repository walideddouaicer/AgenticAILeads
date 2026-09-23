"""Exercise every app page via AppTest, with empty AND populated data.

Usage:
  python tests/test_pages.py            # orchestrates both runs itself
  (internal: AAL_DATA_DIR=<dir> python tests/test_pages.py --single [--interact])

Data comes from tests/empty_data and tests/fixture_data (built by
make_fixtures.py — run automatically). Real repo data files are never
touched.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent

PAGES = ["views/prospects.py", "views/clients.py",
         "views/signals.py", "views/settings.py"]

# ---------------- orchestrator ----------------

if "--single" not in sys.argv:
    print("building fixtures…")
    r = subprocess.run([sys.executable, str(TESTS / "make_fixtures.py")])
    if r.returncode:
        sys.exit("fixture build failed")
    rc = 0
    for data_dir, extra in [(TESTS / "empty_data", []),
                            (TESTS / "fixture_data", ["--interact"])]:
        print(f"\n=== pages against {data_dir.name} ===")
        env = dict(os.environ, AAL_DATA_DIR=str(data_dir),
                   AAL_DISABLE_AUTH="1")  # bypass login gate for page tests
        r = subprocess.run([sys.executable, __file__, "--single"] + extra,
                           env=env)
        rc = rc or r.returncode
    sys.exit(rc)

# ---------------- single run (AAL_DATA_DIR already set) ----------------

sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
DATA = Path(os.environ["AAL_DATA_DIR"])
INTERACT = "--interact" in sys.argv

from streamlit.testing.v1 import AppTest  # noqa: E402

fails = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        fails.append(f"{name}: {detail}")
        print(f"  FAIL {name}: {detail}")


def fresh(page):
    at = AppTest.from_file(ROOT / "app.py", default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]
    at.switch_page(page)
    at.run()
    return at


for page in PAGES:
    at = fresh(page)
    errs = [e.value for e in at.exception]
    check(f"render {page}", not errs, str(errs))

if not INTERACT:
    at = fresh("views/prospects.py")
    entry = {widget.key: widget for widget in at.text_input}
    entry["add_agency"].set_value("Fixture Recruiting")
    entry["add_person"].set_value("Test Contact")
    entry["add_linkedin"].set_value("https://www.linkedin.com/in/test-contact")
    entry["add_niche"].set_value("Java")
    [button for button in at.button
     if button.key == "prospect_add_person"][0].click()
    at.run()
    check("manual prospect entry renders", not at.exception,
          str([error.value for error in at.exception]))
    with (DATA / "prospects.csv").open(newline="", encoding="utf-8") as stream:
        import csv
        imported = list(csv.DictReader(stream))
    check("manual prospect entry persists without a CLI",
          len(imported) == 1 and imported[0]["agency"] == "Fixture Recruiting")

if INTERACT:
    print("  -- interactions --")

    # Prospects: fixture has 1 follow-up due; contacted persists to the
    # SEPARATE prospects_status.json; undo restores it.
    at = fresh("views/prospects.py")
    fu = [b for b in at.button if (b.key or "").startswith("pfu_")]
    check("prospects show follow-up due card", bool(fu))
    ct = [b for b in at.button if (b.key or "").startswith("pct_")]
    check("prospects show new-prospect cards", bool(ct))
    if ct:
        lead_before = (DATA / "inbox_status.json").read_text()
        before = json.loads((DATA / "prospects_status.json").read_text())
        ct[0].click()
        at.run()
        after = json.loads((DATA / "prospects_status.json").read_text())
        gained = [k for k in after
                  if after[k].get("status") == "contacted"
                  and before.get(k, {}).get("status") != "contacted"]
        check("prospect contact persisted separately", len(gained) == 1,
              f"gained={gained}")
        check("lead status file untouched by prospect action",
              (DATA / "inbox_status.json").read_text() == lead_before)
        undo = [b for b in at.button if b.key == "p_undo"]
        check("prospect undo available", bool(undo))
        if undo:
            undo[0].click()
            at.run()
            rev = json.loads((DATA / "prospects_status.json").read_text())
            check("prospect undo restored", rev == before,
                  "status file differs after undo")
    print("  -- four-screen navigation --")
    nav = AppTest.from_file(ROOT / "app.py", default_timeout=30).run()
    for page in PAGES[:4]:
        try:
            nav.switch_page(page)
            nav.run()
            available = not nav.exception
        except ValueError:
            available = False
        check(f"navigation includes {page}", available)
    try:
        nav.switch_page("views/home.py")
    except ValueError:
        old_home_visible = False
    else:
        old_home_visible = True
    check("superseded Home is absent from navigation", not old_home_visible)

print()
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("ALL PAGE CHECKS PASS")
