"""Import Clay-sourced prospects into prospects.csv.

One-off-ish importer for CSVs produced by the Clay CLI runs. Mirrors
prospector_engine exactly: same FIELDNAMES, same seen_key (LinkedIn URL,
falling back to normalized agency+name), same append-only contract —
existing rows and your edits to them are never touched.

Clay exports may carry extra columns (size_bucket, niche_quality, domain,
...). Those are dropped on import; prospects.csv's shape is frozen.

Usage:
    python import_clay.py --dry-run "Clay setup/prospects_all.csv"
    python import_clay.py "Clay setup/prospects_all.csv" "Clay setup/prospects_top50.csv"
    python import_clay.py --require-email "Clay setup/prospects_all.csv"

Always --dry-run first: it prints exactly what would be appended and what
would be skipped, and writes nothing.

Rows are rejected (not imported) when they'd poison outreach:
  - no agency name
  - no person_name, or a truncated one ("James K", "Rhys") — we personalise
    on first name, so a broken name is worse than no row
  - neither a LinkedIn URL nor an email (nothing to contact them on)

Rows WITHOUT an email are kept by default: the LinkedIn URL is enough to
work them from the Prospects page, which is DM-first anyway. Pass
--require-email to import only contactable-by-email rows.
"""

import argparse
import csv
import re
import sys
from datetime import date
from pathlib import Path

from storage import append_csv_unique, read_json, update_json
from runtime import DATA_DIR

PROSPECTS_CSV = DATA_DIR / "prospects.csv"
SEEN_FILE = DATA_DIR / "prospects_seen.json"

# Frozen — must stay identical to prospector_engine.FIELDNAMES.
FIELDNAMES = ["date_found", "agency", "person_name", "person_title",
              "linkedin", "email", "location", "size", "source",
              "niche_keywords"]

SOURCE = "clay"

# "James K", "James K.", "Rhys" -> truncated/one-token, unusable for
# first-name personalisation.
_TRUNCATED = re.compile(r"^\s*\S+(\s+[A-Za-z]\.?)?\s*$")


def seen_key(person: dict) -> str:
    """Dedupe key: LinkedIn URL, else normalized agency+name.

    Byte-for-byte the same rule as prospector_engine.seen_key — an
    Apollo-sourced row and a Clay-sourced row for the same person must
    collide, or the same human lands in the CSV twice.
    """
    lk = (person.get("linkedin") or "").strip().lower().rstrip("/")
    if lk:
        return lk
    return (f"{(person.get('agency') or '').strip().lower()}"
            f"|{(person.get('person_name') or '').strip().lower()}")


def load_seen():
    return read_json(SEEN_FILE, default={}, expected_type=dict)


def clean(value) -> str:
    return (value or "").strip()


def normalize_row(raw: dict, today: str) -> dict:
    """Clay export row -> a prospects.csv row. Extra columns are dropped."""
    row = {k: clean(raw.get(k)) for k in FIELDNAMES}
    row["date_found"] = row["date_found"] or today
    row["source"] = SOURCE          # never trust the file's own source column
    row["linkedin"] = row["linkedin"].rstrip("/")
    return row


def reject_reason(row: dict, require_email: bool) -> str | None:
    """None if the row is importable, else why it isn't."""
    if not row["agency"]:
        return "no agency name"
    name = row["person_name"]
    if not name:
        return "no person_name"
    if _TRUNCATED.match(name):
        return f"truncated person_name ({name!r})"
    if require_email and not row["email"]:
        return "no email (--require-email)"
    if not row["email"] and not row["linkedin"]:
        return "no email and no linkedin — uncontactable"
    return None


def read_export(path: Path, today: str):
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in ("agency", "person_name") if c not in
                   (reader.fieldnames or [])]
        if missing:
            sys.exit(f"{path.name}: missing required column(s): "
                     f"{', '.join(missing)}. Got: {reader.fieldnames}")
        return [normalize_row(r, today) for r in reader]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", help="Clay export CSV(s)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report only; write nothing")
    ap.add_argument("--require-email", action="store_true",
                    help="skip rows with no email (default: keep them, "
                         "LinkedIn is enough to work them)")
    args = ap.parse_args()

    today = date.today().isoformat()
    seen = load_seen()
    before = len(seen)
    added_now = set()          # keys added by THIS run, for honest counting

    new_rows = []
    dup_in_file = 0
    dup_known = 0
    rejected = {}

    for name in args.files:
        path = Path(name)
        if not path.exists():
            sys.exit(f"No such file: {path}")
        rows = read_export(path, today)
        kept = 0
        for row in rows:
            why = reject_reason(row, args.require_email)
            if why:
                rejected[why] = rejected.get(why, 0) + 1
                continue
            key = seen_key(row)
            if key in added_now:
                # same person twice across the file(s) in this batch —
                # e.g. prospects_all.csv and prospects_top50.csv overlap
                dup_in_file += 1
                continue
            if key in seen:
                dup_known += 1
                continue
            seen[key] = today
            added_now.add(key)
            new_rows.append(row)
            kept += 1
        print(f"{path.name}: {len(rows)} row(s) read, {kept} new")

    print(f"\n  new           {len(new_rows)}")
    print(f"  already known {dup_known}")
    print(f"  dup in batch  {dup_in_file}")
    if rejected:
        print("  rejected:")
        for why, n in sorted(rejected.items(), key=lambda kv: -kv[1]):
            print(f"    {n:>5}  {why}")
    no_email = sum(1 for r in new_rows if not r["email"])
    if no_email:
        print(f"\n  {no_email} of the new rows have no email — LinkedIn only.")

    if args.dry_run:
        print("\nDry run — nothing written. Re-run without --dry-run to apply.")
        if new_rows:
            print("\nFirst 5 rows that would be appended:")
            for r in new_rows[:5]:
                print(f"  {r['agency']} | {r['person_name']} | "
                      f"{r['person_title']} | {r['email'] or '(no email)'}")
        return

    if not new_rows:
        print("\nNothing to import.")
        return

    # Existing rows and manual edits are preserved; a retry after a crash
    # deduplicates by the same identity used by prospects_seen.json.
    added = append_csv_unique(
        PROSPECTS_CSV, new_rows, FIELDNAMES, key=seen_key)
    discovered = {seen_key(row): today for row in new_rows}
    update_json(SEEN_FILE, lambda latest: latest.update(discovered),
                default={}, expected_type=dict, indent=1)

    latest_seen = load_seen()
    print(f"\n{added} prospect(s) appended to {PROSPECTS_CSV.name}; "
          f"{SEEN_FILE.name} now holds {len(latest_seen)} key(s) (was {before}).")
    print("Next: open the CRM's Prospects page — the new rows arrive at "
          "stage 'new' and enter the follow-up rotation once contacted.")


if __name__ == "__main__":
    main()
