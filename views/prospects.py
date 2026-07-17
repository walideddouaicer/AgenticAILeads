"""Prospects — OUR pipeline: recruitment agencies we want as clients.

Mirrors the Outreach Queue mechanics, but for our own outreach:
prospects.csv (prospector_engine) joined with prospect_signals.csv
(mandate sweep) for hooks, status in prospects_status.json (separate
from the lead pipeline), and a one-click mini-list per prospect as the
door-opener.
"""

from datetime import date, datetime

import streamlit as st

import common as C
from minilist_engine import build_minilist, render_csv, render_html, DEFAULT_PITCH

C.page_header("🧲 Prospects", "prospects")

prospects = C.load_prospects()
signals = C.load_prospect_signals()
status = C.load_status(C.PROSPECTS_STATUS)
pcfg = C.load_prospects_config()

# ---------------- engines (always available, incl. empty state) ----------------

C.render_engine_panel()
with st.expander("⚙️ Run prospecting engines",
                 expanded=not prospects):
    e1, e2, e3 = st.columns(3)
    with e1:
        pr_limit = int(st.number_input(
            "Prospector API-call cap", 1, 100, 20, key="pr_limit",
            help="Hard cap on Apollo API calls per prospector run "
                 "(free tier: 600/day — stay far under)."))
        C.engine_button("prospector", key="pr_run_prospector",
                        extra_args=["--limit", pr_limit],
                        help_text="Searches Apollo for founders/MDs/owners "
                                  "at small recruitment agencies and appends "
                                  "new ones to prospects.csv. Run "
                                  "`python prospector_engine.py --probe` in "
                                  "a terminal FIRST to verify the filters "
                                  "your Apollo plan accepts.")
        st.caption("Find agency decision-makers (Apollo)")
    with e2:
        C.engine_button("agency_discover", key="pr_run_agency_disc",
                        extra_args=["--from-prospects"],
                        help_text="Probes your prospects' agency names "
                                  "across all 4 ATS platforms and adds "
                                  "boards classified as agencies to "
                                  "agency_watchlist.json.")
        st.caption("Find their job boards (mandate source)")
    with e3:
        C.engine_button("prospect_sweep", key="pr_run_sweep",
                        help_text="Sweeps agency boards; each new posting "
                                  "becomes a mandate hook. First sweep of a "
                                  "board baselines silently.")
        st.caption("Sweep mandates → fresh hooks")

if not prospects:
    st.info("**No prospects yet.** This page fills from `prospects.csv`.")
    st.markdown(
        "1. In a terminal, run `python prospector_engine.py --probe` once "
        "and sanity-check the response (right filters, right countries)\n"
        "2. Then click **Run Prospector** above (or run "
        "`python prospector_engine.py`)\n"
        "3. Optional but powerful: **Agency board discovery** → **Mandate "
        "sweep** gives every prospect a fresh, specific hook\n\n"
        "You can also add rows to `prospects.csv` by hand — columns: "
        "`date_found, agency, person_name, person_title, linkedin, email, "
        "location, size, source, niche_keywords`.")
    st.stop()

# ---------------- per-prospect derived data ----------------

by_key = {}
for p in prospects:
    k = C.prospect_key(p)
    by_key[k] = p   # duplicate rows (same person) collapse to the last one

mandates = {k: C.mandates_for_agency(p.get("agency", ""), signals)
            for k, p in by_key.items()}


def niche_for(k, p):
    """Priority: per-prospect override (status file) > CSV column > derived."""
    entry = status.get(k) or {}
    if (entry.get("niche") or "").strip():
        return C.parse_csv_line(entry["niche"]), "override"
    if (p.get("niche_keywords") or "").strip():
        return C.parse_csv_line(p["niche_keywords"]), "prospects.csv"
    derived = C.derive_niche_keywords(
        [m.get("mandate_title", "") for m in mandates[k]])
    return derived, "derived from mandates" if derived else "none"


