# -*- coding: utf-8 -*-
"""DLJ_DATA 业务库 E-R 图渲染器（SVG 零依赖 + PNG 预览用 Pillow）。

为什么自己写而不是 mermaid/graphviz：
  本机没有 mermaid、没有 graphviz，也连不上 npm/CDN（v2ray 未运行）；
  headless Chrome 在本沙箱起不来（mojo 命名管道被拒），所以 PNG 也自己画。

数据来源：2026-10-20 只读连 47.121.183.81 `dlj_data` 实测；行数是 COUNT(*) 精确值。

线型约定：
  实线 = 字段注释写明「关联 表.id」，或 ai_table_relation 登记过
  虚线 = 只有命名一致，注释没写（按命名推测，需复核）
  红框 = 该表/字段有已知问题（空表、类型不一致、文本范围无主键）

布局：4 条竖列，列距 490px（40 / 530 / 1020 / 1510），列间 160px 够放连线标签。
产出：docs/06-口径与数据/ER/{er_*.svg, er_*.png}
"""
from __future__ import annotations

import html
import os

FONT = "'Microsoft YaHei','Segoe UI',sans-serif"
MONO = "'Consolas','Courier New',monospace"
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

ROW_H = 19
HEAD_H = 54

# 文本宽度用 Pillow 实测（估算法会把「administrator_flag」量短，导致中文名压到字段名上）
try:
    from PIL import ImageFont as _IF

    _MEASURE_CACHE: dict = {}

    def text_w(txt: str, size: float, font_path: str = "C:/Windows/Fonts/msyh.ttc") -> float:
        key = (round(size, 1), font_path)
        f = _MEASURE_CACHE.get(key)
        if f is None:
            f = _IF.truetype(font_path, max(6, int(round(size))))
            _MEASURE_CACHE[key] = f
        return float(f.getlength(txt))
except Exception:  # pragma: no cover - 没有 Pillow 时退回估算
    def text_w(txt: str, size: float, font_path: str = "") -> float:
        return sum((size if ord(ch) > 127 else size * 0.55) for ch in txt)
COL_X = 40
COL_W = 610
BOX_W = 440

C_ORG = ("#2b6cb0", "#eaf2fd")
C_BIZ = ("#2f855a", "#e9f7ef")
C_PUSH = ("#b7791f", "#fdf5e6")
C_AI = ("#6b46c1", "#f3eefc")
C_WARN = ("#c53030", "#fdeaea")
C_SYS = ("#4a5568", "#f2f4f7")


