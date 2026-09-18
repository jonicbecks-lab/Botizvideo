import os
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from controller import PaperController

BASE = Path(__file__).resolve().parent
DATA = BASE / "data" / "paper_state.json"

app = Flask(__name__, static_folder="static")
ctl = PaperController(str(DATA))


@app.get("/")
def root():
    return send_from_directory(BASE / "static", "index.html")


@app.get("/api/state")
def api_state():
    return jsonify(ctl.public())


@app.post("/api/action")
def api_action():
    body = request.get_json(force=True, silent=True) or {}
    symbol = str(body.get("symbol", "")).upper()
    action = str(body.get("action", "")).upper()
    if symbol not in ctl.accounts:
        return jsonify({"error": "bad symbol"}), 400
    try:
        return jsonify(ctl.action(symbol, action))
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@app.post("/api/reset")
def api_reset():
    body = request.get_json(force=True, silent=True) or {}
    symbol = str(body.get("symbol", "")).upper()
    confirm = str(body.get("confirm", ""))
    if symbol not in ctl.accounts:
        return jsonify({"error": "bad symbol"}), 400
    if confirm != "RESET PAPER":
        return jsonify({"error": "confirmation required"}), 400
    return jsonify(ctl.reset(symbol))


if __name__ == "__main__":
    ctl.start()
    host = os.environ.get("PAPER_HOST", "127.0.0.1")
    port = int(os.environ.get("PAPER_PORT", "8765"))
    app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)
