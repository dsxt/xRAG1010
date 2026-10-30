# 追觅科技 AIAgent全栈 三面+HR面 面试题+参考答案
> 岗位特点：**偏工程落地，重线上生产、多语言（Python+Java）、中间件、LangGraph/Agent Harness，算法手写+架构权衡，不是纯大模型算法岗，重点是把Agent塞进业务系统**

## 一面（基础+项目+中间件+RAG+简单算法）
1. **自我介绍**
> 要点：控制2分钟，突出Agent工程落地，讲清楚：技术栈（Python/Java/FastAPI/SpringBoot）、项目角色、解决的线上问题、指标收益。不要只讲调用LangChain。
> 示例框架：我是XX，后端开发，主攻AI Agent工程落地；做过XX业务Agent，负责链路编排、RAG检索优化、工具调用容错，用Python+FastAPI搭建Agent服务，对接Java业务系统，解决过召回不准、工具超时、会话存储等线上问题，熟悉LangChain/LangGraph、Milvus、Redis、MQ，兼顾Python异步和Java服务集成。

2. **最近做的Agent项目介绍，你负责哪块，整体链路怎么跑的**
链路标准流程：
用户query → 预处理/意图识别 → 路由到Agent → 检索RAG获取上下文 → LLM思考是否调用工具Tool Calling → 调用外部业务接口 → 拿到结果再交给大模型汇总 → 返回用户，保存会话日志。
回答重点：明确你的职责（例如：我负责Agent状态管理、工具调用层、RAG检索模块、异常重试；不是只写prompt），讲踩坑：工具脏数据、token膨胀、召回低、死循环，以及对应的优化方案。

3. **Python 的 GIL 是什么，异步编程里 asyncio 的事件循环怎么理解，FastAPI 为什么适合写 Agent 服务**
- GIL：全局解释器锁，CPython独有，同一时刻只有一个线程执行Python字节码；CPU密集型多线程无法并行；IO密集型不受GIL限制。
- asyncio事件循环：单线程调度协程，非阻塞IO；遇到IO等待（调用大模型、http请求、数据库）时让出控制权，调度其他就绪协程，实现IO并发。
- FastAPI适合Agent原因：原生async支持，Agent大量IO（LLM调用、工具API、向量库查询）；自动Pydantic参数校验；高性能；自动openapi接口文档；方便构建流式SSE输出对话。

4. **Java侧怎么把一个现有Spring Boot服务对接大模型Agent，用哪种调用方式，同步还是异步**
两种方案：
1）**同步调用**：SpringBoot发起HTTP/gRPC请求调用Agent服务，阻塞等待返回；适合短任务，缺点：长任务容易超时，占用容器线程。
2）**异步（推荐生产）**：SpringBoot发请求，拿到taskId；Agent后端把任务丢入MQ(Kafka/RocketMQ)异步执行；SpringBoot监听回调topic或者业务轮询task状态。
> 选型：短查询简单任务可以同步；**Agent长任务、多轮思考、多工具调用，必须异步**，防止服务线程耗尽雪崩。通信协议优先gRPC，其次HTTP。

5. **Redis 在 Agent 系统里能干什么，举例说明用 Redis 存会话上下文和限流怎么设计**
Redis用途：会话存储、分布式限流、任务状态缓存、锁、向量检索临时缓存、TTL过期。
- 会话上下文：key=`session:{session_id}`，value=对话消息列表（JSON），设置TTL，用户久不对话自动过期；适合轻量会话；长历史大会话不要全塞Redis，只存摘要，完整日志落MySQL。
- 限流设计：
  1. 简单计数器：key=`rate:{user_id}`，incr+expire，固定窗口，有临界毛刺；
  2. 生产推荐**滑动窗口限流**；或者令牌桶，控制每个用户每分钟Agent请求次数，防止LLM调用打爆。

6. **MySQL 里有一张对话日志表，量大了查询慢，你会怎么建索引，哪些字段不适合建索引**
对话日志表：一般字段id、session_id、user_id、create_time、query、answer。
✅ 建索引：
- 联合索引：`(user_id, create_time)`，按用户查历史对话；`session_id`单独索引，按会话查询。
❌ 不适合建索引：大文本字段query/answer，B+树索引存储开销极大，几乎没有收益；低基数字段（比如状态只有0/1）。
优化补充：分表、冷热分离，旧对话归档，只保留最近N个月在线数据。

