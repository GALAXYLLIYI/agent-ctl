# 电脑控制平台 · AgentCtl

给 AI agent 用的**电脑控制闸门**。agent 不能直接摸键鼠，必须先过闸门；
闸门由**用户在弹窗里输密钥**放行，一次授权有明确的时间、范围和动作额度，
出事有三个独立的急停通道。

版本 1.2.0 · 最后更新 2026-10-12 · 许可证 GPL-3.0

---

## 0. 安装（一行命令）

PowerShell 里粘这一行，回车：

```powershell
irm https://gitee.com/GALAXYLIYI/agent-ctl/raw/main/install.ps1 | iex
```

**不想盲跑**（推荐第一次这么做，能先看清脚本再执行）：

```powershell
irm https://gitee.com/GALAXYLIYI/agent-ctl/raw/main/install.ps1 -OutFile install.ps1
notepad install.ps1      # 看一眼
.\install.ps1
```

> ⚠️ **本仓库当前为私有**：上面的命令需要 Gitee 凭据才能拉取。
> 要让别人 `irm` 就能直接安装，需先把仓库设为公开。

脚本做这六件事，全程可重复执行：

| 步骤 | 做什么 | 注意 |
|---|---|---|
| 0 | 查 PowerShell 版本 | 5.1 或更高 |
| 1 | 找 Python 3.10+ | 没有就用 winget 静默装 3.12 |
| 2 | 下载 zip | Release 优先，没有就退回源码包 |
| 3 | 展开到 `%LOCALAPPDATA%\AgentCtl\app` | 先展到暂存目录再换，旧版留备份 |
| 4 | `pip install -r requirements.txt` | 版本只卡下限，不锁死 |
| 5 | 写桌面快捷方式（`pythonw.exe`） | **不弹控制台窗口** |
| 6 | 跑一次只读自检 | 不消耗额度、不截图 |

装完双击桌面「**电脑控制平台**」，右下角会浮出面板。

### 为什么不用安装包

| | 安装包（Inno/PyInstaller） | 本方案 |
|---|---|---|
| 要代码签名吗 | **要**，否则 SmartScreen 拦 | **不要**（没写任何新 exe 到系统目录） |
| 升级 | 重新下一整个包 | 重跑同一条命令 |
| 卸载 | 控制面板卸载项 | 一个 `uninstall.ps1` |
| 装到哪 | 写注册表、写卸载项 | 只写 `%LOCALAPPDATA%\AgentCtl` |
| 体积 | ~96 MB | zip 0.5 MB，装完 ~1.5 MB |

### 数据不会在更新时丢

```
%LOCALAPPDATA%\AgentCtl\          ← 数据（授权状态、通行证、急停、日志、截图）
%LOCALAPPDATA%\AgentCtl\app\      ← 程序（随更新整体替换）
```

重跑安装命令只换 `app`，**授权和急停状态原地不动**。

### 更新 / 卸载

```powershell
.\install.ps1              # 更新 = 重跑同一条命令
.\uninstall.ps1            # 卸载程序，数据保留
.\uninstall.ps1 -Purge     # 连授权、日志、截图一起清
```

### 连不上 GitHub 时

把 `agentctl.zip` 下下来本地装：

```powershell
.\install.ps1 -Zip .\agentctl.zip
```

---

## 1. 面板与入口

快捷方式指向 `src\panel_main.py`（用 `pythonw.exe` 启动），
**不是** exe —— 这样崩了会把 traceback 写进
`%LOCALAPPDATA%\AgentCtl\panel_crash.log`，不会「双击了没反应」也无从查起。

命令行入口是安装目录下的 `agentctl.cmd`，参数原样转给 `agentctl_main.py`。

### 想要 exe 版（可选）

仓库仍保留 PyInstaller 配置，**需要才构建**，不是分发主线：

| exe | PE Subsystem | 用途 | 双击效果 |
|---|---|---|---|
| `AgentCtl.exe` | 3 Console | mcp 接入、命令行、服务 | 弹黑色终端（正常） |
| `AgentCtlPanel.exe` | 2 GUI | 只有悬浮面板 | **不弹窗口** |

