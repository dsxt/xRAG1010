# utils/milvus_utils.py

import os
from tool.logger import logger
from pymilvus import MilvusClient, AnnSearchRequest, WeightedRanker

from config.milvus_config import milvus_config

_milvus_client = None
def get_milvus_client():
    """
    获取全局单例的MilvusClient对象
    :return: MilvusClient实例
    """
    global _milvus_client
    if _milvus_client is not None:
        return _milvus_client

    _milvus_client = MilvusClient(uri=milvus_config.milvus_url)

    return _milvus_client

def truncate_utf8(value, max_bytes: int) -> str:
    """按 UTF-8 字节数截断。Milvus VARCHAR 的 max_length 按字节计算。"""
    if value is None:
        return ""
    text = str(value)
    if max_bytes <= 0:
        return ""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    return raw[:max_bytes].decode("utf-8", errors="ignore")


def collection_field_meta(client, collection_name: str) -> dict:
    """读取集合字段的类型和 VARCHAR 字节上限。读取失败时返回空字典。"""
    meta = {}
    try:
        desc = client.describe_collection(collection_name)
    except Exception as e:
        logger.warning(f"读取集合字段信息失败[{collection_name}]: {e}")
        return meta

    fields = desc.get("fields", []) if isinstance(desc, dict) else []
    for field in fields:
        if not isinstance(field, dict):
            continue
        name = field.get("name")
        if not name:
            continue
        params = field.get("params") or {}
        raw_type = field.get("type")
        type_code = raw_type.value if hasattr(raw_type, "value") else raw_type
        meta[name] = {
            "type": type_code,
            "type_name": str(raw_type),
            "max_length": params.get("max_length"),
        }
    return meta


def fit_milvus_row(row: dict, field_meta: dict) -> dict:
    """按集合真实 schema 截断字符串、限制整数范围，避免入库因超长或溢出失败。"""
    fitted = dict(row)
    for name, meta in field_meta.items():
        if name not in fitted or fitted[name] is None:
            continue
        max_length = meta.get("max_length")
        if max_length and isinstance(fitted[name], str):
            fitted[name] = truncate_utf8(fitted[name], int(max_length))
        # pymilvus DataType.INT8 == 2，已有旧集合仍可能是 INT8
        type_name = str(meta.get("type_name") or "")
        is_int8 = meta.get("type") == 2 or "INT8" in type_name
        if is_int8 and isinstance(fitted[name], int) and not isinstance(fitted[name], bool):
            fitted[name] = max(-128, min(127, fitted[name]))
    return fitted


def escape_milvus_string(value: str) -> str:
    """
    Milvus数据库过滤表达式中字符串的安全转义函数（防止解析失败）
    作用：
        转义特殊字符（反斜杠、双引号），避免Milvus解析filter时报错
    参数：
        value: 需要转义的原始字符串
    返回：
        str: 转义后的安全字符串
    """
    # 转义反斜杠（\ → \\） 双引号（" → \"） 单引号（' → \'）
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")
    return value

# utils/milvus_utils.py
# 在这个文件中添加如下方法
def create_hybrid_search_requests(dense_vector, sparse_vector, dense_params=None, sparse_params=None, expr=None,
                                  limit=5):
    """
    构建Milvus混合搜索请求对象
    分别创建稠密/稀疏向量的搜索请求，用于后续混合搜索融合
    :param dense_vector: 文本生成的稠密向量
    :param sparse_vector: 文本生成的稀疏向量
    :param dense_params: 稠密向量搜索参数，默认使用余弦相似度
    :param sparse_params: 稀疏向量搜索参数，默认使用内积相似度
    :param expr: 搜索过滤表达式，用于精准筛选数据
    :param limit: 单向量搜索返回结果数量，默认5
    :return: 搜索请求列表，包含[dense_req, sparse_req]
    """
    # 稠密向量默认搜索参数：余弦相似度（COSINE），适配BGE-M3稠密向量
    if dense_params is None:
        dense_params = {"metric_type": "COSINE"}
    # 稀疏向量默认搜索参数：内积（IP），适配BGE-M3稀疏向量
    if sparse_params is None:
        sparse_params = {"metric_type": "IP"}

    # 构建稠密向量搜索请求，关联Milvus的dense_vector字段 近似最近邻（ANN）检索请求的核心类
    dense_req = AnnSearchRequest(
        data=[dense_vector],
        anns_field="dense_vector",
        param=dense_params,
        expr=expr,
        limit=limit
    )

    # 构建稀疏向量搜索请求，关联Milvus的sparse_vector字段
    sparse_req = AnnSearchRequest(
        data=[sparse_vector],
        anns_field="sparse_vector",
        param=sparse_params,
        expr=expr,
        limit=limit
    )

    return [dense_req, sparse_req]


def hybrid_search(client, collection_name, reqs, ranker_weights=(0.5, 0.5), norm_score=False, limit=5,
                  output_fields=None, search_params=None):
    """
    执行Milvus稠密+稀疏向量混合搜索
    基于WeightedRanker实现双向量搜索结果加权融合，提升检索准确性
    :param client: MilvusClient实例
    :param collection_name: 集合名称
    :param reqs: 搜索请求列表，固定为[dense_req, sparse_req]
    :param ranker_weights: 加权融合权重，默认(0.5,0.5)，依次对应稠密/稀疏向量
    :param norm_score: 是否归一化评分后再融合，避免评分量级差异导致权重失效
    :param limit: 混合搜索最终返回结果数量，默认5
    :param output_fields: 需要返回的字段列表，默认返回item_name
    :param search_params: 搜索参数，如ef/topk等，默认None
    :return: 混合搜索结果列表，搜索失败返回None
    """
    try:
        # 初始化加权排名器：按权重融合稠密/稀疏向量的搜索结果
        # norm_score=True：先将两个向量评分归一化到0~1区间，再加权计算，避免一个得分特别大、另一个特别小导致权重失效。
        # 版本：V2.4
        rerank = WeightedRanker(ranker_weights[0], ranker_weights[1], norm_score=norm_score)
        # 默认返回字段：文档标识字段
        if output_fields is None:
            output_fields = ["item_name"]

        # 执行混合搜索：融合稠密+稀疏向量结果，按权重重新排序
        res = client.hybrid_search(
            collection_name=collection_name,
            reqs=reqs,
            ranker=rerank,
            limit=limit,
            output_fields=output_fields,
            search_params=search_params
        )

        logger.info(f"Milvus混合搜索完成，集合[{collection_name}]共检索到{len(res[0])}条结果")
        return res
    except Exception as e:
        logger.error(f"Milvus混合搜索执行失败，集合[{collection_name}]：{str(e)}", exc_info=True)
        return None