# 复核：`act.py` 全量 rc 出口审计（窗口B · 静态只读）

> 审计对象：**交卷态快照**，不是仓库里可能仍在变的 `legacy_tools/_src/act.py`。
> 审计方式：**只读静态阅读**。未运行任何 GUI 自动化，未接触任何 QQ 窗口，未创建判定脚本，未修改任何 `.py`。

## 一、结论（先看这一段）

1. 全快照共 **90 处**能产出整数 rc 的 `return` 语句：**57 处只可能返回非 0**、**17 处恒为 0**、**16 处可 0 可非 0**（含 `main()` 的转发与 `_tell_next` 的透传）。因此**能返回非 0 的出口共 73 处**。
2. 其中 **9 处**把「测不了 / 不知道」折叠成了「确认没变（rc=8）」或「成功（rc=0）」，列为 **FIND 1–9**。最危险的是：
   - **F5**：`read` 动作在 `read_doc()` 明确失败（`txt is None`）时仍 `return 0`，调用方会拿到「读取成功、字符数为 null」。
   - **F1**：`do_type` 正常元素回读失败时，先 `_norm_nl(None)` 抛 `AttributeError`（进程 rc=1）；即使绕过崩溃，最终也落到 `return 0 if (dry or ok) else 8`，把「没读到」报成「确认没变」。
   - **F3**：`do_keys` 已经算出 `changed=None/True/False`，却无条件 `return 0`，且不发 `effect` 信封。
   - **F4**：`do_scroll` 两条真实路径都无条件 `return 0`，`_post_wheel` 的 `_ch` 只打日志就丢弃，现成的 `scroll_pct()` 完全没用。
3. 另有 **5 处判为 PASS 但有保留**（不是 FIND）：`do_click:2925`、`_gate_denied:483`、`_tell_deny:901`、`main --snap-check:3656`、`main targets:3711/3715`。理由见第八节。
4. 另有 **5 个不属于本单「三态折叠」主题、但同样是真 bug** 的问题（dry-run 真打字、dry-run 移动光标、`root=None` 崩溃、反竞态返回值未使用、targets 空列表成功），见第九节。它们不计入 FIND，但建议同批修。
5. 所有 FIND 的修法都遵循同一条：**`True → 0` / `None → 5` / `False → 8`**；**`dry` 分支保持原样**，不因本次修复改变演练语义。

## 二、审计对象身份与计数口径

| 项 | 值 |
|---|---|
| 快照路径 | `<仓库根>\work\_审计_act全量rc出口_20221023\act_交卷态快照_M19P.py` |
| 字节数 | `203875` |
| MD5 | `47E348663E377676FA0991A184FB00F6` |
| 行数 | `3742` |
| 仓库活动源 | `<仓库根>\legacy_tools\_src\act.py`（**未作为审计依据，未修改**） |

**计数口径（可复现）**：

- 只统计「能产出整数 rc 的 `return` 语句行」。返回 dict / tuple / bool / 字符串的辅助函数（如 `_wait_effect`、`modal_blocker`、`read_doc`）不计入 rc 出口。
- `_rc_from_changed` 的 4 个分支逐行计；`ensure_foreground` 的 4 个返回逐行计；`main()` 里 `return do_*()` 的转发行逐行计。
- 「恒 0」= 该行表达式在任何输入下都只能得到 0；「仅非 0」= 该行一旦执行就必然是同一个或某一组非 0；「混合」= 同一行可能返回 0 也可能返回非 0。
- `-1`、异常 traceback（Python 默认 rc=1）不计入「显式 rc 出口」，但在 FIND 里单独说明。

## 三、汇总表（按函数）

| 函数 | rc 出口 | 恒 0 | 仅非 0 | 0/非 0 混合 | 非零可达 | FIND |
|---|---:|---:|---:|---:|---:|---|
| `_selftest_or_die` | 2 | 1 | 1 | 0 | 1 | — |
| `_tell_next`（通用透传） | 1 | 0 | 0 | 1 | 1 | — |
| `_gate_denied` | 1 | 0 | 1 | 0 | 1 | —（见 PASS*） |
| `_tell_deny` | 2 | 0 | 2 | 0 | 2 | —（见 PASS*） |
| `_rc_from_changed` | 4 | 2 | 2 | 0 | 2 | —（口径正确） |
| `require_no_modal` | 2 | 1 | 1 | 0 | 1 | **F6** |
| `ensure_foreground` | 4 | 3 | 1 | 0 | 1 | — |
| `do_keys` | 4 | 2 | 2 | 0 | 2 | **F3** |
| `do_fill` | 7 | 1 | 5 | 1 | 6 | —（W1 正确） |
| `do_click` | 10 | 1 | 9 | 0 | 9 | —（见 PASS*） |
| `_post_hint` | 1 | 0 | 1 | 0 | 1 | — |
| `do_scroll` | 7 | 2 | 5 | 0 | 5 | **F4** |
| `do_focus` | 3 | 0 | 2 | 1 | 3 | — |
| `do_type` | 11 | 1 | 7 | 3 | 10 | **F1 / F7** |
| `do_drag` | 6 | 0 | 5 | 1 | 6 | **F2** |
| `main` | 25 | 3 | 13 | 9 | 22 | **F5** |
| **合计** | **90** | **17** | **57** | **16** | **73** | **9** |

> 注：`do_fill:2627`、`do_type:3388/3419` 已经正确走 `_rc_from_changed`；`do_type:3453`（普通元素回读）仍是旧写法，是 F1 的落点。

## 四、非零 rc 出口全表（57 行「仅非 0」）

