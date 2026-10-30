# -*- coding: utf-8 -*-
"""生成 ER/index.html（自包含，离线可看）与 ER-说明.md（同内容，Markdown 版）。"""
import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

FIGS = [
    ("er_global", "图 0 · 全局域地图",
     "138 张表按业务域分成 9 块。读法是「先看块、再看块内代表表」——**看数字就知道哪块有真数据、哪块是空壳**。"),
    ("er_org", "图 1 · 局部 A：组织 → 角色 → 权限 → 数据主体",
     "「本所角色只能看本所」要用到的全部原料都在这张图里：员工挂哪个部门、部门下有哪些所、角色是谁、"
     "范围配了几行。红框那张 `t_role_data_scope` 就是全系统唯一的「数据范围配置」，实测只有 1 行。"),
    ("er_budget", "图 2 · 局部 B：预算 → 评分 → 排名",
     "预警里 `budget`（预算花不完/超支）和 `review`（投入产出低）两类都在这条链上。"
     "核心陷阱：`county_company_id` 指县公司表，`company_id` / `power_station_id` 指供电所表——**同名不同义**。"),
    ("er_workorder", "图 3 · 局部 C：工单 → 流程状态 → 审批 → 费用",
     "预警里 `project`（审批停留、已批未开工、施工停滞）在这条链上。红虚线那三条是**工程上的真坑**："
     "`t_power_work_order` 的 77 列里 `*_id` 全是 `varchar`，与 `bigint` 主键联表必须 `CAST` + `COLLATE`。"),
    ("er_grid", "图 4 · 局部 D：所 → 台区 → 推送明细 → 指标",
     "线损 / 可靠性 / 售电量这条链。这张图最能说明「按所过滤」为什么不是配置问题："
     "有的表有 `power_station_id`（能 JOIN），有的只有 `scope_name` / `station_name` 文本（只能按名字对，"
     "而部门表叫「新城供电所」、档案写「新城供电站」）。"),
]

RELATIONS = [
    ("t_department.parent_id → t_department.department_id", "自关联（组织树）", "字段注释", "实线"),
    ("t_employee.department_id → t_department", "员工归属部门", "字段注释", "实线"),
    ("t_department.manager_id → t_employee.employee_id", "部门负责人", "字段注释", "实线"),
    ("t_role_employee.role_id / employee_id → t_role / t_employee", "角色-员工", "字段注释", "实线"),
    ("t_role_data_scope.role_id → t_role", "角色范围（只 1 行）", "字段注释", "实线（红）"),
    ("t_role_menu.role_id / menu_id → t_role / t_menu", "角色-菜单/按钮", "字段注释", "实线"),
    ("t_power_supply_company.department_id → t_department", "县公司挂部门", "字段注释", "实线"),
    ("t_power_company.department_id → t_department", "供电所挂部门", "字段注释", "实线"),
    ("t_power_company.parent_id → ?", "注释放明「暂无关联」", "注释否定", "不画"),
    ("t_power_line.power_id → t_power_company.id", "线路的所", "字段注释", "实线"),
    ("t_power_area.power_id → t_power_company.id", "台区的所", "字段注释", "实线"),
    ("t_power_area.line_id → t_power_line.id", "台区的线", "字段注释", "实线"),
    ("t_power_work_order.power_company_id → t_power_company.id", "工单的所", "ai_table_relation + 命名", "虚线（varchar）"),
    ("t_power_work_order.power_line_id → t_power_line.id", "工单的线", "ai_table_relation", "虚线（varchar）"),
    ("t_power_work_order.power_area_id → t_power_area.id", "工单的台区", "ai_table_relation", "虚线（varchar）"),
    ("t_power_work_order_status.work_order_id → 工单", "流程轨迹", "字段注释", "实线"),
    ("t_work_order_audit_task.work_order_id → 工单", "审批任务", "字段注释", "实线"),
    ("t_work_order_audit_task.assignee_id → t_employee", "办理人", "命名（未画线）", "按命名推测"),
    ("t_power_work_cost.work_order_id / record_id → 工单/记录", "费用归属", "字段注释", "实线"),
    ("t_power_company_budget_result.allocation_id → budget_allocation", "预算结果←配置", "字段注释", "实线"),
    ("t_power_company_budget_result.county_company_id → t_power_supply_company", "县", "ai_table_relation（同构表）", "实线"),
    ("t_power_company_budget_result.power_station_id → t_power_company", "所", "字段注释", "实线"),
    ("t_power_company_budget_profile.company_id → t_power_company", "画像的所", "字段注释", "实线"),
    ("t_power_station_score_detail.budget_result_id → budget_result", "评分←预算结果", "字段注释", "实线"),
    ("t_power_station_score_detail.metric_key → scoring_standard.metric_key", "指标字典", "命名（文本，无 FK）", "虚线"),
    ("t_power_push_outage / trip / sales_volume / line_loss.power_station_id → t_power_company", "推送→所", "字段注释", "实线"),
    ("t_power_push_*.area_id → t_power_area.id", "推送→台区", "字段注释", "实线"),
    ("t_power_ai_metric_snapshot.scope_name → 供电所/县名", "文本范围，无 id", "无依据（只能按名字对）", "红虚线"),
    ("t_power_push_station_month_summary.station_name → 供电所名", "文本范围，无 id", "无依据", "红虚线"),
    ("t_power_ai_area_metric_snapshot.*_id → 县/所/线/台区", "有 id 能过滤", "字段注释", "实线"),
]

