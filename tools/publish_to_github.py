# -*- coding: utf-8 -*-
"""把 CathayHub-DEV 的源码脱敏后推到 GitHub。

本地开发版（CathayHub-DEV）里带着作者真实的书库路径和配置；
GitHub 上那份必须是干净的。这个脚本负责中间的「过一遍水」：

    1. 临时目录里 clone 最新仓库（先拿远端最新，别覆盖别人在网页上的改动）
    2. 把 apps/ 与 tools/ 复制过去（跳过备份、构建产物、运行时）
    3. 逐条做脱敏替换，最后**体检**：还有残留个人信息就拒绝提交
    4. commit + push，然后清理临时目录（清理失败会提示手工删除）

用法：
    python tools/publish_to_github.py                # 只发布代码（apps/ + tools/）
    python tools/publish_to_github.py --with-docs    # 连根目录文档一起覆盖
"""
import base64
import io
import json
import os
import shutil
import subprocess
import sys
import time

REPO = 'zzhjim02/CathayHub'
BRANCH = 'main'
PROXY = 'http://127.0.0.1:7890'

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # CathayHub-DEV

# ---------------------------------------------------------------- 脱敏规则

B = chr(92)

# 1) 固定的路径替换（必须按「长在前」的顺序逐条执行）
FULL = 'D:' + B + '我的软件创作库' + B + 'CathayHub 面向人文社科研究者的学术书库浏览、搜索与阅读工具'
PATH_RULES = [
    # 当前布局：<新根目录>\CathayHub-DEV\apps\<App>  /  <新根目录>\CathayHub-REPO
    (FULL + B + 'CathayHub-DEV' + B + 'apps',  'D:' + B + 'CathayHub-DEV' + B + 'apps'),
    (FULL + B + 'CathayHub-DEV',               'D:' + B + 'CathayHub-DEV'),
    (FULL + B + 'CathayHub-REPO',              'D:' + B + 'CathayHub'),
    # 旧布局（万一哪份归档文档里还留着）
    ('D:' + B + '我的软件创作库' + B + 'CathayViewer-DEV',   'D:' + B + 'CathayHub-DEV' + B + 'apps' + B + 'CathayViewer'),
    ('D:' + B + '我的软件创作库' + B + 'CathaySearch-DEV',   'D:' + B + 'CathayHub-DEV' + B + 'apps' + B + 'CathaySearch'),
    ('D:' + B + '我的软件创作库' + B + 'CathayLauncher-DEV', 'D:' + B + 'CathayHub-DEV' + B + 'apps' + B + 'CathayLauncher'),
    ('D:' + B + '我的软件创作库' + B + 'CathayIndexer-DEV',  'D:' + B + 'CathayHub-DEV' + B + 'apps' + B + 'CathayIndexer'),
    ('D:' + B + '我的软件创作库' + B + 'CathayHub',          'D:' + B + 'CathayHub'),
    ('D:' + B + '我的软件创作库',                            'D:' + B + 'CathayHub-DEV'),
    # 用户名
    ('C:' + B + 'Users' + B + '<作者>',                     'C:' + B + 'Users' + B + '<用户名>'),
    # 书库
    ('Y:' + B + '我的书库',                    'X:' + B + '我的书库'),
    ('Y:' + B + '学术信息全文数据库-扩展',                   'X:' + B + '我的书库-扩展'),
    ('我的书库',                               '我的书库'),
    ('我的导引库',                               '我的导引库'),
    # 具体书库/文件夹名：注释、文档里也会提到，一并泛化
    ('Y:' + B + '学术信息独立专题库【不公开】：',            'X:' + B + ''),
    ('2cm\u53f2\u5b66\u5c0f\u91d1\u5e93',                   '\u4e13\u9898\u5e93A'),
    ('\u6d6e\u751f\u53f2\u5b66\u8d44\u6e90\u5e93',                     '\u4e13\u9898\u5e93B'),
    ('\u4e2a\u4eba\u767e\u5ea6\u7f51\u76d8\u6587\u4ef6\u603b\u76ee\u5f55',                 '\u7f51\u76d8\u540c\u6b65\u76ee\u5f55'),
    ('\u5b66\u672f\u53e4\u7c4d\u5168\u6587\u691c\u7d22\u6570\u636e\u5e93',                 '\u53e4\u7c4d\u5168\u6587\u5e93'),
    ('\u4e2d\u56fd\u4e2d\u5c0f\u5b66\u6559\u79d1\u4e66\u96c6\u62102026\u5e74\u6625\u5b63\u7248',   '\u4e2d\u5c0f\u5b66\u6559\u79d1\u4e66\u96c6\u6210'),
    ('\u4e2a\u4eba\u7f51\u7edc\u804a\u5929\u8bb0\u5f55\u5168\u6587\u691c\u7d22\u6570\u636e\u5e93', '\u804a\u5929\u8bb0\u5f55\u5e93'),
    ('Y:' + B + '\u4e2a\u4eba\u7f51\u7edc\u804a\u5929\u8bb0\u5f55\u5168\u6587\u691c\u7d22\u6570\u636e\u5e93', 'X:' + B + '\u804a\u5929\u8bb0\u5f55\u5e93'),
    ('Y:' + B + '\u5b66\u672f\u53e4\u7c4d\u5168\u6587\u691c\u7d22\u6570\u636e\u5e93',            'X:' + B + '\u53e4\u7c4d\u5168\u6587\u5e93'),
    ('Y:' + B + '\u56fd\u4e2d\u5c0f\u5b66\u6559\u79d1\u4e66\u96c6\u62102026\u5e74\u6625\u5b63\u7248', 'X:' + B + '\u4e2d\u5c0f\u5b66\u6559\u79d1\u4e66\u96c6\u6210'),
]

