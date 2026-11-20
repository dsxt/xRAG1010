# KuCoin 半远程 AI Agent 岗位面试题完整答案

以下按面试轮次逐题整理，偏向生产落地视角，重点覆盖成本、评测、权限、回放与异常兜底。


## 一面

### 1. 自我介绍

建议框架：Java 工程背景 → 转型 AI Agent 的契机 → 最近一个可量化的 Agent 项目（业务价值 + 技术亮点） → 为什么选择交易所场景。

示例结构：5 年 Java 后端，近 2 年主导某客服 Agent 从 0 到 1 落地，工具调用成功率从 72% 提升到 94%，人工接管率从 35% 降到 12%。技术栈：Spring Boot + LangChain4j + Redis + Kafka + MySQL。选择 KuCoin 是因为交易所的高风险场景（账户、交易、提币）对 Agent 的权限治理和异常兜底要求最高，这正是我想深耕的方向。

### 2. 最近做的 AI Agent 项目介绍

按“业务目标 → 架构分层 → 关键技术决策 → 量化结果”四段式展开。业务目标：客服场景下自动处理订单查询、退款申请、工单创建。架构分层：接入层（渠道适配 + 鉴权）→ 编排层（LangGraph 状态机 + 意图路由）→ 能力层（RAG 检索 + 工具调用）→ 基础设施层（Redis 会话 + MySQL 日志 + Kafka 异步任务）。关键技术决策：为什么用状态机而非纯 ReAct 循环（高危操作需要确定性边界）；为什么 RAG 用混合检索而非纯向量（工单编号、订单 ID 需要精确匹配）。量化结果：首解率、平均处理时长、Token 成本、人工接管率。

### 3. Agent 面向哪类业务，整体链路怎么画

以交易所客服为例，链路为：

**用户输入 → 接入层（渠道识别/鉴权） → 意图路由（规则+分类模型+LLM 三级） → 编排层（LangGraph 状态机：条件分支/循环/中断） → 能力层（RAG 检索 / 工具调用 / 多 Agent Handoff） → 执行层（API 网关 + 幂等键 + 权限校验） → 输出层（回复生成 + 审计日志）**

关键设计点：编排层用确定性状态机控制业务边界，节点内部用 LLM 处理不确定问题，实现“确定性骨架 + 概率性智能”。

### 4. 意图识别怎么做，规则、分类模型、LLM 路由分别在哪一层

采用三级混合路由架构：

- **第一级：规则层**（正则/关键词/命令前缀）。处理明确指令类请求，延迟 <5ms，零成本。适用场景：“查订单 #12345”“退款”“转人工”。
- **第二级：分类模型层**（轻量 BERT/小模型）。处理自然语言变体，输出可校准的概率分布。必须设置置信度阈值和拒识出口——低置信度不走强制路由，而是进入澄清或兜底。
- **第三级：LLM 路由层**。处理模糊、多意图、需要上下文理解的请求。仅在规则和分类模型都无法高置信度判定时触发。

部署位置：规则层在最前置的网关或编排入口；分类模型作为独立微服务，与路由决策解耦；LLM 路由作为状态机中的一个条件节点。

### 5. 用户模糊输入“帮我看看我账户是不是有点不对”如何判别意图

这是一个典型的意图不确定场景。处理策略：

第一步，**规则层无法命中**（没有明确命令词和实体）。

第二步，**分类模型输出低置信度分布**——可能在“账户查询”“交易记录查询”“异常检测”“客服转接”之间分散。

第三步，**进入 LLM 路由节点**，但不是直接路由到某个工具，而是**生成澄清问题**：“您是指账户余额异常、交易记录不对，还是登录/安全方面的问题？”这是一个 Human-in-the-loop 的轻量形式——用反问代替猜测。

第四步，如果用户仍然模糊，**降级到人工接管**。关键原则：模糊输入不进高危工具路径。交易所场景下，账户相关操作必须有明确的用户确认。

### 6. 意图识别和下游工具路由如何解耦

核心设计：**意图标签 ≠ 工具调用**，中间加一层“路由映射表”。

- 意图识别只输出标准化标签（如 `query_balance`、`query_transaction`、`request_withdrawal`）。
- 路由映射表维护“意图标签 → 工具候选集 + 参数模板 + 权限要求”的映射关系，存储在配置中心而非代码中。
- 状态机根据意图标签查映射表，决定进入哪个节点分支。

解耦效果：修改一个意图的映射关系不影响其他意图；新增意图只需在映射表中加一行 + 注册对应工具；意图识别模块和工具执行模块独立部署、独立回滚。

### 7. RAG 多路召回做了哪几路

生产级 RAG 通常做 **4-6 路召回**：