PITFALLS = [
    ("`t_role_data_scope` 只有 1 行", "全系统「数据范围」配置只给所长（role_id=1）配了一条，"
     "`data_scope_type=1 / view_type=2` 且**没有任何字典说明取值含义**（`t_dict` 里查不到）。",
     "`SELECT * FROM t_role_data_scope` → 1 行"),
    ("工单表的 id 是 varchar", "`t_power_work_order` 的 `power_company_id` / `power_line_id` / `power_area_id` / "
     "`applicant_id` 全是 `varchar(50)`，与 `bigint` 主键联表会直接报错。",
     "实测 `LEFT JOIN` 报 1267：`Illegal mix of collations (utf8mb4_0900_ai_ci vs utf8mb4_general_ci)`"),
    ("同名字段不同义", "`company_id` = 供电所 id（`t_power_company`）；`county_company_id` = 县公司 id"
     "（`t_power_supply_company`）；两者都出现在预算相关表里，取值都从 1 开始。",
     "`budget_profile.company_id=3` 是厉山供电所；`budget_result.county_company_id=3` 是某个县公司"),
    ("AI 指标表没有 id 维度", "`t_power_ai_metric_snapshot` / `_comparison` 只有 `scope_type` + `scope_key` + "
     "`scope_name` 文本，没有 `station_id`，无法 JOIN 供电所表。",
     "`scope_name` 取值：随州市 / 曾都区供电公司 / 文峰供电所 …"),
    ("两张「汇总表」是空的", "`t_power_push_station_month_summary` 和 `t_power_push_line_loss` 都是 0 行，"
     "但文档/口径里被当成主力数据源。", "`SELECT COUNT(*)` → 0"),
    ("文档行数不可信", "`docs/06-口径与数据/口径/数据地图.md` 记的是 133 张表、`t_role_data_scope` 0 行、"
     "`t_role_employee` 124 行——都是 `information_schema.table_rows` 的 InnoDB **估算值**。",
     "实测 138 张表 / 1 行 / 139 行"),
]

COUNTS = [
    ("t_power_company", "47"), ("t_power_area", "24892"), ("t_power_work_order", "6533"),
    ("t_role_data_scope", "1"), ("t_employee", "127"),
]


