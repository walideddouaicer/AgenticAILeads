@echo off
rem Start locally. Use start.py --host with configured logins for shared access.
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" start.py %*
exit /b %errorlevel%
