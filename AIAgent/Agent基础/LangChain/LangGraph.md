# LangGraph 知识点

来源：尚硅谷大模型技术之 LangGraph（V1.1.0）。本文按原课程结构梳理概念、用法和关键代码，长示例只保留能说明机制的部分。

- GitHub：https://github.com/langchain-ai/langgraph
- 文档：https://docs.langchain.com/oss/python/langgraph/overview
- 课程示例版本：`langgraph==1.0.5`

---

## 1. LangGraph 是什么

LangGraph 是低级编排框架和运行时，用来构建、管理和部署**长期运行、有状态**的智能体。核心做法是把 Agent 工作流建成一张图：

| 概念 | 含义 |
| --- | --- |
| 节点（Nodes） | 计算单元：LLM 调用、工具执行或任意自定义逻辑 |
| 边（Edges） | 节点之间的转换，决定下一步走哪 |
| 状态（State） | 整张图执行过程中共享、传递的数据 |

它面向生产级智能体，提供这些能力：

- **持久化执行**：故障后能恢复，适合长时间运行
- **人机协作**：任意时刻检查、修改智能体状态
- **记忆**：短期工作记忆，以及跨会话的长期记忆
- **流式处理**：按工作流逐步把中间结果推出去
- **生产部署**：为有状态、长期运行的工作流提供可扩展基础设施

### 和 LangChain 的分工

LangChain 是高级抽象，适合开箱即用的链式调用。LangGraph 给细粒度控制。一句话：当 LLM 应用需要「有状态、可循环、可分支的多步骤控制流（可回溯的流程图 / 状态机）」时，用 LangGraph。

| 特性 | LangGraph | LangChain |
| --- | --- | --- |
| 抽象级别 | 低级，细粒度控制 | 高级，开箱即用 |
| 状态管理 | 内置状态机和检查点 | 需要自己管状态 |
| 执行模型 | 基于图，可并行 | 线性链式 |
| 持久化 | 原生支持 | 需要额外实现 |
| 适用场景 | 复杂、有状态的智能体 | 简单链式调用 |

更适合 LangGraph 的场景：复杂多智能体、长期记忆、人工审核、后台任务与实时交互、需要精细控制的定制编排。

---

## 2. 快速入门

### 2.1 安装

```bash
conda create --name langgraph_venv python=3.12
conda activate langgraph_venv
pip install langgraph==1.0.5
pip show langgraph
```

### 2.2 固定五步

1. 定义状态
2. 定义节点函数
3. 用状态创建 `StateGraph`（builder），添加节点和边
4. `compile()` 得到可调用的 graph
5. `invoke` / `stream` 调用 graph

入门例子是一个并行 RAG：用户问题同时走知识库检索和联网搜索，两边都完成后由 LLM 汇总。同一起点连出两条边，两条边再汇合到同一个节点，就是并行。

```python
from typing import TypedDict
from langgraph.constants import START, END
from langgraph.graph import StateGraph

class MyState(TypedDict):
    rag_result: str
    web_search_result: str
    query: str
    final_answer: str

def rag_search_node(state: MyState):
    query = state["query"]
    return {"rag_result": f"关于{query}的rag_result"}

def web_search_node(state: MyState):
    query = state["query"]
    return {"web_search_result": f"关于{query}的web_search_result"}

def final_answer_node(state: MyState):
    return {
        "final_answer": (
            f"LLM基于{state['rag_result']}和{state['web_search_result']}的最终回复"
        )
    }

graph = StateGraph(state_schema=MyState)
graph.add_node(rag_search_node)
graph.add_node(web_search_node)
graph.add_node(final_answer_node)
graph.add_edge(START, "rag_search_node")
graph.add_edge(START, "web_search_node")
graph.add_edge("rag_search_node", "final_answer_node")
graph.add_edge("web_search_node", "final_answer_node")
graph.add_edge("final_answer_node", END)

compiled_graph = graph.compile()
res = compiled_graph.invoke({"query": "如何使用LangGraph"})
```

`add_node` 不传名字时，节点名默认是函数名。看图结构可以：

```python
# pip install grandalf
print(compiled_graph.get_graph().draw_ascii())
```

---

## 3. 状态

State 是整张图的结果目标，也是节点要读写的数据。定义状态是建图的第一步。

### 3.1 三种定义方式

每种方式都是声明字段类型，需要合并语义时再给字段挂 reducer。

**TypedDict（推荐）**：给字典的键值做精确类型提示。

```python
from typing import TypedDict

class MyStateFull(TypedDict):
    rag_result: str
    web_search_result: str
    final_answer: str
    query: str
    a_new_key: str
```

**Pydantic BaseModel**：有运行时校验，也能配合静态类型检查。

```python
from pydantic import BaseModel

class MyStateFull(BaseModel):
    rag_result: str
    web_search_result: str
    final_answer: str
    query: str
    a_new_key: str
```

**dataclass**：用标准库装饰器生成 `__init__` 等，适合当数据容器。

```python
from dataclasses import dataclass

@dataclass
class MyStateFull:
    rag_result: str
    web_search_result: str
    final_answer: str
    query: str
    a_new_key: str
```

### 3.2 输入 / 输出隔离

`StateGraph` 可以同时指定三个 schema：

| 参数 | 作用 | 是否必须 |
| --- | --- | --- |
| `state_schema` | 图的完整内部状态，所有节点可读写其中字段 | 必须 |
| `input_schema` | 图接受的输入，是 `state_schema` 的子集；不传则等于完整状态 | 可选 |
| `output_schema` | 图返回的输出，是 `state_schema` 的子集；不传则等于完整状态 | 可选 |

