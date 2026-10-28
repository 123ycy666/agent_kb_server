# -*- coding: utf-8 -*-
"""预警与任务流转 · 请求体模型（进 /docs，字段即契约）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Part = Literal['owner', 'collaborator', 'cc']
Cadence = Literal['daily', 'weekly', 'monthly', 'yearly', 'all']


class AssigneeIn(BaseModel):
    employee_id: int = Field(..., description='员工 ID（t_employee.employee_id）')
    part: Part = Field('owner', description='owner=主责 / collaborator=协办 / cc=抄送')
    role_code: str | None = Field(None, description='可选；不传由服务端按账号解析')


class DispatchIn(BaseModel):
    assignees: list[AssigneeIn] = Field(..., min_length=1, description='至少一个主责')
    title: str | None = Field(None, max_length=200)
    requirement: str = Field('', description='任务要求（AI 草稿可改）')
    reply_due_at: str | None = Field(None, description='ISO 时间；不传按级别默认')
    due_at: str | None = Field(None, description='ISO 时间；不传按子判据默认')
    require_plan: int = Field(0, ge=0, le=1, description='是否必须先交方案')
    require_reply: int = Field(1, ge=0, le=1,
                               description='1=要在回执时限前交东西；0=不用交，系统盯数据判完成')
    verifier_type: Literal['auto', 'human'] = 'auto'
    verify_rule: dict[str, Any] = Field(default_factory=dict)
    task_type: Literal['alert', 'talk', 'review', 'manual'] = 'alert'
    level: Literal['high', 'mid', 'low'] | None = None
    idempotency_key: str | None = Field(None, max_length=64, description='重放返回同一任务')


class PlanIn(BaseModel):
    content: str = Field(..., min_length=1)
    items: list[Any] | None = None


class ReplyIn(BaseModel):
    content: str = Field(..., min_length=1)
    attachments: list[dict[str, Any]] | None = None
    attachment_ids: list[int] | None = Field(None, description='先调附件上传拿到的 id 列表')


class NoteIn(BaseModel):
    note: str = Field('', max_length=500)


class FanoutTargetIn(BaseModel):
    employee_id: int


class FanoutIn(BaseModel):
    targets: list[FanoutTargetIn] | None = Field(None, description='不传=自动按范围找全部直接下级')
    mode: Literal['per_target', 'joint'] = 'per_target'
    title_template: str | None = Field(None, max_length=200, description='可用 {scope} 占位')
    requirement: str | None = None
    due_at: str | None = None
    reply_due_at: str | None = None
    require_reply: int = Field(1, ge=0, le=1)
    require_plan: int = Field(0, ge=0, le=1)
    idempotency_key: str | None = Field(None, max_length=64)


class CommentIn(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000, description='说明：只留记录，不要求谁办')


class SupplementIn(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000,
                         description='补充要求：追加到任务要求里，会通知承办人')


class DeclineIn(BaseModel):
    reason: str = Field('', max_length=500)


class TransferIn(BaseModel):
    to_employee_id: int


class SubtaskIn(BaseModel):
    title: str | None = Field(None, max_length=200)
    requirement: str = ''
    assignees: list[AssigneeIn] = Field(..., min_length=1)
    reply_due_at: str | None = None
    due_at: str | None = None
    require_plan: int = Field(0, ge=0, le=1)
    require_reply: int = Field(1, ge=0, le=1)
    verifier_type: Literal['auto', 'human'] = 'human'
    verify_rule: dict[str, Any] = Field(default_factory=dict)


class CloseIn(BaseModel):
    conclusion: str = ''
    reason: str = 'human_confirmed'


class AdjustIn(BaseModel):
    """发起人改时限/要求（子任务会被夹在父任务窗口内）。"""
    due_at: str | None = Field(None, description='完成时限 ISO；子任务不得晚于父任务')
    reply_due_at: str | None = Field(None, description='回执时限 ISO；设了就按"要回执"处理')
    requirement: str | None = Field(None, max_length=2000)


class ExtendIn(BaseModel):
    """承办人申请延期：只留记录 + 通知发起人，不改时限。"""
    due_at: str | None = Field(None, description='希望延到什么时候（ISO）')
    reason: str = Field('', max_length=500, description='为什么交不出/为什么要延期')


class ScanIn(BaseModel):
    accounts: list[str] | None = Field(None, description='不传=全部有效业务账号')
    cadence: Cadence = 'daily'
    today: str | None = Field(None, description='业务日期 YYYY-MM-DD，便于回放')
    trigger: str = 'manual'


class ChatIn(BaseModel):
    today: str | None = None


class ProposeIn(BaseModel):
    option: int = Field(1, ge=1, le=5, description='选第几条建议')


class PolishIn(BaseModel):
    content: str = Field(..., min_length=1, max_length=20000)


class AskIn(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)


class DraftIn(BaseModel):
    """对话建任务：一轮用户原话 → 智能体拟草稿（不直接建任务）。"""
    session_id: str = Field(..., min_length=1, max_length=64, description='会话 ID（与 /api/ask 同一个）')
    question: str = Field(..., min_length=1, max_length=4000, description='用户原话')
    alert_id: int | None = Field(None, description='从某条预警的对话发起时带上')
    today: str | None = None


class DraftConfirmIn(BaseModel):
    """人工确认草稿：task_id 与 session_id 二选一；message 可传「确认」/「取消」。"""
    task_id: int | None = None
    session_id: str | None = None
    message: str | None = Field(None, max_length=200)
    overrides: dict[str, Any] | None = Field(
        None, description='确认前改：assignees/title/requirement/时间/核销')


class ConversationDraftIn(BaseModel):
    """把整段对话变成任务草稿（前端"把这段对话变成任务"按钮，不判意图）。"""
    session_id: str = Field(..., min_length=1, max_length=64)
    instruction: str = Field('', max_length=4000, description='可补充一句要求；空=用最后一条用户原话')
    alert_id: int | None = None
    today: str | None = None
