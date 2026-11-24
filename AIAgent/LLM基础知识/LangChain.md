# LangChain

> 来源：尚硅谷《大模型技术之 LangChain》V1.0.2。这是阅读稿，示例只保留调用关系和参数，密钥用环境变量名。第 5.4 节是记忆分层，第 6 节是对照面试题补的工具协议和检索约束。LCEL 留着读源码，面试主答不背组件名单。

LangChain 是 2022 年 10 月由 Harrison Chase 发起的开源框架，用来开发由大语言模型驱动的应用：Agent、问答、文档搜索。它比 ChatGPT 公开发布还早大约一个月。

直接调各家 API 也能做应用。框架解决的是三件事：少写各厂商出入参的适配；换模型时调用方式不变；Agent、检索、提示词、输出解析有现成组合方式。业务代码因此可以停在「组流程」，少停在「对接某家 SDK」。

- 仓库：https://github.com/langchain-ai/langchain
- 文档：https://docs.langchain.com/oss/python/langchain/overview
- API：https://reference.langchain.com/python/langchain/

课程环境是 Python 3.12 虚拟环境，依赖放在课程资料的 `requirements.txt`。某节要新包时，再单独安装。

## 1. 包和四块能力

| 包 | 作用 |
|---|---|
| `langchain` | 构建 LLM 应用的主入口 |
| `langchain-core` | 核心接口和抽象，Runnable 在这里 |
| `langchain-openai`、`langchain-deepseek` 等 | 某个模型或工具的集成包。文本模型、工具、文档加载、向量库都走集成包 |
| `langchain-mcp-adapters` | 把 MCP 工具接进 LangChain / LangGraph |
| `langchain-text-splitters` | 文档切分 |
| `langchain-tests` | 校验集成包实现的标准测试 |
| `langchain-classic` | 1.0 之前的遗留实现 |

逻辑上分成四块：

| 模块 | 做什么 |
|---|---|
| Model I/O | 管输入和输出。Format 用模板整理输入，Predict 调用模型，Parse 把输出收成 JSON 等稳定结构 |
| Chains | 把多个组件串成一条可调用的链 |
| Retrieval | RAG。先检索外部资料，再交给模型生成 |
| Agents | 模型自己决定下一步，并调用工具 |

课程后半的判断：Agent 流行之后，框架重心从一次性的 Chain 转到可循环的 Agent，新写法用 LangGraph。Chain 和 LCEL 仍要认得，读源码时会碰到。

## 2. Model I/O：把模型调用统一掉

Model I/O 就是三步：准备输入、调用模型、解析输出。

### 2.1 平台和密钥

课程提到的接入方：CloseAI（国外模型代理）、OpenRouter、阿里云百炼、百度千帆、硅基流动。用法都是注册后拿 API Key 和 Base URL。

密钥不要写进代码，也不要放进 git。两种放法：

- 项目用 `.env`，`python-dotenv` 的 `load_dotenv()` 读入，再用 `os.getenv`。适合正式项目。
- Windows 全局环境变量。适合学习时反复用同一组 Key，省得每个脚本都 `load_dotenv`。

`.env` 里常见两项：`OPENAI_API_KEY`、`OPENAI_BASE_URL`。

### 2.2 不经过 LangChain 时，各家怎么调

多数国产和开源模型兼容 OpenAI 的调用形状，所以 OpenAI SDK 是默认入口。复杂能力（结构化输出等）各家参数仍不一样，这正是后面要包一层的原因。

两套 OpenAI API：

| API | 位置 | 要点 |
|---|---|---|
| Chat Completions | `client.chat.completions.create` | 经典接口。`model` + `messages`，结果在 `choices[0].message.content` |
| Responses | `client.responses.create` | 2025 年中的新接口。可带服务端内置工具（如 `web_search`），也可由服务端维护短期状态。文本在 `response.output_text` |

Gemini 不走这套 SDK，用 `google-genai`：`genai.Client` 配 `api_key` 和 `http_options.base_url`，再 `models.generate_content`。

### 2.3 用 LangChain 调模型

步骤固定：构造 ChatModel，传入字符串或消息列表，再解析返回值。

两种构造方式等价。`init_chat_model` 内部就是去调对应集成包里的类。

```python
from langchain.chat_models import init_chat_model

llm = init_chat_model(
    model="gpt-4o-mini",
    model_provider="openai",
    base_url=os.getenv("OPENAI_BASE_URL"),
    api_key=os.getenv("OPENAI_API_KEY"),
)
resp = llm.invoke("你好")   # AIMessage，正文在 resp.content
```

等价写法是 `from langchain_openai import ChatOpenAI` 后直接实例化。DeepSeek 用 `langchain-deepseek`，换厂商只换这一处。不传 `base_url` / `api_key` 时，初始化也会去读同名环境变量。

`init_chat_model` 要记住的参数：

| 参数 | 含义 |
|---|---|
| `model` | 模型名，如 `gpt-4o-mini` |
| `model_provider` | 厂商，如 `openai` |
| `base_url` | 请求打到哪 |
| `api_key` | 身份校验 |
| `temperature` | 越大越随机，越小越稳定。背后是采样 |
| `timeout` | 取消前最多等多少秒 |
| `max_tokens` | 限制这次回复的 token 数 |
| `max_retries` | 失败后最多重试几次 |

