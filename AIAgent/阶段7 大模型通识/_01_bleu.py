#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author  : Vincent
@Time    : 2026/1/4 09:39
@File    : _01_bleu.py
@Function :
"""
import math

# 飞书课件: https://vinctchanx.feishu.cn/wiki/XUKxwVBnuilzjSkLsfXcb1vZnFc?from=from_copylink
# 第一步安装nltk的包-->pip install nltk
from nltk.translate.bleu_score import sentence_bleu


def cumulative_bleu(reference, candidate):
    """
    计算bleu的 1234，weights对应的权重
    """
    bleu_1_gram = sentence_bleu(reference, candidate, weights=(1, 0, 0, 0))
    bleu_2_gram = sentence_bleu(reference, candidate, weights=(0, 1, 0, 0))
    bleu_3_gram = sentence_bleu(reference, candidate, weights=(0, 0, 1, 0))
    bleu_4_gram = sentence_bleu(reference, candidate, weights=(0, 0, 0, 1))

    # print('bleu 1-gram: %f' % bleu_1_gram)
    # print('bleu 2-gram: %f' % bleu_2_gram)
    # print('bleu 3-gram: %f' % bleu_3_gram)
    # print('bleu 4-gram: %f' % bleu_4_gram)

    return bleu_1_gram, bleu_2_gram, bleu_3_gram, bleu_4_gram


# 生成文本
generated_text = "it is a nice day today".split(" ")

# 参考文本列表
reference_texts = ["today is a nice day".split(" ")]

# 计算 Bleu 指标
c_bleu = cumulative_bleu(reference_texts, generated_text)

# 打印结果

print("The Bleu score is:", c_bleu)
# The Bleu score is: (0.8333333333333334, 0.7071067811865476, 0.63287829698514, 0.537284965911771)
bleu = sentence_bleu(reference_texts, generated_text, weights=(0.25, 0.25, 0.25, 0.25))
print("接口返回结果：", bleu)

print("手动计算结果：",
      math.exp(sum([0.25 * math.log(p) for p in c_bleu]))
      )
