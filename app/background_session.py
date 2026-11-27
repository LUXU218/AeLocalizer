"""Application close and startup decisions that do not need a workspace window."""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol
from weakref import proxy

from app.engine_session import LanguageCommand, background_ready
from core import engine, settings


class AutomaticClose(Enum):
    WAIT = auto()
    KEEP_OPEN = auto()
    KEEP_BACKGROUND = auto()
    CLOSE = auto()


@dataclass(frozen=True)
class BackgroundState:
    shutting_down: bool
    engine_busy: bool
    language_disabled: bool
    language_configured: bool
    language_command: LanguageCommand | None = None
    engine_detected: bool = False
    stop_unconfirmed: bool = False


class BackgroundController(Protocol):
    def background_state(self) -> BackgroundState: ...
    def background_operations_busy(self, *, include_panel: bool = False) -> bool: ...
    def background_needs_effects_controller(self) -> bool: ...
    def background_engine_enabled(self) -> bool: ...
    def notify_background(self, level: str, message: str) -> None: ...
    def shutdown(self) -> None: ...


def should_open_workspace(*, automatic):
    if automatic:
        return not (engine.background_on_ae_startup() and engine.background_language_session())
    engine.request_foreground_window()
    return True


class BackgroundSession:
    def __init__(self, controller: BackgroundController, data_root: str):
        self._controller = proxy(controller)
        self._data_root = data_root
        self._handoff_prepared = False

    def workspace_busy(self):
        return self._controller.background_operations_busy(include_panel=True)

    def effects_can_stay(self):
        return self._can_stay(with_effects=True)

    def language_can_stay(self):
        return self._can_stay(with_effects=False)

    def _can_stay(self, *, with_effects):
        controller = self._controller
        state = controller.background_state()
        effects = controller.background_needs_effects_controller()
        if effects != with_effects:
            return False
        status = engine.control_status()
        return background_ready(
            status, confirmed=engine.profile_runtime_confirmed(status),
            engine_enabled=controller.background_engine_enabled(),
            effects_controller=effects, language_requested=not state.language_disabled,
            language_configured=state.language_configured, language_command=state.language_command,
        )

    def prepare_language_handoff(self):
        if self._handoff_prepared:
            return True
        controller = self._controller
        if controller.background_operations_busy():
            controller.notify_background("info", "正在处理当前操作，请稍候再转入后台。")
            return False
        if controller.background_needs_effects_controller():
            controller.notify_background("info", "效果名通道正在使用设置窗口。可先关闭效果名通道，或在引擎页停止汉化后退出。")
            return False
        state = controller.background_state()
        session = engine.active_language_session()
        if (not session or state.language_disabled or not controller.background_engine_enabled()
                or (state.language_command is not None and not state.language_command.matches_session(session))):
            controller.notify_background("warn", "还未确认当前工程的语言后台。请刷新引擎状态后重试。")
            return False
        config = settings.load(self._data_root)
        config.update(background_language_session=session, background_on_ae_startup=True)
        if not settings.save(self._data_root, config):
            controller.notify_background("warn", "无法保存后台会话，设置窗口已保留。")
            return False
        self._handoff_prepared = True
        controller.shutdown()
        return True

    def remember_background_on_startup(self):
        return settings.put(self._data_root, "background_on_ae_startup", True)

    def request_close(self):
        controller = self._controller
        state = controller.background_state()
        if self._handoff_prepared or state.shutting_down:
            return True
        if state.engine_busy:
            controller.notify_background("info", "汉化引擎正在切换，请稍候再关闭窗口。")
            return False
        if state.stop_unconfirmed:
            controller.notify_background("warn", "还未确认引擎已停止，请在引擎页查看状态。")
            return False
        if controller.background_engine_enabled():
            status = engine.control_status()
            confirmed = engine.profile_runtime_confirmed(status)
            if controller.background_needs_effects_controller() and (state.engine_detected or confirmed):
                controller.notify_background("info", "还未确认效果名可继续在后台运行，工作台已保留。")
                return False
            if (status.get("mode") == "on" and confirmed) or (
                state.engine_detected and state.language_configured and not state.language_disabled
            ):
                return self.prepare_language_handoff()
        controller.shutdown()
        return True

    def automatic_close(self, *, supports_effects_background):
        if not engine.background_on_ae_startup():
            return AutomaticClose.KEEP_OPEN
        effects = supports_effects_background and self.effects_can_stay()
        if not (self.language_can_stay() or effects) or self._controller.background_operations_busy():
            return AutomaticClose.WAIT
        if effects:
            return AutomaticClose.KEEP_BACKGROUND
        if self.prepare_language_handoff():
            return AutomaticClose.CLOSE
        return AutomaticClose.KEEP_OPEN
