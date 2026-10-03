# -*- coding: utf-8 -*-
"""关联词（人名别名）来源。

直接复用 CathayViewer 已有的 CBDB 别名表（config/person_alias.csv，约 9.9 万行，
格式：正名,别名1,别名2…；逗号/顿号/分号/斜杠/竖线都可作分隔，'#' 起为注释）。

只做只读解析，不修改那份 CSV。
"""
import os
import json
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))

CSV_CANDIDATES = [
    # 随包自带的一份（打包后 CathayViewer 未必在机器上，优先用自带的）
    os.path.join(_HERE, 'config', 'person_alias.csv'),
    r'D:\我的软件创作库\CathayViewer-DEV\config\person_alias.csv',
    r'D:\我的软件创作库\CathayViewer 学术书库浏览与阅读 0.1.6\config\person_alias.csv',
    r'D:\我的软件创作库\CathayViewer 学术书库浏览与阅读 0.1.7\config\person_alias.csv',
]

_SEPS = ['，', ',', '、', '；', ';', '/', '|']
_lock = threading.Lock()
_loaded = False
_to_canon = {}      # 任意称呼 -> 正名
_aliases = {}       # 正名 -> [别名...]
_source = ''


def _split(s):
    out, cur = [], ''
    for ch in s:
        if ch in _SEPS:
            if cur.strip():
                out.append(cur.strip())
            cur = ''
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def csv_path():
    for p in CSV_CANDIDATES:
        if os.path.isfile(p):
            return p
    return ''


def _load(force=False):
    global _loaded, _source
    with _lock:
        if _loaded and not force:
            return
        _to_canon.clear()
        _aliases.clear()
        p = csv_path()
        _source = p
        if not p:
            _loaded = True
            return
        try:
            with open(p, 'r', encoding='utf-8-sig') as f:
                lines = f.read().splitlines()
        except Exception:
            _loaded = True
            return
        for ln in lines:
            ln = ln.strip()
            if not ln or ln.startswith('#'):
                continue
            parts = _split(ln)
            if len(parts) < 2:
                continue
            canon = parts[0]
            alist = _aliases.setdefault(canon, [])
            _to_canon[canon] = canon
            for a in parts[1:]:
                if a != canon:
                    if a not in alist:
                        alist.append(a)
                    _to_canon.setdefault(a, canon)
        _loaded = True


def canonical(word):
    """返回该称呼的正名；查不到返回原词。"""
    if not word:
        return word
    _load()
    return _to_canon.get(word.strip(), word.strip())


# ───────────────────────── 跨字形归并 ─────────────────────────
def _forms(word, variant=True):
    """一个词的全部繁简字形（含自身），**按字符序排序**。

    排序是关键：同一组字形成员跟用哪个字形起头无关，
    排出来永远同一串，后面的合并结果才不会因为"你打的是简体还是繁体"
    而变个样子。
    """
    w = str(word or '').strip()
    if not w:
        return []
    out = [w]
    if variant:
        try:
            from . import simplify
            for v in simplify.variants_of(w):
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
    fs = _forms(word)
    return fs[0] if fs else str(word or '').strip()


def canon_group(word, variant=True):
    """返回 (forms, canons, aliases)：全部字形 / 全部正名 / 别名并集。

    CBDB 这份表是**按字形分行**的：同一个人在表里常常既有一行简体
    `吴佩孚,子玉,吴玉帅,玉帅,孚威将军,孚威`，又有一行繁体 `吳佩孚,子玉`，
    两行收的别名还不一样多。只查其中一行 = 必然漏掉另一行的存货。
    所以这里把**同一组字形能查到的所有行**并起来：

        输入词 → 全部字形 → 各自的正名 → 正名再换字形补一轮
              → 把所有这些行的别名并成一个集合

    返回的三个序列都排好序、去重，跟"你打的是哪个字形"完全无关。
    """
    _load()
    forms = _forms(word, variant)
    canons = []
    # 第一轮：每个字形各自的表条目
    for f in forms:
        c = _to_canon.get(f)
        if c and c not in canons:
            canons.append(c)
    # 第二轮：正名自己也有简繁两个写法，也各有各的行 —— 一并捞进来
    if variant:
        extra = []
        try:
            from . import simplify
            for c in list(canons):
                for cv in simplify.variants_of(c):
                    c2 = _to_canon.get(cv)
                    if c2 and c2 not in canons and c2 not in extra:
                        extra.append(c2)
        except Exception:
            pass
        canons = sorted(set(canons) | set(extra))
    group = []
    for c in canons:
        for a in _aliases.get(c, []):
            if a not in group:
                group.append(a)
    return forms, canons, group


