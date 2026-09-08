"""受限的 Elasticsearch 只读接口日志查询操作。"""

import fnmatch
import time
from datetime import datetime, timezone
from typing import Any

from es_mcp.config import ElasticsearchEnvironment

# OpenApi 日志字段映射
OPENAPI_FIELDS = {
    "id", "route", "appName", "model", "service", "startTime", "ip", "endTime",
    "duration", "statusCode", "statusDesc", "businessCode", "businessMessage",
    "invokeUser", "invokeUserId", "invokeTenantId", "invokeKey",
}

# 第三方接口日志字段映射
THIRDAPI_FIELDS = {
    "id", "apiAddr", "apiCode", "apiName", "plateformId", "plateformName",
    "originReqBody", "invokeUser", "invokeUserId", "invokeTenantId",
    "startTime", "endTime", "duration", "statusCode", "statusDesc",
    "thirdRespBody", "thirdRespHeader", "intfReqBody", "intfReqHeader", "intfRespStr",
}

# 索引模式映射
INDEX_PATTERNS = {
    "openapi": "*open_api_log*",
    "thirdapi": "*third_api_log*",
}

# 安全响应字段（排除敏感信息）
SAFE_FIELDS_OPENAPI = [
    "id", "route", "appName", "model", "service", "startTime", "ip", "endTime",
    "duration", "statusCode", "statusDesc", "businessCode", "businessMessage",
    "invokeUser", "invokeTenantId", "invokeKey",
]

SAFE_FIELDS_THIRDAPI = [
    "id", "apiAddr", "apiCode", "apiName", "plateformId", "plateformName",
    "invokeUser", "invokeTenantId", "startTime", "endTime", "duration",
    "statusCode", "statusDesc",
]


class ElasticsearchQueryError(ValueError):
    """Elasticsearch 查询错误。"""


def _validate_index_access(index_pattern: str, allowlist: frozenset[str]) -> bool:
    """校验索引模式是否在白名单内。"""
    for allowed in allowlist:
        if fnmatch.fnmatch(index_pattern, allowed):
            return True
    return False


def _get_index_pattern(index_type: str) -> str:
    """根据索引类型获取索引模式。"""
    pattern = INDEX_PATTERNS.get(index_type)
    if not pattern:
        raise ElasticsearchQueryError(f"不支持的索引类型：{index_type}，支持的类型：openapi, thirdapi")
    return pattern


def _timestamp_to_millis(timestamp: str) -> int:
    """将 ISO 8601 时间戳转换为毫秒时间戳。"""
    try:
        # 支持多种格式
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(timestamp, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)
            except ValueError:
                continue
        raise ElasticsearchQueryError(f"无法解析时间格式：{timestamp}")
    except Exception as error:
        raise ElasticsearchQueryError(f"时间格式错误：{timestamp}") from error


def _safe_error(error: Exception) -> str:
    """返回安全的错误信息，不泄露内部细节。"""
    error_msg = str(error).lower()
    if "connection" in error_msg or "timeout" in error_msg:
        return "Elasticsearch 连接超时或不可达"
    elif "auth" in error_msg or "security" in error_msg:
        return "Elasticsearch 认证失败"
    elif "index" in error_msg and "not_found" in error_msg:
        return "索引不存在或无权访问"
    else:
        return "Elasticsearch 查询失败"


