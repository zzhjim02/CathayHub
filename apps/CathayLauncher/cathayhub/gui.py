# -*- coding: utf-8 -*-
"""CathayHub Launcher —— 启动器界面。

两副面孔：
  · 第一次打开：走"首次运行向导"，自动检测 + 让你挑外观。
  · 之后：就是三个大按钮（搜索 / 阅读 / 索引），左上角留一个"再跑一次向导"的入口。

所有面向用户的文字都尽量说人话：不说"索引路径"，说"书库文件夹"；
不说"转译失败"，说"文件挪过地方，我已经帮你算好新位置"。
"""
import json
import os
import subprocess
import time

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                             QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                             QFormLayout, QFrame, QGridLayout, QGroupBox,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QListWidget, QMainWindow, QMessageBox,
                             QPlainTextEdit, QProgressBar, QPushButton,
                             QRadioButton, QStatusBar, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget,
                             QWizard, QWizardPage)

from . import config, detect, filelib
from . import path_translator as pt

LEVEL_TEXT = {'critical': '主要', 'optional': '次要',
              'dual': '次要（有就显示）'}


def _settings():
    """Launcher 自己的小账本（真正干活的是 config.load_settings）。"""
    return config.load_settings()


def _save_settings(d):
    ok, _err = config.save_settings(d)
    return ok


def launch(kind, path=None):
    """打开 CathayHub 里的另一个程序。"""
    exe = config.exe_path(kind)
    name = {'search': '搜索', 'viewer': '阅读', 'indexer': '索引'}.get(kind, kind)
    if not exe:
        QMessageBox.information(None, config.APP_TITLE,
                                '没找到「%s」那个程序。\n'
                                '请确认它和本程序放在同一个文件夹里。' % name)
        return False
    try:
        args = [exe] + ([path] if path else [])
        subprocess.Popen(args, cwd=config.app_dir())
        return True
    except Exception as e:
        QMessageBox.warning(None, config.APP_TITLE, '打开失败：%s' % e)
        return False


def recent_files(n=5):
    """最近打开过的文件（从 Viewer 那份设置里读）。"""
    try:
        with open(config.viewer_settings_path(), 'r', encoding='utf-8') as f:
            st = json.load(f).get('stats') or {}
    except Exception:
        return []
    rows = []
    for p, e in st.items():
        if not isinstance(e, dict):
            continue
        rows.append((str(e.get('last') or ''), p))
    rows.sort(reverse=True)
    return [p for _, p in rows[:n]]


# ---------------------------------------------------------------- 后台线程
# 关窗时还没跑完的线程寄养在这里：QThread 在运行中被销毁不是抛异常，
# 是把进程直接带崩（用户看到的就是"关个向导，整个程序没了"）。
# 宁可让它在外面跑完自生自灭，也不能让它跟着窗口一起没。
_ORPHAN_THREADS = []


def _give_up_thread(w, ms=3000):
    """收一个后台线程：先好好劝（stop + wait），劝不动就摘出去寄养。"""
    if w is None:
        return
    try:
        if w.isRunning():
            stop = getattr(w, 'stop', None)
            if callable(stop):
                stop()
            w.wait(ms)
    except Exception:
        return
    if w.isRunning():
        # 窗口已经没了，掐断信号再放生：迟到的 emit 没必要再往空气里发
        try:
            w.blockSignals(True)
            w.setParent(None)          # 别再挂在窗口名下，否则关窗时一起被拆
            _ORPHAN_THREADS.append(w)
        except Exception:
            pass


# ---------------------------------------------------------------- 后台检测
class DetectWorker(QThread):
    line = pyqtSignal(str)
    done = pyqtSignal(list, list)

    def run(self):
        self.line.emit('正在你电脑上找书库索引…')
        idxs = detect.find_indexes()
        self.line.emit('找到 %d 个书库索引' % len(idxs))
        self.line.emit('正在核对书库文件夹的位置…')
        items = detect.check_dirs(idxs)
        miss = [i for i in items if not i['found']]
        self.line.emit('核对完毕，%d 个书库没找到' % len(miss))
        self.done.emit(idxs, items)


# ---------------------------------------------------------------- 后台建库
class BuildWorker(QThread):
    """建（或刷新）文件名索引：只读扫盘，一个字节都不往书库里写。"""

    line = pyqtSignal(str)
    done = pyqtSignal(dict)
    fail = pyqtSignal(str)

    def __init__(self, roots, db, fresh=True):
        super().__init__()
        self.roots, self.db, self.fresh = roots, db, fresh
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        kw = {'exts': filelib.EXTS, 'cancel': lambda: self._stop,
              'on_progress': lambda p: self.line.emit(
                  '已扫 %s 个文件…  %s' % (p.get('n'), (p.get('dir') or '')[:70])
                  if p.get('stage') == 'scan'
                  else '跳过（目录不存在）：%s' % p.get('root'))}
        try:
            if self.fresh or not os.path.isfile(self.db):
                r = filelib.build(self.roots, self.db, **kw)
            else:
                r = filelib.refresh(self.db, self.roots, **kw)
            self.done.emit(r or {})
        except Exception as e:
            self.fail.emit('%s: %s' % (type(e).__name__, e))


