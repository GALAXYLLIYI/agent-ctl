# 反哺引擎接入指南（feedback engine v1 · 2026-10-30）

> 组件：`agentctl/memory/feedback.py::FeedbackEngine`
> 判据：P5 ⑲ 组（A/B 交替 / 回灌合并 / 写读闭环 / 自动巩固 / 兄弟结局 / fail-open / 注入咬合）
> 第一个适配客户端：night_loop_v2（量化循环，diff 见其 git 历史）

## 设计律（为什么是结构不是 prompt）

「先查记忆」不进 prompt（号召=建议，可被微调覆盖——DSH P0-2 实证），进 harness
（结构=义务）。理论根据=Tulving 编码特异性：提取失败多是线索不在场，线索供给是
环境设计的责任。与 gate 放 serving 层同款理由。

## 三行接线（Python 宿主：循环/批处理/服务）

```python
from agentctl.memory import open_store
from agentctl.memory.feedback import FeedbackEngine

fb = FeedbackEngine(open_store(), session_id="s-your-agent", actor="your-actor",
                    ab_cycle=(2, 2), promote_every=10)

# 循环头（义务）：结构化召回，命中进决策上下文
ctx = fb.begin_round(); ctx = fb.before_task("任务名", "查询词（语义，不是函数名）")
if ctx["block"]:
    print(ctx["block"])          # 已含出处/结局/recurrences/actor

# 任务尾（义务）：去重回灌 + 每 promote_every 条自动巩固
fb.after_task("任务名", kind="type", obs="发生了什么", outcome="success",
              result="结论/教训", verify_cred={"file": "哪份报告"})
```

口径全部内嵌（不许调用方自选）：subject 分仓（默认 agent 仓）、分数地板 0.10、
展示去重（含兄弟结局）、饥饿豁免（排除集全压时最高分放行并标 shown_before）、
dedup_key 合并（subject+outcome 入键）、promote 收尾、fail-open 双层
（召回/回灌任何失败降级为空结果+标记，绝不拦业务）。

## MCP/HTTP 宿主（Codex/DSH/ZCode 等）

服务重启后可直接用 ctl_memory（契约 v6+）：
- `{"action": "recall", "text": "...", "k": 3}` → 结构化召回（默认 agent 仓），
  返回 block 可直接出示；`subject: "human"` 可查人的记忆仓（只读）
- `{"action": "query", "text": "...", "subject": "agent|human"}` → 全量检索
- 回灌暂无工具面（拉取式铁律）——宿主写经验走 CLI：
  `python -m agentctl.memory record-lessons <jsonl>`（教训格式见 docs/lessons_jsonl_v1.md，
  success 必须带出处 file）

## 参数

| 参数 | 默认 | 含义 |
|---|---|---|
| ab_cycle | (2, 2) | 轮次级 A/B：N 轮对照 + M 轮召回交替；(0, M)=全召回 |
| disabled | RECALL_OFF 环境变量 | 总开关；对照轮留痕于账本 |
| floor | 0.10 | 召回分数地板（滤零分伪影） |
| shown_max | 60 | 排除窗（最近已出示 id），配饥饿豁免 |
| promote_every | 10 | 每 N 条写入自动 promote（写读耦合治标） |
| actor | None | 归因词表（zcode/codex/dsh/dayloop/nightloop/human/unknown） |

## 账本与对账

`fb.stats()` → {round, recall_rounds, control_rounds, writes, written_tasks,
shown_buffer, disabled}。历史在 `fb.history`。A/B 分析脚本读循环日志的
[召回开]/[对照关] 标记（day loop 每轮留痕）。

## 纪律

1. before_task 的查询词用**事件语义**（中文关键词），不是函数名（哈希/嵌入都搜不中）。
2. after_task 的 outcome 用三值（success/failed/unobservable）——「没测出来」不许记成功。
3. verify_cred 尽量带 file/anchor——无出处的经验可信度减半。
4. 不许在 after_task 里改写历史 episode 的结局；新信息=新条目或 dedup 合并刷新结论。
