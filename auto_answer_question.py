from selenium.webdriver.common.by import By
from browser_session import get_authenticated_driver, save_login_state
import time
import random
from question_flow import auto_answer, error_handler


def get_test_num(driver):
    test_list = driver.find_elements(By.XPATH, '//div[@id="examBox"]/div/ul/li')
    return len(test_list)

# 按顺序自动做所有测试
@error_handler
def auto_answer_tests(driver):
    while True:
        test_num = get_test_num(driver)
        print("共有{}个测试待做".format(test_num))
        if test_num == 0:
            print("暂无可做的测试")
            return
        # 选择第一个测试
        todo_test = driver.find_element(By.XPATH, '//div[@id="examBox"]/div/ul/li')
        start_button = todo_test.find_element(By.XPATH, './/a[@title="开始答题"]')
        # driver.execute_script("arguments[0].click();", start_button)
        start_button.click()
        print("开始答题")
        time.sleep(random.uniform(3, 5))
        # 获取当前窗口的句柄
        current_window_handle = driver.current_window_handle
        # 获取所有窗口的句柄
        window_handles = driver.window_handles
        # 切换到新的窗口
        driver.switch_to.window(window_handles[-1])
        auto_answer(driver)
        # 答题结束后，切换回原来的窗口
        driver.switch_to.window(current_window_handle)

def main(url):
    driver = get_authenticated_driver(url)
    try:
        auto_answer_tests(driver)
        input("请按任意键退出...")
    finally:
        save_login_state(driver)
        driver.quit()

if __name__ == '__main__':
    url = input("请输入页面链接：")
    main(url)
