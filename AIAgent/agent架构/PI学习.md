# Pi 源码精读

这份文档面向读源码，不面向使用手册。产品行为以 `packages/coding-agent/docs/` 为准。这里回答三件事：代码在哪、按什么顺序读、一次请求怎么穿过各层，以及这些形状为什么要做成这样。

仓库是 npm workspaces monorepo，Node `>= 22.19`，TypeScript 用 erasable 语法（无 enum、无参数属性）。版本以各包 `package.json` 为准，当前公开包都是 `1.4.0`。

## 先建立的判断

`pi` 命令是 `@earendil-works/pi-coding-agent`。它**不**依赖 Chord，也**不**依赖 Durable。

仓库里实际有两条运行时：

| | 现在的 CLI / SDK | Durable（实验） |
|---|---|---|
| 入口 | `packages/coding-agent` | `packages/durable` |
| 循环 | 进程内 `Agent` + `runLoop` | 提交后才可见的任务状态机 |
| 会话 | JSONL 树，内存里重建活动分支 | Chord 文档 + 存储提交 |
| 模型 | `@earendil-works/pi-ai` | 同一套 `pi-ai` |
| 状态 | 进程死了，进行中的请求丢了，会话文件还在 | 进程死了，从上次 checkpoint 接着跑 |

先把 CLI 这条读通。Durable 和 Chord v2 是下一套架构，单独开一轮。

构建顺序就是依赖方向（根 `package.json` 的 `build`）：

```text
telemetry → schema → chord → tui → codemode → mcp
        → ai → durable → env → agent → coding-agent
```

`evals` 不在这条链上，它是行为评测。

```text
coding-agent
  ├─ agent          工具循环、队列、事件
  ├─ ai             模型、鉴权、各家 API 流
  ├─ tui            终端差分渲染
  ├─ mcp            MCP 客户端（独立，不依赖其它 pi 包）
  ├─ codemode       QuickJS 沙箱里跑模型写的 JS
  └─ schema         被 ai / agent 用来描述和校验工具参数

durable
  ├─ ai
  ├─ chord          对象、契约、可复制状态、链接
  └─ env            可选：工具在远端机器上执行

ai ── telemetry, schema
chord ── schema, telemetry
```

## 一次交互请求怎么走

用户在交互模式回车，到模型看到工具结果，经过这些函数。行号会变，以符号为准。

```text
cli.ts
  setupCli(); main(argv)
main.ts
  resolveAppMode()          有 TTY 且不是 --print / rpc / json → interactive
  createAgentSessionRuntime → createAgentSessionServices → createAgentSession
InteractiveMode             编辑器 onSubmit
  session.prompt(text)

AgentSession.prompt                         core/agent-session.ts
  1. 以 / 开头：扩展命令，不进模型
  2. input 扩展事件；返回 handled 则结束
  3. 展开 /skill:name 和 prompt template
  4. 正在流式：steer（本轮工具结束后插入）或 followUp（整轮结束后插入）
  5. 校验模型和鉴权
  6. _startRun → _prepareRun
       必要时先 compact
       before_agent_start
       组 user 消息（图片按当前模型限制处理）
       系统提示或工具集变了，前面插一条 system 消息
     agent.prompt(messages)
     之后：可重试错误、再次 compact、agent_before_settle，再 agent.continue()

Agent.prompt                                packages/agent/src/agent.ts
  agentLoop() → runAgentLoop() → runLoop()  packages/agent/src/agent-loop.ts

runLoop 一轮（一个 turn = 一次模型请求 + 这批评工具）：
  prepareNextTurn（上一轮结束之后才有）
  把 steering 消息写进上下文
  prepareRequest（每次请求前，可换 context / model）
  streamAssistantResponse → streamFn
  stopReason 是 error / aborted：finishTurn 后直接 agent_end
  有 toolCall：
    stopReason === "length" 时参数可能被截断，全部记失败，不执行
    否则 executeToolCalls（默认 parallel：逐个 preflight，允许的并发执行）
    beforeToolCall 可拦截；afterToolCall 可改结果
    整批结果都带 terminate 才不再请求模型
  finishTurn：end 立即停；continue 保证再请求一次
  有工具结果或 steering → 内层再转
  否则排空 followUp；都没有 → agent_end

streamFn 在 coding-agent 里是 pi-ai 的 streamSimple
  Models.stream                               packages/ai/src/models.ts
    解析鉴权、按 model.api 懒加载实现
  例如 api/anthropic-messages.ts
       api/openai-responses.ts
       api/openai-completions.ts
       api/google-generative-ai.ts
  统一吐出 AssistantMessageEvent：
    text_* / thinking_* / toolcall_* / done / error

工具执行回到 coding-agent 注册的 AgentTool
  core/tools/{read,bash,edit,write,grep,find,ls,powershell}.ts
  结果变成 toolResult，再进下一轮 streamFn

会话落盘：AgentSession 订阅 Agent 事件，交给 SessionManager
  core/session-manager.ts
  JSONL，每条有 id 和 parentId，当前条目决定活动分支
  当前格式版本 CURRENT_SESSION_VERSION = 3
```

四个界面共用 `AgentSession`：交互、print、JSON、RPC。差别只在 I/O。`modes/index.ts` 导出 `InteractiveMode`、`runPrintMode`、`runRpcMode`。

官方的压缩版说明在 `packages/coding-agent/docs/how-pi-works.md`，和上面这条链一致。

## 优秀设计

下面每条都是读源码时值得停下来的决定。格式固定：要解决的问题、代码落在哪、为什么必须是这个形状。理由来自实现和规格里的约束，不是功能清单。

### 1. 会话是只追加的 JSONL 树

问题：用户会从半路改口、从旧消息开一条新分支、fork 出去试另一种改法。如果会话是一条会被改写的数组，分支要么复制整个文件，要么丢掉没走的那条历史。进程在写入中途崩溃时，改写文件还会留下半截 JSON。

做法：`SessionManager` 把每条记录追加到一个 JSONL 文件。除了文件头，每条都有 `id` 和 `parentId`。当前条目沿 parent 走到根，就是活动分支，也是下一次模型请求的历史。`/tree` 换当前条目，`/fork` 把选中的历史抄进新文件。格式见 `docs/session-format.md`，版本在 `CURRENT_SESSION_VERSION`（现在是 3）。v1 是线性序列，加载时迁到树。

