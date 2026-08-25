"""카드 이미지를 올려 인스타그램 캐러셀로 발행하는 로컬 웹앱.

  ./run.sh  →  http://127.0.0.1:5252

내 PC 에서만 돌아간다(127.0.0.1 고정). 실제 게시가 Selenium 으로 로컬 Chrome 을
띄우는 방식이라 서버에 올려서 쓸 수는 없다.

계정·API 키는 화면의 '설정'에서 입력하고 config.json 에 저장된다(git 제외).
"""
import os
import re
import shutil
import threading
from datetime import datetime

from flask import Flask, abort, jsonify, render_template, request, send_file
from PIL import Image

import caption_ai
import instagram
from core import (UPLOADS_DIR, config_exists, is_ready, load_config, log,
                  merge_tags, save_config, set_sink)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 80 * 1024 * 1024

ALLOWED_EXT = {".png", ".jpg", ".jpeg"}
STAMP_RE = re.compile(r"^\d{8}_\d{6}$")
NAME_RE = re.compile(r"^\d{2}\.(png|jpg|jpeg)$")
KEY_MASK = "········"           # 저장된 API 키는 화면으로 절대 되돌려 보내지 않는다

# 한 번에 한 건만. Chrome 프로필은 동시에 두 번 열 수 없다.
JOB = {"status": "idle", "lines": [], "result": None, "error": None}
JOB_LOCK = threading.Lock()


# ---------------------------------------------------------------- 경로 검증
def _safe_dir(stamp):
    if not STAMP_RE.match(stamp or ""):
        abort(400, "잘못된 작업 번호입니다.")
    d = os.path.join(UPLOADS_DIR, stamp)
    if not os.path.isdir(d):
        abort(404, "업로드 폴더를 찾을 수 없습니다.")
    return d


def _safe_paths(stamp, names):
    """화면에서 정렬한 순서 그대로, 검증된 실제 파일 경로 목록을 만든다."""
    d = _safe_dir(stamp)
    paths = []
    for n in names or []:
        if not NAME_RE.match(n or ""):
            abort(400, f"잘못된 파일 이름입니다: {n}")
        p = os.path.join(d, n)
        if not os.path.isfile(p):
            abort(404, f"파일이 없습니다: {n}")
        paths.append(p)
    if not paths:
        abort(400, "이미지를 한 장 이상 선택해 주세요.")
    return paths[:instagram.MAX_IMAGES]


# ---------------------------------------------------------------- 화면
@app.route("/")
def index():
    cfg = load_config()
    return render_template(
        "index.html",
        cfg=_public_settings(cfg),
        needs_setup=not (config_exists() and is_ready()),
        ai_available=bool(cfg.get("anthropic_api_key")),
        max_images=instagram.MAX_IMAGES,
    )


@app.route("/uploads/<stamp>/<name>")
def uploaded(stamp, name):
    if not NAME_RE.match(name or ""):
        abort(400)
    return send_file(os.path.join(_safe_dir(stamp), name))


# ---------------------------------------------------------------- 설정
def _public_settings(cfg):
    """화면에 보낼 설정. API 키는 '설정됨' 여부만 알려주고 값은 보내지 않는다."""
    ig = cfg.get("instagram", {})
    return {
        "api_key_set": bool(cfg.get("anthropic_api_key")),
        "caption_model": cfg.get("caption_model", "claude-opus-5"),
        "account_context": cfg.get("account_context", ""),
        "username": ig.get("username", ""),
        "follow_cta": ig.get("follow_cta", ""),
        "ai_label": bool(ig.get("ai_label")),
        "headless": bool(ig.get("headless")),
        "hashtags": " ".join(cfg.get("hashtags", [])),
        "always_tags": " ".join(cfg.get("always_tags", [])),
        "max_hashtags": int(cfg.get("max_hashtags", 30)),
    }


