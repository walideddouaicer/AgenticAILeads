#!/usr/bin/env python3
"""
Agentic AI Leads — Delivery layer (channel-ready email plumbing).

Lets an email-sending tool (Instantly, Smartlead, or Apollo email
sequences) be plugged in later by only adding an API key to `.env`. No
sending happens until a key is present AND a button is pressed — every
adapter reports "not configured" and no-ops cleanly when its key is
absent, so the default behaviour (draft in the app, a human sends on
LinkedIn) is unchanged.

Adapter interface:
    name                       str
    is_configured()            -> bool
    push_leads(campaign_id, leads) -> dict   (queues leads into a campaign)
    sync_replies(campaign_id=None) -> list[str]   (replier email addresses)

Implementations:
    ManualAdapter            default; always configured; push/sync no-op
    InstantlyAdapter         INSTANTLY_API_KEY   (api.instantly.ai v2)
    SmartleadAdapter         SMARTLEAD_API_KEY   (server.smartlead.ai v1)
    ApolloSequencesAdapter   APOLLO_API_KEY      (api.apollo.io emailer)

CLI:
    python delivery.py --probe                 # print the requests each
                                               # adapter WOULD send (no send)
    python delivery.py --probe --adapter instantly --campaign abc123
    python delivery.py --sync-replies          # poll replies, set stage
                                               # 'replied' on matched leads

Vendor payload/endpoint details vary and can change; fields we are not
100% sure of are marked `# TODO(verify)` and the --probe dry-run prints
the exact request so it can be checked against a live account before the
first real send (same idea as enrichment_engine.py's --probe).

This module is intentionally free of any Streamlit / common import so it
stays usable from the CLI and unit-testable with mocked HTTP.
"""

import argparse
import csv
import io
import os
import sys
from pathlib import Path

import requests

from tls_compat import enable_os_trust_store

HERE = Path(__file__).resolve().parent
TIMEOUT = 20

INSTANTLY_BASE = "https://api.instantly.ai/api/v2"
SMARTLEAD_BASE = "https://server.smartlead.ai/api/v1"
APOLLO_BASE = "https://api.apollo.io/api/v1"


# ----------------------------- helpers ---------------------------------

def load_env_key(name, env_file=None):
    """Read one key from the environment, falling back to an .env file.
    Returns '' when unset — callers treat that as 'adapter not configured'.
    Never raises (unlike enrichment_engine.load_api_key which exits)."""
    v = os.environ.get(name)
    if v and v.strip():
        return v.strip()
    path = Path(env_file) if env_file else (HERE / ".env")
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8",
                                       errors="replace").splitlines():
                line = line.strip()
                if line.startswith(name) and "=" in line:
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
        except OSError:
            pass
    return ""


def has_real_email(lead) -> bool:
    """True only for a usable, unlocked email address."""
    e = (lead.get("contact1_email") or "").strip()
    return "@" in e and "locked" not in e.lower()


def map_lead(lead) -> dict:
    """THE single field mapping every adapter and the CSV export share.

    Produces the canonical outbound shape: email, first/last name,
    company, and the custom variables {hook} and {title} so our existing
    template placeholders resolve server-side in the email tool.
    """
    name = (lead.get("contact1_name") or "").strip()
    parts = name.split()
    return {
        "email": (lead.get("contact1_email") or "").strip(),
        "first_name": parts[0] if parts else "",
        "last_name": " ".join(parts[1:]) if len(parts) > 1 else "",
        "company": lead.get("company", "") or "",
        "custom": {
            "hook": lead.get("hook", "") or "",
            "title": lead.get("title", "") or "",
        },
    }


