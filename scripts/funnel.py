#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SCI 文献追踪 · 统一漏斗
========================
源(PubMed/EPMC/OpenAlex/Crossref) → 归一 + DOI 去重 → 分区/IF 联表
→ 物种判定 → 硬排除 → 相关性 → 门槛 → 排序 → 单归属 → 配额 → 运行目录

设计原则（对应需求规格 v1.1）：
1. **排序与单归属只在本文件定义一次**，不再让多个脚本各自排序。
2. **过滤分「硬排除」与「门槛」两级**，每一级都计数并写进漏斗报告，不允许静默丢弃。
3. **未判定 ≠ 排除**：判不出物种时按口径推断——无活体信号记「纯细胞(无活体)」，否则推定「小鼠(推定)」，并打标 `species_inferred`。
4. 代理**不写死**：默认直连；探测到 PORT 可用才用，失败自动回退。
5. 时间窗口用**入库日**（PubMed EDAT / EPMC FIRST_IDATE / Crossref created）；
   OpenAlex 的 created_date 过滤实测 429+超时不可用 → 降级用 publication_date 并在记录上标注。

用法：
  python funnel.py run   --config config/directions.json [--days 14] [--out runs/20261023]
  python funnel.py check --config config/directions.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

UA = "lit-track/1.1 (+local; mailto:{mailto})"
PROXY_CANDIDATES = ["http://127.0.0.1:50439", "socks5h://127.0.0.1:50440"]
# 需要代理的域名：NCBI 直连会被 TLS 重置（实测 UNEXPECTED_EOF）
PROXY_HOSTS = ("eutils.ncbi.nlm.nih.gov", "pubmed.ncbi.nlm.nih.gov", "ftp.ncbi.nlm.nih.gov")
_OPENER = None
_OPENER_PROXY = None
_PROXY_ALIVE = None


def make_opener(proxy: str | None):
    h = urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
    return urllib.request.build_opener(h)


def _proxy_opener():
    """惰性探测可用代理；不可用返回 None（不写死端口）。"""
    global _OPENER_PROXY, _PROXY_ALIVE
    if _PROXY_ALIVE is not None:
        return _OPENER_PROXY
    for p in PROXY_CANDIDATES:
        try:
            op = make_opener(p)
            with op.open(urllib.request.Request("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi",
                                                headers={"User-Agent": "lit-track-probe"}), timeout=12) as r:
                if r.status < 500:
                    _OPENER_PROXY, _PROXY_ALIVE = op, p
                    return op
        except Exception:  # noqa: BLE001
            continue
    _PROXY_ALIVE = ""
    return None


# ------------------------------------------------------------------ HTTP
def _curl(url: str, timeout: int) -> bytes:
    import subprocess
    p = subprocess.run(["curl.exe", "-sS", "-L", "--noproxy", "*", "-m", str(timeout),
                        "-A", UA.format(mailto="lit-track"), "-o", "-", url], capture_output=True)
    if p.returncode != 0:
        raise RuntimeError((p.stderr.decode("utf-8", "replace") or f"curl rc={p.returncode}").strip()[:150])
    return p.stdout


def http_get(url: str, timeout: int = 45, retries: int = 2) -> bytes:
    host = urllib.parse.urlparse(url).netloc
    need_proxy = any(h in host for h in PROXY_HOSTS)
    prefer = _proxy_opener() if need_proxy else _OPENER
    alt = _OPENER if need_proxy else _proxy_opener()
    last = None
    for i in range(retries + 1):
        for op in (prefer, alt):
            if op is None:
                continue
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA.format(mailto="lit-track")})
                with op.open(req, timeout=timeout) as r:
                    return r.read()
            except Exception as e:  # noqa: BLE001
                last = e
                if "CERTIFICATE_VERIFY_FAILED" in str(e):
                    try:
                        return _curl(url, timeout)
                    except Exception as e2:  # noqa: BLE001
                        last = e2
        if i < retries:
            time.sleep(2.0 * (i + 1))
    raise RuntimeError(f"{type(last).__name__}: {str(last)[:140]} :: {url[:110]}")


def get_json(url: str, **kw):
    return json.loads(http_get(url, **kw).decode("utf-8", "replace"))


# ------------------------------------------------------------------ 工具
def iso(d: str) -> str:
    return (d or "").replace("/", "-").strip()


def norm_issn(s: str) -> str:
    return re.sub(r"[^0-9X]", "", (s or "").upper())


def key_of(r: dict) -> str:
    if r.get("doi"):
        return "doi:" + r["doi"]
    if r.get("pmid"):
        return "pmid:" + r["pmid"]
    return "title:" + re.sub(r"[^a-z0-9]+", "", (r.get("title") or "").lower())[:70]


