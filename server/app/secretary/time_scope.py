# -*- coding: utf-8 -*-
"""时间与统计范围补全：把「上个月」「今年」这类相对说法，换成库里认的具体期间。

**为什么用代码而不是让模型自己换算**
    「上个月」是哪一个月，取决于**今天是哪天**。模型不会去翻日历，
    稍不留神就把 8 月算成 9 月 —— 而且它算错了也看不出来。
    这种「确定性换算」交给代码：零成本、每次都一样、还能顺手把原词改掉。

**口径与库对齐**（`t_power_ai_metric_snapshot.period_type`）
    month       当月       period_key = YYYYMM
    year        全年       period_key = YYYY
    yearToDate  本年累计   period_key = YYYYMM（1 月累计到该月）

**「今年」为什么不给 year**
    今年还没过完。而且库里 station 层的当期数据只在 yearToDate 里
    （year 只到 2025，没有 2026）—— 给 `year=2026` 会一条都查不到。
    所以「今年」= yearToDate + 数据里最新的那个月。
"""
import datetime
import re

from app.secretary import tools_db


def _as_date(v):
    """`today` 允许 date / datetime / 'YYYY-MM-DD' 字符串（上游接口传的就是字符串）。

    2026-10-28 补：口径基准改成「系统日历当月」时把 `today.year` 提到了函数开头，
    于是传字符串的调用方（`agent.ask(today='2026-10-26')`、老测试）当场 AttributeError。
    边界统一在这里转，别让每个调用点各写一份。
    """
    if v is None:
        return datetime.date.today()
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    s = str(v).strip().replace('/', '-')
    try:
        return datetime.date.fromisoformat(s[:10])
    except ValueError:
        return datetime.date.today()


# ---------------------------------------------------------------- 中文数字
_CN_NUM = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7,
           '八': 8, '九': 9, '十': 10, '十一': 11, '十二': 12, '元': 1, '正': 1}
_CN_ALT = '|'.join(sorted(_CN_NUM, key=len, reverse=True))

# 相对月（长的排前面，「上个月」要先于「上月」命中）
_MONTH_REL = [('上个月', -1), ('上月', -1), ('前一个月', -1), ('前月', -1),
              ('本月份', 0), ('本月', 0), ('这个月', 0), ('当月', 0),
              ('下个月', 1), ('下月', 1)]

# 相对年
_YEAR_REL = [('今年以来', 0), ('年初至今', 0), ('本年度', 0),
             ('今年', 0), ('本年', 0),
             ('上一年', -1), ('去年', -1), ('上年', -1),
             ('前年', -2)]
# 相对年 + 月（「今年5月」）用这张表换算年份
_YEAR_OFF = {'今年': 0, '本年': 0, '去年': -1, '上年': -1, '前年': -2}

# 数据最新月份缓存（进程级）—— 这个值一个进程里不会变，没必要反复查库
_LATEST = {'value': None}
# 「该月的当月值有没有」的缓存，按 YYYYMM 存（一个进程里数据不会变）
_HAS = {}
# 「最近一个有值的月份」缓存
_NEAR = {}
# 问题里出现这些词，就认为问的是「供电所/台区」这一层
_STATION_HINT = re.compile(r'供电所|供电服务站|台区|公变|专变|变压器|变电站|线路|台变')

# 出现这些词，说明问题里**确实提到过时间**，只是没对上库里的口径。
# 用途：把「真没说时间」和「说了但没认出来」分开 —— 这两者原来都掉进兜底，
# 全都按「今年至今」算，用户问 5 月被答成 9 月，而且看不出哪里错了。
_TIME_HINT = re.compile(
    r'(\d{1,2}|' + _CN_ALT + r')\s*月|季度|年度|年份|半年|旬|周|星期|礼拜|截至|截到')


