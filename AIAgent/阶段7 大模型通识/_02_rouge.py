#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author  : Vincent
@Time    : 2026/1/4 09:41
@File    : _02_rouge.py
@Function :
"""
# 第一步：安装rouge-->pip install rouge
from rouge import Rouge

# 生成文本
generated_text = "it is a nice day today"

# 参考文本列表
reference_texts = ["today is a nice day"]

# 计算 ROUGE 指标
rouge = Rouge()
scores = rouge.get_scores(generated_text, reference_texts[0])

# 打印结果
print("ROUGE-1 precision:", scores[0]["rouge-1"]["p"])
print("ROUGE-1 recall:", scores[0]["rouge-1"]["r"])
print("ROUGE-1 F1 score:", scores[0]["rouge-1"]["f"])
# ROUGE-1 precision: 0.8
# ROUGE-1 recall: 0.6666666666666666
# ROUGE-1 F1 score: 0.7272727223140496
print("全部结果：", scores)
# rouge-l 计算最长公共子序列