7. **意图识别你做过么，是完全丢给大模型做分类，还是规则加模型混合，各自利弊**
- 纯大模型：开发快，泛化强；缺点：成本高、延迟高、结果不稳定，容易幻觉，高并发不划算。
- 规则+模型混合（工程首选）：简单意图（查询订单、退款）先用正则/关键词规则快速命中；无法匹配的复杂query，再交给LLM做意图分类。
优点：降本、低延迟；缺点：规则维护成本，边界case需要持续迭代。

8. **LangChain 里 Chain 和 Agent 的区别，为什么复杂场景不能直接用简单 Chain**
- Chain：固定预设执行链路，步骤写死；输入→固定步骤→输出，不会自主决策要不要调用工具。适合固定流程任务（文档摘要）。
- Agent：带决策循环，LLM自主判断下一步动作：是否调用哪个工具、什么时候停止；动态分支。
复杂场景不能只用Chain：业务问题不确定，工具调用次数、顺序无法预先写死；固定Chain无法应对动态用户输入。

9. **RAG 基础流程讲一下，文档切片大小怎么定，切片大了小了分别有什么问题**
RAG流程：文档加载 → 文本清洗 → 切片(chunk) → embedding编码 → 存入向量库；用户query → embedding → 向量检索TopK → rerank重排 → 把检索结果拼入prompt交给LLM生成答案。
- chunk太大：单片段信息冗余，包含无关内容；token开销高，容易超过prompt上限；向量表达语义混杂，检索精度下降。
- chunk太小：语义断裂，上下文不完整，信息碎片化；检索召回的片段缺少完整逻辑，LLM回答缺信息。
> 选型：通用文档 300~800token；根据文档类型调整，代码/结构化文档切更小；同时加重叠（overlap）保留上下文连续性。

10. **向量库你用过哪个，Milvus 和 Chroma 选型上怎么考虑**
- Chroma：轻量，嵌入式，单机本地开发原型；不支持分布式，不适合大数据量线上生产。
- Milvus：分布式、支持海量向量、多索引类型、可水平扩容、支持分片与冷热分离；适合生产环境大规模RAG。
> 一句话：本地POC用Chroma；线上生产业务，数据量大、高并发选Milvus。

11. **Tool Calling 里怎么约束大模型传参，Pydantic 模型校验够不够，还要做什么兜底**
Pydantic只能做基础类型校验，**不够**。
分层约束：
1. Prompt层：明确函数schema描述，约束参数范围；
2. LLM输出结构化输出，Pydantic做类型、必填字段校验；
3. 业务层兜底：参数范围校验、白名单枚举校验、权限校验；
4. 失败处理：参数非法时，返回错误信息，让Agent反思重试，限制重试次数，防止死循环。

12. **Agent 调外部工具超时或返回脏数据，怎么重试，重试怎么避免重复下单这种副作用**
- 重试策略：指数退避重试，设置最大重试次数；区分**幂等接口**和非幂等接口。
- 防重复（重点）：给每一次工具调用生成全局唯一requestId；业务系统根据requestId做幂等。
  读类接口（查询物流）可以重试；写类（下单、退款）必须幂等，不可盲目重试；脏数据：增加返回值schema校验，脏数据直接拦截，进入反思节点，而不是直接传给用户。

