# SPDX-License-Identifier: GPL-3.0-or-later
"""Extract and safely rewrite C# ``ldstr`` operands in Unity Mono games."""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path


PACKAGE = "Mono.Cecil.0.11.6.zip"
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")
_KANA = re.compile(r"[぀-ヿ]")
_TECH = re.compile(
    r"(?:Assets[/\\]|\.(?:asset|prefab|png|psd|json|es3)\b|"
    r"\[[A-Za-z][^]]*\]|(?:^|\s)(?:null|true|false|Inspector|Canvas|"
    r"GameObject|MonoBehaviour|RectTransform|RuntimePsd)\b)", re.I)
_DIAGNOSTIC = re.compile(
    r"(?:見つかりません|設定されていません|未設定|解決できません|失敗しました|"
    r"確認してください|追加してください|割り当ててください|スキップします|"
    r"無効です|テスト|デバッグ|ビルド前|Editor|Runtime)")


@dataclass(frozen=True)
class Literal:
    key: str
    text: str
    type_name: str
    method_name: str
    confidence: str                    # "screen" | "review" | "internal"


def classify(text: str, type_name: str = "", method_name: str = "") -> str:
    """Separate likely screen text from diagnostics and lookup identifiers."""
    value = (text or "").strip()
    context = f"{type_name}.{method_name}"
    if not value or not _CJK.search(value):
        return "internal"
    if (len(value) > 500 or _TECH.search(value) or _DIAGNOSTIC.search(value)
            or re.search(r"(?:^|[.])(Test|Editor|Debug|Sample)", context, re.I)):
        return "review"
    # Kana strongly indicates prose/UI.  Day counters are a common all-kanji
    # runtime format ("{0}日目") and must not be lost.
    if _KANA.search(value) or re.fullmatch(r"[\d{}:D０-９]+日目", value):
        return "screen"
    return "review"


def _tool_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
    return base / "gameloc" / "managed-helper-0.11.6"


def _csc() -> Path:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    choices = [
        windir / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe",
        windir / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe",
    ]
    found = next((p for p in choices if p.is_file()), None)
    if found is None:
        raise RuntimeError(".NET Framework C# 컴파일러(csc.exe)를 찾지 못했습니다")
    return found


def _helper() -> Path:
    here = Path(__file__).resolve().parent
    package = here / "bundled" / PACKAGE
    source = here / "managed_helper.cs"
    if not package.is_file():
        raise RuntimeError(f"관리 코드 편집 구성요소가 없습니다: {package.name}")
    out = _tool_dir()
    exe = out / "gameloc-managed.exe"
    cecil = out / "Mono.Cecil.dll"
    if exe.is_file() and cecil.is_file() and exe.stat().st_mtime >= source.stat().st_mtime:
        return exe
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(package) as z:
        with z.open("lib/net40/Mono.Cecil.dll") as src, cecil.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    cmd = [str(_csc()), "/nologo", "/optimize+", "/target:exe",
           f"/out:{exe}", f"/reference:{cecil}", str(source)]
    done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if done.returncode:
        raise RuntimeError((done.stdout + "\n" + done.stderr).strip())
    return exe


def literals(path: Path) -> list[Literal]:
    done = subprocess.run([str(_helper()), "list", str(path)], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or "DLL 문자열을 읽지 못했습니다")
    out = []
    for line in done.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        key = parts[0]
        vals = [base64.b64decode(x).decode("utf-8") for x in parts[1:]]
        out.append(Literal(key, vals[0], vals[1], vals[2],
                           classify(vals[0], vals[1], vals[2])))
    return out


def patch(source: Path, output: Path, edits: dict[str, str]) -> int:
    if not edits:
        return 0
    with tempfile.TemporaryDirectory(prefix="gameloc-managed-") as td:
        mapping = Path(td) / "mapping.tsv"
        mapping.write_text("".join(
            f"{key}\t{base64.b64encode(value.encode('utf-8')).decode('ascii')}\n"
            for key, value in edits.items()), "utf-8")
        done = subprocess.run([str(_helper()), "patch", str(source), str(mapping),
                               str(output)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or
                           f"DLL 문자열 {len(edits)}개 중 일부를 찾지 못했습니다")
    return int(done.stdout.strip().splitlines()[-1])