Token 是模型处理的最小单位，按 token 计费，生成也是一个 token 一个 token 往外吐。课程给出的粗算：1 个中文 token 大约 1 到 1.8 个汉字，1 个英文 token 大约 3 到 4 个字母。可视化工具：OpenAI Tokenizer、百度智能云 Tokenizer。

### 2.4 传入什么、怎么调用

只生成一次、不保留历史时，可以直接传字符串。要表达角色和历史，传消息列表。四种消息：

| 类型 | 谁在说话 |
|---|---|
| `SystemMessage` | 系统指令：人设、语气、回答规范 |
| `HumanMessage` | 用户。可带额外元数据 |
| `AIMessage` | 模型回复。除文本外还有工具调用、token 用量 |
| `ToolMessage` | 工具执行结果。只在工具调用里出现 |

同一条消息还有两种简写，和消息类等价：

- 元组：`("system", "...")`、`("user", "...")`
- 字典：`{"role": "system", "content": "..."}`

调用方式四套，名字在后面所有 Runnable 上重复出现：

| 方法 | 作用 |
|---|---|
| `invoke` / `ainvoke` | 一次输入一次输出。异步用 `ainvoke`，生产里用来提高并发 |
| `stream` | 返回迭代器，按 chunk 打印就是打字机效果，读 `chunk.content` |
| `batch` | 一组输入并行发出、统一收回。同步实现底层用线程池 |

### 2.5 本地模型：Ollama

Ollama 把主流 LLaMA 架构开源模型（Qwen、DeepSeek 等）的下载、启动、本地推理包成一套。课程的定位：适合原型和本地开发。生产部署用 vLLM 这一类框架。

安装：Windows 走安装包，Linux 用官网的 `install.sh`（检查环境、下二进制、建用户和服务、拉起进程）。`ollama run qwen3:8b` 下载并运行。模型很大时，把 Ollama 的 model location 指到已有的模型目录再运行。

LangChain 侧安装 `langchain-ollama`，用 `ChatOllama(model="qwen3:8b")`。服务不在默认地址时加 `base_url="http://localhost:11434"`。之后同样是 `invoke`。

### 2.6 把自然语言收成结构

对话可以直接要一段文字。生产里的中间结果通常要 JSON：下游要字段，不能靠人眼从句子里抠。

两条路：

1. 提示词约束。用 Pydantic 定义 schema，交给 `JsonOutputParser(pydantic_object=...)`。`get_format_instructions()` 放进 system 消息，模型按说明输出，再用 parser 的 `invoke` 收成 dict。模型不够强时会幻觉：JSON 语法坏了，或字段对不上 schema。
2. 厂商的结构化输出。提示词里不必写「请输出 JSON」，API 自己约束。OpenAI 用 `chat.completions.parse(..., response_format=某个 BaseModel)`，结果在 `message.parsed`。Gemini 在 `generate_content` 的 config 里设 `response_mime_type="application/json"` 和 `response_json_schema`，再用 `model_validate_json`。

LangChain 把第 2 条收成一个方法。ChatModel 都有 `with_structured_output(schema=某个 BaseModel)`，返回一个新模型对象，`invoke` 的结果直接是该类型的实例。换 OpenAI 或 Gemini，只改模型怎么构造，解析代码不动。

`langchain_core.output_parsers` 里还有一批解析器。JSON 能覆盖大多数场景，其余知道名字即可：

- 字符串：`StrOutputParser`
- JSON：`JsonOutputParser`、`SimpleJsonOutputParser`
- Pydantic：`PydanticOutputParser`
- XML：`XMLOutputParser`
- 列表：`CommaSeparatedListOutputParser`、`MarkdownListOutputParser`、`NumberedListOutputParser`
- 工具调用：`JsonOutputToolsParser`、`JsonOutputKeyToolsParser`、`PydanticToolsParser`

### 2.7 提示词模板

固定字符串换一个产品就要改代码。模板把变量留成占位符，调用时用字典填进去。

常用两个：`PromptTemplate`（一条字符串）和 `ChatPromptTemplate`（一组带角色的消息）。输入是字典，键就是占位符；输出是 `PromptValue`。它存在的理由是同一份提示既能交给聊天模型，也能转成字符串或消息列表。

```python
chat_prompt_template = ChatPromptTemplate.from_messages([
    ("system", "你是一个专业的评论员"),
    ("human", "请评价{product}的优缺点，包括{aspect1}和{aspect2}。"),
])
messages = chat_prompt_template.invoke(
    {"product": "iPhone 15", "aspect1": "性能", "aspect2": "外观"}
)
```

`PromptTemplate` 用 `from_template("讲一个关于{topic}的笑话")`，变量列表写在 `input_variables`。

## 3. Runnable 和 LCEL

模型、提示词、解析器、检索器、编译后的 LangGraph，底层都实现 Runnable。Runnable 是可以调用、批处理、流式、变换和组合的工作单元。

统一方法是为了能拼起来。没有这层时，提示词用 `.format()`，模型用 `.generate()`，解析器用 `.parse()`，工具用 `.run()`，每接一个组件就要改调用。统一之后全是 `invoke`，再用管道接上：

```python
chain = prompt_template | llm | StrOutputParser()
resp = chain.invoke({"topic": "人工智能"})
```

LCEL（LangChain Expression Language）就是这种声明式组合：用已有 Runnable 造出新的 Runnable。造出来的东西叫链，链自己也是 Runnable。

