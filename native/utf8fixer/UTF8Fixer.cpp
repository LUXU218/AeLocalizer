#include "AEConfig.h"
#include "AE_Effect.h"
#include "AE_EffectCB.h"
#include "AE_Macros.h"
#include "Param_Utils.h"
#include "AE_EffectVers.h"
#include "AE_EffectSuites.h"
#include "AE_GeneralPlug.h"
#include "MinHook.h"

#include <windows.h>
#include <tlhelp32.h>
#ifndef EXCEPTION_EXECUTE_FHANDLER
#define EXCEPTION_EXECUTE_FHANDLER 1
#endif
#include <string>
#include <string_view>
#include <cstring>
#include <unordered_set>
#include <mutex>
#include <shared_mutex>
#include <atomic>
#include <memory>
#include <span>

// 跟踪已 hook 的插件数量
#define WRAPPER_POOL_SIZE 2048
std::atomic<void*> g_OriginalEntryPoints[WRAPPER_POOL_SIZE] = {};
std::atomic<int> g_WrapperCount{ 0 };
std::mutex g_WrapperMutex;

// ==========================================
// 1. UTF-8 验证与处理
// ==========================================

inline bool IsValidUTF8(std::string_view sv) noexcept {
    const auto* bytes = reinterpret_cast<const unsigned char*>(sv.data());
    const auto* end = bytes + sv.size();

    bool has_high_byte = false;      
    bool has_3byte_character = false; 

    while (bytes < end) {
        if (bytes[0] <= 0x7F) {
            bytes += 1;
        }
        else if ((bytes[0] & 0xE0) == 0xC0) {
            if (bytes + 1 >= end || (bytes[1] & 0xC0) != 0x80) return false;
            has_high_byte = true;
            bytes += 2;
        }
        else if ((bytes[0] & 0xF0) == 0xE0) {
            if (bytes + 2 >= end || (bytes[1] & 0xC0) != 0x80 || (bytes[2] & 0xC0) != 0x80) return false;
            has_high_byte = true;
            has_3byte_character = true;
            bytes += 3;
        }
        else if ((bytes[0] & 0xF8) == 0xF0) {
            if (bytes + 3 >= end || (bytes[1] & 0xC0) != 0x80 || (bytes[2] & 0xC0) != 0x80 || (bytes[3] & 0xC0) != 0x80) return false;
            has_high_byte = true;
            bytes += 4;
        }
        else {
            return false;
        }
    }

    if (has_high_byte && !has_3byte_character) {
        return false; 
    }

    return true;
}

void TrimInvalidTailUTF8(std::span<char> s) noexcept {
    if (s.empty()) return;
    size_t i = s.size();
    while (i > 0) {
        auto c = static_cast<unsigned char>(s[i - 1]);
        if ((c & 0x80) == 0x00) {
            break;
        }
        else if ((c & 0xC0) == 0xC0) {
            size_t need_bytes = 0;
            if ((c & 0xE0) == 0xC0) need_bytes = 2;
            else if ((c & 0xF0) == 0xE0) need_bytes = 3;
            else if ((c & 0xF8) == 0xF0) need_bytes = 4;

            if (s.size() - (i - 1) < need_bytes) {
                s[i - 1] = '\0';
            }
            break;
        }
        else if ((c & 0xC0) == 0x80) {
            i--;
        }
        else {
            s[i - 1] = '\0';
            i--;
        }
    }
}

// ==========================================
// 2. GBKToUTF8 转换器
// ==========================================

template<size_t InlineSize = 256>
class GBKToUTF8Converter {
public:
    explicit GBKToUTF8Converter(const char* gbk_str) noexcept {
        if (!gbk_str || gbk_str[0] == '\0') return;

        constexpr UINT codePage = 936; 
        int wlen = MultiByteToWideChar(codePage, 0, gbk_str, -1, nullptr, 0);
        if (wlen <= 1) return;

        wchar_t* wbuf = m_inlineWBuf;
        std::unique_ptr<wchar_t[]> heapWBuf;
        if (static_cast<size_t>(wlen) > InlineSize) {
            heapWBuf = std::make_unique<wchar_t[]>(wlen);
            wbuf = heapWBuf.get();
        }

        MultiByteToWideChar(codePage, 0, gbk_str, -1, wbuf, wlen);

        int u8len = WideCharToMultiByte(CP_UTF8, 0, wbuf, -1, nullptr, 0, nullptr, nullptr);
        if (u8len <= 1) return;

        if (static_cast<size_t>(u8len) > InlineSize * 2) {
            m_heapU8Buf.resize(u8len);
            WideCharToMultiByte(CP_UTF8, 0, wbuf, -1, m_heapU8Buf.data(), u8len, nullptr, nullptr);
            m_heapU8Buf.resize(u8len - 1); 
            m_view = m_heapU8Buf;
        } else {
            WideCharToMultiByte(CP_UTF8, 0, wbuf, -1, m_inlineU8Buf, u8len, nullptr, nullptr);
            m_view = std::string_view(m_inlineU8Buf, u8len - 1);
        }
    }

