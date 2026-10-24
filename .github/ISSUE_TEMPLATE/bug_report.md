---
name: Bug report
about: Something broke — include the exact code= and the evidence
title: "[bug] "
labels: bug
---

## 现象（一句）
<!-- 发生了什么，不要写“好像不对”——写具体动作 + 结果 -->

## 复现
1. 命令/工具：
2. 参数：
3. 期望：
4. 实际：

## 证据
- 退出码 / `code=`：
- 原始输出（贴 `--out` 的 json 或截图路径）：
- 版本：`agentctl --version` 或 `git rev-parse --short HEAD`

## 环境
- Windows 版本：
- Python：
- 是否安装版（`%LOCALAPPDATA%\AgentCtl`）/ 源码跑：

## 自查过
- [ ] 重启过 MCP server（改代码后必须）
- [ ] `python -X utf8 tools\验收_发版.py` 的输出
