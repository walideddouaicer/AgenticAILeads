"""Delivery-layer unit tests with mocked HTTP (no network, no sends).

Usage: python tests/test_delivery.py
"""
import csv
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# common needs a data dir before import (apply_replies test uses it)
_DATA = tempfile.mkdtemp(prefix="aal_delivery_")
os.environ["AAL_DATA_DIR"] = _DATA
# make sure vendor keys never leak in from the real environment
for _k in ("INSTANTLY_API_KEY", "SMARTLEAD_API_KEY", "APOLLO_API_KEY"):
    os.environ.pop(_k, None)

import delivery  # noqa: E402
from delivery import (ManualAdapter, InstantlyAdapter, SmartleadAdapter,  # noqa: E402
                      ApolloSequencesAdapter, map_lead, has_real_email,
                      instantly_csv, active_email_adapter)

failures = []


def check(name, fn):
    try:
        fn()
        print(f"  ok   {name}")
    except AssertionError as e:
        failures.append(name)
        print(f"  FAIL {name}: {e}")


LEAD = {"contact1_name": "Jane Doe", "contact1_email": "jane@acme.com",
        "company": "Acme", "title": "Senior DevOps Engineer",
        "hook": "saw you just opened a Senior DevOps Engineer role"}
LEAD_LOCKED = {"contact1_name": "Bob Lee",
               "contact1_email": "(locked — reveal in Apollo UI)",
               "company": "Beta", "title": "SRE", "hook": "h"}
LEAD_NOEMAIL = {"contact1_name": "", "contact1_email": "", "company": "Gamma",
                "title": "Dev", "hook": "h"}


class FakeResp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise delivery.requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


def keyfile(**kv):
    p = Path(tempfile.mkstemp(suffix=".env")[1])
    p.write_text("\n".join(f"{k}={v}" for k, v in kv.items()), encoding="utf-8")
    return str(p)


EMPTY_ENV = keyfile()  # no keys


# ---------------- field mapping & helpers ----------------

def t_map_lead():
    m = map_lead(LEAD)
    assert m["email"] == "jane@acme.com"
    assert m["first_name"] == "Jane" and m["last_name"] == "Doe"
    assert m["company"] == "Acme"
    assert m["custom"] == {"hook": LEAD["hook"], "title": "Senior DevOps Engineer"}


def t_has_real_email():
    assert has_real_email(LEAD)
    assert not has_real_email(LEAD_LOCKED)
    assert not has_real_email(LEAD_NOEMAIL)


def t_instantly_csv():
    out = instantly_csv([LEAD, LEAD_LOCKED, LEAD_NOEMAIL])
    rows = list(csv.DictReader(out.splitlines()))
    assert list(rows[0].keys()) == ["email", "first_name", "last_name",
                                    "company_name", "hook", "title"]
    assert len(rows) == 1                     # only the unlocked-email lead
    assert rows[0]["email"] == "jane@acme.com"
    assert rows[0]["hook"] == LEAD["hook"]


check("map_lead maps email/name/company/custom", t_map_lead)
check("has_real_email filters locked/empty", t_has_real_email)
check("instantly_csv: unlocked only, right columns", t_instantly_csv)


# ---------------- manual adapter ----------------

def t_manual():
    a = ManualAdapter()
    assert a.is_configured()
    with mock.patch("delivery.requests.request") as mreq:
        res = a.push_leads("c", [LEAD])
        assert res["pushed"] == 0
        assert a.sync_replies() == []
        mreq.assert_not_called()               # manual never hits the network


check("manual adapter: configured, push/sync no-op, no HTTP", t_manual)


# ---------------- not configured -> clean no-op ----------------

def t_not_configured():
    for cls in (InstantlyAdapter, SmartleadAdapter, ApolloSequencesAdapter):
        a = cls(env_file=EMPTY_ENV)
        assert not a.is_configured(), f"{cls.__name__} should be unconfigured"
        with mock.patch("delivery.requests.request") as mreq:
            res = a.push_leads("c", [LEAD])
            assert res["configured"] is False and res["pushed"] == 0
            assert a.sync_replies() == []
            mreq.assert_not_called()            # no key => no request at all


check("unconfigured adapters no-op without any HTTP", t_not_configured)


# ---------------- Instantly configured ----------------

def t_instantly_push():
    a = InstantlyAdapter(env_file=keyfile(INSTANTLY_API_KEY="secret"))
    assert a.is_configured()
    with mock.patch("delivery.requests.request",
                    return_value=FakeResp({"id": "lead_1"})) as mreq:
        res = a.push_leads("camp-uuid", [LEAD, LEAD_LOCKED])
    assert res["pushed"] == 1 and res["skipped"] == 1   # locked one skipped
    assert mreq.call_count == 1
    args, kwargs = mreq.call_args
    assert args[0] == "POST" and args[1].endswith("/leads")
    assert kwargs["headers"]["Authorization"] == "Bearer secret"
    body = kwargs["json"]
    assert body["campaign"] == "camp-uuid"
    assert body["email"] == "jane@acme.com"
    assert body["custom_variables"]["title"] == "Senior DevOps Engineer"


