# nuget-mirror

自托管的 NuGet 镜像源。在 GitHub 网页上写几行 `dotnet add package`，Actions 自动从在线源拉取这些包和全部依赖，合并进仓库，Cloudflare 再把它发布成一个可直接用于 `dotnet restore` 的 NuGet 源。

```
packages.txt ──push──▶ GitHub Actions ──▶ nupkg-packages/ ──merge to main──▶ Cloudflare 构建部署
 (网页上编辑)          拉包 + 全部依赖      (自动 PR 并合并)                    生成 v3 JSON 并发布
```

## 仓库结构

| 路径 | 作用 |
|---|---|
| `packages.txt` | 要拉取的包清单，一行一条 `dotnet add package` |
| `nupkg-packages/` | 镜像的实际内容，所有 `.nupkg` 都在这里 |
| `gen_feed.py` | 由 Cloudflare 构建时运行，把 `nupkg-packages/` 生成静态 NuGet v3 源 |
| `wrangler.jsonc` | Cloudflare 部署配置，指定发布 `public/` 目录 |
| `scripts/prepare.sh` | 把 `packages.txt` 转成临时项目，供两个工作流共用 |
| `.github/workflows/mirror.yml` | 拉包并提交的工作流 |
| `.github/workflows/verify.yml` | 手动运行，验证镜像是否完整 |
| `NuGet.Config.example` | 使用镜像的配置示例 |

## 一次性设置

### 1. GitHub

Settings → Actions → General → Workflow permissions：

- 选 **Read and write permissions**
- 勾选 **Allow GitHub Actions to create and approve pull requests**

不想用 PR 的话，在 Settings → Secrets and variables → Actions → Variables 新增 `DIRECT_COMMIT`，值填 `true`，工作流会直接提交到 `main`。

没开启 PR 权限时，工作流会自动改为直接提交，并在运行页给出一条黄色警告，镜像仍然会更新。

### 2. `wrangler.jsonc`

仓库根目录需要这个文件，`name` 必须与 Cloudflare 里的项目名称完全一致：

```jsonc
{
  "name": "nuget-mirror",
  "compatibility_date": "2026-09-01",
  "assets": { "directory": "./public" }
}
```

### 3. Cloudflare

Workers & Pages → Create → 连接 Git，选择本仓库。

| 字段 | 填写 |
|---|---|
| 项目名称 | `nuget-mirror`（与 `wrangler.jsonc` 的 `name` 一致） |
| 构建命令 | `python3 gen_feed.py --out public nupkg-packages` |
| 部署命令 | `npx wrangler deploy` |
| 预览命令 | `npx wrangler preview`（保持默认） |

在**构建变量**（不是运行时变量）里添加：

| 名称 | 值 |
|---|---|
| `SITE_URL` | 镜像对外的完整地址，例如 `https://nuget-mirror.xxx.workers.dev` 或你的自定义域名，必须 `https://` 开头 |

构建报找不到 python 时，再加一个构建变量 `PYTHON_VERSION = 3.12`。

`SITE_URL` 要手动填，因为 NuGet 要求服务索引里的 `@id` 是固定的绝对地址。如果还不知道最终地址，可以先填一个临时值部署一次，拿到真实地址后改掉 `SITE_URL` 再重新部署。

## 日常使用

全部在 GitHub 网页上完成。

### 添加包

打开 `packages.txt`，点铅笔图标，粘贴命令，提交：

```
dotnet add package HarmonyX --version 2.16.1
dotnet add package BepInEx.Core --version 5.4.21
```

规则：

- 一行一条，只识别 `dotnet add package ...` 开头的行，其他内容会报错
- `#` 开头是注释，空行忽略
- 支持 `--version`、`--prerelease` 等参数
- 目标框架在这一行设置，依赖会按这些框架解析：
  ```
  # frameworks: netstandard2.0;net35;net472
  ```
  只用其中一个框架的话，删掉另外几个，镜像会小很多