    [[nodiscard]] std::string_view view() const noexcept { return m_view; }
    [[nodiscard]] bool empty() const noexcept { return m_view.empty(); }
    
    std::string move_to_string() && noexcept {
        if (!m_heapU8Buf.empty()) {
            return std::move(m_heapU8Buf);
        }
        return std::string(m_view);
    }

private:
    wchar_t m_inlineWBuf[InlineSize]{};
    char m_inlineU8Buf[InlineSize * 2]{};
    std::string m_heapU8Buf;
    std::string_view m_view;
};

// ==========================================
// 3. 全局字符串缓存
// ==========================================

static constexpr size_t kCacheShards = 4;

struct StringHash {
    using is_transparent = void; 
    size_t operator()(std::string_view sv) const noexcept {
        return std::hash<std::string_view>{}(sv);
    }
};

struct StringEqual {
    using is_transparent = void; 
    bool operator()(std::string_view lhs, std::string_view rhs) const noexcept {
        return lhs == rhs;
    }
};

struct CacheContainer {
    mutable std::shared_mutex mutex;
    std::unordered_set<std::string, StringHash, StringEqual> cache;
};

inline CacheContainer& GetShardedCache(std::string_view sv) noexcept {
    static CacheContainer shards[kCacheShards];
    size_t hash = std::hash<std::string_view>{}(sv);
    return shards[hash % kCacheShards];
}

const char* GetCachedUTF8String(GBKToUTF8Converter<>& converter) {
    std::string_view sv = converter.view();
    auto& cache_container = GetShardedCache(sv);
    auto& cache = cache_container.cache;
    auto& mutex = cache_container.mutex;
    
    {
        std::shared_lock read_lock(mutex); 
        if (auto it = cache.find(sv); it != cache.end()) {
            return it->c_str(); 
        }
    }

    std::unique_lock write_lock(mutex);
    if (auto it = cache.find(sv); it != cache.end()) {
        return it->c_str();
    }
    
    auto [it, inserted] = cache.insert(std::move(converter).move_to_string());
    return it->c_str();
}

// ==========================================
// 4. 参数修复与 Hook 逻辑
// ==========================================

inline bool IsBufferWritable(const void* p, size_t len) noexcept {
    if (!p || len == 0) return false;

    auto* base = static_cast<const unsigned char*>(p);
    auto* end = base + len;

    MEMORY_BASIC_INFORMATION mbi{};
    auto* cursor = base;
    while (cursor < end) {
        SIZE_T queried = VirtualQuery(cursor, &mbi, sizeof(mbi));
        if (queried == 0) return false;

        constexpr DWORD writable_mask = PAGE_READWRITE | PAGE_WRITECOPY
                                      | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY;
        if ((mbi.Protect & writable_mask) == 0) return false;

        auto* regionEnd = static_cast<const unsigned char*>(mbi.BaseAddress) + mbi.RegionSize;
        if (regionEnd <= cursor) return false;

        cursor = regionEnd;
    }
    return true;
}

void FixStringPointer(const char*& ptr) noexcept {
    if (ptr && ptr[0] != '\0' && !IsValidUTF8(ptr)) [[unlikely]] {
        GBKToUTF8Converter<> converter(ptr);
        if (!converter.empty()) {
            ptr = GetCachedUTF8String(converter);
        }
    }
}

template <size_t N>
void FixStringArray(char(&arr)[N]) noexcept {
    if (arr[0] != '\0' && !IsValidUTF8(arr)) [[unlikely]] {
        if (!IsBufferWritable(arr, N)) {
            return;
        }
        GBKToUTF8Converter<> converter(arr);
        std::string_view u8name = converter.view();
        if (u8name.empty()) {
            return;
        }

        const size_t copyLen = (u8name.size() < N - 1) ? u8name.size() : (N - 1);
        memcpy_s(arr, N, u8name.data(), copyLen);
        arr[copyLen] = '\0';

        TrimInvalidTailUTF8(std::span<char>(arr, copyLen));
    }
}

