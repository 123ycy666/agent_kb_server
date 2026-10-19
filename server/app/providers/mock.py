"""Mock 档 —— 零依赖的确定性假实现。

⭐ 三条硬约束（违反任何一条，后续开发都会踩坑）
--------------------------------------------------
1. **确定性**：相同文本必须得到相同向量。
   原因：若不确定，检索结果不可复现，测试会 flaky，
        冲突检测的相似度分数也无法作为判定依据。

2. **语义相关性**：Mock 向量必须让「相似文本 → 相似向量」成立。
   原因：若用纯随机向量，演示时检索结果完全随机，
        冲突检测 UI 永远看不到合理数据，等于盲写。
   实现：采用 Hashing Trick（特征哈希），把文本的 token 映射到向量维度。
        这样「报销制度」与「报销标准」会因为共享 token 而天然接近。

3. **MockLLM 必须能返回冲突样例**。
   原因：冲突决策弹窗需要真实数据才能开发与验证。
   实现：解析 prompt 中的结构化标记，用启发式规则给出关系分类。
        注意 —— 真实 LLM 使用**完全相同**的 prompt 模板，
        因此 Mock 与真实实现的行为差异仅在于「判定质量」，而非「接口形态」。
"""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Iterator

from app.core.text import extract_numbers, tokenize
from app.providers.base import (
    MARK_END,
    MARK_NEW,
    MARK_OLD,
    EmbeddingProvider,
    LLMProvider,
    Message,
    ProviderHealth,
    RerankProvider,
    ScoredDoc,
)

# ══════════════════════════════════════════════════════════════
#  哈希工具
# ══════════════════════════════════════════════════════════════
#
# ⚠️ 分词器（tokenize）已抽到 app/core/text.py。
#    原因：关键词检索路必须与向量化路使用**同一套分词**，
#    否则入库时的索引 token 与检索时的查询 token 对不上，
#    关键词检索会静默失效（不报错，只是永远召回不到）。
#    此处 re-export 保持向后兼容。


def _token_hash(token: str) -> int:
    """token → 64 位无符号整数，稳定且跨进程一致。"""
    return int.from_bytes(hashlib.md5(token.encode("utf-8")).digest()[:8], "big", signed=False)


# ══════════════════════════════════════════════════════════════
#  Mock Embedding
# ══════════════════════════════════════════════════════════════