def _split_tags(s):
    return [t for t in re.split(r"[\s,#]+", s or "") if t]


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        return jsonify(_public_settings(load_config()))

    b = request.get_json(silent=True) or {}
    patch = {
        "caption_model": (b.get("caption_model") or "claude-opus-5").strip(),
        "account_context": (b.get("account_context") or "").strip(),
        "instagram": {
            "username": (b.get("username") or "").strip().lstrip("@"),
            "follow_cta": (b.get("follow_cta") or "").strip(),
            "ai_label": bool(b.get("ai_label")),
            "headless": bool(b.get("headless")),
        },
        "hashtags": _split_tags(b.get("hashtags")),
        "always_tags": _split_tags(b.get("always_tags")),
        "max_hashtags": max(0, min(30, int(b.get("max_hashtags") or 30))),
    }
    # 키 칸을 건드리지 않았으면(마스크 그대로) 기존 키를 유지한다.
    key = (b.get("anthropic_api_key") or "").strip()
    if key and key != KEY_MASK:
        patch["anthropic_api_key"] = key
    elif b.get("clear_api_key"):
        patch["anthropic_api_key"] = ""

    cfg = save_config(patch)
    return jsonify(ok=True, cfg=_public_settings(cfg), ready=is_ready())


# ---------------------------------------------------------------- 업로드
@app.route("/api/upload", methods=["POST"])
def api_upload():
    files = [f for f in request.files.getlist("images") if f and f.filename]
    if not files:
        return jsonify(error="이미지를 선택해 주세요."), 400
    if len(files) > instagram.MAX_IMAGES:
        return jsonify(error=f"인스타 캐러셀은 최대 {instagram.MAX_IMAGES}장입니다."), 400

    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    d = os.path.join(UPLOADS_DIR, stamp)
    os.makedirs(d, exist_ok=True)

    out = []
    for i, f in enumerate(files, 1):
        ext = os.path.splitext(f.filename)[1].lower()
        ext = ".jpg" if ext == ".jpeg" else ext
        if ext not in ALLOWED_EXT:
            shutil.rmtree(d, ignore_errors=True)
            return jsonify(error=f"PNG/JPG 만 올릴 수 있습니다: {f.filename}"), 400
        name = f"{i:02d}{ext}"
        path = os.path.join(d, name)
        f.save(path)
        try:
            with Image.open(path) as im:
                w, h = im.size
        except Exception:
            shutil.rmtree(d, ignore_errors=True)
            return jsonify(error=f"이미지를 읽을 수 없습니다: {f.filename}"), 400
        out.append({"name": name, "w": w, "h": h, "url": f"/uploads/{stamp}/{name}"})

    log(f"카드 {len(out)}장 업로드됨 → uploads/{stamp}")
    return jsonify(stamp=stamp, images=out, warnings=_ratio_warnings(out))


def _ratio_warnings(images):
    """캐러셀은 첫 장 비율에 맞춰 나머지가 잘린다 → 미리 알려준다."""
    warns = []
    if len({round(i["w"] / i["h"], 3) for i in images if i["h"]}) > 1:
        warns.append("이미지 비율이 서로 다릅니다. 캐러셀은 첫 장 비율에 맞춰 나머지가 "
                     "잘리니 모두 같은 비율로 맞추는 걸 권합니다.")
    first = images[0]
    if first["h"] and abs(first["w"] / first["h"] - 1.0) > 0.01:
        warns.append("첫 장이 정사각형(1:1)이 아닙니다. 카드뉴스는 1:1 또는 4:5 를 권합니다.")
    return warns


# ---------------------------------------------------------------- 캡션
@app.route("/api/caption", methods=["POST"])
def api_caption():
    b = request.get_json(silent=True) or {}
    paths = _safe_paths(b.get("stamp"), b.get("names"))
    try:
        return jsonify(caption=caption_ai.draft_caption(paths, b.get("hint", "")))
    except Exception as e:
        log(f"AI 캡션 실패: {e}")
        return jsonify(error=str(e)), 500


