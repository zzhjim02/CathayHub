# -*- coding: utf-8 -*-
"""打包期工具：把"指定了不用哪些 dll"变成"连带肯跟着一起进来的一切都别过来"。

为什么需要它
------------
假设在 spec 里写了 excludes=['Qt6Svg']，PyInstaller 只吐掉 Qt6Svg.dll 本身；
可 imageformats\\qsvg.dll、iconengines\\qsvgicon.dll 这两个插件还照旧被打进来 ——
它们一加载就要去找 Qt6Svg.dll，找不到就加载失败。

在本机上这未必看得出来（Qt 加载插件失败通常只是警告），但**换台机器可能
就是"双击没反应"这类怎么都查不出原因的问题**。所以要按依赖做**传递闭包**：
我说不要 Qt6Svg → 凡是直接或间接依赖 Qt6Svg 的文件，一律跟着不要。

用法（在 spec 里）：:

    from pack_utils import peel_unused
    a.binaries = peel_unused(a.binaries, {'qt6svg.dll', 'qt6pdf.dll'})
"""

import os
import struct


def _sections(data, coff):
    nsec, = struct.unpack_from('<H', data, coff + 2)
    opt_size, = struct.unpack_from('<H', data, coff + 16)
    sec_off = coff + 20 + opt_size
    out = []
    for i in range(nsec):
        base = sec_off + i * 40
        _name = data[base:base + 8]
        vsize, rva, raw_size, raw_ptr = struct.unpack_from('<IIII', data, base + 8)
        out.append((rva, vsize, raw_ptr, raw_size))
    return out


def _off_of(rva, sections):
    for va, vsize, raw_ptr, raw_size in sections:
        if 0 <= rva - va < max(vsize, raw_size):
            return raw_ptr + (rva - va)
    return None


def imports_of(path):
    """读 PE 的导入表，返回它要找的 dll 名集合（小写）。非 PE / 读不动 → 空集。"""
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except Exception:
        return set()
    if data[:2] != b'MZ':
        return set()
    try:
        e_lfanew = struct.unpack_from('<I', data, 0x3C)[0]
        if data[e_lfanew:e_lfanew + 4] != b'PE\0\0':
            return set()
        coff = e_lfanew + 4
        opt = coff + 20
        magic, = struct.unpack_from('<H', data, opt)
        dd = opt + (96 if magic == 0x10B else 112)
        imp_rva, _imp_size = struct.unpack_from('<II', data, dd + 8)
        sections = _sections(data, coff)
        if not imp_rva:
            return set()
        off = _off_of(imp_rva, sections)
        if off is None:
            return set()
        names = set()
        while True:
            desc = data[off:off + 20]
            if len(desc) < 20:
                break
            vals = struct.unpack('<IIIII', desc)
            if vals == (0, 0, 0, 0, 0):
                break
            n_off = _off_of(vals[3], sections)
            if n_off is None:
                break
            end = data.find(b'\0', n_off)
            if end < 0:
                break
            names.add(data[n_off:end].decode('ascii', 'replace').lower())
            off += 20
        return names
    except Exception:
        return set()


def _tail(name):
    return (name or '').replace(chr(92), '/').split('/')[-1].lower()


def peel_unused(toc, drop_names, verbose=False):
    """按依赖做传递闭包剔除。

    toc:        PyInstaller 的 TOC（每项至少是 (dest_name, src_path, ...)）
    drop_names: 明确不要的文件名集合（小写，只写文件名即可）

    返回过滤后的 TOC，并在 TOC 里已丢找不到的源文件时静默跳过。
    """
    drop = {str(n).lower() for n in drop_names}
    changed = True
    while changed:
        changed = False
        for item in toc:
            name = _tail(item[0])
            if name in drop or not name.endswith(('.dll', '.pyd', '.exe')):
                continue
            src = item[1] if len(item) > 1 else item[0]
            if not isinstance(src, str) or not os.path.isfile(src):
                continue
            deps = imports_of(src)
            if deps & drop:
                drop.add(name)
                if verbose:
                    print('   peel：%s 依赖已剔除项 → 一并删掉' % name)
                changed = True
    kept = [it for it in toc if _tail(it[0]) not in drop]
    return kept