| 召回路径 | 解决的问题 | 典型场景 |
|---|---|---|
| 稠密向量 | 语义模糊匹配、口语化提问 | “怎么修改密码”匹配到“账户安全设置” |
| BM25/稀疏 | 精确关键词、编号、专有名词 | 订单号 #12345、错误码 TS-999 |
| 元数据过滤 | 权限、时效、版本控制 | 只召回当前用户有权访问的文档 |
| 知识图谱 | 多跳关系推理 | “A 客户的关联账户有哪些” |
| SQL 检索 | 结构化数据查询 | 交易记录、账户余额 |

向量检索擅长语义泛化，但对专有名词和编号极度不敏感；BM25 精准匹配能力强但不懂语义改写。两者互补是生产基线。

### 8. 多路召回分数不可比，如何做结果融合、去重、Rerank

**融合前必须归一化。** 不同检索器的原始分数不在同一尺度，不能直接加权。

**融合方案（按推荐优先级）：**

① **RRF（Reciprocal Rank Fusion）** ——生产默认方案。公式：`RRF(d) = Σ 1/(k + rank_i(d))`。只使用排名，不依赖原始分数尺度，对异构检索器稳定。

② **归一化加权融合**——当需要对不同召回源设置不同权重时使用。先对每路分数做 min-max 或 z-score 归一化，再加权求和：`final_score = 0.5 × dense_normalized + 0.3 × bm25_normalized + 0.2 × sparse_normalized`。权重必须通过业务评测集校准。

③ **Rerank 精排**——融合后的 Top-K 候选进入 Cross-Encoder Reranker 做精排。Rerank 的输入数量要控制，过大导致尾延迟上升。

**去重策略**：按 `document_id` 和内容哈希去重；对同一文档的多个 chunk 做父子合并或摘要保留。

**过滤顺序**：安全过滤（ACL/租户）必须在候选进入模型之前完成，推荐顺序为：租户/ACL 硬过滤 → 有效期/状态过滤 → 向量/关键词召回 → 业务软权重 → 重排。

### 9. 三层记忆划分：短期、工作记忆、长期记忆

| 层级 | 存储位置 | 写入策略 | 淘汰策略 | 生命周期 |
|---|---|---|---|---|
| **瞬时记忆** | LLM 上下文窗口 | 每轮对话直接追加 | 对话结束或超阈值时丢弃 | 单轮 |
| **短期/工作记忆** | Redis（JSON/Hash） | 每轮结束后提炼关键事实写入 | 时间衰减 + 低权重清理，满足条件晋升长期 | 数小时到数天 |
| **长期记忆** | 向量库 + MySQL/图数据库 | 短期记忆满足晋升条件后写入 | 按需召回，不全部加载；周期任务做去重和冲突消解 | 持久 |

流转链路：瞬时记忆提取信息 → 存入短期记忆 → 持续压缩去重 → 满足条件晋升长期记忆；长期记忆也可被召回，临时加载进短期记忆参与交互。

记忆对象的标准字段包括：`memory_id`、`content`、`embedding`、`create_time`、`last_access_time`、`weight`（时间衰减载体）、`version`（冲突处理）、`is_valid`。

### 10. 上下文溢出时，摘要压缩、滑动窗口、关键事实抽取如何配合

三者**分层配合**，不是三选一：

- **滑动窗口保活**：始终保留最近 N 轮原文不压缩，保证短期连续性。
- **摘要压缩处理中段**：对超出窗口的早期对话，先用 LLM 生成分层摘要（整体摘要 + 分主题摘要），保留 gist 但会丢失具体细节如文件路径和 ID。
- **关键事实抽取保护高价值信息**：对交易金额、订单号、用户确认过的操作等高风险事实，用抽取式压缩单独保留，附引用可回溯。

**配合策略**：先按优先级将上下文拆分为 System 规则/用户硬约束/当前目标/已确认事实/工具状态/普通闲聊，对普通闲聊做摘要，对高价值事实做抽取保留，对原文做外置存储 + 保留引用。

### 11. 图数据库在 Agent 里存储什么，和向量库的职责边界

**图数据库（Neo4j）存储**：实体-关系-实体三元组。典型内容：用户与账户的持有关系、账户间的转账路径、设备与 IP 的关联关系、客服工单与订单/用户的关联。适合多跳关系查询，例如“这个用户控制的所有账户中，哪些最近有异常登录”。

**向量库存储**：文档 chunk 的语义嵌入。适合语义相似性搜索，例如“找和这段客服对话语义相似的工单”。

