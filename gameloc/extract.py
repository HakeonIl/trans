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

"""게임 폴더를 훑어 번역자가 볼 문자열을 뽑아냅니다."""

from __future__ import annotations

import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable

from . import (bundle, containers, heuristics, isolate, langs, lowmem, managed,
               nodecanvas, typetree, unityraw)
from .detect import detect
from .model import Entry, Location, Project, make_id, make_id_unique
from .treepath import walk_strings

Progress = Callable[[str], None]

MAX_CELL = 4000   # 이보다 긴 문자열이 UI 한 줄인 경우는 없습니다


def _noop(_: str) -> None:
    pass


def extract(
    root: Path,
    *,
    filter_level: str = "normal",
    dedup: bool = True,
    languages: list[str] | None = None,
    include_scenes: bool = True,
    source_lang: str = "all",
    include_latin: bool = False,
    include_code_review: bool = False,
    include_notes: bool = False,
    include_js: bool = False,
    all_plugin_params: bool = False,
    include_system: bool = False,          # 티라노: system 폴더까지
    pick: list[str] | None = None,         # Ren'Py: 손으로 고른 파일만
    progress: Progress = _noop,
) -> Project:
    info = detect(root)
    threshold = heuristics.LEVELS[filter_level]
    # 원문 언어를 골랐으면 그 언어만 남깁니다. 영어를 고른 경우에는 구조를
    # 못 읽는 스크립트에서도 영어를 긁어와야 합니다.
    wanted = langs.wanted_set(source_lang)
    if source_lang == "en":
        include_latin = True
    progress(info.summary())

    raw: list[tuple[str, Location]] = []
    stats: dict[str, int] = defaultdict(int)

    if info.engine == "rpgmaker":
        from .engines import rpgmaker
        raw += rpgmaker.extract_entries(info.rm_base, info.root,
                                        include_notes=include_notes,
                                        threshold=threshold)
        stats["rm_data_files"] = len(info.asset_files)

        # 플러그인을 쓰는 게임은 대사를 자기가 만든 파일에 담습니다. 구조는
        # 모르지만 JSON·CSV 라면 어느 칸에 글자가 있는지는 알 수 있습니다.
        custom = rpgmaker.custom_data_files(info.rm_base)
        got = 0
        for path in custom:
            try:
                found = _from_loose(path, info.root, threshold, stats)
            except Exception as e:  # noqa: BLE001
                progress(f"  ! {path.name}: {e}")
                continue
            raw += found
            got += len(found)
            if found:
                progress(f"  · {path.name} 에서 문자열 {len(found)}개 "
                         "(이 게임이 직접 만든 파일)")
        stats["rm_custom_files"] = len(custom)
        stats["rm_custom_strings"] = got

        plug = rpgmaker.extract_plugins(info.rm_base, info.root, threshold,
                                        all_params=all_plugin_params)
        raw += plug
        stats["rm_plugin_params"] = len(plug)
        if include_js:
            js = rpgmaker.extract_plugin_scripts(info.rm_base, info.root)
            raw += js
            stats["rm_js_literals"] = len(js)
            progress(f"플러그인 js 파일에서 문자열 {len(js)}개")
        else:
            # 안 켰더라도 **거기 대사가 있는지는 보고** 알려 줍니다.
            # 어떤 게임은 줄거리를 플러그인 코드 안에 통째로 박아 둡니다.
            # 아무 말 없이 빠지면 "2,953개 적용" 이라는 그럴듯한 숫자만 뜨고,
            # 사용자는 무엇이 빠졌는지 알 방법이 없습니다.
            lurking = rpgmaker.count_js_dialogue(info.rm_base)
            stats["rm_js_lurking"] = lurking
            if lurking:
                progress("")
                progress(f"※ 플러그인 js 안에 **대사로 보이는 문장 {lurking}개** 가 "
                         "있습니다. 지금 설정으로는 안 가져옵니다.")
                progress("   화면에 원문이 그대로 남는다면 [고급 설정]에서 "
                         "**플러그인 js 파일까지** 를 켜고 다시 뽑으세요.")

        # 게임이 이름으로 찾는 것들은 빼냅니다 (애니메이션·모델·이벤트 이름 등).
        # 번역하면 게임이 대상을 못 찾아 죽습니다.
        ids = rpgmaker.collect_identifiers(
            info.rm_base, include_plugin_values=not all_plugin_params,
            include_js_literals=not include_js)
        before = len(raw)
        raw = [(t, l) for t, l in raw if t not in ids]
        dropped = before - len(raw)
        stats["identifiers_indexed"] = len(ids)
        stats["dropped_as_identifier"] = dropped
        if dropped:
            progress(f"게임이 이름으로 참조하는 문자열 {dropped}개를 제외했습니다.")

        entries = _collapse(raw, dedup=dedup)
        return Project(
            game_root=str(info.root), engine="rpgmaker",
            unity_version=info.rm_variant,
            languages=languages or ["ko"], entries=entries,
            identifiers=sorted(ids),
            stats={"raw_strings": len(raw), "unique_strings": len(entries),
                   **dict(stats)},
        )

    if info.engine == "tyrano":
        from .engines import tyrano
        raw += tyrano.extract_entries(info.ty_base, info.root,
                                      include_system=include_system)
        by_kind: dict[str, int] = defaultdict(int)
        for _t, l in raw:
            by_kind[l.fmt] += 1
        progress(f"대사 {by_kind['대사']}개, 이름 {by_kind['이름']}개, "
                 f"선택지·항목 {by_kind['항목']}개")
        # 같은 대사가 여러 자리에 있어도 각 자리를 따로 되돌려 넣어야 하므로
        # 위치를 잃지 않게 묶습니다(_collapse 가 위치를 모두 보존합니다).
        entries = _collapse(raw, dedup=dedup)
        return Project(
            game_root=str(info.root), engine="tyrano",
            unity_version=info.unity_version,
            languages=languages or ["ko"], entries=entries,
            stats={"scanned_scripts": len(info.asset_files),
                   "raw_strings": len(raw), "unique_strings": len(entries),
                   **{f"tyrano_{k}": v for k, v in by_kind.items()}},
        )

    if info.engine == "renpy":
        from .engines import renpy
        raw += renpy.extract_entries(info.rp_game, info.root, pick=pick)
        by_kind: dict[str, int] = defaultdict(int)
        for _t, l in raw:
            by_kind[l.fmt] += 1
        progress(f"대사 {by_kind['say']}개, 선택지 {by_kind['menu']}개, "
                 f"기타 문구 {by_kind['string']}개")
        entries = _collapse(raw, dedup=dedup)
        return Project(
            game_root=str(info.root), engine="renpy",
            unity_version=info.unity_version,
            languages=languages or ["ko"], entries=entries,
            stats={"scanned_scripts": len(info.asset_files),
                   "raw_strings": len(raw), "unique_strings": len(entries),
                   **{f"renpy_{k}": v for k, v in by_kind.items()}},
        )

    for path in info.loose_files:
        try:
            raw += _from_loose(path, info.root, threshold, stats)
        except Exception as e:  # noqa: BLE001 - 파일 하나가 전체를 죽이면 안 됩니다
            stats["loose_errors"] += 1
            progress(f"  ! {path.name}: {e}")

    if info.engine == "unity":
        targets = info.asset_files
        scenes = [p for p in targets if _is_scene(p)]
        if not include_scenes:
            targets = [p for p in targets if not _is_scene(p)]
        stats["scenes_skipped"] = 0 if include_scenes else len(scenes)
        stats["unity_version"] = info.unity_version
        # 배포 빌드에는 MonoBehaviour 타입 정보가 없습니다. 게임 DLL 에서
        # 되살려야 스크립트 안의 대사가 보입니다.
        if typetree.generator(info.root, info.unity_version) is not None:
            stats["typetree_generated"] = 1
            progress(f"타입 정보를 게임 코드에서 되살립니다 "
                     f"({typetree.describe(info.root)})")
        elif not typetree.available():
            stats["typetree_missing_pkg"] = 1
        per_file: dict[str, int] = {}
        # 읽는 일은 딴 프로세스에 맡깁니다. 큰 파일 하나에서 죽어도 그
        # 파일만 잃고 나머지 수확은 살립니다 — 예전에는 전부 날아갔습니다.
        worker = isolate.Worker(info.root, threshold, info.unity_version,
                                include_latin) if _isolated() else None
        died: list[str] = []
        for i, path in enumerate(targets, 1):
            progress(f"  [{i}/{len(targets)}] {path.relative_to(info.root)}")
            # 덩이는 **들어 올리기 전에 무게를 잽니다.** 여는 동안에는 안에서
            # 아무 말도 할 수 없어서, 미리 말해 두지 않으면 멈춘 줄 압니다.
            heavy = bundle.weigh(path)
            if heavy.is_bundle:
                progress(f"      {heavy.headline()}")
                if heavy.low_memory:
                    progress(f"      {heavy.lowmem_note()}")
                # 뽑기는 큰 덩이를 임시 파일로 풀어 읽으므로 메모리가 아니라
                # 디스크 빈 공간으로 판정합니다. (폰트·되박기는 아직 아닙니다.)
                hard = heavy.trouble(low_memory=True)
                if hard:
                    progress(f"      ⚠  {hard}")
                    stats["too_heavy"] += 1
            before = len(raw)
            try:
                if worker is not None:
                    got = worker.read(path, progress=progress)
                    if got is None:
                        died.append(path.name)
                        stats["asset_errors"] += 1
                        progress(f"  · {path.name} 을 읽다 멈췄습니다 — "
                                 f"{worker.last_why or isolate.why_it_died(path)}.")
                        progress("    이 파일만 건너뛰고 계속합니다.")
                        continue
                    rows, more = got
                    raw += rows
                    for k, v in more.items():
                        if isinstance(v, int) and k != "unity_version":
                            stats[k] += v
                else:
                    raw += _from_unity_file(path, info.root, threshold, stats,
                                            include_latin=include_latin,
                                            progress=progress)
                per_file[path.name] = len(raw) - before
            except ImportError:
                stats["unitypy_missing"] = 1
                progress("  ! UnityPy 가 설치되어 있지 않아 유니티 에셋 파일은 "
                         "건너뜁니다.  pip install UnityPy 로 설치하세요.")
                break
            except Exception as e:  # noqa: BLE001
                stats["asset_errors"] += 1
                progress(f"  ! {path.name}: {type(e).__name__}: {e}")
        if worker is not None:
            worker.close()

        # Mono games can build visible labels and dialogue directly from C#
        # string literals. Asset-only extraction can never see those. Cecil
        # gives exact ldstr instructions, avoiding UTF-16/compiler noise.
        managed_dir = next((p / "Managed" for p in sorted(info.root.glob("*_Data"))
                            if (p / "Managed").is_dir()), None)
        code_safe = code_review = 0
        if managed_dir is not None:
            dll = managed_dir / "Assembly-CSharp.dll"
            if dll.is_file():
                try:
                    found = managed.literals(dll)
                except Exception as exc:                 # noqa: BLE001
                    stats["managed_errors"] += 1
                    progress(f"  ! {dll.name} 코드 문자열: {exc}")
                else:
                    rel = str(dll.relative_to(info.root))
                    for lit in found:
                        if lit.confidence == "internal":
                            continue
                        if lit.confidence == "review" and not include_code_review:
                            code_review += 1
                            continue
                        note = ("화면 확정 · 코드 문자열" if lit.confidence == "screen"
                                else "검토 필요 · 코드 문자열")
                        note += f" · {lit.type_name}.{lit.method_name}"
                        raw.append((lit.text, Location(
                            file=rel, kind="managed", fmt="il", ptr=lit.key,
                            asset=f"{lit.type_name}.{lit.method_name}"), note))
                        if lit.confidence == "screen":
                            code_safe += 1
                        else:
                            code_review += 1
            stats["managed_screen_strings"] = code_safe
            stats["managed_review_strings"] = code_review
            if code_safe:
                progress(f"   게임 코드에서 화면 문구 {code_safe}개를 찾았습니다.")
            if code_review and not include_code_review:
                progress(f"   코드 문자열 {code_review}개는 애매해서 제외했습니다. "
                         "필요하면 [애매한 코드 문자열도]를 켜세요.")
        stats["died"] = len(died)
        if died:
            progress("")
            progress(f"※ {len(died)}개 파일은 읽다 멈춰 건너뛰었습니다: "
                     + ", ".join(died[:6])
                     + (f" 외 {len(died) - 6}개" if len(died) > 6 else ""))
            progress("   나머지에서 뽑은 것은 그대로 씁니다.")
        # 무슨 언어가 얼마나 나왔는지 보여줍니다. 영어·일본어·중국어를 함께
        # 담은 게임이 흔해서, 이걸 봐야 원문 언어를 제대로 고를 수 있습니다.
        counts = langs.census(row[0] for row in raw)
        if counts:
            progress("   언어별: " + ", ".join(
                f"{langs.NAMES.get(k, k)} {v}개"
                for k, v in sorted(counts.items(), key=lambda x: -x[1])))
        if wanted is not None:
            before = len(raw)
            raw = [row for row in raw if langs.matches(row[0], wanted)]
            stats["lang_filtered"] = before - len(raw)
            progress(f"   {langs.NAMES.get(source_lang, source_lang)} 만 남깁니다 "
                     f"— {len(raw)}개 (걸러낸 것 {before - len(raw)}개)")
        for name, n in per_file.items():
            progress(f"     {name}: 문자열 {n}개")
        stats["per_file"] = per_file
        _unity_advice(info, stats, progress, scenes=scenes,
                      found=len(raw), include_scenes=include_scenes)

    entries = _collapse(raw, dedup=dedup)
    # NodeCanvas gives us an exact, typed list of dialogue and choice fields.
    # Other strings in the same Unity build can be scene names, asset paths or
    # runtime lookup keys even when they look like translatable UI text.
    if stats.get("nodecanvas_strings"):
        stats["apply_scope"] = "visible_text"
    return Project(
        game_root=str(info.root),
        engine=info.engine,
        unity_version=info.unity_version,
        languages=languages or ["ko"],
        entries=entries,
        stats={
            "scanned_assets": len(info.asset_files),
            "scanned_loose": len(info.loose_files),
            "raw_strings": len(raw),
            "unique_strings": len(entries),
            "scripting_backend": info.scripting_backend,
            **dict(stats),
        },
    )


