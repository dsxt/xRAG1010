import os
os.environ["TF_ENABLE_ONEDNN_OPTS"]="0"
import torch
import torch.nn as nn

# HuggingFace 提供的据集加载工具, 可以加载本地数据, 也可以加载公开数据源
from datasets import load_dataset
from torch.utils.data import DataLoader
from tqdm import tqdm
# 导入BERT相关组件(中文文本分词器, 预训练的BERT模型)
from transformers import BertTokenizer, BertModel
# 终端打印美化
from rich import print

# 指定运行设备
# Windows电脑
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
# Mac电脑
# device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")

# 1- 创建预训练模型的实例对象
model_path = r"D:\PretrainedModel\bert-base-chinese"
bert_tokenizer = BertTokenizer.from_pretrained(model_path)
bert_model = BertModel.from_pretrained(model_path).to(device)
# print(f"bert_model内部结构-->{bert_model}")

predict_mask_index = 16 # 要对句子中索引为多少的词进行填空

# 2- 每个批次数据的具体处理
"""
    data参数的数据结构：
        [
            {"label":值,"text":值},
            {"label":值,"text":值}
            ...
        ]
"""
def collate_fn(data):
    # 1- 获取所有的句子
    # sents格式 [句子1, 句子2, ....]
    sents = [line_dict["text"] for line_dict in data]

    # 2- 分词得到词索引
    """
        padding的取值区别：推荐设置为True
        1- max_length：如果句子的长度不够max_length指定的长度，那么会在句子的末尾补0，直到句子长度达到max_length
        2- True：
            2.1- 获得当前批次中最长句子的长度
            2.2- 如果最长的长度不超过max_length的值，那么max_length以当前批次中最长句子的长度为准
            2.3- 如果最长的长度超过了max_length的值，按照padding="max_length"的规则进行处理
    """
    data_tensor = bert_tokenizer.batch_encode_plus(
        sents,
        return_tensors="pt",
        max_length=32,
        padding=True,
        truncation=True
    )
    # print(data_tensor)

    # 3- 提取各个字段
    input_ids = data_tensor["input_ids"]            # 句子分词后的词索引列表
    token_type_ids = data_tensor["token_type_ids"]  # 对应位置的词来自于哪条句子
    attention_mask = data_tensor["attention_mask"]  # 对应位置的词是原始词（值为1）还是padding后的词内容（值为0）

    # 4- 将指定位置的原始词的索引用[MASK]的索引替代
    # 4.1- 将原始词的索引保存下来，作为目标值label来使用
    labels = input_ids[:,predict_mask_index].clone()    # 对每条句子都进行处理
    # print(f"原始词的索引-->{labels}")

    # 4.2- 用[MASK]的索引替代指定位置的索引
    # 获得[MASK]的索引
    # print("mask-->",bert_tokenizer.mask_token)
    mask_id = bert_tokenizer.get_vocab()[bert_tokenizer.mask_token]
    # 替换
    # print(f"替换前的input_ids-->{input_ids}")
    input_ids[:,predict_mask_index] = mask_id
    # print(f"替换后的input_ids-->{input_ids}")

    return input_ids,token_type_ids,attention_mask,labels

# 3- 获得数据加载器
def get_dataloader(task_type):
    # 1- 读取文件
    file_dict = {
        "train":"train.csv",
        "test":"test.csv"
    }
    data_set = load_dataset(path="../data",data_files=file_dict,split=task_type)

    # 2- 数据过滤
    """
        为什么要>32？
        答：
            1- 可以随意设置
            2- 我们现在决定要对索引为16地方的词进行挖孔处理，因此长度必须大于16。如果长度不够，会出现索引越界异常out of index
    """
    data_set = data_set.filter(lambda line_dict:len(line_dict["text"])>32)

    # 3- 创建数据加载器
    """
        参数解释：
            drop_last：如果最后一个批次中样本条数不够batch_size指定的条数，那么最后一个批次就不用来训练模型
            collate_fn：每个批次数据的指定处理函数名称，注意：不可以加小括号。Pytorch底层自动给它传递每个批次的数据
    """
    dataloder = DataLoader(
        dataset=data_set,
        batch_size=8,
        shuffle=True,
        drop_last=True,
        collate_fn=collate_fn
    )

    return dataloder

# 4- 自定义微调网络结构
class AiModel(nn.Module):
    def __init__(self):
        # 1- 初始化父类
        super().__init__()

        # 2- 创建自定义的网络层
        # 注意：[MASK]对应的位置，通过softmax会输出词表个数的概率值，概率之和为1
        self.linear = nn.Linear(768, bert_tokenizer.vocab_size)

    def forward(self,input_ids,token_type_ids,attention_mask):
        # 1- 调用预训练模型
        with torch.no_grad():
            bert_output = bert_model(input_ids,token_type_ids,attention_mask)

        # 2- 调用自定义的网络层
        # 取每条句子中中索引为16对应的隐藏状态信息。因为[MASK]所在位置是16
        # bert_output.last_hidden_state 张量形状 [batch_size,seq_len,hidden_size]
        return self.linear(bert_output.last_hidden_state[:,predict_mask_index])

