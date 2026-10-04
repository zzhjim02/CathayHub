# -*- coding: utf-8 -*-
"""CathayHub Launcher —— 检测逻辑。

干三件事，全程只读（除了最后写自己的映射/登记文件，绝不碰索引数据）：

  1. 找索引：在各盘里翻"书库索引"目录（指纹：里面有 index_settings.xml）。
     用户的机器上未必装过 FileLocator Pro，所以没有登记文件可查，只能真去翻。
  2. 找书库：白名单里那几个目录还在不在？不在就去别的盘找同名文件夹。
     找到了就叫"已转译"。
  3. 落账：写 shared/path_mapping.json（Search/Viewer 靠它转译），
     并把找到的索引登记好、攒成一个组「全部可用索引」。
"""
import json
import os
import time
import uuid

from . import config
from . import filelib
from . import path_translator as pt

APPDATA_SUB = os.path.join('Mythicsoft', 'FileLocatorPro')
GROUP_NAME = '全部可用索引'

# 找同名文件夹时先看这几个盘
PREFER_DRIVES = ('Y', 'D', 'E')


# ---------------------------------------------------------------------------
# 找索引
# ---------------------------------------------------------------------------
def find_indexes(extra_roots=None):
    """这台机器上的书库索引目录列表（全路径）。"""
    try:
        return pt.scan_index_dirs(extra_roots)
    except Exception:
        return []


def index_label(d):
    """索引叫什么 —— 索引目录里没有名字字段，只能拿目录名当名字。"""
    return os.path.basename((d or '').rstrip('\\/'))


# ---------------------------------------------------------------------------
# 找书库（白名单那几个目录现在在哪）
# ---------------------------------------------------------------------------
def _candidate_dirs():
    """各盘根目录 + 下面一层，按"更可能的"排前面。

    只看两层：全盘递归太慢，而索引和书库一般都放在根目录。
    找不到的可以让用户在向导里手动指定。
    """
    out = []
    for dr in pt.drives():
        letter = dr[0].upper()
        pref = 0 if letter in PREFER_DRIVES else 1
        out.append((pref, 0, dr))
        try:
            subs = os.listdir(dr)
        except Exception:
            continue
        for s in subs:
            p = os.path.join(dr, s)
            try:
                if os.path.isdir(p):
                    out.append((pref, 1, p))
            except Exception:
                pass
    out.sort(key=lambda x: (x[0], x[1], x[2]))
    return [p for _, _, p in out]


def locate(expected):
    """目录在哪？

    先按原路径找；找不到就在各盘翻同名文件夹（这就是"已转译"）。
    返回 (实际路径, 是否转译)；实在没有返回 ('', False)。
    """
    if expected and os.path.isdir(expected):
        return expected, False
    name = os.path.basename((expected or '').rstrip('\\/'))
    if not name:
        return '', False
    for root in _candidate_dirs():
        p = os.path.join(root, name)
        try:
            if os.path.isdir(p):
                return p, True
        except Exception:
            continue
    return '', False


def _dirs_from_indexes(index_dirs):
    """白名单没填时，直接从「这些索引当初收了哪些目录」里学一份出来。

    开源版的 MAIN_DIRS 默认是空的 —— 总不能替每个使用者写死他的书库在哪。
    所以这里兜底：把找到的索引各自的源目录收拢成一份清单，照样能定位、
    照样能转译。想精确控制「哪个算主要书库」的人自己去填 MAIN_DIRS，
    填了就以他填的为准。
    """
    seen, out = set(), []
    for d in index_dirs or []:
        try:
            srcs = pt.index_sources(d)
        except Exception:
            continue
        for s in srcs or []:
            k = pt.norm(s)
            if not k or k in seen:
                continue
            seen.add(k)
            out.append({'key': os.path.basename(s.rstrip('\\/')) or s,
                        'expected': s,
                        'level': 'optional',
                        'main': True})
    return out


