"""Client Configs — profiles, live tester, dry-run, backfill."""

import csv
import json
from datetime import date

import streamlit as st

import common as C
from filter_engine import evaluate_job, DEFAULTS

C.page_header("⚙️ Client Configs", "configs")

profiles = C.list_profiles()
choice = st.selectbox("Profile", profiles + ["➕ New client"],
                      index=0 if profiles else len(profiles),
                      key="cfg_choice",
                      help="Each profile is one JSON file in configs/. "
                           "Pick one to edit, or create a new client.")

if not profiles:
    st.info("**No client profiles yet.** Fill in the form below and click "
            "Save — start with your own prospecting profile (client ID "
            "`default`).")

if choice == "➕ New client":
    cfg = {**{k: (v if not isinstance(v, (list, dict)) else type(v)())
              for k, v in DEFAULTS.items()},
           "client_id": "", "dm_template": C.DEFAULT_TEMPLATE,
           "meeting_rate": C.DEFAULT_MEETING_RATE}
    editing_new = True
else:
    cfg = C.load_profile(choice)
    editing_new = False

col_form, col_test = st.columns([1, 1], gap="large")

with col_form:
    st.subheader("Profile")
    client_id = st.text_input("Client ID (filename, no spaces)",
                              cfg["client_id"],
                              help="Becomes configs/<id>.json and the "
                                   "'client' value on every signal. "
                                   "Lowercase, no spaces.")
    label = st.text_input("Label (human-readable)", cfg.get("label", ""),
                          help="Shown on the client page header, e.g. "
                               "'Nishant — US/India IT staffing'.")
    active = st.toggle("Active (included in nightly sweep)",
                       cfg.get("active", True),
                       help="Paused profiles keep their data but stop "
                            "matching new signals.")

    st.subheader("Gates")
    inclusions = st.text_area(
        "Inclusion keywords — title must contain at least one (comma-separated; empty = all pass)",
        ", ".join(cfg.get("inclusion_keywords", [])), height=80,
        help="The client's niche, e.g. 'java, .net, backend'. Each match "
             "adds 10 points to the lead score.")
    exclusions = st.text_area(
        "Exclusion keywords — title must contain none (comma-separated)",
        ", ".join(cfg.get("exclusion_keywords", [])), height=68,
        help="Hard rejects, e.g. 'intern, unpaid, working student'.")
    locations = st.text_area(
        "Allowed locations — substring match, 'remote' also matches remote titles (comma-separated; empty = anywhere)",
        ", ".join(cfg.get("allowed_locations", [])), height=68,
        help="The client's market, e.g. 'united states, remote'. "
             "Matching is substring-based both directions.")

    st.subheader("Outreach & billing")
    dm_template = st.text_area(
        "DM template — placeholders: {first_name} {hook} {company} {title} {location} {client}",
        cfg.get("dm_template", C.DEFAULT_TEMPLATE), height=110,
        help="First-touch LinkedIn message. {hook} is the signal-specific "
             "opener — never send without one.")
    email_subject = st.text_input(
        "Email subject (same placeholders)",
        cfg.get("email_subject", C.DEFAULT_EMAIL_SUBJECT),
        help="Used by the one-click mailto links in Queue/Inbox.")
    email_template = st.text_area(
        "Email template (same placeholders)",
        cfg.get("email_template", C.DEFAULT_EMAIL_TEMPLATE), height=140,
        help="Body for the one-click mailto links.")
    followup_template = st.text_area(
        f"Follow-up #1 (due {C.FOLLOWUP_DAYS} days after contact, same placeholders)",
        cfg.get("followup_template", C.DEFAULT_FOLLOWUP_TEMPLATE), height=90,
        help="Most replies come from follow-ups — keep it short.")
    followup2_template = st.text_area(
        "Follow-up #2 — breakup (last touch, same placeholders)",
        cfg.get("followup2_template", C.DEFAULT_FOLLOWUP2_TEMPLATE), height=90,
        help="After this one the lead leaves the queue for good.")
    meeting_rate = st.number_input("Rate per won meeting ($)",
                                   value=int(cfg.get("meeting_rate",
                                                     C.DEFAULT_MEETING_RATE)),
                                   step=25,
                                   help="What this client pays per showed "
                                        "meeting. Drives the Billable "
                                        "numbers on Home and client pages.")

    new_cfg = {
        "client_id": client_id.strip(),
        "label": label.strip(),
        "active": bool(active),
        "inclusion_keywords": C.parse_csv_line(inclusions),
        "exclusion_keywords": C.parse_csv_line(exclusions),
        "allowed_locations": C.parse_csv_line(locations),
        # scoring UI removed for now — preserve any existing values silently
        "keyword_weights": cfg.get("keyword_weights", {}),
        "min_score": int(cfg.get("min_score", 0)),
        "dm_template": dm_template,
        "email_subject": email_subject,
        "email_template": email_template,
        "followup_template": followup_template,
        "followup2_template": followup2_template,
        "meeting_rate": int(meeting_rate),
    }

    s1, s2 = st.columns(2)
    with s1:
        if st.button("💾 Save profile", type="primary", width="stretch",
                     help="Writes configs/<client_id>.json. Takes effect on "
                          "the next sweep."):
            if not new_cfg["client_id"] or " " in new_cfg["client_id"]:
                st.error("Client ID is required and must have no spaces.")
            else:
                (C.CONFIG_DIR / f"{new_cfg['client_id']}.json").write_text(
                    json.dumps(new_cfg, indent=2), encoding="utf-8")
                C.push_toast(f"Saved configs/{new_cfg['client_id']}.json", "💾")
                st.rerun()
    with s2:
        if not editing_new:
            with st.popover("🗑 Delete profile", width="stretch"):
                st.warning(f"This permanently deletes "
                           f"`configs/{cfg['client_id']}.json`. The client's "
                           "leads and history stay in signals.csv / "
                           "inbox_status.json.")
                confirm_txt = st.text_input(
                    f"Type **{cfg['client_id']}** to confirm",
                    key=f"del_confirm_{cfg['client_id']}",
                    help="Guard against accidental deletion — the exact "
                         "client ID is required.")
                if st.button("Delete permanently", type="primary",
                             key=f"del_btn_{cfg['client_id']}",
                             disabled=(confirm_txt.strip() != cfg["client_id"]),
                             help="Enabled once the typed ID matches."):
                    (C.CONFIG_DIR / f"{cfg['client_id']}.json").unlink(
                        missing_ok=True)
                    C.push_toast(f"Deleted profile {cfg['client_id']}", "🗑")
                    st.rerun()

