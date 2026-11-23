# 本体：写出来容易，确认才算数

检索截止：2026-11-23。本篇记录 2026 年秋季企业本体/语义层的公开事实，以及选型时怎么把「确认」做成查询路径上的关卡。知识体系的位置见 [知识体系建设.md](知识体系建设.md)。

## 本体在 Agent 时代指什么

学术本体是类、属性和逻辑约束。2026 年厂商说的企业本体，是 Agent 回答业务问题之前要拿到的上下文：这个词指什么、数据在哪、和谁有关、怎么计算、依据从哪来、能不能用、谁负责。

Palantir 的用法更宽。Foundry Ontology 把对象、关系和动作放在一起：对象表示实体、关系和事件，动作把变更编排回业务系统，并留下编辑日志。它同时是语义模型和可执行的状态机。CTO Shyam Sankar 在 Stratechery 访谈里说，本体从来不只是数据的语义表示，也包括可以采取的动作，让企业可编程；人和模型都会经过同一套状态转换。访谈由 Ben Thompson 发布，公开转载日期为 2026-10-29。

<https://www.palantir.com/explore/platforms/foundry/ontology/>

Snowflake 一侧的语义视图则主要描述粒度、度量和过滤，计算仍由查询引擎执行。Erik LaRochelle 对两者的区分可以作为阅读厂商材料时的标尺：语义视图描述数据；Palantir 的 Ontology 还是带权限、校验和回写的运行时。Snowflake 自己的工具也能写，只是写操作来自存储过程、代码工具或 MCP，而不是本体里一等公民的 Action。

<https://eriklarochelle.ai/insights/snowflake-and-palantir/>

两种都叫 ontology 时，先问它管的是「含义」还是「含义加动作」。本篇的治理要求对两者都适用：含义要有负责人；动作还要有权限和日志。

## 为什么现在一齐出现

模型越来越容易买到。Agent 之间的差别，在于它能不能拿到这家企业自己的上下文：收入按签约还是按回款、谁算大客户、哪条红线不能碰。这些老员工知道，模型不知道。

手工本体卡了几十年，是因为专家写得完的只是窄窄一层，业务一周就变。2026 年变的是供需两头：Agent 是这些上下文最大的消费者，也开始能从 SQL、仪表盘、笔记和语义模型里把它们整理出来。所以 Databricks、Snowflake、微软、英伟达，以及同期的 Dell、Oracle、SAP、Zoho，都在抢同一层：Agent 用的企业上下文。

这一层最容易的一步是生成。最贵的一步是确认哪一条算数。

## 2026 年 9–11 月的公开时间线

RJL Solutions 的 Richard White 在 2026-11-11 把六周内的七家发布收在一起。下面的日期和成熟度按该文及各家原文，不把预览写成已上线。

| 时间 | 谁 | 公开状态 | 和「谁来确认」相关的事实 |
| --- | --- | --- | --- |
| 2026-09-29，巴塞罗那 FabCon | Microsoft Fabric IQ | 本体及本体 Agent 为 Preview | Agent 从 Power BI 语义模型和 OneLake 提出实体与关系。微软文档要求先审阅草稿和补丁，再在 Act 模式应用；权限仍走 Entra ID 和 Fabric RBAC |
| 2026-10-06 | Dell AI Data Platform | 宣布统一语义层和企业知识图谱，计划 2027 上半年 | 公告未证明确认机制已经可用 |
| 2026-10，仓库创建于 04-27 | NVIDIA Auto Ontology | Apache-2.0 开源，检索日约 14180 star | 从元数据生成草稿，专家认证。官方技能文档写明：当前认证字段不是原子的审核/发布系统 |
| 2026-10-26 | Oracle | 宣布自治 AI 湖仓的 Business Semantic Layer，称 12 个月内可用 | RJL 称公告没有写清批准后 Agent 才能使用 |
| 2026-10-27，柏林 TechEd | SAP Ontology Manager | 未给可用日期 | 同上，公告对批准路径沉默 |
| 2026-10-28 | Zoho Analytics | 语义层和本体层，预览随后几周推出 | 预览 |
| 2026-11-03 | Snowflake | Business Ontology；工程博客写明 private preview。Cortex Sense 为即将到来的 public preview。Semantic Views 负责真正计算 | 每个定义有正式版本和负责人；导入候选标为草稿，待负责人批准；变更记日志 |
| 2026-11-11 | Databricks Genie Ontology | Generally Available | 人治理的定义优先；其余用 OntoRank 排序。见下节 |

