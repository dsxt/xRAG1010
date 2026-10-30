#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author  : Vincent
@Time    : 2026/1/18 12:27
@File    : _07_scaled_attention.py
@Function :
"""
import torch
import torch.nn.functional as F
import math

torch.manual_seed(0)


def attention_test(dk, seq_len=10):
    """
    测试不同 dk 下，是否使用 sqrt(dk) 缩放的影响
    """
    print(f"\n===== dk = {dk} =====")

    # 构造 Q, K
    Q = torch.randn(seq_len, dk, requires_grad=True)
    K = torch.randn(seq_len, dk)

    # 1️⃣ 未缩放的 attention logits
    logits_no_scale = Q @ K.T
    attn_no_scale = F.softmax(logits_no_scale, dim=-1)

    # 2️⃣ 缩放后的 attention logits
    logits_scale = (Q @ K.T) / math.sqrt(dk)
    attn_scale = F.softmax(logits_scale, dim=-1)

    # 打印 logits 范围
    print("Logits (no scale): "
          f"min={logits_no_scale.min():.2f}, "
          f"max={logits_no_scale.max():.2f}")

    print("Logits (scaled):   "
          f"min={logits_scale.min():.2f}, "
          f"max={logits_scale.max():.2f}")

    # 打印 softmax 是否饱和
    print("Softmax max prob (no scale): ",
          attn_no_scale.max().item())

    print("Softmax max prob (scaled):   ",
          attn_scale.max().item())


# 分别测试不同维度
for dk in [8, 64, 256, 1024]:
    attention_test(dk)