def _latest_ym():
    """业务数据里**已结算**的最新月份（YYYYMM）。查不到就用系统当月兜底。

    ★ 不能直接取 MAX(period_key)（2026-10-27 修）。实测快照表里存在**未来月**：
      今天 10-27，库里已有 202610（30 行）、202611（288 行），是上游 ETL 在
      10-08 提前写进去的。无脑取 MAX 会把「今年」算成「1-11月」，
      用户问「26年1-9月」被答成 11 月的数据 —— 这是「硬给我补 1-11 月」的源头。
    两道闸：
      ① 不晚于系统当月（未来月一律不认）；
      ② 该月行数比上一个月少一半以上 → 视为「还没结算」，回退到上一个月。
         （实测 202610 只有 30 行、202609 有 312 行，一眼就是没跑完）
    """
    if _LATEST['value']:
        return _LATEST['value']
    now = datetime.date.today()
    cap = '%04d%02d' % (now.year, now.month)
    k = cap
    try:
        rows = tools_db._exec(
            "SELECT period_key AS k, COUNT(*) AS n FROM t_power_ai_metric_snapshot "
            "WHERE deleted_flag=0 AND period_type='yearToDate' AND period_key <= '%s' "
            "GROUP BY period_key ORDER BY period_key DESC LIMIT 3" % cap)['rows']
        if rows:
            pick = rows[0]
            prev_n = int(rows[1].get('n') or 0) if len(rows) > 1 else 0
            if prev_n and int(pick.get('n') or 0) < prev_n * 0.5:
                pick = rows[1]              # 最新月只跑了一半，不算已结算
            cand = str(pick.get('k') or '')
            if re.fullmatch(r'\d{6}', cand):
                k = cand
    except Exception:
        pass
    _LATEST['value'] = k
    return _LATEST['value']


def _month_has_data(ym):
    """该月份的「当月」口径有没有值（0 和 NULL 都算没值）。

    六项指标按月结算，当月往往要到月末才齐；历史上也有整月缺失的
    （全市层 2026 年 8 月连行都没有）。这两种情况都不能干巴巴回一句「查不到」。
    """
    if ym in _HAS:
        return _HAS[ym]
    ok = True
    try:
        rows = tools_db._exec(
            "SELECT COUNT(*) AS n FROM t_power_ai_metric_snapshot "
            "WHERE deleted_flag=0 AND period_type='month' AND period_key='%s' "
            "AND metric_value IS NOT NULL AND metric_value <> 0" % ym,
            limit_rows=1)['rows']
        ok = int((rows[0].get('n') if rows else 0) or 0) > 0
    except Exception:
        pass                            # 查不到就别下结论，按「有」处理
    _HAS[ym] = ok
    return ok


def _nearest_month(ym):
    """往回想，最近一个「当月值」有数据的月份（YYYYMM）。"""
    if ym in _NEAR:
        return _NEAR[ym]
    got = ''
    try:
        rows = tools_db._exec(
            "SELECT MAX(period_key) AS k FROM t_power_ai_metric_snapshot "
            "WHERE deleted_flag=0 AND period_type='month' AND period_key < '%s' "
            "AND metric_value IS NOT NULL AND metric_value <> 0" % ym,
            limit_rows=1)['rows']
        got = str((rows[0].get('k') if rows else '') or '')
    except Exception:
        pass
    _NEAR[ym] = got
    return got


def _ym_text(ym):
    """YYYYMM → 2026年8月（不合法就返回空串）。"""
    if re.fullmatch(r'\d{6}', str(ym or '')):
        return '%d年%d月' % (int(ym[:4]), int(ym[4:6]))
    return ''


def _prev_ym(ym):
    """上一个月的 YYYYMM（跨年也能算）。用于「累计值减累计值」求单月净增。"""
    if not re.fullmatch(r'\d{6}', str(ym or '')):
        return ''
    y, m = int(ym[:4]), int(ym[4:6]) - 1
    if m == 0:
        y, m = y - 1, 12
    return '%04d%02d' % (y, m)


def _is_station_level(question, fixed):
    """这次问的是不是「供电所/台区」这一层（快照表在这一层没有当月口径）。

    不能只看纠错结果：「环潭供电所」本来就是库里的标准名，纠错层不认为它需要改，
    names / areas 都是空的 —— 只看它就会漏判。所以在原问题上再按词面兜一道。
    """
    for n in ((fixed or {}).get('names') or []):
        if str(n.get('类别') or '') in ('供电所', '供电服务站', '台区'):
            return True
    if (fixed or {}).get('areas'):
        return True
    return bool(_STATION_HINT.search(str(question or '')))


def _to_int(s):
    s = (s or '').strip()
    if s.isdigit():
        return int(s)
    return _CN_NUM.get(s, 0)


# 替换点前后紧贴这些字符时，说明这个词是从区间里切出来的碎片（「26年1-9月」里的「9月」），
# 单独替换会拼出「26年1-2026年9月」「2026年1-11月1-9月」这种废句，直接进模型上下文。
_TIME_GLUE = '0123456789年月日-~～—到至'


