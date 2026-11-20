# -*- coding: utf-8 -*-
"""把 SRS-01 附录 B.1 那份现场内容灌进**当前库**（幂等）。

一个现场 = 一个 PG 库（SRS-01 §1.2）。这份脚本是「随州」那个现场的**迁移来源**：
它把原先散在代码常量里的业务内容一次性写进库，之后代码与库解耦 ——
代码里不再有这些内容（NFR-004），新现场也不需要它们（FR-MIGR-001）。

写入的表（全部来自 DESIGN 第 6 节）：

| 表 | 内容 |
|---|---|
| `cfg.site_info` | 现场名 / 全称 / 地市标签 / 县公司清单 / 默认所 |
| `cfg.prompt_text` (key=identity) | 身份正文（原两份重复副本合并成一份） |
| `cfg.preset_question` | 示例问题（原 `prompts_data.PRESET_ITEMS` 与 `presets.PRESETS` 两份重复副本合并成一份） |
| `cfg.t_nc_word` | 纠错词表：单位/台区类型词、单位前后缀、停用词、单字尾词 |
| `kb.catalog` | 业务分类树（只在表为空时建） |
| `kb.eval_question` | 评测题与文档名 |

    python db/seed_b1.py            # 灌库（幂等）
    python db/seed_b1.py --dry      # 只打印计划
    python db/seed_b1.py --check    # 只断言（灌过没有、灌对没有）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

# ── 现场信息（cfg.site_info）──────────────────────────────────
SITE_KEYS = {
 "site_name": "随州",
 "site_full": "国网随州供电公司",
 "site_code": "suizhou",
 "default_station": "环潭供电所",
 "city_dept_id": "1",
 "city_label": "随州市供电所",
 "county_roles": "[\"cto\", \"yxb\", \"xgscwb\"]",
 "city_names": "[\"随州市\", \"全市\"]",
 "county_depts": "[{\"key\": \"曾都\", \"dept_id\": 10, \"role\": \"xgscwb\", \"label\": \"曾都区供电公司\"}, {\"key\": \"随县\", \"dept_id\": 11, \"role\": \"xgscwb\", \"label\": \"随县供电公司\"}, {\"key\": \"高新\", \"dept_id\": 12, \"role\": \"xgscwb\", \"label\": \"高新供电公司\"}, {\"key\": \"广水\", \"dept_id\": 13, \"role\": \"xgscwb\", \"label\": \"广水供电公司\"}]"
}

# ── 身份（cfg.prompt_text，key=identity；两份重复副本已合并）──────
IDENTITY = "你是{SITE_FULL}（{SITE_NAME}）的办公智能助手，服务本公司及所属供电所的管理人员。\n用户问业务数据时，按系统里配的查数口径查库，再按作答规则把结果讲清楚。\n用户一句话交代，你自己判断这活怎么干。\n（本段可直接在「提示词 → 身份」里改；{SITE_FULL} / {SITE_NAME} 会替换成当前现场的值。）\n"

# ── 示例问题（cfg.preset_question；两份重复副本已合并成这一份）────
PRESET_ITEMS = [
 {
  "group": "缺陷工单",
  "id": "b_wo_open",
  "text": "现在还有多少条缺陷工单没闭环？"
 },
 {
  "group": "缺陷工单",
  "id": "b_wo_new",
  "text": "上个月新增了多少条缺陷工单？"
 },
 {
  "group": "缺陷工单",
  "id": "b_wo_top",
  "text": "哪个供电所的缺陷工单最多？"
 },
 {
  "group": "缺陷工单",
  "id": "b_wo_node",
  "text": "有多少条工单卡在审核环节？"
 },
 {
  "group": "缺陷工单",
  "id": "b_wo_flow",
  "text": "缺陷工单审批要经过几个节点？"
 },
 {
  "group": "项目预算",
  "id": "b_bd_amt",
  "text": "今年项目执行金额是多少？"
 },
 {
  "group": "项目预算",
  "id": "b_bd_rate",
  "text": "预算执行率最低的是哪个单位？"
 },
 {
  "group": "项目预算",
  "id": "b_bd_cnt",
  "text": "今年立项了多少个项目？"
 },
 {
  "group": "项目预算",
  "id": "b_bd_over",
  "text": "有没有超预算的项目？"
 },
 {
  "group": "线损指标",
  "id": "b_ls_rate",
  "text": "上个月全市线损率是多少？"
 },
 {
  "group": "线损指标",
  "id": "b_ls_worst",
  "text": "哪个供电所的线损率最高？"
 },
 {
  "group": "线损指标",
  "id": "b_ls_sale",
  "text": "今年售电量是多少？"
 }
]

# ── 纠错词表（cfg.t_nc_word）：词面在库，算法在 name_corrector ────
WORDS = [
 {
  "kind": "unit_kind",
  "word": "供电公司",
  "extra": {},
  "sort_no": 10
 },
 {
  "kind": "unit_kind",
  "word": "供电所",
  "extra": {},
  "sort_no": 20
 },
 {
  "kind": "unit_kind",
  "word": "供电服务站",
  "extra": {},
  "sort_no": 30
 },
 {
  "kind": "unit_level",
  "word": "供电公司",
  "extra": {
   "level": 0
  },
  "sort_no": 0
 },
 {
  "kind": "unit_level",
  "word": "供电所",
  "extra": {
   "level": 1
  },
  "sort_no": 1
 },
 {
  "kind": "unit_level",
  "word": "供电服务站",
  "extra": {
   "level": 2
  },
  "sort_no": 2
 },
 {
  "kind": "unit_prefix",
  "word": "国网湖北省电力有限公司",
  "extra": {},
  "sort_no": 0
 },
 {
  "kind": "unit_prefix",
  "word": "湖北省电力有限公司",
  "extra": {},
  "sort_no": 1
 },
 {
  "kind": "unit_prefix",
  "word": "国网",
  "extra": {},
  "sort_no": 2
 },
 {
  "kind": "unit_prefix",
  "word": "国家电网",
  "extra": {},
  "sort_no": 3
 },
 {
  "kind": "unit_suffix",
  "word": "供电服务有限公司",
  "extra": {},
  "sort_no": 0
 },
 {
  "kind": "unit_suffix",
  "word": "供电有限公司",
  "extra": {},
  "sort_no": 1
 },
 {
  "kind": "unit_suffix",
  "word": "供电公司",
  "extra": {},
  "sort_no": 2
 },
 {
  "kind": "unit_suffix",
  "word": "供电服务中心",
  "extra": {},
  "sort_no": 3
 },
 {
  "kind": "unit_suffix",
  "word": "供电服务站",
  "extra": {},
  "sort_no": 4
 },
 {
  "kind": "unit_suffix",
  "word": "供电营业站",
  "extra": {},
  "sort_no": 5
 },
 {
  "kind": "unit_suffix",
  "word": "供电中心",
  "extra": {},
  "sort_no": 6
 },
 {
  "kind": "unit_suffix",
  "word": "供电所",
  "extra": {},
  "sort_no": 7
 },
 {
  "kind": "unit_suffix",
  "word": "供电部",
  "extra": {},
  "sort_no": 8
 },
 {
  "kind": "unit_suffix",
  "word": "服务站",
  "extra": {},
  "sort_no": 9
 },
 {
  "kind": "unit_suffix",
  "word": "营业站",
  "extra": {},
  "sort_no": 10
 },
 {
  "kind": "unit_suffix",
  "word": "服务站",
  "extra": {},
  "sort_no": 11
 },
 {
  "kind": "unit_suffix",
  "word": "分公司",
  "extra": {},
  "sort_no": 12
 },
 {
  "kind": "area_suffix",
  "word": "配电变压器",
  "extra": {},
  "sort_no": 0
 },
 {
  "kind": "area_suffix",
  "word": "柱上变压器",
  "extra": {},
  "sort_no": 1
 },
 {
  "kind": "area_suffix",
  "word": "箱式变压器",
  "extra": {},
  "sort_no": 2
 },
 {
  "kind": "area_suffix",
  "word": "变压器",
  "extra": {},
  "sort_no": 3
 },
 {
  "kind": "area_suffix",
  "word": "柱上变",
  "extra": {},
  "sort_no": 4
 },
 {
  "kind": "area_suffix",
  "word": "箱变",
  "extra": {},
  "sort_no": 5
 },
 {
  "kind": "area_suffix",
  "word": "公变",
  "extra": {},
  "sort_no": 6
 },
 {
  "kind": "area_suffix",
  "word": "专变",
  "extra": {},
  "sort_no": 7
 },
 {
  "kind": "area_suffix",
  "word": "室变",
  "extra": {},
  "sort_no": 8
 },
 {
  "kind": "area_suffix",
  "word": "变台",
  "extra": {},
  "sort_no": 9
 },
 {
  "kind": "area_suffix",
  "word": "台区",
  "extra": {},
  "sort_no": 10
 },
 {
  "kind": "area_suffix",
  "word": "配变",
  "extra": {},
  "sort_no": 11
 },
 {
  "kind": "area_suffix",
  "word": "变",
  "extra": {},
  "sort_no": 12
 },
 {
  "kind": "suffix_hint",
  "word": "供电",
  "extra": {},
  "sort_no": 0
 },
 {
  "kind": "suffix_hint",
  "word": "变电站",
  "extra": {},
  "sort_no": 1
 },
 {
  "kind": "suffix_hint",
  "word": "电站",
  "extra": {},
  "sort_no": 2
 },
 {
  "kind": "suffix_hint",
  "word": "台区",
  "extra": {},
  "sort_no": 3
 },
 {
  "kind": "suffix_hint",
  "word": "变压器",
  "extra": {},
  "sort_no": 4
 },
 {
  "kind": "suffix_hint",
  "word": "公变",
  "extra": {},
  "sort_no": 5
 },
 {
  "kind": "suffix_hint",
  "word": "专变",
  "extra": {},
  "sort_no": 6
 },
 {
  "kind": "suffix_hint",
  "word": "室变",
  "extra": {},
  "sort_no": 7
 },
 {
  "kind": "suffix_hint",
  "word": "箱变",
  "extra": {},
  "sort_no": 8
 },
 {
  "kind": "generic_word",
  "word": "国网",
  "extra": {},
  "sort_no": 0
 },
 {
  "kind": "generic_word",
  "word": "供电",
  "extra": {},
  "sort_no": 1
 },
 {
  "kind": "generic_word",
  "word": "公司",
  "extra": {},
  "sort_no": 2
 },
 {
  "kind": "generic_word",
  "word": "供电所",
  "extra": {},
  "sort_no": 3
 },
 {
  "kind": "generic_word",
  "word": "服务站",
  "extra": {},
  "sort_no": 4
 },
 {
  "kind": "generic_word",
  "word": "变电站",
  "extra": {},
  "sort_no": 5
 },
 {
  "kind": "generic_word",
  "word": "中心",
  "extra": {},
  "sort_no": 6
 },
 {
  "kind": "generic_word",
  "word": "台区",
  "extra": {},
  "sort_no": 7
 },
 {
  "kind": "generic_word",
  "word": "线路",
  "extra": {},
  "sort_no": 8
 },
 {
  "kind": "generic_word",
  "word": "用电",
  "extra": {},
  "sort_no": 9
 },
 {
  "kind": "generic_word",
  "word": "电费",
  "extra": {},
  "sort_no": 10
 },
 {
  "kind": "common_word",
  "word": "情况",
  "extra": {},
  "sort_no": 0
 },
 {
  "kind": "common_word",
  "word": "工作",
  "extra": {},
  "sort_no": 1
 },
 {
  "kind": "common_word",
  "word": "数据",
  "extra": {},
  "sort_no": 2
 },
 {
  "kind": "common_word",
  "word": "时候",
  "extra": {},
  "sort_no": 3
 },
 {
  "kind": "common_word",
  "word": "地方",
  "extra": {},
  "sort_no": 4
 },
 {
  "kind": "common_word",
  "word": "结果",
  "extra": {},
  "sort_no": 5
 },
 {
  "kind": "common_word",
  "word": "分析",
  "extra": {},
  "sort_no": 6
 },
 {
  "kind": "common_word",
  "word": "问题",
  "extra": {},
  "sort_no": 7
 },
 {
  "kind": "common_word",
  "word": "办法",
  "extra": {},
  "sort_no": 8
 },
 {
  "kind": "common_word",
  "word": "原因",
  "extra": {},
  "sort_no": 9
 },
 {
  "kind": "common_word",
  "word": "今天",
  "extra": {},
  "sort_no": 10
 },
 {
  "kind": "common_word",
  "word": "昨天",
  "extra": {},
  "sort_no": 11
 },
 {
  "kind": "common_word",
  "word": "明天",
  "extra": {},
  "sort_no": 12
 },
 {
  "kind": "common_word",
  "word": "现在",
  "extra": {},
  "sort_no": 13
 },
 {
  "kind": "common_word",
  "word": "本月",
  "extra": {},
  "sort_no": 14
 },
 {
  "kind": "common_word",
  "word": "上月",
  "extra": {},
  "sort_no": 15
 },
 {
  "kind": "common_word",
  "word": "本年",
  "extra": {},
  "sort_no": 16
 },
 {
  "kind": "common_word",
  "word": "去年",
  "extra": {},
  "sort_no": 17
 },
 {
  "kind": "common_word",
  "word": "今年",
  "extra": {},
  "sort_no": 18
 },
 {
  "kind": "common_word",
  "word": "这个月",
  "extra": {},
  "sort_no": 19
 },
 {
  "kind": "common_word",
  "word": "上个月",
  "extra": {},
  "sort_no": 20
 },
 {
  "kind": "common_word",
  "word": "下个月",
  "extra": {},
  "sort_no": 21
 },
 {
  "kind": "common_word",
  "word": "本周",
  "extra": {},
  "sort_no": 22
 },
 {
  "kind": "common_word",
  "word": "上周",
  "extra": {},
  "sort_no": 23
 },
 {
  "kind": "common_word",
  "word": "本季度",
  "extra": {},
  "sort_no": 24
 },
 {
  "kind": "common_word",
  "word": "上季度",
  "extra": {},
  "sort_no": 25
 },
 {
  "kind": "common_word",
  "word": "年初",
  "extra": {},
  "sort_no": 26
 },
 {
  "kind": "common_word",
  "word": "年底",
  "extra": {},
  "sort_no": 27
 },
 {
  "kind": "common_word",
  "word": "多少",
  "extra": {},
  "sort_no": 28
 },
 {
  "kind": "common_word",
  "word": "哪里",
  "extra": {},
  "sort_no": 29
 },
 {
  "kind": "common_word",
  "word": "什么",
  "extra": {},
  "sort_no": 30
 },
 {
  "kind": "common_word",
  "word": "怎么",
  "extra": {},
  "sort_no": 31
 },
 {
  "kind": "common_word",
  "word": "可以",
  "extra": {},
  "sort_no": 32
 },
 {
  "kind": "common_word",
  "word": "帮助",
  "extra": {},
  "sort_no": 33
 },
 {
  "kind": "common_word",
  "word": "查询",
  "extra": {},
  "sort_no": 34
 },
 {
  "kind": "common_word",
  "word": "统计",
  "extra": {},
  "sort_no": 35
 },
 {
  "kind": "common_word",
  "word": "平均",
  "extra": {},
  "sort_no": 36
 },
 {
  "kind": "common_word",
  "word": "最大",
  "extra": {},
  "sort_no": 37
 },
 {
  "kind": "common_word",
  "word": "最小",
  "extra": {},
  "sort_no": 38
 },
 {
  "kind": "common_word",
  "word": "排名",
  "extra": {},
  "sort_no": 39
 },
 {
  "kind": "common_word",
  "word": "占比",
  "extra": {},
  "sort_no": 40
 },
 {
  "kind": "common_word",
  "word": "增长",
  "extra": {},
  "sort_no": 41
 },
 {
  "kind": "common_word",
  "word": "下降",
  "extra": {},
  "sort_no": 42
 },
 {
  "kind": "common_word",
  "word": "报表",
  "extra": {},
  "sort_no": 43
 },
 {
  "kind": "common_word",
  "word": "异常",
  "extra": {},
  "sort_no": 44
 },
 {
  "kind": "common_word",
  "word": "汇总",
  "extra": {},
  "sort_no": 45
 },
 {
  "kind": "common_word",
  "word": "明细",
  "extra": {},
  "sort_no": 46
 },
 {
  "kind": "common_word",
  "word": "审批",
  "extra": {},
  "sort_no": 47
 },
 {
  "kind": "common_word",
  "word": "大概",
  "extra": {},
  "sort_no": 48
 },
 {
  "kind": "common_word",
  "word": "全部",
  "extra": {},
  "sort_no": 49
 },
 {
  "kind": "common_word",
  "word": "负荷",
  "extra": {},
  "sort_no": 50
 },
 {
  "kind": "common_word",
  "word": "电价",
  "extra": {},
  "sort_no": 51
 },
 {
  "kind": "common_word",
  "word": "用电",
  "extra": {},
  "sort_no": 52
 },
 {
  "kind": "common_word",
  "word": "售电",
  "extra": {},
  "sort_no": 53
 },
 {
  "kind": "common_word",
  "word": "客户",
  "extra": {},
  "sort_no": 54
 },
 {
  "kind": "common_word",
  "word": "用户",
  "extra": {},
  "sort_no": 55
 },
 {
  "kind": "common_word",
  "word": "数量",
  "extra": {},
  "sort_no": 56
 },
 {
  "kind": "common_word",
  "word": "金额",
  "extra": {},
  "sort_no": 57
 },
 {
  "kind": "common_word",
  "word": "总额",
  "extra": {},
  "sort_no": 58
 },
 {
  "kind": "common_word",
  "word": "费用",
  "extra": {},
  "sort_no": 59
 },
 {
  "kind": "common_word",
  "word": "成本",
  "extra": {},
  "sort_no": 60
 },
 {
  "kind": "common_word",
  "word": "效益",
  "extra": {},
  "sort_no": 61
 },
 {
  "kind": "common_word",
  "word": "收入",
  "extra": {},
  "sort_no": 62
 },
 {
  "kind": "common_word",
  "word": "支出",
  "extra": {},
  "sort_no": 63
 },
 {
  "kind": "common_word",
  "word": "预算",
  "extra": {},
  "sort_no": 64
 },
 {
  "kind": "common_word",
  "word": "执行",
  "extra": {},
  "sort_no": 65
 },
 {
  "kind": "common_word",
  "word": "完成",
  "extra": {},
  "sort_no": 66
 },
 {
  "kind": "common_word",
  "word": "进度",
  "extra": {},
  "sort_no": 67
 },
 {
  "kind": "common_word",
  "word": "计划",
  "extra": {},
  "sort_no": 68
 },
 {
  "kind": "common_word",
  "word": "目标",
  "extra": {},
  "sort_no": 69
 },
 {
  "kind": "common_word",
  "word": "指标",
  "extra": {},
  "sort_no": 70
 },
 {
  "kind": "common_word",
  "word": "考核",
  "extra": {},
  "sort_no": 71
 },
 {
  "kind": "common_word",
  "word": "评价",
  "extra": {},
  "sort_no": 72
 },
 {
  "kind": "common_word",
  "word": "里面",
  "extra": {},
  "sort_no": 73
 },
 {
  "kind": "common_word",
  "word": "上面",
  "extra": {},
  "sort_no": 74
 },
 {
  "kind": "common_word",
  "word": "下面",
  "extra": {},
  "sort_no": 75
 },
 {
  "kind": "common_word",
  "word": "这些",
  "extra": {},
  "sort_no": 76
 },
 {
  "kind": "common_word",
  "word": "那些",
  "extra": {},
  "sort_no": 77
 },
 {
  "kind": "common_word",
  "word": "我们",
  "extra": {},
  "sort_no": 78
 },
 {
  "kind": "common_word",
  "word": "你们",
  "extra": {},
  "sort_no": 79
 },
 {
  "kind": "common_word",
  "word": "请问",
  "extra": {},
  "sort_no": 80
 },
 {
  "kind": "common_word",
  "word": "麻烦",
  "extra": {},
  "sort_no": 81
 },
 {
  "kind": "common_word",
  "word": "帮忙",
  "extra": {},
  "sort_no": 82
 },
 {
  "kind": "common_word",
  "word": "一下",
  "extra": {},
  "sort_no": 83
 },
 {
  "kind": "common_word",
  "word": "一个",
  "extra": {},
  "sort_no": 84
 },
 {
  "kind": "common_word",
  "word": "这个",
  "extra": {},
  "sort_no": 85
 },
 {
  "kind": "common_word",
  "word": "那个",
  "extra": {},
  "sort_no": 86
 },
 {
  "kind": "common_word",
  "word": "是否",
  "extra": {},
  "sort_no": 87
 },
 {
  "kind": "common_word",
  "word": "怎么",
  "extra": {},
  "sort_no": 88
 },
 {
  "kind": "common_word",
  "word": "一下",
  "extra": {},
  "sort_no": 89
 },
 {
  "kind": "common_word",
  "word": "整体",
  "extra": {},
  "sort_no": 90
 },
 {
  "kind": "common_word",
  "word": "总体",
  "extra": {},
  "sort_no": 91
 },
 {
  "kind": "common_word",
  "word": "全部",
  "extra": {},
  "sort_no": 92
 },
 {
  "kind": "common_word",
  "word": "所有",
  "extra": {},
  "sort_no": 93
 },
 {
  "kind": "common_word",
  "word": "各个",
  "extra": {},
  "sort_no": 94
 },
 {
  "kind": "common_word",
  "word": "分别",
  "extra": {},
  "sort_no": 95
 },
 {
  "kind": "common_word",
  "word": "主要",
  "extra": {},
  "sort_no": 96
 },
 {
  "kind": "common_word",
  "word": "关键",
  "extra": {},
  "sort_no": 97
 },
 {
  "kind": "common_word",
  "word": "重点",
  "extra": {},
  "sort_no": 98
 },
 {
  "kind": "common_word",
  "word": "基本",
  "extra": {},
  "sort_no": 99
 },
 {
  "kind": "common_word",
  "word": "线损",
  "extra": {},
  "sort_no": 100
 },
 {
  "kind": "common_word",
  "word": "售电量",
  "extra": {},
  "sort_no": 101
 },
 {
  "kind": "common_word",
  "word": "供电量",
  "extra": {},
  "sort_no": 102
 },
 {
  "kind": "common_word",
  "word": "停电",
  "extra": {},
  "sort_no": 103
 },
 {
  "kind": "common_word",
  "word": "跳闸",
  "extra": {},
  "sort_no": 104
 },
 {
  "kind": "common_word",
  "word": "工单",
  "extra": {},
  "sort_no": 105
 },
 {
  "kind": "common_word",
  "word": "容量",
  "extra": {},
  "sort_no": 106
 },
 {
  "kind": "common_word",
  "word": "电压",
  "extra": {},
  "sort_no": 107
 },
 {
  "kind": "common_word",
  "word": "电流",
  "extra": {},
  "sort_no": 108
 },
 {
  "kind": "common_word",
  "word": "功率",
  "extra": {},
  "sort_no": 109
 },
 {
  "kind": "common_word",
  "word": "最近",
  "extra": {},
  "sort_no": 110
 },
 {
  "kind": "common_word",
  "word": "近期",
  "extra": {},
  "sort_no": 111
 },
 {
  "kind": "common_word",
  "word": "目前",
  "extra": {},
  "sort_no": 112
 },
 {
  "kind": "common_word",
  "word": "当前",
  "extra": {},
  "sort_no": 113
 },
 {
  "kind": "common_word",
  "word": "成效",
  "extra": {},
  "sort_no": 114
 },
 {
  "kind": "common_word",
  "word": "绩效",
  "extra": {},
  "sort_no": 115
 },
 {
  "kind": "common_word",
  "word": "安排",
  "extra": {},
  "sort_no": 116
 },
 {
  "kind": "common_word",
  "word": "分配",
  "extra": {},
  "sort_no": 117
 },
 {
  "kind": "common_word",
  "word": "安排",
  "extra": {},
  "sort_no": 118
 },
 {
  "kind": "common_word",
  "word": "使用",
  "extra": {},
  "sort_no": 119
 },
 {
  "kind": "common_word",
  "word": "全县",
  "extra": {},
  "sort_no": 120
 },
 {
  "kind": "common_word",
  "word": "全市",
  "extra": {},
  "sort_no": 121
 },
 {
  "kind": "common_word",
  "word": "全省",
  "extra": {},
  "sort_no": 122
 },
 {
  "kind": "common_word",
  "word": "全区",
  "extra": {},
  "sort_no": 123
 },
 {
  "kind": "common_word",
  "word": "全镇",
  "extra": {},
  "sort_no": 124
 },
 {
  "kind": "common_word",
  "word": "各村",
  "extra": {},
  "sort_no": 125
 },
 {
  "kind": "common_word",
  "word": "各地",
  "extra": {},
  "sort_no": 126
 },
 {
  "kind": "common_word",
  "word": "各所",
  "extra": {},
  "sort_no": 127
 },
 {
  "kind": "common_word",
  "word": "各公司",
  "extra": {},
  "sort_no": 128
 },
 {
  "kind": "common_word",
  "word": "各供电所",
  "extra": {},
  "sort_no": 129
 },
 {
  "kind": "common_word",
  "word": "电力",
  "extra": {},
  "sort_no": 130
 },
 {
  "kind": "common_word",
  "word": "电网",
  "extra": {},
  "sort_no": 131
 },
 {
  "kind": "common_word",
  "word": "变电",
  "extra": {},
  "sort_no": 132
 },
 {
  "kind": "common_word",
  "word": "配电",
  "extra": {},
  "sort_no": 133
 },
 {
  "kind": "common_word",
  "word": "发电",
  "extra": {},
  "sort_no": 134
 },
 {
  "kind": "common_word",
  "word": "送电",
  "extra": {},
  "sort_no": 135
 },
 {
  "kind": "common_word",
  "word": "电业",
  "extra": {},
  "sort_no": 136
 },
 {
  "kind": "common_word",
  "word": "能源",
  "extra": {},
  "sort_no": 137
 },
 {
  "kind": "common_word",
  "word": "变压",
  "extra": {},
  "sort_no": 138
 },
 {
  "kind": "common_word",
  "word": "开关",
  "extra": {},
  "sort_no": 139
 },
 {
  "kind": "common_word",
  "word": "资金",
  "extra": {
   "note": "项目级补充：同音会误改成地名"
  },
  "sort_no": 900
 },
 {
  "kind": "asked_unit_suffix",
  "word": "供电服务站",
  "extra": {
   "kinds": [
    "供电服务站"
   ]
  },
  "sort_no": 0
 },
 {
  "kind": "asked_unit_suffix",
  "word": "供电营业站",
  "extra": {
   "kinds": [
    "供电服务站"
   ]
  },
  "sort_no": 1
 },
 {
  "kind": "asked_unit_suffix",
  "word": "供电服务中心",
  "extra": {
   "kinds": [
    "供电服务站"
   ]
  },
  "sort_no": 2
 },
 {
  "kind": "asked_unit_suffix",
  "word": "供电中心",
  "extra": {
   "kinds": [
    "供电所"
   ]
  },
  "sort_no": 3
 },
 {
  "kind": "asked_unit_suffix",
  "word": "供电公司",
  "extra": {
   "kinds": [
    "供电公司"
   ]
  },
  "sort_no": 4
 },
 {
  "kind": "asked_unit_suffix",
  "word": "分公司",
  "extra": {
   "kinds": [
    "供电公司"
   ]
  },
  "sort_no": 5
 },
 {
  "kind": "asked_unit_suffix",
  "word": "服务站",
  "extra": {
   "kinds": [
    "供电服务站"
   ]
  },
  "sort_no": 6
 },
 {
  "kind": "asked_unit_suffix",
  "word": "营业站",
  "extra": {
   "kinds": [
    "供电服务站"
   ]
  },
  "sort_no": 7
 },
 {
  "kind": "asked_unit_suffix",
  "word": "公司",
  "extra": {
   "kinds": [
    "供电公司"
   ]
  },
  "sort_no": 8
 },
 {
  "kind": "asked_unit_suffix",
  "word": "供电所",
  "extra": {
   "kinds": [
    "供电所"
   ]
  },
  "sort_no": 9
 },
 {
  "kind": "ask_tail",
  "word": "所",
  "extra": {
   "single": "所",
   "kinds": [
    "供电所",
    "供电服务站"
   ],
   "block_next": "上下以内及在外对属有级谓述长需"
  },
  "sort_no": 0
 },
 {
  "kind": "strip_head",
  "word": "国网",
  "extra": {},
  "sort_no": 0
 },
 {
  "kind": "strip_head",
  "word": "湖北省",
  "extra": {},
  "sort_no": 1
 },
 {
  "kind": "strip_head",
  "word": "湖北省电力",
  "extra": {},
  "sort_no": 2
 },
 {
  "kind": "strip_head",
  "word": "随州市",
  "extra": {},
  "sort_no": 3
 },
 {
  "kind": "strip_head",
  "word": "随州",
  "extra": {},
  "sort_no": 4
 },
 {
  "kind": "strip_head",
  "word": "中国",
  "extra": {},
  "sort_no": 5
 },
 {
  "kind": "org_prefix",
  "word": "国网",
  "extra": {},
  "sort_no": 0
 },
 {
  "kind": "org_prefix",
  "word": "国网湖北",
  "extra": {},
  "sort_no": 1
 },
 {
  "kind": "org_prefix",
  "word": "湖北省",
  "extra": {},
  "sort_no": 2
 },
 {
  "kind": "biz_word",
  "word": "供电所|供电公司|台区|工单|预算|审批|线损|停电|缺陷|售电|指标|项目",
  "extra": {
   "note": "业务通用词（正则串，原样保留）"
  },
  "sort_no": 0
 },
 {
  "kind": "station_hint",
  "word": "供电所|供电服务站|台区|公变|专变|变压器|变电站|线路|台变",
  "extra": {
   "note": "地点提示词（正则串，原样保留）"
  },
  "sort_no": 0
 }
]

# ── 业务分类树（kb.catalog，只在空表时灌）──────────────────────
CATALOG_TREE = {
 "综合": [
  "公文处理",
  "会议管理",
  "值班应急",
  "保密档案",
  "综合文秘"
 ],
 "安监": [
  "安全责任",
  "隐患排查",
  "事故调查"
 ],
 "运检": [
  "检修计划",
  "缺陷管理"
 ],
 "营销": [
  "服务规范",
  "电费回收"
 ],
 "财务": [
  "预算管理",
  "报销差旅"
 ]
}

# ── 评测题（kb.eval_question，只在空表时灌）────────────────────
EVAL_SEED = [
 {
  "question": "公文格式有什么要求",
  "expect_doc": "公文格式规范.md"
 },
 {
  "question": "请示报告的写法要求",
  "expect_doc": "请示报告写作要求.md"
 },
 {
  "question": "会议纪要要写哪些内容",
  "expect_doc": "会议管理实施细则.md"
 },
 {
  "question": "突发事件多长时间内报告",
  "expect_doc": "突发事件信息报送流程.md"
 },
 {
  "question": "涉密文件能不能复制",
  "expect_doc": "涉密文件管理办法.md"
 },
 {
  "question": "隐患整改谁挂牌督办",
  "expect_doc": "隐患排查治理办法.md"
 },
 {
  "question": "年度检修计划怎么编",
  "expect_doc": "年度检修计划编制要求.md"
 },
 {
  "question": "设备缺陷多久消缺",
  "expect_doc": "设备缺陷管理流程.md"
 },
 {
  "question": "抢修多长时间到场",
  "expect_doc": "供电服务规范(2025版).md"
 },
 {
  "question": "电费回收考核指标",
  "expect_doc": "电费回收考核办法.md"
 },
 {
  "question": "预算调整要走什么程序",
  "expect_doc": "预算管理办法.md"
 },
 {
  "question": "差旅住宿标准多少钱",
  "expect_doc": "差旅费报销标准.md"
 },
 {
  "question": "值班交接要注意什么",
  "expect_doc": "值班管理规定.md"
 },
 {
  "question": "事故调查报告包含什么",
  "expect_doc": "事故调查报告模板.md"
 }
]


def _rows():
    """按库上的唯一键 (kind, word) 去重：原常量里「服务站」「怎么」这类词本就重复。"""
    seen, out = set(), []
    for r in WORDS:
        key = (r["kind"], r["word"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"kind": r["kind"], "word": r["word"], "extra": r["extra"],
                    "sort_no": r["sort_no"]})
    return out


def plan() -> dict:
    return {
        "cfg.site_info": len(SITE_KEYS),
        "cfg.prompt_text(identity)": 1,
        "cfg.preset_question": len(PRESET_ITEMS),
        "cfg.t_nc_word": len(_rows()),
        "kb.catalog": sum(1 + len(v) for v in CATALOG_TREE.values()) if CATALOG_TREE else 0,
        "kb.eval_question": len(EVAL_SEED),
    }


def seed() -> dict:
    from app.secretary import kb_eval, lex_words, presets, site_info

    site_info.ensure()
    res = site_info.save(SITE_KEYS)
    presets.replace(PRESET_ITEMS)
    lex_words.ensure()
    for r in _rows():
        lex_words.save(r["kind"], r["word"], r["extra"], r["sort_no"])

    # 身份：库里有就不再覆盖（页面上可能已经按现场改过）
    from app.secretary import prompt_text

    if (prompt_text.get("identity", "") or "").strip():
        ident = {"skipped": True}
    else:
        ident = prompt_text.save("identity", IDENTITY, user="seed_b1")

    # 目录树 / 评测题：只在空表时灌（已有内容说明现场已经自己建过）
    from app.secretary import catalog

    catalog.ensure()
    catalog_res = {"skipped": True}
    if not catalog.tree() and CATALOG_TREE:
        catalog_res = catalog.seed(CATALOG_TREE)
    kb_eval.ensure()
    eval_res = {"skipped": True}
    if not kb_eval.rows():
        eval_res = kb_eval.seed()
    return {"site_info": res, "identity": ident, "presets": len(PRESET_ITEMS),
            "words": len(_rows()), "catalog": catalog_res, "eval": eval_res}


def check() -> int:
    """逐项断言：库里有没有、对不对。全 PASS 才返回 0。"""
    from app.secretary import catalog, kb_eval, lex_words, presets, prompt_text, site_info

    fails = []

    def ok(name, cond, detail=""):
        print(("PASS " if cond else "FAIL ") + name + ((" | " + str(detail)) if detail else ""))
        if not cond:
            fails.append(name)

    vals = site_info.all_values()
    ok("cfg.site_info 现场名", vals.get("site_name") == SITE_KEYS["site_name"],
       vals.get("site_name"))
    ok("cfg.site_info 全称", vals.get("site_full") == SITE_KEYS["site_full"],
       vals.get("site_full"))
    ok("cfg.site_info 默认所", vals.get("default_station") == SITE_KEYS["default_station"],
       vals.get("default_station"))
    ok("cfg.site_info 县公司清单", len(site_info.get_list("county_depts")) ==
       len(json.loads(SITE_KEYS["county_depts"])), len(site_info.get_list("county_depts")))
    ok("cfg.site_info 县公司角色", site_info.get_list("county_roles") ==
       json.loads(SITE_KEYS["county_roles"]), site_info.get_list("county_roles"))
    ok("cfg.site_info 地市部门 id", str(site_info.get_int("city_dept_id")) ==
       SITE_KEYS["city_dept_id"], site_info.get_int("city_dept_id"))
    ok("cfg.site_info 地市标签", site_info.get("city_label") == SITE_KEYS["city_label"],
       site_info.get("city_label"))
    ok("kb.catalog 目录树已入库", len(catalog.tree()) == sum(
        1 + len(v) for v in CATALOG_TREE.values()), len(catalog.tree()))

    ident = prompt_text.get("identity", "")
    ok("cfg.prompt_text 身份已入库", bool(ident.strip()), len(ident))
    ok("身份保留现场占位符（不是写死单位名）", "{SITE_FULL}" in ident)

    items = presets.rows()
    ok("cfg.preset_question 条数", len(items) == len(PRESET_ITEMS), len(items))
    ok("示例问题无重复副本（qid 唯一）", len({i["id"] for i in items}) == len(items))

    got, want = {}, {}
    for r in lex_words.rows():
        got[r["kind"]] = got.get(r["kind"], 0) + 1
    for r in _rows():
        want[r["kind"]] = want.get(r["kind"], 0) + 1
    for kind, n in sorted(want.items()):
        ok("cfg.t_nc_word[%s] 条数" % kind, got.get(kind) == n, got.get(kind))

    ok("kb.eval_question 条数", len(kb_eval.rows()) >= len(EVAL_SEED),
       len(kb_eval.rows()))

    print("\n结论：%s" % ("PASS" if not fails else "FAIL " + "; ".join(fails)))
    return 0 if not fails else 1


def main() -> int:
    if "--check" in sys.argv:
        return check()
    if "--dry" in sys.argv:
        print("将写入：")
        for k, v in plan().items():
            print("  %-28s %s" % (k, v))
        return 0
    res = seed()
    print("已灌入：%s" % json.dumps(res, ensure_ascii=False))
    return check()


if __name__ == "__main__":
    sys.exit(main())