> 表中「源」写的是决定 rc 的量；「三态语义」写的是该出口对 `True / None / False` 的映射。固定码没有三态，直接写含义。

### 4.1 启动自检 / 通用拒绝出口

| 行 | 函数 | rc | 源 | 三态语义 |
|---:|---|---|---|---|
| 155 | `_selftest_or_die` | 11 | `_SELFTEST_MISS` 非空 | 环境缺件；纯失败，无三态 |
| 483 | `_gate_denied` | 8 或 9 | `is_quota` | 8=额度耗尽（**与「确认没变」撞码**）；9=无证；均为纯失败 |
| 901 | `_tell_deny` | 7 | `TERMINAL_ASK_RC` | 终端待人工确认；**与「文档非空」撞码** |
| 908 | `_tell_deny` | 3 | 固定 | 防自授权闸门；纯失败 |

### 4.2 三态映射与前置闸

| 行 | 函数 | rc | 源 | 三态语义 |
|---:|---|---|---|---|
| 1380 | `_rc_from_changed` | 5 | `changed is None` | **unknown → 5**（正确口径） |
| 1381 | `_rc_from_changed` | 8 | `changed is False` | **确认没变 → 8**（正确口径） |
| 1634 | `require_no_modal` | 6 | `blocked is True` | 确认被模态挡住 |
| 1711 | `ensure_foreground` | 5 | `fg` 为假 | 置前失败 |

### 4.3 `do_keys`

| 行 | 函数 | rc | 源 | 三态语义 |
|---:|---|---|---|---|
| 2484 | `do_keys` | 8/9 | `_gate_denied` | 无证/额度 |
| 2523 | `do_keys` | 5（`_fg`） | `ensure_foreground` | 置前失败 |

### 4.4 `do_fill`

| 行 | 函数 | rc | 源 | 三态语义 |
|---:|---|---|---|---|
| 2568 | `do_fill` | 3 或 7 | `why` | 目标被拒 |
| 2571 | `do_fill` | 6 | `_mb` | 模态挡住 |
| 2579 | `do_fill` | 8/9 | `_gate_denied` | 无证/额度 |
| 2583 | `do_fill` | 5 | 坐标越界 | 坐标类 |
| 2589 | `do_fill` | 5（`_fg`） | `ensure_foreground` | 置前失败 |

### 4.5 `do_click`

| 行 | 函数 | rc | 源 | 三态语义 |
|---:|---|---|---|---|
| 2685 | `do_click` | 3 或 7 | `why` | 目标被拒 |
| 2689 | `do_click` | 6 | `_mb` | 模态挡住 |
| 2697 | `do_click` | 8/9 | `_gate_denied` | 无证/额度 |
| 2724 | `do_click` | 5（`_fg`） | `ensure_foreground` | 置前失败 |
| 2755 | `do_click` | 4 | `find_control` 返回 `None` | 控件找不到 |
| 2786 | `do_click` | 5 | `r_ok` 为假 | 控件未渲染（坐标类） |
| 2794 | `do_click` | 5 | 控件中心越界 | 坐标类 |
| 2814 | `do_click` | 5 | 反竞态 `r2 != r1` | 界面不稳定 |
| 2841 | `do_click` | 5 | 点击坐标越界 | 坐标类 |

### 4.6 `do_scroll` / `_post_hint`

| 行 | 函数 | rc | 源 | 三态语义 |
|---:|---|---|---|---|
| 3042 | `do_scroll` | 3 或 7 | `why` | 目标被拒 |
| 3046 | `do_scroll` | 6 | `_mb` | 模态挡住 |
| 3050 | `do_scroll` | 8/9 | `_gate_denied` | 无证/额度 |
| 3112 | `do_scroll` | 5（`_fg`） | `ensure_foreground` | 置前失败 |
| 3119 | `do_scroll` | 5 | 滚轮落点越界 | 坐标类 |
| 3030 | `_post_hint` | 5 | 固定 | 后台投递已投但窗口无反应（**投递成功 ≠ 生效**） |

### 4.7 `do_focus`

| 行 | 函数 | rc | 源 | 三态语义 |
|---:|---|---|---|---|
| 3264 | `do_focus` | 3 或 7 | `why` | 目标被拒 |
| 3268 | `do_focus` | 8/9 | `_gate_denied` | 无证/额度 |

### 4.8 `do_type`

| 行 | 函数 | rc | 源 | 三态语义 |
|---:|---|---|---|---|
| 3334 | `do_type` | 3 或 7 | `why` | 目标被拒 |
| 3338 | `do_type` | 6 | `_mb` | 模态挡住 |
| 3342 | `do_type` | 8/9 | `_gate_denied` | 无证/额度 |
| 3346 | `do_type` | 5（`_fg`） | `ensure_foreground` | 置前失败 |
| 3352 | `do_type` | 2 | `nl_mode` 非法 | 参数错 |
| 3391 | `do_type` | 4 | `before is None and not force_send` | 无文本区（**语义过宽，见 F7**） |
| 3426 | `do_type` | 7 | `before.strip() != ""` | 文档非空防污染 |

### 4.9 `do_drag`

| 行 | 函数 | rc | 源 | 三态语义 |
|---:|---|---|---|---|
| 3460 | `do_drag` | 3 或 7 | `why` | 目标被拒 |
| 3466 | `do_drag` | 6 | `_mb` | 模态挡住 |
| 3470 | `do_drag` | 8/9 | `_gate_denied` | 无证/额度 |
| 3479 | `do_drag` | 5（`_fg`） | `ensure_foreground` | 置前失败 |
| 3489 | `do_drag` | 5 | 拖拽端点越界 | 坐标类 |

