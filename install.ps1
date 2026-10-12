<#
.SYNOPSIS
    AgentCtl（电脑控制平台）安装脚本 —— 一行命令装完，不写注册表。

.DESCRIPTION
    设计目标是**像 npx 一样「下载就能用」**：
      · 不做安装包（没有安装向导、没有 Uninstall 注册表项、没有要签名的 exe）
      · 更新 = 重跑同一条命令
      · 卸载 = 一个 uninstall.ps1，删文件即可

    ★ 为什么这样能省掉代码签名：
      安装包里的 AgentCtl.exe 是**新文件**，SmartScreen 会拦、要签名。
      而本方案落地的全是 .py 源码 + 一个 pythonw.exe（解释器自带的），
      没有任何新可执行文件被写进系统目录 —— 所以整套东西**不需要签名**。

    数据与程序的分离（很重要，别改）：
      程序  %LOCALAPPDATA%\AgentCtl\app     ← 本脚本管的，随更新替换
      数据  %LOCALAPPDATA%\AgentCtl          ← **不在**替换范围内
             （授权状态、通行证、急停状态、事件流；更新不能把用户的授权冲掉）

.PARAMETER Zip
    本地 zip 路径或 URL。不给就走 Release 包。
    给了本地路径 = 完全离线安装（内网 / GitHub 连不上时用）。

.PARAMETER Dir
    安装根目录。默认 %LOCALAPPDATA%\AgentCtl。

.PARAMETER NoDeps
    跳过 pip install（依赖已装好、或要离线装时用）。

.PARAMETER NoShortcut
    不写桌面快捷方式。

.PARAMETER Force
    即使已安装也强制重装（默认提示后跳过）。

.EXAMPLE
    一行安装：
      irm https://gitee.com/GALAXYLIYI/agent-ctl/raw/main/install.ps1 | iex

    两步安装（更稳，能先看一眼脚本再跑）：
      irm https://gitee.com/GALAXYLIYI/agent-ctl/raw/main/install.ps1 -OutFile install.ps1
      .\install.ps1
#>

