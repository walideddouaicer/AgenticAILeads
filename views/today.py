"""Today — the daily driver. One prospect at a time, not a list to browse.

This page renders today.json and nothing else. It does no ranking, no
filtering and no matching: build_today.py decides who is in the queue and
in what order, so when that ranking gets smarter (days_open, repost
counts, reply-rate feedback) this file does not change.

The only writes are stage changes, and they go through C.set_stage /
C.mark_followed_up so undo and the toast still work.
"""

import json
from datetime import date

import streamlit as st

import common as C
from minilist_engine import render_csv, render_table

C.page_header("📮 Today", "today")

TODAY_FILE = C.DATA_DIR / "today.json"

# ----------------------------------------------------------------- load
if not TODAY_FILE.exists():
    st.info("**No queue built yet.**")
    st.markdown(
        "Today's list is produced by a script so the ranking can improve "
        "without touching this page. Build it with:\n\n"
        "```powershell\npython build_today.py --n 20\n```\n\n"
        "Then reload. Run it each morning (or add it to `run_sweep.bat`)."
    )
    st.stop()

try:
    data = json.loads(TODAY_FILE.read_text(encoding="utf-8"))
except json.JSONDecodeError:
    st.error("`today.json` is unreadable. Re-run `python build_today.py`.")
    st.stop()

items = data.get("items") or []
built_on = data.get("date", "")
counts = data.get("counts", {})

if built_on != date.today().isoformat():
    st.warning(f"This queue was built on **{built_on}**, not today. "
               "Re-run `python build_today.py` for fresh hooks — a mandate "
               "from last week reads as stale.")

if not items:
    st.warning(
        f"**Nothing in the small list.** "
        f"{counts.get('bulk_queued', 0)} prospect(s) are queued for bulk "
        "email instead.")
    if counts.get("blocked_by"):
        st.markdown("**Why they didn't qualify** — fix the top one first:")
        for why, n in counts["blocked_by"].items():
            st.markdown(f"- **{n}** — {why}")
        st.caption("A prospect can fail several rules, so these add up to "
                   "more than the number of prospects.")
    if counts.get("skipped"):
        st.caption("Skipped entirely: " + " · ".join(
            f"{n} {why}" for why, n in counts["skipped"].items()))
    st.stop()

# --------------------------------------------------------------- state
status = C.load_status(C.PROSPECTS_STATUS)

# Anything actioned since the queue was built drops out, so the counter
# reflects reality even if you also worked prospects from the CRM.
pending = [i for i in items
           if C.effective_status(status.get(i["key"])) in ("new", "contacted")
           or i["is_followup"]]
pending = [i for i in pending
           if C.effective_status(status.get(i["key"])) not in
           ("replied", "won", "call_booked", "client_won", "dismissed")]

done_today = len(items) - len(pending)

# Skipped prospects go to the back of today's queue, not out of it — a skip
# is "not right now", not "never". Cleared when the queue is rebuilt.
skipped = st.session_state.setdefault("_today_skipped", set())
if skipped:
    pending = ([i for i in pending if i["key"] not in skipped]
               + [i for i in pending if i["key"] in skipped])
target = data.get("target", len(items))

c1, c2, c3 = st.columns(3)
c1.metric("Sent / actioned", f"{done_today} / {len(items)}")
c2.metric("Follow-ups due", counts.get("followups_due", 0))
c3.metric("Fresh available", counts.get("fresh_available", 0))
st.progress(done_today / max(len(items), 1))

if not pending:
    st.success(f"**Done for today — {done_today} actioned.** "
               "Re-run `python build_today.py` tomorrow.")
    st.stop()

# ------------------------------------------------------------ the card
item = pending[0]
key = item["key"]
entry = status.get(key) or {}

st.divider()
head_l, head_r = st.columns([3, 1])
with head_l:
    tag = (f"Follow-up #{item['followup_number']}" if item["is_followup"]
           else "First touch")
    st.markdown(f"### {item['agency']}")
    st.caption(f"{item['person_name']} · {item['person_title']} · "
               f"{item['location']} · {item['size'] or '?'} people — **{tag}**")
