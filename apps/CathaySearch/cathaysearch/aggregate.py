# -*- coding: utf-8 -*-
"""结果聚合：把"一堆命中文件"整理成"哪几本书里出现过"。

按用户确认的口径：不需要提前知道每个文件里有多少项命中，
只要知道"这个词和它的关联词分别出现在哪些文件里"。
所以这里只做：过滤 → 按书（目录）归并 → 排序。
"""
import os
from collections import namedtuple

Book = namedtuple('Book', 'title dir files total_bytes')


def _title_of(dirpath):
    """目录名即书名（书库的组织方式：一书一目录）。"""
    d = (dirpath or '').replace('/', os.sep).rstrip(os.sep)
    if not d:
        return '(根目录)'
    return d.split(os.sep)[-1] or d


def group_by_book(hits, min_files=1):
    """把 Hit 列表按所在目录归并成"书"。
    返回 [Book]，按命中文件数降序，同数按书名排序。
    """
    buckets = {}
    for h in hits:
        d = os.path.dirname(h.path)
        buckets.setdefault(d, []).append(h)
    books = []
    for d, fs in buckets.items():
        if len(fs) < min_files:
            continue
        books.append(Book(
            title=_title_of(d),
            dir=d,
            files=sorted(fs, key=lambda x: x.name),
            total_bytes=sum(f.size for f in fs),
        ))
    books.sort(key=lambda b: (-len(b.files), b.title))
    return books


def filter_ext(hits, exts=None, exclude=True):
    """按扩展名过滤。exts 形如 {'.pdf'}；exclude=False 时改为只保留这些。"""
    if not exts:
        return list(hits)
    exts = {e.lower() if e.startswith('.') else '.' + e.lower()
            for e in exts}
    out = []
    for h in hits:
        ext = os.path.splitext(h.name)[1].lower()
        keep = (ext not in exts) if exclude else (ext in exts)
        if keep:
            out.append(h)
    return out


def summarize(hits):
    """粗粒度统计，用于打印表头。"""
    from collections import Counter
    c = Counter(os.path.splitext(h.name)[1].lower() for h in hits)
    return {
        'files': len(hits),
        'bytes': sum(h.size for h in hits),
        'ext': c.most_common(),
    }


def fmt_size(n):
    n = float(n or 0)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return '%.0f %s' % (n, unit) if unit == 'B' else '%.1f %s' % (n, unit)
        n /= 1024
