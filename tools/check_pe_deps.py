# -*- coding: utf-8 -*-
"""打包产物依赖体检 —— 回答一个问题：**换一台没装过任何东西的电脑，它还能不能起来。**

PyInstaller 打包最阴的一类 bug 是这样的：你这台机器上装了 Python、装了 Qt、
装了各种 VC 运行库，所以哪怕包里漏了个 dll，跑起来也照样好好的 ——
因为它在别处被找到了。等包到了别人机器上，就是"双击没反应"或者
"应用程序无法启动"。

所以不能靠"能跑"来证明，要靠**静态把每个 PE 文件的导入表读一遍**：
<_probe_pe_deps.py> 逐个 exe / dll / pyd 的 IMAGE_IMPORT_DESCRIPTOR
走一遍，把要找的每个 dll 名记下来，然后按这个顺序去找它：

    1) 这个文件自己所在的目录
    2) 整个发布包目录树（包内部的 dll 统称 '<包内>'）
    3) C:\\Windows\\System32 等系统目录（'<系统>'：干净 Win10/11 自带，可以依赖）
    4) 都找不到 → **缺失**，这才是会炸的地方

用法：
    python _probe_pe_deps.py <包目录1> [包目录2 ...]
    python _probe_pe_deps.py dist_vw316/CathayHub dist_slim/CathayHub   # 两个一起时会 diff

采样 === 纯 struct 解析 PE，不需要 pefile 之类的第三方库。
"""

import os
import struct
import sys

# 干净 Windows 一定自带的 dll（含 VC++ 通用 CRT 与 VC 运行库的系统同名场合）。
# 这些标记为「可依赖系统」，不算缺失。
SYSTEM_DIRS = [
    os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32'),
    os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'SysWOW64'),
    os.path.join(os.environ.get('SystemRoot', r'C:\Windows')),
]

PE_EXTS = {'.exe', '.dll', '.pyd'}


def _sys_has(name):
    return any(os.path.isfile(os.path.join(d, name)) for d in SYSTEM_DIRS)


def _rva_to_off(rva, sections):
    """把 RVA 换算成文件偏移：看它落在哪一节的虚拟地址区间里。"""
    for va, vsize, raw_ptr, raw_size in sections:
        if 0 <= rva - va < max(vsize, raw_size):
            return raw_ptr + (rva - va)
    return None


def read_imports(path):
    """读一个 PE 文件的导入表，返回它要找的 dll 名列表（小写）。失败返回 None。"""
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except Exception:
        return None
    if data[:2] != b'MZ':
        return None
    try:
        e_lfanew = struct.unpack_from('<I', data, 0x3C)[0]
        if data[e_lfanew:e_lfanew + 4] != b'PE\0\0':
            return None
        coff = e_lfanew + 4
        nsec, = struct.unpack_from('<H', data, coff + 2)
        opt_size, = struct.unpack_from('<H', data, coff + 16)
        opt = coff + 20
        magic, = struct.unpack_from('<H', data, opt)
        dd_off = opt + (96 if magic == 0x10B else 112)      # Optional header 里 DataDirectory 的位置
        # IMPORT table 是第 2 项（index 1）
        imp_rva, imp_size = struct.unpack_from('<II', data, dd_off + 8)
        sec_off = opt + opt_size
        sections = []
        for i in range(nsec):
            base = sec_off + i * 40
            vsize, rva, raw_size, raw_ptr = struct.unpack_from('<IIII', data, base + 8)
            sections.append((rva, vsize, raw_ptr, raw_size))
        if not imp_rva:
            return []
        off = _rva_to_off(imp_rva, sections)
        if off is None:
            return []
        names = []
        while True:
            desc = data[off:off + 20]
            if len(desc) < 20:
                break
            vals = struct.unpack('<IIIII', desc)
            if vals == (0, 0, 0, 0, 0):
                break
            name_rva = vals[3]
            n_off = _rva_to_off(name_rva, sections)
            if n_off is None:
                break
            end = data.find(b'\0', n_off)
            if end < 0:
                break
            names.append(data[n_off:end].decode('ascii', 'replace').lower())
            off += 20
        return names
    except Exception:
        return None


def scan(root):
    """扫描整个包目录：返回 (缺依赖 map, PE 文件数)。"""
    all_pes = []
    for r, _d, fs in os.walk(root):
        for x in fs:
            if os.path.splitext(x)[1].lower() in PE_EXTS:
                all_pes.append(os.path.join(r, x))

    # 包内所有文件名（含各层目录），用于「第二步：包内找」
    in_pkg = set()
    for r, _d, fs in os.walk(root):
        for x in fs:
            in_pkg.add(x.lower())

    missing = {}        # dll -> 谁在找它
    for p in all_pes:
        names = read_imports(p)
        if not names:
            continue
        own_dir = os.path.dirname(p)
        for n in names:
            if os.path.isfile(os.path.join(own_dir, n)):
                continue
            if n in in_pkg:
                continue
            # api-ms-*/ext-ms-* 是 Windows 的 API Set：它们从来不以文件形式躺在
            # System32 里，运行时由 ntdll 按 api set schema 就地虚解析 ——
            # 干净的 Win10/11 一定有。谁把它当缺失，谁就得白紧张一场。
            if n.startswith(('api-ms-win-', 'ext-ms-win-')):
                continue
            if _sys_has(n):
                continue
            missing.setdefault(n, []).append(os.path.relpath(p, root))
    return missing, len(all_pes)


def main():
    roots = sys.argv[1:]
    if not roots:
        print(__doc__)
        return 2
    results = []
    for root in roots:
        absroot = os.path.abspath(root)
        # 几个 dist_*/CathayHub 同名，光看 basename 会互相覆盖，带上父目录
        tag = os.path.join(os.path.basename(os.path.dirname(absroot)),
                           os.path.basename(absroot))
        missing, n = scan(root)
        results.append((tag, missing))
        rel = os.path.relpath(os.path.abspath(root), os.getcwd()).replace(chr(92), '/')
        print('=' * 74)
        print('%s  （PE 文件 %d 个）' % (rel, n))
        if not missing:
            print('  ✓ 没有缺失依赖：包里的 dll 都齐了，剩下的 windows dll 干净系统自带。')
        else:
            for k in sorted(missing):
                who = ', '.join(sorted(set(missing[k]))[:3])
                more = '' if len(missing[k]) <= 3 else ' … 等 %d 处' % len(set(missing[k]))
                print('  ✗ 缺 %-28s ← %s%s' % (k, who, more))

    if len(roots) == 2:
        (tag_a, miss_a), (tag_b, miss_b) = results[0], results[1]
        new = set(miss_b) - set(miss_a)
        print('=' * 74)
        print('对比：%s  →  %s' % (tag_a, tag_b))
        if new:
            print('  ⚠ 后面这个包**新出现**的缺失（这就是会让干净电脑跑不起来的东西）：')
            for k in sorted(new):
                print('     ', k)
        else:
            print('  ✓ 没有新增任何缺失：瘦身不会在新电脑上多炸一处。')
        return 1 if new else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
