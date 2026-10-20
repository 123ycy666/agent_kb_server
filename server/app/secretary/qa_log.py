# -*- coding: utf-8 -*-
"""对话记录落库（agent_data 库）+ 历史查询。

与 tools_db 的分工（**别混**）：
    tools_db   读 dlj_data（业务库），**严格只读**（只放 SELECT/WITH + 表白名单）
    本模块     读写 agent_data（记录库），需要写权限，**独立连接**，不走 tools_db 的闸

三条纪律（与 name_fix / gaps 一致）：
  1. **失败只打日志** —— 落库坏了绝不影响问答。print 必须 flush，否则重定向到文件时看不到
  2. **一个开关能整体关掉** —— SECRETARY_QA_LOG=0
  3. **重活异步，关键同步** —— 见下面 save() 的说明

写哪些表：
    t_chat_session      本会话一行（不存在则建，标题取首问）
    t_chat_message      两条：用户那条 + AI 那条（AI 那条带 reply_to_id 指回用户那条）
    t_chat_query_trace  一行：这次问答的口径与质量（纠错命中 / 时间地点 / 查库次数 / 是否可信）
    t_chat_trace_step   N 行：每一步（纠错/补全/工具调用/作答/拦下）
"""
import json
import os
import threading
import uuid
from datetime import timezone
from zoneinfo import ZoneInfo

# ══════════════════════════════════════════════════════════════
#  时间输出：库里是 UTC，给前端必须是**本地时间**
# ══════════════════════════════════════════════════════════════
#
# 2026-10-20 修（用户报「历史对话的时间有问题，瞎写的」）
# ------------------------------------------------------
# `t_chat_session.create_time` 是 timestamptz，psycopg 取回来带 tzinfo=UTC。
# 原来直接 `str(dt)` 输出成 "2026-10-20 05:01:38.392015" —— **没有时区标识**，
# 前端 dayjs 只能按「本地时间」解析，于是把 UTC 的 05:01 当北京时间 05:01 显示，
# 实际是 13:01，整整差 8 小时。
#
# 为什么在服务端转、而不是让前端 `dayjs.utc()`：
#   - sga 前端是直接 `.slice(0,16)` 截字符串显示的，改成 ISO（带 T）会让他们全乱；
#   - 服务端一次性转成本地，所有消费方（SPA / 静态页 / sga）都对，前端不需要懂时区。
#
# 时区可用 DISPLAY_TZ 覆盖（默认 Asia/Shanghai，本项目场景固定国内）。
_DISPLAY_TZ = ZoneInfo(os.environ.get('DISPLAY_TZ') or 'Asia/Shanghai')


def _local_str(dt):
    """UTC datetime → 本地时间字符串 'YYYY-MM-DD HH:mm:ss'。

    None 原样返回；已经是字符串的也原样返回（幂等，避免二次转换）。
    """
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    if dt.tzinfo is None:            # 无时区 = 库里的约定是 UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_DISPLAY_TZ).strftime('%Y-%m-%d %H:%M:%S')



from app.secretary import pgdb

from app.secretary import config

# 步骤明细的截断长度：一次问答 5~10 步，不截断会把库撑大
_TEXT_MAX = 2000


def _enabled():
    return os.environ.get('SECRETARY_QA_LOG', '1') not in ('0', 'false', 'False', 'off')


def _conn():
    """记录库的连接。**每次用完就关** —— 写入频率低（一次问答一次），
    长连接反而要处理探活/失效，不划算。

    2026-10-15 数据层合并：对话记录已迁到 PG ad_new（与知识库同库），
    通过 pgdb 兼容层保持原有 pymysql 风格代码不变。
    """
    return pgdb.connect(autocommit=True)


def available():
    """记录库是否就绪（供 /health）。"""
    if not _enabled():
        return False
    try:
        c = _conn()
        try:
            cur = c.cursor()
            cur.execute('SELECT COUNT(*) AS n FROM t_chat_session')
            cur.fetchone()
        finally:
            c.close()
        return True
    except Exception:
        return False


