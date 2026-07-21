@echo off
rem Weekly growth chain - Task Scheduler (e.g. Sunday 09:00)
rem Grows the watchlist and refreshes agency boards from prospects.
cd /d C:\Users\harJD\Downloads\AgenticAILeads\app
echo ==== weekly %date% %time% ==== >> sweep.log
python discovery_engine.py --hn 1 >> sweep.log 2>&1
python agency_discovery.py --from-prospects >> sweep.log 2>&1