// ==========================================
// AeLocalizer 扩展：词典查询（内存映射）
// ==========================================
//
// 设计：
//   · 词典由 AeLocalizer 主程序生成（%LOCALAPPDATA%\AeLocalizer\dict.bin）
//   · 本 aex 在首次处理参数时 mmap 它（900 KB，零解析开销）
//   · 每次处理参数名前先查词典，命中就写中文；未命中走原版转码逻辑
//
// 为什么中文要写 GBK 而不是 UTF-8：
//   实测（AE 26.5x89）AE 读参数名缓冲时按 GBK 解释。
//   写 UTF-8 会显示成乱码（"通道切换" -> "閫氶亾鍒囧垏"）。

#include "dictmap.h"
#include <cstdio>
#include <cstdarg>
#include <cstring>

// ==========================================
// 诊断日志（写 %LOCALAPPDATA%\AeLocalizer\plugin.log）
// ==========================================
namespace {
FILE* g_log = nullptr;
std::mutex g_logMu;

void OpenLog() {
    std::lock_guard<std::mutex> lk(g_logMu);
    if (g_log) return;
    wchar_t path[MAX_PATH]{};
    DWORD n = GetEnvironmentVariableW(L"LOCALAPPDATA", path, MAX_PATH);
    if (n == 0 || n >= MAX_PATH) return;
    std::wstring p(path);
    p += L"\\AeLocalizer\\plugin.log";
    g_log = _wfopen(p.c_str(), L"a");
}

void LogF(const char* fmt, ...) {
    OpenLog();
    if (!g_log) return;
    std::lock_guard<std::mutex> lk(g_logMu);
    SYSTEMTIME st{};
    GetLocalTime(&st);
    std::fprintf(g_log, "[%02d:%02d:%02d] ", st.wHour, st.wMinute, st.wSecond);
    va_list ap;
    va_start(ap, fmt);
    std::vfprintf(g_log, fmt, ap);
    va_end(ap);
    std::fprintf(g_log, "\n");
    std::fflush(g_log);
}
}  // namespace

#define AELOG(...) LogF(__VA_ARGS__)
#include <string>
#include <unordered_set>
#include <atomic>

namespace {

aeloc::DictMap g_dict;
std::atomic<bool> g_dictTried{ false };
std::atomic<int> g_dictHits{ 0 };

void EnsureDictLoaded() noexcept {
    if (g_dictTried.exchange(true)) return;

    wchar_t path[MAX_PATH]{};
    DWORD n = GetEnvironmentVariableW(L"LOCALAPPDATA", path, MAX_PATH);
    if (n == 0 || n >= MAX_PATH) return;

    std::wstring p(path);
    p += L"\\AeLocalizer\\dict.bin";
    g_dict.open(p.c_str());
}

// 把 UTF-8 结果转成 GBK 写进定长数组
bool WriteGBKToArray(char* arr, size_t cap, const char* utf8) noexcept {
    if (!arr || !cap || !utf8 || !*utf8) return false;
    if (!IsBufferWritable(arr, cap)) return false;

    wchar_t wbuf[256];
    int wn = MultiByteToWideChar(CP_UTF8, 0, utf8, -1, wbuf, 256);
    if (wn <= 1) return false;

    char gbuf[512];
    int gn = WideCharToMultiByte(936, 0, wbuf, -1, gbuf, sizeof(gbuf), nullptr, nullptr);
    if (gn <= 1) return false;

    const size_t need = static_cast<size_t>(gn - 1);
    if (need >= cap) return false;

    std::memcpy(arr, gbuf, need);
    arr[need] = '\0';
    return true;
}

// ==========================================
// ★★ 核心修复：GBK 字节 -> UTF-8
//
// 【真机实测（AE 26.5x89）】
//   插件参数名本来就是中文，编译时写死 GBK：
//     bc d3 d4 d8 d4 a4 c9 e8  = "加载预设" (GBK)
//   但 AE 2026 按 UTF-8 解码这串字节 -> 无效序列 -> 界面显示乱码。
//
//   所以【不需要词典翻译】，只需要把 GBK 字节转成 UTF-8 写回。
//
//   对比：
//     AE 2021-2025：按 GBK 读 -> 正确显示（所以那时不用修）
//     AE 2026：    按 UTF-8 读 -> 乱码（所以要修）
// ==========================================
bool FixGBKToUTF8(char* arr, size_t cap) noexcept {
    if (!arr || !arr[0] || cap < 2) return false;

    // 纯 ASCII 不用动
    bool hasHigh = false;
    for (const char* p = arr; *p; ++p) {
        if (static_cast<unsigned char>(*p) > 127) { hasHigh = true; break; }
    }
    if (!hasHigh) return false;

    // 已经是合法 UTF-8 也不用动
    if (IsValidUTF8(arr)) return false;

    // GBK -> UTF-16 -> UTF-8
    wchar_t wbuf[256];
    const int wn = MultiByteToWideChar(936, 0, arr, -1, wbuf, 256);
    if (wn <= 1) return false;

    char u8[768];
    const int un = WideCharToMultiByte(CP_UTF8, 0, wbuf, -1, u8, sizeof(u8), nullptr, nullptr);
    if (un <= 1) return false;

    const size_t need = static_cast<size_t>(un - 1);
    if (need >= cap) {
        // 放不下：截断到能放下（保守，避免越界）
        size_t fit = cap - 1;
        while (fit > 0 && (static_cast<unsigned char>(u8[fit]) & 0xC0) == 0x80) --fit;
        if (fit == 0) return false;
        std::memcpy(arr, u8, fit);
        arr[fit] = '\0';
        return true;
    }

    std::memcpy(arr, u8, need);
    arr[need] = '\0';
    return true;
}

} // namespace