快捷方式、开始菜单、开机启动**全部指向 `AgentCtlPanel.exe`**。

⛔ `AgentCtl.exe` 的 Subsystem **不许改成 2（GUI）**：`mcp` 入口靠
stdout/stdin 通信，一没 stdout 就直接死，命令行输出也全靠控制台。

## 2. 环境依赖（从源码跑时）

- Windows 10 / 11（用到 WinRT OCR，Win10 1903+ 才有）
- Python 3.12（开发版本）
- PyInstaller 6.x
- Inno Setup 6.x（只在编译安装包时需要）

```powershell
pip install pywin32 uiautomation comtypes pillow requests
pip install pyinstaller
winget install --id JRSoftware.InnoSetup -e
```

`vendor/` 目录里已经带了 `winrt` 和 `windows-capture` 的预编译包，
PyInstaller 的 spec 会从 `pathex` 里找到它们，**这两个不用 pip 装**。

## 3. 构建 exe（可选）

```powershell
# ① 命令行版（console）
python -m PyInstaller --noconfirm --clean build\AgentCtl.spec

# ② 面板版（无控制台窗口）
python -m PyInstaller --noconfirm --clean build\AgentCtlPanel.spec

# ③ 编译安装包（需要 Inno Setup）
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" build\installer.iss
```

产物落在 `dist/`。第 ③ 步依赖前两步，顺序不能颠倒。

装完先验一遍：

```powershell
.\dist\AgentCtl\AgentCtl.exe selftest
```

## 4. 面板怎么用

右下角浮出，深色 560×330，可拖动，位置会记住。

| 操作 | 效果 |
|---|---|
| 拖标题栏 | 移动窗口 |
| 点标题栏 **✕** | 关闭面板 |
| **右键**面板 | 立即刷新状态 / 在资源管理器中打开 / 退出平台面板 |
| 🔑 授权 | 弹窗申请通行证（**用户在弹窗里输密钥**，助手看不到明文） |
| ⏹ 终止 | **拉下急停**，一切自动控制停止 |
| ▶ 解除急停 | 二次确认后解除 |

**关面板 ≠ 解除急停。** 急停是安全状态，不会被随手关窗口带走。
agent 正在控制时点 ✕，会先问你「先急停再关」/「只关面板」/「取消」。

## 5. 命令入口

```powershell
%LOCALAPPDATA%\AgentCtl\agentctl.cmd serve       # 常驻 HTTP 服务
%LOCALAPPDATA%\AgentCtl\agentctl.cmd mcp          # MCP stdio（⛔ 绝不能无 stdout）
%LOCALAPPDATA%\AgentCtl\agentctl.cmd panel        # 悬浮面板
%LOCALAPPDATA%\AgentCtl\agentctl.cmd selftest     # 只读挂件自检：查有没有绕过闸门
%LOCALAPPDATA%\AgentCtl\agentctl.cmd legacy <脚本> # 跑 legacy 老脚本
```

⚠ `legacy` 只能跑控制链路的脚本。`docx` / `fitz` / `matplotlib` 这类论文脚本
**故意没带**，用 legacy 跑会 ImportError——这是有意为之，不是 bug。

## 6. 已知问题（都在 Windows 侧，不是代码问题）

**① exe 没数字签名 → 双击弹「未知发布者」**

已用自签证书（`CN=AgentCtl Local`）签了，但证书不在受信任根里。
导入后本机就不再弹：

```powershell
Export-Certificate -Cert Cert:\CurrentUser\My\<指纹> -FilePath $env:USERPROFILE\Desktop\AgentCtl_Local.cer
Import-Certificate -FilePath $env:USERPROFILE\Desktop\AgentCtl_Local.cer -CertStoreLocation Cert:\CurrentUser\Root
Import-Certificate -FilePath $env:USERPROFILE\Desktop\AgentCtl_Local.cer -CertStoreLocation Cert:\CurrentUser\TrustedPublisher
```

这**只对本机有效**，发给别人的包对方照样弹。要彻底解决得买 OV 证书。

**② 安装包报「无法创建目录 is-XXXX.tmp，错误 5：拒绝访问」**