# ------------------------------------------------------------------ 源：PubMed
def pubmed_fetch(dirq: str, d0: str, d1: str, retmax: int = 300) -> list[dict]:
    term = f'({dirq}) AND ("{d0.replace("-", "/")}"[EDAT] : "{d1.replace("-", "/")}"[EDAT])'
    u = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?"
         + urllib.parse.urlencode({"db": "pubmed", "term": term, "retmax": retmax, "retmode": "json"}))
    d = get_json(u)
    ids = (d.get("esearchresult") or {}).get("idlist") or []
    if not ids:
        return []
    out = []
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        u2 = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?"
              + urllib.parse.urlencode({"db": "pubmed", "id": ",".join(chunk), "retmode": "xml"}))
        root = ET.fromstring(http_get(u2, timeout=90))
        for art in root.findall(".//PubmedArticle"):
            out.append(_parse_pubmed(art))
        time.sleep(0.4)
    return [x for x in out if x]


def _txt(n, path) -> str:
    e = n.find(path)
    return "".join(e.itertext()).strip() if e is not None else ""


def _parse_pubmed(art) -> dict | None:
    a = art.find(".//MedlineCitation/Article")
    if a is None:
        return None
    title = _txt(a, "ArticleTitle").rstrip(".")
    abst = " ".join(_txt(x, ".") for x in a.findall(".//Abstract/AbstractText"))
    jr = a.find(".//Journal")
    journal = _txt(jr, "Title") if jr is not None else ""
    issns = [norm_issn(_txt(x, ".")) for x in (jr.findall("ISSN") if jr is not None else [])]
    doi = ""
    for aid in art.findall(".//ArticleIdList/ArticleId"):
        if (aid.get("IdType") or "").lower() == "doi":
            doi = (aid.text or "").lower()
    pmid = _txt(art, ".//MedlineCitation/PMID")
    pubtypes = [(_txt(x, ".")) for x in a.findall(".//PublicationTypeList/PublicationType")]
    # 首次在线日期：ArticleDate 优先，其次 ELocationID 无日期，再退回 PubDate
    ad = a.find(".//ArticleDate")
    online = ""
    if ad is not None:
        online = f"{_txt(ad, 'Year')}-{int(_txt(ad, 'Month') or 0):02d}-{int(_txt(ad, 'Day') or 0):02d}"
    if not re.match(r"\d{4}-\d{2}-\d{2}$", online):
        pd = a.find(".//Journal/JournalIssue/PubDate")
        y = _txt(pd, "Year") if pd is not None else ""
        online = f"{y}-01-01" if y.isdigit() else ""
    return {"doi": doi, "pmid": pmid, "title": title, "abstract": abst, "journal": journal,
            "issns": [i for i in issns if i], "date_online": online, "date_window": online,
            "pubtypes": pubtypes, "src": "PubMed", "type": "journal-article"}


# ------------------------------------------------------------------ 源：Europe PMC
def epmc_fetch(dirq: str, d0: str, d1: str, page: int = 200) -> list[dict]:
    q = f"({dirq}) AND (FIRST_IDATE:[{d0} TO {d1}]) AND SRC:MED"
    u = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
         + urllib.parse.urlencode({"query": q, "format": "json", "pageSize": page, "resultType": "core"}))
    d = get_json(u, timeout=60)
    out = []
    for it in (d.get("resultList") or {}).get("result") or []:
        ji = it.get("journalInfo") or {}
        j = ji.get("journal") or {}
        out.append({
            "doi": (it.get("doi") or "").lower(), "pmid": it.get("pmid") or "",
            "title": (it.get("title") or "").rstrip("."),
            "abstract": it.get("abstractText") or "",
            "journal": j.get("title") or "",
            "issns": [x for x in [norm_issn(j.get("issn")), norm_issn(j.get("electronic_issn"))] if x],
            "date_online": iso(it.get("firstPublicationDate")),
            "date_window": iso(it.get("firstIndexDate") or it.get("firstPublicationDate")),
            "pubtypes": ((it.get("pubTypeList") or {}).get("pubType") or []),
            "src": "EuropePMC", "type": "journal-article",
        })
    return out