### 4.10 `main`

| 行 | 函数 | rc | 源 | 三态语义 |
|---:|---|---|---|---|
| 3598 | `main` | 2 | 缺动作名 | 参数错 |
| 3600 | `main` | 2 | 缺 `--hwnd` | 参数错 |
| 3604 | `main` | 2 | `--post` 用在非 click | 参数错 |
| 3620 | `main` | 2 | fill 缺 `--xy` | 参数错 |
| 3631 | `main` | 2 | `resolve_vk` 抛 `ValueError` | 参数错 |
| 3645 | `main` | 8/9 | `_gate_denied` | 无证/额度 |
| 3656 | `main` | 5 | `_tell_next(5, …)` | 快照诊断（**故意的 5**） |
| 3663 | `main` | 9 | `read` 无证 | 纯失败 |
| 3677 | `main` | 9 | `targets` 无证 | 纯失败 |
| 3680 | `main` | 3 或 7 | `why` | 目标被拒 |
| 3720 | `main` | 1 | click 缺 `--name/--xy` | 参数错 |
| 3728 | `main` | 1 | type 缺 `--text` | 参数错 |
| 3734 | `main` | 1 | drag 缺 `--to` | 参数错 |

## 五、混合出口全表（16 行：可 0 可非 0）

| 行 | 函数 | 表达式 / 源 | 三态语义 | 判定 |
|---:|---|---|---|---|
| 291 | `_tell_next` | `return rc` | 入参透传 | 中性 |
| 2627 | `do_fill` | `_rc_from_changed(changed)` | True→0 / None→5 / False→8 | **正确** |
| 3286 | `do_focus` | `0 if (dry or now_fg) else 5` | dry→0 / 前台到位→0 / 未到位→5 | 可接受 |
| 3388 | `do_type --focus` | `_rc_from_changed(changed)` | True→0 / None→5 / False→8 | **正确** |
| 3419 | `do_type --force-send` | `_rc_from_changed(changed, dry=dry)` | dry→0 / True→0 / None→5 / False→8 | **正确** |
| 3453 | `do_type` 普通回读 | `0 if (dry or ok) else 8` | dry→0 / ok→0 / 不 ok→8 | **F1**（`after=None` 先崩） |
| 3501 | `do_drag` | `0 if (dry or (sel is not None and len(sel) > 0)) else 8` | dry→0 / 有选中→0 / 其余→8 | **F2**（`None` 与 `""` 不分） |
| 3511 | `main` | `_selftest_or_die()` | 0 / 11 | 转发 |
| 3615 | `main approve` | `0 if ok else 3` | 成功→0 / 拒签→3 | 可接受 |
| 3622 | `main fill` | `do_fill(...)` | 透传 | 转发 |
| 3632 | `main keys` | `do_keys(...)` | 透传 | 转发 |
| 3721 | `main click` | `do_click(...)` | 透传 | 转发 |
| 3724 | `main focus` | `do_focus(...)` | 透传 | 转发 |
| 3729 | `main type` | `do_type(...)` | 透传 | 转发 |
| 3737 | `main drag` | `do_drag(...)` | 透传 | 转发 |
| 3738 | `main scroll` | `do_scroll(...)` | 透传 | 转发 |

## 六、恒 0 出口全表（17 行）

| 行 | 函数 | 说明 |
|---:|---|---|
| 139 | `_selftest_or_die` | 自检通过 |
| 1376 | `_rc_from_changed` | `dry=True` → 0（**保持不动**） |
| 1378 | `_rc_from_changed` | `changed is True` → 0 |
| 1635 | `require_no_modal` | 未探测到模态（**含探测失败，见 F6**） |
| 1703 | `ensure_foreground` | 已在前台 |
| 1707 | `ensure_foreground` | 置前后已在前台 |
| 1710 | `ensure_foreground` | `dry=True` |
| 2526 | `do_keys` | `dry=True` |
| 2552 | `do_keys` | **普通路径无条件 0（F3）** |
| 2592 | `do_fill` | `dry=True` |
| 2925 | `do_click` | **设计上把画面裁决交给 core 信封（PASS*）** |
| 3090 | `do_scroll` | **UIA Scroll 后无回读（F4）** |
| 3130 | `do_scroll` | **滚轮后无回读（F4）** |
| 3357 | `do_type --focus` | `dry=True` |
| 3670 | `main read` | **`txt is None` 也 0（F5）** |
| 3711 | `main targets --compact` | **空/部分列表也 0（PASS*）** |
| 3715 | `main targets --json` | **空/部分列表也 0（PASS*）** |

## 七、FIND 明细（9 条）

> 每条都按「行号 → 现码 → 为什么是折叠 → 建议修法（三态 + dry 不动）」写。
> 建议修法只给方向与关键分支，不直接改文件。

### F1 · `do_type` 普通元素回读：`after is None` 既不报 5，也报不出 8

**位置**：`do_type()`，行 **3443–3453**（崩溃点 3445，错误映射点 3453）。

**现码**：

```python
    _, after, _ = read_doc(hwnd)
    want = before + text
    ok = (after == want) or (_norm_nl(after) == _norm_nl(want))
    nl_only = (after != want)
    ...
    return 0 if (dry or ok) else 8
```

**为什么是折叠**：

- `read_doc()` 有三条失败路径会给出 `after=None`：
  - 3225：未获授权 → `(None, None, "未获授权")`
  - 3234：窗口不响应 / 找不到文本编辑区 → `(None, None, ...)`
  - 3243：控件在、但 `TextPattern` 读失败 → `(c, None, "读不到文本：…")`
