# -*- coding: utf-8 -*-
"""层级传递规则单测（不连库、不连 MySQL）。

跑法： py -X utf8 server/tests/agent/test_dispatch_chain.py
产出： output/test_results/<日期>-预警任务流转/raw-chain.json
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]          # 仓库根：server/tests/agent/<file> → 上三级
sys.path.insert(0, str(ROOT / 'server'))

from app.services import dispatch_chain as C  # noqa: E402

OUT = ROOT / 'output' / 'test_results' / ('%s-预警任务流转' % time.strftime('%Y-%m-%d'))
OUT.mkdir(parents=True, exist_ok=True)
RESULTS = []

# 假部门树：1 市公司 / 10 曾都县 / 11 随县 / 56 两水所(曾都) / 25 厉山所(随县)
TREE = {1: None, 10: 1, 11: 1, 56: 10, 25: 11}


def parent_of(d):
    return TREE.get(int(d))


def check(no, name, ok, detail=''):
    RESULTS.append({'no': no, 'name': name, 'ok': bool(ok), 'detail': str(detail)[:600]})
    print('%s %2s %s%s' % ('PASS' if ok else 'FAIL', no, name,
                           ('  | ' + str(detail)[:200]) if detail else ''))


def main():
    # ── 1) 层级号 ────────────────────────────────────────────
    check(1, '层级：所长0 < 县专业1 < 市财务2 < 市领导3',
          (C.rank('sz'), C.rank('xgscwb'), C.rank('cto'), C.rank('sgscwb'),
           C.rank('master')) == (0, 1, 1, 2, 3))
    check(2, '未知角色没有层级（fail-closed）', C.rank('codea') is None and C.rank(None) is None)

    # ── 2) 关系矩阵 ──────────────────────────────────────────
    cases = [
        ('sz', 'xgscwb', 'up'), ('xgscwb', 'sz', 'down'),
        ('xgscwb', 'sgscwb', 'up'), ('sgscwb', 'xgscwb', 'down'),
        ('sgscwb', 'master', 'up'), ('master', 'sgscwb', 'down'),
        ('sz', 'sgscwb', 'far_up'), ('sgscwb', 'sz', 'far_down'),
        ('sz', 'master', 'far_up'), ('master', 'sz', 'far_down'),
        ('xgscwb', 'cto', 'peer'), ('cto', 'yxb', 'peer'),
        ('sz', 'sz', 'same'),
    ]
    bad = [(a, b, C.relation(a, b), want) for a, b, want in cases if C.relation(a, b) != want]
    check(3, '关系矩阵 %d 组' % len(cases), not bad, bad[:3])

    # ── 3) 派活：只能直接下一级 或 同岗位 ─────────────────────
    check(4, '市财务 → 所长：越级，拒', C.check('assign', 'sgscwb', 'sz')[0] is False,
          C.check('assign', 'sgscwb', 'sz'))
    check(5, '市财务 → 县财务：允许', C.check('assign', 'sgscwb', 'xgscwb')[0] is True)
    check(6, '县财务 → 所长：允许', C.check('assign', 'xgscwb', 'sz')[0] is True)
    check(7, '县财务 → 县运检：同级，拒', C.check('assign', 'xgscwb', 'cto')[0] is False)
    check(8, '所长 → 所长（本所换人）：允许', C.check('assign', 'sz', 'sz')[0] is True)
    check(9, '所长 → 县财务：不能派活，只能上报', C.check('assign', 'sz', 'xgscwb')[0] is False)

    # ── 4) 上报：只能直接上一级 ───────────────────────────────
    check(10, '所长 → 县财务上报：允许', C.check('escalate', 'sz', 'xgscwb')[0] is True)
    check(11, '所长 → 市财务上报：越级，拒', C.check('escalate', 'sz', 'sgscwb')[0] is False)
    check(12, '县财务 → 市财务上报：允许', C.check('escalate', 'xgscwb', 'sgscwb')[0] is True)
    check(13, '市财务 → 市领导上报：允许', C.check('escalate', 'sgscwb', 'master')[0] is True)
    check(14, '市财务 → 县财务：不能"上报"给下级', C.check('escalate', 'sgscwb', 'xgscwb')[0] is False)

    # ── 5) 继续拆：所长没有下级 ───────────────────────────────
    check(15, '所长不能再拆子任务', C.check('subtask', 'sz', 'sz')[0] is False)
    check(16, '县财务拆给所长：允许', C.check('subtask', 'xgscwb', 'sz')[0] is True)
    check(17, '市财务拆给县财务：允许', C.check('subtask', 'sgscwb', 'xgscwb')[0] is True)

    # ── 6) 抄送：谁都能抄 ────────────────────────────────────
    check(18, '抄送不限层级',
          all(C.check('cc', 'sz', t)[0] for t in ('xgscwb', 'sgscwb', 'master', 'sz', 'cto')))
    check(18.1, '抄送给不存在的岗位：仍拒（fail-closed）',
          C.check('cc', 'sz', 'xyz')[0] is False)

    # ── 7) 管理员豁免 ────────────────────────────────────────
    check(19, '管理员不受层级限制',
          C.check('assign', 'admin', 'sz')[0] and C.check('escalate', 'admin', 'master')[0])

    # ── 8) 组织树 ────────────────────────────────────────────
    check(20, '部门上溯：两水所 → 曾都 → 市',
          C.ancestor_chain(56, parent_of) == [56, 10, 1],
          C.ancestor_chain(56, parent_of))
    check(21, '曾都是两水所的上级', C.is_ancestor(10, 56, parent_of))
    check(22, '随县不是两水所的上级', not C.is_ancestor(11, 56, parent_of))

    # ── 9) 组织归属：层级对但组织不对也要拒 ──────────────────
    check(23, '曾都县财务 → 两水所：组织对', C.check_org(10, 56, parent_of))
    check(24, '随县县财务 → 两水所：跨县，拒', not C.check_org(11, 56, parent_of))
    check(25, '两水所 → 曾都县财务：上报方向对', C.check_org(56, 10, parent_of, upward=True))
    check(26, '两水所 → 市财务：组织链上是祖先，所以必须靠层级拦',
          C.check_org(56, 1, parent_of, upward=True) is True
          and C.check('escalate', 'sz', 'sgscwb')[0] is False)
    check(27, '拿不到部门时不在组织层卡', C.check_org(None, 56, parent_of))

    # ── 10) 拒绝话术要点名"该发给谁" ─────────────────────────
    m1 = C.explain('assign', 'sgscwb', 'sz')
    check(28, '越级下发的话术提到中间那级', '县公司财务部' in m1 and '往下传' in m1, m1)
    m2 = C.explain('assign', 'xgscwb', 'cto')
    check(29, '同级的话术说"只能抄送"', '同级' in m2 and '抄送' in m2, m2)
    m3 = C.explain('escalate', 'sz', 'sgscwb')
    check(30, '越级上报的话术说"先上报谁"', '先上报' in m3, m3)

    (OUT / 'raw-chain.json').write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1),
                                        encoding='utf-8')
    bad = [r for r in RESULTS if not r['ok']]
    print('=' * 90)
    print('%d/%d PASS' % (len(RESULTS) - len(bad), len(RESULTS)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