# ---------------------------------------------------------------- 向导各页
class PageIntro(QWizardPage):
    """第 1 步：自动检测（不用点，进页面就自己跑）"""

    def __init__(self, wiz):
        super().__init__()
        self.wiz = wiz
        self._done = False
        self._asked = False
        self.setTitle('自动检测')
        self.setSubTitle('先花十几秒，让我看看你电脑上的书库在哪')
        v = QVBoxLayout(self)
        v.addWidget(QLabel(
            'CathayHub 是面向人文社科研究的全文检索与阅读工具。\n\n'
            '这一次要做的三件小事：\n'
            '  1. 看看你电脑上有哪些书库索引\n'
            '  2. 看看书库文件夹还在不在原来的位置\n'
            '  3. 建一份"按书名找文件"的索引（阅读时要靠它）\n\n'
            '全程自动，你只要点几下就行。'))
        row = QHBoxLayout()
        # 不用用户点：进到这一页就自动开跑（下面 initializePage）。
        # 这个按钮只是让界面上有个"正在做什么"的交代，跑完就藏起来。
        self.btn = QPushButton('马上就会自动开始，稍等一下…')
        self.btn.setEnabled(False)
        row.addWidget(self.btn)
        row.addStretch(1)
        v.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.hide()
        v.addWidget(self.bar)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(110)
        v.addWidget(self.log)
        self.sum = QLabel('')
        self.sum.setWordWrap(True)
        v.addWidget(self.sum)

    def _start(self):
        self.btn.setEnabled(False)
        self.bar.show()
        self.w = DetectWorker()
        self.w.line.connect(lambda s: self.log.appendPlainText(s))
        self.w.done.connect(self._finish)
        self.w.start()

    def initializePage(self):
        """页面一出现就开跑，不用用户动手；再退回来也不会跑第二遍。"""
        if self._asked:
            return
        self._asked = True
        QTimer.singleShot(120, self._start)

    def _finish(self, idxs, items):
        self.bar.hide()
        self.btn.hide()
        self.wiz.index_dirs = idxs
        self.wiz.items = items
        n_idx = len(idxs)
        miss = [i for i in items if not i['found']]
        trans = [i for i in items if i['translated']]
        txt = ('检测完成：找到 %d 个书库索引' % n_idx)
        if trans:
            txt += '，有 %d 个书库不在原来的盘、已经帮你接上了' % len(trans)
        if miss:
            txt += '，还有 %d 个书库没找到（下一步会说明）' % len(miss)
        else:
            txt += '，书库全部就位'
        self.sum.setText(txt)
        self._done = True
        self.completeChanged.emit()

    def isComplete(self):
        return self._done


