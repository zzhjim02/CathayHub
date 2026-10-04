# -*- coding: utf-8 -*-
"""索引与索引组的管理（增删改查）。

工作原理（都是 2026-09-26 实测坐实的，别凭想象改）：
  1. FileLocator 的 `-idxname` **只认名字，不认路径**。给它一个目录或 xml
     路径，它不报错、照样返回 0 条（实测连"中国"这种常见词都是 0）。
     所以要新增一个检索目标，必须在它的配置目录里**登记一个条目**。
  2. 条目就是一个 200 多字节的小 xml（`%APPDATA%\\Mythicsoft\\
     FileLocatorPro\\config\\idx_{uuid}.xml`）：
       <cfg ver="2"><section name="idx"><type n="1"/>...<id t="3">{uuid}</id>
         <path t="3">D:\\某索引目录</path><name t="3">索引名</name>
       </section></cfg>
     type=1 是普通索引（path 指向索引目录），type=2 是**索引组**
     （path 指向一个"成员列表 xml"）。
  3. 索引组是 FileLocator 的原生能力：一次调用就横跨多个索引，实测
     主索引 11391 + 聊天索引 15 = 组 11406 条，2.26 秒，跟单索引一样快。
     所以我们**绝不用"每个索引各跑一遍再合并"那种笨办法**。
  4. 成员列表 xml 可以放在**我们自己的目录**里（实测可用），不必往它的
     AppData 里塞东西。放自己目录的好处是整个文件夹拷走还能用。

铁律：绝不碰索引数据本身（81 GiB，只读），只登记条目；自己建的条目才允许删。
"""
import os
import re
import json
import glob
import uuid

from . import config

# 索引组只存在本程序的账本里（runtime/indexes.json，跟着程序目录走），
# 不去改 FileLocator 的配置 —— 理由见 add_group 的注释。
BOOK = os.path.join(config.app_dir(), 'runtime', 'indexes.json')

CFG_DIR = config.FLP_CFG_DIR
IDX_GLOB = 'idx_*.xml'

TEMP_GROUP = '（多选时用这一项表示一次横跨好几个索引）'

Entry = dict  # {'name': str, 'path': str, 'kind': 'index'|'group'}


# ------------------------------------------------------------------ 账本
def _read_book():
    try:
        with open(BOOK, encoding='utf-8') as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault('created', [])
            d.setdefault('groups', {})
            d.setdefault('normal', {})
            return d
    except Exception:
        pass
    return {'created': [], 'groups': {}, 'normal': {}}


