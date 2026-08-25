"""업로드한 카드 이미지를 Claude 에게 보여주고 인스타 캡션 초안을 받아온다.

카드에 이미 문구가 들어 있으므로, 뉴스 원문 없이 이미지만으로 캡션을 쓸 수 있다.
초안일 뿐이라 화면에서 얼마든지 고쳐 쓸 수 있다.
"""
import base64
import io
import os

from anthropic import Anthropic
from PIL import Image

from core import load_config, log

# 이미지는 긴 변 1024px 로 줄여 보낸다(비용·속도. 카드 글자는 이 크기에서 충분히 읽힌다).
MAX_EDGE = 1024

SYSTEM = """당신은 대학 학과 공식 인스타그램 계정의 카드뉴스 캡션을 쓰는 편집자입니다.
계정: 세명대학교 재난안전학과 (@smu_ds_official)

규칙:
- 사용자가 올린 카드 이미지들을 순서대로 읽고, 그 내용에 **실제로 담긴 사실만** 씁니다.
  이미지에 없는 수치·날짜·기관명을 지어내지 마세요.
- 첫 줄은 스크롤을 멈추게 하는 한 문장 훅(20자 내외). 이모지 1개까지 허용.
- 그 뒤 2~4문장으로 카드 핵심을 요약합니다. 문장은 짧고 담백하게.
- 마지막에 행동을 유도하는 한 문장(저장·공유·의견 등)을 답니다.
- 전체 350자 이내. 해시태그는 쓰지 마세요(해시태그는 따로 관리합니다).
- 과장·공포 조장·단정적 예측 금지. 공공기관다운 차분한 톤.

출력은 캡션 본문만. 설명이나 머리말 없이 캡션만 그대로 출력하세요."""


def _encode(path):
    """이미지를 API 로 보낼 수 있게 축소 후 base64 JPEG 으로 만든다."""
    with Image.open(path) as im:
        im = im.convert("RGB")
        if max(im.size) > MAX_EDGE:
            ratio = MAX_EDGE / max(im.size)
            im = im.resize((max(1, int(im.width * ratio)),
                            max(1, int(im.height * ratio))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=80)
    return base64.standard_b64encode(buf.getvalue()).decode("utf-8")


def draft_caption(image_paths, hint=""):
    cfg = load_config()
    key = cfg.get("anthropic_api_key")
    if not key:
        raise RuntimeError("config.json 에 anthropic_api_key 가 없습니다.")

    paths = [p for p in image_paths if os.path.exists(p)][:10]
    if not paths:
        raise RuntimeError("캡션을 만들 이미지가 없습니다.")

    content = []
    for i, p in enumerate(paths, 1):
        content.append({"type": "text", "text": f"[카드 {i}]"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": _encode(p)}})
    ask = "위 카드뉴스의 인스타그램 캡션을 써주세요."
    if hint.strip():
        ask += f"\n\n작성자 메모(반영해 주세요): {hint.strip()}"
    content.append({"type": "text", "text": ask})

    log(f"AI 캡션 초안 생성 중... (카드 {len(paths)}장)")
    client = Anthropic(api_key=key)
    msg = client.messages.create(
        model=cfg.get("caption_model", "claude-opus-5"),
        max_tokens=2000,
        system=SYSTEM,
        messages=[{"role": "user", "content": content}],
    )
    if msg.stop_reason == "refusal":
        raise RuntimeError("모델이 응답을 거부했습니다. 카드 내용을 확인해 주세요.")
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    if not text:
        raise RuntimeError("빈 응답을 받았습니다. 다시 시도해 주세요.")
    log("AI 캡션 초안 완료.")
    return text
