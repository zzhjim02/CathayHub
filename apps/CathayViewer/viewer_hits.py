# -*- coding: utf-8 -*-
"""外部传入的「命中词清单」——CathaySearch 算好，CathayViewer 照着跳。

为什么要这个模块
================
两段式检索里，第一段（FileLocator 索引）只回答"哪些文件里有这个词"，
第二段（PyMuPDF 打开单本）才能回答"在第几页"。CathaySearch 在调起阅读器之前
已经把整本扫过一遍了（400 页约 0.3~3 秒），顺手就能记下**每个词各自命中哪些页**。
这份结果如果不传出来，阅读器就只能拿到一个页码，用户想换一个词搜就得回搜索器重来。

于是约定一个 JSON 交接文件，用 `--hits <路径>` 传给阅读器：

    {
      "file" : "Y:\\书库\\xxx\\001.pdf",      # 1 基页码都是针对这本书
      "words": [
        {"word": "吴佩孚", "pages": [12, 45, 88], "count": 27},
        {"word": "子玉",   "pages": [7],          "count": 3}
      ]
    }

- `pages` 是 **1 基**页码（和人眼看到的页码一致），内部再减 1 交给 PdfView。
- `count` 是整本书里这个词出现的总次数（各页命中数之和），只用于显示。
- 没命中的词**不写进清单**（或 pages 为空），导航条里就不给它占位。

本模块**刻意不 import PyQt6**：导航的算术（下一个词、上一页、循环到头）
是最容易写错又最该被测试的部分，拆出来就能脱离界面单独跑。
"""
import json
import os
from collections import namedtuple

HitWord = namedtuple('HitWord', 'word pages count')


class HitSet(object):
    """一本书里的命中词清单。空清单也要能安全构造（界面靠 __bool__ 判断）。"""

    def __init__(self, words=(), source='', file='', text_mode=False):
        """text_mode=True 是给 TXT 用的：没有页码概念，只记"这个词出现几次"。

        普通模式会把 pages 为空的词剔掉（没命中就不占位），但 TXT 本来就没有
        页码，那样会把整份清单剔成空的。
        """
        self.text_mode = bool(text_mode)
        if self.text_mode:
            self.words = [w for w in (words or []) if getattr(w, 'word', '')]
        else:
            self.words = [w for w in (words or []) if getattr(w, 'pages', None)]
        self.source = source or ''      # JSON 路径，出错时好排查
        self.file = file or ''
        self._err = ''

    # ---------------------------------------------------------------- 构造
    @staticmethod
    def load(path):
        """读 JSON。任何异常都退化成"空清单"，绝不让阅读器起不来。"""
        s = HitSet(source=path or '')
        if not path or not os.path.isfile(path):
            s._err = '清单文件不存在'
            return s
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            s._err = '清单解析失败: %s' % e
            return s
        s.file = (data.get('file') or '') if isinstance(data, dict) else ''
        raw = (data.get('words') or []) if isinstance(data, dict) else []
        out = []
        for it in raw:
            try:
                w = str(it.get('word') or '').strip()
                pages = [int(p) for p in (it.get('pages') or []) if int(p) >= 1]
                cnt = int(it.get('count') or 0)
            except Exception:
                continue
            if not w or not pages:
                continue
            out.append(HitWord(w, sorted(set(pages)), cnt or len(pages)))
        s.words = out
        return s

    @staticmethod
    def from_pages(pages, source=''):
        """直接从 locate.PageHit 列表（page, count, word）聚合，便于单测。"""
        by = {}
        for h in pages or []:
            w = getattr(h, 'word', '')
            if not w:
                continue
            p, c = getattr(h, 'page', 0), getattr(h, 'count', 0)
            if p < 1:
                continue
            e = by.setdefault(w, {'pages': set(), 'count': 0})
            e['pages'].add(int(p))
            e['count'] += int(c or 0)
        return HitSet([HitWord(w, sorted(e['pages']), e['count'])
                       for w, e in by.items()], source=source)

    # ---------------------------------------------------------------- 查询
    def __len__(self):
        return len(self.words)

    def __bool__(self):
        return len(self.words) > 0

    @property
    def error(self):
        return self._err

    def word(self, i):
        if not self.words:
            return ''
        return self.words[max(0, min(i, len(self.words) - 1))].word

    def pages(self, i):
        if not self.words:
            return []
        return list(self.words[max(0, min(i, len(self.words) - 1))].pages)

    def count(self, i):
        if not self.words:
            return 0
        return self.words[max(0, min(i, len(self.words) - 1))].count

    def label(self, i):
        """下拉框里显示的文字：吴佩孚 · 3 页 / 27 处（TXT 没有页码，只报处数）"""
        if not self.words:
            return ''
        if self.text_mode or not self.pages(i):
            return '%s · %d 处' % (self.word(i), self.count(i))
        return '%s · %d 页 / %d 处' % (self.word(i), len(self.pages(i)),
                                       self.count(i))

    def index_of(self, i, page):
        """当前页码在命中页列表里的下标；不在其中返回 -1。"""
        ps = self.pages(i)
        try:
            return ps.index(int(page))
        except ValueError:
            return -1

    def locate(self, page):
        """给定一个 1 基页码，找出它属于哪个词、是该词的第几个命中页。

        找不到（这一页没有命中）返回 (0, 0)：保持当前词不变、回到它的第一页，
        界面上不会突然跳到一个不相干的词。
        """
        for i, w in enumerate(self.words):
            if int(page) in w.pages:
                return i, w.pages.index(int(page))
        return 0, 0

    def step_page(self, i, pi, delta):
        """在当前词内前后翻命中页，到头就回到该词的第一页/最后一页（不越词）。"""
        ps = self.pages(i)
        if not ps:
            return i, 0, None
        pi = (pi + delta) % len(ps)
        return i, pi, ps[pi]

    def step_word(self, i, delta=1):
        """换词（循环）。返回 (新词下标, 该词第一个命中页)。"""
        if not self.words:
            return 0, None
        i = (i + delta) % len(self.words)
        ps = self.pages(i)
        return i, (ps[0] if ps else None)


def dump(path, file_path, words):
    """写清单文件（CathaySearch 侧也用同一份结构，签名保持一致）。

    words: [(word, [pages...], count), ...] 或 [HitWord, ...]
    """
    data = {'file': file_path or '', 'words': []}
    for item in words or []:
        if isinstance(item, HitWord):
            w, pages, cnt = item.word, item.pages, item.count
        else:
            w, pages, cnt = item[0], list(item[1]), int(item[2] if len(item) > 2 else 0)
        if not w or not pages:
            continue
        data['words'].append({'word': w, 'pages': [int(p) for p in pages],
                              'count': int(cnt or len(pages))})
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    return path
