from __future__ import annotations

import json
import sys
import threading
import time
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from automation import AnswerBot, BotConfig

PROJECT_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", PROJECT_DIR))
app = Flask(
    __name__,
    template_folder=str(RESOURCE_DIR / "templates"),
    static_folder=str(RESOURCE_DIR / "static"),
)
BASE_DIR = PROJECT_DIR
bot = AnswerBot(BASE_DIR)
CONFIG_PATH = BASE_DIR / "config.json"
DEFAULT_CONFIG = {
    "answer_time": 15,
    "interval_min_time": 0.2,
    "interval_max_time": 0.6,
    "error_time": 0.8,
    "click_error_distance": 2,
    "page_load_timeout": 8,
    "screen_poll_interval": 0.15,
    "notify_on_submit": True,
    "send_image_to_model": False,
    "adb_path": "",
    "device_serial": "",
    "api_key": "",
    "model": "deepseek-flash",
    "api_base": "https://api.deepseek.com",
}


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    try:
        config.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return config


def save_config(config: BotConfig) -> None:
    CONFIG_PATH.write_text(json.dumps({
        "answer_time": config.answer_time,
        "interval_min_time": config.interval_min_time,
        "interval_max_time": config.interval_max_time,
        "error_time": config.error_time,
        "click_error_distance": config.click_error_distance,
        "page_load_timeout": config.page_load_timeout,
        "screen_poll_interval": config.screen_poll_interval,
        "notify_on_submit": config.notify_on_submit,
        "send_image_to_model": config.send_image_to_model,
        "adb_path": config.adb_path,
        "device_serial": config.device_serial,
        "api_key": config.api_key,
        "model": config.model,
        "api_base": config.api_base,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/status")
def status():
    return jsonify(bot.status())


@app.get("/api/config")
def config():
    return jsonify(load_config())


@app.post("/api/config")
def save_current_config():
    payload = request.get_json(silent=True) or {}
    try:
        config = BotConfig.from_payload(payload)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    save_config(config)
    return jsonify({"ok": True, "message": "参数已保存"})


@app.post("/api/start")
def start():
    payload = request.get_json(silent=True) or {}
    try:
        config = BotConfig.from_payload(payload)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    save_config(config)
    started = bot.start(config)
    if not started:
        return jsonify({"ok": False, "error": "答题任务已经在运行中"}), 409
    return jsonify({"ok": True})


@app.post("/api/stop")
def stop():
    bot.stop()
    return jsonify({"ok": True})


def open_browser() -> None:
    time.sleep(1)
    webbrowser.open("http://127.0.0.1:5050")


if __name__ == "__main__":
    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="127.0.0.1", port=5050, debug=False, threaded=True)
