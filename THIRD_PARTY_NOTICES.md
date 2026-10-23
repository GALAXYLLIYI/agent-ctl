# 第三方许可声明（THIRD_PARTY_NOTICES.md）

> 本文件汇总 **agent-ctl 使用与再分发的第三方组件**：包名、版本、SPDX 许可证标识、来源 URL、用途、
> 许可原文位置。
>
> **本项目自身许可**：**Apache License 2.0**（见 [`LICENSE`](LICENSE)）。
> 本文件**只管第三方**，不改变本项目的许可，也不附加任何限制。
>
> **`vendor/` 目录的逐包明细（含 SHA-256 核验、取原文方式）见 [`vendor\NOTICE.md`](vendor/NOTICE.md)** ——
> 那是该目录的权威来源；本文件是面向读者的**总索引**，两者如有出入以 `vendor\NOTICE.md` 为准。

---

## 〇、这个文件为什么存在

Apache-2.0 §4(a) 要求：分发本作品时，**必须附上本许可的副本**；
§4(d) 要求：若作品（或其衍生作品）带有第三方组件的 `NOTICE` 文件，**必须保留其中的署名声明**。
此外，**每一个第三方组件自身的许可条款都必须被遵守** —— 多数组件要求「再分发时保留其版权声明与许可文本」。

