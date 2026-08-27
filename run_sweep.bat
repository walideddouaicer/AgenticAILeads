@echo off
rem ======================================================================
rem Nightly chain - registered in Windows Task Scheduler (daily, 07:00).
rem Register with:
rem   schtasks /create /tn "AAL nightly sweep" /tr "C:\Users\harJD\Downloads\AgenticAILeads\app\run_sweep.bat" /sc daily /st 07:00 /rl highest /f
rem
rem Takes .engine.lock for the whole chain, in the SAME format common.py
rem writes, so the app refuses to start an engine while this is running
rem and vice versa. Without this, a scheduled sweep and an app-triggered
rem engine can write job_history.json at the same time and lose
rem observations - and history is the one thing that cannot be rebuilt.
rem
rem If the machine dies mid-run the lock is left behind; the app's engine
rem panel detects that ("lockfile exists but no engine running") and
rem offers to clear it.
rem ======================================================================
cd /d C:\Users\harJD\Downloads\AgenticAILeads\app

rem Task Scheduler gives us a cp1252 console. The engines print '●' and
rem '—', and encoding those blows up MID-LOOP, before any state is saved.
rem That killed the nightly sweep silently every night from 2026-07-20 to
rem 2026-08-08. common.start_engine sets these for app-launched runs; the
rem scheduled chain must set them too. Do not remove.
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if exist .engine.lock (
  echo ==== nightly %date% %time% SKIPPED, engine lock present ==== >> sweep.log
  exit /b 0
)
echo %RANDOM% run_sweep.bat scheduled >> .engine.lock

echo ==== nightly %date% %time% ==== >> sweep.log

rem Back up the history BEFORE the sweep touches it. It is gitignored and
rem irreplaceable, so a corrupt write must never be the only copy.
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set STAMP=%%d
if not exist backups mkdir backups
if exist job_history.json copy /y job_history.json "backups\job_history_%STAMP%.json" >nul
if exist mandate_history.json copy /y mandate_history.json "backups\mandate_history_%STAMP%.json" >nul

rem The sweep is the one step whose failure is unrecoverable (a missed day
rem is a permanent hole in job_history.json), so its exit code is checked
rem loudly. The others no-op without API keys and are allowed to fail.
python signal_engine.py >> sweep.log 2>&1
if errorlevel 1 (
  echo *** SWEEP FAILED, exit %errorlevel% - HISTORY NOT RECORDED TODAY *** >> sweep.log
  echo *** SWEEP FAILED %date% %time% *** >> sweep_failures.log
)
python enrichment_engine.py >> sweep.log 2>&1
rem agency board discovery + mandate sweep, chained
python agency_discovery.py --from-prospects --and-sweep >> sweep.log 2>&1
python delivery.py --sync-replies >> sweep.log 2>&1

rem Build the morning queue LAST, so it sees today's fresh signals
rem and any replies that just synced.
python build_today.py --n 20 >> sweep.log 2>&1

rem Keep the 30 most recent history backups, drop the rest.
powershell -NoProfile -Command "Get-ChildItem backups\job_history_*.json | Sort-Object Name -Descending | Select-Object -Skip 30 | Remove-Item -Force" 2>nul
powershell -NoProfile -Command "Get-ChildItem backups\mandate_history_*.json | Sort-Object Name -Descending | Select-Object -Skip 30 | Remove-Item -Force" 2>nul

del .engine.lock
echo ==== nightly done %date% %time% ==== >> sweep.log
