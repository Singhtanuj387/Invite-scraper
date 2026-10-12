#!/bin/bash
echo "==================================================="
echo "Starting WhatsApp Invite Scraper Dashboard..."
echo "==================================================="
echo ""
echo "Activating virtual environment: /mnt/extra/morningstar/tfenv"
source /mnt/extra/morningstar/tfenv/bin/activate

echo "Starting server on http://127.0.0.1:5000"
echo "Press Ctrl+C to stop it from the terminal."
echo ""
python app.py
