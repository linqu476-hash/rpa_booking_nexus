@echo off
cd /d "%~dp0"
echo === RPA Booking SMS - Step 5 (runs steps 1-4 first) ===
set /p PLAN=Packing Plan ref (e.g. 609537759987): 
set /p CMT=Comment / invoice text (e.g. Inv number): 
set /p CRD=Cargo ready date yyyy-MM-dd (e.g. 2026-10-19): 
echo.
python step5_edit_items.py "%PLAN%" --comment "%CMT%" --date %CRD%
echo.
pause
