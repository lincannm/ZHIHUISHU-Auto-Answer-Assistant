import logging
import random
import time
import traceback
from collections import defaultdict
from pathlib import Path

from cnocr import CnOcr

from .answer_context import build_answer_prompt, get_course_name
from .console import log_message
from .model import get_model, should_repeat_answers


logging.getLogger("playwright").setLevel(logging.WARNING)

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
SAVE_ANSWER_ENDPOINT = "/answer/saveStudentAnswer"
_SAVE_ANSWER_RESPONSES = defaultdict(list)
_PAGE_RESPONSE_HANDLERS = {}


def error_handler(func):
    def wrapper(*args, **kwargs):
        while True:
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                error_text = str(exc).strip() or "无详细错误信息"
                log_message(f"函数 {func.__name__} 发生错误: {exc.__class__.__name__}: {error_text}")
                traceback_text = traceback.format_exc().strip()
                if traceback_text:
                    log_message(traceback_text)
                input("请修复错误并按回车键继续...")

    return wrapper


def text_ocr(image=QUESTION_SCREENSHOT_PATH):
    image_path = Path(image)
    ocr_results = ocr.ocr(str(image_path))
    extracted_text = "\n".join(
        [item["text"] for item in ocr_results if item["text"].strip()]
    )
    return extracted_text


def _normalize_question_text(text):
    lines = [line.strip() for line in str(text or "").splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines).strip()


def _wait_until(predicate, timeout=20, interval=0.2, timeout_message="等待页面元素超时。"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)

    raise RuntimeError(timeout_message)


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


def capture_question_text(question_element, image_path=QUESTION_SCREENSHOT_PATH, page=None):
    image_path = Path(image_path)
    image_path.parent.mkdir(parents=True, exist_ok=True)
    question_element.screenshot(path=str(image_path))
    return _normalize_question_text(text_ocr(image_path))


def solve_question_element(question_element, course_name="", image_path=QUESTION_SCREENSHOT_PATH):
    question_text = capture_question_text(question_element, image_path)
    answer, _ = get_answer_with_attempts(question_text, course_name)
    return question_text, answer


def _scroll_question_into_view(question_element):
    question_element.evaluate(
        "element => element.scrollIntoView({block: 'center'})"
    )


def _is_question_in_viewport(question_element):
    return question_element.evaluate(
        """
        element => {
            const rect = element.getBoundingClientRect();
            return rect.width > 0 &&
                rect.height > 0 &&
                rect.bottom > 0 &&
                rect.top < window.innerHeight;
        }
        """
    )


def _get_question_center_distance(question_element):
    return question_element.evaluate(
        """
        element => {
            const rect = element.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) {
                return null;
            }
            const viewportCenter = window.innerHeight / 2;
            const elementCenter = rect.top + rect.height / 2;
            return Math.abs(elementCenter - viewportCenter);
        }
        """
    )


def _has_question_layout_box(question_element):
    box = question_element.bounding_box()
    return bool(box and box.get("width", 0) > 0 and box.get("height", 0) > 0)


def _get_question_locator(page):
    return page.locator(f"xpath={QUESTION_XPATH}")


def _iter_question_elements(page):
    question_locator = _get_question_locator(page)
    return [question_locator.nth(index) for index in range(question_locator.count())]


def _find_current_question(page):
    best_match = None
    best_distance = None
    for question_index, question_element in enumerate(_iter_question_elements(page), start=1):
        if not question_element.is_visible():
            continue
        if not _has_question_layout_box(question_element):
            continue
        distance = _get_question_center_distance(question_element)
        if distance is None:
            continue
        if best_distance is None or distance < best_distance:
            best_match = (question_element, question_index)
            best_distance = distance
    return best_match


def _build_save_answer_response_handler(page):
    page_key = id(page)

    def handle_response(response):
        if SAVE_ANSWER_ENDPOINT in str(response.url):
            _SAVE_ANSWER_RESPONSES[page_key].append(response)

    return handle_response


