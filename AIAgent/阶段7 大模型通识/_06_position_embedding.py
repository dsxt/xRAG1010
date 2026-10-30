#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author  : Vincent
@Time    : 2025/12/8 16:51
@File    : _06_position_embedding.py
@Function :
是为了演示同一个token在不同的位置，通过注意力机制以后，输出结果是一样的
"""
import torch
import torch.nn as nn

# 参数
d_model = 8  # 隐藏层维度
num_heads = 2
seq_len = 4
batch = 1

# 创建 MHA
mha = nn.MultiheadAttention(embed_dim=d_model, num_heads=num_heads, batch_first=True)

# 无位置编码的输入
x = torch.randn(batch, seq_len, d_model)

# 原始输出
y1, attn1 = mha(x, x, x)
# 构造置换（例如将顺序改为 2, 0, 3, 1）
perm = torch.tensor([2, 0, 3, 1])
x_perm = x[:, perm, :]

print("原始输入：", x)
print("置换后的输入：", x_perm)

print("===" * 10)
print("原始输出：", y1)

# 置换后的输出
y2, attn2 = mha(x_perm, x_perm, x_perm)
print("置换后的输出：", y2)
# 将 y1 应用相同置换（理论上应满足 y2 = y1[perm]）
y1_perm = y1[:, perm, :]

print("是否满足置换等变性：", torch.allclose(y1_perm, y2, atol=1e-5))
