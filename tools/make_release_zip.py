# -*- coding: utf-8 -*-
"""打发行版 zip。

用法：
    python tools/make_release_zip.py <发行目录> <输出 zip>

为什么要有这个脚本
------------------
打包完的发行目录里会**自动生成**两个文件，它们含有使用者自己的书库路径，
绝不能进发行包：

    shared\\path_mapping.json   书库路径对照表（你的书库在哪个盘、叫什么名字）
    shared\\launcher.json       文件名索引的位置、扫描根目录、上次建库时间

漏掉它们等于把个人目录结构公开发布。所以这里用**黑名单**统一挡掉：
宁可漏带一个能重建的文件，也不能夹带一条别人的路径。

被挡掉的文件都不影响运行 —— Launcher 第一次启动会重新生成它们。
"""
import os
import sys
import zipfile

# 含个人数据 / 不该外发的文件（按文件名匹配）
SKIP_FILES = {
    'cathayviewer_index.db',          # 文件名索引（几百 MB，且含个人书库内容）
    'cathayviewer_settings.json',     # 阅读进度、书签、最近打开
    'path_mapping.json',              # 书库路径对照（含个人目录名）
    'launcher.json',                  # 文件名索引位置与扫描根目录
    'user_alias_blocked.json',
}

# 以这些名字开头的文件也不要（自检输出、日志之类）
SKIP_PREFIX = ('_selftest',)

# 整个目录都跳过
SKIP_DIRS = {
    '__pycache__',
    'incoming',      # 程序间传小信封的临时目录
    '.git',
}

# 扩展名黑名单
SKIP_EXT = {'.pyc', '.log', '.tmp'}


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    src = os.path.abspath(sys.argv[1])
    out = os.path.abspath(sys.argv[2])
    if not os.path.isdir(src):
        print('目录不存在：%s' % src)
        return 2

    # 解压后要落在同名的单层文件夹里，别散一地
    top = os.path.basename(src.rstrip(os.sep + '/')) or 'CathayHub'

    n = skipped = 0
    zf = zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=6)
    with zf:
        for root, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            rel_root = os.path.relpath(root, src).replace(os.sep, '/')
            for fn in files:
                if fn in SKIP_FILES or fn.startswith(SKIP_PREFIX):
                    skipped += 1
                    continue
                if os.path.splitext(fn)[1].lower() in SKIP_EXT:
                    skipped += 1
                    continue
                full = os.path.join(root, fn)
                arc = top + '/' + (fn if rel_root == '.' else rel_root + '/' + fn)
                zf.write(full, arc)
                n += 1
    print('打包完成：%s' % out)
    print('  收进 %d 个文件，按规则挡掉 %d 个（含个人数据的那几个）' % (n, skipped))
    print('  %.1f MB' % (os.path.getsize(out) / 1024 / 1024))
    return 0


if __name__ == '__main__':
    sys.exit(main())