两个基本原语：

| 原语 | 行为 |
|---|---|
| `RunnableSequence` | 顺序执行，上一个的输出是下一个的输入。`a \| b` 就是 `RunnableSequence([a, b])` |
| `RunnableParallel` | 同一份输入同时喂给多个 Runnable。同步用 `ThreadPoolExecutor`，异步用 `asyncio.gather`。每个分支的名字变成结果字典的键 |

并行还有一种不写类的形式：字典就是并行，管道后面再接汇总。

```python
map_chain = {
    "paragraph_1": paragraph_1_chain,
    "paragraph_2": paragraph_2_chain,
} | summary_chain
```

字典里两条链同时跑，输出键名正好填进 `summary_chain` 模板的 `{paragraph_1}`、`{paragraph_2}`。

其余 Runnable，课程只要求认得：

| 组件 | 作用 |
|---|---|
| `RunnableLambda` | 把普通函数包成 Runnable |
| `RunnableBranch` | 按输入做 if-else，分到不同函数 |
| `RunnablePassthrough` | 原样传下去。用来在流水线里保留上下文，或往输出里加键 |
| `RunnableWithFallbacks` | 失败时改走备用 Runnable |

## 4. RAG

### 4.1 为什么要检索

模型有三类硬伤：

- 知识滞后。预训练、微调和安全评估都很贵，新事实进不了权重。
- 知识缺失。私有领域的细节没在训练语料里，专业问题答不准。
- 幻觉。错误陈述、编造事实、推理出错、复杂语境理解失败。来源包括：训练数据里的错被学会了、过度泛化、没学到深层含义、遇到没见过的领域就编。

金融和医疗里一次错误就可能是事故，非专业的人还看不出来。没有方案能把幻觉清到零。

RAG（Retrieval-Augmented Generation，检索增强生成）的做法：生成时配上刚检索到的外部资料，相当于回答前先给模型一本参考书。私有知识库很大、又要靠模型回答时，微调和 RAG 是两条主路。LangChain 把 RAG 收成加载、切分、嵌入、存储、检索、生成。

和另外两条路比：

| | 优点 | 代价 |
|---|---|---|
| 相对提示词工程 | 上下文和样本更丰富，用户不必把背景全部写进问题 | |
| 相对微调 | 时效性和可靠性更好改，换文档不用重新训练；业务数据不必写进权重 | |
| RAG 自己的代价 | | 每次都要查外部系统，延迟更高；塞进上下文的资料消耗 token |

两条流程：

1. 索引：加载数据源，切成小块，做嵌入，存进向量库。
2. 检索生成：用问题取出相关块，和问题一起放进提示词，再生成。

### 4.2 文档加载

文件格式不统一，加载器把它们收成同一种 `Document`。所有加载器实现 `BaseLoader`：`load()` 一次拿全，`lazy_load()` 延迟逐个给。集成列表在官方 document loaders 页。

`Document` 三个字段：

| 字段 | 含义 |
|---|---|
| `page_content` | 文本 |
| `metadata` | 来源等字典 |
| `id` | 可选标识 |

Markdown 是半结构化的。Unstructured 的解析顺序：先按标题等结构切成 element，同一标题下再按空行切段落。`UnstructuredMarkdownLoader(..., mode="elements")`，依赖 `markdown`、`langchain_community`、`unstructured[md]`。

`.docx` 本质是 XML：`<w:p>` 是段落，`<w:r>` 是一段文本。层级比 Markdown 松，标题常常只是正文加了手工样式，解析器对不齐。`UnstructuredWordDocumentLoader` 的 `mode`：`single` 整个文件一个 Document，`elements` 按元素切。课程指出它对 Word 基本按换行切，认不出标题层级。层级不重要时够用；层级重要时改用 MinerU，先得到 Markdown，再用 Markdown 加载器。

PDF 更难：扫描件、电子文本、混合件；单栏、双栏、竖排；还有页眉页脚、表格、公式、化学式、图片。要做文本提取、版面检测、表格和公式识别。

MinerU 把 PDF 转成 Markdown、JSON。课程用的模型是 `opendatalab/MinerU2.5-2509-1.2B`。它分两阶段：先在下采样图上做全局版面分析，再对文本、公式、表格的原分辨率裁剪做细识别。支持 PDF、Word、PPT、图片，以及 OCR、公式、表格。可以 Docker 本地部署（仓库 `opendatalab/MinerU`），也可以走官网 API。

API 两步：带 `MINERU_TOKEN` 向 `https://mineru.net/api/v4/file-urls/batch` 申请上传地址（`model_version` 可用 `vlm`），PUT 文件；再用 `batch_id` 轮询 `.../extract-results/batch/{batch_id}`，状态变成 `done` 后取 `full_zip_url`。输出格式以官方 output files 文档为准。下一步最简单的接法：用它产出的 Markdown，再交给 Markdown 加载器。

### 4.3 切分

整篇 Document 直接塞进提示词不合适，两个原因：

- 无关段落会干扰生成。长上下文里，相关信息夹在中间时，模型利用率明显下降（多文档问答、键值检索上都有这个现象）。
- 模型有最大输入 token。文档太长会被截断。

所以索引和检索的单位是 Chunk。三种策略，按语义完整性来选：