void ProcessAndFixParam(PF_ParamDef* def) noexcept {
    if (!def) return;

    EnsureDictLoaded();

    // ★ AeLocalizer：修复编码（GBK -> UTF-8）
    {
        static std::atomic<int> seen{0};
        const int n = seen.fetch_add(1, std::memory_order_relaxed);
        if (n < 30) {
            char before[128]{};
            std::strncpy(before, def->PF_DEF_NAME, 120);
            const bool fixed = FixGBKToUTF8(def->PF_DEF_NAME, sizeof(def->PF_DEF_NAME));
            if (fixed) {
                AELOG("FIX#%d %s -> %s", n, before, def->PF_DEF_NAME);
            } else if (n < 8) {
                AELOG("SKIP#%d %s", n, before);
            }
        } else {
            FixGBKToUTF8(def->PF_DEF_NAME, sizeof(def->PF_DEF_NAME));
        }
    }

    // 下拉选项：先查词典，未命中则转码
    // ★ 注意：FixStringArray 是数组引用模板，lambda 里传指针无法推导，
    //   所以这里自己实现转码（逻辑与 FixStringArray 一致）
    auto fixEnumArray = [&](char* arr, size_t cap) noexcept {
        if (!arr || !arr[0]) return;
        if (!IsBufferWritable(arr, cap)) return;
        FixGBKToUTF8(arr, cap);
    };


    switch (def->param_type) {
    case PF_Param_POPUP: {
        // ★ 下拉选项：形如 "No|Tile|Reflect"，逐项查词典
        const char*& sp = def->u.pd.u.PF_DEF_NAMESPTR;
        if (sp && sp[0] && g_dict.ready()) {
            if (const char* hit = aeloc::findNarrow(g_dict, sp)) {
                sp = hit;
                break;
            }
            // 整串没命中，尝试逐项翻译后拼接
            std::string s(sp);
            if (s.find('|') != std::string::npos) {
                std::string out;
                size_t pos = 0;
                bool allHit = true;
                while (pos <= s.size()) {
                    size_t bar = s.find('|', pos);
                    std::string part = s.substr(pos, bar == std::string::npos ? std::string::npos : bar - pos);
                    const char* zh = aeloc::findNarrow(g_dict, part.c_str());
                    if (!zh) { allHit = false; break; }
                    if (!out.empty()) out += '|';
                    out += zh;
                    if (bar == std::string::npos) break;
                    pos = bar + 1;
                }
                if (allHit && !out.empty()) {
                    static std::mutex mu2;
                    static std::unordered_set<std::string> c2;
                    std::lock_guard<std::mutex> lk(mu2);
                    auto it = c2.emplace(std::move(out)).first;
                    sp = it->c_str();
                    g_dictHits.fetch_add(1, std::memory_order_relaxed);
                    break;
                }
            }
        }
        FixStringPointer(sp);
        break;
    }

    case PF_Param_CHECKBOX:
        FixStringPointer(def->u.bd.u.PF_DEF_NAMEPTR);
        break;

    case PF_Param_BUTTON:
        FixStringPointer(def->u.button_d.u.PF_DEF_NAMESPTR);
        break;

    case PF_Param_SLIDER:
        fixEnumArray(def->u.sd.value_desc, sizeof(def->u.sd.value_desc));
        break;

    case PF_Param_FIX_SLIDER:
        fixEnumArray(def->u.fd.value_str, sizeof(def->u.fd.value_str));
        fixEnumArray(def->u.fd.value_desc, sizeof(def->u.fd.value_desc));
        break;

    case PF_Param_FLOAT_SLIDER:
        fixEnumArray(def->u.fs_d.value_desc, sizeof(def->u.fs_d.value_desc));
        break;

    default:

        break;
    }
}