def hook_for(k, p):
    """Latest mandate hook (age-degraded like lead hooks) → niche fallback."""
    ms = mandates[k]
    if ms:
        m = ms[0]
        try:
            age = (date.today()
                   - datetime.strptime(m.get("date", ""), "%Y-%m-%d").date()).days
        except ValueError:
            age = None
        if age is not None and age > C.FRESH_DAYS:
            loc = m.get("location", "")
            return (f"saw you've been working a {m.get('mandate_title', '')} "
                    "mandate" + (f" in {loc}" if loc else "")), "mandate"
        return m.get("hook", ""), "mandate"
    kws, src = niche_for(k, p)
    if kws:
        return f"saw you specialize in {' / '.join(kws[:3])} placements", "niche"
    return "", "none"


# ---------------- metrics ----------------

today_iso = date.today().isoformat()
stage_of = {k: C.effective_status(status.get(k)) for k in by_key}
n_due = sum(1 for k in by_key if C.followup_due(status.get(k)))
touched = sum(1 for e in status.values()
              if e.get("on") == today_iso
              and e.get("status") in ("contacted", "replied", "call_booked",
                                      "client_won"))

m = st.columns(7)
for col, stage in zip(m, ["new"] + C.PROSPECT_STAGES + ["dismissed"]):
    col.metric(stage.replace("_", " "),
               sum(1 for s in stage_of.values() if s == stage),
               help=f"Prospects currently in the '{stage}' stage.")
m[6].metric("follow-ups due", n_due,
            help=f"Contacted ≥{C.FOLLOWUP_DAYS} days ago, fewer than "
                 f"{C.MAX_FOLLOWUPS} follow-ups sent.")
st.caption(f"{len(by_key)} prospect(s) · {touched} touched today · "
           f"{len(signals)} mandate signal(s) on file")

# ---------------- templates editor ----------------

with st.expander("✍️ Message templates (prospects_config.json)"):
    st.caption("Placeholders: {first_name} {agency} {hook} {person_title} "
               "{location}. The offer is the PLAYBOOK one: pay per showed "
               "meeting, first free.")
    t1 = st.text_area("First touch", pcfg["dm_template"], height=110,
                      key="pt_dm", help="Used for prospects in 'new'.")
    t2 = st.text_area(f"Follow-up #1 (due {C.FOLLOWUP_DAYS} days after "
                      "contact)", pcfg["followup_template"], height=100,
                      key="pt_fu",
                      help="Most replies come from follow-ups.")
    t3 = st.text_area("Follow-up #2 — breakup (last touch)",
                      pcfg["followup2_template"], height=90, key="pt_fu2",
                      help="After this one the prospect leaves the queue.")
    if st.button("💾 Save templates", key="pt_save", type="primary",
                 help="Writes prospects_config.json — takes effect "
                      "immediately."):
        C.save_prospects_config({"dm_template": t1, "followup_template": t2,
                                 "followup2_template": t3})
        C.push_toast("Prospect templates saved", "💾")
        st.rerun()

# ---------------- undo ----------------

u = C.undo_available()
if u and st.button(f"↩ Undo: {u['label']}", key="p_undo",
                   help="Restores the previous stage/notes of the last "
                        "prospect (or lead) you changed."):
    label = C.undo_last()
    C.push_toast(f"Undone: {label}", "↩")
    st.rerun()


# ---------------- cards ----------------

