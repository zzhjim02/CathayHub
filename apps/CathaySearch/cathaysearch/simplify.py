# -*- coding: utf-8 -*-
"""繁简转换 —— 规则跟 CathayShelf 保持一致。

用户口径：「繁简转换规则参考 SHELF」。CathayShelf 里那套是 OpenCC 的
`t2s` / `s2t` 两个配置（没装 OpenCC 时退回 zhconv 的 zh-cn / zh-tw），
`_【繁转简】`、`_【简转繁】` 这两个文件名后缀也是那边定下来的 ——
本模块照抄同一套规则，免得两边转出来的字不一样。

只做"字符串级"的转换，不碰任何文件。
"""
import os

S_T2S = '_【繁转简】'
S_S2T = '_【简转繁】'
OUT_VARIANTS = ('_【繁转简】', '【繁转简】', '_【简转繁】', '【简转繁】')

DIR_NAME = {'t2s': '繁转简', 's2t': '简转繁'}

# 具体怎么转，不在这里写 —— 在共享模块 query_expand.py 里（CathayViewer 有
# 一份逐字相同的副本）。改规则只改那里，两边一起生效。
from . import query_expand as _QE          # noqa: E402

# 一个简体字常对应不止一个繁体字形（启 → 啓 / 啟），
# 所以不是"简↔繁"两条路，而是四个 OpenCC 配置轮着套。
_CONV_CFGS = _QE._CONV_CFGS


def convert_text(text, act):
    """act: 't2s'（繁→简）/ 's2t'（简→繁）/ 's2tw' / 's2hk'。优先 OpenCC。"""
    return _QE.convert_text(text, act)


def convert_many(words, act):
    """一串词一起转，保持顺序、去掉转换后变空的项。"""
    return _QE.convert_many(words, act)


def variants_of(word):
    """一个词的全部繁/简变体，**不含自身**；繁简同形返回空表。

    四个方向反复套到不再冒出新词为止 —— 从简体还是繁体出发都得到同一组字形。
    """
    return _QE.variants_of(word)


# ───────────────────────── 文件名判定 ─────────────────────────

def strip_txt(name):
    return name[:-4] if name.lower().endswith('.txt') else name


def is_variant(name):
    """文件名本身是不是转换产物（…_【繁转简】.txt / …【简转繁】.txt）"""
    stem = strip_txt(os.path.basename(name or ''))
    return any(stem.endswith(s) for s in OUT_VARIANTS)


def variant_of(name):
    """是产物就返回它带的后缀，不是则返回 ''。"""
    stem = strip_txt(os.path.basename(name or ''))
    for s in OUT_VARIANTS:
        if stem.endswith(s):
            return s
    return ''


def base_of(name):
    """去掉转换后缀与扩展名，得到"原版"的基名。

    '甲书_【繁转简】.txt' → '甲书'
    """
    stem = os.path.splitext(os.path.basename(name or ''))[0]
    s = variant_of(stem + '.txt')
    return stem[:-len(s)] if s else stem


def originals(path):
    """繁转简 TXT 对应的"原版"：同目录下同基名的 .pdf 与 .txt（不带转换后缀）。

    返回 (pdf, txt)，不存在的是 ''。
    """
    d = os.path.dirname(os.path.abspath(path or ''))
    base = base_of(path)
    if not base:
        return '', ''
    pdf = os.path.join(d, base + '.pdf')
    txt = os.path.join(d, base + '.txt')
    return (pdf if os.path.isfile(pdf) else '',
            txt if os.path.isfile(txt) else '')
