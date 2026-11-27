"""界面后端：把 core 模块包装成 QML 可用的对象。

设计原则：
  · QML 里不写业务逻辑，只做绑定与事件
  · 所有耗时操作（扫描/翻译/应用）走线程池，绝不阻塞 UI
  · 状态一律用 Property + Signal 暴露，界面单向绑定
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from dataclasses import replace
from typing import Dict, List, Optional

from PySide6.QtCore import (
    Property, QObject, Qt, QThread, QTimer, Signal, Slot,
)
from PySide6.QtGui import QCursor, QGuiApplication

from app.background_session import BackgroundSession, BackgroundState
from app.dictmodel import DictListModel
from app.engine_session import EngineOperation, EngineSessionState, host_identity
from app.groupmodel import GroupListModel
from app.namemodel import NameListModel

from core.ai.client import ChatClient
from core.ai.pipeline import Cache, Translator
from core.ai.providers import AIConfig, PRESETS, mask_key
from core import attrib, engine
from core import guardruntime, paramledger
from core.dictpack import PackError
from core.dictstore import DictStore
from core.encoder import WORLD_ANSI, describe
from core.effects import load_effect_names, merge_into_store
from core.inventory import (
    CH_EFFECT, CH_ENUM, CH_PARAM,
    Inventory, load_selection, save_selection,
)
from core.pipl import find_ae_installs
from core.workflow import preflight
from app.backend_parts.worker import Worker
from app.backend_parts.panel_ui import PanelController
from app.backend_parts.settings_ui import SettingsUiMixin
from app.backend_parts.wallpaper_ui import WallpaperUiMixin

def _resolve_data_root() -> str:
    """用户数据目录（settings / 词典库 / 密钥 都在这）。

    2026-10-23：测试直接使用默认数据目录时覆盖了已保存的 DPAPI 密文。
    测试、截图和诊断使用 AELOC_DATA_ROOT 指向独立目录，或隔离本模块的 DATA_ROOT。
    """
    env = os.environ.get("AELOC_DATA_ROOT", "").strip()
    if env:
        return env
    # ★ 2026-11-02 二次加固(与 core.engine 同步):unittest 进程内强制沙盒。
    #   02:49 事故:裸跑 unittest discover 未设环境变量, 测试把真实
    #   engine_dict 覆盖成测试词;discover 又不导入 tests/__init__.py,
    #   包级护栏没执行。在 DATA_ROOT 解析这个总闸上兜死。
    if "unittest" in sys.modules:
        import tempfile

        d = os.path.join(tempfile.gettempdir(), "aeloc_test_sandbox_default")
        os.makedirs(d, exist_ok=True)
        return d
    return os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AeLocalizer"
    )


DATA_ROOT = _resolve_data_root()
_ADAPTER_LOCK = threading.Lock()

#: 导入时的真实数据根。测试会把模块级 DATA_ROOT 换成沙盒目录 ——
#: 常驻自动行为(效果名补写/启动预扫)只在 DATA_ROOT 仍是真实根时才允许启动,
#: 双保险防止测试进程对用户正开着的 AE 发起真实写入。
_DEFAULT_DATA_ROOT = DATA_ROOT

#: 判断一行是不是「纯中文」（批量写入时区分译文行 / 英文名行）
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


#: BCC/Sapphire 注册期会向枚举流加入内部轴和曲线标签。补词列表过滤这些内部值。
_ENUM_JUNK_RES = (
    re.compile(r"^L\d{1,4}\b"),
    re.compile(r"^[XYZ0-9]{1,4}\s+Axis$", re.IGNORECASE),
    re.compile(r"^[XYZ]{1,3}\s*Axis$", re.IGNORECASE),
    # 枚举流可能混入 0 - 100 % RGB、32nd、64th 等单位、范围和序号。
    # 补词列表过滤纯数字、序号和数值范围。
    re.compile(r"^\d{1,3}(?:st|nd|rd|th)$", re.IGNORECASE),
    re.compile(r"^\d{1,3}(?:\.\d+)?\s*-\s*\d{1,3}(?:\.\d+)?(?:\s*%)?\s*[A-Za-z]{0,8}$"),
    re.compile(r"^-?\d+(?:\.\d+)?(?:\s*%|\s*[A-Za-z]{1,6})?$"),
    re.compile(r"^\d+(?:-\d+){1,3}$"),
    re.compile(r"^-?\d+(?:\.\d+)?\s+(?:degrees?|px|pixels?|frames?|seconds?)$",
               re.IGNORECASE),
)


def _is_junk_enum(s: str) -> bool:
    return any(rx.search(s) for rx in _ENUM_JUNK_RES)


# BCC、Sapphire、Trapcode 的效果名会出现在参数流的返回结构里。
# 守护在 onLeave 读取 +0x40 时可能把 BCC Z-Blur 等效果误采为参数名。
# 按品牌前缀将这些词排到列表末尾，保留手动翻译入口。
_EFFECT_NAME_PREFIXES = (
    "BCC ", "BCC+", "Sapphire ", "S_", "Trapcode ", "Red Giant ",
    "Element 3D", "Optical Flares", "Plexus", "Particular", "Starglow",
    "Colorista", "Magic Bullet", "Universe ", "Video Copilot ",
    "Continuum ", "GenArts ", "Boris FX", "Rowbyte ",
)


def _looks_like_effect_name(s: str) -> bool:
    return s.startswith(_EFFECT_NAME_PREFIXES)


# 守护原样写回 PreBlur 等同形占位时也会记为改名成功。
# 补词将这些仍显示英文的项目重新列为缺词。缩写、格式和品牌照常保留原文。
_SAME_FORM_KEEP_RE = re.compile(r"^[A-Z0-9][A-Z0-9 +./%|_-]{0,9}$")
_KEEP_SAME = frozenset({
    "mocha", "Mocha", "Mocha AE", "Twixtor", "RSMB", "RSMB Pro",
    "Cosmo II", "Mojo II", "ProRes", "DNxHD", "YCbCr", "sRGB",
})


def _norm_name(s) -> str:
    return " ".join(str(s or "").split())


# ★ 2026-11-02: 引擎词典(engine_dict/*.json)合并在补词卡每次刷新时都会被调用，
#   实测 137ms/次（26k 条 extra_overrides + 25k 条 enum_fill）。主线程同步调用会
#   让「提取」按钮看着卡。这里按目录内文件的 (name, mtime_ns, size) 做缓存，
#   引擎热换词典后文件会变，缓存自动失效。
_MERGED_LAYERS_CACHE: dict = {}


def _collect_merged_layers(dirpath: str):
    from core.export import collect_layers as _collect
    key = []
    try:
        for _n in os.listdir(dirpath):
            _p = os.path.join(dirpath, _n)
            try:
                _st = os.stat(_p)
            except OSError:
                continue
            key.append((_n, _st.st_mtime_ns, _st.st_size))
    except OSError:
        key = None
    key = tuple(sorted(key)) if key is not None else None
    hit = _MERGED_LAYERS_CACHE.get(dirpath)
    if hit is not None and hit[0] == key:
        return hit[1]
    merged, _cnt = _collect(dirpath)
    _MERGED_LAYERS_CACHE[dirpath] = (key, merged)
    return merged


def _parse_batch_lines(text: str, known_ens=None):
    """解析批量写入的多行文本（各写入入口共用）。

    返回 (pairs, ordered_zh, name_list, unknown)：
      · pairs      [(en, zh)]      自带英文名的行，见下面三种写法
      · ordered_zh [str]           纯中文行（按出现顺序写进勾选的效果名）
      · name_list  [str]           纯英文行 —— 不当译文，按名单勾选对应行
      · unknown    int             解析失败的行数

    pairs 认三种写法（★ 从 QQ/微信中转时 TAB 常被换成空格，所以
    「英文名 中文」连在一行的也要认 —— 用已知英文名做前缀匹配）：
      ① 英文<TAB>中文
      ② 英文 = 中文 / 英文：中文 / 英文: 中文
      ③ 英文名 中文（前缀必须是已知英文名，避免把纯中文行拆错）
    """
    known = sorted({e for e in (known_ens or []) if e}, key=len, reverse=True)
    known_set = set(known)
    pairs: List[tuple] = []
    ordered_zh: List[str] = []
    name_list: List[str] = []
    unknown = 0
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if "\t" in line:
            en, zh = line.split("\t", 1)
            if en.strip():
                pairs.append((en.strip(), zh.strip()))
            else:
                unknown += 1
            continue
        if not _CJK_RE.search(line):
            # 没有中文、没有 TAB -> 英文名单行
            name_list.append(line)
            continue

        # 有中文的行：先试显式分隔符，再试「已知英文名 + 空格 + 中文」
        placed = False
        for sep in (" = ", "＝", "：", ": "):
            i = line.find(sep)
            if i <= 0:
                continue
            en = line[:i].strip()
            zh = line[i + len(sep):].strip()
            if (en and zh and not _CJK_RE.search(en) and _CJK_RE.search(zh)
                    and (not known_set or en in known_set)):
                pairs.append((en, zh))
                placed = True
                break
        if not placed and known:
            for en in known:
                if line.startswith(en) and len(line) > len(en):
                    zh = line[len(en):].strip(" \t：:＝=·-— ")
                    if zh and _CJK_RE.search(zh):
                        pairs.append((en, zh))
                        placed = True
                        break
        if not placed:
            ordered_zh.append(line)
    return pairs, ordered_zh, name_list, unknown


def _pick_saved_version(installs) -> tuple:
    """挑启动时该用哪个 AE 版本。

    ★★ 2026-10-22：用户切到 2025 后重启软件又变回 2026 —— 因为原来
      无条件取 installs[0]（排最前的最高版本）。现在优先用上次存的版本，
      只有它已卸载/从没存过才回退到第一个。
    """
    if not installs:
        return "", ""
    saved = ""
    try:
        from core import settings as _settings

        saved = str(_settings.get(DATA_ROOT, "ae_version", "") or "")
    except Exception:
        saved = ""
    if saved:
        for v, r in installs:
            if v == saved:
                return v, r
        # 存的版本已卸载 -> 回退，并把记录清掉避免反复尝试
        try:
            from core import settings as _settings

            _settings.put(DATA_ROOT, "ae_version", "")
        except Exception:
            pass
    return installs[0][0], installs[0][1]

#: 归属索引缓存（每个 AE 版本一份）
def _attrib_cache(ae_version: str) -> str:
    return attrib.cache_path(DATA_ROOT, ae_version)


class AppBackend(WallpaperUiMixin, SettingsUiMixin, QObject):
    """QML 的唯一入口对象。"""

    #: 后台 Python 线程不能直接创建/连接 QThread；统一排队回 owner 线程。
    _runDispatch = Signal(object)
    # ---- 通知 ----
    toast = Signal(str, str)          # (级别 info/warn/error, 文本)
    aiProgressChanged = Signal()
    aiDraftChanged = Signal()
    #: 请求 QML 弹出效果名表文件选择框
    effectsPickRequested = Signal()
    inventoryChanged = Signal()
    versionsChanged = Signal()
    stateChanged = Signal()
    engineSessionChanged = Signal()
    #: 效果名编辑器的勾选变化（批量操作按钮的可用状态）
    nameSelectionChanged = Signal()
    #: 词典内容变了（编辑/新增/导入/接受草稿）→ 词典面板重新载入
    dictChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # 始终取第一个安装项会在重启时将已选的 AE 2025 切回 2026。
        # 启动时读取上次所选版本。首次运行或该版本已卸载时才回退到首项。
        self._installs = find_ae_installs()
        self._version, self._root = _pick_saved_version(self._installs)
        self._panelLocalization = PanelController(self, os.path.join(DATA_ROOT, "panels"))
        self._inv: Optional[Inventory] = None
        self._store = DictStore.load(os.path.join(DATA_ROOT, "store.json"))
        self._paramBaseline = paramledger.ParamBaseline(DATA_ROOT)
        self._busy = False
        self._busyText = ""
        self._engineAutoTried = False
        self._engineAutoTriedAt = 0.0
        self._engineRestoreQuiet = False
        self._engineStopQuiet = False
        self._paramCatchupBusy = False
        self._paramCatchupAt = 0.0
        self._paramRestoreBusy = False
        self._paramIntentGen = 0
        #: AE 启动任务是否在跑（后台线程，防止重复点「启动 AE」）
        self._aeLaunching = False
        #: AE 重启任务是否在跑（切换汉化后自动重启，防止并发关两个 AE）
        self._aeRestarting = False
        self._workers: List[Worker] = []
        # ★ 背景 Python 线程发起的 _run 统一排队回 owner Qt 线程创建 Worker。
        self._runDispatch.connect(self._run_on_main, Qt.QueuedConnection)

        # ★ 插件列表模型（2026-10-21）
        #   原来 QML 用 model: backend.groups（QVariantList 值类型），
        #   每次勾选都重新算出新数组 -> ListView 重置滚动条弹回顶部。
        #   换成 QAbstractListModel 后只发 dataChanged，滚动位置保持。
        #   ★ 必须用【顶层 import】，写在函数里 PyInstaller 静态分析抓不到，
        #     打包后 exe 会 ModuleNotFoundError。
        self._groupModel = GroupListModel(self)
        # 效果名编辑器支持直接修改效果名和修订 AI 译名。
        self._nameModel = NameListModel(self)
        self._nameRows: list = []
        # 效果名页共用的词典浏览与编辑模型。
        self._dictModel = DictListModel(self)
        self._dictVersion = 0
        self._effectsPath = ""
        self._status = "就绪"

        # ---- 运行时引擎状态 ----
        self._engine = engine.EngineStatus()
        self._engineSession = EngineSessionState()
        self.stateChanged.connect(self.engineSessionChanged)
        self._attrib: Dict[str, List[str]] = {}
        self._attribLoaded = False
        self._keepUnowned = True
        self._engineDictInfo = ""
        self._engineDictReason = ""
        self._langPayloadSig = None
        # 运行 host 探测缓存：QML 绑定可能高频求值，避免反复枚举进程。
        self._hostAeCache = None
        self._hostAeCacheAt = 0.0

        # ---- AI 翻译状态 ----
        # 和密钥一同保存服务商、Base URL 与模型。
        # 重启后错用默认服务商会将请求发到错误端点并收到 401。
        self._aiProvider = "deepseek"
        self._aiBaseUrl = ""
        self._aiModel = ""
        # 预算上限 / 每批条数(2026-10-28 起可调,持久化在 settings.json)
        self._aiBatch = 25
        self._aiBudget = 0.5
        self._loadAiSettings()

        # ---- 设置页状态（2026-11-01：主题 / 更新 / 背景图）----
        # ★ 主题默认 light = 历史外观；绝不启动时自动联网检查更新。
        self._uiTheme = "light"          # light | dark | system
        from core.version import DEFAULT_UPDATE_REPOSITORY

        self._updateRepository = DEFAULT_UPDATE_REPOSITORY
        self._updateState = ""
        self._updateText = ""
        self._updateLatest = ""
        self._updateDownloadUrl = ""
        self._updateSha256 = ""
        self._updateFile = ""
        self._updateProgress = -1
        self._bgImage = ""               # 背景图本地绝对路径；空 = 无
        self._bgDim = 0.22               # 图上底色纱罩浓度 0~1（玻璃档）
        # ---- 个性化（2026-11-16 设置页改版）----
        # 默认值 == 历史观感：老用户升级后看不出变化，只有主动去设置页调才变。
        self._accentKey = "blue"         # blue|violet|teal|amber|rose|slate
        self._density = "normal"         # compact|normal|relaxed
        self._fontScale = "normal"       # small|normal|large
        self._reduceMotion = False
        self._plate = 0.60               # 玻璃面板不透明度 0~1（玻璃档）
        self._autoScan = True            # 启动时自动扫描插件清单（常规分区可关）
        # 字体与文字颜色（2026-11-16）：空串 = 内置默认 / 跟随主题
        self._uiFont = ''
        self._textColor = ''
        self._fontCache = None           # 系统字体列表缓存（首次进设置页才枚举）
        # ---- 壁纸引擎接入（2026-11-16）----
        # ★ 必须在 _loadUiSettings 之前初始化：它要恢复 bg_video 路径。
        self._we_init()
        self._loadUiSettings()
        # ★ 2026-11-17：设置读完后把「场景实时渲染」接回来（端口随机，必须重启服务）
        self._restoreBgScene()
        # ★ API Key：启动时从 DPAPI 密文里解出来（磁盘上从无明文）
        self._aiKey = ""
        self._aiKeySaved = False
        try:
            from core import secrets as _secrets

            _k = _secrets.load_key(_secrets.default_path(DATA_ROOT))
            if _k:
                self._aiKey = _k
                self._aiKeySaved = True
        except Exception:
            pass
        # ★ 2026-10-23：50 -> 25。一批 50 条时模型容易漏条目/串行/乱缩成 "BC"。
        #   注意这里才是真正生效的值 —— AIConfig 的同名字段只是兜底默认，
        #   _ai_config() 会用 self._aiBatch 覆盖它。
        self._aiProgress = 0
        self._aiProgressText = ""
        self._aiCost = 0.0
        self._aiDraft: List[dict] = []
        self._aiDraftSummary = ""
        #: 草稿是否已过期（勾选变了但没重新翻译）
        self._aiDraftStale = False
        self._translator: Optional[Translator] = None
        self._aiCache = Cache(os.path.join(DATA_ROOT, "ai-cache.json"))
        # ★ 2026-11-01 解耦:core 不再反向 import app.backend,数据根由这里注入
        try:
            from core import paramnative as _pn

            _pn.set_data_root(DATA_ROOT)
        except Exception:
            pass

        # 启动时同步一次引擎状态（引擎/修复层/词典是否就绪）
        try:
            self._engine = engine.check(self._root, self._version)
            if self._engine.engine_dict_entries:
                self._engineDictInfo = "引擎词典已就绪：%d 条（按上次勾选生成）" % (
                    self._engine.engine_dict_entries)
        except Exception:
            pass

        # 事件循环启动后，检查正在运行且尚未适配的 AE 构建。
        # ★ 2026-10-30 加固:沙盒模式(AELOC_DATA_ROOT 指向测试目录)一律不做 ——
        #   tests/test_backend.py 会构造 AppBackend 并跑事件循环,旧代码会趁机
        #   对【用户真在用的 AE】发起 frida 探测/适配,污染用户的 adapter_profiles。
        if not os.environ.get("AELOC_DATA_ROOT"):
            QTimer.singleShot(2500, lambda: self._auto_adapt_async(
                self._running_ae_version()))

        # ★ 效果名通道常驻换名（2026-11-01 起，2026-11-06 改成「只改显示名缓存」）：
        #   开关状态持久（存选区文件），而内存里的效果标题是一次性的 —— AE
        #   新建一个实例、重建一个效果控件，标题都是英文原名。参数名/下拉值有
        #   守护常驻补写，效果名在这里补上对等的一环：**每一拍**把当前内存里
        #   的实例标题换成中文。不用会话记账：注册表不写了之后，新实例出生
        #   就是英文，一次性记账会让之后新加的每个效果永远停在英文名上。
        #   ★ 沙盒（环境变量 / DATA_ROOT 被测试换掉）绝不启动，见 TestLiveWatch。
        self._liveAutoNoisy: set = set()     # 已为该会话报过一次错的错
        self._liveAutoInflight = False       # 自动换名在飞(防止 tick 重叠)
        self._liveRevertBusy = False         # 关通道自愈扫在飞
        self._liveRevertAt = 0.0             # 上一次关通道自愈时刻(2 秒节流)
        self._liveSeen: dict = {}            # AE 会话首见时刻(稳定窗用)
        # ★ 2026-11-09 开关竞态修复：效果名通道的唯一意图/代号 + 串行锁。
        #   旧实现每个 400ms 看护拍都在 OFF 分支新建一个 _live_hook_stop 线程，
        #   旧线程可能在新一轮 ON 之后才拿到 GIL/锁，把 PAUSED 又置回 true 并
        #   写回英文；这就是「第一遍正常，反复开关两下后第二遍不汉化」的根因。
        #   _effGen 每次点击 +1；所有 pause/resume/revert/apply 前后都必须用它
        #   校验，晚到的旧操作一律放弃。_liveOpLock 把 frida 同步 RPC 串行化，
        #   杜绝 pause 与 resume 并发。
        try:
            from core import settings as _settings
            _lv = _settings.get(DATA_ROOT, "language_intent", None)
            if _lv is not None:
                self._langWant = bool(_lv)
        except Exception:
            pass
        self._liveIntentLock = threading.Lock()
        self._liveOpLock = threading.RLock()
        self._liveHookStopBusy = False       # 同一时刻只允许一个 OFF 收尾线程
        self._liveHookStopped = False        # 已完整停用（避免 OFF 期间每拍重跑）
        self._liveHookResumedGen = None      # 已按哪个意图代号 resume 成功
        self._liveHookCatchupGen = None      # resume 后已排过哪一代补写
        self._liveFirstRefreshGen = None     # 首次 ON 已排过哪一代 Win32 无鼠标刷新
        self._liveHookSession = None         # (pid, creation_time, image) 当前 hook 所属 AE
        self._liveSessionEpoch = 0           # AE 会话变化即 +1，旧线程结果作废
        # ★ 2026-11-09：这些状态原来只在首次使用时动态创建，_liveWatchTick
        #   的「挂钩已接管」判断直接读 self._liveHook，导致测试/首拍
        #   AttributeError。全部在 __init__ 显式初始化。
        self._liveHook = None                # 唯一 DisplayHook 实例
        self._liveShutdown = False           # 普通 App 关闭后禁止再建 effect hook
        self._liveRealNamesCache = None      # (pid, ts, frozenset(注册表真实显示名))
        self._liveRealNamesSig = None        # 已推给当前 JS 的 REAL 指纹
        self._liveHookAt = 0.0               # 1 秒节流
        self._liveHookTickBusy = False       # 看护拍挂钩线程在飞
        self._liveHookStatsAt = 0.0          # 统计 5 秒节流
        self._liveNudgeAt = 0.0              # 轻推 0.6 秒节流
        self._liveHostProbe = None
        self._liveHostProbeAt = 0.0
        self._liveHostSnapshot = None
        self.background_session = BackgroundSession(self, DATA_ROOT)
        self._engineSession.resume_background(engine.background_language_session())
        self._liveAutoEnabled = self._live_auto_allowed()
        if self._liveAutoEnabled:
            # 启动静默预扫：三通道开关与自动补写都依赖插件清单（_inv），
            # 不能等用户想起「扫描」才可用；1.5 秒后让过启动高峰。
            # ★ 2026-11-16：设置页「常规 → 启动时自动扫描」可以关掉它。
            #   关掉后三通道开关要等用户手动扫描才有初值 —— 这是用户的选择，
            #   不是缺陷；设置项旁边已写明。
            if self._autoScan:
                QTimer.singleShot(1500, self._auto_scan_async)
            self._liveWatch = QTimer(self)
            # ★ 2026-11-06「新加效果立刻中文化」：2 秒 → 0.4 秒。
            #   每拍的活是「回读已记住的实例 + 虚表 needle 扫私有堆」两件事，
            #   前者几十个对象、后者是 C 层 bytes.find（35MB 十几毫秒），所以
            #   0.4 秒一拍也不会把 CPU 打满；名字正则只在宽档分片里跑。
            # ★ 2026-11-07「添加效果的那一瞬间就是中文」：0.4 秒一拍太慢 ——
            #   效果刚建出来时面板会立刻画一次（那时还是英文），而我们的
            #   walk 发现是毫秒级、写入也就几次 Read/Write，所以把节拍压到
            #   60ms，尽量赶在面板第一次绘制之前把名字换成中文。
            self._liveWatch.setInterval(60)
            # ★ 定时器频率只能由主线程改；后台 hook 线程只写这个意图值，
            #   _liveWatchTick 在 Qt 主线程应用，避免 cross-thread setInterval
            #   直接让 watchdog 停摆（真机 tick 只有首行即此因）。
            self._liveWatchWantInterval = 60
            self._liveWatch.timeout.connect(self._poll_live_watch)
            self._liveWatch.start()

    # ---------------------------------------------------------- 属性

    @Property("QStringList", notify=versionsChanged)
    def versions(self):
        return [v for v, _ in self._installs]

    @Property(str, notify=stateChanged)
    def currentVersion(self):
        return self._version

    def _pipl_world(self) -> str:
        """写 PiPL / 参数名用的编码世界。

        ★★ 2026-10-22 统一：恒为 GBK(ANSI)。

          原来这里有 4 处写成
              world = WORLD_UTF8 if version >= 2026 else WORLD_ANSI
          但实机采样（core/encoder.py 顶部的记录）证明：
              .aex PiPL 槽位 / 运行期参数名缓冲 / Continuum .rdata 全是 GBK，
              「AE 2026 改用 UTF-8」在中文场景下不成立。

          用 UTF-8 算长度闸的后果是每汉字按 3 字节估，而实际写进去只要 2 字节，
          于是译名被过度缩短（「BCC 模糊溶解」被砍成「BCC 模糊」）。

          写 PiPL 时 aexwrite.plan_file 还会再兜底拉回 ansi，但长度闸在这之前
          就算完了，所以必须在这里就统一。
        """
        return WORLD_ANSI

    @Property(str, notify=stateChanged)
    def worldLabel(self):
        """写入编码标签。

        ★★ 2026-10-22 修正：原来按版本返回 "UTF-8"(≥2026) / "GBK"，
           会让用户以为 2026 要用 UTF-8 写。实机采样结论（core/encoder.py）：
             .aex PiPL 槽位   → GBK
             运行期参数名缓冲 → GBK（采到 \xd7\xd4\xb6\xaf = "自动" 的 GBK 字节）
             Continuum .rdata → GBK
           中文链路【一律 GBK，与版本无关】，两版的差别只在 UI 层怎么解码显示。
        """
        if not self._version:
            return "未知"
        return "GBK"

    @Property(bool, notify=stateChanged)
    def busy(self):
        return self._busy

    @Property(str, notify=stateChanged)
    def busyText(self):
        return self._busyText

    @Property(str, notify=stateChanged)
    def statusText(self):
        return self._status

    @Property(int, notify=inventoryChanged)
    def selectedCount(self):
        return len(self._inv.selected()) if self._inv else 0

    @Property(int, notify=inventoryChanged)
    def selectedFiles(self):
        return sum(g.file_count for g in self._inv.selected()) if self._inv else 0

    @Property(int, notify=stateChanged)
    def dictCount(self):
        return len(self._store.resolve())

    @Property(QObject, constant=True)
    def groupModel(self):
        """插件列表模型（QML ListView 用，支持增量更新和搜索）。"""
        return self._groupModel

    # ---------------------------------------------------- 效果名编辑器

    @Property(QObject, constant=True)
    def nameModel(self):
        """效果名编辑器模型：每行 = 一个插件的效果名 + 当前译文。"""
        return self._nameModel

    @Slot(result=str)
    def refreshNames(self):
        """重建编辑器数据（扫描后 / AI 翻译后都要调）。"""
        try:
            from core import nameeditor

            if not self._inv:
                return "请先扫描插件"
            self._nameRows = nameeditor.collect_rows(self._inv, self._store)
            self._nameModel.setRows(self._nameRows)
            # 刷新后勾选可能被裁掉（行不存在了）-> 让界面上的"已选 N 条"重新取值
            self.nameSelectionChanged.emit()
            st = nameeditor.stats(self._nameRows)
            msg = "已载入 %d 个效果名（已译 %d / 未译 %d / 超长 %d）" % (
                st["total"], st.get("ok", 0) + st.get("same", 0),
                st.get("empty", 0), st.get("long", 0))
            return msg
        except Exception as exc:
            return "载入失败：%s" % str(exc)[:80]

    @Slot(str, bool, str)
    def filterNames(self, keyword: str, onlyZh: bool, vendor: str):
        """过滤编辑器列表（关键词 + 只看已译 + 按插件厂商）。

        vendor 为空时展示全部厂商。按厂商筛选后，可一次勾选当前列表的效果名。
        """
        self._nameModel.setFilter(keyword or "", onlyZh, vendor or "")
        return self._nameModel.rowCount()

    # ---------------- 效果名：按插件名称批量选择（2026-10-23） ----------------

    @Slot(result="QVariantList")
    def nameVendors(self):
        """插件（厂商）下拉框的数据源：[{value, count}, …]。"""
        try:
            return self._nameModel.vendors()
        except Exception:
            return []

    @Property(int, notify=nameSelectionChanged)
    def nameSelectedCount(self):
        return self._nameModel.selectedCount

    @Slot(str, bool)
    def toggleNameSelected(self, en: str, shift: bool):
        """勾选一行。shift=True 时从上次点击的行连选到这一行。"""
        if self._nameModel.toggleSelectedByEn(en or "", bool(shift)):
            self.nameSelectionChanged.emit()

    @Slot(str, bool, result=int)
    def selectNameSuite(self, suite: str, on: bool):
        """按套装表头「全选本组」：选中当前筛选结果里该套装的整块。"""
        n = self._nameModel.selectSuite(suite or "", bool(on))
        self.nameSelectionChanged.emit()
        return n

    @Slot(result="QVariantMap")
    def nameSuiteCounts(self):
        """每个套装当前可见多少条（给分区表头显示）。"""
        try:
            return self._nameModel.suiteCounts()
        except Exception:
            return {}

    @Slot(bool, result=int)
    def selectAllNames(self, on: bool):
        """全选 / 取消全选【当前筛选结果】，返回变化的条数。"""
        n = self._nameModel.setAllVisible(bool(on))
        self.nameSelectionChanged.emit()
        return n

    @Slot()
    def clearNameSelection(self):
        self._nameModel.clearSelection()
        self.nameSelectionChanged.emit()

    @Slot(result=str)
    def batchClearNames(self):
        """把勾选的行恢复成词典原值（删掉用户/锁定层里的词条）。"""
        names = self._nameModel.selectedEn()
        if not names:
            self.toast.emit("warn", "请先勾选要处理的效果名")
            return "未勾选"
        n = 0
        try:
            for en in names:
                try:
                    if self._store.remove_user(en):
                        n += 1
                except Exception:
                    pass
            self._store.save(os.path.join(DATA_ROOT, "store.json"))
            self._hot_reload_notify()
        except Exception as exc:
            msg = "批量清空失败：%s" % str(exc)[:80]
            self.toast.emit("error", msg)
            return msg
        # 内存行同步
        for r in self._nameRows:
            if r.en in names:
                r.zh = ""
                r.locked = False
        self._nameModel.clearSelection()
        self.nameSelectionChanged.emit()
        self._bump_dict()
        msg = "已清空 %d 条译文（恢复词典原值）" % n
        self.toast.emit("info", msg)
        return msg

    @Slot()
    def retranslateSelected(self):
        """把勾选的效果名交给 AI 重译 —— 结果进「翻译」页的草稿区待确认。"""
        if self._busy:
            # ★ 并发防护（2026-10-28）：测试套件共用 backend 且不等待
            #   Worker,所以只给有真实花费的 AI 入口加闸;测试不调这三个槽
            self.toast.emit("warn", "有任务正在进行，请等它完成")
            return
        names = self._nameModel.selectedEn()
        if not names:
            self.toast.emit("warn", "请先勾选要重译的效果名")
            return
        cfg = self._ai_config()
        problems = cfg.validate()
        if problems:
            self.toast.emit("warn", "配置不完整：" + problems[0])
            return

        tr = Translator(cfg, cache=self._aiCache,
                        glossary=self._terms_for_ai())
        self._translator = tr
        self._aiProgress = 0
        self._aiCost = 0.0
        self._aiProgressText = "准备重译 %d 条…" % len(names)
        self.aiProgressChanged.emit()
        n_total = len(names)

        def work():
            def on_prog(p):
                self._aiProgress = p.percent
                self._aiCost = p.cost_usd
                self._aiProgressText = "%d/%d · %s" % (p.done, p.total, p.message[:40])
                self.aiProgressChanged.emit()

            return tr.translate(names, on_progress=on_prog)

        def done(res):
            if res is None:
                self.toast.emit("warn", "没有需要重译的条目")
                return
            draft = [{
                "en": e.en,
                "zh": e.used_zh or e.zh,
                "status": e.status,
                "reason": e.reason or "",
            } for e in res.accepted_entries]
            rejected = []
            for rep in res.reports:
                for e in rep.entries:
                    if not e.accepted:
                        rejected.append({
                            "en": e.en, "zh": "", "status": e.status,
                            "reason": e.reason or "",
                        })
            seen = {}
            for d in draft + rejected:
                if d["en"] not in seen or (not seen[d["en"]]["zh"] and d["zh"]):
                    seen[d["en"]] = d
            self._aiDraft = list(seen.values())
            self._aiDraftStale = False
            self._aiDraftSummary = (
                "（重译所选）通过 %d · 拒绝 %d · 缓存命中 %d · 花费 $%.4f"
                % (len(draft), len(rejected), res.cache_hits, res.progress.cost_usd))
            self._aiProgress = 100
            self._aiProgressText = "重译完成"
            self.aiDraftChanged.emit()
            self.aiProgressChanged.emit()
            if res.errors:
                self.toast.emit("error", "重译部分失败：" + res.errors[0][:140])
            self.toast.emit("info", "已生成 %d 条草稿 —— 去「翻译」页草稿区确认"
                            % len(self._aiDraft))
            self.nameSelectionChanged.emit()

        self._run(work, done, _busy_text="正在重译 %d 条…" % n_total)

    @Slot(str, str, result=str)
    def saveName(self, en: str, zh: str):
        """保存一条效果名译文。

        ★ 写进 user_locked 层（最高优先级），这样：
          ① 覆盖词典包里那条烂翻译；
          ② 以后跑 AI 翻译也不会把它改回去。
        """
        en = (en or "").strip()
        zh = (zh or "").strip()
        if not en:
            return "缺少英文原名"
        try:
            if zh:
                self._store.upsert_user(en, zh, locked=True)
            else:
                # 空 = 撤销这条用户词条，回到词典包原值
                self._store.remove_user(en)
            self._store.save(os.path.join(DATA_ROOT, "store.json"))
            self._hot_reload_notify()
            # 刷新模型里所有同 en 的行
            self._nameModel.updateByEn(en, zh)
            self._bump_dict()
            return "已保存：%s → %s" % (en, zh or "（恢复词典原值）")
        except Exception as exc:
            return "保存失败：%s" % str(exc)[:80]

    @Slot(result=str)
    def nameStats(self):
        """编辑器状态统计（给 UI 显示）。"""
        try:
            from core import nameeditor

            st = nameeditor.stats(self._nameRows)
            return "%d 个效果名 · 已译 %d · 未译 %d · 超长 %d" % (
                st["total"], st.get("ok", 0) + st.get("same", 0),
                st.get("empty", 0), st.get("long", 0))
        except Exception:
            return ""

    @Slot(str)
    def setGroupFilter(self, text: str):
        """搜索框过滤（在模型里做，避免 QML 重建数组导致滚动跳顶）。"""
        self._groupModel.setFilter(text)

    # ---------------------------------------------------- 词典（可编辑）

    def _bump_dict(self):
        """词典内容变了 → 通知词典面板（其它地方改动 store 时也要调）。"""
        self._dictVersion += 1
        self.dictChanged.emit()

    @Property(QObject, constant=True)
    def dictModel(self):
        """词典全量表模型（QML ListView 用，支持关键词过滤和单行更新）。"""
        return self._dictModel

    #: 效果名的厂商前缀。词典包里有些效果名对应的插件当前没装 / 没勾选，
    #: 不会出现在 self._nameRows 里，靠前缀兜住。
    _EFFECT_PREFIX = re.compile(
        r"^(BCC |BCC\+|S_[A-Z0-9]|Trapcode |Magic Bullet |Universe |Particular|"
        r"Optical Flares|Element 3D|Plexus|Starglow|Colorista|Continuum |"
        r"GenArts |Boris FX |Rowbyte |Red Giant )")

    def _effect_name_keys(self) -> set:
        """收集插件清单中的效果名，供词典页排除。

        效果名和参数名共用 DictStore，resolve() 会同时返回两类条目。
        效果名由专用编辑器管理，词典页聚焦参数和下拉项。
        """
        try:
            return {(r.en or "").strip() for r in self._nameRows if r.en}
        except Exception:
            return set()

    def _is_effect_name(self, key: str, known: set) -> bool:
        """这个词典键是不是效果名（而不是参数名 / 下拉值）。"""
        k = (key or "").strip()
        if not k:
            return False
        if k in known:
            return True
        # 下拉值常带 "A|B|C"，前缀规则对它们没有意义
        if "|" in k:
            return False
        return bool(self._EFFECT_PREFIX.match(k))


    @Slot(result=str)
    def refreshDict(self):
        """从词典仓库重建全量表（切到词典子页 / 导入词典包后调）。

        ★ 2026-11-01 性能：按 store.revision 缓存 —— 2 万条逐条 describe 拼
          QVariantList 是词典子页每次打开的全量成本，内容没变就不再重拼
          （dictstore._invalidate 时 revision 自增）。
        """
        try:
            rev = self._store.revision
            if getattr(self, "_dictRowsRev", None) != rev:
                world = self._pipl_world()
                names = self._effect_name_keys()
                rows = (
                    {"en": k, "zh": r.zh, "layer": r.layer,
                     "gauge": describe(k, r.zh, world)}
                    for k, r in self._store.resolve().items()
                    if not self._is_effect_name(k, names)
                )
                self._dictTotal = self._dictModel.setRows(rows)
                self._dictRowsRev = rev
            return "共 %d 条参数/下拉译名（效果名在「效果名」子页改）" % int(
                getattr(self, "_dictTotal", 0))
        except Exception as exc:
            return "载入词典失败：%s" % str(exc)[:80]

    def release_workspace_dictionary(self) -> None:
        """Discard rebuildable dictionary data after the workspace closes."""
        self._dictModel.clear()
        self._dictRowsRev = None
        self._store.clear_resolution_cache()

    @Slot(str)
    def filterDict(self, keyword: str):
        """词典搜索框过滤（英文或中文都匹配）。"""
        self._dictModel.setFilter(keyword or "")

    @Slot(str, str, result=str)
    def saveDictEntry(self, en: str, zh: str):
        """编辑一条词典词条。

        ★ 和效果名编辑器的 saveName 同一套语义：
          · zh 非空 → 写进 user_locked 层（最高优先级，AI/词典包都覆盖不了）
          · zh 为空 → 删掉用户词条，恢复词典包/内置层的原值
                     （如果这条本来就是用户自己加的，删掉后整条消失）
        """
        en = (en or "").strip()
        zh = (zh or "").strip()
        if not en:
            return "缺少英文原名"
        try:
            if zh:
                self._store.upsert_user(en, zh, locked=True)
            else:
                self._store.remove_user(en)
            self._store.save(os.path.join(DATA_ROOT, "store.json"))
            self._hot_reload_notify()
            r = self._store.resolve().get(en)
            if r is None:
                self._dictModel.removeEntry(en)
            else:
                self._dictModel.updateEntry(
                    en, r.zh, r.layer, describe(en, r.zh, self._pipl_world()))
            self._bump_dict()
            return "已保存：%s → %s" % (en, zh or "（恢复词典原值）")
        except Exception as exc:
            return "保存失败：%s" % str(exc)[:80]

    @Slot(str, str, result=str)
    def addDictEntry(self, en: str, zh: str):
        """新增一条词条（英文原名必须没打过空，译文也不能为空）。"""
        en = (en or "").strip()
        zh = (zh or "").strip()
        if not en:
            return "请填写英文原名"
        if not zh:
            return "请填写中文译名"
        return self.saveDictEntry(en, zh)

    # ---------------- 效果名批量复制 ----------------

    @Slot(result=str)
    def copySelectedNames(self):
        """把勾选的效果名英文原名复制到剪贴板（一行一个）。"""
        ens = self._nameModel.selectedEnOrdered()
        if not ens:
            self.toast.emit("warn", "请先勾选要复制的效果名")
            return ""
        text = "\n".join(ens)
        QGuiApplication.clipboard().setText(text)
        self.toast.emit("info", "已复制 %d 条英文名到剪贴板" % len(ens))
        return text

    @Slot(result=str)
    def copySelectedNamePairs(self):
        """把勾选的效果名复制成「英文<TAB>中文」两列（可整块贴进表格）。"""
        ens = self._nameModel.selectedEnOrdered()
        if not ens:
            self.toast.emit("warn", "请先勾选要复制的效果名")
            return ""
        try:
            zhmap = self._nameModel.zhMap()
        except Exception:
            zhmap = {}
        lines = ["%s\t%s" % (en, zhmap.get(en, "")) for en in ens]
        text = "\n".join(lines)
        QGuiApplication.clipboard().setText(text)
        self.toast.emit("info", "已复制 %d 条（英文+译文）到剪贴板" % len(ens))
        return text

    @Slot(result=str)
    def copySelectedTranslations(self):
        """把勾选行的中文译文复制到剪贴板（一行一个，顺序 = 列表显示顺序）。

        中文译名列支持跨行框选，Ctrl+C 按显示顺序输出纯文本。
        """
        ens = self._nameModel.selectedEnOrdered()
        if not ens:
            self.toast.emit("warn", "请先勾选要复制的行")
            return ""
        try:
            zhmap = self._nameModel.zhMap()
        except Exception:
            zhmap = {}
        text = "\n".join(zhmap.get(en, "") for en in ens)
        QGuiApplication.clipboard().setText(text)
        self.toast.emit("info", "已复制 %d 条中文译文到剪贴板" % len(ens))
        return text

    @Slot(result=bool)
    def leftButtonDown(self):
        """左键当前是否按着。

        ★ 框选拖动的兜底：锚点行如果被列表回收销毁，QML 收不到 release，
          定时器靠这个判断"用户其实已经松手了"，不会拖个没完。
        """
        return bool(QGuiApplication.mouseButtons() & Qt.LeftButton)

    @Slot(bool)
    def setWindowResizing(self, active):
        """Defer UI-thread process polling while handling resize input."""
        self._windowResizing = active

    @Slot(result="QVariantList")
    def cursorPos(self):
        """全局光标位置 [x, y]。

        ★ 框选自动滚动不依赖按下的那一行回报坐标（行可能被回收销毁），
          直接问系统光标在哪，列表 mapFromGlobal 换算即可。
        """
        p = QCursor.pos()
        return [p.x(), p.y()]

    def _write_batch_targets(self, targets: List[tuple]) -> str:
        """把 (en, zh) 对写进词典并同步效果名编辑器/词典两处界面。

        zh 为空 = 删用户词条恢复词典原值（和 saveName 一致）。
        返回 "" 或错误信息。
        """
        written: Dict[str, str] = {}
        try:
            for en, zh in targets:
                if zh:
                    self._store.upsert_user(en, zh, locked=True)
                else:
                    self._store.remove_user(en)
                written[en] = zh
            if written:
                self._store.save(os.path.join(DATA_ROOT, "store.json"))
                self._hot_reload_notify()
        except Exception as exc:
            return "批量写入失败：%s" % str(exc)[:80]

        res = self._store.resolve()
        world = self._pipl_world()
        for r in self._nameRows:
            if r.en in written:
                r.zh = written[r.en]
                r.locked = bool(written[r.en])
        for en, zh in written.items():
            self._nameModel.updateByEn(en, zh)
            rr = res.get(en)
            if rr is None:
                self._dictModel.removeEntry(en)
            else:
                self._dictModel.updateEntry(
                    en, rr.zh, rr.layer, describe(en, rr.zh, world))
        self._bump_dict()
        return ""

    @Slot(result="QVariantList")
    def selectedNameEnList(self):
        """当前勾选的英文名（弹层左栏内容，按列表显示顺序）。"""
        try:
            return self._nameModel.selectedEnOrdered()
        except Exception:
            return []

    @Slot(int, int, bool)
    def sweepNameRange(self, i0: int, i1: int, on: bool):
        """列表拖选：把 [i0, i1] 的行统一勾上/取消（QML 按住左键拖动用）。"""
        try:
            if self._nameModel.setRangeByIndex(int(i0), int(i1), bool(on)):
                self.nameSelectionChanged.emit()
        except Exception:
            pass

    @Slot(int, int)
    def sweepNameReplace(self, anchor: int, cur: int):
        """框选拖动专用：勾选恰好 = [anchor..cur]，替换式（2026-10-27）。

        配合 QML 侧 indexAt 的 contentY 修复 —— 往下拖过头再拖回来，
        多框的行要跟着退掉，选中的和屏幕上框到的才一致。
        """
        try:
            if self._nameModel.setSweepRange(int(anchor), int(cur)):
                self.nameSelectionChanged.emit()
        except Exception:
            pass

    @Slot(str, str, result=str)
    def writeNamesFromPopup(self, en_text: str, zh_text: str):
        """按英文名与中文译名两栏批量写入。

        左栏 = 勾选的英文名（定位锚），右栏每行对应左栏一行；
        右栏行若自带英文名（TAB / 「en = zh」/ 「en：zh」，或 Tab 丢失后
        「英文名 中文」连在一行）则按行内英文名写，行数可与左栏不同。
        """
        left = [l.strip() for l in (en_text or "").splitlines() if l.strip()]
        pairs, ordered_zh, name_list, unknown = _parse_batch_lines(
            zh_text, known_ens=self._nameModel.allEn())
        unknown += len(name_list)      # 右栏里的纯英文行没有意义
        targets: List[tuple] = list(pairs)
        skipped = 0
        for i, zh in enumerate(ordered_zh):
            if i < len(left):
                targets.append((left[i], zh))
            else:
                skipped += 1

        if not targets:
            msg = ("右栏没有可写入的内容 —— 把中文一行一个粘进来"
                   "（行序对应左栏；也认「英文<Tab>中文」格式）")
            self.toast.emit("warn", msg)
            return msg

        err = self._write_batch_targets(targets)
        if err:
            self.toast.emit("error", err)
            return err

        msg = "已写入 %d 条" % len(targets)
        if skipped:
            msg += "（右栏多 %d 行没对应上左栏）" % skipped
        if unknown:
            msg += " · 未识别 %d 行" % unknown
        self.toast.emit("info", msg)
        return msg

    @Slot(str, result=str)
    def batchWriteNames(self, text: str):
        """单框批量写入（保留：复制中英整块粘回 / 纯英文名单转勾选）。

        支持三种行格式（可混用）：
          · 英文<TAB>中文 —— 按英文名写入（不需要勾选）；
          · 纯中文行 —— 按列表从上到下的顺序，依次写进当前勾选的效果名；
          · 纯英文行 —— 不当译文（会把英文写进中文），转成【按名单勾选】。
        ★ 执行顺序：名单先勾选，纯中文行再按【勾选后的顺序】映射。
        """
        pairs, ordered_zh, name_list, unknown = _parse_batch_lines(
            text, known_ens=self._nameModel.allEn())

        # ① 名单勾选（先勾，纯中文行才能按新勾选顺序映射）
        names_matched = 0
        sel_msg = ""
        if name_list:
            matched, missing = self._nameModel.selectByEnList(name_list)
            self.nameSelectionChanged.emit()
            names_matched = matched
            sel_msg = " · 按名单勾选 %d 行" % matched
            if missing:
                sel_msg += "（名单 %d 条没找到）" % len(missing)

        # ② 纯中文行 -> 勾选顺序
        targets: List[tuple] = list(pairs)
        skipped_zh = 0
        if ordered_zh:
            sel = self._nameModel.selectedEnOrdered()
            if not sel:
                self.toast.emit(
                    "warn", "粘的是纯中文行 —— 请先勾选效果名"
                            "（或粘「英文<Tab>中文」对，不用勾选）")
                return "请先勾选要写入的效果名"
            for en, zh in zip(sel, ordered_zh):
                targets.append((en, zh))
            skipped_zh = max(0, len(ordered_zh) - len(sel))

        if not targets:
            if not name_list:
                msg = ("没有可写入的内容（未识别 %d 行）—— 支持"
                       "「英文<Tab>中文」对 / 纯中文行（先勾选）/"
                       "纯英文名单（转为勾选）" % unknown)
                self.toast.emit("warn", msg)
                return msg
            if names_matched:
                # 只有名单：这次操作的结果就是勾选本身
                body = sel_msg[3:]          # 去掉前导 " · "
                self.toast.emit("info", body)
                return body
            msg = ("名单里没有能在效果名编辑器中匹配的行"
                   "（共 %d 条，要写译文请粘「英文<Tab>中文」对）" % len(name_list))
            self.toast.emit("warn", msg)
            return msg

        # ③ 写入
        err = self._write_batch_targets(targets)
        if err:
            self.toast.emit("error", err)
            return err

        msg = "已写入 %d 条" % len(targets)
        if skipped_zh:
            msg += "（勾选不够，还有 %d 行没写）" % skipped_zh
        if sel_msg:
            msg += sel_msg
        if unknown:
            msg += " · 未识别 %d 行" % unknown
        self.toast.emit("info", msg)
        return msg

    @Slot(str, str, result="QVariantMap")
    def previewBatchWrite(self, en_text: str, zh_text: str):
        """两栏批量写入弹层的实时预览（点写入前告诉用户会发生什么）。"""
        left = [l.strip() for l in (en_text or "").splitlines() if l.strip()]
        pairs, ordered_zh, name_list, unknown = _parse_batch_lines(
            zh_text, known_ens=self._nameModel.allEn())
        unknown += len(name_list)
        try:
            alive = set(self._nameModel.allEn())
        except Exception:
            alive = set()
        return {
            "left": len(left),
            "pairs": len(pairs),
            "pairsUnknown": sum(1 for en, _ in pairs if en not in alive),
            "zhMapped": min(len(ordered_zh), len(left)),
            "zhSkipped": max(0, len(ordered_zh) - len(left)),
            "unknown": unknown,
        }

    def _invalidateAiDraft(self) -> None:
        """勾选范围变了 -> 旧草稿作废（但不清空，让用户还能决定接受/丢弃）。

        只打一个 stale 标记，界面上提示「这是上次翻译的结果，请重新翻译」。
        """
        if self._aiDraft and not self._aiDraftStale:
            self._aiDraftStale = True
            self.aiDraftChanged.emit()

    def _syncGroupModel(self, reset: bool = False) -> None:
        """把当前库存同步给列表模型。

        reset=True  行结构变了（重新扫描）-> 整体重置
        reset=False 只是字段变了（勾选/切通道）-> 只发 dataChanged，保持滚动
        """
        m = self._groupModel
        if not self._inv:
            m.setGroups([])
            return
        rows = []
        for g in self._inv.groups:
            rows.append({
                "id": g.group_id,
                "label": g.label,
                "vendor": g.vendor,
                "files": g.file_count,
                "effects": g.effects,
                "selected": g.selected,
                "builtin": g.builtin_adobe,
                "chEffect": CH_EFFECT in g.channels,
                "chParam": CH_PARAM in g.channels,
                "chEnum": CH_ENUM in g.channels,
            })
        # ★ 注意比较的是「全量行数」而不是 rowCount()
        #   rowCount() 是过滤后的行数，用它会误判成需要整体重置 -> 滚动跳顶
        if reset or len(rows) != len(m._all):
            m.setGroups(rows)
        else:
            m.refreshFrom(rows)

    # ---------------------------------------------------------- 引擎属性

    # Existing worker paths use these names. The session owns the values.
    @property
    def _engineBusy(self):
        return self._engineSession.busy

    @_engineBusy.setter
    def _engineBusy(self, busy):
        if busy:
            self._engineSession.begin(EngineOperation.STARTING)
        else:
            self._engineSession.finish()

    @property
    def _effWant(self):
        return self._engineSession.effect_request

    @_effWant.setter
    def _effWant(self, value):
        self._engineSession.effect_request = value

    @property
    def _effGen(self):
        return self._engineSession.effect_generation

    @_effGen.setter
    def _effGen(self, value):
        self._engineSession.effect_generation = value

    @property
    def _langWant(self):
        return self._engineSession.language_request

    @_langWant.setter
    def _langWant(self, value):
        self._engineSession.language_request = value

    @property
    def _langGen(self):
        return self._engineSession.language_generation

    @property
    def _reopenedBackground(self):
        return self._engineSession.reopened_background

    @_reopenedBackground.setter
    def _reopenedBackground(self, value):
        self._engineSession.reopened_background = value

    @Property("QVariantMap", notify=engineSessionChanged)
    def engineSessionView(self):
        status = engine.control_status()
        confirmed = engine.profile_runtime_confirmed(status)
        with self._liveIntentLock:
            session = replace(self._engineSession)
            effect, language, _ = self.channelState()
        running = self._engine.running or confirmed
        if session.operation is EngineOperation.STARTING:
            summary = "正在启动引擎"
        elif session.stop_status:
            summary = session.stop_status
        elif not running:
            summary = "引擎未运行"
        elif not confirmed:
            summary = "已检测引擎，当前配置未确认"
        else:
            summary = "当前配置引擎已就绪"

        language_phase = session.language_phase(language, status, running=running, confirmed=confirmed)
        if language_phase == "applied":
            language_text = self._language_status_text(status, receipt_only=True)
            if not language and (status.get("popup_restore_unknown") or status.get("popup_error")):
                language_phase = "restore-unconfirmed"
        elif language_phase == "acknowledged":
            language_text = self._language_status_text(dict(status, language_ready=False))
        elif language_phase == "waiting-receipt":
            action = "开启" if language else "停用"
            language_text = "参数名与下拉值：已请求%s，等待确认" % action
        elif language_phase == "waiting-engine":
            language_text = "参数名与下拉值：设置为开，等待引擎"
        else:
            language_text = ""

        effect_phase = session.effect_phase(effect, running=running, confirmed=confirmed,
                                            host=host_identity(status.get("host")))
        effect_text = {
            "waiting-engine": "效果名通道：设置为开，等待引擎",
            "waiting-receipt": "效果名通道：已请求%s，等待确认" % ("开启" if effect else "停用"),
            "ready": "效果名通道已就绪",
            "paused": "效果名通道已确认暂停",
        }.get(effect_phase, "")
        if session.operation is EngineOperation.STOPPING or session.stop_result is not None:
            effect_text = language_text = ""
            effect_phase = language_phase = "idle"
        return {
            "running": running,
            "confirmed": confirmed,
            "busy": session.busy,
            "summary": summary,
            "effectRequested": effect,
            "languageRequested": language,
            "effectPhase": effect_phase,
            "effectStatus": effect_text,
            "languagePhase": language_phase,
            "languageStatus": language_text,
        }

    @Slot()
    def refreshEngineSessionDisplay(self):
        """Ask the visible workbench to reread the existing receipt."""
        self.engineSessionChanged.emit()

    @Property(bool, notify=stateChanged)
    def engineRunning(self):
        return self._engine.running

    @Property(bool, notify=stateChanged)
    def engineProfileConfirmed(self):
        return self._engine.profile_confirmed

    @Property(str, notify=stateChanged)
    def engineProfileStatus(self):
        if not self._engine.running:
            return "引擎未运行"
        if not self._engine.profile_confirmed:
            return "已检测引擎，当前配置未确认"
        return "当前配置引擎已就绪"

    @Property(str, constant=True)
    def engineProfileDirectory(self):
        return os.path.join(DATA_ROOT, "engine_dict")

    @Property(bool, notify=stateChanged)
    def guardAvailable(self):
        return self._engine.guard_available

    @Property(bool, notify=stateChanged)
    def fixerInstalled(self):
        return self._engine.fixer_installed

    @Property(bool, notify=stateChanged)
    def fixerNeeded(self):
        return self._engine.fixer_needed

    @Property(int, notify=stateChanged)
    def engineDictEntries(self):
        return self._engine.engine_dict_entries

    @Property(str, notify=stateChanged)
    def engineDictInfo(self):
        return self._engineDictInfo

    @Property(str, notify=stateChanged)
    def engineDictDiag(self):
        """Empty-dictionary真实原因；与摘要分开，避免把“空原因”当成成功。"""
        return self._engineDictReason

    @Property(bool, notify=stateChanged)
    def keepUnowned(self):
        return self._keepUnowned

    @Slot(bool)
    def setKeepUnowned(self, v: bool):
        self._keepUnowned = bool(v)
        self.stateChanged.emit()

    @Property(str, notify=stateChanged)
    def attribInfo(self):
        if not self._attribLoaded:
            return "尚未建立归属索引（参数名通道按插件过滤需要它）"
        st = attrib.index_stats(self._attrib)
        return "归属索引 %d 个名字（独占 %d / 共享 %d）" % (
            st["names"], st["exclusive"], st["shared"])

    # ---------------------------------------------------------- 内部

    def _aeRoot(self) -> str:
        """当前 AE 安装根目录（给模块哈希校验用）。"""
        if self._root:
            return self._root
        return self._installs[0][1] if self._installs else ""

    def _run(self, fn, on_done, *args, **kwargs):
        """跑一个后台任务，完成后回调。

        ★ 2026-11-09：Worker(QThread) 的创建、信号连接、busy 状态和完成回调
          必须在 AppBackend 所属 Qt 线程统一执行。后台 Python 线程没有 Qt
          事件循环；旧实现从那里直接 new Worker + connect Python 闭包，QThread
          会结束但 done/failed 永远送不到回调。这里非 owner 调用只投递
          _runDispatch，由主线程 _run_on_main 创建 Worker；任务本身仍在线程跑。
        """
        job = (fn, on_done, args, kwargs)
        try:
            if QThread.currentThread() != self.thread():
                self._runDispatch.emit(job)
                return
        except Exception:
            pass
        self._run_on_main(job)

    @Slot(object)
    def _run_on_main(self, job):
        """只在 AppBackend 的 Qt 线程执行：创建 Worker、接线、启动。"""
        fn, on_done, args, kwargs = job
        # ★ 失败回调/失败是否弹错(_on_fail/_toast_fail,2026-11-01):
        #   必须在创建 Worker 前弹出,否则会透传给业务函数
        toast_fail = kwargs.pop("_toast_fail", True)
        on_fail_cb = kwargs.pop("_on_fail", None)
        # ★ 静默任务(_silent,2026-11-06):常驻换名 0.4 秒一拍,绝不能每拍都把
        #   全局 _busy 点亮 —— 那会让整块界面每秒闪两次「处理中…」、按钮反复
        #   变灰。静默任务照常跑线程、照常回调,只是不参与忙碌态。
        silent = kwargs.pop("_silent", False)
        busy_text = kwargs.pop("_busy_text", "处理中…")
        if not silent:
            self._busy = True
            self._busyText = busy_text
            self.stateChanged.emit()
        w = Worker(fn, *args, **kwargs)
        self._workers.append(w)

        def finish(res):
            if not silent:
                self._busy = False
                self._busyText = ""
                self.stateChanged.emit()
            on_done(res)

        def fail(msg):
            if not silent:
                self._busy = False
                self._busyText = ""
                self.stateChanged.emit()
            if on_fail_cb is not None:
                on_fail_cb(msg)
            if toast_fail:
                self.toast.emit("error", msg.splitlines()[0][:200])

        w.done.connect(finish)
        w.failed.connect(fail)
        w.finished.connect(lambda: self._workers.remove(w) if w in self._workers else None)
        w.start()

    @Slot(str)
    def setVersion(self, ver: str):
        for v, r in self._installs:
            if v == ver:
                self._version, self._root = v, r
                # ★★ 2026-10-22：把选择存盘，否则下次启动又回默认版本。
                try:
                    from core import settings as _settings

                    _settings.put(DATA_ROOT, "ae_version", v)
                except Exception:
                    pass
                self.stateChanged.emit()
                return

    @Slot()
    def scan(self):
        if not self._root:
            self.toast.emit("error", "未检测到 After Effects")
            return
        from core.inventory import build_inventory

        self._status = "正在扫描…"
        self.stateChanged.emit()

        def done(inv):
            self._inv = inv
            self._restore_plugin_selection(inv)
            self._normalize_language_channels()
            self._apply_lang_intent()
            self._status = "扫描完成:%d 组 / %d 文件" % (len(inv.groups), inv.scanned_files)
            self._syncGroupModel(reset=True)
            self.inventoryChanged.emit()
            self.stateChanged.emit()
            self.toast.emit("info", self._status)
            # ★ 自动适配触发点(2026-10-28):扫描完看看运行中的 AE 要不要适配
            self._auto_adapt_async(self._running_ae_version())
            QTimer.singleShot(800, self._auto_restore_engine_async)

        self._run(build_inventory, done, self._root, self._version,
                  _busy_text="正在扫描插件…")

    @Slot(str)
    def toggleGroup(self, group_id: str):
        if not self._inv:
            return
        for g in self._inv.groups:
            if g.group_id == group_id:
                g.selected = not g.selected
                break
        # ★ 只更新这一行（dataChanged），不发全局 inventoryChanged，
        #   否则 QML 的 model 重算 -> 列表滚动条弹回顶部
        self._invalidateAiDraft()
        self._syncGroupModel(reset=False)
        self.stateChanged.emit()

    def _saveSelectionNow(self) -> None:
        """把当前勾选 + 通道状态立刻落盘。

        只在应用时保存会在直接退出时丢失通道状态。
        开关和勾选变更后立即保存，重启时可恢复选择。
        """
        try:
            if self._inv:
                save_selection(
                    os.path.join(DATA_ROOT, "selection-%s.json" % self._version),
                    self._inv)
        except Exception:
            pass

    def _restore_plugin_selection(self, inv) -> None:
        selection = os.path.join(DATA_ROOT, "selection-%s.json" % self._version)
        if os.path.exists(selection):
            load_selection(selection, inv)
        elif self._store.resolve():
            # Match the scope of the explicit third-party selection button.
            for group in inv.groups:
                group.selected = not group.builtin_adobe
            if inv.selected():
                self._saveSelectionNow()

    @Slot(bool, bool, bool, result=int)
    def setAllChannels(self, effect: bool, param: bool, enum: bool) -> int:
        """三个通道独立开关，一次性套用到所有组（工作台左栏用）。"""
        if not self._inv:
            return 0
        try:
            from core.inventory import set_all_channels

            n = set_all_channels(self._inv, effect, param, enum)
            self._invalidateAiDraft()
            self._syncGroupModel(reset=False)
            self._saveSelectionNow()
            self.stateChanged.emit()
            return n
        except Exception:
            return 0

    # ---------------------------------------------------------- 开关意图/代号
    #
    # ★ 2026-11-09：效果名通道的「最后意图」必须是唯一真相。用户快速点
    #   OFF→ON→OFF 时，之前启动的 pause/revert/apply 线程可能晚几百毫秒
    #   甚至几秒才跑到写入点；只靠 bool 标志会让旧线程把新一轮 ON 覆盖掉。
    #   这里用单调递增的 _effGen：操作开始时记下代号，每次真正调用 frida
    #   RPC / 写内存前再比一次；代号变了就立刻放弃，绝不写回旧状态。
    def _live_intent_set(self, on: bool) -> int:
        """记录用户对效果名通道的最新明确意图，返回新的意图代号。"""
        with self._liveIntentLock:
            gen = self._engineSession.request_effect(on)
        if on:
            self._liveHookStopped = False
            self._liveHookResumedGen = None
        return gen

    def _live_intent(self):
        """(当前意图, 代号)。从没点过时回退到组状态，保持旧行为。"""
        with self._liveIntentLock:
            want, gen = self._effWant, self._effGen
        if want is None:
            try:
                _cs = self.channelState()
                want = bool(_cs[0]) if _cs else True
            except Exception:
                want = True
        return bool(want), gen

    def _remember_effect_receipt(self, generation, host, enabled):
        if not host:
            return
        with self._liveIntentLock:
            if self._effGen == generation and not self._liveShutdown:
                self._engineSession.effect_receipt = (
                    generation, (host[0], host[1], os.path.normcase(host[2])), enabled)

    def _live_intent_ok(self, gen: int, on: bool) -> bool:
        """代号和意图都还没变 → 这个操作仍然有效。

        从没点过（_effWant is None）时按 channelState 的当前默认状态判断；
        默认选区恢复的 ON（gen0）也能被随后 OFF 的 gen1 取消。
        """
        with self._liveIntentLock:
            if getattr(self, "_liveShutdown", False):
                return False
            if self._effGen != gen:
                return False
            if self._effWant is None:
                try:
                    _cs = self.channelState()
                    _want = bool(_cs[0]) if _cs else True
                except Exception:
                    _want = True
                return _want == bool(on)
            return bool(self._effWant) == bool(on)

    def _apply_lang_intent(self) -> None:
        """清单就绪后应用点击期间记录的合并语言意图。"""
        want = getattr(self, "_langWant", None)
        if want is None or not self._inv:
            return
        st = self.channelState()
        self.setAllChannels(st[0], bool(want), bool(want))

    def _normalize_language_channels(self) -> int:
        """历史 selection 里 param/enum 不一致时，迁移为两者配对。

        以参数通道为主：param=ON 则 enum=ON，param=OFF 则 enum=OFF；
        effect 通道完全不动。只在加载/扫描后做一次，避免每次回显互相覆盖。
        """
        if not self._inv:
            return 0
        from core.inventory import CH_ENUM, CH_PARAM

        changed = 0
        for g in self._inv.groups:
            want = CH_PARAM in g.channels
            has = CH_ENUM in g.channels
            if want != has:
                base = [c for c in g.channels if c not in (CH_PARAM, CH_ENUM)]
                if want:
                    base.extend((CH_PARAM, CH_ENUM))
                g.channels = tuple(
                    c for c in ("effect", "param", "enum") if c in set(base))
                changed += 1
        if changed:
            self._saveSelectionNow()
            self._syncGroupModel(reset=False)
        return changed

    @Slot(str, bool)
    def setChannelAuto(self, kind: str, on: bool) -> None:
        """引擎卡的三通道开关(2026-10-29 从翻译页通道条移来):切换即全自动。

        · 参数名 / 下拉值已合并为“语言”通道：旧 param/enum 调用都路由到同一
          on/off；引擎在跑时热换词典，不重启 guard，param 关时停写并恢复 baseline。
        · 效果名:开 = 恢复显示名挂钩 + 既有实例就地换名;关 = 暂停挂钩并
          实时还原英文。所有收尾工作都带意图代号，旧的 OFF/ON 线程不会
          覆盖最后一次点击（2026-11-09 修复反复开关失效）。
        """
        from core.inventory import CH_EFFECT, CH_PARAM, CH_ENUM

        # 用户范围已合并：param/enum 视为同一“语言”通道；旧调用兼容路由。
        if kind in ("param", "enum", "lang"):
            kind = "lang"
        ch = {"effect": CH_EFFECT, "lang": CH_PARAM}.get(kind)
        if not ch:
            return
        _gen = None
        if kind == "effect":
            # 先记意图（后面即使清单没就绪也不能把这次点击丢掉）。
            _gen = self._live_intent_set(on)
        elif kind == "lang":
            with self._liveIntentLock:
                # 跨 GUI 重启也可排序：本机 monotonic_ns，而不是从小整数重来。
                self._engineSession.request_language(on, time.monotonic_ns())
            try:
                from core import settings as _settings
                _settings.put(DATA_ROOT, "language_intent", bool(on))
            except Exception:
                pass
        self.engineSessionChanged.emit()
        if self._inv is None:
            # 清单还没就绪（启动预扫没跑完）。effect/lang 都已记意图，
            # 扫描完成后会按最后意图应用；这里只说一声。
            if kind in ("effect", "lang"):
                self.toast.emit("info", "插件清单还没就绪，几秒后会自动生效")
            return
        st = self.channelState()   # [effect, param, enum](全开语义)
        # ★ 2026-11-08：不能「相等就早退」。channelState 是「所有组都开」，
        #   部分组关着时它是 False；用户点「关」会把 False==False 判成
        #   「已是目标状态」直接返回 —— 界面关不掉、还留着几个组在汉化。
        #   永远做一次全量对齐（幂等），开关与组状态才不会各说各话。
        if kind == "lang":
            self.setAllChannels(st[0], bool(on), bool(on))
        else:
            self.setAllChannels(bool(on), st[1], st[2])

        if kind == "lang":
            self._paramIntentGen = int(getattr(self, "_paramIntentGen", 0)) + 1
            from core import engine as _eng
            need = any(CH_PARAM in g.channels or CH_ENUM in g.channels
                       for g in self._inv.selected())
            if not _eng.engine_running():
                # 引擎不在时只有 ON 才需要拉起；OFF 不启动。
                if need and on:
                    self._engineRestoreQuiet = True
                    try:
                        self.startEngine()
                    finally:
                        if not self._engineBusy:
                            self._engineRestoreQuiet = False
                return
            # 合并语言通道即使全部 OFF 也保持 guard 运行，热换为空语言
            # 词典；不能 stopEngine，否则就不是实时切换。
            # managed runtime 是语言唯一写者；旧 WPM display catchup/restore
            # 不再从 GUI 触发，避免并行第二写者。
            if self._reload_engine_channels(allow_empty=True):
                pass
        elif kind == "effect":
            if on and not engine.engine_running():
                # effect 现在由 managed runtime 的单 Frida host 承载；没有
                # guard 时先拉起 host（参数词典可为空），不在 GUI 直接 attach。
                self._engineRestoreQuiet = True
                try:
                    self.startEngine()
                finally:
                    if not self._engineBusy:
                        self._engineRestoreQuiet = False
            if on:
                # 挂钩恢复由 400ms 看护拍完成；这里只补写既有实例。
                # ★ 带 intent_gen：若这次点击后立刻又点了关，apply 会在
                #   每个写入点前发现代号变了并放弃，不会把中文留到关通道后。
                self._live_apply(auto_rename=True, intent_gen=_gen)
            else:
                self._live_restore(intent_gen=_gen)

    @Property(str, notify=stateChanged)
    def languageStatusText(self):
        return self._language_status_text(engine.control_status())

    def _language_status_text(self, st, *, receipt_only=False):
        adapt = getattr(self, "_autoAdaptStatus", "")
        if not st:
            return adapt or "语言控制面未就绪"
        err = str(st.get("error") or "")
        # 行未处理完 / 后台首轮发现未完成是正常进度，不是写入异常。
        progress = (err.startswith("row-pass-pending:")
                    or err.startswith("discovery-pending:"))
        if err and not progress:
            low = err.lower()
            if "afterfxlib build not verified" in low:
                return adapt or "参数通道：当前 AE build 未适配（已安全拒写，详见诊断）"
            if "dvaui text build not verified" in low:
                return "语言通道（参数+文本）：dvaui 构建未验证（整通道未启用，已安全拒写，详见诊断）"
            if "flt build not verified" in low:
                return "下拉通道：当前 AE build 未适配（已安全拒写，详见诊断）"
            if "callsite bytes mismatch" in low:
                return "参数通道：调用点指令校验失败（已安全拒写，详见诊断）"
            return "语言同步异常：%s" % err[:80]
        mode = str(st.get("mode") or "")
        background = bool(st.get("background_discovery_pending"))
        if st.get("language_ready"):
            # 证据化文案：language_ready 只表示“hook 已挂载 + 本轮写入无错”，
            # 不等于面板已经显示中文；必须看 draw_stats 的实际替换计数，
            # 否则 2026 text hook 未命中/词典未覆盖时仍会误报“已显示中文”。
            ds = st.get("draw_stats") or {}
            if not isinstance(ds, dict):
                ds = {}
            if ds.get("call_intact") is False:
                return "参数通道：调用点字节校验失败（写入可能未生效，建议重开工具，详见诊断）"
            text_enabled = bool(ds.get("text_enabled"))
            text_hits = int(ds.get("text_hits") or 0)
            text_replaced = int(ds.get("text_replaced") or 0)
            text_err = int(ds.get("text_err") or 0)
            text_skip_invalid = int(ds.get("text_skip_invalid") or 0)
            text_skip_capacity = int(ds.get("text_skip_capacity") or 0)
            draw_replaced = int(ds.get("replaced") or 0)
            rendered = "参数名" if st.get("popup_error") else "参数/下拉"
            if receipt_only:
                applied_text = "参数通道已收到绘制替换回执"
            else:
                applied_text = ("当前已发现的" if background else "") + rendered + "已显示中文"
            if text_enabled:
                if text_replaced > 0 and text_err == 0:
                    text = applied_text
                elif text_err > 0:
                    text = "参数通道已就绪；文本标签替换异常 %d（详见诊断）" % text_err
                elif text_hits > 0 and text_skip_capacity > 0:
                    text = ("参数通道已就绪；文本标签容量限制跳过 %d"
                            "（详见诊断）" % text_skip_capacity)
                elif text_hits > 0:
                    text = "参数通道已就绪；文本标签未命中（词典可能未覆盖，详见诊断）"
                else:
                    text = "参数通道已就绪；文本标签已启用，等待首次绘制确认"
                if text_err == 0 and text_skip_invalid > 0:
                    text += "；文本标签无效跳过 %d" % text_skip_invalid
            elif draw_replaced > 0:
                text = applied_text
            else:
                text = "参数通道已就绪（等待首次绘制确认）"
            if mode != "on":
                if receipt_only:
                    text = "参数/下拉已收到停用回执"
                else:
                    text = ("当前已发现的参数/下拉已还原原文" if background
                            else "参数/下拉已还原原文")
            # 会话写入或重连后可能缺少下拉恢复所需的记录。
            if mode != "on" and st.get("popup_restore_unknown"):
                if receipt_only:
                    text = "参数已收到停用回执；下拉还原待确认。"
                else:
                    text = ("参数已还原原文；下拉通道状态未知，可能仍显示译文。"
                            "如仍显示译文，请保存工程并重启 AE。")
                if background:
                    text += "，正在检查新增效果…"
                return text
            unsupported = int(st.get("popup_unsupported") or 0)
            if unsupported and mode == "on":
                text += "（%d 组下拉选项容量未确认，保留原文）" % unsupported
            if st.get("popup_error"):
                if st.get("popup_restore_unknown"):
                    text += "；下拉通道状态未知（可能未还原）"
                elif mode != "on":
                    # 刷新失败可能已清除下拉会话，无法确认停用后的结果。
                    text = ("参数可能已还原原文；下拉通道可能未还原。"
                            "如仍显示译文，请保存工程并重启 AE。")
                else:
                    # 刷新失败之前，下拉会话可能已经写入过译文。
                    popup_error = getattr(self, "_autoAdaptPopupError", "") or st["popup_error"]
                    text += "；下拉通道不可用：%s" % str(popup_error)[:150]
            if background:
                text += "，正在检查新增效果…"
            return text
        if background:
            if st.get("rows_pending"):
                return "参数/下拉同步中：已发现的项已应用，正在检查新增效果…"
            return "参数/下拉同步中：正在检查新增效果…"
        if (st.get("pending")
                or st.get("phase") in ("language-pending", "control")):
            return "参数/下拉同步中…"
        return "参数/下拉控制已接收，等待应用"

    @Property(str, notify=stateChanged)
    def hostAeVersionText(self):
        """运行 host 的真实版本（以进程元数据为准，不是 UI 选中安装版本）。"""
        now = time.monotonic()
        cached = self._hostAeCache
        if cached is not None and now - self._hostAeCacheAt < 1.0:
            return cached
        try:
            from core import proc as _proc

            procs = _proc.list_ae_hosts()
        except Exception:
            text = "运行 AE：无法读取进程信息"
        else:
            if not procs:
                text = "运行 AE：未运行"
            elif len(procs) != 1:
                text = "运行 AE：检测到 %d 个候选进程，请只保留一个 AE 编辑窗口" % len(procs)
            else:
                ver = str(procs[0].get("fileVersion") or "")
                text = "运行 AE：%s" % (ver or "未知版本")
        self._hostAeCache = text
        self._hostAeCacheAt = now
        return text

    @Slot(result="QVariantList")
    def channelState(self):
        """当前所有组的三通道状态（给左栏开关做初值/回显）。

        返回 [effect, param, enum]，取「所有组都开」为 true。
        """
        if not self._inv or not self._inv.groups:
            return self._engineSession.channels(not self._reopenedBackground, True)
        return self._engineSession.channels(
            all(CH_EFFECT in group.channels for group in self._inv.groups),
            all(CH_PARAM in group.channels for group in self._inv.groups),
        )

    @Slot()
    def selectAll(self):
        if not self._inv:
            self.toast.emit("warn", "请先扫描插件")
            return
        n = 0
        for g in self._inv.groups:
            if not g.builtin_adobe:
                g.selected = True
                n += 1
        self._invalidateAiDraft()
        self._syncGroupModel(reset=False)
        self.stateChanged.emit()
        self.toast.emit("info", "已全选 %d 组插件" % n)

    @Slot()
    def selectNone(self):
        if not self._inv:
            self.toast.emit("warn", "请先扫描插件")
            return
        for g in self._inv.groups:
            g.selected = False
        self._invalidateAiDraft()
        self._syncGroupModel(reset=False)
        self.stateChanged.emit()
        self.toast.emit("info", "已取消全部勾选")

    @Slot()
    def enableRuntimeSelection(self):
        if not self._inv or self._engineBusy or not self._store.resolve():
            return
        self.selectAll()
        if not self._inv.selected():
            return
        self._saveSelectionNow()
        self._engine_intent_set(True)
        if engine.engine_running():
            self._reload_engine_channels(allow_empty=True)
        else:
            self.startEngine()

    @Slot()
    def pickEffectsFile(self):
        """请求界面弹出文件选择框。

        ★ 崩溃根因（2026-10-21）：
          本程序是 QGuiApplication（纯 QML），
          但这里调了 QtWidgets 的 QFileDialog —— QtWidgets 需要 QApplication，
          在 QGuiApplication 下会直接崩溃。
          改为发信号让 QML 用原生 FileDialog 打开。
        """
        self.effectsPickRequested.emit()

    @Slot(str)
    def pickEffectsFileResult(self, path: str):
        """QML 文件选择框返回路径后调用。"""
        if path:
            self.loadEffects(path)

    @Slot(str)
    def loadEffects(self, path: str):
        table = load_effect_names(path)
        if not table:
            self.toast.emit("warn", "效果名表为空或不存在：%s" % path)
            return
        added = merge_into_store(self._store, table)
        self._effectsPath = path
        self.stateChanged.emit()
        self._bump_dict()
        self.toast.emit("info", "已载入效果名 %d 条（新增 %d）" % (len(table), added))

    @Slot(str)
    def importPack(self, path: str):
        if not path or not os.path.exists(path):
            self.toast.emit("warn", "请先选择一个 .aeloc 词典包")
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
            pv = self._store.preview_import(data)
            if not pv.verify_ok:
                raise PackError(pv.verify_msg)
            info = self._store.import_pack(data)
            self._store.save(os.path.join(DATA_ROOT, "store.json"))
            self._hot_reload_notify()
            self.stateChanged.emit()
            self._bump_dict()
            self.toast.emit(
                "info", "已导入 %s v%s（%d 条，%s）· %s"
                % (info.name, info.version, info.entries, info.verify_msg, pv.summary)
            )
        except Exception as exc:
            self.toast.emit("error", "导入失败：%s" % exc)

    @Slot(str, result=str)
    def exportDict(self, path: str):
        """把当前全部生效词条导出为可重新导入的 .aeloc 词典包。"""
        path = (path or "").strip().replace("file:///", "")
        if not path:
            return "未指定导出路径"
        if not path.lower().endswith(".aeloc"):
            path += ".aeloc"
        try:
            from core.dictpack.pack import build_pack, write_pack

            entries = {}
            for k, r in self._store.resolve().items():
                if r.zh:
                    entries[k] = {"zh": r.zh, "priority": 50,
                                  "note": "来源:%s" % r.layer}
            if not entries:
                return "词典为空，没有可导出的内容"
            data = build_pack(
                pack_id="com.aelocalizer.user-backup",
                name="AeLocalizer 词典备份",
                version=time.strftime("%Y.%m.%d"),
                author="AeLocalizer",
                entries=entries,
                applies_to={
                    "aeVersions": ["2021", "2022", "2023", "2024", "2025", "2026"],
                    "plugins": [],
                    "encoding": "auto",
                },
                description="当前生效词条的完整备份（%d 条）" % len(entries),
            )
            write_pack(data, path)
            msg = "已导出 %d 条到 %s" % (len(entries), path)
            self.toast.emit("info", msg)
            return msg
        except Exception as exc:
            msg = "导出失败：%s" % str(exc)[:100]
            self.toast.emit("error", msg)
            return msg

    @Slot(result="QVariantList")
    def preflight(self):
        if not self._inv:
            return [{"ok": False, "warn": False, "label": "尚未扫描", "detail": "请先扫描插件"}]
        rep = preflight(self._inv, self._store)
        return [
            {"ok": i.ok, "warn": i.warning, "label": i.label, "detail": i.detail}
            for i in rep.items
        ]

    @Slot(result=str)
    def retrustAe(self):
        """重新信任当前 AE 版本（更新模块哈希基线）。

        ★★ 2026-10-23：原来 modhash.verify() 发现模块哈希变了会【拒绝应用】，
          提示「请到引擎页点重新信任」，但 QML 里根本没有这个入口 ——
          用户从 AE 2026 切到 2025 后就彻底卡死，一键应用永远被拒。

          安全考虑：这里只在用户【主动点按钮】时更新基线，不自动放开。
          基线记录的是 AfterFX.exe / FLT.dll 的哈希，用来确认「AE 没被别人
          替换过」，换了版本当然对不上。
        """
        try:
            from core import modhash

            root = self._aeRoot()
            if not root:
                return "找不到 AE 安装目录"
            res = modhash.reset_baseline(root, DATA_ROOT)
            if res.ok:
                self.toast.emit("info", res.reason or "已重新信任")
                self.stateChanged.emit()
                return res.reason or "已重新信任"
            self.toast.emit("error", res.reason or "重新信任失败")
            return res.reason or "重新信任失败"
        except Exception as exc:
            return "重新信任失败：%s" % str(exc)[:80]


    # ---------------------------------------------------------- 引擎槽

    def refreshEngine(self):
        """重新检查引擎/Fixer/词典状态（轻量;内部方法,QML 直接绑 engineReport）。"""
        self._engine = engine.check(self._root, self._version)
        # ★ refreshEngine 已经拿到新状态，必须同步更新 engineReport 的
        #   短 TTL 缓存；否则 QML 紧接着调用 engineReport 会把旧 _engine
        #   覆盖回来（自动恢复后仍显示“未启动”）。
        self._engineReportCache = (time.time(), self._engine)
        self.stateChanged.emit()

    @Slot(result="QVariantList")
    def engineReport(self):
        """引擎页的检查清单。

        ★ 性能（2026-10-21）：engine.check() 要读 2 万条词典，实测 1.44s。
          而 QML 每次刷新都会调用 -> 点击按钮时明显卡顿。
          用短 TTL 缓存（2 秒），连续调用只算一次。
        """
        now = time.time()
        cache = getattr(self, "_engineReportCache", None)
        if cache and (now - cache[0]) < 30.0:
            self._engine = cache[1]
        else:
            self._engine = engine.check(self._root, self._version)
            self._engineReportCache = (now, self._engine)
        e = self._engine
        items = [
            {
                "ok": e.guard_available,
                "warn": False,
                "label": "汉化引擎",
                "detail": e.guard_path if e.guard_available else e.guard_reason,
            },
            {
                "ok": e.dict_available,
                "warn": False,
                "label": "译名词典",
                "detail": "%d 条" % e.dict_entries if e.dict_available else "未找到 dict.bin",
            },
            {
                "ok": e.engine_dict_entries > 0,
                "warn": True,
                "label": "参数名词典",
                "detail": "%d 条（按当前勾选生成）" % e.engine_dict_entries
                          if e.engine_dict_entries else "尚未生成，点「生成并启动」",
            },
            {
                "ok": e.enum_dict_entries > 0,
                "warn": True,
                "label": "下拉值词典",
                "detail": "%d 条" % e.enum_dict_entries
                          if e.enum_dict_entries else "缺失（下拉值会显示英文）",
            },
            {
                "ok": (not e.fixer_needed) or e.fixer_installed,
                "warn": True,
                "label": "UTF-8 修复层",
                "detail": e.fixer_reason or "未知",
            },
            {
                "ok": True,
                "warn": e.running,
                "label": "引擎进程",
                "detail": "运行中" if e.running else "未运行",
            },
        ]
        return items

    def _hot_reload_notify(self) -> None:
        """词典有变:按当前勾选重建引擎词典,并通知守护热换(不重启引擎)。

        守护(2026-10-28 热换补丁)轮询 <hook>/out/RELOAD_DICT.flag 的 mtime,
        发现变化就重跑 build_dict() 并把增量推给运行中的 JS。任何失败都静默
        ——守护侧下次「重启引擎」仍会拿到全量新词典,这里只是快路径。
        """
        try:
            gp, _why = engine.find_guard()
            if not gp:
                return
            entries = {k: r.zh for k, r in self._store.resolve().items() if r.zh}
            if not entries:
                return
            if self._inv is not None:
                need = [g for g in self._inv.selected()
                        if CH_PARAM in g.channels or CH_ENUM in g.channels]
                if not need:
                    return
                selected = {g.group_id for g in need}
            else:
                selected = None
            attrib_idx = self._attrib if self._attribLoaded else None
            engine.prepare_dict(
                entries,
                attribution=attrib_idx,
                selected_groups=selected if attrib_idx else None,
                keep_unowned=self._keepUnowned,
            )
            self._invalidate_lang_payload_cache()
            flag = os.path.join(os.path.dirname(gp), "out", "RELOAD_DICT.flag")
            os.makedirs(os.path.dirname(flag), exist_ok=True)
            with open(flag, "w", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%dT%H:%M:%S"))
        except Exception:
            pass

    _ENGINE_INTENT_KEY = "engine_master_intent"

    def _engine_intent_set(self, on: bool) -> None:
        try:
            from core import settings as _settings

            _settings.put(DATA_ROOT, self._ENGINE_INTENT_KEY, bool(on))
        except Exception:
            pass

    def _engine_intent_allowed(self) -> bool:
        """主意图：显式 stop 为 False，显式 start/on 为 True；缺键才按选区推导。"""
        try:
            from core import settings as _settings

            v = _settings.get(DATA_ROOT, self._ENGINE_INTENT_KEY, None)
            if v is not None:
                return bool(v)
        except Exception:
            pass
        try:
            if self._inv:
                return any(
                    CH_PARAM in g.channels or CH_ENUM in g.channels
                    for g in self._inv.selected())
        except Exception:
            pass
        return bool(self._reopenedBackground)

    def _param_catchup_async(self, reason: str = "") -> None:
        """引擎恢复后，把已存在参数 stream 的显示名缓存补成中文。

        只写参数实例 +display_off wstring，不写序列化名字槽/效果名/注册表。
        走 paramcatalog 的真实参数树，后台有界执行，不重启 AE、不动鼠标。
        """
        if not self._live_auto_allowed():
            return
        if getattr(self, "_paramCatchupBusy", False):
            return
        now = time.monotonic()
        if now - float(getattr(self, "_paramCatchupAt", 0.0)) < 3.0:
            return
        if not self._inv:
            return
        if not any(CH_PARAM in g.channels for g in self._inv.selected()):
            return
        try:
            from core import aelaunch as _al

            pid, _exe = _al.live_ae_target()
        except Exception:
            pid = 0
        if not pid:
            return
        self._paramCatchupBusy = True
        self._paramCatchupAt = now
        _epoch = int(getattr(self, "_liveSessionEpoch", 0))
        _pgen = int(getattr(self, "_paramIntentGen", 0))
        _skey = self._live_session_key(int(pid))

        def work():
            from core import liveparams
            from core.export import collect_layers
            from core.paramdiscovery import parameter_rows
            from core.proc import Process

            merged, _counts = collect_layers(engine.ENGINE_DICT_DIR)
            table = {k: v["zh"] for k, v in merged.items() if v.get("zh")}
            if not table:
                return {"written": 0, "reason": "engine dict empty"}
            allowed = set()
            for g in self._inv.selected() if self._inv else ():
                if CH_PARAM not in g.channels:
                    continue
                for f in getattr(g, "files", ()):
                    if getattr(f, "match_name", ""):
                        allowed.add(f.match_name)
            if not allowed:
                return {"written": 0, "reason": "no param plugins selected"}
            with Process(int(pid), True) as p:
                try:
                    profile, rows, parades, pending = parameter_rows(
                        p, DATA_ROOT,
                        deadline=time.monotonic() + 4.0,
                        cancel=lambda: (int(getattr(self, "_liveSessionEpoch", 0)) != _epoch
                                        or int(getattr(self, "_paramIntentGen", 0)) != _pgen),
                        with_state=True)
                except Exception as exc:
                    return {"written": 0, "reason": "discovery unavailable:%s" % str(exc)[:120]}
                ident = {"version": str(getattr(profile, "version", "")),
                         "flt": str(getattr(profile, "flt_sha", "")),
                         "tdb": str(getattr(profile, "tdb_sha", "")),
                         "plugins": self._param_plugin_hashes()}
                if not self._paramBaseline.identity_ok(ident):
                    return {"written": 0, "reason": "baseline identity changed"}
                self._paramBaseline.set_identity(ident)
                self._paramBaseline.capture_rows(
                    [r for r in rows
                     if (r.get("effectMatchName") or "") in allowed])
                lp = liveparams.LiveParams(p, table, profile=profile,
                                           data_root=DATA_ROOT)
                written = 0
                for _i, row in enumerate(rows):
                    if (int(getattr(self, "_liveSessionEpoch", 0)) != _epoch
                            or int(getattr(self, "_paramIntentGen", 0)) != _pgen
                            or (_i % 64 == 0
                                and self._live_session_key(int(pid)) != _skey)):
                        return {"written": written, "reason": "session/intent changed"}
                    # 按真实 effectMatchName 严格限定选中的 CH_PARAM 插件。
                    if (row.get("effectMatchName") or "") not in allowed:
                        continue
                    display = (row.get("name") or "").strip()
                    pf = (row.get("pfName") or "").strip()
                    if not display:
                        continue
                    effect = str(row.get("effectMatchName") or "")
                    param = str(row.get("matchName") or "")
                    if any("\u3400" <= c <= "\u9fff" for c in display):
                        base = self._paramBaseline.get(effect, param)
                        name = str((base or {}).get("english") or "").strip()
                        if not name:
                            # 没有干净 baseline 的旧中文实例：不猜、不覆盖。
                            continue
                    else:
                        name = display
                    address = int(row.get("address") or 0)
                    # 写入前复核参数流身份/父子/显示缓存，防止过期行。
                    try:
                        if row.get("guid") and p.read(address + 8, 16).hex() != row["guid"]:
                            continue
                        if row.get("parent") and p.u64(address + 32) != int(row["parent"]):
                            continue
                    except Exception:
                        continue
                    slot = lp._slot_from_stream(address)
                    if slot is None or slot.kind != "param":
                        continue
                    # 旧行可能已被新标签覆盖；只写当前显示名仍等于行值的槽。
                    if getattr(slot, "english", "") != display:
                        continue
                    try:
                        pf_raw = p.read(address + liveparams.PF_NAME_OFF, 32)
                        pf_raw = pf_raw.split(b"\x00", 1)[0]
                        pf_real = pf_raw.decode(
                            getattr(lp.profile, "encoding", "mbcs") or "mbcs")
                    except Exception:
                        continue
                    zh = (table.get(name) or table.get(name.strip())
                          or table.get(name.lstrip()))
                    # PF 字段只读用于确认同一参数；不写回 PF。slot.pf_name
                    # 是 latin-1 视图，不能用它和中文 pfName 比较。
                    if (not zh and pf and pf_real == pf
                            and any("\u3400" <= c <= "\u9fff" for c in pf)):
                        zh = pf
                    if not zh or zh == name:
                        continue
                    if lp.write_display(slot, zh):
                        written += 1
                        self._paramBaseline.mark_chinese(effect, param, zh)
                self._paramBaseline.flush()
                return {"written": written, "reason": reason,
                        "roots": len(parades), "rows": len(rows),
                        "pending": bool(pending)}
        def done(res):
            self._paramCatchupBusy = False
            if (int(getattr(self, "_liveSessionEpoch", 0)) != _epoch
                    or int(getattr(self, "_paramIntentGen", 0)) != _pgen
                    or self._live_session_key(int(pid)) != _skey):
                return
            n = int((res or {}).get("written") or 0)
            if n:
                try:
                    self._live_win32_redraw(int(pid))
                except Exception:
                    pass
                self.toast.emit("info", "已补写 %d 个已有参数名" % n)
            why = str((res or {}).get("reason") or "")
            self._live_watch_log("参数实例补写%s:written=%s reason=%s"
                                 % (reason, n, why))
            if (why == "roots pending"
                    or why.startswith("discovery unavailable")
                    or bool((res or {}).get("pending"))):
                # 预算内只扫到部分 parade：让下一拍从 cursor 继续，不能把
                # “已有 rows/写 0 条”当成完成。
                self._paramCatchupAt = 0.0
                QTimer.singleShot(500, self._param_catchup_async)

        def fail(msg):
            self._paramCatchupBusy = False
            self._live_watch_log("参数实例补写失败:%s" % str(msg)[:120])

        self._run(work, done, _busy_text="正在补写已有参数名…",
                  _silent=True, _on_fail=fail, _toast_fail=False)

    def _param_restore_async(self) -> None:
        """param OFF：按 typed baseline 恢复当前参数显示为英文。

        不使用历史地址、不做全局中文反查；只有 baseline 的 effect/param
        身份和当前文本吻合时才写。guard 不停止，enum/effect 不受影响。
        """
        if not self._live_auto_allowed():
            return
        if getattr(self, "_paramRestoreBusy", False):
            return
        if not self._inv:
            return
        try:
            from core import aelaunch as _al

            pid, _exe = _al.live_ae_target()
        except Exception:
            return
        if not pid:
            return
        self._paramRestoreBusy = True
        _epoch = int(getattr(self, "_liveSessionEpoch", 0))
        _pgen = int(getattr(self, "_paramIntentGen", 0))
        _skey = self._live_session_key(int(pid))

        def work():
            from core import liveparams
            from core.paramdiscovery import parameter_rows
            from core.proc import Process

            with Process(int(pid), True) as p:
                try:
                    profile, rows, parades, pending = parameter_rows(
                        p, DATA_ROOT,
                        deadline=time.monotonic() + 4.0,
                        cancel=lambda: (int(getattr(self, "_liveSessionEpoch", 0)) != _epoch
                                        or int(getattr(self, "_paramIntentGen", 0)) != _pgen),
                        with_state=True)
                except Exception as exc:
                    return {"restored": 0, "reason": str(exc)[:120], "pending": False}
                ident = {"version": str(getattr(profile, "version", "")),
                         "flt": str(getattr(profile, "flt_sha", "")),
                         "tdb": str(getattr(profile, "tdb_sha", "")),
                         "plugins": self._param_plugin_hashes()}
                if not self._paramBaseline.identity_ok(ident):
                    return {"restored": 0, "reason": "baseline identity changed"}
                lp = liveparams.LiveParams(p, {}, profile=profile,
                                           data_root=DATA_ROOT)
                restored = 0
                for _i, row in enumerate(rows):
                    if (int(getattr(self, "_liveSessionEpoch", 0)) != _epoch
                            or int(getattr(self, "_paramIntentGen", 0)) != _pgen
                            or (_i % 64 == 0
                                and self._live_session_key(int(pid)) != _skey)):
                        return {"restored": restored, "reason": "cancelled"}
                    effect = str(row.get("effectMatchName") or "")
                    param = str(row.get("matchName") or "")
                    current = str(row.get("name") or "").strip()
                    en = self._paramBaseline.english_for(effect, param, current)
                    if not en or en == current:
                        continue
                    address = int(row.get("address") or 0)
                    try:
                        if row.get("guid") and p.read(address + 8, 16).hex() != row["guid"]:
                            continue
                        if row.get("parent") and p.u64(address + 32) != int(row["parent"]):
                            continue
                    except Exception:
                        continue
                    slot = lp._slot_from_stream(address)
                    if slot is None or slot.kind != "param":
                        continue
                    if getattr(slot, "english", "") != current:
                        continue
                    if lp.write_display(slot, en):
                        restored += 1
                return {"restored": restored, "pending": bool(pending),
                        "reason": "ok"}

        def done(res):
            self._paramRestoreBusy = False
            if (int(getattr(self, "_liveSessionEpoch", 0)) != _epoch
                    or int(getattr(self, "_paramIntentGen", 0)) != _pgen
                    or self._live_session_key(int(pid)) != _skey):
                return
            if int((res or {}).get("restored") or 0):
                try:
                    self._live_win32_redraw(int(pid))
                except Exception:
                    pass
            self._live_watch_log("参数 OFF 恢复:%s" % str(res or {})[:160])
            if bool((res or {}).get("pending")):
                QTimer.singleShot(500, self._param_restore_async)

        def fail(msg):
            self._paramRestoreBusy = False
            self._live_watch_log("参数 OFF 恢复失败:%s" % str(msg)[:120])

        self._run(work, done, _busy_text="正在恢复已有参数名…",
                  _silent=True, _on_fail=fail, _toast_fail=False)

    def _param_plugin_hashes(self) -> dict:
        """选中插件的文件 hash，进入 baseline identity；按 (path,mtime) 缓存。"""
        import hashlib

        cache = getattr(self, "_paramPluginHashes", None)
        if cache is None:
            cache = {}
            self._paramPluginHashes = cache
        out = {}
        if not self._inv:
            return out
        # 用整个 inventory 的插件 hash，选择组变化不应让 baseline 全失效。
        for g in self._inv.groups:
            for f in getattr(g, "files", ()):
                mn = getattr(f, "match_name", "")
                p = getattr(f, "path", "")
                if not mn or not p or not os.path.isfile(p):
                    continue
                try:
                    sig = (p, os.path.getmtime(p), os.path.getsize(p))
                    if sig not in cache:
                        h = hashlib.sha256()
                        with open(p, "rb") as fh:
                            for b in iter(lambda: fh.read(1 << 20), b""):
                                h.update(b)
                        cache[sig] = h.hexdigest()
                    out[mn] = cache[sig]
                except OSError:
                    continue
        return out

    def _write_param_identity(self, procs=None) -> str:
        """把 AE/插件 hash 配置交给 managed runtime（非全局选中集合）。

        ``ae_version`` 是 UI 选中/保存的安装版本（兼容旧字段），
        ``host_ae_version`` 才是当前运行 AE 的真实版本；运行时身份必须用后者，
        否则换版本/多版本共存时 popup baseline 会被错误命名空间复用。
        """
        import json

        host_ver = ""
        try:
            from core import proc as _proc

            if procs is None:
                procs = _proc.list_ae_hosts()
            if len(procs) == 1:
                host_ver = str(procs[0].get("fileVersion") or "")
        except Exception:
            host_ver = ""
        obj = {"schema": 1, "ae_version": str(self._version),
               "host_ae_version": host_ver,
               "plugins": self._param_plugin_hashes()}
        d = os.path.join(engine.DATA_ROOT, "guard_runtime")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "param_identity.json")
        import uuid

        tmp = p + ".tmp." + uuid.uuid4().hex
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        for i in range(20):
            try:
                os.replace(tmp, p)
                return p
            except PermissionError:
                if i == 19:
                    raise
                time.sleep(0.02)
        return p

    def _auto_restore_engine_async(self, *, refresh_runtime: bool = False, refresh_popup: bool = False) -> None:
        """工具启动/扫描后，若持久化选区仍开着参数/下拉通道但 guard 不在，
        自动恢复一次引擎；不碰效果名通道，也不每拍重试。"""
        if not self._live_auto_allowed():
            return
        now = time.monotonic()
        if getattr(self, "_engineBusy", False):
            return
        if not refresh_runtime and (now - float(getattr(self, "_engineAutoTriedAt", 0.0))) < 30.0:
            return
        if not self._inv:
            return
        if not self._engine_intent_allowed():
            return
        need = [g for g in self._inv.selected()
                if CH_PARAM in g.channels or CH_ENUM in g.channels]
        if not need:
            return
        reopened = getattr(self, "_reopenedBackground", None)
        if reopened and self._langWant and engine.active_language_session() == reopened:
            # Opening the settings window alone must leave the current user's session in place.
            return
        self._reopenedBackground = None
        try:
            if engine.engine_running() or engine.read_guard_lock(stale_ok=True):
                # managed runtime 已存活：即便 guard 已有旧 host，也必须把
                # 当前唯一 AE 的新 host + 最后语言 intent 送到控制面，不能
                # 仅因 engine_running 就跳过重连。
                old_runtime = refresh_runtime and guardruntime.managed_code_outdated(engine.DATA_ROOT)
                popup_disabled = refresh_popup and bool(engine.control_status().get("popup_error"))
                if not (old_runtime or popup_disabled):
                    self._send_language_control()
                    return
        except Exception:
            return
        gp, _why = engine.find_guard()
        if not gp:
            return
        self._engineAutoTried = True
        self._engineAutoTriedAt = now
        self._engineRestoreQuiet = True
        self.toast.emit("info", "检测到参数名/下拉值通道已开启，正在恢复引擎…")
        try:
            self.startEngine()
        except Exception:
            self._engineAutoTried = False
            self._engineRestoreQuiet = False
            return
        if not self._engineBusy:
            # startEngine 早退（词典未 ready/无勾选等）：保留 backoff，下一次
            # 扫描/ready 再试，但不永久吞掉。
            self._engineAutoTried = False
            self._engineRestoreQuiet = False

    def _send_language_control(self, procs=None) -> bool:
        """把当前唯一 AE host + 持久/最后 intent 发给 managed control。"""
        try:
            from core import proc as _proc

            if procs is None:
                procs = _proc.list_ae_hosts()
            if len(procs) != 1:
                return False
            p = procs[0]
            host = {"pid": int(p.get("pid") or 0),
                    "ctime": int(p.get("creation_time") or 0),
                    "image": os.path.abspath(
                        str(p.get("image") or "AfterFX.exe"))}
            if not host["pid"] or not host["ctime"]:
                return False
            with self._liveIntentLock:
                command = self._engineSession.language_command(
                    host_identity(host), self.channelState()[1], time.monotonic_ns())
                sent = self._engineSession.sent_language_command
            if sent == command:
                status = engine.control_status()
                if command.acknowledged(status):
                    return True
            try:
                self._write_param_identity(procs)
            except Exception:
                pass
            ok = bool(engine.send_control(command.mode, command.generation, host))
            if ok:
                with self._liveIntentLock:
                    if (self._engineSession.language_generation == command.generation
                            and self._engineSession.language_host == command.host):
                        self._engineSession.sent_language_command = command
            return ok
        except Exception:
            return False

    def _lang_dict_path(self):
        return os.path.join(engine.ENGINE_DICT_DIR, "extra_overrides.json")

    def _lang_file_fingerprint(self):
        """Hash of the published JSON; external edits invalidate the cache."""
        try:
            import hashlib

            h = hashlib.sha256()
            with open(self._lang_dict_path(), "rb") as f:
                for b in iter(lambda: f.read(1 << 20), b""):
                    h.update(b)
            return h.hexdigest()
        except Exception:
            return ""

    def _lang_capture_inputs(self, need):
        """Exact inputs captured before resolve/build; never read current later."""
        try:
            ids = tuple(sorted(str(g.group_id) for g in need))
        except Exception:
            ids = ()
        return {
            "rev": int(getattr(self._store, "revision", 0)),
            "ids": ids,
            "attrib_id": id(self._attrib) if self._attribLoaded else 0,
            "keep": bool(self._keepUnowned),
        }

    def _lang_inputs_signature(self, snap, fingerprint):
        return (int(snap.get("rev") or 0), tuple(snap.get("ids") or ()),
                int(snap.get("attrib_id") or 0), bool(snap.get("keep")),
                str(fingerprint or ""))

    def _lang_current_inputs(self, need=None):
        if need is None:
            try:
                need = [g for g in self._inv.selected()
                        if CH_PARAM in g.channels or CH_ENUM in g.channels]
            except Exception:
                need = []
        return self._lang_inputs_signature(
            self._lang_capture_inputs(need), self._lang_file_fingerprint())

    def _lang_payload_signature(self, need):
        """Inputs that determine the selected full language payload."""
        return self._lang_current_inputs(need)

    def _invalidate_lang_payload_cache(self):
        try:
            self._langPayloadSig = None
        except Exception:
            pass

    def _reload_engine_channels(self, allow_empty: bool = False) -> bool:
        """Mode-only toggle fast path; rebuild only when inputs/file changed."""
        if not self._inv:
            return False
        need = [g for g in self._inv.selected()
                if CH_PARAM in g.channels or CH_ENUM in g.channels]
        lang_on = bool(self.channelState()[1])
        if not need and not allow_empty and not lang_on:
            return False
        # OFF is ALWAYS a mode-only control operation. Do not compute an empty
        # "need" signature and republish: mutating same groups' channels to
        # empty must not destroy the trusted payload needed by the next ON.
        if not lang_on:
            try:
                self._send_language_control()
                QTimer.singleShot(350, self.stateChanged.emit)
                QTimer.singleShot(1200, self.stateChanged.emit)
                return True
            except Exception as exc:
                self._live_watch_log("语言 OFF 控制失败: %s" % str(exc)[:120])
                return False
        sig = self._lang_payload_signature(need)
        cached = (getattr(self, "_langPayloadSig", None) == sig)
        selected = {g.group_id for g in need}
        attrib_idx = self._attrib if self._attribLoaded else None
        try:
            if not cached:
                # Always publish the FULL selected language map; OFF does not
                # clear it. Actual empty selection publishes an empty map.
                snap0 = self._lang_capture_inputs(need)
                entries = ({k: r.zh for k, r in self._store.resolve().items()
                            if r.zh} if need else {})
                res = engine.prepare_dict(
                    entries, attribution=attrib_idx,
                    selected_groups=selected if attrib_idx else None,
                    keep_unowned=self._keepUnowned)
                # Never claim a merely current file as this builder's output:
                # another publisher may have replaced it between JSON/DAT and
                # prime. Verify the builder's exact published bytes and the
                # captured semantic inputs.
                expected = str(getattr(res, "payload_sha256", "") or "")
                fp = self._lang_file_fingerprint()
                if (expected and fp == expected
                        and self._lang_current_inputs()
                        == self._lang_inputs_signature(snap0, expected)):
                    self._langPayloadSig = self._lang_inputs_signature(
                        snap0, expected)
                else:
                    self._langPayloadSig = None
                if lang_on:
                    engine.request_reload()
            # ON with unchanged inputs: no resolve, no DAT rebuild, no reload
            # flag, no map RPC churn. Control still delivers the intent.
            self._send_language_control()
            QTimer.singleShot(350, self.stateChanged.emit)
            QTimer.singleShot(1200, self.stateChanged.emit)
            return True
        except Exception as exc:
            self._langPayloadSig = None
            self._live_watch_log("参数通道热换失败: %s" % str(exc)[:120])
            return False

    @Slot()
    def startEngine(self):
        """按当前勾选生成引擎词典，然后启动守护进程。"""
        if self._engineBusy:
            self.toast.emit("warn", "引擎正在处理中，请稍候")
            return
        if not self._inv:
            self._engineDictReason = "引擎词典未生成：尚未扫描插件"
            self.toast.emit("warn", "请先扫描插件")
            return

        gp, why = engine.find_guard()
        if not gp:
            self._engineDictReason = "引擎词典未生成：%s" % str(why)[:120]
            self.toast.emit("error", why)
            return
        # 迁移闸：旧外部 guard 还可能活着时绝不 spawn 第二个（高权 guard
        # 的 CIM 命令行可能读不到，本检查只认 lock PID 是否仍活）。
        try:
            _old = (0 if os.environ.get("AELOC_DATA_ROOT")
                    else engine.read_legacy_guard_lock_pid_any(engine.DATA_ROOT))
            if _old:
                self._engineDictReason = (
                    "引擎词典未生成：旧外部参数守护仍存活(PID %s)" % _old)
                self.toast.emit(
                    "error",
                    "检测到旧外部参数守护仍存活(PID %s)。请先正常停止旧守护"
                    "（不要强杀未知 PID），完成一次初始化后再启动受控 runtime。" % _old)
                return
        except Exception:
            pass
        # 受控 runtime：首次启动/升级时把外部 guard 只读复制到用户目录；
        # 外部共享资产不改。运行时 OUT/TMP/lock 都随 runtime 落到用户目录。
        try:
            # 源永远是项目模板，runtime 实际文件每次启动前校验/修复。
            try:
                guardruntime.ensure_runtime(
                    engine.DATA_ROOT, guardruntime.template_path())
            except guardruntime.RuntimeUpgradeRequired:
                # 本项目模板的旧部署：做一次原子升级（不删 out/control/tmp，
                # 不要求用户重新初始化）；只有未知/外部 guard 才拒绝。
                if not guardruntime.can_auto_upgrade(engine.DATA_ROOT):
                    raise
                guardruntime.ensure_runtime(
                    engine.DATA_ROOT, guardruntime.template_path(), force=True)
            gp = guardruntime.runtime_guard(engine.DATA_ROOT)
        except guardruntime.RuntimeUpgradeRequired as _e:
            self._engineDictReason = "引擎词典未生成：%s" % str(_e)[:120]
            self.toast.emit("error", str(_e) + "；请先完成一次初始化。")
            return
        except Exception as _e:
            # 普通受控模式 fail-closed：staging 失败绝不回退外部共享 guard。
            self._engineDictReason = "引擎词典未生成：受控运行时初始化失败 %s" % (
                str(_e)[:120])
            self.toast.emit("error", "受控参数运行时初始化失败:" + str(_e)[:160])
            return

        # 只勾了「效果名」通道的组仍需要一个单 Frida host 来承载 effect
        # DisplayHook；参数词典可以合法为空（不启动语言替换）。
        need = [g for g in self._inv.selected()
                if CH_PARAM in g.channels or CH_ENUM in g.channels]
        need_effect = bool(self.channelState()[0])
        if not need and not need_effect:
            self._engineDictReason = "引擎词典未生成：勾选组未开参数名/下拉值/效果名通道"
            self.toast.emit("warn", "勾选的插件都没开参数名/下拉值/效果名通道，无需启动引擎")
            return

        # Capture the complete input snapshot BEFORE resolve. The worker must
        # never derive its cache key from newer current values.
        snap0 = self._lang_capture_inputs(need)
        selected = {g.group_id for g in need}
        attrib_idx = self._attrib if self._attribLoaded else None
        keep_unowned = bool(snap0["keep"])
        entries = ({k: r.zh for k, r in self._store.resolve().items()
                    if r.zh} if need else {})
        if not entries and need:
            # 真实原因：选中组在词典库中匹配到 0 条（导入缺失/归属不符/被勾选剔除），
            # 不是"引擎没启动"或"权限不足"。
            try:
                total = len(self._store.resolve())
            except Exception:
                total = -1
            self._engineDictReason = (
                "引擎词典为空：选中的 %d 个插件组解析出 0 条中文（词典库总条目 %s；"
                "请检查词典包是否导入、插件组归属与勾选过滤）"
                % (len(selected), total if total >= 0 else "未知"))
            self.toast.emit("warn", "词典为空，请先导入词典包")
            return
        # Full selected payload is retained even while language is OFF, so a
        # later ON is a control-only switch. The runtime mode prevents writes.
        _drop = None

        self._engine_intent_set(True)
        if self._engineSession.stop_result is not None:
            self._status = "就绪"
        self._engineSession.begin(EngineOperation.STARTING)
        self.stateChanged.emit()

        def work():
            res = engine.prepare_dict(
                entries,
                attribution=attrib_idx,
                selected_groups=selected if attrib_idx else None,
                keep_unowned=keep_unowned,
                drop_keys=_drop,
            )
            try:
                with self._liveIntentLock:
                    expected = str(getattr(res, "payload_sha256", "") or "")
                    actual = self._lang_file_fingerprint()
                    current = self._lang_current_inputs()
                    captured = self._lang_inputs_signature(snap0, expected)
                    # Prime only with the exact captured inputs and the actual
                    # bytes this builder published; another publisher between
                    # JSON/DAT/prime must invalidate.
                    if expected and actual == expected and current == captured:
                        self._langPayloadSig = captured
                    else:
                        self._langPayloadSig = None
            except Exception:
                self._langPayloadSig = None
            # ★ 模块哈希钉死：AE 被补丁/精简版替换过就拒绝写入
            ok, msg = engine.start_engine(
                gp, engine.ENGINE_DICT_DIR, hold_seconds=3600,
                ae_root=self._aeRoot(), data_root=engine.DATA_ROOT)
            return res, ok, msg

        def done(payload):
            res, ok, msg = payload
            self._engineSession.finish()
            self._engineAutoTried = False
            _quiet_restore = bool(getattr(self, "_engineRestoreQuiet", False))
            self._engineRestoreQuiet = False
            if ok:
                self._engineDictInfo = res.summary()
                self._engineDictReason = ""
                if not self._engine_intent_allowed():
                    # 启动过程中用户明确 stop：最后意图优先，不留下引擎。
                    self.stopEngine()
                    return
                self.toast.emit("info", "%s · %s" % (res.summary(), "已启动"))
                # ★ 引擎启停绝不再调用旧 _liveNamesAsync：它会写
                # EffectNameTable/NameTable 并在 0 写入时 _restart_ae_async。
                # 参数语言由 managed runtime 唯一写者负责，effect 走独立 hook。
                # 启动期间 UI 可能已从选区 A 改到 B。不要拿启动时缓存直接
                # 发最新 ON；走缓存校验，miss 时重建当前选区，再发最终 control。
                try:
                    self._reload_engine_channels(allow_empty=True)
                except Exception:
                    self._send_language_control()
            else:
                self._engineDictReason = "引擎词典已生成但引擎启动失败：%s" % (
                    str(msg).splitlines()[0][:120] if msg else "未知原因")
                self.toast.emit("error", msg)
            self.refreshEngine()

        def start_fail(msg):
            # _run 在线程异常时不会调用 done；不清 busy 会永久拒绝后续启动。
            self._engineSession.finish()
            self._engineAutoTried = False
            self._engineAutoTriedAt = time.monotonic()
            self._engineRestoreQuiet = False
            self._engineDictReason = "引擎启动异常：%s" % (
                str(msg).splitlines()[0][:120] if msg else "未知")
            self.toast.emit("error", msg.splitlines()[0][:200])
            self.stateChanged.emit()
            self.refreshEngine()

        self._run(work, done, _busy_text="正在启动引擎…",
                  _on_fail=start_fail, _toast_fail=False)

    @Slot()
    def stopEngine(self):
        """优雅停止引擎（写 STOP_NOW.flag，等它自己卸载 hook）。"""
        self._engine_intent_set(False)
        self._reopenedBackground = None
        engine.request_foreground_window()
        self._paramIntentGen = int(getattr(self, "_paramIntentGen", 0)) + 1
        if self._engineBusy:
            self.toast.emit("warn", "引擎正在处理中，请稍候")
            self.engineSessionChanged.emit()
            return
        if not engine.engine_running():
            self._engineSession.finish(stop_result=True)
            self._status = "引擎未运行"
            self.toast.emit("info", "引擎本来就没在运行")
            self.refreshEngine()
            return
        self._engineSession.begin(EngineOperation.STOPPING)
        self._status = self._engineSession.stop_status
        self.stateChanged.emit()

        def done(payload):
            ok, msg = payload
            self._engineSession.finish(stop_result=bool(ok))
            self._status = self._engineSession.stop_status
            _quiet_stop = bool(getattr(self, "_engineStopQuiet", False))
            self._engineStopQuiet = False
            self.toast.emit("info" if ok else "warn", msg)
            # managed host 已退出：effect proxy 一并失效，下一次 effect ON
            # 必须新建 proxy/session，不能复用本地 running 缓存。
            if ok and getattr(self, "_liveHook", None) is not None:
                try:
                    self._liveHook.stop()
                except Exception:
                    pass
                self._liveHook = None
                self._liveHookSession = None
                self._liveHookResumedGen = None
                self._liveHookStopped = True
            # 引擎启停不再调用旧 _liveNamesAsync（不写 name slot、不重启 AE）。
            self.refreshEngine()

        def stop_fail(msg):
            self._engineSession.finish(stop_result=False)
            self._status = self._engineSession.stop_status
            self._engineStopQuiet = False
            self.refreshEngine()

        self._run(engine.stop_engine, done, _busy_text="正在停止引擎…", _on_fail=stop_fail)

    def _liveNamesAsync(self, mode: str, reason: str = "",
                        allow_restart: bool = True):
        """不重启 AE 就把工程里已有的名字换成中文/还原英文。

        ★ 原理(2026-10-30 Ghidra 定案): TDB_StreamBase::GetDisplayName 直接把
          this+0xE8 的 std::wstring 缓存返回给界面, _Mysize(this+0xF8) 为 0 时才
          现场生成。所以改写 +0xE8 并同步 _Mysize, 正在运行、已经建好的效果会立即
          换名, 不用重启 AE。
        ★ 只在恰好一个 AE 时动手; 扫不到名字就退回「自动重启 AE」。
        ★ allow_restart=False（2026-11-01）: 效果名开关/常驻补写的自动路径用 ——
          一个名字都没写成时**不**重启用户的 AE（后台路径绝不擅动进程），
          引擎启停路径保持原语义（True）。
        """
        try:
            from core import aelaunch as _al

            pid, exe = _al.live_ae_target()
        except Exception:
            pid, exe = 0, ""
        if not pid:
            if mode == "on" and allow_restart:
                self._restart_ae_async(reason)
            return

        def work():
            from core import liveparams
            from core.export import collect_layers
            from core.proc import Process

            if mode == "off":
                session = liveparams.load_session(engine.DATA_ROOT)
                records = session.get("records") or []
                if not records:
                    return {"restored": 0, "skipped": 0, "oldProcess": False}
                if int(session.get("pid") or 0) != pid:
                    liveparams.clear_session(engine.DATA_ROOT)
                    return {"restored": 0, "oldProcess": True}
                eff = [r for r in records if r.get("obj")]
                tab = [r for r in records if r.get("addr")]
                with Process(pid, True) as p:
                    a = liveparams.EffectNameTable(
                        p, data_root=engine.DATA_ROOT).restore(eff)
                    b = liveparams.NameTable(p, {}).restore(tab)
                liveparams.clear_session(engine.DATA_ROOT)
                return {"restored": int(a.get("restored") or 0) + int(b.get("restored") or 0),
                        "skipped": int(a.get("skipped") or 0) + int(b.get("skipped") or 0)}

            # ★★ 2026-11-06:不再走 aejsx.project_names(AfterFX.exe -r 脚本转发,
            #   超时 25 秒;用户 AE 2025 的 Pref_SCRIPTING_FILE_NETWORK_SECURITY=0
            #   时必败,整条通道静默空转)。名字锚点改成「内存里真实存在的效果流」:
            #   LiveParams.walk() 用 FLT vtable needle 锚定,一遍就把所有实例拿到。
            merged, _counts = collect_layers(engine.ENGINE_DICT_DIR)
            table = {k: v["zh"] for k, v in merged.items()}
            with Process(pid, True) as p:
                # ★★ 2026-11-07 IG5 定案:要写的是**效果实例自己** +0x10 的 48
                #   字节 MBCS 效果名(「效果控件」面板标题读 GetEffectName →
                #   this+0x10)。注册表条目(vtable=flt+fc_vtable)只读不写,
                #   所以顶部「效果」菜单保持英文。
                nt = liveparams.EffectNameTable(p, table, data_root=engine.DATA_ROOT)
                res = nt.apply(sorted(table))
                disp = res.get("records") or []
                # EffectNameTable.apply 返回的是 records（每条 {obj,english,zh}），
                # 没有 applied 字典 —— 拿真写成功的英文原名去喂参数名槽通道。
                names = sorted({str(r.get("english") or "") for r in disp} - {""})
                # ★ 参数名的「实例名字槽」通道(ExtendScript fx.name 读它):
                #   只写「内存里真实出现的英文原名」,不去猜词典里的键。
                if names:
                    tab = liveparams.NameTable(p, table).apply(names)
                else:
                    tab = {"records": [], "written": 0, "candidates": 0}
            records = disp + (tab.get("records") or [])
            if records:
                liveparams.save_session(engine.DATA_ROOT, pid, records)
            res["written"] = len(disp) + int(tab.get("written") or 0)
            res["candidates"] = int(res.get("candidates") or 0) + int(tab.get("candidates") or 0)
            res["names"] = len(names)
            return res

        def done(res):
            written = int(res.get("written") or 0)
            restored = int(res.get("restored") or 0)
            if mode == "on":
                if written:
                    self.toast.emit("info", "已把 %d 个名字换成中文（AE 不用重启）" % written)
                elif allow_restart:
                    self._restart_ae_async(reason)
            else:
                if restored:
                    self.toast.emit("info", "已把 %d 个名字还原成英文（AE 不用重启）" % restored)
                elif allow_restart and not res.get("oldProcess"):
                    self._restart_ae_async(reason)

        self._run(work, done, _busy_text="正在实时切换名称…")

    def _restart_ae_async(self, reason: str = ""):
        """切换汉化后自动重启正在运行的 AE，让译名立刻生效。

        ★ 现在的定位是**兜底**：正常路径走 _liveNamesAsync 的就地改名（§14），
          只有一条名字都没写成（写 0 条）时才退回这里重启进程。
        ★ 为什么要重启：实测（2026-10-30）效果名与参数名是 AE 在「加载工程」时
          按当时的插件状态解析并缓存在进程里的，同会话里重开工程、重绘、选中图层
          都不会重新解析（docs/兼容性-AE2021-2023.md §12）—— 但名字槽本身可以在
          运行中精确改写（§14），所以重启不再是唯一手段。
        ★ 安全闸：工程有未保存改动（窗口标题带 *）时只提示、不动用户的工作。
        """
        try:
            from core.aelaunch import ae_running, restart_running_ae
        except Exception:
            return
        if self._aeRestarting or self._aeLaunching:
            return
        try:
            if not ae_running():
                return
        except Exception:
            return
        self._aeRestarting = True
        self.toast.emit("info", "正在重启 AE 让新译名生效…（约 15 秒，工程会自动重开）")

        def work():
            return restart_running_ae(timeout=90.0, verbose=False)

        def done(res):
            self._aeRestarting = False
            ok, msg = res if isinstance(res, tuple) else (bool(res), str(res))
            self.toast.emit("info" if ok else "warn", msg)

        self._run(work, done, _busy_text="正在重启 AE…")

    @Slot(result=str)
    def installFixer(self):
        ok, msg = engine.install_fixer(self._root)
        self.refreshEngine()
        self.toast.emit("info" if ok else "error", msg)
        return msg

    @Slot(result=str)
    def removeFixer(self):
        ok, msg = engine.remove_fixer(self._root)
        self.refreshEngine()
        self.toast.emit("info" if ok else "error", msg)
        return msg

    def _compat_items(self):
        """兼容性问题列表（compatIssues 的数据源,带 5 秒缓存）。"""
        from core.compat import check_plugin_dir

        now = time.time()
        cache = getattr(self, "_compatCache", None)
        if cache and (now - cache[0]) < 5.0:
            issues = cache[1]
        else:
            mc = os.path.join(
                os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                "Adobe", "Common", "Plug-ins", "7.0", "MediaCore")
            issues = check_plugin_dir(mc)
            self._compatCache = (now, issues)
        return [{
            "vendor": i.vendor,
            "path": i.path,
            "level": i.level,
            "reason": i.reason,
            "action": i.action,
            "disabled": i.disabled,
        } for i in issues]

    @Slot(result="QVariantList")
    def compatIssues(self):
        """兼容性问题列表（自动加载 + 手动按钮共用，静默返回）。

        ★ 2026-10-28：compatReport（手动版，空结果弹 toast）已删 ——
          同一份数据两个槽没有意义；「没发现问题」的反馈由界面在
          按钮旁显示一行提示，不再由后端弹 toast。
        """
        try:
            return self._compat_items()
        except Exception:
            return []

    @Slot(str, bool, result=str)
    def setPluginEnabled(self, vendor: str, enabled: bool):
        """停用/恢复一个需要授权的插件目录。"""
        try:
            from core.compat import check_plugin_dir, disable, enable

            mc = os.path.join(
                os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                "Adobe", "Common", "Plug-ins", "7.0", "MediaCore")
            for i in check_plugin_dir(mc):
                if i.vendor == vendor:
                    ok, msg = (enable(i) if enabled else disable(i))
                    if not ok and "管理员" in msg:
                        # 需要提权：用 runas 重新执行
                        return self._elevate_compat(vendor, enabled)
                    return msg
            return "找不到该厂商的插件"
        except Exception as exc:
            return str(exc)

    def _elevate_compat(self, vendor: str, enabled: bool) -> str:
        """需要管理员权限时，用 runas 重新执行。"""
        try:
            import ctypes

            py = sys.executable
            script = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "tools", "toggle_compat.py")
            args = '"%s" --vendor "%s" --%s' % (
                script, vendor, "enable" if enabled else "disable")
            r = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", py, args, os.path.dirname(script), 1)
            if r > 32:
                return "已发起提权请求，请在弹窗中确认"
            return "提权失败（错误码 %d）" % r
        except Exception as exc:
            return str(exc)

    # ---------------------------------------------------- 提取未汉化参数

    @Slot(result="QVariantMap")
    def captureMissingParams(self):
        """从引擎的实时采集里提取「见过但还没翻译」的参数名（补词面板）。"""
        res = self._capture_missing_core(fresh=True)
        note = res.get("note")
        if note:
            self.toast.emit(note[0], note[1])
        result = {"seen": res["seen"], "items": res["items"]}
        channels = self._capture_channel_status()
        if channels:
            result["channels"] = channels
        return result

    def _nudge_dump(self, gp, wait_s: float = 0.0) -> bool:
        """让守护「立刻」把采集落盘（DUMP_NOW.flag），不再等 60 秒周期。

        只按 60 秒周期写 all_param_names.json 会让刚展开的参数缺席。
        点击提取时请求立即写入当前采集结果。
        守护主循环 1 秒一拍，看到 <hook>/out/DUMP_NOW.flag 就强制 get_collected()
        落盘。这里丢标志；wait_s>0 时轮询文件 mtime 等它刷新（按钮点击可以等
        1~2 秒，自动刷新不等，避免每 4 秒卡 UI）。
        """
        out_dir = os.path.join(os.path.dirname(gp), "out")
        names = os.path.join(out_dir, "all_param_names.json")
        flag = os.path.join(out_dir, "DUMP_NOW.flag")
        try:
            before = os.path.getmtime(names) if os.path.exists(names) else 0.0
        except OSError:
            before = 0.0
        try:
            os.makedirs(out_dir, exist_ok=True)
            with open(flag, "w", encoding="utf-8") as f:
                f.write("1")
        except OSError:
            return False
        if wait_s <= 0:
            return False
        deadline = time.time() + wait_s
        while time.time() < deadline:
            time.sleep(0.08)
            try:
                if os.path.exists(names) and os.path.getmtime(names) != before:
                    time.sleep(0.05)   # 让 json.dump 写完再读
                    return True
            except OSError:
                continue
        return False

    @Slot(result="QVariantMap")
    def captureMissingParamsQuiet(self):
        """自动刷新用：同 captureMissingParams，但不弹 toast（否则每 4 秒一条）。"""
        res = self._capture_missing_core()
        result = {"seen": res["seen"], "items": res["items"]}
        channels = self._capture_channel_status()
        if channels:
            result["channels"] = channels
        return result

    def _capture_channel_status(self):
        """Only report explicit runtime/build evidence, not unseen labels."""
        status = engine.control_status() or {}
        if not status.get("control_ready"):
            return []
        result = []
        draw = status.get("draw_stats") or {}
        if not isinstance(draw, dict):
            draw = {}
        profile = draw.get("profile") or {}
        classic = (isinstance(profile, dict) and bool(profile.get("checkbox_sites"))
                   and draw.get("pre_hooked") is True and draw.get("post_hooked") is True
                   and draw.get("call_intact") is True)
        if draw.get("checkbox_error"):
            result.append("复选框：专用绘制路径未通过校验")
        elif draw.get("checkbox_observed") is not True:
            if not classic and draw.get("checkbox_widget") is not True:
                result.append("复选框：当前会话尚未验证专用绘制路径")
            elif draw.get("checkbox_observed") is False:
                result.append("复选框：专用路径已就绪，本次会话尚未观察到标签")
            else:
                result.append("复选框：运行时尚未提供标签观察状态")
        if status.get("draw_error"):
            result.append("参数名：引擎诊断未通过")
        if status.get("popup_error") or getattr(self, "_autoAdaptPopupError", ""):
            result.append("下拉值：部分绘制或写入路径未通过校验")
        return result

    def _capture_missing_core(self, fresh: bool = False):
        """采集缺词的纯计算部分(无 toast,供补词卡与语料对照共用)。

