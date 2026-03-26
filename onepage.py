from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from cnocr import CnOcr
from answer_context import build_answer_prompt, get_course_name
from browser_session import get_authenticated_driver, save_login_state
from model import get_model, should_repeat_answers
import time
import random
import logging

# 设置日志级别为WARNING，这样ERROR级别的日志将不会被打印
logging.getLogger('selenium').setLevel(logging.WARNING)

ocr = CnOcr()

# 初始化模型
model = get_model()
REPEAT_UNTIL_DUPLICATE = should_repeat_answers()
QUESTION_XPATH = '//div[contains(@class, "examPaper_subject")]'
ANSWER_OPTION_XPATH = './/div[contains(@class, "label") and contains(@class, "clearfix")]'
NEXT_BUTTON_XPATH = '//button[contains(@class, "el-button--primary") and contains(@class, "is-plain")]'
SUBMIT_BUTTON_XPATH = '//button[contains(@class, "btnStyleXSumit")]'
SUBMIT_CONFIRM_DIALOG_XPATH = '//div[contains(@class, "el-message-box__wrapper")]'

def error_handler(func):
    def wrapper(*args, **kwargs):
        while True:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                print(f"函数 {func.__name__} 发生错误: {e}")
                input("请修复错误并按回车键继续...")
    return wrapper

def text_orc(image='question.png'):
    ocr_results = ocr.ocr(image)
    # 提取文本内容
    extracted_text = '\n'.join([item['text'] for item in ocr_results if item['text'].strip()])
    return extracted_text

def get_answer(question, course_name=""):
    prompt = build_answer_prompt(question, course_name)
    if not REPEAT_UNTIL_DUPLICATE:
        cur_answer = model.get_response(prompt)
        print(f'大模型第1次输出：{cur_answer}')
        return cur_answer

    answer_list = []
    index = 0
    while True:
        cur_answer = model.get_response(prompt)
        print(f'大模型第{index+1}次输出：{cur_answer}')
        if cur_answer in answer_list:
            return cur_answer
        answer_list.append(cur_answer)
        index += 1


def get_question_element(driver, index, timeout=20):
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
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", question_element)
    return question_element


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
        visible_buttons = [button for button in buttons if button.is_displayed() and button.is_enabled()]
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
                if button.is_displayed() and button.is_enabled() and button.text.strip() == '确定':
                    return button
        return None

    return wait.until(find_submit_confirm_button)

@error_handler
def answer(driver, index, course_name=""):
    question_element = get_question_element(driver, index)
    question_element.screenshot('question.png')
    question_str = text_orc()
    print(f'第{index+1}题：{question_str}')

    answer = get_answer(question_str, course_name) # answer 形如'A'  或 'B,D' 或 '对' 
    print(f'最终答案：{answer}')

    # 判断题中对与错的顺序可能不一样
    if '对' in answer or '错' in answer: # 判断题
        answer_elements = question_element.find_elements(By.XPATH, ANSWER_OPTION_XPATH)
        for answer_element in answer_elements:
            if  answer in answer_element.text.strip():
                answer_element.click()
                time.sleep(random.uniform(0.2, 0.5))
                break
                
    else: # 选择题
        answer_list = []
        if ',' in answer: # 多选题
            answer_list = [(ord(i)-ord('A')) for i in answer.split(',')]
        else: # 单选题
            answer_list = [(ord(answer)-ord('A'))]
        option_elements = question_element.find_elements(By.XPATH, ANSWER_OPTION_XPATH)
        for answer in answer_list:
            option_elements[answer].click()
            time.sleep(random.uniform(0.2, 0.5))

def auto_answer(driver):
    course_name = get_course_name(driver)
    if course_name:
        print(f'课程名称：{course_name}')
    else:
        print('未识别到课程名称，将仅按题目内容提问。')

    index = 0
    while True:
        answer(driver, index, course_name)
        # 下一题
        next_button = get_next_button(driver)
        if next_button.text.strip() == '保存':
            # 提交作业
            submit_button = get_submit_button(driver)
            submit_button.click()
            time.sleep(random.uniform(0.5, 1))
            confirm_button = get_submit_confirm_button(driver)
            confirm_button.click()
            print("提交成功")
            return
        next_button.click()
        time.sleep(random.uniform(0.5, 1))
        index += 1

if __name__ == '__main__':
    url = input("请输入题目链接：")
    driver = get_authenticated_driver(url)
    try:
        auto_answer(driver)
        input("请按任意键退出...")
    finally:
        save_login_state(driver)
        driver.quit()