- `do_type` 把元组第二项以外的 `c` 与 `how` **全部丢弃**。
- `after=None` 时，3445 先算 `_norm_nl(None)`，直接 `AttributeError: 'NoneType' object has no attribute 'replace'`，**未捕获 → Python 进程 rc=1**。
- 即使只看 3453：`ok=False` → `return 8`，把「没读到」报成「确认没变」。
- 两条路都不是 rc=5，也都不是「成功」；但调用方拿到的分别是「崩溃」和「确认没变」，**都不是 unknown**。

**三态语义应为**：回读成功且一致 → `True → 0`；回读成功但不一致 → `False → 8`；回读失败（`after is None`）→ `None → 5`。

**建议修法**：

```python
    _c, after, _how = read_doc(hwnd)
    if after is None and not dry:          # ★ dry 分支不动
        return _tell_next(5, f"打字后回读失败：{_how}", hwnd,
                          extra="输入可能已经送出，但读不回内容 ⇒ 按 unknown 处理，"
                                "不要报「确认没变」，先重新观测/重试。")
    want = before + text
    ok = (after == want) or (_norm_nl(after) == _norm_nl(want))
    ...
    return 0 if (dry or ok) else 8
```

> 注意 `not dry` 必须保留：演练时本来就不发键，`read_doc` 仍可能失败；当前演练靠 `dry or ok` 恒 0，不能因为本修复把演练改成 5。

---

### F2 · `do_drag` 选区回读：`None`（读不了）与 `""`（确实没选中）一起报 8

**位置**：`do_drag()`，行 **3497–3501**；`read_selection()` 行 **3246–3256**。

**现码**：

```python
    sel = read_selection(hwnd) if not dry else None
    log(...)
    return 0 if (dry or (sel is not None and len(sel) > 0)) else 8
```

**为什么是折叠**：

- `read_selection()` 在三种情况下返回 `None`：
  - 3249：未获授权；
  - 3252：没有文档控件；
  - 3256：`GetTextPattern().GetSelection()` 抛异常（控件在、但读不了选区）。
- 只有「读到了、但列表为空」才会得到 `""`（`"".join([])`），这才是**真正的「没有选中」**。
- 3501 把 `None` 与 `""` 一起判成 `8`：「确认没选中」。但 `None` 是「不知道有没有选中」。
- 另外 `dry` 时 `sel=None`，靠 `dry` 短路返回 0 —— 这一条是**正确且必须保留**的。

**三态语义应为**：`len(sel) > 0 → 0`；`sel == "" → 8`；`sel is None → 5`。

**建议修法**：

```python
    if dry:
        return 0
    sel = read_selection(hwnd)
    log(...)
    if sel is None:
        return _tell_next(5, "拖拽后读不到选中文本（无权限 / 无文档控件 / TextPattern 不可用）",
                          hwnd,
                          extra="拖拽动作可能已经发生，但结果**没测出来**；"
                                "不要当成「没选中」，先重新观测。")
    return 0 if len(sel) > 0 else 8
```

> 若不想改 `read_selection` 的签名，至少要在 `do_drag` 里把 `None` 单独分出来；`""` 继续走 8。

---

### F3 · `do_keys`：算出了 `changed`，却无条件 `return 0`

**位置**：`do_keys()`，行 **2545–2552**。

**现码**：

```python
    after = evidence(hwnd, "after")
    fp0, fp1 = _img_fp(before.get("文件") or ""), _img_fp(after.get("文件") or "")
    changed = None if (fp0 is None or fp1 is None) else (fp1 != fp0)
    ev = "" if changed is None else ("　✔ 页面已变化" if changed else "　⚠ 页面无变化")
    log(...)
    return 0
```

**为什么是折叠**：

- `changed` 已经按三态算好了（指纹缺失 = `None`；相同 = `False`；不同 = `True`），但 2552 **无条件返回 0**。
- 后果一：`None`（没测出来）与 `False`（确认没变）都被报成成功。
- 后果二：这条路径**没有** `_emit_effect(_eff, hwnd)`，core 拿不到 `effect` 信封，也就无从用自己的尺子复核。
- 后果三：`do_keys` 的 `dry` 分支在 2526 已经 `return 0`，所以 2552 只服务真实路径，修它不会碰演练语义。

**三态语义应为**：`changed is True → 0`；`changed is None → 5`；`changed is False → 8`。

**建议修法**（最小改动）：

```python
    # 2552
    return _rc_from_changed(changed)
```

> 更强的修法：在 `do_keys` 里也用 `_wait_effect` + `_emit_effect`，与 `do_click` 一样把「帧 + 原始量」回传 core；但即使只做最小改动，也必须把 2552 换成三态映射。
>
> 注意：按键的「页面变了」同样不能证明「这个键被目标接收了」（目标可能自己在动）。所以 `changed is True` 只能是 0（输入已送出）；真正的生效判定仍归 core。

---

### F4 · `do_scroll`：两条真实路径都没有生效核验，一律 0

**位置**：`do_scroll()`，行 **3081–3090**（UIA Scroll）、**3093–3108**（后台滚轮）、**3122–3130**（前台滚轮）；`scroll_pct()` 行 **2928–2938**；`_post_wheel()` 行 **2951–2976**。

**现码（UIA 路径）**：

```python
    if target is not None and not force_wheel:
        try:
            pat = target.GetScrollPattern()
            amt = (auto.ScrollAmount.LargeIncrement if amount < 0
                   else auto.ScrollAmount.LargeDecrement)
            if not dry:
                pat.Scroll(auto.ScrollAmount.NoAmount, amt)
            log(...)
            return 0
        except Exception as e:
            log(...)
```