with col_test:
    st.subheader("Test before you trust")
    st.markdown("**Quick test — one job**")
    t_title = st.text_input("Job title", "Senior DevOps Engineer",
                            help="Any realistic title — checks the gates on "
                                 "the left in real time.")
    t_loc = st.text_input("Job location", "Remote, Germany",
                          help="Any location string, as messy as real ATS "
                               "data.")
    result = evaluate_job({"title": t_title, "location": t_loc},
                          new_cfg | {"client_id": new_cfg["client_id"] or "draft"})
    if result:
        st.success("PASSES this config")
        st.caption("DM preview with this job:")
        st.code(C.render_dm(new_cfg["dm_template"],
                            {"hook": f"saw you just opened a {t_title} role in {t_loc}",
                             "company": "acme", "title": t_title,
                             "location": t_loc, "client": new_cfg["client_id"]}),
                language=None)
    else:
        st.error("REJECTED by this config")

    st.divider()
    st.markdown("**Dry run — every job currently tracked**")
    jobs = C.all_tracked_jobs()
    if not jobs:
        st.info("**No tracked jobs yet** — the dry run needs "
                "`signals_state.json`. Run the sweep from the Home page "
                "(or `python signal_engine.py`) first.")
        C.render_engine_panel()
        C.engine_button("sweep", key="cfg_run_sweep")
    else:
        hits = []
        for j in jobs:
            r = evaluate_job(j, new_cfg | {"client_id": new_cfg["client_id"] or "draft"})
            if r:
                hits.append({"company": j.get("company", ""),
                             "title": j["title"], "location": j["location"],
                             "url": j.get("url", ""), "platform": j.get("platform", "")})
        st.metric("Jobs this config would catch", f"{len(hits)} / {len(jobs)}",
                  help="How many of ALL currently tracked live jobs pass "
                       "this config's gates.")
        if hits:
            hits.sort(key=lambda x: (x["company"], x["title"]))
            st.dataframe([{k: h[k] for k in ("company", "title", "location")}
                          for h in hits], width="stretch", height=320)

            # ---- backfill: give a new client today's live matches, not just future diffs ----
            cid = new_cfg["client_id"]
            if cid:
                if st.button(f"📥 Backfill these {len(hits)} matches to '{cid}' "
                             "(skips duplicates)", width="stretch",
                             help="Appends today's live matches to signals.csv "
                                  "as leads for this client, so a new client "
                                  "doesn't wait for future diffs. Safe to "
                                  "re-run — duplicates are skipped."):
                    existing = set()
                    if C.SIGNALS_CSV.exists():
                        for r in csv.DictReader(C.SIGNALS_CSV.open(encoding="utf-8")):
                            existing.add((r.get("client", ""), r.get("company", ""),
                                          r.get("title", "")))
                    fieldnames = ["date", "client", "platform", "company", "title",
                                  "location", "score", "url", "hook"]
                    new_file = not C.SIGNALS_CSV.exists()
                    added = 0
                    with C.SIGNALS_CSV.open("a", newline="", encoding="utf-8") as f:
                        w = csv.DictWriter(f, fieldnames=fieldnames)
                        if new_file:
                            w.writeheader()
                        for h in hits:
                            if (cid, h["company"], h["title"]) in existing:
                                continue
                            w.writerow({
                                "date": date.today().isoformat(), "client": cid,
                                "platform": h["platform"], "company": h["company"],
                                "title": h["title"], "location": h["location"],
                                "score": "", "url": h["url"],
                                "hook": f"saw you're currently hiring a {h['title']}"
                                        + (f" in {h['location']}" if h["location"] else ""),
                            })
                            added += 1
                    st.success(f"Backfilled {added} lead(s) to {cid} "
                               f"({len(hits) - added} duplicates skipped). "
                               "Check the Inbox / their client page.")
            else:
                st.caption("Enter a Client ID to enable backfill.")
        if len(hits) == len(jobs) and not new_cfg["inclusion_keywords"]:
            st.warning("Config catches EVERYTHING — add inclusion keywords "
                       "or it's not filtering.")