调用方只能传入 `input_schema` 里的字段，最终也只拿到 `output_schema` 里的字段。中间节点仍读写完整内部状态。

```python
graph = StateGraph(
    state_schema=MyStateFull,
    input_schema=InputSchema,    # 例如只有 query
    output_schema=OutputSchema,  # 例如只有 final_answer
)
```

### 3.3 节点私有状态

节点函数的类型注解可以写成一份非全局 schema。节点只按自己声明的字段读写，用来做节点之间的数据隔离。全局 `state_schema` 仍然是图的主状态。

### 3.4 Reducer：增量状态如何并进全局状态

每个节点返回的是**增量**，不是整份状态。某个 key 怎么和全局状态合并，由该 key 自己的 reducer 决定。

- **默认**：没指定 reducer 就覆盖。这只适用于先后执行的节点。同一步里两个并行节点写同一个没挂 reducer 的 key，运行时无法决定保留哪一份，抛 `InvalidUpdateError`
- **内置**：如 `operator.add`（列表追加、字符串拼接、数值累加），以及 `langgraph.graph.message.add_messages`
- **自定义**：函数接收「当前全局值」和「本次增量」，返回合并结果

用 `Annotated` 把 reducer 绑到字段上：

```python
import operator
from typing import Annotated, List
from typing_extensions import TypedDict

class ListAddState(TypedDict):
    data: Annotated[List[int], operator.add]   # 列表追加

class StringConcatState(TypedDict):
    text: Annotated[str, operator.add]         # 字符串拼接

class NumberAddState(TypedDict):
    count: Annotated[int, operator.add]        # 数值累加
```

并行节点同时写同一个带 reducer 的 key 时，两边的增量都会被合并进去，而不是互相覆盖。

自定义 reducer 签名是 `(left, right) -> merged`：

```python
def add_message(message_list_left: list, message_list_right: list):
    return message_list_left + message_list_right

class MyAgent(TypedDict):
    messages: Annotated[List[BaseMessage], add_message]
```

### 3.5 为什么要存状态

每次 `invoke` 默认会初始化一份空状态，再并入本次输入。这在一次性任务里没问题，两类场景会坏：

1. **同一会话多次调用要保留上下文**。第一次写入的 `messages` 在第二次调用时会被新的空列表替换掉。
2. **中途报错要断点续传**。前面节点已经算完，进程退出后内存没了，不想从头再跑。

解法是把每次执行产生的状态存下来，下次按 id 取回，而不是重新初始化。

### 3.6 Checkpointer 与 thread_id

Checkpointer（检查点）负责把图执行中的状态存到内存或数据库。相关类都在 `langgraph.checkpoint`。

隔离不同用户、不同会话靠 `thread_id`。它和操作系统线程无关，只是状态空间的 key。

三步：

1. 创建 checkpointer 实例
2. `compile(checkpointer=...)` 时传入
3. 调用时在 `config["configurable"]["thread_id"]` 里传 id。同一个 id 延续上下文，新 id 开辟新会话。这就是 Agent 的**短期记忆**。

内存检查点（进程退出即丢失，适合会话内连续调用）：

```python
from langgraph.checkpoint.memory import InMemorySaver

checkpointer = InMemorySaver()
agent = create_agent(model=llm, tools=[weather_tool], checkpointer=checkpointer)

config = {"configurable": {"thread_id": "user_1_session_1"}}
agent.invoke({"messages": "北京2026-01-14天气怎么样"}, config=config)
agent.invoke({"messages": "适合出去玩吗"}, config=config)  # 同一 thread_id，带上上文
```

SQLite 持久化（进程退出后仍在，才能做故障恢复）：

```python
# pip install langgraph-checkpoint-sqlite
import sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver

conn = sqlite3.connect("./sqlite_data/langgraph_sqlite", check_same_thread=False)
memory = SqliteSaver(conn)
graph = builder.compile(checkpointer=memory)
graph.invoke({}, config={"configurable": {"thread_id": "123"}})
```

### 3.7 从故障中恢复

节点报错时，已完成节点的检查点已经写入数据库。修复代码后继续跑，满足两点：

1. `invoke` 的输入传 `None`（不要再塞一份新的初始状态）
2. 传入**相同**的 `thread_id`

```python
graph.invoke(None, config={"configurable": {"thread_id": "123"}})
```

图会按上次留下的快照，从还没跑完的节点接着执行。

### 3.8 底层运行时：Pregel

Pregel 管理整张图从开始到结束的迭代。`compile()` 把逻辑图转成 Pregel 实例：节点变成 Actors，边变成 Channels。

**Actors（`PregelNode`）**

就是前面的 Node。它订阅某些通道、从通道读、向通道写，并实现了 LangChain 的 `Runnable` 接口。

**Channels（通道）**

Actor 之间的通信媒介。每个通道有：值类型、更新类型、更新函数（接收一系列更新，改存储的值）。状态字段上的 reducer，对应的就是通道的更新函数。

因此 LangGraph 的图不是传统 DAG。传统 DAG 里，一个节点要等所有上游都完成才执行。LangGraph 按**超步（Superstep）**推进，一个超步三阶段：

1. **Plan**：选出本步要执行的 Actors。第一步选订阅输入通道的节点；之后选订阅了「上一步被更新的通道」的节点
2. **Execute**：并行跑选中的 Actors，直到全部完成、有一个失败，或超时
3. **Update**：用本步写入的值更新 channels

