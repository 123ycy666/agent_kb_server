"""规则文档：模板、CRUD、版本。"""
from __future__ import annotations

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.workflow.models import DocSchedule, DocVersion, RuleDoc, Schedule

DETECT_SECTIONS = [
    ('goal', '一 目的', '这份文档要查出什么、算到哪个对象、最终在结果表里落成什么、跑完应该看到什么样子。只写目的，不写方法。',
     '（示例：要查的那件事 + 算到哪个对象 + 结果表里一行是什么 + 跑完大约多少行）'),
    ('what', '二 管什么', '这条判据盯的是哪件事、算到哪一层。一句话说清，不写方法。',
     '（示例：一句话说清盯什么、算到哪一层）'),
    ('data', '三 看哪些数据', '哪张表、哪几列、只取哪些行、怎么关联、有什么坑（单位、排序规则、缺数据怎么办）。',
     '（示例：表 + 列 + 过滤条件 + 关联键 + 已知的坑）'),
    ('calc', '四 怎么算', '那个数怎么算出来：时间口径、按什么分组、一个对象一条。',
     '（示例：公式 + 时间口径 + 分组方式）'),
    ('judge', '五 多少算异常', '比较方式、阈值、分档；多少以上不报也写在这一节。',
     '（示例：阈值 + 比较方式 + 分档界线）'),
    ('store', '六 存什么数据', '每个对象要交出去的内容：对象、归属单位、上级单位、当前值与单位、阈值、档位、明细几项、依据几项、可靠性。只写内容。',
     '（示例：对象=什么，归属=哪个单位，当前值=什么配什么单位，明细=几项，依据=几项）'),
]

SEND_SECTIONS = [
    ('goal', '一 目的', '这份文档把哪份取数文档判出来的结果、按哪几层、变成一句什么话、发给谁、产出在哪；跑完是什么样子。',
     '（示例：把某份结果按几层各写一句发出去，产出每人一条通知）'),
    ('source', '二 读哪些数据', '从结果表读、按哪份取数文档过滤、取哪些列、以哪个时间字段为准、排序与翻页。只写"读什么""怎么取"。',
     '（示例：按文档名过滤 + 取哪几列 + 只看今天 + 超过多少条就翻页）'),
    ('fields', '三 字段是什么意思', '要用到的每一列是什么含义；值单位怎么读；明细里有哪些键。',
     '（示例：每一列的语义，值怎么配单位读）'),
    ('rule', '四 怎么判断该不该发', '什么条件成立才发；以及按什么归到各层。',
     '（示例：状态 + 可靠性 + 档位 + 今天命中；按哪个字段归到哪几层）'),
    ('who', '五 发给谁', '层面 → 岗位 → find_user 参数；查到多个取哪个；查不到怎么办。',
     '（示例：每层找哪个岗位，用单位与岗位关键字查人）'),
    ('phrase', '六 一句话怎么写', '提示词：字数上限、只放一个数字、各层句式、禁止什么、正反例各一条。',
     '（示例：字数 + 一个数字 + 各层句式 + 禁止项 + 一条正例）'),
    ('delivery', '七 怎么存', '用哪只工具写、每个参数取哪个值、返回什么算成功。频率归定时方案。',
     '（示例：用 send 工具，各参数取哪一节的什么值）'),
]

TEMPLATES = {
    'detect': {'label': '取数工作流', 'sections': DETECT_SECTIONS},
    'send': {'label': '发送工作流', 'sections': SEND_SECTIONS},
}

WORK_TYPES = ('detect', 'send')


def sections_of(work_type: str) -> list[tuple]:
    tpl = TEMPLATES.get(work_type) or TEMPLATES['detect']
    return tpl['sections']


def blank_doc(work_type: str) -> dict:
    return {k: '' for k, *_ in sections_of(work_type)}


def missing_sections(work_type: str, doc: dict) -> list[str]:
    return [t for k, t, *_ in sections_of(work_type) if not str((doc or {}).get(k) or '').strip()]


def list_docs(db: Session, include_deleted: bool = False, work_type: str | None = None) -> list[dict]:
    where = [] if include_deleted else [RuleDoc.deleted_flag == 0]
    if work_type:
        where.append(RuleDoc.work_type == work_type)
    rows = db.execute(select(RuleDoc).where(*where).order_by(RuleDoc.id)).scalars().all()
    return [dict_of(r) for r in rows]


