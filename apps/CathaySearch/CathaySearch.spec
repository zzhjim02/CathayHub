# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['CathaySearch.py'],
    pathex=[],
    binaries=[],
    datas=[('cathaysearch\\config\\person_alias.csv', 'cathaysearch\\config')],
    # concurrent.futures：多词并发检索在函数内部延迟 import，显式登记更保险
    # opencc / zhconv：繁简转换（v0.2.6），也是在函数里延迟 import 的
    hiddenimports=['fitz', 'pymupdf', 'concurrent', 'concurrent.futures',
                   'opencc', 'zhconv'],
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
    name='CathaySearch',
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
    name='CathaySearch',
)
