# 缺陷修复：`ctl_fill` 忽略 `eid`（静默落到 (0,0)）· 2026-10-25

## 现象

对 WinForms 靶场调 `ctl_fill` 并传 `eid` + `from_capture`：

```
⛔ rc=5：**坐标类**：填字段坐标 (0,0) 不在窗口矩形 [30,30,870,810] 内
```

## 根因

`core.t_fill` 只读 `args["xy"]`，**从不看 `eid`**：

```python
x, y = _xy(args.get("xy") or [0, 0])      # ← eid 被无视，缺省 (0,0)
```

对比 `ctl_click`：它有 eid 严格路径（旁表取件 + 身份复核）。`ctl_fill` 少了这一段，
于是“以为填的是那个框、实际朝屏幕左上角打字”。

## 修法（只加不改）

`t_fill` 现在按 `ctl_click` 的口径处理三种定位口：

1. `eid` + `from_capture` ⇒ 用 `_eid_lookup` 取回 **center**（那就是物理像素坐标）；
2. `xy` ⇒ 原样使用；
3. `eid` 与 `xy` 同时给 ⇒ **拒绝**（同 click 的 Q3 裁定，防“以为点 A 实际点 B”）；
4. `name` ⇒ 明确告知未开放（先 `ctl_find` 拿 eid/center）；
5. 定位不到 ⇒ **`code=4` + 人话处方**，**不再用 (0,0) 兜底**。

## 实测（重启 MCP 加载新 core 之后）

```
ctl_fill(hwnd, eid=e2, text="fill via eid ok", from_capture=...)
→ ok=True code=0
→ 坐标(720,537) 窗口「fillfix_*.txt - Notepad」✔ 页面已变化
→ uiautomation 读回：'fill via eid ok'（逐字一致）
```

错误路径（离线）：
- `eid` 无 `from_capture` ⇒ code=4「拿不到 eid 的坐标（缺失）…」；
- `eid`+`xy` ⇒ code=4「只能二选一」；
- `name` ⇒ code=4「请先 ctl_find」。

## 附带发现

改完代码**必须先重启 `mcp_http`** 才生效——第一次复测仍报 (0,0)，因为 30544 进程里跑的是旧 core。

> 最后更新：2026-10-25 14:0x
