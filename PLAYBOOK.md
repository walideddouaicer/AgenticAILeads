# Historical business notes — unverified and superseded by README.md for app operation.
# Do not treat old volume, pricing, or integration claims as current product behavior.

# AGENTIC AI LEADS — PROJECT PLAYBOOK
*Last updated: July 17, 2026 (blocks 8–10: own-client prospecting machine). Give this file to any AI assistant to restore full project context.*

## The business

**Agentic AI Leads** — two-person company (Walid Eddouaicer: systems/engineering; co-founder: sales/LinkedIn outreach). Both AI/ML engineering background.

**Service:** pay-per-meeting lead generation for tech recruitment/staffing agencies (2–20 employees, US/UK/FR/CH). We detect companies actively hiring (public job-board signals), reach the decision-makers, and book qualified sales meetings for our clients.

**Offer:** first meeting free, then $150 per showed meeting. No retainer, no contract. Planned evolution after 2–3 clients: $1,500/mo base + $100/meeting (industry-standard hybrid — pure pay-per-lead becomes unmanageable at 5+ clients).

**Why this niche:** recruitment agencies already buy leads (no education needed), live on LinkedIn (reachable free), high deal value (one placement = $15–25k), and small ones have no BD staff (founder does sales in spare time). Dedicated vendors already sell to this market (Zaphyr Pro, Agency Leads) — proven category.

**Budget constraint:** started with $0. LinkedIn-first outreach until first revenue funds email infrastructure.

## Key decisions made (and why)

- **Outreach account:** co-founder's personal LinkedIn only (his face = his sales calls). Walid's profile just lists Co-Founder role for due-diligence credibility. Company pages can't DM; company-named personal profiles violate LinkedIn ToS and convert worse.
- **Cold email deferred to ~day 45+:** 2026 Gmail/Yahoo rules are strict (SPF/DKIM/DMARC required, <0.1% complaints, permanent rejections). New domains need 4–6 weeks warmup starting 5–10 emails/day. Never send volume from main domain or personal Gmail. First revenue buys: 2–3 lookalike domains, 2–4 inboxes, Instantly ($37/mo) or Smartlead ($39/mo), then Apollo paid, then Clay ($149/mo).
- **Manual outreach volume limits:** LinkedIn ~100–150 connection requests/week max, ramp slowly on any account. 10–15 manual personalized emails/day from Gmail is safe.
- **Hooks are the strategy:** every message opens with a specific, recent, verifiable fact about the prospect (their post, live mandates, hiring). Signal-referencing messages get 15–25% replies vs 3.4% generic (2026 benchmarks). No hook = don't send.
- **Never scrape LinkedIn** (account death). Automation only via public ATS APIs + licensed data (Apollo/Clay).
- **No fake testimonials/logos ever** on the website; "founding clients" framing until real proof exists.

## The machine (block architecture)

| Block | Function | Status | Implementation |
|---|---|---|---|
| 0 | ICP config per client | ✅ | JSON in `configs/`, edited via `app.py` UI |
| 1 | Signal engine (detect hiring) | ✅ built, running | `signal_engine.py` — sweeps Greenhouse/Lever/Ashby/Workable public job APIs nightly, diffs, emits new-role signals |
| 2 | Enrichment (find the human) | ⚙️ built, needs API key + probe test | `enrichment_engine.py` — Apollo free-tier api_search; run `--probe COMPANY` first to verify response parsing |
| 3 | Per-client filter & scoring | ✅ built, tested | `filter_engine.py` + `configs/*.json` — 3 gates (exclusions/inclusions/locations) + weighted scoring |
| 4 | Copy generation | Partial | DM templates per client config; Gemini prompt for hook extraction from pasted profiles; human reviews every send |
| 5 | Delivery | Manual (LinkedIn) | Email automation waits for warmed domains (~day 45) |
| 6 | Conversations & booking | Human forever | Co-founder handles replies (<2h during US/EU hours) + calls |
| 7 | Tracking & reporting | ✅ | `leadgen-pipeline-tracker.xlsx` (Google Sheets) + inbox status in `app.py` |
| 8 | Own-client prospecting (find agencies to sell to) | ✅ built, run `--probe` first | `prospector_engine.py` — Apollo api_search for founders/MDs/owners at 2–20-person staffing agencies (US/UK/FR) → `prospects.csv`; dedupe cache `prospects_seen.json` |
| 9 | Agency mandate signals (hooks for OUR outreach) | ✅ built | `agency_discovery.py` (classify prospect boards → `agency_watchlist.json`, separate from client watchlist) + `prospect_signal_engine.py` (new posting on an agency board = "they just took a mandate" → `prospect_signals.csv`) |
| 10 | Prospect CRM (DM drafts + one-click mini-list door-opener) | ✅ | app **Prospects** page — stages new→contacted→replied→call_booked→client_won, same day-3/breakup timers, templates in `prospects_config.json`, status in `prospects_status.json` |

