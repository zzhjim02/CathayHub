# -*- coding: utf-8 -*-
"""CathayViewer · 学术书库浏览与阅读 —— 历史纪年换算  v0.1.0

纯逻辑（不依赖 PyQt）。把近代文献里混用的纪年换算成公元年：
  * 民国纪年（民国二十六年 = 1937）
  * 年号纪年（光绪二十四年 = 1898；含明清、太平天国、大韩帝国、日本、伪满等）
  * 干支纪年（戊戌 = 1898；单干支列出 1700–2000 年全部对应年份）
  * 年号 + 干支（咸丰庚申 = 1860）

数据参考用户提供的三个资料表（明清民国年号表、中日朝三国近代纪年表、中华甲子年表）；
年号起始年、干支 60 循环均为可核验的公共事实，故内置精简表，不依赖外部盘。
"""
import re

# 天干 / 地支
STEMS = '甲乙丙丁戊己庚辛壬癸'
BRANCHES = '子丑寅卯辰巳午未申酉戌亥'
_GANZHI_BASE = 1984          # 1984 = 甲子

# 年号 / 纪年 → 元年公元（元年 = start；N 年 = start + N - 1）
ERA = {
    # 明
    '洪武': 1368, '建文': 1399, '永樂': 1403, '永乐': 1403, '洪熙': 1425,
    '宣德': 1426, '正統': 1436, '正统': 1436, '景泰': 1450, '天順': 1457, '天顺': 1457,
    '成化': 1465, '弘治': 1488, '正德': 1506, '嘉靖': 1522, '隆慶': 1567, '隆庆': 1567,
    '萬曆': 1573, '万历': 1573, '泰昌': 1620, '天啟': 1621, '天启': 1621,
    '崇禎': 1628, '崇祯': 1628,
    # 清（含入关前）
    '天命': 1616, '天聰': 1627, '天聪': 1627, '崇德': 1636, '順治': 1644, '顺治': 1644,
    '康熙': 1662, '雍正': 1723, '乾隆': 1736, '嘉慶': 1796, '嘉庆': 1796,
    '道光': 1821, '咸豐': 1851, '咸丰': 1851, '同治': 1862, '光緒': 1875, '光绪': 1875,
    '宣統': 1909, '宣统': 1909,
    # 太平天國 / 中華民國 / 洪憲 / 偽滿
    '太平天國': 1851, '太平天国': 1851,
    '民國': 1912, '民国': 1912, '洪憲': 1916, '洪宪': 1916,
    '大同': 1932, '康德': 1934,
    # 日本
    '明治': 1868, '大正': 1912, '昭和': 1926, '平成': 1989, '令和': 2019,
    # 朝鮮 / 大韓帝國
    '開國': 1392, '开国': 1392, '建陽': 1896, '建阳': 1896, '光武': 1897, '隆熙': 1907,
}

_ERA_ALT = '|'.join(sorted(ERA.keys(), key=len, reverse=True))
_NUM = r'(?:[〇零一二三四五六七八九十百千廿卅0-9]{1,6}|元|正)'
_GRP = r'[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]'

