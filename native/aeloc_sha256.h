/* aeloc_sha256.h — 共享的 FIPS 180-4 SHA-256 实现(单头文件)。
 *
 * ★ 来源:native/ae_parameters.c 的同名实现原样提取(2026-11-01)。
 *   ae_parameters.c 保持自带一份不动(它是按 FLT/TDB 哈希自查后加载进 AE
 *   的模块,改动需重新验证);本头文件给新的小工具 DLL(aeloc_cipher 等)
 *   共用,避免第三份拷贝。
 *
 * 为什么不用 Windows CNG(bcrypt):见 ae_parameters.c 顶部注释 —— 在
 * 某 AE 2025 25.6 上 bcrypt 首个模块哈希永不返回,这里自算只依赖读文件。
 */
#ifndef AELOC_SHA256_H
#define AELOC_SHA256_H

#include <stdint.h>
#include <string.h>
#include <stddef.h>

typedef struct {uint32_t h[8];uint64_t length;unsigned char block[64];size_t used;} aeloc_sha256;
static const uint32_t aeloc_sha_k[64]={
 0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,
 0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
 0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,
 0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
 0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,
 0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
#define AELOC_ROR32(x,n) (((x)>>(n))|((x)<<(32-(n))))
static void aeloc_sha_block(aeloc_sha256 *s,const unsigned char *p) {
    uint32_t w[64],v[8];
    for(int i=0;i<16;i++)w[i]=(uint32_t)p[i*4]<<24|(uint32_t)p[i*4+1]<<16|(uint32_t)p[i*4+2]<<8|(uint32_t)p[i*4+3];
    for(int i=16;i<64;i++)w[i]=w[i-16]+(AELOC_ROR32(w[i-15],7)^AELOC_ROR32(w[i-15],18)^(w[i-15]>>3))+w[i-7]+(AELOC_ROR32(w[i-2],17)^AELOC_ROR32(w[i-2],19)^(w[i-2]>>10));
    memcpy(v,s->h,sizeof(v));
    for(int i=0;i<64;i++){
        uint32_t t1=v[7]+(AELOC_ROR32(v[4],6)^AELOC_ROR32(v[4],11)^AELOC_ROR32(v[4],25))+((v[4]&v[5])^(~v[4]&v[6]))+aeloc_sha_k[i]+w[i];
        uint32_t t2=(AELOC_ROR32(v[0],2)^AELOC_ROR32(v[0],13)^AELOC_ROR32(v[0],22))+((v[0]&v[1])^(v[0]&v[2])^(v[1]&v[2]));
        v[7]=v[6];v[6]=v[5];v[5]=v[4];v[4]=v[3]+t1;v[3]=v[2];v[2]=v[1];v[1]=v[0];v[0]=t1+t2;
    }
    for(int i=0;i<8;i++)s->h[i]+=v[i];
}
static void aeloc_sha_init(aeloc_sha256 *s) {
    static const uint32_t iv[8]={0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    memcpy(s->h,iv,sizeof(iv));s->length=0;s->used=0;
}
static void aeloc_sha_update(aeloc_sha256 *s,const unsigned char *p,size_t n) {
    s->length+=n;
    while(n){size_t take=64-s->used;if(take>n)take=n;memcpy(s->block+s->used,p,take);s->used+=take;p+=take;n-=take;if(s->used==64){aeloc_sha_block(s,s->block);s->used=0;}}
}
static void aeloc_sha_final(aeloc_sha256 *s,unsigned char out[32]) {
    uint64_t bits=s->length*8;s->block[s->used++]=0x80;
    if(s->used>56){memset(s->block+s->used,0,64-s->used);aeloc_sha_block(s,s->block);s->used=0;}
    memset(s->block+s->used,0,56-s->used);
    for(int i=0;i<8;i++)s->block[56+i]=(unsigned char)(bits>>(56-8*i));
    aeloc_sha_block(s,s->block);
    for(int i=0;i<8;i++){out[i*4]=(unsigned char)(s->h[i]>>24);out[i*4+1]=(unsigned char)(s->h[i]>>16);out[i*4+2]=(unsigned char)(s->h[i]>>8);out[i*4+3]=(unsigned char)s->h[i];}
}

#endif /* AELOC_SHA256_H */
