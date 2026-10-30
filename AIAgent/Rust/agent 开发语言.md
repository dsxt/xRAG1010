# Agent 开发语言

> 本文把 Agent 生产链路落到三种语言上：Python 写会变的智能与规则，TypeScript 写用户入口，Rust 写高并发网关、模型路由和安全隔离。  
> 业务立项、行业拆解、ROI 与值班复盘以《agent落地手册》为准；切片、治理、容灾、攻击面的流程细节以《agent全流程》为准。本文只回答：这些能力由谁实现，以及代码骨架长什么样。

高并发接入网关和模型网关都用 Rust。实例前面可以挂 Nginx 或云负载均衡，只做 TLS 终结和流量分发；鉴权、限流、词库、熔断、模型切换写在 Rust 进程里。

---

## 1. 三层分工

| 层 | 语言 | 负责 | 不负责 |
|---|---|---|---|
| 交互层 | TypeScript | Web、CLI、Copilot、MCP 客户端、会话展示、流式渲染 | 最终授权、沙箱、高并发数据面 |
| 编排层 | Python | 规划、状态机、RAG 业务链路、工具编排、策略规则、Prompt 与评测 | 作为唯一公网入口扛高并发 |
| 底座层 | Rust | 接入网关、模型网关、沙箱、热点计算、本地单二进制 | 高频变化的业务流程 |

一句话：**交互层管输入输出，编排层管业务智能，底座层管流量、隔离和性能。**

三条生产约束贯穿三层：

1. 模型只提出下一步，权限、金额、数据范围和风险由确定性代码判定。
2. 用户输入、检索片段、网页、工具结果、其他 Agent 的输出都按不可信数据处理。
3. 一次发布是版本集合：代码、Prompt、模型路由、策略、工具契约、词库、索引要能在同一条 Trace 里对上。

---

## 2. 语言定位

### 2.1 Python：编排与策略

Python 承担会频繁变化、又强依赖 AI 生态的部分。

- 模型调用之后的决策：规划、工具选择、结构化输出校验、有限次重试
- RAG：清洗、切片、Query 改写、多路召回融合、Rerank、上下文组装、引用校验
- 策略治理：输入检测、工具前后拦截、风险分级、审批暂停、预算耗尽
- 记忆、Prompt 版本、评测集与回归
- 长任务 Worker：从队列取任务，按状态机推进

常用栈：FastAPI、LangGraph、Pydantic、LlamaIndex 或自研检索、Redis、Postgres / pgvector。LangGraph 适合表达状态；策略拦截、预算和审计要写在图的外面，框架本身不代替治理。

### 2.2 TypeScript：交互入口

TypeScript 把同一套网关契约接到浏览器、Node 服务、命令行和编辑器插件。

- 登录态携带、`trace_id` 透传、SSE 流式渲染
- 会话列表、人工审批确认、任务进度
- MCP 客户端：本地进程用 stdio + JSONL
- 前端不保存模型密钥，不自行拼接系统 Prompt

常用栈：Next.js、React、Vercel AI SDK、MCP SDK、Node.js。

### 2.3 Rust：网关、隔离与热点

Rust 承担要稳定、要限流、要隔离的进程。

- **接入网关**：身份、签名与防重放、多级限流、请求体上限、轻量违禁词、路由、超时、Trace、降级开关
- **模型网关**：厂商协议统一、健康检查、熔断、按错误类型切换、配额、静态兜底
- **沙箱**：高风险工具独立进程执行，超时杀死，参数走 stdin JSON，不经过 shell
- **热点**：向量归一化、解析、加密等用 PyO3 嵌进 Python；在线索引交给 Qdrant 这类 Rust 向量引擎
- **本地产品**：单二进制 + WASM，适合边缘和离线

常用栈：Axum、Tokio、Tonic、SQLx、Qdrant、PyO3。

---

## 3. 全流程知识点归属

下面是对照两份流程文档后，必须落到语言上的能力。原《Agent 开发语言》只写了语言分工和通信方式，这些环节需要单独有归属。

### 3.1 两类网关

接入网关和模型网关是两个 Rust 服务，不要合成一个“什么都做的 API”。

| | 接入网关 | 模型网关 |
|---|---|---|
| 谁调用 | 浏览器、CLI、业务系统 | Python 编排服务 |
| 核心职责 | 鉴权、限流、体长、词库、路由、Trace、维护开关 | 模型池、协议适配、熔断、切换、token 统计 |
| 失败时 | 返回明确错误码，不把堆栈给用户 | 可切换错误走备用模型；内容安全拦截停止 |

接入网关从令牌解析 `tenant_id`、`user_id`、角色，并覆盖请求体里客户端自填的身份字段。

### 3.2 策略治理

策略服务用 Python（规则复杂后可把判定表达式放到 OPA，由 Python / Rust 调用）。它和模型分开部署，规则版本化，能灰度、能回滚。

执行顺序：

1. 接入网关：词库与限流
2. 策略层：注入检测、长度、脱敏、配额
3. 编排器推进状态机
4. 模型给出工具计划后，策略层再查权限、参数、次数、风险等级
5. 工具结果回到策略层：脱敏、体积截断、间接注入标记
6. 最终输出再过内容与隐私检查

高风险写操作进入 `waiting_approval`，等人确认后再执行。

### 3.3 违禁词与攻击面

防护分三层，分别由不同语言完成。

| 层级 | 语言 | 做什么 |
|---|---|---|
| 词库快拦 | Rust 接入网关 | 命中即返回，请求不进编排，省掉模型费用 |
| 语义与注入 | Python 策略层 | 句式、分类模型、上传文件与工具结果 |
| 动作边界 | Python 策略 + Rust 沙箱 | 工具白名单、参数 Schema、出网域名、超时 |

