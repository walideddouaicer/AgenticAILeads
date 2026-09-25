"""Prospect sample approval persists and stale rows stop exporting."""

import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    with tempfile.TemporaryDirectory() as folder:
        data = Path(folder)
        os.environ["AAL_DATA_DIR"] = str(data)
        os.environ["AAL_DISABLE_AUTH"] = "1"
        os.chdir(ROOT)

        import core
        from minilist_engine import sample_row_id
        from streamlit.testing.v1 import AppTest

        prospect = {"date_found": "2026-09-23", "agency": "Agency Ltd",
                    "person_name": "Alex Smith", "person_title": "Founder",
                    "linkedin": "https://www.linkedin.com/in/alex", "email": "",
                    "location": "London, UK", "size": "12",
                    "source": "fixture", "niche_keywords": "java"}
        with (data / "prospects.csv").open("w", newline="",
                                           encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(prospect))
            writer.writeheader()
            writer.writerow(prospect)
        key = core.prospect_key(prospect)
        at_time = datetime.now(timezone.utc).isoformat()
        job_url = "https://jobs.test/java"
        job = {"title": "Java Engineer", "location": "London",
               "url": job_url, "observed_at": at_time}
        (data / "signals_state.json").write_text(json.dumps({
            "greenhouse:acme": {"1": job}}), encoding="utf-8")
        (data / "board_refresh.json").write_text(json.dumps({
            "greenhouse:acme": {"status": "success",
                                "last_success": at_time}}), encoding="utf-8")
        (data / "company_board_mappings.json").write_text(json.dumps({
            "greenhouse:acme": {"company": "Acme Ltd",
                                "domain": "acme.test",
                                "contacts_key": "Acme Ltd"}}), encoding="utf-8")
        (data / "prospects_status.json").write_text("{}", encoding="utf-8")

        row_id = sample_row_id({"source_board": "greenhouse:acme",
                                "job_url": job_url})
        app = AppTest.from_file(ROOT / "app.py", default_timeout=30).run()
        assert not app.exception, [error.value for error in app.exception]
        approve = [widget for widget in app.multiselect
                   if widget.key == f"sample_approved_{key}"]
        save = [widget for widget in app.button
                if widget.key == f"sample_save_{key}"]
        assert len(approve) == len(save) == 1
        # Some AppTest versions expose download buttons separately.
        downloads = [widget for widget in app.download_button
                     if widget.key == f"mlc_{key}"]
        assert downloads and downloads[0].disabled

        approve[0].set_value([row_id])
        save[0].click()
        app.run()
        assert not app.exception, [error.value for error in app.exception]
        saved = json.loads((data / "prospects_status.json").read_text(
            encoding="utf-8"))
        assert saved[key]["sample_review"]["approved"] == [row_id]
        downloads = [widget for widget in app.download_button
                     if widget.key == f"mlc_{key}"]
        assert downloads and not downloads[0].disabled
        shown = "\n".join(str(block.value) for block in app.code)
        assert "Source:" not in shown and "greenhouse:" not in shown, shown
        assert "https://" in shown, "prospect text should keep the job link"

        title_input = [widget for widget in app.text_input
                       if widget.key == f"sample_hiring_now_{key}_{row_id}"]
        assert len(title_input) == 1
        title_input[0].set_value("Senior Java Engineer")
        [widget for widget in app.button
         if widget.key == f"sample_save_{key}"][0].click()
        app.run()
        saved = json.loads((data / "prospects_status.json").read_text(
            encoding="utf-8"))
        assert saved[key]["sample_review"]["corrections"][row_id][
            "hiring_now"] == "Senior Java Engineer"

        (data / "board_refresh.json").write_text(json.dumps({
            "greenhouse:acme": {"status": "failed",
                                "last_success": at_time}}), encoding="utf-8")
        app.run()
        downloads = [widget for widget in app.download_button
                     if widget.key == f"mlc_{key}"]
        assert downloads and downloads[0].disabled
        print("ALL SAMPLE REVIEW PAGE CHECKS PASS")


if __name__ == "__main__":
    main()
