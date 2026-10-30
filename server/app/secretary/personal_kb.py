# -*- coding: utf-8 -*-
"""个人知识库：每个员工自己的资料库（模板、常用材料、自己的口径笔记）。

它与公司知识库的关系（2026-10-21）
--------------------------------
- **同一套底层**（`knowledge_base` + `document` + 切片 + 向量检索），不另造一套；
- 区别只有两条：`knowledge_base.created_by = 本人`、文件归属本人；
- 检索时把"我的资料库"额外加进范围（永远可检索自己的），**绝不进入别人的范围**。

为什么复用而不是新建表：分块、向量化、权限过滤、冲突检测都已经写好了；
再造一套"个人库"等于把同一堆坑再踩一遍，而且两边数据会分裂。

本模块只做四件事：确保库存在、列文件、统计、给检索用的 kb_id。
"""
from __future__ import annotations

from app.secretary import pgdb

KB_NAME = '我的资料库'
KB_DESC = '个人资料库：本人上传的模板、常用材料与笔记（仅本人可见）'
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


def _embedding() -> tuple:
    """取系统当前 embedding 模型名与维度（与公司库同空间）。"""
    try:
        from app.providers import current_embedding_model_name, get_providers
        name = current_embedding_model_name()
        dim = int(get_providers().embedding.dim)
        return name, dim
    except Exception:                          # noqa: BLE001
        return 'bge-m3', 1024


class PersonalKbError(RuntimeError):
    pass


def _conn():
    try:
        return pgdb.connect(autocommit=True)
    except Exception as exc:                       # noqa: BLE001
        raise PersonalKbError('数据库不可用：%s' % str(exc)[:120]) from exc


def _val(row, key: str, idx: int):
    if row is None:
        return None
    return row.get(key) if isinstance(row, dict) else row[idx]


def ensure(user_id: int, user_code: str | None = None) -> int:
    """拿到（必要时创建）本人的资料库，返回 kb_id。"""
    if not user_id:
        raise PersonalKbError('缺少 user_id')
    conn = _conn()
    try:
        cur = conn.cursor()
        try:
            cur.execute('SELECT id FROM knowledge_base WHERE created_by=%s AND name=%s '
                        'ORDER BY id LIMIT 1', (int(user_id), KB_NAME))
            row = cur.fetchone()
            if row:
                return int(_val(row, 'id', 0))
            # embedding 三元组是 NOT NULL：沿用系统当前的 embedding 模型与分块参数，
            # 与公司库保持同一个向量空间（模型不一致会导致检索串味）。
            model_name, dim = _embedding()
            cur.execute('INSERT INTO knowledge_base (name, description, embedding_model, '
                        'embedding_dim, chunk_size, chunk_overlap, status, created_by, qa_enabled) '
                        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)',
                        (KB_NAME, KB_DESC, model_name, dim, CHUNK_SIZE, CHUNK_OVERLAP,
                         'active', int(user_id), False))
            new_id = getattr(cur, 'lastrowid', None)
            if not new_id:
                cur.execute('SELECT id FROM knowledge_base WHERE created_by=%s AND name=%s '
                            'ORDER BY id DESC LIMIT 1', (int(user_id), KB_NAME))
                new_id = _val(cur.fetchone(), 'id', 0)
            return int(new_id)
        finally:
            cur.close()
    finally:
        try:
            conn.close()
        except Exception:                          # noqa: BLE001
            pass


def list_docs(user_id: int, limit: int = 50) -> list[dict]:
    """本人资料库里的文件（只读；不含别人上传的）。"""
    conn = _conn()
    try:
        cur = conn.cursor()
        try:
            cur.execute('SELECT d.id, d.filename, d.file_ext, d.file_size, d.parse_status, '
                        'd.chunk_count, d.created_at, COALESCE(d.doc_type, %s) AS doc_type '
                        'FROM document d JOIN knowledge_base k ON k.id = d.kb_id '
                        'WHERE k.created_by=%s AND k.name=%s AND d.is_active '
                        'ORDER BY d.id DESC LIMIT %s',
                        ('doc', int(user_id), KB_NAME, int(limit)))
            rows = cur.fetchall() or []
        finally:
            cur.close()
    finally:
        try:
            conn.close()
        except Exception:                          # noqa: BLE001
            pass
    out = []
    for r in rows:
        out.append({'id': _val(r, 'id', 0), 'filename': _val(r, 'filename', 1),
                    'ext': _val(r, 'file_ext', 2), 'size': _val(r, 'file_size', 3),
                    'parse_status': _val(r, 'parse_status', 4),
                    'chunks': _val(r, 'chunk_count', 5),
                    'type': _val(r, 'doc_type', 7) or 'doc',
                    'created_at': str(_val(r, 'created_at', 6))})
    return out


