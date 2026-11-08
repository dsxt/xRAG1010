# 科大讯飞贼难Agent面经：意图识别、图库、向量库、Harness全拷打

> 根据面试截图整理。题干来自原帖，参考答案用于复习，不是候选人当时的口述。

## 面试感受

一面别背八股。意图识别和 RAG 分块要能说到成本和召回率，才像真正做过。二面的多 Agent 和 Codex，只讲概念、不讲沙箱、幂等和取消，会被晾着。三面拼架构，可以反驳面试官：图库不是万能的，亿级文档全量建图就是烧钱；自研还是 LangGraph，看团队能不能接住，不是越新越好。

能落地、能追责、能回滚，才叫 Agent，否则就是 demo。

## 一面

### 1. 自我介绍

**答题思路**

控制在一分半到两分钟，按「我是谁、做过什么、为什么匹配」讲，停在一个能往下挖的项目上。

1. 年限、主语言、系统是在线服务还是离线实验。Java 后端做 Agent，就说清你写的是编排、检索还是工具。
2. 只讲一个上过线的 Agent：用户是谁，一句话要变成查询、办理还是回答。你负责哪一层。
3. 补一个你能展开的取舍。有真实数字再说数字，没有就讲当时放弃了哪条方案，以及怎么看召回或成本。
4. 收到这个岗位：意图要稳，知识库要能追到来源，工具要能超时、幂等和取消。

开场不要把后面的技术题都答一遍。

### 2. 讲一个你做过的 AI Agent 项目，按意图识别、规划、工具调用、RAG、输出校验拆模块说清楚你负责哪里

**答题思路**

用自己的项目填这五块。没做的块直接说没做，不要把框架的默认能力说成自己的设计。

1. 意图识别：标签是闭集还是开放说法，规则、分类器和模型各管哪一段，误判后是追问、拒绝还是转人工。
2. 规划：步骤固定的走工作流，路径看中间结果的才进循环。说清谁有权决定下一步。
3. 工具调用：模型只产生调用。schema、鉴权、超时、重试、幂等在哪一层。写操作有没有确认。
4. RAG：文档从哪来，按什么边界分块，引用能不能回到条款或段落。
5. 输出校验：回答里的单号、金额、状态必须能在工具结果或引用里找到。对不上就改口或停。
6. 你的边界：五块里你写了哪几块，谁评审。准备一个你修过的故障，按现象、定位、改法、怎么验证讲。

### 3. 意图识别如果用规则、轻量分类模型、LLM 三级方案分别怎么做，给出线上误判率和延迟的权衡

**参考答案**

结论：三级是级联短路，不是三选一。规则接确定性说法，分类器扛头部流量，只有低置信和开放说法才进 LLM。

规则用关键词、正则和槽位模板，延迟在毫秒内。精确率高、召回低，同词多义会误判，例如「退款」既可能查进度，也可能发起退款。适合转人工、投诉这类必须命中的强触发。

轻量分类器用线性模型或小 Transformer，标签必须是闭集，延迟通常在几十毫秒这一档，具体看模型大小和是否上 GPU。误判集中在混淆类，用留出集看分意图的精确率和召回率，不要只看一个总准确率。高置信且槽位齐就直接路由，低置信升级。

LLM 能处理改写和一句话里的多件事，延迟到百毫秒甚至数秒，并按 token 计费。它会发明标签，所以输出必须落在已注册意图里，未知类要显式存在。

误判代价不对称。查询类可以再追问；操作类、审批类即使分类器置信度高，也不能因此执行。语音交互里意图阶段通常要压在百毫秒量级，LLM 不该打在每条热路径上。线上看分意图误判、升级率和这一档的延迟、成本。

容易漏的是把低置信硬分到最近的类。没有未知类，误判率会被算得过于好看。

### 4. 用户说「帮我看看那个退款事项」，上下文缺失、实体歧义，你的意图解析和槽位填充怎么落地，Java 侧怎么设计状态机

**参考答案**

结论：这句的意图是查询退款，槽位不齐。先消歧、补槽，再调只读工具。指代消解的结果写回焦点实体，下一轮才能接着用。

缺失的是哪笔订单、哪笔退款、看进度还是看原因。「那个」只绑定会话里的焦点。消歧顺序是本轮说清的实体、上一轮确认过的焦点、进行中的单据。候选取多个时，查询可以列出来让用户点；不能偷偷选「最近一笔」去执行。写操作在槽位确认前不调用。

槽位至少有意图、订单号、退款单号、要看的方面。缺关键槽位就停住，追问只问缺的，已确认的不重问。

Java 侧用显式状态和转移表：收集槽位、等待用户、可执行、工具执行中、等待确认、完成、失败、转人工。每次用户消息是一个事件，状态和槽位一起持久化，键是会话 id。同一会话加锁或进单线程队列，避免两轮消息交错改槽位。非法转移直接拒绝。追问是等待用户，不是失败。进程重启后从持久化状态恢复。

容易漏的是确认态里的短句。上一轮刚问「哪一笔」，用户说「昨天那个」，这是补槽，不是一条新意图。

### 5. RAG 全链路设计：文档解析、分块、向量化、写入、检索、重排、生成。长文档含表格、公式、图片时分块策略怎么定

**参考答案**

结论：先按版面结构切，再按 token 上限兜底。检索用小块，送给模型用父块或完整条款。表格、公式、图片单独成块，并挂回所在章节。

解析要分出标题、正文、表格、公式、图片，丢掉页眉页脚和目录页。表格保留单元格结构，公式留源码，图片保存图注和 OCR，多模态描述和原文分开存。扫描件、双栏要有解析质量分，质量差的重解析或进人工。

分块按标题层级。子块带标题路径、文档 id、父块 id、类型和权限。表格不从中间截断；超大表按表头加行组切，每块重复表头。公式和解释它的句子放在一起。正文重叠只加在句子边界，表格内部不做 token 重叠。一块单独拿出来会答错，就说明切小了。

向量化只对用于检索的子块做。写入幂等键用文档、分块和向量版本。检索走混合召回并下推过滤，重排后再把父块送去生成。生成要求引用分块 id。

容易漏的是全库一个 chunk 长度。合同、制度、表格要按文档类型配置，并用带正确条款 id 的评测集看引用，不看向量分数高不高。

### 6. 混合检索怎么做，BM25、向量、metadata filter 如何融合，RRF 和 Cross-Encoder 重排在什么环节介入，成本怎么控

**参考答案**

结论：过滤先于打分。BM25 和向量各自召回，用 RRF 融合名次，Cross-Encoder 只重排融合后的一小段候选。

metadata filter 包括租户、权限、文档类型和时间，要下推到两路检索里。先召回再在应用层过滤，topK 会被无权文档占满。编号、错误码、专有名词靠 BM25，口语改写靠向量。两路分数量纲不同，没有点击标注时不要手调加权。RRF 只加名次，一条文档的分数是各路 `1 / (k + rank)` 的和，没被某路召回就不加分。论文里常用的 k 是 60，上线以自己的评测为准。

各路先取一段候选，取并集做 RRF，再截断给交叉编码器。交叉编码器看的是查询和文档的配对，精度高，成本和候选数成正比，不能对全库跑。它位于融合之后、生成之前。

成本主要在重排和查询向量。精确编号已经命中时可以跳过重排。查询向量和热门查询的重排结果可以缓存，缓存键带知识库版本。候选数用评测收紧，不要靠把 topK 开得很大换召回。

