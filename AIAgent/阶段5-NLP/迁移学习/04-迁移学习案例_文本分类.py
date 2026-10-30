import os
os.environ["TF_ENABLE_ONEDNN_OPTS"]="0"
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from transformers import BertTokenizer,BertModel
from datasets import load_dataset   # HuggingFace数据加载方法
from tqdm import tqdm

# 1- 定义变量
# 运行的设备
device = ("cuda" if torch.cuda.is_available() else "cpu")
# 预训练模型路径
model_path = r"D:\PretrainedModel\bert-base-chinese"
# 创建词汇映射器：词汇映射器只是对句子进行分词，然后得到词索引，不涉及任何的需要学习的参数，因此可以不用放到GPU或CPU上
tokenizer = BertTokenizer.from_pretrained(model_path)
# 创建Bert预训练模型实例对象：需要学习相关的模型参数，因此必须放到GPU或CPU上
bert_model = BertModel.from_pretrained(model_path).to(device=device)

# 2- 数据探索【可选】
def load_data():

    """
        参数解释：
            path：既能够指定文件目录，也能够指定文件类型。推荐用来指定文件目录
            data_files：可以传递文件路径，还可以传递数据类型和文件路径字典的形式。推荐直接传递文件路径
            split：如果data_files是具体的文件路径，那么这里必须给train
    """
    # 加载方式一：【掌握】如果data_files是具体的文件路径，那么这里必须给train
    data = load_dataset(path="../data", data_files="train.csv", split="train")

    # 加载方式二：如果data_files是具体的文件路径，那么这里必须给train
    # data = load_dataset(path="data", data_files="test.csv", split="train")

    # 加载方式三：path：既能够指定文件目录，也能够指定文件类型。推荐用来指定文件目录
    # data = load_dataset(path="csv", data_files="data/test.csv", split="train")

    # 加载方式四：数据类型和文件路径以字典的形式传递
    # file_dict = {
    #     "train":"../data/train.csv",
    #     "test":"../data/test.csv",
    #     "aaaa":"../data/test.csv"
    # }
    # data = load_dataset(path="csv", data_files=file_dict, split="train")
    # data = load_dataset(path="csv", data_files=file_dict, split="test")
    # data = load_dataset(path="csv", data_files=file_dict, split="aaaa")

    print(type(data))
    print(len(data))
    print(data)
    print(data[0])

# 3- 针对每个批次数据的具体处理函数
def collate_fn(batch_data):
    # print(batch_data)

    # 1- 拆分数据分别得到特征内容和目标值
    sents = [item_dict.get("text") for item_dict in batch_data]
    labels = [item_dict.get("label") for item_dict in batch_data]

    # 2- 句子内容进行分词处理
    data_tensor = tokenizer.batch_encode_plus(
        sents,
        return_tensors="pt",
        max_length=300,
        padding="max_length",
        truncation=True
    )
    # print(data_tensor)

    # 3- 提取处理后的内容
    input_ids = data_tensor["input_ids"]
    token_type_ids = data_tensor["token_type_ids"]
    attention_mask = data_tensor["attention_mask"]

    # 4- 目标值转成张量
    labels = torch.tensor(labels,dtype=torch.long)

    return input_ids,token_type_ids,attention_mask,labels

# 4- 创建数据加载器
def get_dataloader(file_path):
    # 1- 加载指定路径的文件
    dataset = load_dataset(path="../data",data_files=file_path,split="train")

    # 2- 创建数据加载器
    """
        参数解释：
            dataset：数据集对象
            batch_size：每个批次中的样本条数。注意：如果没有进行句子长度规范的限定，那么该值只能是1
            shuffle：为了打散数据，让模型训练更加充分
            drop_last：如果最后一个批次的数据不够一个完整的批次，是否删除最后一个批次的样本数据。
            collate_fn：针对每个批次的样本数据，进行针对性的处理
    """
    dataloader = DataLoader(
        dataset=dataset,
        batch_size=8,
        shuffle=True,
        drop_last=True,
        collate_fn=collate_fn
    )

    return dataloader

# 5- 搭建模型：Bert预训练模型+我们自定义的网络层
class AiModel(nn.Module):
    def __init__(self):
        # 1- 初始化父类
        super().__init__()

        # 2- 在Bert预训练模型的后面，增加自己的线性网络结构层
        """
            这里的参数必须是768和2，为什么？
            答：
                768：Bert的词向量/隐藏状态向量维度是768维
                2：目前案例是二分类场景
        """
        self.linear = nn.Linear(in_features=768,out_features=2)

        # 想多少层就多少层
        # self.linear = nn.Linear(in_features=768,out_features=1024)
        # self.linear = nn.Linear(in_features=1024,out_features=512)
        # self.linear = nn.Linear(in_features=512,out_features=2)

    def forward(self,input_ids,token_type_ids,attention_mask):
        # 1- 先调用Bert预训练模型：得到预训练模型的结果（不是最终的结果）
        """
            为什么使用with torch.no_grad()？
            答：这行代码的作用是冻结预训练模型的反向传播。防止更新预训练模型的w和b。
                如果不使用，那么是参数全量微调，可能会导致训练时间增加，效果也不好
        """
        with torch.no_grad():
            bert_output = bert_model(input_ids,token_type_ids,attention_mask)

        # 2- 再调用我们自定义的网络层
        """
            为什么写成bert_output.last_hidden_state[:, 0]？
            答：
                1- last_hidden_state（张量形状[batch_size,seq_len,hidden_size]）表示取的是Bert模型最后一层隐藏层中每个时间步的隐藏状态。等价于多层RNN中output
                2- [:, 0]取到的是每条句子中第一个token（词），也就是[CLS]的隐藏状态
                3- Bert预训练模型的设计思想：只有编码器端，编码器端注意力是自注意力，也就是每个词都能够获得其他词的隐藏状态信息
                    [CLS]是一个固定的标识，每个句子的开头都有
                4- 结论：Bert做分类，固定写成bert_output.last_hidden_state[:, 0]
                    
                以上的解释，建议看论文：https://arxiv.org/pdf/1810.04805
                对应内容：The final hidden state corresponding to this token is used as the aggregate sequence representation for classification tasks. 
        """
        return self.linear(bert_output.last_hidden_state[:,0])

