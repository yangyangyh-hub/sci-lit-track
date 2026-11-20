---
name: sci-lit-track
description: "可配置的 SCI 文献追踪与进展报告：为**任意研究方向**（由用户给出方向与检索词）从 PubMed、Europe PMC、OpenAlex、Crossref 抓取近 2–4 周正式发表论文，按期刊门槛、主题相关性、内容倾向排序，每方向取约 10 篇，输出「精选导读 + 各方向」结构的中文进展报告。触发词：文献追踪、SCI 文献、文献进展、文献周报、本周文献、追踪文献、找最近的文章、跑一期文献、literature radar、weekly literature。"
description_zh: "可配置方向的 SCI 文献追踪 → 筛选排序 → 中文进展报告"
description_en: "Configurable multi-source literature radar → Chinese progress report"
version: 2.1.0
agent_created: true
---

# SCI 文献追踪与进展报告

把"最近这个领域有什么新文章"变成一份可直接发群里/进笔记库的中文进展报告。

**适用面**：方向、词表、期刊门槛、产出篇数**全部由配置决定**，不绑定某个学科。
内置一份「动物科学五方向」实例（`config/directions.animal-science.json`）供参考，可直接改成自己的领域。

**边界**：本技能负责 发现 → 筛选 → 排序 → 成稿 → 投递。
单篇论文做图文/公众号 用 `sci-wechat-daily`；查引文/严格他引 用 `nature-academic-search`；拿不到 OA 全文 用 `nature-downloader`。

---

## 0. 开工前必须确认的四件事（缺就问）

| 项 | 说明 | 默认 |
|---|---|---|
| **研究方向与词表** | 用户关注哪几个方向？每个方向的中英文检索词 | 无默认，必须问 |
| **期刊门槛** | 三选一：分区/IF 门槛、核心刊清单、或不做门槛 | `zone_if`（1区/2区 或 IF>4） |
| **分区表 / IF 表** | 有就填路径，没有就留空并改门槛模式 | 空 |
| **报告投递目录** | 报告落哪 | 项目内 `reports/`，或 `SCI_RADAR_OUTDIR` |

**换领域时只改配置，不改代码。** 三步：

1. 列出 3–6 个方向，每方向给 5–10 个英文检索词（生医类再加中文词）；
2. 定门槛：有分区表就填表 + `zone_if`；没有就写核心刊清单 + `journal_list`；都没有就 `none`；
3. 定黑名单（可选）：明显不想收的出版方/期刊，先读 5 篇候选再决定要不要加。

---

## 1. 环境

```bash
# 需要一个带 openpyxl 的 Python（仅读 xlsx 用；无表场景可不装）
python -m venv <venv路径>
<venv>/Scripts/python.exe -m pip install openpyxl
```

脚本**只用标准库 + openpyxl**，无其他依赖。个人信息（邮箱）通过 `LIT_MAILTO` 环境变量传入，脚本内无任何账号。

```bash
SK=<本技能目录>
$PY "$SK/scripts/funnel.py" check --config <项目>/config/directions.json
```

`check` 打印四个数据源连通性 + 两张表的索引键数。任一 ✗ 先修网络。

**网络**：PubMed 在部分网络环境直连会被 TLS 重置，脚本按域名自动路由到本机代理（惰性探测 50439/50440，失败回退直连）。
Europe PMC / OpenAlex / Crossref 一般直连可用。**不要把代理端口写死进脚本。**

---

## 2. 六步流程

```bash
# ① 配置
cp "$SK/config/directions.example.json" <项目>/config/directions.json   # 改成自己的方向
#   已有畜牧五方向需求时用：cp "$SK/config/directions.animal-science.json" ...

# ② 抓取 + 去重 + 联表 + 过滤 + 排序 + 配额
$PY "$SK/scripts/funnel.py" run --config <项目>/config/directions.json --out <项目>/runs/<日期>_<标签>
#    产物：result.json（唯一数据源）、report_skeleton.md（占位骨架）
#    只想试一个方向：加 --only 1

# ③ DOI 核验（★ 必做，不核验不许进报告）
LIT_MAILTO=you@example.com $PY "$SK/scripts/verify_doi.py" --run <运行目录>

# ④ 撰写内容：每篇写中文译题 + 摘要整段中译（格式见 references/内容撰写指南.md）
#    产物：<项目>/content/d1.json … dN.json（字段：doi / title_cn / abstract_cn）
#    摘要原文取 result.json 的 final.<方向>.picked[i].abstract，逐句译，不拆四段

# ⑤ 生成报告
$PY "$SK/scripts/build_report.py" --run <运行目录> --content <项目>/content
#    默认写入 $SCI_RADAR_OUTDIR 或 ./reports/

# ⑥ 交付：present_files 展示报告 + 一句话说明本期覆盖与不足
```

**耗时参考**：② 3–8 分钟（可后台）；③ 约 1 分钟；④ 最慢，50 篇需逐篇读摘要翻译。

