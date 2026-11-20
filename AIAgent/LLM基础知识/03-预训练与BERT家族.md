# 预训练与 BERT 家族

`02` 的编码器和解码器是骨架。这一章讲别人已经在大规模语料上训好的模型怎么接到自己的任务上，以及 BERT 这一家各自改了哪一处。

## 1. 三个词

预训练模型是在大量文本上已经训完的网络。它的参数里有通用的词义和句法，体积通常很大。

微调是拿垂直领域的标注，继续更新这些参数，或者只更新你接在后面的新层。开箱即用是任务和预训练目标足够接近时，直接拿现成的头做推理，参数不动。情感分类、填空、抽取式问答、摘要、命名实体识别都有现成 checkpoint 可以先试。

按骨架分成三类：

| 骨架 | 看见的上下文 | 代表 |
|---|---|---|
| 只有编码器 | 每个词同时看见左右 | BERT |
| 只有解码器 | 每个词只看见自己和左边 | GPT、GPT-2 |
| 编码器 + 解码器 | 编码器看全句，解码器看左边并回看编码器 | T5 |

后来常见的名单还有 Transformer-XL、XLNet、XLM、RoBERTa、DistilBERT、ALBERT、XLM-RoBERTa。下面把课程展开过的几个写清楚。

## 2. Hugging Face 的三种调用

同一个 checkpoint 有三种入口，差别是你能改的旋钮有多少。

| 入口 | 适合 | 代价 |
|---|---|---|
| `pipeline(task, model)` | 先确认这个模型能不能做这件事 | 封装深，超参少 |
| `AutoTokenizer` + `AutoModelFor*` | 换 checkpoint 时代码不用改 | 旋钮少于手写具体类 |
| `BertTokenizer` + `BertForMaskedLM` 这种具体类 | 要按这个模型的参数做微调；后面的 LoRA、QLoRA 也从这里接 | 每个模型的类和参数名不一样 |

安装：`transformers`、`datasets`。部分 pipeline 还会拉 `tf-keras`。模型路径用原始字符串，避免 Windows 路径里的反斜杠被转义。

分词器把文本变成三块张量：

| 字段 | 含义 |
|---|---|
| `input_ids` | 词下标，含起始和结束符 |
| `attention_mask` | 1 是真实词，0 是补齐 |
| `token_type_ids` | 这个词来自第几句。单句全是 0；句对里第二句是 1 |

`padding="max_length"` 一律补到 `max_length`。`padding=True` 补到本 batch 里最长的那句，并且不超过 `max_length`。`truncation=True` 把超出的切掉。返回值要给模型算的话，`return_tensors="pt"`。

填空任务不要随意 padding。`[MASK]` 的下标是按未填充的序列数的，补齐会把你记住的位置挤偏。

六个任务，pipeline 和 Auto 取的是同一段输出：

| 任务 | pipeline 的 task | Auto 类 | 结果怎么读 |
|---|---|---|---|
| 文本分类 | `text-classification` | `AutoModelForSequenceClassification` | `logits.argmax(-1)`，再对照 `id2label` |
| 特征抽取 | `feature-extraction` | `AutoModel` | `last_hidden_state` 形状 `[句数, 词数, 隐藏维]`；`pooler_output` 是 `[句数, 隐藏维]` |
| 完形填空 | `fill-mask` | `AutoModelForMaskedLM` | 在 `[MASK]` 那个下标上对词表取 argmax，再用 `convert_ids_to_tokens` |
| 阅读理解 | `question-answering` | `AutoModelForQuestionAnswering` | `start_logits`、`end_logits` 各取 argmax，在上下文的 token 上切片。pipeline 可以一批问题一起问，Auto 这版案例是一题一题问 |
| 摘要 | `summarization` | `AutoModelForSeq2SeqLM` | `generate` 得到下标，`decode(..., skip_special_tokens=True)` |
| 命名实体 | `ner` | `AutoModelForTokenClassification` | 每个词的 logits 取 argmax，用 `config.id2label` 得到 `B-name`、`I-name` 这种 BIO 标签。特殊符号跳过 |

`bert-base-chinese` 的隐藏维是 768。一句「这家餐馆的卫生还行，就是有点油」抽出来大约是 `[1, 17, 768]`：17 含起始符、结束符和每个字。

BERT 的 `pooler_output` 是对 `[CLS]` 的隐藏状态做线性层加 Tanh，和卷积里「取一片区域的最大值」不是同一个运算。名字都叫池化，是因为都把多个数收成一个表示。分类头更常用没有经过这层 Tanh 的 `last_hidden_state[:, 0]`，理由见下一节。

