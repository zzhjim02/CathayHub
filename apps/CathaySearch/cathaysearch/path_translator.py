# -*- coding: utf-8 -*-
"""CathayHub 路径转译模块（四个软件共用：Launcher / Search / Viewer / Indexer）

一句话说明它干什么
--------------------
书库索引里记的文件路径，是**当初建索引时**的路径（比如 X:\\我的书库\\xx.pdf）。
如果用户把书库挪到了别的盘（比如 D 盘），索引里那条老路径就打不开了。
本模块负责：把老路径换算成"现在真正的位置"，让点开结果时还能找到文件。

它**只改要打开的路径，绝不改动索引本身，也绝不移动任何文件**。

用法（两行）
------------
    from path_translator import translate_path
    real = translate_path(r'X:\\我的书库\\a\\b.pdf')

映射文件（CathayHub\\shared\\path_mapping.json）由 CathayHub Launcher 生成。
文件不存在、内容坏了、或者这条路径压根不需要转译时，函数**原样返回**，
调用方可以完全不关心转译这件事。
"""

import json
import os
import re
import string
import sys

MAPPING_NAME = 'path_mapping.json'

# ---------------------------------------------------------------------------
# 一、要照看的目录清单（白名单）
#
# 一条索引可能同时索引几十个目录。全部照看既慢又吵，所以只照看下面这些：
#
#   level    'critical' = 主要书库：缺了要弹窗提醒；
#            'optional' = 次要书库：缺了也只是安静标一行，**始终显示**；
#            'dual'     = 随缘次要（聊天记录、两个独立专题库）：
#                        一句口诀 —— **有就显示，没有就不显示。**
#                        目录还在 → 正常显示；
#                        目录不在、但还有索引收着它 → 照样显示一行，
#                          只是提示一声（不弹窗打扰）；
#                        目录和索引两样都找不到 → **整条不显示**，
#                          列表里不放没用的空行。
#                        判断"有没有索引收着它"见 holder_indexes()。
#   main     True = 这个目录"在不在"，决定对应索引算不算"还在"。
#            学术信息索引那两个大库就是 main，其余（如个人百度网盘目录）
#            只在自己缺失时提示，不影响"索引还在不在"的判断。
#
# expected 一律写**索引里当初记的那个路径**，盘符照实写（第三条实测是 D 盘）。
# ---------------------------------------------------------------------------
# 默认**留空**：拿到源码的人不用先改代码就能用 ——
# Launcher 会自动从「找到的索引当初收了哪些目录」里学一份出来
# （见 cathayhub/detect.py 的 _dirs_from_indexes）。
#
# 想精确指定哪些目录算"主要书库"，照下面注释里的例子往列表里填就行
# （盘符和目录名换成你自己的）：
#
#   {'key': '我的主书库', 'expected': r'X:\我的主书库', 'level': 'critical', 'main': True},
#   {'key': '专题库A',   'expected': r'X:\专题库A',   'level': 'optional', 'main': True},
#
# level  critical = 主要：缺了会弹窗提醒
#        optional = 次要：缺了只是安静标一行，但始终显示
#        dual     = 随缘：目录和索引两样都没有时，整条不显示
# main   True = 这个目录"在不在"，决定对应索引算不算"还在"
MAIN_DIRS = []


def norm(p):
    """把路径整理成可比对的模样：去首尾空白、去末尾斜杠、转小写。

    Windows 路径大小写不敏感，索引里还出现过小写的 d:\\，所以一律归一。
    """
    return (p or '').strip().rstrip('\\/').lower()


# ---------------------------------------------------------------------------
# 二、shared 目录在哪（放 path_mapping.json 的地方）
# ---------------------------------------------------------------------------
_BASE = None


def set_base_dir(d):
    """调用方（Search/Viewer/Launcher）知道自己的程序目录，主动告诉模块一声最好。"""
    global _BASE
    _BASE = d
    return _BASE


def base_dir():
    """CathayHub 的程序目录。按把握从大到小依次猜。"""
    if _BASE:
        return _BASE
    d = os.environ.get('CATHAYHUB_DIR', '')
    if d and os.path.isdir(d):
        return d
    # 打包成 exe 后，sys.executable 就是 CathayHub 目录下那个 exe
    try:
        d = os.path.dirname(sys.executable)
        if os.path.isdir(d) and _looks_like_hub(d):
            return d
    except Exception:
        pass
    # 开发态：本文件在 <程序目录>/shared/ 下
    here = os.path.dirname(os.path.abspath(__file__))
    up = os.path.dirname(here)
    for c in (up, here, os.path.dirname(up)):
        if os.path.isdir(c) and _looks_like_hub(c):
            return c
    return up or here