超步一直重复，直到没有 Actor 被选中，或达到最大步数。

课程里的示例图：`START → a → (b → b_2, 以及 c) → d → END`。执行过程可以理解为：

- step -1：`__START__` 把状态写入通往 `a` 的通道
- step 0：`a` 执行，写入通往 `b`、`c` 的通道
- step 1：`b` 和 `c` 并行，分别写入通往 `b_2`、`d` 的通道
- step 2：`b_2` 和 `d` 执行；`b_2` 再次写入通往 `d` 的通道
- step 3：`d` 因通道又被更新而再执行一次；之后没有新更新，结束

节点 `d` 会执行两次，因为它订阅的通道被更新了两次。这是「订阅通道」和「等所有上游」的差别。

编译后可以查看通道和节点订阅关系：

```python
print(graph.channels)
print(graph.nodes)
print(graph.nodes["a"].triggers)  # 该节点订阅的通道
print(graph.nodes["a"].writers)   # 该节点写入的通道
```

`START` 本身也会落一个 checkpoint。

### 3.9 读取历史状态

必须先挂 checkpointer。按 `thread_id` 读取：

- `get_state(config)`：最近一个时间步的快照
- `get_state_history(config)`：全部时间步，迭代器，**时间倒序**

快照类型是 `StateSnapshot`：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `values` | dict / Any | 当前状态值 |
| `next` | tuple[str, ...] | 本超步接下来要执行的节点名 |
| `config` | RunnableConfig | 取这份快照用的配置 |
| `metadata` | CheckpointMetadata | 关联元数据 |
| `parent_config` | RunnableConfig | 父快照的配置 |
| `interrupts` | tuple[Interrupt, ...] | 本超步里尚未处理的中断 |

`next` 直接告诉你恢复时从哪个节点继续，这是断点续传能定位的原因。

```python
config = {"configurable": {"thread_id": "1"}}
graph.invoke({}, config=config)

last_state = graph.get_state(config)
for state in graph.get_state_history(config):
    print(state.values, state.next)
```

---

## 4. 节点

### 4.1 输入

节点是同步或异步的 Python 函数。运行时按关键字注入这些参数，顺序可以调换：

| 参数 | 含义 |
| --- | --- |
| `state` | 图状态，业务数据 |
| `config` | `RunnableConfig`。含 `thread_id`，也可放调用时传入的自定义配置（如 `user_id`） |
| `runtime` | `Runtime`。含调用时传入的 `context`，以及 `store`、`stream_writer` 等 |

`config` 放配置型数据；`runtime.context` 放调用时注入的依赖对象（LLM、数据库客户端等）。

```python
def node_customer_service(state, config: RunnableConfig, runtime: Runtime) -> dict:
    llm = runtime.context["llm"]
    db = runtime.context["db"]
    user_id = config.get("configurable", {}).get("user_id", "guest")
    runtime.stream_writer({"status": "thinking", "message": "正在生成回复"})
    return {"response": llm.invoke(...)}

graph.invoke(
    {"query": "如何升级会员？"},
    config={"configurable": {"user_id": "vip_user_999"}},
    context={"llm": my_llm, "db": my_db},
)
```

### 4.2 输出必须是增量

节点只返回本节点要改的字段。运行时把返回值当作增量，按各 key 的 reducer 并进全局状态。

把收到的整个 `state` 原样返回会出问题：

- 没配 reducer 的 key：底层无法合并，抛异常
- 配了 reducer 的 key：把本不该由该节点更新的值再写一次，数据会被重复合并

并行节点尤其明显：两个节点都返回完整 state，未声明 reducer 的字段无法合并。

### 4.3 START 与 END

二者是特殊节点，本质是字符串：

```python
START = sys.intern("__start__")  # 把用户输入送进图，用来标明先调用谁
END = sys.intern("__end__")      # 终止。边连到 END 表示这条分支到此结束
```

`START` 执行完也会写一个 checkpoint。

### 4.4 节点缓存

相同输入且缓存未过期时，直接读缓存，不再执行节点。两处配置：

1. `compile(cache=...)` 指定后端。`langgraph.cache` 提供内存、Redis、SQLite
2. `add_node(..., cache_policy=CachePolicy(...))` 指定策略
   - `key_func`：由节点输入生成缓存键，默认对输入做 pickle 再 hash
   - `ttl`：秒。不指定则永不过期

```python
from langgraph.cache.memory import InMemoryCache
from langgraph.types import CachePolicy

builder.add_node("expensive_node", expensive_node, cache_policy=CachePolicy(ttl=10))
graph = builder.compile(cache=InMemoryCache(), checkpointer=checkpointer)

graph.invoke({"x": 5}, config={"configurable": {"thread_id": "1"}})  # 真正计算
graph.invoke({"x": 5}, config={"configurable": {"thread_id": "2"}})  # 命中缓存
```

缓存键看的是节点输入，和 `thread_id` 无关。不同会话只要输入相同，就可以命中同一份缓存。

### 4.5 节点重试

不稳定节点（API、数据库、LLM）在 `add_node` 里加 `retry_policy`。`RetryPolicy` 是命名元组：

- `max_attempts`：最多尝试次数
- `retry_on`：哪些异常要重试

默认策略会重试大多数异常，但**不重试**这些编程错误类异常：`ValueError`、`TypeError`、`ArithmeticError`、`ImportError`、`LookupError`、`NameError`、`SyntaxError`、`RuntimeError`、`ReferenceError`、`StopIteration`、`StopAsyncIteration`、`OSError`。