**现码（滚轮路径，末段）**：

```python
    user32.SetCursorPos(x, y)
    time.sleep(0.12)
    if not dry:
        wheel(120 * amount)
    log(...)
    return 0
```

**为什么是折叠**：

- UIA 路径：`pat.Scroll()` 不抛异常只证明「调用被接受」，不证明「滚动位置真的变了」（已在顶端/底端、或控件忽略该 Pattern 时同样不抛）。3090 直接 `return 0`。
- 后台滚轮路径：`_post_wheel()` 返回的只是 `PostMessageW` 的布尔值；3100 算出的 `_ch` 在 3101–3108 **只用于打日志，随后被丢弃**，最终仍落回前台滚轮并 `return 0`。
- 前台滚轮路径：`wheel()` 无返回值，3130 直接 `return 0`。
- `scroll_pct()`（2928）就是为「核验滚动是否真发生」写的，返回 `None` 表示「该控件不支持」——**当前无人调用**。
- `dry` 路径：3090 在 `not dry` 之外，但 `dry` 时也会 `return 0`；3122 的 `SetCursorPos` 甚至在 `if not dry` 之外（见第九节）。

**三态语义应为**：滚动前/后百分比都读得到 → 变了 `0` / 没变 `8`；任一侧读不到 → `5`；`dry → 0`。

**建议修法**：

```python
    # UIA 路径
    if not dry:
        p0 = scroll_pct(hwnd)
        pat.Scroll(auto.ScrollAmount.NoAmount, amt)
        time.sleep(_settle(200))
        p1 = scroll_pct(hwnd)
        if p0 is None or p1 is None:
            return _tell_next(5, "滚动已发出，但读不到滚动位置（该控件不支持 ScrollPattern）",
                              hwnd, extra="按 unknown 处理，不要报「确认没变」。")
        changed = (p1 != p0)
    else:
        changed = None
    log(...)
    return _rc_from_changed(changed, dry=dry)
```

滚轮路径同理：在 `wheel()` 前后各读一次 `scroll_pct`；若两次都 `None`，**不要**拿 `_ch` 当替代（3102 的注释已经说明「画面变了不证明滚轮生效」），直接 `5`。`dry` 继续保持 `0`。

> 边界：滚到顶/底时 `p1 == p0` 是**真实的「没变」**，应报 8；但如果控件不支持百分比，则必须是 5，两者不能混。

---

### F5 · `main read`：`read_doc` 失败仍 `return 0`

**位置**：`main()`，行 **3658–3670**。

**现码**：

```python
    if a.action == "read":
        try:
            gate.require_any(gate.PERCEIVE_SCOPES, "读文档")
        except gate.GateDenied as e:
            print("⛔ 未获授权，不读取：" + str(e))
            return 9
        _c, txt, how = read_doc(a.hwnd)
        sel = read_selection(a.hwnd)
        print(json.dumps({"读法": how, "字符数": len(txt) if txt is not None else None,
                          "前80字": (txt or "")[:80], "选中": (sel or "")[:60],
                          "选中字数": len(sel) if sel is not None else None},
                         ensure_ascii=False, indent=1))
        return 0
```

**为什么是折叠**：

- `read_doc()` 失败时 `txt=None`、`how` 里写着真实原因（「未获授权」/「目标窗口不响应」/「找不到文本编辑区」/「读不到文本：…」），但 3670 **无条件 `return 0`**。
- 输出里虽然把 `"字符数"` 写成 `null`，但**退出码是成功**。任何只看 rc 的调用方（drill / 本地 agent / `core.py`）都会把这条命令当成「读到了，只是内容为空」。
- `sel=None` 同样只体现在 `"选中字数": null`，rc 不受影响；如果主 payload 是正文，这可以接受，但正文失败绝不能被 0 吞掉。

**三态语义应为**：正文读到（哪怕是空串）→ `0`；无权限 → `9`（已有，保持）；控件在但读不回来 / 窗口不响应 → `5`；确实没有文本编辑区 → `4`。**无论哪一种，都不能是 0。**

**建议修法**：

```python
        _c, txt, how = read_doc(a.hwnd)
        sel = read_selection(a.hwnd)
        print(json.dumps({..., "正文未知": txt is None,
                          "选未知": sel is None}, ensure_ascii=False, indent=1))
        if txt is None:
            if how == "未获授权":
                return _tell_next(9, f"读文档被拒：{how}", a.hwnd)
            rc = 5 if (_c is not None or "不响应" in how) else 4
            return _tell_next(rc, f"读文档失败：{how}", a.hwnd,
                              extra="读到的是 unknown，不是「空文档」；"
                                    "先确认窗口是否响应 / 是否还有文本编辑区。")
        return 0
```

> `txt == ""` 必须继续返回 0：那是**真的读到了空文档**。不能用 `if not txt`。

---

### F6 · `require_no_modal` / `modal_blocker`：探测异常时 fail-open

**位置**：`modal_blocker()` 行 **1574–1585、1618–1621**；`require_no_modal()` 行 **1629–1635**。

**现码**：

```python
    try:
        if not user32.IsWindow(hwnd):
            return False, ""
        if not user32.IsWindowEnabled(hwnd):
            return True, "目标窗口当前处于禁用状态（典型：其上有模态对话框）"
    except Exception:
        return False, ""

    try:
        _tid, tpid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:
        return False, ""
    ...
    try:
        user32.EnumWindows(cb, 0)
    except Exception as e:
        return False, f"(枚举失败，未阻断：{e})"
```

```python
def require_no_modal(hwnd, what):
    blocked, why = modal_blocker(hwnd)
    if blocked:
        log(...)
        return 6
    return 0
```