# 5- 模型训练
def train_model():
    # 1- 准备数据
    dataloader = get_dataloader("train")

    # 2- 禁用预训练模型的梯度下降
    for param in bert_model.parameters():
        param.requires_grad_(False)

    # 3- 创建实例对象
    # 3.1- 创建自定义微调网络对象
    model = AiModel().to(device=device)
    # 3.2- 损失函数对象
    loss = nn.CrossEntropyLoss()
    # 3.3- 优化器对象
    optim = torch.optim.AdamW(params=model.parameters(),lr=1e-3)

    # 4- 切换模式
    model.train()

    # 5- 模型训练
    epochs = 10
    for epoch in range(epochs):
        for i,(input_ids,token_type_ids,attention_mask,labels) in enumerate(tqdm(dataloader), start=1):
            # 5.1- 将数据发送到指定设备
            input_ids = input_ids.to(device=device)
            token_type_ids = token_type_ids.to(device=device)
            attention_mask = attention_mask.to(device=device)
            labels = labels.to(device=device)

            # 5.2- 前向传播
            pred_result = model(input_ids,token_type_ids,attention_mask)

            # 5.3- 计算损失值
            loss_value = loss(pred_result,labels)

            # 5.4- 固定代码
            optim.zero_grad()           # 梯度清零
            loss_value.sum().backward() # 反向传播
            optim.step()                # 更新参数：目前是只更新我们自定义网络层的参数

            # 5.5- 每隔10个批次打印相关信息
            if i%10==0:
                # 统计预测正确的样本条数
                pred_index = torch.argmax(pred_result,dim=-1)
                correct_cnt = (pred_index==labels).sum().item()
                # 计算准确率
                acc_rate = correct_cnt/len(labels)
                print(f"第{epoch+1}轮次，第{i}批次，准确率是{round(acc_rate,4)}")

            # 注意：实际工作中没有这段代码。目前是为了快速看效果
            if i>10:
                break

        # 6- 保存训练好的模型
        torch.save(model.state_dict(),f"../model/bert_mask_{epoch+1}.pkl")

# 6- 模型预测
def predict_model():
    # 1- 加载数据
    dataloader = get_dataloader("test")

    # 2- 加载训练好的模型
    model = AiModel().to(device=device)
    model.load_state_dict(torch.load("../model/bert_mask_1.pkl"))

    # 3- 切换模式
    model.eval()

    # 4- 变量初始化
    correct_cnt = 0         # 预测正确的总样本条数
    total_sample_cnt = 0    # 总样本条数

    # 5- 预测
    for i,(input_ids,token_type_ids,attention_mask,labels) in enumerate(tqdm(dataloader), start=1):

        # 目前是预测阶段，因此不允许模型进行梯度下降
        with torch.no_grad():
            # 5.1- 将数据发送到指定设备
            input_ids = input_ids.to(device=device)
            token_type_ids = token_type_ids.to(device=device)
            attention_mask = attention_mask.to(device=device)
            labels = labels.to(device=device)

            # 5.2- 预测
            pred_result = model(input_ids,token_type_ids,attention_mask)

            # 5.3- 更新变量：统计预测正确的样本条数
            pred_index = torch.argmax(pred_result, dim=-1)
            correct_cnt += (pred_index==labels).sum().item()
            total_sample_cnt += len(labels)

            # 5.4- 每隔10个批次打印相关信息
            if i%10==0:
                # 计算准确率
                acc_rate = correct_cnt/total_sample_cnt

                # 对原始句子、原始词、预测词进行解析：词索引 -> 词内容
                # 原始句子解析
                true_sent = bert_tokenizer.decode(input_ids[0],skip_special_tokens=True,clean_up_tokenization_spaces=True)
                # 原始词解析
                true_word = bert_tokenizer.decode(labels[0])
                # 预测词解析
                print(f"pred_index-->{pred_index}")
                print(f"pred_index-->{pred_index.shape}")
                pred_word = bert_tokenizer.decode(pred_index[0])

                print(f"准确率{round(acc_rate,4)}，原始句子{true_sent}，原始词{true_word}，预测词{pred_word}")

            # 注意：实际工作中没有这段代码。目前是为了快速看效果
            if i > 10:
                break

if __name__ == '__main__':
    # 测试数据加载器
    # dataloader = get_dataloader("train")
    # 为什么还需要写for循环：get_dataloader返回的是数据生成器，是一个懒加载的对象
    # for i in dataloader:
    #     print(i)
    #     break

    # 1- 模型训练测试
    train_model()

    # 2- 模型预测
    predict_model()
