```python
from langgraph.types import RetryPolicy

builder.add_node(
    "unstable_call",
    unstable_api_call,
    retry_policy=RetryPolicy(max_attempts=5),
)
```

### 4.6 图执行过程中把数据送出去：stream

`invoke` 要等整张图结束才返回最终状态。执行中就要拿中间数据（LLM token、当前走到哪一步），用 `stream` / `astream`。图实现了 LangChain `Runnable`，所以自带流式接口。

| 模式 | 产出 |
| --- | --- |
| `values` | 每步结束后的完整状态 |
| `updates` | 每步的增量。同一步多个更新会分别流出。形态是 `{节点名: 该节点输出}` |
| `custom` | 节点里 `runtime.stream_writer(...)` 推送的自定义数据，不进入图状态 |
| `messages` | 任意调用了 LLM 的节点流出二元组 `(LLM token, metadata)` |
| `debug` | 调度、输入输出等全部调试信息 |
| 混合 | `stream_mode` 传列表，每条是 `(mode, data)` |

```python
for event in graph.stream(initial_state, stream_mode="values"):
    ...

for event in graph.stream(initial_state, stream_mode="updates"):
    ...  # {"node_name": {...增量...}}

for event in graph.stream(initial_state, stream_mode="custom"):
    ...  # writer() 送出的对象

for chunk, metadata in graph.stream(initial_state, stream_mode="messages"):
    node_name = metadata.get("langgraph_node")
    token = chunk.content

for mode, data in graph.stream(initial_state, stream_mode=["updates", "custom"]):
    ...
```

节点内推自定义进度：

```python
def node_processing(state, runtime: Runtime):
    writer = runtime.stream_writer
    writer({"step_index": 1, "description": "正在检索知识库..."})
    return {"current_step": "processing_complete"}
```

### 4.7 人工审核：interrupt

`interrupt` 可以写在节点内部任意位置。执行到这里图暂停，把需要人看的数据带出图；人处理完后再次 `invoke`，图继续。必须配合 checkpointer，否则暂停点无法保存。

流程：

1. 第一次 `invoke`，停在 `interrupt`，返回值里有 `__interrupt__`
2. 从 `result["__interrupt__"][0].value` 取出给用户看的内容
3. 第二次 `invoke(Command(resume=用户决策), config=同一 thread_id)` 恢复

```python
from langgraph.types import Command, interrupt

def review_transfer(state):
    user_review = interrupt({
        "title": "转账审核",
        "pending_transfer": {...},
    })
    # user_review 就是恢复时 Command(resume=...) 传入的值
    ...

first = graph.invoke(initial_state, config=config)
payload = first["__interrupt__"][0].value

final = graph.invoke(
    Command(resume={"approved": True, "amount": 80}),
    config=config,
)
```

恢复时，含 `interrupt` 的函数会**从函数开头重新执行**，再次走到 `interrupt` 才把 `resume` 的值交回去。因此 `interrupt` 之前的写库、调外部 API 会再跑一遍。这些操作要幂等，或者拆到单独节点，避免恢复时重复执行。

---

## 5. 边

### 5.1 边的本质

在 Pregel 里，边定义的是：节点订阅哪些状态通道，以及执行后更新哪些通道。

两类：

- **普通边（Normal Edges）**：`add_edge(源, 目标)`，固定走向
- **条件边（Conditional Edges）**：调用路由函数，按当前状态决定下一个（或下一批）节点

一个节点同时 `add_edge` 到多个下游，这些下游在同一步并行。多个上游连到同一个下游，则该下游会因通道更新而在对应超步被调度（见 3.8 里节点 `d` 执行两次的例子）。

### 5.2 条件边

路由函数读 state，返回下一个节点名，或返回映射表里的 key。

```python
def route_condition(state) -> Literal["node_b", "node_c"]:
    if state["value"] % 2 == 0:
        return "node_b_alias"
    return "node_c_alias"

builder.add_conditional_edges(
    "node_a",
    route_condition,
    {
        "node_b_alias": "node_b",
        "node_c_alias": "node_c",
    },
)
```

第三个参数是可选的路径映射：路由函数返回 alias，映射表再转到真实节点名。路由函数也可以直接返回节点名或 `END`。

### 5.3 可控循环与递归上限

条件边指回上游，就形成循环。典型 ReAct：模型决定调工具，工具结果再回到模型，直到模型不再调工具、路由到 `END`。

循环可能停不下来。`recursion_limit` 限制图在报错前允许的超级步数量，默认 **25**，写在 `invoke` 的 config 里。超过仍未自然结束，抛 `GraphRecursionError`。

```python
from langgraph.errors import GraphRecursionError

def route(state) -> Literal["b", END]:
    if state["count"] >= state["max_count"]:
        return END
    return "b"

builder.add_edge(START, "a")
builder.add_conditional_edges("a", route)  # 去 b 或 END
builder.add_edge("b", "a")                 # b 回到 a，形成循环

try:
    graph.invoke(
        {"count": 0, "result": "", "max_count": 300},
        config={"recursion_limit": 6},
    )
except GraphRecursionError as e:
    print(e)
```

业务上的停止条件（`count >= max_count`）和框架上的 `recursion_limit` 是两层保险：前者让循环正常结束，后者防止条件写错时无限跑。

---

## 6. 对照速查