def _looks_like_hub(d):
    """目录里像是有 CathayHub：有 exe、或者已经有 shared 目录。"""
    try:
        for n in os.listdir(d):
            if n.lower().startswith('cathayhub') and n.lower().endswith('.exe'):
                return True
        if os.path.isdir(os.path.join(d, 'shared')):
            return True
    except Exception:
        pass
    return False


def shared_dir():
    d = os.path.join(base_dir(), 'shared')
    return d


def mapping_path():
    return os.path.join(shared_dir(), MAPPING_NAME)


def read_mapping():
    """读映射文件。读不到 / 坏了 / 格式不对 → 返回 {}（等于"不转译"，安全）。"""
    try:
        with open(mapping_path(), 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def write_mapping(data):
    """写映射文件（Launcher 用）。目录不存在就建。"""
    d = shared_dir()
    try:
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with open(mapping_path(), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True, ''
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# 三、索引当初索引了哪些目录
# ---------------------------------------------------------------------------
def index_sources(store):
    """读出这个索引当初索引的所有目录，返回 list。

    索引目录里的 index_settings.xml 用**分号**把几十个目录串成一条，
    直接拿整条去 os.path.isdir() 必然是 False —— 这正是 Indexer 误报
    "源目录不存在"的原因。这里老老实实拆开。
    """
    raw = index_source(store)
    if not raw:
        return []
    out = []
    for p in raw.split(';'):
        p = (p or '').strip()
        if not p:
            continue
        p = p.rstrip('\\/')
        if p and p not in out:
            out.append(p)
    return out


def index_source(store):
    """原始的那条分号串（保留给老代码用）。读不出来返回 ''。"""
    if not store or not os.path.isdir(store):
        return ''
    f = os.path.join(store, 'index_settings.xml')
    if not os.path.isfile(f):
        return ''
    try:
        with open(f, 'r', encoding='utf-8', errors='replace') as fh:
            s = fh.read()
    except Exception:
        return ''
    m = re.search(r'<location t="\d+">([^<]*)</location>', s)
    return (m.group(1) or '').strip() if m else ''


def _match_rule(src):
    """这个源目录对应白名单里的哪一条？先精确比，再按文件夹名比。"""
    n = norm(src)
    for w in MAIN_DIRS:
        if n == norm(w['expected']):
            return w
    base = n.replace('\\', '/').rstrip('/').split('/')[-1]
    for w in MAIN_DIRS:
        if base == norm(w['expected']).replace('\\', '/').rstrip('/').split('/')[-1]:
            return w
    return None


def dir_status(store):
    """判断一个索引的"书库还在不在"。

    返回 dict：
      state    'ok'      主要目录都在（其余目录缺不缺不影响这个结论）
               'partial' 主要目录缺了一部分
               'missing' 主要目录一个都不在
               'unknown' 这个索引没对上白名单（比如用户自己新建的索引），
                         退回老办法：源目录里任意一个还在就算 ok
      missing  当前不存在的源目录清单（**全部**，含白名单外的）
                 —— 更新索引前要给用户的"会丢哪些"就是它
      missing_main 不存在的主要目录（白名单里 main=True 的那几条）
    """
    srcs = index_sources(store)
    if not srcs:
        return {'state': 'unknown', 'missing': [], 'missing_main': [],
                'sources': []}

    hit_main = [w for w in (_match_rule(s) for s in srcs)
                if w and w.get('main')]
    missing = [s for s in srcs if not os.path.isdir(s)]

    if hit_main:
        miss_main = [w for w in hit_main if not os.path.isdir(w['expected'])]
        if not miss_main:
            state = 'ok'
        elif len(miss_main) < len(hit_main):
            state = 'partial'
        else:
            state = 'missing'
    else:
        # 没对上白名单：老办法，有一个目录在就算还在
        state = 'ok' if len(missing) < len(srcs) else 'missing'
        miss_main = []

    return {'state': state, 'missing': missing, 'missing_main': miss_main,
            'sources': srcs}


# ---------------------------------------------------------------------------
# 四、转译
# ---------------------------------------------------------------------------
def rules():
    """把映射文件整理成规则列表，只留真正需要转译的（translated=True）。

    按 expected 从长到短排 —— 防止出现"父目录规则先命中"的错配：
    例如 D:\\导引库 是 D:\\导引库\\网盘同步目录 的父目录，
    两条规则都能匹配同一条路径，必须让更长的那条先说话。
    """
    data = read_mapping()
    out = []
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        exp = v.get('expected') or ''
        act = v.get('actual') or ''
        if not exp or not act or not v.get('translated'):
            continue
        if norm(exp) == norm(act):      # 一模一样，转了也白转
            continue
        out.append({'key': k, 'expected': exp, 'actual': act})
    out.sort(key=lambda r: -len(r['expected']))
    return out


def translate_path(path, mapping=None):
    """把索引里的老路径换算成现在的真实路径。

    返回 (新路径, 命中的规则或 None)。
    不需要转译 / 没有映射文件 / 出了任何岔子 → 原样返回 (path, None)。
    """
    if not path:
        return path, None
    try:
        rs = rules() if mapping is None else _rules_of(mapping)
        low = norm(path)
        for r in rs:
            e = r['expected']
            if low.startswith(norm(e)):
                newp = r['actual'] + path[len(e):]
                return newp, r
    except Exception:
        pass
    return path, None


def _rules_of(mapping):
    """从现成的 dict 里整理规则（不读文件，方便测试）。"""
    out = []
    for k, v in (mapping or {}).items():
        if not isinstance(v, dict):
            continue
        exp, act = v.get('expected') or '', v.get('actual') or ''
        if exp and act and v.get('translated') and norm(exp) != norm(act):
            out.append({'key': k, 'expected': exp, 'actual': act})
    out.sort(key=lambda r: -len(r['expected']))
    return out


def translated_label(rule):
    """状态栏那句提示，例如『路径已转译：Y盘 → D盘』。"""
    if not rule:
        return ''
    a = (rule.get('expected') or '')[:2]
    b = (rule.get('actual') or '')[:2]
    if a and b and a != b:
        return '路径已转译：%s → %s' % (a, b)
    return '路径已转译'


# ---------------------------------------------------------------------------
# 五、找索引（用户机器上没装 FileLocator Pro，也就没有登记文件可查）
# ---------------------------------------------------------------------------
def is_index_dir(d):
    """一个目录是不是书库索引？指纹：里面有 index_settings.xml。"""
    try:
        return os.path.isfile(os.path.join(d, 'index_settings.xml'))
    except Exception:
        return False


def drives():
    """这台机器有哪些盘。"""
    return [d + ':\\' for d in string.ascii_uppercase if os.path.exists(d + ':\\')]


def matched_rule(src):
    """这个源目录对应白名单里的哪一条？没有就 None。"""
    return _match_rule(src)


def holder_indexes(expected, index_dirs=None):
    """还有哪些索引收着这个目录？（判断"有没有索引还惦记着它"）

    看的是索引**当初索引了哪些目录**（index_settings.xml 里的 location），
    不是目录现在在不在。所以书库被挪走甚至删了，只要索引还在，就还算"有人收着"。

    expected 传白名单里那条路径；index_dirs 由调用方给（省得重复扫盘），
    不传就自己扫一遍。
    """
    w = None
    for x in MAIN_DIRS:
        if norm(x['expected']) == norm(expected):
            w = x
            break
    if w is None:
        return []
    out = []
    for d in (index_dirs if index_dirs is not None else scan_index_dirs()):
        try:
            srcs = index_sources(d)
        except Exception:
            continue
        if any(_match_rule(s) is w for s in srcs):
            out.append(d)
    return out


def level_of_index(name, store):
    """这个索引对应白名单里的哪一条？先按源目录比，再按索引名猜。"""
    srcs = index_sources(store) if store else []
    for s in srcs:
        w = _match_rule(s)
        if w:
            return w
    n = name or ''
    for w in MAIN_DIRS:
        k = w['key']
        if k and (k in n or n in k):
            return w
    return None


def should_hide(name, store):
    """这类索引要不要整条不显示？

    目前只有聊天记录这一类（level='dual'）有这个讲究：
    索引和书库**两个都没有** → 一条都不显示（否则列表里躺个没用的空行）。
    只要还有一样在，就照常显示（缺的那一样由调用方提示，但不警告）。
    """
    w = level_of_index(name, store)
    if not w or w.get('level') != 'dual':
        return False
    idx_ok = bool(store) and os.path.isdir(store)
    src_ok = any(os.path.isdir(s) for s in index_sources(store)) if store else False
    return (not idx_ok) and (not src_ok)


def scan_index_dirs(extra_roots=None, depth=2):
    """在各盘里找书库索引目录。

    只看盘符根目录和下层一层（depth=2），不递归 —— 全盘遍历太慢，
    而且索引目录通常就放在根目录。找不全的可以让用户在向导里手动指定。
    """
    found = []

    def _walk(root, left):
        try:
            subs = [os.path.join(root, x) for x in os.listdir(root)]
        except Exception:
            return
        for d in subs:
            try:
                if not os.path.isdir(d):
                    continue
            except Exception:
                continue
            if is_index_dir(d):
                if d not in found:
                    found.append(d)
                continue
            if left > 1:
                _walk(d, left - 1)

    for dr in drives():
        _walk(dr, depth)
    for r in (extra_roots or []):
        if r and os.path.isdir(r):
            _walk(r, depth)
    return found