**职责边界**：图数据库回答“**谁和谁有什么关系**”（关系推理、路径查询、聚合计数）；向量库回答“**什么内容和这个问题最像**”（语义检索、模糊匹配、文档召回）。两者通过文档 ID 关联，GraphRAG 的典型架构是：向量检索找到入口节点 → 图遍历扩展相邻实体 → 取回关联文档 chunk。

### 12. LangChain 和 LangGraph 在项目里的角色，为什么不只用 Chain

**LangChain** 提供基础抽象和工具集成：LLM Client、Prompt Template、Tool 定义、Retriever、Memory 接口。角色是“**能力供给层**”。

**LangGraph** 提供状态机编排：State、Node、Edge、Conditional Edge、Checkpointer、Interrupt。角色是“**流程控制层**”。

**为什么不只用 Chain**：Chain 是线性流水线，无法处理条件分支、循环、中断恢复、人工审批。生产 Agent 需要“执行到一半暂停等人工确认，确认后从断点继续”，Chain 做不到。LangGraph 的状态机模型天然支持 Checkpoint + Interrupt + Resume。

**协作方式**：LangGraph 的 Node 内部调用 LangChain 的 Chain 或 Tool；LangGraph 管理节点间的状态流转和分支条件。

### 13. Redis 在 Agent 系统中的多重角色

| 用途 | 实现方式 | 注意事项 |
|---|---|---|
| **会话状态** | Hash 存储 session_id → state JSON | 设置 TTL，定期持久化到 MySQL |
| **限流** | 滑动窗口计数（INCR + EXPIRE） | 按用户/按工具维度分别限流 |
| **分布式锁** | SET NX EX + Redlock | 保护高危工具的串行执行 |
| **任务队列** | List / Stream 做轻量异步任务 | 仅适合轻量场景，重型任务走 Kafka |
| **缓存** | 意图识别结果、RAG 检索结果、工具 Schema | 注意缓存失效策略与版本号 |

交易所场景下，**分布式锁**是保护提币类操作的关键——同一用户的并发提币请求必须串行化。

### 14. MySQL 存储 Agent 执行日志、会话记录的表结构设计

**核心表：**

① `agent_session`——会话主表。字段：`session_id`（PK）、`user_id`、`channel`、`status`、`created_at`、`updated_at`、`intent_tag`、`resolution_status`。

② `agent_trace`——执行轨迹表。字段：`trace_id`（PK）、`session_id`（FK）、`step_index`、`node_name`、`input_summary`（截断存储）、`output_summary`、`tool_name`、`token_input`、`token_output`、`latency_ms`、`status`、`error_type`。

③ `agent_tool_call`——工具调用明细。字段：`call_id`、`trace_id`、`tool_name`、`arguments`（JSON）、`result_summary`、`idempotency_key`、`executed_at`。

**热点表优化**：`agent_trace` 按月分表，热数据（近 7 天）在 MySQL，冷数据归档到对象存储。高频查询字段（`session_id`、`trace_id`、`user_id`）建联合索引。`input_summary`/`output_summary` 截断到 500 字符，完整内容存 OSS 并在表中保留引用。

### 15. 手写算法：最简意图路由函数

```java
public class IntentRouter {
    private final List<IntentRule> rules;
    private final String fallbackIntent;

    public IntentRouter(List<IntentRule> rules, String fallbackIntent) {
        this.rules = rules;
        this.fallbackIntent = fallbackIntent;
    }

    public RouteResult route(String userInput) {
        String lowerInput = userInput.toLowerCase();

        // 按优先级遍历规则
        for (IntentRule rule : rules) {
            if (rule.matches(lowerInput)) {
                return new RouteResult(rule.getIntent(), 
                    rule.getConfidence(), "rule", rule.getMatchedKeyword());
            }
        }

        // 无命中，返回兜底
        return new RouteResult(fallbackIntent, 0.0, "fallback", null);
    }
}

class IntentRule {
    private final String intent;
    private final List<String> keywords;
    private final double confidence;

    public boolean matches(String input) {
        return keywords.stream().anyMatch(input::contains);
    }
    // getters...
}

class RouteResult {
    private final String intent;
    private final double confidence;
    private final String source;       // "rule" | "fallback"
    private final String matchedKeyword;
    // constructor, getters...
}
```


## 二面

### 1. 自我介绍

同前，但二面侧重：强调项目中遇到的技术难题、架构设计决策、与工程团队的协作方式。

### 2. Agent 工作流包含哪几类节点；条件分支、循环如何落地

**节点类型**：LLM 推理节点、工具执行节点、条件判断节点、人工审批节点、数据转换节点、检索节点。

**条件分支落地**：LangGraph 中用 `add_conditional_edges`，根据 State 中的字段值（如 `intent_tag`、`quality_score`）决定走向哪个下游节点。

