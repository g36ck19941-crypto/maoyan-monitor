import base64
import logging
import os
import shutil
import smtplib
import subprocess
import threading
import time
from datetime import datetime, time as dtime
from email.message import EmailMessage

import requests

try:
    import winsound
except ImportError:  # 非 Windows 环境
    winsound = None

logger = logging.getLogger(__name__)


# 事件 -> 通道与响铃时长。可在 config.yaml 的 notify.routing 里覆盖。
DEFAULT_ROUTING = {
    "sale": {
        "wechat": True,
        "email": True,
        "sound": True,
        "sound_seconds": 20,
        "priority": 2,
        "speak_text": "{title}，已开售，请立即去抢票。",
    },
    "seat_ok": {
        "wechat": True,
        "email": True,
        "sound": True,
        "sound_seconds": 25,
        "priority": 3,
        "speak_text": "占座成功，请尽快完成支付。",
    },
    "seat_fail": {
        "wechat": True,
        "email": True,
        "sound": True,
        "sound_seconds": 3,
        "priority": 1,
        "speak_text": "占座未成功，脚本会自动重试。",
    },
    "error": {
        "wechat": False,
        "sound": False,
        "speak_text": "",
    },
}


class ServerChanNotifier:
    """Server酱推送微信通知"""

    def __init__(self, send_key: str):
        self.api_url = f"https://sctapi.ftqq.com/{send_key}.send"

    def send(self, title: str, desp: str = "") -> bool:
        try:
            resp = requests.post(
                self.api_url,
                data={
                    "title": title,
                    "desp": desp,
                },
                timeout=10,
            )
            data = resp.json()
            if data.get("code") == 0:
                logger.info("Server酱已发送: %s", title)
                return True

            logger.warning("Server酱推送失败: %s", data)
        except Exception as e:
            logger.warning("Server酱推送异常（网络/DNS/服务不可用）: %s", e)
        return False


class EmailNotifier:
    """SMTP 邮件通知（Gmail 应用专用密码）。

    凭据只从 config.local.yaml 读；应用专用密码里的空格会自动去掉。
    """

    def __init__(self, config: dict | None = None):
        cfg = (config or {}).get("email") or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.host = str(cfg.get("smtp_host", "") or "").strip()
        self.port = int(cfg.get("smtp_port", 465) or 465)
        self.use_ssl = bool(cfg.get("ssl", True))
        self.username = str(cfg.get("username", "") or "").strip()
        self.password = str(cfg.get("app_password", "") or "").replace(" ", "").strip()
        raw_to = cfg.get("to") or []
        if isinstance(raw_to, str):
            raw_to = [raw_to]
        self.recipients = [str(x).strip() for x in raw_to if str(x).strip()]
        self.subject_prefix = str(cfg.get("subject_prefix", "") or "")
        self.timeout = int(cfg.get("timeout", 20) or 20)

    def usable(self) -> bool:
        return bool(
            self.enabled
            and self.host
            and self.username
            and self.password
            and self.recipients
        )

    def send(self, title: str, desp: str = "") -> bool:
        if not self.usable():
            logger.warning("邮件通道未就绪（检查 config.local.yaml 的 email 段），跳过")
            return False

        msg = EmailMessage()
        msg["Subject"] = f"{self.subject_prefix}{title}" if self.subject_prefix else title
        msg["From"] = self.username
        msg["To"] = ", ".join(self.recipients)
        msg.set_content(desp or title)

        try:
            if self.use_ssl:
                server = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout)
            else:
                server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
                server.starttls()
            with server:
                server.login(self.username, self.password)
                server.send_message(msg)
            logger.info("邮件已发送 → %s : %s", ", ".join(self.recipients), msg["Subject"])
            return True
        except Exception as e:
            logger.warning("邮件发送失败: %s", e)
            return False


def _parse_hhmm(value: str):
    try:
        hh, mm = value.strip().split(":")
        return dtime(int(hh), int(mm))
    except Exception:
        return None


def in_quiet_hours(spec: str, now: datetime | None = None) -> bool:
    """判断是否处于静音时段，支持 "23:30-07:30" 这种跨午夜区间。"""
    if not spec or "-" not in spec:
        return False
    start_s, end_s = spec.split("-", 1)
    start, end = _parse_hhmm(start_s), _parse_hhmm(end_s)
    if not start or not end:
        return False
    now_t = (now or datetime.now()).time()
    if start <= end:
        return start <= now_t <= end
    return now_t >= start or now_t <= end