原 captureMissingParams 主体(2026-11-01 抽出:语料全量对无全量
语料的版本也要能用同一份对照)。

【从引擎的实时采集里提取「见过但还没翻译」的参数名】

        守护采集 AE 图层实例已展开的参数，并周期性写入 <hook>/out/all_param_names.json。
        本槽从快照中筛选条目：
            · 排除 AE 内部占位名（例如 Generic Slider，见 core/diag.py）
            · 排除效果和插件名称，聚焦参数名
            · 排除词典/引擎生效词典里已有译名的(引擎词库 ⊇ store,
              守护学习词与自带词库只有引擎侧知道)
          返回 {"seen": 采集总数, "items": [{"en": ...}, ...]}
        """
        import json as _json

        from core.diag import is_placeholder

        # 守护只在启动时清空快照。提取前确认 AE 仍在运行，避免读取上次会话的数据。
        #   沙盒/测试进程不拦(与 _live_auto_allowed 同一套三道锁)。
        if self._live_auto_allowed():
            try:
                from core.aelaunch import ae_running as _ae_running

                if not _ae_running():
                    return {"seen": 0, "items": [],
                            "note": ("warn", "AE 没在运行：补词清单来自引擎在 AE 里的实时采集，先启动 AE 并展开参数再提取")}
            except Exception:
                pass

        gp, why = engine.find_guard()
        if not gp:
            return {"seen": 0, "items": [],
                    "note": ("warn", why or "找不到引擎，请先在翻译/效果名页启动一次引擎")}
        # ★ 2026-11-02 即时落盘：守护只在 60 秒周期写采集，用户展开插件马上
        #   点提取时新参数还不在文件里。丢 DUMP_NOW.flag 让它下一拍（约 1 秒）
        #   强制落盘；只在用户点「提取」时等 1.6 秒，自动刷新那一路不丢标志
        #   （避免每 4 秒触发一次采集落盘、把守护日志刷满）。
        if fresh and bool(getattr(self._engine, "running", False)):
            try:
                # ★ 2026-11-02: 不再在 UI 线程里等 1.6 秒（守护要 1~2 秒才落盘，
                #   等不满就白卡）。丢 flag 立即返回，卡片由 4 秒刷新自动接上。
                self._nudge_dump(gp, 0.0)
            except Exception:
                pass
        path = os.path.join(os.path.dirname(gp), "out", "all_param_names.json")
        if not os.path.exists(path):
            return {"seen": 0, "items": [],
                    "note": ("warn", "还没有采集文件：启动引擎后，在 AE 里把插件加到图层并展开参数，再点提取（引擎会即时落盘）")}
        try:
            with open(path, encoding="utf-8") as f:
                names = _json.load(f)
        except Exception as exc:
            return {"seen": 0, "items": [],
                    "note": ("error", "读取采集文件失败：%s" % str(exc)[:80])}
        if not isinstance(names, list):
            names = list(names or [])
        resolved = self._store.resolve()
        # 缺词判定合并 store、守护词库和学习词。例如 Deblur → 去模糊 只有
        # 守护词库有，仅使用 store 会重复列出已汉化参数。
        from core.export import collect_layers as _collect_layers

        # ★ 跟随本模块 DATA_ROOT(生产=真实根,测试沙盒随补丁走);
        #   与 engine.ENGINE_DICT_DIR 同一布局(DATA_ROOT/engine_dict)
        _eng_dir = os.path.join(DATA_ROOT, "engine_dict")
        merged = _collect_merged_layers(_eng_dir)

        def _eng_has(k):
            e = merged.get(k)
            return bool(e and e.get("zh"))

        # 同形占位（zh == en）仍计为缺词。守护原样写回 PreBlur 和 PostBlur
        # 也会记录成功，按成功名单过滤会漏掉这些英文参数。
        same_form: set = set()
        for _k, _v in merged.items():
            if not isinstance(_k, str) or not _v or not _v.get("zh"):
                continue
            if _norm_name(_v.get("zh")) != _norm_name(_k):
                continue
            if _k in _KEEP_SAME or _SAME_FORM_KEEP_RE.match(_k):
                continue
            same_form.add(_k)


        # ★ 2026-11-02 DeepClear:「改名未生效」维度 —— 引擎词典有词 ≠ AE 里
        #   真改了名(探测期入队/补写漏掉的参数采集得到却永远英文, 用户连手动
        #   入口都没有)。守护现在把 ANSI 改名成功的词落盘 renamed_names.json;
        #   词典覆盖但不在改名名单里的 → 列为 stuck(排尾, 在 junk 之前)。
        renamed_set: set = set()
        # ★ 2026-11-02: 未生效原因表必须无条件先定义 —— renamed_names.json
        #   不存在（首次会话/守护刚重启）时下面的 try 不会执行到赋值。
        miss_why: dict = {}
        try:
            _rn_path = os.path.join(os.path.dirname(gp), "out",
                                    "renamed_names.json")
            if os.path.exists(_rn_path):
                with open(_rn_path, encoding="utf-8") as f:
                    _rn = _json.load(f)
                if isinstance(_rn, list):
                    # 采集侧已归一化。比较前也归一化成功名单，避免漏掉
                    # 带前导空格的 Sapphire 参数名，例如 " Phase RGB"。
                    renamed_set = {_norm_name(x) for x in _rn if isinstance(x, str)}
                # 从 rename_miss.json 读取未生效原因，区分 tooLong、entfail 和 flush-gone。
                try:
                    _mm_path = os.path.join(os.path.dirname(gp), "out",
                                           "rename_miss.json")
                    if os.path.exists(_mm_path):
                        with open(_mm_path, encoding="utf-8") as f:
                            _mm = _json.load(f)
                        for _x in (_mm or []):
                            if isinstance(_x, dict) and _x.get("en"):
                                miss_why[_norm_name(_x["en"])] = str(_x.get("why") or "")
                            elif isinstance(_x, str):
                                miss_why[_norm_name(_x)] = ""
                except Exception:
                    miss_why = {}
        except Exception:
            renamed_set = set()
        # ★ 效果名碰撞 → 排尾隔离,不硬排除:2026-10-25 要求效果名不进补词,
        #   但 2026-11-01 实测硬排除会误杀与效果同名的真参数
        #   (新插件的参数 "Deblur" 撞上 BCC 效果 "Deblur" 被吞)—— 排尾两边都不丢
        fx = {r.en for r in self._nameRows if getattr(r, "en", None)}
        items = []
        junk_items = []   # 内部标签/效果名碰撞:不丢弃,排到列表末尾
        stuck_items = []  # 2026-11-02: 词典有词但 AE 改名未生效,同样排尾不丢
        for n in names:
            if not isinstance(n, str):
                continue
            s = " ".join(n.split())
            if not s or is_placeholder(s):
                continue
            # 补词快照排除当前会话已改名的参数。原样写回的同形占位继续检查。
            if s in renamed_set and s not in same_form:
                continue
            if s in fx or _looks_like_effect_name(s):
                junk_items.append({"en": s, "kind": "param", "junk": True})
                continue
            if any("\u4e00" <= ch <= "\u9fff" for ch in s):
                # 引擎注册期换名会让采集到的名字也可能是中文 —— 已翻译,跳过
                continue
            if s in same_form:
                # 词典只有同形占位（PreBlur→PreBlur）：AE 里仍是英文，列缺词
                items.append({"en": s, "kind": "param", "same": True})
                continue
            rec = resolved.get(s)
            if rec is not None and rec.zh:
                # store 已有译名时也检查实际写入，例如 DeepClear 有译名却仍显示英文。
                if s not in renamed_set:
                    stuck_items.append({"en": s, "kind": "param",
                                        "stuck": True, "why": miss_why.get(s, "")})
                continue
            if _eng_has(s):
                if s not in renamed_set:
                    stuck_items.append({"en": s, "kind": "param",
                                        "stuck": True, "why": miss_why.get(s, "")})
                continue
            items.append({"en": s, "kind": "param"})
        # ---- 枚举缺词（下拉值）★ 2026-10-26 通用下拉 ----
        #   引擎把枚举通道没命中的词收集到 <hook>/out/enum_missing.json;
        #   词典里补上这些词(单词级)并重启引擎, 下拉值即可汉化
        #   (guard 已从 CN 加载应用侧枚举表)。
        try:
            import re as _re

            em_path = os.path.join(os.path.dirname(gp), "out", "enum_missing.json")
            if os.path.exists(em_path):
                with open(em_path, encoding="utf-8") as f:
                    misses = _json.load(f)
                taken = {it["en"] for it in items}
                for w in misses:
                    if not isinstance(w, str):
                        continue
                    s = " ".join(w.split())
                    if (not s or len(s) > 60 or not _re.search(r"[A-Za-z]", s)
                            or s in taken):
                        continue
                    if _is_junk_enum(s):
                        # ★ 2026-11-02: 纯数字/序号/数值范围没有可译内容，
                        #   直接丢弃（以前排尾成「下拉」行，用户看到一片噪音）。
                        continue
                    rec = resolved.get(s)
                    if s in same_form:
                        # 下拉值也只有同形占位：AE 里仍是英文
                        items.append({"en": s, "kind": "enum", "same": True})
                        continue
                    if rec is not None and rec.zh:
                        continue
                    if _eng_has(s):
                        continue
                    items.append({"en": s, "kind": "enum"})
        except Exception:
            pass
        items.extend(stuck_items)  # 排尾(junk 前):词典有但 AE 未生效 —— 重加效果/重启引擎可解
        items.extend(junk_items)   # 排尾不丢弃:新插件的合法同名值仍可手动翻译
        # Prefer typed paint evidence when available. Do not turn inventory
        # hints or a label never observed on screen into a failed translation.
        try:
            snapshot = self._read_capture_labels(os.path.dirname(path))
            typed = {}
            for row in snapshot:
                if not isinstance(row, dict) or row.get("kind") not in ("param", "checkbox", "enum"):
                    continue
                en = _norm_name(row.get("en")) if isinstance(row.get("en"), str) else ""
                if en and not is_placeholder(en) and not any("\u4e00" <= c <= "\u9fff" for c in en):
                    key = (en, row["kind"])
                    typed[key] = typed.get(key, False) or row.get("painted") is True
            if typed:
                by_name = {en for en, _ in typed}
                items = [it for it in items if it["en"] not in by_name or it.get("junk")]
                for (en, kind), painted in typed.items():
                    if painted or (kind == "enum" and _is_junk_enum(en)):
                        continue
                    rec = resolved.get(en)
                    has_translation = en not in same_form and (
                        _eng_has(en) or bool(rec is not None and rec.zh and _norm_name(rec.zh) != en))
                    items.append({"en": en, "kind": kind, "stuck": bool(has_translation),
                                  "same": en in same_form, "why": miss_why.get(en, "")})
        except (OSError, ValueError, TypeError):
            pass  # Older or restarting runtimes still supply the legacy list.
        for item in items:
            if item.get("junk") and item["en"] in fx:
                item["kind"] = "effect"
            item["status"] = "not_applied" if item.get("stuck") else ("unconfirmed" if item.get("junk") else "missing")
        return {"seen": len(names), "items": items}

    @staticmethod
    def _read_capture_labels(out_dir):
        import json
        with open(os.path.join(out_dir, "label_diagnostics.json"), encoding="utf-8") as handle:
            snapshot = json.load(handle)
        return snapshot.get("rows", []) if isinstance(snapshot, dict) else []
    @Slot()
    def aiFillMissing(self) -> None:
        """补词卡一键 AI 补译(2026-10-29):当前缺词清单直接交给 AI,
        译名自动记入词典并热换生效 —— 不再需要手抄英文去外部 AI。

        复用「翻译」页选择的服务商，支持 DeepSeek、自定义端点和本地 Ollama。
        """
        if self._busy:
            self.toast.emit("warn", "有任务正在进行，请等它完成")
            return
        cap = self.captureMissingParams()
        items = [it for it in (cap.get("items") or [])
                 if not it.get("junk") and not it.get("stuck")]
        if not items:
            self.toast.emit("info", "没有缺词 —— 词典已覆盖采集到的全部参数/下拉词")
            return
        names = [it["en"] for it in items]
        cfg = self._ai_config()
        problems = cfg.validate()
        if problems:
            self.toast.emit("warn", "配置不完整：" + problems[0])
            return

        tr = Translator(cfg, cache=self._aiCache, glossary=self._terms_for_ai())
        self._busy = True
        self.stateChanged.emit()

        def work():
            return tr.translate(names, plugin="补词")

        def done(res):
            self._busy = False
            self.stateChanged.emit()
            pairs = [{"en": e.en, "zh": e.used_zh or e.zh} for e in res.accepted_entries]
            n = self.commitCapturedWords(pairs) if pairs else 0
            rej = res.progress.rejected
            if res.errors:
                self.toast.emit("error", "AI 补译未完成：" + res.errors[0][:160])
                return
            if n:
                self.toast.emit("info",
                                "AI 补译 %d 条已记入并热换生效(拒绝 %d 条)"
                                % (n, rej))
            else:
                self.toast.emit("warn",
                                "AI 没有给出可用译名(拒绝 %d 条)—— 可手填后「记入」"
                                % rej)

        def fail(msg):
            self._busy = False
            self.stateChanged.emit()
            self.toast.emit("error", msg.splitlines()[0][:200])

        # 直接复用 _run 的失败通道:work 抛异常走 toast
        self._run(work, done, _busy_text="AI 补译中…")

    @Slot("QVariantList", result=int)
    def commitCapturedWords(self, pairs):
        """把提取面板里填好的译名批量记入词典（user_locked 层）。

        pairs: [{"en": "...", "zh": "..."}]，zh 为空的行跳过。返回写入条数。
        """
        n = 0
        try:
            for p in pairs:
                en = str((p or {}).get("en") or "").strip()
                zh = str((p or {}).get("zh") or "").strip()
                if not en or not zh:
                    continue
                self._store.upsert_user(en, zh, locked=True)
                n += 1
            if n:
                self._store.save(os.path.join(DATA_ROOT, "store.json"))
                self._hot_reload_notify()
                self.refreshDict()
                self._bump_dict()
                self.toast.emit(
                    "info", "已记入 %d 条译名;引擎在跑的话已自动热换生效,"
                            "否则下次「启动引擎」生效" % n)
        except Exception as exc:
            self.toast.emit("error", "记入词典失败：%s" % str(exc)[:80])
            return 0
        return n

    def _scoped_popup_file(self):
        return os.path.join(DATA_ROOT, "engine_dict", "scoped_popup.json")

    def _read_scoped_popup(self):
        try:
            with open(self._scoped_popup_file(), encoding="utf-8") as handle:
                value = json.load(handle)
        except FileNotFoundError:
            return {}
        return value if isinstance(value, dict) else {}

    # Exact identifiers and English menu text observed on guest AE 18.0.1.1.
    # Never use a menu index, a display name or the current layer to infer these keys.
    _verified_popup_keys = (
        ("S_Glow", "S_Glow-0530", "Union"),
        ("S_Mosaic", "S_Mosaic-0530", "Union"),
    )

    @Slot(result="QVariantList")
    def captureEffectOptions(self):
        return self.effectOptionState(False)["options"]

    @Slot(bool, result="QVariantMap")
    def effectOptionState(self, include_verified):
        from core.aelaunch import ae_running
        try:
            saved = self._read_scoped_popup()
        except (OSError, ValueError):
            return {"options": [], "message": "已保存的专属译名不可读。"}
        origins = {}
        message = "没有可读取的菜单采集记录。"
        gp, _ = engine.find_guard()
        if gp and (not self._live_auto_allowed() or ae_running()):
            path = os.path.join(os.path.dirname(gp), "out", "label_diagnostics.json")
            try:
                with open(path, encoding="utf-8") as handle:
                    snapshot = json.load(handle)
                scopes = snapshot["scopes"]
                if not isinstance(scopes, list):
                    raise ValueError("scopes")
                for row in scopes[:3000]:
                    if not isinstance(row, dict):
                        continue
                    key = tuple(row.get(k) for k in ("effect", "parameter", "en"))
                    if all(isinstance(v, str) and v and "\x1f" not in v for v in key):
                        origins[key] = "菜单采集"
                has_enum = any(isinstance(r, dict) and r.get("kind") == "enum"
                               for r in snapshot.get("rows", []))
                if origins:
                    message = "可选择采集到的效果、参数和下拉项。"
                elif has_enum:
                    message = "采集记录中有下拉项，尚无可确认的效果和参数归属。"
                else:
                    message = "采集记录中没有可确认的下拉项。"
            except FileNotFoundError:
                pass
            except (OSError, TypeError, ValueError, KeyError):
                message = "菜单采集记录不可读。"
        for key in saved:
            parts = tuple(key.split("\x1f"))
            if len(parts) == 3 and all(parts):
                origins.setdefault(parts, "已保存")
        if include_verified:
            for key in self._verified_popup_keys:
                origins.setdefault(key, "已核验预置")
        options = [{"effect": e, "parameter": p, "en": en,
                    "label": " / ".join((e, p, en)) + " · " + origin,
                    "zh": saved.get("\x1f".join((e, p, en)), "")}
                   for (e, p, en), origin in sorted(origins.items())]
        return {"options": options, "message": message}

    @Slot(str, str, str, str, result=bool)
    def saveEffectOption(self, effect, parameter, en, zh):
        import tempfile
        source = (effect, parameter, en)
        zh = zh.strip()
        if (not all(v and len(v) <= 256 and not any(c in v for c in "\x1f\x00\n\r") for v in source)
                or len(zh) > 120 or any(c in zh for c in "|\x1f\x00\n\r")):
            self.toast.emit("warn", "译名不能包含换行或竖线，且最多 120 字。")
            return False
        if source not in self._verified_popup_keys and not any(
                tuple(row[k] for k in ("effect", "parameter", "en")) == source
                for row in self.captureEffectOptions()):
            self.toast.emit("warn", "未能确认此选项所属参数；请在 AE 展开该下拉再提取。")
            return False
        path = self._scoped_popup_file()
        temp = None
        try:
            data = self._read_scoped_popup()
            key = "\x1f".join(source)
            if zh:
                if len(data) >= 3000 and key not in data:
                    raise ValueError("覆盖词条已达上限")
                data[key] = zh
            else:
                data.pop(key, None)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                    dir=os.path.dirname(path), prefix="scoped-popup-", delete=False) as handle:
                temp = handle.name
                json.dump(data, handle, ensure_ascii=False)
            os.replace(temp, path)
            temp = None
        except (OSError, ValueError) as exc:
            self.toast.emit("error", "保存按效果译名失败：" + str(exc)[:80])
            return False
        finally:
            if temp:
                try:
                    os.unlink(temp)
                except OSError:
                    pass
        self.toast.emit("info", "已保存到当前配置。菜单显示需在 AE 中核验。" if engine.profile_runtime_confirmed()
                        else "已保存到当前配置，尚未确认引擎读取。")
        return True

    @Slot("QVariantList", result=str)
    def copyCapturedEnglish(self, ens):
        """把提取列表里的英文参数名复制到剪贴板（一行一个，方便喂给 AI）。

        ★ 2026-10-25 修复：原来注册成无参 @Slot(result=str)，QML 传数组过来
          找不到匹配签名直接 TypeError —— 表现就是"点复制英文清单没反应"。
        """
        if not ens:
            self.toast.emit("warn", "列表是空的，先点「提取」")
            return ""
        text = "\n".join(str(e) for e in ens)
        QGuiApplication.clipboard().setText(text)
        self.toast.emit("info", "已复制 %d 条参数名到剪贴板" % len(ens))
        return text

    def _kill_ae_msg(self) -> str:
        """taskkill 结束 AfterFX.exe，返回结果消息。"""
        import subprocess

        try:
            r = subprocess.run(
                ["taskkill", "/IM", "AfterFX.exe", "/F", "/T"],
                capture_output=True, text=True, encoding="gbk", errors="replace",
                timeout=20)
            out = ((r.stdout or "") + (r.stderr or "")).strip()
            if r.returncode == 0:
                return "AE 已结束"
            if ("没有找到" in out or "找不到" in out
                    or "not found" in out.lower()):
                return "AE 本来没在运行"
            return "结束 AE 失败：%s" % out[:80]
        except Exception as exc:
            return "结束 AE 失败：%s" % str(exc)[:80]

    @Slot(result=str)
    def rescueStopAll(self):
        """★ 止血（AE 打不开时的第一按钮）：停引擎 + 强杀残留守护 + 结束 AE。

        2026-10-25 事故复盘：守护是常驻的，「重启引擎」不停旧的就起新的，
        多个 frida 代理同时注入同一个 AE → frida-agent.dll 崩溃 →
        AE 卡死在崩溃修复对话框 → 重开 AE 又被注入 → 死循环。
        唯一出口是把所有守护干掉。stop_engine 的 flag 机制在守护卡死时
        不可靠，所以这里补一层强杀（命令行含 guard_uni 的 python 进程）。
        需要管理员权限（守护是从管理员应用起的）——本应用本身是管理员时才有效。
        """
        from core import engine as _eng

        parts = []
        # 1. 优雅停（写 STOP_NOW.flag，守护自己卸载）
        try:
            ok, msg = _eng.stop_engine(timeout=8)
            parts.append("引擎%s" % ("已停止" if ok else "未响应(" + msg[:40] + ")"))
        except Exception as exc:
            parts.append("引擎停止异常：%s" % str(exc)[:50])
        # 2. 强杀残留守护
        try:
            n = _eng.force_kill_guards()
            parts.append("强杀守护 %d 个" % n if n else "无残留守护")
        except Exception as exc:
            parts.append("强杀守护失败：%s" % str(exc)[:50])
        # 3. 结束卡死的 AE
        parts.append(self._kill_ae_msg())
        # ★ 强杀路径也标记停止(2026-10-29):engine_running 立即回 False,
        #   否则按钮/状态假"运行中"90 秒
        from core import engine as _eng2

        _eng2.mark_stopped()
        self.refreshEngine()
        summary = "；".join(parts)
        self.toast.emit("info", summary)
        return summary

    @Slot(result=str)
    def launchAE(self):
        """启动 AE 并自动越过「崩溃修复选项」对话框（后台线程，不冻结界面）。

        ★ 2026-10-21：AE 崩溃后重启会卡在崩溃修复对话框，
          看起来像"AE 没启动"，实际只要点「继续」就行。
          这个接口自动点掉它，等主窗口可用后返回。
        2026-10-30：AE 冷启动实测超过 10 秒。同步调用 launch_ae 会让 QML
        主线程无法刷新。使用后台线程启动，主窗口就绪后再提示并检查适配。
        """
        try:
            from core.aelaunch import launch_ae

            exe = ""
            if self._root:
                exe = os.path.join(self._root, "Support Files", "AfterFX.exe")
            if self._aeLaunching:
                msg = "AE 正在启动中，请稍候…"
                self.toast.emit("info", msg)
                return msg
            self._aeLaunching = True
            self.toast.emit("info", "正在启动 AE…（AE 自身启动约 10 秒，界面可以继续用）")

            def work():
                return launch_ae(exe, timeout=60.0, verbose=False)

            def done(res):
                self._aeLaunching = False
                ok, msg = res if isinstance(res, tuple) else (bool(res), str(res))
                if ok:
                    self.toast.emit("info", "AE 已就绪（已自动处理崩溃修复对话框）")
                    # ★ 自动适配触发点(2026-10-28):AE 起来了就试着适配当前构建
                    self._auto_adapt_async(self._running_ae_version())
                else:
                    self.toast.emit("warn", msg or "AE 启动超时")

            def fail(msg):
                self._aeLaunching = False
                self.toast.emit("error", "启动 AE 失败：%s" % str(msg).splitlines()[0][:200])

            w = Worker(work)
            self._workers.append(w)
            w.done.connect(done)
            w.failed.connect(fail)
            w.finished.connect(lambda: self._workers.remove(w) if w in self._workers else None)
            w.start()
            return "正在启动 AE…"
        except Exception as exc:
            self._aeLaunching = False
            self.toast.emit("error", "启动 AE 失败：%s" % exc)
            return str(exc)

    @Slot(result=int)
    def dismissAEDialogs(self):
        """点掉当前所有挡路的 AE 对话框，返回处理数量。"""
        try:
            from core.aelaunch import dismiss_dialogs

            n = dismiss_dialogs(verbose=False)
            if n:
                self.toast.emit("info", "已点掉 %d 个 AE 对话框" % n)
            else:
                self.toast.emit("info", "当前没有 AE 对话框")
            return n
        except Exception as exc:
            self.toast.emit("error", "处理失败：%s" % exc)
            return 0

    @Slot(result=str)
    def openEngineLog(self):
        """用系统默认编辑器打开引擎日志(自检建议"看日志"时的入口)。"""
        p = engine.engine_log_path()
        if not os.path.exists(p):
            self.toast.emit("warn", "还没有引擎日志（先启动一次引擎）")
            return ""
        try:
            os.startfile(p)
        except Exception as exc:
            self.toast.emit("error", "打开失败：%s" % exc)
        return p

    @Slot(str, result=str)
    def describeEntry(self, key: str):
        r = self._store.lookup(key)
        if not r:
            return "未命中"
        world = self._pipl_world()
        return describe(key, r.zh, world)

    # ---------------------------------------------------------- AI 属性

    #: settings.json 里 AI 配置的键名（跟 ae_version 同一个文件）
    _AI_SETTING_KEYS = ("ai_provider", "ai_base_url", "ai_model")

    def _loadAiSettings(self) -> None:
        """启动时恢复上次选的服务商 / Base URL / 模型。

        ★ 读失败一律回退默认值，绝不能因为设置文件坏了起不来。
        """
        try:
            from core import settings as _settings

            obj = _settings.load(DATA_ROOT)
        except Exception:
            return
        try:
            prov = str(obj.get("ai_provider") or "")
            if prov in PRESETS:
                self._aiProvider = prov
            self._aiBaseUrl = str(obj.get("ai_base_url") or "")
            self._aiModel = str(obj.get("ai_model") or "")
            # 预算/批次(2026-10-28):存了就用存的,范围闸在 setter 里
            try:
                self._aiBudget = float(obj.get("ai_budget") or self._aiBudget)
            except (TypeError, ValueError):
                pass
            try:
                self._aiBatch = int(obj.get("ai_batch") or self._aiBatch)
            except (TypeError, ValueError):
                pass
        except Exception:
            pass

    def _saveAiSettings(self) -> None:
        """把服务商 / Base URL / 模型 / 预算 / 批次写回 settings.json（一次原子写）。"""
        try:
            from core import settings as _settings

            obj = _settings.load(DATA_ROOT)
            obj["ai_provider"] = self._aiProvider
            obj["ai_base_url"] = self._aiBaseUrl
            obj["ai_model"] = self._aiModel
            obj["ai_budget"] = self._aiBudget
            obj["ai_batch"] = self._aiBatch
            _settings.save(DATA_ROOT, obj)
        except Exception:
            pass  # 设置保存失败不影响本次运行

    @Property("QVariantList", notify=stateChanged)
    def aiProviders(self):
        return [{"key": k, "label": p.label, "model": p.default_model,
                 "baseUrl": p.base_url, "needsKey": p.needs_key}
                for k, p in PRESETS.items()]

    @Property(str, notify=stateChanged)
    def aiProvider(self):
        return self._aiProvider

    @Slot(str)
    def setAiProvider(self, key: str):
        self._aiProvider = key
        p = PRESETS.get(key)
        if p:
            self._aiBaseUrl = p.base_url
            self._aiModel = p.default_model
        self._saveAiSettings()
        self.stateChanged.emit()

    @Property(str, notify=stateChanged)
    def aiBaseUrl(self):
        return self._aiBaseUrl or (PRESETS.get(self._aiProvider).base_url
                                   if self._aiProvider in PRESETS else "")

    @Slot(str)
    def setAiBaseUrl(self, v: str):
        self._aiBaseUrl = v
        self._saveAiSettings()
        self.stateChanged.emit()

    @Property(str, notify=stateChanged)
    def aiModel(self):
        return self._aiModel or (PRESETS.get(self._aiProvider).default_model
                                 if self._aiProvider in PRESETS else "")

    @Slot(str)
    def setAiModel(self, v: str):
        self._aiModel = v
        self._saveAiSettings()
        self.stateChanged.emit()

    # ---- 预算 / 每批条数（2026-10-28 起可调，不再写死 $0.50 / 25 条）----

    @Property(float, notify=stateChanged)
    def aiBudget(self):
        return self._aiBudget

    @Slot(float)
    def setAiBudget(self, v: float):
        """预算上限（美元）。范围 0.05–20，超出部分夹住不报错。"""
        try:
            v = float(v)
        except (TypeError, ValueError):
            return
        if v != v:          # NaN(QML 传空串 parseFloat 出来的)
            return
        v = max(0.05, min(20.0, v))
        if abs(v - self._aiBudget) < 1e-9:
            return
        self._aiBudget = v
        self._saveAiSettings()
        self.stateChanged.emit()

    @Property(int, notify=stateChanged)
    def aiBatch(self):
        return self._aiBatch

    @Slot(int)
    def setAiBatch(self, v: int):
        """每批发给模型的名字条数。范围 5–100;太大易漏条目（历史教训:50）。"""
        try:
            v = int(v)
        except (TypeError, ValueError):
            return
        v = max(5, min(100, v))
        if v == self._aiBatch:
            return
        self._aiBatch = v
        self._saveAiSettings()
        self.stateChanged.emit()

    @Property(str, notify=stateChanged)
    def aiKeyMasked(self):
        return mask_key(self._aiKey) if self._aiKey else ""

    @Property(bool, notify=stateChanged)
    def aiHasKey(self):
        return bool(self._aiKey)

    @Property(bool, notify=stateChanged)
    def aiKeySaved(self):
        """Key 是否已加密保存到磁盘（不是只在内存里）。"""
        return self._aiKeySaved

    @Slot(str)
    def setAiKey(self, v: str):
        """设置 Key，并【加密】保存到磁盘（DPAPI，绝不明文）。

        ★ 2026-10-22 需求：Key 要能保存住，但绝不能明文落盘。
          用 Windows DPAPI：密钥由系统托管、绑定当前用户，
          拷到别的机器/别的账户都解不开，且程序里不存任何主密钥。

        保留已保存的密钥和上一份可用密文：
          · 空串只清内存，防止输入框失焦时覆盖磁盘上的密钥。
          · 更换密钥时备份旧密文，支持恢复。
          · 显示保存成功或失败。
        """
        v = (v or "").strip()
        if not v:
            # 只影响本次运行，磁盘上的密钥保持不动
            self._aiKey = ""
            self._aiKeySaved = False
            self.stateChanged.emit()
            return
        old = self._aiKey
        self._aiKey = v
        try:
            from core import secrets

            ok, msg = secrets.save_key(v, secrets.default_path(DATA_ROOT))
            self._aiKeySaved = bool(ok)
            if ok:
                if old and old != v:
                    self.toast.emit("info", "API Key 已更换并备份旧 Key（可一键恢复）")
                else:
                    self.toast.emit("info", "API Key 已加密保存（DPAPI）")
            else:
                # 注意：msg 里绝不含 Key 原文
                self.toast.emit("warn", "Key 已用于本次运行，但保存失败：%s" % msg)
        except Exception as exc:
            self._aiKeySaved = False
            self.toast.emit("warn", "Key 已用于本次运行，但保存失败：%s"
                            % str(exc)[:60])
        self.stateChanged.emit()

    @Slot(result=str)
    def clearAiKey(self):
        """清除已保存的 Key（同时清掉内存里和备份里的）。"""
        try:
            from core import secrets

            secrets.clear_key(secrets.default_path(DATA_ROOT))
        except Exception:
            pass
        self._aiKey = ""
        self._aiKeySaved = False
        self.stateChanged.emit()
        self.toast.emit("info", "已清除保存的 API Key")
        return "已清除"

    @Property(bool, notify=stateChanged)
    def aiKeyCanRestore(self):
        """是否还有「上一个 Key」的加密备份可恢复。

        ★ 注意：这里【不能】加「当前没 Key 才显示」的条件。
          被别的程序/测试覆盖时，当前 Key 是存在的（只是错的），
          那正是最需要恢复按钮的时候。
        """
        try:
            from core import secrets

            return secrets.has_previous(secrets.default_path(DATA_ROOT))
        except Exception:
            return False

    @Slot(result=str)
    def restoreAiKey(self):
        """把上一个 Key 的加密备份恢复成当前 Key。"""
        try:
            from core import secrets

            k = secrets.restore_previous(secrets.default_path(DATA_ROOT))
        except Exception:
            k = ""
        if not k:
            return "没有可恢复的旧 Key"
        self._aiKey = k
        self._aiKeySaved = True
        self.stateChanged.emit()
        self.toast.emit("info", "已恢复上一个 API Key（%s）" % mask_key(k))
        return "已恢复"

    @Property(int, notify=aiProgressChanged)
    def aiProgress(self):
        return self._aiProgress

    @Property(str, notify=aiProgressChanged)
    def aiProgressText(self):
        return self._aiProgressText

    @Property(str, notify=aiProgressChanged)
    def aiCostText(self):
        return "$%.4f" % self._aiCost

    @Property(str, notify=aiDraftChanged)
    def aiDraftSummary(self):
        return self._aiDraftSummary

    @Property(bool, notify=aiDraftChanged)
    def aiDraftStale(self):
        """草稿是否已过期（勾选变了但草稿还是上次的）。

        ★ 2026-10-22 实测问题：
          用户勾选 DLSS 两组，切到 AI 翻译页却看到 Sapphire 的 S_Wipe* 一堆条目。
          取词逻辑其实是对的（只取勾选组），但 _aiDraft 从来不清空，
          所以显示的是【上一次翻译留下的旧草稿】，很容易误解成"勾选没生效"。
        """
        return self._aiDraftStale

    @Property("QVariantList", notify=aiDraftChanged)
    def aiDraft(self):
        return self._aiDraft[:300]

    @Property(int, notify=aiDraftChanged)
    def aiDraftCount(self):
        return len(self._aiDraft)

    def _terms_for_ai(self) -> Dict[str, Dict[str, object]]:
        """把词典里已有的译文整理成 AI 术语表。

        ★ 2026-10-23：之前 Translator 创建时压根没传 glossary，
          AI 完全不知道我们已有的译法，同一个词在不同插件里译得五花八门
          （"Swish Pan" 有"快速摇摄"也有"嗖嗖摇摄"）。

          传进去之后，build_messages -> _pick_glossary 会按本批名字
          做精确 + 词级匹配，只摘相关的几十条，不会撑爆 prompt。

        只收「短而有术语价值」的条目：
          · 译文非空
          · 原文不超过 4 个词（长句子不适合做术语）
          · 译文不超过 12 字
        """
        try:
            res = self._store.resolve()
        except Exception:
            return {}
        out: Dict[str, Dict[str, object]] = {}
        for k, r in res.items():
            zh = (r.zh or "").strip()
            if not zh or len(zh) > 12:
                continue
            en = (k or "").strip()
            if not en or len(en.split()) > 4:
                continue
            # 译文跟原文一样的没术语价值
            if zh.lower() == en.lower():
                continue
            out[en] = {"zh": zh}
        return out

    @Property(QObject, constant=True)
    def panelLocalization(self):
        return self._panelLocalization

    def _ai_config(self) -> AIConfig:
        return AIConfig(
            provider=self._aiProvider,
            base_url=self._aiBaseUrl,
            model=self._aiModel,
            api_key=self._aiKey,
            batch_size=self._aiBatch,
            budget_usd=self._aiBudget,
        )

    @Slot()
    def testAiConnection(self):
        if self._busy:
            self.toast.emit("warn", "有任务正在进行，请等它完成")
            return
        cfg = self._ai_config()
        problems = cfg.validate()
        if problems:
            self.toast.emit("warn", "配置不完整：" + problems[0])
            return

        def probe():
            client = ChatClient(cfg, timeout=30)
            return client.chat([
                {"role": "system", "content": "只回一个词：OK"},
                {"role": "user", "content": "ping"},
            ], max_tokens=8)

        def done(res):
            self.toast.emit("info", "连接成功 · %s · %.1fs" % (res.model or cfg.model, res.elapsed))

        self._run(probe, done, _busy_text="正在测试连接…")

    @Slot()
    def startTranslate(self):
        if self._busy:
            # ★ 并发防护:翻译要花钱,跑着的时候再点会排队烧两份预算
            self.toast.emit("warn", "有任务正在进行，请等它完成")
            return
        if not self._inv:
            self.toast.emit("warn", "请先扫描插件")
            return
        cfg = self._ai_config()
        problems = cfg.validate()
        if problems:
            self.toast.emit("warn", "配置不完整：" + problems[0])
            return

        # ★ 开始新翻译前清掉旧草稿，避免用户以为「勾选没生效」
        self._aiDraft = []
        self._aiDraftStale = False
        self._aiDraftSummary = ""
        self.aiDraftChanged.emit()

        # ★★ 收集 + 过滤必须放【后台线程】（2026-10-22 卡死事故）
        #   实测：60 组全选 = 1037 个文件 / 848MB，
        #   参数名归属过滤在主线程里要跑 15 秒以上 -> 界面「Python 未响应」。
        #   现在主线程只做配置校验和清草稿，重的活全进 work()。
        resolved = self._store.resolve()
        inv = self._inv
        sel_groups = list(inv.selected())

        def collect_names():
            """后台：收集待译名（效果名 + 属于勾选插件的参数名）。"""
            out: List[str] = []

            def _already_zh(s: str) -> bool:
                return any("\u4e00" <= ch <= "\u9fff" for ch in s)

            for g in sel_groups:
                for f in g.files:
                    dn = f.display_name.strip()
                    if not f.name_editable or not dn:
                        continue
                    if dn in resolved or _already_zh(dn):
                        continue
                    out.append(dn)
            n_effect = len(set(out))

            n_param = 0
            n_other = 0
            try:
                from core import engine as _eng
                from core import paramfilter

                cand = [p for p in _eng.seen_param_names() if p not in resolved]
                if cand:
                    hit, miss, _trunc = paramfilter.filter_for_groups(
                        sel_groups, cand)
                    for pname in sorted(hit):
                        out.append(pname)
                    n_param = len(hit)
                    n_other = len(miss)
            except Exception:
                pass

            return [x for x in dict.fromkeys(out) if x], n_effect, n_param, n_other

        # 向 Translator 传入词典术语。缺少词表时，模型曾将表示甩镜或扫掠的
        # Swish 译成拟声词。_pick_glossary 通过整名和词级匹配只选本批相关术语。
        tr = Translator(cfg, cache=self._aiCache, glossary=self._terms_for_ai())
        self._translator = tr
        self._aiProgress = 0
        self._aiCost = 0.0
        self._aiProgressText = "正在统计待译条目…"
        self.aiProgressChanged.emit()

        def work():
            names, n_effect, n_param, n_other = collect_names()
            if not names:
                return None
            if n_param or n_other:
                txt = "待译：效果名 %d 条 + 参数名 %d 条（属于勾选的插件）" % (
                    n_effect, n_param)
                if n_other:
                    txt += "；另有 %d 条属于未勾选的插件，已跳过" % n_other
                self.toast.emit("info", txt)
            self._aiProgressText = "准备翻译 %d 条…" % len(names)
            self.aiProgressChanged.emit()

            def on_prog(p):
                self._aiProgress = p.percent
                self._aiCost = p.cost_usd
                self._aiProgressText = "%d/%d · %s" % (p.done, p.total, p.message[:40])
                self.aiProgressChanged.emit()

            return tr.translate(names, on_progress=on_prog)

        def done(res):
            if res is None:
                # collect_names() 返回空 -> 没有需要翻的
                self._aiProgress = 0
                self._aiProgressText = "没有需要翻译的条目"
                self.aiProgressChanged.emit()
                self.toast.emit("info", "勾选的插件都已命中词典，无需翻译")
                return
            # 只收 accepted_entries 会遗漏被校验拒绝的条目。
            # 草稿保留通过、拒绝和未译的条目，供查看原因和手动补译。
            draft = []
            for e in res.accepted_entries:
                draft.append({
                    "en": e.en,
                    "zh": e.used_zh or e.zh,
                    "status": e.status,
                    "reason": e.reason or "",
                })
            for rep in res.reports:
                for e in rep.entries:
                    if e.accepted:
                        continue
                    draft.append({
                        "en": e.en,
                        "zh": "",
                        "status": e.status,
                        "reason": e.reason or ("未翻译" if e.status == "untranslated" else ""),
                    })
            # 同一英文只留一条（优先保留有译文的）
            seen = {}
            for d in draft:
                k = d["en"]
                if k not in seen or (not seen[k]["zh"] and d["zh"]):
                    seen[k] = d
            draft = list(seen.values())
            self._aiDraft = draft
            acc = res.progress.accepted
            rej = res.progress.rejected
            self._aiDraftSummary = (
                "通过 %d · 拒绝 %d · 缓存命中 %d · 花费 $%.4f"
                % (acc, rej, res.cache_hits, res.progress.cost_usd)
            )

            # 请求全部失败时，草稿为空且统计为零。展示 res.errors，
            # 让全量请求失败能和无需翻译的结果区分。
            if res.errors:
                n_err = len(res.errors)
                self._aiDraftSummary += " · ⚠ %d 批失败" % n_err
                self._aiProgressText = "部分批次失败（%d 条已出稿）" % len(draft)
                self.aiDraftChanged.emit()
                self.aiProgressChanged.emit()
                self.toast.emit("info", self._aiDraftSummary)
                # 把真实原因直接抛给用户（认证/余额/网络都能一眼看出）
                self.toast.emit("error", "翻译未完成：" + res.errors[0][:160])
                return

            if not draft:
                self._aiProgress = 100
                self._aiProgressText = "没有产出"
                self.aiDraftChanged.emit()
                self.aiProgressChanged.emit()
                self.toast.emit("warn",
                                "API 没有返回可用译文（未产生任何草稿），"
                                "请检查 API Key / 余额 / 模型名")
                return

            self._aiProgress = 100
            self._aiProgressText = "完成"
            self.aiDraftChanged.emit()
            self.aiProgressChanged.emit()
            self.toast.emit("info", self._aiDraftSummary)

        def fail(msg):
            self._aiProgress = 0
            self._aiProgressText = "失败"
            self.aiProgressChanged.emit()
            self.toast.emit("error", msg.splitlines()[0][:200])

        self._busy = True
        self._busyText = "正在翻译…"
        self.stateChanged.emit()
        w = Worker(work)
        self._workers.append(w)

        def wrap(res):
            self._busy = False
            self._busyText = ""
            self.stateChanged.emit()
            done(res)

        def wrap_fail(msg):
            self._busy = False
            self._busyText = ""
            self.stateChanged.emit()
            fail(msg)

        w.done.connect(wrap)
        w.failed.connect(wrap_fail)
        w.start()

    @Slot()
    def cancelTranslate(self):
        if self._translator:
            self._translator.cancel()
            self.toast.emit("warn", "已请求取消")
        else:
            self.toast.emit("info", "当前没有正在进行的翻译")

    @Slot()
    def acceptDraft(self):
        if not self._aiDraft:
            self.toast.emit("warn", "没有待接受的草稿")
            return
        n = 0
        for d in self._aiDraft:
            if d.get("zh"):
                self._store.upsert_user(d["en"], d["zh"])
                n += 1
        self._store.save(os.path.join(DATA_ROOT, "store.json"))
        self._hot_reload_notify()
        self._bump_dict()
        self._aiDraft = []
        self._aiDraftSummary = "已接受 %d 条到我的词典" % n
        self.aiDraftChanged.emit()
        self.stateChanged.emit()
        self.toast.emit("info", self._aiDraftSummary)

    @Slot()
    def discardDraft(self):
        if not self._aiDraft:
            self.toast.emit("warn", "没有待丢弃的草稿")
            return
        n = len(self._aiDraft)
        self._aiDraft = []
        self._aiDraftSummary = "已丢弃 %d 条草稿" % n
        self.aiDraftChanged.emit()
        self.toast.emit("info", self._aiDraftSummary)

    @Slot(int, str)
    def setDraftZh(self, row: int, zh: str):
        """草稿区逐条手填/修改译文（2026-10-28）。

        被拒/未译的条目就地改完,点「全部接受」时一起入典 ——
        之前 UI 文案说"手填后也能一起接受"但草稿行是只读的,名不副实。
        """
        if row < 0 or row >= len(self._aiDraft):
            return
        zh = (zh or "").strip()
        if self._aiDraft[row].get("zh") == zh:
            return
        self._aiDraft[row]["zh"] = zh
        # 顺手改写摘要里没有别的状态要动;只刷新草稿列表
        self.aiDraftChanged.emit()

    # ============================================================
    # 实时写入(2026-10-27,A/B/C 三方案):
    #   效果名运行时写 core/livenames.py · 面板消息刷新 core/palette.py
    #   · 参数全量枚举 core/paramcatalog.py · 版本适配 core/adapter.py
    # 布局与安全配方移植自 After-Effects-Plugin-Localizer(MIT)。
    # ============================================================

    catalogChanged = Signal()
    _catalogItems: list = []
    _catalogSeen: int = 0

    # ★ 2026-11-06:core/livenames.py(NameSession,写 FLT_FCSpec+0x10)已从
    #   效果名通道链路彻底摘掉 —— 那个字段是「效果」菜单的数据源(IG5:
    #   FLT_FCSpec::GetEffectName_MB @0x180066b90 = return this+0x10)。
    #   现在只保留「只读快照」用它的 Registry,以及写参数名实例槽的
    #   liveparams.NameTable。

    def _live_mapping(self) -> dict:
        """{英文: 中文} 词典映射 —— 按 store.revision 缓存,每拍不重算。

        ★ build_effect_mapping 每次都要 resolve() 两万多条 + 读一遍
          data/effect_names.json(实测几十毫秒)。0.4 秒一拍绝不能每拍调;
          revision 是 DictStore 的层内容版本号,任何改词操作都会 +1。
        """
        rev = getattr(self._store, "revision", 0)
        cache = getattr(self, "_liveMapCache", None)
        if cache is not None and cache[0] == rev:
            return cache[1]
        from core.workflow import build_effect_mapping

        mapping = build_effect_mapping(self._store)
        self._liveMapCache = (rev, mapping)
        return mapping

    def _live_real_names(self, pid: int) -> set:
        """AE 注册表里真实存在的完整显示名（只读，按 pid 缓存）。

        ★ 给 JS lookup 区分「重复实例后缀」与「本身就叫 BCC Noise Map 2」：
          前者 REAL 里没有，才允许拆后缀；后者 REAL 里有，绝不拆。
        """
        now = time.monotonic()
        cache = getattr(self, "_liveRealNamesCache", None)
        if cache is not None and cache[0] == int(pid):
            # 启动期注册表会继续增长；不能把首次非空（可能只有内置项）
            # 永久冻结。稳态 2s 才重读一次，避免每拍全目录解析。
            if now - float(cache[1]) < 2.0:
                return set(cache[2])
        names = set()
        try:
            from core import livenames
            from core.proc import Process

            with Process(int(pid), False) as p:
                reg = livenames.Registry(p, data_root=DATA_ROOT)
                for r in reg.records():
                    disp = (r.get("displayName") or "").strip()
                    if disp:
                        names.add(disp)
        except Exception:
            # 临时 Registry 失败不能把已确认的有效名单清空推给 JS。
            if cache is not None and cache[0] == int(pid) and cache[2]:
                return set(cache[2])
            names = set()
        self._liveRealNamesCache = (int(pid), now, frozenset(names))
        return names

    def _live_ui_title_ret(self, pid: int) -> int:
        """仅 25.5.0.4 已知的 Effect Controls 标题绘制 callsite。

        其它版本返回 0，JS 不做标题绘制替换；显示缓存链仍然有效，不会把
        一个版本的 RVA 盲目用到所有 AE。
        """
        try:
            from core import proc as _proc

            for p in _proc.list_ae_processes():
                if int(p.get("pid") or 0) == int(pid):
                    ver = str(p.get("fileVersion") or "")
                    return 0xA90C3F if ver.startswith("25.5.0.4") else 0
        except Exception:
            pass
        return 0

    def _live_sync_real_names(self, hook, pid: int) -> bool:
        """注册表启动期增长时把新 REAL 推给已挂 JS，不重挂会话。

        ★ 只有 setreal 真正成功后才提交指纹；失败/异常必须下拍重试，
          否则首次空目录会把 JS 的 REAL 永久冻结在空集。
        """
        real = self._live_real_names(int(pid))
        sig = frozenset(real)
        if sig == getattr(self, "_liveRealNamesSig", None):
            return False
        try:
            ok = bool(hook.set_real_names(real))
        except Exception:
            ok = False
        if not ok:
            return False
        self._liveRealNamesSig = sig
        return True

    def _live_plugin_names(self, allowed: set) -> list:
        """勾了「效果名」通道的插件 → 磁盘上的英文显示名(PiPL)。

        ★ 为什么不用运行时注册表快照当锚点:那一份读出来的是**内存此刻的
          显示名**,写完中文之后它就变中文了 —— 下一拍再拿去查词典必然
          查不到,整条通道从此认不出自己改过什么(旧版就是这样把同一个
          名字反复重写几百遍的)。PiPL 显示名在 .aex 里恒为英文,读一次
          缓存住就永远是对的。
        ★ 纯内存读取(库存已在 self._inv 里),毫秒级,不碰 AE 进程。
        """
        names = []
        inv = self._inv
        if not inv:
            return names
        for g in inv.selected():
            if CH_EFFECT not in g.channels:
                continue
            for f in g.files:
                if not f.name_editable or not f.match_name:
                    continue
                if allowed and f.match_name not in allowed:
                    continue
                disp = (f.display_name or "").strip()
                if disp:
                    names.append(disp)
        return names

    def _live_one_ae(self):
        """恰好一个运行中的 AE → 它的进程信息字典;否则 None。"""
        try:
            from core import proc as _proc

            procs = _proc.list_ae_hosts()
        except Exception:
            return None
        if not procs:
            return None
        if len(procs) > 1:
            return None
        return procs[0]

    def _live_allowed_matchnames(self) -> set:
        """勾了「效果名」通道且可写的插件 → matchName 集合(protect 黑名单排除)。"""
        from core.protect import blocked

        allowed = set()
        if self._inv:
            for g in self._inv.selected():
                if CH_EFFECT not in g.channels:
                    continue
                for f in g.files:
                    if not f.name_editable or not f.match_name:
                        continue
                    try:
                        if f.path and blocked(f.path):
                            continue
                    except Exception:
                        pass
                    allowed.add(f.match_name)
        return allowed

    # ---------------------------------------------------------- 效果名常驻补写

    def _live_auto_allowed(self) -> bool:
        """常驻自动行为(效果名补写/启动预扫)的沙盒闸。

        ★ 三道锁：环境变量(AELOC_DATA_ROOT)是文档契约；DATA_ROOT 被换成
          沙盒目录是 tests/test_backend.py 的实际做法(直接改模块属性)；
          2026-11-02 起测试进程在 DATA_ROOT 解析处就被强制沙盒(见
          _resolve_data_root),此时 DATA_ROOT == _DEFAULT_DATA_ROOT 恒真,
          所以再加第三道 —— unittest 进程一律不许启动定时器。
          测试进程绝不能碰用户正开着的 AE。
        """
        return (not os.environ.get("AELOC_DATA_ROOT")
                and DATA_ROOT == _DEFAULT_DATA_ROOT
                and "unittest" not in sys.modules)

    def _auto_scan_async(self) -> None:
        """启动后静默预扫一次（2026-11-01）：不弹 toast，只为让三通道开关与
        效果名自动补写在用户没点过「扫描」时就绪（channelState 依赖 _inv）。
        用户已手动扫描（或正在扫）就以手动结果为准。"""
        if self._inv or not self._root:
            return
        from core.inventory import build_inventory

        def work():
            return build_inventory(self._root, self._version)

        def done(inv):
            if self._inv or not inv:
                return
            self._inv = inv
            self._restore_plugin_selection(inv)
            self._normalize_language_channels()
            self._apply_lang_intent()
            self._status = "已就绪：%d 组 / %d 文件（启动自动扫描）" % (
                len(inv.groups), inv.scanned_files)
            self._syncGroupModel(reset=True)
            self.inventoryChanged.emit()
            self.stateChanged.emit()
            QTimer.singleShot(800, self._auto_restore_engine_async)

        self._run(work, done, _busy_text="正在准备插件清单…")

    def _poll_live_watch(self) -> None:
        """Refresh the host list off the GUI thread while keeping the existing watch cadence."""
        if self._liveShutdown or getattr(self, "_windowResizing", False):
            return
        now = time.monotonic()
        probe = self._liveHostProbe
        if now >= self._liveHostProbeAt and (probe is None or not probe.is_alive()):
            self._liveHostProbeAt = now + 0.25

            def read_hosts():
                try:
                    from core import proc

                    self._liveHostSnapshot = (now + 0.75, proc.list_ae_hosts())
                except Exception:
                    self._liveHostSnapshot = None

            self._liveHostProbe = threading.Thread(target=read_hosts, name="aeloc-host-probe", daemon=True)
            try:
                self._liveHostProbe.start()
            except RuntimeError:
                self._liveHostProbe = None
        snapshot = self._liveHostSnapshot
        if snapshot is not None and now < snapshot[0]:
            self._liveWatchTick(snapshot[1])

    def _liveWatchTick(self, procs=None) -> None:
        """效果名通道的常驻换名（0.4 秒一拍，只在真实环境启动）。

        语义：开关开着 = 持续生效。每一拍都把「内存里现在有哪些效果实例」与
        词典对照一遍，把「效果控件」面板标题就地改成中文。
        ★★ 2026-11-06 起不再写 FLT_FCSpec 注册表（顶部「效果」菜单因此保持
        英文），也**不再「本会话已写过就永久跳过」**：注册表不写之后，新建的
        实例出生时是英文标题，必须靠每一拍就地换名 —— 上一版的一次性记账
        会让之后新加的每个效果永远停在英文名上。
        ★ 稳态是毫秒级、且**不靠猜**：liveparams 读 parade 对象自己的效果流
        向量（那才是工程里现存效果的权威清单），每个 parade 两次跨进程小读
        就能枚举全部效果流 —— 不读 1.5GB，也不写任何字节。新加的效果直接
        出现在这个向量里，下一拍（0.4 秒）就换名；只有「新图层/新合成」这种
        新建 parade 的情况才需要一次私有堆增量差分。
        ★ 失败下一拍重试并至少出声一次（自动路径绝不重启用户的 AE）。
        """
        if getattr(self, "_windowResizing", False):
            return
        # ★ 这里在 Qt 主线程：应用后台线程写下的频率意图，绝不在 worker
        #   里直接 setInterval（Qt 会报 cross-thread 并停掉定时器）。
        try:
            _iv = int(getattr(self, "_liveWatchWantInterval", 60))
            if self._liveWatch.interval() != _iv:
                self._liveWatch.setInterval(_iv)
        except Exception:
            pass
        # ★ 诊断（每 5 秒一行）：确认看护拍到底有没有被调度、卡在哪一面。
        try:
            _td = time.monotonic()
            if _td - getattr(self, "_liveTickDiagAt", 0.0) >= 5.0:
                self._liveTickDiagAt = _td
                with open(os.path.join(DATA_ROOT, "tick_diag.log"), "a",
                          encoding="utf-8") as _f:
                    _f.write("%s tick busy=%s eng=%s inv=%s inflight=%s want=%r\n"
                             % (time.strftime("%H:%M:%S"), bool(self._busy),
                                bool(self._engineBusy), bool(self._inv),
                                bool(getattr(self, "_liveAutoInflight", False)),
                                getattr(self, "_effWant", None)))
        except Exception:
            pass
        if not self._live_auto_allowed():
            return
        # 语言控制面独立于 effect：即使 effect OFF，也要检测 AE 新 host 并
        # 发送最后 language intent；同 host 不递增 gen。
        if time.monotonic() - getattr(self, "_lastLangCheckAt", 0.0) >= 2.0:
            self._lastLangCheckAt = time.monotonic()
            try:
                self._send_language_control(procs)
                if any(self.channelState()[:2]):
                    self._auto_adapt_async(self._running_ae_version(procs), hosts=procs)
            except Exception:
                pass
            self.engineSessionChanged.emit()
        if self._busy or self._engineBusy or not self._inv:
            # 固定字符串会被去重。记录当前等待的条件和时间。
            _why = ("busy" if self._busy else
                    "engine" if self._engineBusy else "noinv")
            self._live_watch_log(
                "等待应用空闲/清单就绪(%s) %s"
                % (_why, time.strftime("%H:%M:%S")))
            return
        # 未选中的组仍可能带有 CH_EFFECT。判断自动补写时只检查选中的组，
        # 避免关闭效果名通道后还给新实例写入译名。
        from core.inventory import CH_EFFECT as _CH_EFF
        # 优先使用由 setChannelAuto 记录的显式开关意图。没有记录时读取
        # channelState()。清单重建期间的瞬时 False 可能让再次启用失败。
        _want = getattr(self, "_effWant", None)
        if _want is not None:
            _eff_on = bool(_want)
        else:
            try:
                _cs = self.channelState()
                _eff_on = bool(_cs[0]) if _cs else True
            except Exception:
                _eff_on = True
        if not _eff_on:                     # 效果名通道没开
            self._liveAutoInflight = False  # 清掉可能卡住的在飞标记
            self._live_hook_stop("tick-off want=%r" % (getattr(self, "_effWant", None),))
            self._liveSeen.clear()
            # 旧版将 +0x10 写进工程，重开工程时可能加载已保存的中文标题。
            # 效果名通道关闭时也执行还原。
            self._live_revert_tick()
            return
        # 通道开着才进入写入流程；在飞检查放在这里，关通道不受它阻挡。
        if (self._liveAutoInflight
                and time.monotonic() - getattr(self, "_liveAutoInflightAt", 0.0) < 3.0):
            return
        if self._liveAutoInflight:
            # ★ 2026-11-08：在飞标记卡住时不能一直静默 return —— 用户开关
            #   通道后看护拍全被它挡住，表现为「第二遍死活不恢复」。
            self._liveAutoInflight = False
            self._live_watch_log("清掉卡住的在飞标记(%s)"
                                 % (getattr(self, "_liveApplyNote", "") or "-",))

        from core.inventory import CH_EFFECT

        if not any(CH_EFFECT in g.channels for g in self._inv.groups):
            self._live_watch_log("没有勾选的效果名插件")
            return                          # 没有可写的插件，无从补写
        # ★ 每一拍都真的去做:0.4 秒拍只是「节奏」,不是「这一拍跳过」。
        #   稳态下 liveparams 走 parade 根向量(每个 parade 两次小读)+ 根流
        #   缓存,实测毫秒级,不做会话记账也能扛住。
        from core import proc as _proc

        try:
            if procs is None:
                procs = _proc.list_ae_hosts()
        except Exception:
            return
        if not procs:
            self._live_watch_log("AE 未运行")
            self._liveSeen.clear()
            return
        if len(procs) != 1:
            self._live_watch_log("AE 进程数 %d,不唯一" % len(procs))
            return
        p = procs[0]
        key = (p["pid"], p["creation_time"])
        # ★ 2026-11-01 的 45 秒稳定窗(批量写名段有堆竞争嫌疑)随 frida 崩溃修复
        #   一并取消：AE 主窗口就绪就写，宁可让布局校准在目录变动时失败
        #   (下一拍重试)，也不让用户等。
        first = self._liveSeen.setdefault(key, time.monotonic())
        if time.monotonic() - first < self._LIVE_STABLE_SECS:
            self._live_watch_log("等待 AE 稳定窗(%ss,pid=%s)"
                                 % (int(self._LIVE_STABLE_SECS), p["pid"]))
            return
        try:
            _proc.main_window(p["pid"])     # AE 还没起好/有对话框 → 下一拍再试
        except Exception:
            self._live_watch_log("等待 AE 主窗口就绪(pid=%s)" % p["pid"])
            return
        # ★ 2026-11-08：frida 的 exports_sync（setpaused/resume/stats）是同步
        #   RPC —— 放在 Qt 定时器回调里，一旦 AE 主线程忙（比如它正在我们的
        #   钩子里写名字），这个 RPC 会长时间不返回，把整个界面线程按住，
        #   看护拍随即停摆几十秒，用户点开关完全没反应。改成后台线程执行。
        if not getattr(self, "_liveHookTickBusy", False):
            self._liveHookTickBusy = True

            def _ht(_pid=int(p["pid"])):
                try:
                    self._live_hook_tick(_pid)
                except Exception:
                    pass
                finally:
                    self._liveHookTickBusy = False

            try:
                threading.Thread(target=_ht, daemon=True).start()
            except Exception:
                self._liveHookTickBusy = False
        if (self._liveHook is not None and self._liveHook.running()
                and not self._liveHook.paused()):
            # ★ 2026-11-07 晚：挂钩已接管全部写入（读取端 GetDisplayName 负责
            #   已有实例，写入端 SetDisplayName 负责新加效果）。不再轮询扫内存，
            #   既省 CPU，也避免分片 discover 每拍几十 MB 的读取拖慢界面。
            self._liveAutoInflight = False
            return
        self._liveAutoInflight = True
        self._liveAutoInflightAt = time.monotonic()
        self._live_watch_log("开始自动补写(pid=%s)" % p["pid"])
        # ★ 2026-11-08：apply 里可能跑几秒（枚举 9000 个区间 + 扫 20MB，实测
        #   4.9~23.5 秒）。以前是同步跑在 Qt 定时器回调里 → 主线程被占住，
        #   看护拍整段停摆，用户点开关后几十秒都没反应（表现为「第二遍开
        #   通道死活不生效」）。改成后台线程，主线程只负责调度。
        try:
            # 默认选区恢复的 ON（_effWant is None）也属于当前 generation 0；
            # 用户随后 OFF（gen1）必须能取消它。真正手动未绑定意图才传 None。
            _igen = int(getattr(self, "_effGen", 0))
            threading.Thread(target=self._live_apply, daemon=True,
                             kwargs={"auto_rename": True, "quiet": True,
                                     "memo_key": key, "intent_gen": _igen}).start()
        except Exception as _e:
            self._liveAutoInflight = False
            self._live_watch_log("自动补写线程启动失败: %r" % (_e,))

    def _live_win32_redraw(self, pid: int) -> int:
        """只发 Win32 重绘消息，绝不移动鼠标/键盘/改选择。"""
        try:
            import ctypes
            from ctypes import wintypes as W

            from core import proc as _proc

            hwnd = int(_proc.main_window(int(pid)) or 0)
            if not hwnd:
                return 0
            u = ctypes.windll.user32
            u.RedrawWindow.argtypes = [W.HWND, ctypes.c_void_p, ctypes.c_void_p, W.UINT]
            u.RedrawWindow.restype = W.BOOL
            u.InvalidateRect.argtypes = [W.HWND, ctypes.c_void_p, W.BOOL]
            u.InvalidateRect.restype = W.BOOL
            u.UpdateWindow.argtypes = [W.HWND]
            u.UpdateWindow.restype = W.BOOL
            # 不使用 RDW_UPDATENOW/UpdateWindow：跨进程同步 WM_PAINT 可能
            # 阻塞/重入；只标脏，让 AE 自己下一帧重绘并触发 GetName。
            flags = 0x1 | 0x4 | 0x80 | 0x400  # INVALIDATE|ERASE|ALLCHILDREN|FRAME
            ok = bool(u.RedrawWindow(W.HWND(hwnd), None, None, flags))
            u.InvalidateRect(W.HWND(hwnd), None, False)
            return 1 if ok else 0
        except Exception:
            return 0

    def _live_first_refresh(self, pid: int, gen: int) -> None:
        """首次 ON 后，hook 已挂但既有 CECTitle pane 还没被画过：有界刷新 3 次。

        旧版本 PANES=0，首次 ON 只能等用户加效果/切面板；这里只发
        RedrawWindow/InvalidateRect（无鼠标无按键），让 GetName hook 首次
        看到 CECTitle，再用 DisplayHook.invalidate 收口。每次间隔 150ms，
        最多 3 次，不扫堆、不阻塞 Qt。
        """
        if getattr(self, "_liveFirstRefreshGen", None) == gen:
            return
        self._liveFirstRefreshGen = gen
        hook = getattr(self, "_liveHook", None)
        if hook is None:
            return
        _epoch = int(getattr(self, "_liveSessionEpoch", 0))

        def work():
            n = 0
            for _i in range(3):
                if int(getattr(self, "_liveSessionEpoch", 0)) != _epoch:
                    return
                try:
                    self._live_win32_redraw(int(pid))
                except Exception:
                    pass
                time.sleep(0.15)
                if int(getattr(self, "_liveSessionEpoch", 0)) != _epoch:
                    return
                try:
                    n = int(hook.invalidate() or 0)
                except Exception:
                    n = 0
                if n:
                    break
            self._live_watch_log(
                "首次无鼠标刷新完成(gen=%s, CECTitle pane=%d)" % (gen, n))

        try:
            threading.Thread(target=work, daemon=True).start()
        except Exception:
            pass

    def _live_hook_catchup(self, gen: int) -> None:
        """resume 成功后，对「暂停窗口内出生的当前 parade 实例」补一次汉化。

        ★ 不能只把 Python 的 paused 置 False 就算完成：SetDisplayName 已经错过
          的实例不会因 getter 再被读一次就必然重写。这里排一次后台 _live_apply，
          它走已修好的 parade 最新根向量，只写显示名缓存，不扫全堆。
          同一代只排一次；旧代号由 intent_gen 校验自动放弃。
        """
        if getattr(self, "_liveHookCatchupGen", None) == gen:
            return
        self._liveHookCatchupGen = gen
        try:
            self._live_watch_log("挂钩恢复后补写(gen=%s)" % gen)
            self._live_apply(auto_rename=True, quiet=True, intent_gen=gen)
        except Exception as exc:
            self._liveHookCatchupGen = None
            self._live_watch_log("挂钩恢复后补写失败: %s" % exc)

    def _effects_need_window(self):
        hook = getattr(self, "_liveHook", None)
        if hook is not None and hook.running() and not hook.paused():
            return True
        if self._effWant is not None:
            return self._effWant
        return bool(self._inv and any(CH_EFFECT in g.channels for g in self._inv.selected()))

    def background_state(self) -> BackgroundState:
        with self._liveIntentLock:
            language = self.channelState()[1]
            return BackgroundState(
                shutting_down=self._liveShutdown,
                engine_busy=self._engineBusy,
                language_disabled=self._langWant is False,
                language_configured=self._inv is not None or bool(self._reopenedBackground),
                language_command=self._engineSession.requested_language_command(language),
                engine_detected=self._engine.running,
                stop_unconfirmed=self._engineSession.stop_result is False,
            )

    def background_operations_busy(self, *, include_panel: bool = False) -> bool:
        return (self.busy or self._engineBusy or (include_panel and self.panelLocalization.busy)
                or any(worker.isRunning() for worker in self._workers))

    def background_needs_effects_controller(self) -> bool:
        return self._effects_need_window()

    def background_engine_enabled(self) -> bool:
        return self._engine_intent_allowed()

    def notify_background(self, level: str, message: str) -> None:
        self.toast.emit(level, message)

    @Property(bool, notify=engineSessionChanged)
    def backgroundModeAvailable(self):
        return self.background_session.language_can_stay()

    @Slot(result=bool)
    def closeToBackground(self):
        return self.background_session.prepare_language_handoff()

    def rememberBackgroundOnStartup(self):
        return self.background_session.remember_background_on_startup()

    @Slot(result=bool)
    def requestWindowClose(self):
        return self.background_session.request_close()

    @Slot()
    def shutdown(self):
        """Ordinary app close: bounded effect teardown, never hang the UI."""
        if getattr(self, "_liveShutdown", False):
            ev = getattr(self, "_shutdownEvent", None)
            if ev is not None:
                ev.wait(2.0)
            return
        self._liveShutdown = True
        self._liveSessionEpoch = int(getattr(self, "_liveSessionEpoch", 0)) + 1
        self._effGen = int(getattr(self, "_effGen", 0)) + 1
        self._shutdownEvent = threading.Event()
        try:
            if getattr(self, "_liveWatch", None) is not None:
                self._liveWatch.stop()
        except Exception:
            pass

        def worker():
            try:
                with self._liveOpLock:
                    if not getattr(self, "_liveShutdown", False):
                        return
                    hook = getattr(self, "_liveHook", None)
                    if hook is not None:
                        try:
                            ok_stop = hook.stop(
                                gen=int(getattr(self, "_effGen", 0)))
                        except TypeError:
                            ok_stop = hook.stop()
                        if not ok_stop:
                            self._live_watch_log(
                                "shutdown effect stop failed: %s"
                                % (getattr(hook, "last_error", "") or ""))
                            return
                    self._liveHook = None
                    self._liveHookSession = None
                    self._liveHookResumedGen = None
                    self._liveHookStopped = True
            except Exception as exc:
                self._live_watch_log("shutdown effect exception: %r" % (exc,))
            finally:
                self._shutdownEvent.set()

        threading.Thread(target=worker, daemon=True,
                         name="aeloc-shutdown-effect").start()
        self._shutdownEvent.wait(2.0)

    def _live_host(self, pid: int):
        try:
            from core import proc as _proc
            import os as _os

            for p in _proc.list_ae_processes():
                if int(p.get("pid") or 0) == int(pid):
                    return {"pid": int(pid),
                            "ctime": int(p.get("creation_time") or 0),
                            "image": _os.path.abspath(
                                str(p.get("image") or ""))}
        except Exception:
            pass
        return None

    def _live_session_key(self, pid: int):
        """AE 身份键：同一 pid 重启也不能沿用旧 hook 的 gen 标记。"""
        try:
            from core import proc as _proc

            for p in _proc.list_ae_processes():
                if int(p.get("pid") or 0) == int(pid):
                    return (int(pid), int(p.get("creation_time") or 0),
                            str(p.get("image") or ""))
        except Exception:
            pass
        return (int(pid), 0, "")

    def _live_hook_tick(self, pid: int) -> None:
        """确保 frida「显示名就地汉化」挂钩已挂上（通道开着时）。

        ★ 2026-11-07 真机定案：面板标题读实例显示名缓存，AE 通过导出函数
          SetDisplayName 填它。挂钩在填充前把英文换成中文 → 加效果瞬间即中文，
          不再依赖轮询发现新实例。
        ★ 1 秒节流 + 后台线程；挂不上就静默回退到原来的轮询换名。
        """
        if getattr(self, "_liveShutdown", False):
            return
        now = time.monotonic()
        _hook0 = getattr(self, "_liveHook", None)
        if (not self._engineBusy
                and not engine.engine_running()
                and self._engine_intent_allowed()
                and now - getattr(self, "_effectHostAttempt", 0.0) >= 5.0):
            # runtime 崩溃/正常 STOP 后即使旧 proxy 对象还在，也不能被
            # _hook0 is not None 永久挡住 effect-only host 恢复。
            self._effectHostAttempt = now
            if _hook0 is not None:
                try:
                    _hook0.stop()
                except Exception:
                    pass
                self._liveHook = None
                self._liveHookSession = None
                self._liveHookStopped = False
            QTimer.singleShot(0, self.startEngine)
            return
        _want0, _gen0 = self._live_intent()
        # ★ 2026-11-09：新 ON / paused 会话必须越过 1 秒节流。否则用户在第 3 轮
        #   ON 时新增的效果落在「上次 tick 还没满 1 秒」的窗口里，resume 被推迟，
        #   SetDisplayName 已经错过，之后 running() 又让 tick 早退 → 永久英文。
        _priority = bool(_want0) and (
            getattr(self, "_liveHookResumedGen", None) != _gen0
            or (_hook0 is not None and _hook0.paused())
            or getattr(self, "_liveHookSession", None) != self._live_session_key(pid))
        if not _priority and now - getattr(self, "_liveHookAt", 0.0) < 1.0:
            return
        self._liveHookAt = now
        # ★ 测试沙盒保护：unittest 里的假 pid（111/77/4242…）绝不能真的
        #   frida.attach。TestLiveWatch 会 patch _live_auto_allowed 来测
        #   补写链，但那不代表允许测试进程去挂真实 AE。
        if "unittest" in sys.modules and getattr(self, "_liveHook", None) is None:
            return
        # ★ 独立诊断文件（绕开 live_watch.log 的去重/过滤），确认本函数真的被调用
        try:
            with open(os.path.join(DATA_ROOT, "hook_diag.log"), "a",
                      encoding="utf-8") as _f:
                _f.write("%s tick pid=%s hook=%s\n" % (
                    time.strftime("%H:%M:%S"), pid,
                    getattr(getattr(self, "_liveHook", None), "_pid", None)))
        except Exception:
            pass
        # ★★ 2026-11-09：本函数由 _liveWatchTick 的后台线程调用，**自己不再
        #   另起 work 线程**。旧版外层 _liveHookTickBusy 在 work 线程启动后
        #   立即复位，下一次看护拍又能进来，于是同一个 hook 上会同时跑
        #   resume / stats / ensure；和 OFF 分支的 pause/revert 撞在一起时，
        #   旧线程会在新 ON 之后把 PAUSED 置回 true。现在整段拿 _liveOpLock，
        #   和 _live_hook_stop 严格串行，且每个 RPC 前后校验意图代号。
        with self._liveOpLock:
            if getattr(self, "_liveShutdown", False):
                return
            _want, _gen = self._live_intent()
            if not _want:
                return
            _skey = self._live_session_key(pid)
            # ★ AE 退出重开（PID/creation/image 变化）时，_effGen 仍是同值，
            #   不能复用旧会话的 resumed/catchup/firstRefresh 标记；否则新 hook
            #   已 ensure 却被这些 gen 标记跳过，现有/新增效果都永远不补写。
            if (self._liveHookSession is not None
                    and self._liveHookSession != _skey):
                hook_old = getattr(self, "_liveHook", None)
                if hook_old is not None:
                    try:
                        hook_old.stop()
                    except Exception:
                        pass
                self._liveHookResumedGen = None
                self._liveHookCatchupGen = None
                self._liveFirstRefreshGen = None
                self._liveHookStopped = False
                self._liveHookSession = None
                self._liveRealNamesSig = None
                self._liveRealNamesCache = None
                self._liveTableCache = None
                self._liveSessionEpoch = int(getattr(self, "_liveSessionEpoch", 0)) + 1
                self._live_watch_log(
                    "AE 会话变化，重置 hook 代次标记 pid=%s" % pid)
            # 先恢复已有的同名会话（即使词典表一时为空，也必须先 resume；
            # 旧版「表为空就 return」会让第二次 ON 永远恢复不了）。
            hook = getattr(self, "_liveHook", None)
            if (self._liveHookSession is None and hook is not None
                    and hook.running() and hook.pid() == int(pid)):
                # 兼容已存在 hook 但 session 标记尚未初始化的首拍（迁移/测试）。
                self._liveHookSession = _skey
            if hook is not None:
                try:
                    if (hook.running() and hook.pid() == int(pid)
                            and self._liveHookSession == _skey):
                        need_resume = (hook.paused()
                                       or getattr(self, "_liveHookResumedGen", None) != _gen)
                        if need_resume:
                            ok_re = self._live_hook_resume(
                                hook, int(getattr(self, "_effGen", 0)))
                            if ok_re:
                                self._liveHookResumedGen = _gen
                                self._remember_effect_receipt(_gen, _skey, True)
                                self._live_watch_log("挂钩恢复成功(gen=%s)" % _gen)
                            else:
                                self._live_watch_log(
                                    "挂钩恢复失败(将重挂):%s"
                                    % (getattr(hook, "last_error", "") or "unknown"))
                        else:
                            ok_re = True
                        if ok_re:
                            _sync_ok = self._live_sync_real_names(hook, pid)
                            if _sync_ok:
                                # 注册表从空/部分变成完整：重新排 catchup 和
                                # 首帧 pane 刷新，不要求用户再拨开关。
                                self._liveHookCatchupGen = None
                                self._liveFirstRefreshGen = None
                            # REAL 没有成功同步时不要用空白名单做一次“成功”补写。
                            if getattr(self, "_liveRealNamesSig", None):
                                self._live_hook_catchup(_gen)
                                self._live_first_refresh(int(pid), _gen)
                            self._live_hook_stats()
                            return
                except Exception as _e:
                    self._live_watch_log("挂钩恢复异常: %r" % (_e,))
            try:
                mapping = self._live_mapping() or {}
            except Exception:
                return
            table = {k: v for k, v in mapping.items()
                     if k and v and v != k and "|" not in k
                     and len(k) <= 47 and len(v) <= 47}
            if not table:
                return
            if hook is None:
                try:
                    from core.effect_proxy import DisplayHookProxy as DisplayHook
                except Exception:
                    return
                hook = DisplayHook(engine.DATA_ROOT)
                hook.on_patch = self._live_nudge_after_patch
                hook.on_ra = self._live_ra_log
                self._liveHook = hook
                self._liveHookStopped = False
            try:
                _real = self._live_real_names(int(pid))
                _ui = self._live_ui_title_ret(int(pid))
                _hsig = (int(getattr(self._store, "revision", 0)),
                         len(table), hash(frozenset(table.items())),
                         len(_real), hash(frozenset(_real)),
                         int(_ui or 0))
                ok = self._live_hook_ensure(
                    hook, int(pid), table, _real, _ui,
                    self._live_host(int(pid)), _hsig,
                    int(getattr(self, "_effGen", 0)))
            except Exception as exc:
                self._live_watch_log("显示名挂钩异常: %s" % exc)
                return
            if not ok:
                self._live_watch_log("显示名挂钩启用失败: %s"
                                     % (getattr(hook, "last_error", "") or "unknown"))
                return
            self._liveHookStopped = False
            try:
                self._liveRealNamesSig = frozenset(_real)
            except Exception:
                pass
            if self._liveHookSession != _skey:
                self._liveHookSession = _skey
                self._liveHookResumedGen = None
                self._liveHookCatchupGen = None
                self._liveFirstRefreshGen = None
            # attach/ensure 可能耗时；如果这期间用户已经点了关，别再把
            # 「已启用」写成最终状态，交给 OFF 收尾线程 pause。
            _want2, _gen2 = self._live_intent()
            if not _want2 or _gen2 != _gen:
                return
            # ensure 只保证 attach + load；再用 resume 明确把 JS 侧 PAUSED
            # 置 false（也会把 WANT_ZH 置 true），避免脚本里残留暂停态。
            try:
                if not self._live_hook_resume(
                        hook, int(getattr(self, "_effGen", 0))):
                    self._live_watch_log(
                        "显示名挂钩恢复失败(将重挂):%s"
                        % (getattr(hook, "last_error", "") or "unknown"))
                    return
            except Exception as _e:
                self._live_watch_log("显示名挂钩恢复异常: %r" % (_e,))
                return
            _want3, _gen3 = self._live_intent()
            if not _want3 or _gen3 != _gen:
                return
            self._liveHookResumedGen = _gen
            self._remember_effect_receipt(_gen, _skey, True)
            self._live_hook_catchup(_gen)
            self._live_first_refresh(int(pid), _gen)
            self._live_watch_log("显示名挂钩已启用(pid=%s,gen=%s)" % (pid, _gen))
            # ★ 冷 parade 发现统一由 _live_hook_catchup -> _live_apply ->
            #   EffectNameTable._current_roots -> _roots_for_walk 的
            #   _WALK_BUSY 单 flight 完成（walk(expand=False)）。这里不要再开
            #   第二条 walk，否则会展开参数树并与统一发现并发。
            # 挂上后立刻轻推一次重绘：面板重绘会调用 GetDisplayName，
            # 读取端钩子借此把「已有实例」的英文缓存就地改成中文——
            # 不需要用户切图层，也不需要全量内存扫描。
            try:
                self._live_nudge_after_patch()
            except Exception:
                pass
            # 只写意图；下一次 _liveWatchTick 在 Qt 主线程应用。
            self._liveWatchWantInterval = 400

    def _live_ra_log(self, d) -> None:
        """记录 GetDisplayName 的调用点（诊断用：找面板重建入口）。

        ★ 2026-11-07：面板控件持有自己的字符串副本，只有「选择变化」时重建
          才会重新读 GetDisplayName。要无感触发重建，必须先知道是哪个函数
          在调 GetDisplayName —— 这里把 hook 采到的调用点写进日志。
        """
        try:
            items = sorted(d.items(), key=lambda kv: -kv[1])[:8]
            txt = ", ".join("%s x%d" % (k, v) for k, v in items)
            self._live_watch_log("GetDisplayName 调用点: " + txt)
        except Exception:
            pass

    def _live_nudge_after_patch(self, force: bool = False) -> None:
        """效果名写入/OFF 后，让 AE 真实重绘当前窗口。

        RPC/JS 侧不再调历史 pane 的 UpdateNow/InvalTopic；必须由 Python 发
        无输入 Win32 RedrawWindow/InvalidateRect（异步，不发 WM_PAINT 同步等待），
        否则 AE 自绘 Effect Controls 的纹理/缓存不会重画。3 次有界，后台线程。
        """
        now = time.monotonic()
        if not force and now - getattr(self, "_liveNudgeAt", 0.0) < 0.6:
            return
        self._liveNudgeAt = now
        hook = getattr(self, "_liveHook", None)
        if hook is None or not hook.running():
            return
        try:
            pid = int(hook.pid() or 0)
        except Exception:
            pid = 0
        if not pid:
            return
        epoch = int(getattr(self, "_liveSessionEpoch", 0))

        def work():
            # 2~3 次有界异步标脏：AE 自己下一帧处理，不 UpdateWindow，
            # 不 RDW_UPDATENOW，不阻塞 Qt，也不重入 frida/UI。
            for i in range(3):
                if int(getattr(self, "_liveSessionEpoch", 0)) != epoch:
                    return
                try:
                    hook.invalidate()
                except Exception:
                    pass
                try:
                    self._live_win32_redraw(pid)
                except Exception:
                    pass
                if i < 2:
                    time.sleep(0.12)

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _live_hook_ensure(hook, pid, table, real, ui, host, sig, gen):
        try:
            return bool(hook.ensure(pid, table, real, ui, host=host,
                                    sig=sig, gen=gen))
        except TypeError:
            # Legacy/fake DisplayHook contract used by older tests.
            try:
                return bool(hook.ensure(pid, table, real, ui))
            except TypeError:
                return bool(hook.ensure(pid, table))

    @staticmethod
    def _live_hook_pause(hook, gen):
        try:
            return bool(hook.pause(gen=gen))
        except TypeError:
            return bool(hook.pause())

    @staticmethod
    def _live_hook_resume(hook, gen):
        try:
            return bool(hook.resume(gen=gen))
        except TypeError:
            return bool(hook.resume())

    def _live_hook_stats(self) -> None:
        """每 5 秒把挂钩统计写一行日志：哪些名字没被换、哪些容量不够。"""
        now = time.monotonic()
        if now - getattr(self, "_liveHookStatsAt", 0.0) < 5.0:
            return
        self._liveHookStatsAt = now
        hook = getattr(self, "_liveHook", None)
        if hook is None:
            return
        try:
            st = hook.stats() or {}
        except Exception as _e:
            self._live_watch_log("挂钩统计读取失败: %r" % (_e,))
            return
        try:
            with open(os.path.join(DATA_ROOT, "hook_stats.log"), "a",
                      encoding="utf-8") as _f:
                _f.write("%s %r\n" % (time.strftime("%H:%M:%S"), st))
        except Exception:
            pass
        if not st:
            self._live_watch_log("挂钩统计为空(脚本未就绪)")
            return
        try:
            miss = "、".join((st.get("miss") or [])[:6])
            caps = "、".join((st.get("caps") or [])[:4])
            bt = " | ".join(" ".join((f or [])[:4])
                            for f in (st.get("edbBt") or [])[:2])
            # 统计行必须每 5 秒落盘（去重会把它当重复吞掉）
            self._liveWatchLastNote = None
            self._live_watch_log(
                "挂钩统计 calls=%s hit=%s patched=%s skip_cap=%s fill=%s/%s topic=%s/%s draw=%s/%s dname=%s panes=%s edb=%s/%s/%s/%s/%s/%s nameErr=%s scriptErr=%s edbBt=%s pauseFix=%s%s%s%s"
                % (st.get("calls"), st.get("hit"), st.get("patched"),
                   st.get("skip_cap"), st.get("fillCalls"), st.get("fillPatched"),
                   st.get("nameCalls"), st.get("namePatched"),
                   st.get("slotCalls"), st.get("slotPatched"),
                   st.get("dn"), st.get("panes"),
                   st.get("edbCalls"), st.get("edbCandidates"),
                   st.get("edbTitles"), st.get("edbPatched"),
                   st.get("edbReadFail"), st.get("edbWriteFail"),
                   st.get("nameErrors"),
                   str(st.get("script_error") or "")[:80],
                   bt[:160], st.get("pauseFix"),
                   (" | 名字样本: " + "、".join((st.get("nameSample") or [])[:3]))
                   if st.get("nameSample") else "",
                   (" | 未命中: " + miss) if miss else "",
                   (" | 容量不足: " + caps) if caps else ""))
        except Exception:
            pass

    def _live_panel_refresh_jsx(self, pid: int) -> None:
        """用 ExtendScript 让 AE 重建「效果控件」面板（一次性，后台线程）。

        ★ 2026-11-07 晚：内存里的显示名已写回英文，但面板控件持有一份自己的
          字符串副本，只有面板重建/切图层才会重读。这里用 AfterFX.exe -r 转交
          一段脚本「先选另一个图层、再选回来」，AE 自己会重建面板 —— 不靠鼠标、
          不抢焦点。只在关通道时跑一次（以前每拍都跑，把程序卡死过）。
        """
        try:
            from core import aejsx
            exe = aejsx.exe_of_pid(int(pid))
            if not exe:
                return
            body = """
