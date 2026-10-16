"""冲突检测引擎。

流程（对应设计方案 10.2）
------------------------
① 候选召回：新文档每个 chunk → 向量检索 Top-K（限定**当前主体可见**的文档）
② 精排    ：rerank 对 (新chunk, 库内chunk) 打分，取阈值以上
③ LLM 判定：结构化输出关系分类（NLI 任务）
④ 文档级聚合 → 生成冲突报告

⚠️ 五个必须避开的坑
1. 绝不让 LLM 凭空判断冲突 —— LLM 不知道库里有什么，必然幻觉。必须先检索再判定。
2. 冲突 ≠ 相似 —— 相似度高可能只是重复/一致，必须做关系分类。
3. 复杂度必须控制 —— 只取 Top-K 候选，O(N×K) 而非 O(N×M)。
4. 关系类型要在 Prompt 中写死，尤其区分「时效型更新」与「真冲突」。
5. LLM 输出必须结构化 + 低温度，否则判定不稳定。

权限边界（严格方案）
-------------------
候选集 = 当前主体可见的文档。受限文档**完全不参与**检测与提示，
因此不存在「通过上传探测受限文档」的侧信道泄露。
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select, true
from sqlalchemy.orm import Session, aliased

from app.core.config import settings
from app.core.deps import CurrentActor
from app.models import ConflictRecord, Document, DocumentChunk
from app.providers import LLMProvider, Message, get_providers
from app.providers.base import MARK_END, MARK_NEW, MARK_OLD
from app.services.permission import SCOPE_SEARCH, visible_document_filter

logger = logging.getLogger(__name__)

# 每个新切片召回的候选数
CANDIDATE_TOP_K = 5

# 冲突判定的并发度 —— 已抽到 settings.conflict_judge_concurrency（2026-10-15）。
#
# 背景：原先的判定是**串行** for 循环，12 次 LLM 调用 × 每次 8~20s ≈ 170s，
# 用户体感「上传没有异步」（其实接口 0.02s 就返回了，慢的是这个后台步骤）。
# 判定彼此独立、只依赖外部 LLM，因此可以并发：总耗时 ≈ 最慢的那一次。
# ⚠️ 不要调太高：在线 LLM 有限流，默认 8 兼顾提速与稳定（见 .env）。


# 切片的「实质内容」最小长度（去掉标题路径后计算）。
# 形如「【差旅报销】」的纯标题碎片没有事实主张，不应参与冲突比对。
MIN_CLAIM_CHARS = 12

_HEADING_PREFIX = re.compile(r"^【[^】]*】")


def _has_substance(text: str) -> bool:
    """切片是否含有可比较的实质内容。

    ⚠️ 必须过滤纯标题碎片。它们会带来两个问题：
       ① 噪声候选挤占精排名额，把真正的冲突对挤掉；
       ② 标题与「含标题的正文」之间相似度虚高，产生假候选。
    """
    body = _HEADING_PREFIX.sub("", (text or "").strip(), count=1).strip()
    return len(body) >= MIN_CLAIM_CHARS


def _thresholds() -> tuple[float, float, float]:
    """返回 (最低向量相似度, 最低精排分数, 高相似度直通阈值)。

    ⚠️ 阈值必须随档位调整，不能一刀切。
       mock 档的向量由 Hashing Trick 生成，相似度绝对值普遍偏低
       （真实语义模型下相似度 0.6 的内容，mock 下可能只有 0.3）。
       若沿用真实模型的阈值，会把全部候选过滤掉，冲突检测等于失效 ——
       这种「功能静默不工作」比报错更难排查。

    ⚠️ 第三个值是**召回兜底**：精排模型在「长查询 vs 短文档」这类组合上
       打分并不稳定，实测曾把向量相似度 0.98 的真冲突对打到 0.35 以下。
       因此对「向量相似度极高」的候选直接放行，避免精排造成漏报。
    """
    if get_providers().mode == "mock":
        return 0.08, 0.08, 0.55
    return 0.30, 0.35, 0.90

# 标记符定义见 providers/base.py（带显式结束符，避免与切片正文撞车）

JUDGE_SYSTEM_PROMPT = """你是一名企业知识库的内容一致性审查员。你的任务是判断两段文本之间是否存在**实质性冲突**。