SCENE_RE = re.compile(r"^level\d*$|\.unity$", re.I)


def _is_scene(path: Path) -> bool:
    """``level0`` 같은 씬 파일인가. 게임 화면에 놓인 것들이 여기 들어 있습니다."""
    return bool(SCENE_RE.match(path.name)) or path.name.lower().startswith("level")


def _unity_advice(info, stats, progress: Progress, *, scenes=(), found: int = 0,
                  include_scenes: bool = False) -> None:
    """유니티에서 텍스트가 거의 안 나올 때, 왜인지 사람 말로 알려줍니다."""
    # 유니티 게임은 대사가 씬(level0, level1 …) 안에 있는 경우가 압도적입니다.
    # 예전에는 느리다는 이유로 씬을 건너뛰는 게 기본이었는데, 그러면 대사가
    # 통째로 안 나오면서도 "82개 뽑음" 같은 그럴듯한 숫자가 찍혀서 사용자가
    # 무엇이 빠졌는지 알 수가 없었습니다. 이제는 읽는 게 기본입니다.
    if scenes and not include_scenes:
        names = ", ".join(p.name for p in scenes[:4])
        progress("")
        progress(f"※ 씬 파일 {len(scenes)}개({names})를 **읽지 않았습니다.**")
        progress("   유니티 게임은 대사가 씬 안에 있는 경우가 가장 흔합니다.")
        progress("   대사가 안 나왔다면 [고급 설정]의 "
                 "**씬 파일 건너뛰기** 를 끄고 다시 뽑으세요.")

    skipped_en = stats.get("raw_latin_skipped", 0)
    if skipped_en:
        progress("")
        progress(f"※ 영어 문장 {skipped_en}개를 건너뛰었습니다. 구조를 못 읽는 "
                 "스크립트에서는 기본적으로 일본어·중국어만 가져옵니다 "
                 "(영어는 명령 이름·경로인 경우가 많아서입니다).")
        progress("   원문이 영어인 게임이라면 [고급 설정]에서 "
                 "**영어 문장도 뽑기** 를 켜고 다시 뽑으세요.")

    blind = stats.get("monobehaviour_no_typetree", 0)
    if not blind:
        return

    # 타입 정보만 있으면 열리는 경우가 압도적으로 많습니다. 먼저 안내합니다.
    if stats.get("typetree_missing_pkg"):
        progress("")
        progress(f"※ 스크립트 {blind}곳을 못 읽었습니다. 유니티는 배포 빌드에 "
                 "MonoBehaviour 의 타입 정보를 넣지 않기 때문입니다.")
        progress("   게임의 코드 파일에서 되살릴 수 있습니다:")
        for ln in typetree.install_hint().split("\n"):
            progress("   " + ln)
        return

    if info.scripting_backend == "IL2CPP":
        progress(
            f"IL2CPP 로 빌드된 게임입니다. 스크립트 안에 든 문자열 {blind}곳은 "
            "타입 정보가 통째로 지워져 있어 읽을 수 없습니다. "
            "TextAsset(csv·json 등)과 StreamingAssets 만 나옵니다.")
    else:
        progress(f"타입 정보를 못 읽은 스크립트가 {blind}곳 있습니다. "
                 "그만큼은 목록에서 빠집니다.")