| 策略 | 做法 | 取舍 |
|---|---|---|
| 固定字符数或 token 数 | 数够就切 | 可能把句子切断 |
| 递归分隔符 | 按分隔符从粗到细切，同时卡住长度 | 课程采用的折中，尽量不切断句子 |
| 语义切分 | 相邻句子组成句组，嵌入后看余弦距离，距离分布的第 N 百分位当阈值，跳变大的地方切开，再合并过短的段 | 语义最完整，慢，块长度可能很不均 |

`RecursiveCharacterTextSplitter`（包 `langchain-text-splitters`）是第二种。默认分隔符 `["\n\n", "\n", " ", ""]`，按顺序试，直到块足够小，因此优先保住段落，其次句子，最后词。中文材料把句号、问号、逗号加进列表。

| 参数 | 含义 |
|---|---|
| `separators` | 从粗到细的切分符 |
| `chunk_size` | 块的最大长度 |
| `chunk_overlap` | 相邻块重叠多少，用来保住跨界的语义 |
| `length_function` | 怎么量长度，默认 `len`。换成 token 计数函数就按 token 切 |
| `add_start_index` | 在 metadata 里记下这块在原文的起始位置 |

对 Document 列表用 `split_documents`。

### 4.4 嵌入

切完要变成向量，检索时和问题的向量比相似度。BERT 能出向量，但没为句子嵌入优化。Sentence-BERT 改了结构和预训练任务，句子向量可以直接用余弦相似度比，找相似句的开销小很多。论文：https://arxiv.org/pdf/1908.10084

| 模型 | 机构 | 维度 | 序列长度 | 备注 |
|---|---|---|---|---|
| bge-large-zh | BAAI | 1024 | 512 | 开源，中文 |
| bge-base-zh | BAAI | 768 | 512 | 开源，中文 |
| bge-small-zh | BAAI | 512 | 512 | 开源，中文 |
| bge-m3 | BAAI | 1024 | 8192 | 开源，多语言，能同时出稠密和稀疏向量 |
| text-embedding-3-small | OpenAI | 1536 | 8192 | 多语言 |
| text-embedding-3-large | OpenAI | 3072 | 8192 | 多语言 |

LangChain 的 `Embedding` 抽象两个方法：`embed_documents(list[str]) -> list[list[float]]` 给文档，`embed_query(str) -> list[float]` 给问题。实现类包括 `HuggingFaceEmbeddings`、`OpenAIEmbeddings`。本地开源模型：

```python
embed_model = HuggingFaceEmbeddings(model_name=r".\assets\models\bge-base-zh-v1.5")
query_result = embed_model.embed_query("你好，世界")
docs_result = embed_model.embed_documents(["你好，世界", "你好，世界"])
```

依赖 `sentence-transformers`、`langchain_huggingface`。

### 4.5 向量库

关系库擅长精确条件（时间、地点、型号）。按内容相似性找照片、商品、段落，要把对象放进向量空间，检索的是最相近的一批，不是唯一匹配。图片、视频、商品向量化之后，就是以图搜图、相关推荐、相似商品。

LangChain 给向量库一套标准接口，本地库和云库可以换。课程点名的几个：

| 库 | 特点 |
|---|---|
| FAISS | 稠密向量的相似性搜索和聚类 |
| Chroma | 轻量，API 极简 |
| Milvus | 为向量搜索做的云原生库，从原型到十亿级 |
| Pgvector | PostgreSQL 扩展，增加向量类型和相似性搜索 |
| Redis | 内存数据结构，支持向量搜索 |
| Elasticsearch | 结构化、非结构化、向量放在同一套列式存储里 |

课程后文以 Milvus 为准。

### 4.6 Milvus

搜索、插入、索引和压缩被拆成可并行的进程，查询节点、数据节点、索引节点可以各自纵向或横向扩展。数据组织是数据库、Collection、实体，对应库、表、行。Collection 是列固定、行可变的二维表，列是字段，行是实体。有哪些列、什么类型、什么索引，由 Collection Schema 决定。

向量字段：

| 种类 | 类型 | 是什么 |
|---|---|---|
| 稠密 | `FLOAT_VECTOR` | 32 位浮点列表。通常由 Transformer Encoder（如 Sentence-BERT）生成 |
| 稠密 | `FLOAT16_VECTOR` | 16 位半精度 |
| 稠密 | `BFLOAT16_VECTOR` | 16 位，指数范围接近 float32，尾数更短 |
| 稠密 | `INT8_VECTOR` | 8 位有符号整数 |
| 稀疏 | `SPARSE_FLOAT_VECTOR` | 非零值加它的序号。表示关键词和权重 |
| 二进制 | `BINARY_VECTOR` | 0/1 列表 |

稀疏向量早期用 TF-IDF 这类词袋统计，现在也可以由模型直接学习。BGE-M3 一次编码能同时给 `dense_vecs` 和 `lexical_weights`；`convert_id_to_token` 把稀疏向量里的 id 变回 token。

标量字段用来存元数据，检索时做过滤，提高结果是否「对」而不只是「近」：`VARCHAR`、`BOOL`、`INT8/16/32/64`、`FLOAT`、`DOUBLE`、`ARRAY`、`JSON`。

Schema 规则：一个主键，最多四个向量字段，外加若干标量。主键只能是 Int64 或 VarChar。默认插入时要带主键；打开 `auto_id` 后由 Milvus 生成，插入数据里就不要再带主键。