容易漏的是权限不在 RRF 里解决。融合只决定候选集合，最终对不对要看生成是否引用了正确片段。

### 7. 向量数据库选型：Milvus、Qdrant、pgvector、Elasticsearch 向量能力、Redis 向量分别适合什么规模，Collection、Partition、Segment、索引类型怎么定

**参考答案**

结论：按数据量、过滤是否复杂、团队已经有什么存储来选。不同向量模型不放进同一个索引。

pgvector 适合已经在用 PostgreSQL、数据在百万到千万级、过滤要和业务条件一起做的场景。Elasticsearch 的向量能力适合本来就用它做全文的系统，BM25 和向量可以在一处融合；内存和分片上去之后，它不是亿级专用向量库的默认选项。Redis 向量适合语义缓存和规模不大的热数据，延迟低，内存贵，不适合当主知识库。Qdrant 的标量过滤和向量结合得紧，组件相对少，适合千万到数亿、过滤条件多的线上库。Milvus 适合亿级、要多种索引和多副本，并接受 proxy、查询节点、数据节点、元数据和对象存储这一套运维。

Collection 是同一套 schema 和距离度量。维度或模型变了就新建，不混查。Partition 用来缩小查询范围。租户很多时不要一租户一个分区，分区数量有上限；大租户可隔离，长尾用分区键或标量过滤。Segment 是物理段，写入中的段和已封存建完索引的段会影响可见性和查询扇出。

在线低延迟、内存够，用 HNSW。规模上去、要压缩或落盘，用 IVF 加量化或 DiskANN 这一类，召回和延迟必须用同一份标注集测。距离度量跟嵌入模型走，归一化之后内积和余弦一致，一个集合里不要混用。过滤很严时，先搜索再过滤会把真正有权限的结果滤空，要预过滤或按租户分区。

容易漏的是照抄默认的搜索参数。ef、nprobe 这类没有脱离数据的通用最优值。

### 8. Embedding 模型迭代后老向量不兼容，如何做双写迁移、回滚和增量重建，保证线上召回不抖

**参考答案**

结论：两个模型的向量空间不能放在同一次检索里比距离。新旧分集合存储，用读指针切流，回滚就是把指针拨回去。

每条向量带模型版本。新文档和更新文档双写两个版本。历史数据按文档 id 分批重建到新集合，幂等键是文档、分块和模型版本，可重跑。维度变了必须新集合。重建过程中旧集合继续接流量，未建完的文档不提前切过去。

切流前用同一批查询做影子检索，比 Recall 和人工抽检，用户仍看旧结果。达标后按租户灰度。一次请求钉死一个版本。查询向量缓存和检索缓存的键都带模型版本，避免旧向量命中新缓存。

回滚不覆盖旧数据。观察期内旧集合保留，开关回到旧版本即可。双写期间存储和写入延迟都会上去，容量要先留出来。重排模型如果吃的是旧候选的分布，要和新索引一起评。

容易漏的是只重建增量、忘了历史，或者同一问句的前半段用新向量、后半段用旧向量。召回抖动通常来自这种混用，不来自重建任务本身。

### 9. Agent 接 Spring Boot，Function Calling 和 MCP 怎么封装成工具注册中心，工具 Schema、鉴权、超时、重试、幂等怎么统一治理

**参考答案**

结论：本地 Function Calling 和 MCP 工具都收成同一种工具定义。模型只能提议调用，鉴权、超时、重试、幂等和限流走同一条执行链。

启动时收集本地工具和 MCP 的工具列表，归一成名字、描述、JSON Schema、是否有副作用、超时、重试策略和所需权限。给模型的 schema 从注册中心生成，并且只含当前用户有权看到的工具。名字保持稳定，不把 MCP 的临时前缀直接暴露出去。

执行前用 schema 校验类型、必填和枚举。用户身份来自服务端会话，不来自模型参数。超时是这次调用自己的截止时间，取消要传到下游；超时是明确失败，不是空成功。只对幂等读和标明可重试的错误做退避重试。写操作默认不重试，除非带幂等键。幂等键用会话和 tool call id，重复调用返回第一次的结果。限流按工具和租户配在这条链上，超限返回可解释错误，让模型改道或向用户说明。

Spring AI 里可以把这两类工具都接成 `ToolCallback` 再交给 `ChatClient`。注册中心的意义是策略不散落在每个工具方法里。

容易漏的是 MCP 的 schema 会变。要有版本，拒绝未知字段。工具描述里不写密钥和内网地址。鉴权失败返回固定错误码，不要把内部原因交回模型自由发挥。

### 10. ReAct 循环里模型反复调同一个工具、不更新计划，harness 层怎么检测死循环、做步数熔断、降级到人工

**参考答案**

结论：死循环看工具指纹和状态有没有变化。步数上限是最后一道熔断，熔断后把已有观察交给人，不再让模型继续编。

指纹是工具名加上规范化参数：键排序、去掉无关空白。同一指纹连续出现，或在一个窗口里超过次数，就判重复。同时看计划的结构化哈希和工具结果哈希。计划和观察都不变，就是空转。参数顺序不同但语义相同，也要算同一次调用。

全局有最大步数，单个工具有最大调用次数，配置放在 harness 里，不写死在提示词里。到达后停止循环，状态进入熔断再转人工。交给人的内容是原问题、已确认事实和失败工具。读工具空转与写工具重复副作用分开：写工具在第二次执行前就该被幂等键挡住。

熔断原因写进 trace。阈值用线上真实循环的步数分布来收，没有分布时先用保守上限，再按误熔断调整。

容易漏的是只数步数。步数用完可能只是慢，指纹重复才说明没有新信息。

### 11. Redis 在 Agent 里存 session、意图状态、语义缓存怎么设计，缓存穿透、击穿、雪崩在问答高并发下怎么防

**参考答案**

结论：三类数据分键、分过期策略。语义缓存只缓存无副作用的查询，并且键里带租户、权限范围和知识库版本。

会话状态整包保存，键是会话 id，内容是状态机状态、焦点实体和窗口指针。新消息到来时续期，过期时间按会话空闲来定。同一会话加锁再改，避免并发消息互相覆盖。意图里的写操作确认态要能从数据库恢复，Redis 丢失后不能只剩内存。长文不塞进一个大键，库里存对象存储的地址。

语义缓存的键不用原始问句硬匹配，可以用规范化问题，或用向量相似度命中相似问。值里是答案、引用和知识库版本。版本变了整组失效。个性化答案不进全局缓存。相似度阈值用评测集定，不要拍一个固定分数。

穿透是库里也没有的数据被反复回源。订单号这类结构化键先做校验，并用允许误判的结构挡掉明显不存在的键；开放问句对空召回做短过期缓存，同时限制单用户回源。击穿是热点键过期瞬间被打穿，用互斥重建，或在值里放逻辑过期、先返回旧值再异步刷新。雪崩是大量键一起过期或 Redis 不可用。过期时间加抖动，模型调用另有并发上限，缓存大面积失效时排队或返回降级说明。

容易漏的是语义缓存绕过权限。键里没有权限范围，一个用户的答案会被另一个用户命中。写操作的结果也不能当语义缓存复用。

### 12. Kafka 在 Agent 异步工具回调、审计埋点、评测样本回流里怎么用，重复消费和顺序消费如何保证幂等

**参考答案**

结论：三条链路分 topic。Kafka 负责投递，业务用事件 id 做到重复到达只生效一次。顺序只保证同一个任务键的分区内顺序。

