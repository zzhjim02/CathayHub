# -*- coding: utf-8 -*-
"""CathayHub Indexer —— 学术索引管理工具（主界面）。

定位：CathayHub 文件夹里的第三个软件。它不自己检索，专门管"索引"这件事，
功能对齐 FileLocator Pro 的索引管理器（Index List）：

    新建索引 / 引用已有索引 / 更新 / 重建 / 取消 / 删除 / 重命名 / 索引组

底层干活的还是那套 9.3 的 flpidx.exe（9.0 那份的命令行是死的，见 flp.py）。

**关于"一边更新一边用"**：更新是在子进程里跑的，本程序只是盯着它，界面
一点不卡；而索引本身（Lucene 段文件）允许"写的同时读"——实测过：1200 个
文件的索引，更新跑到一半时连着检索两次，都正常出结果。所以更新期间你
尽可以照常用 CathayHub Search 去搜，不用等。
"""
import os
import sqlite3
import time

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                             QComboBox, QDialog, QDialogButtonBox,
                             QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
                             QInputDialog, QLabel, QLineEdit, QMainWindow,
                             QMessageBox, QPlainTextEdit, QProgressBar,
                             QPushButton, QSplitter, QTableWidget,
                             QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)

from . import config, flp, indexes, path_translator as pt

APP_NAME = 'CathayHub Indexer'
APP_TITLE = 'CathayHub Indexer 学术索引管理工具'
APP_VER = '0.1.1'

COL_NAME, COL_KIND, COL_STORE, COL_SRC, COL_STAT = range(5)


def _stat_text(st):
    """把 dir_status 的结果说成人话：这个索引的书库还在不在。

    只要"最主要的"那几个目录在，就算在 —— 一条索引往往还捎带索引了
    几十个别的目录（比如 D:\\华为云盘\\文档 下面那一串），它们缺不缺
    不影响"这个索引还能不能用"这个结论。
    """
    s = (st or {}).get('state')
    if s == 'ok':
        return '在'
    if s == 'partial':
        names = '、'.join((w.get('key') or '')
                          for w in (st.get('missing_main') or []))
        return '部分在（缺 %s）' % names if names else '部分在'
    if s == 'missing':
        return '不在'
    return '—'


