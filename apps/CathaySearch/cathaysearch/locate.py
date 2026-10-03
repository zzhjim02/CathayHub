# -*- coding: utf-8 -*-
"""单本精确定位：打开 PDF 后在书内找词，给出【页码】。

为什么必须有这一步：FileLocator 只给"文本行号"（如 line 21262），
给不了 PDF 页码；而打开书看的是页码。PyMuPDF 正好补上这一环。

M1 实测：40.6 MB / 400 页的 OCR PDF，全本搜索 0.33 秒（0.8 ms/页）。
"""
import os
from collections import namedtuple

PageHit = namedtuple('PageHit', 'page count word')
Snippet = namedtuple('Snippet', 'page word text')


def _open_doc(path):
    import fitz
    return fitz.open(path)


def find_pages(path, words, max_pages=None, want_snippet=True,
               snippet_chars=70):
    """在一本 PDF 里找词。

    返回 (pages, snippets, meta)
      pages    : [PageHit(page, count, word)]  page 为 1 基页码
      snippets : [Snippet(page, word, text)]   命中处的上下文
      meta     : dict(npages, elapsed, opened, error)
    """
    import time
    words = [w for w in ([words] if isinstance(words, str) else words)
             if w and w.strip()]
    meta = {'npages': 0, 'elapsed': 0.0, 'opened': False, 'error': ''}
    if not os.path.isfile(path):
        meta['error'] = '文件不存在'
        return [], [], meta
    if not words:
        meta['error'] = '检索词为空'
        return [], [], meta

    t0 = time.time()
    try:
        doc = _open_doc(path)
    except Exception as e:
        meta['error'] = '打开失败: %s' % e
        return [], [], meta
    meta['opened'] = True
    meta['npages'] = doc.page_count

    pages, snippets = [], []
    try:
        n = doc.page_count if max_pages is None else min(doc.page_count,
                                                         max_pages)
        for i in range(n):
            page = doc[i]
            for w in words:
                try:
                    rects = page.search_for(w)
                except Exception:
                    rects = []
                if rects:
                    pages.append(PageHit(i + 1, len(rects), w))
                    if want_snippet:
                        try:
                            txt = page.get_text()
                        except Exception:
                            txt = ''
                        idx = txt.find(w)
                        if idx < 0:
                            idx = 0
                        frag = txt[max(0, idx - snippet_chars):
                                   idx + snippet_chars * 2]
                        snippets.append(
                            Snippet(i + 1, w, ' '.join(frag.split())))
    finally:
        doc.close()

    meta['elapsed'] = time.time() - t0
    return pages, snippets, meta


def first_page(path, words, max_pages=None):
    """只要第一个命中的页码（用于"跳到第一次出现处"）。没命中返回 None。"""
    pages, _, _ = find_pages(path, words, max_pages=max_pages,
                             want_snippet=False)
    return pages[0].page if pages else None