工具回调用任务 id 做消息键，编排服务消费后推进状态机。审计单独成 topic，失败重试不能挡住用户主链路，消费端按事件时间整理。评测回流用采样后的轨迹，去掉证件号和密钥，离线建评测集。

生产端的幂等生产者只能去掉生产侧重试造成的重复。消费端仍会至少一次投递。处理完再提交位点，崩溃会再投，所以处理本身要幂等：以事件 id 或任务 id 加状态做唯一键，相同结果重复到达不推进第二次。去重记录要有过期时间。

同一任务的调用和结果使用同一键，分区内有序。消费者并发时按键串行。不同会话不需要全局有序。审计不要求和回调严格同序。Kafka 事务能把「消费并再写入 Kafka」收得更紧，工具调用这种外部副作用仍然要靠业务幂等。

容易漏的是回调堆积。用户会一直停在工具执行中，所以状态机要有超时兜底，不能只等 topic。

### 13. AI 工作流和自主 Agent 边界怎么划，什么场景必须用固定 DAG，什么场景放模型自主决策

**参考答案**

结论：路径、权限和失败处理能事先写清的，用固定 DAG。目标清楚、下一步取决于中间观察的，才让模型在白名单里选。

退款办理、审批、按固定数据源出报表、开户，步骤和责任人事先就有。模型只填槽位，不决定调用顺序。失败可以重试到具体节点，也可以停在人工节点。

开放排障、跨系统查问题、需要根据上一步决定要不要再查的研究式问答，可以用受限循环。工具白名单、步数、预算和写权限仍由 harness 控制。

中间地带是 DAG 的某个节点内部做一次小循环，例如知识检索节点里允许改写和有限次多跳，出了节点必须回到流程图。写操作、花钱、对外发通知默认进 DAG 或人工节点。

容易漏的是把自主当成更高级的形态。选择标准是失败代价和路径能不能枚举。

### 14. 手撕算法：实现按句子边界的文本分块函数，给定长文本、最大 token 近似长度和重叠长度，尽量避免截断句子，返回分块列表

**参考答案**

结论：先按句界切开，再把整句装进预算。装不下就封块，下一块从上一块尾部回退一段重叠。只有单句本身超过预算时才硬切。

近似刻度要先说清：非 ASCII 记 1 个 token，ASCII 每 4 个字符记 1 个。实现用 4 倍整数单位，避免小数。英文句号只在后面是空白或结尾时切开，两侧都是数字的小数点除外。句末空白留在前一句，重叠为 0 时按顺序拼回原文。缩写后面如果有空格，仍可能被当成句号，生产环境应换断句模型。

硬切和正常封块都保证起点至少前进一个片段，重叠再大也不会死循环。每个起点只前进一次，字符被重复拷贝的次数随重叠增加，重叠有上限时接近线性。空间是 O(n)。

```java
import java.util.ArrayList;
import java.util.List;

public final class SentenceChunker {
    private SentenceChunker() {
    }

    public static List<String> chunk(String text, int maxTokens, int overlapTokens) {
        if (text == null || text.isBlank()) {
            return List.of();
        }
        if (maxTokens <= 0 || overlapTokens < 0 || overlapTokens >= maxTokens) {
            throw new IllegalArgumentException("需要 maxTokens > overlapTokens >= 0");
        }
        int maxUnits = maxTokens * 4;
        int overlapUnits = overlapTokens * 4;
        List<String> pieces = new ArrayList<>();
        for (String sentence : splitSentences(text)) {
            if (unitCount(sentence) <= maxUnits) {
                pieces.add(sentence);
            } else {
                pieces.addAll(splitOversized(sentence, maxUnits, overlapUnits));
            }
        }
        int[] weight = new int[pieces.size()];
        for (int i = 0; i < pieces.size(); i++) {
            weight[i] = unitCount(pieces.get(i));
        }
        List<String> chunks = new ArrayList<>();
        int start = 0;
        while (start < pieces.size()) {
            int end = start;
            int sum = 0;
            while (end < pieces.size() && (sum == 0 || sum + weight[end] <= maxUnits)) {
                sum += weight[end];
                end++;
            }
            chunks.add(join(pieces, start, end));
            if (end >= pieces.size()) {
                break;
            }
            int next = end;
            int overlap = 0;
            while (next - 1 > start && overlap + weight[next - 1] <= overlapUnits) {
                next--;
                overlap += weight[next];
            }
            start = next;
        }
        return chunks;
    }

    static List<String> splitSentences(String text) {
        List<String> out = new ArrayList<>();
        StringBuilder cur = new StringBuilder();
        int i = 0;
        while (i < text.length()) {
            int cp = text.codePointAt(i);
            int next = i + Character.charCount(cp);
            cur.appendCodePoint(cp);
            if (isBoundary(text, i, cp, next)) {
                i = next;
                while (i < text.length()) {
                    int follow = text.codePointAt(i);
                    if (!Character.isWhitespace(follow)) {
                        break;
                    }
                    cur.appendCodePoint(follow);
                    i += Character.charCount(follow);
                }
                push(out, cur);
                continue;
            }
            i = next;
        }
        push(out, cur);
        return out;
    }

    static void push(List<String> out, StringBuilder cur) {
        if (!cur.isEmpty() && !cur.toString().isBlank()) {
            out.add(cur.toString());
        }
        cur.setLength(0);
    }

    static boolean isBoundary(String text, int index, int cp, int nextIndex) {
        if (cp == '\n' || cp == '。' || cp == '！' || cp == '？' || cp == '!' || cp == '?') {
            return true;
        }
        if (cp != '.') {
            return false;
        }
        int prev = index > 0 ? text.codePointBefore(index) : -1;
        int nextCp = nextIndex < text.length() ? text.codePointAt(nextIndex) : -1;
        if (Character.isDigit(prev) && Character.isDigit(nextCp)) {
            return false;
        }
        return nextCp == -1 || Character.isWhitespace(nextCp);
    }

    static List<String> splitOversized(String sentence, int maxUnits, int overlapUnits) {
        int[] cps = sentence.codePoints().toArray();
        int[] units = new int[cps.length];
        for (int i = 0; i < cps.length; i++) {
            units[i] = cps[i] <= 0x7F ? 1 : 4;
        }
        List<String> parts = new ArrayList<>();
        int start = 0;
        while (start < cps.length) {
            int end = start;
            int sum = 0;
            while (end < cps.length && (sum == 0 || sum + units[end] <= maxUnits)) {
                sum += units[end];
                end++;
            }
            parts.add(new String(cps, start, end - start));
            if (end >= cps.length) {
                break;
            }
            int next = end;
            int overlap = 0;
            while (next - 1 > start && overlap + units[next - 1] <= overlapUnits) {
                next--;
                overlap += units[next];
            }
            start = next;
        }
        return parts;
    }

    static int unitCount(String text) {
        int units = 0;
        for (int i = 0; i < text.length(); ) {
            int cp = text.codePointAt(i);
            units += cp <= 0x7F ? 1 : 4;
            i += Character.charCount(cp);
        }
        return units;
    }

    static String join(List<String> parts, int start, int end) {
        StringBuilder sb = new StringBuilder();
        for (int i = start; i < end; i++) {
            sb.append(parts.get(i));
        }
        return sb.toString();
    }
}
```

## 二面

### 1. 自我介绍

**答题思路**

