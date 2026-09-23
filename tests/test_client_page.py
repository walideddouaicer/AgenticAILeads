"""First client can be created, backfilled and reported through the app."""

import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory() as folder:
        data = Path(folder)
        os.environ["AAL_DATA_DIR"] = str(data)
        os.environ["AAL_DISABLE_AUTH"] = "1"
        sys.path.insert(0, str(ROOT))
        os.chdir(ROOT)

        from streamlit.testing.v1 import AppTest
        import client_delivery as D
        import core

        stamp = datetime.now(timezone.utc).isoformat()
        job = {"title": "Java Engineer", "location": "London",
               "url": "https://jobs.test/java", "observed_at": stamp}
        (data / "signals_state.json").write_text(json.dumps({
            "greenhouse:example": {"1": job}}), encoding="utf-8")
        (data / "board_refresh.json").write_text(json.dumps({
            "greenhouse:example": {"status": "success",
                                   "last_success": stamp}}), encoding="utf-8")
        (data / "prospects_status.json").write_text(
            '{"agency-key": {"status": "contacted"}}', encoding="utf-8")

        at = AppTest.from_file(ROOT / "app.py", default_timeout=30).run()
        at.switch_page("views/clients.py")
        at.run()
        assert not at.exception, [error.value for error in at.exception]
        assert [widget for widget in at.selectbox if widget.label == "Client"][0] \
            .value == "Create client", "default cannot appear as a paying client"
        assert any("default" in str(item.value)
                   for item in at.info), "empty state should explain default"
        for widget in at.text_input:
            if widget.label == "Client ID":
                widget.set_value("first_client")
            elif widget.label == "Client name":
                widget.set_value("First Client")
        for widget in at.text_area:
            if widget.label == "Target roles / niche keywords":
                widget.set_value("Java")
            elif widget.label == "Allowed locations":
                widget.set_value("London")
        [button for button in at.button
         if button.label == "Save client profile"][0].click()
        at.run()
        assert any("agreed rate" in str(item.value) for item in at.error), \
            "a new client must not save an assumed meeting rate"
        assert not (data / "configs" / "first_client.json").exists()
        for widget in at.number_input:
            if widget.label == "Agreed rate per attended, chargeable meeting ($)":
                assert widget.value is None, widget.value
                widget.set_value(175)
        [button for button in at.button
         if button.label == "Save client profile"][0].click()
        at.run()
        assert not at.exception, [error.value for error in at.exception]
        assert (data / "configs" / "first_client.json").exists()
        choices = [widget for widget in at.multiselect
                   if widget.label == "Select verified jobs to backfill"]
        assert choices, "verified matching job must be selectable"
        selection = D.job_identity({"board_key": "greenhouse:example",
                                    "job_id": "1"})
        choices[0].set_value([selection])
        at.run()
        [button for button in at.button
         if button.label == "Add selected jobs as client leads"][0].click()
        at.run()
        assert not at.exception, [error.value for error in at.exception]
        leads, _ = core.load_leads()
        assert len(leads) == 1 and leads[0]["client"] == "first_client"

        [widget for widget in at.segmented_control
         if widget.key == "client_view"][0].set_value("Outreach")
        at.run()
        assert not at.exception, [error.value for error in at.exception]
        draft = [widget for widget in at.text_area
                 if widget.label == "Final message"]
        assert draft and draft[0].value.startswith("Hi there"), (
            draft[0].value if draft else "missing")
        # Adding the contact must refresh an unsaved generated greeting.
        (data / "enriched_contacts.json").write_text(json.dumps({
            "greenhouse:example": [{"name": "Jane Doe", "title": "Hiring Lead",
                                    "linkedin": "https://linkedin.com/in/janedoe",
                                    "email": ""}]}), encoding="utf-8")
        at.run()
        draft = [widget for widget in at.text_area
                 if widget.label == "Final message"]
        assert draft and draft[0].value.startswith("Hi Jane — saw"), (
            draft[0].value if draft else "missing")
        draft[0].set_value("Hi Jane - reviewed your Java opening. Let's talk.")
        at.run()
        [button for button in at.button
         if button.label == "Record manual send"][0].click()
        at.run()
        assert not at.exception, [error.value for error in at.exception]
        saved = json.loads((data / "inbox_status.json").read_text())
        key = core.lead_key(leads[0])
        recorded = saved[key]["activity"][0]["message"]
        expected = "Hi Jane - reviewed your Java opening. Let's talk."
        assert recorded == expected, (repr(recorded), repr(expected))
        assert (data / "prospects_status.json").read_text() == (
            '{"agency-key": {"status": "contacted"}}')

        [widget for widget in at.segmented_control
         if widget.key == "client_view"][0].set_value("Outcomes & report")
        at.run()
        assert not at.exception, [error.value for error in at.exception]
        for widget in at.text_input:
            if widget.label == "Meeting ID or calendar reference":
                widget.set_value("calendar-1")
            elif widget.label == "Person who attended":
                widget.set_value("Jane Doe")
        [widget for widget in at.multiselect
         if widget.label == "Related client leads"][0].set_value([key])
        [button for button in at.button
         if button.label == "Save attended meeting"][0].click()
        at.run()
        assert not at.exception, [error.value for error in at.exception]
        meetings = D.load_meetings()
        assert len(meetings) == 1
        assert not next(iter(meetings.values()))["chargeable"]
        assert any("$0" in str(metric.value) for metric in at.metric)
        charge_status = [widget for widget in at.selectbox
                         if widget.label == "Charge status"]
        assert charge_status and charge_status[0].value == "Chargeable", (
            charge_status[0].value if charge_status else "missing")
        for widget in at.text_input:
            if widget.label == "Meeting ID or calendar reference":
                widget.set_value("calendar-2")
            elif widget.label == "Person who attended":
                widget.set_value("Jane Doe")
        [widget for widget in at.multiselect
         if widget.label == "Related client leads"][0].set_value([key])
        [button for button in at.button
         if button.label == "Save attended meeting"][0].click()
        at.run()
        assert not at.exception, [error.value for error in at.exception]
        meetings = D.load_meetings()
        assert len(meetings) == 2
        assert sum(bool(value["chargeable"]) for value in meetings.values()) == 1
        assert any("$175" in str(metric.value) for metric in at.metric)
        exports = [button for button in at.download_button
                   if button.label == "Export selected report CSV"]
        assert exports and not exports[0].disabled
        print("ALL FIRST-CLIENT PAGE CHECKS PASS")


if __name__ == "__main__":
    main()
