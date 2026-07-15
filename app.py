#!/usr/bin/env python3
"""
Agentic AI Leads — Local CRM
Tabs:
  Outreach Queue — daily working view: follow-ups due (pre-drafted) + top-N
                   ranked new sends, one-click copy / mark contacted,
                   cross-client double-DM guard
  Daily Inbox    — all signals -> DMs -> stages (contacted/replied/won),
                   notes, dismiss, follow-up timers
  Clients        — per-client page: leads, filters, metrics, CSV export
  Mini-List      — free gift list for prospect agencies (CSV + branded HTML)
  Watchlist      — boards being swept nightly
  Client Configs — profiles, live tester, dry-run, backfill

Run:  streamlit run app.py
"""

import csv
import hashlib
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import streamlit as st

from filter_engine import evaluate_job, DEFAULTS
from minilist_engine import build_minilist, render_csv, render_html, DEFAULT_PITCH

HERE = Path(__file__).resolve().parent
CONFIG_DIR = HERE / "configs"
SIGNALS_CSV = HERE / "signals.csv"
ENRICHED_CSV = HERE / "enriched_signals.csv"
CONTACTS_CACHE = HERE / "enriched_contacts.json"
STATE_FILE = HERE / "signals_state.json"
INBOX_STATUS = HERE / "inbox_status.json"
WATCHLIST_FILE = HERE / "watchlist.json"

DEFAULT_TEMPLATE = (
    "Hi {first_name} — {hook}. Roles like that are exactly what we place. "
    "Worth a quick chat about how we can help you fill them faster?"
)
DEFAULT_EMAIL_SUBJECT = "Your {title} opening"
DEFAULT_EMAIL_TEMPLATE = (
    "Hi {first_name},\n\n"
    "{hook} — filling roles like that is exactly what we do.\n\n"
    "Worth a quick call this week? Happy to share how we'd approach it.\n\n"
    "Best,\n"
)
DEFAULT_FOLLOWUP_TEMPLATE = (
    "Hi {first_name} — floating this back up. {hook}, and roles like that "
    "don't stay open long. Worth a quick chat this week?"
)
DEFAULT_FOLLOWUP2_TEMPLATE = (
    "Hi {first_name} — last note from me. If filling roles faster ever becomes "
    "a priority, happy to show you how we work: you pay per meeting that "
    "actually happens, first one free. Door's open."
)
FOLLOWUP_DAYS = 3
MAX_FOLLOWUPS = 2   # after follow-up #2 (breakup), lead leaves the queue
FRESH_DAYS = 7      # signals older than this switch to "currently hiring" wording
DEFAULT_MEETING_RATE = 150
DAILY_QUEUE_CAP = 15   # default new sends/day (LinkedIn-safe)

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
    cfg.setdefault("email_subject", DEFAULT_EMAIL_SUBJECT)
    cfg.setdefault("email_template", DEFAULT_EMAIL_TEMPLATE)
    cfg.setdefault("followup_template", DEFAULT_FOLLOWUP_TEMPLATE)
    cfg.setdefault("followup2_template", DEFAULT_FOLLOWUP2_TEMPLATE)
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
    """signals.csv is the source of truth; contacts are joined in live from
    enriched_contacts.json, so new signals never wait on an enrichment
    re-run. Falls back to enriched_signals.csv if signals.csv is missing."""
    src = SIGNALS_CSV if SIGNALS_CSV.exists() else ENRICHED_CSV
    if not src.exists():
        return [], src.name
    rows = list(csv.DictReader(src.open(encoding="utf-8")))
    try:
        cache = json.loads(CONTACTS_CACHE.read_text(encoding="utf-8")) \
            if CONTACTS_CACHE.exists() else {}
    except json.JSONDecodeError:
        cache = {}
    for r in rows:
        if not r.get("client"):
            r["client"] = "default"
        if not r.get("contact1_name"):
            for i, c in enumerate((cache.get(r.get("company", "")) or [])[:2], 1):
                for k, v in c.items():
                    r[f"contact{i}_{k}"] = v
    label = src.name + (" + contacts" if cache else "")
    return rows, label


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
    try:
        if int(entry.get("fu", 0) or 0) >= MAX_FOLLOWUPS:
            return False   # breakup already sent — stop chasing
    except (TypeError, ValueError):
        pass
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


