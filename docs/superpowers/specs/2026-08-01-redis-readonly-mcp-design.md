# Redis 只读排障 MCP 设计

## 目标

新增 `redis-mcp`，以现有项目共享的 `env.yaml` 为唯一配置来源，按环境连接 Redis 并提供面向生产排障的受限只读查询能力。支持单实例/主从、Sentinel 和 Cluster。

## 配置

每个 `env` 条目可包含独立的 `redis` 节点：

```yaml
env:
  - env_name: 矿机(一期生产)
    redis:
      mode: cluster # standalone | sentinel | cluster
      username: mcp_readonly # 可省略
      password: "" # 可省略、null 或空字符串；均不发送 AUTH
      tls: false
      socket_timeout_seconds: 5
      connect_timeout_seconds: 5

      # standalone
      host: redis.internal.example
      port: 6379
      database: 0

      # sentinel
      # master_name: mymaster
      # sentinels:
      #   - { host: sentinel-1.internal.example, port: 26379 }

      # cluster
      # startup_nodes:
      #   - { host: redis-1.internal.example, port: 6379 }
```

`mode` 决定合法字段：`standalone` 要求 `host`/`port`，`sentinel` 要求 `master_name`/`sentinels`，`cluster` 要求 `startup_nodes`。配置不接受调用时覆盖；只使用预注册环境。

密码可以直接写入 YAML，工具返回、日志和错误信息不得回显密码、用户名、连接地址或其他认证细节。配置文件必须排除在版本控制之外，并仅向管理员开放读取权限。

## MCP 工具

- `list_redis_environments`：返回可用环境名称和部署模式，不返回连接细节。
- `get_redis_overview`：汇总 Redis `INFO` 的服务、复制、内存、持久化、统计、CPU、键空间信息；Cluster 附加节点/槽位健康摘要；Sentinel 附加主节点与 Sentinel 健康摘要。
- `scan_redis_keys`：仅以 `SCAN`（Cluster 时安全遍历各节点）列举键，支持 `match`、分页游标和受限 `count`；禁止 `KEYS`。
- `inspect_redis_key`：返回类型、TTL、内存占用及按类型截断后的内容或结构摘要，支持 string/hash/list/set/zset/stream。
- `get_redis_slowlog`：读取受限数量的最近慢日志；禁止重置。
- `get_redis_clients`：返回连接统计和受限客户端摘要，避免回显可能包含敏感数据的客户端名称。
- `get_redis_command_stats`：返回命令调用量和耗时统计。

## 安全与可靠性

不提供原生命令代理，且明确拒绝写入、删除、过期、清库、订阅、脚本执行和其他会改变状态或长期占用连接的操作。每个请求使用固定的连接和命令超时、结果条数与响应大小上限。值读取和列表遍历必须截断。

错误使用中文、可操作的说明，涵盖未配置环境、认证失败、连接失败、超时和集群重定向，但不得泄露端点或凭据。

## 验证与交付

实现沿用 `loki-mcp` 的 Python/FastMCP 项目组织，提供本地 stdio 和 Docker Streamable HTTP 启动方式。测试覆盖三类部署的配置解析、空密码、客户端参数、只读访问边界、扫描分页及结果截断。README 说明安装、配置、Codex 接入、部署及敏感 YAML 的管理要求。
