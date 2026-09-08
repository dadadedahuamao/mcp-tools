"""受限只读的 Elasticsearch 集群、节点和索引诊断。"""

from typing import Any

from es_mcp.client import ElasticsearchQueryError, _get_index_pattern, _validate_index_access
from es_mcp.config import ElasticsearchEnvironment


def _allowed(config: ElasticsearchEnvironment, index_type: str) -> str:
    pattern = _get_index_pattern(index_type)
    if not _validate_index_access(pattern, config.index_allowlist):
        raise ElasticsearchQueryError(f"索引 {pattern} 不在允许访问列表中")
    return pattern


def _num(v: Any) -> int:
    try:
        return int(float(str(v or 0).rstrip("%")))
    except (TypeError, ValueError):
        return 0


def cluster_health(client: Any, config: ElasticsearchEnvironment) -> dict:
    try:
        h = client.cluster.health()
        return {k: h.get(k) for k in ("cluster_name", "status", "number_of_nodes", "number_of_data_nodes", "active_primary_shards", "active_shards", "relocating_shards", "initializing_shards", "unassigned_shards", "delayed_unassigned_shards", "number_of_pending_tasks")}
    except Exception as e:
        raise ElasticsearchQueryError("Elasticsearch 集群健康查询失败") from e


def node_health(client: Any, config: ElasticsearchEnvironment) -> dict:
    try:
        data = client.nodes.stats(metric="os,process,jvm,fs,thread_pool,indices").get("nodes", {})
        nodes = []
        for node_id, n in data.items():
            mem = n.get("jvm", {}).get("mem", {})
            fs = n.get("fs", {}).get("total", {}).get("total_in_bytes", 0)
            avail = n.get("fs", {}).get("total", {}).get("available_in_bytes", 0)
            nodes.append({"id": node_id, "name": n.get("name"), "roles": n.get("roles", []), "cpu_percent": n.get("os", {}).get("cpu", {}).get("percent"), "heap_percent": mem.get("heap_used_percent"), "heap_used_bytes": mem.get("heap_used_in_bytes"), "disk_total_bytes": fs, "disk_available_bytes": avail, "disk_percent": round((fs-avail) * 100 / fs, 2) if fs else 0.0, "search_rejected": n.get("thread_pool", {}).get("search", {}).get("rejected", 0), "write_rejected": n.get("thread_pool", {}).get("write", {}).get("rejected", 0)})
        return {"nodes": nodes}
    except Exception as e:
        raise ElasticsearchQueryError("Elasticsearch 节点健康查询失败") from e


def shard_allocation(client: Any, config: ElasticsearchEnvironment, index_type: str, *, unassigned_only: bool = False, limit: int = 200) -> dict:
    pattern = _allowed(config, index_type)
    try:
        rows = client.cat.shards(index=pattern, format="json", bytes="b", h="index,shard,prirep,state,docs,store,node,unassigned.reason")
        if unassigned_only:
            rows = [r for r in rows if r.get("state") == "UNASSIGNED"]
        items = [{"index": r.get("index"), "shard": _num(r.get("shard")), "role": r.get("prirep"), "state": r.get("state"), "docs": _num(r.get("docs")), "size_bytes": _num(r.get("store")), "node": r.get("node"), "unassigned_reason": r.get("unassigned.reason")} for r in rows[:max(1, min(limit, 500))]]
        return {"index_pattern": pattern, "total": len(rows), "truncated": len(rows) > len(items), "unassigned_only": unassigned_only, "shards": items}
    except Exception as e:
        raise ElasticsearchQueryError("Elasticsearch 分片分配查询失败") from e


def index_mapping(client: Any, config: ElasticsearchEnvironment, index_type: str) -> dict:
    pattern = _allowed(config, index_type)
    try:
        return {"index_pattern": pattern, "mappings": client.indices.get_mapping(index=pattern)}
    except Exception as e:
        raise ElasticsearchQueryError("索引映射查询失败") from e


def index_settings(client: Any, config: ElasticsearchEnvironment, index_type: str) -> dict:
    pattern = _allowed(config, index_type)
    try:
        raw = client.indices.get_settings(index=pattern).get("indices", {})
        keys = {"number_of_shards", "number_of_replicas", "refresh_interval", "max_result_window", "blocks", "routing", "translog"}
        return {"index_pattern": pattern, "indices": {name: {"settings": {k: v for k, v in value.get("settings", {}).get("index", {}).items() if k in keys or any(k.startswith(x) for x in ("number_of_", "refresh", "blocks.", "routing.", "translog."))}} for name, value in raw.items()}}
    except Exception as e:
        raise ElasticsearchQueryError("索引设置查询失败") from e


def index_aliases(client: Any, config: ElasticsearchEnvironment, index_type: str) -> dict:
    pattern = _allowed(config, index_type)
    try:
        return {"index_pattern": pattern, "aliases": client.indices.get_alias(index=pattern)}
    except Exception as e:
        raise ElasticsearchQueryError("索引别名查询失败") from e


def index_templates(client: Any, config: ElasticsearchEnvironment, name: str | None = None) -> dict:
    try:
        kwargs = {"name": name} if name else {}
        try:
            templates = client.indices.get_index_template(**kwargs)
            return {"api": "_index_template", "templates": templates.get("index_templates", [])}
        except Exception:
            templates = client.indices.get_template(**kwargs)
            return {"api": "_template", "templates": templates}
    except Exception as e:
        raise ElasticsearchQueryError("Elasticsearch 模板查询失败") from e


def index_retention(client: Any, config: ElasticsearchEnvironment, index_type: str, *, limit: int = 200) -> dict:
    pattern = _allowed(config, index_type)
    try:
        rows = client.cat.indices(index=pattern, format="json", bytes="b", h="index,docs.count,store.size")
        rows = rows[:max(1, min(limit, 500))]
        return {"index_pattern": pattern, "index_count": len(rows), "indices": [{"index": r.get("index"), "docs": _num(r.get("docs.count")), "size_bytes": _num(r.get("store.size"))} for r in rows]}
    except Exception as e:
        raise ElasticsearchQueryError("索引保留周期查询失败") from e
