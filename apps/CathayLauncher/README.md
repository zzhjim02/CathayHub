# CathayHub Launcher + 路径转译

## 这两个东西解决什么问题

书库索引里记的文件路径，是**当初建索引时**的路径，比如：

    X:\我的书库\某书.pdf

如果你后来把书库挪到了 D 盘（或者换电脑、接了个新硬盘），索引里那条老路径就打不开了：
**搜索照样出结果，但点开时提示文件不存在。**

- **CathayHub Launcher**：第一次打开时自动检测"索引在哪、书库在哪"，生成本地的
  路径对照表，并把找到的索引攒成一个检索组。
- **path_translator（路径转译）**：搜索和阅读在打开文件前，查一下对照表，
  把老路径换算成现在的真实路径。你点开结果时，书照常打开。

全程**只改"要打开的路径"，不改动索引本身，也不移动任何文件**。

---

## 文件清单

| 文件 | 作用 |
|---|---|
| `CathayHubLauncher.py` | 启动器入口（`--selftest` 跑自检） |
| `cathayhub/config.py` | 程序目录、那三个 exe 在哪 |
| `cathayhub/detect.py` | 检测逻辑：找索引、找书库、写映射、登记、写主题、建文件名索引记账 |
| `cathayhub/gui.py` | 首次运行向导（5 步）+ 主界面 + 设置页 |
| `cathayhub/path_translator.py` | **共用模块**：白名单、状态判定、转译、找索引 |
| `cathayhub/filelib.py` | **共用模块**：文件名索引引擎（viewer_core.py 的副本） |
| `CathayHubLauncher.spec` | 打包配置（`_internal_launcher`，与另外三个共存） |

`path_translator.py` 同时被**四个**软件引用，各放一份副本：

    CathayHub\shared\path_translator.py          ← 正本
    CathaySearch-DEV\cathaysearch\path_translator.py
    CathayViewer-DEV\path_translator.py
    CathayIndexer-DEV\cathayindexer\path_translator.py
    CathayLauncher-DEV\cathayhub\path_translator.py

`filelib.py` 是 `CathayViewer-DEV\viewer_core.py` 的副本（两边建出来的库格式
必须一致，Viewer 才能直接认下 Launcher 建好的库）：

    CathayHub\shared\filelib.py                  ← 正本（带说明头）
    CathayLauncher-DEV\cathayhub\filelib.py

改了正本记得同步各份副本。

---

## 怎么运行

开发态（DEV 目录里，会自动把 CathayHub 目录认作程序目录）：

    python CathayHubLauncher.py
    python CathayHubLauncher.py --selftest      # 自检，不弹窗

打包：

    python -m PyInstaller CathayHubLauncher.spec --noconfirm

