@echo off
cd /d "%~dp0"
REM Use this if login fails or the session expired: it deletes the saved session and logs in fresh.
if exist auth_state.json del auth_state.json
python step1_login.py
pause