13. **手撕算法：手写一个带过期时间的 LRUCache（Python或Java均可，要求get/put O(1)，支持 TTL）**
> 核心：双向链表保存LRU顺序 + Hash Map O(1)查找；每个key额外存储expire时间戳；每次get/put先清理过期数据。
```python
import time
from collections import OrderedDict

class LRUCacheWithTTL:
    def __init__(self, capacity):
        self.cap = capacity
        self.cache = OrderedDict() # 有序字典模拟双向链表
        self.ttl_map = dict() # key: expire_ts

    def _clean_expire(self):
        now = time.time()
        expired = [k for k, exp in self.ttl_map.items() if exp < now]
        for k in expired:
            del self.cache[k]
            del self.ttl_map[k]

    def get(self, key):
        self._clean_expire()
        if key not in self.cache:
            return -1
        self.cache.move_to_end(key) # 移到尾部，表示最近使用
        return self.cache[key]

    def put(self, key, value, ttl_seconds):
        self._clean_expire()
        expire_ts = time.time() + ttl_seconds
        if key in self.cache:
            self.cache[key] = value
            self.ttl_map[key] = expire_ts
            self.cache.move_to_end(key)
        else:
            if len(self.cache) >= self.cap:
                old_key, _ = self.cache.popitem(last=False) # LRU淘汰头部
                del self.ttl_map[old_key]
            self.cache[key] = value
            self.ttl_map[key] = expire_ts
```
复杂度：get/put O(1)，清理过期是懒删除。

---

## 二面（LangGraph、多Agent、任务断点、RAG调优、MQ、架构）
1. **自我介绍**
2. **讲一个你最熟的 Agent 项目，从用户提问到最终动作执行，中间所有模块列一遍**
用户Query → 输入清洗、脱敏 → 意图路由模块 → Agent状态机（LangGraph）
→ 检索模块（RAG，向量库+rerank） → LLM Planner决策是否调用工具
→ ToolCalling模块（参数校验、幂等包装）→ 调用外部业务API（订单/物流）
→ 工具返回结果写入状态 → LLM反思判断是否继续循环 / 终止
→ 结果整理、输出给用户；日志、状态持久化；监控埋点。

3. **LangGraph 的 State、Node、Edge 分别解决什么问题，Checkpointer 在生产环境怎么用**
- State：状态，整个Agent链路共享的上下文，保存对话历史、工具返回、中间结果；在节点之间传递数据。
- Node：状态机里的执行单元，一个动作（调用LLM、调用工具、RAG检索），接收state，返回更新后的state。
- Edge：节点之间的跳转逻辑；分普通Edge（固定跳转）和Conditional Edge（条件分支，LLM决定下一步去哪，比如继续调用工具/结束）。
- Checkpointer：状态持久化。保存每一步的state快照，绑定thread_id；长任务崩溃后，可以通过thread_id读取checkpoint，从上次中断节点继续执行，实现断点恢复。生产一般用Redis/PostgreSQL作为Checkpoint存储。

4. **多Agent架构你踩过什么坑，Supervisor-Worker 和 Peer-to-Peer 通信怎么选**
- Supervisor-Worker：主Agent（主管）负责任务拆分、分配、汇总结果；子Worker执行子任务。适合复杂、可拆解的业务任务，便于管控，限制子Agent权限；缺点：主管成为单点瓶颈。
- Peer-to-Peer对等：Agent之间互相自由通信协商，没有中心；适合开放式协作；缺点：难以控制，容易无限对话、不可控、不可审计，生产很少直接上。
> 工程优先 Supervisor-Worker；踩坑：子Agent输出不可控、任务重复执行、token消耗爆炸、状态同步混乱。

5. **Agent 长任务跑到一半崩了，怎么从断点恢复，thread_id 和 checkpoint 怎么配合**
thread_id：唯一标识一条Agent会话任务；每次状态变更，Checkpointer以`thread_id`为key保存state快照。
流程：任务中断后，前端/后端携带同一个thread_id重新拉起LangGraph，框架读取该thread_id最近的checkpoint状态，从上次中断的Node继续执行，不需要从头跑整个任务。
注意：业务工具调用必须做幂等，断点恢复不能重复执行写操作。

6. **意图识别模块线上准确率掉了怎么排查，是样本漂移、提示词问题还是模型版本问题**
排查顺序：
1. 先对比线上bad case，看分布：新用户query和训练样本差异大 → **样本漂移**；
2. 相同query，换旧prompt测试，结果不一致 → prompt问题；
3. 相同query+相同prompt，新旧模型返回不一样 → **模型版本问题**。
观测指标：分布偏移监控、准确率、拒识率，做A/B验证。