# 年号实际使用的最后一年（用于判断「康熙63年」这类越界纪年；缺项则不做越界检查）
ERA_END = {
    '洪武': 1398, '建文': 1402, '永樂': 1424, '永乐': 1424, '洪熙': 1425,
    '宣德': 1435, '正統': 1449, '正统': 1449, '景泰': 1456, '天順': 1464, '天顺': 1464,
    '成化': 1487, '弘治': 1505, '正德': 1521, '嘉靖': 1566, '隆慶': 1572, '隆庆': 1572,
    '萬曆': 1620, '万历': 1620, '泰昌': 1620, '天啟': 1627, '天启': 1627,
    '崇禎': 1644, '崇祯': 1644,
    '天命': 1626, '天聰': 1635, '天聪': 1635, '崇德': 1643, '順治': 1661, '顺治': 1661,
    '康熙': 1722, '雍正': 1735, '乾隆': 1795, '嘉慶': 1820, '嘉庆': 1820,
    '道光': 1850, '咸豐': 1861, '咸丰': 1861, '同治': 1874, '光緒': 1908, '光绪': 1908,
    '宣統': 1912, '宣统': 1912,
    '太平天國': 1864, '太平天国': 1864,
    '大同': 1933, '康德': 1945, '洪憲': 1916, '洪宪': 1916,
    '明治': 1912, '大正': 1926, '昭和': 1989, '平成': 2019,
    '開國': 1896, '开国': 1896, '建陽': 1897, '建阳': 1897, '光武': 1907, '隆熙': 1910,
    # 注：民國（1912– ）、令和（2019– ）仍在沿用，不做越界判定
}

_RE_ERA_N = re.compile(r'(%s)\s*(%s)\s*年' % (_ERA_ALT, _NUM))
_RE_ERA_GZ = re.compile(r'(%s)\s*(%s)' % (_ERA_ALT, _GRP))
_RE_GZ = re.compile(r'(%s)(年?)' % _GRP)
_RE_FY = re.compile(r'公元\s*([0-9]{3,4})\s*年?')
_RE_SKIP = re.compile(r'【[^】]*】')


def _cn2int(s):
    s = (s or '').strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s in ('元', '正'):
        return 1
    s = s.replace('廿', '二十').replace('卅', '三十')
    d = {'〇': 0, '零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
         '六': 6, '七': 7, '八': 8, '九': 9}
    u = {'十': 10, '百': 100, '千': 1000}
    if s == '十':
        return 10
    total, num = 0, 0
    for ch in s:
        if ch in d:
            num = d[ch]
        elif ch in u:
            total += (num if num else 1) * u[ch]
            num = 0
        else:
            return None
    return total + num


def ganzhi_index(gz):
    """干支 → 0–59 序号（1984 甲子 = 0）；非法组合返回 -1。"""
    if not gz or len(gz) < 2:
        return -1
    a, b = gz[0], gz[1]
    if a not in STEMS or b not in BRANCHES:
        return -1
    si, bi = STEMS.index(a), BRANCHES.index(b)
    for k in range(6):
        if (si + 10 * k) % 12 == bi:
            return si + 10 * k
    return -1


def year_to_ganzhi(year):
    i = (int(year) - _GANZHI_BASE) % 60
    return STEMS[i % 10] + BRANCHES[i % 12]


def ganzhi_years(gz, y0=1700, y1=2000):
    idx = ganzhi_index(gz)
    if idx < 0:
        return []
    return [y for y in range(int(y0), int(y1) + 1) if (y - _GANZHI_BASE) % 60 == idx]


def era_end(era):
    """年号实际使用的最后一年（公元）；未知返回 None。"""
    return ERA_END.get(era)


def _cn_num(n):
    """阿拉伯数字 → 汉数字（用于年号纪年，如 24 → 二十四）。"""
    n = int(n)
    if n <= 0:
        return str(n)
    d = '〇一二三四五六七八九'
    if n < 10:
        return d[n]
    if n < 20:
        return '十' + (d[n - 10] if n > 10 else '')
    if n < 100:
        t, u = divmod(n, 10)
        return d[t] + '十' + (d[u] if u else '')
    if n < 1000:
        h, rem = divmod(n, 100)
        s = d[h] + '百'
        if rem == 0:
            return s
        if rem < 10:
            return s + '〇' + d[rem]
        return s + _cn_num(rem)
    return str(n)


def to_cn(n):
    """对外：年数字 → 汉数字（n=1 也用「1」；纪年请用 era_year_cn）。"""
    return _cn_num(n)


def era_year_cn(n):
    """纪年年数写法：1 → 元，其余汉数字。"""
    return '元' if int(n) == 1 else _cn_num(n)