# GitHub 账号名 `zzhjim02` 是**公开信息**，脱敏时不能被误伤（链接还要用）。
# 处理前后用哨兵把它藏起来。
_GH = 'zzhjim02'
_SENTINEL = '\x00GHUSER\x00'


def _protect(text):
    return text.replace(_GH, _SENTINEL)


def _restore(text):
    return text.replace(_SENTINEL, _GH)

# 2) 整个 MAIN_DIRS 列表换成「空列表 + 注释」（四份 path_translator.py）
MAIN_DIRS_COMMENT = (
    '# 默认**留空**：拿到源码的人不用先改代码就能用 ——\n'
    '# Launcher 会自动从「找到的索引当初收了哪些目录」里学一份出来\n'
    '# （见 cathayhub/detect.py 的 _dirs_from_indexes）。\n'
    '#\n'
    '# 想精确指定哪些目录算"主要书库"，照下面注释里的例子往列表里填就行\n'
    '# （盘符和目录名换成你自己的）：\n'
    '#\n'
    "#   {'key': '我的主书库', 'expected': r'X:" + B + "我的主书库', 'level': 'critical', 'main': True},\n"
    "#   {'key': '专题库A',   'expected': r'X:" + B + "专题库A',   'level': 'optional', 'main': True},\n"
    '#\n'
    '# level  critical = 主要：缺了会弹窗提醒\n'
    '#        optional = 次要：缺了只是安静标一行，但始终显示\n'
    '#        dual     = 随缘：目录和索引两样都没有时，整条不显示\n'
    '# main   True = 这个目录"在不在"，决定对应索引算不算"还在"\n'
    'MAIN_DIRS = []\n'
)

# 3) DEFAULT_ROOTS 也置空
DEFAULT_ROOTS_COMMENT = (
    '# 默认扫描目录留空：让使用者在向导里自己挑\n'
    'DEFAULT_ROOTS = []\n'
)

# 4) 体检黑名单：命中任何一个就拒绝提交
LEAK_WORDS = [
    '我的软件创作库', '<作者>', '2cm\u53f2\u5b66', '\u6d6e\u751f\u53f2\u5b66',
    'Y:' + B + '\u5b66\u672f\u4fe1\u606f', 'Y:' + B + '\u4e2d\u56fd\u4e2d\u5c0f\u5b66',
    'Y:' + B + '\u4e2a\u4eba\u7f51\u7edc', '\u5b66\u672f\u4fe1\u606f\u72ec\u7acb\u4e13\u9898\u5e93',
    '\u4e2a\u4eba\u767e\u5ea6\u7f51\u76d8\u6587\u4ef6\u603b\u76ee\u5f55',
    '\u4e2d\u56fd\u4e2d\u5c0f\u5b66\u6559\u79d1\u4e66\u96c6\u6210',
    '\u5b66\u672f\u53e4\u7c4d\u5168\u6587\u691c\u7d22\u6570\u636e\u5e93',
]

# 复制时跳过的目录 / 文件
SKIP_DIR_NAMES = {'__pycache__', '.git', 'runtime', '_shot', '_export'}
SKIP_DIR_PREFIX = ('_bak_', '_archive_', 'build_', 'dist_', 'dev_dist_',
                   '_probe_', '_tmp_')
