# OBS MCP

受限、只读的 S3/MinIO 兼容 OBS 查询 MCP。它只支持列对象、读取对象元数据与受大小限制的文本读取；不提供上传、删除、复制或 ACL 修改。

在共享 `env.yaml` 的环境节点中配置 `obs`，格式见 [config/env.yaml.example](config/env.yaml.example)。真实 AK/SK 只可放在受保护且 Git 忽略的配置文件中。

```toml
[mcp_servers.obs_readonly]
command = "E:\\Project\\cxh\\mcp-tools\\obs-mcp\\.venv\\Scripts\\python.exe"
args = ["E:\\Project\\cxh\\mcp-tools\\obs-mcp\\main.py", "--env-file", "E:\\SecureConfig\\env.yaml"]
```

可用工具：`list_obs_environments`、`list_obs_buckets`、`list_obs_objects`、`get_obs_object_metadata`、`read_obs_text_object`。

## Docker

```bash
cp config/env.yaml.example config/env.yaml
cp .env.example .env
# Edit config/env.yaml to use real AK/SK and .env to set OBS_MCP_ALLOWED_HOST.
docker compose up -d --build
```

The service is available at `http://<internal-host>:18004/mcp`. Keep it on a trusted internal network; if it must be exposed externally, terminate TLS and enforce enterprise authentication in a reverse proxy.
