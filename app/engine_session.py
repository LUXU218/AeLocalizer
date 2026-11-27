"""Desktop engine intent and the interpretation of existing runtime receipts."""

from dataclasses import dataclass
from enum import Enum, auto
from os.path import normcase


def host_identity(host):
    if not isinstance(host, dict):
        return None
    pid, created, image = (host.get(key) for key in ("pid", "ctime", "image"))
    if type(pid) is not int or type(created) is not int or not pid or not created or not image:
        return None
    if not isinstance(image, str):
        return None
    return pid, created, normcase(image)


@dataclass(frozen=True)
class LanguageCommand:
    host: tuple
    mode: str
    generation: int

    def acknowledged(self, status):
        return (
            status.get("ack_gen") == self.generation
            and status.get("mode") == self.mode
            and host_identity(status.get("host")) == self.host
        )

    def applied(self, status):
        return (
            self.acknowledged(status)
            and status.get("gen") == self.generation
            and status.get("applied_gen") == self.generation
            and status.get("language_ready") is True
        )

    def matches_session(self, session):
        return self.mode == "on" and self.generation == session["gen"] and self.host == (
            session["host_pid"], session["host_ctime"], session["host_image"],
        )


class EngineOperation(Enum):
    IDLE = auto()
    STARTING = auto()
    STOPPING = auto()


@dataclass
class EngineSessionState:
    effect_request: bool | None = None
    language_request: bool | None = None
    effect_generation: int = 0
    language_generation: int = 0
    language_host: tuple | None = None
    sent_language_command: LanguageCommand | None = None
    reopened_background: dict | None = None
    effect_receipt: tuple | None = None
    operation: EngineOperation = EngineOperation.IDLE
    stop_result: bool | None = None

    @property
    def busy(self):
        return self.operation is not EngineOperation.IDLE

    @property
    def stop_status(self):
        if self.operation is EngineOperation.STOPPING:
            return "正在停止引擎"
        if self.stop_result is False:
            return "未确认引擎已停止"
        if self.stop_result is True:
            return "引擎已停止"
        return ""

    def begin(self, operation):
        self.operation = operation
        self.stop_result = None

    def finish(self, *, stop_result=None):
        self.operation = EngineOperation.IDLE
        self.stop_result = stop_result

    def requested_language_command(self, enabled):
        if self.language_host is None or not self.language_generation:
            return None
        mode = "on" if enabled else "off"
        return LanguageCommand(self.language_host, mode, self.language_generation)

    def channels(self, effect_default, language_default):
        effect = effect_default if self.effect_request is None else self.effect_request
        language = language_default if self.language_request is None else self.language_request
        return [effect, language, language]

    def request_effect(self, enabled):
        self.effect_request = bool(enabled)
        self.effect_generation += 1
        self.effect_receipt = None
        return self.effect_generation

    def request_language(self, enabled, now):
        self.language_request = bool(enabled)
        self.language_generation = max(now, self.language_generation + 1)

    def resume_background(self, session):
        self.reopened_background = session
        if session:
            self.language_request = True
            self.language_generation = session["gen"]
            self.language_host = (session["host_pid"], session["host_ctime"], session["host_image"])
            self.sent_language_command = LanguageCommand(self.language_host, "on", self.language_generation)

    def language_command(self, host, language_enabled, now):
        if host != self.language_host or not self.language_generation:
            self.language_host = host
            self.language_generation = max(now, self.language_generation + 1)
            self.sent_language_command = None
        return self.requested_language_command(language_enabled)

    def effect_phase(self, enabled, *, running, confirmed, host):
        if not running:
            return "waiting-engine" if enabled else "idle"
        if confirmed and host is not None:
            if self.effect_receipt == (self.effect_generation, host, enabled):
                return "ready" if enabled else "paused"
        if not enabled and self.effect_request is None:
            return "idle"
        return "waiting-receipt"

    def language_phase(self, enabled, status, *, running, confirmed):
        if not running:
            return "waiting-engine" if enabled else "idle"
        command = self.requested_language_command(enabled)
        if not confirmed or command is None:
            return "waiting-receipt"
        if not command.acknowledged(status):
            return "waiting-receipt"
        if command.applied(status):
            return "applied"
        return "acknowledged"


def background_ready(status, *, confirmed, engine_enabled, effects_controller, language_requested,
                     language_configured, language_command):
    if not (confirmed and engine_enabled):
        return False
    if effects_controller:
        return True
    return (
        language_requested and language_configured and status.get("mode") == "on"
        and status.get("language_ready") is True
        and (language_command is None or language_command.applied(status))
    )