def status():
    if not _enabled():
        return {'available': False, 'reason': '已通过 SECRETARY_QA_LOG=0 关闭'}
    try:
        c = _conn()
        try:
            cur = c.cursor()
            cur.execute('SELECT COUNT(*) AS n FROM t_chat_session')
            cur.fetchone()
        finally:
            c.close()
        return {'available': True, 'reason': ''}
    except Exception as e:                      # noqa: BLE001
        return {'available': False,
                'reason': '记录库 %s 不可用（%s: %s），本次问答不落库' %
                          (config.AGENT_DB, type(e).__name__, str(e)[:120])}


def new_session_id():
    """不传 session_id 时由服务端生成（UUID，对外暴露用这个，不暴露自增 id）。"""
    return 's_' + uuid.uuid4().hex


# ---------------------------------------------------------------- 写

def _json(v, cap=_TEXT_MAX):
    try:
        s = json.dumps(v, ensure_ascii=False, default=str)
    except Exception:
        s = str(v)
    return s[:cap]


def _resolve_user(cur, user_id, user_code):
    """拿到 t_user.id。调用方可能只给业务用户ID（uid），这里换成主键。"""
    if user_id:
        return int(user_id), None
    code = (user_code or '').strip()
    if not code:
        return None, None
    cur.execute('SELECT id, uid FROM t_user WHERE uid=%s AND deleted_flag=0 LIMIT 1', (code,))
    r = cur.fetchone()
    return (r['id'], r['uid']) if r else (None, code)


def save(result, session_id=None, user_id=None, user_code=None,
         client_ip=None, channel=None):
    """把一次问答落库。返回 {'session_id', 'qa_id'}；失败抛异常（由调用方兜住）。

    **为什么关键部分同步、步骤异步**：
      - 主表（会话/消息/trace）同步写，因为响应里要回 `qa_id` 给前端做评价，
        异步就拿不到 id 了。这几条 INSERT 正常只要几十毫秒。
      - 步骤明细每次 5~10 行、每行最多 2KB，是最重的一块，放后台线程，
        不占用响应时间。
    """
    sid = session_id or new_session_id()
    if not _enabled():
        return {'session_id': sid, 'qa_id': None}
    info = _save_sync(result, sid, user_id, user_code, client_ip, channel)
    if info.get('steps'):
        t = threading.Thread(target=_save_steps, args=(info['qa_id'], info['steps']), daemon=True)
        t.start()
    return {'session_id': sid, 'qa_id': info['qa_id']}


