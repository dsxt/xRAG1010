# 阿里千问事业部ai应用全栈开发二面，不是考大模型，是考系统稳定性

> 根据面试截图整理。题干来自原帖，参考答案用于复习，不是候选人当时的口述。

## 面试感受

这轮不算刁难，但很吃真做过。背概念基本活不过三轮追问，尤其工具容错、记忆隔离、线上 bad case 这种，没踩过坑一眼假。阿里这边更在意你能不能把 Agent 从 demo 拉到生产：成本、延迟、权限、可观测，少一个都不行。面试官要是只问概念不带业务，反而要警惕他是不是自己也没落地过。整体别怂，把 trade-off 讲明白比背架构图加分。

## 二面

### 1. 自我介绍

**答题思路**

二面时间短，身份一句带过，把一个已经上线的 Agent 讲到对方能往下追。

1. 用一句话说年限和方向，点出你实际写过的是编排、工具网关、检索还是线上排障。
2. 只留一个系统：谁在用、要完成什么、你负责哪一段。
3. 用一个你能讲清实现的稳定性取舍收尾，例如写操作带幂等键、敏感动作先人工确认，或检索没有依据就拒答。
4. 职级、薪资和流量只讲你能回溯的事实。讲完停住，等对方选一层往下钻。

### 2. 挑一个你落地过的 Agent 项目，讲清楚为什么必须用 Agent，而不是普通工作流，核心链路和你个人贡献是什么

**答题思路**

面试官要听三件事：为什么收不成固定流程，链路怎么走，哪一段是你写的。用你真实落地的系统，按这个顺序讲。

1. 先讲用户目标和不确定在哪。说得清「必须用 Agent」的，通常是下一步取决于上一次工具结果、分支事先枚举不完，或中途必须等人。三件里至少有一件。
2. 步骤如果是固定的有向无环图，就直接说工作流更合适，模型只负责抽槽和生成回复。这个判断比硬讲「必须是 Agent」更贴这轮的稳定性主题。
3. 链路按一次请求讲下来：接入和身份、状态、模型提议、工具网关、检索或记忆、人工确认、结果落在哪。每跳说清输入和输出。
4. 个人贡献只圈你写过的层。准备一个坏例，按「现象、怎么定位、改了什么、拿什么验证」讲。优先选工具超时、重复写、记忆串用户、检索命中了但回答改了数字。
5. 指标只报你能回溯的。没有数字就讲评测集和坏例。
6. 停在你准备被追问的点上：状态存在哪、失败时用户看到什么、幂等键怎么组成。

### 3. LangGraph 里 State、Node、Edge、Interrupt 怎么配合，复杂任务里你怎么做状态流转和人工介入

**参考答案**

State 是带合并规则的共享数据，Node 只提交局部更新，Edge 决定下一跳，Interrupt 在副作用之前把图暂停到检查点，人处理完后用同一个 thread 恢复。

State 按字段拆开：消息、计划、当前步、工具结果、待审批动作、步数。消息用 `add_messages`：相同 id 原地更新，新 id 追加。给消息稳定 id，节点只返回新增或改过的那几条；把换了新 id 的历史再交回去，上下文会越拼越长。没有 reducer 的字段被整段覆盖。节点只返回这次要改的字段。并行分支写同一个字段时必须有 reducer，否则一次 super-step 里会冲突。

Node 按职责拆开：调模型、校验、执行工具、请求人工，不要合成一个大节点。Edge 里，固定边连接确定的下一跳；条件边只读状态，返回节点名，例如继续、转人工或结束。步数、墙钟和 token 预算写在状态里，由条件边截断。

暂停必须配进程外的 checkpointer，按 `thread_id` 存。敏感动作可以用 `interrupt_before` 停在工具节点进入之前；也可以在节点里先 `interrupt(动作)`，批准后再执行。动态 `interrupt()` 在恢复时会让整个节点从头再跑，它之前的代码会再执行一次，所以写操作放在 `interrupt()` 后面。恢复时用 `Command(resume=决定)` 带同一个 `thread_id`，`interrupt()` 直接返回这个决定。节点在写完并成功留下下一个检查点之前崩溃，恢复仍会再执行，写操作还是要带幂等键。拒绝写入状态后走到修改或结束。待审批有超时，超时就作废，未批准的动作不执行。

