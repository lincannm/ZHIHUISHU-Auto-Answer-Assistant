import time


COURSE_NAME_XPATHS = (
    '//div[contains(@class, "course_name")]',
    '//li[./label[normalize-space()="名称"]]/span',
)
COURSE_NAME_FALLBACK_SCRIPT = """
const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
const fromHeader = normalize(document.querySelector('.course_name')?.textContent);
if (fromHeader) {
    return fromHeader;
}

const fromMeta = Array.from(document.querySelectorAll('li'))
    .find((item) => normalize(item.querySelector('label')?.textContent) === '名称');
return normalize(fromMeta?.querySelector('span')?.textContent);
"""


def _normalize_text(value):
    return " ".join((value or "").split()).strip()


def build_answer_prompt(question, course_name=""):
    course_context = ""
    if course_name:
        course_context = f"课程名称：\n{course_name}\n\n"

    return f"""
请仔细阅读以下题目并思考分析，根据题目类型，严格按照以下要求作答：

选择题（单选）： 如果题目为单选题，请从选项中选择一个正确的答案，并仅输出该选项（A、B、C或D），不提供任何额外解释。
选择题（多选）： 如果题目为多选题，请选择所有正确的选项，并仅输出所有正确选项的字母，用','分隔（如A,C），按字母顺序排列，不提供任何额外解释。
判断题： 如果题目为判断题，请分析题目并仅输出 "对" 或 "错"，不提供任何额外解释。
请遵循以上规则直接给出你的答案。

{course_context}题目：
{question}

你的答案："""


def _find_course_name(page):
    for xpath in COURSE_NAME_XPATHS:
        locator = page.locator(f"xpath={xpath}")
        count = locator.count()
        for index in range(count):
            element = locator.nth(index)
            if not element.is_visible():
                continue

            try:
                course_name = _normalize_text(element.inner_text())
            except Exception:
                continue

            if course_name and course_name != "名称":
                return course_name

    try:
        fallback_value = page.evaluate(COURSE_NAME_FALLBACK_SCRIPT)
    except Exception:
        fallback_value = ""
    return _normalize_text(fallback_value)


def get_course_name(page, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        course_name = _find_course_name(page)
        if course_name:
            return course_name
        time.sleep(0.2)

    return _find_course_name(page)
