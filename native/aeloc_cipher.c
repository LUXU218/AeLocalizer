/* aeloc_cipher.c — 引擎词典密钥流加解密(C 重写,2026-11-01)。
 *
 * 格式规格 = core/legacy/writer.py 的 _keystream_legacy(黄金对照测试锁定,
 * 字节级一致):
 *   h0 = SHA256(key); keystream = h0 ‖ SHA256(h0) ‖ SHA256(SHA256(h0)) ‖ …
 *   out = blob XOR keystream[:len(blob)]
 *
 * python 侧(core/legacy/native_cipher.py)在 DLL 缺失/加载失败时回退纯
 * python 实现 —— 两边输出必须逐字节相同(tests/test_native_cipher.py)。
 *
 * 构建:tools/build_native.py(gcc/clang/cl 均可,-O2)。
 * 导出(64 位):
 *   void aeloc_cipher(const unsigned char* key, size_t key_len,
 *                     const unsigned char* blob, size_t blob_len,
 *                     unsigned char* out)
 *       out 至少 blob_len 字节;key/blob 允许为 NULL(长度 0)。
 */
#include <stddef.h>
#include <stdint.h>
#include "aeloc_sha256.h"

#ifdef _WIN32
#define AELOC_EXPORT __declspec(dllexport)
#else
#define AELOC_EXPORT __attribute__((visibility("default")))
#endif

AELOC_EXPORT void aeloc_cipher(const unsigned char *key, size_t key_len,
                               const unsigned char *blob, size_t blob_len,
                               unsigned char *out) {
    aeloc_sha256 s;
    unsigned char h[32];
    size_t done = 0;

    if (!blob_len || !out)
        return;
    aeloc_sha_init(&s);
    if (key_len && key)
        aeloc_sha_update(&s, key, key_len);
    aeloc_sha_final(&s, h);
    while (done < blob_len) {
        size_t take = blob_len - done;
        size_t i;
        if (take > 32)
            take = 32;
        for (i = 0; i < take; i++)
            out[done + i] = (unsigned char)(blob[done + i] ^ h[i]);
        done += take;
        if (done < blob_len) {
            aeloc_sha256 n;
            aeloc_sha_init(&n);
            aeloc_sha_update(&n, h, 32);
            aeloc_sha_final(&n, h);
        }
    }
}

/* 自检入口(可选):对内置测试向量跑一遍,返回 0 = 一致。
 * 供构建后快速冒烟;python 侧黄金对照仍是权威。 */
AELOC_EXPORT int aeloc_cipher_selftest(void) {
    /* SHA256("abc") = ba7816bf 8f01cfea 414140de 5dae2223 b00361a3 96177a9c b410ff61 f20015ad */
    static const unsigned char expect[32] = {
        0xba, 0x78, 0x16, 0xbf, 0x8f, 0x01, 0xcf, 0xea, 0x41, 0x41, 0x40, 0xde,
        0x5d, 0xae, 0x22, 0x23, 0xb0, 0x03, 0x61, 0xa3, 0x96, 0x17, 0x7a, 0x9c,
        0xb4, 0x10, 0xff, 0x61, 0xf2, 0x00, 0x15, 0xad};
    aeloc_sha256 s;
    unsigned char d[32];
    int i;
    aeloc_sha_init(&s);
    aeloc_sha_update(&s, (const unsigned char *)"abc", 3);
    aeloc_sha_final(&s, d);
    for (i = 0; i < 32; i++)
        if (d[i] != expect[i])
            return 1;
    return 0;
}
