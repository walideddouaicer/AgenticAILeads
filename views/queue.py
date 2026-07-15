"""Outreach Queue — the daily working view.

Follow-ups due (pre-drafted) first, then the top-N ranked new sends.
One-click copy / mark contacted, cross-client double-DM guard.
"""

import streamlit as st

import common as C

C.page_header("🚀 Outreach Queue", "queue")

leads, src_name = C.load_leads()
status = C.load_status()

if not leads:
    st.info("**No signals yet.** The queue fills up once the signal engine "
            "has run twice (first run records the baseline, the next runs "
            "report new roles).")
    st.markdown("1. Make sure the watchlist has boards — **System → Watchlist**\n"
                "2. Run the sweep below (or `python signal_engine.py`)\n"
                "3. Come back here — new leads appear ranked and pre-drafted")
    C.render_engine_panel()
    C.engine_button("sweep", key="q_run_sweep")
    st.stop()

# collision guard: contacts already touched, keyed by LinkedIn URL
contacted_by_link = {}
for r in leads:
    e = status.get(C.lead_key(r))
    if e and e.get("status") in ("contacted", "replied", "won"):
        lk = (r.get("contact1_linkedin") or "").strip()
        if lk:
            contacted_by_link.setdefault(lk, r.get("client", ""))

qc1, qc2, qc3 = st.columns([1, 1, 2])
with qc1:
    q_client = st.selectbox("Client", ["all"] + C.all_clients(leads),
                            key="q_client",
                            help="Work one client's queue, or all at once.")
with qc2:
    q_cap = int(st.number_input("Daily cap (new sends)", 1, 50,
                                C.DAILY_QUEUE_CAP, key="q_cap",
                                help="Max NEW leads queued per day. Keep it "
                                     "LinkedIn-safe (~15/day, 100–150 "
                                     "connection requests/week)."))

due, fresh = [], []
for r in leads:
    if q_client != "all" and r.get("client") != q_client:
        continue
    e = status.get(C.lead_key(r))
    if C.followup_due(e):
        due.append(r)
    elif C.effective_status(e) == "new":
        fresh.append(r)


def _score(r):
    try:
        return int(r.get("score") or 0)
    except ValueError:
        return 0


# best first: has a named contact > lead score > freshness
fresh.sort(key=lambda r: (bool(r.get("contact1_name")), _score(r),
                          r.get("date", "")), reverse=True)

from datetime import date  # noqa: E402

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

C.render_engine_panel()


def _reset_focus():
    st.session_state["q_focus_done"] = 0
    st.session_state["q_focus_idx"] = 0


fm1, fm2 = st.columns([1, 3], vertical_alignment="center")
with fm1:
    focus_mode = st.toggle("🎯 Focus mode", key="q_focus",
                           on_change=_reset_focus,
                           help="One card at a time with a progress bar — "
                               "Sent/Dismiss/Followed-up auto-advances to "
                               "the next lead. Toggle off for the full list.")
with fm2:
    u = C.undo_available()
    if u and st.button(f"↩ Undo: {u['label']}", key="q_undo",
                       help="Restores the previous stage/notes/follow-up "
                            "state of the last lead you changed."):
        label = C.undo_last()
        if st.session_state.get("q_focus"):
            st.session_state["q_focus_done"] = max(
                0, st.session_state.get("q_focus_done", 0) - 1)
        C.push_toast(f"Undone: {label}", "↩")
        st.rerun()


def _count_focus_action():
    if st.session_state.get("q_focus"):
        st.session_state["q_focus_done"] = \
            st.session_state.get("q_focus_done", 0) + 1


def lead_label(row):
    return f"{row.get('company', '?')} — {row.get('title', '?')}"