# ------------------------------------------------------------------ 源：OpenAlex
def openalex_fetch(dirq: str, d0: str, d1: str, n: int = 100) -> list[dict]:
    clean = re.sub(r'["()]', " ", dirq)
    u = ("https://api.openalex.org/works?" + urllib.parse.urlencode({
        "search": " ".join(clean.split())[:400],
        "filter": f"from_publication_date:{d0},to_publication_date:{d1}",
        "per-page": n, "mailto": MAILTO}))
    d = get_json(u, timeout=60)
    out = []
    for w in d.get("results") or []:
        src = ((w.get("primary_location") or {}).get("source") or {})
        out.append({
            "doi": (w.get("doi") or "").lower().replace("https://doi.org/", ""),
            "pmid": "", "title": (w.get("display_name") or "").rstrip("."),
            "abstract": _inv_text(w.get("abstract_inverted_index")),
            "journal": src.get("display_name") or "",
            "issns": [norm_issn(x) for x in ([src.get("issn_l")] + (src.get("issn") or [])) if norm_issn(x)],
            "date_online": w.get("publication_date") or "",
            "date_window": w.get("publication_date") or "",
            "pubtypes": [], "src": "OpenAlex", "type": w.get("type") or "",
            "date_source": "publication_date(降级：created_date 过滤不可用)",
        })
    return out


def _inv_text(inv) -> str:
    if not inv:
        return ""
    pos = {}
    for w, idxs in inv.items():
        for i in idxs:
            pos[i] = w
    return " ".join(pos[i] for i in sorted(pos))


# ------------------------------------------------------------------ 源：Crossref
def crossref_fetch(dirq: str, d0: str, d1: str, n: int = 100) -> list[dict]:
    clean = re.sub(r'["()]', " ", dirq)
    u = ("https://api.crossref.org/works?" + urllib.parse.urlencode({
        "query.bibliographic": " ".join(clean.split())[:400],
        "filter": f"from-created-date:{d0},until-created-date:{d1},type:journal-article",
        "rows": n, "select": "DOI,title,ISSN,container-title,abstract,created,type",
        "mailto": MAILTO}))
    d = get_json(u, timeout=60)
    out = []
    for it in (d.get("message") or {}).get("items") or []:
        cr = (it.get("created") or {}).get("date-parts") or [[None]]
        p = cr[0]
        dt = f"{p[0]}-{int(p[1] if len(p) > 1 else 1):02d}-{int(p[2] if len(p) > 2 else 1):02d}" if p and p[0] else ""
        out.append({
            "doi": (it.get("DOI") or "").lower(), "pmid": "",
            "title": ((it.get("title") or [""])[0] or "").rstrip("."),
            "abstract": re.sub(r"<[^>]+>", " ", it.get("abstract") or ""),
            "journal": ((it.get("container-title") or [""])[0] or ""),
            "issns": [norm_issn(x) for x in (it.get("ISSN") or []) if norm_issn(x)],
            "date_online": dt, "date_window": dt, "pubtypes": [], "src": "Crossref",
            "type": it.get("type") or "", "date_source": "created(注册日)",
        })
    return out


# ------------------------------------------------------------------ 分区 / IF 联表
ZONE, ZCAT, IFV = {}, {}, {}
PUB, PUB_NAME = {}, {}   # ISSN→出版机构 / 归一刊名→出版机构

# ------------------------------------------------------------------ 出版商黑名单（最高优先级）
def _norm_title(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split())


def resolve_publisher(r: dict) -> str:
    for k in r.get("issns") or []:
        if k in PUB:
            return PUB[k]
    key = _norm_title(r.get("journal") or "")
    if key:
        if key in PUB_NAME:
            return PUB_NAME[key]
        # 刊名归一后退化匹配：'animals an open access journal from mdpi' → 'animals'
        for name, pub in PUB_NAME.items():
            if key.startswith(name) and len(name) >= 5:
                return pub
    return ""


def banned_reason(r: dict, bcfg: dict) -> str | None:
    """出版层面硬排除。在任何摘要处理之前调用。"""
    pubs = {p.upper() for p in bcfg.get("publishers", [])}
    pub = resolve_publisher(r)
    r["publisher"] = pub
    if pub and pub.upper() in pubs:
        return f"出版商黑名单:{pub}"
    jn = _norm_title(r.get("journal") or "")
    for j in bcfg.get("journals", []):
        if jn == _norm_title(j):
            return f"刊名黑名单:{j}"
    for p in bcfg.get("journal_prefixes", []):
        if jn.startswith(_norm_title(p) + " "):
            return f"刊名黑名单:{p}*"
    for j in bcfg.get("known_titles", []):
        if jn == _norm_title(j):
            return "刊名黑名单(已知MDPI刊)"
    return None


