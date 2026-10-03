# -*- coding: utf-8 -*-
"""CathaySearch 配置：FileLocator Pro 位置、可用索引的自动发现。
只做只读发现，绝不写入任何索引目录。

【v0.2.5】依赖随包：本程序文件夹里自带一份 FileLocator Pro
（`runtime/FileLocatorPro/`），优先用它；机器上自己装的那份只作兜底。
索引数据本身一动不动（81 GiB，只读）——这里只登记"去问谁"。
"""
import os
import re
import sys
import glob


def app_dir():
    """本程序所在目录：打包后是 exe 所在目录，源码运行时是工程根目录。

    所有"随程序带的依赖"都放在它下面，整个目录拷到别处照样能跑。
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# 内置的那份（随程序走，最优先）
BUNDLED_FLP_DIR = os.path.join(app_dir(), 'runtime', 'FileLocatorPro')
BUNDLED_FLP_EXE = os.path.join(BUNDLED_FLP_DIR, 'FileLocatorPro.exe')

# FileLocator Pro 主程序候选（必须用与安装版本一致的 FileLocatorPro.exe，
# 不用 flpsearch.exe —— 后者版本不匹配，实测一律返回码 2）
FLP_CANDIDATES = [
    BUNDLED_FLP_EXE,
    r'D:\Program Files\FileLocator Pro\FileLocatorPro.exe',
    r'C:\Program Files\FileLocator Pro\FileLocatorPro.exe',
    r'D:\Program Files\Mythicsoft\FileLocator Pro\FileLocatorPro.exe',
    r'C:\Program Files\Mythicsoft\FileLocator Pro\FileLocatorPro.exe',
]
FLP_EXE = next((p for p in FLP_CANDIDATES if os.path.isfile(p)),
               FLP_CANDIDATES[0])

# 【v0.2.12】建/更新索引用的命令行工具 flpidx.exe（跟主程序同一目录）。
# 它跟 FileLocatorPro.exe 是两回事：主程序负责搜，flpidx 负责改索引。
# 注意 9.0.3307 那份 Mod 版的 flpidx 是**死的**（-list 直接 exit 2，
# 因为破解只 patch 了 GUI，CLI 没被 patch），必须 9.3 的才是活的。
FLPIDX_CANDIDATES = [p.replace('FileLocatorPro.exe', 'flpidx.exe')
                     for p in FLP_CANDIDATES]
FLPIDX_EXE = next((p for p in FLPIDX_CANDIDATES if os.path.isfile(p)),
                  FLPIDX_CANDIDATES[0])


def flp_source():
    """当前这个引擎是哪来的 —— 状态栏要向用户交代清楚。"""
    try:
        if os.path.samefile(FLP_EXE, BUNDLED_FLP_EXE):
            return '内置引擎（随本程序）'
    except Exception:
        pass
    return '系统安装的 FileLocator Pro'

# 索引定义所在目录（只读）
FLP_CFG_DIR = os.path.join(
    os.environ.get('APPDATA', ''), 'Mythicsoft', 'FileLocatorPro', 'config')
FLP_IDX_GLOB = 'idx_*.xml'

DEFAULT_INDEX = '学术信息索引-Y盘实验版'

# CathayViewer 发布目录（找版本号最高的那个 exe）
VIEWER_DIRS = [
    r'D:\我的软件创作库',
    r'C:\我的软件创作库',
]


def flp_exists():
    return os.path.isfile(FLP_EXE)


def find_viewer():
    """自动找阅读器主程序。

    【v0.2.9 整合】CathayHub 之后两个主程序放在**同一个文件夹**，所以先
    在本程序目录里找 `CathayHub Viewer*.exe`——这才是该联动的那个。
    找不到（单独用旧版 / 目录被挪过）再退回老规矩：发布目录里挑版本号
    最高的 CathayViewer。只读扫描，不改任何东西。找不到返回空串。
    """
    here = app_dir()
    for p in sorted(glob.glob(os.path.join(here, 'CathayHub Viewer*.exe'))):
        if os.path.isfile(p):
            return p
    cands = []
    for base in VIEWER_DIRS:
        if not os.path.isdir(base):
            continue
        for d in sorted(glob.glob(os.path.join(base, 'CathayViewer*'))):
            if not os.path.isdir(d):
                continue
            m = re.search(r'(\d+\.\d+\.\d+)', os.path.basename(d))
            ver = (tuple(int(x) for x in m.group(1).split('.'))
                   if m else (0, 0, 0))
            for p in sorted(glob.glob(os.path.join(d, '*.exe'))):
                cands.append((ver, p))
    if not cands:
        return ''
    cands.sort(reverse=True)
    return cands[0][1]


def find_search_exe():
    """本程序自己的 exe（给 Viewer 反着唤起用）。源码运行时返回空串。"""
    if getattr(sys, 'frozen', False):
        return os.path.abspath(sys.executable)
    return ''


def discover_indexes():
    """扫描 FileLocator 配置，返回 [(索引名, 索引存放路径)]。只读。"""
    out = []
    if not os.path.isdir(FLP_CFG_DIR):
        return out
    for p in sorted(glob.glob(os.path.join(FLP_CFG_DIR, FLP_IDX_GLOB))):
        try:
            s = open(p, encoding='utf-8', errors='replace').read()
        except Exception:
            continue
        name = None
        path = None
        m = re.search(r'<name t="\d+">([^<]*)</name>', s)
        if m:
            name = m.group(1).strip()
        m = re.search(r'<path t="\d+">([^<]*)</path>', s)
        if m:
            path = m.group(1).strip()
        if name:
            out.append((name, path or ''))
    return out


def discover_index_groups():
    """返回索引组：[(组文件路径, [成员索引路径, ...])]。只读。"""
    groups = []
    base = os.path.join(os.environ.get('APPDATA', ''),
                        'Mythicsoft', 'FileLocatorPro', 'Index')
    if os.path.isdir(base):
        for p in sorted(glob.glob(os.path.join(base, '*.xml'))):
            try:
                s = open(p, encoding='utf-8', errors='replace').read()
            except Exception:
                continue
            members = re.findall(
                r'<section name="idxchild_\d+"><[^>]*>([^<]*)</', s)
            if members:
                groups.append((p, members))
    # 安装目录下也可能放了自定义索引组
    alt = r'D:\Program Files\Mythicsoft'
    if os.path.isdir(alt):
        for p in sorted(glob.glob(os.path.join(alt, '*.xml'))):
            try:
                s = open(p, encoding='utf-8', errors='replace').read()
            except Exception:
                continue
            members = re.findall(
                r'<section name="idxchild_\d+"><[^>]*>([^<]*)</', s)
            if members:
                groups.append((p, members))
    return groups


if __name__ == '__main__':
    print('FLP_EXE 存在:', flp_exists())
    print()
    print('可用索引:')
    for n, p in discover_indexes():
        print('  %-32s %s' % (n, p))
    print()
    print('索引组:')
    for g, ms in discover_index_groups():
        print('  %s' % os.path.basename(g))
        for m in ms:
            print('      ', m)