def _save_sync(result, sid, user_id, user_code, client_ip, channel):
    """写会话 + 两条消息 + trace 主行。seq_no 冲突时重试一次。"""
    scope = result.get('scope') or {}
    timings = result.get('timings') or {}
    answer = result.get('answer') or ''
    question = result.get('input_question') or ''
    unverified = bool(result.get('unverified'))
    try:
        from app.secretary import gaps
        is_gap = bool(gaps.is_gap(answer, unverified))
    except Exception:
        is_gap = unverified

    last_err = None
    for _ in range(2):                          # 唯一键冲突重试一次
        c = _conn()
        try:
            cur = c.cursor()
            uid_pk, uid_code = _resolve_user(cur, user_id, user_code)
            cur.execute('SELECT id FROM t_chat_session WHERE session_id=%s', (sid,))
            row = cur.fetchone()
            if row:
                skey = row['id']
                cur.execute('SELECT MAX(seq_no) AS m FROM t_chat_message WHERE session_id=%s', (skey,))
                seq = int(cur.fetchone()['m'] or 0)
            else:
                cur.execute(
                    'INSERT INTO t_chat_session '
                    '(session_id, user_id, user_code, ai_model, title, channel, client_ip, '
                    ' msg_count, status, last_msg_at) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s,0,1,NOW())',
                    (sid, uid_pk, uid_code, result.get('model'),
                     (question or '')[:50] or None, channel or 'webapi', client_ip))
                skey = cur.lastrowid
                seq = 0
            # 用户那条
            cur.execute(
                'INSERT INTO t_chat_message '
                '(session_id, chat_session_id, seq_no, direction, sender_type, sender_id, '
                ' msg_type, content, gen_status, send_status) '
                'VALUES (%s,%s,%s,1,1,%s,1,%s,1,3)',
                (skey, sid, seq + 1, uid_pk, question))
            user_msg_id = cur.lastrowid
            # AI 那条（带耗时与模型）
            cur.execute(
                'INSERT INTO t_chat_message '
                '(session_id, chat_session_id, seq_no, direction, sender_type, reply_to_id, '
                ' msg_type, content, model, latency_ms, gen_status, send_status, error_msg) '
                'VALUES (%s,%s,%s,2,2,%s,1,%s,%s,%s,%s,3,%s)',
                (skey, sid, seq + 2, user_msg_id, answer, result.get('model'),
                 int(result.get('elapsed_ms') or 0),
                 2 if unverified else 1,
                 '未调用工具即下结论，已拦下' if unverified else None))
            ai_msg_id = cur.lastrowid
            # trace 主行
            cur.execute(
                'INSERT INTO t_chat_query_trace '
                '(message_id, chat_session_id, raw_question, fixed_question, completed_question, '
                ' user_profile, name_fix_used, completion_used, '
                ' period_type, period_key, time_text, time_is_default, place, place_is_default, '
                ' model, db_query_count, sql_query_count, kb_query_count, steps, '
                ' no_db_query, unverified, is_gap, elapsed_ms, namefix_ms, completion_ms, loop_ms) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (ai_msg_id, sid,
                 question, result.get('fixed_question'), result.get('completed_question'),
                 (result.get('user_profile') or None),
                 1 if result.get('name_fix_used') else 0,
                 1 if result.get('completion_used') else 0,
                 scope.get('period_type'), scope.get('period_key'), scope.get('time'),
                 1 if scope.get('time_is_default') else 0,
                 scope.get('place'), 1 if scope.get('place_is_default') else 0,
                 result.get('model'),
                 int(result.get('db_query_count') or 0), int(result.get('sql_query_count') or 0),
                 int(result.get('kb_query_count') or 0), int(result.get('steps') or 0),
                 1 if result.get('no_db_query') else 0, 1 if unverified else 0, 1 if is_gap else 0,
                 int(result.get('elapsed_ms') or 0),
                 int(timings.get('namefix_ms') or 0), int(timings.get('completion_ms') or 0),
                 int(timings.get('loop_ms') or 0)))
            qa_id = cur.lastrowid
            cur.execute('UPDATE t_chat_session SET msg_count = msg_count + 2, last_msg_at = NOW(), '
                        'total_cost_ms = total_cost_ms + %s WHERE id=%s',
                        (int(result.get('elapsed_ms') or 0), skey))
            return {'session_id': sid, 'qa_id': qa_id,
                    'steps': result.get('trace') or [], 'ok': True}
        except Exception as e:                      # noqa: BLE001
            # 迁 PG 后只对「唯一约束冲突」（seq_no 撞键，PG 的 UniqueViolation）重试一次；
            # 其余异常照原样抛出（外层本来就要求"落库失败不影响回答"）。
            if not any(k in type(e).__name__ for k in ("UniqueViolation", "IntegrityError")):
                raise
            last_err = e
        finally:
            c.close()
    raise last_err or RuntimeError('落库失败')


def _save_steps(qa_id, trace):
    """步骤明细（后台线程）。失败只打日志。"""
    if not qa_id or not trace:
        return
    try:
        c = _conn()
        try:
            cur = c.cursor()
            for i, s in enumerate(trace, 1):
                if not isinstance(s, dict):
                    continue
                res = s.get('result') if isinstance(s.get('result'), dict) else None
                err = (res or {}).get('error')
                out = res if res else (s.get('content') or '')
                cur.execute(
                    'INSERT INTO t_chat_trace_step '
                    '(trace_id, step_no, step_kind, round_no, tool_name, input_text, output_text, '
                    ' sql_text, result_rows, cost_ms, model_ms, status, error_msg) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    (qa_id, int(s.get('seq') or i), s.get('kind'), s.get('round'),
                     s.get('tool'), _json(s.get('args')), _json(out),
                     (res or {}).get('sql'), (res or {}).get('row_count'),
                     s.get('ms'), s.get('model_ms'),
                     1 if err else 0, (str(err)[:500] if err else None)))
        finally:
            c.close()
    except Exception as e:                        # noqa: BLE001
        print('[qa_log] 步骤明细写入失败（不影响回答）：%s: %s'
              % (type(e).__name__, e), flush=True)