要覆盖的攻击包括：直接注入、间接注入、越狱、工具越权、SSRF、跨租户泄露、超长输入打满 token、供应链投毒。知识库里可疑指令不要直接删掉原文，以免改掉业务含义；打上信任等级和 `suspected_injection`，检索内容只能当资料，不能当系统指令。

内容安全拒绝（厂商 4xx 内容拦截）停在当前结果上，换备用模型会变成绕过安全策略。

### 3.4 切片、向量与替代存储

| 步骤 | 语言 | 要点 |
|---|---|---|
| 清洗、脱敏、信任标记 | Python | 切片前做手机号、证件号、密钥掩码 |
| 切片 | Python | token 计数；512～1024 为通用文本实验起点，overlap 约 10%～20%，以金标集为准 |
| Embedding | Python 调本地模型 | 企业文本默认不出域 |
| L2 归一化等热点 | Rust（PyO3）或向量库内建 | 余弦检索前归一化 |
| 在线索引 | Qdrant（Rust）或 pgvector | HNSW 偏向低延迟，IVF 偏向更大规模，以压测为准 |
| 权限过滤 | 检索请求里带 ACL | 在召回阶段过滤，避免先召回敏感片段再丢弃 |

向量库之外的存法：

| 方案 | 适用 | 谁写 |
|---|---|---|
| BM25 / 全文 | 专有名词，以及向量库故障时的降级 | Python 发查询 |
| Postgres + pgvector | 中等规模、希望少一个中间件 | Python + SQL |
| JSON / 数组在应用里算距离 | 几千条以内的原型 | Python |
| Parquet / 对象存储 | 离线构建与归档 | Python 批处理 |
| 单机 FAISS / 本地文件 | 边缘、单机 | Python 或 Rust，多副本要另做同步 |
| 图数据库 | 实体关系，和向量、全文一起用 | Python 编排查询 |
| 业务库 / API | 价格、库存、订单状态 | 工具层实时查，不写进向量快照 |

生产检索是「全文 + 向量 + 结构化查询 + Rerank」。向量值本身不做普通加密，加密会破坏近邻检索；隔离靠租户、ACL、传输加密和磁盘加密。

### 3.5 RAG 在线链路

Python 串起这条链路：

`问题分类 → 清洗 / 改写 / 拆分 → ACL → 向量 + BM25 + 元数据 → RRF 融合 → Rerank → 时效与冲突检查 → 分隔符组装 → 生成 → 引用校验`

调优时按数据、解析、切片、ACL、召回、Rerank、截断、Prompt、模型的顺序排查。指标至少包括 Recall@K、Precision@K、引用准确率、该拒答时是否拒答、ACL 泄露数（必须为 0）。

### 3.6 状态机、记忆、Prompt

编排器（Python）显式保存状态，而不是只靠模型说“做完了”。

状态：`pending`、`running`、`waiting_approval`、`waiting_tool`、`succeeded`、`failed`、`cancelled`、`handed_off`。

每步检查：最大步骤、最大工具次数、token 预算、截止时间、取消标记。

记忆：

- 短期会话放 Redis，带租户和用户隔离、长度上限、过期时间
- 长期只写授权过的偏好或摘要，整段对话不要无差别向量化
- 记忆内容仍是不可信数据，不能覆盖系统指令
- 审计记录输入版本、证据、计划摘要、工具请求与结果、策略结论；不保存模型私有思维链

Prompt 按模板 ID + 版本加载，带变量 Schema 和输出 Schema。解析失败最多再请求一次。

### 3.7 工具调用链

工具注册中心（Python + Postgres）是唯一工具来源。每条工具声明：JSON Schema、风险等级、权限标签、是否只读、是否幂等、幂等键、超时、单会话次数、返回大小、域名白名单、脱敏、补偿动作、是否要审批。

模式：

- 串行：后一步依赖前一步，每步过后策略再放行
- 并行：仅互不依赖的只读工具，限制扇出
- 分支：用校验后的结果或确定性规则选择
- 有限循环：次数、时间、费用到顶就停
- 补偿：跨系统写入记录每步，失败后补偿或转人工

重试：

- 查询类幂等操作：指数退避加抖动
- 400、权限拒绝、内容拦截：停止
- 写操作超时：先按幂等键查询真实状态，再决定要不要重放
- 接入网关、编排器、工具 SDK 共用一个重试预算，避免三层各试三次

高风险或要跑代码的工具，由 Rust 沙箱进程执行。普通查询工具可以是 Python 函数或 TS MCP Server。

### 3.8 多智能体

默认单 Agent 或确定性工作流。多 Agent 要同时满足明确收益：模型或权限不同、可以并行、单上下文放不下，并且对照实验证明成功率的提升盖得过延迟和成本。

生产用 Python 中心化编排器管理 DAG、租约、扇出上限和聚合。Agent 之间的消息带上来源、任务 ID 和 Schema，再过策略层。去中心化互相对话只留在实验环境。

### 3.9 高并发与模型容灾

容量按峰值估算，而不是日均 QPS：

- 在途请求数 ≈ 到达率 × 平均处理时长
- token 吞吐 ≈ QPS × 每请求平均 token
- Worker 数 ≈ 到达率 × 任务时长 ÷ 单 Worker 并发度
- 队列在堆积时，先限流和降优先级，确认下游还有余量再加 Worker

| 手段 | 落点 |
|---|---|
| 无状态多副本、多级限流、体长、连接上限 | Rust 接入网关 |
| 会话外置、短问答同步、长任务入队、取消传播 | Python + Redis Stream / NATS / Kafka |
| Query、Embedding 缓存；向量故障切 BM25 | Python + Redis |
| 动态批处理、并发舱壁 | 模型服务（vLLM / TensorRT-LLM）+ Rust 模型网关限并发 |
| 每工具独立超时和连接池 | Python 调度，高危执行在 Rust |

