#!/usr/bin/env bash
# Mac / Linux 실행 런처
cd "$(dirname "$0")"
# python3 우선, 없으면 python
if command -v python3 >/dev/null 2>&1; then
  python3 server.py
else
  python server.py
fi
