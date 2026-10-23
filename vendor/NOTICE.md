# `vendor/` 第三方组件许可与来源说明（NOTICE）

本文件汇总 `vendor/` 目录下全部第三方组件的**包名、版本、许可证（SPDX）、许可原文位置、
上游来源 URL 与修改状态**，供本项目**以 Apache License 2.0 分发**时随附查阅
（对应 Apache-2.0 §4(a)「随附许可原文」与 §4(d)「保留 NOTICE 声明」）。

- ★ **本项目自身许可 = Apache License 2.0**（见仓库根目录 `LICENSE`，2026-10-23 由用户拍板确定）。
  ⇒ **当前生效的兼容性结论见 §3.4**（Apache-2.0 口径）。
  ⇒ §3.1/§3.2 是**补齐本文件时的历史口径（GPL-3.0）**，**仅存档**，不再适用于本项目的对外分发
  （Apache-2.0 → GPLv3 为**单向不兼容**，本仓不得再以 GPL-3.0 分发）。两种口径下
  `vendor/` 全部组件的兼容性结论**一致**。
- 每个包的**许可原文**随包放在其 `*-<ver>.dist-info/licenses/LICENCE`；Microsoft 运行时条款见
  `vendor/winrt/msvcp140-LICENCE`。
- ★ **2026-10-24（C-0 落地）**：`vendor\winrt\msvcp140.dll`（微软 VC++ 运行时）**已从本仓移除** ——
  本项目**不再分发**它，装出来的程序走**目标机 `System32\msvcp140.dll` 自带那份**
  （缺时由 `install.ps1` **指引**用户去微软官网装官方 vcredist，我们只指路、自己不发）。
  ⇒ 「一个微软专有二进制都不发」⇒ 微软条款里 "limited to licensed Visual Studio users"
  这条**不再触发** ⇒ 原先唯一需要用户拍板的再分发条件**消解**（不需要任何人持 VS 许可）。
  ⇒ `vendor\winrt\msvcp140-LICENCE`（条款原文）**保留在仓**，作为这次判读的留档。
- 所有许可原文均为**逐字**取自上游官方来源，未改写、未概括、未代为编写。

---

## 1. 组件总览

| # | 包名 / 组件 | 版本 | 许可证 (SPDX) | 许可原文（相对仓库根路径） | 上游 URL | 是否修改 |
|---|---|---|---|---|---|---|
| 1 | windows-capture | 2.0.1 | `MIT` | `vendor/windows_capture-2.0.1.dist-info/licenses/LICENCE` | https://github.com/NiiightmareXD/windows-capture | **未修改** |
| 2 | winrt-runtime | 3.2.1 | `MIT` | `vendor/winrt_runtime-3.2.1.dist-info/licenses/LICENCE` | https://github.com/pywinrt/pywinrt | **未修改** |
| 3 | winrt-Windows.Foundation | 3.2.1 | `MIT` | `vendor/winrt_windows_foundation-3.2.1.dist-info/licenses/LICENCE` | https://github.com/pywinrt/pywinrt | **未修改** |
| 4 | winrt-Windows.Foundation.Collections | 3.2.1 | `MIT` | `vendor/winrt_windows_foundation_collections-3.2.1.dist-info/licenses/LICENCE` | https://github.com/pywinrt/pywinrt | **未修改** |
| 5 | winrt-Windows.Globalization | 3.2.1 | `MIT` | `vendor/winrt_windows_globalization-3.2.1.dist-info/licenses/LICENCE` | https://github.com/pywinrt/pywinrt | **未修改** |
| 6 | winrt-Windows.Graphics.Imaging | 3.2.1 | `MIT` | `vendor/winrt_windows_graphics_imaging-3.2.1.dist-info/licenses/LICENCE` | https://github.com/pywinrt/pywinrt | **未修改** |
| 7 | winrt-Windows.Media.Ocr | 3.2.1 | `MIT` | `vendor/winrt_windows_media_ocr-3.2.1.dist-info/licenses/LICENCE` | https://github.com/pywinrt/pywinrt | **未修改** |
| 8 | winrt-Windows.Storage.Streams | 3.2.1 | `MIT` | `vendor/winrt_windows_storage_streams-3.2.1.dist-info/licenses/LICENCE` | https://github.com/pywinrt/pywinrt | **未修改** |
| 9 | Microsoft® Visual C++ v14 Runtime（`msvcp140.dll`） | 14.29.30157.0 | 专有（Microsoft 专有许可；SPDX 无对应标识，可记作 `LicenseRef-Microsoft-VC14-Redist`） | `vendor/winrt/msvcp140-LICENCE`（**条款留档**） | https://aka.ms/VCRedistLicense | **已按 C-0 移除（2026-10-24）：本仓不再分发** |