索引是另建的加速结构，类型跟字段走。

稠密向量两种：

- HNSW（分层导航小世界）。多层图，底层是全部点，上层是抽样。搜索从顶层固定入口开始，在当前层贪心走向更近的邻居，到局部最近后降到下一层，底层再做一次细化。精度高、延迟低，分层图占内存。
- FLAT。查询向量和库里每条向量都比一遍，没有预处理。召回率 100%，最慢。

稀疏向量用 `SPARSE_INVERTED_INDEX`：倒排先找出包含查询 token 的文档，再算相似度。两种 metric：

- IP（内积）。两边都有的维度，权重相乘再相加。例如查询 `{27:0.7, 100:0.4}` 对文档 `{27:0.5, 100:0.3, 5369:0.6}`，得分是 `0.5×0.7 + 0.3×0.4 = 0.47`。
- BM25。对重合词加权，综合词频 TF、逆文档频率 IDF（出现该词的文档越少，IDF 越高）、文档长度归一化。

部署三档：

| 版本 | 场景 | 限制 |
|---|---|---|
| Milvus Lite | `pip install pymilvus[milvus-lite]`，本机轻量 | 只用 FLAT，不管建库时写了什么索引；只支持 macOS 和 Linux |
| Standalone | 单机 Docker | 课程演示用这档 |
| Distributed | Kubernetes | 生产集群 |

课程资料提供 `milvus_image.tar`，`docker load -i` 之后，Linux 用 `standalone_embed.sh start`，Windows Docker Desktop 用 `standalone.bat start`。图形客户端 Attu 用 token 连上去看数据。代码里的地址是 `http://localhost:19530`。

建库三步：schema、index、`create_collection`。课程示例：`auto_id=True`；`id` 为 INT64 主键；`vector` 为 1024 维 `FLOAT_VECTOR`；`text` 为 VARCHAR；`metadata` 为 JSON；`sparse_vector` 为 `SPARSE_FLOAT_VECTOR`。稠密索引用 HNSW、metric `L2`；稀疏索引用 `SPARSE_INVERTED_INDEX`、metric `IP`。

插入：把实体做成 `list[dict]`，字段对齐 schema，`client.insert`。课程的流水线是 Word 加载、递归切分、BGE-M3 同时出稠密和稀疏、组装后插入。`auto_id` 打开时字典里不写 `id`。返回值能看出插入条数。删用 `client.delete`，可以传 id 列表，也可以写过滤表达式，如 `id in [...]`，返回里的 `delete_count` 是删掉的条数。

检索分向量和标量。

向量检索关注四项：查询向量（维度必须和字段一致）、`anns_field`（比哪一列）、`limit`（TopK）、`metric_type`（`COSINE`、`L2` 或 `IP`，要和索引、向量语义一致）。支持精确 KNN 和近似 ANN。`client.search` 走单路。

混合检索把稠密和稀疏各做一路召回，再用重排序融合。重排序不扩大召回，只在已有候选里把顺序排准。课程讲的是 RRF：收集每一路的名次，按名次换成倒数分数再相加。`rank_i(d)` 是文档 d 在第 i 路的名次，平滑常数 `k` 通常取 60。代码是两个 `AnnSearchRequest` 交给 `client.hybrid_search`，`ranker=RRFRanker(k=60)`。

标量检索不算向量相似度，只按过滤条件返回。`client.query` 的 `filter` 可以写 `text like "%大模型%"`，也可以写 JSON 路径 `metadata["source"] like "%sample%"`。它不是关系库那种通用查询，定位是配合向量检索做筛选。

生成阶段把命中文本拼进上下文。课程示例的系统提示要求：只根据上下文回答；上下文不够时固定回答「根据上下文无法回答该问题」。然后把上下文和问题一起作为 user 消息交给 `ChatOpenAI`。

## 5. Agent

课程把 Agent 放在和自动驾驶类比的位置：模型会说话还不够，还要能行动。Agent 用 LLM 当推理引擎，决定采取什么行动、行动的输入是什么；行动结果再喂回去，由它决定继续还是结束。固定的链走预定步骤；Agent 适合开放的、多步的问题，自己拆任务、选工具、用中间结果往下推。

组件：

| 组件 | 作用 |
|---|---|
| LLM | 推理、规划、理解 |
| Memory | 短期和长期记忆，以及快速把知识检回来 |
| Tools | 调 API、数据库等外部能力 |
| Planning | 拆任务、反思 |
| Action | 把决策真正执行掉 |
| 协作 | 多个 Agent 配合 |

入口是 `create_agent`。

| 参数 | 必填 | 作用 |
|---|---|---|
| `model` | 是 | 模型实例 |
| `system_prompt` | 否 | 系统提示 |
| `tools` | 否 | 工具列表 |
| `response_format` | 否 | 结构化输出的 schema |
| `checkpointer` | 否 | 保存状态，用来做短期记忆 |
| `middleware` | 否 | 一组中间件，在各个阶段拦截并修改 Agent 行为 |

课程正文展开了工具、MCP 和 checkpointer。中间件只定义到这一句：能在各阶段拦截和修改行为。

### 5.1 工具就是函数调用

添加工具用的是模型服务的 function calling。所有工具先变成 JSON Schema，跟问题一起送给模型；模型不直接执行，只返回要调哪个函数、参数是什么。应用执行函数，把结果以工具消息送回，模型再生成给人看的话。

