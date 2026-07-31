# Loki MCP

本地 stdio MCP：根据项目根目录 `env.yaml` 的环境名称，调用 Grafana 数据源代理执行只读 Loki `query_range` 查询。

## 安装

```powershell
cd E:\Project\cxh\mcp-tools\loki-mcp
D:\Program Files\Python\Python312\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test,build]"
.\build.ps1
```

## Codex 配置

在 `C:\Users\huge\.codex\config.toml` 追加：

```toml
[mcp_servers.loki]
command = "E:\\Project\\xcmg\\xcmg_uat\\mom-backend\\.mcp\\loki-mcp\\loki-mcp.exe"
args = [
  "--env-file",
  "E:\\Project\\xcmg\\xcmg_uat\\mom-backend\\env.yaml"
]

[mcp_servers.loki.env]
PYTHONUTF8 = "1"
```

重启 Codex 并新开任务后，可调用 `query_loki_logs`：

```text
environment: "矿机(一期生产)"
start: "2026-07-29T12:00:00+08:00"
end: "2026-07-29T12:03:00+08:00"
limit: 200
contains: "NullPointerException"
```

## 安全和边界

- 仅查询 `env.yaml` 中精确匹配的环境；不跨环境兜底。
- 仅访问 Grafana 数据源代理的 Loki `query_range` 接口；不支持写入、删除和 tail。
- `limit` 默认 200，最大 2,000。
- UAT 域名 `mesu.xcmg.com` 使用旧 TLS 协商兼容选项，但仍保持服务器证书校验。
