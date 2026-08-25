"""검증된 insta-cardnews/post_instagram.py 를 빌려 쓰는 다리(bridge).

인스타 웹 DOM 은 자주 바뀌고, 그 대응 로직(버튼 라벨 후보·'다음' 전진·공유 완료 감지)이
insta-cardnews 쪽에 이미 쌓여 있다. 복사해 오면 인스타가 바뀔 때마다 두 군데를 고쳐야
하므로, 여기서는 그 모듈을 import 해서 **경로·설정·로그만 이 프로젝트 것으로 갈아끼운다.**
→ insta-cardnews 를 고치면 이 앱도 같이 고쳐진다.
"""
import os
import sys
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains

from core import BASE_DIR, PROFILE_DIR, LOGS_DIR, UPLOADS_DIR, load_config, log

# 형제 프로젝트 위치. 옮겼다면 config.cardnews_dir 나 환경변수로 지정 가능.
DEFAULT_CARDNEWS_DIR = os.path.join(os.path.dirname(BASE_DIR), "insta-cardnews")

_pi_cache = None


def _cardnews_dir():
    return (os.environ.get("INSTA_CARDNEWS_DIR")
            or load_config().get("cardnews_dir")
            or DEFAULT_CARDNEWS_DIR)


def load_pi():
    """post_instagram 모듈을 불러와 이 프로젝트 쪽 경로로 패치한 뒤 돌려준다."""
    global _pi_cache
    if _pi_cache is not None:
        return _pi_cache

    d = _cardnews_dir()
    if not os.path.isfile(os.path.join(d, "post_instagram.py")):
        raise RuntimeError(
            f"insta-cardnews 를 찾지 못했습니다: {d}\n"
            "config.json 의 cardnews_dir 에 실제 경로를 적어주세요.")
    if d not in sys.path:
        sys.path.append(d)      # append: 이 프로젝트 모듈이 항상 우선하도록

    import post_instagram as pi

    # 프로필/로그/설정/로그함수를 이 앱 것으로 교체. (build_driver·shot 이 이 전역을 본다)
    pi.PROFILE_DIR = PROFILE_DIR
    pi.LOGS_DIR = LOGS_DIR
    pi.POSTS_DIR = UPLOADS_DIR
    pi.log = log
    pi.load_config = load_config
    _pi_cache = pi
    return pi


def _effective_cfg(first_comment):
    """이번 게시에만 적용할 설정. 첫 댓글 여부는 화면에서 매번 고른다."""
    cfg = load_config()
    ig = dict(cfg.get("instagram", {}))
    ig["first_comment"] = bool(first_comment)
    cfg = dict(cfg)
    cfg["instagram"] = ig
    cfg.pop("reel", None)       # 이 앱은 캐러셀 전용 → 음악 크레딧 문구가 붙지 않게
    return cfg


def build_caption(caption, hashtags, first_comment):
    """화면 미리보기와 실제 게시가 같은 문구를 쓰도록, 캡션 조립도 pi 것을 그대로 쓴다.

    돌려주는 값: (게시글 캡션, 첫 댓글 내용 or "")
    """
    pi = load_pi()
    cfg = _effective_cfg(first_comment)
    data = {"caption": caption or "", "hashtags": list(hashtags or []), "sources": []}
    body = pi._build_caption(data, cfg)
    extras = pi._caption_extras(data, cfg) if first_comment else ""
    return body, extras


