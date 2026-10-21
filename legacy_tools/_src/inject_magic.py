# -*- coding: utf-8 -*-
r"""inject_magic.py —— **agent 注入的专属魔数**（M4，2026-10-22）

一句话：**每一次 agent 注入都在 `dwExtraInfo` 里带上同一个魔数**
⇒ 归因层可以「一票确定这条输入是 agent 做的」，不必靠「时间邻近」硬猜。

★ 为什么是**单独一个模块**（而不是把数字直接写在 act.py 里）：
  注入方（`act.py`）与判定方（判据 / 将来的归因层 `src/agentctl/attribution.py`）
  **必须用同一个数**。各写一个 ⇒ 今天 0xA6E17、明天 0xA6E18 ⇒ 归因静默失效
  （而且「归因说不是 agent，但其实是 agent」是最坏的一类错）。
  ⇒ **单一真源**：定义只在这里一处，两边都 import 它。

★ 为什么选 0xA6E17：
  · 不是任何 Win32 保留值/常见 API tag（0xA6E17 = 682,519，不是常见的 0xFFFF/0xFFFFFFFF 类）；
  · 非 0 ⇒ 与「没带标记」的注入（dwExtraInfo = NULL = 0）**天然可区分**；
  · 单次 `SendInput` 只会填一个 ULONG_PTR ⇒ 32 位/64 位都放得下。

★ 边界（如实写清，不许含糊）：
  · 魔数只能证明「**这一条注入是 agent 发的**」，**不能**证明「这条输入是用户要求的」；
  · 别的程序若**故意**用同一个魔数注入，本机无法区分（魔数不是密码，是标记）；
  · 物理键盘/鼠标输入的 dwExtraInfo 恒为 0 ⇒ **永远不会**被误认成 agent。

用法：
    import inject_magic
    inp = KEYBDINPUT(vk, 0, flags, 0, inject_magic.INJECT_MAGIC)
"""

#: agent 注入的 dwExtraInfo 魔数（**全仓唯一定义处**）
INJECT_MAGIC = 0xA6E17