这一轮面试官要往下拆，开场比一面更短，把钩子放在你真正写过的那一层。

1. 用两三句重说定位，不要复述一面的完整项目故事。
2. 点明你负责的是编排、状态、工具还是观测。准备一张你能画出来的请求路径。
3. 主动留一个工程问题：多用户推理怎么隔离，或者一次工具失败后状态停在哪里。
4. 被问到没做过的层，说了解原理、线上没落地。

### 2. 深拆你的 Agent 项目：编排层、状态层、工具层、观测层各自职责，Java 服务里如何用线程池和虚拟线程隔离多用户推理任务

**答题思路**

按四层各说职责，再讲并发隔离。数字和线程池参数只用你压测过的；没压过就讲取数方法。

1. 编排层决定下一步：工作流边，或受限循环。它不计算业务规则，不握有数据库密码。
2. 状态层保存会话、槽位、计划、工具结果和检查点。说清重启之后从哪恢复，写操作的幂等键存在哪。
3. 工具层做 schema、鉴权、超时和真实调用。模型的输出到这里才变成请求。
4. 观测层把一次请求的模型、检索、工具和最终引用串成 trace，能按会话回放。
5. 并发：等待模型和工具的阻塞 IO 用虚拟线程，每个下游用信号量限制在途数。本地嵌入、重排、大 JSON 解析用靠近核数的平台线程池，和 IO 分开。Java 21 到 23 里，`synchronized` 会钉住虚拟线程的载体线程，阻塞调用不要包在里面；Java 24 起这个钉住已经去掉。不要把虚拟线程再放进一个很大的池子，限制应加在配额上，而不是加在线程个数上。
6. 准备一个真实故障：哪个用户的任务把下游打满，你怎么隔离，怎么确认其他用户恢复。

### 3. 多 Agent 编排讲 Supervisor-Worker、Pipeline、黑板制、消息总线四种，给出在工单分流、知识检索、代码修改、报表生成里的选型依据

**参考答案**

结论：先看路径是否固定、谁有权做最后决定、任务是同步还是长耗时。四种可以嵌套，对外仍是一个入口。

Supervisor-Worker 由主管拆任务、派工、收结果。Worker 权限不同，不能互相直接调用写接口。Pipeline 是固定顺序，每步有输入输出契约，适合失败后重试某一个节点。黑板是共享的部分解，谁有发现谁写上，顺序不固定，必须有人判断信息够不够、写入冲突怎么处理。消息总线用订阅解耦，天然异步，重复投递和「谁拍板」要单独规定。

工单分流用主管加工人：主管做意图和优先级，查询、办理、审批是不同工人。知识检索若只是改写、召回、生成，用 Pipeline；多路证据要来回补充时用黑板，主管仍负责停止。代码修改里编译、测试、扫描顺序固定就用 Pipeline，检查要并行就用总线，合并者只能有一个。报表生成用 Pipeline，模型只负责最后的叙述，不改取数顺序。

容易漏的是写操作的审批。无论哪种编排，审批都不能变成「几个 Agent 讨论后自己执行」。

### 4. 意图路由层设计：查询类、操作类、审批类、闲聊类、未知类如何分发；强意图和弱意图冲突时怎么用历史会话消歧

**参考答案**

结论：五类都是正式出口。未知类不能被最近的业务类吞掉。历史会话优先用来补槽，不能把查询升级成操作。

查询类走只读工具或 RAG，可以自动执行。操作类要槽位齐全、用户确认和幂等键。审批类只创建审批单，人不批就不执行。闲聊类不调用业务工具，避免顺口提到的业务词触发接口。未知类澄清或转人工。

路由先出标签和置信度。强意图是明确的动词加对象，或高置信且槽位齐；弱意图是闲聊里夹着业务词。两者冲突时保留强意图，弱意图最多变成回复里的一句，不触发工具。一句里有多件事，先做查询，操作另走确认。

历史的用法有两条。上一轮如果停在补槽或等待确认，本轮短句优先当成补槽。焦点实体只能填已经确定的意图，不能因为上文在查退款，就把「帮我处理一下」直接变成退款操作。

容易漏的是确认态。用户回一个「好的」，要结合上一轮是在问槽位还是在请求确认，不能一律当成新意图。

### 5. Agentic RAG 对比被动 RAG：查询改写、子问题分解、多跳检索、自我反思在哪一层做；检索结果和工具实时数据冲突以谁为准，给出可审计方案

**参考答案**

结论：被动 RAG 是一次检索、一次生成。Agentic RAG 把改写、分解、多跳和反思放进有次数上限的循环。权威顺序是工具实时数据、知识库文档、模型记忆。

查询改写在检索之前，用历史补全省略。原话和改写都留下。子问题分解在规划层，把复杂问题拆成可以单独检索的子问。多跳在检索层，上一跳得到的实体作为下一跳的过滤条件，不把整段答案当成下一次查询。自我反思在生成前或生成后，检查引用是否覆盖子问题、有没有空召回。反思通常只给一次，否则会变成另一个死循环。反思不能改写工具返回值。

冲突时，订单状态、库存、权限以工具实时结果为准；制度和说明以版本匹配的文档为准；同为文档时，结构化字段优先于散文。每次回答绑定检索集合和工具调用：分块 id、文档版本、调用 id、来源系统和决定采用哪一边的原因。最终回答引用的 id 必须出现在这次绑定里，评测可以回放这条决定。

容易漏的是审计只存改写后的查询。用户原话对不上，就无法追责。

### 6. 图数据库增强 RAG：Neo4j 存储实体关系，向量库负责语义入口，复杂多跳如某供应商关联哪些合同和合规风险如何遍历；图库和向量库一致性怎么维护

**参考答案**

结论：向量库负责从一句话找到入口实体，图负责沿显式关系走。模型只组织语言，不发明关系，也不直接写 Cypher。

「某供应商关联哪些合同和合规风险」分三步。先用向量或关键词把供应商名落到节点 id。再跑预先写好的遍历模板，例如供应商到合同、合同到风险，带上租户和权限，并限制深度、度数、条数和超时。高扇出节点要按风险等级过滤并分页，不能做无界扩展。返回路径上的属性和来源分块。每一跳都检查目标节点的权限，不能因为入口可见就把关联合同全部返回。

文档版本是源头。同一次发布同时写向量和图谱，边能回到分块 id。新版本写完再切换可读版本；失败就继续读旧版本。对账找悬挂边和指向旧文档的边。查询使用参数化模板，用户输入不拼进查询字符串。

容易漏的是只更新了向量、没更新图。现象是搜得到实体，关系仍是旧的。正文长文档留在向量库，图里只放需要多跳的实体关系。

### 7. 知识图谱构建成本很高，实体抽取和关系抽取用 LLM 出错怎么纠，增量三元组如何做去重、版本化和回滚

**参考答案**

结论：LLM 只产出候选三元组。入库前做闭集校验、来源定位和去重，线上查询读的是已发布版本。回滚是切换读版本，不是在图上直接删改。

实体类型和关系类型用闭集，非法关系丢弃。主体、日期、金额要能链到源文档的片段，链不上就不进线上图。合规、合同主体这类关键关系，低置信度进人工审核；未审核的标成候选，不参与线上遍历。抽错后的修正写新版本，旧三元组和审核记录保留。

实体去重优先用外部 id，例如统一社会信用代码、合同号。只有名称相似时，合并要高阈值或人工，避免把两个供应商并成一个。关系的唯一键是主体、关系、客体和来源文档。同一事实来自多份文档，就增加来源，不复制边。

