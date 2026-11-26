# AeLocalizer · 映

AeLocalizer 是 Windows 上的 After Effects 插件本地化工具。选择需要汉化的插件后，可以分别启用效果名和参数名、下拉值的汉化。工具也支持管理词条、调用自配的翻译服务，以及处理兼容的 CEP 面板和明文 JSX 脚本。

当前源码版本为 **1.0.28**。项目面向 AE 2021–2026，实时通道按实际运行的构建校验。AE 本体、第三方插件和翻译服务均需自行安装或配置。

Windows x64 便携版可从 [GitHub Releases](https://github.com/LUXU218/AeLocalizer/releases/latest) 下载。完整解压后，运行 `AeLocalizer.exe`。

## 从源码运行

在 64 位 Windows 上安装 64 位 Python 3.12，然后在仓库目录打开 PowerShell。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --target .runtime-deps -r requirements-runtime.txt
.\.venv\Scripts\python.exe -m app.main
```

完成安装后，也可以双击 `启动汉化工具.bat`。应用默认把设置、备份和词典写入 `%LOCALAPPDATA%\AeLocalizer`。开发和测试可用 `AELOC_DATA_ROOT` 指定独立的数据目录。

## 主要功能

- **效果名和参数名**可在「引擎」中分别切换。参数名和下拉值使用同一个开关。
- **AI 翻译**在「翻译」页配置服务地址、模型和 API Key。Windows 使用 DPAPI 保存 API Key。调用翻译服务时，选中的待译文本会发给该服务。
- **面板与脚本**支持扫描兼容的 CEP 面板与明文 JSX，读取词条后可手动编辑、翻译、预览和恢复。明文 JSX 可导出汉化副本。带签名的第三方 CEP 可借助 Adobe 官方 ZXPSignCmd 安装独立汉化副本。
- **外观**可选择本地图片或已安装的 Wallpaper Engine 壁纸，并调整界面样式。
- **更新**默认读取本仓库的 GitHub Releases。在「设置 → 更新」检查版本、下载并校验更新包。也可填写其他公开仓库，留空恢复官方更新源。

## 源码与开发

| 目录 | 内容 |
| --- | --- |
| `app/` | PySide6 桌面入口、QML 页面和控制器 |
| `core/` | 词典、更新、翻译和本地化逻辑 |
| `runtime/` | 运行时汉化引擎源码 |
| `native/` | 本机辅助库及 UTF-8 修复组件源码 |
| `jsx/` | AE 内运行的 ExtendScript 桥接脚本 |
| `data/` | 应用自带的词条数据 |
| `spec/` | 词典包格式规范 |
| `tests/` | 测试及合成夹具 |

构建、测试和旧词典迁移见 [开发指南](docs/DEVELOPMENT.md)。版本记录见 [CHANGELOG](CHANGELOG.md)，依赖的来源和许可见 [第三方声明](THIRD_PARTY_NOTICES.md)。

AeLocalizer 的原创代码采用 [MIT 许可证](LICENSE)。第三方组件使用各自的许可证。
