"""
WhatsApp Invite Scraper — Web Dashboard Backend
Flask application with SSE streaming for real-time monitoring.
"""

import os
import csv
import json
import time
import threading
from datetime import datetime
from queue import Queue

from flask import (
    Flask, render_template, request, jsonify,
    send_file, Response, stream_with_context
)

# Import the core scraper function
from scraper import check_whatsapp_invite

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Directories
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
scraper_state = {
    "status": "idle",          # idle | running | stopping | done | error
    "total": 0,
    "processed": 0,
    "success": 0,
    "failed": 0,
    "expired": 0,
    "input_file": None,
    "output_file": None,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "results": [],             # list of dicts for the results table
}

stop_event = threading.Event()
sse_subscribers: list[Queue] = []
sse_lock = threading.Lock()


def broadcast_event(event_type: str, data: dict):
    """Push an SSE event to every connected subscriber."""
    payload = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    dead = []
    with sse_lock:
        for q in sse_subscribers:
            try:
                q.put_nowait(payload)
            except Exception:
                dead.append(q)
        for q in dead:
            sse_subscribers.remove(q)


# ---------------------------------------------------------------------------
# Background scraper
# ---------------------------------------------------------------------------
def run_scraper(input_path: str, output_path: str):
    """Run the scraper in a background thread, streaming results via SSE."""
    global scraper_state

    fieldnames = [
        "Invite Link", "Group Name", "Status", "Members",
        "Country", "Admin", "Processing Status", "Timestamp of Scan", "Notes"
    ]

    # Read links from the uploaded CSV
    links = []
    try:
        with open(input_path, mode="r", encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                if row:
                    link = row[0].strip()
                    if link.startswith("http"):
                        links.append(link)
    except Exception as e:
        scraper_state["status"] = "error"
        scraper_state["error"] = str(e)
        broadcast_event("error", {"message": str(e)})
        return

    if not links:
        scraper_state["status"] = "error"
        scraper_state["error"] = "No valid links found in the uploaded CSV."
        broadcast_event("error", {"message": scraper_state["error"]})
        return

    scraper_state["total"] = len(links)
    scraper_state["processed"] = 0
    scraper_state["success"] = 0
    scraper_state["failed"] = 0
    scraper_state["expired"] = 0
    scraper_state["results"] = []
    scraper_state["status"] = "running"
    scraper_state["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    scraper_state["finished_at"] = None
    scraper_state["error"] = None

    broadcast_event("started", {
        "total": len(links),
        "started_at": scraper_state["started_at"],
    })

    try:
        with open(output_path, mode="w", newline="", encoding="utf-8") as outfile:
            writer = csv.DictWriter(outfile, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
            writer.writeheader()

            for idx, link in enumerate(links, start=1):
                # Check if stop was requested
                if stop_event.is_set():
                    scraper_state["status"] = "stopped"
                    broadcast_event("stopped", {
                        "processed": scraper_state["processed"],
                        "total": scraper_state["total"],
                    })
                    return

                # Process the link
                result = check_whatsapp_invite(link)
                writer.writerow(result)
                outfile.flush()

                # Update counters
                scraper_state["processed"] = idx
                if result["Processing Status"] == "Success":
                    scraper_state["success"] += 1
                    if result["Status"] == "Expired":
                        scraper_state["expired"] += 1
                else:
                    scraper_state["failed"] += 1

                scraper_state["results"].append(result)

                # Broadcast the result
                broadcast_event("result", {
                    "index": idx,
                    "total": len(links),
                    "row": result,
                    "counters": {
                        "processed": scraper_state["processed"],
                        "success": scraper_state["success"],
                        "failed": scraper_state["failed"],
                        "expired": scraper_state["expired"],
                        "total": scraper_state["total"],
                    }
                })

                # Rate-limit to be polite
                time.sleep(0.5)

    except Exception as e:
        scraper_state["status"] = "error"
        scraper_state["error"] = str(e)
        broadcast_event("error", {"message": str(e)})
        return

    scraper_state["status"] = "done"
    scraper_state["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    broadcast_event("done", {
        "processed": scraper_state["processed"],
        "total": scraper_state["total"],
        "finished_at": scraper_state["finished_at"],
    })


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file part in request"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if not file.filename.lower().endswith(".csv"):
        return jsonify({"error": "Only CSV files are accepted"}), 400

    # Save
    save_path = os.path.join(UPLOAD_DIR, "input.csv")
    file.save(save_path)

    # Count links
    count = 0
    with open(save_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if row and row[0].strip().startswith("http"):
                count += 1

    scraper_state["input_file"] = save_path
    return jsonify({
        "success": True,
        "filename": file.filename,
        "link_count": count,
    })


@app.route("/api/start", methods=["POST"])
def start():
    if scraper_state["status"] == "running":
        return jsonify({"error": "Scraper is already running"}), 409

    input_path = scraper_state.get("input_file")
    if not input_path or not os.path.exists(input_path):
        return jsonify({"error": "No input file uploaded. Upload a CSV first."}), 400

    output_path = os.path.join(OUTPUT_DIR, "output.csv")
    scraper_state["output_file"] = output_path

    # Reset stop event
    stop_event.clear()

    # Launch background thread
    t = threading.Thread(target=run_scraper, args=(input_path, output_path), daemon=True)
    t.start()

    return jsonify({"success": True, "message": "Scraper started"})


@app.route("/api/stop", methods=["POST"])
def stop():
    if scraper_state["status"] != "running":
        return jsonify({"error": "Scraper is not running"}), 409

    stop_event.set()
    scraper_state["status"] = "stopping"
    return jsonify({"success": True, "message": "Stop signal sent"})


@app.route("/api/status", methods=["GET"])
def status():
    return jsonify({
        "status": scraper_state["status"],
        "total": scraper_state["total"],
        "processed": scraper_state["processed"],
        "success": scraper_state["success"],
        "failed": scraper_state["failed"],
        "expired": scraper_state["expired"],
        "started_at": scraper_state["started_at"],
        "finished_at": scraper_state["finished_at"],
        "error": scraper_state["error"],
    })


@app.route("/api/results", methods=["GET"])
def results():
    return jsonify(scraper_state["results"])


@app.route("/api/stream")
def stream():
    """SSE endpoint — clients connect here for live updates."""
    q: Queue = Queue()
    with sse_lock:
        sse_subscribers.append(q)

    def event_stream():
        try:
            while True:
                payload = q.get()  # blocks until an event is available
                yield payload
        except GeneratorExit:
            pass
        finally:
            with sse_lock:
                if q in sse_subscribers:
                    sse_subscribers.remove(q)

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/download", methods=["GET"])
def download():
    output_path = scraper_state.get("output_file")
    if not output_path or not os.path.exists(output_path):
        return jsonify({"error": "No output file available yet"}), 404

    return send_file(
        output_path,
        mimetype="text/csv",
        as_attachment=True,
        download_name="scraper_output.csv",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