# ---------------------------------------------------------------- 后台任务
class TaskWorker(QThread):
    """后台跑一条 flpidx 命令：实时吐行、可取消、结束报结果。"""

    line = pyqtSignal(str)
    finished_one = pyqtSignal(dict)

    def __init__(self, fn, kwargs, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._kwargs = kwargs or {}
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            res = self._fn(on_line=lambda s: self.line.emit(s),
                           cancel=lambda: self._stop,
                           **self._kwargs)
        except Exception as e:
            res = {'ok': False, 'rc': -1, 'elapsed': 0, 'tail': [str(e)]}
        self.finished_one.emit(res or {})


# ---------------------------------------------------------------- 小工具
def _fmt_size(nb):
    nb = float(nb or 0)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if nb < 1024 or unit == 'TB':
            return '%.1f %s' % (nb, unit)
        nb /= 1024.0
    return '%.1f TB' % nb


def _dir_stat(path):
    """粗略统计一个目录：文件数 + 总大小。失败就 (0, 0)。"""
    n = tot = 0
    try:
        for dp, dn, fn in os.walk(path):
            for f in fn:
                try:
                    tot += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
                n += 1
                if n > 200000:
                    break
    except Exception:
        pass
    return n, tot


def _guid_of(entry_path):
    """索引登记文件名里的 GUID —— IndexLog 下的统计库按它分目录。"""
    base = os.path.basename(entry_path or '')
    if base.startswith('idx_') and base.endswith('.xml'):
        return base[4:-4]
    return ''


def _index_stats(guid):
    """从 IndexLog/{GUID}/idxmonitor.db 里挖：跟踪多少文件、最后更新何时。

    读不到就给空串 —— 详情是锦上添花，不能因为它把界面拖垮。
    """
    if not guid:
        return '', ''
    db = os.path.join(os.path.dirname(config.FLP_CFG_DIR), 'IndexLog',
                      guid, 'idxmonitor.db')
    if not os.path.isfile(db):
        return '', ''
    n = ''
    when = ''
    try:
        con = sqlite3.connect('file:%s?mode=ro' % db.replace('\\', '/'),
                              uri=True, timeout=3)
        try:
            cur = con.cursor()
            try:
                row = cur.execute('select count(*) from File').fetchone()
                n = '%s' % row[0] if row else ''
            except Exception:
                pass
            try:
                row = cur.execute(
                    'select max(UpdatedDt) from File').fetchone()
                if row and row[0]:
                    when = str(row[0])[:19]
            except Exception:
                pass
        finally:
            con.close()
    except Exception:
        pass
    return n, when


# ---------------------------------------------------------------- 主窗口
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('%s %s' % (APP_TITLE, APP_VER))
        self.resize(1180, 720)
        self._worker = None
        self._after_create = ''      # 新建完成后要接着跑一次更新
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(60000)
        self._auto_timer.timeout.connect(self._auto_tick)
        self._auto_every = 0          # 分钟；0 = 不自动
        self._auto_left = 0
        self._build()
        QTimer.singleShot(120, self.refresh)

    # ------------------------------------------------------------ 界面
    def _build(self):
        c = QWidget()
        self.setCentralWidget(c)
        v = QVBoxLayout(c)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        # 工具栏
        bar = QHBoxLayout()
        self.b_new = QPushButton('新建索引…')
        self.b_ref = QPushButton('引用已有索引…')
        self.b_upd = QPushButton('更新索引')
        self.b_recreate = QPushButton('重建索引…')
        self.b_stop = QPushButton('停止')
        self.b_del = QPushButton('删除…')
        self.b_ren = QPushButton('重命名…')
        self.b_grp = QPushButton('索引组…')
        self.b_auto = QPushButton('定时更新…')
        self.b_refresh = QPushButton('刷新')
        self.b_stop.setEnabled(False)
        for b, tip, fn in (
                (self.b_new, '选好源目录、建一个全新的索引', self.do_new),
                (self.b_ref, '把别处已经建好的索引登记进来', self.do_addref),
                (self.b_upd, '只处理新增/改动/删除的文件（快）', self.do_update),
                (self.b_recreate, '推倒重来一遍（慢，但能修坏掉的索引）',
                 self.do_recreate),
                (self.b_stop, '停掉正在跑的任务', self.do_stop),
                (self.b_del, '从列表移除，或连索引数据一起删', self.do_delete),
                (self.b_ren, '改索引在列表里显示的名字', self.do_rename),
                (self.b_grp, '把几个索引合成一个「组」，一次搜多个库',
                 self.do_group),
                (self.b_auto, '每隔一段时间自动更新一次', self.do_auto),
                (self.b_refresh, '重新读一遍索引列表', self.refresh),
        ):
            b.setToolTip(tip)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch(1)
        v.addLayout(bar)

        # 列表 + 详情
        sp = QSplitter(Qt.Orientation.Vertical)
        self.tv = QTableWidget(0, 5)
        self.tv.setHorizontalHeaderLabels(
            ['索引名', '类型', '索引存放位置', '被索引的源目录', '状态'])
        self.tv.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.tv.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tv.verticalHeader().setVisible(False)
        self.tv.setAlternatingRowColors(True)
        self.tv.itemSelectionChanged.connect(self._show_detail)
        hh = self.tv.horizontalHeader()
        hh.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(COL_KIND, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(COL_STORE, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(COL_SRC, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(COL_STAT, QHeaderView.ResizeMode.Stretch)
        self.tv.setColumnWidth(COL_STORE, 260)
        self.tv.setColumnWidth(COL_SRC, 300)
        sp.addWidget(self.tv)

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFixedHeight(150)
        sp.addWidget(self.detail)
        sp.setStretchFactor(0, 3)
        sp.setStretchFactor(1, 1)
        v.addWidget(sp, 1)

        # 进度 + 输出
        h = QHBoxLayout()
        self.pb = QProgressBar()
        self.pb.setTextVisible(False)
        self.pb.setFixedHeight(8)
        self.pb.setRange(0, 0)
        self.pb.setVisible(False)
        h.addWidget(self.pb, 1)
        self.lb_task = QLabel('')
        h.addWidget(self.lb_task)
        v.addLayout(h)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4000)
        self.log.setFixedHeight(170)
        v.addWidget(self.log)

        self.statusBar().showMessage('')
        self._say_engine()

    def _say_engine(self):
        ok, why = flp.flpidx_alive()
        src = config.flp_source()
        if ok:
            self.statusBar().showMessage(
                '引擎：%s　索引管理命令行可用' % src)
        else:
            self.statusBar().showMessage('⚠ %s' % (why or '命令行工具不可用'))

    # ------------------------------------------------------------ 列表
    def refresh(self):
        try:
            items = indexes.list_all()
        except Exception as e:
            self._log('读索引列表失败：%s' % e)
            items = []
        self.tv.setRowCount(0)
        for e in items:
            store0 = e.get('path') or ''
            # 聊天记录这一类：索引和书库两个都没了就整条不显示
            try:
                if pt.should_hide(e.get('name') or '', store0):
                    continue
            except Exception:
                pass
            r = self.tv.rowCount()
            self.tv.insertRow(r)
            store = e.get('path') or ''
            try:
                st = pt.dir_status(store)
            except Exception:
                st = {'state': 'unknown', 'missing': [], 'sources': []}
            srcs = st.get('sources') or []
            # 一条索引可能索引几十个目录，全摊开根本看不清 —— 只列头两个
            if srcs:
                src = '；'.join(srcs[:2])
                if len(srcs) > 2:
                    src += '　等 %d 个' % len(srcs)
            else:
                src = ''
            kind = e.get('kind') or ''
            kind_s = {'group': '[组]', 'index': '索引'}.get(kind, kind)
            vals = [e.get('name') or '', kind_s, store, src, _stat_text(st)]
            for i, s in enumerate(vals):
                it = QTableWidgetItem(s)
                if i == COL_STORE and s and not os.path.isdir(s):
                    it.setForeground(QColor('#B00020'))
                if i == COL_SRC and st.get('state') in ('partial', 'missing'):
                    it.setForeground(QColor('#B00020'))
                    it.setText(s + ('　（已不存在！）' if st.get('state') == 'missing'
                                    else '　（部分不在）'))
                if i == COL_STAT and st.get('state') in ('partial', 'missing'):
                    it.setForeground(QColor('#B00020'))
                self.tv.setItem(r, i, it)
            self.tv.item(r, COL_NAME).setData(Qt.ItemDataRole.UserRole,
                                              e.get('name') or '')
        if self.tv.rowCount():
            self.tv.selectRow(0)
        self._log('索引列表已刷新，共 %d 条' % self.tv.rowCount())

    def _cur(self):
        r = self.tv.currentRow()
        if r < 0:
            return None
        name = self.tv.item(r, COL_NAME)
        store = self.tv.item(r, COL_STORE)
        if not name:
            return None
        return {'name': name.text(),
                'store': store.text() if store else '',
                'kind': (self.tv.item(r, COL_KIND).text()
                         if self.tv.item(r, COL_KIND) else '')}

    def _show_detail(self):
        e = self._cur()
        if not e:
            self.detail.setPlainText('')
            return
        store = e['store']
        try:
            st = pt.dir_status(store)
        except Exception:
            st = {'state': 'unknown', 'missing': [], 'sources': []}
        srcs = st.get('sources') or []
        lines = ['索引名：%s' % e['name'],
                 '类型：%s' % e['kind'],
                 '存放位置：%s' % (store or '（未登记路径）')]
        if srcs:
            lines.append('被索引的源目录（共 %d 个）：' % len(srcs))
            for s in srcs[:6]:
                lines.append('    %s%s' % (s, '' if os.path.isdir(s) else '　★ 不在'))
            if len(srcs) > 6:
                lines.append('    ……还有 %d 个' % (len(srcs) - 6))
        else:
            lines.append('被索引的源目录：（读不出来）')
        if store and os.path.isdir(store):
            n, tot = _dir_stat(store)
            lines.append('索引体积：%s（%d 个文件）' % (_fmt_size(tot), n))
        else:
            lines.append('索引体积：存放位置不存在')
        lines.append('书库还在不在：%s' % _stat_text(st))
        if st.get('missing'):
            lines.append('现在找不到的目录：%d 个' % len(st['missing']))
            for s in st['missing'][:6]:
                lines.append('    × %s' % s)
            if len(st['missing']) > 6:
                lines.append('    ……还有 %d 个' % (len(st['missing']) - 6))
        try:
            guid = _guid_of(indexes._entry_path(e['name']))
        except Exception:
            guid = ''
        n, when = _index_stats(guid)
        if n:
            lines.append('已跟踪文件：%s 个' % n)
        if when:
            lines.append('最后更新：%s' % when)
        self.detail.setPlainText('\n'.join(lines))

    # ------------------------------------------------------------ 日志
    def _log(self, s):
        self.log.appendPlainText(s)
        self.log.ensureCursorVisible()

    def _busy(self, on, what=''):
        self.b_stop.setEnabled(on)
        for b in (self.b_new, self.b_ref, self.b_upd, self.b_recreate,
                  self.b_del, self.b_ren, self.b_grp):
            b.setEnabled(not on)
        self.pb.setVisible(on)
        self.lb_task.setText(what)
        if on:
            self.statusBar().showMessage(
                '%s —— 更新期间检索照常可用，不用等' % what)

    # ------------------------------------------------------------ 任务
    def _run(self, fn, kwargs, what):
        if self._worker is not None:
            QMessageBox.information(self, APP_NAME, '还有一个任务没跑完')
            return
        ok, why = flp.flpidx_alive()
        if not ok:
            QMessageBox.warning(self, APP_NAME, why or '命令行工具不可用')
            return
        self._busy(True, what)
        self._log('=== %s　%s' % (what, time.strftime('%H:%M:%S')))
        w = TaskWorker(fn, kwargs, parent=self)
        w.line.connect(self._log)
        w.finished_one.connect(self._on_done)
        self._worker = w
        w.start()

    def _on_done(self, res):
        self._worker = None
        self._busy(False)
        el = res.get('elapsed') or 0
        nxt = getattr(self, '_after_create', '')
        if nxt:
            self._after_create = ''
            if res.get('ok'):
                self._log('索引壳建好了，接着把文件收录进去…')
                self._run(flp.update_index, {'name': nxt},
                          '首次更新「%s」' % nxt)
                return
            self._log('索引没建成功，后面的更新就不跑了')
        if res.get('cancelled'):
            self._log('—— 已停止（%.1f 秒）' % el)
            self.statusBar().showMessage('任务已停止')
            self.refresh()
            return
        if res.get('timed_out'):
            self._log('—— 超时中断（%.1f 秒）' % el)
            self.statusBar().showMessage('任务超时，已中断')
            return
        if res.get('ok'):
            self._log('—— 完成（%.1f 秒，返回码 %s）' % (el, res.get('rc')))
            self.statusBar().showMessage('完成')
        else:
            self._log('—— 失败（返回码 %s）' % res.get('rc'))
            for s in (res.get('tail') or [])[-6:]:
                self._log('    %s' % s)
            self.statusBar().showMessage('任务失败，看下面的输出')
        self.refresh()

    def do_stop(self):
        w = self._worker
        if w is None:
            return
        w.stop()
        self.b_stop.setEnabled(False)
        self._log('正在停止…')

    # ------------------------------------------------------------ 各操作
    def do_update(self):
        e = self._cur()
        if not e:
            return
        if e['kind'] == '[组]':
            QMessageBox.information(self, APP_NAME,
                                    '「%s」是索引组，更新请选它下面的单个索引'
                                    % e['name'])
            return
        try:
            st = pt.dir_status(e['store'])
        except Exception:
            st = {'state': 'unknown', 'missing': [], 'sources': []}
        # ① 主要书库一个都不在 —— 硬拦，更新等于清空索引
        if st.get('state') == 'missing':
            names = '、'.join((w.get('key') or '')
                              for w in (st.get('missing_main') or []))
            if not names:
                names = '、'.join((st.get('sources') or [])[:3])
            QMessageBox.warning(
                self, APP_NAME,
                '这个索引的书库已经不在了：\n\n%s\n\n'
                '现在更新，引擎会把索引里记的文件全判成"已删除"再写回去，'
                '等于把索引清空。\n\n已拦住，不更新。' % names)
            return
        # ② 主要书库还在，但别的目录有缺失 —— 说清楚会丢什么，交给用户定
        miss = st.get('missing') or []
        if miss:
            shown = '\n'.join('  × %s' % s for s in miss[:10])
            if len(miss) > 10:
                shown += '\n  ……还有 %d 个' % (len(miss) - 10)
            r = QMessageBox.question(
                self, APP_NAME,
                '主要书库都在，可以更新。\n\n'
                '但有 %d 个目录现在找不到。更新时引擎会把这些目录里记的文件'
                '判成"已删除"，索引里属于它们的条目会丢掉：\n\n%s\n\n'
                '确定继续吗？' % (len(miss), shown),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                self._log('已取消更新（有 %d 个目录找不到）' % len(miss))
                return
        self._run(flp.update_index, {'name': e['name']},
                  '更新索引「%s」' % e['name'])

    def do_recreate(self):
        e = self._cur()
        if not e or e['kind'] == '[组]':
            return
        r = QMessageBox.question(
            self, APP_NAME,
            '重建「%s」会把现有索引整个删掉、从头扫一遍。\n'
            '大书库可能要跑很久。\n\n确定重建吗？' % e['name'],
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if r != QMessageBox.StandardButton.Yes:
            return
        self._run(flp.recreate_index, {'name': e['name']},
                  '重建索引「%s」' % e['name'])

    def do_new(self):
        d = NewIndexDialog(self)
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        name, store, srcs, ft = d.values()
        if not name or not store or not srcs:
            QMessageBox.warning(self, APP_NAME, '名称、存放位置、源目录都要填')
            return
        try:
            indexes.add_index(name, store)
        except Exception as ex:
            QMessageBox.warning(self, APP_NAME, '登记索引失败：%s' % ex)
            return
        # 新建只是把索引的"壳"搭起来（实测 1.8 秒），内容要第一次更新才
        # 真正收录进去 —— 所以建完自动接一次更新，免得用户建完一搜是空的。
        self._after_create = name
        self._run(flp.create_index,
                  {'name': name, 'path': store, 'dirs': srcs,
                   'file_types': ft},
                  '新建索引「%s」' % name)

    def do_addref(self):
        e = self._cur()
        path = QFileDialog.getExistingDirectory(
            self, '选择已存在的索引目录（里面有 index_settings.xml 的那个）')
        if not path:
            return
        name, ok = QInputDialog.getText(self, APP_NAME, '给这个索引起个名字：',
                                        QLineEdit.Normal,
                                        os.path.basename(path) or '')
        if not ok or not name.strip():
            return
        name = name.strip()
        try:
            indexes.add_index(name, path)
        except Exception as ex:
            QMessageBox.warning(self, APP_NAME, '登记失败：%s' % ex)
            return
        self._run(flp.addref_index, {'name': name, 'path': path},
                  '引用索引「%s」' % name)

    def do_delete(self):
        e = self._cur()
        if not e:
            return
        m = QMessageBox(self)
        m.setWindowTitle(APP_NAME)
        m.setText('删除「%s」' % e['name'])
        m.setInformativeText(
            '“只从列表移除”＝这台机器上不再显示，索引数据原封不动；\n'
            '“连索引数据一起删”＝连 %s 一起从磁盘删掉。' % (e['store'] or '（无路径）'))
        b1 = m.addButton('只从列表移除', QMessageBox.ButtonRole.AcceptRole)
        b2 = m.addButton('连索引数据一起删', QMessageBox.ButtonRole.DestructiveRole)
        m.addButton('取消', QMessageBox.ButtonRole.RejectRole)
        m.exec()
        clicked = m.clickedButton()
        if clicked is b1:
            try:
                indexes.remove(e['name'])
                self._log('已从列表移除：%s（数据未动）' % e['name'])
            except Exception as ex:
                QMessageBox.warning(self, APP_NAME, '移除失败：%s' % ex)
            self.refresh()
        elif clicked is b2:
            if not e['store']:
                QMessageBox.warning(self, APP_NAME, '没有登记路径，删不了数据')
                return
            r = QMessageBox.question(
                self, APP_NAME,
                '真的要删除磁盘上的索引数据吗？\n\n%s\n\n这一步不可撤销。'
                % e['store'])
            if r != QMessageBox.StandardButton.Yes:
                return
            try:
                indexes.remove(e['name'])
            except Exception:
                pass
            import shutil
            try:
                shutil.rmtree(e['store'])
                self._log('已删除索引数据：%s' % e['store'])
            except Exception as ex:
                QMessageBox.warning(self, APP_NAME, '删数据失败：%s' % ex)
            self.refresh()

    def do_rename(self):
        e = self._cur()
        if not e:
            return
        name, ok = QInputDialog.getText(self, APP_NAME, '新的索引名：',
                                        QLineEdit.Normal, e['name'])
        if not ok or not name.strip() or name.strip() == e['name']:
            return
        name = name.strip()
        try:
            indexes.remove(e['name'])
            indexes.add_index(name, e['store'])
            self._log('已改名为：%s' % name)
        except Exception as ex:
            QMessageBox.warning(self, APP_NAME, '改名失败：%s' % ex)
        self.refresh()

    def do_group(self):
        d = GroupDialog(self)
        if d.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def do_auto(self):
        cur = self._auto_every
        v, ok = QInputDialog.getInt(
            self, APP_NAME,
            '每隔多少分钟自动更新一次列表里选中的索引？\n（0 = 不自动）',
            cur or 0, 0, 1440, 10)
        if not ok:
            return
        self._auto_every = int(v)
        self._auto_left = int(v)
        if self._auto_every:
            self._auto_timer.start()
            self._log('已开启定时更新：每 %d 分钟一次' % self._auto_every)
        else:
            self._auto_timer.stop()
            self._log('已关闭定时更新')

    def _auto_tick(self):
        if not self._auto_every or self._worker is not None:
            return
        self._auto_left -= 1
        if self._auto_left > 0:
            return
        self._auto_left = self._auto_every
        e = self._cur()
        if not e or e['kind'] == '[组]':
            return
        self._run(flp.update_index, {'name': e['name']},
                  '定时更新「%s」' % e['name'])

    def closeEvent(self, ev):
        w = self._worker
        if w is not None:
            r = QMessageBox.question(
                self, APP_NAME, '还有任务在跑，确定退出吗？')
            if r != QMessageBox.StandardButton.Yes:
                ev.ignore()
                return
            w.stop()
            try:
                w.wait(5000)
            except Exception:
                pass
        ev.accept()


# ---------------------------------------------------------------- 对话框
class NewIndexDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('新建索引')
        f = QFormLayout(self)
        self.e_name = QLineEdit()
        self.e_store = QLineEdit()
        self.e_src = QLineEdit()
        self.e_ft = QLineEdit('*.*')
        b_store = QPushButton('选择…')
        b_src = QPushButton('选择…')
        h1 = QHBoxLayout()
        h1.addWidget(self.e_store, 1)
        h1.addWidget(b_store)
        h2 = QHBoxLayout()
        h2.addWidget(self.e_src, 1)
        h2.addWidget(b_src)
        b_store.clicked.connect(lambda: self._pick(self.e_store))
        b_src.clicked.connect(lambda: self._pick(self.e_src))
        f.addRow('索引名：', self.e_name)
        f.addRow('索引存放位置：', h1)
        f.addRow('要收录的目录：', h2)
        f.addRow('文件类型：', self.e_ft)
        f.addRow(QLabel('多个类型用分号隔开，如  *.pdf;*.txt;*.doc*'))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _pick(self, edit):
        d = QFileDialog.getExistingDirectory(self, '选择目录')
        if d:
            edit.setText(d)

    def values(self):
        return (self.e_name.text().strip(), self.e_store.text().strip(),
                self.e_src.text().strip(), self.e_ft.text().strip())


class GroupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('索引组')
        v = QVBoxLayout(self)
        v.addWidget(QLabel('勾上要合进一个组的索引，起个组名：'))
        self.rows = []
        try:
            items = [e for e in indexes.list_all()
                     if (e.get('kind') or '') != 'group']
        except Exception:
            items = []
        for e in items:
            cb = QCheckBox(e.get('name') or '')
            cb.setProperty('path', e.get('path') or '')
            v.addWidget(cb)
            self.rows.append(cb)
        h = QHBoxLayout()
        self.e_name = QLineEdit()
        h.addWidget(QLabel('组名：'))
        h.addWidget(self.e_name, 1)
        v.addLayout(h)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _ok(self):
        name = self.e_name.text().strip()
        if not name:
            QMessageBox.warning(self, APP_NAME, '组名不能为空')
            return
        members = [cb.property('path') for cb in self.rows if cb.isChecked()]
        if not members:
            QMessageBox.warning(self, APP_NAME, '至少勾一个索引')
            return
        try:
            indexes.add_group(name, members)
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, '建组失败：%s' % e)
            return
        self.accept()