# ---------------------------------------------------------------- 读（历史会话）

def load_context(session_id, limit=5, answer_chars=400):
    """取同一场会话最近的 N 轮问答，用作**多轮上下文**。返回按时间正序（旧 → 新）。

    只取「有 AI 回答」的轮次（靠 trace 关联），所以正在进行中的那轮不会混进来。
    用途有两个：
      ① 交给模型当上下文 —— 理解「那全市的呢」这类省略了主语的问法；
      ② 在**问题完全没提时间/地点**时，沿用上一轮的口径（见 time_scope.describe）。

    ★ 上游传 0 表示「本次不带上下文」（见 agent.ask），那时**不应该调用本函数** ——
      这里 max(1, ...) 会兜成 1 轮，语义就变了。
    """
    if not session_id:
        return []
    limit = max(1, min(int(limit or 5), 20))
    c = _conn()
    try:
        cur = c.cursor()
        cur.execute(
            'SELECT m.seq_no, q.raw_question, q.fixed_question, q.place, q.place_is_default, '
            '       q.period_type, q.period_key, q.time_text, q.time_is_default, m.content AS answer '
            'FROM t_chat_message m '
            'JOIN t_chat_query_trace q ON q.message_id = m.id AND q.deleted_flag = 0 '
            'WHERE m.chat_session_id=%s AND m.deleted_flag=0 AND m.sender_type=2 '
            'ORDER BY m.seq_no DESC LIMIT %s', (session_id, limit))
        rows = cur.fetchall()[::-1]                     # 转成正序
        for r in rows:
            r['answer'] = (r.get('answer') or '')[:answer_chars]
        return rows
    finally:
        c.close()


def norm_limit(value):
    """把接口传进来的 limit 规范化。

    约定（与《接口对接说明.md》一致）：
      - 不传 / 空 / 0 / 负数 → **None，表示不限制**（返回符合条件的全部）
      - 正整数              → 该值，表示分页取前 N 条

    ★ 注意别写成 `int(value or 0) or None` 那种简写 —— 那样 `limit=0` 会被当成"没传"，
      而这里 `limit=0` 恰恰是"全部"的显式写法。
    """
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _cap(limit, total, max_rows):
    """算出最终要用的 LIMIT 与是否被防呆上限截断。

    limit=None 表示"全部"：此时用 max_rows 兜底，总数超过它就是被截断了。
    limit>0 是调用方自己选的分页，不算截断。
    """
    max_rows = int(max_rows or config.API_MAX_ROWS or 5000)
    if limit is None:
        return max_rows, total > max_rows
    return limit, False


