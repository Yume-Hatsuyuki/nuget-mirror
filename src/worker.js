// 静态 NuGet 镜像的搜索接口。
// 仅 /v3/search 和 /v3/autocomplete 会先进入这个 Worker (见 wrangler.jsonc 的 run_worker_first),
// 其它路径(restore 下载、元数据)仍由静态资源直接返回, 不消耗 Worker 调用。
// 数据来自构建时由 gen_feed.py 生成的 /v3/search-index.json。

const JSON_HEADERS = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "public, max-age=60",
  "Access-Control-Allow-Origin": "*",
};

async function loadIndex(env, url) {
  const r = await env.ASSETS.fetch(new URL("/v3/search-index.json", url));
  if (!r.ok) throw new Error("search-index.json 不存在, 请检查构建是否成功");
  return r.json();
}

function int(v, d) {
  const n = parseInt(v ?? "", 10);
  return Number.isFinite(n) && n >= 0 ? n : d;
}

function parseQuery(q) {
  // 支持 id:xxx / packageid:xxx / title:xxx / tags:xxx / author(s):xxx / description:xxx / "带空格的短语"
  const terms = [];
  const re = /(?:(\w+):)?(?:"([^"]*)"|(\S+))/g;
  let m;
  while ((m = re.exec(q || ""))) {
    const field = (m[1] || "").toLowerCase();
    const val = (m[2] ?? m[3] ?? "").toLowerCase();
    if (val) terms.push({ field, val });
  }
  return terms;
}

function matches(p, terms, meta) {
  const hay = {
    id: p.lid,
    packageid: p.lid,
    title: (meta.title || "").toLowerCase(),
    tags: (meta.tags || []).join(" ").toLowerCase(),
    author: (meta.authors || "").toLowerCase(),
    authors: (meta.authors || "").toLowerCase(),
    owner: (meta.authors || "").toLowerCase(),
    description: ((meta.description || "") + " " + (meta.summary || "")).toLowerCase(),
  };
  const all = [p.lid, hay.title, hay.tags, hay.authors, hay.description].join(" ");
  return terms.every(({ field, val }) => (hay[field] !== undefined ? hay[field] : all).includes(val));
}

function pick(p, prerelease) {
  const v = prerelease ? p.latest : p.latestStable;
  return v ? { version: v, meta: p.meta[v] || p.meta[p.latest] || {} } : null;
}

function search(url, idx) {
  const sp = url.searchParams;
  const q = sp.get("q") || "";
  const skip = int(sp.get("skip"), 0);
  const take = Math.min(int(sp.get("take"), 20), 1000);
  const prerelease = (sp.get("prerelease") || "false").toLowerCase() === "true";
  const terms = parseQuery(q);
  const plain = q.trim().toLowerCase();

  const hits = [];
  for (const p of idx.packages) {
    const c = pick(p, prerelease);
    if (!c || !matches(p, terms, c.meta)) continue;
    hits.push({ p, c, rank: p.lid === plain ? 0 : p.lid.startsWith(plain) ? 1 : 2 });
  }
  hits.sort((a, b) => a.rank - b.rank || a.p.lid.localeCompare(b.p.lid));

  const data = hits.slice(skip, skip + take).map(({ p, c }) => ({
    "@id": p.registration,
    "@type": "Package",
    registration: p.registration,
    id: p.id,
    version: c.version,
    description: c.meta.description || "",
    summary: c.meta.summary || "",
    title: c.meta.title || p.id,
    iconUrl: c.meta.iconUrl || "",
    licenseUrl: c.meta.licenseUrl || "",
    projectUrl: c.meta.projectUrl || "",
    tags: c.meta.tags || [],
    authors: (c.meta.authors || "").split(",").map((s) => s.trim()).filter(Boolean),
    owners: [],
    totalDownloads: 0,
    verified: false,
    packageTypes: [{ name: "Dependency" }],
    versions: p.versions,
  }));

  return {
    "@context": { "@vocab": "http://schema.nuget.org/schema#", "@base": `${idx.base}/v3/registration/` },
    totalHits: hits.length,
    data,
  };
}

function autocomplete(url, idx) {
  const sp = url.searchParams;
  const skip = int(sp.get("skip"), 0);
  const take = Math.min(int(sp.get("take"), 20), 1000);
  const prerelease = (sp.get("prerelease") || "false").toLowerCase() === "true";
  const ctx = { "@vocab": "http://schema.nuget.org/schema#" };

  const id = (sp.get("id") || "").toLowerCase();
  if (id) {  // 列出某个包的版本
    const p = idx.packages.find((x) => x.lid === id);
    const vs = p ? p.versions.map((v) => v.version).filter((v) => prerelease || !v.includes("-")) : [];
    return { "@context": ctx, totalHits: vs.length, data: vs.slice(skip, skip + take) };
  }
  const q = (sp.get("q") || "").trim().toLowerCase();
  const ids = idx.packages
    .filter((p) => (prerelease ? p.latest : p.latestStable) && p.lid.includes(q))
    .map((p) => p.id);
  return { "@context": ctx, totalHits: ids.length, data: ids.slice(skip, skip + take) };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: { ...JSON_HEADERS, "Access-Control-Allow-Methods": "GET, OPTIONS" } });
    }
    if (url.pathname === "/v3/search" || url.pathname === "/v3/autocomplete") {
      try {
        const idx = await loadIndex(env, url);
        const body = url.pathname === "/v3/search" ? search(url, idx) : autocomplete(url, idx);
        return new Response(JSON.stringify(body), { headers: JSON_HEADERS });
      } catch (e) {
        return new Response(JSON.stringify({ error: String(e.message || e) }), { status: 500, headers: JSON_HEADERS });
      }
    }
    return env.ASSETS.fetch(request);
  },
};
