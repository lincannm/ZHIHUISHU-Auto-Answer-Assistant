import random
import time

from .answer_context import get_course_name
from .console import log_message
from .question_flow import (
    auto_answer,
    capture_question_text,
    error_handler,
    get_answer_with_attempts,
    get_current_question_element,
    get_question_count,
    log_answer_attempts,
)


HELP_TEXT = """支持命令：
ask / 答题          回答当前正在显示的题目
ask / 答题 <n>      仅当当前显示的是第 n 题时回答，否则提示你先手动切题
show / 看题         只识别当前正在显示的题目，不调用 AI
show / 看题 <n>     仅当当前显示的是第 n 题时识别，否则提示你先手动切题
list / count / 题数 显示当前是第几题、总共有几题
course / 课程       显示当前识别到的课程名称
help / 帮助         显示帮助
quit / exit / 退出  退出手动模式
"""

MODE_ALIASES = {
    "1": "manual",
    "manual": "manual",
    "手动": "manual",
    "手动模式": "manual",
    "2": "onepage",
    "onepage": "onepage",
    "single": "onepage",
    "单页": "onepage",
    "答题页": "onepage",
    "单个答题页": "onepage",
    "单个测试页": "onepage",
    "3": "tests",
    "tests": "tests",
    "list": "tests",
    "答题列表": "tests",
    "答题列表页": "tests",
    "题目列表": "tests",
    "题目列表页": "tests",
    "测试列表": "tests",
    "测试列表页": "tests",
}

MODE_LABELS = {
    "manual": "手动模式",
    "onepage": "单个答题页",
    "tests": "答题列表页",
}


def normalize_mode(mode):
    normalized = MODE_ALIASES.get((mode or "").strip().lower())
    if normalized:
        return normalized

    normalized = MODE_ALIASES.get((mode or "").strip())
    if normalized:
        return normalized

    raise ValueError(f"不支持的模式：{mode}")


def _parse_question_number(command_parts):
    if len(command_parts) == 1:
        return None
    if len(command_parts) != 2:
        raise ValueError("命令格式错误，只支持一个题号参数。")

    raw_value = command_parts[1].strip()
    if not raw_value.isdigit():
        raise ValueError("题号必须是正整数。")

    question_number = int(raw_value)
    if question_number <= 0:
        raise ValueError("题号必须大于 0。")
    return question_number


def _get_question_progress(page):
    question_element, current_number = get_current_question_element(page)
    total_count = get_question_count(page)
    return question_element, current_number, total_count


def _resolve_target_question(page, question_number=None):
    question_element, current_number, total_count = _get_question_progress(page)
    if question_number is None or question_number == current_number:
        label = f"当前显示第{current_number}题（共{total_count}题）"
        return question_element, current_number, total_count, label

    raise ValueError(
        f"这个页面一次只显示一道题。现在显示的是第{current_number}题，共{total_count}题；"
        f"请先在浏览器里手动切到第{question_number}题，再执行命令。"
    )


def _show_question(page, question_number=None):
    question_element, resolved_number, total_count, label = _resolve_target_question(
        page,
        question_number,
    )
    question_text = capture_question_text(question_element)
    log_message(f"{label}：{question_text}")
    return resolved_number, total_count, question_text


def _ask_question(page, course_name="", question_number=None):
    resolved_number, total_count, question_text = _show_question(page, question_number)
    final_answer, answer_attempts = get_answer_with_attempts(question_text, course_name)
    log_answer_attempts(answer_attempts)
    log_message(f"最终答案：{final_answer}")
    return resolved_number, total_count, question_text, final_answer


def run_manual_mode(page):
    course_name = get_course_name(page)
    if course_name:
        log_message(f"课程名称：{course_name}")
    else:
        log_message("未识别到课程名称，将仅按题目内容提问。")
    log_message(HELP_TEXT)

    while True:
        raw_command = input("manual> ").strip()
        if not raw_command:
            continue

        command_parts = raw_command.split()
        action = command_parts[0].lower()

        try:
            if action in {"quit", "exit", "退出"}:
                return

            if action in {"help", "帮助"}:
                log_message(HELP_TEXT)
                continue

            if action in {"course", "课程"}:
                course_name = get_course_name(page)
                if course_name:
                    log_message(f"课程名称：{course_name}")
                else:
                    log_message("当前页面未识别到课程名称。")
                continue

            if action in {"list", "count", "题数"}:
                _, current_number, total_count = _get_question_progress(page)
                log_message(
                    f"当前显示第{current_number}题，共{total_count}题。这个页面一次只显示一道题。"
                )
                continue

            if action in {"show", "看题"}:
                question_number = _parse_question_number(command_parts)
                _show_question(page, question_number)
                continue

            if action in {"ask", "答题"}:
                question_number = _parse_question_number(command_parts)
                _ask_question(page, course_name, question_number)
                continue

            log_message("未知命令，输入 help 查看可用命令。")
        except Exception as exc:
            log_message(f"命令执行失败：{exc}")


def get_test_num(page):
    test_list = page.locator('xpath=//div[@id="examBox"]/div/ul/li')
    return test_list.count()


def _wait_for_exam_page(page, previous_pages, timeout=10):
    start_url = page.url
    deadline = time.time() + timeout
    while time.time() < deadline:
        current_pages = page.context.pages
        for candidate in current_pages:
            if all(candidate is not existing_page for existing_page in previous_pages):
                candidate.wait_for_load_state("domcontentloaded")
                return candidate, True

        if page.url != start_url:
            page.wait_for_load_state("domcontentloaded")
            return page, False

        time.sleep(0.2)

    return page, False


@error_handler
def run_tests_mode(page):
    while True:
        test_num = get_test_num(page)
        log_message(f"共有{test_num}个答题页待处理")
        if test_num == 0:
            log_message("暂无可处理的答题页")
            return

        todo_test = page.locator('xpath=//div[@id="examBox"]/div/ul/li').first
        start_button = todo_test.locator('xpath=.//a[@title="开始答题"]')
        previous_pages = list(page.context.pages)
        start_button.click()
        log_message("开始答题")
        time.sleep(random.uniform(3, 5))

        exam_page, opened_new_page = _wait_for_exam_page(page, previous_pages)
        auto_answer(exam_page)

        if opened_new_page and not exam_page.is_closed():
            exam_page.close()
        else:
            page.go_back(wait_until="domcontentloaded")

        page.bring_to_front()


def run_workflow(mode, page):
    normalized_mode = normalize_mode(mode)
    if normalized_mode == "manual":
        run_manual_mode(page)
        return
    if normalized_mode == "onepage":
        auto_answer(page)
        return
    if normalized_mode == "tests":
        run_tests_mode(page)
        return

    raise ValueError(f"不支持的模式：{mode}")