[CmdletBinding()]
param(
    [string]$Zip,
    [string]$Dir,
    [switch]$NoDeps,
    [switch]$NoShortcut,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

# ★ 中文 Windows 控制台默认 GBK，打不出部分符号会抛 UnicodeEncodeError，
#   把整个安装打断（AgentCtl.exe --help 冻结构建时就栽过这个）。
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
$OutputEncoding = [Text.Encoding]::UTF8

# ★ 仓库地址【单点定义】：换宿主 / 换 owner 只改这几行，脚本其它地方全部引用它们。
#   现状 = Gitee（仓库真实所在，owner 单 L）。
#   ★ 长期宿主已定为 **Gitee**（2026-10-12 用户拍板）；如需换宿主，改这几行即可：
#     $Owner='GALAXYLIYI'; $RepoHost='https://github.com'
#     $DefaultZip="$RepoHost/$Owner/agent-ctl/releases/latest/download/agentctl.zip"
#     $RepoZip="https://codeload.github.com/$Owner/agent-ctl/zip/refs/heads/main"
$Owner = 'GALAXYLIYI'
$RepoHost = 'https://gitee.com'
$DefaultZip = "$RepoHost/$Owner/agent-ctl/releases/latest/download/agentctl.zip"
$RepoZip = "$RepoHost/$Owner/agent-ctl/repository/archive/main.zip"
$MinPy = [Version]'3.10'
$AppName = '电脑控制平台'

function Say { param([string]$m) Write-Host "  $m" }
function Step { param([string]$m) Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Bad { param([string]$m) Write-Host "[X] $m" -ForegroundColor Red }
function Good { param([string]$m) Write-Host "[OK] $m" -ForegroundColor Green }

# ─────────────────────────────────────────────────────────────
Step '0/6  环境自检'
# ─────────────────────────────────────────────────────────────
if ($PSVersionTable.PSVersion.Major -lt 5) {
    Bad "PowerShell 版本太低（$($PSVersionTable.PSVersion)）。请用 Windows PowerShell 5.1 或 PowerShell 7+。"
    exit 1
}
Good "PowerShell $($PSVersionTable.PSVersion)"

if (-not $Dir) { $Dir = Join-Path $env:LOCALAPPDATA 'AgentCtl' }
$AppDir = Join-Path $Dir 'app'
Say "安装根目录：$Dir"

# ─────────────────────────────────────────────────────────────
Step '1/6  找 Python（没有就用 winget 装）'
# ─────────────────────────────────────────────────────────────
function Find-Python {
    $try = @()
    if (Get-Command py.exe -ErrorAction SilentlyContinue) { $try += , @('py.exe', @('-3.12', '-3', '-3.11')) }
    foreach ($n in @('python.exe', 'python3.exe')) {
        if (Get-Command $n -ErrorAction SilentlyContinue) { $try += , @($n, @()) }
    }
    foreach ($c in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe", "$env:ProgramFiles\Python312\python.exe", "$env:ProgramFiles\Python313\python.exe")) {
        if (Test-Path $c) { $try += , @($c, @()) }
    }
    foreach ($t in $try) {
        $exe = $t[0]
        foreach ($p in $t[1]) {
            # ★ 打两行：版本 + **真实解释器路径**。
            #   第一版这里只打了版本，于是选了 C:\Windows\py.exe，
            #   接着 Split-Path 拿它同目录的 pythonw.exe —— 那是 C:\Windows\pythonw.exe，
            #   **不存在**。py 启动器只是个转发器，它所在目录没有解释器。
            # ★ 打三行：major / minor / micro + 解释器路径。
            #   第一版想用 major*100+minor*10+micro 一个数编码版本，
            #   结果 3.12.7 算成 427 → 4.2.7。**位数不够就会串版**，别再用这种编码。
            $o = & $exe $p -c "import sys;print(sys.version_info[0]);print(sys.version_info[1]);print(sys.version_info[2]);print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $o -and $o.Count -ge 4) {
                $v = [Version]::new([int]$o[0], [int]$o[1], [int]$o[2])
                $real = $o[3].Trim()
                if ($v -ge $MinPy -and (Test-Path $real)) {
                    # ★ 拿到真实解释器后就**一律用它调**，前缀固定为空数组。
                    #   `py.exe -3.12` 只有启动器认这个参数，`python.exe -3.12` 会直接报错，
                    #   所以不能把探测用的前缀原样带回来。
                    return @{ Exe = $real; Prefix = @(); Ver = $v }
                }
            }
        }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Say "本机没有 Python $MinPy 或更高版本。"
    if (Get-Command winget.exe -ErrorAction SilentlyContinue) {
        Say '尝试用 winget 静默安装 Python 3.12（可能弹 UAC，请点是）...'
        try {
            $null = & winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements --disable-interactivity 2>&1
        } catch { Say "winget 调用失败：$($_.Exception.Message)" }
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
        $py = Find-Python
    }
}

if (-not $py) {
    Bad "没找到可用的 Python $MinPy+。二选一："
    Bad "  [1] 装完 Python 后重跑本脚本（https://www.python.org/downloads/）"
    Bad "  [2] 或一行装：winget install Python.Python.3.12"
    exit 2
}
Good "Python $($py.Ver)  ->  $($py.Exe)"

# ★ 前缀数组不能无条件包一层：py.exe -3.12 的前缀是 -3.12，
#   而直接 python.exe 的前缀是**空数组**；@(@()) 会展开成一个空字符串参数，
#   后面的命令直接报 "Cannot bind argument to parameter 'Path' because it is an empty string"。
$prefix = @()
if ($py.Prefix) { $prefix = @($py.Prefix) }

# pythonw.exe：无控制台窗口的宿主，用来挂面板
$pyw = Join-Path (Split-Path $py.Exe -Parent) 'pythonw.exe'
if (-not (Test-Path $pyw)) { Bad "找不到 pythonw.exe（面板要无窗口运行）：$pyw"; exit 3 }

# ─────────────────────────────────────────────────────────────
Step '2/6  取安装包'
# ─────────────────────────────────────────────────────────────
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('agentctl_inst_' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$null = New-Item -ItemType Directory -Force -Path $tmp
$tmpZip = Join-Path $tmp 'agentctl.zip'
$extract = Join-Path $tmp 'x'

function Get-Package {
    if ($Zip) {
        if ($Zip -match '^https?://') {
            Say "下载：$Zip"
            Invoke-WebRequest -Uri $Zip -OutFile $tmpZip -UseBasicParsing
        } else {
            $full = (Resolve-Path $Zip).Path
            Say "用本地包：$full"
            Copy-Item $full $tmpZip -Force
        }
        return
    }
    Say "尝试 Release 包：$DefaultZip"
    try {
        Invoke-WebRequest -Uri $DefaultZip -OutFile $tmpZip -UseBasicParsing -ErrorAction Stop
        return
    } catch {
        Say "Release 还没有（$(($_.Exception.Message -split "`n")[0])），改用源码包。"
    }
    Say "下载源码包：$RepoZip"
    Invoke-WebRequest -Uri $RepoZip -OutFile $tmpZip -UseBasicParsing
}

try { Get-Package } catch {
    Bad "取包失败：$($_.Exception.Message)"
    Bad '如果这台机器连不上 GitHub，把 zip 下下来再跑：.\install.ps1 -Zip .\agentctl.zip'
    exit 4
}

if (-not (Test-Path $tmpZip)) { Bad '安装包没落地'; exit 4 }
Good ("拿到安装包（{0} MB）" -f [math]::Round((Get-Item $tmpZip).Length / 1MB, 1))

Expand-Archive -Path $tmpZip -DestinationPath $extract -Force

# zip 里顶层可能套一层仓库名目录（源码包就会），找真正含 src\agentctl 的那层
$srcRoot = $extract
for ($i = 0; $i -lt 3; $i++) {
    if (Test-Path (Join-Path $srcRoot 'src\agentctl\core.py')) { break }
    $sub = Get-ChildItem $srcRoot -Directory | Select-Object -First 1
    if (-not $sub) { break }
    $srcRoot = $sub.FullName
}
if (-not (Test-Path (Join-Path $srcRoot 'src\agentctl\core.py'))) {
    Bad '安装包结构不对：找不到 src\agentctl\core.py'
    exit 5
}
Good "包内根目录：$srcRoot"

# ─────────────────────────────────────────────────────────────
Step '3/6  落地程序（数据目录不动）'
# ─────────────────────────────────────────────────────────────
if ((Test-Path $AppDir) -and -not $Force) {
    Good "已经装过了：$AppDir"
    Say '要更新就重跑同一条命令；要重装加 -Force。'
} else {
    $staging = Join-Path $Dir 'app.new'
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backup = Join-Path $Dir ("app.old-$stamp")
    if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
    $null = New-Item -ItemType Directory -Force -Path $Dir
    # ★ 目标必须**先存在且是目录**。第一版直接把目录拷到还不存在的 $staging，
    #   Copy-Item 会把 srcagentctl 之类的容器当成 leaf item，
    #   报 "Container cannot be copied onto existing leaf item"。
    $null = New-Item -ItemType Directory -Force -Path $staging
    Say "展开到暂存目录：$staging"
    Get-ChildItem -Path $srcRoot -Force | Copy-Item -Destination $staging -Recurse -Force

    if (Test-Path $AppDir) { Say "旧版移到：$backup"; Move-Item $AppDir $backup }
    Move-Item $staging $AppDir
    Good "程序已就位：$AppDir"
    if (Test-Path $backup) { Say "旧版留在 $backup，确认没问题后可自行删除（uninstall.ps1 -CleanOld 会清）" }
}

# 源码态靠这个指针文件找 legacy 脚本目录（gate.py 的 _resolve_home 读它）
$legacyHome = Join-Path $AppDir 'legacy_tools'
$ptr = Join-Path $AppDir 'build\local_legacy_home.txt'
$null = New-Item -ItemType Directory -Force -Path (Split-Path $ptr -Parent)
Set-Content -Path $ptr -Value $legacyHome -Encoding UTF8
Say "legacy 工具目录指针：$ptr"

if (-not (Test-Path $legacyHome)) {
    Bad '包内没有 legacy_tools\ —— 这是控制链路的底座（窗口枚举/键鼠注入/OCR 都在里面）。'
    Bad '请确认下载的是完整的 agentctl.zip，而不是源码 zip。'
    exit 6
}

# ─────────────────────────────────────────────────────────────
Step '4/6  装依赖'
# ─────────────────────────────────────────────────────────────
$req = Join-Path $AppDir 'requirements.txt'
if ($NoDeps) {
    Say '按要求跳过 pip。'
} elseif (Test-Path $req) {
    Say 'pip install -r requirements.txt（首次约 1-3 分钟）...'
    & $py.Exe @prefix -m pip install --disable-pip-version-check -q -r $req
    if ($LASTEXITCODE -ne 0) {
        Bad "pip 装依赖返回 $LASTEXITCODE。程序可能起不来，可加 -NoDeps 重跑后手动装。"
    } else { Good '依赖装好了' }
} else {
    Bad '包内没有 requirements.txt，依赖没法自动装。'
}

# ─────────────────────────────────────────────────────────────
Step '5/6  桌面快捷方式 + 命令行入口'
# ─────────────────────────────────────────────────────────────
$entry = Join-Path $AppDir 'src\agentctl_main.py'
$panelEntry = Join-Path $AppDir 'src\panel_main.py'

if (-not $NoShortcut) {
    $desk = [Environment]::GetFolderPath('Desktop')
    $lnk = Join-Path $desk ($AppName + '.lnk')
    $sh = New-Object -ComObject WScript.Shell
    $s = $sh.CreateShortcut($lnk)
    $s.TargetPath = $pyw
    # ★ 用 panel_main.py 而不是 `agentctl_main.py panel`：
    #   前者是面板专用入口，崩了会把 traceback 写进 %LOCALAPPDATA%\AgentCtl\panel_crash.log。
    #   用后者的话崩了**什么都没有** —— 用户只会看到「双击了没反应」，无处可查。
    $s.Arguments = '-X utf8 "' + $panelEntry + '"'
    $s.WorkingDirectory = $AppDir
    $s.Description = 'AgentCtl 电脑控制平台 —— 面板、授权、急停'
    $s.IconLocation = "$pyw,0"
    $s.Save()
    Good "桌面快捷方式：$lnk"
    Say '（用 pythonw.exe 启动，不会出现终端窗口）'
}

$cmd = Join-Path $Dir 'agentctl.cmd'
# ★ 2026-10-12 D1 冒烟抓到的真 bug：原来写 `-Encoding OEM` —— 按**写时**代码页落盘，
#   而 cmd.exe 按**运行时**代码页解析；安装路径含非 ASCII（中文用户名/中文目录）
#   或运行环境代码页不同（如 65001）时，路径变乱码，入口直接废（冒烟实测复现）。
#   修法：UTF-8 **不带 BOM** + 第二行 `chcp 65001`。
#   ★ 为什么不能带 BOM（2026-10-12 实测）：GBK 控制台下 cmd 把 EF BB BF 解成"锘"缀在
#     首行前面，`@echo off` 失效（回显全开）；不带 BOM 时前两行是纯 ASCII，任何代码页
#     都安全，chcp 生效后含中文的路径按 UTF-8 解析 —— 与宿主控制台代码页彻底无关。
$cmdBody = '@echo off' + "`r`n" + 'chcp 65001 >nul' + "`r`n" + 'REM AgentCtl 命令行入口。参数原样转给 agentctl_main.py' + "`r`n" + '"' + $py.Exe + '" -X utf8 "' + $entry + '" %*' + "`r`n"
[System.IO.File]::WriteAllText($cmd, $cmdBody, (New-Object System.Text.UTF8Encoding($false)))
Good "命令行入口：$cmd"

# ─────────────────────────────────────────────────────────────
Step '6/6  自检'
# ─────────────────────────────────────────────────────────────
Say '跑一次只读自检（selftest 不消耗额度、不截图、不动任何东西）...'
$st = & $py.Exe @prefix $entry selftest 2>&1
$rc = $LASTEXITCODE
$st | ForEach-Object { Say "  $_" }
if ($rc -eq 0) {
    Good '自检通过，装完了。'
} else {
    Bad "自检返回 $rc。上面是原因，照着处理即可；程序仍然装上了。"
}

$oldInno = Join-Path $env:LOCALAPPDATA 'Programs\AgentCtl\AgentCtl.exe'
if (Test-Path $oldInno) {
    Say ''
    Say "[!] 还发现旧安装包版：$oldInno"
    Say '    两份可以并存，但桌面图标可能指到旧的。要干净就卸载旧版（控制面板 -> AgentCtl）。'
}

Write-Host ''
Good '全部完成'
Say "程序：$AppDir"
Say '开始：双击桌面「电脑控制平台」，或运行 agentctl.cmd serve'
Say '更新：重跑同一条安装命令'
Say '卸载：运行 uninstall.ps1'
exit 0