class PageDetail(QWizardPage):
    """第 2 步：检测详情"""

    def __init__(self, wiz):
        super().__init__()
        self.wiz = wiz
        self.setTitle('检测结果')
        self.setSubTitle('索引和书库都列在这儿，缺什么一眼看得出')
        v = QVBoxLayout(self)

        # 上半：这台机器上找到的索引
        v.addWidget(QLabel('一、找到的书库索引'))
        self.tv_idx = QTableWidget()
        self.tv_idx.setColumnCount(4)
        self.tv_idx.setHorizontalHeaderLabels(
            ['索引', '收着这些书库', '放在哪', '状态'])
        self.tv_idx.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        self.tv_idx.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tv_idx.setMaximumHeight(150)
        v.addWidget(self.tv_idx)
        self.sum_idx = QLabel('')
        self.sum_idx.setWordWrap(True)
        v.addWidget(self.sum_idx)

        # 下半：索引要用的那批书库文件夹
        v.addWidget(QLabel('二、书库文件夹在不在'))
        self.tv = QTableWidget()
        self.tv.setColumnCount(4)
        self.tv.setHorizontalHeaderLabels(['书库', '重要程度', '在哪', '状态'])
        self.tv.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        self.tv.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        v.addWidget(self.tv)
        self.tip = QLabel('')
        self.tip.setWordWrap(True)
        v.addWidget(self.tip)

        # 有书库没找到时，就地在这里指定新位置（不再单开一步弹窗打扰）
        row = QHBoxLayout()
        self.b_pick = QPushButton('有书库没找到？手动指定它现在的位置…')
        self.b_pick.clicked.connect(self._pick)
        row.addWidget(self.b_pick, 0, Qt.AlignmentFlag.AlignLeft)
        row.addStretch(1)
        v.addLayout(row)
        self._inited = False

    def _st(self, it):
        if it['found'] and it['translated']:
            return '换地方了，已接上'
        if it['found']:
            return '就位'
        if it['level'] == 'optional':
            return '没找到（不影响搜索）'
        if it['level'] == 'dual':
            return '书库没找到（索引还在，照样能搜）'
        return '没找到'

    def _fill_indexes(self):
        """上半页：找到哪些索引、它们各自收着谁、会不会进那个组。"""
        rows = detect.describe_indexes(self.wiz.index_dirs)
        self.tv_idx.setRowCount(0)
        for it in rows:
            r = self.tv_idx.rowCount()
            self.tv_idx.insertRow(r)
            books = ('、'.join(it['books']) if it['books']
                     else '（不是咱们照看的那批书库）')
            vals = [it['name'], books, it['store'],
                    '早就登记过了' if it['registered'] else '这次新登记的']
            for i, s in enumerate(vals):
                cell = QTableWidgetItem(s)
                if i == 3 and not it['registered']:
                    cell.setForeground(QColor('#8A6100'))
                self.tv_idx.setItem(r, i, cell)
        self.tv_idx.resizeColumnsToContents()

        n_new = len([i for i in rows if not i['registered']])
        if rows:
            self.sum_idx.setText(
                '这 %d 个索引会被合成一个组「%s」，'
                '以后在 CathayHub 搜索就默认搜它 —— 一次横跨所有找得到的书库。'
                '%s' % (len(rows), detect.GROUP_NAME,
                        '（其中 %d 个是这次新登记的）' % n_new if n_new else ''))
        else:
            self.sum_idx.setText(
                '⚠ 一个索引都没找到：这台机器上没有索引，就没法搜索内容。\n'
                '下一步可以手动指出索引放在哪个文件夹里。')

    def initializePage(self):
        self._fill_indexes()
        items = self.wiz.items or []
        # 【v0.3.9】文件名索引那一块搬走了 —— 它和"索引/书库在不在"是两码事，
        # 挤在第 2 步里一堆内容叠着看不清。现在独立成第 3 步。
        self.tv.setRowCount(0)
        for it in items:
            r = self.tv.rowCount()
            self.tv.insertRow(r)
            vals = [it['key'], LEVEL_TEXT.get(it['level'], it['level']),
                    it['actual'] or '（没找到）', self._st(it)]
            for i, s in enumerate(vals):
                cell = QTableWidgetItem(s)
                if not it['found'] and it['level'] == 'critical':
                    cell.setForeground(QColor('#B00020'))
                elif it['translated']:
                    cell.setForeground(QColor('#8A6100'))
                self.tv.setItem(r, i, cell)
        self.tv.resizeColumnsToContents()
        # 缺什么就地说明白（不再单开一步弹窗）——主要书库用红字，次要的用灰点
        miss = [i for i in items if not i['found']]
        miss_crit = [i for i in miss if i['level'] == 'critical']
        miss_soft = [i for i in miss if i['level'] in ('optional', 'dual')]
        txt = []
        if miss_crit:
            txt.append(
                '<span style="color:#B00020">没找到的主要书库：%s\n'
                '  能搜索、看得到结果，但点开时打不开文件。\n'
                '  要么把书库放回它原来的位置，要么用下面的按钮指出它现在在哪。'
                '</span>' % '、'.join(i['key'] for i in miss_crit))
        if miss_soft:
            txt.append(
                '没找到的书库（次要，不影响搜索）：\n' +
                '\n'.join('⚪ %s\n   能搜到结果，但点开时打不开文件。'
                          '想打开就把书库放回指定位置，或者按搜索结果自己去网盘下载。'
                          % i['key'] for i in miss_soft))
        self.tip.setText('\n\n'.join(txt))
        self.b_pick.setVisible(bool(miss))

    def nextId(self):
        """【v0.3.9】第 2 步看完就交给第 3 步（文件名索引）。"""
        for i in self.wizard().pageIds():
            if isinstance(self.wizard().page(i), PageFiledb):
                return i
        return -1

    def _miss_any(self):
        return [i for i in (self.wiz.items or []) if not i['found']]

    def _pick(self):
        miss = self._miss_any()
        if not miss:
            QMessageBox.information(self, '都在', '书库都找到了，不用指定。')
            return
        d = QFileDialog.getExistingDirectory(
            self, '选中「%s」这个书库现在的位置' % miss[0]['key'])
        if not d:
            return
        for it in self.wiz.items or []:
            if it['key'] == miss[0]['key']:
                it['actual'] = d
                it['found'] = True
                it['translated'] = (pt.norm(d) != pt.norm(it['expected']))
                break
        self.initializePage()
        QMessageBox.information(self, '已接上',
                                '「%s」已经指到：\n%s\n\n'
                                '剩下的还可以继续点那个按钮指定。'
                                % (miss[0]['key'], d))


