# -*- mode: python ; coding: utf-8 -*-
"""CathayHub Launcher 打包配置。

与 Search / Viewer / Indexer 放在**同一个文件夹**，所以：
  · COLLECT 的 name 用 'CathayHub'（产物目录名）；
  · contents_directory 必须改名（默认的 _internal 会和另外三个撞）——
    四个 exe 并列、各自带一份内部文件，互不干扰。

注意：Launcher 不带 FileLocator 引擎（那是外挂的 runtime 目录），
它只负责"找书库、写映射、把另外三个程序拉起来"，所以包很小。
"""
a = Analysis(
    ['CathayHubLauncher.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['sqlite3'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# ------------------------------------------------------------ 瘦身：剔除用不上的库
# 【v0.3.16】上面这些是 PyInstaller 顺着依赖网泛洪拉进来的，源码里一行都没 import，
# 留着只是让包白胖一圈。全部删掉后重跑 --selftest + 全套探针都过，才敢写在这。
import sys as _sys
if SPECPATH not in _sys.path:            # SPECPATH 由 PyInstaller 注入
    _sys.path.insert(0, SPECPATH)
from pack_utils import peel_unused as _peel

# Qt 那部分：PyInstaller 的 PyQt6 hook 是「整个 Qt 目录连锅端」。
# 【关键：只点名 dll 不够】光踢掉 Qt6Svg.dll，imageformats\qsvg.dll 还会照旧进来，
# 它一加载就要找 Qt6Svg.dll，找不到就失败 —— 本机多半只是警告，换台机器可能就是
# "双击没反应"。所以走 peel_unused 做传递闭包：点名的不要，凡是依赖它的也跟着不要。
_QT_DROP = {
    'opengl32sw.dll',     # 软件 OpenGL：桌面 Widgets 走光栅，用不着
    'qt6pdf.dll',         # 读 PDF 一律走 PyMuPDF，不用 QtPdf
    'qt6svg.dll',         # 界面图标是 ico / 字符，没有 SVG 资源
    'qt6network.dll',     # 本程序不联网
}
_QT_KEEP_QM = {'qt_zh_cn.qm', 'qtbase_zh_cn.qm'}


def _tail(name):
    # 用 chr(92) 代替反斜杠字面量：spec 不是标准 .py，写 '\' 容易在
    # 各种读写环节被转义吃掉（吃过一次亏，打包时直接 SyntaxError）。
    parts = [p for p in str(name or '').replace(chr(92), '/').split('/') if p]
    return parts[-1].lower() if parts else ''


a.binaries = _peel(a.binaries, _QT_DROP)
a.datas = [d for d in a.datas
           if not (_tail(d[0]).endswith('.qm')
                   and 'translations' in d[0].lower()
                   and _tail(d[0]) not in _QT_KEEP_QM)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='CathayHub Launcher 统一入口',
    # 内部文件目录改名：默认的 _internal 会和另外三个撞名
    contents_directory='_internal_launcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['app.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='CathayHub',
    contents_directory='_internal_launcher',
)
