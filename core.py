"""insta-manual 전용 설정·경로·로그 유틸.

insta-cardnews 와 독립된 프로젝트다. 다만 인스타 게시 로직만은 검증된 것을
그대로 빌려 쓴다(ig_bridge.py 참고).

★ 모듈 이름이 common.py 가 아니라 core.py 인 이유:
  ig_bridge 가 insta-cardnews 를 sys.path 에 얹는데, 그때 insta-cardnews/post_instagram.py
  의 `from common import ...` 가 이 프로젝트 파일을 잘못 집어가면 프로필·설정이 뒤섞인다.
  이름을 다르게 둬서 그 충돌을 원천 차단한다.
"""
import json
import os
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
PROFILE_DIR = os.path.join(BASE_DIR, "chrome-profile")   # 이 앱 전용 인스타 로그인 프로필
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")          # 업로드된 카드 이미지
LOGS_DIR = os.path.join(BASE_DIR, "logs")

for _d in (UPLOADS_DIR, LOGS_DIR):
    os.makedirs(_d, exist_ok=True)


def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError(
            "config.json 이 없습니다. config.example.json 을 복사해 값을 채우세요.")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- 로그
# 게시는 백그라운드 스레드에서 돌고 브라우저는 SSE 로 실시간 로그를 받아본다.
# _sink 가 걸려 있으면 로그를 거기에도 쌓아 화면으로 흘려보낸다.
_sink = None
_sink_lock = threading.Lock()


def set_sink(lines):
    """진행 중인 작업의 로그 버퍼(list)를 등록/해제(None)한다."""
    global _sink
    with _sink_lock:
        _sink = lines


def log(msg):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        path = os.path.join(LOGS_DIR, f"{datetime.now():%Y-%m}.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass
    with _sink_lock:
        if _sink is not None:
            _sink.append(line)