def _rewrite(q, matched, text):
    """把问题里的时间词换成具体期间；**换不安全就不换**（保留原词，另附说明）。

    改写是有代价的：改错了模型看到的就是一句错话，比不改更糟。
    所以只在「整词替换且不与相邻时间字符粘连」时才动手。
    """
    if not q or not matched or matched == text:
        return q
    if q.count(matched) != 1:
        return q
    i = q.find(matched)
    head, tail = q[:i], q[i + len(matched):]
    # ⚠️ 必须先判空再判字符：Python 里 '' in '任意串' 恒为 True，
    #    写成 `head[-1:] in _TIME_GLUE` 会让「句首替换」永远被当成粘连而放弃改写。
    if (head[-1:] and head[-1] in _TIME_GLUE) or (tail[:1] and tail[0] in _TIME_GLUE):
        return q
    new = head + text + tail
    # 「今年分了多少钱」→「2026年1-9月了多少钱」读起来像「9月份了」，补一个顿开
    j = i + len(text)
    if j < len(new) and new[j] in ('分', '份'):
        new = new[:j] + '，' + new[j:]
    return new


def _mk(matched, text, ptype, pkey, is_default=False, note='', q=None):
    """组装结果；rewritten = 把问题里那个相对时间词换成具体时间的完整问题。"""
    return {'matched': matched, 'time_text': text, 'period_type': ptype,
            'period_key': pkey, 'is_default': is_default, 'note': note,
            'rewritten': _rewrite(q, matched, text)}


