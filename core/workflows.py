import random
import time

from selenium.webdriver.common.by import By

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


def _get_question_progress(driver):
    question_element, current_number = get_current_question_element(driver)
    total_count = get_question_count(driver)
    return question_element, current_number, total_count


def _resolve_target_question(driver, question_number=None):
    question_element, current_number, total_count = _get_question_progress(driver)
    if question_number is None or question_number == current_number:
        label = f"当前显示第{current_number}题（共{total_count}题）"
        return question_element, current_number, total_count, label

    raise ValueError(
        f"这个页面一次只显示一道题。现在显示的是第{current_number}题，共{total_count}题；"
        f"请先在浏览器里手动切到第{question_number}题，再执行命令。"
    )


def _show_question(driver, question_number=None):
    question_element, resolved_number, total_count, label = _resolve_target_question(
        driver,
        question_number,
    )
    question_text = capture_question_text(question_element)
    log_message(f"{label}：{question_text}")
    return resolved_number, total_count, question_text


def _ask_question(driver, course_name="", question_number=None):
    resolved_number, total_count, question_text = _show_question(driver, question_number)
    final_answer, answer_attempts = get_answer_with_attempts(question_text, course_name)
    log_answer_attempts(answer_attempts)
    log_message(f"最终答案：{final_answer}")
    return resolved_number, total_count, question_text, final_answer


def run_manual_mode(driver):
    course_name = get_course_name(driver)
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
                course_name = get_course_name(driver)
                if course_name:
                    log_message(f"课程名称：{course_name}")
                else:
                    log_message("当前页面未识别到课程名称。")
                continue

            if action in {"list", "count", "题数"}:
                _, current_number, total_count = _get_question_progress(driver)
                log_message(
                    f"当前显示第{current_number}题，共{total_count}题。这个页面一次只显示一道题。"
                )
                continue

            if action in {"show", "看题"}:
                question_number = _parse_question_number(command_parts)
                _show_question(driver, question_number)
                continue

            if action in {"ask", "答题"}:
                question_number = _parse_question_number(command_parts)
                _ask_question(driver, course_name, question_number)
                continue

            log_message("未知命令，输入 help 查看可用命令。")
        except Exception as exc:
            log_message(f"命令执行失败：{exc}")


def get_test_num(driver):
    test_list = driver.find_elements(By.XPATH, '//div[@id="examBox"]/div/ul/li')
    return len(test_list)


@error_handler
def run_tests_mode(driver):
    while True:
        test_num = get_test_num(driver)
        log_message(f"共有{test_num}个答题页待处理")
        if test_num == 0:
            log_message("暂无可处理的答题页")
            return

        todo_test = driver.find_element(By.XPATH, '//div[@id="examBox"]/div/ul/li')
        start_button = todo_test.find_element(By.XPATH, './/a[@title="开始答题"]')
        start_button.click()
        log_message("开始答题")
        time.sleep(random.uniform(3, 5))

        current_window_handle = driver.current_window_handle
        window_handles = driver.window_handles
        driver.switch_to.window(window_handles[-1])
        auto_answer(driver)
        driver.switch_to.window(current_window_handle)


def run_workflow(mode, driver):
    normalized_mode = normalize_mode(mode)
    if normalized_mode == "manual":
        run_manual_mode(driver)
        return
    if normalized_mode == "onepage":
        auto_answer(driver)
        return
    if normalized_mode == "tests":
        run_tests_mode(driver)
        return

    raise ValueError(f"不支持的模式：{mode}")