## File inventory

- `signal_engine.py` — Block 1+3. Run: `--baseline` first time, then nightly plain. State: `signals_state.json`. Output: `signals.csv` (columns: date, client, platform, company, title, location, score, url, hook).
- `filter_engine.py` — Block 3 logic. Schema documented in docstring.
- `configs/default.json` — own-prospecting profile (tech roles, intern exclusions, seniority weights).
- `enrichment_engine.py` — Block 2. Needs `.env` with `APOLLO_API_KEY=...`. Outputs `enriched_signals.csv` + cache `enriched_contacts.json`. Optional `company_domains.json` (token→domain) improves matching. **Untested against live API — run `--probe gitlab` first; if parsing fails, fix `search_company()` against the raw dump.**
- `app.py` — Streamlit CRM: `streamlit run app.py`. Tab 1 = client configs + live tester + dry-run. Tab 2 = daily inbox (reads enriched CSV if present, drafts DMs from per-client templates, copy button, mark-contacted tracked in `inbox_status.json`).
- `leadgen-pipeline-tracker.xlsx` — prospect pipeline (imported to Google Sheets). Dashboard formulas + ICP spec pinned.
- `site/` — landing page (index.html + assets). Brutalist/editorial style, acid lime #D8FF3E, Space Grotesk/Mono, hero video, motion layer. Host on GitHub Pages → plug URL into HubSpot. Contact email is a placeholder — update when real address exists.
- `ai-leadgen-launch-plan.md` — original day-1/30/60 plan.

## Positioning & copy (canonical versions)

**LinkedIn headline (co-founder):** Co-Founder @ Agentic AI Leads | I book qualified sales meetings for tech recruitment agencies — pay per showed meeting, not promises | AI/ML Engineer

**The DM that started it (Nishant, hot lead — posted seeking commission-based BD partner for US/India staffing):**
> Hi Nishant — saw your post looking for commission-based help with direct client acquisition. I'll be straight: I don't come with a rolodex of MSP contacts. What I do is different — I run signal-based outbound that finds companies actively hiring in your niche right now and books direct-client meetings with the decision-makers. Same engagement model you described: you pay per meeting that actually happens, first one free so you can judge the quality yourself. Worth a 15-minute call to see if it fits what you're building?

**Discovery call agenda (client onboarding):** what does a good client look like (industry/size/geo) → what roles do you fill best → what's a mandate worth → who's the decision-maker you need → what's worked/failed before → close: "I build a target list, you approve, I start conversations. Pay per showed meeting, first free."

**Delivery model for clients without our email infra:** we write, outreach goes through the client's own inbox/LinkedIn (their domain is warm, their name has credibility), or co-founder's LinkedIn opens and hands off.

## Current status (July 8, 2026)

- List of 50 hooked prospects built; DMs going out via co-founder's account.
- Nishant = hottest lead (his post literally requested our engagement model).
- Signal engine baselined on 13 boards (~1,700+ jobs tracked); watchlist growth is ongoing manual work (Google dorks: `site:boards.greenhouse.io "devops"` etc.).
- Website ready to host; HubSpot signup blocked on hosting it.
- No revenue yet. Next milestones: first discovery call → first free meeting delivered → first paid meeting → buy domains + start email warmup clock.

## Operating rules

1. Sends before systems: outreach hours are sacred; build the machine in the gaps.
2. Reply to every prospect reply within 2 hours (US/EU business hours).
3. Follow-up at day 3 and day 7 — most replies come from follow-ups.
4. One niche until $5k/mo.
5. Every claim on the website and in DMs must survive scrutiny — no inflated numbers, no fake proof.
6. Track everything in the sheet; the Dashboard reply-rate is the health metric (target ≥8%, hooks push 15–25%).