# 字段中文名（来源：information_schema.columns.column_comment；过长的压短到 ≤10 字）
CN = {
    ("t_department", "department_id"): "部门ID",
    ("t_department", "department_name"): "部门名称",
    ("t_department", "parent_id"): "父级部门ID",
    ("t_department", "manager_id"): "部门负责人",
    ("t_employee", "employee_id"): "员工ID",
    ("t_employee", "login_name"): "登录账号",
    ("t_employee", "actual_name"): "员工姓名",
    ("t_employee", "department_id"): "部门ID",
    ("t_employee", "administrator_flag"): "是否超管",
    ("t_role", "role_id"): "角色ID",
    ("t_role", "role_code"): "角色编码",
    ("t_role", "role_name"): "角色名称",
    ("t_menu", "menu_id"): "菜单ID",
    ("t_menu", "menu_name"): "菜单名称",
    ("t_menu", "menu_type"): "类型",
    ("t_menu", "parent_id"): "父菜单ID",
    ("t_menu", "api_perms"): "后端权限串",
    ("t_menu", "web_perms"): "前端权限串",
    ("t_role_employee", "id"): "主键",
    ("t_role_employee", "role_id"): "角色ID",
    ("t_role_employee", "employee_id"): "员工ID",
    ("t_role_data_scope", "id"): "主键",
    ("t_role_data_scope", "role_id"): "角色ID",
    ("t_role_data_scope", "data_scope_type"): "数据范围类型",
    ("t_role_data_scope", "view_type"): "可见范围类型",
    ("t_role_menu", "role_menu_id"): "主键",
    ("t_role_menu", "role_id"): "角色ID",
    ("t_role_menu", "menu_id"): "菜单ID",
    ("t_power_supply_company", "id"): "县公司ID",
    ("t_power_supply_company", "name"): "名称",
    ("t_power_supply_company", "department_id"): "部门ID",
    ("t_power_company", "id"): "供电所ID",
    ("t_power_company", "name"): "名称",
    ("t_power_company", "code"): "编号",
    ("t_power_company", "department_id"): "部门ID",
    ("t_power_company", "parent_id"): "上级局(未关联)",
    ("t_power_substation", "id"): "变电站ID",
    ("t_power_substation", "name"): "名称",
    ("t_power_substation", "company_id"): "所属公司(?)",
    ("t_power_line", "id"): "线路ID",
    ("t_power_line", "line_code"): "线路编号",
    ("t_power_line", "power_id"): "所属供电所ID",
    ("t_power_line", "substation_id"): "变电站ID",
    ("t_power_area", "id"): "台区ID",
    ("t_power_area", "area_code"): "台区编号",
    ("t_power_area", "power_id"): "所属供电所ID",
    ("t_power_area", "line_id"): "所属线路ID",
    ("t_power_area", "loss_rate"): "台区线损率",
    ("t_power_company_budget_allocation", "id"): "主键",
    ("t_power_company_budget_allocation", "county_company_id"): "县公司ID",
    ("t_power_company_budget_allocation", "year"): "年份",
    ("t_power_company_budget_allocation", "total_budget"): "总预算",
    ("t_power_company_budget_allocation", "base_value"): "K值基数",
    ("t_power_company_budget_allocation", "is_new"): "是否最新",
    ("t_power_company_budget_result", "id"): "主键",
    ("t_power_company_budget_result", "allocation_id"): "预算配置ID",
    ("t_power_company_budget_result", "county_company_id"): "县公司ID",
    ("t_power_company_budget_result", "power_station_id"): "供电所ID",
    ("t_power_company_budget_result", "year"): "年份",
    ("t_power_company_budget_profile", "id"): "主键",
    ("t_power_company_budget_profile", "company_id"): "供电所ID",
    ("t_power_company_budget_profile", "year"): "预算年度",
    ("t_power_company_scoring_standard", "id"): "主键",
    ("t_power_company_scoring_standard", "metric_key"): "指标key",
    ("t_power_company_scoring_standard", "direction"): "正向/反向",
    ("t_power_company_scoring_standard", "full_score_pct"): "满分标准%",
    ("t_power_company_scoring_standard", "is_new"): "是否最新",
    ("t_power_company_rank_weight", "id"): "主键",
    ("t_power_company_rank_weight", "metric_key"): "指标key",
    ("t_power_company_rank_weight", "weight"): "权重系数",
    ("t_power_company_rank_weight", "rank_type"): "排名类型",
    ("t_power_company_rank_weight", "is_new"): "是否最新",
    ("t_power_station_score_detail", "id"): "主键",
    ("t_power_station_score_detail", "power_station_id"): "供电所ID",
    ("t_power_station_score_detail", "county_company_id"): "县公司ID",
    ("t_power_station_score_detail", "budget_result_id"): "预算结果ID",
    ("t_power_station_score_detail", "year"): "年份",
    ("t_power_station_score_detail", "metric_key"): "指标key",
    ("t_power_station_score_detail", "weight"): "权重",
    ("t_power_work_order", "id"): "工单ID",
    ("t_power_work_order", "source_id"): "原系统工单ID",
    ("t_power_work_order", "power_company_id"): "供电公司ID",
    ("t_power_work_order", "power_line_id"): "电力线路ID",
    ("t_power_work_order", "power_area_id"): "台区ID",
    ("t_power_work_order", "applicant_id"): "申请人ID",
    ("t_power_work_order", "flow_instance_id"): "流程实例ID",
    ("t_power_work_order_status", "id"): "主键",
    ("t_power_work_order_status", "work_order_id"): "工单ID",
    ("t_power_work_order_status", "node_code"): "节点编码",
    ("t_power_work_order_status", "work_status"): "工作状态",
    ("t_power_work_order_status", "audit_task_id"): "审批任务ID",
    ("t_power_work_order_status", "approval_round"): "审批轮次",
    ("t_work_order_flow_node", "id"): "主键",
    ("t_work_order_flow_node", "node_code"): "节点编码",
    ("t_work_order_flow_node", "node_name"): "节点名称",
    ("t_work_order_flow_node", "node_order"): "节点顺序",
    ("t_work_order_flow_node", "role_code"): "审批角色编码",
    ("t_work_order_audit_task", "id"): "主键",
    ("t_work_order_audit_task", "work_order_id"): "工单ID",
    ("t_work_order_audit_task", "node_code"): "节点编码",
    ("t_work_order_audit_task", "assignee_id"): "办理人ID",
    ("t_power_work_order_record", "id"): "主键",
    ("t_power_work_order_record", "work_order_id"): "工单ID",
    ("t_power_work_cost", "id"): "主键",
    ("t_power_work_cost", "work_order_id"): "工单ID",
    ("t_power_work_cost", "work_order_record_id"): "项目明细ID",
    ("t_power_work_cost", "cost_item_id"): "预算项ID",
    ("t_power_push_outage", "id"): "主键",
    ("t_power_push_outage", "power_station_id"): "供电所ID",
    ("t_power_push_outage", "area_id"): "台区ID",
    ("t_power_push_outage", "resource_id"): "设备资源编号",
    ("t_power_push_trip", "id"): "主键",
    ("t_power_push_trip", "power_station_id"): "供电所ID",
    ("t_power_push_trip", "area_id"): "台区ID",
    ("t_power_push_trip", "resource_id"): "设备资源编号",
    ("t_power_push_sales_volume", "id"): "主键",
    ("t_power_push_sales_volume", "power_station_id"): "供电所ID",
    ("t_power_push_sales_volume", "area_id"): "台区ID",
    ("t_power_push_sales_volume", "resource_id"): "设备资源编号",
    ("t_power_push_line_loss", "id"): "主键",
    ("t_power_push_line_loss", "power_station_id"): "供电所ID",
    ("t_power_push_line_loss", "area_id"): "台区ID",
    ("t_power_ai_metric_snapshot", "id"): "主键",
    ("t_power_ai_metric_snapshot", "scope_type"): "范围类型",
    ("t_power_ai_metric_snapshot", "scope_key"): "范围键",
    ("t_power_ai_metric_snapshot", "scope_name"): "范围名称",
    ("t_power_ai_area_metric_snapshot", "id"): "主键",
    ("t_power_ai_area_metric_snapshot", "company_id"): "县公司ID",
    ("t_power_ai_area_metric_snapshot", "power_station_id"): "供电所ID",
    ("t_power_ai_area_metric_snapshot", "line_id"): "主线路ID",
    ("t_power_ai_area_metric_snapshot", "area_id"): "台区ID",
    ("t_power_push_district_month_summary", "id"): "主键",
    ("t_power_push_district_month_summary", "stat_month"): "统计月yyyyMM",
    ("t_power_push_district_month_summary", "summary_type"): "汇总口径",
    ("t_power_push_district_month_summary", "district_name"): "区县名(文本)",
    ("t_power_push_station_month_summary", "id"): "主键",
    ("t_power_push_station_month_summary", "stat_month"): "统计月yyyyMM",
    ("t_power_push_station_month_summary", "station_name"): "供电所名(文本)",
    ("t_power_push_month_summary", "resource_id"): "中台所标识",
    ("t_power_push_station_month_summary", "resource_id"): "中台所标识",
}


