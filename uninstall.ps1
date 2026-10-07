<#
.SYNOPSIS
    卸载 AgentCtl（电脑控制平台）—— 只删自己写的文件，不碰系统。

.DESCRIPTION
    为什么能这么简单：这个平台**没有安装注册表项、没有服务、没有写系统目录**，
    所以卸载就是「删掉 %LOCALAPPDATA%\AgentCtl + 桌面快捷方式」。

    ★ 默认**保留**数据（授权状态、通行证、急停状态、事件流、截图）。
      这是有意的：卸载不等于「抹掉用户的授权痕迹」，重新装回来能接着用。
      确实要连数据一起清，加 -Purge。

.PARAMETER Purge
    连数据目录一起删（授权、通行证、日志、截图全部消失，不可恢复）。

.PARAMETER CleanOld
    删掉历次更新留下的 app.old-* 备份目录。

.PARAMETER Dir
    安装根目录。默认 %LOCALAPPDATA%\AgentCtl。

.PARAMETER KeepShortcut
    保留桌面快捷方式（默认会删）。

.EXAMPLE
    .\uninstall.ps1
    .\uninstall.ps1 -Purge
#>

[CmdletBinding()]
param(
    [switch]$Purge,
    [switch]$CleanOld,
    [switch]$KeepShortcut,
    [string]$Dir
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$AppName = '电脑控制平台'
if (-not $Dir) { $Dir = Join-Path $env:LOCALAPPDATA 'AgentCtl' }

function Say { param([string]$m) Write-Host "  $m" }
function Bad { param([string]$m) Write-Host "[X] $m" -ForegroundColor Red }
function Good { param([string]$m) Write-Host "[OK] $m" -ForegroundColor Green }

Write-Host "`n== 卸载 AgentCtl" -ForegroundColor Cyan

# ★ 先把当前目录踢出去。
#   人在安装目录里敲卸载脚本是常事，而 Windows **不允许删掉进程的当前目录**，
#   于是 Remove-Item 抛 "because it is in use"，脚本直接崩在半路 ——
#   看起来就像「卸载到一半坏了」，其实只是自己的脚把自己绊住了。
try { Set-Location -Path $env:TEMP } catch { }

# ── 先停进程，否则 .py 文件被占用删不掉 ──
#   ★ 判据必须精确到「进程名 + 入口脚本」，不能只搜命令行里的 agentctl 字样：
#     第一版用 `-like '*agentctl*'`，结果**把这个卸载脚本自己**（命令行带脚本路径）也杀了，
#     卸载跑到一半凭空断掉，看上去像「删到一半崩了」。
$me = @($PID, (Get-CimInstance Win32_Process -Filter "ProcessId=$PID").ParentProcessId)
$running = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.ProcessId -notin $me -and
    ($_.Name -like 'python*.exe' -or $_.Name -like 'AgentCtl*.exe') -and
    $_.CommandLine -and
    ($_.CommandLine -like '*agentctl_main.py*' -or $_.CommandLine -like '*AgentCtl.exe*')
}
if ($running) {
    Say "发现 $($running.Count) 个还在跑的 AgentCtl 进程，先停掉："
    foreach ($p in $running) {
        Say "  PID $($p.ProcessId)  $($p.Name)"
        try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch { Bad "停不掉 PID $($p.ProcessId)：$($_.Exception.Message)" }
    }
    Start-Sleep -Milliseconds 700
}

if (-not (Test-Path $Dir)) {
    Good "$Dir 不存在，本来就没装（或已经卸干净了）。"
} else {
$app = Join-Path $Dir 'app'
# 默认「程序已不在」，只有真删失败才翻成 False ——
# 否则「本来就没装」会被报成「删不掉」，那是假的。
$appRemoved = $true
if (Test-Path $app) {
    try {
        Remove-Item $app -Recurse -Force -ErrorAction Stop
        Good "删掉程序：$app"
        $appRemoved = $true
    } catch {
        Bad "程序目录删不掉：$app"
        Bad "  原因：$($_.Exception.Message)"
        Bad '  多半是有进程还开着这个目录（面板、serve、编辑器）。'
        Bad '  关掉它们再跑一次，或手动删掉残留的 .py 文件。'
    }
}

# 命令行入口（install.ps1 生成的）
$cmd = Join-Path $Dir 'agentctl.cmd'
if (Test-Path $cmd) {
    Remove-Item $cmd -Force -ErrorAction SilentlyContinue
    Good "删掉命令行入口：$cmd"
}

    if ($CleanOld) {
        Get-ChildItem $Dir -Directory -Filter 'app.old-*' -ErrorAction SilentlyContinue |
            ForEach-Object { Remove-Item $_.FullName -Recurse -Force; Say "删掉旧版备份：$($_.Name)" }
        Get-ChildItem $Dir -Directory -Filter 'app.new' -ErrorAction SilentlyContinue |
            ForEach-Object { Remove-Item $_.FullName -Recurse -Force; Say "删掉暂存目录：$($_.Name)" }
    }

    if ($Purge) {
        if ($appRemoved) {
            try {
                Remove-Item $Dir -Recurse -Force -ErrorAction Stop
                Good "连数据一起清了：$Dir"
                Say '（授权、通行证、日志、截图都没了，下次运行要重新授权）'
            } catch {
                Bad "数据目录清不掉：$Dir（$($_.Exception.Message)）"
                Say '程序已经删了；数据目录里剩下的可以手动删。'
            }
        } else {
            Bad '程序都没删掉，**没有动数据**（免得留下一个跑不起来的半拉子）。'
            Say "请先关掉占用进程，然后重跑；或直接手动删：$Dir"
        }
    } else {
        if (-not $appRemoved) {
            Bad '程序目录仍在，数据原封不动。'
        } else {
            $left = @(Get-ChildItem $Dir -ErrorAction SilentlyContinue)
            if (-not $left.Count) {
                Remove-Item $Dir -Force -ErrorAction SilentlyContinue
                Good "清空了，顺手把空目录也删了：$Dir"
            } else {
                Good "程序删了，数据保留：$Dir"
                Say ('（' + (($left | Select-Object -ExpandProperty Name) -join ', ') + '）')
            }
        }
    }
}

if (-not $KeepShortcut) {
    $lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) ($AppName + '.lnk')
    if (Test-Path $lnk) { Remove-Item $lnk -Force; Good "删掉桌面快捷方式：$lnk" }
}

Write-Host ''
Good '卸载完成'
Say 'Python 和 pip 装的依赖**没动**（多个项目共用，删了会连累别的）。'
Say '真要清干净：pip uninstall pywin32 uiautomation comtypes pillow mss numpy psutil websocket-client zstandard'
exit 0