def dict_of(d: RuleDoc) -> dict:
    missing = missing_sections(d.work_type, d.doc or {})
    return {
        'id': d.id, 'name': d.name, 'work_type': d.work_type,
        'work_type_label': (TEMPLATES.get(d.work_type) or {}).get('label', d.work_type),
        'alert_type': d.alert_type, 'enabled': bool(d.enabled),
        'disabled_reason': d.disabled_reason, 'scope_level': d.scope_level,
        'schedule_id': d.schedule_id, 'pair_doc_id': d.pair_doc_id,
        'doc': d.doc or {}, 'version': d.version,
        'updated_by': d.updated_by,
        'updated_at': d.updated_at.isoformat() if d.updated_at else None,
        'missing': missing, 'complete': not missing,
    }


def get_doc(db: Session, doc_id: int) -> RuleDoc | None:
    d = db.get(RuleDoc, int(doc_id))
    return d if d is not None and not d.deleted_flag else None


def create_doc(db: Session, *, name: str, work_type: str, alert_type: str = '',
               scope_level: str = 'city', doc: dict | None = None,
               pair_doc_id: int | None = None,
               updated_by: str = '') -> RuleDoc:
    d = RuleDoc(name=name[:120], work_type=(work_type if work_type in WORK_TYPES else 'detect'),
                alert_type=(alert_type or '')[:64], scope_level=scope_level or 'city',
                pair_doc_id=pair_doc_id,
                doc=doc if doc is not None else blank_doc(work_type), updated_by=updated_by)
    db.add(d)
    db.commit()
    return d


def save_doc(db: Session, d: RuleDoc, *, name: str | None = None, doc: dict | None = None,
             alert_type: str | None = None, scope_level: str | None = None,
             schedule_id=('KEEP',), pair_doc_id=('KEEP',), enabled: bool | None = None,
             disabled_reason: str | None = None, updated_by: str = '') -> RuleDoc:
    if name:
        d.name = name[:120]
    if alert_type is not None:
        d.alert_type = alert_type[:64]
    if scope_level:
        d.scope_level = scope_level
    if schedule_id != ('KEEP',):
        d.schedule_id = int(schedule_id) if schedule_id not in (None, '', 0) else None
    if pair_doc_id != ('KEEP',):
        d.pair_doc_id = int(pair_doc_id) if pair_doc_id not in (None, '', 0) else None
    if enabled is not None:
        d.enabled = 1 if enabled else 0
    if disabled_reason is not None:
        d.disabled_reason = disabled_reason
    if doc is not None:
        d.doc = doc
        d.version = int(d.version or 1) + 1
        db.add(DocVersion(doc_id=d.id, version=d.version, doc=doc, updated_by=updated_by))
    d.updated_by = updated_by or d.updated_by
    db.commit()
    return d


def versions(db: Session, doc_id: int) -> list[dict]:
    rows = db.execute(select(DocVersion).where(DocVersion.doc_id == int(doc_id))
                      .order_by(DocVersion.version.desc()).limit(50)).scalars().all()
    return [{'version': v.version, 'updated_by': v.updated_by,
             'at': v.created_at.isoformat() if v.created_at else None} for v in rows]


def export_markdown(d: RuleDoc) -> str:
    """导出成 markdown：标题 + 八节/六节正文，可直接审阅、也可再导回来。"""
    tpl = TEMPLATES.get(d.work_type) or TEMPLATES['detect']
    lines = ['# %s' % d.name, '', '> 工种：%s　版本：%d' % (tpl['label'], int(d.version or 1)), '']
    for key, title, _hint, _ex in sections_of(d.work_type):
        lines.append('## ' + title)
        lines.append('')
        lines.append(str((d.doc or {}).get(key) or '').strip())
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def import_markdown(d: RuleDoc, text: str) -> dict:
    """从 markdown 导回来：按 `## 标题` 切节，标题对上就覆盖那一节，对不上忽略。"""
    title_map = {t: k for k, t, *_ in sections_of(d.work_type)}
    title_map.update({t.split(' ', 1)[-1]: k for k, t, *_ in sections_of(d.work_type)})
    out = dict(d.doc or {})
    cur, buf = None, []
    for line in str(text or '').splitlines():
        if line.startswith('## '):
            if cur:
                out[cur] = '\n'.join(buf).strip()
            name = line[3:].strip()
            cur, buf = title_map.get(name), []
            continue
        if cur:
            buf.append(line)
    if cur:
        out[cur] = '\n'.join(buf).strip()
    return out


def duplicate(db: Session, d: RuleDoc, name: str = '', updated_by: str = '') -> RuleDoc:
    base = (name or (d.name + ' 副本'))[:120]
    cand, i = base, 2
    while db.execute(select(RuleDoc).where(RuleDoc.name == cand)).scalars().first() is not None:
        cand = ('%s %d' % (base, i))[:120]
        i += 1
    nd = RuleDoc(name=cand, work_type=d.work_type, alert_type=d.alert_type,
                 scope_level=d.scope_level, doc=dict(d.doc or {}), enabled=0,
                 schedule_id=None, updated_by=updated_by)
    db.add(nd)
    db.commit()
    return nd


