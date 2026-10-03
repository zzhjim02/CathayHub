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

_CC = {}


def _cc(cfg):
    if cfg not in _CC:
        import opencc
        _CC[cfg] = opencc.OpenCC(cfg)
    return _CC[cfg]


def convert_text(text, act):
    """act: 't2s'（繁→简）/ 's2t'（简→繁）。优先 OpenCC，没有就退回 zhconv。"""
    text = text or ''
    try:
        return _cc(act).convert(text)
    except Exception:
        try:
            import zhconv
            return zhconv.convert(text, 'zh-cn' if act == 't2s' else 'zh-tw')
        except Exception:
            return text


def convert_many(words, act):
    """一串词一起转，保持顺序、去掉转换后变空的项。"""
    out = []
    for w in words or []:
        w2 = convert_text(str(w or '').strip(), act)
        if w2 and w2 not in out:
            out.append(w2)
    return out


# 一个简体字常对应不止一个繁体字形（启 → 啓 / 啟），
# 所以这里不是"简↔繁"两条路，而是四个 OpenCC 配置轮着套。
# CBDB 那张别名表就是混着写的：`梁启超` 一行、`梁啟超` 一行，收的别名
# 还不一样多 —— 少认一个字形就少一半存货。
_CONV_CFGS = ('t2s', 's2t', 's2tw', 's2hk')


def variants_of(word):
    """一个词的全部繁/简变体，**不含自身**；繁简同形返回空表。

    做法：四个方向的转换反复套到不再冒出新词为止（最多三轮）。
    好处是**对称** —— 无论从简体还是繁体出发，得到的都是同一组字形，
    绝不会出现"打简体漏掉繁体那行"的事。重复的一律丢掉。
    """
    w = str(word or '').strip()
    if not w:
        return []
    out = []
    src = [w]
    for _ in range(3):
        grown = False
        for x in src:
            for cfg in _CONV_CFGS:
                v = convert_text(x, cfg)
                v = (v or '').strip()
                if v and v != w and v not in out:
                    out.append(v)
                    grown = True
        if not grown:
            break
        src = [x for x in [w] + out]
    return out


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
