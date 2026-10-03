# -*- coding: utf-8 -*-
"""CathaySearch 图形界面（PyQt6）。

界面刻意模仿 FileLocator Pro：**一张扁平的结果表**，每行一个文件，
列出 名称 / 所在文件夹 / 大小 / 类型 / 修改时间。不做"书—文件"两级目录，
也不在这里预览正文——本程序只回答"这个词出现在哪些文件里"，
看内容请到 CathayViewer（双击结果会带页码直接翻到那一页）。

两段式检索，与 M1 实测结论一致：
  第一段  FileLocator Pro 索引 → 81 GiB 里"哪些文件有这个词"(-ocn，秒级)
  第二段  PyMuPDF 打开单本 PDF → "在第几页"(0.33 秒 / 400 页)

按 v0.2.8 用户口径：
  · 联想词（CBDB 别名）与繁简通搜都**默认开启**，类型默认「全部类型」；
  · 「萧耀南」与「蕭耀南」必须给出同一套联想词 —— 别名表是按字形分行的，
    少了哪一个字形就少一半存货，所以查表一律先合并同一组字形的全部行；
  · 联想词在这一个对话框里管完：查看、勾选、增删、要不要永久生效；
  · 删除是**真的删**：这次临时删掉也好、勾了永久生效也好，
    都不许下一次检索又把它端回来（另有一份"减法表"记账）。
联想词不做分级、不做降权：短别名（任公、卓如）确实会误命中，
但"愿任公断"这类误报机器判断不了，交给使用者人眼筛。
"""
import os
import sys
import time
import subprocess

from PyQt6.QtCore import (Qt, QThread, pyqtSignal, QSettings, QTimer,
                          QSize)
from PyQt6.QtGui import QFont, QColor, QAction
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QComboBox, QCheckBox, QLabel,
    QTreeWidget, QTreeWidgetItem, QStatusBar, QMessageBox,
    QHeaderView, QAbstractItemView, QMenu, QProgressBar, QCompleter,
    QStyleFactory,
)

from . import (config, flp, aggregate, locate, alias, hits, indexes, simplify,
               path_translator as pt)
from .dialogs import AliasDialog, IndexDialog

APP_NAME = 'CathayHub Search'
APP_VER = '0.2.14'
APP_TITLE = 'CathayHub Search 学术书库搜索工具'

# CathayViewer 主程序：本目录里的 CathayHub Viewer 优先（整合后二者同目录），
# 找不到再退回老发布目录里版本号最高的那个。都找不到就隐藏按钮。
CV_EXE = config.find_viewer()

EXT_ALL = '全部类型'
EXT_PRESETS = [
    ('仅 PDF', {'.pdf'}),
    ('PDF + EPUB', {'.pdf', '.epub'}),
    ('PDF + EPUB + TXT', {'.pdf', '.epub', '.txt'}),
    (EXT_ALL, None),
]

# 结果列：与 FileLocator Pro 的结果列表对齐。
# 第 6 列「命中词」只在多词检索（开了联想词）时出现；单关键词时藏起来。
COLUMNS = ['名称', '所在文件夹', '大小', '类型', '修改时间', '命中词',
           '来自索引']
COL_HIT = 5
COL_SRC = 6

# 默认用联想词检索：别名表是按字形分行的人名词表，默认只搜一个词
# 等于白放着几十个别名不用（"吴佩孚"在库里常写作"孚威将军"）
DEFAULT_USE_ALIAS = True

# 索引下拉里"这一个到底叫什么"存在这个格子里（Qt 预留给用户自定义的第一个）
NAME_ROLE = Qt.ItemDataRole.UserRole + 1
# 繁简通搜同样默认开：表是按字形建的，不换字形就等于认不得另一半
DEFAULT_USE_VARIANT = True

# 索引下拉旁边那个按钮的叫法（连 help 里提到的措辞都对准同一个常量）
INDEX_BTN_TEXT = '索引管理'

# 多词模式下同时放几个 FileLocator 进程；
# 索引在机械盘/网盘上时并发收益有限，4 个是实测不互相拖死的数
MAX_CONCURRENT_SEARCH = 4


class _Item(QTreeWidgetItem):
    """大小列按字节数排，不能按 "1.2 GB" 这种字符串排
    （字符串比较会把 1.2 GB 排在 900 MB 前面）。"""

    def __lt__(self, other):
        tw = self.treeWidget()
        if tw is not None and tw.sortColumn() == 2:
            a = self.data(2, Qt.ItemDataRole.UserRole)
            b = other.data(2, Qt.ItemDataRole.UserRole)
            if isinstance(a, int) and isinstance(b, int):
                return a < b
        return super().__lt__(other)


# ---------------------------------------------------------------- 后台线程
# 关窗时还没跑完的线程寄养在这里：QThread 在运行中被销毁不是抛异常，
# 是把进程直接带崩（用户看到的就是"关个窗口，整个程序没了"）。
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
            w.setParent(None)          # 别再挂在主窗口名下，否则关窗时一起被拆
            _ORPHAN_THREADS.append(w)
        except Exception:
            pass


class SearchWorker(QThread):
    """索引检索。可能跑几十秒，必须离开主线程。"""
    done = pyqtSignal(object, object)      # hits, meta
    failed = pyqtSignal(str)
    progress = pyqtSignal(int)             # 已解析到的条数（边跑边报，防"假死"）
    word_done = pyqtSignal(str, int)       # 某个词跑完了：词、命中文件数

    def __init__(self, words, targets, timeout, parent=None):
        """words: 要检索的词列表（第 1 个是主词，其后是联想词）。
        targets: 要检索的索引名列表（1 个 = 单索引，多个 = 联合检索）。

        【v0.2.4】多词不再合成一条 OR 表达式 —— 合成后就分不清某个文件
        到底是被"吴佩孚"命中的，还是被短别名"任公"误撞的。现在**每个词
        各检索一次**（并行），再按路径合并去重，边合并边记下这个文件是被
        哪些词命中的，结果表里就能给出"命中词"一列。

        【v0.2.5】多个索引也不合成一个 FileLocator 原生组，而是**每个索引
        各跑一遍**再合并：原生组是一次调用横跨多库（实测 2.26 秒），但它对
        外面是个黑盒——某个成员"假空"（同表达式一次 11391、一次 0）发生在
        它内部时无法察觉，只能看到总数莫名少了一块。各跑一遍耗时相同
        （实测 2.23 秒），却能让每个索引各自有失败重试，还能标明这条结果
        出自哪个索引。
        """
        super().__init__(parent)
        self.words = [w for w in (words or []) if w and w.strip()]
        self.targets = [t for t in (targets or []) if t] or [
            config.DEFAULT_INDEX]
        self.index = self.targets[0]      # 兼容老代码/单索引路径
        self.timeout = timeout
        self._stop = False
        self._n = 0
        self._last_emit = 0.0

    def stop(self):
        self._stop = True

    def _cancelled(self):
        return self._stop

    def _on_line(self, _hit):
        """FileLocator 一边写 XML 我们一边数，状态栏就一直在动。"""
        self._n += 1
        now = time.time()
        if now - self._last_emit >= 0.4:
            self._last_emit = now
            self.progress.emit(self._n)

    # ------------------------------------------------------------ 跑起来
    def run(self):
        try:
            if len(self.words) <= 1 and len(self.targets) <= 1:
                hits, meta = self._run_one()
            else:
                hits, meta = self._run_multi()
        except Exception as e:
            self.failed.emit(str(e))
            return
        self.progress.emit(len(hits))
        self.done.emit(hits, meta)

    def _run_one(self):
        """单关键词 × 单索引：一次检索，命中词就只有它自己。"""
        w = self.words[0]
        hits, meta = flp.run_search(
            w, index=self.index, boolean=False, timeout=self.timeout,
            cancel=self._cancelled, on_line=self._on_line,
            include_folders=True)
        meta['multi'] = False
        meta['per_word'] = [(w, len(hits))]
        # 只有一个词，"命中词"列没信息量（每行都一样），不给
        meta['words_by_path'] = {}
        meta['targets'] = list(self.targets)
        return hits, meta

    def _run_multi(self):
        """多词 / 多索引：每个「词 × 索引」组合各搜一次，再按路径合并。"""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        # 任务表：每个 (词, 索引) 一个。标签给人看（"吴佩孚 @ 主索引"）
        tasks = [(w, t) for w in self.words for t in self.targets]
        results = [None] * len(tasks)
        metas = [None] * len(tasks)

        def job(i, w, t):
            # 每个词各跑一次引擎。这里 retries 降到 1：多词本来就重，
            # 某个词真 0 命中重试两次纯属浪费（重试一次已能挡住"假空"）。
            hh, mm = flp.run_search(
                w, index=t, boolean=False, timeout=self.timeout,
                cancel=self._cancelled, retries=1,
                include_folders=True)
            results[i] = hh
            metas[i] = mm

        nproc = min(MAX_CONCURRENT_SEARCH, len(tasks))
        pool = ThreadPoolExecutor(max_workers=nproc)
        try:
            futs = [pool.submit(job, i, w, t)
                    for i, (w, t) in enumerate(tasks)]
            reported = set()          # 每个任务只报一次进度
            for fu in as_completed(futs):
                fu.result()          # 子线程里的异常照旧往外抛
                # 趁还没全部跑完，先给个阶段性反馈：某个词/某个索引出了多少
                for i, m in enumerate(metas):
                    if m is not None and i not in reported:
                        reported.add(i)
                        self.word_done.emit(
                            '%s＠%s' % (tasks[i][0], tasks[i][1]),
                            len(results[i] or []))
                self.progress.emit(sum(len(r or []) for r in results))
        finally:
            # 取消时要立刻收摊：不等没跑完的任务
            pool.shutdown(wait=not self._stop, cancel_futures=True)

        by_path = {}
        words_by_path = {}
        target_by_path = {}
        per_word = [(w, 0) for w in self.words]
        per_target = {t: 0 for t in self.targets}
        n_targets = max(len(self.targets), 1)
        for i, (w, t) in enumerate(tasks):
            hh = results[i] or []
            for h in hh:
                # 同一路径只在第一次出现时登记 Hit；命中词/命中索引累加
                if h.path not in by_path:
                    by_path[h.path] = h
                    words_by_path[h.path] = []
                    target_by_path[h.path] = []
                if w not in words_by_path[h.path]:
                    words_by_path[h.path].append(w)
                if t not in target_by_path[h.path]:
                    target_by_path[h.path].append(t)

        # 计数一律按**去重后的文件数**给，不能按命中次数累加：
        # 一个文件被 6 个词命中就被记 6 次、被 3 个库收录再乘 3，
        # 状态栏会冒出 38,645 这种比库里实际命中还大的数，看着像 bug
        per_word = [(w, sum(1 for ws in words_by_path.values() if w in ws))
                    for w in self.words]
        per_target = {t: sum(1 for ts in target_by_path.values() if t in ts)
                      for t in self.targets}

        hits = list(by_path.values())
        meta = {
            'elapsed': max((m or {}).get('elapsed', 0.0) for m in metas) \
                if metas else 0.0,
            'elapsed_total': sum((m or {}).get('elapsed', 0.0) for m in metas),
            'expr': ' OR '.join(self.words),
            'index': self.targets[0],
            'targets': list(self.targets),
            'multi': True,
            'per_word': per_word,
            'per_target': per_target,
            'words_by_path': words_by_path,
            'target_by_path': target_by_path,
            'cancelled': self._stop,
            'timed_out': any((m or {}).get('timed_out') for m in metas),
            'attempts': 1,
            'retried': False,
            'found_text': '%d 个文件（%d 个词 × %d 个索引，分别检索后合并）'
                          % (len(hits), len(self.words), len(self.targets)),
        }
        return hits, meta