def _ps_encoded(script: str) -> str:
    """把 PowerShell 脚本编码成 UTF-16LE base64，绕开引号与编码问题。"""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


class SoundNotifier:
    """本机发声告警：蜂鸣 + 中文语音播报。不需要任何凭据。"""

    def __init__(self, config: dict | None = None):
        cfg = ((config or {}).get("notify") or {}).get("sound") or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.max_seconds = int(cfg.get("max_seconds", 20) or 20)
        self.quiet_seconds = int(cfg.get("quiet_seconds", 3) or 3)
        self.quiet_hours = str(cfg.get("quiet_hours", "") or "")
        self.volume = int(cfg.get("volume", 60) or 60)
        self.speak_text = str(cfg.get("speak_text", "") or "")
        self.use_beep = bool(cfg.get("beep", True))
        self.beep_count = max(1, int(cfg.get("beep_count", 4) or 4))
        self.beep_ms = max(50, int(cfg.get("beep_ms", 250) or 250))
        self.beep_freq = int(cfg.get("beep_freq", 1100) or 1100)
        self.beep_freq_alt = int(cfg.get("beep_freq_alt", 1500) or 1500)
        self.gap_seconds = max(0.0, float(cfg.get("gap_seconds", 0.2) or 0.2))
        self._thread: threading.Thread | None = None
        self._active_priority = 0
        self._stop = threading.Event()
        self._proc = None

    def send(
        self,
        title: str,
        desp: str = "",
        seconds: int | None = None,
        speak_text: str | None = None,
        priority: int = 0,
    ) -> bool:
        """接口与其它通道一致；立刻返回，响铃在后台线程里跑。

        seconds / speak_text / priority 由分发层按事件类型传入。
        priority 更高的事件会抢占正在播放的响声；同优先级或更低的会被跳过。
        """
        if not self.enabled:
            logger.info("声音通道已关闭，跳过")
            return False
        if self._thread and self._thread.is_alive():
            if priority > self._active_priority:
                logger.info(
                    "更高优先级通知到达（%s > %s），抢占当前响声",
                    priority,
                    self._active_priority,
                )
                self._preempt()
            else:
                logger.info(
                    "上一轮声响仍在播（优先级 %s ≥ %s），跳过本次",
                    self._active_priority,
                    priority,
                )
                return True

        quiet = in_quiet_hours(self.quiet_hours)
        base = int(seconds) if seconds else self.max_seconds
        duration = min(base, self.quiet_seconds) if quiet else base
        text = self._render_text(title, desp, speak_text)

        self._stop.clear()
        self._active_priority = priority
        self._thread = threading.Thread(
            target=self._play, args=(text, duration), daemon=True
        )
        self._thread.start()
        logger.info(
            "声音通道已触发（%s，约 %s 秒）: %s",
            "静音时段" if quiet else "常规",
            duration,
            text,
        )
        return True

    def _render_text(self, title: str, desp: str, speak_text: str | None = None) -> str:
        template = speak_text if speak_text is not None else self.speak_text
        if template:
            try:
                return template.format(title=title, desp=desp)
            except Exception:
                return template
        return title

    def _preempt(self):
        """打断当前响声：先杀掉正在发声的 PowerShell，再等播放线程退出。"""
        self._stop.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)
        self._thread = None

    def _play(self, text: str, seconds: int):
        deadline = time.time() + max(1, seconds)
        try:
            while time.time() < deadline and not self._stop.is_set():
                if self.use_beep:
                    self._beep()
                if self._stop.is_set():
                    break
                self._speak(text, timeout=max(5, int(seconds) + 5))
                time.sleep(self.gap_seconds)
        except Exception as e:
            logger.warning("响声循环异常: %s", e)
        finally:
            self._active_priority = 0

    def _beep(self):
        if winsound is None:
            return
        try:
            for i in range(self.beep_count):
                freq = self.beep_freq if i % 2 == 0 else self.beep_freq_alt
                winsound.Beep(freq, self.beep_ms)
        except Exception as e:
            logger.warning("蜂鸣失败（可能没有音频设备或被静音）: %s", e)

    def _speak(self, text: str, timeout: int = 25):
        if not text:
            return
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if not ps:
            logger.warning("找不到 powershell，语音播报跳过")
            return
        vol = max(0, min(100, self.volume))
        script = (
            "Add-Type -AssemblyName System.Speech;"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            f"$s.Volume = {vol};"
            "$v = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'zh*' } | Select-Object -First 1;"
            "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) };"
            "$s.Speak([string]$env:DSH_SPEAK_TEXT)"
        )
        env = dict(os.environ)
        env["DSH_SPEAK_TEXT"] = text
        try:
            proc = subprocess.Popen(
                [ps, "-NoProfile", "-NonInteractive", "-EncodedCommand", _ps_encoded(script)],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self._proc = proc
            try:
                _, err_out = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                logger.warning("语音播报超时（%s 秒）", timeout)
                return
            if not self._stop.is_set() and proc.returncode not in (0, None):
                err = (err_out or b"").decode("utf-8", "ignore").strip()[:200]
                logger.warning("语音播报失败: %s", err)
        except Exception as e:
            logger.warning("语音播报异常: %s", e)
        finally:
            self._proc = None


class NotifierHub:
    """按事件类型把通知分发给各个通道（微信 / 声音，邮箱后续接入）。"""

    def __init__(self, config: dict | None = None, *, enable_wechat=True, enable_sound=True, enable_email=True):
        config = config or {}
        self.config = config
        self.routing = {k: dict(v) for k, v in DEFAULT_ROUTING.items()}
        for ev, spec in (((config.get("notify") or {}).get("routing")) or {}).items():
            self.routing.setdefault(ev, {}).update(spec or {})

        key = str(config.get("serverchan_send_key") or "").strip()
        if key and enable_wechat:
            self.wechat = ServerChanNotifier(key)
        else:
            self.wechat = None
            if not key:
                logger.warning("未配置 serverchan_send_key，微信通道已跳过")
        self.sound = SoundNotifier(config) if enable_sound else None
        self.email = EmailNotifier(config) if enable_email else None
        if self.email is not None and not self.email.usable():
            logger.info("邮件通道未就绪，已跳过（检查 config.local.yaml 的 email 段）")

    def dispatch(self, event: str, title: str, desp: str = "") -> dict:
        spec = self.routing.get(event)
        if spec is None:
            logger.warning("未知通知事件 %s，按 sale 处理", event)
            spec = self.routing.get("sale") or {}

        result = {"event": event, "wechat": None, "sound": None, "email": None}

        if spec.get("wechat") and self.wechat:
            result["wechat"] = self.wechat.send(title, desp)

        if spec.get("sound") and self.sound:
            result["sound"] = self.sound.send(
                title,
                desp,
                seconds=spec.get("sound_seconds"),
                speak_text=spec.get("speak_text"),
                priority=int(spec.get("priority", 0) or 0),
            )

        if spec.get("email") and self.email:
            result["email"] = self.email.send(title, desp)

        if result["wechat"] is None and result["sound"] is None and result["email"] is None:
            logger.warning("通知[%s] 仅记日志、不推送：%s | %s", event, title, desp)
        else:
            logger.info(
                "通知分发[%s] 微信=%s 声音=%s 邮件=%s",
                event,
                result["wechat"],
                result["sound"],
                result["email"],
            )
        return result


def _load_config(path: str = "config.yaml") -> dict:
    from config_loader import load_config as _load

    return _load(path)


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    argv = sys.argv[1:]
    cfg = _load_config()

    if "--test" in argv:
        idx = argv.index("--test")
        target = argv[idx + 1] if len(argv) > idx + 1 else "sound"
        if target == "route":
            hub = NotifierHub(cfg)
            hub.wechat = None
            hub.sound = None
            for ev in ("sale", "seat_ok", "seat_fail", "error"):
                print(ev, "->", hub.routing.get(ev))
        elif target == "sound":
            s = SoundNotifier(cfg)
            test_secs = int((((cfg.get("notify") or {}).get("sound") or {}).get("test_seconds", 3)) or 3)
            s.quiet_hours = ""  # 自检时忽略静音时段
            s.max_seconds = min(s.max_seconds, test_secs)
            ok = s.send("声音通道自检", "听到蜂鸣和这句中文播报，就说明声音通道可用。")
            print("已触发:", ok)
            time.sleep(s.max_seconds + 4)
        elif target == "email":
            e = EmailNotifier(cfg)
            print(
                "就绪:", e.usable(),
                "| 收件人:", e.recipients,
                "| 服务器:", "%s:%s ssl=%s" % (e.host, e.port, e.use_ssl),
            )
            ok = e.send("邮件通道自检", "收到这封邮件说明 Gmail 通道可用。\n\n来自 maoyan-monitor。")
            print("发送结果:", ok)
        else:
            print(f"未知测试目标: {target}（当前支持: sound / route / email）")
    else:
        print("用法: python notifier.py --test sound | --test route")