def load_tables(cfg):
    """读取分区 / IF 表。路径为空或文件不存在时跳过，不抛异常（便于无表场景）。"""
    import openpyxl

    def rows_of(path):
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.worksheets[0]
        it = ws.iter_rows(min_row=1, max_row=1, values_only=True)
        hdr = [h for h in next(it)]
        for r in ws.iter_rows(min_row=2, values_only=True):
            if r and r[0]:
                yield dict(zip(hdr, r))
        wb.close()

    if not (cfg["paths"].get("zone_xlsx") and os.path.exists(cfg["paths"]["zone_xlsx"])):
        print("      [warn] 未提供分区表，跳过分区联表", flush=True)
        zone_src = []
    else:
        zone_src = rows_of(cfg["paths"]["zone_xlsx"])
    for x in zone_src:
        z = (x.get("大类新锐分区") or "").strip()
        c = (x.get("大类中文名") or "").strip()
        pub = (x.get("出版机构") or "").strip()
        jn = (x.get("刊名") or "").strip()
        for k in {norm_issn(x.get("ISSN")), norm_issn(x.get("EISSN"))}:
            if k:
                ZONE.setdefault(k, z)
                ZCAT.setdefault(k, c)
                if pub:
                    PUB.setdefault(k, pub)
        if jn and pub:
            PUB_NAME.setdefault(_norm_title(jn), pub)
    if not (cfg["paths"].get("if_xlsx") and os.path.exists(cfg["paths"]["if_xlsx"])):
        print("      [warn] 未提供 IF 表，跳过 IF 联表", flush=True)
        if_src = []
    else:
        if_src = rows_of(cfg["paths"]["if_xlsx"])
    for x in if_src:
        try:
            v = float(x.get("IF(2025)"))
        except (TypeError, ValueError):
            continue
        for k in {norm_issn(x.get("ISSN")), norm_issn(x.get("EISSN"))}:
            if k:
                IFV.setdefault(k, v)
    return len(ZONE), len(IFV)


def join_metrics(r: dict):
    z = c = None
    for k in r.get("issns") or []:
        if k in ZONE:
            z = ZONE[k]
            c = ZCAT.get(k)
            break
    f = None
    for k in r.get("issns") or []:
        if k in IFV:
            f = IFV[k]
            break
    r["zone"], r["zone_cat"], r["impact"] = z, c, f
    r["zone_missing"] = z is None


# ------------------------------------------------------------------ 物种 / 硬排除
SPECIES = [
    ("猪", r"(?<!guinea )\b(pig|pigs|swine|porcine|piglet|piglets|boar|sow|gilts?|sus scrofa)\b"),
    ("牛", r"\b(cattle|bovine|cow|cows|beef|calf|calves|heifer|bull|yak|yaks|buffalo|bison)\b"),
    ("羊", r"\b(sheep|ovine|lamb|lambs|goat|goats|caprine|ewe)\b"),
    ("家禽", r"\b(chicken|chickens|poultry|broiler|broilers|layer|hens?|duck|ducks|turkey|avian)\b"),
    ("小鼠", r"\b(mice|mouse|murine|mus musculus|c57bl)\b"),
    ("大鼠", r"\b(rat|rats|sprague-dawley|wistar)\b"),
    ("人", r"\b(human|humans|patient|patients|participants|volunteers|subjects|cohort)\b"),
    ("其他动物", r"\b(rabbit|rabbits|horse|equine|dog|dogs|canine|zebrafish|monkey|macaque|guinea pig)\b"),
]
SP_ORDER = ["猪", "牛", "羊", "家禽", "小鼠", "大鼠", "其他动物", "人", "小鼠(推定)", "纯细胞(无活体)"]
LIVESTOCK = {"猪", "牛", "羊", "家禽"}
NON_TARGET = (r"\b(melon|melon|cucumber|maize|corn|wheat|barley|ryegrass|soybean|rice|tomato|potato|"
              r"arabidopsis|plant|crop|crops|seedling|seedlings|leaf|leaves|grape|apple|citrus|strawberry|"
              r"banana|pepper|cabbage|lettuce|kernel|cultivar|germplasm line|shrimp|prawn|crab|portunus|"
              r"tilapia|salmon|trout|carassius|drosophila|aphid|insects?|aquacultur|mosquito|nematode|caenorhabditis|"
              r"yeast|saccharomyces|foliar|agronomic)\b")
STRONG_ANIMAL = r"\b(piglet|piglets|weaned pig|finishing pig|sus scrofa|porcine|c57bl/6|mouse model|cattle|broiler)\b"


CELL_HINTS = (r"\b(in vitro|ex vivo|cell line|cell lines|cultured cells|primary cells|cultured|culture medium|"
              r"hek293|hek-293|hela|a549|cho cells|ipsc|ips cells|esc|organoid|organoids|transfect|transfection|"
              r"transfected|fibroblast|fibroblasts|myoblast|myoblasts|c2c12|3t3-l1|3 t3 l1|raw264|raw 264|"
              r"ipec-j2|ipec j2|porcine alveolar macrophage|monolayer|passaged|subcultured)\b")
LIVE_HINTS = (r"\b(in vivo|animal experiment|animal trial|feeding trial|fed with|were fed|slaughter|"
              r"carcass|euthanized|housed|weaned|fattening|growth trial|randomly assigned)\b")