def resolve(question, today=None, prev=None):
    """解析问题里的时间说法。返回 dict（字段见 _mk）。

    优先级：区间 > 具体年月 > 两位年+月 > 相对年+月 > 具体年 > 光说月份
            > 截至X月 > 相对说法 > 季度 > **沿用上一轮** > 默认（系统当月）。

    prev = 上一轮的统计范围（同一场会话）。**只在问题完全没提时间时才生效** ——
    用户这轮说了时间就必须用他说的。

    ★ 基准月 = **系统日历当月**（2026-10-27 改）。
      原先把「业务已结算最新月」当基准，两个概念混在一个变量里，
      「没说时间」被补成「1-9 月累计」—— 用户问 5 月、答的却是 9 月。
      现在 cur_y/cur_m 只跟日历走；biz_ym 仅用于「这个月数据出了没有」的提示，
      **绝不参与口径计算**。
    """
    q = str(question or '')
    today = _as_date(today)
    cur_y, cur_m = int(today.year), int(today.month)
    now_key = '%04d%02d' % (cur_y, cur_m)
    biz_ym = _latest_ym()          # 业务已结算最新月：只写提示，不改口径

    # ⓪ 区间：2026年1-9月 / 26年1-9月 / 今年1-9月 / 1月到9月 / 1~9月
    #    **必须排在「具体年月」之前**。否则「2026年1月到9月」会先被切成「2026年1月」，
    #    「1-9月」会被切成「9月」，区间语义整个丢掉（2026-10-27 实测：
    #    用户问 1-9 月，系统按 9 月单月答、或按 1-11 月答）。
    #    库里没有「任意区间」口径，只能落到 yearToDate（1 月累计到该月）的结束月。
    m = re.search(
        r'(?:(?:((?:19|20)?\d{2})\s*年|(今年|本年|今年以来))\s*)?'
        r'(\d{1,2}|' + _CN_ALT + r')\s*月?\s*[-~～—到至]\s*'
        r'(\d{1,2}|' + _CN_ALT + r')\s*月', q)
    if m:
        y = int(m.group(1)) if m.group(1) else cur_y
        if y < 100:
            y += 2000                      # 「26年」= 2026 年
        sm, em = _to_int(m.group(3)), _to_int(m.group(4))
        if 1 <= sm <= 12 and 1 <= em <= 12 and sm <= em:
            notes = []
            if em > cur_m:
                notes.append('问题里的区间到 %d 月，但系统当期是 %d 月，按 %d 月截断，'
                             '回答时要说明实际取到哪个月' % (em, cur_m, cur_m))
                em = cur_m
            if '%04d%02d' % (y, em) > biz_ym:
                notes.append('库内已结算到 %s，%d年%d月 的数据可能还没出齐 —— '
                             '若数明显偏小请如实说明这是未结算口径，不要自行换成别的月份'
                             % (_ym_text(biz_ym), y, em))
            if sm > 1:
                notes.append('库里只有「1 月累计到该月」的口径，%d-%d 月的净增要用 '
                             '%04d%02d 的累计值减 %04d%02d 的累计值'
                             % (sm, em, y, em, y, sm - 1))
            return _mk(m.group(0), '%d年%d-%d月' % (y, sm, em), 'yearToDate',
                       '%04d%02d' % (y, em), note='；'.join(notes), q=q)

    # ① 具体年月：2026年8月 / 2026年08月 / 2026年八月 / 2026年8月份
    m = re.search(r'(20\d{2})\s*年\s*(\d{1,2}|' + _CN_ALT + r')\s*月', q)
    if m:
        y, mo = int(m.group(1)), _to_int(m.group(2))
        if 1 <= mo <= 12:
            return _mk(m.group(0), '%d年%d月' % (y, mo), 'month',
                       '%04d%02d' % (y, mo), q=q)

    # ①′ 两位年 + 月：26年5月 / 25年12月。
    #     ②″ 只认「整年」，遇到后面跟月份的会主动让开，这一段不补上
    #     就会掉到 ③ 按「5月」算成今年，年份整个丢掉。
    m = re.search(r'(?<!\d)(\d{2})\s*年\s*(\d{1,2}|' + _CN_ALT + r')\s*月', q)
    if m:
        y2, mo = 2000 + int(m.group(1)), _to_int(m.group(2))
        if 1 <= mo <= 12 and 0 <= y2 - cur_y <= 50:
            return _mk(m.group(0), '%d年%d月' % (y2, mo), 'month',
                       '%04d%02d' % (y2, mo), q=q)

    # ①″ 相对年 + 月：今年5月 / 去年12月 / 前年3月。
    #     ③ 的 lookbehind 把「5月」前面的「年」当成了要躲的字符（原本为了不切
    #     「2026年5月」），结果「今年5月」里的「5月」整段落空、被 ⑤「今年」抢走，
    #     答成「今年至今」。相对年就得在这里先吃掉，否则月份必然丢。
    m = re.search(r'(今年|本年|去年|上年|前年)\s*(\d{1,2}|' + _CN_ALT + r')\s*月', q)
    if m:
        y = cur_y + _YEAR_OFF[m.group(1)]
        mo = _to_int(m.group(2))
        if 1 <= mo <= 12:
            return _mk(m.group(0), '%d年%d月' % (y, mo), 'month',
                       '%04d%02d' % (y, mo), q=q)

    # ② 具体年：2026年（后面不再跟月份）
    m = re.search(r'(20\d{2})\s*年(?!\s*(?:\d{1,2}|' + _CN_ALT + r')\s*月)', q)
    if not m:
        # ②′ 裸年份：`2025` 不带「年」字（口语里很常见：「凉水2025售电量」）。
        #     放在这里、③（光说月份）之前；数字边界用 \d 卡死，免得切进长数字的中段。
        #     紧跟「月」的情况不进这一支（那是 ③ 的活），避免把「2025 8月」读成整年。
        m = re.search(r'(?<![\d.])(20\d{2})(?![\d.]|月)', q)
    if not m:
        # ②″ 两位年份「26年」。正则写死 20\d{2} 时这种说法整段落空，
        #     年份被丢掉、「1-9月」里的「9月」被当成单月（2026-10-27 实测）。
        m = re.search(r'(?<!\d)(\d{2})\s*年(?!\s*(?:\d{1,2}|' + _CN_ALT + r')\s*月)', q)
        if m and not (0 <= 2000 + int(m.group(1)) - cur_y <= 50):
            m = None                        # 只认靠近今年的两位年，「99年」不当 2099
    if m:
        g = m.group(1)
        y = 2000 + int(g) if len(g) == 2 else int(g)
        if y == cur_y:
            # 今年的年份说法，含义仍是「今年以来」→ 累计到系统当月
            _note = ''
            if now_key > biz_ym:
                _note = ('库内已结算到 %s，%d年1-%d月 里的 %d 月可能还没出齐，'
                         '若数偏小请如实说明，不要自行换成别的月份'
                         % (_ym_text(biz_ym), y, cur_m, cur_m))
            return _mk(m.group(0), '%d年1-%d月' % (y, cur_m), 'yearToDate',
                       now_key, note=_note, q=q)
        _note = ''
        if y > cur_y:
            _note = '%d 年还没到，库里不会有数据 —— 请如实告知用户，不要用其他年份顶替' % y
        return _mk(m.group(0), '%d年' % y, 'year', str(y), note=_note, q=q)

    # ③′ 截至 / 前 N 个月：截至9月、前9个月、到9月份（口径 = 1 月累计到该月）
    #     ★ 必须排在 ③ 之前：「截至5月」里也有「5月」，③ 会先把它当单月吃掉，
    #       「截至」这个累计语义就丢了。长的说法先认，这条顺序不能调。
    m = re.search(r'(?:截至|截到|到|前)\s*(\d{1,2}|' + _CN_ALT + r')\s*个?\s*月份?', q)
    if m:
        mo = _to_int(m.group(1))
        if 1 <= mo <= 12 and mo <= cur_m:
            return _mk(m.group(0), '%d年1-%d月' % (cur_y, mo), 'yearToDate',
                       '%04d%02d' % (cur_y, mo),
                       note='按「1 月累计到 %d 月」的口径取数' % mo, q=q)

    # ③ 光说月份：8月 / 8月份 / 五月（默认当年）
    #    ★ 原来带 (?!份)，把最常见的口语「5月份」整句挡在门外 —— 掉到 ⑦ 兜底，
    #      被当成「没说时间」，答成「今年至今」，模型还会收到「问题里没说时间」
    #      这句自相矛盾的说明（2026-10-27 实测：问 5 月答成 1-9 月）。
    #    ★ lookbehind 只留 \d：①′/①″ 已各自吃掉「26年5月」「今年5月」，
    #      再躲「年」就会把相对年后面的月份一起误伤。
    #    ★ 未来月（说 12 月、现在才 10 月）不再静默落空，按当年给 + 提示未到。
    m = re.search(r'(?<!\d)(\d{1,2}|' + _CN_ALT + r')\s*月份?', q)
    if m:
        mo = _to_int(m.group(1))
        if 1 <= mo <= 12:
            _note = ''
            if mo > cur_m:
                _note = ('问题说的是 %d 月，但系统当期才 %d 月 —— 该月还没到，'
                         '请如实告知用户，不要用别的月份顶替' % (mo, cur_m))
            return _mk(m.group(0), '%d年%d月' % (cur_y, mo), 'month',
                       '%04d%02d' % (cur_y, mo), note=_note, q=q)

    # ④ 相对月份：上月 / 本月 / 下月
    for word, off in sorted(_MONTH_REL, key=lambda x: -len(x[0])):
        if word in q:
            y, mo = cur_y, cur_m + off
            while mo <= 0:
                mo += 12
                y -= 1
            while mo > 12:
                mo -= 12
                y += 1
            return _mk(word, '%d年%d月' % (y, mo), 'month',
                       '%04d%02d' % (y, mo), q=q)

    # ⑤ 相对年份：今年 / 去年 / 前年
    for word, off in sorted(_YEAR_REL, key=lambda x: -len(x[0])):
        if word in q:
            y = cur_y + off
            if off == 0:
                _note = ''
                if now_key > biz_ym:
                    _note = ('库内已结算到 %s，%d年1-%d月 里的 %d 月可能还没出齐，'
                             '若数偏小请如实说明，不要自行换成别的月份'
                             % (_ym_text(biz_ym), y, cur_m, cur_m))
                return _mk(word, '%d年1-%d月' % (y, cur_m), 'yearToDate',
                           now_key, note=_note, q=q)
            return _mk(word, '%d年' % y, 'year', str(y), q=q)

    # ⑥ 季度：库里没有季度口径，只能给区间 + 说明
    m = re.search(r'([1-4一二三四])\s*季度|本季度|这个季度', q)
    if m:
        head = m.group(0)
        if head in ('本季度', '这个季度'):
            qn = (cur_m - 1) // 3 + 1
        else:
            qn = _to_int(m.group(1)) or 1
        sm = (qn - 1) * 3 + 1
        em = sm + 2
        _note = '库里没有季度口径，只能按 %d 年 %d-%d 月取累计值，' \
                '回答时要说清这是区间累计而不是季度指标。' % (cur_y, sm, em)
        if em > cur_m:
            _note += '该季度尚未结束（系统当期 %d 月），数据只到 %d 月。' % (cur_m, cur_m)
            em = cur_m
        # ★ period_key 必须是**该季度的结束月**，不能取「业务最新月」——
        #   原写法会让「二季度」被算成「1-9 月」，季度区间整个失效。
        return _mk(head, '%d年%d-%d月' % (cur_y, sm, em), 'yearToDate',
                   '%04d%02d' % (cur_y, em), note=_note, q=q)

    # ⑥′ 有时间的字眼、却没落进任何口径 —— **绝不许静默改用默认值**
    #     典型：13月、说了个库里没有的周期说法。原来直接掉 ⑦ 按「今年至今」答，
    #     用户完全看不出时间被换掉了。这里显式标 unmatched，让模型去跟用户确认。
    _hint = _TIME_HINT.search(q)
    if _hint:
        r = _mk(_hint.group(0), '未确定', 'month', now_key, q=q)
        r['unmatched'] = True
        r['note'] = ('问题里的「%s」没能对上可用的口径 —— 请先跟用户确认具体期间再取数，'
                     '**不要自己假定成今天/本月**' % _hint.group(0))
        return r

    # ⑦ 一个时间词都没有 → 优先沿用上一轮，其次默认**系统当月**
    if prev and prev.get('period_type') and prev.get('period_key'):
        return _mk('', prev.get('time_text') or str(prev.get('period_key')),
                   prev['period_type'], prev['period_key'], is_default=True,
                   note='问题里没说时间，沿用上一轮的「%s」（多轮会话）。'
                        % (prev.get('time_text') or prev.get('period_key')), q=q)
    return _mk('', '%d年%d月' % (cur_y, cur_m), 'month', now_key, is_default=True,
               note='问题里没说时间，按系统当月（%d年%d月）理解。' % (cur_y, cur_m), q=q)