具体类和 Auto 的前向一样。填空示例：

```python
tokenizer = BertTokenizer.from_pretrained(model_path)
model = BertForMaskedLM.from_pretrained(model_path)
batch = tokenizer("我想明天去[MASK]家吃饭", return_tensors="pt")
logits = model(**batch).logits          # [1, seq, vocab]
mask_pos = (batch.input_ids == tokenizer.mask_token_id).nonzero()[0, 1]
token_id = logits[0, mask_pos].argmax().item()
print(tokenizer.convert_ids_to_tokens(token_id))
```

位置不要写死成 6。不同分词器、有没有空格，`[MASK]` 的下标都会变。

## 3. 微调时接一个新头

主干负责把句子变成隐藏状态，新头负责任务。课程的两个案例都先把 BERT 的梯度关掉，只训练新头。这样做是为了先跑通、省显存；标注够、时间够时再打开主干，学习率用下面 BERT 论文那一组小学习率。

冻结：

```python
for param in bert_model.parameters():
    param.requires_grad_(False)

optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
```

`requires_grad_(False)` 之后，优化器里那些参数没有梯度，不会被更新。前向外面再包 `torch.no_grad()` 是同一件事的第二道。只写 `no_grad()`、又把整个 `model.parameters()` 交给优化器，主干参数仍占着优化器状态。

训练步：`model.train()`，batch 和模型放到同一块设备，前向，`CrossEntropyLoss`，`zero_grad`，`backward`，`step`。评估步：`model.eval()` 加上 `torch.no_grad()`，结束继续训练前要回到 `model.train()`，否则 Dropout 一直关着。GPU 上存的权重拿到 CPU 上加载时加 `map_location="cpu"`。

### 分类头

二分类情感、十类新闻都是这个头，只是输出维不同。

```python
class Classifier(nn.Module):
    def __init__(self, hidden, num_labels):
        super().__init__()
        self.fc = nn.Linear(hidden, num_labels)

    def forward(self, input_ids, attention_mask, token_type_ids=None):
        with torch.no_grad():
            out = bert_model(
                input_ids=input_ids,
                attention_mask=attention_mask,
                token_type_ids=token_type_ids,
            )
        return self.fc(out.last_hidden_state[:, 0])
```

`[:, 0]` 是每句第一个 token，也就是 `[CLS]`。BERT 的自注意力让这个位置汇集了整句。论文里这句话是：这个 token 的最终隐藏状态，用作分类任务的整句表示。

`pooler_output` 和 `last_hidden_state[:, 0]` 差一次线性层加激活。分类头自己还有线性层，所以直接用 `[:, 0]`。蒸馏那一章的教师模型用了 `pooler_output`，是为了拿一个已经收成句向量的表示，见 `05`。

情感案例的数据是 `datasets.load_dataset` 读 csv，字段 `text`、`label`。`collate_fn` 里 `batch_encode_plus`，标签变成 `torch.long`。`batch_size=8`，`max_length=300`，最后一批不足就丢掉。学习率 `1e-3` 只更新这个新头；主干若一起训，改用 `2e-5` 到 `5e-5`。

### 填空头

把每句固定位置换成 `[MASK]`，让新头在那个位置上预测原来的词。这是在模仿 MLM，用来练习「取序列中的某一个位置」，正式预训练仍用 BERT 自己的 `BertForMaskedLM`。

句子先滤掉长度不够的，避免下标越界。`collate_fn` 里先编码，再把该位置的原始 id 存成标签，然后写成 `tokenizer.mask_token_id`。新头是 `Linear(768, vocab_size)`，损失仍是交叉熵。

```python
return self.fc(out.last_hidden_state[:, mask_index])
```

## 4. BERT 本身

BERT，Bidirectional Encoder Representations from Transformers，2018 年 Google 提出。它只保留 `02` 的编码器。base 是 12 层、隐藏 768、12 头、约 1.1 亿参数。large 是 24 层、隐藏 1024、16 头、约 3.4 亿参数。

发布时在 SQuAD 1.1 的两个指标上超过当时的人类成绩，GLUE 到 80.4（绝对提升 7.6 个点），MultiNLI 准确率 86.7（绝对提升 5.6 个点）。

### 三层嵌入相加

| 嵌入 | 作用 |
|---|---|
| Token | 词本身。序列以 `[CLS]` 开头 |
| Segment | 这个词属于句对中的第一句还是第二句，为 NSP 准备 |
| Position | 可学习的位置向量，上限 512。这里不是 `02` 的正弦编码 |