def stats(user_id: int) -> dict:
    docs = list_docs(user_id, limit=500)
    return {'kb_name': KB_NAME, 'files': len(docs),
            'chunks': sum(int(d.get('chunks') or 0) for d in docs),
            'parsed': sum(1 for d in docs if (d.get('parse_status') or '') == 'success')}


def kb_id_for(user_id: int) -> int:
    """检索用：本人的 kb_id（确保存在）。"""
    return ensure(int(user_id))


def with_personal(scope, user_id: int, user_code: str | None = None) -> list[int]:
    """把"我的资料库"并进检索范围（去重、保序）。

    只并本人库；库不存在时按需建；数据库故障时**原样返回**，不阻断问答。
    """
    base = [int(x) for x in (scope or []) if str(x).strip().isdigit()]
    try:
        mine = ensure(int(user_id), user_code)
    except Exception as exc:                       # noqa: BLE001
        print('[personal_kb] 并入失败（不影响回答）：%s' % str(exc)[:120])
        return base
    return base if mine in base else base + [int(mine)]


def personal_rules(user_id: int, limit: int = 1500) -> dict:
    """本人资料库里 **规则类**（`doc_type='rule'`）的内容，供每轮注入。

    为什么要它（2026-10-30）：个人库原先只有"资料"一条通道（靠向量检索碰运气），
    规则类内容永远进不了模型。现在按类型分开：
      - rule → 每轮注入（本函数），限量 1500 字，超了按文件更新顺序取前面的
      - doc  → 仍走检索（`with_personal` 已把本人库并进范围）

    返回 {'text','files':[...],'chars','overflow'}；没有规则时 text=''。
    任何失败都返回空 —— 个人规则读不到不影响问答。
    """
    out = {'text': '', 'files': [], 'chars': 0, 'overflow': ''}
    try:
        kid = ensure(int(user_id))
    except Exception as exc:                       # noqa: BLE001
        print('[personal_kb] 个人规则取库失败（忽略）：%s' % str(exc)[:120])
        return out
    conn = _conn()
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT d.id, d.filename, c.content FROM document_chunk c "
                "JOIN document d ON d.id = c.doc_id "
                "WHERE d.kb_id=%s AND d.is_active AND c.is_retrievable AND d.doc_type='rule' "
                "ORDER BY d.id, c.chunk_index", (int(kid),))
            rows = cur.fetchall() or []
        finally:
            cur.close()
    finally:
        try:
            conn.close()
        except Exception:                          # noqa: BLE001
            pass

    if not rows:
        return out
    blocks: list[tuple[str, str]] = []
    for r in rows:
        did = _val(r, 'id', 0)
        name = str(_val(r, 'filename', 1) or '')
        body = str(_val(r, 'content', 2) or '').strip()
        if body.startswith('【'):
            nl = body.find('】')
            if 0 < nl < 200:
                body = body[nl + 1:].lstrip('\n')
        if blocks and blocks[-1][0] == name:
            blocks[-1] = (name, blocks[-1][1] + '\n' + body)
        else:
            blocks.append((name, body))
    parts: list[str] = []
    used = 0
    skipped: list[str] = []
    for name, body in blocks:
        piece = '《%s》\n%s' % (name, body)
        if used + len(piece) > limit:
            skipped.append(name)
            continue
        parts.append(piece)
        used += len(piece)
    if not parts:
        return out
    out['text'] = ('【S7 我的规则（本人上传，优先于公司通用口径；不改阈值与红线）】\n'
                   + '\n\n'.join(parts))
    out['files'] = [name for name, _ in blocks if name not in skipped]
    out['chars'] = len(out['text'])
    if skipped:
        out['overflow'] = ('你的规则合计超过 %d 字，以下未注入：%s'
                           % (limit, '、'.join(skipped)))
    return out
