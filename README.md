# litpush · 学术文献每日推送（Obsidian / Markdown）

每天自动扫描你关心的一批期刊 + 主题检索 + 「谁引用了某个作者」，按你自己配置的研究方向打分，
生成中文速读卡片，直接落到 Obsidian（或任何 Markdown 目录）。

纯 Python 标准库实现：不需要数据库服务、不需要 Docker、不用常驻进程。
一个 `config.toml` + 一条命令 + 一个计划任务就够了。

```
   ① 期刊扫描 ─┐
   ② 主题检索 ─┼─→ 去重合并 ─→ 方向打分 ─→ 取前 N 篇 ─→ 摘要改写(可选) ─→ 卡片 / 日报
   ③ 引用追踪 ─┘
```

---

## 它解决什么问题

每天早上刷期刊网站，80% 的文章跟你无关；真正相关的那两三篇，常常藏在
你没盯的期刊里，或者以「引用了你在跟的那位作者」的形式出现。

litpush 替你做三件枯燥活：

| 你原本要做 | litpush 替你做 |
| --- | --- |
| 挨个翻 5～10 个期刊的新目录 | 按 ISSN 抓每个刊最近 N 天的新文章（窗口可逐刊配置） |
| 用关键词在 Google Scholar 里筛 | 正则 + 语境闸门（guard）打分，标题命中权重更高 |
| 看谁引用了某位作者 | 调 OpenAlex 的 `cites:` 过滤，精确到「引用了 TA 的哪一篇」 |
| 复制摘要、判断值不值得读 | 生成固定字段的卡片：元数据 / 研究方向 / 摘要全文 / 引用关系 |

---

## 输出长什么样

**卡片**（每篇一个 `.md`）：

```markdown
# 论文标题（居中）

## 💡 Meta Data
| Title / Journal / Authors / Pub. date / Tags / DOI / Attachment |

## 📜 研究背景 & 基础 & 目的
- 与你的方向：…
- 期刊/时间：…
- OpenAlex 关键词：…

## 📄 摘要全文
（摘要原文，可直接复制）

## 🔗 引用与关联
- OpenAlex 被引：3 次
- 引用关注学者工作：是 — 《…》(2018)
- DOI / 全文 PDF
```

**日报**（`output_dir/2026-11-10.md`；也可以追加到当天的 Obsidian 日记末尾）：

```markdown
## 📚 今日文献速递 · 11-10

> 扫描 343 篇，方向命中 67 篇，本次推送 10 篇（A 3 · B 7）。

### ⭐ A 级 · 建议精读

#### 1. 标题…
`A` · **JMLR** · 2026 · 评分 9.4 · 深度学习

> 一句话结论（配了大模型是中文改写；没配是"中文框架 + 原文摘录"）

- **特色**：…
- **与你**：…
- **值得读**：…
- **构型/工况**：…

[[2026 作者 - 标题|📄 精读卡片]] · [原文](https://doi.org/…)
```

---

## 快速开始

**要求**：Python 3.11+（用到标准库 `tomllib`）。Windows / macOS / Linux 均可。

```bash
git clone https://github.com/<your-name>/litpush.git
cd litpush
```

```powershell
.\litpush.cmd init          # 生成 config\config.toml（macOS/Linux: python3 run.py init）
notepad config\config.toml  # 改 output_dir、期刊 ISSN、你的关键词
.\litpush.cmd doctor        # 自检：配置 / 目录权限 / 网络 / 模型
.\litpush.cmd preview --explain   # 只看效果，不写任何文件
.\litpush.cmd run           # 正式跑一次
```

> 建议先去 <https://openalex.org/settings/api> 免费申请一个 OpenAlex API key 填进
> `openalex_api_key`。匿名请求的每日额度是按 IP 共享的，很容易 429。

---

## 配置要点

### 期刊

```toml
[[journals]]
name = "Journal of Machine Learning Research"
issn = "1532-4435"
tier = 1              # 1=顶刊 2=领域认可 3=扩展
lookback_days = 35    # 重要：按月出整期的刊要给 30~40 天，否则整批漏掉
```

### 研究方向（决定准不准）

```toml
[[topics]]
id = "machine-learning"
label = "机器学习"
weight = 3.6            # ≥4.0 视为重点方向，命中标题额外加分
priority = "core"       # core=命中即入候选；secondary=只加分不单独触发
guard = 'model|learning|neural|dataset'   # 语境闸门，挡掉同名不同义
patterns = [
  'machine[\s\-]*learning',
  'deep[\s\-]*learning',
  'large[\s\-]*language[\s\-]*model',
]
```

