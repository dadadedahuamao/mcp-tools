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

## Linux Docker 部署

将服务器上的真实 `env.yaml` 保存为 `config/env.yaml`（该文件已被 Git 忽略），然后执行：

```bash
cp config/env.yaml.example config/env.yaml
cp .env.example .env
chmod 600 config/env.yaml
chown 10001:10001 config/env.yaml
# 编辑 config/env.yaml
# 编辑 .env，将 LOKI_MCP_ALLOWED_HOST 改为实际服务器 IP 或域名及端口
docker-compose up -d --build
```

服务地址为 `http://<服务器IP或域名>:8001/mcp`。`.env` 中的 `LOKI_MCP_ALLOWED_HOST` 必须与该 URL 中的 `IP 或域名:8001` 一致。`env.yaml` 通过只读卷挂载到容器，不会进入镜像；修改后执行 `docker-compose restart loki-mcp`。