7. **RAG 检索召回不准，你从分块、embedding、混合检索、rerank哪一层先动手，为什么**
优先 **rerank**，改动成本最低，见效最快；不需要重新全量向量化入库。
顺序：rerank → embedding模型 → 混合检索（关键词+向量） → 文档分块策略。
理由：修改分块/embedding需要全库重新向量化，成本极高；rerank只对检索出来的top候选重排序，低成本提升召回精准度。

8. **Agent 出现死循环一直调工具，工程上怎么掐断，最大步数、token 预算、反思节点怎么配合**
三层防护：
1. **最大迭代步数**：状态机设置最大循环次数，超过强制终止，兜底；
2. **Token预算**：累计对话token超过阈值直接截断，防止无限膨胀；
3. **反思节点**：每次工具调用后，反思节点判断是否陷入重复动作，检测重复tool_call，主动终止。
终止后返回人工接管提示，不继续执行。

9. **Kafka 在 Agent 系统里用来干什么，任务队列和事件总线的用法区别**
- 任务队列：异步执行Agent长任务；生产者下发任务，Agent消费执行；一条消息只被一个consumer消费。
- 事件总线：Agent内部事件广播（任务开始、工具调用完成、任务失败）；一条事件可以被多个业务模块订阅（监控、日志、告警）。
Kafka高吞吐，适合Agent大量异步任务、事件埋点。

10. **RocketMQ 和 Kafka 在 Agent 工具异步执行场景里怎么选**
- Kafka：高吞吐，海量日志、事件总线；消息延迟略高；适合大量Agent事件流式采集。
- RocketMQ：支持事务消息、死信队列、消息重试，可靠性更强；适合业务写操作（退款、订单）这类强一致性Agent任务，防止消息丢失，业务金融属性优先选RocketMQ。

11. **AI 编程你平时用哪些工具，Cursor、Copilot、Claude Code 怎么介入你日常开发，会不会带来代码质量风险**
日常：Cursor写Agent框架代码，Copilot补全单元测试，Claude Code做复杂架构梳理、重构。
风险：生成代码存在隐藏bug、安全漏洞、幻觉；解决方案：代码必须人工Review，增加单测、静态扫描，不能直接上线。

12. **Harness 工程你理解是什么，Agent=Model+Harness 这句话你怎么展开讲**
Harness = Agent运行时工程框架，不是大模型本身。
> Agent = Model（LLM思考、推理） + Harness（承载执行的工程骨架）
Harness包含：状态管理、工具调用调度、参数校验、重试熔断、checkpoint断点、监控指标、评估链路、权限安全、日志埋点。
大模型只负责思考决策；**Harness负责控制、执行、兜底、可观测性**；很多Agent线上问题不在模型，而在Harness。

13. **场景：追觅的智能客服Agent要接订单、物流、售后三个系统，让用户一句话查退款进度并触发催办，画出架构并说清每步谁决策**
```
用户query → API网关
↓
输入预处理&意图识别（小模型/规则）
↓
Agent状态机（LangGraph）
├─Node1：LLM Planner决策：需要调用【售后退款查询工具】，拿到退款状态
├─Node2：判断状态，如果待处理 → 调用【催办工具】
└─Node3：汇总结果
↓
工具层（统一网关）对接订单/物流/售后3个Java业务系统，幂等包装
↓
结果返回用户，日志写入数据库，指标上报监控
```
决策主体：**LLM负责判断“是否需要催办”；工具调用、鉴权、幂等由Harness工程层控制**，高危操作（催办）增加人工确认节点。

14. **手撕算法：合并 K 个有序链表（优先队列实现，顺带说复杂度）**
```python
import heapq
class ListNode:
    def __init__(self, val=0, next=None):
        self.val = val
        self.next = next

def mergeKLists(lists):
    heap = []
    for idx, node in enumerate(lists):
        if node:
            heapq.heappush(heap, (node.val, idx, node))
    dummy = ListNode()
    cur = dummy
    while heap:
        val, idx, node = heapq.heappop(heap)
        cur.next = node
        cur = cur.next
        if node.next:
            heapq.heappush(heap, (node.next.val, idx, node.next))
    return dummy.next
```
复杂度：N是全部节点总数；O(N log K)；K链表个数。

---