def check_dirs(index_dirs=None):
    """逐条检查白名单里的目录，返回一份清清楚楚的清单。

    每条：{'key','expected','actual','found','translated','level','main'}

    index_dirs 可由调用方传进来（向导已经扫过一遍，别重复扫盘）。

    level='dual' 的随缘次要条目（聊天记录、两个独立专题库）按口诀处理：
    目录在 → 显示；目录不在但还有索引收着它 → 也显示（由调用方轻声提示）；
    目录和索引都没有 → **整条不出现**。
    """
    if index_dirs is None:
        index_dirs = find_indexes()
    out = []
    watch = pt.MAIN_DIRS or _dirs_from_indexes(index_dirs)
    for w in watch:
        actual, trans = locate(w['expected'])
        if not actual and w.get('level') == 'dual':
            # 目录没了，看还有没有索引收着它 —— 一样都没有就干脆不显示
            if not pt.holder_indexes(w['expected'], index_dirs):
                continue
        out.append({'key': w['key'],
                    'expected': w['expected'],
                    'actual': actual,
                    'found': bool(actual),
                    'translated': trans,
                    'level': w.get('level', 'critical'),
                    'main': bool(w.get('main'))})
    return out


def describe_indexes(index_dirs=None):
    """把找到的索引讲清楚，好在向导里摆给用户看。

    每条：{'name','store','books','registered','joins'}
      name        索引叫什么（目录名，索引内部没有名字字段）
      store       放在哪
      books       它收着咱们照看的哪几个书库（白名单里命中 key）
      registered  之前登记过没有（登记过 = Search 本来就认得它）
      joins       会不会进「全部可用索引」这个组 —— 索引目录在就进
    """
    if index_dirs is None:
        index_dirs = find_indexes()
    have = _existing_paths()
    out = []
    for d in index_dirs or []:
        srcs = pt.index_sources(d)
        books = []
        for s in srcs:
            w = pt.matched_rule(s)
            if w and w['key'] not in books:
                books.append(w['key'])
        out.append({'name': index_label(d),
                    'store': d,
                    'books': books,
                    'registered': pt.norm(d) in have,
                    'joins': True})
    return out


# ---------------------------------------------------------------------------
# 文件名索引（按书名找文件用的那份库，跟 Viewer 的"建库"是同一个东西）
# ---------------------------------------------------------------------------
def filename_db_default():
    """库默认放哪 —— 就用 Viewer 默认找的那个位置，Viewer 才能直接认下来。"""
    return config.filename_db_path()


def filename_db_state(db=''):
    """文件名索引现在什么情况。

    {'path','has','files','built_at','size'} —— has=False 就是还没建过。
    """
    db = db or filename_db_default()
    out = {'path': db, 'has': False, 'files': 0, 'built_at': '', 'size': 0}
    if not db or not os.path.isfile(db):
        return out
    out['has'] = True
    try:
        out['size'] = os.path.getsize(db)
    except OSError:
        pass
    try:
        s = filelib.stats(db) or {}
        out['files'] = int(s.get('files') or 0)
        out['built_at'] = str(s.get('built_at') or '')
    except Exception:
        pass
    return out


def suggest_roots(items):
    """文件名索引该扫哪些目录 —— 就扫这次检测到的、确实还在的那些书库。

    用的是**实际路径**（换过盘的已经是新位置），所以扫出来的文件位置
    就是能直接打开的位置，不用再转译一层。
    """
    out = []
    for it in items or []:
        p = (it.get('actual') or '').rstrip('\\/')
        if it.get('found') and p and p not in out:
            out.append(p)
    return out