def _write_book(d):
    try:
        os.makedirs(os.path.dirname(BOOK), exist_ok=True)
        with open(BOOK, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception:
        # 目录只读就退化成"本次会话记住"，不致命
        pass


# ------------------------------------------------------------------ 读取
def _entry_path(name):
    """找到某个名字对应的配置文件；没有返回 None。"""
    if not os.path.isdir(CFG_DIR):
        return None
    for p in sorted(glob.glob(os.path.join(CFG_DIR, IDX_GLOB))):
        try:
            s = open(p, encoding='utf-8', errors='replace').read()
        except Exception:
            continue
        m = re.search(r'<name t="\d+">([^<]*)</name>', s)
        if m and m.group(1).strip() == name:
            return p
    return None


def kind_of(path):
    """path 指向 *.xml 就是组，否则是普通索引目录。"""
    return 'group' if path.lower().endswith('.xml') else 'index'


def list_all():
    """返回 [{'name','path','kind','ours'}]，ours=True 表示本程序登记的。

    kind='group' 的有两种来源：
      - FileLocator 原生组（path 指向它的成员 xml）——原样列出；
      - 本程序攒的组（账本里）——虚拟条目，没有 FileLocator 条目。
    """
    book = _read_book()
    mine = set(book['created'])
    out = []
    if os.path.isdir(CFG_DIR):
        for p in sorted(glob.glob(os.path.join(CFG_DIR, IDX_GLOB))):
            try:
                s = open(p, encoding='utf-8', errors='replace').read()
            except Exception:
                continue
            nm = re.search(r'<name t="\d+">([^<]*)</name>', s)
            pt = re.search(r'<path t="\d+">([^<]*)</path>', s)
            if not nm:
                continue
            name = nm.group(1).strip()
            path = (pt.group(1).strip() if pt else '')
            out.append({'name': name, 'path': path,
                        'kind': kind_of(path), 'ours': name in mine})
    # 本程序攒的组：不写进 FileLocator，只存在于自己的账本里
    for g in sorted(book.get('groups', {})):
        if next((x for x in out if x['name'] == g), None):
            continue
        out.append({'name': g, 'path': '', 'kind': 'group', 'ours': True})
    return out


def index_source(path):
    """这个索引当初是拿哪个目录建的？（从索引目录里的 index_settings.xml 读）

    【v0.2.12】更新索引前**必须**先查这个。索引里记着上万个文件，要是它
    的源目录整个没了，更新时引擎会把这些文件全判成"已删除"再写回索引
    —— 等于把索引清空。实测就有这么一个：源目录早不在了，索引里还躺着
    一万多条记录，一点"更新"就全没了。

    读不出来返回 ''（那就当"查不到"，调用方照样要拦）。
    """
    if not path or not os.path.isdir(path):
        return ''
    f = os.path.join(path, 'index_settings.xml')
    if not os.path.isfile(f):
        return ''
    try:
        s = open(f, encoding='utf-8', errors='replace').read()
    except Exception:
        return ''
    m = re.search(r'<location t="\d+">([^<]*)</location>', s)
    return (m.group(1) or '').strip() if m else ''


def group_members(name):
    """某个组都有哪些成员？

    我们自己建的组存在自己的账本里（名字列表）；FileLocator 原生组的成员
    写在它那份 xml 里，照读就是了。
    """
    book = _read_book()
    if name in book.get('groups', {}):
        return list(book['groups'][name])
    e = next((x for x in list_all() if x['name'] == name), None)
    if not e or e['kind'] != 'group':
        return []
    if not os.path.isfile(e['path']):
        return []
    try:
        s = open(e['path'], encoding='utf-8', errors='replace').read()
    except Exception:
        return []
    return re.findall(
        r'<section name="idxchild_\d+"><[^>]*>([^<]*)</', s)


def our_groups():
    """本程序自己攒的组：{组名: [成员索引名, ...]}。"""
    return dict(_read_book().get('groups', {}))


def _name_by_path():
    """{索引目录路径(规范化): 索引名} —— 用来把组里的成员路径换回索引名。"""
    out = {}
    for e in list_all():
        if e['kind'] == 'index' and e['path']:
            try:
                k = os.path.normcase(os.path.normpath(e['path']))
            except Exception:
                continue
            out[k] = e['name']
    return out


def group_targets(name):
    """组 → 能直接喂给 `-idxname` 的**索引名**列表。

    【v0.2.9 修的真 bug】FileLocator 原生组的成员 xml 里存的是**目录路径**
    （`D:\\学术古籍索引-Y盘版` 这种），而 `-idxname` 只认索引名——给它路径
    它不报错、照样返回 0 条。之前界面把成员路径原样当目标传下去，于是
    「[组] 联合检索库」看着有 5 个成员，一检索一条都没有。

    这里做一次 path→name 映射（索引名不一定等于目录名，例如目录
    `D:\\中小学教科书索引` 登记的名字是「中国中小学教科书索引」）。
    万一有成员压根没登记过、映射不上，就整体退回用组名——引擎自己会跨库。
    """
    book = _read_book()
    if name in book.get('groups', {}):
        return list(book['groups'][name])      # 我们攒的组存的本来就是名字
    ms = group_members(name)
    if not ms:
        return [name]
    names = _name_by_path()
    out, miss = [], []
    for m in ms:
        try:
            k = os.path.normcase(os.path.normpath(m))
        except Exception:
            miss.append(m)
            continue
        n = names.get(k)
        if n:
            if n not in out:
                out.append(n)
        else:
            miss.append(m)
    if miss:
        return [name]                          # 映射不全 → 交给引擎整体处理
    return out or [name]


# ------------------------------------------------------------------ 写条目
def _xml(kind_n, uid, path, name):
    return ('<cfg ver="2"><section name="idx"><type n="%d"/>'
            '<readonly n="0"/><id t="3">%s</id><path t="3">%s</path>'
            '<name t="3">%s</name></section></cfg>'
            % (kind_n, uid, path, name))


def _put_entry(name, path, kind):
    """登记（或更新）一个条目。返回 (是否成功, 说明)。"""
    if not os.path.isdir(CFG_DIR):
        return False, '找不到 FileLocator 的配置目录：%s' % CFG_DIR
    old = _entry_path(name)
    if old:
        try:
            s = open(old, encoding='utf-8', errors='replace').read()
            uid = re.search(r'<id t="\d+">([^<]*)</id>', s)
            uid = uid.group(1) if uid else '{' + str(uuid.uuid4()) + '}'
        except Exception:
            uid = '{' + str(uuid.uuid4()) + '}'
        target = old
    else:
        uid = '{' + str(uuid.uuid4()) + '}'
        target = os.path.join(CFG_DIR, 'idx_%s.xml' % uid)
    try:
        with open(target, 'w', encoding='utf-8') as f:
            f.write(_xml(2 if kind == 'group' else 1, uid, path, name))
    except Exception as e:
        return False, '写入失败：%s' % e
    return True, target


# ------------------------------------------------------------------ 对外 API
def add_index(name, path):
    """登记一个普通索引：name 是给它起的名字，path 是索引所在目录。"""
    name = (name or '').strip()
    path = (path or '').strip()
    if not name or not path:
        return False, '索引名和目录都得填'
    if _entry_path(name):
        return False, '已经有个索引叫「%s」了，换个别名' % name
    if not os.path.isdir(path):
        return False, '这个目录不存在：%s' % path
    ok, msg = _put_entry(name, path, 'index')
    if not ok:
        return False, msg
    book = _read_book()
    book['normal'][name] = path
    if name not in book['created']:
        book['created'].append(name)
    _write_book(book)
    return True, '已登记索引「%s」→ %s' % (name, path)


def add_group(name, members):
    """攒一个索引组 —— **只写我们自己的账本**，不去改 FileLocator 的配置。

    为什么不顺手在 FileLocator 里也建一个原生组？实测过：原生组一次调用
    确实能横跨多库（2.26 秒出两库之和），但它对调用方是个黑盒——某个成员
    索引偶发的"假空"（同一个表达式一次 11391、一次 0）发生在它内部时，
    外面完全无从察觉，只能看到总数少了。而我们自己并发各跑一遍，每个成员
    各自有失败重试，哪个库没出结果一眼可见。两者耗时实测相同。
    """
    name = (name or '').strip()
    members = [m for m in (members or []) if m]
    if not name:
        return False, '组名是空的'
    if len(members) < 1:
        return False, '一个成员都没有'
    known = {e['name'] for e in list_all()}
    miss = [m for m in members if m not in known]
    if miss:
        return False, '这几个不是已登记的索引：%s' % '、'.join(miss)
    book = _read_book()
    book['groups'][name] = members
    if name not in book['created']:
        book['created'].append(name)
    _write_book(book)
    return True, '已建成索引组「%s」（%d 个成员）' % (name, len(members))


def resolve_targets(names):
    """把用户勾选的名字摊平成**真正要检索的 FileLocator 目标**。

    本程序攒的组 → 换成成员；FileLocator 原生组 → 保持原样交给引擎
    （它内部会自己跨库搜）；普通索引 → 原样。去重保序。
    """
    out = []
    for n in names or []:
        if not n:
            continue
        book = _read_book()
        if n in book.get('groups', {}):          # 我们攒的组
            members = book['groups'][n]
        else:
            e = next((x for x in list_all() if x['name'] == n), None)
            if e and e['kind'] == 'group':        # 原生组：整体交给引擎
                members = [n]
            else:
                members = [n]
        for m in members:
            if m and m not in out:
                out.append(m)
    return out


def remove(name):
    """删除条目 —— 只删本程序自己登记的，用户手工建的一律不碰。"""
    book = _read_book()
    if name not in book['created']:
        return False, '「%s」不是本程序登记的，不删（只能用 FileLocator 自己删）' % name
    # 本程序攒的组本来就没有 FileLocator 条目，从账本里划掉就算删干净了
    e = _entry_path(name)
    if e:
        try:
            os.remove(e)
        except Exception as e2:
            return False, '删除失败：%s' % e2
    book['created'] = [x for x in book['created'] if x != name]
    book['groups'].pop(name, None)
    book.get('normal', {}).pop(name, None)
    _write_book(book)
    return True, '已删除「%s」' % name


def heal():
    """启动时自愈：换了电脑 / 清过 AppData 之后，本程序登记过的**普通索引**
    计数目标就没了；账本里记着路径，目录还在就补登记回来。返回补了几个。

    组不需要补：它就存在我们自己的账本里，跟着程序目录走。
    """
    book = _read_book()
    n = 0
    for name, path in list(book.get('normal', {}).items()):
        if _entry_path(name):
            continue
        if os.path.isdir(path):
            ok, _ = _put_entry(name, path, 'index')
            n += ok
        else:
            # 目录都没了，登记也跟着失效，从账本里划掉
            book['created'] = [x for x in book['created'] if x != name]
            book['normal'].pop(name, None)
            _write_book(book)
    return n
