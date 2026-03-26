import json
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from .console import log_message


ROOT_DIR = Path(__file__).resolve().parent.parent
LEGACY_COOKIE_STORE_PATH = ROOT_DIR / "data" / "zhihuishu_cookies.json"
STORAGE_STATE_PATH = ROOT_DIR / "data" / "zhihuishu_storage_state.json"
COOKIE_FIELDS = {"name", "value", "path", "domain", "secure", "httpOnly", "expiry", "sameSite"}
DEFAULT_BROWSER_START_TIMEOUT_MS = 25_000
DEFAULT_NAVIGATION_TIMEOUT_MS = 30_000
DEFAULT_ACTION_TIMEOUT_MS = 20_000


@dataclass
class BrowserSession:
    playwright: Playwright
    browser: Browser
    context: BrowserContext
    page: Page

    def close(self):
        try:
            self.context.close()
        finally:
            try:
                self.browser.close()
            finally:
                self.playwright.stop()


def _sleep(min_seconds=0.5, max_seconds=1.5):
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


def _resolve_browser_executable_path():
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


def _build_browser_start_error_message(browser_path, timeout_ms, error_text):
    seconds = timeout_ms / 1000
    return (
        f"浏览器启动失败，等待超过 {seconds:.0f} 秒或启动过程中报错。"
        f"当前检测到的浏览器路径：{browser_path or '未找到本机 Chrome，将尝试使用 Playwright 自带 Chromium'}。"
        f"原始错误：{error_text}。"
        "如果尚未安装 Playwright 浏览器，请先执行 `python -m playwright install chromium`；"
        "如需指定本机 Chrome，请设置环境变量 ZHIHUISHU_CHROME_BINARY。"
    )


def _build_launch_options(browser_path):
    launch_options = {
        "headless": False,
        "timeout": DEFAULT_BROWSER_START_TIMEOUT_MS,
        "args": [
            "--ignore-certificate-errors",
            "--disable-blink-features=AutomationControlled",
        ],
    }
    if browser_path:
        launch_options["executable_path"] = str(browser_path)
    return launch_options


def _create_browser():
    browser_path = _resolve_browser_executable_path()
    if browser_path:
        log_message(f"正在启动浏览器，使用本机 Chrome：{browser_path}")
    else:
        log_message("正在启动浏览器，未检测到本机 Chrome，将尝试使用 Playwright 自带 Chromium。")

    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.launch(**_build_launch_options(browser_path))
    except Exception as exc:
        playwright.stop()
        raise RuntimeError(
            _build_browser_start_error_message(
                browser_path,
                DEFAULT_BROWSER_START_TIMEOUT_MS,
                str(exc).strip() or exc.__class__.__name__,
            )
        ) from exc

    return playwright, browser


def _load_legacy_cookie_store():
    if not LEGACY_COOKIE_STORE_PATH.exists():
        return []

    try:
        with LEGACY_COOKIE_STORE_PATH.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except (json.JSONDecodeError, OSError):
        return []

    cookies = payload.get("cookies", [])
    if not isinstance(cookies, list):
        return []

    return [cookie for cookie in cookies if isinstance(cookie, dict) and not _is_cookie_expired(cookie)]


def _normalize_legacy_cookie(cookie, fallback_url):
    normalized = {
        key: value
        for key, value in cookie.items()
        if key in COOKIE_FIELDS and value not in (None, "")
    }

    domain = str(normalized.get("domain", "")).strip()
    path = str(normalized.get("path", "/")).strip() or "/"
    if domain:
        normalized["domain"] = domain
        normalized["path"] = path
    else:
        normalized["url"] = fallback_url
        normalized.pop("path", None)

    expiry = normalized.pop("expiry", None)
    if expiry is not None:
        try:
            normalized["expires"] = int(expiry)
        except (TypeError, ValueError):
            pass

    same_site = normalized.get("sameSite")
    if same_site not in {"Strict", "Lax", "None"}:
        normalized.pop("sameSite", None)

    return normalized


def _create_context(browser, target_url):
    context = None
    restored_from_storage = False

    if STORAGE_STATE_PATH.exists():
        try:
            context = browser.new_context(
                storage_state=str(STORAGE_STATE_PATH),
                ignore_https_errors=True,
            )
            restored_from_storage = True
        except Exception as exc:
            log_message(f"读取本地登录状态失败，将忽略并重新创建浏览器上下文：{exc}")

    if context is None:
        context = browser.new_context(ignore_https_errors=True)

        legacy_cookies = _load_legacy_cookie_store()
        if legacy_cookies:
            normalized_cookies = [
                _normalize_legacy_cookie(cookie, target_url)
                for cookie in legacy_cookies
            ]
            normalized_cookies = [cookie for cookie in normalized_cookies if cookie.get("name") and cookie.get("value")]
            if normalized_cookies:
                try:
                    context.add_cookies(normalized_cookies)
                    log_message(f"已导入旧版 Selenium 登录态，载入 {len(normalized_cookies)} 个 cookie。")
                except Exception as exc:
                    log_message(f"导入旧版 Selenium cookie 失败：{exc}")

    context.set_default_timeout(DEFAULT_ACTION_TIMEOUT_MS)
    context.set_default_navigation_timeout(DEFAULT_NAVIGATION_TIMEOUT_MS)
    return context, restored_from_storage


def create_browser_session(target_url):
    playwright, browser = _create_browser()
    context, restored_from_storage = _create_context(browser, target_url)
    page = context.new_page()

    try:
        page.goto(target_url, wait_until="domcontentloaded")
    except Exception:
        page.goto(target_url)

    _sleep()

    if restored_from_storage and not _looks_like_login_page(page.url):
        cookie_count = len(context.cookies())
        log_message(f"已恢复本地登录状态，载入 {cookie_count} 个 cookie。")

    return BrowserSession(
        playwright=playwright,
        browser=browser,
        context=context,
        page=page,
    )


def save_login_state(session):
    if _looks_like_login_page(session.page.url):
        log_message("当前仍处于登录页，已跳过登录状态保存。")
        return

    cookies = session.context.cookies()
    if not cookies:
        log_message("当前页面未读取到可保存的登录状态，已跳过持久化。")
        return

    STORAGE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    session.context.storage_state(path=str(STORAGE_STATE_PATH))
    log_message(f"已保存登录状态到 {STORAGE_STATE_PATH.name}（{len(cookies)} 个 cookie）")


def get_authenticated_session(target_url):
    session = create_browser_session(target_url)
    if not _looks_like_login_page(session.page.url):
        return session

    input("未检测到可用登录状态，请登录后按回车继续...")
    save_login_state(session)

    scheme, host = _get_target_parts(target_url)
    if scheme and host:
        session.page.goto(target_url, wait_until="domcontentloaded")
        _sleep()

    return session