模型网关按错误分类：

| 错误 | 动作 |
|---|---|
| 429、超时、5xx | 记一次失败，熔断后切到已验证的备用模型 |
| 400 参数错误 | 修正请求，不切换 |
| 内容安全拦截 | 按策略返回，不切换 |
| 上下文超长 | 压缩或拒绝 |
| 全部不可用 | 返回预设兜底文案 |

备用模型要事先验证过工具 Schema、JSON、上下文长度、语言质量和安全策略。接口能通，只说明网络切换成功。流式响应一旦已经把首 token 写给接入网关，模型网关就结束这次流并返回明确错误；编排器用新的请求重试，不把两家模型的输出拼进同一条 SSE。

### 3.10 安全配套、评测与观测

- 传输 TLS；密钥放在密钥系统，Prompt 和代码里不写密钥
- 容器非 root、只读根文件系统、出网 NetworkPolicy
- 原文删除时，切片、向量、缓存、引用一起删
- 评测在 Python：金标集、回归集、对抗集；发布门槛包含任务成功率、ACL 泄露为 0、P95 和单任务成本
- 每条请求记录 `trace_id`、`session_id`、`task_id`，以及 Prompt、模型、策略、工具、索引版本
- 错误码三端共用第 6.11 节的字符串；缓存键和租约见 6.12；审批继续见 6.13；检索 ACL 见 6.14

### 3.11 和《agent全流程》的章节对应

流程细节以《agent全流程》为准。下表只标实现落点，避免在语言文档里再写一套步骤。

| 全流程 | 谁实现 | 本文骨架 |
|---|---|---|
| 第 0 章 请求路径、同步/异步分叉、超时、错误码、Shadow | Rust 接入网关转发；Python 决定入队 | 6.1、6.6、6.11 |
| 第 2 章 切片、信任标记、替代存储、删除传播 | Python 流水线；L2 归一化可 PyO3；在线索引用 Qdrant 或 pgvector | 6.4、6.7 |
| 第 2.7、2.8、3.6 章 多模态与 GraphRAG | 解析和抽取在 Python；在线索引仍走现有检索栈，不单开无 ACL 旁路 | 6.14 |
| 第 3 章 RAG、RRF、BM25 降级、缓存键、引用、超窗 | Python | 6.7、6.12、6.14 |
| 第 4 章 状态机、预算、取消、审批协议、Worker 租约 | Python；状态在 Redis / Postgres | 6.6、6.12、6.13 |
| 第 5 章 工具链、幂等、补偿 | Python 调度；高风险执行在 Rust 沙箱 | 6.3、6.5、6.10 |
| 第 6、8 章 策略四次拦截、违禁词分层 | 词库在 Rust 网关；语义、工具前后、输出在 Python | 6.1、6.5 |
| 第 7、9 章 双网关、熔断与切换、继续/查询路由 | 两个 Rust 服务 | 6.1、6.2、6.13 |
| 第 10 章 高并发 | 网关限流在 Rust；队列和缓存键在 Python | 6.1、6.6、6.12 |
| 第 11 章 中心化编排、Agent 消息 | Python；消息 Schema 过策略 | 6.9 |
| 第 12、13 章 审计字段、评测门禁 | Python 评测；Trace 字段三端都传 | 4、6.8 |

业务阶段门、ROI、Runbook、退役单、等保/个保法、FinOps 在《agent落地手册》。第 24 章是教学闭环；改生产行为时以本章骨架为准，再对照手册里的替换清单。

---

## 4. 通信与状态

| 场景 | 方式 |
|---|---|
| TS → Rust 接入网关 → Python | HTTP JSON；流式用 SSE |
| Python → Rust 模型网关 | HTTP JSON 或 gRPC |
| 热点函数 | PyO3 |
| 本地 MCP | stdio + JSONL |
| 长任务 | Redis Stream / NATS / Kafka，状态写 Redis / Postgres |
| 取消 | 网关或用户写入取消标记，Worker 每步读取 |

会话、任务状态、工具结果、审计日志都落存储。进程内存只作缓存。

请求体公共字段：

```json
{
  "trace_id": "由网关生成或透传",
  "session_id": "会话",
  "task_id": "长任务才有",
  "execution_mode": "sync 或 async",
  "message": "用户输入",
  "tenant_id": "网关用令牌覆盖",
  "user_id": "网关用令牌覆盖"
}
```

---

## 5. 部署与演进

**原型：** Python + TypeScript 跑通工具、RAG 和页面。此阶段可以不上向量引擎和沙箱，但不要把 Python 端口直接暴露成公网高并发入口。

**预生产：** Rust 接入网关和模型网关先就位。限流、熔断、身份覆盖、词库属于正确性，等到“性能不够”再补会把故障留到上线后。

**生产：** 模型推理、检索、工具执行分进程。Python 水平扩展 Worker；Rust 网关无状态多副本。热点归一化、解析再下沉 PyO3。边缘节点用 Rust 做本地代理，复杂编排仍在 Python。

业务规则、工作流和审批阈值继续留在 Python，方便按周改。索引、Prompt、策略、模型路由和代码一起灰度：Shadow → 内部账号 → 小流量 → 全量。

---

## 6. 代码实战

示例是骨架，依赖用各语言的常规库即可：Rust 侧 `axum`、`tokio`、`reqwest`、`serde`、`uuid`、`pyo3`；Python 侧 `pydantic`；TypeScript 侧浏览器或 Node 18+。身份校验在示例里用环境变量中的开发令牌代替 IdP，换上 JWT/OIDC 验签后即可接入真实登录。

### 6.1 Rust 接入网关

职责：校验令牌、用令牌覆盖客户端自填身份、全局限流和用户限流、限制请求体、词库拦截，再转发到 Python。长任务的入队在 6.6。