def ensure_answer_response_listener(page):
    page_key = id(page)
    if page_key in _PAGE_RESPONSE_HANDLERS:
        return

    handler = _build_save_answer_response_handler(page)
    page.on("response", handler)
    _PAGE_RESPONSE_HANDLERS[page_key] = handler


def _get_save_answer_result_from_logs(page):
    for response in reversed(_SAVE_ANSWER_RESPONSES.get(id(page), [])):
        try:
            response.finished()
            response_data = response.json()
        except Exception:
            continue

        if isinstance(response_data, dict):
            return response_data
    return None


def clear_driver_network_logs(page):
    ensure_answer_response_listener(page)
    _SAVE_ANSWER_RESPONSES[id(page)].clear()


def get_question_element(page, index, timeout=20, scroll=True):
    def find_target_question():
        return _try_get_question_element_by_index(page, index, scroll=scroll)

    return _wait_until(
        find_target_question,
        timeout=timeout,
        timeout_message=f"等待第{index + 1}题出现超时。",
    )


def _try_get_question_element_by_index(page, index, scroll=True):
    question_elements = _iter_question_elements(page)
    if len(question_elements) <= index:
        return None

    question_element = question_elements[index]
    if not question_element.is_visible():
        return None
    if not _has_question_layout_box(question_element):
        return None
    if scroll:
        _scroll_question_into_view(question_element)
    return question_element


def get_question_count(page):
    return _get_question_locator(page).count()


def get_viewport_question_elements(page):
    question_elements = _iter_question_elements(page)
    return [
        question_element
        for question_element in question_elements
        if question_element.is_visible()
        and _is_question_in_viewport(question_element)
    ]


def get_viewport_question_count(page):
    return len(get_viewport_question_elements(page))


def get_viewport_question_element(page, visible_index, timeout=20):
    def find_target_question():
        question_elements = get_viewport_question_elements(page)
        if len(question_elements) <= visible_index:
            return None
        return question_elements[visible_index], visible_index + 1

    return _wait_until(
        find_target_question,
        timeout=timeout,
        timeout_message=f"等待当前视口第{visible_index + 1}题出现超时。",
    )


def get_current_question_element(page, timeout=20):
    return _wait_until(
        lambda: _find_current_question(page),
        timeout=timeout,
        timeout_message="等待当前题目出现超时。",
    )


def resolve_auto_question_element(page, index, timeout=20):
    indexed_question = _try_get_question_element_by_index(page, index)
    if indexed_question is not None:
        return indexed_question, index + 1

    log_message(
        f"未在页面中直接定位到第{index + 1}题，已改为识别当前显示的题目继续答题。"
    )
    current_question, current_number = get_current_question_element(page, timeout=timeout)
    return current_question, current_number or (index + 1)


def wait_for_question_change(page, previous_question_number, timeout=20):
    ensure_answer_response_listener(page)
    deadline = time.time() + timeout
    while time.time() < deadline:
        current_question = _find_current_question(page)
        if current_question:
            question_element, current_number = current_question
            if current_number != previous_question_number:
                return question_element, current_number

        save_answer_result = _get_save_answer_result_from_logs(page)
        if save_answer_result:
            status = str(save_answer_result.get("status", "")).strip()
            if status and status != "200":
                message = save_answer_result.get("msg") or str(save_answer_result)
                raise RuntimeError(f"保存答案失败：{message}")

        time.sleep(0.2)

    raise RuntimeError(
        f"点击下一题后等待题号变化超时，当前仍停留在第{previous_question_number}题。"
    )


def _get_last_visible_locator(locator, require_enabled=False):
    for index in range(locator.count() - 1, -1, -1):
        candidate = locator.nth(index)
        if not candidate.is_visible():
            continue
        if require_enabled and not candidate.is_enabled():
            continue
        return candidate
    return None


def get_next_button(page, timeout=20):
    locator = page.locator(f"xpath={NEXT_BUTTON_XPATH}")
    return _wait_until(
        lambda: _get_last_visible_locator(locator),
        timeout=timeout,
        timeout_message="等待“下一题”按钮出现超时。",
    )