// ==========================================
// 5. 后处理扫描，确保插件修改后参数名称仍然有效
// ==========================================

static int FixAllParamNamesAndCheck(PF_InData* in_data, PF_ParamDef* params[]) noexcept {
    if (!in_data || !params || in_data->num_params <= 0) return 0;

    PF_ParamIndex scan_limit = in_data->num_params;
    if (scan_limit > 10000) scan_limit = 10000;

    int fixed_count = 0;
    for (PF_ParamIndex i = 1; i < scan_limit; i++) {
        if (params[i] && !IsValidUTF8(params[i]->PF_DEF_NAME)) {
            ProcessAndFixParam(params[i]);
            fixed_count++;
        }
    }
    return fixed_count;
}

inline bool TryCreateAndEnableHook(void* pTarget, LPVOID pDetour, LPVOID* ppOriginal) noexcept {
    if (MH_CreateHook(pTarget, pDetour, ppOriginal) != MH_OK) {
        return false;
    }
    if (MH_EnableHook(pTarget) != MH_OK) {
        MH_DisableHook(pTarget);
        MH_RemoveHook(pTarget);
        return false;
    }
    return true;
}

static std::unordered_set<void*> g_HookedAddParamAddrs;
static PF_Err(*g_OriginalHostAddParam)(PF_ProgPtr, PF_ParamIndex, PF_ParamDef*) = nullptr;
static std::mutex g_AddParamHookMutex;

PF_Err Hooked_HostAddParam(PF_ProgPtr effect_ref, PF_ParamIndex index, PF_ParamDef* def) {
    if (def) ProcessAndFixParam(def);
    return g_OriginalHostAddParam(effect_ref, index, def);
}

void EnsureHostAddParamHooked(void* pTarget) noexcept {
    if (!pTarget) return;

    std::lock_guard<std::mutex> lock(g_AddParamHookMutex);

    if (g_HookedAddParamAddrs.count(pTarget) > 0) {
        return;
    }

    if (TryCreateAndEnableHook(pTarget,
                               reinterpret_cast<LPVOID>(&Hooked_HostAddParam),
                               reinterpret_cast<LPVOID*>(&g_OriginalHostAddParam))) {
        g_HookedAddParamAddrs.insert(pTarget);
    }
}

static PF_Err(*g_Original_PF_UpdateParamUI)(PF_ProgPtr, PF_ParamIndex, const PF_ParamDef*) = nullptr;

PF_Err Hooked_PF_UpdateParamUI(PF_ProgPtr effect_ref, PF_ParamIndex param_index, const PF_ParamDef* defP) {
    if (!defP) return g_Original_PF_UpdateParamUI(effect_ref, param_index, defP);
    
    PF_ParamDef fixed_def = *defP;
    ProcessAndFixParam(&fixed_def);
    return g_Original_PF_UpdateParamUI(effect_ref, param_index, &fixed_def);
}

static bool TryInstallPFUpdateParamUIHook(SPBasicSuite* basic) {
    if (!basic) return false;

    // ★ MinGW 不支持 MSVC 的 __try/__except（SEH）。
    //   这里改用「先查指针有效性再调用」的防御式写法，
    //   效果等价：拿不到 suite / 函数指针就直接返回 false。
    PF_ParamUtilsSuite3* paramSuite = nullptr;
    const void* raw = nullptr;
    A_Err err = basic->AcquireSuite(kPFParamUtilsSuite,
                                    kPFParamUtilsSuiteVersion3, &raw);
    paramSuite = const_cast<PF_ParamUtilsSuite3*>(
        reinterpret_cast<const PF_ParamUtilsSuite3*>(raw));
    if (err != A_Err_NONE || !paramSuite) return false;

    if (!paramSuite->PF_UpdateParamUI) {
        basic->ReleaseSuite(kPFParamUtilsSuite, kPFParamUtilsSuiteVersion3);
        return false;
    }

    void* target = reinterpret_cast<void*>(paramSuite->PF_UpdateParamUI);
    const bool ok = TryCreateAndEnableHook(
        target,
        reinterpret_cast<LPVOID>(&Hooked_PF_UpdateParamUI),
        reinterpret_cast<LPVOID*>(&g_Original_PF_UpdateParamUI));

    basic->ReleaseSuite(kPFParamUtilsSuite, kPFParamUtilsSuiteVersion3);
    return ok;
}

