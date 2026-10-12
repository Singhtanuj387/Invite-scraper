#!/bin/bash
echo "==================================================="
echo "Stopping WhatsApp Invite Scraper..."
echo "==================================================="

# Kill any python process running app.py
pkill -f "python app.py"

echo "Done! Server stopped."