def eras_in_year(year, span=40):
    """公元 year 年在用的年号 → [{'era','n'}（n = 该年号的第几年）]（按起始年排序）。

    优先按实际起讫年（ERA_END）判定；未知结束年的年号（如民國）用宽窗口。
    """
    y = int(year)
    out = []
    for k, start in ERA.items():
        if _is_trad_dup(k) or start is None:
            continue
        end = ERA_END.get(k)
        if end is not None:
            if not (start <= y <= end):
                continue
        elif not (start <= y <= start + span):
            continue
        out.append({'era': k, 'n': y - start + 1})
    out.sort(key=lambda r: ERA[r['era']])
    return out


def format_eras(year, span=40, sep='、'):
    """反查输出：把该年每个年号写成「光绪二十四年」这样的形式。"""
    parts = ['%s%s年' % (r['era'], era_year_cn(r['n']))
             for r in eras_in_year(year, span)]
    return sep.join(parts)


def _overflow_note(era, n, y):
    """越界纪年（如康熙63年）的提示：说明超出了多少年，该年实际对应哪些纪年。"""
    st, end = ERA.get(era), ERA_END.get(era)
    if st is None or end is None:
        return ''
    span = end - st + 1
    if n <= span:
        return ''
    actual = format_eras(y) or '（同期无内置年号）'
    return '（存疑：%s共%d年，无第%d年；该年实为 %s）' % (era, span, n, actual)


_RE_REPUBLIC_MIN = 39          # 民国 39 年及以后才自动换算（复制/摘录时民国元年–38 年不再标注）


def _skip_era(era, n, allow_short=False):
    """复制/摘录自动换算时跳过民国 1–38 年；工具「换算」可传 allow_short=True 全支持。"""
    if allow_short:
        return False
    return era in ('民國', '民国') and n is not None and n <= (_RE_REPUBLIC_MIN - 1)


def convert(text, gz_from=1700, gz_to=2000, allow_short_republic=False):
    """识别 text 中的纪年，返回命中列表 [{start,end,token,note,kind,year}]（不重叠）。

    allow_short_republic=True → 民国 1–38 年也换算（供「历史纪年换算」工具用）；
    默认 False（复制/摘录自动注记时不标注民国 1–38 年）。
    """
    if not text:
        return []
    spans = [(m.start(), m.end()) for m in _RE_SKIP.finditer(text)]

    def prot(i):
        return any(a <= i < b for a, b in spans)

    def tail(m):
        return text[m.end():m.end() + 1] == '【'      # 紧跟着已有标注 → 跳过（幂等）

    cands = []
    for m in _RE_ERA_N.finditer(text):
        if prot(m.start()) or tail(m):
            continue
        era, numtxt = m.group(1), m.group(2)
        n = _cn2int(numtxt)
        st = ERA.get(era)
        if not n or st is None:
            continue
        y = st + n - 1
        if _skip_era(era, n, allow_short_republic):
            continue
        note = '%s%s年=%d年%s' % (era, numtxt, y, _overflow_note(era, n, y))
        cands.append((m.start(), m.end(), note, 'era', y))
    for m in _RE_ERA_GZ.finditer(text):
        if prot(m.start()) or tail(m):
            continue
        era, gz = m.group(1), m.group(2)
        st = ERA.get(era)
        ys = ganzhi_years(gz, gz_from, gz_to)
        pick = None
        if st is not None:
            for y in ys:
                if st - 1 <= y <= st + 70:
                    pick = y
                    break
        if pick is None and ys:
            pick = min(ys, key=lambda y: abs(y - st if st else y))
        if pick is not None:
            cands.append((m.start(), m.end(), '%s%s=%d年' % (era, gz, pick), 'era_gz', pick))
    for m in _RE_GZ.finditer(text):
        if prot(m.start()) or tail(m):
            continue
        gz = m.group(1)
        ys = ganzhi_years(gz, gz_from, gz_to)
        if not ys:
            continue
        note = '%s=%s年' % (gz, '、'.join(str(y) for y in ys))
        cands.append((m.start(), m.end(), note, 'gz', None))
    # 去重叠：起点靠前、跨度长者优先
    cands.sort(key=lambda c: (c[0], -(c[1] - c[0])))
    chosen, last = [], -1
    for s, e, note, kind, y in cands:
        if s < last:
            continue
        chosen.append((s, e, note, kind, y))
        last = e
    return [{'start': s, 'end': e, 'token': text[s:e], 'note': note,
             'kind': kind, 'year': y} for s, e, note, kind, y in chosen]