```python
def tool_node(state):
    decision = interrupt({"tool": state["pending_tool"], "args": state["args"]})
    if decision != "approve":
        return {"status": "rejected"}
    result = call_tool(state["pending_tool"], state["args"])
    return {"result": result, "status": "done"}
```

复杂任务把计划做成步骤和状态，合法转移由条件边约束。模型可以提议下一步，不能自行改写已经成功的步骤。

### 4. ReAct 和 Plan-Execute 各自适合什么场景，长任务里怎么防止规划跑偏和无限循环

**参考答案**

下一步强依赖刚刚的观察、步数又少，用 ReAct。目标清楚、步骤多、希望少跑几次大模型，用 Plan-Execute，并把计划和对停止的判断放进运行时。

ReAct 是推理和行动交替：模型给出工具调用或最终回答，运行时执行工具，把观察追加进上下文，再调模型。它适合排障、探索和「看完结果才能决定下一步」的短任务。长任务每步都重新决策，容易忘了原目标，token 也更贵。

Plan-Execute 先产出结构化计划，执行器只做当前步，环境变了再单独进入再规划。规划和执行可以用不同模型。它适合大体能拆开的长任务。旧计划在外部状态变化后会过时，所以再规划本身要有次数上限。

防跑偏要把目标、约束和已完成步骤放进状态，每步提示都带上，执行器只允许完成当前步。已经成功的步骤默认不重做，要重做必须走显式的再规划。再规划看到的是步骤状态，不是越来越长的散文。

防死循环靠运行时：最大步数、再规划次数、墙钟和 token 预算。同一工具配同一组规范化参数再次出现，或观察摘要没有新信息，就停。触顶后用已有观察收束，或交给人工，不再裸调模型。

停止条件写在代码里。只在提示词里写「不要循环」，模型仍然可能不停。

### 5. Function Calling 完整链路讲一下，模型输出工具参数后，你们怎么做 schema 校验、参数补全和失败重试

**参考答案**

模型只提议工具名和参数。解析、校验、补全、执行和重试都在运行时，身份类字段以登录态为准。

一轮调用的顺序是：按用户权限选出本轮允许的工具，把名字、说明和 JSON Schema 跟上下文一起交给模型；模型返回 `tool_calls`，其中的 arguments 在常见实现里是 JSON 字符串，先解析，已经是对象就直接校验。解析失败不打下游。工具名必须在本轮白名单里，再用 schema 检查类型、必填和枚举，丢掉未声明字段。并行的多个调用逐个校验。读操作可以按条返回错误；必须同时成功的一组写，有一条不合法就整组不执行。

参数补全只使用可信来源。登录态里的用户和租户覆盖模型填的同名字段；对话状态里已经确认的订单号可以补上。缺的是业务主键，而且状态里没有，就追问，不编一个。确认位、金额、是否提交这类字段不设默认值。

重试分两层，两边都要封顶。参数不合法：不打下游，把校验错误按对应的 `tool_call_id` 交回模型，限制它修正的次数。下游超时、429、5xx：网关对同一个幂等键做有限次退避，并加抖动。业务拒绝不重试。没有幂等键的写操作不自动重试。网关内部的重试和模型再次发起调用会叠在一起，所以网关的次数要少。

没有工具调用就是这一轮的结束候选。运行时仍然用最大步数兜住，避免模型不停地重新发起调用。

### 6. 工具调用出现超时、重复调用、返回脏数据，系统怎么降级、熔断、做幂等和恢复现场

**参考答案**

每次调用在网关里收成成功、失败或未知。编排只根据这个状态转移。未知不等于失败，更不能当成「没执行过」再打一次。

超时把连接和读取分开限制。到点后按幂等键向下游查询这笔调用的结果。查到成功就记成功，查到业务拒绝就记失败，查不到就保持未知。读操作可以对未知做有限次重试。写操作在未知时先停下来核对；下游如果没有按幂等键查询的能力，就进入对账或人工，不立刻再执行。

幂等键由任务、步骤、工具名和规范化参数组成。成功结果和不可重试的业务结果按这个键缓存，重放直接返回。瞬时失败不要被永久缓存成最终结果，否则后面的合法重试会被挡住。

