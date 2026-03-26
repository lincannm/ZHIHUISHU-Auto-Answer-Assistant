import json
import os
import random
import shutil
import threading
import time
from collections import defaultdict
from pathlib import Path
from queue import Queue
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.chrome.service import Service

from .console import log_message


ROOT_DIR = Path(__file__).resolve().parent.parent
COOKIE_STORE_PATH = ROOT_DIR / "data" / "zhihuishu_cookies.json"
COOKIE_FIELDS = {"name", "value", "path", "domain", "secure", "httpOnly", "expiry", "sameSite"}
DEFAULT_DRIVER_START_TIMEOUT = 25


def _sleep(min_seconds=0.5, max_seconds=2):
    time.sleep(random.uniform(min_seconds, max_seconds))


def _get_target_parts(target_url):
    parsed = urlparse(target_url)
    scheme = parsed.scheme or "https"
    host = parsed.hostname or ""
    return scheme, host


def _is_cookie_expired(cookie):
    expiry = cookie.get("expiry")
    if expiry is None:
        return False

    try:
        return int(expiry) <= int(time.time())
    except (TypeError, ValueError):
        return False


def _normalize_cookie(cookie):
    normalized = {
        key: value
        for key, value in cookie.items()
        if key in COOKIE_FIELDS and value not in (None, "")
    }

    expiry = normalized.get("expiry")
    if expiry is not None:
        try:
            normalized["expiry"] = int(expiry)
        except (TypeError, ValueError):
            normalized.pop("expiry", None)

    same_site = normalized.get("sameSite")
    if same_site not in {"Strict", "Lax", "None"}:
        normalized.pop("sameSite", None)

    return normalized


def _load_cookie_store():
    if not COOKIE_STORE_PATH.exists():
        return []

    try:
        with COOKIE_STORE_PATH.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except (json.JSONDecodeError, OSError):
        return []

    cookies = payload.get("cookies", [])
    if not isinstance(cookies, list):
        return []

    return [cookie for cookie in cookies if isinstance(cookie, dict) and not _is_cookie_expired(cookie)]


def _group_cookies_by_host(cookies, fallback_host):
    grouped = defaultdict(list)
    for cookie in cookies:
        host = cookie.get("domain", "").lstrip(".") or fallback_host
        if not host:
            continue
        grouped[host].append(cookie)
    return grouped


def _looks_like_login_page(current_url):
    parsed = urlparse(current_url)
    host = parsed.hostname or ""
    path = parsed.path.lower()
    return "passport.zhihuishu.com" in host or "login" in path


def _first_existing_path(candidates):
    for candidate in candidates:
        if not candidate:
            continue

        path = Path(candidate).expanduser()
        if path.exists():
            return path
    return None


def _resolve_chrome_binary_path():
    env_candidates = [
        os.getenv("ZHIHUISHU_CHROME_BINARY"),
        os.getenv("CHROME_BINARY"),
        os.getenv("GOOGLE_CHROME_BIN"),
    ]
    common_candidates = [
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe",
    ]
    return _first_existing_path([*env_candidates, *common_candidates])


def _resolve_chromedriver_path():
    env_candidates = [
        os.getenv("ZHIHUISHU_CHROMEDRIVER"),
        os.getenv("CHROMEDRIVER"),
    ]
    common_candidates = [
        ROOT_DIR / "chromedriver.exe",
        ROOT_DIR / "drivers" / "chromedriver.exe",
        ROOT_DIR / "data" / "chromedriver.exe",
    ]
    resolved = _first_existing_path([*env_candidates, *common_candidates])
    if resolved:
        return resolved

    in_path = shutil.which("chromedriver")
    if in_path:
        return Path(in_path)
    return None


def _build_driver_timeout_message(chromedriver_path, chrome_binary_path, timeout_seconds):
    location_lines = [
        f"chromedriver: {chromedriver_path or '未找到'}",
        f"chrome.exe: {chrome_binary_path or '未找到'}",
    ]
    locations = "；".join(location_lines)
    return (
        f"浏览器启动超过 {timeout_seconds} 秒，已中止等待。"
        f"当前检测结果：{locations}。"
        "如果本机无法通过 Selenium Manager 自动下载驱动，"
        "请下载与 Chrome 版本匹配的 chromedriver.exe，放到仓库根目录、drivers/ 或 data/ 目录，"
        "或者设置环境变量 ZHIHUISHU_CHROMEDRIVER 后重试。"
    )


