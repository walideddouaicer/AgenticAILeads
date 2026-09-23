# Agentic AI Leads

A local Streamlit app for two founder workflows: finding recruitment agencies to approach, then managing hiring leads for a client. Sending and outcome recording are manual. The four screens are **Prospects**, **Clients**, **Signals**, and **Settings**.

## Install

Use Python 3.13.15 on Linux (see `.python-version`):

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/check.py --logs-dir /tmp/aal-check
```

On Windows, create the environment with `py -3.13 -m venv .venv`, then use `.venv\Scripts\python.exe` for the commands below. The isolated check runner copies source and fixtures to a temporary directory, blocks external network calls, and does not use your working data.

## Configure and start

Set `APP_USERNAME` and `APP_PASSWORD` in the process environment, or `APP_USERS` for two logins. Keep credentials out of Git. The launcher defaults to the project-local `data/` folder and seeds both separate board lists plus the default profile only when that folder is empty. Set `AAL_DATA_DIR` to an absolute path to use existing data elsewhere, including an older installation with data in the repository root. Optional `AAL_ENV_FILE` points to an environment file with an Apollo key for prospect or contact lookup. No provider key is needed for CSV imports, manual contact entry, or manual outreach.

```sh
export APP_USERNAME=operator
export APP_PASSWORD='choose-a-private-password'
.venv/bin/python start.py
```

Open the local URL printed by Streamlit and sign in. The Windows equivalent is to set the two variables with `$env:APP_USERNAME` and `$env:APP_PASSWORD` in PowerShell, then run `.venv\Scripts\python.exe start.py` or `start_app.bat`. The default address is `127.0.0.1`. For shared access, configure login credentials and explicitly pass `--host 0.0.0.0` on a trusted network; use HTTPS at the external access layer. `start.py --check` validates credentials and local data setup without starting a server. The optional Docker image uses the same startup path and requires a persistent volume at `/data`; CORS and XSRF protection stay enabled.

## Acquire an agency client

1. In **Prospects**, import an agency CSV or add a person manually. Apollo search is optional. Check the agency and person details.
2. Use **Signals** to add or discover agency boards, then refresh agency postings. Confirm board mappings before using a mandate as evidence.
3. Review the prospect's current evidence and sample in **Prospects**. Correct missing contacts or niche details, edit the final message, and send it yourself through the selected channel.
4. Record the send only after it happened. Work replies and follow-ups on the same screen; mark a client won when agreed. **Settings** holds prospect criteria and editable message templates.

The app does not send messages, enroll people in email sequences, or sync replies. The send record preserves the actual message and sample used.

## Deliver for a client

1. In **Clients**, create a client profile with target roles, locations, exclusions, outreach channel, and agreed meeting rate.
2. In **Signals**, add or discover employer boards and run a refresh. Only recently verified postings support current claims.
3. Back in **Clients**, review matching jobs, select verified ones for backfill, inspect the source and contact, and correct the contact if needed.
4. Edit the message, send it manually, and record the send, reply, and outcome. Record attended meetings once, link related leads, and mark whether each is free or chargeable. Export the selected client report CSV.

The first meeting defaults to free. The recorded estimate uses the saved client rate for chargeable attended meetings; it is not an invoice or a payment record. Apollo contact enrichment is optional and only runs when an operator chooses it.

Useful headless commands (run from the project directory) are `.venv/bin/python refresh.py nightly --dry-run` to inspect the portable refresh chain, `.venv/bin/python refresh.py nightly` to run it, and `.venv/bin/python refresh.py weekly` to grow board coverage. These use the same data directory and runner lock as the app. They are manual commands; no cron, systemd task, or Windows schedule is installed. For individual work, `signal_engine.py --baseline` and `prospect_signal_engine.py --baseline` establish first-sweep baselines; `build_today.py --dry-run` previews the acquisition queue, and `enrichment_engine.py --list-companies` previews lookup candidates without provider calls. Run individual scripts with `AAL_DATA_DIR` set to the same directory as the app. A first sweep does not claim old postings are newly opened.

## Back up and recover

Keep the app's data directory on persistent storage. The refresh chain saves existing job and mandate histories under `backups/` before it runs. **Settings** can restore one of those histories after confirmation, preserving the current copy. A damaged JSON file is reported rather than silently replaced.

For a complete operational backup, stop the app and refresh processes, then run:

```sh
.venv/bin/python recovery.py backup
.venv/bin/python recovery.py restore data/backups/operational-YYYYMMDDTHHMMSSZ.zip restored-data
```

Use the actual archive name printed by the backup command. The archive includes profiles, mappings, contact corrections, statuses, client meetings, sweep checkpoints, and both histories. It excludes `.env`, provider probes and old archives; it still contains private prospect and client data, so keep it private. Restore validates file contents and checksums into a **new** directory and never switches the running app to it. After inspecting the restored directory, stop the app and deliberately set `AAL_DATA_DIR` to its absolute path before restarting. Keep a separate copy of the previous data directory until recovery is confirmed. If you keep extra files outside the supported operational set, copy those privately as well.

Current engineering checkpoints and remaining release gates are in [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) and [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). Older strategy and deployment notes are historical and do not describe the supported interface.