请严格区分以下四种关系：

1. contradiction（真冲突）：两段文本讨论**同一事项**，但事实、数值或规则**互相矛盾**。
   例如：「报销上限 500 元」与「报销上限 800 元」。
2. entailment（蕴含/细化）：新文本是库内文本的细化、补充或无害的表述变化，**不构成矛盾**。
3. duplication（重复）：两段文本内容基本一致。
4. unrelated（无关）：主题不同，不构成任何关系。

特别提醒：
- **时效型更新不算冲突**：如果新文本只是在陈述「自某日期起，规则由 A 变为 B」，
  而库内文档是变更前的旧版，这属于正常的版本演进，应判为 unrelated，并在 reason 中说明。
- 不要因为用词不同就判定为冲突，要聚焦**事实与数值**是否矛盾。

必须严格输出以下 JSON 结构，不要输出任何其他内容：
{
  "relation": "contradiction | entailment | duplication | unrelated",
  "conflict_type": "fact | rule | temporal | wording | null",
  "confidence": 0.0-1.0 之间的小数,
  "reason": "用一句中文说明判定理由，若为冲突需指出具体矛盾点",
  "severity": "high | medium | low",
  "new_value": "冲突中体现的新值（无则 null）",
  "old_value": "冲突中体现的旧值（无则 null）"
}"""


@dataclass
class ConflictCandidate:
    new_chunk_id: int
    new_text: str
    old_chunk_id: int
    old_doc_id: int
    old_doc_name: str
    old_doc_version: int
    old_text: str
    vector_similarity: float
    rerank_score: float | None = None


@dataclass
class DetectResult:
    doc_id: int
    candidate_pairs: int = 0
    conflicts: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    llm_calls: int = 0

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicts)


def _call_judge(llm: LLMProvider, new_text: str, old_text: str) -> dict:
    """调用 LLM 做关系判定，返回结构化结果。"""
    user_prompt = (
        f"{MARK_NEW}\n{new_text}\n{MARK_END}\n\n"
        f"{MARK_OLD}\n{old_text}\n{MARK_END}\n\n"
        "请判断这两段文本的关系，并按要求输出 JSON。"
    )
    raw = llm.chat(
        [
            Message(role="system", content=JUDGE_SYSTEM_PROMPT),
            Message(role="user", content=user_prompt),
        ],
        temperature=0.0,
        json_mode=True,
    )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # LLM 偶尔会包裹 ```json``` 代码块，做一次兜底提取
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}

    relation = str(data.get("relation", "unrelated")).lower()
    if relation not in ("contradiction", "entailment", "duplication", "unrelated"):
        relation = "unrelated"

    conflict_type = data.get("conflict_type")
    if conflict_type in (None, "null", "None"):
        conflict_type = None

    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    return {
        "relation": relation,
        "conflict_type": conflict_type,
        "confidence": max(0.0, min(1.0, confidence)),
        "reason": str(data.get("reason") or "").strip() or "模型未给出理由",
        "severity": str(data.get("severity") or "low"),
        "new_value": data.get("new_value"),
        "old_value": data.get("old_value"),
    }


def _find_candidates(
    db: Session,
    *,
    actor: CurrentActor,
    new_doc: Document,
    limit_per_chunk: int = CANDIDATE_TOP_K,
    ignore_permission: bool = False,
) -> list[ConflictCandidate]:
    """为新文档的每个切片召回候选对比切片。

    ⚠️ 候选集默认限定在「当前主体可见的文档」—— 严格权限方案，
       避免通过反复上传探测到无权查看的内容（信息侧信道）。

    ignore_permission=True 仅供管理员「全库一致性扫描」使用：
       日常上传检测存在盲区 —— 「受限文档 与 公开文档」之间的矛盾
       永远不会被自动发现，需要管理者定期巡检补上。
       该路径必须由 system:scan 权限保护，且操作要进审计日志。
    """
    bundle = get_providers()
    model_name = f"{bundle.mode}/{bundle.embedding.model_id}"
    min_similarity, _, _ = _thresholds()

    # ⚠️ 2026-10-15 批量召回：原实现对**每个**新切片各发一条 SQL
    #    （1500 段 = 1500 次带 pgvector 排序的查询，累计几十秒）。
    #    改为一条 SQL + CROSS JOIN LATERAL，由 PostgreSQL 对每个新切片各取
    #    Top-K —— 语义与逐条查询完全一致，数据库往返从 N 次降到 1 次。
    nc = aliased(DocumentChunk, name="nc")
    oc = aliased(DocumentChunk, name="oc")
    od = aliased(Document, name="od")

    distance = oc.embedding.cosine_distance(nc.embedding)
    top_k = (
        select(
            oc.id.label("old_chunk_id"),
            oc.doc_id.label("old_doc_id"),
            od.filename.label("old_doc_name"),
            od.version_no.label("old_doc_version"),
            oc.content.label("old_text"),
            (1.0 - distance).label("similarity"),
        )
        .join(od, od.id == oc.doc_id)
        .where(
            oc.kb_id == nc.kb_id,
            oc.doc_id != nc.doc_id,                      # 排除自己
            od.is_active.is_(True),
            oc.embedding_model == model_name,
            oc.is_retrievable.is_(True),
            # ⚠️ 严格权限：只比对上传者可见的文档（全库巡检模式除外）。
            #    复用权限单一出口，仅把表对象换成别名 od —— 不另写 where 条件。
            *(
                []
                if ignore_permission
                else [visible_document_filter(actor, SCOPE_SEARCH, od)]
            ),
        )
        .order_by(distance)
        .limit(limit_per_chunk)
        .lateral("topk")
    )

    stmt = (
        select(
            nc.id.label("new_chunk_id"),
            nc.content.label("new_text"),
            top_k.c.old_chunk_id,
            top_k.c.old_doc_id,
            top_k.c.old_doc_name,
            top_k.c.old_doc_version,
            top_k.c.old_text,
            top_k.c.similarity,
        )
        .select_from(nc)
        .join(top_k, true())
        .where(nc.doc_id == new_doc.id, nc.embedding.is_not(None))
        .order_by(nc.chunk_index)
    )

    candidates: list[ConflictCandidate] = []
    for row in db.execute(stmt):
        # 纯标题碎片没有事实主张，不参与比对（新/旧任一侧是碎片都跳过）
        if not _has_substance(row.new_text) or not _has_substance(row.old_text):
            continue
        similarity = max(0.0, float(row.similarity or 0.0))
        if similarity < min_similarity:
            continue
        candidates.append(
            ConflictCandidate(
                new_chunk_id=row.new_chunk_id,
                new_text=row.new_text,
                old_chunk_id=row.old_chunk_id,
                old_doc_id=row.old_doc_id,
                old_doc_name=row.old_doc_name,
                old_doc_version=row.old_doc_version,
                old_text=row.old_text,
                vector_similarity=round(similarity, 4),
            )
        )

    return candidates


def _rerank_candidates(candidates: list[ConflictCandidate]) -> list[ConflictCandidate]:
    """按新切片分组做精排，筛掉明显不相关的候选以省下 LLM 调用。

    通过标准是**两条取或**：
        ① 精排分数达标 —— 精度保障（省成本的主力）
        ② 向量相似度极高 —— 召回兜底

    ⚠️ 只保留 ① 会漏报：实测精排模型对「长查询 vs 长文档」这类组合打分偏低，
       把向量相似度 0.98 的真冲突对（500 元 vs 1200 元）打到阈值以下，
       冲突检测因此静默返回 0 条。凡是「静默漏报」的环节都必须有兜底。
    """
    if not candidates:
        return []

    bundle = get_providers()
    _, rerank_threshold, high_similarity = _thresholds()

    # 按新切片分组，每组用其文本作为 query 对候选打分
    grouped: dict[int, list[ConflictCandidate]] = {}
    for c in candidates:
        grouped.setdefault(c.new_chunk_id, []).append(c)

    groups = list(grouped.values())

    # ⚠️ 组数上限（2026-10-15）：精排是「每组一次 HTTP 请求」，而候选组数没有
    #    天然上界。病态输入（整篇高度雷同的文档）会产生上千组，把外部 rerank
    #    服务打爆。超出时按**组内最高向量相似度**取前 N 组 —— 它们正是最可能
    #    冲突的候选；其余组即使精排了，也排在 max_llm_calls 之外不会被判定。
    if len(groups) > settings.conflict_rerank_max_groups:
        groups.sort(key=lambda g: -max(c.vector_similarity for c in g))
        logger.info(
            "冲突精排候选组过多（%s 组），仅精排前 %s 组",
            len(groups), settings.conflict_rerank_max_groups,
        )
        groups = groups[: settings.conflict_rerank_max_groups]

    def _rerank_one(group: list[ConflictCandidate]):
        """单组精排（子线程执行：只调外部服务，不碰 db）。"""
        try:
            scored = bundle.rerank.rerank(
                group[0].new_text, [c.old_text for c in group], top_k=len(group)
            )
            return group, scored, None
        except Exception as exc:  # noqa: BLE001
            return group, None, exc

    # ⚠️ 并发精排（2026-10-15）：原为串行 for 循环，大文档下组数可达数百，
    #    是仅次于向量化的第二大耗时源。各组彼此独立 ⇒ 可并发。
    #    map 保持组顺序，kept 的顺序与串行时一致（不改变判定结果）。
    workers = max(1, min(settings.conflict_rerank_concurrency, len(groups)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        outcomes = list(ex.map(_rerank_one, groups))

    kept: list[ConflictCandidate] = []
    for group, scored, err in outcomes:
        if err is not None:
            logger.warning("冲突候选精排失败，降级为全部送入判定：%s", err)
            kept.extend(group)
            continue
        for s in scored:
            if 0 <= s.index < len(group):
                item = group[s.index]
                item.rerank_score = round(s.score, 4)
                if s.score >= rerank_threshold or item.vector_similarity >= high_similarity:
                    kept.append(item)

    return kept


def detect_conflicts(
    db: Session,
    *,
    actor: CurrentActor,
    doc_id: int,
    persist: bool = True,
    ignore_permission: bool = False,
    max_llm_calls: int | None = None,
) -> DetectResult:
    """对指定文档执行冲突检测。

    ignore_permission=True 时不做可见性限制 —— 仅供管理员巡检使用，
    调用方必须自行做 system:scan 权限校验并写审计日志。

    max_llm_calls 是 LLM 判定的**上限**（安全阀）。上传后的自动检测在请求内
    同步执行，若不设上限，超长文档会触发几十次 LLM 调用，把上传接口拖到几十秒。
    超出上限时中止并给出警告，提示改用管理员全库扫描。
    """
    doc = db.get(Document, doc_id)
    if doc is None:
        raise ValueError(f"文档不存在：{doc_id}")

    result = DetectResult(doc_id=doc_id)

    candidates = _find_candidates(
        db, actor=actor, new_doc=doc, ignore_permission=ignore_permission
    )
    result.candidate_pairs = len(candidates)

    if not candidates:
        return result

    candidates = _rerank_candidates(candidates)
    if not candidates:
        return result

    # ⚠️ 取**判定专用** LLM（2026-10-15）：主 LLM 还要服务知识库问答与问数 agent，
    #    不能被判定这条链路的提速策略（换快模型）波及；未单独配置判定模型时回落主 LLM。
    bundle = get_providers()
    llm: LLMProvider = bundle.judge_llm or bundle.llm

    # ── 并发执行 LLM 判定（2026-10-15 优化）──────────────────────────
    # 原先这里是 `for cand in candidates: _call_judge(...)` 串行执行：
    # 12 次 × 每次 8~20s ≈ 170s，用户体感「上传没有异步」（其实接口 0.02s
    # 就返回了 202，慢的是这个后台步骤）。
    # 判定只依赖外部 LLM、彼此独立 ⇒ 可以并发；DB 写入仍留在主线程串行
    # （SQLAlchemy Session 不是线程安全的）。
    batch = candidates
    if max_llm_calls is not None and len(candidates) > max_llm_calls:
        # ⚠️ 必须按「可疑程度」取前 N，而不是按切片顺序截断 ——
        #    顺序截断会把相似度最高的真冲突对排到后面，直接漏判（静默漏报）。
        batch = sorted(
            candidates,
            key=lambda c: (
                -(c.rerank_score if c.rerank_score is not None else 0.0),
                -c.vector_similarity,
            ),
        )[:max_llm_calls]
        result.warnings.append(
            f"候选较多，本次只判定最可疑的 {max_llm_calls} 对；"
            "完整比对请由管理员执行「全库一致性扫描」"
        )

    def _judge_one(cand: ConflictCandidate):
        """单个候选的 LLM 判定（在子线程执行，不碰 db）。"""
        try:
            return cand, _call_judge(llm, cand.new_text, cand.old_text), None
        except Exception as exc:  # noqa: BLE001
            return cand, None, exc

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, min(settings.conflict_judge_concurrency, len(batch)))
    ) as ex:
        judged = list(ex.map(_judge_one, batch))   # map 保持与 batch 同序

    for cand, verdict, judge_err in judged:
        if judge_err is not None:
            logger.warning("冲突判定失败（chunk %s）：%s", cand.new_chunk_id, judge_err)
            result.warnings.append(f"部分片段判定失败：{judge_err}")
            continue

        result.llm_calls += 1

        # 只记录真正的矛盾；时效型/无关/重复不产生冲突记录
        if verdict["relation"] != "contradiction":
            continue
        if verdict["severity"] == "low":
            continue

        conflicts_payload = {
            "new_chunk_id": cand.new_chunk_id,
            "old_chunk_id": cand.old_chunk_id,
            "old_doc_id": cand.old_doc_id,
            "old_doc_name": cand.old_doc_name,
            "old_doc_version": cand.old_doc_version,
            "conflict_type": verdict["conflict_type"] or "fact",
            "relation": verdict["relation"],
            "similarity": cand.vector_similarity,
            "rerank_score": cand.rerank_score,
            "reason": verdict["reason"],
            "new_snippet": cand.new_text[:500],
            "old_snippet": cand.old_text[:500],
            "confidence": verdict["confidence"],
            "severity": verdict["severity"],
            "new_value": verdict.get("new_value"),
            "old_value": verdict.get("old_value"),
        }
        result.conflicts.append(conflicts_payload)

        if persist:
            db.add(
                ConflictRecord(
                    kb_id=doc.kb_id,
                    new_doc_id=doc.id,
                    old_doc_id=cand.old_doc_id,
                    new_chunk_id=cand.new_chunk_id,
                    old_chunk_id=cand.old_chunk_id,
                    conflict_type=verdict["conflict_type"] or "fact",
                    relation=verdict["relation"],
                    similarity_score=cand.vector_similarity,
                    rerank_score=cand.rerank_score,
                    reason=verdict["reason"],
                    new_snippet=cand.new_text[:2000],
                    old_snippet=cand.old_text[:2000],
                    status="pending",
                    created_at=datetime.now(UTC),
                )
            )

    if result.has_conflict and persist:
        # ⚠️ 无人值守策略：**只隔离冲突的那几片**，同文档其余切片照常可检索。
        #    旧实现给整篇文档打 has_conflict（检索里一票否决），一个切片冲突
        #    就让整份文档从检索里消失 —— 这正是「一小点废掉一大段」的根源。
        conflict_chunk_ids = {
            c["new_chunk_id"] for c in result.conflicts if c.get("new_chunk_id")
        }
        if conflict_chunk_ids:
            db.query(DocumentChunk).filter(
                DocumentChunk.id.in_(conflict_chunk_ids)
            ).update(
                {
                    DocumentChunk.is_retrievable: False,
                    DocumentChunk.retired_reason: "conflict",
                },
                synchronize_session=False,
            )
        # has_conflict 保留，但语义降级为「该文档存在待处理冲突」（供 UI 提示），
        # 不再参与检索过滤 —— 检索现在只看切片级 is_retrievable。
        doc.has_conflict = True

    if persist:
        db.commit()
        db.refresh(doc)

    return result
