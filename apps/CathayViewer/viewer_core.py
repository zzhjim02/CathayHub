# -*- coding: utf-8 -*-
"""CathayViewer · 学术书库浏览与阅读 —— 文件名索引引擎  v0.1.0

原则（代码里就这么干）：
  * 对源书库**只读**：只 scandir/stat，一个字节都不写、不建临时文件、不改名；
  * 只写**自己的**索引库（位置由用户指定；先写临时文件 → 校验 → 原子替换，绝不留半截）；
  * 认盘：本地/移动盘用卷序列号，网络盘用「UNC 根路径」，盘符变了也能认回来；拔盘不崩；
  * 不写注册表、不装服务、不联网。

索引里只有：文件名 / 路径 / 大小 / 修改时间（+ 认盘信息）。**不存正文**。
"""
import ctypes
import io
import json
import os
import sqlite3
import sys
import time
from ctypes import wintypes

APP_TITLE = 'CathayHub Viewer · 学术书库阅读工具'
APP_VERSION = 'v0.3.22'
DEFAULT_ROOTS = [r'X:\我的书库', r'X:\我的书库-扩展']
SKIP_DIRS = {'$recycle.bin', 'system volume information', '__pycache__', '.git',
             '.svn', 'node_modules', '.idea', '$windows.~ws'}
EXTS = ('.pdf', '.txt', '.md', '.epub', '.mobi', '.azw3', '.djvu', '.uvz', '.zip', '.rar',
        '.7z', '.pdg', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.csv', '.json',
        '.html', '.htm', '.xml', '.rtf', '.wps', '.ofd', '.caj')
BATCH = 4000

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
  id     INTEGER PRIMARY KEY,
  root   TEXT, dir TEXT, name TEXT, stem TEXT, ext TEXT,
  size   INTEGER, mtime REAL,
  vol_label TEXT, vol_serial INTEGER, drive_kind INTEGER,
  UNIQUE(dir, name));
CREATE VIRTUAL TABLE IF NOT EXISTS files_fts
  USING fts5(name, stem, content='', contentless_delete=1);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS idx_dir  ON files(dir);