LEGEND = ("—— 实线=注释/ai_table_relation 写明   ---- 虚线=仅命名一致需复核   "
          "○ =1 端   ⊰ =多端   红框=已知问题")


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


class Box:
    """一张表：标题 + 若干 (字段, 类型, 标记)。

    行名按可用宽度自动缩字号/截断 —— 中文行名（「t_department 部门」）用 mono 画会溢出。
    """

    def __init__(self, x, y, cn, en, rows, color=C_ORG, note="", warn=False):
        self.x, self.y = x, y
        self.cn, self.en = cn, en
        self.color = C_WARN if warn else color
        self.note = note
        self.w = BOX_W
        self.h = HEAD_H + len(rows) * ROW_H + (18 if note else 0) + 10
        self.warn = warn
        self.rows = [self._fit(n, t, m) for n, t, m in rows]

    @staticmethod
    def _est(txt, size):
        """粗估文本宽度：CJK 按 1 个字宽，ASCII 按 0.55。"""
        return sum((size if ord(ch) > 127 else size * 0.55) for ch in txt)

    def _fit(self, name, typ, mark):
        cn = CN.get((self.en, name), "")
        cnw = text_w(cn, 10.5) + (10 if cn else 0)
        typw = text_w(typ, 9.5) + 8
        avail = BOX_W - 36 - 14 - cnw - typw
        size = 12.0
        while size > 8.5 and text_w(name, size) > avail:
            size -= 0.5
        if text_w(name, size) > avail:
            while len(name) > 4 and text_w(name + "…", size) > avail:
                name = name[:-1]
            name = name + "…"
        return (name, typ, mark, size, cn, text_w(name, size))

    def rect(self):
        return (self.x, self.y, self.x + self.w, self.y + self.h)

    def center(self):
        return (self.x + self.w / 2, self.y + self.h / 2)

    def svg(self):
        x, y, _, _ = self.rect()
        stroke, fill = self.color
        out = [
            f'<g><rect x="{x}" y="{y}" width="{self.w}" height="{self.h}" rx="7" fill="#fff" '
            f'stroke="{stroke}" stroke-width="{2 if self.warn else 1.4}"/>',
            f'<path d="M{x} {y + 10} a7,7 0 0 1 7,-7 h{self.w - 14} a7,7 0 0 1 7,7 '
            f'v{HEAD_H - 10} h-{self.w} z" fill="{fill}"/>',
            f'<text x="{x + 11}" y="{y + 21}" font-family="{FONT}" font-size="13.5" font-weight="700" '
            f'fill="{stroke}">{esc(self.cn)}</text>',
            f'<text x="{x + 11}" y="{y + 38}" font-family="{MONO}" '
            f'font-size="10" fill="#5a6472">{esc(self.en)}</text>',
        ]
        ty = y + HEAD_H + 4
        for name, typ, mark, size, cn, namew in self.rows:
            b = {"PK": "#b7791f", "FK": "#3182ce", "!": "#c53030"}.get(mark)
            if b:
                out.append(f'<text x="{x + 11}" y="{ty}" font-family="{MONO}" font-size="9" fill="{b}">'
                           f'{"!!" if mark == "!" else mark}</text>')
            out.append(f'<text x="{x + 36}" y="{ty}" font-family="{MONO}" font-size="{size:g}" '
                       f'fill="{"#c53030" if mark == "!" else "#1a202c"}">{esc(name)}</text>')
            if cn:
                out.append(f'<text x="{x + 36 + namew + 10:.1f}" y="{ty}" font-family="{FONT}" '
                           f'font-size="10.5" fill="#4b5563">{esc(cn)}</text>')
            out.append(f'<text x="{x + self.w - 11}" y="{ty}" text-anchor="end" font-family="{MONO}" '
                       f'font-size="9.5" fill="#718096">{esc(typ)}</text>')
            ty += ROW_H
        if self.note:
            out.append(f'<text x="{x + 11}" y="{ty}" font-family="{FONT}" font-size="10.5" '
                       f'fill="{stroke}" opacity="0.92">{esc(self.note)}</text>')
        out.append("</g>")
        return "\n".join(out)


def B(col, y, cn, en, rows, color=C_ORG, note="", warn=False):
    return Box(COL_X + col * COL_W, y, cn, en, rows, color, note, warn)


def _clip(box, tx, ty):
    cx, cy = box.center()
    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0:
        return cx, cy
    hw, hh = box.w / 2, box.h / 2
    sx = abs(hw / dx) if dx else float("inf")
    sy = abs(hh / dy) if dy else float("inf")
    s = min(sx, sy)
    return cx + dx * s, cy + dy * s


def route(a, b, off=0):
    """折线点列：跨列走「横-竖-横」（竖线可横向偏移 off），同列走直线。"""
    if a is b:
        x, y = a.x, a.rect()[1]
        w = a.w
        return [(x + w * .38, y), (x + w * .34, y - 28), (x + w * .62, y - 28), (x + w * .66, y)], True
    ax, ay = a.center()
    bx, by = b.center()
    sx, sy = _clip(a, bx, by)
    ex, ey = _clip(b, ax, ay)
    if abs(b.x - a.x) > 380:
        mx = ((a.x + a.w + b.x) / 2 if b.x > a.x else (a.x + b.x + b.w) / 2) + off
        return [(sx, sy), (mx, sy), (mx, ey), (ex, ey)], False
    return [(sx, sy), (ex, ey)], False


def label_pos(pts):
    if len(pts) == 4:
        return pts[1][0], (pts[1][1] + pts[2][1]) / 2 - 2
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    return (x1 + x2) / 2, (y1 + y2) / 2 - 4