# --------------------------------------------------------------------------
# 표의 '메모' 칸은 만든 사람이 자기 보라고 적어 둔 것입니다. 화면에 안
# 나오는데 일본어라서 대사처럼 보입니다. 실제로 본 예:
#
#     memo = "Confirmed source: レオン / source order=1"     ×3075줄
#
# 이걸 같이 뽑으면 번역할 것의 절반이 쓸모없는 줄이 됩니다.
_NOTE_COLUMNS = {"memo", "note", "notes", "comment", "comments", "remark",
                 "remarks", "description_internal", "메모", "비고", "주석"}


def _note_columns(text: str, filename: str) -> set[int]:
    """첫 줄이 열 이름이면, 그중 '메모' 칸의 번호를."""
    import csv as _csv
    import io as _io
    try:
        head = next(_csv.reader(_io.StringIO(text),
                                delimiter=containers._delim(text, filename)))
    except Exception:                     # noqa: BLE001
        return set()
    return {i for i, name in enumerate(head)
            if name.strip().lstrip("\ufeff").lower() in _NOTE_COLUMNS}


def _from_loose(path: Path, root: Path, threshold: float, stats) -> list:
    text = path.read_text("utf-8", errors="strict")
    fmt = containers.sniff(text, path.name)
    rel = str(path.relative_to(root))
    skip_cols = _note_columns(text, path.name) if fmt == "csv" else set()
    out = []
    for ptr, cell in containers.explode(text, fmt, path.name):
        if skip_cols and ptr.endswith("]"):
            try:
                col = int(ptr.rsplit("[", 1)[1].rstrip("]"))
            except ValueError:
                col = -1
            if col in skip_cols:
                stats["note_cells_skipped"] = stats.get("note_cells_skipped", 0) + 1
                continue
        if _keep(cell, threshold):
            out.append((cell, Location(file=rel, kind="loose", fmt=fmt, ptr=ptr)))
    stats["loose_files_read"] += 1
    return out