CREATE INDEX IF NOT EXISTS idx_root ON files(root);
CREATE INDEX IF NOT EXISTS idx_vol  ON files(vol_serial);
"""


class Cancelled(Exception):
    pass


def app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def settings_path():
    return os.path.join(app_dir(), 'cathayviewer_settings.json')


def find_search_exe():
    """找同目录下的 CathayHub Search 主程序（「全库全文检索」要唤起它）。

    CathayHub 之后两个主程序并列放在同一个文件夹，所以先在本目录找；
    找不到就再到创作库里找 CathaySearch 的发布目录。找不到返回空串。
    """
    here = app_dir()
    try:
        for fn in sorted(os.listdir(here)):
            if fn.startswith('CathayHub Search') and fn.lower().endswith('.exe'):
                return os.path.join(here, fn)
    except OSError:
        pass
    for base in (r'D:\CathayHub-DEV\apps',):
        if not os.path.isdir(base):
            continue
        try:
            for dn in sorted(os.listdir(base)):
                if not dn.startswith('CathaySearch'):
                    continue
                d = os.path.join(base, dn)
                if not os.path.isdir(d):
                    continue
                for fn in sorted(os.listdir(d)):
                    if fn.lower().endswith('.exe'):
                        return os.path.join(d, fn)
        except OSError:
            continue
    return ''


def shared_runtime(*parts):
    """与 CathayHub Search 共用的数据目录（程序目录/runtime）。

    两个主程序在同一个文件夹里，`runtime/` 天然就是同一份 —— 联想词、
    索引账本这些"两边都该看见的东西"都放这儿，一边改另一边立刻生效。
    """
    d = os.path.join(app_dir(), 'runtime')
    if parts:
        d = os.path.join(d, *parts)
    return d


def default_settings():
    return {'primary_db': os.path.join(app_dir(), 'cathayviewer_index.db'),
            'backup_db': '', 'roots': list(DEFAULT_ROOTS), 'only_exts': True,
            'last_built': '', 'last_count': 0}


def load_settings():
    p = settings_path()
    s = default_settings()
    try:
        if os.path.isfile(p):
            s.update(json.load(open(p, encoding='utf-8')) or {})
    except Exception:
        pass
    return s


def save_settings(s):
    try:
        json.dump(s, open(settings_path(), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


# ------------------------------------------------------- 认 CathayHub 建好的库
def shared_dir():
    """四个软件共用的目录（本程序旁边的 shared/）。

    CathayHub 统一入口（Launcher）在首次运行向导里建好文件名索引后，
    会把"库在哪、扫了哪些目录"记在这儿的 launcher.json。
    """
    return os.path.join(app_dir(), 'shared')


def hub_launcher_json():
    return os.path.join(shared_dir(), 'launcher.json')


def _hub_launcher():
    try:
        with open(hub_launcher_json(), 'r', encoding='utf-8') as f:
            return json.load(f) or {}
    except Exception:
        return {}


def hub_filename_db():
    """统一入口已经建好的文件名索引在哪（没有就空串）。"""
    p = str(_hub_launcher().get('filename_db') or '').strip()
    return p if p and os.path.isfile(p) else ''


def hub_filename_roots():
    return [r for r in (_hub_launcher().get('filename_roots') or [])
            if isinstance(r, str) and r]


def adopt_hub_db(st):
    """统一入口已经建好库的话，直接认过来 —— 别再弹"建立文件名索引"问用户。

    只有本机那份库确实不存在时才认（用户自己建过的优先）。
    返回 (settings, 认过来没有)。
    """
    if os.path.isfile((st or {}).get('primary_db') or ''):
        return st, False
    hub = hub_filename_db()
    if not hub:
        return st, False
    st = dict(st or {})
    st['primary_db'] = hub
    roots = hub_filename_roots()
    # 用户自己改过"扫哪些目录"就别动他的；还是出厂那两个才换成 Launcher 扫出来的
    if roots and (not st.get('roots')
                  or list(st.get('roots')) == list(DEFAULT_ROOTS)):
        st['roots'] = roots
    try:
        save_settings(st)
    except Exception:
        pass
    return st, True


# ---------------------------------------------------------------- 认盘（免权限）
def drive_kind(path):
    """2=可移动 3=本地 4=网络 5=光驱 6=内存盘；0=未知（含 UNC \\\\server\\share）"""
    try:
        root = os.path.splitdrive(os.path.abspath(path))[0]
        if not root:
            return 4 if os.path.abspath(path).startswith('\\\\') else 0
        return int(ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root + '\\')))
    except Exception:
        return 0


def volume_info(path):
    """返回 (卷标, 序列号)。网络盘拿不到 → ('', 0)，此时靠根路径认。"""
    try:
        root = os.path.splitdrive(os.path.abspath(path))[0] + '\\'
        if drive_kind(path) == 4:
            return ('', 0)
        vol = ctypes.create_unicode_buffer(261)
        fs = ctypes.create_unicode_buffer(261)
        serial = wintypes.DWORD()
        mcl = wintypes.DWORD()
        flags = wintypes.DWORD()
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root), vol, 261, ctypes.byref(serial),
            ctypes.byref(mcl), ctypes.byref(flags), fs, 261)
        return (vol.value, int(serial.value)) if ok else ('', 0)
    except Exception:
        return ('', 0)


def volume_key(path):
    """认盘用的稳定标识：本地/移动→ 'SN:<序列号>'；网络→ 'UNC:<根路径>'。"""
    k = drive_kind(path)
    if k == 4 or str(path).startswith('\\\\'):
        return 'UNC:' + os.path.splitdrive(os.path.abspath(path))[0].upper()
    lab, sn = volume_info(path)
    return 'SN:%d' % sn if sn else 'ROOT:' + os.path.splitdrive(os.path.abspath(path))[0].upper()


def charspace(s):
    """逐字加空格（中文按单字进 FTS，和家里其他库一致）。"""
    return ' '.join(ch for ch in (s or '') if not ch.isspace())


# ---------------------------------------------------------------- 库操作
def connect(db, readonly=False):
    if readonly:
        uri = 'file:%s?mode=ro' % os.path.abspath(db).replace('\\', '/').replace('#', '%23')
        c = sqlite3.connect(uri, uri=True)
    else:
        c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    return c


def iter_files(roots, exts=EXTS, cancel=None, on_progress=None, every=2000):
    """只读遍历：产出 (root, dir, name, size, mtime)，不碰源文件。"""
    n = 0
    for root in roots:
        if not os.path.isdir(root):
            if on_progress:
                on_progress({'stage': 'skip', 'root': root, 'reason': '目录不存在', 'n': n})
            continue
        for dp, dns, fns in os.walk(root, onerror=lambda e: None):
            dns[:] = [d for d in dns
                      if d.lower() not in SKIP_DIRS and not d.startswith('.')]
            for fn in fns:
                if cancel and cancel():
                    raise Cancelled()
                if exts:
                    if os.path.splitext(fn)[1].lower() not in exts:
                        continue
                p = os.path.join(dp, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                n += 1
                if on_progress and n % every == 0:
                    on_progress({'stage': 'scan', 'n': n, 'dir': dp})
                yield (root, dp, fn, int(st.st_size), float(st.st_mtime))


def build(roots, db, exts=EXTS, cancel=None, on_progress=None, copy_to=''):
    """全量建库：写临时文件 → 校验 → 原子替换。返回统计 dict。"""
    t0 = time.time()
    tmp = db + '.building'
    for f in (tmp, tmp + '-journal', tmp + '-wal', tmp + '-shm'):
        try:
            if os.path.exists(f):
                os.remove(f)
        except OSError:
            pass
    parent = os.path.dirname(os.path.abspath(db))
    if parent:
        os.makedirs(parent, exist_ok=True)
    c = connect(tmp)
    try:
        c.executescript(SCHEMA)
        c.execute('PRAGMA journal_mode=TRUNCATE')
        rows = []
        n = 0
        kind_cache = {}
        for root, dp, fn, size, mt in iter_files(roots, exts, cancel, on_progress):
            if root not in kind_cache:
                kind_cache[root] = (volume_info(root)[0], volume_info(root)[1], drive_kind(root))
            lab, sn, kind = kind_cache[root]
            stem, ext = os.path.splitext(fn)
            rows.append((root, dp, fn, stem, ext.lower(), size, mt, lab, sn, kind))
            if len(rows) >= BATCH:
                _flush(c, rows)
                n += len(rows)
                rows = []
        if rows:
            _flush(c, rows)
            n += len(rows)
        c.execute('INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)',
                  ('roots', json.dumps(list(roots), ensure_ascii=False)))
        c.execute('INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)', ('built_at', _now()))
        c.execute('INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)', ('version', APP_VERSION))
        c.execute('INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)', ('count', str(n)))
        c.commit()
        got = c.execute('SELECT COUNT(*) FROM files').fetchone()[0]
        fts = c.execute('SELECT COUNT(*) FROM files_fts').fetchone()[0]
    finally:
        c.close()
    if got != n or fts != n:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise RuntimeError('建库校验失败：files=%d fts=%d 期望=%d' % (got, fts, n))
    for suf in ('-journal', '-wal', '-shm'):
        try:
            if os.path.exists(tmp + suf):
                os.remove(tmp + suf)
        except OSError:
            pass
    os.replace(tmp, db)                      # 原子落位，绝不留半截
    if copy_to:                              # 备查位置：副本（主库仍是唯一写入目标）
        try:
            d = os.path.dirname(os.path.abspath(copy_to))
            if d:
                os.makedirs(d, exist_ok=True)
            import shutil
            shutil.copy2(db, copy_to)
        except Exception:
            pass
    return {'files': n, 'secs': round(time.time() - t0, 2), 'db': db,
            'size': os.path.getsize(db) if os.path.exists(db) else 0}


def _flush(c, rows):
    c.executemany(
        'INSERT OR REPLACE INTO files(root,dir,name,stem,ext,size,mtime,'
        'vol_label,vol_serial,drive_kind) VALUES(?,?,?,?,?,?,?,?,?,?)',
        [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9]) for r in rows])
    ids = [x[0] for x in c.execute(
        'SELECT id FROM files WHERE dir=? AND name=?',
        (rows[-1][1], rows[-1][2])).fetchall()] if False else []
    for r in rows:                            # 逐条拿 rowid 写 FTS（同库同事务，快）
        rid = c.execute('SELECT id FROM files WHERE dir=? AND name=?', (r[1], r[2])).fetchone()
        if rid:
            c.execute('DELETE FROM files_fts WHERE rowid=?', (rid[0],))
            c.execute('INSERT INTO files_fts(rowid,name,stem) VALUES(?,?,?)',
                      (rid[0], charspace(r[2]), charspace(r[3])))


def _now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def refresh(db, roots=None, exts=EXTS, cancel=None, on_progress=None, copy_to=''):
    """增量刷新：按 大小+mtime 比对，只增/改/删变化项。返回统计。"""
    t0 = time.time()
    if not os.path.isfile(db):
        return build(roots or DEFAULT_ROOTS, db, exts, cancel, on_progress, copy_to)
    c = connect(db)
    try:
        old = {}
        for r in c.execute('SELECT id,dir,name,size,mtime FROM files'):
            old[(r['dir'], r['name'])] = (r['id'], r['size'], r['mtime'])
        roots = roots or json.loads(
            (c.execute("SELECT v FROM meta WHERE k='roots'").fetchone() or ['[]'])[0])
        add = upd = 0
        seen = set()
        buf = []
        for root, dp, fn, size, mt in iter_files(roots, exts, cancel, on_progress):
            key = (dp, fn)
            seen.add(key)
            if key in old:
                oid, osz, omt = old[key]
                if osz == size and abs(omt - mt) < 1:
                    continue
                stem, ext = os.path.splitext(fn)
                lab, sn, kind = volume_info(root)[0], volume_info(root)[1], drive_kind(root)
                buf.append(('u', oid, root, dp, fn, stem, ext.lower(), size, mt, lab, sn, kind))
                upd += 1
            else:
                stem, ext = os.path.splitext(fn)
                lab, sn, kind = volume_info(root)[0], volume_info(root)[1], drive_kind(root)
                buf.append(('a', None, root, dp, fn, stem, ext.lower(), size, mt, lab, sn, kind))
                add += 1
            if len(buf) >= BATCH:
                _apply(c, buf)
                buf = []
        if buf:
            _apply(c, buf)
        gone = [(k[0], k[1]) for k in old.keys() if k not in seen]
        for d, n in gone:
            oid = old[(d, n)][0]
            c.execute('DELETE FROM files WHERE id=?', (oid,))
            c.execute('DELETE FROM files_fts WHERE rowid=?', (oid,))
        cnt = c.execute('SELECT COUNT(*) FROM files').fetchone()[0]
        c.execute('INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)', ('built_at', _now()))
        c.execute('INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)', ('count', str(cnt)))
        c.commit()
    finally:
        c.close()
    if copy_to:
        try:
            import shutil
            shutil.copy2(db, copy_to)
        except Exception:
            pass
    return {'add': add, 'update': upd, 'delete': len(gone), 'files': cnt,
            'secs': round(time.time() - t0, 2)}


def _apply(c, buf):
    for it in buf:
        tag = it[0]
        if tag == 'a':
            cur = c.execute('INSERT INTO files(root,dir,name,stem,ext,size,mtime,'
                            'vol_label,vol_serial,drive_kind) VALUES(?,?,?,?,?,?,?,?,?,?)',
                            it[2:])
            rid = cur.lastrowid
        else:
            rid = it[1]
            c.execute('UPDATE files SET root=?,dir=?,name=?,stem=?,ext=?,size=?,mtime=?,'
                      'vol_label=?,vol_serial=?,drive_kind=? WHERE id=?', it[2:] + (rid,))
            c.execute('DELETE FROM files_fts WHERE rowid=?', (rid,))
        c.execute('INSERT INTO files_fts(rowid,name,stem) VALUES(?,?,?)',
                  (rid, charspace(it[4]), charspace(it[5])))


def detect_db(db):
    """认库型：'cathayviewer' | 'cathayindex' | ''（读不了）。只读打开，不改文件。"""
    if not db or not os.path.isfile(db):
        return ''
    try:
        c = connect(db, readonly=True)
    except Exception:
        return ''
    try:
        tabs = {str(r[0]).lower() for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        if 'files' in tabs and 'files_fts' in tabs:
            return 'cathayviewer'
        if _foreign_table(c):
            return 'cathayindex'
        return ''
    except Exception:
        return ''
    finally:
        c.close()


def _foreign_table(c):
    """找出可用的『别人的库』表：返回 (表名, 列名列表) 或 None。"""
    try:
        names = {str(r[0]).lower(): str(r[0]) for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    except Exception:
        return None
    for cand in ('items', 'local_files', 'files', 'docs', 'documents'):
        if cand in names:
            try:
                cols = [str(r[1]) for r in c.execute('PRAGMA table_info(%s)' % names[cand])]
            except Exception:
                continue
            lc = [x.lower() for x in cols]
            if ('filepath' in lc or 'path' in lc) and ('filename' in lc or 'name' in lc
                                                     or 'title' in lc):
                return names[cand], cols
    return None


def _search_foreign(db, kw, limit=500):
    """在别人的库（如 CathayIndex 的 local_files.db）里搜文件名 —— 只读。"""
    c = connect(db, readonly=True)
    try:
        ft = _foreign_table(c)
        if not ft:
            return []
        tbl, cols = ft
        lc = {x.lower(): x for x in cols}
        name_c = lc.get('filename') or lc.get('name')
        path_c = lc.get('filepath') or lc.get('path')
        size_c = lc.get('size')
        time_c = lc.get('mtime') or lc.get('modify_time') or lc.get('updated_at')
        sel = [name_c, path_c]
        if size_c:
            sel.append(size_c)
        if time_c:
            sel.append(time_c)
        like = '%' + kw + '%'
        sql = 'SELECT %s FROM %s WHERE %s LIKE ? OR %s LIKE ? LIMIT ?' % (
            ','.join('"%s"' % s for s in sel), tbl, name_c, path_c)
        out = []
        for r in c.execute(sql, (like, like, limit)):
            name = r[0] or ''
            path = r[1] or ''
            d = os.path.dirname(path)
            if not d and path:
                d = path
            out.append({'name': name, 'dir': d, 'ext': os.path.splitext(name or path)[1],
                        'size': (r[2] if size_c else 0) or 0,
                        'mtime': (r[3] if time_c else 0) or 0, 'path': path})
        return out
    except Exception:
        return []
    finally:
        c.close()


def _stats_foreign(db):
    c = connect(db, readonly=True)
    try:
        ft = _foreign_table(c)
        if not ft:
            return {}
        n = c.execute('SELECT COUNT(*) FROM %s' % ft[0]).fetchone()[0]
        return {'files': n, 'db_size': os.path.getsize(db), 'built_at': '(已有库)',
                'missing': 0, 'kind': 'cathayindex'}
    except Exception:
        return {}
    finally:
        c.close()


def search(db, kw, limit=500):
    """文件名/书名搜索（逐字短语，与家里别的库一致）。只读打开。"""
    if not os.path.isfile(db):
        return []
    c = connect(db, readonly=True)
    try:
        want = charspace(kw)
        if not want:
            return []
        sql = ('SELECT f.* FROM files_fts x JOIN files f ON f.id=x.rowid '
               'WHERE files_fts MATCH ? LIMIT ?')
        try:
            rows = c.execute(sql, ('"%s"' % want.replace('"', ''), limit)).fetchall()
        except sqlite3.Error:
            rows = []
        if not rows:
            like = '%' + kw + '%'
            rows = c.execute('SELECT * FROM files WHERE name LIKE ? OR stem LIKE ? '
                             'OR dir LIKE ? LIMIT ?', (like, like, like, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def by_dir(db, d, limit=500):
    """同一文件夹下已索引的文件（直接打开某文件时列出“同文件夹的其他文件”）。只读。"""
    if not os.path.isfile(db) or not d:
        return []
    c = connect(db, readonly=True)
    try:
        try:
            rows = c.execute('SELECT * FROM files WHERE dir=? COLLATE NOCASE '
                             'ORDER BY name LIMIT ?', (d, limit)).fetchall()
        except sqlite3.Error:
            rows = []
        return [dict(r) for r in rows]
    finally:
        c.close()


def by_dir_ext(db, d, exts, limit=800):
    """同文件夹下**受支持格式**的文件（书库浏览树展开时用）。只读。

    与 by_dir 的区别：扩展名在 SQL 层就过滤掉。若先 LIMIT 再在 Python 里筛，
    遇上"2.5 万个文件里绝大部分是 .htm"那种大目录，取回的前 N 条可能一个
    能打开的都没有，用户就会看到"这个文件夹是空的"。
    """
    if not os.path.isfile(db) or not d:
        return []
    _e = tuple((x or '').lower() for x in (exts or ()) if x)
    if not _e:
        return []
    ph = ','.join('?' * len(_e))
    c = connect(db, readonly=True)
    try:
        try:
            rows = c.execute(
                'SELECT * FROM files WHERE dir=? COLLATE NOCASE '
                'AND lower(ext) IN (%s) ORDER BY name LIMIT ?' % ph,
                (d,) + _e + (int(limit),)).fetchall()
        except sqlite3.Error:
            rows = []
        return [dict(r) for r in rows]
    finally:
        c.close()


def dir_counts(db, exts):
    """每个文件夹下**受支持格式**的文件数 → [(dir, n), …]。只读。

    建文件树骨架时用：只统计打得开的那些，免得树里冒出一堆"点开什么都没有"
    的空文件夹（库里 15% 是 .htm、13% 是 .xls，全算进去会严重误导）。
    """
    if not os.path.isfile(db):
        return []
    _e = tuple((x or '').lower() for x in (exts or ()) if x)
    if not _e:
        return []
    ph = ','.join('?' * len(_e))
    c = connect(db, readonly=True)
    try:
        try:
            rows = c.execute(
                'SELECT dir, COUNT(*) FROM files WHERE lower(ext) IN (%s) '
                'GROUP BY dir' % ph, _e).fetchall()
        except sqlite3.Error:
            rows = []
        return [(r[0] or '', int(r[1] or 0)) for r in rows]
    finally:
        c.close()


def like_stem(db, core, limit=300):
    """书名主干相似（前缀相同）的其它文件（不限文件夹）。只读。"""
    if not os.path.isfile(db) or not core:
        return []
    c = connect(db, readonly=True)
    try:
        try:
            rows = c.execute('SELECT * FROM files WHERE stem LIKE ? ORDER BY name LIMIT ?',
                             (core + '%', limit)).fetchall()
        except sqlite3.Error:
            rows = []
        return [dict(r) for r in rows]
    finally:
        c.close()


def stats(db):
    if detect_db(db) == 'cathayindex':
        return _stats_foreign(db)
        return {}
    c = connect(db, readonly=True)
    try:
        out = {r['k']: r['v'] for r in c.execute('SELECT k,v FROM meta')}
        out['files'] = c.execute('SELECT COUNT(*) FROM files').fetchone()[0]
        out['db_size'] = os.path.getsize(db)
        out['missing'] = 0
        for r in c.execute('SELECT dir,name FROM files LIMIT 20000'):
            if not os.path.exists(os.path.join(r['dir'], r['name'])):
                out['missing'] += 1
        return out
    finally:
        c.close()


# ---------------------------------------------------------------- 自检
def selftest():
    import shutil
    import tempfile
    log = []

    def ok(cond, msg):
        log.append(('OK  ' if cond else 'FAIL') + ' ' + msg)
        return bool(cond)

    base = tempfile.mkdtemp(prefix='cv_st_')
    src = os.path.join(base, '书库')
    deep = os.path.join(src, '子库', '孙目录')
    os.makedirs(deep, exist_ok=True)
    names = ['甲书.pdf', '甲书_【繁转简】.txt', '乙书_PD6AIFOCR.pdf', '丙书.epub',
             '说明.docx', '忽略我.bin']
    for n in names:
        open(os.path.join(src, n), 'wb').write(b'x' * 32)
    open(os.path.join(deep, '丁书.epub'), 'wb').write(b'x' * 32)
    before = sorted((f, os.stat(os.path.join(src, f)).st_mtime) for f in os.listdir(src))
    db = os.path.join(base, 'idx.db')
    bk = os.path.join(base, '备查', 'idx.db')
    r = build([src], db, copy_to=bk)
    ok(r['files'] == 6, '建库：扫到 6 个文件（含孙目录 1 个；.bin 被扩展名过滤）→ %d' % r['files'])
    ok(os.path.isfile(db) and os.path.isfile(bk), '主库 + 备查副本都生成')
    ok(not os.path.exists(db + '.building'), '没有残留 .building 临时文件')
    hits = search(db, '甲书')
    ok(len(hits) == 2, '搜索「甲书」命中 2 个版本 → %d' % len(hits))
    ok(len(search(db, '丁书')) == 1, '孙目录里的文件也能搜到（递归）')
    ok(search(db, '忽略我') == [], '被过滤的扩展名不进库')
    after = sorted((f, os.stat(os.path.join(src, f)).st_mtime) for f in os.listdir(src))
    ok(before == after, '源书库**未被改动**（mtime 逐一相同）')
    ok(sorted(f for f in os.listdir(src)
               if os.path.isfile(os.path.join(src, f))) == sorted(names),
       '源目录里没多出任何文件（子目录也在）')
    st = stats(db)
    ok(int(st['files']) == 6 and st['missing'] == 0, '统计正常：%d 个文件，%d 缺失' %
       (int(st['files']), int(st['missing'])))
    open(os.path.join(deep, '戊书.pdf'), 'wb').write(b'x' * 48)
    r2 = refresh(db, [src])
    ok(r2['add'] == 1 and r2['files'] == 7, '增量刷新：+1 新增 → %s' % r2)
    os.remove(os.path.join(src, '丙书.epub'))
    r3 = refresh(db, [src])
    ok(r3['delete'] == 1 and r3['files'] == 6, '增量刷新：-1 删除 → %s' % r3)
    key = volume_key(src)
    ok(bool(key), '认盘标识：%s（类型=%d）' % (key, drive_kind(src)))
    ok(sqlite3.connect('file:%s?mode=ro' % db.replace('\\', '/'), uri=True)
       .execute('SELECT COUNT(*) FROM files').fetchone()[0] == 6, '库能以只读方式打开（mode=ro）')
    shutil.rmtree(base, ignore_errors=True)
    txt = '\n'.join(log)
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    print('%s %s' % (APP_TITLE, APP_VERSION))
    print(txt)
    print('result = ' + ('OK' if all(l.startswith('OK') for l in log) else 'FAIL'))
    try:
        open(os.path.join(app_dir(), '_selftest.txt'), 'w', encoding='utf-8').write(
            '%s %s\n%s\nresult = %s\n' % (APP_TITLE, APP_VERSION, txt,
                                          'OK' if all(l.startswith('OK') for l in log) else 'FAIL'))
    except Exception:
        pass


def cli(argv):
    roots = [a for a in argv if not a.startswith('-')][:8]
    db = os.path.join(app_dir(), 'cathayviewer_index.db')
    for i, a in enumerate(argv):
        if a in ('--out', '-o') and i + 1 < len(argv):
            db = argv[i + 1]
    if not roots:
        s = load_settings()
        roots = s.get('roots') or DEFAULT_ROOTS
    r = build(roots, db, on_progress=lambda p: print('  ', p))
    print('建库完成：%s' % r)


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        selftest()
    elif '--cli' in sys.argv:
        cli(sys.argv)
    else:
        selftest()
