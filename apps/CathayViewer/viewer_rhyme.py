# -*- coding: utf-8 -*-
"""CathayViewer · 学术书库浏览与阅读 —— 电报「代日韵目」表  v0.1.0

近代中文电报用《平水韵》的韵目字代替日期（所谓「韵目代日」）：
「有电」= 25 日的电报，「艳电」= 29 日的电报（汪兆铭 1938-12-29 的《艳电》即因此得名）。

本模块纯逻辑（不依赖 PyQt），提供：
  * 完整的 1–31 日 × 上平声 / 下平声 / 上声 / 去声 / 入声 五栏韵目表
  * 按字查日（lookup）、按日查字（day_chars / common_char）
  * annotate(text)：在文末追加【代日韵目：有=25日；艳=29日】
  * selftest()：表结构自检

数据依据（栏位 = 平水韵 106 韵按序号对齐日期）：
  上平声 15 韵 → 只能排到 15 日；下平声 15 韵 → 15 日；
  上声 29 韵 → 29 日；去声 30 韵 → 30 日；入声 17 韵 → 17 日。
  所以 16 日起上平 / 下平两栏空缺，18 日起入声栏空缺，30 日起上声栏空缺——
  这就是「部分韵目因电报价目表的选择而空缺」。

「常用字」（实际通电最常用的一栏）：
  1–15 日 用上平声；16–28 日 用上声；29 日 用去声「艳」（不用生僻的「豏」）；
  30 日 按规律是去声「陷」，但军中嫌「陷」不吉利，通用「卅」；
  31 日 平水韵没有第 31 韵，用「世」（卅一的合写）或「引」（形似 31）。
"""

# ---------------------------------------------------------------- 数据表

TONES = ('上平声', '下平声', '上声', '去声', '入声')

# 每日五栏韵目（None = 该声调没有这么靠后的韵，故空缺）
DAY_RHYME = {
    1:  ('东', '先', '董', '送', '屋'),
    2:  ('冬', '萧', '肿', '宋', '沃'),
    3:  ('江', '肴', '讲', '绛', '觉'),
    4:  ('支', '豪', '纸', '寘', '质'),
    5:  ('微', '歌', '尾', '未', '物'),
    6:  ('鱼', '麻', '语', '御', '月'),
    7:  ('虞', '阳', '麌', '遇', '曷'),
    8:  ('齐', '庚', '荠', '霁', '黠'),
    9:  ('佳', '青', '蟹', '泰', '屑'),
    10: ('灰', '蒸', '贿', '卦', '药'),
    11: ('真', '尤', '轸', '队', '陌'),
    12: ('文', '侵', '吻', '震', '锡'),
    13: ('元', '覃', '阮', '问', '职'),
    14: ('寒', '盐', '旱', '愿', '缉'),
    15: ('删', '咸', '潸', '翰', '合'),
    16: (None, None, '铣', '谏', '叶'),
    17: (None, None, '篠', '霰', '洽'),
    18: (None, None, '巧', '啸', None),
    19: (None, None, '皓', '效', None),
    20: (None, None, '哿', '号', None),
    21: (None, None, '马', '个', None),
    22: (None, None, '养', '祃', None),
    23: (None, None, '梗', '漾', None),
    24: (None, None, '迥', '敬', None),
    25: (None, None, '有', '径', None),
    26: (None, None, '寝', '宥', None),
    27: (None, None, '感', '沁', None),
    28: (None, None, '俭', '勘', None),
    29: (None, None, '豏', '艳', None),
    30: (None, None, None, '陷', None),
    31: (None, None, None, None, None),
}

# 实际通电最常用的那一栏（30 日用「卅」、31 日用「世」代替）
COMMON = {
    1: '东', 2: '冬', 3: '江', 4: '支', 5: '微', 6: '鱼', 7: '虞', 8: '齐',
    9: '佳', 10: '灰', 11: '真', 12: '文', 13: '元', 14: '寒', 15: '删',
    16: '铣', 17: '篠', 18: '巧', 19: '皓', 20: '哿', 21: '马', 22: '养',
    23: '梗', 24: '迥', 25: '有', 26: '寝', 27: '感', 28: '俭', 29: '艳',
    30: '卅', 31: '世',
}

# 31 日没有平水韵韵目，用这两个字代替；30 日因「陷」不吉利而用「卅」
SUBSTITUTE = {
    30: ('卅', '「陷」字军中嫌不吉利，通用「卅」'),
    31: ('世', '平水韵无第 31 韵：用「世」（卅一合写）或「引」（形似 31）'),
}

# 异体 / 常见写法
ALIAS = {
    '筱': '篠',      # 篠 的简体写法
    '噳': '麌',
    '簌': '篠',
}

_CN_NUM = '〇一二三四五六七八九'