本项目采用**源码态分发**（`install.ps1` 装的是 `.py` 源码 + 解释器自带的 `pythonw.exe`），
但 `vendor\` 目录里**确实含预编译的第三方二进制**（`.pyd` / `.dll`）⇒ **属于再分发，条款必须遵守**。

### 状态总览

| 组件类别 | 是否随本仓再分发 | 许可原文 | 状态 |
|---|---|---|---|
| `vendor\` 的 **8 个 wheel**（含各自 `licenses\LICENCE`） | ✅ 是 | ✅ 已随包 | ✅ **已补齐** |
| `vendor\winrt\msvcp140.dll`（微软 VC++ 运行库） | ❌ **否**（★ **C-0**，2026-10-24 已从仓中移除：**我们不分发它**） | ✅ `vendor\winrt\msvcp140-LICENCE`（条款**留档**） | ✅ **已消解**：不分发 ⇒ 4 条再分发条件全部不适用 |
| `windows-capture` 内嵌的 **44 个 Rust crate**（静态链接进 `.pyd`） | ⚠ 间接 | ❌ 未随包 | ⏳ **待核**（许可表达式已核实，见 §二） |
| `requirements.txt` 的 **14 个直接依赖** | ❌ **否**（`pip install` 装的，不进 zip） | ❌ 不需随包 | ✅ 许可标识**已核实**，见 §三 |

> **一句话结论（已核实）**：本仓再分发的第三方组件**没有一个是 copyleft**，
> 与 Apache-2.0 **全部兼容**。★ 原先唯一需要额外处理的**微软 VC++ DLL** 已按 **C-0**
> （2026-10-24）**不再分发** ⇒ 不再需要任何人为此拍板（详见 §1.1）。

---

## 一、`vendor\` 内置的再分发组件

这些包**随源码一起分发**（`tools\pack_dist.py` 的 `DIRS` 里有 `vendor`），
运行时被 `src\agentctl\clock.py` 插入 `sys.path`（`clock.py:153-154`）后加载。

| # | 包名 / 组件 | 版本 | 许可证（SPDX） | 来源 URL | 用途 | 许可原文 | 已修改 |
|---|---|---|---|---|---|---|---|
| 1 | `windows-capture` | 2.0.1 | `MIT` | <https://github.com/NiiightmareXD/windows-capture> | 原生窗口/屏幕捕获（Rust 扩展 `.pyd`） | ✅ `vendor\windows_capture-2.0.1.dist-info\licenses\LICENCE` | 否 |
| 2 | `winrt-runtime` | 3.2.1 | `MIT` | <https://github.com/pywinrt/pywinrt> | WinRT 运行时绑定（`winrt\runtime`） | ✅ `vendor\winrt_runtime-3.2.1.dist-info\licenses\LICENCE` | 否 |
| 3 | `winrt-Windows.Foundation` | 3.2.1 | `MIT` | <https://github.com/pywinrt/pywinrt> | WinRT 基础类型 | ✅ `vendor\winrt_windows_foundation-3.2.1.dist-info\licenses\LICENCE` | 否 |
| 4 | `winrt-Windows.Foundation.Collections` | 3.2.1 | `MIT` | <https://github.com/pywinrt/pywinrt> | WinRT 集合类型 | ✅ `vendor\winrt_windows_foundation_collections-3.2.1.dist-info\licenses\LICENCE` | 否 |
| 5 | `winrt-Windows.Globalization` | 3.2.1 | `MIT` | <https://github.com/pywinrt/pywinrt> | WinRT 区域/全球化（OCR 用） | ✅ `vendor\winrt_windows_globalization-3.2.1.dist-info\licenses\LICENCE` | 否 |
| 6 | `winrt-Windows.Graphics.Imaging` | 3.2.1 | `MIT` | <https://github.com/pywinrt/pywinrt> | WinRT 图像解码（OCR 前处理） | ✅ `vendor\winrt_windows_graphics_imaging-3.2.1.dist-info\licenses\LICENCE` | 否 |
| 7 | `winrt-Windows.Media.Ocr` | 3.2.1 | `MIT` | <https://github.com/pywinrt/pywinrt> | **系统自带 OCR**（不额外下模型） | ✅ `vendor\winrt_windows_media_ocr-3.2.1.dist-info\licenses\LICENCE` | 否 |
| 8 | `winrt-Windows.Storage.Streams` | 3.2.1 | `MIT` | <https://github.com/pywinrt/pywinrt> | WinRT 流类型（图像数据搬运） | ✅ `vendor\winrt_windows_storage_streams-3.2.1.dist-info\licenses\LICENCE` | 否 |
| 9 | Microsoft® Visual C++ v14 运行时（`msvcp140.dll`） | 14.29.30157.0 | 专有（`LicenseRef-Microsoft-VC14-Redist`） | <https://aka.ms/VCRedistLicense> | VC++ 运行时（由 `winrt-runtime` 的 wheel 收录） | ✅ `vendor\winrt\msvcp140-LICENCE` | 否 |

> 8 个 wheel 的 `License-Expression` 均为 **`MIT`**，取自各自 `dist-info\METADATA`（**已核实**）。
> 7 份 `winrt_*` 的 MIT 原文**逐字节取自上游 Git 标签 `v3.2.1` 的 `LICENSE`**（SHA-256 核验见 `vendor\NOTICE.md` §2.2）。
> **全部组件均未修改** ⇒ 不产生需要额外声明的修改。

### 1.1 `vendor\winrt\msvcp140.dll`（★ **C-0 已落地：不再分发**）

| 项 | 内容 |
|---|---|
| 文件 | `vendor\winrt\msvcp140.dll`，**567,352 字节**（已核实） |
| 性质 | **微软 Visual C++ 运行库 DLL**，非 pywinrt 自己的代码 |
| 许可证 | **Microsoft 专有许可**（SPDX 无对应标识，可记作 `LicenseRef-Microsoft-VC14-Redist`）；**允许在满足条件的前提下再分发** |
| 许可原文 | ✅ `vendor\winrt\msvcp140-LICENCE`（附注 + Microsoft 官方条款原文；官方 EULA ID `Cpp_v14_ENU.1033`） |
| 被再分发的路径 | ★ **已无**（C-0：文件已从仓中移除；`tools\pack_dist.py` 打的 `vendor` 里不再有它）⇒ 装出来的程序走**目标机 `System32\msvcp140.dll` 自带那份**；目标机缺时由 `install.ps1` **指引**去微软官网装官方 vcredist（只指路，自己不发） |
| 条件 ① | **必须保持未修改** —— ✅ 已满足（SHA-256 与上游 wheel `RECORD` 登记值一致） |
| 条件 ② | **必须随附 Microsoft 条款原文** —— ✅ 已满足 |
| 条件 ③ | **只能作为独立文件随程序加载分发**（mere aggregation），不得静态链接进单一作品 —— ✅ 本项目以独立文件 + 运行时加载使用 |
| 条件 ④ | **再分发者须具备有效 Visual Studio 许可** —— ✅ **已消解**（C-0：本仓**不再分发**该文件 ⇒ 没有再分发方义务 ⇒ 不需要任何人拍板） |

> ⛔ **发布前请维护者确认条件 ④**：Microsoft 条款写明再分发 "limited to licensed Visual Studio users"。
> 这一条由**分发方 / 维护者**负责确认，本文件不作保证。

---

## 二、`windows-capture` 内嵌的 Rust 静态依赖

`windows_capture.pyd` 是 Rust 编译产物，**静态链接**了下列 crate。
清单**直接取自仓内 SBOM 文件** `vendor\windows_capture-2.0.1.dist-info\sboms\windows-capture-python.cyclonedx.json`
（**已核实**，非人工拼写）。

| crate | 版本 | 许可证（SBOM `licenses[].expression`） |
|---|---|---|
| `windows-capture` | 2.0.1 | `MIT` |
| `windows` | 0.62.2 | `MIT OR Apache-2.0` |
| `windows-core` | 0.62.2 | `MIT OR Apache-2.0` |
| `windows-collections` | 0.3.2 | `MIT OR Apache-2.0` |
| `windows-future` | 0.3.2 | `MIT OR Apache-2.0` |
| `windows-implement` | 0.60.2 | `MIT OR Apache-2.0` |
| `windows-interface` | 0.59.3 | `MIT OR Apache-2.0` |
| `windows-link` | 0.2.1 | `MIT OR Apache-2.0` |
| `windows-numerics` | 0.3.1 | `MIT OR Apache-2.0` |
| `windows-result` | 0.4.1 | `MIT OR Apache-2.0` |
| `windows-strings` | 0.5.1 | `MIT OR Apache-2.0` |
| `windows-threading` | 0.2.1 | `MIT OR Apache-2.0` |
| `pyo3` | 0.29.0 | `MIT OR Apache-2.0` |
| `pyo3-build-config` | 0.29.0 | `MIT OR Apache-2.0` |
| `pyo3-ffi` | 0.29.0 | `MIT OR Apache-2.0` |
| `pyo3-macros` | 0.29.0 | `MIT OR Apache-2.0` |
| `pyo3-macros-backend` | 0.29.0 | `MIT OR Apache-2.0` |
| `bitflags` | 2.10.0 | `MIT OR Apache-2.0` |
| `cfg-if` | 1.0.4 | `MIT OR Apache-2.0` |
| `crossbeam-deque` | 0.8.6 | `MIT OR Apache-2.0` |
| `crossbeam-epoch` | 0.9.18 | `MIT OR Apache-2.0` |
| `crossbeam-utils` | 0.8.21 | `MIT OR Apache-2.0` |
| `either` | 1.15.0 | `MIT OR Apache-2.0` |
| `heck` | 0.5.0 | `MIT OR Apache-2.0` |
| `libc` | 0.2.186 | `MIT OR Apache-2.0` |
| `lock_api` | 0.4.14 | `MIT OR Apache-2.0` |
| `once_cell` | 1.21.4 | `MIT OR Apache-2.0` |
| `parking_lot` | 0.12.5 | `MIT OR Apache-2.0` |
| `parking_lot_core` | 0.9.12 | `MIT OR Apache-2.0` |
| `portable-atomic` | 1.13.1 | `Apache-2.0 OR MIT` |
| `proc-macro2` | 1.0.106 | `MIT OR Apache-2.0` |
| `quote` | 1.0.46 | `MIT OR Apache-2.0` |
| `rayon` | 1.12.0 | `MIT OR Apache-2.0` |
| `rayon-core` | 1.13.0 | `MIT OR Apache-2.0` |
| `redox_syscall` | 0.5.17 | `MIT` |
| `scopeguard` | 1.2.0 | `MIT OR Apache-2.0` |
| `smallvec` | 1.15.1 | `MIT OR Apache-2.0` |
| `syn` | 2.0.118 | `MIT OR Apache-2.0` |
| `target-lexicon` | 0.13.5 | ⚠ `Apache-2.0 WITH LLVM-exception` |
| `thiserror` | 2.0.18 | `MIT OR Apache-2.0` |
| `thiserror-impl` | 2.0.18 | `MIT OR Apache-2.0` |
| `unicode-ident` | 1.0.24 | ⚠ `(MIT OR Apache-2.0) AND Unicode-3.0` |

> **结论（已核实）**：以上 crate **没有一个是 copyleft**（无 GPL / LGPL / AGPL），与 Apache-2.0 **全部兼容**。
> 两处需额外留意：`target-lexicon`（Apache-2.0 **带 LLVM 例外**）与 `unicode-ident`（额外含 **Unicode-3.0** 许可）。
>
> ⏳ **待核**：这些 crate 的**许可原文未随包**。是否需要逐个附上，取决于分发形态与上游实践 ——
> 由维护者决定；**当前按「不附」处理，但清单在此备查**。

---

## 三、`requirements.txt` 的直接依赖

`requirements.txt` 共 **14 条依赖条目**（9 个常规 + 5 个 `winrt-Windows.*`）。

> ℹ️ **这些包不由本仓再分发** —— `install.ps1` 第 4 步用 `pip install -r requirements.txt` 装它们，
> **不进 `dist\agentctl.zip`**。因此**没有「随附许可原文」的义务**，但**各自的许可条款仍必须遵守**
> （例如 PSF / BSD / MIT 的「保留声明」条款）。下表列出是为了**透明**。

「版本下限」= `requirements.txt` 写的下限；「本机实测版本」= 核验本文件时本机 `pip` 装着的版本。

| # | 包名 | 版本下限 | 本机实测版本 | 许可证（SPDX） | 来源 URL | 用途 |
|---|---|---|---|---|---|---|
| 1 | `pywin32` | `>=312` | 312 | `PSF-2.0`（包元数据写 `PSF`） | <https://github.com/mhammond/pywin32> | Win32 API：窗口枚举、键鼠注入、剪贴板 |
| 2 | `uiautomation` | `>=2.0.20` | 2.0.29 | `Apache-2.0` | <https://github.com/yinkaisheng/Python-UIAutomation-for-Windows> | UI Automation 控件树 |
| 3 | `comtypes` | `>=1.2` | 1.4.16 | `MIT` | <https://github.com/enthought/comtypes> | COM 调用 |
| 4 | `pillow` | `>=10.0` | 12.3.0 | `MIT-CMU` | <https://github.com/python-pillow/Pillow> | 图像处理、截图编解码 |
| 5 | `mss` | `>=9.0` | 10.2.0 | `MIT` | <https://github.com/BoboTiG/python-mss> | 跨平台屏幕捕获 |
| 6 | `numpy` | `>=1.24` | 2.5.2 | `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0` | <https://numpy.org> | 像素差分、数组运算 |
| 7 | `psutil` | `>=5.9` | 7.2.2 | `BSD-3-Clause` | <https://github.com/giampaolo/psutil> | 进程/窗口枚举 |
| 8 | `websocket-client` | `>=1.7` | 1.9.0 | `Apache-2.0` | <https://github.com/websocket-client/websocket-client> | CDP（浏览器调试协议）通道 |
| 9 | `zstandard` | `>=0.21` | 0.25.0 | `BSD-3-Clause` | <https://github.com/indygreg/python-zstandard> | 时间线压缩 |
| 10 | `winrt-Windows.Media.Ocr` | `>=3.2.0` | 3.2.1 | `MIT` | <https://github.com/pywinrt/pywinrt> | 系统 OCR（路线①） |
| 11 | `winrt-Windows.Graphics.Imaging` | `>=3.2.0` | 3.2.1 | `MIT` | <https://github.com/pywinrt/pywinrt> | 图像解码 |
| 12 | `winrt-Windows.Storage.Streams` | `>=3.2.0` | 3.2.1 | `MIT` | <https://github.com/pywinrt/pywinrt> | 流类型 |
| 13 | `winrt-Windows.Globalization` | `>=3.2.0` | 3.2.1 | `MIT` | <https://github.com/pywinrt/pywinrt> | 区域/语言（OCR 用） |
| 14 | `winrt-Windows.Foundation` | `>=3.2.0` | 3.2.1 | `MIT` | <https://github.com/pywinrt/pywinrt> | WinRT 基础类型 |

> **许可证核实方式**：上表「许可证」列取自**本机 `importlib.metadata` 读到的包元数据**
> （`License-Expression` / `License` / `Classifier: License ::` 三处，以最具体者为准）—— **已核实**。
>
> ⚠ `numpy` 的表达式是**复合许可**（BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0），
> 需按其 `LICENSE.txt` 的分节逐条履行。
> ⚠ `pillow` 的 `MIT-CMU` 与普通 MIT **不是同一份文本**，别拿 MIT 顶替。
>
> **版本策略**：本仓**只卡下限**（`>=x.y`），不锁死上限 —— 详见 `requirements.txt` 头部注释。
> 要固定环境请 `pip freeze > requirements.lock.txt` 自己留档。

### 3.1 故意不列的包

`requirements.txt` 尾部注明**故意不列**：`opencv-python` / `matplotlib` / `python-docx` / `PyMuPDF`
（论文类一次性脚本用，不在控制链路上；列进来会让安装包体积翻几倍）。
⚠ 注意：`windows-capture` 的元数据声明 `Requires-Dist: opencv-python`，但**本仓不在控制链路上直接用它** ⇒ 待核是否真会被装进来。

---

## 四、待核 / 待办清单

| # | 事项 | 状态 | 备注 |
|---|---|---|---|
| 1 | 8 个 `vendor` wheel 的许可原文 | ✅ **已补齐** | 位置见 §一；`vendor\NOTICE.md` 有 SHA-256 核验 |
| 2 | `msvcp140.dll` 的微软条款原文 | ✅ **已补齐** | `vendor\winrt\msvcp140-LICENCE` |
| 3 | ~~**`msvcp140.dll` 条件 ④：分发方须持有效 VS 许可**~~ | ✅ **已消解** | ★ **C-0（2026-10-24）**：该 DLL 已从仓中移除、**本仓不再分发** ⇒ 四条再分发条件全部不适用 ⇒ 条件 ④ 不再需要任何人拍板 |
| 4 | Rust crate 的许可原文（44 项） | ⏳ **待核** | crate 名与许可表达式**已核实**（见 §二），缺的是原文；是否需附由维护者定 |
| 5 | `windows-capture` 是否真会拉入 `opencv-python` | ⏳ **待核** | 元数据声明了 `Requires-Dist`，但本仓未在链路使用 |
| 6 | `pywin32` 的 `PSF` 标识与 SPDX `PSF-2.0` 的对应 | ✅ 已核实（标识） | 包元数据写 `PSF` |
| 7 | 本仓根的 `NOTICE` 文件 | ❌ **不存在** | Apache-2.0 §4(d) 只在**上游带 NOTICE** 时才要求保留；本仓无需自造。`vendor\NOTICE.md` 是 vendor 自己的，随包带走即可 |
| 8 | 新增许可文件未登记进上游 wheel 的 `RECORD` | ⏳ 已知 | 见 `vendor\NOTICE.md` §4.2：不影响许可随附效力；若重新打包这些 wheel 应一并写入 |
| 9 | `vendor\NOTICE.md` 正文仍以 GPL-3.0 为主要口径叙述 | ⏳ **待更新** | 该文件已自行标注「作业期间 `LICENSE` 被改为 Apache-2.0」并给出两种口径结论（§3.4）；但 §3 标题仍写「与 GPL-3.0 的兼容性结论」。**vendor 目录归另一窗口所有**，由该窗口统一改写 |

---

## 五、维护约定

1. **新增依赖必须同时改本文件**：加一行（包名 / 版本 / SPDX / URL / 用途 / 原文位置 / 是否修改）。
2. **改依赖版本也要改本文件**：版本号变了，表里的「本机实测版本」与「版本下限」都要同步。
3. **改 `vendor\` 里的组件也要同步改 `vendor\NOTICE.md`** —— 那是该目录的权威来源。
4. **打包要把本文件带上**：`tools\pack_dist.py` 的 `FILES` 白名单里必须有 `THIRD_PARTY_NOTICES.md`
   —— 否则**打包出去的作品不带第三方声明 = 每次分发都不合规**。
   同理，`vendor\NOTICE.md` 与 8 份 `licenses\LICENCE` 也必须进包（`vendor` 已在 `DIRS` 里，随包带走）。

---

Copyright (c) 2026 <作者署名占位：用户待定，主控收口时替换>
本项目采用 Apache License 2.0，详见 [`LICENSE`](LICENSE)。
