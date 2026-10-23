#!/usr/bin/env bash
# 读取 packages.txt, 生成 work/fetch.csproj (含全部 PackageReference) 和 work/NuGet.Config(在线源)
set -euo pipefail
LIST="${1:-packages.txt}"
mkdir -p work
LIST="$(realpath "$LIST")"
cd work

FW=$(grep -iE '^[[:space:]]*#[[:space:]]*frameworks:' "$LIST" | head -1 \
     | sed -E 's/^[^:]*:[[:space:]]*//' | tr -d '\r ' || true)
FW=${FW:-netstandard2.0}
echo "目标框架: $FW"

cat > fetch.csproj <<XML
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFrameworks>$FW</TargetFrameworks>
  </PropertyGroup>
</Project>
XML

cat > NuGet.Config <<'XML'
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <packageSources>
    <clear />
    <add key="nuget.org" value="https://api.nuget.org/v3/index.json" />
    <add key="BepInEx" value="https://nuget.bepinex.dev/v3/index.json" />
  </packageSources>
  <disabledPackageSources>
    <clear />
  </disabledPackageSources>
</configuration>
XML

count=0
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%$'\r'}"
  line="$(printf '%s' "$line" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
  case "$line" in ''|'#'*) continue ;; esac
  if [[ "$line" =~ ^dotnet[[:space:]]+add[[:space:]]+package[[:space:]]+(.+)$ ]]; then
    args="${BASH_REMATCH[1]}"
    # 可选 --skip-framework <tfm>[,<tfm>...]: 该包不支持的框架, 只在其它框架下引用, 否则 restore 报 NU1202。
    # 不用 dotnet add 自带的 --framework: 配合 --no-restore 时它会被忽略, 生成的引用没有 Condition。
    skip=""
    if [[ "$args" =~ ^(.*)[[:space:]]+--skip-framework[[:space:]]+([^[:space:]]+)$ ]]; then
      args="${BASH_REMATCH[1]}"
      skip="${BASH_REMATCH[2]}"
    fi
    pkg="${args%% *}"
    echo "+ dotnet add package $args${skip:+  (跳过框架: $skip)}"
    dotnet add fetch.csproj package $args --no-restore
    if [ -n "$skip" ]; then
      cond=""
      IFS=',' read -ra tfms <<< "$skip"
      for t in "${tfms[@]}"; do
        cond="${cond:+$cond And }'\$(TargetFramework)' != '$t'"
      done
      sed -i -E "s#(<PackageReference Include=\"${pkg}\" Version=\"[^\"]*\")[[:space:]]*/>#\1 Condition=\"${cond}\" />#" fetch.csproj
      grep -q "<PackageReference Include=\"${pkg}\" Version=\"[^\"]*\" Condition=" fetch.csproj \
        || { echo "::error file=packages.txt::无法为 $pkg 写入框架条件"; exit 1; }
    fi
    count=$((count+1))
  else
    echo "::error file=packages.txt::无法识别的行(只支持 dotnet add package ...): $line"
    exit 1
  fi
done < "$LIST"

if [ "$count" -eq 0 ]; then
  echo "::error file=packages.txt::没有任何 dotnet add package 行"
  exit 1
fi
echo "共 $count 个包声明"