增量批次带批次 id。节点和边带生效版本，查询默认带当前发布版本。回滚把发布指针退回上一版，粒度到批次或文档。向量库里对应的文档版本一起退，避免图文错版。

容易漏的是自动合并过于积极。实体并错比漏抽更难恢复。

### 8. MySQL 存对话、意图日志、工具调用明细，按租户和用户维度如何分库分表；热点会话和长文本摘要分别怎么落库

**参考答案**

结论：分片键按在线查询路径定。会话消息和工具明细用会话 id，用户的会话列表另建按用户 id 分片的索引表。大租户单独库。

租户是第一维。小租户按租户取模分库，大租户独立库，避免一个大客户打满一个分片。消息详情的访问都带会话 id，用它做分片键，一次定位。按用户列会话则查用户维索引，再按会话 id 取详情。工具调用明细和消息放同一分片，避免为一条会话做分布式事务。意图日志量大，在分片之上按时间分区，过期直接丢分区。跨租户的运营分析走汇总库或数仓，不在在线分片上扇出。

热点来自巨型租户，不来自把一行拆开。进行中的状态放 Redis，MySQL 异步追加消息。超长会话只在消息表保留近期窗口和历史指针。

长文本原文放对象存储，MySQL 存地址、哈希、token 数、摘要和这份摘要覆盖到哪条消息。当前摘要一行一会话，历史摘要另表。消息行里不放大段全文。

容易漏的是分片键以后改不动。如果在线查询总是按时间扫全租户，这个需求不该走这套分片。

### 9. RocketMQ 延迟消息做 Agent 定时兜底、超时任务重投，和 Redis 过期回调、调度平台相比在 Exactly-Once 和失败重试上有什么坑

**参考答案**

结论：三者都不能把外部副作用做成端到端恰好一次。能做到的是至少一次，加上幂等和条件更新，让重复投递只生效一次。

RocketMQ 4.x 的延迟消息是固定延迟级别，不是任意时刻的闹钟；5.x 的定时消息可以指定投递时间。它适合每个任务一个超时通知。消费仍可能重复，事务消息解决的是本地事务和发消息的一致，不是消费恰好一次。消费者按任务 id 判断：已经完成或已经取消就丢掉兜底消息；仍在执行才用条件更新抢一次重投或转人工。失败重试要有上限和死信，坏任务不能无限打到模型。

Redis 键过期通知不持久，主从切换和客户端离线都会丢，过期时间也不精确。它适合本地缓存失效，不适合作为超时任务的唯一触发器。

调度平台适合周期扫描数据库里的超时任务。可靠性来自任务表，精度取决于扫描间隔。多实例要用抢占或乐观锁，避免同一任务被扫出多次。大批量对账用扫描，单任务闹钟用延迟消息。

时钟和完成写库会竞态。兜底到达时用「状态仍是执行中」作为更新条件，不能只看消息到了就重跑。用户已经取消的请求，延迟消息到了也不能再跑。

容易漏的是把 Exactly-Once 理解成某个开关。重复生效一次，靠的是幂等键和条件更新。

### 10. Codex 实战：让 Codex 改一个 Spring Boot 接口并跑 JUnit，harness 怎么给沙箱、如何把编译错误和测试失败回灌模型、如何禁止它改测试凑通过

**参考答案**

结论：模型只改允许的源码路径。编译和测试由 harness 在沙箱里跑，通过与否以原始测试为准。

沙箱使用独立的 git worktree，不碰开发者当前未提交的工作区。网络默认关闭，依赖从已有的本地仓库或只读缓存解析。进程用受限用户，CPU、内存和时长有上限。Maven 命令由 harness 执行，模型不能自己拼参数跳过测试。构建脚本和 `pom` 默认只读，除非这次任务明确是改构建。

回灌时跑规定的测试命令，收集编译错误和失败断言，截断到固定长度，带文件名和行号写回对话。补丁先校验再应用，应用失败也是一种观察。不把整个构建日志塞进上下文。

测试目录不在写路径里，补丁碰到测试就拒绝。放宽断言、加上忽略注解、删测试，用补丁规则拦截。若任务本身是生成测试，生产代码反过来只读，两种写权限不同时打开。最终由 harness 在干净沙箱重跑原始测试，不采信模型说的「已通过」。

容易漏的是模型改构建配置来跳过测试。只锁测试目录、不锁构建脚本，仍然能凑通过。

### 11. AI 编码 Agent 安全：密钥不进 prompt、不进 diff、不进日志，Maven 私服凭证、数据库密码、云 AK 如何通过 vault 和最小权限注入；full-auto 和 suggest 模式在 Java 大仓怎么选

**参考答案**

结论：密钥只在运行时从 vault 注入进程环境。prompt、diff 和日志里出现的密钥都按已经泄露处理。

收集上下文时排除环境文件、凭证文件和已知密钥格式，文件名命中也不把内容送给模型。提交前扫描 diff，有密钥就阻断。工具输出和命令行用同一套脱敏，Maven 的密码参数不能出现在 trace 里。崩溃日志也要走脱敏，环境变量打进日志同样会泄密。

Maven 私服的 settings 放在沙箱外，由 harness 只读提供。凭证运行时领取，任务结束销毁，模型看不到这份文件。数据库使用任务级临时账号，权限只够这次测试，连接串用占位符加环境变量，不连生产库。云访问使用短期凭证，限定资源范围，任务结束即废，不用长期 AK。

suggest 模式下改文件和执行命令前要人确认。full-auto 只在隔离工作区、网络关闭、测试和构建脚本只读、没有生产凭证时，用于低风险修改。Java 大仓默认 suggest。权限、SQL、鉴权和依赖升级都要人点头后再应用。full-auto 一次改太多模块，评审和回滚都差。

容易漏的是「最小权限」只说成一个开发者令牌。它要具体到这次任务能读哪些模块、能访问哪个库。

### 12. SSE 流式输出时用户点停止，Java 侧如何真正取消模型推理和工具调用，而不是只断前端；断线重连如何基于 event id 补事件

**参考答案**

结论：用户停止是一条带 run id 的取消请求，服务端据此断开上游推理和工具调用。网络断开只说明连接没了，任务继续，并用事件 id 补发。

浏览器断开、刷新和用户点停止都会让连接结束，所以不能把连接关闭一律当成取消。取消令牌贯穿这次运行。读模型流时每读一块都检查它，并通过可取消的 HTTP 调用关掉上游，例如取消 `HttpClient` 的 `sendAsync`，或调用 OkHttp 的 `Call.cancel()`。只停止往前端写，上游仍在跑，费用和副作用都还在。工具调用在发出前检查令牌；已经发出的 HTTP 同样取消。已经成功的写操作不能靠取消撤回，要走补偿，或明确告诉用户停的是后续步骤。状态进入已取消后，延迟兜底看到这个状态不再继续。

每个 SSE 事件有按 run 递增的 id，先写入可回放日志，再写到连接。客户端重连带最后收到的 id，服务端从其后补发，补完再接实时流。补发和实时共用同一条顺序日志。客户端按 id 去重。运行已经结束时，补完历史后发送结束事件。

容易漏的是先写前端、后记日志。断在这两步之间，重连会缺事件。

### 13. 手撕算法：用 Trie 实现意图路由，输入用户语句返回最长前缀匹配意图名和剩余槽位串，支持多意图优先级和兜底

