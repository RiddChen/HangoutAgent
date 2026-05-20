# app/models/session.py
"""会话管理：checkpointer（对话持久化）+ store（方案/偏好持久化）。

默认使用 Redis。未配置 REDIS_URL 时回退到 SQLite + InMemoryStore。
"""

import os

from langchain_core.messages import AIMessage, HumanMessage

from app.common.logger import logger

# Redis URL，默认本地
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")


class SessionManager:
    """管理 checkpointer 和 store 的生命周期。

    - checkpointer: 对话历史持久化（按 thread_id）
    - store: 方案/偏好持久化（按 user_id，重启不丢）
    """

    def __init__(self):
        self.checkpointer = None
        self.store = None
        self._conn = None  # SQLite fallback 用

    async def init(self):
        """初始化持久化后端。"""
        try:
            await self._init_redis()
        except Exception as e:
            logger.warning(f"Redis 连接失败（{e}），回退到 SQLite + InMemoryStore")
            await self._init_sqlite_fallback()

    async def _init_redis(self):
        """Redis 后端：checkpointer + store 都持久化。"""
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver
        from langgraph.store.redis import AsyncRedisStore

        self.checkpointer = AsyncRedisSaver(redis_url=REDIS_URL)
        await self.checkpointer.asetup()

        self.store = AsyncRedisStore(redis_url=REDIS_URL)
        await self.store.setup()

        logger.info(f"SessionManager 初始化完成 ✓（Redis: {REDIS_URL}）")

    async def _init_sqlite_fallback(self):
        """SQLite 回退：checkpointer 持久化，store 仅内存。"""
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.store.memory import InMemoryStore

        db_path = os.path.join(os.path.dirname(__file__), "../db/hangout.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        self._conn = await aiosqlite.connect(db_path)
        self.checkpointer = AsyncSqliteSaver(conn=self._conn)
        await self.checkpointer.setup()

        self.store = InMemoryStore()

        logger.warning("SessionManager 初始化完成 ⚠️（SQLite + InMemoryStore，store 重启会丢失）")

    async def close(self):
        """关闭连接。"""
        if self._conn:
            await self._conn.close()
        # Redis 客户端通常不需要显式关闭

    async def get_messages(self, graph, thread_id: str) -> dict:
        """获取会话历史消息。"""
        if not graph:
            return {"messages": []}

        config = {"configurable": {"thread_id": thread_id}}
        state = await graph.aget_state(config)
        if not state or not state.values:
            return {"messages": []}

        result = []
        for msg in state.values.get("messages", []):
            content = msg.content if hasattr(msg, "content") else ""
            if not content:
                continue
            if isinstance(msg, HumanMessage):
                result.append({"role": "user", "content": content})
            elif isinstance(msg, AIMessage):
                result.append({"role": "assistant", "content": content})
        return {"messages": result}

    async def clear_messages(self, thread_id: str):
        """清除会话。"""
        if self.checkpointer and hasattr(self.checkpointer, "adelete_thread"):
            await self.checkpointer.adelete_thread(thread_id)
            logger.info(f"会话 {thread_id} 已清除")


# 单例
session_manager = SessionManager()
