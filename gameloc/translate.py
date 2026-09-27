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

"""자동 번역 엔진.

엔진은 갈아끼울 수 있습니다. ``Provider`` 를 상속해 ``_translate`` 하나만
구현하고 ``register`` 하면 화면 목록에 뜹니다.

공통으로 처리하는 것:
  · 제어문자 보호 (protect.py)   · 같은 문장 재사용 캐시
  · 동시 요청 제한 + 재시도       · 용어집 강제 적용
  · 깨진 결과는 버리고 사유 기록
"""

from __future__ import annotations

import inspect
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import protect, throttle

SETTINGS = Path.home() / ".gameloc" / "settings.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) gameloc"


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text("utf-8"))
    except Exception:
        return {}


def save_settings(d: dict) -> None:
    try:
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(json.dumps(d, ensure_ascii=False, indent=1), "utf-8")
    except Exception:
        pass


def _post(url: str, data: bytes, headers: dict, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _get_auth(url: str, key: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


class Unbatchable(Exception):
    """묶어 보냈더니 짝이 안 맞습니다. 하나씩 다시 보내야 합니다."""


def _llm_string_array(arr) -> list[str]:
    """Accept only the requested JSON array of translated strings."""
    if not isinstance(arr, list) or not all(isinstance(x, str) for x in arr):
        raise RuntimeError("translation response is not an array of strings")
    return arr


@dataclass
class Result:
    text: str = ""
    problem: str | None = None


class Provider:
    key_id = ""
    label = ""
    needs_key = False
    batch_size = 1
    workers = 4
    retry_delay = 1.5      # 재시도 간격(초). 테스트에서는 0으로 낮춥니다.
    start_gap = 0.0        # 처음부터 요청 사이에 둘 틈(초)
    note = ""

    def __init__(self, api_key: str = "", model: str = "") -> None:
        self.api_key = (api_key or "").strip()
        self.model = model

    def check(self) -> None:
        if self.needs_key and not self.api_key:
            raise RuntimeError(f"{self.label}는 API 키가 필요합니다.")

    def _translate(self, texts: list[str], src: str, dst: str) -> list[str]:
        raise NotImplementedError

    def _translate_contextual(self, texts: list[str], contexts: list[str],
                              src: str, dst: str) -> list[str]:
        """문맥을 받을 수 없는 번역기는 예전처럼 원문만 번역합니다."""
        return self._translate(texts, src, dst)


REGISTRY: dict[str, type[Provider]] = {}


def register(cls: type[Provider]) -> type[Provider]:
    REGISTRY[cls.key_id] = cls
    return cls


def provider_list() -> list[dict]:
    """화면에 보여 줄 엔진 목록.

    **이지트랜스는 깔려 있을 때만 뜹니다.** 우리가 배포하지 않는 물건이라,
    목록에 띄워 놓고 "받아 오세요" 라고 하면 불법 복제본을 받으라는 말이
    됩니다. 이미 있는 사람에게만 보입니다.
    """
    out = []
    for c in REGISTRY.values():
        if getattr(c, "only_if_present", None) and not c.only_if_present():
            continue
        out.append({"id": c.key_id, "label": c.label,
                    "needsKey": c.needs_key, "note": c.note})
    return out


@register
class GoogleFree(Provider):
    """구글 무료.

    ## 왜 묶어 보내는가

    크롬이 웹페이지를 번역할 때는 글 뭉텅이를 **한 번에** 보냅니다.
    그런데 우리는 문장 하나마다 따로 불렀습니다 — 3,295문장이면 3,295번.
    같은 양을 크롬은 수십 번에 끝냅니다.

    구글 무료는 정식 창구가 아니라 부르는 횟수가 많으면 그 IP 를 잠시
    막습니다. 그러니 **횟수를 줄이는 것이 곧 안 막히는 길**입니다.

        하나씩   3,295번 호출
        묶으면     165번 호출   ← 20분의 1

    ## 묶으면 위험한 것

    구글이 줄을 합치거나 순서를 바꿔 돌려주면 **번역이 통째로 어긋납니다.**
    429보다 훨씬 나쁩니다. 그래서 묶어 보낸 뒤 **개수가 맞는지 반드시
    확인하고, 어긋나면 그 묶음만 하나씩 다시 보냅니다.** 묶기가 실패해도
    결과는 옛날과 같고, 느려질 뿐입니다.
    """

    key_id = "google"
    label = "구글 번역 (무료, 키 없음)"
    needs_key = False
    # 크롬처럼 뭉텅이로 보냅니다. 어긋나면 알아서 하나씩으로 떨어집니다.
    batch_size = 20
    # 이 값은 1.2.0 부터 그대로입니다. 10만 문장을 문제없이 넘긴
    # 설정이라 함부로 낮추면 안 됩니다 — 낮췄다가 10만 문장이
    # 2.8시간에서 9.7시간이 될 뻔했습니다.
    workers = 3
    # 첫 타석만 살살. 잘 풀리면 곧 0 으로 풀려 제 속도가 납니다.
    start_gap = 0.2
    note = ("설정 없이 바로 됩니다. 여러 문장을 묶어 보내 덜 막힙니다. "
            "그래도 막히면 알아서 쉬었다 이어서 하니 그대로 두세요.")

    URL = "https://translate.googleapis.com/translate_a/single"
    batched = True        # 묶기가 통하는가 (안 통하면 하나씩)

    # 묶기가 **연달아** 이만큼 어긋나면 그만 묶습니다. 한 번 삐끗했다고 영영
    # 하나씩으로 떨어지면 호출이 20배가 되어 그게 더 큰 사고입니다. 성공하면
    # 세기를 0 으로 되돌립니다.
    MISS_LIMIT = 3
    _misses = 0

    @staticmethod
    def _pieces(inner) -> list:
        """번역 조각만 고릅니다. 조각은 ``[번역, 원문, ...]`` — 앞의 둘이 문자열인 리스트.

        언어 감지(``["en"]``)나 확신도(``[0.99]``) 같은 메타데이터도 리스트라서
        "리스트면 번역" 으로 읽으면 숫자가 번역 자리에 섞여 들어옵니다(TypeError).
        """
        return [p for p in inner
                if isinstance(p, list) and len(p) >= 2
                and isinstance(p[0], str) and isinstance(p[1], str)]

    # 큐(``translate_entries``)가 부를 때만 켭니다. 묶음이 안 통하면 여기서 하나씩
    # 다시 보내지 않고 ``Unbatchable`` 을 큐로 올려서, 문장마다 속도 제한·취소·
    # 재시도를 따로 거치게 합니다. ``_translate`` 를 직접 부르면 예전처럼 안에서
    # 하나씩 떨어집니다.
    split_on_miss = False

    @staticmethod
    def _norm(s: str) -> str:
        """공백의 **양**은 무시하되 **있고 없음**은 구별합니다.

        줄바꿈이 공백으로 바뀌어 돌아오는 정도는 같은 글로 보지만, ``"a b"`` 와
        ``"ab"`` 는 다른 글입니다. 공백을 전부 지우면 이 둘이 같아져서, 순서가
        밀려도 통과시켜 버립니다.
        """
        return " ".join(s.split())

    @classmethod
    def _same(cls, a: str, b: str) -> bool:
        return cls._norm(a) == cls._norm(b)

    @classmethod
    def _ambiguous(cls, texts: list[str]) -> bool:
        """묶음 안에 **정규화하면 같아지는 문장**이 둘 이상인가.

        그런 문장끼리는 원문 대조로 순서 밀림을 가릴 수 없습니다. 보내기 전에
        알 수 있으니 묶지 않고 하나씩 보냅니다(요청을 낭비하지 않습니다).
        """
        # 빈 문장은 보내지 않으니(``_call`` 이 걸러냅니다) 세지 않습니다.
        seen = [cls._norm(t) for t in texts if t.strip()]
        return len(set(seen)) != len(seen)

    def _read_single(self, data, text: str) -> str:
        """단건 응답을 **엄격하게** 읽습니다. 번역 구간은 ``data[0]`` 뿐입니다.

        구간 하나라도 모양이 다르면 버리고 넘어가지 않고 예외를 냅니다 — 버리면
        뒷부분이 빠진 번역이 **성공으로** 저장됩니다.
        """
        if (not isinstance(data, list) or not data
                or not isinstance(data[0], list) or not data[0]):
            raise RuntimeError("구글 번역 응답에 번역문이 없습니다.")
        parts = []
        for seg in data[0]:
            if not isinstance(seg, list) or not seg or not isinstance(seg[0], str):
                raise RuntimeError(
                    "구글 번역 응답 형식이 달라 번역문을 확인할 수 없습니다.")
            parts.append(seg[0])
        out = "".join(parts)
        if text.strip() and not out.strip():
            raise RuntimeError("구글 번역이 빈 번역문을 반환했습니다.")
        return out

    def _read_batch(self, data, n: int) -> list[tuple[str, str]]:
        """묶음 응답에서 ``(번역, 돌려받은 원문)`` 을 ``n`` 개까지 읽습니다."""
        got = []
        for chunk in data:
            if not isinstance(chunk, list):
                continue
            inner = chunk[0] if (chunk and isinstance(chunk[0], list)
                                 and chunk[0] and isinstance(chunk[0][0], list)) else chunk
            pieces = self._pieces(inner)
            if not pieces:
                continue                    # 번역 조각이 없는 덩어리는 세지 않습니다
            got.append(("".join(p[0] for p in pieces), "".join(p[1] for p in pieces)))
            if len(got) == n:
                break
        return got

    def _call(self, texts: list[str], src: str, dst: str) -> list[str]:
        """한 번 불러서 ``texts`` 개수만큼 돌려받습니다.

        이 창구는 ``q`` 를 여러 개 받아 각각을 따로 번역해 돌려줍니다.
        믿지 않는 것: 개수가 다른 것, 번역이 빈 것, **돌려받은 원문이 보낸
        문장과 다른 것**(개수가 맞아도 순서가 밀렸을 수 있습니다), 모양을 읽다
        예외가 난 것. 전부 ``Unbatchable`` — 짝이 어긋난 번역을 넣느니 하나씩
        다시 보내는 편이 낫습니다.
        """
        blank = [not t.strip() for t in texts]
        if any(blank):
            # 빈 문장은 보내지 않습니다(응답에서 자리를 못 찾습니다).
            send = [t for t, b in zip(texts, blank) if not b]
            part = iter(self._call(send, src, dst) if send else [])
            return ["" if b else next(part) for b in blank]

        args = [("client", "gtx"), ("sl", src or "auto"), ("tl", dst),
                ("dt", "t")] + [("q", t) for t in texts]
        raw = _get(self.URL + "?" + urllib.parse.urlencode(args))
        data = json.loads(raw.decode("utf-8"))

        if len(texts) == 1:
            return [self._read_single(data, texts[0])]

        # 여러 개를 보내면 [[...], [[...]], ...] 처럼 묶음마다 하나씩
        # 옵니다. 모양이 조금씩 다를 수 있어 너그럽게 읽되, **개수와 대응은
        # 엄격하게** 봅니다.
        try:
            got = self._read_batch(data, len(texts))
        except (TypeError, ValueError, IndexError, KeyError) as exc:
            raise Unbatchable(
                f"묶음 응답의 모양을 읽지 못했습니다 ({type(exc).__name__})") from exc
        if len(got) != len(texts):
            raise Unbatchable(
                f"묶어 보냈더니 {len(texts)}개가 아니라 {len(got)}개가 왔습니다")
        for sent, (out_text, echoed) in zip(texts, got):
            # 개수만 맞고 **내용을 잘못 읽었을** 수도 있습니다. 원문이 있는데
            # 번역이 비었다면 잘못 읽은 것으로 봅니다.
            if not out_text.strip():
                raise Unbatchable("묶음에서 빈 칸이 나왔습니다")
            # 구글이 원문을 그대로 돌려주므로, 보낸 문장과 다르면 순서가 밀린 것.
            if not self._same(sent, echoed):
                raise Unbatchable("돌려받은 원문이 보낸 문장과 다릅니다")
        return [out_text for out_text, _ in got]

    def _translate(self, texts: list[str], src: str, dst: str) -> list[str]:
        if len(texts) > 1:
            if self.batched and not self._ambiguous(texts):
                try:
                    out = self._call(texts, src, dst)
                    self._misses = 0
                    return out
                except Unbatchable:
                    # 이 묶음은 안 통했습니다. 연달아 어긋나면 그만 묶습니다.
                    self._misses += 1
                    if self._misses >= self.MISS_LIMIT:
                        self.batched = False
                    if self.split_on_miss:
                        raise
            elif self.split_on_miss:
                raise Unbatchable("묶지 않고 하나씩 보냅니다")
            # 여기부터는 하나씩 — 느릴 뿐, 결과는 옛날과 같습니다.
        out = []
        for t in texts:
            out.extend(self._call([t], src, dst))
        return out



@register
class DeepL(Provider):
    key_id = "deepl"
    label = "DeepL (무료 키, 월 50만 자)"
    needs_key = True
    batch_size = 40
    workers = 3
    note = "deepl.com 에서 무료 키를 받으세요. 일→한 품질이 좋고 안 막힙니다."

    def _endpoint(self) -> str:
        return ("https://api-free.deepl.com/v2/translate"
                if self.api_key.endswith(":fx")
                else "https://api.deepl.com/v2/translate")

    def _translate(self, texts: list[str], src: str, dst: str) -> list[str]:
        payload = {"text": texts, "target_lang": dst.upper()}
        if src and src != "auto":
            payload["source_lang"] = src.upper()
        raw = _post(self._endpoint(), json.dumps(payload).encode("utf-8"),
                    {"Authorization": f"DeepL-Auth-Key {self.api_key}",
                     "Content-Type": "application/json"})
        return [x["text"] for x in json.loads(raw)["translations"]]


@register
class ClaudeAPI(Provider):
    key_id = "claude"
    label = "Claude API (유료 키)"
    needs_key = True
    batch_size = 20
    workers = 2
    note = "문맥과 용어집을 함께 넘겨 말투를 유지합니다. 토큰당 과금됩니다."

    SYSTEM = (
        "당신은 게임 현지화 번역가입니다. 주어진 JSON 배열의 각 항목을 "
        "{src}에서 {dst}로 번역해, 같은 길이의 JSON 배열로만 답하세요.\n"
        "입력 항목은 문자열 또는 {{\"text\": 원문, \"context\": 장면·화자}} "
        "객체입니다. 객체이면 context를 말투 판단에만 쓰고 text만 번역하세요.\n"
        "규칙:\n"
        "- ⟦0⟧ ⟦1⟧ 같은 토큰은 게임 제어문자입니다. 번역하지 말고 위치만 자연스럽게 "
        "유지하세요. 개수와 번호가 원문과 정확히 같아야 합니다.\n"
        "- 줄바꿈은 원문과 같은 개수로 유지하세요. 대사창 줄 수와 직결됩니다.\n"
        "- UI 라벨처럼 짧은 항목은 짧게. 원문보다 길어지면 화면에서 잘립니다.\n"
        "- 설명이나 사족 없이 JSON 배열만 출력하세요."
    )

    def __init__(self, api_key: str = "", model: str = "") -> None:
        super().__init__(api_key, model or "claude-sonnet-4-6")
        self.glossary_text = ""

    def _request(self, texts: list[str], payload, src: str, dst: str) -> list[str]:
        sys_prompt = self.SYSTEM.format(src=src or "원어", dst=dst)
        if self.glossary_text:
            sys_prompt += "\n용어집(반드시 이대로):\n" + self.glossary_text
        body = {
            "model": self.model,
            "max_tokens": 8000,
            "system": sys_prompt,
            "messages": [{"role": "user",
                          "content": json.dumps(payload, ensure_ascii=False)}],
        }
        raw = _post("https://api.anthropic.com/v1/messages",
                    json.dumps(body).encode("utf-8"),
                    {"x-api-key": self.api_key,
                     "anthropic-version": "2023-06-01",
                     "Content-Type": "application/json"}, timeout=180)
        text = "".join(b.get("text", "") for b in json.loads(raw)["content"])
        m = re.search(r"\[.*\]", text, re.S)
        arr = json.loads(m.group(0) if m else text)
        if len(arr) != len(texts):
            raise RuntimeError(f"응답 개수가 맞지 않습니다 ({len(arr)}/{len(texts)})")
        return _llm_string_array(arr)

    def _translate(self, texts: list[str], src: str, dst: str) -> list[str]:
        return self._request(texts, texts, src, dst)

    def _translate_contextual(self, texts: list[str], contexts: list[str],
                              src: str, dst: str) -> list[str]:
        if not any(contexts):
            return self._translate(texts, src, dst)
        payload = [{"text": text, "context": context}
                   for text, context in zip(texts, contexts)]
        return self._request(texts, payload, src, dst)


class _LLM(Provider):
    """GPT·Gemini 처럼 '말로 시키는' 번역기의 공통 부분.

    똑같은 지시문을 쓰고, JSON 배열을 받아 개수만 맞는지 봅니다. 서로
    다른 것은 주소와 몸통 모양뿐이라 :meth:`_ask` 하나만 다시 씁니다.
    """

    batch_size = 20
    workers = 2
    glossary_text = ""

    def _prompt(self, src: str, dst: str) -> str:
        out = ClaudeAPI.SYSTEM.format(src=src or "원어", dst=dst)
        if self.glossary_text:
            out += "\n용어집(반드시 이대로):\n" + self.glossary_text
        return out

    def _ask(self, prompt: str, payload: str) -> str:
        raise NotImplementedError

    def _translate(self, texts: list[str], src: str, dst: str) -> list[str]:
        text = self._ask(self._prompt(src, dst),
                          json.dumps(texts, ensure_ascii=False))
        m = re.search(r"\[.*\]", text, re.S)
        arr = json.loads(m.group(0) if m else text)
        if len(arr) != len(texts):
            raise RuntimeError(f"응답 개수가 맞지 않습니다 ({len(arr)}/{len(texts)})")
        return _llm_string_array(arr)

    def _translate_contextual(self, texts: list[str], contexts: list[str],
                              src: str, dst: str) -> list[str]:
        if not any(contexts):
            return self._translate(texts, src, dst)
        payload = [{"text": text, "context": context}
                   for text, context in zip(texts, contexts)]
        text = self._ask(self._prompt(src, dst),
                         json.dumps(payload, ensure_ascii=False))
        m = re.search(r"\[.*\]", text, re.S)
        arr = json.loads(m.group(0) if m else text)
        if len(arr) != len(texts):
            raise RuntimeError(f"응답 개수가 맞지 않습니다 ({len(arr)}/{len(texts)})")
        return _llm_string_array(arr)


@register
class OpenAIAPI(_LLM):
    key_id = "openai"
    label = "ChatGPT (OpenAI 키)"
    needs_key = True
    note = "platform.openai.com 에서 키를 받으세요. 문맥과 용어집을 함께 넘깁니다."

    def __init__(self, api_key: str = "", model: str = "") -> None:
        super().__init__(api_key, model or "gpt-4.1-mini")

    def _ask(self, prompt: str, payload: str) -> str:
        body = {"model": self.model,
                "messages": [{"role": "system", "content": prompt},
                             {"role": "user", "content": payload}]}
        raw = _post("https://api.openai.com/v1/chat/completions",
                    json.dumps(body).encode("utf-8"),
                    {"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"}, timeout=180)
        return json.loads(raw)["choices"][0]["message"]["content"]


@register
class CustomOpenAI(_LLM):
    """주소를 직접 넣는 곳. **OpenAI 와 같은 모양으로 말하는 서버 전부.**

    ## 왜 엔진을 하나씩 안 붙이는가

    사흘 사이에 NVIDIA NIM, LM Studio, Vertex AI 를 넣어 달라는 요청이
    따로따로 왔습니다. 셋을 뜯어보니 **전부 같은 모양**이었습니다 —
    ``POST /chat/completions`` 에 ``Authorization: Bearer`` 하나.

    그래서 셋을 붙이는 대신 **칸 세 개**를 둡니다. 이러면 우리가 목록에
    없는 곳도 사용자가 스스로 넣습니다. 어느 서비스가 모델 이름을 바꾸든
    문 닫든, 우리를 기다릴 필요가 없습니다 — 오늘 아침 Gemini 가
    ``gemini-2.0-flash`` 를 없앴을 때 사람들이 겪은 게 정확히 그거였습니다.

    ## 넣는 값

        주소   https://integrate.api.nvidia.com/v1   (NVIDIA NIM)
               http://localhost:1234/v1              (LM Studio)
               http://localhost:11434/v1             (Ollama)
        모델   그 서비스가 부르는 이름 그대로
        키     로컬에서 돌리는 것이면 비워도 됩니다

    ``/chat/completions`` 는 우리가 붙입니다. 끝에 ``/v1`` 까지만 넣으세요.
    """

    key_id = "custom"
    label = "직접 넣기 (OpenAI 호환)"
    needs_key = False          # 로컬 서버는 키가 없습니다
    note = ("NVIDIA NIM · LM Studio · Ollama 처럼 OpenAI 와 같은 방식으로 "
            "말하는 곳이면 다 됩니다. 주소와 모델 이름을 넣으세요.")

    def __init__(self, api_key: str = "", model: str = "",
                 base_url: str = "") -> None:
        super().__init__(api_key, model or "")
        self.base_url = (base_url or "").strip().rstrip("/")

    def _url(self) -> str:
        if not self.base_url:
            raise RuntimeError(
                "주소를 넣어야 합니다. 보기: http://localhost:1234/v1")
        base = self.base_url
        # 사람이 어디까지 붙여넣을지 모릅니다. 셋 다 받아 줍니다.
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return base + "/chat/completions"
        return base + "/v1/chat/completions"

    def _ask(self, prompt: str, payload: str) -> str:
        if not self.model:
            raise RuntimeError("모델 이름을 넣어야 합니다.")
        body = {"model": self.model,
                "messages": [{"role": "system", "content": prompt},
                             {"role": "user", "content": payload}]}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        raw = _post(self._url(), json.dumps(body).encode("utf-8"),
                    headers, timeout=300)
        got = json.loads(raw)
        try:
            return got["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            # 남의 서버라 모양이 다를 수 있습니다. 뭉개지 말고 보여 줍니다.
            raise RuntimeError(
                "대답 모양이 OpenAI 와 다릅니다: "
                + json.dumps(got, ensure_ascii=False)[:200]) from None


@register
class GeminiAPI(_LLM):
    """구글 Gemini.

    **모델 이름을 코드에 박아 두면 언젠가 반드시 죽습니다.** 실제로
    그랬습니다 — ``gemini-2.0-flash`` 가 서비스 종료되면서 이 엔진을 쓰는
    사람 전부가 404 를 받았고, 화면에 모델을 바꿀 칸도 없어 스스로
    빠져나갈 방법이 없었습니다.

    그래서 이제 **키에게 직접 물어봅니다.** 구글은 "이 키로 쓸 수 있는
    모델 목록" 을 알려주는 창구를 두고 있습니다. 거기서 골라 쓰면 이름이
    바뀌어도 따라갑니다. 물어보지 못하면 아는 이름들을 차례로 시도합니다.
    """

    key_id = "gemini"
    label = "Gemini (구글 AI 키)"
    needs_key = True
    note = ("aistudio.google.com 에서 무료 키를 받을 수 있습니다. "
            "쓸 모델은 키에게 물어 알아서 고릅니다.")

    BASE = "https://generativelanguage.googleapis.com/v1beta"
    # 물어보지 못했을 때 차례로 시도할 이름들.
    #
    # **여기에 지어낸 이름을 넣으면 안 됩니다.** 실제로 그랬습니다 — 제가
    # 있을 법한 이름을 앞에 두었더니, 목록을 못 물어본 사람은 없는 모델로
    # 45,000건을 통째로 실패했습니다. 그래서 구글이 **별칭으로 유지하는**
    # 이름만 둡니다. 별칭은 실제 모델이 바뀌어도 따라갑니다.
    KNOWN = ("gemini-flash-latest", "gemini-2.5-flash", "gemini-2.0-flash")

    def __init__(self, api_key: str = "", model: str = "") -> None:
        super().__init__(api_key, model or "")
        self._picked = model or ""
        self._dead: set[str] = set()       # 404 를 받은 이름들
        self._asked = False                # 목록을 물어봤나

    def available(self) -> list[str]:
        """이 키로 쓸 수 있는 모델 이름들. 못 물어보면 빈 목록."""
        try:
            raw = _get_auth(f"{self.BASE}/models", self.api_key)
            got = json.loads(raw).get("models") or []
        except Exception:                               # noqa: BLE001
            return []
        out = []
        for m in got:
            name = str(m.get("name") or "").rsplit("/", 1)[-1]
            if not name:
                continue
            if "generateContent" not in (m.get("supportedGenerationMethods")
                                         or ["generateContent"]):
                continue
            out.append(name)
        return out

    def candidates(self) -> list[str]:
        """써 볼 이름들. 키가 알려 준 것이 먼저, 그다음이 별칭."""
        got = [m for m in self.available()
               if "image" not in m and "preview" not in m and "exp" not in m]
        # 값싸고 빠른 flash 계열을 먼저. 없으면 아무거나.
        got.sort(key=lambda m: (0 if "flash" in m else 1, len(m)))
        out = got + [k for k in self.KNOWN if k not in got]
        return [m for m in out if m not in self._dead]

    def _model(self) -> str:
        if self._picked and self._picked not in self._dead:
            return self._picked
        left = self.candidates()
        if not left:
            raise RuntimeError(self._nothing_works())
        self._picked = left[0]
        return self._picked

    def _nothing_works(self) -> str:
        """**한 번만** 나오는 말이라야 뜻이 있습니다.

        예전에는 45,000건이 같은 말을 2,257번 되풀이했습니다. 게다가
        '최신판으로 올리세요' 라고 했는데, 그분은 이미 최신판이었습니다.
        """
        seen = self.available()
        if seen:
            return ("이 키로 쓸 수 있는 모델이 있는데 전부 거절당했습니다: "
                    + ", ".join(seen[:6]))
        return ("이 키로 쓸 수 있는 모델을 하나도 못 찾았습니다. "
                "aistudio.google.com 에서 키가 살아 있는지, "
                "Generative Language API 가 켜져 있는지 보세요.")

    def check(self) -> None:
        """**시작하기 전에** 한 번 확인합니다.

        4만 건을 다 실패하고 나서 알려주는 것은 알려주는 게 아닙니다.
        """
        super().check()
        if self._picked:
            return
        if not self.candidates():
            raise RuntimeError(self._nothing_works())

    def _ask(self, prompt: str, payload: str) -> str:
        body = {"systemInstruction": {"parts": [{"text": prompt}]},
                "contents": [{"role": "user", "parts": [{"text": payload}]}]}
        last: Exception | None = None
        # 이름 하나가 안 되면 **다음 이름으로 갈아탑니다.** 예전에는 처음
        # 고른 이름을 끝까지 물고 늘어져, 그게 없는 이름이면 전부
        # 실패했습니다.
        for _try in range(4):
            name = self._model()
            try:
                raw = _post(f"{self.BASE}/models/{name}:generateContent",
                            json.dumps(body).encode("utf-8"),
                            {"x-goog-api-key": self.api_key,
                             "Content-Type": "application/json"}, timeout=180)
            except Exception as exc:                    # noqa: BLE001
                if getattr(exc, "code", None) != 404:
                    raise
                self._dead.add(name)
                self._picked = ""
                last = exc
                continue
            cand = json.loads(raw)["candidates"][0]
            return "".join(part.get("text", "")
                           for part in cand["content"]["parts"])
        raise RuntimeError(self._nothing_works()) from last


@register
class Papago(Provider):
    key_id = "papago"
    label = "파파고 (네이버 클라우드 키)"
    needs_key = True
    batch_size = 1
    workers = 2
    note = ("네이버 클라우드에서 받은 두 값을 '아이디:비밀키' 처럼 "
            "가운데 쌍점을 넣어 한 줄로 적으세요. 한국어 말맛이 좋습니다.")

    # 파파고가 아는 말. 모르는 말을 보내면 400 이 옵니다.
    KNOWN = {"ko", "en", "ja", "zh-CN", "zh-TW", "vi", "id", "th", "de",
             "ru", "es", "it", "fr"}
    ALIAS = {"zh": "zh-CN", "zh-cn": "zh-CN", "zh-tw": "zh-TW", "jp": "ja"}

    def check(self) -> None:
        super().check()
        if ":" not in self.api_key:
            raise RuntimeError(
                "파파고는 키가 둘입니다. '아이디:비밀키' 처럼 가운데 쌍점을 "
                "넣어 한 줄로 적어 주세요.")

    def _lang(self, code: str) -> str:
        code = (code or "").strip()
        code = self.ALIAS.get(code.lower(), code)
        if code not in self.KNOWN:
            raise RuntimeError(f"파파고는 '{code}' 를 모릅니다. "
                               "원문 언어를 직접 골라 주세요.")
        return code

    def _translate(self, texts: list[str], src: str, dst: str) -> list[str]:
        cid, _, secret = self.api_key.partition(":")
        headers = {"X-NCP-APIGW-API-KEY-ID": cid.strip(),
                   "X-NCP-APIGW-API-KEY": secret.strip(),
                   "Content-Type": "application/x-www-form-urlencoded"}
        out = []
        for t in texts:
            body = urllib.parse.urlencode({
                "source": self._lang(src), "target": self._lang(dst),
                "text": t}).encode("utf-8")
            raw = _post("https://naveropenapi.apigw.ntruss.com"
                        "/nmt/v1/translation", body, headers)
            out.append(json.loads(raw)["message"]["result"]["translatedText"])
        return out


def _why(exc: Exception | None) -> str:
    """오류를 사람이 읽을 수 있는 말로. 원래 내용도 뒤에 남깁니다."""
    if exc is None:
        return "알 수 없는 까닭"
    said = throttle.busy_for(exc)
    if said is not None:
        return ("번역 서버가 계속 막습니다(너무 많이 불렀습니다). "
                "조금 뒤에 다시 해 보세요.")
    code = getattr(exc, "code", None)
    if code == 404:
        # 실제로 겪은 사고입니다. gemini-2.0-flash 가 서비스 종료되자
        # 그 엔진을 쓰던 사람 전부가 'HTTP Error 404' 만 보고 왜인지
        # 몰랐습니다. 무엇이 없어진 것인지 말해 줘야 합니다.
        # '최신판으로 올리세요' 라고 하면 안 됩니다. 실제로 최신판을 쓰던
        # 분이 그 말을 45,000번 봤습니다. 고칠 수 있는 것을 말해야 합니다.
        return ("번역기가 그 모델을 모른다고 합니다. 이름이 바뀌었거나 "
                "이 키에 권한이 없습니다. aistudio.google.com 에서 키가 "
                "살아 있는지 보시고, 그래도 안 되면 다른 엔진을 써 보세요.")
    if code in (401, 403):
        return "API 키가 틀렸거나 권한이 없습니다."
    if code == 400:
        return f"번역 서버가 요청을 거절했습니다. ({exc})"
    if isinstance(exc, (TimeoutError, OSError)) and code is None:
        return f"인터넷 연결이 끊겼거나 응답이 없습니다. ({exc})"
    return f"{type(exc).__name__}: {exc}"


@dataclass
class Report:
    total: int = 0
    done: int = 0
    skipped: int = 0
    failed: int = 0
    throttled: int = 0                   # 서버가 '잠깐 쉬어라' 한 횟수
    note: str = ""                       # 사람에게 해 줄 말
    problems: list[str] = field(default_factory=list)

    def summary(self) -> str:
        s = f"자동 번역 {self.done}건 완료"
        if self.failed:
            s += f", 실패 {self.failed}건"
        if self.skipped:
            s += f", 건너뜀 {self.skipped}건"
        if self.throttled:
            s += f" (서버가 {self.throttled}번 막아 쉬었다 이어서 했습니다)"
        return s


def make_provider(pid: str, api_key: str = "", model: str = "",
                  base_url: str = "") -> Provider:
    cls = REGISTRY.get(pid)
    if cls is None:
        raise RuntimeError(f"모르는 번역 엔진: {pid}")
    # 주소를 받는 엔진은 지금 하나뿐입니다. 없는 데 넘기면 터지므로
    # 받을 수 있는 곳에만 줍니다.
    if "base_url" in inspect.signature(cls.__init__).parameters:
        p = cls(api_key, model, base_url)
    else:
        p = cls(api_key, model)
    p.check()
    return p


def translate_entries(
    entries: list,
    provider: Provider,
    *,
    src: str = "auto",
    dst: str = "ko",
    glossary: dict[str, str] | None = None,
    lang_col: str | None = None,
    engine: str = "",
    speed: str = "",
    progress=lambda m: None,
    should_stop=lambda: False,
) -> Report:
    """``entries`` 의 원문을 번역해 ``translations[lang_col]`` 에 채웁니다.

    ``speed`` 는 **키 없이 쓰는 구글 무료에만** 뜻이 있습니다. 키를 넣고
    쓰는 곳(DeepL·Gemini·GPT)은 이런 식으로 IP 를 막지 않습니다.
    """
    lang_col = lang_col or dst
    glossary = glossary or {}
    rep = Report(total=len(entries))
    if not entries:
        return rep

    if isinstance(provider, (ClaudeAPI, _LLM)) and glossary:
        provider.glossary_text = "\n".join(f"{a} → {b}" for a, b in glossary.items())

    # 같은 문장이라도 화자·장면이 다르면 말투가 달라질 수 있습니다. 문맥까지
    # 같은 것만 한 번 부릅니다. 문맥이 없는 기존 프로젝트는 예전과 같습니다.
    uniq: dict[tuple[str, str], list] = {}
    for e in entries:
        uniq.setdefault((e.text, e.note or ""), []).append(e)
    progress(f"번역할 문장 {len(uniq)}개 (항목 {len(entries)}개)")

    masked = {key: protect.mask(key[0], engine=engine) for key in uniq}
    keys = list(uniq)
    chunks = [keys[i:i + provider.batch_size]
              for i in range(0, len(keys), provider.batch_size)]

    results: dict[tuple[str, str], Result] = {}
    lock = threading.Lock()
    # 속도는 구글 무료에만 걸립니다. 그 밖의 곳은 사용자 IP 가 아니라
    # 키로 셈해서, 갈래를 늘린다고 막히지 않습니다.
    tune = throttle.speed_of(speed) if isinstance(provider, GoogleFree) else None
    if tune:
        provider.workers = tune["workers"]
        progress(f"속도: {tune['label']} — {tune['why']}")

    brake = throttle.Brake(
        scale=getattr(provider, "wait_scale", 1.0),
        gap=tune["gap"] if tune else getattr(provider, "start_gap", 0.0))
    counter = {"n": 0}
    demoted = {"done": False}

    def work(chunk: list[str]) -> None:
        if should_stop():
            return
        payload = [masked[key][0] for key in chunk]
        contexts = [key[1] for key in chunk]
        last = None
        # 막혀서 쉬는 것은 '실패' 가 아니라 '기다림' 입니다. 그래서 보통
        # 오류보다 훨씬 여러 번 다시 해 봅니다. 안 그러면 서버가 잠깐
        # 문을 닫은 것만으로 게임 전체 번역이 날아갑니다.
        tries = busy_tries = 0
        while tries < 3 and busy_tries < len(throttle.WAITS) + 1:
            brake.wait(should_stop)
            if should_stop():
                return
            try:
                got = provider._translate_contextual(payload, contexts, src, dst)
                brake.eased()
                break
            except Unbatchable:
                # 묶음이 안 통했습니다. 문장마다 따로 보냅니다 — 각각 속도
                # 제한·취소·재시도를 거칩니다. 한 번의 제한 검사 안에서 몰아
                # 보내면 요청마다 걸려야 할 제한을 건너뜁니다.
                for key in chunk:
                    work([key])
                return
            except Exception as exc:  # noqa: BLE001
                last = exc
                if should_stop():
                    return
                said = throttle.busy_for(exc)
                if said is None:
                    tries += 1
                    time.sleep(provider.retry_delay * tries)
                    continue
                busy_tries += 1
                secs = brake.hit(said)
                # '빠르게' 가 이 컴퓨터에서 안 통한다는 것이 드러나면
                # 스스로 내려갑니다. 사용자가 고른 것을 뒤집는 일이니
                # 반드시 화면에 알리고 합니다.
                if (tune and speed == "fast" and not demoted["done"]
                        and brake.hits >= throttle.GIVE_UP_FAST):
                    demoted["done"] = True
                    if brake.slow_down(throttle.SPEEDS["normal"]["gap"]):
                        with lock:
                            progress("  · [빠르게] 로 " 
                                     f"{brake.hits}번 막혔습니다. 이대로면 "
                                     "오히려 더 느립니다 — [보통] 속도로 "
                                     "낮춰서 이어 갑니다.")
                with lock:
                    if brake.hits == 1 or brake.hits % 5 == 0:
                        # '막았습니다' 는 사고처럼 읽혀서 버그로 신고가
                        # 들어옵니다. 정상 동작이라는 게 보여야 합니다.
                        progress(f"  · 잠시 쉬어 갑니다 ({secs:.0f}초) — "
                                 "서버가 속도를 늦추라고 합니다. "
                                 "그대로 두시면 알아서 이어서 합니다.")
        else:
            why = _why(last)
            with lock:
                for key in chunk:
                    results[key] = Result(problem=why)
                counter["n"] += len(chunk)
                progress(f"  [{counter['n']}/{len(keys)}] 실패: {why}")
            return

        with lock:
            for key, out in zip(chunk, got):
                src_text = key[0]
                codes = masked[key][1]
                final, problem = protect.roundtrip(src_text, out, codes)
                if problem:
                    results[key] = Result(problem=problem)
                else:
                    results[key] = Result(
                        text=protect.apply_glossary(final, glossary))
            counter["n"] += len(chunk)
            progress(f"  [{counter['n']}/{len(keys)}]")

    was_split = getattr(provider, "split_on_miss", None)
    if isinstance(provider, GoogleFree):
        provider.split_on_miss = True
    try:
        with ThreadPoolExecutor(max_workers=provider.workers) as pool:
            list(pool.map(work, chunks))
    finally:
        if was_split is not None:
            provider.split_on_miss = was_split

    for key, group in uniq.items():
        text = key[0]
        r = results.get(key)
        if r is None or r.problem:
            rep.failed += len(group)
            if r and r.problem and len(rep.problems) < 40:
                rep.problems.append(f"{text[:40]} — {r.problem}")
            continue
        if not r.text.strip() or r.text.strip() == text.strip():
            rep.skipped += len(group)
            continue
        for e in group:
            e.translations[lang_col] = r.text
            if lang_col not in e.machine:
                e.machine.append(lang_col)
            rep.done += 1
    rep.throttled = brake.hits
    tip = brake.advice(provider.label)
    if tip:
        rep.note = tip
        progress("  " + tip)
    return rep


@register
class EzTransXP(Provider):
    """이지트랜스(EZTrans XP) — **이미 깔려 있으면** 쓰는 오프라인 번역기.

    한국 게임 번역판에서 "뚝딱 되는" 도구들의 정체입니다. 인터넷을 안 써서
    한도도 차단도 없고 15,000줄이 몇 분입니다. 대신 20년 된 규칙 엔진이라
    품질이 나쁩니다 — **초벌용**입니다.

    우리가 배포하지 않습니다. 상용 프로그램이고 정상 판매처가 없어서,
    이미 깔려 있는 것을 찾아 쓸 뿐입니다. 없으면 목록에 안 뜹니다.
    """

    key_id = "eztrans"
    label = "이지트랜스 (이 컴퓨터, 무제한)"
    needs_key = False
    note = ("인터넷을 안 써서 한도도 차단도 없고 아주 빠릅니다. "
            "대신 품질이 낮으니 초벌로 쓰고 손보세요. 일본어 → 한국어만 됩니다.")
    batch_size = 200
    workers = 1

    def __init__(self, key: str = "", model: str = "", base_url: str = ""):
        super().__init__(key)
        from . import eztrans as ez

        self._ez = ez
        self._engine = ez.Engine(base_url)      # 주소 칸에 폴더를 넣게 합니다

    @staticmethod
    def only_if_present() -> bool:
        from . import eztrans as ez

        return ez.installed()

    def check(self) -> str:
        return self._engine.why

    def _translate(self, texts: list[str], src: str, dst: str) -> list[str]:
        if dst != "ko":
            raise RuntimeError("이지트랜스는 일본어를 한국어로만 바꿉니다.")
        got = self._engine.translate(list(texts))
        if len(got) != len(texts):
            raise RuntimeError(
                f"보낸 것 {len(texts)}개, 돌아온 것 {len(got)}개 — 수가 안 맞습니다.")
        return got

    def close(self) -> None:
        self._engine.close()
