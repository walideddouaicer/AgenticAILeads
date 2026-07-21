@echo off
rem Nightly chain - registered in Windows Task Scheduler (run daily, e.g. 07:00)
rem Each step logs to sweep.log; steps without API keys no-op harmlessly.
cd /d C:\Users\harJD\Downloads\AgenticAILeads\app
echo ==== nightly %date% %time% ==== >> sweep.log
python signal_engine.py >> sweep.log 2>&1
python enrichment_engine.py >> sweep.log 2>&1
python prospect_signal_engine.py >> sweep.log 2>&1
python delivery.py --sync-replies >> sweep.log 2>&1
