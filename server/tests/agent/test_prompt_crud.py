# -*- coding: utf-8 -*-
"""提示词材料 增 / 删 / 改 / 查 的极限条件验收（2026-11-02）。

跑法：`py -X utf8 server/tests/agent/test_prompt_crud.py`
**不需要外部数据库**：用 `pgserver`（自带 PG 16.2 二进制）在本机起一个临时实例，
建与 `db_init.py` 一致的 DDL，然后拿真 SQL 打极限条件。

为什么值得单独一层：这些页面动作是"改一条就影响下一轮问答"的写操作，而它们
在真 PG 上的行为跟本地假库不一样 —— 下面这些 bug 全是**只在真库上现形**的：
    ① `pgdb` 给所有 INSERT 追加 `RETURNING id`，而 `prompt_text` 主键是 `k`、
       没有 id 列 → 页面「身份 / 模式做法 / 词表」四个覆盖项**一直保存失败**
       （线上 prompt_text 至今 0 行就是这条）。
    ② `replace_slot` 先 DELETE 再逐行 INSERT、autocommit → 第三行写不进去时
       原内容已被删、新内容只写了一半（服务器实测：320 字的自检清单变成「测试一/测试二」）。
    ③ `complete_rules.save` 同一个病：priority 传非数字 → **整张补全表被清空**，
       而且失败路径不清缓存，页面继续显示旧行（看着没事）。
    ④ `delete_section` 不看 rowcount，删不存在的 id 也回「成功」。
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import traceback
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVER))

import pgserver                                          # noqa: E402
import psycopg                                           # noqa: E402

_checks: list[tuple[str, bool, str]] = []


def chk(label: str, cond: bool, extra: str = '') -> None:
    _checks.append((label, bool(cond), str(extra)[:200]))


DDL = """
CREATE TABLE IF NOT EXISTS prompt_material (
  id bigserial PRIMARY KEY,
  slot varchar(16) NOT NULL,
  section varchar(160) NOT NULL,
  body text NOT NULL,
  sort_no int NOT NULL DEFAULT 0,
  enabled boolean NOT NULL DEFAULT true,
  version_no int NOT NULL DEFAULT 1,
  updated_by varchar(64) NOT NULL DEFAULT '',
  updated_at timestamptz NOT NULL DEFAULT now(),
  pinned boolean NOT NULL DEFAULT true);