> 除第 9 项外，**其余全部组件均未修改**；第 9 项原为**未修改**（哈希核验见第 4 节），
> 现**已从仓中移除**（C-0）⇒ 它不再计入"随本仓分发"的集合，也不再产生再分发条件。
> 因此本项目对上述第三方代码不产生需要额外声明的修改，GPL-3.0 第 5 条意义上的
> 「已修改声明」仅需针对本项目自身代码作出。

---

## 2. 逐包明细

### 2.1 windows-capture 2.0.1

| 项 | 内容 |
|---|---|
| 包名 / 版本 | `windows-capture` / 2.0.1 |
| 许可证 SPDX | `MIT` |
| 许可原文 | `vendor/windows_capture-2.0.1.dist-info/licenses/LICENCE`（随上游 wheel 自带，仓内原已存在；本次未改动该文件） |
| 原文版权行 | `Copyright (c) 2023 NiiightmareXD` |
| 取原文方式 | 随 wheel 自带（原文已在仓内，非本次新增） |
| 上游 URL | https://github.com/NiiightmareXD/windows-capture ；PyPI: https://pypi.org/project/windows-capture/2.0.1/ |
| 元数据佐证 | `METADATA` 内含 `License-Expression: MIT`、`Classifier: License :: OSI Approved :: MIT License`、`License-File: LICENCE` |
| 是否修改 | **未修改** |
| 对应源代码 | 上游 2.0.1 源码（同上 URL），本仓所含即未改动的上游代码 |

### 2.2 winrt-runtime 3.2.1（★ 原含 `vendor/winrt/msvcp140.dll`，该 DLL 已按 C-0 移除）

| 项 | 内容 |
|---|---|
| 包名 / 版本 | `winrt-runtime` / 3.2.1 |
| 许可证 SPDX | `MIT` |
| 许可原文 | `vendor/winrt_runtime-3.2.1.dist-info/licenses/LICENCE` |
| 原文版权行 | `Copyright (c) Microsoft Corporation. All rights reserved.` ／ `Copyright (c) 2021-2025 David Lechner <david@pybricks.com>` |
| 取原文方式 | **逐字**取自上游 Git 标签 `v3.2.1` 的 `LICENSE`：`https://raw.githubusercontent.com/pywinrt/pywinrt/v3.2.1/LICENSE`；并经 GitHub contents API 的 base64 blob 复核。落盘文件 1225 字节，SHA-256 `6E898069E8B3C6D8D23DC70AC7067CC2B7C9DB14C36DF873026758DB82C0891D`，与上游**逐字节一致**（含上游原文每行 4 空格缩进、末行 `SOFTWARE` 无句点等特征，未作任何"规范化"） |
| 上游 URL | https://github.com/pywinrt/pywinrt ；文档 https://pywinrt.readthedocs.io ；PyPI: https://pypi.org/project/winrt-runtime/3.2.1/ |
| 元数据佐证 | `METADATA` 内含 `License-Expression: MIT` |
| 是否修改 | **未修改** |
| 对应源代码 | 上游 3.2.1 源码（同上 URL），本仓所含即未改动的上游代码 |

### 2.3 winrt-Windows.Foundation 3.2.1

| 项 | 内容 |
|---|---|
| 包名 / 版本 | `winrt-Windows.Foundation` / 3.2.1 |
| 许可证 SPDX | `MIT` |
| 许可原文 | `vendor/winrt_windows_foundation-3.2.1.dist-info/licenses/LICENCE`（与 2.2 同为 pywinrt 上游同一份 LICENSE，逐字一致） |
| 取原文方式 | 同 2.2（GitHub `pywinrt/pywinrt` 标签 `v3.2.1` 的 `LICENSE`，逐字节一致） |
| 上游 URL | https://github.com/pywinrt/pywinrt ；PyPI: https://pypi.org/project/winrt-Windows.Foundation/3.2.1/ |
| 元数据佐证 | `METADATA` 内含 `License-Expression: MIT` |
| 是否修改 | **未修改** |

