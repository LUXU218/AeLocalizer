# 开发与构建

## 运行与打包

请先按 [README](../README.md) 创建 Python 3.12 虚拟环境。打包前安装开发依赖，并将项目使用的 Frida 安装到 `.runtime-deps`。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m pip install --target .runtime-deps -r requirements-runtime.txt
.\.venv\Scripts\python.exe tools/build_exe.py
```

打包机需要将 64 位 MinGW-w64 GCC 的 `bin` 目录加入 `PATH`，用于编译开机待机程序和全量枚举使用的宿主身份模块。默认输出为 `dist/AeLocalizer/`。分发时请压缩整个目录，保留 `AeLocalizer.exe`、`AeLocalizerStartup.exe` 和 `_internal/` 的相对位置。

使用 64 位 MinGW-w64 GCC 时，可运行 `python tools/build_native.py` 构建 `native/` 下的可选辅助 DLL。`native/utf8fixer/` 中保留修复组件的源码。构建该组件时，请从 [Adobe After Effects 开发者站点](https://developer.adobe.com/after-effects/) 自行取得适当版本的 SDK，按本地工具链配置头文件和构建工具。

## 本地测试

工作台关闭、已有后台会话的确认与登录恢复决策由 `app/background_session.py` 管理。它通过 `BackgroundController` 读取应用状态和请求清理。Qt 工作台和任务栏入口由 `app/workspace_window.py` 管理，后台启动可以先创建任务栏入口，再按需加载 QML。效果名仍由原桌面进程中的控制器管理。

独立设置窗口使用原有的 `SettingsPage.qml`。父进程通过 `app/settings_workspace.py` 在私有子进程管道上传递外观和常规设置，父进程负责写入设置。需要词典、壁纸、更新或引擎诊断的页面由完整工作台处理。

`app/engine_session.py` 保存当前会话的通道选择、请求代次和启停结果。`AppBackend.engineSessionView` 根据它和后台回执生成工作台显示的状态。引擎页只在用户操作时发送请求，关窗或重开都从同一个后台控制器读取状态。

应用的离线回归工具和用法见 [回归记录校验器](SGLOW_REGRESSION.md)。

测试请使用独立的数据根目录。以下命令覆盖窗口几何、设置更新、Wallpaper Engine 和面板与脚本的回归检查。

```powershell
$env:AELOC_DATA_ROOT = Join-Path $PWD '.test-scratch\data'
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m unittest tests.test_window_geometry tests.test_updater tests.test_wallpaper_engine tests.test_panel_integration
```

需要真实 AE、专用旧词典或编译产物的用例，依各用例的前置条件启用。JavaScript 回归用例需要 Node.js。

## 迁移本地数据

| 环境变量 | 用途 |
| --- | --- |
| `AELOC_DATA_ROOT` | 指定应用的数据目录 |
| `AELOC_PARAM_DUMP_DIR` | 指定离线补词语料所在目录，文件按 `dump_<AE 年份>.json` 命名 |
| `AELOC_LEGACY_DICT_KEY` | 迁移旧词典时本地提供对应的格式密钥，让兼容读写沿用旧格式参数 |
| `AELOC_LEGACY_AES_MASTER_HEX` | 导入 LXAE2 词典时，本地提供 32 字节主密钥的十六进制表示 |
| `AELOC_LEGACY_ENUM_PATH` | 指定已有下拉值词典的本地文件位置 |
| `AELOC_LEGACY_GUARD_PATH` | 指定旧守护脚本的位置，供迁移时检查旧进程锁 |
| `AELOC_LEGACY_TEST_LOADER` | 本地交叉验证用的旧词典读取脚本路径 |

需要迁移旧词典时，请先备份原文件，并给迁移进程配置对应的格式密钥。新数据目录可以直接使用仓库版本的默认格式参数。

## Windows 发布资料

为公开更新源准备 Release 时，版本以 `core/version.py` 为准。使用 `vX.Y.Z` 标签和 `AeLocalizer-X.Y.Z-win64.zip` 资产文件名。更新器会对下载资产进行 SHA-256 校验。发版前从 ZIP 解压到空目录，验证 EXE 启动、版本显示和本轮修改。
