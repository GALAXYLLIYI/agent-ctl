# -*- coding: utf-8 -*-
r"""agentctl.memory.embedder —— 记忆层 v0 的本地确定性文本向量（记忆层刀 · 2026-10-26）

口径（总单 §四）：
    NAME = "hash-ngram-zh-v1"
    分词：contract.normalize_text 后，CJK 连续字符取二元组 + 拉丁/数字词；
    哈希：hashlib.md5(token)[:8] 取整 mod EMBED_DIM —— **禁用 Python hash()**
          （每进程随机化，落库向量将不可比）；
    词频累计后 L2 归一化；空文本 ⇒ 全零向量。

纯本地、无第三方模型、无网络。向量只在本模块这一处定义口径，
store / retrieve / compact 全部复用同一份 tokenize/embed。
"""
from __future__ import annotations

import hashlib
import math
import re

from . import contract

__all__ = ["NAME", "HashNGramEmbedder", "tokenize"]

NAME = "hash-ngram-zh-v1"

# 拉丁词（含数字串）。normalize_text 已小写，因此无需 A-Z。
_LATIN_WORD = re.compile(r"[a-z0-9]+")

# CJK 表意文字区段（基本区 + 扩展 A + 兼容区 + 扩展 B）。
_CJK_RANGES = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2FA1F),
)


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    for lo, hi in _CJK_RANGES:
        if lo <= code <= hi:
            return True
    return False


def tokenize(text: str) -> list[str]:
    """按 embed 口径切词，返回**带重复**的 token 列表。

    - CJK 连续段：相邻两字组成二元组（单字段落退化为该单字，避免完全丢词）；
    - 拉丁/数字连续段：整词；
    - 其余字符（标点、空白）只作分隔符。
    """
    s = contract.normalize_text(text)
    tokens: list[str] = []
    run: list[str] = []

    def flush_cjk() -> None:
        if len(run) == 1:
            tokens.append(run[0])
        elif len(run) >= 2:
            tokens.extend(run[i] + run[i + 1] for i in range(len(run) - 1))
        run.clear()

    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if _is_cjk(ch):
            run.append(ch)
            i += 1
            continue
        flush_cjk()
        m = _LATIN_WORD.match(s, i)
        if m:
            tokens.append(m.group(0))
            i = m.end()
            continue
        i += 1
    flush_cjk()
    return tokens


def _hash_token(token: str, dim: int) -> int:
    """md5 前 8 字节 → 无符号整数 → mod dim（跨进程稳定）。"""
    digest = hashlib.md5(token.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "little") % dim


class HashNGramEmbedder:
    """确定性 hash-ngram 向量器；dim 与 BLOB 口径一律来自 contract。"""

    NAME = NAME
    dim = contract.EMBED_DIM

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in tokenize(text):
            vec[_hash_token(token, self.dim)] += 1.0
        norm = math.sqrt(sum(x * x for x in vec))
        if norm < 1e-12:
            return vec
        return [x / norm for x in vec]