class MockEmbeddingProvider(EmbeddingProvider):
    """基于 Hashing Trick 的确定性伪向量。

    相似文本会得到高余弦相似度，可支撑检索与冲突检测的功能演示。
    """

    def __init__(self, dim: int = 1024, model_id: str | None = None) -> None:
        self.dim = dim
        self.model_id = model_id or f"mock-embedding-{dim}d"

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = tokenize(text)

        if not tokens:
            # 空文本兜底：返回一个确定性的单位向量，避免全零导致 NaN
            idx = _token_hash("__kb_empty__") % self.dim
            vec[idx] = 1.0
            return vec

        for tok in tokens:
            h = _token_hash(tok)
            idx = h % self.dim
            # 用最高位决定符号，减少哈希碰撞带来的系统性偏置
            sign = 1.0 if (h >> 63) & 1 == 0 else -1.0
            vec[idx] += sign

        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            idx = _token_hash(text) % self.dim
            vec[idx] = 1.0
            return vec
        return [v / norm for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def health_check(self) -> ProviderHealth:
        start = time.perf_counter()
        self.embed(["健康检查"])
        cost = int((time.perf_counter() - start) * 1000)
        return ProviderHealth(
            ok=True,
            provider="mock",
            model=self.model_id,
            message=f"Mock 档位运行中，{self.dim} 维确定性向量，无需外部依赖",
            latency_ms=cost,
            extra={"dim": self.dim},
        )


# ══════════════════════════════════════════════════════════════
#  Mock Rerank
# ══════════════════════════════════════════════════════════════


class MockRerankProvider(RerankProvider):
    """基于 token 重合度的精排。

    保证「越像分越高」这一直觉成立，使精排环节的上下游逻辑可被验证。
    """

    def __init__(self, model_id: str = "mock-rerank") -> None:
        self.model_id = model_id

    @staticmethod
    def _score(query_tokens: set[str], doc: str) -> float:
        doc_tokens = tokenize(doc)
        if not doc_tokens or not query_tokens:
            return 0.0
        doc_set = set(doc_tokens)
        inter = len(query_tokens & doc_set)
        if inter == 0:
            return 0.0
        jaccard = inter / len(query_tokens | doc_set)
        coverage = inter / len(query_tokens)
        # 覆盖率权重更高：长文档不应因篇幅大而被 Jaccard 压低
        return round(0.35 * jaccard + 0.65 * coverage, 6)

    def rerank(
        self, query: str, docs: list[str], top_k: int | None = None
    ) -> list[ScoredDoc]:
        q = set(tokenize(query))
        scored = [
            ScoredDoc(index=i, text=d, score=self._score(q, d)) for i, d in enumerate(docs)
        ]
        scored.sort(key=lambda s: (-s.score, s.index))
        return scored[:top_k] if top_k is not None else scored

    def health_check(self) -> ProviderHealth:
        start = time.perf_counter()
        self.rerank("报销标准", ["报销标准是 500 元", "天气不错"])
        cost = int((time.perf_counter() - start) * 1000)
        return ProviderHealth(
            ok=True,
            provider="mock",
            model=self.model_id,
            message="Mock 精排运行中，基于 token 重合度打分",
            latency_ms=cost,
        )


# ══════════════════════════════════════════════════════════════
#  Mock LLM
# ══════════════════════════════════════════════════════════════

# 标记符定义见 providers/base.py，与真实实现共用同一套协议


class MockLLMProvider(LLMProvider):
    """模板 + 启发式规则的假 LLM。

    在 json_mode（冲突判定场景）下，会解析 prompt 中的
    【新文本】/【库内文本】标记，用数值比对 + 文本重合度给出关系分类，
    使冲突决策界面能看到合理数据。
    """

    def __init__(self, model_id: str = "mock-llm") -> None:
        self.model_id = model_id

    # ── 启发式冲突判定 ────────────────────────────────────────

    @staticmethod
    def _extract(prompt: str, mark: str) -> str:
        """从 prompt 中提取被 mark / MARK_END 包裹的内容。

        用显式结束标记而不是空行来界定边界 ——
        切片正文里可能包含任意换行与符号，靠空行分割一定会错位。
        """
        start = prompt.find(mark)
        if start < 0:
            return ""
        start += len(mark)

        end = prompt.find(MARK_END, start)
        if end < 0:
            end = len(prompt)
        return prompt[start:end].strip()

    @staticmethod
    def _numbers(text: str) -> set[str]:
        return extract_numbers(text)

    def _judge(self, prompt: str) -> dict[str, object]:
        """启发式关系分类。

        规则（从强到弱）：
        1. 两边都有数值且数值集合不同 → contradiction（事实型）
        2. 相似度极高且数值一致 → duplication
        3. 相似度中等，一边包含另一边 → entailment
        4. 否则 → unrelated
        """
        new_text = self._extract(prompt, MARK_NEW)
        old_text = self._extract(prompt, MARK_OLD)

        if not new_text or not old_text:
            return {
                "relation": "unrelated",
                "conflict_type": None,
                "confidence": 0.0,
                "reason": "未能从输入中解析到待比对文本",
                "severity": "low",
            }

        nt, ot = set(tokenize(new_text)), set(tokenize(old_text))
        inter = len(nt & ot)
        union = len(nt | ot) or 1
        similarity = inter / union

        n_new, n_old = self._numbers(new_text), self._numbers(old_text)

        # 规则 1：数值冲突
        if n_new and n_old and n_new != n_old and similarity >= 0.15:
            differ = sorted(n_old - n_new)
            return {
                "relation": "contradiction",
                "conflict_type": "fact",
                "confidence": round(min(0.95, 0.5 + similarity), 2),
                "reason": (
                    f"两段文本讨论同一事项但数值不一致："
                    f"库内为 {', '.join(differ) or '—'}，新文本为 {', '.join(sorted(n_new)) or '—'}"
                ),
                "severity": "high",
                "old_value": ", ".join(differ),
                "new_value": ", ".join(sorted(n_new)),
            }

        # 规则 2：几乎完全一致
        if similarity >= 0.85:
            return {
                "relation": "duplication",
                "conflict_type": "wording",
                "confidence": round(similarity, 2),
                "reason": "两段文本高度一致，属于重复内容",
                "severity": "low",
            }

        # 规则 3：一方包含另一方
        if similarity >= 0.4:
            longer, shorter = (nt, ot) if len(nt) >= len(ot) else (ot, nt)
            if len(longer & shorter) / (len(shorter) or 1) >= 0.8:
                return {
                    "relation": "entailment",
                    "conflict_type": "wording",
                    "confidence": round(similarity, 2),
                    "reason": "新文本是库内文本的细化或补充，不构成冲突",
                    "severity": "low",
                }
            return {
                "relation": "wording",
                "conflict_type": "wording",
                "confidence": round(similarity, 2),
                "reason": "两段文本表述不同但主题相近，建议人工确认",
                "severity": "medium",
            }

        return {
            "relation": "unrelated",
            "conflict_type": None,
            "confidence": round(1 - similarity, 2),
            "reason": "两段文本主题不相关",
            "severity": "low",
        }

    # ── 对外接口 ─────────────────────────────────────────────

    def chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        prompt = "\n".join(m.content for m in messages)

        if json_mode:
            import json

            return json.dumps(self._judge(prompt), ensure_ascii=False)

        # 非结构化：返回带引用的模板化回答
        question = messages[-1].content if messages else ""
        snippet = prompt[:0]
        ctx_count = prompt.count("【片段")
        lines = [
            "【Mock 档位生成】",
            f"针对问题「{question.strip()[:60]}」，系统已检索到 {ctx_count} 条相关片段。",
        ]
        if ctx_count:
            lines.append("以下为基于检索片段整理的回答（接入真实模型后将替换为生成式回答）：")
            lines.append("— 相关条款请参见下方引用的来源文件与对应片段。")
        else:
            lines.append("未检索到相关片段，无法回答该问题。")
        lines.append("")
        lines.append("⚠️ 当前为 Mock 档位，回答内容仅用于验证链路连通性。")
        return "\n".join(lines) + snippet

    def chat_stream(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        text = self.chat(messages, temperature=temperature, max_tokens=max_tokens)
        # 按小片段吐出，模拟流式效果
        step = 12
        for i in range(0, len(text), step):
            yield text[i : i + step]

    def health_check(self) -> ProviderHealth:
        start = time.perf_counter()
        self.chat([Message(role="user", content="你好")])
        cost = int((time.perf_counter() - start) * 1000)
        return ProviderHealth(
            ok=True,
            provider="mock",
            model=self.model_id,
            message="Mock LLM 运行中，支持结构化输出（冲突判定）",
            latency_ms=cost,
        )
