# -*- coding: utf-8 -*-
"""F4-2/F4-3 验收：个人知识库 + 个人偏好（真 PG）。

钉死七条
--------
1. 个人库：建库幂等（两次同一个 kb_id）。
2. 个人库：不同人拿到不同的库（各人各库，不串）。
3. 个人库：列表只返回本人库里的文件（当前为空 → 0 条，不报错）。
4. 偏好：白名单外的键报错；越界数值报错。
5. 偏好：真库存 → 读回 → 整体替换 → 清空。
6. 三级覆盖：站点默认 ← 个人 ← 本次请求。
7. **进 L6**：个人偏好真的出现在最终 system 里（`agent_v2.system(prefs=…)`）。

运行：py -X utf8 server/tests/agent/test_personal_kb.py
"""
from __future__ import annotations

import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVER))

_checks: list[tuple[str, bool, str]] = []


def chk(label: str, cond: bool, extra: str = "") -> None:
    _checks.append((label, bool(cond), extra))


def main() -> int:
    print("F4-2/F4-3：个人知识库 + 个人偏好（真 PG）")
    from app.secretary import personal_kb as K, prefs as P, agent, agent_v2, site_pack as SP

    # ── 1/2/3 个人库 ───────────────────────────────────────────
    try:
        _db_ok = True
        kid_a = K.ensure(401)
        kid_a2 = K.ensure(401)
        kid_b = K.ensure(402)
        chk("建库幂等（两次同一个 kb_id）", kid_a == kid_a2, '%s vs %s' % (kid_a, kid_a2))
        chk("不同人不同库（不串）", kid_a != kid_b, '%s vs %s' % (kid_a, kid_b))
        docs = K.list_docs(401)
        chk("列表返回列表类型（空也不报错）", isinstance(docs, list), str(type(docs)))
        st = K.stats(401)
        chk("统计字段齐全", {'kb_name', 'files', 'chunks', 'parsed'} <= set(st), str(st))

        # ── 4/5 偏好 ──────────────────────────────────────────
        chk("白名单外键报错", _raises(lambda: P.normalize({'hack': 1})))
        chk("字号越界报错", _raises(lambda: P.normalize({'body_pt': 40})))
        r = P.set(401, {'body_pt': 15, 'line_spacing_pt': 26, 'sign_off': '环潭供电所'})
        chk("偏好写入成功", r.get('saved') is True, str(r)[:80])
        got = P.get(401)
        chk("偏好读回一致", got.get('body_pt') == 15 and got.get('sign_off') == '环潭供电所',
            str(got))
        P.set(401, {'body_pt': 18})
        chk("整体替换（旧键消失）", P.get(401) == {'body_pt': 18}, str(P.get(401)))

        # ── 6 三级覆盖 ────────────────────────────────────────
        base = SP.load(strict=False).get('doc_defaults')
        f1 = P.merge_fmt(base, 401)
        f2 = P.merge_fmt(base, 401, {'body_pt': 22})
        chk("个人覆盖站点默认（18）", f1.get('body_pt') == 18, str(f1.get('body_pt')))
        chk("本次请求覆盖个人（22）", f2.get('body_pt') == 22, str(f2.get('body_pt')))

        # ── 7 进入 L6 ─────────────────────────────────────────
        sysmsg = agent_v2.system('general', agent._system, prefs={'body_pt': 18,
                                                                'sign_off': '环潭供电所'})
        chk("个人偏好出现在 system（L6 段）", 'L6 个人偏好' in sysmsg, sysmsg[:60])
        chk("偏好内容进 system", '环潭供电所' in sysmsg)
        chk("红线仍在前",
            sysmsg.index('诚信与安全红线') < sysmsg.index('===== L6'))
        P.set(401, {})
    except K.PersonalKbError as exc:
        _db_ok = False
        print("  [跳过] 数据库不可用：%s" % str(exc)[:120])

    # ── 8 检索范围：把"我的资料库"并进 kb_scope ──────
    if _db_ok:
        merged = K.with_personal([1, 3], 401)
        chk("并入后含本人库", K.ensure(401) in merged, str(merged))
        chk("并入保序（公司库在前）", merged[:2] == [1, 3], str(merged))
        chk("重复并入不重复", K.with_personal(merged, 401) == merged,
            str(K.with_personal(merged, 401)))
        chk("并入不会带入别人的库",
            K.with_personal([], 402) != K.with_personal([], 401),
            '%s vs %s' % (K.with_personal([], 401), K.with_personal([], 402)))
        _old = K.ensure
        K.ensure = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('boom'))
        try:
            chk("库故障时原样返回（不阻断问答）",
                K.with_personal([7], 401) == [7], str(K.with_personal([7], 401)))
        finally:
            K.ensure = _old

    # ── 9 接口已注册（AST，避免起服务）────────────────────────
    import ast, io
    src = io.open(str(SERVER / 'app/api/v1/agent.py'), encoding='utf-8').read()
    paths = set()
    for node in ast.parse(src).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.decorator_list:
                if isinstance(d, ast.Call):
                    for a in d.args:
                        if isinstance(a, ast.Constant) and isinstance(a.value, str):
                            paths.add(a.value)
    chk("接口 /me/prefs 已注册", '/me/prefs' in paths, str(sorted(p for p in paths if 'me' in p)))
    chk("接口 /me/personal-kb 已注册", '/me/personal-kb' in paths)

    bad = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(("  [OK]   " if ok else "  [FAIL] ") + label + (("   ← " + extra) if not ok and extra else ""))
    print("\n%d 项检查，%d 项失败" % (len(_checks), len(bad)))
    return 1 if bad else 0


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:                              # noqa: BLE001
        return True


if __name__ == '__main__':
    sys.exit(main())