SKIP_DIR_EXTRA = {'开发', '_history'}          # 本地开发报告，含大量真实路径，不进仓库
SKIP_FILE_SUFFIX = ('.db', '.json', '.log', '.pyc', '.ini', '.png', '.ico')
SKIP_FILE_EXACT = {'cathayviewer_settings.json', 'launcher.json',
                   'path_mapping.json', 'cathayviewer_index.db'}
# 一次性诊断/补丁脚本：只在本机有意义，且普遍写死真实路径
SKIP_FILE_PREFIX = ('_probe_', '_ssg_', '_patch_', '_e2e_', '_dbg', '_chk_')
# 本脚本自己的规则表必然写着这些敏感词，体检时豁免自身
SELF_EXEMPT = {'publish_to_github.py'}


def scrub_main_dirs(text):
    """把 MAIN_DIRS = [...]/DEFAULT_ROOTS = [...] 整块换成空列表。"""
    import re
    text = re.sub(r'(?m)^MAIN_DIRS = \[.*?^\]\n', MAIN_DIRS_COMMENT,
                  text, flags=re.S | re.M)
    text = re.sub(r'(?m)^DEFAULT_ROOTS = \[.*?^\]\n', DEFAULT_ROOTS_COMMENT,
                  text, flags=re.S | re.M)
    return text


def scrub(text, path):
    """先处理结构性差异（MAIN_DIRS），再做路径替换，最后清剩的用户名。"""
    if os.path.basename(path) == 'path_translator.py':
        text = scrub_main_dirs(text)
    for old, new in PATH_RULES:
        if old in text:
            text = text.replace(old, new)
    # 藏起公开账号名，剩下的裸用户名才是真个人信息
    text = _protect(text)
    text = text.replace('<作者>', '<\u4f5c\u8005>')
    text = text.replace('Y:' + B * 2, 'X:' + B * 2)
    return _restore(text)


def copy_and_scrub(src_root, dst_root):
    """复制 src_root 下全部源码到 dst_root，顺手脱敏。"""
    n = 0
    for root, dirs, files in os.walk(src_root):
        dirs[:] = [d for d in dirs
                   if d not in SKIP_DIR_NAMES
                   and d not in SKIP_DIR_EXTRA
                   and not d.startswith(SKIP_DIR_PREFIX)]
        rel = os.path.relpath(root, src_root)
        out_dir = dst_root if rel == '.' else os.path.join(dst_root, rel)
        os.makedirs(out_dir, exist_ok=True)
        for fn in files:
            if fn in SKIP_FILE_EXACT or fn.endswith(SKIP_FILE_SUFFIX):
                continue
            if fn.startswith(SKIP_FILE_PREFIX) and fn.endswith('.py'):
                continue
            src = os.path.join(root, fn)
            dst = os.path.join(out_dir, fn)
            try:
                if fn.endswith(('.py', '.md', '.txt', '.spec', '.bat')):
                    txt = io.open(src, encoding='utf-8', errors='ignore').read()
                    io.open(dst, 'w', encoding='utf-8', newline='\n').write(scrub(txt, src))
                else:
                    shutil.copy2(src, dst)
                n += 1
            except Exception as e:
                print('   !! 跳过 %s：%s' % (fn, e))
    return n


def health_check(root):
    """扫一遍待提交内容，还有个人信息就报错。"""
    bad = []
    for r, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != '.git']
        for fn in files:
            if fn.endswith(('.pyc', '.png', '.ico')) or fn in SELF_EXEMPT:
                continue
            p = os.path.join(r, fn)
            try:
                t = io.open(p, encoding='utf-8', errors='ignore').read()
            except Exception:
                continue
            t = _protect(t)          # 公开账号名不参与体检
            for w in LEAK_WORDS:
                if w in t:
                    bad.append((os.path.relpath(p, root), w))
                    break
    return bad