def delete_session(session_id, user_id=None, user_code=None):
    """删除一场历史会话（软删，可恢复）。

    ★ 为什么是软删而不是 DELETE（2026-10-20 加）
    ------------------------------------------------
    `t_chat_session` 本来就有 `deleted_flag`，列表与历史接口都按它过滤；
    删错了（用户手滑、同名会话看错）只能靠数据库备份找回，代价太高。
    软删之后：列表立刻看不到、按 session_id 直接查历史也 404（get_history 带
    `deleted_flag=0`），但数据还在，必要时一条 UPDATE 就能恢复。

    ★ 归属校验不能省
    ----------------
    session_id 不是密钥：它会出现在页面上、日志里、别人转发的截图里。
    只凭 session_id 就删，等于「知道 id 就能删别人的历史」。
    所以传了 user_id 就**必须匹配**；两个都没传则拒绝（不给「匿名删任意会话」的口子）。

    返回 {'ok': bool, 'deleted': int, 'reason': str}
    """
    sid = (session_id or '').strip()
    if not sid:
        return {'ok': False, 'deleted': 0, 'reason': '缺少 session_id'}
    if not user_id and not user_code:
        return {'ok': False, 'deleted': 0, 'reason': '缺少归属信息，拒绝删除'}

    where = ' WHERE session_id=%s AND deleted_flag=0'
    params = [sid]
    if user_id:
        where += ' AND user_id=%s'
        params.append(int(user_id))
    elif user_code:
        where += ' AND user_code=%s'
        params.append(user_code)

    c = _conn()
    try:
        cur = c.cursor()
        cur.execute('UPDATE t_chat_session SET deleted_flag=1' + where, params)
        n = int(cur.rowcount or 0)
        if n:
            # 明细跟着一起标记：单会话查询已经进不去了，但统计/导出是按明细扫的，
            # 不标记就会在「问答量」里继续被算进去（删了还占用数字）。
            cur.execute('UPDATE t_chat_message SET deleted_flag=1 '
                        'WHERE chat_session_id=%s AND deleted_flag=0', (sid,))
            cur.execute('UPDATE t_chat_query_trace SET deleted_flag=1 '
                        'WHERE chat_session_id=%s AND deleted_flag=0', (sid,))
        return {'ok': bool(n), 'deleted': n,
                'reason': '' if n else '会话不存在或不属于当前用户'}
    finally:
        c.close()


def list_sessions(user_code=None, limit=None, offset=0, max_rows=None, user_id=None):
    """历史对话列表：一行一场会话，按最后活动时间倒序。

    limit=None（不传）= **全部返回**；limit>0 = 分页。默认不再有 200 条的硬上限。

    user_id（2026-10-15 单进程合并新增）：按统一用户表的登录用户过滤，
    「我的会话」只返回自己的。user_id 优先于 user_code。

    返回 dict（不再只返回行列表）：
      total     符合条件的**总数**（不受 limit/offset 影响）
      returned  本次实际返回的条数
      limit     实际生效的条数上限（全部返回时等于防呆上限）
      offset    偏移
      truncated 是否因为超过防呆上限（config.API_MAX_ROWS）被截断

    ★ total 必须单独 COUNT 一次。之前的实现把这个字段填成了"本页条数"，
      前端拿到 total=20 会以为库里总共只有 20 场会话。
    """
    limit = norm_limit(limit)
    offset = max(0, int(offset or 0))
    where, params = ' WHERE s.deleted_flag=0', []
    if user_id:
        where += ' AND s.user_id = %s'
        params.append(int(user_id))
    elif user_code:
        where += ' AND s.user_code = %s'
        params.append(user_code)
    c = _conn()
    try:
        cur = c.cursor()
        cur.execute('SELECT COUNT(*) AS n FROM t_chat_session s' + where, params)
        total = int(cur.fetchone()['n'])
        limit, truncated = _cap(limit, total, max_rows)
        # 原来每行挂两个相关子查询（N 行 = 2N 次查询）；改成一个 LEFT JOIN + GROUP BY，
        # 一次性把每场会话的问答数/缺口数算好再关联。放开 limit 全量取时这是数量级的差别。
        sql = ('SELECT s.session_id, s.user_id, s.user_code, s.title, s.ai_model, s.channel, s.client_ip, '
               '       s.msg_count, s.status, s.create_time, s.last_msg_at, s.ended_at, '
               '       COALESCE(t.ask_count, 0) AS ask_count, '
               '       COALESCE(t.gap_count, 0) AS gap_count '
               'FROM t_chat_session s '
               'LEFT JOIN (SELECT chat_session_id, '
               '                  COUNT(*) AS ask_count, '
               '                  SUM(CASE WHEN is_gap = 1 THEN 1 ELSE 0 END) AS gap_count '
               '           FROM t_chat_query_trace WHERE deleted_flag = 0 '
               '           GROUP BY chat_session_id) t '
               '       ON t.chat_session_id = s.session_id' + where +
               ' ORDER BY COALESCE(s.last_msg_at, s.create_time) DESC, s.id DESC LIMIT %s OFFSET %s')
        cur.execute(sql, params + [limit, offset])
        rows = cur.fetchall()
        for r in rows:                            # datetime → 本地时间字符串，便于 JSON 输出
            for k in ('create_time', 'last_msg_at', 'ended_at'):
                if r.get(k) is not None:
                    r[k] = _local_str(r[k])
        return {'total': total, 'returned': len(rows), 'limit': limit, 'offset': offset,
                'truncated': truncated, 'sessions': rows}
    finally:
        c.close()


