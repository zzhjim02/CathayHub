# -*- coding: utf-8 -*-
"""CathayHub · 检索词扩展（繁简通搜 + 联想词）—— 共享权威实现  v1.0

一个检索词该变成哪些写法一起去搜，这件事 CathaySearch（全库检索）和
CathayViewer（单本书里查找）必须给出**一模一样**的答案：
书库里搜「吴佩孚」带上了「吴玉帅」，点进书里却只认「吴佩孚」三个字，
那叫同一套软件自己打自己。

所以规则只在这里写一份。两个程序各带一份**逐字相同**的副本：

    shared/query_expand.py                     ← 权威（改这里）
    apps/CathayViewer/viewer_query.py          ← 副本
    apps/CathaySearch/cathaysearch/query_expand.py  ← 副本

改完权威就跑 `python tools/sync_shared.py --push`；`--check` 只体检不改。

设计约定：本文件**不 import 任何 app 的东西**（不认 PyQt、不认各自的配置），
CSV 的位置与公共目录由调用方 `set_csv_paths()` / `set_public_dir()` 注入 ——
只有这样才能保证两份副本一字不差。
"""
import os
import json
import threading

__all__ = [
    'convert_text', 'convert_many', 'variants_of',
    'Table', 'csv_path', 'set_csv_paths', 'load_table', 'reload',
    'set_extra_csv_dirs', 'extra_dir', 'load_extra', 'extra_tables',
    'source_names', 'set_source_enabled', 'source_enabled',
    'forms_of', 'block_key', 'canon_group', 'related', 'expand',
    'public_dir', 'set_public_dir', 'migrate_legacy',
    'user_path', 'blocked_path', 'user_aliases', 'set_user_aliases', 'of',
    'blocked', 'set_blocked', 'block_session', 'clear_session',
]

# ═══════════════════════ 一、繁简（OpenCC，跟 CathayShelf 同规则）═══════════════════════

_CC = {}
_CC_LOCK = threading.Lock()

# 一个简体字常对应不止一个繁体字形（启 → 啓 / 啟），所以不是"简↔繁"两条路，
# 而是四个 OpenCC 配置轮着套。CBDB 那张别名表就是混着写的：`梁启超` 一行、
# `梁啟超` 一行，收的别名还不一样多 —— 少认一个字形就少一半存货。
_CONV_CFGS = ('t2s', 's2t', 's2tw', 's2hk')


def _cc(cfg):
    if cfg not in _CC:
        with _CC_LOCK:
            if cfg not in _CC:
                import opencc
                _CC[cfg] = opencc.OpenCC(cfg)
    return _CC[cfg]


def convert_text(text, act):
    """act: 't2s'（繁→简）/ 's2t'（简→繁）/ 's2tw' / 's2hk'。优先 OpenCC，退回 zhconv。"""
    text = text or ''
    try:
        return _cc(act).convert(text)
    except Exception:
        try:
            import zhconv
            return zhconv.convert(text, 'zh-cn' if act == 't2s' else 'zh-tw')
        except Exception:
            return text


def convert_many(words, act):
    """一串词一起转，保持顺序、去掉转换后变空的项。"""
    out = []
    for w in words or []:
        w2 = convert_text(str(w or '').strip(), act)
        if w2 and w2 not in out:
            out.append(w2)
    return out


def variants_of(word):
    """一个词的全部繁/简变体，**不含自身**；繁简同形返回空表。

    做法：四个方向的转换反复套到不再冒出新词为止（最多三轮）。
    好处是**对称** —— 无论从简体还是繁体出发，得到的都是同一组字形，
    绝不会出现"打简体漏掉繁体那行"的事。
    """
    w = str(word or '').strip()
    if not w:
        return []
    out = []
    src = [w]
    for _ in range(3):
        grown = False
        for x in src:
            for cfg in _CONV_CFGS:
                v = convert_text(x, cfg)
                v = (v or '').strip()
                if v and v != w and v not in out:
                    out.append(v)
                    grown = True
        if not grown:
            break
        src = [x for x in [w] + out]
    return out


# ═══════════════════════ 二、CBDB 别名表 ═══════════════════════

_SEPS = ['，', ',', '、', '；', ';', '/', '|']


