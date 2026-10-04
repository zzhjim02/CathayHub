# -*- coding: utf-8 -*-
"""共享文件同步 —— 保证 CathayViewer 与 CathaySearch 用的是同一套东西。

「一处有修改，另一处也要有修改」不能靠记性：这套脚本把共享文件登记成
「一份权威 + N 份副本」，随时能体检，一键能推平。

    python tools/sync_shared.py --check    只体检，列出来哪份落后了（退出码 1 = 有落后）
    python tools/sync_shared.py --push     把权威覆盖到各副本

新增共享文件时往 MIRRORS 里加一条就行。
"""
import os
import sys
import hashlib
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
DEV = os.path.dirname(HERE)

# 权威文件 → 各 app 里的副本（必须逐字相同）
MIRRORS = {
    # 检索词扩展规则：繁简通搜 + 联想词（CBDB 字号/笔名/化名 + 用户自攒）
    'shared/query_expand.py': [
        'apps/CathayViewer/viewer_query.py',
        'apps/CathaySearch/cathaysearch/query_expand.py',
    ],
    # CBDB 人名别名大表：Viewer 里的那份是权威（CBDB 导入工具写在那里）
    'apps/CathayViewer/config/person_alias.csv': [
        'apps/CathaySearch/cathaysearch/config/person_alias.csv',
    ],
}

# 补充异名表（地名 / 机构 / 译名 / 人物）：权威在 shared/alias_extra/，
# 两个 app 各带一份逐字相同的副本。
for _fn in ('place.csv', 'org.csv', 'foreign.csv', 'figure.csv'):
    MIRRORS['shared/alias_extra/' + _fn] = [
        'apps/CathayViewer/config/alias_extra/' + _fn,
        'apps/CathaySearch/cathaysearch/config/alias_extra/' + _fn,
    ]


def _sha(p):
    h = hashlib.sha256()
    try:
        with open(p, 'rb') as f:
            for b in iter(lambda: f.read(1 << 20), b''):
                h.update(b)
    except OSError:
        return ''
    return h.hexdigest()


def _kb(p):
    try:
        return os.path.getsize(p) / 1024.0
    except OSError:
        return 0.0


def main():
    push = '--push' in sys.argv
    check = '--check' in sys.argv
    if not push and not check:
        print(__doc__)
        return 0
    bad = 0
    for src, dsts in MIRRORS.items():
        sp = os.path.join(DEV, src.replace('/', os.sep))
        if not os.path.isfile(sp):
            print('  缺失  权威文件不见了：%s' % src)
            bad += 1
            continue
        sh = _sha(sp)
        print('权威  %s  (%.1f KB)' % (src, _kb(sp)))
        for d in dsts:
            dp = os.path.join(DEV, d.replace('/', os.sep))
            if not os.path.isfile(dp):
                status = '副本缺失'
                bad += 1
            elif _sha(dp) == sh:
                status = '一致'
            else:
                status = '落后'
                bad += 1
            if status != '一致' and push:
                try:
                    dd = os.path.dirname(dp)
                    if dd and not os.path.isdir(dd):
                        os.makedirs(dd, exist_ok=True)
                    shutil.copyfile(sp, dp)
                    if os.path.isfile(dp) and _sha(dp) == sh:
                        bad -= 1
                        status = '已同步'
                    else:
                        status = '同步后仍不一致'
                except OSError as e:
                    status = '同步失败：%s' % e
            print('    %-8s %s' % (status, d))
    if push:
        print('\n已按权威文件推平全部副本。')
    else:
        print('\n%s' % ('全部一致。' if not bad
                         else '有 %d 份副本落后，跑一次 --push 推平。' % bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
