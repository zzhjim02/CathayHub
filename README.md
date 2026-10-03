<div align="center">

# 🏛️ CathayHub

**一套给人文社科研究者用的本地书库工具 —— 在自己的文件夹里检索，找到的书直接翻开，指的是同一本实体书的各种文件互相认得出来**

![license](https://img.shields.io/badge/license-GPL--3.0-blue)
![platform](https://img.shields.io/badge/platform-Windows%2010%2B-lightgrey)
![python](https://img.shields.io/badge/python-3.10%2B-3776ab)
![version](https://img.shields.io/badge/Viewer-v0.3.16-orange)

</div>

> 所有的"看见"都是只读的：**不改动你的任何一个源文件，也不往书库里写一个字节**。
> 索引数据（可能几十 GB）始终留在你自己的盘上，本程序从不写它。

---

## 🔗 Cathay 人文社科工具链

| 步骤 | 工具 | 功能 |
|:---:|---|---|
| ① | CathayIndex | 把本地文件夹建成可检索的「本地文件库」 |
| ② | CathayFinder | 综合性图书检索引擎：11 个渠道精准查书 |
| ③ | CathayPDG | 读秀/超星 PDG 批量转 PDF |
| ④ | CathayOCR | 扫描件 OCR，产出可搜索文字层 PDF |
| ⑤ | CathayShelf | 图书著录自动化整理（繁简转换 / 编码规范化） |
| ⑥ | CathayViewer | 书库浏览与阅读（**单体版，仍单独维护**） |
| ⑦ | **CathayHub（本仓库）** | **四合一发行版：把入口、索引、检索、阅读装进同一个文件夹，开箱即用** |

CathayHub 是这套工具链的**整合出口**：四个程序并列放在同一个文件夹，既能互相唤起，
也能各自双击独立运行。日常其实只要用一个 —— 在 Search 里双击结果，书就在 Viewer 里翻开了。

---

## 📦 四个程序

| 程序 | 版本 | 干什么 |
|---|---|---|
| **CathayHub Launcher** | 0.1.3 | 第一次打开时用它：自动找"索引在哪、书库在哪"，做好路径对照。**书库挪位置后不用来回改路径** |
| **CathayHub Indexer** | 0.1.1 | 管检索索引：新建、引用已有、增量更新、重建、重命名、删除、分组、定时更新 |
| **CathayHub Search** | 0.2.14 | 跨整个书库全文检索。走 FileLocator Pro 索引，十几万条秒级出结果，还能算出命中在 PDF 第几页 |
| **CathayHub Viewer** | v0.3.16 | 浏览与阅读。文件名索引、文内查找、PDF/TXT 对读、摘录本、截图本、学术引用，**多本书用标签页并列翻** |

---

## 🚀 下载与安装

**Windows 10 / 11（64 位）。不需要你装 Python、Qt、PyMuPDF —— 全部随包带来。**

1. 到 [Releases](../../releases) 下载 `CathayHub 发行版 *.zip`
2. 解压到任意位置（本地盘、移动硬盘都行），**整个文件夹拷走即可**
3. 第一次先双击 **「CathayHub Launcher」** 跑一遍向导

> Windows 出于安全考虑不允许程序直接抢"默认打开方式"。
> 想让 PDF/TXT 双击就用 Viewer 打开：双击文件夹里的 **`关联文件类型.bat`**
> （普通权限即可，只动你自己那份注册表，也不会抢走现在的默认程序），
> 再去「设置 → 应用 → 默认应用」里手动选一次。不想要了就跑 **`取消文件关联.bat`**。

详细操作流程、快捷键表、常见故障排查见 **[使用指南.md](使用指南.md)**。

---

## 🛠️ 从源码构建

普通用户用不到这一节。想改代码或重新打包 exe 才需要。

```bash
# 1. 装依赖
pip install -r requirements.txt
pip install pyinstaller>=6.0

# 2. 逐个打包（四个各自打，产物目录名都叫 CathayHub）
python -m PyInstaller --noconfirm --clean apps/CathayViewer/CathayHubViewer.spec   --distpath dist_viewer   --workpath build_viewer
python -m PyInstaller --noconfirm --clean apps/CathaySearch/CathayHubSearch.spec    --distpath dist_search   --workpath build_search
python -m PyInstaller --noconfirm --clean apps/CathayLauncher/CathayHubLauncher.spec --distpath dist_launcher --workpath build_launcher
python -m PyInstaller --noconfirm --clean apps/CathayIndexer/CathayHubIndexer.spec  --distpath dist_indexer  --workpath build_indexer

# 3. 合并到同一个文件夹（四个程序的 _internal_* 各占一份，互不干扰）
mkdir CathayHub
cp -r dist_viewer/CathayHub/.   CathayHub/
cp -r dist_search/CathayHub/.   CathayHub/
cp -r dist_launcher/CathayHub/. CathayHub/
cp -r dist_indexer/CathayHub/.  CathayHub/
```

**注意**：`runtime/FileLocatorPro/`（检索引擎，第三方软件）**不在本仓库里**，
需要自己放进去，否则 Search 搜不了。增量更新索引要求引擎 9.3 及以上。

### 打包瘦身

四个 spec 都调用了 [`tools/pack_utils.py`](tools/pack_utils.py) 的 `peel_unused()`，
剔掉了源码里一行都没 import 的库（pandas / numpy / PIL / cryptography / psutil / tzdata 等），
以及 Qt 里用不到的软件 OpenGL（20 MB）、96 种语言翻译（只留中文）、QtPdf / Svg / Network。
**613 MB → 327 MB**。

这里有个坑值得说：光在 spec 里排除 `qt6svg.dll` 是不够的 —— `imageformats\qsvg.dll`、
`iconengines\qsvgicon.dll` 仍会被打进来，它们加载时找不到 Qt6Svg，
**在开发机上多半只是个警告，换台电脑可能就是"双击没反应"**。
`peel_unused()` 做的是**依赖传递闭包**：点名的不要，凡依赖它的一律跟着不要。

验证用 [`tools/check_pe_deps.py`](tools/check_pe_deps.py)：逐个解析包里所有 exe/dll/pyd 的
导入表，检查每个 dll 在哪能找到，对比瘦身前后有无新增缺失。

```bash
python tools/check_pe_deps.py 旧包目录 新包目录
```

---

## 📁 源码目录

```
CathayHub/
├── apps/
│   ├── CathayViewer/      阅读器（gui.py + viewer_*.py + CathayHubViewer.spec）
│   ├── CathaySearch/      检索（cathaysearch/ + CathayHubSearch.spec）
│   ├── CathayLauncher/    入口向导（cathayhub/ + CathayHubLauncher.spec）
│   └── CathayIndexer/     索引管理（cathayindexer/ + CathayHubIndexer.spec）
├── tools/
│   ├── pack_utils.py      打包瘦身：依赖传递闭包裁剪
│   └── check_pe_deps.py   依赖体检：解析 PE 导入表找缺失
├── docs/                  开发过程记录
├── requirements.txt
├── 使用指南.md             详细功能手册
└── LICENSE                GPL-3.0
```

每个 app 目录内保持原有的相对结构，`*.spec` 里的路径都是相对于 spec 自身的，直接就能跑。

---

## 🔒 数据边界

| 数据 | 放哪 | 能不能删 |
|---|---|---|
| 四个 exe 与 `_internal_*` | 安装文件夹 | 升级时整批替换 |
| FileLocator 引擎 | `runtime\FileLocatorPro\` | 不能删，删了就搜不了 |
| 索引**数据**（几十 GB） | 你自己的盘上，**不在安装文件夹里** | 本程序从不写它 |
| 文件名索引 `cathayviewer_index.db` | 安装文件夹 | 删了重建即可，但别在升级时删 |
| 设置与进度 `cathayviewer_settings.json` | 安装文件夹 | 删了就回到出厂设置，**别删** |

**仓库里不含任何个人数据**：`.gitignore` 明确挡掉了 `*.db` 与 `cathayviewer_settings.json`
（这两个文件含个人书库路径与阅读记录，误提交等于把隐私推上公网）。

---

## 📜 更新日志

当前构建：**Search 0.2.14 / Viewer v0.3.16 / Launcher 0.1.3 / Indexer 0.1.1**（2026-10-03）

Viewer v0.3.16 主要改动：

1. **打开 PDF 有进度条了** —— 顶部 3px 细线，预读 / 开文档 / 抽页尺寸三段真实进度合成，缓动推进只许前进不许后退
2. **「所有关键词」下点「下一个」点不动** —— 修了。根因是把"填结果"和"跳位置"绑成一条条件，页码只要被挪过一下结果就整个不建了
3. **`_OPT` 结尾能认亲了** —— `_OPT`/`_opt`/`（优化版）`/`（压缩）`只是"这本被压过"的记号，不是另一本书。1870 本实测漏配 13 → 0
4. **关掉「图文对读」回到 PDF** —— 以前不管先开什么都只剩 TXT，现在有 PDF 优先回 PDF
5. **全项目体检**：修掉 6 处"关窗即崩进程"，四个 exe 瘦身 613 MB → 327 MB

各程序的完整历史见 [使用指南.md](使用指南.md) 第八节。

---

## ⚖️ 许可

**GPL-3.0**，见 [LICENSE](LICENSE)。

第三方组件：FileLocator Pro（检索引擎，需自备）、PyMuPDF（PDF 读取）、PyQt6（界面）、
OpenCC / zhconv（繁简转换）、CBDB 人名别名数据。