def edge_svg(a, b, label="", dashed=False, color="#4a5568", off=0):
    """只画线；标签由 Spec 在所有表框之后统一画，避免被框盖住。"""
    pts, self_loop = route(a, b, off)
    dash = 'stroke-dasharray="6 4"' if dashed else ""
    d = "M" + " L".join(f"{p[0]:.1f} {p[1]:.1f}" for p in pts)
    out = [f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.3" {dash}/>']
    if self_loop:
        return "\n".join(out)
    sx, sy = pts[0]
    ex, ey = pts[-1]
    out.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="2.6" fill="#fff" stroke="{color}" stroke-width="1.3"/>')
    lx, ly = pts[-2]
    vx, vy = ex - lx, ey - ly
    n = (vx * vx + vy * vy) ** .5 or 1
    ux, uy = vx / n, vy / n
    px, py = -uy, ux
    for t in (-5, 0, 5):
        out.append(f'<line x1="{ex - ux * 9:.1f}" y1="{ey - uy * 9:.1f}" x2="{ex + px * t:.1f}" '
                   f'y2="{ey + py * t:.1f}" stroke="{color}" stroke-width="1.3"/>')
    return "\n".join(out)


def label_svg(text, x, y):
    w = len(text) * 6.0 + 10
    return (f'<rect x="{x - w / 2:.1f}" y="{y - 11:.1f}" width="{w:.1f}" height="15" rx="3" '
            f'fill="#fff" opacity="0.95"/>\n'
            f'<text x="{x:.1f}" y="{y + 0.5:.1f}" text-anchor="middle" font-family="{MONO}" '
            f'font-size="9.5" fill="#4a5568">{esc(text)}</text>')


class Spec:
    def __init__(self, title, subtitle, boxes, edges):
        self.title, self.subtitle, self.boxes, self.edges = title, subtitle, boxes, edges
        self.w = max(b.x + b.w for b in boxes) + 40
        self.h = max(b.y + b.h for b in boxes) + 70

    def _labels(self):
        for a, b, kw in self.edges:
            lab = kw.get("label", "")
            if not lab:
                continue
            pts, _ = route(a, b, kw.get("off", 0))
            if a is b:
                yield lab, pts[0][0] - 34, pts[0][1] - 30
            else:
                x, y = label_pos(pts)
                yield lab, x, y

    def to_svg(self):
        p = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w:.0f}" height="{self.h:.0f}" '
            f'viewBox="0 0 {self.w:.0f} {self.h:.0f}">',
            '<rect width="100%" height="100%" fill="#fbfcfe"/>',
            f'<text x="20" y="27" font-family="{FONT}" font-size="17" font-weight="700" fill="#1a202c">'
            f'{esc(self.title)}</text>',
            f'<text x="20" y="47" font-family="{FONT}" font-size="11.5" fill="#718096">'
            f'{esc(self.subtitle)}</text>',
        ]
        for e in self.edges:
            p.append(edge_svg(e[0], e[1], **e[2]))
        for b in self.boxes:
            p.append(b.svg())
        for lab, x, y in self._labels():
            p.append(label_svg(lab, x, y))
        p.append(f'<text x="20" y="{self.h - 16}" font-family="{FONT}" font-size="10.5" fill="#718096">'
                 f'{esc(LEGEND)}</text>')
        p.append("</svg>")
        return "\n".join(p)

    def to_png(self, path, ss=2):
        from PIL import Image, ImageDraw, ImageFont

        msyh, msyhb, mono = ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc",
                             "C:/Windows/Fonts/consola.ttf")

        def f(name, size):
            return ImageFont.truetype(name, max(1, int(size * ss)))

        W, H = int(self.w * ss), int(self.h * ss)
        img = Image.new("RGB", (W, H), "#fbfcfe")
        d = ImageDraw.Draw(img)
        d.text((20 * ss, 12 * ss), self.title, font=f(msyhb, 17), fill="#1a202c")
        d.text((20 * ss, 38 * ss), self.subtitle, font=f(msyh, 11.5), fill="#718096")

        for a, b, kw in self.edges:
            pts, self_loop = route(a, b, kw.get("off", 0))
            col = kw.get("color", "#4a5568")
            P = [(x * ss, y * ss) for x, y in pts]
            if kw.get("dashed"):
                for i in range(len(P) - 1):
                    (x1, y1), (x2, y2) = P[i], P[i + 1]
                    dist = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** .5 or 1
                    seg = 6 * ss
                    k = 0
                    while k * 2 * seg < dist:
                        t0, t1 = k * 2 * seg / dist, min((k * 2 + 1) * seg / dist, 1)
                        d.line([x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0,
                                x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1],
                               fill=col, width=max(1, int(1.3 * ss)))
                        k += 1
            else:
                for i in range(len(P) - 1):
                    d.line([P[i], P[i + 1]], fill=col, width=max(1, int(1.3 * ss)))
            if self_loop:
                continue
            sx, sy = P[0]
            ex, ey = P[-1]
            d.ellipse([sx - 2.6 * ss, sy - 2.6 * ss, sx + 2.6 * ss, sy + 2.6 * ss], fill="white", outline=col)
            lx, ly = P[-2]
            vx, vy = ex - lx, ey - ly
            n = (vx * vx + vy * vy) ** .5 or 1
            ux, uy = vx / n, vy / n
            px, py = -uy, ux
            for t in (-5, 0, 5):
                d.line([ex - ux * 9 * ss, ey - uy * 9 * ss, ex + px * t * ss, ey + py * t * ss],
                       fill=col, width=max(1, int(1.3 * ss)))

        for b in self.boxes:
            x, y, x2, y2 = [v * ss for v in b.rect()]
            stroke, fill = b.color
            d.rounded_rectangle([x, y, x2, y2], radius=7 * ss, fill="white", outline=stroke,
                                width=max(1, int((2 if b.warn else 1.4) * ss)))
            d.rounded_rectangle([x, y, x2, y + HEAD_H * ss], radius=7 * ss, fill=fill)
            d.rectangle([x, y + HEAD_H * ss - 8 * ss, x2, y + HEAD_H * ss], fill=fill)
            d.text((x + 11 * ss, y + 13 * ss), b.cn, font=f(msyhb, 13.5), fill=stroke)
            d.text((x + 11 * ss, y + 31 * ss), b.en, font=f(mono, 10), fill="#5a6472")
            ty = y + HEAD_H * ss + 4 * ss
            for name, typ, mark, size, cn, namew in b.rows:
                badge = {"PK": "#b7791f", "FK": "#3182ce", "!": "#c53030"}.get(mark)
                if badge:
                    d.text((x + 11 * ss, ty), "!!" if mark == "!" else mark, font=f(mono, 9), fill=badge)
                d.text((x + 36 * ss, ty - 1 * ss), name, font=f(msyh, size),
                       fill="#c53030" if mark == "!" else "#1a202c")
                if cn:
                    d.text(((36 + namew + 10) * ss + x, (ty - 1 * ss)), cn,
                           font=f(msyh, 10.5), fill="#4b5563")
                d.text((x2 - 11 * ss, ty), typ, font=f(mono, 9.5), fill="#718096", anchor="ra")
                ty += ROW_H * ss
            if b.note:
                d.text((x + 11 * ss, ty), b.note, font=f(msyh, 10.5), fill=stroke)

        for lab, lx, ly in self._labels():
            wlab = (len(lab) * 6.0 + 10) * ss
            d.rectangle([lx * ss - wlab / 2, (ly - 11) * ss, lx * ss + wlab / 2, (ly + 4) * ss],
                        fill="white")
            d.text((lx * ss, (ly - 2.5) * ss), lab, font=f(msyh, 9.5), fill="#4a5568", anchor="ms")
        d.text((20 * ss, (self.h - 24) * ss), LEGEND, font=f(msyh, 10.5), fill="#718096")
        img.resize((int(self.w), int(self.h)), Image.LANCZOS).save(path, optimize=True)