### 2.4 winrt-Windows.Foundation.Collections 3.2.1

| 项 | 内容 |
|---|---|
| 包名 / 版本 | `winrt-Windows.Foundation.Collections` / 3.2.1 |
| 许可证 SPDX | `MIT` |
| 许可原文 | `vendor/winrt_windows_foundation_collections-3.2.1.dist-info/licenses/LICENCE` |
| 取原文方式 | 同 2.2（逐字节一致） |
| 上游 URL | https://github.com/pywinrt/pywinrt ；PyPI: https://pypi.org/project/winrt-Windows.Foundation.Collections/3.2.1/ |
| 元数据佐证 | `METADATA` 内含 `License-Expression: MIT` |
| 是否修改 | **未修改** |

### 2.5 winrt-Windows.Globalization 3.2.1

| 项 | 内容 |
|---|---|
| 包名 / 版本 | `winrt-Windows.Globalization` / 3.2.1 |
| 许可证 SPDX | `MIT` |
| 许可原文 | `vendor/winrt_windows_globalization-3.2.1.dist-info/licenses/LICENCE` |
| 取原文方式 | 同 2.2（逐字节一致） |
| 上游 URL | https://github.com/pywinrt/pywinrt ；PyPI: https://pypi.org/project/winrt-Windows.Globalization/3.2.1/ |
| 元数据佐证 | `METADATA` 内含 `License-Expression: MIT` |
| 是否修改 | **未修改** |

### 2.6 winrt-Windows.Graphics.Imaging 3.2.1

| 项 | 内容 |
|---|---|
| 包名 / 版本 | `winrt-Windows.Graphics.Imaging` / 3.2.1 |
| 许可证 SPDX | `MIT` |
| 许可原文 | `vendor/winrt_windows_graphics_imaging-3.2.1.dist-info/licenses/LICENCE` |
| 取原文方式 | 同 2.2（逐字节一致） |
| 上游 URL | https://github.com/pywinrt/pywinrt ；PyPI: https://pypi.org/project/winrt-Windows.Graphics.Imaging/3.2.1/ |
| 元数据佐证 | `METADATA` 内含 `License-Expression: MIT` |
| 是否修改 | **未修改** |

### 2.7 winrt-Windows.Media.Ocr 3.2.1

| 项 | 内容 |
|---|---|
| 包名 / 版本 | `winrt-Windows.Media.Ocr` / 3.2.1 |
| 许可证 SPDX | `MIT` |
| 许可原文 | `vendor/winrt_windows_media_ocr-3.2.1.dist-info/licenses/LICENCE` |
| 取原文方式 | 同 2.2（逐字节一致） |
| 上游 URL | https://github.com/pywinrt/pywinrt ；PyPI: https://pypi.org/project/winrt-Windows.Media.Ocr/3.2.1/ |
| 元数据佐证 | `METADATA` 内含 `License-Expression: MIT` |
| 是否修改 | **未修改** |

### 2.8 winrt-Windows.Storage.Streams 3.2.1

| 项 | 内容 |
|---|---|
| 包名 / 版本 | `winrt-Windows.Storage.Streams` / 3.2.1 |
| 许可证 SPDX | `MIT` |
| 许可原文 | `vendor/winrt_windows_storage_streams-3.2.1.dist-info/licenses/LICENCE` |
| 取原文方式 | 同 2.2（逐字节一致） |
| 上游 URL | https://github.com/pywinrt/pywinrt ；PyPI: https://pypi.org/project/winrt-Windows.Storage.Streams/3.2.1/ |
| 元数据佐证 | `METADATA` 内含 `License-Expression: MIT` |
| 是否修改 | **未修改** |

### 2.9 Microsoft® Visual C++ v14 Runtime — `msvcp140.dll`