来源：<https://rjlsol.com/insights/viewpoints/who-signs-off-what-the-data-means>；微软 Learn，[Ontology Agent Responsible AI FAQ](https://learn.microsoft.com/en-us/fabric/iq/ontology/resources-responsible-ai-faq)；Snowflake 工程博客，<https://www.snowflake.com/en/blog/engineering/business-ontology-context/>；Databricks，<https://www.databricks.com/blog/announcing-ga-genie-ontology>。

RJL 给采购的四个检查，可以直接当上线条件：

- 每个 Agent 能用的术语有一个负责人。
- 草稿到不了生产回答。
- 导出时带上负责人、版本和审批历史。
- 每条答案记下用过的定义和版本。

另外两条来自同一作者的区分：含义和计算逻辑分开；批准要能离开这家平台，否则换平台就要重新签一次，副本会漂。

## Databricks：学习长尾，人工口径优先

Genie Ontology 于 2026-11-11 由 Ken Wong、Paul Roome、Richard Tomlinson 宣布 GA。官方定义是：Agent 从日常工作里建，人治理必须精确的定义，任何 Agent 都能用。

**学出来的一层。** Ontology Snippets 从仪表盘、SQL、笔记本抽取计算、业务规则和数据关系，来源变化时更新。外部 BI 里，Power BI 已支持，Tableau 和 Sigma 在路线上。每条 snippet 挂回原资产，继承原资产权限，引用时能看到出处、作者和为何被信任。

**人定的一层。** Unity Catalog 里的 Metric Views、Domains / Subdomains、Pages。Page 是术语、实体或缩写的权威解释，有负责人和所属域。问题碰到已定义概念时，Genie 把 Page 当作事实来源，优先于学出来的上下文，并引用它。Domains 用来处理同一个词在不同团队含义不同。Ontology Manager 在宣布时是 coming soon in Beta，计划提供健康度、缺口和把学到的知识提升为 Page 的队列。

**排序。** OntoRank 在学出来的知识和建好的模型之间做选择，信号包括来源权威、认证、相关人员的专业程度和与提问者的关系、使用热度、新鲜度。组织目录（先从 Workday）提供正式汇报关系，再和「谁真正一起用这些资产」的行为信号合在一起。

**对外。** 通过 Genie MCP 给 ChatGPT、Claude、Copilot 等使用。文档页更新于 2026-11-17：Unity Catalog 权限每次都生效；建议 on-behalf-of，让用户身份穿过请求。

**Genie Search** 同日 GA，连接文档、工单和 wiki。当时支持 Google Drive、Jira、Confluence、Workday，SharePoint 为 Beta。索引内容也进入 OntoRank 的证据。权限随 Unity Catalog 连接从源系统带入，在返回前执行。实时读写工单仍走 MCP，由 Genie 决定这次用索引、MCP 还是 SQL。

官方和 Turing 做的基准是虚构英国能源公司 Calderwick，工作区里故意留下复制的笔记本、做了一半的分析、几个月没人碰的仪表盘。每个任务跑 5 次：

| Agent | 通过率 | 平均耗时 | 平均 token |
| --- | --- | --- | --- |
| Genie（带 Genie Ontology） | 91% | 约 100 秒 | 约 43.2 万 |
| Frontier Agent A | 45% | 约 435 秒 | 约 88.9 万 |
| Frontier Agent B | 58% | 约 307 秒 | 约 105 万 |