# --------- watchlist helpers ---------

ATS_URL_PATTERNS = [
    ("greenhouse", r"greenhouse\.io/embed/job_board\?for=([A-Za-z0-9_-]+)"),
    ("greenhouse", r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)"),
    ("greenhouse", r"(?:boards|job-boards)\.greenhouse\.io/([A-Za-z0-9_-]+)"),
    ("lever", r"api\.lever\.co/v0/postings/([A-Za-z0-9_-]+)"),
    ("lever", r"jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_-]+)"),
    ("ashby", r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_.%-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)"),
    ("workable", r"apply\.workable\.com/api/v1/widget/accounts/([A-Za-z0-9_-]+)"),
    ("workable", r"apply\.workable\.com/([A-Za-z0-9_-]+)"),
]


def parse_board_url(url: str):
    """Any job-board URL -> (platform, token) or (None, None)."""
    url = (url or "").strip()
    for platform, pat in ATS_URL_PATTERNS:
        m = re.search(pat, url)
        if m:
            token = m.group(1).lower()
            if token in ("api", "v1", "embed", "widget"):
                continue
            return platform, token
    return None, None


def load_watchlist():
    if WATCHLIST_FILE.exists():
        try:
            wl = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            wl = {}
    else:
        wl = {}
    return {p: sorted(set(str(t).strip().lower() for t in wl.get(p, []) if str(t).strip()))
            for p in ["greenhouse", "lever", "ashby", "workable"]}


def save_watchlist(wl):
    WATCHLIST_FILE.write_text(json.dumps(wl, indent=2), encoding="utf-8")


def tracked_job_counts():
    """{platform:token -> live job count} from the engine's state file."""
    if not STATE_FILE.exists():
        return {}
    state = json.loads(STATE_FILE.read_text())
    return {k: len(v) for k, v in state.items()}


def mark_followed_up(status, key):
    """Reset the follow-up timer and count the touch (day-3 -> day-6 -> stop)."""
    e = status.get(key, {"status": "contacted", "on": "", "notes": ""})
    e["on"] = date.today().isoformat()
    e["fu"] = int(e.get("fu", 0) or 0) + 1
    status[key] = e
    save_status(status)


def set_stage(status, key, stage, notes=None):
    entry = status.get(key, {"status": "new", "on": "", "notes": ""})
    entry["status"] = stage
    entry["on"] = date.today().isoformat()
    if notes is not None:
        entry["notes"] = notes
    status[key] = entry
    save_status(status)


# ================================ UI ====================================

tab_queue, tab_inbox, tab_clients, tab_minilist, tab_watchlist, tab_configs = st.tabs(
    ["🚀 Outreach Queue", "📥 Daily Inbox", "👤 Clients", "🎁 Mini-List",
     "📡 Watchlist", "⚙️ Client Configs"])

# ------------------------- TAB: Outreach Queue ---------------------------