| 项 | 内容 |
|---|---|
| 组件 | `msvcp140.dll`（Microsoft® C Runtime Library，x64） |
| 版本 | 14.29.30157.0（`FileVersion` / `ProductVersion`；`built by: cloudtest`；对应 Visual Studio 2019 v142 运行时） |
| 许可证 | **Microsoft 专有许可**（SPDX 无对应标识；可记作 `LicenseRef-Microsoft-VC14-Redist`）。允许在满足条件的前提下再分发，**非** GPL-only，**非**禁止再分发条款 |
| 许可原文 | `vendor/winrt/msvcp140-LICENCE`（附注 + Microsoft 官方条款原文；官方 EULA ID: `Cpp_v14_ENU.1033`，Last Updated: October 1, 2025） |
| 取原文方式 | **逐字**取自 Microsoft 官方文档 `https://visualstudio.microsoft.com/wp-content/uploads/2025/10/Visual-C-V14-License-Redistributable_and_Runtime_ENU.docx`（官方入口 `https://aka.ms/VCRedistLicense` → `https://visualstudio.microsoft.com/license-terms/vs2026-ga-visualcpp-v14-redist-runtime/`），抽取为纯文本；措辞、标点、段落与法文条款逐字保留，仅丢失加粗等排版格式 |
| 再分发依据 | https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files —— "Distribution of the Visual C++ Runtime Redistributable package, merge modules, and individual binaries is limited to licensed Visual Studio users and is subject to Microsoft Software License Terms."<br>REDIST 清单（Visual Studio 2019）：https://learn.microsoft.com/en-us/visualstudio/releases/2019/redistribution —— "[VisualStudioFolder]\VC\redist" 下文件 "you may copy and distribute with your program … You may not modify these files." |
| 上游 URL | https://aka.ms/VCRedistLicense ；https://aka.ms/VisualStudioLicenses ；https://visualstudio.microsoft.com/license-terms/ |
| 归属包 | `winrt-runtime` 3.2.1（该 DLL 由 `winrt_runtime-3.2.1.dist-info/RECORD` 收录，随 pywinrt 官方 wheel 分发） |
| 是否修改 | **未修改**。SHA-256 `75872c426555ac8af55b5a7f6d4aea8831af74353b22612850a2bfc40e3b67e1`，与 RECORD 登记值 `sha256=dYcsQmVVrIr1W1p_bUrqiDGvdDU7ImEoUKK_xA47Z-E` 一致 |
| 对应源代码 | 该运行时为闭源专有二进制，Microsoft 未提供其源代码；其再分发权以「原样再分发」为限（见第 3 节条件） |

> ★ **2026-10-24 C-0 现状（这一节现在的结论）**：本仓**不再分发** `msvcp140.dll`
> （文件已从仓中移除；程序走目标机 `System32` 自带那份）。
> ⇒ 下面记的再分发依据与四条条件**全部不再适用**（不分发 ⇒ 没有再分发方义务）⇒
>   「再分发者须持有效 Visual Studio 许可」这条**不需要任何人拍板**。
> ⇒ 本节保留原文，作为**判读留档**（条款原文仍在 `vendor/winrt/msvcp140-LICENCE`）。

---

## 3. 兼容性结论（逐包）—— ★ **主口径：Apache-2.0（见 3.4）**；3.1/3.2 为 GPL-3.0 历史口径（仅存档）

本项目自身采用 **GPL-3.0**（作业期间根 `LICENSE` 被并行窗口改为 Apache-2.0，补充结论见 3.4）。
逐项结论如下：

### 3.1 MIT 组件（windows-capture 2.0.1、winrt-runtime 3.2.1 及 6 个 winrt-Windows.* 3.2.1）—— **兼容**

- MIT 是宽松许可，与 GPL-3.0 **单向兼容**（MIT → GPL-3.0）：可与 GPL-3.0 作品合并，整体按 GPL-3.0 分发。
- MIT 不含任何 GPL-only 条款、不含禁止再分发条款、不含要求本项目代码保持专有的条款。
- 满足条件（已满足）：
  1. **随附版权与许可声明**：MIT 要求 "The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software" —— 已按包随附原文（`*/licenses/LICENCE`），并在分发时须与二进制一同提供。
  2. **未修改**：本项目未改动这些组件，故 GPL-3.0 第 5 条的「修改声明」不涉及它们；GPL-3.0 第 6 条所要求的「对应源代码」即为上游同版本源码（URL 见第 2 节），随许可声明一并提供即可。
  3. `LICENSE`（MIT）不得改写 —— 落盘文件与上游逐字节一致，已核验。