def rich(t: str) -> str:
    """把说明里的 **加粗** 和 `代码` 转成 HTML，其余转义。"""
    import html as _h
    t = _h.escape(t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
    return t



def md() -> str:
    out = [
        "# dlj_data 业务库 E-R 图（全局 + 4 张局部）",
        "",
        "> 2026-10-20 只读连 `47.121.183.81:3306/dlj_data` 实测生成。",
        "> 行数是 `SELECT COUNT(*)` 的**精确值**，不是 `information_schema.table_rows` 的估算值。",
        "> 图片：`docs/06-口径与数据/ER/*.svg`（矢量）/ `*.png`（预览）。复现：`python build_er.py`。",
        "",
        "## 一句话",
        "",
        "库里 **138 张表**，真正有业务的只有 5 个域；「谁能看哪些所」这件事，"
        "**角色和组织树是齐的，数据范围配置只有 1 行，业务表的组织字段有 3 种不一致的写法**。",
        "",
        "## 线型约定",
        "",
        "| 画法 | 含义 |",
        "|---|---|",
        "| 实线 | 字段注释写明「关联 表.id」，或 `ai_table_relation` 登记过 |",
        "| 虚线 | 只有命名一致，注释没写 → 按命名推测，**用前要核对数据** |",
        "| 红虚线 | 文本范围字段（按名字对，不是 JOIN） |",
        "| 红框 | 该表/字段有已知问题 |",
        "| ○ / ⊰ | 1 端 / 多端（crow's foot） |",
        "",
    ]
    for name, title, desc in FIGS:
        out += [f"## {title}", "", f"![{title}](ER/{name}.png)", "", desc, ""]
    out += ["## 关系依据清单", "",
            "| 关系 | 含义 | 依据 | 画法 |", "|---|---|---|---|"]
    for a, b, c, d in RELATIONS:
        out.append(f"| `{a}` | {b} | {c} | {d} |")
    out += ["", "## 6 个已知坑（都有实测证据）", "",
            "| 坑 | 说明 | 证据 |", "|---|---|---|"]
    for a, b, c in PITFALLS:
        out.append(f"| {a} | {b} | {c} |")
    out += ["", "## 行数核对（图上数字的抽样）", "", "| 表 | COUNT(*)|", "|---|---|"]
    for a, b in COUNTS:
        out.append(f"| `{a}` | {b} |")
    out.append("")
    return "\n".join(out)


def html() -> str:
    css = """
    :root{--bg:#fbfcfe;--ink:#1a202c;--dim:#718096;--line:#e2e8f0}
    *{box-sizing:border-box}
    body{margin:0;background:var(--bg);color:var(--ink);
         font:15px/1.7 'Microsoft YaHei','Segoe UI',sans-serif}
    header{padding:28px 40px 18px;border-bottom:1px solid var(--line);background:#fff}
    h1{margin:0 0 6px;font-size:22px}
    header p{margin:0;color:var(--dim);font-size:13px}
    nav{position:sticky;top:0;background:#fffffff2;backdrop-filter:blur(6px);
        border-bottom:1px solid var(--line);padding:10px 40px;z-index:9}
    nav a{margin-right:18px;color:#2b6cb0;text-decoration:none;font-size:13px}
    nav a:hover{text-decoration:underline}
    main{padding:24px 40px 80px;max-width:2000px}
    section{margin-bottom:52px}
    h2{font-size:18px;margin:0 0 6px;padding-top:8px}
    .desc{color:var(--dim);font-size:13.5px;margin:0 0 14px;max-width:1100px}
    .fig{border:1px solid var(--line);border-radius:10px;background:#fff;overflow:auto;
         box-shadow:0 1px 3px #0000000a}
    .fig img{display:block;width:100%;height:auto}
    table{border-collapse:collapse;width:100%;font-size:13px;margin:10px 0 4px;
          background:#fff;border:1px solid var(--line);border-radius:8px;overflow:hidden}
    th,td{border-bottom:1px solid var(--line);padding:8px 12px;text-align:left;vertical-align:top}
    th{background:#f7fafc;font-weight:600;color:#2d3748;white-space:nowrap}
    code{background:#f1f5f9;padding:1px 5px;border-radius:4px;font-family:Consolas,monospace;
         font-size:12.5px}
    .kv{display:flex;gap:28px;flex-wrap:wrap;margin:6px 0 18px}
    .kv div{background:#fff;border:1px solid var(--line);border-radius:8px;padding:10px 16px;min-width:150px}
    .kv b{display:block;font-size:20px;color:#2b6cb0}
    .kv span{color:var(--dim);font-size:12.5px}
    footer{padding:20px 40px;color:var(--dim);font-size:12.5px;border-top:1px solid var(--line)}
    """
    nav = "".join(f'<a href="#{n}">{t.split("·")[0].strip()}</a>' for n, t, _ in FIGS)
    body = []
    for n, t, d in FIGS:
        body.append(
            f'<section id="{n}"><h2>{t}</h2><p class="desc">{rich(d)}</p>'
            f'<div class="fig"><img src="{n}.svg" alt="{t}"></div></section>'
        )
    rows = "".join(f"<tr><td><code>{a}</code></td><td>{b}</td><td>{c}</td><td>{d}</td></tr>"
                   for a, b, c, d in RELATIONS)
    pit = "".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td></tr>" for a, b, c in PITFALLS)
    cnt = "".join(f"<tr><td><code>{a}</code></td><td>{b}</td></tr>" for a, b in COUNTS)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>dlj_data 业务库 E-R 图</title><style>{css}</style></head>
<body>
<header>
  <h1>dlj_data 业务库 E-R 图（全局 + 4 张局部）</h1>
  <p>2026-10-20 只读实测生成 · 行数是 SELECT COUNT(*) 精确值 · 矢量图在 er_*.svg，复现：<code>python build_er.py</code></p>
</header>
<nav>{nav}<a href="#rel">关系依据</a><a href="#pit">已知坑</a></nav>
<main>
  <div class="kv">
    <div><b>138</b><span>张表（文档记 133，已过期）</span></div>
    <div><b>9</b><span>个业务域</span></div>
    <div><b>1 / 7</b><span>角色里配了数据范围的</span></div>
    <div><b>3 种</b><span>业务表里「所」的存法</span></div>
    <div><b>0 行</b><span>t_power_push_line_loss / station_month_summary</span></div>
  </div>
  <section>
    <h2>怎么读这些图</h2>
    <p class="desc">
      实线 = 字段注释写明「关联 表.id」或 <code>ai_table_relation</code> 登记过；
      虚线 = 只有命名一致、注释没写（按命名推测，用前核对数据）；
      红虚线 = 文本范围字段（只能按名字对，不是 JOIN）；
      红框 = 该表/字段有已知问题。○ 是「1」端，⊰ 是「多」端。
    </p>
  </section>
  {''.join(body)}
  <section id="rel">
    <h2>关系依据清单（每条连线的出处）</h2>
    <p class="desc">标「字段注释」= 建表时就写明了关联；标「按命名推测」= 我没找到依据，你用时必须先验证。</p>
    <table><thead><tr><th>关系</th><th>含义</th><th>依据</th><th>画法</th></tr></thead>
    <tbody>{rows}</tbody></table>
  </section>
  <section id="pit">
    <h2>6 个已知坑（都有实测证据）</h2>
    <table><thead><tr><th>坑</th><th>说明</th><th>证据</th></tr></thead>
    <tbody>{pit}</tbody></table>
    <h2 style="margin-top:34px">行数核对（图上数字的抽样）</h2>
    <table><thead><tr><th>表</th><th>COUNT(*)</th></tr></thead><tbody>{cnt}</tbody></table>
  </section>
</main>
<footer>生成脚本：<code>docs/06-口径与数据/ER/build_er.py</code> · 本页无外部依赖，断网可看</footer>
</body></html>"""


if __name__ == "__main__":
    io.open(os.path.join(HERE, "index.html"), "w", encoding="utf-8").write(html())
    io.open(os.path.join(HERE, "..", "ER-说明.md"), "w", encoding="utf-8").write(md())
    print("index.html + ER-说明.md written")