def instantly_csv(leads) -> str:
    """Instantly lead-import CSV — works today with zero API access.
    Only leads with a real unlocked email are included. Columns map 1:1
    to Instantly's importer (custom columns 'hook' and 'title' become
    custom variables during import)."""
    cols = ["email", "first_name", "last_name", "company_name", "hook", "title"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    for lead in leads:
        if not has_real_email(lead):
            continue
        m = map_lead(lead)
        w.writerow({
            "email": m["email"], "first_name": m["first_name"],
            "last_name": m["last_name"], "company_name": m["company"],
            "hook": m["custom"]["hook"], "title": m["custom"]["title"],
        })
    return buf.getvalue()


def _redact(d):
    if not d:
        return d
    out = {}
    for k, v in d.items():
        out[k] = "***" if k.lower() in ("authorization", "x-api-key",
                                        "api_key") else v
    return out


# ----------------------------- adapters --------------------------------

class DeliveryAdapter:
    """Base adapter. Subclasses provide _headers/_auth_params and the
    request builders (_push_request/_reply_request) + _parse_replies;
    push_leads/sync_replies are generic here so behaviour stays uniform."""

    name = "base"
    key_env = None            # env var holding the API key (None = keyless)
    docs_url = ""
    setup_hint = ""

    def __init__(self, env_file=None, dry_run=False):
        self.env_file = env_file
        self.dry_run = dry_run

    # --- configuration ---
    def api_key(self) -> str:
        return load_env_key(self.key_env, self.env_file) if self.key_env else ""

    def is_configured(self) -> bool:
        return bool(self.api_key())

    # --- per-vendor auth (override) ---
    def _headers(self) -> dict:
        return {"Content-Type": "application/json"}

    def _auth_params(self) -> dict:
        return {}

    # --- transport (shared; honours dry_run) ---
    def _send(self, method, url, json=None, params=None):
        params = {**self._auth_params(), **(params or {})}
        headers = self._headers()
        if self.dry_run:
            return {"dry_run": True, "method": method, "url": url,
                    "headers": _redact(headers), "params": _redact(params),
                    "json": json}
        enable_os_trust_store()
        r = requests.request(method, url, headers=headers, json=json,
                             params=params or None, timeout=TIMEOUT)
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            return {"status_code": r.status_code, "text": r.text[:500]}

    def _emailable(self, leads):
        return [l for l in leads if has_real_email(l)]

    # --- request builders (override) ---
    def _push_request(self, campaign_id, m):
        """-> (method, url, json_body) for pushing ONE mapped lead."""
        raise NotImplementedError

    def _reply_request(self, campaign_id):
        """-> (method, url, params) for polling replies."""
        raise NotImplementedError

    def _parse_replies(self, data) -> list:
        raise NotImplementedError

    # --- public interface ---
    def push_leads(self, campaign_id, leads) -> dict:
        if not self.is_configured():
            return {"adapter": self.name, "configured": False, "pushed": 0,
                    "skipped": len(leads),
                    "note": f"{self.key_env} not set — no-op "
                            "(manual channel unaffected)."}
        emailable = self._emailable(leads)
        results, requests_sent = [], []
        for lead in emailable:
            method, url, body = self._push_request(campaign_id, map_lead(lead))
            requests_sent.append({"method": method, "url": url, "json": body})
            results.append(self._send(method, url, json=body))
        return {"adapter": self.name, "configured": True,
                "pushed": len(results), "skipped": len(leads) - len(emailable),
                "requests": requests_sent, "results": results}

    def sync_replies(self, campaign_id=None) -> list:
        if not self.is_configured():
            return []
        method, url, params = self._reply_request(campaign_id)
        data = self._send(method, url, params=params)
        if self.dry_run:
            return []
        return self._parse_replies(data)

    # --- probe (build requests without sending; needs no key) ---
    def describe_requests(self, campaign_id, sample):
        out = {"adapter": self.name, "configured": self.is_configured()}
        try:
            method, url, body = self._push_request(campaign_id, map_lead(sample))
            out["push"] = {"method": method, "url": url,
                           "headers": _redact(self._headers()),
                           "params": _redact(self._auth_params()), "json": body}
        except NotImplementedError:
            out["push"] = None
        try:
            method, url, params = self._reply_request(campaign_id)
            out["sync"] = {"method": method, "url": url,
                           "headers": _redact(self._headers()),
                           "params": _redact({**self._auth_params(),
                                              **(params or {})})}
        except NotImplementedError:
            out["sync"] = None
        return out


class ManualAdapter(DeliveryAdapter):
    """Default channel: the app drafts, a human sends on LinkedIn. Always
    'configured' (needs no key); push and sync are deliberate no-ops."""

    name = "manual"
    key_env = None
    setup_hint = ("No key needed. Drafts are copied from the queue/inbox and "
                  "sent by hand on LinkedIn — the default, always-on channel.")

    def is_configured(self) -> bool:
        return True

    def push_leads(self, campaign_id, leads) -> dict:
        return {"adapter": self.name, "configured": True, "pushed": 0,
                "skipped": len(leads),
                "note": "Manual channel — nothing to push; send from the "
                        "queue by hand."}

    def sync_replies(self, campaign_id=None) -> list:
        return []


class InstantlyAdapter(DeliveryAdapter):
    """Instantly.ai v2 API (Bearer auth)."""

    name = "instantly"
    key_env = "INSTANTLY_API_KEY"
    docs_url = "https://developer.instantly.ai/"
    setup_hint = ("Instantly → Settings → Integrations → API key (v2). Put "
                  "INSTANTLY_API_KEY in .env, then set each email client's "
                  "campaign_id to the campaign UUID.")

    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key()}",
                "Content-Type": "application/json"}

    def _push_request(self, campaign_id, m):
        body = {
            "campaign": campaign_id,          # TODO(verify): v2 create-lead uses 'campaign' (UUID)
            "email": m["email"],
            "first_name": m["first_name"],
            "last_name": m["last_name"],
            "company_name": m["company"],
            "custom_variables": m["custom"],  # TODO(verify): custom-variable object shape
        }
        return "POST", f"{INSTANTLY_BASE}/leads", body

    def _reply_request(self, campaign_id):
        params = {"event_type": "reply"}      # TODO(verify): reply filter param
        if campaign_id:
            params["campaign"] = campaign_id
        return "GET", f"{INSTANTLY_BASE}/emails", params

    def _parse_replies(self, data):
        items = (data.get("items") or data.get("data") or []) \
            if isinstance(data, dict) else []
        emails = set()
        for it in items:
            if not isinstance(it, dict):
                continue
            # TODO(verify): which field carries the replying lead's address
            em = (it.get("lead_email") or it.get("from_address")
                  or it.get("from") or "")
            if em and "@" in em:
                emails.add(em.strip().lower())
        return sorted(emails)


