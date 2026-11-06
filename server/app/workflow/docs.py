"""规则文档：模板、CRUD、版本。"""
from __future__ import annotations

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.workflow.models import DocSchedule, DocVersion, RuleDoc, Schedule

DETECT_SECTIONS = [
    ('what', '一 管什么', '这条预警盯的是哪件事、算到哪一层。一句话说清，不要写方法。',
     '哪个所今年的预算到年底花不完。算到供电所，一个所一条。'),
    ('data', '二 看哪些数据', '哪张表、哪几列是什么意思、只取哪些行、有什么坑（单位、怎么关联、缺数据怎么办）。',
     '表 A 存分配额（列 X 是所、列 Y 是金额，单位元）；表 B 存工单（列 Z 是是否审批通过）。只取未删除的行。'),
    ('calc', '三 怎么算', '那个数怎么算出来：时间口径、按什么分组、一个对象一条还是并一条。',
     '执行率 = 已花 / 分配；按时间进度外推到年底，得到预计执行率。'),
    ('judge', '四 多少算异常', '比较方式、阈值、分档；多少以上不报也写在这一节。',
     '预计执行率 < 0.6 报；< 0.3 为高档，其余中档；0.6 及以上不报。'),
    ('store', '五 存什么数据', '每条命中要交出去的内容：对象和键、当前值、明细里放哪几项、依据指哪几列。只写内容，不写表名字段名。',
     '对象=所名，键=所id；当前值=预计执行率；明细=分配额、已花、执行率、预计结余；依据=分配额与已审批金额两列。'),
]

SEND_SECTIONS = [
    ('what', '一 管什么', '这个发送工作流每天几点做什么。',
     '每天 08:30 读未关闭事项，整理成一句话发给对应的人。'),
    ('source', '二 读什么', '读哪些事项、怎么排序、取多少条。',
     '读所有未关闭事项，按最近命中倒序，取前 200 条。'),
    ('who', '三 发给谁、说什么', '按什么规则找人（写成话）、一句话怎么组织；查不到人怎么办。',
     '所级事项发该所负责人；一句话 = 对象 + 事实 + 关键数 + 档位。查不到人不发并写明理由。'),
    ('cadence', '四 发几次、什么不发', '多久一次、静默期、升档补发、关闭后怎么发、已发过的不重发。',
     '同一事项同一人 1 天内只发一次；档位升档补发；关闭不发。'),
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
        'schedule_id': d.schedule_id, 'doc': d.doc or {}, 'version': d.version,
        'updated_by': d.updated_by,
        'updated_at': d.updated_at.isoformat() if d.updated_at else None,
        'missing': missing, 'complete': not missing,
    }


def get_doc(db: Session, doc_id: int) -> RuleDoc | None:
    d = db.get(RuleDoc, int(doc_id))
    return d if d is not None and not d.deleted_flag else None


def create_doc(db: Session, *, name: str, work_type: str, alert_type: str = '',
               scope_level: str = 'city', doc: dict | None = None,
               updated_by: str = '') -> RuleDoc:
    d = RuleDoc(name=name[:120], work_type=(work_type if work_type in WORK_TYPES else 'detect'),
                alert_type=(alert_type or '')[:64], scope_level=scope_level or 'city',
                doc=doc if doc is not None else blank_doc(work_type), updated_by=updated_by)
    db.add(d)
    db.commit()
    return d


def save_doc(db: Session, d: RuleDoc, *, name: str | None = None, doc: dict | None = None,
             alert_type: str | None = None, scope_level: str | None = None,
             schedule_id=('KEEP',), enabled: bool | None = None,
             disabled_reason: str | None = None, updated_by: str = '') -> RuleDoc:
    if name:
        d.name = name[:120]
    if alert_type is not None:
        d.alert_type = alert_type[:64]
    if scope_level:
        d.scope_level = scope_level
    if schedule_id != ('KEEP',):
        d.schedule_id = int(schedule_id) if schedule_id not in (None, '', 0) else None
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