**循环落地**：通过 Edge 从下游节点指回上游节点，配合 State 中的 `retry_count` 字段做循环次数控制。达到上限时走兜底分支。

### 3. LangGraph 的 State、Node、Edge、Condition、Checkpointer 如何映射业务

以交易所客服 Agent 为例：

| 概念 | 业务映射 |
|---|---|
| State | 全局上下文：用户 ID、会话 ID、意图标签、已收集参数、工具执行结果、重试次数、人工审批状态 |
| Node | 每个业务步骤：意图识别、参数收集、工具调用、结果校验、回复生成 |
| Edge | 确定性流转：意图识别 → 参数收集（无条件） |
| Conditional Edge | 条件分支：参数完整 → 工具调用；参数缺失 → 追问用户；置信度低 → 转人工 |
| Checkpointer | 每步执行后持久化 State，支持断点恢复和回放 |

关键设计：State 显式建模业务状态，而非只维护 `messages` 列表。即使模型上下文重新组装，系统仍然知道任务执行到了哪里。

### 4. 不使用 LangGraph，用 Java 自研 Agent 状态机如何设计

**核心组件**：

① **State 定义**：用 Java Record 或不可变类定义状态快照，包含 `Map<String, Object>` 的扩展字段。利用 Sealed Classes 限定状态类型。

② **Node 接口**：`Node<S> { S execute(S state, NodeContext ctx); }`。每个 Node 实现一个业务步骤。

③ **Edge/Transition 定义**：用声明式 DSL 注册流转规则：`from("intent").to("param_collect").when(state -> state.hasIntent())`。

④ **State Machine Engine**：执行循环——根据当前 State 找到匹配的 Transition，执行目标 Node，更新 State，直到到达 END 或中断点。

⑤ **Checkpointer**：每步后将 State 序列化存入 Redis/MySQL，支持从任意步骤恢复。

**与 LangGraph 的差异**：Java 自研的优势在于与现有 Spring 生态无缝集成、强类型、事务安全；劣势是需要自己实现 Checkpoint、Interrupt、并行执行等机制。

### 5. 对 Harness 工程的理解

Harness 是模型之外、把模型包装成可用产品的那一整层工程。核心公式：`Agent = Model + Harness`，模型负责“思考”，Harness 负责让思考可理解、可协作、可复现、可长期运行。复杂 Agent 中，模型可能只完成 20% 的工作，剩下 80% 来自 Harness。

**分层映射**：

| 能力 | 所在层 |
|---|---|
| 上下文装配 | Harness 层——决定哪些信息进入模型上下文，怎么压缩和排序 |
| 工具调度 | Harness 层——Tool Registry、参数校验、权限过滤、执行编排 |
| 异常兜底 | Harness 层——重试策略、降级链路、人工接管触发 |
| 回放评测 | Harness 层——Trace 记录、离线回放、指标计算、回归测试 |

**关键洞察**：同一个模型、同一套题，换一套 Harness 去跑，完成率能差出十几二十个百分点，这个差值常常比两代模型之间的差距还大。

### 6. 工具调用失败类型及处理策略

| 失败类型 | 根因 | 处理策略 |
|---|---|---|
| **参数错误** | 模型生成参数格式不对、缺少必填字段、类型不匹配 | Schema 校验拦截 → 返回明确错误给模型 → 模型修正后重试（带修正提示） |
| **超时** | 下游服务慢、网络抖动 | 先查外部状态（幂等键查询）→ 确认未执行后再受限重试（指数退避） |
| **脏数据** | 下游返回格式异常、字段缺失 | 输入清洗 + 输出校验 → 校验失败走降级链路（返回简化结果或转人工） |
| **模型乱调用** | 模型选错工具、编造工具名 | Tool Registry 白名单过滤 → 调用前校验工具是否存在 → 未注册工具直接拒绝 |

核心原则：**参数错误给模型反馈让它自我修正；超时不能盲目重试，先查状态；脏数据走降级；乱调用做白名单兜底**。

### 7. 高危工具调用的幂等键设计

**幂等键设计**：`idempotency_key = hash(业务单据号 + 动作类型 + 业务日期 + 用户ID)`。写入去重表，加排他约束。

**重试机制**：

① 超时类错误不能马上重试，先用幂等键或 `external_request_id` 查询外部状态，确认未执行后再进入受限重试。

② 三态状态机：`PENDING → EXECUTING → SUCCESS/FAILED/UNKNOWN`。UNKNOWN 状态（超时、连接中断后不确定是否执行成功）是最危险的，必须通过状态查询或对账来消解。

