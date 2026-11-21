"""翻译管线：分批、缓存、成本控制、并发、重试。

三条设计原则：
  1. **绝不重复请求**：缓存键 = sha256(英文 + 插件 + 术语表版本 + 模型 + prompt 版本)
  2. **绝不超预算**：实时估算花费，超限立即暂停并告知用户
  3. **绝不阻塞界面**：进度以回调上报，可暂停/取消
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional

from .client import AIError, ChatClient
from .gates import GateReport, gates_from_text, run_gates
from .prompt import build_messages, PROMPT_VERSION
from .providers import AIConfig

#: 粗略单价（美元 / 1M tokens），仅用于预算提示，不追求精确
PRICE_TABLE: Dict[str, tuple] = {
    "deepseek-chat": (0.14, 0.28),
    "deepseek-reasoner": (0.55, 2.19),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "qwen-plus": (0.11, 0.28),
    "glm-4-flash": (0.014, 0.014),
    "moonshot-v1-8k": (1.68, 1.68),
}
DEFAULT_PRICE = (0.50, 1.50)


class BudgetExceeded(Exception):
    """超出用户设定的花费上限。"""


class Cancelled(Exception):
    """用户取消。"""


@dataclass
class Progress:
    """一次翻译任务的进度快照（回调给界面）。"""

    done: int = 0
    total: int = 0
    accepted: int = 0
    rejected: int = 0
    shortened: int = 0
    untranslated: int = 0
    cost_usd: float = 0.0
    elapsed: float = 0.0
    current_batch: int = 0
    total_batches: int = 0
    message: str = ""

    @property
    def percent(self) -> int:
        return int(self.done * 100 / self.total) if self.total else 0


@dataclass
class TranslateResult:
    reports: List[GateReport] = field(default_factory=list)
    progress: Progress = field(default_factory=Progress)
    cache_hits: int = 0
    errors: List[str] = field(default_factory=list)

    @property
    def accepted_entries(self) -> List:
        out = []
        for r in self.reports:
            out.extend(r.accepted)
        return out


class Cache:
    """磁盘缓存：键 -> 译名。命中即 0 成本。"""

    def __init__(self, path: str = ""):
        self.path = path
        self._data: Dict[str, str] = {}
        self._dirty = False
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}

    @staticmethod
    def key(en: str, plugin: str, glossary_version: str, model: str) -> str:
        h = hashlib.sha256()
        for part in (en, plugin, glossary_version, model, PROMPT_VERSION):
            h.update(part.encode("utf-8"))
            h.update(b"\x00")
        return h.hexdigest()[:32]

    def get(self, key: str) -> Optional[str]:
        return self._data.get(key)

    def put(self, key: str, zh: str) -> None:
        self._data[key] = zh
        self._dirty = True

    def pop(self, key: str) -> None:
        """删掉一条（用于清理历史遗留的垃圾译文）。"""
        if key in self._data:
            del self._data[key]
            self._dirty = True

    def flush(self) -> None:
        if not self._dirty or not self.path:
            return
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False)
        os.replace(tmp, self.path)
        self._dirty = False

    def __len__(self) -> int:
        return len(self._data)

    def __bool__(self) -> bool:
        # 显式声明：缓存对象恒为真，避免空缓存被 or 短路掉
        return True


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pin, pout = PRICE_TABLE.get(model, DEFAULT_PRICE)
    return (prompt_tokens * pin + completion_tokens * pout) / 1_000_000


class Translator:
    """把一批英文名翻成中文，带缓存、预算、重试、闸门。"""

    def __init__(
        self,
        config: AIConfig,
        *,
        cache: Optional[Cache] = None,
        glossary: Optional[Dict[str, Dict[str, object]]] = None,
        world: Optional[str] = None,
        client: Optional[ChatClient] = None,
    ):
        self.config = config.resolved()
        self.client = client or ChatClient(self.config)
        # ★ 注意：不能写 cache or Cache() —— Cache 实现了 __len__，
        #   空缓存会被判为 falsy，于是每次都新建一个，缓存永远不生效。
        self.cache = cache if cache is not None else Cache()
        self.glossary = glossary or {}
        self.world = world
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def _glossary_version(self) -> str:
        h = hashlib.sha256()
        for k in sorted(self.glossary):
            h.update(k.encode("utf-8"))
            h.update(str(self.glossary[k].get("zh", "")).encode("utf-8"))
        return h.hexdigest()[:16]

    def translate(
        self,
        names: Iterable[str],
        *,
        plugin: str = "",
        effect: str = "",
        on_progress: Optional[Callable[[Progress], None]] = None,
    ) -> TranslateResult:
        names = [n for n in dict.fromkeys(names) if n]
        result = TranslateResult()
        prog = result.progress
        prog.total = len(names)
        t0 = time.time()

        gver = self._glossary_version()
        # ---- 先过缓存 ----
        #
        # ★★ 2026-10-23 严重 bug 修复（用户报「明明还有 646 条没翻译，
        #   点 AI 翻译却说没有要翻的」）：
        #
        #   原逻辑：命中缓存只把计数 +1，把译文【丢掉】，
        #   然后 if not pending: return result —— 返回的 result 里
        #   reports 是空的，accepted_entries 也就是空的，
        #   后端 done() 拿到 0 条草稿，界面显示「还没有草稿」。
        #
        #   用户真实处境：之前翻译过一批（已进 ai-cache.json），
        #   但那些译文从没写进词典（草稿被丢弃/没点接受），
        #   所以编辑器里仍显示「未翻译」；
        #   再点翻译 -> 全部命中缓存 -> 一条都不给 -> 像「没得翻了」。
        #
        #   修复：命中缓存的条目也要走完六道闸门并进 reports，
        #   草稿区就能照常拿到它们，点「全部接受」才真的写进词典。
        pending: List[str] = []
        cached_items: List[dict] = []
        for n in names:
            k = Cache.key(n, plugin, gver, self.config.model)
            hit = self.cache.get(k)
            if hit is not None:
                # ★ 2026-10-23：缓存里可能存着垃圾（实测 1064 条里 102 条是 "BC"，
                #   模型抽风时把 "BCC DeGrain OBS" 吐成了缩写残渣）。
                #   这种当【未命中】处理，重新翻译，不要拿垃圾去污染草稿。
                from .gates import check_too_short

                bad, _why = check_too_short(n, hit)
                if bad:
                    self.cache.pop(k)
                    pending.append(n)
                    continue
                result.cache_hits += 1
                cached_items.append({"en": n, "zh": hit})
            else:
                pending.append(n)

        if cached_items:
            rep_cached = run_gates(
                cached_items, world=self.world, glossary=self.glossary)
            result.reports.append(rep_cached)
            prog.done += len(cached_items)
            prog.accepted += len(rep_cached.accepted)
            prog.rejected += rep_cached.rejected
            prog.shortened += rep_cached.shortened
            prog.untranslated += rep_cached.untranslated

        if on_progress:
            on_progress(prog)
        if not pending:
            result.progress.elapsed = time.time() - t0
            result.progress.message = "全部命中缓存"
            if on_progress:
                on_progress(result.progress)
            return result

        batch_size = max(1, self.config.batch_size)
        batches = [pending[i:i + batch_size] for i in range(0, len(pending), batch_size)]
        prog.total_batches = len(batches)

        for bi, batch in enumerate(batches, 1):
            if self._cancel.is_set():
                raise Cancelled("用户取消")
            prog.current_batch = bi
            prog.message = "正在翻译第 %d/%d 批（%d 条）" % (bi, len(batches), len(batch))
            if on_progress:
                on_progress(prog)

            messages = build_messages(
                batch, plugin=plugin, effect=effect, glossary=self.glossary
            )

            # ★★ 2026-10-23 治本改造：条数校验 + 自动重试一次。
            #   实测模型偶尔会漏条目（返回 48/50）、把两条合并成一条、
            #   或者干脆乱缩成 "BC"。漏掉的条目以前【静默消失】，
            #   用户看到的就是「明明还有没翻译的、却扫不出来」。
            #   现在：条数对不上就重试一次；仍不对就把缺的记进 errors。
            report = None
            for attempt in range(2):
                try:
                    res = self._call_with_retry(messages)
                except AIError as exc:
                    result.errors.append("第 %d 批失败: %s" % (bi, exc))
                    prog.message = "第 %d 批失败: %s" % (bi, exc)
                    report = None
                    break

                cost = estimate_cost(
                    self.config.model, res.usage.prompt_tokens,
                    res.usage.completion_tokens)
                prog.cost_usd += cost
                if prog.cost_usd > self.config.budget_usd:
                    raise BudgetExceeded(
                        "已花费 $%.4f，超过预算上限 $%.2f" % (
                            prog.cost_usd, self.config.budget_usd)
                    )

                report = gates_from_text(
                    res.text, world=self.world, glossary=self.glossary)
                if report.total == len(batch):
                    break
                if attempt == 0:
                    prog.message = ("第 %d/%d 批只返回 %d/%d 条，重试一次"
                                    % (bi, len(batches), report.total, len(batch)))
                    if on_progress:
                        on_progress(prog)

            if report is None:
                continue
            if report.total != len(batch):
                result.errors.append(
                    "第 %d 批只返回 %d/%d 条（已重试）" % (
                        bi, report.total, len(batch)))
            result.reports.append(report)

            prog.accepted += len(report.accepted)
            prog.rejected += report.rejected
            prog.shortened += report.shortened
            prog.untranslated += report.untranslated
            prog.done += len(batch)

            # 通过闸门的写缓存
            for e in report.accepted:
                k = Cache.key(e.en, plugin, gver, self.config.model)
                self.cache.put(k, e.zh)

            if on_progress:
                on_progress(prog)

        self.cache.flush()
        result.progress.elapsed = time.time() - t0
        result.progress.message = "完成"
        if on_progress:
            on_progress(result.progress)
        return result

    def _call_with_retry(self, messages, *, attempts: int = 3):
        delay = 1.0
        last: Optional[AIError] = None
        for i in range(attempts):
            try:
                return self.client.chat(messages)
            except AIError as exc:
                last = exc
                if not exc.retryable or i == attempts - 1:
                    raise
                time.sleep(delay)
                delay *= 2
        raise last or AIError("未知错误")