def fig_global():
    o = B(0, 70, "组织与权限（10 表）", "ORG", [
        ("t_department 部门", "52", ""), ("t_employee 员工", "127", ""),
        ("t_role 角色", "7", ""), ("t_role_employee 角色-员工", "139", ""),
        ("t_menu / t_role_menu", "167 / 85", ""), ("t_role_data_scope 数据范围", "1", "!"),
    ], C_ORG, note="范围只配了所长 1 个角色")
    m = B(0, 400, "主数据（12 表）", "MDM", [
        ("supply_company 县公司", "4", ""), ("power_company 供电所", "47", ""),
        ("power_substation 变电站", "11", ""), ("power_line 线路", "670", ""),
        ("power_area 台区", "24892", ""),
    ], C_BIZ, note="所有业务数据最终落在 所/线/台区")
    bu = B(0, 700, "预算与评分（8 表）", "BUDGET", [
        ("budget_allocation 预算配置", "12", ""), ("budget_profile 画像", "46", ""),
        ("budget_result 预算结果", "470", ""), ("scoring_standard/rank_weight", "16 / 23", ""),
        ("station_score_detail 评分明细", "9686", ""), ("governance_effectiveness", "15", ""),
    ], C_BIZ, note="按所（power_station_id）切分")
    w = B(1, 70, "工单与项目（12 表）", "WORKORDER", [
        ("power_work_order 工单", "6533", ""), ("work_order_status 状态流转", "34459", ""),
        ("work_order_record 治理记录", "4221", ""), ("work_order_audit_task 审批", "324", ""),
        ("work_order_flow_node 流程节点", "7", ""), ("power_work_cost 工单费用", "18542", ""),
        ("cost_material 物料价", "6356", ""),
    ], C_BIZ, note="所字段是 varchar，联表要 CAST")
    pu = B(1, 400, "中台推送落库（10 表）", "PUSH", [
        ("push_outage 停电用户", "171870", ""), ("push_trip 跳闸", "109905", ""),
        ("push_sales_volume 售电量", "252861", ""), ("push_line_loss 台区线损", "0", "!"),
        ("push_district_month_summary", "48", ""), ("push_station_month_summary", "0", "!"),
    ], C_PUSH, note="自带 power_station_id，最干净")
    ai = B(1, 700, "AI 指标分析（4 表）", "AI-METRIC", [
        ("ai_metric_snapshot 六指标快照", "1296", ""), ("ai_metric_comparison 同比环比", "1482", ""),
        ("ai_area_metric_snapshot 台区快照", "159101", ""),
        ("ai_area_metric_comparison 台区对比", "313070", ""),
    ], C_AI, note="六指标表只有文本 scope_name")
    sy = B(2, 70, "系统支撑（约 45 表）", "SYS", [
        ("t_dict / t_dict_data 字典", "19 / 92", ""), ("t_file 文件", "6589", ""),
        ("t_login_log 登录日志", "1028", ""), ("t_operate_log 操作日志", "589", ""),
        ("t_notice 通知", "12", ""), ("process_* 流程引擎", "全 0", "!"),
    ], C_SYS, note="日志按 employee_id 关联员工（跨列未画线）")
    me = B(2, 400, "AI 元数据与问答（12 表）", "AI-META", [
        ("ai_table_metadata 表元数据", "10", ""), ("ai_column_metadata 字段元数据", "129", ""),
        ("ai_metric_metadata 指标元数据", "14", ""), ("ai_table_relation 表关联", "5", ""),
        ("ai_prompt 提示词", "19", ""), ("t_ai_conversation_* 会话/消息", "9 / 18", ""),
        ("ai_chat_trace 链路追踪", "153", ""),
    ], C_AI, note="老系统已登记关系与指标")
    jk = B(2, 700, "备份与暂存（12 表）", "DEPRECATED", [
        ("*_bak_* / *_copy*", "副本", "!"), ("stg_power_work_order", "0", "!"),
        ("power_work_order_copy1/2", "5512 / 5916", "!"),
    ], C_SYS, note="不要当数据源（数据地图第 1 条）")

    E = [
        (o, m, dict(label="department_id")),
        (m, bu, dict(label="power_station_id")),
        (m, w, dict(label="power_company_id")),
        (pu, m, dict(label="power_station_id", off=70)),
        (ai, m, dict(label="scope_name 文本", dashed=True, color="#c53030", off=-70)),
        (me, ai, dict(label="登记/描述", dashed=True, off=-45)),
        (jk, w, dict(label="已废弃副本", dashed=True, off=38)),
    ]
    return Spec("图 0 · 全局域地图：dlj_data 138 张表怎么分成 9 块",
                "数字是 SELECT COUNT(*) 精确行数（2026-10-20 实测）。红框 = 这块有已知坑。",
                [o, m, bu, w, pu, ai, sy, me, jk], E)