with tab_queue:
    leads, src_name = load_leads()
    status = load_status()

    if not leads:
        st.info("No signals yet — run signal_engine.py, then refresh.")
    else:
        # collision guard: contacts already touched, keyed by LinkedIn URL
        contacted_by_link = {}
        for r in leads:
            e = status.get(lead_key(r))
            if e and e.get("status") in ("contacted", "replied", "won"):
                lk = (r.get("contact1_linkedin") or "").strip()
                if lk:
                    contacted_by_link.setdefault(lk, r.get("client", ""))

        qc1, qc2, qc3 = st.columns([1, 1, 2])
        with qc1:
            q_client = st.selectbox("Client", ["all"] + all_clients(leads),
                                    key="q_client")
        with qc2:
            q_cap = int(st.number_input("Daily cap (new sends)", 1, 50,
                                        DAILY_QUEUE_CAP, key="q_cap"))

        due, fresh = [], []
        for r in leads:
            if q_client != "all" and r.get("client") != q_client:
                continue
            e = status.get(lead_key(r))
            if followup_due(e):
                due.append(r)
            elif effective_status(e) == "new":
                fresh.append(r)

        def _score(r):
            try:
                return int(r.get("score") or 0)
            except ValueError:
                return 0

        # best first: has a named contact > lead score > freshness
        fresh.sort(key=lambda r: (bool(r.get("contact1_name")), _score(r),
                                  r.get("date", "")), reverse=True)

        today_iso = date.today().isoformat()
        sent_today = sum(
            1 for e in status.values()
            if e.get("on") == today_iso
            and e.get("status") in ("contacted", "replied", "won"))
        with qc3:
            st.caption(f"Source: {src_name} — "
                       f"**{len(due)} follow-up(s) due · "
                       f"{min(len(fresh), q_cap)} of {len(fresh)} new queued · "
                       f"{sent_today} touched today**")

        def outreach_card(row, kind, i):
            """One actionable card. kind: 'fu' (follow-up) or 'new'."""
            key = lead_key(row)
            entry = status.get(key)
            client = row.get("client", "default")
            cfg = load_profile(client)
            wkey = f"q{kind}_{key}_{i}"
            contact = row.get("contact1_name", "")

            head = (("🔁 " if kind == "fu" else "🆕 ")
                    + f"{row.get('company','?')} — {row.get('title','?')}  "
                    f"[{client}]"
                    + (f"  · {contact}" if contact else "  · ⚠ no contact"))
            with st.expander(head, expanded=(i == 0)):
                meta = " · ".join(x for x in [row.get("date", ""),
                                              row.get("location", "")] if x)
                if meta:
                    st.caption(meta)

                lk = (row.get("contact1_linkedin") or "").strip()
                if lk and contacted_by_link.get(lk, client) != client:
                    st.warning(f"{contact} was already contacted for client "
                               f"'{contacted_by_link[lk]}' — don't double-DM.")
                if contact:
                    st.markdown(f"**{contact}** — {row.get('contact1_title','')}"
                                + (f" · [open LinkedIn ↗]({lk})" if lk else ""))
                else:
                    st.caption("No contact yet — run enrichment_engine.py or "
                               "find them manually on LinkedIn.")
                if row.get("url"):
                    st.markdown(f"[job posting]({row['url']})")

                if kind == "fu":
                    n_fu = int((entry or {}).get("fu", 0) or 0)
                    tmpl = (cfg.get("followup2_template",
                                    DEFAULT_FOLLOWUP2_TEMPLATE) if n_fu >= 1
                            else cfg.get("followup_template",
                                         DEFAULT_FOLLOWUP_TEMPLATE))
                    st.caption(f"Follow-up #{n_fu + 1}"
                               + (" (breakup)" if n_fu >= 1 else "")
                               + f" — last touch {(entry or {}).get('on', '?')}")
                else:
                    tmpl = cfg.get("dm_template", DEFAULT_TEMPLATE)
                st.code(render_dm(tmpl or DEFAULT_TEMPLATE, row), language=None)

                email = row.get("contact1_email", "")
                if "@" in email and "locked" not in email.lower():
                    subj = render_dm(cfg.get("email_subject",
                                             DEFAULT_EMAIL_SUBJECT), row)
                    body = render_dm(tmpl or DEFAULT_TEMPLATE, row)
                    st.markdown(f"[✉️ email {email}]"
                                f"(mailto:{email}?subject={quote(subj)}"
                                f"&body={quote(body)})")

                b1, b2 = st.columns(2)
                if kind == "fu":
                    with b1:
                        if st.button("🔁 Followed up", key=f"fud_{wkey}",
                                     type="primary", use_container_width=True):
                            mark_followed_up(status, key)
                            st.rerun()
                    with b2:
                        if st.button("💬 They replied", key=f"rpd_{wkey}",
                                     use_container_width=True):
                            set_stage(status, key, "replied")
                            st.rerun()
                else:
                    with b1:
                        if st.button("✅ Sent — mark contacted",
                                     key=f"ctd_{wkey}", type="primary",
                                     use_container_width=True):
                            set_stage(status, key, "contacted")
                            st.rerun()
                    with b2:
                        if st.button("🚫 Dismiss", key=f"dxd_{wkey}",
                                     use_container_width=True):
                            set_stage(status, key, "dismissed")
                            st.rerun()

        st.markdown(f"#### 🔁 Follow-ups due ({len(due)}) — do these first")
        if due:
            for i, r in enumerate(due):
                outreach_card(r, "fu", i)
        else:
            st.caption("None due.")

        st.markdown(f"#### 🆕 New sends (top {q_cap} of {len(fresh)})")
        if fresh:
            for i, r in enumerate(fresh[:q_cap]):
                outreach_card(r, "new", i)
        else:
            st.caption("No new leads — run signal_engine.py or grow the watchlist.")

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

                    # one-click email compose (only when a real address exists)
                    email = row.get("contact1_email", "")
                    if "@" in email and "locked" not in email.lower():
                        subj = render_dm(cfg.get("email_subject",
                                                 DEFAULT_EMAIL_SUBJECT), row)
                        body = render_dm(cfg.get("email_template",
                                                 DEFAULT_EMAIL_TEMPLATE), row)
                        st.markdown(
                            f"[✉️ Compose email to {email}]"
                            f"(mailto:{email}?subject={quote(subj)}&body={quote(body)})")

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
                                mark_followed_up(status, key)
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