**为什么是折叠**：

- `modal_blocker` 的 `False` 同时表示两种完全不同的结论：
  1. **确实没有模态**（枚举成功、没找到）；
  2. **根本没法判断**（`IsWindow`/`IsWindowEnabled`/`GetWindowThreadProcessId` 抛异常；`EnumWindows` 抛异常；甚至 1621 的文案自己都写着「未阻断」）。
- `require_no_modal` 只看 `if blocked:`，于是第二种也走 `return 0`。
- 这是**安全闸 fail-open**：动作会在一个「可能被模态挡着」的窗口上继续执行，后续的点击/打字可能落到对话框上。

**三态语义应为**：`True → 6`；`False → 0`；`None（探测失败）→ 5`（不确定，拒绝执行并让调用方重试）。

**建议修法**：

```python
def modal_blocker(hwnd):
    try:
        if not user32.IsWindow(hwnd):
            return None, "IsWindow 失败：目标窗口句柄无效或已销毁"
        if not user32.IsWindowEnabled(hwnd):
            return True, "..."
    except Exception as e:
        return None, f"模态探测失败（IsWindow/IsWindowEnabled）：{e}"

    try:
        _tid, tpid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception as e:
        return None, f"模态探测失败（取进程 ID）：{e}"
    ...
    try:
        user32.EnumWindows(cb, 0)
    except Exception as e:
        return None, f"模态探测失败（EnumWindows）：{e}"
```

```python
def require_no_modal(hwnd, what):
    blocked, why = modal_blocker(hwnd)
    if blocked is None:
        return _tell_next(5, f"无法确认是否被模态挡住：{why}", hwnd,
                          extra="这是 unknown，不是「没有模态」；"
                                "为安全起见本次不执行动作，先重试或人工确认。")
    if blocked:
        log(...)
        return 6
    return 0
```

> 该函数没有 `dry` 参数，修它不影响演练分支。`require_no_modal` 的调用方都把它放在 gate 之前，返回 5 不会消耗额度。

---

### F7 · `do_type` 前置读：把「文本区读失败」当成「没有文本区」

**位置**：`do_type()`，行 **3389–3404**；`read_doc()` 行 **3222–3243**。

**现码**：

```python
    c, before, how = read_doc(hwnd)
    if before is None and not force_send:
        return _tell_next(4, f"打不了字：{how}", hwnd, ...)
    if before is None and force_send:
        log("⚠ 目标无 UIA 文本区 → 走【网页表单模式】：直接发键，"
            "**不做 UIA 回读核验**，改用前后截图比对（2026-09-19 补，此前是盲打无核验）")
        b0 = {} if dry else evidence(hwnd, "typesend0")
        before = ""
        ...
        send_text(text)
```

**为什么是折叠**：

- `read_doc()` 的四个失败态：
  - 3225：无权限 → `(None, None, "未获授权")`
  - 3234：无控件 / 窗口不响应 → `(None, None, "找不到文本编辑区" / "目标窗口不响应…")`
  - 3243：**控件在、但读失败** → `(c, None, "读不到文本：…")`
- `do_type` 只判断 `before is None`，把 `c` 和 `how` 都丢了。
- 于是「控件明明在、只是这次读不出来」被当成「目标没有 UIA 文本区」：
  - 不加 `--force-send`：报 rc=4「打不了字」，方向错（应该报 5「读不了」）。
  - 加 `--force-send`：直接进入盲发分支，**跳过 3421 的「文档非空」防污染护栏**。如果那个控件里其实有用户的真实文档，这一次就会打进去。
- 另外 3404 的 `send_text(text)` 在 `--dry-run` 下也会执行（见第九节），使这个分支更危险。

**三态语义应为**：`c is not None and before is None` → 读失败 → `5`，**绝不盲发**；`c is None and how == "未获授权"` → `9`；`c is None`（确实没有文本区）→ 允许 `--force-send` 的降级路径，否则 `4`。

**建议修法**：

```python
    c, before, how = read_doc(hwnd)
    if before is None and c is not None:
        return _tell_next(5, f"文本区存在，但内容读不回来：{how}", hwnd,
                          extra="现在无法确认文档是否为空；盲发可能污染真实文档。"
                                "先重新观测/重试，不要用 --force-send 绕过。")
    if before is None and how == "未获授权":
        return _tell_next(9, f"打不了字：{how}", hwnd)
    if before is None and not force_send:
        return _tell_next(4, f"打不了字：{how}", hwnd, ...)
    if before is None and force_send:
        ...   # 只有「确实没有文本区」才允许走到这里
```

> `dry` 分支保持原样：如果将来给 `--force-send --dry-run` 修「不真打字」，那是第九节的独立 bug，不与本条混。

---

### F8 · `_budget_verdict`：`changed=None` 被映射成「确认没变」

**位置**：`_budget_verdict()`，行 **1352–1356**（调用点 1414、1439）。

**现码**：

```python
    if not last_d:
        return False
    if last_d.get("changed") is True:
        return None
    return False
```

**为什么是折叠**：

- `last_d` 是 `_img_diff()` 的结果，形状是 `{"changed": True/False/None, "ratio": ...}`。
- `{"changed": None}` 是**真值**，`not last_d` 为假；`changed is True` 为假；于是落到 1356 `return False`。
- `False` 在 `_wait_effect` 里的含义是「预算内**没看到**变化」→ 调用方按 `changed is False` 报 **rc=8「确认没变」**。
- `last_d is None`（从未采到帧）同样返回 `False`。注释说这是「保持改前行为」，但改前行为本身就是本单要修的折叠。
- 结果是：**指纹算不出来**（图太小、编码失败、`_img_diff` 异常）被当成「确认没动」。

