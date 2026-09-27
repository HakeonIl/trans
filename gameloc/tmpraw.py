# SPDX-License-Identifier: GPL-3.0-or-later
"""TextMeshPro 폰트의 '앞머리' 만 바이트로 직접 읽고 고칩니다.

## 왜 이런 게 필요한가

IL2CPP 로 빌드한 게임은 폰트(MonoBehaviour)의 **구조 설명서가 들어 있지
않습니다.** 그래서 보통 방법으로는 폰트 안을 들여다볼 수 없습니다.

그런데 TextMeshPro 폰트는 **앞부분 순서가 정해져 있습니다.**

    m_GameObject · m_Enabled · m_Script · m_Name
    hashCode · material · materialHashCode        ← TMP_Asset 이 물려준 것
    m_Version            "1.1.0"
    m_SourceFontFileGUID "09f5f6f2c59fdf2449740899f95946b9"
    m_SourceFontFile     원본 글꼴 파일을 가리킴 (없으면 0)
    m_AtlasPopulationMode  0=Static 1=Dynamic 2=DynamicOS

여기까지만 읽으면 됩니다. 뒤쪽(글자표·그림판)은 손대지 않습니다.

## 무엇을 고치는가

    m_SourceFontFile     0  →  한글이 든 글꼴 오브젝트
    m_AtlasPopulationMode 0 →  1 (Dynamic)

Static 폰트는 글자 그림이 통째로 구워져 있어서 없는 글자를 못 만듭니다.
Dynamic 으로 돌리고 원본 글꼴을 물려주면, 게임이 돌면서 필요한 글자를
그때그때 만들어냅니다. **16바이트만 바뀌고 파일 길이는 그대로입니다.**

## 안전장치

앞머리를 읽어서 m_Version 이 '숫자.숫자.숫자' 이고 GUID 가 32자리 16진수이고
방식이 0~2 안에 있을 때만 폰트로 인정합니다. 하나라도 어긋나면 그 오브젝트는
아예 건드리지 않습니다. 엉뚱한 것을 폰트로 잘못 보고 망가뜨리는 일을 막습니다.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass

STATIC, DYNAMIC, DYNAMIC_OS = 0, 1, 2

_VERSION = re.compile(r"\A[0-9]{1,4}(\.[0-9]{1,4}){1,3}\Z")
_GUID = re.compile(r"\A[0-9a-fA-F]{32}\Z")

# m_GameObject(12) + m_Enabled(4) + m_Script(12)
_NAME_AT = 28
# hashCode(4) + material PPtr(12) + materialHashCode(4)
_AFTER_NAME = 20

MAX_NAME = 240


@dataclass(frozen=True)
class Head:
    """폰트 앞머리에서 읽어낸 것."""

    name: str
    version: str
    guid: str
    src_file: int
    src_path: int
    mode: int
    off_src: int          # m_SourceFontFile 이 놓인 자리
    off_mode: int         # m_AtlasPopulationMode 가 놓인 자리

    @property
    def has_source(self) -> bool:
        return self.src_path != 0

    @property
    def is_static(self) -> bool:
        return self.mode == STATIC

    def needs_help(self) -> bool:
        """원본 글꼴이 없는 Static 폰트 — 없는 글자를 만들 길이 없습니다."""
        return self.is_static and not self.has_source


def _string(raw: bytes, i: int) -> tuple[str, int]:
    (n,) = struct.unpack_from("<i", raw, i)
    i += 4
    if n < 0 or n > MAX_NAME or i + n > len(raw):
        raise ValueError("문자열 길이가 이상합니다")
    s = raw[i:i + n].decode("utf-8")
    i += n
    return s, (i + 3) & ~3


def read_head(raw: bytes) -> Head | None:
    """TextMeshPro 폰트면 앞머리를 돌려주고, 아니면 ``None``."""
    if len(raw) < _NAME_AT + 64:
        return None
    try:
        name, i = _string(raw, _NAME_AT)
        i += _AFTER_NAME
        version, i = _string(raw, i)
        if not _VERSION.match(version):
            return None
        guid, i = _string(raw, i)
        if guid and not _GUID.match(guid):
            return None
        off_src = i
        (src_file,) = struct.unpack_from("<i", raw, i)
        (src_path,) = struct.unpack_from("<q", raw, i + 4)
        i += 12
        off_mode = i
        (mode,) = struct.unpack_from("<i", raw, i)
    except (ValueError, struct.error, UnicodeDecodeError):
        return None
    if mode not in (STATIC, DYNAMIC, DYNAMIC_OS):
        return None
    if src_file < 0 or src_file > 4096:
        return None
    if not name:
        return None
    return Head(name=name, version=version, guid=guid, src_file=src_file,
                src_path=src_path, mode=mode, off_src=off_src,
                off_mode=off_mode)


def retarget(raw: bytes, head: Head, font_path_id: int) -> bytes:
    """원본 글꼴을 물려주고 Dynamic 으로 돌립니다. 길이는 안 변합니다."""
    if font_path_id == 0:
        raise ValueError("가리킬 글꼴이 없습니다")
    out = bytearray(raw)
    struct.pack_into("<i", out, head.off_src, 0)                 # 같은 파일 안
    struct.pack_into("<q", out, head.off_src + 4, font_path_id)
    struct.pack_into("<i", out, head.off_mode, DYNAMIC)
    new = bytes(out)
    if len(new) != len(raw):
        raise ValueError("길이가 변했습니다")
    check = read_head(new)
    if check is None:
        raise ValueError("고친 뒤 다시 읽지 못했습니다")
    if (check.src_path, check.mode, check.name) != (font_path_id, DYNAMIC, head.name):
        raise ValueError("고친 값이 들어가지 않았습니다")
    return new
