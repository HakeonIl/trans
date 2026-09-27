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

"""Gameloc — 게임 폴더를 넣으면 번역할 문자열을 뽑아주고,
채워 넣은 번역을 다시 게임에 되돌려 넣는 도구."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from . import __version__
from .apply import apply as apply_translations
from .apply import restore as restore_backup
from .detect import detect
from .extract import extract as run_extract
from .heuristics import LEVELS
from .model import Project
from .sheet import (merge_into_project, read_workbook, unfinished_message,
                    write_workbook)

PROJECT_JSON = "project.json"
SHEET_XLSX = "translation.xlsx"


def _say(msg: str) -> None:
    click.echo(msg)


def _load_project(workdir: Path) -> Project:
    pj = workdir / PROJECT_JSON
    if not pj.exists():
        raise click.ClickException(
            f"{pj} 가 없습니다. 먼저 `python -m gameloc.cli extract` 를 실행하세요.")
    return Project.load(pj)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__)
def main() -> None:
    """게임 폴더 → 번역 → 다시 게임으로."""


@main.command()
@click.option("--port", default=8777, help="쓸 포트")
@click.option("--no-browser", is_flag=True, help="브라우저를 자동으로 열지 않음")
def gui(port: int, no_browser: bool) -> None:
    """브라우저 번역기를 엽니다 (권장)."""
    from .webui import serve
    serve(port, not no_browser)


@main.command()
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
def scan(game_folder: Path) -> None:
    """폴더를 분석만 하고 무엇이 들어있는지 알려줍니다."""
    info = detect(game_folder)
    _say(info.summary())
    if info.data_dir:
        _say(f"데이터 폴더: {info.data_dir}")
    for p in info.asset_files[:15]:
        _say(f"  · {p.relative_to(info.root)}")
    if len(info.asset_files) > 15:
        _say(f"  … 외 {len(info.asset_files) - 15}개")


@main.command()
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("-o", "--out", type=click.Path(path_type=Path),
              help="보고서를 저장할 파일 (기본: 게임폴더 옆 gameloc_진단.txt)")
@click.option("--no-scenes", is_flag=True, help="씬 파일은 건너뜁니다 (빠름)")
def peek(game_folder: Path, out: Path | None, no_scenes: bool) -> None:
    """[유니티] 파일 안에 무엇이 들어 있는지 그대로 적어줍니다.

    번역할 문장을 못 찾을 때, 원인이 '자리를 모른다' 인지 '못 읽는다' 인지
    가려내는 용도입니다. 게임 파일은 읽기만 하고 아무것도 바꾸지 않습니다.
    """
    from .peek import report

    text = report(game_folder, include_scenes=not no_scenes)
    dest = out or game_folder.parent / f"{game_folder.name}_gameloc_진단.txt"
    dest.write_text(text, "utf-8")
    _say(text[:2000])
    _say("")
    _say(f"전체 보고서: {dest}")


@main.command(name="fonts")
def fonts_cmd() -> None:
    """이 컴퓨터에 깔린 한글 글꼴을 보여줍니다."""
    from .fontlist import scan

    found = scan()
    if not found:
        _say("한글이 든 글꼴을 찾지 못했습니다.")
        return
    _say(f"한글 글꼴 {len(found)}개:")
    for f in found:
        _say(f"  · {f.name}")
        _say(f"      {f.path}")


@main.command(name="font")
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--ttf", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="쓸 한글 글꼴 파일. 없으면 컴퓨터에서 알아서 찾습니다.")
@click.option("--dry-run", is_flag=True, help="바꾸지 않고 무엇을 할지만 봅니다")
def font_cmd(game_folder: Path, ttf: Path | None, dry_run: bool) -> None:
    """[유니티] 한글이 □□□ 로 나오는 것을 고칩니다."""
    from .font import fix

    rep = fix(game_folder, ttf=ttf, dry_run=dry_run, progress=_say)
    _say("")
    _say(rep.summary())
    for n in rep.notes:
        _say("  · " + n)
    if rep.changed_files:
        _say("되돌리려면: python -m gameloc.cli restore <게임폴더>")


@main.command()
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
def unpack(game_folder: Path) -> None:
    """[Electron] resources/app.asar 를 풀어 번역할 수 있게 만듭니다."""
    from . import asar
    info = detect(game_folder)
    if info.electron_app and not info.electron_asar:
        _say("이미 풀려 있습니다.")
        return
    if not info.electron_asar:
        raise click.ClickException("이 폴더에서 app.asar 를 찾지 못했습니다.")
    n, dest = asar.open_up(info.electron_asar)
    _say(f"{n}개 파일을 풀었습니다 → {dest}")
    _say("원본 app.asar 는 app.asar.gameloc-off 로 이름만 바꿔 두었습니다.")
    after = detect(game_folder)
    _say("")
    _say("안쪽 엔진: " + after.summary())


@main.command()
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
def repack(game_folder: Path) -> None:
    """[Electron] 푼 폴더를 지우고 원래 app.asar 로 되돌립니다."""
    from . import asar
    if asar.close_up(game_folder):
        _say("원래대로 되돌렸습니다.")
    else:
        _say("되돌릴 것이 없습니다 (app.asar.gameloc-off 가 없습니다).")


@main.command()
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("text")
def find(game_folder: Path, text: str) -> None:
    """번역이 안 된 문장이 어느 파일에 있는지 찾아줍니다."""
    from .locate import find as locate_find
    rep = locate_find(game_folder, text)
    _say(rep.verdict())
    _say(f"(텍스트 파일 {rep.scanned}개 확인)")
    for h in rep.hits:
        _say("")
        _say(f"  {h.file}  [{h.kind}]  {'가져옴' if h.covered else '안 가져옴'}")
        _say(f"    위치: {h.where}   ({h.how})")
        _say(f"    원문: {h.raw}")
        _say(f"    {h.reason}")


@main.command()
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("-o", "--out", "workdir", type=click.Path(path_type=Path), default=None,
              help="작업 폴더 (기본: ./<게임이름>_loc)")
@click.option("-l", "--lang", "langs", default="ko",
              help="만들 번역 열, 쉼표로 구분 (예: ko,ja)")
@click.option("-f", "--filter", "filter_level",
              type=click.Choice(list(LEVELS)), default="normal",
              help="[유니티] 문자열 선별 강도")
@click.option("--no-dedup", is_flag=True, help="같은 문장을 합치지 않고 등장 위치마다 한 줄씩")
@click.option("--no-scenes", is_flag=True,
              help="[유니티] level* 씬 파일을 건너뜁니다 (빠르지만 대사가 통째로 빠질 수 있습니다)")
@click.option("--src-lang", "src_lang",
              type=click.Choice(["all", "en", "ja", "zh", "ko"]), default="all",
              help="[유니티] 뽑을 원문 언어. 영어·일본어·중국어를 함께 담은 "
                   "게임에서 한 언어만 골라 뽑습니다")
@click.option("--notes", is_flag=True, help="[RPG Maker] 데이터베이스 메모(note) 칸도 포함")
@click.option("--js", "use_js", is_flag=True, help="[RPG Maker] 플러그인 js 파일 안의 문구까지")
@click.option("--all-plugin-params", "all_params", is_flag=True,
              help="[RPG Maker] 플러그인 파라미터를 전부 (위험: 게임이 죽을 수 있음)")
@click.option("--code-review", is_flag=True,
              help="[유니티 Mono] 화면 문구인지 애매한 DLL 코드 문자열도 포함")
@click.option("--max-rows", type=int, default=None, help="엑셀에 쓸 최대 행 수")
def extract(game_folder: Path, workdir: Path | None, langs: str,
            filter_level: str, no_dedup: bool, no_scenes: bool, src_lang: str,
            notes: bool,
            use_js: bool, all_params: bool, code_review: bool,
            max_rows: int | None) -> None:
    """게임 폴더에서 문자열을 뽑아 엑셀을 만듭니다."""
    workdir = workdir or Path.cwd() / f"{game_folder.resolve().name}_loc"
    workdir.mkdir(parents=True, exist_ok=True)

    proj = run_extract(
        game_folder,
        filter_level=filter_level,
        dedup=not no_dedup,
        languages=[l.strip() for l in langs.split(",") if l.strip()],
        include_scenes=not no_scenes,
        source_lang=src_lang,
        include_notes=notes,
        include_js=use_js,
        all_plugin_params=all_params,
        include_code_review=code_review,
        progress=_say,
    )
    proj.save(workdir / PROJECT_JSON)
    xlsx = write_workbook(proj, workdir / SHEET_XLSX, max_rows=max_rows)

    _say("")
    _say(f"고유 문자열 {len(proj.entries)}개 (총 등장 {proj.stats['raw_strings']}회)")
    _say(f"엑셀: {xlsx}")
    _say(f"번역을 채운 뒤:  python -m gameloc.cli apply {workdir}")
    if proj.stats.get("monobehaviour_no_typetree"):
        _say(f"참고: 타입트리가 없어 건너뛴 오브젝트 "
             f"{proj.stats['monobehaviour_no_typetree']}개 (IL2CPP 빌드에서 흔합니다)")


@main.command()
@click.argument("workdir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("-l", "--lang", default=None, help="적용할 언어 열 (기본: 첫 번째 언어)")
@click.option("--game", type=click.Path(exists=True, file_okay=False, path_type=Path),
              default=None, help="게임 폴더를 다른 위치로 지정")
@click.option("-n", "--dry-run", is_flag=True, help="쓰지 않고 결과만 보기")
def apply(workdir: Path, lang: str | None, game: Path | None, dry_run: bool) -> None:
    """엑셀에 채운 번역을 게임 파일에 넣습니다."""
    proj = _load_project(workdir)
    xlsx = workdir / SHEET_XLSX
    found: list[str] = []
    problems: list[str] = []
    n = 0
    if xlsx.exists():
        edits, found = read_workbook(xlsx, problems)
        n = merge_into_project(proj, edits)
        proj.save(workdir / PROJECT_JSON)

    lang = lang or (found[0] if found else proj.languages[0])
    total = sum(1 for e in proj.entries if e.translations.get(lang))
    _say(f"엑셀 읽음 — 언어 열: {', '.join(found) or '없음'} | "
         f"'{lang}' 번역 {total}건 (이번에 새로 바뀐 것 {n}건)")
    if problems:
        _say(f"  ! {unfinished_message(problems)}")

    rep = apply_translations(proj, lang, dry_run=dry_run, game_root=game, progress=_say)
    _say("")
    if dry_run:
        _say("[미리보기] 파일은 아직 안 건드렸습니다.")
    for line in rep.report():
        _say(line)
    for f in rep.failures[:20]:
        _say(f"  ! {f}")
    if rep.risky:
        _say("")
        _say("대사가 아니라 '이름'으로 보여 넣지 않은 항목:")
        for r in rep.risky[:20]:
            _say(f"  · {r}")
    if not dry_run and rep.files_written:
        _say("원본은 <게임폴더>/__gameloc_backup__ 에 있습니다. "
             "되돌리려면: python -m gameloc.cli restore <게임폴더>")


@main.command()
@click.argument("workdir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("-l", "--lang", "langs", default=None, help="엑셀에 둘 언어 열 (예: ko,ja,en)")
@click.option("--max-rows", type=int, default=None)
@click.option("--google-formulas", is_flag=True,
              help="비어 있는 번역 칸에 구글 시트 번역 수식을 채웁니다 "
                   "(구글 드라이브에서 열면 바로 번역됩니다)")
def sheet(workdir: Path, langs: str | None, max_rows: int | None,
          google_formulas: bool) -> None:
    """엑셀을 다시 만듭니다. 언어 열을 바꾸거나 추가할 때 씁니다."""
    proj = _load_project(workdir)
    xlsx = workdir / SHEET_XLSX
    if xlsx.exists():
        problems: list[str] = []
        edits, _ = read_workbook(xlsx, problems)
        merge_into_project(proj, edits)
        if problems:
            _say(f"  ! {unfinished_message(problems)}")
    if langs:
        proj.languages = [l.strip() for l in langs.split(",") if l.strip()]
    proj.save(workdir / PROJECT_JSON)
    write_workbook(proj, xlsx, max_rows=max_rows, google_formulas=google_formulas)
    _say(f"엑셀 갱신: {xlsx} (언어 열: {', '.join(proj.languages)})")
    if google_formulas:
        _say("구글 시트용 수식을 채웠습니다. 사용법은 엑셀 안의 [정보] 시트에 있습니다.")


@main.command()
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
def restore(game_folder: Path) -> None:
    """번역 적용 전 원본으로 되돌립니다."""
    n = restore_backup(game_folder, progress=_say)
    _say(f"{n}개 파일 복원")


if __name__ == "__main__":
    sys.exit(main())
