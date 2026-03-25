import json
import random
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from selenium import webdriver


ROOT_DIR = Path(__file__).resolve().parent
COOKIE_STORE_PATH = ROOT_DIR / "data" / "zhihuishu_cookies.json"
COOKIE_FIELDS = {"name", "value", "path", "domain", "secure", "httpOnly", "expiry", "sameSite"}


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


def create_driver():
    options = webdriver.ChromeOptions()
    # selenium尝试连接https网站时会报SSL handshake failed, 加上以下两行代码可以忽略证书错误
    options.add_argument("--ignore-certificate-errors")
    # 设置日志级别为3, 仅记录警告和错误
    options.add_argument("--log-level=3")
    return webdriver.Chrome(options=options)


def save_login_state(driver):
    if _looks_like_login_page(driver.current_url):
        print("当前仍处于登录页，已跳过登录状态保存。")
        return

    cookies = driver.get_cookies()
    if not cookies:
        print("当前页面未读取到可保存的 cookie，已跳过登录状态持久化。")
        return

    COOKIE_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": int(time.time()),
        "current_url": driver.current_url,
        "cookies": cookies,
    }
    with COOKIE_STORE_PATH.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)

    print(f"已保存登录状态到 {COOKIE_STORE_PATH.name}")


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

    print(f"已恢复本地登录状态，载入 {added_count} 个 cookie。")
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
