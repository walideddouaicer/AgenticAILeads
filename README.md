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

Set `APP_USERNAME` and `APP_PASSWORD` in the process environment, or `APP_USERS` for two logins. Keep credentials out of Git. Optional `AAL_DATA_DIR` points all app and engine data at one shared directory; by default it is the project directory. Optional `AAL_ENV_FILE` points to an environment file with an Apollo key for prospect or contact lookup. No provider key is needed for CSV imports, manual contact entry, or manual outreach.

```sh
export APP_USERNAME=operator
export APP_PASSWORD='choose-a-private-password'
.venv/bin/python -m streamlit run app.py --server.address 127.0.0.1
```

Open the local URL printed by Streamlit and sign in. The Windows equivalent is to set the two variables with `$env:APP_USERNAME` and `$env:APP_PASSWORD` in PowerShell, then run `.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1`.

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

Useful headless commands (run from the project directory) are `signal_engine.py --baseline` for the first employer sweep, `prospect_signal_engine.py --baseline` for the first agency sweep, `build_today.py --dry-run` for the current acquisition queue, and `enrichment_engine.py --list-companies` to inspect lookup candidates without making provider calls. Each supports `--help`. The app also has buttons for the supported refresh tasks. A first sweep establishes a baseline; it does not claim old postings are newly opened.

## Back up and recover

Keep the app's data directory on persistent storage. The runner's refresh chain saves existing job and mandate histories under `backups/` before it runs. **Settings** shows these backups and can restore a selected history after confirmation. The restore saves the current history as a pre-restore backup. A damaged JSON file is reported rather than silently replaced.

For a full manual backup, stop writers and copy the entire data directory, including profiles, mappings, contact corrections, statuses, meetings, and both histories, to a private location. Keep `.env` and login credentials out of shareable archives. Before restoring, make a separate copy of the current directory, inspect the backup, and deliberately replace the intended files while the app and engines are stopped. A complete automated backup/restore round trip and portable Linux scheduling are Phase 7 work.

Current engineering checkpoints and remaining release gates are in [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) and [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). Older strategy and deployment notes are historical and do not describe the supported interface.
