#!/usr/bin/env python3
"""把若干目录里的 .nupkg 生成静态 NuGet v3 源 (只含 PackageBaseAddress, 够 restore 用)。
用法: python gen_feed.py --out public --base https://nuget.example.com DIR [DIR ...]
"""
import argparse, json, re, shutil, sys, zipfile
from collections import defaultdict
from pathlib import Path

LIMIT = 25 * 1024 * 1024  # Cloudflare Pages 单文件上限 25 MiB

ap = argparse.ArgumentParser()
ap.add_argument("--out", default="public")
ap.add_argument("--base", default="", help="站点根 URL(https://); 缺省读环境变量 SITE_URL")
ap.add_argument("srcs", nargs="+")
a = ap.parse_args()

import os
base = (a.base or os.environ.get("SITE_URL", "")).strip().rstrip("/")
if not base and os.environ.get("CF_PAGES_URL") and os.environ.get("CF_PAGES_BRANCH", "main") not in ("main", "master"):
    # 仅给 Cloudflare 的预览分支构建兜底, 生产构建必须显式设置 SITE_URL
    base = os.environ["CF_PAGES_URL"].rstrip("/")
    print(f"提示: 预览构建, 使用 CF_PAGES_URL = {base}")
if not base.startswith("https://"):
    sys.exit("错误: 需要 https:// 开头的站点根URL: 请在 Cloudflare Pages 的环境变量里设置 SITE_URL")
out = Path(a.out)

def norm(v):
    v = v.split("+")[0].lower()
    core, _, pre = v.partition("-")
    parts = core.split(".")
    while len(parts) < 3: parts.append("0")
    if len(parts) == 4 and parts[3] == "0": parts.pop()
    parts = [str(int(p)) for p in parts]
    return ".".join(parts) + (("-" + pre) if pre else "")

files = []
for s in a.srcs:
    p = Path(s)
    if p.is_dir():
        files += sorted(p.rglob("*.nupkg"))
if not files:
    sys.exit("错误: 没有找到任何 .nupkg")

shutil.rmtree(out, ignore_errors=True)
flat = out / "v3" / "flat"
flat.mkdir(parents=True)
versions, seen, too_big = defaultdict(set), set(), []

for pkg in files:
    if pkg.stat().st_size > LIMIT:
        too_big.append(f"{pkg.name} ({pkg.stat().st_size/1048576:.1f} MiB)")
        continue
    with zipfile.ZipFile(pkg) as z:
        name = next(n for n in z.namelist() if n.endswith(".nuspec") and "/" not in n)
        data = z.read(name).decode("utf-8-sig")
    pid = re.search(r"<id>(.*?)</id>", data).group(1).strip().lower()
    ver = norm(re.search(r"<version>(.*?)</version>", data).group(1).strip())
    if (pid, ver) in seen:
        continue  # 前面的目录优先 (在线拉取的结果), 重复的忽略
    seen.add((pid, ver))
    d = flat / pid / ver
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(pkg, d / f"{pid}.{ver}.nupkg")
    (d / f"{pid}.nuspec").write_text(data, encoding="utf-8")
    versions[pid].add(ver)

if too_big:
    print("警告: 以下包超过 Cloudflare Pages 25 MiB 单文件限制, 已跳过 (需改用 R2):", *too_big, sep="\n  ")

for pid, vs in versions.items():
    (flat / pid / "index.json").write_text(json.dumps({"versions": sorted(vs)}))

(out / "v3" / "index.json").write_text(json.dumps({
    "version": "3.0.0",
    "resources": [{"@id": f"{base}/v3/flat/", "@type": "PackageBaseAddress/3.0.0"}],
}, indent=2))

(out / "_headers").write_text(
"""/v3/index.json
  Content-Type: application/json
  Cache-Control: public, max-age=60
/v3/flat/*/index.json
  Content-Type: application/json
  Cache-Control: public, max-age=60
/v3/flat/*.nupkg
  Content-Type: application/octet-stream
  Cache-Control: public, max-age=31536000, immutable
""")
print(f"完成: {len(seen)} 个包版本, {len(versions)} 个包 ID -> {out}  (@id = {base}/v3/flat/)")
if too_big:
    sys.exit(1)