def detect_species(r) -> list[str]:
    """物种判定。判不出时不记「待人工」，按下述推断规则打标：

    - 命中活体/饲养/屠宰类信号，或摘要未提细胞实验 → 推定「小鼠」
    - 只有细胞/体外信号、无活体信号 → 「纯细胞(无活体)」
    """
    t = ((r.get("title") or "") + " " + (r.get("abstract") or "")).lower()
    got = [n for n, p in SPECIES if re.search(p, t)]
    if got:
        return got
    has_cell = bool(re.search(CELL_HINTS, t))
    has_live = bool(re.search(LIVE_HINTS, t))
    r["species_inferred"] = True
    if has_cell and not has_live:
        return ["纯细胞(无活体)"]
    return ["小鼠(推定)"]


def hard_exclude(r, cfg, d=None) -> str | None:
    """返回排除原因；None = 通过。所有原因都会进漏斗统计。"""
    if r.get("type") in ("posted-content", "preprint"):
        return "预印本"
    pt = " ".join(r.get("pubtypes") or []).lower()
    for bad in ("editorial", "comment", "erratum", "retraction", "letter", "news"):
        if bad in pt:
            return f"文献类型:{bad}"
    # 期刊名本身即非目标学科（摘要里不会出现刊名，必须单独判）
    jn = (r.get("journal") or "").lower()
    if jn and re.search(NON_TARGET, jn):
        return "非目标生物(刊名：植物/水产等)"
    if d and d.get("exclude_journal"):
        if re.search(d["exclude_journal"], jn):
            return "非目标学科(刊名黑名单)"
    minlen = int(cfg.get("min_abstract_chars") or 0)   # 0 = 不限长度（有些期刊摘要本身就短）
    if minlen and len(r.get("abstract") or "") < minlen:
        return f"摘要过短(<{minlen}字符)"
    t = ((r.get("title") or "") + " " + (r.get("abstract") or "")).lower()
    if re.search(NON_TARGET, t) and not re.search(STRONG_ANIMAL, t):
        return "非目标生物(植物/水产/昆虫)"
    if d and d.get("require_any"):
        if not any(k in t for k in d["require_any"]):
            return "未命中方向强词"
    # 方向级排除词：如方向 1 排除植物/病毒/细菌基因组（有强畜禽信号时放行）
    if d and d.get("exclude_terms"):
        if re.search(d["exclude_terms"], t) and not re.search(STRONG_ANIMAL, t):
            return "方向排除词(非动物基因组)"
    return None


# ------------------------------------------------------------------ 相关性
def _richness(it: dict) -> int:
    return sum(1 for f in ("abstract", "journal", "doi", "date_online") if it.get(f))


def dedupe_by_title(items: list[dict]) -> list[dict]:
    """二次去重：部分源缺 DOI，与有 DOI 的同一条并存 → 按归一标题再收敛一次。"""
    def tkey(it):
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (it.get("title") or "").lower())[:80]

    best: dict[str, dict] = {}
    for it in items:
        k = tkey(it)
        if not k:
            best[f"__{id(it)}"] = it
            continue
        cur = best.get(k)
        if cur is None:
            best[k] = it
            continue
        keep, drop = (cur, it) if _richness(cur) >= _richness(it) else (it, cur)
        for f in ("abstract", "journal", "doi", "pmid", "date_online", "type"):
            if not keep.get(f) and drop.get(f):
                keep[f] = drop[f]
        keep["issns"] = list({*(keep.get("issns") or []), *(drop.get("issns") or [])})
        keep["src"] = "+".join(sorted({s for s in (keep.get("src", ""), drop.get("src", "")) if s}))
        best[k] = keep
    return list(best.values())


def relevance(r, core) -> tuple[int, list, list]:
    t = (r.get("title") or "").lower()
    a = (r.get("abstract") or "").lower()
    th = [k for k in core if k in t]
    ah = [k for k in core if k in a]
    return len(th) * 3 + len(ah), th, ah


# ------------------------------------------------------------------ 排序 / 门槛
def gate_pass(r, g) -> tuple[bool, str]:
    """期刊水平门槛。三种模式（在配置的 gate 里用 mode 指定）：

    - `zone_if`（默认）：分区 ∈ zone_allow 或 IF > if_min，满足其一即通过
    - `journal_list`：期刊名命中给定列表（适合没有分区表 / 领域自有核心刊清单的场景）
    - `none`：不做期刊门槛（仅按相关性，慎用）
    """
    mode = g.get("mode", "zone_if")
    if mode == "none":
        return True, ""
    if mode == "journal_list":
        if journal_hit(r.get("journal") or "", g.get("journals") or []):
            return True, ""
        return False, "门槛未过(不在指定期刊清单)"
    zone, f = r.get("zone"), r.get("impact") or 0
    allow = g.get("zone_allow") or ["1 区", "2 区"]
    if zone in allow:
        return True, ""
    if f > g.get("if_min", 4.0):
        return True, ""
    if r.get("zone_missing") and not IFV:
        return True, "无分区/IF 数据(未启用期刊门槛，需人工复核)"
    return False, f"门槛未过(分区={zone or '无'}, IF={f})"