def prospect_card(k, p, kind, i):
    """kind: 'fu' | 'new' | 'play'."""
    entry = status.get(k)
    eff = C.effective_status(entry)
    due = C.followup_due(entry)
    agency = p.get("agency", "?")
    person = p.get("person_name", "?")
    label = f"{person} @ {agency}"
    hook, hook_src = hook_for(k, p)

    icon = {"fu": "🔁 ", "new": "🆕 ", "play": "💬 "}[kind]
    head = (f"{icon}{person} — {p.get('person_title', '?')}  [{agency}]"
            + ("  · 🎣 mandate hook" if hook_src == "mandate" else ""))
    with st.expander(head, expanded=(i == 0 and kind != "play")):
        st.markdown(C.stage_badge(eff, due), unsafe_allow_html=True)
        meta = " · ".join(x for x in [
            p.get("location", ""),
            f"{p.get('size', '')} employees" if p.get("size") else "",
            f"found {p.get('date_found', '')}"] if x)
        if meta:
            st.caption(meta)
        lk = (p.get("linkedin") or "").strip()
        if lk:
            st.markdown(f"[open LinkedIn ↗]({lk})")
        email = p.get("email", "")
        if "@" in email and "locked" not in email.lower():
            st.caption(f"✉️ {email}")

        # mandates & hook
        ms = mandates[k]
        if ms:
            st.caption(f"Latest mandate ({ms[0].get('date', '')}): "
                       f"**{ms[0].get('mandate_title', '')}** "
                       f"{ms[0].get('location', '')} · "
                       f"{len(ms)} mandate signal(s) on file")
        if not hook:
            st.warning("No hook yet — run **Agency board discovery** + "
                       "**Mandate sweep** above, or set niche keywords "
                       "below. PLAYBOOK rule: no hook = don't send.")

        # message draft
        if kind == "fu":
            n_fu = int((entry or {}).get("fu", 0) or 0)
            tmpl = (pcfg["followup2_template"] if n_fu >= 1
                    else pcfg["followup_template"])
            st.caption(f"Follow-up #{n_fu + 1}"
                       + (" (breakup)" if n_fu >= 1 else "")
                       + f" — last touch {(entry or {}).get('on', '?')}")
        else:
            tmpl = pcfg["dm_template"]
        if kind != "play":
            st.code(C.render_prospect_dm(tmpl, p, hook), language=None)

        # niche keywords + one-click mini-list (the door-opener)
        kws, kw_src = niche_for(k, p)
        nc1, nc2 = st.columns([3, 1], vertical_alignment="bottom")
        with nc1:
            niche_in = st.text_input(
                "Niche keywords (comma-separated)", ", ".join(kws),
                key=f"nk_{k}",
                help="What THIS agency places — drives their mini-list. "
                     f"Currently: {kw_src}. Edit + Save to override "
                     "(persisted per prospect).")
        with nc2:
            if st.button("Save", key=f"nk_save_{k}",
                         help="Persists the override in "
                              "prospects_status.json."):
                e = status.get(k, {"status": "new", "on": "", "notes": ""})
                e["niche"] = niche_in
                status[k] = e
                C.save_status(status, C.PROSPECTS_STATUS)
                C.push_toast(f"Niche saved for {agency}", "🏷")
                st.rerun()

        if kws:
            ml_rows = build_minilist(kws, n=10, state_file=C.STATE_FILE,
                                     contacts_file=C.CONTACTS_CACHE)
            if ml_rows:
                n_contact = sum(1 for r in ml_rows if r["contact_name"])
                st.caption(f"🎁 Mini-list ready: **{len(ml_rows)} companies** "
                           f"hiring {', '.join(kws[:3])} right now "
                           f"({n_contact} with contacts) — the door-opener.")
                d1, d2 = st.columns(2)
                slug = "".join(c if c.isalnum() else "-"
                               for c in agency.lower()).strip("-") or "prospect"
                fname = f"minilist_{slug}_{date.today().isoformat()}"
                with d1:
                    st.download_button(
                        "⬇ Mini-list CSV", render_csv(ml_rows),
                        file_name=f"{fname}.csv", mime="text/csv",
                        key=f"mlc_{k}", width="stretch",
                        help="Raw rows — paste the top ones into a DM.")
                with d2:
                    st.download_button(
                        "⬇ Branded one-pager (HTML)",
                        render_html(agency, ml_rows, DEFAULT_PITCH),
                        file_name=f"{fname}.html", mime="text/html",
                        key=f"mlh_{k}", width="stretch",
                        help="Open → Ctrl+P → PDF, attach to the DM: "
                             "'10 companies hiring exactly what you place "
                             "— want 50/week?'")
            else:
                st.caption("No tracked jobs match these niche keywords — "
                           "widen them or grow the client watchlist.")

        # notes (in-play prospects are live conversations)
        if kind == "play":
            notes = st.text_area("Notes", (entry or {}).get("notes", ""),
                                 height=70, key=f"pn_{k}",
                                 placeholder="reply content, call time, "
                                             "objections…",
                                 help="Saved with the prospect.")
        else:
            notes = None

        # actions
        b1, b2 = st.columns(2)
        if kind == "fu":
            with b1:
                if st.button("🔁 Followed up", key=f"pfu_{k}",
                             type="primary", width="stretch",
                             help="Logs the follow-up and resets the "
                                  f"{C.FOLLOWUP_DAYS}-day timer."):
                    C.mark_followed_up(status, k, label,
                                       path=C.PROSPECTS_STATUS)
                    st.rerun()
            with b2:
                if st.button("💬 They replied", key=f"prp_{k}",
                             width="stretch",
                             help="Moves the prospect to 'replied' — "
                                  "answer within 2 hours."):
                    C.set_stage(status, k, "replied", lead_label=label,
                                path=C.PROSPECTS_STATUS)
                    st.rerun()
        elif kind == "new":
            with b1:
                if st.button("✅ Sent — mark contacted", key=f"pct_{k}",
                             type="primary", width="stretch",
                             help="Click after you actually sent the DM — "
                                  "starts the follow-up timer."):
                    C.set_stage(status, k, "contacted", lead_label=label,
                                path=C.PROSPECTS_STATUS)
                    st.rerun()
            with b2:
                if st.button("🚫 Dismiss", key=f"pdx_{k}", width="stretch",
                             help="Bad fit / wrong person — out of the "
                                  "queue. Undo available after."):
                    C.set_stage(status, k, "dismissed", lead_label=label,
                                path=C.PROSPECTS_STATUS)
                    st.rerun()
        else:  # play: replied -> call_booked -> client_won
            with b1:
                if eff == "replied" and st.button(
                        "📅 Call booked", key=f"pcb_{k}", type="primary",
                        width="stretch",
                        help="The discovery call is on the calendar."):
                    C.set_stage(status, k, "call_booked", notes,
                                lead_label=label, path=C.PROSPECTS_STATUS)
                    st.rerun()
                if eff == "call_booked" and st.button(
                        "🎉 Client won", key=f"pcw_{k}", type="primary",
                        width="stretch",
                        help="They're a client — set up their profile in "
                             "System → Client Configs!"):
                    C.set_stage(status, k, "client_won", notes,
                                lead_label=label, path=C.PROSPECTS_STATUS)
                    st.rerun()
            with b2:
                if st.button("🚫 Dismiss", key=f"pdx2_{k}", width="stretch",
                             help="Conversation went nowhere — close it "
                                  "out."):
                    C.set_stage(status, k, "dismissed", notes,
                                lead_label=label, path=C.PROSPECTS_STATUS)
                    st.rerun()


