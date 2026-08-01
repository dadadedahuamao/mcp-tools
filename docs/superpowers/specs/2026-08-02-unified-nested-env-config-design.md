# 统一嵌套环境配置设计

## 目标

将共享 `env.yaml` 统一为按工具分组的结构。每个环境下使用 `db`、`loki`、`k8s`、`redis` 节点；DB、Loki、K8s MCP 不再读取旧的 `db_*`、`loki_*`、`k8s_*` 扁平字段。

## 新格式

```yaml
env:
  - env_name: "本地"
    db:
      connect_name: local_pg
      type: postgres
      host: localhost
      port: 5432
      name: app
      user: readonly
      password: ""
      schema: public
    loki:
      url: https://grafana.example/grafana/
      datasource_uid: loki_uid
      username: readonly
      password: ""
      query: '{namespace="default"}'
    k8s:
      namespace: default
      kubeconfig: local.yaml
      context: local
    redis:
      mode: standalone
      host: localhost
      port: 6379
      database: 10
      password: ""
```

每个 MCP 只读取自己的节点；旧扁平字段缺少对应节点时视为未配置，不做兼容回退。敏感值可为空，现有各项目的密码校验和只读权限边界保持不变。

## 实施范围

- DB：将 `env[].db` 映射到现有 `DatabaseSource`，继续支持 postgres/mysql/oracle、TLS、查询限制和明文密码。
- Loki：将 `env[].loki` 映射到现有 `LokiEnvironment`。
- K8s：将 `env[].k8s` 映射到现有环境注册表，继续从受控 kubeconfig 目录解析文件名和 context。
- 同步更新三套示例、README、测试；迁移工作区实际共享 `env.yaml`，不改变环境名称或连接目标。

## 验证

为三个解析器增加新格式成功和旧格式拒绝测试；运行 DB、Loki、K8s、Redis 全部单元测试，并对迁移后的共享配置做只读解析检查。