def get_submit_button(page, timeout=20):
    locator = page.locator(f"xpath={SUBMIT_BUTTON_XPATH}")
    return _wait_until(
        lambda: _get_last_visible_locator(locator, require_enabled=True),
        timeout=timeout,
        timeout_message="等待“提交”按钮出现超时。",
    )


def get_submit_confirm_button(page, timeout=20):
    dialogs = page.locator(f"xpath={SUBMIT_CONFIRM_DIALOG_XPATH}")

    def find_submit_confirm_button():
        for dialog_index in range(dialogs.count()):
            dialog = dialogs.nth(dialog_index)
            if not dialog.is_visible():
                continue

            buttons = dialog.locator('xpath=.//button[contains(@class, "el-button--primary")]')
            for button_index in range(buttons.count()):
                button = buttons.nth(button_index)
                if not button.is_visible() or not button.is_enabled():
                    continue
                try:
                    if button.inner_text().strip() == "确定":
                        return button
                except Exception:
                    continue
        return None

    return _wait_until(
        find_submit_confirm_button,
        timeout=timeout,
        timeout_message="等待提交确认按钮出现超时。",
    )


def _parse_answer_indexes(answer):
    normalized_answer = str(answer or "").strip().upper().replace("，", ",")
    if "," in normalized_answer:
        answer_parts = [item.strip() for item in normalized_answer.split(",") if item.strip()]
    else:
        answer_parts = [normalized_answer]
    return [(ord(item) - ord("A")) for item in answer_parts]


def apply_answer(question_element, answer):
    normalized_answer = str(answer or "").strip()
    if not normalized_answer:
        raise RuntimeError("模型未返回可用答案。")

    option_elements = question_element.locator(f"xpath={ANSWER_OPTION_XPATH}")
    option_count = option_elements.count()
    if option_count == 0:
        raise RuntimeError("当前题目未找到可点击的选项。")

    if "对" in normalized_answer or "错" in normalized_answer:
        for answer_index in range(option_count):
            answer_element = option_elements.nth(answer_index)
            if normalized_answer in answer_element.inner_text().strip():
                answer_element.click()
                time.sleep(random.uniform(0.2, 0.5))
                return
        raise RuntimeError(f"未找到与判断题答案匹配的选项：{normalized_answer}")

    answer_indexes = _parse_answer_indexes(normalized_answer)
    for answer_index in answer_indexes:
        if answer_index < 0 or answer_index >= option_count:
            raise RuntimeError(f"模型返回的答案超出选项范围：{normalized_answer}")
        option_elements.nth(answer_index).click()
        time.sleep(random.uniform(0.2, 0.5))


@error_handler
def answer(page, index, course_name=""):
    ensure_answer_response_listener(page)
    question_element, question_number = get_current_question_element(page)
    expected_question_number = index + 1
    if question_number != expected_question_number:
        log_message(
            f"当前显示的是第{question_number}题，预期处理第{expected_question_number}题；已按当前题目继续答题。"
        )
    question_text = capture_question_text(question_element)
    log_message(f"第{question_number}题：{question_text}")
    final_answer, answer_attempts = get_answer_with_attempts(question_text, course_name)
    log_answer_attempts(answer_attempts)
    log_message(f"最终答案：{final_answer}")
    apply_answer(question_element, final_answer)
    return question_number


def auto_answer(page):
    ensure_answer_response_listener(page)
    course_name = get_course_name(page)
    if course_name:
        log_message(f"课程名称：{course_name}")
    else:
        log_message("未识别到课程名称，将仅按题目内容提问。")

    index = 0
    while True:
        answered_question_number = answer(page, index, course_name)
        next_button = get_next_button(page)
        if next_button.inner_text().strip() == "保存":
            submit_button = get_submit_button(page)
            submit_button.click()
            time.sleep(random.uniform(0.5, 1))
            confirm_button = get_submit_confirm_button(page)
            confirm_button.click()
            log_message("提交成功")
            return
        clear_driver_network_logs(page)
        next_button.click()
        time.sleep(random.uniform(0.5, 1))
        wait_for_question_change(page, answered_question_number)
        index = answered_question_number
