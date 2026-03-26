from core.answer_context import get_course_name
from core.browser_session import get_authenticated_driver, save_login_state
from core.question_flow import (
    capture_question_text,
    get_answer,
    get_current_question_element,
    get_question_count,
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
    print(f"{label}：")
    print(question_text)
    return resolved_number, total_count, question_text


def _ask_question(driver, course_name="", question_number=None):
    resolved_number, total_count, question_text = _show_question(driver, question_number)
    answer = get_answer(question_text, course_name)
    print(f"答案：{answer}")
    return resolved_number, total_count, question_text, answer


def manual_mode(driver):
    course_name = get_course_name(driver)
    if course_name:
        print(f"课程名称：{course_name}")
    else:
        print("未识别到课程名称，将仅按题目内容提问。")
    print(HELP_TEXT)

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
                print(HELP_TEXT)
                continue

            if action in {"course", "课程"}:
                course_name = get_course_name(driver)
                if course_name:
                    print(f"课程名称：{course_name}")
                else:
                    print("当前页面未识别到课程名称。")
                continue

            if action in {"list", "count", "题数"}:
                _, current_number, total_count = _get_question_progress(driver)
                print(f"当前显示第{current_number}题，共{total_count}题。这个页面一次只显示一道题。")
                continue

            if action in {"show", "看题"}:
                question_number = _parse_question_number(command_parts)
                _show_question(driver, question_number)
                continue

            if action in {"ask", "答题"}:
                question_number = _parse_question_number(command_parts)
                _ask_question(driver, course_name, question_number)
                continue

            print("未知命令，输入 help 查看可用命令。")
        except Exception as exc:
            print(f"命令执行失败：{exc}")


def main(url):
    driver = get_authenticated_driver(url)
    try:
        manual_mode(driver)
    finally:
        save_login_state(driver)
        driver.quit()


if __name__ == "__main__":
    url = input("请输入题目链接：")
    main(url)