提交后 Actions 自动运行，拉取包和它们的全部依赖，合并进 `nupkg-packages/`，自动建 PR 并合并，Cloudflare 随后重新部署。一般一两分钟内完成。

### 删除包

在 `nupkg-packages/` 里直接删除对应的 `.nupkg` 文件并提交，Cloudflare 会自动重建。同时把 `packages.txt` 里对应的行删掉，否则下次修改 `packages.txt` 时，这个包会被重新拉回来。

### 手动补充包

网页上 Add file → Upload files，传到 `nupkg-packages/`。文件名无所谓，`gen_feed.py` 读取的是包内的 `.nuspec`。

### 合并规则

拉取是累加的：只新增和覆盖同名文件，不会删除已有的包。`nupkg-packages/` 才是镜像内容的最终来源，`packages.txt` 只是"要拉哪些包"的清单。

## 使用镜像

在需要还原包的项目旁放一份 `NuGet.Config`：

```xml
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <packageSources>
    <clear />
    <add key="YumeHatsuyuki_Mirror" value="https://nuget-mirror.yume-hatsuyuki.moe/v3/index.json" protocolVersion="3" />
  </packageSources>
  <disabledPackageSources>
    <clear />
  </disabledPackageSources>
</configuration>
```

- 地址以 `.json` 结尾时，NuGet 会自动按 v3 处理，可省略 `protocolVersion="3"`。
- `<disabledPackageSources><clear /></disabledPackageSources>` 请保留。NuGet 的禁用列表是按源名称匹配、并且从全局配置继承的，`<clear />` 只清空源列表，不会清空禁用列表。全局配置里如果禁用了同名的源，你的源也会被连带禁用。
- 地址必须是 `https://`。

验证：

```powershell
dotnet nuget locals all --clear
dotnet restore -v n
```

### 验证镜像是否完整

Actions → Verify mirror → Run workflow。它只用你部署好的镜像源（不含 nuget.org 和 BepInEx）在干净环境里还原一次 `packages.txt` 里的所有包，通过就说明镜像完整。需要先在仓库 Variables 里设置 `SITE_URL`。

## 常见问题

| 现象 | 原因与处理 |
|---|---|
| Actions 的"提交"步骤失败 | 多半是没开启 PR 权限，见上面 GitHub 设置。现在会自动降级为直接提交 |
| Cloudflare 报 `Could not detect a directory containing static files` | 仓库缺少 `wrangler.jsonc`，或构建命令为空，`public/` 没有生成 |
| Cloudflare 报项目名称不匹配 | `wrangler.jsonc` 的 `name` 与 Cloudflare 项目名称不一致 |
| 构建日志提示需要 `https://` 开头的站点根 URL | `SITE_URL` 没加在构建变量里，或没写 `https://` |
| 构建日志提示 `python3: not found` | 在构建变量里加 `PYTHON_VERSION = 3.12` |
| `dotnet nuget list source` 里源显示"已禁用" | 本地 `NuGet.Config` 缺少 `<disabledPackageSources><clear />` |
| restore 报 `NU1101`（找不到包） | 镜像里缺这个包或它的依赖，补进 `packages.txt` 后重新运行，用 Verify mirror 检查 |
| restore 报 `Unable to load the service index` | `SITE_URL` 对应的地址打不开，浏览器先访问 `/v3/index.json` 看能否看到 JSON |
| 部署失败，提示文件过大 | 单个文件上限 25 MiB，超过的包需要改用 R2 |
| 某个旧版本又出现了 | 它还在 `packages.txt` 里，把对应的行也删掉 |

## 限制

- 只支持 restore，不提供搜索和发布接口。
- 静态托管，更新有几十秒到几分钟的延迟，另外 JSON 文件带 60 秒缓存。
- 国内访问 `*.workers.dev` 可能不稳定，绑定自定义域名通常会好很多，绑定后记得同步修改 `SITE_URL` 并重新部署。
- 提交记录会随包的增加而变大。包不多时没有影响。
