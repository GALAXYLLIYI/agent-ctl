# 这个目录现在是什么（2026-10-09 查实）

## 一句话

**它已经不是打包源了。** 改这里任何文件都不会进包。

## 三条身份，逐条有据

### ① 不是打包源（T14 阶段 2 起）

`tools/pack_dist.py` 已改为**打包时现场从老线真源生成**：

```
dsh-viewer-plugin/tools ──copy+净化──▶ dist/_legacy_for_pack/ ──▶ agentctl.zip
```

生成器是 `tools/gen_legacy.py`（7 条精确净化规则 + AST 补 import + md5 台账）。
打包流程**根本不读本目录**。

⇒ **要改控制链路，改 `<工作区>/Documents/dsh-viewer-plugin/tools/` 那一份。**
在本目录改完发现包没变，不是 bug，是这条路已经不参与打包了。

### ② 仍是运行时兜底家目录（不能删）

新线 `src/agentctl/gate.py` 的 `_resolve_home()` 在源码态会**向上找 `legacy_tools` 目录名**。
删掉会让 `work/_t7_selftest.py` 当场崩（它从这个目录拷 `gate.py`）。

2026-10-09 曾按工单把它归档过一次，T7 立刻 `FileNotFoundError`，已撤销。

### ③ 仍是若干工具脚本的取件处

`tools/fix_leaks.py`、`tools/verify_dist.py`、`tools/port_legacy.py`、
以及 `work/` 下的 T7/T12/T13/T15 自测脚本都从这里取文件。

## ⚠️ 本目录里的 `gate.py` 是危险品，不是生效的那份

它**不认**通行证里的 `targets` 约束 ⇒ 带 targets 的证送到它手里，约束被**静默忽略**
⇒ **fail-OPEN**，与项目其余闸门的 fail-closed 方向相反。

2026-10-09 22:31 实测当前实际生效的是：

| 项 | 值 |
|---|---|
| HOME | `<工作区>/Documents/dsh-viewer-plugin/tools` |
| gate.py | 该目录下那份（18799 B） |
| 体检 | `ok=true`、`caps.targets=true` ← 认 targets |

⇒ **生效的是老线那份，本目录这份没在生效链路上。**
别把带 targets 的证送到本目录这份来。

## 为什么要留着这些「过时」的注释

`gate.py` 顶部有一段 T7 出单时写的警告，说「本文件就是当前实际生效的那一份」——
**那是 T7 落地前的状态，写这话时是对的，现在过时了**。

2026-10-09 已在那段上加了「已作废」标记并附实测证据。
**不要删这段历史注释**：它记录了一个真实事故（静默 fail-open），
后人看到才能明白为什么要加闸门版本自检。

---

相关文档：

- `work/T14_逐条台账_20261009_182843.md` —— 双份漂移逐条台账（363 行）
- `agent-manual/框架/2026-10-09_T14阶段1_逐条裁决.md` —— 哪些搬、哪些废、为什么
- `tools/gen_legacy.py` —— 现在的打包源生成器

