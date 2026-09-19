import logging
import time
from pathlib import Path

import yaml

from maoyan_client import MaoyanClient
from name_fetcher import fetch_cinema_name, fetch_movie_name
from notifier import NotifierHub
from parser import parse_available_sessions
from probe import ShowtimeProbe
from resolver import build_combined_url
from seat_selector import SeatSelector
from showtime_selector import _parse_date, select_date
from state import State

# 确保日志目录存在
Path("logs").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/maoyan.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def load_config():
    """读取 config.yaml，并用 config.local.yaml 覆盖（密钥放后者，已在 .gitignore 中）。"""
    from config_loader import load_config as _load

    return _load("config.yaml")


def get_cinemas(config: dict) -> list[dict]:
    """返回影院列表，兼容旧的 cinema_url 单影院配置和新版 cinemas 多影院配置。"""
    direct_url = (config.get("url") or "").strip()
    if direct_url and direct_url != "auto":
        return [{
            "url": direct_url,
            "name": (config.get("cinema_name") or "").strip(),
            "direct": True,
        }]

    cinemas = config.get("cinemas") or []
    if cinemas:
        result = []
        for item in cinemas:
            if not isinstance(item, dict):
                continue
            url = (item.get("url") or "").strip()
            if url:
                result.append({"url": url, "name": (item.get("name") or "").strip()})
        return result

    cinema_url = (config.get("cinema_url") or "").strip()
    if cinema_url:
        return [{
            "url": cinema_url,
            "name": (config.get("cinema_name") or "").strip(),
        }]
    return []


def validate_config(config: dict):
    """启动前检查关键配置，避免运行时才报错。"""
    url = (config.get("url") or "").strip()
    movie_url = (config.get("movie_url") or "").strip()

    if url and url != "auto":
        return

    missing = []
    if not movie_url:
        missing.append("movie_url")
    if not get_cinemas(config):
        missing.append("cinema_url / cinemas")

    if missing:
        raise ValueError(
            "配置不完整：请填写 " + "、".join(missing) +
            "。\n"
            "例如：\n"
            "movie_url: https://www.maoyan.com/films/123456\n"
            "cinemas:\n"
            "  - url: https://www.maoyan.com/cinemas/12345\n"
            "  - url: https://www.maoyan.com/cinemas/67890\n"
            "或者直接填写 url 二合一场次页。"
        )


