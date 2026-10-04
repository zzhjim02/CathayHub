# -*- coding: utf-8 -*-
"""补充异名表体检：跟 CBDB 是补充关系，还是真打架？

每次往 `shared/alias_extra/` 里加词条后都该跑一遍：

    python tools/check_alias_extra.py

判据（这是本文件的核心，别改坏）：

  **正名撞车不一定是坏事。** 康熙、鲁迅这些 CBDB 本来就有 —— 只要我补的别名是
  CBDB **没有**的，那就是「增量补充」，良性；只有别名它也全有了，才是该删的重复。
  真正要报警的是**串味**：某个词在 CBDB 里归到**别的**正名下（如「培根」是三个
  清代人的字号），那才会把不相干的实体连起来。

其他体检项：单字别名（误命中率过高）、繁简冗余（繁简通搜已覆盖，收了白收）、
组内重复、跨表重名。
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEV = os.path.dirname(HERE)
EXTRA_DIR = os.path.join(DEV, 'shared', 'alias_extra')
CBDB = os.path.join(DEV, 'apps', 'CathayViewer', 'config', 'person_alias.csv')
sys.path.insert(0, os.path.join(DEV, 'shared'))
import query_expand as QE          # noqa: E402

_SEPS = re.compile(r'[，,、；;/|]')


def read_table(path):
    out = []
    if not os.path.isfile(path):
        return out
    with io.open(path, 'r', encoding='utf-8-sig') as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith('#'):
                continue
            p = [x.strip() for x in _SEPS.split(ln) if x.strip()]
            if len(p) >= 2:
                out.append((p[0], p[1:]))
    return out


def main():
    cbdb_to_canon = {}
    cbdb_aliases = {}
    for canon, alist in read_table(CBDB):
        cbdb_aliases[canon] = set(alist)
        cbdb_to_canon[canon] = canon
        for a in alist:
            cbdb_to_canon.setdefault(a, canon)
    print('CBDB: %d 组 / %d 个词条' % (len(cbdb_aliases), len(cbdb_to_canon)))

    def owner_of(w):
        for f in QE.forms_of(w):
            if f in cbdb_to_canon:
                return cbdb_to_canon[f]
        return None

    def forms_set(words):
        s = set()
        for w in words:
            s.update(QE.forms_of(w))
        return s

    files = sorted(f for f in os.listdir(EXTRA_DIR) if f.endswith('.csv'))
    if not files:
        print('!! %s 里没有 csv' % EXTRA_DIR)
        return 1

    bad = 0            # 真问题：完全重复 / 单字 / 组内重复 / 繁简冗余 / 跨表重名
    same_form = 0      # 同形提示：地名、尊称被 CBDB 当成了某人的字号（多半良性）
    seen_canon = {}
    all_terms = set()

    for fn in files:
        rows = read_table(os.path.join(EXTRA_DIR, fn))
        print('\n=== %s ：%d 组 ===' % (fn, len(rows)))
        for canon, alist in rows:
            if canon in seen_canon:
                print('  [!] 跨表重名：正名「%s」也在 %s 里' % (canon, seen_canon[canon]))
                bad += 1
            else:
                seen_canon[canon] = fn
            all_terms.add(canon)

            short = [a for a in alist if len(a) == 1]
            if short:
                print('  [!] 单字别名（误命中率高）: %s → %s'
                      % (canon, '、'.join(short)))
                bad += len(short)
            dup = [a for a in alist if a == canon or alist.count(a) > 1]
            if dup:
                print('  [!] 组内重复: %s → %s' % (canon, '、'.join(sorted(set(dup)))))
                bad += 1

            owner = owner_of(canon)
            if owner is None:
                pass
            else:
                have = forms_set(cbdb_aliases.get(owner, ()))
                new = [a for a in alist if not (set(QE.forms_of(a)) & have)]
                if new:
                    print('  [补充] %s：CBDB 已有 %d 个，这组新增 %s'
                          % (canon, len(cbdb_aliases.get(owner, ())), '、'.join(new)))
                else:
                    print('  [!] 完全重复：%s 的别名 CBDB 全有，这组该删' % canon)
                    bad += 1

            for a in alist:
                all_terms.add(a)
                oa = owner_of(a)
                if oa is not None and oa != canon and owner != oa:
                    # 只提示，不算失败：中文里地名/尊称被拿去当字号太常见了，
                    # 「北平」是溫紹原的号、「长安」是朱永通的号，删不胜删。
                    # 这类撞车由 canon_group() 的「以补充表为准」规则兜住，
                    # 用户还可以按来源整个关掉。真要看的是前面 [!] 那几项。
                    print('  [同形] %s 的别名「%s」在 CBDB 里也是「%s」的称呼'
                          % (canon, a, oa))
                    same_form += 1

        if not any(True for _ in ()):
            pass

    print('\n=== 繁简冗余（繁简通搜已覆盖，收了只是让检索式变长）===')
    red = 0
    for fn in files:
        for canon, alist in read_table(os.path.join(EXTRA_DIR, fn)):
            forms = set(QE.forms_of(canon))
            for a in alist:
                if a in forms:
                    print('  %s：「%s」的别名「%s」只是另一个字形' % (fn, canon, a))
                    red += 1
    if not red:
        print('  OK  没有纯繁简冗余')
    bad += red

    print('\n总计 %d 个词条；真问题 %d 个，同形提示 %d 处'
          % (len(all_terms), bad, same_form))
    if same_form:
        print('（同形 = 地名/尊称被 CBDB 当成某人的字号，多半良性；'
              '由 canon_group() 的「以补充表为准」兜住，也可按来源关掉）')
    return 0 if bad == 0 else 2


if __name__ == '__main__':
    sys.exit(main())