def content_tag(r, pref) -> int:
    """内容倾向：+1 偏营养/分子机制，-1 偏临床，0 中性。只影响排序，不做排除。"""
    if not pref:
        return 0
    t = ((r.get("title") or "") + " " + (r.get("abstract") or "")).lower()
    p = sum(1 for k in pref.get("prefer", []) if k in t)
    c = sum(1 for k in pref.get("clinical", []) if k in t)
    score = p - 2 * c          # 临床词按双倍计权：偏临床的文章要明显更强的机制/营养信号才能扳回
    if score >= 3:
        return 1
    if score <= -2:
        return -1
    return 0


def rank_key(r):
    """排序：相关性 → 内容倾向(机制/营养优先、临床靠后)
    → 1区且IF>8 → 1区 → IF>4 → 猪优先 → 日期新"""
    f = r.get("impact") or 0
    z1 = r.get("zone") == "1 区"
    d = re.sub(r"[^0-9]", "", r.get("date_online") or "")[:8].ljust(8, "0")
    return (
        -r["rel"],
        -(r.get("content_tag") or 0),
        -(1 if (z1 and f > 8) else 0),
        -(1 if z1 else 0),
        -(1 if f > 4 else 0),
        -(1 if "猪" in (r.get("species") or []) else 0),
        -int(d or 0),
    )


