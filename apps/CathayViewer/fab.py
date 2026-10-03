# -*- coding: utf-8 -*-
"""一键发版：打包 → 校验值 → 发行说明骨架。"""
import hashlib
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = 'CathayViewer'
ENTRY = 'gui.py'


def version():
    s = open(os.path.join(HERE, ENTRY), encoding='utf-8').read()
    m = re.search(r"APP_VERSION\s*=\s*'v?([\d.]+)'", s)
    return m.group(1) if m else '0.1.0'


def main():
    v = version()
    if '--skip-build' not in sys.argv:
        r = subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                            '--onefile', '--windowed', '--name', NAME, '--icon', 'app.ico',
                            '--add-data', 'config;config', '--distpath', 'dev_dist',
                            '--workpath', 'dev_build', '--specpath', '.', ENTRY],
                           cwd=HERE, capture_output=True)
        print('build rc', r.returncode)
    exe = os.path.join(HERE, 'dev_dist', NAME + '.exe')
    if not os.path.isfile(exe):
        print('没找到成品 exe：', exe)
        return
    rel = os.path.join(HERE, 'dist', 'Release')
    os.makedirs(rel, exist_ok=True)
    dst = os.path.join(rel, NAME + '.exe')
    shutil.copy2(exe, dst)
    h = hashlib.sha256(open(dst, 'rb').read()).hexdigest()
    open(os.path.join(rel, '校验值.txt'), 'w', encoding='utf-8').write(
        '%s v%s\n%d 字节\nSHA256 %s\n' % (NAME, v, os.path.getsize(dst), h))
    note = os.path.join(rel, '发行说明_v%s.md' % v)
    if not (os.path.isfile(note) and os.path.getsize(note) > 200):
        open(note, 'w', encoding='utf-8').write(
            '# %s v%s\n\n- 这是什么：学术书库浏览与阅读工具（只读）\n'
            '- 包内包含：exe + config + 开发\n- SHA256：%s\n' % (NAME, v, h))
    print('发版完成 v%s ｜ %d 字节 ｜ %s' % (v, os.path.getsize(dst), h))


if __name__ == '__main__':
    import shutil
    main()