### 3.2 `msvcp140.dll`（Microsoft VC++ 运行时）—— ★ **已按 C-0 移除 ⇒ 本节条件全部不再适用**

- ★ **2026-10-24 C-0 之后的结论（现在的口径）**：本仓**不再分发**该文件 ⇒
  下面这四条再分发条件（保持未修改／随附条款／独立文件不静态链接／**分发方持有效 VS 许可**）
  **全部不再适用** ⇒ 「唯一需要在对外发布前由维护者显式确认的条件 ④」**已消解**。
- 以下为**移除前的判读留档**（原文保留，未改写）：

- 性质：**专有但允许再分发**。它**不是** GPL-only 许可，**也不是**禁止再分发的条款，因此不与 GPL-3.0 直接冲突。
- 但它带有必须遵守的再分发条件，任一条不满足即构成冲突，故列为「条件兼容」：
  1. **必须保持未修改** —— 已满足（哈希与上游 wheel RECORD 一致）。
  2. **必须随附 Microsoft 条款原文** —— 已满足（`vendor/winrt/msvcp140-LICENCE`）。分发时该文件必须与 DLL 一同提供。
  3. **只能作为独立文件随程序加载分发（mere aggregation）** —— 不得把该 DLL 静态链接进 GPL 程序形成单一作品，也不得对本项目的 GPL 代码施加使其无法与该 DLL 分开修改的约束。否则将出现「GPL-3.0 要求用户可修改该衍生作品」与「Microsoft 条款禁止修改该 DLL」的直接冲突。**本项目当前以独立文件 + 运行时加载方式使用，符合此条件。**
  4. **再分发者须具备有效 Visual Studio 许可** —— Microsoft 条款原文写明再分发 "limited to licensed Visual Studio users"。此项由**分发方 / 维护者**负责确认，本文件不作保证。
- 结论：**在满足上述 4 条的前提下，与 GPL-3.0 兼容**；第 9 项是本仓唯一需要按条件处理、也是唯一需要在对外发布前由维护者显式确认（条件 4）的条目。

### 3.3 汇总

| 组件 | 许可 | 与 GPL-3.0 | 是否需要额外动作 |
|---|---|---|---|
| windows-capture 2.0.1 | MIT | 兼容 | 无（随附原文即可） |
| winrt-runtime 3.2.1 | MIT | 兼容 | 无（随附原文即可） |
| winrt-Windows.Foundation 3.2.1 | MIT | 兼容 | 无 |
| winrt-Windows.Foundation.Collections 3.2.1 | MIT | 兼容 | 无 |
| winrt-Windows.Globalization 3.2.1 | MIT | 兼容 | 无 |
| winrt-Windows.Graphics.Imaging 3.2.1 | MIT | 兼容 | 无 |
| winrt-Windows.Media.Ocr 3.2.1 | MIT | 兼容 | 无 |
| winrt-Windows.Storage.Streams 3.2.1 | MIT | 兼容 | 无 |
| `msvcp140.dll` 14.29.30157.0 | 专有（**但本仓已不再分发**） | **不适用**（已按 C-0 移除，2026-10-24） | **无**：文件已从仓中移除 ⇒ 四条再分发条件全部不再适用；程序走目标机 `System32` 自带那份 |

**未发现任何 GPL-only 或禁止再分发的第三方许可**；不存在必须点名报回的「不兼容项」，
但第 3.2 节的 `msvcp140.dll` 属**带条件的专有项**，已在表中显式标注。

### 3.4 **★ 主口径（当前生效）**：与 Apache-2.0 的兼容性结论（项目许可 = Apache License 2.0）

本次作业期间，仓库根 `LICENSE` 被其它并行窗口由 GPL-3.0 全文改写为 **Apache License 2.0**
（作业开始时读取到的仍是 GPL-3.0）。本节给出同口径结论，供按项目最终许可分发时选用：

