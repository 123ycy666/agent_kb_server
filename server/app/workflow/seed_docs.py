"""把业务判据整理成标准规则文档，幂等写进库（只写文档，不跑）。

结构（2026-11-06 定稿）：取数文档五节、发送文档五节。一节只放一类内容，跨节不重复。
- 取数：一 管什么 / 二 看哪些数据 / 三 怎么算 / 四 多少算异常 / 五 存什么数据
- 发送：一 管什么 / 二 读什么 / 三 发给谁、说什么 / 四 发几次、什么不发 / 五 存什么数据
口径来源：`server/app/secretary/skills/判据规则.md` 与被删的旧判据代码；只使用业务字典白名单里的表。
用法：`py -X utf8 -m app.workflow.seed_docs`
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.database import SessionLocal
from app.workflow import docs as docsdb
from app.workflow.models import RuleDoc, Schedule

DETECT_DOCS: list[tuple[str, str, str, dict]] = [
    (
        '预算花不完', '预算', 'station',
        {
            'what': '哪个所今年的预算到年底花不完。\n算到供电所，一个所一条。',
            'data': '一、t_power_ai_company_budget_result（所级年度分配预算）\n'
                    '  power_station_id：所 id（关联键）\n  power_station_name：所名\n'
                    '  county_company_name：所属县\n  budget_amount：分配额，单位元\n'
                    '  year：年份\n  deleted_flag：删除标记\n'
                    '二、t_power_ai_work_order（工单）\n  power_company_id：所 id（关联键）\n'
                    '  is_approved：是否审批通过，1=通过\n  total_budget：工单预算，单位元\n'
                    '  year：年份\n  deleted_flag：删除标记\n'
                    '只取：year=今年 且 deleted_flag=0 的行。\n'
                    '坑一：两表按 id 关联（power_station_id = power_company_id），按单位名关联会报 1267。\n'
                    '坑二：total_budget 注释写万元，实测是元，不要换算。',
            'calc': '分配额 = 该所今年 budget_amount 合计（一个所多行，SUM）。\n'
                    '已花 = 该所今年 is_approved=1 工单的 total_budget 合计。\n'
                    '执行率 = 已花 / 分配额。\n'
                    '时间进度 = 已过天数 / 365。\n'
                    '预计年末执行率 = 执行率 / 时间进度。\n'
                    '预计结余 = 分配额 ×（1 - 预计年末执行率）。\n'
                    '分配额为 0 或没有分配记录的所不参与计算。',
            'judge': '预计年末执行率 < 60% 报；< 30% 为高档，30%~60% 为中档；60% 及以上不报。',
            'store': '对象：所名；键：所 id。\n'
                     '当前值：预计年末执行率（小数，如 0.00）。\n'
                     '阈值 0.6，比较方式 小于。\n'
                     '明细六项：分配额、已花、执行率、时间进度、预计结余、所属县。\n'
                     '依据三项：分配额取自所级年度分配预算；已花取自当年审批通过工单的预算合计；数据截止日。\n'
                     '可靠性：两张表都有该所数据才可靠；工单表没有该所记录时按已花 0 处理并在明细里注明。',
        },
    ),
    (
        '预算未分配', '预算', 'station',
        {
            'what': '哪个所今年完全没有预算分配。\n算到供电所，一个所一条。',
            'data': '一、t_power_company：id 所 id、name 所名、level 层级（按业务字典里的说明判断所级）。\n'
                    '二、t_power_ai_company_budget_result：power_station_id、budget_amount（元）、year、deleted_flag。\n'
                    '只取：deleted_flag=0；预算只取 year=今年。',
            'calc': '以 t_power_company 里的所级单位为全集；\n'
                    '该所今年在预算表里没有记录，或 budget_amount 合计为 0 → 记为未分配。',
            'judge': '无记录或合计为 0 即报，全部为高档。',
            'store': '对象：所名；键：所 id。\n'
                     '当前值：分配额合计（无记录按 0）。\n'
                     '阈值 0，比较方式 等于。\n'
                     '明细三项：是否有预算记录、年度、所属县。\n'
                     '依据两项：所清单取自单位表；分配额取自所级年度分配预算。\n'
                     '可靠性：预算表整年无数据时标不可靠（可能是数据没导入，不是没分配）。',
        },
    ),
    (
        '单笔工单预算异常', '项目', 'station',
        {
            'what': '哪张工单的预算金额明显偏大，需要复核。\n算到工单，一张一条。',
            'data': 't_power_ai_work_order：id 工单 id、work_code 工单号、work_name 工单名、'
                    'power_company_name 所名、county_company_name 县名、total_budget 预算金额（元）、'
                    'work_status 状态、year、deleted_flag。\n'
                    '只取：year=今年 且 deleted_flag=0。',
            'calc': '取今年每张工单的 total_budget。',
            'judge': 'total_budget ≥ 1000000 元（100 万）报，全部为高档。',
            'store': '对象：工单名（带所名）；键：工单 id。\n'
                     '当前值：total_budget（元）。\n'
                     '阈值 1000000，比较方式 大于等于。\n'
                     '明细四项：工单号、所名、县名、状态。\n'
                     '依据两项：预算金额取自工单表 total_budget；年度。',
        },
    ),
    (
        '工单金额缺失', '项目', 'station',
        {
            'what': '哪张已审批通过的工单没填预算金额。\n算到工单，一张一条。',
            'data': 't_power_ai_work_order：id、work_code、work_name、power_company_name、'
                    'total_budget（元）、is_approved（1=通过）、year、deleted_flag。\n'
                    '只取：year=今年、deleted_flag=0、is_approved=1。',
            'calc': '取今年审批通过工单的 total_budget。',
            'judge': 'total_budget 为 0 或空 → 报，中档。',
            'store': '对象：工单名（带所名）；键：工单 id。\n'
                     '当前值：total_budget（空按 0）。\n'
                     '阈值 0，比较方式 等于。\n'
                     '明细三项：工单号、所名、审批状态。\n'
                     '依据两项：预算金额取自工单表；审批状态取自 is_approved。',
        },
    ),
    (
        '已批未开工', '项目', 'station',
        {
            'what': '哪张工单批下来了却迟迟没开工。\n算到工单，一张一条。',
            'data': 't_power_ai_work_order：id、work_code、work_name、power_company_name、'
                    'is_approved（1=通过）、actual_start_time 实际开工时间、create_time 创建时间、'
                    'total_budget（元）、year、deleted_flag。\n'
                    '只取：year=今年、deleted_flag=0、is_approved=1、actual_start_time 为空。',
            'calc': '天数 = create_time 到今天的天数。',
            'judge': '天数 > 90 报；> 180 为高档，其余中档。',
            'store': '对象：工单名（带所名）；键：工单 id。\n'
                     '当前值：已过天数。\n'
                     '阈值 90，比较方式 大于。\n'
                     '明细四项：工单号、预算金额、创建时间、天数按创建时间推算的说明。\n'
                     '依据三项：审批状态、实际开工时间、创建时间。\n'
                     '可靠性：actual_start_time 整表为空时标"按创建时间推算，天数偏大"。',
        },
    ),
    (
        '工单长期未结束', '项目', 'station',
        {
            'what': '哪张工单拖了很久还没结束。\n算到工单，一张一条。',
            'data': 't_power_ai_work_order：id、work_code、work_name、power_company_name、'
                    'actual_end_time 实际结束时间、create_time、work_status、total_budget（元）、'
                    'year、deleted_flag。\n'
                    '只取：year=今年、deleted_flag=0、actual_end_time 为空。',
            'calc': '天数 = create_time 到今天的天数。',
            'judge': '天数 > 180 报；> 270 为高档，其余中档。',
            'store': '对象：工单名（带所名）；键：工单 id。\n'
                     '当前值：已过天数。\n'
                     '阈值 180，比较方式 大于。\n'
                     '明细三项：工单号、工作状态、预算金额。\n'
                     '依据两项：实际结束时间、创建时间。',
        },
    ),
    (
        '审批长期未通过', '项目', 'station',
        {
            'what': '哪张工单卡在审批里很久没通过。\n算到工单，一张一条。',
            'data': 't_power_ai_work_order：id、work_code、work_name、power_company_name、'
                    'is_approved（0=未通过）、current_node_name 当前审批节点、'
                    'update_time 最后更新时间、create_time、total_budget（元）、year、deleted_flag。\n'
                    '只取：year=今年、deleted_flag=0、is_approved=0。',
            'calc': '天数 = update_time 到今天的天数（没有审批任务表，只能用最后更新时间近似，是下限）。',
            'judge': '天数 > 30 报；> 90 为高档，其余中档。',
            'store': '对象：工单名（带所名）；键：工单 id。\n'
                     '当前值：停留天数。\n'
                     '阈值 30，比较方式 大于。\n'
                     '明细四项：当前节点、工单号、预算金额、天数按最后更新时间估算的说明。\n'
                     '依据三项：审批状态、当前节点、最后更新时间。\n'
                     '可靠性：update_time 为空的跳过，并在运行记录里注明。',
        },
    ),
    (
        '台区线损超线', '指标', 'station',
        {
            'what': '哪个台区的线损率超过考核线。\n算到台区，一个台区一条。',
            'data': 't_power_ai_area_effect：power_area_name 台区名、area_code 台区编码、'
                    'line_loss_rate 当月线损率（%）、sales_volume 当月售电量（kWh）、'
                    'stat_month 统计月份（YYYYMM）、deleted_flag。\n'
                    '只取：deleted_flag=0，且 stat_month 为全表最新月份。',
            'calc': '取最新月份的台区线损率，每个台区一条。',
            'judge': 'line_loss_rate > 3 报；> 5 为高档，3~5 为中档。',
            'store': '对象：台区名；键：台区名（无 id 时）。\n'
                     '当前值：线损率（%）。\n'
                     '阈值 3，比较方式 大于。\n'
                     '明细三项：统计月份、售电量、台区编码。\n'
                     '依据两项：线损率取自当月台区成效表；统计月份。\n'
                     '可靠性：最新月份只有极少量台区时标不可靠（数据未补齐）。',
        },
    ),
    (
        '台区售电量骤降', '指标', 'station',
        {
            'what': '哪个台区这个月售电量比上个月掉得厉害。\n算到台区，一个台区一条。',
            'data': 't_power_ai_area_effect：power_area_name、sales_volume（kWh）、stat_month、deleted_flag。\n'
                    '只取：deleted_flag=0。',
            'calc': '同一台区取最新两个 stat_month 的 sales_volume；\n'
                    '环比 =（本月 - 上月）/ 上月。',
            'judge': '环比 < -30% 报；< -50% 为高档，其余中档。',
            'store': '对象：台区名；键：台区名。\n'
                     '当前值：环比变化率（%，负数表示下降）。\n'
                     '阈值 -30，比较方式 小于。\n'
                     '明细四项：本月售电量、上月售电量、本月月份、上月月份。\n'
                     '依据两项：售电量取自台区成效表两个月份。\n'
                     '可靠性：上月售电量为 0 或缺失的台区不参与计算。',
        },
    ),
    (
        '指标同比恶化', '指标', 'station',
        {
            'what': '哪个所的核心指标比去年同期变差了。\n算到供电所，一个所一条。',
            'data': 't_power_ai_station_summary：station_name 所名、county_company_name 所属县、'
                    'stat_month 累计截止月（YYYYMM）、loss_rate 累计线损率（%）、'
                    'loss_rate_yoy_rate 线损率同比变化率（%）、trip_count 跳闸次数、'
                    'trip_count_yoy_rate 跳闸同比变化率（%）、'
                    'average_outage_duration_yoy_rate 平均停电时长同比变化率（%）、deleted_flag。\n'
                    '只取：deleted_flag=0，且 stat_month 为全表最新月份。',
            'calc': '取最新累计月份每个所的三项同比变化率（正数=同比变差）。',
            'judge': '任一项超过门槛即报：线损率同比 > 5%、跳闸同比 > 20%、停电时长同比 > 20%；\n'
                     '超出门槛一倍为高档，其余中档。',
            'store': '对象：所名；键：所 id（无 id 时用所名）。\n'
                     '当前值：最差那一项的同比变化率（%）。\n'
                     '阈值：对应项的门槛（5 或 20）。\n'
                     '明细四项：线损率同比、跳闸同比、停电时长同比、所属县。\n'
                     '依据两项：三项同比取自所级汇总表对应列；统计月份。\n'
                     '可靠性：三项同比全空的所不参与计算。',
        },
    ),
]

SEND_DOCS: list[tuple[str, str, str, dict]] = [
    (
        '事项发送（按单位找负责人）', '发送', 'station',
        {
            'what': '每天 08:30 读未关闭事项，整理成一句话发给对应的人。',
            'source': '读 status=open 的事项，按最近命中时间倒序，取前 200 条。\n'
                      '已关闭的不读。',
            'who': '所级事项发给该所负责人；县公司层发给县公司对应岗位；全市层发给市公司对应岗位。\n'
                   '做法：用 run_sql 按单位 + 岗位查账号表找人；\n'
                   '一句话 = 对象 + 事实 + 关键数 + 档位，例如「厉山供电所 预计年底结余 68.9 万，高档」。\n'
                   '查不到人就不发，并写清理由。\n'
                   '注意：业务字典白名单里目前没有账号表，查不到人时按此规则不发。',
            'cadence': '同一事项同一人 1 天内只发一次；档位升档时补发一条；事项关闭后不再发；'
                       '静默期内不重复发。',
            'store': '发送记录存五项：收件人（登录名 + 姓名）、通道、发出去的那句话、依据、发送时间。\n'
                     '失败的要存失败原因。\n'
                     '同时把事项的已发送时间更新为本次时间。',
        },
    ),
]


def upsert(db, name: str, work_type: str, alert_type: str, scope: str, body: dict) -> str:
    existed = db.execute(select(RuleDoc).where(RuleDoc.name == name)).scalars().first()
    if existed is not None:
        existed.alert_type = alert_type
        existed.scope_level = scope
        existed.doc = body
        existed.enabled = 1
        existed.version = int(existed.version or 1) + 1
        db.commit()
        return 'updated'
    docsdb.create_doc(db, name=name, work_type=work_type, alert_type=alert_type,
                      scope_level=scope, doc=body, updated_by='seed')
    return 'created'


def main() -> None:
    db = SessionLocal()
    try:
        from app.workflow.store import ensure_tables
        ensure_tables()
        created = []
        for name, typ, scope, body in DETECT_DOCS:
            created.append((name, upsert(db, name, 'detect', typ, scope, body)))
        for name, typ, scope, body in SEND_DOCS:
            created.append((name, upsert(db, name, 'send', typ, scope, body)))

        def ensure_schedule(name: str, cron: str, note: str) -> Schedule:
            s = db.execute(select(Schedule).where(Schedule.name == name)).scalars().first()
            if s is None:
                s = Schedule(name=name, cron=cron, enabled=0, note=note)
                db.add(s)
                db.commit()
            return s

        s1 = ensure_schedule('每天 08:00 取数', '0 8 * * *', '取数工作流：到点按文档跑一轮')
        s2 = ensure_schedule('每天 08:30 发送', '30 8 * * *', '发送工作流：读事项整理成一句话发出去')
        for d in db.execute(select(RuleDoc).where(RuleDoc.work_type == 'detect',
                                                  RuleDoc.deleted_flag == 0)).scalars().all():
            d.schedule_id = s1.id
        for d in db.execute(select(RuleDoc).where(RuleDoc.work_type == 'send',
                                                  RuleDoc.deleted_flag == 0)).scalars().all():
            d.schedule_id = s2.id
        db.commit()
        print('文档:', created)
        print('方案:', (s1.name, s1.cron, s1.enabled), (s2.name, s2.cron, s2.enabled))
    finally:
        db.close()


if __name__ == '__main__':
    main()
