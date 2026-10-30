# -*- coding: utf-8 -*-
"""给每个字段加中文含义列：BOX 加宽，行内画「字段名 + 中文名 + 类型」。"""
import io

p = "build_er.py"
s = io.open(p, encoding="utf-8").read()

# 1) 尺寸：440 宽、列距 610（间距仍 170）
s = s.replace("COL_W = 490", "COL_W = 610").replace("BOX_W = 330", "BOX_W = 440")

# 2) 字段中文名字典（取自 information_schema.columns.column_comment，过长的手工压短）
cn = '''
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


'''
s = s.replace("LEGEND = (", cn + "LEGEND = (", 1)

# 3) Box：行内多画一列中文名
old_fit_start = s.index("    def _fit(self, name, typ, mark):")
old_fit_end = s.index("    def rect(self):")
new_fit = '''    def _fit(self, name, typ, mark):
        cn = CN.get((self.en, name), "")
        cnw = self._est(cn, 10.5) + (10 if cn else 0)
        typw = self._est(typ, 9.5) + 8
        avail = BOX_W - 36 - 14 - cnw - typw
        size = 12.0
        while size > 8.5 and self._est(name, size) > avail:
            size -= 0.5
        if self._est(name, size) > avail:
            while len(name) > 4 and self._est(name + "…", size) > avail:
                name = name[:-1]
            name = name + "…"
        return (name, typ, mark, size, cn, self._est(name, size))

'''
s = s[:old_fit_start] + new_fit + s[old_fit_end:]

# 4) SVG 行渲染
s = s.replace(
    '''        for name, typ, mark, size in self.rows:''',
    '''        for name, typ, mark, size, cn, namew in self.rows:''')
s = s.replace(
    '''            out.append(f'<text x="{x + 36}" y="{ty}" font-family="{MONO}" font-size="{size:g}" '
                       f'fill="{"#c53030" if mark == "!" else "#1a202c"}">{esc(name)}</text>')''',
    '''            out.append(f'<text x="{x + 36}" y="{ty}" font-family="{MONO}" font-size="{size:g}" '
                       f'fill="{"#c53030" if mark == "!" else "#1a202c"}">{esc(name)}</text>')
            if cn:
                out.append(f'<text x="{x + 36 + namew + 10:.1f}" y="{ty}" font-family="{FONT}" '
                           f'font-size="10.5" fill="#4b5563">{esc(cn)}</text>')''')

# 5) PNG 行渲染
s = s.replace(
    '''            for name, typ, mark, size in b.rows:''',
    '''            for name, typ, mark, size, cn, namew in b.rows:''')
s = s.replace(
    '''                d.text((x + 36 * ss, ty - 1 * ss), name, font=f(msyh, size),
                       fill="#c53030" if mark == "!" else "#1a202c")''',
    '''                d.text((x + 36 * ss, ty - 1 * ss), name, font=f(msyh, size),
                       fill="#c53030" if mark == "!" else "#1a202c")
                if cn:
                    d.text(((36 + namew + 10) * ss + x, (ty - 1 * ss)), cn,
                           font=f(msyh, 10.5), fill="#4b5563")''')

io.open(p, "w", encoding="utf-8").write(s)
print("cn column added")