def _split(s):
    out, cur = [], ''
    for ch in s or '':
        if ch in _SEPS:
            if cur.strip():
                out.append(cur.strip())
            cur = ''
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


class Table(object):
    """人名别名表：正名 ↔ 字号 / 笔名 / 化名 / 绰号。

    CBDB 那张表是**按字形分行**的：同一个人常常既有一行简体
    `吴佩孚,子玉,吴玉帅`，又有一行繁体 `吳佩孚,子玉`，两行收的别名还不一样多。
    只查其中一行 = 必然漏掉另一行的存货。所以所有查询都先铺开字形再合并。
    """

    def __init__(self, to_canon=None, aliases=None, source=''):
        self.to_canon = to_canon or {}
        self.aliases = aliases or {}
        self.source = source or ''

    @classmethod
    def from_lines(cls, lines, source=''):
        to_canon, aliases = {}, {}
        for ln in lines or []:
            ln = (ln or '').strip()
            if not ln or ln.startswith('#'):
                continue
            parts = _split(ln)
            if len(parts) < 2:
                continue
            canon = parts[0]
            alist = aliases.setdefault(canon, [])
            to_canon[canon] = canon
            for a in parts[1:]:
                if a != canon:
                    if a not in alist:
                        alist.append(a)
                    to_canon.setdefault(a, canon)
        return cls(to_canon, aliases, source)

    @classmethod
    def load(cls, path):
        if not path or not os.path.isfile(path):
            return cls()
        try:
            with open(path, 'r', encoding='utf-8-sig') as f:
                return cls.from_lines(f.read().splitlines(), path)
        except Exception:
            return cls()

    def canonical(self, word):
        return self.to_canon.get(str(word or '').strip(), str(word or '').strip())

    def known(self, word):
        return str(word or '').strip() in self.to_canon

    def groups(self):
        return len(self.aliases)


_CSV_PATHS = []
_TABLE = None
_LOCK = threading.Lock()


def set_csv_paths(paths):
    """注入别名表候选位置（按优先顺序），由各 app 启动时调用一次。"""
    global _CSV_PATHS, _TABLE
    _CSV_PATHS = [p for p in (paths or []) if p]
    _TABLE = None


def csv_path():
    for p in _CSV_PATHS:
        if p and os.path.isfile(p):
            return p
    return ''


def load_table(force=False):
    global _TABLE
    if _TABLE is not None and not force:
        return _TABLE
    with _LOCK:
        if _TABLE is None or force:
            _TABLE = Table.load(csv_path())
    return _TABLE


def reload():
    """表文件换过了（用户导入新别名）→ 重新读一遍。"""
    return load_table(force=True)


# ════════════════════ 二之二、补充异名表（地名 / 机构 / 译名 / 人物）════════════════════
#
# CBDB 只管人名（字号别称）。可史料里的「北平 / 北京」「中研院 / 中央研究院」
# 「沙士比亚 / 莎士比亚」同样是同实异名 —— 这四张小表就是补它的。
#
# 跟主表的关系是**补充，不是改写**：
#   · 主表（CBDB）一个字节都不动，读法跟以前完全一样；
#   · 补充表走的是**同一套** _canon_group_one()（含跨字形归并），
#     所以繁简通搜对新数据自动生效 —— 繁简那段代码一个字都不用改；
#   · 结果取**并集**：主表给它的，补充表给它的，合在一起返回。
#     两边若是同一实体（如康熙），并集正好把 CBDB 缺的那几个别名补上。
#
# 每张表一个文件，文件名主干就是「来源名」（place / org / foreign / figure），
# 来源可以单独开关 —— 嫌地名串味就把 place 关掉，别的照用。

_EXTRA_DIRS = []
_EXTRA_TABLES = None
_SOURCE_ON = {}          # {来源名: 是否启用}；没登记过的来源默认启用


def set_extra_csv_dirs(dirs):
    """注入补充表所在目录（按优先顺序，第一个存在的生效）。"""
    global _EXTRA_DIRS, _EXTRA_TABLES
    _EXTRA_DIRS = [d for d in (dirs or []) if d]
    _EXTRA_TABLES = None


def extra_dir():
    for d in _EXTRA_DIRS:
        if d and os.path.isdir(d):
            return d
    return ''


