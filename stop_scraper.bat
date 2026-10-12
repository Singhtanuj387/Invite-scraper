@echo off
echo ===================================================
echo Stopping WhatsApp Invite Scraper...
echo ===================================================
echo.

taskkill /F /FI "WINDOWTITLE eq WhatsApp Invite Scraper Server" >nul 2>&1
taskkill /F /IM python.exe /T >nul 2>&1

echo Done! Server stopped.
pause
