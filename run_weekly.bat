@echo off
rem ======================================================================
rem Weekly growth chain - Task Scheduler (Sunday 09:00).
rem Register with:
rem   schtasks /create /tn "AAL weekly growth" /tr "C:\Users\harJD\Downloads\AgenticAILeads\app\run_weekly.bat" /sc weekly /d SUN /st 09:00 /rl highest /f
rem
rem Grows the watchlist and refreshes agency boards from prospects.
rem Takes the same lock as the nightly chain - these must never overlap,
rem because discovery_engine adds boards that the sweep then baselines.
rem ======================================================================
cd /d C:\Users\harJD\Downloads\AgenticAILeads\app

rem See run_sweep.bat — a cp1252 console breaks any script that prints
rem non-Latin-1 characters, mid-run, before anything is saved.
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if exist .engine.lock (
  echo ==== weekly %date% %time% SKIPPED, engine lock present ==== >> sweep.log
  exit /b 0
)
echo %RANDOM% run_weekly.bat scheduled >> .engine.lock

echo ==== weekly %date% %time% ==== >> sweep.log
python discovery_engine.py --hn 1 >> sweep.log 2>&1
python agency_discovery.py --from-prospects --and-sweep >> sweep.log 2>&1

del .engine.lock
echo ==== weekly done %date% %time% ==== >> sweep.log
