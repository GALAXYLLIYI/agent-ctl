# fresh_clone_check · 干净 clone 自足验收（说明 + 可复制命令）

> 目的：证明**这份仓在任何一台干净 Windows 机器上都能自证活着的**——
> 不依赖开发机的 `F:\` 绝对路径、不依赖 `%LOCALAPPDATA%` 里的历史状态、
> 不依赖仓外的真源目录。这是"可审计"承诺的地基（产品定位 §四：仓不自足，可审计自相矛盾）。
> 配套：窗口D 窗 X 的 `tools\selfcheck_minrepo.py`（仓自足验收脚本，本单同日并行施工；
> 命令行以其 `--help`/回执为准，若有出入以 X 回执更新本文件）。

## 一、"自足"的五条验收面

1. **无盘符绝对路径**：`src/`、`tests/`、`contract/`、`docs/` 里 grep 不到 `F:\`、`C:\Users\<用户>`（写死判据同口径）；
2. **无本机状态依赖**：跑起来不需要 `%LOCALAPPDATA%\AgentCtl` 已存在、不需要真 `auth_pass.json`、不需要任何租约；
3. **依赖可装**：`pip install -r requirements.txt` 在全新 Python 3.12 环境成功；
4. **legacy_tools 是产物不是真源**：`gen_legacy` 重生成后与仓内逐字节一致（CHK-A 两腿）；
5. **判定以副本为准**：所有检查读的是**自己所在的这份仓**，输出里不出现真仓路径（原 wM 判据的核心断言，wM 降级后由本脚本继承）。

## 二、标准流程（可复制）

```powershell
# ① 干净 clone（全新目录，别复用工作仓）
git clone <你的仓库地址> agent-ctl-fresh
cd agent-ctl-fresh

# ② 装依赖（全新环境首跑）
python -X utf8 -m pip install -r requirements.txt

# ③ 仓自足验收（窗X 脚本；无参数默认全查）
python -X utf8 tools\selfcheck_minrepo.py

# ④ 负向对照（可选但推荐）：故意抽掉一块，必须红
python -X utf8 tools\selfcheck_minrepo.py --expect-fail   # 以 X 回执口径为准
```

**通过标准**：③ 退出码 0，且输出中 ① 无 `F:\` / `C:\Users\` 字样、② 无"跳过（文件不存在）"类软提示；
**未通过**：任何 rc≠0 / 出现真机路径 / 出现"缺件但继续跑" ⇒ 仓不自足，发布冻结。

## 三、与降载后判据体系的关系

- 原 **wM（可移植性）** 判据降级后，其职责**整体移交本脚本**（见 `docs\判据降载清单.md`）——
  快车道不再跑 wM，但"干净 clone 能跑"仍是发布验收口径之一（README 验收表）。
- 原 **台账（CHK-A）** 保留在快车道，与本脚本分工：台账管"产物 vs 真源一致"，本脚本管"副本自己能活"。
- 发布流程顺序建议：**干净 clone 自足（本脚本）→ 快车道 6 条 → release_audit 16 条**（见判据降载清单）。

## 四、本机验证留痕

- 2026-10-18 窗Y 只写了本说明，未执行 clone/脚本（不跑判据约束）；
- 脚本由 窗口D 窗 X 同日交付（`work\_X_receipt.md`），首跑记录以该回执为准。