class PageFiledb(QWizardPage):
    """第 3 步：文件名索引（按书名找文件用的那份库）

    【v0.3.9】它原先攒在第 2 步底下 —— 两块互不相干的事挤在一页，看得一头雾水。
    现在单独一步，问的就一件事：这份索引要不要现在建、扫哪些目录、放哪儿。
    """

    def __init__(self, wiz):
        super().__init__()
        self.wiz = wiz
        self._inited = False
        self.setTitle('文件名索引')
        self.setSubTitle('阅读时要靠它按书名找文件；书库本身不会被改动')
        v = QVBoxLayout(self)

        self.lb_fdb = QLabel('')
        self.lb_fdb.setWordWrap(True)
        v.addWidget(self.lb_fdb)
        v.addWidget(self._line())
        self.ck_build = QCheckBox('现在就建（或刷新）这份索引')
        self.ck_build.setChecked(True)
        v.addWidget(self.ck_build)
        v.addWidget(QLabel('扫哪些目录（默认就是上一步找到的那些书库）：'))
        self.lst = QListWidget()
        self.lst.setMaximumHeight(110)
        v.addWidget(self.lst)
        row2 = QHBoxLayout()
        b_add = QPushButton('添加文件夹…')
        b_del = QPushButton('移除选中')
        b_add.clicked.connect(self._add_root)
        b_del.clicked.connect(self._del_root)
        row2.addWidget(b_add)
        row2.addWidget(b_del)
        row2.addStretch(1)
        v.addLayout(row2)
        row3 = QHBoxLayout()
        self.ed_db = QLineEdit()
        b_db = QPushButton('浏览…')
        b_db.clicked.connect(self._pick_db)
        row3.addWidget(QLabel('索引放哪'))
        row3.addWidget(self.ed_db, 1)
        row3.addWidget(b_db)
        v.addLayout(row3)
        v.addStretch(1)

    def _line(self):
        ln = QFrame()
        ln.setFrameShape(QFrame.Shape.HLine)
        ln.setFrameShadow(QFrame.Shadow.Sunken)
        return ln

    # ---- 这一块的小动作
    def _add_root(self):
        d = QFileDialog.getExistingDirectory(self, '选要收进文件名索引的文件夹')
        if d and not self.lst.findItems(d, Qt.MatchFlag.MatchExactly):
            self.lst.addItem(d)

    def _del_root(self):
        for i in sorted(self.lst.selectedIndexes(), key=lambda x: -x.row()):
            self.lst.takeItem(i.row())

    def _pick_db(self):
        p, _ = QFileDialog.getSaveFileName(
            self, '文件名索引放哪儿（.db）',
            self.ed_db.text() or 'cathayviewer_index.db', '索引库 (*.db)')
        if p:
            self.ed_db.setText(p)

    def _roots(self):
        return [self.lst.item(k).text() for k in range(self.lst.count())]

    def _fill_filedb(self):
        """把这份索引的现状摆出来，并给出默认勾不勾。"""
        st = detect.filename_db_state(self.ed_db.text().strip()
                                      or detect.filename_db_default())
        if st['has']:
            self.lb_fdb.setText(
                '已经有了：%s 个文件，建于 %s（%.1f MB）。\n%s\n'
                '勾上下面的框再扫一遍，可以把后来新加的书也收进去。'
                % (st['files'], st['built_at'] or '（时间不明）',
                   st['size'] / 1048576.0, st['path']))
        else:
            self.lb_fdb.setText(
                '还没有。勾上下面的框，点"下一步"就开始扫描。\n'
                '只读扫一遍文件名，不会改动你的书库，也不会动索引数据。')
        return st

    def initializePage(self):
        if not self._inited:
            # 默认值只铺一次：之后用户怎么改都听他的，别每进一次就冲掉
            self._inited = True
            self.ed_db.setText(self.wiz.file_db or detect.filename_db_default())
            if not self.lst.count():
                for r in (self.wiz.file_roots
                          or detect.suggest_roots(self.wiz.items or [])):
                    self.lst.addItem(r)
            self.ck_build.setChecked(not self._fill_filedb()['has'])
        else:
            self._fill_filedb()

    def nextId(self):
        """勾了"现在就建"才去建库那一步，没勾就直接去完成页。"""
        want_build = self.ck_build.isChecked()
        for i in self.wizard().pageIds():
            p = self.wizard().page(i)
            if want_build and isinstance(p, PageBuild):
                return i
            if not want_build and isinstance(p, PageDone):
                return i
        return -1