## 三面（架构权衡、项目管理、指标评估、MCP、Agent安全、模型路由、DAG拓扑排序）
1. **自我介绍**
2. **你带过 Agent 项目么，几个人，怎么拆任务，模型调用层和业务工具层谁写谁审**
分工示例：3人小组；
- 模型层（prompt、RAG、LLM调用、状态机）：后端/AI工程师开发；
- 业务工具层（对接订单/物流接口、参数校验、幂等）：后端开发；
评审：**跨层互审**，业务层同学审工具接口；Agent框架同学审模型调用逻辑；所有上线都要过eval评估集回归测试。

3. **当算法同学认为应该全自治 Agent，你作为工程侧坚持加入人工确认节点，意见不合怎么推**
先对齐目标：不是否定自治，而是控制线上风险。
1. 先达成共识：区分风险等级；查询类只读操作可以自治；写操作（退款、改单）属于高风险，必须人工确认；
2. 用数据说话：用POC评估集看自治场景bad case，量化风险；
3. 折中方案：灰度上线，低风险全自治；高风险分支走人工确认；后续随着eval指标提升，逐步放开人工卡点。

4. **Agent上线后老板问效果怎么量化，你建哪些指标：任务成功率、工具准确率、人工接管率、成本 per session**
- 任务成功率：用户目标是否完成（核心业务指标）
- 工具调用准确率：选对工具+传参正确的比例
- 人工接管率：Agent无法处理，转人工客服的比例
- Cost per session：单次会话LLM token成本
附加指标：平均会话轮次、响应时延、失败原因分布、用户满意度。

5. **Eval Harness 你怎么搭，离线用例集怎么来，bad case 怎么回流成回归集**
Eval Harness：自动化评估框架，批量跑测试用例，自动计算指标。
用例来源：
1. 人工编写标准case；
2. 线上真实query采样；
3. 线上bad case自动采集。
回流流程：线上bad case人工标注，加入回归测试集；每次框架/模型版本变更，自动跑eval harness，指标下降阻断上线。

6. **多Agent协作里某个子Agent一直产出低质量结果，是换prompt、换模型，还是拆角**
排查顺序：先看eval指标
1. 优先优化prompt，低成本验证；
2. prompt优化无效，换更强模型；
3. 如果是职责边界混乱导致，就拆分角色，重定义子Agent的职责边界。

7. **MCP协议和Function Calling 的关系，为什么2026年大家开始聊MCP，它解决了什么痛点**
Function Calling：LLM和**单个工具**调用的能力，是LLM输出结构化函数调用；但工具定义、鉴权、传输没有统一标准，每个工具要单独写schema适配。
MCP（Model Context Protocol）：**标准化协议**，统一定义工具、资源、提示词。
痛点解决：不用为每个工具单独写适配代码；一套协议，Agent可以动态发现外部工具，跨系统对接工具，统一权限、上下文传输；工具可以插拔，不用改Agent核心代码。

8. **ReAct 和 Plan-and-Execute 在你项目里怎么取舍，什么任务你绝对不用 ReAct**
- ReAct：思考+行动循环，每一步思考、调用工具；适合不确定、需要动态探索的任务。
- Plan-and-Execute：先生成完整任务规划，再按计划分步执行；适合任务目标清晰、步骤可预先规划的长任务。
> 不适合ReAct场景：高风险写操作（退款），ReAct单步迭代容易偏离目标、反复试探；这种优先Plan-and-Execute，前置整体规划，增加人工确认。

9. **Agent 安全你怎么做，提示注入、越权调工具、敏感信息回显分别怎么防**
- 提示注入：输入过滤、prompt沙盒隔离，使用结构化输出，分隔用户输入和系统prompt；检测注入特征。
- 越权调工具：工具层权限鉴权，每个工具绑定用户权限，不是Agent想调用就能调用；工具白名单。
- 敏感信息回显：LLM输出内容脱敏，屏蔽手机号、身份证；上下文里敏感数据提前脱敏后再送入LLM。

