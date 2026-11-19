@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
 py -3 server.py --open-browser
) else (
 python server.py --open-browser
)
echo.
echo MOSS Layout Studio has stopped. If the port is in use, close the old server window first.
pause
