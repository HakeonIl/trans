# SPDX-License-Identifier: GPL-3.0-or-later
"""NodeCanvas 대화 그래프 안의 실제 대사와 선택지.

NodeCanvas 는 그래프 전체를 MonoBehaviour 의 ``_serializedGraph`` 문자열에
JSON 으로 넣습니다. 평범한 타입트리 순회에서는 이 JSON 전체가 문자열 하나로
보이므로, 길이 제한에 걸려 대사가 통째로 빠집니다.

여기서는 화면에 나오는 것이 확실한 노드만 좁게 다룹니다. 그래프에는 에셋 ID,
변수 이름, 그림 레이어명도 아주 많아서 모든 문자열을 꺼내면 게임을 깨뜨리기
쉽습니다.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from .treepath import get_by_path, set_by_path


FIELD = "_serializedGraph"


@dataclasses.dataclass(frozen=True)
class Cell:
    text: str
    ptr: str
    note: str


_SAY_NODES = {
    "StatementNode": "대사",
    "PhoneCallSayNode": "전화 상대",
}
_CHOICE_NODES = {
    "MultipleChoiceNode": "선택지",
    "PhoneCallMultipleChoiceNode": "전화 선택지",
}


def load(tree: dict) -> dict | None:
    """타입트리가 NodeCanvas 그래프이면 파싱한 JSON, 아니면 ``None``."""
    raw = tree.get(FIELD)
    if not isinstance(raw, str) or not raw.lstrip().startswith("{"):
        return None
    try:
        graph = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        return None
    return graph


def _short_type(node: dict) -> str:
    return str(node.get("$type") or "").rsplit(".", 1)[-1]


def _scene(tree: dict) -> str:
    return str(tree.get("Nickname") or tree.get("m_Name") or "").strip()


def _note(scene: str, kind: str, speaker: str = "") -> str:
    parts = []
    if scene:
        parts.append(f"장면: {scene}")
    if speaker:
        parts.append(f"화자: {speaker}")
    parts.append(f"유형: {kind}")
    return " | ".join(parts)


def actor_labels(tree: dict, objects: dict[int, Any]) -> dict[str, str]:
    """actor parameter의 내부 별칭을 사람이 읽는 캐릭터명에 연결합니다.

    일부 Unity 판에서는 캐릭터 타입트리를 못 읽어도 원시 데이터의 문자열
    순서는 ``ID, 표시명, NodeCanvas 별칭, 설명...`` 으로 남습니다. 별칭과 같은
    문자열 바로 앞을 표시명으로 쓰되, 확실히 찾은 경우에만 반환합니다.
    """
    from . import unityraw

    out: dict[str, str] = {}
    params = tree.get("actorParameters")
    if not isinstance(params, list):
        return out
    for param in params:
        if not isinstance(param, dict):
            continue
        key = str(param.get("_keyName") or "").strip()
        pid = (param.get("_actorObject") or {}).get("m_PathID")
        obj = objects.get(pid)
        if not key or obj is None:
            continue
        try:
            strings = [s for _off, s in unityraw.scan(obj.get_raw_data(), limit=40)]
        except Exception:  # pragma: no cover - 망가진 참조는 별칭 그대로 쓰면 됩니다
            continue
        try:
            at = strings.index(key)
        except ValueError:
            continue
        if at <= 0 or not strings[at - 1].strip():
            continue
        label = f"{strings[at - 1]} ({key})"
        out[key] = label
        actor_id = str(param.get("_id") or "")
        if actor_id:
            out[actor_id] = label
    return out


def cells(tree: dict, actors: dict[str, str] | None = None) -> list[Cell] | None:
    """그래프의 번역 가능한 셀.

    ``None`` 은 NodeCanvas 그래프가 아니라는 뜻이고, 빈 목록은 그래프이지만
    지원하는 화면 문구 노드가 없다는 뜻입니다.
    """
    graph = load(tree)
    if graph is None:
        return None

    scene = _scene(tree)
    actors = actors or {}
    out: list[Cell] = []
    for ni, node in enumerate(graph["nodes"]):
        if not isinstance(node, dict):
            continue
        kind = _short_type(node)

        if kind in _SAY_NODES:
            statement = node.get("statement")
            text = statement.get("_text") if isinstance(statement, dict) else None
            if not isinstance(text, str) or not text.strip():
                continue
            if kind == "StatementNode":
                # actor 가 없는 StatementNode 는 이 게임을 포함한 NodeCanvas
                # 작품에서 내레이션이나 주인공 속말로 쓰입니다. 단정하지 않고
                # 둘 다 보여 주어 번역자가 판단하게 합니다.
                raw_speaker = str(node.get("_actorName") or "")
                actor_id = str(node.get("_actorParameterID") or "")
                speaker = (actors.get(raw_speaker) or actors.get(actor_id)
                           or raw_speaker or "내레이션/독백")
            else:
                # PhoneCallSayNode 자체에는 화자 필드가 없습니다. 선택지 노드와
                # 번갈아 나오므로 통화 상대의 말이라는 것까지만 확실합니다.
                speaker = "전화 상대"
            out.append(Cell(
                text=text,
                ptr=f"nodes[{ni}].statement._text",
                note=_note(scene, _SAY_NODES[kind], speaker),
            ))
            continue

        if kind in _CHOICE_NODES:
            choices = node.get("availableChoices")
            if not isinstance(choices, list):
                continue
            for ci, choice in enumerate(choices):
                if not isinstance(choice, dict):
                    continue
                statement = choice.get("statement")
                text = statement.get("_text") if isinstance(statement, dict) else None
                if not isinstance(text, str) or not text.strip():
                    continue
                out.append(Cell(
                    text=text,
                    ptr=f"nodes[{ni}].availableChoices[{ci}].statement._text",
                    note=_note(scene, _CHOICE_NODES[kind], "플레이어"),
                ))
    return out


def patch(raw: str, edits: dict[str, str]) -> str:
    """그래프 JSON 의 지정한 문구만 바꾸고 다시 직렬화합니다."""
    graph = json.loads(raw)
    for ptr, value in edits.items():
        # 잘못된 위치를 새로 만드는 대신 기존 문자열 자리만 허용합니다.
        before = get_by_path(graph, ptr)
        if not isinstance(before, str):
            raise TypeError(f"NodeCanvas 위치가 문자열이 아닙니다: {ptr}")
        set_by_path(graph, ptr, value)
    # 원본도 공백 없는 JSON 이고 비 ASCII 문자는 \uXXXX 로 저장합니다.
    # 키 순서는 json.loads 가 보존하므로 NodeCanvas 의 $id/$ref 관계도 그대로입니다.
    return json.dumps(graph, ensure_ascii=True, separators=(",", ":"))


def read(raw: str, ptr: str) -> Any:
    """검증용: 직렬화된 그래프의 한 위치를 읽습니다."""
    return get_by_path(json.loads(raw), ptr)