def t_instantly_sync():
    a = InstantlyAdapter(env_file=keyfile(INSTANTLY_API_KEY="secret"))
    payload = {"items": [{"lead_email": "Jane@Acme.com"},
                         {"from_address": "x@y.com"}, {"no_email": 1}]}
    with mock.patch("delivery.requests.request",
                    return_value=FakeResp(payload)) as mreq:
        emails = a.sync_replies("camp-uuid")
    assert mreq.call_args[0][0] == "GET"
    assert emails == ["jane@acme.com", "x@y.com"]      # lowercased, deduped


check("instantly push: right URL/auth/payload, locked skipped", t_instantly_push)
check("instantly sync: parses replier emails", t_instantly_sync)


# ---------------- Smartlead configured (api_key as query param) ----------------

def t_smartlead_push():
    a = SmartleadAdapter(env_file=keyfile(SMARTLEAD_API_KEY="sl_key"))
    with mock.patch("delivery.requests.request",
                    return_value=FakeResp({"ok": True})) as mreq:
        a.push_leads("123", [LEAD])
    args, kwargs = mreq.call_args
    assert args[0] == "POST" and args[1].endswith("/campaigns/123/leads")
    assert kwargs["params"]["api_key"] == "sl_key"     # auth via query param
    assert kwargs["json"]["lead_list"][0]["email"] == "jane@acme.com"
    assert kwargs["json"]["lead_list"][0]["custom_fields"]["hook"] == LEAD["hook"]


check("smartlead push: query-param auth + lead_list payload", t_smartlead_push)


# ---------------- Apollo sequences (two-step create + enroll) ----------------

def t_apollo_push():
    a = ApolloSequencesAdapter(env_file=keyfile(APOLLO_API_KEY="ak"))
    with mock.patch("delivery.requests.request",
                    return_value=FakeResp({"contact": {"id": "c99"}})) as mreq:
        res = a.push_leads("seq1", [LEAD])
    assert res["pushed"] == 1
    urls = [c.args[1] for c in mreq.call_args_list]
    assert any(u.endswith("/contacts") for u in urls)          # step 1 create
    assert any("add_contact_ids" in u for u in urls)           # step 2 enroll
    for c in mreq.call_args_list:
        assert c.kwargs["headers"]["X-Api-Key"] == "ak"


check("apollo push: create-contact then enroll, X-Api-Key auth", t_apollo_push)


# ---------------- active_email_adapter selection ----------------

def t_active_adapter():
    assert active_email_adapter(env_file=EMPTY_ENV) is None
    a = active_email_adapter(env_file=keyfile(SMARTLEAD_API_KEY="x"))
    assert a is not None and a.name == "smartlead"


check("active_email_adapter picks first configured / None", t_active_adapter)


# ---------------- common.apply_replies matches by email ----------------

def t_apply_replies():
    import common as C
    # 3 leads sharing/carrying emails via the contacts cache
    (Path(_DATA) / "enriched_contacts.json").write_text(json.dumps({
        "greenhouse:acme": [{"name": "Jane Doe", "title": "H",
                  "linkedin": "https://linkedin.com/in/j", "email": "jane@acme.com"}],
        "greenhouse:beta": [{"name": "Jo", "title": "H",
                  "linkedin": "https://linkedin.com/in/b", "email": "jane@acme.com"}],
        "greenhouse:gamma": [{"name": "Al", "title": "H",
                   "linkedin": "https://linkedin.com/in/a", "email": "other@x.com"}],
    }), encoding="utf-8")
    fields = ["date", "client", "platform", "company", "title", "location",
              "score", "url", "hook"]
    rows = [
        {"date": "2026-07-10", "client": "default", "platform": "greenhouse",
         "company": "Acme", "title": "T1", "location": "", "score": "",
         "url": "", "hook": "h"},          # new -> should become replied
        {"date": "2026-07-10", "client": "default", "platform": "greenhouse",
         "company": "Beta", "title": "T2", "location": "", "score": "",
         "url": "", "hook": "h"},          # won -> must stay won
        {"date": "2026-07-10", "client": "default", "platform": "greenhouse",
         "company": "Gamma", "title": "T3", "location": "", "score": "",
         "url": "", "hook": "h"},          # email not in replies -> stays new
    ]
    with (Path(_DATA) / "signals.csv").open("w", newline="",
                                            encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    k = [C.lead_key(r) for r in rows]
    (Path(_DATA) / "inbox_status.json").write_text(json.dumps({
        k[1]: {"status": "won", "on": "2026-07-11", "notes": ""},
    }), encoding="utf-8")

    class FakeAdapter:
        name = "fake"

        def sync_replies(self, campaign_id=None):
            return ["JANE@acme.com"]

    summary = C.apply_replies(FakeAdapter())
    assert summary["matched"] == 1, summary          # only the 'new' Acme lead
    saved = json.loads((Path(_DATA) / "inbox_status.json").read_text())
    assert saved[k[0]]["status"] == "replied"        # advanced
    assert saved[k[1]]["status"] == "won"            # NOT downgraded
    assert C.effective_status(saved.get(k[2])) == "new"  # unmatched untouched


check("apply_replies: matches by email, advances only new/contacted",
      t_apply_replies)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("ALL DELIVERY TESTS PASS")