**三态语义应为**：`changed is True → None`（预算耗尽但最后一帧与基线不同，仍判不了）；`changed is None → None`；`changed is False → False`；`last_d is None → None`。

**建议修法**：

```python
    if not last_d:
        return None
    ch = last_d.get("changed")
    if ch is None:
        return None
    if ch is True:
        return None
    return False
```

> 这样 `_wait_effect` 的两个预算耗尽出口都返回「生效=None」，`do_click` 等调用方走 `changed is None` 的 unknown 文案；`do_click` 本身仍 `return 0`（PASS*），由 core 的信封判 5。
>
> `dry` 不受影响：`_wait_effect` 在 `base_img is None` 时本来就直接返回 `生效=None`，而各 `do_*` 的 dry 分支在调用它之前/之后都不把该值当失败。

---

### F9 · `_is_degraded` / `_emit_effect`：退化「判不了」被写成 `False`

**位置**：`_is_degraded()` 行 **1187–1192**；`_emit_effect()` 行 **1460**。

**现码**：

```python
def _is_degraded(img):
    if _fc is None or img is None:
        return False
    try:
        return bool(_fc.is_blank(img))
    except Exception:
        return False
```

```python
        payload = {"effect": {..., "degraded": bool((eff or {}).get("退化")), ...}}
```

**为什么是折叠**：

- `_is_degraded` 的 `False` 同时表示「图不空白（没退化）」与「没判」（`_fc` 未加载 / `img is None` / `is_blank` 抛异常）。
- `_emit_effect` 再用 `bool(...)` 把 `None`、缺失键、`0` 全部压成 `False`。
- core 收到的 `"degraded": false` 看起来像「已检查、未退化」，实际上可能是「根本没检查」。
- 这会让「退化」这一路证据在 core 侧失去 unknown 分支，进一步把 unknown 推向「正常」。

**三态语义应为**：`True → 退化`；`False → 未退化`；`None → 没判`。

**建议修法**（优先加法，避免破坏 core 现有布尔契约）：

```python
def _is_degraded(img):
    if _fc is None or img is None:
        return None
    try:
        v = _fc.is_blank(img)
    except Exception:
        return None
    return None if v is None else bool(v)
```

```python
        _deg = (eff or {}).get("退化")
        payload = {"effect": {
            ...,
            "degraded": None if _deg is None else bool(_deg),
            "degraded_known": _deg is not None,
            ...}}
```

> 若 core 目前只接受布尔，至少加 `degraded_known: false`；不要继续用 `bool(None)` 把「没判」伪装成「未退化」。
> 该字段只影响信封，不影响任何 `do_*` 的 `dry` 返回。

## 八、判 PASS 但有保留（不计 FIND，5 处）

### P1 · `do_click:2925` `return 0`

**现码**：`do_click` 末尾无条件 `return 0`。

**为什么不是 FIND**：这是**刻意的判定权上交**。2916–2924 的注释写明：act 的 rc 只表示「输入送没送到」，「画面变了没」由 core 用自己的尺子判；三条路径（后台投递 / SendInput / UIA）都调用了 `_emit_effect(_eff, hwnd)`，信封里带 `rounds`、`first_change_ms`、`degraded`、`frame_ref`、`口径`。因此它不是「把 unknown 折叠成成功」，而是「把结论换了个通道」。

**保留**：如果调用方**只看 rc、不读 stdout 的 `effect` 信封**，那么每一次 click 都会被当成成功。这个契约必须在 core 侧保证；另外 `_emit_effect` 的 `except` 兜底虽然发了 `degraded: null`，但**没有 `frame_ref`**，core 必须能识别这种「无帧」并回落 `wait_change`（1467 的注释说会回落，但本次未验证 core 实现）。

### P2 · `_gate_denied:483` 额度耗尽返回 8

**现码**：`return _tell_next(9 if not is_quota else 8, ...)`。

**为什么不是 FIND**：它不是「unknown → 8」，而是**另一个失败被塞进 8 这个码**。`NEXT_STEP[8]` 已经同时写了「页面无变化 / 额度相关问题」两种解释，人读得出来。

**保留**：机器侧若按 `act rc=8 ⇒ CODE_NO_CHANGE` 映射，会把「额度耗尽」报成「确认没变」。这是**码位撞车**，建议给额度单独一个码（或至少在信封里加 `reason="quota"`），不要与「确认没变」共用 8。

### P3 · `_tell_deny:901` 终端待确认返回 7

**现码**：`return _tell_next(TERMINAL_ASK_RC, ...)`，`TERMINAL_ASK_RC = 7`。

**为什么不是 FIND**：它是「等用户点同意」，不是「没测出来」。

**保留**：7 同时被 `do_type:3426` 的「文档非空」占用。`_tell_deny` 通过 `tip=` 显式传了终端话术来避免查表串味（276–277 的注释承认了这个撞车），但机器侧仍只能看到一个 7。建议后续拆码，或在信封里带 `reason="terminal_ask"`。

### P4 · `main --snap-check:3656` 返回 5

**现码**：`rc = _tell_next(5, why, a.hwnd, extra=extra)`，随后 3654–3655 明确打印「本条是只读诊断；rc=5 表示按 rc=5 的形状摆给你看，不是本次动作失败」。

**为什么不是 FIND**：这是**诊断命令的刻意形状**，且有人读文案兜底。

**保留**：机器侧若只按 rc 判，会把「诊断完成」当成「不稳定」。建议给诊断单独码，或让调用方只在 `--snap-check` 下忽略 rc。