class PageBuild(QWizardPage):
    """建文件名索引（只有在上一步勾了"现在就建"才会走到这儿）"""

    def __init__(self, wiz):
        super().__init__()
        self.wiz = wiz
        self._state = 'idle'          # idle / running / done / skipped
        self.worker = None
        self.setTitle('正在建文件名索引')
        self.setSubTitle('只读书名和路径，不会改动你的书库')
        v = QVBoxLayout(self)
        self.lab = QLabel('准备中…')
        self.lab.setWordWrap(True)
        v.addWidget(self.lab)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        v.addWidget(self.bar)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.log, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.b_skip = QPushButton('不想等了，跳过')
        self.b_skip.clicked.connect(self._skip)
        row.addWidget(self.b_skip)
        v.addLayout(row)

    def initializePage(self):
        if self._state != 'idle':
            return
        self._from_detail()
        db = (self.wiz.file_db or detect.filename_db_default()).strip()
        self.wiz.file_db = db
        if not self.wiz.file_roots:
            self._state = 'skipped'
            self.lab.setText('没有要扫的目录，跳过这一步。')
            self.bar.hide()
            self.b_skip.hide()
            self.completeChanged.emit()
            return
        self._state = 'running'
        self.lab.setText('正在扫描 %d 个目录…（只读，不会改动源书库）'
                         % len(self.wiz.file_roots))
        self.worker = BuildWorker(self.wiz.file_roots, db,
                                  fresh=not os.path.isfile(db))
        self.worker.line.connect(self.log.appendPlainText)
        self.worker.done.connect(self._on_done)
        self.worker.fail.connect(self._on_fail)
        self.worker.start()

    def _from_detail(self):
        """建库要扫的目录、库放哪 —— 都在第 3 步那个页面里，用户可能改过。"""
        for i in self.wizard().pageIds():
            p = self.wizard().page(i)
            if isinstance(p, PageFiledb):
                self.wiz.file_roots = list(p._roots())
                self.wiz.file_db = (p.ed_db.text().strip()
                                    or detect.filename_db_default())
                return
        self.wiz.file_roots = list(self.wiz.file_roots or [])

    def _on_done(self, r):
        self.bar.hide()
        self.b_skip.hide()
        self._state = 'done'
        self.wiz.build_result = r or {}
        n = int((r or {}).get('files') or 0)
        self.lab.setText('建库完成 ✓ 共 %s 个文件，用时 %s 秒。'
                         % (n, (r or {}).get('secs')))
        self.log.appendPlainText('库：%s' % ((r or {}).get('db') or ''))
        # 记两笔账：Search 看得见，Viewer 一打开也知道库已经有了
        detect.save_filename_db(self.wiz.file_db, self.wiz.file_roots,
                                n, time.strftime('%Y-%m-%d %H:%M:%S'))
        self.completeChanged.emit()
        QTimer.singleShot(600, lambda: self.wizard()
                          and self.wizard().next())

    def _on_fail(self, msg):
        self.bar.hide()
        self._state = 'done'
        self.lab.setText('建库失败：%s\n\n书库本身没受影响，'
                         '以后可以在"设置"或重跑向导时再试。' % msg)
        self.wiz.build_result = {'error': msg}
        self.completeChanged.emit()

    def _skip(self):
        w = self.worker
        self.worker = None
        _give_up_thread(w)      # 劝不住就摘出去，别让它跟着向导一起被拆
        self._state = 'skipped'
        self.lab.setText('已跳过。以后想建，重跑一次这个向导就行。')
        self.bar.hide()
        self.b_skip.hide()
        self.completeChanged.emit()
        self.wizard().next()

    def isComplete(self):
        return self._state in ('done', 'skipped')

    def nextId(self):
        for i in self.wizard().pageIds():
            if isinstance(self.wizard().page(i), PageDone):
                return i
        return -1


class PageDone(QWizardPage):
    """最后一步：总结一下 + 顺手挑个颜色"""

    def __init__(self, wiz):
        super().__init__()
        self.wiz = wiz
        self.setTitle('都准备好了')
        self.setSubTitle('点"完成"就可以开始用了')
        v = QVBoxLayout(self)
        self.lab = QLabel('')
        self.lab.setWordWrap(True)
        v.addWidget(self.lab)

        row = QHBoxLayout()
        row.addWidget(QLabel('界面颜色（三个软件一起变）：'))
        self.group = {}
        cur = (_settings().get('theme') or 'light')
        for k, (name, _) in detect.THEMES.items():
            r = QRadioButton(name)
            if k == cur:
                r.setChecked(True)
            self.group[k] = r
            row.addWidget(r)
        if not any(r.isChecked() for r in self.group.values()):
            self.group['light'].setChecked(True)
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)

    def picked(self):
        for k, r in self.group.items():
            if r.isChecked():
                return k
        return 'light'

    def initializePage(self):
        n = len(self.wiz.index_dirs or [])
        txt = ('已经记下 %d 个书库索引，并攒成「%s」这个检索组，'
               '搜索时默认就用它。\n' % (n, detect.GROUP_NAME))
        r = getattr(self.wiz, 'build_result', None)
        if r and r.get('files'):
            txt += ('\n文件名索引：%s 个文件（按书名找文件用，阅读时靠它）。'
                    % r.get('files'))
        else:
            fst = detect.filename_db_state(self.wiz.file_db)
            txt += ('\n文件名索引：%s'
                    % ('已有 %s 个文件。' % fst['files'] if fst['has']
                       else '还没建 —— 阅读里也可以随时点「建库 / 刷新」补上。'))
        txt += ('\n\n以后打开本程序不会再出现这个向导。'
                '想重新检测，去右下角"设置"里点"重新检测"。')
        self.lab.setText(txt)