class SmartleadAdapter(DeliveryAdapter):
    """Smartlead.ai v1 API (api_key query parameter)."""

    name = "smartlead"
    key_env = "SMARTLEAD_API_KEY"
    docs_url = "https://api.smartlead.ai/reference/"
    setup_hint = ("Smartlead → Settings → API key. Put SMARTLEAD_API_KEY in "
                  ".env, then set each email client's campaign_id to the "
                  "numeric campaign id.")

    def _auth_params(self):
        return {"api_key": self.api_key()}

    def _push_request(self, campaign_id, m):
        # Smartlead adds leads in a batch; we send one lead per call so a
        # single bad row can't reject the batch.
        body = {"lead_list": [{
            "email": m["email"],
            "first_name": m["first_name"],
            "last_name": m["last_name"],
            "company_name": m["company"],
            "custom_fields": m["custom"],     # TODO(verify): custom_fields vs custom_variables
        }]}
        return "POST", f"{SMARTLEAD_BASE}/campaigns/{campaign_id}/leads", body

    def _reply_request(self, campaign_id):
        # TODO(verify): reply polling endpoint — message history per campaign
        return ("GET",
                f"{SMARTLEAD_BASE}/campaigns/{campaign_id}/leads-message-history",
                {})

    def _parse_replies(self, data):
        emails = set()
        rows = data if isinstance(data, list) else (
            data.get("history") or data.get("data") or []) \
            if isinstance(data, dict) else []
        for r in rows:
            if not isinstance(r, dict):
                continue
            # TODO(verify): reply event marker + lead-email field
            etype = str(r.get("type") or r.get("event_type") or "").lower()
            if "reply" in etype or r.get("is_reply"):
                em = r.get("lead_email") or r.get("email") or ""
                if em and "@" in em:
                    emails.add(em.strip().lower())
        return sorted(emails)