OpenAI 原生写法里，工具 JSON 包含 `name`、`description`、`parameters`（JSON Schema，含 `properties`、`required`、`additionalProperties`），并可设 `strict: true`。一轮对话的顺序：

1. `chat.completions.create` 带上 `tools`。
2. 把助手消息追加进历史。助手消息上有 `tool_calls`。
3. 按函数名找到实现，`json.loads` 参数，执行。
4. 追加一条 `role: "tool"` 的消息，带上 `tool_call_id` 和结果。`tool_call_id` 必须对上那次调用。
5. 再请求一次模型，由它根据工具结果组织最终回复。

LangChain 不必手写这整段 JSON。`@tool` 装饰普通函数即可。说明文字来自装饰器的 `description`，或函数文档字符串。参数说明可以用 Pydantic `BaseModel` 加 `Field(description=...)`，传给 `args_schema`。

```python
@tool(description="获取城市在特定日期的天气", args_schema=GetWeatherArgs)
def get_weather(city, date) -> str:
    """获取指定城市在指定日期的天气"""
    return f"{city} 在 {date} 天气多云"
```

`llm.bind_tools([get_weather])` 得到一个知道这些工具的新模型。`invoke` 之后看 `AIMessage.tool_calls`，元素里有 `args` 和 `id`。执行用 `get_weather.invoke(args)`，再构造 `ToolMessage(tool_call_id=id, content=...)` 追加，第二次 `invoke` 才是最终回复。

接到 Agent 上时不用手写这个循环。`create_agent(model, tools, system_prompt)` 自己决定何时调工具。课程用 Tavily 搜索演示：`pip install langchain-tavily`，`TavilySearch(max_results=5)`，Key 放环境变量。调用输入是 `{"messages": [{"role": "user", "content": "..."}]}`。

多步工具会比较久。`agent.stream(...)` 把中间消息陆续吐出来，用来看进度。要看每步耗时和完整输入输出，用 LangSmith：设 `LANGSMITH_TRACING=true` 和 `LANGSMITH_API_KEY`，代码不用改。记录默认进 `default` 项目，`LANGSMITH_PROJECT` 指定别的项目，不存在会自动建。LangSmith 是 LangChain / LangGraph 应用的调试、测试、评估和监控平台。

### 5.2 MCP

MCP（Model Context Protocol）把「模型和外部工具、数据源怎么说话」收成一个协议。工具提供方集成一次，就能被任何兼容 MCP 的宿主使用。课程的类比是 USB-C：多种设备共用一种接口。

架构是客户端-服务器：

| 角色 | 做什么 |
|---|---|
| MCP 主机 | AI 应用本身，管一个或多个客户端 |
| MCP 客户端 | 维持和服务器的连接，发现并请求资源、工具，管理连接生命周期 |
| MCP 服务器 | 向客户端暴露工具、资源和提示模板 |

传输三种：

| 传输 | 场景 |
|---|---|
| Stdio | 标准输入输出。本地进程，例如本地文件服务 |
| Streamable HTTP | HTTP POST 和 GET。服务器可用 SSE 把多条消息流回来，支持服务端到客户端的通知，也支持 Bearer、API Key、自定义头 |
| SSE | 早期的 HTTP + Server-Sent Events，正在被 Streamable HTTP 替换 |

一次调用的顺序：

1. 握手和能力声明。宿主按配置拉起服务器（常常是子进程），记住工具名字和说明，此时不执行工具代码。
2. 用户提问。宿主把问题和工具说明书一起送给模型。
3. 模型决定要不要调、调哪个。
4. 宿主用 JSON-RPC 发给服务器。Stdio 时在子进程里执行；Streamable HTTP 时在远端执行。驱动和复杂逻辑都留在服务器。
5. 服务器把结果送回宿主，宿主再交给模型生成下一步。

Python SDK 用 `mcp` 包的 `FastMCP`。三种暴露方式：`@mcp.tool()`、`@mcp.resource("greeting://default")`、`@mcp.prompt()`。`mcp.run(transport="stdio")` 走标准流；`transport="streamable-http"` 默认听 `127.0.0.1:8000`，host 和 port 可在 `mcp.settings` 里改。

客户端都是先建立传输，再 `ClientSession`，然后 `initialize()`。之后 `list_tools` / `call_tool`、`list_resources` / `read_resource`、`list_prompts` / `get_prompt`。Stdio 用 `StdioServerParameters`（`command` 是解释器，`args` 是服务器脚本）包进 `stdio_client`。HTTP 用 `streamable_http_client(url="http://127.0.0.1:8000/mcp")`。

接到 LangChain：`langchain_mcp_adapters` 的 `MultiServerMCPClient`。配置 JSON 里的 `type` 要改成 `transport`。`await client.get_tools()` 把 MCP 工具收成 LangChain 工具，传给 `create_agent`。这个客户端只有异步接口，所以 Agent 用 `ainvoke`。课程建议给每个工具设 `handle_tool_error`，工具抛错时返回一段说明，避免整次 Agent 退出。示例接的是魔搭 MCP 广场上已经部署好的服务（如 12306），也可以用自己的函数计算部署。

### 5.3 短期记忆

没有 checkpointer 时，每次 `invoke` 都是新的消息列表，上一句问过什么模型不知道。短期记忆就是把多次调用的状态存下来。