def get_history(session_id, limit=None, with_steps=False, max_rows=None):
    """某场会话的历史对话记录：消息 + 每条 AI 回答对应的口径/质量信息。

    消息与 trace 是 **LEFT JOIN** —— 用户那条消息没有 trace，不能丢。

    limit=None（不传）= **全部返回**；limit>0 = 取前 N 条（按 seq_no 正序，即最早的 N 条）。
    返回 dict：total 是这场会话的消息**总数**，returned 是本次实际返回条数，
    truncated 表示是否被防呆上限（config.API_MAX_ROWS）截断。默认不再有 1000 条的硬上限。
    """
    limit = norm_limit(limit)
    c = _conn()
    try:
        cur = c.cursor()
        cur.execute('SELECT session_id, user_id, user_code, title, ai_model, channel, msg_count, '
                    'status, create_time, last_msg_at, ended_at '
                    'FROM t_chat_session WHERE session_id=%s AND deleted_flag=0', (session_id,))
        session = cur.fetchone()
        if not session:
            return None
        for k in ('create_time', 'last_msg_at', 'ended_at'):
            if session.get(k) is not None:
                session[k] = _local_str(session[k])
        # total = 这场会话的消息总数（不是本页条数）；再用防呆上限算出本次实际取多少。
        cur.execute('SELECT COUNT(*) AS n FROM t_chat_message '
                    'WHERE chat_session_id=%s AND deleted_flag=0', (session_id,))
        total = int(cur.fetchone()['n'])
        limit, truncated = _cap(limit, total, max_rows)
        cur.execute(
            'SELECT m.id, m.seq_no, m.direction, m.sender_type, m.sender_id, m.reply_to_id, '
            '       m.msg_type, m.content, m.model, m.latency_ms, m.create_time, '
            '       q.id AS qa_id, q.fixed_question, q.completed_question, '
            '       q.name_fix_used, q.completion_used, q.period_type, q.period_key, '
            '       q.time_text, q.time_is_default, q.place, q.place_is_default, '
            '       q.db_query_count, q.sql_query_count, q.kb_query_count, q.steps, '
            '       q.no_db_query, q.unverified, q.is_gap, q.elapsed_ms, '
            '       q.feedback, q.feedback_note '
            'FROM t_chat_message m '
            'LEFT JOIN t_chat_query_trace q ON q.message_id = m.id AND q.deleted_flag = 0 '
            'WHERE m.chat_session_id=%s AND m.deleted_flag=0 '
            'ORDER BY m.seq_no LIMIT %s', (session_id, limit))
        msgs = cur.fetchall()
        for m in msgs:
            if m.get('create_time') is not None:
                m['create_time'] = _local_str(m['create_time'])
        if with_steps:
            ids = [m['qa_id'] for m in msgs if m.get('qa_id')]
            steps = {}
            if ids:
                marks = ','.join(['%s'] * len(ids))
                cur.execute('SELECT trace_id, step_no, step_kind, round_no, tool_name, '
                            '       sql_text, result_rows, cost_ms, model_ms, status, error_msg '
                            'FROM t_chat_trace_step WHERE trace_id IN (' + marks + ') AND deleted_flag=0 '
                            'ORDER BY trace_id, step_no', ids)
                for s in cur.fetchall():
                    steps.setdefault(s['trace_id'], []).append(s)
            for m in msgs:
                if m.get('qa_id'):
                    m['steps'] = steps.get(m['qa_id'], [])
        return {'session': session, 'total': total, 'returned': len(msgs),
                'limit': limit, 'truncated': truncated, 'messages': msgs}
    finally:
        c.close()