为什么：追加写崩溃了最多丢掉最后一行，已经落盘的行仍是合法 JSON。分支不改旧行，所以同一文件里可以留着没采纳的尝试。活动分支是读出来的投影，文件本身不保存「当前数组」这份会漂移的状态。

读：`core/session-manager.ts` 的 `SessionEntryBase`，然后找按 `parentId` 收集活动路径的函数。

### 2. 压缩是新条目，历史留在树上

问题：上下文窗口满了必须丢掉旧消息，但用户还要在 `/tree` 里看见压缩前发生了什么，下一次压缩也要知道上次摘要已经覆盖了哪一段。

做法：`core/compaction/` 从活动分支末尾往回走，留住最近约 `keepRecentTokens`（默认 2 万）的消息，把更早的一段交给模型写成结构化摘要，再追加一条 `CompactionEntry`，里面有摘要和 `firstKeptEntryId`。下次组请求时，模型只看见摘要加上该 id 之后的消息。原始条目还在文件里。从旧条目开新分支时，另有 branch summary，避免切分支后模型失去那条枝上做过的事。摘要请求关掉 prompt cache 写入，因为这种一次性提示不会被复用。

为什么：删除旧行会让树的 parent 断掉，`/tree` 和重放都无法回到压缩前。用「从哪条开始保留」做游标，摘要可以迭代：下次压缩把上一份摘要当作输入，而不是从会话开头重算。自动压缩的触发是 `contextTokens > contextWindow - reserveTokens`（`reserveTokens` 默认 16384），它只决定何时开始，不决定摘要能写多长。

读：`docs/compaction.md` 的图，再读 `compaction.ts` 的 `findCutPoint` 和 `shouldCompact`。

### 3. 系统提示和工具集写在转录里，重放即得到当前值

问题：系统提示、启用的工具会在会话中途变化（换技能、扩展调用 `setActiveTools`、压缩后重发）。如果这些只存在内存或单独的设置文件里，重开会话、换模型、从旧分支继续时，模型看到的提示会和当时不一致。整段重写系统提示还会打掉供应商的前缀缓存。

做法：第一条请求把完整分段和工具声明写成一条 `role: "system"` 消息。之后只追加补丁：`sections` 按名字替换，`null` 表示删掉一节；工具用 `toolsAdded` / `toolsRemoved` 表示差量。`declareToolChanges`（`agent-loop.ts`）在每次请求前计算「转录里已经声明的工具」和「`context.tools` 里现在能执行的工具」的差，写成下一条 system 消息。重放这些 system 消息就得到当前提示和当前工具，没有第二份提示状态。分段包在同名标签里，例如 `<tools>`、`<skills>`，后一次补丁和前一次正文对得上。

为什么：会话文件是唯一事实来源。差量补丁让稳定前缀保持不动，只有变了的那一节进入新消息，前缀缓存才有机会命中。可执行集合和声明集合分开，是因为隐藏工具、codemode、扩展可以改变「模型该看见谁」，但运行时仍要按真实注册表执行。

读：`docs/session-format.md` 里 system 消息那两行 JSON，然后读 `agent-loop.ts` 的 `declareToolChanges` 和 `system-prompt.ts` 开头的分段说明。

### 4. 会话消息比模型消息宽，边界上再收窄

问题：UI 需要展示 `!` 命令、压缩摘要、扩展塞进来的自定义卡片。供应商只接受 `system` / `user` / `assistant` / `toolResult`。把 UI 类型塞进 `pi-ai` 会让所有供应商实现依赖 coding-agent；在进模型之前丢弃又会让这些内容无法被模型利用（有的应该被看见，例如压缩摘要和 `!` 的输出）。

做法：`pi-agent-core` 的 `AgentMessage` 用声明合并扩展（`CustomAgentMessages`）。coding-agent 在 `core/messages.ts` 增加 `bashExecution`、`custom`、`branchSummary`、`compactionSummary`。循环全程携带 `AgentMessage`。只有调用 `streamFn` 时才经 `convertToLlm` 收成供应商消息：摘要变成带固定前后缀的 user 文本，`excludeFromContext` 的 `!!` 命令直接消失。

为什么：agent-core 保持供应商无关，不引用 `pi-ai/compat`。应用自己决定哪些自定义角色进上下文、以什么角色进去。模型协议变化时只改边界上的转换，会话文件格式不用跟着供应商走。

读：`packages/agent/README.md` 的 Message Flow，然后 `core/messages.ts` 的 `convertToLlm`。

### 5. 模型失败是一条 assistant 消息，不是抛出的异常

问题：供应商超时、用户按中断、上下文溢出、工具参数不合法，都要进会话、进 UI、进下一轮调度。如果 `stream()` 抛异常，调用方要有第二条错误通道，而且抛出点之前已经写出的半条消息很难再落盘。

做法：`StreamFn` 的契约写在 `packages/agent/src/types.ts`：请求失败不要 throw，要返回流，并收成 `stopReason: "error" | "aborted"` 的 `AssistantMessage`。`runLoop` 看到这两种就 `finishTurn`、`turn_end`、`agent_end`。`AgentSession._handlePostAgentRun` 再决定要不要重试或压缩后 `continue()`。`error` 和 `aborted` 上的 `finishTurn` 返回值被忽略，避免钩子把失败轮次接成死循环。

为什么：UI、JSON 模式、RPC、会话树都已经会渲染 assistant 消息。失败走同一条事件序列，订阅者不用再监听一种「循环炸了」的旁路。可恢复的溢出和长度截断留在 `AgentSession`，因为那是产品策略（要不要再花一次请求做摘要），不属于通用循环。

读：`types.ts` 里 `StreamFn` 的注释，再看 `runLoop` 里对 `stopReason` 的两个分支。

### 6. 流在同步时刻就交出去，准备工作放在流后面

问题：解析 OAuth、刷新令牌、按 `model.api` 动态加载某一家 SDK，都是异步的。若 `Models.stream` 本身是 async，调用方得先 await 准备，失败变成拒绝的 Promise，和流里的 `error` 事件成了两种失败。agent 循环希望立刻拿到可迭代对象，好把后续事件推进 `EventStream`。