脏数据先过响应 schema，再做少量交叉检查，例如状态和金额要能对上。原始报文留在追踪里。交给模型和用户的是错误码，不是一份半残 JSON。

降级按读写分开。读可以缺项返回，并写明哪一项没有。写失败就失败，不用模型编造一个结果。熔断按下游拆开，连续超时或 5xx 后打开，打开期间快速失败，半开时放探测。搜索超时只熔断搜索，不连带到下单。

现场恢复靠每步之后的检查点，里面有这一步的三种状态。进程挂了就从最后一步继续。未知步骤先核对再转移。用户用任务号看到当前步。

### 7. 多轮对话里上下文越来越长，你怎么做短期记忆压缩、长期记忆召回和关键信息不丢失

**参考答案**

短期记忆是最近窗口加滚动摘要，长期记忆是按用户隔离、可删除的事实；不能丢的槽位单独结构化保存，不放进摘要里碰运气。

短期只把最近几轮原文放进提示，更早的内容折成增量摘要，不要每轮把全量历史重新压缩一遍。摘要、原文、工具定义和检索片段共用一个 token 预算。

长期记忆在一轮结束后抽取偏好和稳定事实，带上用户、时间和来源。召回时先把当前话改写成独立查询，再按用户过滤，取少量相关条目注入。新旧冲突以更新的一条为准；冲突会改变动作时先问用户。注销和删除要能按用户删掉，不靠 TTL 慢慢过期。

订单号、金额、约束和已经获得的批准写成状态字段。做完摘要后用这些字段回填提示，摘要里丢了也能加回来。工具返回的权威结果留在结构化字段里，提示里放引用和必要片段。

验收用拉长的对话检查金额和约束还在不在，并检查有没有串到别的用户。摘要是有损的。关键槽位如果只活在摘要里，压缩一次就可能丢。

### 8. RAG 链路里 chunk 切分、混合检索、rerank 分别怎么调，召回噪声和幻觉怎么定位与兜底

**参考答案**

切分、混合检索和重排都用「问题能否召回该片段」来调。回答通顺不能用来代替召回评测。依据不在最终提示里，是检索或改写的问题；依据在，回答却相反，才是生成的问题。

切分先按标题、段落、表格和代码块进行，表格和成组步骤保持完整。长度用带标注的召回集试几档：太长会掺进无关句，太短会把条件切开。块之间留重叠，避免一句话被切断。可以检索小块，再回填它所在的小节。每块带上来源、版本、生效时间和权限。

混合检索里，向量覆盖同义说法，BM25 覆盖专有名词和编号。两路分数量纲不同，按名次做 RRF，常用常数 k 取 60，不把余弦和 BM25 直接相加。权限和生效时间在重排前滤掉。专有名词整片没命中，就检查词法索引和文档本身，而不是先换重排模型。

重排只用交叉编码器打召回后的几十条，留下少数几条。留几条、最低接受多少，用标注集同时看命中率和空召回率。排在前面的几条分差已经很小，说明召回里主要是噪声，重排拉不开。

日志留下改写后的查询、两路命中、融合分、重排分和真正进入提示的片段 id。噪声是进了提示的片段对不上实体或版本。幻觉是回答中的事实在这些片段里找不到，或工具已经给了数字，回答却改写了。

低于阈值就说明没查到，并允许追问。引用对不上的句子删掉。金额和状态用结构化字段。检索到的文本只作为数据，不作为指令。

### 9. 工具数量膨胀到几十上百个时，怎么让模型选对工具，避免误调用和提示词注入

**参考答案**

每轮只给模型一小撮它有权使用的候选工具。选得准和允许执行是两件事。用户内容、网页、文档和工具返回都按不可信数据处理。

先按角色取出允许的工具，再用工具名和说明做一次检索，留下少数几个。高频且稳定的工具可以常驻。说明里写清适用和不适用的情况，两个工具职责重叠时最容易叫错。候选太少会漏掉该调用的工具，太多会选错。用标注集看正确工具在不在候选里，再看模型最后有没有选中它。这两步分开，才能知道该改检索还是改模型。

模型只能调用本轮名单里的名字，参数仍然过 schema。没有合适工具就明确不调，可以向用户追问。不要用「必须调用某个工具」把这一步堵死。写操作不因为模型给出了高置信度就跳过确认。