def version_doc(db: Session, doc_id: int, version: int) -> dict | None:
    v = db.execute(select(DocVersion).where(DocVersion.doc_id == int(doc_id),
                                            DocVersion.version == int(version))).scalars().first()
    return v.doc if v else None


def delete_doc(db: Session, d: RuleDoc) -> None:
    d.deleted_flag = 1
    db.commit()


def docs_of_schedule(db: Session, schedule_id: int) -> list[RuleDoc]:
    ids = [r for r in db.execute(select(DocSchedule.doc_id)
                                 .where(DocSchedule.schedule_id == int(schedule_id)))
           .scalars().all()]
    if not ids:
        return []
    return db.execute(select(RuleDoc).where(RuleDoc.id.in_(ids),
                                            RuleDoc.deleted_flag == 0)).scalars().all()


def schedules_of_doc(db: Session, doc_id: int) -> list[Schedule]:
    ids = [r for r in db.execute(select(DocSchedule.schedule_id)
                                  .where(DocSchedule.doc_id == int(doc_id))).scalars().all()]
    if not ids:
        return []
    return db.execute(select(Schedule).where(Schedule.id.in_(ids))).scalars().all()


def set_schedule_docs(db: Session, schedule_id: int, doc_ids) -> None:
    """把方案的挂载文档整体替换成给定的这一批。"""
    want = {int(x) for x in (doc_ids or [])}
    cur = {int(x) for x in db.execute(select(DocSchedule.doc_id)
                                      .where(DocSchedule.schedule_id == int(schedule_id)))
           .scalars().all()}
    for d in cur - want:
        db.execute(text('DELETE FROM wf_doc_schedule WHERE schedule_id=:s AND doc_id=:d'),
                   {'s': int(schedule_id), 'd': int(d)})
    for d in want - cur:
        db.add(DocSchedule(doc_id=int(d), schedule_id=int(schedule_id)))
    db.commit()


def unlink_doc(db: Session, doc_id: int) -> None:
    """把一份文档从所有方案上摘掉（停用文档时用）。"""
    db.execute(text('DELETE FROM wf_doc_schedule WHERE doc_id=:d'), {'d': int(doc_id)})
    db.commit()


def count_docs(db: Session) -> int:
    return int(db.execute(select(func.count()).select_from(RuleDoc)
                          .where(RuleDoc.deleted_flag == 0)).scalar_one())

# ── 文档导入导出（业务正文只在库里，代码里不放） ──────────────────────
def export_json(db, path: str) -> int:
    import json
    rows = db.execute(select(RuleDoc).where(RuleDoc.deleted_flag == 0)
                      .order_by(RuleDoc.id)).scalars().all()
    names = {r.id: r.name for r in rows}
    out = [{'name': r.name, 'work_type': r.work_type, 'alert_type': r.alert_type,
            'scope_level': r.scope_level, 'enabled': bool(r.enabled),
            'pair_name': names.get(r.pair_doc_id), 'doc': r.doc or {}} for r in rows]
    with open(path, 'w', encoding='utf-8') as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2))
    return len(out)


def import_json(db, path: str) -> dict:
    import json
    with open(path, encoding='utf-8') as f:
        items = json.loads(f.read())
    created = updated = 0
    for it in items:
        d = db.execute(select(RuleDoc).where(RuleDoc.name == it['name'])).scalars().first()
        if d is None:
            d = RuleDoc(name=it['name'], work_type=it.get('work_type') or 'detect', doc={})
            db.add(d)
            created += 1
        else:
            updated += 1
        d.work_type = it.get('work_type') or 'detect'
        d.alert_type = it.get('alert_type') or ''
        d.scope_level = it.get('scope_level') or 'station'
        d.enabled = 1 if it.get('enabled', True) else 0
        d.deleted_flag = 0
        d.doc = it.get('doc') or {}
        db.commit()
        has = db.execute(select(DocVersion).where(DocVersion.doc_id == d.id,
                                                  DocVersion.version == int(d.version or 1))).scalars().first()
        if has is None:
            db.add(DocVersion(doc_id=d.id, version=int(d.version or 1), doc=d.doc or {},
                              updated_by='import'))
            db.commit()
    for it in items:
        if not it.get('pair_name'):
            continue
        d = db.execute(select(RuleDoc).where(RuleDoc.name == it['name'])).scalars().first()
        p = db.execute(select(RuleDoc).where(RuleDoc.name == it['pair_name'])).scalars().first()
        if d is not None and p is not None:
            d.pair_doc_id = p.id
            db.commit()
    return {'created': created, 'updated': updated}