# 供电所/公司名单（从纠错库里取一次，按长度降序）
_UNITS = {'list': None}
# 简称表：把「国网随县供电公司」这类全名剥成核心「随县」，用于认出用户说的简称。
# 只认长度 >= 2 的核心，避免「所」「站」这类一个字的核心误命中。
_UNIT_CORES = {'list': None}
_STRIP_HEAD = ('国网', '湖北省', '湖北省电力', '随州市', '随州', '中国')
_STRIP_TAIL = ('供电服务站', '供电服务公司', '供电有限公司', '供电公司', '供电所', '服务站', '公司', '供电')


def _units():
    """库里的供电所/公司名，长的排前面（用于从问题里认出地点）。"""
    if _UNITS['list'] is None:
        out = []
        try:
            from app.secretary import name_fix
            c = name_fix._corrector()
            if c is not None:
                out = [(nm, k) for nm, k in c.kind.items()
                       if k in ('供电公司', '供电所', '供电服务站')]
                out.sort(key=lambda x: -len(x[0]))
        except Exception:
            out = []
        _UNITS['list'] = out
    return _UNITS['list']


def _strip_unit(name):
    """把全名剥成核心：国网随县供电公司 → 随县；环潭供电所 → 环潭。"""
    s = str(name or '').strip()
    changed = True
    while changed:
        changed = False
        for h in _STRIP_HEAD:
            if s.startswith(h) and len(s) > len(h) + 1:
                s = s[len(h):]
                changed = True
        for t in _STRIP_TAIL:
            if s.endswith(t) and len(s) > len(t) + 1:
                s = s[:-len(t)]
                changed = True
    return s.strip()