```rust
use axum::{
    body::Bytes,
    extract::State,
    http::{header, HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::post,
    Json, Router,
};
use serde_json::{json, Value};
use std::{collections::HashMap, collections::HashSet, sync::Arc, time::{SystemTime, UNIX_EPOCH}};
use tokio::sync::Mutex;

#[derive(Clone)]
struct Identity {
    tenant_id: String,
    user_id: String,
    roles: Vec<String>,
}

struct TokenBucket {
    capacity: f64,
    tokens: f64,
    refill_per_sec: f64,
    last_ms: u64,
}

impl TokenBucket {
    fn new(capacity: f64, refill_per_sec: f64) -> Self {
        Self { capacity, tokens: capacity, refill_per_sec, last_ms: now_ms() }
    }

    fn try_acquire(&mut self) -> bool {
        let now = now_ms();
        let elapsed = now.saturating_sub(self.last_ms) as f64 / 1000.0;
        self.last_ms = now;
        self.tokens = (self.tokens + elapsed * self.refill_per_sec).min(self.capacity);
        if self.tokens >= 1.0 {
            self.tokens -= 1.0;
            true
        } else {
            false
        }
    }
}

/// 单进程演示。多副本要把同一套 key 放到 Redis，否则限流会被实例数放大。
struct Limiter {
    global: TokenBucket,
    users: HashMap<String, TokenBucket>,
    user_capacity: f64,
    user_refill: f64,
}

impl Limiter {
    fn allow(&mut self, user_id: &str) -> bool {
        if !self.global.try_acquire() {
            return false;
        }
        let bucket = self.users.entry(user_id.to_string()).or_insert_with(|| {
            TokenBucket::new(self.user_capacity, self.user_refill)
        });
        bucket.try_acquire()
    }
}

#[derive(Clone)]
struct Gateway {
    lexicon: Arc<HashSet<String>>,
    limiter: Arc<Mutex<Limiter>>,
    max_body: usize,
    upstream: String,
    client: reqwest::Client,
    dev_token: String,
}

struct Admitted {
    trace_id: String,
    payload: Value,
}

enum GateError {
    Unauthorized,
    TooLarge,
    RateLimited,
    BadJson,
    Blocked,
    Upstream,
}

impl IntoResponse for GateError {
    fn into_response(self) -> Response {
        let (status, error) = match self {
            GateError::Unauthorized => (StatusCode::UNAUTHORIZED, "unauthorized"),
            GateError::TooLarge => (StatusCode::PAYLOAD_TOO_LARGE, "body_too_large"),
            GateError::RateLimited => (StatusCode::TOO_MANY_REQUESTS, "rate_limited"),
            GateError::BadJson => (StatusCode::BAD_REQUEST, "invalid_json"),
            GateError::Blocked => (StatusCode::BAD_REQUEST, "blocked_lexicon"),
            GateError::Upstream => (StatusCode::BAD_GATEWAY, "upstream_unavailable"),
        };
        (status, Json(json!({ "error": error }))).into_response()
    }
}

fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}

impl Gateway {
    async fn admit(&self, headers: &HeaderMap, body: &Bytes) -> Result<Admitted, GateError> {
        if body.len() > self.max_body {
            return Err(GateError::TooLarge);
        }
        let token = headers
            .get(header::AUTHORIZATION)
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.strip_prefix("Bearer "))
            .unwrap_or("");
        // 生产替换为 IdP 公钥验签，并读取过期时间、受众和角色。
        if token.is_empty() || token != self.dev_token {
            return Err(GateError::Unauthorized);
        }
        let identity = Identity {
            tenant_id: "dev-tenant".into(),
            user_id: "dev-user".into(),
            roles: vec!["agent.user".into()],
        };
        {
            let mut limiter = self.limiter.lock().await;
            if !limiter.allow(&identity.user_id) {
                return Err(GateError::RateLimited);
            }
        }
        let mut payload: Value = serde_json::from_slice(body).map_err(|_| GateError::BadJson)?;
        let message = payload.get("message").and_then(|v| v.as_str()).unwrap_or("");
        let folded = message.to_lowercase();
        if self.lexicon.iter().any(|word| folded.contains(word)) {
            return Err(GateError::Blocked);
        }
        let trace_id = headers
            .get("x-trace-id")
            .and_then(|v| v.to_str().ok())
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string())
            .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
        if let Some(obj) = payload.as_object_mut() {
            obj.insert("trace_id".into(), json!(trace_id));
            obj.insert("tenant_id".into(), json!(identity.tenant_id));
            obj.insert("user_id".into(), json!(identity.user_id));
            obj.insert("roles".into(), json!(identity.roles));
        }
        Ok(Admitted { trace_id, payload })
    }
}

async fn run_sync(State(gw): State<Gateway>, headers: HeaderMap, body: Bytes) -> Result<Response, GateError> {
    let admitted = gw.admit(&headers, &body).await?;
    let resp = gw.client
        .post(format!("{}/v1/agent/run", gw.upstream))
        .header("x-trace-id", &admitted.trace_id)
        .json(&admitted.payload)
        .send()
        .await
        .map_err(|_| GateError::Upstream)?;
    let status = StatusCode::from_u16(resp.status().as_u16()).unwrap_or(StatusCode::BAD_GATEWAY);
    let bytes = resp.bytes().await.unwrap_or_default();
    Ok((status, bytes.to_vec()).into_response())
}

fn router(gw: Gateway) -> Router {
    Router::new().route("/v1/agent/run", post(run_sync)).with_state(gw)
}
```

词库放在可热加载的集合里，按版本发布。语义级有害内容和注入检测仍交给 Python，避免网关里堆业务规则。

### 6.2 Rust 模型网关：分类、熔断、切换