③ 幂等键的生命周期绑定在“一次业务意图”上，而非“一次请求”上。用 UUID4 现场生成幂等键会导致重试时无法识别为同一逻辑动作。

④ 补偿栈：如果部分步骤成功部分失败，按逆序执行补偿操作（如先撤销退款申请再撤销工单）。

### 8. 多 Agent 协作拓扑选型

| 拓扑 | 确定性 | 并行性 | 上下文隔离 | 适用场景 |
|---|---|---|---|---|
| **Supervisor/Worker** | 中到高 | 高 | 高 | 通用生产级多 Agent |
| **Router/Handoff** | 中 | 中 | 高 | 客服、专家路由 |
| **Debate/Judge** | 中 | 中 | 中 | 决策、验证、推理 |
| **DAG** | 高 | 高 | 高 | 审批、文档流水线 |

**交易所场景推荐**：**Supervisor/Worker** 作为主干。Supervisor 维护目标、任务图、依赖和完成判断；Worker 在受限上下文中执行子任务。Handoff 用于需要“接管用户交互”的场景（如从普通客服 Agent 转接到风控 Agent），但 Handoff 需要原子性和重新授权。

**选型理由**：Supervisor 模式上下文隔离好（Worker 看不到全局状态，降低越权风险），工程复杂度可控，确定性中到高。

### 9. 多 Agent 之间上下文传递方案；防止消息膨胀和角色越权

**上下文传递**：通过 State 对象传递，而非直接传递完整对话历史。每个 Agent 只接收与其任务相关的 State 切片（Sub-State），包括任务描述、约束条件、输入数据、期望输出格式。

**防消息膨胀**：设置每个 Agent 的输入长度上限；历史对话压缩为摘要 + 关键事实；工具返回结果做裁剪或摘要后再注入。

**防角色越权**：每个 Agent 分配独立的权限凭证（最小权限原则）；Agent 只能调用自己白名单内的工具；跨 Agent 的操作需要通过 Supervisor 中转并重新校验权限。

### 10. RAG 切片策略

| 策略 | 适用场景 | 优缺点 |
|---|---|---|
| **固定长度切分** | 通用文本 | 简单但可能切断语义 |
| **语义切分** | 段落结构清晰的文档 | 保持语义完整但计算成本高 |
| **标题层级切分** | 结构化文档（合同、制度） | 保留层级关系但依赖文档结构质量 |

**表格处理**：将表格转为 Markdown 格式保留结构，表头作为元数据字段单独索引；小表格整表作为一个 chunk，大表格按行拆分并保留表头引用。

**PDF 处理**：先用版面分析工具（如 PaddleOCR/unstructured）提取文本块、表格和图片，按阅读顺序重排后再切片。

### 11. 文档更新后的向量库增量同步

**方案**：

① 文档更新时，生成新的 `document_version`。② 通过 Kafka 发送“文档更新事件”，消费端触发异步 embedding 重建。③ 新 chunk 写入后，标记旧版本 chunk 为 `is_valid = false`，不物理删除。④ 查询时在元数据过滤中加 `is_valid = true AND version = latest`，优先加载新版本。⑤ 定期任务清理无效 chunk 的向量索引。

**版本冲突处理**：同一 `document_id` 存在多个版本时，按 `effective_date` 和 `version` 取最新有效版本。

### 12. Kafka 在 Agent 系统中的用途

| 用途 | Topic 设计 | 说明 |
|---|---|---|
| **任务队列** | `agent-task-request` | 批量文档解析、切片、向量化入库；长文本多步推理任务异步执行 |
| **日志回流** | `agent-trace-log` | 每次工具调用、每步推理的 trace 异步写入，用于可观测和离线评测 |
| **评测数据** | `agent-eval-batch` | 将线上 trace 导出为评测样本，回流到评测流水线 |
| **异步 embedding** | `doc-embedding-request` | 文档更新后触发 embedding 重建，不阻塞 serving |

**关键原则**：文档 ingestion、批量 embedding、长 Agent Run、RAG re-index 都不能靠同步 HTTP 扛，必须走异步队列。

### 13. ES + 向量库混合检索的坑

| 坑 | 处理方案 |
|---|---|
| **中文分词** | 写入用 `ik_max_word`（细粒度，召回全），查询用 `ik_smart`（粗粒度，精度高），即“非对称分词” |
| **同义词** | 维护同义词词典（如“提币 → 提现 → withdrawal”），在稀疏检索侧做扩展，不插入 LLM prompt |
| **编号/错误码** | 对编号字段禁用分词（`keyword` 类型），精确匹配；BM25 字段提高权重 |
| **专有名词** | 维护专有名词词典，对专有名词做精确匹配 + 同义词扩展双路召回 |
| **大小写敏感** | 对函数名、变量名、ID 等做大小写归一化后再索引 |

