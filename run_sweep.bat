@echo off
rem Nightly sweep — registered in Windows Task Scheduler
cd /d C:\Users\PC\Projects\AgenticAILeads
echo ==== sweep %date% %time% ==== >> sweep.log
python signal_engine.py >> sweep.log 2>&1
