import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

# 让 webui/app.py 能引用项目根目录下的模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from flask import Flask, jsonify, render_template, request

from name_fetcher import fetch_names

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
LOG_DIR = ROOT / "logs"

app = Flask(__name__)

# 记录后台子进程
PROCESSES = {}
LOCK = threading.Lock()


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_config(data):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def read_tail(path: Path, max_lines: int = 300) -> str:
    if not path.exists():
        return ""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 16384))
        data = f.read().decode("utf-8", errors="replace")
    lines = data.splitlines()
    return "\n".join(lines[-max_lines:])


def process_status(name: str) -> dict:
    with LOCK:
        item = PROCESSES.get(name)
    if not item:
        return {"running": False, "pid": None, "log": ""}

    proc = item["proc"]
    if proc.poll() is None:
        return {"running": True, "pid": proc.pid, "log": read_tail(item["log_path"])}

    # 已退出，清理
    with LOCK:
        PROCESSES.pop(name, None)
    try:
        item["log_file"].close()
    except Exception:
        pass
    return {"running": False, "pid": None, "log": read_tail(item["log_path"])}


def start_process(name: str, cmd: list, log_name: str) -> bool:
    with LOCK:
        item = PROCESSES.get(name)
        if item and item["proc"].poll() is None:
            return False

        LOG_DIR.mkdir(exist_ok=True)
        log_path = LOG_DIR / log_name
        log_file = open(log_path, "wb", buffering=0)

        # 强制子进程以 UTF-8 输出，避免 Windows 下 GBK/UTF-8 乱码
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
        )
        PROCESSES[name] = {
            "proc": proc,
            "log_path": log_path,
            "log_file": log_file,
            "started_at": time.time(),
        }
    return True


def stop_process(name: str) -> bool:
    with LOCK:
        item = PROCESSES.get(name)
        if not item:
            return False
        proc = item["proc"]
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    return True


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config")
def api_get_config():
    return jsonify(load_config())


@app.route("/api/config", methods=["POST"])
def api_save_config():
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "配置格式错误"}), 400

    # 简单校验必填字段
    if not data.get("movie_url") and not data.get("url"):
        return jsonify({"ok": False, "error": "movie_url 不能为空"}), 400
    cinemas = data.get("cinemas") or []
    if not cinemas and not data.get("cinema_url"):
        return jsonify({"ok": False, "error": "至少需要一个影院"}), 400

    try:
        save_config(data)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True})


@app.route("/api/fetch-names", methods=["POST"])
def api_fetch_names():
    data = request.get_json(force=True)
    movie_url = (data.get("movie_url") or "").strip()
    cinemas = data.get("cinemas") or []
    cinema_urls = [((c.get("url") if isinstance(c, dict) else "") or "").strip() for c in cinemas]

    if not movie_url and not any(cinema_urls):
        return jsonify({"ok": False, "error": "请至少填写电影 URL 或影院 URL"}), 400

    try:
        cfg = load_config()
        profile_dir = data.get("profile") or cfg.get("user_data_dir") or "./profile"
        if not os.path.isabs(profile_dir):
            profile_dir = str(ROOT / profile_dir)

        movie_name, cinema_names = fetch_names(movie_url, cinema_urls, profile_dir=profile_dir)
        return jsonify({
            "ok": True,
            "movie_name": movie_name,
            "cinemas": [
                {"url": url, "name": name}
                for url, name in zip(cinema_urls, cinema_names)
            ],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/state/reset", methods=["POST"])
def api_state_reset():
    state_file = ROOT / "state" / "notified.txt"
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("", encoding="utf-8")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/status")
def api_status():
    return jsonify(
        {
            "monitor": process_status("monitor"),
            "seat_map": process_status("seat_map"),
            "test_seat": process_status("test_seat"),
        }
    )


@app.route("/api/monitor/start", methods=["POST"])
def api_monitor_start():
    ok = start_process("monitor", [sys.executable, "main.py"], "webui_monitor.log")
    if not ok:
        return jsonify({"ok": False, "error": "监控已经在运行"}), 400
    return jsonify({"ok": True})


@app.route("/api/monitor/stop", methods=["POST"])
def api_monitor_stop():
    stop_process("monitor")
    return jsonify({"ok": True})


@app.route("/api/tool/run", methods=["POST"])
def api_tool_run():
    data = request.get_json(force=True)
    tool = data.get("tool")
    url = (data.get("url") or "").strip()
    profile = (data.get("profile") or "").strip()

    if tool not in ("seat_map", "test_seat"):
        return jsonify({"ok": False, "error": "未知工具"}), 400
    if not url:
        return jsonify({"ok": False, "error": "请填写场次页 URL"}), 400

    cmd = [sys.executable, f"{tool}.py", url]
    if profile:
        cmd.append(profile)

    log_name = f"webui_{tool}.log"
    ok = start_process(tool, cmd, log_name)
    if not ok:
        return jsonify({"ok": False, "error": f"{tool} 已经在运行"}), 400

    return jsonify({"ok": True})


@app.route("/api/tool/stop", methods=["POST"])
def api_tool_stop():
    data = request.get_json(force=True)
    tool = data.get("tool")
    if tool not in ("seat_map", "test_seat"):
        return jsonify({"ok": False, "error": "未知工具"}), 400
    stop_process(tool)
    return jsonify({"ok": True})


@app.route("/api/log/<name>")
def api_log(name):
    if name not in ("monitor", "seat_map", "test_seat"):
        return jsonify({"error": "未知日志"}), 404
    status = process_status(name)
    return jsonify({"log": status["log"]})


if __name__ == "__main__":
    print("猫眼监控控制面板: http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