### 14. 手写算法：带最大步数限制的 ReAct 循环骨架

```java
public class ReActLoop {
    private final LLMClient llm;
    private final ToolRegistry tools;
    private final int maxSteps;

    public ReActResult run(String userInput) {
        List<Step> steps = new ArrayList<>();
        String context = userInput;

        for (int i = 0; i < maxSteps; i++) {
            // 1. Thought
            String thought = llm.generateThought(context);

            // 2. Action: 模型选择工具和参数
            ToolCall call = llm.selectTool(context, thought, tools.listSchemas());
            if (call == null) {
                // 模型认为无需工具，直接输出最终答案
                return new ReActResult(llm.finalAnswer(context, thought), steps, "completed");
            }

            // 3. Execute tool
            Observation obs;
            try {
                obs = tools.execute(call.getName(), call.getArgs());
            } catch (ToolExecutionException e) {
                obs = Observation.error(e.getMessage());
            }

            steps.add(new Step(thought, call, obs));
            context = context + "\nThought: " + thought 
                    + "\nAction: " + call + "\nObservation: " + obs;
        }

        // 死循环强制退出
        return new ReActResult("达到最大步数限制，任务未完成", steps, "max_steps_exceeded");
    }
}
```

关键点：`maxSteps` 硬限制；每轮记录 Thought/Action/Observation；超限时返回明确的“未完成”状态而非静默失败。


## 三面

### 1. 最难的 Agent 线上故障

**案例框架**：描述一个典型的 Agent 生产事故——例如重复退款。原因是工具超时重试时没有携带稳定幂等键，导致服务端认为两次调用是不同的业务请求。定位方法：通过 trace_id 串联两个请求，发现幂等键是 UUID4 现场生成，每次不同。修复方案：改用业务稳定的幂等键（订单号 + 动作类型 + 日期）；超时后先查状态再重试。修复后指标：重复退款率从 0.3% 降到 0。

### 2. Agent 效果如何验证，评测集构建与指标计算

**评测集构建**：从线上真实 trace 中采样，覆盖高频意图、边界 case、高风险操作。每个样本包含：输入、期望意图标签、期望工具调用序列、期望最终结果。

**指标计算**：

- **任务成功率** = 端到端完成的样本数 / 总样本数。TSR 低于 80% 意味着能力存在根本性缺口。
- **工具调用成功率** = 工具调用返回有效结果的次数 / 总工具调用次数。需要区分“调用成功”和“结果正确”。
- **幻觉率** = 包含无根据断言的输出数 / 总输出数。通过对比检索到的上下文与生成内容做判定。
- **人工接管率** = 转人工的会话数 / 总会话数。反映 Agent 的自主完成能力。

### 3. BadCase 回流机制与 Prompt 迭代回归测试

**回流机制**：线上 trace 标记为 BadCase → 提取输入、中间步骤、期望输出 → 加入评测集 → 触发 Prompt 回归测试。

**防破坏原能力的回归测试**：每次 Prompt 变更后，在固定评测集上跑全量回归。评测集分为：核心集（不可退化的基线用例）、边界集（容易出错的 case）、新增集（针对当前 BadCase 的用例）。变更后的 Prompt 必须在核心集上不低于基线分数，在新增集上有提升。

### 4. GraphRAG 场景题：先图检索还是先向量检索

**取决于查询类型**：

- **关系型查询**（“A 的关联账户有哪些”“从 A 到 B 的转账路径”）→ **先图检索**。先在图数据库中做实体匹配和关系遍历，再用关联的 document_id 去向量库取原文。
- **证据型查询**（“帮我找关于这个故障码的文档”“和这段对话相似的工单”）→ **先向量检索**。先用向量相似度找到相关 chunk，再用 chunk 关联的实体去图中扩展上下文。
- **混合型查询**：先向量检索找到入口节点，再做图遍历扩展。这是 GraphRAG 最常见的模式——向量检索负责“找到起点”，图遍历负责“展开上下文”。

**交易所场景**：账户关系查询走图优先，文档知识查询走向量优先。

### 5. 多Agent生产环境风险优先级排序

**优先级（从高到低）** ：

① **权限越权**——最高优先级。Agent 调用了不该调用的工具（如客服 Agent 调用了提币 API），造成直接资金损失。

② **成本突增**——第二优先级。循环调用或上下文膨胀导致 Token 消耗失控，可能在短时间内产生大量费用。

③ **循环调用**——第三优先级。Agent A 调用 Agent B，B 又调回 A，形成死循环。需要设置全局步数上限和循环检测。

