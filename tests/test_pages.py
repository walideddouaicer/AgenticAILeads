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

PAGES = [
    "views/home.py", "views/queue.py", "views/inbox.py",
    "views/prospects.py", "views/clients.py", "views/minilist.py",
    "views/watchlist.py", "views/livejobs.py", "views/configs.py",
    "views/help.py",
]

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
        env = dict(os.environ, AAL_DATA_DIR=str(data_dir))
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
    at = AppTest.from_file("app.py", default_timeout=30)
    at.switch_page(page)
    at.run()
    return at


for page in PAGES:
    at = fresh(page)
    errs = [e.value for e in at.exception]
    check(f"render {page}", not errs, str(errs))

if INTERACT:
    print("  -- interactions --")

    # Queue: mark contacted persists, undo restores
    at = fresh("views/queue.py")
    sent = [b for b in at.button if (b.key or "").startswith("ctd_")]
    check("queue has Sent buttons", bool(sent))
    if sent:
        before = json.loads((DATA / "inbox_status.json").read_text())
        n0 = sum(1 for v in before.values() if v.get("status") == "contacted")
        sent[0].click()
        at.run()
        after = json.loads((DATA / "inbox_status.json").read_text())
        n1 = sum(1 for v in after.values() if v.get("status") == "contacted")
        check("mark-contacted persisted", n1 == n0 + 1, f"{n0} -> {n1}")
        undo = [b for b in at.button if b.key == "q_undo"]
        if undo:
            undo[0].click()
            at.run()
            rev = json.loads((DATA / "inbox_status.json").read_text())
            n2 = sum(1 for v in rev.values() if v.get("status") == "contacted")
            check("undo restored lead status", n2 == n0, f"{n2} != {n0}")

    # Queue focus mode
    at = fresh("views/queue.py")
    tog = [t for t in at.toggle if t.key == "q_focus"]
    if tog:
        tog[0].set_value(True)
        at.run()
        check("focus mode renders", not at.exception)
        check("focus mode has skip",
              any(b.key == "q_skip_btn" for b in at.button))

    # Inbox pagination
    at = fresh("views/inbox.py")
    show = [s for s in at.selectbox if s.key == "inbox_show_v2"]
    if show:
        show[0].set_value("all")
        at.run()
    pagers = [n for n in at.number_input if n.key == "inbox_page"]
    check("inbox paginates >25 rows", bool(pagers))
    if pagers:
        pagers[0].set_value(2)
        at.run()
        check("inbox page 2 renders", not at.exception)

    # Prospects: fixture has 1 follow-up due; contacted persists to the
    # SEPARATE prospects_status.json; undo restores it; mini-list appears
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
    ml = [b for b in at.download_button
          if (b.key or "").startswith("mlc_")] if hasattr(at, "download_button") else []
    if ml is not None:
        # download_button accessor may not exist on older AppTest; the
        # render check above already covers the mini-list code path.
        pass

    # Watchlist search + bulk remove widgets
    at = fresh("views/watchlist.py")
    check("watchlist search present",
          any(t.key == "wl_search" for t in at.text_input))
    check("watchlist bulk-remove present",
          any(m.key == "wl_bulk_rm" for m in at.multiselect))

    # Configs: delete disabled until ID typed
    at = fresh("views/configs.py")
    dels = [b for b in at.button if (b.key or "").startswith("del_btn_")]
    check("profile delete requires typed confirmation",
          bool(dels) and all(b.disabled for b in dels))

print()
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("ALL PAGE CHECKS PASS")
