# -*- coding: utf-8 -*-
"""CathayViewer · 学术书库浏览与阅读 —— 界面（首启向导 + 主窗口）

只读原则：所有库都以 mode=ro 打开；源书库一个字节都不写。
"""
import io
import json
import os
import re
import sys
import subprocess
import threading
import time
import traceback
from bisect import bisect_right

_FULL_SCAN_MAX = 200      # batch16：PDF 深著录最多全文扫描的页数（超过则只用前后采样）

from PyQt6.QtCore import (Qt, QEvent, QPoint, QPointF, QProcess, QRect, QSize,
                         QThread, QTimer, QUrl, pyqtSignal)
from PyQt6.QtGui import (QColor, QFont, QFontMetrics, QIcon, QImage, QKeySequence, QPainter,
                         QPalette, QPen, QPixmap, QPolygonF, QShortcut, QTextBlockFormat,
                         QTextCursor, QTextDocument, QTextImageFormat)
from PyQt6.QtWidgets import (QApplication, QAbstractScrollArea, QCheckBox, QComboBox, QDialog,
                             QDockWidget, QFileDialog, QLayout,
                             QGroupBox, QSpinBox, QScrollArea, QMenu, QStyle,
                             QStyledItemDelegate, QStyleOptionViewItem,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem,
                             QMainWindow, QMessageBox, QProgressBar, QPushButton,
                             QRadioButton, QSplitter, QStackedWidget, QTableWidget,
                             QTableWidgetItem, QTabWidget, QTextEdit, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

JSON_MAX_BYTES = 20 * 1024 * 1024    # JSON 树：超过此大小回退纯文本（或 ijson 流式）
TEXT_MAX_BYTES = 64 * 1024 * 1024    # 文本阅读上限：超过只载入前 64 MB（状态栏提示，不静默丢弃）
TEXT_DISPLAY_MAX = 8 * 1024 * 1024   # batch16：普通文本阅读最多载入前 8 MB（大 TXT 不再卡界面）
DUAL_TXT_MAX = 8 * 1024 * 1024       # batch16：对读 TXT 最多载入前 8 MB
DUAL_AUTO_MAX = 64 * 1024 * 1024     # batch16：TXT 超过此大小不自动进对读（只提示）
# 可直接拖入 / 双击在阅读区打开的扩展名
READER_EXTS = ('.pdf', '.txt', '.text', '.md', '.epub', '.json', '.csv')

# 无书签的书最多自动生成多少条"第 N 页"目录。超过就只给提示——
# 三千多页的书逐页建一个列表项要几百毫秒，全堵在"打开"这条路上。
NAV_TOC_MAX_PAGES = 600

# v0.3.18：文内查找开联想词时，最多带多少个别名一起去扫。
# CBDB 里有些人的字号能列到三四十个，全带上扫一本大书要好几分钟；
# 16 个是"够用又不至于把人等走"的数（要更多可在别名表里把常用的排在前面）。
FIND_ALIAS_LIMIT = 16

import viewer_core as C
import viewer_meta as META
import viewer_tools as TOOLS
import viewer_chrono as CHRONO
import viewer_rhyme as RHYME          # batch24：电报「代日韵目」表
import viewer_alias as ALIAS
import viewer_hits as HITS             # batch28：外部检索器（CathaySearch）传来的命中词清单
import viewer_query as QE              # v0.3.18：检索词扩展（与 CathaySearch 同一套规则）

APP_TITLE = C.APP_TITLE
APP_VERSION = C.APP_VERSION
TIP_EXAMPLE = '（下面两行只是示例，可改成你自己的目录）'

# 【v0.3.18】检索词扩展（繁简通搜 / 联想词）用的表在哪。
# 规则本身写在 viewer_query.py 里 —— 那是 shared/query_expand.py 的逐字副本，
# CathaySearch 里还有一份同样的（tools/sync_shared.py 保证三份一字不差）：
# 在检索里给某人加过的字号，进到书里查找照样认得。
# 打包之后这张表躺在 `_internal_viewer/config/` 里（PyInstaller 的 datas 落点），
# 而 app_dir() 是 exe 所在的目录 —— 只认 app_dir() 的话，干净机器上就扑空了，
# 联想词只剩个空壳。所以三个位置都排上：开发态、打包态、以及万一 _MEIPASS 改名。
QE.set_csv_paths([
    os.path.join(C.app_dir(), 'config', 'person_alias.csv'),
    os.path.join(getattr(sys, '_MEIPASS', '') or '', 'config', 'person_alias.csv'),
    os.path.join(C.app_dir(), '_internal_viewer', 'config', 'person_alias.csv'),
    os.path.join(C.app_dir(), 'person_alias.csv'),
])
# 【v0.3.19】补充异名表：地名 / 机构 / 译名 / 人物别称。
# CBDB 只管人名字号，这四张小表补的是它管不到的那几类同实异名。
# 一个目录里的每个 csv 就是一个来源，文件名主干即来源名，可单独开关。
QE.set_extra_csv_dirs([
    os.path.join(C.app_dir(), 'config', 'alias_extra'),
    os.path.join(getattr(sys, '_MEIPASS', '') or '', 'config', 'alias_extra'),
    os.path.join(C.app_dir(), '_internal_viewer', 'config', 'alias_extra'),
])
# 用户自己攒的联想词：老版本写在程序目录/runtime 下，搬到两边共用的公共目录
try:
    QE.migrate_legacy([(ALIAS.shared_json_path(), '')])
except Exception:
    pass


# 【v0.3.16】关窗时还没跑完的后台线程寄存在这里。
#
# 它们是些"等不动又杀不掉"的活儿（深著录读整本文字层，大书十几秒）。
# 与其让它们跟着窗口一起被销毁（QThread 运行中被销毁 = 原生崩），不如摘掉
# 父子关系、由这份模块级名单接住，让它自己跑完。名单不清理 —— 一个进程里
# 也就关那么几次窗，留着几个对象比崩一次划算。
_ORPHAN_THREADS = []


def icon_path():
    for p in (os.path.join(getattr(sys, '_MEIPASS', ''), 'app.ico'),
              os.path.join(C.app_dir(), 'app.ico')):
        if p and os.path.isfile(p):
            return p
    return ''


class BuildWorker(QThread):
    """【v0.3.16】建库线程（归宿在 Wizard，见 Wizard.closeEvent）。"""
    prog = pyqtSignal(dict)
    done = pyqtSignal(dict)
    fail = pyqtSignal(str)

    def __init__(self, roots, db, copy_to, exts, fresh=True):
        super().__init__()
        self.roots, self.db, self.copy_to, self.exts = roots, db, copy_to, exts
        self.fresh = fresh
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            fn = C.build if self.fresh else C.refresh
            kw = {'exts': self.exts, 'cancel': lambda: self._stop,
                  'on_progress': lambda p: self.prog.emit(p), 'copy_to': self.copy_to}
            r = fn(self.roots, self.db, **kw) if self.fresh else fn(self.db, self.roots, **kw)
            self.done.emit(r)
        except Exception as e:
            self.fail.emit('%s: %s' % (type(e).__name__, e))


class MetaWorker(QThread):
    """batch16：深著录解析放后台线程（大 PDF 读文字层可达 10s，不能卡 UI）。"""
    done = pyqtSignal(str, float, object)     # path, mtime, meta

    def __init__(self, name, path, mtime, parent=None):
        super().__init__(parent)
        self._n, self._p, self._t = name, path, mtime

    def run(self):
        m = None
        try:
            m = META.parse(self._n or '', self._p)
        except Exception:
            m = None
        try:
            self.done.emit(self._p, float(self._t), m)
        except Exception:
            pass


class Wizard(QDialog):
    """首启向导：① 选目录 ② 选库（自建 / 用已有） ③ 建库进度"""

    def __init__(self, parent=None, settings=None, first_run=True):
        super().__init__(parent)
        self._closing = False
        self.setWindowTitle(('第一次使用 —— ' if first_run else '') + '建立文件名索引')
        self.resize(760, 520)
        self.st = settings or C.load_settings()
        self.result_info = None
        self.worker = None

        self.stack = QStackedWidget()
        self.stack.addWidget(self._page1())
        self.stack.addWidget(self._page2())
        self.stack.addWidget(self._page3())
        lay = QVBoxLayout(self)
        self.lb_head = QLabel('<b>① 选要索引的文件夹</b>')
        lay.addWidget(self.lb_head)
        lay.addWidget(self.stack)
        row = QHBoxLayout()
        self.b_back = QPushButton('← 上一步')
        self.b_next = QPushButton('下一步 →')
        self.b_back.clicked.connect(self._back)
        self.b_next.clicked.connect(self._next)
        row.addStretch(1)
        row.addWidget(self.b_back)
        row.addWidget(self.b_next)
        lay.addLayout(row)
        self._sync()
        if icon_path():
            from PyQt6.QtGui import QIcon
            self.setWindowIcon(QIcon(icon_path()))

    # 【v0.3.16】建库跑到一半被关窗（点 X 或按 Esc）：以前只有「停止」按钮
    # 能掐掉线程，走 X / Esc 那条路的话，worker 跟着对话框一起被销毁 ——
    # QThread 运行中被销毁会原生崩，连个报错都没有，进程直接没了。
    def _halt_worker(self):
        w = getattr(self, 'worker', None)
        self.worker = None
        if w is None:
            return
        try:
            w.stop()
            if w.isRunning():
                w.wait(3000)
        except Exception:
            pass

    def reject(self):
        try:
            self._halt_worker()
        except Exception:
            pass
        try:
            super().reject()
        except Exception:
            pass

    def closeEvent(self, ev):
        try:
            self._halt_worker()
        except Exception:
            pass
        try:
            super().closeEvent(ev)
        except Exception:
            pass

    # ---- 页 1：目录
    def _page1(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel('要索引的书库目录（含子文件夹、孙文件夹）：\n'
                           '<span style="color:#b8860b">%s</span>' % TIP_EXAMPLE))
        self.lst = QListWidget()
        self.lst.setAcceptDrops(True)
        for r in (self.st.get('roots') or C.DEFAULT_ROOTS):
            self.lst.addItem(r)
        v.addWidget(self.lst, 1)
        row = QHBoxLayout()
        b1 = QPushButton('添加文件夹…')
        b2 = QPushButton('移除选中')
        b1.clicked.connect(self._add_dir)
        b2.clicked.connect(lambda: [self.lst.takeItem(i.row())
                                    for i in sorted(self.lst.selectedIndexes(),
                                                    key=lambda x: -x.row())])
        row.addWidget(b1)
        row.addWidget(b2)
        row.addStretch(1)
        v.addLayout(row)
        v.addWidget(QLabel('提示：本地盘 / 移动硬盘 / 网络盘都可以；移动盘换盘符也能认回来。'))
        return w

    def _add_dir(self):
        d = QFileDialog.getExistingDirectory(self, '选要索引的文件夹')
        if d:
            self.lst.addItem(d)

    # ---- 页 2：库
    def _page2(self):
        w = QWidget()
        v = QVBoxLayout(w)
        g = QGroupBox('索引库')
        gv = QVBoxLayout(g)
        self.rb_new = QRadioButton('自建新库（推荐）—— 扫描上面的目录，生成一份文件名索引')
        self.rb_use = QRadioButton('直接用已有的索引库（只读，不改它）'
                                   ' —— 比如别人给的 .db 或另一台机器建的')
        gv.addWidget(self.rb_new)
        gv.addWidget(self.rb_use)
        row = QHBoxLayout()
        self.ed_db = QLineEdit(self.st.get('primary_db') or '')
        b = QPushButton('浏览…')
        b.clicked.connect(self._pick_db)
        row.addWidget(QLabel('主库位置'))
        row.addWidget(self.ed_db, 1)
        row.addWidget(b)
        gv.addLayout(row)
        row2 = QHBoxLayout()
        self.ed_bk = QLineEdit(self.st.get('backup_db') or '')
        b2 = QPushButton('浏览…')
        b2.clicked.connect(self._pick_bk)
        row2.addWidget(QLabel('备查位置'))
        row2.addWidget(self.ed_bk, 1)
        row2.addWidget(b2)
        gv.addLayout(row2)
        gv.addWidget(QLabel('<span style="color:#666">备查位置留空也行；它只当副本，'
                            '主库不可用时自动改读它（比如移动硬盘没插）。</span>'))
        v.addWidget(g)
        v.addWidget(QLabel('<span style="color:#666">“直接用已有的库”支持：本软件的索引库、'
                           'CathayIndex 的 local_files.db。认不出的库会明确提示。</span>'))
        self.rb_new.setChecked(True)
        v.addStretch(1)
        return w

    def _pick_db(self):
        p, _ = QFileDialog.getSaveFileName(self, '索引库放哪儿（.db）',
                                           self.ed_db.text() or 'cathayviewer_index.db',
                                           '索引库 (*.db)')
        if p:
            self.ed_db.setText(p)

    def _pick_bk(self):
        p, _ = QFileDialog.getSaveFileName(self, '备查位置（可留空）',
                                           self.ed_bk.text() or 'index_backup.db',
                                           '索引库 (*.db)')
        if p:
            self.ed_bk.setText(p)

    # ---- 页 3：进度
    def _page3(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.lb_run = QLabel('准备好后点「开始建库」')
        self.pb = QProgressBar()
        self.pb.setRange(0, 0)
        self.pb.setVisible(False)
        self.tx = QTextEdit()
        self.tx.setReadOnly(True)
        v.addWidget(self.lb_run)
        v.addWidget(self.pb)
        v.addWidget(self.tx, 1)
        return w

    # ---- 导航
    def _sync(self):
        i = self.stack.currentIndex()
        self.lb_head.setText(['<b>① 选要索引的文件夹</b>',
                              '<b>② 选索引库放哪儿</b>',
                              '<b>③ 开始建库</b>'][i])
        self.b_back.setEnabled(i > 0 and not self._busy())
        self.b_next.setText('开始建库' if i == 2 else '下一步 →')
        self.b_next.setEnabled(not self._busy())

    def _busy(self):
        return bool(self.worker and self.worker.isRunning())

    def _back(self):
        if not self._busy():
            self.stack.setCurrentIndex(max(0, self.stack.currentIndex() - 1))
            self._sync()

    def _next(self):
        i = self.stack.currentIndex()
        if i < 2:
            self.stack.setCurrentIndex(i + 1)
            self._sync()
            return
        self._start()

    def _start(self):
        if self._busy():
            return
        roots = [self.lst.item(k).text() for k in range(self.lst.count())]
        db = self.ed_db.text().strip()
        if self.rb_use.isChecked():
            if not os.path.isfile(db):
                QMessageBox.warning(self, '提示', '这个索引库文件不存在：\n%s' % db)
                return
            kind = C.detect_db(db)
            if not kind:
                QMessageBox.warning(self, '提示',
                                    '这个库我读不了（不认识的库型）。\n'
                                    '支持：本软件的索引库、CathayIndex 的 local_files.db')
                return
            self.result_info = {'used_existing': True, 'db': db, 'kind': kind}
            self.accept()
            return
        if not roots:
            QMessageBox.warning(self, '提示', '至少加一个要索引的文件夹。')
            return
        self.pb.setVisible(True)
        self.lb_run.setText('正在扫描…（只读，不会改动源书库）')
        self._sync()
        self.worker = BuildWorker(roots, db, self.ed_bk.text().strip(),
                                  C.EXTS, fresh=True)
        self.worker.prog.connect(self._on_prog)
        self.worker.done.connect(self._on_done)
        self.worker.fail.connect(self._on_fail)
        self.worker.start()
        self.b_stop = QPushButton('停止')
        self.b_stop.clicked.connect(lambda: self.worker and self.worker.stop())
        self.layout().addWidget(self.b_stop)

    def _on_prog(self, p):
        if p.get('stage') == 'scan':
            self.tx.append('已扫 %s 个文件…  %s' % (p.get('n'), p.get('dir', '')[:70]))
        elif p.get('stage') == 'skip':
            self.tx.append('跳过（目录不存在）：%s' % p.get('root'))

    def _on_done(self, r):
        self.pb.setVisible(False)
        mb = r.get('files', 0) * 1.8 / 1024.0
        self.lb_run.setText('建库完成 ✓')
        self.tx.append('共 %s 个文件，索引约 %.1f MB，用时 %ss\n库：%s'
                       % (r.get('files'), mb, r.get('secs'), r.get('db')))
        st = C.load_settings()
        st.update({'primary_db': r.get('db'), 'backup_db': self.ed_bk.text().strip(),
                   'roots': [self.lst.item(k).text() for k in range(self.lst.count())],
                   'last_count': r.get('files', 0)})
        C.save_settings(st)
        self.result_info = {'used_existing': False, 'info': r, 'settings': st}
        self.accept()

    def _on_fail(self, msg):
        self.pb.setVisible(False)
        self.lb_run.setText('建库失败')
        self.tx.append('失败：%s' % msg)
        self._sync()


# ======================================================================
# batch7：全新 PDF 阅读内核（平滑滚动 + 文字层选择/复制）
#          + 阅读器独立窗口 + 跨文件全文检索（独立进程）
# ======================================================================

def _is_cjk(ch):
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0xF900 <= o <= 0xFAFF
            or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF or 0x3040 <= o <= 0x30FF
            or 0x2018 <= o <= 0x201F)


def _join_words(words):
    """把 PyMuPDF 的词按阅读顺序拼回文本：拉丁词之间补空格，中日文不补。"""
    out = []
    prev = ''
    for w in words:
        if out and prev:
            a, b = prev[-1], w[0]
            if (not _is_cjk(a)) and (not _is_cjk(b)) and a.isalnum() and b.isalnum():
                out.append(' ')
        out.append(w)
        prev = w
    return ''.join(out)


def read_bytes(p, limit=0):
    """只读原始字节：limit=0 时大文件用 mmap 映射后整块取出；limit>0 只读前 N 字节。"""
    try:
        size = os.path.getsize(p)
    except OSError:
        size = 0
    if limit and size > limit:
        with open(p, 'rb') as f:
            return f.read(limit)
    if size >= 4 * 1024 * 1024:
        import mmap
        with open(p, 'rb') as f:
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                return bytes(mm)
    with open(p, 'rb') as f:
        return f.read()


def decode_bytes(b):
    """把字节解码为文本；chardet 优先，回退常见中文编码。"""
    if not b:
        return ''
    enc = ''
    try:
        import chardet
        enc = (chardet.detect(b[:262144]).get('encoding') or '').lower()
    except Exception:
        enc = ''
    if enc in ('gb2312', 'gbk', 'gb18030'):
        enc = 'gb18030'
    cands = []
    for e in ([enc] if enc else []) + ['utf-8-sig', 'utf-8', 'gb18030', 'big5']:
        if e and e not in cands:
            cands.append(e)
    for e in cands:
        try:
            return b.decode(e)
        except (UnicodeDecodeError, LookupError):
            continue
    return b.decode('utf-8', 'replace')


def _ctx_text(t, j, n, span=30):
    a = max(0, j - span)
    b = min(len(t), j + n + span)
    s = t[a:b].replace('\n', ' ').replace('\r', ' ').strip()
    return ('…' if a > 0 else '') + s + ('…' if b < len(t) else '')


class FlowLayout(QLayout):
    """会自动换行的横向布局（batch22）。

    窗口变窄时，放不下的按钮自动折到下一行 —— 这样每个按钮都能保住自己的
    sizeHint，文字不会被 Qt 挤成省略号。移植自 Qt 官方 FlowLayout 示例。
    """

    def __init__(self, parent=None, margin=0, spacing=6):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    def addItem(self, item):
        self._items.append(item)

    def addStretch(self, *args, **kwargs):
        """弹簧：宽裕时把后面的按钮推到本行右侧；地方不够时自动失效（不会挤坏）。

        这样原来 `row.addStretch(1)` + 「关闭」按钮靠右的写法，改成自动换行后
        视觉上仍然一致。
        """
        try:
            from PyQt6.QtWidgets import QSpacerItem, QSizePolicy
            self._items.append(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding,
                                           QSizePolicy.Policy.Minimum))
        except Exception:
            pass

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do_layout(QRect(0, 0, int(w), 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect, test_only):
        from PyQt6.QtWidgets import QSpacerItem
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = eff.x(), eff.y(), 0
        sp = self.spacing()
        for it in self._items:
            if isinstance(it, QSpacerItem):          # 弹簧：吃掉本行剩余空间
                rest = eff.right() - x
                if rest > 0:
                    if not test_only:
                        it.setGeometry(QRect(x, y, rest, max(1, line_h)))
                    x += rest
                continue
            hint = it.sizeHint()
            w, h = hint.width(), hint.height()
            if line_h > 0 and x + w > eff.right() + 1:
                x = eff.x()
                y += line_h + sp
                line_h = 0
            if not test_only:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += w + sp
            line_h = max(line_h, h)
        return y + line_h - rect.y() + m.bottom()


SPLIT_ARROW_W = 20                       # 拆分按钮最右边「▾」那一块的宽度
_SPLIT_PAD = '     '                     # 文字尾巴补的空格：文字居中，不补三角会压住最后一字


def _event_x(e):
    """鼠标事件的 x 坐标（PyQt6 推荐 position()，老版本只有 x()）。"""
    try:
        return int(e.position().x())
    except Exception:
        return int(e.x())


class SplitButton(QPushButton):
    """【v0.3.9】一个按钮干两件相近的事：主体是"上次挑的那个"，▾ 换另一个。

    工具栏上「复制文本 / 复制格式文本」、「复制引用 / 脚注」其实是同一件事的两个
    变体，各占一个按钮既费宽度又让人先纠结点哪个。合起来之后：
      · 点主体 —— 直接上次那个（默认第一项）；
      · 点 ▾   —— 弹出菜单挑另一个，挑完它就变成新的主体，下次一按就是它。

    ▾ 不是独立控件，是**画在按钮最右边**的一小块（一条分隔线 + 一个实心三角）：
    早先用的是一个真的 QPushButton，结果在各套主题下它就是个突兀的白框，看着像
    "没画完"，没人知道那块能点。现在它跟按钮同底色同高，鼠标移上去整块微微发亮，
    提示语里也把"点右边 ▾ 换一种方式"写清楚了。
    """
    triggered = pyqtSignal(int)          # 换了第几项（0 基）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.main = self                 # 兼容老写法（btn_cite.main.text()）
        self._acts = []                  # [(按钮文字, 说明, 回调), …]
        self._menu = QMenu(self)
        self._menu.aboutToShow.connect(self._sync_menu)
        self._cur = 0
        self._hover_arrow = False
        self.setMouseTracking(True)
        self.clicked.connect(self._run)
        try:
            self.setMinimumHeight(22)
        except Exception:
            pass

    # ---- 组装 -----------------------------------------------------------
    def add_action(self, text, tip, fn):
        """加一种方式；第一次加进去的那个顺便当默认的主体。"""
        a = self._menu.addAction(text)
        a.setCheckable(True)             # 菜单里给"现在这个"打个勾
        try:
            a.setToolTip(tip)
        except Exception:
            pass
        self._acts.append((text, tip, fn))
        i = len(self._acts) - 1
        a.triggered.connect(lambda _c=False, _i=i: self._pick(_i))
        if i == 0:
            self._apply_main(0)
        return a

    def set_current(self, i):
        """只改默认项、不立刻执行（读设置恢复上次选择时用）。"""
        if 0 <= i < len(self._acts):
            self._cur = i
            self._apply_main(i)

    def current_index(self):
        return int(self._cur)

    def actions(self):
        return list(self._acts)

    # ---- ▾ 那一块 -------------------------------------------------------
    def _arrow_rect(self):
        return QRect(self.width() - SPLIT_ARROW_W, 0, SPLIT_ARROW_W, self.height())

    def _in_arrow(self, x):
        return x >= self.width() - SPLIT_ARROW_W

    def _popup(self):
        try:
            self._menu.exec(self.mapToGlobal(
                QPoint(self.width() - SPLIT_ARROW_W, self.height())))
        except Exception:
            pass

    def _sync_menu(self):
        """菜单打开时，给"现在用的就是这个"打个勾。"""
        try:
            for i, a in enumerate(self._menu.actions()):
                a.setChecked(i == self._cur)
        except Exception:
            pass

    # ---- 画 -------------------------------------------------------------
    def paintEvent(self, e):
        super().paintEvent(e)            # 先让主题把按钮画好，我们只补右边那一块
        if len(self._acts) < 2:
            return
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            r = self._arrow_rect()
            pal = self.palette()
            en = self.isEnabled()
            if self._hover_arrow and en:
                # 浅色主题叠浅灰、深色主题叠浅白 —— 两套主题下都看得出来
                light = pal.color(QPalette.ColorRole.Window).lightness() > 128
                p.fillRect(r.adjusted(1, 1, -1, -1),
                           QColor(0, 0, 0, 30) if light else QColor(255, 255, 255, 40))
            line = pal.color(QPalette.ColorRole.Mid)
            ink = pal.color(QPalette.ColorRole.ButtonText) if en else line
            p.setPen(QPen(line, 1))
            p.drawLine(r.left(), 4, r.left(), self.height() - 5)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink)
            cx = r.center().x()
            cy = self.height() / 2.0
            p.drawPolygon(QPolygonF([QPointF(cx - 3.5, cy - 1.5),
                                     QPointF(cx + 3.5, cy - 1.5),
                                     QPointF(cx, cy + 2.5)]))
            p.end()
        except Exception:
            pass

    # ---- 左半边执行、右半边换方式 ---------------------------------------
    def mousePressEvent(self, e):
        try:
            if self._in_arrow(_event_x(e)):
                self._popup()            # 点 ▾ 只弹菜单，不执行动作
                return
        except Exception:
            pass
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        try:
            h = self._in_arrow(_event_x(e))
            if h != self._hover_arrow:
                self._hover_arrow = h
                self.update()
        except Exception:
            pass
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        if self._hover_arrow:
            self._hover_arrow = False
            self.update()
        super().leaveEvent(e)

    # ---- 执行 -----------------------------------------------------------
    def _pad_spaces(self):
        """文字尾巴要补几个空格：按当前字体算出约 24px 的留白，
        让文字与右边那条分隔线之间永远隔得开（不同字体空格宽度差很多）。"""
        try:
            fm = QFontMetrics(self.font())
            per = max(fm.horizontalAdvance(' '), 1)
            return ' ' * max(4, min(12, int(round(24.0 / per))))
        except Exception:
            return _SPLIT_PAD

    def _apply_main(self, i):
        try:
            t, tip, _fn = self._acts[i]
        except Exception:
            return
        self.setText(t + self._pad_spaces())
        self.setToolTip('%s\n（点按钮最右边的 ▾ 换一种方式，挑过之后它就成默认的了）'
                        % tip)

    def _pick(self, i, run=True):
        if not (0 <= i < len(self._acts)):
            return
        self._cur = i
        self._apply_main(i)
        self.triggered.emit(i)
        if run:
            self._run()

    def _run(self):
        try:
            _t, _tip, fn = self._acts[self._cur]
            fn()
        except Exception:
            pass

    def sizeHint(self):
        try:
            s = super().sizeHint()
            return QSize(s.width() + 8, max(s.height(), 22))
        except Exception:
            return QSize(90, 22)


class VerComboBox(QComboBox):
    """【v0.3.9】版本下拉：收起显示简称（原版PDF / 繁体TXT），展开才亮全称。

    一本书的版本全称动辄四五十个字，平时摆在工具栏上会把整行挤到第二行去。
    多数时候只要知道"现在看的是原版 PDF 还是繁体 TXT"就够了 —— 真要挑文件时，
    点开来再给它看全称。
    """
    ROLE_SHORT = Qt.ItemDataRole.UserRole + 1
    ROLE_FULL = Qt.ItemDataRole.UserRole + 2

    def add_version(self, short, full):
        self.addItem(short)
        i = self.count() - 1
        self.setItemData(i, short, self.ROLE_SHORT)
        self.setItemData(i, full, self.ROLE_FULL)
        return i

    def full_text(self, i):
        try:
            return str(self.itemData(i, self.ROLE_FULL) or '')
        except Exception:
            return ''

    def short_text(self, i):
        try:
            return str(self.itemData(i, self.ROLE_SHORT) or '')
        except Exception:
            return ''

    def _swap_full(self):
        """展开前：把每一项换成「简称 ｜ 文件名」。"""
        try:
            for i in range(self.count()):
                f = self.full_text(i)
                if f:
                    self.setItemText(i, f)
        except Exception:
            pass

    def _swap_short(self):
        """收起前：换回简称，否则按钮上会一直留着长串文件名。"""
        try:
            for i in range(self.count()):
                s = self.short_text(i)
                if s:
                    self.setItemText(i, s)
        except Exception:
            pass

    def showPopup(self):
        self._swap_full()
        super().showPopup()

    def hidePopup(self):
        self._swap_short()
        super().hidePopup()


SIZE_PROBE_PAGES = 32      # 抽样页数：跨全书均匀取，尺寸一致就先按它搭首屏
SIZE_GUESS_RATIO = 0.90    # 抽样里"众数"占到这个比例，就敢先拿它当全书尺寸
SIZE_GUESS_DEADLINE = 1.2  # 秒：就算尺寸不齐，扫到这个点也先把首屏交给用户
SCAN_START_DELAY_MS = 120  # 书装载完先让首屏画几帧，再开扫词线程
PREFETCH_MIN_MB = 8        # 大于等于这么大的书，开档前先顺序预读一遍（拉进系统缓存）
WORDS_CHUNK_PAGES = 64     # 扫词每扫够这么多页就把"目前的命中"先发出来
WORDS_NO_TEXT_PAGES = 24   # 前这么多页一个字都没有 → 认定是扫描版，立即收工
TWIN_MIN_PAGE_W = 400      # v0.3.9：双页并排时每页至少这么宽（px），再窄就退回单页
# ---- v0.3.16：开档进度条
PREFETCH_REPORT_BYTES = 6 * 1024 * 1024   # 预读每读够这么多就往外报一次进度
OPEN_PROG_STEP_PAGES = 24                 # 扫尺寸每这么多页报一次进度
OPEN_PROG_SHOW_MS = 160                   # 少于这么久就开完的书，进度条不露面（免得闪）
TEXT_WARMUP_DELAY_MS = 600    # v0.3.14：开完书隔这么久再后台抽全文（避开首屏抢 IO）
TEXT_WARMUP_MIN_PAGES = 60    # 页数比这还少的书抽得飞快，用不着专门预热
TEXT_WARMUP_MAX_WAIT_MS = 8000  # 前台等后台抽完的最长时间，超时就自己来
FIND_FLUSH_MIN_MS = 80        # v0.3.15：「所有关键词」每隔这么久就把已找到的结果推给界面一次


def _env_flag(name):
    """环境变量开关：设成 1 就打开，用来在出问题时一键退回老路子。"""
    return str(os.environ.get(name, '') or '').strip().lower() in ('1', 'on', 'true', 'yes')


class OpenProgress(QWidget):
    """【v0.3.16】开档进度：贴在阅读区顶端的一条细线，学着浏览器的样子。

    Qt 自带的 QProgressBar 得在布局里占一行，做不出这种"浮在内容顶端"的样子，
    干脆自己画：一条淡底 + 按比例填充的一条实线。

    **两条讲究：**
    · 只走真实进度 —— 每一格都由后台线程回报（预读多少字节 / 扫到第几页），
      绝不用定时器假装往前爬：假的一遇冷读大书（七八秒）立刻露馅。
    · 显示值追着目标值跑（差得远走得快、快到了走得慢）—— 底层回报其实是跳变
      的，缓过这一道之后看着就是蹭蹭往前爬，而不是一格一格蹦。

    开得太快的书（不到 OPEN_PROG_SHOW_MS 就完了）干脆不露面，免得闪一下。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._v = 0.0            # 现在画到哪
        self._t = 0.0            # 目标是到哪
        self._done = False
        self._tm = QTimer(self)
        self._tm.setInterval(16)            # ≈60fps
        self._tm.timeout.connect(self._tick)
        try:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        except Exception:
            pass
        self.setFixedHeight(3)
        self.hide()

    # ---- 对外：begin / to / finish
    def begin(self):
        self._v, self._t, self._done = 0.0, 0.02, False
        self._place()
        self._tm.start()
        try:
            QTimer.singleShot(OPEN_PROG_SHOW_MS, self._maybe_show)
        except Exception:
            pass

    def to(self, frac):
        """只许前进，不许倒退（概率公理：用户的进度条往回缩是最伤士气的）。"""
        try:
            f = max(0.0, min(0.94, float(frac)))
        except Exception:
            return
        self._t = max(self._t, f)

    def finish(self):
        self._t = 1.0
        self._done = True

    # ---- 内部
    def _maybe_show(self):
        if self._done:
            return
        self._place()
        self.show()
        self.raise_()

    def _tick(self):
        if self._t >= 0.10 and not self.isVisible():
            self._place()
            self.show()
            self.raise_()
        if self._v >= 1.0:
            self._tm.stop()
            try:
                QTimer.singleShot(240, self.hide)     # 到顶停一小会儿再收
            except Exception:
                pass
            self.update()
            return
        d = self._t - self._v
        self._v = self._t if abs(d) < 0.004 else (self._v + d * 0.18 + 0.0022)
        self.update()

    def _place(self):
        p = self.parent()
        if p is not None:
            try:
                self.setGeometry(0, 0, max(1, int(p.width())), 3)
            except Exception:
                pass

    def paintEvent(self, ev):
        try:
            from PyQt6.QtGui import QPainter, QColor
        except Exception:
            return
        try:
            w, h = self.width(), self.height()
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            x = int(w * max(0.0, min(1.0, self._v)))
            if x > 0:
                p.fillRect(0, 0, x, h, QColor('#2E7BD6'))
            p.end()
        except Exception:
            pass


class PdfPrefetchThread(QThread):
    """【v0.3.7】开档前先把整本书**顺序**读一遍。

    为什么有用：PyMuPDF 解析 PDF 是满文件随机跳转（页树、对象流、图像流……），
    冷读一本 120 MB 的书实测要 11 秒——绝大部分时间花在磁头来回跑上，而不是
    算。而同一个文件**顺序**读一遍往往只要两三秒。先把文件整段拽进系统缓存，
    后面 fitz 的随机访问就全部命中内存，于是"第一个 PDF 特别慢"基本消失。

    只读不写、随时可掐；书小、或设了 CV_NOPREFETCH=1 就根本不启动。

    【v0.3.16】顺手往外报"读了多少"（progress 信号）—— 这一步往往是开大书时
    最久的一段，以前这段时间除了"请稍候"什么也没有，现在进度条能跟着爬。
    """
    progress = pyqtSignal(float)      # 已读比例 0~1

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self._path = path or ''
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            total = max(1, os.path.getsize(self._path))
            got = 0
            _reported = 0
            with open(self._path, 'rb') as f:
                while not self._stop:
                    b = f.read(1 << 20)          # 1 MB 一块
                    if not b:
                        break
                    got += len(b)
                    if got - _reported >= PREFETCH_REPORT_BYTES:
                        _reported = got
                        self.progress.emit(min(1.0, got / float(total)))
        except Exception:
            pass


class PdfOpenWorker(QThread):
    """【v0.3.3】后台打开 PDF：fitz.open + 逐页取尺寸。

    实测一本 120 MB / 648 页的书：fitz.open 约 0.74 秒、逐页扫尺寸约 1.10 秒，
    2000 页的大书还要翻几倍。这两步原先全在主线程里跑，期间窗口不重绘也不
    响应，Windows 直接给盖上"无响应"——就是用户说的"打开 PDF 后卡死几秒"。

    挪到后台后，主线程转一个 QEventLoop 等结果：窗口照常重绘、状态栏照常刷新，
    看不出卡住；而调用方拿到的仍是同步返回值，上下游一行都不用改。

    【v0.3.4】再砍一刀：尺寸的"全本逐页扫"其实经常是白等——书里绝大多数页
    共用同一套版心。于是先看前 `SIZE_PROBE_PAGES` 页，若尺寸完全一致就**先把
    首屏搭出来**（省掉那 1.1 秒），后台接着把剩下的扫完；真扫出不一样的页，
    再发 `refined` 让界面纠正一次。尺寸本来就不齐的书，照老路等全量。

    【v0.3.7】把"等全量"这种情况再压一压（就是用户说的"打开 PDF 很慢"）：
      · 抽样改成**跨全书均匀取**（前几页可能正好是特殊页，以前那样取样会误判
        成"尺寸不齐"，于是白等全本 11 秒）；
      · 判定放宽：完全一致，或者众数占到 `SIZE_GUESS_RATIO` 以上，都先交首屏；
      · 兜底硬期限 `SIZE_GUESS_DEADLINE`：真遇上尺寸千奇百怪的书，扫到这个点
        也不再让用户干等，先拿众数把首屏搭出来，剩下的后台扫完再纠正。
    """
    ready = pyqtSignal(object, object)     # (fitz.Document, [(宽pt, 高pt), …])
    refined = pyqtSignal(object)           # 全量尺寸（只有"先猜了"才会再发一次）
    failed = pyqtSignal(str)
    # 【v0.3.16】开档进度：('doc', 0, 0) = 解析完了；('page', 已扫页数, 总页数)
    progress = pyqtSignal(str, int, int)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path
        self._guessed = False
        self._reported = 0
        self._stop = False

    def stop(self):
        """【v0.3.16】关窗 / 换书时掐掉它（以前没有，只能干等它扫完）。"""
        self._stop = True

    def run(self):
        import fitz
        try:
            doc = fitz.open(self.path)
        except Exception as e:
            self.failed.emit(str(e))
            return
        self.progress.emit('doc', 0, 0)
        n = int(getattr(doc, 'page_count', 0) or 0)
        sizes = []
        probe = min(n, SIZE_PROBE_PAGES)
        t0 = time.time()
        guess = None
        try:
            # `for page in doc` 迭代比循环里反复 doc[i] 快得多（后者每次重走
            # page tree）—— 这条是 PdfView._scan_page_sizes 里验证过的
            for page in doc:
                r = page.rect
                sizes.append((max(1.0, float(r.width)),
                              max(1.0, float(r.height))))
                if len(sizes) - self._reported >= OPEN_PROG_STEP_PAGES:
                    self._reported = len(sizes)
                    self.progress.emit('page', len(sizes), n)
                if not self._guessed:
                    if probe and len(sizes) == probe and len(set(sizes)) == 1:
                        # 头几十页版心一致：先交差让界面出来，剩下的边看边扫
                        self._guessed, guess = True, [sizes[0]]
                        break
                    elif (n > probe and len(sizes) >= 8
                          and (time.time() - t0) >= SIZE_GUESS_DEADLINE):
                        # 尺寸确实不齐（扫描件常见）：也别让用户干等，拿众数先铺
                        self._guessed, guess = True, self._guess(sizes)
                        break
                if len(sizes) >= n or self._stop:
                    break
        except Exception:
            sizes = []
        if self._guessed and guess is not None:
            # 【v0.3.16】doc 一交出去就**不许再碰**：主界面马上要拿它渲染，
            # 而 MuPDF 的 Document 不能两个线程同时用（本项目注释里早写明）。
            # 以前这里是"交出后接着用同一个 doc 往下扫"，等于一边渲染一边扫 ——
            # 偶发的花页 / 版心错乱 / 崩溃就是这么来的。剩下的页数换一份句柄扫。
            self.ready.emit(doc, guess)
            sizes = self._rest(n, sizes)
            self.progress.emit('page', max(len(sizes), n), max(n, 1))
            self.refined.emit(sizes)        # 猜过的才需要再来一次纠正
            return
        self.progress.emit('page', max(len(sizes), n), max(n, 1))
        self.ready.emit(doc, sizes)

    def _rest(self, n, sizes):
        """交出 doc 之后，用**另一份自己开的句柄**把剩下几页的尺寸扫完。

        多开一次句柄的开销落在后台（界面这会儿已经在看书了），换来的是彻底
        不再和渲染抢同一份 Document。
        """
        if self._stop or n <= 0 or len(sizes) >= n:
            return sizes
        try:
            import fitz
            d2 = fitz.open(self.path)
        except Exception:
            return sizes
        try:
            i = 0
            for page in d2:
                if self._stop:
                    break
                if i < len(sizes):          # 前面已经扫过的跳过
                    i += 1
                    continue
                r = page.rect
                sizes.append((max(1.0, float(r.width)),
                              max(1.0, float(r.height))))
                if len(sizes) - self._reported >= OPEN_PROG_STEP_PAGES:
                    self._reported = len(sizes)
                    self.progress.emit('page', len(sizes), n)
                i += 1
                if len(sizes) >= n:
                    break
        except Exception:
            pass
        try:
            d2.close()
        except Exception:
            pass
        return sizes

    @staticmethod
    def _guess(sizes):
        """用已经扫到的页拼一份"全书尺寸"：众数补齐没扫到的页。"""
        s = [x for x in sizes if x]
        if not s:
            return list(sizes)
        common = max(set(s), key=s.count)
        if s.count(common) / float(len(s)) >= SIZE_GUESS_RATIO:
            return [common]                 # 众数压倒性 → 让界面照这一个尺寸铺满
        return list(sizes)


def _squash_ws(s):
    """把字符串里**所有**空白（含换行）全部抹掉。

    【v0.3.4】竖排 / 分栏的 OCR 文本，字与字之间夹着换行——实测一本中华书局
    的竖排书，取出来是 `'中\\n華\\n書\\n局'`。于是任何多字词的字面匹配都会被
    硬生生切断：同一个词「乙卯」在这本书里原本只数得出 3 处，抹掉空白后是
    88 处。这就是为什么"关键词明明传过来了，却一个也定位不到"。
    """
    return ''.join((s or '').split())


def _merge_word_rects(ws, k0, k1):
    """把第 k0~k1 个词的矩形按行合并 —— 一行一个框，别一个字画一个。

    横排书里一个词的几个字是同一行的连续 word，合并成一个长条才像"高亮"；
    竖排书里同一个词的字是上下排的，竖向差值大于字高，就自然分成几段。
    """
    out = []
    prev = None
    for k in range(max(0, int(k0)), min(len(ws) - 1, int(k1)) + 1):
        w = ws[k]
        try:
            x0, y0, x1, y1 = float(w[0]), float(w[1]), float(w[2]), float(w[3])
        except Exception:
            continue
        if prev is not None and abs(y0 - prev[1]) <= max(2.0, (y1 - y0) * 0.6):
            prev = (min(prev[0], x0), min(prev[1], y0),
                    max(prev[2], x1), max(prev[3], y1))
        else:
            if prev is not None:
                out.append(prev)
            prev = (x0, y0, x1, y1)
    if prev is not None:
        out.append(prev)
    return out


def _norm_pdf_text(s):
    """PDF 取文里五花八门的空格 / 替换字符归一成半角空格（后台线程也要用）。"""
    return (s or '').replace('\xa0', ' ').replace('\u3000', ' ').replace('\ufffd', ' ')


# ── 【v0.3.7】PDF 文字层缓存 ──────────────────────────────────────
# 一本书的正文抽出来要全本解析一遍（120 MB / 648 页约 3 秒）。抽过一次就记着，
# 后面 --words 扫词、文内查找、命中词导航都直接用，不再开第二份 Document 重来。
# 只缓存最近 3 本（多了占内存），并且拿 (大小, 修改时间) 当指纹——书换过了就失效。
_PDF_TEXT_CACHE = {}
_PDF_TEXT_CACHE_MAX = 3


def _pdf_text_sig(path):
    try:
        st = os.stat(path)
        return (int(st.st_size), int(st.st_mtime))
    except OSError:
        return None


def pdf_text_cache_get(path):
    """拿这本书抽好的文字层；没有 / 书换过了 → None。"""
    sig = _pdf_text_sig(path)
    if sig is None:
        return None
    ent = _PDF_TEXT_CACHE.get(os.path.normcase(path))
    if ent is not None and ent[0] == sig:
        return ent[1]
    return None


def pdf_text_cache_put(path, texts):
    """把抽好的文字层记下来（最多留 3 本，超了丢最早那本）。"""
    sig = _pdf_text_sig(path)
    if sig is None:
        return
    k = os.path.normcase(path)
    try:
        del _PDF_TEXT_CACHE[k]
    except KeyError:
        pass
    _PDF_TEXT_CACHE[k] = (sig, texts)
    while len(_PDF_TEXT_CACHE) > _PDF_TEXT_CACHE_MAX:
        try:
            _PDF_TEXT_CACHE.pop(next(iter(_PDF_TEXT_CACHE)))
        except (KeyError, StopIteration):
            break


# 【v0.3.14】同一本书的"抹掉空白"副本单独放一份：1316 页算一遍要 0.17 秒，
# 让后台预热线程顺手做好，查找时就能省下这段。
_PDF_FLAT_CACHE = {}


def pdf_flat_cache_get(path):
    sig = _pdf_text_sig(path)
    if sig is None:
        return None
    ent = _PDF_FLAT_CACHE.get(os.path.normcase(path))
    if ent is not None and ent[0] == sig:
        return ent[1]
    return None


def pdf_flat_cache_put(path, flats):
    sig = _pdf_text_sig(path)
    if sig is None:
        return
    k = os.path.normcase(path)
    try:
        del _PDF_FLAT_CACHE[k]
    except KeyError:
        pass
    _PDF_FLAT_CACHE[k] = (sig, flats)
    while len(_PDF_FLAT_CACHE) > _PDF_TEXT_CACHE_MAX:
        try:
            _PDF_FLAT_CACHE.pop(next(iter(_PDF_FLAT_CACHE)))
        except (KeyError, StopIteration):
            break


_ZH_CC = {}


def _zh_variants(w):
    """一个词的繁简变体：'吴翌凤' → '吳翌鳳'，反过来也一样。

    书库里大量书是繁体，而用户搜的常是简体（或相反）。只拿原词去扫，正文里
    写着「遜志堂雜鈔」的书就一个都命中不了（实测：简体「逊志堂」1 处、
    繁体「遜志堂」148 处）。

    优先 OpenCC（Viewer 打包里带的就是 Search 那一套），没有就退回 zhconv，
    再不行只认原词——宁可少命中，也不能因为转换失败把扫描搞崩。
    """
    w = w or ''
    if not w:
        return []
    out = []
    try:
        import opencc
        for cfg in ('s2t', 't2s'):
            if cfg not in _ZH_CC:
                _ZH_CC[cfg] = opencc.OpenCC(cfg)
            v = _ZH_CC[cfg].convert(w)
            if v and v != w and v not in out:
                out.append(v)
        return out
    except Exception:
        pass
    try:
        import zhconv
        for tgt in ('zh-tw', 'zh-cn'):
            v = zhconv.convert(w, tgt)
            if v and v != w and v not in out:
                out.append(v)
    except Exception:
        pass
    return out


def _word_candidates(w):
    """一个词在扫描时该试的**全部写法**：原词 + 紧凑写法 + 各自的繁简变体。"""
    cands = []
    raw = []
    for v in [w] + list(_zh_variants(w)):
        if not v or v in raw:
            continue
        raw.append(v)
        f = _squash_ws(v)
        if f and f not in cands:
            cands.append(f)
    return cands, raw


def _squash_map(s):
    """抹掉全部空白，同时记下：紧凑串第 k 个字符，在原串里是第几个。

    有了这张回指表，"紧凑串里找到了词"就能换算回原文本的真实偏移 —— 跳转、
    选区、上下文都要用真实偏移，只有紧凑偏移是跳不准的。
    """
    s = s or ''
    flat, cmap = [], []
    for i, ch in enumerate(s):
        if ch.isspace():
            continue
        flat.append(ch)
        cmap.append(i)
    return ''.join(flat), cmap


def _text_hit_spans(text, kw, limit=0, flat=None, cmap=None, strict=False):
    """一段文本里 kw 出现在哪：返回 [(起始偏移, 命中长度)]，按先后排、同处不重复。

    【v0.3.8】这条才是"查找"该用的口径 —— 和后台扫词线程完全一致：
      1. 字面找原词；
      2. 字面找它的繁简变体（书是繁体、你搜简体时全靠它）；
      3. 抹掉全部空白再找一遍（竖排 / 分栏 OCR 会把一个词用换行切开）。

    以前查找走的是纯 `t.find(kw)`，上面三条一条都不占。于是导航条说"本书
    命中 3 处"，查找栏却永远是"第 0 / 0 处"。

    flat / cmap 可以传进来复用（多个词轮流扫同一本书时，省得每词重建一遍）。

    【v0.3.18】strict=True 时**只认字面**（第 2、3 条都免掉）：用户在查找条上
    把「繁简通搜」关掉了，就该只找他打出来的那几个字，跟 CathaySearch 里
    不勾繁简通搜一个道理。
    """
    text = text or ''
    kw = kw or ''
    if not kw or not text:
        return []
    found = {}                              # 偏移 → 命中长度（同一处留最长的）
    variants = [kw] if strict else [kw] + [v for v in _zh_variants(kw) if v]
    for v in variants:
        if not v:
            continue
        start = 0
        while start < len(text):
            j = text.find(v, start)
            if j < 0:
                break
            if len(v) > found.get(j, 0):
                found[j] = len(v)
            start = j + max(1, len(v))
    if flat is None or cmap is None:
        flat, cmap = _squash_map(text)
    if flat:
        for v in variants:
            c = _squash_ws(v)
            if not c or len(c) > len(flat):
                continue
            start = 0
            while start < len(flat):
                k = flat.find(c, start)
                if k < 0:
                    break
                a = cmap[k]
                b = cmap[min(k + len(c) - 1, len(cmap) - 1)]
                n = max(1, b - a + 1)
                if n > found.get(a, 0):
                    found[a] = n
                start = k + max(1, len(c))
    out = sorted(found.items())
    return out if not limit else out[:limit]


_TEXT_EXTRACT_BUSY = set()       # 正在抽文字层的书：避免两条线程同时解析同一本


def text_extract_claim(path):
    """声明"我来抽这本书"。抢到 True 才可以开工，抽完务必 release。

    【v0.3.14】--words 的扫词线程（WordsScanWorker）本来就要全本抽一遍并存进
    缓存；预热是"锦上添花"，撞车时应该**让位**，白白多解析一本 100 MB 的书会
    把两边一起拖慢。CPython 下 set 的增删是原子的，够用了。
    """

    k = os.path.normcase(path or '')
    if not k or k in _TEXT_EXTRACT_BUSY:
        return False
    _TEXT_EXTRACT_BUSY.add(k)
    return True


def text_extract_release(path):
    try:
        _TEXT_EXTRACT_BUSY.discard(os.path.normcase(path or ''))
    except Exception:
        pass


class TextLayerWorker(QThread):
    """【v0.3.14】开完书就悄悄把全文抽好，别等用户按下 Ctrl+F 才现抽。

    文内查找 / 「所有关键词」第一次按下去，得先把整本书的文字层抽出来：1316 页
    实测 3.3 秒，这段时间主线程是堵着的 —— 用户看到的就是"点了没反应，过好几
    秒才蹦出来"。

    与其等到那时候，不如趁翻书的工夫先抽好。几个讲究：

    · **推迟启动**（TEXT_WARMUP_DELAY）：刚打开的头一秒主线程正在渲首屏、
      预取邻页，这时候插进来一个全本解析会跟它抢 IO，反而拖慢"打开"本身；
    · **自己 open 一份**：PyMuPDF 的 Document 不能跨线程共用（理由同
      WordsScanWorker），这里独立 open、只读、抽完即关；
    · **可放弃**：换书或窗口关闭时 stop()，正在抽的那本直接丢掉，不入库；
    · **别人抽过就别干**：进线程先查一次缓存，避免和 Ctrl+F 那趟重复。
    """

    done = pyqtSignal(str, int)          # 路径, 抽到的页数

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        import fitz
        if not text_extract_claim(self.path):
            return                                   # 扫词线程已经在这本书上忙了
        try:
            doc = fitz.open(self.path)
        except Exception:
            text_extract_release(self.path)
            return
        n = int(getattr(doc, 'page_count', 0) or 0)
        if n <= 0:
            try:
                doc.close()
            except Exception:
                pass
            text_extract_release(self.path)
            return
        try:
            if pdf_text_cache_get(self.path) is not None:
                return                               # 已经有别人抽好了
            out = []
            _k = 0
            # 一次迭代，不反复 doc[i]（见 _pdf_texts_of 里的说明）
            for _page in doc:
                if self._stop:
                    return
                try:
                    out.append(_norm_pdf_text(_page.get_text() or ''))
                except Exception:
                    out.append('')
                _k += 1
                # 温和节流：每抽 300 页喘 20 ms，别把磁盘跑满拖累前台渲染
                # （1316 页一共多花不到 0.1 秒，换来的后台不抢 IO）
                if _k % 300 == 0:
                    time.sleep(0.02)
            if self._stop or len(out) != n:
                return
            if pdf_text_cache_get(self.path) is None:
                pdf_text_cache_put(self.path, out)
            # 【v0.3.14】顺手把"抹掉空白"的副本也做好：前台查找拿到就能直接开扫，
            # 不然每本书第一次查找都还要现算这一遍（1316 页约 0.2 秒）。
            if pdf_flat_cache_get(self.path) is None:
                try:
                    pdf_flat_cache_put(self.path, [_squash_map(t) for t in out])
                except Exception:
                    pass
            self.done.emit(self.path, n)
        finally:
            try:
                doc.close()
            except Exception:
                pass
            text_extract_release(self.path)


class WordsScanWorker(QThread):
    """【v0.3.2】后台按一组词扫一遍文件，报出**每个词命中哪些页**。

    以前这一步在 UI 线程同步跑（`_words_apply` 里 for 循环挨个扫），大书会把
    界面卡住转圈。挪到线程之后：书照常先开出来，命中词导航条随后长出来。

    为什么自己 open 一份文档而不复用主界面的 `self.pd`：PyMuPDF 的 Document
    **不是**线程安全的（PdfRenderWorker 的注释里早写明了），主线程正拿它渲染，
    后台共用会出事。这里独立 open 一份，只读、扫完即关。

    结果以 [(词, [1 基页码…], 总次数), …] 回传；TXT 没有页码概念，页码列表留空、
    只报处数（交给 HitSet 的 text_mode）。
    """

    scanned = pyqtSignal(str, object)      # 路径, [(词, [页码…], 次数), …]
    # 【v0.3.7】边扫边报：扫完一坨就把"目前命中了哪些页"先发出来（路径, 结果,
    # 已扫页数, 总页数）。以前要等整本书扫完才发一次，大书就得干等好几秒。
    partial = pyqtSignal(str, object, int, int)

    def __init__(self, path, words, normfn=None, textfn=None, parent=None):
        super().__init__(parent)
        self._path = path or ''
        self._words = [w for w in (words or []) if w]
        self._normfn = normfn              # 空白归一化（主界面 _norm_ws，纯字符串）
        self._textfn = textfn              # TXT 读文本（纯 Python，可跨线程）
        self._stop = False

    def stop(self):
        self._stop = True

    def _norm(self, s):
        if callable(self._normfn):
            try:
                return self._normfn(s)
            except Exception:
                pass
        return (s or '').replace('\xa0', ' ').replace('\u3000', ' ').replace('\ufffd', ' ')

    def run(self):
        path = self._path
        out = []
        try:
            if path and os.path.isfile(path) and self._words:
                ext = os.path.splitext(path)[1].lower()
                out = (self._scan_pdf(path) if ext == '.pdf'
                       else self._scan_txt(path))
        except Exception:
            out = []
        self.scanned.emit(path, out)

    def _scan_pdf(self, path):
        # 【v0.3.7】抽文与计数合成一趟：扫够 WORDS_CHUNK_PAGES 页就把目前的
        # 命中发一次，命中词导航条因此**一开书就长出来**，不用等整本扫完。
        # 另外主界面此前抽过文字层的话直接复用（省掉一次 fitz.open + 全本解析）。
        cached = pdf_text_cache_get(path)
        if cached is not None:
            return self._count(cached)

        import fitz
        doc = fitz.open(path)
        n = int(getattr(doc, 'page_count', 0) or 0)
        texts = []
        flats = []
        # 每个词一份"跑着的账"：已命中的页码 + 累计数；候选写法先算好只用一次
        cands = [_word_candidates(w) for w in self._words]
        state = [[[], 0] for _ in self._words]
        i = 0
        try:
            while i < n and not self._stop:
                j = min(n, i + WORDS_CHUNK_PAGES)
                for k in range(i, j):
                    try:
                        t = self._norm(doc[k].get_text() or '')
                    except Exception:
                        t = ''
                    texts.append(t)
                    # 【v0.3.4】同时留一份"抹掉全部空白"的副本：竖排 / 分栏的书
                    # 字间夹着换行，多字词只能在紧凑副本里才连得起来。
                    flats.append(_squash_ws(t))
                # 扫描版（纯图、没文字层）别再往下扫了 —— 前几十页一个字都没有
                # 就是这种情况，全本扫下去纯属白等
                if (len(texts) >= WORDS_NO_TEXT_PAGES
                        and not any(x.strip() for x in texts[:WORDS_NO_TEXT_PAGES])):
                    # 【v0.3.16】只 return：run() 末尾本来就还会发一次 scanned。
                    # 这里再发一次的话，"一处也没找到"会弹两遍、查找表也会被清两遍。
                    return []
                for wi, (flat_cands, raw_cands) in enumerate(cands):
                    pages, cnt = state[wi]
                    for k in range(i, j):
                        hit = 0
                        ft = flats[k]
                        for c in flat_cands:
                            x = ft.count(c)
                            if x > hit:
                                hit = x
                        if not hit:             # 紧凑版没有（词本身带空白等）
                            t = texts[k]
                            for c in raw_cands:
                                x = t.count(c)
                                if x > hit:
                                    hit = x
                        if hit:
                            cnt += hit
                            if not pages or pages[-1] != k + 1:
                                pages.append(k + 1)     # 1 基，和界面页码一致
                    state[wi] = [pages, cnt]
                out = [(w, p, c) for w, (p, c) in zip(self._words, state) if c]
                self.partial.emit(path, out, len(texts), n)
                i = j
        finally:
            try:
                doc.close()
            except Exception:
                pass
        # 只有**整本扫完**才值得记缓存；中途被掐断的话存的是半本，不能要
        if not self._stop and len(texts) >= n:
            pdf_text_cache_put(path, texts)
        return [(w, p, c) for w, (p, c) in zip(self._words, state) if c]

    def _count(self, texts):
        """手上已经有文字层（缓存/复用）时的纯计数：不碰 fitz，快。"""
        flats = [_squash_ws(t) for t in texts]
        out = []
        for w in self._words:
            if self._stop:
                break
            flat_cands, raw_cands = _word_candidates(w)
            pages, cnt = [], 0
            for i, t in enumerate(texts):
                # 各写法之间取**最大**命中数而不是相加：一个词的简体与繁体
                # 不会同时出现在一页里，相加只会把总数吹成两倍。
                hit = 0
                ft = flats[i]
                for c in flat_cands:
                    k = ft.count(c)
                    if k > hit:
                        hit = k
                if not hit:
                    for c in raw_cands:
                        k = t.count(c)
                        if k > hit:
                            hit = k
                if hit:
                    cnt += hit
                    if not pages or pages[-1] != i + 1:
                        pages.append(i + 1)
            if cnt:
                out.append((w, pages, cnt))
        return out

    def _scan_txt(self, path):
        out = []
        t = ''
        if callable(self._textfn):
            try:
                t = self._textfn(path) or ''
            except Exception:
                t = ''
        else:
            try:
                with open(path, 'r', encoding='utf-8', errors='replace') as f:
                    t = f.read()
            except Exception:
                t = ''
        ft = _squash_ws(t)
        for w in self._words:
            if self._stop:
                break
            flat_cands, raw_cands = _word_candidates(w)
            # 与 PDF 同一口径：紧凑版优先，各写法取最大而不是相加
            cnt = 0
            for c in flat_cands:
                k = ft.count(c)
                if k > cnt:
                    cnt = k
            if not cnt:
                for c in raw_cands:
                    k = t.count(c)
                    if k > cnt:
                        cnt = k
            if cnt:
                out.append((w, [], cnt))           # 无页码，只报处数
        return out


class PdfRenderWorker(QThread):
    """PDF 后台渲染线程（batch22）。

    为什么要单独开一份文档：PyMuPDF 的 Document **不是线程安全**的，主线程
    在用它选词/查找，后台不能直接共用；所以这里自己 open 一份同一文件来渲染
    （磁盘热了以后 open 很快）。渲染结果以 QImage 回传 —— QPixmap 只能在
    GUI 线程里造，所以位图转换留到主线程做。

    设置环境变量 CV_SYNC_RENDER=1 可退回「老的主线程同步渲染」，出问题时能一键避开。
    """

    pageReady = pyqtSignal(int, object, float)      # 页号, QImage, 缩放

    def __init__(self, path, zoom=1.0, parent=None):
        super().__init__(parent)
        self._path = path or ''
        self._zoom = float(zoom or 1.0)
        self._jobs = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop_it = False
        self._doc = None
        self._failed = False

    def busy(self):
        with self._lock:
            return bool(self._jobs)

    def set_zoom(self, z):
        with self._lock:
            self._zoom = float(z or 1.0)
            self._jobs = []              # 缩放变了，旧的请求全部作废
        self._wake.set()

    def request(self, i):
        with self._lock:
            if self._stop_it or self._failed:
                return
            i = int(i)
            if i not in self._jobs:
                self._jobs.append(i)
        self._wake.set()

    def clear_jobs(self):
        with self._lock:
            self._jobs = []

    def stop(self):
        self._stop_it = True
        self._wake.set()

    def run(self):
        try:
            import fitz
            self._doc = fitz.open(self._path)
            if int(getattr(self._doc, 'page_count', 0) or 0) <= 0:
                self._doc = None
        except Exception:
            self._doc = None
        if self._doc is None:
            self._failed = True
            return
        while not self._stop_it:
            self._wake.wait(0.2)
            self._wake.clear()
            while not self._stop_it:
                with self._lock:
                    if not self._jobs:
                        break
                    i = self._jobs.pop(0)
                    z = self._zoom
                try:
                    pm = self._doc[i].get_pixmap(matrix=fitz.Matrix(z, z))
                    img = QImage(pm.samples, pm.width, pm.height, pm.stride,
                                 QImage.Format.Format_RGB888).copy()
                except Exception:
                    continue
                if self._stop_it:
                    break
                try:
                    self.pageReady.emit(i, img, z)
                except Exception:
                    pass
        try:
            self._doc.close()
        except Exception:
            pass
        self._doc = None


class PdfPageItem(QWidget):
    """PDF 单页：画页图 + 文字层（鼠标选词/复制）+ 查找高亮。"""
    def __init__(self, view, index):
        super().__init__()
        self.view = view
        self.index = index
        self.pix = None
        self.words = []
        self.hl = []
        self.sel_a = -1
        self.sel_b = -1
        self._drag = False
        self._rr = None            # batch11：框选截图用的橡皮筋矩形
        self._r0 = None
        self.setCursor(Qt.CursorShape.IBeamCursor)

    def set_content(self, pix, words, hl):
        self.pix = pix
        self.words = words or []
        self.hl = hl or []
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        if self.pix is not None:
            p.drawPixmap(0, 0, self.pix)
        if self._rr is not None:                # batch11：框选截图的选中框
            p.setPen(QPen(QColor(30, 120, 220), 2, Qt.PenStyle.DashLine))
            p.setBrush(QColor(30, 120, 220, 40))
            p.drawRect(self._rr)
        if self.hl:
            p.setPen(QPen(QColor(220, 140, 0), 2))
            p.setBrush(QColor(255, 235, 0, 80))
            for (x0, y0, x1, y1) in self.hl:
                p.drawRect(int(x0), int(y0), int(max(1, x1 - x0)), int(max(1, y1 - y0)))
        if self.sel_a >= 0 and self.sel_b >= 0 and self.words:
            a, b = sorted((self.sel_a, self.sel_b))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(51, 153, 255, 95))
            for k in range(a, min(b + 1, len(self.words))):
                x0, y0, x1, y1, _t = self.words[k][:5]
                p.drawRect(int(x0), int(y0), int(max(1, x1 - x0)), int(max(1, y1 - y0)))
        p.end()

    def _word_at(self, pt):
        x, y = pt.x(), pt.y()
        for k, w in enumerate(self.words):
            x0, y0, x1, y1 = w[0], w[1], w[2], w[3]
            if x0 - 2 <= x <= x1 + 2 and y0 - 2 <= y <= y1 + 2:
                return k
        best, bd = -1, 1e9
        for k, w in enumerate(self.words):
            x0, y0, x1, y1 = w[0], w[1], w[2], w[3]
            if y0 - 5 <= y <= y1 + 5:
                dd = 0 if x0 <= x <= x1 else min(abs(x - x0), abs(x - x1))
                if dd < bd:
                    bd, best = dd, k
        return best

    def mousePressEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        if self.view.region_active():          # batch11：框选截图模式
            self._r0 = ev.position().toPoint()
            self._rr = QRect(self._r0, self._r0)
            self.update()
            ev.accept()
            return
        if not self.words:
            return
        k = self._word_at(ev.position().toPoint())
        if k < 0:
            return
        self.view.begin_select(self.index, k, ev.globalPosition().toPoint())
        ev.accept()

    def mouseMoveEvent(self, ev):
        if self.view.region_active() and self._r0 is not None:
            self._rr = QRect(self._r0, ev.position().toPoint()).normalized()
            self.update()
            ev.accept()
            return
        if not self.view.is_dragging():
            return
        self.view.extend_select(ev.globalPosition().toPoint())
        ev.accept()

    def mouseReleaseEvent(self, ev):
        if self.view.region_active() and self._r0 is not None:
            rr = QRect(self._r0, ev.position().toPoint()).normalized()
            self._r0 = None
            self._rr = None
            self.update()
            self.view.finish_region(self.index, rr)
            ev.accept()
            return
        if not self.view.is_dragging():
            return
        self.view.end_select()
        ev.accept()

    def selected_text(self):
        if self.sel_a < 0 or self.sel_b < 0 or not self.words:
            return ''
        a, b = sorted((self.sel_a, self.sel_b))
        idxs = sorted(range(a, min(b + 1, len(self.words))),
                      key=lambda k: (round(self.words[k][1] / 4.0), self.words[k][0]))
        return _join_words([self.words[k][4] for k in idxs])

    def clear_sel(self):
        self.sel_a = self.sel_b = -1
        self.update()


class PdfView(QScrollArea):
    """连续纵向滚动 PDF 阅读器：按页懒渲染、像素级平滑滚动、文字层可选可复制。"""
    pageChanged = pyqtSignal(int)
    zoomRequested = pyqtSignal(float)
    fitZoom = pyqtSignal(float)          # batch10：窗口变化自动重排后的新缩放
    selectionMade = pyqtSignal(str)
    regionSelected = pyqtSignal(int, object)   # batch11：框选截图（页号, QRect）
    regionSpansSelected = pyqtSignal(object)   # batch23：跨页框选 [(页号, QRect), …]
    excerptRequested = pyqtSignal(str)          # batch11：右键「摘录选中文字」
    snapshotRequested = pyqtSignal(int)         # batch11：右键「截图本页」（页号）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._content = QWidget()
        self._lay = QVBoxLayout(self._content)
        self._lay.setContentsMargins(10, 10, 10, 10)
        self._lay.setSpacing(14)
        self._lay.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.setWidget(self._content)
        self.doc = None
        self._zoom = 1.0
        self._invert = False
        self._hl_kw = ''
        self._hl_multi = []          # batch30：多词高亮
        self._items = []
        self._pix = {}
        self._words = {}
        self._hl = {}
        self._render_count = {}
        self._pr = []                 # 各页原始尺寸（pt），开档时一次批量扫出
        self._worker = None           # batch22：后台渲染线程（None＝用同步渲染）
        self._pending = set()         # 已提交后台、等结果的页号
        self._pix_span = 8            # 位图缓存保留半径：当前页 ±8 页
        self._pix_max = 20            # 超过这个页数才清理（滞回，避免每帧都释放位图）
        self._word_span = 3           # 文字层缓存保留半径：当前页 ±3 页
        self._word_max = 12
        self._tops = []
        self._cur_page = 0
        # 【v0.3.12】焦点页：鼠标 / 选区真正落在哪一页。双页时 _cur_page 是
        # 「这一行的头一页」（滚动定位用），摘录、截图、复制本页若照它取，
        # 右边那页就会被记成左边的页码。
        self._focus_p = 0
        self._active = None
        self._dragging = False
        self._anchor = None
        self._focus = None
        self._region = False          # batch11：框选截图模式
        # 【v0.3.9】双页：窗口够宽时把两页并排放一行，省一半滚动高度。
        # _rows 是"行容器"，每页仍然是独立的 PdfPageItem —— 索引、缓存、
        # 选中、截图那一套都照旧，只是外面多包一层横向盒子。
        self._rows = []
        self._ncols = 1
        self._max_cols = 2
        self._twin = True             # 总开关：关了就永远是单列（对读模式用它）
        self._vt = QTimer(self)
        self._vt.setSingleShot(True)
        self._vt.setInterval(30)
        self._vt.timeout.connect(self._on_scroll_settled)
        # batch21：空闲预取 —— 停下滚动 200 ms 后，小口小口地把邻页渲进缓存
        self._pre_span = 6               # 预取半径（在 LRU 保留半径 ±8 之内，不额外吃内存）
        self._pt = QTimer(self)
        self._pt.setSingleShot(True)
        self._pt.setInterval(200)
        self._pt.timeout.connect(self._prefetch)
        # batch10：适应窗口 —— 窗口/视口变化时自动重算缩放（去抖）
        self._fit_mode = None            # 'fitw' / 'fitp' / None
        self._ft = QTimer(self)
        self._ft.setSingleShot(True)
        self._ft.setInterval(60)
        self._ft.timeout.connect(self._refit_now)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.verticalScrollBar().setSingleStep(40)
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._copyk = QShortcut(QKeySequence.StandardKey.Copy, self)
        self._copyk.activated.connect(self.copy_selection)
        self._copyak = QShortcut(QKeySequence('Ctrl+A'), self)
        self._copyak.activated.connect(self.select_all_page)

    # ---- 文档装载
    def clear(self):
        self._vt.stop()
        try:
            self._pt.stop()
        except Exception:
            pass
        self._stop_render_worker()      # batch22：换书/关档先把渲染线程收干净
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.doc = None
        self._items = []
        self._pix = {}
        self._words = {}
        self._hl = {}
        self._render_count = {}
        self._pr = []
        self._rows = []               # v0.3.9：一并丢掉双页的行容器
        self._top_row_widgets = []
        try:
            self._pending.clear()
        except Exception:
            pass
        self._tops = []
        self._cur_page = 0
        self._focus_p = 0
        self._anchor = None          # v0.3.12：换书时别把上一本的选区 / 焦点页带过来
        self._focus = None
        self._active = None
        self._content.adjustSize()

    def set_document(self, doc, page=0, zoom=1.0, invert=False, hl='', fit=None,
                     pr=None):
        """装载文档。fit='fitw'/'fitp' 时先算好缩放再建控件，避免建完又全量重排一遍。

        pr: 后台线程预先算好的逐页尺寸（v0.3.3）。给了且页数对得上就直接用，
        省掉主线程里那次逐页扫描（648 页约 1.1 秒）。
        """
        self.clear()
        self.doc = doc
        self._zoom = float(zoom or 1.0)
        self._invert = bool(invert)
        # 【v0.3.17】hl 可以是一串词（「所有关键词」模式）。
        # 以前这里无条件把 _hl_multi 抹平 —— 于是后台把页面尺寸算准之后那次
        # `_pdf_show(pr=sizes)`（= 重新 set_document）会把刚设好的多词高亮
        # 整个清掉，表现就是「选所有关键词不高亮，选某个单独的词才高亮」。
        if isinstance(hl, (list, tuple, set)):
            self._hl_multi = [str(x) for x in hl if x]
            self._hl_kw = self._hl_multi[0] if self._hl_multi else ''
        else:
            self._hl_kw = hl or ''
            self._hl_multi = []
        if doc is None:
            return
        if fit in ('fitw', 'fitp'):
            self._fit_mode = fit
        n = int(getattr(doc, 'page_count', 0) or 0)
        # 【v0.3.4】pr 允许只是**抽样**（前 SIZE_PROBE_PAGES 页）：版心一致的
        # 书先用它把首屏搭出来，缺的页拿最后那一页的尺寸顶上，等后台把全本
        # 扫完再发 refined 纠正一次。以前要求 len(pr) >= n，等于白等那 1.1 秒。
        if pr and len(pr) >= 1:
            self._pr = list(pr)
            if len(self._pr) < n:
                self._pr += [self._pr[-1]] * (n - len(self._pr))
        else:
            self._pr = self._scan_page_sizes(n)
        self._start_render_worker(getattr(doc, 'name', '') or '')
        if self._fit_mode:
            z = self.fit_zoom(self._fit_mode)
            if z:
                self._zoom = z
        for i in range(n):
            it = PdfPageItem(self, i)
            it.setFixedSize(self._page_size(i))
            self._items.append(it)
        self._ncols = self._target_cols()
        self._relayout_rows()             # 里面会 adjustSize + build_tops
        self.goto_page(max(0, min(n - 1, int(page))), emit=False)
        if self._fit_mode:
            self._refit_now()
        self._render_visible()
        try:
            self._pt.start()             # batch21：装完先预取邻页
        except Exception:
            pass

    def _scan_page_sizes(self, n):
        """一次迭代取出全部页的原始尺寸（pt）。

        `for page in doc` 迭代比在循环里反复 `doc[i]` 索引快得多（后者每次都要
        重走 page tree）。1576 页的扫描实测由 0.76s 降到约 0.1s 量级。
        """
        out = []
        if self.doc is None or n <= 0:
            return out
        try:
            for page in self.doc:
                r = page.rect
                out.append((max(1.0, float(r.width)), max(1.0, float(r.height))))
                if len(out) >= n:
                    break
        except Exception:
            out = []
        return out

    def _page_size(self, i):
        z = self._zoom
        try:
            if self._pr and i < len(self._pr):
                pw, ph = self._pr[i]
            else:                                  # 预扫失败时退回老办法
                r = self.doc[i].rect
                pw, ph = max(1.0, float(r.width)), max(1.0, float(r.height))
            w = max(40, int(round(pw * z)))
            h = max(40, int(round(ph * z)))
        except Exception:
            w, h = int(612 * z), int(792 * z)
        return QSize(w, h)

    def _build_tops(self):
        """每一页在内容里的顶端 y —— 双页时同一行的两页共享同一个 y。"""
        tops = []
        y = int(self._lay.contentsMargins().top())
        sp = int(self._lay.spacing())
        if self._rows:
            for r in self._rows:
                _y = y
                y += max(1, r.sizeHint().height()) + sp
                # 这一行里有几页就写几遍 —— 保持 tops 与页号一一对应
                tops += [_y] * max(1, int(r.property('cvcols') or 1))
        else:
            for it in self._items:
                tops.append(y)
                y += it.height() + sp
        self._tops = tops

    # ---- 【v0.3.9】双页：一行放两页（宽窗口）————————————————————
    def set_twin_pages(self, on):
        """开关双页。对读模式/窄窗口依旧是单列 —— 判断在 _target_cols 里。"""
        self._twin = bool(on)
        self._apply_cols()

    def twin_pages(self):
        return bool(self._twin) and int(self._ncols) >= 2

    def _target_cols(self):
        """现在该几列：1 还是 2 —— **只看宽度**。

        【v0.3.12】以前还跟高度挂钩（适应页面时 min(宽比, 高比)，窗口一矮就
        把缩放压下去，于是"每页宽度不足"判成单列）。可并肩看两页本来图的就是
        横向铺得开，高度矮无非是滚动条长一点；用户明确说这个限制不合理，所以：
            · 窗口够宽 → 双页，哪怕这一屏连一页都装不下；
            · 宽度不够 → 单页。
        判据落到"并排之后每页还够不够宽读正文"：正文栏宽掉到 400px 以下，字
        就得眯着眼认（A4 对应缩放约 67%）。宁可留单页。
        """
        if not self._twin or self._max_cols < 2:
            return 1
        if self.doc is None or not self._items:
            return 1
        try:
            i = min(int(self._cur_page), len(self._items) - 1)
            pw = 612.0
            if self._pr and i < len(self._pr):
                pw = float(self._pr[i][0]) or 612.0
            if self._fit_mode:
                # width_only：别让高度把缩放压小，只问"横向分两半够不够"
                z = self.fit_zoom(self._fit_mode, cols=2, width_only=True)
                return 2 if (z * pw) >= TWIN_MIN_PAGE_W else 1
            vw = max(60, self.viewport().width() - 30)
            w0 = self._page_size(i).width()
            return 2 if (w0 >= TWIN_MIN_PAGE_W
                         and 2 * w0 + int(self._lay.spacing()) <= vw) else 1
        except Exception:
            return 1

    def _apply_cols(self):
        """宽度（或开关）变了 → 必要时重排；列数没变就什么都不做。"""
        try:
            c = self._target_cols()
        except Exception:
            return
        if c == self._ncols and self._rows:
            return
        keep = int(self._cur_page)
        self._ncols = c
        self._relayout_rows()
        try:
            self.goto_page(keep, emit=False)
        except Exception:
            pass
        if self._fit_mode:
            self._refit_now()
        self._render_visible()

    def _relayout_rows(self):
        """按 _ncols 重新把页装进"行容器"。

        行容器只是个横盒子：这样 _build_tops / _page_at_y 照按"行"算，
        渲染、选中、截图全都不用知道有几列。
        """
        for it in self._items:                  # 先从旧的行里摘出来
            try:
                it.setParent(None)
            except Exception:
                pass
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._rows = []
        n = max(1, int(self._ncols or 1))
        gap = int(self._lay.spacing())
        for s in range(0, len(self._items), n):
            chunk = self._items[s:s + n]
            row = QWidget()
            row.setProperty('cvcols', len(chunk))
            hl = QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(gap)
            hl.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            for it in chunk:
                hl.addWidget(it, 0, Qt.AlignmentFlag.AlignTop)
            self._lay.addWidget(row, 0, Qt.AlignmentFlag.AlignHCenter)
            self._rows.append(row)
        self._content.adjustSize()
        self._build_tops()

    def page_count(self):
        return len(self._items)

    def current_page(self):
        return int(self._cur_page)

    def render_counts(self):
        return dict(self._render_count)

    def _page_at_y(self, y):
        if not self._tops:
            return 0
        k = bisect_right(self._tops, y + 6) - 1
        if self._rows and self._ncols > 1:
            # 同一行的几页共用一个 y：回到这一行的头一页，避免"该行第 2 页"
            # 被当成第 1 页报出去（行内左右不算换页）
            n = max(1, int(self._ncols))
            k = (max(0, k) // n) * n
        return max(0, min(len(self._items) - 1, k))

    def _page_at(self, x, y):
        """内容坐标 → 真正那一页：先看落在哪一行，再按 x 分左右。

        【v0.3.12】_page_at_y 只知道纵向、必然把整行算成头一页 —— 于是
        "点右边那页"永远得到左边的页号。这里补上横向那一刀：行内逐页比
        x，落在谁身上就是谁。
        """
        if not self._tops or not self._items:
            return 0
        k = max(0, min(len(self._items) - 1, bisect_right(self._tops, y + 6) - 1))
        n = max(1, int(self._ncols))
        if self._rows and n > 1:
            base = (k // n) * n
            end = min(base + n, len(self._items))
            for j in range(base, end):
                it = self._items[j]
                if x < it.x() + it.width():          # 落在这一页横向范围内
                    return j
            return max(0, end - 1)                   # 行右侧的页面间距里
        return k

    # ---- 【v0.3.12】焦点页：摘录 / 截图 / 复制本页该记哪一页 ————————
    def selection_page(self):
        """选区**起点**在第几页（没选东西 → -1）。摘录最该认这个。

        【v0.3.13】取起点而不是"按下那一下的页"：从下往上拖回去时，按下的那页
        是后头那页，出处却该写靠前那页。
        """
        try:
            a = getattr(self, '_anchor', None)
            f = getattr(self, '_focus', None)
            ps = [int(x[0]) for x in (a, f) if x]
            if ps:
                p = min(ps)
                return max(0, min(len(self._items) - 1, p))
        except Exception:
            pass
        return -1

    def selection_span(self):
        """选区 / 框选盖住的页号列表（升序）。

        【v0.3.13】双页并排时，在同一竖列里往下拖（左边第 1 页拖到左边第 3 页），
        中间夹着一行右边那页 —— 它压根不在你拖过的范围里，不能被算进来。
        所以：起止落在同一列 → 只取这一列（按列跳着走）；跨列（左拖到右）才
        把整段都算上。
        """
        try:
            a = getattr(self, '_anchor', None)
            f = getattr(self, '_focus', None)
            if a and f:
                lo, hi = sorted((int(a[0]), int(f[0])))
            else:
                return []
        except Exception:
            return []
        n = max(1, int(self._ncols or 1))
        lo = max(0, lo)
        hi = min(hi, len(self._items) - 1)
        if hi < lo:
            return []
        if n > 1 and self._rows and (hi - lo) >= n and (lo % n) == (hi % n):
            return list(range(lo, hi + 1, n))       # 同一列：跳过旁边那一列
        return list(range(lo, hi + 1))

    def _span_pages(self, i0, i1):
        """框选用的同一套口径（起止页号 → 真正要裁的页）。见 selection_span。"""
        n = max(1, int(self._ncols or 1))
        lo, hi = max(0, min(int(i0), int(i1))), max(int(i0), int(i1))
        hi = min(hi, len(self._items) - 1)
        if hi < lo:
            return []
        if n > 1 and self._rows and (hi - lo) >= n and (lo % n) == (hi % n):
            return list(range(lo, hi + 1, n))
        return list(range(lo, hi + 1))

    def focus_page(self):
        """当前该算作"你正在看的这一页"：有选区取选区那页，否则取鼠标落点那页。

        双页模式下别再用 _cur_page —— 那是行首（永远左边那页）。
        """
        try:
            sp = self.selection_page()
            if sp >= 0:
                return sp
            fp = int(getattr(self, '_focus_p', 0) or 0)
            return max(0, min(len(self._items) - 1, fp)) if self._items else 0
        except Exception:
            return 0

    def _set_focus_page(self, i):
        """鼠标落点 / 选区挪了 → 记下来；变了就通知外面（页码框、状态栏）。"""
        try:
            i = max(0, min(len(self._items) - 1, int(i)))
        except Exception:
            return
        if i == int(getattr(self, '_focus_p', 0) or 0):
            return
        self._focus_p = i
        try:
            self._set_active(self._items[i])
        except Exception:
            pass
        try:
            self.pageChanged.emit(i)
        except Exception:
            pass

    def _sync_focus_from(self, gpos):
        """全局坐标 → 更新焦点页（右键菜单、拖选时先走一步）。"""
        try:
            cp = self._content.mapFromGlobal(gpos)
            self._set_focus_page(self._page_at(cp.x(), cp.y()))
        except Exception:
            pass

    def _on_scroll(self, _v=None):
        p = self._page_at_y(self.verticalScrollBar().value())
        if p != self._cur_page:
            self._cur_page = p
            self.pageChanged.emit(p)
        self._vt.start()
        self._pt.start()             # batch21：停下滚动后预取邻页（滚动中会不断被重置）

    def goto_page(self, i, emit=True):
        if not self._items:
            return
        i = max(0, min(len(self._items) - 1, int(i)))
        self._cur_page = i
        # 【v0.3.9】tops 本来就是"页相对内容顶端"的坐标，滚动条也是同一套坐标
        # —— 老代码再减一次上边距，等于每次都往上偏了 10px，于是 jump 完之后
        # "当前页"报的是上一页（跳到第 7 页，状态栏写第 6 页）。
        y = self._tops[i]
        self.verticalScrollBar().setValue(max(0, y))
        # batch22：打开 / 跳页时目标页当场同步渲染 → 立刻可见，不闪空白；
        # 其余可视页交给后台线程
        self._render_page(i, sync=True)
        self._render_visible()
        if emit:
            self.pageChanged.emit(i)

    # ---- 渲染
    def _page_words(self, i):
        if i in self._words:
            return self._words[i]
        ws = []
        try:
            z = self._zoom
            for w in self.doc[i].get_text('words'):
                ws.append((float(w[0]) * z, float(w[1]) * z, float(w[2]) * z,
                           float(w[3]) * z, w[4]))
        except Exception:
            ws = []
        self._words[i] = ws
        return ws

    def _hl_words(self):
        """【batch30】要画的高亮词：多词模式用 _hl_multi，否则退回单个 _hl_kw。

        【v0.3.8】每个词再补上繁简变体：页面上写着「遜志堂雜鈔」，关键词却是简体
        「逊志堂杂钞」时，只拿简体去 search_for 是画不出黄框的 —— 位置算出来了、
        页面上却没反应，比 0/0 更让人纳闷。
        """
        multi = [w for w in (getattr(self, '_hl_multi', None) or []) if w]
        base = multi or ([self._hl_kw] if getattr(self, '_hl_kw', '') else [])
        out = []
        for w in base:
            for v in [w] + list(_zh_variants(w)):
                if v and v not in out:
                    out.append(v)
        return out

    def _page_hl(self, i):
        """本页该画的高亮框：缩放后坐标 [(x0, y0, x1, y1), …]。

        【v0.3.14】以前是"每个词 × 每个繁简变体"挨个调 `page.search_for()`：
        15 个关键词在繁体书里要展开成 23 个 pattern，实测**每页 0.12 秒** ——
        比渲染一页（0.023 s）还贵 5 倍。滚动时每进一页就算这么一下，于是
        "带着关键词翻书"越翻越卡，而这跟书多大、页多不多没关系。

        现在**一次成型**：本页的逐词坐标本来就有缓存（悬停取字、拖框选字都在
        用），把它们拼成一串再逐个 pattern 做字符串查找就行了 —— 一次
        get_text('words') 只要 0.007 s/页，而且还常常是白捡的（已有缓存）。

        顺带好处：拼起来之后字与字之间没有换行了，竖排 / 分栏被拆开的词也能
        连成一整个框，跟文内查找用的是同一把尺子。
        """
        kws = self._hl_words()
        if not kws:
            return []
        if i in self._hl:
            return self._hl[i]
        rects = []
        try:
            ws = self._page_words(i)
            if not ws:
                self._hl[i] = rects
                return rects
            # 【v0.3.20】拼串口径必须和后台扫词（WordsScanWorker._scan_pdf）
            # **一字不差**：那边是先 `_norm_pdf_text()`（\xa0 / \u3000 / \ufffd
            # 归一成空格）再 `_squash_ws()` 抹掉全部空白，然后才去数词。
            #
            # 这边以前直接 `''.join(w[4])` 拼原文 —— 那些字符原样留在串里，
            # 把词硬生生切断。OCR 质量差的书里 \ufffd（替换字符）遍地都是，
            # 于是出现「导航条明明写着本书命中 N 处，页面上却一个黄框都没画
            # 出来」：命中在紧凑串上算，画框却在原始串上找，两把尺子不同。
            # 现在两边同一把尺子 —— 能数出来的就一定画得出来。
            parts = []
            # 第 k 个字符属于第几个词 —— 命中了字符范围才知道该框住哪些词
            owner = []
            for _k, w in enumerate(ws):
                s = _squash_ws(_norm_pdf_text(w[4] if len(w) > 4 else ''))
                if not s:
                    continue    # 纯空白的词不占字符位（其余词照旧记原下标）
                parts.append(s)
                owner.extend([_k] * len(s))
            text = ''.join(parts)
            if not text:
                self._hl[i] = rects
                return rects
            spans = []
            for kw in kws:
                # 拼接串里没有空白，词自带空格时也要能配上
                forms = [kw, _squash_ws(kw)]
                for v in forms:
                    if not v or len(v) > len(text):
                        continue
                    start = 0
                    while start < len(text):
                        j = text.find(v, start)
                        if j < 0:
                            break
                        spans.append((j, j + len(v)))
                        start = j + max(1, len(v))
            spans.sort()
            merged = []
            for a, b in spans:                      # 同一处重叠的留成一个大范围
                if merged and a <= merged[-1][1]:
                    if b > merged[-1][1]:
                        merged[-1][1] = b
                else:
                    merged.append([a, b])
            for a, b in merged:
                if a >= len(owner):
                    continue
                ks = owner[a:min(b, len(owner))]
                if not ks:
                    continue
                rects.extend(_merge_word_rects(ws, min(ks), max(ks)))
        except Exception:
            rects = []
        self._hl[i] = rects
        return rects

    def _start_render_worker(self, path):
        """起后台渲染线程（拿不到路径、或设了 CV_SYNC_RENDER 就退回同步渲染）。"""
        self._stop_render_worker()
        if not path or os.environ.get('CV_SYNC_RENDER'):
            self._worker = None
            return
        try:
            w = PdfRenderWorker(path, self._zoom, self)
            w.pageReady.connect(self._on_page_ready)
            w.start()
            self._worker = w
        except Exception:
            self._worker = None

    def _stop_render_worker(self):
        w = getattr(self, '_worker', None)
        self._worker = None
        if w is None:
            return
        try:
            w.stop()
            w.wait(4000)          # 渲染线程若还在跑，等它收尾再让它销毁（否则会原生崩）
        except Exception:
            pass

    def _on_page_ready(self, i, img, z):
        """后台渲染完成 → 主线程转 QPixmap 并贴上去（QPixmap 只能主线程造）。"""
        try:
            # 【必须校验】换书瞬间，旧线程可能还有信号排在主线程队列里；
            # 此时 self._pix 已经属于新书，若不校验会把上一本的页图写进新书缓存
            # （两本书缩放常相同、页号也可能撞上，只有"是不是当前线程"能区分）。
            if self._worker is None or self.sender() is not self._worker:
                return
            self._pending.discard(i)
            if self.doc is None:
                return
            if abs(float(z) - float(self._zoom)) > 1e-6:
                return                        # 缩放已变，这张作废
            if self._pix.get(i) is not None:
                return
            if self._invert:
                try:
                    img.invertPixels(QImage.InvertMode.InvertRgb)
                except Exception:
                    pass
            self._pix[i] = QPixmap.fromImage(img)
            self._render_count[i] = int(self._render_count.get(i, 0)) + 1
            a, b = self._visible_range()
            if a <= i <= b and 0 <= i < len(self._items):
                self._items[i].set_content(self._pix.get(i), self._page_words(i),
                                           self._page_hl(i))
                self._trim_cache()
        except Exception:
            pass

    def _sync_render_page(self, i):
        """在主线程直接渲一页（打开/跳页时用，保证立刻看到内容，不闪空白）。"""
        try:
            import fitz
            pm = self.doc[i].get_pixmap(matrix=fitz.Matrix(self._zoom, self._zoom))
            img = QImage(pm.samples, pm.width, pm.height, pm.stride,
                         QImage.Format.Format_RGB888).copy()
            if self._invert:
                img.invertPixels(QImage.InvertMode.InvertRgb)
            pix = QPixmap.fromImage(img)
        except Exception:
            pix = None
        self._pix[i] = pix
        self._render_count[i] = int(self._render_count.get(i, 0)) + 1
        return pix

    def _render_page(self, i, sync=False):
        """取第 i 页位图。

        · sync=True（打开 / 跳页 / 停下滚动兜底）→ 主线程当场渲染，保证立刻可见；
        · 否则有后台线程就排队异步渲染（滚动时不占主线程）；
        · 没有线程（拿不到路径 / CV_SYNC_RENDER）就一律同步，行为跟以前一样。
        """
        i = int(i)
        if sync:
            self._pending.discard(i)
            if self._pix.get(i) is not None:
                return self._pix[i]
            return self._sync_render_page(i)
        if i in self._pix:
            return self._pix[i]               # 含 None（渲过但失败，不再反复重试）
        if i in self._pending:
            return None                       # 已在后台排队
        w = self._worker
        if w is not None and not getattr(w, '_failed', False):
            self._pending.add(i)
            try:
                w.request(i)
            except Exception:
                self._pending.discard(i)
                return self._sync_render_page(i)
            return None
        return self._sync_render_page(i)

    def _visible_range(self):
        if not self._items:
            return (0, -1)
        top = self.verticalScrollBar().value()
        h = max(1, self.viewport().height())
        a = self._page_at_y(top)
        b = self._page_at_y(top + h) + 1
        return (max(0, a - 1), min(len(self._items) - 1, b + 1))

    def _on_scroll_settled(self):
        """滚动停下 30 ms 后：把还没渲出来的可视页当场补渲，别让用户看着空白。"""
        self._render_visible(force_sync=True)

    def _render_visible(self, force_sync=False):
        if not self._items:
            return
        a, b = self._visible_range()
        for i in range(a, b + 1):
            if force_sync and self._pix.get(i) is None:
                self._render_page(i, sync=True)
            else:
                self._render_page(i)
            self._items[i].set_content(self._pix.get(i),
                                       self._page_words(i), self._page_hl(i))
        self._trim_cache()

    def _prefetch(self):
        """空闲时把当前页附近的页预先渲进缓存（不 set_content）。

        滚动到这些页时就能直接命中缓存、不必现渲，滚动更顺。每批只渲染 2 页，
        渲完还有欠缺就 50 ms 后再来一批 —— 一次闷头渲 13 页会卡住界面约 0.3 秒，
        分批做则每批只占 40~50 ms。
        """
        if not self._items or self.doc is None:
            return
        c = int(self._cur_page)
        n = len(self._items)
        todo = [i for i in range(max(0, c - self._pre_span),
                                 min(n - 1, c + self._pre_span) + 1)
                if i not in self._pix and i not in self._pending]
        for i in todo[:2]:
            self._render_page(i)
        if len(todo) > 2:
            QTimer.singleShot(50, self._prefetch)

    def _trim_cache(self):
        """只保留当前页附近的位图/词层，其余淘汰。

        旧行为是翻过的每一页位图都永久留在 self._pix 里：每页约 1.3 MB，
        翻完 1576 页就是 2 GB，越翻越卡直至内存耗尽。这里按当前页 ±N 淘汰，
        内存稳定在十几 MB。被淘汰的页回到可视范围时 _render_visible 会重渲染。
        """
        if not self._items:
            return
        c = int(self._cur_page)
        # 滞回：缓存没超上限就别动，免得每滚一帧都去析构一次位图（那会带来掉帧尖峰）
        if len(self._pix) <= self._pix_max and len(self._words) <= self._word_max:
            return
        pl, ph = c - self._pix_span, c + self._pix_span
        wl, wh = c - self._word_span, c + self._word_span
        for k in [k for k in self._pix if k < pl or k > ph]:
            self._pix.pop(k, None)
            it = self._items[k] if 0 <= k < len(self._items) else None
            if it is not None:
                it.pix = None                  # 断开控件对位图的引用，否则释放不掉
        for k in [k for k in self._words if k < wl or k > wh]:
            self._words.pop(k, None)
            it = self._items[k] if 0 <= k < len(self._items) else None
            if it is not None:
                it.words = []
        for k in [k for k in self._hl if k < wl or k > wh]:
            self._hl.pop(k, None)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._ft.start()             # batch10：适应宽度/页面时随窗口重排（去抖）
        self._vt.start()
        try:                         # v0.3.9：变宽 / 变窄可能该换列数了
            QTimer.singleShot(0, self._apply_cols)
        except Exception:
            pass

    # ---- batch10：适应窗口（随视口大小自动缩放，保证整页左右都看得见）
    def fit_zoom(self, mode=None, cols=None, width_only=False):
        """按当前视口算「适应宽度 / 适应页面」的缩放系数。

        cols：按几列分摊宽度（v0.3.9 双页时用 2）。
        width_only：v0.3.12 —— 只看横向，不管高矮。给 _target_cols 判断
        "够不够宽并排"用；正常缩放仍要照顾高度（min(宽比, 高比)）。
        """
        mode = mode or self._fit_mode
        if self.doc is None or not mode:
            return float(self._zoom)
        try:
            hi = (len(self._pr) or len(self._items)) - 1
            i = max(0, min(max(0, hi), int(self._cur_page)))
            if self._pr and i < len(self._pr):
                pw, ph = self._pr[i]                 # 用预扫尺寸，不再访问 doc
            else:
                r = self.doc[i].rect
                pw, ph = max(1.0, float(r.width)), max(1.0, float(r.height))
        except Exception:
            pw, ph = 612.0, 792.0
        vp = self.viewport().size()
        vw, vh = max(60, vp.width() - 24), max(60, vp.height() - 24)
        if int(cols or self._ncols or 1) > 1:        # 双页：宽度分给两半
            vw = max(60, (vp.width() - 24 - int(self._lay.spacing())) // 2)
        if width_only:
            z = vw / pw
        else:
            z = (min(vw / pw, vh / ph) if mode == 'fitp' else (vw / pw))
        return max(0.2, min(5.0, z))

    def set_fit(self, mode):
        """mode: 'fitw' / 'fitp' / None（None＝用 set_zoom 的固定值）。"""
        self._fit_mode = mode
        if mode:
            self._refit_now()

    def _refit_now(self):
        if not self._fit_mode or self.doc is None or not self._items:
            return
        z = self.fit_zoom(self._fit_mode)
        if abs(z - self._zoom) > max(0.002, self._zoom * 0.002):
            keep = self._cur_page
            self.set_zoom(z)
            try:
                self.goto_page(keep, emit=False)
            except Exception:
                pass
            self.fitZoom.emit(z)

    # ---- 高亮 / 缩放 / 反色
    def set_highlight(self, kw):
        """kw 可以是一个词，也可以是一串词（batch30：所有关键词一起高亮）。"""
        if isinstance(kw, (list, tuple, set)):
            self._hl_multi = [str(x) for x in kw if x]
            self._hl_kw = self._hl_multi[0] if self._hl_multi else ''
        else:
            self._hl_kw = kw or ''
            self._hl_multi = []
        self._hl = {}
        self._vt.start()

    def set_invert(self, b):
        b = bool(b)
        if b != self._invert:
            self._invert = b
            self._pix = {}
            self._render_count = {}
            for it in self._items:
                it.pix = None
            self._render_visible()

    def set_zoom(self, z):
        z = max(0.2, min(6.0, float(z)))
        if abs(z - self._zoom) < 1e-4:
            return
        keep = self._cur_page
        self._zoom = z
        for i, it in enumerate(self._items):
            it.setFixedSize(self._page_size(i))
        for r in getattr(self, '_rows', []):       # v0.3.9：让行跟着重算高度
            try:
                lay = r.layout()
                if lay is not None:
                    lay.invalidate()
                r.updateGeometry()
            except Exception:
                pass
        self._content.adjustSize()
        self._pix = {}
        self._words = {}
        self._hl = {}
        self._render_count = {}
        self._pending.clear()
        try:                                # batch22：通知后台线程换缩放，旧请求作废
            if self._worker is not None:
                self._worker.set_zoom(z)
        except Exception:
            pass
        for it in self._items:              # 断开控件引用，否则旧位图释放不掉
            it.pix = None
            it.words = []
            it.hl = []
        self._build_tops()
        self.goto_page(keep, emit=False)
        self._render_visible()

    # ---- 选择 / 复制（batch8：支持跨页连选）
    def is_dragging(self):
        return bool(self._dragging)

    # ---- batch11：框选截图
    def region_active(self):
        return bool(getattr(self, '_region', False))

    def begin_region(self):
        """进入一次性框选模式：下一次在页面上拖拽即选定截图区域。"""
        self._region = True
        try:
            self.setCursor(Qt.CursorShape.CrossCursor)
        except Exception:
            pass

    def cancel_region(self):
        self._region = False
        try:
            self.unsetCursor()
        except Exception:
            pass

    def finish_region(self, page, rr):
        """框选结束。

        batch23：支持**跨页**——从一页拖到下一页时，把选区按页切开，
        逐页给出裁剪矩形（regionSpansSelected）；只有一页时仍走原来的单页信号。
        """
        self._region = False
        try:
            self.unsetCursor()
        except Exception:
            pass
        if rr is None or rr.width() < 8 or rr.height() < 8 or not self._items:
            return
        page = max(0, min(len(self._items) - 1, int(page)))
        self._set_focus_page(page)      # v0.3.12：在这页上拉框，截图就记这页
        try:
            ya = self._tops[page] + rr.top()
            yb = self._tops[page] + rr.bottom()
            # 双页时行内的顺序还要靠 x 分左右（跨页拉多半是同一列，起止用同一竖列）
            _rx = rr.left() + self._items[page].x()
            i0 = self._page_at(_rx, max(0, ya))
            i1 = self._page_at(_rx, max(0, yb))
            i0 = max(0, min(i0, page))
            i1 = max(i0, min(i1, len(self._items) - 1))
            spans = []
            # v0.3.13：双页并排时同列上下拖，只裁这一列（别把隔壁那页也拼进来）
            for j in self._span_pages(i0, i1):
                h = max(1, self._items[j].height())
                y0 = max(0, ya - self._tops[j])
                y1 = min(h, yb - self._tops[j])
                if y1 - y0 < 6:
                    continue
                spans.append((j, QRect(int(rr.left()), int(y0),
                                       int(rr.width()), int(y1 - y0))))
            if not spans:
                spans = [(page, QRect(rr))]
        except Exception:
            spans = [(page, QRect(rr))]
        if len(spans) > 1:
            self.regionSpansSelected.emit(spans)
        else:
            self.regionSelected.emit(spans[0][0], spans[0][1])

    def current_page_index(self):
        return int(self._cur_page)

    def grab_page_pixmap(self, i=None):
        """取第 i 页当前缩放下的位图（QPixmap）；i=None 用焦点页。截图用。"""
        if not self._items:
            return None
        # 【v0.3.12】默认改认焦点页：双页时 _cur_page 是行首（左边那页），
        # 在右页上按截图会截到左页去。
        if i is None:
            i = self.focus_page()
        i = max(0, min(len(self._items) - 1, int(i)))
        return self._render_page(i)

    def begin_select(self, page, k, gpos):
        self._dragging = True
        self._anchor = (int(page), int(k))
        self._focus = (int(page), int(k))
        self._set_focus_page(page)           # v0.3.12：从哪一页开始拖，就认哪一页
        self._apply_selection()

    def extend_select(self, gpos):
        if not self._dragging:
            return
        hit = self._hit_global(gpos)
        if hit is not None:
            self._focus = hit
            self._set_focus_page(hit[0])     # v0.3.12：拖到右页就记右页
            self._apply_selection()

    def end_select(self):
        self._dragging = False
        t = self.selected_text()
        if t:
            QApplication.clipboard().setText(t)
            self.selectionMade.emit(t)

    def _hit_global(self, gpos):
        """全局坐标 → (页号, 词序号)，超出页面时贴到最近的行/端。"""
        try:
            cp = self._content.mapFromGlobal(gpos)
        except Exception:
            return None
        if not self._items:
            return None
        # 【v0.3.12】双页时必须连 x 一起看：只用 _page_at_y 的话，右下角拖过去
        # 也会被当成左边那页，选中的词、随后的摘录页码就全错了。
        page = self._page_at(cp.x(), cp.y())
        it = self._items[page]
        if not it.words:
            return (page, 0)
        local = QPoint(cp.x() - it.x(), cp.y() - it.y())
        k = it._word_at(local)
        if k < 0:
            best, bd = 0, 1e18
            for kk, w in enumerate(it.words):
                cy = (w[1] + w[3]) / 2.0
                dd = abs(cy - local.y())
                if dd < bd:
                    bd, best = dd, kk
            k = best
        return (page, k)

    def _apply_selection(self):
        a, b = self._anchor, self._focus
        if a is None or b is None:
            return
        if a > b:
            a, b = b, a
        # v0.3.13：双页并排时"哪几页算选区"由 selection_span 定（同列只取同列），
        # 不能再简单地"从 a[0] 到 b[0] 全算上"—— 那会把旁边那一列也捎进来。
        pages = self.selection_span()
        if not pages:
            return
        pset = set(pages)
        first, last = pages[0], pages[-1]
        for i, it in enumerate(self._items):
            n = len(it.words)
            if n == 0:
                continue
            if i not in pset:
                it.sel_a = it.sel_b = -1
            elif first == last:
                it.sel_a, it.sel_b = a[1], b[1]
            elif i == a[0]:
                it.sel_a, it.sel_b = a[1], n - 1
            elif i == b[0]:
                it.sel_a, it.sel_b = 0, b[1]
            else:
                it.sel_a, it.sel_b = 0, n - 1
            it.update()

    def _set_active(self, it):
        self._active = it

    def selected_text(self):
        parts = []
        for it in self._items:
            t = it.selected_text()
            if t:
                parts.append(t)
        return '\n'.join(parts).strip()

    def copy_selection(self):
        t = self.selected_text()
        if t:
            try:                                     # 复制选中文字 → 自动附纪年换算
                t2, hits = CHRONO.annotate_append(t)
                if hits:
                    t = t2
            except Exception:
                pass
            QApplication.clipboard().setText(t)
            self.selectionMade.emit(t)
        return t

    def select_all_page(self):
        # v0.3.12：全选"我这一页"—— 双页时别再默认左页
        try:
            self._set_active(self._items[self.focus_page()])
        except Exception:
            return
        if self._active is not None and self._active.words:
            self._active.sel_a = 0
            self._active.sel_b = len(self._active.words) - 1
            self._active.update()
            self.copy_selection()

    def page_text(self, i):
        try:
            return _join_words([w[4] for w in self._page_words(i)])
        except Exception:
            return ''

    # ---- 滚轮 / 键盘
    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            dy = ev.angleDelta().y()
            if dy:
                self.zoomRequested.emit(1.1 if dy > 0 else 1.0 / 1.1)
            ev.accept()
            return
        sb = self.verticalScrollBar()
        pd = ev.pixelDelta()
        if pd.y() or pd.x():
            sb.setValue(sb.value() - pd.y())
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - pd.x())
        else:
            dy = ev.angleDelta().y()
            dx = ev.angleDelta().x()
            if dy:
                sb.setValue(sb.value() - int(dy / 120.0 * 66))
            if dx:
                self.horizontalScrollBar().setValue(
                    self.horizontalScrollBar().value() - int(dx / 120.0 * 66))
        ev.accept()

    def keyPressEvent(self, ev):
        k = ev.key()
        if k == Qt.Key.Key_Escape and self.region_active():
            self.cancel_region()
            ev.accept()
            return
        sb = self.verticalScrollBar()
        st = max(40, min(200, self.viewport().height() // 12))
        if k == Qt.Key.Key_Down:
            sb.setValue(sb.value() + st)
        elif k == Qt.Key.Key_Up:
            sb.setValue(sb.value() - st)
        elif k in (Qt.Key.Key_PageDown, Qt.Key.Key_Space):
            # v0.3.12：双页时一屏是两页 —— PageDown 该翻过一整"行"
            self.goto_page(self._cur_page + max(1, int(self._ncols)))
        elif k == Qt.Key.Key_PageUp:
            self.goto_page(self._cur_page - max(1, int(self._ncols)))
        elif k == Qt.Key.Key_Home:
            self.goto_page(0)
        elif k == Qt.Key.Key_End:
            self.goto_page(len(self._items) - 1)
        else:
            super().keyPressEvent(ev)
            return
        ev.accept()

    def contextMenuEvent(self, ev):
        # 【v0.3.12】先认住鼠标底下是哪一页：双页时右键往往点在右边那页上
        self._sync_focus_from(ev.globalPos())
        fp = self.focus_page()
        m = QMenu(self)
        t = self.selected_text()
        a_c = m.addAction('复制选中文字' if t else '复制本页文字')
        a_a = m.addAction('全选本页（第 %d 页）' % (fp + 1))
        m.addSeparator()
        a_ex = m.addAction('✂ 摘录选中文字')
        a_sn = m.addAction('📷 截图本页（第 %d 页）' % (fp + 1))
        a_sr = m.addAction('▣ 框选截图')
        m.addSeparator()
        a_z1 = m.addAction('放大（Ctrl+滚轮）')
        a_z2 = m.addAction('缩小（Ctrl+滚轮）')
        chosen = m.exec(ev.globalPos())
        if chosen is a_c:
            if t:
                self.copy_selection()
            else:
                txt = self.page_text(fp)
                if txt:
                    try:                             # 复制本页文字 → 自动附纪年换算
                        txt, _ = CHRONO.annotate_append(txt)
                    except Exception:
                        pass
                    QApplication.clipboard().setText(txt)
                    self.selectionMade.emit(txt)
        elif chosen is a_ex:
            self.excerptRequested.emit(t)
        elif chosen is a_sn:
            self.snapshotRequested.emit(int(self.focus_page()))
        elif chosen is a_sr:
            self.begin_region()
        elif chosen is a_a:
            self.select_all_page()
        elif chosen is a_z1:
            self.zoomRequested.emit(1.1)
        elif chosen is a_z2:
            self.zoomRequested.emit(1.0 / 1.1)


class ReaderWindow(QMainWindow):
    """阅读器独立窗口：关闭时自动收回主窗口。

    batch18：**不设父窗口**（独立顶层窗口）——这样最小化主窗口（文件列表）时，
    阅读器窗口不会被连带最小化。
    """
    def __init__(self, owner):
        super().__init__(None)
        self.owner = owner
        self.setWindowTitle('CathayViewer 阅读器')
        try:
            self.setWindowFlag(Qt.WindowType.Window, True)
            self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
            self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
            self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
        except Exception:
            pass
        try:
            if icon_path():
                self.setWindowIcon(QIcon(icon_path()))
        except Exception:
            pass

    def closeEvent(self, ev):
        try:
            if not getattr(self, '_cv_no_attach', False):
                self.owner._attach_reader(from_close=True)
        except Exception:
            pass
        try:
            super().closeEvent(ev)
        except Exception:
            pass


class TxtSyncView(QTextEdit):
    """对读用 TXT 视图：带页码索引，滚动/跳页与 PDF 同步（算法移植自 CathayReader）。"""
    pageChanged = pyqtSignal(int)          # 印刷页码（1 起）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont('Microsoft YaHei', 12))
        self.idx = None
        self._syncing = False
        self._cur = 0
        self.truncated = False      # batch16：TXT 过大时只载入前 DUAL_TXT_MAX
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)

    def load(self, path):
        try:
            _sz = os.path.getsize(path)
        except OSError:
            _sz = 0
        lim = DUAL_TXT_MAX if _sz > DUAL_TXT_MAX else 0
        txt = TOOLS._read_text(path, lim)
        self.truncated = bool(lim)
        self.setPlainText(txt)
        self.idx = TOOLS.SyncIndex(txt)
        self._cur = 0
        return bool(txt)

    def goto_number(self, number):
        """跳到印刷页码 number（1 起）；没有页码标记就按比例。"""
        if not self.idx or self.idx.page_count == 0:
            return
        off, hit = self.idx.offset_for_page(int(number))
        doc = self.document()
        pos = min(int(off), max(0, doc.characterCount() - 1))
        c = QTextCursor(doc)
        c.setPosition(max(0, pos))
        self._syncing = True
        try:
            self.setTextCursor(c)
            cr = self.cursorRect(c)
            sb = self.verticalScrollBar()
            sb.setValue(max(0, sb.value() + cr.top() - 12))
            self._cur = int(hit)
        finally:
            self._syncing = False

    def _on_scroll(self, _v):
        if self._syncing or not self.idx or self.idx.page_count == 0:
            return
        cur = self.cursorForPosition(QPoint(0, 0))
        n = self.idx.page_at_position(cur.position())
        if n and n != self._cur:
            self._cur = int(n)
            self.pageChanged.emit(int(n))


class TextView(QTextEdit):
    """文本阅读框：复制（右键 / Ctrl+C）时自动在文末追加历史纪年换算。

    batch23：另外支持「框选截图」—— 在文本区拖一个框，把那一小块图截下来
    （TXT/MD 也能截图，不再只有 PDF 能截）。
    """

    snapRegionSelected = pyqtSignal(object)     # 选中矩形（viewport 坐标）

    def _snap_off(self):
        self._snap_region = False
        try:
            self.unsetCursor()
        except Exception:
            pass

    def begin_snap_region(self):
        """进入一次性框选截图模式。"""
        self._snap_region = True
        self._snap_r0 = None
        try:
            self.setCursor(Qt.CursorShape.CrossCursor)
        except Exception:
            pass

    def mousePressEvent(self, ev):
        if getattr(self, '_snap_region', False) and ev.button() == Qt.MouseButton.LeftButton:
            self._snap_r0 = ev.position().toPoint()
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):
        if getattr(self, '_snap_region', False) and self._snap_r0 is not None:
            p0 = self._snap_r0
            self._snap_r0 = None
            self._snap_off()
            rr = QRect(p0, ev.position().toPoint()).normalized()
            if rr.width() >= 8 and rr.height() >= 8:
                self.snapRegionSelected.emit(rr)
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def _annot(self, sel):
        try:
            t, hits = CHRONO.annotate_append(sel)
            return t
        except Exception:
            return sel

    def _sel_text(self):
        return self.textCursor().selectedText().replace('\u2029', '\n')

    def _copy_annotated(self):
        sel = self._sel_text()
        if not (sel or '').strip():
            super().copy()
            return
        QApplication.clipboard().setText(self._annot(sel))

    def keyPressEvent(self, ev):
        if ev.matches(QKeySequence.StandardKey.Copy):
            self._copy_annotated()
            ev.accept()
            return
        super().keyPressEvent(ev)

    def contextMenuEvent(self, ev):
        m = QMenu(self)
        a1 = m.addAction('复制（含纪年换算）')
        a2 = m.addAction('复制原文')
        a3 = m.addAction('全选')
        chosen = m.exec(ev.globalPos())
        if chosen is a1:
            self._copy_annotated()
        elif chosen is a2:
            super().copy()
        elif chosen is a3:
            self.selectAll()


class DualRead(QWidget):
    """PDF（左）+ TXT（右）左右并列对读，中间纵列是 PDF 导航；页码双向同步。

    batch11 新增；batch14 改为左右两纵列 + 中间 PDF 导航页，并支持切换 TXT 版本与单独打开。
    """
    pageChanged = pyqtSignal(int)          # PDF 页号（0 起），供主窗状态栏联动
    txtChanged = pyqtSignal(str)           # 用户切换了 TXT 版本 → 主窗更新 _text_path
    exitRequested = pyqtSignal(str)        # 点「只看 PDF / 只看 TXT」→ 'pdf' / 'text'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pdf = PdfView()
        self.pdf.set_twin_pages(False)     # v0.3.9：对读时左边就是单页（右边是 TXT）
        self.txt = TxtSyncView()
        self._guard = False
        self._pdf_count = 0
        self._txt_path = ''
        # 中间纵列（PDF 导航页）：页码 / 翻页 / 同步 / 缩放 / TXT 版本 / 单独打开
        nav = QWidget()
        nv = QVBoxLayout(nav)
        nv.setContentsMargins(4, 4, 4, 4)
        nv.setSpacing(5)
        self.lb = QLabel('对读')
        self.lb.setWordWrap(True)
        nv.addWidget(self.lb)
        nv.addWidget(QLabel('PDF 导航'))
        self.cb_sync = QCheckBox('同步翻页')
        self.cb_sync.setChecked(True)
        nv.addWidget(self.cb_sync)
        nv.addWidget(QLabel('页'))
        self.ed = QLineEdit()
        self.ed.setFixedWidth(56)
        self.ed.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ed.returnPressed.connect(self._jump)
        nv.addWidget(self.ed)
        b_go = QPushButton('跳页')
        b_go.clicked.connect(self._jump)
        nv.addWidget(b_go)
        pn = QHBoxLayout()
        pn.setSpacing(2)
        self.b_first = QPushButton('⏮')
        self.b_prev = QPushButton('◀')
        self.b_next = QPushButton('▶')
        self.b_last = QPushButton('⏭')
        for _b in (self.b_first, self.b_prev, self.b_next, self.b_last):
            _b.setMaximumWidth(30)
            pn.addWidget(_b)
        self.b_first.clicked.connect(self._first)
        self.b_prev.clicked.connect(lambda: self._step(-1))
        self.b_next.clicked.connect(lambda: self._step(1))
        self.b_last.clicked.connect(self._last)
        nv.addLayout(pn)
        self.cb_fit = QComboBox()
        self.cb_fit.addItems(['适应宽度', '适应页面', '100%'])
        self.cb_fit.setCurrentIndex(1)
        self.cb_fit.currentIndexChanged.connect(self._fit)
        nv.addWidget(self.cb_fit)
        nv.addWidget(QLabel('TXT 版本'))
        self.cb_txt = QComboBox()
        self.cb_txt.setToolTip('切换对读用的 TXT（不同 OCR / 繁简版本）')
        self.cb_txt.currentIndexChanged.connect(self._switch_txt)
        nv.addWidget(self.cb_txt)
        self.b_pdf_only = QPushButton('只看 PDF')
        self.b_pdf_only.setToolTip('退出对读，只显示 PDF（保持当前页）')
        self.b_pdf_only.clicked.connect(lambda: self._single('pdf'))
        self.b_txt_only = QPushButton('只看 TXT')
        self.b_txt_only.setToolTip('退出对读，只显示 TXT')
        self.b_txt_only.clicked.connect(lambda: self._single('text'))
        nv.addWidget(self.b_pdf_only)
        nv.addWidget(self.b_txt_only)
        nv.addStretch(1)
        nav.setMinimumWidth(118)
        nav.setMaximumWidth(196)
        sp = QSplitter(Qt.Orientation.Horizontal)
        sp.addWidget(self.pdf)
        sp.addWidget(nav)
        sp.addWidget(self.txt)
        sp.setSizes([620, 150, 540])
        sp.setStretchFactor(0, 3)
        sp.setStretchFactor(1, 0)
        sp.setStretchFactor(2, 3)
        sp.setChildrenCollapsible(False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addWidget(sp, 1)
        self.pdf.pageChanged.connect(self._on_pdf_page)
        self.txt.pageChanged.connect(self._on_txt_page)

    # ---- 装载
    def load(self, doc, txt_path, first=1, fit='fitp'):
        self._guard = True
        try:
            self.pdf.set_document(doc, max(0, int(first) - 1), 1.0,
                                  fit=(fit if fit in ('fitw', 'fitp') else None))
            self._pdf_count = int(getattr(doc, 'page_count', 0) or 0)
            self.txt.load(txt_path)
            self._txt_path = txt_path
            self._set_fit(fit)
            self.txt.goto_number(int(first))
            try:
                self.ed.setText(str(int(first)))
            except Exception:
                pass
            try:
                import os as _os
                _tail = '｜TXT 过大，仅前 %d MB' % (DUAL_TXT_MAX // 1048576) \
                    if getattr(self.txt, 'truncated', False) else ''
                self.lb.setText('对读：《%s》 ｜ PDF %d 页 / TXT %d 页%s（按页码标记同步）'
                                % (_os.path.basename(txt_path),
                                   self._pdf_count, self.txt.idx.page_count if self.txt.idx else 0,
                                   _tail))
            except Exception:
                self.lb.setText('对读：已加载')
        finally:
            self._guard = False

    def _set_fit(self, fit):
        m = {'fitw': 0, 'fitp': 1, '100': 2}.get(fit or 'fitp', 1)
        self.cb_fit.blockSignals(True)
        self.cb_fit.setCurrentIndex(m)
        self.cb_fit.blockSignals(False)
        if fit in ('fitw', 'fitp'):
            self.pdf.set_fit(fit)
        else:
            self.pdf.set_fit(None)
            self.pdf.set_zoom(1.0)

    def _fit(self, i):
        if i == 0:
            self.pdf.set_fit('fitw')
        elif i == 1:
            self.pdf.set_fit('fitp')
        else:
            self.pdf.set_fit(None)
            self.pdf.set_zoom(1.0)

    # ---- batch14：TXT 版本切换 / 单独打开 / 跳页面控
    def set_txt_list(self, items):
        """items: [(标签, 路径), …] —— 对读时可切换的 TXT 版本。"""
        self.cb_txt.blockSignals(True)
        self.cb_txt.clear()
        for label, path in (items or []):
            self.cb_txt.addItem(str(label), path)
        if items:
            i = self.cb_txt.findData(self._txt_path)
            self.cb_txt.setCurrentIndex(i if i >= 0 else 0)
        self.cb_txt.blockSignals(False)
        self.cb_txt.setEnabled(self.cb_txt.count() > 1)

    def _switch_txt(self, i):
        p = self.cb_txt.itemData(i)
        if not p or p == self._txt_path:
            return
        if self.load_txt(p):
            self.txtChanged.emit(p)

    def load_txt(self, path):
        """换一个 TXT 并重新对齐到当前页。"""
        try:
            if not self.txt.load(path):
                return False
        except Exception:
            return False
        self._txt_path = path
        try:
            n = int(re.sub(r'\D', '', self.ed.text()) or '1')
        except Exception:
            n = 1
        self._guard = True
        try:
            self.txt.goto_number(n)
        finally:
            self._guard = False
        return True

    def _first(self):
        self.ed.setText('1')
        self._jump()

    def _last(self):
        self.ed.setText(str(max(1, int(self._pdf_count or 1))))
        self._jump()

    def _step(self, d):
        try:
            n = int(re.sub(r'\D', '', self.ed.text()) or '1') + int(d)
        except Exception:
            n = 1
        self.ed.setText(str(max(1, n)))
        self._jump()

    def _single(self, which):
        self.exitRequested.emit(str(which))

    # ---- 同步
    def _on_pdf_page(self, i):
        if self._guard:
            return
        try:
            self.ed.setText(str(int(i) + 1))
        except Exception:
            pass
        if self.cb_sync.isChecked():
            self._guard = True
            try:
                self.txt.goto_number(int(i) + 1)
            finally:
                self._guard = False
        self.pageChanged.emit(int(i))

    def _on_txt_page(self, n):
        if self._guard:
            return
        try:
            self.ed.setText(str(int(n)))
        except Exception:
            pass
        if self.cb_sync.isChecked():
            self._guard = True
            try:
                self.pdf.goto_page(int(n) - 1)
            finally:
                self._guard = False
        self.pageChanged.emit(int(n) - 1)

    def _jump(self):
        try:
            n = int(re.sub(r'\D', '', self.ed.text()) or '1')
        except Exception:
            n = 1
        self._guard = True
        try:
            self.pdf.goto_page(int(n) - 1)
            self.txt.goto_number(int(n))
        finally:
            self._guard = False
        self.pageChanged.emit(int(n) - 1)

    def goto_page(self, n):
        self.ed.setText(str(int(n) + 1))
        self._guard = True
        try:
            self.pdf.goto_page(int(n))
            self.txt.goto_number(int(n) + 1)
        finally:
            self._guard = False

    def current_page(self):
        return int(self.pdf.current_page())

    def clear(self):
        try:
            self.pdf.clear()
            self.txt.clear()
            self.txt.idx = None
        except Exception:
            pass


# ---- 跨文件全文检索（独立进程 worker）
def _fts_pdf(path, kw, per_file):
    """返回 (总命中次数, 前 per_file 条上下文)。"""
    hits = []
    count = 0
    import fitz
    d = fitz.open(path)
    try:
        for i in range(int(d.page_count)):
            try:
                t = (d[i].get_text() or '').replace('\xa0', ' ')
            except Exception:
                t = ''
            j = t.find(kw)
            while j >= 0:
                count += 1
                if len(hits) < per_file:
                    hits.append({'page': i, 'ctx': _ctx_text(t, j, len(kw))})
                j = t.find(kw, j + len(kw))
    finally:
        try:
            d.close()
        except Exception:
            pass
    return count, hits


def _fts_text(path, kw, per_file):
    """返回 (总命中次数, 前 per_file 条上下文)。"""
    t = decode_bytes(read_bytes(path, TEXT_MAX_BYTES)).replace('\xa0', ' ')
    hits = []
    count = 0
    start = 0
    while True:
        j = t.find(kw, start)
        if j < 0:
            break
        count += 1
        if len(hits) < per_file:
            hits.append({'page': None, 'ctx': _ctx_text(t, j, len(kw))})
        start = j + len(kw)
    return count, hits


def _emit(s):
    """向 stdout 写进度；窗口版 exe 没有控制台（stdout 可能为 None），静默忽略。"""
    try:
        if sys.stdout is not None:
            sys.stdout.write(s)
            sys.stdout.flush()
    except Exception:
        pass


def fts_run(job_path):
    """在独立进程里执行跨文件全文检索：读 job.json，进度写 stdout，结果写 out.json。"""
    with open(job_path, 'r', encoding='utf-8') as f:
        job = json.load(f)
    kw = job.get('kw') or ''
    files = job.get('files') or []
    out = job.get('out') or ''
    budget = int(job.get('max_hits') or 500)
    files_rep = []
    errors = []
    scanned = 0
    total = 0
    per_file = int(job.get('max_per_file') or 50)
    if kw:
        for p in files:
            scanned += 1
            cnt, hh = 0, []
            try:
                ext = os.path.splitext(p)[1].lower()
                if ext == '.pdf':
                    cnt, hh = _fts_pdf(p, kw, per_file)
                elif ext in ('.txt', '.md', '.json', '.csv', '.log', '.htm', '.html'):
                    cnt, hh = _fts_text(p, kw, per_file)
            except Exception as e:
                errors.append('%s: %s' % (os.path.basename(p), e))
            if cnt:
                files_rep.append({'path': p, 'name': os.path.basename(p),
                                  'count': cnt, 'hits': hh})
                total += cnt
            _emit('PROGRESS\t%d\t%d\t%d\n' % (scanned, len(files), total))
    flat = []
    for fr in files_rep:
        for h in fr.get('hits') or []:
            flat.append({'path': fr['path'], 'name': fr['name'],
                         'page': h.get('page'), 'ctx': h.get('ctx')})
    try:
        with open(out, 'w', encoding='utf-8') as f:
            json.dump({'kw': kw, 'files': files_rep, 'hits': flat, 'errors': errors,
                       'scanned': scanned, 'total': len(files)}, f, ensure_ascii=False)
    except Exception:
        pass
    _emit('DONE\n')
    return 0


class _HiDelegate(QStyledItemDelegate):
    """列表里把命中的关键词用黄底画出来（支持多个关键词，超长自动裁剪）。"""
    def __init__(self, kw_getter, parent=None):
        super().__init__(parent)
        self._kw = kw_getter

    def _kws(self):
        try:
            v = self._kw() if callable(self._kw) else ''
        except Exception:
            v = ''
        if isinstance(v, (list, tuple, set)):
            out = [str(x) for x in v if x]
        else:
            out = [str(v)] if v else []
        return sorted(set(out), key=len, reverse=True)     # 长的优先，避免短词切碎

    def paint(self, painter, option, index):
        kws = self._kws()
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or '')
        if not kws or not any(k in text for k in kws):
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        opt.text = ''
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        painter.save()
        fm = painter.fontMetrics()
        rect = option.rect.adjusted(4, 0, -4, 0)
        x = rect.left()
        baseline = rect.top() + (rect.height() - fm.height()) // 2 + fm.ascent()
        sel = bool(opt.state & QStyle.StateFlag.State_Selected)
        fg = opt.palette.color(QPalette.ColorRole.HighlightedText if sel
                               else QPalette.ColorRole.Text)

        def _hot(i):
            for k in kws:
                if text.startswith(k, i):
                    return k
            return ''

        i, stop = 0, False
        while i < len(text) and not stop:
            k = _hot(i)
            if k:
                seg, hot = k, True
                i += len(k)
            else:
                seg, hot = text[i], False
                i += 1
            w = fm.horizontalAdvance(seg)
            if x + w > rect.right() + 2:
                stop = True
                break
            if hot:
                painter.fillRect(QRect(x, rect.top() + 2, w, rect.height() - 4),
                                 QColor(255, 221, 0, 175))
            painter.setPen(fg)
            painter.drawText(x, baseline, seg)
            x += w
        painter.restore()


class MainWindow(QMainWindow):
    def __init__(self, settings=None, tab_child=False, parent=None):
        super().__init__(parent)
        self.st = settings or C.load_settings()
        self.db = self.st.get('primary_db') or ''
        # CathayHub 统一入口可能已经帮我们把文件名索引建好了 —— 那就直接用，
        # 别再弹"第一次使用 —— 建立文件名索引"来问用户。
        if not os.path.isfile(self.db or ''):
            hub = C.hub_filename_db()
            if hub:
                self.db = hub
                self.st['primary_db'] = hub
        # 【v0.3.1 多标签】一本 = 一个 MainWindow 实例，各自抱着自己的
        # 阅读区、页码、命中词、查找栏——天然隔离，不必把 God Class 拆开。
        # tab_child=True 的是"只为某个标签而生"的实例：永不显示，它的
        # reader_panel 会被搬进主窗口的标签页里。
        self._tab_child = tab_child
        self._tabbar = None          # QTabWidget：有两个以上标签时才建
        self._tabs = []              # [{'mw','page','path'}]
        self.rows = []
        self.vers = []
        self.meta = None
        self._reader = ''        # 'pdf' / 'text'：当前阅读区内容类型
        self._top_widgets = []   # 顶部工具栏（全屏时隐藏）
        self._btn_widgets = []   # 详情区按钮行（全屏时隐藏）
        self._f11 = QShortcut(QKeySequence('F11'), self) if QShortcut else None
        if self._f11:
            self._f11.activated.connect(self.toggle_full)
        self.pd = None      # 当前 PDF 文档
        self.pgno = 0       # 当前页（0 起）
        self._pd_path = ''  # 当前打开文档的路径（目录跟随它）
        self._stat_path = ''  # 阅读统计：当前计时的文件
        self._stat_t0 = 0.0
        self._last_toc = None
        self._pgdn = QShortcut(QKeySequence('PgDown'), self)
        self._pgdn.activated.connect(lambda: self.pdf_goto(1))
        self._pgup = QShortcut(QKeySequence('PgUp'), self)
        self._pgup.activated.connect(lambda: self.pdf_goto(-1))
        self._toc = QShortcut(QKeySequence('Ctrl+T'), self)
        self._toc.activated.connect(self.toc_here)
        self._mark = QShortcut(QKeySequence('Ctrl+B'), self)
        self._mark.activated.connect(self.bookmark_here)
        self._hist = QShortcut(QKeySequence('Ctrl+H'), self)
        self._hist.activated.connect(self.hist_here)
        self._sk = QShortcut(QKeySequence('Ctrl+K'), self)
        self._sk.activated.connect(self.search_history_dialog)
        self._sd = QShortcut(QKeySequence('Ctrl+D'), self)
        self._sd.activated.connect(self.save_search)
        self._rstat = QShortcut(QKeySequence('Ctrl+Shift+H'), self)
        self._rstat.activated.connect(self.read_stats_dialog)
        self._epub = QShortcut(QKeySequence('Ctrl+E'), self)
        self._epub.activated.connect(self.epub_here)
        self._cpy = QShortcut(QKeySequence('Ctrl+Shift+C'), self)
        self._cpy.activated.connect(self.copy_text)
        # batch11：截图本 / 摘录本 / 对读 / 跨文件检索历史
        self.dual = None                 # PDF+TXT 对读面板（_ui 里创建）
        self._exk = QShortcut(QKeySequence('Ctrl+Shift+E'), self)
        self._exk.activated.connect(self.excerpt_here)
        self._snk = QShortcut(QKeySequence('Ctrl+Shift+X'), self)
        self._snk.activated.connect(self.snapshot_here)
        self._dualk = QShortcut(QKeySequence('Ctrl+Shift+D'), self)
        self._dualk.activated.connect(self.toggle_dual)
        self._ftshistk = QShortcut(QKeySequence('Ctrl+Shift+F'), self)
        self._ftshistk.activated.connect(self.fts_history_dialog)
        self._footk = QShortcut(QKeySequence('Ctrl+Shift+I'), self)
        self._footk.activated.connect(self.footnote_here)
        self._listk = QShortcut(QKeySequence('Ctrl+Shift+L'), self)      # batch17：收起/展开左栏
        self._listk.activated.connect(self.toggle_list)
        self._chronok = QShortcut(QKeySequence('Ctrl+Shift+Y'), self)
        self._chronok.activated.connect(self.chrono_dialog)
        self._rhymek = QShortcut(QKeySequence('Ctrl+Shift+R'), self)   # batch24：代日韵目
        self._rhymek.activated.connect(self.rhyme_dialog)
        self._aliask = QShortcut(QKeySequence('Ctrl+Shift+A'), self)
        self._aliask.activated.connect(self.alias_dialog)
        self._excvk = QShortcut(QKeySequence('Ctrl+Shift+M'), self)
        self._excvk.activated.connect(self.excerpt_viewer)
        self.st.setdefault('auto_dual', True)     # 打开 TXT 且有同名 PDF → 自动对读
        self._suppress_dual = False               # 版本切换等场景临时禁用自动对读
        # batch3：MD 渲染 / JSON 树 / 相关文件 / 缩略图
        self._md_render = False     # 阅读区是否处于 Markdown 渲染态
        self._md_path = ''          # 当前渲染的 .md 路径
        self._md_src = ''           # 当前 .md 的源码文本
        self._thumb_dock = None     # 右侧缩略图面板
        self._thumb_list = None
        self._thumb_timer = None
        self._thumbs_added = 0
        # batch5：连续滚动 / 导航面板 / 文内查找
        self._nav_dock = None       # 导航面板（QDockWidget：目录 / 缩略图 / 查找）
        self._nav_tabs = None
        self._nav_toc = None
        self._nav_find = None
        # batch7：PDF 渲染缓存/滚动记账已移入 PdfView（阅读器内核）
        self._fts_proc = None       # 跨文件全文检索进程
        self._fts_kw = ''
        self._fts_total = 0
        self._msg_hold_until = 0.0  # 显式提示的保护期（避免被滚动状态栏覆盖）
        self._zoom = 1.0            # 自定义缩放系数
        self._zoom_mode = 'fitp'    # batch8：默认「适应页面」；fitp/fitw/100/custom
        self._pd_texts = None       # PDF 页级文本缓存
        self._pd_texts_doc = None   # 缓存归属的文档 id（换文件时重建）
        self._hl_kw = ''            # 当前 PDF 高亮关键词
        self._hl_multi = []         # batch30：多词高亮（"所有关键词"模式）
        self._find_kw = ''
        self._find_total = 0
        self._find_idx = -1
        self._find_hits = []        # batch9: [{path,name,page,off,ctx,txt_mark}]
        self._find_kept = []        # 保留（默认展示）的命中
        self._find_hidden = []      # 被去重、默认隐藏的命中
        self._find_view = []        # 实际展示（kept + 可选的 hidden）
        self._find_show_hidden = False
        self._find_manual_words = []   # v0.3.18：手输词铺出来的写法（换书要清）
        # v0.3.23：Viewer 自己铺开的写法，各自命中几处（下拉要按它排序、标未命中）
        self._find_word_counts = {}
        self._text_path = ''        # 当前文本视图打开的文件路径
        self._last_json = None
        self._last_related = None
        self._mdk = QShortcut(QKeySequence('Ctrl+M'), self)
        self._mdk.activated.connect(self.md_toggle)
        self._jsk = QShortcut(QKeySequence('Ctrl+J'), self)
        self._jsk.activated.connect(self.json_tree_dialog)
        self._relk = QShortcut(QKeySequence('Ctrl+R'), self)
        self._relk.activated.connect(self.related_dialog)
        self._thk = QShortcut(QKeySequence('Ctrl+Shift+T'), self)
        self._thk.activated.connect(self.toggle_thumbs)
        # batch5：Ctrl+Home/End 首/末页、Ctrl+F 文内查找、Esc 关闭查找条
        self._homek = QShortcut(QKeySequence('Ctrl+Home'), self)
        self._homek.activated.connect(self.pdf_home)
        self._endk = QShortcut(QKeySequence('Ctrl+End'), self)
        self._endk.activated.connect(self.pdf_end)
        self._findk = QShortcut(QKeySequence('Ctrl+F'), self)
        self._findk.activated.connect(self.find_focus)
        self._esck = QShortcut(QKeySequence('Esc'), self)
        self._esck.activated.connect(self.find_close)
        # batch28/batch30：命中词导航（CathaySearch 调起时才有）—— 导航条并进查找栏后
        # 快捷键照旧：Alt+←/→ 换词，Alt+↓ 下一处
        self._hitwk = QShortcut(QKeySequence('Alt+Right'), self)
        self._hitwk.activated.connect(self._find_next_word)
        self._hitpk = QShortcut(QKeySequence('Alt+Left'), self)
        self._hitpk.activated.connect(self._find_prev_word)
        self._hitnk = QShortcut(QKeySequence('Alt+Down'), self)
        self._hitnk.activated.connect(self.find_next)
        # 【v0.3.2】本文件夹全文检索已整体移除，Ctrl+Shift+S 一并释放
        # （跨整个书库检索请用 Ctrl+Shift+G 唤起 CathayHub Search）
        self._ftsk = None
        # CathayHub：全库检索（唤起 CathayHub Search）
        self._hubsk = QShortcut(QKeySequence('Ctrl+Shift+G'), self)
        self._hubsk.activated.connect(self.hub_search)
        try:
            from PyQt6.QtCore import QTimer as _QT
            # 【v0.3.1】只有"主实例"负责命令行。多标签下每个标签都是一整个
            # MainWindow，若也各注册一次，就会变成"开三本书、同一份命令行被
            # 打开三遍、搜索历史也被塞三条"。子实例的活只有一个：看它的书。
            if not self._tab_child:
                _QT.singleShot(300, self.open_cli_arg)   # 双击 / 命令行带路径 → 直接打开
        except Exception:
            pass
        self.setWindowTitle('%s %s' % (APP_TITLE, APP_VERSION))
        self._apply_default_size()
        self.setFont(QFont('Microsoft YaHei', 9))
        if icon_path():
            from PyQt6.QtGui import QIcon
            self.setWindowIcon(QIcon(icon_path()))
        self._ui()
        self._refresh_status()
        try:
            # batch23：状态栏提示统一转到阅读器窗口（阅读器独立时）
            self.statusBar().messageChanged.connect(self._mirror_status)
        except Exception:
            pass
        try:
            self._sync_tool_visibility()      # 初始：行距/MD 藏、在阅读区打开 显示
        except Exception:
            pass
        try:
            self.setAcceptDrops(True)         # batch15：支持把 PDF / TXT 拖进来打开
        except Exception:
            pass

    def _ui(self):
        cen = QWidget()
        self.setCentralWidget(cen)
        v = QVBoxLayout(cen)
        # 【v0.3.16】开档进度条：浮在内容顶端的一条细线（浏览器那样）。
        # 用绝对定位直接贴在中央部件上面，不参与任何布局，所以不会把下面的东西挤歪。
        try:
            self._openbar = OpenProgress(cen)
        except Exception:
            self._openbar = None
        # 搜索框 / 建库按钮先建好，稍后摆进左栏顶部（见下方 left）
        self.ed_kw = QLineEdit()
        self.ed_kw.setPlaceholderText('搜文件名 / 书名（逐字精准匹配；回车搜索）')
        self.ed_kw.returnPressed.connect(self.do_search)
        self.b_search = QPushButton('🔍 搜索')
        self.b_search.setToolTip('在库里按文件名 / 书名搜（回车也行）')
        self.b_search.clicked.connect(self.do_search)
        self.b_build = QPushButton('📚 建库 / 刷新')
        self.b_build.setToolTip('把书库里的文件名收进索引库（第一次用、或书库动了之后点它）')
        self.b_build.clicked.connect(self.do_build)
        self._top_widgets = [self.ed_kw, self.b_search, self.b_build]
        # 主体
        sp = QSplitter(Qt.Orientation.Horizontal)
        self.tb = QTableWidget(0, 3)
        self.tb.setHorizontalHeaderLabels(['序号', '文件名', '上级文件夹'])
        self.tb.verticalHeader().setVisible(False)     # batch10：去掉行号列（序号只留一列）
        self.tb.verticalHeader().setDefaultSectionSize(22)
        _hh = self.tb.horizontalHeader()
        _hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        _hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        # batch23：上级文件夹列也让它一起分空白。原来是固定 150px + 文件名列 Stretch，
        # 于是"窗口还有空地方，这一列也只显示几个字"（用户反馈的问题）。
        # 两个 Stretch 会按内容比例分，既不会被长路径撑爆，也不会只剩下一点点。
        _hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        _hh.setStretchLastSection(False)
        _hh.setMinimumSectionSize(24)
        # 列宽要跟着左栏宽度走：拖分割条 / 窗口缩放时用定时器节流重算
        try:
            from PyQt6.QtCore import QTimer as _QT
            self._fitcol_t = _QT(self)
            self._fitcol_t.setSingleShot(True)
            self._fitcol_t.timeout.connect(self._fit_parent_column)
        except Exception:
            self._fitcol_t = None
        self.tb.setWordWrap(False)
        self.tb.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tb.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tb.itemSelectionChanged.connect(self.on_pick)
        self.tb.itemDoubleClicked.connect(self._on_item_dbl)
        self.tb.itemClicked.connect(self._on_item_click)      # 单击文件名即打开
        self.tb.setMinimumWidth(48)           # batch17：可以拖得非常窄（原先 90 仍嫌占位）
        self.tb.setMaximumWidth(880)          # 上限（resizeEvent 里再按窗口比例收紧）
        # 命中关键词在文件名列黄底高亮
        self._last_kw = ''
        self.st.setdefault('alias_mode', 'ask')      # 人名别名：ask / auto / off
        # v0.3.18：文内查找要不要按全库检索那一套规则铺开写法
        self.st.setdefault('find_variant', True)     # 繁简通搜（别的字形一起找）
        self.st.setdefault('find_alias', True)       # 联想词（字号 / 笔名 / 化名一起找）
        # v0.3.19：联想词用到哪些补充来源（地名 / 机构 / 译名 / 人物）。
        # 开关写在两边共用的公共目录里 —— 在 Search 那边关掉的，这里也是关的。
        try:
            QE.load_source_config()
        except Exception:
            pass
        self._hl_kws = []
        self._hi = _HiDelegate(lambda: (getattr(self, '_hl_kws', None)
                                        or ([self._last_kw] if getattr(self, '_last_kw', '') else [])),
                               self.tb)
        self.tb.setItemDelegate(self._hi)
        # 左栏：文件列表 + 「全文搜索本文件夹」按钮
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        # 【v0.3.9】文件名搜索框 / 建库按钮以前占着窗口最上面一整行 —— 可它们只跟
        # "书库浏览"有关，看 PDF 时纯属白占。挪到左栏顶部之后：左栏一收（进入纯
        # 阅读）它们就跟着消失，上下都让给正文，PDF 显示面积实打实变大。
        _libtop = QHBoxLayout()
        _libtop.setContentsMargins(0, 0, 0, 0)
        _libtop.addWidget(self.ed_kw, 1)
        _libtop.addWidget(self.b_search)
        _libtop.addWidget(self.b_build)
        self._lib_top_row = _libtop
        lv.addLayout(_libtop)
        # batch23：改用自动换行布局 —— 左栏窄了这些按钮折到第二行，
        # 而不是被收进「⋮」里"消失"（用户反馈：缩窄窗口好几个按钮就找不到了）
        lh = FlowLayout(margin=0, spacing=4)
        # 【CathayHub 整合】按用户口径改名：说清楚范围是"本文件夹"
        self.b_fts = QPushButton('🔎 全文搜索本文件夹')
        self.b_fts.setToolTip(
            '对当前搜索结果（本文件夹内）的 PDF/文本做全文检索'
            '（另开进程，不卡界面）—— Ctrl+Shift+S')
        self.b_fts.clicked.connect(self.fulltext_search)
        # 新增：全库检索是 CathayHub Search 的活儿，从这里一键唤起
        self.b_hub_search = QPushButton('🌐 全库全文检索')
        self.b_hub_search.setToolTip(
            '跨整个书库检索（交给 CathayHub Search，走 FileLocator 索引，'
            '秒级出结果）—— Ctrl+Shift+G')
        self.b_hub_search.clicked.connect(self.hub_search)
        self.b_fts_hist = QPushButton('🕘 检索历史')
        self.b_fts_hist.setToolTip('调阅历次全文检索结果（自动保存，Ctrl+Shift+F）')
        self.b_fts_hist.clicked.connect(self.fts_history_dialog)
        self.b_alias = QPushButton('👤 人名别名')
        self.b_alias.setToolTip('近代人物字号/笔名/化名归一：检索人物名时可一并检索其别名（Ctrl+Shift+A）')
        self.b_alias.clicked.connect(self.alias_dialog)
        self.b_exc = QPushButton('🗂 摘录截图本')
        self.b_exc.setToolTip('查看 / 编辑已摘录的文字与截图（Ctrl+Shift+M）')
        self.b_exc.clicked.connect(self.excerpt_viewer)
        # 【v0.3.2】打开某本书后左栏会切到"文件阅览"，靠这个按钮回书库浏览
        self.b_lib = QPushButton('🗄 书库浏览')
        self.b_lib.setToolTip('开 / 关 书库浏览：按文件夹逐级展开整个书库，双击打开；'
                              '再点一次就收起左栏，给正文腾出宽度')
        self.b_lib.clicked.connect(self._lib_toggle)
        # batch17：左栏变窄时，上面 4 个按钮收进「⋮」菜单，让文件列表能缩到極小
        from PyQt6.QtWidgets import QToolButton
        self.b_more = QToolButton()
        self.b_more.setText('⋮')
        self.b_more.setToolTip(
            '更多：全文搜索本文件夹 / 全库全文检索 / 检索历史 / 人名别名 / 摘录本')
        try:
            self.b_more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        except Exception:
            pass
        _mmenu = QMenu(self.b_more)
        _mmenu.addAction('🌐 全库全文检索', self.hub_search)
        _mmenu.addAction('🕘 检索历史', self.fts_history_dialog)
        _mmenu.addAction('👤 人名别名', self.alias_dialog)
        _mmenu.addAction('🗂 摘录截图本', self.excerpt_viewer)
        _mmenu.addSeparator()
        # batch23：「独立窗口」按钮撤了，但能力留着 —— 想收回单窗口时从这里切
        _mmenu.addAction('🗗 阅读器独立窗口 / 收回', self.toggle_reader_window)
        self.b_more.setMenu(_mmenu)
        # b_fts 不再入布局：「本文件夹全文检索」已移除（v0.3.2）
        lh.addWidget(self.b_hub_search)
        lh.addWidget(self.b_fts_hist)
        lh.addWidget(self.b_alias)
        lh.addWidget(self.b_exc)
        lh.addWidget(self.b_lib)
        lh.addWidget(self.b_more)
        # 【v0.3.3】书库浏览开着的时候，这里要有个明确的"关掉"（用户反馈 #4：
        # 以前点开就收不回去，左栏一直占着宽度）。
        self.b_left_close = QPushButton('✕')
        self.b_left_close.setToolTip('关闭书库浏览，收起左栏（正文更宽）')
        try:
            self.b_left_close.setFixedWidth(30)
            self.b_left_close.setMinimumHeight(24)
        except Exception:
            pass
        self.b_left_close.clicked.connect(self._lib_toggle)
        lh.addWidget(self.b_left_close)
        self.b_more.setVisible(False)
        lh.addStretch(1)
        # batch20：这排按钮按自然宽度显示（batch17 设的 Ignored 策略会把它们压成 0 宽、整排看不见 —— 已修）；
        # 拖窄时靠 _fit_left_buttons 把次要按钮收进 ⋮，主入口「检索」始终保留
        from PyQt6.QtWidgets import QSizePolicy
        for _b in (self.b_fts, self.b_hub_search, self.b_fts_hist, self.b_alias,
                   self.b_exc, self.b_lib, self.b_more):
            _b.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            _b.setMinimumHeight(24)
        lv.addLayout(lh)
        # 【v0.3.2】书库浏览模式：建库的递归文件树（资源管理器风格，默认折叠）。
        # 库里有 44 万文件 / 1.6 万个文件夹，绝不能一次性全建出来 ——
        # 只先搭文件夹骨架，展开某个文件夹时才去查它下面的文件（见 _tree_expand）。
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['名称'])
        try:
            self.tree.header().setVisible(False)      # 单列，不要表头，更紧凑
            self.tree.setUniformRowHeights(True)
            self.tree.setIndentation(14)
            self.tree.setIconSize(QSize(16, 16))
        except Exception:
            pass
        self.tree.itemExpanded.connect(self._tree_expand)
        self.tree.itemDoubleClicked.connect(self._tree_dbl)
        self.tree.setVisible(False)
        lv.addWidget(self.tree, 1)
        lv.addWidget(self.tb, 1)
        sp.addWidget(left)
        self._left = left
        right = QWidget()
        right.setMinimumWidth(320)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(2, 1, 2, 1)
        rv.setSpacing(2)
        self.lb_info = QLabel('选一个文件看详情')
        self.lb_info.setWordWrap(True)
        self.lb_info.setVisible(False)      # 默认隐藏（正文进 tooltip），点「ⓘ」展开
        self.text_view = TextView()
        self.pdf_view = PdfView()
        self.pdf_view.pageChanged.connect(self._on_pdf_page)
        self.pdf_view.zoomRequested.connect(self._pdf_zoom_step)
        self.pdf_view.fitZoom.connect(self._on_fit_zoom)
        self.pdf_view.selectionMade.connect(self._on_pdf_selection)
        self.pdf_view.regionSelected.connect(self._on_pdf_region)
        self.pdf_view.regionSpansSelected.connect(self._on_pdf_region_spans)
        try:                                  # batch23：文本区也能框选截图
            self.text_view.snapRegionSelected.connect(self._on_text_region)
        except Exception:
            pass
        self.pdf_view.excerptRequested.connect(self._excerpt_from_pdf)
        self.pdf_view.snapshotRequested.connect(self._snapshot_from_pdf)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.text_view)      # 0：文本 / MD / JSON
        self.stack.addWidget(self.pdf_view)       # 1：PDF 连续滚动
        self.dual = DualRead()                     # 2：PDF + TXT 对读
        self.dual.pageChanged.connect(self._on_dual_page)
        self.dual.pdf.regionSelected.connect(self._on_pdf_region)
        self.dual.pdf.regionSpansSelected.connect(self._on_pdf_region_spans)
        self.dual.pdf.excerptRequested.connect(self._excerpt_from_pdf)
        self.dual.pdf.snapshotRequested.connect(self._snapshot_from_pdf)
        self.stack.addWidget(self.dual)
        self.view = self.text_view                # 兼容：文本类代码继续用 self.view
        self.view.setReadOnly(True)
        self.view.installEventFilter(self)      # Ctrl+滚轮缩放（滚轮事件发给 viewport）
        try:
            self.view.viewport().installEventFilter(self)
        except Exception:
            pass
        trow = FlowLayout(margin=0, spacing=4)     # batch22：窗口窄了自动换行
        trow.setContentsMargins(0, 0, 0, 0)
        # 【v0.3.3】左栏收起后，"书库浏览"按钮自己也被一起收进去了 —— 左栏一关
        # 就再也打不开。所以在这条**常驻**的阅读区工具栏上再放一个开关。
        self.b_lib_top = QPushButton('🗄 书库浏览')
        self.b_lib_top.setToolTip(
            '开 / 关 左侧书库浏览：按文件夹逐级展开整个书库，双击打开；'
            '再点一次收起左栏，给正文腾出宽度')
        self.b_lib_top.clicked.connect(self._lib_toggle)
        trow.addWidget(self.b_lib_top)
        # 【v0.3.9】「版本」两个字以前是临时 QLabel，没跟右边的框统一过高度，
        # 于是它比别人矮一截、字贴着顶边。存成属性，下面统一设高 + 垂直居中。
        self.lb_ver = QLabel('版本')
        trow.addWidget(self.lb_ver)
        self.cb_ver = VerComboBox()
        # 【v0.3.9】下拉里平时只显示简称（PDF 原本 / 繁体TXT），点开才亮出文件名
        # 全称 —— 全称动辄五六十个字，摆那儿会把整行挤到第二行去。
        self.cb_ver.setMinimumWidth(128)
        self.cb_ver.setMaximumWidth(230)
        self.cb_ver.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cb_ver.currentIndexChanged.connect(self.on_ver)
        self._fit_ver_width()                # v0.3.13：宽度按简称走，先按"空"收一下
        trow.addWidget(self.cb_ver)
        self.b_info = QPushButton('ⓘ 详情')
        self.b_info.setCheckable(True)
        # batch31：以前这里写死 maxWidth=70，而它自己想要 80 —— 四个字永远
        # 贴着边、（字距被压到最小）看着像被切了一刀。这一行是 FlowLayout，
        # 放不放得下由换行决定，不靠掐按钮宽度。
        self.b_info.setToolTip('显示/隐藏 文件详情（默认隐藏，为阅读让出空间）')
        self.b_info.toggled.connect(self.lb_info.setVisible)
        trow.addWidget(self.b_info)
        # batch23：把这一行的文字标签都存起来，统一高度/垂直居中（跟右边的框对齐）
        # 【v0.3.9】这三个标签拿掉了：它们右边那个框自己就说得清是什么
        # （"浅色/深色/护眼"、"适应宽度/…"、"13"），再加上每样两个字纯属占地。
        # 属性留着是为了不破坏别处的引用，只是不摆进这行了。
        self.lb_theme = QLabel('主题')
        self.cb_theme = QComboBox()
        self.cb_theme.addItems(['浅色', '深色', '护眼'])
        self.cb_theme.setToolTip('阅读底色：浅色 / 深色 / 护眼；最后一项是 PDF 反色开关')
        self.cb_theme.setMaximumWidth(92)         # 三选一，用不着那么宽
        self.cb_theme.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        # batch23：把「PDF 反色」并进主题下拉（最后一项，可勾选）——
        # 少一个控件，同时反色仍能与任意主题自由组合。
        try:
            from PyQt6.QtGui import QStandardItem
            _iv = QStandardItem('◐ 反色')
            _iv.setToolTip('PDF 反色（旧扫描件的白底黑字反过来更省眼）')
            _iv.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                         | Qt.ItemFlag.ItemIsUserCheckable)
            _iv.setCheckState(Qt.CheckState.Unchecked)
            self.cb_theme.model().appendRow(_iv)
        except Exception:
            pass
        trow.addWidget(self.cb_theme)
        self.lb_font = QLabel('字号')
        trow.addWidget(self.lb_font)
        self.sp_font = QSpinBox()
        self.sp_font.setRange(8, 30)
        self.sp_font.setValue(13)
        trow.addWidget(self.sp_font)
        # 字号 / 行距只对文字有意义 —— 【v0.3.9】纯 PDF 时整组收起来
        # （以前只有"行距"藏，字号天天摆在那，却对 PDF 一点用也没有）
        self.lb_line = QLabel('行距')
        trow.addWidget(self.lb_line)
        self.sp_line = QSpinBox()
        self.sp_line.setRange(100, 250)
        self.sp_line.setValue(150)
        self.sp_line.setSingleStep(10)
        self.sp_line.setSuffix('%')
        trow.addWidget(self.sp_line)
        # 【v0.3.9】「缩放」下拉与旁边那个百分比框合成一处：百分比只在选了
        # 「自定义」时才冒出来 —— 平时就一个下拉，位置留给 PDF。
        self.lb_zoom = QLabel('缩放')
        self.cb_zoom = QComboBox()
        self.cb_zoom.addItems(['适应宽度', '适应页面', '100%', '自定义…'])
        self.cb_zoom.setCurrentIndex(1)
        self.cb_zoom.setMaximumWidth(108)
        self.cb_zoom.setToolTip(
            'PDF 缩放：适应宽度 / 适应页面 / 100% / 自定义（Ctrl+滚轮）\n'
            '选「自定义…」后右边会出现百分比框')
        self.cb_zoom.currentIndexChanged.connect(self._zoom_changed)
        trow.addWidget(self.cb_zoom)
        self.sp_zoom = QSpinBox()
        self.sp_zoom.setRange(50, 400)
        self.sp_zoom.setValue(100)
        self.sp_zoom.setSuffix('%')
        self.sp_zoom.setKeyboardTracking(False)
        self.sp_zoom.setFixedWidth(62)
        self.sp_zoom.setToolTip('自定义缩放 50%–400%（回车 / 失焦生效）')
        self.sp_zoom.valueChanged.connect(self._zoom_pct_changed)
        self.sp_zoom.setVisible(False)          # 只有「自定义…」才露面
        trow.addWidget(self.sp_zoom)
        # 隐藏的旧复选框：只作为「反色状态」的存储，界面上由主题下拉的勾选项代理
        self.cb_invert = QCheckBox('◐ PDF 反色')
        self.cb_invert.setVisible(False)
        # 【v0.3.13】这一行以前末尾有个 addStretch —— FlowLayout 里的弹簧会把
        # **本行剩下的宽度全吃掉**，于是它后面的「页 29 / 250 + 翻页」永远放不进
        # 同一行，窗口再宽也是两行。宽度够就该挤成一行，弹簧撤掉。
        self.lb_pageword = QLabel('页')
        trow.addWidget(self.lb_pageword)
        self.ed_page = QLineEdit()
        self.ed_page.setFixedWidth(46)      # v0.3.13：四位页码够用，省一点是一点
        self.ed_page.setToolTip('输入页码后回车跳转（N / M）')
        self.ed_page.setText('0')
        self.ed_page.returnPressed.connect(self.page_jump)
        trow.addWidget(self.ed_page)
        self.lb_pg_total = QLabel('/ 0')
        trow.addWidget(self.lb_pg_total)
        # 【v0.3.13】翻页这块以前是 ⏮ ◀ ▶ ⏭ 四个光秃秃的符号、每个还占 34px：
        # 符号彼此长得像，不悬停根本分不出哪个是"回首页"哪个是"翻一页"。
        # 现在首尾两个直接写汉字「首页」「末页」，中间两个只留箭头（夹在页码框
        # 两侧，方向本身就是意思），宽度也压下来 —— 这块从 148px 缩到 116px。
        self.b_pg_first = QPushButton('首页')
        self.b_pg_first.setToolTip('跳到第 1 页（Ctrl+Home）')
        self.b_pg_first.clicked.connect(self.pdf_home)
        self.b_pg_prev = QPushButton('◀')
        self.b_pg_prev.setToolTip('上一页（PgUp）')
        self.b_pg_prev.clicked.connect(lambda: self.pdf_goto(-1))
        self.b_pg_next = QPushButton('▶')
        self.b_pg_next.setToolTip('下一页（PgDn）')
        self.b_pg_next.clicked.connect(lambda: self.pdf_goto(1))
        self.b_pg_last = QPushButton('末页')
        self.b_pg_last.setToolTip('跳到最后一页（Ctrl+End）')
        self.b_pg_last.clicked.connect(self.pdf_end)
        for _pb, _mw in ((self.b_pg_first, 40), (self.b_pg_prev, 26),
                         (self.b_pg_next, 26), (self.b_pg_last, 40)):
            _pb.setMaximumWidth(_mw)
            _pb.setFixedHeight(22)          # 跟这一行其它控件同高，别又贴着顶边
            trow.addWidget(_pb)
        rv.addLayout(trow)
        for _w in (self.cb_ver, self.cb_theme, self.sp_font, self.sp_line,
                   self.cb_zoom, self.sp_zoom, self.ed_page, self.b_info):
            try:
                _w.setFixedHeight(22)
            except Exception:
                pass
        # batch23：标签与右边的框同高、垂直居中 —— 以前标签各自为高，看着上下错开
        # 【v0.3.9】补上「版本」（它以前是匿名标签，一直是"字贴着顶边"的元凶）
        # 【v0.3.13】补上 lb_pg_total —— 它是「页 29 / 250」里那个「/ 250」，
        # 以前漏在这张"统一高度 + 垂直居中"的名单外，于是它按自己的 sizeHint
        # 高度被 FlowLayout 顶在行首，看着比「页」和页码框高一截。
        for _lb in (self.lb_ver, self.lb_theme, self.lb_font, self.lb_line,
                    self.lb_zoom, self.lb_pageword, self.lb_pg_total):
            try:
                _lb.setFixedHeight(22)
                _lb.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            except Exception:
                pass
        self.cb_theme.currentIndexChanged.connect(self._on_theme_index)
        self.sp_font.valueChanged.connect(self.apply_theme)
        self.sp_line.valueChanged.connect(self.apply_theme)
        self.cb_invert.stateChanged.connect(self._on_invert)
        row = FlowLayout()                   # batch22：窗口窄了自动换行，不挤掉字
        self.b_preview = QPushButton('📖 在阅读区打开')
        self.b_preview.setToolTip('把左栏选中的文件在阅读区打开')
        self.b_preview.clicked.connect(self.preview_here)
        b1 = self.b_preview
        b2 = QPushButton('↗ 用外部程序打开')
        b2.setToolTip('用系统默认程序打开当前文件（双击不再走这里）')
        b2.clicked.connect(self.open_external)
        b3 = QPushButton('📂 打开所在文件夹')
        b3.setToolTip('在资源管理器里打开，并把当前这个文件选中')
        b3.clicked.connect(self.open_folder)
        # 【v0.3.9】「复制引用」与「脚注（贴 Word）」合成一个按钮（`▾` 里换方式）；
        # 挑过一次之后它就是新的默认，下次一按直接生效。
        self.btn_cite = SplitButton()
        self.btn_cite.add_action(
            '📋 复制引用',
            '把当前文件的规范出处（书名 / 册次 / 页码）复制到剪贴板（Ctrl+Shift+C）',
            self.copy_cite)
        self.btn_cite.add_action(
            '📋 复制引用（脚注格式）',
            '把选中引文连出处做成 Word 脚注，粘进去就是真脚注（Ctrl+Shift+I）',
            self.footnote_here)
        b4 = self.btn_cite
        b5 = QPushButton('⤒ 版权页')
        b5.setToolTip('跳到版权页（著录信息最全的那几页）')
        b5.clicked.connect(self.colophon_here)
        # 【v0.3.9】「复制文本」与「复制格式文本」同理合成一个
        self.btn_copy = SplitButton()
        self.btn_copy.add_action(
            '⧉ 复制文本', '按纯文本复制选中的内容（Ctrl+C）', self.copy_text)
        self.btn_copy.add_action(
            '⧉ 复制格式文本',      # 原名「复制带格式」，说法更直白
            '按原排版复制（HTML 格式），贴到 Word 里保留加粗 / 段落等',
            self.copy_html)
        b6 = self.btn_copy
        self.b_md = QPushButton('𝐌D 渲染')          # 只有 MD / 像 Markdown 的 TXT 才显示
        self.b_md.setToolTip('Markdown 渲染 / 源码切换（Ctrl+M）')
        self.b_md.clicked.connect(self.md_toggle)
        b8 = self.b_md
        # 「独立窗口」按钮去掉了（需求：默认就是两窗口，不需要这个开关）；
        # 功能保留在「⋮ 更多」菜单里，万一要收回来还能用。
        self._btn_widgets = [b1, b2, b3, b4, b5, b6, b8]
        _bsnap = QPushButton('📷 截图')
        _bsnap.setToolTip('整页截图 / 自选区域截图，存入「文档\\Cathay文档记录\\截图本」（Ctrl+Shift+X）')
        _bsnap.clicked.connect(self.snapshot_here)
        _bexc = QPushButton('✂ 摘录')
        _bexc.setToolTip('把选中文字存入「文档\\Cathay文档记录\\摘录本」，自动附出处（Ctrl+Shift+E）')
        _bexc.clicked.connect(self.excerpt_here)
        _bdual = QPushButton('⇄ 图文对读')
        _bdual.setToolTip('PDF 与 TXT 对照阅读、按页码同步（Ctrl+Shift+D）')
        _bdual.clicked.connect(self.toggle_dual)
        # 【v0.3.9】脚注不再单占一个按钮 —— 它现在是「📋 复制引用 ▾」里的第二项
        _bchrono = QPushButton('⌛ 纪年换算')
        _bchrono.setToolTip('民国 / 年号 / 干支纪年 ⇄ 公元年（含民国 1–38 年）（Ctrl+Shift+Y）')
        _bchrono.clicked.connect(self.chrono_dialog)
        _brhyme = QPushButton('韵 代日韵目')     # batch24：电报韵目代日表
        _brhyme.setToolTip('电报「韵目代日」对照表（有=25日、艳=29日…），'
                           '输入韵目字或日期即可高亮（Ctrl+Shift+R）')
        _brhyme.clicked.connect(self.rhyme_dialog)
        _bexcv = QPushButton('🗂 摘录截图本')
        _bexcv.setToolTip('查看 / 编辑已摘录的文字与截图（Ctrl+Shift+M）')
        _bexcv.clicked.connect(self.excerpt_viewer)
        self._btn_widgets += [_bsnap, _bexc, _bdual, _bchrono, _brhyme, _bexcv]
        for x in self._btn_widgets:
            row.addWidget(x)
        rv.addWidget(self.lb_info)
        rv.addWidget(self._build_find_bar())
        # batch30：命中词导航条撤掉了 —— 它的活（挑词 / 换词 / 逐处走）已经并入
        # 查找栏那一行。控件还建着（属性与自检脚本要用），但不再摆到界面上，
        # 免得同一批词上下两行各说一遍。
        self._build_hit_bar()
        rv.addWidget(self.stack, 1)
        rv.addLayout(row)
        self.reader_panel = right               # 阅读区（可整体拆到独立窗口）
        self._reader_win = None
        self._split_sizes = None
        sp.addWidget(right)
        sp.setChildrenCollapsible(False)      # 两侧不可被折叠掉
        sp.setCollapsible(0, True)            # batch17：左栏可完全收起（拖到 0 或 Ctrl+Shift+L）
        sp.setCollapsible(1, False)
        sp.setStretchFactor(0, 0)             # 左：列表/详情，不抢空间
        sp.setStretchFactor(1, 1)             # 右：阅读区，占据窗口增量
        sp.setSizes([340, 1160])              # 阅读区默认占大头（窗口变窄时按比例缩放）
        self._split = sp
        self._split.splitterMoved.connect(self._on_split_moved)   # batch17：拖分割条时自适应左栏按钮
        v.addWidget(sp, 1)
        self.statusBar().showMessage('就绪')
        try:
            self._fit_left_buttons(340)
        except Exception:
            pass

    # ---- batch17：左栏宽度自适应／可缩到極小
    def _on_split_moved(self, *_):
        try:
            self._fit_left_buttons(self._split.sizes()[0])
        except Exception:
            pass
        try:                                   # 左栏宽度变了 → 重排「上级文件夹」列
            if getattr(self, '_fitcol_t', None) is not None:
                self._fitcol_t.start(120)
        except Exception:
            pass

    def _fit_left_buttons(self, w):
        """左栏窄 → 次要按钮（检索历史/人名别名/摘录本）收进「⋮」；
        「全文搜索本文件夹」是本栏主入口，**尽量留住**（窄了换短标签，
        再窄只留图标）。「全库全文检索」跟着它一起留/收。"""
        try:
            w = int(w)
        except Exception:
            w = 340
        # v0.3.2：本文件夹检索移除后，「全库全文检索」就是本栏主入口
        primary = getattr(self, 'b_hub_search', None)
        hub = None
        others = [x for x in (getattr(self, 'b_fts_hist', None),
                              getattr(self, 'b_alias', None),
                              getattr(self, 'b_exc', None)) if x is not None]
        more = getattr(self, 'b_more', None)
        # batch23：按钮行改成自动换行后，只要不是特别窄就都露出来（折行显示），
        # 不再"藏起来"；只有真的很窄（<300）才收进「⋮」。
        if w >= 300:
            if primary is not None:
                primary.setText('🌐 全库全文检索' if w >= 560 else '🌐 全库')
            for b in others:
                b.setVisible(True)
            if more is not None:
                more.setVisible(False)
        elif w >= 160:
            if primary is not None:
                primary.setText('🌐 全库')
            if hub is not None:
                hub.setText('🌐')
            for b in others:
                b.setVisible(False)
            if more is not None:
                more.setVisible(True)
        else:
            if primary is not None:
                primary.setText('🔎')
            if hub is not None:
                hub.setText('🌐')
            for b in others:
                b.setVisible(False)
            if more is not None:
                more.setVisible(True)
        tb = getattr(self, 'tb', None)
        if tb is not None:
            tb.setColumnHidden(2, w < 220)     # 太窄时收起「上级文件夹」列

    def toggle_list(self):
        """收起/展开左侧文件列表（Ctrl+Shift+L）。"""
        sp = getattr(self, '_split', None)
        if sp is None:
            return
        try:
            sz = sp.sizes()
        except Exception:
            return
        if sz and sz[0] > 40:
            self._list_prev = int(sz[0])
            sp.setSizes([0, int(sz[1]) + int(sz[0])])
            self.statusBar().showMessage('已收起文件列表（Ctrl+Shift+L 恢复）')
        else:
            w0 = int(getattr(self, '_list_prev', 360) or 360)
            right_w = int(sz[1]) if len(sz) > 1 else 900
            sp.setSizes([w0, max(200, right_w - w0)])
            self.statusBar().showMessage('已展开文件列表')
        try:
            self._fit_left_buttons(sp.sizes()[0])
        except Exception:
            pass

    # ---- 窗口尺寸 / 分割比例
    def _apply_default_size(self):
        """默认窗口尺寸：1200×820（不超过屏幕可用区 80%）——只要能看清 PDF 就好，不占满屏。"""
        try:
            scr = QApplication.primaryScreen()
            g = scr.availableGeometry() if scr is not None else None
            if g is not None and g.width() > 0 and g.height() > 0:
                w, h = min(1200, int(g.width() * 0.8)), min(820, int(g.height() * 0.8))
            else:
                w, h = 1200, 820
        except Exception:
            w, h = 1200, 820
        self.resize(max(1000, w), max(680, h))
        try:
            self._split.setSizes([260, max(700, w - 260)])   # 阅读器约占 3/4
            self._split.setStretchFactor(0, 1)              # 窗口拉伸时阅读器拿大头
            self._split.setStretchFactor(1, 3)
        except Exception:
            pass

    def resizeEvent(self, ev):
        """窗口变化时：左侧列表最大宽度 ≤ 窗口 32%（且 ≤560px），保证阅读区占大头。"""
        try:
            super().resizeEvent(ev)
        except Exception:
            pass
        try:                                    # batch24：空闲态被用户手动拉宽 ⇒ 别再自动还原
            if (getattr(self, '_reader_collapsed', False)
                    and not getattr(self, '_auto_resizing', False)):
                self._user_resized_idle = True
        except Exception:
            pass
        try:
            tb = getattr(self, 'tb', None)
            if tb is not None:
                if getattr(self, '_reader_win', None) is not None:
                    tb.setMaximumWidth(16777215)      # 阅读器已独立 → 列表可占满
                else:
                    tb.setMaximumWidth(max(tb.minimumWidth(),
                                           min(880, int(self.width() * 0.46))))
            self._fit_left_buttons(self._split.sizes()[0])   # batch17：窗口变化时左栏按钮自适应
        except Exception:
            pass
        try:                                    # v0.3.16：进度条跟着窗口宽度走
            bar = getattr(self, '_openbar', None)
            if bar is not None and bar.isVisible():
                bar._place()
        except Exception:
            pass
        try:                                   # batch23：窗口缩放后重排「上级文件夹」列
            if getattr(self, '_fitcol_t', None) is not None:
                self._fitcol_t.start(120)
        except Exception:
            pass

    def _on_item_dbl(self, *_):
        """列表项双击 → 一律在阅读区内部打开（不再甩给外部程序）。"""
        self.open_here()

    def _on_item_click(self, item):
        """单击「文件名」列 → 即打开该文件（单击序号/上级文件夹列不打开）。"""
        if item is None or item.column() != 1:
            return
        r = item.row()
        if self.tb.currentRow() != r:
            self.tb.setCurrentCell(r, 1)
        self.open_here()

    def open_here(self):
        """在阅读区内部打开当前项：PDF/EPUB 走内置引擎渲染，TXT/MD/JSON/CSV 走文本预览。
        记历史/统计，刷新著录与版本下拉；外部程序只由「↗ 用外部程序打开」显式触发。
        batch9：防重入 —— 双击 = 单击 + 双击两次触发，这里只处理一次，避免重复加载卡顿。
        """
        r = self._cur()
        if not r:
            self.statusBar().showMessage('先在列表里选一个文件')
            return False
        p = os.path.join(r.get('dir') or '', r.get('name') or '')
        if not os.path.isfile(p):
            self.statusBar().showMessage('文件不在了：%s' % p)
            return False
        import time
        _now = time.time()
        if getattr(self, '_last_open_path', '') == p \
                and _now - getattr(self, '_last_open_t', 0.0) < 0.6:
            return False                      # 单击后紧跟的双击 → 忽略
        self._last_open_path, self._last_open_t = p, _now
        self.on_pick()                       # 刷新著录信息 + 版本下拉
        ext = ((r.get('ext') or os.path.splitext(p)[1]) or '').lower()
        if ext in ('.pdf', '.epub', '.xps', '.cbz', '.mobi', '.fb2', '.svg'):
            try:
                import fitz
                if ext == '.pdf':
                    with open(p, 'rb') as f:
                        if not f.read(5).startswith(b'%PDF'):
                            raise ValueError('PDF 结构不完整（可先用 CathayRepair 修一下）')
                d = fitz.open(p)
                if int(getattr(d, 'page_count', 0) or 0) <= 0:
                    self.statusBar().showMessage('这个文件没有可显示的页面')
                    return False
                self._set_pdf(d, p)
                self.pgno = 0
                try:
                    _mk = (C.load_settings().get('marks') or {}).get(p) or {}
                    self.pgno = max(0, min(int(d.page_count) - 1, int(_mk.get('page') or 0)))
                except Exception:
                    pass
                self._pdf_show()
                self._after_pdf_open()
                self._hist_add()
                self._stat_open(p)
                self.statusBar().showMessage(
                    '已在阅读区打开：%s（共 %d 页，连续滚动，PgUp/PgDn 翻页）'
                    % (os.path.basename(p), int(d.page_count)))
                return True
            except Exception as e:
                self.statusBar().showMessage(
                    '阅读区打开失败：%s（可点「↗ 用外部程序打开」）' % e)
                return False
        if ext in ('.txt', '.md', '.json', '.csv'):
            try:
                self._show_text_file(p)      # 内部已记统计
                self._hist_add()
                self.statusBar().showMessage('已在阅读区打开：%s' % os.path.basename(p))
                return True
            except Exception as e:
                self.statusBar().showMessage('阅读区打开失败：%s' % e)
                return False
        self.view.setPlainText('这个格式（%s）暂不支持在此预览，'
                               '点「↗ 用外部程序打开」。' % ext)
        self._pdf_stop()
        self._hide_nav()
        self.pd = None
        self._pd_path = ''
        self._set_reader('text')
        return False

    # ---- 状态
    def _refresh_status(self):
        if not self.db or not os.path.isfile(self.db):
            self.statusBar().showMessage('还没建索引 —— 点「建库 / 刷新」开始（只读扫描，不改源库）')
            return
        s = C.stats(self.db)
        self.statusBar().showMessage(
            '索引：%s 个文件 ｜ 库 %.1f MB ｜ %s ｜ 建于 %s%s'
            % (s.get('files'), s.get('db_size', 0) / 1048576.0, self.db,
               s.get('built_at', ''), ' ｜ 缺失 %s' % s.get('missing') if s.get('missing') else ''))

    # ---- 动作
    def do_build(self):
        w = Wizard(self, self.st, first_run=not os.path.isfile(self.db or ''))
        if w.exec() and w.result_info and not w.result_info.get('used_existing'):
            self.st = w.result_info.get('settings', self.st)
            self.db = self.st.get('primary_db', self.db)
            self._refresh_status()
        elif w.result_info and w.result_info.get('used_existing'):
            self.db = w.result_info['db']
            self._refresh_status()

    def do_search(self):
        if not self.db or not os.path.isfile(self.db):
            QMessageBox.information(self, '提示', '还没有索引库，先点「建库 / 刷新」。')
            return
        kw = self.ed_kw.text().strip()
        if not kw:
            return
        # v0.3.2：书库浏览模式下，书名检索结果直接落进左栏那棵文件树
        # （按所属文件夹逐级展开），不必再看扁平列表
        try:
            if getattr(self, '_mode', 'library') == 'library':
                self._tree_filter(kw)
        except Exception:
            pass
        self._search_hist_add(kw)      # 记一条搜索历史（去重、新的在前）
        extras = self._alias_extra(kw)         # 人名别名归一：一并检索字号/笔名/化名
        kws = [kw] + extras
        self._hl_kws = list(kws)               # 多关键词黄底高亮
        rows = []
        for _k in kws:
            try:
                rows += C.search(self.db, _k, 800)
            except Exception:
                pass
        # 同一个文件的多种后缀（PDF/TXT/繁转简…）串成一条，不在列表里并列显示
        seen = {}
        self.rows = []
        for r in rows:
            try:
                q = META.parse(r.get('name') or '',
                               os.path.join(r.get('dir') or '', r.get('name') or ''),
                               deep=False)   # 列表只按文件名分组，不读版权页（快）
                key = (r.get('dir') or '', q.get('name') or r.get('name'), q.get('volume') or '')
            except Exception:
                key = (r.get('dir') or '', r.get('name') or '', '')
            hit = seen.get(key)
            if hit is None:
                # 【v0.3.16】直接记行号：以前这里存的是整行数据，替换时要靠
                # `self.rows.index(整行)` 去找 —— 那是拿整行 dict 逐个比相等，
                # 几千条结果下来是实打实的 O(n²)。
                seen[key] = len(self.rows)
                self.rows.append(r)
            else:
                hn, rn = (self.rows[hit].get('name') or '').lower(), (r.get('name') or '').lower()
                if rn.endswith('.pdf') and not hn.endswith('.pdf'):   # PDF 原本优先
                    self.rows[hit] = r
                    seen[key] = hit
        self._last_kw = kw            # 供文件名列黄底高亮
        self._fill_rows(rows)
        _msg = '搜到 %d 本（原始命中 %d 条，同书各版本已串在一起）' % (len(self.rows), len(rows))
        if len(kws) > 1:
            _msg += '；已并入别名：%s' % '、'.join(extras[:10])
        self.statusBar().showMessage(_msg)

    def _fill_rows(self, rows, dedup=True):
        """填充左栏表格。dedup=True 时把「同书同版本」串一条（PDF 原本优先）；
        dedup=False（直接打开文件时的邻居列表）则**每个文件都保留**。"""
        seen = {}
        self.rows = []
        for r in rows:
            if not dedup:
                self.rows.append(r)
                continue
            try:
                q = META.parse(r.get('name') or '',
                               os.path.join(r.get('dir') or '', r.get('name') or ''),
                               deep=False)   # 列表只按文件名分组，不读版权页（快）
                key = (r.get('dir') or '', q.get('name') or r.get('name'), q.get('volume') or '')
            except Exception:
                key = (r.get('dir') or '', r.get('name') or '', '')
            hit = seen.get(key)
            if hit is None:
                # 【v0.3.16】直接记行号：以前这里存的是整行数据，替换时要靠
                # `self.rows.index(整行)` 去找 —— 那是拿整行 dict 逐个比相等，
                # 几千条结果下来是实打实的 O(n²)。
                seen[key] = len(self.rows)
                self.rows.append(r)
            else:
                hn, rn = (self.rows[hit].get('name') or '').lower(), (r.get('name') or '').lower()
                if rn.endswith('.pdf') and not hn.endswith('.pdf'):   # PDF 原本优先
                    self.rows[hit] = r
                    seen[key] = hit
        self.tb.setRowCount(0)
        for r in self.rows:
            k = self.tb.rowCount()
            self.tb.insertRow(k)
            nm = r.get('name') or ''
            dr = r.get('dir') or ''
            parent = os.path.basename(dr.rstrip('\\/')) or dr      # 只显示上一级
            it0 = QTableWidgetItem(str(k + 1))
            it0.setToolTip('序号')
            it1 = QTableWidgetItem(nm)
            it1.setToolTip(nm)
            it2 = QTableWidgetItem(parent)
            it2.setToolTip(dr)
            self.tb.setItem(k, 0, it0)
            self.tb.setItem(k, 1, it1)
            self.tb.setItem(k, 2, it2)
        try:
            self._fit_parent_column()
        except Exception:
            pass
        try:
            self.tb.viewport().update()
        except Exception:
            pass
        return len(self.rows)

    def _fit_parent_column(self):
        """「上级文件夹」列按内容给宽度（有下限也有上限），剩下的全给文件名列。

        两个 Stretch 时窄窗口会把这一列压成 0（用户反馈：明明还有地方，字却没了）；
        纯 ResizeToContents 又会被超长路径撑爆。所以量完内容夹在 72~220px 之间。
        """
        _hh = self.tb.horizontalHeader()
        _fm = self.tb.fontMetrics()
        _mx = 0
        for r in (self.rows or []):
            _d = (r.get('dir') or '').rstrip('\\/')
            _p = os.path.basename(_d) or _d
            try:
                _mx = max(_mx, _fm.horizontalAdvance(_p) + 16)
            except Exception:
                pass
        try:                                   # 可用宽度：表格减去序号列
            _avail = max(120, self.tb.viewport().width() - _hh.sectionSize(0))
        except Exception:
            _avail = 300
        _w2 = int(min(max(_mx, 72), _avail * 0.5))     # 先按内容，最多占一半
        _w2 = max(_w2, int(_avail * 0.25))             # 再保底：至少四分之一
        _w2 = min(_w2, int(_avail * 0.6))              # 也不许把文件名挤没
        # 两列都自己分（不用 Stretch）—— 窄窗口时 Stretch 会把这一列压成 0；
        # 而且 Qt 会把被压到 0 的列标记为「隐藏」，不重新 show 出来就再也设不上宽度。
        try:
            if _hh.isSectionHidden(2):
                _hh.showSection(2)
            if _hh.isSectionHidden(1):
                _hh.showSection(1)
        except Exception:
            pass
        _hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        _hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        _hh.resizeSection(2, int(_w2))
        _hh.resizeSection(1, int(max(60, _avail - _w2)))

    # ---- batch18：直接打开某文件 → 左栏列出「同文件夹 + 文件名相似」的文件
    def _similar_key(self, name):
        """相似文件名的检索键：书名主干，再去掉「续编 / 补遗 / 外编」这类尾缀。"""
        try:
            s = META.book_core(name or '')
        except Exception:
            s = name or ''
        s2 = re.sub(r'(续编|续集|续录|补遗|补编|外编|附编|新编|前编|后编|别编|二编|三编|再编)$', '', s)
        s2 = s2.strip(' _-—+·、.')
        return s2 or s

    # ------------------------------------------- 书库浏览 / 文件阅览 两种左栏模式
    def _set_mode(self, mode):
        """两种左栏模式（v0.3.2）：

        - **书库浏览**（直接打开本程序时）：左栏是整个书库的递归文件树，
          各级文件夹默认折叠，展开后看得到文件，双击就在右侧打开，
          顶部检索框可按书名过滤。
        - **文件阅览**（打开 CathaySearch 传来的 PDF/TXT，或用户指定打开文件）：
          就专心读这一本，不显示"当前目录"，也没有本文件夹检索。
        """
        mode = 'file' if mode == 'file' else 'library'
        self._mode = mode
        lib = (mode == 'library')
        try:
            # 【v0.3.3】文件阅览：把**整块左栏**收起来，而不只是藏里面的控件。
            # 以前只藏 tree/tb，左栏容器仍占着那一大条，PDF 右侧平白少几百像素。
            _lf = getattr(self, '_left', None)
            if _lf is not None:
                _lf.setVisible(lib)
            self.tree.setVisible(lib)
            self.tb.setVisible(False)     # 两种模式都不再显示旧的扁平列表
            _bl = getattr(self, 'b_lib', None)
            if _bl is not None:
                _bl.setVisible(not lib)   # 已经在书库浏览了就不用显示这个按钮
        except Exception:
            pass
        if lib:
            self._lib_tree_build()

    def _lib_toggle(self):
        """🗄 书库浏览：开 / 关。

        【v0.3.3】原先这个按钮只能"开"，开了就关不掉（用户反馈 #4）。现在是个
        开关：左栏开着就收起来，收着就展开成书库树。
        """
        try:
            _lf = getattr(self, '_left', None)
            _open = bool(_lf is not None and _lf.isVisibleTo(_lf.parentWidget())
                         and _lf.isVisible())
        except Exception:
            _open = False
        if _open:
            self._set_mode('file')
            self._say('已关闭书库浏览（再点「🗄 书库浏览」可打开）', hold=3)
        else:
            self._set_mode('library')

    def _lib_tree_build(self):
        """搭书库的**文件夹骨架**（只建文件夹，文件等展开时再挂）。

        44 万文件要是全塞进树里，光建树就得几十秒、内存也吃不消；文件夹只有
        1.6 万个，先搭骨架（默认折叠）、按需展开才跑得动。
        """
        if getattr(self, '_tree_built', False):
            return
        db = getattr(self, 'db', '') or ''
        if not db or not os.path.isfile(db):
            self._say('还没有建库：点顶部「建库 / 刷新」先扫一遍书库', hold=4)
            return
        # 【v0.3.3】只统计"打得开"的那些格式。以前用 COUNT(*) 全算，于是树里
        # 冒出大量标着几百、点开却空空如也的文件夹（库里 15% .htm / 13% .xls，
        # 而这些当时根本没被列出来）。
        try:
            rows = C.dir_counts(db, getattr(C, 'EXTS', ('.pdf', '.txt')))
        except Exception as e:
            self._say('读取书库失败：%s' % e, hold=4)
            return
        if not rows:
            self._say('库里还没有可打开的文件（.pdf/.txt/.htm 等）', hold=4)
            return
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        nodes = {}
        for dirpath, n in rows:
            dirpath = (dirpath or '').strip()
            parts = [p for p in re.split(r'[\\/]+', dirpath) if p]
            if not parts:
                continue
            for k in range(len(parts)):
                cur = os.sep.join(parts[:k + 1])
                if cur in nodes:
                    continue
                up = nodes.get(os.sep.join(parts[:k])) if k else None
                it = QTreeWidgetItem(up if up is not None else self.tree,
                                     [parts[k]])
                it.setData(0, Qt.ItemDataRole.UserRole + 1, cur)   # 完整路径
                nodes[cur] = it
            last = nodes.get(os.sep.join(parts))
            if last is not None:
                last.setText(0, '%s  (%d)' % (parts[-1], n))
        self.tree.setUpdatesEnabled(True)
        self._tree_built = True
        self._say('书库浏览：%d 个文件夹，逐级展开，双击文件打开' % len(nodes),
                  hold=4)

    def _tree_expand(self, item):
        """展开某个文件夹时才去库里取它下面的文件挂上去（懒加载）。

        【v0.3.3】两个真 bug，都会让人以为"这树里根本没有文件"：

        1. **提前 return**：骨架阶段已经把子文件夹挂成了子节点，于是
           `childCount()` 恒大于 0，这里一律直接返回 —— 结果**只有最末级
           文件夹**才会去取文件，中间层永远看不到文件。改成用"已加载"标记，
           而不是用有没有子节点来判断。
        2. **扩展名只放行 .pdf/.txt**：库里 15% 是 .htm、13% 是 .xls，
           大量文件夹点开后一个文件都不显示。改成按 C.EXTS（27 种）过滤，
           并且在 SQL 层就过滤（否则大目录取回的前 800 条可能全是别的格式）。
        """
        if item is None:
            return
        _R = Qt.ItemDataRole.UserRole
        try:
            if item.data(0, _R + 2):        # 已加载过
                return
            item.setData(0, _R + 2, True)
            d = item.data(0, _R + 1) or ''
            db = getattr(self, 'db', '') or ''
            if not d or not db:
                return
            _lim = 800
            try:
                fs = C.by_dir_ext(db, d, getattr(C, 'EXTS', ('.pdf', '.txt')),
                                  limit=_lim + 1)
            except Exception:
                fs = []
            more = len(fs) > _lim
            for r in (fs or [])[:_lim]:
                nm = (r.get('name') or '').strip()
                if not nm:
                    continue
                it = QTreeWidgetItem(item, [nm])
                it.setData(0, _R, os.path.join(r.get('dir') or '', nm))
                it.setToolTip(0, os.path.join(r.get('dir') or '', nm))
            if more:
                tip = QTreeWidgetItem(item, ['… 还有更多文件未列出'])
                tip.setDisabled(True)
            # 资源管理器风格：**文件夹在前、文件在后**，各自按名称排
            self._tree_sort_children(item)
            if not item.childCount():
                ph = QTreeWidgetItem(item, ['（空）'])
                ph.setDisabled(True)
        except Exception:
            pass

    def _tree_sort_children(self, item):
        """子节点排序：文件夹在前、文件在后，各自按名称。

        文件是展开时才插进去的，会跟预先建好的子文件夹混在一起 —— 不排一次
        就会出现"文件夹在两个文件夹中间"的乱序。
        """
        if item is None:
            return
        _R = Qt.ItemDataRole.UserRole
        try:
            kids = []
            while item.childCount():
                kids.append(item.takeChild(0))
            def _k(k):
                return (k.text(0) or '').lower()
            folders = sorted([k for k in kids if not k.data(0, _R)], key=_k)
            files = sorted([k for k in kids if k.data(0, _R)], key=_k)
            for k in folders + files:
                item.addChild(k)
        except Exception:
            pass

    def _tree_dbl(self, item, col=0):
        """双击树里的文件：在右侧打开。双击文件夹：展开 / 收起。

        【v0.3.3】以前只处理文件，文件夹那一下等于没反应 —— 用户报的
        "双击也没有显示最末级文件夹下的文件"就是这么来的：光靠默认的
        expandsOnDoubleClick，在这棵动态加载的树上并不总能触发。
        """
        try:
            p = item.data(0, Qt.ItemDataRole.UserRole) if item else ''
        except Exception:
            p = ''
        if p and os.path.isfile(p):
            self._open_path(p)
            return
        try:                              # 文件夹：手动开合
            if item is not None:
                item.setExpanded(not item.isExpanded())
        except Exception:
            pass

    def _tree_filter(self, kw):
        """按书名过滤：命中的文件按所属文件夹逐级展开成一棵小树。"""
        kw = (kw or '').strip()
        if not kw:
            self._tree_built = False
            self._lib_tree_build()
            return
        db = getattr(self, 'db', '') or ''
        if not db or not os.path.isfile(db):
            return
        try:
            fs = C.search(db, kw, limit=800)
        except Exception:
            fs = []
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        nodes = {}
        for r in fs or []:
            d = (r.get('dir') or '').strip()
            nm = (r.get('name') or '').strip()
            if not nm:
                continue
            parts = [p for p in re.split(r'[\\/]+', d) if p]
            parent = None
            for k in range(len(parts)):
                cur = os.sep.join(parts[:k + 1])
                if cur not in nodes:
                    it = QTreeWidgetItem(parent if parent is not None
                                         else self.tree, [parts[k]])
                    it.setData(0, Qt.ItemDataRole.UserRole + 1, cur)
                    # 过滤模式下这些文件夹**按筛选结果**展示，不能再让
                    # _tree_expand 把整个目录的文件都灌进来（那样等于没过滤）
                    it.setData(0, Qt.ItemDataRole.UserRole + 2, True)
                    nodes[cur] = it
                parent = nodes[cur]
            it = QTreeWidgetItem(parent if parent is not None else self.tree,
                                 [nm])
            it.setData(0, Qt.ItemDataRole.UserRole, os.path.join(d, nm))
        self.tree.expandAll()
        self.tree.setUpdatesEnabled(True)
        self._say('书名检索「%s」：%d 个' % (kw, len(fs or [])), hold=4)

    def _list_neighbors(self, p, limit_same=300, limit_like=300):
        """把 p 所在文件夹的文件 + 与 p 书名主干相似的文件填到左栏（并尽量选中 p）。"""
        p = os.path.abspath(p)
        d = os.path.dirname(p)
        base = os.path.basename(p)
        _stem = os.path.splitext(base)[0]
        try:
            self.ed_kw.setText(_stem)
        except Exception:
            pass
        self._last_kw = ''
        self._hl_kws = []
        rows, seen = [], set()

        def _add(rs):
            for r in rs:
                try:
                    k = (os.path.normcase(r.get('dir') or ''),
                         (r.get('name') or '').lower())
                except Exception:
                    continue
                if k in seen:
                    continue
                seen.add(k)
                rows.append(r)

        n_same = 0
        # ① 同文件夹（先查索引库，再把磁盘上没进库的也补上）
        try:
            same = C.by_dir(self.db, d, limit_same) if self.db else []
        except Exception:
            same = []
        n_same = len(same)
        _add(same)
        try:
            for e in os.scandir(d):
                if not e.is_file():
                    continue
                if os.path.splitext(e.name)[1].lower() not in C.EXTS:
                    continue
                try:
                    stt = e.stat()
                except OSError:
                    continue
                _add([{'dir': d, 'name': e.name, 'size': stt.st_size,
                       'mtime': stt.st_mtime}])
        except OSError:
            pass
        # ② 书名主干相似（不限文件夹）：主干 + 去掉「续编/补遗」等尾缀的更宽键
        try:
            core = META.book_core(base) or _stem
        except Exception:
            core = _stem
        try:
            core2 = self._similar_key(base)
        except Exception:
            core2 = core
        keys = []
        for k in (core, core2):
            if k and k not in keys:
                keys.append(k)
        n_like = 0
        for k in keys:
            try:
                like = C.like_stem(self.db, k, limit_like) if self.db else []
                n_like += len(like)
                _add(like)
            except Exception:
                pass
        n = self._fill_rows(rows, dedup=False)          # 邻居列表：每个文件都列出来
        # 选中打开的那一个
        want = os.path.normcase(p)
        for i, r in enumerate(self.rows):
            if os.path.normcase(os.path.abspath(
                    os.path.join(r.get('dir') or '', r.get('name') or ''))) == want:
                try:
                    self.tb.setCurrentCell(i, 0)
                except Exception:
                    pass
                break
        self.statusBar().showMessage(
            '左侧：同文件夹 %d 个 ｜ 相似文件名 %d 个（共 %d 个）' % (n_same, n_like, n))
        return n

    def _meta_brief(self, name, path):
        """著录列文本：作者 · 出版社 · 年 · SSID（只解析文件名，deep=False，快）。"""
        try:
            q = META.parse(name or '', path or '', deep=False)
        except Exception:
            return ''
        bits = []
        try:
            for b in (q.get('author'), q.get('publisher'),
                      ((q.get('year') or '') + '年') if q.get('year') else '',
                      ('SSID ' + q.get('ssid')) if q.get('ssid') else ''):
                if b:
                    bits.append(b)
        except Exception:
            pass
        return ' · '.join(bits)

    # ---- 搜索历史 / 已保存的搜索（Ctrl+K / Ctrl+D）----
    # ---- 人名别名归一（👤 / Ctrl+Shift+A）
    def _alias_extra(self, kw):
        """kw 是收录的人物时，返回要一并检索的字号/笔名/化名（按设置询问/自动/关闭）。"""
        try:
            extra = ALIAS.expand(kw)
        except Exception:
            extra = []
        if not extra:
            return []
        mode = self.st.get('alias_mode', 'ask')
        if mode == 'off' or getattr(self, '_alias_silent', False):
            return []
        if mode == 'auto':
            return extra
        try:
            r = QMessageBox.question(
                self, '人名别名',
                '「%s」还有其他字号 / 笔名 / 化名：\n\n%s\n\n是否连同这些名字一起检索？'
                % (kw, '、'.join(extra[:20]) + ('…等 %d 个' % len(extra) if len(extra) > 20 else '')),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            return extra if r == QMessageBox.StandardButton.Yes else []
        except Exception:
            return []

    def _refresh_alias_count(self, lbl):
        try:
            c = ALIAS.count()
            lbl.setText('已收录 %d 人 / %d 个字号·笔名·化名；检索到人物名时按设置处理。'
                        % (c['people'], c['aliases']))
        except Exception:
            pass

    def alias_dialog(self):
        """👤 人名别名表：查看/增删/导入导出，并设置检索时的处理方式。

        batch22：改成**非模态**（不锁前台，主窗口还能继续用），
        并且「查一个人」查不到时会明确弹框告诉用户（以前只在状态栏提示，
        被这个窗口挡着看不见，看起来像没反应）。
        """
        from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QFileDialog, QInputDialog
        # CathayHub：联想词两边共用一份，开窗口前先重读，
        # 这样在 CathayHub Search 那边刚改的这里立刻就能看见
        try:
            ALIAS.reload()
        except Exception:
            pass
        _open_dlg = getattr(self, '_alias_dlg', None)
        if _open_dlg is not None:
            try:
                _open_dlg.show()
                _open_dlg.raise_()
                _open_dlg.activateWindow()
                return
            except Exception:
                pass
        dlg = QDialog(self)
        dlg.setWindowTitle('人名别名表')
        dlg.resize(780, 640)
        vb = QVBoxLayout(dlg)
        head = QLabel('')
        head.setWordWrap(True)
        vb.addWidget(head)
        row = QHBoxLayout()
        row.addWidget(QLabel('检索时：'))
        cb = QComboBox()
        cb.addItems(['询问我（默认）', '自动并入别名', '关闭别名归一'])
        cb.setCurrentIndex({'ask': 0, 'auto': 1, 'off': 2}.get(self.st.get('alias_mode', 'ask'), 0))

        def _mode(i):
            self.st['alias_mode'] = {0: 'ask', 1: 'auto', 2: 'off'}[i]
            try:
                C.save_settings(self.st)
            except Exception:
                pass
            self.statusBar().showMessage('人名别名：%s' % cb.currentText())
        cb.currentIndexChanged.connect(_mode)
        row.addWidget(cb)
        row.addStretch(1)
        vb.addLayout(row)

        # v0.3.19：除了 CBDB 人名，联想词还认哪些同实异名 —— 按来源单独开关。
        # 开关写进公共目录，CathaySearch 那边共用同一份。
        try:
            _srcs = QE.source_names()
        except Exception:
            _srcs = []
        if _srcs:
            _lab = {'place': '地名', 'org': '机构',
                    'foreign': '译名', 'figure': '人物'}
            _tip = {
                'place': '北平→北京、奉天→沈阳 这类历史政区异名',
                'org': '中研院→中央研究院、北大→北京大学 这类机构简称',
                'foreign': '沙士比亚→莎士比亚、奈端→牛顿 这类旧译名',
                'figure': '康熙→清圣祖、孙中山→国父 这类年号庙号别称',
            }
            gb = QGroupBox('联想词来源（CBDB 人名之外，还认哪些同实异名）')
            hb = QHBoxLayout(gb)
            for _n in _srcs:
                ck = QCheckBox(_lab.get(_n, _n))
                ck.setChecked(QE.source_enabled(_n))
                ck.setToolTip(_tip.get(_n, _n))

                def _on(v, _n=_n, _lab=_lab):
                    QE.set_source_enabled(_n, bool(v), save=True)
                    self.statusBar().showMessage(
                        '联想词来源「%s」已%s'
                        % (_lab.get(_n, _n), '开启' if v else '关闭'))
                ck.toggled.connect(_on)
                hb.addWidget(ck)
            hb.addStretch(1)
            vb.addWidget(gb)

        lst = QListWidget()
        vb.addWidget(lst, 1)

        def _fill():
            lst.clear()
            g = ALIAS.groups()
            for canon in sorted(g.keys(), key=lambda x: (len(x), x)):
                als = g.get(canon) or []
                it = QListWidgetItem('%s：%s' % (canon, '、'.join(als[:12]) + ('…' if len(als) > 12 else '')))
                it.setData(Qt.ItemDataRole.UserRole, canon)
                it.setToolTip(canon + '：' + '、'.join(als))
                lst.addItem(it)
        _fill()
        self._refresh_alias_count(head)
        brow = FlowLayout()                  # batch22：窗口窄了自动换行
        b_add = QPushButton('新增/编辑')
        b_del = QPushButton('删除')
        b_imp = QPushButton('导入 CSV')
        b_exp = QPushButton('导出 CSV')
        b_cbdb = QPushButton('从 CBDB 导入…')
        b_find = QPushButton('查一个人')
        b_close = QPushButton('关闭')
        for w in (b_add, b_del, b_imp, b_exp, b_cbdb, b_find):
            brow.addWidget(w)
        brow.addStretch(1)
        brow.addWidget(b_close)
        vb.addLayout(brow)

        def do_add():
            canon, okk = QInputDialog.getText(dlg, '新增/编辑', '正名（如 吴佩孚）：')
            if not okk or not (canon or '').strip():
                return
            als, ok2 = QInputDialog.getText(dlg, '别名', '该人的字/号/笔名/化名（顿号或逗号分隔）：')
            if not ok2:
                return
            ALIAS.add_person(canon.strip(), als)
            _fill()
            self._refresh_alias_count(head)

        def do_del():
            it = lst.currentItem()
            if not it:
                return
            canon = it.data(Qt.ItemDataRole.UserRole)
            if QMessageBox.question(dlg, '删除', '从本地表里删除「%s」？' % canon) \
                    == QMessageBox.StandardButton.Yes:
                ALIAS.remove_person(canon)
                _fill()
                self._refresh_alias_count(head)

        def do_imp():
            p, _ = QFileDialog.getOpenFileName(dlg, '导入别名 CSV', '', 'CSV (*.csv);;所有文件 (*)')
            if not p:
                return
            n = ALIAS.import_csv(p)
            _fill()
            self._refresh_alias_count(head)
            self.statusBar().showMessage('导入完成：新增 %d 人 / %d 别名' % (n[0], n[1]))

        def do_exp():
            p, _ = QFileDialog.getSaveFileName(dlg, '导出别名 CSV', 'person_alias.csv', 'CSV (*.csv)')
            if p:
                ALIAS.write_csv(p)
                self.statusBar().showMessage('已导出：%s' % p)

        def do_cbdb():
            d = QFileDialog.getExistingDirectory(
                dlg, '选 CBDB 导出目录（含 ALTNAME_DATA.xlsx / BIOG_MAIN.xlsx）')
            if not d:
                return
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                n = ALIAS.import_cbdb_dir(d)
                _fill()
                self._refresh_alias_count(head)
                self.statusBar().showMessage('CBDB 导入完成：%d 人 / %d 别名' % (n[0], n[1]))
            except Exception as e:
                QMessageBox.warning(dlg, '导入失败', str(e))
            finally:
                QApplication.restoreOverrideCursor()

        def do_find():
            k, okk = QInputDialog.getText(dlg, '查一个人', '输入人名或字号：')
            if not okk or not (k or '').strip():
                return
            k = k.strip()
            hits = ALIAS.lookup(k)
            if not hits:
                # 以前只往状态栏发消息，被这个窗口挡着根本看不见 —— 现在明确弹框
                QMessageBox.information(
                    dlg, '没查到',
                    '没查到「%s」。\n\n'
                    '可能的原因：\n'
                    '· 别名表里还没收录这个人；\n'
                    '· 换他的正名、字、号或笔名再试试；\n'
                    '· 点「＋ 添加」可以自己补一条，或「导入 CBDB」批量补充。'
                    % k)
                self.statusBar().showMessage('人名别名：未收录 %s' % k)
                return
            QMessageBox.information(dlg, '查询结果', '\n\n'.join(
                '正名：%s\n别名：%s' % (h['canonical'], '、'.join(h['aliases'])) for h in hits))

        b_add.clicked.connect(do_add)
        b_del.clicked.connect(do_del)
        b_imp.clicked.connect(do_imp)
        b_exp.clicked.connect(do_exp)
        b_cbdb.clicked.connect(do_cbdb)
        b_find.clicked.connect(do_find)
        b_close.clicked.connect(dlg.accept)
        self._last_alias_dlg = {'dlg': dlg, 'list': lst, 'combo': cb}
        try:
            dlg.setModal(False)              # batch22：不锁前台
        except Exception:
            pass
        dlg.finished.connect(lambda *_: setattr(self, '_alias_dlg', None))
        self._alias_dlg = dlg
        dlg.show()

    def _search_hist_add(self, kw):
        """Enter 搜索时自动记一条（去重、新的在前、最多 50 条）。"""
        kw = (kw or '').strip()
        if not kw:
            return
        try:
            st = C.load_settings()
            h = [x for x in (st.get('search_hist') or []) if x != kw]
            h.insert(0, kw)
            st['search_hist'] = h[:50]
            C.save_settings(st)
        except Exception:
            pass

    def _rerun_search(self, kw):
        """用给定关键词重跑一次搜索（历史/已保存双击用）。"""
        try:
            self.ed_kw.setText(kw or '')
            self.do_search()
            self.statusBar().showMessage('已重搜：%s' % kw)
        except Exception as e:
            self.statusBar().showMessage('重搜失败：%s' % e)

    def save_search(self):
        """Ctrl+D：把当前关键词存入 settings['saved_searches']。"""
        kw = self.ed_kw.text().strip()
        if not kw:
            self.statusBar().showMessage('搜索框是空的，先输入关键词再按 Ctrl+D')
            return
        try:
            st = C.load_settings()
            s = [x for x in (st.get('saved_searches') or []) if x != kw]
            s.insert(0, kw)
            st['saved_searches'] = s[:50]
            C.save_settings(st)
            self.statusBar().showMessage('已保存搜索：%s（Ctrl+K 查看/删除）' % kw)
        except Exception as e:
            self.statusBar().showMessage('保存失败：%s' % e)

    def search_history_dialog(self):
        """Ctrl+K：搜索历史 + 已保存的搜索（双击重搜，可删除）。"""
        try:
            st = C.load_settings()
            hist = list(st.get('search_hist') or [])
            saved = list(st.get('saved_searches') or [])
        except Exception:
            hist, saved = [], []
        dlg = QDialog(self)
        dlg.setWindowTitle('搜索历史 / 已保存的搜索')
        dlg.resize(640, 540)
        vb = QVBoxLayout(dlg)
        vb.addWidget(QLabel('历史（最近 %d 条，双击重搜）：' % len(hist)))
        lh = QListWidget()
        for k in hist:
            lh.addItem(k)
        vb.addWidget(lh, 1)
        vb.addWidget(QLabel('已保存（Ctrl+D 添加，双击重搜）：'))
        ls = QListWidget()
        for k in saved:
            ls.addItem(k)
        vb.addWidget(ls, 1)

        def go(kw):
            self._rerun_search(kw)
            dlg.accept()

        def go_h():
            i = lh.currentRow()
            if 0 <= i < len(hist):
                go(hist[i])

        def go_s():
            i = ls.currentRow()
            if 0 <= i < len(saved):
                go(saved[i])

        def dele():
            kill = set()
            for it in lh.selectedItems():
                kill.add(it.text())
            for it in ls.selectedItems():
                kill.add(it.text())
            if not kill:
                self.statusBar().showMessage('先选中要删除的条目')
                return
            try:
                stt = C.load_settings()
                stt['search_hist'] = [x for x in (stt.get('search_hist') or [])
                                      if x not in kill]
                stt['saved_searches'] = [x for x in (stt.get('saved_searches') or [])
                                         if x not in kill]
                C.save_settings(stt)
            except Exception as e:
                self.statusBar().showMessage('删除失败：%s' % e)
                return
            for lst, items in ((lh, list(lh.selectedItems())),
                               (ls, list(ls.selectedItems()))):
                for it in items:
                    lst.takeItem(lst.row(it))
            self.statusBar().showMessage('已删除 %d 条（历史/已保存）' % len(kill))

        lh.itemDoubleClicked.connect(lambda _: go_h())
        ls.itemDoubleClicked.connect(lambda _: go_s())
        self._last_hist = {'hist': hist, 'saved': saved}
        row = FlowLayout()                   # batch22：窗口窄了自动换行
        bd = QPushButton('删除选中')
        bd.clicked.connect(dele)
        bf = QPushButton('关闭')
        bf.clicked.connect(dlg.accept)
        row.addWidget(bd)
        row.addStretch(1)
        row.addWidget(bf)
        vb.addLayout(row)
        self._show_util_dialog(dlg, '_dlg_hist')

    # ---- 阅读统计（打开次数 / 累计时长 / 最常阅读）----
    def _stat_open(self, path):
        """打开文件时调用：先结算上一个文件，再给这个文件记一次打开。"""
        try:
            import time
            self._stat_flush()            # 结算上一个文件
            if not path:
                return
            st = C.load_settings()
            stats = st.get('stats') or {}
            e = stats.get(path) or {}
            e['opens'] = int(e.get('opens') or 0) + 1
            e['secs'] = float(e.get('secs') or 0.0)
            e['last'] = time.strftime('%m-%d %H:%M')
            stats[path] = e
            st['stats'] = stats
            C.save_settings(st)
            self._stat_path = path
            self._stat_t0 = time.time()
        except Exception as ex:
            self._stat_path = ''
            self._stat_t0 = 0.0
            self.statusBar().showMessage('统计记录失败：%s' % ex)

    def _stat_flush(self):
        """把当前文件的阅读时长累加进 settings['stats']（切换文件/关窗时）。"""
        p = getattr(self, '_stat_path', '') or ''
        t0 = getattr(self, '_stat_t0', 0.0) or 0.0
        self._stat_path = ''
        self._stat_t0 = 0.0
        if not p or not t0:
            return
        try:
            import time
            secs = max(0.0, time.time() - t0)
            st = C.load_settings()
            stats = st.get('stats') or {}
            e = stats.get(p) or {}
            e['secs'] = float(e.get('secs') or 0.0) + secs
            e['opens'] = int(e.get('opens') or 0)
            e['last'] = time.strftime('%m-%d %H:%M')
            stats[p] = e
            st['stats'] = stats
            C.save_settings(st)
        except Exception as ex:
            self.statusBar().showMessage('统计写入失败：%s' % ex)

    def _stats_text(self):
        """阅读统计文本：总累计时长 + 最近 20 本 + 最常阅读 Top 20。"""
        def fmt(s):
            s = float(s or 0)
            if s < 60:
                return '%.0f 秒' % s
            if s < 3600:
                return '%.1f 分钟' % (s / 60.0)
            return '%.1f 小时' % (s / 3600.0)

        try:
            st = C.load_settings()
            stats = {p: e for p, e in (st.get('stats') or {}).items()
                     if isinstance(e, dict)}
        except Exception:
            stats = {}
        total = sum(float(e.get('secs') or 0) for e in stats.values())

        def line(p, e):
            return '%-30s  打开 %d 次 · 累计 %s · 最后 %s' % (
                os.path.basename(p), int(e.get('opens') or 0),
                fmt(e.get('secs')), e.get('last') or '')

        recent = sorted(stats.items(), key=lambda x: str(x[1].get('last') or ''),
                        reverse=True)[:20]
        top = sorted(stats.items(),
                     key=lambda x: (int(x[1].get('opens') or 0),
                                    float(x[1].get('secs') or 0)),
                     reverse=True)[:20]
        L = ['总累计阅读时长：%s（共 %d 本有记录）' % (fmt(total), len(stats)), '',
             '— 最近读的 20 本 —']
        L += [line(p, e) for p, e in recent] or ['（暂无）']
        L += ['', '— 最常阅读 Top 20（按打开次数 / 时长）—']
        L += [line(p, e) for p, e in top] or ['（暂无）']
        return '\n'.join(L)

    def read_stats_dialog(self):
        """Ctrl+Shift+H：阅读统计对话框。"""
        txt = self._stats_text()
        self._last_stats_text = txt
        dlg = QDialog(self)
        dlg.setWindowTitle('阅读统计')
        dlg.resize(680, 540)
        vb = QVBoxLayout(dlg)
        te = QTextEdit()
        te.setReadOnly(True)
        te.setPlainText(txt)
        vb.addWidget(te, 1)
        bb = QPushButton('好')
        bb.clicked.connect(dlg.accept)
        vb.addWidget(bb)
        self._show_util_dialog(dlg, '_dlg_stats')

    def closeEvent(self, ev):
        """关窗：停后台线程（深著录 + 扫词）+ 结算阅读时长 + 记住布局 + 一并关掉独立阅读窗口。"""
        try:
            self._stop_meta_worker()
        except Exception:
            pass
        try:
            self._stop_scan_worker()
        except Exception:
            pass
        # v0.3.7：顺序预读线程也收掉（同上，运行中被销毁会崩）
        try:
            _pf = getattr(self, '_pdf_prefetch', None)
            self._pdf_prefetch = None
            if _pf is not None and _pf.isRunning():
                _pf.stop()
                _pf.wait(1000)
        except Exception:
            pass
        # 【v0.3.16】还在后台扫尺寸的开档线程也收掉 —— 以前漏了它：抽样命中的
        # 书（`_pdf_size_worker` 还活着）正扫到一半被关窗，就是一次原生崩。
        try:
            self._stop_open_worker()
        except Exception:
            pass
        # batch22：渲染线程必须在窗口/控件销毁前收干净（QThread 运行中被销毁会原生崩）
        for _pv in (getattr(self, 'pdf_view', None),
                    getattr(getattr(self, 'dual', None), 'pdf', None)):
            try:
                if _pv is not None:
                    _pv._stop_render_worker()
            except Exception:
                pass
        try:
            self._stat_flush()
        except Exception:
            pass
        # 【v0.3.1 多标签】每个标签都是一整个 MainWindow 实例，它们各自带着
        # 渲染线程——必须在这里逐个关掉（触发各自的 closeEvent 收线程），
        # 否则"QThread 运行中被销毁"，退出那一刻直接原生崩。
        try:
            for _it in list(getattr(self, '_tabs', []) or []):
                _mw = _it.get('mw')
                if _mw is None or _mw is self:
                    continue
                try:
                    _mw._cv_no_attach = True
                    _mw.close()
                except Exception:
                    pass
            self._tabs = [x for x in (getattr(self, '_tabs', []) or [])
                          if x.get('mw') is self]
        except Exception:
            pass
        self._save_layout()
        # 【v0.3.14】停掉后台抽全文的线程（QThread 运行中被销毁会原生崩）
        try:
            self._stop_text_warmup()
        except Exception:
            pass
        # batch18：阅读器是独立顶层窗口，主窗口退出时把它一并关掉（否则程序不会退出）
        try:
            win = getattr(self, '_reader_win', None)
            if win is not None:
                self._closing = True
                win._cv_no_attach = True
                win.close()
                self._reader_win = None
        except Exception:
            pass
        try:
            super().closeEvent(ev)
        except Exception:
            pass

    # ---- batch9：窗口布局记忆（尺寸/位置、分割比例、是否拆窗）
    def _layout_dict(self):
        d = {'win_geom': [self.x(), self.y(), self.width(), self.height()],
             'reader_detached': getattr(self, '_reader_win', None) is not None}
        try:
            _sz = list(self._split.sizes())
            # 阅读区处于「收起点开书之前」的折叠态时不要把 [大,0] 写回去，
            # 否则下次启动会把正常比例冲掉
            if len(_sz) == 2 and int(_sz[1]) > 0:
                d['split_sizes'] = _sz
            else:
                d['split_sizes'] = (getattr(self, '_split_sizes_idle', None)
                                    or [340, 1160])
        except Exception:
            pass
        w = getattr(self, '_reader_win', None)
        if w is not None:
            try:
                d['reader_geom'] = [w.x(), w.y(), w.width(), w.height()]
            except Exception:
                pass
        return d

    def _save_layout(self):
        if not getattr(self, '_layout_track', False):
            return
        try:
            st = C.load_settings()
            st.update(self._layout_dict())
            C.save_settings(st)
        except Exception:
            pass

    def _restore_layout(self):
        """启动时恢复上次的窗口布局；返回是否应拆成双窗口。"""
        try:
            st = C.load_settings()
        except Exception:
            return False
        g = st.get('win_geom')
        if isinstance(g, (list, tuple)) and len(g) == 4:
            try:
                self.setGeometry(int(g[0]), int(g[1]),
                                 max(1000, int(g[2])), max(680, int(g[3])))
            except Exception:
                pass
        sz = st.get('split_sizes')
        if isinstance(sz, (list, tuple)) and len(sz) == 2:
            try:
                self._split.setSizes([max(160, int(sz[0])), max(300, int(sz[1]))])
            except Exception:
                pass
        self._layout_track = True
        return bool(st.get('reader_detached'))

    # ---- batch22：工具类窗口统一「非模态」显示
    def _show_util_dialog(self, dlg, key):
        """把工具窗口按非模态显示：不锁前台，主窗口还能继续用。

        key 用于防重复：同一个工具再点一次就把它带到前面，不会叠一堆窗口。
        """
        try:
            dlg.setModal(False)
        except Exception:
            pass
        old = getattr(self, key, None)
        if old is not None and old is not dlg:
            try:
                old.close()
            except Exception:
                pass
        try:
            def _forget(*_a):
                if getattr(self, key, None) is dlg:
                    setattr(self, key, None)
            dlg.finished.connect(_forget)
        except Exception:
            pass
        try:
            setattr(self, key, dlg)
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
        except Exception:
            pass

    # ---- batch22：启动时没打开文件，就把阅读区收起来，只留文件列表
    def _collapse_reader_if_idle(self):
        """刚启动、还没打开任何文件 → 阅读区收起（一片空白占着半屏没意义）。

        用户一点开书（PDF/TXT/对读）会自动展开，见 _expand_reader()。
        """
        try:
            if getattr(self, '_reader_collapsed', False):
                return                     # 已经收过了，别再来一遍
            if getattr(self, '_reader', '') or getattr(self, 'pd', None) is not None:
                return                     # 已经打开东西了（命令行带文件等），别动
            if getattr(self, '_reader_win', None) is not None:
                return                     # 拆窗模式：阅读器在另一个窗口，这里不用管
            sz = list(self._split.sizes())
            if len(sz) != 2 or int(sz[1]) <= 0:
                return                     # 还没布局好（分裂条尺寸为 0），等下一次机会
            self._split_sizes_idle = [int(sz[0]), int(sz[1])]
            # 右侧设了 setCollapsible(False)，直接 setSizes(...,0) 会被它的最小宽度
            # 顶住（实测还留 320px）。隐藏面板才真的不占地方。
            self.reader_panel.hide()
            self._reader_collapsed = True
            self._shrink_to_list()         # batch24：顺手把窗口收到「列表够用」的宽度
            self.statusBar().showMessage('就绪 —— 在左栏点「文件名」就能开始阅读')
        except Exception:
            pass

    def _shrink_to_list(self):
        """空闲态（没收任何文件）时：窗口只留文件列表，别把列表拉成整屏宽。

        记住原来的窗口尺寸，打开文件时由 _expand_reader() 还原回去。
        """
        try:
            g = self.geometry()
            self._idle_full_geom = (int(g.x()), int(g.y()),
                                    int(g.width()), int(g.height()))
            keep = int((getattr(self, '_split_sizes_idle', None) or [340, 0])[0])
            tgt = int(min(max(560, keep + 300), 760))       # 列表宽度留点余量，最多 760
            if tgt >= int(g.width()) - 40:                  # 本来就不宽，别折腾
                return
            try:
                scr = (QApplication.screenAt(g.center()) or QApplication.primaryScreen())
                _av = scr.availableGeometry()
            except Exception:
                _av = None
            if _av is not None:
                tgt = max(400, min(tgt, int(_av.width()) - 40))
                x0 = max(int(_av.x()),
                         min(int(g.x()), int(_av.x()) + max(0, int(_av.width()) - tgt)))
            else:
                x0 = int(g.x())
            self._auto_resizing = True
            self.setGeometry(int(x0), int(g.y()), int(tgt), int(g.height()))
            self._auto_resizing = False
        except Exception:
            self._auto_resizing = False

    def _expand_reader(self):
        """打开文件时把（被收起的）阅读区放回来。"""
        # batch31：启动时因为"没有要打开的文件"而压着没拆的双窗，现在书来了，
        # 该兑现了 —— 之前空着弹个白板窗口没意义，此刻才有内容可放。
        if getattr(self, '_pending_two_window', False):
            self._pending_two_window = False
            try:
                self.start_two_window_mode()
            except Exception:
                pass
        if not getattr(self, '_reader_collapsed', False):
            return
        self._reader_collapsed = False
        try:
            # batch24：先还原窗口宽度（空闲态为了让列表不至于太宽把它收窄了）
            _fg = getattr(self, '_idle_full_geom', None)
            _w = int(self.width())
            if _fg and not getattr(self, '_user_resized_idle', False):
                _w = max(1000, int(_fg[2]))
                try:
                    scr = (QApplication.screenAt(self.geometry().center())
                           or QApplication.primaryScreen())
                    _av = scr.availableGeometry()
                    _w = min(_w, max(600, int(_av.width()) - 40))
                    x0 = max(int(_av.x()),
                             min(int(_fg[0]),
                                 int(_av.x()) + max(0, int(_av.width()) - _w)))
                    self._auto_resizing = True
                    self.setGeometry(int(x0), int(_fg[1]), int(_w), int(_fg[3]))
                    self._auto_resizing = False
                except Exception:
                    self._auto_resizing = False
            self.reader_panel.show()
            self._split.setSizes([0, 0])          # 先让布局重算
            total = max(700, _w - 20)
            keep = getattr(self, '_split_sizes_idle', None) or [340, 1160]
            w0 = max(220, min(int(keep[0]), int(total * 0.55)))
            self._split.setSizes([w0, max(300, total - w0)])
        except Exception:
            pass

    def _cur(self):
        i = self.tb.currentRow()
        return self.rows[i] if 0 <= i < len(self.rows) else None

    def on_pick(self):
        """选中一项 → 立即用「浅著录」显示详情（不阻塞），深著录（读 PDF 文字层）放后台线程。 batch16"""
        r = self._cur()
        if not r:
            return
        p = os.path.join(r.get('dir') or '', r.get('name') or '')
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            mtime = 0.0
        try:
            nkey = os.path.normcase(os.path.abspath(p))
        except Exception:
            nkey = p
        key = (nkey, round(float(mtime), 3))
        cache = getattr(self, '_meta_map', None)
        if cache is None:
            cache = self._meta_map = {}
        m = cache.get(key)
        deep = bool(m is not None and m.get('_deep'))
        if m is None:
            try:
                m = META.parse(r.get('name') or '', p, deep=False)
            except Exception:
                m = {'name': r.get('name') or '', 'volume': '', 'author': '',
                     'publisher': '', 'year': '', 'ssid': '', 'trad': False,
                     'ext': '', 'tail': '', 'path': p, 'raw': r.get('name') or ''}
            if len(cache) > 400:
                cache.clear()
            cache[key] = m
        self.meta = m
        self._meta_cache = {'_k': (p, mtime), '_m': m}      # 兼容旧引用
        self._render_info(r, m)
        self._fill_versions(m)
        if not deep:
            self._request_deep_meta(r.get('name') or '', p, mtime, key)

    def _render_info(self, r, m):
        """把著录信息写进详情条（浅/深著录共用）。"""
        try:
            p = os.path.join(r.get('dir') or '', r.get('name') or '')
            bits = [b for b in (m.get('author'), m.get('volume'), m.get('publisher'),
                                ((m.get('year') or '') + '年') if m.get('year') else '',
                                ('SSID ' + m['ssid']) if m.get('ssid') else '',
                                '【繁体】' if m.get('trad') else '') if b]
            info = ('<b>%s</b><br>%s<br>%.1f KB ｜ %s<br>'
                    '<span style="color:#1e7a6f">%s</span>'
                    % (m.get('name') or r.get('name'), p,
                       (r.get('size') or 0) / 1024.0, _ts(r.get('mtime')),
                       ' ｜ '.join(bits) or '（文件名里没有元数据）'))
            _rb = self._rights_text(m)
            if _rb:
                info += '<br><span style="color:#8a5a00">版权/授权：%s</span>' % _rb
            if not m.get('_deep'):
                info += '<br><span style="color:#888">（正在后台深度著录…）</span>'
            self.lb_info.setText(info)
            _tip = (re.sub(r'<br\s*/?>', '\n', info).replace('&nbsp;', ' ')
                    .replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&'))
            self.lb_info.setToolTip(_tip)
            self.b_info.setToolTip('文件详情（默认隐藏）：\n' + _tip)
        except Exception:
            pass

    # ---- batch16：后台深著录（不卡界面）
    # ── 【v0.3.14】文字层后台预热 ─────────────────────────────────
    def _stop_text_warmup(self, ms=3000):
        """换书 / 关窗：丢掉正在跑的预热线程（QThread 运行中被销毁会原生崩）。"""
        t = getattr(self, '_warm_timer', None)
        if t is not None:
            try:
                t.stop()
            except Exception:
                pass
        w = getattr(self, '_warm_worker', None)
        self._warm_worker = None
        if w is None:
            return
        try:
            w.stop()
            if w.isRunning():
                w.wait(int(ms))
        except Exception:
            pass

    def _start_text_warmup(self, path, npages=0):
        """开完书安排一次后台抽全文 —— 等用户按下 Ctrl+F 时就不用再等那几秒。

        延后 TEXT_WARMUP_DELAY_MS 再开工：头一两秒主线程正忙着渲首屏、预取邻页，
        这时候插一脚全本解析会把"打开"本身拖慢，反而绕远。
        """
        self._stop_text_warmup()
        try:
            self._warm_pending = None
            if not path or npages < TEXT_WARMUP_MIN_PAGES:
                return                              # 小书现抽也不过零点几秒
            if pdf_text_cache_get(path) is not None:
                return                              # 已经抽过了
            t = getattr(self, '_warm_timer', None)
            if t is None:
                t = QTimer(self)
                t.setSingleShot(True)
                t.timeout.connect(self._text_warmup_go)
                self._warm_timer = t
            self._warm_pending = path
            t.start(int(TEXT_WARMUP_DELAY_MS))
        except Exception:
            pass

    def _text_warmup_go(self):
        path = getattr(self, '_warm_pending', None)
        self._warm_pending = None
        if not path:
            return
        try:
            if pdf_text_cache_get(path) is not None:
                return
            w = TextLayerWorker(path, self)
            w.done.connect(self._on_text_warm_done)
            self._warm_worker = w
            w.start()
        except Exception:
            pass

    def _on_text_warm_done(self, path, npages):
        """全文已备好：让当前这本书立刻能用上，省得查找时再抽一遍。"""
        try:
            if path is None or not os.path.isfile(path):
                return
            d = getattr(self, 'pd', None)
            if d is None or npages != int(getattr(d, 'page_count', 0) or 0):
                return
            if os.path.normcase(getattr(self, '_pd_path', '') or '') != os.path.normcase(path):
                return
            c = pdf_text_cache_get(path)
            if c is None or len(c) != npages:
                return
            self._pd_texts = c                      # 同一份对象，flat 缓存才能命中
            self._pd_texts_doc = id(d)
        except Exception:
            pass

    def _stop_open_worker(self, ms=2500):
        """【v0.3.16】收掉开档线程：可能一个是"正开着"的，一个是"还在扫尺寸"的。

        以前只有 `_pdf_open_worker` 在函数退出时被置 None，`_pdf_size_worker`
        （抽样命中后继续扫全本尺寸的那一个）没人管，closeEvent 里也没停 ——
        扫到一半关窗就是"QThread 运行中被销毁"原生崩。
        """
        for _k in ('_pdf_open_worker', '_pdf_size_worker'):
            w = getattr(self, _k, None)
            try:
                setattr(self, _k, None)
            except Exception:
                pass
            try:
                if w is not None and w.isRunning():
                    try:
                        w.stop()
                    except Exception:
                        pass
                    w.wait(int(ms))
            except Exception:
                pass

    def _stop_meta_worker(self, ms=4000):
        """关窗/退出前等后台线程结束（QThread 运行中被销毁会触发原生 fail-fast）。

        【v0.3.16】深著录读的是整本 PDF 的文字层，大书能跑到十几秒 —— 以前
        只等 4 秒就撒手，等不动的那次恰恰最容易崩（线程还活着，窗口却拆了）。
        现在等不动就**把它摘出去**：脱离窗口父子关系、由模块级名单接住，让它
        自己在后台跑完，不再跟着窗口一起被销毁。
        """
        self._meta_closing = True
        self._meta_pending = None
        w = getattr(self, '_meta_worker', None)
        self._meta_worker = None
        if w is None:
            return
        try:
            if w.isRunning():
                w.wait(int(ms))
        except Exception:
            pass
        try:
            if w.isRunning():                 # 还在跑：摘出去，别被窗口带走
                w.setParent(None)
                _ORPHAN_THREADS.append(w)
        except Exception:
            pass

    def _request_deep_meta(self, name, path, mtime, key):
        """请求深著录。

        batch20：延后 900 ms 再真正启动 —— 深著录会再 open 一遍这个 PDF，
        立刻跑会和首屏渲染抢磁盘 IO（150 MB 的书实测能把首屏拖慢一倍）。
        """
        if getattr(self, '_meta_closing', False):
            return
        cur = getattr(self, '_meta_worker', None)
        if cur is not None and cur.isRunning():
            self._meta_pending = (name, path, mtime, key)
            return
        try:
            QTimer.singleShot(900, lambda: self._start_deep_meta(name, path, mtime, key))
        except Exception:
            self._start_deep_meta(name, path, mtime, key)

    def _start_deep_meta(self, name, path, mtime, key):
        if getattr(self, '_meta_closing', False):
            return
        cur = getattr(self, '_meta_worker', None)
        if cur is not None and cur.isRunning():
            self._meta_pending = (name, path, mtime, key)
            return
        try:
            w = MetaWorker(name, path, mtime, self)
        except Exception:
            return
        w.done.connect(self._on_deep_meta)
        self._meta_worker = w
        self._meta_pending = None
        try:
            w.start()
        except Exception:
            pass

    def _on_deep_meta(self, path, mtime, m):
        try:
            if m:
                m['_deep'] = True
                try:
                    nkey = os.path.normcase(os.path.abspath(path))
                except Exception:
                    nkey = path
                key = (nkey, round(float(mtime), 3))
                if getattr(self, '_meta_map', None) is None:
                    self._meta_map = {}
                self._meta_map[key] = m
                r = self._cur()
                cp = os.path.join(r.get('dir') or '', r.get('name') or '') if r else ''
                if r and os.path.normcase(os.path.abspath(cp)) == nkey:
                    self.meta = m
                    self._meta_cache = {'_k': (cp, mtime), '_m': m}
                    self._render_info(r, m)
        except Exception:
            pass
        finally:
            pend = getattr(self, '_meta_pending', None)
            self._meta_pending = None
            if pend:
                self._request_deep_meta(*pend)
            else:
                # 当前项仍是浅著录 → 继续补深著录
                try:
                    r = self._cur()
                    if r:
                        p = os.path.join(r.get('dir') or '', r.get('name') or '')
                        mm = self._meta_map.get((os.path.normcase(os.path.abspath(p)),
                                                 round(float(os.path.getmtime(p)), 3)))
                        if mm is not None and not mm.get('_deep'):
                            self._request_deep_meta(r.get('name') or '', p,
                                                    os.path.getmtime(p),
                                                    (os.path.normcase(os.path.abspath(p)),
                                                     round(float(os.path.getmtime(p)), 3)))
                except Exception:
                    pass

    def _rights_text(self, m):
        """把版权/授权信息拼成一行（标注来源：文件名 / 上级目录 / 版权页）。"""
        rr = (m.get('rights') or {}) if isinstance(m, dict) else {}
        src = (m.get('rights_src') or {}) if isinstance(m, dict) else {}
        labels = [('authorization', '授权'), ('copyright_holder', '版权'),
                  ('copyright_line', '版权'), ('rights_holder', '出版发行'),
                  ('isbn', 'ISBN'), ('edition', '版次'), ('printing', '印次'),
                  ('pub_date', '出版年月')]
        srcname = {'file': '文件名', 'folder': '上级目录', 'colophon': '版权页'}
        out = []
        for k, lab in labels:
            v = rr.get(k)
            if v:
                tag = srcname.get(src.get(k), '')
                out.append('%s %s%s' % (lab, v, ('（%s）' % tag) if tag else ''))
        return ' ｜ '.join(out)

    def _fill_versions(self, m):
        """按书名把同一本书的各版本都找出来（PDF 原本 / 繁转简 TXT / 其它）。"""
        self.vers = []
        self.cb_ver.blockSignals(True)
        self.cb_ver.clear()
        try:
            cand = C.search(self.db, m.get('name') or '', 200) if m.get('name') else []
            for r in cand:
                q = META.parse(r.get('name') or '',
                               os.path.join(r.get('dir') or '', r.get('name') or ''),
                               deep=False)   # 同书判定用文件名即可
                if q.get('name') == m.get('name') and q.get('volume') == m.get('volume'):
                    self.vers.append(q)
            self.vers = META.versions_of(self.vers, m.get('name'), m.get('volume'))
        except Exception:
            pass
        for v in self.vers:
            # 【v0.3.13】只有一本书、没有别的 TXT 时，简称就写「原版PDF」——
            # 「PDF 原本」四个字既绕口又比实际需要长，占着工具栏的宽度。
            tag = '原版PDF' if (v.get('ext') == '.pdf' and not v.get('tail')) else \
                (v.get('ext', '').lstrip('.').upper() + (v.get('tail') or ''))
            full = '%s ｜ %s' % (tag, os.path.basename(v.get('path') or ''))
            # v0.3.9：收起时只写简称，点开才给全称（见 VerComboBox）
            try:
                self.cb_ver.add_version(tag, full)
            except Exception:                      # 万一拿到的是普通 QComboBox
                self.cb_ver.addItem(full)
        self.cb_ver.blockSignals(False)
        self._fit_ver_width()
        if self.vers:
            self.statusBar().showMessage('这本书有 %d 个版本（下拉可切）' % len(self.vers))

    def _fit_ver_width(self):
        """【v0.3.13】版本框按**简称**的实际宽度定尺寸，不再占 128–230。

        以前写死 min 128 / max 230：一本书往往只有一个「原版PDF」，六个字符的
        框硬占 128px，把整行挤到第二行去。现在量一下最长那个简称要几个字就给
        几个字（再加上拉箭头那点宽）—— 只有「原版PDF」时框就很短；多个版本时
        也只按简称排，全称留给点开以后的下拉。
        """
        try:
            fm = self.cb_ver.fontMetrics()
            w = 0
            for i in range(self.cb_ver.count()):
                s = ''
                try:
                    s = self.cb_ver.short_text(i)
                except Exception:
                    s = ''
                if not s:
                    s = self.cb_ver.itemText(i)
                w = max(w, fm.horizontalAdvance(str(s or '')))
            w = int(min(max(w + 40, 76), 230))     # 40：下拉箭头 + 左右内边距
            self.cb_ver.setMinimumWidth(w)
            self.cb_ver.setMaximumWidth(w)
        except Exception:
            pass

    def bookmark_here(self):
        """记住当前位置（写进设置；下次打开同一文件自动跳回）。Ctrl+B"""
        r = self._cur()
        if not r:
            return
        import time
        p = os.path.join(r.get('dir') or '', r.get('name') or '')
        try:
            st = C.load_settings()
            bm = st.get('marks') or {}
            _pg1 = self._pdf_page_1based()   # v0.3.12：双页时记焦点页，不是左边那页
            bm[p] = {'page': max(0, int(_pg1) - 1),
                     'at': time.strftime('%Y-%m-%d %H:%M')}
            st['marks'] = bm
            C.save_settings(st)
            self._hist_add()
            self.statusBar().showMessage('已记住位置：第 %d 页' % int(_pg1))
        except Exception as e:
            self.statusBar().showMessage('记忆失败：%s' % e)

    def toc_here(self):
        """Ctrl+T：显示导航面板的「目录」页签（PDF / EPUB 大纲；没大纲就列页码）。"""
        _mw = self._tab_current_mw()      # v0.3.21：多标签 → 交给当前那本
        if _mw is not None and _mw is not self:
            return _mw.toc_here()
        cur = self._path()
        ext = os.path.splitext(cur)[1].lower()
        openable = ext in ('.pdf', '.epub', '.xps', '.cbz', '.mobi', '.fb2', '.svg')
        d = getattr(self, 'pd', None)
        # 当前选中项是可渲染的电子书（含 EPUB），且不是已打开的那本 → 就地打开它
        if cur and os.path.isfile(cur) and openable and \
                (d is None or getattr(self, '_pd_path', '') != cur):
            try:
                import fitz
                d = fitz.open(cur)
                self._set_pdf(d, cur)
                self.pgno = 0
                self._pdf_show()
                self._after_pdf_open()
            except Exception:
                d = getattr(self, 'pd', None)
        if d is None:
            self.statusBar().showMessage('目录只对已打开的 PDF / EPUB 有效（先点 📖 在阅读区打开）')
            return
        n = self._fill_nav_toc()
        self._show_nav(0)
        self.statusBar().showMessage('目录：%d 项（Ctrl+T 显示/隐藏）' % n)

    def open_path_in_reader(self, p, note='', page=None):
        """把某个文件在阅读区内部打开；左栏自动列出同文件夹 + 文件名相似的文件（batch18）。

        page: 1 基页码，给了就打开后直接翻到那一页（batch27，供 CathaySearch 调起）。
        """
        # v0.3.2：打开某本书 → 进入「文件阅览」，左栏不再显示当前目录
        self._set_mode('file')
        try:
            if self.db and os.path.isfile(self.db):
                # 【v0.3.3】这个查询**不能省**。v0.3.2 为了"打开更快"把它去掉了，
                # 结果 self.rows 一直是空的，而 `_cur()` 是从 rows 里取当前文件的 ——
                # 于是「⇄ 对读」「Ctrl+R 相关文件」全都报"先在列表里选一本书"（用户反馈 #5）。
                #
                # 左栏虽已收起（文件阅览模式），这份数据仍要给对读用。
                # limit_like=0：跳过"书名相似"那两次全库模糊查，只取同文件夹，
                # 够对读用了，也免得把打开时间拖长。
                self._list_neighbors(p, limit_same=300, limit_like=0)
            else:                                # 没索引库：退回按文件名搜
                self.ed_kw.setText(os.path.splitext(os.path.basename(p))[0])
                self.do_search()
            want = os.path.normcase(os.path.abspath(p))
            cur = -1
            for i, r in enumerate(self.rows):
                rp = os.path.normcase(os.path.abspath(
                    os.path.join(r.get('dir') or '', r.get('name') or '')))
                if rp == want:
                    cur = i
                    break
            if cur < 0 and self.rows:
                cur = 0                          # 库里没登记 → 仍打开文件，列表选第一个
            if cur >= 0:
                self.tb.setCurrentCell(cur, 0)
                self.on_pick()
        except Exception:
            pass
        ok = self._open_path(p, start_page=page)   # 一律在阅读区内部打开
        if ok:
            msg = '已在阅读区打开：%s%s' % (os.path.basename(p), note or '')
            if page:
                if self._goto_page(page):
                    msg += '，已翻到第 %d 页' % page
                else:
                    msg += '（第 %d 页没能跳过去，可能不是 PDF 或页码超出）' % page
            self._say(msg, hold=1.5)
        return ok

    def _pdf_ready(self, doc=None):
        """这本书是不是已经装在 PdfView 里、页控件也都建好了？

        batch29：用来判断"能不能直接跳页"。以前 `_goto_page` 无条件
        `_pdf_show()`，遇上 `--page` 就把同一份文档整体重装一遍，代价是
        整本扫描 + 几千个页控件重建（这段时间界面是冻住的）。
        """
        doc = doc if doc is not None else getattr(self, 'pd', None)
        v = getattr(self, 'pdf_view', None)
        if v is None or doc is None:
            return False
        if getattr(v, 'doc', None) is not doc:
            return False
        try:
            n = int(getattr(doc, 'page_count', 0) or 0)
        except Exception:
            return False
        if n <= 0:
            return False
        items = getattr(v, '_items', None) or []
        return len(items) == n

    def _goto_page(self, n):
        """翻到 1 基页码 n。成功返回 True（batch27）。

        只在 PDF/EPUB 已打开时有效；页码越界会自动夹到首页/末页。

        【batch29】文档已经在视图里就**只跳页**，不再整本重装一次
        （`_pdf_show` 会走 `set_document`，那是一次全量重建）。
        """
        try:
            n = int(n)
        except Exception:
            return False
        doc = getattr(self, 'pd', None)
        try:
            total = doc.page_count if doc is not None else 0
        except Exception:
            total = 0
        if total <= 0:
            return False
        n = 1 if n < 1 else (total if n > total else n)
        self.pgno = n - 1
        if self._pdf_ready(doc):
            try:
                self.pdf_view.goto_page(self.pgno)
            except Exception:
                return False
            # stack 只管"把阅读区显示出来"，它失手不影响页已经翻过去了
            try:
                self.stack.setCurrentWidget(self.pdf_view)
            except Exception:
                pass
        else:
            try:
                self._pdf_show()
            except Exception:
                return False
        try:
            self._sync_page_box()
        except Exception:
            pass
        try:
            self._upd_status_pdf()
        except Exception:
            pass
        try:
            self._expand_reader()
            self.stack.setCurrentWidget(self.pdf_view)
        except Exception:
            pass
        return True

    # ---- batch15：拖入文件即打开（PDF / TXT 等）
    def _url_reader_ok(self, u):
        try:
            p = u.toLocalFile()
            return bool(p) and os.path.isfile(p) and \
                os.path.splitext(p)[1].lower() in READER_EXTS
        except Exception:
            return False

    def dragEnterEvent(self, ev):
        try:
            md = ev.mimeData()
            if md is not None and md.hasUrls() and any(self._url_reader_ok(u) for u in md.urls()):
                ev.acceptProposedAction()
                return
        except Exception:
            pass
        try:
            ev.ignore()
        except Exception:
            pass

    def dragMoveEvent(self, ev):
        try:
            ev.acceptProposedAction()
        except Exception:
            pass

    def dropEvent(self, ev):
        """把拖入的 PDF / TXT（可多选）在阅读区打开；多个时只开第一个并提示。"""
        paths = []
        try:
            for u in ev.mimeData().urls():
                p = u.toLocalFile()
                if p and os.path.isfile(p):
                    paths.append(os.path.abspath(p))
        except Exception:
            paths = []
        try:
            ev.acceptProposedAction()
        except Exception:
            pass
        if not paths:
            self.statusBar().showMessage('拖入的内容不是文件（支持把 PDF / TXT 等文件拖进来）')
            return
        p = paths[0]
        ext = os.path.splitext(p)[1].lower()
        if ext not in READER_EXTS:
            self.statusBar().showMessage('这个格式暂不支持在阅读区打开：%s' % os.path.basename(p))
            return
        note = ('（另有 %d 个拖入文件已忽略）' % (len(paths) - 1)) if len(paths) > 1 else ''
        self.open_path_in_reader(p, note)
        self._hist_add()

    # ------------------------------------------- 命令行参数（含 IPC 转发）
    def _cli_params(self, argv=None):
        """从命令行里拎出：要打开的文件们 + --page / --hits / --words。

        GUI 启动（open_cli_arg）和 IPC 转发（别的进程把活塞进来）走的是
        同一套规矩，别各写各的。
        """
        argv = list(sys.argv) if argv is None else list(argv)
        # 注意：第 0 个是程序自己的路径，不是书。cli_file_args(None) 内部取
        # sys.argv[1:]；一旦把完整列表传进去，"我自己"就成了一本待开的书
        # ——吃过这个亏：会把 _probe 脚本自身打开，还引发一轮递归。
        files = cli_file_args(argv[1:] if argv is not None else None)
        page = None
        try:
            if '--page' in argv:
                page = int(argv[argv.index('--page') + 1])
        except Exception:
            page = None
        hits = ''
        try:
            if '--hits' in argv:
                hits = argv[argv.index('--hits') + 1]
        except Exception:
            hits = ''
        words = []
        try:
            if '--words' in argv:
                words = [w.strip() for w in re.split(
                    r'[、,\t|]', argv[argv.index('--words') + 1]) if w.strip()]
        except Exception:
            words = []
        return files, page, hits, words

    # ---------------------------------------------------- 多标签（v0.3.1）
    TAB_SAFE_MB = 900          # 给系统、给别的应用留的安全水位
    TAB_SLOT_MB = 120          # 一个标签界面本身的固定开销（粗估）

    def _tab_avail_mb(self):
        """系统当前可用物理内存（MB）；量不到返回 None（那时不拦人）。"""
        try:
            import ctypes

            class MEM(ctypes.Structure):
                _fields_ = [('dwLength', ctypes.c_ulong),
                            ('dwMemoryLoad', ctypes.c_ulong),
                            ('ullTotalPhys', ctypes.c_ulonglong),
                            ('ullAvailPhys', ctypes.c_ulonglong),
                            ('ullTotalPageFile', ctypes.c_ulonglong),
                            ('ullAvailPageFile', ctypes.c_ulonglong),
                            ('ullTotalVirtual', ctypes.c_ulonglong),
                            ('ullAvailVirtual', ctypes.c_ulonglong),
                            ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]

            m = MEM()
            m.dwLength = ctypes.sizeof(MEM)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return m.ullAvailPhys / (1024.0 * 1024.0)
        except Exception:
            pass
        return None

    def _tab_cost_mb(self, path):
        """打开这一本大约要占多少 MB：文件本身 + 按页数估的渲染缓存。"""
        try:
            size = os.path.getsize(path) / (1024.0 * 1024.0)
        except Exception:
            size = 0.0
        mb = size * 0.35 + self.TAB_SLOT_MB
        if str(path).lower().endswith('.pdf'):
            try:
                import fitz
                d = fitz.open(path)
                mb += d.page_count * 0.25
                d.close()
            except Exception:
                mb += 30
        return mb

    def _tab_room_ok(self, path):
        """按"剩余内存 + 这本书要多大"决定还能不能再开一个标签。"""
        avail = self._tab_avail_mb()
        if avail is None:
            return True, ''
        need = self._tab_cost_mb(path)
        if avail - need >= self.TAB_SAFE_MB:
            return True, ''
        return False, ('内存不够再开一本了：现在可用 %.0f MB，这本大约要'
                       '%.0f MB（至少留 %.0f MB 才安全）。先关掉几个标签再来。'
                       % (avail, need, self.TAB_SAFE_MB))

    def _tab_title(self, path):
        n = os.path.basename(path) if path else '空'
        return (n[:26] + '…') if len(n) > 27 else (n or '空')

    def _tab_current_mw(self):
        """当前标签对应的那个实例（还没标签条时就是自己）。

        【v0.3.21】多标签下，主窗口的快捷键（Ctrl+T 目录 / Ctrl+Shift+T 缩略图 /
        Ctrl+F 查找）绑的都是主实例自己。不转发的话，在第 2、3 个标签里按这些
        键，动的却是第 1 本书 —— 面板和快捷键得一起跟到当前标签才算数。
        """
        tb = getattr(self, '_tabbar', None)
        if tb is None:
            return self
        i = tb.currentIndex()
        tabs = getattr(self, '_tabs', None) or []
        if 0 <= i < len(tabs):
            return tabs[i].get('mw') or self
        return self

    def _nav_handoff(self, mw):
        """【v0.3.21】右栏「导航 – 目录 / 缩略图 / 查找」跟着当前标签走。

        一本 = 一个 MainWindow 实例，导航面板（QDockWidget）建在各自的窗口上；
        而标签里只搬了阅读区 —— 于是无论切到第几本，右栏挂着的永远是最先那本
        的面板：目录是它的、查找结果也是它的（第 2、3 本压根没带检索词，却一直
        显示着第 1 本的命中）。这里把当前那本的面板搬到主窗口右侧，上一本的
        还回去。
        """
        if mw is None:
            return
        prev = getattr(self, '_nav_dock_from', None)
        if prev is mw:
            return
        # 搬运过程中 dock 的可见性会变，别把它记成"用户主动收起了面板"
        for _o in (self, prev, mw):
            if _o is not None:
                try:
                    _o._nav_moving = True
                except Exception:
                    pass
        try:
            # ① 上一本：面板先收起来；是别的标签的就还回它自己的窗口
            #    （那窗口本就永不显示；主实例那份留在自己这儿，切回来还要用）
            if prev is not None:
                dk0 = getattr(prev, '_nav_dock', None)
                if dk0 is not None:
                    try:
                        if prev is not self and dk0.parent() is self:
                            self.removeDockWidget(dk0)
                            dk0.setParent(prev)
                            prev.addDockWidget(
                                Qt.DockWidgetArea.RightDockWidgetArea, dk0)
                        dk0.hide()
                    except Exception:
                        pass
            self._nav_dock_from = mw
            if mw is self:
                dk = getattr(self, '_nav_dock', None)
                if dk is not None and dk.parent() is not self:
                    try:
                        dk.setParent(self)
                        self.addDockWidget(
                            Qt.DockWidgetArea.RightDockWidgetArea, dk)
                    except Exception:
                        pass
                if dk is not None:
                    try:
                        dk.setVisible(bool(getattr(self, '_nav_wanted', False)))
                    except Exception:
                        pass
                return
            # ② 当前这本：把它的面板搬进主窗口右侧
            dk = getattr(mw, '_nav_dock', None)
            if dk is None:
                try:
                    mw._build_nav_dock()
                    dk = getattr(mw, '_nav_dock', None)
                except Exception:
                    dk = None
            if dk is None:
                return
            try:
                if dk.parent() is mw:
                    mw.removeDockWidget(dk)
                dk.setParent(self)
                self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dk)
                dk.setVisible(bool(getattr(mw, '_nav_wanted', False)))
            except Exception:
                pass
        finally:
            for _o in (self, prev, mw):
                if _o is not None:
                    try:
                        _o._nav_moving = False
                    except Exception:
                        pass

    def _nav_vis_changed(self, vis):
        """记住"用户想不想看导航面板"。切标签搬面板时的显隐不算数。"""
        try:
            if getattr(self, '_nav_moving', False):
                return
            self._nav_wanted = bool(vis)
        except Exception:
            pass

    def _tab_find(self, path):
        """这本书是不是已经开在某个标签里了。"""
        key = os.path.normcase(os.path.normpath(path))
        for i, it in enumerate(self._tabs):
            if it.get('path') and os.path.normcase(
                    os.path.normpath(it['path'])) == key:
                return i
        return -1

    def _ensure_tabbar(self):
        """第一次要开"第二本"时才把阅读区装进 QTabWidget。

        只有一个标签时不显示标签条——不打扰，也不冒多余的一条。
        主窗口自己那份阅读区原地搬进第 0 页，老功能一行都不用改。
        """
        if self._tabbar is not None:
            return self._tabbar
        from PyQt6.QtWidgets import QTabWidget
        sp = self.reader_panel.parent()
        idx = sp.indexOf(self.reader_panel) if sp is not None else -1
        page0 = QWidget()
        lay0 = QVBoxLayout(page0)
        lay0.setContentsMargins(0, 0, 0, 0)
        lay0.addWidget(self.reader_panel)
        tb = QTabWidget()
        tb.setDocumentMode(True)
        tb.setTabsClosable(True)
        tb.setMovable(True)
        tb.setUsesScrollButtons(True)
        tb.tabCloseRequested.connect(self._tab_close_at)
        tb.currentChanged.connect(self._tab_switched)
        tb.addTab(page0, self._tab_title(getattr(self, '_pd_path', '')
                                         or getattr(self, '_text_path', '')))
        if sp is not None and idx >= 0:
            sp.insertWidget(idx, tb)
        self._tabs = [{'mw': self, 'page': page0,
                       'path': (getattr(self, '_pd_path', '')
                                or getattr(self, '_text_path', ''))}]
        self._tabbar = tb
        # v0.3.21：主实例那块导航面板此刻正挂在自己窗口上 —— 记下来，
        # 切到别的标签时才知道该收起哪一块。
        self._nav_dock_from = self
        return tb

    def _tab_new(self, path):
        """新建一个"只为这个标签而生"的实例，把它的阅读区搬进来。"""
        self._ensure_tabbar()
        ok, why = self._tab_room_ok(path)
        if not ok:
            self._say(why, hold=8)
            return None
        # parent=self：把它的身家性命交给 Qt 的父子链管。多实例共存时，
        # 销毁顺序错一步就是"退出即崩"（实测过：不挂 parent，关掉程序那一刻
        # 直接 native crash），挂上之后由 Qt 负责先子后父。
        try:
            mw = MainWindow(C.load_settings(), tab_child=True, parent=self)
        except Exception as e:
            self._say('打不开新的标签：%s' % e, hold=6)
            return None
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(mw.reader_panel)
        i = self._tabbar.addTab(page, '打开中…')
        self._tabs.append({'mw': mw, 'page': page, 'path': path})
        self._tabbar.setCurrentIndex(i)
        return mw

    def _tab_close_at(self, i):
        """关掉一个标签：只有超过一个时才允许关，且要放掉它抱着的 PDF。"""
        if self._tabbar is None or i < 0 or i >= len(self._tabs):
            return
        if self._tabbar.count() <= 1:
            return
        it = self._tabs[i]
        mw = it.get('mw')
        if mw is not None and mw is not self:
            # v0.3.21：它那块导航面板此刻可能正挂在主窗口右侧 —— 先摘下来，
            # 否则实例销毁后（deleteLater）面板跟着一起没，右栏就空了
            if getattr(self, '_nav_dock_from', None) is mw:
                _dk = getattr(mw, '_nav_dock', None)
                if _dk is not None and _dk.parent() is self:
                    try:
                        self.removeDockWidget(_dk)
                    except Exception:
                        pass
                self._nav_dock_from = None
            try:
                fn = getattr(mw, '_release_pdf_doc', None)
                if fn:
                    fn()
            except Exception:
                pass
            try:
                mw.close()
                mw.deleteLater()
            except Exception:
                pass
        self._tabbar.removeTab(i)
        self._tabs.pop(i)

    def _tab_switched(self, i):
        if not self._tabs or i < 0 or i >= len(self._tabs):
            return
        it = self._tabs[i]
        # v0.3.21：右栏导航面板跟着当前标签换（目录 / 缩略图 / 查找都归它）
        try:
            self._nav_handoff(it.get('mw'))
        except Exception:
            pass
        p = it.get('path') or ''
        if p:
            self.setWindowTitle('%s — %s' % (APP_TITLE, os.path.basename(p)))
        else:
            self.setWindowTitle(APP_TITLE)

    def _tab_open(self, mw, path, page=None, hits='', words=()):
        """在指定的那个实例（主窗口自己 or 某个标签）里把书打开。"""
        try:
            fn = getattr(mw, '_open_path', None)
            if fn:
                fn(path, start_page=page)
            if hits and os.path.isfile(hits):
                getattr(mw, '_hit_apply', lambda *a: None)(hits)
            if words:
                getattr(mw, '_words_apply', lambda *a: None)(list(words))
        except Exception as e:
            self._say('打开失败：%s' % e, hold=6)
            return False
        for it in self._tabs:
            if it.get('mw') is mw:
                it['path'] = path
        if self._tabbar is not None:
            for i, it in enumerate(self._tabs):
                if it.get('mw') is mw:
                    self._tabbar.setTabText(i, self._tab_title(path))
                    self._tabbar.setTabToolTip(i, path)
                    self._tabbar.setCurrentIndex(i)
                    break
        self._tab_switched(self._tabbar.currentIndex()
                           if self._tabbar is not None else -1)
        return True

    def open_in_tab(self, path, page=None, hits='', words=(), force_new=False,
                    prefer_tab=False):
        """在一个标签里打开这本书。

        - 还没标签条、也没在看书（或没要求"最好给个标签"）：就地打开，
          跟以前一模一样，界面不会平白多出一条；
        - prefer_tab（外部唤起时的口径）：正在读的那本就地升为第 0 个标签，
          新来的开在第 1 个——于是从 CathaySearch 连着双击几本书就是几张标签，
          而不是几个窗口；
        - 同一本已经开着：切过去，不重复占位置。
        """
        path = os.path.normpath(path)
        if not os.path.isfile(path):
            return False
        if not force_new and self._tabbar is None:
            busy = bool(getattr(self, '_pd_path', '')
                        or getattr(self, '_text_path', ''))
            if not (prefer_tab and busy):
                return self._tab_open(self, path, page, hits, words)
        self._ensure_tabbar()
        if not force_new:
            i = self._tab_find(path)
            if i >= 0:
                self._tabbar.setCurrentIndex(i)
                return self._tab_open(self._tabs[i]['mw'], path, page,
                                      hits, words)
        mw = self._tab_new(path)
        if mw is None:
            return False
        return self._tab_open(mw, path, page, hits, words)

    # -------------------------------------------- 单实例：接住后来者（v0.3.1）
    def _bring_front(self):
        """把窗口顶到前面（外部唤起/重复启动时，别让用户以为没反应）。"""
        try:
            self.setWindowState((self.windowState()
                                 & ~Qt.WindowState.WindowMinimized)
                                | Qt.WindowState.WindowActive)
            self.show()
            self.raise_()
            self.activateWindow()
        except Exception:
            pass

    def _ipc_setup(self):
        """当"先到的那个实例"：定时到信箱里看看有没有后来者塞进来的活。"""
        d = _ipc_dir()
        if not d:
            return
        self._ipc_dir = d
        self._ipc_seen = set()    # 处理过的信（认领后再重复扫到也不重开）
        try:
            from PyQt6.QtCore import QTimer as _T
            self._ipc_t = _T(self)
            self._ipc_t.setInterval(400)
            self._ipc_t.timeout.connect(self._ipc_poll)
            self._ipc_t.start()
            _T.singleShot(200, self._ipc_poll)     # 启动时先把存量清一遍
        except Exception:
            pass

    def _ipc_poll(self):
        """取信：[有请求]→ 立刻删文件（等于告诉对方"我接了"）→ 再打开书。"""
        d = getattr(self, '_ipc_dir', '')
        if not d:
            return
        try:
            names = sorted(n for n in os.listdir(d)
                           if n.startswith('cmd_') and n.endswith('.json'))
        except Exception:
            return
        for n in names:
            if n in getattr(self, '_ipc_seen', set()):
                continue
            p = os.path.join(d, n)
            try:
                if time.time() - os.path.getmtime(p) > 600:
                    self._ipc_claim(p)              # 陈货，丢了处理
                    continue
                with open(p, encoding='utf-8') as f:
                    argv = json.load(f)
            except Exception:
                self._ipc_claim(p)
                continue
            try:
                self._ipc_seen.add(n)
            except Exception:
                pass
            self._ipc_claim(p)                      # 先认领，再干活
            if isinstance(argv, list):
                self._ipc_handle(argv)

    def _ipc_claim(self, p):
        """把这封信标记为已取走：删得掉就删，删不掉就改名 .done。

        本机正常时直接删掉；万一目录只读或删除被拦（沙箱环境就遇上了），
        改名也能让投递方知道"有人接了"，不至于两边僵着重复开工。
        """
        try:
            os.remove(p)
            return
        except Exception:
            pass
        try:
            os.replace(p, p + '.done')
        except Exception:
            pass

    def _ipc_handle(self, argv):
        """处理后来者带来的命令行：按"一本一个标签"的口径打开，再顶到前台。"""
        files, page, hits, words = self._cli_params(argv)
        if not files:
            self._bring_front()
            return
        for i, f in enumerate(files):
            # 页码 / 命中词清单只属于第一本（Search 一次只唤一本）
            self.open_in_tab(f, page=(page if i == 0 else None),
                             hits=(hits if i == 0 else ''),
                             words=(words if i == 0 else ()),
                             prefer_tab=True)
        self._bring_front()

    def open_cli_arg(self):
        """双击关联 / 命令行带文件启动：一律先在阅读区内部打开（不甩给外部程序）。

        【v0.3.1】以前多个文件只开第一个、其余提示"已忽略"；现在第 2 个起
        各自开一个标签，一次拖进来好几本也能同屏对照着看。
        """
        files, page, hits, words = self._cli_params()
        # v0.3.1：三重保险 —— 子实例不碰命令行；同一次别被重入再跑一遍；
        # 命令行里写错的路径不该被当成书。
        if getattr(self, '_tab_child', False):
            return
        if getattr(self, '_cli_arg_busy', False):
            return
        self._cli_arg_busy = True
        try:
            files = [f for f in files if os.path.isfile(f)]
            if not files:
                self._set_mode('library')   # 没带文件打开：书库浏览模式
            self._open_cli_files(files, page, hits, words)
        finally:
            self._cli_arg_busy = False

    def _open_cli_files(self, files, page, hits, words):
        """真正执行"命令行交给我的这几本"。"""
        if not files:
            return
        if len(files) == 1:
            # 单文件照老路走：顺带把左栏列表跟到它所在的文件夹
            self.open_path_in_reader(files[0], '', page=page)
            try:
                if hits and os.path.isfile(hits):
                    self._hit_apply(hits)
                if words:
                    self._words_apply(words)
            except Exception:
                pass
            return
        # 多文件：第一本就地打开，其余各占一个标签
        self._tab_open(self, files[0], page, hits, words)
        for extra in files[1:]:
            self.open_in_tab(extra, force_new=True)
        self._say('一次来了 %d 本：第一本就地打开，后 %d 本各占一个标签'
                  % (len(files), len(files) - 1), hold=4)

    def epub_here(self):
        """用 PDF 引擎直接打开当前项（支持 EPUB；也可以打开 PDF）。Ctrl+E"""
        r = self._cur()
        if not r:
            return
        p = os.path.join(r.get('dir') or '', r.get('name') or '')
        if not os.path.isfile(p):
            self.statusBar().showMessage('文件不在了：%s' % p)
            return
        try:
            d = self._open_fitz(p)
            if d.page_count <= 0:
                try:
                    d.close()            # v0.3.16：别把空文档留在手上
                except Exception:
                    pass
                self.statusBar().showMessage('这个文件没有可显示的页面')
                return
            self._set_pdf(d, p)
            self.pgno = 0
            self._pdf_show()
            self._after_pdf_open()
            self._hist_add()
            self._stat_open(p)
            self.statusBar().showMessage('已用 PDF 引擎打开（%s，共 %d 页）｜连续滚动，PgUp/PgDn 翻页'
                                         % (os.path.splitext(p)[1].lower() or 'file',
                                            d.page_count))
        except Exception as e:
            self.statusBar().showMessage('打不开：%s' % e)

    def hist_here(self):
        """最近打开（Ctrl+H；双击重开）。"""
        from PyQt6.QtWidgets import QListWidget
        st = C.load_settings()
        hs = list(st.get('history') or [])
        dlg = QDialog(self)
        dlg.setWindowTitle('最近打开（%d 条）' % len(hs))
        dlg.resize(640, 460)
        vb = QVBoxLayout(dlg)
        lst = QListWidget()
        for h in hs[:200]:
            lst.addItem('%s   %s' % (h.get('at', ''), h.get('path', '')))
        vb.addWidget(lst)

        def go():
            i = lst.currentRow()
            if 0 <= i < len(hs):
                p = hs[i].get('path', '')
                p = self._real(p)        # 书库换过位置时，先换算成现在的路径
                if os.path.isfile(p):
                    try:
                        self.ed_kw.setText(os.path.splitext(os.path.basename(p))[0])
                        self.do_search()
                        if self.rows:
                            self.tb.setCurrentCell(0, 0)
                            self.on_pick()
                            self.preview_here()
                    except Exception as e:
                        self.statusBar().showMessage('重开失败：%s' % e)
                else:
                    self.statusBar().showMessage('文件不在了：%s' % p)
            dlg.accept()

        lst.itemDoubleClicked.connect(lambda _: go())
        vb.addWidget(QPushButton('好'))
        self._show_util_dialog(dlg, '_dlg_hist_here')

    def _hist_add(self):
        """把当前项追加进阅读历史（不重复，最多 300 条）。"""
        r = self._cur()
        import time
        if not r:
            return
        p = os.path.join(r.get('dir') or '', r.get('name') or '')
        try:
            st = C.load_settings()
            hs = [h for h in (st.get('history') or []) if h.get('path') != p]
            hs.insert(0, {'path': p, 'at': time.strftime('%m-%d %H:%M')})
            st['history'] = hs[:300]
            C.save_settings(st)
        except Exception:
            pass

    # ============================================================ batch5
    # ① PDF 连续纵向滚动（懒加载 + 预渲染 + QPixmap 缓存）＋ O(log n) 二分查页
    def _pdf_stop(self):
        """关掉 PDF 阅读态：清空 PdfView，复位查找/文本缓存。"""
        try:
            self.pdf_view.clear()
        except Exception:
            pass
        self._pd_texts = None
        self._pd_texts_doc = None
        self._hl_kw = ''
        self._hl_multi = []          # batch30：换书/关书时多词高亮也要清

    def _zoom_factor(self):
        """当前缩放系数：适应宽度 / 适应页面 / 100% / 自定义。"""
        d = getattr(self, 'pd', None)
        mode = getattr(self, '_zoom_mode', 'fitp')
        if mode == 'custom':
            return max(0.2, min(5.0, float(getattr(self, '_zoom', 1.0))))
        if mode == '100' or d is None:
            return 1.0
        try:                                  # batch10：适应宽度/页面交给 PdfView 按视口算
            if getattr(self.pdf_view, 'doc', None) is not None:
                return float(self.pdf_view.fit_zoom(mode))
        except Exception:
            pass
        try:
            r = d[int(getattr(self, 'pgno', 0))].rect
            pw, ph = max(1.0, float(r.width)), max(1.0, float(r.height))
        except Exception:
            pw, ph = 612.0, 792.0
        try:
            vp = self.pdf_view.viewport().size()
            vw, vh = max(60, vp.width() - 26), max(60, vp.height() - 26)
        except Exception:
            vw, vh = 800, 1000
        if mode == 'fitp':
            return max(0.2, min(5.0, min(vw / pw, vh / ph)))
        return max(0.2, min(5.0, vw / pw))        # fitw

    def _pdf_show(self, pr=None):
        """把当前文档交给 PdfView 显示（首次装入 / 换文件 / 重建）；跳页请用 pdf_goto。

        pr: 后台线程已经算好的逐页尺寸。给了就别再在主线程里扫一遍（v0.3.3）。
        """
        d = getattr(self, 'pd', None)
        if d is None:
            return
        self._expand_reader()                 # batch22：打开书 → 把阅读区放回来
        self.stack.setCurrentWidget(self.pdf_view)
        self._set_reader('pdf')
        self._text_path = ''
        inv = bool(self.cb_invert.isChecked()) if hasattr(self, 'cb_invert') else False
        _m = getattr(self, '_zoom_mode', 'fitp')      # batch10：适应模式
        _fit = _m if _m in ('fitw', 'fitp') else None
        # fit 交给 set_document 在建控件之前一次算好（原来这里是建完全部页后再
        # set_fit → set_zoom 又全量重排一遍并清空缓存重渲）
        # 【v0.3.17】高亮要跟着"多词模式"走：只传 _hl_kw 的话，「所有关键词」
        # 模式下它是空串，重建视图时高亮就没了（见 set_document 里那段注释）。
        _hl_multi = [str(x) for x in (getattr(self, '_hl_multi', None) or []) if x]
        _hl_arg = _hl_multi if _hl_multi else (getattr(self, '_hl_kw', '') or '')
        self.pdf_view.set_document(d, int(getattr(self, 'pgno', 0)),
                                   self._zoom_factor(), invert=inv,
                                   hl=_hl_arg, fit=_fit,
                                   pr=pr)
        if _fit is None:
            self.pdf_view.set_fit(None)
        self._sync_zoom_ui()
        self._sync_page_box()
        self._upd_status_pdf()
        try:
            self.pdf_view.setFocus()
        except Exception:
            pass
        # batch23：打开新书后，让独立阅读窗口按页面宽度收放（A4 竖版不留大片空白）
        try:
            QTimer.singleShot(260, lambda: self._autosize_reader_win(True))
            QTimer.singleShot(900, lambda: self._autosize_reader_win(True))
        except Exception:
            pass

    def _on_pdf_page(self, i):
        """PdfView 滚动/跳页 → 同步页码框、目录高亮、状态栏。"""
        d = getattr(self, 'pd', None)
        if d is None:
            return
        self.pgno = max(0, min(int(d.page_count) - 1, int(i)))
        self._sync_page_box()
        self._sync_toc_highlight()
        self._upd_status_pdf()

    def _on_fit_zoom(self, _z):
        """batch10：适应窗口自动重排后，同步缩放 UI 与状态栏。"""
        try:
            self._sync_zoom_ui()
            self._upd_status_pdf()
        except Exception:
            pass

    def _pdf_zoom_step(self, factor):
        """Ctrl+滚轮 / 右键菜单 → 自定义缩放步进。"""
        if getattr(self, 'pd', None) is None:
            return
        cur = (float(self._zoom) if self._zoom_mode == 'custom' else self._zoom_factor())
        self._zoom = max(0.25, min(4.0, cur * float(factor)))
        self._zoom_mode = 'custom'
        if getattr(self, 'cb_zoom', None) is not None:
            self.cb_zoom.blockSignals(True)
            self.cb_zoom.setCurrentIndex(3)
            self.cb_zoom.blockSignals(False)
        self._pdf_view_active().set_zoom(self._zoom)
        self._sync_zoom_ui()
        self._upd_status_pdf()

    def _on_pdf_selection(self, t):
        if t:
            self.statusBar().showMessage(
                '已选中 %d 字（已复制到剪贴板；Ctrl+C 可再复制）' % len(t))

    def _sync_page_box(self):
        """页码框与滚动实时双向同步：滚动 → 框里数字跟着变（非法输入会被纠正回当前页）。"""
        try:
            self.ed_page.setText(str(int(self.pgno) + 1))
        except Exception:
            pass

    def _sync_toc_highlight(self):
        """目录页签自动高亮当前章节（O(log n)，仅面板可见时做）。"""
        try:
            dk = getattr(self, '_nav_dock', None)
            lt = getattr(self, '_last_toc', None)
            if dk is None or not dk.isVisible() or not lt or self._nav_toc is None:
                return
            pages = lt.get('row_pages')
            if not pages:
                return
            k = bisect_right(pages, int(self.pgno)) - 1
            if k < 0:
                k = 0
            k = max(0, min(int(self._nav_toc.count()) - 1, int(k)))
            if self._nav_toc.currentRow() != k:
                self._nav_toc.setCurrentRow(k)
                it = self._nav_toc.item(k)
                if it is not None:
                    self._nav_toc.scrollToItem(it)
        except Exception:
            pass

    def _upd_status_pdf(self):
        d = getattr(self, 'pd', None)
        if d is None:
            return
        try:
            self.lb_pg_total.setText('/ %d' % int(d.page_count))
        except Exception:
            pass
        self._sync_page_box()
        try:
            if time.time() < float(getattr(self, '_msg_hold_until', 0.0) or 0.0):
                return                      # 显式提示保护期内，不用滚动状态覆盖它
        except Exception:
            pass
        self.statusBar().showMessage(
            '第 %d / %d 页（滚轮/↑↓ 平滑滚动，PgUp/PgDn 翻页；缩放 %d%%）'
            % (int(self.pgno) + 1, int(d.page_count), round(100 * self._zoom_factor())))

    def pdf_goto(self, delta):
        d = getattr(self, 'pd', None)
        if d is None:
            return
        self.pgno = max(0, min(max(1, int(d.page_count)) - 1, int(self.pgno) + int(delta)))
        self._pdf_view_active().goto_page(self.pgno)

    def pdf_home(self):
        """Ctrl+Home：回到首页。"""
        if getattr(self, 'pd', None) is None:
            return
        self.pgno = 0
        self._pdf_view_active().goto_page(0)

    def pdf_end(self):
        """Ctrl+End：跳到末页。"""
        d = getattr(self, 'pd', None)
        if d is None:
            return
        self.pgno = max(0, int(d.page_count) - 1)
        self._pdf_view_active().goto_page(self.pgno)

    def page_jump(self):
        """页码框（N / M）回车跳页。"""
        d = getattr(self, 'pd', None)
        if d is None:
            return
        try:
            n = int(re.sub(r'\D', '', self.ed_page.text()) or (int(self.pgno) + 1))
        except Exception:
            n = int(self.pgno) + 1
        self.pgno = max(0, min(int(d.page_count) - 1, n - 1))
        self._pdf_view_active().goto_page(self.pgno)
        self._upd_status_pdf()

    def _zoom_changed(self, i):
        """缩放三档 + 自定义（适应宽度 / 适应页面 / 100% / 自定义百分比）。"""
        i = max(0, min(3, int(i)))
        self._zoom_mode = ['fitw', 'fitp', '100', 'custom'][i]
        if i == 3:
            try:
                self._zoom = max(0.5, min(4.0, float(self.sp_zoom.value()) / 100.0))
            except Exception:
                self._zoom = 1.0
        if getattr(self, 'pd', None) is not None:
            if self._zoom_mode in ('fitw', 'fitp'):
                self._pdf_view_active().set_fit(self._zoom_mode)
            else:
                self._pdf_view_active().set_fit(None)
                self._pdf_view_active().set_zoom(self._zoom_factor())
            self.statusBar().showMessage('缩放：%d%%（%s）'
                                         % (round(100 * self._zoom_factor()),
                                            self._zoom_label()))
        self._sync_zoom_ui()

    def _zoom_pct_changed(self, v):
        """百分比框（50–400）回车/失焦 → 切到自定义缩放并重建。"""
        try:
            self._zoom = max(0.5, min(4.0, float(v) / 100.0))
        except Exception:
            return
        self._zoom_mode = 'custom'
        if getattr(self, 'pd', None) is not None:
            self._pdf_view_active().set_fit(None)
            self._pdf_view_active().set_zoom(self._zoom)
            self.statusBar().showMessage('缩放：%d%%（自定义）' % int(round(100 * self._zoom)))
        self._sync_zoom_ui()

    def _zoom_label(self):
        return {'fitw': '适应宽度', 'fitp': '适应页面', '100': '100%'}.get(
            getattr(self, '_zoom_mode', 'fitp'), '自定义')

    def _sync_zoom_ui(self):
        """把当前有效缩放回填到下拉框 / 百分比框（阻断信号，避免递归）。"""
        try:
            pct = int(round(100 * self._zoom_factor()))
            self.sp_zoom.blockSignals(True)
            self.sp_zoom.setValue(max(50, min(400, pct)))
            self.sp_zoom.blockSignals(False)
        except Exception:
            pass
        try:
            idx = {'fitw': 0, 'fitp': 1, '100': 2, 'custom': 3}.get(
                getattr(self, '_zoom_mode', 'fitp'), 1)
            self.cb_zoom.blockSignals(True)
            self.cb_zoom.setCurrentIndex(idx)
            self.cb_zoom.blockSignals(False)
        except Exception:
            pass
        # 【v0.3.9】百分比框只在「自定义…」时露面（平时省下它的位置给 PDF）
        try:
            self.sp_zoom.setVisible(int(self.cb_zoom.currentIndex()) == 3)
        except Exception:
            pass
        # batch23：缩放一改，页面宽窄就变了 → 阅读器窗口跟着放宽（只加不减）
        try:
            QTimer.singleShot(240, self._autosize_reader_win)
        except Exception:
            pass

    def _say(self, msg, hold=0.0):
        """状态栏提示；hold>0 时在保护期内不被滚动状态覆盖。"""
        try:
            self.statusBar().showMessage(msg)
            if hold:
                self._msg_hold_until = time.time() + float(hold)
        except Exception:
            pass

    # ---- batch23：状态栏提示统一发到「阅读器窗口」
    def _mirror_status(self, msg):
        """主窗口状态栏收到消息 → 转给阅读器窗口显示，自己这边清掉。

        阅读器独立成窗口之后，用户眼睛在阅读器上；「复制引用」这类提示
        写到文件列表窗口底部根本看不见。阅读器没独立（单窗口模式）时不动。
        """
        try:
            if not msg or getattr(self, '_mirroring', False):
                return
            w = getattr(self, '_reader_win', None)
            if w is None or not w.isVisible():
                return
            self._mirroring = True
            try:
                w.statusBar().showMessage(msg)
                self.statusBar().clearMessage()
            finally:
                self._mirroring = False
        except Exception:
            pass

    # ---- batch23：按「当前在读什么」决定几个控件露不露面
    def _set_reader(self, v):
        """设置当前阅读内容类型（pdf / text / dual / 空），并同步控件显隐。"""
        self._reader = v
        try:
            self._sync_tool_visibility()
        except Exception:
            pass

    def _sync_tool_visibility(self):
        """字号 / 行距 / 在阅读区打开 / MD 渲染 / 自定义百分比 —— 按内容显隐。

        · 字号、行距 —— 只对文本有意义，读 PDF 时整组收起来（v0.3.9：字号以前
          天天摆在那儿，可它对 PDF 一点用也没有）；
        · 「在阅读区打开」 —— 已经在阅读区看它了，再点一次是自相矛盾的；
        · 「MD 渲染」 —— 只有 .md、或明显是 Markdown 写法的 TXT 才给；
        · 缩放百分比 —— 只有选了「自定义…」才露出来（v0.3.9）。
        """
        rd = getattr(self, '_reader', '')
        _txt = rd in ('text', 'dual')
        for w in (getattr(self, 'lb_line', None), getattr(self, 'sp_line', None),
                  getattr(self, 'lb_font', None), getattr(self, 'sp_font', None)):
            try:
                if w is not None:
                    w.setVisible(_txt)
            except Exception:
                pass
        try:
            if getattr(self, 'sp_zoom', None) is not None:
                self.sp_zoom.setVisible(
                    int(getattr(self, 'cb_zoom', None).currentIndex()) == 3)
        except Exception:
            pass
        try:
            if getattr(self, 'b_preview', None) is not None:
                self.b_preview.setVisible(not rd)
        except Exception:
            pass
        try:
            if getattr(self, 'b_md', None) is not None:
                self.b_md.setVisible(self._looks_like_md())
        except Exception:
            pass

    def _looks_like_md(self):
        """当前在读的算不算 Markdown（.md 文件，或 TXT 里明显是 Markdown）。"""
        rd = getattr(self, '_reader', '')
        p = ''
        try:
            if rd in ('text', 'dual'):
                p = (getattr(self, '_text_path', '') or getattr(self, '_md_path', '')
                     or getattr(self, '_pd_path', ''))
        except Exception:
            p = ''
        if not p:
            return False
        low = p.lower()
        if low.endswith(('.md', '.markdown')):
            return True
        if not low.endswith(('.txt', '.text')):
            return False
        try:                       # TXT 抽头一段看特征
            if getattr(self, '_md_path', '') == p and getattr(self, '_md_src', ''):
                s = self._md_src
            else:
                s = TOOLS._read_text(p, 200000)
        except Exception:
            return False
        return self._md_like_score(s)

    @staticmethod
    def _md_like_score(s):
        """粗判像不像 Markdown；要凑够 2 个特征才认，免得把普通 TXT 误判。"""
        s = s or ''
        if not s.strip():
            return False
        n = 0
        if re.search(r'(?m)^\s{0,3}#{1,6}\s+\S', s):
            n += 1
        if re.search(r'(?m)^\s{0,3}[-*+]\s+\S', s):
            n += 1
        if re.search(r'(?m)^\s{0,3}\d+\.\s+\S', s):
            n += 1
        if '**' in s or '__' in s:
            n += 1
        if re.search(r'\[[^\]]+\]\([^)]+\)', s):
            n += 1
        if '```' in s:
            n += 1
        if re.search(r'(?m)^\s{0,3}>\s+\S', s):
            n += 1
        return n >= 2

    def eventFilter(self, obj, ev):
        # PDF 的 Ctrl+滚轮缩放已由 PdfView 自己处理（zoomRequested 信号）
        return super().eventFilter(obj, ev)

    def _on_invert(self, *_):
        """勾选/取消 ◐ PDF 反色 时清缓存并立即重画。"""
        if getattr(self, 'pd', None) is not None:
            self._pdf_view_active().set_invert(bool(self.cb_invert.isChecked()))

    def _on_theme_index(self, i):
        """主题下拉换项。

        前三项是主题；第 4 项是「◐ PDF 反色」开关 —— 点它不该把主题也换掉，
        所以恢复原来的主题下标，只翻转反色勾选。这样反色跟任何主题都能并存。
        """
        try:
            if int(i) == 3:
                self.cb_theme.blockSignals(True)
                self.cb_theme.setCurrentIndex(int(getattr(self, '_theme_idx', 0)))
                self.cb_theme.blockSignals(False)
                self._toggle_invert_from_menu()
                return
            self._theme_idx = int(i)
            self.apply_theme()
        except Exception:
            pass

    def _toggle_invert_from_menu(self):
        """从主题下拉里勾/取消「PDF 反色」。"""
        try:
            m = self.cb_theme.model()
            it = m.item(3)
            new = (it.checkState() != Qt.CheckState.Checked)
            it.setCheckState(Qt.CheckState.Checked if new else Qt.CheckState.Unchecked)
            if getattr(self, 'cb_invert', None) is not None:
                self.cb_invert.blockSignals(True)
                self.cb_invert.setChecked(bool(new))
                self.cb_invert.blockSignals(False)
            self._on_invert()
            self.statusBar().showMessage(
                'PDF 反色：%s（主题仍是「%s」）'
                % ('开' if new else '关',
                   ['浅色', '深色', '护眼'][max(0, min(2, int(getattr(self, '_theme_idx', 0))))]))
        except Exception:
            pass

    def apply_theme(self):
        """主题（浅色/深色/护眼）+ 正文字号 + 行距；F11 全屏。"""
        i = max(0, min(2, int(getattr(self, '_theme_idx', 0))))
        bg, fg = [('#ffffff', '#222222'), ('#1e1e1e', '#d8d8d8'),
                  ('#f4ecd8', '#3a3226')][i]
        try:
            self.view.setStyleSheet('QTextEdit{background:%s;color:%s;}' % (bg, fg))
            try:
                self.pdf_view.setStyleSheet('QScrollArea{background:%s;}' % bg)
                self.pdf_view.viewport().setStyleSheet('background:%s;' % bg)
            except Exception:
                pass
            sz = self.sp_font.value() if hasattr(self, 'sp_font') else 13
            self.view.setFont(QFont('Microsoft YaHei', sz))
            lh = self.sp_line.value() if hasattr(self, 'sp_line') else 150
            self._apply_line_height(lh)
            self.statusBar().showMessage(
                '主题：%s%s（字号 %d，行距 %d%%，F11 全屏）'
                % (['浅色', '深色', '护眼'][i],
                   '＋PDF 反色' if (getattr(self, 'cb_invert', None) is not None
                                    and self.cb_invert.isChecked()) else '',
                   sz, lh))
        except Exception:
            pass

    def _apply_line_height(self, lh=None):
        """把行距（百分比）作用到阅读区：整篇按比例块格式。"""
        try:
            if lh is None:
                lh = self.sp_line.value() if hasattr(self, 'sp_line') else 150
            from PyQt6.QtGui import QTextBlockFormat, QTextCursor
            vs = self.view.verticalScrollBar().value()
            cur = self.view.textCursor()
            cur.select(QTextCursor.SelectionType.Document)
            bf = QTextBlockFormat()
            bf.setLineHeight(float(lh), 1)      # 1 = ProportionalHeight（比例）
            cur.mergeBlockFormat(bf)
            cur.setPosition(0)                  # 收掉选区，避免高亮
            self.view.setTextCursor(cur)
            self.view.verticalScrollBar().setValue(vs)
        except Exception:
            pass

    def toggle_full(self):
        """F11：全屏时自动隐藏工具栏/按钮行（退出时恢复）。"""
        try:
            if self.isFullScreen():
                self.showNormal()
                for w, vis in getattr(self, '_full_prev', []):
                    try:
                        w.setVisible(vis)
                    except Exception:
                        pass
                self.statusBar().showMessage('已退出全屏')
            else:
                widgets = list(getattr(self, '_top_widgets', [])) + \
                    list(getattr(self, '_btn_widgets', []))
                self._full_prev = [(w, w.isVisible()) for w in widgets]
                for w in widgets:
                    w.setVisible(False)
                self.showFullScreen()
                self.statusBar().showMessage('按 F11 退出全屏')
        except Exception as e:
            self.statusBar().showMessage('全屏切换失败：%s' % e)

    # ---- batch7：阅读器独立窗口
    def _detach_reader(self):
        if getattr(self, '_reader_win', None) is not None:
            return
        try:
            _csz = self._split.sizes()
            self._split_sizes = ([340, 1160] if (len(_csz) == 2 and int(_csz[1]) <= 0)
                                 else _csz)
        except Exception:
            self._split_sizes = [340, 1160]
        try:
            self.reader_panel.show()      # 启动时可能是收起的，拆出去要显出来
            self._reader_collapsed = False
        except Exception:
            pass
        win = ReaderWindow(self)
        self.reader_panel.setParent(win)
        win.setCentralWidget(self.reader_panel)
        dk = getattr(self, '_nav_dock', None)
        if dk is not None:
            try:
                self.removeDockWidget(dk)
                dk.setParent(win)
                win.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dk)
                dk.show()
            except Exception:
                pass
        self._reader_win = win
        # batch23：不再一上来就最大化 —— A4 竖版页面在宽窗口里两侧全是空白。
        # 按当前内容算个「刚好装得下」的宽度，高度撑满可用高度。
        try:
            scr = QApplication.primaryScreen()
            _ah = int(scr.availableGeometry().height() * 0.94) if scr else 900
            _aw = self._reader_ideal_width() or 900
            win.resize(max(760, min(int(_aw), 1600)), max(600, _ah))
        except Exception:
            win.resize(1000, 900)
        win.show()
        self._bind_reader_shortcuts(win)      # batch18：独立窗口也要能用 Ctrl+F 查找
        self.statusBar().showMessage('阅读器已独立成窗口（可最大化 · F11 全屏）')

    def _reader_ideal_width(self):
        """算「刚好装下当前内容」的窗口宽度：页面宽 + 边距（+ 导航面板 / TXT 栏）。"""
        base = 0
        try:
            rd = getattr(self, '_reader', '')
            if rd == 'dual' and getattr(self, 'dual', None) is not None:
                n = self.dual.pdf.page_count()
                if n <= 0:
                    return 0
                i = min(max(0, self.dual.pdf.current_page()), n - 1)
                base = self.dual.pdf._page_size(i, self.dual.pdf._zoom).width() + 520 + 240
            else:
                v = self._pdf_view_active()
                n = v.page_count()
                if n <= 0:
                    return 0
                i = min(max(0, v.current_page()), n - 1)
                base = v._page_size(i, v._zoom).width() + 90      # 边距 + 滚动条
        except Exception:
            return 0
        try:
            dk = getattr(self, '_nav_dock', None)
            if dk is not None and dk.isVisible():
                base += max(240, int(dk.width() or 300))
        except Exception:
            pass
        return int(base)

    def _autosize_reader_win(self, force=False):
        """让独立的阅读器窗口宽度跟着内容走。

        页面窄（A4 竖版）→ 窗口就不必那么宽，两侧不留大片空白；
        进对读 / 放大缩放 / 换宽页面 → 自动加宽。默认**只加不减**免得窗口
        自己缩来缩去；打开新书时 force=True，允许收缩到合适宽度。
        """
        w = getattr(self, '_reader_win', None)
        if w is None:
            return
        try:
            if not w.isVisible():
                return
            want = self._reader_ideal_width()
            if want <= 0:
                return
            scr = None
            try:
                scr = w.screen() or QApplication.primaryScreen()
            except Exception:
                scr = None
            max_w = 1600
            try:
                if scr is not None:
                    max_w = max(900, int(scr.availableGeometry().width() * 0.92))
            except Exception:
                pass
            want = max(760, min(int(want), max_w))
            cur = int(w.width())
            if force:
                if abs(cur - want) < 40:
                    return
            elif want <= cur + 40:
                return
            w.resize(want, int(w.height()))
        except Exception:
            pass

    def _bind_reader_shortcuts(self, win):
        """batch18：给独立阅读窗口挂上窗口级快捷键（Ctrl+F/Esc/F11）——
        主窗口上的 QShortcut 在焦点落到独立窗口时不会触发。"""
        try:
            scs = []

            def _mk(seq, slot):
                sc = QShortcut(QKeySequence(seq), win)
                try:
                    sc.setContext(Qt.ShortcutContext.WindowShortcut)
                except Exception:
                    pass
                sc.activated.connect(slot)
                scs.append(sc)

            _mk('Ctrl+F', self.find_focus)
            _mk('Esc', self.find_close)
            _mk('F11', lambda: self._reader_full(win))
            try:
                win._cv_sc = scs          # 持引用，避免被 GC
            except Exception:
                pass
        except Exception:
            pass

    def _reader_full(self, win):
        """独立阅读窗口的 F11 全屏切换。"""
        try:
            if win.isFullScreen():
                win.showMaximized()
            else:
                win.showFullScreen()
        except Exception:
            pass

    def _attach_reader(self, from_close=False):
        win = getattr(self, '_reader_win', None)
        if win is None:
            return
        self._reader_win = None
        if getattr(self, '_closing', False):      # batch18：主窗口正在关闭 → 只清掉独立窗口
            try:
                win.hide()
                win.deleteLater()
            except Exception:
                pass
            return
        dk = getattr(self, '_nav_dock', None)
        if dk is not None:
            try:
                win.removeDockWidget(dk)
                dk.setParent(self)
                self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dk)
                dk.hide()
            except Exception:
                pass
        try:
            self.reader_panel.setParent(None)
            self._split.addWidget(self.reader_panel)
            self._split.setSizes(getattr(self, '_split_sizes', None) or [340, 1160])
        except Exception:
            pass
        try:
            win.hide()
            win.deleteLater()
        except Exception:
            pass
        self.statusBar().showMessage('阅读器已收回主窗口（🗗 可再独立）')

    def toggle_reader_window(self):
        if getattr(self, '_reader_win', None) is not None:
            self._attach_reader()
        else:
            self._detach_reader()

    def start_two_window_mode(self):
        """默认双窗口：主窗口放文件列表（靠左），阅读器独立成窗口（尽量大，靠右）。"""
        try:
            if getattr(self, '_reader_win', None) is not None:
                return
            self._detach_reader()
            win = getattr(self, '_reader_win', None)
            scr = QApplication.primaryScreen()
            g = scr.availableGeometry() if scr is not None else None
            if win is None or g is None or g.width() <= 0:
                return
            win.showNormal()
            st = C.load_settings()
            rg = st.get('reader_geom')
            if isinstance(rg, (list, tuple)) and len(rg) == 4 and int(rg[2]) > 400:
                win.setGeometry(int(rg[0]), int(rg[1]), int(rg[2]), int(rg[3]))
            else:
                w = max(640, int(g.width() * 0.62))
                h = max(480, int(g.height() * 0.88))
                top = g.top() + int(g.height() * 0.06)
                win.setGeometry(g.right() - w + 1, top, w, h)
                self.resize(min(560, max(400, int(g.width() * 0.25))), h)
                self.move(g.left() + 8, top)                        # 靠左
            self.statusBar().showMessage('双窗口：左侧文件列表 ｜ 右侧阅读器（可最大化 · 🗗 收回）')
        except Exception as e:
            try:
                self.statusBar().showMessage('双窗口模式失败：%s' % e)
            except Exception:
                pass

    # ---- batch7：跨文件全文检索（独立进程）
    _FTS_EXTS = ('.pdf', '.txt', '.md', '.json', '.csv')

    def _expand_family(self, files):
        """batch9：把每本书的 PDF 与对应 TXT（含不同 OCR 引擎 / 繁简变体 / _opt）都纳入检索范围。"""
        out, seen = [], set()

        def add(p):
            k = os.path.normcase(os.path.abspath(p))
            if p and k not in seen and os.path.isfile(p):
                seen.add(k)
                out.append(p)

        for p in files:
            add(p)
            try:
                d = os.path.dirname(os.path.abspath(p))
                key = META.family_key(os.path.basename(p))
                if not key:
                    continue
                for fn in os.listdir(d):
                    if os.path.splitext(fn)[1].lower() in self._FTS_EXTS \
                            and META.family_key(fn) == key:
                        add(os.path.join(d, fn))
            except OSError:
                pass
        return out

    def hub_search(self, kw=None):
        """全库全文检索 —— 唤起 CathayHub Search。

        本文件夹那点范围由 Viewer 自己搞定（上面的「全文搜索本文件夹」）；
        要跨整个书库（FileLocator 索引，秒级出结果）得交给 Search。
        两个程序在同一个 CathayHub 目录里，找同目录的 exe 即可；
        已经开着就直接复用，不会再开第二个。
        """
        exe = C.find_search_exe()
        if not exe:
            self.statusBar().showMessage(
                '没找到 CathayHub Search —— 它应该和本程序放在同一个文件夹里')
            return
        if kw is None:
            from PyQt6.QtWidgets import QInputDialog
            kw, ok = QInputDialog.getText(
                self, '全库全文检索',
                '关键词（交给 CathayHub Search，跨整个书库检索）：',
                text=self._current_kw() or '')
            if not ok or not kw.strip():
                return
            kw = kw.strip()
        try:
            # v0.3.2：光把词填进 Search 的输入框不算"唤起"——用户还得手点一次
            # 检索。带上 --autosearch，让它自己开搜。
            subprocess.Popen([exe, '--word', kw, '--autosearch'])
            self.statusBar().showMessage(
                '已交给 CathayHub Search 全库检索「%s」' % kw)
        except Exception as e:
            self.statusBar().showMessage('唤起 CathayHub Search 失败：%s' % e)

    def _current_kw(self):
        """当前检索框里的词（唤起 Search 时预填上，省得再敲一遍）。"""
        ed = getattr(self, 'ed_search', None) or getattr(self, 'ed_kw', None)
        try:
            return (ed.text() or '').strip() if ed is not None else ''
        except Exception:
            return ''

    def fulltext_search(self):
        """【v0.3.2】本文件夹全文检索已移除，留个提示免得旧入口点了没反应。"""
        self._say('「本文件夹全文检索」已移除 —— 请用「🌐 全库全文检索」'
                  '（Ctrl+Shift+G）交给 CathayHub Search 跨整个书库搜', hold=5)
        return

        if getattr(self, '_fts_proc', None) is not None:
            self.statusBar().showMessage('全文检索进行中…（完成后自动弹出结果）')
            return
        rows = getattr(self, 'rows', []) or []
        files = []
        seen = set()
        for r in rows:
            p = os.path.join(r.get('dir') or '', r.get('name') or '')
            if p and p not in seen and os.path.isfile(p):
                seen.add(p)
                files.append(p)
        files = self._expand_family(files)      # batch9：把对应的 TXT 也纳入检索范围
        if not files:
            self.statusBar().showMessage('当前搜索结果为空：先搜出一些文件再全文检索')
            return
        from PyQt6.QtWidgets import QInputDialog
        kw, ok = QInputDialog.getText(
            self, '全文搜索本文件夹',
            '关键词（在 %d 个文件里全文查找，PDF 取文字层）：' % len(files))
        if not ok or not kw.strip():
            return
        kw = kw.strip()
        import tempfile
        jobdir = tempfile.mkdtemp(prefix='cvfts_')
        job = os.path.join(jobdir, 'job.json')
        out = os.path.join(jobdir, 'out.json')
        try:
            with open(job, 'w', encoding='utf-8') as f:
                json.dump({'kw': kw, 'files': files, 'out': out}, f, ensure_ascii=False)
        except Exception as e:
            self.statusBar().showMessage('无法创建检索任务：%s' % e)
            return
        if getattr(sys, 'frozen', False):
            prog, args = sys.executable, ['--fts-worker', job]
        else:
            prog = sys.executable
            args = [os.path.abspath(__file__), '--fts-worker', job]
        proc = QProcess(self)
        proc.setProgram(prog)
        proc.setArguments(args)
        proc.readyReadStandardOutput.connect(lambda: self._fts_progress(proc))
        proc.finished.connect(lambda code, st: self._fts_done(code, out, jobdir, kw))
        self._fts_proc = proc
        self._fts_total = len(files)
        self._fts_kw = kw
        try:
            proc.start()
        except Exception as e:
            self._fts_proc = None
            self.statusBar().showMessage('启动检索进程失败：%s' % e)
            return
        self.statusBar().showMessage(
            '已在独立进程开始全文检索：%d 个文件，关键词「%s」…' % (len(files), kw))

    def _fts_progress(self, proc):
        try:
            data = bytes(proc.readAllStandardOutput()).decode('utf-8', 'replace')
        except Exception:
            return
        for line in data.splitlines():
            if line.startswith('PROGRESS\t'):
                parts = line.split('\t')
                if len(parts) >= 4:
                    self.statusBar().showMessage(
                        '全文检索中… %s / %s 个文件，已命中 %s 处（关键词 %s）'
                        % (parts[1], parts[2], parts[3], getattr(self, '_fts_kw', '')))

    def _fts_done(self, code, out, jobdir, kw):
        self._fts_proc = None
        try:
            with open(out, 'r', encoding='utf-8') as f:
                res = json.load(f)
        except Exception:
            self.statusBar().showMessage('全文检索失败（进程返回 %s）' % code)
            return
        files = res.get('files') or []
        if not files and res.get('hits'):                 # 兼容旧输出
            agg = {}
            for h in res['hits']:
                p = h.get('path')
                a = agg.setdefault(p, {'path': p,
                                       'name': os.path.basename(p or ''),
                                       'count': 0, 'hits': []})
                a['count'] += 1
                a['hits'].append({'page': h.get('page'), 'ctx': h.get('ctx')})
            files = list(agg.values())
        kept, hidden = META.dedup_split(files)             # batch9 去重（被去重项默认隐藏）
        total = sum(int(f.get('count') or 0) for f in kept)
        self.statusBar().showMessage(
            '全文检索完成：扫描 %s 个文件，命中 %d 处（去重后 %d 个文件%s）'
            % (res.get('scanned'), total, len(kept),
               ('，另有 %d 个被去重隐藏' % len(hidden)) if hidden else ''))
        # batch11：跨文件检索历史自动保存（随时可在「🕘 检索历史」调阅）
        try:
            stt = C.load_settings()
            rec = {'kw': kw, 'at': time.strftime('%Y-%m-%d %H:%M'),
                   'scanned': res.get('scanned'), 'files': len(kept),
                   'hidden': len(hidden), 'total': total,
                   'kept': kept, 'kept_hidden': hidden,
                   'errors': res.get('errors') or []}
            stt['fts_hist'] = TOOLS.fts_hist_add(stt.get('fts_hist') or [], rec)
            C.save_settings(stt)
        except Exception:
            pass
        self._show_fts_dialog(kw, kept, hidden, res.get('errors') or [])

    def _show_fts_dialog(self, kw, files, hidden, errors):
        def _rows(fs, mark_hidden=False):
            out = []
            for f in fs:
                mark = '（TXT）' if f.get('txt_mark') else ''
                if mark_hidden:
                    mark += '（已去重）'
                for h in f.get('hits') or []:
                    out.append((f.get('path'), h.get('page'),
                                '%s%s' % (f.get('name') or '', mark), h.get('ctx') or ''))
            return out

        kept_rows = _rows(files)
        hid_rows = _rows(hidden, mark_hidden=True)
        dlg = QDialog(self)
        dlg.setWindowTitle('全文搜索本文件夹「%s」— %d 个文件 / %d 处'
                           % (kw, len(files), len(kept_rows)))
        dlg.resize(900, 620)
        vb = QVBoxLayout(dlg)
        lst = QListWidget()
        info = QLabel()
        info.setWordWrap(True)
        shown = {'hidden': False}

        def _fill():
            lst.clear()
            rows = kept_rows + (hid_rows if shown['hidden'] else [])
            for path, pg, name, ctx in rows:
                tag = ('第 %d 页' % (int(pg) + 1)) if pg is not None else '—'
                it = QListWidgetItem('%s ｜ %s ｜ %s' % (name, tag, ctx))
                it.setData(Qt.ItemDataRole.UserRole, (path, pg))
                lst.addItem(it)
            info.setText('双击一条结果 → 在阅读区打开并跳到该页。共 %d 个文件 / %d 处。%s'
                         % (len(files), len(kept_rows),
                            ('已展开 %d 个被去重项。' % len(hid_rows)) if shown['hidden']
                            else ('' if not hid_rows else '另有 %d 个被去重项，默认隐藏。'
                                  % len(hid_rows))))

        _fill()
        vb.addWidget(info)
        vb.addWidget(lst, 1)
        if errors:
            lb = QLabel('有 %d 个文件读取失败：%s' % (len(errors), '；'.join(errors[:3])))
            lb.setWordWrap(True)
            vb.addWidget(lb)
        bb = FlowLayout()                    # batch22：窗口窄了自动换行
        b1 = QPushButton('全部复制')
        b3 = QPushButton('▸ 显示被去重的 %d 项' % len(hid_rows))
        b3.setVisible(bool(hid_rows))
        b2 = QPushButton('关闭')
        bb.addWidget(b1)
        bb.addWidget(b3)
        bb.addStretch(1)
        bb.addWidget(b2)
        vb.addLayout(bb)

        def _toggle_hidden():
            shown['hidden'] = not shown['hidden']
            b3.setText(('▾ 收起被去重的 %d 项' if shown['hidden']
                        else '▸ 显示被去重的 %d 项') % len(hid_rows))
            _fill()

        def _copy_all():
            lines = []
            for path, pg, name, ctx in (kept_rows + (hid_rows if shown['hidden'] else [])):
                lines.append('%s%s\n%s' % (
                    name, ('（第 %d 页）' % (int(pg) + 1)) if pg is not None else '', ctx))
            QApplication.clipboard().setText('\n\n'.join(lines))
            self.statusBar().showMessage('已复制全部检索结果')

        def _open(it):
            d = it.data(Qt.ItemDataRole.UserRole)
            if d:
                self._open_fts_hit(d[0], d[1])

        b1.clicked.connect(_copy_all)
        b3.clicked.connect(_toggle_hidden)
        b2.clicked.connect(dlg.accept)
        lst.itemDoubleClicked.connect(_open)
        self._show_util_dialog(dlg, '_dlg_ftsres')

    def _open_fts_hit(self, path, page):
        for i, r in enumerate(getattr(self, 'rows', []) or []):
            p = os.path.join(r.get('dir') or '', r.get('name') or '')
            if os.path.normcase(p) == os.path.normcase(path or ''):
                self.tb.setCurrentCell(i, 0)
                self.on_pick()
                break
        self.preview_here()
        if page is not None and getattr(self, 'pd', None) is not None:
            try:
                self.pgno = max(0, min(int(self.pd.page_count) - 1, int(page)))
                self.pdf_view.goto_page(self.pgno)
            except Exception:
                pass

    # ---- batch11：跨文件全文检索历史（自动保存，Ctrl+Shift+F）
    def fts_history_dialog(self):
        """检索历史：跨文件全文检索 + 文件名检索，两类都列出（分页签，标清楚）。

        batch22：以前点「🕘 检索历史」只看得到跨文件全文检索，实际上
        「文件名检索」（左栏搜关键字）也有历史，现在两类一起给。
        """
        try:
            st = C.load_settings()
            hist = list(st.get('fts_hist') or [])
            khist = list(st.get('search_hist') or [])
        except Exception:
            hist, khist = [], []
        self._last_fts_hist = hist
        self._last_kw_hist = khist
        dlg = QDialog(self)
        dlg.setWindowTitle('检索历史')
        dlg.resize(780, 560)
        vb = QVBoxLayout(dlg)
        vb.addWidget(QLabel('两类检索分开列（双击都能重来一次）：\n'
                            '· 跨文件全文检索 —— 在书的**内容**里搜过什么（Ctrl+Shift+F）\n'
                            '· 文件名检索 —— 在**左栏列表**里搜过什么（Ctrl+K / 搜索框回车）'))
        tabs = QTabWidget()
        _p1 = QWidget()
        v1 = QVBoxLayout(_p1)
        v1.addWidget(QLabel('历次全文检索（最近 %d 次，双击重开该次结果）：' % len(hist)))
        lst = QListWidget()
        for h in hist:
            lst.addItem('%s ｜ 「%s」 ｜ %s 个文件 / %s 处%s'
                        % (h.get('at') or '', h.get('kw') or '',
                           h.get('files') or 0, h.get('total') or 0,
                           ('，另有 %s 个被去重' % h.get('hidden')) if h.get('hidden') else ''))
        v1.addWidget(lst, 1)

        def _open():
            i = lst.currentRow()
            if 0 <= i < len(hist):
                h = hist[i]
                dlg.accept()
                self._show_fts_dialog(h.get('kw') or '', h.get('kept') or [],
                                     h.get('kept_hidden') or [], h.get('errors') or [])

        def _del():
            i = lst.currentRow()
            if not (0 <= i < len(hist)):
                self.statusBar().showMessage('先选中一条历史')
                return
            try:
                stt = C.load_settings()
                hh = list(stt.get('fts_hist') or [])
                del hh[i]
                stt['fts_hist'] = hh
                C.save_settings(stt)
            except Exception as e:
                self.statusBar().showMessage('删除失败：%s' % e)
                return
            lst.takeItem(i)
            hist.pop(i)
            self.statusBar().showMessage('已删除该条检索历史')

        def _clear():
            try:
                stt = C.load_settings()
                stt['fts_hist'] = []
                C.save_settings(stt)
            except Exception:
                pass
            hist[:] = []
            lst.clear()
            self.statusBar().showMessage('已清空检索历史')

        lst.itemDoubleClicked.connect(lambda _: _open())
        row = FlowLayout()                   # batch22：窗口窄了自动换行
        bd = QPushButton('删除选中')
        bd.clicked.connect(_del)
        bc = QPushButton('清空')
        bc.clicked.connect(_clear)
        row.addWidget(bd)
        row.addWidget(bc)
        row.addStretch(1)
        v1.addLayout(row)
        tabs.addTab(_p1, '全文搜索本文件夹（%d）' % len(hist))

        # ---- ② 文件名检索历史（左栏搜索框搜过的关键词）
        _p2 = QWidget()
        v2 = QVBoxLayout(_p2)
        v2.addWidget(QLabel('文件名检索历史（最近 %d 条，双击重搜）：' % len(khist)))
        lst2 = QListWidget()
        for k in khist:
            lst2.addItem(k)
        v2.addWidget(lst2, 1)

        def _open_kw():
            i = lst2.currentRow()
            if 0 <= i < len(khist):
                kw = khist[i]
                dlg.accept()
                self._rerun_search(kw)

        def _del_kw():
            i = lst2.currentRow()
            if not (0 <= i < len(khist)):
                self.statusBar().showMessage('先选中一条文件名检索历史')
                return
            try:
                stt = C.load_settings()
                hh = list(stt.get('search_hist') or [])
                if 0 <= i < len(hh):
                    del hh[i]
                stt['search_hist'] = hh
                C.save_settings(stt)
            except Exception as e:
                self.statusBar().showMessage('删除失败：%s' % e)
                return
            lst2.takeItem(i)
            khist.pop(i)
            self.statusBar().showMessage('已删除该条文件名检索历史')

        def _clear_kw():
            try:
                stt = C.load_settings()
                stt['search_hist'] = []
                C.save_settings(stt)
            except Exception:
                pass
            khist[:] = []
            lst2.clear()
            self.statusBar().showMessage('已清空文件名检索历史')

        lst2.itemDoubleClicked.connect(lambda _: _open_kw())
        row2 = QHBoxLayout()
        bd2 = QPushButton('删除选中')
        bd2.clicked.connect(_del_kw)
        bc2 = QPushButton('清空')
        bc2.clicked.connect(_clear_kw)
        row2.addWidget(bd2)
        row2.addWidget(bc2)
        row2.addStretch(1)
        v2.addLayout(row2)
        tabs.addTab(_p2, '文件名检索（%d）' % len(khist))

        vb.addWidget(tabs, 1)
        bf = QPushButton('关闭')
        bf.clicked.connect(dlg.accept)
        _rowf = QHBoxLayout()
        _rowf.addStretch(1)
        _rowf.addWidget(bf)
        vb.addLayout(_rowf)
        try:
            dlg.setModal(False)          # batch22：不锁前台
        except Exception:
            pass
        _od = getattr(self, '_hist_dlg', None)
        if _od is not None:
            try:
                _od.close()
            except Exception:
                pass
        dlg.finished.connect(lambda *_: setattr(self, '_hist_dlg', None))
        self._hist_dlg = dlg
        dlg.show()

    def on_ver(self, i):
        """版本下拉切换 → 真正打开该版本（PDF 原本 / 繁转简 TXT / 其它）。batch10 修正：以前只显示路径文字。"""
        if not (0 <= i < len(self.vers)):
            return
        p = self.vers[i].get('path') or ''
        if not p or not os.path.isfile(p):
            self.statusBar().showMessage('这个版本的文件不在了：%s' % p)
            return
        self._stat_flush()
        self._suppress_dual = True          # 切版本不强制对读，保持当前阅读方式
        try:
            opened = self._open_path(p)
        finally:
            self._suppress_dual = False
        if opened:
            try:
                self.cb_ver.blockSignals(True)
                self.cb_ver.setCurrentIndex(i)
                self.cb_ver.blockSignals(False)
            except Exception:
                pass
            self.statusBar().showMessage('已切到：%s' % os.path.basename(p))

    def copy_cite(self):
        m = getattr(self, 'meta', None)
        if not m:
            return
        s = META.cite(m, page='X')
        try:                                     # 引文里含纪年就自动换算
            s, _hits = CHRONO.annotate(s)
        except Exception:
            pass
        QApplication.clipboard().setText(s)
        self.statusBar().showMessage('已复制引用：%s' % s)

    def colophon_here(self):
        """跳到当前这本书的版权页（PDF；当前项是 txt 时自动找同书 PDF）。找不到就提示。"""
        r = self._cur()
        if not r:
            self.statusBar().showMessage('先在列表里选一本书')
            return
        p = os.path.join(r.get('dir') or '', r.get('name') or '')
        try:
            pdf = p if p.lower().endswith('.pdf') else ''
            if not pdf:
                cands = META._sibling_pdfs(p) if hasattr(META, '_sibling_pdfs') else []
                pdf = cands[0] if cands else ''
            if not pdf or not os.path.isfile(pdf):
                self.statusBar().showMessage('没找到版权页')
                return
            import fitz
            d = fitz.open(pdf)
            if d.page_count <= 0:
                try:
                    d.close()            # v0.3.16：同上
                except Exception:
                    pass
                self.statusBar().showMessage('没找到版权页')
                return
            # batch11：先查 PDF 目录（标签页/书签）里有没有「版权页 / 版权」
            try:
                toc = d.get_toc() or []
            except Exception:
                toc = []
            page = TOOLS.find_colophon_in_toc(toc)
            src = '目录' if page else ''
            if not page:
                page = META.find_colophon_page(pdf)        # 1 起；0 = 没找到
                src = '文字层' if page else ''
            if not page:
                try:
                    d.close()
                except Exception:
                    pass
                self.statusBar().showMessage('没找到版权页')
                return
            self._set_pdf(d, pdf)
            self.pgno = max(0, min(d.page_count - 1, page - 1))
            self._pdf_show()
            self._hist_add()
            self._stat_open(pdf)
            self._say('版权页在第 %d / %d 页（来源：%s）'
                      % (self.pgno + 1, d.page_count, src or '文字层'), hold=2.0)
        except Exception as e:
            self.statusBar().showMessage('没找到版权页：%s' % e)

    def copy_text(self):
        """⧉ 复制文本：把阅读区当前纯文本复制到剪贴板（自动附纪年换算）。"""
        def _ann(x):
            try:
                y, hits = CHRONO.annotate_append(x)
                return y, len(hits)
            except Exception:
                return x, 0
        try:
            if getattr(self, '_reader', '') == 'pdf' and getattr(self, 'pd', None) is not None:
                try:
                    _sel = self.pdf_view.selected_text()
                except Exception:
                    _sel = ''
                if _sel:
                    _sel, _n = _ann(_sel)
                    QApplication.clipboard().setText(_sel)
                    self.statusBar().showMessage('已复制选中文本（%d 字%s）'
                                                 % (len(_sel), ('，含纪年换算 %d 处' % _n) if _n else ''))
                    return
                t = ''
                try:
                    t = (self.pd[self.pgno].get_text() or '').strip()
                except Exception:
                    t = ''
                if not t:
                    self.statusBar().showMessage('这页没有文字层')
                    return
                t, _n = _ann(t)
                QApplication.clipboard().setText(t)
                self.statusBar().showMessage('已复制本页文本（%d 字%s）'
                                             % (len(t), ('，含纪年换算 %d 处' % _n) if _n else ''))
                return
            t = self.view.toPlainText()
            if not t.strip():
                self.statusBar().showMessage('阅读区没有可复制的文本')
                return
            t, _n = _ann(t)
            QApplication.clipboard().setText(t)
            self.statusBar().showMessage('已复制阅读区文本（%d 字%s）'
                                         % (len(t), ('，含纪年换算 %d 处' % _n) if _n else ''))
        except Exception as e:
            self.statusBar().showMessage('复制失败：%s' % e)

    def copy_html(self):
        """⧉ 复制带格式：保留 HTML 格式复制（PDF 用文字层拼段落）。"""
        try:
            from PyQt6.QtCore import QMimeData
            html = self.view.toHtml() or ''
            text = self.view.toPlainText() or ''
            if getattr(self, '_reader', '') == 'pdf' and getattr(self, 'pd', None) is not None:
                try:
                    t = (self.pd[self.pgno].get_text() or '').strip()
                except Exception:
                    t = ''
                if not t:
                    self.statusBar().showMessage('这页没有文字层')
                    return
                import html as _h
                text = t
                html = ('<p>' + '</p><p>'.join(
                    _h.escape(x) for x in t.splitlines() if x.strip()) + '</p>')
            if not text.strip():
                self.statusBar().showMessage('阅读区没有可复制的文本')
                return
            try:                                     # 复制带格式 → 文本部分附纪年换算
                t2, hits = CHRONO.annotate_append(text)
                if hits:
                    import html as _h2
                    extra = t2[len(text):].strip()
                    html = (html or '') + '<p>' + _h2.escape(extra) + '</p>'
                    text = t2
            except Exception:
                pass
            md = QMimeData()
            md.setHtml(html)
            md.setText(text)
            QApplication.clipboard().setMimeData(md)
            self.statusBar().showMessage('已复制带格式文本')
        except Exception as e:
            self.statusBar().showMessage('复制失败：%s' % e)

    # ============================================================ batch11
    # ① 截图本（📷）／② 摘录本（✂）／③ PDF+TXT 对读（⇄）
    def _record_meta(self):
        """当前阅读对象的著录（书名/卷/作者/出版社/年），供截图/摘录出处用。"""
        path = getattr(self, '_pd_path', '') or getattr(self, '_text_path', '') or self._path()
        m = getattr(self, 'meta', None)
        if not m or not isinstance(m, dict):
            try:
                m = META.parse(os.path.basename(path or ''), path or '', deep=False)
            except Exception:
                m = {}
        return m or {}

    def _pdf_view_active(self):
        """当前有效的 PDF 视图：对读时用对读里的 PDF，否则用主阅读器的。"""
        if getattr(self, '_reader', '') == 'dual' and getattr(self, 'dual', None) is not None:
            return self.dual.pdf
        return self.pdf_view

    def _pdf_selection_span(self):
        """当前选区盖住的书页范围（1 起，(起, 止)）；没选东西 → None。

        【v0.3.13】双页并排时同一列上下拖，只有这一列的页算数（见 PdfView
        .selection_span），所以「跨了第 3–5 页」这种话不会把隔壁那页数进来。
        """
        try:
            v = self._pdf_view_active()
            if v is None or v.page_count() == 0:
                return None
            sp = v.selection_span()
            if not sp:
                return None
            return (int(sp[0]) + 1, int(sp[-1]) + 1)
        except Exception:
            return None

    def _pdf_page_1based(self):
        """记出处用的页码（1 起）—— 摘录 / 截图 / 书签都用它。

        【v0.3.12】不能用 self.pgno：双页并排时它跟的是"那一行的左边一页"，
        在右边那页上摘录、截图就会被记成左边的页码。这里取当前视图的焦点页
        （鼠标落点 / 选区所在那一页）。
        """
        try:
            v = self._pdf_view_active()
            if v is not None and v.page_count() > 0:
                return int(v.focus_page()) + 1
        except Exception:
            pass
        return int(getattr(self, 'pgno', 0) or 0) + 1

    def _txt_widget(self):
        """当前有效的文本框：对读时用对读里的 TXT，否则用主文本框。"""
        if getattr(self, '_reader', '') == 'dual' and getattr(self, 'dual', None) is not None:
            return self.dual.txt
        return self.view

    # ------------------------------------------------ batch26：出处文件的定位
    @staticmethod
    def _path_key_(p):
        """路径比较用的统一键（Windows 下大小写、斜杠方向都要忽略）。"""
        try:
            return os.path.normcase(os.path.abspath(str(p or '')))
        except Exception:
            return str(p or '')

    def _same_size_npages(self, path, size='', npages=''):
        """候选文件与记录的「字节数 / 页数」是否对得上。

        一项都对不上时返回 True（老记录没记指纹，不能因此否掉）。
        """
        if not path or not os.path.isfile(path):
            return False
        ok_any = False
        try:
            if str(size or '').isdigit():
                ok_any = True
                if int(os.path.getsize(path)) == int(size):
                    return True
        except OSError:
            pass
        try:
            if str(npages or '').isdigit() and int(npages) > 0:
                ok_any = True
                ext = os.path.splitext(path)[1].lower()
                if ext in ('.pdf', '.epub', '.xps', '.cbz', '.mobi', '.fb2'):
                    import fitz
                    d = fitz.open(path)
                    n = int(getattr(d, 'page_count', 0) or 0)
                    d.close()
                    if n == int(npages):
                        return True
        except Exception:
            pass
        return not ok_any

    def _scan_dirs_for_file(self, want, size='', npages='', max_items=20000,
                            max_depth=4):
        """在「已知书库根目录」里按文件名找一遍（找不到就算了，不要卡住界面）。

        只对 settings 里登记过的 roots 动手 —— 全盘扫太慢，也不该由程序自作主张。
        """
        want_l = (want or '').lower()
        if not want_l:
            return ''
        try:
            roots = [r for r in (C.load_settings().get('roots') or [])
                     if r and os.path.isdir(r)]
        except Exception:
            roots = []
        seen = 0
        import time
        t0 = time.time()
        for root in roots[:6]:
            try:
                for dirpath, dirnames, filenames in os.walk(root):
                    depth = dirpath[len(root):].count(os.sep)
                    if depth >= max_depth:
                        dirnames[:] = []
                    for f in filenames:
                        seen += 1
                        if seen > max_items or time.time() - t0 > 3.0:
                            return ''
                        if f.lower() == want_l:
                            p = os.path.join(dirpath, f)
                            if os.path.isfile(p):
                                return p
            except Exception:
                continue
        return ''

    def _alias_lookup(self, hint, fn=''):
        """查「这个文件的实际位置」记录表（用户手动指定过的优先）。"""
        try:
            m = TOOLS.load_path_map()
        except Exception:
            return ''
        if not m:
            return ''
        for k in (hint, fn, os.path.basename(hint or '')):
            k = (k or '').strip()
            if not k:
                continue
            for kk in (k, self._path_key_(k)):
                v = m.get(kk) or m.get(k) or ''
                if v and os.path.isfile(v):
                    return v
        return ''

    def _tail_segments(self, hint):
        """把绝对路径拆成「由深到浅」的尾部片段，用来在候选里比对目录结构。"""
        segs = []
        try:
            parts = [x for x in re.split(r'[\\/]+', str(hint).replace('/', os.sep))
                     if x]
            for i in range(len(parts) - 1, -1, -1):
                tail = os.sep.join(parts[i:])
                if len(tail) >= 3 and i < len(parts) - 1:
                    segs.append(tail.lower())
        except Exception:
            pass
        return segs[:6]

    def _find_file_by_hint(self, hint, fn='', size='', npages=''):
        """由「出处线索」定位真实文件（batch26 重写）。

        书本搬到别的盘、换过索引库、或者摘录是老版本记的之后，绝对路径就失效了。
        这里按「由准到糊」的顺序一路回捞：

          ① 绝对路径还在                → 直接用
          ② 用户手动指定过的位置        → 直接用（并长期记住）
          ③ 当前索引库：文件名精确
          ④ 当前索引库：书名主干前缀
          ⑤ 当前索引库：按原路径的尾部目录逐级比对
          ⑥ 备用索引库（若配了）
          ⑦ 在已登记的书库根目录里按文件名扫一遍

        ①②③…⑦ 逐级退让，任一级命中且「字节数/页数」对得上就返回。
        """
        hint = (hint or '').strip()
        want = fn or (os.path.basename(hint) if hint else '')
        core = os.path.splitext(want)[0] if want else ''
        if not hint and not want:
            return ''

        # ① 绝对路径还在就不用折腾了
        try:
            if hint and os.path.isfile(hint):
                return hint
        except Exception:
            pass

        # ② 用户之前手动指过位置 —— 最可靠，优先于任何猜测
        try:
            p = self._alias_lookup(hint, want)
            if p:
                return p
        except Exception:
            pass

        # ③ ④ ⑤ 索引库候选池（一次取出来，后面几级复用）
        pool = []
        for _db in self._candidate_dbs():
            try:
                rows = C.like_stem(_db, core, 60) if core else []
            except Exception:
                rows = []
            for r in rows:
                p = os.path.join(r.get('dir') or '', r.get('name') or '')
                if os.path.isfile(p) and p not in pool:
                    pool.append(p)
            if len(pool) >= 120:
                break

        # ③ 文件名完全一致
        _wl = (want or '').lower()
        exact = [p for p in pool if os.path.basename(p).lower() == _wl] if _wl else []
        for p in exact:
            if self._same_size_npages(p, size, npages):
                return p
        if exact:
            return exact[0]

        # ⑤ 按原路径的尾部目录逐级比对（书库整体搬家时，子目录结构常常是保留的）
        tails = self._tail_segments(hint)
        best = ''
        for t in tails:
            for p in pool:
                try:
                    if os.path.normcase(p).lower().endswith(t):
                        best = p
                        break
                except Exception:
                    continue
            if best:
                break
        if best:
            return best

        # ④ 主干前缀（书名被自动改名/加后缀时的兜底）
        for p in pool:
            if self._same_size_npages(p, size, npages):
                return p
        if pool:
            return pool[0]

        # ⑦ 已登记的书库根目录里扫一遍
        try:
            p = self._scan_dirs_for_file(want, size, npages)
            if p:
                return p
        except Exception:
            pass
        return ''

    def _candidate_dbs(self):
        """当前主索引库 + 备用索引库（存在的才给）。"""
        out = []
        try:
            if getattr(self, 'db', None) and os.path.isfile(self.db):
                out.append(self.db)
        except Exception:
            pass
        try:
            b = (C.load_settings().get('backup_db') or '')
            if b and os.path.isfile(b) and b not in out:
                out.append(b)
        except Exception:
            pass
        return out

    def _ask_missing_source(self, hint, want='', page='', where='摘录'):
        """出处文件实在找不到时，明确告诉用户 + 提供「手动指定」（batch26）。

        以前只往状态栏丢一句话，用户点了「打开出处」看着就像没反应；这里改成
        弹窗，并允许当场指认一次 —— 指认结果写进 filepaths.json，以后同书
        的所有摘录/截图都能直接打开，不用再找一遍。
        """
        from PyQt6.QtWidgets import QMessageBox, QFileDialog
        want = want or (os.path.basename(hint) if hint else '')
        mbox = QMessageBox(self)
        mbox.setWindowTitle('打不开出处')
        mbox.setIcon(QMessageBox.Icon.Information)
        mbox.setText('找不到这条%s的出处文件：\n%s' % (where, (hint or '（这条没记出处）')))
        mbox.setInformativeText(
            '常见原因：书搬到别的盘 / 换过索引库 / 文件改名。\n'
            '点「手动指定」选一次文件位置，以后会自动记住。')
        b_manual = mbox.addButton('手动指定…', QMessageBox.ButtonRole.AcceptRole)
        mbox.addButton('关闭', QMessageBox.ButtonRole.RejectRole)
        mbox.exec()
        if mbox.clickedButton() is not b_manual:
            return False
        start = os.path.dirname(hint) if hint and os.path.isdir(
            os.path.dirname(hint)) else ''
        p, _ = QFileDialog.getOpenFileName(
            self, '指出这本书现在在哪儿',
            start,
            '文档 (*.pdf *.epub *.txt *.md *.xps *.cbz *.mobi *.fb2);;所有文件 (*.*)')
        if not p or not os.path.isfile(p):
            return False
        keys = [(k or '').strip()
                for k in (hint, want, os.path.basename(hint or '')) if k]
        try:
            for k in keys:
                TOOLS.remember_path(k, p)   # 内部会一并登记原名 key 与文件名索引
        except Exception:
            pass
        try:
            self.statusBar().showMessage('已记住：%s → %s' % (want or hint, p))
        except Exception:
            pass
        self._open_and_goto(p, page)
        return True

    def _open_and_goto(self, path, page=''):
        """打开文件并跳到指定页（page 为 1 起；给不出就只打开）。"""
        if not path or not self._open_path(path):
            return False
        try:
            pg = int(page)
        except Exception:
            pg = 0
        if pg > 0:
            d = getattr(self, 'pd', None)
            if d is not None and int(getattr(d, 'page_count', 0) or 0) > 0:
                self.pgno = max(0, min(int(d.page_count) - 1, pg - 1))
                try:
                    self._pdf_view_active().goto_page(self.pgno)
                except Exception:
                    pass
        return True

    # ---- ① 截图本
    def snapshot_here(self):
        """📷 截图：先让用户选「整页」还是「自选区域」，再存进截图本。

        batch23：截图有两条路子 —— ① 整页（按当前页出图）；② 自选区域
        （自己拖框，PDF 支持跨页；文本视图也能拖框）。两条都会引导填页码。
        """
        rd = getattr(self, '_reader', '')
        has_pdf = False
        try:
            has_pdf = (rd in ('pdf', 'dual')
                       and self._pdf_view_active().page_count() > 0
                       and getattr(self, 'pd', None) is not None)
        except Exception:
            has_pdf = False
        has_txt = rd in ('text', 'dual')
        if not has_pdf and not has_txt:
            r = self._cur()
            p = os.path.join(r.get('dir') or '', r.get('name') or '') if r else ''
            if p.lower().endswith('.pdf') and os.path.isfile(p):
                self.preview_here()
                has_pdf = getattr(self, 'pd', None) is not None
        if not has_pdf and not has_txt:
            self.statusBar().showMessage('截图需要先在阅读区打开一个 PDF 或 TXT')
            return
        from PyQt6.QtWidgets import QMenu as _QM
        m = _QM(self)
        if has_pdf:
            # v0.3.12：整页截图取焦点页（双页时可能是右边那页），不再一律取 pgno
            _p0 = (self.dual.current_page() if rd == 'dual'
                   else self._pdf_view_active().focus_page())
            m.addAction('🖼 整页截图（第 %d 页）' % (int(_p0) + 1),
                        lambda: self._snapshot_from_pdf(int(_p0)))
            m.addAction('✂ 自选区域截图（拖框，可跨页）', self._begin_pdf_region)
        if has_txt:
            m.addAction('✂ 自选区域截图（文本区拖框）', self._begin_text_region)
        try:
            m.exec(self.mapToGlobal(QPoint(24, self.height() - 90)))
        except Exception:
            pass

    def _begin_pdf_region(self):
        """进入 PDF 框选截图模式（下一次拖拽即在 PDF 上拉框）。"""
        try:
            v = self._pdf_view_active()
            v.begin_region()
            self._say('在页面上按住左键拖出要截的区域（可以拖到下一页，支持跨页）', hold=6)
        except Exception as e:
            self.statusBar().showMessage('进入框选失败：%s' % e)

    def _begin_text_region(self):
        """进入文本区框选截图模式（TXT / MD 也能截）。"""
        try:
            tw = self._txt_widget()
            if tw is None:
                return
            tw.begin_snap_region()
            self._say('在文本区按住左键拖出要截的片段（Esc 取消）', hold=6)
        except Exception as e:
            self.statusBar().showMessage('进入文本框选失败：%s' % e)

    def _snapshot_from_pdf(self, page):
        """整页截图 → 引导页码 → 引导备注 → 存入截图本。"""
        try:
            v = self._pdf_view_active()
            pm = v.grab_page_pixmap(int(page))
            if pm is None or pm.isNull():
                self.statusBar().showMessage('这一页还没渲染好，稍等一下再截')
                return
            from PyQt6.QtWidgets import QInputDialog
            num, ok = QInputDialog.getInt(
                self, '截图页码', '这张图对应书里的第几页？（可修改）',
                int(page) + 1, 0, 100000, 1)
            if not ok:
                return
            self._save_snapshot(pm, num)
        except Exception as e:
            self.statusBar().showMessage('截图失败：%s' % e)

    def _on_pdf_region(self, page, rect):
        """单页框选 → 裁剪 → 引导页码 → 存入截图本。"""
        try:
            v = self._pdf_view_active()
            pm = v.grab_page_pixmap(int(page))
            if pm is None or pm.isNull():
                self.statusBar().showMessage('框选失败：页面未渲染')
                return
            r = QRect(rect).intersected(QRect(0, 0, pm.width(), pm.height()))
            if r.width() < 8 or r.height() < 8:
                return
            crop = pm.copy(r)
            from PyQt6.QtWidgets import QInputDialog
            num, ok = QInputDialog.getInt(
                self, '截图页码', '选中区域对应书里的第几页？',
                int(page) + 1, 0, 100000, 1)
            if not ok:
                return
            self._save_snapshot(crop, num)
        except Exception as e:
            self.statusBar().showMessage('框选截图失败：%s' % e)

    def _on_pdf_region_spans(self, spans):
        """跨页框选：把各页的对应部分裁下来，纵向拼成一张图再保存。"""
        try:
            v = self._pdf_view_active()
            crops = []
            for page, rect in (spans or []):
                pm = v.grab_page_pixmap(int(page))
                if pm is None or pm.isNull():
                    continue
                r = QRect(rect).intersected(QRect(0, 0, pm.width(), pm.height()))
                if r.width() < 4 or r.height() < 4:
                    continue
                crops.append(pm.copy(r))
            if not crops:
                self.statusBar().showMessage('框选区域没有内容（页面可能还没渲染好）')
                return
            try:
                w = max(c.width() for c in crops)
                h = sum(c.height() for c in crops)
                out = QPixmap(w, h)
                out.fill(QColor(255, 255, 255))
                pnt = QPainter(out)
                _y = 0
                for c in crops:
                    pnt.drawPixmap(0, _y, c)
                    _y += c.height()
                pnt.end()
            except Exception as e:
                self.statusBar().showMessage('拼接跨页截图失败：%s' % e)
                return
            a = int(spans[0][0]) + 1
            b = int(spans[-1][0]) + 1
            from PyQt6.QtWidgets import QInputDialog
            num, ok = QInputDialog.getInt(
                self, '跨页截图',
                '这段跨了第 %d–%d 页。对应书里的起始页是第几页？' % (a, b),
                a, 0, 100000, 1)
            if not ok:
                return
            self._save_snapshot(out, num, span=(a, b))
        except Exception as e:
            self.statusBar().showMessage('跨页截图失败：%s' % e)

    def _on_text_region(self, rect):
        """文本框选截图：截下那一小块 → 引导页码 → 存入截图本（TXT/MD 也能截）。"""
        try:
            tw = self._txt_widget()
            if tw is None:
                return
            pm = tw.viewport().grab(QRect(rect))
            if pm is None or pm.isNull():
                self.statusBar().showMessage('文本框选截图失败')
                return
            from PyQt6.QtWidgets import QInputDialog
            num, ok = QInputDialog.getInt(
                self, '截图页码',
                '这段文字对应书里的第几页？（不确定就填 0）',
                0, 0, 100000, 1)
            if not ok:
                return
            self._save_snapshot(pm, num)
        except Exception as e:
            self.statusBar().showMessage('文本框选截图失败：%s' % e)

    def _page_text_for_note(self, page, span=None):
        """取某一页（跨页则取首尾两页）的文字层，供「没写备注时自动收录」。"""
        d = getattr(self, 'pd', None)
        if d is None:
            return ''
        out = []
        try:
            n = int(d.page_count)
            pages = ([int(span[0]), int(span[1])] if span else [int(page)])
            for p in pages:
                if 1 <= p <= n:
                    t = (d[p - 1].get_text() or '').strip()
                    if t:
                        out.append(t)
        except Exception:
            return ''
        return '\n'.join(out)[:8000]

    def _save_snapshot(self, pm, page, span=None):
        """存截图前的收尾：引导备注 → 不填就自动收录文字层 → 写入截图本。

        batch23（用户要求）：截图保存时要问一句备注，方便以后搜到这张图；
        用户不写，就把这一页（跨页则首尾两页）的文字层记进去并注明来源，
        这样"截图里的内容"依然是可检索的。
        """
        note, text, text_auto = '', '', False
        try:
            from PyQt6.QtWidgets import QInputDialog
            note, okk = QInputDialog.getText(
                self, '截图备注',
                '给这张截图写个备注吧（以后好搜）？\n'
                '留空也行 —— 那就自动把这一页的文字层记进去（会注明是自动收录）。')
            if not okk:
                return False
            note = (note or '').strip()
        except Exception:
            note = ''
        if not note:
            text = self._page_text_for_note(page, span)
            text_auto = bool(text)
        try:
            from PyQt6.QtCore import QBuffer, QIODevice, QByteArray
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            pm.save(buf, 'PNG')
            buf.close()
            meta = self._record_meta()
            rd = getattr(self, '_reader', '')
            if rd == 'text':
                src = getattr(self, '_text_path', '') or self._path()
            else:
                src = getattr(self, '_pd_path', '') or self._path()
            try:
                _cite = META.cite(meta, page=(page if page else 'X'))
            except Exception:
                _cite = ''
            r = TOOLS.add_snapshot(bytes(ba), meta, page, src, note=note,
                                   text=text, text_auto=text_auto,
                                   cite_std=_cite, span=span,
                                   npages=(int(getattr(self.pd, 'page_count', 0) or 0)
                                           or None))
            if not r or r.get('error'):
                self.statusBar().showMessage('截图保存失败：%s' % ((r or {}).get('error') or '未知'))
                return False
            _tail = '（已自动收录该页文字，便于检索）' if text_auto else ''
            self._say('已存入截图本：%s%s' % (r.get('name') or '', _tail), hold=3.0)
            self._refresh_snapshot_tab()
            return True
        except Exception as e:
            self.statusBar().showMessage('截图保存失败：%s' % e)
            return False

    def _refresh_snapshot_tab(self):
        """截图本窗口开着的话，存完立刻刷新出来。"""
        try:
            fn = getattr(self, '_snap_reload', None)
            if callable(fn):
                fn()
        except Exception:
            pass

    # ---- ② 摘录本
    def _current_selection(self):
        """当前阅读区选中的文字（PDF 文字层 / 文本 / 对读），无则空串。"""
        sel = ''
        if getattr(self, '_reader', '') in ('pdf', 'dual'):
            try:
                sel = self._pdf_view_active().selected_text()
            except Exception:
                sel = ''
        if not (sel or '').strip():
            try:
                sel = self._txt_widget().textCursor().selectedText().replace('\u2029', '\n')
            except Exception:
                sel = ''
        return sel or ''

    def excerpt_here(self):
        """✂ 摘录：把选中文字存入摘录本（自动附出处 + 纪年换算）。"""
        sel = self._current_selection()
        if not (sel or '').strip():
            self.statusBar().showMessage('先在正文里选中一段文字再摘录（Ctrl+Shift+E）')
            return
        self._excerpt_from_text(sel)

    def _excerpt_from_pdf(self, sel):
        if not (sel or '').strip():
            self.statusBar().showMessage('先选中一段文字再摘录')
            return
        self._excerpt_from_text(sel)

    def _excerpt_from_text(self, text):
        meta = self._record_meta()
        rd = getattr(self, '_reader', '')
        _np = 0
        if rd in ('pdf', 'dual') and getattr(self, 'pd', None) is not None:
            # 【v0.3.12】别再用 self.pgno：双页时它是"那一行左边的页"，
            # 摘录右边那页就会被记成左边页码。改用焦点页（有选区就取选区那页）。
            page = int(self._pdf_page_1based())
            src = getattr(self, '_pd_path', '') or self._path()
            try:
                _np = int(getattr(self.pd, 'page_count', 0) or 0)
            except Exception:
                _np = 0
        else:
            page = ''
            src = getattr(self, '_text_path', '') or self._path()
        try:                                     # 摘录也是「引文」：自动做纪年换算
            text, _hits = CHRONO.annotate(text)
            _n = len(_hits)
        except Exception:
            _n = 0
        try:                       # 标准引文（与「📋 复制引文」同一格式），随摘录一起存
            _cite_std = META.cite(meta, page=(page if page else 'X'))
        except Exception:
            _cite_std = ''
        r = TOOLS.add_excerpt(meta, text, page, src, cite_std=_cite_std,
                              npages=_np or None)
        if not r or r.get('error'):
            self.statusBar().showMessage('摘录失败：%s' % ((r or {}).get('error') or '未知'))
            return
        # 【v0.3.13】跨页摘录：出处按"起头那页"记，再在提示里说清跨了哪几页 ——
        # 免得事后翻摘录本时对着一条文字不知道它到底跨没跨页。
        _sp = self._pdf_selection_span()
        _sp_tip = ''
        if _sp and _sp[0] != _sp[1]:
            _sp_tip = '（跨第 %d–%d 页，出处记第 %d 页）' % (_sp[0], _sp[1], _sp[0])
        self._say('已摘录 %d 字 → 摘录本%s%s'
                  % (r.get('chars') or 0,
                     ('（含纪年换算 %d 处）' % _n) if _n else '',
                     _sp_tip), hold=2.5 + (1.5 if _sp_tip else 0))
        try:                       # 摘录本窗口开着的话，立刻能看到新摘的这条
            fn = getattr(self, '_exc_reload', None)
            if callable(fn):
                fn()
        except Exception:
            pass

    # ---- ④ 历史纪年换算（⌛ / Ctrl+Shift+Y）
    def chrono_dialog(self):
        """随时可开的「历史纪年换算」工具：民国/年号/干支 ⇄ 公元年。"""
        dlg = QDialog(self)
        dlg.setWindowTitle('历史纪年换算')
        dlg.resize(780, 640)
        vb = QVBoxLayout(dlg)
        vb.addWidget(QLabel('把带纪年的文字（民国 / 年号 / 干支）粘到下面，点「换算」：'
                            '每个纪年后会加【=公元年】；民国 1～38 年也会换算；'
                            '越界纪年（如康熙63年）会算出公元年并提示该年实际纪年；'
                            '干支会列出 1700–2000 年全部对应年份。'))
        ed_in = QTextEdit()
        vb.addWidget(ed_in, 1)
        row = FlowLayout()                   # batch22：窗口窄了自动换行
        b_conv = QPushButton('换算')
        b_copy = QPushButton('复制结果')
        b_ins = QPushButton('取当前选区')
        b_look = QPushButton('反查公元年')
        b_close = QPushButton('关闭')
        row.addWidget(b_conv)
        row.addWidget(b_copy)
        row.addWidget(b_ins)
        row.addWidget(b_look)
        row.addStretch(1)
        row.addWidget(b_close)
        vb.addLayout(row)
        vb.addWidget(QLabel('结果：'))
        ed_out = QTextEdit()
        ed_out.setReadOnly(True)
        vb.addWidget(ed_out, 1)
        pre = self._current_selection()
        if not (pre or '').strip():
            try:
                pre = QApplication.clipboard().text() or ''
            except Exception:
                pre = ''
        ed_in.setPlainText(pre or '')

        def do_conv():
            ann, hits = CHRONO.annotate(ed_in.toPlainText(), allow_short_republic=True)
            ed_out.setPlainText(ann)
            self.statusBar().showMessage('纪年换算：命中 %d 处' % len(hits))

        def do_copy():
            QApplication.clipboard().setText(ed_out.toPlainText())
            self.statusBar().showMessage('已复制换算结果')

        def do_ins():
            s = self._current_selection()
            if (s or '').strip():
                ed_in.setPlainText(s)
                do_conv()
            else:
                self.statusBar().showMessage('阅读区没有选中文字')

        def do_look():
            from PyQt6.QtWidgets import QInputDialog
            y, okk = QInputDialog.getInt(self, '反查公元年', '输入公元年（1000–2100）：',
                                         1898, 1000, 2100, 1)
            if not okk:
                return
            eras = CHRONO.format_eras(y)          # 每个年号都注明「是第几年」
            gz_note = ''
            try:
                gz_note = CHRONO.year_to_ganzhi(y)
            except Exception:
                pass
            lines = ['公元 %d 年：' % y,
                     '干支：%s' % gz_note,
                     '在用的年号：%s' % (eras or '—'),
                     ('民国纪年：民国 %s年' % CHRONO.era_year_cn(y - 1911))
                     if y >= 1912 else '（无民国纪年）']
            ed_out.setPlainText('\n'.join(lines))

        b_conv.clicked.connect(do_conv)
        b_copy.clicked.connect(do_copy)
        b_ins.clicked.connect(do_ins)
        b_look.clicked.connect(do_look)
        b_close.clicked.connect(dlg.accept)
        self._last_chrono = {'in': ed_in, 'out': ed_out, 'dlg': dlg}
        if (pre or '').strip():
            do_conv()
        self._show_util_dialog(dlg, '_dlg_chrono')

    # ---- ⑤b 代日韵目（电报韵目代日对照表）Ctrl+Shift+R
    def rhyme_dialog(self):
        """「代日韵目」表：默认列出 1–31 日的全部韵目；输入韵目字或日期即高亮。

        近代电报用《平水韵》韵目字代替日期——「有电」＝25 日的电报，
        「艳电」＝29 日的电报。五栏 = 上平声 / 下平声 / 上声 / 去声 / 入声，
        某声调排不到那么后的一栏就空缺（所以 16 日起上平、下平两栏是空的）。
        """
        try:
            from PyQt6.QtGui import QColor, QBrush, QFont
        except Exception:
            QColor = QBrush = QFont = None
        dlg = QDialog(self)
        dlg.setWindowTitle('代日韵目（电报韵目代日对照表）')
        dlg.resize(780, 720)
        vb = QVBoxLayout(dlg)
        vb.addWidget(QLabel(
            '近代电报用《平水韵》韵目字代替日期：「有电」＝25 日的电报，「艳电」＝29 日的电报。\n'
            '16 日起上平声 / 下平声已无对应韵目，故两栏空缺；30 日按规律是去声「陷」，'
            '因军中嫌「陷」不吉利而通用「卅」；31 日平水韵无此韵，用「世」或「引」。'))
        _lb = QLabel()
        _lb.setWordWrap(True)
        vb.addWidget(_lb)

        ed = QLineEdit()
        ed.setPlaceholderText('输入韵目字（如 艳 / 有 / 卅）或日期（如 29）→ 自动高亮')
        vb.addWidget(ed)

        rows = RHYME.table_rows()
        tbl = QTableWidget(len(rows), 7)
        tbl.setHorizontalHeaderLabels(['日期', '上平声', '下平声', '上声', '去声', '入声', '常用'])
        tbl.verticalHeader().setVisible(False)
        try:
            tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            tbl.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        except Exception:
            pass
        _cell = {}
        for r, row in enumerate(rows):
            it = QTableWidgetItem('%d 日' % row['day'])
            tbl.setItem(r, 0, it)
            _cell.setdefault(str(row['day']), []).append((r, 0))
            for ci, ch in enumerate(row['cells']):
                x = QTableWidgetItem(ch or '－')
                if ch:
                    x.setToolTip('%s　→　%d 日' % (RHYME.full_name(ch, row['day']), row['day']))
                    _cell.setdefault(ch, []).append((r, ci + 1))
                else:
                    x.setForeground(QColor('#9a9a9a') if QColor else x.foreground())
                tbl.setItem(r, ci + 1, x)
            cm = row['common']
            x = QTableWidgetItem(cm)
            if QFont:
                f = QFont()
                f.setBold(True)
                x.setFont(f)
            x.setToolTip((row['note'] or '最常用的代日用字') + '　→　%d 日' % row['day'])
            _cell.setdefault(cm, []).append((r, 6))
            tbl.setItem(r, 6, x)
        try:
            _hh = tbl.horizontalHeader()
            _hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            _hh.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        except Exception:
            pass
        vb.addWidget(tbl, 1)

        _hl = QColor('#ffd54f') if QColor else None
        _br = QBrush(_hl) if (QBrush and _hl) else None

        def clear_hl():
            for r in range(tbl.rowCount()):
                for c in range(tbl.columnCount()):
                    it = tbl.item(r, c)
                    if it is not None and _br is not None:
                        it.setBackground(QBrush())

        def do_find():
            clear_hl()
            q = (ed.text() or '').strip()
            if not q:
                _lb.setText('')
                return
            hits, days = [], []
            if q.isdigit():                      # 输日期 → 整行高亮
                d = int(q)
                if 1 <= d <= 31:
                    days = [d]
                    for c in range(tbl.columnCount()):
                        it = tbl.item(d - 1, c)
                        if it is not None and _br is not None:
                            it.setBackground(_br)
                    tbl.scrollToItem(tbl.item(d - 1, 0))
            else:
                for ch in q:
                    if ch.isspace():
                        continue
                    info = RHYME.lookup(ch)
                    if not info:
                        continue
                    days.append(info['day'])
                    hits.append('%s＝%d 日（%s）' % (ch, info['day'], info['name']))
                    for r, c in _cell.get(ch, []):
                        it = tbl.item(r, c)
                        if it is not None and _br is not None:
                            it.setBackground(_br)
                    if _cell.get(ch):
                        tbl.scrollToItem(tbl.item(_cell[ch][0][0], _cell[ch][0][1]))
            if hits:
                _lb.setText('　'.join(hits))
                self._say('代日韵目：%s' % '；'.join(hits))
            elif days:
                _lb.setText('%d 日：常用「%s」' % (days[0], RHYME.common_char(days[0])))
                self._say('代日韵目：%d 日，常用「%s」' % (days[0], RHYME.common_char(days[0])))
            else:
                _lb.setText('「%s」不在韵目表里' % q)
                self._say('代日韵目表里没有「%s」' % q)

        brow = FlowLayout()                  # 窗口窄了自动换行
        b_find = QPushButton('查找')
        b_ins = QPushButton('取当前选区')
        b_copyrow = QPushButton('复制本表')
        b_close = QPushButton('关闭')
        for x in (b_find, b_ins, b_copyrow):
            brow.addWidget(x)
        brow.addStretch(1)
        brow.addWidget(b_close)
        vb.addLayout(brow)

        def do_ins():
            s = (self._current_selection() or '').strip()
            if not s:
                try:
                    s = (QApplication.clipboard().text() or '').strip()
                except Exception:
                    s = ''
            if s:
                ed.setText(s[:8])
                do_find()

        def do_copy():
            lines = ['日期\t上平声\t下平声\t上声\t去声\t入声\t常用']
            for row in rows:
                lines.append('\t'.join(['%d 日' % row['day']]
                                       + [c or '－' for c in row['cells']]
                                       + [row['common']]))
            QApplication.clipboard().setText('\n'.join(lines))
            self._say('已复制整张代日韵目表')

        b_find.clicked.connect(do_find)
        b_ins.clicked.connect(do_ins)
        b_copyrow.clicked.connect(do_copy)
        b_close.clicked.connect(dlg.accept)
        ed.textChanged.connect(lambda *_: do_find())
        ed.returnPressed.connect(do_find)
        self._last_rhyme = {'dlg': dlg, 'ed': ed, 'tbl': tbl}
        self._show_util_dialog(dlg, '_dlg_rhyme')

    # ---- ⑥ 摘录资料 查看 / 编辑（🗂 / Ctrl+Shift+M）
    def excerpt_viewer(self):
        """查看 + 编辑「摘录本」；另有「截图本」页签（可预览 / 打开图片）。

        batch22：改成**非模态**（不锁前台，主窗口照常能选中文字继续摘录），
        并支持「复制引文 / 打开出处 / 参考页码 / 复制正文（可选带出处）」，
        关闭时有改动会问是否保存。
        """
        from PyQt6.QtWidgets import (QTabWidget, QListWidget, QListWidgetItem,
                                     QPlainTextEdit)
        from PyQt6.QtGui import QPixmap
        _open_dlg = getattr(self, '_exc_dlg', None)
        if _open_dlg is not None:
            try:                       # 已经开着 → 直接带到前面，不开第二个
                _open_dlg.show()
                _open_dlg.raise_()
                _open_dlg.activateWindow()
                return
            except Exception:
                pass
        dlg = QDialog(self)
        dlg.setWindowTitle('摘录截图本（摘录 + 截图）')
        dlg.resize(960, 660)
        vb = QVBoxLayout(dlg)
        tabs = QTabWidget()
        vb.addWidget(tabs, 1)

        # ---------- 摘录本（可编辑）
        page = QWidget()
        pv = QHBoxLayout(page)
        lcol = QVBoxLayout()
        self._exc_filter = QLineEdit()
        self._exc_filter.setPlaceholderText('过滤：书名 / 关键词')
        lcol.addWidget(self._exc_filter)
        lst = QListWidget()
        lcol.addWidget(lst, 1)
        lrow = FlowLayout()                  # batch22：窗口窄了自动换行
        b_new = QPushButton('＋ 新增')
        b_del = QPushButton('－ 删除')
        b_up = QPushButton('↑ 上移')
        b_dn = QPushButton('↓ 下移')
        for x in (b_new, b_del, b_up, b_dn):
            lrow.addWidget(x)
        lcol.addLayout(lrow)
        rcol = QVBoxLayout()
        rcol.addWidget(QLabel('正文（可编辑）：'))
        ed_body = QPlainTextEdit()
        rcol.addWidget(ed_body, 3)
        rcol.addWidget(QLabel('出处（书名 / 页码 / 版本 …，可编辑）：'))
        ed_cite = QPlainTextEdit()
        ed_cite.setMaximumHeight(90)
        rcol.addWidget(ed_cite, 1)
        # batch22：标准引文（与「📋 复制引文」同格式）+ 参考页码 + 打开出处
        rcol.addWidget(QLabel('标准引文（复制引文用）：'))
        row_cite = QHBoxLayout()
        ed_citestd = QLineEdit()
        ed_citestd.setReadOnly(True)
        ed_citestd.setPlaceholderText('（这条没有标准引文；老记录可能没有，可自己复制下面出处）')
        b_copycite = QPushButton('📋 复制引文')
        b_copycite.setToolTip('复制成标准的学术引用格式（作者：《书名》，出版地：出版社，年，第X页。）')
        row_cite.addWidget(ed_citestd, 1)
        row_cite.addWidget(b_copycite)
        rcol.addLayout(row_cite)
        row_src = QHBoxLayout()
        lb_page = QLabel('参考页码：—')
        row_src.addWidget(lb_page, 1)
        b_opensrc = QPushButton('📖 打开出处')
        b_opensrc.setToolTip('打开这条摘录对应的文件，并跳到记录的那一页')
        b_copybody = QPushButton('⧉ 复制正文')
        b_copybody.setToolTip('复制正文；会问你是否连出处一起复制')
        row_src.addWidget(b_opensrc)
        row_src.addWidget(b_copybody)
        rcol.addLayout(row_src)
        brow = FlowLayout()                  # batch22：窗口窄了自动换行
        b_save = QPushButton('保存本条')
        b_save_all = QPushButton('保存全部')
        b_open = QPushButton('打开摘录本.md')
        b_reload = QPushButton('重新载入')
        b_close = QPushButton('关闭')
        for x in (b_save, b_save_all, b_open, b_reload):
            brow.addWidget(x)
        brow.addStretch(1)
        brow.addWidget(b_close)
        rcol.addLayout(brow)
        pv.addLayout(lcol, 1)
        pv.addLayout(rcol, 1)
        tabs.addTab(page, '摘录本')

        # ---------- 截图本（预览 + 出处/备注 + 复制/打开）
        sp = QWidget()
        sv = QHBoxLayout(sp)
        slist = QListWidget()
        sprev = QLabel('选一张截图预览')
        sprev.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sprev.setMinimumWidth(320)
        sright = QVBoxLayout()
        sright.addWidget(QLabel('这张截图的出处 / 备注：'))
        sdetail = QPlainTextEdit()
        sdetail.setReadOnly(True)
        sdetail.setMaximumWidth(320)
        sright.addWidget(sdetail, 1)
        _srow = FlowLayout(margin=0, spacing=4)
        b_scite = QPushButton('📋 复制引文')
        b_scite.setToolTip('照「复制引用」的格式复制这张截图的出处')
        b_snote = QPushButton('📋 复制备注')
        b_snote.setToolTip('把备注连同出处一起复制（含自动收录的正文）')
        b_ssrc = QPushButton('📖 打开出处')
        b_ssrc.setToolTip('打开这张截图对应的文件，并跳到记录的那一页')
        b_sopen = QPushButton('用系统看图打开')
        b_sdir = QPushButton('打开截图本文件夹')
        b_srefresh = QPushButton('刷新')
        for x in (b_scite, b_snote, b_ssrc, b_sopen, b_sdir, b_srefresh):
            _srow.addWidget(x)
        sright.addLayout(_srow)
        sv.addWidget(slist, 1)
        sv.addWidget(sprev, 2)
        sv.addLayout(sright)
        tabs.addTab(sp, '截图本')

        state = {'recs': [], 'cur': -1, 'dirty': False}

        def open_any(p):
            try:
                os.startfile(p)
            except Exception:
                try:
                    self._open_path(p)
                except Exception:
                    self.statusBar().showMessage('打不开：%s' % p)

        snap_state = {'rows': [], 'paths': {}, 'cur': -1}

        def refresh_snaps():
            """列出截图本：以「截图本.md」的记录为主（带出处/备注），
            目录里有图但没记录的（老截图）也一并列出、只是没有出处信息。"""
            try:
                rows = TOOLS.parse_snapshots()
            except Exception:
                rows = []
            try:
                files = {s['name']: s['path'] for s in TOOLS.list_snapshots()}
            except Exception:
                files = {}
            _dir = os.path.dirname(TOOLS.snapshot_target())
            _known = {r.get('img') for r in rows if r.get('img')}
            for name in files:
                if name not in _known:
                    rows.append({'ts': '', 'headline': name, 'detail': '', 'img': name,
                                 'note': '', 'text': '', 'src': '', 'page': '',
                                 'cite_std': '', 'dir': _dir})
            snap_state['rows'] = rows
            snap_state['paths'] = files
            slist.blockSignals(True)
            slist.clear()
            for i, r in enumerate(rows):
                _note = ((r.get('note') or '').strip()
                         or (r.get('text') or '').strip()[:26] or '（没写备注）')
                txt = '%s ｜ %s ｜ %s' % ((r.get('ts') or '')[:16],
                                         (r.get('headline') or '')[:18],
                                         _note[:24])
                it = QListWidgetItem(txt)
                it.setData(Qt.ItemDataRole.UserRole, i)
                slist.addItem(it)
            slist.blockSignals(False)
            if slist.count():
                slist.setCurrentRow(0)
            else:
                sprev.setText('截图本还是空的（阅读时点「📷 截图」）')
                sdetail.setPlainText('')

        def _snap_cur():
            i = snap_state['cur']
            if 0 <= i < len(snap_state['rows']):
                return snap_state['rows'][i]
            return None

        def show_snap(*_):
            it = slist.currentItem()
            if not it:
                return
            i = int(it.data(Qt.ItemDataRole.UserRole))
            snap_state['cur'] = i
            r = _snap_cur() or {}
            name = r.get('img') or ''
            path = snap_state['paths'].get(name) or ''
            if not path:
                try:
                    path = os.path.join(os.path.dirname(TOOLS.snapshot_target()), name)
                except Exception:
                    path = ''
            if path and os.path.isfile(path):
                pm = QPixmap(path)
            else:
                pm = QPixmap()
            if pm.isNull():
                sprev.setText('打不开图片')
            else:
                sprev.setPixmap(pm.scaled(max(340, sprev.width() - 10), 520,
                                          Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
            _bits = []
            if r.get('ts'):
                _bits.append('截图时间：%s' % r['ts'])
            if r.get('headline'):
                _bits.append('出处：%s' % r['headline'])
            if r.get('detail'):
                _bits.append(r['detail'])
            if r.get('page'):
                _bits.append('参考页码：第 %s 页（读自文件，可能与书上页码不一致）' % r['page'])
            if r.get('note'):
                _bits.append('备注：%s' % r['note'])
            if r.get('text'):
                _bits.append('—— 收录的正文（自动）——\n%s' % r['text'])
            if not r.get('src') and not r.get('detail'):
                _bits.append('（这条是目录里的老截图，没有记录出处）')
            sdetail.setPlainText('\n'.join(_bits))

        def _snap_copy(kind):
            r = _snap_cur()
            if not r:
                self.statusBar().showMessage('先选中一张截图')
                return
            cite = (r.get('cite_std') or '').strip()
            if not cite:
                cite = ' '.join(x for x in (r.get('headline'), r.get('detail')) if x).strip()
            if kind == 'cite':
                if not cite:
                    self.statusBar().showMessage('这条没记出处，复制不了引文')
                    return
                QApplication.clipboard().setText(cite)
                self.statusBar().showMessage('已复制引文：%s' % cite[:60])
                return
            parts = []
            if (r.get('note') or '').strip():
                parts.append(r['note'].strip())
            if (r.get('text') or '').strip():
                parts.append(r['text'].strip())
            if cite:
                parts.append('—— ' + cite)
            if not parts:
                self.statusBar().showMessage('这条没有备注和出处可复制')
                return
            QApplication.clipboard().setText('\n\n'.join(parts))
            self.statusBar().showMessage('已复制备注 + 出处')

        def _snap_open_src():
            r = _snap_cur()
            if not r:
                self.statusBar().showMessage('先选中一张截图')
                return
            src = (r.get('src') or '').strip()
            pg = (r.get('page') or '').strip()
            hint = src or (r.get('headline') or '')
            # batch26：指纹（文件名/字节数/页数）跟着走，路径失效也能回捞
            p2 = self._find_file_by_hint(
                src, fn=(r.get('fn') or os.path.basename(src or '')),
                size=(r.get('size') or ''), npages=(r.get('npages') or ''))
            if not p2 and hint:
                p2 = self._find_file_by_hint(hint)
            if p2:
                self._open_and_goto(p2, pg)
                return
            self._ask_missing_source(src or hint,
                                     want=(r.get('fn') or os.path.basename(src or '')),
                                     page=pg, where='截图')

        def row_of(idx, widget):
            for k in range(widget.count()):
                if int(widget.item(k).data(Qt.ItemDataRole.UserRole)) == idx:
                    return k
            return 0

        def refresh_list(keep=-1):
            lst.blockSignals(True)
            lst.clear()
            kw = (self._exc_filter.text() or '').strip().lower()
            for i, r in enumerate(state['recs']):
                line = (r.get('body') or '').splitlines()
                line = line[0] if line else ''
                txt = '%s ｜ %s' % (r.get('ts') or '（无时间）', line[:40])
                hay = (txt + (r.get('body') or '') + (r.get('cite') or '')).lower()
                if kw and kw not in hay:
                    continue
                it = QListWidgetItem(txt)
                it.setData(Qt.ItemDataRole.UserRole, i)
                lst.addItem(it)
            lst.blockSignals(False)
            if lst.count() and keep >= 0:
                lst.setCurrentRow(row_of(keep, lst))
            elif lst.count():
                lst.setCurrentRow(0)
            else:
                ed_body.setPlainText('')
                ed_cite.setPlainText('')
                state['cur'] = -1

        def load_cur(*_):
            it = lst.currentItem()
            if not it:
                return
            i = int(it.data(Qt.ItemDataRole.UserRole))
            if not (0 <= i < len(state['recs'])):
                return
            # batch22 修数据丢失：切换条目前，先把上一条编辑框里的改动收起来，
            # 否则在 A 条改完直接点 B 条，A 的改动会被覆盖掉
            _prev = state['cur']
            if 0 <= _prev < len(state['recs']) and _prev != i:
                _p0 = state['recs'][_prev]
                _nb = ed_body.toPlainText().strip('\n')
                _nc = ed_cite.toPlainText().strip('\n')
                if _nb != (_p0.get('body') or '') or _nc != (_p0.get('cite') or ''):
                    _p0['body'] = _nb
                    _p0['cite'] = _nc
                    state['dirty'] = True
            state['cur'] = i
            rec = state['recs'][i]
            ed_body.setPlainText(rec.get('body') or '')
            ed_cite.setPlainText(rec.get('cite') or '')
            # batch22：标准引文 + 参考页码 + 打开出处可用性
            ed_citestd.setText(rec.get('cite_std') or '')
            pg = str(rec.get('page') or '').strip()
            if pg:
                lb_page.setText('参考页码：第 %s 页（读自文件，可能与书上的页码不一致）' % pg)
            else:
                lb_page.setText('参考页码：—（这条没记页码）')
            _src = str(rec.get('src') or '').strip()
            _hint = str(rec.get('file_hint') or '').strip()
            if _src and os.path.isfile(_src):
                b_opensrc.setEnabled(True)
                b_opensrc.setToolTip('打开：%s%s'
                                     % (_src, ('，跳到第 %s 页' % pg) if pg else ''))
            elif _hint or _src:
                b_opensrc.setEnabled(True)
                b_opensrc.setToolTip('按文件名「%s」在索引库里找并打开%s'
                                     % (_hint or _src, ('，跳到第 %s 页' % pg) if pg else ''))
            else:
                b_opensrc.setEnabled(False)
                b_opensrc.setToolTip('这条摘录没有记录出处文件')

        def push_cur():
            i = state['cur']
            if i < 0:
                return False
            rec = state['recs'][i]
            nb = ed_body.toPlainText().strip('\n')
            nc = ed_cite.toPlainText().strip('\n')
            # 出处首行规范成「—— …」：解析时靠这个特征把正文和出处分开，
            # 否则「只填出处、不填正文」的条目会被当成正文（内容不丢但归错类）
            if nc:
                _first = nc.splitlines()[0].strip()
                if _first and not _first.startswith(('——', '—', '《', '文件')):
                    nc = '—— ' + nc
            if nb != (rec.get('body') or '') or nc != (rec.get('cite') or ''):
                state['dirty'] = True
            rec['body'] = nb
            rec['cite'] = nc
            return True

        def do_save_cur():
            if push_cur():
                refresh_list(state['cur'])
                self.statusBar().showMessage('已更新本条（记得「保存全部」写回文件）')

        def _rec_key(r):
            """摘录的身份键 —— **只能用时间戳**。

            正文不能进键：用户改过正文的条目，键一变就会被当成"磁盘上另有一条"，
            合并时会插出一条重复的。时间戳为空时才退回正文开头。
            """
            ts = (r.get('ts') or '').strip()
            return ts or ('~' + (r.get('body') or '').strip()[:40])

        def _merge_disk_new():
            """把磁盘上「这边还没见过」的条目并进来。

            为什么需要：有未保存改动时自动刷新会被跳过，内存里的列表就比文件少
            几条新摘的；这时若直接整体重写文件，会把那几条**抹掉**。所以保存前
            先补进来（新增的排在最前，跟「新的在前」一致）。

            用计数比较，同一时间戳出现多条时也不会漏（少合并 = 丢数据，宁可多留）。
            """
            from collections import Counter
            try:
                disk = TOOLS.read_excerpts()
            except Exception:
                return 0
            mem_cnt = Counter(_rec_key(r) for r in state['recs'])
            seen, extra = Counter(), []
            for r in disk:
                k = _rec_key(r)
                seen[k] += 1
                if seen[k] > mem_cnt.get(k, 0):
                    extra.append(r)
            if extra:
                state['recs'] = extra + list(state['recs'])
            return len(extra)

        def do_save_all():
            push_cur()
            try:
                n_new = _merge_disk_new()
                p = TOOLS.write_excerpts(state['recs'])
                state['dirty'] = False
                state['cur'] = -1
                refresh_list()                   # 列表标题跟着更新（保存前是旧的首行）
                _m = '已保存摘录本：%s（共 %d 条）' % (os.path.basename(p),
                                                      len(state['recs']))
                if n_new:
                    _m += '；期间新摘的 %d 条也一并保留' % n_new
                self.statusBar().showMessage(_m)
            except Exception as e:
                self.statusBar().showMessage('保存失败：%s' % e)

        def do_new():
            push_cur()
            import time as _t
            state['recs'].insert(0, {'ts': _t.strftime('%Y-%m-%d %H:%M:%S'),
                                     'body': '', 'cite': '', 'src': '', 'page': '',
                                     'cite_std': '', 'file_hint': ''})
            state['dirty'] = True
            refresh_list(0)
            lst.setCurrentRow(0)
            ed_body.setFocus()

        def do_del():
            it = lst.currentItem()
            if not it:
                return
            i = int(it.data(Qt.ItemDataRole.UserRole))
            if QMessageBox.question(dlg, '删除摘录',
                                    '确定删除这一条摘录？') != QMessageBox.StandardButton.Yes:
                return
            if not (0 <= i < len(state['recs'])):
                return
            state['recs'].pop(i)
            state['cur'] = -1
            state['dirty'] = True
            refresh_list(min(i, len(state['recs']) - 1) if state['recs'] else -1)

        def do_move(d):
            it = lst.currentItem()
            if not it:
                return
            i = int(it.data(Qt.ItemDataRole.UserRole))
            j = i + d
            if not (0 <= j < len(state['recs'])):
                return
            push_cur()
            state['recs'][i], state['recs'][j] = state['recs'][j], state['recs'][i]
            state['dirty'] = True
            refresh_list(j)

        def do_reload(force=False):
            """重新从文件读。有未保存改动时先问一句，别让人一点就丢了。"""
            if not force:
                try:
                    push_cur()
                    if state.get('dirty'):
                        _ans = QMessageBox.question(
                            dlg, '重新载入摘录本',
                            '这边有改动还没保存，重新载入会把它们丢掉。确定继续吗？',
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                            QMessageBox.StandardButton.No)
                        if _ans != QMessageBox.StandardButton.Yes:
                            return
                except Exception:
                    pass
            try:
                state['recs'] = TOOLS.read_excerpts()
            except Exception:
                state['recs'] = []
            state['cur'] = -1
            state['dirty'] = False
            refresh_list()

        def _auto_reload():
            """主窗口新摘了一条 → 自动刷新列表。

            但如果这边还有未保存的改动，**不要**去覆盖编辑框（否则用户正在
            改的字就没了）；只提示一声，让他自己点「重新载入」。
            """
            try:
                push_cur()
                if state.get('dirty'):
                    self.statusBar().showMessage(
                        '摘录本已添加新内容 —— 你这边有未保存的改动，'
                        '先「保存全部」或点「重新载入」再看新条目')
                    return
                do_reload()
            except Exception:
                pass

        # ---- batch22：复制引文 / 打开出处 / 复制正文（可选带出处）
        def do_copy_cite():
            i = state['cur']
            if i < 0:
                return
            push_cur()
            s = str(state['recs'][i].get('cite_std') or '').strip()
            _fallback = False
            if not s:            # 老记录没有标准引文 → 用原出处文字兜底，别让按钮没用
                s = str(state['recs'][i].get('cite') or '').strip()
                _fallback = True
            if not s:
                self.statusBar().showMessage('这条摘录没有出处文字，没法生成引文')
                return
            try:
                QApplication.clipboard().setText(s)
            except Exception:
                return
            self.statusBar().showMessage(
                ('已复制出处文字（这条是老记录，没有标准引文格式）：%s' if _fallback
                 else '已复制引文：%s') % s.replace('\n', ' '))

        def do_open_src():
            i = state['cur']
            if i < 0:
                return
            push_cur()
            rec = state['recs'][i]
            src = str(rec.get('src') or '').strip()
            pg = str(rec.get('page') or '').strip()
            _fn = str(rec.get('fn') or os.path.basename(src or ''))
            if src and os.path.isfile(src):
                self._open_and_goto(src, pg)
                return
            # batch26：绝对路径失效时的多级回捞（换盘 / 换库 / 改名都能找回）
            p2 = self._find_file_by_hint(
                src, fn=_fn, size=str(rec.get('size') or ''),
                npages=str(rec.get('npages') or ''))
            if not p2:
                p2 = self._find_file_by_hint(
                    os.path.basename(src) if src else '',
                    fn=str(rec.get('file_hint') or ''))
            if p2:
                self._open_and_goto(p2, pg)
                return
            self._ask_missing_source(
                src or str(rec.get('file_hint') or ''), want=_fn,
                page=pg, where='摘录')

        def do_copy_body():
            i = state['cur']
            if i < 0:
                return
            push_cur()
            rec = state['recs'][i]
            body = str(rec.get('body') or '').strip()
            if not body:
                self.statusBar().showMessage('这条摘录正文是空的')
                return
            src_txt = (str(rec.get('cite_std') or '').strip()
                       or str(rec.get('cite') or '').strip())
            with_src = False
            if src_txt:
                ans = QMessageBox.question(
                    dlg, '复制摘录',
                    '要把出处一起复制吗？\n\n正文 %d 字\n出处：%s'
                    % (len(body), src_txt.replace('\n', ' ')[:70]),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Yes)
                if ans == QMessageBox.StandardButton.Cancel:
                    return
                with_src = (ans == QMessageBox.StandardButton.Yes)
            # batch24：正文里出现「韵目字 + 电」（如 有电 / 艳电）→ 文末自动注明代日
            _body2, _rh = RHYME.annotate(body)
            if _rh:
                body = _body2
            try:
                QApplication.clipboard().setText(
                    body + ('\n\n' + src_txt if with_src else ''))
            except Exception:
                return
            self.statusBar().showMessage(
                '已复制摘录正文%s%s'
                % ('（含出处）' if with_src else '',
                   ('；代日韵目 %d 处：%s' % (
                       len(_rh), '、'.join('%s=%d日' % (h['char'], h['day']) for h in _rh)))
                   if _rh else ''))

        def _confirm_close():
            """有改动还没写回文件就问一句；返回 True 表示可以关。"""
            try:
                push_cur()          # 先把编辑框内容收起来（据此判断是否有改动）
                if not state.get('dirty'):
                    return True
                ans = QMessageBox.question(
                    dlg, '摘录本有改动',
                    '摘录本有改动还没写回文件，要保存吗？',
                    QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                    | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Save)
                if ans == QMessageBox.StandardButton.Cancel:
                    return False
                if ans == QMessageBox.StandardButton.Save:
                    do_save_all()
                    return not state.get('dirty')
                state['dirty'] = False          # 放弃改动
                return True
            except Exception:
                return True

        def do_close():
            if _confirm_close():
                try:
                    self._exc_dlg = None
                    self._exc_reload = None
                    self._snap_reload = None
                except Exception:
                    pass
                dlg.close()

        def _close_event(ev):
            if _confirm_close():
                try:
                    self._exc_dlg = None
                    self._exc_reload = None
                    self._snap_reload = None
                except Exception:
                    pass
                ev.accept()
            else:
                ev.ignore()

        dlg.closeEvent = _close_event

        lst.currentItemChanged.connect(load_cur)
        self._exc_filter.textChanged.connect(lambda *_: refresh_list(state['cur']))
        b_new.clicked.connect(do_new)
        b_del.clicked.connect(do_del)
        b_up.clicked.connect(lambda: do_move(-1))
        b_dn.clicked.connect(lambda: do_move(1))
        b_save.clicked.connect(do_save_cur)
        b_save_all.clicked.connect(do_save_all)
        b_reload.clicked.connect(do_reload)
        b_open.clicked.connect(lambda: open_any(TOOLS.excerpt_target_path()))
        b_close.clicked.connect(do_close)
        b_copycite.clicked.connect(do_copy_cite)
        b_opensrc.clicked.connect(do_open_src)
        b_copybody.clicked.connect(do_copy_body)
        def _snap_open_viewer():
            r = _snap_cur()
            name = (r or {}).get('img') or ''
            p = snap_state['paths'].get(name) or ''
            if p:
                open_any(p)
            elif name:
                open_any(os.path.join(os.path.dirname(TOOLS.snapshot_target()), name))

        slist.currentItemChanged.connect(show_snap)
        b_srefresh.clicked.connect(refresh_snaps)
        b_scite.clicked.connect(lambda: _snap_copy('cite'))
        b_snote.clicked.connect(lambda: _snap_copy('note'))
        b_ssrc.clicked.connect(_snap_open_src)
        b_sopen.clicked.connect(_snap_open_viewer)
        b_sdir.clicked.connect(lambda: open_any(TOOLS.record_dir(TOOLS._SNAPSHOT_DIR)))
        refresh_snaps()
        do_reload()
        self._last_excerpt_dlg = {'dlg': dlg, 'list': lst, 'body': ed_body,
                                  'cite': ed_cite, 'tabs': tabs, 'recs': state,
                                  'slist': slist, 'sdetail': sdetail,
                                  'snaps': snap_state}
        self._snap_reload = refresh_snaps
        try:
            dlg.setModal(False)            # batch22：不锁前台
        except Exception:
            pass
        self._exc_dlg = dlg
        self._exc_reload = _auto_reload
        dlg.show()

    # ---- ⑤ 注释一键插入（脚注，❞ / Ctrl+Shift+I）
    def footnote_here(self):
        """把引文（当前选区 / 剪贴板）做成带出处的「脚注」；粘到 Word 即成真脚注。"""
        body = self._current_selection()
        if not (body or '').strip():
            try:
                body = QApplication.clipboard().text() or ''
            except Exception:
                body = ''
        meta = self._record_meta()
        rd = getattr(self, '_reader', '')
        if rd in ('pdf', 'dual') and getattr(self, 'pd', None) is not None:
            page = self._pdf_page_1based()   # v0.3.12：双页时认焦点页
        else:
            page = 'X'
        try:
            src = META.cite(meta, page=page)
        except Exception:
            src = ''
        if not src:
            src = self._path()
        try:
            src, _ = CHRONO.annotate(src)
        except Exception:
            pass
        try:
            from PyQt6.QtCore import QMimeData
            rtf = TOOLS.footnote_rtf(body, src).encode('ascii', 'replace')
            md = QMimeData()
            md.setData('text/rtf', bytes(rtf))
            md.setHtml(TOOLS.footnote_html(body, src))
            md.setText(('%s  %s' % (body or '', src or '')).strip())
            QApplication.clipboard().setMimeData(md)
        except Exception as e:
            self.statusBar().showMessage('生成脚注失败：%s' % e)
            return
        self._say('已生成脚注（正文 %d 字 ｜ 脚注「%s」）——到 Word 里 Ctrl+V 即成脚注'
                  % (len(body or ''), (src or '')[:24]), hold=3.5)

    # ---- ③ PDF + TXT 对读（⇄）
    def toggle_dual(self):
        """开/关 PDF 与 TXT 的对照阅读（按页码同步）。"""
        if getattr(self, '_reader', '') == 'dual':
            # 【v0.3.16】关掉对读 = 回到 PDF。以前一律退回 TXT，可绝大多数时候
            # 用户本来在看 PDF，对读只是临时拿来对一下文字 —— 一关就把人从图上
            # 甩到文本上，方向是反的。明确点了「只看 TXT」的才留在 TXT（那条走
            # _dual_single('text')，不走这里）。
            self._dual_single('pdf' if getattr(self, 'pd', None) is not None
                              else 'text')
            return
        # 【v0.3.16】以**正在读的那本**为准，而不是左栏选中的那一行：
        # 大文件夹里列表常常选错（和 v0.3.11「打开所在文件夹」同一个毛病），
        # 结果对着另一本书找同伴、报「没有同名 TXT」。
        p = self._path() or ''
        if not p or not os.path.isfile(p):
            r = self._cur()
            p = os.path.join(r.get('dir') or '', r.get('name') or '') if r else ''
        if not p or not os.path.isfile(p):
            self.statusBar().showMessage('先在列表里选一本书')
            return
        ext = os.path.splitext(p)[1].lower()
        if ext in ('.txt', '.text'):
            if not self._enter_dual(p):
                self.statusBar().showMessage('这本书没有同名 PDF，无法对读')
        elif ext == '.pdf':
            txts = []
            if hasattr(META, '_sibling_texts'):
                try:
                    txts = [x for x in META._sibling_texts(p) if os.path.isfile(x)]
                except Exception:
                    txts = []
            if txts:
                self._enter_dual(txts[0], p)
            else:
                self.statusBar().showMessage('这本 PDF 没有同名 TXT，无法对读')
        else:
            self.statusBar().showMessage('对读只支持 PDF / TXT')

    def _maybe_auto_dual(self, txt_path):
        """打开 TXT 时：若有同名 PDF，则自动进入对读（可在设置里关）。"""
        try:
            if getattr(self, '_suppress_dual', False):
                return False
            if not (txt_path or '').lower().endswith(('.txt', '.text')):
                return False
            if not self.st.get('auto_dual', True):
                return False
            try:                                  # batch16：过大 TXT 不自动进对读（会很卡 / 且只能截断）
                if os.path.getsize(txt_path) > DUAL_TXT_MAX:
                    self.statusBar().showMessage(
                        '这个 TXT 较大（%.0f MB）：未自动进入对读'
                        '（对读只载入前 %d MB）；可点「⇄ 对读」手动打开'
                        % (os.path.getsize(txt_path) / 1048576.0, DUAL_TXT_MAX // 1048576))
                    return False
            except OSError:
                pass
            pdfs = []
            if hasattr(META, '_sibling_pdfs'):
                pdfs = [x for x in META._sibling_pdfs(txt_path) if x.lower().endswith('.pdf')]
            if not pdfs:
                return False
            return self._enter_dual(txt_path, pdfs[0])
        except Exception:
            return False

    def _dual_txt_variants(self, txt_path, pdf_path=''):
        """同一本书可用的 TXT 版本：[(标签, 路径)]（含繁简 / 不同 OCR 版本）。"""
        items, seen = [], set()

        def add(p):
            if not p or not os.path.isfile(p):
                return
            k = os.path.normcase(os.path.abspath(p))
            if k in seen:
                return
            seen.add(k)
            base = os.path.basename(p)
            items.append(('当前：%s' % base if p == txt_path else base, p))

        add(txt_path)
        try:
            for fn in (META._sibling_texts(txt_path) if hasattr(META, '_sibling_texts') else []):
                add(fn)
        except Exception:
            pass
        if pdf_path:
            try:
                d = os.path.dirname(os.path.abspath(pdf_path))
                key = META.family_key(os.path.basename(pdf_path))
                for fn in os.listdir(d):
                    if fn.lower().endswith(('.txt', '.text')) and META.family_key(fn) == key:
                        add(os.path.join(d, fn))
            except OSError:
                pass
        return items

    def _connect_dual(self):
        if getattr(self, '_dual_wired', False):
            return
        try:
            self.dual.txtChanged.connect(self._on_dual_txt_changed)
            self.dual.exitRequested.connect(self._dual_single)
        except Exception:
            pass
        self._dual_wired = True

    def _on_dual_txt_changed(self, path):
        self._text_path = path
        self.statusBar().showMessage('已切换对读 TXT：%s' % os.path.basename(path))

    def _dual_single(self, which):
        """退出对读，只留 PDF 或只留 TXT（保持当前位置）。"""
        try:
            page = int(self.dual.current_page()) + 1
        except Exception:
            page = int(getattr(self, 'pgno', 0)) + 1
        if which == 'pdf':
            if getattr(self, 'pd', None) is None:
                self.statusBar().showMessage('没有 PDF 可单独打开')
                return
            self.pgno = max(0, page - 1)
            self._pdf_show()
            self._say('已退出对读，只显示 PDF（第 %d 页）' % page, hold=2.5)
            return
        tp = getattr(self, '_text_path', '')
        self._set_reader('text')
        if tp and os.path.isfile(tp):
            self._suppress_dual = True
            try:
                self._show_text_file(tp)
            finally:
                self._suppress_dual = False
            self._say('已退出对读，只显示 TXT', hold=2.5)
        else:
            self.stack.setCurrentWidget(self.text_view)
            self._say('已退出对读', hold=2.5)

    def _enter_dual(self, txt_path, pdf_path=None):
        try:
            import fitz
            if not pdf_path and hasattr(META, '_sibling_pdfs'):
                cands = [x for x in META._sibling_pdfs(txt_path) if x.lower().endswith('.pdf')]
                pdf_path = cands[0] if cands else ''
            if not pdf_path or not os.path.isfile(pdf_path):
                return False
            d = fitz.open(pdf_path)
            if int(d.page_count) <= 0:
                return False
            # batch14：进入对读时 PDF 停在第几页——若当前读的就是这本 PDF，就保持在原页
            first = 1
            same = (getattr(self, 'pd', None) is not None
                    and getattr(self, '_pd_path', '') == pdf_path)
            if same:
                try:
                    first = int(getattr(self, 'pgno', 0)) + 1
                except Exception:
                    first = 1
            first = max(1, min(int(d.page_count), first))
            self._connect_dual()
            self._set_pdf(d, pdf_path)
            self.pgno = first - 1
            fit = getattr(self, '_zoom_mode', 'fitp')
            if fit not in ('fitw', 'fitp', '100'):
                fit = 'fitp'
            self.dual.load(d, txt_path, first=first, fit=fit)
            self.dual.set_txt_list(self._dual_txt_variants(txt_path, pdf_path))
            self._text_path = txt_path
            self._set_reader('dual')
            self._expand_reader()             # batch22：进对读 → 把阅读区放回来
            self.stack.setCurrentWidget(self.dual)
            self._hide_nav()
            self._sync_page_box()
            self._upd_status_pdf()
            self._stat_open(txt_path)
            self._say('已进入 PDF + TXT 对读（左右并列），PDF 停在第 %d 页、TXT 已同步' % first,
                      hold=3.0)
            return True
        except Exception as e:
            self.statusBar().showMessage('对读打开失败：%s' % e)
            return False

    def _on_dual_page(self, i):
        if getattr(self, 'pd', None) is None:
            return
        try:
            self.pgno = max(0, min(int(self.pd.page_count) - 1, int(i)))
        except Exception:
            self.pgno = 0
        self._sync_page_box()
        self._upd_status_pdf()

    # ============================================================ batch3
    # ① Markdown 渲染 / 源码切换（Ctrl+M / 𝐌D 渲染）
    def _read_text_file(self, p, limit=0):
        """只读文本文件：limit=0 读全文（大文件走 mmap，避免额外缓冲）；limit>0 只读前 limit 字节。
        编码：chardet 优先，失败再依次试 utf-8-sig / utf-8 / gb18030 / big5。"""
        try:
            return self._decode_text(self._read_bytes(p, limit))
        except Exception:
            return ''

    def _read_bytes(self, p, limit=0):
        """只读原始字节：limit=0 时大文件用 mmap 映射后整块取出；limit>0 只读前 N 字节。"""
        try:
            size = os.path.getsize(p)
        except OSError:
            size = 0
        if limit and size > limit:
            with open(p, 'rb') as f:
                return f.read(limit)
        if size >= 4 * 1024 * 1024:
            import mmap
            with open(p, 'rb') as f:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    return bytes(mm)
        with open(p, 'rb') as f:
            return f.read()

    def _decode_text(self, b):
        """把字节解码为文本；chardet 优先，回退常见中文编码。"""
        if not b:
            return ''
        enc = ''
        try:
            import chardet
            enc = (chardet.detect(b[:262144]).get('encoding') or '').lower()
        except Exception:
            enc = ''
        if enc in ('gb2312', 'gbk', 'gb18030'):
            enc = 'gb18030'
        cands = []
        for e in ([enc] if enc else []) + ['utf-8-sig', 'utf-8', 'gb18030', 'big5']:
            if e and e not in cands:
                cands.append(e)
        for e in cands:
            try:
                return b.decode(e)
            except (UnicodeDecodeError, LookupError):
                continue
        return b.decode('utf-8', 'replace')

    def _show_text_file(self, p):
        """把文本文件读进阅读区（纯文本态）；同时复位 MD 状态。"""
        try:
            _sz = os.path.getsize(p)
        except OSError:
            _sz = 0
        _capped = _sz > TEXT_DISPLAY_MAX
        txt = self._read_text_file(p, TEXT_DISPLAY_MAX if _capped else 0)
        self._pdf_stop()
        # 【v0.3.22】打开 TXT / MD 时右栏也要展开，并直接切到「查找」页签 ——
        # 以前这里是无条件 `_hide_nav()`，于是 TXT 那一份命中结果只能挤在阅读区
        # 顶部那条窄栏里，右栏要么空着、要么还挂着上一本书的。
        self._show_nav(2)
        try:
            # TXT 没有大纲也没有页面缩略图：别留着上一本 PDF 的条目在那儿误导人
            toc = getattr(self, '_nav_toc', None)
            if toc is not None:
                toc.clear()
            tl = getattr(self, '_thumb_list', None)
            if tl is not None:
                tl.clear()
        except Exception:
            pass
        self._expand_reader()                 # batch22：打开文本 → 把阅读区放回来
        self.stack.setCurrentWidget(self.text_view)
        self.view.setPlainText(txt)
        self.pd = None
        self._pd_path = ''
        self._text_path = p
        self._set_reader('text')
        # batch10：不再清空查找结果状态（否则跳转到其它命中文件后列表/计数就废了）
        try:
            self.lb_pg_total.setText('/ 0')
            self.ed_page.setText('0')
        except Exception:
            pass
        self._md_path = ''
        self._md_render = False
        self._md_src = ''
        self._apply_line_height()
        self._stat_open(p)
        if _capped:
            self._say('文件很大（%.0f MB）：为不卡界面，只载入前 %d MB（不静默丢弃）'
                      % (_sz / 1048576.0, TEXT_DISPLAY_MAX // 1048576))
        return True

    def md_toggle(self):
        """Ctrl+M / 𝐌D 渲染：.md 文件在「渲染态 / 源码态」间切换。"""
        r = self._cur()
        p = os.path.join(r.get('dir') or '', r.get('name') or '') if r else ''
        if not p or not p.lower().endswith('.md'):
            self.statusBar().showMessage('这个文件不是 Markdown')
            return
        try:
            self.stack.setCurrentWidget(self.text_view)
            if self._md_path != p:
                # 首次：从磁盘读源码，先以源码态呈现
                try:
                    _sz = os.path.getsize(p)
                except OSError:
                    _sz = 0
                self._md_src = self._read_text_file(
                    p, TEXT_MAX_BYTES if _sz > TEXT_MAX_BYTES else 0)
                self._md_path = p
                self._md_render = False
                self.pd = None
                self._pd_path = ''
                self._text_path = p
                self._set_reader('text')
                self.view.setPlainText(self._md_src)
                self._apply_line_height()
                self._stat_open(p)
            self._md_render = not self._md_render
            if self._md_render:
                self.view.document().setMarkdown(self._md_src or '')
                st = '渲染'
            else:
                self.view.setPlainText(self._md_src or '')
                self._apply_line_height()
                st = '源码'
            self.statusBar().showMessage('Markdown %s态（Ctrl+M 切换）' % st)
        except Exception as e:
            self.statusBar().showMessage('Markdown 切换失败：%s' % e)

    # ② JSON 树形浏览（Ctrl+J）
    def _json_load(self, p):
        """读 + 解析 JSON。返回 (data, err)：err='' 成功；'too_big' 超限；否则异常串。"""
        try:
            if os.path.getsize(p) > JSON_MAX_BYTES:
                return None, 'too_big'
            with open(p, 'rb') as f:
                raw = f.read()
            return json.loads(raw.decode('utf-8-sig')), ''
        except Exception as e:
            return None, str(e)

    def _json_load_stream(self, p, max_items=5000):
        """大 JSON 流式：用 ijson 只读顶层结构（不整份载入）。
        返回 (tree, tag)：tree=None 表示失败（tag 为 'no_ijson' 或异常串）。"""
        try:
            import ijson
        except Exception:
            return None, 'no_ijson'
        tw = QTreeWidget()
        tw.setHeaderLabels(['键 / 索引', '说明'])
        tw.setColumnWidth(0, 280)
        top = ''
        cnt = 0
        try:
            with open(p, 'rb') as f:
                for prefix, event, value in ijson.parse(f):
                    if prefix == '' and event == 'start_map':
                        top = 'map'
                    elif prefix == '' and event == 'start_array':
                        top = 'array'
                    elif top == 'map' and prefix == '' and event == 'map_key':
                        QTreeWidgetItem(tw, [str(value), '顶层键'])
                        cnt += 1
                    elif top == 'array' and prefix == 'item' and event in (
                            'start_map', 'start_array', 'number', 'string', 'boolean', 'null'):
                        QTreeWidgetItem(tw, ['[%d]' % cnt, '元素'])
                        cnt += 1
                    if cnt >= max_items:
                        break
        except Exception as e:
            return None, str(e)
        tw.expandToDepth(0)
        return tw, ('array' if top == 'array' else 'map')

    def _json_show_dialog(self, p, tree, note=''):
        """弹出 JSON 树对话框（可过滤）。"""
        dlg = QDialog(self)
        title = 'JSON 树 — %s' % os.path.basename(p)
        if note:
            title += '｜' + note
        dlg.setWindowTitle(title)
        dlg.resize(760, 620)
        vb = QVBoxLayout(dlg)
        ed = QLineEdit()
        ed.setPlaceholderText('按 key 过滤（输入即筛）')
        vb.addWidget(ed)
        vb.addWidget(tree, 1)
        ed.textChanged.connect(lambda s: self._json_filter(tree, s))
        bb = QPushButton('关闭')
        bb.clicked.connect(dlg.accept)
        vb.addWidget(bb)
        self._show_util_dialog(dlg, '_dlg_json')

    def _json_build_tree(self, data):
        """把 JSON 数据建成 QTreeWidget。dict/list 可展开；值显示类型与内容。"""
        tw = QTreeWidget()
        tw.setHeaderLabels(['键', '值 / 类型'])
        tw.setColumnWidth(0, 240)

        def add(parent, key, val):
            if isinstance(val, dict):
                it = QTreeWidgetItem(parent, [str(key), 'object(%d)' % len(val)])
                for k, v in val.items():
                    add(it, k, v)
            elif isinstance(val, list):
                it = QTreeWidgetItem(parent, [str(key), 'array(%d)' % len(val)])
                for i, v in enumerate(val):
                    add(it, '[%d]' % i, v)
            else:
                QTreeWidgetItem(parent, [str(key), _json_val_str(val)])

        if isinstance(data, dict):
            for k, v in data.items():
                add(tw, k, v)
        elif isinstance(data, list):
            add(tw, '(root array)', data)
        else:
            QTreeWidgetItem(tw, ['(root)', _json_val_str(data)])
        tw.expandToDepth(0)
        return tw

    def _json_filter(self, tree, txt):
        """按 key 过滤：命中项及其祖先显示，否则隐藏。返回可见顶层数。"""
        txt = (txt or '').strip().lower()

        def walk(it):
            hit = (not txt) or (txt in it.text(0).lower())
            child_hit = False
            for i in range(it.childCount()):
                if walk(it.child(i)):
                    child_hit = True
            show = hit or child_hit
            it.setHidden(not show)
            return show

        n = 0
        for i in range(tree.topLevelItemCount()):
            if walk(tree.topLevelItem(i)):
                n += 1
        if txt:
            tree.expandAll()
        return n

    def json_tree_dialog(self):
        """Ctrl+J：.json 文件键值树（可展开、可过滤）。大文件/坏文件回退纯文本。"""
        r = self._cur()
        p = os.path.join(r.get('dir') or '', r.get('name') or '') if r else ''
        if not p or not p.lower().endswith('.json'):
            self.statusBar().showMessage('这个文件不是 JSON')
            return
        if not os.path.isfile(p):
            self.statusBar().showMessage('文件不在了：%s' % p)
            return
        data, err = self._json_load(p)
        if err == 'too_big':
            tree, tag = self._json_load_stream(p)
            if tree is not None:
                self._last_json = {'tree': tree, 'path': p, 'top': tree.topLevelItemCount()}
                self._json_show_dialog(p, tree, '大文件·流式（仅顶层）')
                return
            self.statusBar().showMessage('这个 JSON 超过 20 MB，改用纯文本显示')
            try:
                self._show_text_file(p)
            except Exception as e:
                self.statusBar().showMessage('回退纯文本失败：%s' % e)
            return
        if err:
            self.statusBar().showMessage('JSON 解析失败（%s），改用纯文本显示' % err)
            try:
                self._show_text_file(p)
            except Exception as e:
                self.statusBar().showMessage('回退纯文本失败：%s' % e)
            return
        tree = self._json_build_tree(data)
        self._last_json = {'tree': tree, 'path': p, 'top': tree.topLevelItemCount()}
        self._json_show_dialog(p, tree)

    # ③ 相关文件推荐（Ctrl+R）
    def _series_prefix(self, name):
        """书名去除卷册/括注后，取开头连续中文作为「丛书/专题」前缀。"""
        try:
            s = META.book_core(name or '')
        except Exception:
            s = name or ''
        s = re.sub(r'[（(][^（）()]*[)）]', '', s)          # 去所有括注
        s = re.sub(r'第\s*[0-9一二三四五六七八九十百千]{1,4}\s*[册卷集部编篇辑期]', '', s)
        s = re.sub(r'全\s*[0-9一二三四五六七八九十]{1,3}\s*册', '', s)
        s = re.sub(r'[\s_\-—+·、.]+', ' ', s).strip(' _-—+·、.')
        m = re.match(r'[\u4e00-\u9fa5]{2,}', s)
        return (m.group(0) if m else s).strip()

    def related_groups(self):
        """相关文献推荐。返回多档：
        A=同丛书/同专题(兼容) B=同作者(兼容) C=同目录(兼容)；
        另加 batch11 四档细分：series / topic / same_book / same_author。
        """
        out = {'A': [], 'B': [], 'C': [], 'prefix': '', 'author': '', 'db': self.db,
               'series': [], 'topic': [], 'same_book': [], 'same_author': []}
        r = self._cur()
        if not r:
            return out
        p = os.path.join(r.get('dir') or '', r.get('name') or '')
        folder = r.get('dir') or ''
        try:
            cm = META.parse(r.get('name') or '', p, deep=False)
        except Exception:
            cm = {}
        prefix = self._series_prefix(cm.get('name') or r.get('name') or '')
        author = cm.get('author') or ''
        out['prefix'], out['author'] = prefix, author
        folder_base = os.path.basename(os.path.normpath(folder)) if folder else ''

        cands = {}
        # 相邻书架：直接读当前目录（最快，不查库）
        try:
            for fn in sorted(os.listdir(folder)):
                fp = os.path.join(folder, fn)
                if os.path.isfile(fp) and fp != p:
                    cands[fp] = {'name': fn, 'dir': folder, 'path': fp,
                                 'ext': os.path.splitext(fn)[1].lower()}
        except OSError:
            pass
        # 同丛书 / 同作者：search 关键词（限制 200 条候选）
        for kw in (prefix, author):
            if not kw:
                continue
            try:
                for rr in C.search(self.db, kw, 200):
                    fp = os.path.join(rr.get('dir') or '', rr.get('name') or '')
                    if fp and fp != p and fp not in cands:
                        cands[fp] = rr
            except Exception:
                pass
        cands = dict(list(cands.items())[:200])

        cand_list = []
        for fp, c in cands.items():
            nm = c.get('name') or os.path.basename(fp)
            d = c.get('dir') or os.path.dirname(fp)
            try:
                xm = META.parse(nm, fp, deep=False)
            except Exception:
                xm = {}
            xp = self._series_prefix(xm.get('name') or nm)
            same_folder = bool(folder_base) and \
                os.path.basename(os.path.normpath(d)) == folder_base
            item = {'name': nm, 'dir': d, 'path': fp}
            if (prefix and xp and xp == prefix) or same_folder:
                out['A'].append(item)
            if author and (xm.get('author') or '') == author:
                out['B'].append(item)
            if os.path.normpath(d) == os.path.normpath(folder):
                out['C'].append(item)
            cand_list.append({'name': nm, 'dir': d, 'path': fp,
                              'author': (xm.get('author') or '')})
        out['C'] = out['C'][:50]
        # batch11：四档细分（同丛书 / 同专题 / 同一本书的其他书 / 同一作者的其他著作）
        cur = {'name': cm.get('name') or r.get('name') or '', 'dir': folder,
               'path': p, 'author': author}
        try:
            out.update(TOOLS.classify_related(cur, cand_list))
        except Exception:
            pass
        return out

    # ------------------------------------------------ batch26：文档句柄管理
    def _set_pdf(self, d, path=''):
        """登记当前 PDF 文档 —— 顺手把上一份关掉。"""
        old = getattr(self, 'pd', None)
        try:
            if old is not None and old is not d:
                self._release_pdf_doc(old)
        except Exception:
            pass
        self.pd = d
        if path:
            self._pd_path = path
        return d

    def _release_pdf_doc(self, doc):
        """关掉一份 fitz.Document。

        PyMuPDF 的 Document 既占内存又挂着文件句柄，以前每次「打开出处」都
        直接覆盖 self.pd、旧文档从不关闭 —— 连点几次就攒下好几份同一本书，
        于是越点越卡。这里先让还在引用它的视图松手，再 close。
        """
        if doc is None:
            return
        try:
            if getattr(doc, 'is_closed', False):
                return
        except Exception:
            return
        try:
            for _pv in (getattr(self, 'pdf_view', None),
                        getattr(getattr(self, 'dual', None), 'pdf', None)):
                try:
                    if _pv is not None and getattr(_pv, 'doc', None) is doc:
                        _pv.clear()
                except Exception:
                    pass
            doc.close()
        except Exception:
            pass

    def _same_pdf_already(self, path):
        """这本书是不是正开着？开着就别再 fitz.open 一遍重建全部页控件（batch26）。"""
        try:
            cur = getattr(self, '_pd_path', '') or ''
            if not cur or not path:
                return False
            if self._path_key_(cur) != self._path_key_(path):
                return False
            d = getattr(self, 'pd', None)
            if d is None or getattr(d, 'is_closed', False):
                return False
            if int(getattr(d, 'page_count', 0) or 0) <= 0:
                return False
            v = getattr(self, 'pdf_view', None)
            if v is None or getattr(v, 'doc', None) is not d:
                return False
            return True
        except Exception:
            return False

    def _open_fitz(self, path):
        """打开 PDF/EPUB；大文件先给出「正在打开」反馈再解析。

        batch20：fitz.open 冷读一本 150 MB 的扫描版要 0.85~2 s，这段时间界面
        是冻住的，以前没有任何反馈，看起来就像卡死。这里先把状态栏画出来、
        换个忙光标，再去解析。
        """
        import fitz
        try:
            sz = os.path.getsize(path)
        except OSError:
            sz = 0
        busy = sz >= 20 * 1024 * 1024
        if busy:
            self.statusBar().showMessage('正在打开 %s（%.0f MB，请稍候…）'
                                         % (os.path.basename(path), sz / 1048576.0))
            try:
                QApplication.processEvents()          # 让提示先画出来
                QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
            except Exception:
                pass
        try:
            return fitz.open(path)
        finally:
            if busy:
                try:
                    QApplication.restoreOverrideCursor()
                except Exception:
                    pass

    def _pdf_sizes_refined(self, sizes):
        """【v0.3.4】全本页尺寸扫完了：先前只是"猜"的，这里纠正一次。

        版心一致的书猜得准，进来一对发现一模一样就直接返回（零代价）；只有
        真扫出尺寸不一样的页才重排布局——那是少数情况，而且发生在用户已经
        能翻页之后，不影响首屏。

        【v0.3.16】两道护栏，以前都没有：
          · **认人**：连着开两本书时，上一本迟到的尺寸会把新书的版心改掉
            （新书刚好也在等尺寸 → 直接拿旧数据铺上去，滚动条长度、跳页全错）；
          · **认数**：没扫全的尺寸（被掐断 / 中途出错）不能用，用了会把后半本
            的页高搞乱 —— 宁可留着先前的猜测。
        """
        try:
            if self.sender() is not getattr(self, '_pdf_size_worker', None):
                return                      # 不是当前这一本的尺寸，丢掉
        except Exception:
            pass
        w = getattr(self, '_pdf_size_worker', None)
        self._pdf_size_worker = None
        if w is not None:
            try:
                w.wait(2000)
            except Exception:
                pass
        try:
            pv = self._pdf_view_active()
            cur = list(getattr(pv, '_pr', []) or [])
        except Exception:
            return
        if not sizes or not cur:
            return
        try:                                # 没扫全的尺寸宁可不用
            if getattr(pv, 'doc', None) is not None:
                n = int(getattr(pv.doc, 'page_count', 0) or 0)
                if n and len(sizes) != n:
                    return
        except Exception:
            pass
        if len(sizes) == len(cur):
            same = True
            for a, b in zip(sizes, cur):
                if abs(a[0] - b[0]) > 0.5 or abs(a[1] - b[1]) > 0.5:
                    same = False
                    break
            if same:
                return                      # 猜对了，一个像素都不用动
        try:
            pv._pr = list(sizes)
            for i, it in enumerate(getattr(pv, '_items', []) or []):
                if i < len(pv._pr):
                    it.setFixedSize(pv._page_size(i))
            pv._content.adjustSize()
            pv._build_tops()
            pv._render_visible()
        except Exception:
            pass

    def _open_prog(self, kind, frac=1.0, name=''):
        """【v0.3.16】开档进度：动那条线，顺便让状态栏报个百分比。

        kind：`pf` 顺序预读 / `doc` 解析完了 / `page` 扫到第几页 / `done` 收工。
        预读和扫尺寸是**两个并发线程**，各自报各自的；所以这里不能谁报就按谁的
        数往下走（那样进度条会往后缩），而是分别记住每段的进度、再合出一个总量
        —— 各段自己只增不减，合出来的也就一定只增不减。
        """
        st = getattr(self, '_open_prog_state', None)
        if st is None:
            st = self._open_prog_state = {}
        bar = getattr(self, '_openbar', None)
        if kind == 'done':
            if bar is not None:
                try:
                    bar.finish()
                except Exception:
                    pass
            return
        try:
            f = max(0.0, min(1.0, float(frac)))
        except Exception:
            f = 1.0
        if kind == 'pf':
            st['pf'] = max(st.get('pf', 0.0), f)
        elif kind == 'doc':
            st['doc'] = 1.0
        elif kind == 'page':
            st['page'] = max(st.get('page', 0.0), f)
        whole = 0.34 * st.get('pf', 0.0)
        if st.get('doc'):
            whole = max(whole, 0.36 + 0.56 * st.get('page', 0.0))
        if bar is not None:
            bar.to(max(0.02, whole))
            pct = int(min(94, max(2, whole * 100)))
            self.statusBar().showMessage(
                '正在打开 %s … %d%%' % (name or '文件', pct))

    def _open_fitz_bg(self, path):
        """后台打开 PDF（fitz.open + 逐页取尺寸），期间界面保持响应。

        返回 ``(doc, sizes)``；失败抛异常，调用方退回同步的 `_open_fitz`。
        之所以"后台干、同步等"，是为了让 `_open_path` 之后的那一串
        （`_pdf_show` / `_after_pdf_open` / `_hist_add`）顺序不变 ——
        改成真异步的话，Search 传来的 `--words` 会在文档就绪前就跑去扫，
        反而扫不到东西。
        """
        from PyQt6.QtCore import QEventLoop
        try:
            sz = os.path.getsize(path)
        except OSError:
            sz = 0
        # 【v0.3.16】浏览器那样的一条进度：真进度驱动，见 OpenProgress 的注释
        _name = os.path.basename(path)
        try:
            getattr(self, '_openbar').begin()
        except Exception:
            pass
        self._open_prog_state = {}          # 新的一本，进度从零算
        if sz >= 20 * 1024 * 1024:
            self.statusBar().showMessage(
                '正在打开 %s（%.0f MB，请稍候…）' % (_name, sz / 1048576.0))
            try:
                QApplication.processEvents()
                QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
            except Exception:
                pass
        res = {'doc': None, 'sizes': [], 'err': '', 'partial': False}
        # 【v0.3.7】先把整本书顺序读一遍，把文件拽进系统缓存。PDF 解析是满文件
        # 随机跳转，冷读一本 120 MB 的书实测 11 秒；顺序预读只要两三秒，之后
        # fitz 的访问全部命中内存 —— "第一个 PDF 特别慢"主要就是靠这条解决。
        pf = None
        if (sz >= PREFETCH_MIN_MB * 1024 * 1024
                and not _env_flag('CV_NOPREFETCH')):
            pf = PdfPrefetchThread(path, self)
            self._pdf_prefetch = pf
            # 预读是开大书最久的一段（占总时间的三成左右），回报它的字节数
            pf.progress.connect(lambda f: self._open_prog('pf', f, _name))
            pf.start()
        w = PdfOpenWorker(path)
        self._pdf_open_worker = w          # 持住引用，别被 GC 掉

        def _ok(doc, sizes):
            res['doc'] = doc
            res['sizes'] = sizes or []
            try:
                n = int(getattr(doc, 'page_count', 0) or 0)
            except Exception:
                n = 0
            # 只拿到抽样 → 界面先出来，线程还在后面扫剩下的页
            res['partial'] = bool(n and len(res['sizes']) < n)

        def _bad(m):
            res['err'] = m or '打不开'

        w.ready.connect(_ok)
        w.failed.connect(_bad)
        w.refined.connect(self._pdf_sizes_refined)
        # 【v0.3.16】解析完 / 扫到第几页 —— 进度条的后两段
        w.progress.connect(
            lambda kind, done, total: self._open_prog(
                'doc' if kind == 'doc' else 'page',
                1.0 if kind == 'doc'
                else (float(done) / float(total) if total else 1.0),
                _name))
        loop = QEventLoop()
        w.ready.connect(loop.quit)         # 【v0.3.4】抽样一到就往下走
        w.failed.connect(loop.quit)
        w.finished.connect(loop.quit)      # 没走抽样时靠它收尾，别挂死
        try:
            w.start()
            loop.exec()                    # 让窗口继续重绘 / 响应，只是不往下走
            if res['partial']:
                self._pdf_size_worker = w  # 还活着，等 refined 来收
            else:
                w.wait(3000)
        finally:
            self._pdf_open_worker = None
            try:
                self._open_prog('done', 1.0, _name)    # 到顶，缓一下自动收起
            except Exception:
                pass
            if pf is not None:
                # 书已经开出来了，剩下的预读交给后台自己跑完（渲染/翻页还要用缓存）
                if not pf.isRunning():
                    self._pdf_prefetch = None
            if sz >= 20 * 1024 * 1024:
                try:
                    QApplication.restoreOverrideCursor()
                except Exception:
                    pass
        if res['err']:
            raise RuntimeError(res['err'])
        if res['doc'] is None:
            raise RuntimeError('文档打开失败')
        return res['doc'], res['sizes']

    def _real(self, path):
        """索引里记的老路径 → 现在的真实路径。

        书库被挪到别的盘、别的文件夹时，索引里那条老路径就打不开了。
        这里按 CathayHub Launcher 生成的 shared/path_mapping.json 换算一次。
        没有映射文件、或者这条路径没挪过 → 原样返回，等于什么也没做。
        """
        if not path:
            return path
        try:
            import path_translator as pt
            p, rule = pt.translate_path(path)
            if rule:
                self.statusBar().showMessage(
                    '%s：%s' % (pt.translated_label(rule),
                                os.path.basename(p)), 4000)
            return p
        except Exception:
            return path

    def _open_path(self, path, start_page=None):
        """在阅读区打开一个路径（PDF/EPUB 走 fitz；文本走纯文本）。

        start_page: 1 基起始页码。给了就**第一次装载时直接落到那一页**。

        【batch29】以前带 --page 打开是这样的：先 `_open_path` 装到第 1 页，
        再让调用方 `_goto_page`，而 `_goto_page` 里又是 `_pdf_show()` ——
        等于 `set_document` 跑了两次：整本 `_scan_page_sizes` 扫两遍、全部
        页控件建两遍、两次同步 `get_pixmap`、两次 `w.wait(4000)` 停渲染线程。
        这段时间主线程是堵着的，Windows 就把没画完的窗口截成"中间一个方框、
        四周带阴影"，看着像卡死。现在起始页在第一次装载时就给进去。
        """
        path = self._real(path)          # 书库换过位置时，先换算成现在的路径
        if not path or not os.path.isfile(path):
            self.statusBar().showMessage('文件不在了：%s' % path)
            return False
        # batch26：正在打开别的文件时不接受新请求 —— 连点「打开出处」会把
        # fitz.open 排成长队，看起来就是卡死。
        if getattr(self, '_opening', False):
            self.statusBar().showMessage('正在打开上一本，稍候…')
            return False
        ext = os.path.splitext(path)[1].lower()
        if ext in ('.pdf', '.epub', '.xps', '.cbz', '.mobi', '.fb2', '.svg'):
            # batch26：这本书已经开着 → 只把视图切回来，不重新解析、不重建页控件
            if self._same_pdf_already(path):
                try:
                    self._expand_reader()
                    self.stack.setCurrentWidget(self.pdf_view)
                    self._set_reader('pdf')
                    # batch29：这本书开过 ≠ 不用跳页，以前这里漏了 --page
                    if start_page:
                        self._goto_page(start_page)
                    self._sync_page_box()
                    self._upd_status_pdf()
                    self.pdf_view.setFocus()
                except Exception:
                    pass
                return True
            self._opening = True
            try:
                # 【v0.3.3】后台打开（界面不冻）；任何环节出问题都退回原来的同步路子
                try:
                    d, sizes = self._open_fitz_bg(path)
                except Exception:
                    # 【v0.3.16】退回主线程同步开 —— 那是冷读 120 MB 十来秒的活，
                    # 界面会彻底冻住。至少先说一声"很忙"，别让人以为死了。
                    self.statusBar().showMessage(
                        '正在打开 %s（后台开档没走通，改直接打开，'
                        '这一步界面会停一会儿…）' % os.path.basename(path))
                    try:
                        QApplication.processEvents()
                        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
                    except Exception:
                        pass
                    try:
                        d, sizes = self._open_fitz(path), []
                    finally:
                        try:
                            QApplication.restoreOverrideCursor()
                        except Exception:
                            pass
                if d is None:
                    self.statusBar().showMessage('打不开：%s'
                                                 % os.path.basename(path))
                    return False
                if d.page_count <= 0:
                    try:
                        d.close()        # v0.3.16：空文档也是占着的一本，别留着
                    except Exception:
                        pass
                    self.statusBar().showMessage('这个文件没有可显示的页面')
                    return False
                self._set_pdf(d, path)
                self.pgno = (max(0, int(start_page) - 1) if start_page else 0)
                self._pdf_show(pr=sizes)
                self._after_pdf_open()
                self._hist_add()
                self._stat_open(path)
                return True
            except Exception as e:
                self.statusBar().showMessage('打不开：%s' % e)
                return False
            finally:
                self._opening = False
        try:
            self._show_text_file(path)
            self._maybe_auto_dual(path)
            self.statusBar().showMessage('已打开：%s' % os.path.basename(path))
            return True
        except Exception as e:
            self.statusBar().showMessage('打不开：%s' % e)
            return False

    def related_dialog(self):
        """Ctrl+R：相关文件（同丛书/同作者/相邻书架，三档分组，双击打开）。"""
        r = self._cur()
        if not r:
            self.statusBar().showMessage('先在列表里选一本书')
            return
        g = self.related_groups()
        self._last_related = g
        tree = QTreeWidget()
        tree.setHeaderLabels(['相关文件', '所在文件夹'])
        tree.setColumnWidth(0, 300)
        series = g.get('series') or g.get('A') or []
        topic = g.get('topic') or []
        same_book = g.get('same_book') or []
        same_author = g.get('same_author') or g.get('B') or []
        groups = [('同丛书（%d）' % len(series), series),
                  ('同专题（同目录，%d）' % len(topic), topic),
                  ('同一本书的其他书（%d）' % len(same_book), same_book),
                  ('同一作者的其他著作（%d）' % len(same_author), same_author)]
        for title, lst in groups:
            gi = QTreeWidgetItem(tree, [title, ''])
            for it in lst:
                ci = QTreeWidgetItem(gi, [it['name'], it['dir']])
                ci.setData(0, Qt.ItemDataRole.UserRole, it['path'])
            gi.setExpanded(True)
        g['tree'] = tree
        dlg = QDialog(self)
        dlg.setWindowTitle('相关文件 — %s' % (r.get('name') or ''))
        dlg.resize(760, 600)
        vb = QVBoxLayout(dlg)
        vb.addWidget(QLabel('四档推荐：同丛书（前缀「%s」）／ 同专题（同目录）／'
                            '同一本书的其他书（同名）／ 同一作者的其他著作。双击打开。'
                            % (g['prefix'] or '—')))
        vb.addWidget(tree, 1)
        bb = QPushButton('关闭')
        bb.clicked.connect(dlg.accept)
        vb.addWidget(bb)
        g['dialog'] = dlg

        def go(item):
            if item is None:
                return
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if path:
                self._open_path(path)
                dlg.accept()

        tree.itemDoubleClicked.connect(go)
        self._show_util_dialog(dlg, '_dlg_related')

    # ④ 导航面板（目录 / 缩略图 / 查找）—— Ctrl+T · Ctrl+Shift+T 聚焦对应页签
    def _build_nav_dock(self):
        """构建导航面板（右侧 QDockWidget，含 目录 / 缩略图 / 查找 三个页签）。"""
        if getattr(self, '_nav_dock', None) is not None:
            return
        dk = QDockWidget('导航', self)
        dk.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea |
                           Qt.DockWidgetArea.RightDockWidgetArea)
        tabs = QTabWidget()
        toc = QListWidget()
        toc.setUniformItemSizes(True)     # v0.3.15：同为单行文本，省得逐行问高矮
        toc.setWordWrap(False)
        toc.itemDoubleClicked.connect(self._nav_toc_go)
        toc.itemClicked.connect(self._nav_toc_go)     # 单击即跳页（batch7）
        lst = QListWidget()
        lst.setViewMode(QListWidget.ViewMode.IconMode)
        lst.setIconSize(QSize(110, 150))
        lst.setResizeMode(QListWidget.ResizeMode.Adjust)
        lst.setMovement(QListWidget.Movement.Static)
        lst.setSpacing(6)
        lst.itemClicked.connect(self._thumb_clicked)
        fnd = QListWidget()
        # 【v0.3.15】每一行都是单行文本（超长自动省略号），高矮一模一样 —— 让 Qt
        # 别再一路 ask 每个item量的 sizeHint：面板可见时每插入一批就要把几千行
        # 重新问一遍，实测「所有关键词」从 2.20 s 掉到 0.76 s。
        fnd.setUniformItemSizes(True)
        fnd.setWordWrap(False)
        # 【v0.3.23】当前这一处要在列表里"亮"出来。默认选中色在面板失焦时会被
        # Qt 刷成灰的、看着像没选中，所以连 :!active 一起指定。
        fnd.setStyleSheet(
            'QListWidget::item:selected{background:#0B5CAB;color:#FFFFFF;}'
            'QListWidget::item:selected:!active{background:#0B5CAB;color:#FFFFFF;}')
        fnd.itemDoubleClicked.connect(self._nav_find_go)
        fnd.itemClicked.connect(self._nav_find_go)
        fnd_box = QWidget()
        _fv = QVBoxLayout(fnd_box)
        _fv.setContentsMargins(0, 0, 0, 0)
        _fv.setSpacing(2)
        _fv.addWidget(fnd, 1)
        self._btn_find_hidden = QPushButton('显示被去重的项')
        self._btn_find_hidden.setToolTip('被去重（同名且命中数相同的 TXT）默认隐藏；点此展开/收起')
        self._btn_find_hidden.setVisible(False)
        self._btn_find_hidden.clicked.connect(self.toggle_find_hidden)
        _fv.addWidget(self._btn_find_hidden)
        tabs.addTab(toc, '目录')
        tabs.addTab(lst, '缩略图')
        tabs.addTab(fnd_box, '查找')
        # batch10：窗口小的时候页签字不显示不全 —— 不拉伸、缩小内边距/字号、带滚动按钮
        try:
            tabs.setDocumentMode(True)
            tabs.setUsesScrollButtons(True)
            tabs.tabBar().setExpanding(False)
            tabs.tabBar().setElideMode(Qt.TextElideMode.ElideNone)
            _tf = tabs.font()
            _tf.setPointSize(max(7, _tf.pointSize() - 1))
            tabs.setFont(_tf)
            tabs.setStyleSheet('QTabBar::tab{padding:2px 7px;}')
            for _k, _t in enumerate(('目录', '缩略图', '查找')):
                tabs.setTabToolTip(_k, _t)
        except Exception:
            pass
        tabs.currentChanged.connect(self._nav_tab_changed)
        dk.setWidget(tabs)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dk)
        self._nav_dock = dk
        self._nav_tabs = tabs
        self._nav_toc = toc
        self._nav_find = fnd
        self._thumb_dock = dk            # 兼容旧断言/旧叫法
        self._thumb_list = lst
        # 【v0.3.21】记着"用户想不想看这块面板"：切标签时面板要整体搬家，
        # 搬过去之后该不该显示，就照这个来（不然第 2 本一打开就把面板弹出来，
        # 或者用户明明收起了又自己冒出来）。
        self._nav_wanted = False
        try:
            dk.visibilityChanged.connect(self._nav_vis_changed)
        except Exception:
            pass

    def _build_thumb_dock(self):        # 兼容旧名
        self._build_nav_dock()

    def _nav_tab_changed(self, i):
        if i == 1 and getattr(self, 'pd', None) is not None:
            self._start_thumb_load()

    def _show_nav(self, tab=0):
        self._build_nav_dock()
        try:
            self._nav_tabs.setCurrentIndex(int(tab))
        except Exception:
            pass
        if int(tab) == 1:
            self._start_thumb_load()
        self._nav_wanted = True          # v0.3.21：这本书是想看面板的
        self._nav_dock.show()

    def _hide_nav(self):
        dk = getattr(self, '_nav_dock', None)
        if dk is not None:
            dk.hide()
        self._nav_wanted = False         # v0.3.21：切回来时别再自己冒出来
        self._stop_thumb_timer()

    def _nav_toc_go(self, item):
        """单击/双击目录项 → 跳页。"""
        if item is None:
            return
        pg = item.data(Qt.ItemDataRole.UserRole)
        d = getattr(self, 'pd', None)
        if pg is None or d is None:
            return
        self.pgno = max(0, min(int(d.page_count) - 1, int(pg)))
        self.pdf_view.goto_page(self.pgno)
        self.statusBar().showMessage('已跳到第 %d 页' % (int(self.pgno) + 1))

    def _nav_find_go(self, item):
        """双击查找结果 → 跳该页并高亮。"""
        if item is None:
            return
        k = item.data(Qt.ItemDataRole.UserRole)
        if k is None:
            return
        self._find_idx = int(k)
        self._find_jump(int(k))
        self._upd_find_label()

    def _nav_sync_current(self):
        """【v0.3.23】「导航-查找」里把**当前这一处**选中并滚到可见处。

        以前只有"用户点了列表"才会选中：用「下一个 / 上一个」一处一处走的时候
        列表一动不动，上千条结果里根本看不出自己到哪儿了。
        """
        lst = getattr(self, '_nav_find', None)
        if lst is None:
            return
        try:
            i = int(getattr(self, '_find_idx', -1))
        except Exception:
            i = -1
        try:
            _blk = lst.blockSignals(True)      # 选中是我们设的，别反过来又跳一次
            try:
                if i < 0 or i >= lst.count():
                    lst.setCurrentRow(-1)
                else:
                    lst.setCurrentRow(i)
                    it = lst.item(i)
                    if it is not None:
                        lst.scrollToItem(it)
            finally:
                lst.blockSignals(_blk)
        except Exception:
            pass

    def _fill_nav_toc(self):
        """把当前文档的大纲（无大纲则页码）填进「目录」页签，返回条目数。"""
        d = getattr(self, 'pd', None)
        if d is None:
            return 0
        try:
            items = d.get_toc() or []
        except Exception:
            items = []
        self._build_nav_dock()
        toc = self._nav_toc
        toc.clear()
        row_pages = []
        if items:
            for lvl, title, pg in items:
                it = QListWidgetItem('%s%s（第 %d 页）'
                                     % ('    ' * max(0, int(lvl) - 1), title, int(pg)))
                it.setData(Qt.ItemDataRole.UserRole, int(pg) - 1)
                toc.addItem(it)
                row_pages.append(max(0, int(pg) - 1))
        else:
            n = int(d.page_count)
            if n > NAV_TOC_MAX_PAGES:
                # batch29：《辞海》这类三千多页的书，逐页建 QListWidgetItem
                # 要好几百毫秒，而且全堵在打开的路上——使用者描述的"卡一下、
                # 中间一个方框"有一部分就是它。这种书本来也没有书签可看，
                # 给一句话说明 + 页码框跳转就够了。
                tip = QListWidgetItem(
                    '本书没有书签（共 %d 页），请直接用页码框跳转' % n)
                try:
                    tip.setFlags(Qt.ItemFlag.NoItemFlags)
                except Exception:
                    pass
                toc.addItem(tip)
            else:
                for i in range(n):
                    it = QListWidgetItem('第 %d 页' % (i + 1))
                    it.setData(Qt.ItemDataRole.UserRole, i)
                    toc.addItem(it)
                    row_pages.append(i)
        self._last_toc = {'items': items, 'pages': int(d.page_count),
                          'rows': toc.count(), 'row_pages': row_pages,
                          'path': getattr(self, '_pd_path', '')}
        return toc.count()

    def _after_pdf_open(self):
        """打开 PDF/EPUB 后：清掉上一份文档的查找状态，填目录并默认展开导航面板。"""
        # 【v0.3.21】换了书：上一本那趟后台扫词得掐掉。以前只有关窗时才收，
        # 于是旧书扫到一半的结果会盖到新书上 —— 用户看到的就是"本书命中……
        # 还在定位后面的页码…"一直挂着不动。
        try:
            self._stop_scan_worker(ms=200)
        except Exception:
            pass
        self._pd_texts = None
        self._pd_texts_doc = None
        self._hl_kw = ''
        self._hl_multi = []          # batch30
        self._find_kw = ''
        self._find_total = 0
        self._find_idx = -1
        self._find_hits = []
        # 【v0.3.17】换了书，右边「导航-查找」里还挂着上一本的几千行结果：
        # 以前只清了命中词下拉（`_fill_find_words`），压根没动结果列表，
        # 于是新书打开后翻到查找页签，看到的还是上一本书的命中。
        self._find_kept = []
        self._find_view = []
        self._find_hidden = []
        self._find_show_hidden = False
        self._hit_words_summary = ''
        # v0.3.18：上一本手输词铺出来的写法也一并清掉，别带进新书
        self._find_manual_words = []
        self._find_word_counts = {}     # v0.3.23：上一本的"谁命中多少"同理
        try:
            # 上一本那趟流式检索可能还在主线程里跑着，把代号 +1 让它自己认输
            self._find_scan_gen = int(getattr(self, '_find_scan_gen', 0) or 0) + 1
        except Exception:
            pass
        try:
            if getattr(self, '_nav_find', None) is not None:
                self._populate_find_tab()
                self._upd_find_label()
        except Exception:
            pass
        # batch29：换了书，上一本书的命中词不能还挂在查找框旁边
        try:
            self._hitset = None
            self._fill_find_words(None, run=False)
        except Exception:
            pass
        try:
            self._fill_nav_toc()
        except Exception:
            pass
        self._show_nav(0)
        # 【v0.3.14】趁用户在翻书，后台把全文抽好：等他按下 Ctrl/F 或看
        # 「所有关键词」时直接命中缓存，不用再堵主线程好几秒。
        try:
            self._start_text_warmup(getattr(self, '_pd_path', '') or '',
                                    int(getattr(getattr(self, 'pd', None),
                                               'page_count', 0) or 0))
        except Exception:
            pass

    # ③b 命中词导航条（batch28：外部检索器 CathaySearch 用 --hits <json> 传进来）
    def _build_hit_bar(self):
        """一条横向导航条：挑一个词 → 跳到它的命中页 → 一键换下一个词。

        逻辑（换词、翻命中页、循环）全在 viewer_hits 里，这里只管按钮和显示。
        """
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        self.cb_hit = QComboBox()
        self.cb_hit.setMinimumWidth(260)
        self.cb_hit.setToolTip(
            '这本书里命中了哪些词 —— 由 CathaySearch 打开时算好传进来。\n'
            '选中哪个词就跳到它在本书里的第一个命中处并高亮。')
        self.cb_hit.currentIndexChanged.connect(self._hit_pick)
        self.b_hit_prev = QPushButton('◀ 上一处')
        self.b_hit_prev.setToolTip('这个词的上一处命中')
        self.b_hit_prev.clicked.connect(self._hit_prev)
        self.b_hit_next = QPushButton('下一处 ▶')
        self.b_hit_next.setToolTip('这个词的下一处命中（Alt+↓）')
        self.b_hit_next.clicked.connect(self._hit_next)
        self.b_hit_word = QPushButton('下一个词 ▸')
        self.b_hit_word.setToolTip(
            '这个词看完了，一键换下一个词接着搜（Alt+→；Alt+← 回上一个词）')
        self.b_hit_word.clicked.connect(self._hit_next_word)
        self.lb_hit = QLabel('')
        self.b_hit_close = QPushButton('✕')
        self.b_hit_close.setToolTip('关闭命中词导航条（同时清掉高亮）')
        self.b_hit_close.clicked.connect(self._hit_close)
        for _b in (self.b_hit_prev, self.b_hit_next, self.b_hit_word,
                   self.b_hit_close):
            try:
                _b.setFixedHeight(24)
            except Exception:
                pass
        h.addWidget(QLabel('命中词'))
        h.addWidget(self.cb_hit, 0)
        h.addWidget(self.b_hit_prev)
        h.addWidget(self.b_hit_next)
        h.addWidget(self.b_hit_word)
        h.addWidget(self.lb_hit)
        h.addWidget(self.b_hit_close)
        h.addStretch(1)
        self._hit_bar_widget = w
        self._hitset = None
        self._hit_wi = 0
        self._hit_pi = 0
        w.setVisible(False)          # 默认隐藏：只有外部传了清单才出现
        return w

    def _hit_apply(self, path):
        """读入 CathaySearch 传来的命中词清单，显示导航条并落到起始处。"""
        s = HITS.HitSet.load(path)
        self._hitset = s if s else None
        if not s:
            try:
                self._hit_bar_widget.setVisible(False)
            except Exception:
                pass
            if s.error:
                self._say('命中词清单没读到：%s' % s.error, hold=3)
            return
        try:
            self.cb_hit.blockSignals(True)
            self.cb_hit.clear()
            for i in range(len(s)):
                self.cb_hit.addItem(s.label(i))
            self.cb_hit.blockSignals(False)
            # batch30：导航条这一行已经撤掉（并入查找栏），这里不再显示它
            self._hit_bar_widget.setVisible(False)
        except Exception:
            pass
        # batch30：词摆进查找栏、默认「所有关键词」合并检索并落到离当前页最近的一处
        # （--page 已经把我们带到某一页了，不该又被拽回第一处）
        try:
            self._fill_find_words(s)
        except Exception:
            pass

    def _stop_scan_worker(self, ms=3000):
        """关窗/换书前收掉后台扫词线程（QThread 运行中被销毁会原生崩）。"""
        # 【v0.3.4】扫描改成延时启动了：定时器还没响就换书/关窗的话，必须把
        # 它掐掉，否则线程会在没人接手的时候被拉起来（结果没人收 = 白跑一趟）。
        t = getattr(self, '_scan_timer', None)
        self._scan_timer = None
        if t is not None:
            try:
                t.stop()
                t.deleteLater()
            except Exception:
                pass
        w = getattr(self, '_scan_worker', None)
        self._scan_worker = None
        # 【v0.3.23】扫词被掐断（换书 / 换标签 / 关窗）时，最后一批往往到不了
        # 100%，那串"还在定位后面的页码…"就永久挂在摘要后面了。掐掉线程之后
        # 顺手把它收掉。（放在 return 之前：本来就没有在跑的线程时同样要收）
        self._set_hit_words_label()
        if w is None:
            return
        try:
            if w.isRunning():
                w.stop()
                w.wait(int(ms))
        except Exception:
            pass

    def _set_hit_words_label(self, pct=None):
        """【v0.3.23】刷新查找栏上「本书命中…」那一行。

        pct 给了（0~100）就把后台扫词的进度挂在摘要后面；不给 = 这一轮结束了，
        只留摘要。以前这串进度字样只由"某一批扫词回调"负责收，扫词被掐断、
        或最后一批没到 100% 时它就一直挂在那儿，看着像永远扫不完。
        """
        try:
            _sum = getattr(self, '_hit_words_summary', '') or ''
            if pct is not None:
                pct = int(pct)
                if pct >= 100 or _sum:
                    txt = _sum if pct >= 100 else \
                        '%s（还在定位后面的页码… %d%%）' % (_sum, pct)
                else:
                    txt = '正在定位命中页码… %d%%' % pct
            else:
                txt = _sum
            if self.lb_hit_words.text() != txt:
                self.lb_hit_words.setText(txt)
            if txt:
                self.lb_hit_words.setVisible(True)
        except Exception:
            pass

    def _words_apply(self, words):
        """外部用 `--words 词1、词2` 传一组词：开书后按这些词各搜一遍。

        【v0.3.2】重写为**后台扫描**。以前在 UI 线程里同步挨个扫（for 循环调
        `_scan_pdf`），大书会把界面卡住转圈；而 Search 那头改成"立刻唤起、不等
        页码"之后，这里若还是同步的，等于把等待从 Search 原样搬到 Viewer，
        还额外搭上一个卡界面。

        现在：书照常先开出来，扫描在后台跑，扫完再长出命中词导航条并落到命中处。
        PDF 现在也给出**页级清单**（以前 `--words` 只有 TXT 走，且只报处数、
        不报页码），命中词导航条因此可以逐页跳。
        """
        words = [w.strip() for w in (words or []) if w.strip()]
        if not words:
            return
        cur = self._find_current_path()
        if not cur:
            return
        _prior = getattr(self, '_hitset', None)
        if _prior is not None and getattr(_prior, 'source', '') == '--hits':
            return          # 页级清单已经把查找栏填好了，别再扫一遍
        self._stop_scan_worker()
        self._last_scan_words = list(words)
        try:
            self._scan_base_page = int(self.pgno) + 1     # pgno 是 0 起
        except Exception:
            self._scan_base_page = 1

        def _txt(p):
            """TXT 取文口径要和 `_scan_text` 一致（超 64 MB 只载前 64 MB）。"""
            try:
                sz = os.path.getsize(p)
            except OSError:
                sz = 0
            return self._norm_ws(self._read_text_file(
                p, TEXT_MAX_BYTES if sz > TEXT_MAX_BYTES else 0))

        w = WordsScanWorker(cur, words, normfn=self._norm_ws,
                            textfn=_txt, parent=self)
        w.scanned.connect(self._on_words_scanned)
        # v0.3.7：扫一坨就先报一次 → 命中词导航条立刻长出来，不用等整本扫完
        w.partial.connect(self._on_words_partial)
        self._scan_worker = w
        self._words_partial_seen = False
        self._hit_words_summary = ''      # v0.3.17：这一轮还没出结果
        try:
            self.lb_hit_words.setText('正在定位命中页码…')
            self.lb_hit_words.setVisible(True)
        except Exception:
            pass
        # 【v0.3.4】别跟首屏渲染抢 CPU：书刚装载完那一下，渲染线程正满负荷
        # 画可见页，扫词（大书近 1 秒）这时候插进来会把翻页拖成幻灯片。
        # 让首屏那几帧先画完再开扫；中途换书/关窗由 _stop_scan_worker 掐掉。
        try:
            from PyQt6.QtCore import QTimer as _ST
            t = _ST(self)
            t.setSingleShot(True)
            t.timeout.connect(w.start)
            t.start(SCAN_START_DELAY_MS)
            self._scan_timer = t
        except Exception:
            w.start()

    def _on_words_partial(self, path, out, done, total):
        """【v0.3.7】扫了一部分就先报：命中词导航条**马上**长出来。

        以前非得等整本书扫完才有导航条，大书要等好几秒——用户看到的就是
        "打开后过半天才有搜索词"。现在扫够一坨（默认 64 页）先出一批命中，
        后面陆续补齐；第一次出结果时就跳到第一个命中处，之后只更新不抢位置。
        """
        cur = self._find_current_path()
        if not cur or not path or os.path.normcase(cur) != os.path.normcase(path):
            # 期间换书了，这批不算 —— 【v0.3.21】顺便把"还在定位后面的页码…"
            # 这串进度字样收掉：换书时摘要已清空，不收的话它会一直挂在那儿。
            self._set_hit_words_label()
            return                      # 期间换书了，这批不算
        first = not getattr(self, '_words_partial_seen', False)
        self._words_partial_seen = True
        if out:
            self._apply_word_hits(path, out, jump=first)
        if total:
            pct = int(min(100, done * 100.0 / max(1, total)))
            # 【v0.3.17】以前这里无条件把文字改回「正在定位…%d%%」，
            # 而上面那句 _apply_word_hits 刚把「本书命中：…」填好 ——
            # 于是这一行被反复盖掉，用户看到的就是"结果明明已经能点
            # 『下一个』跳了，却一直写着正在定位命中页码"。
            # 现在：已经有命中摘要就把进度挂在它后面，100% 时不再留进度字样。
            # 【v0.3.23】统一交给 _set_hit_words_label：扫词被掐断时它也会收尾。
            self._set_hit_words_label(pct)

    def _apply_word_hits(self, path, out, jump=False):
        """把一批命中结果填进命中词导航条 / 查找栏；jump=True 时顺手跳过去。"""
        is_pdf = os.path.splitext(path)[1].lower() == '.pdf'
        hs = [HITS.HitWord(_w, list(_p), int(_c)) for _w, _p, _c in out]
        s = HITS.HitSet(hs, source='--words', file=path, text_mode=not is_pdf)
        self._hitset = s
        try:
            cur1 = int(self.pgno) + 1
        except Exception:
            cur1 = 1
        # 【v0.3.16】这里以前把"填结果"和"跳过去"绑成了一件事：
        # `run = jump and cur1 == base`。于是只要这两三百毫秒里页码动过一下
        # —— Search 带 `--page` 进来、PDF 尺寸纠正后重排渲染时的那次 `_on_pdf_page`
        # 同步都算"动过" —— 就既不给结果也不建命中序列，用户看到的就是
        # 「下一个」怎么点都没反应、状态栏一直停在「正在定位命中页码…10%」，
        # 非得手点一个单独的关键词才行（那条路径压根不走这里）。
        # 现在：结果**一定**建一份（"下一个"才有得走），只是不抢用户的页码。
        stayed = (jump and cur1 == getattr(self, '_scan_base_page', 1))
        self._fill_find_words(s, run=True, jump=bool(stayed))

    def _on_words_scanned(self, path, out):
        """后台扫完了：长出命中词导航条，并落到命中处。

        两件事要当心：
        - 扫的这半秒里用户可能已经换了书（或切了标签），这批结果就该作废；
        - 自动跳页会把正在看的位置拽走 —— 只有用户没翻过页才跳。
        """
        self._scan_worker = None
        cur = self._find_current_path()
        if not cur or not path or os.path.normcase(cur) != os.path.normcase(path):
            # 期间换书了，这批结果作废 —— 【v0.3.21】同上，把可能还挂着的
            # "还在定位…"进度字样一起收掉。
            self._set_hit_words_label()
            return                      # 期间换书了，这批结果作废
        words = list(getattr(self, '_last_scan_words', []) or [])
        if not out:
            self._fill_find_words(None, run=False)
            try:                        # 词照样摆进查找栏，别让用户对着空栏发呆
                self.cb_find_word.blockSignals(True)
                for _w in words:
                    if self.cb_find_word.findData(_w) < 0:
                        self.cb_find_word.addItem(_w, _w)
                self.cb_find_word.blockSignals(False)
            except Exception:
                pass
            _tip = '这几个词在文件里一处都没找到'
            if os.path.splitext(cur)[1].lower() == '.pdf':
                _tip += '（多半是扫描版 PDF，没有文字层）'
            self._say(_tip + '—— 词已填进查找栏，可改词再搜', hold=4)
            return
        # v0.3.7：中途已经出过结果（导航条早就长出来了）→ 这里只把全本结果补上，
        # 不再跳页，免得把用户正在看的位置又拽走一次
        self._apply_word_hits(
            cur, out,
            jump=not getattr(self, '_words_partial_seen', False))
        # 【v0.3.23】整本扫完了：不管最后一批报的是百分之多少，进度字样到此为止
        self._set_hit_words_label()

    def _hit_goto(self, wi, pi):
        """跳到某个词的第 pi 个命中处：高亮该词 + 翻到那一页。"""
        s = getattr(self, '_hitset', None)
        if not s:
            return
        pg = s.pages(wi)
        if not pg:
            return
        pi = max(0, min(int(pi), len(pg) - 1))
        self._hit_wi, self._hit_pi = wi, pi
        word = s.word(wi)
        page1 = pg[pi]
        try:
            self.pgno = max(0, page1 - 1)
            self._hl_kw = word
            self._pdf_view_active().set_highlight(word)
            self._pdf_view_active().goto_page(self.pgno)
        except Exception:
            pass
        try:
            self.cb_hit.blockSignals(True)
            self.cb_hit.setCurrentIndex(wi)
            self.cb_hit.blockSignals(False)
        except Exception:
            pass
        # batch29：导航条换到哪个词，查找框旁边的下拉也跟着指过去
        self._sync_find_word(word)
        self._upd_hit_label()
        self._say('命中词「%s」：第 %d 页（第 %d/%d 处）'
                  % (word, page1, pi + 1, len(pg)), hold=2)

    def _upd_hit_label(self):
        s = getattr(self, '_hitset', None)
        try:
            if not s:
                self.lb_hit.setText('')
                return
            self.lb_hit.setText('第 %d / %d 处' % (self._hit_pi + 1,
                                                   len(s.pages(self._hit_wi))))
        except Exception:
            pass

    def _hit_pick(self, i):
        """下拉框选词。currentIndexChanged 在填充时已被 blockSignals 挡掉。"""
        if getattr(self, '_hitset', None) is None:
            return
        self._hit_goto(i, 0)

    def _hit_next(self):
        s = getattr(self, '_hitset', None)
        if not s:
            return
        wi, pi, _pg = s.step_page(self._hit_wi, self._hit_pi, 1)
        self._hit_goto(wi, pi)

    def _hit_prev(self):
        s = getattr(self, '_hitset', None)
        if not s:
            return
        wi, pi, _pg = s.step_page(self._hit_wi, self._hit_pi, -1)
        self._hit_goto(wi, pi)

    def _hit_next_word(self):
        s = getattr(self, '_hitset', None)
        if not s:
            return
        wi, _pg = s.step_word(self._hit_wi, 1)
        self._hit_goto(wi, 0)

    def _hit_prev_word(self):
        s = getattr(self, '_hitset', None)
        if not s:
            return
        wi, _pg = s.step_word(self._hit_wi, -1)
        self._hit_goto(wi, 0)

    def _hit_close(self):
        try:
            self._hit_bar_widget.setVisible(False)
        except Exception:
            pass
        try:
            self._pdf_view_active().set_highlight('')
        except Exception:
            pass
        self._hl_kw = ''
        self._hl_multi = []          # batch30
        self._hitset = None
        # 导航条关了，查找框旁边的那一行也一并收掉；查找条本身留着继续用
        try:
            self.lb_hit_words.setText('')
            self.lb_hit_words.setVisible(False)
            self.cb_find_word.blockSignals(True)
            self.cb_find_word.clear()
            self.cb_find_word.blockSignals(False)
            self.cb_find_word.setVisible(False)
            self.ed_find.setPlaceholderText(
                '文内查找（Ctrl+F；回车查找）—— PDF 需有文字层')
        except Exception:
            pass

    # ④ 文内查找条（Ctrl+F）
    def _build_find_bar(self):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        self.ed_find = QLineEdit()
        self.ed_find.setPlaceholderText('文内查找（Ctrl+F；回车查找）—— PDF 需有文字层')
        self.ed_find.returnPressed.connect(self.find_run)
        self.b_find_prev = QPushButton('上一个')
        self.b_find_prev.clicked.connect(self.find_prev)
        self.b_find_next = QPushButton('下一个')
        self.b_find_next.clicked.connect(self.find_next)
        self.lb_find = QLabel('第 0 / 0 处')
        self.b_find_close = QPushButton('✕')
        self.b_find_close.setToolTip('关闭查找条（Esc）')
        self.b_find_close.clicked.connect(self.find_close)
        self.lb_hit_words = QLabel('')
        self.lb_hit_words.setToolTip(
            '这本书里命中了哪些词（CathaySearch 打开时算好一起传进来的）。')
        self.lb_hit_words.setStyleSheet('color:#0B5CAB;')
        self.cb_find_word = QComboBox()
        self.cb_find_word.setMinimumWidth(190)
        self.cb_find_word.setToolTip(
            '这本书命中的词（由 CathaySearch 传入）。\n'
            '默认「所有关键词」：所有词的命中合并成一条按出现先后排好的序列，\n'
            '用「上一个 / 下一个」一处一处走；\n'
            '也可以只挑某一个词，那时只在这个词的命中里走。')
        self.cb_find_word.currentIndexChanged.connect(self._find_pick_word)
        # batch30：换词按钮（原来在独立导航条上，现在并到这一行）
        self.b_find_word = QPushButton('下一个词 ▸')
        self.b_find_word.setToolTip(
            '换成下一个关键词，跳到它的第一处（Alt+→；Alt+← 回上一个词）。\n'
            '停在「所有关键词」时不需要换词，按钮是灰的。')
        self.b_find_word.clicked.connect(self._find_next_word)
        h.addWidget(self.lb_hit_words)
        h.addWidget(self.cb_find_word)
        h.addWidget(self.b_find_word)
        h.addWidget(QLabel('查找'))
        h.addWidget(self.ed_find, 1)
        # v0.3.18：单独打开一本书时，也能按全库检索那一套规则去找
        self.chk_find_variant = QCheckBox('繁简')
        self.chk_find_variant.setToolTip(
            '繁简通搜：你打的这个词别的字形也一起找（搜「吴佩孚」也认「吳佩孚」）。\n'
            '跟 CathaySearch 里用的是同一条规则。')
        self.chk_find_alias = QCheckBox('联想')
        self.chk_find_alias.setToolTip(
            '联想词：这个人的字号 / 笔名 / 化名也一起找（搜「吴佩孚」也认「子玉」\n'
            '「吴玉帅」）。用的是同一张 CBDB 别名表，在检索里自己加过的也算。')
        for _c in (self.chk_find_variant, self.chk_find_alias):
            _c.setChecked(True)
            h.addWidget(_c)
        self.chk_find_variant.setChecked(bool(self.st.get('find_variant', True)))
        self.chk_find_alias.setChecked(bool(self.st.get('find_alias', True)))
        self.chk_find_variant.toggled.connect(
            lambda v: (self.st.__setitem__('find_variant', bool(v)),
                       self._say('文内查找：繁简通搜已%s（回车重找一次生效）'
                                 % ('开' if v else '关'), hold=2)))
        self.chk_find_alias.toggled.connect(
            lambda v: (self.st.__setitem__('find_alias', bool(v)),
                       self._say('文内查找：联想词已%s（回车重找一次生效）'
                                 % ('开' if v else '关'), hold=2)))
        for _b in (self.b_find_prev, self.b_find_next, self.b_find_close,
                   self.b_find_word):
            try:
                _b.setFixedHeight(24)
            except Exception:
                pass
        h.addWidget(self.b_find_prev)
        h.addWidget(self.b_find_next)
        h.addWidget(self.lb_find)
        h.addWidget(self.b_find_close)
        self._find_bar_widget = w
        w.setVisible(False)          # 初始隐藏，Ctrl+F 才出现（给阅读让空间）
        return w

    # ---------------------------------------------- 命中词 ↔ 查找条（batch29）
    # batch30：原来"查找栏里一行 + 独立导航条一行"，两行各说一遍同样的词，
    # 是两批需求各加一个造成的。现在合成一行：导航条撤掉，换词按钮搬过来，
    # 默认停在第一项「所有关键词」——所有词的命中合并成一条按出现先后排好的
    # 序列，一处一处走；要只看某一个词时再从这个下拉里挑。
    def _fill_find_words(self, s, run=True, jump=True):
        """把 CathaySearch 传来的命中词摆进查找栏，并默认按「所有关键词」检索一遍。

        run=False 时不自动检索（换书清场时用）；jump=False 时**照样要建那一份结果**
        （不然「下一个」没得走），但不去动用户正在看的页码 —— 见 `_apply_word_hits`
        里的那段注释。
        """
        # 【v0.3.23】`_hl_multi` **不能在这里无条件清掉**：后台扫词是分批发的
        # （每 64 页一批），第二批及以后走到这儿时词没变（下面 sig 相同 → 不重算），
        # 高亮却被抹平了；随后后台把页面尺寸算准、再走一次 `_pdf_show()` 时拿到的
        # 就是空高亮 —— 表现正是「所有关键词模式下关键词不高亮」。
        # 现在改成：真要重算才清；不重算就把上一轮的高亮原样按回去。
        if not run:
            self._find_applied_sig = None     # 换书清场：下一批词要重算
            self._hl_multi = []
            self._find_word_counts = {}
        self._find_manual_words = []  # v0.3.18：下拉交回 CathaySearch 传来的词
        try:
            self.cb_find_word.blockSignals(True)
            self.cb_find_word.clear()
            self.cb_find_word.addItem('所有关键词', '')   # 第 0 项：全部词合并
            if s and len(s):
                for i in range(len(s)):
                    self.cb_find_word.addItem(s.label(i), s.word(i))
                summary = '、'.join(
                    '%s(%d页/%d处)' % (s.word(i), len(s.pages(i)), s.count(i))
                    for i in range(len(s)))
                self._hit_words_summary = '本书命中：%s' % summary
                self.lb_hit_words.setText(self._hit_words_summary)
            else:
                self._hit_words_summary = ''
                self.lb_hit_words.setText('')
            self.cb_find_word.setCurrentIndex(0)
            self.cb_find_word.blockSignals(False)
        except Exception:
            pass
        vis = bool(s and len(s))
        try:
            self.lb_hit_words.setVisible(vis)
            self.cb_find_word.setVisible(vis)
            self.b_find_word.setVisible(vis)
        except Exception:
            pass
        try:
            if vis:
                self._find_bar_widget.setVisible(True)
                self.ed_find.setPlaceholderText(
                    '文内查找：默认已按「所有关键词」合并排好，'
                    '回车可另查一个词（Ctrl+F 或 ✕ 关掉）')
            else:
                self.ed_find.setPlaceholderText(
                    '文内查找（Ctrl+F；回车查找）—— PDF 需有文字层')
        except Exception:
            pass
        self._upd_word_btn()
        if vis and run:
            # 默认「所有关键词」：把所有词的命中合并成一条序列。
            # 【v0.3.16】同一批词只建一次 —— 后台扫词是分批发的（每 64 页一次），
            # 不设这个闸的话后面每一批都会再走一遍全本合并检索，白等好几趟。
            sig = tuple(self._find_kws_list())
            if getattr(self, '_find_applied_sig', None) != sig:
                self._find_applied_sig = sig
                self._hl_multi = []          # 真要重算了，上一轮的高亮才撤
                self._find_all_kws(jump_near=bool(jump), auto=bool(jump))
            else:
                # 同一批词：结果不用重算，高亮也得留着 —— 见上面那段注释
                self._hl_reassert()

    def _hl_reassert(self):
        """【v0.3.23】把当前的高亮词重新按到视图上（结果没重算时用）。

        只在"视图上的高亮跟现在该有的不一致"时才真的设一次，免得每来一批
        扫词结果就把高亮缓存清一遍、整屏重画 —— 流式检索最怕这个。
        """
        try:
            _m = [str(x) for x in (getattr(self, '_hl_multi', None) or []) if x]
            if not _m or getattr(self, 'pd', None) is None:
                return
            v = self._pdf_view_active()
            if v is None:
                return
            if [str(x) for x in (getattr(v, '_hl_multi', None) or [])] != _m:
                v.set_highlight(_m)
        except Exception:
            pass

    def _find_kws_list(self):
        """下拉里除「所有关键词」之外的全部词（按顺序、去重）。"""
        out = []
        cb = getattr(self, 'cb_find_word', None)
        if cb is None:
            return out
        for i in range(1, cb.count()):
            w = ''
            try:
                w = cb.itemData(i) or cb.itemText(i) or ''
            except Exception:
                w = ''
            w = str(w).strip()
            if w and w not in out:
                out.append(w)
        return out

    def _upd_word_btn(self):
        """「下一个词」只有停在单个词上才有意义：所有关键词模式下置灰。"""
        try:
            cb = self.cb_find_word
            i = cb.currentIndex()
            n = cb.count()
            self.b_find_word.setEnabled(i > 0 and n > 2)
        except Exception:
            pass

    def _find_next_word(self):
        """换到下一个词并跳到它的第一处（多个词之间循环）。"""
        cb = getattr(self, 'cb_find_word', None)
        if cb is None or cb.currentIndex() <= 0 or cb.count() <= 2:
            return
        n = cb.count()
        j = cb.currentIndex() + 1
        cb.setCurrentIndex(1 if j >= n else j)

    def _find_prev_word(self):
        """换到上一个词（多个词之间循环）。"""
        cb = getattr(self, 'cb_find_word', None)
        if cb is None or cb.currentIndex() <= 0 or cb.count() <= 2:
            return
        n = cb.count()
        j = cb.currentIndex() - 1
        cb.setCurrentIndex(n - 1 if j < 1 else j)

    def _find_pick_word(self, i):
        """下拉选择：第 0 项 = 所有关键词（合并混排）；其余 = 只看这一个词。"""
        if i < 0 or not hasattr(self, 'cb_find_word'):
            return
        self._upd_word_btn()
        if i == 0:
            self._find_all_kws(jump_near=False)
            return
        w = ''
        try:
            w = self.cb_find_word.itemData(i) or self.cb_find_word.itemText(i)
        except Exception:
            return
        w = (w or '').strip()
        if not w:
            return
        # 【v0.3.23】下拉里标了「未命中」的词：别再白跑一趟全本扫描，直接说清楚
        _c = getattr(self, '_find_word_counts', None) or {}
        if w in _c and not _c.get(w):
            self._say('「%s」在这本书里一处也没命中（未命中的写法都排在下拉最后，'
                      '想换口径可在查找框里重搜）' % w, hold=4)
            return
        try:
            self.ed_find.setText(w)
        except Exception:
            return
        # 命中词逐个搜：限定在本书，不外扩到同名变体（那是 Ctrl+F 的行为）
        self._find_only_current = True
        self._hl_multi = []          # 只看一个词 → 只高亮这一个
        # v0.3.18：下拉里点哪个词就只看哪个词 —— 不再给它铺开繁简/联想词，
        # 否则点「子玉」又冒出「吴佩孚」那一串，等于没点。
        self._find_no_expand = True
        try:
            self.find_run()
        finally:
            self._find_no_expand = False
        self._say('只看「%s」（%d 处）——用「下一个」逐处看，'
                  '「下一个词」换别的词，「所有关键词」回到合并序列'
                  % (w, int(_c.get(w, 0) or 0) if w in _c
                     else int(self._find_total or 0)), hold=3)

    def _find_all_kws(self, words=None, jump_near=True, auto=True, strict=False):
        """【batch30】「所有关键词」：每个词各扫一遍，合并成一条按出现先后排好的序列。

        words 不传时取下拉里的词（「所有关键词」那个入口）；v0.3.18 起手输词
        铺开成多个写法之后也走这里 —— 传 words 即可，两条路共用同一套扫描。


        同一个文件里，先按页码、同页再按页内位置混排——用户要的就是"顺着往下读"，
        而不是一个词看完再跳回开头看下一个词。

        jump_near=True 时落在**离当前页最近**的那一处：CathaySearch 是带 --page
        调起来的，刚跳到那一页又被拽回第 1 处会莫名其妙。

        auto=False：**只建结果、不动页码**（也不自动跳到第一处）。后台扫词后续
        补结果时用：用户这会儿多半已经在自己翻了，别把他拽走 —— 「下一个」照样
        能一处一处走，只是从结果的第一处开始。

        【v0.3.15】PDF 走**流式**：抽一页找一页，找到就往界面上推。以前是先抽完整
        本书（1100 页约 1.2 秒）才一次性炸出几千行结果 —— 这中间界面上一个字都
        没有，看着像没反应。现在第一批结果几十毫秒就出来了，后续往上长。
        """
        if words is None:
            words = self._find_kws_list()
        words = [str(w or '').strip() for w in (words or [])]
        words = [w for w in words if w]
        if not words:
            return
        cur = self._find_current_path()
        if not cur:
            return
        self._find_only_current = True          # 就在这本书里，不外扩同名变体
        ext = os.path.splitext(cur)[1].lower()
        fmeta = {'path': cur, 'name': os.path.basename(cur)}

        def _key(x):
            h = x[0]
            return (int(h.get('page')) if h.get('page') is not None else -1,
                    int(h.get('off')) if h.get('off') is not None else 0)

        done = True                             # 这一趟有没有被后来者挤掉
        _jumps0 = int(getattr(self, '_find_jump_seq', 0) or 0)   # 开找之前跳过几回
        if ext == '.pdf':
            gen = int(getattr(self, '_find_scan_gen', 0) or 0) + 1
            self._find_scan_gen = gen
            kept = []
            self._find_kept = kept
            self._find_hidden = []
            self._find_show_hidden = False
            self._find_kw = words[0]
            self._hl_kw = ''
            self._hl_multi = list(words)        # 所有关键词一起高亮
            self._rebuild_find_view()
            try:
                if getattr(self, 'pd', None) is not None:
                    self._pdf_view_active().set_highlight(list(words))
            except Exception:
                pass
            self._populate_find_tab()           # 先把旧结果清掉，别混着长
            # 面板要**现在**就亮出来：原来放在最后 `_show_nav(2)`，等于前面找到的
            # 那几千条全憋在暗处，用户什么也看不见 —— 这正是流式要解决的。
            self._show_nav(2)
            shown = 0
            for scanned, total, pairs in self._iter_find_hits(cur, words,
                                                             strict=strict):
                # 用户中途又按了一次「所有关键词」→ 这一趟作废，让位给新的
                if int(getattr(self, '_find_scan_gen', 0) or 0) != gen:
                    done = False
                    break
                if pairs:
                    if len(pairs) > 1:
                        pairs.sort(key=_key)
                    for h, kw in pairs:
                        kept.append(dict(self._hit(fmeta, h), kw=kw))
                    self._find_kept = kept
                    self._rebuild_find_view()
                    self._populate_find_from(shown)
                    shown = len(self._find_view)
                    self._upd_find_label()
                try:
                    self.statusBar().showMessage(
                        '正在找… %d / %d 页，已找到 %d 处' % (scanned, total,
                                                       len(self._find_view)))
                except Exception:
                    pass
                try:
                    QApplication.processEvents()   # 别把界面按死
                except Exception:
                    pass
            if done:
                self._rebuild_find_view()
                if len(self._find_view) != self._nav_count():
                    self._populate_find_tab()
        else:
            merged = []
            for kw in words:
                try:
                    _cnt, hh = self._scan_text(cur, kw, strict=strict)
                except Exception:
                    continue
                for h in hh:
                    merged.append((h, kw))
            merged.sort(key=_key)
            self._find_kept = [dict(self._hit(fmeta, h), kw=kw)
                               for h, kw in merged]
            self._find_hidden = []
            self._find_show_hidden = False
            self._find_kw = words[0]
            self._hl_kw = ''
            self._hl_multi = list(words)        # 所有关键词一起高亮
            self._rebuild_find_view()
            try:
                if getattr(self, 'pd', None) is not None:
                    self._pdf_view_active().set_highlight(list(words))
            except Exception:
                pass
            self._populate_find_tab()
        if not done:
            return
        self._upd_find_label()
        if not self._find_view:
            self.statusBar().showMessage(
                '「所有关键词」在这本书里一处也没找到（多半是扫描版没文字层）')
            return
        i0 = 0
        if jump_near:
            pg = int(getattr(self, 'pgno', 0) or 0)
            for k, h in enumerate(self._find_view):
                p = h.get('page')
                if p is not None and int(p) >= pg:
                    i0 = k
                    break
        if not auto:
            # 只要结果、不要跳：停在"还没开始"（下标 -1），用户按「下一个」
            # 就从第一处走起 —— 既不抢位置，「下一个」也不会点不动。
            self._find_idx = -1
            self._upd_find_label()
            return
        # 流式期间用户要是自己点过某条结果，就别在收尾时把人从看的地方拽回第
        # 1 处 —— 超大书抽起来要好几分钟，这种"跳回来"最烦人。
        if int(getattr(self, '_find_jump_seq', 0) or 0) == _jumps0:
            self._find_jump(i0)
        try:
            _tab = int(self._nav_tabs.currentIndex())   # 同理：没人翻页签才固回查找页
        except Exception:
            _tab = 2
        if _tab == 2:
            self._show_nav(2)
        self.statusBar().showMessage(
            '所有关键词（%d 个词）：共 %d 处，已按出现先后排好 ——'
            '「下一个」一处一处走' % (len(words), self._find_total))
        # 【v0.3.20】落到某一处之后，要是这一页**一个黄框都画不出来**，别让用户
        # 对着干干净净的页面纳闷"不是命中了吗"。多半是这一页的文字层里根本
        # 定位不到这个词（OCR 认错字 / 纯图页），明确说一句比沉默强。
        # 放在上面那句 showMessage **之后** —— 否则会被它盖掉。
        try:
            _v = self._pdf_view_active()
            if _v is not None and getattr(self, '_reader', '') == 'pdf':
                _cur1 = int(getattr(self, 'pgno', 0) or 0) + 1
                if _cur1 >= 1 and not _v._page_hl(_cur1 - 1):
                    self.statusBar().showMessage(
                        '第 %d 页报了命中，但这一页的文字层里定位不到它的具体位置'
                        '（OCR 认错字或纯图页），所以画不出黄框 —— 换一处或用'
                        '「下一个」看看别的页' % _cur1, 8000)
        except Exception:
            pass

    def _sync_find_word(self, word):
        """导航条换了词 → 查找框旁边的下拉跟着换，两边别打架。"""
        try:
            if not hasattr(self, 'cb_find_word'):
                return
            idx = -1
            for i in range(1, self.cb_find_word.count()):
                if (self.cb_find_word.itemData(i) or '') == word:
                    idx = i
                    break
            self.cb_find_word.blockSignals(True)
            self.cb_find_word.setCurrentIndex(idx if idx >= 0 else 0)
            self.cb_find_word.blockSignals(False)
        except Exception:
            pass

    def find_focus(self):
        """Ctrl+F：显示并聚焦查找条。"""
        _mw = self._tab_current_mw()      # v0.3.21：多标签 → 交给当前那本
        if _mw is not None and _mw is not self:
            return _mw.find_focus()
        try:
            self._find_only_current = False      # 手输 Ctrl+F 恢复跨同名变体的旧行为
            self._find_bar_widget.setVisible(True)
            self.ed_find.setFocus()
            self.ed_find.selectAll()
        except Exception:
            pass

    def find_close(self):
        """Esc / ✕：关闭查找条，清掉 PDF 高亮。"""
        try:
            self._find_bar_widget.setVisible(False)
        except Exception:
            pass
        if getattr(self, '_hl_kw', '') or getattr(self, '_hl_multi', None):
            self._hl_kw = ''
            self._hl_multi = []          # batch30：多词高亮一并清掉
            try:
                self.pdf_view.set_highlight('')
            except Exception:
                pass
        self._find_total = 0
        self._find_idx = -1
        self._upd_find_label()

    def _upd_find_label(self):
        try:
            # 【v0.3.8】别写 `getattr(...) or -1`：下标 0 会被 or 当成"没值"吞掉，
            # 于是明明有 4 处、正停在第 1 处，标签却写"第 0 / 4 处"
            _i = int(getattr(self, '_find_idx', -1))
        except Exception:
            _i = -1
        try:
            _n = int(getattr(self, '_find_total', 0) or 0)
            self.lb_find.setText('第 %d / %d 处' % ((_i + 1) if _n > 0 else 0, _n))
        except Exception:
            pass

    def _find_terms_of(self, kw):
        """【v0.3.18】手输的一个词 → 该一并去搜的写法表（主词永远排第一）。

        规则跟 CathaySearch 全库检索**一模一样**（同一份实现、同一张 CBDB 表）：
        书库里搜「吴佩孚」带上了「吴玉帅」，点进书里却只认三个字，那不成。
        两个开关都关掉 = 只认原词，跟老版本一个口径。
        """
        kw = (kw or '').strip()
        if not kw:
            return []
        use_v = bool(self.st.get('find_variant', True))
        use_a = bool(self.st.get('find_alias', True))
        if not use_v and not use_a:
            return [kw]
        try:
            words = QE.expand(kw, use_variant=use_v, use_alias=use_a,
                              limit=FIND_ALIAS_LIMIT)
        except Exception:
            return [kw]
        out = []
        for w in words or []:
            w = str(w or '').strip()
            if w and w not in out:
                out.append(w)
        if kw not in out:
            out.insert(0, kw)
        return out or [kw]

    def _fill_find_words_manual(self, words):
        """把手输词铺出来的写法摆进下拉（第 0 项「所有关键词」= 全部合并）。"""
        self._find_word_counts = {}   # v0.3.23：新一轮扫完才知道谁命中多少
        try:
            self.cb_find_word.blockSignals(True)
            self.cb_find_word.clear()
            self.cb_find_word.addItem('所有关键词', '')
            for w in words:
                self.cb_find_word.addItem(w, w)
            self.cb_find_word.setCurrentIndex(0)
            self.cb_find_word.blockSignals(False)
        except Exception:
            pass
        try:
            self.lb_hit_words.setVisible(True)
            self.cb_find_word.setVisible(True)
            self.b_find_word.setVisible(True)
        except Exception:
            pass
        self._upd_word_btn()

    def _upd_word_summary(self):
        """扫完之后，把每个写法各命中多少处写在下拉旁边（多的在前）。"""
        try:
            cnt = {}
            for h in (getattr(self, '_find_view', None) or []):
                k = h.get('kw') or ''
                if k:
                    cnt[k] = cnt.get(k, 0) + 1
            self._find_word_counts = cnt
            if not cnt:
                return cnt
            self._hit_words_summary = '本书命中：%s' % '、'.join(
                '%s(%d处)' % (k, n)
                for k, n in sorted(cnt.items(), key=lambda x: -x[1]))
            self._set_hit_words_label()
            return cnt
        except Exception:
            return {}

    def _rank_find_words(self, counts=None):
        """【v0.3.23】下拉里的写法按**命中多少**重排：多的在前，一处也没命中的
        沉到最后并写明「未命中」。

        以前一律按"繁简/联想铺开的顺序"摆出来 —— 十几个写法里真正命中的可能
        只有两三个，用户却得挨个点过去试。现在一眼看得出来，想单独看哪个词
        直接点它就行（未命中的点了会直接说明，不再白跑一趟扫描）。

        只动 Viewer 自己铺开的那一栏（`find_run`），CathaySearch 传来的命中词
        清单照旧 —— 那边本来就是按命中的词给的，顺序由 Search 决定。
        """
        cb = getattr(self, 'cb_find_word', None)
        if cb is None or cb.count() <= 1:
            return
        cnt = dict(counts if counts is not None
                   else (getattr(self, '_find_word_counts', None) or {}))
        items = []
        for i in range(1, cb.count()):
            w = ''
            try:
                w = str(cb.itemData(i) or cb.itemText(i) or '').strip()
            except Exception:
                w = ''
            if w:
                items.append((w, int(cnt.get(w, 0) or 0)))
        if not items:
            return
        # 命中多的在前；一样多时保持铺开的原顺序（主词本来排第一）
        order = {w: k for k, (w, _n) in enumerate(items)}
        items.sort(key=lambda x: (-x[1], order.get(x[0], 0)))
        try:
            cb.blockSignals(True)
            try:
                cur = cb.currentIndex()
                cur_w = str(cb.itemData(cur) or '') if cur > 0 else ''
                cb.clear()
                cb.addItem('所有关键词', '')
                for w, n in items:
                    cb.addItem('%s（%s）' % (w, ('%d处' % n) if n else '未命中'), w)
                idx = 0
                if cur_w:
                    for i in range(1, cb.count()):
                        if str(cb.itemData(i) or '') == cur_w:
                            idx = i
                            break
                cb.setCurrentIndex(idx)
            finally:
                cb.blockSignals(False)
        except Exception:
            try:
                cb.blockSignals(False)
            except Exception:
                pass
        self._find_word_counts = {w: n for w, n in items}
        self._upd_word_btn()

    def find_run(self):
        """执行查找（当前文件内）。"""
        kw = (self.ed_find.text() or '').strip()
        if not kw:
            self._find_total = 0
            self._find_idx = -1
            self._upd_find_label()
            return
        if getattr(self, '_find_no_expand', False):
            words = [kw]                       # 下拉里点明的那一个词，原样搜
        else:
            words = self._find_terms_of(kw)
        # 关掉「繁简通搜」= 只认打出来的那几个字（跟 CathaySearch 不勾一样）
        strict = not bool(self.st.get('find_variant', True))
        if len(words) <= 1:
            # 只一个写法（或用户把两个开关都关了）→ 走原来那条路，一点没变。
            # 上一轮要是铺开过，这轮得把下拉收回去，别把上一串词留在那儿。
            if getattr(self, '_find_manual_words', None):
                self._fill_find_words(None, run=False)
            self._find_manual_words = []
            self._find_all(kw, strict=strict)
            return
        # 多个写法 → 跟「所有关键词」同一条路：合并混排、一起高亮、可逐个看
        self._find_manual_words = list(words)
        self._fill_find_words_manual(words)
        self._find_only_current = True      # 就在这本里，不外扩同名变体
        self._find_all_kws(words=words, jump_near=False, auto=True,
                           strict=strict)
        # 【v0.3.23】下拉按命中多少重排：未命中的写法沉到最后并标「未命中」
        self._rank_find_words(self._upd_word_summary())
        _bits = []
        if bool(self.st.get('find_variant', True)):
            _bits.append('繁简通搜')
        if bool(self.st.get('find_alias', True)):
            _bits.append('联想词')
        _nm = sum(1 for _n in (self._find_word_counts or {}).values() if not _n)
        self.statusBar().showMessage(
            '文内查找「%s」：按%s铺开 %d 个写法，本册共 %d 处（其中 %d 个写法未命中，'
            '已排在下拉最后）——「下一个」一处一处走，也可在左边下拉里只看某一个写法'
            % (kw, '+'.join(_bits) or '原词', len(words), int(self._find_total or 0),
               _nm))

    @staticmethod
    def _hit(f, h):
        """一条命中 —— 统一成可跳转的结构。"""
        return {'path': f.get('path'), 'name': f.get('name'), 'page': h.get('page'),
                'off': h.get('off'), 'n': h.get('n'),
                'ctx': h.get('ctx') or '', 'txt_mark': f.get('txt_mark')}

    def _rebuild_find_view(self):
        """当前展示的命中列表 = 保留项 (+ 被去重项，若用户打开了开关)。"""
        self._find_view = list(getattr(self, '_find_kept', []))
        if getattr(self, '_find_show_hidden', False):
            self._find_view += list(getattr(self, '_find_hidden', []))
        self._find_hits = list(getattr(self, '_find_kept', []))   # 兼容旧引用
        self._find_total = len(self._find_view)
        if not self._find_view:
            self._find_idx = -1
        elif self._find_idx < 0 or self._find_idx >= self._find_total:
            self._find_idx = 0

    def toggle_find_hidden(self):
        """展开/收起「被去重」的命中（默认隐藏）。"""
        self._find_show_hidden = not getattr(self, '_find_show_hidden', False)
        self._rebuild_find_view()
        self._populate_find_tab()
        self._upd_find_label()
        if self._find_view:
            self._find_jump(self._find_idx if self._find_idx >= 0 else 0)
        self.statusBar().showMessage(
            '%s被去重项（共 %d 处）' % ('已展开' if self._find_show_hidden else '已收起',
                                      len(getattr(self, '_find_hidden', []))))

    def _find_targets(self):
        """batch9：单文件范围 = 当前打开的文件 + 同目录「同一本书」的 PDF/TXT 变体。

        batch29：从命中词下拉挑词时不外扩（`_find_only_current`）——那是
        "就在这本书里把这个词看一遍"，跨同名变体反而会跑题也更慢。
        """
        cur = self._find_current_path()
        if not cur:
            return []
        if getattr(self, '_find_only_current', False):
            return [cur]
        files, seen = [], set()

        def add(p):
            k = os.path.normcase(os.path.abspath(p))
            if p and k not in seen and os.path.isfile(p):
                seen.add(k)
                files.append(p)

        add(cur)
        try:
            d = os.path.dirname(os.path.abspath(cur))
            key = META.family_key(os.path.basename(cur))
            if key:
                for fn in os.listdir(d):
                    if os.path.splitext(fn)[1].lower() in ('.pdf', '.txt') \
                            and META.family_key(fn) == key:
                        add(os.path.join(d, fn))
        except OSError:
            pass
        return files

    def _find_current_path(self):
        if getattr(self, '_reader', '') == 'pdf':
            return getattr(self, '_pd_path', '')
        return getattr(self, '_text_path', '')

    def _find_all(self, kw, strict=False):
        """batch9 统一入口：当前文件 + 同书 PDF/TXT 一起找，按规则去重后列表展示。

        strict=True：只认字面（v0.3.18「繁简通搜」关掉时）。
        """
        self._find_kw = kw
        rep = []
        for p in self._find_targets():
            try:
                ext = os.path.splitext(p)[1].lower()
                cnt, hh = (self._scan_pdf(p, kw, strict=strict) if ext == '.pdf'
                           else self._scan_text(p, kw, strict=strict))
            except Exception:
                continue
            rep.append({'path': p, 'name': os.path.basename(p), 'count': cnt, 'hits': hh})
        kept, hidden = META.dedup_split(rep)          # batch9：被去重项默认隐藏
        self._find_kept = [self._hit(f, h)
                           for f in kept for h in (f.get('hits') or [])]
        self._find_hidden = [dict(self._hit(f, h), hidden=True)
                             for f in hidden for h in (f.get('hits') or [])]
        self._find_show_hidden = False
        self._rebuild_find_view()
        self._hl_kw = kw if self._find_view else ''
        self._hl_multi = []                  # batch30：单关键词查找 → 不高亮别的词
        try:
            if getattr(self, 'pd', None) is not None and getattr(self, '_reader', '') in ('pdf', 'dual'):
                self._pdf_view_active().set_highlight(self._hl_kw)
        except Exception:
            pass
        self._populate_find_tab()
        self._upd_find_label()
        if self._find_view:
            self._find_jump(0)
            self._show_nav(2)
            _msg = '文内查找：命中 %d 处（%d 个文件）' % (len(self._find_kept), len(kept))
            if self._find_hidden:
                _msg += '，另有 %d 处被去重隐藏（点「显示被去重的项」查看）' % len(self._find_hidden)
            self.statusBar().showMessage(_msg)
        else:
            cur = self._find_current_path()
            _notxt = False
            if (cur or '').lower().endswith('.pdf'):
                d = getattr(self, 'pd', None)
                if d is not None and getattr(self, '_pd_texts_doc', None) == id(d):
                    _notxt = not any((x or '').strip() for x in (self._pd_texts or []))
            if _notxt:
                self.statusBar().showMessage(
                    '这个 PDF 没有文字层，需要先做 OCR（可以交给 CathayOCR）')
            else:
                self.statusBar().showMessage('没找到：%s' % kw)

    def _scan_text(self, p, kw, strict=False):
        """扫描一个文本文件：返回 (总次数, 命中[{off,n,ctx}])。

        strict=True：只认字面（v0.3.18「繁简通搜」关掉时）。

        【v0.3.8】同样换用 _text_hit_spans（理由见 _scan_pdf）；同一个文件被多个
        词轮着扫时，"抹掉空白"的副本只做一次。
        """
        try:
            _sz = os.path.getsize(p)
            _mt = os.path.getmtime(p)
        except OSError:
            _sz = _mt = 0
        key = (os.path.normcase(p), _sz, _mt)
        cached = getattr(self, '_scan_txt_key', None)
        if cached and cached[0] == key:
            t, _fl, _cm = cached[1], cached[2], cached[3]
        else:
            t = self._norm_ws(self._read_text_file(
                p, TEXT_MAX_BYTES if _sz > TEXT_MAX_BYTES else 0))
            t = t.replace('\r\n', '\n').replace('\r', '\n')   # 对齐 QTextEdit 的换行处理
            _fl, _cm = _squash_map(t)
            self._scan_txt_key = (key, t, _fl, _cm)
        count, hits = 0, []
        for off, n in _text_hit_spans(t, kw, flat=_fl, cmap=_cm, strict=strict):
            count += 1
            if len(hits) < 500:
                hits.append({'off': off, 'n': n, 'ctx': self._ctx(t, off, n)})
        return count, hits

    def _find_jump(self, i):
        """跳到展示列表的第 i 处；命中在别的文件里就先切过去。"""
        # v0.3.15：记一下"总共跳过几回" —— 流式检索要用它判断期间用户有没有
        # 自己点过结果。自己点过就别在收尾时把人从看的地方拽回第 1 处。
        self._find_jump_seq = int(getattr(self, '_find_jump_seq', 0) or 0) + 1
        view = getattr(self, '_find_view', None) or getattr(self, '_find_hits', [])
        if not (0 <= i < len(view)):
            return
        h = view[i]
        _cur = self._find_current_path() or ''
        if h.get('path') and os.path.normcase(h['path']) != os.path.normcase(_cur):
            if not self._open_path(h['path']):            # batch10：打不开就明确提示
                self.statusBar().showMessage('打不开这条结果：%s' % (h.get('path') or ''))
                return
            QTimer.singleShot(0, self._populate_find_tab)   # 延后刷新，避免在信号里清表
        self._find_idx = i
        if h.get('page') is not None and getattr(self, 'pd', None) is not None:
            self.pgno = max(0, min(int(self.pd.page_count) - 1, int(h['page'])))
            try:
                # batch30：多词模式下 _hl_kw 是空的，这里不能拿它去重设高亮
                # （会把"所有关键词"的高亮一笔勾掉）
                _hl = list(getattr(self, '_hl_multi', None) or [])
                self._pdf_view_active().set_highlight(_hl if _hl else self._hl_kw)
                self._pdf_view_active().goto_page(self.pgno)
            except Exception:
                pass
        elif h.get('off') is not None:
            try:
                tw = self._txt_widget()
                if tw is self.view:
                    self.stack.setCurrentWidget(self.text_view)
                doc = tw.document()
                c = tw.textCursor()
                c.setPosition(min(int(h['off']), max(0, doc.characterCount() - 1)))
                # batch30：多词模式下 _hl_kw 是第一个词，长度不一定对 —— 用这条命中自己的词
                # v0.3.8：优先用"真实跨度"—— 词中间被换行切开时，它比字数长
                _kwlen = int(h.get('n') or len(h.get('kw') or self._hl_kw or ''))
                c.setPosition(min(c.position() + _kwlen,
                                  max(0, doc.characterCount() - 1)),
                              QTextCursor.MoveMode.KeepAnchor)
                tw.setTextCursor(c)
                tw.centerCursor()
            except Exception:
                pass
        mark = '（TXT）' if h.get('txt_mark') else ''
        pg = ('第 %d 页' % (int(h['page']) + 1)) if h.get('page') is not None else '文本'
        # batch30：跳过去之后标签得跟着走，否则批量检索完还停在"第 0 / N 处"
        self._upd_find_label()
        # 【v0.3.23】列表里也把这一处选中（「下一个/上一个」走的时候要看得见）
        self._nav_sync_current()
        self.statusBar().showMessage('第 %d / %d 处 ｜ %s%s ｜ %s：%s'
                                     % (i + 1, self._find_total, h.get('name') or '',
                                        mark, pg, h.get('ctx') or ''))

    def _text_step(self, forward):
        tw = self._txt_widget()
        if forward:
            found = tw.find(self._find_kw)
        else:
            found = tw.find(self._find_kw, QTextDocument.FindFlag.FindBackward)
        if not found:                       # 循环：回到头/尾再找
            cur = tw.textCursor()
            cur.movePosition(QTextCursor.MoveOperation.Start if forward
                             else QTextCursor.MoveOperation.End)
            tw.setTextCursor(cur)
            if forward:
                found = tw.find(self._find_kw)
            else:
                found = tw.find(self._find_kw, QTextDocument.FindFlag.FindBackward)
        return found

    def find_next(self):
        if self._find_total <= 0:
            if self.ed_find.text():
                self.find_run()
            return
        self._find_idx = (self._find_idx + 1) % self._find_total
        self._find_jump(self._find_idx)
        self._upd_find_label()

    def find_prev(self):
        if self._find_total <= 0:
            if self.ed_find.text():
                self.find_run()
            return
        self._find_idx = (self._find_idx - 1) % self._find_total
        self._find_jump(self._find_idx)
        self._upd_find_label()

    def _norm_ws(self, s):
        """把 PDF 取文里常见的非断行空格/全角空格归一为普通空格，便于检索。"""
        return _norm_pdf_text(s)

    def _ctx(self, t, j, n, span=30):
        a = max(0, j - span)
        b = min(len(t), j + n + span)
        s = t[a:b].replace('\n', ' ').replace('\r', ' ').strip()
        return ('…' if a > 0 else '') + s + ('…' if b < len(t) else '')

    def _pdf_texts_of(self, doc):
        """逐页抽文字层。

        大文档（>200 页）分块让出事件循环并报进度 —— 1576 页一次抽完要 1.2 秒，
        以前这段时间界面完全假死、也没有任何反馈，看起来像卡死。

        【v0.3.7】后台扫词线程抽过的书直接复用缓存：那一趟本来就要全本解析，
        这里再抽一遍等于同一本书读两次，界面还要卡一下。

        【v0.3.14】1316 页的实测：抽一遍要 3.3 秒，其中一半是"重走 page tree"
        的冤枉路 —— 循环里反复 `doc[i]` 不如一次迭代 `for page in doc`。
        """
        n = int(getattr(doc, 'page_count', 0) or 0)
        _name = ''
        try:
            _name = getattr(doc, 'name', '') or ''
        except Exception:
            _name = ''
        if _name and n:
            self._wait_warmup(_name)          # 后台正在抽同一本 → 等它，别抢 IO
            c = pdf_text_cache_get(_name)
            if c is not None and len(c) == n:
                # 【v0.3.14】直接交出缓存里那个 list，**别再 list(c) 拷一份**：
                # _pdf_flat_map 是按对象身份缓存的，每拷一份新 list 那份缓存就
                # 失效，于是 15 个词能把整本书的"抹空白副本"重算 15 遍
                # （实测每遍 0.21 s）。调用方只读不改（只有 _scan_pdf 用），
                # 共用同一份是安全的。
                return c
        out = []
        big = n > 200
        _nw = self._norm_ws
        i = 0
        try:
            for page in doc:                  # 一次迭代：每次 doc[i] 都重走 page tree
                try:
                    out.append(_nw(page.get_text() or ''))
                except Exception:
                    out.append('')
                i += 1
                if big and i % 150 == 0:
                    try:
                        self.statusBar().showMessage(
                            '正在抽取文字层… %d / %d 页' % (i, n))
                        QApplication.processEvents()
                    except Exception:
                        pass
        except Exception:
            pass
        while len(out) < n:                   # 迭代中途断了要补齐，免得后面越界
            out.append('')
        if _name and out:
            pdf_text_cache_put(_name, out)
        return out

    def _wait_warmup(self, path, max_ms=None):
        """后台正在抽这本书 → 别跟着一起抽，转事件循环等它收工。

        【v0.3.14】这不上算：1316 页的书，两个线程各解析一遍，实测谁也别想快
        —— 主线程那趟能从 3.3 s 涨到 6.1 s（磁盘来回跳、缓存互踩）。等它抽完
        直接命中缓存反而最快，而且这段时间事件循环照转、窗口不僵。
        """
        max_ms = int(max_ms or TEXT_WARMUP_MAX_WAIT_MS)
        w = getattr(self, '_warm_worker', None)
        try:
            if w is None or not w.isRunning():
                return
            if os.path.normcase(getattr(w, 'path', '') or '') != os.path.normcase(path or ''):
                return
        except Exception:
            return
        _t0 = time.time()
        _spin = 0
        while w.isRunning() and (time.time() - _t0) * 1000.0 < max_ms:
            _spin += 1
            if _spin % 10 == 0:
                try:
                    self.statusBar().showMessage('正在抽取文字层…')
                except Exception:
                    pass
            try:
                QApplication.processEvents()
            except Exception:
                pass
            time.sleep(0.01)

    def _pdf_flat_map(self, texts, path=""):
        """每页文本的"抹掉空白"副本 + 回指表。多个词轮着扫同一本书时共用一份。

        【v0.3.14】再垫一层按**路径**的缓存：对象身份那种（_pd_flat_key）在换了
        一本书再切回来时会失效，于是整本书的副本又得重算一遍。现在后台预热线程
        顺手把它做好存下来，前台查找直接取用，省下那 0.2 秒。
        """
        cur = getattr(self, '_pd_flat_key', None)
        if cur is not None and cur[0] is texts:
            return cur[1]
        if path:
            c = pdf_flat_cache_get(path)
            if c is not None and len(c) == len(texts or []):
                self._pd_flat_key = (texts, c)
                return c
        out = [_squash_map(t) for t in (texts or [])]
        self._pd_flat_key = (texts, out)
        if path and out:
            pdf_flat_cache_put(path, out)
        return out

    def _pdf_texts_for(self, p):
        """取这本书的文字层：**优先复用已经打开的那份文档**，别再 open 一遍。

        当前正在读的书，直接用 PdfView 手里那个 Document 去抽 —— 再 fitz.open
        一份等于把同一本 120 MB 的书从头解析两遍。
        """
        d = getattr(self, 'pd', None)
        if d is not None and os.path.normcase(
                getattr(self, '_pd_path', '') or '') == os.path.normcase(p):
            if self._pd_texts is None or getattr(self, '_pd_texts_doc', None) != id(d):
                try:
                    self._pd_texts = self._pdf_texts_of(d)
                except Exception:
                    self._pd_texts = []
                self._pd_texts_doc = id(d)
            return self._pd_texts
        import fitz
        doc = fitz.open(p)
        try:
            return self._pdf_texts_of(doc)
        finally:
            try:
                doc.close()
            except Exception:
                pass

    def _scan_pdf(self, p, kw, strict=False):
        """扫描一个 PDF 的文字层：返回 (总次数, 命中[{page,off,n,ctx}])。

        strict=True：只认字面（v0.3.18「繁简通搜」关掉时）。

        【v0.3.8】换成 _text_hit_spans：原词 / 繁简变体 / 抹掉空白后再连起来，
        和后台扫词线程同一把尺子。以前只做纯字面 `t.find(kw)`，繁体书里搜简体
        词一处也找不到 —— 导航条说"本书命中"，查找栏却一直停在"第 0 / 0 处"。

        【v0.3.14】取文字层抽成 _pdf_texts_for，好和多词扫描共用同一趟数据。
        """
        texts = self._pdf_texts_for(p)
        flats = self._pdf_flat_map(texts, p)
        count, hits = 0, []
        for i, t in enumerate(texts):
            _fl, _cm = flats[i] if i < len(flats) else (None, None)
            for off, n in _text_hit_spans(t, kw, flat=_fl, cmap=_cm,
                                          strict=strict):
                count += 1
                if len(hits) < 500:
                    # batch30：记下页内偏移 —— 多词合并时要靠它排出页内先后
                    # v0.3.8：n 是真实跨度，字间有换行时会比字数长
                    hits.append({'page': i, 'off': off, 'n': n,
                                 'ctx': self._ctx(t, off, n)})
        return count, hits

    def _scan_pdf_multi(self, p, words):
        """【v0.3.14】一次把好几个词全找出来：[(词, 总次数, 命中…), …]。

        以前「所有关键词」是 for kw in words: _scan_pdf(p, kw) —— K 个词就把整本
        书从头到尾翻 K 遍，绝大部分开销花在"重复遍历一遍列表"上，而不是真的
        在比字。现在**页只走一趟**，页内挨个词算。

        1316 页 × 15 个词实测：0.43 s → 0.22 s。
        """
        texts = self._pdf_texts_for(p)
        flats = self._pdf_flat_map(texts, p)
        cnts = dict((w, 0) for w in words)
        hits = dict((w, []) for w in words)
        for i, t in enumerate(texts):
            _fl, _cm = flats[i] if i < len(flats) else (None, None)
            for kw in words:
                hh = hits[kw]
                for off, n in _text_hit_spans(t, kw, flat=_fl, cmap=_cm):
                    cnts[kw] = cnts[kw] + 1
                    if len(hh) < 500:
                        hh.append({'page': i, 'off': off, 'n': n,
                                   'ctx': self._ctx(t, off, n)})
        return [(w, cnts[w], hits[w]) for w in words]

    def _find_pdf_jump(self, i):
        """兼容旧接口：跳到第 i 处。"""
        self._find_jump(int(i))

    def _iter_pdf_pages(self, p, _total_out=None):
        """【v0.3.15】逐页交出 `(页号, 正文, 紧凑串, 回指表)`，**能交多少交多少**。

        和 `_pdf_texts_for` 的区别在于它不要求"整本书抽完才给你"：抽出来的页
        当场往外递，调用方可以立刻拿去找词。这才叫"边抽边找"——以前要先等完整
        本（1100 页 1.2 秒），这段时间界面上什么都没有。

        缓存里有就直接交缓存；没有才真抽，抽完顺手把整份塞回缓存（下次打开、
        后台预热、单字查找都能白嫖这一趟）。
        """
        def _from_cache(texts):
            try:
                fl = pdf_flat_cache_get(p)
            except Exception:
                fl = None
            if fl is not None and len(fl) != len(texts):
                fl = None
            if _total_out is not None:
                _total_out.append(len(texts))
            mine = []
            for _i, _t in enumerate(texts):
                if fl is None:
                    _fl, _cm = _squash_map(_t)
                else:
                    _fl, _cm = fl[_i]
                mine.append((_fl, _cm))
                yield (_i, _t, _fl, _cm)
            # 正文是缓存的、副本却缺（比如只被后台预热过一半）→ 顺手补存回去，
            # 省得后面单字查找再为整本书重算这一遍（1100 页 0.17 秒）
            if fl is None and mine:
                try:
                    pdf_flat_cache_put(p, mine)
                except Exception:
                    pass

        try:
            c = pdf_text_cache_get(p)
        except Exception:
            c = None
        if c is None:
            # 后台正在抽同一本 → 等它，别两边各解析一遍（v0.3.14 的老规矩）
            self._wait_warmup(p)
            try:
                c = pdf_text_cache_get(p)
            except Exception:
                c = None
        if c is not None:
            for _tup in _from_cache(c):
                yield _tup
            return

        doc, own = None, False
        texts, flats = [], []
        try:
            d = getattr(self, 'pd', None)
            if d is not None and os.path.normcase(
                    getattr(self, '_pd_path', '') or '') == os.path.normcase(p):
                doc = d                      # 正在读的那本，别再 open 一遍 2GB
            else:
                import fitz
                doc = fitz.open(p)
                own = True
            if _total_out is not None:
                _total_out.append(int(getattr(doc, 'page_count', 0) or 0))
            _nw = self._norm_ws
            for _pg in doc:                  # 一次迭代；每次 doc[i] 都要重走 page tree
                try:
                    t = _nw(_pg.get_text() or '')
                except Exception:
                    t = ''
                sq = _squash_map(t)
                texts.append(t)
                flats.append(sq)
                yield (len(texts) - 1, t, sq[0], sq[1])
        finally:
            # 抽到哪里都留一份：即便中途被打断，也别让后面的人再从头抽一遍
            if texts:
                try:
                    pdf_text_cache_put(p, texts)
                    pdf_flat_cache_put(p, flats)
                except Exception:
                    pass
            if own and doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass

    def _iter_find_hits(self, p, words, cap=500, flush_ms=None, strict=False):
        """【v0.3.15】流式多词扫描：`yield (已扫页数, 总页数, 这段新命中 [(hit,kw),…])`。

        页一趟走完，页内挨个词算（v0.3.14 的法子保留）；新增的是**按时间分喘**：
        攒够 FIND_FLUSH_MIN_MS 就把手上的结果吐一次给界面，所以第一批结果几十
        毫秒就能看见，而不是等整本扫完。每个词的明细最多留 cap 条。
        """
        flush_ms = int(flush_ms if flush_ms is not None
                       else globals().get('FIND_FLUSH_MIN_MS', 80))
        got = dict((w, 0) for w in words)
        total_out = []
        batch = []
        scanned = 0
        _t = time.time()
        _first = True
        for i, t, _fl, _cm in self._iter_pdf_pages(p, total_out):
            scanned = i + 1
            for kw in words:
                g = got[kw]
                if g >= cap:
                    continue
                for off, ln in _text_hit_spans(t, kw, flat=_fl, cmap=_cm,
                                              strict=strict):
                    if g >= cap:
                        break
                    g += 1
                    batch.append(({'page': i, 'off': off, 'n': ln,
                                   'ctx': self._ctx(t, off, ln)}, kw))
                got[kw] = g
            if batch and (((time.time() - _t) * 1000.0 >= flush_ms)
                          or (_first and scanned >= 20)):
                # 头一批交代勤一点（让人立刻看见有东西），后面按时间喘息
                yield (scanned, (total_out[0] if total_out else scanned), batch)
                batch = []
                _t = time.time()
                _first = False
        if batch:
            yield (scanned, (total_out[0] if total_out else scanned), batch)

    def _nav_count(self):
        """导航「查找」页签里现有行数（列表还没建起来时为 -1）。"""
        lst = getattr(self, '_nav_find', None)
        try:
            return int(lst.count()) if lst is not None else -1
        except Exception:
            return -1

    def _find_row_text(self, h, ncur):
        """查找面板里一行长什么样。ncur = 当前文件路径的 normcase。"""
        head = ('第 %d 页' % (int(h['page']) + 1)) if h.get('page') is not None else '文本'
        if os.path.normcase(h.get('path') or '') != ncur:
            head = '%s%s ｜ %s' % (h.get('name') or '',
                                   '（TXT）' if h.get('txt_mark') else '', head)
        if h.get('hidden'):
            head += '（已去重）'
        return '%s ｜ %s' % (head, h.get('ctx') or '')

    def _populate_find_tab(self):
        self._build_nav_dock()
        lst = self._nav_find
        lst.clear()
        ncur = os.path.normcase(self._find_current_path() or '')
        for k, h in enumerate(getattr(self, '_find_view', [])):
            it = QListWidgetItem(self._find_row_text(h, ncur))
            it.setData(Qt.ItemDataRole.UserRole, k)
            lst.addItem(it)
        self._upd_find_hidden_btn()
        self._nav_sync_current()        # v0.3.23：重建后把当前那一处重新选中

    def _populate_find_from(self, start):
        """【v0.3.15】只把 [start:] 这些**新找到**的结果追加进列表。

        流式检索要一段一段刷新，每来一批就整表重建一遍的话，最后那批要重排
        几千行 —— 白扔掉几十毫秒不说，选中项还会被清掉。
        """
        view = getattr(self, '_find_view', []) or []
        if start >= len(view):
            return
        self._build_nav_dock()
        lst = self._nav_find
        # 行数对不上说明中间被别处清过表（换文件、去重开关），老老实实重建
        if self._nav_count() != start:
            self._populate_find_tab()
            return
        ncur = os.path.normcase(self._find_current_path() or '')
        for k in range(start, len(view)):
            it = QListWidgetItem(self._find_row_text(view[k], ncur))
            it.setData(Qt.ItemDataRole.UserRole, k)
            lst.addItem(it)
        self._upd_find_hidden_btn()
        self._nav_sync_current()        # v0.3.23：流式追加后别把选中项弄丢

    def _upd_find_hidden_btn(self):
        """被去重项那个按钮：没有要隐藏的就别占地方。"""
        b = getattr(self, '_btn_find_hidden', None)
        if b is None:
            return
        nh = len(getattr(self, '_find_hidden', []))
        b.setVisible(nh > 0)
        b.setText(('▾ 收起被去重的 %d 项' if getattr(self, '_find_show_hidden', False)
                   else '▸ 显示被去重的 %d 项') % nh)

    def _thumb_add_one(self):
        """渲染下一页缩略图（懒加载，最多 40 页）。成功返回 True。"""
        d = getattr(self, 'pd', None)
        lst = getattr(self, '_thumb_list', None)
        if d is None or lst is None:
            return False
        total = int(getattr(d, 'page_count', 0) or 0)
        i = self._thumbs_added
        if i >= 40 or i >= total:
            return False
        try:
            import fitz
            pm = d[i].get_pixmap(matrix=fitz.Matrix(0.15, 0.15))
            img = QImage(pm.samples, pm.width, pm.height, pm.stride,
                         QImage.Format.Format_RGB888).copy()
            it = QListWidgetItem(QIcon(QPixmap.fromImage(img)), '第 %d 页' % (i + 1))
            it.setData(Qt.ItemDataRole.UserRole, i)
            lst.addItem(it)
        except Exception:
            pass
        self._thumbs_added = i + 1
        return True

    def _thumb_step(self):
        if not self._thumb_add_one():
            self._stop_thumb_timer()
            lst = getattr(self, '_thumb_list', None)
            if lst is not None and lst.count():
                self.statusBar().showMessage('缩略图已就绪：%d 页' % lst.count())

    def _stop_thumb_timer(self):
        t = getattr(self, '_thumb_timer', None)
        if t is not None:
            t.stop()

    def _start_thumb_load(self):
        lst = getattr(self, '_thumb_list', None)
        if lst is None:
            return
        lst.clear()
        self._thumbs_added = 0
        if getattr(self, '_thumb_timer', None) is None:
            from PyQt6.QtCore import QTimer as _QT
            self._thumb_timer = _QT(self)
            self._thumb_timer.setInterval(0)
            self._thumb_timer.timeout.connect(self._thumb_step)
        self._thumb_timer.start()

    def _thumb_clicked(self, item):
        """点缩略图 → 跳该页。"""
        i = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        d = getattr(self, 'pd', None)
        if i is None or d is None:
            return
        self.pgno = max(0, min(int(d.page_count) - 1, int(i)))
        self.pdf_view.goto_page(self.pgno)

    def toggle_thumbs(self):
        """Ctrl+Shift+T：打开/隐藏右侧缩略图面板（PDF/EPUB，懒加载前 40 页）。"""
        _mw = self._tab_current_mw()      # v0.3.21：多标签 → 交给当前那本
        if _mw is not None and _mw is not self:
            return _mw.toggle_thumbs()
        d = getattr(self, 'pd', None)
        if d is None or int(getattr(d, 'page_count', 0) or 0) <= 0:
            self.statusBar().showMessage('缩略图只对已打开的 PDF / EPUB 有效'
                                         '（先点 📖 在阅读区打开或 Ctrl+E）')
            return
        if (self._nav_dock is not None and self._nav_dock.isVisible()
                and self._nav_tabs is not None and self._nav_tabs.currentIndex() == 1):
            self._nav_dock.hide()
            self._stop_thumb_timer()
            self.statusBar().showMessage('已隐藏缩略图（Ctrl+Shift+T 再开）')
            return
        self._show_nav(1)
        self.statusBar().showMessage('缩略图加载中…（Ctrl+Shift+T 隐藏）')

    def preview_here(self):
        r = self._cur()
        if not r:
            return
        p = os.path.join(r.get('dir') or '', r.get('name') or '')
        ext = (r.get('ext') or '').lower()
        try:
            if ext in ('.txt', '.md', '.json', '.csv'):
                self._show_text_file(p)
                if ext == '.txt':
                    self._maybe_auto_dual(p)
                self._hist_add()
                self.statusBar().showMessage('已打开文本（只读，全文；超大文件最多前 64 MB）')
            elif ext in ('.pdf', '.epub', '.xps', '.cbz', '.mobi', '.fb2', '.svg'):
                if ext == '.pdf':
                    # 先验文件头/结尾：坏 PDF 会让 PyMuPDF 直接 abort（连异常都不抛）
                    with open(p, 'rb') as f:
                        head = f.read(5)
                        f.seek(max(0, os.path.getsize(p) - 2048))
                        tail = f.read()
                    if not head.startswith(b'%PDF') or b'%%EOF' not in tail:
                        raise ValueError('PDF 结构不完整（可先用 CathayRepair 修一下）')
                d = self._open_fitz(p)
                self._set_pdf(d, p)
                self.pgno = 0
                try:
                    _mk = (C.load_settings().get('marks') or {}).get(p) or {}
                    self.pgno = max(0, min(d.page_count - 1, int(_mk.get('page') or 0)))
                except Exception:
                    pass
                self._pdf_show()
                self._after_pdf_open()
                self._hist_add()
                self._stat_open(p)
            else:
                self.stack.setCurrentWidget(self.text_view)
                self.view.setPlainText('这个格式（%s）暂不支持在此预览，'
                                       '点「↗ 用外部程序打开」。' % ext)
                self._pdf_stop()
                self._hide_nav()
        except Exception as e:
            self.stack.setCurrentWidget(self.text_view)
            self.view.setPlainText('打不开：%s: %s' % (type(e).__name__, e))

    def _path(self):
        r = self._cur()
        return os.path.join(r.get('dir') or '', r.get('name') or '') if r else ''

    def _now_path(self):
        """阅读区**正打开着**的那个文件（已经是换算过的真实路径）。"""
        return (getattr(self, '_pd_path', '') or getattr(self, '_text_path', '')
                or '')

    def _target_path(self):
        """「打开所在文件夹」「用外部程序打开」该认哪一个文件。

        这两个按钮以前只认左栏选中行（`_path()`）——可从 CathaySearch 跳过来、
        从"最近打开"重开、或者命令行带过来的书，左栏那一行常常压根不是它：
        `open_path_in_reader` 只把**同文件夹前 300 个**文件装进 rows，大文件夹
        （书库里 48% 的文件都在超过 300 个文件的大文件夹里）里排后面的就匹配不上，
        于是它退而选中第 0 行 —— 点"打开所在文件夹"打开的就成了别人的文件夹。

        现在先看阅读区正开着的那本（那一定是用户想找的那本），没有再退回列表行。
        """
        p = self._now_path()
        if p and os.path.isfile(p):
            return p
        r = self._cur()
        if r:
            return self._real(os.path.join(r.get('dir') or '',
                                           r.get('name') or ''))
        return p or ''

    @staticmethod
    def _reveal_in_explorer(p):
        """在资源管理器里打开文件夹**并把这个文件选中**（不是只开文件夹）。

        书库一个文件夹动辄几千个文件，只开文件夹等于让人自己找。
        路径太长（Windows 那道 260 字的坎）时 explorer 会静默失败 ——
        这时返回 False，让调用方退回"只开文件夹"。
        """
        try:
            import subprocess
            if len(p) > 250:
                return False
            subprocess.Popen(['explorer', '/select,', os.path.normpath(p)])
            return True
        except Exception:
            return False

    def open_external(self):
        p = self._target_path()
        if p and os.path.isfile(p):
            try:
                os.startfile(p)
            except Exception as e:
                QMessageBox.warning(self, '提示', '打开失败：%s' % e)
        else:
            QMessageBox.information(
                self, '提示', '这个文件现在打不开：\n%s\n\n（盘没插？还是书库挪走过、'
                              '索引里记的还是老位置？）' % (p or '（没选中文件）'))

    def open_folder(self):
        p = self._target_path()
        if not p:
            QMessageBox.information(self, '提示',
                                    '先在列表里选一本书，或在阅读区打开一个文件')
            return
        d = os.path.dirname(p)
        if not d or not os.path.isdir(d):
            QMessageBox.information(
                self, '提示', '这个文件夹现在打不开：\n%s\n\n（盘没插？还是书库挪走过、'
                              '索引里记的还是老位置？）' % d)
            return
        if os.path.isfile(p):
            # 先试"打开并选中"；路径太长 explorer 不干，就退回只开文件夹
            if self._reveal_in_explorer(p):
                return
            try:
                os.startfile(d)
            except Exception as e:
                QMessageBox.warning(self, '提示', '打开失败：%s' % e)
            return
        # 文件夹还在、文件没了：照样把文件夹打开，但得说一声，不然以为点错了
        try:
            os.startfile(d)
            self.statusBar().showMessage(
                '文件已经不在了：%s（文件夹照旧打开）' % os.path.basename(p), 4000)
        except Exception as e:
            QMessageBox.warning(self, '提示', '打开失败：%s' % e)


def _json_val_str(v):
    """JSON 叶子值 → 显示文本（字符串截断 200 字，附带类型）。"""
    if isinstance(v, str):
        s = v if len(v) <= 200 else v[:200] + '…（共 %d 字）' % len(v)
        return 'str: ' + s
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'bool: ' + ('true' if v else 'false')
    if isinstance(v, int):
        return 'int: %d' % v
    if isinstance(v, float):
        return 'float: %g' % v
    return str(v)


def _ts(t):
    try:
        import time
        return time.strftime('%Y-%m-%d %H:%M', time.localtime(float(t)))
    except Exception:
        return ''


# ---------------------------------------------------------------- 自检
def selftest():
    import shutil
    import tempfile
    log = []

    def ok(c, m):
        log.append(('OK  ' if c else 'FAIL') + ' ' + m)
        return bool(c)

    _plat = os.environ.get('CV_PLATFORM')
    if _plat is None:
        # Windows 用原生平台；offscreen 在本机 PyQt6 6.10 + Python 3.14 下会在 MainWindow 构造处原生崩溃
        _plat = '' if sys.platform.startswith('win') else 'offscreen'
    if _plat:
        os.environ['QT_QPA_PLATFORM'] = _plat
    else:
        os.environ.pop('QT_QPA_PLATFORM', None)
    app = QApplication.instance() or QApplication(sys.argv)   # 必须持引用，否则被 GC → 原生崩溃
    base = tempfile.mkdtemp(prefix='cv_gui_')
    src = os.path.join(base, '书库')
    os.makedirs(os.path.join(src, '子', '孙'), exist_ok=True)
    for n in ('甲书.pdf', '甲书_【繁转简】.txt', '乙书.epub'):
        open(os.path.join(src, n), 'wb').write(b'x' * 64)
    open(os.path.join(src, '子', '孙', '丙书.txt'), 'wb').write(b'y' * 64)
    db = os.path.join(base, 'idx.db')
    r = C.build([src], db)
    ok(r['files'] == 4, '建库 4 个文件 → %d' % r['files'])
    st = C.load_settings()
    st.update({'primary_db': db, 'roots': [src]})
    w = MainWindow(st)
    ok(w.tb.columnCount() == 3, '主窗口构建：列表 3 列')
    _hd = [w.tb.horizontalHeaderItem(i).text() for i in range(w.tb.columnCount())]
    ok(_hd == ['序号', '文件名', '上级文件夹'], '列表表头：%s' % _hd)
    w.ed_kw.setText('甲书')
    w.do_search()
    ok(w.tb.rowCount() >= 1 and len(C.search(db, '甲书')) >= 2,
       '界面搜索「甲书」→ %d 行（原始 %d 条）' % (w.tb.rowCount(), len(C.search(db, '甲书'))))
    w.tb.setCurrentCell(0, 0)
    w.on_pick()
    ok('甲书' in w.lb_info.text(), '选中行显示详情')
    w._show_text_file(os.path.join(src, '甲书_【繁转简】.txt'))   # 直接走文本读取通路
    ok('x' * 10 in w.view.toPlainText(), '文本预览能读出内容')
    wz = Wizard(None, st, first_run=True)
    ok(wz.lst.count() >= 1 and wz.stack.count() == 3, '向导：目录列表 + 3 页')
    ok(hasattr(wz, 'rb_new') and hasattr(wz, 'rb_use'), '向导：自建 / 用已有 两种模式')
    wz.close()
    # v0.3.18：文内查找的繁简通搜 / 联想词靠这张表 —— 打包后它躺在
    # _internal_viewer/config 下，路径没接对的话功能就成了摆设，所以在这里卡一道。
    _csv = QE.csv_path()
    ok(bool(_csv), 'CBDB 别名表就位 → %s' % (_csv or '(没找到，联想词会只剩内置表)'))
    if _csv:
        ok(QE.load_table().groups() > 1000,
           '别名表读得出内容 → %d 人' % QE.load_table().groups())
    try:
        import opencc                          # noqa: F401
        opencc.OpenCC('s2t')
        opencc.OpenCC('t2s')
        ok(True, 'OpenCC 就位（繁简通搜靠它）')
        # v0.3.19 补充异名表（地名/机构/译名/人物）：跟 CBDB 那张表一个道理，
        # 打包后在 _internal_viewer/config/alias_extra/ 下，路径找不对就是空壳
        _ex = QE.extra_dir()
        ok(bool(_ex) and os.path.isdir(_ex),
           '补充异名表目录就位 → %s' % (_ex or '(没找到)'))
        _srcs = QE.source_names()
        ok(len(_srcs) >= 4, '补充异名表来源：%s' % ('、'.join(_srcs) or '(无)'))
        _n = sum(t.groups() for t in QE.extra_tables())
        ok(_n > 100, '补充异名表读得出内容 → %d 组' % _n)
        ok('北京' in QE.expand('北平'),
           '补充异名表真的生效：搜「北平」→ %s' % '、'.join(QE.expand('北平')[:5]))
    except Exception as e:
        ok(False, 'OpenCC 就位（繁简通搜靠它） → %s' % e)
    w.close()
    shutil.rmtree(base, ignore_errors=True)
    txt = '\n'.join(log) + '\nresult = %s\n' % ('OK' if all(l.startswith('OK') for l in log) else 'FAIL')
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    print('%s %s' % (APP_TITLE, APP_VERSION))
    print(txt)
    try:
        open(os.path.join(C.app_dir(), '_selftest_gui.txt'), 'w', encoding='utf-8').write(txt)
    except Exception:
        pass


def cli_file_args(argv=None):
    """命令行里真正是**文件**的那些参数（batch31）。

    `--page N / --hits X / --words Y` 的值是参数不是书，必须连值一起排掉，
    否则 `--hits xxx.json` 会被当成"要打开的第 2 个文件"（batch28 的坑）。
    """
    args = sys.argv[1:] if argv is None else list(argv)
    skip = set()
    for flag in ('--page', '--hits', '--words'):
        if flag in args:
            try:
                i = args.index(flag)
                skip.add(i)
                skip.add(i + 1)
            except Exception:
                pass
    out = []
    for k, a in enumerate(args):
        if k in skip or str(a).startswith('-'):
            continue
        try:
            if os.path.isfile(a):
                out.append(os.path.abspath(a))
        except Exception:
            continue
    return out


def _cli_has_file(argv=None):
    """这次启动命令行里带了要打开的文件吗？决定要不要立刻弹阅读器窗口。"""
    return bool(cli_file_args(argv))


# ------------------------------------------------------------------ 统一主题
# 【CathayHub 整合】与 CathayHub Search 用**同一份**外观：同目录下
# runtime/hub_theme.qss 优先（改一个文件两边都变），没有就用这份内置的。
HUB_QSS = """
QWidget { font-family: "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC";
          font-size: 9pt; color: #1F2430; }
QMainWindow, QDialog { background: #F7F8FA; }
QTreeWidget, QTableWidget, QListWidget, QTreeView, QTableView {
    background: #FFFFFF; alternate-background-color: #F3F6FA;
    selection-background-color: #0B5CAB; selection-color: #FFFFFF; }
QHeaderView::section { background: #EEF2F7; padding: 4px 6px; border: 0;
                       border-right: 1px solid #DCE3EC; }
QPushButton { background: #FFFFFF; border: 1px solid #C9D3E0;
              border-radius: 4px; padding: 4px 10px; }
QPushButton:hover { background: #EAF2FC; border-color: #0B5CAB; }
QPushButton:pressed { background: #D8E6F8; }
QPushButton:disabled { color: #9AA5B1; background: #F2F4F7; }
QLineEdit, QComboBox, QTextEdit, QPlainTextEdit {
    background: #FFFFFF; border: 1px solid #C9D3E0; border-radius: 4px;
    padding: 3px 5px; }
QStatusBar { background: #EEF2F7; }
QToolTip { background: #FFFFF0; color: #1F2430; }
"""


def apply_hub_theme(app):
    """套上统一主题。找不到共用 qss 就用内置的，绝不因此起不来。"""
    qss = HUB_QSS
    try:
        p = C.shared_runtime('hub_theme.qss')
        if os.path.isfile(p):
            with open(p, encoding='utf-8') as f:
                qss = f.read()
    except Exception:
        pass
    try:
        app.setStyleSheet(qss)
    except Exception:
        pass


def _ipc_dir():
    """实例之间的"信箱"目录：后来者把命令行写成文件，先到者取走。

    为什么不用 QLocalServer/Socket：那条路依赖 Qt 的 socket notifier，
    在本项目的自检环境里会把主线程带崩（无 traceback，进程直接没），
    排查成本高、收益却只是省几毫秒。文件信箱的好处是看得见、删得掉、
    排错时直接用记事本打开就行；代价是 0.4 秒轮询延迟，用户无感。
    """
    try:
        d = C.shared_runtime('incoming')
        os.makedirs(d, exist_ok=True)
        return d
    except Exception:
        try:
            import tempfile
            d = os.path.join(tempfile.gettempdir(), 'CathayHub_viewer_incoming')
            os.makedirs(d, exist_ok=True)
            return d
        except Exception:
            return ''


def _ipc_try_send(argv, wait=1.6):
    """已经有实例在跑，就把命令行丢进信箱，自己收工。

    CathaySearch 每次双击结果都是起一个新进程——没有这一步，"连着打开
    几本书"就会变成"桌面上冒出好几个窗口"（用户原话）。

    判定"有没有人接"的办法很朴素：写完文件盯着它，被读走了（文件没了）
    说明有人接，本进程退出；盯到超时还没人动，那就自己扛起来（正常启动）。
    """
    d = _ipc_dir()
    if not d:
        return False
    fn = os.path.join(d, 'cmd_%d_%d.json' % (os.getpid(), int(time.time() * 1000)))
    tmp = fn + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(list(argv), f, ensure_ascii=False)
        os.replace(tmp, fn)                 # 原子投递，别让人读到半截
        import time as _t
        t0 = _t.time()
        while _t.time() - t0 < wait:
            # 被吃掉了 = 有人接；改名为 .done = 有人接、只是删不掉
            if not os.path.isfile(fn) or os.path.isfile(fn + '.done'):
                return True
            _t.sleep(0.05)
        try:
            os.remove(fn)                   # 没人接，别留垃圾
        except Exception:
            try:
                os.replace(fn, fn + '.dead')
            except Exception:
                pass
    except Exception:
        pass
    return False


def prewarm_pdf_engine():
    """【v0.3.7】开窗口之后、用户点书之前，先把 PDF 引擎悄悄预热好。

    PyMuPDF（fitz）和 OpenCC（繁简转换）都是**用到才 import** 的，而第一次
    import 要把几十 MB 的 DLL / 词典从磁盘拉进来。以前这笔钱算在"打开第一本
    PDF"头上，于是第一本总是明显比后面的慢。现在挪到启动后空闲时做掉。
    """
    def _job():
        try:
            import fitz          # noqa: F401
        except Exception:
            pass
        try:
            import opencc        # noqa: F401
            opencc.OpenCC('s2t')
            opencc.OpenCC('t2s')
        except Exception:
            pass

    try:
        threading.Thread(target=_job, name='cv-prewarm', daemon=True).start()
    except Exception:
        pass


def main():
    os.environ.setdefault('QT_QPA_PLATFORM', os.environ.get('QT_QPA_PLATFORM', ''))
    if '--fts-worker' in sys.argv:
        try:
            i = sys.argv.index('--fts-worker')
            job = sys.argv[i + 1]
        except Exception:
            return 2
        try:
            return fts_run(job)
        except Exception:
            return 3
    app = QApplication(sys.argv)
    apply_hub_theme(app)
    # v0.3.1 单实例：后面启动的同款都把命令行交给已经在跑的这个，然后退出
    if _ipc_try_send(sys.argv):
        return 0
    st = C.load_settings()
    # 统一入口（Launcher）建过库的话，直接认过来 —— 下面那个首启向导就不会弹了
    st, adopted = C.adopt_hub_db(st)
    win = MainWindow(st)
    if adopted:
        win._refresh_status()
    win._ipc_setup()          # 当先到的那个：之后启动的都把活塞给它
    win.show()
    prewarm_pdf_engine()      # v0.3.7：趁空闲把 PDF/繁简引擎预热好，第一本书不再慢
    if not os.path.isfile(st.get('primary_db') or ''):
        wz = Wizard(win, st, first_run=True)
        if wz.exec() and wz.result_info:
            if wz.result_info.get('used_existing'):
                win.db = wz.result_info['db']
                win._refresh_status()
            else:
                win.st = wz.result_info.get('settings', st)
                win.db = win.st.get('primary_db', win.db)
                win._refresh_status()
    try:
        if win._restore_layout():        # batch9：恢复上次布局；上次拆窗则继续拆
            # batch31：今天这次启动**没有要打开的文件**时别急着把阅读器窗口
            # 弹出来 —— 用户口径是"启动时只显示文件列表"。空窗让读者盯着一块
            # 白板，不如等他真点开书时再拆（见 _expand_reader）。
            if _cli_has_file():
                win.start_two_window_mode()
            else:
                win._pending_two_window = True
    except Exception:
        pass
    try:
        # batch24：趁窗口还没画出来就把空闲态的窄宽度定好，避免先宽后窄闪一下。
        win._collapse_reader_if_idle()
    except Exception:
        pass
    try:
        # batch22：启动时没打开文件 → 收起阅读区（等窗口尺寸定下来再收）。
        # 命令行/双击带文件的情况，open_cli_arg 会稍后打开文件并把阅读区展开。
        # batch24：首帧布局可能还没完成（分裂条尺寸为 0），这里重试几次直到收起来。
        def _try_collapse(_n=0):
            try:
                win._collapse_reader_if_idle()
            except Exception:
                pass
            if getattr(win, '_reader_collapsed', False) or _n >= 12:
                return
            try:
                QTimer.singleShot(120, lambda: _try_collapse(_n + 1))
            except Exception:
                pass

        QTimer.singleShot(0, lambda: _try_collapse())
    except Exception:
        pass
    sys.exit(app.exec())


def selftest2():
    """稳当版自检：每步先落盘（offscreen 下旧自检会原生死锁，故另起一套）。"""
    import shutil
    import tempfile
    log = []
    REP = os.path.join(C.app_dir(), '_selftest_gui.txt')

    def ok(c, m):
        log.append(('OK  ' if c else 'FAIL') + ' ' + m)
        try:
            with open(REP, 'w', encoding='utf-8') as f:
                f.write('\n'.join(log) + '\n')
        except Exception:
            pass
        return bool(c)

    _plat = os.environ.get('CV_PLATFORM')
    if _plat is None:
        _plat = '' if sys.platform.startswith('win') else 'offscreen'
    if _plat:
        os.environ['QT_QPA_PLATFORM'] = _plat
    else:
        os.environ.pop('QT_QPA_PLATFORM', None)
    from PyQt6.QtWidgets import QApplication
    # app 必须立即持引用（QApplication(app) 的返回值被丢弃 → Python 包装对象被 GC → 原生死锁）
    app = QApplication.instance() or QApplication(sys.argv)
    base = tempfile.mkdtemp(prefix='cv2_')
    src = os.path.join(base, '书库')
    os.makedirs(os.path.join(src, '子', '孙'), exist_ok=True)
    for n in ('甲书.pdf', '甲书_【繁转简】.txt', '乙书.epub'):
        open(os.path.join(src, n), 'wb').write(b'x' * 64)
    open(os.path.join(src, '子', '孙', '丙书.txt'), 'wb').write(b'y' * 64)
    db = os.path.join(base, 'idx.db')
    r = C.build([src], db)
    ok(r['files'] == 4, '建库 4 个文件 → %d' % r['files'])
    C.save_settings({'primary_db': db, 'roots': [src]})
    st = C.load_settings()
    w = MainWindow(st)
    ok(True, '主窗口')
    w.ed_kw.setText('甲书')
    w.do_search()
    ok(len(w.rows) >= 1 and len(C.search(db, '甲书')) >= 2,
       '搜索「甲书」→ %d 行（原始 %d 条）' % (len(w.rows), len(C.search(db, '甲书'))))
    ti = next((i for i, r in enumerate(w.rows)
               if (r.get('name') or '').lower().endswith('.txt')), 0)
    w.tb.setCurrentCell(ti, 0)      # 挑 txt：假 PDF 会让 PyMuPDF 直接 abort
    w.on_pick()
    ok(bool(getattr(w, 'meta', None)), '元数据面板')
    ok(w.cb_ver.count() >= 1, '版本下拉 %d 项' % w.cb_ver.count())
    w.copy_cite()
    ok(bool(QApplication.clipboard().text()), '复制引用')
    w.cb_theme.setCurrentIndex(2)
    w.sp_font.setValue(16)
    w.apply_theme()
    ok(True, '主题/字号')
    w.preview_here()
    ok(len(w.view.toPlainText()) > 0, '预览')
    wz = Wizard(w, st)
    ok(wz.stack.count() == 3, '向导 %d 页' % wz.stack.count())
    wz.close()

    # ---- batch29：命中词进查找框 + 打开不重复装载 ----
    ok(hasattr(w, 'cb_find_word'), '查找框旁有命中词下拉')
    ok(hasattr(w, '_pdf_ready'), '打开路径有重复装载防护')
    w._fill_find_words(None)
    ok(w.cb_find_word.isHidden(), '无命中词时下拉收起')
    ok('Ctrl+F' in w.ed_find.placeholderText(), '无命中词时提示语复原')
    _j = os.path.join(os.environ.get('TEMP') or os.getcwd(), '_st29.json')
    try:
        HITS.dump(_j, 'Y:/a.pdf',
                  [('吴佩孚', [12, 45], 27), ('子玉', [7], 3)])
        _s = HITS.HitSet.load(_j)
        w._fill_find_words(_s)
        ok(w.cb_find_word.count() == 3, '命中词摆进下拉（%d 项）'
           % w.cb_find_word.count())
        ok('吴佩孚' in w.lb_hit_words.text()
           and '子玉' in w.lb_hit_words.text(),
           '本书命中一行写全：%s' % w.lb_hit_words.text())
        ok(not w.cb_find_word.isHidden(), '有命中词时下拉展开')
    finally:
        try:
            os.remove(_j)
        except OSError:
            pass
    w.close()
    shutil.rmtree(base, ignore_errors=True)
    good = all(x.startswith('OK') for x in log)
    ok(good, 'result')
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    print('%s %s' % (APP_TITLE, APP_VERSION))
    print('\n'.join(log))
    print('result = %s' % ('OK' if good else 'FAIL'))
    sys.exit(0 if good else 1)


if __name__ == '__main__':
    if '--selftest2' in sys.argv:
        selftest2()
    elif '--selftest' in sys.argv:
        selftest()
    else:
        main()
