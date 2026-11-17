# -*- coding: utf-8 -*-
# ⚠️ 本文件由 tools/gen_site_defaults.py 生成，内容来自原站点包（2026-10-30 拆解）。
"""产品提示词正文（原站点包 prompts/ 与 _platform/，2026-10-30 内联进代码）。"""
from __future__ import annotations


# ★ 2026-11-03 定稿（Fred）：**模式做法里的业务做法全部清空**，只留"基础流程"这一段。
#   链路上不再按词表判模式（`modes.judge` 已从问答链路移除），所以这里只放
#   与业务无关的通用流程说明：怎么答、要不要查、缺信息就问。页面可改（key = `modes`）。
#   data / doc / office 三段原文备份：
#   docs/08-历史归档/2026-11-03-删除的提示词规则/prompts_data-原文.py


IDENTITY = ('你是{SITE_FULL}（{SITE_NAME}）的办公智能助手，服务本公司及所属供电所的管理人员。\n'
            '用户问业务数据时，按系统里配的查数口径查库，再按作答规则把结果讲清楚。\n')



DOC_DEFAULTS = {'docx': {'body_font_cn': '仿宋_GB2312',
          'body_font_fallback': '仿宋',
          'title_font_cn': '方正小标宋简体',
          'body_pt': 16,
          'title_pt': 22,
          'line_spacing_pt': 28,
          'first_line_indent_chars': 2,
          'ai_note': '本文件由系统辅助生成，数据与结论请人工复核后使用。'},
 'xlsx': {'header_fill': 'DDEBF7',
          'border_color': 'BFBFBF',
          'freeze_header': True,
          'max_col_width': 60,
          'min_col_width': 10},
 'pptx': {'slide_width_in': 13.333, 'slide_height_in': 7.5, 'font_cn': '微软雅黑'}}

MODE_RULES = {'produce': ['做', '写', '生成', '出一份', '出个', '整理成', '转成', '导出', '起草', '拟一份', '汇总成'],
 'objects_file': ['ppt', '幻灯片', '演示', '汇报材料', '文档', '公文', '简报', '请示', '表格', '台账', '清单', '模板', '方案'],
 'objects_text': ['总结', '作文', '文案', '讲稿', '邮件', '通知', '说明', '心得体会', '材料', '报告', '纪事', '稿子'],
 # ★ 2026-11-03（Fred）：**业务相关的问题词表已删除**（原来这里有一份 business_topics）。
 #   业务问题改成「没命中直通词就是业务」，不再靠词表去认。原文备份：
 #   docs/08-历史归档/2026-11-03-删除的提示词规则/业务相关问题词表.md
 'data': ['多少',
          '多大',
          '几个',
          '几笔',
          '几条',
          '占比',
          '比例',
          '排名',
          '前几',
          '同比',
          '环比',
          '哪几个',
          '哪些',
          '统计',
          '合计',
          '总额',
          '总数',
          '平均',
          '执行率',
          '完成率',
          '差多少'],
 'kb': ['规定', '流程', '制度', '办法', '标准', '怎么规定', '谁审批', '依据'],
 'chat': ['你好', '您好', 'hi', 'hello', '在吗', '谢谢', '你是谁', '你能干什么']}

PRESET_GROUPS = ['缺陷工单', '项目预算', '线损指标']

PRESET_ITEMS = [{'group': '缺陷工单', 'id': 'b_wo_open', 'text': '现在还有多少条缺陷工单没闭环？'},
 {'group': '缺陷工单', 'id': 'b_wo_new', 'text': '上个月新增了多少条缺陷工单？'},
 {'group': '缺陷工单', 'id': 'b_wo_top', 'text': '哪个供电所的缺陷工单最多？'},
 {'group': '缺陷工单', 'id': 'b_wo_node', 'text': '有多少条工单卡在审核环节？'},
 {'group': '缺陷工单', 'id': 'b_wo_flow', 'text': '缺陷工单审批要经过几个节点？'},
 {'group': '项目预算', 'id': 'b_bd_amt', 'text': '今年项目执行金额是多少？'},
 {'group': '项目预算', 'id': 'b_bd_rate', 'text': '预算执行率最低的是哪个单位？'},
 {'group': '项目预算', 'id': 'b_bd_cnt', 'text': '今年立项了多少个项目？'},
 {'group': '项目预算', 'id': 'b_bd_over', 'text': '有没有超预算的项目？'},
 {'group': '线损指标', 'id': 'b_ls_rate', 'text': '上个月全市线损率是多少？'},
 {'group': '线损指标', 'id': 'b_ls_worst', 'text': '哪个供电所的线损率最高？'},
 {'group': '线损指标', 'id': 'b_ls_sale', 'text': '今年售电量是多少？'}]
