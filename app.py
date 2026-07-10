#!/usr/bin/env python3
"""
Agentic AI Leads — Local CRM
Tab 1: Daily Inbox    (signals -> DMs -> stages: contacted/replied/won, notes,
                       dismiss, follow-up timers)
Tab 2: Clients        (per-client page: all their leads, filters, metrics, export)
Tab 3: Client Configs (profiles, live tester, dry-run)

Run:  streamlit run app.py
"""

import csv
import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import streamlit as st

from filter_engine import evaluate_job, DEFAULTS

HERE = Path(__file__).resolve().parent
CONFIG_DIR = HERE / "configs"
SIGNALS_CSV = HERE / "signals.csv"
ENRICHED_CSV = HERE / "enriched_signals.csv"
STATE_FILE = HERE / "signals_state.json"
INBOX_STATUS = HERE / "inbox_status.json"

DEFAULT_TEMPLATE = (
    "Hi {first_name} — {hook}. Roles like that are exactly what we place. "
    "Worth a quick chat about how we can help you fill them faster?"
)
FOLLOWUP_DAYS = 3
FRESH_DAYS = 7      # signals older than this switch to "currently hiring" wording
DEFAULT_MEETING_RATE = 150

STAGES = ["contacted", "replied", "won"]
STAGE_ICON = {"new": "", "contacted": "✅ ", "replied": "💬 ",
              "won": "🏆 ", "dismissed": "🚫 "}
# legacy stage names from earlier versions -> current
LEGACY_MAP = {"call booked": "replied", "showed": "won", "snoozed": "new"}

st.set_page_config(page_title="Agentic AI Leads — CRM", layout="wide")
CONFIG_DIR.mkdir(exist_ok=True)


# ============================ helpers ============================

def list_profiles():
    return sorted(p.stem for p in CONFIG_DIR.glob("*.json"))