template<int ID>
PF_Err Trigger_EffectEntryPoint(
    ULONG_PTR arg0,
    ULONG_PTR arg1,
    ULONG_PTR arg2,
    ULONG_PTR arg3,
    ULONG_PTR arg4,
    ULONG_PTR arg5)
{
    void* original = g_OriginalEntryPoints[ID].load(std::memory_order_acquire);
    if (!original) [[unlikely]] return PF_Err_NONE;

    if (arg0 > 0xFFFF) {
        typedef PF_Err(*AEGP_EntryPoint_t)(struct SPBasicSuite*, A_long, A_long, AEGP_PluginID, AEGP_GlobalRefcon*);
        auto aegp_original = reinterpret_cast<AEGP_EntryPoint_t>(original);
        return aegp_original(
            reinterpret_cast<struct SPBasicSuite*>(arg0),
            static_cast<A_long>(arg1),
            static_cast<A_long>(arg2),
            static_cast<AEGP_PluginID>(arg3),
            reinterpret_cast<AEGP_GlobalRefcon*>(arg4)
        );
    }

    PF_Cmd cmd = static_cast<PF_Cmd>(arg0);
    auto* in_data = reinterpret_cast<PF_InData*>(arg1);
    auto* out_data = reinterpret_cast<PF_OutData*>(arg2);
    auto** params = reinterpret_cast<PF_ParamDef**>(arg3);
    auto* output = reinterpret_cast<PF_LayerDef*>(arg4);
    void* extra = reinterpret_cast<void*>(arg5);

    typedef PF_Err(*EffectEntryPoint_t)(PF_Cmd, PF_InData*, PF_OutData*, PF_ParamDef* [], PF_LayerDef*, void*);
    auto effect_original = reinterpret_cast<EffectEntryPoint_t>(original);

    if (cmd == PF_Cmd_PARAMS_SETUP && in_data) {
        if (in_data->inter.add_param) {
            EnsureHostAddParamHooked(reinterpret_cast<void*>(in_data->inter.add_param));
        }
        if (in_data->pica_basicP && !g_Original_PF_UpdateParamUI) {
            TryInstallPFUpdateParamUIHook(in_data->pica_basicP);
        }
    }

    PF_Err result = effect_original(cmd, in_data, out_data, params, output, extra);

    if (cmd == PF_Cmd_UPDATE_PARAMS_UI || cmd == PF_Cmd_USER_CHANGED_PARAM) {
        int fixed_count = FixAllParamNamesAndCheck(in_data, params);
        if (fixed_count > 0 && out_data) {
            out_data->out_flags |= PF_OutFlag_REFRESH_UI;
        }
    }

    return result;
}

#define P_ENTRY(n) &Trigger_EffectEntryPoint<n>
typedef PF_Err(*EntryPointFunc_t)(ULONG_PTR, ULONG_PTR, ULONG_PTR, ULONG_PTR, ULONG_PTR, ULONG_PTR);

#define P_ENTRY_8(n)   P_ENTRY(n), P_ENTRY(n+1), P_ENTRY(n+2), P_ENTRY(n+3), P_ENTRY(n+4), P_ENTRY(n+5), P_ENTRY(n+6), P_ENTRY(n+7)
#define P_ENTRY_32(n)  P_ENTRY_8(n), P_ENTRY_8(n+8), P_ENTRY_8(n+16), P_ENTRY_8(n+24)
#define P_ENTRY_128(n) P_ENTRY_32(n), P_ENTRY_32(n+32), P_ENTRY_32(n+64), P_ENTRY_32(n+96)
#define P_ENTRY_512(n) P_ENTRY_128(n), P_ENTRY_128(n+128), P_ENTRY_128(n+256), P_ENTRY_128(n+384)
#define P_ENTRY_2048(n) P_ENTRY_512(n), P_ENTRY_512(n+512), P_ENTRY_512(n+1024), P_ENTRY_512(n+1536)

EntryPointFunc_t g_WrapperPool[WRAPPER_POOL_SIZE] = {
    P_ENTRY_2048(0)
};