# --------------------------- TAB: Mini-List ------------------------------

with tab_minilist:
    st.markdown("### 🎁 Free mini-list — the door-opener")
    st.caption("Unsolicited gift for a prospect agency: companies hiring in "
               "THEIR niche right now, from your tracked boards. Send it with "
               "“Want 50 more per week?” — strongest $0 sales asset "
               "you have.")

    g1, g2 = st.columns(2)
    with g1:
        ml_prospect = st.text_input("Prospect agency name",
                                    placeholder="Acme Staffing",
                                    key="ml_prospect")
        ml_keywords = st.text_input(
            "Their niche keywords (comma-separated)",
            placeholder="java, .net, backend", key="ml_kw")
    with g2:
        ml_locations = st.text_input(
            "Their market (locations, comma-separated — empty = anywhere)",
            placeholder="united states, remote", key="ml_loc")
        ml_n = st.slider("List size", 5, 25, 10, key="ml_n")
    ml_pitch = st.text_input("Footer pitch line", DEFAULT_PITCH, key="ml_pitch")

    ml_kws = parse_csv_line(ml_keywords)
    if not ml_kws:
        st.info("Enter at least one niche keyword to build the list.")
    else:
        ml_rows = build_minilist(ml_kws, parse_csv_line(ml_locations),
                                 n=int(ml_n))
        if not ml_rows:
            st.warning("No tracked jobs match — widen the keywords or grow "
                       "the watchlist.")
        else:
            n_contact = sum(1 for r in ml_rows if r["contact_name"])
            st.caption(f"**{len(ml_rows)} companies** ({n_contact} with a "
                       "named contact). Companies with contacts and multiple "
                       "matching roles rank first.")
            st.dataframe(
                ml_rows, use_container_width=True, height=340,
                column_config={
                    "job_url": st.column_config.LinkColumn("job_url"),
                    "contact_linkedin":
                        st.column_config.LinkColumn("contact_linkedin")})

            ml_slug = re.sub(r"[^a-z0-9]+", "-",
                             (ml_prospect or "prospect").lower()).strip("-")
            ml_fname = f"minilist_{ml_slug}_{date.today().isoformat()}"
            d1, d2 = st.columns(2)
            with d1:
                st.download_button("⬇ CSV (raw data)", render_csv(ml_rows),
                                   file_name=f"{ml_fname}.csv",
                                   mime="text/csv", use_container_width=True)
            with d2:
                st.download_button(
                    "⬇ Branded one-pager (HTML)",
                    render_html(ml_prospect or "you", ml_rows, ml_pitch),
                    file_name=f"{ml_fname}.html", mime="text/html",
                    use_container_width=True)
            st.caption("Open the HTML and Ctrl+P → PDF for a clean "
                       "attachment, or paste the top rows straight into a DM.")

# --------------------------- TAB: Watchlist ------------------------------