CREATE INDEX IF NOT EXISTS idx_material_slot ON prompt_material (slot, enabled, sort_no);
CREATE TABLE IF NOT EXISTS complete_rule (
  id bigserial PRIMARY KEY,
  kind varchar(16) NOT NULL DEFAULT 'norm',
  match_text varchar(200) NOT NULL,
  fill_text varchar(300) NOT NULL,
  priority int NOT NULL DEFAULT 100,
  enabled boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS prompt_text (
  k varchar(64) PRIMARY KEY,
  body text NOT NULL,
  version_no int NOT NULL DEFAULT 1,
  updated_by varchar(64) NOT NULL DEFAULT '',
  updated_at timestamptz NOT NULL DEFAULT now());
"""

CASES_MD = [
    ('空文本', '', []),
    ('没有标题', '一段说明文字', [('全文', '一段说明文字')]),
    ('只有一级标题', '# 总标题\n正文', [('全文', '# 总标题\n正文')]),
    ('两个二级标题', '## 甲\n甲的正文\n## 乙\n乙的正文', [('甲', '甲的正文'), ('乙', '乙的正文')]),
    ('标题下没正文被跳过', '## 空的\n\n## 有正文\n正文', [('有正文', '正文')]),
    ('正文里带标题符号', '## 甲\n正文里有 ## 但不是行首', [('甲', '正文里有 ## 但不是行首')]),
]


def main() -> int:
    print('提示词材料 增删改查 极限验收（本机临时 PG）')
    uri = None
    db = None
    try:
        db = pgserver.get_server(Path(tempfile.gettempdir()) / 'pgsrv_prompt_test')
        uri = db.get_uri()
    except Exception as exc:                              # noqa: BLE001
        print('  [SKIP] 起不了内嵌 PG：%s: %s' % (type(exc).__name__, str(exc)[:160]))
        return 0
    conn = psycopg.connect(uri, autocommit=True)
    conn.execute(DDL)
    conn.close()
    print('  PG = %s' % uri.rsplit('@', 1)[-1])

    # pgdb 的 DSN 在 import 时算好，必须先设 PG_DSN
    import os
    os.environ['PG_DSN'] = uri

    from app.secretary import prompt_material as pm, prompt_text

    # ★ 2026-11-03（Fred）：主分支空壳 —— `prompt_material.SLOTS` 已置空，
    #   补全表（`complete_rules`）整个删除。这个测试验的是**材料读写机制**，
    #   所以本地把四份临时开回来（不改仓库配置）。
    pm.SLOTS = ('fetch', 'judge', 'answer', 'check')

    def q(sql, params=()):
        c = psycopg.connect(uri, autocommit=True)
        try:
            cur = c.execute(sql, params)
            return [dict(zip([d.name for d in cur.description], r)) for r in cur.fetchall()]
        finally:
            c.close()

    def n_material():
        return q('select count(*) n from prompt_material')[0]['n']

    def reset():
        c = psycopg.connect(uri, autocommit=True)
        c.execute('DELETE FROM prompt_material; DELETE FROM prompt_text;')
        c.close()
        pm.clear_cache(); prompt_text.clear_cache()
        pm.add_section('fetch', '甲、门槛', '正文甲', pinned=True)
        pm.add_section('fetch', '乙、可选', '正文乙', pinned=False)
        pm.add_section('judge', '丙、判据', '正文丙', pinned=True)

    # ── 查 ───────────────────────────────────────────────────
    reset()
    chk('查：未知 slot 返回空', pm.sections('nope') == [])
    chk('查：slot 大小写敏感', pm.sections('FETCH') == [])
    chk('查：正常 slot 2 条', len(pm.sections('fetch')) == 2, len(pm.sections('fetch')))
    chk('查：text 只拼必带', pm.text('fetch') == '正文甲', repr(pm.text('fetch')))
    chk('查：text(False) 拼全部', '正文乙' in pm.text('fetch', pinned_only=False))
    chk('查：清单只列非必带',
        pm.index_text(('fetch',)).endswith('- 乙、可选'), repr(pm.index_text(('fetch',))))
    chk('查：block 带标记', pm.block(('fetch',)).startswith(pm.MARK))
    chk('查：全非必带的份 block 不空（有清单）',
        pm.block(('fetch',), pinned_only=True) != '')
    chk('查：没有内容的份 block 为空串', pm.block(('answer',)) == '')
    chk('查：strip_block 无标记原样返回', pm.strip_block('abc') == 'abc')
    chk('查：strip_block 从标记处截断', pm.strip_block('abc' + pm.MARK + 'x') == 'abc')
    chk('查：正文恰好含标记会被截（已知边界，钉住现状）',
        pm.strip_block('说明' + pm.MARK) == '说明')
    # ★ 2026-11-05：材料是经 `prompt_stack._block()` 进 system 的，它会 strip() 正文，
    #   MARK 的前导空行被吃掉；只认 MARK 就切不掉，收尾那次仍背 18k 查数口径。
    chk('查：前导空行被 strip 后仍能截断',
        pm.strip_block('说明' + pm.MARK.strip() + '材料正文') == '说明')
    from app.secretary import prompt_stack
    _sysm = prompt_stack.build(mode='data', rules_menu=pm.block(('fetch',)))['system']
    chk('查：真实装配出的 system 能切回基础提示词（材料在、切完不在）',
        '正文甲' in _sysm and '正文甲' not in pm.strip_block(_sysm),
        pm.strip_block(_sysm)[-40:])
    chk('查：summary 固定四份',
        [x['slot'] for x in pm.summary()] == ['fetch', 'judge', 'answer', 'check'])

    # ── 增 ───────────────────────────────────────────────────
    reset()
    chk('增：空标题被拒', pm.add_section('fetch', '   ').get('error') == '节标题不能为空')
    chk('增：未知 slot 被拒', 'error' in pm.add_section('nope', 'x'))
    pm.add_section('fetch', '长' * 300, '正文')
    got = [r for r in pm.sections('fetch') if r['section'].startswith('长长长')][0]
    chk('增：300 字标题截到 160', len(got['section']) == 160, len(got['section']))
    big = '字' * 1_000_000
    pm.add_section('check', '百万字', big)
    got = [r for r in pm.sections('check') if r['section'] == '百万字'][0]
    chk('增：100 万字符正文存得下、读得回', len(got['body']) == 1_000_000, len(got['body']))
    chk('增：正文含 NUL 返回 error（不抛、不崩）',
        'error' in pm.add_section('check', '含空字符', 'a\x00b'))
    pm.add_section('fetch', '甲、门槛', '重名正文')
    chk('增：允许重名（表上没有唯一约束）',
        len([r for r in pm.sections('fetch') if r['section'] == '甲、门槛']) == 2)
    chk('增：新节自动排到最后',
        pm.sections('fetch')[-1]['section'] in ('甲、门槛', '长' * 160),
        [r['section'][:6] for r in pm.sections('fetch')])

    # ── 改 ───────────────────────────────────────────────────
    reset()
    sid = pm.sections('fetch')[0]['id']
    res = pm.save_section(sid, '改过的正文')
    chk('改：单节保存 + 版本号 +1', res.get('ok') and res.get('version_no') == 2, res)
    chk('改：保存后缓存立刻失效', pm.text('fetch') == '改过的正文', pm.text('fetch'))
    chk('改：不存在的小节报错且不动数据',
        'error' in pm.save_section(999999, 'x') and n_material() == 3, n_material())
    chk('改：整份覆盖成功',
        pm.replace_slot('fetch', [{'section': '新一', 'body': 'A'},
                                  {'section': '新二', 'body': 'B'}]).get('ok')
        and n_material() == 3, n_material())
    chk('改：覆盖后顺序按传入',
        [r['section'] for r in pm.sections('fetch')] == ['新一', '新二'],
        [r['section'] for r in pm.sections('fetch')])
    chk('改：全空正文被拒', 'error' in pm.replace_slot('fetch', [{'section': 'x', 'body': ' '}]))
    chk('改：被拒时原数据还在', n_material() == 3, n_material())

    # ★ 回滚：这两条是服务器上真丢过数据的地方
    reset()
    before = [r['section'] for r in pm.sections('fetch')]
    res = pm.replace_slot('fetch', [{'section': '一', 'body': 'A'},
                                    {'section': '二', 'body': 'B'},
                                    {'section': '三', 'body': 'C', 'sort_no': '不是数字'}])
    chk('改：sort_no 非法被拒', 'error' in res, res)
    chk('改：sort_no 非法要整体回滚（原内容一行不少）',
        [r['section'] for r in pm.sections('fetch')] == before,
        [r['section'] for r in pm.sections('fetch')])
    reset()
    before = [r['section'] for r in pm.sections('fetch')]
    res = pm.replace_slot('fetch', [{'section': '一', 'body': 'A'},
                                    {'section': '二', 'body': 'a\x00b'}])
    chk('改：正文含 NUL 被拒且回滚',
        'error' in res and [r['section'] for r in pm.sections('fetch')] == before,
        'res=%s 现存=%s' % (res, [r['section'] for r in pm.sections('fetch')]))

    # ── 删 ───────────────────────────────────────────────────
    reset()
    sid = pm.sections('fetch')[0]['id']
    chk('删：删一行', pm.delete_section(sid).get('ok') and n_material() == 2, n_material())
    chk('删：删完立刻生效', '甲、门槛' not in [r['section'] for r in pm.sections('fetch')])
    chk('删：删不存在的 id 要报错（不能假报成功）',
        'error' in pm.delete_section(999999), pm.delete_section(999999))
    for r in list(pm.sections('fetch')) + list(pm.sections('judge')):
        pm.delete_section(r['id'])
    chk('删：全删光后 block 为空', pm.block(('fetch', 'judge')) == '',
        repr(pm.block(('fetch', 'judge'))))
    chk('删：全删光后清单也为空', pm.index_text(('fetch', 'judge')) == '')

    # ── prompt_text（① 号 bug 就在这）────────────────────────
    prompt_text.clear_cache()
    chk('文本：未知 key 被拒', 'error' in prompt_text.save('nope', 'x'))
    chk('文本：modes 坏 JSON 被拒', 'error' in prompt_text.save('modes', '{坏 json'))
    res = prompt_text.save('modes', '{"data":"新做法"}')
    chk('文本：合法 JSON 能真的写进 PG（原本 100% 失败）', res.get('ok'), res)
    prompt_text.clear_cache()
    chk('文本：读回来是新值', prompt_text.get('modes', '') == '{"data":"新做法"}',
        prompt_text.get('modes', '')[:20])
    chk('文本：库里真有这行',
        q("select count(*) n from prompt_text where k='modes'")[0]['n'] == 1)
    chk('文本：version_no 递增', res.get('version_no') == 1, res)
    chk('文本：reset 删掉覆盖', prompt_text.reset('modes').get('ok'))
    prompt_text.clear_cache()
    chk('文本：reset 后回退默认', prompt_text.get('modes', 'DEF') == 'DEF')
    prompt_text.save('identity', '   ')
    prompt_text.clear_cache()
    chk('文本：空白覆盖等于没覆盖', prompt_text.get('identity', 'DEF') == 'DEF')
    # 2026-11-03：`answer_format`（汇报口吻）已下线；多出 6 个时间地点规则的 key
    _keys = [x['key'] for x in prompt_text.all_texts()]
    chk('文本：all_texts 的 key 清单',
        _keys[:3] == ['identity', 'modes', 'keywords']
        and 'answer_format' not in _keys
        and 'time_month_rel' in _keys, str(_keys))
    chk('文本：all_texts 里 overridden 判定对',
        [x for x in prompt_text.all_texts() if x['key'] == 'identity'][0]['overridden'] is False)

    # ── complete_rule（③ 号 bug）已随补全表下线删除（2026-11-03）──
    #    原文与当年那条「priority 传非数字 → 整表清空」的回归留在这里备查：
    #    docs/08-历史归档/2026-11-03-删除的提示词规则/补全表.md

    # ── pgdb 兼容层（改的是全项目共用的 INSERT 处理）──────────
    from app.secretary import pgdb
    c = psycopg.connect(uri, autocommit=True)
    c.execute("DELETE FROM prompt_text WHERE k='identity'")
    c.close()
    conn2 = pgdb.connect()
    cur2 = conn2.cursor()
    cur2.execute('INSERT INTO prompt_material (slot, section, body, sort_no, enabled, pinned, '
                 'version_no, updated_by) VALUES (%s,%s,%s,0,true,true,1,%s)',
                 ('fetch', 'id 测试', 'x', 't'))
    chk('pgdb：有 id 的表 lastrowid 仍然正常（qa_log 等靠它）',
        isinstance(cur2.lastrowid, int) and cur2.lastrowid > 0, cur2.lastrowid)
    cur2.execute('INSERT INTO prompt_text (k, body) VALUES (%s,%s)', ('identity', 'x'))
    chk('pgdb：没有 id 列的表 lastrowid 为 None 且不抛', cur2.lastrowid is None, cur2.lastrowid)
    chk('pgdb：没有 id 列的表也确实写进去了',
        len(q("select k from prompt_text where k='identity'")) == 1)
    conn2.close()

    # ── import 的 markdown 切分（纯函数）─────────────────────
    try:
        spec = importlib.util.spec_from_file_location(
            'prompts_api', SERVER / 'app' / 'api' / 'v1' / 'prompts.py')
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
        for label, text, want in CASES_MD:
            got = [(s['section'], s['body']) for s in api._split_md(text)]
            chk('切分：' + label, got == want, got)
        chk('切分：标题超长截 160',
            len(api._split_md('## ' + 'x' * 300 + '\n正文')[0]['section']) == 160)
        chk('切分：文首长说明单独成一节',
            api._split_md('说' * 40 + '\n\n## 甲\n正文')[0]['section'] == '说明')
        chk('切分：CRLF 也能切',
            len(api._split_md('## 甲\r\n正文\r\n## 乙\r\n正文\r\n')) == 2,
            api._split_md('## 甲\r\n正文\r\n## 乙\r\n正文\r\n'))
        chk('切分：全是空标题返回空', api._split_md('## 一\n\n## 二\n') == [])
    except Exception as exc:                              # noqa: BLE001
        chk('切分：_split_md 可导入', False, '%s: %s' % (type(exc).__name__, str(exc)[:120]))

    bad = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(('  [OK]   ' if ok else '  [FAIL] ') + label
              + (('   ← ' + extra) if (not ok and extra) else ''))
    print('\n%d 项检查，%d 项失败' % (len(_checks), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:                                     # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