# ------------------------------------------------------------------ 主流程
def cmd_run(args):
    global MAILTO
    cfg = json.load(open(args.config, encoding="utf-8"))
    MAILTO = cfg.get("mailto") or "anonymous@example.com"
    days = int(args.days or cfg.get("window_days", 14))
    dirs = cfg["directions"]
    if getattr(args, "only", None):
        ids = {x.strip() for x in str(args.only).split(",")}
        dirs = [d for d in dirs if d["id"] in ids]
        if not dirs:
            raise SystemExit(f"--only {args.only} 没有匹配的方向")
    d1 = date.today()
    today = d1                      # age_days() 用；此前漏定义导致所有记录被判超期
    d0 = d1 - timedelta(days=days)
    D0, D1 = d0.isoformat(), d1.isoformat()
    run_dir = args.out or os.path.join(".", f"runs/{d1.strftime('%Y%m%d')}")
    os.makedirs(run_dir, exist_ok=True)

    print(f"[1/6] 载入分区 / IF 表 ...", flush=True)
    nz, nf = load_tables(cfg)
    print(f"      分区索引 {nz} 键；IF 索引 {nf} 键", flush=True)

    funnel = {"window": [D0, D1], "per_direction": {}}
    per_dir_raw = {}
    for d in dirs:
        did, dq = d["id"], d["query"]
        got, src_log = [], []
        for name, fn in (("PubMed", lambda: pubmed_fetch(dq, D0, D1)),
                         ("EuropePMC", lambda: epmc_fetch(dq, D0, D1)),
                         ("OpenAlex", lambda: openalex_fetch(dq, D0, D1)),
                         ("Crossref", lambda: crossref_fetch(dq, D0, D1))):
            try:
                x = fn()
                got.extend(x)
                src_log.append(f"{name}:{len(x)}")
            except Exception as e:  # noqa: BLE001
                src_log.append(f"{name}:ERR({str(e)[:60]})")
            time.sleep(1.0)
        per_dir_raw[did] = (d, got)
        print(f"[2/6] {d['name']:12s} 原始 {len(got):4d}  [{' '.join(src_log)}]", flush=True)

    print("[3/6] 归一 + 去重 + 联表 + 硬排除 + 相关性 ...", flush=True)
    pool = []          # 全方向候选（含 direction 归属候选）
    for did, (d, got) in per_dir_raw.items():
        st = {"raw": len(got)}
        by_key = {}
        for r in got:
            k = key_of(r)
            if k not in by_key:
                by_key[k] = dict(r)
            else:  # 合并：补字段 + 记录多源
                cur = by_key[k]
                for fld in ("abstract", "journal", "doi", "pmid", "date_online"):
                    if not cur.get(fld) and r.get(fld):
                        cur[fld] = r[fld]
                if r["src"] not in cur["src"]:
                    cur["src"] = cur["src"] + "+" + r["src"]
                cur["issns"] = list({*(cur.get("issns") or []), *(r.get("issns") or [])})
        st["unique"] = len(by_key)
        by_key = {key_of(x) if x.get("doi") or x.get("pmid") else f"t{i}": x
                  for i, x in enumerate(dedupe_by_title(list(by_key.values())))}
        st["unique_after_title"] = len(by_key)

        keep, exc = [], {}
        for r in by_key.values():
            join_metrics(r)
            # ★ 最高优先级：出版商 / 刊名黑名单，在读摘要之前丢弃
            bwhy = banned_reason(r, cfg.get("exclude_publishers") or {})
            if bwhy:
                exc[bwhy] = exc.get(bwhy, 0) + 1
                continue
            cats = d.get("zone_cat_allow")
            if cats:
                zc = r.get("zone_cat")
                if zc and zc not in cats:
                    k = f"期刊大类不在白名单:{zc}"
                    exc[k] = exc.get(k, 0) + 1
                    continue
                if not zc and d.get("zone_cat_strict"):
                    exc["期刊大类未知(严格模式)"] = exc.get("期刊大类未知(严格模式)", 0) + 1
                    continue
            r["species"] = detect_species(r)
            why = hard_exclude(r, cfg, d)
            if why:
                exc[why] = exc.get(why, 0) + 1
                continue
            r["rel"], r["hit_title"], r["hit_abs"] = relevance(r, d["core"])
            r["content_tag"] = content_tag(r, cfg.get("content_preference"))
            if not r["hit_title"] and r["rel"] < 2:
                r["weak_rel"] = True   # 相关性偏弱：14 天窗口内不选，仅用于 4 周补位
            keep.append(r)
        st["after_hard"] = len(keep)
        st["excluded"] = exc

        g = d.get("gate") or cfg["gate_default"]
        passed = []
        for r in keep:
            ok, why = gate_pass(r, g)
            if ok:
                if why:
                    r["gate_note"] = why
                passed.append(r)
            else:
                exc[why.split("(")[0]] = exc.get(why.split("(")[0], 0) + 1
        st["after_gate"] = len(passed)
        for r in passed:
            r["cand_dir"] = did
        pool.extend(passed)
        funnel["per_direction"][did] = st
        print(f"      方向{did} 去重 {st['unique']:4d} → 硬排除后 {st['after_hard']:4d} "
              f"→ 门槛后 {st['after_gate']:4d}", flush=True)

    # ---- 单归属：同一 DOI 只留 rel 最高的方向
    print("[4/6] 单归属（跨方向去重） ...", flush=True)
    best = {}
    for r in pool:
        k = key_of(r)
        if k not in best or r["rel"] > best[k]["rel"]:
            best[k] = r
    assigned = list(best.values())
    cross = len(pool) - len(assigned)
    print(f"      跨方向重复 {cross} 条已收敛，剩余 {len(assigned)} 条", flush=True)

    # ---- 配额
    quota = int(cfg.get("quota_per_direction", 10))
    recent_days = int(cfg.get("recent_days", 14))
    win2_days = int(cfg.get("window2_days", 28))

    def age_days(r):
        d0 = r.get("date_online") or ""
        try:
            return (today - datetime.strptime(d0, "%Y-%m-%d").date()).days
        except Exception:  # noqa: BLE001
            return 999

    final = {}
    for d in dirs:
        lst = [r for r in assigned if r["cand_dir"] == d["id"]]
        lst = [r for r in lst if age_days(r) <= win2_days]      # 超出 4 周直接不要
        lst.sort(key=rank_key)
        recent = [r for r in lst if age_days(r) <= recent_days and not r.get("weak_rel")]
        picked = list(recent[:quota])
        if len(picked) < quota:                                  # 补位：放宽相关性 + 延到 4 周
            for r in lst:
                if len(picked) >= quota:
                    break
                if r not in picked:
                    picked.append(r)
        picked.sort(key=rank_key)
        final[d["id"]] = {
            "name": d["name"],
            "pool": len(lst),
            "recent_pool": len(recent),
            "picked": picked,
            "not_picked": len(lst) - len(picked),
            "extended": len(picked) > len(recent[:quota]),
        }
    print("[5/6] 配额分配完成", flush=True)

    # ---- 输出
    json.dump({"meta": {"generated": datetime.now().isoformat(timespec="seconds"), "window": [D0, D1],
                        "cross_direction_merged": cross},
               "funnel": funnel, "final": final},
              open(os.path.join(run_dir, "result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    md = render(funnel, final, cfg, D0, D1, cross)
    open(os.path.join(run_dir, "report_skeleton.md"), "w", encoding="utf-8").write(md)
    print(f"[6/6] 输出：{run_dir}")
    print(f"      结果 {os.path.join(run_dir, 'result.json')}")
    print(f"      报告骨架 {os.path.join(run_dir, 'report_skeleton.md')}")
    return 0


def render(funnel, final, cfg, D0, D1, cross) -> str:
    L = [f"# 五方向 SCI 文献进展报告（{D0} ~ {D1}）", "",
         f"> 生成 {datetime.now():%Y-%m-%d %H:%M}｜窗口 {cfg.get('window_days', 14)} 天｜"
         f"每方向配额 {cfg.get('quota_per_direction', 10)} 篇｜跨方向重复已收敛 {cross} 条",
         "> 概述依据：仅摘要，未读全文；摘要未写明的写「摘要未提供」。", "",
         "## 漏斗统计", "",
         "| 方向 | 原始 | 去重 | 硬排除后 | 门槛后 | 严格候选 | 收录 | 猪相关 |",
         "|---|---|---|---|---|---|---|---|"]
    for did in sorted(final):
        s, f = funnel["per_direction"][did], final[did]
        pig = sum(1 for r in f["picked"] if "猪" in (r.get("species") or []))
        L.append(f"| {did} {f['name']} | {s['raw']} | {s['unique']} | {s['after_hard']} | "
                 f"{s['after_gate']} | {f['pool']} | {len(f['picked'])} | {pig} |")
    L += ["", "**排除原因明细**", ""]
    for did in sorted(funnel["per_direction"]):
        ex = funnel["per_direction"][did].get("excluded") or {}
        if ex:
            L.append(f"- 方向{did}：" + "；".join(f"{k} {v}" for k, v in sorted(ex.items())))
    L += ["", "---", ""]
    for did in sorted(final):
        f = final[did]
        L += [f"## {did}. {f['name']}", "",
              f"> 严格相关候选 {f['pool']} 篇，收录 {len(f['picked'])} 篇"
              + ("（不足配额，未凑数）" if len(f["picked"]) < cfg.get("quota_per_direction", 10) else ""), "",
              "| # | 相关性 | 倾向 | 中文译题 | 期刊 | 分区 | IF | 首次在线 | 物种 | DOI |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for i, r in enumerate(f["picked"], 1):
            tag = {1: "机制/营养", -1: "偏临床", 0: "—"}.get(r.get("content_tag") or 0, "—")
            L.append(f"| {i} | {r.get('rel')} | {tag} | {r.get('title_cn') or '（待填）'} | {r.get('journal') or '—'} | "
                     f"{r.get('zone') or '—'} | {r.get('impact') or '—'} | {r.get('date_online') or '—'} | "
                     f"{'/'.join(r.get('species') or [])} | `{r.get('doi') or '—'}` |")
        L += ["", "<!-- 每篇待补：英文原题、四段概述（背景/方法/主要结果/结论） -->", ""]
    L += ["---", f"_漏斗由 `scripts/funnel.py` 生成；排序口径：相关性→1区且IF>8→1区→IF>4→猪优先→日期_"]
    return "\n".join(L)


def cmd_check(args):
    global MAILTO
    cfg = json.load(open(args.config, encoding="utf-8"))
    MAILTO = cfg.get("mailto") or "anonymous@example.com"
    print(f"python  : {sys.executable}")
    print(f"探测代理: 直连")
    d1 = date.today()
    d0 = d1 - timedelta(days=3)
    probes = {
        "PubMed": lambda: pubmed_fetch('(genomic selection OR GWAS)', d0.isoformat(), d1.isoformat()),
        "EuropePMC": lambda: epmc_fetch('("gut microbiota")', d0.isoformat(), d1.isoformat()),
        "OpenAlex": lambda: openalex_fetch('"gut microbiota"', d0.isoformat(), d1.isoformat(), 5),
        "Crossref": lambda: crossref_fetch('"meat quality"', d0.isoformat(), d1.isoformat(), 5),
    }
    for k, fn in probes.items():
        t = time.time()
        try:
            n = len(fn())
            print(f"  ✓ {k:10s} {n:3d} 条  {(time.time()-t):.1f}s")
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {k:10s} {type(e).__name__}: {str(e)[:110]}")
    try:
        nz, nf = load_tables(cfg)
        print(f"  ✓ 分区表 {nz} 键 / IF 表 {nf} 键")
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ 表读取 {type(e).__name__}: {str(e)[:110]}")


def main():
    global _OPENER, MAILTO
    MAILTO = "anonymous@example.com"
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default=None, help="一般不需要；默认直连")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--config", required=True)
    r.add_argument("--days", default=None, type=int)
    r.add_argument("--out", default=None)
    r.add_argument("--only", default=None, help="只跑指定方向，逗号分隔，如 1 或 1,3")
    r.set_defaults(func=cmd_run)
    c = sub.add_parser("check")
    c.add_argument("--config", required=True)
    c.set_defaults(func=cmd_check)
    a = ap.parse_args()
    _OPENER = make_opener(a.proxy)
    sys.exit(a.func(a) or 0)


if __name__ == "__main__":
    main()
