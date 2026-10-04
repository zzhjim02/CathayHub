# -*- coding: utf-8 -*-
"""命令行入口：最小可跑版。

用法：
  python -m cathaysearch.cli 吴佩孚
  python -m cathaysearch.cli 吴佩孚 --also 吴子玉,蓬莱秀才
  python -m cathaysearch.cli 吴佩孚 --top 20 --ext pdf
  python -m cathaysearch.cli 吴佩孚 --locate 3      # 对第 3 本书定位页码
  python -m cathaysearch.cli --list-index
"""
import sys
import argparse

from . import config, flp, aggregate, locate, alias


def _print_indexes():
    print('可用索引：')
    for n, p in config.discover_indexes():
        mark = ' *' if n == config.DEFAULT_INDEX else ''
        print('  %-32s %s%s' % (n, p, mark))
    print()
    print('索引组：')
    for g, ms in config.discover_index_groups():
        import os
        print('  %s (%d)' % (os.path.basename(g), len(ms)))
        for m in ms:
            print('      ', m)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='cathaysearch', description='学术书库全文搜索（FileLocator Pro 索引）')
    ap.add_argument('word', nargs='?', help='检索词')
    ap.add_argument('--also', default='', help='关联词，逗号分隔（OR 联合检索）')
    ap.add_argument('--index', default=None, help='索引名，默认 %s'
                    % config.DEFAULT_INDEX)
    ap.add_argument('--top', type=int, default=20, help='打印前 N 本书，默认 20')
    ap.add_argument('--ext', default='', help='只保留扩展名，逗号分隔，如 pdf')
    ap.add_argument('--files', action='store_true', help='同时列出每本书的文件')
    ap.add_argument('--locate', type=int, default=0,
                    help='对第 N 本书的首个 PDF 用 PyMuPDF 定位页码')
    ap.add_argument('--list-index', action='store_true', help='列出可用索引后退出')
    ap.add_argument('--timeout', type=int, default=180)
    ap.add_argument('--alias', action='store_true',
                    help='开启联想词模式：按 CBDB 别名表自动展开（默认关闭，'
                         '只搜你输入的那个词）')
    ap.add_argument('--max-alias', type=int, default=8,
                    help='联想词模式下最多取几个，默认 8')
    args = ap.parse_args(argv)

    if args.list_index:
        _print_indexes()
        return 0
    if not args.word:
        ap.print_help()
        return 1

    also = [w for w in args.also.replace('，', ',').split(',') if w.strip()]

    # 默认单关键词：只有显式 --alias 才展开联想词
    auto = []
    if args.alias:
        auto = alias.related(args.word, limit=args.max_alias + 1,
                             include_self=False)
        if auto:
            print('联想词     : %s' % '、'.join(auto[:args.max_alias]))
        auto = auto[:args.max_alias]

    expr, boolean = flp.build_expr(args.word, also + auto)
    print('检索表达式 : %s%s' % (expr, '  (布尔)' if boolean else ''))
    print('索引       : %s' % (args.index or config.DEFAULT_INDEX))
    print('检索中…（-ocn 模式，只取文件清单）')

    hits, meta = flp.run_search(expr, index=args.index, boolean=boolean,
                                timeout=args.timeout)
    if meta['timed_out']:
        print('  ⚠ 超时，结果可能不完整')
    print('耗时       : %.2f 秒' % meta['elapsed'])
    if meta.get('found_text'):
        print('引擎统计   : %s' % meta['found_text'])

    if args.ext:
        exts = {e.strip() for e in args.ext.split(',') if e.strip()}
        hits = aggregate.filter_ext(hits, exts=exts, exclude=False)

    books = aggregate.group_by_book(hits)
    summ = aggregate.summarize(hits)
    print()
    print('命中文件   : %d 个（%s）' % (summ['files'],
                                        aggregate.fmt_size(summ['bytes'])))
    print('归并为书   : %d 本' % len(books))
    if summ['ext']:
        print('类型分布   : ' + ', '.join('%s %d' % (e or '(无)', c)
                                          for e, c in summ['ext'][:6]))
    print()

    print('%-4s %-58s %6s %10s' % ('#', '书名（目录名）', '文件数', '合计大小'))
    print('-' * 84)
    for i, b in enumerate(books[:args.top], 1):
        t = b.title if len(b.title) <= 56 else b.title[:53] + '...'
        print('%-4d %-58s %6d %10s' % (
            i, t, len(b.files), aggregate.fmt_size(b.total_bytes)))
        if args.files:
            for f in b.files[:10]:
                ft = f.name if len(f.name) <= 52 else f.name[:49] + '...'
                print('       - %-52s %10s' % (ft, aggregate.fmt_size(f.size)))
            if len(b.files) > 10:
                print('       ... 另 %d 个' % (len(b.files) - 10))
    if len(books) > args.top:
        print('... 另 %d 本（用 --top 调整）' % (len(books) - args.top))

    if args.locate:
        idx = args.locate - 1
        if idx < 0 or idx >= len(books):
            print('\n--locate %d 超出范围（共 %d 本）' % (args.locate, len(books)))
            return 1
        b = books[idx]
        pdfs = [f for f in b.files
                if f.name.lower().endswith('.pdf')] or b.files
        target = pdfs[0]
        print()
        print('定位：%s' % b.title)
        print('文件：%s' % target.path)
        pages, snips, lm = locate.find_pages(
            target.path, [args.word] + also + auto)
        if lm['error']:
            print('  ✗ %s' % lm['error'])
            return 1
        print('  %d 页 / 搜索 %.2f 秒' % (lm['npages'], lm['elapsed']))
        if not pages:
            print('  书内未找到（索引命中但正文抽取不到，OCR 质量或文本层问题）')
            return 0
        print('  命中 %d 页：%s' % (
            len(pages), ', '.join('P%d' % p.page for p in pages[:25]) +
            (' …' if len(pages) > 25 else '')))
        for s in snips[:3]:
            print()
            print('  [第 %d 页 · %s]' % (s.page, s.word))
            print('  ' + s.text[:200])
    return 0


if __name__ == '__main__':
    sys.exit(main())
