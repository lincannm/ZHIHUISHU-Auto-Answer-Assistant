import logging
import random
import time
from pathlib import Path

from cnocr import CnOcr
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from .answer_context import build_answer_prompt, get_course_name
from .console import log_message
from .model import get_model, should_repeat_answers


logging.getLogger("selenium").setLevel(logging.WARNING)

ocr = CnOcr()
model = get_model()
REPEAT_UNTIL_DUPLICATE = should_repeat_answers()
QUESTION_XPATH = '//div[contains(@class, "examPaper_subject")]'
ANSWER_OPTION_XPATH = './/div[contains(@class, "label") and contains(@class, "clearfix")]'
NEXT_BUTTON_XPATH = '//button[contains(@class, "el-button--primary") and contains(@class, "is-plain")]'
SUBMIT_BUTTON_XPATH = '//button[contains(@class, "btnStyleXSumit")]'
SUBMIT_CONFIRM_DIALOG_XPATH = '//div[contains(@class, "el-message-box__wrapper")]'
ROOT_DIR = Path(__file__).resolve().parent.parent
QUESTION_SCREENSHOT_PATH = ROOT_DIR / "data" / "question.png"


def error_handler(func):
    def wrapper(*args, **kwargs):
        while True:
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                log_message(f"函数 {func.__name__} 发生错误: {exc}")
                input("请修复错误并按回车键继续...")

    return wrapper


def text_ocr(image=QUESTION_SCREENSHOT_PATH):
    image_path = Path(image)
    ocr_results = ocr.ocr(str(image_path))
    extracted_text = "\n".join(
        [item["text"] for item in ocr_results if item["text"].strip()]
    )
    return extracted_text


def get_answer_with_attempts(question, course_name=""):
    prompt = build_answer_prompt(question, course_name)
    if not REPEAT_UNTIL_DUPLICATE:
        cur_answer = model.get_response(prompt)
        return cur_answer, [cur_answer]

    answer_attempts = []
    answer_list = []
    while True:
        cur_answer = model.get_response(prompt)
        answer_attempts.append(cur_answer)
        if cur_answer in answer_list:
            return cur_answer, answer_attempts
        answer_list.append(cur_answer)


def log_answer_attempts(answer_attempts):
    for index, cur_answer in enumerate(answer_attempts, start=1):
        log_message(f"大模型第{index}次输出：{cur_answer}")


def get_answer(question, course_name=""):
    final_answer, _ = get_answer_with_attempts(question, course_name)
    return final_answer


def capture_question_text(question_element, image_path=QUESTION_SCREENSHOT_PATH):
    image_path = Path(image_path)
    image_path.parent.mkdir(parents=True, exist_ok=True)
    question_element.screenshot(str(image_path))
    return text_ocr(image_path)


def solve_question_element(question_element, course_name="", image_path=QUESTION_SCREENSHOT_PATH):
    question_text = capture_question_text(question_element, image_path)
    answer, _ = get_answer_with_attempts(question_text, course_name)
    return question_text, answer


def _scroll_question_into_view(driver, question_element):
    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center'});",
        question_element,
    )


def _is_question_in_viewport(driver, question_element):
    return driver.execute_script(
        """
        const rect = arguments[0].getBoundingClientRect();
        return rect.width > 0 &&
            rect.height > 0 &&
            rect.bottom > 0 &&
            rect.top < window.innerHeight;
        """,
        question_element,
    )


def _get_question_center_distance(driver, question_element):
    return driver.execute_script(
        """
        const rect = arguments[0].getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) {
            return null;
        }
        const viewportCenter = window.innerHeight / 2;
        const elementCenter = rect.top + rect.height / 2;
        return Math.abs(elementCenter - viewportCenter);
        """,
        question_element,
    )


def _has_question_layout_box(driver, question_element):
    return driver.execute_script(
        """
        const rect = arguments[0].getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
        """,
        question_element,
    )


def get_question_element(driver, index, timeout=20, scroll=True):
    wait = WebDriverWait(driver, timeout)

    def find_target_question(current_driver):
        question_elements = current_driver.find_elements(By.XPATH, QUESTION_XPATH)
        if len(question_elements) <= index:
            return None

        question_element = question_elements[index]
        if question_element.is_displayed():
            return question_element
        return None

    question_element = wait.until(find_target_question)
    if scroll:
        _scroll_question_into_view(driver, question_element)
    return question_element


def get_question_count(driver):
    return len(driver.find_elements(By.XPATH, QUESTION_XPATH))


def get_viewport_question_elements(driver):
    question_elements = driver.find_elements(By.XPATH, QUESTION_XPATH)
    return [
        question_element
        for question_element in question_elements
        if question_element.is_displayed()
        and _is_question_in_viewport(driver, question_element)
    ]