def _now() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main():
    config = load_config()
    validate_config(config)

    notify = NotifierHub(config)
    probe = ShowtimeProbe(config)
    _pd = (config.get("preferred_date") or "").strip()
    _pd_obj = _parse_date(_pd) if _pd else None
    date_label = f"{_pd_obj.month}月{_pd_obj.day}" if _pd_obj else ""
    state = State()
    client = MaoyanClient(
        user_data_dir=config.get("user_data_dir", "./profile"),
        headless=config.get("headless", False),
    )

    client.start()

    cinemas = get_cinemas(config)
    movie_name = (config.get("movie_name") or "").strip()

    # 如果电影名/影院名为空，自动从 URL 页面获取
    try:
        if not movie_name and config.get("movie_url"):
            movie_name = fetch_movie_name(client.page, config["movie_url"])
            config["movie_name"] = movie_name
            logger.info("已自动获取电影名: %s", movie_name)
        for cinema in cinemas:
            if not cinema.get("name") and not cinema.get("direct"):
                cinema["name"] = fetch_cinema_name(client.page, cinema["url"])
                logger.info("已自动获取影院名: %s", cinema["name"])
    except Exception as e:
        logger.warning("自动获取名称失败，将继续使用现有配置: %s", e)

    logger.info("浏览器启动成功，开始监控电影: %s，影院数量: %d", movie_name or config.get("movie_name"), len(cinemas))

    try:
        while True:
            for cinema in cinemas:
                cinema_name = cinema.get("name") or cinema.get("url") or "未知影院"
                row = {"ts": _now(), "cinema": cinema_name}
                try:
                    # 1. 构造该影院对应的场次页 URL
                    if cinema.get("direct"):
                        target_url = cinema["url"]
                    else:
                        target_url = build_combined_url(config["movie_url"], cinema["url"])

                    row["url"] = target_url
                    logger.info("检查影院【%s】: %s", cinema_name, target_url)

                    # 2. 访问场次页
                    client.page.goto(
                        target_url,
                        wait_until="domcontentloaded",
                        timeout=30000,
                    )
                    logger.info("场次页加载完成: %s", cinema_name)
                    client.page.wait_for_timeout(1000)

                    # 2.1 如果指定了优先日期，先切换到该日期再检测
                    preferred_date = (config.get("preferred_date") or "").strip()
                    if preferred_date:
                        date_ok = select_date(client.page, preferred_date)
                        row["date_ok"] = date_ok
                        if not date_ok:
                            row["stage"] = "date_not_open"
                            logger.info("【%s】所选日期 %s 尚未开放购票/未排片，继续下一影院", cinema_name, preferred_date)
                            continue
                        client.page.wait_for_timeout(500)

                    # 2.2 关键校验：页面必须包含目标电影名，否则可能是猫眼忽略了 movieId
                    #     显示了影院当前上映的其他电影，绝不能误买
                    body_text = client.page.inner_text("body")
                    movie_name_now = movie_name or (config.get("movie_name") or "").strip()
                    movie_found = (not movie_name_now) or (movie_name_now in body_text)
                    row["movie_found"] = movie_found
                    row.update(probe.date_bar_facts(client.page, date_label))
                    if not movie_found:
                        row["stage"] = "movie_missing"
                        logger.info("【%s】页面未找到目标电影《%s》，跳过该影院", cinema_name, movie_name_now)
                        continue

                    # 3. 检测是否有可购买场次
                    sessions = parse_available_sessions(client.page, config)

                    row["sessions"] = len(sessions)
                    row["keywords"] = "|".join(str(x.get("keyword", "")) for x in sessions)[:80]
                    if not sessions:
                        row["stage"] = "no_sessions"
                        logger.info("【%s】暂未开售，继续下一影院", cinema_name)
                        continue

                    row["stage"] = "detected"

                    for session in sessions:
                        date_tag = preferred_date or "any"
                        key = f"{movie_name_now}|{cinema_name}|{date_tag}|{session['keyword']}"
                        
                        # 只有真正下单成功过的场次才永久跳过
                        if state.already_ordered(key):
                            logger.info("【%s】该场次已下单成功，跳过: %s", cinema_name, key)
                            continue
                        
                        # 没开自动占座时，通知过就不再重复处理
                        if not config.get("auto_select_seat") and state.already_notified(key):
                            logger.info("【%s】该场次已通知过，跳过重复通知: %s", cinema_name, key)
                            continue
                        
                        if state.already_notified(key):
                            logger.info("【%s】已通知过，继续尝试占座: %s", cinema_name, key)
                        else:
                            logger.info("【%s】检测到可购票: %s", cinema_name, session)
                            sent = notify.dispatch(
        "sale",
                                title=f"【猫眼开售提醒】{movie_name_now}",
                                desp=(
                                    f"影院：{cinema_name}\n"
                                    f"命中：{session['keyword']}\n"
                                    f"请尽快打开猫眼查看！"
                                ),
                            )
                            logger.info("【%s】开售通知分发结果: %s", cinema_name, sent)
                            state.mark_notified(key)
                            state.save()
                        
                        # 4. 可选：自动占座 + 下单
                        if config.get("auto_select_seat"):
                            cinema_config = {**config, "cinema_name": cinema_name}
                            selector = SeatSelector(client.page, cinema_config)
                            ok = selector.select()
                        
                            if ok:
                                row["stage"] = "seat_ok"
                                logger.info("【%s】自动占座/下单成功", cinema_name)
                                state.mark_ordered(key)
                                state.save()
                                notify.dispatch("seat_ok",
                                    title="【猫眼占座成功】请尽快支付",
                                    desp=(
                                        f"电影：{movie_name_now}\n"
                                        f"影院：{cinema_name}\n"
                                        f"最佳座位：{config.get('best_seat')}\n"
                                        f"已自动进入订单页，请立即去支付！"
                                    ),
                                )
                                # 一个影院成功后立即停止全部监控
                                logger.info("检测到成功下单，停止全部影院监控")
                                return
                        
                            row["stage"] = "seat_fail"
                            logger.warning("【%s】占座失败，本轮不算完成，下一轮继续重试: %s", cinema_name, key)
                            fail_key = f"{key}|seatfail-notified"
                            if not state.already_notified(fail_key):
                                notify.dispatch("seat_fail",
                                    title="【猫眼占座失败】脚本会自动重试",
                                    desp=(
                                        f"电影：{movie_name_now}\n"
                                        f"影院：{cinema_name}\n"
                                        f"最佳座位：{config.get('best_seat')}\n"
                                        f"脚本会在下一轮继续重试该场次；如你已在场，请手动抢票。"
                                    ),
                                )
                                state.mark_notified(fail_key)
                                state.save()

                except Exception as e:
                    row["stage"] = "exception"
                    row["error"] = str(e)[:120]
                    logger.exception("影院【%s】运行异常", cinema_name)
                    notify.dispatch("error", "【猫眼脚本异常】", str(e))
                finally:
                    probe.record(row)

            # 所有影院都检查完一轮后等待
            time.sleep(config.get("interval_seconds", 600))

    finally:
        client.close()


if __name__ == "__main__":
    main()
