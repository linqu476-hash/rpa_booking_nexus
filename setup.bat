@echo off
cd /d "%~dp0"
echo === RPA Booking SMS - one-time setup ===
python -m pip install -r requirements.txt
if errorlevel 1 goto :err
python -m playwright install chromium
if errorlevel 1 goto :err
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo.
  echo Created .env  ->  open it in Notepad and fill in INFOR_USERNAME, INFOR_PASSWORD, INFOR_URL
) else (
  echo .env already exists - left unchanged.
)
echo.
echo Setup finished. Next: double-click run_step5.bat
pause
exit /b 0
:err
echo.
echo Setup failed - check that Python is installed and added to PATH.
pause
exit /b 1