做法：`api/lazy.ts` 的 `lazyStream` 同步构造 `AssistantMessageEventStream` 并返回。`setup()` 的 Promise 在后面跑；成功就把内层流逐事件转发，失败则推一条 `error` 事件并结束。动态加载放在 `api/*.lazy.ts`，真正的 HTTP 实现可以不被打进不使用的包。

为什么：调用方只有一种消费方式：`for await` 加最后的 `result()`。准备失败和供应商中途失败外观相同，重试、落盘、UI 都不用分支。同步返回也让「还没开始请求就可以 abort」成立：abort 信号在 setup 期间已经挂上。

读：`lazy.ts` 全文很短，接着打开一家 `*.lazy.ts` 看它加载哪个实现。

### 7. `pi-ai` 的包入口不带供应商

问题：OpenAI、Anthropic、Google、Bedrock 各有一份重 SDK。coding-agent 需要全部内置，但嵌进别的应用、或打浏览器包时，不该因为 `import '@earendil-works/pi-ai'` 就把每一家拖进来。

做法：`ai/src/index.ts` 只导出类型、`Models`、鉴权、faux provider 和工具函数，注释写明无副作用。工厂在 `@earendil-works/pi-ai/providers/<id>`，线路实现在 `@earendil-works/pi-ai/api/*`。`package.json` 的 `sideEffects` 只标了少数会注册表的入口。旧的全局函数留在 `compat`，新代码走 `Models` 实例。

为什么：依赖图和打包图一致。测试用 `providers/faux.ts` 可以不碰网络、不碰密钥，`coding-agent/test/suite/` 就是这么跑的。模型目录是生成的（`catalog/`），和「如何呼叫一家 API」分开，换价格和上下文窗口不用改流式解析。

读：`ai/src/index.ts` 前 10 行，再看 `models.ts` 里 `stream` 如何按 `model.api` 选择实现。

### 8. 两条队列：steer 和 followUp

问题：模型还在跑工具时，用户会再打一句。这句话有两种含义。一种是纠正当前步骤（「停，改成只读配置」），应在这一轮工具结果回到模型之前插进去。另一种是追加任务（「做完这个再跑测试」），应等当前目标自然结束。若只有一个队列，纠正会排到长任务之后，追加任务又会把正在进行的工具链打断。

做法：`Agent` 有 `steeringQueue` 和 `followUpQueue`。`runLoop` 在每轮请求前排空 steer；工具结果或 steer 会让内层循环继续。内层不再继续时才排空 followUp。`QueueMode` 为 `one-at-a-time` 时，每个排空点只取最老的一条，所以压缩这种长时间的 `prepareNextTurn` 只会补拉一次，避免一次交出两条。正在流式时，`AgentSession.prompt` 要求调用方显式给 `streamingBehavior`，交互模式据此选择。中断把还没进模型的队列交回编辑器。

为什么：插入点是调度语义，不是 UI 细节。steer 发生在 assistant 回合之后、下一次请求之前，模型能在看到工具结果的同时看到人的纠正。followUp 不参与「还有没有工具要继续」的判断，所以不会把一次已完成的回答再拉开，除非队列里真有内容。`prepareRequest` 自己不读队列，避免和 one-at-a-time 的计数打架；长时间准备之后由循环补一次 steer 轮询，用户在压缩期间打的字不会丢。

读：`packages/agent/README.md` 的 continue / queued input，对照 `runLoop` 里 `getSteeringMessages` 和 `getFollowUpMessages` 的三处调用。

### 9. 工具默认可并行，落盘顺序跟 assistant 原文

问题：一次回复里经常有多路 `read` / `grep`，串行会把延迟加在一起。并行完成后，若按完成顺序把 `toolResult` 写进上下文，下一家供应商或重放会看到和 tool call 不一致的顺序；只靠 `toolCallId` 对齐并不够，因为会话树和部分 API 都按数组顺序消费。

做法：默认 `toolExecution: "parallel"`。每个调用先按源顺序做 preflight（解析参数、`beforeToolCall`），然后允许执行的那些并发跑。`tool_execution_end` 谁先做完谁先发出，UI 可以马上画完。写入上下文和 `turn_end.toolResults` 时再按 assistant 内容里的顺序排好。批里只要有一个工具标了 `executionMode: "sequential"`，整批改回串行。

为什么：preflight 串行，是为了让钩子看到稳定的顺序，并在真正产生副作用前拦截。执行并发，是为了只让纯读重叠。整批降级而不是「串行的那个等别人」，是因为串行工具表达的是「我和同批的其它副作用有顺序」；只把它自己排队，仍会和并行的写交错。事件顺序和消息顺序分开，UI 不需要为了协议正确性而卡住显示。

读：`agent-loop.ts` 的 `executeToolCalls`，以及 `types.ts` 里 `ToolExecutionMode` 的注释。

### 10. 被截断的 tool call 不执行；提前结束必须整批同意

问题：输出顶到 `maxTokens` 时，`stopReason` 是 `length`，最后一个工具的参数 JSON 可能只写了一半。执行它会变成一次真实的半截 `edit` 或 `bash`。另一个方向：某个工具想说「到此为止，别再叫模型」。如果批里还有别的工具正在改文件，单独一条 `terminate` 会丢掉那些结果，模型下一轮看不到。

做法：`length` 且带 tool call 时，`failToolCallsFromTruncatedMessage` 给每个调用一条失败结果，不进入 `execute`。`beforeToolCall`、`afterToolCall` 和工具返回值都可以带 `terminate: true`，但 `shouldTerminateToolBatch` 要求本批每一个已完成结果都为 true，循环才不再为这批结果请求模型。

为什么：残缺参数没有安全的修补方式，失败结果仍然是合法的 `toolResult`，模型能看见「这些调用没有跑」并决定重试。`terminate` 用全批合取，是因为工具结果是一个原子回合：要么模型看见全部结果并停止，要么看见全部结果并继续。混着停会留下没有对应模型回合的副作用。

