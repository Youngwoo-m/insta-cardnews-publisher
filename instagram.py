"""인스타그램 웹에 카드 이미지를 캐러셀로 올린다. (Selenium)

이 파일 하나로 게시가 완결된다 — 다른 프로젝트에 기대지 않는다.

인스타 웹은 봇 탐지가 강하고 DOM 이 자주 바뀐다. 그래서 셀렉터를 하나로 고정하지 않고
한국어/영어 라벨 후보를 여러 개 두고 순서대로 시도하며, 단계마다 logs/ 에 스크린샷을
남긴다. 버튼을 못 찾으면 그 스크린샷을 보고 아래 *_LABELS 목록에 문구를 추가하면 된다.
"""
import os
import subprocess
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from core import LOGS_DIR, PROFILE_DIR, log, shot_path

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")
MOD_KEY = Keys.COMMAND if IS_MAC else Keys.CONTROL

MAX_IMAGES = 10          # 인스타 캐러셀 상한

# 한국어/영어 UI 모두 대응하기 위한 라벨 후보
CREATE_LABELS = ["새로운 게시물 만들기", "새로운 게시물", "새 게시물", "만들기",
                 "New post", "Create new post", "Create"]
POST_MENU_LABELS = ["게시물", "Post"]
NEXT_LABELS = ["다음", "Next"]
SHARE_LABELS = ["공유하기", "공유", "Share"]
SELECT_LABELS = ["컴퓨터에서 선택", "Select from computer", "Select From Computer"]
DISMISS_LABELS = ["나중에 하기", "Not Now", "닫기", "Close"]


# ------------------------------------------------------------------ 입력 유틸
def _set_clip(text):
    """OS 클립보드에 텍스트를 올린다(맥 pbcopy / 윈도우 PowerShell / 리눅스 xclip).

    윈도우는 stdin 인코딩 문제를 피하려 UTF-8 임시파일을 PowerShell 로 읽어 올린다.
    """
    try:
        if IS_MAC:
            return subprocess.run(["pbcopy"], input=text, text=True).returncode == 0
        if IS_WIN:
            import tempfile
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                             encoding="utf-8") as f:
                f.write(text)
                tmp = f.name
            try:
                subprocess.run(["powershell", "-NoProfile", "-Command",
                                f"Set-Clipboard -Value (Get-Content -Raw -Encoding UTF8 "
                                f"-LiteralPath '{tmp}')"], check=False)
            finally:
                os.remove(tmp)
            return True
        return subprocess.run(["xclip", "-selection", "clipboard"],
                              input=text, text=True).returncode == 0
    except Exception:
        return False


def _send_text(driver, text):
    """한 줄을 입력한다.

    맥의 ChromeDriver 는 BMP 밖 문자(이모지)를 send_keys 로 보낼 때 서로게이트 쌍이
    깨져 두부 글자로 들어간다. 그런 줄만 클립보드 붙여넣기로 우회한다.
    """
    if IS_MAC and any(ord(ch) > 0xFFFF for ch in text):
        if _set_clip(text):
            ActionChains(driver).key_down(MOD_KEY).send_keys("v").key_up(MOD_KEY).perform()
            time.sleep(0.3)
            return
        log("클립보드 복사 실패 → send_keys 로 대체(이모지가 깨질 수 있습니다).")
    ActionChains(driver).send_keys(text).perform()


# ------------------------------------------------------------------ 드라이버
def build_driver(headless=False):
    opts = Options()
    opts.add_argument(f"--user-data-dir={PROFILE_DIR}")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--window-size=1280,1000")
    opts.add_argument("--lang=ko-KR")
    opts.set_capability("unhandledPromptBehavior", "accept")
    if headless:
        opts.add_argument("--headless=new")
    driver = webdriver.Chrome(options=opts)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
    )
    return driver


