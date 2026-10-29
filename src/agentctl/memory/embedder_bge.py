# -*- coding: utf-8 -*-
r"""agentctl.memory.embedder_bge —— 语义嵌入器（v6 · 2026-10-30）

口径：
    NAME = "bge-small-zh-v1.5/512"（BAAI/bge-small-zh-v1.5，512 维）
    查询侧必须带模型卡规定的指令前缀（BGE_QUERY_PREFIX）——评估实测缺前缀会掉分；
    语料侧不加前缀（对称 asymmetric 检索：短查询→长文档）。
    device 固定 CPU（显存留给训练/视频——用户铁律）；bge-small CPU 单条 ~10ms。

离线纪律：
    模型解析顺序：显式 path 参数 → AGENTCTL_BGE_MODEL 环境变量 → HF 缓存
    （local_files_only=True，不碰网络）。找不到 → RuntimeError 响亮报错，
    绝不静默回退到哈希（口径混写比检索差更危险）。

确定性：同一模型同一文本同一版本 ⇒ 同一向量（float32 落库）。
"""
from __future__ import annotations

import os

from . import contract

__all__ = ["BGE_QUERY_PREFIX", "BGE_NAME", "BgeEmbedder"]

BGE_NAME = "bge-small-zh-v1.5/512"
BGE_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："



class BgeEmbedder:
    """bge-small-zh 语义嵌入器；dim 与 BLOB 口径一律来自 contract（512）。"""

    NAME = BGE_NAME
    dim = contract.EMBED_DIM

    def __init__(self, model_path: str | None = None, device: str = "cpu"):
        self._model_path = model_path or os.environ.get("AGENTCTL_BGE_MODEL") or None
        self._device = device
        self._model = None

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        os.environ.setdefault("HF_HUB_OFFLINE", "1")     # 离线纪律：不碰网络
        from sentence_transformers import SentenceTransformer  # 迟导入：不拖累轻量路径

        path = self._model_path
        if path is None:
            # 从 HF 缓存解析（local_files_only；缓存没有就响亮报错）
            from huggingface_hub import snapshot_download
            try:
                path = snapshot_download(
                    "BAAI/bge-small-zh-v1.5", local_files_only=True
                )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    "BgeEmbedder 找不到本地模型：设 AGENTCTL_BGE_MODEL 指向 bge-small-zh-v1.5 "
                    "快照目录，或先用 HF_HOME 指向含该模型的缓存。原错误: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
        self._model = SentenceTransformer(path, device=self._device)
        get_dim = getattr(self._model, "get_embedding_dimension", None) or \
            getattr(self._model, "get_sentence_embedding_dimension", None)
        dim = int(get_dim())
        if dim != self.dim:
            raise RuntimeError(
                f"模型输出维度 {dim} 与 contract.EMBED_DIM {self.dim} 不一致——"
                "换模型必须同步升 contract 口径（这不是可静默的差异）"
            )
        return self._model

    def embed(self, text: str) -> list[float]:
        model = self._ensure_model()
        vec = model.encode(text if text else " ", convert_to_numpy=True).tolist()
        return [float(x) for x in vec]

    def embed_query(self, text: str) -> list[float]:
        """查询侧编码：带指令前缀（模型卡规定）。"""
        return self.embed(BGE_QUERY_PREFIX + (text or ""))

    def embed_batch(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        model = self._ensure_model()
        safe = [t if t else " " for t in texts]
        out = model.encode(safe, batch_size=batch_size,
                           convert_to_numpy=True, show_progress_bar=False)
        return [[float(x) for x in vec] for vec in out]