读：`runLoop` 里 `stopReason === "length"` 的分支，和 `shouldTerminateToolBatch`。

### 11. 同一个文件的修改串行，不同文件仍然并行

问题：默认并行之后，两个 `edit` 读到同一份旧内容再各自写回，后写的覆盖先写的，而且两边的 diff 都「成功」了。路径字符串还可能一个是相对路径、一个是符号链接，看起来像两个文件。

做法：`withFileMutationQueue` 用 `realpath` 当键，把针对同一 inode 的写操作接成 Promise 链。文件还不存在时 `realpath` 会 `ENOENT`，退回 `resolve` 后的路径，让并发创建也进同一条链。不同键互不等待。注册本身还有一条 `registrationQueue`，避免两个调用同时读到「还没有队列」而各建一条。

为什么：锁的粒度是文件而不是进程，读工具和写其它文件的工具保持并行。用真实路径而不是用户传入的字符串，符号链接和 `a/../b` 才会撞上一把锁。队列是进程内的，和「Pi 没有跨进程文件锁」一致；要隔离得靠容器，而不是在这里假装有分布式锁。

读：`core/tools/file-mutation-queue.ts`，看 `edit` / `write` 在哪里包上它。

### 12. 大输出按工具的阅读方向截断，并留下完整副本

问题：把命令输出或文件全文塞进上下文会撑爆窗口，而且模型会在无关日志上浪费注意力。一刀切地只留开头，会切掉命令最后的报错；只留结尾，会切掉源文件的包声明和函数签名。

做法：`truncate.ts` 同时用行数（默认 2000）和字节（默认 50KB），先到先截，并且尽量不切断一行。`read`、`grep`、`find`、`ls` 用 `truncateHead`。bash / PowerShell 的流式累积器用 `truncateTail`（`output-accumulator.ts`），因为进程的结论通常在末尾。截断标记告诉模型发生了截断；更长的原文可以落到临时文件，路径写进结果。codemode 里调用 `bash` 时，脚本看到的上限比模型看到的更宽（约 1MiB，两头保留），模型只拿到脚本筛选后的输出。

为什么：截断策略跟「谁在读」绑定。人看文件从上面读，模型也需要头部结构；人看命令从下面看退出原因。把完整输出留在文件里，模型下一轮可以用 `read` 按偏移继续，而不是在这一轮把 10MB 日志都放进提示。

读：`truncate.ts` 文件头注释，再对照 `read.ts` 和 `output-accumulator.ts` 各调用了哪个函数。

### 13. 工具参数是一份 JSON Schema，既发给模型也用来校验

问题：工具参数要有 TypeScript 类型、要放进供应商的 tool definition、还要在执行前拒绝模型编造的字段。三份手写定义会漂移：类型允许的字段，模型 schema 里没有，或相反。用 `eval` 或代码生成做校验，在严格 CSP、以及打包进单文件二进制时都要额外运行时。

做法：`@earendil-works/pi-schema` 的 builder（`obj`、`str`、`opt`…）返回普通 JSON Schema。`Static<typeof Schema>` 从这份 schema 推出类型。`compile` 把 schema 编成一次性闭包，`check` 是无分配的热路径。同一对象交给供应商。`format` 只是注解，默认不校验，避免上游 schema 里的自定义 format 把合法参数判死。执行前 agent-loop 调用 `validateToolArguments`。

为什么：模型看见的契约和运行时拒绝的契约是同一份数据。schema 包不依赖其它 pi 包，Chord 的契约、MCP 的 inputSchema、内置工具可以共用。编译成闭包而不是生成源码，是为了在不能 `eval` 的环境里仍然接近代码生成校验器的速度。

读：`packages/schema/README.md` 的 Quick Start，再看 `core/tools/read.ts` 的参数 schema 如何同时成为工具定义。

### 14. codemode：脚本里的工具调用不进入模型上下文

问题：模型若用普通工具循环去「读 30 个文件、过滤、再算」，每一跳都是一轮完整请求，上下文里堆满中间结果，费用和延迟都线性涨。把 Node 的 `fs` 暴露给模型写的脚本，脚本就拥有了和 Pi 进程一样的权限，还绕过了工具钩子。

做法：`@earendil-works/pi-codemode` 在 QuickJS WASM 里把脚本当成 async 函数体执行。沙箱没有文件系统、网络和计时器。外面的能力只通过注入的 `tools.*` 进来，参数和返回值走 JSON。脚本可以用 `Promise.all` 并行调用。回到模型的只有 `text()` / `image()` / `return` 和失败信息。`store` / `load` 的成功写入追加为会话里的 custom 条目，所以换分支不会看见另一条枝上的键。内置扩展是 `replaceable`：用户用同名工具换掉它时，内置实现退出，而不是两个 `codemode` 并存。

为什么：中间结果留在沙箱里，模型上下文只增加一份脚本和一份筛选后的输出。工具调用仍走 `AgentTool.execute`，`beforeToolCall`、文件队列、截断都还在。WASM 解释器没有宿主 API，权限边界是「能调用的工具」，和进程沙箱是两层不同的东西。store 进会话树，是为了和分支语义一致：状态跟着走过的那条 parent 链，而不是一份全局 Map。

读：`packages/codemode/README.md` 开头，再读 `coding-agent/src/extensions/codemode/tool.ts` 如何把会话工具注进沙箱。

### 15. 差分渲染，并且一帧一次提交

问题：每打一个字就清屏重画，光标和选择会闪。逐行 `write` 时，终端会把半帧画出来。主屏模式还要和用户的滚动缓冲区共存，不能接管整个屏幕。

做法：组件实现 `render()`，返回字符串行。`TuiMainScreen` 记住上一帧的行，只把变化的行写回终端。调用方自己包上同步输出序列（CSI 2026）：开始缓冲、写入差分、再一次性显示。图片行用终端图形协议，换帧时按 id 回收上一帧的图。`TUI` 接口下面还有备用屏实现，滚动由应用自己管，因为备用屏没有可靠的终端滚动缓冲。

为什么：差分把一次按键的输出从「整个对话」降到「改动的那几行」。同步输出把这些写合并成用户可见的一次更新，避免看到先擦掉再补上的中间态。组件不碰 stdout，渲染器才能换主屏或备用屏，测试也能喂一个假终端。

