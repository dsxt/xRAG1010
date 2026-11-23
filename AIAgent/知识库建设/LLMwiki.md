# LLM Wiki：把知识编译一次，并持续维护

检索截止：2026-11-23。模式来自 Andrej Karpathy 2026-04-04 的 LLM Wiki 说明；企业用法是在该模式上补上确认、权限和过期，见 [知识体系建设.md](知识体系建设.md)。

## 和 RAG 的差别

常见 RAG 在提问时从原文切块里现找、现拼。五份文档才能回答的问题，每次都要重新发现。NotebookLM、聊天机器人的文件上传，以及多数向量库，都是这条路径。它适合长原文和刚进来的材料，见 [RAG和知识图谱.md](RAG和知识图谱.md)。

LLM Wiki 在来源和提问之间放一层持久的、互相链接的 Markdown。新来源进来时，模型读原文和现有 wiki，改实体页、改综述、标出和旧主张的冲突，并更新索引。综合结果被保存下来。下一次提问直接读已经编译过的页。

Karpathy 的原话把分工说死了：人负责选来源、探索和提问；模型负责摘要、交叉引用、归档和簿记。人放弃 wiki，通常是因为簿记成本涨得比价值快。模型不会因为要改 15 个交叉引用就停下来。

企业不能把这句话理解成「模型写的就是真的」。编译降低的是重复发现的成本，不自动产生负责人。关键口径的确认见 [本体Ontology.md](本体Ontology.md)。

## 三层

| 层 | 谁写 | 规则 |
| --- | --- | --- |
| 原始来源 | 人、连接器、业务系统 | 不可被模型改写。这是证据 |
| Wiki | 模型维护，人审阅高风险页 | 摘要、实体、概念、对照、综述、索引、日志 |
| Schema | 人和模型一起演进 | `AGENTS.md` / `CLAUDE.md` 一类文件，规定目录、页面类型、摄取、问答和维护流程 |

Schema 是这个模式里真正的配置。没有它，模型只是一个会改 Markdown 的聊天机器人。目录结构、页面类型和工具都可以换；摄取时必须更新哪些页、矛盾怎么标、什么内容不许自动发布，要写进 schema。

## 三个操作

**Ingest。** 投入一篇来源。模型阅读后写摘要页，更新索引，回写相关实体和概念，追加日志。一份来源常常碰到 10–15 页。Karpathy 本人倾向一次投一篇并看diff；也可以批量，但要把监督程度写进 schema。对企业材料，摄取是一次重构：旧主张被新证据削弱时，要改旧页，而不是只追加「另见」。

**Query。** 先读索引，再读相关页，回答时引用页面。有价值的回答写回 wiki，例如一次对照、一次事故综述。探索本身也要沉淀，否则又掉回聊天记录。高风险问题的回答若引用了 `draft` 或过期页，应在答案里显式标出，而不是混在正文里。

**Lint。** 周期性健康检查，分两遍更稳。第一遍不调用模型：断链、孤儿页、缺 frontmatter、过了 `stale_after`、索引和目录不一致。第二遍调用模型：页与页矛盾、综述落后于新来源、提到了重要概念却没有专页、缺口值得补来源。每个问题对应一个可执行的修复建议。修复高风险页仍然走人审。

## 两份导航文件

`index.md` 按内容编目：链接、一行摘要、可选的日期和来源数。摄取时更新。提问时模型先读它。Karpathy 的经验是，大约 100 份来源、几百页时，索引够用，不必先上向量库。

`log.md` 只追加。建议固定标题，便于检索：

```text
## [2026-11-23] ingest | 结算制度 2026-Q4
## [2026-11-23] query  | 收入按签约还是按回款
## [2026-11-23] lint   | 3 处矛盾，1 处过期
```

日志让模型和人知道最近发生过什么。它不是审计系统。企业审计还要记录调用者、知识版本和放行决定，见总纲。

