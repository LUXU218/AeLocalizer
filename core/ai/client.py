"""OpenAI 兼容客户端：只用标准库，零依赖。

为什么不用 requests / openai SDK：
  · 打包体积（每 MB 都影响安装包）
  · 用户可能在内网/离线环境，依赖越少越好
  · 协议足够简单，urllib 完全够用

支持：任何 OpenAI 兼容端点（DeepSeek / OpenAI / 通义 / 智谱 / Kimi /
      硅基流动 / Ollama 本地 / 自建 vLLM）。只要它能吃 /chat/completions。
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .providers import AIConfig


class AIError(Exception):
    """调用失败（网络/鉴权/协议/限流）。"""

    def __init__(self, message: str, *, retryable: bool = False, status: int = 0):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens


@dataclass
class ChatResult:
    text: str
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    elapsed: float = 0.0


class ChatClient:
    """最小可用的 chat/completions 客户端。"""

    def __init__(self, config: AIConfig, *, timeout: float = 60.0):
        self.config = config.resolved()
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "AeLocalizer/0.1",
        }
        if self.config.api_key:
            h["Authorization"] = "Bearer " + self.config.api_key
        return h

    def _payload(self, messages: List[Dict[str, str]], *, max_tokens: int = 2048) -> bytes:
        body: Dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        return json.dumps(body, ensure_ascii=False).encode("utf-8")

    def chat(self, messages: List[Dict[str, str]], *, max_tokens: int = 2048) -> ChatResult:
        url = self.config.endpoint()
        data = self._payload(messages, max_tokens=max_tokens)
        req = urllib.request.Request(url, data=data, headers=self._headers(), method="POST")

        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            retryable = exc.code in (408, 429, 500, 502, 503, 504)
            raise AIError(
                "HTTP %d: %s" % (exc.code, detail or exc.reason),
                retryable=retryable, status=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise AIError("网络错误: %s" % exc.reason, retryable=True) from exc
        except socket.timeout as exc:
            raise AIError("请求超时（%.0fs）" % self.timeout, retryable=True) from exc
        except Exception as exc:
            raise AIError("请求失败: %s" % exc) from exc

        elapsed = time.time() - t0
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise AIError("响应不是合法 JSON: %s" % raw[:200]) from exc

        choices = obj.get("choices") or []
        if not choices:
            err = obj.get("error") or {}
            msg = err.get("message") if isinstance(err, dict) else None
            raise AIError("响应缺少 choices%s" % (": " + msg if msg else ""))

        text = ""
        first = choices[0]
        if isinstance(first.get("message"), dict):
            text = first["message"].get("content") or ""
        elif "text" in first:
            text = first["text"] or ""

        u = obj.get("usage") or {}
        usage = Usage(
            prompt_tokens=int(u.get("prompt_tokens", 0) or 0),
            completion_tokens=int(u.get("completion_tokens", 0) or 0),
            total_tokens=int(u.get("total_tokens", 0) or 0),
        )
        if not usage.total_tokens:
            usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
        return ChatResult(text=text, usage=usage, model=str(obj.get("model", "")), elapsed=elapsed)


def strip_code_fence(text: str) -> str:
    """去掉模型爱加的 ```json ... ``` 包裹。"""
    t = text.strip()
    if not t.startswith("```"):
        return t
    lines = t.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()