class ApolloSequencesAdapter(DeliveryAdapter):
    """Apollo.io email sequences (emailer campaigns), X-Api-Key auth.

    NOTE: Apollo sequences act on contacts that already exist in Apollo,
    so push is a two-step create-then-enroll. Both steps are heavily
    TODO-flagged — run --probe against a real account before first use.
    """

    name = "apollo_sequences"
    key_env = "APOLLO_API_KEY"
    docs_url = "https://docs.apollo.io/"
    setup_hint = ("Uses the same APOLLO_API_KEY as enrichment (master key). "
                  "Set each email client's campaign_id to the Apollo "
                  "emailer_campaign id. Sending requires a verified mailbox "
                  "in Apollo.")

    def _headers(self):
        return {"X-Api-Key": self.api_key(),
                "Content-Type": "application/json",
                "Cache-Control": "no-cache"}

    def _push_request(self, campaign_id, m):
        # Step shown for --probe/dry-run: enrolling a contact by id.
        # TODO(verify): real flow is POST /contacts (create) -> capture id ->
        # POST /emailer_campaigns/{id}/add_contact_ids with
        # {contact_ids, emailer_campaign_id, send_email_from_email_account_id}.
        body = {
            "emailer_campaign_id": campaign_id,
            "contact_ids": [],                # TODO(verify): filled from create-contact step
            "email": m["email"],              # create-contact fields:
            "first_name": m["first_name"],
            "last_name": m["last_name"],
            "organization_name": m["company"],
            # TODO(verify): send_email_from_email_account_id is required by Apollo
        }
        return ("POST",
                f"{APOLLO_BASE}/emailer_campaigns/{campaign_id}/add_contact_ids",
                body)

    def push_leads(self, campaign_id, leads) -> dict:
        # Override to make the two-step nature explicit while reusing _send.
        if not self.is_configured():
            return {"adapter": self.name, "configured": False, "pushed": 0,
                    "skipped": len(leads),
                    "note": f"{self.key_env} not set — no-op."}
        emailable = self._emailable(leads)
        results, reqs = [], []
        for lead in emailable:
            m = map_lead(lead)
            # 1) create/find the contact in Apollo
            create_body = {"email": m["email"], "first_name": m["first_name"],
                           "last_name": m["last_name"],
                           "organization_name": m["company"]}
            reqs.append({"method": "POST", "url": f"{APOLLO_BASE}/contacts",
                         "json": create_body})
            created = self._send("POST", f"{APOLLO_BASE}/contacts",
                                 json=create_body)
            cid = ""
            if isinstance(created, dict):
                cid = ((created.get("contact") or {}).get("id")
                       or created.get("id") or "")
            # 2) enroll the contact id into the sequence
            enroll_body = {
                "emailer_campaign_id": campaign_id,
                "contact_ids": [cid] if cid else [],
                # TODO(verify): send_email_from_email_account_id required
            }
            url = f"{APOLLO_BASE}/emailer_campaigns/{campaign_id}/add_contact_ids"
            reqs.append({"method": "POST", "url": url, "json": enroll_body})
            results.append(self._send("POST", url, json=enroll_body))
        return {"adapter": self.name, "configured": True,
                "pushed": len(results), "skipped": len(leads) - len(emailable),
                "requests": reqs, "results": results}

    def _reply_request(self, campaign_id):
        # TODO(verify): reply activity endpoint for emailer campaigns
        params = {}
        if campaign_id:
            params["emailer_campaign_id"] = campaign_id
        return "POST", f"{APOLLO_BASE}/emailer_messages/search", params

    def _parse_replies(self, data):
        msgs = (data.get("emailer_messages") or data.get("messages") or []) \
            if isinstance(data, dict) else []
        emails = set()
        for msg in msgs:
            if not isinstance(msg, dict):
                continue
            # TODO(verify): reply marker + replier address field
            if str(msg.get("type") or "").lower() == "reply" \
                    or msg.get("replied_at"):
                em = msg.get("from_email") or msg.get("email") or ""
                if em and "@" in em:
                    emails.add(em.strip().lower())
        return sorted(emails)