同一次用户请求里，每个模型最多调用一次。内容拦截和参数错误立刻返回，让上层压缩上下文或修正调用，而不是换一家模型再试。

```rust
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CallClass {
    Ok,
    Switch,
    BadRequest,
    SafetyStop,
    ContextTooLong,
}

pub fn classify(status: u16, error_code: &str) -> CallClass {
    match (status, error_code) {
        (200..=299, _) => CallClass::Ok,
        (_, "content_filter") => CallClass::SafetyStop,
        (_, "context_length_exceeded") => CallClass::ContextTooLong,
        (429, _) | (408, _) | (500..=599, _) => CallClass::Switch,
        (_, "timeout") => CallClass::Switch,
        (400, _) => CallClass::BadRequest,
        _ => CallClass::BadRequest,
    }
}

pub struct Endpoint {
    pub name: String,
    consecutive_failures: AtomicU32,
    open_until_ms: AtomicU64,
    half_open_probe: AtomicBool,
    fail_threshold: u32,
    cool_down_ms: u64,
}

impl Endpoint {
    pub fn new(name: &str) -> Self {
        Self {
            name: name.into(),
            consecutive_failures: AtomicU32::new(0),
            open_until_ms: AtomicU64::new(0),
            half_open_probe: AtomicBool::new(false),
            fail_threshold: 3,
            cool_down_ms: 30_000,
        }
    }

    /// 关闭状态放行；熔断窗口内拒绝；冷却结束后只放一个半开探针。
    pub fn allow(&self, now_ms: u64) -> bool {
        let until = self.open_until_ms.load(Ordering::Acquire);
        if until == 0 {
            return true;
        }
        if now_ms < until {
            return false;
        }
        self.half_open_probe
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_ok()
    }

    pub fn record_success(&self) {
        self.consecutive_failures.store(0, Ordering::Release);
        self.open_until_ms.store(0, Ordering::Release);
        self.half_open_probe.store(false, Ordering::Release);
    }

    pub fn record_failure(&self, now_ms: u64) {
        let fails = self.consecutive_failures.fetch_add(1, Ordering::AcqRel) + 1;
        self.half_open_probe.store(false, Ordering::Release);
        if fails >= self.fail_threshold {
            self.open_until_ms.store(now_ms.saturating_add(self.cool_down_ms), Ordering::Release);
        }
    }
}

pub struct ModelResponse {
    pub model: String,
    pub status: u16,
    pub error_code: String,
    pub body: String,
}

pub async fn call_with_failover<F, Fut>(endpoints: &[Endpoint], mut call: F) -> Result<ModelResponse, &'static str>
where
    F: FnMut(&str) -> Fut,
    Fut: std::future::Future<Output = ModelResponse>,
{
    let mut tried = false;
    for ep in endpoints {
        let now = now_ms();
        if !ep.allow(now) {
            continue;
        }
        tried = true;
        let result = call(&ep.name).await;
        match classify(result.status, &result.error_code) {
            CallClass::Ok => {
                ep.record_success();
                return Ok(result);
            }
            CallClass::Switch => ep.record_failure(now_ms()),
            CallClass::SafetyStop => return Err("content_blocked"),
            CallClass::ContextTooLong => return Err("context_too_long"),
            CallClass::BadRequest => return Err("bad_request"),
        }
    }
    if tried { Err("all_models_unavailable") } else { Err("all_models_open") }
}

fn now_ms() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}
```

`all_models_unavailable` 时，网关返回事先写好的兜底回答。编排器看到 `context_too_long` 后压缩证据再调用一次，这一次算进总预算。

### 6.3 Rust 沙箱：只跑注册表里的程序

模型给出的 `tool_id` 先在策略层映射成注册表中的可执行文件路径。这里没有 shell，超时后子进程会被杀掉。

```rust
use tokio::{io::AsyncWriteExt, process::Command, time::{timeout, Duration}};

pub async fn run_registered_tool(binary: &str, stdin_json: &[u8], limit: Duration) -> Result<Vec<u8>, String> {
    let mut child = Command::new(binary)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .map_err(|err| err.to_string())?;
    if let Some(mut stdin) = child.stdin.take() {
        stdin.write_all(stdin_json).await.map_err(|err| err.to_string())?;
    }
    let output = timeout(limit, child.wait_with_output())
        .await
        .map_err(|_| "tool_timeout".to_string())?
        .map_err(|err| err.to_string())?;
    if output.status.success() {
        Ok(output.stdout)
    } else {
        Err("tool_failed".into())
    }
}
```

配套限制放在运行环境里：非 root、只读文件系统、出网域名白名单、内存上限。需要更强隔离时，把同一个 stdin/stdout 协议放进 WASM 运行时或独立微虚机，编排协议不用改。

### 6.4 Rust 热点：L2 归一化（PyO3）

切片和检索策略仍在 Python。对大批向量做归一化时，把纯计算交给 Rust。

```rust
use pyo3::prelude::*;

#[pyfunction]
fn l2_normalize(mut values: Vec<f32>) -> Vec<f32> {
    let norm = values.iter().map(|x| x * x).sum::<f32>().sqrt();
    if norm > 0.0 {
        for value in &mut values {
            *value /= norm;
        }
    }
    values
}

#[pymodule]
fn agent_kernels(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(l2_normalize, module)?)?;
    Ok(())
}
```

### 6.5 Python 策略层：工具调用前后各拦一次

