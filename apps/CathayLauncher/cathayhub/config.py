# -*- coding: utf-8 -*-
"""CathayHub Launcher —— 位置信息。

CathayHub 是一套软件放在同一个文件夹里（Search / Viewer / Indexer / Launcher），
本模块负责答"文件夹在哪、那三个程序在哪、共用文件放哪"。
"""
import json
import os
import sys

APP_NAME = 'CathayHub Launcher'
APP_TITLE = 'CathayHub'
APP_VER = '0.1.3'

# 开发时本程序不在 CathayHub 目录里，指过去方便调试
_DEV_HUB = r'D:\我的软件创作库\CathayHub'

EXES = {
    'search': 'CathayHub Search 学术书库搜索工具.exe',
    'viewer': 'CathayHub Viewer 学术书库阅读工具.exe',
    'indexer': 'CathayHub Indexer 学术索引管理工具.exe',
}

# 三个软件共用的那份界面主题
THEME_FILE = os.path.join('runtime', 'hub_theme.qss')
# Viewer 记的"最近打开"就在这份设置里
VIEWER_SETTINGS = 'cathayviewer_settings.json'
# 文件名索引（按书名找文件用的那份库）——放在 CathayHub 文件夹里，
# 跟 Viewer 默认找的位置是同一个，Viewer 才能直接认下来、不再问用户要建库。
FILE_DB = 'cathayviewer_index.db'


def _has_hub(d):
    """目录里像是有 CathayHub：有其中任一个 exe，或者有 shared 目录。"""
    try:
        if not os.path.isdir(d):
            return False
        for n in os.listdir(d):
            if n.startswith('CathayHub') and n.endswith('.exe'):
                return True
        return os.path.isdir(os.path.join(d, 'shared'))
    except Exception:
        return False


def app_dir():
    """CathayHub 文件夹在哪。"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    d = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not _has_hub(d) and _has_hub(_DEV_HUB):
        return _DEV_HUB
    return d


def shared_dir():
    return os.path.join(app_dir(), 'shared')


def exe_path(kind):
    p = os.path.join(app_dir(), EXES.get(kind, ''))
    return p if os.path.isfile(p) else ''


def theme_path():
    return os.path.join(app_dir(), THEME_FILE)


def viewer_settings_path():
    return os.path.join(app_dir(), VIEWER_SETTINGS)


def filename_db_path():
    """文件名索引库默认放哪（CathayHub 文件夹里，和 Viewer 的默认一致）。"""
    return os.path.join(app_dir(), FILE_DB)


def settings_path():
    """Launcher 自己的小账本（首次运行跑过没、主题、启动行为）。

    这也是 Launcher 跟 Search 打招呼的地方：向导做完后会把
    「默认检索哪个索引」写在这里，Search 启动时来读。
    """
    return os.path.join(shared_dir(), 'launcher.json')


DEFAULT_SETTINGS = {'first_run': True, 'theme': 'light', 'auto_open': ''}


def load_settings():
    """读小账本。读不到就当第一次来（用上面那份默认值）。"""
    d = dict(DEFAULT_SETTINGS)
    try:
        with open(settings_path(), 'r', encoding='utf-8') as f:
            d.update(json.load(f))
    except Exception:
        pass
    return d


def save_settings(d):
    """写小账本。返回 (成没成, 出错原因)。"""
    try:
        os.makedirs(shared_dir(), exist_ok=True)
        with open(settings_path(), 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        return True, ''
    except Exception as e:
        return False, str(e)