def _unit_cores():
    """核心 → 全名，长的核心排前面。"""
    if _UNIT_CORES['list'] is None:
        d = {}
        for nm, _k in _units():
            core = _strip_unit(nm)
            if len(core) >= 2 and core not in d:
                d[core] = nm
        _UNIT_CORES['list'] = sorted(d.items(), key=lambda x: -len(x[0]))
    return _UNIT_CORES['list']


# 地点层的类别（供电所 / 公司 / 服务站）。台区是**对象层**，单独处理，见 place_of。
_PLACE_KINDS = ('', '供电公司', '供电所', '供电服务站')


def place_of(fixed, question=''):
    """从纠错结果里取地点（= 本轮的统计范围）；一个都认不出就返回空串。

    纠错层只报告「被改过」的名字 —— 「环潭供电所」本来就在库里，
    它一个字都不改，names/areas 都是空的。所以还要拿名单在原问题上兜一道。

    ★ **台区也算地点**。库里的名字 99.7% 是台区（两万多条；供电所级只有几十条），
      只认供电所的话，「9颗松情况怎么样」的地点会被当成「全市」，
      下一句追问「情况怎么样，售电量，线损这些」就把「全市」沿用过去，整题答偏 ——
      用户问的是那个台区，答出来的却是全市的数。
      两级同时出现时取**台区**：更具体的那个才是本轮范围，
      否则「环潭供电所九棵松台区」会被退回整个供电所，数对不上。
    """
    names = (fixed or {}).get('names') or []
    areas = (fixed or {}).get('areas') or []
    unit_hit = ''
    area_hit = ''
    for src in (areas, names):
        for it in src:
            nm = str(it.get('标准名') or it.get('文本') or '').strip()
            if not nm:
                continue
            kd = str(it.get('类别') or '')
            if kd == '台区':
                area_hit = area_hit or nm
            elif kd in _PLACE_KINDS:
                unit_hit = unit_hit or nm
    if area_hit or unit_hit:
        return area_hit or unit_hit
    # 原问题和纠错后的问题都比一遍：「凉水供电所」在纠错后是「两水供电所」，
    # 名单里存的是后者，只比原问题就认不出来。
    blob = (question or '') + ' ' + str((fixed or {}).get('text') or '')
    # ★ 先认**台区**：纠错层只报「改过的」名字，「广水市第一人民医院」本来就是
    #   库里的标准名，names/areas 全是空的 —— 不加这一步，下面的简称兜底会把
    #   「广水」认成县公司，把台区范围**放大成整个公司**（2026-10-27 实测：
    #   问题问的是那个医院台区，口径却变成广水市供电公司，数字看着正常但全错）。
    try:
        from app.secretary import object_index
        for o in object_index.scan(blob):
            if o['level'] == 'area':
                return o['std_name']
    except Exception:                       # noqa: BLE001
        pass
    for nm, _k in _units():
        if nm in blob:
            return nm
    # 只说了简称（「随县」而名单里是「国网随县供电公司」）时，用核心名兜一道。
    # 放在全名匹配之后：全名命中优先，避免简称把更具体的地名抢走。
    for core, full in _unit_cores():
        if core in blob:
            return full
    return ''