def _cn(n):
    """1–31 → 汉字数字（二十九）"""
    if n <= 0:
        return str(n)
    if n < 10:
        return _CN_NUM[n]
    if n == 10:
        return '十'
    if n < 20:
        return '十' + _CN_NUM[n - 10]
    return _CN_NUM[n // 10] + '十' + (_CN_NUM[n % 10] if n % 10 else '')


def full_name(char, day=None):
    """给一个韵目字返回完整称呼，如 有 → 上声二十五有。不在表里返回空串。"""
    d = day if day else (CHAR2DAY.get(char) or {}).get('day')
    if not d:
        return ''
    for ti, ch in enumerate(DAY_RHYME.get(d) or ()):
        if ch == char:
            return '%s%s%s' % (TONES[ti], _cn(d), char)
    if char == '卅':
        return '（卅＝陷，去声三十陷）'
    if char in ('世', '引'):
        return '（31 日无韵目，用「世」或「引」代替）'
    return ''


def _build():
    """展开成 {字: {'day':日, 'tone':声调序, 'name':完整称呼, 'common':是否常用字}}"""
    m = {}
    for d, row in DAY_RHYME.items():
        for ti, ch in enumerate(row):
            if not ch:
                continue
            m[ch] = {'day': d, 'tone': ti,
                     'name': '%s%s%s' % (TONES[ti], _cn(d), ch),
                     'common': (COMMON.get(d) == ch)}
    for ch, sub in SUBSTITUTE.items():
        m[sub[0]] = {'day': ch, 'tone': None, 'name': sub[1],
                     'common': True}
    if '引' not in m:                       # 31 日另一个代替字
        m['引'] = {'day': 31, 'tone': None,
                   'name': '（31 日无韵目，用「世」或「引」代替）', 'common': False}
    for a, b in ALIAS.items():              # 异体指到正字
        if b in m:
            v = dict(m[b])
            v['alias'] = a
            m[a] = v
    return m


CHAR2DAY = _build()


def lookup(ch):
    """查一个字 → {'day','tone','name','common'}，查不到返回 None。"""
    if not ch:
        return None
    ch = ALIAS.get(ch, ch)
    return CHAR2DAY.get(ch)


def day_chars(d):
    """某日全部可用韵目字 → [(声调序, 字, 是否常用)]"""
    out = []
    for ti, ch in enumerate(DAY_RHYME.get(d) or ()):
        if ch:
            out.append((ti, ch, COMMON.get(d) == ch))
    return out


def common_char(d):
    """某日最常用的那个字"""
    return COMMON.get(d, '')


def table_rows():
    """给界面用的 31 行：{'day','cells':[5 个字或 ''],'common','note'}"""
    rows = []
    for d in range(1, 32):
        row = DAY_RHYME.get(d) or ()
        cells = [c or '' for c in row]
        note = ''
        if d in SUBSTITUTE:
            note = SUBSTITUTE[d][1]
            if d == 31:
                note += '；现代也写作「卅一」'
        rows.append({'day': d, 'cells': cells,
                     'common': COMMON.get(d, ''), 'note': note})
    return rows


# ------------------------------------------------- 「代日韵目 + 电」自动标注

# 这些是现代词，不是「某日电报」，命中就跳过（只列首字是韵目字的）
SKIP_WORDS = {'送电', '发电', '停电', '供电', '用电', '充电', '放电', '漏电',
              '触电', '断电', '感电', '水电', '火电', '核电', '风电', '光电',
              '彩电', '家电', '邮电', '贺电', '急电'}
# 前一字是这些否定词时不算（「没有电」不该注成「有电」）
NEG_PREFIX = '没無无未'
# 「有电文」＝「有 + 电文」，不是韵目代日
NEG_SUFFIX = '文'


def find_hits(text):
    """在正文里找「韵目字 + 电」→ [(字, 日, 位置)]，按出现顺序去重。"""
    if not text:
        return []
    hits, seen = [], set()
    n = len(text)
    for i, ch in enumerate(text):
        if ch != '电' or i == 0:
            continue
        a = text[i - 1]
        if a in NEG_PREFIX:                  # 无电 / 未电 …
            continue
        if i >= 2 and text[i - 2] in NEG_PREFIX and lookup(a):
            continue                          # 没有电 / 未有电 … 里的「有电」不是韵目
        if i + 1 < n and text[i + 1] in NEG_SUFFIX:   # 有电文
            continue
        if a + '电' in SKIP_WORDS:
            continue
        info = lookup(a)
        if not info or a in seen:
            continue
        seen.add(a)
        hits.append((a, info['day'], info.get('name') or ''))
    return hits


def annotate(text, sep='；'):
    """在正文末尾追加【代日韵目：有=25日；艳=29日】。返回 (新文本, 命中列表)。

    命中列表元素形如 {'char':'有','day':25,'name':'上声二十五有'}
    """
    hits = find_hits(text or '')
    if not hits:
        return text or '', []
    tail = '【代日韵目：%s】' % sep.join('%s=%d日' % (c, d) for c, d, _n in hits)
    out = (text or '')
    if out and not out.endswith('\n'):
        out += '\n'
    return out + tail, [{'char': c, 'day': d, 'name': n} for c, d, n in hits]


def annotate_append(text, sep='；'):
    """与 annotate 相同，供调用方统一命名（对齐 viewer_chrono）。"""
    return annotate(text, sep)


# ------------------------------------------------------------------ 自检

def selftest():
    """表结构 + 标注规则自检；返回 (通过数, 失败数)。"""
    ok = bad = 0

    def chk(cond, msg):
        nonlocal ok, bad
        if cond:
            ok += 1
        else:
            bad += 1
            print('  ✗ %s' % msg)

    # 1 结构：31 天、每天 5 栏
    chk(len(DAY_RHYME) == 31, '应有 31 天')
    chk(all(len(v) == 5 for v in DAY_RHYME.values()), '每天应有 5 栏')
    # 2 上平/下平只有 15，上声 29，去声 30，入声 17
    chk(all(DAY_RHYME[d][0] for d in range(1, 16))
        and not any(DAY_RHYME[d][0] for d in range(16, 32)), '上平声只到 15 日')
    chk(all(DAY_RHYME[d][1] for d in range(1, 16))
        and not any(DAY_RHYME[d][1] for d in range(16, 32)), '下平声只到 15 日')
    chk(all(DAY_RHYME[d][2] for d in range(1, 30))
        and not any(DAY_RHYME[d][2] for d in range(30, 32)), '上声只到 29 日')
    chk(all(DAY_RHYME[d][3] for d in range(1, 31))
        and not DAY_RHYME[31][3], '去声只到 30 日')
    chk(all(DAY_RHYME[d][4] for d in range(1, 18))
        and not any(DAY_RHYME[d][4] for d in range(18, 32)), '入声只到 17 日')
    # 3 无重复字（除代替字）
    chars = [c for row in DAY_RHYME.values() for c in row if c]
    chk(len(chars) == len(set(chars)), '韵目字不应重复：%d/%d' % (len(chars), len(set(chars))))
    # 4 关键锚点
    chk(lookup('有') and lookup('有')['day'] == 25, '有 = 25 日')
    chk(lookup('艳') and lookup('艳')['day'] == 29, '艳 = 29 日')
    chk(lookup('东') and lookup('东')['day'] == 1, '东 = 1 日')
    chk(lookup('卅') and lookup('卅')['day'] == 30, '卅 = 30 日')
    chk(lookup('世') and lookup('世')['day'] == 31, '世 = 31 日')
    chk(lookup('引') and lookup('引')['day'] == 31, '引 = 31 日')
    chk(lookup('陷') and lookup('陷')['day'] == 30, '陷 = 30 日')
    chk(lookup('筱') and lookup('筱')['day'] == 17, '筱（篠异体）= 17 日')
    chk(lookup('发电')[:1] if False else not lookup('发'), '发 不是韵目字')
    chk(full_name('有') == '上声二十五有', 'full_name(有) = 上声二十五有（实为 %s）' % full_name('有'))
    chk(full_name('艳') == '去声二十九艳', 'full_name(艳) = 去声二十九艳（实为 %s）' % full_name('艳'))
    # 5 常用字
    chk(COMMON[25] == '有' and COMMON[29] == '艳' and COMMON[30] == '卅'
        and COMMON[31] == '世' and COMMON[1] == '东' and COMMON[16] == '铣', '常用字表')
    # 6 标注规则
    t, h = annotate('昨日接有电，当即复去。')
    chk('【代日韵目：有=25日】' in t and len(h) == 1, '有电 → 25 日（实得 %r）' % t[-30:])
    t2, h2 = annotate('艳电发表后，各方震动。')
    chk('艳=29日' in t2, '艳电 → 29 日（实得 %r）' % t2[-30:])
    t3, h3 = annotate('有电文三件，已归档。')
    chk(h3 == [] and '代日韵目' not in t3, '有电文 不注（实得 %r）' % t3[-30:])
    t4, _ = annotate('此处没有电源。')
    chk('代日韵目' not in t4, '没有电 不注（实得 %r）' % t4[-30:])
    t5, _ = annotate('变电站今日送电。')
    chk('代日韵目' not in t5, '送电 不注（实得 %r）' % t5[-30:])
    t6, h6 = annotate('东电与艳电均已收到。')
    chk(len(h6) == 2 and '东=1日' in t6 and '艳=29日' in t6, '多命中合并（实得 %r）' % t6[-40:])
    t7, h7 = annotate('通电全国。')
    chk(h7 == [], '通电 不该命中（通非韵目字）')
    # 7 表格行
    rows = table_rows()
    chk(len(rows) == 31 and rows[28]['cells'][3] == '艳'
        and rows[29]['common'] == '卅', 'table_rows 结构')
    return ok, bad


if __name__ == '__main__':
    _o, _b = selftest()
    print('代日韵目自检：%d 通过 / %d 失败' % (_o, _b))
    print()
    print('日期  上平声  下平声  上声  去声  入声   常用')
    for r in table_rows():
        print('%2d    %s' % (r['day'],
                             '  '.join((c or '－').ljust(3) for c in r['cells'])
                             + '   ' + r['common']))