def get_viewport_question_count(driver):
    return len(get_viewport_question_elements(driver))


def get_viewport_question_element(driver, visible_index, timeout=20):
    wait = WebDriverWait(driver, timeout)

    def find_target_question(current_driver):
        question_elements = get_viewport_question_elements(current_driver)
        if len(question_elements) <= visible_index:
            return None
        return question_elements[visible_index], visible_index + 1

    return wait.until(find_target_question)


def get_current_question_element(driver, timeout=20):
    wait = WebDriverWait(driver, timeout)

    def find_target_question(current_driver):
        best_match = None
        best_distance = None
        question_elements = current_driver.find_elements(By.XPATH, QUESTION_XPATH)
        for question_index, question_element in enumerate(question_elements, start=1):
            if not question_element.is_displayed():
                continue
            if not _has_question_layout_box(current_driver, question_element):
                continue
            distance = _get_question_center_distance(current_driver, question_element)
            if distance is None:
                continue
            if best_distance is None or distance < best_distance:
                best_match = (question_element, question_index)
                best_distance = distance
        return best_match

    return wait.until(find_target_question)


def get_next_button(driver, timeout=20):
    wait = WebDriverWait(driver, timeout)

    def find_next_button(current_driver):
        buttons = current_driver.find_elements(By.XPATH, NEXT_BUTTON_XPATH)
        visible_buttons = [button for button in buttons if button.is_displayed()]
        if visible_buttons:
            return visible_buttons[-1]
        return None

    return wait.until(find_next_button)


def get_submit_button(driver, timeout=20):
    wait = WebDriverWait(driver, timeout)

    def find_submit_button(current_driver):
        buttons = current_driver.find_elements(By.XPATH, SUBMIT_BUTTON_XPATH)
        visible_buttons = [
            button for button in buttons if button.is_displayed() and button.is_enabled()
        ]
        if visible_buttons:
            return visible_buttons[-1]
        return None

    return wait.until(find_submit_button)


def get_submit_confirm_button(driver, timeout=20):
    wait = WebDriverWait(driver, timeout)

    def find_submit_confirm_button(current_driver):
        dialogs = current_driver.find_elements(By.XPATH, SUBMIT_CONFIRM_DIALOG_XPATH)
        for dialog in dialogs:
            if not dialog.is_displayed():
                continue

            buttons = dialog.find_elements(
                By.XPATH,
                './/button[contains(@class, "el-button--primary")]',
            )
            for button in buttons:
                if (
                    button.is_displayed()
                    and button.is_enabled()
                    and button.text.strip() == "确定"
                ):
                    return button
        return None

    return wait.until(find_submit_confirm_button)


def apply_answer(question_element, answer):
    if "对" in answer or "错" in answer:
        answer_elements = question_element.find_elements(By.XPATH, ANSWER_OPTION_XPATH)
        for answer_element in answer_elements:
            if answer in answer_element.text.strip():
                answer_element.click()
                time.sleep(random.uniform(0.2, 0.5))
                break
        return

    if "," in answer:
        answer_indexes = [(ord(item) - ord("A")) for item in answer.split(",")]
    else:
        answer_indexes = [(ord(answer) - ord("A"))]

    option_elements = question_element.find_elements(By.XPATH, ANSWER_OPTION_XPATH)
    for answer_index in answer_indexes:
        option_elements[answer_index].click()
        time.sleep(random.uniform(0.2, 0.5))


@error_handler
def answer(driver, index, course_name=""):
    question_element = get_question_element(driver, index)
    question_text = capture_question_text(question_element)
    log_message(f"第{index + 1}题：{question_text}")
    final_answer, answer_attempts = get_answer_with_attempts(question_text, course_name)
    log_answer_attempts(answer_attempts)
    log_message(f"最终答案：{final_answer}")
    apply_answer(question_element, final_answer)


def auto_answer(driver):
    course_name = get_course_name(driver)
    if course_name:
        log_message(f"课程名称：{course_name}")
    else:
        log_message("未识别到课程名称，将仅按题目内容提问。")

    index = 0
    while True:
        answer(driver, index, course_name)
        next_button = get_next_button(driver)
        if next_button.text.strip() == "保存":
            submit_button = get_submit_button(driver)
            submit_button.click()
            time.sleep(random.uniform(0.5, 1))
            confirm_button = get_submit_confirm_button(driver)
            confirm_button.click()
            log_message("提交成功")
            return
        next_button.click()
        time.sleep(random.uniform(0.5, 1))
        index += 1
