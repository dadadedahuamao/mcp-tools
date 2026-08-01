# 安全别名匹配 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 K8s、Loki 和 DB MCP 增加一致的、安全的唯一候选别名匹配。

**Architecture:** 每个 MCP 的配置层新增纯函数解析已注册别名。解析顺序为精确、规范化、唯一业务名称包含匹配；零候选和多候选均以现有配置异常类型拒绝。服务层继续只消费已解析的配置对象。

**Tech Stack:** Python 3.12、标准库 `re` 与 `unicodedata`、pytest。

## Global Constraints

- 所有 MCP 继续只读。
- 不向调用方返回或记录 kubeconfig、密码、token 或连接串。
- 模糊匹配只能在候选唯一时生效；歧义不得自动选择。
- 保持精确匹配兼容。

---

### Task 1: K8s 集群别名解析

**Files:**

- Modify: `k8s-mcp/k8s_mcp/config.py`
- Modify: `k8s-mcp/k8s_mcp/server.py`
- Modify: `k8s-mcp/tests/test_config.py`

**Interfaces:**

- Produces: `resolve_registered_cluster(clusters: dict[str, RegisteredCluster], requested: str) -> RegisteredCluster`

- [ ] 写失败测试：`二期道路环境` 唯一命中道路二期集群；`重型` 命中多个候选时抛出包含“不唯一”的 `KubernetesConfigurationError`。
- [ ] 运行 `k8s-mcp/.venv\\Scripts\\python.exe -m pytest tests/test_config.py -v`，确认新增测试失败。
- [ ] 实现精确、规范化、唯一业务名称包含三段解析，并在 `server.reader` 中替换直接字典访问。
- [ ] 运行 `k8s-mcp/.venv\\Scripts\\python.exe -m pytest -v`。

### Task 2: Loki 环境别名解析

**Files:**

- Modify: `loki-mcp/loki_mcp/config.py`
- Modify: `loki-mcp/tests/test_config.py`

**Interfaces:**

- Produces: `resolve_environment(items: list[dict[str, Any]], requested: str) -> dict[str, Any]`

- [ ] 写失败测试：`load_environment(..., "二期道路环境")` 唯一命中 `道路(道路、筑路、养护)(二期UAT)`；歧义名称拒绝。
- [ ] 实现与 K8s 相同的匹配顺序，并运行 `loki-mcp/.venv\\Scripts\\python.exe -m pytest -v`。

### Task 3: DB 数据源别名解析

**Files:**

- Modify: `db-mcp/db_mcp/config.py`
- Modify: `db-mcp/tests/test_config.py`

**Interfaces:**

- Produces: `resolve_source(config: DatabaseConfig, requested: str) -> DatabaseSource`

- [ ] 写失败测试：规范化唯一别名可命中预注册数据源；歧义别名抛出 `DatabaseConfigurationError`。
- [ ] 实现同一规则，并运行 `db-mcp/.venv\\Scripts\\python.exe -m pytest -v`。

### Task 4: 文档与全量验证

**Files:**

- Modify: `k8s-mcp/README.md`
- Modify: `loki-mcp/README.md`
- Modify: `db-mcp/README.md`

- [ ] 写明“精确优先、唯一模糊匹配、歧义拒绝”。
- [ ] 运行三个项目的完整 pytest 套件。
- [ ] 运行 `git diff --check`，提交 `feat: add safe fuzzy alias matching`。
