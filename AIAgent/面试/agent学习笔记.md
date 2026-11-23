# 计算机王的学习笔记

范围是博客里 AI 系列的第 1 篇到第 184 篇（标题带 `1-` 或 `N.`，不含同号的 Windows 驱动、游戏逆向、渗透笔记）。笔记按作者原文里的机制整理，不是目录摘要。原文在 CSDN「计算机王」（[qq_36301061](https://blog.csdn.net/qq_36301061)），文内写明内容参考图灵 AI 大模型全栈。第 183、184 篇标题把 DeepAgents 写成 DeppAgents，把 Harness 写成 Harnees。

篇目链接在文末。

---

## 1. 大模型在解决什么

第 1–6 篇。

人工智能从 1956 年达特茅斯会议起，目标被定为通用人工智能：能独立思考、自主学习、处理多类问题。实现路径有两条。符号主义用写死的规则模拟专家推理，决策树、随机森林都在这条线上；规则一旦要加症状就得改算法，上限停在专家水平。联结主义模仿神经元，用海量数据自己找规律。感知机把输入、权重和阈值收成一个判断：加权和大于等于阈值才算正类。现在的大模型走的是这条线。

提示词被当成这个环境里的编程语言。链式思考（笔记标题写作 TOC，正文讲的是 Chain of Thought）是让模型先把步骤写出来再给结论。好的提示词要给角色、约束、输出格式和例子，而不是只丢一句问话。

Token 是计费单位，也是模型看到的最小片段。输入算上行，输出算下行。一句话会被切成若干 token，末尾往往还有一个看不见的结束符 EOS，模型生成到 EOS 就停。词典不是人手写的：模型在语料里统计哪些字经常一起出现，就把它们收成一个编号。同一个词在不同模型里切法不同。模型并不“懂”某个 token 的字典义，它靠上下文把「货拉拉拉不拉拉不拉多」这种串重新切开。

幻觉有两类：答非所问，以及给了正确材料仍不按材料答。原因是训练语料真假都收、奖励机制鼓励“写得像那么回事”、知识被打散后按概率捞句子。控制办法是给干净材料、用 RAG 强制按资料答、人来做最终决定。金融和医疗不能靠幻觉发挥。

记忆分两层。短期记忆是这一轮对话：把历史消息再次发给模型。长期记忆是训练进权重的知识，以及写死在系统提示词里的内容。模型没有跨窗口的记忆，除非应用自己存下来再塞回去。

参数量决定能力上限和显存。本地用 Ollama 拉模型，`ollama run deepseek-r1:14b` 里的 14b 是 140 亿参数。Windows 看显卡显存；显存不够时 Ollama 会用到共享 GPU 内存，大约是系统内存的一半。服务侧用 Docker 把 One-API、FastGPT 一类组件装起来，镜像源要换成国内，WSL 太旧时先 `wsl --update`。

Transformer 原本做翻译，左边编码器、右边解码器。句子先切 token，短句用 `<pad>` 补齐，每个 token 变成固定维度向量（讲解里用 768 维）。自注意力不看词序，「我爱你」和「你爱我」的词向量集合一样，所以要加位置编码，再和词向量相加。解码器从 `<start>` 逐词生成，直到 `<end>`。可视化可以看 [Transformer Explainer](https://poloclub.github.io/transformer-explainer/)。

## 2. 为什么要 RAG，向量怎么存

第 7–8 篇。

直接问大模型有三件事做不到：幻觉、知识更新慢、行业里那 20% 没公开的专业知识学不到。RAG 在调用模型之前先查人准备好的资料，把问题和命中的片段拼进提示词。流程是：文档切块、变成向量、写入向量库；问题也变成向量；按相似度取出片段；再生成。

笔记把 RAG 分成普通、高级、模块化。模块化是把高级 RAG 封装成可替换模块。高级 RAG 比普通多了两段：前置处理把用户问得含糊的问题改清楚再检索；后置处理把检索结果过滤、重排、压缩，少喂 token。

相似度是向量之间的距离。本地向量模型把句子打成一串小数；在线用百炼一类后付费接口，用量要自己盯着。向量库这一篇以 Chroma 为主，内存或落盘都行，落盘后底层能在 SQLite 里看到。`add` 写入，`update` 改，用问题向量做查询。Redis 也能当向量库，笔记里用得少。

## 3. LangChain 把 RAG 和调用封装起来

第 9–20 篇。笔记里的版本感是：1.0 和更新的 3.0 不兼容，Agent 部分后面改到 1.0 的 `create_agent`。调用大模型仍然走 OpenAI 兼容接口，密钥放 `.env`，用 `load_dotenv()`。

笔记这一段按 LangChain 1.0 来写。作者文中把“更新的不兼容版本”写成了 3.0，和后来实际发布的版本号不是一回事，面试时按你本机 `langchain` 的版本说。

**提示词三种。** 字符串模板做一次性生成。聊天模板带角色，`ChatPromptTemplate.from_messages` 里用 `{变量}`，`format_messages` 时替换。少量样本模板给几个输入输出例子，把不稳定的回答拉向固定格式。

**模型三种、调用五条路。** 语言模型是纯文本补全，聊天模型带对话（名字里常有 chat），嵌入模型只出向量。调用方式包括 `init_chat_model`、OpenAI 兼容客户端、厂商 SDK、社区包（如通义 `Tongyi`）、本地 Ollama。`init_chat_model` 和厂商 SDK 会把思考过程返回来；OpenAI 兼容调用未必把思考过程暴露出来，不代表模型没想。模型名字后缀可以当选型：Ultra/Max 偏能力，Pro/Plus 日常，Base 未做指令微调所以适合再训练，Flash/Turbo 偏速度，Mini/Nano 偏端侧，Vision/VL 看图，Image 生图，Audio 做语音。

**输出解释器**不负责让模型“变成 JSON”，它只检查返回是不是 JSON、XML 或 CSV。格式仍靠提示词。模型老是缺括号，就得改提示词或后面做修复。`LLMChain` 已弃用，改成管道。

**文档。** RAG 四步：准备、切分、向量化、入库。LangChain 能读 PDF、CSV、HTML、JSON、Markdown。PDF 按页切会把一句话拆到两页，语义断掉，所以后面改走 PDF 转 Markdown。切分常用 `RecursiveCharacterTextSplitter` 和按 Markdown 标题切的 `MarkdownHeaderTextSplitter`。入库后用 Retriever，比手写向量查询多一层接口。

**LCEL 和 Runnable。** 链用 `|` 串起来，底层是 Python 的 `__or__`。提示词、模型、解析器、检索器都继承 Runnable，所以能塞进同一条管道。多个输入用 `RunnableMap` 一类并行分支，再汇合。

**工具和 0.3 / 1.0 的 Agent。** 模型只会说，Agent 让它碰到外部世界。工具要有名字、简介、参数。搜索用 Tavily，自定义工具把函数登记进去。工具一多，全塞进提示词又占 token 又占内存，所以后面用 Skills 只先给名字和简介。0.3 用 `AgentExecutor` 当总管：问题进来，Agent 决定走本地检索还是 Tavily，真正调用交给 Action Executor。1.0 用 `create_agent`，中间件插在循环上，调用次数也可以限住。笔记写明要装到 1.0.2 附近，否则示例对不上。

**记忆和 LangSmith。** 短期记忆就是把历史消息随请求再发一遍。LangSmith 用来看每一步实际拼出来的提示词和工具调用，排查“模型为什么选了这个工具”。

## 4. 高级 RAG：索引怎么建，检索怎么改

第 21–33 篇。

一条朴素 RAG 会在三个地方坏。建索引时内容缺失、PDF 加载不准、切块粒度没标准。检索时高相关片段排不进前列（Missed Top Ranked）。生成时上下文和答案无关、格式不是要求的 JSON、只答了问题的一半、文档里有答案却没抽出来（模型不够强），或者答案过宽、过窄。

笔记用六篇论文把成熟做法对齐：

| 做法 | 在补哪一块 |
| --- | --- |
| T-RAG | 任务和时间敏感。知识按树走：总公司、分公司、部门、人。生成模型还按知识库微调过 |
| CRAG | 检索结果打成 Correct / Ambiguous / Incorrect。靠谱就提纯，不全就补网上的，全是垃圾就丢掉改去网上查 |
| Self-RAG | 先决定要不要检索。1+1 这种不查。查完再标资料是否相关（IsREL） |
| GraphRAG | 用图做跨段落推理，见下一节 |
| RAG-Fusion | 一个问题扩成多个查询，结果合并重排 |
| Rewrite-Retrieve-Read | 先改写问题再检索 |

RAG 和微调：RAG 是外挂库，改资料就改库，质量取决于检索。微调是把数据写进权重，回答时不再查库，贵，而且新闻这种天天变的数据不适合。第 23 篇正文很短，对比就这一层，其余是图。

**六种索引。**

- 摘要索引：每块原文生成摘要，摘要进向量库，用同一个 id 绑回原文。查询打在摘要上，喂给模型的是原文。
- 父子索引：父块大约两千字，再切成约五百字的子块。用子块做向量匹配，命中后把父块交给模型。小块抓得准，大块上下文完整。父块可以落在 MySQL，检索器 `add_documents` 时按 id 写入。
- 假设问题索引：让模型对每块提三个问题，向量化的是问题不是正文。问句对问句更容易命中，但生成的问题不可控，笔记里用得不多。
- 元数据索引：切块后在 metadata 里写日期、来源、作者、评分，字段说明尽量用英文。问题先交给模型，生成一份过滤 JSON，再用这份过滤条件去向量库。
- 混合索引：向量抓语义，BM25 抓词面。专有名词、代码里的函数名、游戏黑话，向量经常失败，BM25 能中。两路各有分数，融合后再交给模型。
- 预检索（查询优化）：用户问题先扩成多个角度。三个问题各取三篇就是九篇，不能全塞给模型，后面必须融合或压缩。

**后检索。** RAG-Fusion 用 RRF：`1 / (rank + k)`，k 常取 60，rank 从 0 或 1 起算按实现为准。多查询命中的文档把分数加起来再取 Top-K。只被一个查询命中的文档分数会偏低。

上下文压缩有三种，不要混：

- `LLMChainExtractor` 用模型把文档改写成更短的相关内容，贵，会改原文。
- `LLMChainFilter` 只判断相关与否，不改原文。
- `EmbeddingsFilter` 对已经取出的 Top-K 再按向量分数卡阈值（笔记里的例子是 0.66），便宜。问题往往比入库的块短很多，第一次相似度不可靠，所以要这第二遍。

**PDF 和表格。** 按页读 PDF 会把跨页表格拆开。MinerU 把 PDF 转成 Markdown，在线、桌面、命令行都能用，开发批量用本地。切分按 `#` 标题，不按页。表格先用正则抽出，换成 `tbl_数字__` 占位符，正文切块和向量化时不带表格；检索命中这块后再把表格填回去。这样表格结构不会被切碎，向量也不会被大段格子带偏。

## 5. 图 RAG

第 34–39、81–86 篇。

向量检索做不到“这篇文章的主题是什么”，主题通常不会写成一个可匹配的句子。知识图谱补的是关系和推理。节点是实体，边是关系。两种模型：LPG（Neo4j 这一路）有标签和属性，适合企业里的复杂关系；RDF 是主谓宾三元组，把属性再拆成节点。感知智能是认图认字，认知智能是推理，图谱在后者。

Neo4j 5.x 配 JDK 17，`neo4j console` 起来后浏览器开 `http://localhost:7474/`，默认账号密码都是 neo4j，页面不要整页翻译。Python 侧笔记锁的是 `langchain_neo4j` 0.9.0，还要放对应版本的 APOC jar。建图流程：切文档，用提示词让模型抽出实体和关系（人、地点、法宝、事件，以及师徒、位于、拥有这类关系），装成 `GraphDocument` 写入 Neo4j。提问时让模型生成 CQL，查完再生成答案。

微软的 GraphRAG 最早把这件事做成框架，但笔记里的判断是：绑 GPT、建图极慢、Token 很贵、不能增量加节点。LightRAG（香港大学）改成图和向量一起用，可以增量。双层检索是核心：低层拿实体和直接相连的边，回答“孙悟空的师傅是谁”；高层沿两三跳看主题，回答“西游记在讲什么”。低层从孙悟空只能到敖广，高层可以再从敖广到龙婆。

LightRAG 建索引时，向量里存的是 id，原文和 metadata 在 json。增量时先查 json，已有就不重新抽。抽完的实体要去重，同一个“苹果”来自不同段落时，再让模型把多处描述合成一条。本地文件各自一份职责：chunk / entity / relation 的 FAISS 索引，文档状态（用来跳过未改动的文档），实体到文本块的对照，全文和实体详情。提问时低层先查实体向量，再拿图、拿 chunk 原文，拼进提示词；高层先查关系向量，再扩邻居。

LlamaIndex 侧的抽取器有四种。隐式抽取不单独声明关系类型。默认抽取同时建图索引和图检索。动态抽取让模型自己决定抽什么。Schema LLM Path 抽取按给定的 schema 走，关系类型受约束。第 86 篇把 LightRAG 接到中文语料上，原理仍是上面这套。

## 6. 怎么判断 RAG 好不好

第 40–41、87–92 篇。

检索和生成要分开评。检索看查准和查全，生成看答案是否贴着资料、是否贴着问题。

基础定义：精准度是前 k 个结果里相关文档的比例。召回率是前 k 个里相关文档数，除以库里全部相关文档数，k 常用 3、5、10。F1 是 `2 * P * R / (P + R)`，两个都高 F1 才会高。笔记给的经验区间：0.5 以下还在原型，0.5–0.7 内部能用，0.7–0.85 接近上线，0.85–0.93 是生产，再高是质检、法条这种不能错的场景。父子索引、混合检索、压缩、重排都是在拉这两个数。

Ragas 把四个指标做成可算的流程，输入是问题、检索到的上下文、模型答案、标准答案。

- context precision：检索内容和问题有多贴。
- context recall：检索内容覆盖了多少标准答案。
- faithfulness：答案里有多少句能在检索文档中找到。把答案拆成声明，逐条回文档里对，对上的条数除以总条数。0.75 就是四句里三句有依据。拉高的办法是提示词禁止发挥，以及多给相关文档。
- answer relevancy：用答案再反问出若干问题（笔记里是 3 个），看这些反问和原问题有多像。

LlamaIndex 的评估把同一件事拆成检索评估和响应评估：检索看召回的节点对不对，响应再看忠实度和相关性。响应评估和检索评估的示例分在第 91、92 篇。

## 7. LlamaIndex：从文档到一轮回答

第 42–60 篇。笔记在这里换框架，原因是 LangChain 转向 Agent 之后，RAG 组件不再是它的重点。LlamaIndex 早期叫 GPT Index，2022 年 11 月由 Jerry Liu 开始做，最初用树索引突破上下文长度，后来补了列表和关键词索引。文档、源码和插件市场分别是官方文档、GitHub 和 LlamaHub。

一条链是：Settings 里放全局的对话模型和向量模型（在线、本地、厂商 SDK 都可以）→ 提示词模板（普通模板和 Jinja2，Jinja2 还能带图片做多模态）→ `Document` 和各种 Loader → LlamaParse 负责高精度解析 → 文本 / HTML / JSON / Markdown 分割器变成 Node → 文档存储、向量存储、索引存储分开 → Retriever 取出 Node。默认向量库是 `SimpleVectorStore`，默认相似度是余弦，具体算法跟所选向量库走。

取出很多 Node 之后不能直接拼接。响应合成器的 `response_mode`：

- COMPACT：拼成一次提示词，超长就先摘要再生成。调用少，摘要会丢数字和因果关系。
- REFINE：用第一篇出初稿，后面每篇拿来改一版。细节留得多，调用次数随文档线性涨。
- TREE_SUMMARIZE：分组总结，再总结这些总结。模型不够强时，分组摘要会把重点删掉。

`as_query_engine()` 内部自己建检索器、从 `Settings.llm` 补模型、返回查询引擎。`query` 的真正工作是 `retrieve` 拿 Node，再合成答案。它没有对话历史。

`as_chat_engine()` 多一个 memory。聊天模式里，SIMPLE（`SimpleChatEngine`）不查库，把历史原样塞给模型，指代能懂，私有数据会编。CONTEXT（`ContextChatEngine`）每轮都查，但检索词只用当前这一句，不把历史融进去，所以“那利润率呢”可能查偏。

记忆本身要回答三件事：怎么存、每次喂多少、存多久。`ChatMemoryBuffer` 是原文缓冲。`ChatSummaryMemoryBuffer` 把旧对话压成摘要，摘要仍会变长，细节会丢。`VectorMemory` 按语义长期存，措辞差太远会搜偏，也没有逐字记忆。`SimpleComposableMemory` 把这几种拼起来：近的用缓冲，稍远的用摘要，很久但重要的用向量。落盘有 JSON 文件、Redis、Postgres、Upstash。生产用 Redis 时，把 `SimpleChatStore` 换成 `RedisChatStore` 即可。

## 8. 同一套高级 RAG，在 LlamaIndex 里再做一遍

第 61–80 篇。问题和第 21 篇是同一类：切块、查询、排序、生成。优化手段换成 LlamaIndex 的组件。

摄取管道把加载、切分、抽取、向量化串成一条可重复的流水线。元数据抽取在入库时就把日期、来源写进 Node。Redis 做查询缓存，相同问题不必再打模型和向量库。查询优化仍是改写和扩展问题。父子索引、元数据过滤在这里再实现一次，接口换成 LlamaIndex 的。

混合检索用 `QueryFusionRetriever`。它先把问题扩成多个，每个问题走多路检索器，再融合。三种融合：

- `reciprocal_rerank` 是 RRF：`权重 * 1 / (排名 + 60)`。只看名次，不看原始相似度，快，可能丢掉分数很高但名次靠后的文档。权重用 `retriever_weights`，例如向量 0.4、BM25 0.6。
- `relative_score` 先把各路分数归一化再融合，避免 BM25 和余弦的量纲直接相加。
- `dist_based_score` 按距离分布融合。

路由检索器把每个库包装成工具，让模型按问题选库。语文库和小说库、DeepSeek 介绍和斗破苍穹，不该进同一个向量空间硬比。跨库问题（“萧炎怎么看 DeepSeek”）选哪边，取决于模型，笔记里的例子选了小说库。

SQL 检索器做不到语义相似，它做的是自然语言到 SQL。`NLSQLTableQueryEngine` 把表名、字段、类型、主外键交给模型，模型写 SQL，执行结果再交回模型组织成回答。招聘表这种结构化数据走这条，不走向量。

后处理在 Retriever 之后、生成之前，传给 `RetrieverQueryEngine` 的 `node_postprocessors` 就不用手调：

- 相似度过滤：0 分和低于约 0.7 的块不要进提示词。
- 重排序：用专门的排序模型把“像，但不是答案”的块压下去。
- 上下文优化：压缩或改写，控制 token。
- 时间：按时间排序、时间衰减、时间相似度，新文档权重更高。
- 上下文扩展：命中小块后把相邻块补回来，避免句子被切断。

第 78、79 篇把 MinerU 和 Markdown 元素分割器接进这条管道：PDF 先转 Markdown，再按标题、表格、代码块这些元素切，而不是按固定字数。第 80 篇用这套做金融助手，把加载、索引、查询收成一个可问财报的应用。

## 9. Dify 工作流，以及一套 LlamaIndex 问答应用

第 93–103 篇。

Dify 把上面这些节点做成界面。知识库和大模型节点是最小闭环。Agent 节点让模型自己选工具。问题分类器加条件分支做路由，人工节点把不该自动做的步骤停下来。迭代节点对列表逐项处理，循环节点按条件重复，变量赋值在轮次之间存状态。模板转换、变量聚合、参数提取负责把上游乱七八糟的输出收成下游要的字段。HTTP 节点请求外部接口，再用大模型读返回体。

第 100–103 篇是一个前后端 RAG 问答：前端负责对话界面，后端三个接口。登录发会话。上传走解析、切分、向量化。聊天接口拿历史和检索结果调用 LlamaIndex 的查询或聊天引擎，把答案流回去。这是把第 42–80 篇收成产品形状，而不是再讲一个新算法。

## 10. Agent：给模型手脚

第 104–129 篇。

豆包、DeepSeek 这种对话是一问一答，不会自己跟进订单。Agent 是给模型补上规划、行动、记忆、主动。笔记里的框架梯度：LangChain 做简单 Agent，LangGraph 做必须按步骤走的工作流，DeepAgents 做开箱的复杂 Agent。单智能体、带知识库的智能体、多智能体是三种形态。

环境用 uv 锁依赖，老版本 PyCharm 要单独配 uv 解释器。第一个 Agent 就是模型加一个工具再循环。State 是这一轮的上下文，LangSmith 看轨迹。

模型调用在 Agent 篇又过了一遍：在线多模态、工具调用、厂商包、`langchain_openai`、`init_chat_model`、Ollama。消息分 system、human、ai、tool。工具参数用 Pydantic 描述，模型看到的是 JSON Schema，不是 Python 类型。Function Calling 是模型返回“要调哪个函数、参数是什么”，应用再用 Python 动态调用；参数展开就是函数调用里的 `*` 和 `**`。

短期记忆可以放 Postgres，用 `thread_id` 区分会话。自定义 State 是在消息列表之外再加业务字段，例如用户权限、订单号。

流式输出三种：`updates` 看状态改了什么，`messages` 看模型逐字输出，`custom` 是自己往流里写的进度。事件流是另一条更细的轨迹，笔记标成测试能力。

结构化输出两条策略。模型原生策略直接要 Pydantic、dataclass、TypedDict 或 JSON Schema。工具策略 `ToolStrategy` 把结构伪装成一次工具调用，模型填参数，应用把参数当成结果。原生做不到稳定 JSON 的模型走工具策略。

Runtime 是这一轮的只读上下文：用户 id、数据库连接、日志器。节点读它，不把它当对话状态改。

**中间件就是插在循环上的函数。** 生命周期从外到内：

1. `before_agent`：权限、把长期记忆读进状态、敏感词和提示词注入、按用户等级限 token。可以用 `can_jump_to` 声明能跳到 `tools`、`model` 或 `end`，返回 `jump_to` 才生效；没声明的跳转会被忽略。
2. `before_model`：裁消息、脱敏。
3. `wrap_model_call`：包住这次模型调用。`ModelRequest` 上能改工具列表，20 个工具收成 5 个，或按是否 VIP 换一套。它能取代单独的 before/after model。
4. `after_model`：校验输出、修 JSON、记日志。
5. `wrap_tool_call`：参数校验、权限、重试、改工具结果。
6. `after_agent`：评估、扣费、写工单、把记忆落库。

同类钩子的顺序跟 `middleware` 列表一致：`before_` 和 `wrap_` 从上到下，`after_` 从下到上。要在一个对象里同时写同步和异步、多个钩子、可配置策略，就继承 `AgentMiddleware`，不要散落一堆装饰器。

消息三件套，和中间件绑在一起：

- `trim_messages` 在进模型前按 token 裁，只留尾巴。`allow_partial=True` 会把一条消息切剩一半。笔记认为一般不要裁，因为事实会丢，例如用户刚说过的名字。
- `RemoveMessage` 在模型输出后按消息 id 删。超过阈值就删最早的几条，LangGraph 看到这个标记会真正删。
- 摘要中间件 `SummarizationMiddleware` 把旧对话压成一段再送模型。
- `TodoListMiddleware` 让模型先列计划再执行，用来把 plan-and-execute 叠到默认的 ReAct 上。
- 动态提示词按状态改系统提示，例如按用户等级换人设。笔记标题里的函数名写成了 `dynamic_promapt`。

模型吐出坏 JSON 时，简单情况用正则补括号，复杂情况再叫一次模型。修好的内容写回状态。

## 11. 设计模式、多 Agent、Skills

第 130–143 篇。

六种模式可以叠，不是六选一。

- ReAct：想一步、做一个动作、看结果、再想。`create_agent` 在 1.0 之后默认就是它。容易死循环，也费 token，要用调用次数中间件卡住。
- plan-and-execute：先列计划再执行。现在用任务规划中间件叠在 ReAct 上，而不是换成另一套运行时。
- Multi-Agent：多个角色加一个协调者。
- Workflow / DAG：步骤写死在 LangGraph 里，人可控，开发长。和任务规划中间件的差别是：谁来决定下一步。前者是代码，后者是模型。
- Self-Ask：先自己拆出子问题再答。笔记认为已经没人用。
- Thinking and Self-Reflection：答完再自我检查，是现在还在用的一种。

一个 Agent 同时做需求、编码、测试，会把三件事搅成一件。MAS 按角色拆开，主管收结果。旅行的例子是天气、机票、路线、预算、攻略各一个 Agent，可以并行。

AutoGen 是微软的对话式协作：Agent 可定制模型和工具，彼此用自然语言说话。笔记里的例子是生成代码、检查有没有代码、审核、本地跑、失败就回到生成。CrewAI 的零件是 Agent、Task、Tool、Crew、Flow，还有 Memory。它按设定的流程走一遍，审计不通过时不能只重做写作那一步。它和 uv 环境容易冲突，笔记另开了 Python 3.12 的 conda 环境。

LangChain 这条线的四种多 Agent，笔记放在一起对比：

- SubAgents：主管把子 Agent 当工具调用，子 Agent 做完把结果交回来。
- Handoff：把控制权交给另一个 Agent，常和任务规划一起用。
- Skills：不把工具全文塞进上下文。
- Router：先分类，再把问题送到对应 Agent。

Skills 的加载是渐进的。先只给技能的名字和简介；模型认为要用时，再读正文；正文里的参考文件还可以再延后读。一个技能是一组文件，不是一个函数。多个技能放在一起，就得到一组按需出现的专家，而不必做多个常驻 Agent。执行时模型先选技能，再按技能文档里的步骤调用真正的工具。

## 12. LangGraph：流程必须可控

第 144–175 篇。

退款这种流程不能交给模型自由发挥：查订单、查物流、分责任、金额超过 200 要人工、物流没问题要改口并再核实。LangChain 的 Agent 把这些写成提示词，执行是黑盒，服务停了只能从头来，人机交互中间件也不好用。LangGraph 把步骤写成图，生产环境按图走。安装 LangChain 时会带上 langgraph，仍建议 `uv add langgraph` 把版本锁住。

四个零件：State 是流过全图的上下文，Node 是函数，Edge 决定下一跳，Graph 把三者收成 `StateGraph`。节点执行前后都能读写 State，跳不跳也看 State，所以它强调的是状态图，不是一张死流程图。

State 用 `TypedDict`。Pydantic 能校验和默认值，但进出图要做 `dict` 转换，笔记不推荐拿来当全局 State。输入、内部、输出可以是三套 schema：输入只有问题，内部包含问题、检索结果和答案，输出只留答案。内部那套必须覆盖输入和输出的字段。

节点返回的是局部更新。没有 Reducer 时，两个并行节点写同一个字段，后写的覆盖先写的。`Annotated[list, operator.add]` 表示追加。`operator.add` 只会往后加，不能按 id 改某一条。消息要用 `add_messages`：带 id 的更新是修改，不是再追加一条。`MessagesState` 已经把 `messages` 配好了，继承即可。

`START` 和 `END` 是特殊节点。`add_edge(START, "a")` 可以代替 `set_entry_point`。一个节点连出两条边就是并行，汇合点要等两边都到。不要把边接成环却没有出口。

条件边 `add_conditional_edges`：节点跑完调用一个函数，返回值当 key，映射到下一个节点名。条件入口 `set_conditional_entry_point` 是进图之前就选第一站，例如按用户是 VIP、普通还是游客。

普通边不能按数据条数动态分叉。`Send` 做 Map-Reduce：分发节点返回一组 `Send("worker", 局部状态)`，每个 worker 拿自己的那份状态并行跑，全部结束后计数归零，汇合节点才执行。Reducer 把各自分数收成一个列表。

`Command` 把更新状态和跳转写在节点里面，可以不再画边。`update` 改状态，`goto` 指定下一节点，也可以是一组 `Send`。`graph=Command.PARENT` 从子图跳回父图。`resume` 用来在中断之后把人填的值送回去。

Runtime 在第 153 篇拆成三类。`context` 是这次执行的只读依赖，`invoke(..., context={"language": "zh"})`，节点里 `runtime.context` 读取。`store` 是跨会话的长期存储。`control.request_drain` 要求当前超步结束后停机并留下检查点，节点看 `drain_requested` 和 `drain_reason`。`execution_info` 里有节点重试次数。递归限制 `recursion_limit` 默认很大（笔记写 1000），每跳一次计一次，节点里看 `remaining_steps`，实际可跳次数是限制减一，用来打断死循环。

节点失败用 `RetryPolicy`。不写 `retry_on` 就用默认的一组异常（`ValueError`、`OSError`、`RuntimeError` 等）。数据库断线这种要自己写进 `retry_on`，否则节点直接失败、图停住。`max_attempts` 限制这个节点能跑几次。`runtime.execution_info.node_attempt` 是当前第几次，可以用来降级。

图可以导出 Mermaid，或装 IPython 后导出 PNG。

工具仍然是 Function Calling。`@tool` 装饰函数，`bind_tools` 得到一个带工具说明的新模型对象。图上是：模型节点 → 条件边 → 要么 `ToolNode`，要么结束。工具节点写回 `ToolMessage`，再回到模型节点。`tools_condition` 是现成的判断函数。

子图是把一张图当成一个节点。共享状态：子图和主图用同一套 State，`add_node` 直接挂上去，字段会互相看见。不共享：主图节点里手调 `subgraph.invoke`，只传入需要的字段，生产用这种，避免子图污染主图。

多智能体六种摆法：单 Agent 多工具；Network 是彼此交接；Supervisor 由主管点名；Supervisor as tools 把别的 Agent 包成工具，对应 LangChain 的子代理；Hierarchical 是主管套主管；Custom 就是自己用边把走向写死。交接用 `make_handoff_tool` 生成 `transfer_to_某专家`，子图里 `Command(goto=..., graph=Command.PARENT)` 跳回父图。LangChain 靠换系统提示词换角色，LangGraph 靠子图跳转。自定义主管则是：主管节点返回子图名字，条件边跳过去，数据用非共享状态传递。

**检查点是短期记忆。** 每一步状态更新叫一个超级步；并行的两个节点汇合前算同一步。每步结束存一个 `StateSnapshot`：`config`、`metadata`、`values`（全局状态，不是节点的返回值）、`next`、`tasks`。检查点挂在 `thread_id` 上，线程之间互不可见。保存发生在局部更新合并进全局状态之后。同一个线程再次 `invoke`，历史是追加的，所以第二次问“我叫什么”还能看见第一次的自我介绍。

Replay 不是读缓存。选中节点 B 的检查点，加载它的状态，从下一个节点重新执行。新跑出来的检查点追加在列表里，不覆盖旧的。`update_state` 在指定检查点上开一条新分支，`values` 走 Reducer，`as_node` 标明这是哪个节点的产出。人可以改草稿再继续。`invoke` 恢复时输入是 `None`，改状态用 `update_state`，不要指望 `invoke` 的 input。

长期记忆用 Store，跨线程。一条是 Item：`namespace`、`key`、`value`、创建和更新时间。namespace 用元组隔离用户，例如 `("user_id", "memories", "2026")`。`put` 写入，`get` 按 key 取，`search` 取这个命名空间下的全部。语义检索要在创建 Store 时配向量维度和 `fields`；`fields` 写上后分数才按字段算，不写时笔记里看到的分数是 0.5 一档。图里通过 `runtime.store` 取，在调用模型前按问题搜记忆，拼进系统提示词。同一 `user_id`、不同 `thread_id` 能对上“你还记得我吗”。

支持的后端是 Postgres、MongoDB、Redis、Oracle。笔记的生产搭配是 Postgres 放长期记忆，Redis 放短期检查点。Postgres 的检查点表是 `checkpoints`（概要）、`checkpoint_blobs`（真正的二进制状态）、`checkpoint_writes`（每个节点写了什么）、`checkpoint_migrations`。长期记忆只有一张 `store`。这些表是 LangGraph 建的，不手改。

流式模式比 LangChain 多。`values` 是节点结束后的完整状态，`updates` 是谁改了哪些字段，`messages` 是模型 token，`custom` 用 `get_stream_writer` 自己写，`debug` 带 step、时间、触发边，`checkpoints` 和 `tasks` 看检查点和任务起止。1.1 以上可以用 v2，收成统一的 `StreamPart`。`stream_mode` 可以同时要好几种。模型本身不支持流式时，把那次调用改成 `invoke`，并在状态里标明。

图里的消息管理要自己写，没有 LangChain 那种现成中间件。进模型前 `trim_messages`。超长时用 `RemoveMessage` 删最早的几条。`add_sequence([call_llm, delete_messages])` 按数组顺序一次加上节点和边。摘要用 `langmem` 的摘要节点：先摘要再回答。不改提示词时摘要是英文。摘要另存，原消息列表还在；送给下一步的是“摘要加本轮新消息”，摘要放进系统提示词。更新摘要时要明确写：新摘要替换旧摘要，姓名、目标和偏好必须留下。

人机交互是动态中断。`interrupt_before=["publisher"]` 用在“活干完了，副作用发出去之前要人看一眼”，例如代码写好了但先别部署。节点里调用 `interrupt(要人填的内容)` 用在“跑到一半缺参数”。`stream` 能看到传给 `interrupt` 的内容，人输入后 `invoke(None, config)` 继续。恢复时会从该节点第一行再执行一遍，所以 `interrupt` 放在节点最前；一个节点最好只中断一次，虽然技术上能多次。

## 13. MCP：工具从 Agent 里搬出去

第 176–182 篇。

模型不知道今天几号，也看不到某个人的聊天记录。这些能力是工具，不是再训练一次就能永久解决的。三个 Agent 各写一套工具会重复，工具上百个之后也无法维护，换一个模型还可能对不上参数格式。MCP 把工具抽成服务：Agent 是客户端，MCP 是服务器，入参和返回都收成统一协议。笔记里的比喻是拓展坞，电脑只认这一个接口，后面接数据库、文件、搜索。

三个角色：Host 是 AI 应用，Client 负责连服务器并把上下文交回 Host，Server 提供能力。Server 上有三类东西：Tools 执行动作，Resources 读数据（文件、查询结果），Prompts 放可复用的提示词模板。Client 侧还有通知和采样。发现能力、调用工具、读资源、取提示词，都走同一条协议。

FastMCP 里，`@mcp.tool`、`@mcp.resource`、`@mcp.prompt` 登记三类能力，`mcp.run()` 启动。笔记用加法、BMI、健康指南做示例。Cherry Studio 当图形客户端：命令填 Python，参数填服务脚本。新版默认模型经常不调用 MCP，要换成百炼上较强的模型，并在会话里把 MCP 开关打开，变绿才算这个会话能用。

代码客户端用 fastmcp 的 `Client`，传入服务脚本路径，`async with` 建立会话，然后 `call_tool`、列出资源 URI、按名字取提示词。

传输有两套，旧的 HTTP+SSE 不推荐。

STDIO 是本机进程。客户端拉起服务端子进程，用 stdin 写 JSON-RPC，用 stdout 读 JSON-RPC。stdout 上不能打日志，否则协议被污染；日志走 stderr，Python 里是 `print(..., file=sys.stderr)`。因此可以在中间插一个进程：把两边的 stdin/stdout 原样转发，同时把报文写到文件。报文里能看到工具名、参数和返回值。客户端并不检查你给的文件是不是合法 Server，它都会按 MCP 去说话。

Streamable HTTP 用于远程。Server 的 `run` 改成这种传输，等于挂出一个 HTTP 接口。Client 用 `StreamableHttpTransport`。FastMCP 可以挂进 FastAPI，业务应用和 MCP 共用一个进程。官方说明在 FastMCP 的部署文档和 FastAPI 集成文档里。

LangChain 接入有新旧两套。旧的 `langchain-mcp-adapters` 把 MCP 工具转成 LangChain 工具，笔记写明 mcp 低于 2.0.0 才对得上。2026-11 前后的新版本（笔记里的环境是 mcp 2.3、fastmcp 4.2、langchain 1.4）改成：fastmcp `Client` 负责 `call_tool`、读资源和提示词，再手工包一层 LangChain 工具交给 `create_agent`。模型决定调工具，LangChain 工具再调用 `client.call_tool`。

## 14. DeepAgents 和 Harness

第 183–184 篇。

ReAct 做长任务会丢最初目标，工具超时、参数错误、权限不足都要模型自己扛，上下文随工具结果膨胀。这些可以在 LangGraph 里一个节点一个节点写出来，但状态、记忆、异常都要自己处理。DeepAgents 是建在 LangGraph 和 LangChain 上的库，用来少写这些样板。笔记的比喻：LangGraph 是菜板和刀，DeepAgents 是已经备好的菜。

它相对普通 ReAct 多四样：规划工具，把大任务拆开；文件系统，长内容写到文件里，上下文只留路径和摘要；多级子代理；一份为长任务写的系统提示。

Harness 在笔记里是一种分工，不是某个安装包。大模型不可控，harness 像马具，负责让它按缰绳走。决策者只思考下一步；运行环境只执行、记日志，不思考。一个完整的运行环境要有：执行循环，工具的注册、校验和返回，状态，每次给模型多少上下文，长短期记忆，失败后的重试或换方案，超时，错误分类和恢复，日志和调用链，缓存，并发限制，以及人可以插手的决策点。用户问题进来后，由这层把技能、工具清单和历史拼成提示词；模型只判断要不要调工具，并给出最终答案；工具的真正执行在环境里。LangChain 和 LangGraph 已经是这种结构，只是上面那些能力要自己补。

官方拆法是三层：DeepAgents 做决策，harness 接执行，后端只提供跑工具的环境。上手是安装后调用 `create_deep_agent`。

---

## 篇目

链接指向原文。括号里是这篇在上面哪一节。

| 篇 | 标题 | 归类 |
| --- | --- | --- |
| [1](https://blog.csdn.net/qq_36301061/article/details/161026306) | AI大模型基础认知 | 大模型 |
| [2](https://blog.csdn.net/qq_36301061/article/details/161059881) | 链式思考与提示词 | 大模型 |
| [3](https://blog.csdn.net/qq_36301061/article/details/161091107) | Token | 大模型 |
| [4](https://blog.csdn.net/qq_36301061/article/details/161147344) | 幻觉、记忆、参数 | 大模型 |
| [5](https://blog.csdn.net/qq_36301061/article/details/161165794) | Transformer | 大模型 |
| [6](https://blog.csdn.net/qq_36301061/article/details/162013244) | 本地模型服务 | 大模型 |
| [7](https://blog.csdn.net/qq_36301061/article/details/161235102) | 相似度与 RAG 为何出现 | RAG 基础 |
| [8](https://blog.csdn.net/qq_36301061/article/details/161265108) | Chroma 向量库 | RAG 基础 |
| [9](https://blog.csdn.net/qq_36301061/article/details/161399026) | LangChain 实现 RAG | LangChain |
| [10](https://blog.csdn.net/qq_36301061/article/details/161429546) | 三种提示词 | LangChain |
| [11](https://blog.csdn.net/qq_36301061/article/details/161461490) | 五种模型调用 | LangChain |
| [12](https://blog.csdn.net/qq_36301061/article/details/161491910) | 输出解释器 | LangChain |
| [13](https://blog.csdn.net/qq_36301061/article/details/161547551) | 文件读取 | LangChain |
| [14](https://blog.csdn.net/qq_36301061/article/details/161571193) | 文档切分 | LangChain |
| [15](https://blog.csdn.net/qq_36301061/article/details/161600066) | LCEL | LangChain |
| [16](https://blog.csdn.net/qq_36301061/article/details/161631727) | Runnable | LangChain |
| [17](https://blog.csdn.net/qq_36301061/article/details/161664213) | Tavily 与自定义工具 | LangChain |
| [18](https://blog.csdn.net/qq_36301061/article/details/161695879) | 0.3 Agent 与模型后缀 | LangChain |
| [19](https://blog.csdn.net/qq_36301061/article/details/161752537) | 1.0 Agent 与中间件 | LangChain |
| [20](https://blog.csdn.net/qq_36301061/article/details/161770505) | 记忆与 LangSmith | LangChain |
| [21](https://blog.csdn.net/qq_36301061/article/details/161806906) | RAG 的问题 | 高级 RAG |
| [22](https://blog.csdn.net/qq_36301061/article/details/161838213) | 六种成熟实现 | 高级 RAG |
| [23](https://blog.csdn.net/qq_36301061/article/details/161868666) | RAG 与微调 | 高级 RAG |
| [24](https://blog.csdn.net/qq_36301061/article/details/161901634) | 摘要索引 | 高级 RAG |
| [25](https://blog.csdn.net/qq_36301061/article/details/161959946) | 父子索引 | 高级 RAG |
| [26](https://blog.csdn.net/qq_36301061/article/details/161974783) | 假设问题索引 | 高级 RAG |
| [27](https://blog.csdn.net/qq_36301061/article/details/162013649) | 元数据索引 | 高级 RAG |
| [28](https://blog.csdn.net/qq_36301061/article/details/162043247) | 混合索引 | 高级 RAG |
| [29](https://blog.csdn.net/qq_36301061/article/details/162075806) | 预检索 | 高级 RAG |
| [30](https://blog.csdn.net/qq_36301061/article/details/162117821) | RAG-Fusion 与 RRF | 高级 RAG |
| [31](https://blog.csdn.net/qq_36301061/article/details/162140332) | 上下文压缩 | 高级 RAG |
| [32](https://blog.csdn.net/qq_36301061/article/details/162169221) | MinerU | 高级 RAG |
| [33](https://blog.csdn.net/qq_36301061/article/details/162211028) | PDF 表格占位 | 高级 RAG |
| [34](https://blog.csdn.net/qq_36301061/article/details/162243945) | 图 RAG 概念 | 图 RAG |
| [35](https://blog.csdn.net/qq_36301061/article/details/162281797) | Neo4j | 图 RAG |
| [36](https://blog.csdn.net/qq_36301061/article/details/162313171) | Python 建图问答 | 图 RAG |
| [37](https://blog.csdn.net/qq_36301061/article/details/162350445) | LightRAG 双层检索 | 图 RAG |
| [38](https://blog.csdn.net/qq_36301061/article/details/162363590) | LightRAG 索引与查询 | 图 RAG |
| [39](https://blog.csdn.net/qq_36301061/article/details/162388858) | LightRAG 本地文件 | 图 RAG |
| [40](https://blog.csdn.net/qq_36301061/article/details/162429671) | 精准度、召回率、F1 | 评估 |
| [41](https://blog.csdn.net/qq_36301061/article/details/162463647) | Ragas 四指标 | 评估 |
| [42](https://blog.csdn.net/qq_36301061/article/details/162496605) | LlamaIndex 说明 | LlamaIndex |
| [43](https://blog.csdn.net/qq_36301061/article/details/162527708) | 在线、本地、厂商 SDK | LlamaIndex |
| [44](https://blog.csdn.net/qq_36301061/article/details/162560892) | 向量模型 | LlamaIndex |
| [45](https://blog.csdn.net/qq_36301061/article/details/162574452) | Settings | LlamaIndex |
| [46](https://blog.csdn.net/qq_36301061/article/details/162603227) | 提示词与 Jinja2 | LlamaIndex |
| [47](https://blog.csdn.net/qq_36301061/article/details/162641375) | 多模态提示词 | LlamaIndex |
| [48](https://blog.csdn.net/qq_36301061/article/details/162673559) | Document 与加载器 | LlamaIndex |
| [49](https://blog.csdn.net/qq_36301061/article/details/162702543) | LlamaParse | LlamaIndex |
| [50](https://blog.csdn.net/qq_36301061/article/details/162738227) | 文本分割器 | LlamaIndex |
| [51](https://blog.csdn.net/qq_36301061/article/details/162771151) | HTML、JSON、Markdown 分割 | LlamaIndex |
| [52](https://blog.csdn.net/qq_36301061/article/details/162787400) | 文档存储 | LlamaIndex |
| [53](https://blog.csdn.net/qq_36301061/article/details/162809512) | 向量存储与索引存储 | LlamaIndex |
| [54](https://blog.csdn.net/qq_36301061/article/details/162848890) | 检索器 | LlamaIndex |
| [55](https://blog.csdn.net/qq_36301061/article/details/162880890) | 响应合成器 | LlamaIndex |
| [56](https://blog.csdn.net/qq_36301061/article/details/162912195) | 查询引擎源码 | LlamaIndex |
| [57](https://blog.csdn.net/qq_36301061/article/details/162944201) | 查询引擎配置 | LlamaIndex |
| [58](https://blog.csdn.net/qq_36301061/article/details/162977742) | 聊天引擎 | LlamaIndex |
| [59](https://blog.csdn.net/qq_36301061/article/details/162987579) | 四种记忆 | LlamaIndex |
| [60](https://blog.csdn.net/qq_36301061/article/details/163015052) | Redis 记忆 | LlamaIndex |
| [61](https://blog.csdn.net/qq_36301061/article/details/163053761) | RAG 问题复盘 | LlamaIndex 高级 |
| [62](https://blog.csdn.net/qq_36301061/article/details/163085476) | RAG 优化 | LlamaIndex 高级 |
| [63](https://blog.csdn.net/qq_36301061/article/details/163140391) | 检索、变体、微调 | LlamaIndex 高级 |
| [64](https://blog.csdn.net/qq_36301061/article/details/163174139) | 摄取管道 | LlamaIndex 高级 |
| [65](https://blog.csdn.net/qq_36301061/article/details/163182732) | 元数据提取 | LlamaIndex 高级 |
| [66](https://blog.csdn.net/qq_36301061/article/details/163208436) | Redis 缓存 | LlamaIndex 高级 |
| [67](https://blog.csdn.net/qq_36301061/article/details/163250093) | 查询优化 | LlamaIndex 高级 |
| [68](https://blog.csdn.net/qq_36301061/article/details/163281117) | 父子索引 | LlamaIndex 高级 |
| [69](https://blog.csdn.net/qq_36301061/article/details/163343427) | 元数据索引 | LlamaIndex 高级 |
| [70](https://blog.csdn.net/qq_36301061/article/details/163375958) | BM25、RRF、归一化、DBSF | LlamaIndex 高级 |
| [71](https://blog.csdn.net/qq_36301061/article/details/163387655) | 路由检索器 | LlamaIndex 高级 |
| [72](https://blog.csdn.net/qq_36301061/article/details/163411475) | SQL 检索器 | LlamaIndex 高级 |
| [73](https://blog.csdn.net/qq_36301061/article/details/163511712) | 文档过滤 | LlamaIndex 高级 |
| [74](https://blog.csdn.net/qq_36301061/article/details/163542356) | 重排序 | LlamaIndex 高级 |
| [75](https://blog.csdn.net/qq_36301061/article/details/163572329) | 上下文优化 | LlamaIndex 高级 |
| [76](https://blog.csdn.net/qq_36301061/article/details/163572811) | 时间衰减 | LlamaIndex 高级 |
| [77](https://blog.csdn.net/qq_36301061/article/details/163581667) | 上下文扩展 | LlamaIndex 高级 |
| [78](https://blog.csdn.net/qq_36301061/article/details/163586659) | MinerU 三种调用 | LlamaIndex 高级 |
| [79](https://blog.csdn.net/qq_36301061/article/details/163604401) | Markdown 元素分割 | LlamaIndex 高级 |
| [80](https://blog.csdn.net/qq_36301061/article/details/163609144) | 金融助手 | LlamaIndex 高级 |
| [81](https://blog.csdn.net/qq_36301061/article/details/163644915) | 知识图谱 RAG | 图 RAG |
| [82](https://blog.csdn.net/qq_36301061/article/details/163674772) | 隐式关系抽取 | 图 RAG |
| [83](https://blog.csdn.net/qq_36301061/article/details/163675878) | 默认抽取器 | 图 RAG |
| [84](https://blog.csdn.net/qq_36301061/article/details/163704892) | 动态抽取器 | 图 RAG |
| [85](https://blog.csdn.net/qq_36301061/article/details/163705899) | Schema 抽取 | 图 RAG |
| [86](https://blog.csdn.net/qq_36301061/article/details/163733701) | LightRAG 中文 | 图 RAG |
| [87](https://blog.csdn.net/qq_36301061/article/details/163762777) | 评估说明 | 评估 |
| [88](https://blog.csdn.net/qq_36301061/article/details/163775481) | Ragas 公式 | 评估 |
| [89](https://blog.csdn.net/qq_36301061/article/details/163792304) | Ragas 实例 | 评估 |
| [90](https://blog.csdn.net/qq_36301061/article/details/163832313) | LlamaIndex 评估逻辑 | 评估 |
| [91](https://blog.csdn.net/qq_36301061/article/details/163862934) | 响应评估示例 | 评估 |
| [92](https://blog.csdn.net/qq_36301061/article/details/163863824) | 检索评估示例 | 评估 |
| [93](https://blog.csdn.net/qq_36301061/article/details/163895246) | Dify 使用 | Dify 与应用 |
| [94](https://blog.csdn.net/qq_36301061/article/details/163926246) | Dify 大模型与知识库 | Dify 与应用 |
| [95](https://blog.csdn.net/qq_36301061/article/details/163954868) | Dify Agent | Dify 与应用 |
| [96](https://blog.csdn.net/qq_36301061/article/details/163966418) | 分类器、分支、人工 | Dify 与应用 |
| [97](https://blog.csdn.net/qq_36301061/article/details/164001672) | 迭代、循环、赋值 | Dify 与应用 |
| [98](https://blog.csdn.net/qq_36301061/article/details/164031562) | 模板、聚合、参数提取 | Dify 与应用 |
| [99](https://blog.csdn.net/qq_36301061/article/details/164124413) | HTTP 与解析 | Dify 与应用 |
| [100](https://blog.csdn.net/qq_36301061/article/details/164154736) | 前端问答 | Dify 与应用 |
| [101](https://blog.csdn.net/qq_36301061/article/details/164167193) | 登录接口 | Dify 与应用 |
| [102](https://blog.csdn.net/qq_36301061/article/details/164185288) | 上传接口 | Dify 与应用 |
| [103](https://blog.csdn.net/qq_36301061/article/details/164229646) | 聊天接口 | Dify 与应用 |
| [104](https://blog.csdn.net/qq_36301061/article/details/164303713) | Agent 与主流框架 | Agent |
| [105](https://blog.csdn.net/qq_36301061/article/details/164338823) | LangChain 智能体框架 | Agent |
| [106](https://blog.csdn.net/qq_36301061/article/details/164376526) | uv 与 PyCharm | Agent |
| [107](https://blog.csdn.net/qq_36301061/article/details/164395287) | 第一个 Agent | Agent |
| [108](https://blog.csdn.net/qq_36301061/article/details/164422438) | State 与 LangSmith | Agent |
| [109](https://blog.csdn.net/qq_36301061/article/details/164499127) | 在线与本地模型 | Agent |
| [110](https://blog.csdn.net/qq_36301061/article/details/164630220) | Messages | Agent |
| [111](https://blog.csdn.net/qq_36301061/article/details/164755873) | Tools 与 Pydantic | Agent |
| [112](https://blog.csdn.net/qq_36301061/article/details/165007046) | Function Calling | Agent |
| [113](https://blog.csdn.net/qq_36301061/article/details/165091113) | 短期记忆与 Postgres | Agent |
| [114](https://blog.csdn.net/qq_36301061/article/details/165181483) | 自定义 State | Agent |
| [115](https://blog.csdn.net/qq_36301061/article/details/165361258) | 三种流式 | Agent |
| [116](https://blog.csdn.net/qq_36301061/article/details/165489249) | 事件流 | Agent |
| [117](https://blog.csdn.net/qq_36301061/article/details/165627436) | 原生结构化输出 | Agent |
| [118](https://blog.csdn.net/qq_36301061/article/details/165755555) | ToolStrategy | Agent |
| [119](https://blog.csdn.net/qq_36301061/article/details/165889930) | Runtime | Agent |
| [120](https://blog.csdn.net/qq_36301061/article/details/165984301) | 摘要中间件 | Agent |
| [121](https://blog.csdn.net/qq_36301061/article/details/166140061) | TodoList 中间件 | Agent |
| [122](https://blog.csdn.net/qq_36301061/article/details/166254164) | 动态提示词 | Agent |
| [123](https://blog.csdn.net/qq_36301061/article/details/166490511) | 生命周期钩子 | Agent |
| [124](https://blog.csdn.net/qq_36301061/article/details/166601293) | trim_messages | Agent |
| [125](https://blog.csdn.net/qq_36301061/article/details/166633858) | RemoveMessage | Agent |
| [126](https://blog.csdn.net/qq_36301061/article/details/166633825) | before_agent | Agent |
| [127](https://blog.csdn.net/qq_36301061/article/details/166679418) | JSON 修复 | Agent |
| [128](https://blog.csdn.net/qq_36301061/article/details/166725674) | wrap_model_call | Agent |
| [129](https://blog.csdn.net/qq_36301061/article/details/166793536) | AgentMiddleware | Agent |
| [130](https://blog.csdn.net/qq_36301061/article/details/166848240) | 六种设计模式 | 多 Agent |
| [131](https://blog.csdn.net/qq_36301061/article/details/166903280) | MAS | 多 Agent |
| [132](https://blog.csdn.net/qq_36301061/article/details/166939067) | AutoGen | 多 Agent |
| [133](https://blog.csdn.net/qq_36301061/article/details/166987284) | CrewAI | 多 Agent |
| [134](https://blog.csdn.net/qq_36301061/article/details/167029672) | LangChain 多智能体 | 多 Agent |
| [135](https://blog.csdn.net/qq_36301061/article/details/167074595) | SubAgents | 多 Agent |
| [136](https://blog.csdn.net/qq_36301061/article/details/167124681) | Handoff | 多 Agent |
| [137](https://blog.csdn.net/qq_36301061/article/details/167177335) | Skills 说明 | 多 Agent |
| [138](https://blog.csdn.net/qq_36301061/article/details/167215428) | Skills 文件结构 | 多 Agent |
| [139](https://blog.csdn.net/qq_36301061/article/details/167281720) | 渐进式加载 | 多 Agent |
| [140](https://blog.csdn.net/qq_36301061/article/details/167396560) | 执行 Skills | 多 Agent |
| [141](https://blog.csdn.net/qq_36301061/article/details/167496516) | 编写 Skills | 多 Agent |
| [142](https://blog.csdn.net/qq_36301061/article/details/167587174) | Skills 实现多 Agent | 多 Agent |
| [143](https://blog.csdn.net/qq_36301061/article/details/167701587) | Router 与四种架构 | 多 Agent |
| [144](https://blog.csdn.net/qq_36301061/article/details/167812076) | 为什么要 LangGraph | LangGraph |
| [145](https://blog.csdn.net/qq_36301061/article/details/167920033) | State、Node、Edge、Graph | LangGraph |
| [146](https://blog.csdn.net/qq_36301061/article/details/168028943) | 三套 State | LangGraph |
| [147](https://blog.csdn.net/qq_36301061/article/details/168145197) | Reducer | LangGraph |
| [148](https://blog.csdn.net/qq_36301061/article/details/168227248) | add_messages、MessagesState | LangGraph |
| [149](https://blog.csdn.net/qq_36301061/article/details/168329558) | 节点与并行 | LangGraph |
| [150](https://blog.csdn.net/qq_36301061/article/details/168452452) | 条件边与条件入口 | LangGraph |
| [151](https://blog.csdn.net/qq_36301061/article/details/168560021) | Send | LangGraph |
| [152](https://blog.csdn.net/qq_36301061/article/details/168660525) | Command | LangGraph |
| [153](https://blog.csdn.net/qq_36301061/article/details/168771935) | Runtime | LangGraph |
| [154](https://blog.csdn.net/qq_36301061/article/details/168939098) | 递归限制 | LangGraph |
| [155](https://blog.csdn.net/qq_36301061/article/details/168976810) | RetryPolicy | LangGraph |
| [156](https://blog.csdn.net/qq_36301061/article/details/169057567) | 可视化 | LangGraph |
| [157](https://blog.csdn.net/qq_36301061/article/details/169169556) | ReAct 工具循环 | LangGraph |
| [158](https://blog.csdn.net/qq_36301061/article/details/169266937) | 子图 | LangGraph |
| [159](https://blog.csdn.net/qq_36301061/article/details/169339701) | 交接 | LangGraph |
| [160](https://blog.csdn.net/qq_36301061/article/details/169364657) | 主管架构 | LangGraph |
| [161](https://blog.csdn.net/qq_36301061/article/details/169463669) | 线程与检查点 | LangGraph |
| [162](https://blog.csdn.net/qq_36301061/article/details/169563840) | 检查点的作用 | LangGraph |
| [163](https://blog.csdn.net/qq_36301061/article/details/169628861) | Replay | LangGraph |
| [164](https://blog.csdn.net/qq_36301061/article/details/169732062) | update_state | LangGraph |
| [165](https://blog.csdn.net/qq_36301061/article/details/169847895) | Store | LangGraph |
| [166](https://blog.csdn.net/qq_36301061/article/details/169950077) | Store 语义检索 | LangGraph |
| [167](https://blog.csdn.net/qq_36301061/article/details/170051540) | 图中使用 Store | LangGraph |
| [168](https://blog.csdn.net/qq_36301061/article/details/170147912) | 七种流式 | LangGraph |
| [169](https://blog.csdn.net/qq_36301061/article/details/170200953) | 多模式与关闭流式 | LangGraph |
| [170](https://blog.csdn.net/qq_36301061/article/details/170245625) | Redis | LangGraph |
| [171](https://blog.csdn.net/qq_36301061/article/details/170288440) | Postgres | LangGraph |
| [172](https://blog.csdn.net/qq_36301061/article/details/170351740) | 修剪消息 | LangGraph |
| [173](https://blog.csdn.net/qq_36301061/article/details/170453720) | 删除消息 | LangGraph |
| [174](https://blog.csdn.net/qq_36301061/article/details/170558098) | 总结消息 | LangGraph |
| [175](https://blog.csdn.net/qq_36301061/article/details/170645011) | interrupt | LangGraph |
| [176](https://blog.csdn.net/qq_36301061/article/details/170743373) | MCP 是什么 | MCP |
| [177](https://blog.csdn.net/qq_36301061/article/details/170761445) | Host、Client、Server | MCP |
| [178](https://blog.csdn.net/qq_36301061/article/details/170803615) | FastMCP Server | MCP |
| [179](https://blog.csdn.net/qq_36301061/article/details/170869088) | fastmcp Client | MCP |
| [180](https://blog.csdn.net/qq_36301061/article/details/170927555) | stdin、stdout、stderr | MCP |
| [181](https://blog.csdn.net/qq_36301061/article/details/170988044) | STDIO 与 Streamable HTTP | MCP |
| [182](https://blog.csdn.net/qq_36301061/article/details/171048811) | LangChain 连接 MCP | MCP |
| [183](https://blog.csdn.net/qq_36301061/article/details/171111938) | DeepAgents 四项能力 | DeepAgents |
| [184](https://blog.csdn.net/qq_36301061/article/details/171141926) | Harness 与 create_deep_agent | DeepAgents |
