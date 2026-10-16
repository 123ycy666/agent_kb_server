"""通用 Schema：分页、错误、统一响应。"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    """可从 ORM 对象构造的基类。"""

    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    """统一分页响应。"""

    items: list[T]
    total: int
    page: int = 1
    page_size: int = 20

    @property
    def pages(self) -> int:
        return (self.total + self.page_size - 1) // self.page_size if self.page_size else 0


class PageParams(BaseModel):
    """分页查询参数。"""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=200)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class ErrorDetail(BaseModel):
    """统一错误体。

    对外 API 必须返回明确错误码，不得只回 500。
    """

    code: str
    message: str
    detail: dict | list | None = None
    required: list[str] | None = None


class ErrorResponse(BaseModel):
    detail: ErrorDetail | str


class OkResponse(BaseModel):
    """简单成功响应。

    ⚠️ warnings 不是装饰字段：删除/恢复文档时会带回「复活了哪些旧版本」
    「向量模型已过期需重建索引」这类**用户必须知道**的信息。
    静默吞掉会让用户以为修好了，实际仍检索不到。
    """

    ok: bool = True
    message: str = "操作成功"
    warnings: list[str] = []


class IdResponse(BaseModel):
    id: int