系统策略放在用户文本和工具结果改不到的通道里。检索片段和工具返回去掉伪造的角色标记，作为数据塞回。第三方工具的说明同样不可信，不能靠一段说明把高权限工具引进本轮名单。文本里出现「立刻执行」也不改变网关的授权。身份字段继续以登录态为准。

### 10. 生产级 Agent 怎么分层设计，权限边界、审计日志、敏感操作人工确认怎么做

**参考答案**

模型只负责提议。身份、权限和副作用收在工具网关。敏感动作在执行前暂停，审批绑定的是即将发出的那一份参数。

接入层做认证和限流。编排层管状态、步数和预算，不存放数据库凭据。模型层产出工具提议和最终表述，提示词要有版本。工具网关是唯一执行副作用的地方，负责授权、schema、幂等、超时和熔断。记忆和检索按租户过滤。审计旁路记录全过程，不参与放行判断。

权限分两层。角色决定能看见哪些工具；资源规则决定能作用到哪些数据。能调用「查订单」只表示能查当前用户被允许的订单，资源 id 以登录态为准。工具凭据只留在网关，不进提示词，也不进日志。

审计用同一条 trace 串起用户、thread、模型名、提示词版本、工具名、规范化参数、结果码和审批人。秘密和证件号脱敏。记录追加写，和线上状态分开存放。

金额、删除、外发和改权限进确认策略。图停在工具节点执行之前，展示给人的参数就是将要执行的参数。审批单绑定这份参数的摘要，恢复时对不上就作废。超时把审批作废，未批准的动作不执行。

Function Calling 的输出只是提议。用户是否有权做这件事，由网关在执行前再判一次。

### 11. Agent 全链路可观测要埋哪些指标，怎么区分是模型弱、检索错、工具挂还是编排问题

**参考答案**

先保证一次用户请求有一条带步骤号的 trace，再看指标。四类故障比的是证据落在哪一段，不是回答通不通顺。

指标按段埋：

- 端到端看任务成功率、首 token、总时延、完成一次任务的 token 和费用、人工介入占比。
- 模型看时延、错误率、参数解析失败次数、输入和输出 token。
- 检索看空召回、进入提示的条数和重排分。
- 工具按名字看次数、时延、超时、错误码和熔断状态。
- 编排看步数、再规划次数、重复调用、检查点失败，以及落在未知的步骤数。

同一条 trace 上这样区分：

- 工具挂了：工具 span 超时或返回 5xx，换一个问题打同一个工具也失败。模型和检索的 span 正常。
- 检索错了：工具没有挂，最终提示里没有能支持答案的片段，或片段版本错了。这时回答往往仍然通顺。
- 模型弱：提示里的证据是对的，工具结果也是对的，但回答矛盾、工具选错，或参数过不了 schema。候选名单里没有正确工具时，这是工具检索或编排给错了名单；名单里有却选错，才归到模型。
- 编排有问题：每一步各自成功，但顺序错、该停没停、状态被覆盖，或人已经批准却没有用同一个 thread 恢复。看步骤图和状态差分。

没有步骤序号时，编排故障会被看成模型不行。

### 12. Kafka 在 Agent 系统里能承担什么角色，异步任务、事件驱动和失败重放怎么设计

**参考答案**

Kafka 承担任务受理、事件和可重放的日志。会话状态和检查点放在编排自己的存储里。消费按至少一次设计，用任务状态机把重复投递变成无害。

长任务在接口里先受理，马上把任务号返回给用户，Agent 在消费者里跑。审批通过、工具完成、文档入库都做成事件，编排和执行就拆开了。消息体只带任务号和事件 id，不带整段对话。用任务号做消息键，同一个任务进同一分区，从而保持顺序；不同任务并行。分区之间没有全局顺序。

发消息和落任务行之间有一个崩溃窗口：先落库再发，可能留下一个永远没人消费的任务；先发再落库，可能消费到一条没有任务行的消息。数据库事务里同时写任务和发件箱，再由发件箱投递，这个窗口才关上。消费端对事件 id 做唯一约束。