def _isolated() -> bool:
    """딴 프로세스로 읽을 수 있는 상황인가.

    자식 프로세스가 ``gameloc.isolate`` 를 다시 부르므로, 자식 안에서 또
    자식을 띄우면 안 됩니다. 시험에서 끄고 싶을 때를 위한 스위치도 둡니다.
    """
    import os
    import sys

    if os.environ.get("GAMELOC_NO_ISOLATE"):
        return False
    return not sys.argv[0].endswith("isolate.py")


def _mb(path: Path) -> str:
    try:
        n = path.stat().st_size / (1024 * 1024)
    except OSError:
        return "크기 모름"
    return f"{n:,.0f}MB" if n >= 1 else "1MB 미만"


def _left_to_go(done: int, total: int, spent: float) -> str:
    """남은 시간을 사람 말로. **지금까지 속도로** 잽니다.

    "12,000/38,242" 만 보여 주면 저게 5분인지 한 시간인지 모릅니다. 기다릴지
    끌지를 정하려면 남은 시간이 필요합니다.
    """
    if done <= 0 or done >= total or spent < 1.0:
        return ""
    left = (total - done) * (spent / done)
    if left < 60:
        return " (곧 끝납니다)"
    if left < 3600:
        return f" (약 {round(left / 60)}분 남음)"
    return f" (약 {left / 3600:.1f}시간 남음)"


