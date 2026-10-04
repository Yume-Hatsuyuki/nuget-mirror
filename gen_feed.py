#!/usr/bin/env python3
"""把若干目录里的 .nupkg 生成静态 NuGet v3 源。
生成内容:
  v3/index.json               服务索引
  v3/flat/...                 PackageBaseAddress (restore 用)
  v3/registration/...         RegistrationsBaseUrl (VS 包详情/依赖/版本列表用)
  v3/search-index.json        搜索索引, 由 src/worker.js 的 /v3/search、/v3/autocomplete 读取
用法: python gen_feed.py --out public [--base https://域名] DIR [DIR ...]
"""
import argparse, json, os, re, shutil, sys, zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

LIMIT = 25 * 1024 * 1024  # Cloudflare 单文件上限 25 MiB

ap = argparse.ArgumentParser()
ap.add_argument("--out", default="public")
ap.add_argument("--base", default="", help="站点根 URL(https://); 缺省读环境变量 SITE_URL")
ap.add_argument("srcs", nargs="+")
a = ap.parse_args()

base = (a.base or os.environ.get("SITE_URL", "")).strip().rstrip("/")
if not base and os.environ.get("CF_PAGES_URL") and os.environ.get("CF_PAGES_BRANCH", "main") not in ("main", "master"):
    base = os.environ["CF_PAGES_URL"].rstrip("/")  # 仅给预览分支兜底
    print(f"提示: 预览构建, 使用 CF_PAGES_URL = {base}")
if not base.startswith("https://"):
    sys.exit("错误: 需要 https:// 开头的站点根URL: 请在 Cloudflare 的【构建】变量里设置 SITE_URL")
out = Path(a.out)

def norm(v):
    v = v.split("+")[0].lower()
    core, _, pre = v.partition("-")
    parts = core.split(".")
    while len(parts) < 3: parts.append("0")
    if len(parts) == 4 and parts[3] == "0": parts.pop()
    parts = [str(int(p)) for p in parts]
    return ".".join(parts) + (("-" + pre) if pre else "")

def vkey(v):  # SemVer 排序
    core, _, pre = v.partition("-")
    nums = tuple(int(x) for x in core.split("."))
    nums += (0,) * (4 - len(nums))
    if not pre:
        return (nums, 1, ())
    return (nums, 0, tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split(".")))

def ln(e):  # 去掉命名空间
    return e.tag.split("}")[-1]

def child(parent, name):
    for c in parent:
        if ln(c) == name:
            return c
    return None

def text(parent, name, default=""):
    c = child(parent, name)
    return (c.text or "").strip() if c is not None and c.text else default

def dep_range(v):
    v = (v or "").strip()
    if not v: return "(, )"
    if v[0] in "[(": return v
    return f"[{v}, )"

def parse_nuspec(raw, ver, pid_lower, ts):
    root = ET.fromstring(raw)
    meta = child(root, "metadata")
    groups = []
    deps = child(meta, "dependencies")
    if deps is not None:
        plain = [d for d in deps if ln(d) == "dependency"]
        sets = [("", plain)] if plain else []
        sets += [(g.get("targetFramework", ""), [d for d in g if ln(d) == "dependency"])
                 for g in deps if ln(g) == "group"]
        for tf, ds in sets:
            g = {"@type": "PackageDependencyGroup"}
            if tf: g["targetFramework"] = tf
            g["dependencies"] = [
                {"@type": "PackageDependency", "id": d.get("id", ""), "range": dep_range(d.get("version"))}
                for d in ds
            ]
            groups.append(g)
    lic = child(meta, "license")
    tags = text(meta, "tags").replace(",", " ").split()
    authors = text(meta, "authors")
    entry = {
        "id": text(meta, "id"),
        "version": ver,
        "authors": authors,
        "description": text(meta, "description"),
        "summary": text(meta, "summary"),
        "title": text(meta, "title") or text(meta, "id"),
        "tags": tags,
        "projectUrl": text(meta, "projectUrl"),
        "licenseUrl": text(meta, "licenseUrl"),
        "iconUrl": text(meta, "iconUrl"),
        "licenseExpression": (lic.text or "").strip() if lic is not None and lic.get("type") == "expression" else "",
        "requireLicenseAcceptance": text(meta, "requireLicenseAcceptance").lower() == "true",
        "language": text(meta, "language"),
        "minClientVersion": meta.get("minClientVersion", ""),
        "listed": True,
        "published": ts,
        "dependencyGroups": groups,
    }
    return entry