官方解读：三家都会写 SQL，差别在上下文。Genie 用已经学好的规则，另外两家每次重新发现，并且经常不满足业务规则。数字来自厂商自建环境和自定评分。独立评论指出对照 Agent 没有接 Genie MCP。它说明的是「在已经积累了工作痕迹的 Databricks 工作区里，带这套本体的 Genie 比当场摸索更稳」，不能换成任意企业的准确率承诺。

来源：<https://www.databricks.com/blog/announcing-ga-genie-ontology>；任务拆解：<https://www.databricks.com/blog/genie-genie-ontology-delivers-16-accuracy-less-half-tokens>。

### 0.52 和 0.31：表是对的，口径在笔记里

基准里有一道题，问商业部门为 FY24/25 定的 CfD 计划费率。账单成本表 `cfd_rate_p` 在 FY24/25 的行上写着 0.31 便士/千瓦时，因为集市用的是 FY25/26 的假设，并把新假设套到了更早的账期上。建表笔记本 `45_mart_bill_cost_stack` 另有一张历史表：FY24/25 为 0.52，FY25/26 为 0.31，FY26/27 草案为 0.45。

Genie 五次都先回答 0.52，并给出笔记本出处。另外两个 Agent 没有稳定得到 0.52：一个五次都是 0.31，另一个在 0.31 和「不确定有没有按年记录」之间。

这道题说明三件事：

1. Schema 描述数据长什么样。哪一年的计划费率才是当时定下来的数，写在工作痕迹里。
2. 定稿、草案和重算后的数会混在同一份笔记里。0.45 是草案，不能和 0.52 一样被引用。
3. 排序只能估计该先看哪条。草案和定稿的差别，要靠来源状态和人工确认，不能只靠新鲜度或热度。

## Snowflake：含义、推断、计算分成三层

2026-11-03 前后的公开材料把三层拆开，并写明不必按顺序上：

| 层 | 角色 | 成熟度（工程/产品博客当时的说法） |
| --- | --- | --- |
| Semantic Views | 真正执行连接、过滤和聚合。一个指标的计算以它为准 | 既有产品能力 |
| Cortex Sense | 从 schema、查询、语义视图、仪表盘、转换逻辑和外部来源推断实体、指标、关系和冲突 | public preview 即将到来 |
| Business Ontology | 较小的那组需要问责的定义：含义、类型、负责人、适用政策、由哪个资产实现 | private preview |

概念是图上的节点，边有方向和类型，例如 Gross Revenue `DERIVES` Net Revenue，政策 `APPLIES_TO` 某指标。表示（representation）指向实现它的语义视图资产，本体不执行公式。概念带域：`Finance.Net Revenue` 和 `Sales.Net Revenue` 可以并存。只有 metric 能有公式、排除项和公式出处；公式按原文保存。

Cortex Sense 的发现要先提升成草稿，再由 steward 批准。底层资产变了，表示可以被重新检查。RJL 特别把 Snowflake 和 Oracle、SAP 分开：Snowflake 这次写了负责人和草稿。

产品行为在预览期会变。采购时仍要自己验证「草稿能不能被 Agent 读到」。

## 微软：提案先落到人手里

Fabric 的 Ontology Agent 在 2026-11 仍是 Preview。微软的 Responsible AI FAQ 写明：它检查工作区、抽样、在应用前验证草稿；草稿、补丁和查询结果仍要人审。Act 模式只能做调用者本来就有权做的事，数据范围限于调用者能访问的 lakehouse、eventhouse、语义模型和本体项。

这是门上有人的设计：模型提变更，人通过后才生效。导入导出 TTL / RDF 同样处于预览，OWL 语义不能假设无损往返。

<https://learn.microsoft.com/en-us/fabric/iq/ontology/resources-responsible-ai-faq>