读：`packages/tui/README.md` 的 Features，再看 `tui-main-screen.ts` 里 `previousLines` 如何参与下一帧。

### 16. 项目信任只管加载哪些资源

问题：项目里的 `AGENTS.md`、扩展、skills 是可执行或可影响提示的输入。打开一个不信任的仓库不该自动跑它的扩展。与此同时，如果在工具层做一套「允许读哪些路径」的权限系统，又会和用户真正要用的沙箱（容器、micro-VM）重复，而且必然有漏洞。

做法：`project-trust.ts` 在加载项目设置和项目资源之前做一次信任决定。未信任就不加载那些资源。信任之后，`read` / `bash` / `edit` 使用启动 `pi` 的那个用户的操作系统权限。扩展跑在同一进程里。需要隔离时用文档里的三种容器化，而不是工具里的路径白名单。

为什么：把「这段代码会不会被加载」和「加载之后能做什么」拆开。前者 Pi 能可靠地做，因为它发生在自己的启动顺序里。后者一旦做成应用层 ACL，模型仍可能通过 `bash` 绕过，用户会误以为已经隔离。诚实的边界让容器方案成为唯一的隔离机制。

读：`docs/how-pi-works.md` 最后一节，以及根 README 的 Permissions & Containerization。

### 17. Durable：看得见的进度都已经提交；外部动作不在提交回调里

问题：CLI 的循环把状态放在内存里，进程在模型请求中途死掉，会话文件里有上一轮，进行中的这一轮没有。重开只能靠用户再发一次。若把「调用模型、跑 bash、写会话」放在同一个事务里，事务会跨网络等待，锁住整条写入线，崩溃时还会留下执行到一半的外部副作用和没提交的记录。

做法：`pi-durable` 的规格第一条是：一次 Session 提交把不可变 entry、任务记录和文档原子写下去；只有提交之后的状态才能被观察（延迟提交是明确标出来的例外，崩溃会丢掉那个窗口）。任务是状态机，每步 checkpoint。`pi.generation` 调模型，拥有自己的 `pi.tool` 子任务。不变量 4：外部效果不在 Session 的变更事务里执行。存储错误让这次打开的 Session 失败，必须重新打开，避免在坏掉的写入线上继续发看起来成功的事件。

为什么：崩溃恢复变成「从最后一条已提交的 checkpoint 继续」，而不是「猜内存里跑到哪」。模型请求死掉算一次失败的尝试，可以重试；连续把进程打崩的请求会结束，而不是无限重启。外部效果放在事务外，提交回调就可以只做内存里的 JSON 变更并尽快落盘，bash 的副作用用工具任务自己的状态来记录「已经跑过」，避免恢复时无条件再跑一遍。

读：`packages/durable/docs/spec.md` 开头的 core rule 和不变量 1–8，再读 `harness/generation.ts` 的 checkpoint 类型。CLI 主路径不必先读这一套。

### 18. Chord：调用方拿到的是引用；JSON 修订靠所有权，不靠冻结

问题：对象会热更新、会跨进程，调用方如果握住真实对象，换实现时所有指针都失效，远程和本地的调用方式也不一样。状态要同步给多个观察者，若每次全量拷贝，大文档的开销在每次按键上；若 `Object.freeze` 整棵树，又要在热路径上遍历。

做法：`get` 返回引用，不返回对象。对象的身份是 `(类型, 参数, principal)`，所有者和客人拿到的实现可以不同。状态是 JSON，观察者先拿快照再拿增量。`@earendil-works/chord/delta` 用所有权约定不可变：`track()` 之后调用方不再改那棵树，草稿只在 `beginChange` 到 `prepare` 之间可写，`applyImmutable` 与权威版本共享没改过的子树。非法就地修改不会被检测，会静默破坏，这是写明的代价。热更新时，已经在跑的调用用它开始时的那份代码跑完，状态留下，不兼容的变更在安全点迁移。

为什么：引用让本地调用和跨链接调用变成同一种写法，热更新只换引用背后的实现。结构共享让增量同步不必复制整份会话。不做防御性冻结，是因为这份代码坐在热路径上，而调用方都是 Chord 自己的运行时；规格把「谁可以写」写成所有权表，而不是在每次读时再检查一遍。

读：`packages/chord/README.md` 前两节，然后 `src/delta/README.md` 的 Mutation rights 表。v2 的目标写在 `docs/next/design.md` 第 1 节，那是另一轮阅读。

### 19. 遥测没有环境上的「当前 span」

问题：异步 agent 里，一个请求会扇出成工具、子任务、压缩。用 `AsyncLocalStorage` 保存当前 span，会在队列、定时器和跨进程恢复时悄悄串线：steer 进来的新输入可能继承到不该继承的 trace，重开进程时又没有这个存储。

做法：`@earendil-works/pi-telemetry` 只定义 `TelemetryContext` / `TelemetrySpan`。span 通过参数传递。`NOOP_TELEMETRY_CONTEXT` 是默认值，库代码不需要判断「有没有装遥测」。适配器由应用持有，不放进 Chord 的 `Context`。Durable 把 trace carrier 存进任务 JSON，恢复时从载体抽出父 span，而不是假设进程内还有调用栈。记录的属性是 id 和控制信息；消息正文、工具参数和结果不进 span，避免遥测后端变成第二份会话库。

为什么：显式传递在序列化、跨进程和测试里都看得见。noop 让库可以无条件打点，调用方不接适配器就没有开销也没有分支。内容和 id 分开，是因为 trace 会送到进程外，而会话里才是正文的存放处。

读：`packages/telemetry/README.md` 开头，以及 `durable/README.md` 里 `pi.durable.*` 那一段哪些字段被记录。

### 设计之间的关系

这几条是一套，不是散点：

- 会话文件是事实来源（1、2、3）。内存里的 `Agent` 状态是活动分支的投影。
- 模型协议是窄边界（4、5、6、7）。宽出来的部分留在会话和 UI。
- 一轮工具调用是一个原子回合（9、10、11）。并行只发生在这个回合内部，对外仍是有序的 `toolResult` 列表。
- 能力尽量做成「少放进上下文」而不是「再加一个代理角色」（12、13、14）。截断、schema、codemode 都是在控制模型每一轮真正看见的字节。
- Durable / Chord 把 CLI 里「投影 + 内存循环」换成「提交 + 引用」（17、18）。读的时候用 CLI 的会话树去对照，不要把两套类型混成一套。

