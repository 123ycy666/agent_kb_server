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
    verifier_type: Literal['auto', 'human'] = 'human'
    verify_rule: dict[str, Any] = Field(default_factory=dict)


class CloseIn(BaseModel):
    conclusion: str = ''
    reason: str = 'human_confirmed'


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
