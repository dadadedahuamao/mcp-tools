"""接口日志的关联和对比分析。"""

from typing import Any
from es_mcp.client import ElasticsearchQueryError, _get_index_pattern, _validate_index_access, SAFE_FIELDS_OPENAPI, SAFE_FIELDS_THIRDAPI
from es_mcp.config import ElasticsearchEnvironment

def _pattern(c: ElasticsearchEnvironment, t: str) -> str:
    p = _get_index_pattern(t)
    if not _validate_index_access(p, c.index_allowlist): raise ElasticsearchQueryError(f"索引 {p} 不在允许访问列表中")
    return p

def _query(start: str | None, end: str | None, route: str | None, t: str) -> dict:
    must = []
    if start or end: must.append({"range": {"startTime": {**({"gte": start} if start else {}), **({"lte": end} if end else {})}}})
    if route: must.append({"wildcard": {("route" if t == "openapi" else "apiAddr"): f"*{route}*"}})
    return {"bool": {"must": must}} if must else {"match_all": {}}

def compare_periods(client: Any, config: ElasticsearchEnvironment, index_type: str, *, first_start: str, first_end: str, second_start: str, second_end: str) -> dict:
    p = _pattern(config, index_type)
    try:
        def agg(a: str, b: str) -> dict:
            r = client.search(index=p, query=_query(a,b,None,index_type), aggs={"avg": {"avg": {"field":"duration"}}, "p": {"percentiles": {"field":"duration", "percents":[50,90,95,99]}}}, size=0)
            return {"requests": r.get("hits",{}).get("total",{}).get("value",0), "avg_duration_ms": r.get("aggregations",{}).get("avg",{}).get("value",0), "percentiles": r.get("aggregations",{}).get("p",{}).get("values",{})}
        return {"index_pattern":p,"first":agg(first_start,first_end),"second":agg(second_start,second_end)}
    except Exception as e: raise ElasticsearchQueryError("接口时间段对比失败") from e

def latency_percentiles(client: Any, config: ElasticsearchEnvironment, index_type: str, *, start_time: str|None=None, end_time: str|None=None, route: str|None=None, top_n: int=50) -> dict:
    p=_pattern(config,index_type); field="route" if index_type=="openapi" else "apiAddr"
    try:
        r=client.search(index=p,query=_query(start_time,end_time,route,index_type),size=0,aggs={"routes":{"terms":{"field":f"{field}.keyword","size":min(max(top_n,1),100)},"aggs":{"count":{"value_count":{"field":"id"}},"latency":{"percentiles":{"field":"duration","percents":[50,90,95,99]}}}}})
        return {"percentiles":[{"route":b.get("key"),"request_count":b.get("count",{}).get("value",0),"values":b.get("latency",{}).get("values",{})} for b in r.get("aggregations",{}).get("routes",{}).get("buckets",[])]}
    except Exception as e: raise ElasticsearchQueryError("接口延迟分位数查询失败") from e

def error_samples(client: Any, config: ElasticsearchEnvironment, index_type: str, *, start_time: str|None=None, end_time: str|None=None, top_n: int=20) -> dict:
    p=_pattern(config,index_type)
    try:
        r=client.search(index=p,query={"bool":{"must":[{"regexp":{"statusCode":"[45]\\d{2}"}}, *(_query(start_time,end_time,None,index_type).get("bool",{}).get("must",[]))]}},size=min(max(top_n,1),50),sort=[{"startTime":{"order":"desc"}}])
        fields=SAFE_FIELDS_OPENAPI if index_type=="openapi" else SAFE_FIELDS_THIRDAPI
        return {"samples":[{k:v for k,v in h.get("_source",{}).items() if k in fields} for h in r.get("hits",{}).get("hits",[])]}
    except Exception as e: raise ElasticsearchQueryError("错误样本查询失败") from e

def trace_request(client: Any, config: ElasticsearchEnvironment, index_type: str, *, request_id: str, start_time: str|None=None, end_time: str|None=None) -> dict:
    p=_pattern(config,index_type)
    try:
        q={"bool":{"must":[{"term":{"id":request_id}}, *(_query(start_time,end_time,None,index_type).get("bool",{}).get("must",[]))]}}
        r=client.search(index=p,query=q,size=20,sort=[{"startTime":{"order":"asc"}}])
        return {"request_id":request_id,"hits":[h.get("_source",{}) for h in r.get("hits",{}).get("hits",[])]}
    except Exception as e: raise ElasticsearchQueryError("接口调用链查询失败") from e

def topology(client: Any, config: ElasticsearchEnvironment, index_type: str, *, start_time: str|None=None, end_time: str|None=None, top_n: int=50) -> dict:
    p=_pattern(config,index_type); field="route" if index_type=="openapi" else "apiAddr"
    try:
        r=client.search(index=p,query=_query(start_time,end_time,None,index_type),size=0,aggs={"topology":{"terms":{"field":f"{field}.keyword","size":min(max(top_n,1),100)},"aggs":{"apps":{"terms":{"field":("appName.keyword" if index_type=="openapi" else "plateformName.keyword"),"size":10}}}}})
        return {"topology":r.get("aggregations",{}).get("topology",{}).get("buckets",[])}
    except Exception as e: raise ElasticsearchQueryError("接口拓扑查询失败") from e
