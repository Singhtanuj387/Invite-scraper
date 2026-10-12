@echo off
title WhatsApp Invite Scraper Server
echo ===================================================
echo Starting WhatsApp Invite Scraper Dashboard...
echo ===================================================
echo.
echo Activating virtual environment (tfenv)...
powershell -Command "Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass"
call ..\tfenv\Scripts\activate
echo.
echo Starting server on http://127.0.0.1:5000
echo Open your browser and go to: http://127.0.0.1:5000
echo.
echo Press Ctrl+C to stop the server.
echo.
cd /d "%~dp0"
python app.py
pause