`create_agent(..., checkpointer=InMemorySaver())`。底层是 LangGraph 的检查点。隔离单位是 `thread_id`，和操作系统线程无关。调用时放进配置：

```python
config = {"configurable": {"thread_id": "abc123"}}
agent.invoke({"messages": [...]}, config=config)
```

同一个 `thread_id` 共用一条消息历史，模型只看这条历史。新会话用新的 `thread_id`。`InMemorySaver` 存在进程内存里，进程退出就没了；要跨进程保留，换数据库检查点（LangGraph 那份笔记里的 `SqliteSaver`）。

课程示例的第二次调用如果写成了另一个 `thread_id`，按上面的规则不会记得第一次。要记住，两次必须是同一个 id。

### 5.4 短期、工作记忆和长期记忆

`thread_id` 只解决短期：这一次会话的消息。任务进行中的槽位和跨会话的画像要分开存，过滤器和寿命也分开。

| 层 | 存什么 | 怎么隔离 |
|---|---|---|
| 短期 | 会话消息 | 一个 `thread_id` 一条历史，检查点可恢复，会话结束可过期 |
| 工作记忆 | 可校验的槽位：意图、实体、待确认项、已用预算 | 属于这一次任务。结束时丢掉，或压成摘要。推理草稿限制长度，不进长期存储 |
| 长期 | 用户或租户的画像、已确认的事实 | 单独的库。检索必须带 `tenant_id`。TTL 和权限过滤与会话消息不同 |

上下文放不下时，用滑动窗口加上结构化摘要。摘要里留下实体、已经作出的承诺、还没填的槽位。压完用几道探针题问这些约束还在不在。只看摘要变短了多少，会把「还不能退款」这类限制丢掉。工具返回的大结果放在外面，上下文里留摘要和指针，见工具笔记。网页和工单原文不直接写进长期记忆。

长期记忆带版本和置信度。写入前做冲突检测。用户可以纠正，也可以按条遗忘。记错了用回放集看污染范围，按版本回滚。多租户查询强制带租户条件，并有越权用例。向量库上的 metadata 过滤同样不能省。

## 6. 工具和检索的生产约束

2026 年的问法不再是「LangChain 有哪些模块」。Chain 和 `AgentExecutor` 是遗留接口。组件层负责把模型、提示、解析器、检索器和工具接成固定的线性流程。要循环、暂停和恢复，用 `LangGraph.md` 里的状态图，或自己写 loop。这一节补组件课没展开、但面试会追的两块：工具怎么被安全地调用，检索错了怎么收。

### 6.1 一次工具调用在协议上长什么样

模型不执行工具。它在助手消息里返回 `tool_calls`，每一项有 `id`、`name` 和 JSON 格式的 `arguments`。服务端校验之后执行，再用 `role: tool` 的消息把结果送回，`tool_call_id` 和那次调用对齐。轨迹里三样东西要分开记：thought 是推理痕迹，action 是工具名和参数，observation 是工具返回。日志按这三段对齐，才能分开看「选错了工具」和「工具结果没写回上下文」。

一次回复里可以有多个 `tool_calls`。没有数据依赖的读操作可以并行，有依赖的必须串行。错误不要只回一段异常字符串，要带错误码和模型能执行的下一步：重试、改参数或换工具。

`tool_choice` 控制这一次许不许选工具。`auto` 由模型决定。`required` 表示至少调用一个。指定函数名就是强制走那一个。`none` 表示禁止调用。开放对话用 `auto`。结构化抽取、必须查实时数据的路由，才在那个节点上 forced。forced 只决定选不选工具，不代替 Schema 校验、权限和人工确认。写操作仍然要服务端鉴权。更稳的入口是先分层：闲聊直接回答，明确业务进固定流程，目标开放再交给 Agent。

Chatbot、Workflow、Agent 的边界按副作用和容错来划。Chatbot 主要是多轮对话，很少改外部系统。Workflow 是预定的状态机，分支固定，适合退款审批这类错一次就有资损的流程。Agent 由模型循环选工具，并根据 observation 改计划，适合排查这种要探索的任务。也可以外层用固定流程守住合规，内层只让 Agent 做局部探索。

ReAct 是边想边做，适应变化，步数和延迟难控。Plan-and-Execute 先给出计划再执行，方便并行和审计，计划也容易过时。可分解、工具稳定的任务偏后者，探索性任务偏前者。生产里常见的是短计划加上局部 ReAct，并加上步数、重复调用和费用的硬限制。最小 loop 就是：步数没到上限就调模型；有 `tool_calls` 就先做 Schema 校验再执行、把结果写回消息，否则返回最终答复。取消、重复签名和预算在循环里检查，不靠提示词里的「请简短」。

### 6.2 工具本身要带的契约

统一的工具定义里至少有这些字段：名字、参数 Schema、超时、重试策略、副作用标记（只读还是会写）、幂等键怎么生成。Schema 用服务端再校验一遍，模型和客户端各写一套就会漂移。