位移表示「事件已经收进任务表」，不表示 Agent 已经跑完。处理顺序是：用条件更新把任务抢成执行中并写上租约，成功后再提交位移，然后才开始跑。自动提交按时间交位移，对不上这个顺序，要关掉。跑的过程中续租。提交位移之后进程挂了，扫描器接手过期租约。终态上的重复消息直接返回。瞬时错误有限次退避；仍然失败就写入死信，再提交原位移，避免一条坏消息堵住分区。修好后按事件 id 重放，重放仍然走同一个幂等判断。

长推理不占着 poll 线程。Kafka 事务也覆盖不到外部工具，写工具自己带幂等键。

### 13. Redis 存会话状态和记忆时，怎么解决多用户隔离、TTL 失效和热点 Key 问题

**参考答案**

Redis 里是按用户隔开的热数据。订单号、约束和批准结果同时写在检查点里，键过期后还能装回来。

隔离靠键和身份一起做。键里包含租户和用户，这两项来自登录态，每次读写核对键上的用户和当前身份一致。只按会话号存储、又不校验归属，别人拿到会话号就能读。长期记忆的过滤条件里带用户。检查点的 thread 绑定这个用户，避免用别人的 thread 把状态恢复出来。必须落在同一分片的相关键使用同一个 hash tag。

会话用滑动过期，并另外记下创建时间，超过绝对寿命就不再续期。读取和续期用 `GETEX` 这样的单命令完成。共享缓存的过期时间加抖动，避免同一时刻一起失效后把检查点或数据库打满。过期是惰性加上定期抽样，不能当成准时的定时器。键不在了就按 thread 从检查点装回；检查点也没有，就告诉用户会话失效。用空槽位继续做动作，会把之前的约束弄丢。注销时按用户删除，不单靠过期。

热点通常来自大家共用的工具清单、热门检索缓存和全局计数。读多写少的放一份进程内短缓存，回源时做单飞。计数拆到多个键上。把一个会话拆小，是为了避免大键的读写占住分片主线程；这些键如果带着同一个 hash tag，仍然在同一个分片，不会因此摊到整个集群。删除大键用 `UNLINK`。刚写完就要读的会话读主节点，避免从副本读到还没有同步的槽位。

### 14. 最近在跟进哪些 Agent 方向的新技术，LangGraph、MCP 或 RAG 演进里你更看好哪条路线

**答题思路**

按「你最近真看过的一个点、三条路线各管哪一层、你押哪一层、它失败时长什么样」来讲。报一串发布名称，追问一次就断。

1. 从你自己系统里已经痛的问题起：工具接不完、长任务要停下来等人，还是答非所问。只展开你读过实现或上过线的那一个。
2. 三条路线管的不是同一层，可以这样讲：
   - LangGraph 是编排运行时，解决状态、条件转移、检查点和人工暂停。它不提高检索质量，也不替你规定工具协议。一次检索再生成，不必上图。
   - MCP 把工具、资源和提示收成统一协议，减少每个 Agent 各写一套适配。选择、鉴权、审计仍在你的网关。远程服务器的说明和返回内容默认不可信。
   - RAG 的演进解决有没有依据。混合检索、重排、有预算的查询改写、强制引用，这些是生产上常用的一步。开放式的多跳研究循环会增加延迟和失控面，要用坏例证明它值得开。
3. 表态落到你能量化的瓶颈。工具接入和权限最痛，就讲 MCP 加上网关；长任务经常跑飞或必须人工确认，就讲 LangGraph 的显式状态；坏例主要是依据不对，就讲 RAG。三条都说看好，等于没有选择。
4. 准备一个追问：你选的那条失败时怎么观测，什么负载下不用它。不编一个你没读过的版本号或基准分数。

### 15. 手撕算法：实现一个简易 Agent 工具调度器，支持任务入队、并发执行、超时取消和结果回调

**参考答案**

用有界队列加固定数量的 worker。每个任务放在独立的子 task 里执行，所以取消任务不会把 worker 一起取消。超时用 `asyncio.wait_for`，到点取消这一次协程。还在排队的任务，`cancel` 时立刻回调，并马上归还队列名额；已经在跑的任务，要协程自己响应 `CancelledError`。阻塞调用放进线程后不会被取消，并发槽会一直占到线程返回。