规模变大后，索引本身装不进上下文。Karpathy 原文把本地混合搜索作为可选工具：关键词加向量，模型通过 CLI 或 MCP 调用，例如他提到的 qmd。搜索结果仍应返回 wiki 路径，由模型打开原文页，而不是只拿搜索摘要作答。

## 页面上至少要有的头

OKF v0.3 把 Agent 持续改写知识库时缺的五件事写成字段：出处、信任、新鲜度、生命周期、计算过程是否符合声明。最小可用子集：

```yaml
title: 净收入
status: draft          # draft | stable | deprecated
owner: human:finance-steward
generated:
  by: agent:wiki-maintainer
  at: "2026-11-23T02:10:00Z"
verified:
  - { by: "human:finance-steward", at: "2026-11-01T09:00:00Z" }
stale_after: "2027-02-01T00:00:00Z"
sources:
  - raw/finance/rev-rec-2026.md
```

信任只看不早于 `generated.at` 的确认。人在 11 月 1 日签过，模型在 11 月 23 日改写后，这条不再是 human-reviewed，直到有新的人工确认。旧记录留着，界面可以显示「上次确认早于本版」。OKF 写明这些等级是建议，不是权限。

`deprecated` 页不要删掉。写上 `superseded_by`，让旧链接继续工作。

## 个人模式搬进企业时要改的四处

**1. 来源不一定是人精选的文章。** Karpathy 的个人用法假设来源可信、可整篇阅读。企业来源里有草稿、过期制度、复制出来改了一半的笔记、几个月没人打开的仪表盘。摄取时要带来源类型和状态。草稿可以进证据库，默认不进 `stable` 页。

**2. 模型拥有全部 wiki 这句话要收窄。** 低风险页可以让模型直接维护。收入、客户、违约、权限、对外承诺只接受补丁。WeKnora v0.9.0（2026-10-29）把执行权限分成只读、变更前确认、自动写入，这个分流可以直接借来用。

**3. 多人多 Agent 不能最后写入获胜。** Karpathy gist 下 2026-04-11 的评论把规模问题说得很具体：错误会沉淀成下一轮生成的先验；三个以上写者并发时，无类型链接会丢上下文。链接至少要能表达 `supports`、`contradicts`、`supersedes`。冲突页先并存并标负责人，不在摄取时静默选边。

**4. 编译层会放大错误。** 一次幻觉在 RAG 里通常停在当次回答。写进 wiki 后，它会变成后续页面的依据。所以 lint 和黄金问题是运行要求，不是上线后的优化。发现错误的修复也要写日志，并回扫引用了该主张的页。

## 2026 年可以读代码的实现

