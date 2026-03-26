from main import run_application


def main(url):
    run_application("tests", url)


if __name__ == "__main__":
    url = input("请输入页面链接：")
    main(url)