# ---------------------------------------------------------------- L2 会话摘要
#
# 表结构：t_chat_session.summary / summary_upto_seq / summary_time（见 tools/kb_file_schema.sql）
# 语义：summary 覆盖到「第 summary_upto_seq 条消息（按 t_chat_message.seq_no）为止」；
#       往提示里注入时只取 seq_no > summary_upto_seq 的轮次，避免摘要与明细重复。
#
# ★ 为什么不把"过滤"做进 load_context：load_context 还兼着「时间/地点沿用上一轮」的活，
#   那条链路**必须能看到紧邻的上一轮**，哪怕它已经被摘要覆盖。所以过滤放在 agent 层做。

def get_summary(session_id):
    """取某场会话的摘要。返回 {'summary','upto_seq','time'}；没有就给空值。"""
    if not session_id:
        return {'summary': '', 'upto_seq': 0, 'time': None}
    c = _conn()
    try:
        cur = c.cursor()
        cur.execute('SELECT summary, summary_upto_seq, summary_time FROM t_chat_session '
                    'WHERE session_id=%s AND deleted_flag=0', (session_id,))
        r = cur.fetchone()
    finally:
        c.close()
    if not r:
        return {'summary': '', 'upto_seq': 0, 'time': None}
    return {'summary': (r.get('summary') or '').strip(),
            'upto_seq': int(r.get('summary_upto_seq') or 0),
            'time': str(r.get('summary_time')) if r.get('summary_time') else None}


def set_summary(session_id, summary, upto_seq, expect_upto=None):
    """写回摘要。**带乐观锁**：给了 `expect_upto` 就要求当前值仍等于它。

    为什么需要锁：摘要跑在后台线程，同一场会话可能有两个任务同时在跑
    （用户连着问几句会触发两次）。没有锁，后写的会盖掉先写的，
    summary_upto_seq 也会乱跳 —— 结果就是"摘要和明细对不上"。
    """
    c = _conn()
    try:
        cur = c.cursor()
        sql = ('UPDATE t_chat_session SET summary=%s, summary_upto_seq=%s, summary_time=NOW() '
               'WHERE session_id=%s')
        params = [summary, int(upto_seq), session_id]
        if expect_upto is not None:
            sql += ' AND summary_upto_seq=%s'
            params.append(int(expect_upto))
        cur.execute(sql, params)
        return cur.rowcount
    finally:
        c.close()


def turns_after(session_id, after_seq=0, limit=40, answer_chars=300):
    """取 seq_no > after_seq 的问答轮次（旧的在前），供摘要使用。

    只取「有 AI 回答」的轮次（靠 trace 关联），正在进行的这轮不会混进来。
    """
    if not session_id:
        return []
    c = _conn()
    try:
        cur = c.cursor()
        cur.execute(
            'SELECT m.seq_no, q.raw_question, q.fixed_question, q.place, q.time_text, '
            '       m.content AS answer '
            'FROM t_chat_message m '
            'JOIN t_chat_query_trace q ON q.message_id = m.id AND q.deleted_flag = 0 '
            'WHERE m.chat_session_id=%s AND m.deleted_flag=0 AND m.sender_type=2 '
            '  AND m.seq_no > %s '
            'ORDER BY m.seq_no LIMIT %s', (session_id, int(after_seq or 0), int(limit)))
        rows = cur.fetchall()
        for r in rows:
            r['answer'] = (r.get('answer') or '')[:answer_chars]
        return rows
    finally:
        c.close()


def unsummarized_count(session_id):
    """还没被摘要覆盖的问答轮数 —— 攒够阈值才触发摘要（不是每轮都烧一次模型）。"""
    if not session_id:
        return 0
    c = _conn()
    try:
        cur = c.cursor()
        cur.execute(
            'SELECT COUNT(*) AS n FROM t_chat_message m '
            'JOIN t_chat_session s ON s.session_id = m.chat_session_id '
            'WHERE m.chat_session_id=%s AND m.deleted_flag=0 AND m.sender_type=2 '
            '  AND m.seq_no > COALESCE(s.summary_upto_seq, 0)', (session_id,))
        return int(cur.fetchone()['n'])
    finally:
        c.close()


