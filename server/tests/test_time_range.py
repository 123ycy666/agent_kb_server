# -*- coding: utf-8 -*-
"""统计时间解析回归（2026-10-27）。

    python tests/test_time_range.py

钉死三类行为：

  A. **区间要整体识别**。「1-9月」不是「9月」：
     库里只有「1 月累计到该月」（yearToDate）的口径，区间必须落到**结束月**。
     旧实现没有区间概念，会把「26年1-9月」切成「9月」，把「2026年1-9月」切成
     「2026年」再展开成「1-11月」—— 用户看到的「硬给我补 1-11 月」就是这么来的。
  B. **两位年份要认**（26年）。正则写死 20\\d{2} 时年份被丢掉，只剩月。
  C. **不允许拿未来月当口径**。快照表里被上游提前写入了未来月
     （今天 10-27，库里已有 202611），取 MAX(period_key) 会把「今年」算成 1-11 月。

另外守住改写：原问题被改成「2026年1-11月1-9月」这种废句会直接进模型上下文。
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.secretary import time_scope   # noqa: E402

YM = time_scope._latest_ym()          # 业务已结算月：C 段用它跟系统当月比
_TODAY = datetime.date.today()
CY, CM = _TODAY.year, _TODAY.month    # ★ 口径基准 = 系统日历当月（不再跟业务月走）
NOW = '%04d%02d' % (CY, CM)

# (输入, 期望 period_type, 期望 period_key, 说明)
CASES = [
    ('26年1-9月全市情况', 'yearToDate', '%d09' % CY, '两位年 + 区间'),
    ('2026年1-9月全市情况', 'yearToDate', '%d09' % CY, '四位年 + 区间'),
    ('2026年1月到9月线损', 'yearToDate', '%d09' % CY, '「到」连接的区间'),
    ('今年1-9月全市情况', 'yearToDate', '%d09' % CY, '「今年」+ 区间'),
    ('1-9月线损', 'yearToDate', '%d09' % CY, '裸区间默认今年'),
    ('2026年1~9月线损', 'yearToDate', '%d09' % CY, '波浪号区间'),
    # 单点行为保持不变
    ('2026年8月的线损', 'month', '%d08' % CY, '单月仍是 month 口径'),
]

REWRITE_FORBIDDEN = ('1-2026年', '2026年1-%d月1-' % CM, '2026年1-%d月2' % CM)


def main() -> int:
    ok = fail = 0
    print('=' * 78)
    print('A/B. 区间识别（latest_ym = %s）' % YM)
    print('=' * 78)
    for q, want_t, want_k, why in CASES:
        r = time_scope.resolve(q)
        good = (r['period_type'] == want_t and r['period_key'] == want_k)
        if good:
            ok += 1
            print('  [ OK ] %-22s -> %s/%s' % (q, r['period_type'], r['period_key']))
        else:
            fail += 1
            print('  [FAIL] %-22s -> %s/%s' % (q, r['period_type'], r['period_key']))
            print('         期望 %-10s %s（%s）' % (want_t, want_k, why))

    print('\n' + '=' * 78)
    print('C. 不允许用未来月 / 改写不许造废句')
    print('=' * 78)
    today = datetime.date.today()
    cap = '%04d%02d' % (today.year, today.month)
    if YM <= cap:
        ok += 1
        print('  [ OK ] latest_ym=%s 不晚于系统当月 %s' % (YM, cap))
    else:
        fail += 1
        print('  [FAIL] latest_ym=%s 晚于系统当月 %s（未来月被当口径）' % (YM, cap))

    for q in ('26年1-9月全市情况', '2026年1-9月全市情况', '2026年1月到9月线损'):
        rw = time_scope.resolve(q)['rewritten']
        bad = [p for p in REWRITE_FORBIDDEN if p in rw] or ('月1-' in rw and '年1-' not in rw)
        if bad:
            fail += 1
            print('  [FAIL] 改写产生废句：%s -> %s' % (q, rw))
        else:
            ok += 1
            print('  [ OK ] 改写正常：%s -> %s' % (q, rw))

    print('\n' + '=' * 78)
    print('D. 月份说法必须精准识别（基准 = 系统当月 %s）' % NOW)
    print('=' * 78)
    month_cases = [
        ('两水供电所5月信息', 'month', '%d05' % CY, '裸月份'),
        ('两水供电所5月份信息', 'month', '%d05' % CY, '带「份」：旧实现 (?!份) 整句漏识别'),
        ('两水供电所五月的信息', 'month', '%d05' % CY, '中文数字'),
        ('两水供电所今年5月', 'month', '%d05' % CY, '相对年+月：旧实现被「今年」抢走'),
        ('两水供电所去年5月', 'month', '%d05' % (CY - 1), '去年 + 月'),
        ('两水供电所前年12月', 'month', '%d12' % (CY - 2), '前年 + 月'),
        ('两水供电所%02d年5月' % (CY % 100), 'month', '%d05' % CY, '两位年 + 月'),
        ('两水供电所%d年5月' % CY, 'month', '%d05' % CY, '四位年 + 月'),
        ('两水供电所5月份的售电量', 'month', '%d05' % CY, '「的」紧跟在月份后'),
        ('两水供电所 5 月线损', 'month', '%d05' % CY, '月前后有空格'),
        ('截至5月售电量', 'yearToDate', '%d05' % CY, '截至 X 月'),
        ('1-5月售电量', 'yearToDate', '%d05' % CY, '区间'),
    ]
    for q, want_t, want_k, why in month_cases:
        r = time_scope.resolve(q, today=_TODAY)
        if r['period_type'] == want_t and r['period_key'] == want_k:
            ok += 1
            print('  [ OK ] %-24s -> %s/%s' % (q, r['period_type'], r['period_key']))
        else:
            fail += 1
            print('  [FAIL] %-24s -> %s/%s（期望 %s/%s，%s）'
                  % (q, r['period_type'], r['period_key'], want_t, want_k, why))

    print('\n' + '=' * 78)
    print('E. 没说时间 → 默认**系统当月**（不是「今年至今」）')
    print('=' * 78)
    for q in ('两水供电所信息', '两水供电所情况怎么样', '售电量多少'):
        r = time_scope.resolve(q, today=_TODAY)
        if r['period_type'] == 'month' and r['period_key'] == NOW and r['is_default']:
            ok += 1
            print('  [ OK ] %-24s -> month/%s（默认值）' % (q, NOW))
        else:
            fail += 1
            print('  [FAIL] %-24s -> %s/%s（期望 month/%s 默认值）'
                  % (q, r['period_type'], r['period_key'], NOW))

    print('\n' + '=' * 78)
    print('F. 说了时间但没对上口径 → 不许静默降级成默认值')
    print('=' * 78)
    r = time_scope.resolve('两水供电所13月的信息', today=_TODAY)
    if r.get('unmatched') and not r['is_default']:
        ok += 1
        print('  [ OK ] 13月 -> unmatched 标记，交回用户确认')
    else:
        fail += 1
        print('  [FAIL] 13月 -> 被当成默认值（%s/%s）' % (r['period_type'], r['period_key']))

    print('\n' + '=' * 78)
    print('G. 多轮沿用：本轮没说时间、上一轮有 → 跟上一轮走（优先于默认值）')
    print('=' * 78)
    prev = {'period_type': 'month', 'period_key': '%d05' % CY,
            'time_text': '%d年5月' % CY, 'place': '两水供电所'}
    r = time_scope.resolve('那售电量呢', today=_TODAY, prev=prev)
    if r['period_key'] == '%d05' % CY and r['period_type'] == 'month':
        ok += 1
        print('  [ OK ] 那售电量呢 -> 沿用 %s' % r['period_key'])
    else:
        fail += 1
        print('  [FAIL] 那售电量呢 -> %s/%s（期望沿用 %d05）'
              % (r['period_type'], r['period_key'], CY))

    print('\n' + '=' * 78)
    print('结果：%d 通过 / %d 失败' % (ok, fail))
    print('=' * 78)
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
