# sci-lit-track

可配置的 SCI 文献追踪与中文进展报告生成器。

给定若干研究方向与检索词，从 **PubMed / Europe PMC / OpenAlex / Crossref** 抓取近 2–4 周正式发表的论文，
按期刊门槛、主题相关性、内容倾向排序，每个方向取约 10 篇，输出「精选导读 + 各方向」结构的中文进展报告。

不绑定学科：方向、词表、期刊门槛、产出篇数全部由配置决定。仓库内附一份动物科学五方向实例。

---

## 特点

- **多源合并 + DOI 归一去重**：任一源挂掉自动降级，不中断整轮
- **期刊门槛可切换**：分区/影响因子（`zone_if`）、核心刊清单（`journal_list`）、或不做门槛（`none`）
- **分级过滤、逐级计数**：写进摘要前的硬排除（预印本、社论、非目标对象）与期刊门槛分开统计，不静默丢弃
- **两阶段时间窗**：优先近 14 天，不足才放宽相关性并延到 28 天，期刊门槛不动
- **DOI 反查核验**：Crossref 比对题名，纠正张冠李戴的 DOI，查不到就留空而非猜测
- **报告只需结果**：精选导读 + 各方向，中文译题作标题、正文首条英文原题、每篇带直达链接
- 纯标准库 + `openpyxl`，无其他依赖；不含任何账号、密钥或个人信息

## 安装

```bash
git clone https://github.com/yangyangyh-hub/sci-lit-track.git
cd sci-lit-track

python -m venv .venv
.venv/Scripts/python.exe -m pip install openpyxl      # Windows
# source .venv/bin/activate && pip install openpyxl    # macOS / Linux
```

## 快速开始

```bash
# 1) 配置：复制模板，把方向换成你关注的领域
cp config/directions.example.json config/directions.json

# 2) 自检：四个数据源 + 分区表
python scripts/funnel.py check --config config/directions.json

# 3) 抓取 → 去重 → 联表 → 过滤 → 排序 → 配额
python scripts/funnel.py run --config config/directions.json --out runs/2026-10-23
#    只想试一个方向：加 --only 1

# 4) DOI 核验（必做）
LIT_MAILTO=you@example.com python scripts/verify_doi.py --run runs/2026-10-23

# 5) 撰写内容（每篇中文译题 + 四段概述），格式见 references/内容撰写指南.md
#    产物：content/d1.json … dN.json

# 6) 生成报告
SCI_RADAR_OUTDIR=./reports python scripts/build_report.py \
    --run runs/2026-10-23 --content content
```

## 配置要点

| 字段 | 说明 |
|---|---|
| `directions[]` | 每个方向：`name`、`query`（宽检索式）、`core`（判相关性核心词）、可选 `require_any` / `exclude_terms` / `exclude_journal` |
| `gate_default.mode` | `zone_if`（默认）｜`journal_list`｜`none`；可被单个方向的 `gate` 覆盖 |
| `paths.zone_xlsx` / `if_xlsx` | 分区表与影响因子表；留空则跳过后不再做期刊门槛，可改用 `journal_list` |
| `exclude_publishers` | 出版方硬排除（出版机构 / 刊名 / 刊名前缀），**最高优先级，读摘要前执行** |
| `content_preference` | 排序用的内容倾向：机制/方法类加分、纯临床/纯综述减分 |
| `recent_days` / `window2_days` | 14 / 28：优先窗口与补位窗口 |
| `min_abstract_chars` | 摘要最小字符数，**默认 0 = 不限** |

完整口径见 [`references/口径说明.md`](references/口径说明.md)。

## 目录

```
sci-lit-track/
├── config/           # 通用模板 + 动物科学实例
├── scripts/          # funnel.py / verify_doi.py / build_report.py
├── references/       # 口径说明、内容撰写指南
├── assets/           # 内容 JSON 模板
└── tests/            # 出版方黑名单、对象判定回归测试
```

## 环境变量

| 变量 | 用途 | 默认 |
|---|---|---|
| `LIT_MAILTO` | 调 Crossref / Unpaywall 的礼貌池邮箱 | `anonymous@example.com` |
| `SCI_RADAR_OUTDIR` | 报告输出目录 | `./reports` |

## 测试

```bash
python tests/test_banned.py     # 出版方 / 刊名黑名单
python tests/test_species.py    # 对象（物种）判定
```

## 已知限制

- 单源单方向有结果上限（PubMed 300 / Europe PMC 200 / OpenAlex 100 / Crossref 100），顶格时存在召回损失
- OpenAlex 的 `created_date` 过滤不可靠，降级用 `publication_date` 并在记录上标注
- 部分网络环境 PubMed 需代理；脚本按域名自动路由，代理不可用时回退直连
- 报告四段概述依据摘要生成，摘要未写明的信息标注「摘要未提供」，不做推测

## License

MIT
