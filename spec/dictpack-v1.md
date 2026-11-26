# 词典包格式 aeloc-dict/1

AeLocalizer 使用 .aeloc 文件备份和交换词条。在桌面工具的「词典」或「设置 → 数据」中可以导入和导出。

## 容器

.aeloc 是未加密的 ZIP。只接受以下名称，文件都位于 ZIP 根目录。

| 文件 | 用途 |
| --- | --- |
| `manifest.json` | 必需。包标识、作者、适用范围、哈希和可选签名 |
| `dict.json` | 必需。词条数据，使用 UTF-8 |
| `glossary.json` | 可选。用于交换术语，桌面工具导入时记录术语数量 |
| `LICENSE.txt` | 可选。发布者提供的许可文本 |
| `README.md` | 可选。说明、致谢和变更记录 |
| `icon.png` | 可选。图标附件，当前桌面工具不展示 |

读取器在 ZIP 内读取数据，拒绝名单之外的文件名，不把文件解压到应用目录，也不执行包内内容。ZIP 字节流的上限为 20 MiB（20,971,520 字节），`dict.json` 最多包含 200,000 个词条键。

## 清单

`manifest.json` 的 `schema` 必须为 `aeloc-dict/1`。建议用反向域名形式的 `id` 标识词典包，并填写 `name`、`version`、`author`、`license` 和 `description`。`created`、`updated`、`homepage` 和 `stats` 可提供发布信息。`appliesTo.aeVersions`、`appliesTo.plugins` 和 `appliesTo.encoding` 可声明适用范围，当前导入和词条裁决不按这些字段过滤。

`files` 必须包含 `dict.json` 的 `sha256`，包中有 `glossary.json` 时也必须登记它的 `sha256`。词条和术语 JSON 的哈希按规范化内容计算。使用各自的 schema 和解析出的 entries 对象，按键排序，UTF-8 编码，不转义非 ASCII 字符，也不输出多余空白。哈希值用小写十六进制表示。生成器同时记录 `bytes`，当前校验以 SHA-256 为准。许可、说明和图标可在 `files` 中登记，登记时按原始字节校验。

`signature` 可省略。提供时，`alg` 为 `ed25519`，`publicKey` 和 `sig` 分别是 32 字节公钥和 64 字节签名的 Base64 表示。签名输入是以下内容依次拼接后的 SHA-256 原始摘要。

1. 移除 `signature` 后的规范化清单 JSON。
2. 按文件名排序，逐个拼接载荷文件名的 UTF-8 字节和该文件内容。词条、术语使用上述规范化 JSON，其他附件使用原始字节。

完整性校验通过的未签名包可以导入。已有签名时必须通过验证，字段为空、签名无效或缺少验证库时导入会停止并提示原因。「签名有效」表示签名通过了包内公钥的验证。确认作者身份还需要通过独立可信渠道核对公钥。

## 词条

```json
{
  "schema": "aeloc-dict/1",
  "entries": {
    "Glow Threshold": {
      "zh": "发光阈值",
      "scope": ["Trapcode Particular"],
      "priority": 50,
      "note": "常用译名",
      "locked": false
    }
  }
}
```

- 键应使用插件中的原文。匹配区分大小写并保留空白。
- 键和 `zh` 必须为非空字符串。`zh` 拒绝 U+0000–U+0008、U+000B–U+000C、U+000E–U+001F 及 U+FFFD。导入校验接受 emoji。AE 中的实际显示还取决于运行时编码和控件容量。
- 作者应避免重复键。当前使用 Python 默认的 JSON 解析器，同一对象内的重复键取最后一个值。见 [Python JSON 文档](https://docs.python.org/3.12/library/json.html#repeated-names-within-an-object)。
- 格式没有单条译名长度上限。运行时另按目标位置的编码和容量处理，不能写入译名时可能保留原文。
- 可随词条保存 `scope`、`priority`、`note` 和 `locked`。若提供 `priority`，应使用整数。它随解析结果保留，目前不参与词条裁决。导入时不校验 `scope` 中的插件名，裁决时不按它限定插件。包内 `locked` 不会锁定本地词条。桌面界面没有这些包字段的独立编辑控件。

## 术语表

```json
{
  "schema": "aeloc-glossary/1",
  "entries": {
    "Mask": { "zh": "蒙版", "strict": true },
    "Opacity": { "zh": "不透明度", "strict": false }
  }
}
```

可以随包交换术语和 `strict` 标记。桌面导入只保存术语数量，包内术语不参与词条裁决，也不会加入应用的 AI 翻译术语表。将包内术语接入桌面词典和翻译流程属于规划。

## 导入与冲突

桌面入口先检查包，校验通过后导入并保存。成功提示显示签名状态及新增、不同、相同和被本地锁定词条挡住的数量。「译名不同」统计与已有生效译名的差异，最终生效情况由词典层级决定。

词典层级从高到低为本地锁定词条、本地词条、已启用的词典包、内置词条。多个已启用的包包含同一词条时，最后导入的包优先。再次导入相同 `id` 会替换该包并把它放到导入顺序的末尾，本地词条保留。当前按 `id` 替换，不比较版本号，也没有升级确认或降级提示。

逐条选择冲突译名、图形化签名徽标、版本比较和升级确认属于规划。

## 导出

桌面工具导出当前全部生效词条，生成一个未签名的 .aeloc 备份。可在另一套 AeLocalizer 中通过「导入词典包」读取。增量导出、桌面附带术语表或许可文件、密钥和签名选项属于规划。开发者可以直接用 `build_pack()` 附加术语、许可、说明、图标或签名。

以下示例生成可直接导入的最小词典包。

```python
from core.dictpack import build_pack, write_pack

data = build_pack(
    pack_id="com.example.hello",
    name="示例词典",
    version="1.0.0",
    author="示例作者",
    entries={"Glow Threshold": {"zh": "发光阈值"}},
)
write_pack(data, "example.aeloc")
```