def _from_unity_file(path: Path, root: Path, threshold: float, stats, *,
                     include_latin: bool = False, progress=None) -> list:
    import time

    import UnityPy

    # 여는 데만 몇 분 걸리는 파일이 있습니다(data.unity3d). 여기서 아무 말도
    # 안 하면 사용자는 파일 이름만 보고 몇 분을 기다립니다.
    if progress:
        progress(f"      … 파일을 여는 중입니다 ({_mb(path)})")
    # 폴더를 명시해야 짝이 되는 파일(globalgamemanagers.assets 등)을 찾습니다.
    if bundle.weigh(path).low_memory:
        # 큰 덩이는 메모리에 통째로 올리지 않고 임시 파일로 풀어 읽습니다.
        stats["lowmem"] += 1
        with lowmem.enabled():
            env = UnityPy.load(str(path), path=str(path.parent))
    else:
        env = UnityPy.load(str(path), path=str(path.parent))
    typetree.attach(env, root, stats.get("unity_version", ""))
    rel = str(path.relative_to(root))
    out = []
    # data.unity3d 한 덩이에 게임이 통째로 든 빌드가 있습니다. 오브젝트가
    # 수만 개라 몇 분씩 걸리는데, 아무 소식이 없으면 멈춘 줄 압니다.
    objects = env.objects
    total = len(objects)
    step = 2000 if total > 4000 else 0
    began = time.monotonic()
    if progress and step:
        progress(f"      … 오브젝트 {total:,}개를 읽습니다")
    breaker: dict = {}          # 느리게 실패한 클래스 (이 파일 안에서만)
    # 직렬화 파일이 여럿 든 번들이면 path_id 만으로는 오브젝트를 특정할 수 없습니다
    # (data.unity3d 는 26개, 위치의 99.9% 가 겹치는 번호를 가리켰습니다).
    # 그럴 때만 위치에 파일 이름(serial)을 적습니다 — 한 파일뿐인 게임은 그대로입니다.
    multi = _has_several_files(objects)
    objects_by_pid = {obj.path_id: obj for obj in objects}
    for i, obj in enumerate(objects, 1):
        if step and progress and i % step == 0:
            progress(f"      … {i:,}/{total:,}개 읽는 중"
                     + _left_to_go(i, total, time.monotonic() - began))
        before = len(out)
        if obj.type.name == "TextAsset":
            out += _from_textasset(obj, rel, threshold, stats)
        elif obj.type.name == "MonoBehaviour":
            got = _from_monobehaviour(obj, rel, threshold, stats, breaker,
                                      objects_by_pid)
            if got is None:
                # 타입 정보를 못 읽은 오브젝트. 바이트에서 직접 꺼냅니다 —
                # Naninovel 처럼 대사가 통째로 여기 있는 경우가 있습니다.
                got = _from_monobehaviour_raw(obj, rel, stats, threshold,
                                              include_latin=include_latin)
            out += got
        if multi and len(out) > before:
            serial = getattr(getattr(obj, "assets_file", None), "name", "") or ""
            for row in out[before:]:
                loc = row[1]
                loc.serial = serial
    return out