三块形状相同，逐元素相加，再进编码器。

常见 checkpoint：

| 名称 | 规模 | 文本 |
|---|---|---|
| `bert-base-uncased` / `bert-large-uncased` | 110M / 340M | 英文，训练前转成小写 |
| `bert-base-cased` / `bert-large-cased` | 110M / 340M | 英文，保留大小写 |
| `bert-base-multilingual-uncased` / `large` | 110M / 340M | 102 种语言的小写文本 |
| `bert-base-chinese` | 110M | 简体和繁体，按字切 |

`uncased` 是不区分大小写，`cased` 是保留大小写。中文模型的词表以字为单位，生僻字会落到 `UNK`，需要词向量的任务不能把字向量直接当成词向量。

### 预训练任务一：Masked LM

从左到右的语言模型看不到右边。BERT 在原文里随机抽出 15% 的 token 来预测，从而两边都能用。

被抽中的 token 有三种替换：

| 概率 | 做法 | 目的 |
|---|---|---|
| 80% | 换成 `[MASK]` | 迫使模型靠上下文把这个位置填回来 |
| 10% | 换成一个随机词 | 挡住「看见原词就照抄」 |
| 10% | 保持原词 | 让模型仍然见到真实文本 |

模型不知道哪个位置被动过，只能学上下文。15% 这个比例是为了不要把句子拆得太碎。

全换成 `[MASK]` 会有偏差：微调时输入里没有 `[MASK]`，模型在预训练里又没见过这些词的原样。80% 承担预测，20% 把原词或随机词留在输入里。随机的那 10% 专门对付照抄。

只有这 15% 的位置产生 MLM 损失，所以 BERT 比「每个词都预测下一个」的从左到右模型收敛慢。`[MASK]` 只出现在预训练，推理和微调里没有，这是暴露偏差。后面的 MacBERT 用近义词替换来减轻它。

### 预训练任务二：下一句预测

问答和自然语言推断要判断两句的关系。句子 A 取自原文；50% 的 B 是真实的下一句（IsNext），50% 是随机一句（NotNext）。课程记录的测试集准确率大约 97% 到 98%。后面几种变体认为这个任务太容易，负样本往往只是换了话题，模型不必理解顺序。它们拿掉 NSP，或者换成顺序预测。

### 接到下游任务

序列分类：取 `[CLS]`，加一层全连接，再 softmax。句对关系、单句分类、抽取式问答、序列标注（NER）都是换这个最上面的头，编码器保持不变。

论文给出的微调起点：batch 16 或 32，Adam 学习率 `5e-5`、`3e-5`、`2e-5`，epoch 3 或 4。

### 长文本

输入上限 512，其中两个位置留给 `[CLS]` 和 `[SEP]`，正文最多 510。更长的文本按截断来构造样本：

- 只留头部：前 510 个 token。
- 只留尾部：最后 510 个 token。
- 头加尾：总长 800 以内取前 128 加后 382；更长则取前 256 加后 254。

### 使用上的限制

- 参数多，资源和延迟都高。压缩手段在 `05`。
- 中文按字切，词级应用和生僻字要另做处理。
- MLM 的 `[MASK]` 造成预训练和下游的输入分布不一致，掩码比例不能再提高。
- 每个 batch 只有 15% 的 token 进入损失，训练步数要更多。

## 5. ALBERT

论文是《A Lite BERT for Self-supervised Learning of Language Representations》。骨架仍是 Transformer 编码器，目标是把预训练做小，同时把关键能力留下。相对于「更多语料、更深、更多任务」这条加规模的路，它做了五处减法。

**词嵌入因式分解。** 词向量本身信息有限，语义主要在隐藏层。嵌入维不必等于隐藏维。BERT 的嵌入矩阵大约是 `30000 × 768 ≈ 2304 万`。ALBERT 先投到一个中间维 `E`（常取 128），再投到隐藏维：`30000 × 128 + 128 × 768 ≈ 394 万`。

**层间共享参数。** BERT 每一层编码器有自己的注意力和前馈。ALBERT 所有层共用一套。12 层的 base、24 层的 large，这一块参数大约变成原来的 1/12、1/24。

**用 SOP 换掉 NSP。** Sentence Order Prediction 的正样本是原文顺序 `[A, B]`，负样本是对调后的 `[B, A]`。两句仍然来自同一处，模型必须看顺序，不能只靠话题是否跳跃。

