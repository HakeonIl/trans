# Gameloc — 게임 폴더를 넣으면 번역해 주는 도구
# Copyright (C) 2026  하진
#
# 이 프로그램은 자유 소프트웨어입니다. 자유 소프트웨어 재단이 펴낸 GNU 일반
# 공중 사용 허가서 3판 또는 그 이후 판의 조건에 따라 재배포하거나 고칠 수
# 있습니다.
#
# 이 프로그램은 쓸모가 있기를 바라며 배포하지만 어떠한 보증도 하지 않습니다.
# 자세한 내용은 GNU 일반 공중 사용 허가서를 보세요.
# 함께 받은 LICENSE 파일에 전문이 있습니다. <https://www.gnu.org/licenses/>

"""텍스트 덩어리 <-> 주소가 붙은 칸들.

유니티 ``TextAsset`` 이나 StreamingAssets 파일은 하나의 큰 문자열입니다.
번역자가 대사 한 줄만 고치게 하려면 칸으로 쪼개고, 어디서 왔는지 기억하고,
나머지를 건드리지 않고 그 칸만 되돌려 넣어야 합니다.

수정이 없으면 ``explode``+``implode`` 는 입력을 그대로 재현해야 합니다.
테스트가 이를 강제합니다.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Iterator

from . import jsonnest
from .treepath import format_path, parse_path, walk_strings

# js/plugins.js 는 JS 파일이지만 알맹이는 JSON 배열 하나입니다.
_PLUGINS_RE = re.compile(r"(\$plugins\s*=\s*)(\[.*\])(\s*;?\s*)$", re.S)


def sniff(text: str, filename: str = "") -> str:
    stripped = text.lstrip()
    low = filename.lower()
    if low.endswith("plugins.js") or "$plugins" in text[:400]:
        return "rmplugins"
    if low.endswith(".json") or stripped[:1] in "{[":
        if _is_json(text):
            return "json"
        # JSON 처럼 생겼는데 안 읽힙니다(주석·꼬리 쉼표·JSON5 등).
        # 이때 줄 단위로 쪼개면 재앙입니다 — `"バージョン": {` 같은 **구조가**
        # 통째로 번역 대상이 되고, 번역하면 게임이 값을 못 찾습니다.
        # 못 읽는 건 손대지 않는 게 맞습니다.
        return "opaque"
    if low.endswith((".csv", ".tsv")) or _looks_csv(text):
        return "csv"
    if "\n" in text.strip():
        return "lines"
    return "raw"


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except Exception:
        return False


def _looks_csv(text: str) -> bool:
    head = text.splitlines()[:8]
    if len(head) < 2:
        return False
    counts = [ln.count(",") for ln in head]
    return min(counts) >= 1 and max(counts) - min(counts) <= 1


def _plugins_doc(text: str):
    m = _PLUGINS_RE.search(text)
    if not m:
        raise ValueError("plugins.js 에서 $plugins 배열을 찾지 못했습니다")
    return json.loads(m.group(2))


def _delim(text: str, filename: str) -> str:
    if filename.lower().endswith(".tsv"):
        return "\t"
    first = text.splitlines()[0] if text.splitlines() else ""
    return "\t" if first.count("\t") > first.count(",") else ","


def explode(text: str, fmt: str, filename: str = "") -> Iterator[tuple[str, str]]:
    """덩어리 안 주소가 붙는 문자열마다 ``(ptr, 값)``."""
    if fmt == "opaque":
        return                      # 건드리지 않습니다
    if fmt == "raw":
        yield "", text
    elif fmt == "lines":
        for i, line in enumerate(text.split("\n")):
            if line.strip():
                yield f"[{i}]", line
    elif fmt == "json":
        yield from walk_strings(json.loads(text))
    elif fmt == "csv":
        rows = list(csv.reader(io.StringIO(text), delimiter=_delim(text, filename)))
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                if cell.strip():
                    yield f"[{r}][{c}]", cell
    elif fmt == "rmplugins":
        yield from jsonnest.walk(_plugins_doc(text))
    else:
        raise ValueError(f"unknown container format {fmt!r}")


def implode(text: str, fmt: str, edits: dict[str, str], filename: str = "") -> str:
    """``edits`` (ptr -> 새 값) 를 적용한 덩어리."""
    if fmt == "opaque" or (not edits and fmt != "json"):
        return text

    if fmt == "raw":
        return edits.get("", text)

    if fmt == "lines":
        lines = text.split("\n")
        for ptr, val in edits.items():
            idx = parse_path(ptr)[0]
            if isinstance(idx, int) and 0 <= idx < len(lines):
                lines[idx] = val
        return "\n".join(lines)

    if fmt == "json":
        doc = json.loads(text)
        for ptr, val in edits.items():
            # jsonnest 는 평범한 경로도 그대로 다루고 중첩 JSON(!)까지 지원합니다.
            jsonnest.set_(doc, ptr, val)
        if "\n  " in text or "\n\t" in text:
            return json.dumps(doc, ensure_ascii=False, indent=2)
        # RPG Maker 등 배포된 데이터 파일은 compact 입니다. 그대로 맞춥니다.
        return json.dumps(doc, ensure_ascii=False, separators=(",", ":"))

    if fmt == "rmplugins":
        m = _PLUGINS_RE.search(text)
        doc = json.loads(m.group(2))
        for ptr, val in edits.items():
            jsonnest.set_(doc, ptr, val)
        body = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
        return text[:m.start()] + m.group(1) + body + m.group(3)

    if fmt == "csv":
        delim = _delim(text, filename)
        newline = "\r\n" if "\r\n" in text else "\n"
        rows = list(csv.reader(io.StringIO(text), delimiter=delim))
        for ptr, val in edits.items():
            r, c = parse_path(ptr)
            if 0 <= r < len(rows) and 0 <= c < len(rows[r]):
                rows[r][c] = val
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=delim, lineterminator=newline,
                       quoting=csv.QUOTE_MINIMAL)
        w.writerows(rows)
        out = buf.getvalue()
        if not text.endswith(("\n", "\r")):
            out = out.rstrip("\r\n")
        return out

    raise ValueError(f"unknown container format {fmt!r}")


__all__ = ["sniff", "explode", "implode", "format_path"]