## 英伟达：认证是标记，发布文档自己说还不是关卡

Auto Ontology 从企业元数据生成本体，用 MCP、CLI 和 API 暴露给 Agent，并用 Apache Ossie 保持可移植。仓库创建于 2026-04-27，Apache-2.0。

它用六个元素回答六个问题：

| 问题 | 元素 |
| --- | --- |
| 是什么意思 | Concept：术语、描述、同义词 |
| 在哪 | Mapping：术语到表和列 |
| 和什么相连 | Relationship：声明的键，加上用数据确认过的推断连接 |
| 怎么算 | Metric：挂在术语下、经过审阅的 SQL |
| 从哪来 | Lineage：术语和属性回到源表列，答案带回 SQL |
| 该不该用 | Certification：专家确认名称、描述和属性，元素标成已审阅 |

生成过程读表、列、键、视图和查询历史，对取值做剖析，推断外键，把重复 schema 收成术语族，输出到 Postgres 和 pgvector，并可导出 Ossie YAML。人用真实问题、`check_answerable` 和 `ask_question` 检验草稿，再认证。下一次编译会保留人工修改，但连接用哪一把语义外键会在每次编译时重选。

官方技能文档 `publication.md` 把发布和认证分开，这是读实现时最重要的一段：

- 聊天成功、图表、本地回执，都不是发布。
- MCP 只读，不能发布。发布要用带调用者权限的 REST 或源写入器。
- 发布可以暴露一条建议，不能暗示已经批准、承诺或执行。
- 原文写着：当前认证字段不是一套原子的审核、提升、替代和回滚系统。在那套生命周期出现之前，不要把已认证说成原子发布。

因此，Auto Ontology 适合作为起草、对照 SQL 和可移植交换的开源底座。若把它放进财务回答，关卡要加在自己的查询服务上：只把状态为已确认、且确认晚于最后一次编译改写的元素交给 Agent。徽章可以展示，不能代替这道检查。

仓库：<https://github.com/NVIDIA/auto-ontology>  
发布说明：<https://github.com/NVIDIA/auto-ontology/blob/main/skills/nvidia-ontology-management/references/publication.md>

## 「让 AI 设计本体，会得到平庸」成立的范围

Sankar 在同一次访谈里说：让 AI 为企业设计 ontology，结果是中等水平，全是 beta；它会按给任何公司建模的方式给你建模，磨平真正不同的地方，向平均回归。

这句话成立的前提，是模型拿通用经验来套企业。从这家公司自己的 SQL、仪表盘和笔记里学，学到的是它独有的工作痕迹，例如上面的 0.52，而不是行业平均费率。

学不出来的是还没拍板的事。收入何时确认、谁算大客户、哪条红线不能碰，在工作痕迹里常常是乱的：定稿、草案和重算后的数放在一起。把这些交给排序算法，得到的是「看起来最可信」。把它们交给具名负责人，得到的是可以问责的口径。

实用的分法：

- 长尾、团队级、变个不停的上下文，让 Agent 从工作痕迹里学，保留出处、作者、权限和草稿/定稿状态。
- 会进入财务、风险、合规、安全和对外承诺的少量定义，由负责人确认。
- 答题路径真的去查确认状态。没有当前确认，就不用它回答这类问题。
- 数据团队的工作从替所有人写定义，变成经营队列、域和评测。

Sankar 的另一句也值得留下：即便主张多用 AI，也要知道何时不用。他提到曾要求新员工第一周只用 AI FDE 学平台，结果一些人不会底层构件。本体同理。自动生成可以加快草稿，不能代替有人懂这条口径为什么这样定。

## 查询路径上的关卡

把各家做法收成一张可实施的表。

