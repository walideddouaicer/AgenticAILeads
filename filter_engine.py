#!/usr/bin/env python3
"""
Agentic AI Leads — Phase 3: per-client Filter & Config Engine.

Each client = one JSON file in ./configs/. signal_engine.py routes every
new job through every active client config; survivors become scored,
client-tagged signals.

Client profile schema (all fields optional except client_id):
{
  "client_id":          "nishant",
  "label":              "Nishant — US/India IT staffing",
  "active":             true,
  "inclusion_keywords": ["java", "developer", ".net"],   # title must contain >=1 (empty = all pass)
  "exclusion_keywords": ["intern", "unpaid"],            # title must contain none
  "allowed_locations":  ["united states", "remote"],     # empty = anywhere
  "keyword_weights":    {"contract": 5, "senior": 2},    # bonus points if in title
  "min_score":          0                                # drop signals scoring below this
}

Scoring: 10 points per matched inclusion keyword + keyword_weights bonuses.
"""

import re
from pathlib import Path

from runtime import DATA_DIR
from storage import read_json


CONFIG_DIR = DATA_DIR / "configs"

DEFAULTS = {
    "label": "",
    "active": True,
    "inclusion_keywords": [],
    "exclusion_keywords": [],
    "allowed_locations": [],
    "keyword_weights": {},
    "min_score": 0,
}


def _norm(s) -> str:
    """lowercase, collapse whitespace — all matching happens on this."""
    return " ".join(str(s or "").lower().split())


def keyword_matches(text: str, keyword: str) -> bool:
    """Match a whole technical term without substring collisions.

    Punctuation is meaningful: `.net`, `c++`, `c#`, and `node.js` stay
    intact. Alphanumeric boundaries keep `java` from matching `javascript`.
    Spaces in phrases tolerate common title separators.
    """
    text, keyword = _norm(text), _norm(keyword)
    if not text or not keyword:
        return False
    parts = [re.escape(part) for part in keyword.split()]
    body = r"[\s/_-]+".join(parts)
    return bool(re.search(rf"(?<![a-z0-9]){body}(?![a-z0-9])", text))


def load_configs(config_dir: Path = CONFIG_DIR):
    """Return active client configs; damaged files stop unsafe routing."""
    configs = []
    if not config_dir.exists():
        return configs
    for p in sorted(config_dir.glob("*.json")):
        cfg = read_json(p, expected_type=dict)
        cfg.setdefault("client_id", p.stem)
        for k, v in DEFAULTS.items():
            cfg.setdefault(k, v if not isinstance(v, (list, dict)) else type(v)())
        if cfg["active"]:
            configs.append(cfg)
    return configs


def location_ok(job_location: str, allowed: list, title: str = "",
                workplace: str = "") -> bool:
    """True if the job's location satisfies the allowed list.

    Geographic permission and working arrangement are separate. A remote job
    with no published geography does not qualify for every country; it matches
    only when `remote` is explicitly allowed. Blank/ambiguous geography fails
    a non-empty geographic filter instead of silently becoming "anywhere".
    """
    if not allowed:
        return True
    loc, t, arrangement = (_norm(job_location), _norm(title),
                           _norm(workplace))
    for a in allowed:
        a = _norm(a)
        if not a:
            continue
        if a == "remote":
            if arrangement == "remote" or keyword_matches(loc, "remote") \
                    or keyword_matches(t, "remote"):
                return True
            continue
        if loc and keyword_matches(loc, a):
            return True
    return False


def evaluate_job(job: dict, cfg: dict):
    """Pass one job through one client's gates.

    Returns a copy of the job with 'lead_score' and 'client_id' appended,
    or None if any gate rejects it.
    """
    title = _norm(job.get("title", ""))
    if not title:
        return None

    # Gate 1 — exclusions
    for kw in cfg.get("exclusion_keywords", []):
        k = _norm(kw)
        if k and keyword_matches(title, k):
            return None

    # Gate 2 — inclusions (empty list = everything passes)
    inclusions = [_norm(k) for k in cfg.get("inclusion_keywords", []) if _norm(k)]
    matched = [k for k in inclusions if keyword_matches(title, k)]
    if inclusions and not matched:
        return None

    # Gate 3 — locations
    if not location_ok(job.get("location", ""), cfg.get("allowed_locations", []),
                       job.get("title", ""), job.get("workplace", "")):
        return None

    # Score
    score = 10 * len(matched)
    for kw, pts in (cfg.get("keyword_weights") or {}).items():
        k = _norm(kw)
        if k and keyword_matches(title, k):
            try:
                score += int(pts)
            except (TypeError, ValueError):
                pass

    try:
        if score < int(cfg.get("min_score", 0)):
            return None
    except (TypeError, ValueError):
        pass

    out = dict(job)
    out["lead_score"] = score
    out["client_id"] = cfg["client_id"]
    return out
