# -*- coding: utf-8 -*-
"""CathaySearch —— 学术书库全文搜索专版（基于 FileLocator Pro 索引 + PyMuPDF 定位）。

两段式：
  第一段 flp.run_search     全库倒排检索 → 哪些文件里有（秒级）
  第二段 locate.find_pages  打开具体书 → 在第几页（亚秒级，给页码）
"""
__version__ = '0.1.0'

from . import config          # noqa: F401
from . import flp             # noqa: F401
from . import aggregate       # noqa: F401
from . import locate          # noqa: F401

__all__ = ['config', 'flp', 'aggregate', 'locate', 'search', '__version__']


def search(main, also=(), index=None, timeout=180, exts=None):
    """一步到位的便捷入口。

    返回 (books, hits, meta)
      books: [aggregate.Book]  按书归并后的结果
      hits : [flp.Hit]         原始文件清单
      meta : dict              检索元信息（耗时、表达式、索引等）
    """
    from . import flp as _flp, aggregate as _agg
    expr, boolean = _flp.build_expr(main, also)
    hits, meta = _flp.run_search(expr, index=index, boolean=boolean,
                                 timeout=timeout)
    if exts:
        hits = _agg.filter_ext(hits, exts=exts, exclude=False)
    books = _agg.group_by_book(hits)
    meta['files'] = len(hits)
    meta['books'] = len(books)
    return books, hits, meta