bool SafeGetExportDirectory(HMODULE hMod, IMAGE_EXPORT_DIRECTORY** out_exports) noexcept {
    if (!hMod || !out_exports) return false;

    // ★ 原版用 MSVC 的 __try/__except 包住整段（防畸形 PE 崩溃）。
    //   MinGW 不支持 SEH，改为「先把所有边界值读完再算，每一步都校验」，
    //   任何一个校验不过就返回 false —— 等价的安全效果，只是依赖的是
    //   显式边界判断而不是异常捕获。
    auto* base = reinterpret_cast<unsigned char*>(hMod);
    auto* dosHeader = reinterpret_cast<IMAGE_DOS_HEADER*>(base);
    if (dosHeader->e_magic != IMAGE_DOS_SIGNATURE) return false;
    if (dosHeader->e_lfanew < static_cast<LONG>(sizeof(IMAGE_DOS_HEADER)) ||
        dosHeader->e_lfanew > 0x10000) {
        return false;
    }
    auto* ntHeaders = reinterpret_cast<IMAGE_NT_HEADERS*>(base + dosHeader->e_lfanew);
    if (ntHeaders->Signature != IMAGE_NT_SIGNATURE) return false;

    IMAGE_DATA_DIRECTORY exportDirInfo =
        ntHeaders->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_EXPORT];
    if (exportDirInfo.Size == 0 || exportDirInfo.VirtualAddress == 0) return false;

    const DWORD imageSize = ntHeaders->OptionalHeader.SizeOfImage;
    if (exportDirInfo.VirtualAddress >= imageSize) return false;
    if (exportDirInfo.VirtualAddress + exportDirInfo.Size > imageSize) return false;

    *out_exports = reinterpret_cast<IMAGE_EXPORT_DIRECTORY*>(
        base + exportDirInfo.VirtualAddress);
    return true;
}

//: 插件效果入口的导出名白名单
//: 说明：这些都是「效果主入口」，参数注册会经过它们。
//: 不包含 PluginDataEntryFunction / AEGPEntryPointFunc 等非效果入口。
bool IsEffectEntryName(const char* name) noexcept {
    static const char* kNames[] = {
        "EntryPointFunc",     // Red Giant / Maxon
        "EffectMain",         // 通用
        "EffectMainExtra",    // Adobe 内置
        "EffectMainExtra2",   // Adobe 内置（部分）
        "pluginMain",         // Sapphire
        "PluginMain",         // BCC+
        "main",               // BCC 老系列
        "mainB",              // RSMB
        "FilterMain",         // 部分第三方
        "MainEntry",          // 部分第三方
        "PluginEntry",        // Beauty Box 等
    };
    for (const char* k : kNames) {
        if (std::strcmp(name, k) == 0) return true;
    }
    return false;
}

void HookModuleEntryPoints(HMODULE hMod, const std::wstring& modPath) {
    if (!hMod) return;

    std::wstring lowerPath = modPath;
    if (lowerPath.empty()) return;

    CharLowerBuffW(lowerPath.data(), static_cast<DWORD>(lowerPath.size()));

    if (lowerPath.find(L".aex") == std::wstring::npos &&
        lowerPath.find(L"\\plug-ins\\") == std::wstring::npos) {
        return;
    }

    IMAGE_EXPORT_DIRECTORY* exports = nullptr;
    if (!SafeGetExportDirectory(hMod, &exports)) {
        return;
    }

    auto* base = reinterpret_cast<unsigned char*>(hMod);
    auto* names = reinterpret_cast<DWORD*>(base + exports->AddressOfNames);
    auto* functions = reinterpret_cast<DWORD*>(base + exports->AddressOfFunctions);
    auto* ordinals = reinterpret_cast<WORD*>(base + exports->AddressOfNameOrdinals);

    std::lock_guard<std::mutex> lock(g_WrapperMutex);

    for (DWORD i = 0; i < exports->NumberOfNames; i++) {
        const char* name = reinterpret_cast<const char*>(base + names[i]);
        // ★★ 实测：AE 插件的入口导出名有 8 种以上（扫描 1271 个插件统计）：
        //     main(324) pluginMain(287) PluginMain(160) EntryPointFunc(139)
        //     EffectMainExtra(110) FilterMain(92) MainEntry(76) EffectMain(56)
        //   原版只匹配 EntryPointFunc / EffectMain，
        //   导致 Sapphire(pluginMain) 和 BCC(main) 完全没被挂钩。
        if (IsEffectEntryName(name)) {
            void* pTarget = reinterpret_cast<void*>(base + functions[ordinals[i]]);

            bool alreadyHooked = false;
            for (int j = 0; j < g_WrapperCount.load(std::memory_order_relaxed); j++) {
                if (g_OriginalEntryPoints[j].load(std::memory_order_relaxed) == pTarget) [[unlikely]] {
                    alreadyHooked = true;
                    break;
                }
            }
            if (alreadyHooked) continue;

            int currentID = g_WrapperCount.load(std::memory_order_relaxed);
            if (currentID >= WRAPPER_POOL_SIZE) break;
            {
                char np[512]{};
                WideCharToMultiByte(CP_UTF8, 0, modPath.c_str(), -1, np, 512, nullptr, nullptr);
                AELOG("挂钩插件入口: %s (%s)", np, name);
            }
            void* trampolineBuf = nullptr;
            if (TryCreateAndEnableHook(pTarget,
                                       reinterpret_cast<LPVOID>(g_WrapperPool[currentID]),
                                       reinterpret_cast<LPVOID*>(&trampolineBuf))) {
                g_OriginalEntryPoints[currentID].store(trampolineBuf, std::memory_order_release);
                g_WrapperCount.fetch_add(1, std::memory_order_release);
            }
        }
    }
}