| 场景 | 做法 |
|---|---|
| 结果很大 | 先摘要、分页或只回模型做下一步需要的字段，再写进上下文。整页 HTML 或整表塞回去会爆窗 |
| 写操作 | 幂等键放在业务主键上，例如订单号加动作加一次请求的 id。续跑时同一个键不能写第二次 |
| 分页 | 模型决定要不要翻下一页，要用目标字段有没有变化来判断，不能只因为「还有下一页」就一直翻 |
| 多个写操作 | 每一步可补偿。中间失败就按相反顺序补偿，或停在已成功的那一步等人处理 |
| 第三方限流 | 工具层排队，并把「稍后重试」这种结构化错误交回模型，而不是让整次 Agent 崩溃 |
| 描述写得差 | 模型和相近工具会互相叫错。描述里写清何时用、何时不用、参数单位。两个都能「搜」的工具，要在描述里分开「搜文档」和「打开网页」 |

动态注册工具要分信任等级。内置工具和租户自己上传的脚本不能进同一张可调用清单。Schema 改名或改字段时，旧 Agent 还在跑，要保留旧名字一个兼容期，或在网关上做映射。线上行为变了，要能回到上一版工具定义。

Skill 和 Tool 分成两层。Skill 是装给业务方的能力包，里面有提示、数据和它依赖的工具。Tool 是运行时真正执行的契约。装一个外部 Skill 之前要检查：它声明了哪些工具、会不会写、数据出不出租户、Schema 和现有工具冲不冲突。

结构化输出约束的是模型自己回复的格式，例如必须是一个 JSON 对象。工具调用约束的是它想执行的动作。两件事先分开：要落库的字段用结构化输出或校验器收，要查外部系统再用工具。不要把业务写入伪装成一次普通的 JSON 生成。

### 6.3 MCP 在企业里多问的那一层

协议本身是把工具、资源和提示模板的发现与调用收成一种形状，集成一次就能被多个宿主使用。传统插件通常绑死在某一个应用的进程里，换宿主就要再写一遍。

传输和安全一起看。Stdio 是本机子进程，适合开发者和可信的本地文件服务，没有独立的网络鉴权面，也不要把它暴露到公网。Streamable HTTP 才能谈 Bearer、租户和工具级 ACL，服务器在远端，复杂依赖留在服务器。SSE 是早期的服务端推送，新系统用 Streamable HTTP。MCP 服务和企业已有的 API 网关并存：网关继续做鉴权、限流和审计，MCP 只暴露已经过网关的那一组工具，不另开一条绕过权限的通道。

### 6.4 检索：召回、重排、引用、权限

嵌入、切分、重排、引用解决的是四件不同的事。嵌入把问题和文档放进同一个向量空间，负责从库里快速召回一批候选。切分决定候选的粒度，太大夹杂无关句子，太小把实体和表格切断。重排在已经召回的小集合里重新排序，不扩大召回范围。引用让回答能点回原文，并且回答里的句子确实来自那一段。

两段式是因为两段的成本不同。第一段用稠密或稀疏检索，便宜，可以多召回一些。第二段用重排模型，更准也更慢，只吃前几十条。重排的延迟要单独预算。候选已经很少、或者时延预算很紧时，可以关掉第二段，并用评测看忠实度掉了多少。

稠密检索吃语义，稀疏检索吃关键词和实体。问「小细胞癌」却召回「细胞癌」，多半是向量近、词没对齐。这种错同时看三处：查询和文档的分词、元数据过滤、重排有没有把更精确的条目抬上来。混合检索加 RRF 是课程里的默认融合。图检索适合关系本身就是答案的问题，例如「这个零件被哪些流程引用」，不要默认所有知识库都上图。

权限写进检索条件。先按用户、租户、密级滤掉看不见的块，再做向量检索。先召回全文、再在提示词里要求模型别说，挡不住。制造、车企这类场景还要加上项目、工厂、供应商这几维，同一套文档对不同人可见范围不同。

没有命中时，拒答还是改用模型自身的知识，是产品决定，写进系统提示，并放进评测。知识库问答默认拒答，并说明库里没有。开放助手可以声明「以下不是来自知识库」。索引有延迟时，用户会看到旧答案。更新后要么版本化索引、回答里带上文档时间，要么在新索引就绪前继续读旧索引。

换了嵌入模型却没重建索引，向量空间已经对不上，当晚的止血是切回旧模型和旧索引，而不是连夜重训对话模型。重建完成、用同一套查询对过 Recall 之后，再切新索引。

离线指标分成两层。Recall@k、nDCG 看相关文档在不在前 k 条、排得靠不靠前。忠实度看生成的句子有没有被检索到的材料支持。召回高但回答瞎编，是生成约束的问题。召回低，调切分、查询改写和索引，不要先怪模型。

查询改写包括补全指代、拆成多个子查询、加上同义词。改写会改坏：用户的专有名词被换成了更「通顺」的词，精确匹配就丢了。专有名词要原样保留。

Agentic RAG 让模型自己决定要不要改写、换数据源、再检一次。朴素 RAG 是固定的「检索一次然后生成」。多一次检索就多一次延迟和跑偏。要有步数上限，并且每一步的 query 和命中都进 trace。

百万级文档不要一个平面索引打到底。先按租户、文档类型或目录路由到小索引，再在小索引里检索。向量库的选择按已有设施和规模：原型和要和业务表放在一起，用 Pgvector；已经有搜索引擎、还要关键词和过滤，用 Elasticsearch；规模上去、要专门的稠密加稀疏和水平扩展，用 Milvus。权限过滤和混合检索的要求，比「哪个库更流行」先决定选型。
