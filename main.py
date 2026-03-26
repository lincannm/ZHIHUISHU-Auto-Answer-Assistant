from core.browser_session import get_authenticated_session, save_login_state
from core.console import log_message
from core.workflows import MODE_LABELS, normalize_mode, run_workflow


MODE_PROMPT = """请选择运行模式：
1. 手动模式
2. 单个答题页
3. 答题列表页
"""


def prompt_mode():
    while True:
        log_message(MODE_PROMPT)
        raw_mode = input("请输入模式编号或名称：").strip()
        try:
            return normalize_mode(raw_mode)
        except ValueError:
            log_message("模式无效，请输入 1 / 2 / 3，或输入对应模式名称。")


def prompt_url():
    while True:
        url = input("请输入页面链接：").strip()
        if url:
            return url
        log_message("链接不能为空，请重新输入。")


def run_application(mode, url):
    normalized_mode = normalize_mode(mode)
    log_message(f"当前模式：{MODE_LABELS[normalized_mode]}")
    session = get_authenticated_session(url)
    try:
        run_workflow(normalized_mode, session.page)
        if normalized_mode != "manual":
            input("流程结束，按回车退出...")
    finally:
        save_login_state(session)
        session.close()


def main():
    mode = prompt_mode()
    url = prompt_url()
    run_application(mode, url)


if __name__ == "__main__":
    main()
