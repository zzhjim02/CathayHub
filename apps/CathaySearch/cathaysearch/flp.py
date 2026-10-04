# -*- coding: utf-8 -*-
"""FileLocator Pro 外壳调用层。

铁律（M1 实测结论，别改）：
  1. 必须用 FileLocatorPro.exe（与安装版本一致），flpsearch.exe 一律返回码 2。
  2. 必须带 -o，保证静默不弹界面。
  3. 必须带 -ocn（关闭抽正文）。抽正文约 0.9 s/文件，上万个文件会跑几分钟；
     关掉后只拿文件清单，2.9~6.7 秒返回。
  4. -f 文件名 / -d 目录在 -idxname 模式下会被忽略，只能全库检索。
  5. 只给文本行号，给不了 PDF 页码 —— 页码交给 locate.py 用 PyMuPDF 算。

本模块只负责：全库告诉我"哪些文件里有这个词"。
"""
import os
import re
import time
import tempfile
import subprocess
import xml.etree.ElementTree as ET
from collections import namedtuple

from . import config

NS = '{http://www.mythicsoft.com/FileLocator_16Aug2005}'

Hit = namedtuple('Hit', 'path name size type isfolder modified')


class FlpError(RuntimeError):
    pass


def _kill(proc):
    """杀掉子进程，杀不掉就退而求其次 terminate。"""
    try:
        proc.kill()
        return
    except Exception:
        pass
    try:
        proc.terminate()
    except Exception:
        pass


# ============================================================ 索引更新
def flpidx_alive():
    """当前这套引擎的 flpidx.exe 能不能用？

    【v0.2.12】9.0.3307 那份 Mod 版的 CLI 是**死的**：实测 `-list` 直接
    返回码 2、一个索引都列不出来（破解只 patch 了 GUI，CLI 没管）。
    换到 9.3.3536 之后才活。所以更新索引之前先探一下，别让用户对着
    一个永远"已完成"其实什么都没干的按钮发呆。

    返回 (能用吗, 不能用的原因)。
    """
    exe = config.FLPIDX_EXE
    if not os.path.isfile(exe):
        return False, '找不到命令行工具：%s' % exe
    try:
        p = subprocess.run([exe, '-list'], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=60)
    except Exception as e:
        return False, '跑不起来（%s）' % e
    if p.returncode == 2:
        return False, (
            '当前引擎的命令行工具 flpidx.exe 一启动就退出（返回码 2），'
            '做不了索引更新。\n\n'
            '多半是引擎版本太旧 —— 9.0.3307 那份 Mod 版只破解了界面，'
            '命令行那几个没被破解。换成 9.3 及以上就好了。')
    return True, ''