# 「明说是全市范围」的说法。place_of 对「明说全市」和「什么都没说」都返回空串，
# 所以多轮里判断"要不要沿用上一轮地点"时，必须靠这几个词把两者分开。
_WHOLE_CITY = ('全市', '整个市', '市局', '各区县', '各县区', '各个区县', '所有区县', '各县', '各所')


def _says_whole_city(question):
    """问题里是不是**明说**要全市范围（而不是"没说地点"）。"""
    q = str(question or '')
    return any(w in q for w in _WHOLE_CITY)


def _object_of(place):
    """地名 → 对象身份（含该在每张表里怎么过滤）。

    ★ 这一步解决「补全后的名字换个表就查不到」：
      补全层只能给一个名字，而同一个对象在各表写法不同（「广水市供电公司」vs
      「国网广水市供电公司」）、station 层的 scope_key 还是 sha256。
      查一次维表，把可直接抄进 SQL 的条件一起交给模型。
    维表没就绪 / 认不出 → 返回 None，只是少一段说明，不影响口径。
    """
    try:
        from app.secretary import object_index
        if not object_index.available():
            return None
        if place in ('', '全市'):
            # 「全市」不是一个对象名，但模型照样需要一个正确条件
            return object_index.best('随州市', level='city')
        return object_index.best(place)
    except Exception:                       # noqa: BLE001
        return None