| 做法 | 代表 | 未确认的知识能否进答案 | 适合 |
| --- | --- | --- | --- |
| 提案后必须人审才应用 | Fabric Ontology Agent（Preview） | 设计上不能直接改正式本体 | 变更少、必须有门的域 |
| 官方定义优先，其余排序 | Databricks OntoRank + Pages | 学出来的 snippet 仍可能被选中；Page 在冲突时优先 | 长尾大、少数 KPI 必须精确 |
| 推断只作为草稿 | Snowflake Cortex Sense → Business Ontology | 按公告应先批准；预览期要自己测 | 已有语义视图、要补业务含义 |
| 认证字段 | NVIDIA Auto Ontology | 字段本身不是原子关卡 | 自建、要可导出的草稿 |

自建时采用第三列最严的行为，而不是最松的：

```text
回答高风险问题
  → 解析出涉及的术语 ID
  → 只取 status=stable 且存在晚于 generated.at 的 human verified 且 now < stale_after
  → 没有则拒答或只说明「尚无确认口径」，不改用检索片段顶上
  → 低风险解释可以引用草稿，但答案中标明 draft
  → 日志写入术语 ID、版本、确认人、来源哈希
```

OntoRank、热度、新鲜度和认证徽章可以决定草稿的审核顺序，不决定高风险答案的资格。

## 和 wiki、RAG 的边界

本体不存放整本制度，也不替代向量检索。

- 制度原文和证据片段在来源库，RAG 负责按权限找回来。
- 跨文档综述在 LLM Wiki，引用本体 ID，不另写一套「我们公司的净收入是……」。
- 本体只保留需要稳定引用的概念：含义、域、负责人、状态、确认、适用政策、实现该概念的指标或表。
- 动作类概念若会写回业务系统，单独审批，和「这个词是什么意思」的审批分开。

Palantir 强调动作，Databricks 和 Snowflake 这次公开材料更强调上下文和计算。企业若只做问答，可以先不做动作本体；一旦 Agent 能改工单、改权限或改主数据，动作必须有前置条件、权限、审计和补偿，不能复用问答的排序。

## 选型时只做的实验

在厂商环境或 PoC 里准备三条互相冲突的材料：现行制度、一份草稿、一张用新口径回算了历史的表。然后问一个财务问题。

记录：

1. 答案用的是哪一版，是否可见负责人。
2. 把确认取消或把页面改回草稿，同一问题是否立刻不再使用它。
3. 一个无权查看源笔记的身份来问，答案是否还包含该笔记里的数字。
4. 导出后再导入，负责人、版本和审批时间是否还在。
5. 修改计算公式，概念含义的版本是否被错误地一起抬高，或相反。

五条里任何一条做不到，就还是徽章，不是关卡。

## 来源

- Databricks，Announcing GA of Genie Ontology，2026-11-11：<https://www.databricks.com/blog/announcing-ga-genie-ontology>
- Databricks，基准与 0.52 案例：<https://www.databricks.com/blog/genie-genie-ontology-delivers-16-accuracy-less-half-tokens>
- Databricks 文档，Genie One MCP，页眉更新 2026-11-17：<https://docs.databricks.com/aws/en/agents/mcp-tools/genie-mcp>
- Snowflake 工程博客：<https://www.snowflake.com/en/blog/engineering/business-ontology-context/>
- Snowflake 产品博客：<https://www.snowflake.com/en/blog/business-ontology-context/>
- Microsoft Learn，Ontology Agent FAQ：<https://learn.microsoft.com/en-us/fabric/iq/ontology/resources-responsible-ai-faq>
- NVIDIA auto-ontology README 与 `skills/nvidia-ontology-management/references/publication.md`
- RJL Solutions，Richard White，2026-11-11：<https://rjlsol.com/insights/viewpoints/who-signs-off-what-the-data-means>
- Palantir Foundry Ontology 产品页；Shyam Sankar 访谈，Stratechery，公开转载 2026-10-29。
- Erik LaRochelle，Snowflake 与 Palantir 的对照：<https://eriklarochelle.ai/insights/snowflake-and-palantir/>