```python
from enum import Enum
from pydantic import BaseModel, Field


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    NEED_APPROVAL = "need_approval"


class ToolSpec(BaseModel):
    tool_id: str
    risk_level: str  # low | medium | high
    permission_tags: list[str]
    is_idempotent: bool
    is_read_only: bool
    timeout_ms: int
    max_calls_per_session: int
    allowed_params: list[str]
    denied_keywords: list[str]
    max_result_chars: int = 8000


class ToolCall(BaseModel):
    tool_id: str
    arguments: dict = Field(default_factory=dict)
    idempotency_key: str


class PolicyVerdict(BaseModel):
    decision: Decision
    reason: str


def pre_check(spec: ToolSpec, call: ToolCall, user_permissions: set[str], used_times: int) -> PolicyVerdict:
    if not set(spec.permission_tags).issubset(user_permissions):
        return PolicyVerdict(decision=Decision.DENY, reason="missing_permission")
    if used_times >= spec.max_calls_per_session:
        return PolicyVerdict(decision=Decision.DENY, reason="call_budget_exceeded")
    unknown = set(call.arguments) - set(spec.allowed_params)
    if unknown:
        return PolicyVerdict(decision=Decision.DENY, reason="param_not_allowed")
    blob = " ".join(str(v) for v in call.arguments.values()).lower()
    if any(word in blob for word in spec.denied_keywords):
        return PolicyVerdict(decision=Decision.DENY, reason="param_denied_keyword")
    if spec.risk_level == "high":
        return PolicyVerdict(decision=Decision.NEED_APPROVAL, reason="high_risk")
    return PolicyVerdict(decision=Decision.ALLOW, reason="ok")


def post_check(spec: ToolSpec, raw: str) -> str:
    """工具结果视为不可信数据：截断，并标出可疑指令，保留原文供审计。"""
    text = raw[: spec.max_result_chars]
    suspected = any(phrase in text.lower() for phrase in ("ignore previous", "忽略以上指令", "system prompt"))
    flag = " suspected_injection=true" if suspected else ""
    return f"--- BEGIN UNTRUSTED TOOL {spec.tool_id}{flag} ---\n{text}\n--- END UNTRUSTED TOOL {spec.tool_id} ---"
```

注入短语列表只能挡住明显样本。上线时要用分类模型和对抗集，规则版本和词库一起发布。

写操作如果超时，编排器用 `idempotency_key` 查询业务状态；确认没有成功，才允许重放。

### 6.6 Python 状态机：模型提议，程序推进

```python
import json
from enum import Enum
from pydantic import BaseModel, Field, ValidationError


class TaskState(str, Enum):
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    HANDED_OFF = "handed_off"


class Budget(BaseModel):
    max_steps: int = 8
    max_tool_calls: int = 6
    max_tokens: int = 12000


class Action(BaseModel):
    type: str  # tool | final | handoff
    tool_id: str | None = None
    arguments: dict = Field(default_factory=dict)
    answer: str | None = None


class RunContext(BaseModel):
    state: TaskState = TaskState.RUNNING
    steps: int = 0
    tool_calls: int = 0
    tokens: int = 0
    parse_retries: int = 0


def parse_action(raw: str) -> Action:
    return Action.model_validate(json.loads(raw))


def advance(ctx: RunContext, budget: Budget, raw_model_output: str) -> tuple[RunContext, Action | None]:
    ctx.steps += 1
    if ctx.steps > budget.max_steps or ctx.tokens > budget.max_tokens:
        ctx.state = TaskState.HANDED_OFF
        return ctx, None
    try:
        action = parse_action(raw_model_output)
    except (json.JSONDecodeError, ValidationError):
        ctx.parse_retries += 1
        if ctx.parse_retries > 1:
            ctx.state = TaskState.FAILED
        return ctx, None
    if action.type == "final":
        ctx.state = TaskState.SUCCEEDED
        return ctx, action
    if action.type == "tool":
        ctx.tool_calls += 1
        if ctx.tool_calls > budget.max_tool_calls:
            ctx.state = TaskState.HANDED_OFF
            return ctx, None
        return ctx, action
    ctx.state = TaskState.HANDED_OFF
    return ctx, action
```

`action.type == "tool"` 之后调用上一节的 `pre_check`。结果是 `NEED_APPROVAL` 时把状态改成 `waiting_approval` 并持久化，进程重启后从该状态恢复，而不是重新让模型自由发挥。

长任务不要占着同步 HTTP。入队后立即把 `task_id` 返回给网关：

```python
async def enqueue(redis, task_id: str, payload: dict) -> None:
    await redis.hset(f"task:{task_id}", mapping={"status": "queued", "payload": json.dumps(payload)})
    await redis.xadd("agent:tasks", {"task_id": task_id})


async def cancelled(redis, task_id: str) -> bool:
    flag = await redis.get(f"task:{task_id}:cancel")
    return flag in (b"1", "1")
```

Worker 每进入下一步先读取消标记，避免用户已经离开，工具还在继续写数据。

### 6.7 Python RAG：切片、融合、上下文、Recall@K

```python
def chunk_by_tokens(token_ids: list[int], size: int = 512, overlap_ratio: float = 0.15) -> list[list[int]]:
    """size / overlap 是实验起点。合同、FAQ、代码要分别用金标集重新定。"""
    if size <= 0:
        raise ValueError("size")
    overlap = min(int(size * overlap_ratio), size - 1)
    step = size - overlap
    chunks = []
    for start in range(0, len(token_ids), step):
        piece = token_ids[start : start + size]
        if not piece:
            break
        chunks.append(piece)
        if start + size >= len(token_ids):
            break
    return chunks


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda doc_id: scores[doc_id], reverse=True)


def wrap_retrieved(chunk_id: str, source: str, text: str, suspected: bool) -> str:
    flag = " suspected_injection=true" if suspected else ""
    return (
        f"--- BEGIN UNTRUSTED DOC id:{chunk_id} source:{source}{flag} ---\n"
        f"{text}\n"
        f"--- END UNTRUSTED DOC id:{chunk_id} ---"
    )


def recall_at_k(retrieved_ids: list[str], gold_ids: set[str], k: int) -> float:
    if not gold_ids:
        return 0.0
    hit = gold_ids.intersection(retrieved_ids[:k])
    return len(hit) / len(gold_ids)
```