**去掉 Dropout。** 训练到 100 万个 batch 仍没有过拟合。去掉之后下游效果更好。这是课程里记录的、大规模预训练里 Dropout 帮倒忙的一个例子，ALBERT v2 也因此成为当时主流模型里不使用 Dropout 的一个。

**MLM 的两处调整。** BERT 前 90% 的步用长度 128，后 10% 才用 512。ALBERT 在 90% 的步里就用 512，上下文更长。遮罩从单 token 改成 n-gram，长度 1、2、3 的概率按 `6/11`、`3/11`、`2/11`。

`albert-tiny` 只有 4 层、约 180 万参数。课程记录的速度大约是 BERT 的 10 倍，LCQMC 语义相似度测试集 85.4%，比 `bert-base` 低 1.5 个点。

## 6. RoBERTa

论文是《RoBERTa: A Robustly Optimized BERT Pretraining Approach》。结构和 BERT 相同，改的是训练配方。

| 改动 | 内容 |
|---|---|
| 数据 | 从 16GB 加到约 160GB：Books+Wiki 16GB，CC-News 76GB，OpenWebText 38GB，Stories 31GB |
| batch | 从 256 加大到最高约 8000 |
| 步数 | 训练更久 |
| NSP | 去掉 |
| 掩码 | 静态改成动态。BERT 在预处理时遮一次，之后每个 epoch 重复；RoBERTa 每次喂进模型时重新抽 |
| 分词 | 字符级 BPE 改成字节级 BPE，词表从约 3 万增到约 5 万。base 大约多 1500 万参数，large 大约多 2000 万 |

去掉 NSP 的对照实验，效果从低到高是：句段加 NSP，句子对加 NSP，允许跨文档的连续长句，不跨文档的连续长句。结论有三条：短句拼成句段更好；不要 NSP；尽量别跨文档。

## 7. MacBERT

哈工大针对中文，论文是《Revisiting Pre-trained Models for Chinese Natural Language Processing》。骨架大部分沿用 BERT，两处不同。

掩码改成全词加 n-gram。1 到 4 个字的比例是 40%、30%、20%、10%。被选中的 15% 里，80% 用 Word2Vec 近义词替换，10% 随机词，10% 原词。近义词找不到就退回随机词。这样输入里不再出现只在训练时存在的 `[MASK]`，减轻暴露偏差。

NSP 换成 ALBERT 的 SOP。阅读理解上表现突出。

## 8. SpanBERT

三件事：

1. 连续遮一段比随机遮单字好。先按几何分布抽 span 长度，再按均匀分布抽起点，然后整段遮住。论文里被遮文本的平均长度是 3.8。整词遮罩（BERT-WWM）是同一思路的前一步；百度 ERNIE 则按命名实体整段遮。
2. Span Boundary Objective。用 span 外侧左右两个边界词的向量，加上被遮位置的位置向量，去预测 span 里原来的词。向量拼接后过 GeLU、LayerNorm 和两层全连接。SBO 的损失和 MLM 的损失相加。抽取式问答从这里获益。
3. 再次看到去掉 NSP、用一条长句训练更好。负样本若来自另一篇文档，会给 MLM 带进话题噪声；NSP 本身也偏容易；长句能学到更远的依赖。

## 9. ELMo 和 GPT

ELMo 早于 BERT，用双层双向 LSTM，不是 Transformer。底层是嵌入，中间是左向和右向各一套 LSTM，上层把几层的表示加权合成。预训练分两段：先用语言模型得到静态词向量和 LSTM，再把带上下文的句子送进去，得到随上下文变化的词向量。和 GloVe 那种固定向量比，多义词更清楚。它的「双向」是左右两个单向模型各算一遍再拼接，左右没有在同一层里互相看见。特征抽取也不如自注意力。

GPT 用解码器。原始解码器三层子注意力里，和编码器交互的那一层被去掉，只留因果多头自注意力和前馈。12 块这样的层叠起来。预训练是无监督的从左到右语言模型，只看左边预测当前词；第二阶段才在下游标注上微调。

## 10. 三者放在一起

| | 特征抽取 | 上下文 |
|---|---|---|
| ELMo | 双层双向 LSTM，左右分开算再拼接 | 表面上双向 |
| GPT | 解码器，无交叉注意力 | 只有左边 |
| BERT | 编码器 | 同一层里左右一起看 |

BERT 的 MLM 让左右在同一次前向里交互。GPT 的因果掩码把右边挡住。ELMo 的两个方向直到融合时才见面。