def _has_several_files(objects) -> bool:
    """이 번들의 오브젝트가 직렬화 파일 둘 이상에 나뉘어 있는가."""
    seen = None
    for o in objects:
        name = getattr(getattr(o, "assets_file", None), "name", None)
        if name is None:
            return False
        if seen is None:
            seen = name
        elif name != seen:
            return True
    return False


def _from_textasset(obj, rel: str, threshold: float, stats) -> list:
    try:
        data = obj.read()
        name = data.m_Name or ""
        script = data.m_Script
    except Exception:
        stats["textasset_unreadable"] += 1
        return []
    if isinstance(script, (bytes, bytearray)):
        try:
            script = script.decode("utf-8")
        except UnicodeDecodeError:
            stats["textasset_binary"] += 1
            return []
    if not isinstance(script, str) or not script.strip():
        return []

    fmt = containers.sniff(script, name)
    # 프로그램 코드와 라이선스 문서는 번역할 글이 아닙니다(번역하면 코드가 깨집니다).
    if heuristics.is_license_asset(name) or (
            fmt == "lines" and heuristics.looks_like_code(script)):
        stats["textasset_code_skipped"] = stats.get("textasset_code_skipped", 0) + 1
        return []
    try:
        cells = list(containers.explode(script, fmt, name))
    except Exception:
        fmt, cells = "raw", [("", script)]
    out = []
    for ptr, cell in cells:
        if _keep(cell, threshold):
            out.append((cell, Location(file=rel, kind="asset", fmt=fmt, ptr=ptr,
                                       path_id=obj.path_id, asset=name)))
    stats["textassets"] += 1
    return out


# 타입 정보 읽기가 이보다 **오래 걸린 끝에 실패한** 클래스는 같은 파일 안에서 다시
# 시도하지 않습니다(``_from_monobehaviour`` 의 ``breaker``).
#
# 진짜 게임(Freedom)의 data.unity3d 에는 되살린 타입 정보가 데이터와 안 맞는 클래스가
# 9개 있었습니다. 그 클래스의 오브젝트는 읽다가 2.6~8초 만에 EOFError 로 실패합니다.
# 한 클래스에 오브젝트가 5,655개라 **계속 시도하면 여섯 시간**이고, 부모가 180초 조용하면
# 자식을 죽이므로 결과가 ``died`` 였습니다. 첫 실패 뒤로는 바이트 훑기로 넘어가면
# 파일 하나가 54초에 끝납니다.
#
# 즉시 실패하는 클래스(0.5초 미만)와 잘 읽히는 클래스는 이 문에 걸리지 않으므로
# 결과가 바뀌지 않습니다.
SLOW_FAIL = 0.5


def _class_key(obj):
    """MonoBehaviour 가 어느 스크립트 클래스인지 가리키는 값. 모르면 ``None``."""
    return getattr(getattr(obj, "serialized_type", None), "script_type_index", None)