## 精读顺序

每轮读完再进下一轮。大文件按函数读，不要从头滑到尾。

### 第 0 轮：地图（1 小时）

1. 根 `README.md` 的 Packages 表。
2. `packages/coding-agent/docs/how-pi-works.md`（约 50 行，先读完）。
3. `packages/coding-agent/docs/message-types.md`、`session-format.md`、`compaction.md`。
4. 根 `package.json` 的 `workspaces` 和 `build`，确认依赖方向。

### 第 1 轮：把一条请求走通

按调用顺序，只追主路径：

| 顺序 | 文件 | 看什么 |
|---|---|---|
| 1 | `packages/coding-agent/src/cli.ts` | 两行：`setupCli` + `main` |
| 2 | `packages/coding-agent/src/main.ts` | `resolveAppMode`、`createAgentSessionRuntime`、模式分发。文件很长，其余分支先跳过 |
| 3 | `packages/coding-agent/src/core/sdk.ts` | `createAgentSession`、`CreateAgentSessionOptions`。工具白名单、`setDefaultStreamFn(streamSimple)` |
| 4 | `packages/coding-agent/src/core/agent-session-services.ts` | cwd 绑定的服务：`ModelRuntime`、`SettingsManager`、`ResourceLoader` |
| 5 | `packages/coding-agent/src/core/agent-session.ts` | 只读 `prompt`、`_startRun`、`_prepareRun`、`_handlePostAgentRun` |
| 6 | `packages/agent/src/types.ts` | `StreamFn`、`AgentEvent`、`AgentTool`、`QueueMode`、`ToolExecutionMode` |
| 7 | `packages/agent/src/agent-loop.ts` | `runLoop`、`declareToolChanges`、`executeToolCalls` |
| 8 | `packages/agent/src/agent.ts` | `prompt` / `steer` / `followUp` / `continue` 怎么喂给 loop |
| 9 | `packages/ai/src/types.ts` | `Model`、`Context`、`AssistantMessage`、`stopReason` |
| 10 | `packages/ai/src/models.ts` | `Models.stream` / `streamSimple` 如何选 API 和鉴权 |
| 11 | `packages/ai/src/api/lazy.ts` | 同步返回流，鉴权和动态 import 在流后面做 |
| 12 | 任选一个 API 实现 | 建议 `api/openai-responses.ts` 或 `api/anthropic-messages.ts`，看事件如何映射成统一流 |
| 13 | `packages/coding-agent/src/core/tools/read.ts` | 一个工具的模板：schema、`execute`、截断 |
| 14 | `packages/coding-agent/src/core/session-manager.ts` | `SessionHeader`、`SessionEntry`、追加与按 parent 重建分支 |

读完应能回答：用户文本在哪变成 `role: "user"`，工具参数在哪校验，结果在哪写回 JSONL。

### 第 2 轮：上下文怎么拼

模型看不到会话里的全部条目。活动分支要先变成 LLM 消息。

| 文件 | 作用 |
|---|---|
| `core/messages.ts` | 声明合并出 `bashExecution`、`custom`、`branchSummary`、`compactionSummary`。`convertToLlm` 把它们收成 `user` / `assistant` / `toolResult` |
| `core/system-prompt.ts` | 系统提示分段：基础说明、工具、skills 目录、AGENTS/上下文文件、cwd。分段进 `SystemMessage.sections` |
| `core/skills.ts` | skill 发现。正文默认不进每次请求，`/skill:name` 或模型读文件才展开 |
| `core/prompt-templates.ts` | `/template` 在变成 user 消息之前展开 |
| `core/compaction/` | `shouldCompact`、`findCutPoint`、`generateSummary`。摘要是新条目，旧条目留在树上 |
| `packages/agent/README.md` 的 Message Flow | `transformContext` → `convertToLlm` → `Message[]` |

`AgentMessage` 比 LLM 消息宽。只有 `user`、`assistant`、`toolResult`、`system` 能直接送进供应商；其余角色必须在 `convertToLlm` 里消掉。这是 agent-core 用声明合并留的扩展点（`CustomAgentMessages`）。

### 第 3 轮：工具、扩展、资源

| 文件 | 作用 |
|---|---|
| `core/tools/index.ts` | `createCodingTools` / `createReadOnlyTools`。名字：`read` `bash` `powershell` `edit` `write` `grep` `find` `ls` |
| `core/bash-executor.ts` | `!` 命令，和模型调用的 bash 工具不是同一条 UI 路径 |
| `core/tools/file-mutation-queue.ts` | 写文件串行，避免并行 edit/write 互相踩 |
| `core/extensions/types.ts` | 扩展能注册的全部事件和 `ExtensionAPI`。先读类型，再读实现 |
| `core/extensions/loader.ts` | 从设置、包、项目目录加载 |
| `core/extensions/runner.ts` | 按事件调用 handler |
| `core/resource-loader.ts` | skills、prompt templates、themes、扩展、上下文文件 |
| `extensions/index.ts` | 内置扩展：`llama.cpp`、`codemode`、`tool-search`、`mcp`。后三个 `replaceable`：同名注册会换掉内置，而不是叠着跑 |
| `core/project-trust.ts`、`core/trust-manager.ts` | 项目资源加载前的信任决定。信任不等于沙箱；工具权限等于启动 `pi` 的用户 |

扩展事件里和主循环最相关的：`input`、`before_agent_start`、`context`、`tool_call` / `tool_result`、`turn_end`、`agent_before_settle`。完整列表以 `extensions/types.ts` 为准，用户向说明在 `docs/extensions.md`。

### 第 4 轮：交互 UI

TUI 是独立库。先读库，再读 Pi 怎么用它。

