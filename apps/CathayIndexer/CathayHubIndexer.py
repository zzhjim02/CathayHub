# -*- coding: utf-8 -*-
"""CathayHub Indexer 入口。

--selftest：不弹窗口跑一遍自检（打包验证用）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _theme(app):
    """与 CathayHub Search / Viewer 共用同一份外观。"""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     'runtime', 'hub_theme.qss')
    if not os.path.isfile(p):
        # 打包后 runtime 在 exe 同目录
        p = os.path.join(os.path.dirname(sys.executable),
                         'runtime', 'hub_theme.qss')
    if os.path.isfile(p):
        try:
            app.setStyleSheet(open(p, encoding='utf-8').read())
        except Exception:
            pass


def selftest():
    from cathayindexer import config, flp, indexes
    ok = fail = 0

    def ck(name, cond, extra=''):
        nonlocal ok, fail
        if cond:
            ok += 1
            print('OK   %s' % name)
        else:
            fail += 1
            print('FAIL %s  %s' % (name, extra))

    print('== CathayHub Indexer 自检 ==')
    ck('引擎主程序找得到', os.path.isfile(config.FLP_EXE), config.FLP_EXE)
    ck('命令行工具 flpidx 找得到', os.path.isfile(config.FLPIDX_EXE),
       config.FLPIDX_EXE)
    alive, why = flp.flpidx_alive()
    ck('命令行工具能用（9.0 那份是死的）', alive, why[:120])
    try:
        items = indexes.list_all()
        ck('索引列表读得到', isinstance(items, list), type(items).__name__)
        print('     共 %d 条' % len(items))
        for e in items[:12]:
            print('       %-30s %s' % (e.get('name'), e.get('kind')))
        ck('至少有一个索引', len(items) > 0, len(items))
    except Exception as e:
        ck('索引列表读得到', False, str(e))
    print()
    print('通过 %d，失败 %d' % (ok, fail))
    return 1 if fail else 0


def main():
    if '--selftest' in sys.argv:
        return selftest()
    from PyQt6.QtWidgets import QApplication
    from cathayindexer.gui import MainWindow
    os.environ.setdefault('QT_QPA_PLATFORM', '')
    app = QApplication(sys.argv)
    _theme(app)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
