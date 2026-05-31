"""
JobForge — entry point.
Starts Flask at localhost:7070 and opens the browser automatically.
"""

import threading
import webbrowser

from flask import Flask, redirect, url_for

import config_manager
from ui.routes import register_blueprints

app = Flask(__name__, template_folder="ui/templates", static_folder="ui/static")
app.secret_key = "jobforge-2026-secret"

register_blueprints(app)


@app.route("/")
def index():
    settings = config_manager.load_settings()
    settings = config_manager.auto_detect_paths(settings)
    if config_manager.is_first_run():
        return redirect(url_for("setup.wizard"))
    return redirect(url_for("dashboard.home"))


def _open_browser(port: int, first_run: bool):
    path = "/setup" if first_run else "/"
    webbrowser.open(f"http://localhost:{port}{path}")


if __name__ == "__main__":
    settings = config_manager.load_settings()
    settings = config_manager.auto_detect_paths(settings)
    port = settings.get("app", {}).get("port", 7070)
    first_run = config_manager.is_first_run()

    auto_open = settings.get("app", {}).get("auto_open_browser", True)
    if auto_open:
        threading.Timer(1.2, _open_browser, args=[port, first_run]).start()

    print(f"\n  JobForge running at http://localhost:{port}")
    print(f"  Press Ctrl+C to stop\n")
    app.run(host="127.0.0.1", port=port, debug=False)
