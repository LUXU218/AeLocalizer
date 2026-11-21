# 第三方声明

| 组件 | 来源与许可 |
| --- | --- |
| 面板与脚本本地化 | 改编自 [After Effects Plugin Localizer v1.4.3](https://github.com/versereyh-commits/After-Effects-Plugin-Localizer/tree/6e77e919edafaa2c1004e6035c4975e5a186225a)，见 [原始 MIT 许可](core/panel_localization/LICENSE) |
| WebWallGL | 场景壁纸渲染组件，见 [原始 MIT 许可](app/assets/webwallgl/LICENSE) |
| UTF-8 修复组件 | 见 [原始 MIT 许可](native/utf8fixer/LICENSE) |
| MinHook | [MinHook 项目](https://github.com/TsudaKageyu/minhook)，许可和署名保留在 [源文件](native/minhook/include/MinHook.h) 中 |

Python、Qt、Frida 和构建工具由各自的发行渠道安装，适用各自的许可证。After Effects SDK 由 Adobe 提供，请从 Adobe 获取并按其条款使用。第三方文件的许可适用于对应组件。

## Windows 便携包

Qt、PySide6 和 Shiboken 按 LGPL v3 分发，版本为 6.12.0。Qt WebEngine Python 包的版本为 6.12.0.140，Chromium 及其随附组件适用各自的许可。相关许可文本随便携包放在 `licenses/` 中。

- [Qt 6.12.0 对应源码](https://download.qt.io/archive/qt/6.12/6.12.0/single/)及 [PySide6 和 Shiboken 6.12.0 对应源码](https://github.com/qtproject/pyside-pyside-setup/tree/v6.12.0)
- [Qt WebEngine 6.140.0 对应源码（含 Chromium）](https://download.qt.io/archive/qtwebengine/6.140/6.140.0/)及 [Qt WebEngine 的第三方许可](https://doc.qt.io/qtwebengine/qtwebengine-licensing.html)
- [Qt Multimedia 使用的 FFmpeg 和对应许可](https://doc.qt.io/qt-6/qtmultimedia-attribution-ffmpeg.html)
- [CPython 源码](https://github.com/python/cpython/tree/v3.12.14)、[PyInstaller 源码](https://github.com/pyinstaller/pyinstaller)、[Frida 源码](https://github.com/frida/frida/tree/17.26.3)、[Capstone 源码](https://github.com/capstone-engine/capstone/tree/5.0.9)、[pefile 源码](https://github.com/erocarrera/pefile)、[cryptography 源码](https://github.com/pyca/cryptography/tree/50.0.2)

Qt 运行库通过动态链接加载。可在备份后，用 ABI 兼容的修改版替换 `_internal` 下对应的 Qt DLL 或 `PySide6`、`shiboken6` 下的 Python 扩展。可按 LGPL 条款为调试此类修改执行逆向工程。
