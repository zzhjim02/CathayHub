# -*- coding: utf-8 -*-
"""CathayViewer · 文件名元数据解析 / 多版本聚合 / 学术引用

规则对齐 CathayShelf（同一套产物后缀、同一套卷册写法），并复用它的出版社→城市表。
原则：只解析文件名（后续再接版权页），绝不改动任何文件。
"""
import csv
import os
import re

# ---- 产物/噪声后缀（与 CathayShelf、CathayPDG 一致）
NOISE_TOK = re.compile(
    r'(ocr\s*优化|ocr优化版|orpalis\s*优化|orp\s*优化|zhelper[-\s]?search|zhelper|'
    r'(?:pd[a-z]{0,2}\d*)?(?:ai)?f?ocr|layered|result|unlocked|清晰扫描版|扫描版|'
    r'【?\s*(?:繁转简|简转繁|繁转繁)\s*】?|纯文本|可搜索版|opt)', re.I)
TAIL_JUNK = re.compile(r'[\s_\-—+·、.]+$')
# 【v0.3.16】产物标记：只表示这份文件被**加工过**（压缩 / 图像优化），和
# 「是不是同一本书」毫无关系 —— 用户口径：`_OPT`、`_opt`、`优化`、`繁转简`
# 一律不许影响关联。和 NOISE_TOK 的区别是：这里**只认尾巴上**的（`书名（优化版）`
# 认、`生产率优化` 不认），免得把《某某优化方法》这种真书名削掉。
_PROD_MARK = re.compile(
    r'[（(【\[](?:opt|优化版|优化|压缩版|压缩)[）)】\]]'          # 整块带括号：`（优化版）`
    r'|[\s_\-—+](?:opt|优化版|优化|压缩版|压缩)'                  # 挂在尾巴上：`_opt`
    r'(?=[\s_\-—+（(【\[]|$|\.)', re.I)
SSID_RE = re.compile(r'(?<![0-9])(\d{6,10})(?![0-9])')
VOL_RE = re.compile(r'第\s*([一二三四五六七八九十百千0-9]{1,4})\s*([册卷集部编篇辑期])'
                    r'|[（(]\s*([上下中一二三四五六七八九十]{1,3})\s*[)）]|\b([上下中])卷\b')
TRAD_RE = re.compile(r'【\s*繁[体體]\s*】')
PUB_RE = re.compile(r'([\u4e00-\u9fa5]{2,18}(?:大学出版社|出版社|印书馆|书局|书社|出版公司))')
YEAR_RE = re.compile(r'(?<![0-9])(1[89]\d{2}|20\d{2})(?![0-9])')
ROLE_RE = re.compile(r'(编著|主编|著|撰|编|译|校注|校|辑|注|选辑)')
AUTHOR_LEAD = re.compile(r'^\s*([\u4e00-\u9fa5]{2,4})[：:]\s*')
SURNAME = ('赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章'
           '云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常'
           '乐于时傅皮齐康伍余元卜顾孟平黄和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞'
           '熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌'
           '霍虞万支柯昝管卢莫房裘缪干解应宗丁宣邓郁单杭洪包诸左石崔吉钮龚程嵇邢滑裴陆荣翁'
           '荀羊甄封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾'
           '暴甘钭厉戎祖武符刘景詹束龙叶幸司韶郜黎蓟薄印宿白怀蒲台从鄂索咸籍赖卓蔺屠蒙池乔'
           '阴胥能苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍璩桑桂濮牛寿通边扈燕冀浦尚农温别庄晏'
           '柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧殳沃'
           '利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养鞠须丰巢关蒯相查后荆红游竺'
           '权逯盖益桓公')
COMPOUND = ('欧阳', '太史', '端木', '上官', '司马', '东方', '独孤', '南宫', '万俟', '闻人', '夏侯',
            '诸葛', '尉迟', '公羊', '赫连', '澹台', '皇甫', '宗政', '濮阳', '公冶', '太叔', '申屠',
            '公孙', '慕容', '仲孙', '钟离', '长孙', '宇文', '司徒', '鲜于', '司空', '闾丘', '子车')

# 深著录：PDF 文字层「全文扫描」的页数上限。
# 超过这个页数就只做前后采样，绝不全文 get_text（1.8GB 的 PDF 全文要 8~10 秒）。
# 注意：这个常量以前没定义，_pdf_text 里引用它会抛 NameError 并被 except 吞掉，
#      导致大书深著录静默失败（界面一直显示「正在后台深度著录…」）。
_FULL_SCAN_MAX = 400


def book_core(name):
    """书名主干：剥扩展名 + 产物后缀 + 繁简标记 + 8 位编号（用于「同一本书」判定）。"""
    s = os.path.splitext(str(name))[0]
    s = TRAD_RE.sub('', s)
    s = NOISE_TOK.sub('', s)
    s = _PROD_MARK.sub('', s)          # 【v0.3.16】压缩 / 优化标记
    s = re.sub(r'[（(]\s*[)）]', '', s)          # 空括号
    # 书名尾巴里内嵌的著录括注（作者/出版社/年）——聚合与引用时剥掉。
    # 【v0.3.16】末尾允许残留分隔符：以前要求「）」紧跟到行尾，`_OCR_opt`
    # 这类产物尾巴剥掉后会留下 `_`，于是带产物的 PDF 保留了括注、不带产物
    # 的 TXT 把它剥掉了 —— 同一本书两边算出不一样的主干，直接后果是
    # 图文对读找不到同伴 TXT、检索扩样也拉不进来。
    _tail = re.compile(r'[（(][^（）()]*(?:出版社|印书馆|书局|书社|出版公司|编著|主编|著|译|'
                       r'[12][09]\d{2})[^（）()]*[)）][\s_\-—+·、]*$')
    for _ in range(2):
        s = re.sub(r'[\s_\-—+·、]+$', '', s)     # 先清尾部残渣，再看括注
        s = _tail.sub('', s).strip()
    s = SSID_RE.sub('', s)
    s = re.sub(r'[\s_\-—+·]+', ' ', s).strip(' _-—+·、.')
    return s.strip()


# ---- batch9：检索去重（用户口径：只忽略 _opt；不去 OCR 引擎标记 / 不去编号 / 不去繁简标记）
_OPT_TOK = re.compile(r'(?i)[\s_\-—+]*opt(?=[\s_\-—+（(【\[]|$|\.)')
_TRAD_MARK = re.compile(r'【\s*(?:繁转简|简转繁|繁转繁)\s*】|[_\-—](?:繁转简|简转繁|繁转繁)')
_ENG_TAG = re.compile(r'(?i)[\s_\-—+]*(?:pd[a-z]{0,2}\d*)?[a-z]{0,6}ocr[\s_\-—+]*$')
# 任意位置的 OCR 引擎标记（仅在 family_key 里剥，用于跨文件检索扩样）
_ANY_OCR = re.compile(r'(?i)[\s_\-—+]*(?:pd[a-z]{0,2}\d*)?(?:ai)?f?ocr[\s_\-—+]*')