| 需求 | 做法 |
| --- | --- |
| 建一张图 | `StateGraph` → `add_node` / `add_edge` → `compile` → `invoke` |
| 并行 | 同一个节点连出多条边 |
| 分支 | `add_conditional_edges` + 路由函数 |
| 循环 | 条件边指回上游，并设停止条件 |
| 防止死循环 | `config["recursion_limit"]`，超限抛 `GraphRecursionError` |
| 字段追加而不是覆盖 | `Annotated[T, reducer]`，如 `operator.add`、`add_messages` |
| 隐藏内部字段 | `input_schema` / `output_schema` |
| 同一会话记住上文 | checkpointer + 相同 `thread_id` |
| 新会话 | 换一个 `thread_id` |
| 进程重启后接着跑 | SQLite / DB checkpointer；修复后 `invoke(None, 同一 config)` |
| 看执行到哪 | `get_state` / `get_state_history`，看 `values` 和 `next` |
| 注入依赖 | `invoke(..., context={...})`，节点里读 `runtime.context` |
| 调用期配置 | `config["configurable"]` |
| 流式拿状态 / token / 进度 | `stream_mode`：`values` / `updates` / `messages` / `custom` |
| 节点内推进度 | `runtime.stream_writer` |
| 人工审批后继续 | `interrupt` + `Command(resume=...)` + checkpointer |
| 贵节点重复计算 | `CachePolicy(ttl=...)` + `compile(cache=...)` |
| 抖动节点 | `RetryPolicy(max_attempts=...)` |

---

## 7. 面试题（对照 2026-11 招聘）

截至 2026-11，Agent / 应用开发岗把 LangGraph 当成有状态智能体的默认编排运行时来问。国内一面（互联网、出行、金融、企业软件）很少停在名词解释，常见顺序是：为什么不用 Chain → 状态里放什么 → reducer 和并行怎么合并 → checkpoint 解决什么 → 人审和恢复会不会重复扣款 → 项目里怎么评测、延迟从哪来。

和前面课程笔记的差别，面试里要主动说清：

- 课程示例钉在 `langgraph==1.0.5`。2025-10 前后 LangChain 与 LangGraph 进入 1.x 后，**LangGraph 是运行时，LangChain 的 `create_agent` 跑在这套运行时上**。二者是分层，不是二选一。
- 旧的 `AgentExecutor` 已弃用，公开的维护窗口到 2026-12。面试若问迁移：简单 ReAct 用 `create_agent`（或仍常见的 `create_react_agent`），要自定义分支、循环、审批就手写 `StateGraph`。
- 2026-10 起 AutoGen 已归档，后继是 Microsoft Agent Framework。对比框架时把它当成历史方案，不要当成现行主选。
- 课程里的「默认覆盖」对**顺序节点**成立。同一步并行写同一 key 且没有 reducer，会抛 `InvalidUpdateError`。这是目前最常见的陷阱题。

按岗级，考察重心不同：

| 层级 | 主要在考 |
| --- | --- |
| 初级 | 节点 / 边 / 状态、增量返回、什么时候图比链合适 |
| 中级 | reducer、checkpointer 与 `thread_id`、`interrupt` 恢复、流式、ReAct 环 |
| 高级 / 架构 | `Command`、`Send`、子图、多 Agent 拓扑、Postgres 水平扩展、成本、评测、事故里的幂等 |

下面的答案按「先给结论，再补会扣分的边界」来写，方便口述。

### 7.1 基础

**1. LangGraph 解决什么问题？什么时候不用它？**

它把智能体收成一张可持久化的状态图：确定性步骤（校验、鉴权、写库）和模型步骤（选工具、起草）放在同一条显式控制流里，并能暂停、恢复、流式输出。自由循环「模型想调什么工具就调」能做演示，但不好设边界、不好审计、挂了只能重头跑。

单次模型调用、固定的检索再回答、没有分支也没有暂停点时不要上图。那种场景用普通函数、LCEL 或一个 `create_agent` 更合适。面试里主动说「这里我不会用 LangGraph」是加分项。

**2. 节点、边、状态在一次执行里怎么配合？**

状态是这一次运行的唯一事实来源。节点收到当前状态，只返回要改的字段。运行时按该字段的 reducer 合并，没返回的字段不动。边决定下一步。`compile()` 做结构检查并挂上 checkpointer，之后调用的是编译结果，不是 builder。

**3. 为什么节点不能把整个 state 返回？**

返回值一律当成增量。整份返回会把别的节点负责的字段再写一次：没 reducer 的字段在并行时直接冲突；有 reducer 的字段会被重复合并，消息列表变长、计数被加两次。

**4. 没写 reducer 时，两个并行节点写同一个字段会怎样？**

抛 `InvalidUpdateError`，不是后写覆盖。顺序执行才是后者覆盖前者。列表、消息、计数字段只要可能被并行写，就要挂 reducer。

**5. `add_messages` 和 `operator.add` 差在哪？**

`operator.add` 只做拼接，相同消息会重复堆上去。`add_messages` 按消息 id 更新已有消息，并支持 `RemoveMessage` 删除，还能把 `("user", "hi")` 收成消息对象。对话历史用 `add_messages`，普通列表追加用 `operator.add`。预置的 `MessagesState` 就是带 `add_messages` 的 `messages` 字段，业务图通常在它上面再加 `ticket_id`、`retry_count` 这类字段。

**6. 状态、config、runtime context 各放什么？**

- **state**：执行过程中会变、并且需要被 checkpoint 的业务数据。必须可序列化，不能塞数据库连接、模型客户端。
- **config**：调用管道，核心是 `thread_id`，也可以放 `user_id` 这类配置。
- **context**：这一次运行的依赖和身份（模型、数据库客户端、租户、角色）。1.x 可以用 `context_schema` 声明。角色放进 state 是安全问题，节点能把它改掉；放 context 里，这次运行是只读的。