def shot(driver, name):
    """단계별 스크린샷. 버튼을 못 찾았을 때 원인을 찾는 유일한 단서다."""
    path = shot_path(name)
    try:
        driver.save_screenshot(path)
        log(f"스크린샷: {os.path.basename(path)}")
    except Exception as e:
        log(f"스크린샷 실패: {e}")
    return path


# ------------------------------------------------------------------ 요소 찾기
def _click_by_labels(driver, labels, timeout=8):
    """aria-label 또는 보이는 텍스트가 labels 중 하나와 일치하는 클릭요소를 누른다."""
    end = time.time() + timeout
    while time.time() < end:
        for lb in labels:
            for xp in (f"//*[@aria-label='{lb}']",
                       f"//*[@role='button'][normalize-space(.)='{lb}']",
                       f"//button[normalize-space(.)='{lb}']",
                       f"//div[@role='button'][.//text()[normalize-space()='{lb}']]",
                       f"//*[normalize-space(text())='{lb}']"):
                try:
                    el = driver.find_element(By.XPATH, xp)
                    if el.is_displayed():
                        try:
                            el.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", el)
                        return True
                except Exception:
                    continue
        time.sleep(0.5)
    return False


def _find_file_input(driver, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        for inp in driver.find_elements(By.CSS_SELECTOR, "input[type='file']"):
            accept = (inp.get_attribute("accept") or "").lower()
            if accept == "" or any(k in accept for k in ("image", "jpeg", "png")):
                return inp
        time.sleep(0.5)
    return None


def _dialog(driver):
    try:
        return driver.find_element(By.CSS_SELECTOR, "div[role='dialog']")
    except Exception:
        return driver


def _click_dialog_button(driver, labels):
    """다이얼로그 우상단의 '다음'/'공유하기' 같은 버튼을 정확히 눌러 진행한다.

    단순 라벨 클릭은 부모 div 를 눌러 아무 일도 안 일어나는 경우가 있다. 그래서
    다이얼로그 범위 안에서 텍스트가 정확히 일치하는 요소를 찾아 스크롤 후 JS 클릭한다.
    """
    scope = _dialog(driver)
    for lb in labels:
        for xp in (f".//div[@role='button'][normalize-space(.)='{lb}']",
                   f".//button[normalize-space(.)='{lb}']",
                   f".//*[@role='button'][normalize-space(.)='{lb}']",
                   f".//div[normalize-space(text())='{lb}']"):
            try:
                for el in scope.find_elements(By.XPATH, xp):
                    if el.is_displayed():
                        driver.execute_script(
                            "arguments[0].scrollIntoView({block:'center'});", el)
                        time.sleep(0.2)
                        driver.execute_script("arguments[0].click();", el)
                        return True
            except Exception:
                continue
    return False


def _caption_box(driver):
    """캡션 입력란(없으면 None). '캡션 화면에 도달했는지' 판별에도 쓴다.

    캡션란은 aria-label 이 자주 바뀌고 contenteditable div 라서
    ① aria-label 부분일치 → ② 다이얼로그 내 편집영역 폴백 순으로 찾는다.
    (자르기·필터 화면에는 편집영역이 없으므로 이게 캡션화면 신호가 된다.)
    """
    for kw in ("문구", "caption", "Caption"):
        try:
            for el in driver.find_elements(By.XPATH, f"//*[contains(@aria-label,'{kw}')]"):
                if el.is_displayed():
                    return el
        except Exception:
            continue
    scope = _dialog(driver)
    for sel in ("div[contenteditable='true']", "textarea", "div[role='textbox']"):
        try:
            for el in scope.find_elements(By.CSS_SELECTOR, sel):
                if el.is_displayed():
                    return el
        except Exception:
            continue
    return None


def _type_caption(driver, caption):
    box = _caption_box(driver)
    if box is None:
        return False
    try:
        ActionChains(driver).move_to_element(box).click().perform()
        time.sleep(0.4)
        # 줄바꿈은 SHIFT+ENTER 로 따로 보낸다(그냥 ENTER 면 바로 게시될 수 있다).
        lines = caption.split("\n")
        for i, line in enumerate(lines):
            if line:
                _send_text(driver, line)
            if i < len(lines) - 1:
                ActionChains(driver).key_down(Keys.SHIFT).send_keys(Keys.ENTER)\
                    .key_up(Keys.SHIFT).perform()
            time.sleep(0.02)
        return True
    except Exception:
        return False


def _try_enable_ai_label(driver):
    """'고급 설정 → AI 레이블' 토글을 켠다(실패해도 게시는 계속).

    Meta 는 사실적인 AI 생성 콘텐츠에 레이블을 요구한다. UI 가 자주 바뀌므로 여러 문구를
    시도하고, 못 찾으면 로그만 남긴다(게시 흐름을 깨지 않는다).
    """
    try:
        _click_by_labels(driver, ["고급 설정", "고급설정", "Advanced settings"], timeout=3)
        time.sleep(1.2)
        for kw in ("AI 레이블", "AI label", "AI로 생성", "인공지능"):
            for el in [e for e in driver.find_elements(
                    By.XPATH, f"//*[contains(text(),'{kw}')]") if e.is_displayed()]:
                try:
                    sw = el.find_element(
                        By.XPATH, "./ancestor::*[.//*[@role='switch' or @role='checkbox']][1]"
                                  "//*[@role='switch' or @role='checkbox']")
                    if (sw.get_attribute("aria-checked") or "").lower() != "true":
                        sw.click()
                        time.sleep(0.8)
                    log("AI 레이블 ON(시도).")
                    return True
                except Exception:
                    continue
        log("AI 레이블 토글을 찾지 못했습니다 → 게시 후 앱에서 직접 확인해 주세요.")
    except Exception as e:
        log(f"AI 레이블 건너뜀: {type(e).__name__}")
    return False


# ------------------------------------------------------------------ 캡션 조립
def build_caption(caption, hashtags, cta="", first_comment=False):
    """게시글 캡션과 첫 댓글 내용을 만든다.

    first_comment=True 면 해시태그를 본문에서 빼 첫 댓글로 돌린다.
    돌려주는 값: (캡션, 첫 댓글) — first_comment 가 False 면 두 번째는 빈 문자열.
    """
    body = (caption or "").strip()
    cta = (cta or "").strip()
    if cta and cta not in body:
        body = (body + "\n\n" + cta).strip()
    tags = " ".join("#" + t.lstrip("#") for t in (hashtags or []) if t)
    if first_comment:
        return body, tags
    return "\n\n".join(p for p in (body, tags) if p), ""


# ------------------------------------------------------------------ 게시
def _open_instagram(driver):
    """인스타 첫 화면을 열고 로그인 상태를 확인한다."""
    driver.get("https://www.instagram.com/")
    time.sleep(6)
    _click_by_labels(driver, DISMISS_LABELS + ["허용", "Allow"], timeout=3)
    if "/accounts/login" in driver.current_url:
        shot(driver, "not_logged_in")
        raise RuntimeError("인스타그램 로그인이 필요합니다. 화면에서 '인스타 로그인'을 눌러 주세요.")


def post_carousel(images, caption, *, dry_run=True, headless=False,
                  first_comment_text="", username="", ai_label=False,
                  supervised=True):
    """카드 이미지들을 캐러셀 게시물로 올린다.

    dry_run=True 면 '공유하기' 직전까지만 진행하고 멈춘다(게시되지 않음).
    supervised=True 면 CAPTCHA·보안확인이 떴을 때 사람이 직접 풀도록 최대 3분 기다린다.
    """
    images = [p for p in images if os.path.exists(p)][:MAX_IMAGES]
    if not images:
        raise RuntimeError("올릴 이미지가 없습니다.")

    driver = build_driver(headless)
    try:
        _open_instagram(driver)

        # 1) 새 게시물 만들기
        if not _click_by_labels(driver, CREATE_LABELS, timeout=12):
            if supervised:
                shot(driver, "challenge")
                log("⚠️ '새 게시물' 버튼이 안 보입니다. 보안확인(CAPTCHA)일 수 있습니다.")
                log("   열린 Chrome 창에서 직접 확인을 완료해 주세요. 최대 3분 기다립니다...")
                ok = False
                for i in range(18):
                    time.sleep(10)
                    if _click_by_labels(driver, CREATE_LABELS, timeout=5):
                        ok = True
                        log(f"   확인 통과({(i + 1) * 10}초) → 계속 진행합니다.")
                        break
                if not ok:
                    shot(driver, "no_create_button")
                    raise RuntimeError("3분 안에 확인이 끝나지 않았습니다. 앱에서 직접 올려 주세요.")
            else:
                shot(driver, "no_create_button")
                raise RuntimeError("'새 게시물' 버튼을 찾지 못했습니다(스크린샷 확인).")
        time.sleep(1.5)
        _click_by_labels(driver, POST_MENU_LABELS, timeout=3)   # 하위 메뉴가 있는 UI 대응
        time.sleep(1.5)

        # 2) 파일 선택 — 여러 경로를 한 번에 넘기면 캐러셀이 된다
        inp = _find_file_input(driver, timeout=8)
        if inp is None:
            _click_by_labels(driver, SELECT_LABELS, timeout=4)
            inp = _find_file_input(driver, timeout=6)
        if inp is None:
            shot(driver, "no_file_input")
            raise RuntimeError("파일 선택 입력을 찾지 못했습니다(스크린샷 확인).")
        inp.send_keys("\n".join(os.path.abspath(p) for p in images))
        log(f"이미지 {len(images)}장 업로드 중...")
        time.sleep(9)
        shot(driver, "after_upload")

        # 3) 자르기 → (편집) → 캡션 화면까지 '다음'으로 전진.
        #    화면 수가 가변이라, 캡션란이 보일 때까지 최대 4번 시도하며 매번 검증한다.
        for step in range(1, 5):
            if _caption_box(driver) is not None:
                break
            if not _click_next(driver):
                shot(driver, f"no_next_{step}")
                log(f"'다음' 버튼을 찾지 못했습니다(단계 {step}).")
                break
            for _ in range(12):          # 화면 전환 대기(최대 6초)
                time.sleep(0.5)
                if _caption_box(driver) is not None:
                    break
            log(f"'다음' {step}회 진행.")
        if _caption_box(driver) is None:
            shot(driver, "stuck")
            raise RuntimeError("캡션 화면까지 진행하지 못했습니다(스크린샷 stuck 확인).")
        shot(driver, "caption_screen")

        # 4) 캡션 입력
        if not _type_caption(driver, caption):
            shot(driver, "no_caption_box")
            log("캡션 입력란을 찾지 못했습니다. 캡션 없이 진행됩니다.")
        time.sleep(1)

        if ai_label:
            _try_enable_ai_label(driver)
        shot(driver, "before_share")

        if dry_run:
            log("테스트 모드: '공유하기' 직전에서 멈췄습니다. 게시되지 않았습니다.")
            log("   logs/ 의 caption_screen·before_share 스크린샷으로 확인하세요.")
            return {"status": "dry_run", "images": len(images)}

        # 5) 공유하기
        if not _click_dialog_button(driver, SHARE_LABELS):
            shot(driver, "no_share")
            raise RuntimeError("'공유하기' 버튼을 찾지 못했습니다.")
        # ★ 여러 장 업로드는 시간이 걸린다. '공유 중'일 때 창을 닫으면 게시가 중단되므로
        #    '공유되었습니다'가 뜰 때까지 최대 100초 기다린다.
        ok = False
        for _ in range(66):
            time.sleep(1.5)
            src = driver.page_source or ""
            if not ("공유 중" in src or "업로드 중" in src) and \
                    ("공유되었습니다" in src or "게시물이 공유" in src
                     or "post has been shared" in src.lower()):
                ok = True
                break
        time.sleep(3)
        shot(driver, "after_share")
        if not ok:
            log("공유 완료 문구를 확인하지 못했습니다. 프로필에서 직접 확인해 주세요.")
            return {"status": "shared_unconfirmed", "images": len(images)}
        log("게시 완료.")
        result = {"status": "published", "images": len(images)}

        # 6) 첫 댓글(선택) — 같은 세션에서 이어서 단다
        if first_comment_text.strip() and username:
            result["first_comment"] = "ok" if _comment_on_latest_post(
                driver, username, first_comment_text) else "failed"
        return result

    except Exception as e:
        shot(driver, "error")
        log(f"게시 중 오류: {e}")
        raise
    finally:
        driver.quit()


def _comment_on_latest_post(driver, username, text):
    """프로필의 최신 게시물에 첫 댓글을 단다."""
    try:
        driver.get(f"https://www.instagram.com/{username.lstrip('@')}/")
        time.sleep(6)
        links = [a for a in driver.find_elements(By.CSS_SELECTOR, "a[href*='/p/']")
                 if a.is_displayed()]
        if not links:
            shot(driver, "comment_no_post")
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
            shot(driver, "comment_no_box")
            log("첫 댓글: 댓글 입력란을 찾지 못했습니다.")
            return False

        box.click()
        time.sleep(0.6)
        # 줄바꿈·이모지 보존을 위해 붙여넣기. (엔터는 즉시 게시되므로 쓰지 않는다)
        _set_clip(text)
        ActionChains(driver).key_down(MOD_KEY).send_keys("v").key_up(MOD_KEY).perform()
        time.sleep(1.5)
        if not _click_dialog_button(driver, ["게시", "Post"]):
            log("첫 댓글: '게시' 버튼을 찾지 못했습니다.")
            return False
        time.sleep(3)
        shot(driver, "comment_after_post")
        log("첫 댓글 등록 완료.")
        return True
    except Exception as e:
        log(f"첫 댓글 실패: {type(e).__name__}: {e}")
        return False


# ------------------------------------------------------------------ 로그인
def check_login(headless=False):
    """로그인 세션이 살아 있는지 확인한다(게시하지 않음)."""
    driver = build_driver(headless)
    try:
        driver.get("https://www.instagram.com/")
        time.sleep(6)
        _click_by_labels(driver, DISMISS_LABELS, timeout=3)
        shot(driver, "check_login")
        if "/accounts/login" in driver.current_url:
            log("로그인이 필요합니다.")
            return False
        log("로그인 세션 정상.")
        return True
    finally:
        driver.quit()


def interactive_login(timeout_sec=300):
    """Chrome 창을 열어 사용자가 직접 로그인하게 하고, 성공을 감지하면 닫는다.

    ★ 아이디·비밀번호 입력과 보안확인(CAPTCHA)은 반드시 사람이 직접 해야 한다.
      이 함수는 창을 띄우고 기다릴 뿐, 자격증명을 다루지 않는다.
    """
    driver = build_driver(headless=False)     # 로그인은 화면을 봐야 하므로 항상 보이게
    try:
        driver.get("https://www.instagram.com/accounts/login/")
        log("Chrome 창이 열렸습니다. 그 창에서 직접 로그인해 주세요.")
        log(f"로그인이 끝나면 자동으로 감지합니다. (최대 {timeout_sec // 60}분 대기)")
        end = time.time() + timeout_sec
        while time.time() < end:
            time.sleep(3)
            try:
                url = driver.current_url
            except Exception:
                log("Chrome 창이 닫혔습니다. 로그인이 끝났다면 '로그인 확인'을 눌러 주세요.")
                return False
            if "/accounts/login" not in url and "instagram.com" in url:
                time.sleep(3)
                _click_by_labels(driver, DISMISS_LABELS, timeout=3)
                shot(driver, "login_done")
                log("로그인 완료. 세션이 저장되었습니다.")
                return True
        log("시간 안에 로그인이 완료되지 않았습니다. 다시 시도해 주세요.")
        return False
    finally:
        try:
            driver.quit()
        except Exception:
            pass
