"""설정·경로·로그.

설정은 웹 화면에서 입력해 config.json 에 저장한다(개인 정보라 git 에 올라가지 않는다).
파일이 없어도 기본값으로 동작하며, 화면이 설정을 채우라고 안내한다.
"""
import json
import os
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
PROFILE_DIR = os.path.join(BASE_DIR, "chrome-profile")   # 인스타 로그인 세션이 사는 곳
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

for _d in (UPLOADS_DIR, LOGS_DIR, PROFILE_DIR):
    os.makedirs(_d, exist_ok=True)

DEFAULTS = {
    "anthropic_api_key": "",          # 비우면 AI 초안 기능만 꺼진다(게시는 정상 동작)
    "caption_model": "claude-opus-5",
    "instagram": {
        "username": "",               # @ 없이. 첫 댓글을 달 때 프로필을 찾는 데 쓴다
        "headless": False,            # True 면 창 없이 — 로그인 문제 추적이 어려워 권장 안 함
        "follow_cta": "",             # 캡션 끝에 자동으로 붙일 문구(선택)
        "ai_label": False,            # 게시 시 'AI 레이블' 토글 시도
    },
    "hashtags": [],                   # 기본 해시태그
    "always_tags": [],                # 항상 포함할 태그(브랜딩용). 개수 제한보다 우선한다
    "max_hashtags": 30,               # 인스타 상한이 30개
}


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def config_exists():
    return os.path.exists(CONFIG_PATH)


def load_config():
    """저장된 설정을 기본값 위에 얹어 돌려준다. 파일이 없어도 예외를 던지지 않는다."""
    saved = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            log(f"config.json 을 읽지 못했습니다({e}). 기본값으로 진행합니다.")
    cfg = _merge(DEFAULTS, saved)
    # 예전 이름 호환: local_tags → always_tags
    if not cfg.get("always_tags") and saved.get("local_tags"):
        cfg["always_tags"] = saved["local_tags"]
    return cfg


def save_config(patch):
    """화면에서 넘어온 설정을 기존 값 위에 병합해 저장한다."""
    current = load_config()
    cfg = _merge(current, patch or {})
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)      # 쓰다 만 파일이 남지 않도록 원자적 교체
    try:
        os.chmod(CONFIG_PATH, 0o600)  # API 키가 들어가므로 본인만 읽게
    except OSError:
        pass
    log("설정을 저장했습니다.")
    return cfg


def is_ready():
    """게시에 필요한 최소 설정이 채워졌는지."""
    return bool(load_config().get("instagram", {}).get("username"))


def merge_tags(tags, cfg=None):
    """always_tags 를 반드시 포함시키면서 max_hashtags 개로 자른다."""
    cfg = cfg or load_config()
    always = [t.lstrip("#") for t in cfg.get("always_tags", []) if t]
    maxn = int(cfg.get("max_hashtags", 30))
    rest = [t.lstrip("#") for t in (tags or []) if t and t.lstrip("#") not in always]
    return list(dict.fromkeys(rest[:max(0, maxn - len(always))] + always))[:maxn]


def shot_path(name):
    return os.path.join(LOGS_DIR, f"{datetime.now():%Y%m%d_%H%M%S}_{name}.png")


# ---------------------------------------------------------------- 로그
# 게시는 백그라운드 스레드에서 돌고 브라우저가 실시간으로 로그를 받아본다.
# _sink 가 걸려 있으면 그쪽에도 쌓아 화면으로 흘려보낸다.
_sink = None
_sink_lock = threading.Lock()


def set_sink(lines):
    global _sink
    with _sink_lock:
        _sink = lines


def log(msg):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(os.path.join(LOGS_DIR, f"{datetime.now():%Y-%m}.log"), "a",
                  encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except OSError:
        pass
    with _sink_lock:
        if _sink is not None:
            _sink.append(line)
