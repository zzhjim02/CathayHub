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
    console=True,
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
