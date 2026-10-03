# -*- coding: utf-8 -*-
"""CathayHub Launcher 入口。

    python CathayHubLauncher.py            # 正常启动
    python CathayHubLauncher.py --selftest # 跑一遍自检（不弹窗，检查完就退出）
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cathayhub import config          # noqa: E402


def _selftest():
    """自检：全部只读，不写任何文件（连界面都是画在内存里的）。"""
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'   # 必须在 QApplication 之前
    from PyQt6.QtWidgets import QApplication

    from cathayhub import detect, filelib, gui
    from cathayhub import path_translator as pt

    ok_n = 0
    bad = []
    lines = []

    def _safe(s):
        """控制台可能是 GBK，状态栏里的彩色圆点（🟡）打不出来会直接崩。

        打印前先按控制台的编码兜一遍；写文件用 utf-8，原样保留。
        """
        try:
            enc = getattr(sys.stdout, 'encoding', '') or 'utf-8'
            return s.encode(enc, 'replace').decode(enc)
        except Exception:
            return s

    def ok(name, cond, extra=''):
        nonlocal ok_n
        if cond:
            ok_n += 1
            line = '  OK   %s %s' % (name, extra)
        else:
            bad.append(name)
            line = '  FAIL %s %s' % (name, extra)
        print(_safe(line))
        lines.append(line)

    print('== 位置 ==')
    ok('程序目录认得出来', os.path.isdir(config.app_dir()), config.app_dir())
    for k in ('search', 'viewer', 'indexer'):
        ok('能找到「%s」那个程序' % k, bool(config.exe_path(k)),
           os.path.basename(config.exe_path(k)))

    print('== 检测 ==')
    idxs = detect.find_indexes()
    ok('扫得到书库索引', len(idxs) > 0, '共 %d 个' % len(idxs))
    items = detect.check_dirs(idxs)
    ok('白名单条目齐全', 0 < len(items) <= len(pt.MAIN_DIRS),
       '%d 条' % len(items))
    ok('每条字段完整',
       all(set(i) >= {'key', 'expected', 'actual', 'found', 'translated',
                      'level'} for i in items))
    ok('主库判定正确（就该在，不该报缺失）',
       any(i['found'] and i['key'] == '学术信息全文检索数据库' for i in items))

    print('== 索引清单 ==')
    rows = detect.describe_indexes(idxs)
    ok('每个索引都说清楚了', bool(rows) and all(
        set(r) >= {'name', 'store', 'books', 'registered', 'joins'}
        for r in rows), '%d 条' % len(rows))
    ok('条目数跟扫到的索引数一致', len(rows) == len(idxs))
    ok('至少有一个索引认得出它收着主书库',
       any('学术信息全文检索数据库' in r['books'] for r in rows))

    print('== 默认检索组 ==')
    # 写进临时账本里试，别弄脏真实的 launcher.json
    import tempfile
    tmpd = tempfile.mkdtemp(prefix='chprobe')
    _oshared, _opath = config.shared_dir, config.settings_path
    try:
        config.shared_dir = lambda: tmpd
        config.settings_path = lambda: os.path.join(tmpd, 'launcher.json')
        okd, errd = detect.save_default_index(idxs)
        ok('默认组写得进去', okd, errd)
        d = config.load_settings()
        ok('默认目标就是「%s」' % detect.GROUP_NAME,
           d.get('default_index') == detect.GROUP_NAME)
        ok('带时间戳（让搜索认得出这是新一轮向导）',
           bool(d.get('default_index_stamp')))
        ok('一个索引都没有时不去动默认值',
           detect.save_default_index([])[0] is False)
    finally:
        config.shared_dir, config.settings_path = _oshared, _opath
        try:
            os.remove(os.path.join(tmpd, 'launcher.json'))
        except Exception:
            pass
        try:
            os.rmdir(tmpd)
        except Exception:
            pass

    print('== 映射文件 ==')
    mp = detect.build_mapping(items)
    ok('格式是 {名字: {expected, actual, translated}}',
       all(set(v) == {'expected', 'actual', 'translated'}
           for v in mp.values()))
    t = pt.translate_path(r'Y:\学术信息全文检索数据库\a\b.pdf', mp)
    ok('没转译的路径原样返回', t[0] == r'Y:\学术信息全文检索数据库\a\b.pdf')

    print('== 主题 ==')
    ok('三种颜色都有内容',
       all(v[1].strip() for v in detect.THEMES.values()),
       '、'.join(v[0] for v in detect.THEMES.values()))

    print('== 界面 ==')
    app = QApplication(sys.argv)
    gui.apply_theme(app)
    w = gui.MainWindow()
    ok('主窗口建得起来', w is not None)
    ok('三个大按钮都在',
       all(hasattr(w, b) for b in ('b_search', 'b_view', 'b_index')))
    ok('有"首次运行向导"入口', hasattr(w, 'b_wizard'))
    ok('状态栏交代了索引个数', '个索引' in (w.status.text() or ''),
       w.status.text())
    wiz = gui.SetupWizard()
    # v0.3.9：文件名索引从详情页挪出来单成一页 → 现在是五步
    ok('向导五步（索引/书库 → 文件名索引 → 建库 → 完成）',
       wiz.pageIds() and len(wiz.pageIds()) == 5,
       '共 %d 步' % len(wiz.pageIds()))
    ok('有"文件名索引"这一步，且排在检测详情之后',
       isinstance(wiz.page(wiz.pageIds()[2]), gui.PageFiledb)
       and isinstance(wiz.page(wiz.pageIds()[1]), gui.PageDetail))
    ok('有"建文件名索引"那一步',
       any(isinstance(wiz.page(i), gui.PageBuild) for i in wiz.pageIds()))
    ok('完成页顺手能挑颜色',
       isinstance(wiz.page(wiz.pageIds()[-1]), gui.PageDone)
       and wiz.theme_key() in detect.THEMES)

    # 第 1 步不该让用户点按钮：进页面就得自己开跑
    p0 = wiz.page(wiz.pageIds()[0])
    ok('第一步那个按钮是禁用的（不用你点）',
       hasattr(p0, 'btn') and not p0.btn.isEnabled())
    import time
    p0.initializePage()
    for _ in range(60):
        app.processEvents()
        time.sleep(0.05)
        if p0._done:
            break
    ok('自动检测自己跑完了', bool(p0._done))
    ok('跑完把索引交给了向导', bool(wiz.index_dirs),
       '%d 个索引' % len(wiz.index_dirs or []))

    # 第 2 步要把"索引"和"书库"都摆出来
    p1 = wiz.page(wiz.pageIds()[1])
    ok('详情页既有索引表又有书库表',
       hasattr(p1, 'tv_idx') and hasattr(p1, 'tv'))
    p1.initializePage()
    app.processEvents()
    ok('索引表里填了东西', p1.tv_idx.rowCount() == len(wiz.index_dirs or []),
       '%d 行' % p1.tv_idx.rowCount())
    ok('书库表里填了东西', p1.tv.rowCount() > 0, '%d 行' % p1.tv.rowCount())
    ok('说明了这些索引会被合成一个组',
       detect.GROUP_NAME in (p1.sum_idx.text() or ''))

    # 建库那一块（v0.3.9：从第 2 步挪到第 3 步，单独一页）：默认扫哪些目录、
    # 库放哪、要不要现在建
    p2 = wiz.page(wiz.pageIds()[2])
    ok('第 3 步就是文件名索引那一页',
       type(p2).__name__ == 'PageFiledb', type(p2).__name__)
    ok('第 2 步不再混着文件名索引（干净了）',
       not any(hasattr(p1, a) for a in ('ck_build', 'lst', 'ed_db')))
    ok('第 2 步看完就交给第 3 步', p1.nextId() == wiz.pageIds()[2],
       'nextId=%d' % p1.nextId())
    p2.initializePage()
    app.processEvents()
    ok('文件名索引那一页有（勾选项 + 目录列表 + 库位置）',
       all(hasattr(p2, a) for a in ('ck_build', 'lst', 'ed_db')))
    ok('要扫的目录默认就是找得到的那些书库',
       p2.lst.count() == len([i for i in wiz.items if i['found']])
       and p2.lst.count() > 0, '%d 个目录' % p2.lst.count())
    ok('库位置默认就是 Viewer 认的那个',
       os.path.basename(p2.ed_db.text()) == config.FILE_DB,
       p2.ed_db.text())
    fst = detect.filename_db_state()
    ok('文件名索引的状态查得出来',
       set(fst) >= {'path', 'has', 'files', 'built_at', 'size'},
       ('已有 %d 个文件' % fst['files']) if fst['has'] else '还没建')
    ok('已有库时默认不勾"现在就建"（免得每次都重扫）',
       p2.ck_build.isChecked() == (not fst['has']))
    ok('勾了才去建库那一步，没勾直接到完成页',
       p2.nextId() == (wiz.pageIds()[3] if p2.ck_build.isChecked()
                       else wiz.pageIds()[4]),
       '勾=%s → 第 %d 页' % (p2.ck_build.isChecked(), p2.nextId()))

    # v0.3.9：点了三个大按钮就把活交出去，Launcher 自己退
    _mw = gui.MainWindow()
    ok('主界面三个大按钮都在',
       all(getattr(_mw, a, None) is not None
           for a in ('b_search', 'b_view', 'b_index')))
    ok('点大按钮会连带关掉 Launcher（交出任务就收工）',
       hasattr(_mw, '_launch') and callable(_mw._launch))
    _mw.close()

    print('== 建库（临时小目录，不碰真书库）==')
    try:
        import tempfile
        td = tempfile.mkdtemp(prefix='chbuild')
        src = os.path.join(td, 'src')
        os.makedirs(os.path.join(src, '子目录'), exist_ok=True)
        for i in range(3):
            open(os.path.join(src, '第%d本.pdf' % i), 'wb').write(b'x')
        open(os.path.join(src, '子目录', '孙层.txt'), 'w',
             encoding='utf-8').close()
        db = os.path.join(td, 't.db')
        r = filelib.build([src], db)
        ok('建库能跑通', r.get('files') == 4, '扫到 %s 个文件' % r.get('files'))
        st2 = detect.filename_db_state(db)
        ok('建完查得到状态和文件数',
           st2['has'] and st2['files'] == 4, '%d 个' % st2['files'])
        ok('认得出这是自己的库', filelib.detect_db(db) == 'cathayviewer')
        # 记账：两份账本都指到临时目录，别弄脏真实的那两个文件
        _op, _os = config.viewer_settings_path, config.settings_path
        try:
            config.viewer_settings_path = lambda: os.path.join(td, 'vs.json')
            config.settings_path = lambda: os.path.join(td, 'launcher.json')
            okf, errf = detect.save_filename_db(db, [src], 4, '2026-10-01')
            ok('建完能记账（Viewer 靠它知道库已经有了）', okf, errf)
            vs = json.load(open(os.path.join(td, 'vs.json'),
                                encoding='utf-8'))
            ok('Viewer 那份设置里写进了库的位置',
               vs.get('primary_db') == db and vs.get('last_count') == 4)
        finally:
            config.viewer_settings_path, config.settings_path = _op, _os
    finally:
        try:
            for root, _d, fs2 in os.walk(td):
                for fn2 in fs2:
                    try:
                        os.remove(os.path.join(root, fn2))
                    except OSError:
                        pass
            os.rmdir(td)
        except Exception:
            pass

    w.close()
    wiz.close()
    app.processEvents()

    tail = ['', '通过 %d，失败 %d' % (ok_n, len(bad))]
    if bad:
        tail.append('失败清单：%s' % bad)
    for t2 in tail:
        print(t2)
        lines.append(t2)
    # 打包成窗口程序后没有控制台，结果得落到文件里才看得见
    try:
        with open(os.path.join(config.app_dir(), '_selftest_launcher.txt'),
                  'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
    except Exception:
        pass
    return 0 if not bad else 1


def main():
    from PyQt6.QtWidgets import QApplication

    from cathayhub import detect, gui

    app = QApplication(sys.argv)
    gui.apply_theme(app)
    s = gui._settings()

    if s.get('first_run'):
        w = gui.SetupWizard()
        if w.exec():
            # 跟主界面那条路走同一套收尾（记账、登记、默认检索组、建库成果），
            # 以前这里漏了"把组设成默认"，Search 还是去搜单个主索引。
            gui.finish_wizard(w)
        else:
            sys.exit(0)

    mw = gui.MainWindow()
    mw.show()
    auto = s.get('auto_open') or ''
    if auto:
        gui.launch(auto)
    sys.exit(app.exec())


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(_selftest())
    main()