def publish(images, caption, hashtags, dry_run=True, first_comment=False):
    """카드 이미지들을 인스타 캐러셀로 올린다.

    dry_run=True 면 '공유하기' 직전까지만 진행하고 멈춘다(스크린샷은 남는다).
    """
    pi = load_pi()
    cfg = _effective_cfg(first_comment)
    data = {"images": list(images), "caption": caption or "",
            "hashtags": list(hashtags or []), "sources": []}

    # post_carousel 이 내부에서 load_config() 를 다시 부르므로, 이번 판 설정을 물려준다.
    orig_loader = pi.load_config
    pi.load_config = lambda: cfg
    try:
        result = pi.post_carousel(data, dry_run=dry_run)
    finally:
        pi.load_config = orig_loader

    # 첫 댓글: post_carousel 은 드라이버를 닫고 끝나므로 별도 세션에서 단다.
    if first_comment and result.get("status") in ("published", "shared_unconfirmed"):
        extras = pi._caption_extras(data, cfg)
        username = cfg.get("instagram", {}).get("username", "")
        if extras and username:
            ok = _comment_on_latest_post(pi, cfg, username, extras, dry=dry_run)
            result["first_comment"] = "ok" if ok else "failed"
    return result


def _comment_on_latest_post(pi, cfg, username, text, dry=False):
    """프로필의 최신 '게시물'(캐러셀)에 첫 댓글을 단다.

    insta-cardnews 의 _post_first_comment 는 릴 전용(/reels/, a[href*='/reel/'])이라
    캐러셀에는 못 쓴다. 헬퍼(_set_clip·_click_dialog_button·shot)만 재사용하고
    프로필 게시물 탭을 여는 부분만 여기서 따로 처리한다.
    """
    driver = pi.build_driver(cfg.get("instagram", {}).get("headless", False))
    try:
        driver.get(f"https://www.instagram.com/{username}/")
        time.sleep(6)
        links = [a for a in driver.find_elements(By.CSS_SELECTOR, "a[href*='/p/']")
                 if a.is_displayed()]
        if not links:
            pi.shot(driver, "comment_no_post")
            log("첫 댓글: 프로필에서 게시물을 찾지 못했습니다.")
            return False
        links[0].click()
        time.sleep(5)

        box = None
        for sel in ("textarea[aria-label*='댓글']", "textarea[placeholder*='댓글']",
                    "div[contenteditable='true'][aria-label*='댓글']",
                    "textarea[aria-label*='omment']",
                    "div[contenteditable='true'][aria-label*='omment']"):
            els = [e for e in driver.find_elements(By.CSS_SELECTOR, sel) if e.is_displayed()]
            if els:
                box = els[0]
                break
        if box is None:
            pi.shot(driver, "comment_no_box")
            log("첫 댓글: 댓글 입력란을 찾지 못했습니다.")
            return False

        box.click()
        time.sleep(0.6)
        # 줄바꿈·이모지 보존을 위해 클립보드 붙여넣기. (엔터는 즉시 게시되므로 안 씀)
        pi._set_clip(text)
        ActionChains(driver).key_down(pi.MOD_KEY).send_keys("v").key_up(pi.MOD_KEY).perform()
        time.sleep(1.5)
        pi.shot(driver, "comment_before_post")

        if dry:
            log("첫 댓글(dry): 입력까지만 하고 게시하지 않음.")
            return True
        if not pi._click_dialog_button(driver, ["게시", "Post"]):
            log("첫 댓글: '게시' 버튼을 찾지 못했습니다.")
            return False
        time.sleep(3)
        pi.shot(driver, "comment_after_post")
        log("첫 댓글 등록 완료.")
        return True
    except Exception as e:
        pi.shot(driver, "comment_error")
        log(f"첫 댓글 실패: {type(e).__name__}: {e}")
        return False
    finally:
        driver.quit()


def check_login():
    """로그인 세션이 살아 있는지만 확인한다(게시하지 않음)."""
    pi = load_pi()
    cfg = load_config()
    driver = pi.build_driver(cfg.get("instagram", {}).get("headless", False))
    try:
        driver.get("https://www.instagram.com/")
        time.sleep(6)
        pi._click_by_labels(driver, ["나중에 하기", "Not Now", "닫기", "Close"], timeout=3)
        url = driver.current_url
        pi.shot(driver, "check_login")
        if "/accounts/login" in url:
            log("로그인 세션이 없습니다. login_setup.py 를 실행해 한 번 로그인해 주세요.")
            return False
        log("로그인 세션 정상.")
        return True
    finally:
        driver.quit()
