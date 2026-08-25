"""카드 이미지 6장을 올리면 인스타그램 캐러셀로 발행해 주는 로컬 웹앱.

맥에서만 돌아간다(127.0.0.1). 실제 게시가 Selenium 으로 로컬 Chrome 을 띄우는 방식이라
외부 호스팅에는 올릴 수 없다.

  ./run.sh  →  http://127.0.0.1:5252
"""
import os
import re
import shutil
import threading
from datetime import datetime

from flask import Flask, abort, jsonify, render_template, request, send_file
from PIL import Image

import caption_ai
import ig_bridge
from core import UPLOADS_DIR, load_config, log, set_sink

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 80 * 1024 * 1024   # 요청 전체 80MB

ALLOWED_EXT = {".png", ".jpg", ".jpeg"}
MAX_IMAGES = 10                                        # 인스타 캐러셀 상한
STAMP_RE = re.compile(r"^\d{8}_\d{6}$")
NAME_RE = re.compile(r"^\d{2}\.(png|jpg|jpeg)$")

# 한 번에 한 건만 게시한다(Chrome 프로필은 동시에 두 번 못 연다).
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
    return paths[:MAX_IMAGES]


# ---------------------------------------------------------------- 화면
@app.route("/")
def index():
    cfg = load_config()
    ig = cfg.get("instagram", {})
    # 지역 태그는 채널 브랜딩 규칙상 항상 들어간다 → 기본값에 먼저 채워 둔다.
    local = cfg.get("local_tags", [])
    topic = [t for t in cfg.get("hashtags", []) if t not in local]
    maxn = int(cfg.get("max_hashtags", 15))
    tags = (topic[:max(0, maxn - len(local))] + local)[:maxn]
    return render_template(
        "index.html",
        username=ig.get("username", ""),
        follow_cta=ig.get("follow_cta", ""),
        default_tags=" ".join(tags),
        local_tags=local,
        max_images=MAX_IMAGES,
    )


@app.route("/uploads/<stamp>/<name>")
def uploaded(stamp, name):
    if not NAME_RE.match(name or ""):
        abort(400)
    return send_file(os.path.join(_safe_dir(stamp), name))


# ---------------------------------------------------------------- 업로드
@app.route("/api/upload", methods=["POST"])
def api_upload():
    files = request.files.getlist("images")
    files = [f for f in files if f and f.filename]
    if not files:
        return jsonify(error="이미지를 선택해 주세요."), 400
    if len(files) > MAX_IMAGES:
        return jsonify(error=f"인스타 캐러셀은 최대 {MAX_IMAGES}장입니다."), 400

    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    d = os.path.join(UPLOADS_DIR, stamp)
    os.makedirs(d, exist_ok=True)

    out = []
    for i, f in enumerate(files, 1):
        ext = os.path.splitext(f.filename)[1].lower()
        if ext == ".jpeg":
            ext = ".jpg"
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
        out.append({"name": name, "original": f.filename, "w": w, "h": h,
                    "url": f"/uploads/{stamp}/{name}"})

    log(f"카드 {len(out)}장 업로드됨 → uploads/{stamp}")
    return jsonify(stamp=stamp, images=out, warnings=_ratio_warnings(out))


def _ratio_warnings(images):
    """인스타 캐러셀은 첫 장 비율에 맞춰 나머지가 잘린다 → 미리 알려준다."""
    warns = []
    ratios = {round(i["w"] / i["h"], 3) for i in images if i["h"]}
    if len(ratios) > 1:
        warns.append("이미지 비율이 서로 다릅니다. 캐러셀은 첫 장 비율에 맞춰 "
                     "나머지가 잘리니 모두 같은 비율로 맞추는 걸 권합니다.")
    first = images[0]
    if first["h"] and abs(first["w"] / first["h"] - 1.0) > 0.01:
        warns.append("첫 장이 정사각형(1:1)이 아닙니다. 카드뉴스는 1:1 또는 4:5 를 권합니다.")
    return warns


# ---------------------------------------------------------------- 캡션
@app.route("/api/caption", methods=["POST"])
def api_caption():
    body = request.get_json(silent=True) or {}
    paths = _safe_paths(body.get("stamp"), body.get("names"))
    try:
        return jsonify(caption=caption_ai.draft_caption(paths, body.get("hint", "")))
    except Exception as e:
        log(f"AI 캡션 실패: {e}")
        return jsonify(error=str(e)), 500


@app.route("/api/preview", methods=["POST"])
def api_preview():
    """게시될 최종 문구를 미리 보여준다(게시 때와 같은 조립 로직을 씀)."""
    body = request.get_json(silent=True) or {}
    try:
        caption, comment = ig_bridge.build_caption(
            body.get("caption", ""), body.get("hashtags", []),
            bool(body.get("first_comment")))
        return jsonify(caption=caption, comment=comment)
    except Exception as e:
        return jsonify(error=str(e)), 500


# ---------------------------------------------------------------- 게시
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


@app.route("/api/publish", methods=["POST"])
def api_publish():
    body = request.get_json(silent=True) or {}
    with JOB_LOCK:
        if JOB["status"] == "running":
            return jsonify(error="이미 진행 중인 작업이 있습니다."), 409

        paths = _safe_paths(body.get("stamp"), body.get("names"))
        caption = body.get("caption", "")
        tags = [t for t in (body.get("hashtags") or []) if t]
        first_comment = bool(body.get("first_comment"))
        # ★ 안전장치: dry_run 이 기본값. 실제 게시는 화면에서 명시적으로 확인해야 한다.
        dry_run = body.get("dry_run", True) is not False

        mode = "테스트(dry-run)" if dry_run else "실제 게시"
        _run_job(lambda: ig_bridge.publish(
            paths, caption, tags, dry_run=dry_run, first_comment=first_comment),
            header=f"===== {mode} 시작 — 카드 {len(paths)}장 =====")
    return jsonify(ok=True, dry_run=dry_run)


@app.route("/api/check-login", methods=["POST"])
def api_check_login():
    with JOB_LOCK:
        if JOB["status"] == "running":
            return jsonify(error="이미 진행 중인 작업이 있습니다."), 409
        _run_job(lambda: {"status": "logged_in" if ig_bridge.check_login()
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
