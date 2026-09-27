# SPDX-License-Identifier: GPL-3.0-or-later
"""[적용] 을 **몇 번을 눌러도 같은 결과**가 되게 합니다.

## 무엇이 문제였나

적용은 문자열이 **몇 번째 바이트에 있는지**로 기억했습니다. 그런데 한 번
넣고 나면 그 자리가 전부 밀립니다. 두 번째 적용은 처음에 기억해 둔 옛
자리를 그대로 쓰니 엉뚱한 곳을 가리킵니다.

    [ko] 파일 2개, 문자열 5개 적용 | 실패 17건
      ! level1:  오프셋 356 의 문자열이 범위를 넘습니다
      ! level10: 오프셋 272 의 문자열이 범위를 넘습니다
      ! …  (17개 파일 전부)

실수로 두 번 누르는 것만의 문제가 아닙니다. 이게 늘 하는 일입니다.

    뽑기 → 자동 번역 → 적용 → 게임에서 어색한 데 발견
    → 표에서 고친다 → 다시 적용        ← 여기서 깨집니다

## 어떻게 고쳤나

**자리를 안 쓰고 글로 찾습니다.**

    ① 백업이 있으면 먼저 원래대로 되돌린다
    ② 지금 게임 폴더에서 **다시 뽑는다**   ← 자리는 항상 새것
    ③ 뽑힌 원문을 번역표에서 찾아 짝지운다
    ④ 원래 쓰던 적용기에 넘긴다

밀릴 자리를 저장하지 않으니 밀릴 일이 없습니다.

처음 누르는 것이면 ①②③ 을 건너뛰고 곧장 넣습니다 — 되돌릴 것도,
밀린 자리도 없기 때문입니다. 빠른 길은 빠른 채로 둡니다.
"""

from __future__ import annotations

from pathlib import Path


def _noop(_: str) -> None:
    pass


def reapply(proj, lang: str, game_root: Path, *, dry_run: bool = False,
            progress=_noop):
    """``ApplyReport`` 를 돌려줍니다. 부르는 쪽은 달라질 게 없습니다."""
    from . import apply as ap
    from . import extract as ex

    root = Path(game_root)
    if ap._backup_root(root) is None:
        return ap.apply(proj, lang, dry_run=dry_run, game_root=root,
                        progress=progress)

    # 번역표를 먼저 챙깁니다. 다시 뽑으면 proj 의 자리 정보는 버리지만
    # **사람이 채운 번역문**은 그대로 살려야 합니다.
    translated = [e for e in proj.entries
                  if (e.translations.get(lang) or "").strip()
                  and e.translations[lang] != e.text]
    table = {e.id: e.translations[lang] for e in translated}
    by_context = {(e.text, e.note): e.translations[lang] for e in translated
                  if e.note}
    by_text: dict[str, list] = {}
    for e in translated:
        by_text.setdefault(e.text, []).append(e.translations[lang])
    if not table:
        return ap.apply(proj, lang, dry_run=dry_run, game_root=root,
                        progress=progress)

    progress("이미 한 번 넣은 폴더입니다. 되돌리고 처음부터 다시 넣습니다.")
    n = ap.restore(root, progress=lambda _m: None)
    if n:
        progress(f"  · 원본 {n}개 파일로 되돌렸습니다.")

    fresh = ex.extract(root, source_lang=proj.source_lang or "all",
                       progress=progress)
    hit = 0
    for e in fresh.entries:
        new = table.get(e.id) or by_context.get((e.text, e.note))
        if not new and len(by_text.get(e.text, [])) == 1:
            new = by_text[e.text][0]
        if new:
            e.translations[lang] = new
            hit += 1
    progress(f"  · 번역표와 맞은 문장 {hit:,}개 / {len(table):,}개")
    return ap.apply(fresh, lang, dry_run=dry_run, game_root=root,
                    progress=progress)
