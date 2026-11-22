"""
Start the web app.

    Laptop (development):  python run.py          -> http://localhost:5000
    Raspberry Pi:          waitress-serve --host 0.0.0.0 --port 80 run:app
"""
import os

from app import create_app

# `python run.py` starts TWO processes: a watcher that restarts the app when code changes, and
# the app itself. Only the app writes logs/app.log - if both kept it open, Windows couldn't
# rotate the file at 1 MB ("--- Logging error ---").
_reloader_watcher = __name__ == "__main__" and os.environ.get("WERKZEUG_RUN_MAIN") != "true"
app = create_app({"LOG_TO_FILE": False} if _reloader_watcher else None)

if __name__ == "__main__":
    app.config["TEMPLATES_AUTO_RELOAD"] = True      # template edits show up without restarting
    app.jinja_env.auto_reload = True                # (the Jinja env already exists, so set it directly too)
    # (CSS/JS are cached by the browser but carry ?v=<file time>, so edits still show up)
    app.run(host="0.0.0.0", port=5000, debug=True)
