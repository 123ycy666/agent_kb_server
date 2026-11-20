# -*- coding: utf-8 -*-
"""运行时：一个循环 + 内置工具 + 技能目录 + 会话 + SSE 事件。"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import date

from agents import (Agent, OpenAIChatCompletionsModel, Runner, set_tracing_disabled)
from openai import AsyncOpenAI

from app.agent import config, db, memory, session as sess, skills, trace
from app.agent.tools import Ctx, TOOLS

set_tracing_disabled(True)

STOP: dict[str, bool] = {}
_CLIENTS: dict[str, AsyncOpenAI] = {}


def _client(base: str, key: str) -> AsyncOpenAI:
    c = _CLIENTS.get(base + '|' + key[-8:])
    if c is None:
        c = AsyncOpenAI(base_url=base, api_key=key, timeout=180.0)
        _CLIENTS[base + '|' + key[-8:]] = c
    return c


def _model():
    base, key, name = config.llm()
    return OpenAIChatCompletionsModel(model=name, openai_client=_client(base, key))


# 兜底身份：与现场无关的一句（真正的身份在库里的 cfg.prompt_text，key=identity）。
DEFAULT_IDENTITY = '你是办公智能助手，服务对象是本单位员工。用户一句话交代，你自己判断这活怎么干。'

RULES = [
    '要读文件、查数、查制度、出文件，就用工具做；不需要工具就直接回答。',
    '数字只能来自 query_data 的结果；查不到就写「需另行核实」。',
    '出文件前先看技能目录：有对应技能就 read_skill 读完整篇再照做；没有就按自己的能力做。',
    '技能里写了检查项的，交付前自己逐条核对并在回答里说明；不要调自检工具。',
    '用户说「这个文件」「刚才那份」「再改一版」时，用 read_file 按 file_id 读回来再改，别重写无关部分。',
    '用户要改动（换地点、加一句、换格式）时，改完必须交出新的文件；只回一句「已改」不交文件，等于没改完。',
    '查数：先读《查数口径》，口径里有的表直接用；表清单最多看一次，别反复列全表。',
    '同一种查询失败或返回空两次，就换做法；换不了就在回答里写「需另行核实」，别硬凑。',
    '要查多个数时，把互不依赖的查询一次发出来（同一条消息里多个 query_data），别一条一条排队。',
    '没有数字要求时，不要为了凑数去查库。',
    '用户明确要文件（说了 word / excel / ppt，或说「出一份」「做个表」）时，先把文件做出来；'
    '缺的信息用【待确认】标在正文里，在回答末尾列出来让他补，不要只反问不动手。',
    '缺关键信息又猜不出来时，问一句再动手；能从上下文推出来的不要问。',
    '回答里不要出现 file_id、下载地址、/api/ 路径；文件由界面以附件卡片交付，你只说文件名和要点。',
]


def _field(obj, name: str) -> str:
    """SDK 的事件对象有的是模型、有的是 dict（工具输出就是 TypedDict），两种都取得到。"""
    v = getattr(obj, name, None)
    if v is None and isinstance(obj, dict):
        v = obj.get(name)
    return str(v or '')


def identity() -> str:
    """身份只有一处：提示词页的「身份」。读不到才用代码兜底。占位符要替换掉再给模型。"""
    try:
        from app.secretary import prompt_text, prompts_data, project
        t = (prompt_text.get('identity', prompts_data.IDENTITY_FALLBACK) or '').strip()
        return project.fill(t) if t else DEFAULT_IDENTITY
    except Exception:                              # noqa: BLE001
        return DEFAULT_IDENTITY


def style_text() -> str:
    """回答风格是页面上的一段话；没配就不加这一节，走中性。"""
    try:
        from app.secretary import prompt_text
        return (prompt_text.get('agent_style', '') or '').strip()
    except Exception:                              # noqa: BLE001
        return ''


def _strip_tool(chunk: str, inside: bool) -> tuple[str, bool]:
    """模型在没有工具可用时会把调用语法当正文吐出来，这里把它整段丢掉。"""
    out = ''
    while chunk:
        if inside:
            i = chunk.find('</tool_use>')
            if i < 0:
                return out, True
            chunk = chunk[i + len('</tool_use>'):]
            inside = False
            continue
        i = chunk.find('<tool_use>')
        if i < 0:
            return out + chunk, False
        out += chunk[:i]
        chunk = chunk[i + len('<tool_use>'):]
        inside = True
    return out, inside


def disabled_tools() -> set[str]:
    """页面上关掉的工具名。"""
    try:
        from app.secretary import prompt_text
        raw = (prompt_text.get('agent_tools', '') or '').strip()
        if raw:
            v = json.loads(raw)
            if isinstance(v, list):
                return {str(x) for x in v}
    except Exception:                              # noqa: BLE001
        pass
    return set()


def active_tools() -> list:
    off = disabled_tools()
    return [t for t in TOOLS if getattr(t, 'name', '') not in off]


def skills_scope() -> tuple[str, list[str]]:
    """技能范围：all（默认）/ off / selected（只给名单里的）。"""
    try:
        from app.secretary import prompt_text
        raw = (prompt_text.get('agent_skills', '') or '').strip()
        if raw:
            v = json.loads(raw)
            mode = str(v.get('mode') or 'all')
            if mode in ('off', 'selected'):
                return mode, [str(x) for x in (v.get('names') or [])]
    except Exception:                              # noqa: BLE001
        pass
    return 'all', []


def catalog_text(user_id: int) -> str:
    mode, names = skills_scope()
    if mode == 'off':
        return ''
    text = skills.catalog(user_id, config.CATALOG_MAX) or ''
    if mode == 'selected' and names:
        keep = [ln for ln in text.split('\n')
                if any(ln.lstrip('- ').strip().startswith(n) for n in names)]
        return '\n'.join(keep)
    return text


def instructions(ctx: Ctx) -> str:
    catalog = catalog_text(ctx.user_id) or '（还没配技能，按通用能力做）'
    about = memory.about(ctx.user_id, config.ABOUT_MAX)
    lines = [identity(), '', '【作业规则】'] + ['- ' + r for r in RULES] + ['', '【技能目录】', catalog]
    style = style_text()
    if style:
        lines += ['', '【回答风格】', style]
    off = disabled_tools()
    if off:
        lines += ['', '【这轮不能用的工具】' + '、'.join(sorted(off)),
                  '这些工具这轮关了：不要输出调用语法，直接说明这件事现在做不了。']
    if about:
        lines += ['', '【关于我】', about]
    lines += ['', '【今天】' + date.today().isoformat()]
    return '\n'.join(lines)


def _input_text(text: str, file_ids: list[str], user_id: int) -> str:
    out = (text or '').strip()
    if not file_ids:
        return out
    from app.secretary import doc_files
    rows = []
    for fid in file_ids:
        try:
            meta = doc_files.load(fid, user_id)
            rows.append('- 《%s》 file_id=%s' % (meta.get('name') or fid, fid))
        except Exception:                          # noqa: BLE001
            rows.append('- file_id=%s（取不到名字）' % fid)
    return out + '\n\n【本轮文件】\n' + '\n'.join(rows) + '\n（要正文用 read_file）'


async def run_stream(*, user_id: int, text: str, session_id: str | None = None,
                     file_ids: list[str] | None = None, actor=None):
    """产出 SSE 事件字典。调用方负责拼成 event-stream。"""
    base, key, model_name = config.llm()
    file_ids = [str(x) for x in (file_ids or []) if str(x).strip()]
    db.ensure()
    session_id = sess.resolve_alias(session_id or '', user_id, title=(text or '')[:40])
    sess.ensure(session_id, user_id)
    sess.touch(session_id, title=(text or '')[:40])

    out_dir = 'data/agent_out/%s' % session_id
    ctx = Ctx(user_id=user_id, session_id=session_id, out_dir=out_dir, actor=actor)
    run_id = trace.start(session_id, user_id, model_name)
    root = trace.span(run_id, 'agent', '秘书', inp={'text': (text or '')[:500]})
    STOP[run_id] = False
    started = time.time()
    answer, stopped, error = '', False, ''
    artifacts: list[dict] = []
    calls: dict[str, dict] = {}
    gen_t0 = time.time()
    leak = False

    yield {'type': 'run', 'run_id': run_id, 'session_id': session_id,
           'model': model_name}

    agent = Agent(name='秘书', instructions=instructions(ctx), model=_model(), tools=active_tools())
    result = Runner.run_streamed(agent, _input_text(text, file_ids, user_id),
                                 session=sess.PGSession(session_id),
                                 context=ctx,
                                 max_turns=config.MAX_TURNS)
    try:
        async for ev in result.stream_events():
            if STOP.get(run_id):
                stopped = True
                result.cancel(mode='after_turn')
                break
            if ev.type == 'raw_response_event':
                d = getattr(ev, 'data', None)
                dt = getattr(d, 'type', '')
                if dt == 'response.output_text.delta':
                    delta = getattr(d, 'delta', '') or ''
                    if delta:
                        if not gen_t0:
                            gen_t0 = time.time()
                        delta, leak = _strip_tool(delta, leak)
                        if delta:
                            yield {'type': 'text-delta', 'text': delta}
                elif dt in ('response.completed', 'response.incomplete'):
                    resp = getattr(d, 'response', None)
                    usage = getattr(resp, 'usage', None)
                    tok = int(getattr(usage, 'total_tokens', 0) or 0)
                    trace.span(run_id, 'generation', model_name,
                                          parent_id=root or None,
                                          inp={'model': model_name},
                                          out={'tokens': tok},
                                          ms=int((time.time() - gen_t0) * 1000) if gen_t0 else 0,
                                          level='ERROR' if dt == 'response.incomplete' else 'DEFAULT')
                    gen_t0 = 0.0
            elif ev.type == 'run_item_stream_event':
                name = getattr(ev, 'name', '')
                item = getattr(ev, 'item', None)
                raw = getattr(item, 'raw_item', None)
                if name == 'tool_called':
                    fname = _field(raw, 'name')
                    args = _field(raw, 'arguments')
                    cid = _field(raw, 'call_id')
                    calls[cid] = {'name': fname, 'args': str(args), 't0': time.time()}
                    yield {'type': 'tool-call-start', 'tool_call_id': cid, 'name': fname}
                    if args:
                        yield {'type': 'tool-call-args-delta', 'tool_call_id': cid,
                               'text': str(args)[:2000]}
                elif name == 'tool_output':
                    cid = _field(raw, 'call_id')
                    info = calls.pop(cid, None) or {}
                    fname = info.get('name') or 'tool'
                    ms = int((time.time() - info.get('t0', time.time())) * 1000)
                    body = getattr(item, 'output', '') or ''
                    text_body = body if isinstance(body, str) else json.dumps(
                        body, ensure_ascii=False, default=str)
                    is_error = text_body.startswith('工具执行失败')
                    trace.span(run_id, 'tool', fname, parent_id=root or None, call_id=cid,
                               inp={'args': str(info.get('args', ''))[:2000]},
                               out={'result': text_body[:2000]}, ms=ms,
                               level='ERROR' if is_error else 'DEFAULT')
                    yield {'type': 'tool-call-result', 'tool_call_id': cid,
                           'result': text_body[:4000], 'is_error': is_error}
                    gen_t0 = time.time()
                    if fname == 'make_file':
                        try:
                            d = json.loads(text_body)
                        except Exception:          # noqa: BLE001
                            d = None
                        if isinstance(d, dict) and d.get('url'):
                            made = {'name': d.get('name'), 'file_id': d.get('file_id'),
                                    'url': d.get('url'), 'kind': d.get('kind'),
                                    'size': d.get('bytes') or 0}
                            artifacts.append(made)
                            yield {'type': 'file', **made}
    except Exception as exc:                       # noqa: BLE001
        error = '%s: %s' % (type(exc).__name__, str(exc)[:400])
        yield {'type': 'error', 'message': error}
    finally:
        STOP.pop(run_id, None)

    try:
        answer = _strip_tool(str(getattr(result, 'final_output', '') or ''), False)[0]
    except Exception:                              # noqa: BLE001
        answer = ''

    tokens = 0
    try:
        usage = getattr(getattr(result, 'context_wrapper', None), 'usage', None)
        tokens = int(getattr(usage, 'total_tokens', 0) or 0)
    except Exception:                              # noqa: BLE001
        pass

    ms = int((time.time() - started) * 1000)
    status = 'stopped' if stopped else ('failed' if error else 'done')
    trace.end(run_id, status, error=error, tokens=tokens, ms=ms)
    try:
        await asyncio.to_thread(sess.maybe_compact, session_id, model_name)
    except Exception:                              # noqa: BLE001
        pass
    yield {'type': 'finish', 'run_id': run_id, 'session_id': session_id,
           'status': status, 'answer': answer, 'artifacts': artifacts,
           'usage': {'tokens': tokens}, 'error': error, 'ms': ms}


def stop(run_id: str) -> None:
    STOP[run_id] = True


# ── 同步一次性运行：给老接口 /api/ask 的 office 分支用 ──────────────────

def run_once(*, user_id: int, text: str, session_id: str | None = None,
             file_ids: list[str] | None = None) -> dict:
    """同步入口（调用方必须在线程池里跑）。内部复用同一条 SSE 循环。"""
    async def _collect():
        evs = []
        async for ev in run_stream(user_id=int(user_id or 0), text=text,
                                   session_id=session_id, file_ids=file_ids):
            evs.append(ev)
        return evs

    t0 = time.time()
    events = asyncio.run(_collect())
    tools = [e.get('name') for e in events if e.get('type') == 'tool-call-start']
    artifacts = [{'name': e.get('name'), 'file_id': e.get('file_id'),
                  'url': e.get('url'), 'kind': e.get('kind')}
                 for e in events if e.get('type') == 'file']
    done = next((e for e in events if e.get('type') == 'finish'), {}) or {}
    sp = [{'kind': 'tool', 'tool': n} for n in tools]
    return {
        'input_question': text,
        'answer': done.get('answer') or '',
        'lane': 'office',
        'product': (artifacts[0].get('kind') if artifacts else ''),
        'howto': '', 'spec_used': {}, 'self_check': {}, 'number_sources': [],
        'artifacts': artifacts, 'trace': sp, 'steps': len(sp),
        'model': config.llm()[2],
        'db_query_count': tools.count('query_data'),
        'kb_query_count': tools.count('search_kb'),
        'no_db_query': 'query_data' not in tools,
        'qtype': '办公' if artifacts else '读件',
        'today': date.today().isoformat(), 'files': [],
        'injection': {'total': 0, 'budget': 0, 'parts': [], 'compressed': []},
        'stopped': done.get('status') == 'stopped',
        'elapsed_ms': int((time.time() - t0) * 1000),
        'tokens': (done.get('usage') or {}).get('tokens') or 0,
        'session_id': done.get('session_id') or session_id,
        'qa_id': None,
    }

