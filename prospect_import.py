"""Small, offline CSV/manual import for agency prospects."""

import csv
from datetime import date
import io

import core
from storage import append_csv_unique


COLUMNS = ["date_found", "agency", "person_name", "person_title",
           "linkedin", "email", "location", "size", "source",
           "niche_keywords"]
REQUIRED = {"agency", "person_name"}


def identity(row):
    linkedin = (row.get("linkedin") or "").strip().casefold().rstrip("/")
    return linkedin or (f"{(row.get('agency') or '').strip().casefold()}|"
                        f"{(row.get('person_name') or '').strip().casefold()}")


def preview_rows(incoming, existing=()):
    """Return clean rows and row-numbered rejection reasons without writing."""
    seen = {identity(row) for row in existing}
    accepted, rejected = [], []
    for number, raw in enumerate(incoming, start=2):
        if not isinstance(raw, dict) or None in raw:
            rejected.append({"row": number, "reason": "Malformed CSV row"})
            continue
        row = {field: str(raw.get(field) or "").strip() for field in COLUMNS}
        if not row["agency"] or not row["person_name"]:
            rejected.append({"row": number,
                             "reason": "Agency and person name are required"})
            continue
        key = identity(row)
        if key in seen:
            rejected.append({"row": number,
                             "reason": "Duplicate person or LinkedIn URL"})
            continue
        seen.add(key)
        row["date_found"] = row["date_found"] or date.today().isoformat()
        row["source"] = row["source"] or "manual import"
        accepted.append(row)
    return accepted, rejected


def preview_csv(content, existing=()):
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("CSV must be UTF-8 encoded") from error
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""))
        headers = set(reader.fieldnames or [])
        if not REQUIRED.issubset(headers):
            raise ValueError("CSV needs agency and person_name columns")
        return preview_rows(list(reader), existing)
    except csv.Error as error:
        raise ValueError("CSV could not be parsed") from error


def import_rows(rows):
    """Append without replacing existing or manually corrected records."""
    return append_csv_unique(core.PROSPECTS_CSV, rows, COLUMNS, key=identity)