产物 exe 放进 `CathayHub\` 文件夹，与 Search / Viewer / Indexer 并列即可。

---

## 首次运行向导（5 步）

1. **自动检测** —— **不用点任何按钮**，页面一出现就自己开跑
2. **检测结果** —— 只看索引和书库：
   - 找到的**索引**（它收着哪几个书库、放在哪、是不是新登记的）
   - **书库文件夹**在不在（缺了就地说明，主要书库用红字；要手动指定就点页面上的按钮）
3. **文件名索引** —— 按书名找文件用的那份库：要不要现在建、扫哪些目录、放哪儿。
   勾上"现在就建"才走第 4 步
4. **建文件名索引**（勾了才有这一步）——只读扫一遍，进度和日志都在页面上，
   不想等可以点"跳过"
5. **完成** —— 总结 + 顺手挑个颜色（浅色 / 深色 / 护眼，三个软件一起变）

> 【v0.3.9】原先把"文件名索引"压在第 2 步底下：一页里堆着索引表、书库表、
> 再加一整组"要不要建库、扫哪些目录、库放哪"，看着全叠在一起。它跟"索引/书库
> 在不在"其实是两件事，现在单独第 3 步，一页只问一件事。
>
> 更早之前的"欢迎页"、"缺的书库怎么办（弹窗）"、"挑个颜色"三步也都并掉了：
> 欢迎页并进检测（反正自动跑），缺失处理并进检测结果（就地说明 + 就地指定，
> 不再弹窗打断），挑颜色挪到完成页的一行里。

---

## 向导做完会落下四样东西

| 落到哪 | 是什么 | 谁在用 |
|---|---|---|
| `shared/path_mapping.json` | 路径对照表（哪个书库挪到哪去了） | Search / Viewer 打开文件时 |
| `%APPDATA%\Mythicsoft\FileLocatorPro\` | 索引登记条目 + 组「全部可用索引」 | 引擎（`-idxname`） |
| `shared/launcher.json` | `default_index` / `default_index_stamp` | **Search 启动时读** |
| `shared/launcher.json` + `cathayviewer_settings.json` | `filename_db` / `filename_roots`：文件名索引在哪、扫了哪些目录 | **Viewer 启动时读** |

第三行是"默认搜谁"的关键：Search 以前把默认索引写死在主索引上，
现在改成先读 `launcher.json`，读到就用 Launcher 攒的那个组。

- `default_index_stamp` 是个时间戳。**时间戳变了 = 刚跑过新一轮向导**，
  Search 会把默认切回组；用过一次就记下这个戳，之后你自己挑什么都不抢。
- 没这个文件（没跑过 Launcher）→ 退回老默认，行为跟以前完全一样。

第四行是"阅读用的文件名索引"（就是 Viewer 里那个「建库 / 刷新」建出来的库，
按书名找文件全靠它）：

- Launcher 用**同一份引擎**（`filelib.py`，即 `viewer_core.py` 的副本）来建，
  建出来的库 Viewer 能直接读；
- 建完记两笔账，其中一笔直接写进 Viewer 自己的设置 ——
  **Viewer 一打开就知道库已经有了，不会再弹"第一次使用 —— 建立文件名索引"问用户**；
- 库默认放在 `CathayHub\cathayviewer_index.db`，正是 Viewer 默认找的位置；
- 库已经存在时，向导默认**不勾**"现在就建"（免得每次重跑都重扫几十万文件），
  也不会去动 Viewer 原来那套扫描目录。

索引数据本身全程只读，一步都没碰。

之后打开就是主界面：搜索 / 阅读 / 索引 三个大按钮 + 最近打开 + 状态栏。
左上角常驻**「首次运行向导（索引加载）」**，随时可以重新检测。

**点那三个大按钮，Launcher 会把那个软件叫起来然后自己退出** —— 它本来就是个入口，
任务交出去了就没必要杵在前台。【v0.3.9】
按住 **Shift** 再点则留在本界面（一次要开好几个时用）。

---

## 路径对照表长什么样

写在 `CathayHub\shared\path_mapping.json`：

```json
{
  "我的书库": {
    "expected": "X:\\我的书库",
    "actual": "X:\\我的书库",
    "translated": false
  },
  "学术古籍全文检索数据库": {
    "expected": "X:\\学术古籍全文检索数据库",
    "actual": "D:\\古籍库",
    "translated": true
  }
}
```

`translated: true` 表示"这个书库不在原来的位置了，已经接到新位置"。
文件不存在、内容坏了，转译函数一律**原样返回**，不会出错。

---

## 自己的程序怎么接转译（两行）

```python
from path_translator import translate_path, translated_label

real, rule = translate_path(r'X:\我的书库\a\b.pdf')
if rule:
    print(translated_label(rule))      # 路径已转译：Y: → D:
```

Search 和 Viewer 里已经接好了（打开文件或点"最近打开"之前各调一次），
状态栏会提示一句"路径已转译：Y盘 → D盘"。

---

## 照看哪些书库（白名单）

只有列在 `path_translator.py` 的 `MAIN_DIRS` 里的目录会被检测和转译，
其余（比如 `D:\华为云盘\文档\` 下面那一串）一律不管——不检测、不提示。

| 书库 | 默认位置 | 缺了怎么办 |
|---|---|---|
| 我的书库 | Y:\… | 弹窗提醒（主要） |
| 学术信息全文数据库-扩展 | Y:\… | 弹窗提醒（主要） |
| 网盘同步目录 | D:\…\网盘同步目录 | 弹窗提醒 |
| 专题库A | Y:\… | 有就显示、没有就不显示（随缘次要） |
| 专题库B | Y:\… | 有就显示、没有就不显示（随缘次要） |
| 我的导引库 | D:\… | 弹窗提醒 |
| 学术古籍全文检索数据库 | Y:\… | 安静标一行（次要） |
| 中小学教科书集成 | Y:\… | 安静标一行（次要） |
| 个人网络聊天记录全文检索数据库 | Y:\… | 有就显示、没有就不显示（随缘次要） |

「随缘次要」（代码里叫 `dual`）的口诀是：**有就显示，没有就不显示。**

- 书库还在 → 正常显示一行；
- 书库找不到、但还有索引收着它 → 照样显示一行，只轻声提示（不弹窗）；
- 书库和索引两样都没有 → **整条不显示**，列表里不放没用的空行。

---

## 几个要注意的地方

- **一个索引可能索引几十个目录**（主索引就挂了 28 个）。判断"这个索引还在不在"，
  只看其中标了 `main` 的那几个主要目录——其余目录缺不缺不影响结论。
  早期版本把整条分号串拿去 `os.path.isdir()`，结果恒为 False，
  导致主索引被误报"已不存在"、更新也被拦住。现在已修。
- **匹配取最长前缀**：`D:\导引库` 是 `D:\导引库\网盘同步目录` 的父目录，
  两条规则都能匹配同一条路径，长的那条优先。
- **忽略大小写**：索引里出现过小写的 `d:\`。
- **更新索引要小心**：如果主要书库在、但别的目录缺了，更新时引擎会把那些目录里
  记的文件判成"已删除"。所以这种情况会先弹一个确认框，列清楚会丢哪些目录。