def source_names():
    """补充表里都有哪些来源（= 文件名主干，不含扩展名）。"""
    d = extra_dir()
    if not d:
        return []
    try:
        return sorted(os.path.splitext(f)[0]
                      for f in os.listdir(d) if f.lower().endswith('.csv'))
    except Exception:
        return []


def set_source_enabled(name, on, save=False):
    """开关某个来源。关掉的来源整张表都不参与扩展。

    save=True 才写盘（写到两边共用的公共目录）—— 自检和临时改动别带这个参数，
    免得把用户的配置改掉。
    """
    global _EXTRA_TABLES
    name = str(name or '').strip()
    if not name:
        return
    if _SOURCE_ON.get(name) != bool(on):
        _EXTRA_TABLES = None      # 开关变了，已加载的表作废，下次重读
    _SOURCE_ON[name] = bool(on)
    if not save:
        return
    try:
        p = source_config_path()
        _ensure_dir(os.path.dirname(p))
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(_SOURCE_ON, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def source_enabled(name):
    return bool(_SOURCE_ON.get(str(name or '').strip(), True))


def source_config_path():
    """来源开关存哪儿 —— **两边共用的公共目录**。

    跟用户自攒的联想词一个道理：在检索里关掉地名来源，进到书里查找也该是关的。
    各存各的 settings 就会两边不一致。
    """
    return os.path.join(public_dir(), 'alias_sources.json')


def load_source_config():
    """启动时从公共目录把来源开关读回来。文件不存在 = 全部启用。"""
    p = source_config_path()
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    global _EXTRA_TABLES
    for k, v in data.items():
        k = str(k or '').strip()
        if k:
            _SOURCE_ON[k] = bool(v)
    _EXTRA_TABLES = None          # 读回来就按新开关重读
    return dict(_SOURCE_ON)


def load_extra(force=False):
    """读出所有**已启用**的补充表，返回 [Table, ...]。"""
    global _EXTRA_TABLES
    if _EXTRA_TABLES is not None and not force:
        return _EXTRA_TABLES
    with _LOCK:
        if _EXTRA_TABLES is None or force:
            d = extra_dir()
            out = []
            if d:
                for fn in sorted(os.listdir(d)):
                    if not fn.lower().endswith('.csv'):
                        continue
                    name = os.path.splitext(fn)[0]
                    if not source_enabled(name):
                        continue
                    t = Table.load(os.path.join(d, fn))
                    if t.to_canon:
                        t.source = name
                        out.append(t)
            _EXTRA_TABLES = out
    return _EXTRA_TABLES


def extra_tables():
    return load_extra()


def forms_of(word, variant=True):
    """一个词的全部繁简字形（含自身），**按字符序排序**。

    排序是关键：同一组字形成员跟用哪个字形起头无关，排出来永远同一串，
    后面的合并结果才不会因为"你打的是简体还是繁体"而变个样子。
    """
    w = str(word or '').strip()
    if not w:
        return []
    out = [w]
    if variant:
        try:
            for v in variants_of(w):
                if v not in out:
                    out.append(v)
        except Exception:
            pass
    return sorted(out)


def block_key(word):
    """同一个"人"的统一记账 key：拿它全部字形里排最前的一个。

    屏蔽（"别再给我这个联想词"）必须对简繁两个字形同时生效 ——
    在简体下删掉的东西，切到繁体又冒出来那叫没删干净。
    """
    fs = forms_of(word)
    return fs[0] if fs else str(word or '').strip()


def _canon_group_one(word, table, variant=True):
    """在一张表里查一个词 —— canon_group 的内核，主表和补充表都走这一条。

    单独抽出来是为了让补充表**完完整整复用**主表的口径（含跨字形归并），
    而不是另写一套。改这里等于同时改两边。
    """
    forms = forms_of(word, variant)
    canons = []
    for f in forms:
        c = table.to_canon.get(f)
        if c and c not in canons:
            canons.append(c)
    if variant:
        extra = []
        try:
            for c in list(canons):
                for cv in variants_of(c):
                    c2 = table.to_canon.get(cv)
                    if c2 and c2 not in canons and c2 not in extra:
                        extra.append(c2)
        except Exception:
            pass
        canons = sorted(set(canons) | set(extra))
    group = []
    for c in canons:
        for a in table.aliases.get(c, []):
            if a not in group:
                group.append(a)
    return forms, canons, group


def canon_group(word, table=None, variant=True):
    """返回 (forms, canons, aliases)：全部字形 / 全部正名 / 别名并集。

    输入词 → 全部字形 → 各自的正名 → 正名再换字形补一轮
          → 把所有这些行的别名并成一个集合。
    返回的三个序列都排好序、去重，跟"你打的是哪个字形"完全无关。

    **传了 table 就只用那一张表**（自检 / 隔离测试用这个），不传才会
    主表 + 补充表一起查、结果取并集。老调用者的行为因此一点没变。
    """
    if table is not None:
        return _canon_group_one(word, table, variant)
    table = load_table()
    forms, canons, group = _canon_group_one(word, table, variant)

    ex_canons, ex_group, hit = [], [], False
    for t in extra_tables():
        # 空目录 / 全关掉时 extra_tables() 返回 []，这里就是空转，零开销
        _f, c2, g2 = _canon_group_one(word, t, variant)
        if c2:
            hit = True
        for x in c2:
            if x not in ex_canons:
                ex_canons.append(x)
        for x in g2:
            if x not in ex_group:
                ex_group.append(x)
    if not hit:
        return forms, canons, group

    # 补充表认得这个词，而主表只把它当**别人的别名** → 以补充表为准。
    # 中文里地名/尊称被拿去当字号太常见了：「北平」是清代人溫紹原的号，
    # 「长安」是朱永通的号 —— 不加这条，搜「北平」会把溫紹原、北屏、壯勇
    # 一起端出来。补充表是人工核准过的，撞车时信它。
    # 反过来，主词在主表里**就是正名**（如康熙、孙中山）则两边合并 ——
    # 那时是同一实体，并集正好把 CBDB 缺的别名补上。
    if canons and word not in canons:
        return forms, ex_canons, ex_group

    for x in ex_canons:
        if x not in canons:
            canons.append(x)
    for x in ex_group:
        if x not in group:
            group.append(x)
    return forms, canons, group


def related(word, limit=None, include_self=True, variant=True, block=True,
            table=None, session=True):
    """返回与 word 关联的称呼（正名 + 全部别名，跨简繁字形合并）。

    include_self=True 时把 word 本身放进首位（检索表达式需要主词）；
    limit 只掐"别名"这一段（字形与正名不在其列）；
    block=True 时剔除用户明确不要的词。
    """
    if not word or not str(word).strip():
        return []
    w = str(word).strip()
    # table 保持 None 往下传：canon_group 见到 None 才会「主表 + 补充表」一起查。
    # 这里要是图省事先 load_table() 填进去，补充表就永远轮不上了。
    forms, canons, group = canon_group(w, table, variant)
    if limit:
        group = group[:int(limit)]
    out = []
    if include_self:
        out.append(w)
    # ① 别的字形：搜繁体书库得用繁体词，缺一个字形就缺一半命中
    for f in forms:
        if f != w and f not in out:
            out.append(f)
    # ② 正名（多数时候就等于某个字形，重了就跳过）
    for c in canons:
        if c != w and c not in out:
            out.append(c)
    # ③ 别名：用户自己攒的排在前面 —— 那是他明确点名要搜的，比表里推出来的更可信
    for a in of(w):
        if a != w and a not in out:
            out.append(a)
    for a in group:
        if a != w and a not in out:
            out.append(a)
    if block:
        bad = set(blocked(w, session=session))
        if bad:
            out = [x for x in out if x == w or x not in bad]
    return out


def expand(word, use_variant=True, use_alias=True, limit=None, table=None,
           block=True):
    """【统一入口】一个检索词 → 该一并去搜的词表（主词永远在第 1 位）。

    use_variant：繁简通搜（别的字形）
    use_alias  ：联想词（CBDB 字号 / 笔名 / 化名 + 用户自己攒的）
    两个都关 = 只认原词，跟老版本"文内查找"一个口径。
    """
    w = str(word or '').strip()
    if not w:
        return []
    if not use_variant and not use_alias:
        return [w]
    if use_alias:
        return related(w, limit=limit, include_self=True,
                       variant=bool(use_variant), block=block, table=table)
    out = [w]
    if use_variant:
        for v in variants_of(w):
            if v and v not in out:
                out.append(v)
        if block:
            bad = set(blocked(w))
            if bad:
                out = [x for x in out if x == w or x not in bad]
    return out


# ═══════════════════════ 三、用户自己攒的 / 不要的联想词 ═══════════════════════
# 这两个表 CathaySearch 和 CathayViewer **共用同一份**（放在公共目录）：
# 在检索里加过的字号，进到书里查找就该照样认 —— 反过来也一样。
# CBDB 那份大表是只读的公共数据，一个字节都不改；这里是"加法"和"减法"两张小表。

_PUBLIC_DIR = None
_SESSION = {}          # 只活本次运行的临时屏蔽 {block_key: set(词)}


def _appdata_base():
    env = os.environ.get('CATHAYHUB_HOME', '').strip()
    if env:
        return env
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'CathayHub')