| 项目 | 许可与体量（检索日） | 适合看什么 |
| --- | --- | --- |
| [Karpathy LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) | 想法文件，2026-04-04 | 三层、三操作、index 与 log。不是产品 |
| [lucasastorian/llmwiki](https://github.com/lucasastorian/llmwiki/) | Apache-2.0，约 1783 star，2026-04-04 创建 | 上传、网页剪藏、MCP、夜间例行维护、图谱。作者希望组织用它做制度记忆，确认流程要自己加 |
| [ddsyasas/llm-wiki](https://github.com/ddsyasas/llm-wiki) | MIT，v1.2.3，约 75 star，2026-05-23 创建 | 本地优先，自带密钥。Ingest、带引用的 Query、Lint 和一键修复都在 |
| [balukosuri/llm-wiki-karpathy](https://github.com/balukosuri/llm-wiki-karpathy) | 约 223 star，2026-04-07 创建 | 面向 Cursor / Obsidian 的工作流 |
| [Pratiyush/llm-wiki](https://github.com/pratiyush/llm-wiki) | MIT，约 402 star，2026-04-08 创建 | 把 Claude Code、Codex、Copilot、Cursor、Gemini 的会话编成 wiki，并导出 `llms.txt`、JSON-LD。材料是工作痕迹，不是已批准制度 |
| [Tencent/WeKnora](https://github.com/Tencent/WeKnora) | 约 38004 star；许可证页为 Other | v0.5.0 起 Wiki 模式生成带引用的互联 Markdown 和知识图谱；页可编辑、可看修订、可回滚。v0.5.2 称摄取可到约 4 万篇文档。和 RAG、Agent 共用知识库 |
| [infiniflow/ragflow](https://github.com/infiniflow/ragflow) | Apache-2.0，约 94547 star | 1.0.0 于 2026-11-06 发布。2026-08-19 加入知识编译，能从文档和数据集生成 Wiki、图、树、PageIndex、时间线等 |
| [open-knowledge-format](https://github.com/open-knowledge-format/open-knowledge-format) | SPEC v0.3 | 页头标准。不管检索和权限 |

星数只说明检索当日的关注度。WeKnora、RAGFlow 是平台，llmwiki 一族更接近「目录加 schema」的原模式。平台省去摄取和界面，原模式更方便用 Git 审计每一页 diff。

Jonex 的公开说明把 Ontology 和符合 OKF 的 Wiki 放在同一引擎里，作为从摄取、编译、索引到反馈的企业知识系统的一部分。它是产品叙述，架构上和「规范 Markdown + 派生索引」一致：<https://github.com/jonexaiorg/jonex>。

## 一个能跑起来的目录

```text
knowledge/
  AGENTS.md                 # schema：页面类型、摄取、问答、lint、禁止自动发布的目录
  raw/                      # 只追加。模型不改
  wiki/
    index.md
    log.md
    entities/
    concepts/
    comparisons/
    syntheses/
  review/                   # 高风险 diff，合并前不算 stable
```

Schema 里写清五条就够开始：

1. 摄取只改 `wiki/` 和 `review/`，不改 `raw/`。
2. 每次摄取更新 `index.md`，并在 `log.md` 追加一条。
3. 新主张和旧主张冲突时，两边都保留，并写 `contradicts` 链接，不覆盖。
4. `wiki/concepts/finance/`、`security/`、`legal/` 的修改只生成补丁到 `review/`。
5. Lint 每周跑。过期、断链和未确认却被高风险页引用的情况，输出成队列。

小库不必上向量库。索引文件先用着。出现「索引太长、模型找不到页」时，再给 wiki 本身做混合搜索。原文库的 RAG 与这层搜索是两套索引：一个找证据，一个找已经编译的结论。

## 和图谱、本体怎么接

Wiki 页之间的链接是给人读的关联。多跳问题、社区级综述、以及「删掉一篇文档后图节点是否还在」，交给图检索，见 RAG 篇。

Wiki 可以写「收入这个词在本公司有两种用法」。哪一种能回答本季度财务问题、谁签的字、计算公式在哪张语义视图里，交给本体。本体页稳定之后，wiki 引用它的 ID，不另抄一份定义。

## 维护节奏

| 频率 | 动作 | 产出 |
| --- | --- | --- |
| 每次来源变更 | 单篇摄取或增量同步 | 摘要、回写、日志 |
| 每次高风险补丁 | 负责人看 diff | 合并或驳回；驳回不进检索 |
| 每周 | 结构 lint + 矛盾 lint | 队列 |
| 每次合并到 stable | 跑这几页相关的黄金问题 | 失败则回滚该补丁 |
| 每季 | 抽查机器确认页，清理无人引用的孤儿 | 降级、合并或废弃 |

## 来源

- Andrej Karpathy，LLM Wiki，2026-04-04。公开转载：<https://llm-wiki.md/> 。以 Karpathy gist 原文为准。
- 同页 2026-04-11 评论：持久错误会复利、多 Agent 并发需要带类型的链接、索引大了以后要分层记忆。评论是实践者意见，不是 Karpathy 的规范。
- Open Knowledge Format SPEC v0.3：<https://github.com/open-knowledge-format/open-knowledge-format/blob/main/SPEC.md>。
- Google Cloud，[Introducing OKF v0.3](https://cloud.google.com/blog/products/data-analytics/introducing-okf-v0-3)：改写后只有新的确认算当前确认。
- 各仓库 README，访问于 2026-11-23。