files = []
for s in a.srcs:
    p = Path(s)
    if p.is_dir():
        files += sorted(p.rglob("*.nupkg"))
if not files:
    sys.exit("错误: 没有找到任何 .nupkg")

shutil.rmtree(out, ignore_errors=True)
v3 = out / "v3"
flat = v3 / "flat"
reg = v3 / "registration"
flat.mkdir(parents=True)
reg.mkdir(parents=True)

pkgs = defaultdict(dict)   # pid -> ver -> entry
seen, too_big = set(), []

for pkg in files:
    if pkg.stat().st_size > LIMIT:
        too_big.append(f"{pkg.name} ({pkg.stat().st_size/1048576:.1f} MiB)")
        continue
    with zipfile.ZipFile(pkg) as z:
        name = next(n for n in z.namelist() if n.endswith(".nuspec") and "/" not in n)
        raw = z.read(name)
        y, mo, d, h, mi, se = z.getinfo(name).date_time
    data = raw.decode("utf-8-sig")
    pid = re.search(r"<id>(.*?)</id>", data).group(1).strip().lower()
    ver = norm(re.search(r"<version>(.*?)</version>", data).group(1).strip())
    if (pid, ver) in seen:
        continue
    seen.add((pid, ver))
    dd = flat / pid / ver
    dd.mkdir(parents=True, exist_ok=True)
    shutil.copy(pkg, dd / f"{pid}.{ver}.nupkg")
    (dd / f"{pid}.nuspec").write_text(data, encoding="utf-8")
    ts = datetime(y, mo, d, h, mi, se, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    try:
        entry = parse_nuspec(raw, ver, pid, ts)
    except Exception as e:
        print(f"警告: 解析 {pkg.name} 的元数据失败, 仅保证 restore 可用: {e}")
        entry = {"id": pid, "version": ver, "authors": "", "description": "", "summary": "",
                 "title": pid, "tags": [], "projectUrl": "", "licenseUrl": "", "iconUrl": "",
                 "licenseExpression": "", "requireLicenseAcceptance": False, "language": "",
                 "minClientVersion": "", "listed": True, "published": ts, "dependencyGroups": []}
    pkgs[pid][ver] = entry

if too_big:
    print("警告: 以下包超过 25 MiB 单文件限制, 已跳过 (需改用 R2):", *too_big, sep="\n  ")

now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
flat_url = f"{base}/v3/flat/"
reg_url = f"{base}/v3/registration/"
search_index = []

for pid, vmap in pkgs.items():
    vers = sorted(vmap, key=vkey)
    (flat / pid / "index.json").write_text(json.dumps({"versions": vers}))

    rdir = reg / pid
    rdir.mkdir(parents=True, exist_ok=True)
    index_url = f"{reg_url}{pid}/index.json"
    leaves = []
    for v in vers:
        e = vmap[v]
        leaf_url = f"{reg_url}{pid}/{v}.json"
        nupkg = f"{flat_url}{pid}/{v}/{pid}.{v}.nupkg"
        ce = {"@id": leaf_url, "@type": "PackageDetails", **e, "packageContent": nupkg}
        for g in ce["dependencyGroups"]:
            g["@id"] = f"{leaf_url}#dependencyGroup/{g.get('targetFramework','any')}"
            for dep in g["dependencies"]:
                dep["@id"] = f"{leaf_url}#dependencyGroup/{g.get('targetFramework','any')}/{dep['id'].lower()}"
        leaf = {"@id": leaf_url, "@type": "Package", "commitId": "static", "commitTimeStamp": now,
                "catalogEntry": ce, "packageContent": nupkg, "registration": index_url}
        leaves.append(leaf)
        (rdir / f"{v}.json").write_text(json.dumps(
            {"@context": {"@vocab": "http://schema.nuget.org/schema#", "xsd": "http://www.w3.org/2001/XMLSchema#"},
             **leaf}, ensure_ascii=False))
    page = {"@id": f"{index_url}#page/{vers[0]}/{vers[-1]}", "@type": "catalog:CatalogPage",
            "commitId": "static", "commitTimeStamp": now, "count": len(leaves),
            "lower": vers[0], "upper": vers[-1], "parent": index_url, "items": leaves}
    (rdir / "index.json").write_text(json.dumps(
        {"@id": index_url, "@type": ["catalog:CatalogRoot", "PackageRegistration", "catalog:Permalink"],
         "commitId": "static", "commitTimeStamp": now, "count": 1, "items": [page],
         "@context": {"@vocab": "http://schema.nuget.org/schema#", "catalog": "http://schema.nuget.org/catalog#",
                      "xsd": "http://www.w3.org/2001/XMLSchema#"}}, ensure_ascii=False))

    stable = [v for v in vers if "-" not in v]
    keys = {vers[-1], stable[-1] if stable else None} - {None}
    search_index.append({
        "id": vmap[vers[-1]]["id"] or pid,
        "lid": pid,
        "versions": [{"version": v, "downloads": 0, "@id": f"{reg_url}{pid}/{v}.json"} for v in vers],
        "latest": vers[-1],
        "latestStable": stable[-1] if stable else None,
        "meta": {v: {k: vmap[v][k] for k in ("title", "description", "summary", "authors", "tags",
                                              "projectUrl", "licenseUrl", "iconUrl")} for v in keys},
        "registration": f"{reg_url}{pid}/index.json",
    })

search_index.sort(key=lambda x: x["lid"])
(v3 / "search-index.json").write_text(json.dumps({"base": base, "packages": search_index}, ensure_ascii=False))

def res(t, url, c=""):
    r = {"@id": url, "@type": t}
    if c: r["comment"] = c
    return r

search_url, auto_url = f"{base}/v3/search", f"{base}/v3/autocomplete"
resources = [res("PackageBaseAddress/3.0.0", flat_url, "restore 用的下载地址")]
for t in ("SearchQueryService", "SearchQueryService/3.0.0-beta", "SearchQueryService/3.0.0-rc"):
    resources.append(res(t, search_url, "由 Worker 提供的搜索"))
for t in ("SearchAutocompleteService", "SearchAutocompleteService/3.0.0-beta", "SearchAutocompleteService/3.0.0-rc"):
    resources.append(res(t, auto_url))
for t in ("RegistrationsBaseUrl", "RegistrationsBaseUrl/3.0.0-beta", "RegistrationsBaseUrl/3.0.0-rc",
          "RegistrationsBaseUrl/3.4.0", "RegistrationsBaseUrl/3.6.0", "RegistrationsBaseUrl/Versioned"):
    resources.append(res(t, reg_url, "包元数据 (VS 详情页)"))
(v3 / "index.json").write_text(json.dumps({"version": "3.0.0", "resources": resources}, indent=2, ensure_ascii=False))

(out / "_headers").write_text(
"""/v3/index.json
  Content-Type: application/json
  Cache-Control: public, max-age=60
/v3/search-index.json
  Cache-Control: public, max-age=60
/v3/registration/*
  Content-Type: application/json
  Cache-Control: public, max-age=60
/v3/flat/*/index.json
  Content-Type: application/json
  Cache-Control: public, max-age=60
/v3/flat/*.nupkg
  Content-Type: application/octet-stream
  Cache-Control: public, max-age=31536000, immutable
""")
print(f"完成: {len(seen)} 个包版本, {len(pkgs)} 个包 ID -> {out}  (@id = {base}/v3/flat/)")
if too_big:
    sys.exit(1)
