# 教训 JSONL 格式 v1（memory intake · 2026-10-29）

> 用途：把三宿主（ZCode / Codex / DSH）与两个循环（dayloop / nightloop）的**交付结论、
> 翻车教训、验证结论**从「会话与报告文件」搬进记忆库，做成可检索、可归因、带出处的 episode。
> 配套代码：`src/agentctl/memory/intake.py`（`ingest_lessons` / `parse_lesson`）；
> CLI：`python -m agentctl.memory record-lessons <file>`。
> 相关契约：`contract/tools.json` 的 `ctl_memory`（只读口）——本格式只影响**写入侧**。

## 1. 每行一个 JSON 对象（UTF-8，`\n` 分隔，允许空行）

```json
{
  "t": "2026-10-29T19:00:00+08:00",
  "actor": "dsh",
  "kind": "lesson",
  "obs": "发生了什么（一句话，能被检索到的那半句）",
  "outcome": "success",
  "result": "结论 / 教训（可以演进：重复写同一 obs 会合并到同一行并刷新它）",
  "target": "可选：主题词（去重键的一部分，缺省空串）",
  "session_id": "可选：来源会话 id",
  "verify": {
    "cred": {
      "file": "D:\\...\\验收报告_20261029.md",
      "anchor": "P0-1"
    }
  }
}
```

## 2. 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `t` | ✅ | ISO8601，**带时区偏移**（`+08:00`）。naive 时间会按本机时区补（P5 ⑫ 同类坑） |
| `actor` | ✅ | 归因宿主：`human` / `zcode` / `codex` / `dsh` / `dayloop` / `nightloop` / `unknown`（见 §4） |
| `kind` | ✅ | 自由文本。推荐词表：`lesson`（教训）/ `delivery`（交付）/ `pitfall`（翻车）/ `verify`（验证结论）/ `decision`（决策） |
| `obs` | ✅ | 发生了什么。**写可检索的那半句**（哈希嵌入器不分中文词，查询词要与 obs 同源） |
| `outcome` | ✅ | 三值结局：`success` / `failed` / `unobservable`（另允许 `blocked`，落库时 verdict 记 unobservable） |
| `result` | ✅ | 结论 / 教训。同一 obs 重复写入时，这一格会被**最新一次**覆盖（结论可以演进） |
| `target` | ⬜ | 主题词，参与去重键；不写就空串 |
| `session_id` | ⬜ | 来源会话 id（出处链的一环） |
| `verify.cred` | ✅（见 §3） | 出处：`file` 必填（哪份报告/哪个脚本），`anchor` 建议填小节号或行号 |

## 3. 出处硬规矩（本格式存在的理由）

1. **`outcome="success"` 必须带 `verify.cred.file`**——没有出处的「我成了」不算成功，
   写入会抛 `ValueError` 并整行拒收（`intake.REQUIRE_PROVENANCE`）。
2. **没验证过的就标 `unobservable`**，不要标 success。三值结局的语义与动作侧同一套：
   `success` = 效果已发生且被证实；`failed` = 可观测地没发生；`unobservable` = 没测出来。
3. `anchor` 要能被人一眼定位（`P0-1` / `§3` / `L24`）。生成方可以自己先做一次
   「anchor 是否真的在文件里」的校验（`v5_证据/build_lessons.py` 就是这么做的）。

## 4. actor 与分仓（subject）的关系

分仓仍是**二元**（`human` / `agent`），actor 只是机器仓内部的精确归因：

| actor | subject | 含义 |
|---|---|---|
| `human` | `human` | 人的桌面情景记忆（timeline harvest）。**不可被代理改写** |
| `zcode` / `codex` / `dsh` | `agent` | 三个宿主写的经验 |
| `dayloop` / `nightloop` | `agent` | 两个循环写的经验 |
| `unknown` | `agent` | 认不出就 unknown，**宁可 unknown 不许猜** |

映射是单向的：`human` ⇒ human 仓，其余一律 agent 仓。显式传 `subject` 与 `actor` 不一致
（例如 `actor=zcode, subject=human`）会被拒收——分仓语义不许被归因随手改。

**机器仓内跨宿主互相可见**（ZCode 踩的坑 DSH 干活前该看到）；`human` 仓不会漏进代理决策上下文。

## 5. 断点续收与 promote

- 游标文件默认 `<file>.cursor`，内容是**已处理到的行号**（含被拒的行）。
  同一文件重跑 ⇒ 零新增；被拒的行由返回值的 `errors` 明示，改完想重试就删游标或换新文件。
- 摄入结束**必须 promote**（`--no-promote` 仅供调试）：写回不巩固，检索看不见——
  这条是夜间量化踩过的写读耦合（`promoted_until` 曾落后 1527 条）。

## 6. 去重合并（v5）

去重键 = `sha1(subject | kind | normalize(obs) | normalize(target) | outcome)`：

- 同主体、同 kind、同 obs、同 target、同 outcome ⇒ **不新增行**，原行 `recurrences+1`
  并刷新 `result` / `verify`，首次时间留在 `verify.cred.first_t`；
- `outcome` 进键 ⇒ 同一事件先 `failed` 后 `success` 是**新信息**（两行各自成立）；
- 归一化后相同（全角/大小写/多余空白）算同一条；
- `subject` 进键 ⇒ 两个仓永不合并。

## 7. 示例（可直接被 `record-lessons` 吃进去）

见 `D:\agentctl-training\_派单_DSH\v5_证据\lessons_50.jsonl`（50 条，全部 anchor 已校验）。

最后更新：2026-10-29 19:10
