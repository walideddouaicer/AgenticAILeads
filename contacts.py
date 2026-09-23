#!/usr/bin/env python3
"""
Agentic AI Leads — contact provider layer.

One adapter per optional contact lookup vendor.
Deliberately **Streamlit-free and common-free**: pure, CLI- and unit-
testable. `enrichment_engine.py` and the CRM may import this; this imports
neither of them.

WHY THIS EXISTS
---------------
`enrichment_engine.py` was hardcoded to Apollo — the URL, the payload
shape, the error strings. When Apollo's free plan started 403ing the
search endpoint, there was no way to point the same pipeline at a
different vendor without a rewrite. Contact data is the one thing in this
system we must *buy* rather than generate, so the vendor behind it should
be a config change, not a refactor.

The house rule: **buy data you can't generate, generate data you can.**
Job postings we generate (signal_engine). Verified work emails we can't.

ADDING A PROVIDER
-----------------
Subclass ContactProvider, set `name` / `key_env`, implement `_search()`
returning the raw response dict, and add it to PROVIDERS. `parse_people()`
is deliberately shared and defensive — most vendors return some variant of
{people: [...]} with names split or joined, so start there before writing
a bespoke parser.

Every provider no-ops when its key is absent, so the nightly chain runs
harmlessly with nothing configured. Nothing is ever queried without a key.
"""

import json
import os
from pathlib import Path

import requests

from runtime import ENV_FILE
from tls_compat import enable_os_trust_store

enable_os_trust_store()

TIMEOUT = 20
CONTACTS_PER_COMPANY = 2

# Decision-maker titles for "this company is hiring" signals: the people who
# own hiring and would buy a recruitment agency's services.
TARGET_TITLES = [
    "talent acquisition", "technical recruiter", "recruiting manager",
    "head of talent", "head of people", "head of hr", "hr director",
    "people operations", "founder", "co-founder",
]

RATE_LIMITED = "RATE_LIMITED"


def load_env_key(name: str, env_file=None) -> str:
    """Read a process key, falling back to the configured .env file."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    env_file = Path(env_file) if env_file else None
    if env_file and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == name:
                return v.strip().strip('"').strip("'")
    return ""


def parse_people(data: dict, limit: int = CONTACTS_PER_COMPANY):
    """Defensive parse of one response page -> [{name,title,linkedin,email}].

    Mirrors prospector_engine: people may arrive under 'people' and/or
    'contacts'; names may be split or joined; emails are often locked
    behind a credit on free tiers.
    """
    raw_people = ((data or {}).get("people") or []) + \
                 ((data or {}).get("contacts") or [])
    out = []
    for p in raw_people:
        if not isinstance(p, dict):
            continue
        name = p.get("name") or " ".join(
            x for x in [p.get("first_name"), p.get("last_name")] if x)
        email = p.get("email") or ""
        if "not_unlocked" in email:
            email = "(locked — reveal in Apollo UI costs 1 credit)"
        out.append({
            "name": name,
            "title": p.get("title") or "",
            "linkedin": p.get("linkedin_url") or "",
            "email": email,
        })
        if len(out) >= limit:
            break
    return out


class ContactProvider:
    """Base class. Subclasses implement _search()."""

    name = "base"
    key_env = ""
    docs_url = ""
    setup_hint = ""

    def __init__(self, env_file=None, dry_run: bool = False):
        self.env_file = env_file
        self.dry_run = dry_run

    def api_key(self) -> str:
        return load_env_key(self.key_env, self.env_file) if self.key_env else ""

    def is_configured(self) -> bool:
        return bool(self.api_key())

    def _search(self, company: str, domain=None):
        raise NotImplementedError

    def find_contacts(self, company: str, domain=None):
        """(contacts, raw). Returns ([], None) when unconfigured — never
        raises for a missing key, so the nightly chain stays quiet."""
        if not self.is_configured():
            return [], None
        raw = self._search(company, domain)
        if raw == RATE_LIMITED:
            return RATE_LIMITED, None
        return parse_people(raw), raw


class ApolloProvider(ContactProvider):
    """Apollo.io people search.

    NOTE: this endpoint is NOT on Apollo's free plan — it returns 403 with
    API_INACCESSIBLE regardless of whether the key is master-scoped. Paid
    plans (Basic and up) include API access.
    """

    name = "apollo"
    key_env = "APOLLO_API_KEY"
    docs_url = "https://docs.apollo.io/reference/people-api-search"
    setup_hint = (
        "Apollo -> Settings -> Integrations -> API -> create a key with "
        "'master API key' enabled, then put APOLLO_API_KEY=... in .env.\n"
        "If you get 403 API_INACCESSIBLE, the endpoint is not on your plan "
        "— that is a plan limit, not a key problem. Free tier cannot use "
        "it at all; fall back to manual Apollo UI searches (the CRM's "
        "contact editor writes the same cache) until a paid plan or trial "
        "is active.")

    url = "https://api.apollo.io/api/v1/mixed_people/api_search"

    def _search(self, company: str, domain=None):
        payload = {"person_titles": TARGET_TITLES, "page": 1, "per_page": 10}
        if domain:
            payload["q_organization_domains_list"] = [domain]
        else:
            payload["q_organization_name"] = company

        if self.dry_run:
            return {"_dry_run": True, "url": self.url, "payload": payload}

        r = requests.post(
            self.url, json=payload, timeout=TIMEOUT,
            headers={"X-Api-Key": self.api_key(),
                     "Content-Type": "application/json",
                     "Cache-Control": "no-cache"})
        if r.status_code == 403:
            raise ProviderForbidden(
                f"403 from {self.name}. Raw response:\n{r.text[:800]}\n\n"
                + self.setup_hint)
        if r.status_code == 429:
            return RATE_LIMITED
        r.raise_for_status()
        return r.json()


class ProviderForbidden(RuntimeError):
    """403 — plan or key problem. Carries the provider's setup hint."""


# Order matters: the first configured provider wins in active_provider().
PROVIDERS = [ApolloProvider]


def all_providers(env_file=None, dry_run=False):
    return [cls(env_file=env_file, dry_run=dry_run) for cls in PROVIDERS]


def get_provider(name, env_file=None, dry_run=False):
    for p in all_providers(env_file, dry_run):
        if p.name == name:
            return p
    raise KeyError(f"unknown provider {name!r}; "
                   f"have {[c.name for c in PROVIDERS]}")


def active_provider(env_file=None, dry_run=False):
    """First provider with a key, or None."""
    for p in all_providers(env_file, dry_run):
        if p.is_configured():
            return p
    return None


if __name__ == "__main__":
    import sys
    print("Contact providers:")
    for p in all_providers(env_file=ENV_FILE):
        state = "configured" if p.is_configured() else f"no {p.key_env}"
        print(f"  {p.name:<10} {state}")
    company = sys.argv[1] if len(sys.argv) > 1 else None
    if company:
        prov = active_provider(env_file=ENV_FILE)
        if not prov:
            sys.exit("No provider configured — set a key in .env")
        contacts, raw = prov.find_contacts(company)
        print(json.dumps(contacts, indent=2))
