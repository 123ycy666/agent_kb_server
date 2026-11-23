"""表达式：把文档里写的一行式子算成一个值。

只认白名单语法：列名、常量、四则、比较、and/or/not、三元、以及下面这些函数。
不许属性访问、不许下标、不许 lambda —— 文档是给人填的，不是给代码执行的。
"""
from __future__ import annotations

import ast
import datetime as _dt
import math
import operator
from typing import Any

AGG_FUNCS = {'sum', 'avg', 'min', 'max', 'count', 'countd'}
FUNCS = {
    'today', 'now', 'round', 'abs', 'int', 'num', 'len',
    'datediff', 'coalesce', 'ifnull', 'upper', 'lower', 'concat', 'substr', 'isnull',
} | AGG_FUNCS


class ExprError(ValueError):
    pass


def _num(v):
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str) and v.strip() != '':
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _date(v):
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    if isinstance(v, str) and len(v) >= 10:
        try:
            return _dt.date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def _add(a, b):
    if a is None or b is None:
        return None
    if isinstance(a, str) and isinstance(b, str):
        return a + b
    da, db = _date(a), _date(b)
    if da is not None and isinstance(b, int) and not isinstance(b, bool) and not isinstance(b, _dt.date):
        return da + _dt.timedelta(days=b)
    na, nb = _num(a), _num(b)
    if na is None or nb is None:
        return None
    return na + nb


def _sub(a, b):
    if a is None or b is None:
        return None
    da, db = _date(a), _date(b)
    if da is not None and db is not None:
        return (da - db).days
    na, nb = _num(a), _num(b)
    if na is None or nb is None:
        return None
    return na - nb


def _mul(a, b):
    na, nb = _num(a), _num(b)
    return None if na is None or nb is None else na * nb


def _div(a, b):
    na, nb = _num(a), _num(b)
    if na is None or nb is None or nb == 0:
        return None
    return na / nb


def _cmp(a, b, op):
    if a is None or b is None:
        return False
    da, db = _date(a), _date(b)
    if da is not None and db is not None:
        return op(da, db)
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        return op(na, nb)
    try:
        return op(str(a), str(b))
    except TypeError:
        return False


_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
}
_BIN = {
    ast.Add: _add,
    ast.Sub: _sub,
    ast.Mult: _mul,
    ast.Div: _div,
    ast.Mod: lambda a, b: None if _num(b) in (None, 0) else _num(a) % _num(b),
}
_CMP = {
    ast.Eq: lambda a, b: (_num(a) == _num(b)) if (_num(a) is not None and _num(b) is not None) else a == b,
    ast.NotEq: lambda a, b: not ((_num(a) == _num(b)) if (_num(a) is not None and _num(b) is not None) else a == b),
    ast.Lt: lambda a, b: _cmp(a, b, operator.lt),
    ast.LtE: lambda a, b: _cmp(a, b, operator.le),
    ast.Gt: lambda a, b: _cmp(a, b, operator.gt),
    ast.GtE: lambda a, b: _cmp(a, b, operator.ge),
    ast.In: lambda a, b: a in (b or []),
    ast.NotIn: lambda a, b: a not in (b or []),
}