def fig_org():
    d = B(0, 104, "部门（组织树）", "t_department", [
        ("department_id", "bigint", "PK"), ("department_name", "varchar(50)", ""),
        ("parent_id", "bigint", "FK"), ("manager_id", "bigint", "FK"),
    ], C_ORG, note="52 行：1=市公司，10-13=县公司，14+=所")
    e = B(1, 104, "员工（登录源=HR）", "t_employee", [
        ("employee_id", "bigint", "PK"), ("login_name", "varchar(30)", ""),
        ("actual_name", "varchar(30)", ""), ("department_id", "bigint", "FK"),
        ("administrator_flag", "tinyint", ""),
    ], C_ORG, note="127 行；administrator_flag=1 只 2 人")
    r = B(2, 104, "角色", "t_role", [
        ("role_id", "bigint", "PK"), ("role_code", "varchar(500)", ""),
        ("role_name", "varchar(20)", ""),
    ], C_ORG, note="7 个：sz/cto/yxb/xgscwb/sgscwb/master/codea")
    m = B(3, 104, "菜单/按钮（功能权限）", "t_menu", [
        ("menu_id", "bigint", "PK"), ("menu_name", "varchar(200)", ""),
        ("menu_type", "int", ""), ("parent_id", "bigint", "FK"),
        ("api_perms", "varchar(5000)", ""), ("web_perms", "varchar(5000)", ""),
    ], C_SYS, note="167 行：98 个按钮带 api_perms")
    sc = B(0, 360, "县级供电公司", "t_power_supply_company", [
        ("id", "bigint", "PK"), ("name", "varchar(250)", ""), ("department_id", "bigint", "FK"),
    ], C_BIZ, note="4 行（曾都/随县/广水/高新）")
    re = B(1, 360, "角色-员工", "t_role_employee", [
        ("id", "bigint", "PK"), ("role_id", "bigint", "FK"), ("employee_id", "bigint", "FK"),
    ], C_ORG, note="139 行；一人可挂多角色")
    ds = B(2, 360, "角色的数据范围", "t_role_data_scope", [
        ("id", "bigint", "PK"), ("role_id", "bigint", "FK"),
        ("data_scope_type", "int", "!"), ("view_type", "int", "!"),
    ], warn=True, note="⚠ 只有 1 行（role_id=1 所长），取值无字典说明")
    rm = B(3, 360, "角色-菜单", "t_role_menu", [
        ("role_menu_id", "bigint", "PK"), ("role_id", "bigint", "FK"), ("menu_id", "bigint", "FK"),
    ], C_SYS, note="85 行：每角色 8~24 个")
    pc = B(0, 660, "供电所（47 个数据主体）", "t_power_company", [
        ("id", "bigint", "PK"), ("name", "varchar(250)", ""), ("code", "varchar(50)", ""),
        ("department_id", "bigint", "FK"), ("parent_id", "bigint", "!"),
    ], C_BIZ, note="47 行；parent_id 注释「暂无关联」")
    ss = B(1, 660, "变电站", "t_power_substation", [
        ("id", "bigint", "PK"), ("name", "varchar(200)", ""), ("company_id", "bigint", "!"),
    ], C_BIZ, note="11 行；company_id 指所还是县公司，注释没写")

    E = [
        (d, d, dict(label="parent_id 自关联")),
        (e, d, dict(label="employee.department_id")),
        (d, e, dict(label="department.manager_id")),
        (re, r, dict(label="role_id")),
        (re, e, dict(label="employee_id")),
        (ds, r, dict(label="role_id", color="#c53030")),
        (rm, r, dict(label="role_id")),
        (rm, m, dict(label="menu_id")),
        (m, m, dict(label="menu.parent_id 自关联")),
        (sc, d, dict(label="department_id")),
        (pc, d, dict(label="department_id")),
        (ss, pc, dict(label="company_id ?", dashed=True)),
    ]
    return Spec("图 1 · 局部 A：组织 → 角色 → 权限 → 数据主体（「谁能看哪些所」的全部原料）",
                "四个环节：员工在哪个部门、部门下有哪些所、角色是谁、范围配了什么。",
                [d, e, r, m, sc, re, ds, rm, pc, ss], E)