due_list = [(k, p) for k, p in by_key.items()
            if C.followup_due(status.get(k))]
new_list = [(k, p) for k, p in by_key.items() if stage_of[k] == "new"]
# best first: has a mandate hook > has any mandates > recency
new_list.sort(key=lambda kp: (hook_for(kp[0], kp[1])[1] == "mandate",
                              bool(mandates[kp[0]]),
                              kp[1].get("date_found", "")), reverse=True)
play_list = [(k, p) for k, p in by_key.items()
             if stage_of[k] in ("replied", "call_booked")]

st.markdown(f"#### 🔁 Follow-ups due ({len(due_list)}) — do these first")
if due_list:
    for i, (k, p) in enumerate(due_list):
        prospect_card(k, p, "fu", i)
else:
    st.caption("None due.")

st.markdown(f"#### 🆕 New prospects ({len(new_list)})")
if new_list:
    for i, (k, p) in enumerate(new_list):
        prospect_card(k, p, "new", i)
else:
    st.caption("No new prospects — run the Prospector above.")

st.markdown(f"#### 💬 In play — replied / call booked ({len(play_list)})")
if play_list:
    for i, (k, p) in enumerate(play_list):
        prospect_card(k, p, "play", i)
else:
    st.caption("No live conversations right now.")

# ---------------- full table ----------------

with st.expander("📋 All prospects (table)"):
    rows = []
    for k, p in by_key.items():
        entry = status.get(k) or {}
        rows.append({
            "agency": p.get("agency", ""),
            "person": p.get("person_name", ""),
            "title": p.get("person_title", ""),
            "stage": stage_of[k].replace("_", " ")
                     + (" · follow-up due" if C.followup_due(entry) else ""),
            "since": entry.get("on", ""),
            "mandates": len(mandates[k]),
            "location": p.get("location", ""),
            "linkedin": p.get("linkedin", ""),
            "notes": entry.get("notes", ""),
        })
    st.dataframe(rows, width="stretch", height=340,
                 column_config={
                     "linkedin": st.column_config.LinkColumn("linkedin")})