**参考答案**

结论：Trie 存的是字面前缀，不是语义相似。匹配长度优先，同一长度再用优先级，没有任何结束节点就走兜底。

插入时沿短语建树，短语结束的节点挂上意图名和优先级。同一个节点可以挂多个意图，例如「退款」上同时有低优先级和高优先级的两个意图。查询从句首走，记录所有到达过的结束节点。更长的结束位置替换当前结果；长度相同，优先级更高才替换。同长度同优先级保留先插入的。剩余槽位串是前缀之后的内容，去掉两端空白。

因此「退款进度很快」会命中更长的「退款进度」，即使「退款」的优先级更高。没有匹配时，兜底意图的剩余串是整句。时间是短语长度，空间是全部短语的字符数。实现按 UTF-16 码元走，中文和 ASCII 足够；增补字符要改成码点。

```java
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public final class IntentTrie {
    static final class Intent {
        final String name;
        final int priority;

        Intent(String name, int priority) {
            this.name = name;
            this.priority = priority;
        }
    }

    public static final class Match {
        public final String intent;
        public final String rest;

        Match(String intent, String rest) {
            this.intent = intent;
            this.rest = rest;
        }
    }

    static final class Node {
        final Map<Character, Node> next = new HashMap<>();
        final List<Intent> intents = new ArrayList<>();
    }

    private final Node root = new Node();
    private final String fallback;

    public IntentTrie(String fallback) {
        this.fallback = fallback;
    }

    public void add(String phrase, String intent, int priority) {
        Node cur = root;
        for (int i = 0; i < phrase.length(); i++) {
            cur = cur.next.computeIfAbsent(phrase.charAt(i), key -> new Node());
        }
        cur.intents.add(new Intent(intent, priority));
    }

    public Match route(String text) {
        if (text == null) {
            return new Match(fallback, "");
        }
        Node cur = root;
        int bestEnd = -1;
        Intent best = null;
        for (int i = 0; i < text.length(); i++) {
            cur = cur.next.get(text.charAt(i));
            if (cur == null) {
                break;
            }
            for (Intent intent : cur.intents) {
                int end = i + 1;
                if (best == null || end > bestEnd
                        || (end == bestEnd && intent.priority > best.priority)) {
                    best = intent;
                    bestEnd = end;
                }
            }
        }
        if (best == null) {
            return new Match(fallback, text.strip());
        }
        return new Match(best.name, text.substring(bestEnd).strip());
    }
}
```

## 三面

### 1. 自我介绍

**答题思路**

三面看的是架构判断和协作，不再把项目功能讲一遍。

1. 用一两句说明你现在对哪条链路负责，以及这条链路的约束是权限、成本还是可回滚。
2. 讲一个你做过的取舍：什么能力没上，为什么。例如全量建图、自主写操作，或换框架。
3. 补一句你怎么和算法、数据、评测对齐：接口、版本和故障按哪一层分。
4. 收在一个你准备展开的架构问题上，方便面试官反驳。反驳来了就讲证据和回滚，不把立场说成框架信仰。

### 2. 从零设计科大讯飞级别 AI Agent 平台：意图识别、RAG、向量库、图库、多 Agent、工作流、harness、评测、运营监控整体架构，画出核心数据和消息流

**参考答案**

结论：控制面和数据面分开。模型只在 harness 里提议动作，权限、状态和发布版本都在自己的系统里。先把查询做稳，写操作进工作流。

同步数据流是：文本或语音转写进入会话状态，带上租户、用户和转写置信度；意图层用规则、分类器和低置信才调用的模型，输出闭集意图和槽位；固定流程进工作流，需要根据观察选下一步的进受限循环。需要知识时，检索服务按权限查向量库，只有明确的多跳才查图。工具一律经过网关。生成后做输出校验，再由 SSE 返回。

核心数据有四类。会话和检查点保存状态、计划和工具结果。证据保存分块 id 和文档版本。工具记录保存调用 id、参数哈希和结果。发布指针把模型、提示词、索引和图谱绑成一组，一次切换。

异步消息不走用户的同步链路。超时兜底、索引重建、图谱增量和评测回流进消息队列。运营监控看工具成功率、空召回、熔断、人工接管和 P95。评测既有离线集，也吃线上抽样。

图库只接关系清楚的实体，不全量入图。多 Agent 的主管在编排层，工人不共享生产写权限。

容易漏的是 trace 上没有版本号。线上一次答错，就分不清是模型、索引还是图谱的问题。

### 3. 高难意图场景：语音转写带错别字、方言、省略主语、跨轮指代，如何结合 ASR 置信度、用户画像、业务上下文做联合消歧和兜底

**参考答案**

结论：转写置信度低、主语缺失或指代有多个候选时，先澄清或只做只读候选。写操作使用更高的门槛。

ASR 保留 n-best 和词级置信度，不只用第一名文本。产品名、地名用热词放在识别侧。低置信片段不能作为槽位的唯一依据。错别字对订单号用校验位，对名称用业务别名词典。模型可以给出规范化文本，原文和规范化结果一起留。方言优先在识别模型侧处理，对话模型不再同时猜方言和业务。

省略主语从焦点实体补。没有焦点就追问。「那个」绑定上一轮提到的实体；候选多个，或识别置信度低，就列出候选让用户确认。

联合分数看转写置信度、意图置信度和槽位是否齐全。低于门槛走澄清、只读候选或人工。兜底也记日志，用来补热词和标注。回流训练只用高置信且人对过的样本。

容易漏的是把识别错误当成用户原话去训练分类器。错别字会被学成一种「正常说法」。

### 4. 超大规模企业知识库 RAG：亿级文档、多租户、权限随组织架构变更，向量检索、图遍历、行列权限、临时授权如何一起生效且不拖慢 P95

**参考答案**

结论：权限在检索和图遍历时下推。向量上存文档和组，不存每个用户的名单。组织变更改的是人与组的关系，不是重刷亿级向量。

大租户使用独立的集合或分区，查询必须带租户。文档 ACL 变更才更新受影响文档的组字段。行权限是组命中；列权限在检索服务里裁剪，敏感列不进 prompt，不靠模型自觉。临时授权是一张小的短期表，条件是组命中或未过期授权。授权过期用时间条件，不做全库回刷。权限缓存的键包含用户组，调岗后失效。

向量过滤选择很严时，分区内再做近邻搜索，避免先搜索再滤空。图从有权限的入口出发，扩展时检查目标节点，并限制深度、度数和超时。无权限节点不返回内容。图只建需要多跳的关系，正文留在向量库。图查询超时就降级为向量检索，不把主链路拖死。结果缓存的键包含用户组和查询，避免串权限。

P95 的预算花在一次下推过滤加有限候选上。全图遍历和每次调岗重刷向量，都会直接打穿这个预算。

容易漏的是临时授权没进缓存键。授权收回后，旧结果仍会被命中。

### 5. 多 Agent 自治边界：什么时候允许子 Agent 直接调生产工具，什么时候必须人工审批；错误在多个 Agent 间放大如何做 critic、citation 约束和回滚

**参考答案**

结论：子 Agent 默认只能读和提议。生产写、付款、对外发送、删除和改权限必须人工批准后再由网关执行一次。

可以直接调用的是只读、幂等、网关已经鉴权、结果可以复算的工具。需要审批的，子 Agent 提交参数和理由，主管或人批准后执行。批准前工具对模型不可执行。