var c = app.project ? app.project.activeItem : null;
var r = 'no';
if (c && c.selectedLayers && c.selectedLayers.length > 0) {
  var L = c.layers, sel = c.selectedLayers[0], other = null;
  for (var i = 0; i < L.length; i++) {
    if (L[i] !== sel) { other = L[i]; break; }
  }
  if (other) { other.selected = true; sel.selected = true; }
  else { sel.selected = false; sel.selected = true; }
  r = 'ok';
}
var f = new File('__OUT__');
if (f) { f.open('w'); f.write(r); f.close(); }
"""
            out = aejsx.run_script(exe, body, timeout=12.0,
                                   out_dir=os.path.join(DATA_ROOT, "out"))
            self._live_watch_log("关通道后重建面板: %r" % (out,))
        except Exception as exc:
            self._live_watch_log("关通道后重建面板失败: %s" % exc)

    def _live_hook_stop(self, reason: str = "", force: bool = False) -> None:
        """暂停显示名挂钩并还原英文。**幂等 + 串行 + 带意图代号**。

        ★ 2026-11-09 根因修复：旧版每次看护拍（400ms）都新建一个 _bg 线程，
          每个线程都 pause/revert；用户快速 OFF→ON 时，旧的 OFF 线程可能在
          新 ON 的 resume 之后才执行，于是 PAUSED 又被置 true、中文又被写回
          英文 —— 这就是「第一遍正常，反复开关两下后第二遍不汉化」。现在：
            · _liveHookStopBusy：同一时刻最多一个收尾线程；
            · _liveHookStopped：完整停过一次就不再每拍重复 RPC；
            · _liveOpLock：和 _live_hook_tick 的 resume/stats 严格互斥；
            · 每个阻塞点后都复查 _live_intent()，新 ON 一到立刻放弃，让
              ON 路径去 resume，绝不把旧意图写回。
        """
        if getattr(self, "_liveShutdown", False):
            return
        _want, _gen = self._live_intent()
        if _want and not force:
            return
        if getattr(self, "_liveHookStopped", False) and not force:
            return
        # 还没有任何挂钩：不需要为「停」启动线程。之前这里仍会每拍起线程，
        # 既浪费也让测试/首次启动多出无意义的并发。
        if getattr(self, "_liveHook", None) is None and not force:
            _w2, _g2 = self._live_intent()
            if (not _w2) and _g2 == _gen:
                self._liveHookStopped = True
            return
        if getattr(self, "_liveHookStopBusy", False):
            return
        self._liveHookStopBusy = True
        if reason:
            self._live_watch_log("停止挂钩(%s, gen=%s)" % (reason, _gen))

        def _superseded() -> bool:
            # 新 ON 已到达或 App 正在退出：后面的还原不能再 resume/重建。
            return getattr(self, "_liveShutdown", False) or (
                (not force) and self._live_intent()[0])

        def _mark_stopped(host=None) -> None:
            # 只有「代号仍是本次 OFF」才记已停用；否则旧线程可能在新 ON 之后
            # 写脏 _liveHookStopped，让下一轮 OFF 直接早退、根本不去 pause。
            _w2, _g2 = self._live_intent()
            if (not _w2) and _g2 == _gen:
                self._liveHookResumedGen = None
                self._liveHookStopped = True
                self._remember_effect_receipt(_gen, host, False)

        def _bg():
            try:
                with self._liveOpLock:
                    if _superseded():
                        return
                    hook = getattr(self, "_liveHook", None)
                    if hook is None:
                        _mark_stopped()
                        return
                    _pid = 0
                    try:
                        _pid = int(hook.pid() or 0)
                    except Exception:
                        _pid = 0
                    # 顺序很重要：先 pause 再还原。还原走 AE 自己的
                    # SetDisplayName（可能要重新分配缓冲），不先 pause 的话
                    # 钩子会把英文入参又改成中文。
                    try:
                        if not self._live_hook_pause(hook, _gen):
                            self._live_watch_log(
                                "OFF pause 未确认(gen=%s): %s"
                                % (_gen, getattr(hook, "last_error", "")))
                            return
                    except Exception as exc:
                        self._live_watch_log(
                            "OFF pause 异常(gen=%s): %r" % (_gen, exc))
                        return
                    if _superseded():
                        # ON 在我们 pause 期间到达：把刚按下的 PAUSED 立刻恢复，
                        # 不让「旧的关」成为最终状态。
                        try:
                            self._live_hook_resume(
                                hook, int(getattr(self, "_effGen", 0)))
                        except Exception:
                            pass
                        self._liveHookResumedGen = None
                        return
                    n_rev = 0
                    try:
                        n_rev = int(hook.revert_now() or 0)
                    except Exception:
                        pass
                    if _superseded():
                        try:
                            self._live_hook_resume(
                                hook, int(getattr(self, "_effGen", 0)))
                        except Exception:
                            pass
                        self._liveHookResumedGen = None
                        return
                    _addrs = []
                    try:
                        from core import liveparams as _lp
                        _addrs = list(((_lp._WALK_CACHE.get(_pid) or {})
                                       .get("streams") or []))
                    except Exception:
                        _addrs = []
                    n_addr = 0
                    if _addrs:
                        # 分块还原并逐块复查意图：单块最多 256 个地址，
                        # 避免一次 RPC 长时间占着控制锁、把新 ON 的 resume
                        # 堵到几秒以上。
                        for _i in range(0, len(_addrs), 256):
                            if _superseded():
                                break
                            try:
                                _r = hook.revert_addrs(_addrs[_i:_i + 256]) or {}
                                n_addr += int(_r.get("written") or 0)
                            except Exception:
                                pass
                    if _superseded():
                        try:
                            self._live_hook_resume(
                                hook, int(getattr(self, "_effGen", 0)))
                        except Exception:
                            pass
                        self._liveHookResumedGen = None
                        return
                    self._live_watch_log(
                        "显示名挂钩已暂停(快还原: 账本 %d, 地址 %d, 面板实例 %d 个)"
                        % (n_rev, n_addr,
                           hook.panes() if hasattr(hook, "panes")
                           else getattr(hook, "_panes", 0)))
                    try:
                        self._live_nudge_after_patch(force=True)
                    except Exception:
                        pass
                    # ★ 2026-11-09：旧版在 op 锁内现场 walk（15~45 秒）再按
                    #   地址还原，会把紧随其后的 ON 的 resume 一起堵住，导致
                    #   「第二遍开通道 30 秒不生效」。walk 缓存为空时不再在
                    #   OFF 收尾里做慢扫描：pause 后读取端 GetDisplayName 已经
                    #   按 ZH2EN 返回英文，nudge 一次就能刷新；漏掉的实例由
                    #   _live_revert_tick 的后台自愈拍补，不再占开关关键路径。
                    # 只有仍是最新 OFF 意图才记账为「已停用」。若期间变成 ON，
                    # 不设 _liveHookStopped，下一拍会走 resume 分支。
                    _mark_stopped(self._liveHookSession)
            except Exception:
                pass
            finally:
                self._liveHookStopBusy = False

        try:
            threading.Thread(target=_bg, daemon=True).start()
        except Exception:
            self._liveHookStopBusy = False
        # 只写意图；不要在 worker 线程直接 setInterval。
        self._liveWatchWantInterval = 400

    def _live_revert_tick(self) -> None:
        """通道关着时的自愈拍：把内存里已经是中文的标题写回英文。

        ★ 只写实例 +display_off 的显示名缓存（不写 +0x10），所以既不会再把
          工程写脏，也能清掉历史上写脏的那批。
        ★ 2 秒节流 + 后台线程：每一拍只是虚表 needle 扫 12MB（约 50ms），
          不占 UI 线程。
        """
        if self._liveRevertBusy:
            return
        _want, _gen = self._live_intent()
        if _want:
            return
        now = time.monotonic()
        # ★ 2026-11-07 晚：挂钩接管后，关通道的还原已由 frida 在卸载前
        #   原地完成（对象地址都在内存里，不用扫）。这里的扫描只兜底
        #   「历史遗留/挂钩没挂上」的情况，因此放宽到 10 秒一拍，避免
        #   每 2 秒读几十 MB 拖慢界面。
        if now - self._liveRevertAt < 3.0:
            return
        from core import proc as _proc

        try:
            procs = _proc.list_ae_processes()
        except Exception:
            return
        # JSX 转发可能留下额外进程。选用第一个有主窗口的 AE 执行还原。
        p = None
        for q in procs:
            try:
                if _proc.main_window(q["pid"]):
                    p = q
                    break
            except Exception:
                continue
        if p is None:
            return
        mapping = self._live_mapping() or {}
        table = {k: v for k, v in mapping.items() if k and v and v != k}
        if not table:
            return
        self._liveRevertAt = now
        self._liveRevertBusy = True

        def _cancelled() -> bool:
            # 新一轮 ON 已到达：旧 OFF 扫描不能再往内存里写英文。
            return (not self._live_intent_ok(_gen, False))

        def work():
            from core import liveparams
            from core.proc import Process

            try:
                if _cancelled():
                    return {"cancelled": True, "restored": 0}
                with Process(int(p["pid"]), True) as proc:
                    return liveparams.EffectNameTable(
                        proc, table, data_root=DATA_ROOT).revert(cancel=_cancelled)
            except Exception as exc:
                return {"error": "%s: %s" % (type(exc).__name__, exc)}

        def done(res):
            self._liveRevertBusy = False
            res = res or {}
            if res.get("error"):
                self._live_watch_log("关通道自愈异常:%s" % res["error"][:80])
                return
            if res.get("cancelled"):
                self._live_watch_log("关通道自愈已取消(新意图已到)")
                return
            self._live_watch_log(
                "关通道自愈 %s:restored=%s skipped=%s walked=%s ents=%s"
                % (__import__("time").strftime("%H:%M:%S"),
                   res.get("restored"), res.get("skipped"),
                   res.get("walked"), res.get("ents")))

        self._run(work, done, _silent=True, _toast_fail=False)

    # AE 主窗口就绪后立即补写。早期用 45 秒稳定窗规避崩溃循环，
    # 后来定位到 Frida trampoline 的 NOP 填充（见 guard_uni）。
    _LIVE_STABLE_SECS = 0.0

    def _live_watch_log(self, note: str) -> None:
        """效果名常驻补写的诊断日志(状态变化才写一行,<数据根>/live_watch.log)。

        记录每个跳过步骤的原因，便于定位常驻补写在哪个条件等待或失败。
        """
        if note == getattr(self, "_liveWatchLastNote", None):
            return
        self._liveWatchLastNote = note
        try:
            with open(os.path.join(DATA_ROOT, "live_watch.log"), "a",
                      encoding="utf-8") as f:
                f.write("%s  %s\n" % (time.strftime("%m-%d %H:%M:%S"), note))
        except Exception:
            pass

    def _live_apply(self, auto_rename: bool = False, quiet: bool = False,
                    memo_key=None, intent_gen=None):
        """效果名就地换名:只改「效果控件」面板的效果标题,让顶部「效果」菜单保持英文。

        ★★ 2026-11-06 通道重构(用户硬标准:菜单一定是英文,控件标题是中文):
          · 只写 TDB_StreamBase 的显示名缓存(效果名流 + display_off:25.x 是
            0xF0、22.x 是 0xE8),那份 wstring 是「效果控件」标题与界面绘制读的;
          · **绝不写 FLT_FCSpec+0x10**(原 livenames.NameSession)。IG5 反编译
            FLT_FCSpec::GetEffectName_MB @0x180066b90 就是 `return this+0x10`,
            顶部「效果」菜单与新建实例的初始标题全从这儿读 —— 上一版写它,
            所以菜单跟着一起变中文。现在对它只读不写。
          · 不再调 aejsx.project_names(AfterFX.exe -r 脚本转发,25 秒超时;
            用户 AE 2025 的 Pref_SCRIPTING_FILE_NETWORK_SECURITY=0 时必败),
            名字锚点改用「注册表快照里属于选区插件的英文名」。
        · auto_rename: 调用方标记「自动路径」,用于日志/记账,不再串跑脚本换名。
        · quiet: 自动路径 —— 「已经全部是中文」这类无事可做保持安静,
          硬失败(异常)仍会报错(同会话只报第一次)。
        · memo_key: 自动补写的会话账本键 (pid, creation_time)。
          ★ 成功(含「无事可做」)才记账;失败不记,下一拍重试 ——
          2026-11-01 事故:原版先记账后写入,一次瞬时失败就永久放弃该会话。
        · intent_gen: 用户点击产生的意图代号。OFF 一到，apply 在每个写入点
          前都会发现代号变了并停止，避免旧 ON 线程在新 OFF 之后把中文写回。
          手动路径不传（None）时保持旧语义。
        """
        if getattr(self, "_liveShutdown", False):
            return
        _epoch = int(getattr(self, "_liveSessionEpoch", 0))

        def table_for(allowed):
            """{英文原名: 中文} —— 只含选区里勾了「效果名」通道的插件。

            ★ 两个名字来源,合并成一张表:
              1. 运行时效果注册表的显示名(只读 !)。本版起 +0x10 一个字节都不写,
                 所以它**永远是英文原名**,和内存里的流名字一字不差(重复实例的
                 " 2" 后缀由 LiveParams.translate 兜)。
              2. 磁盘上这些插件的 PiPL 显示名(库存里已缓存的字段,毫秒级)。
                 注册表读不到时(构建没适配、AE 正在切换状态)靠它兜住,
                 绝不因为这一步失败就让整条通道罢工。
            """
            from core.effects import resolve_effect_name_instance

            mapping = self._live_mapping()
            if not mapping:
                return {}
            names = []
            complete = False
            try:
                from core import livenames

                snap = livenames.read_live(DATA_ROOT)
                for r in snap.get("effects") or []:
                    if allowed and r.get("matchName") not in allowed:
                        continue
                    disp = (r.get("displayName") or "").strip()
                    if disp:
                        names.append(disp)
                complete = True
            except Exception:
                pass                            # 读不到就只用 PiPL 名,不影响主流程
            names.extend(self._live_plugin_names(allowed))
            table = {}
            for disp in names:
                if not disp or disp in table:
                    continue
                zh = resolve_effect_name_instance(disp, "", mapping)
                if not zh:
                    zh = mapping.get(disp)
                if zh and zh != disp:
                    table[disp] = zh
            # 「这一份表是不是完整的」——注册表没读到(首个拍 AE 还没加载完)
            # 时只算半份,不能缓存给整个会话用,否则那些只在注册表里出现的
            # 名字会一直缺译。
            return table, complete

        def _cancelled() -> bool:
            # AE 会话变化后，旧任务的地址/结果一律作废；intent_gen 只在
            # 自动路径存在时再做最后意图校验（手动 None 保持旧语义）。
            if int(getattr(self, "_liveSessionEpoch", 0)) != _epoch:
                return True
            return (intent_gen is not None
                    and not self._live_intent_ok(intent_gen, True))

        def work():
            from core import liveparams
            from core.proc import Process

            _t0 = time.monotonic()
            if _cancelled():
                return {"cancelled": True, "written": 0, "records": []}
            # ★ 自愈(2026-10-28):构建没适配就先自动适配,再写入 —— 用户
            #   不需要先去引擎卡点「自动识别与适配」
            if intent_gen is None:
                self._ensure_adapted()
            if _cancelled():
                return {"cancelled": True, "written": 0, "records": []}
            _t1 = time.monotonic()
            allowed = self._live_allowed_matchnames()
            if not allowed:
                raise RuntimeError("还没有勾选开了「效果名」通道的插件(翻译页左栏)")
            procs = self._live_one_ae()
            if procs is None:
                raise RuntimeError("没有唯一的运行中 AE,等 AE 启动完成再试")
            pid = int(procs["pid"])
            # ★ 名字表按 (AE 会话, 词典版本, 选区) 缓存:注册表 + PiPL 名在一个
            #   AE 会话里都不会变(本版一个字节都不往注册表写),0.4 秒一拍没必要
            #   每拍把 AE 的注册表再读一遍。
            key_t = (pid, getattr(self._store, "revision", 0),
                     tuple(sorted(allowed)))
            cache = getattr(self, "_liveTableCache", None)
            if cache is not None and cache[0] == key_t:
                table = cache[1]
            else:
                table, complete = table_for(allowed)
                if complete:
                    # 只在拿到注册表快照时缓存:半份表缓存住会让缺译的名字
                    # 一直缺到下次改词典。
                    self._liveTableCache = (key_t, table)
                else:
                    self._liveTableCache = None
            if not table:
                return {"applied": {}, "unchanged": [], "skipped": [],
                        "noopReason": "选区里没有可写入的译名(词典没命中,或已经全部是中文)"}
            if _cancelled():
                return {"cancelled": True, "written": 0, "records": []}
            _t2 = time.monotonic()
            if _cancelled():
                return {"cancelled": True, "written": 0, "records": []}
            with Process(pid, True) as p:
                # ★ data_root 必须给:profile(25.x 的 display_cache=0xF0 /
                #   root_vtable=0xC5A70)在本机适配文件里,不给就退回 2022 的
                #   0xE8/0x978F8,一个流都扫不到 —— 这正是「2025 上整条通道
                #   静默空转」的根因。
                # ★★ 2026-11-07 真机定案（截屏 + 内存普查双证）：写的是**效果
                #   实例**的 +display_off 显示名 wstring 缓存（25.5 = 0xF0）。
                #   ① 实例的 +0x10 根本不是名字（普查 40 条实例全是乱码），
                #      旧版拿 +0x10 当判据 → 一个实例都认不出（日志 inst=0）；
                #   ② AE 会将 +0x10 序列化到工程的 fnma chunk，重开工程会保留译名。
                #      +0xF0 只保留在运行时，适合临时更改标题。
                #   ③ 面板标题实测跟着 +0xF0 变（2026-11-07 13:2x 截屏）。
                #   注册表条目（vtable = flt+fc_vtable）只读不写 → 顶部菜单英文。
                _t3 = time.monotonic()
                nt = liveparams.EffectNameTable(p, table, data_root=DATA_ROOT)
                _t4 = time.monotonic()
                res = nt.apply(sorted(table), cancel=_cancelled)
                _t5 = time.monotonic()
                _cost = (_t1 - _t0, _t2 - _t1, _t3 - _t2, _t4 - _t3, _t5 - _t4)
                if max(_cost) > 0.5:
                    self._live_watch_log(
                        "耗时拆解: 适配=%.1fs 名字表=%.1fs 开进程=%.1fs 建表=%.1fs 写入=%.1fs"
                        % _cost)
                diag = liveparams.effect_diag()
            if res.get("cancelled"):
                return {"cancelled": True, "written": int(res.get("written") or 0),
                        "records": res.get("records") or [], "diag": ""}
            if res.get("noopReason"):
                return {"cancelled": False, "written": 0, "records": [],
                        "noopReason": res.get("noopReason"), "diag": ""}
            records = res.get("records") or []
            if records:
                # 还原账本(本程序数据目录,不含任何 AE 文件):关通道时按它写回英文
                liveparams.save_session(DATA_ROOT, pid, records)
            written = int(res.get("written") or 0)
            # 启动时的注册表快照会漏掉新实例的名称。用完整词典查找本轮未译名称，
            # 同一轮重新写入。实测延迟从约一分钟降到一轮（0.4 秒）。
            _un0 = [n for n in (res.get("untranslated") or []) if n]
            if _un0 and not _cancelled():
                try:
                    _mapping = self._live_mapping() or {}
                    _extra = {}
                    for _n in set(_un0):
                        _zh = _mapping.get(_n)
                        if not _zh:
                            try:
                                from core.effects import resolve_effect_name_instance as _r
                                _zh = _r(_n, "", _mapping)
                            except Exception:
                                _zh = ""
                        if _zh and _zh != _n:
                            _extra[_n] = _zh
                    if _extra:
                        table.update(_extra)
                        self._liveTableCache = None
                        if _cancelled():
                            return {"cancelled": True, "written": written,
                                    "records": res.get("records") or [], "diag": ""}
                        with Process(pid, True) as _p2:
                            _nt2 = liveparams.EffectNameTable(
                                _p2, table, data_root=DATA_ROOT)
                            _r2 = _nt2.apply(sorted(table), cancel=_cancelled)
                        if _r2.get("cancelled"):
                            return {"cancelled": True, "written": written,
                                    "records": res.get("records") or [], "diag": ""}
                        _w2 = int(_r2.get("written") or 0)
                        written += _w2
                        _rec2 = _r2.get("records") or []
                        if _rec2:
                            res["records"] = (res.get("records") or []) + _rec2
                            liveparams.save_session(DATA_ROOT, pid, _rec2)
                        self._live_watch_log(
                            "补译新效果名(%d):%s"
                            % (len(_extra), ",".join(sorted(_extra)[:12])))
                except Exception as _e2:
                    self._live_watch_log("补译失败:%s" % str(_e2)[:140])
            # 同时记录本轮实例与无译名效果，便于区分缺词和写入失败。
            _un = [n for n in (res.get("untranslated") or []) if n]
            if _un:
                self._live_watch_log(
                    "未译效果名(%d):%s" % (len(set(_un)),
                                          ",".join(sorted(set(_un))[:24])))
            _uk = [n for n in (res.get("unknown") or []) if n]
            if _uk:
                _sig = ",".join(sorted(set(_uk))[:24])
                if _sig != getattr(self, "_liveUnknownSig", ""):
                    self._liveUnknownSig = _sig
                    self._live_watch_log("未识别效果名(%d):%s" % (len(set(_uk)), _sig))
            _nm = [n for n in (res.get("names") or []) if n]
            if _nm:
                # 只在名单变化时写一行（每 0.4 秒刷一次同样的长名单会拖慢磁盘）
                _sig = ",".join(sorted(set(_nm)))
                if _sig != getattr(self, "_liveNameSig", ""):
                    self._liveNameSig = _sig
                    self._live_watch_log(
                        "本拍实例(%d):%s"
                        % (len(set(_nm)), ",".join(sorted(set(_nm))[:24])))
            if written:
                # 只对 CECTitle pane 做原生失效重绘，绝不再 SetCursorPos/鼠标。
                try:
                    self._live_nudge_after_patch()
                except Exception as _e:
                    self._live_watch_log("CECTitle 失效失败:%s" % str(_e)[:120])
            # ★ 诊断串:没有它,「扫不到流」和「已经是中文」在日志里一模一样,
            #   2026-11-06 那版就是靠这一条看出「0 个流」的。
            from core import liveparams as _lp
            diag_txt = ("|inst=%s reg=%s other=%s disp=%s miss=%s %s cand=%s "
                        "scan=%.0fMB/%.2fs skip=%.0fMB") % (
                diag.get("inst") or 0, diag.get("reg") or 0,
                diag.get("other") or 0, diag.get("disp") or 0,
                diag.get("dispmiss") or 0,
                diag.get("scan") or "-", int(res.get("candidates") or 0),
                float(_lp._LAST_EFFECT_SCAN.get("mb") or 0),
                float(_lp._LAST_EFFECT_SCAN.get("secs") or 0),
                float(_lp._LAST_EFFECT_SCAN.get("skip_mb") or 0))
            if not written:
                # 已经全是中文(或本拍没有新的实例)—— 自动路径保持安静,
                # 手动点「应用」时才提示一句。绝不在这里写注册表兜底。
                return {"applied": {}, "unchanged": [], "skipped": [],
                        "written": 0,
                        "already": int(res.get("already") or 0),
                        "diag": diag_txt,
                        "noopReason": "效果标题已经是中文(本拍无改动)"}
            applied = {r["english"]: r["zh"] for r in records}
            return {"applied": applied, "unchanged": [], "skipped": [],
                    "written": written, "already": int(res.get("already") or 0),
                    "diag": diag_txt}

        def note_fail(msg):
            # 旧 AE 会话/旧 ON 代的失败不能清新会话的 catchup/inflight，
            # 也不能弹出旧错误。
            if int(getattr(self, "_liveSessionEpoch", 0)) != _epoch:
                self._live_watch_log("忽略旧 AE 会话的补写失败")
                return
            if (intent_gen is not None
                    and not self._live_intent_ok(intent_gen, True)):
                self._live_watch_log("忽略旧代自动补写失败(gen=%s)" % intent_gen)
                return
            # 自动路径:同一会话第一次失败出声,之后静默重试(下一拍再试)
            self._liveAutoInflight = False
            if (intent_gen is not None
                    and getattr(self, "_liveHookCatchupGen", None) == intent_gen):
                self._liveHookCatchupGen = None  # 允许下一拍 resume 后重试
            if memo_key is None:
                self.toast.emit("error", msg.splitlines()[0][:200])
                return
            first = memo_key not in self._liveAutoNoisy
            self._liveAutoNoisy.add(memo_key)
            if first:
                self.toast.emit("error", "效果名自动补写失败(会自动重试):"
                                + msg.splitlines()[0][:160])
            self._live_watch_log("失败,将重试(pid=%s):%s"
                                 % (memo_key[0], msg.splitlines()[0][:120]))

        def done(res):
            # 状态修改前先拒绝旧会话/旧代；失败/完成都不能改新会话标记。
            if int(getattr(self, "_liveSessionEpoch", 0)) != _epoch:
                self._live_watch_log("忽略旧 AE 会话的补写完成")
                return
            # 旧 ON 的完成结果不能在用户 OFF/新 ON 之后覆盖状态或弹提示。
            # 只有真正未绑定代次的手动调用（intent_gen=None）保持旧语义。
            if (intent_gen is not None
                    and not self._live_intent_ok(intent_gen, True)):
                self._live_watch_log("忽略旧代自动补写完成(gen=%s)" % intent_gen)
                return
            self._liveAutoInflight = False
            if res.get("cancelled"):
                # 用户已经点了别的意图；这不是错误，也不要 toast。
                self._live_watch_log("自动补写已取消(意图代号已变)")
                return
            reason = res.get("noopReason") or ""
            show_effect_feedback = not self._engineSession.stop_status
            if reason:
                if "parade" in reason:
                    # roots 还没热身：允许 catchup 下一拍再排一次，别永久记账。
                    if (intent_gen is not None
                            and getattr(self, "_liveHookCatchupGen", None) == intent_gen):
                        self._liveHookCatchupGen = None
                    wait_msg = "效果名同步中，稍后自动补写"
                    if show_effect_feedback and getattr(self, "_status", "") != wait_msg:
                        self._status = wait_msg
                        self.stateChanged.emit()
                    if show_effect_feedback and not quiet:
                        self.toast.emit("info", wait_msg)
                    self._live_watch_log(
                        "效果名同步等待(gen=%s):%s" % (intent_gen, reason))
                    return
                if not quiet:
                    self.toast.emit("warn", reason)
            else:
                applied = res.get("applied") or {}
                unchanged = res.get("unchanged") or []
                skipped = res.get("skipped") or []
                parts = ["实时写入 %d 条" % len(applied)]
                if unchanged:
                    parts.append("%d 条已是中文" % len(unchanged))
                if skipped:
                    parts.append("%d 条未加载" % len(skipped))
                msg = "、".join(parts)
                if show_effect_feedback:
                    self._status = msg
                    self.stateChanged.emit()
                    self.toast.emit("info", msg
                                    + "(面板已刷新;AE 重启后会自动补写,不用再拨开关)")
                problem = res.get("unwritable")
                if problem and problem.get("message"):
                    self.toast.emit("warn", problem["message"])
                # ★ 自动化(2026-10-29):写完自动刷新「效果和预设」面板
                try:
                    self.refreshPalette()
                except Exception:
                    pass
            # ★ 2026-11-06 起不再「成功即永久记账」:注册表不写之后,新建的
            #   效果实例出生就是英文名,只能靠每一拍就地换名(见 _liveWatchTick)。
            #   这里只把「这个会话我们已经看过」写进诊断日志,供排障用。
            if memo_key is not None:
                self._liveAutoNoisy.discard(memo_key)
                try:
                    from core import liveparams as _lp

                    scan = getattr(_lp, "_LAST_SCAN_SECS", 0.0)
                    mb = getattr(_lp, "_LAST_HEAP_MB", 0.0)
                except Exception:
                    scan = mb = 0.0
                # 只有真的做了全量重扫（>50ms）才记一行，免得日志每秒刷。
                extra = res.get("diag") or ""
                if scan > 0.05:
                    extra += "|重扫%.2fs/%.0fMB" % (scan, mb)
                # 60ms 一拍，完成行不能每拍都写（IO 会把看护拖慢）。
                # 只在真有写入、或每 5 秒报一次平安时落盘。
                import time as _t
                _last_done = getattr(self, "_liveDoneLogAt", 0.0)
                _now_done = _t.time()
                if int(res.get("written") or 0) or _now_done - _last_done > 5.0:
                    self._liveDoneLogAt = _now_done
                    # ★ 时间戳保证 note 唯一 —— _live_watch_log 会按内容去重，
                    #   固定字符串会让「每 5 秒一行」的心跳从第二次起全部丢失。
                    self._live_watch_log(
                        "完成 %s(pid=%s):%s%s" % (_t.strftime("%H:%M:%S"),
                                                  memo_key[0],
                                                  reason or ("%d 条"
                                                             % len(res.get("applied") or {})),
                                                  extra))

        # ★ 自动路径(quiet)一律静默执行:0.4 秒一拍,不能每拍点亮全局忙碌态。
        self._run(work, done, _busy_text="正在实时写入效果名…",
                  _silent=bool(quiet),
                  _toast_fail=memo_key is None,
                  _on_fail=note_fail if memo_key is not None else None)

    def _live_restore(self, intent_gen=None):
        """关掉效果名通道:把内存里被换成中文的效果标题写回英文原名。

        ★ 2026-11-07 真机定案：面板标题读的是**效果实例** +display_off 的
          UTF-16 显示名缓存（截屏 + 内存普查双证），所以这里写的也是它；
          +0x10 一个字节都不写（写它会被 AE 存进工程，正是上一版「关掉通道
          重进 AE 仍是中文」的原因）。
        ★ 两步：① 按会话账本写回本会话改过的；② revert() 扫一遍把「工程
          文件里带进来的中文」也写回英文（那批账本里没有）。FLT_FCSpec
          注册表条目（vtable = flt+fc_vtable）从来没写过，顶部菜单不需要还原。
        ★ intent_gen：OFF 点击的代号。用户马上又点 ON 时，这里每一步都会
          发现代号已变并放弃，不会把中文又写回英文。
        """
        # 先停挂钩，再还原名称。读路径的挂钩仍在时，会在下一次读取时恢复译名。
        # ★ 2026-11-09：手动 liveRestore 没有代号，force=True 保持原语义；
        #   开关路径传了代号，由 _live_hook_stop 自己按最新意图判断。
        self._live_hook_stop("restore", force=(intent_gen is None))

        def _cancelled() -> bool:
            return (intent_gen is not None
                    and not self._live_intent_ok(intent_gen, False))

        def work():
            from core import liveparams
            from core.proc import Process

            if _cancelled():
                return {"cancelled": True, "restored": 0}
            session = liveparams.load_session(DATA_ROOT)
            records = [r for r in (session.get("records") or []) if r.get("obj")]
            if not records:
                return {"restored": 0, "skipped": 0, "oldProcess": False}
            pid = int(session.get("pid") or 0)
            try:
                from core import proc as _proc

                procs = _proc.list_ae_processes()
            except Exception:
                procs = []
            if not procs:
                # AE 已经退出:内存里的改动随进程消失,账本作废即可
                liveparams.clear_session(DATA_ROOT)
                return {"restored": 0, "skipped": 0, "oldProcess": True}
            if int(procs[0]["pid"]) != pid:
                liveparams.clear_session(DATA_ROOT)
                return {"restored": 0, "skipped": 0, "oldProcess": True}
            if _cancelled():
                return {"cancelled": True, "restored": 0}
            mapping = self._live_mapping() or {}
            table = {k: v for k, v in mapping.items() if k and v and v != k}
            with Process(pid, True) as p:
                nt = liveparams.EffectNameTable(p, table, data_root=DATA_ROOT)
                out = nt.restore(records, cancel=_cancelled)
                if _cancelled() or out.get("cancelled"):
                    return {"cancelled": True, "restored": 0}
                extra = nt.revert(cancel=_cancelled)  # 工程里带进来的中文也要清
            if _cancelled() or (extra or {}).get("cancelled"):
                return {"cancelled": True, "restored": 0}
            if int(out.get("restored") or 0) or int((extra or {}).get("restored") or 0):
                try:
                    self._live_nudge_after_patch()
                except Exception:
                    pass
            liveparams.clear_session(DATA_ROOT)
            out = dict(out or {})
            out["restored"] = (int(out.get("restored") or 0)
                               + int((extra or {}).get("restored") or 0))
            return out

        def done(res):
            if res.get("cancelled"):
                self._live_watch_log("关通道还原已取消(新意图已到)")
                return
            restored = int(res.get("restored") or 0)
            if res.get("oldProcess"):
                self.toast.emit("info", "上次的实时写入属于已退出的 AE,记录已清理")
            elif restored:
                self.toast.emit("info", "已还原 %d 条效果名" % restored)
            else:
                self.toast.emit("info", "没有需要还原的记录")

        self._run(work, done, _busy_text="正在还原实时效果名…")

    @Slot()
    def refreshPalette(self):
        """刷新 AE「效果和预设」面板:纯窗口消息,不动鼠标、不抢前台。"""

        def work():
            from core import palette, proc

            procs = proc.list_ae_processes()
            if len(procs) != 1:
                raise RuntimeError("AE 未运行或开了多个,无法定位面板")
            return palette.refresh(procs[0]["pid"], timeout=10, data_root=DATA_ROOT)

        def done(_res):
            self.toast.emit("info", "已刷新「效果和预设」面板")

        self._run(work, done, _busy_text="正在刷新效果面板…")

    # ---- AE 构建自动适配 ----

    def _running_ae_version(self, procs=None) -> str:
        """唯一运行中 AE 的构建号;没有/多个返回空串。"""
        try:
            from core import proc

            if procs is None:
                procs = proc.list_ae_hosts()
            return procs[0].get("fileVersion") or "" if len(procs) == 1 else ""
        except Exception:
            return ""

    def _adapt_running_ae(self, *, force=False) -> dict:
        with _ADAPTER_LOCK:
            return self._adopt_current_ae(force=force)

    def _adopt_current_ae(self, *, force=False) -> dict:
        """对唯一运行中的 AE 执行完整适配链(同步,须在工作线程调)。

        静态识别 → 目录探针 → 交叉校验 →
        adopt_candidate 启用。失败抛异常,由调用方决定呈现。

        ★ 2026-10-28 实测修复:必须分析【运行中 AE 的】AfterFX.exe ——
          原来用 self._aeRoot()(应用选中的版本),运行的是 2025、应用选的
          是 2026 时,静态识别与运行中 FLT 哈希对不上,版本守卫正确拦截但
          永远适配不了。运行中进程的 image 路径就是那个 AfterFX.exe。
        """
        from core import aescan, adapter as _adapter

        from core import proc as _proc

        procs = _proc.list_ae_hosts()
        if len(procs) != 1:
            raise RuntimeError("需要恰好一个运行中的 AE 才能自动适配"
                               "(当前 %d 个)" % len(procs))
        exe = procs[0]["image"]
        try:
            _proc.main_window(procs[0]["pid"])
        except RuntimeError:
            return {"loading": True}
        from runtime import build_profile
        from core import paramnative

        cached = None
        with _proc.Process(procs[0]["pid"], write=False) as p:
            if p.creation_time != procs[0]["creation_time"]:
                raise RuntimeError("AE 在适配期间发生变化，请重试")
            modules = {m["name"].casefold(): m for m in p.modules()}
            flt = modules.get("flt.dll")
            draw = modules.get("afterfxlib.dll")
            if not flt or not draw:
                return {"loading": True}
            if not force:
                digest = _proc.module_digest(p, flt["path"])
                version = _proc.file_version(p.image)
                if _adapter.is_supported(version, digest, DATA_ROOT):
                    cached = _adapter.adapted_runtime_profile(p, modules.values(), DATA_ROOT)
                    known_draw = build_profile.known_version("afterfxlib", _proc.module_digest(p, draw["path"]))
                    tdb = modules.get("tdb.dll")
                    if build_profile.known_version("flt", digest) and (known_draw or cached):
                        return {}
                    if (cached and cached.popup_layout_verified and tdb
                            and _proc.module_digest(p, tdb["path"]) == cached.tdb_sha):
                        media = modules.get("mediafoundation.dll")
                        if (not cached.popup_media_sha or (media and
                                _proc.module_digest(p, media["path"]) == cached.popup_media_sha)):
                            return {}

        report = aescan.analyze_install(exe)
        if report.get("state") != "candidate":
            return {"blocked": report.get("summary") or "静态识别失败"}
        profile = aescan.candidate_from_report(report)
        popup_only = bool(cached and cached.root_vtable and cached.count_get
                          and cached.tdb_sha and cached.tdb_sha == profile.tdb_sha
                          and cached.parameter_draw_sha == profile.parameter_draw_sha
                          and cached.flt_sha == profile.flt_sha)
        popup_error = report.get("popupLayoutError", "")
        if popup_only and not profile.popup_layout_verified:
            return {"popup_blocked": popup_error or "下拉布局未通过校验"}
        with _proc.Process(procs[0]["pid"], write=False) as p:
            if (p.creation_time != procs[0]["creation_time"]
                    or os.path.normcase(p.image) != os.path.normcase(exe)
                    or _proc.file_version(p.image) != profile.version):
                raise RuntimeError("AE 在适配期间发生变化，请重试")
            modules = {m["name"].casefold(): m for m in p.modules()}
            flt = modules.get("flt.dll")
            if flt is None:
                raise RuntimeError("运行中的 AE 里没有 FLT.dll")
            if _proc.module_digest(p, flt["path"]) != profile.flt_sha:
                raise RuntimeError("运行中的 AE 与扫描的安装文件不同，请重试")
            if not popup_only:
                catalog = paramnative.probe_catalog(p, timeout=60)
            p.ensure_alive()
            hosts = _proc.list_ae_hosts()
            if len(hosts) != 1 or hosts[0] != procs[0]:
                raise RuntimeError("AE 编辑会话在适配期间发生变化，将自动重试")
            loaded = {m["name"].casefold(): m for m in p.modules()}
            for mod, expected in (("flt.dll", profile.flt_sha),
                                  ("afterfxlib.dll", profile.parameter_draw_sha),
                                  ("tdb.dll", profile.tdb_sha),
                                  ("mediafoundation.dll", profile.popup_media_sha)):
                if not expected:
                    continue
                m = loaded.get(mod)
                if (not m or m != modules.get(mod)
                        or os.path.normcase(os.path.dirname(m["path"])) != os.path.normcase(os.path.dirname(flt["path"]))
                        or _proc.module_digest(p, m["path"]) != expected):
                    raise RuntimeError("AE 模块在适配期间不一致：" + mod)
            if popup_only:
                _adapter.save_local(DATA_ROOT, {cached.version: replace(
                    cached, popup_layout_verified=True, popup_media_sha=profile.popup_media_sha)})
                layout = {"offset": cached.registry_vector, "encoding": cached.encoding}
            else:
                layout = _adapter.adopt_candidate(p, flt["base"], profile, catalog, DATA_ROOT)
            return {"adopted": True, "version": profile.version,
                    "scope": bool(report.get("paramsSupported")),
                    "parameter_draw_error": report.get("parameterDrawError", ""),
                    "popup_error": popup_error, "popup_upgraded": popup_only,
                    "offset": layout["offset"], "encoding": layout["encoding"]}

    def _auto_adapt_async(self, version: str, force: bool = False, *, hosts=None) -> None:
        """Verify one live AE session in the background, with bounded retry frequency."""
        if (not version or self._busy or getattr(self, "_liveShutdown", False)
                or getattr(self, "_autoAdaptRunning", False)):
            return
        if hosts is None:
            from core import proc

            hosts = proc.list_ae_hosts()
        if len(hosts) != 1 or hosts[0].get("fileVersion") != version:
            return
        host = hosts[0]
        key = (host["pid"], host["creation_time"], host["image"])
        if key != getattr(self, "_autoAdaptKey", None):
            self._autoAdaptKey = key
            self._autoAdaptNext = 0.0
            self._autoAdaptStatus = ""
            self._autoAdaptPopupError = ""
        if not force and time.monotonic() < self._autoAdaptNext:
            return
        self._autoAdaptRunning = True

        def work():
            return self._adapt_running_ae(force=force)

        def failed(message):
            self._autoAdaptRunning = False
            self._autoAdaptNext = time.monotonic() + 30.0
            detail = (message.strip().splitlines() or ["目录验证失败"])[0][:150]
            status = f"AE {version} 适配等待重试：{detail}"
            if self._autoAdaptStatus != status:
                self._live_watch_log(status)
                self._autoAdaptStatus = status
                self.stateChanged.emit()

        def done(res):
            self._autoAdaptRunning = False
            self._autoAdaptNext = float("inf")
            self._autoAdaptStatus = ""
            self._autoAdaptPopupError = res.get("popup_blocked") or res.get("popup_error", "")
            if res.get("loading"):
                self._autoAdaptNext = time.monotonic() + 2.0
            elif res.get("blocked"):
                self._autoAdaptStatus = f"AE {version} 适配未通过：{res['blocked']}"
                self._live_watch_log(self._autoAdaptStatus)
            elif res.get("adopted"):
                reason = res.get("parameter_draw_error")
                if reason:
                    self._autoAdaptStatus = f"AE {version} 参数名绘制校验未通过：{reason}"
                self._live_watch_log(self._autoAdaptStatus or "已自动适配 AE " + res["version"])
                self.toast.emit(
                    "warn" if reason else "info",
                    self._autoAdaptStatus or "已验证 AE %s 的本机适配（编码 %s）"
                    % (res["version"], res["encoding"]))
                if reason:
                    QTimer.singleShot(0, self._auto_restore_engine_async)
                else:
                    QTimer.singleShot(0, lambda: self._auto_restore_engine_async(
                        refresh_runtime=True, refresh_popup=bool(res.get("popup_upgraded"))))
            self.stateChanged.emit()

        self._run(work, done, _silent=True, _toast_fail=False, _on_fail=failed)

    def _ensure_adapted(self) -> None:
        """实时写入路径的自愈(在工作线程调):没适配就先自动适配,
        失败抛异常(消息足够明确)。"""
        version = self._running_ae_version()
        if not version:
            return
        from core import adapter as _adapter

        if _adapter.is_supported(version, data_root=DATA_ROOT):
            return
        res = self._adapt_running_ae()
        if res.get("blocked"):
            raise RuntimeError(res["blocked"])
        if res.get("adopted"):
            self.toast.emit("info", "已自动适配 %s(编码 %s)"
                            % (res["version"], res["encoding"]))

    @Slot()
    def corpusFullDiff(self):
        """一次性对照运行时语料和词典，列出各效果的参数与下拉缺词。

        语料对照离线运行，无需在 AE 工程中添加效果。
        按第三方出现频次降序;结果并入补词列表(catalogItems)。
        """

        def work():
            year = self._version
            dump_dir = os.environ.get("AELOC_PARAM_DUMP_DIR") or os.path.join(DATA_ROOT, "param_dumps")
            path = os.path.join(dump_dir, "dump_%s.json" % year)
            if not os.path.isfile(path):
                # 2026-11-01 全版本化:无全量语料的年份(2021-2024)改用
                # 引擎采集对照 —— 守护把见过的参数名/下拉缺词都采集在案,
                # 同样能给出缺译清单(覆盖范围=引擎开着的时段所见,随使用增长)
                cap = self._capture_missing_core()
                items = [{"en": it["en"], "kind": it["kind"],
                          "effect": "引擎采集"}
                         for it in cap.get("items") or []]
                return {"items": items, "effects": 0, "total": len(items),
                        "local": True, "unsupported": year}
            data = json.load(open(path, encoding="utf-8"))
            effects = data.get("effects") or {}
            resolved = self._store.resolve()
            fx = {r.en for r in self._nameRows if getattr(r, "en", None)}
            import re as _re

            numeric = _re.compile(r"^[\s\d.\-+%°/:(),xX×÷]*$")

            def has_cjk(s):
                return any("\u3400" <= c <= "\u9fff" for c in s)

            def keep(s):
                s = " ".join(str(s or "").split())
                if not (2 <= len(s) <= 60) or has_cjk(s):
                    return ""
                if not any(c.isalpha() for c in s):
                    return ""
                if s in resolved or numeric.match(s):
                    return ""
                if "%" in s or "{" in s or "://" in s:
                    return ""
                if _re.search(r"\.[a-z]{2,4}$", s, _re.IGNORECASE):
                    # 贴图/预设文件名(ScriptEP 的 *.jpg 等),不是 UI 文案
                    return ""
                return s

            freq = {}
            where = {}
            kind = {}
            for match, info in (effects.items() if isinstance(effects, dict) else
                                [(x.get("matchName", "?"), x) for x in effects]):
                for prm in info.get("params") or []:
                    nm = keep(prm.get("name"))
                    if nm:
                        freq[nm] = freq.get(nm, 0) + 1
                        where.setdefault(nm, match)
                        kind.setdefault(nm, "param")
                    for it in prm.get("items") or []:
                        s = keep(it if isinstance(it, str) else
                                 (it or {}).get("name", ""))
                        if s and not _is_junk_enum(s):
                            freq[s] = freq.get(s, 0) + 1
                            where.setdefault(s, match)
                            kind.setdefault(s, "enum")
            items = [{"en": k, "kind": kind.get(k, "param"), "effect": where[k]}
                     for k in sorted(freq, key=lambda x: -freq[x])]
            # 内部标签/效果名碰撞排尾不丢弃(2026-11-01):平时不碍眼,真有合法同名值也不丢
            def _tail(it):
                return _is_junk_enum(it["en"]) or it["en"] in fx
            items = ([it for it in items if not _tail(it)]
                     + [it for it in items if _tail(it)])
            return {"items": items, "effects": len(effects),
                    "total": sum(freq.values())}

        def done(res):
            if res.get("unsupported"):
                self._catalogItems = res["items"]
                self._catalogSeen = res["total"]
                self.catalogChanged.emit()
                self.toast.emit(
                    "info", "%s 没有全量语料(只有 2025/2026 配了),已改用"
                            "引擎采集对照:缺译 %d 个(覆盖=引擎开着的时段)"
                    % (res["unsupported"], len(res["items"])))
                self.stateChanged.emit()
                return
            self._catalogItems = res["items"]
            self._catalogSeen = res["total"]
            self.catalogChanged.emit()
            self.toast.emit("info", "语料全量对照:%d 效果 · %d 个串,缺译 %d 个"
                            % (res["effects"], res["total"], len(res["items"])))
            self.stateChanged.emit()

        self._run(work, done, _busy_text="正在对照语料全量…")

    @Slot()
    def captureCatalog(self):
        """全量枚举运行中 AE 的参数名(只读,主动式采集)。

        与 captureMissingParams 的被动采集互补:不需要用户逐个展开参数。
        两条通路自动选择(2026-10-29):
          ① 内存扫描(paramcatalog)—— 布局已适配的构建,全量已加载效果;
          ② 脚本桥(run_native catalog)—— 仅效果名布局的构建(如 25.5.0.4)
             的兜底,不需要任何内存布局,拿工程里已应用效果的参数。
        同样排除占位名/效果名/已有译名/已中文。
        """

        def work():
            from core import paramcatalog, paramnative, proc
            from core.diag import is_placeholder

            procs = proc.list_ae_processes()
            if len(procs) != 1:
                raise RuntimeError("需要恰好一个运行中的 AE(当前 %d 个)" % len(procs))
            resolved = self._store.resolve()
            rows = []
            source = ""
            with proc.Process(procs[0]["pid"], write=False) as p:
                try:
                    rows = paramcatalog.enumerate_parameters(p, DATA_ROOT)
                    # ★ 下拉项采集(ENUMCAP-20261029):与守护同源偏移,读每条
                    #   参数流上的枚举定义串('A|B|C'),逐项并入 —— 之前只采集
                    #   参数名,下拉缺词(Glitchify 传输模式 46 项)永远看不见
                    try:
                        cat = paramcatalog.ParameterCatalog(p, DATA_ROOT)
                        seen_item = set()
                        for pr in cat.popup_rows():
                            nm = pr["item"]
                            if nm in seen_item:
                                continue
                            seen_item.add(nm)
                            rows.append({"effectMatchName": pr["effectMatchName"],
                                         "matchName": "",
                                         "name": nm, "pfName": nm})
                    except Exception:
                        pass
                    source = "内存扫描"
                except RuntimeError as exc:
                    if "未适配参数枚举" not in str(exc):
                        raise
                    # ★ 仅效果名布局的构建:退回脚本桥(零布局依赖)
                    from core import adapter as _adapter

                    prof = _adapter.get_profile(
                        procs[0].get("fileVersion") or "", data_root=DATA_ROOT)
                    res = paramnative.run_native(p, [], action="catalog",
                                                 timeout=150, profile=prof)
                    for r in res.get("parameters") or []:
                        try:
                            effect = json.loads(r.get("id") or "[]")[0]
                        except Exception:
                            effect = ""
                        rows.append({"effectMatchName": effect,
                                     "name": r.get("name") or "",
                                     "pfName": r.get("name") or ""})
                    source = "脚本桥·工程内已应用效果"
            items = []
            for r in rows:
                # ★ 2026-10-28 实测:引擎改写过的参数,原名(pfName)和显示名
                #   在内存里都已是中文(注册期换名 + 热换覆写),且显示名缓存
                #   留着英文尾巴。补词键用【注册原名 pfName】(显示名只作
                #   兜底);已经是中文的行 = 引擎已翻译,没有可补的,跳过。
                raw = r.get("pfName") or r.get("name") or ""
                s = " ".join(str(raw).split())
                if not s or is_placeholder(s) or s.startswith("<"):
                    continue
                if any("\u4e00" <= ch <= "\u9fff" for ch in s):
                    continue
                rec = resolved.get(s)
                if rec is not None and rec.zh:
                    continue
                items.append({"en": s, "kind": "param", "effect": r["effectMatchName"]})
            return {"seen": len(rows), "items": items, "source": source}

        def done(res):
            self._catalogItems = res["items"]
            self._catalogSeen = res["seen"]
            self.catalogChanged.emit()
            self.toast.emit("info", "全量枚举完成(%s):%d 个参数名,缺词 %d 个"
                            % (res.get("source") or "内存扫描",
                               res["seen"], len(res["items"])))

        self._run(work, done, _busy_text="正在枚举 AE 参数目录…")

    @Property("QVariantList", notify=catalogChanged)
    def catalogItems(self):
        return self._catalogItems

    @Property(int, notify=catalogChanged)
    def catalogSeen(self):
        return self._catalogSeen