| 文件 | 作用 |
|---|---|
| `packages/tui/src/tui.ts` | `TUI` 接口：组件、焦点、overlay、输入 |
| `packages/tui/src/tui-main-screen.ts` | 默认主屏渲染。差分更新，CSI 2026 同步输出 |
| `packages/tui/src/components/editor.ts` | 输入框 |
| `packages/tui/src/keybindings.ts` | 键匹配。业务代码禁止写死 `matchesKey(key, "ctrl+x")`，默认值放 keybinding 表 |
| `coding-agent/src/modes/interactive/interactive-mode.ts` | 把 `AgentSession` 事件画成组件。约五千行，按功能跳：启动、提交、渲染消息、slash、中断 |
| `modes/interactive/theme/theme.ts` | 主题加载 |

`interactive-mode.ts` 不要通读。从 `session.prompt` 的调用点往上找编辑器提交，往下找 `AgentSessionEvent` 的订阅。

### 第 5 轮：模型与鉴权

`packages/ai/src/index.ts` 故意没有副作用：不带全量目录、不带各家工厂。工厂在 `@earendil-works/pi-ai/providers/*`，旧的全局 API 在 `@earendil-works/pi-ai/compat`。coding-agent 仍从 `compat` 引 `streamSimple`。

| 路径 | 作用 |
|---|---|
| `ai/src/models.ts` | `Models`：注册 provider、`getModel`、`stream`、刷新动态目录 |
| `ai/src/auth/` | 环境变量、`auth.json`、OAuth 刷新。解析在 `auth/resolve.ts` |
| `ai/src/providers/*.ts` | 一家一个工厂。先读 `providers/openai.ts`、`providers/anthropic.ts` |
| `ai/src/api/*` | 一家线路一个实现。`*.lazy.ts` 只负责动态加载 |
| `ai/catalog/` | 目录生成。`generate.ts` 拉上游，写出 `providers/data/*.json`（gitignore）和 `models.generated.ts` |
| `ai/src/providers/faux.ts` | 测试用假 provider。`coding-agent/test/suite/` 走它，不打真 API |
| `coding-agent/src/core/model-runtime.ts` | CLI 侧的模型运行时：凭证、`models.json`、覆盖 |
| `coding-agent/src/core/model-registry.ts` | 扩展注册的自定义 provider / 模型 |

改模型数据：改 `packages/ai/catalog/`，再生成。不要手改 `packages/ai/src/models.generated.ts`。

`api` 字段决定走哪个实现，例如 `openai-responses`、`openai-completions`、`anthropic-messages`、`google-generative-ai`。OpenAI 兼容端点多数复用 completions 或 responses，差异在 provider 的 `compat`。

### 第 6 轮：另外三个可独立阅读的包

和主循环正交，用到再读。

**schema**（`packages/schema/src/`）

JSON Schema 构建和校验，无 `eval`。`builders.ts` 产生普通 schema 对象，`compiler.ts` / `validator.ts` 编成闭包。工具参数、Chord 契约都用它。入口：`index.ts`，先看 README 的 builder 表。

**mcp**（`packages/mcp/src/`）

不依赖官方 MCP SDK，也不依赖其它 pi 包。`client.ts` 是协议客户端，`transports/` 是 stdio、Streamable HTTP、内存传输，`oauth/` 是客户端 OAuth 子集。coding-agent 的包装在 `src/extensions/mcp/`。

**codemode**（`packages/codemode/src/`）

模型写的 JS 跑在 QuickJS WASM 里，能力只有注入的 `tools.*`。嵌套工具调用不进 LLM 上下文，只有脚本输出和返回值进去。`runtime/worker.ts` 是 Node/Bun 每执行一个 worker；`runtime/inline.ts` 在调用线程跑。coding-agent 的工具在 `src/extensions/codemode/`。

### 第 7 轮：Durable 和 Chord（另一条产品线）

确认 CLI 主路径已经能跟完再开始。

Durable 的概念在 `packages/durable/README.md` 前 100 行就够用：

- `Harness`：一份打开的存储，所有变更走一次原子提交，提交成功才对外可见。
- `Conversation`：不可变 entry 的转录。模型只看最近一次 reset 之后的 entry。
- `Task`：每步 checkpoint。内置 `pi.generation` 调模型，并拥有 `pi.tool` 子任务。
- 一次回答：`submit` → `pi.user` → `pi.generation` → `pi.assistant` → `pi.tool` → `pi.tool-result` → 下一轮 generation。

源码入口：

| 文件 | 作用 |
|---|---|
| `durable/src/harness/harness.ts` | `Harness.open`、`root`、`submit` |
| `durable/src/harness/generation.ts` | 模型轮次状态机 |
| `durable/src/harness/tool.ts` | 工具任务 |
| `durable/src/harness/compaction.ts` | 压缩任务 |
| `durable/src/entries.ts` | `pi.user`、`pi.assistant`、`pi.tool-result` 等 entry 种类 |
| `durable/src/session/session.ts` | 会话与提交 |
| `durable/src/storage/` | memory、jsonl、sqlite、indexeddb |
| `durable/src/tools/` | 和 CLI 类似的 read/edit/write/bash，但是 Durable 的 `ExecutionEnv` |
| `env/src/` | SSH 上跑 Rust daemon，工具在远端，凭证和存储留在本地 |

Chord 是 Durable 的宿主运行时：对象用引用访问，状态是可观察 JSON，热更新换代码、留下 state。已发布 API 在 `packages/chord/src/`（`contract.ts`、`host.ts`、`delta/`）。正在重写的 v2 在 `packages/chord/src/next/` 和 `src/next-node/`，计划在 `packages/chord/docs/next/slices.md` 和 `design.md`。v2 按垂直切片做，读某一片要连同 `design.md` 全文，不要只读切片 spec 里引用的小节。

`packages/telemetry` 是各包共用的 span / metric 契约，没有全局 current span，也没有导出器。`InMemoryTelemetryContext` 是参考实现。Durable 的 span 名（`pi.durable.submit`、`pi.durable.phase`）写在它的 README 里，实现在 `durable/src/harness/telemetry.ts`。

## 包内文件地图

下面是精读时的目录职责，不是文件清单。

### `packages/agent`（6 个源文件，整包可读）

