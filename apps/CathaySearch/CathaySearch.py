# -*- coding: utf-8 -*-
"""CathaySearch 启动入口。

双击或在命令行直接运行本文件即可打开图形界面。
开发环境用带 PyQt6 + PyMuPDF 的解释器，例如：
  C:\\Users\\zzhjim\\AppData\\Local\\Python\\pythoncore-3.14-64\\python.exe CathaySearch.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cathaysearch.gui import main     # noqa: E402

if __name__ == '__main__':
    sys.exit(main())
