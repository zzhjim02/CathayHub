# -*- coding: utf-8 -*-
"""工具：把 CBDB（中国历代人物传记资料库）导出表 → CathayViewer 人名别名表 CSV。

用法：
  py -3 工具_CBDB导入别名表.py "<CBDB导出目录>" [--no-merge] [--year-from Y] [--year-to Y] [--min-len N]

<CBDB导出目录> 需含 ALTNAME_DATA.xlsx 与 BIOG_MAIN.xlsx（CBDB 官方 per-table 导出即可）。
产出写到 CathayViewer 的 person_alias.csv（与程序设置同目录），程序启动时自动加载。

数据版权归 CBDB（Harvard/北大/中研院）所有；本工具只读你本地的导出，产物仅存本地，不随程序发布。
若要在软件里用：运行本工具生成 CSV 后，在「人名别名表」对话框中「导入 CSV」亦可。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import viewer_alias as A  # noqa: E402


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    folder = argv[0]
    if not os.path.isdir(folder):
        print('目录不存在:', folder)
        return 2
    merge = '--no-merge' not in argv
    kw = {'merge': merge}
    for flag, key in (('--year-from', 'year_from'), ('--year-to', 'year_to'),
                      ('--min-len', 'min_len')):
        if flag in argv:
            try:
                kw[key] = int(argv[argv.index(flag) + 1])
            except Exception:
                pass

    def prog(n, m):
        print('  ... 已扫描 正名 %d / 别名行 %d' % (n, m))
        sys.stdout.flush()

    print('CBDB 导入：', folder)
    p, a, out = A.import_cbdb_dir(folder, on_progress=prog, **kw)
    print('DONE people=%d aliases=%d' % (p, a))
    print('OUT', out)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