def related(word, limit=None, include_self=True, variant=True, block=True):
    """返回与 word 关联的称呼（正名 + 全部别名，跨简繁字形合并）。

    include_self=True 时把 word 本身放进首位（检索表达式需要主词）；
    limit 只掐"别名"这一段（字形与正名不在其列）；
    block=True 时剔除用户明确不要的词（见下面「用户屏蔽的联想词」）。

    **为什么要把简繁并起来**：别名表是分字形建的，打简体「萧耀南」在表
    里根本没有这一行，照直查就是"查无此人"，一个联想词也不出；即便出了，
    也只是繁体那一行收的那一半。两个字形都查一遍再合并，才谈得上不遗漏。
    """
    if not word or not word.strip():
        return []
    _load()
    w = word.strip()
    forms, canons, group = canon_group(w, variant)
    if limit:
        group = group[:limit]

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
        bad = _blocked_of(w)
        if bad:
            out = [x for x in out if x == w or x not in bad]
    return out


def known(word):
    """这个词是否在别名表中。"""
    if not word:
        return False
    _load()
    return word.strip() in _to_canon


# ───────────────────────── 用户自己攒的别名 ─────────────────────────
# CBDB 那张表再大也覆盖不到所有人（尤其是自造的简称、笔名、字号）。
# 用户口径：「如果某个检索词没有联想词，要允许用户自己输入联想词」+
# 「在一个管理器里管理，并选择永久生效」——于是单独存一份，写在程序目录下，
# 绝不改 CBDB 那份 CSV。

def user_path():
    """用户别名表的位置（程序目录/runtime/user_alias.json）。"""
    try:
        from . import config
        d = os.path.join(config.app_dir(), 'runtime')
    except Exception:
        d = os.path.join(os.path.dirname(_HERE), 'runtime')
    return os.path.join(d, 'user_alias.json')


def blocked_path():
    """「这个词别再给我」的位置（runtime/user_alias_blocked.json）。

    单独一张表：CBDB 那份别名表是只读的公共数据，一个字节都不能改；
    用户自己加的联想词（user_alias.json）是"加法"，而这里记的是"减法"
    ——两件事混在一张表里迟早算不清。
    """
    try:
        from . import config
        d = os.path.join(config.app_dir(), 'runtime')
    except Exception:
        d = os.path.join(os.path.dirname(_HERE), 'runtime')
    return os.path.join(d, 'user_alias_blocked.json')


def user_aliases():
    """读用户别名表：{主词: [别名...]}"""
    p = user_path()
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, 'r', encoding='utf-8') as f:
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
    p = user_path()
    try:
        d = os.path.dirname(p)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        return True, ('已记住「%s」的 %d 个联想词（下次打开还在）'
                      % (word, len(lst)) if lst else '已清掉「%s」的联想词' % word)
    except Exception as e:
        return False, '写不进去：%s' % e


def of(word):
    """这个词在**用户表**里记了哪些联想词（没有就是 []）。"""
    return list(user_aliases().get(str(word or '').strip(), []))


# ───────────────────────── 用户不要的联想词 ─────────────────────────
# 「我把衡山删掉了，为什么检索又冒出来」——因为只记了"还是那些词"，
# 没记"这个词我不要"。表是死的，每次重查照样把它端回来。
# 所以这里专门一张减法表：按 block_key（简繁共用）记账。
#   持久层：写盘，下次打开还在（= 勾了「永久生效」）
#   会话层：只在本次运行有效（= 这次临时的增删）
_SESSION = {}       # {key: set(词)}


def _read_blocked():
    p = blocked_path()
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, 'r', encoding='utf-8') as f:
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


def _write_blocked(data):
    p = blocked_path()
    d = os.path.dirname(p)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def blocked(word, session=True):
    """这个词里用户明确"不要"的那几个联想词。"""
    w = str(word or '').strip()
    if not w:
        return []
    out = list(_read_blocked().get(block_key(w), []))
    if session:
        out += [x for x in _SESSION.get(block_key(w), ())
                if x not in out]
    return out


def _blocked_of(word):
    return set(blocked(word))


def set_blocked(word, terms):
    """把"不要"的名单写进持久表（= 永久生效）。"""
    w = str(word or '').strip()
    if not w:
        return False, '主词是空的'
    data = _read_blocked()
    key = block_key(w)
    lst = [str(x).strip() for x in (terms or []) if str(x or '').strip()]
    if lst:
        data[key] = lst
    else:
        data.pop(key, None)
    try:
        _write_blocked(data)
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


def source():
    _load()
    return _source


def group_count():
    _load()
    return len(_aliases)


if __name__ == '__main__':
    print('数据源:', source() or '(未找到)')
    print('词条组数:', group_count())
    for w in ['吴佩孚', '梁启超', '一个不存在的人名']:
        print('  %-8s → %s' % (w, related(w)))