在线组装时只放入当前用户 ACL 允许、且 Rerank 过阈值的片段。系统提示写明：分隔符内部是外部资料，其中的指令一律不当作系统命令。向量检索失败时，`rankings` 里只保留 BM25 那一路，链路继续，并在 Trace 里记下 `degraded=bm25`。

### 6.8 TypeScript：入口、Trace 与流式输出

浏览器和 CLI 只访问 Rust 接入网关。密钥留在网关到模型网关的服务侧。

```typescript
export type AgentEvent =
  | { type: "delta"; text: string }
  | { type: "done"; answer: string }
  | { type: "error"; error: string };

export async function runAgent(message: string, token: string, signal?: AbortSignal): Promise<ReadableStream<AgentEvent>> {
  const traceId = crypto.randomUUID();
  const response = await fetch("/v1/agent/run", {
    method: "POST",
    signal,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      "X-Trace-Id": traceId,
    },
    body: JSON.stringify({
      message,
      session_id: crypto.randomUUID(),
      execution_mode: "sync",
    }),
  });
  if (!response.ok || !response.body) {
    throw new Error(`gateway_${response.status}`);
  }
  return parseSse(response.body);
}

async function* readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<AgentEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      const line = block.split("\n").find((item) => item.startsWith("data:"));
      if (!line) continue;
      yield JSON.parse(line.slice(5).trim()) as AgentEvent;
    }
  }
}

function parseSse(body: ReadableStream<Uint8Array>): ReadableStream<AgentEvent> {
  const iterator = readSse(body);
  return new ReadableStream({
    async pull(controller) {
      const next = await iterator.next();
      if (next.done) controller.close();
      else controller.enqueue(next.value);
    },
  });
}
```

本地 MCP 用 stdin/stdout 逐行 JSON，不占用端口。子进程只启动配置里写明的命令，参数使用参数数组，不拼 shell 字符串。

```typescript
import { spawn } from "node:child_process";

export function openMcp(command: string, args: string[]) {
  const child = spawn(command, args, { stdio: ["pipe", "pipe", "pipe"] });
  function send(message: unknown) {
    child.stdin.write(`${JSON.stringify(message)}\n`);
  }
  return { send, stdout: child.stdout };
}
```

审批按钮调用的是网关的“继续任务”接口，并带上原 `task_id`。前端不直接执行工具。

### 6.9 Python 编排器：Agent 之间的消息先过策略

子 Agent 的输出带上来源和任务 ID。`Decision`、`PolicyVerdict`、`BaseModel` 与第 6.5 节相同。策略不通过时，编排器不把这段文字交给下一个 Agent。

```python
class AgentMessage(BaseModel):
    source_agent: str
    task_id: str
    schema_version: str
    body: str
    confidence: float = 0.0


def accept_agent_message(message: AgentMessage, parent_task_id: str, fanout_used: int, fanout_limit: int) -> PolicyVerdict:
    if message.task_id != parent_task_id:
        return PolicyVerdict(decision=Decision.DENY, reason="task_mismatch")
    if fanout_used >= fanout_limit:
        return PolicyVerdict(decision=Decision.DENY, reason="fanout_exceeded")
    if not message.source_agent or not message.schema_version:
        return PolicyVerdict(decision=Decision.DENY, reason="missing_envelope")
    suspected = any(phrase in message.body.lower() for phrase in ("ignore previous", "忽略以上指令", "system prompt"))
    if suspected:
        return PolicyVerdict(decision=Decision.DENY, reason="untrusted_agent_injection")
    return PolicyVerdict(decision=Decision.ALLOW, reason="ok")
```

放行后仍然用分隔符包住 `body`，下一个 Agent 的系统提示把它当资料。扇出上限和预算记在父任务上，不让规划 Agent 自己声明“还可以再派十个”。

### 6.10 Python：写操作超时先查幂等键

```python
class WriteReceipt(BaseModel):
    state: str  # unknown | succeeded | failed
    business_id: str | None = None


async def replay_write(store, spec: ToolSpec, call: ToolCall, invoke) -> WriteReceipt:
    """超时后先问业务系统。确认没有成功记录，才允许用同一幂等键再执行一次。"""
    existing = await store.find(call.idempotency_key)
    if existing is not None:
        return existing
    if not spec.is_idempotent:
        return WriteReceipt(state="unknown")
    try:
        return await invoke(spec, call)
    except TimeoutError:
        again = await store.find(call.idempotency_key)
        if again is not None:
            return again
        return WriteReceipt(state="unknown")
```

`unknown` 进入 `handed_off`，由人按业务单核对。无补偿动作的高风险工具不要走 `invoke` 的自动重放。

### 6.11 统一错误码：Rust 返回，TS 展示，Python 复用

三端使用同一组字符串，不要各自发明。含义见《agent全流程》第 0.2 节。

```rust
#[derive(Clone, Copy)]
pub enum ErrorCode {
    Unauthorized,
    RateLimited,
    BlockedLexicon,
    PolicyDenied,
    WaitingApproval,
    ContentBlocked,
    ContextTooLong,
    ModelUnavailable,
    DegradedBm25,
    TaskNotFound,
}

impl ErrorCode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Unauthorized => "unauthorized",
            Self::RateLimited => "rate_limited",
            Self::BlockedLexicon => "blocked_lexicon",
            Self::PolicyDenied => "policy_denied",
            Self::WaitingApproval => "waiting_approval",
            Self::ContentBlocked => "content_blocked",
            Self::ContextTooLong => "context_too_long",
            Self::ModelUnavailable => "model_unavailable",
            Self::DegradedBm25 => "degraded_bm25",
            Self::TaskNotFound => "task_not_found",
        }
    }
}
```