`patterns` 是正则，不会写也能改——三种写法覆盖 90% 的情况：

| 想匹配 | 写法 |
| --- | --- |
| 固定短语 | `'low-speed streaks'` |
| 中间可能有空格/连字符 | `'low[\s\-]*speed[\s\-]*streaks?'` |
| 几个词任意一个 | `'streak|streaks|streaky'` |

`guard` 是"语境闸门"：全文必须出现 guard 里的词，这个方向才计数。用来挡住
`streak camera`（条纹相机）、`phase transition`（相变）这类同词不同义。

### 追踪某个作者（可选）

```toml
[advisor]
enabled = true
name = "某位老师"
orcid = "0000-0002-1825-0097"   # 或直接填 openalex_id = "A1234567890"
```

命中文章会单独分组「🔔 引用 XX 工作的新文献」，并写明引用了哪一篇。

### 大模型（可选）

不配也能用（规则版：中文框架 + 原文摘录）。配了之后摘要会被改写成更连贯的中文：

```toml
[llm]
enabled = true
base_url = "https://api.deepseek.com/v1"   # 任何 OpenAI 兼容接口
model = "deepseek-chat"
api_key_env = "LITPUSH_LLM_API_KEY"
```

key 放环境变量（`SetEnvironmentVariable(..., 'User')`），不要写进配置文件。

---

## 每天自动跑

```powershell
# Windows：每天 08:30，错过自动补跑，失败重试 3 次
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -Time 08:30
```

macOS / Linux 用 cron：

```cron
30 8 * * * cd /path/to/litpush && /usr/bin/python3 run.py run >> logs/cron.log 2>&1
```

---

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `litpush.cmd run` | 跑一次并写文件 |
| `litpush.cmd preview --explain` | 不写盘，打印每篇的打分明细（调参必备） |
| `litpush.cmd doctor` | 自检：配置、目录权限、网络、模型连通性 |
| `litpush.cmd stats` | 历史运行与累计推送统计 |
| `litpush.cmd refill --doi 10.xxxx/yyy --abstract-file a.txt` | 补/重做某一篇（出版社没给摘要时） |
| `litpush.cmd recard` | 按当前模板重排已有卡片 |
| `litpush.cmd reset` | 清空推送记录，让窗口内文献重新推一遍 |
| `litpush.cmd advisor-refresh` | 强制刷新被追踪作者的著作列表 |

macOS / Linux 把 `litpush.cmd` 换成 `python3 run.py` 即可。

---

## 数据从哪来

| 来源 | 用途 | 备注 |
| --- | --- | --- |
| [OpenAlex](https://openalex.org) | 期刊目录、主题检索、引用关系、摘要 | 免费；填 key 后 10 万次/天 |
| [Crossref](https://www.crossref.org) | 摘要兜底 | OpenAlex 没摘要时再试一次 |
| 本地 `data/abstracts.json` | 手动补录摘要 | `refill` 写入，优先级最高 |

所有数据都在本地：`data/litpush.sqlite3`（去重与运行历史）、`data/cache/`（HTTP 缓存）。
不会向第三方上传你的任何信息。

---

## 目录结构

```
litpush/
├── config/config.example.toml   配置模板（init 复制成 config.toml）
├── litpush/                     源码
│   ├── openalex.py              期刊 / 检索 / 引用查询
│   ├── crossref.py              摘要兜底
│   ├── classify.py              方向匹配 + 打分
│   ├── summarize.py             卡片文本生成（规则版）
│   ├── llm.py                   可选：大模型改写
│   ├── render.py                Markdown 渲染
│   ├── journal.py               写进 Obsidian 日记
│   ├── store.py                 去重库（SQLite）
│   └── pipeline.py              主流程
├── scripts/                     计划任务脚本（Windows）
├── data/                        运行期数据（已 gitignore）
└── logs/                        日志（已 gitignore）
```

---

## 已知限制

1. 只读**标题 + 摘要 + 元数据**，不下载 PDF、不解析全文；摘要缺失时卡片会退化。
2. OpenAlex 的作者实体有合并误差，引用追踪偶尔会混入同名的他人文章；卡片里写了
   「引用了哪一篇」，便于判断。
3. `lookback_days` 必须匹配出版节奏：月刊给 14 天会漏。
4. 预印本走 OpenAlex 的 arXiv 记录，覆盖面略窄于 arXiv 全站。

---

## License

MIT，见 [LICENSE](LICENSE)。

---

## 个人记录
依旧vibe coding，及时整理最新文献，结合Obsidian知识管理，可迁移到不同领域