错误放大靠三道约束。下游只能引用上游给出的证据 id，不能把上游的推测当成事实再传播。critic 是另一个只读检查者，只看证据和提案，检查结论是否被证据支持、工具是否越权、数字是否来自工具结果。它不和执行者共用一份会自我认同的草稿，自己也不执行写操作。没有来源的句子不能触发下一个 Agent 的写操作。

一次用户目标对应一个 saga。每个自动写操作事先登记补偿；没有补偿的步骤不能自动执行。中间失败就停止后续 Agent，执行已登记的补偿。主管的扇出和步数有上限。

容易漏的是 critic 只是同一个 Agent 再想一遍。上下文相同，它会沿着原错误继续写。

### 6. Harness 工程深度：agent loop、context compaction、tool registry、hook、approval、sandbox、cancel token、checkpoint 怎么设计成模型无关；换模型不改业务代码怎么做

**参考答案**

结论：业务只依赖 harness 的状态和动作。厂商协议放在适配器里，换模型改适配器和配置，不改工具与流程。

循环的输入是状态，输出是统一动作：调用工具、询问用户或结束。不同模型的函数调用或文本格式都由适配器收成这一种动作。上下文压缩由 harness 按策略摘要旧步骤，保留系统规则、当前计划、未完成的工具结果和引用。压缩产物的格式固定，逻辑不写进业务工具。

工具注册表保存名字、schema、权限、超时和幂等。hook 用代码注册在检索前、工具前后和回复前，负责脱敏、审计和拒绝。审批把循环挂起，批准事件再恢复。沙箱执行文件和命令，模型不直接碰。取消令牌贯穿循环、模型流和工具，适配器负责把它变成上游断连。检查点把消息、计划、工具结果和版本序列化到自己的库。厂商的会话 id 只是可选缓存。

换模型时实现同一套流式接口、schema 映射和错误码映射。评测集回归通过后再切流量。提示词里少放厂商方言，工具描述保持稳定。

容易漏的是把会话存在厂商云上。换模型时检查点不在自己手里，恢复和审计都断了。

### 7. Codex 类编程 Agent 接入 Java 研发平台：PR 自动修复、单测生成、SQL 评审、安全扫描、CI 卡点，如何设计评测集衡量不是假装修好而是真降缺陷

**参考答案**

结论：成功由固定仓库版本上的客观检查决定。模型自评、编译通过和 diff 变小都不算修好。Agent 的结论不能单独放行，CI 卡点仍是测试、扫描和规则。

PR 修复的样本来自历史缺陷：修复前测试失败，修复后目标测试通过，相关测试没有新增失败，补丁不改测试、不删断言、不加忽略、不改构建去跳过。单测生成看新测试能不能抓住埋进去的缺陷或变异，覆盖率上升而断言恒真，判为失败。SQL 评审用标注过的慢查询、注入和全表扫描，同时看检出率和误报率。安全扫描用已知漏洞样本和干净样本，干净样本被乱报也是失败。

样本按时间切开。用旧缺陷调试，用其后的缺陷评测，避免只会对做过的题。每个样本固定仓库版本、命令和通过标准。线上再看缺陷密度、回滚率和人工接受率，和没开 Agent 的对照比。离线集里的失败要能区分改测试凑通过和确实没修好。

容易漏的是用编译通过当修复成功。编译通过只说明没有语法错误。

### 8. 向量库和图库联合调优：embedding 漂移、图结构过期、查询计划不稳定，如何用离线评测、线上 AB、人工抽检形成闭环

**参考答案**

结论：离线评测定能不能发布，线上实验看任务有没有变好，人工抽检专门看漂移和过期。三份结果进同一个 bad case 池，修完先补进离线集。

嵌入漂移用固定的查询和相关文档 id，看 Recall@K 和 MRR，不看分数绝对值。换模型先走影子检索，用户仍看旧结果。图过期用多跳问题，标注正确的路径或答案实体，发布后看路径是否还在。对账任务找悬挂边和指向旧版本文档的边。查询计划不稳定时，记录过滤条件、分区和搜索参数；参数变更当成一次发布，带参数版本。

线上按会话分流，不要按单次检索分流，否则同一用户前后证据不一致。看任务完成、引用命中、人工接管和 P95。检索分上升不能当成线上成功。图和向量使用同一发布版本，否则实验分不清是谁的变化。

人工抽检分层抽空召回、低置信、图和向量冲突、高扇出实体。结论写明是分块、权限、抽取还是模型问题。修数据或参数之后，这条样本进入发布前的离线集，避免下一次发布打回去。

容易漏的是只比较向量分数。分数的量纲随模型变化，任务指标不会跟着自动变好。

### 9. 学习能力相关：最近半年你从零啃过哪个 Agent、向量或推理新东西，源码看到哪，做了什么压测或复现，踩坑怎么反推官方设计

**答题思路**

只讲一个半年内真看过的东西。临时报一个项目名，追问入口类就会穿。

1. 为什么看：线上遇到了什么现象，或者你想替换哪个组件。
2. 源码范围说具体：入口在哪，状态写到哪，失败怎么返回。没看过的模块说没看。
3. 验证：压测或最小复现里你改了哪个变量，观察到什么。没有数字就讲和对照相比的趋势。
4. 踩坑怎么对应设计：例如重试造成重复写，对上幂等放在调用方而不是框架里。用这个说明你理解取舍。
5. 收在你准备怎么用到自己的系统。说不出源码位置，就不要把「读过文档」说成「读过源码」。

### 10. 技术选型打架时怎么定：自研 while-loop harness 对 LangGraph、LangChain4j、Spring AI，按团队规模、合规、模型供应商、交付节奏怎么拍板

**参考答案**

结论：按谁值班、数据能不能出域、模型换得频不频、交付期限来拍。框架解决的是循环和模型接入，不替代权限、沙箱和评测。

Java 团队、流程相对固定、要快交付：优先 Spring AI 或 LangChain4j，把模型和工具接进现有 Spring Boot，少引入一条 Python 运行时。Spring AI 的 `ChatClient`、advisor 和工具回调适合嵌在现有服务里。LangChain4j 适合把模型、RAG 和简单工具调用先接起来。复杂长循环若只散落在链的回调里，会难恢复、难取消。

路径已经能画成状态图，又需要持久化和人工中断：LangGraph 合适。它的主实现在 Python，Java 团队要么多维护一个运行时，要么只借鉴状态图自己写。权限和沙箱仍在自己的网关。

合规要求提示词、工具和日志都进自己的审计，并且模型供应商会换：自研 while 循环更合适。状态、取消、幂等和检查点自己掌握。代价是检查点、压缩、可观测和评测都要自己做。小团队不要一开始就从这里起步。

拍板用一个真实场景做对照：失败后能不能恢复，换模型要改多少业务代码，一次错答能不能追到版本。值班的人有否决权。交付紧时先上 DAG 和工具网关，自主循环只包一个场景。

容易漏的是把选型说成新旧之分。要讲清楚放弃了哪条路径的什么成本。

### 11. 若带人：Agent 平台同时有算法、Java、数据、评测，如何定接口契约、故障定责、模型版本和知识库版本一起发布，避免谁都改一点最后没人敢发

**参考答案**

结论：契约按版本化接口定，发布按一组版本切换，故障按链路阶段定责。提示词进版本库，和代码一样评审。

意图输出、检索证据、工具 schema 和评测样本各自有 schema。算法改模型或提示词，Java 改编排，数据改索引，都通过这些接口，不互相改对方的库。契约测试放在 CI 里，用固定输入输出，不靠口头约定。