已排除：权限（ACL 给当前用户完全控制）、磁盘（130GB+ 空闲）、
Temp 目录（换 F 盘无效）、杀软（Defender 无记录）、受控文件夹访问（关闭）、
本工程配置（最小包同样失败）。**手动双击能装成功，从自动化环境启动则失败**
——两者差异尚未定位，重装时若复现请留意你是怎么启动的。

**③ 绿色版 zip 有 66MB**

两个 exe 各带一整套依赖。面板版实际只用到 `widget.py`，
`eye.py`/`core.py` 那套 WinRT OCR 和 UIA 对它是多余的，可瘦身但尚未做。

## 7. 目录结构

```
src/
  agentctl_main.py        命令行/mcp/服务入口
  panel_main.py           面板入口（无控制台）
  agentctl/
    core.py               工具实现（点击/打字/截屏/控件树）
    gate.py               闸门：通行证、动作计数、急停
    widget.py             悬浮面板
    eye.py                感知层：区域/裁剪/OCR/颜色判选中
    recorder.py           录制与留存
    store.py / clock.py   状态存储与时间线
    _bundled_deps.py      只为打包存在，见文件头注释
build/
  AgentCtl.spec           命令行版打包配置
  AgentCtlPanel.spec      面板版打包配置（console=False）
  installer.iss           Inno Setup 安装脚本
contract/tools.json       工具契约
vendor/                   winrt / windows-capture 预编译依赖
tools/                    构建与安装辅助脚本
tests/                    验收脚本（S2~S5）
docs/                     详细设计与验收证据
verify/                   打包后自检
```

## 8. 设计上的几条硬规矩

1. **密钥明文不落盘、不回显**，只存加盐 SHA-256；主 agent 物理接触不到。
2. **只读挂件不许消耗额度**，不许 import 任何能操作屏幕的模块——
   `selftest` 每次都会审计源码，违反就报。
3. **观测与动作绑定**：点击必须带 `from_capture`，不许凭记忆点坐标。
4. **不拿「看起来对」当判据**：不许用「页面没报错」当「操作成功」。

改代码前请先读 `src/agentctl/gate.py` 和 `src/agentctl/eye.py` 的模块说明——
里面的注释都是踩过坑写出来的，不是装饰。

## 9. 交付包不含什么（安全）

以下目录**刻意不打进交付包**：

- `baseline/` —— 2026-10-02 的历史项目快照，内含
  `anythingllm_key.txt`（**明文 API Key**）与 `auth_key.json`，
  与本工程无关且带密钥，整目录排除，且被 `.gitignore` 挡在仓库之外。
  控制链路真正要用的那 46 个脚本，由 `tools/make_legacy_dir.py` 抽成干净的
  `legacy_tools/` 进仓；`case_data.json`（真实手机号 + 身份证）被隐私闸门拦下，
  列在永久排除名单里。
- `work/` —— 运行日志与会话 token。
- `dist/` —— 构建产物，体积大且可再生。
- `__pycache__/`、`*.bak*` —— 编译缓存与本地备份。
- `src/work/state/eye_regions.json` —— 运行时状态，接近 1MB。

### 三道闸门，别只靠眼睛

打包和核验都是脚本，不是人肉检查：

| 闸门 | 位置 | 作用 |
|---|---|---|
| 抽取闸门 | `tools/make_legacy_dir.py` | 扫手机号 / 身份证 / 密钥赋值，命中即不复制 |
| 打包闸门 | `tools/pack_dist.py` | 白名单 + 敏感名 + 隐私复扫，缺控制链路即中止 |
| 核验闸门 | `tools/verify_dist.py` | **独立重新实现**一遍判据，只解包检查，不信打包脚本 |

核验脚本刻意不复用打包脚本的函数：同一份代码既是运动员又是裁判，
出错就一起错。

```powershell
python -X utf8 tools/make_legacy_dir.py   # 抽干净的 legacy_tools/
python -X utf8 tools/pack_dist.py         # 打 dist/agentctl.zip
python -X utf8 tools/verify_dist.py       # 独立核验这个 zip
```