def outreach_card(row, kind, i):
    """One actionable card. kind: 'fu' (follow-up) or 'new'."""
    key = C.lead_key(row)
    entry = status.get(key)
    client = row.get("client", "default")
    cfg = C.load_profile(client)
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
            st.caption("No contact yet — run enrichment (Home page) or "
                       "find them manually on LinkedIn.")
        if row.get("url"):
            st.markdown(f"[job posting]({row['url']})")

        if kind == "fu":
            n_fu = int((entry or {}).get("fu", 0) or 0)
            tmpl = (cfg.get("followup2_template",
                            C.DEFAULT_FOLLOWUP2_TEMPLATE) if n_fu >= 1
                    else cfg.get("followup_template",
                                 C.DEFAULT_FOLLOWUP_TEMPLATE))
            st.caption(f"Follow-up #{n_fu + 1}"
                       + (" (breakup)" if n_fu >= 1 else "")
                       + f" — last touch {(entry or {}).get('on', '?')}")
        else:
            tmpl = cfg.get("dm_template", C.DEFAULT_TEMPLATE)
        st.code(C.render_dm(tmpl or C.DEFAULT_TEMPLATE, row), language=None)

        email = row.get("contact1_email", "")
        if "@" in email and "locked" not in email.lower():
            subj = C.render_dm(cfg.get("email_subject",
                                       C.DEFAULT_EMAIL_SUBJECT), row)
            body = C.render_dm(tmpl or C.DEFAULT_TEMPLATE, row)
            st.markdown(f"[✉️ email {email}]({C.mailto_link(email, subj, body)})")

        b1, b2 = st.columns(2)
        if kind == "fu":
            with b1:
                if st.button("🔁 Followed up", key=f"fud_{wkey}",
                             type="primary", width="stretch",
                             help="Logs the follow-up and resets the "
                                  f"{C.FOLLOWUP_DAYS}-day timer. After "
                                  f"follow-up #{C.MAX_FOLLOWUPS} (breakup) "
                                  "the lead leaves the queue."):
                    C.mark_followed_up(status, key, lead_label(row))
                    _count_focus_action()
                    st.rerun()
            with b2:
                if st.button("💬 They replied", key=f"rpd_{wkey}",
                             width="stretch",
                             help="Moves the lead to the 'replied' stage — "
                                  "handle the conversation in the Inbox."):
                    C.set_stage(status, key, "replied",
                                lead_label=lead_label(row))
                    _count_focus_action()
                    st.rerun()
        else:
            with b1:
                if st.button("✅ Sent — mark contacted",
                             key=f"ctd_{wkey}", type="primary",
                             width="stretch",
                             help="Click after you actually sent the DM. "
                                  "Starts the follow-up timer."):
                    C.set_stage(status, key, "contacted",
                                lead_label=lead_label(row))
                    _count_focus_action()
                    st.rerun()
            with b2:
                if st.button("🚫 Dismiss", key=f"dxd_{wkey}",
                             width="stretch",
                             help="Removes the lead from the queue (bad fit, "
                                  "duplicate…). Undo is available after."):
                    C.set_stage(status, key, "dismissed",
                                lead_label=lead_label(row))
                    _count_focus_action()
                    st.rerun()


if focus_mode:
    # one card at a time: follow-ups first, then the capped new sends
    work = [(r, "fu") for r in due] + [(r, "new") for r in fresh[:q_cap]]
    done = st.session_state.get("q_focus_done", 0)
    total = done + len(work)
    if not work:
        st.success("🎉 Queue clear — every follow-up and new send for today "
                   "is done. Check the Inbox for replies, or raise the "
                   "daily cap if you have volume left.")
        st.session_state["q_focus_done"] = 0
        st.session_state["q_focus_idx"] = 0
    else:
        idx = st.session_state.get("q_focus_idx", 0) % len(work)
        st.progress(done / total if total else 0.0,
                    text=f"Send {min(done + 1, total)} of {total}"
                         f"  ·  {len(due)} follow-up(s), "
                         f"{len(work) - len(due)} new")
        row, kind = work[idx]
        outreach_card(row, kind, 0)
        sk1, _ = st.columns([1, 3])
        with sk1:
            if st.button("Skip →", key="q_skip_btn", width="stretch",
                         help="Puts this card at the back and shows the "
                              "next one — nothing is recorded."):
                st.session_state["q_focus_idx"] = idx + 1
                st.rerun()
else:
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
        st.caption("No new leads — run the sweep (Home page) or grow the "
                   "watchlist (System → Watchlist).")
