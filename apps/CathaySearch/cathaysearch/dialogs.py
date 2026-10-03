# -*- coding: utf-8 -*-
"""联想词对话框：查看全部 + 增删 + 永久生效，**一个对话框管完**。

单独放在这里，一是 gui.py 已经够长，二是这东西能脱离主窗口单测。

【v0.2.8】把原来的「查看全部」和「联想词管理」合并成这一个：
以前是两个按钮开两个框，一个能勾不能存、一个能存看不全，用户在那里
蒙着猜。现在一处做完：一条一行摆全、勾宣布参与、增删、要不要永久。

自动补出来的繁简字形要**标出来**（灰色 + 〔繁简〕后缀）：它们不是这个人
的别名，只是同一个名字的另一副字形，不写进任何配置。
"""
import os

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
                             QHBoxLayout, QInputDialog, QLabel, QListWidget,
                             QListWidgetItem, QMessageBox, QPushButton,
                             QTextEdit, QVBoxLayout)

from . import indexes, path_translator as pt

DERIVED_MARK = '　〔繁简〕'


# ================================================================ 联想词
class AliasDialog(QDialog):
    """「%s」到底该联想哪些词，摆开了由你定。

    一条一行：勾上的参与本次检索；没勾的就是这次不用（勾了永久生效则
    以后也不用）；还能自己添加、删掉。
    带 〔繁简〕 的是程序按另一个字形自动补出来的，不是别名表里的存货
    ——看得见、能勾，但**不写进任何配置**。
    """

    def __init__(self, word, all_words, chosen, derived=None, parent=None):
        super().__init__(parent)
        self.word = (word or '').strip()
        self.derived = dict(derived or {})
        self.setWindowTitle('联想词 —— 「%s」' % self.word)
        self.resize(470, 500)

        v = QVBoxLayout(self)
        v.addWidget(QLabel(
            '一条一行，勾上的参与检索。勾了「永久生效」就记下来，'
            '以后搜「%s」都按这个来。' % self.word))

        self.lst = QListWidget()
        seen, items = set(), []
        for w in (list(all_words) or []):
            w = (w or '').strip()
            if not w or w == self.word or w in seen:
                continue
            seen.add(w)
            items.append(w)
        self._all_words = list(items)
        chosen = set(chosen or ())
        for w in self._all_words:
            it = QListWidgetItem(w + (DERIVED_MARK if w in self.derived else ''))
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable
                        | Qt.ItemFlag.ItemIsEditable)
            it.setData(Qt.ItemDataRole.UserRole, w)
            it.setCheckState(Qt.CheckState.Checked if w in chosen
                             else Qt.CheckState.Unchecked)
            if w in self.derived:
                it.setForeground(QColor('#7A6A00'))
                src = self.derived.get(w) or ''
                it.setToolTip(
                    '不是别名表里的称呼，是「%s」换了个字形写出来的。\n'
                    '同一个名字在繁体书里写作这一副，搜繁体书库缺它不可。\n'
                    '它看得见、能勾掉，但不会写进任何配置。' % src)
            self.lst.addItem(it)
        v.addWidget(self.lst, 1)

        row = QHBoxLayout()
        for txt, fn in (('添加', self._add), ('删掉选中的', self._del),
                        ('全选', True), ('全不选', False)):
            b = QPushButton(txt)
            if callable(fn):
                b.clicked.connect(fn)
            else:
                b.clicked.connect(lambda _c, s=fn: self._set_all(s))
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)

        self.chk_keep = QCheckBox('永久生效（记下来，下次打开还在）')
        self.chk_keep.setChecked(False)
        self.chk_keep.setToolTip(
            '勾上：这份勾选写进程序目录下的 runtime，以后搜「%s」\n'
            '都按这次的来 —— 勾上的留下、没勾的不再冒出来。\n'
            '不勾：只改这一次，跟在输入框里手改一样。\n'
            'CBDB 那份别名表一个字节都不会动。' % self.word)
        v.addWidget(self.chk_keep)

        self.tip = QLabel('')
        self.tip.setStyleSheet('color:#666;')
        v.addWidget(self.tip)
        self._syncing = False
        self._upd_tip()
        self.lst.itemChanged.connect(self._upd_tip)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText('就用这些')
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText('算了')
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    # ------------------------------------------------------------ 列表
    def _word_at(self, row):
        """行的"真词"。

        词本身存在 UserRole 里，屏幕上那行字数还有 〔繁简〕 这种装饰，
        所以取词一律走这里，别拿 item.text() 当词。
        """
        it = self.lst.item(row)
        raw = (it.data(Qt.ItemDataRole.UserRole) or '').strip()
        if raw:
            return raw
        return it.text().replace(DERIVED_MARK, '').strip()

    def _set_all(self, on):
        st = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        for i in range(self.lst.count()):
            self.lst.item(i).setCheckState(st)

    def _add(self):
        it = QListWidgetItem('新联想词')
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsEditable)
        it.setData(Qt.ItemDataRole.UserRole, '新联想词')
        it.setCheckState(Qt.CheckState.Checked)
        self.lst.addItem(it)
        self.lst.editItem(it)
        self._upd_tip()

    def _del(self):
        for it in list(self.lst.selectedItems()):
            self.lst.takeItem(self.lst.row(it))
        self._upd_tip()

    def _upd_tip(self):
        if self._syncing:
            return
        self._syncing = True
        try:
            # 行是可编辑的：手改过就把 UserRole 跟着更新，
            # 否则取出来还是改动前那个词
            for i in range(self.lst.count()):
                it = self.lst.item(i)
                raw = it.text().replace(DERIVED_MARK, '').strip()
                if raw and raw != (it.data(Qt.ItemDataRole.UserRole) or ''):
                    it.setData(Qt.ItemDataRole.UserRole, raw)
        finally:
            self._syncing = False
        p = self.picked()
        n_d = len([x for x in p if x in self.derived])
        tail = '（其中 %d 个是自动补的繁简字形，不保存）' % n_d if n_d else ''
        self.tip.setText('当前 %d 个联想词%s' % (len(p), tail))

    def all_words(self):
        return [self._word_at(i) for i in range(self.lst.count())
                if self._word_at(i)]

    def picked(self):
        out = []
        for i in range(self.lst.count()):
            it = self.lst.item(i)
            if (it.checkState() == Qt.CheckState.Checked
                    and self._word_at(i)):
                out.append(self._word_at(i))
        return out

    def not_picked(self):
        """列表里摆着、但这次没勾的（= 用户明确不要的）。"""
        p = set(self.picked())
        return [w for w in self.all_words() if w not in p]

    def keep(self):
        return self.chk_keep.isChecked()