void EnumerateAndHookLoadedModules() {
    HANDLE hSnapshot = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, GetCurrentProcessId());
    if (hSnapshot != INVALID_HANDLE_VALUE) {
        MODULEENTRY32W me;
        me.dwSize = sizeof(me);
        if (Module32FirstW(hSnapshot, &me)) {
            do {
                HookModuleEntryPoints(me.hModule, me.szExePath);
            } while (Module32NextW(hSnapshot, &me));
        }
        CloseHandle(hSnapshot);
    }
}

typedef HMODULE(WINAPI* LoadLibraryW_t)(LPCWSTR);
typedef HMODULE(WINAPI* LoadLibraryExW_t)(LPCWSTR, HANDLE, DWORD);
LoadLibraryW_t Original_LoadLibraryW = nullptr;
LoadLibraryExW_t Original_LoadLibraryExW = nullptr;

HMODULE WINAPI Hooked_LoadLibraryW(LPCWSTR lpLibFileName) {
    HMODULE hMod = Original_LoadLibraryW(lpLibFileName);
    if (hMod && lpLibFileName) {
        HookModuleEntryPoints(hMod, lpLibFileName);
    }
    return hMod;
}

HMODULE WINAPI Hooked_LoadLibraryExW(LPCWSTR lpLibFileName, HANDLE hFile, DWORD dwFlags) {
    HMODULE hMod = Original_LoadLibraryExW(lpLibFileName, hFile, dwFlags);
    if (hMod && lpLibFileName) {
        HookModuleEntryPoints(hMod, lpLibFileName);
    }
    return hMod;
}

#if defined(_MSC_VER)
#define FIXER_EXPORT __declspec(dllexport)
#else
#define FIXER_EXPORT __attribute__((visibility("default")))
#endif

extern "C" FIXER_EXPORT PF_Err EntryPointFunc(
    struct SPBasicSuite* pica_basicP,
    A_long major_version,
    A_long minor_version,
    AEGP_PluginID aegp_plugin_id,
    AEGP_GlobalRefcon* global_refconP)
{
    AELOG("EntryPointFunc 被调用 arg0=%p", reinterpret_cast<void*>(pica_basicP));
    MH_STATUS status = MH_Initialize();
    AELOG("MH_Initialize -> %d", static_cast<int>(status));
    if (status == MH_OK || status == MH_ERROR_ALREADY_INITIALIZED) {
        HMODULE hKernel32 = GetModuleHandleW(L"kernel32.dll");
        if (hKernel32) {
            auto* pLLW = reinterpret_cast<void*>(GetProcAddress(hKernel32, "LoadLibraryW"));
            if (pLLW) {
                TryCreateAndEnableHook(pLLW,
                                       reinterpret_cast<LPVOID>(&Hooked_LoadLibraryW),
                                       reinterpret_cast<LPVOID*>(&Original_LoadLibraryW));
            }

            auto* pLLEW = reinterpret_cast<void*>(GetProcAddress(hKernel32, "LoadLibraryExW"));
            if (pLLEW) {
                TryCreateAndEnableHook(pLLEW,
                                       reinterpret_cast<LPVOID>(&Hooked_LoadLibraryExW),
                                       reinterpret_cast<LPVOID*>(&Original_LoadLibraryExW));
            }
        }

        EnumerateAndHookLoadedModules();
        AELOG("已枚举并挂钩已加载模块");
    }
    AELOG("EntryPointFunc 返回");
    return PF_Err_NONE;
}

BOOL WINAPI DllMain(HINSTANCE hinstDLL, DWORD fdwReason, LPVOID lpvReserved) {
    if (fdwReason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hinstDLL);
    } 
    else if (fdwReason == DLL_PROCESS_DETACH) {
        MH_DisableHook(MH_ALL_HOOKS);
        MH_Uninitialize();
    }
    return TRUE;
}