跨节点要共享的才进 state。节点内部的临时变量留在函数里。

**7. `input_schema` / `output_schema` 用来干什么？**

给调用方一份契约：进去只有 `question`，出来只有 `answer` 和 `citations`。检索原文、中间分数留在内部 state。这样改内部字段不会弄坏前端，敏感中间结果也不会出现在接口响应里。

**8. 普通边、条件边、`Command` 怎么选？**

普通边是固定走向。条件边把路由写成独立函数，图的结构在 `add_conditional_edges` 处能看出来。`Command(update=..., goto=...)` 让节点在返回状态更新的同时指定下一跳，适合路由判断已经在节点内部算完、拆成路由函数会重复的情况。`Command(resume=...)` 是另一件事，用来恢复 `interrupt`。`Command(graph=Command.PARENT, goto=...)` 用于子图把控制权交回父图，也就是 Agent 交接。

条件边更好审图；`Command` 更贴近业务判断。两种都要会说取舍。

**9. 循环怎么停？只设 `recursion_limit` 够不够？**

不够。`recursion_limit` 是安全网，默认 25 个超步，超出抛 `GraphRecursionError`，版本间默认值变过，上线要显式设置。正常结束必须写在路由里：没有工具调用、质量达标、或 state 里的尝试次数到顶。再往外加工具调用预算、费用上限和墙钟超时。`RemainingSteps` 可以让模型在硬限制到来前主动收尾，而不是突然被异常打断。

**10. 一个超步里，并行节点能看到彼此的更新吗？**

不能。同一超步选出的节点一起跑，更新在这一步结束时通过 reducer 合并，下一步才能看见。一个分支抛错，这一步失败；挂了 checkpointer 时，同一步里已经成功的写入会记成 pending writes，恢复时不重跑那些成功节点。

### 7.2 持久化、人机协作、记忆

**11. checkpointer 和 `thread_id` 各是什么？没有 checkpointer 会失去什么？**

checkpointer 在每个超步结束后按线程存一份状态快照。`thread_id` 是这组快照的主键，和操作系统线程无关。它同时支撑四件事：同一会话的短期记忆、`interrupt` 之后恢复、崩溃后续跑、按历史快照做时间旅行。人机协作一定要有它，否则暂停点无处可存。

**12. 开发用哪种 Saver，生产用哪种？**

| 实现 | 适用 |
| --- | --- |
| `InMemorySaver` | 单测、笔记本。进程一退全丢 |
| `SqliteSaver` | 本机、单进程。多实例并发写不合适 |
| `PostgresSaver` / `AsyncPostgresSaver` | 生产。重启不丢，多实例共享同一线程，可连接池 |

「上线后对话全没了」几乎都是生产用了内存 Saver。Postgres 要跑一次 `setup()` 建表，检查点库和业务库分开，并做保留期清理。异步图配异步 Saver。

**13. 短期记忆和长期记忆有什么区别？**

checkpointer 是线程内记忆：同一个 `thread_id` 的多轮对话。`BaseStore`（`InMemoryStore`、`PostgresStore`）是跨线程的长期记忆，按 namespace（通常含 `user_id`）存用户偏好、历史结论。把长期记忆塞进线程状态里，换一个会话就丢，也容易把 A 用户的内容带到 B 用户。隔离单位是 `thread_id` 加用户维度的 namespace，不能只用一个全局 id。

**14. 崩溃后怎么恢复？和 `interrupt` 恢复有何不同？**

节点抛错后，上一超步的检查点还在。修复代码后用同一个 `thread_id` 调用 `invoke(None, config)`，从上次成功的边界继续。若要改掉导致失败的输入，先 `update_state`，再恢复。若要跳过失败节点，`update_state(..., as_node="那个节点")` 注入它本应写出的结果。

`interrupt` 是主动暂停等外部输入，恢复时传 `Command(resume=用户决定)`，不是传 `None`。

**15. 为什么恢复之后邮件发了两次、款扣了两次？**

检查点保证的是状态可恢复，不是副作用恰好一次。`interrupt` 恢复时，所在函数会从头再执行，`interrupt()` 之前的代码会再跑。节点重试、崩溃后续跑也一样。

做法：每个外部写操作单独成节点，并且放在 `interrupt` 之后；用 `thread_id + 动作` 做幂等键，下游或自己的表按这个键去重；在 state 里记下 `refund_issued=true`，行动前先看标记。不要用裸 `try/except` 包住 `interrupt()`，它靠抛出专门异常来暂停，接住就暂停失败。

**16. 静态断点和 `interrupt()` 怎么选？**

`compile` 或调用时的 `interrupt_before` / `interrupt_after` 是按节点名设的固定断点，适合调试和「这个节点之前一律等人」。`interrupt()` 写在节点内部，可以按金额、风险分、工具名有条件地暂停，载荷由调用方在 `__interrupt__` 里拿到。有业务条件的审批用动态 `interrupt`；只是想在固定节点停下来看状态，用静态断点。

审批按后果画线，不按步骤类型：花钱、对外发信、写系统记录、删除、不可逆操作要人。只读和可回滚的内部步骤不要一律打断，否则流程会慢到不可用。

**17. 时间旅行是什么？**

`get_state_history` 列出某线程的全部快照。选一个更早的 `checkpoint_id`，可以用 `invoke(None, config)` 从那里重放，或 `update_state` 改完状态再继续。原历史还在，分叉是一条新分支。排障时不用把 10 步对话重演一遍，从出错前一步改状态或换提示词即可验证。