def save_filename_db(db, roots, count=None, built_at='', write_roots=True):
    """库建好了，记两笔账。

    一笔写 shared/launcher.json（Search 那边也看这个文件）；
    另一笔直接写进 Viewer 自己的设置 —— 这样 Viewer 一打开就知道库已经有了，
    不会再弹"第一次使用 —— 建立文件名索引"来问用户。

    write_roots=False：只登记"库在哪"，**不动 Viewer 原来那套扫描目录**
    （向导没真建库时不该悄悄给人家换掉）。
    """
    d = config.load_settings()
    d['filename_db'] = db
    d['filename_roots'] = list(roots or [])
    d['filename_db_count'] = int(count or 0)
    d['filename_db_built_at'] = built_at or time.strftime('%Y-%m-%d %H:%M:%S')
    ok, err = config.save_settings(d)
    try:
        p = config.viewer_settings_path()
        vs = {}
        if os.path.isfile(p):
            with open(p, 'r', encoding='utf-8') as f:
                vs = json.load(f) or {}
        vs['primary_db'] = db
        if write_roots and roots:
            vs['roots'] = list(roots)
        vs['last_count'] = int(count or 0)
        vs['last_built'] = d['filename_db_built_at']
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(vs, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return ok, err


def save_default_index(index_dirs):
    """把「全部可用索引」设为 Search 的默认检索目标。

    记在 shared/launcher.json 里，Search 启动时来读；时间戳让 Search 认出
    "这是新一轮向导"，重跑向导能重新把默认夺回来。找不到任何索引就不写，
    免得把明明能用的默认给改成空的。
    """
    if not index_dirs:
        return False, '一个索引都没找到，先不动默认的那个'
    d = config.load_settings()
    d['default_index'] = GROUP_NAME
    d['default_index_stamp'] = str(int(time.time()))
    ok, err = config.save_settings(d)
    return ok, ('' if ok else err)


def build_mapping(items):
    """按用户定的格式拼映射表：{数据库名: {expected, actual, translated}}"""
    d = {}
    for it in items or []:
        d[it['key']] = {'expected': it['expected'],
                        'actual': it['actual'] or it['expected'],
                        'translated': bool(it['translated'])}
    return d


def save_mapping(items):
    ok, err = pt.write_mapping(build_mapping(items))
    return ok, err


# ---------------------------------------------------------------------------
# 登记索引 + 攒组（用户机器上没装 FileLocator Pro，登记得我们自己写）
# ---------------------------------------------------------------------------
def _cfg_dir():
    return os.path.join(os.environ.get('APPDATA', ''), APPDATA_SUB, 'config')


def _idx_dir():
    return os.path.join(os.environ.get('APPDATA', ''), APPDATA_SUB, 'Index')


def _existing_paths():
    """已经登记过哪些索引目录（别重复登记，也别覆盖人家原有的）。"""
    import glob
    import re
    paths = set()
    for f in glob.glob(os.path.join(_cfg_dir(), 'idx_*.xml')):
        try:
            with open(f, 'r', encoding='utf-8', errors='replace') as fh:
                s = fh.read()
        except Exception:
            continue
        m = re.search(r'<path t="\d+">([^<]*)</path>', s)
        if m:
            paths.add(pt.norm(m.group(1)))
    return paths


def _write_entry(path, name, kind=1):
    """写一条索引登记（kind=1 普通索引，kind=2 组）。"""
    d = _cfg_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        return False
    uid = '{' + str(uuid.uuid4()) + '}'
    s = ('<cfg ver="2"><section name="idx"><type n="%d"/>'
         '<id t="3">%s</id><path t="3">%s</path><name t="3">%s</name>'
         '<readonly n="0"/></section></cfg>' % (kind, uid, path, name))
    try:
        with open(os.path.join(d, 'idx_%s.xml' % uid), 'w',
                  encoding='utf-8') as f:
            f.write(s)
        return True
    except Exception:
        return False


def register(index_dirs, group_name=GROUP_NAME):
    """把索引登记进 FileLocator 的配置，并攒成一个组。

    只**新增**登记，碰到已登记过的目录就跳过 —— 不改动任何既有条目。
    返回 (新增了几条, 组文件路径)
    """
    added = 0
    have = _existing_paths()
    for d in index_dirs or []:
        if pt.norm(d) in have:
            continue
        if _write_entry(d, index_label(d), 1):
            have.add(pt.norm(d))
            added += 1

    gpath = os.path.join(_idx_dir(), '%s.xml' % group_name)
    try:
        os.makedirs(_idx_dir(), exist_ok=True)
        s = ['<cfg ver="2"><section name="idxgrp"><count n="%d"/></section>'
             % len(index_dirs)]
        for i, d in enumerate(index_dirs or []):
            s.append('<section name="idxchild_%d"><path t="3">%s</path></section>'
                     % (i, d))
        s.append('</cfg>')
        with open(gpath, 'w', encoding='utf-8') as f:
            f.write(''.join(s))
    except Exception:
        gpath = ''
    if gpath:
        # 组登记同样只登记一次 —— 不然向导每跑一回就多堆一条
        if pt.norm(gpath) not in have:
            _write_entry(gpath, group_name, 2)
    return added, gpath


# ---------------------------------------------------------------------------
# 主题（三个软件都读 runtime/hub_theme.qss，改这一个文件就够）
# ---------------------------------------------------------------------------
THEMES = {
    'light': ('浅色', """# CathayHub 统一界面主题（浅色）
QWidget {
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC";
    font-size: 9pt;
    color: #1F2430;
}
QMainWindow, QDialog { background: #F7F8FA; }
QTreeWidget, QTableWidget, QListWidget, QTreeView, QTableView {
    background: #FFFFFF;
    alternate-background-color: #F3F6FA;
    selection-background-color: #0B5CAB;
    selection-color: #FFFFFF;
}
QHeaderView::section {
    background: #EEF2F7;
    padding: 4px 6px;
    border: 0;
    border-right: 1px solid #DCE3EC;
}
QPushButton {
    background: #FFFFFF;
    border: 1px solid #C9D3E0;
    border-radius: 4px;
    padding: 4px 10px;
}
QPushButton:hover { background: #EAF2FC; border-color: #0B5CAB; }
QPushButton:pressed { background: #D8E6F8; }
QPushButton:disabled { color: #9AA5B1; background: #F2F4F7; }
QLineEdit, QComboBox, QTextEdit, QPlainTextEdit {
    background: #FFFFFF;
    border: 1px solid #C9D3E0;
    border-radius: 4px;
    padding: 3px 5px;
}
QStatusBar { background: #EEF2F7; }
QToolTip { background: #FFFFF0; color: #1F2430; }
"""),
    'dark': ('深色', """# CathayHub 统一界面主题（深色）
QWidget {
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC";
    font-size: 9pt;
    color: #E4E7EC;
}
QMainWindow, QDialog { background: #23262C; }
QTreeWidget, QTableWidget, QListWidget, QTreeView, QTableView {
    background: #2B2F36;
    alternate-background-color: #30343C;
    selection-background-color: #4A86C8;
    selection-color: #FFFFFF;
}
QHeaderView::section {
    background: #30343C;
    color: #E4E7EC;
    padding: 4px 6px;
    border: 0;
    border-right: 1px solid #3C414A;
}
QPushButton {
    background: #33383F;
    color: #E4E7EC;
    border: 1px solid #4A5058;
    border-radius: 4px;
    padding: 4px 10px;
}
QPushButton:hover { background: #3D434B; border-color: #6C93C8; }
QPushButton:pressed { background: #484F58; }
QPushButton:disabled { color: #6B7280; background: #2B2F36; }
QLineEdit, QComboBox, QTextEdit, QPlainTextEdit {
    background: #2B2F36;
    color: #E4E7EC;
    border: 1px solid #4A5058;
    border-radius: 4px;
    padding: 3px 5px;
}
QStatusBar { background: #30343C; color: #C9CDD4; }
QToolTip { background: #3A3F47; color: #E4E7EC; }
"""),
    'sepia': ('护眼', """# CathayHub 统一界面主题（护眼）
QWidget {
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC";
    font-size: 9pt;
    color: #3A3226;
}
QMainWindow, QDialog { background: #F3EDE0; }
QTreeWidget, QTableWidget, QListWidget, QTreeView, QTableView {
    background: #FBF6EA;
    alternate-background-color: #F5EEE0;
    selection-background-color: #B98A4A;
    selection-color: #FFFFFF;
}
QHeaderView::section {
    background: #EDE4D2;
    padding: 4px 6px;
    border: 0;
    border-right: 1px solid #DED3BC;
}
QPushButton {
    background: #FBF6EA;
    border: 1px solid #CBBFA4;
    border-radius: 4px;
    padding: 4px 10px;
}
QPushButton:hover { background: #F2E9D6; border-color: #B98A4A; }
QPushButton:pressed { background: #E9DECA; }
QPushButton:disabled { color: #A79C86; background: #F0E9DC; }
QLineEdit, QComboBox, QTextEdit, QPlainTextEdit {
    background: #FBF6EA;
    border: 1px solid #CBBFA4;
    border-radius: 4px;
    padding: 3px 5px;
}
QStatusBar { background: #EDE4D2; }
QToolTip { background: #FFFDF5; color: #3A3226; }
"""),
}


def write_theme(key):
    """把选中的主题写进 runtime/hub_theme.qss（三个软件都读它）。"""
    item = THEMES.get(key)
    if not item:
        return False, '没有这个主题：%s' % key
    try:
        with open(config.theme_path(), 'w', encoding='utf-8') as f:
            f.write(item[1])
        return True, ''
    except Exception as e:
        return False, str(e)