def fig_budget():
    sc = B(0, 70, "县级供电公司", "t_power_supply_company", [
        ("id", "bigint", "PK"), ("name", "varchar(250)", ""),
    ], C_BIZ, note="4 行（曾都/随县/广水/高新）")
    pc = B(0, 300, "供电所", "t_power_company", [
        ("id", "bigint", "PK"), ("name", "varchar(250)", ""), ("department_id", "bigint", "FK"),
    ], C_BIZ, note="47 行")
    al = B(1, 70, "预算配置（按县/年）", "t_power_company_budget_allocation", [
        ("id", "bigint", "PK"), ("county_company_id", "bigint", "FK"),
        ("year", "varchar(10)", ""), ("total_budget", "decimal(20,2)", ""),
        ("base_value K值基数", "varchar(10)", ""), ("is_new", "tinyint", ""),
    ], C_BIZ, note="12 行；is_new=1 才是当前版本")
    rs = B(1, 300, "预算分配结果", "t_power_company_budget_result", [
        ("id", "bigint", "PK"), ("allocation_id", "bigint", "FK"),
        ("county_company_id", "bigint", "FK"), ("power_station_id", "bigint", "FK"),
        ("year", "varchar(10)", ""),
    ], C_BIZ, note="470 行；所+县双份冗余")
    pf = B(1, 560, "预算画像（按所/年）", "t_power_company_budget_profile", [
        ("id", "bigint", "PK"), ("company_id", "bigint", "FK"), ("year", "int", ""),
    ], C_BIZ, note="46 行（47 个所差一个）")
    sstd = B(2, 70, "评分标准（指标字典）", "t_power_company_scoring_standard", [
        ("id", "bigint", "PK"), ("metric_key", "varchar(100)", ""),
        ("direction", "varchar(20)", ""), ("full_score_pct", "decimal(10,2)", ""),
        ("is_new", "tinyint", ""),
    ], C_BIZ, note="16 行（16 个指标）")
    sd = B(2, 300, "供电所评分明细", "t_power_station_score_detail", [
        ("id", "bigint", "PK"), ("power_station_id", "bigint", "FK"),
        ("county_company_id", "bigint", ""), ("budget_result_id", "bigint", "FK"),
        ("year", "varchar(10)", ""), ("metric_key", "varchar(100)", ""),
        ("weight", "decimal(10,4)", ""),
    ], C_BIZ, note="9686 行 = 所 × 指标 × 年；power_station_id 同指供电所（未画线）")
    rw = B(2, 620, "排名权重", "t_power_company_rank_weight", [
        ("id", "bigint", "PK"), ("metric_key", "varchar(100)", ""),
        ("weight", "decimal(10,6)", ""), ("rank_type", "varchar(50)", ""), ("is_new", "tinyint", ""),
    ], C_BIZ, note="23 行")

    E = [
        (al, sc, dict(label="county_company_id")),
        (rs, al, dict(label="allocation_id")),
        (rs, sc, dict(label="county_company_id")),
        (rs, pc, dict(label="power_station_id")),
        (pf, pc, dict(label="company_id")),
        (sd, rs, dict(label="budget_result_id")),
        (sd, sstd, dict(label="metric_key 文本", dashed=True)),
        (sd, rw, dict(label="metric_key 文本", dashed=True)),
    ]
    return Spec("图 2 · 局部 B：预算 → 评分 → 排名（预警的 budget / review 两类在这条链上）",
                "注意：county_company_id 指县公司表，company_id / power_station_id 指供电所表——同名不同义。",
                [sc, pc, al, rs, pf, sstd, sd, rw], E)


def fig_wo():
    pc = B(0, 104, "供电所", "t_power_company", [
        ("id", "bigint", "PK"), ("name", "varchar(250)", ""),
    ], C_BIZ, note="47 行")
    ln = B(0, 320, "线路", "t_power_line", [
        ("id", "bigint", "PK"), ("line_code", "varchar(200)", ""),
        ("power_id", "bigint", "FK"), ("substation_id", "bigint", "FK"),
    ], C_BIZ, note="670 行")
    ar = B(0, 560, "台区", "t_power_area", [
        ("id", "bigint", "PK"), ("area_code", "varchar(100)", ""),
        ("line_id", "bigint", "FK"), ("power_id", "bigint", "FK"),
    ], C_BIZ, note="24892 行")
    wo = B(1, 104, "工单 / 项目储备", "t_power_work_order", [
        ("id", "bigint", "PK"), ("source_id", "varchar(100)", ""),
        ("power_company_id", "varchar(50)", "!"), ("power_line_id", "varchar(50)", "!"),
        ("power_area_id", "varchar(50)", "!"), ("applicant_id", "varchar(50)", ""),
        ("flow_instance_id", "bigint", "!"),
    ], C_BIZ, note="6533 行；⚠ 77 列里 *_id 全是 varchar")
    st = B(2, 104, "状态流转（流程轨迹）", "t_power_work_order_status", [
        ("id", "bigint", "PK"), ("work_order_id", "bigint", "FK"),
        ("node_code", "varchar(50)", ""), ("work_status", "tinyint", ""),
        ("audit_task_id", "bigint", "FK"), ("approval_round", "int", ""),
    ], C_BIZ, note="34459 行，最大的一张轨迹表")
    fn = B(3, 104, "流程节点定义", "t_work_order_flow_node", [
        ("id", "bigint", "PK"), ("node_code", "varchar(50)", ""),
        ("node_name", "varchar(100)", ""), ("node_order", "int", ""),
        ("role_code", "varchar(50)", ""),
    ], C_BIZ, note="7 行；role_code 对 t_role.role_code")
    at = B(2, 400, "审批任务", "t_work_order_audit_task", [
        ("id", "bigint", "PK"), ("work_order_id", "bigint", "FK"),
        ("node_code", "varchar(50)", ""), ("assignee_id", "bigint", "FK"),
    ], C_BIZ, note="324 行")
    rd = B(1, 420, "设备缺陷治理记录", "t_power_work_order_record", [
        ("id", "bigint", "PK"), ("work_order_id", "bigint", "FK"),
    ], C_BIZ, note="4221 行")
    co = B(1, 640, "工单预算费用", "t_power_work_cost", [
        ("id", "bigint", "PK"), ("work_order_id", "bigint", "FK"),
        ("work_order_record_id", "bigint", "FK"), ("cost_item_id", "bigint", "FK"),
    ], C_BIZ, note="18542 行")

    E = [
        (wo, pc, dict(label="power_company_id vc", dashed=True, color="#c53030")),
        (wo, ln, dict(label="power_line_id vc", dashed=True, color="#c53030")),
        (wo, ar, dict(label="power_area_id vc", dashed=True, color="#c53030")),
        (st, wo, dict(label="work_order_id")),
        (st, at, dict(label="audit_task_id")),
        (at, wo, dict(label="work_order_id")),
        (rd, wo, dict(label="work_order_id")),
        (co, wo, dict(label="work_order_id")),
        (co, rd, dict(label="record_id")),
        (ln, pc, dict(label="power_id")),
        (ar, ln, dict(label="line_id")),
        (ar, pc, dict(label="power_id")),
        (st, fn, dict(label="node_code", dashed=True)),
        (fn, fn, dict(label="role_code → t_role")),
    ]
    return Spec("图 3 · 局部 C：工单 → 流程状态 → 审批 → 费用（预警的 project 一类在这条链上）",
                "红虚线 = 字段是 varchar（缩写 vc），与 bigint 主键联表要 CAST + COLLATE，否则 MySQL 报 1267。",
                [pc, ln, ar, wo, st, fn, at, rd, co], E)