class LocateWorker(QThread):
    """单本 PDF 内定位页码。"""
    done = pyqtSignal(object, object, object)   # pages, snippets, meta
    failed = pyqtSignal(str)

    def __init__(self, path, words, parent=None):
        super().__init__(parent)
        self.path = path
        self.words = words

    def run(self):
        try:
            pages, snips, meta = locate.find_pages(self.path, self.words)
        except Exception as e:
            self.failed.emit(str(e))
            return
        self.done.emit(pages, snips, meta)


# ---------------------------------------------------------------- 主窗口
class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle('%s %s' % (APP_TITLE, APP_VER))
        self.resize(1360, 820)

        self._hits = []          # 当前检索到的全部文件
        self._meta = {}
        self._worker = None
        self._lworker = None
        self._t0 = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._hist = []
        # 双击后"算完页码再打开"的暂存
        # 【v0.2.10】_pending_words 必须在这里先落地：open_selected_cv() 那条路
        # 只设了 _pending_open，如果 _on_locate_done 直接读属性就会 AttributeError
        # （在 worker 线程里崩，表现为进程静默退出码 127，很难查）。
        self._pending_open = None
        self._pending_words = None
        # 自动展开的"上一次"状态，用来判断用户有没有手动改过联想词
        self._auto_word = ''
        self._auto_val = ''
        # 繁简通搜派生出来的词：{派生词: 它的原词}。
        # 派生词**看得见**（显示在联想词框里，打〔繁简〕标记）但**不保存**
        # ——不写 ini、不进 user_alias.json、不进"永久生效"。它只是此刻
        # 算出来的影子，原词一没，影子跟着没。
        self._derived = {}
        # 多词检索：路径 → 这个文件被哪几个词命中；以及每个词各自的命中数
        self._words_by_path = {}
        self._per_word = []
        self._target_by_path = {}
        # 临时联合检索选了哪些索引（还原下次打开时的勾选状态用）
        self._temp_members = []
        # 主词改了就重新去查别名（去抖，别每敲一个字查一次）
        self._alias_timer = QTimer(self)
        self._alias_timer.setSingleShot(True)
        self._alias_timer.setInterval(350)
        self._alias_timer.timeout.connect(self._refresh_alias_now)

        self.settings = QSettings(
            os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), 'cathaysearch.ini'),
            QSettings.Format.IniFormat)

        self._build_ui()
        self._restore()

    # ------------------------------------------------------------ 界面
    def _build_ui(self):
        f = QFont('Microsoft YaHei UI', 9)
        QApplication.setFont(f)

        root = QWidget()
        v = QVBoxLayout(root)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        # ---- 第一行：检索词 + 按钮
        r1 = QHBoxLayout()
        r1.addWidget(QLabel('检索词'))
        self.ed_word = QLineEdit()
        self.ed_word.setPlaceholderText(
            '输入要找的词，回车开始（多个词用空格分隔 = 同时包含，例：吴佩孚 电报）')
        self.ed_word.setToolTip(
            '检索规则（FileLocator 引擎实测）：\n'
            '  吴佩孚            → 找含这个词的文件\n'
            '  吴佩孚 电报        → 空格 = 同时包含两者（AND），不是短语\n'
            '  "吴佩孚"          → 加引号 = 精确短语\n'
            '  默认只搜你输入的这个词；勾选"联想词"后才会把别名（子玉、玉帅…）\n'
            '  一起并进来，主词与联想词之间是 OR（任一命中即可）')
        self.ed_word.setMinimumWidth(220)
        self.ed_word.returnPressed.connect(self.do_search)
        r1.addWidget(self.ed_word, 2)

        self.btn_go = QPushButton('检索')
        self.btn_go.setFixedWidth(80)
        self.btn_go.clicked.connect(self.do_search)
        r1.addWidget(self.btn_go)

        self.btn_stop = QPushButton('停止')
        self.btn_stop.setFixedWidth(80)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.do_stop)
        r1.addWidget(self.btn_stop)

        self.pbar = QProgressBar()
        self.pbar.setRange(0, 0)
        self.pbar.setFixedWidth(120)
        self.pbar.setVisible(False)
        r1.addWidget(self.pbar)

        v.addLayout(r1)

        # ---- 第二行：联想词开关 + 联想词框 + 索引 + 类型
        r2 = QHBoxLayout()
        self.chk_alias = QCheckBox('联想词')
        self.chk_alias.setChecked(DEFAULT_USE_ALIAS)
        self.chk_alias.setToolTip(
            '默认开启。按 CBDB 别名表自动展开（吴佩孚 → 子玉、吴玉帅、玉帅、\n'
            '孚威将军…），主词与联想词之间是 OR，命中数会明显变多、也会更慢。\n'
            '短联想词（任公、卓如）会有误命中，机器判断不了，由你人眼筛；\n'
            '嫌吵就在这儿取消勾选，回到只搜你输入的那个词。')
        self.chk_alias.toggled.connect(self._on_alias_toggled)
        r2.addWidget(self.chk_alias)

        # 繁简通搜：默认开（表是按字形建的，不换字形就认不得另一半）
        self.chk_variant = QCheckBox('繁简通搜')
        self.chk_variant.setChecked(True)
        self.chk_variant.setToolTip(
            '默认开启。做两件事：\n'
            '① 查表时——你打简体「萧耀南」，表里只有繁体「蕭耀南」，\n'
            '  换个字形再查一次，别让"查无此人"白白吞掉一整组别名；\n'
            '② 检索时——联想词里缺的那个字形自动补上（萧耀南 ↔ 蕭耀南）。\n'
            '  繁简同形的词（行珊）不重复补。\n'
            '补出来的词看得见、能删，**但不会存进任何配置**。')
        self.chk_variant.toggled.connect(self._on_variant_toggled)
        r2.addWidget(self.chk_variant)

        self.ed_alias = QLineEdit()
        self.ed_alias.setPlaceholderText(
            '自动填入联想词；这次可以临时用顿号增删，不写入任何配置')
        self.ed_alias.setToolTip(
            '这里列出的词会与主词一起检索（每个词各搜一次，结果标明是被哪个词命中的）。\n'
            '改动只对本次有效：换个检索词或取消勾选就恢复，不会记进配置文件。')
        self.ed_alias.returnPressed.connect(self.do_search)
        # 改完（回车或失焦）补一次缺的字形、把没了原词的派生词连坐清掉
        self.ed_alias.editingFinished.connect(self._on_alias_edited)
        r2.addWidget(self.ed_alias, 3)

        # 有没有查到联想词、查到了几个，这里一句话说清楚。
        # 一个别名都没有时，输入框直接藏掉——不摆个空框让人猜。
        self.lb_alias = QLabel('')
        self.lb_alias.setStyleSheet('color:#666;')
        r2.addWidget(self.lb_alias)

        # 联想词的查看 / 勾选 / 增删 / 永久生效，一个对话框管完。
        # 以前分「查看全部」和「联想词…」两个按钮：一个能勾不能存、
        # 一个能存却看不全，人得两头猜。现在一处做完。
        self.btn_alias = QPushButton('联想词…')
        self.btn_alias.setToolTip(
            '这个词该联想哪些称呼，一条一行摆开了由你定。\n'
            '带 〔繁简〕 的是按另一个字形自动补出来的（不写进配置）。\n'
            '勾「永久生效」就记下来，下次打开还在 —— 这次没勾的也不再冒出来。')
        self.btn_alias.clicked.connect(self._open_alias_box)
        r2.addWidget(self.btn_alias)

        # 繁简转换：书本有繁简两版，检索词也得跟着换，否则搜繁体书
        # 用简体词必然 0 命中。按钮带下拉选方向（规则跟 CathayShelf 一致）。
        self.btn_conv = QPushButton('繁简 ▾')
        self.btn_conv.setToolTip(
            '把检索词和联想词一起转繁或转简。\n'
            '搜繁体书库就得用繁体词，搜简体版就得用简体词 —— 一个字都错不得。')
        self._conv_menu = QMenu(self.btn_conv)
        self._act_t2s = self._conv_menu.addAction('转成简体（繁→简）')
        self._act_s2t = self._conv_menu.addAction('转成繁体（简→繁）')
        self._act_t2s.triggered.connect(lambda: self._convert_words('t2s'))
        self._act_s2t.triggered.connect(lambda: self._convert_words('s2t'))
        self.btn_conv.setMenu(self._conv_menu)
        r2.addWidget(self.btn_conv)

        r2.addWidget(QLabel('索引'))
        self.cb_index = QComboBox()
        self.cb_index.setMinimumWidth(200)
        self.cb_index.setToolTip(
            '单索引 = 只搜它；带「组」字 = 一次横跨好几个索引（FileLocator\n'
            '原生能力，跨 5 个索引跟搜 1 个一样快）。点「索引管理」可以添加索引、\n'
            '把几个索引攒成一个组、或者删掉本程序自己建的条目。')
        r2.addWidget(self.cb_index, 1)

        self.btn_idx = QPushButton(INDEX_BTN_TEXT)
        self.btn_idx.setToolTip('添加索引 / 组建索引组 / 删除本程序登记的条目')
        self.btn_idx.clicked.connect(self._manage_index)
        r2.addWidget(self.btn_idx)

        r2.addWidget(QLabel('类型'))
        self.cb_ext = QComboBox()
        self.cb_ext.addItems([p[0] for p in EXT_PRESETS])
        self.cb_ext.currentIndexChanged.connect(self._refill)
        r2.addWidget(self.cb_ext)

        # 【v0.2.9】文件夹名也是被索引的：实测「中国」在学术信息索引里
        # 命中 3,392 个文件夹。以前全被丢掉，用户以为搜不到。默认收进来。
        self.chk_folder = QCheckBox('含文件夹名')
        self.chk_folder.setChecked(True)
        self.chk_folder.setToolTip(
            '文件夹的**名字**里有关键词时，把文件夹本身也列进结果。\\n'
            '（FileLocator 建索引时连文件夹名一起建了，所以这些是真命中，\\n'
            '不是"命中文件的父目录"。）\\n'
            '选了具体类型（比如「仅 PDF」）时不参与——那时你要的是文件。')
        self.chk_folder.toggled.connect(self._refill)
        r2.addWidget(self.chk_folder)

        v.addLayout(r2)

        # ---- 结果表：一张扁平的列表，每行一个文件
        self.tv = QTreeWidget()
        self.tv.setHeaderLabels(COLUMNS)
        self.tv.setRootIsDecorated(False)
        self.tv.setAlternatingRowColors(True)
        self.tv.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tv.setUniformRowHeights(True)
        self.tv.setSortingEnabled(True)
        # 必须显式指定升序：Qt 的 sortIndicator 默认是"降序"，
        # 搜"吴佩孚"会把"左宗棠""左联"顶到最前面（码点比"吴"大），
        # 看上去就像"把整个库列出来了"。
        self.tv.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.tv.itemDoubleClicked.connect(self._on_double_click)
        self.tv.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.tv.customContextMenuRequested.connect(self._menu)
        h = self.tv.header()
        h.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        h.setStretchLastSection(False)
        self.tv.setColumnWidth(0, 420)
        self.tv.setColumnWidth(1, 480)
        self.tv.setColumnWidth(2, 90)
        self.tv.setColumnWidth(3, 70)
        self.tv.setColumnWidth(4, 140)
        v.addWidget(self.tv, 1)

        # ---- 底部操作行
        r3 = QHBoxLayout()
        self.lab_count = QLabel('')
        r3.addWidget(self.lab_count, 1)

        self.btn_open = QPushButton('打开')
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self.open_selected)
        r3.addWidget(self.btn_open)

        self.btn_cv = QPushButton('用阅读器打开（跳到该页）')
        self.btn_cv.setToolTip('调起 CathayViewer 并直接翻到命中那一页')
        self.btn_cv.setEnabled(False)
        self.btn_cv.clicked.connect(self.open_selected_cv)
        if not CV_EXE:
            self.btn_cv.setVisible(False)
        r3.addWidget(self.btn_cv)

        self.btn_reveal = QPushButton('打开所在文件夹')
        self.btn_reveal.setEnabled(False)
        self.btn_reveal.clicked.connect(
            lambda: self._reveal(os.path.dirname(self._current_path() or '')))
        r3.addWidget(self.btn_reveal)

        v.addLayout(r3)

        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage('就绪')

        comp = QCompleter(self._hist, self)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.ed_word.setCompleter(comp)
        # 主词一改，联想词跟着重查（去抖 350 ms），不用等你按下搜索
        self.ed_word.textChanged.connect(self._on_word_changed)

    # ------------------------------------------------------------ 配置存取
    def _restore(self):
        # 换过电脑/清过 AppData 的话，我们自己登记过的索引就都没了；
        # 账本里还记得记录了路径，这里按账本补回来
        try:
            n = indexes.heal()
            if n:
                self.statusBar().showMessage('自动恢复了 %d 个本程序登记的索引' % n)
        except Exception:
            pass
        saved = self.settings.value('index', '') or ''
        sel = [x for x in saved.split('\n') if x] or None
        sel = self._launcher_default_choice(sel)
        self._reload_indexes(sel)

    def _launcher_default_choice(self, saved):
        """Launcher 新跑了一轮向导的话，按它指定的默认来（通常是那个组）。

        Launcher 每做完向导就把默认目标和一个时间戳写进 shared/launcher.json。
        时间戳跟本地记的对不上 = 这是新一轮向导，听它的；用完把戳记下，
        以后用户自己挑什么都不再抢，直到下一轮向导。
        """
        name, stamp = config.launcher_default()
        if not name or not stamp:
            return saved
        if str(self.settings.value('default_index_stamp', '')) == stamp:
            return saved
        self.settings.setValue('default_index_stamp', stamp)
        try:
            ms = indexes.group_targets(name)
            if ms and len(ms) > 1:
                return list(ms)          # 组的话展开成成员，走逐个检索再合并
        except Exception:
            pass
        return [name]

        self.ed_word.setText(self.settings.value('word', '') or '')
        # 联想词与繁简通搜都默认开（v0.2.8）。这里 blockSignals：还原界面时
        # 不该触发"开关切换"的补词动作，下面 _refresh_alias_now 一次算到位
        self.chk_variant.blockSignals(True)
        self.chk_variant.setChecked(
            self.settings.value(
                'use_variant', str(DEFAULT_USE_VARIANT).lower()) == 'true')
        self.chk_variant.blockSignals(False)
        self.chk_alias.blockSignals(True)
        self.chk_alias.setChecked(
            self.settings.value(
                'use_alias', str(DEFAULT_USE_ALIAS).lower()) == 'true')
        self.chk_alias.blockSignals(False)
        # 类型默认「全部类型」：默认就该把库里所有格式都翻一遍，
        # 想收窄到 PDF 自己点。存的是**标签**不是序号 —— 以后调整候选顺序
        # 不会把你选好的那项悄悄换成别的。
        want_ext = self.settings.value('ext', EXT_ALL) or EXT_ALL
        ext_i = next((i for i, p in enumerate(EXT_PRESETS)
                      if p[0] == want_ext), len(EXT_PRESETS) - 1)
        self.cb_ext.setCurrentIndex(ext_i)
        self.chk_folder.setChecked(
            self.settings.value('with_folder', 'true') == 'true')
        # setChecked 在状态没变时不会发 toggled，所以这里必须显式同步一次：
        # 上次开着联想词的话，一进界面就得把别名摆出来
        if self.chk_alias.isChecked():
            self._refresh_alias_now()
        else:
            self._sync_alias_ui()

        self._hist = list(self.settings.value('history', []) or [])
        comp = self.ed_word.completer()
        if comp:
            comp.model().setStringList(self._hist)

        # 依赖是内置的还是系统装的，得让用户一眼看见（关系到"能不能拷走用"）
        self.statusBar().showMessage(
            '就绪 · 检索引擎：%s · 当前检索目标：%s'
            % (config.flp_source(), self._current_index()))

    def _save(self):
        # 多选时用换行连起来存；下次打开自动还原成"联合检索"状态
        self.settings.setValue('index', '\n'.join(self._current_targets()))
        self.settings.setValue('word', self.ed_word.text())
        self.settings.setValue(
            'use_alias', 'true' if self.chk_alias.isChecked() else 'false')
        self.settings.setValue(
            'use_variant', 'true' if self.chk_variant.isChecked() else 'false')
        self.settings.setValue('ext', EXT_PRESETS[
            max(0, min(self.cb_ext.currentIndex(),
                       len(EXT_PRESETS) - 1))][0])
        self.settings.setValue(
            'with_folder', 'true' if self.chk_folder.isChecked() else 'false')
        self.settings.setValue('history', self._hist[:50])

    def closeEvent(self, e):
        self._halt_workers()
        self._save()
        super().closeEvent(e)

    def _halt_workers(self):
        """关窗前把后台线程收干净。

        检索单次最长 300 秒，定位页码还要把整本 PDF 读一遍。窗口一关、
        线程还在跑，QThread 跟着窗口被拆 → 不是报错，是直接崩进程。
        """
        self._timer.stop()
        for name in ('_worker', '_lworker'):
            w = getattr(self, name, None)
            setattr(self, name, None)
            _give_up_thread(w)

    # ------------------------------------------------------------ 索引 / 组
    def _reload_indexes(self, select=None):
        """重建索引下拉。

        每项都带 userData：单目标是引擎认的那个名字，多选则是用换行连起来的
        一串名字（索引名里不会有换行）。显示文字可以带装饰，取名字一律走
        `_current_targets()`。
        """
        if isinstance(select, str):
            select = [select] if select else None
        entries = indexes.list_all()
        self.cb_index.blockSignals(True)
        self.cb_index.clear()
        # 组排前面：跨库检索是这里的主角
        for e in entries:
            if e['kind'] != 'group':
                continue
            # 组的成员在引擎那份 xml 里是**目录路径**，必须换成索引名再交给
            # -idxname（它只认名字，给路径会静默返回 0 条）。group_targets 干这个。
            ms = indexes.group_targets(e['name'])
            self.cb_index.addItem('[组] %s（%d 个索引）' % (e['name'], len(ms)),
                                  '\n'.join(ms) if ms else e['name'])
            # 组名另存一份在一个**专属格子**里（NAME_ROLE）。
            # 注意不能存 UserRole：那正是 addItem 第二个参数的位置，
            # 存进去会把"成员名单"覆盖掉，比对默认项时就会对不上。
            self.cb_index.setItemData(self.cb_index.count() - 1, e['name'],
                                      NAME_ROLE)
            if len(ms) > 1:
                self.cb_index.setItemData(
                    self.cb_index.count() - 1,
                    '这个组横跨 %d 个索引：\n  %s\n'
                    '（各搜一次再合并，会在「来自索引」列标明出处）'
                    % (len(ms), '\n  '.join(ms)),
                    Qt.ItemDataRole.ToolTipRole)
        for e in entries:
            if e['kind'] == 'group':
                continue
            self.cb_index.addItem(e['name'], e['name'])
            self.cb_index.setItemData(self.cb_index.count() - 1, e['name'],
                                      NAME_ROLE)
        if not self.cb_index.count():
            self.cb_index.addItem(config.DEFAULT_INDEX, config.DEFAULT_INDEX)
        want = '\n'.join(select) if select else ''
        idx = -1
        for i in range(self.cb_index.count()):
            if self.cb_index.itemData(i) == want:
                idx = i
                break
        if idx < 0 and select:
            if len(select) == 1:
                # 单个名字不在清单里（换机器了），先摆出来，引擎报错由它负责
                self.cb_index.insertItem(0, select[0], select[0])
                idx = 0
            else:
                # 多选组合：清单里本来就没有这一项，现生成一个摆在顶上
                label = '[联合] %s' % '、'.join(select)
                self.cb_index.insertItem(0, label, want)
                self.cb_index.setItemData(
                    0, '这一次性联合这些索引：\n  %s\n'
                    '（每个索引各搜一次再合并，会在「来自索引」列标明出处）'
                    % '\n  '.join(select),
                    Qt.ItemDataRole.ToolTipRole)
                idx = 0
        if idx < 0:
            # 先按"名字"（组名/索引名）找默认项，再退回"目标串"比对。
            # 顺序很重要：组的 UserData 是成员名单，跟组名对不上。
            idx = next((i for i in range(self.cb_index.count())
                        if self.cb_index.itemData(i, NAME_ROLE)
                        == config.DEFAULT_INDEX), -1)
        if idx < 0:
            idx = next((i for i in range(self.cb_index.count())
                        if self.cb_index.itemData(i) == config.DEFAULT_INDEX),
                       0)
        self.cb_index.setCurrentIndex(max(idx, 0))
        self.cb_index.blockSignals(False)

    def _current_targets(self):
        """当前检索目标 = [索引名, ...]（1 个就是单索引检索）。"""
        v = self.cb_index.currentData()
        if isinstance(v, str) and v:
            return [x for x in v.split('\n') if x]
        return [self.cb_index.currentText() or config.DEFAULT_INDEX]

    def _current_index(self):
        """给只需要一个名字的场合（状态栏、记账）用：多个就点明是联合。"""
        ts = self._current_targets()
        return ts[0] if len(ts) == 1 else '%d 个索引联合' % len(ts)

    def _manage_index(self):
        """打开索引管理：勾选 = 要联合检索的对象。"""
        entries = indexes.list_all()
        cur = self._current_targets()
        dlg = IndexDialog(entries, cur, self)
        if dlg.exec() != IndexDialog.DialogCode.Accepted:
            return
        picked = dlg.picked()
        if not picked:
            self.statusBar().showMessage('没勾任何索引，保持原来的选择')
            return
        targets = indexes.resolve_targets(picked)
        self._temp_members = list(picked)
        self._reload_indexes(targets)
        if len(targets) == 1:
            self.statusBar().showMessage('检索目标已切为「%s」' % targets[0])
        else:
            self.statusBar().showMessage(
                '联合检索：%s —— 每个索引各搜一次再合并，'
                '结果表会标明每条出自哪个索引' % '、'.join(targets))

    def _current_member_names(self):
        return list(getattr(self, '_temp_members', []) or [])

    # ------------------------------------------------------------ 联想词开关
    def _sync_alias_ui(self, got=None):
        """把联想词区的状态摆到界面上（v0.2.4 用户口径）：

        开关关 → 整个联想词区让位给单关键词检索；
        开关开 · 查到别名 → 显示输入框和个数，等你按下搜索前再改；
        开关开 · 没查到别名 → **不摆空框**，一句话说明这次就是单关键词。
        """
        on = self.chk_alias.isChecked()
        # 单关键词模式下没地方补字形（补出来的词得挂在联想词里），
        # 所以开关跟着联想词一起灰掉，免得勾了却没反应
        self.chk_variant.setEnabled(on)
        if not on:
            # 关掉就把自动展开的内容清掉，避免它悄悄继续参与检索
            self.ed_alias.clear()
            self._auto_word = ''
            self._auto_val = ''
            self.ed_alias.setVisible(False)
            self.btn_alias.setVisible(False)
            if hasattr(self, 'lb_alias'):
                self.lb_alias.setText('')
            return
        if got is None:
            got = self._alias_terms()
        n = len(got)
        # 一个别名都没有时**输入框也要留着**：用户口径是"没有联想词时允许
        # 自己输入"（CBDB 那张表再大也覆盖不到所有人）。空框＋一句提示，
        # 比把框藏起来让人以为"这个功能用不了"强。
        self.ed_alias.setVisible(True)
        # 联想词按钮永远在：一行装不下十几个别名，点开就是全清单，
        # 还能勾挑、增删、决定要不要永久生效
        self.btn_alias.setVisible(True)
        w = self.ed_word.text().strip()
        # 补出来的繁简字形要跟"真查到的别名"分开说清楚：前者是算出来的影子，
        # 不保存；挤在一起报个数，用户会纳闷"我哪来的联想词"
        nd = sum(1 for t in got if t in self._derived)
        if not n:
            self.lb_alias.setText(
                '「%s」没有联想词 —— 可以自己输入（顿号分隔），'
                '或点「联想词…」管理并让它永久生效' % w if w
                else '输入检索词后自动查联想词')
        elif not w:
            self.lb_alias.setText('')
        elif nd and nd == n:
            self.lb_alias.setText(
                '没有联想词，自动补了 %d 个繁简字形（不保存）；'
                '也可以自己输入' % nd)
        elif nd:
            self.lb_alias.setText(
                '%d 个联想词 + %d 个自动补的繁简字形（都不保存）'
                % (n - nd, nd))
        elif n >= 15:
            # 几十个别名 = 几十次从库里各搜一遍，先打招呼，别让人以为卡死
            self.lb_alias.setText(
                '%d 个联想词 —— 词有点多，检索会慢些；'
                '可在「联想词…」里勾掉不要的' % n)
        else:
            self.lb_alias.setText('%d 个联想词（可临时增删）' % n)

    def _on_alias_toggled(self, on):
        if on:
            got = self._refresh_alias_now()   # 勾上的那一刻就去查，别等按下搜索
            if got:
                self.statusBar().showMessage(
                    '联想词模式：开 —— 主词 + %d 个别名，每个词各搜一次，'
                    '结果表会标明是被哪个词命中的' % len(got))
            else:
                self.statusBar().showMessage(
                    '联想词模式：开，但这个主词在 CBDB 别名表里没有别名，'
                    '本次仍是单关键词检索')
        else:
            self._sync_alias_ui()
            self.statusBar().showMessage('联想词模式：关（只搜输入的词）')

    # ------------------------------------------------------------ 联想词刷新
    def _on_word_changed(self, _t):
        """主词一变就重新去查别名（去抖 350 ms，别每敲一个字都查一次表）。"""
        try:
            self._alias_timer.start(350)
        except Exception:
            pass

    def _refresh_alias_soon(self):
        try:
            self._alias_timer.start(120)
        except Exception:
            pass

    def _refresh_alias_now(self):
        """按当前主词查一遍别名表。

        被 `_on_alias_toggled`（勾选的瞬间）和去抖定时器（主词改了）
        调用。返回这次实际采用的别名列表（可能已被用户临时增删）。
        """
        if not self.chk_alias.isChecked():
            self._sync_alias_ui()
            return []
        w = self.ed_word.text().strip()
        return self._auto_expand(w, force=True)

    # ------------------------------------------------------------ 联想词框
    def _full_alias_words(self, word):
        """这个词**可能**用上的全部称呼（无视"不要"的记录）。

        给联想词对话框摆全清单用：这次没勾的、以前屏蔽过的、用户自己
        敲进去的，统统得摆出来 —— 摆不出来就等于"再也找不回来"。
        """
        got = alias.related(word, include_self=False,
                            variant=self.chk_variant.isChecked(), block=False)
        out, _ = self._variant_view([t for t in got if t != word], word)
        for t in self._alias_terms():
            if t != word and t not in out:
                out.append(t)
        for t in alias.blocked(word):
            if t != word and t not in out:
                out.append(t)
        return out

    def _open_alias_box(self):
        """联想词对话框：查看 / 勾挑 / 增删 / 永久生效，一处做完。"""
        w = self.ed_word.text().strip()
        if not w:
            self.statusBar().showMessage('先输入检索词，再挑它的联想词')
            return
        if not self.chk_alias.isChecked():
            self.chk_alias.setChecked(True)
            self._refresh_alias_now()
        allw = self._full_alias_words(w)
        chosen = [t for t in self._alias_terms() if t != w]
        dlg = AliasDialog(w, allw, chosen, dict(self._derived), self)
        if dlg.exec() != AliasDialog.DialogCode.Accepted:
            return
        got = [t for t in dlg.picked() if t != w]
        # 这次没勾的 = 明确不要的：写进屏蔽名单，
        # 免得下一次检索又把它端回来（用户这回报告的就是这个）
        alias.block_session(w, dlg.not_picked())
        txt = '、'.join(got)
        self.ed_alias.setText(txt)
        self._auto_word = w
        self._auto_val = txt
        self._sync_alias_ui(got)
        nd = len([t for t in got if t in self._derived])
        if dlg.keep():
            # 写得进盘的是"真联想词"；自动补的繁简字形是算出来的影子，
            # 按用户定的规矩一个都不落地写进去
            ok2, msg = alias.set_user_aliases(
                w, [t for t in got if t not in self._derived])
            ok3, msg2 = alias.set_blocked(w, dlg.not_picked())
            if ok2 and ok3:
                self.statusBar().showMessage(
                    '记住了「%s」的 %d 个联想词（另有 %d 个不再出现）'
                    % (w, len(got) - nd, len(dlg.not_picked())))
            else:
                self.statusBar().showMessage(
                    '记不住：%s' % (msg if not ok2 else msg2))
        else:
            self.statusBar().showMessage(
                '按你定的 %d 个联想词检索（只这一次，没勾永久生效）'
                % len(got))

    # ------------------------------------------------------------ 检索
    def _alias_terms(self):
        return [w.strip() for w in
                self.ed_alias.text().replace(',', '、').split('、')
                if w.strip()]

    # ------------------------------------------------- 繁简通搜（补字形）
    def _variant_view(self, terms, seed_word=None):
        """返回 (全部词, 谁是谁的字形)，**不改动任何状态**。

        seed_word 是主词：检索时主词只用框里那一个字形去搜，另一副字形
        得靠联想词带过去。

        用户定的三条规矩：
        1. **看得见**：补出来的词直接进联想词框，能删——看不见就谈不上
           "删掉原词时跟着去掉"，也谈不上"删掉就不再回来"。
        2. **不保存**：只活在内存里，不写 ini、不进 user_alias.json、
           不进"永久生效"。它是此刻算出来的影子，不是你的联想词。
        3. **连坐**：原词没了，它的影子跟着没；专门删掉影子本身也一样
           ——框里没有的就是不要的，写进屏蔽名单（`alias.block_session`），
           下一次查表不会再把它们端回来。
        """
        if not self.chk_variant.isChecked():
            return list(terms), {}
        out = list(terms)
        derived = {}
        # 连坐：原词已经在框里没了，它补出来的字形跟着走
        out = [t for t in out
               if not (t in self._derived and self._derived[t] not in out)]
        bad = set(alias.blocked(seed_word or ''))
        seed = (seed_word or '').strip()
        src = ([seed] if seed else []) + list(out)

        def claim(v, src_word):
            """记一笔"v 是 src_word 换了个字形"。

            成对的字形谁当"原词"只认**第一次**（按框里的先后顺序），
            绝不两头都记：一旦 A→B、B→A 都进了表，下一次重新算的时候
            B 就会被当成"没有原词的孤儿"给连带删掉（实测一口气泡掉一排词）。
            """
            if v in derived or v in derived.values():
                return
            derived[v] = src_word

        for w in src:
            for v in simplify.variants_of(w):
                if v == w or v == seed or v in bad:
                    continue
                if v in out:
                    # 已经在框里了（多半是查表本身就把另一个字形带回来了）：
                    # 照样记一笔"它其实是别人的字形"，界面上才标得出来
                    claim(v, w)
                    continue
                # 插在原词后面，一眼能看出这两个是一对
                out.insert((out.index(w) + 1) if w in out else 0, v)
                claim(v, w)
        return out, derived

    def _normalize_variants(self, terms, seed_word=None):
        """补字形 + 记下"谁是谁的字形"（写进 self._derived）。"""
        out, derived = self._variant_view(terms, seed_word)
        self._derived = derived
        return out

    def _on_alias_edited(self):
        """用户手改完联想词框（失焦/回车后）：以框为准，补字形、回写。

        框里没有的就此算作"不要"，写进本次的屏蔽名单 —— 否则下一次
        检索重新查表，删掉的词照旧冒出来。
        """
        if not self.chk_alias.isChecked():
            return
        w = self.ed_word.text().strip()
        terms = self._alias_terms()
        # 先按**用户此刻那一版原始内容**判断他不要了什么：补字形这一步会把
        # 刚删掉的字形又添回来，不先记下来就等于"删了白删"
        part = self._full_alias_words(w) if w else []
        gone = [t for t in part if t not in terms]
        new = self._normalize_variants(terms, seed_word=w or None)
        new = [t for t in new if t not in gone]
        if w:
            alias.block_session(w, gone)
        self._write_alias(new)

    def _on_variant_toggled(self, on):
        """繁简通搜开关：开 → 补上缺的字形；关 → 把补出来的影子撤掉。"""
        if not self.chk_alias.isChecked():
            self._sync_alias_ui()
            return
        terms = self._alias_terms()
        if on:
            new = self._normalize_variants(
                terms, seed_word=self.ed_word.text().strip() or None)
        else:
            # 关掉：之前自动补出来的影子全撤走。你亲手删过的那些记在
            # 屏蔽名单里，不该因为开关拨一下就复活
            new = [t for t in terms if t not in self._derived]
            self._derived = {}
        self._write_alias(new)
        self.statusBar().showMessage(
            '繁简通搜：%s' % ('开 —— 缺的字形会自动补上，不写进任何配置'
                              if on else '关 —— 只按字面检索'), 4000)

    def _write_alias(self, terms):
        """把联想词写回输入框；内容没变就不动，免得打断光标。"""
        txt = '、'.join(terms)
        if txt != self.ed_alias.text().strip():
            self.ed_alias.blockSignals(True)
            self.ed_alias.setText(txt)
            self.ed_alias.blockSignals(False)
        self._auto_val = txt
        self._sync_alias_ui(terms)

    def _convert_words(self, act):
        """繁简转换：检索词 + 联想词一起转（规则跟 CathayShelf 一致）。

        书本有繁简两版，检索词不跟着换就必然 0 命中——这就是要这个按钮的原因。
        转换只改输入框，跟手改联想词一样是本次有效，不写配置。
        """
        w = self.ed_word.text().strip()
        als = self._alias_terms()
        if not w and not als:
            self.statusBar().showMessage('框里还没有词，先输入检索词')
            return
        w2 = simplify.convert_text(w, act) if w else ''
        als2 = simplify.convert_many(als, act)
        changed = (w2 != w) or (als2 != als)
        # 主词换了就得记下"这次自动写入的内容"，否则下一次自动展开会
        # 拿旧的 _auto_val 判断、把用户刚转好的词又覆盖回去
        if w2:
            self.ed_word.setText(w2)
            self._auto_word = w2
        self.ed_alias.setText('、'.join(als2))
        self._auto_val = '、'.join(als2)
        if self.chk_alias.isChecked():
            self._sync_alias_ui(als2)
        tail = '' if changed else '（本来就是这个样子，没变）'
        self.statusBar().showMessage(
            '已%s：%s%s' % (simplify.DIR_NAME.get(act, act),
                            '、'.join(([w2] if w2 else []) + als2), tail))

    def _auto_expand(self, word, force=False):
        """按 CBDB 别名表查出联想词，摆到输入框里给用户当场看、当场改。

        force=True 用于"刚勾上开关"和"主词换成别的词了"——这两种情况必须
        重查；否则遵循"用户对当前主词手改过就不覆盖"的老规矩：判断依据是
        主词没变、框里内容却和上次自动写入的不一样，那就是用户动过手脚，
        刚删掉的噪声短别名（如"任公"）不能一点回车又回来。

        用户的增删只活在这一次里：不写 ini、不随下一次打开生效
        （除非他在联想词框里勾了「永久生效」——那走 `alias` 自己的两张表）。
        """
        # 换了一个主词（不是同一组字形的另一个写法）= 新的一轮，
        # 上一轮"这个词我不要"的临时记录就此翻篇，别带到别人身上
        if word != self._auto_word:
            if alias.block_key(word) != alias.block_key(self._auto_word):
                alias.clear_session(self._auto_word)
        if (not force and word == self._auto_word and self._auto_val
                and self.ed_alias.text().strip() != self._auto_val):
            got = self._alias_terms()          # 用户改过，用他的
            # 只补字形，不动 _auto_val —— 否则下次会当成"没改过"重新查表，
            # 把用户刚删的噪声别名又铺回来
            got = self._normalize_variants(got, seed_word=word)
            txt = '、'.join(got)
            if txt != self.ed_alias.text().strip():
                self.ed_alias.blockSignals(True)
                self.ed_alias.setText(txt)
                self.ed_alias.blockSignals(False)
            self._sync_alias_ui(got)
            return got
        # 查表：先把同一组繁简字形下的所有行并起来，再补缺的字形。
        # 用户明确不要的那些词由 alias 这一层挡掉，不端回来
        got = alias.related(word, include_self=False,
                            variant=self.chk_variant.isChecked()) if word else []
        got = self._normalize_variants(got, seed_word=word)
        txt = '、'.join(got)
        self.ed_alias.setText(txt)
        self._auto_word = word
        self._auto_val = txt
        self._sync_alias_ui(got)
        return got

    def _build_expr(self):
        word = self.ed_word.text().strip()
        if not word:
            return None, None, False
        if self.chk_alias.isChecked():
            also = self._auto_expand(word)
        else:
            also = []          # 单关键词模式：框里就算有残留也不参与
        expr, boolean = flp.build_expr(word, also)
        return word, expr, boolean

    def do_search(self):
        if self._worker is not None:
            return
        word, expr, boolean = self._build_expr()
        if expr is None:
            QMessageBox.information(self, APP_NAME, '请先输入检索词')
            return
        if not config.flp_exists():
            QMessageBox.critical(
                self, APP_NAME, '找不到 FileLocator Pro：\n%s'
                % config.FLP_EXE)
            return

        words = self._words()
        if not words:
            QMessageBox.information(self, APP_NAME, '请先输入检索词')
            return

        if word and word not in self._hist:
            self._hist.insert(0, word)
            comp = self.ed_word.completer()
            if comp:
                comp.model().setStringList(self._hist)

        self.tv.clear()
        self.lab_count.setText('')
        self._words_by_path = {}
        self._per_word = []
        self._target_by_path = {}
        self._set_open_enabled(False)

        self.btn_go.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.pbar.setVisible(True)
        self._t0 = time.time()
        self._timer.start()
        if len(words) > 1:
            self.statusBar().showMessage(
                '检索中（%d 个词各搜一次）：%s' % (len(words), expr))
        else:
            self.statusBar().showMessage('检索中：%s' % expr)

        # 文件夹条目**每次都取回来**，显不显示由「含文件夹名」那个勾选决定
        # —— 这样拨开关是瞬时的，不必为了看文件夹再把十几万条重搜一遍
        self._worker = SearchWorker(words, self._current_targets(), 300)
        self._worker.progress.connect(self._on_progress)
        self._worker.word_done.connect(self._on_word_done)
        self._worker.done.connect(self._on_search_done)
        self._worker.failed.connect(self._on_search_failed)
        self._worker.finished.connect(self._on_search_finished)
        self._worker.start()

    def do_stop(self):
        if self._worker is not None:
            self.statusBar().showMessage('正在停止…')
            self._worker.stop()

    def _tick(self):
        self.statusBar().showMessage(
            '检索中… 已运行 %.1f 秒' % (time.time() - self._t0))

    def _on_word_done(self, word, n):
        """多词模式：每跑完一个词就报一次，别让人干等。"""
        self.statusBar().showMessage(
            '检索中… %.1f 秒：「%s」命中 %s 个文件'
            % (time.time() - self._t0, word, format(n, ',')))

    def _on_progress(self, n):
        """边解析边报数，避免"几十秒一动不动"的假死感。"""
        if self._worker is not None:
            self.statusBar().showMessage(
                '检索中… %.1f 秒，已找到 %s 个文件'
                % (time.time() - self._t0, format(n, ',')))

    # ------------------------------------------------------------ 结果填充
    def _current_exts(self):
        return EXT_PRESETS[self.cb_ext.currentIndex()][1]

    def _filtered_hits(self):
        exts = self._current_exts()
        hits = self._hits
        if not self.chk_folder.isChecked():
            hits = [h for h in hits if not h.isfolder]
        if not exts:
            return list(hits)
        # 文件夹没有扩展名，按类型过滤时自然会被 filter_ext 排掉
        return aggregate.filter_ext(hits, exts=exts, exclude=False)

    def _refill(self):
        """按类型过滤，重建结果表。

        两列按需出现：
        「命中词」——只在多词检索时才有意义（记录这个文件被哪几个词命中），
                    单关键词时整列一个值，纯属占位，藏起来；
        「来自索引」——只在联合检索（≥2 个索引）时出现，否则每行都一样。
        """
        hits = self._filtered_hits()
        tv = self.tv
        wmap = self._words_by_path or {}
        tmap = self._target_by_path or {}
        multi = bool(wmap)
        mt = bool(tmap) and len(self._current_targets()) > 1
        tv.setUpdatesEnabled(False)
        tv.clear()
        items = []
        for h in hits:
            ws = wmap.get(h.path) or []
            ts = tmap.get(h.path) or []
            it = _Item([
                ('📁 ' + h.name) if h.isfolder else h.name,
                os.path.dirname(h.path),
                aggregate.fmt_size(h.size),
                (h.type or '').upper(),
                h.modified,
                '、'.join(ws),
                '、'.join(ts),
            ])
            it.setData(0, Qt.ItemDataRole.UserRole, h.path)
            it.setData(2, Qt.ItemDataRole.UserRole, int(h.size or 0))
            it.setData(COL_HIT, Qt.ItemDataRole.UserRole, '、'.join(ws))
            it.setToolTip(0, h.path)
            it.setToolTip(1, os.path.dirname(h.path))
            if h.isfolder:
                # 文件夹跟文件混在一张表里，得一眼能分开
                it.setToolTip(0, '文件夹：%s\n（名字里有检索词）' % h.path)
                it.setForeground(0, QColor('#0B5CAB'))
            if ws:
                it.setToolTip(COL_HIT, '命中词：%s' % '、'.join(ws))
                # 只命中联想词（主词反而没命中）的行标出来——多半是短别名误撞
                main = (self.ed_word.text() or '').strip()
                if main and main not in ws:
                    it.setForeground(COL_HIT, QColor('#B26A00'))
            if ts:
                it.setToolTip(COL_SRC, '出自：%s' % '、'.join(ts))
            items.append(it)
        tv.addTopLevelItems(items)
        try:
            tv.setColumnHidden(COL_HIT, not multi)
            if multi:
                tv.resizeColumnToContents(COL_HIT)
                if tv.columnWidth(COL_HIT) > 320:
                    tv.setColumnWidth(COL_HIT, 320)
            tv.setColumnHidden(COL_SRC, not mt)
            if mt:
                tv.resizeColumnToContents(COL_SRC)
                if tv.columnWidth(COL_SRC) > 260:
                    tv.setColumnWidth(COL_SRC, 260)
        except Exception:
            pass
        tv.setUpdatesEnabled(True)

        total_bytes = sum(h.size for h in hits)
        # 标注当前的类型过滤：状态栏报的是各词「全类型」命中数，
        # 表格这里是过滤后的行数，不对齐会让人以为少给了结果
        _fi = self.cb_ext.currentIndex()
        filt = '' if _fi >= len(EXT_PRESETS) - 1 \
            else '　（%s）' % EXT_PRESETS[_fi][0]
        self.lab_count.setText(
            '%s 个文件 · %s%s' % (format(len(hits), ','),
                                  aggregate.fmt_size(total_bytes), filt))
        self._set_open_enabled(bool(items))

    def _on_search_done(self, hits, meta):
        self._hits = hits
        self._words_by_path = meta.get('words_by_path') or {}
        self._per_word = meta.get('per_word') or []
        self._target_by_path = meta.get('target_by_path') or {}
        if meta.get('cancelled'):
            self.statusBar().showMessage('已停止')
        else:
            msg = ['耗时 %.2f 秒' % meta['elapsed']]
            if meta.get('timed_out'):
                msg.append('⚠ 超时，结果不完整')
            if len(self._per_word) > 1:
                msg.append('；'.join('%s %s' % (w, format(n, ','))
                                     for w, n in self._per_word))
            self.statusBar().showMessage(' | '.join(msg))
        self._meta = meta

        # 联合检索时逐个交代每个索引出了多少 —— 哪个是 0 必须看得见：
        # 引擎偶发的"假空"专挑单个索引下手，闷声吞掉的话用户只会觉得
        # "结果怎么少了"，根本想不到去重搜
        per_t = meta.get('per_target') or {}
        if len(per_t) > 1:
            dead = [t for t, n in per_t.items() if not n]
            line = ' | '.join('%s %s' % (t, format(n, ','))
                              for t, n in per_t.items())
            if dead and not meta.get('cancelled'):
                line = ('⚠ %d 个索引本次返回 0 条（%s）——可能是引擎偶发“假空”，'
                        '建议再搜一次确认 || %s'
                        % (len(dead), '、'.join(dead), line))
            self.statusBar().showMessage(line)

        if hits:
            self._refill()
        else:
            self.tv.clear()
            self.lab_count.setText('0 个文件')
            self._set_open_enabled(False)
            if meta.get('cancelled'):
                return
            if meta.get('retried'):
                self.statusBar().showMessage(
                    '未命中（已自动重试 %d 次，排除引擎偶发“假空”）'
                    % (meta['attempts'] - 1))
            else:
                self.statusBar().showMessage('索引里没有这个词')

    def _on_search_failed(self, err):
        QMessageBox.critical(self, APP_NAME, '检索失败：\n%s' % err)
        self.statusBar().showMessage('检索失败')

    def _on_search_finished(self):
        self._worker = None
        self.btn_go.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.pbar.setVisible(False)
        self._timer.stop()

    def _set_open_enabled(self, on):
        self.btn_open.setEnabled(on)
        self.btn_cv.setEnabled(on and bool(CV_EXE))
        self.btn_reveal.setEnabled(on)

    # ------------------------------------------------------------ 打开
    def _current_path(self):
        it = self.tv.currentItem()
        return it.data(0, Qt.ItemDataRole.UserRole) if it else ''

    def _real(self, path):
        """索引里记的老路径 → 现在的真实路径。

        书库被挪到别的盘、别的文件夹时，索引里那条老路径就打不开了。
        这里按 CathayHub Launcher 生成的 shared/path_mapping.json 换算一次。
        没有映射文件、或者这条路径压根没挪过 → 原样返回，等于什么也没做。
        """
        if not path:
            return path
        try:
            p, rule = pt.translate_path(path)
        except Exception:
            return path
        if rule:
            self.statusBar().showMessage(
                '%s：%s' % (pt.translated_label(rule), os.path.basename(p)),
                4000)
        return p

    def _words(self):
        """双击打开时要在这本书里定位的词：单关键词模式下就只有主词。"""
        words = [self.ed_word.text().strip()]
        if self.chk_alias.isChecked():
            words += self._alias_terms()
        return [w.strip() for w in words if w.strip()]

    def _current_hitwords(self):
        """当前这一行**实际被哪几个词命中**——索引早就答过这道题了。

        多词检索时结果表第 6 列「命中词」逐行标着这个文件是被哪几个词命中的
        （`_run_multi` 里按路径累加的）。以前双击时无视它，拿主词+联想词
        **全部**再去书里扫一遍——8 个词里常常只有 2 个真命中，白扫 6 遍。

        单关键词检索时该列是空的（每行都一样，没信息量，界面上直接隐藏），
        这时只能退回 `_words()`。
        """
        it = self.tv.currentItem()
        if it is not None:
            try:
                raw = it.data(COL_HIT, Qt.ItemDataRole.UserRole)
            except Exception:
                raw = None
            if raw:
                ws = [w.strip() for w in str(raw).split('、') if w.strip()]
                if ws:
                    return ws
        return self._words()

    def _on_double_click(self, *_):
        """双击：PDF 走"算页码→阅读器跳页"（本程序的核心价值）；
        TXT 走"阅读器打开 + 按检索词/联想词各搜一遍"；
        其他类型（或没装阅读器）用系统默认程序打开。

        `X_【繁转简】.txt` 双击 = 默认①：打开原版 PDF，
        而且要用**繁体**词去搜（原版是繁体书，拿简体词必然 0 命中）。
        """
        path = self._current_path()
        if not path or not os.path.isfile(path):
            self.open_selected()
            return
        low = path.lower()
        if low.endswith('.txt') and simplify.is_variant(path):
            self.open_original_pdf(path)      # 双击＝①，缺了就按 ③→② 回退
            return
        if low.endswith('.txt') and CV_EXE:
            self.open_txt(path, self._words())
            return
        if low.endswith('.pdf') and CV_EXE:
            self.open_selected_cv()
            return
        self.open_selected()

    def _variant_words(self, path):
        """繁转简 TXT 对应的"原版"用什么词搜：**转成繁体**。

        原版是繁体书，拿转换后的简体词去搜必然一处也找不到 —— 这就是
        右键三选项里①和②必须转繁、③保持原样的原因。
        """
        if simplify.variant_of(path) == simplify.S_S2T:
            return simplify.convert_many(self._words(), 't2s')   # 简转繁产物 → 原版是简体
        return simplify.convert_many(self._words(), 's2t')       # 繁转简产物 → 原版是繁体

    def open_original_pdf(self, path):
        """① 打开原版 PDF（同目录同基名的 .pdf），用繁体检索词。

        原版 PDF 不在就按用户定的回退链：繁转简 TXT → 原版 TXT。
        """
        pdf, txt = simplify.originals(path)
        words = self._variant_words(path)
        if pdf and os.path.isfile(pdf):
            self._open_pdf_with(pdf, words)
            return
        if os.path.isfile(path):                       # 回退：就打开这个繁转简 TXT
            self.statusBar().showMessage(
                '同目录里没有原版 PDF，改开繁转简 TXT')
            self.open_txt(path, self._words())
            return
        if txt and os.path.isfile(txt):                # 再回退：原版 TXT
            self.statusBar().showMessage('原版 PDF 与繁转简 TXT 都不在，改开原版 TXT')
            self.open_txt(txt, words)
            return
        self.statusBar().showMessage('原版 PDF / 原版 TXT 都不在：%s'
                                     % simplify.base_of(path))

    def open_original_txt(self, path):
        """② 打开原版 TXT（同目录同基名的 .txt），用繁体检索词。"""
        _pdf, txt = simplify.originals(path)
        if txt and os.path.isfile(txt):
            self.open_txt(txt, self._variant_words(path))
            return
        self.statusBar().showMessage(
            '同目录里没有原版 TXT：%s.txt' % simplify.base_of(path))

    def open_variant_txt(self, path):
        """③ 打开繁转简 TXT 本身，用**原样**的检索词（它就是简体，不用转）。"""
        if os.path.isfile(path):
            self.open_txt(path, self._words())
        else:
            self.statusBar().showMessage('文件不在了：%s' % path)

    def open_txt(self, path, words):
        """TXT 一律用 CathayViewer 打开，并把词交给它各搜一遍。

        词是按 --words 传进去的：阅读器自己扫一遍，查找栏给出
        「本书命中：吴佩孚 · 12 处、子玉 · 3 处」+ 合并后的逐处跳转。
        """
        if not CV_EXE:
            self.open_selected()
            return
        path = self._real(path)
        words = [w for w in (words or []) if w]
        args = self._cv_args(path, None, None, words)
        try:
            subprocess.Popen(args)
            self.statusBar().showMessage(
                '已交给 CathayViewer（TXT）：%s —— 按 %d 个词各搜一遍'
                % (os.path.basename(path), len(words)))
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, '启动阅读器失败：%s' % e)

    def _open_pdf_with(self, path, words):
        """PDF：算页码 → 带 --page 打开。words 用来算"每个词命中哪些页"。"""
        if not CV_EXE:
            self.open_selected()
            return
        path = self._real(path)
        self._pending_open = path
        self._pending_words = words
        self.pbar.setVisible(True)
        self.statusBar().showMessage(
            '正在 %s 内定位页码…' % os.path.basename(path))
        self._lworker = LocateWorker(path, words)
        self._lworker.done.connect(self._on_locate_done)
        self._lworker.failed.connect(self._on_locate_failed)
        self._lworker.finished.connect(self._on_locate_finished)
        self._lworker.start()

    def open_selected(self):
        """打开：用系统默认程序打开（PDF 不跳页，最快）。"""
        path = self._real(self._current_path())
        if not path:
            return
        # 结果里现在可能有文件夹：它没有"默认程序"，交给资源管理器打开
        if os.path.isdir(path):
            self._reveal(path)
            return
        if not os.path.isfile(path):
            self.statusBar().showMessage('文件不存在：%s' % path)
            return
        try:
            os.startfile(path)
            self.statusBar().showMessage('已打开 %s' % os.path.basename(path))
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, '打开失败：%s\n%s' % (path, e))

    def open_selected_cv(self):
        """调起 CathayViewer：立刻把书打开，页码交给阅读器自己定位。

        【v0.2.11】以前是"本程序先用 PyMuPDF 全篇扫一遍算页码，扫完才启动
        阅读器"——书没出现就得干等，大书要好几秒（页码确实是 FileLocator
        给不了的，得 PyMuPDF 现算，这个价值还在，只是不该让用户干等）。

        现在两步拆开：本程序只把**这个文件实际命中的那几个词**交过去
        （`_current_hitwords()`：结果表早就标好了，不必再查一遍），
        阅读器先开书、后台再定位页码并长出命中词导航条。

        总工作量其实没少（仍然要扫一遍），真正的收益是首屏等待从几秒降到 0，
        外加只扫真命中的那几个词而不是全部检索词。
        """
        path = self._real(self._current_path())
        if not path:
            return
        if os.path.isdir(path):
            self._reveal(path)      # 文件夹没有页码可算，定位到它即可
            return
        if not os.path.isfile(path):
            self.statusBar().showMessage('文件不存在：%s' % path)
            return
        if not CV_EXE:
            QMessageBox.information(self, APP_NAME, '没找到 CathayViewer')
            return
        self._launch_cv(path, None, None, self._current_hitwords())

    def _on_locate_done(self, pages, snips, meta):
        path = self._pending_open or ''
        self._pending_open = None
        _w = getattr(self, '_pending_words', None) or self._words()
        if meta.get('error'):
            self.statusBar().showMessage('✗ %s' % meta['error'])
            self._launch_cv(path, None, None, _w)
            return
        page = pages[0].page if pages else None
        if page is None:
            self.statusBar().showMessage(
                '%s：%d 页里都没有这些词（%.2f 秒）——索引说有，书内却搜不到，'
                '多半是扫描版 PDF 没文字层' % (os.path.basename(path),
                                              meta.get('npages', 0),
                                              meta.get('elapsed', 0)))
            self._launch_cv(path, None, None, _w)
            return
        # 记下"每个词各命中哪些页"，随 --hits 交给阅读器做命中词导航
        words = hits.group_by_word(pages)
        hp = hits.write(path, words)
        if words:
            msg = '命中词：%s' % hits.brief(words)
            if not hp:
                msg += '（清单没写成，阅读器里只能看这一页）'
            self.statusBar().showMessage(msg)
        self._launch_cv(path, page, hp, _w)

    def _on_locate_failed(self, err):
        path = self._pending_open or ''
        self._pending_open = None
        self.statusBar().showMessage('定位失败：%s' % err)
        self._launch_cv(path, None, None,
                        getattr(self, '_pending_words', None) or self._words())

    def _on_locate_finished(self):
        self._lworker = None
        self.pbar.setVisible(False)

    def _cv_args(self, path, page, hits_path=None, words=None):
        """构造调起 CathayViewer 的命令行。

        `<exe> <文件> [--page N] [--hits X] [--words 词1、词2]`
        - 非 PDF 不带页码（阅读器对文本没有页码概念）；页码非法时退回第 1 页。
        - --hits 是"每个词各命中哪些页"的清单，阅读器据此显示命中词导航条，
          用户可以在里面挑一个词、一键换下一个词，不必回检索器重搜。
        - --words 是检索词本身。【v0.2.10 修的真 bug】以前只有 TXT 带它，
          PDF 一律不带——于是 PDF 一旦书内定位不到（扫描版没文字层，
          page=None、也没有 --hits），连搜索词一起丢了，用户看到的是
          "书打开了，可查找栏空空的"。现在一律带上。
        """
        args = [CV_EXE, path]
        if os.path.splitext(path)[1].lower() == '.pdf' and page:
            try:
                n = int(page)
            except (TypeError, ValueError):
                n = 0
            if n >= 1:
                args += ['--page', str(n)]
        # page 为 None/0（书内没找到）时不带 --page：
        # 否则会静默跳到第 1 页，看起来像"跳页成功"其实没命中
        if hits_path and os.path.isfile(hits_path):
            args += ['--hits', hits_path]
        ws = [w for w in (words or []) if w]
        if ws:
            args += ['--words', '、'.join(ws)]
        return args

    def _launch_cv(self, path, page, hits_path=None, words=None):
        if not path or not os.path.isfile(path):
            return
        args = self._cv_args(path, page, hits_path, words)
        try:
            subprocess.Popen(args)
            tail = ' 第 %d 页' % page if page else ''
            if hits_path:
                tail += '（已带命中词清单，可在阅读器里换词）'
            elif words:
                tail += '（页码由阅读器后台定位）'
            self.statusBar().showMessage(
                '已交给 CathayViewer：%s%s' % (os.path.basename(path), tail))
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, '启动阅读器失败：%s' % e)

    # ------------------------------------------------------------ 右键菜单
    def _menu(self, pos):
        it = self.tv.currentItem()
        if not it:
            return
        p = it.data(0, Qt.ItemDataRole.UserRole)
        m = QMenu(self)
        a_open = QAction('打开', self)
        a_cv = QAction('用阅读器打开（跳到该页）', self)
        a_copy = QAction('复制完整路径', self)
        a_copy_name = QAction('复制文件名', self)
        a_dir = QAction('打开所在文件夹', self)
        for a in (a_open, a_cv, a_copy, a_copy_name, a_dir):
            m.addAction(a)
        a_cv.setEnabled(bool(CV_EXE) and p.lower().endswith('.pdf'))

        # 繁转简 TXT：原版 PDF / 原版 TXT / 它自己，三条路摆在最上面
        a_v1 = a_v2 = a_v3 = None
        if p and p.lower().endswith('.txt') and simplify.is_variant(p):
            base = simplify.base_of(p)
            m.insertSeparator(a_open)
            a_v1 = QAction('① 打开原版 PDF（%s.pdf，用繁体词搜）' % base, self)
            a_v2 = QAction('② 打开原版 TXT（%s.txt，用繁体词搜）' % base, self)
            a_v3 = QAction('③ 打开繁转简 TXT（用现在的词搜）', self)
            for a in (a_v1, a_v2, a_v3):
                m.insertAction(a_open, a)
            _pdf, _txt = simplify.originals(p)
            a_v1.setEnabled(bool(_pdf) and bool(CV_EXE))
            a_v2.setEnabled(bool(_txt) and bool(CV_EXE))
            a_v3.setEnabled(bool(CV_EXE))
        act = m.exec(self.tv.viewport().mapToGlobal(pos))
        if act is a_v1:
            self.open_original_pdf(p)
            return
        if act is a_v2:
            self.open_original_txt(p)
            return
        if act is a_v3:
            self.open_variant_txt(p)
            return
        if act == a_open:
            try:
                os.startfile(p)
            except Exception as e:
                QMessageBox.warning(self, APP_NAME, '打开失败：%s' % e)
        elif act == a_cv:
            self.open_selected_cv()
        elif act == a_copy:
            QApplication.clipboard().setText(p)
        elif act == a_copy_name:
            QApplication.clipboard().setText(os.path.basename(p))
        elif act == a_dir:
            self._reveal(os.path.dirname(p))

    def _reveal(self, path):
        if not path or not os.path.isdir(path):
            return
        try:
            subprocess.Popen(['explorer', os.path.normpath(path)])
        except Exception:
            pass