**18. `durability` 怎么选？**

`invoke` / `stream` 的 `durability` 控制检查点何时落盘：

- `sync`：下一步开始前落完，有外部副作用的支付、开通流程用这个
- `async`：与下一步重叠，短对话可以接受；崩溃时机不巧会丢掉刚完成的一步
- `exit`：只在结束或暂停时落盘，最快。重跑很便宜的批处理可以用，中间进度崩溃即丢

### 7.3 2026 年会加问、课程里只点到的控制流

**19. ReAct 环在 LangGraph 里长什么样？**

`agent` 节点调模型；`tools_condition` 看最后一条消息有没有 `tool_calls`，有则进 `ToolNode`，没有则到 `END`；`ToolNode` 执行工具并把 `ToolMessage` 写回 `messages`，再固定边回到 `agent`。工具失败不要直接把异常抛出图外，写成工具消息让模型改参数，同时限制次数。简单场景直接 `create_agent`，它底层就是这张图。要在工具前后加策略校验、人工审批、检索改写，再拆成自己的 `StateGraph`。

**20. `Send` 解决什么问题？**

普通并行边的条数在建图时是固定的。`Send(node, payload)` 在运行时按列表长度扇出，每个元素一份独立输入，同一步并行，结果用列表 reducer 收拢。合同逐条审查、理赔逐个附件都是这个模式。扇出节点之一超时，不要让整单永远停住：给节点 `RetryPolicy` 和超时，失败写进该分支自己的结果（例如 `{"ok": false}`），汇总节点按「部分失败」继续，而不是让一个分支拖死整个超步且没有出口。

**21. 什么时候拆子图？**

一块逻辑能单独复用、单独测试、单独换人维护时拆。子图作为父图的一个节点。状态键和父图相同就直接合并；schema 不同就包一层节点，负责把父状态映射进去、把结果映射出来。交接用 `Command(graph=Command.PARENT, goto=...)`。在节点里当普通函数调用的子图，恢复时父节点会从头再跑，子图里的 `interrupt` 也会从该节点开头再跑，副作用同样要幂等。

**22. 多 Agent 用 Supervisor、Swarm 还是层级？**

默认 Supervisor：一个路由者把任务分给工人，再汇总，最好推理和排障。Swarm 是工人之间用 `Command(goto=...)` 点对点交接，灵活，但环和重复劳动难查。层级是 Supervisor 管 Supervisor，只在一个路由者成为瓶颈时再上。

一个模型加一组工具通常优于多个 Agent。多个 Agent 会把每次交接都变成额外的模型调用，三步任务可以膨胀成九次调用。只有工具集、提示词或权限边界已经互相干扰时才拆。工人之间不要无预算地互相回跳，要有共享的任务状态和明确的完成条件。

**23. Graph API 和 Functional API？**

都在同一运行时上，检查点、流式、中断都有。`StateGraph` 是声明式的，拓扑能画出来，适合分支多、多人维护。Functional API 用 `@entrypoint` 和 `@task` 把普通 Python 的 `if` / `for` 变成可持久化任务，适合给已有过程式代码补上恢复能力。`@task` 的结果会被记下，恢复时复用，用来包住非确定性和副作用。

**24. 聊天界面 15 秒没字、最后一次性弹出，怎么改？**

不要等 `invoke` 的最终状态。`stream_mode="messages"` 把模型 token 推到前端；进度用节点里的 `stream_writer`，前端用 `custom` 收。`updates` 用来显示「现在在哪个节点」。混合模式是 `stream_mode=["messages", "custom"]`，每条事件是 `(mode, data)`。完整 state 不要默认推给浏览器。

### 7.4 生产、测试和事故

**25. 节点拆多细？**

节点是重跑的单位。拆在你需要检查点、重试、暂停、单独追踪或分支的地方。每次外部写操作单独一个节点，每次要单独评测的模型调用单独一个节点。太粗，节点后半段失败会把前半段副作用再跑一遍。太细，每步都落检查点，图也没法读。

**26. 怎么测一张图？**

图用工厂函数创建，每个测试 `compile` 一个新的 `InMemorySaver`。三层就够：

- 节点：直接调用节点函数，或 `graph.nodes["research"].invoke(state)`
- 路由：给路由函数造 state，断言返回的节点名
- 半程：`update_state(config, values, as_node="step_2")` 假装前面跑过，`interrupt_after` 停在要断言的位置

模型换成返回固定消息（含固定 `tool_calls`）的假模型。人机流程再测两步：第一次 `invoke` 断言 `__interrupt__` 的载荷，第二次 `Command(resume=...)` 断言终态。单测通过不代表回答好，业务质量要另有评测集。

**27. 评测集和回归怎么做？国内一面很爱追问。**

评测集来自真实提问日志和业务同学的高频问题，不要只造「标准问答」。分层：单跳事实、多跳、该拒答的越界问题、时效问题。规模几十到几百条就能看趋势。检索看 recall@k / MRR，生成看要点命中和是否跟证据一致，另加拒答准确率。改提示词、换模型、调切块后在 CI 里重跑，看哪些 case 从对变错，而不是只看平均分。

**28. 延迟和费用从哪来，怎么压？**

延迟主要是串行的模型往返、工具的网络、过长的 `messages`、以及不必要的 Agent 交接。能并行的检索用多条边或 `Send`；历史用 `trim_messages` 或摘要节点，别让检查点里的消息无限涨。费用用三层预算卡住：`recursion_limit`、state 里的工具次数、单次运行的 token 或金额上限。同一输入的贵节点用 `CachePolicy`。追踪用 LangSmith 或 Langfuse，先看一步数和 token，再看哪一个节点在循环。

