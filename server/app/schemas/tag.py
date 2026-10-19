"""标签 Schema（极简版）。

标签即**自由字符串**。本模块只提供三件事：

- 词表条目的增 / 改 / 查（供前端下拉复用）
- 重命名与删除（治理用）
- 检索时的标签过滤选项

原先的「维度 / 值类型 / 枚举约束 / 同义检测 / 合并废弃」全部移除。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class TagOut(ORMModel):
    """词表条目。"""

    id: int
    name: str
    description: str | None = None
    usage_count: int = 0
    created_at: datetime


class TagCreate(BaseModel):
    """新增标签（管理员维护词表时使用）。

    普通用户上传文档时**无需**先建标签 —— 直接写标签名即可，
    系统会自动登记进词表。
    """

    name: str = Field(min_length=1, max_length=32)
    description: str | None = Field(default=None, max_length=256)


class TagUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=256)


class TagRenameRequest(BaseModel):
    """重命名标签：会同步改写全部文档与切片上的标签值。"""

    old_name: str = Field(min_length=1, max_length=32)
    new_name: str = Field(min_length=1, max_length=32)


class TagListResponse(BaseModel):
    items: list[TagOut] = []
    total: int = 0


class TagOptionOut(BaseModel):
    """检索筛选下拉项。"""

    name: str
    usage_count: int = 0


class TagOptionsResponse(BaseModel):
    options: list[TagOptionOut] = []
