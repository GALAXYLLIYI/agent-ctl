#!/usr/bin/env pwsh
# dev_env.ps1 —— 一键准备 AgentCtl 的开发环境，并跑一遍**无桌面依赖**的闸门。
#
# ★ 为什么要单独一个脚本：
#   `install.ps1` 是给**使用者**装到本机用的（装完就能调工具）；
#   这个是给**贡献者**用的（装依赖 + 跑闸门 + 告诉你下一步）。
#   两者混在一起会导致「我只是想跑一下判据，结果把我本机环境改了」。
#
# 用法：
#   pwsh -File tools\dev_env.ps1                 # 建 .venv、装依赖、跑闸门
#   pwsh -File tools\dev_env.ps1 -NoVenv         # 用当前解释器，不建 venv
#   pwsh -File tools\dev_env.ps1 -GateOnly        # 只跑闸门，不装任何东西
#
# 退出码：0 = 闸门全绿；非 0 = 至少一条红。

[CmdletBinding()]
param(
    [switch]$NoVenv,
    [switch]$GateOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    [!] $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "    [x] $msg" -ForegroundColor Red }

# ── 0. 平台 ───────────────────────────────────────────────────────────────
if (-not $IsWindows -and $env:OS -ne 'Windows_NT') {
    Write-Err '本项目只在 Windows 上成立（UIA / 抓屏 / WinRT OCR / 键鼠注入）。'
    exit 2
}

# ── 1. Python ─────────────────────────────────────────────────────────────
$py = $null
if (-not $NoVenv -and -not $GateOnly) {
    $venv = Join-Path $RepoRoot '.venv'
    if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))) {
        Write-Step '建 .venv'
        python -m venv $venv
    }
    $py = Join-Path $venv 'Scripts\python.exe'
    Write-Ok "解释器：$py"
} else {
    $py = 'python'
}

& $py -c "import sys;print('    Python', '.'.join(map(str,sys.version_info[:3])))"
$ver = & $py -c "import sys;print(sys.version_info[0]*100+sys.version_info[1])"
if ([int]$ver -lt 310) {
    Write-Err '需要 Python >= 3.10（开发基线 3.12）。'
    exit 2
}

# ── 2. 依赖 ───────────────────────────────────────────────────────────────
if (-not $GateOnly) {
    Write-Step '装依赖（requirements.txt + pyflakes）'
    & $py -m pip install --upgrade pip | Out-Null
    & $py -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Write-Err 'requirements.txt 安装失败（vendor 预编译轮子可能需要本地构建）。'; exit 1 }
    & $py -m pip install 'pyflakes>=3'
    Write-Ok '依赖就绪'
}

# ── 3. 闸门 ───────────────────────────────────────────────────────────────
# ★ 只跑**不需要交互桌面**的那些。
#   需要桌面的判据（要真弹窗/真抓屏/真抢热键）必须在你自己的桌面上手工跑，
#   清单与理由见 docs\开发环境.md。
$gates = @(
    @{ name = '语法';     cmd = @('-X', 'utf8', '-m', 'compileall', '-q', 'src/agentctl') },
    @{ name = '静态';     cmd = @('-X', 'utf8', 'tools/lint_gate.py') },
    @{ name = '契约';     cmd = @('-X', 'utf8', 'tools/check_contract.py') },
    @{ name = '提交前扫描'; cmd = @('-X', 'utf8', 'tools/presubmit_scan.py') }
)

Write-Step '跑无桌面依赖的闸门'
$failed = @()
foreach ($g in $gates) {
    Write-Host "  -- $($g.name)"
    & $py @($g.cmd)
    if ($LASTEXITCODE -ne 0) { $failed += $g.name }
}

# presubmit_scan / scan_repo 扫的是 **git 索引** ⇒ 没暂存就等于没扫。
# 这里如实提示，不假装它扫过了。
$staged = (git diff --cached --name-only 2>$null | Measure-Object).Count
if ($staged -eq 0) {
    Write-Warn '提交前扫描扫的是 git 索引；当前没有暂存内容，它实际上没扫到东西。'
    Write-Warn '真要提交前扫一遍：git add -A 之后再跑 tools\presubmit_scan.py。'
}

Write-Host ''
if ($failed.Count -eq 0) {
    Write-Host '闸门全绿。' -ForegroundColor Green
} else {
    Write-Host ("红了：{0}" -f ($failed -join '、')) -ForegroundColor Red
}
Write-Host ''
Write-Host '下一步：' -ForegroundColor Cyan
Write-Host '  · 有桌面的 Windows 上跑全部判据：python -X utf8 tests\run_all.py'
Write-Host '  · 看哪条判据要桌面、哪条不需要：docs\开发环境.md'
Write-Host '  · 提交前：git add -A ; python -X utf8 tools\presubmit_scan.py'
exit $(if ($failed.Count -eq 0) { 0 } else { 1 })