def same_name_key(name):
    """「完全同名」判定键：去扩展名 + 全角转半角/清私用区 + 仅忽略产物标记（返回小写）。"""
    s = _nw(os.path.splitext(str(name))[0])
    s = _OPT_TOK.sub('', s)
    s = _PROD_MARK.sub('', s)     # 【v0.3.16】优化 / 压缩也一并忽略
    s = re.sub(r'[\s_\-—+·、.]+$', '', s)
    s = re.sub(r'^[\s_\-—+·、]+', '', s)
    return s.strip().lower()


def is_trad_variant(name):
    """是否为「繁转简/简转繁」变体（这类 TXT 一律不隐藏 —— batch9 规则④）。"""
    return bool(_TRAD_MARK.search(str(name)))


# 尾巴上的著录括注（作者 / 出版社 / 年份）—— 允许后面残留分隔符，理由见 book_core
_TAIL_BIB = re.compile(r'[（(][^（）()]*(?:出版社|印书馆|书局|书社|出版公司|编著|主编|著|译|'
                       r'[12][09]\d{2})[^（）()]*[)）][\s_\-—+·、]*$')


def book_shell(name):
    """书名壳体：剥扩展名 + 产物标记（opt/优化）+ OCR 引擎 + 繁简标记。

    **和 book_core 的两处关键区别：**
    1. 这里**不动编号**：《国务院公报》叫 `gwyb198001_FOCR_opt.pdf`，那个 6 位
       年月号和 SSID 长得一模一样，一刀切会把 636 期并成"一本书"（检索扩样直
       接炸）。编号差一期就是两本书，留着；`_11222324` 与 `11222324` 这种**粘连
       差别**交给 family_key 抹分隔符时解决，用不着动数字本身。
    2. 这里**不剥尾巴上的著录括注**（作者/出版社/年）：《陳布雷從政日記（1935）》
       这一套是按年份分卷的，剥掉年份 14 卷就成了一本，找同伴 TXT 时会串
       到别的年份去。留着反而两边一致 —— 有没有 `_opt` 都不影响它。
    """
    s = _nw(os.path.splitext(str(name))[0])
    s = TRAD_RE.sub('', s)
    s = NOISE_TOK.sub('', s)
    s = _PROD_MARK.sub('', s)
    s = _OPT_TOK.sub('', s)
    s = _TRAD_MARK.sub('', s)
    s = _ANY_OCR.sub('', s)
    s = _ENG_TAG.sub('', s)
    s = re.sub(r'[（(]\s*[)）]', '', s)          # 空括号
    s = re.sub(r'[\s_\-—+·、]+$', '', s)
    return s.strip(' _-—+·、.')


def family_key(name):
    """同一本书的「族键」—— 关联判定（检索扩样 / 对读变体 / 同书并列）都问它。

    【v0.3.16】以前它只是"同名键再剥两个标记"，于是
    `某书_11222324_AIFOCR_opt.pdf` 与 `某书11222324_AIFOCR.txt`
    （只差一个下划线、一个 `_opt`）算成两家人：检索扩样不带它、图文对读的
    版本列表里也见不到它。现在走 `book_shell` 剥壳，再把余下的分隔符 / 括号
    一并抹平 —— **粘连与否、有没有 `_opt`/`优化`、走的哪路 OCR、繁简哪一版，
    都不许把同一本书拆开**。
    反过来两处不动：卷册（上/下/中、第X辑）与原书自带的编号留在壳里。
    """
    s = book_shell(name)
    s = re.sub(r'[\s_\-—+·、.,，;；:：\[\]【】（）()「」『』]+', '', s)
    s = re.sub(r'[\s_\-—+·、.]+$', '', s)
    s = re.sub(r'^[\s_\-—+·、]+', '', s)
    return s.strip().lower()


def _ext_of(name):
    return os.path.splitext(str(name))[1].lower()


def dedup_files(files):
    """batch9 去重：只在「同一目录、PDF 与其同名 TXT」成对时动手。
    ① 命中数相同 → 隐藏 TXT；② 命中数不同 → 都留，TXT 打 txt_mark；
    ③ 不同 OCR 引擎的 TXT（名字不同）→ 都留；④ 繁转简/简转繁 TXT → 一律不隐藏；
    其余文件一律保留。files: [{'name','path','count',...}] → 返回过滤后的新列表。
    """
    groups = {}
    for f in files:
        p = f.get('path') or f.get('name') or ''
        d = os.path.normcase(os.path.dirname(os.path.abspath(p)))
        groups.setdefault(d, []).append(f)
    out = []
    for _, grp in groups.items():
        pdfs = [f for f in grp if _ext_of(f.get('name')) == '.pdf']
        for f in grp:
            g = dict(f)
            if _ext_of(g.get('name')) not in ('.txt', '.text'):
                out.append(g)
                continue
            k = same_name_key(g.get('name'))
            peers = [p for p in pdfs if same_name_key(p.get('name')) == k]
            if not peers:
                out.append(g)
                continue
            if is_trad_variant(g.get('name')):            # ④ 繁简变体豁免
                g['txt_mark'] = True
                out.append(g)
                continue
            if any(int(p.get('count') or 0) == int(g.get('count') or 0) for p in peers):
                continue                                  # ① 隐藏 TXT
            g['txt_mark'] = True                          # ② 显示并标注
            out.append(g)
    return out


def dedup_split(files):
    """返回 (kept, hidden)：hidden = 因规则①被隐藏的 TXT 项（默认不显示，可手动展开）。"""
    kept = dedup_files(files)
    kk = {(f.get('path'), f.get('name')) for f in kept}
    hidden = [f for f in files if (f.get('path'), f.get('name')) not in kk]
    return kept, hidden


def parse(name, path='', deep=None):
    """入口：当给了存在意义的 path（且 deep 不为 False）时，走「四来源深度著录」parse_deep；
    否则只解析文件名。保持旧签名兼容 —— parse(name) 行为与从前完全一致。"""
    if deep is None:
        deep = bool(path)
    if deep and path:
        try:
            return parse_deep(name, path)
        except Exception:
            pass                     # 深度著录出任何岔子都退化回文件名解析，绝不误伤
    return _parse_file(name, path)


def _parse_file(name, path=''):
    """从文件名解析：书名 / 卷册 / 作者 / 出版社 / 年份 / SSID / 是否繁体。"""
    stem = os.path.splitext(str(name))[0]
    out = {'name': book_core(name), 'raw': str(name), 'path': path,
           'ext': os.path.splitext(str(name))[1].lower(),
           'volume': '', 'author': '', 'publisher': '', 'year': '', 'ssid': '',
           'trad': bool(TRAD_RE.search(stem)), 'tail': ''}
    out['tail'] = '_【繁转简】' if '繁转简' in stem else ''
    m = VOL_RE.search(stem)
    if m:
        if m.group(1):
            out['volume'] = '第%s%s' % (m.group(1), m.group(2))
        elif m.group(3):
            out['volume'] = '（%s）' % m.group(3)
        elif m.group(4):
            out['volume'] = '%s卷' % m.group(4)
    if not out['volume']:
        m = re.search(r'全\s*([0-9一二三四五六七八九十]{1,3})\s*册', stem)
        if m:
            out['volume'] = '全%s册' % m.group(1)
    for mm in SSID_RE.finditer(stem):
        v = mm.group(1)
        if not (1900 <= int(v) <= 2099 and len(v) == 4):
            out['ssid'] = v
            break
    mp = PUB_RE.search(stem)
    if mp:
        out['publisher'] = mp.group(1)
    ys = [y for y in YEAR_RE.findall(stem)
          if not (out['ssid'] and y == out['ssid'])]
    if ys:
        out['year'] = ys[-1]
    out['author'] = _author(stem, out['name'])
    # 书名里若还带着「作者：」（CathayShelf 的旧风格），剥掉
    m2 = AUTHOR_LEAD.match(out['name'])
    if m2:
        if not out['author']:
            out['author'] = m2.group(1)
        out['name'] = out['name'][m2.end():].strip()
    return out