```text
src/types.ts        类型契约
src/agent-loop.ts   循环。先读这个
src/agent.ts        有状态外壳：队列、订阅、abort
src/stream-fn.ts    默认 streamFn（coding-agent 在 sdk.ts 里换成 streamSimple）
src/proxy.ts        代理辅助
src/index.ts        再导出
```

事件顺序以 `packages/agent/README.md` 的 Event Flow 为准：`agent_start` → `turn_start` → `message_*` → 可选 `tool_execution_*` → `turn_end` → 可能再 `turn_start` → `agent_end`。`subscribe` 的监听器按注册顺序 await。`agent_end` 之后循环不再发事件，但 `prompt()` 要等 `agent_end` 的监听器跑完。

### `packages/coding-agent/src`

```text
cli.ts / main.ts / rpc-entry.ts     进程入口
cli/                                参数、登录命令、首次设置、会话选择
config.ts                           路径：~/.pi/agent、包内资源。禁止用 __dirname，走这里的 helper
core/sdk.ts                         编程入口 createAgentSession
core/agent-session.ts               会话生命周期。最大的文件
core/agent-session-runtime.ts       换 cwd / 换会话时拆掉并重建 runtime
core/session-manager.ts             JSONL 树
core/compaction/                    自动与手动压缩
core/tools/                         内置工具
core/extensions/                    加载、运行、类型
core/model-*.ts                     模型解析与运行时
core/auth-storage.ts                凭证文件
core/settings-manager.ts            设置层叠
core/resource-loader.ts             资源发现
core/system-prompt.ts               系统提示
core/messages.ts                    自定义消息和 convertToLlm
modes/interactive/                  TUI 应用
modes/print-mode.ts                 打一次就退出
modes/rpc/                          JSONL RPC
extensions/                         内置扩展实现
utils/                              路径、剪贴板、图片、git
```

SDK 对外导出集中在 `src/index.ts`。读公开 API 时从这里进，再跳到实现。

### `packages/ai/src`

```text
index.ts            无副作用核心
types.ts            消息、模型、流事件
models.ts           Models 集合
auth/               凭证解析
api/                各供应商流实现
providers/          工厂和模型表。data/ 是生成的 JSON，不进 git
catalog/            生成器，不在 src 里
utils/              溢出判断、重试、流式 JSON、校验
compat.ts           旧全局 API
```

流事件是统一的：`start`、`text_delta`、`thinking_delta`、`toolcall_delta`、`done`、`error`。工具参数在流里是残缺 JSON，`utils/streaming-json.ts` 和依赖 `partial-json` 负责补全。真正执行前 agent-loop 还会 `validateToolArguments`。

### `packages/tui/src`

组件在 `components/`。渲染器是 `tui-main-screen.ts` 和 `tui-alt-screen.ts`，都实现 `TUI`。原生按键修饰在 `native/`（darwin / linux / win32）。读交互模式卡在输入或重绘时再进来。

## 读的时候抓住的不变量

理由在「优秀设计」。这里只留检查清单，读到相关代码时用来核对：

1. **LLM 边界很窄。** 自定义角色留在会话里。`convertToLlm` 只在 `streamFn` 之前执行。见设计 4。
2. **工具声明和可执行工具是两套。** `declareToolChanges` 用差量把它们对齐。见设计 3。
3. **活动分支才是上下文。** 压缩追加摘要，不删历史。见设计 1、2。
4. **steer 和 followUp 插入点不同。** 流式时 `prompt` 必须显式选择。见设计 8。
5. **失败是 assistant 消息。** `error` / `aborted` 结束底层循环，重试和压缩在 `AgentSession`。见设计 5。
6. **并行工具的事件顺序和落盘顺序不同。** 完成顺序发事件，源顺序写 `toolResult`。见设计 9。
7. **项目信任不等于沙箱。** 工具权限等于进程权限。见设计 16。
8. **两套压缩不要混。** CLI 在 `core/compaction/`，Durable 是 `CompactionTask`。见设计 2 和 17。

## 第一遍不要读

| 路径 | 原因 |
|---|---|
| `packages/ai/src/models.generated.ts` | 生成物 |
| `packages/ai/src/providers/data/` | 生成的模型 JSON，且 gitignore |
| `packages/ai/src/providers/*.models.ts` | 每家模型表，需要时再查一家 |
| `packages/ai/src/auth/oauth/` 全部供应商 | 登录流程，和主循环无关 |
| `coding-agent/src/modes/interactive/components/easter-egg-3d.ts` | 彩蛋 |
| `coding-agent/src/core/export-html/` | 会话导出 HTML |
| `packages/chord/src/next/` | v2 重写。不读 Durable 就不要进 |
| `packages/evals/` | 评测套件，要真模型密钥 |
| `packages/*/test/` | 用来对照行为，不适合当第一遍主线 |

测试想看行为时，优先 `packages/agent/test` 和 `packages/coding-agent/test/suite/`（faux provider）。不要为了读源码跑 `npm test`：根测试包含有密钥才会激活的 e2e。

## 和文档的对照

读源码时这些页面够用，不必先通读 `docs/`：

| 问题 | 文档 |
|---|---|
| 产品级数据流 | `packages/coding-agent/docs/how-pi-works.md` |
| 会话文件 | `docs/session-format.md`、`docs/sessions.md` |
| 消息角色 | `docs/message-types.md` |
| 扩展 API | `docs/extensions.md` |
| SDK | `docs/sdk.md` |
| RPC | `docs/rpc.md` |
| 模型配置 | `docs/models.md`、`docs/providers.md` |
| 压缩 | `docs/compaction.md` |
| agent 循环细节 | `packages/agent/README.md` |
| Durable | `packages/durable/README.md` |
| Chord 已发布 API | `packages/chord/README.md`、`docs/guide.md` |
| Chord v2 | `packages/chord/docs/next/design.md`、`slices.md` |

## 建议的阅读笔记格式

每读完一个文件，用固定四行，避免笔记变成摘抄：

```text
文件：
职责（一句）：
入口函数：
它调用的下一跳：
我仍不确定的不变量：
```

第 1 轮结束后，用自己的话写出「从 `InteractiveMode` 到 `anthropic-messages` 再回到 `SessionManager.append`」的调用链。写不出来就回到 `agent-session.ts` 的 `_startRun`，不要进入 Chord。
