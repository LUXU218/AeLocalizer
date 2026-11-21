/* aeloc_rpm.c — 跨进程批量读(C 重写,2026-11-01)。
 *
 * 背景:core/livenames.py 的注册表读取每条记录一次 ReadProcessMemory
 * (~1400 条 → 4200+ 次 ctypes 调用/次写入)。每次 ctypes 调用的固定
 * 开销(参数打包/句柄查找)远超 160 字节读取本身 —— 批量化后 N 次调用
 * 收敛为 1 次。
 *
 * 导出(64 位):
 *   int aeloc_rpm_many(void* handle,
 *                      const unsigned long long* addrs,
 *                      const unsigned long* lens,
 *                      int n,
 *                      unsigned char* out,     [总长 = sum(lens)]
 *                      unsigned char* ok)      [n 项,1=成功 0=失败]
 *   返回成功条数。任一参数非法返回 -1。每段独立尝试,失败不影响后续。
 *
 * 语义与 core/proc.py 的单条 read 一致:ReadProcessMemory 失败 → ok=0。
 * python 侧(core/proc.py read_many)在 DLL 缺失时回退逐条路径。
 */
#include <stddef.h>
#include <stdint.h>
#include <windows.h>

#define AELOC_EXPORT __declspec(dllexport)

typedef BOOL(WINAPI *RpmFn)(HANDLE, LPCVOID, LPVOID, SIZE_T, SIZE_T *);

AELOC_EXPORT int aeloc_rpm_many(void *handle,
                                const unsigned long long *addrs,
                                const unsigned long *lens,
                                int n,
                                unsigned char *out,
                                unsigned char *ok) {
    RpmFn rpm;
    int good = 0;
    SIZE_T off = 0;
    int i;

    if (!addrs || !lens || !out || !ok || n <= 0)
        return -1;
    rpm = (RpmFn)(void *)GetProcAddress(GetModuleHandleW(L"kernel32.dll"),
                                        "ReadProcessMemory");
    if (!handle || !rpm)
        return -1;
    for (i = 0; i < n; i++) {
        SIZE_T got = 0;
        unsigned long len = lens[i];
        ok[i] = 0;
        if (len == 0) {          /* 零长度段:视为成功,不读 */
            ok[i] = 1;
            good++;
            continue;
        }
        if (rpm(handle, (LPCVOID)(uintptr_t)addrs[i], out + off,
                (SIZE_T)len, &got) && got == (SIZE_T)len) {
            ok[i] = 1;
            good++;
        }
        off += len;
    }
    return good;
}