def _author(stem, core):
    """作者：优先「X著/编/译」，其次开头「X：」，最后核心名前的姓氏词。"""
    for m in ROLE_RE.finditer(stem):
        pre = stem[:m.start()].strip(' 　_·、,，')
        seg = re.split(r'[\s　_·、,，]+', pre)[-1] if pre else ''
        seg = re.sub(r'^[\u4e00-\u9fa5]{0,2}[·•]', '', seg)
        if 2 <= len(seg) <= 4 and _is_name(seg):
            return seg
    m = AUTHOR_LEAD.match(core)
    if m:
        return m.group(1)
    return ''


def _is_name(tok):
    if not tok or len(tok) < 2 or len(tok) > 4:
        return False
    if not all('\u4e00' <= ch <= '\u9fff' for ch in tok):
        return False
    if tok[0:2] in COMPOUND:
        return True
    return tok[0] in SURNAME


# ---- 出版社 → 城市（复用 CathayShelf 的表；只读读入，不改它）
_CITY = None


def _city_map(extra_csv=''):
    """懒加载 出版社→城市 字典（只读读入，不改任何外部文件）。"""
    global _CITY
    if _CITY is None:
        _CITY = {}
        cands = [extra_csv] if extra_csv else []
        here = os.path.dirname(os.path.abspath(__file__))
        cands += [os.path.join(here, 'config', 'publishers.csv'),
                  os.path.join(app_dir_guess(), 'config', 'publishers.csv'),
                  # 只读兜底：直接读 CathayShelf 那份表（不复制、不改它）
                  os.path.join(os.path.dirname(here), 'CathayShelf-DEV', 'config',
                               'publishers.csv'),
                  os.path.join(r'D:\CathayHub-DEV\CathayShelf-DEV\config', 'publishers.csv')]
        for p in cands:
            if p and os.path.isfile(p):
                try:
                    with open(p, encoding='utf-8-sig', newline='') as f:
                        for row in csv.reader(f):
                            if len(row) >= 2 and row[0].strip() and not row[0].startswith('#'):
                                _CITY.setdefault(row[0].strip(), row[1].strip())
                except Exception:
                    pass
    return _CITY


def publisher_city(pub, extra_csv=''):
    return _city_map(extra_csv).get(pub, '') if pub else ''


def app_dir_guess():
    import sys
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


# ============================================================================
# 深度著录：文件夹链 + 文件名 + 同名文本 + PDF 文本层（算法移植自 CathayShelf core.py）
#   只读复用其规则，不改动、不导入 CathayShelf（避免打包耦合）。
# ============================================================================

# 全角 -> 半角（版权页常用全角数字/字母，如 ２０１６、ＩＳＢＮ）
_FW = {}
for _i in range(10):
    _FW[0xFF10 + _i] = chr(ord('0') + _i)
for _i in range(26):
    _FW[0xFF21 + _i] = chr(ord('A') + _i)
    _FW[0xFF41 + _i] = chr(ord('a') + _i)
for _a, _b in zip('：，．；－（）［］／＼％＆＃＠　', ':,.;-()[]/\\%&#@ '):
    _FW[ord(_a)] = _b

_JUNK = re.compile('[\ue000-\uf8ff\ufffd\u200b-\u200f\ufeff\u2028\u2029'
                   '\U000F0000-\U000FFFFD\U00100000-\U0010FFFD\U000E0000-\U000E007F]')


def _nw(s):
    """全角数字/字母/标点 -> 半角；并清掉 OCR 常见的私用区乱码。"""
    return _JUNK.sub('', (s or '').translate(_FW))


# 简体化（仅用于匹配出版社名）
_T2S_LIGHT = str.maketrans('書館學報廣東藝齋華務國義語經點叢屬歸讀寫爲與對輯廠業這圖錄發會',
                           '书馆学报广东艺斋华务国义语经点丛属归读写为与对辑厂业这图录发会')

_STOP_PUB = ('各', '这些', '这一', '官', '私营', '如', '由', '于', '在', '的',
             '及', '与', '等', '年', '第', '所', '其', '该', '和', '被', '设',
             '兼', '前', '后', '原', '旧', '私营')

_PUB_TAIL = r'(?:出版社|印书馆|书局|书社|出版公司|书店|出版集团)'

_PUBLINE = re.compile(r'出版社|印书馆|書局|书局|書社|书社|新华书店|出版发行|出版發行')
_VOLONLY = re.compile(r'^第?[一二三四五六七八九十百\d]{0,4}\s*[卷册集部编篇辑]$'
                      r'|^[（(]?[一二三四五六七八九十\d]{1,3}[）)]?$')
_TITLESKIP = re.compile(r'出版社|印书馆|書局|书局|書社|书社|新华书店|印刷|发行|發行|经销|經銷|'
                        r'定价|定價|印数|印數|印张|印張|开本|開本|字数|字數|插页|插頁|'
                        r'编|著|译|譯|室|会|會|院|系|大学|大學|研究所|公司|合编|编委|'
                        r'主编|校订|点校|整理|北京|上海|广州|南京|武汉|成都|'
                        r'中华民国|民国|历史|近代史|社会科学院')

# CIP 行：书名／责任者．—出版地：出版社，年
_CIP_RE = re.compile(
    r'([\u4e00-\u9fa5A-Za-z0-9·、（）()《》〔〕]{2,40})[／/]'
    r'([\u4e00-\u9fa5A-Za-z0-9·\s]{1,28}?)\s*'
    r'[.．,，]?\s*[-—－一.．]{1,3}\s*'
    r'([\u4e00-\u9fa5]{2,4}?)\s*[:：]?\s*'
    r'([\u4e00-\u9fa5]{2,20}?(?:出版社|印书馆|书局|书社|出版公司))'
    r'[，,]?\s*((?:19|20)\d{2})')

_STRONG = re.compile(r'定价|定價|统一书号|統一書號|ISBN|第[1１一]次印刷|印数|印數|印张|印張')

_TRAD_PAIRS = [('書', '书'), ('學', '学'), ('國', '国'), ('會', '会'), ('發', '发'),
               ('錄', '录'), ('圖', '图'), ('這', '这'), ('業', '业'), ('廠', '厂'),
               ('輯', '辑'), ('對', '对'), ('們', '们'), ('為', '为'), ('與', '与'),
               ('說', '说'), ('讀', '读'), ('寫', '写'), ('歸', '归'), ('屬', '属'),
               ('廣', '广'), ('語', '语'), ('叢', '丛'), ('經', '经'), ('點', '点')]

