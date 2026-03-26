from main import run_application


def main(url):
    run_application("onepage", url)


if __name__ == "__main__":
    url = input("请输入题目链接：")
    main(url)