def annotate(text, gz_from=1700, gz_to=2000, allow_short_republic=False):
    """在引文里每个纪年后加【对应公元年】。返回 (新文本, 命中列表)。"""
    hits = convert(text, gz_from, gz_to, allow_short_republic)
    if not hits:
        return text, []
    out, prev = [], 0
    for h in hits:
        out.append(text[prev:h['start']])
        out.append(h['token'])
        out.append('【%s】' % h['note'])
        prev = h['end']
    out.append(text[prev:])
    return ''.join(out), hits


def annotate_append(text, gz_from=1700, gz_to=2000, allow_short_republic=False):
    """复制文本时用：在整段文字后面追加一栏【纪年换算】（去重、保留出现顺序）。

    返回 (新文本, 命中列表)。无命中则原文返回。
    """
    hits = convert(text, gz_from, gz_to, allow_short_republic)
    if not hits:
        return text, []
    notes = []
    for h in hits:
        if h['note'] not in notes:
            notes.append(h['note'])
    return (text.rstrip() + '\n【纪年换算】' + '；'.join(notes) + '。'), hits


def year_eras(year, span=40):
    """反查：公元 year 前后 span 年内在用的年号（粗略，供速查）。"""
    y = int(year)
    hit = [k for k in ERA if ERA[k] <= y <= ERA[k] + span and not _is_trad_dup(k)]
    return sorted(set(hit), key=lambda k: ERA[k])


_SIMP_MAP = {'樂': '乐', '統': '统', '順': '顺', '慶': '庆', '國': '国', '憲': '宪',
             '萬': '万', '曆': '历', '啟': '启', '禎': '祯', '聰': '聪', '緒': '绪',
             '陽': '阳', '豐': '丰'}
_TRAD_KEYS = set(k for k in ERA if any(c in _SIMP_MAP for c in k))


def _is_trad_dup(k):
    """繁体写法：简繁同词时反查只留简体那一个，避免重复。"""
    return k in _TRAD_KEYS