def _truncate_response(data: dict, max_bytes: int) -> dict:
    """截断响应以保护内存。"""
    import json
    serialized = json.dumps(data, ensure_ascii=False)
    if len(serialized.encode("utf-8")) > max_bytes:
        # 简单截断策略
        truncated = serialized[:max_bytes // 2] + "...[响应已截断]"
        return {"truncated": True, "preview": truncated}
    return data


def create_client(config: ElasticsearchEnvironment) -> Any:
    """创建 Elasticsearch 客户端连接。"""
    try:
        from elasticsearch import Elasticsearch
        hosts = list(config.hosts)
        kwargs: dict[str, Any] = {
            "hosts": hosts,
            "request_timeout": config.timeout_seconds,
        }
        if config.username and config.password:
            kwargs["basic_auth"] = (config.username, config.password)
        return Elasticsearch(**kwargs)
    except Exception as error:
        raise ElasticsearchQueryError("Elasticsearch 连接初始化失败") from error


def search_logs(
    client: Any,
    config: ElasticsearchEnvironment,
    index_type: str,
    *,
    start_time: str | None = None,
    end_time: str | None = None,
    route: str | None = None,
    status_code: str | None = None,
    invoke_user: str | None = None,
    page: int = 1,
    size: int = 20,
) -> dict:
    """搜索接口日志。"""
    try:
        index_pattern = _get_index_pattern(index_type)
        if not _validate_index_access(index_pattern, config.index_allowlist):
            raise ElasticsearchQueryError(f"索引 {index_pattern} 不在允许访问列表中")

        # 构建查询
        must_conditions: list[dict] = []

        # 时间范围过滤
        if start_time or end_time:
            time_range: dict[str, str] = {}
            if start_time:
                time_range["gte"] = start_time
            if end_time:
                time_range["lte"] = end_time
            must_conditions.append({"range": {"startTime": time_range}})

        # 路由过滤
        if route:
            if index_type == "openapi":
                must_conditions.append({"wildcard": {"route": f"*{route}*"}})
            else:
                must_conditions.append({"wildcard": {"apiAddr": f"*{route}*"}})

        # 状态码过滤
        if status_code:
            must_conditions.append({"term": {"statusCode": status_code}})

        # 调用用户过滤
        if invoke_user:
            must_conditions.append({"wildcard": {"invokeUser": f"*{invoke_user}*"}})

        query = {"bool": {"must": must_conditions}} if must_conditions else {"match_all": {}}

        # 分页限制
        size = min(max(size, 1), 100)
        from_val = max((page - 1) * size, 0)
        if from_val + size > config.max_result_window:
            raise ElasticsearchQueryError(f"分页超出限制，最大支持 {config.max_result_window} 条")

        # 执行搜索
        result = client.search(
            index=index_pattern,
            query=query,
            from_=from_val,
            size=size,
            sort=[{"startTime": {"order": "desc"}}],
        )

        # 处理结果
        hits = result.get("hits", {}).get("hits", [])
        total = result.get("hits", {}).get("total", {}).get("value", 0)

        # 安全化字段
        safe_fields = SAFE_FIELDS_OPENAPI if index_type == "openapi" else SAFE_FIELDS_THIRDAPI
        safe_hits = []
        for hit in hits:
            source = hit.get("_source", {})
            safe_hit = {k: v for k, v in source.items() if k in safe_fields}
            safe_hit["id"] = hit.get("_id", source.get("id"))
            safe_hits.append(safe_hit)

        return {"total": total, "page": page, "size": size, "hits": safe_hits}
    except ElasticsearchQueryError:
        raise
    except Exception as error:
        raise ElasticsearchQueryError(_safe_error(error)) from error


def get_log_detail(
    client: Any,
    config: ElasticsearchEnvironment,
    index_type: str,
    log_id: str,
) -> dict:
    """获取单条日志详情。"""
    try:
        index_pattern = _get_index_pattern(index_type)
        if not _validate_index_access(index_pattern, config.index_allowlist):
            raise ElasticsearchQueryError(f"索引 {index_pattern} 不在允许访问列表中")

        result = client.get(index=index_pattern, id=log_id)
        source = result.get("_source", {})

        # 返回完整字段（但排除请求体等大字段的安全性由调用方控制）
        return {"id": result.get("_id"), "index": result.get("_index"), "source": source}
    except ElasticsearchQueryError:
        raise
    except Exception as error:
        if "not_found" in str(error).lower():
            raise ElasticsearchQueryError(f"日志不存在：{log_id}")
        raise ElasticsearchQueryError(_safe_error(error)) from error


def get_index_stats(
    client: Any,
    config: ElasticsearchEnvironment,
    index_type: str,
) -> dict:
    """获取索引统计信息。"""
    try:
        index_pattern = _get_index_pattern(index_type)
        if not _validate_index_access(index_pattern, config.index_allowlist):
            raise ElasticsearchQueryError(f"索引 {index_pattern} 不在允许访问列表中")

        # 获取索引统计
        stats = client.indices.stats(index=index_pattern)

        # 提取关键信息
        indices = stats.get("indices", {})
        total_docs = 0
        total_size = 0
        index_details = []

        for index_name, index_data in indices.items():
            docs = index_data.get("primaries", {}).get("docs", {}).get("count", 0)
            size = index_data.get("primaries", {}).get("store", {}).get("size_in_bytes", 0)
            total_docs += docs
            total_size += size
            index_details.append({
                "index": index_name,
                "docs": docs,
                "size_bytes": size,
                "size_human": f"{size / 1024 / 1024:.2f} MB",
            })

        return {
            "pattern": index_pattern,
            "total_indices": len(indices),
            "total_docs": total_docs,
            "total_size_bytes": total_size,
            "total_size_human": f"{total_size / 1024 / 1024:.2f} MB",
            "indices": index_details[:20],  # 限制返回数量
        }
    except ElasticsearchQueryError:
        raise
    except Exception as error:
        raise ElasticsearchQueryError(_safe_error(error)) from error


def _number_value(value: Any) -> int:
    """将 Cat API 的数值字段安全转换为整数。"""
    try:
        return int(float(str(value or "0")))
    except (TypeError, ValueError):
        return 0


def _percent_value(value: Any) -> float:
    """将 Cat API 的百分比字段安全转换为浮点数。"""
    try:
        return float(str(value or "0").rstrip("%"))
    except (TypeError, ValueError):
        return 0.0


def get_shard_capacity(
    client: Any,
    config: ElasticsearchEnvironment,
    index_type: str,
    *,
    shard_limit: int = 100,
) -> dict:
    """获取白名单索引的分片状态，以及集群节点的磁盘使用情况。"""
    try:
        index_pattern = _get_index_pattern(index_type)
        if not _validate_index_access(index_pattern, config.index_allowlist):
            raise ElasticsearchQueryError(f"索引 {index_pattern} 不在允许访问列表中")

        shard_limit = min(max(shard_limit, 1), 100)
        shard_rows = client.cat.shards(
            index=index_pattern,
            format="json",
            bytes="b",
            h="index,shard,prirep,state,docs,store,node",
        )
        allocation_rows = client.cat.allocation(
            format="json",
            bytes="b",
            h="node,shards,disk.indices,disk.used,disk.avail,disk.total,disk.percent",
        )

        shards = [
            {
                "index": row.get("index", ""),
                "shard": _number_value(row.get("shard")),
                "role": row.get("prirep", ""),
                "state": row.get("state", ""),
                "docs": _number_value(row.get("docs")),
                "size_bytes": _number_value(row.get("store")),
                "node": row.get("node", ""),
            }
            for row in shard_rows[:shard_limit]
        ]
        nodes = [
            {
                "node": row.get("node", ""),
                "shard_count": _number_value(row.get("shards")),
                "index_size_bytes": _number_value(row.get("disk.indices")),
                "disk_used_bytes": _number_value(row.get("disk.used")),
                "disk_available_bytes": _number_value(row.get("disk.avail")),
                "disk_total_bytes": _number_value(row.get("disk.total")),
                "disk_percent": _percent_value(row.get("disk.percent")),
            }
            for row in allocation_rows
            if row.get("node") and row.get("node") != "UNASSIGNED"
        ]
        return {
            "index_pattern": index_pattern,
            "summary": {
                "total_shards": len(shard_rows),
                "primary_shards": sum(row.get("prirep") == "p" for row in shard_rows),
                "replica_shards": sum(row.get("prirep") == "r" for row in shard_rows),
                "unassigned_shards": sum(row.get("state") == "UNASSIGNED" for row in shard_rows),
            },
            "shards": shards,
            "shards_truncated": len(shard_rows) > shard_limit,
            "nodes": nodes,
        }
    except ElasticsearchQueryError:
        raise
    except Exception as error:
        raise ElasticsearchQueryError(_safe_error(error)) from error


def get_api_statistics(
    client: Any,
    config: ElasticsearchEnvironment,
    index_type: str,
    *,
    start_time: str | None = None,
    end_time: str | None = None,
    route: str | None = None,
) -> dict:
    """获取接口调用统计（次数、平均耗时、错误率）。"""
    try:
        index_pattern = _get_index_pattern(index_type)
        if not _validate_index_access(index_pattern, config.index_allowlist):
            raise ElasticsearchQueryError(f"索引 {index_pattern} 不在允许访问列表中")

        # 构建查询
        must_conditions: list[dict] = []
        if start_time or end_time:
            time_range: dict[str, str] = {}
            if start_time:
                time_range["gte"] = start_time
            if end_time:
                time_range["lte"] = end_time
            must_conditions.append({"range": {"startTime": time_range}})

        if route:
            field = "route" if index_type == "openapi" else "apiAddr"
            must_conditions.append({"wildcard": {field: f"*{route}*"}})

        query = {"bool": {"must": must_conditions}} if must_conditions else {"match_all": {}}

        # 聚合查询
        aggs = {
            "total_count": {"value_count": {"field": "id"}},
            "avg_duration": {"avg": {"field": "duration"}},
            "max_duration": {"max": {"field": "duration"}},
            "min_duration": {"min": {"field": "duration"}},
            "duration_percentiles": {"percentiles": {"field": "duration", "percents": [50, 90, 95, 99]}},
            "status_codes": {"terms": {"field": "statusCode", "size": 10}},
        }

        result = client.search(
            index=index_pattern,
            query=query,
            aggs=aggs,
            size=0,  # 只返回聚合结果
        )

        aggregations = result.get("aggregations", {})
        total = result.get("hits", {}).get("total", {}).get("value", 0)

        # 计算错误率
        status_buckets = aggregations.get("status_codes", {}).get("buckets", [])
        error_count = sum(
            bucket.get("doc_count", 0)
            for bucket in status_buckets
            if bucket.get("key", "").startswith(("4", "5"))
        )
        error_rate = (error_count / total * 100) if total > 0 else 0

        return {
            "total_requests": total,
            "avg_duration_ms": round(aggregations.get("avg_duration", {}).get("value", 0) or 0, 2),
            "max_duration_ms": aggregations.get("max_duration", {}).get("value", 0),
            "min_duration_ms": aggregations.get("min_duration", {}).get("value", 0),
            "duration_percentiles": aggregations.get("duration_percentiles", {}).get("values", {}),
            "status_codes": {bucket["key"]: bucket["doc_count"] for bucket in status_buckets},
            "error_count": error_count,
            "error_rate_percent": round(error_rate, 2),
        }
    except ElasticsearchQueryError:
        raise
    except Exception as error:
        raise ElasticsearchQueryError(_safe_error(error)) from error


def get_slow_apis(
    client: Any,
    config: ElasticsearchEnvironment,
    index_type: str,
    *,
    start_time: str | None = None,
    end_time: str | None = None,
    top_n: int = 10,
) -> dict:
    """获取慢查询接口排行。"""
    try:
        index_pattern = _get_index_pattern(index_type)
        if not _validate_index_access(index_pattern, config.index_allowlist):
            raise ElasticsearchQueryError(f"索引 {index_pattern} 不在允许访问列表中")

        # 构建查询
        must_conditions: list[dict] = []
        if start_time or end_time:
            time_range: dict[str, str] = {}
            if start_time:
                time_range["gte"] = start_time
            if end_time:
                time_range["lte"] = end_time
            must_conditions.append({"range": {"startTime": time_range}})

        query = {"bool": {"must": must_conditions}} if must_conditions else {"match_all": {}}

        # 聚合查询：按路由分组，计算平均耗时
        route_field = "route" if index_type == "openapi" else "apiAddr"
        top_n = min(max(top_n, 1), 50)

        aggs = {
            "slow_apis": {
                "terms": {
                    "field": f"{route_field}.keyword",
                    "size": top_n,
                    "order": {"avg_duration": "desc"},
                },
                "aggs": {
                    "avg_duration": {"avg": {"field": "duration"}},
                    "max_duration": {"max": {"field": "duration"}},
                    "request_count": {"value_count": {"field": "id"}},
                },
            }
        }

        result = client.search(
            index=index_pattern,
            query=query,
            aggs=aggs,
            size=0,
        )

        buckets = result.get("aggregations", {}).get("slow_apis", {}).get("buckets", [])

        return {
            "slow_apis": [
                {
                    "route": bucket["key"],
                    "avg_duration_ms": round(bucket.get("avg_duration", {}).get("value", 0) or 0, 2),
                    "max_duration_ms": bucket.get("max_duration", {}).get("value", 0),
                    "request_count": bucket.get("request_count", {}).get("value", 0),
                }
                for bucket in buckets
            ]
        }
    except ElasticsearchQueryError:
        raise
    except Exception as error:
        raise ElasticsearchQueryError(_safe_error(error)) from error


def get_error_apis(
    client: Any,
    config: ElasticsearchEnvironment,
    index_type: str,
    *,
    start_time: str | None = None,
    end_time: str | None = None,
    top_n: int = 10,
) -> dict:
    """获取错误接口排行。"""
    try:
        index_pattern = _get_index_pattern(index_type)
        if not _validate_index_access(index_pattern, config.index_allowlist):
            raise ElasticsearchQueryError(f"索引 {index_pattern} 不在允许访问列表中")

        # 构建查询：只查询错误请求（状态码以4或5开头）
        must_conditions: list[dict] = [
            {"regexp": {"statusCode": "[45]\\d{2}"}},
        ]

        if start_time or end_time:
            time_range: dict[str, str] = {}
            if start_time:
                time_range["gte"] = start_time
            if end_time:
                time_range["lte"] = end_time
            must_conditions.append({"range": {"startTime": time_range}})

        query = {"bool": {"must": must_conditions}}

        # 聚合查询：按路由分组，统计错误数量
        route_field = "route" if index_type == "openapi" else "apiAddr"
        top_n = min(max(top_n, 1), 50)

        aggs = {
            "error_apis": {
                "terms": {
                    "field": f"{route_field}.keyword",
                    "size": top_n,
                    "order": {"error_count": "desc"},
                },
                "aggs": {
                    "error_count": {"value_count": {"field": "id"}},
                    "status_codes": {"terms": {"field": "statusCode", "size": 10}},
                    "avg_duration": {"avg": {"field": "duration"}},
                },
            }
        }

        result = client.search(
            index=index_pattern,
            query=query,
            aggs=aggs,
            size=0,
        )

        buckets = result.get("aggregations", {}).get("error_apis", {}).get("buckets", [])

        return {
            "error_apis": [
                {
                    "route": bucket["key"],
                    "error_count": bucket.get("error_count", {}).get("value", 0),
                    "status_codes": {b["key"]: b["doc_count"] for b in bucket.get("status_codes", {}).get("buckets", [])},
                    "avg_duration_ms": round(bucket.get("avg_duration", {}).get("value", 0) or 0, 2),
                }
                for bucket in buckets
            ]
        }
    except ElasticsearchQueryError:
        raise
    except Exception as error:
        raise ElasticsearchQueryError(_safe_error(error)) from error


def analyze_api_trend(
    client: Any,
    config: ElasticsearchEnvironment,
    index_type: str,
    *,
    start_time: str | None = None,
    end_time: str | None = None,
    route: str | None = None,
    interval: str = "hour",
) -> dict:
    """分析接口调用趋势（按小时或天）。"""
    try:
        index_pattern = _get_index_pattern(index_type)
        if not _validate_index_access(index_pattern, config.index_allowlist):
            raise ElasticsearchQueryError(f"索引 {index_pattern} 不在允许访问列表中")

        # 构建查询
        must_conditions: list[dict] = []
        if start_time or end_time:
            time_range: dict[str, str] = {}
            if start_time:
                time_range["gte"] = start_time
            if end_time:
                time_range["lte"] = end_time
            must_conditions.append({"range": {"startTime": time_range}})

        if route:
            field = "route" if index_type == "openapi" else "apiAddr"
            must_conditions.append({"wildcard": {field: f"*{route}*"}})

        query = {"bool": {"must": must_conditions}} if must_conditions else {"match_all": {}}

        # 时间间隔
        interval_map = {"hour": "1h", "day": "1d"}
        calendar_interval = interval_map.get(interval, "1h")

        # 聚合查询：按时间直方图
        aggs = {
            "trend": {
                "date_histogram": {
                    "field": "startTime",
                    "calendar_interval": calendar_interval,
                    "format": "yyyy-MM-dd HH:mm",
                },
                "aggs": {
                    "request_count": {"value_count": {"field": "id"}},
                    "avg_duration": {"avg": {"field": "duration"}},
                    "error_count": {
                        "filter": {"regexp": {"statusCode": "[45]\\d{2}"}},
                    },
                },
            }
        }

        result = client.search(
            index=index_pattern,
            query=query,
            aggs=aggs,
            size=0,
        )

        buckets = result.get("aggregations", {}).get("trend", {}).get("buckets", [])

        return {
            "interval": interval,
            "trend": [
                {
                    "time": bucket["key_as_string"],
                    "request_count": bucket.get("request_count", {}).get("value", 0),
                    "avg_duration_ms": round(bucket.get("avg_duration", {}).get("value", 0) or 0, 2),
                    "error_count": bucket.get("error_count", {}).get("doc_count", 0),
                }
                for bucket in buckets
            ]
        }
    except ElasticsearchQueryError:
        raise
    except Exception as error:
        raise ElasticsearchQueryError(_safe_error(error)) from error


def get_api_distribution(
    client: Any,
    config: ElasticsearchEnvironment,
    index_type: str,
    *,
    start_time: str | None = None,
    end_time: str | None = None,
    group_by: str = "statusCode",
) -> dict:
    """获取接口调用分布统计。"""
    try:
        index_pattern = _get_index_pattern(index_type)
        if not _validate_index_access(index_pattern, config.index_allowlist):
            raise ElasticsearchQueryError(f"索引 {index_pattern} 不在允许访问列表中")

        # 校验分组字段
        allowed_group_fields = {"statusCode", "appName", "plateformName"}
        if group_by not in allowed_group_fields:
            raise ElasticsearchQueryError(f"不支持的分组字段：{group_by}，支持的字段：{', '.join(allowed_group_fields)}")

        # 构建查询
        must_conditions: list[dict] = []
        if start_time or end_time:
            time_range: dict[str, str] = {}
            if start_time:
                time_range["gte"] = start_time
            if end_time:
                time_range["lte"] = end_time
            must_conditions.append({"range": {"startTime": time_range}})

        query = {"bool": {"must": must_conditions}} if must_conditions else {"match_all": {}}

        # 聚合查询
        field = f"{group_by}.keyword" if group_by != "statusCode" else group_by
        aggs = {
            "distribution": {
                "terms": {
                    "field": field,
                    "size": 20,
                },
                "aggs": {
                    "request_count": {"value_count": {"field": "id"}},
                    "avg_duration": {"avg": {"field": "duration"}},
                },
            }
        }

        result = client.search(
            index=index_pattern,
            query=query,
            aggs=aggs,
            size=0,
        )

        buckets = result.get("aggregations", {}).get("distribution", {}).get("buckets", [])

        return {
            "group_by": group_by,
            "distribution": [
                {
                    "key": bucket["key"],
                    "request_count": bucket.get("request_count", {}).get("value", 0),
                    "avg_duration_ms": round(bucket.get("avg_duration", {}).get("value", 0) or 0, 2),
                }
                for bucket in buckets
            ]
        }
    except ElasticsearchQueryError:
        raise
    except Exception as error:
        raise ElasticsearchQueryError(_safe_error(error)) from error


def search_error_logs(
    client: Any,
    config: ElasticsearchEnvironment,
    index_type: str,
    *,
    start_time: str | None = None,
    end_time: str | None = None,
    error_type: str = "all",
    keyword: str | None = None,
    page: int = 1,
    size: int = 20,
) -> dict:
    """搜索错误日志。"""
    try:
        index_pattern = _get_index_pattern(index_type)
        if not _validate_index_access(index_pattern, config.index_allowlist):
            raise ElasticsearchQueryError(f"索引 {index_pattern} 不在允许访问列表中")

        # 构建查询
        must_conditions: list[dict] = []

        # 时间范围过滤
        if start_time or end_time:
            time_range: dict[str, str] = {}
            if start_time:
                time_range["gte"] = start_time
            if end_time:
                time_range["lte"] = end_time
            must_conditions.append({"range": {"startTime": time_range}})

        # 错误类型过滤
        if error_type == "http_error":
            must_conditions.append({"regexp": {"statusCode": "[45]\\d{2}"}})
        elif error_type == "business_error":
            if index_type == "openapi":
                must_conditions.append({"bool": {"must_not": [{"term": {"businessCode": "S"}}]}})
            else:
                must_conditions.append({"bool": {"must_not": [{"term": {"statusCode": "200"}}]}})

        # 关键词搜索
        if keyword:
            if index_type == "openapi":
                must_conditions.append({
                    "multi_match": {
                        "query": keyword,
                        "fields": ["route", "appName", "service", "businessMessage"],
                    }
                })
            else:
                must_conditions.append({
                    "multi_match": {
                        "query": keyword,
                        "fields": ["apiAddr", "apiCode", "apiName"],
                    }
                })

        query = {"bool": {"must": must_conditions}} if must_conditions else {"match_all": {}}

        # 分页
        size = min(max(size, 1), 100)
        from_val = max((page - 1) * size, 0)

        # 执行搜索
        result = client.search(
            index=index_pattern,
            query=query,
            from_=from_val,
            size=size,
            sort=[{"startTime": {"order": "desc"}}],
        )

        hits = result.get("hits", {}).get("hits", [])
        total = result.get("hits", {}).get("total", {}).get("value", 0)

        # 安全化字段
        safe_fields = SAFE_FIELDS_OPENAPI if index_type == "openapi" else SAFE_FIELDS_THIRDAPI
        safe_hits = []
        for hit in hits:
            source = hit.get("_source", {})
            safe_hit = {k: v for k, v in source.items() if k in safe_fields}
            safe_hit["id"] = hit.get("_id", source.get("id"))
            safe_hits.append(safe_hit)

        return {"total": total, "page": page, "size": size, "hits": safe_hits}
    except ElasticsearchQueryError:
        raise
    except Exception as error:
        raise ElasticsearchQueryError(_safe_error(error)) from error


def analyze_slow_requests(
    client: Any,
    config: ElasticsearchEnvironment,
    index_type: str,
    *,
    start_time: str | None = None,
    end_time: str | None = None,
    min_duration: int = 1000,
    route: str | None = None,
    page: int = 1,
    size: int = 20,
) -> dict:
    """分析慢请求。"""
    try:
        index_pattern = _get_index_pattern(index_type)
        if not _validate_index_access(index_pattern, config.index_allowlist):
            raise ElasticsearchQueryError(f"索引 {index_pattern} 不在允许访问列表中")

        # 构建查询
        must_conditions: list[dict] = [
            {"range": {"duration": {"gte": min_duration}}},
        ]

        if start_time or end_time:
            time_range: dict[str, str] = {}
            if start_time:
                time_range["gte"] = start_time
            if end_time:
                time_range["lte"] = end_time
            must_conditions.append({"range": {"startTime": time_range}})

        if route:
            field = "route" if index_type == "openapi" else "apiAddr"
            must_conditions.append({"wildcard": {field: f"*{route}*"}})

        query = {"bool": {"must": must_conditions}}

        # 分页
        size = min(max(size, 1), 100)
        from_val = max((page - 1) * size, 0)

        # 执行搜索
        result = client.search(
            index=index_pattern,
            query=query,
            from_=from_val,
            size=size,
            sort=[{"duration": {"order": "desc"}}],
        )

        hits = result.get("hits", {}).get("hits", [])
        total = result.get("hits", {}).get("total", {}).get("value", 0)

        # 安全化字段
        safe_fields = SAFE_FIELDS_OPENAPI if index_type == "openapi" else SAFE_FIELDS_THIRDAPI
        safe_hits = []
        for hit in hits:
            source = hit.get("_source", {})
            safe_hit = {k: v for k, v in source.items() if k in safe_fields}
            safe_hit["id"] = hit.get("_id", source.get("id"))
            safe_hits.append(safe_hit)

        # 统计信息
        durations = [hit.get("duration", 0) for hit in hits if hit.get("duration")]
        stats = {}
        if durations:
            stats = {
                "min_duration_ms": min(durations),
                "max_duration_ms": max(durations),
                "avg_duration_ms": round(sum(durations) / len(durations), 2),
            }

        return {
            "total": total,
            "page": page,
            "size": size,
            "min_duration_filter": min_duration,
            "stats": stats,
            "hits": safe_hits,
        }
    except ElasticsearchQueryError:
        raise
    except Exception as error:
        raise ElasticsearchQueryError(_safe_error(error)) from error