`submit` 和 `cancel` 都是 O(1)，空间是 O(队列容量 + 并发数)。完成标记先写下，回调恰好一次；监听器抛错也不影响 worker。还没开始的任务超过队列容量就拒绝。调度器跑在单个事件循环上，跨线程要用 `call_soon_threadsafe`。

超时只说明这边停止了等待。写操作在下游仍可能已经执行，幂等键放在工具网关，调度器不把超时当成精确一次。

```python
import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


class ToolJobCancelled(Exception):
    """任务在排队中被取消，或运行中的协程响应了取消。"""


@dataclass
class ToolJob:
    job_id: str
    run: Callable[[], Awaitable[Any]]
    timeout: float
    on_done: Callable[[str, Any, BaseException | None], None]
    finished: bool = False


class ToolScheduler:
    """有界队列和固定 worker。每个任务的回调恰好触发一次。"""

    def __init__(self, concurrency: int, queue_size: int) -> None:
        if concurrency < 1 or queue_size < 1:
            raise ValueError("concurrency and queue_size must be >= 1")
        self._concurrency = concurrency
        self._queue_size = queue_size
        self._queued: OrderedDict[str, ToolJob] = OrderedDict()
        self._jobs: dict[str, ToolJob] = {}
        self._inflight: dict[str, asyncio.Task[None]] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._wakeup = asyncio.Event()
        self._stop_signals = 0
        self._stopping = False

    def start(self) -> None:
        if self._workers:
            return
        loop = asyncio.get_running_loop()
        for _ in range(self._concurrency):
            self._workers.append(loop.create_task(self._worker()))

    def submit(self, job: ToolJob) -> None:
        if self._stopping:
            raise RuntimeError("scheduler is shutting down")
        if job.finished:
            raise ValueError("job already finished")
        if job.timeout <= 0:
            raise ValueError("timeout must be positive")
        if job.job_id in self._jobs:
            raise ValueError(f"duplicate job_id: {job.job_id}")
        if len(self._queued) >= self._queue_size:
            raise asyncio.QueueFull
        self._jobs[job.job_id] = job
        self._queued[job.job_id] = job
        self._wakeup.set()

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.finished:
            return False
        if self._queued.pop(job_id, None) is not None:
            self._callback(job, None, ToolJobCancelled())
            return True
        task = self._inflight.get(job_id)
        if task is not None and not task.done():
            task.cancel()
            return True
        return False

    async def shutdown(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        if not self._workers:
            while self._queued:
                _, job = self._queued.popitem(last=False)
                self._callback(job, None, ToolJobCancelled())
            return
        self._stop_signals += len(self._workers)
        self._wakeup.set()
        await asyncio.gather(*self._workers)

    async def _worker(self) -> None:
        while True:
            job = await self._next()
            if job is None:
                return
            child = asyncio.create_task(self._execute(job))
            self._inflight[job.job_id] = child
            try:
                # 直接 await child 会把子任务的取消传给 worker。
                await asyncio.wait({child})
                if not child.cancelled():
                    child.exception()
            except asyncio.CancelledError:
                child.cancel()
                raise
            finally:
                if self._inflight.get(job.job_id) is child:
                    self._inflight.pop(job.job_id, None)

    async def _next(self) -> ToolJob | None:
        while True:
            if self._queued:
                _, job = self._queued.popitem(last=False)  # 先入队的先执行
                return job
            if self._stop_signals:
                self._stop_signals -= 1
                return None
            self._wakeup.clear()
            if self._queued or self._stop_signals:
                continue
            await self._wakeup.wait()

    async def _execute(self, job: ToolJob) -> None:
        try:
            result = await asyncio.wait_for(job.run(), job.timeout)
        except TimeoutError as exc:
            self._callback(job, None, exc)
        except asyncio.CancelledError:
            self._callback(job, None, ToolJobCancelled())
            raise
        except Exception as exc:
            self._callback(job, None, exc)
        else:
            self._callback(job, result, None)

    def _callback(
        self,
        job: ToolJob,
        result: Any,
        error: BaseException | None,
    ) -> None:
        if job.finished:
            return
        job.finished = True
        self._jobs.pop(job.job_id, None)
        try:
            job.on_done(job.job_id, result, error)
        except Exception:
            return
```