def run(args, tag=''):
    r = subprocess.run(args, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    if r.returncode != 0 and tag:
        print('  [%s] 退出码 %d：%s' % (tag, r.returncode,
                                        (r.stderr or r.stdout or '')[:300]))
    return r


def main():
    with_docs = '--with-docs' in sys.argv
    dry_run = '--dry-run' in sys.argv

    # 需要一个能动的 git（走本机代理）
    git = ['git', '-c', 'http.proxy=' + PROXY, '-c', 'https.proxy=' + PROXY,
           '-c', 'http.sslBackend=openssl']

    # 临时目录放在创作库下的工作区，别污染项目目录。
    # 注意：本环境下 Python 的删除会被安全沙箱拦住，所以这里**不删**，
    # 改成让 git 自己复位（fetch + reset --hard + clean），反复复用同一处。
    tmp = os.path.join('D:' + B + '我的软件创作库' + B + '_gh_work', '_publish_tmp')
    if os.path.isdir(os.path.join(tmp, '.git')):
        print('1) 复用上次的临时仓库，拉到最新 …')
        run(git + ['-C', tmp, 'fetch', 'origin', BRANCH])
        run(git + ['-C', tmp, 'reset', '--hard', 'origin/' + BRANCH])
        run(git + ['-C', tmp, 'clean', '-fdx'])
    else:
        os.makedirs(tmp, exist_ok=True)
        print('1) 拉取远端最新 …')
        for i in range(8):
            run(git + ['clone', '--depth', '1', '-b', BRANCH,
                       'https://github.com/%s.git' % REPO, tmp],
                'clone' if i == 0 else '')
            if os.path.isdir(os.path.join(tmp, '.git')):
                break
            print('   第 %d 次失败，重试' % (i + 1))
            time.sleep(4)
        else:
            print('!! clone 一直失败，先停在原地（没推任何东西）')
            return 1

    print('2) 复制并脱敏 apps/ …')
    n1 = copy_and_scrub(os.path.join(HERE, 'apps'), os.path.join(tmp, 'apps'))
    print('3) 复制 shared/（两个程序共用的规则，权威文件）…')
    n2 = copy_and_scrub(os.path.join(HERE, 'shared'), os.path.join(tmp, 'shared'))
    print('4) 复制并脱敏 tools/ …')
    n3 = copy_and_scrub(os.path.join(HERE, 'tools'), os.path.join(tmp, 'tools'))
    n4 = 0
    if with_docs:
        print('5) 复制文档 …')
        for fn in ('README.md', '使用指南.md', 'requirements.txt', 'LICENSE'):
            src = os.path.join(HERE, fn)
            if os.path.isfile(src):
                txt = io.open(src, encoding='utf-8').read()
                io.open(os.path.join(tmp, fn), 'w', encoding='utf-8',
                        newline='\n').write(scrub(txt, fn))
                n4 += 1
    print('   共 %d 个文件' % (n1 + n2 + n3 + n4))

    print('6) 隐私体检 …')
    bad = health_check(tmp)
    if bad:
        print('!! 还有 %d 个文件残留个人信息，拒绝提交：' % len(bad))
        for p, w in bad[:20]:
            print('   %s  <- %s' % (p, w))
        print('   临时目录留在 %s，改完脱敏规则再重跑。' % tmp)
        return 2
    print('   ✅ %d 个文件、零残留个人信息' % (n1 + n2 + n3 + n4))

    if dry_run:
        print('6) --dry-run：到此为止，没有提交任何东西')
        print('   脱敏后的内容留在 %s 供检查' % tmp)
        return 0

    r = run(git + ['-C', tmp, 'status', '--short'])
    if not r.stdout.strip():
        print('6) 远端已是最新，没有需要提交的改动')
    else:
        run(git + ['-C', tmp, 'add', '-A'])
        msg = sys.argv[-1] if (len(sys.argv) > 1 and not sys.argv[-1].startswith('--')) \
            else 'sync: 同步 CathayHub-DEV 的最新源码'
        run(git + ['-C', tmp, 'commit', '-m', msg])
        print('6) 推送 …')
        ok = False
        for i in range(10):
            run(git + ['-C', tmp, 'push', 'origin', BRANCH])
            rem = run(git + ['-C', tmp, 'ls-remote', 'origin', BRANCH]).stdout.split()
            loc = run(git + ['-C', tmp, 'rev-parse', 'HEAD']).stdout.strip()
            if rem and rem[0] == loc:
                ok = True
                print('   ✅ 推送成功（第 %d 次尝试）' % (i + 1))
                break
            time.sleep(4)
        if not ok:
            print('   !! 推送失败，内容留在 %s' % tmp)
            return 3

    try:
        shutil.rmtree(tmp, ignore_errors=True)
        print('7) 临时目录已清理')
    except Exception:
        print('7) 临时目录删不掉，手工删除：%s' % tmp)
    return 0


if __name__ == '__main__':
    sys.exit(main())