def count_turns(session_id):
    """这场会话一共有多少轮问答（只数有 AI 回答的那一侧）。

    用途：判断会话够不够长、值不值得注入【本场会话摘要】——
    短会话的 history 已经把全场带上了，再注入摘要是重复（见 agent._summary_worth_it）。
    """
    if not session_id:
        return 0
    c = _conn()
    try:
        cur = c.cursor()
        cur.execute('SELECT COUNT(*) AS n FROM t_chat_message '
                    'WHERE chat_session_id=%s AND deleted_flag=0 AND sender_type=2',
                    (session_id,))
        return int(cur.fetchone()['n'])
    finally:
        c.close()


def sessions_needing_summary(every=5, limit=20):
    """找出「未摘要轮数 >= every」的会话（服务重启后补跑用）。"""
    c = _conn()
    try:
        cur = c.cursor()
        cur.execute(
            # 迁 PG：HAVING 不能引用 select 别名（MySQL 可以），包一层子查询，语义完全一致。
            'SELECT t.session_id FROM ('
            '  SELECT s.session_id, s.last_msg_at, '
            '         (SELECT COUNT(*) FROM t_chat_message m '
            '           WHERE m.chat_session_id = s.session_id AND m.deleted_flag=0 '
            '             AND m.sender_type=2 AND m.seq_no > COALESCE(s.summary_upto_seq, 0)) AS pending '
            '  FROM t_chat_session s WHERE s.deleted_flag=0'
            ') t WHERE t.pending >= %s ORDER BY t.last_msg_at DESC LIMIT %s',
            (int(every), int(limit)))
        return [r['session_id'] for r in cur.fetchall()]
    finally:
        c.close()


def set_feedback(qa_id, feedback, note=None):
    """给某次回答打评价（1赞 / 0踩）。返回受影响行数。"""
    fb = 1 if int(feedback) == 1 else 0
    c = _conn()
    try:
        cur = c.cursor()
        cur.execute('UPDATE t_chat_query_trace SET feedback=%s, feedback_note=%s, feedback_time=NOW() '
                    'WHERE id=%s AND deleted_flag=0', (fb, (note or None), int(qa_id)))
        return cur.rowcount
    finally:
        c.close()


def stats():
    """记录库概览（供页面/运维看）。"""
    c = _conn()
    try:
        cur = c.cursor()
        out = {}
        for name, sql in [
            ('sessions', 'SELECT COUNT(*) AS n FROM t_chat_session WHERE deleted_flag=0'),
            ('messages', 'SELECT COUNT(*) AS n FROM t_chat_message WHERE deleted_flag=0'),
            ('questions', 'SELECT COUNT(*) AS n FROM t_chat_query_trace WHERE deleted_flag=0'),
            ('steps', 'SELECT COUNT(*) AS n FROM t_chat_trace_step WHERE deleted_flag=0'),
            ('gaps', 'SELECT COUNT(*) AS n FROM t_chat_query_trace WHERE is_gap=1 AND deleted_flag=0'),
            ('unverified', 'SELECT COUNT(*) AS n FROM t_chat_query_trace WHERE unverified=1 AND deleted_flag=0'),
            ('name_fix_hit', 'SELECT COUNT(*) AS n FROM t_chat_query_trace WHERE name_fix_used=1 AND deleted_flag=0'),
        ]:
            cur.execute(sql)
            out[name] = int(cur.fetchone()['n'])
        return out
    finally:
        c.close()


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    print('SECRETARY_QA_LOG 开启：', _enabled())
    print('记录库：', config.AGENT_DB)
    print('状态：', status())
    if _enabled() and status()['available']:
        print('统计：', stats())
        r = list_sessions(limit=5)
        print('\n最近 5 场会话（库里共 %d 场）：' % r['total'])
        for s in r['sessions']:
            print('   %s | %s | %s 问 | %s' % (s['session_id'], s['title'], s['ask_count'], s['last_msg_at']))