def _from_monobehaviour(obj, rel: str, threshold: float, stats,
                        breaker: dict | None = None,
                        objects_by_pid: dict | None = None) -> list | None:
    """읽을 수 있으면 목록, **타입 정보를 못 읽으면 None**(부르는 쪽이 대체).

    ``breaker`` 를 넘기면(파일 하나를 읽는 동안 같은 딕셔너리) 느리게 실패한 클래스를
    기억해 두었다가 다음 오브젝트부터 바로 ``None`` 을 돌려줍니다. 안 넘기면 예전과
    똑같이 매번 시도합니다.
    """
    key = _class_key(obj) if breaker is not None else None
    if key is not None and key in breaker:
        stats["typetree_skipped"] = stats.get("typetree_skipped", 0) + 1
        stats["monobehaviour_no_typetree"] += 1
        return None
    began = time.perf_counter() if key is not None else 0.0
    try:
        tree = obj.read_typetree()
    except Exception:
        stats["monobehaviour_no_typetree"] += 1
        if key is not None:
            spent = time.perf_counter() - began
            if spent > SLOW_FAIL:
                breaker[key] = spent
                stats["breaker_classes"] = stats.get("breaker_classes", 0) + 1
        return None
    if not isinstance(tree, dict):
        return []
    name = tree.get("m_Name") or ""

    # NodeCanvas 는 그래프 전체를 JSON 문자열 하나에 넣습니다. 일반 문자열
    # 순회로는 4천 자 제한에 걸려 실제 대사와 선택지가 통째로 빠지므로 내부의
    # 화면 문구 노드만 별도로 엽니다. 이 오브젝트에서 나머지 문자열은 그래프
    # 이름·변수·에셋 ID 이므로 함께 긁지 않습니다.
    actors = nodecanvas.actor_labels(tree, objects_by_pid or {})
    graph_cells = nodecanvas.cells(tree, actors)
    if graph_cells is not None:
        out = []
        scene = tree.get("Nickname") or name
        for cell in graph_cells:
            if _keep(cell.text, threshold):
                out.append((
                    cell.text,
                    Location(file=rel, kind="asset", fmt="nodecanvas",
                             ptr=cell.ptr, path_id=obj.path_id, asset=scene),
                    cell.note,
                ))
        stats["nodecanvas_graphs"] = stats.get("nodecanvas_graphs", 0) + 1
        stats["nodecanvas_strings"] = (stats.get("nodecanvas_strings", 0)
                                       + len(out))
        stats["monobehaviours"] += 1
        return out
    # 유니티 Localization 표인지 **여기서 한 번** 판단하고, 그 표에만 쓰는
    # 규칙은 이 안에 가둡니다. 전역 규칙에 섞으면 다른 게임까지 조용히
    # 영향을 받습니다.
    is_loc = heuristics.is_localization_table(tree)
    # 대사 옆에 붙은 이름표(Fungus 의 description·targetMethod, Live2D 의
    # ParameterIds 등)는 **오브젝트 모양으로** 알아봅니다. 필드 이름만으로는
    # 다른 게임의 멀쩡한 글까지 걸러 냅니다.
    denials = heuristics.shape_denials(tree)
    out = []
    for ptr, cell in walk_strings(tree):
        if heuristics.is_denied_key(ptr):
            continue
        if heuristics.denied_by_shape(ptr, denials):
            if _keep(cell, threshold):
                stats["identifier_shapes"] = stats.get("identifier_shapes", 0) + 1
            continue
        if is_loc and heuristics.denies_in_localization(ptr):
            continue
        if heuristics.is_display_key(ptr):
            # 확실한 화면 문구 자리. 'EXIT' 같은 짧은 대문자 라벨도 그대로.
            if isinstance(cell, str) and cell.strip() and len(cell) <= MAX_CELL:
                out.append((cell, Location(file=rel, kind="asset", fmt="typetree",
                                           ptr=ptr, path_id=obj.path_id,
                                           asset=name)))
            continue
        if _keep(cell, threshold):
            out.append((cell, Location(file=rel, kind="asset", fmt="typetree",
                                       ptr=ptr, path_id=obj.path_id, asset=name)))
    stats["monobehaviours"] += 1
    return out