# 6- 模型训练
def train():
    # 1- 加载数据
    dataloader = get_dataloader("train.csv")

    # 2- 禁用预训练模型的梯度下降（也就是不允许反向传播更新w和b）
    # 强烈建议与前面的with torch.no_grad()一起使用
    for param in bert_model.parameters():
        param.requires_grad_(False)

    # 3- 创建对象
    # 3.1- 模型对象
    model = AiModel().to(device=device)
    # 3.2- 优化器对象
    optim = torch.optim.AdamW(params=model.parameters(),lr=1e-3)
    # 3.3- 损失函数对象
    loss = nn.CrossEntropyLoss()

    # 4- 切换到训练模型
    model.train()

    # 5- 训练
    epochs = 1
    for epoch in range(epochs):
        # 外层循环控制轮次

        for i,(input_ids,token_type_ids,attention_mask,labels) in enumerate(tqdm(dataloader), start=1):
            # 内层循环控制批次

            # 5.1- 将数据发送到对应的设置
            input_ids = input_ids.to(device=device)
            token_type_ids = token_type_ids.to(device=device)
            attention_mask = attention_mask.to(device=device)
            labels = labels.to(device=device)

            # 5.2- 前向传播：得到预测结果
            pred_result = model(input_ids,token_type_ids,attention_mask)

            # 5.3- 计算损失值
            loss_value = loss(pred_result,labels)

            # 5.4- 固定代码
            optim.zero_grad()       # 梯度清零
            loss_value.sum().backward() # 反向传播
            optim.step()            # 更新参数：我们自定义网络层中的w和b

            # 5.5- 阶段性的输出准确率信息
            if i%20==0:
                # 得到：预测正确的样本条数
                pred_index = torch.argmax(pred_result,dim=-1)
                pred_correct_cnt = (pred_index==labels).sum().item()
                # 计算准确率 = 预测正确的样本条数 / 总样本条数
                acc = pred_correct_cnt / len(labels)
                print(f"第{epoch+1}个轮次，第{i}批次，准确率为{round(acc,4)}")

            if i>100:
                break

    # 6- 保存训练好的模型
    torch.save(model.state_dict(),"../model/my_bert.pkl")

# 7- 模型预测
def predict():
    # 1- 加载数据
    dataloader = get_dataloader("test.csv")

    # 2- 加载训练好的模型
    model = AiModel().to(device)
    model.load_state_dict(torch.load("../model/my_bert.pkl"))

    # 3- 定义准确率统计变量
    correct_count = 0   # 预测正确的样本条数
    total_sample_count = 0  # 已经预测的总样本条数

    # 4- 预测
    model.eval()

    # 在当前代码中，with torch.no_grad()作用是对我们自定义的网络结构禁用梯度下降。原因是：现在是预测的过程
    with torch.no_grad():

        for i,(input_ids,token_type_ids,attention_mask,labels) in enumerate(tqdm(dataloader),start=1):
            # 4.1- 将数据发送到对应设备
            input_ids = input_ids.to(device)
            token_type_ids = token_type_ids.to(device)
            attention_mask = attention_mask.to(device)
            labels = labels.to(device)

            # 4.2- 预测得到结果
            pred_result = model(input_ids,token_type_ids,attention_mask)

            # 4.3- 计算准确率：算的累计平均准确率
            pred_index = torch.argmax(pred_result,dim=-1)
            correct_count += (pred_index==labels).sum().item()
            total_sample_count += len(labels)

            # print("labels-->",labels)
            # print("pred_index-->",pred_index)
            # print("input_ids-->",input_ids)

            # 4.4- 间隔输出相关指标
            if i % 20 == 0:
                # 计算准确率
                acc = correct_count / total_sample_count
                print(f"已预测的样本批次{i}，累计平均准确率{round(acc, 4)}")

                # 恢复得到原始句子内容
                text_content = "".join(tokenizer.convert_ids_to_tokens(input_ids[0], skip_special_tokens=True))
                print(f"每个批次中第一条样本的原始评价内容：{text_content}，预测的类别是：{pred_index[0]}")

if __name__ == '__main__':
    # 测试数据探索
    # load_data()

    # 测试数据加载器
    # dataloader = get_dataloader(file_path="train.csv")
    # for input_ids,token_type_ids,attention_mask,labels in dataloader:
    #     print(token_type_ids)
    #     break

    train()

    predict()