def load_profile(name):
    try:
        cfg = json.loads((CONFIG_DIR / f"{name}.json").read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    cfg.setdefault("client_id", name)
    cfg.setdefault("dm_template", DEFAULT_TEMPLATE)
    cfg.setdefault("meeting_rate", DEFAULT_MEETING_RATE)
    for k, v in DEFAULTS.items():
        cfg.setdefault(k, v if not isinstance(v, (list, dict)) else type(v)())
    return cfg


def parse_csv_line(text):
    return [x.strip() for x in text.split(",") if x.strip()]


def parse_weights(text):
    out = {}
    for line in text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            try:
                out[k.strip()] = int(v.strip())
            except ValueError:
                continue
    return out


def weights_to_text(w):
    return "\n".join(f"{k}: {v}" for k, v in (w or {}).items())


def all_tracked_jobs():
    if not STATE_FILE.exists():
        return []
    state = json.loads(STATE_FILE.read_text())
    jobs = []
    for board_key, board_jobs in state.items():
        platform, _, company = board_key.partition(":")
        for j in board_jobs.values():
            jobs.append({**j, "company": company, "platform": platform})
    return jobs


def lead_key(row):
    raw = "|".join(row.get(k, "") or "" for k in ["date", "client", "company", "title"])
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def load_leads():
    src = ENRICHED_CSV if ENRICHED_CSV.exists() else SIGNALS_CSV
    if not src.exists():
        return [], src.name
    rows = list(csv.DictReader(src.open(encoding="utf-8")))
    for r in rows:
        if not r.get("client"):
            r["client"] = "default"
    return rows, src.name


def load_status():
    """{lead_key: {'status': str, 'on': iso, 'notes': str}} — migrates all
    earlier formats (plain string, legacy stage names, snooze fields)."""
    if not INBOX_STATUS.exists():
        return {}
    raw = json.loads(INBOX_STATUS.read_text())
    out = {}
    for k, v in raw.items():
        if isinstance(v, str):
            v = {"status": v, "on": ""}
        v.pop("until", None)
        v.setdefault("notes", "")
        s = v.get("status", "new")
        v["status"] = LEGACY_MAP.get(s, s)
        out[k] = v
    return out


def save_status(status):
    INBOX_STATUS.write_text(json.dumps(status, indent=1))


def effective_status(entry) -> str:
    if not entry:
        return "new"
    return entry.get("status", "new")


def followup_due(entry) -> bool:
    if not entry or entry.get("status") != "contacted":
        return False
    on = entry.get("on") or ""
    try:
        return (date.today() - datetime.strptime(on, "%Y-%m-%d").date()).days >= FOLLOWUP_DAYS
    except ValueError:
        return False


def all_clients(leads):
    return sorted(set(list_profiles()) | {r.get("client", "default") for r in leads})


def display_hook(row):
    """Hook wording follows the signal's age: fresh (<= FRESH_DAYS) keeps the
    stored 'just opened' hook; older signals degrade to 'currently hiring'
    so a DM never claims recency the posting doesn't have."""
    d = row.get("date") or ""
    try:
        age = (date.today() - datetime.strptime(d, "%Y-%m-%d").date()).days
    except ValueError:
        age = None
    if age is not None and age > FRESH_DAYS:
        loc = row.get("location", "")
        return (f"saw you're currently hiring a {row.get('title', '')}"
                + (f" in {loc}" if loc else ""))
    return row.get("hook", "")


def render_dm(template, row):
    contact = row.get("contact1_name", "")
    first = contact.split()[0] if contact else "there"
    fields = {
        "first_name": first,
        "hook": display_hook(row),
        "company": row.get("company", ""),
        "title": row.get("title", ""),
        "location": row.get("location", ""),
        "client": row.get("client", ""),
    }
    try:
        return template.format(**fields)
    except (KeyError, IndexError, ValueError):
        return template


def set_stage(status, key, stage, notes=None):
    entry = status.get(key, {"status": "new", "on": "", "notes": ""})
    entry["status"] = stage
    entry["on"] = date.today().isoformat()
    if notes is not None:
        entry["notes"] = notes
    status[key] = entry
    save_status(status)


# ================================ UI ====================================

tab_inbox, tab_clients, tab_configs = st.tabs(
    ["📥 Daily Inbox", "👤 Clients", "⚙️ Client Configs"])

# ---------------------------- TAB: Inbox --------------------------------

with tab_inbox:
    leads, src_name = load_leads()
    status = load_status()

    if not leads:
        st.info("No signals yet — run signal_engine.py, then refresh.")
    else:
        c1, c2, c3 = st.columns([1, 1, 2])
        with c1:
            f_client = st.selectbox("Client", ["all"] + all_clients(leads),
                                    key="inbox_client")
        with c2:
            f_status = st.selectbox(
                "Show",
                ["new", "follow-up due", "contacted", "replied", "won",
                 "dismissed", "all"],
                key="inbox_show_v2")
        with c3:
            n_due = sum(1 for r in leads if followup_due(status.get(lead_key(r))))
            n_new = sum(1 for r in leads
                        if effective_status(status.get(lead_key(r))) == "new")
            st.caption(f"Source: {src_name} — {len(leads)} signals — "
                       f"**{n_new} new · {n_due} follow-up(s) due**")

        shown = 0
        for row in reversed(leads):
            key = lead_key(row)
            entry = status.get(key)
            eff = effective_status(entry)
            due = followup_due(entry)

            if f_client != "all" and row.get("client") != f_client:
                continue
            keep = (
                (f_status == "all") or
                (f_status == "new" and eff == "new") or
                (f_status == "follow-up due" and due) or
                (f_status == eff)
            )
            if not keep:
                continue

            shown += 1
            if shown > 100:
                st.caption("…more hidden (first 100 shown)")
                break
            wkey = f"{key}_{shown}"  # widget key: unique even for duplicate rows

            icon = "🔁 " if due else STAGE_ICON.get(eff, "")
            notes_preview = (entry or {}).get("notes", "")
            head = (f"{icon}{row.get('company','?')} — {row.get('title','?')}  "
                    f"[{row.get('client')}]"
                    + ("  📝" if notes_preview else ""))
            with st.expander(head, expanded=False):
                a, b = st.columns([1, 1])
                with a:
                    st.text(f"Date:      {row.get('date','')}")
                    st.text(f"Location:  {row.get('location','')}")
                    st.text(f"Hook:      {display_hook(row)}")
                    if entry and eff != "new":
                        st.text(f"Stage:     {eff}"
                                + (f" (since {entry.get('on','')})" if entry.get('on') else "")
                                + ("  — FOLLOW-UP DUE" if due else ""))
                    if row.get("url"):
                        st.markdown(f"[job posting]({row['url']})")
                    if row.get("contact1_name"):
                        st.markdown(
                            f"**Contact:** {row['contact1_name']} — {row.get('contact1_title','')}  \n"
                            f"[LinkedIn]({row.get('contact1_linkedin','')}) · "
                            f"{row.get('contact1_email','')}")
                        if row.get("contact2_name"):
                            st.markdown(
                                f"**Alt:** {row['contact2_name']} — {row.get('contact2_title','')} "
                                f"([LinkedIn]({row.get('contact2_linkedin','')}))")
                    else:
                        st.caption("No contact yet — run enrichment_engine.py")

                    notes = st.text_area("Notes", (entry or {}).get("notes", ""),
                                         height=80, key=f"nt_{wkey}",
                                         placeholder="reply content, objections, next step…")
                    if st.button("Save notes", key=f"svnt_{wkey}"):
                        e = status.get(key, {"status": "new", "on": "", "notes": ""})
                        e["notes"] = notes
                        status[key] = e
                        save_status(status)
                        st.rerun()

                with b:
                    client = row.get("client", "default")
                    cfg = load_profile(client) \
                        if (CONFIG_DIR / f"{client}.json").exists() \
                        else {"dm_template": DEFAULT_TEMPLATE}
                    dm = render_dm(cfg.get("dm_template", DEFAULT_TEMPLATE), row)
                    edited = st.text_area("Draft DM (edit, then copy)", dm,
                                          height=130, key=f"dm_{wkey}")
                    st.code(edited, language=None)

                    if eff == "new":
                        q1, q2 = st.columns(2)
                        with q1:
                            if st.button("Mark contacted", key=f"c_{wkey}",
                                         type="primary", use_container_width=True):
                                set_stage(status, key, "contacted", notes)
                                st.rerun()
                        with q2:
                            if st.button("Dismiss", key=f"dx_{wkey}",
                                         use_container_width=True):
                                set_stage(status, key, "dismissed", notes)
                                st.rerun()
                    else:
                        stage_options = STAGES + ["dismissed"]
                        cur = eff if eff in stage_options else "contacted"
                        new_stage = st.selectbox(
                            "Stage", stage_options,
                            index=stage_options.index(cur), key=f"st_{wkey}")
                        u1, u2 = st.columns(2)
                        with u1:
                            if st.button("Update stage", key=f"up_{wkey}",
                                         type="primary", use_container_width=True):
                                set_stage(status, key, new_stage, notes)
                                st.rerun()
                        with u2:
                            if eff == "contacted" and st.button(
                                    "Followed up (reset timer)", key=f"fu_{wkey}",
                                    use_container_width=True):
                                status[key]["on"] = date.today().isoformat()
                                save_status(status)
                                st.rerun()
        if shown == 0:
            st.success("Nothing for this filter. Go check replies instead.")

# --------------------------- TAB: Clients --------------------------------

with tab_clients:
    leads, src_name = load_leads()
    status = load_status()
    clients = all_clients(leads)

    if not clients:
        st.info("No clients yet — create one in Client Configs.")
    else:
        sel = st.selectbox("Open client page", clients, key="cl_select")
        client_leads = [r for r in leads if r.get("client") == sel]

        # --- client header ---
        has_cfg = (CONFIG_DIR / f"{sel}.json").exists()
        cfg = load_profile(sel) if has_cfg else {}
        st.markdown(f"## {cfg.get('label') or sel}")
        info_bits = [f"`{sel}`"]
        if has_cfg:
            info_bits.append("🟢 active" if cfg.get("active") else "⚪ paused")
            info_bits.append(f"${cfg.get('meeting_rate', DEFAULT_MEETING_RATE)}/meeting")
            if cfg.get("inclusion_keywords"):
                info_bits.append("keywords: " + ", ".join(cfg["inclusion_keywords"][:6])
                                 + ("…" if len(cfg["inclusion_keywords"]) > 6 else ""))
        else:
            info_bits.append("(no config file — data only)")
        st.caption(" · ".join(info_bits))

        if not client_leads:
            st.info("No leads assigned to this client yet. They appear here "
                    "after the nightly sweep matches jobs to this client's config.")
        else:
            # --- client-page filters ---
            f1, f2, f3 = st.columns([1, 2, 1])
            with f1:
                cf_stage = st.selectbox(
                    "Stage", ["all", "new", "follow-up due", "contacted",
                              "replied", "won", "dismissed"], key="cl_stage_v2")
            with f2:
                cf_search = st.text_input("Search (title/company/location/notes)",
                                          key="cl_search")
            with f3:
                cf_days = st.selectbox("Period", ["all time", "7 days", "30 days", "90 days"],
                                       key="cl_days")

            cutoff = None
            if cf_days != "all time":
                cutoff = (date.today() - timedelta(days=int(cf_days.split()[0]))).isoformat()

            table = []
            for r in client_leads:
                if cutoff and (r.get("date", "") or "") < cutoff:
                    continue
                entry = status.get(lead_key(r))
                eff = effective_status(entry)
                due = followup_due(entry)
                if cf_stage == "follow-up due" and not due:
                    continue
                if cf_stage not in ("all", "follow-up due") and eff != cf_stage:
                    continue
                notes = (entry or {}).get("notes", "")
                if cf_search:
                    blob = " ".join([r.get("title", ""), r.get("company", ""),
                                     r.get("location", ""), notes]).lower()
                    if cf_search.lower() not in blob:
                        continue
                table.append({
                    "date": r.get("date", ""),
                    "company": r.get("company", ""),
                    "title": r.get("title", ""),
                    "location": r.get("location", ""),
                    "stage": eff + (" 🔁" if due else ""),
                    "since": (entry or {}).get("on", ""),
                    "notes": notes,
                    "contact": r.get("contact1_name", ""),
                    "url": r.get("url", ""),
                })

            def n_stage(*stages):
                return sum(1 for t in table if t["stage"].split()[0] in stages)

            rate = int(cfg.get("meeting_rate", DEFAULT_MEETING_RATE)) if has_cfg \
                else DEFAULT_MEETING_RATE
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Leads", len(table))
            m2.metric("Contacted+", n_stage("contacted", "replied", "won"))
            m3.metric("Replied", n_stage("replied"))
            m4.metric("Won", n_stage("won"))
            m5.metric("Billable", f"${n_stage('won') * rate}")

            st.caption("Click any column header to sort.")
            st.dataframe(table, use_container_width=True, height=420,
                         column_config={"url": st.column_config.LinkColumn("url")})

            if table:
                out = "\n".join([",".join(table[0].keys())] + [
                    ",".join(f'"{str(v).replace(chr(34), chr(39))}"' for v in t.values())
                    for t in table])
                fname = f"report_{sel}_{date.today().isoformat()}.csv"
                st.download_button("⬇ Export this view as CSV (client report)", out,
                                   file_name=fname, mime="text/csv")

# --------------------------- TAB: Configs --------------------------------

with tab_configs:
    st.sidebar.title("Client profiles")
    profiles = list_profiles()
    choice = st.sidebar.radio("Profile", profiles + ["➕ New client"],
                              index=0 if profiles else len(profiles))

    if choice == "➕ New client":
        cfg = {**{k: (v if not isinstance(v, (list, dict)) else type(v)())
                  for k, v in DEFAULTS.items()},
               "client_id": "", "dm_template": DEFAULT_TEMPLATE,
               "meeting_rate": DEFAULT_MEETING_RATE}
        editing_new = True
    else:
        cfg = load_profile(choice)
        editing_new = False

    col_form, col_test = st.columns([1, 1], gap="large")

    with col_form:
        st.subheader("Profile")
        client_id = st.text_input("Client ID (filename, no spaces)", cfg["client_id"])
        label = st.text_input("Label (human-readable)", cfg.get("label", ""))
        active = st.toggle("Active (included in nightly sweep)", cfg.get("active", True))

        st.subheader("Gates")
        inclusions = st.text_area(
            "Inclusion keywords — title must contain at least one (comma-separated; empty = all pass)",
            ", ".join(cfg.get("inclusion_keywords", [])), height=80)
        exclusions = st.text_area(
            "Exclusion keywords — title must contain none (comma-separated)",
            ", ".join(cfg.get("exclusion_keywords", [])), height=68)
        locations = st.text_area(
            "Allowed locations — substring match, 'remote' also matches remote titles (comma-separated; empty = anywhere)",
            ", ".join(cfg.get("allowed_locations", [])), height=68)

        st.subheader("Outreach & billing")
        dm_template = st.text_area(
            "DM template — placeholders: {first_name} {hook} {company} {title} {location} {client}",
            cfg.get("dm_template", DEFAULT_TEMPLATE), height=110)
        meeting_rate = st.number_input("Rate per won meeting ($)",
                                       value=int(cfg.get("meeting_rate",
                                                         DEFAULT_MEETING_RATE)), step=25)

        new_cfg = {
            "client_id": client_id.strip(),
            "label": label.strip(),
            "active": bool(active),
            "inclusion_keywords": parse_csv_line(inclusions),
            "exclusion_keywords": parse_csv_line(exclusions),
            "allowed_locations": parse_csv_line(locations),
            # scoring UI removed for now — preserve any existing values silently
            "keyword_weights": cfg.get("keyword_weights", {}),
            "min_score": int(cfg.get("min_score", 0)),
            "dm_template": dm_template,
            "meeting_rate": int(meeting_rate),
        }

        s1, s2 = st.columns(2)
        with s1:
            if st.button("💾 Save profile", type="primary", use_container_width=True):
                if not new_cfg["client_id"] or " " in new_cfg["client_id"]:
                    st.error("Client ID is required and must have no spaces.")
                else:
                    (CONFIG_DIR / f"{new_cfg['client_id']}.json").write_text(
                        json.dumps(new_cfg, indent=2), encoding="utf-8")
                    st.success(f"Saved configs/{new_cfg['client_id']}.json")
                    st.rerun()
        with s2:
            if not editing_new and st.button("🗑 Delete profile", use_container_width=True):
                (CONFIG_DIR / f"{cfg['client_id']}.json").unlink(missing_ok=True)
                st.warning(f"Deleted {cfg['client_id']}")
                st.rerun()

    with col_test:
        st.subheader("Test before you trust")
        st.markdown("**Quick test — one job**")
        t_title = st.text_input("Job title", "Senior DevOps Engineer")
        t_loc = st.text_input("Job location", "Remote, Germany")
        result = evaluate_job({"title": t_title, "location": t_loc},
                              new_cfg | {"client_id": new_cfg["client_id"] or "draft"})
        if result:
            st.success("PASSES this config")
            st.caption("DM preview with this job:")
            st.code(render_dm(new_cfg["dm_template"],
                              {"hook": f"saw you just opened a {t_title} role in {t_loc}",
                               "company": "acme", "title": t_title,
                               "location": t_loc, "client": new_cfg["client_id"]}),
                    language=None)
        else:
            st.error("REJECTED by this config")

        st.divider()
        st.markdown("**Dry run — every job currently tracked**")
        jobs = all_tracked_jobs()
        if not jobs:
            st.info("No signals_state.json yet — run signal_engine.py first.")
        else:
            hits = []
            for j in jobs:
                r = evaluate_job(j, new_cfg | {"client_id": new_cfg["client_id"] or "draft"})
                if r:
                    hits.append({"company": j.get("company", ""),
                                 "title": j["title"], "location": j["location"],
                                 "url": j.get("url", ""), "platform": j.get("platform", "")})
            st.metric("Jobs this config would catch", f"{len(hits)} / {len(jobs)}")
            if hits:
                hits.sort(key=lambda x: (x["company"], x["title"]))
                st.dataframe([{k: h[k] for k in ("company", "title", "location")}
                              for h in hits], use_container_width=True, height=320)

                # ---- backfill: give a new client today's live matches, not just future diffs ----
                cid = new_cfg["client_id"]
                if cid:
                    if st.button(f"📥 Backfill these {len(hits)} matches to '{cid}' "
                                 "(skips duplicates)", use_container_width=True):
                        existing = set()
                        if SIGNALS_CSV.exists():
                            for r in csv.DictReader(SIGNALS_CSV.open(encoding="utf-8")):
                                existing.add((r.get("client", ""), r.get("company", ""),
                                              r.get("title", "")))
                        fieldnames = ["date", "client", "platform", "company", "title",
                                      "location", "score", "url", "hook"]
                        new_file = not SIGNALS_CSV.exists()
                        added = 0
                        with SIGNALS_CSV.open("a", newline="", encoding="utf-8") as f:
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
                st.warning("Config catches EVERYTHING — add inclusion keywords or it's not filtering.")
