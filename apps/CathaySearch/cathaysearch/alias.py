# -*- coding: utf-8 -*-
"""关联词（人名别名）来源 —— CathaySearch 侧的封装。

规则本身不在本文件里，而在 `query_expand.py`：那份 CathayViewer 里有一份
逐字相同的副本（`viewer_query.py`），由 `tools/sync_shared.py` 保证两边一致。
——在检索里给「吴佩孚」加过的字号，进到书里查找时照样认，靠的就是这个。

本文件只做两件 CathaySearch 自己的事：
  1. 告诉共享模块 CBDB 大表在哪（随包自带的那一份）；
  2. 把共享模块的接口照原样转出去 —— **对外函数名、参数、返回值一律不变**，
     gui / dialogs / cli 那边一个字都不用改。

数据 = 只读解析，不修改那份 CSV。
"""
import os

from . import query_expand as QE

_HERE = os.path.dirname(os.path.abspath(__file__))

# 随包自带的一份（打包后在 _internal_search/cathaysearch/config 下）
CSV_CANDIDATES = [
    os.path.join(_HERE, 'config', 'person_alias.csv'),
]

QE.set_csv_paths(CSV_CANDIDATES)

# 【v0.3.19】补充异名表（地名 / 机构 / 译名 / 人物别称）：一个 csv 一个来源，
# 可单独开关。跟 CathayViewer 用的是同一批文件（tools/sync_shared.py 保证一致）。
QE.set_extra_csv_dirs([
    os.path.join(_HERE, 'config', 'alias_extra'),
])

# 来源开关写在两边共用的公共目录里 —— 在这儿关掉地名，CathayViewer 里也是关的。
try:
    QE.load_source_config()
except Exception:
    pass

# 老版本把用户自己攒的联想词写在「程序目录/runtime」下。现在两边共用
# %APPDATA%\CathayHub 里那一份 —— 第一次启动时搬过去（老文件留着不动）。
def _legacy_paths():
    try:
        from . import config
        d = os.path.join(config.app_dir(), 'runtime')
        return [(os.path.join(d, 'user_alias.json'),
                 os.path.join(d, 'user_alias_blocked.json'))]
    except Exception:
        return []


try:
    QE.migrate_legacy(_legacy_paths())
except Exception:
    pass

# 老代码里有人直接摸这两个表；指向共享模块的同一份数据，行为不变。
_SESSION = QE._SESSION


def _table():
    return QE.load_table()


def _load(force=False):
    """兼容旧调用：现在由共享模块统一缓存，force 时重读一遍。"""
    if force:
        QE.reload()
    return _table()


def csv_path():
    for p in CSV_CANDIDATES:
        if os.path.isfile(p):
            return p
    return ''


def source():
    return QE.csv_path()


def group_count():
    return _table().groups()


def canonical(word):
    """返回该称呼的正名；查不到返回原词。"""
    return _table().canonical(word)


def known(word):
    """这个词是否在别名表中。"""
    return _table().known(word)


def block_key(word):
    """同一个"人"的统一记账 key（简繁两个字形共用一个）。"""
    return QE.block_key(word)


def canon_group(word, variant=True):
    """返回 (forms, canons, aliases)：全部字形 / 全部正名 / 别名并集。"""
    return QE.canon_group(word, _table(), variant)


def related(word, limit=None, include_self=True, variant=True, block=True):
    """返回与 word 关联的称呼（正名 + 全部别名，跨简繁字形合并）。"""
    return QE.related(word, limit=limit, include_self=include_self,
                      variant=variant, block=block, table=_table())


def user_path():
    """用户别名表的位置（两边共用：%APPDATA%\\CathayHub\\user_alias.json）。"""
    return QE.user_path()


def blocked_path():
    """「这个词别再给我」的位置（%APPDATA%\\CathayHub\\user_alias_blocked.json）。"""
    return QE.blocked_path()


def user_aliases():
    """读用户别名表：{主词: [别名...]}"""
    return QE.user_aliases()


def set_user_aliases(word, aliases):
    """写用户别名表（永久生效就靠它）。传空列表 = 删掉这个词。"""
    return QE.set_user_aliases(word, aliases)


def of(word):
    """这个词在**用户表**里记了哪些联想词（没有就是 []）。"""
    return QE.of(word)


def blocked(word, session=True):
    """这个词里用户明确"不要"的那几个联想词。"""
    return QE.blocked(word, session=session)


def set_blocked(word, terms):
    """把"不要"的名单写进持久表（= 永久生效）。"""
    return QE.set_blocked(word, terms)


def block_session(word, terms):
    """只屏蔽这一次（不写盘）。terms 为空 = 这次一个都不屏蔽。"""
    return QE.block_session(word, terms)


def clear_session(word=None):
    """清掉临时屏蔽。不传词 = 全清。"""
    return QE.clear_session(word)


def reload():
    """表文件换过了（用户导入了新别名）→ 重新读一遍。"""
    return QE.reload()


if __name__ == '__main__':
    print('数据源:', source() or '(未找到)')
    print('词条组数:', group_count())
    for w in ['吴佩孚', '梁启超', '一个不存在的人名']:
        print('  %-8s → %s' % (w, related(w)))