def public_dir():
    """两边共用的数据目录（默认 %APPDATA%\\CathayHub，可用 CATHAYHUB_HOME 改）。"""
    global _PUBLIC_DIR
    if _PUBLIC_DIR:
        return _PUBLIC_DIR
    return _appdata_base()


def set_public_dir(path):
    global _PUBLIC_DIR
    _PUBLIC_DIR = str(path or '').strip() or None


def _ensure_dir(d):
    try:
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
    except Exception:
        pass


def user_path():
    return os.path.join(public_dir(), 'user_alias.json')


def blocked_path():
    return os.path.join(public_dir(), 'user_alias_blocked.json')


def migrate_legacy(legacy_paths):
    """老版本把用户表写在各自程序目录/runtime 下 —— 搬到公共目录，只搬一次。

    传 [(旧用户表, 旧屏蔽表), ...]，按序尝试；公共目录里已经有了就什么也不做。
    老文件**不删**（万一别的版本还在用）。
    """
    moved = []
    try:
        pairs = ((user_path(), 0), (blocked_path(), 1))
        for new, idx in pairs:
            if os.path.isfile(new):
                continue
            for item in (legacy_paths or []):
                try:
                    old = item[idx]
                except Exception:
                    continue
                if old and os.path.isfile(old):
                    try:
                        _ensure_dir(os.path.dirname(new))
                        with open(old, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        with open(new, 'w', encoding='utf-8') as f:
                            json.dump(data, f, ensure_ascii=False, indent=1)
                        moved.append((old, new))
                        break
                    except Exception:
                        continue
    except Exception:
        pass
    return moved


def _read_json(path):
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for k, v in data.items():
        k = str(k or '').strip()
        if not k:
            continue
        lst = [str(x).strip() for x in (v or []) if str(x or '').strip()]
        if lst:
            out[k] = lst
    return out


def _write_json(path, data):
    _ensure_dir(os.path.dirname(path))
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def user_aliases():
    """{主词: [别名...]}"""
    return _read_json(user_path())


def set_user_aliases(word, aliases):
    """写用户别名表（永久生效就靠它）。传空列表 = 删掉这个词。"""
    word = str(word or '').strip()
    if not word:
        return False, '主词是空的'
    data = user_aliases()
    lst = [str(x).strip() for x in (aliases or []) if str(x or '').strip()]
    if lst:
        data[word] = lst
    else:
        data.pop(word, None)
    try:
        _write_json(user_path(), data)
        return True, ('已记住「%s」的 %d 个联想词（下次打开还在）'
                      % (word, len(lst)) if lst else '已清掉「%s」的联想词' % word)
    except Exception as e:
        return False, '写不进去：%s' % e


def of(word):
    """这个词在**用户表**里记了哪些联想词（没有就是 []）。"""
    return list(user_aliases().get(str(word or '').strip(), []))


def blocked(word, session=True):
    """这个词里用户明确"不要"的那几个联想词。"""
    w = str(word or '').strip()
    if not w:
        return []
    out = list(_read_json(blocked_path()).get(block_key(w), []))
    if session:
        out += [x for x in _SESSION.get(block_key(w), ()) if x not in out]
    return out


def set_blocked(word, terms):
    """把"不要"的名单写进持久表（= 永久生效）。"""
    w = str(word or '').strip()
    if not w:
        return False, '主词是空的'
    data = _read_json(blocked_path())
    key = block_key(w)
    lst = [str(x).strip() for x in (terms or []) if str(x or '').strip()]
    if lst:
        data[key] = lst
    else:
        data.pop(key, None)
    try:
        _write_json(blocked_path(), data)
        return True, ('记住了「%s」的 %d 个不要用的联想词' % (w, len(lst))
                      if lst else '清掉了「%s」的屏蔽记录' % w)
    except Exception as e:
        return False, '写不进去：%s' % e


def block_session(word, terms):
    """只屏蔽这一次（不写盘）。terms 为空 = 这次一个都不屏蔽。"""
    key = block_key(str(word or '').strip())
    lst = set(str(x).strip() for x in (terms or []) if str(x or '').strip())
    if lst:
        _SESSION[key] = lst
    else:
        _SESSION.pop(key, None)


def clear_session(word=None):
    """清掉临时屏蔽。不传词 = 全清（换主词时用得上）。"""
    if word is None:
        _SESSION.clear()
        return
    _SESSION.pop(block_key(str(word or '').strip()), None)


# ═══════════════════════ 四、自检 ═══════════════════════

def selftest():
    """不带任何 app 环境也能跑的最小体检：繁简对称 + 表归并。"""
    rep = []

    def ok(c, m):
        rep.append((bool(c), m))

    ok('吴佩孚' and bool(variants_of('吴佩孚')), '繁简变体非空（简体出发）')
    ok(bool(variants_of('吳佩孚')), '繁简变体非空（繁体出发）')
    # 对称性：从简体出发和从繁体出发，铺出来的必须是同一组字形（各自含自身）
    a = sorted(set(['吴佩孚'] + variants_of('吴佩孚')))
    b = sorted(set(['吳佩孚'] + variants_of('吳佩孚')))
    ok(a == b and len(a) > 1, '简繁两个方向铺出同一组字形（对称）：%s' % '、'.join(a))
    t = Table.from_lines(['吴佩孚,子玉,吴玉帅', '吳佩孚,子玉,孚威将军'])
    forms, canons, group = canon_group('吴佩孚', t)
    ok('子玉' in group and '孚威将军' in group, '跨字形归并：两行的别名都收齐')
    r = related('吴佩孚', table=t)
    ok(r and r[0] == '吴佩孚', '主词排第一')
    ok(len(r) == len(set(r)), '结果无重复')
    ok(expand('吴佩孚', False, False) == ['吴佩孚'], '两个开关都关 = 只认原词')

    # ── 补充异名表（地名 / 机构 / 译名 / 人物）─────────────────────
    # 塞一张假表进去，验证「主表 + 补充表取并集」以及「传了 table 就只用那一张」
    global _EXTRA_TABLES
    saved = _EXTRA_TABLES
    try:
        fake = Table.from_lines(['北京,北平,燕京'])
        fake.source = 'place'
        _EXTRA_TABLES = [fake]
        ok('北京' in related('北平'), '补充表：搜「北平」带出「北京」')
        ok('燕京' in related('北平'), '补充表：连同组别的写法一起给')
        # 补充表走的是同一套内核，所以繁简通搜自动生效（繁简那段不用改）
        ok('北京' in related('北平', variant=False),
           '补充表：关掉繁简也照样认（表内本来同形）')
        # 老调用者：显式传 table 时不掺补充表 —— 行为跟加表之前一模一样
        t0 = Table.from_lines(['吴佩孚,子玉'])
        ok('北京' not in related('北平', table=t0),
           '传了 table 就只用那一张（老调用者不受补充表影响）')
    finally:
        _EXTRA_TABLES = saved

    ok(source_enabled('place') is True, '来源默认启用')
    set_source_enabled('place', False)
    ok(source_enabled('place') is False, '来源可以关掉')
    set_source_enabled('place', True)
    ok(source_enabled('place') is True, '来源可以再打开')
    return rep


if __name__ == '__main__':
    set_csv_paths([])
    for good, msg in selftest():
        print(('  OK  ' if good else ' FAIL ') + msg)
    print('表:', csv_path() or '(未配置)')