with tab_watchlist:
    wl = load_watchlist()
    counts = tracked_job_counts()
    total = sum(len(v) for v in wl.values())
    st.markdown(f"### {total} companies watched")
    st.caption("Every company here gets swept nightly. Paste any job-board URL "
               "below — platform and token are detected automatically. "
               "Newly added companies are baselined silently on their first "
               "sweep (no false 'new job' flood).")

    url_in = st.text_input(
        "Paste a job-board URL",
        placeholder="https://boards.greenhouse.io/acme  ·  https://jobs.lever.co/acme  ·  https://jobs.ashbyhq.com/acme  ·  https://apply.workable.com/acme",
        key="wl_url")
    if url_in.strip():
        platform, token = parse_board_url(url_in)
        if platform:
            already = token in wl[platform]
            if already:
                st.info(f"**{token}** ({platform}) is already on the watchlist.")
            else:
                st.success(f"Detected: **{token}** on **{platform}**")
                if st.button(f"➕ Add {token} to watchlist", type="primary",
                             key="wl_add"):
                    wl[platform].append(token)
                    wl[platform] = sorted(set(wl[platform]))
                    save_watchlist(wl)
                    st.rerun()
        else:
            st.error("Couldn't detect a Greenhouse / Lever / Ashby / Workable "
                     "board in that URL. Add manually below.")

    with st.expander("Bulk import — paste anything containing board URLs "
                     "(page source, HN thread, newsletter)"):
        blob = st.text_area("Paste text here", height=140, key="wl_bulk",
                            placeholder="Any text with greenhouse/lever/ashby/"
                                        "workable links — all are detected.")
        if blob.strip():
            import html as _html
            bulk_found = set()
            for _platform, _pat in ATS_URL_PATTERNS:
                for m in re.finditer(_pat, _html.unescape(blob), re.IGNORECASE):
                    t = m.group(1).lower()
                    if t not in ("api", "v1", "embed", "widget"):
                        bulk_found.add((_platform, t))
            bulk_new = sorted(x for x in bulk_found if x[1] not in wl[x[0]])
            st.caption(f"**{len(bulk_found)}** board(s) detected — "
                       f"**{len(bulk_new)}** not yet watched")
            if bulk_new:
                st.write(", ".join(f"`{t}` ({p})" for p, t in bulk_new))
                if st.button(f"➕ Add all {len(bulk_new)}", type="primary",
                             key="wl_bulk_add"):
                    for p, t in bulk_new:
                        wl[p].append(t)
                    for p in wl:
                        wl[p] = sorted(set(wl[p]))
                    save_watchlist(wl)
                    st.rerun()

    with st.expander("Add manually (platform + token)"):
        mc1, mc2, mc3 = st.columns([1, 2, 1])
        with mc1:
            m_platform = st.selectbox("Platform",
                                      ["greenhouse", "lever", "ashby", "workable"],
                                      key="wl_mplat")
        with mc2:
            m_token = st.text_input("Board token (e.g. 'acme')", key="wl_mtok")
        with mc3:
            st.write("")
            st.write("")
            if st.button("Add", key="wl_madd") and m_token.strip():
                wl[m_platform].append(m_token.strip().lower())
                wl[m_platform] = sorted(set(wl[m_platform]))
                save_watchlist(wl)
                st.rerun()

    st.divider()
    for platform in ["greenhouse", "lever", "ashby", "workable"]:
        tokens = wl[platform]
        st.markdown(f"**{platform.capitalize()}** — {len(tokens)} board(s)")
        if not tokens:
            st.caption("none yet")
            continue
        for i, token in enumerate(tokens):
            k = f"{platform}:{token}"
            n = counts.get(k)
            rc1, rc2, rc3 = st.columns([3, 2, 1])
            rc1.text(token)
            rc2.caption(f"{n} live jobs tracked" if n is not None
                        else "not swept yet — appears after next run")
            if rc3.button("remove", key=f"wl_rm_{platform}_{i}"):
                wl[platform].remove(token)
                save_watchlist(wl)
                st.rerun()

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
        email_subject = st.text_input(
            "Email subject (same placeholders)",
            cfg.get("email_subject", DEFAULT_EMAIL_SUBJECT))
        email_template = st.text_area(
            "Email template (same placeholders)",
            cfg.get("email_template", DEFAULT_EMAIL_TEMPLATE), height=140)
        followup_template = st.text_area(
            f"Follow-up #1 (due {FOLLOWUP_DAYS} days after contact, same placeholders)",
            cfg.get("followup_template", DEFAULT_FOLLOWUP_TEMPLATE), height=90)
        followup2_template = st.text_area(
            "Follow-up #2 — breakup (last touch, same placeholders)",
            cfg.get("followup2_template", DEFAULT_FOLLOWUP2_TEMPLATE), height=90)
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
            "email_subject": email_subject,
            "email_template": email_template,
            "followup_template": followup_template,
            "followup2_template": followup2_template,
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