with head_r:
    st.caption(f"{len(pending)} left in today's queue")

# hook — the thing that earns the reply
src = item["hook_source"]
badge = {"mandate": "board posting", "niche": "stated niche"}.get(src, src)
st.markdown(f"**Hook** ({badge})")
st.info(item["hook"])
if item.get("mandate_url"):
    st.caption(f"Proof: {item['mandate_url']}")

# links to act on
l1, l2 = st.columns(2)
with l1:
    if item.get("linkedin"):
        st.link_button("Open LinkedIn profile", item["linkedin"],
                       width="stretch")
with l2:
    if item.get("email"):
        st.link_button("Email", C.mailto_link(item["email"], "", item["message"]),
                       width="stretch")
    else:
        st.button("No email on file", disabled=True, width="stretch",
                  key=f"noemail_{key}",
                  help="LinkedIn only for this one — that's fine, the "
                       "prospect pipeline is DM-first.")

# the message, editable before sending
msg = st.text_area("Message", item["message"], height=170, key=f"msg_{key}",
                   help="Edit freely — the template is what we measure, so "
                        "change the hook rather than the offer when you can.")
st.code(msg, language=None)

# the gift
gift = item.get("gift") or []
if gift:
    with st.expander(f"🎁 Sample — {len(gift)} board/company rows matching "
                     f"{', '.join(item['niche'][:3])} "
                     f"({item['gift_with_contacts']} with named contacts)"):
        st.dataframe(gift, width="stretch", hide_index=True)
        st.markdown("**Paste this into the message** — a table in the body "
                    "beats an attachment nobody opens:")
        st.code(render_table(gift), language=None)
        slug = "".join(ch if ch.isalnum() else "-"
                       for ch in item["agency"].lower()).strip("-") or "prospect"
        st.download_button(
            "⬇ CSV (if they ask for the full list)", render_csv(gift),
            file_name=f"minilist_{slug}_{date.today().isoformat()}.csv",
            mime="text/csv", key=f"gc_{key}", width="stretch")
    if item["gift_with_contacts"] == 0:
        st.caption("No named contacts in this sample. Confirm company "
                   "identities and add contacts manually, or run a "
                   "provider lookup if configured.")
else:
    st.caption("No mini-list — this prospect has no niche keywords, so "
               "there's nothing to build a gift from.")

# ------------------------------------------------------------ actions
st.divider()
a1, a2, a3 = st.columns(3)
label = item["agency"]
with a1:
    if st.button("✅ Sent", key=f"sent_{key}", width="stretch",
                 type="primary",
                 disabled=msg.startswith("Template needs correction:"),
                 help="Marks contacted and starts the follow-up timer."):
        if item["is_followup"]:
            C.mark_followed_up(status, key, label, C.PROSPECTS_STATUS)
        else:
            C.set_stage(status, key, "contacted", lead_label=label,
                        path=C.PROSPECTS_STATUS)
        st.rerun()
with a2:
    if st.button("⏭ Skip for now", key=f"skip_{key}", width="stretch",
                 help="Moves them to the back of today's queue. Stage is "
                      "untouched, so they return tomorrow either way."):
        skipped.add(key)
        st.rerun()
with a3:
    if st.button("🚫 Not a fit", key=f"nofit_{key}", width="stretch",
                 help="Dismisses them permanently, with a reason."):
        st.session_state[f"_nofit_{key}"] = True

if st.session_state.get(f"_nofit_{key}"):
    why = st.text_input("Why not a fit?", key=f"why_{key}",
                        placeholder="too big / wrong niche / no longer trading",
                        help="Reasons are how the targeting gets better — "
                             "they're the only record of what we got wrong.")
    if st.button("Confirm dismiss", key=f"cd_{key}"):
        C.set_stage(status, key, "dismissed", notes=why, lead_label=label,
                    path=C.PROSPECTS_STATUS)
        st.session_state.pop(f"_nofit_{key}", None)
        st.rerun()