# ================================================================ 索引管理
class IndexDialog(QDialog):
    """索引的查看 / 添加 / 删除 / 组成索引组。

    一个坑先说清楚：FileLocator 的命令行一次只能对一个目标（一个索引，
    或一个索引组），所以"多索引联合检索"= 先把它们**攒成一个组**，再让
    引擎按组搜。组是 FileLocator 的原生能力，跨 5 个索引跟搜 1 个一样快
    （实测 2.26 秒），不是我们自己在应用层把结果拼起来。
    """

    def __init__(self, entries, selected, parent=None):
        """entries: indexes.list_all() 的结果；selected: 当前勾着的索引名."""
        super().__init__(parent)
        self.setWindowTitle('索引管理')
        self.resize(620, 420)

        v = QVBoxLayout(self)
        v.addWidget(QLabel(
            '勾选要用来检索的索引：勾 1 个 = 单索引检索，'
            '勾 ≥2 个 = 联合检索（自动攒成一个索引组）。'))

        self.lst = QListWidget()
        self._fill(entries, selected)
        v.addWidget(self.lst, 1)

        row = QHBoxLayout()
        b_add = QPushButton('添加索引…')
        b_add.setToolTip('挑一个已经建好的索引目录（FileLocator 建的那种）')
        b_add.clicked.connect(self._add)
        row.addWidget(b_add)

        b_upd = QPushButton('更新索引…')
        b_upd.setToolTip('把勾选的索引更新到最新（只处理新增、改动、删掉的'
                         '文件）\n引擎要 9.3 及以上 —— 9.0 的命令行工具是坏的')
        b_upd.clicked.connect(self._update)
        row.addWidget(b_upd)

        b_grp = QPushButton('把勾选的存为索引组…')
        b_grp.setToolTip('给当前勾选的这个起个名字，以后一次点选')
        b_grp.clicked.connect(self._save_group)
        row.addWidget(b_grp)

        b_del = QPushButton('删除…')
        b_del.setToolTip('只能删本程序自己登记的索引/组，'
                         '你在 FileLocator 里手工建的不碰')
        b_del.clicked.connect(self._remove)
        row.addWidget(b_del)
        row.addStretch(1)
        v.addLayout(row)

        self.tip = QLabel('')
        self.tip.setStyleSheet('color:#666;')
        v.addWidget(self.tip)
        self.lst.itemChanged.connect(self._upd_tip)
        self._upd_tip()

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText('用这些检索')
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    # ------------------------------------------------------------ 列表
    def _fill(self, entries, selected):
        self.lst.blockSignals(True)
        self.lst.clear()
        for e in entries:
            if e['kind'] == 'group':
                ms = indexes.group_members(e['name'])
                desc = '索引组 · %d 个成员' % len(ms)
            else:
                alive = '' if os.path.isdir(e['path']) else '（目录已不在！）'
                desc = '索引 %s' % alive
            it = QListWidgetItem('%s    [%s]\n    %s'
                                 % (e['name'], desc, e['path']))
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setData(Qt.ItemDataRole.UserRole, e['name'])
            it.setCheckState(Qt.CheckState.Checked
                             if e['name'] in selected
                             else Qt.CheckState.Unchecked)
            self.lst.addItem(it)
        self.lst.blockSignals(False)

    def _names(self):
        return [self.lst.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.lst.count())]

    def picked(self):
        out = []
        for i in range(self.lst.count()):
            it = self.lst.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                out.append(it.data(Qt.ItemDataRole.UserRole))
        return out

    def _upd_tip(self):
        p = self.picked()
        self.tip.setText(
            '已勾选 %d 个：%s' % (len(p), '、'.join(p)) if p
            else '一个都没勾（会退回上一次的选择）')

    # ------------------------------------------------------------ 动作
    def _add(self):
        d = QFileDialog.getExistingDirectory(
            self, '挑索引所在的目录（里面应有 Lucene 索引数据）')
        if not d:
            return
        base = os.path.basename(d.rstrip('\\/'))
        name, ok = QInputDialog.getText(
            self, '索引名', '给这个索引起个名字：', text=base)
        if not ok:
            return
        ok2, msg = indexes.add_index(name, d)
        if not ok2:
            QMessageBox.warning(self, '没添上', msg)
            return
        self._fill(indexes.list_all(), self.picked() + [name.strip()])

    def _update(self):
        """把勾选的索引更新到最新。

        进来先过三道关，一关不过就不动手：
          1. 勾的是不是普通索引（组只能更新它的成员）；
          2. 引擎的 flpidx.exe 活着吗（9.0 那份是死的，早探早说）；
          3. **索引的源目录还在吗** —— 不在就绝不能更新：引擎会把索引里
             记的文件全判成"已删除"再写回，等于把索引清空。
        """
        from . import flp

        entries = {e['name']: e for e in indexes.list_all()}
        picked = self.picked()
        groups = [n for n in picked
                  if entries.get(n, {}).get('kind') != 'index']
        names = [n for n in picked
                 if entries.get(n, {}).get('kind') == 'index']
        if not names:
            QMessageBox.information(
                self, '更新哪个',
                '先勾一个要更新的索引。\n'
                + ('（「%s」是索引组，组本身不能更新，请改成勾它的成员索引）'
                   % '、'.join(groups) if groups else ''))
            return

        alive, why = flp.flpidx_alive()
        if not alive:
            QMessageBox.warning(self, '更新不了', why)
            return

        todo, blocked, risk = [], [], []
        for n in names:
            # 一条索引可能索引几十个目录（主索引就挂了 28 个）。
            # 判定只看"最主要的"那几个：只要它们在，就允许更新。
            st = pt.dir_status(entries[n].get('path', ''))
            if not st.get('sources'):
                blocked.append((n, '查不到它当初索引的是哪个目录'))
            elif st.get('state') == 'missing':
                nm = '、'.join((w.get('key') or '')
                               for w in (st.get('missing_main') or []))
                blocked.append((n, '主要书库已经不在了：%s'
                                % (nm or '、'.join(st['sources'][:3]))))
            else:
                todo.append(n)
                if st.get('missing'):
                    risk.append((n, st['missing']))

        msg = ''
        if blocked:
            msg += ('下面这些**不能更新**，已经跳过：\n'
                    + '\n'.join('· %s —— %s' % (n, w) for n, w in blocked)
                    + '\n\n书库整个没了还去更新，引擎会把索引里记的文件全判成'
                      '"已删除"写回去，索引就空了。\n\n')
        if not todo:
            QMessageBox.warning(self, '一个都更新不了', msg)
            return

        # 主要书库在、但别处有缺失：先说清楚会丢什么，让用户自己定
        if risk:
            msg += ('注意：这些索引有部分目录现在找不到，更新时引擎会把这些'
                    '目录里记的文件判成"已删除"，索引里属于它们的条目会丢掉：\n')
            for n, miss in risk:
                msg += ('· %s —— 找不到 %d 个目录（如 %s%s）\n'
                        % (n, len(miss), '、'.join(miss[:2]),
                           '…' if len(miss) > 2 else ''))
            msg += '\n'

        msg += ('要更新这 %d 个索引：\n' % len(todo)
                + '\n'.join('· ' + n for n in todo)
                + '\n\n只会处理新增、改动和删掉的文件。索引大的话可能要跑'
                  '很久，期间别同时检索。')
        if QMessageBox.question(self, '确认更新', msg) != \
                QMessageBox.StandardButton.Yes:
            return

        dlg = _UpdateDialog(todo, self)
        dlg.exec()

    def _save_group(self):
        p = self.picked()
        if len(p) < 2:
            QMessageBox.information(self, '不用存',
                                    '至少勾 2 个索引才谈得上"组"')
            return
        name, ok = QInputDialog.getText(self, '组名', '给这个索引组起个名字：',
                                        text='我的联合检索')
        if not ok:
            return
        ok2, msg = indexes.add_group(name, p)
        if not ok2:
            QMessageBox.warning(self, '没建成', msg)
            return
        self._fill(indexes.list_all(), [name.strip()])

    def _remove(self):
        p = self.picked()
        if not p:
            QMessageBox.information(self, '删哪个', '先勾一个要删的')
            return
        n = p[0]
        if QMessageBox.question(
                self, '确认删除', '确定删掉「%s」？\n'
                '（只是撤销本程序的登记，索引数据本身不会动）'
                % n) != QMessageBox.StandardButton.Yes:
            return
        ok, msg = indexes.remove(n)
        self._fill(indexes.list_all(), [])
        if not ok:
            QMessageBox.warning(self, '删不掉', msg)

    def targets_of(self, names):
        """勾选的名字 → 真正要检索的目标列表（我们攒的组会摊平成成员）。"""
        return indexes.resolve_targets(names)


