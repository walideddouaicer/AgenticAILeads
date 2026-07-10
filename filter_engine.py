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

import json
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent / "configs"

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


def load_configs(config_dir: Path = CONFIG_DIR):
    """Return list of active, validated client configs. Bad files are
    skipped with a warning, never crash the nightly sweep."""
    configs = []
    if not config_dir.exists():
        return configs
    for p in sorted(config_dir.glob("*.json")):
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [!] config {p.name}: unreadable, skipped ({e.__class__.__name__})")
            continue
        if not isinstance(cfg, dict):
            print(f"  [!] config {p.name}: not a JSON object, skipped")
            continue
        cfg.setdefault("client_id", p.stem)
        for k, v in DEFAULTS.items():
            cfg.setdefault(k, v if not isinstance(v, (list, dict)) else type(v)())
        if cfg["active"]:
            configs.append(cfg)
    return configs


def location_ok(job_location: str, allowed: list, title: str = "") -> bool:
    """True if the job's location satisfies the allowed list.

    Handles messy ATS strings: substring match both directions after
    normalization, so allowed "germany" matches "Remote, Germany" and
    allowed "berlin" matches "Berlin, DE" won't miss "Berlin".
    "remote" as an allowed location also matches remote-flagged titles.
    """
    if not allowed:
        return True
    loc, t = _norm(job_location), _norm(title)
    for a in allowed:
        a = _norm(a)
        if not a:
            continue
        if a in loc or (loc and loc in a):
            return True
        if a == "remote" and "remote" in t:
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
        if k and k in title:
            return None

    # Gate 2 — inclusions (empty list = everything passes)
    inclusions = [_norm(k) for k in cfg.get("inclusion_keywords", []) if _norm(k)]
    matched = [k for k in inclusions if k in title]
    if inclusions and not matched:
        return None

    # Gate 3 — locations
    if not location_ok(job.get("location", ""), cfg.get("allowed_locations", []),
                       job.get("title", "")):
        return None

    # Score
    score = 10 * len(matched)
    for kw, pts in (cfg.get("keyword_weights") or {}).items():
        k = _norm(kw)
        if k and k in title:
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
