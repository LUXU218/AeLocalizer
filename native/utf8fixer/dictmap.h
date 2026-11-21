// dictmap.h —— 词典内存映射读取器（跨进程共享，只读）
//
// 设计：AE 进程里加载的这个 .aex 直接 mmap 一个二进制词典文件，
//       查询走哈希表，零拷贝、零解析开销。
//
// 文件格式（AeLocalizerDict v1）：
//   [0..7]    魔数 "AELOCD01"
//   [8..11]   条目数 N (uint32 LE)
//   [12..15]  字符串池字节数 S (uint32 LE)
//   [16..19]  哈希桶数 B (uint32 LE)
//   [20..27]  索引表偏移 I (uint64 LE)
//   [28..35]  字符串池偏移 P (uint64 LE)
//   [64..]    索引表：N 个 {hash:uint32, keyOff:uint32, valOff:uint32} 按 hash 排序
//             字符串池：所有键值连续存放，以 \0 分隔
#pragma once

#include <windows.h>
#include <cstdint>
#include <cstring>
#include <cwchar>
#include <string>

namespace aeloc {

struct DictEntry {
    uint32_t hash;
    uint32_t keyOff;   // 相对字符串池
    uint32_t valOff;
};

class DictMap {
public:
    DictMap() = default;
    ~DictMap() { close(); }

    DictMap(const DictMap&) = delete;
    DictMap& operator=(const DictMap&) = delete;

    bool open(const wchar_t* path) noexcept {
        close();
        hFile_ = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, nullptr,
                             OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (hFile_ == INVALID_HANDLE_VALUE) { hFile_ = nullptr; return false; }

        LARGE_INTEGER sz{};
        if (!GetFileSizeEx(hFile_, &sz) || sz.QuadPart < 64) { close(); return false; }
        size_ = static_cast<size_t>(sz.QuadPart);

        hMap_ = CreateFileMappingW(hFile_, nullptr, PAGE_READONLY, 0, 0, nullptr);
        if (!hMap_) { close(); return false; }

        base_ = static_cast<const uint8_t*>(MapViewOfFile(hMap_, FILE_MAP_READ, 0, 0, 0));
        if (!base_) { close(); return false; }

        if (std::memcmp(base_, "AELOCD01", 8) != 0) { close(); return false; }
        count_ = *reinterpret_cast<const uint32_t*>(base_ + 8);
        poolSize_ = *reinterpret_cast<const uint32_t*>(base_ + 12);
        indexOff_ = *reinterpret_cast<const uint64_t*>(base_ + 20);
        poolOff_ = *reinterpret_cast<const uint64_t*>(base_ + 28);
        if (indexOff_ + static_cast<uint64_t>(count_) * sizeof(DictEntry) > size_) {
            close(); return false;
        }
        if (poolOff_ + poolSize_ > size_) { close(); return false; }

        index_ = reinterpret_cast<const DictEntry*>(base_ + indexOff_);
        pool_ = reinterpret_cast<const char*>(base_ + poolOff_);
        ready_ = true;
        return true;
    }

    void close() noexcept {
        if (base_) { UnmapViewOfFile(base_); base_ = nullptr; }
        if (hMap_) { CloseHandle(hMap_); hMap_ = nullptr; }
        if (hFile_) { CloseHandle(hFile_); hFile_ = nullptr; }
        ready_ = false;
        index_ = nullptr;
        pool_ = nullptr;
        count_ = 0;
    }

    [[nodiscard]] bool ready() const noexcept { return ready_; }
    [[nodiscard]] uint32_t size() const noexcept { return count_; }

    // 用 UTF-8 的窄串查中文（GBK 字节也可，因为词典键就是原样字节）
    // 返回值为 UTF-8 译名，找不到返回 nullptr
    [[nodiscard]] const char* find(const char* key) const noexcept {
        if (!ready_ || !key || !*key) return nullptr;
        const uint32_t h = fnv1a(key);
        uint32_t lo = 0, hi = count_;
        while (lo < hi) {
            const uint32_t mid = lo + (hi - lo) / 2;
            const uint32_t mh = index_[mid].hash;
            if (mh < h) { lo = mid + 1; }
            else if (mh > h) { hi = mid; }
            else {
                // 哈希相同，往后扫所有同哈希项比对字符串
                uint32_t j = mid;
                while (j > 0 && index_[j - 1].hash == h) { --j; }
                for (; j < count_ && index_[j].hash == h; ++j) {
                    if (std::strcmp(pool_ + index_[j].keyOff, key) == 0) {
                        return pool_ + index_[j].valOff;
                    }
                }
                return nullptr;
            }
        }
        return nullptr;
    }

    static uint32_t fnv1a(const char* s) noexcept {
        uint32_t h = 2166136261u;
        while (*s) { h ^= static_cast<uint8_t>(*s++); h *= 16777619u; }
        return h;
    }

private:
    HANDLE hFile_ = nullptr;
    HANDLE hMap_ = nullptr;
    const uint8_t* base_ = nullptr;
    const DictEntry* index_ = nullptr;
    const char* pool_ = nullptr;
    uint32_t count_ = 0;
    uint32_t poolSize_ = 0;
    uint64_t indexOff_ = 0;
    uint64_t poolOff_ = 0;
    size_t size_ = 0;
    bool ready_ = false;
};

// UTF-16（宽）-> 词典查询：先用 UTF-8 或 GBK 编码成窄串再查
// 返回 UTF-8 译名或 nullptr
inline const char* findWide(const DictMap& d, const wchar_t* w) noexcept {
    if (!d.ready() || !w || !*w) return nullptr;

    // 先试 UTF-8
    char buf[512];
    int n = WideCharToMultiByte(CP_UTF8, 0, w, -1, buf, sizeof(buf), nullptr, nullptr);
    if (n > 1) {
        if (const char* hit = d.find(buf)) return hit;
    }
    // 再试 GBK
    n = WideCharToMultiByte(936, 0, w, -1, buf, sizeof(buf), nullptr, nullptr);
    if (n > 1) {
        if (const char* hit = d.find(buf)) return hit;
    }
    return nullptr;
}

inline const char* findNarrow(const DictMap& d, const char* s) noexcept {
    if (!d.ready() || !s || !*s) return nullptr;
    if (const char* hit = d.find(s)) return hit;
    // 若 s 是 GBK，转成 UTF-8 再查一次
    wchar_t wbuf[256];
    int n = MultiByteToWideChar(936, 0, s, -1, wbuf, 256);
    if (n > 1) {
        char u8[768];
        int m = WideCharToMultiByte(CP_UTF8, 0, wbuf, -1, u8, sizeof(u8), nullptr, nullptr);
        if (m > 1) return d.find(u8);
    }
    return nullptr;
}

} // namespace aeloc