def _create_driver_with_timeout(factory, timeout_seconds, timeout_message):
    result_queue = Queue(maxsize=1)

    def target():
        try:
            result_queue.put(("driver", factory()))
        except Exception as exc:
            result_queue.put(("error", exc))

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        raise RuntimeError(timeout_message)
    if result_queue.empty():
        raise RuntimeError("浏览器启动失败：驱动进程已退出，但未返回可用结果。")

    result_type, value = result_queue.get()
    if result_type == "error":
        raise RuntimeError(f"浏览器启动失败: {value}") from value
    return value


def create_driver():
    options = webdriver.ChromeOptions()
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    # selenium尝试连接https网站时会报SSL handshake failed, 加上以下两行代码可以忽略证书错误
    options.add_argument("--ignore-certificate-errors")
    # 设置日志级别为3, 仅记录警告和错误
    options.add_argument("--log-level=3")
    chrome_binary_path = _resolve_chrome_binary_path()
    if chrome_binary_path:
        options.binary_location = str(chrome_binary_path)

    chromedriver_path = _resolve_chromedriver_path()
    if chromedriver_path:
        log_message(f"正在启动浏览器，使用本地驱动：{chromedriver_path}")
    else:
        log_message(
            "正在启动浏览器，未检测到本地 chromedriver，将尝试使用 Selenium Manager 自动获取驱动..."
        )

    service = Service(executable_path=str(chromedriver_path)) if chromedriver_path else None

    def factory():
        if service:
            return webdriver.Chrome(service=service, options=options)
        return webdriver.Chrome(options=options)

    driver = _create_driver_with_timeout(
        factory,
        DEFAULT_DRIVER_START_TIMEOUT,
        _build_driver_timeout_message(
            chromedriver_path,
            chrome_binary_path,
            DEFAULT_DRIVER_START_TIMEOUT,
        ),
    )
    try:
        driver.execute_cdp_cmd("Network.enable", {})
    except Exception:
        pass
    return driver


def save_login_state(driver):
    if _looks_like_login_page(driver.current_url):
        log_message("当前仍处于登录页，已跳过登录状态保存。")
        return

    cookies = driver.get_cookies()
    if not cookies:
        log_message("当前页面未读取到可保存的 cookie，已跳过登录状态持久化。")
        return

    COOKIE_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": int(time.time()),
        "current_url": driver.current_url,
        "cookies": cookies,
    }
    with COOKIE_STORE_PATH.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)

    log_message(f"已保存登录状态到 {COOKIE_STORE_PATH.name}")


def restore_login_state(driver, target_url):
    cookies = _load_cookie_store()
    if not cookies:
        return False

    scheme, fallback_host = _get_target_parts(target_url)
    grouped_cookies = _group_cookies_by_host(cookies, fallback_host)
    added_count = 0

    for host, domain_cookies in grouped_cookies.items():
        try:
            driver.get(f"{scheme}://{host}/")
            _sleep(0.2, 0.6)
        except Exception:
            continue

        for cookie in domain_cookies:
            normalized = _normalize_cookie(cookie)
            if not normalized:
                continue
            try:
                driver.add_cookie(normalized)
                added_count += 1
            except Exception:
                continue

    if added_count == 0:
        return False

    driver.get(target_url)
    _sleep()

    if _looks_like_login_page(driver.current_url):
        return False

    log_message(f"已恢复本地登录状态，载入 {added_count} 个 cookie。")
    return True


def get_authenticated_driver(target_url):
    driver = create_driver()
    if restore_login_state(driver, target_url):
        return driver

    driver.get(target_url)
    _sleep()
    input("未检测到可用登录状态，请登录后按回车继续...")
    save_login_state(driver)
    return driver
