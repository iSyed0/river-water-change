@echo off
title Pakistan Surface Water Observatory
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install Python 3 and tick "Add python.exe to PATH".
    pause
    exit /b 1
)

rem Open the browser a couple of seconds after the server starts
start "" /b cmd /c "timeout /t 2 /nobreak >nul & start "" http://127.0.0.1:8765"

python serve.py 8765
echo.
echo The server stopped. If port 8765 is already in use, the portal may already be running.
pause
