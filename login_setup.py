"""인스타 로그인 세션을 이 앱 전용 Chrome 프로필에 저장한다(터미널용).

보통은 웹 화면의 '인스타 로그인' 버튼을 쓰면 되고, 이 스크립트는 그게 안 될 때
쓰는 대체 경로다. 어느 쪽이든 아이디·비밀번호 입력과 보안확인(CAPTCHA)은
사람이 직접 해야 한다 — 앱은 자격증명을 다루지 않는다.
"""
from instagram import interactive_login

if __name__ == "__main__":
    print("\n  Chrome 창이 열립니다. 그 창에서 직접 로그인해 주세요.")
    print("  로그인이 끝나면 자동으로 감지하고 창을 닫습니다.\n")
    raise SystemExit(0 if interactive_login() else 1)