def describe(question, fixed=None, prev=None, today=None, force_place=None):
    """一步到位：返回 {time, place, scope_text, question, inherited}。

    scope_text 是给模型看的【统计范围】说明；question 是补全后的完整问题。
        · 相对时间词（上月/本月/今年…）会被换成具体时间，写回问题里；
        · 问题里没时间、没地点 → **优先沿用上一轮**（多轮会话），
          再没有才补默认值（**系统当月** / 全市）；沿用与默认都**不改写原问题**，
          只附说明，免得把用户的原话改得面目全非。

    prev = 上一轮的统计范围（同一场会话）：{period_type, period_key, time_text, place}
    force_place = 上游（业务系统）指定的数据范围，**强制**：提问人是谁、在哪个页面只有上游知道，
        所以给了它就压过「问题里认出的地点」与「沿用上一轮的地点」。
    """
    t = resolve(question, today=today, prev=prev)
    forced = (force_place or '').strip()
    if forced and _says_whole_city(question):
        # 上游给的是「这个人默认可看的范围」，但本轮用户**明说**要全市（「全市几个节点最慢？」）
        # —— 明说的优先，范围按问题里的来。
        forced = ''
    place = forced or place_of(fixed, question)
    place_default = False
    place_inherited = False
    if not place:
        # 没认出地点：上一轮有、且这轮没明说「全市」这类范围词 → 沿用上一轮。
        # 注意必须排除「全市」：place_of 对「明说全市」和「什么都没说」都返回空串，
        # 不加这一个判断，用户问「那全市的呢」会被错误地沿用到上一轮的供电所。
        if prev and prev.get('place') and not _says_whole_city(question):
            place = prev['place']
            place_inherited = True
        else:
            place = '全市'
            place_default = True
    _today = _as_date(today)
    cur_y, cur_m = int(_today.year), int(_today.month)
    now_key = '%04d%02d' % (cur_y, cur_m)
    biz_ym = _latest_ym()          # 只用于说明「数据出没出」，不参与任何口径计算

    lines = []
    lines.append('时间：%s（%s，库内字段 period_type=%s、period_key=%s）'
                 % (t['time_text'],
                    {'month': '当月', 'year': '全年', 'yearToDate': '本年累计'}.get(t['period_type'], ''),
                    t['period_type'], t['period_key']))
    # 「用的默认值 / 沿用上一轮」这类说明由 note 给出（resolve 里已写好），这里不重复
    if t['note']:
        lines.append('      ' + t['note'])
    if forced:
        lines.append('地点：%s（**上游业务系统指定的数据范围，强制按此范围取数**；'
                     '本轮问题里若出现别的地名，指的是该范围内的对象，不改范围）' % place)
    elif place_inherited:
        # 沿用的地点是**推断**出来的，不是用户这轮说的 —— 必须允许模型推翻，
        # 否则用户换成别的简称（我们没认出来的）就会被错误地按上一轮的口径查。
        lines.append('地点：%s（本轮问题里没认出地点，沿用上一轮。'
                     '**如果本轮问题里其实提到了别的地点，以问题里说的为准**）' % place)
    else:
        lines.append('地点：%s%s' % (place, '（问题里没说地点，默认全市）' if place_default else ''))

    # 把这个对象的「跨表过滤条件」一起交给模型。名字在各表写法不同，条件不会变。
    obj = _object_of(place)
    if obj:
        from app.secretary import object_index
        for ln in object_index.describe(obj).splitlines():
            lines.append('      ' + ln)

    # 口径可用性：提前说清 —— **只说明，绝不替用户换口径**
    #   ★ 原来的写法是「请改用本年累计」，等于让模型把用户问的 5 月偷偷换成 9 月累计。
    #     用户看到的就是「我问 5 月，系统硬补到 9 月」（2026-10-27 修）：
    #     口径必须保持在用户说的那个月，数据有没有是另一件事，如实说就行。
    if t.get('unmatched'):
        lines.append('      ⚠️ 本轮的时间没定下来，**先跟用户确认具体期间**，不要套用任何默认月份。')
    if t['period_type'] == 'month':
        if not _month_has_data(t['period_key']):
            near = _nearest_month(t['period_key'])
            if t['period_key'] > biz_ym:
                lines.append('      ⚠️ %s 的数据还没结算完（库内已结算到 %s）—— '
                             '请如实说明这一点，**不要自行换成别的月份**。'
                             % (t['time_text'], _ym_text(biz_ym)))
            elif near:
                lines.append('      ⚠️ %s 的「当月」值在快照表里是空的（数据缺失）—— '
                             '请如实说明该月缺数，**不要自行换成别的月份**；'
                             '用户同意后再改用 %s。' % (t['time_text'], _ym_text(near)))
            else:
                lines.append('      ⚠️ %s 的「当月」值在快照表里是空的 —— '
                             '请如实说明缺数，**不要自行换成别的月份**。' % t['time_text'])
        if _is_station_level(question, fixed):
            _p = _prev_ym(t['period_key'])
            lines.append('      ⚠️ 六项指标快照表在「供电所/台区」粒度**没有当月口径**（只有本年累计和全年）。'
                         '要 %s 的数，请用 %s 的累计值减 %s 的累计值求净增，'
                         '并在回答里说明这是「差额」口径 —— 不要拿最新月份的数顶替。'
                         % (t['time_text'], t['period_key'], _p))

    return {'time': t, 'place': place, 'place_default': place_default,
            'object': obj,
            'inherited': {'time': bool(t['note'] and '沿用上一轮' in t['note']),
                          'place': place_inherited},
            'scope_text': '\n'.join(lines), 'question': t['rewritten'] or question}


# ── Site pack override (P3, 2026-10-21): lexicon/business_terms.yml ─────
def _apply_pack_lexicon() -> None:
    try:
        from app.secretary import site_pack
        lx = ((site_pack.load(strict=False) or {}).get("lexicon") or {}).get("business_terms") or {}
    except Exception:                              # noqa: BLE001
        return
    patterns = {"station_hint": "_STATION_HINT", "biz_words": "_BIZ"}
    for key, gname in patterns.items():
        pat = (lx or {}).get(key)
        if not pat or gname not in globals():
            continue
        try:
            globals()[gname] = re.compile(pat)
        except Exception:                          # noqa: BLE001
            print("[lexicon] bad pattern, keep default:", key)


_apply_pack_lexicon()


# ── 站点可配：剥名字前缀（P3, 2026-10-21）──────────────────────────────
def _apply_pack_strip_head() -> None:
    try:
        from app.secretary import site_pack
        lx = ((site_pack.load(strict=False) or {}).get('lexicon') or {}).get('business_terms') or {}
        pat = lx.get('strip_head')
        if pat and '_STRIP_HEAD' in globals():
            globals()['_STRIP_HEAD'] = tuple(pat)
    except Exception:                              # noqa: BLE001
        pass


_apply_pack_strip_head()
