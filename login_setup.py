"""인스타 로그인 세션을 이 앱 전용 Chrome 프로필에 저장한다.

자격증명 입력·CAPTCHA 는 사람이 직접 해야 하므로, 창만 띄우고 기다린다.
프로필을 insta-cardnews 에서 복사해 왔다면 보통 이미 로그인돼 있어 실행할 일이 없다.
세션이 만료됐을 때만 쓰면 된다.
"""
import time

from core import log
from ig_bridge import load_pi


def main():
    pi = load_pi()
    driver = pi.build_driver(headless=False)   # 로그인은 반드시 화면을 봐야 한다
    try:
        driver.get("https://www.instagram.com/")
        time.sleep(3)
        print("\n  열린 Chrome 창에서 직접 로그인해 주세요.")
        print("  피드가 보이면 이 터미널로 돌아와 Enter 를 누르세요.\n")
        input("  준비되면 Enter > ")
        pi.shot(driver, "login_setup")
        url = driver.current_url
        if "/accounts/login" in url:
            log("아직 로그인 화면입니다. 로그인 후 다시 실행해 주세요.")
        else:
            log("로그인 세션이 프로필에 저장되었습니다.")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