_NAME_STOP = ('出版', '发行', '印刷', '书店', '书局', '本社', '该', '其', '以上', '以下')
_INST_TAIL = re.compile(r'(研究室|委员会|编辑部|编委会|研究所|研究院|办公厅|办公室|'
                        r'大学|学院|出版社|图书馆|档案馆|博物馆|研究会|'
                        r'省委|市委|县委|部|局|社|馆|会|室|院|系|所|中心)$')
_ROLE_TAIL = r'(编著|主编|编选|选编|辑录|校注|校订|点校|纂|著|着|撰|编)'


def _valid_author(s):
    if not s or not (1 < len(s) <= 12):
        return False
    if any(t in s for t in ('出版社', '印书馆', '书局', '书社', '出版公司', '书店')):
        return False
    return bool(re.fullmatch(r'[\u4e00-\u9fa5·\s]{2,12}', s))


def _parse_cip(block):
    """优先从 CIP「图书在版编目」行抽 书名/作者/城市/出版社/年。"""
    m = _CIP_RE.search(block)
    if not m:
        return {}
    book, au, city, pub, yr = [x.strip() for x in m.groups()]
    if not _valid_author(au):
        au = ''
    return {'book': book, 'author': au, 'city': city, 'publisher': pub, 'year': yr}


def _locate_colophon(text):
    """滑窗找版权页：在关键词（定价/ISBN/印张…）最密集的窗口切出。"""
    lines = [l.rstrip() for l in text.splitlines()]
    W = 12
    w = []
    for l in lines:
        s = 0
        if 2 <= len(l) <= 80:
            if re.search(r'图书在版编目|CIP数据', l):
                s += 6
            if re.search(r'ISBN|统一书号|书号', l):
                s += 5
            if re.search(r'定价|定價', l):
                s += 5
            if re.search(r'第[1１一]版|第[1１一]次印刷', l):
                s += 4
            if re.search(r'出版|出版|發行|发行|印刷|印制', l):
                s += 3
            if re.search(r'印张|印張|开本|開本|字数|字數|印数|印數|插页|插頁', l):
                s += 3
            if re.search(r'出版社|印书馆|書局|书局|書社|书社', l):
                s += 2
        w.append(s)
    best, bi, best_strong = -1, 0, -1
    for i in range(len(lines)):
        seg = lines[i:i + W]
        strong = sum(1 for l in seg if _STRONG.search(l))
        tot = sum(w[i:i + W])
        if (strong, tot) > (best_strong, best):
            best_strong, best, bi = strong, tot, i
    if best <= 0:
        return '\n'.join(lines[:30] + ['…'] + lines[-50:])
    return '\n'.join(lines[max(0, bi - 22):bi + 24])


def _extract_title(block, text=''):
    """从版权页附近反推书名（优先取出版社行上方最近的像书名的一行）。"""
    lines = [l.strip() for l in block.splitlines()]

    def ok(s):
        if not (2 <= len(s) <= 22):
            return False
        if not re.search(r'[\u4e00-\u9fa5]', s):
            return False
        if _TITLESKIP.search(s) or _VOLONLY.match(re.sub(r'\s+', '', s)):
            return False
        if re.search(r'[。，；：！？、\u3000]$', s) or '。' in s or '，' in s:
            return False
        if re.search(r'(出版|發行|发行|印刷|印制|印行)$', s):
            return False
        if re.search(r'[0-9]{3,}', s):
            return False
        return True

    pubidx = [i for i, l in enumerate(lines) if _PUBLINE.search(l)]
    for pi in pubidx:
        for j in range(pi - 1, max(-1, pi - 16), -1):
            if ok(lines[j]):
                return lines[j]
    for l in lines:
        if ok(l):
            return l
    if text:
        from collections import Counter
        c = Counter(l.strip() for l in text.splitlines() if ok(l.strip()))
        if c and c.most_common(1)[0][1] >= 3:
            return c.most_common(1)[0][0]
    return ''


def _find_pub(block, pmap):
    """出版社（词典优先）。返回 (出版社, 城市, 置信度)。"""
    nb = block.translate(_T2S_LIGHT)
    LINE_PUB = re.compile(r'^[\u4e00-\u9fa5]{2,14}(?:出版社|印书馆|书局|书社|出版公司|书店)$')
    for l in nb.splitlines():
        s2 = l.strip()
        if LINE_PUB.match(s2) and s2 in pmap:
            return s2, pmap[s2], 0.95
    cands = [(k, nb.find(k)) for k in pmap if k in nb]
    if cands:
        cands.sort(key=lambda x: (-len(x[0]), -x[1]))
        return cands[0][0], pmap[cands[0][0]], 0.9
    cnt = {}
    for m in re.finditer(r'([\u4e00-\u9fa5]{2,12}?(?:出版社|印书馆|书局|书社|出版公司))', nb):
        s = m.group(1)
        if len(s) >= 4 and not s.startswith(_STOP_PUB):
            cnt[s] = cnt.get(s, 0) + 1
    if cnt:
        pub = max(cnt, key=lambda s: (cnt[s], len(s)))
        return pub, '', 0.5
    return '', '', 0.0


def _find_year(block, cip_year=''):
    m = re.search(r'((?:19|20)\d{2})\s*年?\s*\d{0,2}\s*月?\s*第[1１一]版', block)
    if m:
        return m.group(1), 0.85
    if cip_year:
        return cip_year, 0.7
    m = re.search(r'((?:19|20)\d{2})\s*年\s*\d{0,2}\s*月', block)
    if m:
        return m.group(1), 0.75
    m = re.search(r'((?:19|20)\d{2})\s*年[^\n]{0,20}?(?:出版|印刷)', block)
    if m:
        return m.group(1), 0.6
    m = re.search(r'出版[^\n]{0,20}?((?:19|20)\d{2})', block)
    if m:
        return m.group(1), 0.5
    return '', 0.0


def _author_from(text):
    """从一段文字里找「XX著 / XX编 / XX编著」这类责任者。"""
    if not text:
        return ''
    for m in re.finditer(r'([\u4e00-\u9fa5·]{2,16}(?:[、，,]+[\u4e00-\u9fa5·]{2,16}){0,3})'
                         r'\s*' + _ROLE_TAIL, text):
        raw, role = m.group(1), m.group(2)
        if role == '着':
            role = '著'
        names = [x for x in re.split(r'[、，,]+', raw) if x]
        keep = []
        for nm in names:
            if any(t in nm for t in _NAME_STOP):
                continue
            if _INST_TAIL.search(nm):
                keep.append(nm)
            elif 2 <= len(nm) <= 4 and _is_name(nm):
                keep.append(nm)
            elif 2 <= len(nm) <= 4 and m.end() >= len(text.rstrip(' 　．。；;，,、）)】]')):
                keep.append(nm)
        if keep:
            return ' '.join(keep) + role
    return ''


def _detect_trad(text):
    t = sum(text.count(a) for a, b in _TRAD_PAIRS)
    s = sum(text.count(b) for a, b in _TRAD_PAIRS)
    return (t / (t + s)) if (t + s) else 0.0


# ---- 四个来源 ----------------------------------------------------------