@app.route("/api/preview", methods=["POST"])
def api_preview():
    """게시될 최종 문구를 보여준다(게시 때와 같은 조립 로직을 쓴다)."""
    b = request.get_json(silent=True) or {}
    cfg = load_config()
    caption, comment = instagram.build_caption(
        b.get("caption", ""), merge_tags(b.get("hashtags"), cfg),
        cta=cfg.get("instagram", {}).get("follow_cta", ""),
        first_comment=bool(b.get("first_comment")))
    return jsonify(caption=caption, comment=comment)


# ---------------------------------------------------------------- 작업 실행
def _run_job(fn, header=""):
    """백그라운드 스레드에서 돌리고, 그 사이 로그를 JOB['lines'] 로 흘려보낸다.

    header 는 sink 를 건 뒤에 찍는다. 먼저 찍으면 그 줄만 화면 로그에서 빠진다.
    """
    def worker():
        try:
            JOB["result"] = fn()
            JOB["status"] = "done"
        except Exception as e:
            JOB["error"] = f"{type(e).__name__}: {e}"
            JOB["status"] = "error"
            log(f"실패: {JOB['error']}")
        finally:
            set_sink(None)
    JOB.update(status="running", lines=[], result=None, error=None)
    set_sink(JOB["lines"])
    if header:
        log(header)
    threading.Thread(target=worker, daemon=True).start()


def _busy():
    return JOB["status"] == "running"


@app.route("/api/publish", methods=["POST"])
def api_publish():
    b = request.get_json(silent=True) or {}
    with JOB_LOCK:
        if _busy():
            return jsonify(error="이미 진행 중인 작업이 있습니다."), 409
        cfg = load_config()
        ig = cfg.get("instagram", {})
        if not ig.get("username"):
            return jsonify(error="먼저 설정에서 인스타그램 아이디를 입력해 주세요."), 400

        paths = _safe_paths(b.get("stamp"), b.get("names"))
        caption, comment = instagram.build_caption(
            b.get("caption", ""), merge_tags(b.get("hashtags"), cfg),
            cta=ig.get("follow_cta", ""), first_comment=bool(b.get("first_comment")))
        # ★ 안전장치: dry_run 이 기본값. 실제 게시는 화면에서 명시적으로 눌러야 한다.
        dry_run = b.get("dry_run", True) is not False

        mode = "테스트(공유 안 함)" if dry_run else "실제 게시"
        _run_job(lambda: instagram.post_carousel(
            paths, caption, dry_run=dry_run, headless=ig.get("headless", False),
            first_comment_text=comment, username=ig.get("username", ""),
            ai_label=ig.get("ai_label", False)),
            header=f"===== {mode} 시작 — 카드 {len(paths)}장 =====")
    return jsonify(ok=True, dry_run=dry_run)


@app.route("/api/login", methods=["POST"])
def api_login():
    """Chrome 을 열어 사용자가 직접 로그인하게 한다(자격증명은 앱이 다루지 않는다)."""
    with JOB_LOCK:
        if _busy():
            return jsonify(error="이미 진행 중인 작업이 있습니다."), 409
        _run_job(lambda: {"status": "logged_in" if instagram.interactive_login()
                          else "logged_out"},
                 header="===== 인스타 로그인 =====")
    return jsonify(ok=True)


@app.route("/api/check-login", methods=["POST"])
def api_check_login():
    with JOB_LOCK:
        if _busy():
            return jsonify(error="이미 진행 중인 작업이 있습니다."), 409
        headless = load_config().get("instagram", {}).get("headless", False)
        _run_job(lambda: {"status": "logged_in" if instagram.check_login(headless)
                          else "logged_out"},
                 header="===== 로그인 세션 확인 =====")
    return jsonify(ok=True)


@app.route("/api/job")
def api_job():
    since = request.args.get("since", type=int) or 0
    return jsonify(status=JOB["status"], lines=JOB["lines"][since:],
                   total=len(JOB["lines"]), result=JOB["result"], error=JOB["error"])


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5252"))
    print(f"\n  인스타 카드뉴스 발행기  →  http://127.0.0.1:{port}\n")
    # 127.0.0.1 고정: 같은 네트워크의 다른 기기에서 접근할 수 없게 한다.
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