一次失败先看 trace 停在哪一段：意图、检索、权限过滤、工具、生成，还是评测没拦住。每段有一个负责人。跨段问题由当次发布负责人牵头。复盘的结论要回到评测集，否则下一次还会用同样的方式改线上。

一个变更集写明模型、提示词、嵌入、索引、图谱和服务版本。索引没建完不能切模型。发布指针一次切换读版本，按租户灰度。离线评测、契约测试或回滚演练有一项失败，整包不动。回滚也回到上一组版本，避免新模型读旧向量。发布窗口内，除紧急修复外，不再同时改同一索引。

容易漏的是谁都能直接改线上提示词。这样每次发布都掺着未评审的变化，最后没有人敢发。

### 12. 手撕算法：给定一个工具调用事件流，包含 call、result、error、human_approve，判断是否存在环路依赖和不可终止循环，输出建议熔断点及重构后的 DAG

**参考答案**

结论：依赖环和同参重复调用都改成人工节点，不再自动执行。`human_approve` 清空重复计数，所以审批之后的同参调用可以留下。

事件需要先定义清楚。`call` 带 callId、工具名、参数和它等待的 callId 列表；`result` 和 `error` 结束某次调用；`human_approve` 表示人已经介入。依赖边从被等待的调用指向当前调用。图上出现环，就把环上的所有调用列为熔断点，并删掉形成环的那条回边。两次审批之间，相同工具和相同参数的第二次及以后的 `call` 也是熔断点，用来拦住失败后的自动重试。等待一个从未出现的 callId，同样熔断，否则会永远等下去。

重构时，熔断点替换成 `approve@callId`，不再保留自动工具节点。其余依赖做拓扑排序。例如同参扣款失败后再次调用，熔断第二次，边是第一次调用指向对应的 approve 节点。A 等 B 且 B 等 A 时，两个调用都熔断，图上只剩人工节点。

流里原有的 `human_approve` 是计数器的重置点。图上的 `approve@` 是建议新加的熔断点。允许有限次轮询时，把第二次才熔断改成明确的最大次数，并在 DAG 里写成一个带次数的节点，不要让模型自由重复。同一 callId 的重复事件先去重。

扫描、环检测和拓扑都是 O(n + e)，空间 O(n + e)。

```java
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

public final class ToolFlowChecker {
    public enum Type { CALL, RESULT, ERROR, HUMAN_APPROVE }

    public record Event(String id, Type type, String callId, String tool, String args, List<String> waitFor) {}

    public record Report(List<String> fusePoints, List<String> dagNodes, List<String> dagEdges) {}

    private ToolFlowChecker() {
    }

    public static Report analyze(List<Event> events) {
        Map<String, List<String>> deps = new LinkedHashMap<>();
        Map<String, String> signature = new HashMap<>();
        List<String> callOrder = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (Event event : events) {
            if (event.type() != Type.CALL || !seen.add(event.callId())) {
                continue;
            }
            callOrder.add(event.callId());
            signature.put(event.callId(), event.tool() + "\0" + event.args());
            List<String> waiting = new ArrayList<>();
            if (event.waitFor() != null) {
                waiting.addAll(event.waitFor());
            }
            deps.put(event.callId(), waiting);
        }

        Set<String> fuse = new LinkedHashSet<>();
        Set<String> backEdges = new HashSet<>();
        Map<String, Integer> color = new HashMap<>();
        List<String> stack = new ArrayList<>();
        for (String id : callOrder) {
            if (color.getOrDefault(id, 0) == 0) {
                dfs(id, deps, color, stack, fuse, backEdges);
            }
        }
        Set<String> called = new HashSet<>(callOrder);
        for (Map.Entry<String, List<String>> entry : deps.entrySet()) {
            for (String wait : entry.getValue()) {
                if (!called.contains(wait)) {
                    fuse.add(entry.getKey());
                }
            }
        }

        Map<String, Integer> repeats = new HashMap<>();
        Set<String> counted = new HashSet<>();
        for (Event event : events) {
            if (event.type() == Type.HUMAN_APPROVE) {
                repeats.clear();
                continue;
            }
            if (event.type() != Type.CALL || !counted.add(event.callId())) {
                continue;
            }
            int times = repeats.merge(signature.get(event.callId()), 1, Integer::sum);
            if (times >= 2) {
                fuse.add(event.callId());
            }
        }

        Map<String, List<String>> outgoing = new LinkedHashMap<>();
        Map<String, Integer> indegree = new HashMap<>();
        List<String> edges = new ArrayList<>();
        Set<String> seenEdges = new HashSet<>();
        for (String id : callOrder) {
            String node = nodeName(id, fuse);
            outgoing.putIfAbsent(node, new ArrayList<>());
            indegree.putIfAbsent(node, 0);
        }
        for (Map.Entry<String, List<String>> entry : deps.entrySet()) {
            String dependent = nodeName(entry.getKey(), fuse);
            for (String wait : entry.getValue()) {
                if (!called.contains(wait) || backEdges.contains(entry.getKey() + "->" + wait)) {
                    continue;
                }
                String prerequisite = nodeName(wait, fuse);
                if (prerequisite.equals(dependent)) {
                    continue;
                }
                String edge = prerequisite + " -> " + dependent;
                if (!seenEdges.add(edge)) {
                    continue;
                }
                outgoing.get(prerequisite).add(dependent);
                indegree.merge(dependent, 1, Integer::sum);
                edges.add(edge);
            }
        }

        List<String> ready = new ArrayList<>();
        for (Map.Entry<String, Integer> entry : indegree.entrySet()) {
            if (entry.getValue() == 0) {
                ready.add(entry.getKey());
            }
        }
        List<String> topo = new ArrayList<>();
        Set<String> placed = new HashSet<>();
        for (int i = 0; i < ready.size(); i++) {
            String node = ready.get(i);
            if (!placed.add(node)) {
                continue;
            }
            topo.add(node);
            for (String next : outgoing.getOrDefault(node, List.of())) {
                int left = indegree.merge(next, -1, Integer::sum);
                if (left == 0) {
                    ready.add(next);
                }
            }
        }
        if (topo.size() < outgoing.size()) {
            for (String node : outgoing.keySet()) {
                if (!placed.contains(node)) {
                    fuse.add(node.startsWith("approve@") ? node.substring("approve@".length()) : node);
                }
            }
        }
        return new Report(new ArrayList<>(fuse), topo, edges);
    }

    static String nodeName(String callId, Set<String> fuse) {
        return fuse.contains(callId) ? "approve@" + callId : callId;
    }

    static void dfs(String id, Map<String, List<String>> deps, Map<String, Integer> color,
                    List<String> stack, Set<String> fuse, Set<String> backEdges) {
        color.put(id, 1);
        stack.add(id);
        for (String wait : deps.getOrDefault(id, List.of())) {
            if (!deps.containsKey(wait)) {
                continue;
            }
            int state = color.getOrDefault(wait, 0);
            if (state == 0) {
                dfs(wait, deps, color, stack, fuse, backEdges);
            } else if (state == 1) {
                backEdges.add(id + "->" + wait);
                int at = stack.indexOf(wait);
                for (int i = at; i < stack.size(); i++) {
                    fuse.add(stack.get(i));
                }
            }
        }
        stack.remove(stack.size() - 1);
        color.put(id, 2);
    }
}
```