### P5 · `main targets:3711/3715` 空列表返回 0

**现码**：`ts, geo = targets(...)`，打印后 `return 0`；`targets()` 在 `_need_perceive` 失败时返回 `[]`（1852），枚举结果也可能因 `limit=40` 被截断。

**为什么不是 FIND**：`targets` 是**列表命令**，不是「变了没」的裁决；返回 0 表示「命令跑完了」，空列表本身可以是合法结果（窗口确实没有可交互控件）。

**保留**：它无法区分「枚举成功但确实没有」与「枚举失败/被截断」。建议在输出里加 `"枚举失败": bool` / `"已截断": len(ts) >= limit`，让人和机器都能分辨。

## 九、其他非 FIND 问题（同批建议修）

这些不属于「unknown → 0/8」的三态折叠，但都是本快照里**可复现的静态缺陷**。

### X1 · `do_type:3402/3404`：`--force-send --dry-run` 真的会打字

```python
        if scan_code:
            n_scan, fb_chars = send_text_scan(text)
        else:
            send_text(text)
```

`b0` / `b1` 已经用 `{} if dry else evidence(...)` 保护了，但**发送本身没有 `if not dry`**。因此 `do_type --force-send --dry-run` 会真的把字打进目标窗口，违背 docstring 的「演练只算不做」。修法：`if not dry:` 包住 3401–3404。

### X2 · `do_scroll:3122`：`--dry-run` 会移动鼠标光标

```python
    user32.SetCursorPos(x, y)
    time.sleep(0.12)
    if not dry:
        wheel(120 * amount)
```

`wheel` 被 dry 保护了，`SetCursorPos` 没有。演练仍会把用户光标挪到窗口中心。修法：把 `SetCursorPos` 与 `sleep` 一起放进 `if not dry:`。

### X3 · `do_scroll:3113`：`root=None` 时 `_center(None)` 崩溃，路线 P 兜底失效

`_uia` 超时/失败时 3056/3059 把 `root` 置 `None`，注释说「直接走路线 P」。但 3113 无条件 `_center(root)`，而 `_center`（1820）直接访问 `ctrl.BoundingRectangle` → `AttributeError` → 进程 rc=1。**路线 P 永远走不到前台兜底**。修法：

```python
    if root is None:
        r = info["rect"]
        x, y, ok_rect = (r[0] + r[2]) // 2, (r[1] + r[3]) // 2, r, True
    else:
        x, y, rr, ok_rect = _center(root)
```

### X4 · `do_click:2809`：`_rdy` 算了但从不使用

`_rdy = _wait_ready(...)` 是「就绪轮询」的结果，但后续反竞态只比较 `r2 != r1`，`_rdy` 从未被读取（全文件仅此一处）。这意味着「界面还在动但矩形恰好没变」时仍会点下去，M7 的就绪轮询白做。修法：把 `_rdy` 的「是否稳定」并入 2813 的判断，或删掉这次无用轮询（后者会丢功能，不推荐）。

### X5 · `main targets` 无法表达「枚举失败」

见 P5。`targets()` 的 `_need_perceive` 失败与「真的没有控件」都返回 `[]`；`_uia` 失败则直接抛异常（不在 `targets` 内捕获），`main` 也没有 `try`，最终是 rc=1 traceback。建议给 `targets` 一个显式的失败标记，而不是用空列表兼任。

## 十、本次**没有**验证什么

1. **没有运行** `act.py`，没有做任何 GUI 自动化，没有接触 QQ 窗口，没有验证任何真机行为。全部结论来自对快照的静态阅读。
2. **没有改任何 `.py`**，没有创建判定/审计脚本；本文件是唯一写出的文件。
3. **没有验证 `core.py` 的实际映射**。文中「rc=5 ⇒ CODE_UNSTABLE，rc=8 ⇒ CODE_NO_CHANGE」来自快照 1372–1373 的注释与 `_rc_from_changed` 的 docstring，不是对 `core.py` 的独立复核。
4. **没有验证 `_fc.is_blank()` 是否可能返回 `None`**。F9 的建议按「可能返回 None」做防御；若它保证返回 bool，则 `_is_degraded` 的 `None` 只来自 `_fc is None / img is None / 异常` 三种情况，结论不变。
5. **没有验证真实控件的 `TextPattern` / `ScrollPattern` 行为**（例如 `GetSelection()` 在无选区时是返回空列表还是抛异常、`Scroll` 在边界是否抛异常）。F2/F4 的「None 与空串/同值必须分开」是按接口契约与代码路径推的，不是实测。
6. **没有穷举所有未捕获异常**。除 F1（`_norm_nl(None)`）与 X3（`_center(None)`）外，可能还有别的 traceback → rc=1 路径；本次只对 rc 出口与三态折叠负责。
7. **没有审计 `gate.py` / `perceive.py` / `inject_magic.py`** 的 rc 语义与异常行为；`_gate_denied` 的 8/9、`_need_perceive` 的 False 都按快照内的用法理解。
8. **没有验证 `_emit_effect` 的 stdout 信封在真实 core 调用下会被解析**；只确认了快照里 `print(json.dumps(...))` 的字段形状。
9. **没有审计仓库活动源** `legacy_tools/_src/act.py`；它可能与快照不同，本报告不对它负责。

## 十一、复核用一句话

> 这次要守的线只有一条：**`None` 不是 `False`**。
> `True → 0`、`None → 5`、`False → 8`；凡是「读不到 / 枚举失败 / 探测异常 / 没采到帧」的地方，都不许再落到 `0` 或 `8`。`dry` 分支一律不动。



