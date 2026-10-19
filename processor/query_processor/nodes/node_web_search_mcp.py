# processor/query_processor/nodes/node_web_search_mcp.py
import asyncio
import json

from agents.mcp import MCPServerSse, MCPServerStreamableHttp

from config.bailian_mcp_config import mcp_config
from processor.query_processor.base import NodeBase
from processor.query_processor.state import QueryGraphState
from tool.logger import logger
from utils.json_format_utils import serialize_json


class NodeWebSearchMcp(NodeBase):
    """
    节点功能，调用外部搜索引擎补充信息
    """

    # 覆盖基类的 name 属性，标识节点名称
    name: str = "node_web_search_mcp"

    def process(self, state: QueryGraphState) -> QueryGraphState:

        query = state.get("rewritten_query", "")
        docs = []
        # 联网失败时返回空结果，不能拖垮本地检索
        if query:
            try:
                result = asyncio.run(self._mcp_call(query))
                docs = self._parse_pages(result)
                if docs:
                    logger.info(f"MCP 搜索结果: {docs}")
            except Exception as e:
                logger.exception(f"联网搜索失败: {e}")
                return {"web_search_docs": []}

        if docs:
            return {"web_search_docs": docs}
        return {"web_search_docs": []}

    def _parse_pages(self, result) -> list:
        if not result:
            return []
        content = getattr(result, "content", None) or []
        if not content:
            return []
        text = getattr(content[0], "text", None)
        if not text:
            return []
        payload = json.loads(text) if isinstance(text, str) else text
        if not isinstance(payload, dict):
            return []
        pages = payload.get("pages") or []
        if not isinstance(pages, list):
            return []

        docs = []
        for item in pages:
            if not isinstance(item, dict):
                continue
            snippet = (item.get("snippet") or "").strip()
            url = (item.get("url") or "").strip()
            title = (item.get("title") or "").strip()
            if not snippet:
                continue
            docs.append({"title": title, "url": url, "snippet": snippet})
        return docs


    async def _mcp_call(self, query):
        url = mcp_config.mcp_base_url or ""
        # 配置地址以 /sse 结尾时走 SSE，其余走 Streamable HTTP
        server_cls = MCPServerSse if url.rstrip("/").endswith("/sse") else MCPServerStreamableHttp
        search_mcp = server_cls(
            name="search_mcp",
            params={
                "url": url,
                "headers": {"Authorization": f"Bearer {mcp_config.api_key}"},
                "timeout": 10,
            },
            cache_tools_list=True,
            max_retry_attempts=3,
        )

        try:
            await search_mcp.connect()
            result = await search_mcp.call_tool(
                tool_name="bailian_web_search",
                arguments={"query": query, "count": 5},
            )
            return result
        finally:
            await search_mcp.cleanup()

if __name__ == "__main__":

    init_state = {
        "rewritten_query": "关于brother HAK180烫金机，如何调节转印温度？"
    }

    # 执行节点的业务调用
    node_web_search_mcp = NodeWebSearchMcp()
    result = node_web_search_mcp(init_state)
    logger.info(serialize_json(result, indent=4))