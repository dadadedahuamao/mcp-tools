# Redis MCP

受限只读 Redis 生产排障 MCP。连接信息只从共享 `env.yaml` 的环境配置读取，支持 `standalone`、`sentinel` 和 `cluster`；不提供写入、删除、过期、清库、脚本、订阅或任意命令执行。

## 安装

```powershell
cd E:\Project\cxh\mcp-tools\redis-mcp
D:\Program Files\Python\Python312\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test,build]"
.\build.ps1
```

## env.yaml 配置

在共享文件的对应环境中加入 `redis`。`password` 可以省略、设为 `null` 或空字符串，此时不会发送 AUTH。真实文件含明文密码，必须 Git 忽略并限制为管理员可读。

```yaml
env:
  - env_name: "示例生产"
    redis:
      mode: standalone # standalone | sentinel | cluster
      host: redis.internal.example
      port: 6379
      database: 0
      username: readonly # 可省略
      password: "" # 可省略
      tls: false
      socket_timeout_seconds: 5
      connect_timeout_seconds: 5

  - env_name: "Sentinel 示例"
    redis:
      mode: sentinel
      master_name: mymaster
      database: 1 # A 环境可使用 DB 1；单实例和 Sentinel 支持
      password: "REPLACE_WITH_PASSWORD"
      sentinels:
        - { host: sentinel-1.internal.example, port: 26379 }

  - env_name: "Cluster 示例"
    redis:
      mode: cluster
      # Redis Cluster 固定只支持 DB 0，请省略 database 或设为 0。
      startup_nodes:
        - { host: redis-1.internal.example, port: 6379 }
        - { host: redis-2.internal.example, port: 6379 }
```

## Codex 配置

```toml
[mcp_servers.redis_readonly]
command = "E:\\Project\\cxh\\mcp-tools\\redis-mcp\\dist\\redis-mcp.exe"
args = ["--env-file", "E:\\SecureConfig\\env.yaml"]
```

可用工具：`list_redis_environments`、`get_redis_overview`、`scan_redis_keys`、`inspect_redis_key`、`get_redis_slowlog`、`get_redis_clients`、`get_redis_command_stats`。键扫描仅使用分页 `SCAN`，读取内容、条数和响应大小均受限。

## Docker

```bash
cp config/env.yaml.example config/env.yaml
cp .env.example .env
# 编辑真实 Redis 配置与 REDIS_MCP_ALLOWED_HOST
docker compose up -d --build
```

MCP 地址为 `http://<内网主机>:18003/mcp`。仅能在受信任内网使用；若需对外暴露，须在反向代理层配置 HTTPS 和企业认证。