class SetupWizard(QWizard):
    """首次运行向导"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.index_dirs = []
        self.items = []
        # 文件名索引：库放哪、扫哪些目录（上一步用户可能改过）
        self.file_db = detect.filename_db_default()
        self.file_roots = []
        self.build_result = None
        self.setWindowTitle('%s 首次运行向导' % config.APP_TITLE)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.HaveCustomButton1, False)
        self.addPage(PageIntro(self))
        self.addPage(PageDetail(self))
        self.addPage(PageFiledb(self))      # v0.3.9：文件名索引单独成第 3 步
        self.addPage(PageBuild(self))
        self.addPage(PageDone(self))
        self.setButtonText(QWizard.WizardButton.NextButton, '下一步')
        self.setButtonText(QWizard.WizardButton.BackButton, '上一步')
        self.setButtonText(QWizard.WizardButton.FinishButton, '完成')
        self.setButtonText(QWizard.WizardButton.CancelButton, '取消')
        self.setMinimumSize(680, 560)

    def theme_key(self):
        p = self.page(self.pageIds()[-1])
        return p.picked() if isinstance(p, PageDone) else 'light'

    def _halt_worker(self):
        """关窗/取消前把后台线程收干净。

        第 1 页在扫全盘找索引、第 4 页在建文件名索引（十几万本书，好几分钟）。
        这时候点 X 或 Esc，向导一关线程还在跑 → QThread 跟着窗口被拆，
        不是抛异常，是直接崩进程。所以要么等它停，要么摘出去寄养。
        """
        for i in self.pageIds():
            p = self.page(i)
            for attr in ('w', 'worker'):
                w = getattr(p, attr, None)
                if isinstance(w, QThread):
                    setattr(p, attr, None)
                    _give_up_thread(w)

    def reject(self):
        """点「取消」/ 按 Esc。"""
        self._halt_worker()
        super().reject()

    def closeEvent(self, e):
        self._halt_worker()
        super().closeEvent(e)


def finish_wizard(w):
    """向导走完了：该记的账都在这儿记。

    主界面和直接启动（main）两处都调它，免得漏掉哪一笔。
    返回一份统计，交给调用方去写给人看的那句话。
    """
    ok_m, err_m = detect.save_mapping(w.items)
    added, gpath = detect.register(w.index_dirs)
    ok_t, err_t = detect.write_theme(w.theme_key())
    ok_d, err_d = detect.save_default_index(w.index_dirs)
    # 库已经建好了（这次建的，或者以前就有）—— 一定要记下来，
    # 不然 Viewer 打开还会弹"第一次使用 —— 建立文件名索引"来问用户。
    fst = detect.filename_db_state(getattr(w, 'file_db', ''))
    ok_f, err_f = (True, '')
    if fst['has']:
        # 这次真建过库才顺带把扫描目录也交给 Viewer；没建就别动人家的
        built = bool((getattr(w, 'build_result', None) or {}).get('files'))
        ok_f, err_f = detect.save_filename_db(
            fst['path'],
            getattr(w, 'file_roots', None) or detect.suggest_roots(w.items),
            fst['files'], fst['built_at'], write_roots=built)
    s = _settings()
    s['first_run'] = False
    s['theme'] = w.theme_key()
    _save_settings(s)
    return {'indexes': len(w.index_dirs or []), 'added': added,
            'group': detect.GROUP_NAME,
            'mapping': (ok_m, err_m), 'theme': (ok_t, err_t),
            'default': (ok_d, err_d), 'filedb': (ok_f, err_f),
            'filedb_state': fst,
            'build': getattr(w, 'build_result', None)}


# ---------------------------------------------------------------- 设置页
class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('设置')
        self.setMinimumSize(640, 460)
        v = QVBoxLayout(self)
        self.tv = QTableWidget()
        self.tv.setColumnCount(4)
        self.tv.setHorizontalHeaderLabels(['书库', '重要程度', '在哪', '状态'])
        self.tv.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        self.tv.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        v.addWidget(self.tv, 1)
        self.info = QPlainTextEdit()
        self.info.setReadOnly(True)
        self.info.setFixedHeight(110)
        v.addWidget(self.info)

        form = QFormLayout()
        self.cb_theme = QComboBox()
        for k, (name, _) in detect.THEMES.items():
            self.cb_theme.addItem(name, k)
        form.addRow('界面颜色：', self.cb_theme)
        self.cb_auto = QComboBox()
        self.cb_auto.addItem('什么都不做', '')
        self.cb_auto.addItem('自动打开搜索', 'search')
        self.cb_auto.addItem('自动打开阅读', 'viewer')
        form.addRow('打开本程序时：', self.cb_auto)
        v.addLayout(form)

        row = QHBoxLayout()
        b_re = QPushButton('重新检测')
        b_re.clicked.connect(self._redetect)
        row.addWidget(b_re)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        v.addLayout(row)
        self.items = []
        self._load()

    def _load(self):
        s = _settings()
        i = self.cb_theme.findData(s.get('theme', 'light'))
        self.cb_theme.setCurrentIndex(max(0, i))
        i = self.cb_auto.findData(s.get('auto_open', ''))
        self.cb_auto.setCurrentIndex(max(0, i))
        self._fill(detect.check_dirs())

    def _fill(self, items):
        self.items = items
        self.tv.setRowCount(0)
        for it in items:
            r = self.tv.rowCount()
            self.tv.insertRow(r)
            st = ('换地方了，已接上' if it['translated'] else
                  '就位' if it['found'] else
                  '没找到（索引还在，照样能搜）' if it['level'] == 'dual' else
                  '没找到')
            vals = [it['key'], LEVEL_TEXT.get(it['level'], it['level']),
                    it['actual'] or '（没找到）', st]
            for c, s in enumerate(vals):
                cell = QTableWidgetItem(s)
                if not it['found'] and it['level'] == 'critical':
                    cell.setForeground(QColor('#B00020'))
                self.tv.setItem(r, c, cell)
        self.tv.resizeColumnsToContents()
        miss = [i for i in items
                if not i['found'] and i['level'] in ('optional', 'dual')]
        if miss:
            self.info.setPlainText(
                '\n\n'.join(
                    '⚪ %s（没找到）\n'
                    '  · 搜索：能搜，看得到结果\n'
                    '  · 打开：没法直接打开文件\n'
                    '  · 原因：书库文件不在本机\n'
                    '  · 办法：按搜索结果去网盘下载' % i['key'] for i in miss))
        else:
            self.info.setPlainText('')

    def _redetect(self):
        self.btn_text = '正在检测…'
        items = detect.check_dirs()
        self._fill(items)
        detect.save_mapping(items)
        QMessageBox.information(self, '检测完成', '已经重新检测一遍。')

    def accept(self):
        s = _settings()
        s['theme'] = self.cb_theme.currentData() or 'light'
        s['auto_open'] = self.cb_auto.currentData() or ''
        _save_settings(s)
        ok, err = detect.write_theme(s['theme'])
        if not ok:
            QMessageBox.warning(self, '颜色没改成', err)
        super().accept()


# ---------------------------------------------------------------- 主界面
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('%s  %s' % (config.APP_TITLE, config.APP_VER))
        self.setMinimumSize(620, 430)
        self.items = []

        c = QWidget()
        self.setCentralWidget(c)
        v = QVBoxLayout(c)
        v.setContentsMargins(24, 20, 24, 16)
        v.setSpacing(14)

        top = QHBoxLayout()
        self.b_wizard = QPushButton('首次运行向导（索引加载）')
        self.b_wizard.clicked.connect(self.open_wizard)
        self.b_wizard.setToolTip('重新检测书库和索引的位置')
        top.addWidget(self.b_wizard, 0, Qt.AlignmentFlag.AlignLeft)
        top.addStretch(1)
        v.addLayout(top)

        t = QLabel('CathayHub')
        f = QFont()
        f.setPointSize(20)
        f.setBold(True)
        t.setFont(f)
        v.addWidget(t)
        sub = QLabel('面向人文社科研究的全文检索与阅读工具')
        sub.setStyleSheet('color:#6B7280')
        v.addWidget(sub)

        g = QGridLayout()
        g.setSpacing(12)
        self.b_search = self._big('搜索', '在书库里全文检索', 'search')
        self.b_view = self._big('阅读', '打开并阅读找到的书', 'viewer')
        self.b_index = self._big('索引', '新建、更新、管理索引', 'indexer')
        g.addWidget(self.b_search, 0, 0)
        g.addWidget(self.b_view, 0, 1)
        g.addWidget(self.b_index, 0, 2)
        v.addLayout(g)

        v.addWidget(self._line())
        v.addWidget(QLabel('最近打开'))
        self.recent = QVBoxLayout()
        v.addLayout(self.recent)
        v.addStretch(1)

        bot = QHBoxLayout()
        self.status = QLabel('')
        self.status.setWordWrap(True)
        bot.addWidget(self.status, 1)
        self.b_set = QPushButton('设置')
        self.b_set.clicked.connect(self.open_settings)
        self.b_about = QPushButton('关于')
        self.b_about.clicked.connect(self.about)
        bot.addWidget(self.b_set)
        bot.addWidget(self.b_about)
        v.addLayout(bot)

        sb = QStatusBar()
        self.setStatusBar(sb)
        self.sb = sb

        self._fill_recent()
        self.refresh_status()

    def _line(self):
        ln = QFrame()
        ln.setFrameShape(QFrame.Shape.HLine)
        ln.setFrameShadow(QFrame.Shadow.Sunken)
        return ln

    def _big(self, title, tip, kind):
        b = QPushButton('%s\n%s' % (title, tip))
        b.setMinimumHeight(78)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setToolTip('按住 Shift 再点：Launcher 留在这儿不关（一次要开好几个时用）')
        b.clicked.connect(lambda: self._launch(kind))
        return b

    def _launch(self, kind):
        """把活交给那个软件，Launcher 自己退掉（需求：点了就该收工）。

        以前点完"搜索" Launcher 还杵在前台，得再手动关一次。它本来就是个入口，
        任务交出去了就该让位。
        """
        if not launch(kind):        # 没找到那个程序：弹过提示了，别瞎关
            return
        _mods = QApplication.keyboardModifiers()
        if bool(_mods & Qt.KeyboardModifier.ShiftModifier):
            self.status.setText('已经打开了（按住 Shift，Launcher 留在这儿）。')
            return
        QTimer.singleShot(200, self.close)     # 让目标窗口先抬头，再退

    def _fill_recent(self):
        while self.recent.count():
            it = self.recent.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        files = recent_files(4)
        if not files:
            lb = QLabel('（还没有打开过）')
            lb.setStyleSheet('color:#9AA5B1')
            self.recent.addWidget(lb)
            return
        for p in files:
            b = QPushButton(os.path.basename(p))
            b.setToolTip(p)
            b.setStyleSheet('text-align:left; border:0; color:#0B5CAB')
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, q=p: launch('viewer', q))
            self.recent.addWidget(b)

    def refresh_status(self):
        # 索引要先扫出来：书库条目里有几条是"看有没有索引收着它"才显不显示的
        self.index_dirs = detect.find_indexes()
        self.items = detect.check_dirs(self.index_dirs)
        n_idx = len(self.index_dirs)
        miss = [i for i in self.items if not i['found']]
        trans = [i for i in self.items if i['translated']]
        fst = detect.filename_db_state()
        head = '找到 %d 个索引 · ' % n_idx if n_idx else '一个索引都没找到 · '
        head += ('文件名索引 %s 个文件 · ' % fst['files'] if fst['has']
                 else '还没建文件名索引 · ')
        if not miss:
            self.status.setText('🟢 %s书库已就位' % head)
        else:
            crit = [i for i in miss if i['level'] == 'critical']
            if crit:
                self.status.setText('🟡 %s有 %d 个主要书库没找到 ［查看详情］'
                                    % (head, len(crit)))
            else:
                self.status.setText('🟡 %s部分次要书库没找到（不影响搜索）'
                                    ' ［查看详情］' % head)
        self.status.mousePressEvent = lambda e: self.open_settings()
        if trans:
            self.sb.showMessage('有 %d 个书库换了位置，打开文件时会自动接上'
                                % len(trans))
        else:
            self.sb.clearMessage()

    def open_settings(self):
        d = SettingsDialog(self)
        d.exec()
        self.refresh_status()

    def open_wizard(self):
        w = SetupWizard(self)
        if w.exec():
            self._after_wizard(w)

    def _after_wizard(self, w):
        info = finish_wizard(w)
        self.refresh_status()
        ok_t, err_t = info['theme']
        ok_d, err_d = info['default']
        fs = info['filedb_state']
        QMessageBox.information(
            self, '搞定',
            '检测完毕：\n'
            '  · 书库索引 %d 个\n'
            '  · 新登记 %d 条\n'
            '  · 检索组：%s\n'
            '  · 搜索时默认用它：%s\n'
            '  · 文件名索引：%s\n'
            '  · 界面颜色：%s%s'
            % (info['indexes'], info['added'], info['group'],
               '是' if ok_d else '没设成（%s）' % err_d,
               ('%s 个文件' % fs['files']) if fs['has'] else '还没建',
               detect.THEMES.get(w.theme_key(), ('?', ''))[0],
               ('\n\n（颜色没写成：%s）' % err_t) if not ok_t else ''))

    def about(self):
        QMessageBox.information(
            self, '关于 CathayHub',
            'CathayHub %s\n\n'
            '面向人文社科研究的全文检索与阅读工具。\n\n'
            '三个程序：\n'
            '  搜索 —— 在书库里全文检索\n'
            '  阅读 —— 打开并阅读找到的书\n'
            '  索引 —— 新建、更新、管理索引\n\n'
            '程序目录：%s' % (config.APP_VER, config.app_dir()))


def apply_theme(app):
    """自己也跟着那份 hub_theme.qss 走，跟另外三个软件保持一致。"""
    try:
        with open(config.theme_path(), 'r', encoding='utf-8') as f:
            app.setStyleSheet(f.read())
    except Exception:
        pass
