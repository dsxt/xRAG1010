# Transformer

这一章按阶段5的六个 demo 把模型从头接起来。默认宽度沿用演示：`d_model = 512`，8 个头，每个头 64 维，编码器和解码器各 6 层，前馈中间宽度在演示里是 1024（论文是 2048）。词表大小在演示里是 1000，只为了把张量跑通。

数据流：

```text
源词下标 → 嵌入 × √d_model → 加上位置编码
        → 编码器 × 6
目标词下标 → 嵌入 × √d_model → 加上位置编码
        → 解码器 × 6（每层：带掩码的自注意力 → 交叉注意力 → 前馈）
        → 线性层 → softmax，得到词表上的概率
```

## 1. 输入

### 词嵌入

`nn.Embedding(vocab_size, d_model)` 把词下标变成向量。输出再乘 `√d_model`。嵌入维度一大，初始值往往很小，和后面位置编码的正弦值不在一个量级；乘上这个系数，两者相加时谁也不会把谁淹没，训练更稳。

形状：`[batch, seq_len] → [batch, seq_len, d_model]`。

### 位置编码

自注意力本身不分先后，位置要另加。演示用的是固定正弦编码，不参与训练：

```text
PE(pos, 2i)   = sin(pos / 10000^(2i / d_model))
PE(pos, 2i+1) = cos(pos / 10000^(2i / d_model))
```

实现：先做一块 `[max_len, d_model]` 的零矩阵，偶数维填 sin，奇数维填 cos，再在 batch 维上扩成 `[1, max_len, d_model]`，用 `register_buffer` 存下来。它跟着模型走，优化器不更新它。

前向时只取前 `seq_len` 行，和嵌入相加，再 Dropout。句子比 `max_len` 长的部分，这块编码覆盖不到。演示的 `max_len` 是 60。

BERT 把这三项里的位置换成了可学习的向量，上限 512，见 `03`。

## 2. 缩放点积注意力

一个函数覆盖三种用法，差别只在 Q、K、V 是否相同，以及有没有掩码。

| 位置 | Q、K、V | 掩码 |
|---|---|---|
| 编码器自注意力 | 三个都是这一层的输入 | 无。每个词看全句 |
| 解码器自注意力 | 三个都是解码器这一层的输入 | 因果掩码。当前词看不到未来的目标词 |
| 交叉注意力 | Q 来自解码器，K 和 V 来自编码器输出 | 演示里没有。Q 的每个位置看整个源句 |

```text
scores = Q Kᵀ / √d_k
scores = mask_fill(scores, mask == 0, -1e9)
weight = softmax(scores)          # 在最后一维，也就是 key 那一维
输出   = weight V
```

`d_k` 是 Q 的最后一维。点积会随维度变大，softmax 容易饱和，除以 `√d_k` 把数值拉回来。

掩码里 1 是可见，0 是屏蔽。屏蔽位填 `-1e9` 再做 softmax，`exp(-1e9)` 趋近 0，这个位置分到的权重就没了。直接填 0 不行，0 经过 softmax 仍然有正的权重。

因果掩码是下三角：位置 i 只能看见列 `j <= i`。`torch.tril` 得到这块 1。演示代码有的地方用了 `torch.triu`，可见范围会翻成「自己和右边」。读源码时以上面的约定为准。

## 3. 多头

`d_model` 必须能被头数整除。`head_dim = d_model / head`。

四次线性变换：

1. Q 的投影：这一头在找什么。
2. K 的投影：其他位置能提供什么。
3. V 的投影：提供出来的内容。
4. 多头拼回去之后再投影一次，把各头的结果混匀。

Q、K、V 各自投影后，形状从 `[batch, seq, d_model]` 变成 `[batch, head, seq, head_dim]`，按头并行做上一节的注意力，再转回 `[batch, seq, d_model]`，过第 4 个线性层。

掩码在头这一维上广播。演示把 `[head, seq, seq]` 扩成 `[1, head, seq, seq]`，一个 batch 里的每句话共用一份掩码。

`clones` 用 `copy.deepcopy` 做 N 份结构相同、参数各自独立的层。浅拷贝会让多层共用同一套权重。

## 4. 前馈、层归一化、残差

前馈对每个位置单独做，位置之间不再互相看：

```text
Linear(d_model → d_ff) → ReLU → Dropout → Linear(d_ff → d_model)
```

注意力的打分是线性的，ReLU 补上非线性。论文用 ReLU；后来的模型常换成 GELU，结构仍是「升维再降回来」。

层归一化在最后一维（也就是 `d_model`）上做：

```text
y = k * (x - mean) / (std + ε) + b
```

`k` 和 `b` 是 `nn.Parameter`，形状 `[d_model]`，会进优化器。`ε = 1e-6`，避免除以 0。`keepdim=True`，均值的形状留在 `[batch, seq, 1]`，才能和 `x` 相减。

每个子层外面包一层 `SubLayerConnection`。演示用的是 Pre-Norm：

```text
输出 = Dropout(子层(LayerNorm(x))) + x
```

原始 Transformer 论文是 Post-Norm：先子层，再加上残差，最后归一化。Pre-Norm 把归一化放进残差支路里面，深层堆起来更不容易梯度消失，GPT-2 之后的主干多用这种摆法。残差要求子层的输入输出都是 `d_model`，所以前馈最后一定要投回 `d_model`。

## 5. 编码器

一层编码器：自注意力子层，然后前馈子层。堆 6 层。6 层之后再做一次层归一化，把堆完之后的数值稳住。每个子层内部已经有归一化，课程把这最后一次标成可选。

自注意力的 Q、K、V 都是该层输入，掩码为空。

## 6. 解码器

一层解码器三个子层，都走 Pre-Norm 残差：

1. 带因果掩码的自注意力。Q、K、V 都是解码器输入。
2. 交叉注意力。Q 是上一子层的输出，K 和 V 都是编码器的最终输出。解码器的每个位置由此拿到源句。
3. 前馈。

堆 6 层。同一份编码器输出被每一层的交叉注意力重复使用。

## 7. 输出

解码器输出 `[batch, tgt_len, d_model]`，线性层投到词表大小，再 softmax。演示把概率直接返回。

真正训练时更常见的是线性层停在 logits，损失用 `CrossEntropyLoss`。它内部带 log-softmax，外面再 softmax 一次会重复，数值也不如 logits 稳。

## 8. 整网前向

`MyTransformer` 把五块接起来：源侧嵌入加位置、编码器、目标侧嵌入加位置、解码器、输出层。源嵌入和目标嵌入是两套参数。

```text
encoder_data = Encoder(PositionalEncoding(Embedding(src)))
decoder_data = Decoder(
    PositionalEncoding(Embedding(tgt)),
    mask,
    encoder_output=encoder_data,
)
probs = softmax(Linear(decoder_data))
```

一次前向里，源句每个位置并行；目标侧因为因果掩码，位置 i 的表示只依赖 `0..i`。生成时仍然要一个词一个词地把新词接到 `tgt` 后面再跑，这一步的串行留在采样，不留在层内部。

## 9. 从这里分出后面的模型

- 只要编码器，在输入侧加 `[CLS]`，用它的最终隐藏状态做分类，就是 BERT 这一支，见 `03`。
- 只要解码器，删掉交叉注意力，留下因果自注意力和前馈，从左往右预测下一个词，就是 GPT 这一支，也在 `03`。
- 编码器加解码器保留下来，就是 T5 这一类，以及这一章自己的翻译模型。