_TXT_SUFFIXES = ('_result', '_PD6AIFOCR', '_PD6AIOCR', '_PDVL6AIFOCR', '_PDVL6AIOCR',
                 '_PDV6AIFOCR', '_PDV6AIOCR', '_PDV5AIFOCR', '_PDV5AIOCR',
                 '_PD5AIFOCR', '_PD5AIOCR', '_PDL5AIFOCR', '_PDL5AIOCR',
                 '_【繁转简】', '_【简转繁】',
                 '_PD6AIFOCR_【繁转简】', '_PDVL6AIFOCR_【繁转简】',
                 '_PDV6AIFOCR_【简转繁】', '_PDV5AIFOCR_【简转繁】',
                 '_PDV5AIOCR_【简转繁】', '_PD6AIFOCR_【简转繁】')


def _read_head(path, limit=400000):
    """读文件头若干字节并解码（只读，绝不改写）。"""
    try:
        with open(path, 'rb') as f:
            raw = f.read(limit)
    except Exception:
        return ''
    for enc in ('utf-8-sig', 'utf-8', 'gb18030', 'big5'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')


def _folder_names(path):
    """从近到远列出各级父目录名（含盘符根的名字）。"""
    names = []
    d = os.path.dirname(os.path.abspath(path))
    while d:
        parent = os.path.dirname(d)
        nm = os.path.basename(d)
        if nm:
            names.append(nm)
        if parent == d:
            break
        d = parent
    return names


def _folder_meta(path):
    """把所在文件夹链当「文件名」解析（复用 _parse_file）：近的优先补空。"""
    out = {}
    for idx, nm in enumerate(_folder_names(path)):
        if not re.search(r'[\u4e00-\u9fa5]', nm):
            continue                     # 纯英文/随机目录名（Temp 等）不参与取名
        m = _parse_file(nm, path)
        take_name = (idx == 0) or bool(re.search(r'[（(：:《]', nm))
        if take_name and m.get('name') and 'name' not in out:
            out['name'] = _strip_vol_tail(m['name'])
        for f in ('author', 'publisher', 'year', 'volume'):
            if m.get(f) and f not in out:
                out[f] = m[f]
    return out


def _strip_vol_tail(s):
    """去掉书名尾巴上的（全N册）/（第N册）等，避免污染书名。"""
    return re.sub(r'[（(]\s*(?:全\s*[0-9一二三四五六七八九十]{1,3}\s*册|'
                  r'第\s*[0-9一二三四五六七八九十]{1,4}\s*[册卷集部编篇辑])\s*[）)]\s*$', '', s).strip()


_LOOSE_LIMIT = 6


def _loose_peers(path, exts, limit=_LOOSE_LIMIT):
    """兜底配对：族键再剥掉 6~10 位编号后的同伴。

    为了对上 `内蒙古文史资料 第10辑_OCR_opt.pdf` 与
    `内蒙古文史资料 第10辑_10077398_PD6AIOCR.txt` 这种**一侧多了个馆藏书号**的。
    护栏：命中数超过 limit 就不认 —— 那说明这串数字其实是卷号 / 年月号
    （《国务院公报》`gwyb198001`…`gwyb199912` 就是这样，636 期一视同仁）
    ，糊成一团比漏配更糟。
    """
    key0 = SSID_RE.sub('', family_key(os.path.basename(path)))
    if not key0:
        return []
    hits = []
    try:
        d = os.path.dirname(os.path.abspath(path))
        for fn in os.listdir(d):
            if fn.lower().endswith(exts) and SSID_RE.sub('', family_key(fn)) == key0:
                hits.append(os.path.join(d, fn))
    except OSError:
        return []
    return hits if 0 < len(hits) <= limit else []


def _sibling_texts(path):
    """同目录下同名/同书的文本变体（txt），非繁转简优先。"""
    d = os.path.dirname(os.path.abspath(path))
    stem = os.path.splitext(os.path.basename(path))[0]
    ext = os.path.splitext(path)[1].lower()
    found = []
    if ext == '.txt':
        found.append(os.path.abspath(path))
    for suf in _TXT_SUFFIXES:
        p = os.path.join(d, stem + suf + '.txt')
        if os.path.isfile(p) and p not in found:
            found.append(p)
    # 【v0.3.16】以前这里比的是 book_core，于是只差一个下划线 / 一个 `_opt`
    # 的同伴漏掉（见 family_key 的注释）。族键把这类差别都抹平了。
    try:
        key = family_key(os.path.basename(path))
        if key:
            for fn in os.listdir(d):
                if fn.lower().endswith('.txt') and family_key(fn) == key:
                    p = os.path.join(d, fn)
                    if p not in found:
                        found.append(p)
    except OSError:
        pass
    if not found:                      # v0.3.16：兜底 —— 一侧多带个馆藏书号
        for p in _loose_peers(path, ('.txt', '.text')):
            if p not in found:
                found.append(p)
    found.sort(key=lambda p: (1 if re.search(r'[繁简]转[繁简]', os.path.basename(p)) else 0))
    return found


def _sibling_pdfs(path):
    """同目录下同书的 PDF（当用户选的是 txt 时补读）。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.pdf':
        return [os.path.abspath(path)]
    d = os.path.dirname(os.path.abspath(path))
    out = []
    try:
        key = family_key(os.path.basename(path))     # v0.3.16：和对读同伴同一把尺子
        if key:
            for fn in os.listdir(d):
                if fn.lower().endswith('.pdf') and family_key(fn) == key:
                    out.append(os.path.join(d, fn))
    except OSError:
        pass
    if not out:                                      # v0.3.16：同样是兜底
        out = _loose_peers(path, ('.pdf',))
    return out


def _pdf_text(path, first=16, last=16):
    """只读 PDF 自带文字层（不做 OCR）；前 16 + 后 16 页，无版权页特征才扩大采样。

    batch16：大书（> FULL_SCAN_MAX 页）**不再全文扫描**（scanning a 1.8 GB PDF 全文要 8–10s，会卡界面）。
    """
    try:
        import fitz
    except Exception:
        return ''
    try:
        doc = fitz.open(path)
    except Exception:
        return ''
    try:
        n = doc.page_count
        idx = sorted(set(list(range(min(first, n))) + list(range(max(0, n - last), n))))
        txt = '\n'.join(doc[i].get_text() for i in idx)
        if not any(k in txt for k in ('ISBN', '出版', '定价', '印刷')):
            if n <= _FULL_SCAN_MAX:
                txt = '\n'.join(doc[i].get_text() for i in range(n))
            else:
                # 大书：扩大前后采样（不全文），已经足够找版权/CIP
                idx2 = sorted(set(range(min(60, n)) + range(max(0, n - 30), n)))
                txt = '\n'.join(doc[i].get_text() for i in idx2)
        return txt
    except Exception:
        return ''
    finally:
        try:
            doc.close()
        except Exception:
            pass


def _colophon_meta(text):
    """从一段文字（txt/PDF）里抽著录信息：优先 CIP 行，其次版权页窗口。"""
    if not text or not text.strip():
        return {}, ''
    t = _nw(text)
    block = _locate_colophon(t)
    cip = _parse_cip(block)
    pub, city, _ = _find_pub(block, _city_map())
    year, _ = _find_year(block, cip.get('year', ''))
    au = cip.get('author') or _author_from(block)
    title = cip.get('book') or _extract_title(block, t)
    d = {}
    if title:
        d['name'] = title
    if au:
        d['author'] = au
    if cip.get('publisher') or pub:
        d['publisher'] = cip.get('publisher') or pub
    if cip.get('city') or city:
        d['city'] = cip.get('city') or city
    if cip.get('year') or year:
        d['year'] = cip.get('year') or year
    return d, block


def _colophon_score(text):
    """给一页文本打「版权页」分：返回 (强特征行数, 关键词总分)。
    与 _locate_colophon 的滑窗计分同一套关键词，便于逐页定位版权页。"""
    strong = 0
    total = 0
    for l in (text or '').splitlines():
        if _STRONG.search(l):
            strong += 1
        if not (2 <= len(l) <= 80):
            continue
        s = 0
        if re.search(r'图书在版编目|CIP数据', l):
            s += 6
        if re.search(r'ISBN|统一书号|书号', l):
            s += 5
        if re.search(r'定价|定價', l):
            s += 5
        if re.search(r'第[1１一]版|第[1１一]次印刷', l):
            s += 4
        if re.search(r'出版|發行|发行|印刷|印制', l):
            s += 3
        if re.search(r'印张|印張|开本|開本|字数|字數|印数|印數|插页|插頁', l):
            s += 3
        if re.search(r'出版社|印书馆|書局|书局|書社|书社', l):
            s += 2
        total += s
    return strong, total


def find_colophon_page(path, front=20, back=15):
    """定位一本书的**版权页在 PDF 里的页码**（1 起；找不到返回 0）。

    默认只看「前 front 页 + 后 back 页」（版权页通常在这两段），逐页用
    _colophon_score 计分，取强特征行数/总分最高的那页。只读打开，绝不改文件。
    """
    try:
        import fitz
    except Exception:
        return 0
    if not path or not os.path.isfile(path):
        return 0
    try:
        doc = fitz.open(path)
    except Exception:
        return 0
    try:
        n = doc.page_count
        if n <= 0:
            return 0
        idxs = sorted(set(list(range(min(front, n))) +
                          list(range(max(0, n - back), n))))
        best = (-1, -1, -1)            # (强特征行数, 总分, 页序)
        for i in idxs:
            try:
                t = _nw(doc[i].get_text() or '')
            except Exception:
                continue
            strong, total = _colophon_score(t)
            if (strong, total) > (best[0], best[1]):
                best = (strong, total, i)
        if best[2] >= 0 and (best[0] >= 1 or best[1] >= 6):
            return best[2] + 1
        return 0
    finally:
        try:
            doc.close()
        except Exception:
            pass


def parse_deep(name, path=''):
    """四来源合并著录（优先级：文件名 > 文件夹链 > 同名TXT > PDF）。
    返回字段与 parse() 一致，另加 'city'（出版社→城市）与 'src'（各字段来源）。"""
    base = _parse_file(name, path)
    res = dict(base)
    res.setdefault('city', '')

    folder = _folder_meta(path) if path else {}

    txt_meta, colophon, txt_hit = {}, '', ''
    if path:
        for tp in _sibling_texts(path):
            m, blk = _colophon_meta(_read_head(tp))
            if m:
                txt_meta, colophon, txt_hit = m, blk, tp
                break

    pdf_meta = {}
    if path:
        for pp in _sibling_pdfs(path):
            if os.path.splitext(pp)[1].lower() != '.pdf':
                continue
            m, blk = _colophon_meta(_pdf_text(pp))
            if m:
                pdf_meta = m
                if not colophon:
                    colophon = blk
                break

    sources = [('file', base), ('folder', folder), ('txt', txt_meta), ('pdf', pdf_meta)]
    flds = ('name', 'volume', 'author', 'publisher', 'year', 'ssid', 'city')
    src = {}
    for f in flds:
        for sname, m in sources:
            v = m.get(f)
            if v:
                res[f] = v
                src[f] = sname
                break

    # 繁体：文件名/文件夹的【繁】标记 或 文本层繁体占比
    trad = bool(base.get('trad'))
    if not trad and path:
        if any(re.search(r'繁[体體]', nm) for nm in _folder_names(path)):
            trad = True
        elif _detect_trad(colophon) >= 0.5:
            trad = True
    res['trad'] = trad

    if not res.get('city') and res.get('publisher'):
        c = publisher_city(res['publisher'])
        if c:
            res['city'] = c
            src['city'] = 'dict'
    res['src'] = src
    res['colophon'] = colophon
    res['txt_src'] = txt_hit
    # 版权 / 授权信息：文件名 + 上级目录链 + 版权页 三来源合并
    try:
        rights, rsrc = _rights_scan(name, path, colophon)
    except Exception:
        rights, rsrc = {}, {}
    res['rights'] = rights
    res['rights_src'] = rsrc
    for _k in ('authorization', 'copyright_holder', 'copyright_line',
               'isbn', 'edition', 'printing', 'pub_date', 'rights_holder'):
        res.setdefault(_k, rights.get(_k, ''))
    return res


# ---- 版权 / 授权信息（batch7：文件名 + 上级目录 + 版权页 三来源合并）
_RIGHTS_PATS = [
    ('authorization', re.compile(
        r'(?:据|根據|根据|經|经)?\s*([\u4e00-\u9fa5A-Za-z0-9·（）()]{2,30}?)\s*'
        r'(?:獨家授權|独家授权|授權|授权)(?:影印|重印|出版|刊行|發行|发行)?')),
    ('copyright_holder', re.compile(
        r'(?:版權所有|版权所有|著作權所有|著作权所有|版權|版权|著作权|著作權)\s*[:：]?\s*'
        r'([\u4e00-\u9fa5A-Za-z0-9·（）()]{2,40})')),
    ('copyright_line', re.compile(r'(?:©|\(C\))\s*([\u4e00-\u9fa5A-Za-z0-9·（）()]{2,40})')),
    ('isbn', re.compile(r'ISBN\s*[:：]?\s*([0-9\-xX]{10,20})')),
    ('edition', re.compile(r'第\s*([0-9一二三四五六七八九十]{1,3})\s*版')),
    ('printing', re.compile(r'第\s*([0-9一二三四五六七八九十]{1,3})\s*次印刷')),
    ('pub_date', re.compile(r'((?:19|20)\d{2})\s*年\s*([0-9]{1,2})?\s*月?')),
    ('rights_holder', re.compile(r'(?:出版發行|出版发行|出版者|印刷發行|印刷发行)\s*[:：]?\s*'
                                 r'([\u4e00-\u9fa5]{2,25})')),
]


def _rights_meta(text):
    """从一段文本（文件名/目录名/版权页）抽版权与授权信息。"""
    d = {}
    if not text:
        return d
    t = _nw(text)
    for key, rx in _RIGHTS_PATS:
        m = rx.search(t)
        if not m:
            continue
        if key == 'isbn':
            v = re.sub(r'\s', '', m.group(1)).upper()
            if len(v) >= 10 and key not in d:
                d[key] = v
        elif key == 'pub_date':
            if key not in d:
                d[key] = m.group(1) + ('年%s月' % m.group(2) if m.group(2) else '年')
        else:
            v = (m.group(1) or '').strip(' :：')
            if v and key not in d and (len(v) >= 2 or key in ('edition', 'printing')):
                d[key] = v
    return d


def _rights_scan(name, path, colophon=''):
    """三来源合并：文件名 → 上级目录链 → 版权页。返回 (dict, 各字段来源)。"""
    out, src = {}, {}
    stems = [os.path.splitext(str(name or ''))[0]]
    if path:
        stems += _folder_names(path)
    for i, s in enumerate(stems):
        tag = 'file' if i == 0 else 'folder'
        for k, v in _rights_meta(s).items():
            if v and k not in out:
                out[k] = v
                src[k] = tag
    m = _rights_meta(colophon)
    for k, v in m.items():
        if v and not out.get(k):
            out[k] = v
            src[k] = 'colophon'
    return out, src


# ---- 多版本聚合
def group(parsed):
    """把同一本书的多个版本聚成一组：{key: [条目...]}（key = 书名 + 卷册）"""
    g = {}
    for p in parsed:
        k = (p.get('name') or p.get('raw'), p.get('volume') or '')
        g.setdefault(k, []).append(p)
    return g


def versions_of(parsed, name, volume=''):
    """取同一本书的所有版本，按「PDF 原本 → 繁转简 TXT → 其它」排序。"""
    o = [p for p in parsed if (p.get('name') == name and p.get('volume', '') == volume)]
    order = {'.pdf': 0, '.txt': 1}
    return sorted(o, key=lambda p: (1 if p.get('tail') else 0,
                                    order.get(p.get('ext'), 2), p.get('path', '')))


# ---- 学术引用
def cite(meta, page='X'):
    """格式：作者：《书名》第N册，出版地：出版社，年，第X页。"""
    name = meta.get('name') or ''
    vol = meta.get('volume') or ''
    au = meta.get('author') or ''
    pub = meta.get('publisher') or ''
    city = meta.get('city') or (publisher_city(pub) if pub else '')
    parts = []
    if au:
        parts.append(au)
    parts.append('：《%s》%s' % (name, '' if (vol and vol.strip('（）()') in name) else vol))
    if pub or city or meta.get('year'):
        tail = '%s：%s，%s年' % (city or '', pub or '', meta.get('year') or '')
        tail = tail.replace('：，', '：').replace('：年', '').replace('，年', '年')
        parts.append('，' + tail.strip('，'))
    parts.append('，第%s页。' % (page or 'X'))
    s = ''.join(parts)
    s = s.replace('， ，', '，').replace('，：', '：').replace('，%s年' % '', '')
    return re.sub(r'，+', '，', s).replace('，。', '。')


def _selftest_deep(ok):
    """深度著录 5 个用例：自造临时文件，跑完即删。"""
    import tempfile
    import shutil
    try:
        import fitz
        try:
            fitz.TOOLS.mupdf_display_errors(False)
        except Exception:
            pass
    except Exception:
        pass
    root = tempfile.mkdtemp(prefix='vmtest_')
    try:
        # ① 只有文件夹名
        d1 = os.path.join(root, '李泽厚：美的历程（全1册）（北京 文物出版社1981年）')
        os.makedirs(d1, exist_ok=True)
        f1 = os.path.join(d1, '10342277_PD6AIFOCR.pdf')
        open(f1, 'wb').close()
        p1 = parse_deep('10342277_PD6AIFOCR.pdf', f1)
        ok(p1.get('author') == '李泽厚' and p1.get('publisher') == '文物出版社'
           and p1.get('year') == '1981' and p1.get('volume') == '全1册'
           and p1.get('city') == '北京' and p1['src'].get('publisher') == 'folder',
           '①只有文件夹名 → pub=%s year=%s au=%s vol=%s city=%s'
           % (p1.get('publisher'), p1.get('year'), p1.get('author'),
              p1.get('volume'), p1.get('city')))

        # ② 只有文件名
        f2 = os.path.join(root, '续伪书通考_郑良树编著_台湾学生书局1984年.pdf')
        open(f2, 'wb').close()
        p2 = parse_deep('续伪书通考_郑良树编著_台湾学生书局1984年.pdf', f2)
        ok(p2.get('publisher') == '台湾学生书局' and p2.get('year') == '1984'
           and p2.get('author') == '郑良树' and p2.get('city') == '台北'
           and p2['src'].get('publisher') == 'file',
           '②只有文件名 → pub=%s year=%s au=%s city=%s'
           % (p2.get('publisher'), p2.get('year'), p2.get('author'), p2.get('city')))

        # ③ 同名 _result.txt 里有 CIP 行
        f3 = os.path.join(root, '美的历程_10502401_PD6AIFOCR.pdf')
        t3 = os.path.join(root, '美的历程_10502401_PD6AIFOCR_result.txt')
        open(f3, 'wb').close()
        with open(t3, 'w', encoding='utf-8') as fh:
            fh.write('图书在版编目（CIP）数据\n'
                     '美的历程／李泽厚著．—北京：文物出版社，1981.3\n'
                     'ISBN 7-5010-0000-0\n定价：2.00元\n')
        p3 = parse_deep('美的历程_10502401_PD6AIFOCR.pdf', f3)
        ok(p3.get('publisher') == '文物出版社' and p3.get('year') == '1981'
           and '李泽厚' in (p3.get('author') or '') and p3.get('city') == '北京'
           and p3['src'].get('publisher') == 'txt',
           '③同名_result.txt CIP → pub=%s year=%s au=%s city=%s'
           % (p3.get('publisher'), p3.get('year'), p3.get('author'), p3.get('city')))

        # ④ 合成 PDF 文本层里有版权页
        f4 = os.path.join(root, '22222222_PD6AIFOCR.pdf')
        made4 = False
        try:
            import fitz
            _fp = r'C:\Windows\Fonts\msyh.ttc'
            if not os.path.isfile(_fp):
                _fp = r'C:\Windows\Fonts\simsun.ttc'
            doc = fitz.open()
            pg = doc.new_page()
            pg.insert_text((60, 70),
                           '图书在版编目（CIP）数据\n'
                           '十五至十八世纪的物质文明／布罗代尔著．—北京：商务印书馆，1992.11\n'
                           'ISBN 7-100-01234-5\n定价：38.00元',
                           fontsize=11, fontname='cjk', fontfile=_fp)
            doc.save(f4)
            doc.close()
            made4 = True
        except Exception:
            made4 = False
        if made4:
            p4 = parse_deep('22222222_PD6AIFOCR.pdf', f4)
            ok(p4.get('publisher') == '商务印书馆' and p4.get('year') == '1992'
               and p4.get('city') == '北京' and p4['src'].get('publisher') == 'pdf',
               '④合成PDF版权页 → pub=%s year=%s city=%s name=%s'
               % (p4.get('publisher'), p4.get('year'), p4.get('city'), p4.get('name')))
        else:
            ok(False, '④合成PDF版权页 → 无法生成 PDF（fitz 不可用）')

        # ⑤ 合并优先级：folder 补 file 的空缺，file 的字段不被 folder 覆盖
        d5 = os.path.join(root, '旧书名（北京 商务印书馆1992年）')
        os.makedirs(d5, exist_ok=True)
        f5 = os.path.join(d5, '十五至十八世纪的物质文明 第1卷_14296873_layered.pdf')
        open(f5, 'wb').close()
        p5 = parse_deep('十五至十八世纪的物质文明 第1卷_14296873_layered.pdf', f5)
        ok('十五至十八' in (p5.get('name') or '') and p5.get('volume') == '第1卷'
           and p5.get('publisher') == '商务印书馆' and p5.get('year') == '1992'
           and p5.get('city') == '北京'
           and p5['src'].get('name') == 'file' and p5['src'].get('publisher') == 'folder',
           '⑤合并优先级 → name=%s vol=%s pub=%s year=%s src=%s'
           % (p5.get('name'), p5.get('volume'), p5.get('publisher'), p5.get('year'),
              {k: p5['src'].get(k) for k in ('name', 'publisher', 'year', 'city')}))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _selftest_rights(ok):
    """版权/授权信息抽取：文件名、上级目录、版权页三来源。"""
    import tempfile
    import shutil
    m = _rights_meta('史记_中华书局授权影印本')
    ok(bool(m.get('authorization')), '版权：文件名「X授权」→ %s' % m.get('authorization'))
    m2 = _rights_meta('据商务印书馆授权重印_民国丛书')
    ok(bool(m2.get('authorization')), '版权：文件名「据X授权」→ %s' % m2.get('authorization'))
    col = ('本书由中华书局授权出版发行\n'
           '版权所有：中华书局\nISBN 978-7-101-00000-1\n'
           '1998年3月第1版，1998年3月第1次印刷')
    m3 = _rights_meta(col)
    ok(bool(m3.get('isbn')) and m3['isbn'].startswith('978'), '版权：ISBN → %s' % m3.get('isbn'))
    ok(bool(m3.get('edition')) and bool(m3.get('printing')),
       '版权：版次/印次 → %s / %s' % (m3.get('edition'), m3.get('printing')))
    ok(bool(m3.get('copyright_holder')) or bool(m3.get('authorization')),
       '版权：版权所有主体 → %s' % (m3.get('copyright_holder') or m3.get('authorization')))
    root = tempfile.mkdtemp(prefix='vmr_')
    try:
        sub = os.path.join(root, '某大学图书馆藏_授权影印')
        os.makedirs(sub, exist_ok=True)
        fake = os.path.join(sub, '癸巳类稿.pdf')
        with open(fake, 'wb') as f:
            f.write(b'%PDF-1.4\n')
        rr, src = _rights_scan('癸巳类稿.pdf', fake, col)
        ok(bool(rr.get('authorization')) and bool(rr.get('isbn')),
           '版权：文件+目录+版权页三来源合并 → %s src=%s' % (rr, src))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def selftest():
    import sys
    cases = [
        ('孙中山全集 第三册_10490188_PD6AIFOCR.pdf',
         {'volume': '第三册', 'ssid': '10490188'}),
        ('鲒埼亭集_PDVL6AIFOCR_【繁转简】.txt', {'tail': '_【繁转简】'}),
        ('第3辑外交_PDV5AIFOCR.txt', {'name': '第3辑外交', 'volume': '第3辑'}),
        ('第3辑外交_PDV5AIFOCR_【简转繁】.txt', {'name': '第3辑外交'}),
        ('第3辑外交_OCR_PD5AIOCR.pdf', {'name': '第3辑外交'}),
        ('某书_PDV6AIFOCR_【繁转简】.txt', {'tail': '_【繁转简】'}),
        ('中国伪书综考（全1册）（邓瑞全 王冠英编著 合肥 黄山书社1998年）.pdf',
         {'volume': '全1册'}),
        ('（可打印版）金翼英文版扫描版-全篇_unlocked_PD6AIOCR.pdf', {}),
        ('近代中国史料丛刊三辑 0427 冯宫保（子材）军牍集要（一）_PD6AIFOCR.pdf',
         {'volume': '（一）'}),
        ('布罗代尔：十五至十八世纪的物质文明 第1卷_14296873_layered.pdf',
         {'ssid': '14296873', 'volume': '第1卷'}),
        ('续伪书通考_郑良树编著_台湾学生书局1984年.pdf',
         {'author': '郑良树', 'publisher': '台湾学生书局', 'year': '1984'}),
    ]
    log = []

    def ok(c, m):
        log.append(('OK  ' if c else 'FAIL') + ' ' + m)

    for fn, want in cases:
        p = parse(fn)
        bad = [k for k, v in want.items() if p.get(k) != v]
        ok(not bad, '%s → %s%s' % (fn[:34], p.get('name'),
                                   '' if not bad else ' ✗ %s' % bad))
    # 同一本书多版本聚合
    files = ['布罗代尔：十五至十八世纪的物质文明 第1卷_14296873_layered.pdf',
             '布罗代尔：十五至十八世纪的物质文明 第1卷_14296873_【繁转简】.txt',
             '布罗代尔：十五至十八世纪的物质文明 第1卷_PD6AIFOCR.pdf']
    ps = [parse(f) for f in files]
    g = group(ps)
    ok(len(g) == 1, '多版本聚合：3 个文件聚成 1 本 → %d 组' % len(g))
    vs = versions_of(ps, ps[0]['name'], ps[0]['volume'])
    ok(len(vs) == 3 and vs[0]['ext'] == '.pdf', '版本排序：PDF 原本排第一 → %s'
       % [v['ext'] for v in vs])
    c = cite(ps[2], page='27')
    ok(c.startswith('布罗代尔：《十五至十八世纪的物质文明') and c.endswith('第27页。'),
       '引用：%s' % c)
    # batch19：PDV5/PDV6 后缀也能归到同一本（用户报的 BUG）
    fam = [(n, book_core(n), family_key(n), same_name_key(n)) for n in (
        '第3辑外交_OCR.txt',
        '第3辑外交_OCR_PD5AIOCR.pdf',
        '第3辑外交_OCR_PD5AIOCR.txt',
        '第3辑外交_PDV5AIFOCR.txt',
        '第3辑外交_PDV5AIFOCR_【简转繁】.txt',
        'X_PDV6AIFOCR.pdf',
        'X_PDV6AIOCR_【繁转简】.txt',
        'X_PDVL6AIFOCR.txt',
    )]
    cores = set(x[1] for x in fam if x[0].startswith('第3辑外交'))
    ok(cores == {'第3辑外交'}, 'PDV5/PD6 后缀：书主干一致 → %s' % cores)
    fams = set(x[2] for x in fam if x[0].startswith('第3辑外交'))
    ok(fams == {'第3辑外交'}, 'PDV5/PD6 后缀：族键一致 → %s' % fams)
    fams6 = set(x[2] for x in fam if x[0].startswith('X_'))
    ok(fams6 == {'x'}, 'PDV6/PDVL6 后缀：族键一致 → %s' % fams6)
    _selftest_deep(ok)
    _selftest_rights(ok)
    txt = '\n'.join(log) + '\nresult = %s\n' % (
        'OK' if all(l.startswith('OK') for l in log) else 'FAIL')
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    print(txt)


if __name__ == '__main__':
    selftest()
