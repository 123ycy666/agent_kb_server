# -*- coding: utf-8 -*-
"""内置工具（9 只）。一只动作一件事，错误写「怎么改」，每只写清什么时候别用。"""
import asyncio
import json
from dataclasses import dataclass
from typing import Any

from agents import RunContextWrapper, function_tool

from app.agent import config, memory, skills


@dataclass
class Ctx:
    user_id: int
    session_id: str
    out_dir: str
    actor: Any = None
    howto: str = ''


def _dump(obj, cap: int | None = None) -> str:
    try:
        text = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:                              # noqa: BLE001
        text = str(obj)
    cap = config.TOOL_CAP if cap is None else cap
    return text if len(text) <= cap else text[:cap] + '…（结果超长已截断，要细节就缩小范围）'


def _file_ctx(c: Ctx) -> None:
    from app.secretary import tools_file
    tools_file.set_ctx(user_id=c.user_id, out_dir=c.out_dir, howto=c.howto)


def _err(ctx, exc) -> str:
    return '工具执行失败：%s: %s。改参数或换做法再试。' % (type(exc).__name__, str(exc)[:300])


@function_tool(failure_error_function=_err)
async def read_file(ctx: RunContextWrapper[Ctx], file_id: str, offset: int = 0,
                    limit: int = 40) -> str:
    """读取会话文件的一段正文（本轮上传的、或此前产出的）。用户说「这个文件」「刚才那份」时用它。
    一次读一段，返回 next_offset；next_offset 为 null 才算读完，读完不要重复读。
    别用在：只是聊文件是什么、不需要正文的时候。"""
    c = ctx.context

    def _run():
        _file_ctx(c)
        from app.secretary import tools_file
        return tools_file.read_file(str(file_id), int(offset or 0), int(limit or 40))

    return _dump(await asyncio.to_thread(_run))


@function_tool(failure_error_function=_err, strict_mode=False)
async def make_file(ctx: RunContextWrapper[Ctx], kind: str, spec: dict) -> str:
    """生成并交付一份文件。kind 为 docx / xlsx / pptx；spec 按对应形状给内容：
    docx {title, sections:[[小节,正文]], sign_off:[单位,日期], tables:[{header,rows}]}；
    xlsx {sheets:[{name,title,header,rows}]}；
    pptx {title, slides:[{layout:cover|section|bullets|end,title,bullets}]}。
    版式由技能决定，你只给内容；返回 file_id 与下载地址（这两个是给你自己后续改稿用的，
    不要写进给用户的回答里）。
    别用在：用户只是问问、没要文件的时候；也别为了凑一份文件去生成。"""
    c = ctx.context

    def _run():
        _file_ctx(c)
        from app.secretary import tools_file
        return tools_file.make_file(str(kind or 'docx').lower(), spec or {}, self_check=False)

    return _dump(await asyncio.to_thread(_run))


@function_tool(failure_error_function=_err)
async def read_skill(ctx: RunContextWrapper[Ctx], name: str) -> str:
    """读一篇技能的正文（步骤、口径、样板、检查项）。目录里只给了名字和用途，要正文就用它。
    正文里的《书名号》是关联技能，需要就再读。
    别用在：只想知道有哪些技能（目录里已经有名字和用途）。"""
    c = ctx.context

    def _run():
        return skills.read(c.user_id, str(name or '').strip())

    out = await asyncio.to_thread(_run)
    if out.get('ok'):
        c.howto = out.get('name') or ''
    return _dump(out, cap=30000)


@function_tool(failure_error_function=_err)
async def list_skills(ctx: RunContextWrapper[Ctx]) -> str:
    """列全部技能（名字 · 什么时候用 · 产物）。目录里没列全时再调。
    别用在：目录里已经列到、名字对得上的时候。"""
    c = ctx.context
    out = await asyncio.to_thread(lambda: skills.catalog(c.user_id, 200))
    return out or '（还没有配技能）'


@function_tool(failure_error_function=_err)
async def search_kb(ctx: RunContextWrapper[Ctx], query: str, top_k: int = 5) -> str:
    """检索制度、办法、规定等资料的原文切片。问「怎么规定的 / 谁审批 / 标准是多少」时用它。
    它不返回技能正文，技能用 read_skill 取。
    别用在：查数字（那用 query_data）、找做事做法（那用 read_skill）。"""
    c = ctx.context

    def _run():
        from app.secretary import kb_adapter, tools_kb
        if c.actor is not None:
            kb_adapter.set_actor(c.actor)
        return tools_kb.kb_search(str(query or ''), top_k=int(top_k or 5))

    return _dump(await asyncio.to_thread(_run))


@function_tool(failure_error_function=_err)
async def query_data(ctx: RunContextWrapper[Ctx], sql: str) -> str:
    """查业务库。只允许 SELECT，一条一句。查数前先 read_skill('查数口径') 看表名与指标算法。
    数字只能来自这个工具，查不到就写「需另行核实」。
    别用在：用户没要数字、只是想听解释的时候；也别为凑数去查。"""
    c = ctx.context

    def _run():
        from app.secretary import agent as A
        return A.execute('run_sql', {'sql': str(sql or '')})

    return _dump(await asyncio.to_thread(_run))


@function_tool(failure_error_function=_err)
async def db_schema(ctx: RunContextWrapper[Ctx], table: str = '', keyword: str = '',
                    list_tables: bool = False) -> str:
    """看业务库结构：给 table 看字段；给 keyword 按词找字段；list_tables=true 列出可用表。
    别用在：已经知道表名和字段的时候；也别反复列全表，一张表清单看一次就够。"""
    c = ctx.context

    def _run():
        from app.secretary import agent as A
        if list_tables:
            out = A.execute('list_tables', {'keyword': str(keyword or '')})
            if isinstance(out, dict) and isinstance(out.get('rows'), list) and len(out['rows']) > 40:
                out = dict(out, rows=out['rows'][:40], note='只列前 40 张；要具体的表用 table 看字段')
            return out
        if keyword:
            return A.execute('find_column', {'keyword': str(keyword)})
        if table:
            return A.execute('describe_table', {'table': str(table)})
        return {'error': '要么给 table，要么给 keyword，要么 list_tables=true'}

    return _dump(await asyncio.to_thread(_run))


@function_tool(failure_error_function=_err)
async def remember(ctx: RunContextWrapper[Ctx], kind: str, text: str) -> str:
    """记住一条跨会话信息。kind 三选一：preference（文风/口径偏好）、fact（单位名、人名等事实）、
    artifact（产出过的文件）。只记以后还用得上的，别记过程。
    别用在：这轮的过程细节、一次性的中间结果。"""
    c = ctx.context
    return _dump(await asyncio.to_thread(memory.remember, c.user_id, kind, text))


@function_tool(failure_error_function=_err)
async def recall(ctx: RunContextWrapper[Ctx], query: str = '', top_k: int = 5) -> str:
    """按关键词找回跨会话记忆（偏好/事实/产出过的文件）。用户说「上次那个」时用它。
    别用在：这轮对话里刚说过的内容（上下文里就有）。"""
    c = ctx.context
    return _dump(await asyncio.to_thread(memory.recall, c.user_id, query, int(top_k or 5)))


TOOLS = [read_file, make_file, read_skill, list_skills, search_kb, query_data,
         db_schema, remember, recall]