④ **状态不一致**——第四优先级。多 Agent 并行执行时对共享状态的读写冲突。通过状态所有权和版本控制解决。

**排序理由**：权限越权直接关联资金安全；成本突增影响可控但可快速扩大；循环调用和状态不一致更多是效率问题。

### 6. 高危操作接入 Human-in-the-loop

**设计**：在状态机中设置**审批断点（Interrupt）** 。当 Agent 准备执行高危操作时，State 中的 `requires_approval = true`，状态机在此中断，将当前操作详情推送到审批工作台。

**审批流程**：操作摘要 → 风险等级标注 → 审批人确认 → 确认后状态机 Resume，携带审批凭证继续执行；拒绝则走取消分支。

**交易所场景**：提币、大额转账、删除数据必须走 HITL。小额退款可设置自动审批阈值。审批断点必须是状态机的正式节点，而非嵌入在循环中的临时逻辑。

### 7. 提示词注入的防御

**攻击场景**：外部网页/邮件/用户输入中包含隐藏指令，诱导 Agent 偏离原有目标执行攻击者意图。

**分层防御**：

① **输入层**：对外部内容做来源标记和不可信隔离，杜绝“看到即执行”。用特殊标记包裹外部内容，明确告知模型“以下内容来自不可信来源”。

② **工具层**：为每个工具设置独立的最小权限凭证，在沙箱中运行。高权限工具不允许被外部内容直接触发。

③ **决策层**：对高影响动作（删除、外发、支付）引入人工确认或策略网关。

④ **观测层**：记录每一步推理与调用，确保事后可审计、可回溯。

核心原则：**让智能体“能做事，但做不了大事”** 。

### 8. 模型路由设计

| 环节 | 模型选择 | 理由 |
|---|---|---|
| 意图识别 | 小模型/分类模型 | 判别任务，不需要生成，小模型足够且成本低 |
| 参数提取 | 小模型 | 结构化抽取，规则+小模型可覆盖大部分场景 |
| 工具选择 | 中模型 | 需要理解工具语义和当前上下文 |
| 复杂推理/最终回复 | 强模型 | 需要深度理解和生成质量 |

**降级链路**：强模型超时或不可用时，降级到中模型；中模型不可用时，降级到规则引擎 + 模板回复。

### 9. 可观测体系搭建

**四层串联**：

① **Trace 层**：每个请求生成 `trace_id`，记录每一步的 prompt、token、决策、工具调用、耗时。用 OpenTelemetry 标准导出。

② **成本层**：每个 span 记录 input/output token 和费用，归因到模型、工具、Agent 步骤。

③ **质量层**：召回结果、幻觉检测、用户反馈（点赞/点踩/转人工）关联到 trace_id。

④ **告警层**：异常检测——无限循环、上下文耗尽、成本突增、错误率突增。实测可将 MTTR 从 47 分钟压到 4 分钟。

### 10. Java + SpringAI / LangChain4j + LangGraph 技术选型

**分层选型建议**：

- **基础 AI 能力集成**：Spring AI。与 Spring Boot 生态无缝集成，提供 Model、Vector Store、Tool Calling、Structured Output 的统一抽象。适合已有 Spring 应用逐步增加 AI 能力。
- **RAG + 工具调用**：LangChain4j。提供更丰富的 Provider 集成和 RAG 组件，适合需要广覆盖 LLM 提供商的场景。
- **复杂状态编排**：LangGraph4j（Java 版）或自研状态机。如果团队已有 Java 工程能力，自研状态机可以与现有基础设施（事务、Redis、Kafka）深度集成。
- **最佳实践**：Spring AI 做能力层，LangChain4j 做 RAG 和工具层，自研或 LangGraph4j 做编排层。

### 11. AI 工程方向学习情况

重点准备 2-3 个方向，能说出具体实践：

- **MCP**：理解 Host-Client-Server 架构，实际用过 MCP 连接工具和数据源。标准化了 Agent 与外部工具的接口，支持工具发现和统一权限审计。
- **Eval**：搭建过评测集和回归测试流水线，理解任务成功率、工具调用准确率、幻觉率的计算方式。
- **Harness**：理解 Harness 即产品的理念，在实践中落地了上下文管理、工具调度、异常兜底和回放评测。

### 12. 新 AI 框架从 Demo 落地生产：只调用 API 还是阅读源码

**两者都要，但顺序和深度不同**：

- **第一阶段（快速验证）** ：只调 API，用最小代码验证核心能力是否满足需求。重点是快速试错。
- **第二阶段（生产落地）** ：必须读源码。需要理解框架的异常处理机制、重试语义、并发模型、内存管理，才能在出现问题时定位和修复。特别是涉及高危操作时，必须确认框架的幂等和重试行为是否符合业务要求。

