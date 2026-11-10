@echo off
title Pakistan Surface Water Observatory - shared on the network
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install Python 3 and tick "Add python.exe to PATH".
    pause
    exit /b 1
)

echo Other PCs on the NDMA network can open the address shown below.
echo Keep this window open: closing it stops the portal for everyone.
echo If Windows asks whether Python may use the network, allow it on Private/Domain networks.
echo.

start "" /b cmd /c "timeout /t 2 /nobreak >nul & start "" http://127.0.0.1:8765"

python serve.py 8765 --lan
echo.
echo The server stopped. If port 8765 is already in use, the portal may already be running.
pause