def _from_monobehaviour_raw(obj, rel: str, stats, threshold: float = 0.0, *,
                            include_latin: bool = False) -> list:
    """타입 정보 없이 바이트에서 직접.

    기본은 일본어·중국어가 든 문자열만 가져옵니다. 영어 원문을 번역하려면
    ``include_latin`` 을 켜야 하는데, 안 켠 채로 영어 문장을 지나쳤다면
    **몇 개를 그냥 지나쳤는지 세어 둡니다** — 아무 말 없이 빠지면 사용자는
    "왜 영어가 안 나오지" 하고 원인을 못 찾습니다.
    """
    try:
        data = obj.get_raw_data()
    except Exception:  # noqa: BLE001
        return []
    found = unityraw.translatable(data, include_latin=include_latin)
    # **바이트에서 직접 긁는 이 길에는 거르는 문이 없었습니다.**
    #
    # 다른 세 길은 ``_keep`` 이 점수로 걸러 주는데 여기만 그냥 통과였습니다.
    # 그래서 두 번 연달아 같은 사고가 났습니다.
    #
    # ① .NET 배관이 번역 목록에 올라왔습니다
    #      KaiwaHojo, Assembly-CSharp, Version=0.0.0.0, Culture=neutral, ...
    #      ShakeCoroutine (Single, Single, Int32): Void
    #    번역해 넣으면 유니티가 메서드를 못 찾아 이벤트가 안 돕니다.
    #
    # ② **유니티 Localization 의 이름표(키)까지 올라왔습니다.**
    #      No translation found for 'titlemenu…' in UIkankei
    #    이름표를 번역하면 게임이 문구를 못 찾아 **표가 통째로** 안 나옵니다.
    #    typetree 로 읽을 때는 ``m_Key`` 라는 필드 이름을 보고 막는데
    #    (LOCALIZATION_FIELDS), 여기는 필드 이름 자체가 없어서 그 방어가
    #    통째로 안 걸립니다.
    #
    # 그래서 **여기도 같은 문**을 씁니다. 이름표는 식별자 모양이라
    # ``looks_like_identifier`` 에 걸리고, 일본어 대사는 CJK 점수로
    # 넉넉히 통과합니다.
    before = len(found)
    found = [(off, t) for off, t in found if _keep(t, threshold)]
    if before != len(found):
        stats["raw_dropped"] = (stats.get("raw_dropped", 0)
                                + before - len(found))
    if not include_latin:
        skipped = len(unityraw.latin_sentences(data))
        if skipped:
            stats["raw_latin_skipped"] = stats.get("raw_latin_skipped", 0) + skipped
    if not found:
        return []
    stats["raw_objects"] += 1
    stats["raw_strings"] = stats.get("raw_strings", 0) + len(found)
    return [(text, Location(file=rel, kind="unityraw", fmt="raw",
                            ptr=str(off), path_id=obj.path_id,
                            asset=f"path_id {obj.path_id}"))
            for off, text in found]


def _keep(cell: str, threshold: float) -> bool:
    """점수가 문턱을 넘는가.

    **0점은 문턱과 상관없이 거부입니다.** ``>=`` 만 쓰면 문턱을 0 으로 둔
    사람에게는 "절대 안 됨" 이 "다 통과" 가 됩니다.
    """
    if not isinstance(cell, str) or len(cell) > MAX_CELL:
        return False
    got = heuristics.score(cell)
    return got > 0.0 and got >= threshold


def _collapse(raw: list, *, dedup: bool) -> list[Entry]:
    def split(row):
        if len(row) >= 3:
            return row[0], row[1], row[2]
        return row[0], row[1], ""

    if dedup:
        by_text: dict[str, Entry] = {}
        entries: list[Entry] = []
        for row in raw:
            text, loc, note = split(row)
            # 같은 글이라도 화자가 다르면 번역 말투가 달라집니다. NodeCanvas
            # 항목은 위치마다 따로 두어 자동번역과 수동번역 모두 문맥을 잃지
            # 않게 합니다.
            if loc.fmt == "nodecanvas":
                entries.append(Entry(id=make_id_unique(text, loc), text=text,
                                     locations=[loc], note=note))
                continue
            e = by_text.get(text)
            if e is None:
                e = by_text[text] = Entry(id=make_id(text), text=text, note=note)
                entries.append(e)
            e.locations.append(loc)
    else:
        entries = [Entry(id=make_id_unique(t, l), text=t, locations=[l], note=n)
                   for row in raw for t, l, n in [split(row)]]
    # 정렬하지 않습니다. 추출 순서 = 파일 순서 = 대체로 이야기 순서라서,
    # 번역자가 위에서 아래로 읽어 내려가며 문맥을 유지할 수 있습니다.
    return entries
