# -*- coding: utf-8 -*-
"""把"这本书里每个词各命中哪些页"记下来，写成 JSON 交给 CathayViewer。

为什么要记
==========
第一段检索（FileLocator 索引）只知道"哪些文件里有这个词"；
只有第二段（PyMuPDF 打开这本 PDF）才知道"在第几页"——而且它是**逐词**扫的，
所以扫完一遍，自然就同时得到了"词 A 在 12/45/88 页、词 B 在 7 页"。

这份数据如果不传给阅读器，用户想换一个词看命中，就得回到检索器重搜一遍。
现在写成一个小 JSON，用 `--hits <路径>` 交给 CathayViewer，
阅读器据此显示"命中词导航条"：挑一个词 → 跳命中页 → 一键换下一个词。

JSON 结构（与 CathayViewer/viewer_hits.py 严格对应）：
    {"file": "X:\\...\\001.pdf",
     "words": [{"word": "吴佩孚", "pages": [12, 45, 88], "count": 27}, ...]}
`pages` 一律 **1 基**页码。
"""
import json
import os
import tempfile
import time

TMP_DIRNAME = 'CathaySearch'
KEEP = 20          # 临时目录里最多留几份清单


def group_by_word(pages):
    """把 locate.find_pages 返回的 [PageHit(page, count, word)] 聚合为按词的清单。

    只收真正命中的词（pages 为空的不写），页码去重升序。
    """
    by = {}
    for h in pages or []:
        w = getattr(h, 'word', '') or ''
        p = getattr(h, 'page', 0)
        c = getattr(h, 'count', 0)
        if not w or int(p or 0) < 1:
            continue
        e = by.setdefault(w, {'pages': set(), 'count': 0})
        e['pages'].add(int(p))
        e['count'] += int(c or 0)
    out = []
    for w, e in by.items():
        out.append({'word': w, 'pages': sorted(e['pages']),
                    'count': e['count'] or len(e['pages'])})
    # 命中页数多的排前面：主词通常比短别名更值得先看
    out.sort(key=lambda d: (-len(d['pages']), -d['count'], d['word']))
    return out


def _tmpdir():
    d = os.path.join(tempfile.gettempdir(), TMP_DIRNAME)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = tempfile.gettempdir()
    return d


def _sweep(d):
    """只留最近 KEEP 份，别让临时目录无限长。"""
    try:
        fs = [os.path.join(d, f) for f in os.listdir(d)
              if f.startswith('hits_') and f.endswith('.json')]
        fs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for p in fs[KEEP:]:
            try:
                os.remove(p)
            except Exception:
                pass
    except Exception:
        pass


def write(file_path, words):
    """写清单文件，返回路径；任何一步失败都返回 ''（不能拖累打开书本这件事）。"""
    if not words:
        return ''
    d = _tmpdir()
    name = 'hits_%d_%d.json' % (os.getpid(), int(time.time() * 1000))
    p = os.path.join(d, name)
    data = {'file': file_path or '',
            'words': [{'word': w['word'], 'pages': list(w['pages']),
                       'count': int(w['count'])} for w in words]}
    try:
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        _sweep(d)
        return p
    except Exception:
        return ''


def brief(words, limit=6):
    """状态栏用的一句话：吴佩孚(3 页)、子玉(1 页)。"""
    if not words:
        return ''
    s = '、'.join('%s(%d 页)' % (w['word'], len(w['pages']))
                 for w in words[:limit])
    if len(words) > limit:
        s += ' 等 %d 个词' % len(words)
    return s