---

## 3. 口径速查（全文见 `references/口径说明.md`）

| 维度 | 口径 |
|---|---|
| 期刊门槛 | `zone_if`（分区 ∈ 1区/2区 **或** IF>4）｜`journal_list`｜`none`，**门槛一经确定不因数量不足而降低** |
| 时间窗 | 优先近 **14 天**；不足则放宽相关性并延到 **28 天**补位；仍不足**如实少收** |
| 配额 | 每方向 10 篇（可配） |
| 必排 | 预印本、社论/评论/勘误/撤稿、非目标对象（按方向配置）。**摘要长度不设门槛**（`min_abstract_chars` 默认 0） |
| 排序 | 相关性 → 内容倾向（机制/方法 +1、纯临床/综述 −1）→ 分区档位 → 用户优先项 → 日期 |
| 单归属 | 同一 DOI 只归一个方向（取相关性最高） |
| 报告版式 | `# <日期> 文献进展` → `一、精选导读` → `二~N` 各方向；每篇 = 中文译题 + 英文原题 + 元数据 + 链接 + **摘要整段中译** |

---

## 4. 内容撰写硬规则

1. **标题用中文译题**；正文**首条**是**英文原题全文**，再跟元数据、链接，**末尾是摘要整段中译**。
2. **每篇只写摘要中文译文一段**（`abstract_cn`），**不再拆背景/方法/主要结果/结论四段**；
   原文怎么排就怎么译，不重排、不压缩成要点。
3. 只依据正式摘要：摘要没写的不补，摘要截断的回 `result.json` 取全文。
   确实无摘要的写「**原刊未提供摘要**」，**禁止推测、禁止替作者补意义**。
4. 数字、样本量、基因/菌种名照原文；术语首次出现用「中文（英文）」；保留全部限定语（`仅在…中`、`不影响…`）。
5. **每篇必须有直达链接**（doi.org → PubMed → PMC → Europe PMC）。
6. 不写"具有重要意义""提供了新思路"式空话。

---

## 5. 已知坑（实测）

| 坑 | 现象 | 处理 |
|---|---|---|
| **宽泛 except 吞掉 NameError** | 变量名写错 → 记录被判"超期" → 所有方向 0 篇且不报错 | 改完选文逻辑先用 `--only 1` 看数字是否合理 |
| **DOI 张冠李戴** | 部分 DOI 指向别的论文（多源合并时题名与 DOI 不同源） | 强制跑 `verify_doi.py`；查不到真 DOI 就留空 |
| **期刊子串误匹配** | `Cell` 命中 `Frontiers in Cell...` | 期刊匹配用精确匹配，`Cell*` 才表示前缀 |
| **`\binsect\b` 匹配不到 `insects`** | 昆虫类文献漏检 | 词表用 `insects?`；必要时把该刊列入 `journals` 黑名单 |
| **`guinea pig` 当成猪** | 豚鼠研究被判"猪相关" | 物种正则加否定环视 `(?<!guinea )pig` |
| **OpenAlex `created_date` 不可用** | 429/超时 | 降级 `from_publication_date` 并在记录上标注 |
| **Europe PMC `fullTextPDF` 常 404** | 新入库记录拿不到 PDF | 不作主路径；取全文用 DOI/PMC/Unpaywall 兜底 |
| **分区表查无此刊** | 新刊、改名刊被静默丢弃 | 单独计数，不与其他排除原因混算；或用 `journal_list` 兜底 |
| **按摘要长度筛文献** | 短摘要期刊被整批误删（曾有杂志要求摘要 <400 字符） | 默认不限制；要限制用 `min_abstract_chars` |
| **报告出现两份副本** | 版本分叉 | 只写投递目录，项目内不留副本 |

---

## 6. 文件

```
sci-lit-track/
├── SKILL.md
├── config/
│   ├── directions.example.json        # 通用模板（两方向示例 + 三种门槛模式）
│   └── directions.animal-science.json # 动物科学五方向实例（词表/黑名单/门槛）
├── scripts/
│   ├── funnel.py         # check / run（抓取→筛选→排序→配额）
│   ├── verify_doi.py     # DOI 反查核验（LIT_MAILTO）
│   └── build_report.py   # result.json + content/*.json → 报告
├── references/
│   ├── 口径说明.md        # 完整口径（唯一权威，改动须升版本）
│   └── 内容撰写指南.md    # 摘要中译写法 + content JSON 格式 + 常见错误
├── assets/content_template.json
└── tests/                # 黑名单 / 物种判定回归（改规则就跑）
```

**回归测试**：

```bash
$PY "$SK/tests/test_banned.py"     # 出版方/刊名黑名单
$PY "$SK/tests/test_species.py"    # 对象（物种）判定
```

测试默认读 `config/directions.animal-science.json`；依赖分区表时若表不存在会自动跳过相关用例。