def update_index(name, on_line=None, cancel=None, timeout=21600):
    """把某个索引更新到最新（增量：只处理新增、改动、删掉的文件）。

    【v0.2.12 新增】以前界面里没有"更新索引"这条路，正是被上面那个死的
    CLI 堵死的。现在 9.3 的 flpidx 能跑了，才有这个函数。

    三条规矩：
      1. **只认返回码，不解析 stdout**：实测 flpidx 的输出一碰到中文索引名
         就断（切英文语言照样断），拿 stdout 当结果会误判成失败。
      2. 更新期间别同时检索（索引正被写），调用方负责挡一下。
      3. 索引的源目录要是不在了，更新会把里面记的文件全判成"已删除"、
         写回索引 —— 等于把索引清空。所以调用方必须先查源目录。

    返回 dict：ok / rc / elapsed / timed_out / cancelled / tail
    """
    exe = config.FLPIDX_EXE
    args = [exe, '-name', name, '-update', '-verbose']
    t0 = time.time()
    lines = []

    def _pump(proc):
        """单独一根线程把输出往外掏，主线程才能边跑边显示、还能取消。"""
        try:
            for raw in iter(proc.stdout.readline, b''):
                s = raw.decode('utf-8', 'replace').rstrip()
                lines.append(s)
                if on_line:
                    on_line(s)
        except Exception:
            pass
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass

    proc = subprocess.Popen(args, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    import threading
    th = threading.Thread(target=_pump, args=(proc,), daemon=True)
    th.start()

    timed_out = False
    cancelled = False
    deadline = t0 + timeout
    while proc.poll() is None:
        if cancel is not None and cancel():
            _kill(proc)
            cancelled = True
            break
        if time.time() > deadline:
            _kill(proc)
            timed_out = True
            break
        time.sleep(0.2)
    try:
        proc.wait(timeout=15)
    except Exception:
        _kill(proc)
    try:
        th.join(timeout=5)
    except Exception:
        pass
    rc = proc.returncode
    return {
        'ok': (rc == 0 and not timed_out and not cancelled),
        'rc': rc,
        'elapsed': time.time() - t0,
        'timed_out': timed_out,
        'cancelled': cancelled,
        'tail': lines[-12:],
    }


def build_expr(main, extra=()):
    """把主词与关联词拼成布尔表达式。
    只有主词时返回纯文本；有关联词时用 OR 连接，需要 -ceb。
    """
    terms = [t.strip() for t in [main] + list(extra) if t and t.strip()]
    seen, uniq = set(), []
    for t in terms:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    if not uniq:
        raise ValueError('检索词为空')
    if len(uniq) == 1:
        return uniq[0], False
    return ' OR '.join(uniq), True


def run_search(expr, index=None, boolean=False, timeout=180,
               out_path=None, on_line=None, retries=2, cancel=None,
               include_folders=False):
    """执行一次全库索引检索，返回 (hits, meta)。

    hits: [Hit]。include_folders=False（老行为）时只含文件；为 True 时把
    **文件夹名命中**的条目也收进来（这类条目 Hit.isfolder=True）。

    【v0.2.9】FileLocator 的索引是连文件夹名一起建的：实测拿「中国」搜
    学术信息索引，引擎报 191,143 项，其中 3,392 项是文件夹，而且看名字
    都是**文件夹名自己命中**（不是命中文件的父目录）。这些原先被
    parse_results 里一句 `if not isfolder` 全丢了 —— 用户纳闷"文件夹名里
    明明有关键词，为什么搜不出来"。
    meta: dict(elapsed, xml_path, returncode, timed_out, expr, index,
               duration_ms, attempts, retried)

    retries: 命中为空时的重试次数。FileLocator 会偶发"假空"——自称
    已完成、耗时 1.3 秒却返回 0 条，而同样表达式重跑能出 4 万条。
    真零结果重试仍是 0，所以默认重试 2 次，代价只是多几秒。

    cancel: 可选 callable，轮询调用；返回 True 时立刻杀掉子进程
    （GUI 的"停止"按钮用这个。不传则与原来完全等价）。
    """
    index = index or config.DEFAULT_INDEX
    if not config.flp_exists():
        raise FlpError('找不到 FileLocator Pro：%s' % config.FLP_EXE)

    last = None
    for attempt in range(retries + 1):
        if cancel is not None and cancel():
            break
        hits, meta = _run_once(expr, index, boolean, timeout, out_path,
                               on_line, cancel=cancel,
                               include_folders=include_folders)
        meta.setdefault('cancelled', False)
        meta['attempts'] = attempt + 1
        meta['retried'] = attempt > 0
        last = (hits, meta)
        if meta['cancelled']:
            break
        if not _looks_fake_empty(hits, meta):
            break
    if last is None:      # 一上来就被取消
        return [], {'elapsed': 0.0, 'xml_path': '', 'returncode': None,
                    'timed_out': False, 'cancelled': True, 'expr': expr,
                    'index': index, 'found_text': '', 'status': '',
                    'duration_ms': None, 'attempts': 0, 'retried': False}
    return last


def _temp_out_path():
    """给本次检索单独一个输出文件。

    【v0.2.4 血的教训】以前用 `flp_<毫秒时间戳>.xml`：单线程没事，
    多词并发（4 路）时四个进程几乎同时启动，**撞成同一个文件名**，
    两个进程往同一份 XML 里写 → 互相截断 → 解析出 0 条，而外层
    `_looks_fake_empty` 还把这当成"假空"去重跑一遍，白白多等几秒。
    `mkstemp` 由系统保证唯一，彻底断掉这个隐患。
    """
    tmpdir = os.path.join(tempfile.gettempdir(), 'CathaySearch')
    os.makedirs(tmpdir, exist_ok=True)
    fd, p = tempfile.mkstemp(prefix='flp_', suffix='.xml', dir=tmpdir)
    try:
        os.close(fd)
    except OSError:
        pass
    return p


def _run_once(expr, index, boolean, timeout, out_path, on_line, cancel=None,
              include_folders=False):
    index = index or config.DEFAULT_INDEX
    if out_path is None:
        # 放系统临时目录：打包后程序目录可能是只读的（Program Files）
        out_path = _temp_out_path()
    else:
        os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)

    args = [config.FLP_EXE, '-idxname', index, '-c', expr]
    if boolean:
        args.append('-ceb')
    args += ['-ocn', '-ofx', '-oe8', '-o', out_path]

    t0 = time.time()
    timed_out = False
    cancelled = False
    # 用 Popen 轮询而非 subprocess.run：这样 GUI 的"停止"能真正杀掉子进程。
    # cancel 为 None 时行为与 subprocess.run 等价（超时精度 0.15 秒）。
    proc = subprocess.Popen(args, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    deadline = t0 + timeout
    while proc.poll() is None:
        if cancel is not None and cancel():
            _kill(proc)
            cancelled = True
            break
        if time.time() > deadline:
            _kill(proc)
            timed_out = True
            break
        time.sleep(0.15)
    try:
        proc.wait(timeout=10)
    except Exception:
        _kill(proc)
    rc = proc.returncode
    elapsed = time.time() - t0

    hits, stat = [], {}
    if os.path.isfile(out_path):
        hits, stat = parse_results(out_path, on_line=on_line,
                                   include_folders=include_folders)

    try:
        duration_ms = int(stat.get('duration', '0') or 0)
    except ValueError:
        duration_ms = None

    meta = {
        'elapsed': elapsed, 'xml_path': out_path, 'returncode': rc,
        'timed_out': timed_out, 'cancelled': cancelled, 'expr': expr,
        'index': index,
        'found_text': stat.get('found', ''), 'status': stat.get('status', ''),
        'duration_ms': duration_ms,
    }
    return hits, meta


def parse_results(xml_path, on_line=None, include_folders=False):
    """解析 FileLocator 的 XML 结果。

    流式解析，被中断导致的残缺 XML 也能容错。默认只取文件条目（老行为）；
    include_folders=True 时把文件夹条目也保留下来，Hit.isfolder=True、
    type 记成「文件夹」——详见 run_search 的说明。
    返回 (hits, stat)
    """
    hits = []
    stat = {}
    cur = {}
    try:
        for event, el in ET.iterparse(xml_path, events=('end',)):
            tag = el.tag
            if tag in (NS + 'found', NS + 'status', NS + 'endtime',
                       NS + 'duration'):
                # 注意：这几个节点的 value 属性不带命名空间前缀（与
                # rslt:isfolder / rslt:totalhitcount 不同）
                v = el.get('value')
                if v is None:
                    v = el.get(NS + 'value')
                stat[tag[len(NS):]] = (v or '').strip()
                continue
            if tag == NS + 'file':
                isfolder = (el.get(NS + 'isfolder') or '').lower() == 'true'
                name = cur.get('name', '')
                path = cur.get('path', '')
                if name and (not isfolder or include_folders):
                    # 引擎给的 path 结尾带分隔符，万一不带（老版本/改过）
                    # 就补一个，免得拼出 "D:\某目录文件名" 这种残废路径
                    sep = '' if (not path
                                 or path.endswith(('\\', '/'))) else '\\'
                    full = path + sep + name if path else name
                    h = Hit(
                        path=full, name=name,
                        size=int(cur.get('sizebytes', '0') or 0),
                        type=('文件夹' if isfolder
                              else (cur.get('type', '') or '')),
                        isfolder=isfolder,
                        modified=cur.get('modified', ''),
                    )
                    hits.append(h)
                    if on_line:
                        on_line(h)
                cur = {}
                el.clear()
            elif tag.startswith(NS):
                key = tag[len(NS):]
                if key in ('name', 'path', 'type', 'modified', 'sizebytes'):
                    cur[key] = (el.text or '').strip()
    except ET.ParseError:
        # 残缺 XML（进程被中断）时保留已解析部分
        pass

    # 文本格式输出时的统计块（XML 走上面的 statistics 节点）
    try:
        s = open(xml_path, encoding='utf-8', errors='replace').read(200000)
        for key, pat in (('found', r'找到:\s*([^\n]*)'),
                         ('status', r'状态:\s*([^\n]*)')):
            if key not in stat:
                m = re.search(pat, s)
                if m:
                    stat[key] = m.group(1).strip()
    except Exception:
        pass
    return hits, stat


def _looks_fake_empty(hits, meta):
    """判断是否"假空"：引擎自称已完成、返回码 0，但一个结果都没有。

    M1 实测确实出现过：同一条 9 词布尔表达式，一次跑 71 秒命中 48979，
    另一次 1.3 秒返回 0 且 status="已完成"。真零结果（词确实不存在）
    重试也还是 0，所以无脑重试是安全的，代价只是多几秒。

    【v0.2.1 修正】以前用"耗时 >5 秒就判为真零"来省时间，这是错的——
    真零结果同样 2.4 秒就返回（实测 '吴佩孚' 假空 2.4 秒 / 0 条，
    真生僻词也是 2.4 秒 / 0 条），耗时区分不了真假，只会漏掉真结果。
    现在 0 命中一律重试：漏掉一万条远比多等 5 秒严重。
    """
    if hits or meta.get('timed_out') or meta.get('cancelled'):
        return False
    if meta.get('returncode') not in (0, None):
        return False
    return True
