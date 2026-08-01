# DB MCP

`db-mcp` 是兼容 MySQL、PostgreSQL 和 Oracle 的受限只读 MCP。它既可以本地使用 stdio，也可以在 Linux 上以 Streamable HTTP 常驻运行。服务不接受调用时提供的连接串、账号或密码，也不支持写操作、事务、存储过程调用或文件导出。

## 安装与构建

```powershell
cd E:\Project\cxh\mcp-tools\db-mcp
D:\Program Files\Python\Python312\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test,build]"
.\build.ps1
```

如需显式指定清华 PyPI 镜像：

```powershell
.\.venv\Scripts\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e ".[test,build]"
```

成功后交付 `dist\db-mcp.exe`。Oracle 使用 python-oracledb Thin 模式，不需要 Oracle Client。

## 数据源配置

将配置保存为仅管理员可读的绝对路径 YAML。每个数据源的密码可用 `password_env` 引用环境变量，或使用本地明文 `password`；两者必须二选一。

```yaml
datasources:
  mes_mysql:
    dialect: mysql
    host: mysql.internal.example
    port: 3306
    database: mes
    username: mcp_readonly
    password: "123456" # 本地明文密码；不要同时设置 password_env
    default_schema: mes
    allowed_schemas: [mes, mes_report]
    tls: true # MySQL：开启驱动 SSL
    query_limits:
      max_rows: 500
      timeout_seconds: 20
      max_response_bytes: 500000
      connect_timeout_seconds: 10

  mes_pg:
    dialect: postgresql
    host: pg.internal.example
    port: 5432
    database: mes
    username: mcp_readonly
    password_env: MES_PG_READONLY_PASSWORD
    default_schema: public
    allowed_schemas: [public, reporting]
    tls: { sslmode: verify-full, sslrootcert: "E:\\SecureConfig\\ca.pem" }

  mes_oracle:
    dialect: oracle
    host: oracle.internal.example
    port: 1521
    service_name: MESUAT
    username: MCP_READONLY
    password_env: MES_ORACLE_READONLY_PASSWORD
    default_schema: MES
    allowed_schemas: [MES, MES_REPORT]
    tls: { protocol: tcps, wallet_location: "E:\\SecureConfig\\oracle-wallet" }
```

数据库用户也必须被授予最小只读权限。MCP 的 SQL 校验是额外防线，并不能取代数据库授权。即使配置仅本地使用，仍建议将配置文件设为当前用户可读，并避免提交到 Git。

## Codex 配置

在 `C:\Users\<用户名>\.codex\config.toml` 添加：

```toml
[mcp_servers.db_readonly]
command = "E:\\Project\\cxh\\mcp-tools\\db-mcp\\dist\\db-mcp.exe"
args = [
  "--config-file",
  "E:\\SecureConfig\\db-mcp.yaml"
]

[mcp_servers.db_readonly.env]
MES_MYSQL_READONLY_PASSWORD = "从安全密钥库注入"
MES_PG_READONLY_PASSWORD = "从安全密钥库注入"
MES_ORACLE_READONLY_PASSWORD = "从安全密钥库注入"
```

重启 Codex 并新开任务后，使用数据源别名调用 `list_data_sources`、`list_tables`、`describe_table`、`query`、`explain_query`、`get_active_sessions` 和 `get_lock_summary`。

`query` 仅接受一条参数化 `SELECT` 或只读 CTE，例如：

```text
data_source: "mes_pg"
sql: "SELECT id, status FROM reporting.orders WHERE created_at >= :start"
parameters: { "start": "2026-07-31T00:00:00+08:00" }
limit: 100
```

## 安全边界

- 仅使用 YAML 中预注册的数据源；不回显密码、连接串、SQL 参数或驱动原始错误。
- SQL 使用方言解析，仅允许单条 `SELECT` / CTE；拒绝 DML、DDL、事务、过程调用、锁定读和文件导出。
- 所有对象必须属于 `allowed_schemas`；未限定 schema 的表按 `default_schema` 校验。
- 查询强制连接超时、语句超时、最大行数与最大返回字节数。
- MySQL/PG 支持只读 `EXPLAIN`；Oracle 仅读取已有游标的 `DBMS_XPLAN`，需要调用者提供可访问的 `sql_id`。
- TLS 参数按驱动方言传递：MySQL 使用 `ssl` 选项，PostgreSQL 使用 libpq 的 `sslmode` 等选项，Oracle 使用 Thin 连接描述符/钱包选项；请使用对应驱动的参数名。

## 可选集成测试

默认测试不要求 Docker。安装 `.[test,integration]` 后，设置 `TEST_DATABASE_INTEGRATION=1` 可通过 Testcontainers 启动 MySQL 与 PostgreSQL 做真实连通性测试。Oracle XE 测试需要在 CI 或本机提供受许可的 Oracle XE 实例，首版只提供 Thin 驱动单元测试与可访问游标计划查询。

## Linux Docker 部署

服务器上将项目复制到部署目录后，先创建本地配置（该文件已被 Git 忽略）：

```bash
cd db-mcp
mkdir -p config
# 将三套 MCP 共用的 env.yaml 放入 config/env.yaml
cp .env.example .env
chmod 600 config/env.yaml
chown 10001:10001 config/env.yaml
# 编辑 config/env.yaml，填入实际的内网数据库信息
# 编辑 .env，将 DB_MCP_ALLOWED_HOST 改为实际服务器 IP 或域名及端口
docker compose up -d --build
docker compose logs -f db-mcp
```

Compose 默认监听 `0.0.0.0:8000`。`.env` 中的 `DB_MCP_ALLOWED_HOST` 必须设置为实际访问 URL 中的 `IP 或域名:8000`；MCP 端点为 `http://<服务器IP或域名>:8000/mcp`。需要给其他人 URL 时，在同一服务器配置 TLS 反向代理，并使用 `deploy/nginx.conf.example` 作为模板，最终公开地址为 `https://<你的域名>/mcp`。

请在 Nginx/API 网关层接入企业 SSO、mTLS 或 Bearer Token 校验后再对外开放。当前 Compose 的默认端口开放仅适用于受信任内网；如需覆盖监听地址，修改 `.env` 中的 `DB_MCP_BIND_ADDRESS`。

没有域名、仅在受信任内网使用时，可直接通过 IP 和端口提供服务。`DB_MCP_ALLOWED_HOST` 必须与 URL 中的 `IP:端口` 一致。此模式没有 HTTPS，不应暴露到公网；公网使用必须经由 HTTPS 和认证反向代理。

容器启动命令等价于：

```bash
python main.py --config-file /etc/db-mcp/env.yaml --transport streamable-http
```