一夜之间 token 翻几倍、有的运行走到几百步：先在追踪里找没有出口的环和重复的相同工具参数，给重复调用熔断，再核对是不是路由提示词把失败又送回了同一个节点。

**29. 水平扩展时检查点要注意什么？**

多副本必须共享 Postgres（或其他支持并发的）checkpointer，内存和 SQLite 会让请求落到另一台机器时丢状态。`thread_id` 由网关按会话生成并一直带着走。大工具结果不要整份塞进 state，检查点每步都序列化全量状态；存对象存储的 id，节点要用时再取。表膨胀就按线程保留期清理，暂停中的审批线程不要被清掉。

**30. 图已经改版，几百个审批还停在旧图上，怎么发版？**

这些线程的快照是按旧节点名和旧状态字段写的。新图删节点、改字段名，恢复时对不上。发版前要么等这批暂停结束，要么写迁移：读旧 checkpoint，映射到新 schema，再恢复。进行中的长任务和代码版本要一起考虑，不能只滚动更新无状态服务。

**31. LangGraph、`create_agent`、Temporal / Step Functions 怎么选？**

模型在环里、要工具循环、人审和流式，用 LangGraph 或直接 `create_agent`。流程完全确定、没有模型分支，工作流引擎更合适，重试和补偿是它们的本行。两者也能搭配：确定性的长事务放工作流引擎，其中某个活动再调用一张图。框架提供的是状态、边界和可观测性，不提供智能。

**32. 工具很多、还要接 MCP 时怎么管？**

不要把上百个工具全塞进一次模型调用。按领域分组，路由到对应子图或 `create_agent`，每组控制在模型能稳定选择的数量。MCP 把外部工具接到同一套工具调用协议上，权限仍要在图里做：写操作校验调用者角色（角色来自 context，不来自模型输出），高后果工具走 `interrupt`。工具描述写清参数、副作用和何时不要用。

### 7.5 场景题

**退款草稿：金额超过阈值必须人审，通过后才能写账。**

状态放 `amount`、`draft`、`approved`、`ledger_id`。起草节点只生成草稿。下一节点若超过阈值就 `interrupt` 把草稿送出；未超过则写 `approved=true`。写账是单独节点，放在审批之后，用 `thread_id + refund_id` 做幂等键。checkpointer 用 Postgres，`durability="sync"`。拒绝则路由到结束并记下原因。

**恢复之后，出院小结在 EHR 里出现了两份。**

写 EHR 的代码在 `interrupt` 之前，或和会失败的逻辑放在同一个节点里，恢复时整段重跑。把「生成草稿」和「提交 EHR」拆开，提交节点先查幂等键；已提交则只把已有 id 写回 state。

**客服突然用了另一个顾客的订单历史。**

`thread_id` 被复用了：用了用户 id 以外的固定值、前端没换会话 id，或长期记忆的 namespace 没带 `user_id`。线程内记忆和跨线程 Store 都要按用户隔离。再查是不是把角色或他人上下文写进了会被模型看到的 state。

**Supervisor 总把网络故障分给数据库 Agent。**

看该步的追踪：路由节点的输入是否缺故障类型，工具描述是不是都在说「查数据」。给路由一个结构化输出（枚举工人名），并用一批标注过的事件做路由准确率回归。不要靠再加一个 Agent 来补救一个含糊的路由提示词。

### 7.6 手写：带上限的工具环

面试常要求白板写出来。结构如下，工具执行可换成 `ToolNode`。

```python
from typing import Annotated, Literal
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

class State(TypedDict):
    messages: Annotated[list, add_messages]
    steps: int

def agent(state: State) -> dict:
    if state["steps"] >= 5:
        return {"messages": [ai_message("已达工具上限，根据现有结果回答")]}
    response = llm_with_tools.invoke(state["messages"])
    return {"messages": [response], "steps": state["steps"] + 1}

def route(state: State) -> Literal["tools", "__end__"]:
    if state["steps"] >= 5:
        return END
    return tools_condition(state)

builder = StateGraph(State)
builder.add_node("agent", agent)
builder.add_node("tools", ToolNode(tools))
builder.add_edge(START, "agent")
builder.add_conditional_edges("agent", route)
builder.add_edge("tools", "agent")
graph = builder.compile(checkpointer=checkpointer)
```

口述时补三句：`messages` 必须用 `add_messages`；上限写在 state 里，`recursion_limit` 只是最后一道；生产上 `compile` 传 `PostgresSaver`，调用带稳定的 `thread_id`。

### 7.7 讲自己的项目时按这个顺序

国内一面通常从简历里的 Agent 项目往下挖，可以按六句准备：

1. 业务上有环：检索不够要改写后再检，工具失败要重试，关键动作要人审。线性 Chain 表达不了，所以用图。
2. state 里有对话、证据、计划、工具结果、重试计数、最终答案。临时变量和客户端不在里面。
3. 哪些字段是覆盖，哪些挂了 reducer。并行检索为什么不会互相覆盖。
4. checkpointer 用在哪：多轮记忆、失败续跑，还是审批。`thread_id` 按什么生成。
5. 评测集从哪来、分哪几层、改一次提示词之后哪类 case 变好或变坏。
6. 延迟预算：用户能等多久，哪些步并行，什么情况下放弃工具直接回答。

没有评测就直接说没有，并说出打算怎么补。编一个准确率比讲不清来源更容易被追问穿。
