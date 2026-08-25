#!/bin/bash
# 인스타 카드뉴스 발행기 — 로컬 웹앱 실행
cd "$(dirname "$0")" || exit 1
exec ./.venv/bin/python app.py
