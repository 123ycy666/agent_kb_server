# -*- coding: utf-8 -*-
"""问数智能体接口的响应契约（2026-10-15 新增）。

⚠️ 这份 schema 的作用是**把对外契约写清楚**（体现在 `/docs` 的 OpenAPI 里），
   而不是充当"过滤器"：路由返回的是 `Response` 对象，FastAPI 不会再拿它
   做序列化，所以这里的字段增删**不会改变实际输出**，是纯声明。

为什么要有它
------------
`/api/ask` 早期是"给自家前端用"的 UI 接口，直接返回内部函数的 dict，
没有契约约束。将来要把系统嵌进别的系统时，对方需要一个**稳定的字段说明**：
哪些字段必须依赖（answer / session_id）、哪些可以忽略（trace 是调试信息）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    """POST /api/ask 的请求体（除 question 外全部可选）。

    ⚠️ 路由目前用宽容解析（非法 JSON 也不报错），这份声明用于文档与
       将来的外部集成对齐；字段名有别名的地方以这里的为准。
    """

    question: str = Field(description="用户原话（必填）。不需要上游预处理，错字/简称/漏时间地点都能理解")
    session_id: str | None = Field(default=None, description="会话 id；首次不传由服务端生成，多轮对话需回传")
    kb_ids: list[int] | None = Field(
        default=None,
        description="本次检索的知识库 id 列表（问答页勾选）。不传则用「参与智能问答」标记的库。"
        "服务端会校验：不在可用集合内的会被过滤；全部无效则返回 403",
    )
    model: str | None = Field(default=None, description="指定模型 id；不传用默认（deepseek-v4.1-flash-1017）")
    profile: str | None = Field(default=None, description="用户画像（可选），与问题一起交给模型")
    today: str | None = Field(default=None, description="业务日期（YYYY-MM-DD）；不传按系统当天")
    scope: str | None = Field(default=None, description="业务范围（可选）")
    want_page: bool = Field(default=False, description="是否额外生成数据大屏 HTML")
    history_turns: int | None = Field(default=None, description="读取同一会话最近 N 轮作为上下文；0=不用上下文")
    uid: str | None = Field(default=None, description="业务用户标识（记录用）")
    channel: str | None = Field(default=None, description="来源渠道（web / api / …），记录用")


class AskResponse(BaseModel):
    """POST /api/ask 的响应。

    **外部集成只需依赖前两个字段**：`answer` 与 `session_id`；
    其余为过程与观测信息。
    """

    answer: str = Field(description="★ 回答正文（核心字段）")
    session_id: str = Field(description="★ 会话 id，多轮对话下次原样回传")
    input_question: str = Field(default="", description="用户原始提问")
    question: str = Field(default="", description="补全后的问题（已补时间/地点/口径）")
    completed_question: str | None = Field(default=None, description="补全参考文本（展示用）")
    user_name: str = Field(default="", description="提问者显示名")
    steps: int = Field(default=0, description="模型循环轮数")
    db_query_count: int = Field(default=0, description="查业务库次数")
    kb_query_count: int = Field(default=0, description="知识库检索次数")
    elapsed_ms: int = Field(default=0, description="总耗时（毫秒）")
    unverified: bool = Field(default=False, description="是否因证据不足而拒答（true=没查到，不是故障）")
    trace: list = Field(
        default_factory=list,
        description="⚠️ 工具调用轨迹，**仅调试用**；外部集成可忽略，也可要求不返回",
    )