### 13. 手写算法：简化 Tool Dispatcher

```java
public class ToolDispatcher {
    private final Map<String, ToolDefinition> registry;

    public DispatchResult dispatch(String userRequest, String intentTag) {
        // 1. 工具候选召回
        List<ToolDefinition> candidates = registry.values().stream()
            .filter(t -> t.supportsIntent(intentTag))
            .sorted(Comparator.comparingDouble(ToolDefinition::getRelevance).reversed())
            .limit(5)
            .collect(Collectors.toList());

        // 2. 参数提取（简化：从请求中抽取键值对）
        Map<String, Object> params = extractParams(userRequest, candidates);

        // 3. Schema 校验
        for (ToolDefinition tool : candidates) {
            ValidationResult vr = tool.validateParams(params);
            if (vr.isValid()) {
                return DispatchResult.success(tool.getName(), params);
            }
        }

        // 4. 校验失败降级
        return DispatchResult.degraded("无匹配工具或参数校验失败");
    }

    private Map<String, Object> extractParams(String request, List<ToolDefinition> candidates) {
        // 简化实现：从请求文本中按候选工具的参数名抽取
        Map<String, Object> params = new HashMap<>();
        for (ToolDefinition tool : candidates) {
            for (String paramName : tool.getParamNames()) {
                // 实际实现用正则/NER/LLM 抽取
                params.put(paramName, extractValue(request, paramName));
            }
        }
        return params;
    }
}
```


## HR 面

### 1. 自我介绍 + 为什么选择 KuCoin 交易所 AI 岗位

**为什么 KuCoin**：交易所场景是 AI Agent 落地难度最高的场景之一——涉及资金安全、合规约束、高并发。这种高压场景最能锻炼 Agent 的权限治理和异常兜底能力。我的 Java 工程背景让我在构建可靠的生产级 Agent 系统上有天然优势。

### 2. 半远程办公能否胜任

**沟通同步方案**：核心重叠时段（北京时间 10:00-16:00）保证在线响应；异步文档先行——需求、设计决策、变更记录全部文档化，减少同步沟通成本；每日简短站会同步进度和阻塞。

**自我管理**：有独立交付完整项目的经验，能自主拆解任务、管理优先级、按时交付。

### 3. 离职原因

不要只说“想转 AI”。框架：上一家业务已经进入稳定维护期，技术挑战有限；主动选择转向 AI Agent 方向，因为这是未来 3-5 年最具工程价值的领域；选择交易所是因为高风险场景对工程能力的要求最高。

### 4. 薪资预期与加密行业波动

薪资预期给出合理区间，同时表达对期权/代币激励的开放态度。关于行业波动：加密行业确实有周期性，但交易所作为行业基础设施，在牛熊周期中都有真实业务需求；AI Agent 的价值在于降低运营成本、提升服务效率，这在任何市场环境下都是刚需。

### 5. 紧急需求：明天接入一个完全陌生的 Agent 场景

第一步是**理解业务本质**：这个场景涉及哪些实体、哪些操作、哪些风险等级。第二步是**梳理最小可行路径**：先跑通“意图识别 → 工具调用 → 结果返回”的主链路，边缘 case 后续迭代。第三步是**确认安全边界**：哪些操作是高危的、需要人工审批的、需要幂等保护的。

### 6. 和算法、前端、风控、合规同学意见冲突

**处理原则**：先对齐目标而非方案——分歧往往来自对不同约束的优先级判断不同。风控和合规的诉求是风险控制，算法追求效果上限，前端关注用户体验，后端关注系统可靠性。找到满足多方核心约束的最小可行方案，先落地再迭代。

### 7. 核心竞争力：Java 工程底子 vs AI 系统嗅觉

两者不是二选一，而是**乘积关系**。AI 系统嗅觉决定“做什么”，Java 工程底子决定“能不能做好”。交易所场景下，AI 系统嗅觉让我知道什么场景适合用 Agent、什么场景应该用规则；Java 工程底子让我能构建高可靠、可观测、可回放的生产系统。如果必须选一个更值钱的：**在交易所场景下，Java 工程底子更值钱**——因为资金安全场景下，系统可靠性是底线，没有这个底线，再好的 AI 效果也没有意义。

### 8. 入职前空窗期：刷题还是落地真实 Agent 项目

**落地真实 Agent 项目**。刷题可以保持手感，但落地项目能回答“你在生产环境中遇到过什么问题、怎么解决的”。空窗期我会做一个完整的 Agent 项目，覆盖 RAG + 工具调用 + 权限控制 + 评测，并在 GitHub 上开源，作为面试作品集。同时保持每天 1-2 道算法题的手感。