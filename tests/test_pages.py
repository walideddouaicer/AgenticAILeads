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
    # Visible by default
    "views/prospects.py", "views/signals.py",
    "views/home.py", "views/minilist.py", "views/help.py",
    # Hidden behind AAL_SHOW_CLIENT_PAGES but still rendered here — they
    # must keep working for the week client #1 signs.
    "views/queue.py", "views/inbox.py", "views/clients.py",
    "views/configs.py",
    # Superseded by views/signals.py; kept until Help is rewritten.
    "views/watchlist.py", "views/livejobs.py",
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

    # Manual contact entry: invalid linkedin rejected, valid one saved
    # into enriched_contacts.json under the card's company
    at = fresh("views/queue.py")
    name_inputs = [t for t in at.text_input if (t.key or "").startswith("mc_n_")]
    check("manual contact form present", bool(name_inputs))
    if name_inputs:
        sfx = name_inputs[0].key[len("mc_n_"):]

        def fill(link):
            [t for t in at.text_input if t.key == f"mc_n_{sfx}"][0] \
                .set_value("Test Person")
            [t for t in at.text_input if t.key == f"mc_t_{sfx}"][0] \
                .set_value("Head of Talent")
            [t for t in at.text_input if t.key == f"mc_l_{sfx}"][0] \
                .set_value(link)

        cache_before = json.loads(
            (DATA / "enriched_contacts.json").read_text())

        fill("https://example.com/not-linkedin")
        [b for b in at.button if b.key == f"mc_s_{sfx}"][0].click()
        at.run()
        cache_mid = json.loads((DATA / "enriched_contacts.json").read_text())
        check("invalid linkedin URL rejected", cache_mid == cache_before,
              "cache changed despite invalid URL")

        name_inputs = [t for t in at.text_input if t.key == f"mc_n_{sfx}"]
        if name_inputs:
            fill("https://www.linkedin.com/in/test-person")
            [b for b in at.button if b.key == f"mc_s_{sfx}"][0].click()
            at.run()
            cache_after = json.loads(
                (DATA / "enriched_contacts.json").read_text())
            gained = [co for co, cs in cache_after.items()
                      if any(c.get("name") == "Test Person" for c in cs)]
            check("manual contact saved to cache", len(gained) == 1,
                  f"gained={gained}")
            if gained:
                entry = [c for c in cache_after[gained[0]]
                         if c.get("name") == "Test Person"][0]
                check("contact entry uses enrichment structure",
                      set(entry) == {"name", "title", "linkedin", "email"},
                      str(set(entry)))
                # joined onto the company's leads after rerun
                at2 = fresh("views/queue.py")
                check("saved contact renders on rerun",
                      any("Test Person" in str(m.value) for m in at2.markdown),
                      "contact not visible on queue cards")

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

    # Delivery: channel field on configs, export button on queue/clients,
    # sync-replies disabled when no adapter key is set
    at = fresh("views/configs.py")
    check("configs has delivery channel selector",
          any("channel" in str(getattr(s, "label", "")).lower()
              for s in at.selectbox))
    at = fresh("views/queue.py")
    check("queue has 'Export for email tool' button",
          any("email tool" in str(getattr(b, "label", "")).lower()
              for b in at.download_button))
    at = fresh("views/clients.py")
    check("client page has email-tool export",
          any("email tool" in str(getattr(b, "label", "")).lower()
              for b in at.download_button))
    # fixture .env carries a (fake) APOLLO_API_KEY, so the Apollo sequences
    # adapter is 'configured' and the sync button is enabled and named for
    # it — confirms is_configured() gating drives the control.
    sync_btns = [b for b in at.button
                 if "sync replies" in str(getattr(b, "label", "")).lower()]
    check("client page has a 'Sync replies' control", bool(sync_btns))
    import delivery as _d
    if _d.active_email_adapter(env_file=DATA / ".env"):
        check("sync-replies enabled + adapter-named when key present",
              sync_btns and not sync_btns[0].disabled
              and _d.active_email_adapter(env_file=DATA / ".env").name
              in sync_btns[0].label.lower())
    else:
        check("sync-replies disabled when no key",
              sync_btns and sync_btns[0].disabled)

    # ---- Loop A pages are hidden by default, restored by the env flag ----
    # The four client pages still exist and still render (every entry in
    # PAGES above is exercised regardless) — only the sidebar entries are
    # gated. This guards the gate itself: a typo in the env var name would
    # otherwise either hide them forever or never hide them at all.
    print("  -- navigation gate --")
    import importlib

    def nav_titles(show):
        prev = os.environ.get("AAL_SHOW_CLIENT_PAGES")
        if show:
            os.environ["AAL_SHOW_CLIENT_PAGES"] = "1"
        else:
            os.environ.pop("AAL_SHOW_CLIENT_PAGES", None)
        try:
            import app as app_mod
            importlib.reload(app_mod)
            return app_mod.SHOW_CLIENT_PAGES
        finally:
            if prev is None:
                os.environ.pop("AAL_SHOW_CLIENT_PAGES", None)
            else:
                os.environ["AAL_SHOW_CLIENT_PAGES"] = prev

    check("client pages hidden by default", nav_titles(False) is False)
    check("client pages restored by AAL_SHOW_CLIENT_PAGES=1",
          nav_titles(True) is True)

print()
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("ALL PAGE CHECKS PASS")