10. **模型路由你做过么，简单意图用便宜小模型，复杂任务路由大模型，路由本身不准怎么办**
路由：意图分类器做模型分发。
路由不准方案：
1. 路由增加置信度阈值，低于阈值直接路由到大模型兜底；
2. 路由模型持续用线上数据迭代；
3. 增加fallback机制，小模型处理失败自动升级到大模型。

11. **最近半年你学的新东西是什么，LangGraph源码、某个embedding模型、还是 harness 模式，怎么学的**
示例：主攻LangGraph源码与Agent Harness工程模式；学习方式：读源码、搭建POC项目、写单元测试；搭建Eval Harness做自动化评估，把Harness思想落地到Agent项目，解决断点、监控、评估问题。

12. **如果让你从零搭一套追觅内部的 Agent 开发框架，你会先写哪三层，为什么?**
优先三层：
1. **工具与安全层**：统一工具网关、参数校验、幂等、鉴权；Agent最容易出线上事故的地方，先兜底安全。
2. **状态与检查点层**：状态管理、Checkpoint断点，支撑长任务、故障恢复。
3. **模型调用层**：统一LLM接口、模型路由、token限流、重试熔断。
> 理由：上层应用可以迭代，底层稳定层优先落地，保证框架可生产，而不是先写prompt和业务逻辑。

13. **手撕算法：给定一个 DAG 表示 Agent 任务依赖，手写拓扑排序并输出一种合法执行顺序，顺便处理环检测**
```python
from collections import deque
def topo_sort(graph):
    n = len(graph)
    in_degree = {u:0 for u in graph}
    for u in graph:
        for v in graph[u]:
            in_degree[v] +=1
    q = deque([u for u in in_degree if in_degree[u]==0])
    res = []
    while q:
        cur = q.popleft()
        res.append(cur)
        for nxt in graph[cur]:
            in_degree[nxt] -=1
            if in_degree[nxt]==0:
                q.append(nxt)
    # 环检测
    if len(res) != len(graph):
        return None # 存在环
    return res
```
说明：Kahn算法；返回None代表有环，Agent任务依赖存在循环，无法执行；返回列表为合法执行顺序。

## HR面
1. **自我介绍**
2. **为什么看追觅，家电硬件公司做 AI Agent 你觉得机会在哪？**
追觅有硬件（扫地机、家电）+真实用户场景；Agent不是纯软件对话，可以打通设备控制、售后客服、订单物流，软硬一体；区别纯大模型公司，有真实落地业务场景，AI Agent可以落地到用户全链路，有商业化价值。

3. **你现在薪资多少，期望多少，base还是总包口径**
> 面试话术建议：按总包口径沟通，提前想好合理区间。

4. **前三轮技术面下来你觉得哪轮最吃力，自己哪块最虚？**
真诚，不要说致命短板；可以说：三面架构权衡、多Agent风险决策需要持续沉淀，正在持续补充Eval Harness和MCP相关实践。

5. **你做Agent项目时和算法、产品吵过架么，举一个真例子**
选业务权衡例子，突出解决问题，不是抱怨。示例：算法希望全自治Agent减少人工卡点，我提出高风险退款操作需要人工确认；最终达成灰度方案，低风险自动、高风险人工，用评估集验证风险。

6. **未来三年你想成为 Agent 架构师还是往管理走**
回答：短期深耕Agent工程落地，目标成为**Agent架构师**；当项目规模扩大，自然承担带团队任务，优先把技术做扎实。

7. **入职后能接受加班和线下协作节奏么，深圳base没问题吧？**
如实回答，确认地点、协作模式。

---

# 备考优先级建议
1. 项目讲稿（重中之重，三面反复深挖）
2. LangGraph + Agent Harness、多Agent架构、断点恢复
3. RAG全链路调优、召回问题排查
4. 中间件：Redis/MQ(Kafka/RocketMQ)在Agent场景的工程用法
5. 算法：LRU+TTL、合并k有序链表、拓扑排序（手写+复杂度）
6. Agent安全、评估指标、MCP协议、ToolCalling容错、幂等重试

信心打分：9分，全部题目给出工程向面试回答，贴合追觅这一场“生产落地”风格；部分开放架构题可以根据你真实项目经历微调。

要不要我把这份文档拆成**一页精简背诵版**（压缩字数，适合面试前快速过一遍）？