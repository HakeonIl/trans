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

"""제어문자 보호.

게임 텍스트는 순수한 문장이 아닙니다. RPG Maker 대사에는 ``\\V[1]``(변수),
``\\N[1]``(주인공 이름), ``\\C[3]``(색), ``\\!``(입력 대기) 같은 코드가 박혀 있고,
전투 메시지에는 ``%1``, ``%2`` 자리표시자가 들어갑니다. 번역기에 그대로 넣으면
번역기가 이것들을 번역하거나, 띄어쓰기를 바꾸거나, 통째로 지워버립니다.
그러면 게임이 깨집니다.

그래서 보내기 전에 코드를 자리표시 토큰으로 바꾸고, 받은 뒤 되돌립니다.
되돌리다 토큰이 하나라도 사라졌으면 **그 번역은 버립니다** — 깨진 번역을
자동으로 집어넣는 것보다 사람이 손대게 두는 편이 낫습니다.
"""

from __future__ import annotations

import re

# 토큰은 번역기가 건드리지 않을 만큼 낯설고, 문장 부호로 오해하지 않을 모양이어야
# 합니다. 유니코드 홑화살괄호 + 숫자 조합이 실측상 가장 잘 살아남았습니다.
TOKEN = "\u27e6{}\u27e7"
TOKEN_RE = re.compile(r"\u27e6\s*(\d+)\s*\u27e7")

# 게임이 불러 쓰는 파일 이름. 문장 한가운데 있어도 번역되면 안 됩니다.
# 신고가 가장 많던 사고입니다 — "번역 후 그림이 안 나온다".
FILE_EXT = ("png", "jpg", "jpeg", "webp", "gif", "bmp", "tga",
            "ogg", "mp3", "wav", "m4a", "ttf", "otf", "mp4", "webm")

PATTERNS = [
    r"\\[A-Za-z]+\[[^\]]*\]",   # \V[1] \N[1] \C[3] \I[64]
    r"\\[.|!^><$}{\\]",         # \. \| \! \^ \> \< \$ \{ \} \\
    r"\\[A-Za-z]+",             # \G \PX 등
    r"%\d+",                    # %1 %2  (RPG Maker 메시지 서식)
    # 중괄호 태그. 전에는 \w+ 만 받아서 {image=emo/happy.png}
    # {color=#ff0000} {w=1.0} 이 그대로 번역기에 갔습니다 — = / . # 이
    # 들어가면 안 걸렸기 때문입니다.
    #
    # 다만 **안에 뭐가 있든** 잡으면 JSON 덩어리까지 삼킵니다.
    # RPG Maker 의 plugins.js 는 {"nameHint":"主人公の…"} 처럼 생겼는데,
    # 그걸 통째로 가려 버리면 그 안의 대사를 영영 못 봅니다.
    # 태그에는 빈칸도 따옴표도 없습니다. 그것으로 가릅니다.
    r"\{[^{}\s\n\"']{1,60}\}",
    r"<[^<>\n]{1,60}>",          # <color=#fff> 같은 태그
    # 확장자가 붙은 덩어리. 이름이 img 든 bg 든 상관없이 잡습니다.
    r"[^\s\"'<>{}\[\]()]*\.(?:%s)\b" % "|".join(FILE_EXT),
]

# 렌파이는 [player] 처럼 대괄호로 이름을 넣습니다. 다른 엔진에서는
# [1] 같은 평범한 글이 걸릴 수 있어 **렌파이일 때만** 켭니다.
RENPY_PATTERNS = [r"\[[^\[\]\n]{1,40}\]"]

CODE_RE = re.compile("|".join(PATTERNS))
RENPY_RE = re.compile("|".join(PATTERNS + RENPY_PATTERNS))


def _re_for(engine: str) -> "re.Pattern[str]":
    return RENPY_RE if engine == "renpy" else CODE_RE


def mask(text: str, *, engine: str = "") -> tuple[str, list[str]]:
    """제어문자를 토큰으로 바꾼 문자열과, 원래 조각들을 돌려줍니다."""
    codes: list[str] = []

    def sub(m: re.Match) -> str:
        codes.append(m.group(0))
        return TOKEN.format(len(codes) - 1)

    return _re_for(engine).sub(sub, text), codes


def unmask(text: str, codes: list[str]) -> str:
    """토큰 자리에 원래 조각을 되돌려 넣습니다."""
    def sub(m: re.Match) -> str:
        i = int(m.group(1))
        return codes[i] if 0 <= i < len(codes) else m.group(0)

    return TOKEN_RE.sub(sub, text)


def verify(translated: str, codes: list[str]) -> str | None:
    """되돌리기 전 검사. 문제가 있으면 사람이 읽을 이유를 돌려줍니다."""
    found = sorted(int(m) for m in TOKEN_RE.findall(translated))
    want = list(range(len(codes)))
    if found == want:
        return None
    missing = [i for i in want if i not in found]
    if missing:
        return f"제어문자 {len(missing)}개가 번역 과정에서 사라졌습니다"
    return "제어문자가 중복되거나 뒤엉켰습니다"


def roundtrip(source: str, translated: str, codes: list[str]) -> tuple[str, str | None]:
    """(최종 문자열, 문제 사유). 문제가 있으면 원문을 그대로 돌려줍니다."""
    problem = verify(translated, codes)
    if problem:
        return source, problem
    return unmask(translated, codes), None


def apply_glossary(text: str, glossary: dict[str, str]) -> str:
    """용어집을 강제로 적용합니다. 긴 항목부터 바꿔 부분 일치를 막습니다."""
    for src in sorted(glossary, key=len, reverse=True):
        if src:
            text = text.replace(src, glossary[src])
    return text


def parse_glossary(raw: str) -> dict[str, str]:
    """'원문=번역' 한 줄에 하나씩."""
    out: dict[str, str] = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        a, b = line.split("=", 1)
        if a.strip():
            out[a.strip()] = b.strip()
    return out