def packed_selftest():
    """打包产物自检：`CathaySearch.exe --selftest`

    windowed 版没有控制台，结果写到 exe 旁边的 `_selftest_打包版.txt`。
    全程 offscreen，不弹任何窗口。
    """
    lines = []
    bad = 0

    def ok(name, cond, extra=''):
        nonlocal bad
        if not cond:
            bad += 1
        lines.append('%s %s%s' % ('OK  ' if cond else 'FAIL', name,
                                  ('  → ' + extra) if extra else ''))

    ok('FileLocator 可用', config.flp_exists(), config.FLP_EXE)
    ok('索引发现', len(config.discover_indexes()) > 0,
       '%d 个' % len(config.discover_indexes()))
    ok('别名表随包', bool(alias.csv_path()), alias.csv_path() or '(缺失)')
    alias._load()
    ok('别名词条组 > 1000', alias.group_count() > 1000,
       '%d 组' % alias.group_count())
    ok('关联词展开', len(alias.related('吴佩孚')) >= 3,
       '、'.join(alias.related('吴佩孚')[:5]))
    ok('找到阅读器', bool(config.find_viewer()), config.find_viewer() or '(未找到)')

    from PyQt6.QtWidgets import QApplication
    app = QApplication(['CathaySearch'])
    try:
        w = MainWindow()
        ok('主窗口构建', True)
        ok('索引下拉', w.cb_index.count() > 0, '%d 项' % w.cb_index.count())
        w.ed_word.setText('吴佩孚')
        ok('默认用联想词模式', DEFAULT_USE_ALIAS is True)
        ok('默认繁简通搜', DEFAULT_USE_VARIANT is True)
        ok('默认搜全部类型', EXT_PRESETS[-1][0] == EXT_ALL,
           EXT_PRESETS[-1][0])
        ok('索引按钮叫「索引管理」', INDEX_BTN_TEXT == '索引管理',
           INDEX_BTN_TEXT)
        # 跨字形合并：同一组字形的两个写法必须给出同一套联想词
        ok('简体繁体联想词一致',
           set(alias.related('吴佩孚')) == set(alias.related('吳佩孚')),
           '、'.join(alias.related('吴佩孚')))
        ok('合并后没漏掉另一行的别名',
           '孚威将军' in alias.related('吴佩孚')
           and '孚威将军' in alias.related('吳佩孚'), '、'.join(
               alias.related('吳佩孚')))
        _had = set(alias.related('萧耀南'))
        alias.block_session('萧耀南', ['衡山'])
        ok('删掉（屏蔽）一个联想词就真不带',
           '衡山' not in alias.related('萧耀南')
           and '衡山' not in alias.related('蕭耀南'),
           '、'.join(alias.related('萧耀南')))
        alias.clear_session('萧耀南')
        ok('取消屏蔽后回来', set(alias.related('萧耀南')) == _had)
        w.chk_alias.setChecked(False)  # 不受 exe 旁 ini 里保存的旧设置影响
        _, expr1, boolean1 = w._build_expr()
        ok('单关键词表达式', (not boolean1) and expr1 == '吴佩孚', expr1)
        ok('未勾选时联想词区不占位置', w.ed_alias.isHidden())
        ok('单关键词定位只用主词', w._words() == ['吴佩孚'], str(w._words()))
        # v0.2.4：勾上的那一刻就该把别名查出来，不必等按下搜索
        w.chk_alias.setChecked(True)
        ok('勾选即刻查出联想词', bool(w.ed_alias.text()), w.ed_alias.text())
        ok('查到后联想词框显示出来', not w.ed_alias.isHidden())
        _, expr, boolean = w._build_expr()
        ok('联想词表达式', boolean and ' OR ' in expr, expr)
        ok('检索词 = 主词 + 联想词',
           w._words() == ['吴佩孚'] + w.ed_alias.text().split('、'),
           str(w._words()))
        # v0.2.4：多词要把"这个文件被哪几个词命中"落到结果表里
        w._words_by_path = {'Y:/a/aa.pdf': ['吴佩孚', '子玉']}
        w._hits = [flp.Hit('Y:/a/aa.pdf', 'aa.pdf', 1024, '.pdf', False, 'x')]
        w.cb_ext.setCurrentText('全部类型')
        w._refill()
        ok('多词时命中词列出现', not w.tv.isColumnHidden(COL_HIT))
        ok('命中词列内容正确',
           w.tv.topLevelItem(0).text(COL_HIT) == '吴佩孚、子玉',
           w.tv.topLevelItem(0).text(COL_HIT))
        w.ed_word.setText('一个绝无别名的人名xyzzy')
        w._refresh_alias_now()
        # v0.2.6 起反过来：没有联想词时**要留着输入框**让人自己输入
        ok('无别名时输入框仍留着', not w.ed_alias.isHidden())
        ok('无别名时有文字说明', '没有联想词' in w.lb_alias.text(),
           w.lb_alias.text())
        ok('无别名时能打开联想词框', not w.btn_alias.isHidden())
        w.chk_alias.setChecked(False)
        ok('取消勾选后清空', w.ed_alias.text() == '', w.ed_alias.text())
        # v0.2.6：繁简转换（规则跟 CathayShelf 一致）
        w.ed_word.setText('吳佩孚')
        w._convert_words('t2s')
        ok('能转成简体', w.ed_word.text() == '吴佩孚', w.ed_word.text())
        w._convert_words('s2t')
        ok('能转回繁体', w.ed_word.text() == '吳佩孚', w.ed_word.text())
        w.ed_word.setText('吴佩孚')
        ok('繁转简文件名认得出', simplify.is_variant('甲书_【繁转简】.txt')
           and not simplify.is_variant('甲书.txt'))
        ok('原版基名算得出', simplify.base_of('甲书_【繁转简】.txt') == '甲书',
           simplify.base_of('甲书_【繁转简】.txt'))
        a = w._cv_args(r'Y:\a\b.pdf', 163)
        ok('调起阅读器带页码', a[-2:] == ['--page', '163'], ' '.join(a[-2:]))
        # v0.2.3：命中词清单要能写盘、能随命令行交出去
        _jp = hits.write(r'Y:\a\b.pdf',
                         [{'word': '吴佩孚', 'pages': [12, 45], 'count': 9}])
        ok('命中词清单能写盘', bool(_jp) and os.path.isfile(_jp), _jp or '(没写成)')
        if _jp:
            ok('调起阅读器带 --hits',
               w._cv_args(r'Y:\a\b.pdf', 163, _jp)[-2:] == ['--hits', _jp],
               ' '.join(w._cv_args(r'Y:\a\b.pdf', 163, _jp)[-2:]))
        ok('结果表列数', w.tv.columnCount() == len(COLUMNS),
           '%d 列' % w.tv.columnCount())
        # ---- v0.2.5：索引管理 + 联合检索 + 联想词查看
        ok('引擎自带（能整个目录拷走）',
           config.FLP_EXE == config.BUNDLED_FLP_EXE, config.flp_source())
        _ents = indexes.list_all()
        ok('索引下拉每项都挂了引擎名',
           all(w.cb_index.itemData(i) for i in range(w.cb_index.count())),
           '%d 项' % w.cb_index.count())
        _names = [e['name'] for e in _ents]
        ok('当前目标是登记在册的条目',
           all(t in _names for t in w._current_targets()),
           str(w._current_targets()))
        _mk = '_CStest_打包自检_%d' % int(time.time())
        _ok1, _m1 = indexes.add_index(_mk, os.path.dirname(sys.executable))
        ok('能登记一个新索引', _ok1, _m1)
        ok('登记后清单里有了', _mk in [e['name'] for e in indexes.list_all()])
        _ok2, _m2 = indexes.remove(_mk)
        ok('能删掉自己登记的', _ok2, _m2)
        _foreign = next((e['name'] for e in _ents if not e['ours']), '')
        ok('别人手工建的索引不给删',
           bool(_foreign) and not indexes.remove(_foreign)[0], _foreign)
        _g = next((e['name'] for e in _ents if e['kind'] == 'group'), '')
        if _g:
            ok('索引组的成员读得出来', len(indexes.group_members(_g)) >= 1,
               '%s → %d 个成员' % (_g, len(indexes.group_members(_g))))
        # 自己攒的组：建、摊平、删，全套能跑
        _gn = '_CStest_打包自检组'
        _okg, _mg = indexes.add_group(_gn, _names[:2])
        ok('能攒自己的索引组', _okg, _mg)
        ok('自己的组不写 FileLocator 配置', indexes._entry_path(_gn) is None)
        ok('自己的组会摊平成成员分别检索',
           indexes.resolve_targets([_gn]) == _names[:2],
           str(indexes.resolve_targets([_gn])))
        indexes.remove(_gn)
        ok('自己的组删干净', _gn not in indexes.our_groups())
        # 联想词：一个对话框管完（查看 / 勾挑 / 增删 / 永久生效）
        w.chk_alias.setChecked(True)
        w.ed_word.setText('梁启超')
        w._refresh_alias_now()
        _own = lambda: [t for t in w._alias_terms() if t not in w._derived]  # noqa: E731
        ok('联想词按钮在界面上', not w.btn_alias.isHidden())
        ok('繁简两行并起来没漏', len(_own()) > 8, str(len(_own())))
        _full = w._full_alias_words('梁启超')
        ad = AliasDialog('梁启超', _full, set(w._alias_terms()),
                         dict(w._derived), None)
        ok('摆的是完整清单', ad.lst.count() == len(set(_full)),
           '%d 行 / %d 个' % (ad.lst.count(), len(set(_full))))
        ad._set_all(True)
        ok('可以勾到全部', len(ad.picked()) == len(set(_full)),
           str(len(ad.picked())))
        ok('自动补的字形认得出来',
           bool([x for x in ad.picked() if x in ad.derived]))
        ad._set_all(False)
        ok('全不选时 not_picked 就是全部',
           set(ad.not_picked()) == set(_full), str(len(ad.not_picked())))
        ad._set_all(True)
        w.ed_word.setText('梁启超')
        w._refresh_alias_now()
        _before = w._alias_terms()
        # 手改联想词框 = 这次定下来了，下一次查表不许再冒出来
        w.ed_alias.setText('、'.join(x for x in _before if x != '任公'))
        w._on_alias_edited()
        ok('随手删掉的词不再回来',
           '任公' not in w._alias_terms(), str(w._alias_terms()))
        # 换同一个人的另一种写法：屏蔽跟着走（简繁共用一个记账 key）
        w.ed_word.setText('梁啟超')
        w._refresh_alias_now()
        ok('换个字形也照样尊重这次的删除',
           '任公' not in w._alias_terms(), str(w._alias_terms()))
        # 换成另一个人：新一轮，不再继承上一次的临时增删
        w.ed_word.setText('袁世凯')
        w._refresh_alias_now()
        w.ed_word.setText('梁启超')
        w._refresh_alias_now()
        ok('换了检索词就是新一轮', '任公' in w._alias_terms(),
           str(w._alias_terms()))
        ok('（没勾永久生效时）配置里不留联想词内容',
           not any(k.lower() in ('alias', 'aliases')
                   for k in w.settings.allKeys()),
           str(sorted(w.settings.allKeys())))
    except Exception as e:
        ok('主窗口构建', False, '%s: %s' % (type(e).__name__, e))

    import fitz
    ok('PyMuPDF 就位', bool(fitz.__doc__), (fitz.__doc__ or '').splitlines()[0])

    lines.append('')
    lines.append('通过 %d，失败 %d' % (len(lines) - 1 - bad, bad))
    txt = '\n'.join(lines) + '\n'
    try:
        out = os.path.join(os.path.dirname(sys.executable),
                           '_selftest_打包版.txt')
        if not os.path.isdir(os.path.dirname(out)):
            out = '_selftest_打包版.txt'
        open(out, 'w', encoding='utf-8').write(txt)
    except Exception:
        pass
    try:
        sys.stdout.write(txt)
        sys.stdout.flush()
    except Exception:
        pass
    return 1 if bad else 0


# ------------------------------------------------------------------ 统一主题
# 【v0.2.9 整合】CathayHub Search 与 CathayHub Viewer 用**同一份**外观：
# 同目录下 runtime/hub_theme.qss 优先（一份文件改两处），没有就用这份内置的。
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
        p = os.path.join(config.app_dir(), 'runtime', 'hub_theme.qss')
        if os.path.isfile(p):
            with open(p, encoding='utf-8') as f:
                qss = f.read()
    except Exception:
        pass
    try:
        app.setStyleSheet(qss)
    except Exception:
        pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if '--selftest' in argv:
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'   # 必须在 QApplication 之前
        return packed_selftest()
    app = QApplication(argv)
    app.setStyle(QStyleFactory.create('Fusion'))
    apply_hub_theme(app)
    w = MainWindow()
    if '--word' in argv:
        i = argv.index('--word')
        if i + 1 < len(argv):
            w.ed_word.setText(argv[i + 1])
    w.show()
    # 【v0.2.11】Viewer 的「🌐 全库全文检索」带 --autosearch 过来：
    # 光把词填进输入框、用户还得手点一次检索，等于没唤起 —— 这里直接开搜。
    if '--autosearch' in argv and w.ed_word.text().strip():
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(250, w.do_search)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
