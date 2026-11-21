"""AI 服务商配置：DeepSeek 为默认，用户可自由更换。

设计原则：
  · 统一走 OpenAI 兼容协议 POST {base_url}/chat/completions
  · 预置只是"填好默认值"，任何字段都能改
  · 支持完全自定义端点（自建 vLLM / LM Studio / 局域网）
  · Key 默认只在内存；勾选记住后用 Windows DPAPI 加密落盘
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

DEFAULT_PROVIDER = "deepseek"


@dataclass(frozen=True)
class ProviderPreset:
    key: str
    label: str
    base_url: str
    default_model: str
    models: List[str] = field(default_factory=list)
    needs_key: bool = True
    note: str = ""


PRESETS: Dict[str, ProviderPreset] = {
    p.key: p
    for p in [
        ProviderPreset(
            "deepseek", "DeepSeek（默认）", "https://api.deepseek.com/v1", "deepseek-chat",
            ["deepseek-chat", "deepseek-reasoner"],
            note="中文术语表现好、价格低，适合批量生成词典",
        ),
        ProviderPreset(
            "openai", "OpenAI", "https://api.openai.com/v1", "gpt-4o-mini",
            ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"],
        ),
        ProviderPreset(
            "claude", "Claude（OpenAI 兼容层）", "https://api.anthropic.com/v1",
            "claude-3-5-sonnet-latest", ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"],
        ),
        ProviderPreset(
            "gemini", "Gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
            "gemini-2.0-flash", ["gemini-2.0-flash", "gemini-1.5-pro"],
        ),
        ProviderPreset(
            "qwen", "通义千问", "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "qwen-plus", ["qwen-plus", "qwen-max", "qwen-turbo"],
        ),
        ProviderPreset(
            "zhipu", "智谱 GLM", "https://open.bigmodel.cn/api/paas/v4", "glm-4-flash",
            ["glm-4-flash", "glm-4-plus"],
        ),
        ProviderPreset(
            "kimi", "Kimi", "https://api.moonshot.cn/v1", "moonshot-v1-8k",
            ["moonshot-v1-8k", "moonshot-v1-32k"],
        ),
        ProviderPreset(
            "siliconflow", "硅基流动", "https://api.siliconflow.cn/v1",
            "Qwen/Qwen2.5-7B-Instruct", ["Qwen/Qwen2.5-7B-Instruct", "deepseek-ai/DeepSeek-V3"],
        ),
        ProviderPreset(
            "ollama", "Ollama（本地，零成本零外发）", "http://127.0.0.1:11434/v1",
            "qwen2.5:7b", ["qwen2.5:7b", "qwen2.5:14b", "llama3.1:8b"],
            needs_key=False, note="完全离线；速度取决于本机显卡",
        ),
        ProviderPreset(
            "custom", "自定义（OpenAI 兼容）", "", "", [], note="自建 vLLM / LM Studio / 公司内网",
        ),
    ]
}


@dataclass
class AIConfig:
    provider: str = DEFAULT_PROVIDER
    base_url: str = ""
    model: str = ""
    api_key: str = ""          # 仅内存；落盘时替换为 DPAPI 密文
    # ★ 2026-10-23：温度 0.2 -> 0.1。翻译是确定性任务，不需要创造力；
    #   实测较高温度时模型偶尔把 "BCC+Film Glow" 缩成 "BC" 这种抽搐输出。
    temperature: float = 0.1
    # ★ 批量 50 -> 25。一批 50 条时模型容易漏条目/串行/乱缩；
    #   25 条时输出稳定得多，代价只是多几次请求（重复部分有缓存兜底）。
    batch_size: int = 25
    concurrency: int = 3
    budget_usd: float = 0.50
    use_cache: bool = True
    remember_key: bool = False

    def resolved(self) -> "AIConfig":
        """用预设补全空字段；custom 时原样返回。"""
        preset = PRESETS.get(self.provider)
        if not preset or preset.key == "custom":
            return self
        return AIConfig(
            provider=self.provider,
            base_url=self.base_url or preset.base_url,
            model=self.model or preset.default_model,
            api_key=self.api_key,
            temperature=self.temperature,
            batch_size=self.batch_size,
            concurrency=self.concurrency,
            budget_usd=self.budget_usd,
            use_cache=self.use_cache,
            remember_key=self.remember_key,
        )

    def validate(self) -> List[str]:
        """返回问题列表；空列表表示可用。"""
        c = self.resolved()
        problems: List[str] = []
        if not c.base_url:
            problems.append("未填写 Base URL")
        elif not c.base_url.startswith(("http://", "https://")):
            problems.append("Base URL 必须以 http:// 或 https:// 开头")
        if not c.model:
            problems.append("未选择模型")
        preset = PRESETS.get(c.provider)
        if (preset is None or preset.needs_key) and not c.api_key:
            problems.append("未填写 API Key")
        if c.batch_size < 1 or c.batch_size > 200:
            problems.append("批量大小需在 1–200 之间")
        if c.concurrency < 1 or c.concurrency > 8:
            problems.append("并发需在 1–8 之间")
        return problems

    def endpoint(self) -> str:
        c = self.resolved()
        return c.base_url.rstrip("/") + "/chat/completions"


def mask_key(key: str) -> str:
    """界面展示用：只露后四位。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return "•" * (len(key) - 4) + key[-4:]