```typescript
export const ERROR_CODES = [
  "unauthorized",
  "rate_limited",
  "blocked_lexicon",
  "policy_denied",
  "waiting_approval",
  "content_blocked",
  "context_too_long",
  "model_unavailable",
  "degraded_bm25",
  "task_not_found",
] as const;

export type ErrorCode = (typeof ERROR_CODES)[number];

export function showError(code: ErrorCode): string {
  if (code === "waiting_approval") return "需要审批后才能继续";
  if (code === "content_blocked") return "内容未通过安全检查";
  return code;
}
```

Python 策略层的 `reason` 写入同一组码，经接入网关原样返回。前端只展示码和说明。

### 6.12 Python：缓存键、超时预算、Worker 租约

```python
from datetime import datetime, timedelta, timezone


def cache_key(kind: str, tenant_id: str, acl_ver: str, index_ver: str, embed_ver: str, query_hash: str) -> str:
    """kind 为 ret / emb / gen。缺版本字段会在权限或索引切换后串租户。"""
    return f"{kind}:{tenant_id}:{acl_ver}:{index_ver}:{embed_ver}:{query_hash}"


class TimeoutBudget:
    def __init__(self, gateway_ms: int, orchestrator_ms: int, model_ms: int, tool_ms: int):
        if not gateway_ms > orchestrator_ms > model_ms > 0:
            raise ValueError("nested_timeout")
        if tool_ms >= orchestrator_ms:
            raise ValueError("tool_timeout")
        self.deadline = datetime.now(timezone.utc) + timedelta(milliseconds=orchestrator_ms)
        self.model_ms = model_ms
        self.tool_ms = tool_ms

    def remaining_ms(self) -> int:
        left = (self.deadline - datetime.now(timezone.utc)).total_seconds() * 1000
        return max(0, int(left))

    def allow_model(self) -> bool:
        return self.remaining_ms() > self.model_ms

    def allow_tool(self) -> bool:
        return self.remaining_ms() > self.tool_ms


async def renew_lease(redis, task_id: str, worker_id: str, ttl_sec: int = 30) -> bool:
    key = f"task:{task_id}:lease"
    owner = await redis.get(key)
    if owner not in (None, worker_id, worker_id.encode()):
        return False
    await redis.set(key, worker_id, ex=ttl_sec)
    return True
```

每步先 `cancelled`（6.6）再 `renew_lease`。租约抢不到说明任务已被回收，当前 Worker 退出。毒消息写入 `agent:tasks:dead`，不要 `xadd` 回主队列。

### 6.13 审批继续与任务查询

Rust 接入网关增加两条路由，鉴权和身份覆盖与 6.1 相同。`task_id` 必须属于令牌里的租户，否则 `task_not_found`。

```rust
fn router(gw: Gateway) -> Router {
    Router::new()
        .route("/v1/agent/run", post(run_sync))
        .route("/v1/agent/tasks/{task_id}/continue", post(continue_task))
        .route("/v1/agent/tasks/{task_id}", axum::routing::get(get_task))
        .with_state(gw)
}
```

`continue_task` 把网关覆盖后的身份和路径里的 `task_id` 转给 Python。Python 只恢复 `waiting_approval` 且参数哈希未变的任务，再执行已批准的那一次工具调用。

```typescript
export async function continueTask(taskId: string, token: string): Promise<void> {
  const response = await fetch(`/v1/agent/tasks/${taskId}/continue`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ task_id: taskId }),
  });
  if (!response.ok) throw new Error(`gateway_${response.status}`);
}

export async function getTask(taskId: string, token: string): Promise<{ status: string; error?: string }> {
  const response = await fetch(`/v1/agent/tasks/${taskId}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw new Error(`gateway_${response.status}`);
  return response.json();
}
```

前端不带工具参数，不执行工具。审批页只提交 `task_id`。

### 6.14 Python：检索 ACL 与可选分路

权限过滤写进查询，不要先召回再丢弃。多模态和图谱是可选一路，解析和抽取仍在 Python，在线索引不另开无 ACL 旁路。

```python
def acl_sql(tenant_id: str, roles: list[str]) -> str:
    return """
    SELECT chunk_id, content, source, version
    FROM chunks
    WHERE tenant_id = $1
      AND acl_roles && $2::text[]
      AND (effective_from IS NULL OR effective_from <= now())
      AND (effective_to IS NULL OR effective_to > now())
    """


def qdrant_filter(tenant_id: str, roles: list[str], modality: str | None) -> dict:
    must = [
        {"key": "tenant_id", "match": {"value": tenant_id}},
        {"key": "acl_roles", "match": {"any": roles}},
    ]
    if modality:
        must.append({"key": "modality", "match": {"value": modality}})
    return {"must": must}


def citation_ok(claim_ids: list[str], allowed_ids: set[str]) -> bool:
    return bool(claim_ids) and set(claim_ids).issubset(allowed_ids)
```

`citation_ok` 为假则撤回该句或整段拒答。图谱查询同样带 `tenant_id` 和 `acl_roles`；路径上有不可见节点就丢整条路径，回退到 `qdrant_filter(..., modality=None)` 的混合检索。

---

## 7. 落地时的判断顺序

1. 这个逻辑下周还会改吗？会，就放 Python。
2. 它是否面对公网流量、需要限流熔断，或要隔离不可信程序？是，就放 Rust。
3. 它是否只为了把结果展示给人、或接进编辑器？是，就放 TypeScript。
4. 模型是否在做最终允许或拒绝？如果是，把判定挪到策略服务。
5. 同一条 Trace 能否说清代码、Prompt、模型、策略、工具和索引版本？说不清就先补版本，再扩功能。
