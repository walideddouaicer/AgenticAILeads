@echo off
rem Weekly watchlist growth. Uses the same runner and lock as every app engine.
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

echo ==== weekly %date% %time% ==== >> sweep.log
"%~dp0.venv\Scripts\python.exe" refresh.py weekly >> sweep.log 2>&1
set RESULT=%errorlevel%
echo ==== weekly done %date% %time% (exit %RESULT%) ==== >> sweep.log
exit /b %RESULT%
