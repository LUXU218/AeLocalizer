# 汉化回归记录校验器

`tests.sglow_regression` 是项目自带的离线校验工具。它读取双图层工程的记录，分别检查通道请求、后台回执和原生界面文字。

从仓库根目录生成模板，填入观察结果后运行校验。记录和截图保存在本地。

```powershell
py -3.12 -m tests.sglow_regression --template | Set-Content -Encoding utf8 .\record.json
py -3.12 -m tests.sglow_regression .\record.json
```

退出码 0 表示记录完整且符合预期，1 表示存在明确不符，2 表示记录缺失或无法读取。
