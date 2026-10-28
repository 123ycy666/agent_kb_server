# -*- coding: utf-8 -*-
"""层级传递规则（纯逻辑，不查库）。

**为什么单独一个文件**：层级是产品口径，不是某个服务的私有细节。
`dispatch_service` 里 30 多处要判"这个人能不能派给那个人"，
规则散在各处必然漂移；这里只放"谁比谁高一级"和"这个动作允许哪种关系"。

口径（Fred 2026-10-21 定）：
  1. **严格相邻**：派活只能给直接下一级，上报只能给直接上一级。
  2. 同级之间不能派活，只能抄送知会。
  3. 例外：**同一岗位、同一部门**可以互相派（本所内换人分工），不算越级。
  4. 层级对但组织不对也算越级（曾都的县财务派不到随县的所）。
  5. 管理员不参与层级判定。

层级（数值越大越上级）：

    master  3   市领导
    sgscwb  2   市公司财务部
    xgscwb / cto / yxb   1   县公司财务部 / 运检部 / 营销部（同级，互不隶属）
    sz      0   供电所所长

注意 `cto` 和 `xgscwb` **同级**——县运检和县财务谁也不管谁，
所以县运检不能把活派给县财务，只能抄送。
"""
from __future__ import annotations

from typing import Callable, Iterable

# 角色 → 层级号（不在表里的角色不能参与派活/上报）
ROLE_RANK: dict[str, int] = {
    'sz': 0,
    'xgscwb': 1, 'cto': 1, 'yxb': 1,
    'sgscwb': 2,
    'master': 3,
}

# 不参与层级判定（系统管理员）
EXEMPT_ROLES = frozenset({'admin'})

ROLE_NAMES: dict[str, str] = {
    'sz': '供电所所长', 'xgscwb': '县公司财务部', 'cto': '县公司运检部',
    'yxb': '县公司营销部', 'sgscwb': '市公司财务部', 'master': '市领导',
    'admin': '管理员', 'codea': '录入员',
}

# 动作 → 允许的层级关系
#   same     同一岗位（同部门内换人/分工）
#   down     直接下一级
#   up       直接上一级
#   peer     同级不同岗（只能抄送）
ACTION_ALLOW: dict[str, set[str]] = {
    # 主责 = 真把活交给他负责 → 只能给直接下一级，或本部门同岗位换人
    'assign':   {'down', 'same'},
    # 协办 = 请人配合 → 相邻一级的上下、以及同级跨部门都可以，但不能跳级
    'collab':   {'down', 'up', 'peer', 'same'},
    'subtask':  {'down'},             # 继续拆给下级
    'transfer': {'down', 'same'},     # 转派
    'escalate': {'up'},               # 上报/升级
    # 抄送 = 知会/汇总，不传任务，所以不限层级（市领导"只收汇总"就是这么来的）；
    # 但仍要求对方岗位真实存在，否则 fail-closed。
    'cc':       {'down', 'up', 'peer', 'same', 'far_down', 'far_up'},
}

# 关系 → 人话
REL_TEXT = {
    'same': '同一个岗位',
    'down': '你的下一级',
    'up': '你的上一级',
    'peer': '同级但不是一条线',
    'far_down': '隔了好几级的下级',
    'far_up': '隔了好几级的上级',
    'unknown': '没有层级关系',
}


def rank(role_code: str | None) -> int | None:
    """角色层级号；未知角色返回 None（fail-closed，不当 0）。"""
    return ROLE_RANK.get((role_code or '').strip())


def name_of(role_code: str | None) -> str:
    rc = (role_code or '').strip()
    return ROLE_NAMES.get(rc, rc or '未知岗位')


def is_exempt(role_code: str | None) -> bool:
    return (role_code or '') in EXEMPT_ROLES


def relation(actor_role: str | None, target_role: str | None) -> str:
    """两个人之间的层级关系。"""
    a, b = rank(actor_role), rank(target_role)
    if a is None or b is None:
        return 'unknown'
    if (actor_role or '') == (target_role or ''):
        return 'same'
    if a == b:
        return 'peer'
    if b == a - 1:
        return 'down'
    if b == a + 1:
        return 'up'
    return 'far_down' if b < a else 'far_up'


def check(action: str, actor_role: str | None, target_role: str | None) -> tuple[bool, str]:
    """只看层级，不看组织。返回 (是否允许, 关系)。"""
    if is_exempt(actor_role):
        return True, 'exempt'
    rel = relation(actor_role, target_role)
    return rel in (ACTION_ALLOW.get(action) or set()), rel


def next_ranks(role_code: str | None, step: int = 1) -> list[str]:
    """比这个角色高/低 step 级的所有角色（同级多条线时会返回多个）。"""
    base = rank(role_code)
    if base is None:
        return []
    want = base + step
    return [r for r, v in ROLE_RANK.items() if v == want]


# ─────────────────────────────────────────────── 组织树（父级由调用方给）

ParentOf = Callable[[int], int | None]


def ancestor_chain(dept_id: int | None, parent_of: ParentOf, limit: int = 8) -> list[int]:
    """自己 + 所有上级部门，从近到远。防环。"""
    out: list[int] = []
    cur = dept_id
    while cur and len(out) < limit:
        cur = int(cur)
        if cur in out:
            break
        out.append(cur)
        nxt = parent_of(cur)
        if not nxt or int(nxt) == cur:
            break
        cur = int(nxt)
    return out


def is_ancestor(anc: int | None, child: int | None, parent_of: ParentOf) -> bool:
    """anc 是不是 child 的上级（含自己）。"""
    if not anc or not child:
        return False
    return int(anc) in ancestor_chain(int(child), parent_of)


def check_org(actor_dept: int | None, target_dept: int | None, parent_of: ParentOf,
              upward: bool = False) -> bool:
    """组织归属：下发必须往自己的子树走，上报必须往自己的上级走。"""
    if not actor_dept or not target_dept:
        return True                      # 拿不到部门就不在这里卡（另有账号校验）
    if upward:
        return is_ancestor(target_dept, actor_dept, parent_of)
    return is_ancestor(actor_dept, target_dept, parent_of)


def explain(action: str, actor_role: str | None, target_role: str | None,
            rel: str | None = None) -> str:
    """给出"为什么不行 + 该发给谁"的人话，直接进接口 message。"""
    rel = rel or relation(actor_role, target_role)
    me, you = name_of(actor_role), name_of(target_role)
    if rel == 'peer':
        return '%s和%s是同级，不能互相派活，只能抄送知会。' % (me, you)
    if rel == 'far_down':
        mid = '、'.join(name_of(r) for r in next_ranks(actor_role, -1))
        return ('%s不能直接派给%s，中间还隔着%s。请先发给%s，由%s往下传。'
                % (me, you, mid, mid, mid))
    if rel == 'far_up':
        mid = '、'.join(name_of(r) for r in next_ranks(actor_role, 1))
        return '%s不能直接上报给%s，中间还隔着%s。请先上报%s。' % (me, you, mid, mid)
    if rel == 'same':
        return '%s只能在自己部门内换人，跨部门请按层级走。' % me
    if rel == 'down':
        return '%s不能把活往回派给下级%s。' % (me, you)
    if rel == 'up' and action in ('assign', 'subtask', 'transfer'):
        return ('%s不能把活派给上级%s。要让上级办，请用「上报」，'
                '或者把任务抄送给他。' % (me, you))
    return '看不出来%s和%s谁管谁，这条不能走。' % (me, you)


def chain_text(roles: Iterable[str]) -> str:
    return ' → '.join(name_of(r) for r in roles if r)