def fig_grid():
    pc = B(0, 70, "供电所", "t_power_company", [
        ("id", "bigint", "PK"), ("name", "varchar(250)", ""), ("department_id", "bigint", "FK"),
    ], C_BIZ, note="47 行")
    ar = B(0, 340, "台区", "t_power_area", [
        ("id", "bigint", "PK"), ("area_code", "varchar(100)", ""), ("power_id", "bigint", "FK"),
        ("line_id", "bigint", "FK"), ("loss_rate", "decimal(10,6)", ""),
    ], C_BIZ, note="24892 行；power_id 指所")
    am = B(0, 620, "AI 台区指标快照", "t_power_ai_area_metric_snapshot", [
        ("id", "bigint", "PK"), ("company_id", "bigint", "FK"),
        ("power_station_id", "bigint", "FK"), ("line_id", "bigint", "FK"), ("area_id", "bigint", "FK"),
    ], C_AI, note="159101 行；有 id 能过滤（与供电所也同源，未画线）")
    ms = B(1, 70, "AI 六指标快照", "t_power_ai_metric_snapshot", [
        ("id", "bigint", "PK"), ("scope_type", "varchar(20)", "!"),
        ("scope_key", "varchar(128)", "!"), ("scope_name", "varchar(128)", "!"),
    ], warn=True, note="⚠ 只有文本范围，没有 station_id")
    ou = B(1, 300, "停电用户（推送）", "t_power_push_outage", [
        ("id", "bigint", "PK"), ("power_station_id", "bigint", "FK"),
        ("area_id", "bigint", "FK"), ("resource_id", "varchar(100)", ""),
    ], C_PUSH, note="171870 行")
    tr = B(1, 480, "跳闸事件（推送）", "t_power_push_trip", [
        ("id", "bigint", "PK"), ("power_station_id", "bigint", "FK"),
        ("area_id", "bigint", "FK"), ("resource_id", "varchar(100)", ""),
    ], C_PUSH, note="109905 行")
    sv = B(1, 660, "台区售电量（推送）", "t_power_push_sales_volume", [
        ("id", "bigint", "PK"), ("power_station_id", "bigint", "FK"),
        ("area_id", "bigint", "FK"), ("resource_id", "varchar(100)", ""),
    ], C_PUSH, note="252861 行")
    ll = B(1, 840, "台区线损（推送）", "t_power_push_line_loss", [
        ("id", "bigint", "PK"), ("power_station_id", "bigint", "FK"), ("area_id", "bigint", "FK"),
    ], warn=True, note="⚠ 0 行 —— 线上这个口径是空的")
    dm = B(2, 70, "区县月汇总（推送）", "t_power_push_district_month_summary", [
        ("id", "bigint", "PK"), ("stat_month", "char(6)", ""),
        ("summary_type", "varchar(20)", ""), ("district_name", "varchar(100)", "!"),
    ], C_PUSH, note="48 行；只有区县名，对不上供电所 id")
    sm = B(2, 300, "供电所月汇总（推送）", "t_power_push_station_month_summary", [
        ("id", "bigint", "PK"), ("stat_month", "char(6)", ""),
        ("station_name", "varchar(200)", "!"), ("resource_id", "varchar(100)", "!"),
    ], C_PUSH, warn=True, note="⚠ 0 行；按所名 + 中台 resource_id 对")

    E = [
        (ms, pc, dict(label="scope_name 文本", dashed=True, color="#c53030")),
        (ou, pc, dict(label="station_id", off=0)),
        (tr, pc, dict(label="station_id", off=22)),
        (sv, pc, dict(label="station_id", off=44)),
        (ll, pc, dict(label="station_id", off=66)),
        (ou, ar, dict(label="area_id", off=0)),
        (sv, ar, dict(label="area_id", off=30)),
        (ar, pc, dict(label="power_id")),
        (am, ar, dict(label="area_id")),
        (sm, ms, dict(label="都是按名字对范围", dashed=True, color="#c53030", off=-40)),
        (dm, ms, dict(label="都是按名字对范围", dashed=True, color="#c53030", off=-40)),
    ]
    return Spec("图 4 · 局部 D：所 → 台区 → 推送明细 → 指标（线损/可靠性/售电量在这条链上）",
                "能 JOIN 的（实线）与只能按名字对上的（红虚线）混在一起——这就是「按所过滤」真正的难度。",
                [pc, ar, am, ms, ou, tr, sv, ll, dm, sm], E)



DIAGRAMS = {
    "er_global": fig_global,
    "er_org": fig_org,
    "er_budget": fig_budget,
    "er_workorder": fig_wo,
    "er_grid": fig_grid,
}

if __name__ == "__main__":
    for name, fn in DIAGRAMS.items():
        spec = fn()
        with open(os.path.join(OUT_DIR, name + ".svg"), "w", encoding="utf-8") as f:
            f.write(spec.to_svg())
        spec.to_png(os.path.join(OUT_DIR, name + ".png"))
        print("wrote %-14s %dx%d" % (name, spec.w, spec.h))