# ============================================================ 更新索引
class _UpdateWorker(QThread):
    """挨个调 flpidx 更新，放线程里跑界面才不假死。

    关窗口时必须先 stop() 再 wait()：否则线程还在跑、界面已经销毁，
    退出时会崩（我们吃过这个亏，表现是退出码 127）。
    """
    sig_line = pyqtSignal(str)
    sig_one = pyqtSignal(str, dict)
    sig_all = pyqtSignal(list)

    def __init__(self, names, parent=None):
        super().__init__(parent)
        self.names = list(names)
        self.results = []
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        from . import flp
        for n in self.names:
            if self._stop:
                break
            self.sig_line.emit('────── 开始更新「%s」' % n)
            r = flp.update_index(n,
                                 on_line=self.sig_line.emit,
                                 cancel=lambda: self._stop)
            self.results.append((n, r))
            self.sig_one.emit(n, r)
            if r['cancelled']:
                break
        self.sig_all.emit(self.results)


class _UpdateDialog(QDialog):
    """更新过程的实时输出。索引大，得让用户看得见它在动。"""

    def __init__(self, names, parent=None):
        super().__init__(parent)
        self.setWindowTitle('更新索引')
        self.resize(600, 400)
        v = QVBoxLayout(self)
        v.addWidget(QLabel('正在更新 %d 个索引，期间别同时检索：'
                           % len(names)))
        self.box = QTextEdit()
        self.box.setReadOnly(True)
        v.addWidget(self.box, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.b_stop = QPushButton('停止')
        self.b_stop.clicked.connect(self._stop)
        row.addWidget(self.b_stop)
        v.addLayout(row)

        self._done = False
        self.worker = _UpdateWorker(names, self)
        self.worker.sig_line.connect(self.box.append)
        self.worker.sig_one.connect(self._one)
        self.worker.sig_all.connect(self._all)
        self.worker.start()

    def _one(self, n, r):
        tag = ('完成' if r['ok'] else
               '已取消' if r['cancelled'] else
               '超时' if r['timed_out'] else '失败')
        self.box.append('【%s】%s　用时 %.1f 秒　返回码 %s'
                        % (n, tag, r['elapsed'], r['rc']))

    def _all(self, results):
        self._done = True
        self.b_stop.setText('关闭')
        bad = [n for n, r in results if not r['ok']]
        self.box.append('')
        self.box.append('全部结束：%s'
                        % ('都更新好了' if not bad
                           else '有 %d 个没成 —— %s' % (len(bad), '、'.join(bad))))
        if bad:
            for n, r in results:
                if r['ok']:
                    continue
                self.box.append('  · %s：返回码 %s' % (n, r['rc']))
                for s in r.get('tail', [])[-5:]:
                    self.box.append('      %s' % s)

    def _stop(self):
        if self._done:
            self.accept()
            return
        self.b_stop.setEnabled(False)
        self.box.append('正在停止…（已经写进去的部分不会撤）')
        self.worker.stop()

    def closeEvent(self, e):
        if not self._done:
            self.worker.stop()
            self.worker.wait(5000)
        super().closeEvent(e)
