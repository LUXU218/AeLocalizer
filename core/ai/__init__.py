"""AI 翻译子系统。

模块：
  providers  服务商预设 + 配置校验（默认 DeepSeek，可自由更换）
  client     OpenAI 兼容 HTTP 客户端（标准库，零依赖）
  prompt     Prompt 工程（版本化，影响缓存键）
  gates      6 道质量闸（AI 产出必须全过才进草稿）
  pipeline   分批 + 缓存 + 预算 + 重试 + 并发
"""
from .client import AIError, ChatClient, ChatResult, Usage
from .gates import (
    BAD_TRANSLATIONS, DraftEntry, GateReport, gates_from_text, parse_response, run_gates,
)
from .pipeline import (
    BudgetExceeded, Cache, Cancelled, Progress, TranslateResult, Translator, estimate_cost,
)
from .prompt import PROMPT_VERSION, STYLE_CONCISE, STYLE_FAITHFUL, build_messages
from .providers import AIConfig, DEFAULT_PROVIDER, PRESETS, ProviderPreset, mask_key

__all__ = [
    "AIConfig", "DEFAULT_PROVIDER", "PRESETS", "ProviderPreset", "mask_key",
    "AIError", "ChatClient", "ChatResult", "Usage",
    "BAD_TRANSLATIONS", "DraftEntry", "GateReport", "gates_from_text",
    "parse_response", "run_gates",
    "BudgetExceeded", "Cache", "Cancelled", "Progress", "TranslateResult",
    "Translator", "estimate_cost",
    "PROMPT_VERSION", "STYLE_CONCISE", "STYLE_FAITHFUL", "build_messages",
]