# ---- registry ----

ADAPTER_CLASSES = [ManualAdapter, InstantlyAdapter, SmartleadAdapter,
                   ApolloSequencesAdapter]
ADAPTER_BY_NAME = {c.name: c for c in ADAPTER_CLASSES}


def all_adapters(env_file=None, dry_run=False):
    return [c(env_file=env_file, dry_run=dry_run) for c in ADAPTER_CLASSES]


def get_adapter(name, env_file=None, dry_run=False):
    cls = ADAPTER_BY_NAME.get(name, ManualAdapter)
    return cls(env_file=env_file, dry_run=dry_run)


def active_email_adapter(env_file=None):
    """First configured email adapter (Instantly → Smartlead → Apollo), or
    None. Manual is excluded — it's the fallback, not an email channel."""
    for cls in (InstantlyAdapter, SmartleadAdapter, ApolloSequencesAdapter):
        a = cls(env_file=env_file)
        if a.is_configured():
            return a
    return None


SAMPLE_LEAD = {
    "contact1_name": "Jane Doe", "contact1_email": "jane@example.com",
    "company": "Acme", "title": "Senior DevOps Engineer",
    "hook": "saw you just opened a Senior DevOps Engineer role",
}


# --------------------------------- CLI ---------------------------------

def _probe(adapter_name, campaign_id):
    import json
    adapters = ([get_adapter(adapter_name)] if adapter_name
                else all_adapters())
    cid = campaign_id or "CAMPAIGN_ID"
    for a in adapters:
        print(f"\n=== {a.name} "
              f"({'configured' if a.is_configured() else 'NOT configured'}) ===")
        print(json.dumps(a.describe_requests(cid, SAMPLE_LEAD), indent=2))
    print("\n(no requests were sent — this is a dry run. Verify the payloads "
          "above against the vendor docs / a live account before the first "
          "real send. Fields marked TODO(verify) in delivery.py are the "
          "uncertain ones.)")


def _sync_replies(adapter_name, campaign_id):
    # Lazily import common so the module itself stays Streamlit-free.
    import common as C
    env_file = C.ENV_FILE
    adapter = (get_adapter(adapter_name, env_file=env_file) if adapter_name
               else active_email_adapter(env_file))
    if adapter is None or not adapter.is_configured():
        print("No configured email adapter (set INSTANTLY_API_KEY / "
              "SMARTLEAD_API_KEY / APOLLO_API_KEY in .env). Nothing to sync.")
        return
    summary = C.apply_replies(adapter, campaign_id)
    if summary.get("error"):
        print(f"Sync failed: {summary['error']}")
        return
    print(f"{adapter.name}: {summary.get('emails', 0)} replier email(s) "
          f"found, {summary.get('matched', 0)} lead(s) moved to 'replied'.")


def main():
    ap = argparse.ArgumentParser(description="Delivery layer for email tools.")
    ap.add_argument("--probe", action="store_true",
                    help="print the requests each adapter WOULD send (no send)")
    ap.add_argument("--sync-replies", action="store_true",
                    help="poll replies and set stage 'replied' on matched leads")
    ap.add_argument("--adapter", choices=list(ADAPTER_BY_NAME),
                    help="limit to one adapter (default: all / first configured)")
    ap.add_argument("--campaign", default="",
                    help="campaign id to probe/sync against")
    args = ap.parse_args()

    if args.probe:
        _probe(args.adapter, args.campaign)
    elif args.sync_replies:
        _sync_replies(args.adapter, args.campaign)
    else:
        ap.error("nothing to do — pass --probe or --sync-replies")


if __name__ == "__main__":
    sys.exit(main())