class Expr:
    """一条式子。eval(row, group) 算出一个值；group 用来做分组聚合。"""

    def __init__(self, text: str):
        self.text = str(text or '').strip()
        if not self.text:
            raise ExprError('式子为空')
        try:
            self.tree = ast.parse(self.text, mode='eval').body
        except SyntaxError as exc:
            raise ExprError('式子写法有问题：%s' % getattr(exc, 'msg', str(exc))) from exc
        self.names = self._names(self.tree)
        self.aggregated = any(isinstance(n, ast.Call) and getattr(n.func, 'id', '') in AGG_FUNCS
                              for n in ast.walk(self.tree))

    @staticmethod
    def _names(node) -> set[str]:
        """式子引用了哪些列：`别名.列` 记成整体，函数名与别名本身不算。"""
        out: set[str] = set()

        def walk(n):
            if isinstance(n, ast.Call):
                for a in n.args:
                    walk(a)
                for k in n.keywords:
                    walk(k.value)
                return
            if isinstance(n, ast.Attribute):
                if isinstance(n.value, ast.Name):
                    out.add('%s.%s' % (n.value.id, n.attr))
                else:
                    walk(n.value)
                return
            if isinstance(n, ast.Name):
                out.add(n.id)
                return
            for child in ast.iter_child_nodes(n):
                walk(child)

        walk(node)
        return out

    def eval(self, row: dict, group: list | None = None) -> Any:
        return self._ev(self.tree, row, group)

    # ── 内部 ───────────────────────────────────────────────────────────
    def _ev(self, node, row, group):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return row.get(node.id)
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name):
                return row.get('%s.%s' % (node.value.id, node.attr))
            raise ExprError('属性写法不支持')
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.USub):
                v = _num(self._ev(node.operand, row, group))
                return None if v is None else -v
            if isinstance(node.op, ast.Not):
                return not bool(self._ev(node.operand, row, group))
            raise ExprError('不支持的运算符')
        if isinstance(node, ast.BinOp):
            fn = _BIN.get(type(node.op))
            if fn is None:
                raise ExprError('不支持的四则运算')
            return fn(self._ev(node.left, row, group), self._ev(node.right, row, group))
        if isinstance(node, ast.BoolOp):
            vals = [bool(self._ev(v, row, group)) for v in node.values]
            return all(vals) if isinstance(node.op, ast.And) else any(vals)
        if isinstance(node, ast.Compare):
            left = self._ev(node.left, row, group)
            for op, comp in zip(node.ops, node.comparators):
                fn = _CMP.get(type(op))
                if fn is None:
                    raise ExprError('不支持的比较')
                right = self._ev(comp, row, group)
                if not fn(left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return self._ev(node.body, row, group) if bool(self._ev(node.test, row, group)) \
                else self._ev(node.orelse, row, group)
        if isinstance(node, ast.List):
            return [self._ev(x, row, group) for x in node.elts]
        if isinstance(node, ast.Call):
            return self._call(node, row, group)
        raise ExprError('这种写法不支持：%s' % type(node).__name__)

    def _call(self, node, row, group):
        if not isinstance(node.func, ast.Name):
            raise ExprError('函数名必须是名字')
        name = node.func.id
        if name not in FUNCS:
            raise ExprError('没有这个函数：%s' % name)

        if name in AGG_FUNCS and group is not None and len(node.args) == 1:
            vals = [self._ev(node.args[0], r, None) for r in group]
            vals = [v for v in vals if v is not None]
            if name == 'count':
                return len(vals)
            if name == 'countd':
                return len({str(v) for v in vals})
            nums = [_num(v) for v in vals]
            nums = [x for x in nums if x is not None]
            if not nums:
                return 0 if name == 'sum' else None
            if name == 'sum':
                return sum(nums)
            if name == 'avg':
                return sum(nums) / len(nums)
            if name == 'min':
                return min(nums)
            return max(nums)

        args = [self._ev(a, row, group) for a in node.args]

        if name == 'today':
            return _dt.date.today()
        if name == 'now':
            return _dt.datetime.now()
        if name == 'round':
            x = _num(args[0] if args else None)
            n = int(_num(args[1]) if len(args) > 1 else 0) or 0
            return None if x is None else round(x, n)
        if name == 'abs':
            x = _num(args[0] if args else None)
            return None if x is None else abs(x)
        if name == 'int':
            x = _num(args[0] if args else None)
            return None if x is None else int(x)
        if name == 'num':
            return _num(args[0] if args else None)
        if name == 'len':
            v = args[0] if args else None
            return 0 if v is None else len(str(v))
        if name == 'datediff':
            a, b = (_date(args[0]), _date(args[1])) if len(args) > 1 else (_date(args[0]) if args else None, None)
            return None if a is None or b is None else (a - b).days
        if name == 'coalesce':
            for v in args:
                if v is not None and v != '':
                    return v
            return None
        if name == 'ifnull':
            return args[0] if (len(args) > 0 and args[0] is not None) else (args[1] if len(args) > 1 else None)
        if name == 'upper':
            return None if not args or args[0] is None else str(args[0]).upper()
        if name == 'lower':
            return None if not args or args[0] is None else str(args[0]).lower()
        if name == 'concat':
            return ''.join('' if a is None else str(a) for a in args)
        if name == 'substr':
            if not args or args[0] is None:
                return None
            s = str(args[0])
            start = int(_num(args[1]) or 0) if len(args) > 1 else 0
            length = int(_num(args[2]) or 0) if len(args) > 2 else None
            return s[start:start + length] if length else s[start:]
        if name == 'isnull':
            return not args or args[0] is None or args[0] == ''
        if name in ('min', 'max'):
            vals = [_num(a) for a in args]
            vals = [v for v in vals if v is not None]
            if not vals:
                return None
            return min(vals) if name == 'min' else max(vals)
        if name == 'sum':
            return sum(_num(a) for a in args if _num(a) is not None)
        if name == 'avg':
            vals = [_num(a) for a in args if _num(a) is not None]
            return sum(vals) / len(vals) if vals else None
        if name == 'count':
            return len(args)
        if name == 'countd':
            return len({str(a) for a in args})
        raise ExprError('没有这个函数：%s' % name)


def safe_float(v):
    n = _num(v)
    if n is None or isinstance(n, float) and (math.isnan(n) or math.isinf(n)):
        return None
    return n
