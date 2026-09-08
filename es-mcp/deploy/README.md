# ES-MCP 部署目录

## 服务器信息

- **服务器IP**: 10.189.91.153
- **用户名**: root
- **部署目录**: /soft/mcp-tools/es-mcp
- **服务端口**: 18005

## 文件说明

- `Dockerfile` - Docker镜像构建文件
- `docker-compose.yaml` - Docker Compose编排配置
- `server-deploy.sh` - 服务器端部署脚本
- `deploy.ps1` - Windows PowerShell一键部署脚本

## 快速部署

### PowerShell一键部署（推荐）

```powershell
cd E:\project\xcmg\ai\mcp\mcp-tools\es-mcp\deploy
.\deploy.ps1
```

按提示输入服务器密码即可完成部署。

### 手动部署

#### 1. 上传文件

```powershell
# 上传整个es-mcp目录
scp -r E:\project\xcmg\ai\mcp\mcp-tools\es-mcp root@10.189.91.153:/soft/mcp-tools/
```

#### 2. 登录服务器

```powershell
ssh root@10.189.91.153
```

#### 3. 执行部署

```bash
cd /soft/mcp-tools/es-mcp
chmod +x deploy/server-deploy.sh
bash deploy/server-deploy.sh
```

## 服务管理

```bash
# 查看状态
docker ps | grep es-mcp

# 查看日志
docker logs -f es-mcp

# 重启服务
docker restart es-mcp

# 停止服务
docker stop es-mcp

# 启动服务
docker start es-mcp
```

## 验证服务

```bash
# 本地验证
curl -X POST http://localhost:18005/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "tools/list", "id": 1}'

# 远程验证
curl -X POST http://10.189.91.153:18005/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "tools/list", "id": 1}'
```

## 故障排查

```bash
# 查看容器日志
docker logs -f es-mcp

# 进入容器调试
docker exec -it es-mcp bash

# 检查配置文件
docker exec -it es-mcp cat /etc/es-mcp/env.yaml

# 测试ES连接
docker exec -it es-mcp python -c "
from elasticsearch import Elasticsearch
es = Elasticsearch(['http://10.180.6.166:9200'], basic_auth=('admin', 'xcmg#@!MES01'))
print(es.info())
"
```
