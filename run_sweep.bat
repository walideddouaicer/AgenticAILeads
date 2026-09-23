@echo off
rem Nightly refresh. The shared Python runner owns locking, backups,
rem step failure handling and run status for both the app and scheduler.
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

echo ==== nightly %date% %time% ==== >> sweep.log
"%~dp0.venv\Scripts\python.exe" refresh.py nightly >> sweep.log 2>&1
set RESULT=%errorlevel%
if not "%RESULT%"=="0" echo *** NIGHTLY FAILED %date% %time% (exit %RESULT%) *** >> sweep_failures.log
echo ==== nightly done %date% %time% (exit %RESULT%) ==== >> sweep.log
exit /b %RESULT%
