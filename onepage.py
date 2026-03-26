from core.browser_session import get_authenticated_driver, save_login_state
from core.question_flow import auto_answer

if __name__ == '__main__':
    url = input("请输入题目链接：")
    driver = get_authenticated_driver(url)
    try:
        auto_answer(driver)
        input("请按任意键退出...")
    finally:
        save_login_state(driver)
        driver.quit()
