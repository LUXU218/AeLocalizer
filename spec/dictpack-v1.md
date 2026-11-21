# 词典包规范 aeloc-dict/1（可分发格式）

> 这是 AeLocalizer · 映 的**公开交换格式**。任何人都可以制作、分享、导入词典包。
> 设计目标：① 单一文件、双击可导 ② 内容可读可审 ③ 可校验完整性 ④ 可声明适用插件与 AE 版本 ⑤ 支持增量（只含"我改过的"）。

---

## 1. 容器：`.aeloc` = ZIP

`.aeloc` 就是一个标准 ZIP（无加密），扩展名自定义。内部结构：

```
mydict.aeloc
├── manifest.json        # 必需：包元数据 + 文件校验
├── dict.json            # 必需：词条主体（UTF-8，无 BOM）
├── glossary.json        # 可选：术语锁定表（跨包共享的强制译名）
├── LICENSE.txt          # 可选：授权声明（建议 CC BY-SA 4.0 / MIT）
├── README.md            # 可选：说明、致谢、变更记录
└── icon.png             # 可选：256×256，列表里显示
```

**为什么用 ZIP 而不是裸 JSON**：单文件便于分发、可放多个文件、可只读打开、Windows 资源管理器原生可解压（用户能自己审查内容）。

---

## 2. manifest.json

```jsonc
{
  "schema": "aeloc-dict/1",
  "id": "com.example.packaging-cn",          // 全局唯一，建议反向域名
  "name": "影视包装常用词",
  "version": "1.2.0",                         // 语义化版本
  "author": "AUTHOR_NAME",
  "homepage": "https://example.com/pack",
  "license": "CC-BY-SA-4.0",
  "created": "2026-10-20T10:00:00+08:00",
  "updated": "2026-10-20T10:00:00+08:00",
  "description": "针对 Trapcode / Universe / BCC 的常用参数译名",

  "appliesTo": {
    "aeVersions": ["2024", "2025", "2026"],   // 空数组 = 全部
    "plugins": ["Trapcode Particular", "Universe", "BCC Continuum"],
    "encoding": "auto"                        // auto | gbk | utf8
  },

  "stats": { "entries": 2301, "glossary": 214 },

  "files": {
    "dict.json":     { "sha256": "HEX64", "bytes": 182344 },
    "glossary.json": { "sha256": "HEX64", "bytes": 8412 }
  },

  "signature": {
    "alg": "ed25519",
    "publicKey": "BASE64_32BYTES",
    "sig": "BASE64_64BYTES"                   // 对 canonical(manifest 去掉 signature) + 各文件 sha256 签名
  }
}
```

**签名是可选的**。没有 `signature` 时导入界面显示「未签名」黄色徽标；有签名且校验通过显示「已验证」绿色徽标。
**签名不阻止导入**，只用于让用户判断来源可信度。

---

## 3. dict.json

```jsonc
{
  "schema": "aeloc-dict/1",
  "entries": {
    "Glow Threshold": {
      "zh": "发光阈值",
      "scope": ["Trapcode Particular"],   // 空/缺省 = 全局
      "priority": 50,                     // 冲突裁决，默认 50，越大越优先
      "note": "官方术语",                  // 可选，编辑器里作为提示
      "locked": false                      // true = 导入后不允许被低层覆盖
    },
    "Matte": { "zh": "遮罩", "priority": 100, "locked": true }
  }
}
```

**硬性约束**：
- 键（英文原名）必须是插件里**逐字节原样**的字符串，不做 trim、不改大小写
- 值 `zh` 必须是 UTF-8 合法字符串，禁止控制字符、U+FFFD、emoji
- 同一包内键不可重复（ZIP 内 JSON 天然保证）
- `scope` 里的插件名必须与 AeLocalizer 扫描到的显示名一致（导入时会校验并提示不匹配项）

---

## 4. glossary.json（术语锁定表）

用于跨包强制一致，例如「Mask 一律翻蒙版、Matte 一律翻遮罩」：

```jsonc
{
  "schema": "aeloc-glossary/1",
  "entries": {
    "Mask":  { "zh": "蒙版", "strict": true },
    "Matte": { "zh": "遮罩", "strict": true },
    "Opacity": { "zh": "不透明度", "strict": false }
  }
}
```

- `strict: true` → 词条里出现该词时**强制**用此译名（AI 翻译时也作为不可违反的约束注入 prompt）
- `strict: false` → 仅作建议

---

## 5. 冲突与优先级

导入后按以下顺序裁决（高者胜）：

```
① 用户锁定词条 (locked, 本地)         ← 永不被包覆盖
② 包内 locked 词条
③ 用户词条（本地，priority 高者）
④ 导入的包（按 manifest.priority 或导入顺序）
⑤ 内置权威表
⑥ 规则翻译
```

**导入时的冲突提示**：显示"本包有 42 条会覆盖你现有的译名"，可逐条查看差异，支持"全部保留我的 / 全部用包的 / 逐条选"。

---

## 6. 校验与安全

| 项 | 做法 |
|---|---|
| 结构校验 | JSON Schema 校验（schema 字段 + 必填项 + 类型） |
| 完整性 | 逐文件 sha256 与 manifest 比对 |
| 签名 | 有则验 ed25519；无则标"未签名" |
| 内容审查 | 拒绝含控制字符/U+FFFD/emoji 的译名；拒绝超长（超长项标黄不拒绝） |
| 路径安全 | 解压时拒绝 `..`、绝对路径、符号链接（防 zip-slip） |
| 大小限制 | 单包 ≤ 20 MB，条目 ≤ 200,000 |
| 执行安全 | **绝不执行包内任何脚本**；只读 JSON/图片 |

---

## 7. 导出（"把我的词典给别人"）

导出对话框选项：

```
[✓] 只导出我改过的词条（相对内置表）
[ ] 导出全部生效词条
[✓] 附带术语表
[✓] 附带 LICENSE 与 README
[ ] 用我的密钥签名（需先生成密钥对）
压缩级别：[标准 ▾]
```

产物：单个 `.aeloc` 文件，可直接微信/QQ/网盘发给别人，对方双击或拖进软件即可导入。

---

## 8. 版本与升级

- 同一个 `id` 的包，导入时若已存在旧版 → 提示"检测到 v1.1.0，是否升级到 v1.2.0"并显示新增/修改条数
- 升级保留用户的本地锁定词条
- 包内 `version` 降低时警告"这是旧版本"

---

## 9. 最小可用示例（可直接复制使用）

manifest.json：
```json
{"schema":"aeloc-dict/1","id":"com.example.hello","name":"示例词典","version":"1.0.0","author":"YOU","license":"MIT","created":"2026-10-20T10:00:00+08:00","appliesTo":{"aeVersions":[],"plugins":[],"encoding":"auto"},"stats":{"entries":2,"glossary":0},"files":{"dict.json":{"sha256":"PUT_REAL_HASH","bytes":120}}}
```

dict.json：
```json
{"schema":"aeloc-dict/1","entries":{"Glow Threshold":{"zh":"发光阈值"},"Wrap X":{"zh":"环绕 X"}}}
```
