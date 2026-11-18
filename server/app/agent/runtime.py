# -*- coding: utf-8 -*-
"""运行时：一个循环 + 内置工具 + 技能目录 + 会话 + SSE 事件。"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import date

from agents import (Agent, OpenAIChatCompletionsModel, Runner, set_tracing_disabled)
from openai import AsyncOpenAI

from app.agent import config, memory, session as sess, skills, trace
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


def instructions(ctx: Ctx) -> str:
    catalog = skills.catalog(ctx.user_id, config.CATALOG_MAX) or '（还没配技能，按通用能力做）'
    about = memory.about(ctx.user_id, config.ABOUT_MAX)
    lines = [
        '你是随州供电公司的办公秘书，服务对象是公司员工。用户一句话交代，你自己判断这活怎么干。',
        '',
        '【作业规则】',
        '- 要读文件、查数、查制度、出文件，就用工具做；不需要工具就直接回答。',
        '- 数字只能来自 query_data 的结果；查不到就写「需另行核实」。',
        '- 出文件前先看技能目录：有对应技能就 read_skill 读完整篇再照做；没有就按自己的能力做。',
        '- 技能里写了检查项的，交付前自己逐条核对并在回答里说明；不要调自检工具。',
        '- 用户说「这个文件」「刚才那份」「再改一版」时，用 read_file 按 file_id 读回来再改，别重写无关部分。',
        '- 缺关键信息又猜不出来时，问一句再动手；能从上下文推出来的不要问。',
        '',
        '【技能目录】',
        catalog,
    ]
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
    session_id = session_id or sess.create(user_id, title=(text or '')[:40])
    sess.ensure(session_id, user_id)
    sess.touch(session_id, title=(text or '')[:40])

    out_dir = 'data/agent_out/%s' % session_id
    ctx = Ctx(user_id=user_id, session_id=session_id, out_dir=out_dir, actor=actor)
    run_id = trace.start(session_id, user_id, model_name)
    STOP[run_id] = False
    started = time.time()
    answer, stopped, error = '', False, ''
    artifacts: list[dict] = []
    calls: dict[str, dict] = {}

    yield {'type': 'run', 'run_id': run_id, 'session_id': session_id,
           'model': model_name}

    agent = Agent(name='秘书', instructions=instructions(ctx), model=_model(), tools=TOOLS)
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
                if getattr(d, 'type', '') == 'response.output_text.delta':
                    delta = getattr(d, 'delta', '') or ''
                    if delta:
                        yield {'type': 'delta', 'text': delta}
            elif ev.type == 'run_item_stream_event':
                name = getattr(ev, 'name', '')
                item = getattr(ev, 'item', None)
                raw = getattr(item, 'raw_item', None)
                if name == 'tool_called':
                    fname = getattr(raw, 'name', '') or ''
                    args = getattr(raw, 'arguments', '') or ''
                    cid = getattr(raw, 'call_id', '') or ''
                    calls[cid] = {'name': fname, 't0': time.time()}
                    trace.span(run_id, 'tool', fname, {'args': str(args)[:2000]})
                    yield {'type': 'tool_call', 'name': fname, 'args': str(args)[:2000],
                           'call_id': cid}
                elif name == 'tool_output':
                    cid = getattr(raw, 'call_id', '') or ''
                    info = calls.pop(cid, None) or (calls.pop(next(iter(calls)), {}) if calls else {})
                    fname = info.get('name') or 'tool'
                    ms = int((time.time() - info.get('t0', time.time())) * 1000)
                    body = getattr(item, 'output', '') or ''
                    text_body = body if isinstance(body, str) else json.dumps(
                        body, ensure_ascii=False, default=str)
                    trace.span(run_id, 'tool_result', fname,
                               {'result': text_body[:2000]}, ms=ms)
                    if fname == 'make_file':
                        try:
                            d = json.loads(text_body)
                            if isinstance(d, dict) and d.get('url'):
                                artifacts.append({'name': d.get('name'),
                                                  'file_id': d.get('file_id'),
                                                  'url': d.get('url'),
                                                  'kind': d.get('kind')})
                        except Exception:          # noqa: BLE001
                            pass
                    brief = text_body[:400]
                    yield {'type': 'tool_result', 'name': fname, 'ms': ms,
                           'brief': brief}
                elif name == 'message_output_created':
                    txt = getattr(item, 'output', '') or ''
                    if txt:
                        yield {'type': 'message', 'text': str(txt)}
    except Exception as exc:                       # noqa: BLE001
        error = '%s: %s' % (type(exc).__name__, str(exc)[:400])
        yield {'type': 'error', 'message': error}
    finally:
        STOP.pop(run_id, None)

    try:
        answer = str(getattr(result, 'final_output', '') or '')
    except Exception:                              # noqa: BLE001
        answer = ''
    for a in artifacts:
        yield {'type': 'artifact', **a}

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
    yield {'type': 'done', 'run_id': run_id, 'session_id': session_id,
           'status': status, 'answer': answer, 'artifacts': artifacts,
           'ms': ms, 'tokens': tokens}


def stop(run_id: str) -> None:
    STOP[run_id] = True