- **MIT ×8**：`MIT → Apache-2.0` 单向兼容（可并入 Apache-2.0 作品整体分发）。Apache-2.0 第 4 条
  要求随附许可副本、保留版权与声明、不对衍生作品施加额外限制 —— 由随包放置的 `licenses/LICENCE`
  与「未修改」状态满足。
- **`msvcp140.dll`**：★ **已按 C-0（2026-10-24）从仓中移除 ⇒ 本仓不再分发它** ⇒
  3.2 的四条条件**全部不再适用**（其中「分发方持有效 VS 许可」这条**不再需要任何人拍板**）。
  程序改走目标机 `System32\msvcp140.dll` 自带那份；目标机缺 VC++ 运行库时由 `install.ps1`
  **指引**去微软官网装官方 vcredist（我们只指路、自己不发）。
- 结论：**本仓 `vendor/` 目录的全部第三方组件，在 GPL-3.0 与 Apache-2.0 两种口径下均兼容**，
  且**不再有带条件的专有项**（`msvcp140.dll` 已按 C-0 移除）。
- ⚠ 需提请注意的**项目层面**问题（不属本文件范围）：若项目最终采用 Apache-2.0，则项目整体
  **不能再以 GPL-3.0 分发**（Apache-2.0 → GPLv3 为单向不兼容），本文件 3.1 / 3.2 的 GPL-3.0 结论
  仅在项目许可仍为 GPL-3.0 时适用。请以项目最终许可为准。

---

## 4. 打包与校验说明

1. **「未修改」的判定依据**：按各上游 wheel 的 `RECORD` 逐条重算 SHA-256 与字节数，
   `vendor/` 下 **62/62** 条记录全部一致 —— 即仓内既有文件与上游 wheel **逐字节相同**，
   未做任何本地改动。
   ★ **2026-10-24 C-0 的增补**：`vendor\winrt\msvcp140.dll` 已从仓中移除 ⇒ 该条**不再在仓内**，
   也**不再计入**「随本仓分发」的集合；**其余条目未改动**。
   ⚠ 诚实边界：本次移除**没有**在**本 worktree** 里重算全量 RECORD 哈希
   （`vendor` 的 8 个 `.pyd` 是**未跟踪**文件，不在 git 检出里 ⇒ 全量重算在这里做不了）；
   本单只核了**仓内可核的那部分**：54 条逐字节一致、8 条 `.pyd` 缺失（未跟踪）、8 条为
   RECORD 自引用条目（其 hash 字段为空，非不一致）。**「62/62」是移除前的历史核验记录。**
2. **新增的许可文件**（7 个 `*/licenses/LICENCE` + `vendor/winrt/msvcp140-LICENCE` +
   本 `NOTICE.md`）**未登记进对应包的 `RECORD`** —— `RECORD` 属既有文件，按本次
   「只增不改」的范围约束未作修改。此为打包元数据未同步，**不影响**许可随附的效力；
   若后续重新打包这些 wheel，应把上述许可文件一并写入 `RECORD`。
3. **新增文本文件**编码为 UTF-8 无 BOM，换行 LF（与上游原文一致）。
4. **上游许可缺失的原因（已核实）**：pywinrt 3.2.1 的 sdist 目录清单中**不含** `LICENSE`
   文件，其 wheel 亦未随附许可文件（这正是本次补齐的直接原因）；本次按上游 Git 标签
   `v3.2.1` 的仓库 `LICENSE` 原文补入各包 `licenses/LICENCE`。

### 分发时需一并带走的许可文件清单

```
vendor/NOTICE.md
vendor/windows_capture-2.0.1.dist-info/licenses/LICENCE
vendor/winrt_runtime-3.2.1.dist-info/licenses/LICENCE
vendor/winrt_windows_foundation-3.2.1.dist-info/licenses/LICENCE
vendor/winrt_windows_foundation_collections-3.2.1.dist-info/licenses/LICENCE
vendor/winrt_windows_globalization-3.2.1.dist-info/licenses/LICENCE
vendor/winrt_windows_graphics_imaging-3.2.1.dist-info/licenses/LICENCE
vendor/winrt_windows_media_ocr-3.2.1.dist-info/licenses/LICENCE
vendor/winrt_windows_storage_streams-3.2.1.dist-info/licenses/LICENCE
vendor/winrt/msvcp140-LICENCE
```
