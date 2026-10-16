"""中英文混合分词与检索文本生成。

为什么需要它
------------
本项目需要**关键词检索路**来补向量检索的短板：
向量对数值、条款编号、专有名词极不敏感，而这些恰恰是冲突最常发生的地方
（如「报销上限 500」vs「800」）。

⚠️ 关键约束：pgvector 官方镜像**不含 pg_bigm 扩展**，而 PostgreSQL 内置的
   tsvector 又没有中文分词器（没有 zhparser / pg_jieba 时，
   `to_tsvector('simple', '报销标准是500元')` 会把整句当成一个 token，完全无效）。

因此采用：**应用层分词 → 空格分隔的 token 串 → to_tsvector('simple', ...)**
这样既不依赖任何 PG 中文插件，又能得到可用的中文关键词索引。

分词策略
--------
- 中文：单字 + 二元组（bigram）。二元组能抓住「报销」「标准」这类复合词
- 英文/数字：按词切分
- 停用词过滤，减少索引体积
- 数值单独保留（冲突检测的关键信号）
"""

from __future__ import annotations

import re
import unicodedata

_EN_WORD = re.compile(r"[a-z0-9_]+")
_CN_CHAR = re.compile(r"[\u4e00-\u9fff]")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")

_STOPWORDS = frozenset(
    {
        # 中文虚词
        "的", "了", "是", "在", "和", "与", "或", "有", "被", "把", "为", "对",
        "这", "那", "就", "都", "而", "及", "等", "之", "其", "以", "于", "并",
        # 英文虚词
        "the", "a", "an", "of", "to", "in", "is", "are", "and", "or", "for",
        "on", "at", "by", "with", "as", "be", "this", "that",
    }
)


def normalize(text: str) -> str:
    """全角转半角 + 小写化，保证「ＡＢＣ」与「abc」产生相同 token。"""
    return unicodedata.normalize("NFKC", text).lower()


def tokenize(text: str) -> list[str]:
    """中英文混合分词。返回 token 列表（保持出现顺序，不去重）。"""
    if not text:
        return []

    lowered = normalize(text)
    tokens: list[str] = []

    # 英文 / 数字词
    tokens.extend(w for w in _EN_WORD.findall(lowered) if w not in _STOPWORDS)

    # 中文单字与二元组
    cn_chars = _CN_CHAR.findall(lowered)
    tokens.extend(c for c in cn_chars if c not in _STOPWORDS)
    tokens.extend(cn_chars[i] + cn_chars[i + 1] for i in range(len(cn_chars) - 1))

    # 数值单独补一遍（数字在 _EN_WORD 里已被捕获，这里保证小数、百分比不漏）
    tokens.extend(_NUMBER.findall(lowered))

    return tokens


def to_search_text(text: str) -> str:
    """生成用于 `to_tsvector('simple', ...)` 的 token 串。

    token 之间用空格分隔；去重以减少 tsvector 体积（词频对 ts_rank_cd 影响有限，
    而中文二元组的重复率很高）。
    """
    tokens = tokenize(text)
    if not tokens:
        return ""
    # 保序去重
    seen: set[str] = set()
    unique: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return " ".join(unique)


def to_query_text(query: str) -> str:
    """生成用于 `plainto_tsquery('simple', ...)` 的查询串（AND 语义）。

    ⚠️ 注意：`plainto_tsquery` 生成的是 AND 语义，要求所有词全部命中。
       中文提问通常包含「是多少」「怎么办」这类文档中不存在的词，
       用它做**召回**会直接把结果归零。召回请用 `to_tsquery_or`。
    """
    return to_search_text(query)


def to_tsquery_or(query: str, max_terms: int = 40) -> str:
    """把查询转成 tsquery 的 OR 表达式，用于关键词路召回。

    ⚠️ 为什么必须用 OR
    ------------------
    `plainto_tsquery` 是 AND 语义，要求**每个** token 都命中。
    中文提问里必然混有「是多少」「怎么办」这类文档中不存在的词，
    AND 会让召回率直接归零 —— 而且不报错，
    表现为「关键词路永远返回空」，属于最难排查的一类静默故障。

    改用 OR 后：命中部分词即可召回，再由 `ts_rank_cd` 按命中程度排序，
    既保住召回率，又能区分相关度。

    ⚠️ 同时做字符白名单过滤：`! & | ( ) : *` 是 tsquery 的元字符，
       混入会直接抛语法错误导致整个检索请求 500。
    """
    tokens = tokenize(query)
    unique: list[str] = []
    for tok in tokens:
        safe = re.sub(r"[^a-z0-9_\u4e00-\u9fff]", "", tok)
        if safe and safe not in unique:
            unique.append(safe)
        if len(unique) >= max_terms:
            break
    return " | ".join(unique)


def extract_numbers(text: str) -> set[str]:
    """提取文本中的数字（含小数），供冲突判定做数值比对。"""
    return set(_NUMBER.findall(normalize(text)))