# ----------------------------------------------------------------- 自检
def selftest():
    import sys
    log = []

    def ok(c, m):
        log.append(('OK  ' if c else 'FAIL') + ' ' + m)
        return bool(c)

    ok(_cn2int('二十四') == 24, '汉语数字 二十四 = %s' % _cn2int('二十四'))
    ok(_cn2int('元') == 1 and _cn2int('正') == 1, '元/正 = 1')
    ok(_cn2int('廿四') == 24 and _cn2int('卅') == 30, '廿四=24 卅=30')
    ok(_cn2int('100') == 100, '阿拉伯 100')
    ok(year_to_ganzhi(1840) == '庚子', '1840 = %s（应 庚子）' % year_to_ganzhi(1840))
    ok(year_to_ganzhi(1898) == '戊戌', '1898 = %s（应 戊戌）' % year_to_ganzhi(1898))
    ok(ganzhi_index('甲子') == 0 and ganzhi_index('庚子') == 36, '干支序号 甲子=0 庚子=36')
    ok(ganzhi_index('甲丑') == -1, '非法干支 甲丑 → -1')
    ys = ganzhi_years('甲子', 1700, 2000)
    ok(ys == [1744, 1804, 1864, 1924, 1984], '甲子年份 %s' % ys)

    t1 = '光绪二十四年，戊戌变法。'
    a1, h1 = annotate(t1)
    ok('光绪二十四年=1898年】' in a1, '光绪二十四年→1898：%s' % a1)
    ok('戊戌' in a1 and '1898' in a1, '戊戌→含 1898')

    a2, h2 = annotate('民国四十年，迁台。')
    ok('民国四十年' in a2 and '1951' in a2, '民国四十年→1951：%s' % a2)
    a2b, h2b = annotate('民国三十八年，改元。')
    ok('【' not in a2b and h2b == [], '民国三十八年不转换（默认）：%s' % a2b)
    a2c, h2c = annotate('民国三十九年')
    ok('1950' in a2c, '民国三十九年→1950：%s' % a2c)
    # 工具模式（allow_short_republic=True）：民国 1–38 年也换算
    a2d, h2d = annotate('民国二十六年七七事变', allow_short_republic=True)
    ok('1937' in a2d and '民国二十六年=1937年】' in a2d,
       '民国 1–38 年在工具里可换算：%s' % a2d)
    a2e, h2e = annotate('民国元年', allow_short_republic=True)
    ok('1912' in a2e, '民国元年（工具）→1912：%s' % a2e)
    ok(convert('民国三十八年', allow_short_republic=True)[0]['year'] == 1949,
       '民国三十八年（工具）→1949')
    # 越界纪年：康熙63年（康熙共61年）→ 1724，并提示该年实际纪年
    a8, h8 = annotate('康熙六十三年')
    ok('1724' in a8 and '存疑' in a8, '康熙63年→1724 并提示存疑：%s' % a8)
    ok('雍正' in a8, '康熙63年提示该年实为雍正：%s' % a8)
    a9, h9 = annotate('光绪三十五年')
    ok('1909' in a9 and '存疑' in a9 and '宣统' in a9,
       '光绪35年（仅 34 年）→1909 并提示宣统：%s' % a9)
    # 反查注明年数
    ok(era_year_cn(24) == '二十四' and era_year_cn(1) == '元', '年号年数写法 24→二十四 1→元')
    fe = format_eras(1898)
    ok('光绪二十四年' in fe and '明治三十一年' in fe and '同治' not in fe,
       '反查 1898 注明年数（且不列已结束的年号）：%s' % fe)
    ok([r['n'] for r in eras_in_year(1898) if r['era'] == '光绪'] == [24],
       'eras_in_year(1898) 光绪 = 24 年')
    ap, hp = annotate_append('光绪二十四年戊戌')
    ok('【纪年换算】' in ap and '1898' in ap, '复制追加：%s' % ap)

    a3, h3 = annotate('咸丰庚申，英法联军入京。')
    ok('1860' in a3, '咸丰庚申→1860：%s' % a3)

    a4, h4 = annotate('事在康熙元年前后。')
    ok('1662' in a4, '康熙元年→1662：%s' % a4)

    a5, h5 = annotate('甲午战争（1894年）')
    ok('1894' in a5 and ('1834' in a5 or '1954' in a5 or '1894' in a5),
       '单干支列出 1700–2000：%s' % a5)

    a6, h6 = annotate('光绪二十四年')
    a6b, _ = annotate(a6)                       # 再标注不应重复
    ok(a6b.count('【') == a6.count('【'), '已标注不重复标注')

    a7, h7 = annotate('明治四十五年、大正十五年、昭和六十四年')
    ok('1912' in a7 and '1926' in a7 and '1989' in a7, '日本年号：%s' % a7)
    ok('光绪' in year_eras(1898), '反查 1898 → %s' % year_eras(1898))

    out = '\n'.join(log) + '\nresult = %s\n' % (
        'OK' if all(l.startswith('OK') for l in log) else 'FAIL')
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    print('CathayViewer · 历史纪年换算  v0.1.0')
    print(out)


if __name__ == '__main__':
    selftest()
