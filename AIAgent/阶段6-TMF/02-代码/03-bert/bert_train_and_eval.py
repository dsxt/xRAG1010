from config import Config
from data_preprocessing import build_dataloader
from bert_model import BertClassifierModel
import torch
import torch.nn as nn
from tqdm import tqdm
from sklearn.metrics import accuracy_score,precision_score,recall_score,f1_score

config = Config()

def eval_model(model):
    # 1- 加载验证集数据
    dev_dataloader = build_dataloader(datapath=config.dev_datapath,shuffle=False)

    # 2- 模型评估
    # 2.1- 定义用来计算准确率的变量
    all_pred_result = []    # 预测结果列表
    all_true_result = []    # 真实结果列表

    model.eval()
    with torch.no_grad():
        for i,batch in enumerate(tqdm(dev_dataloader), start=1):
            # 2.2- 将数据发送到对应设备
            input_ids, attention_mask, labels = batch
            input_ids = input_ids.to(config.device)
            attention_mask = attention_mask.to(config.device)
            labels = labels.to(config.device)

            # 2.3- 前向传播：预测
            pred_output = model(input_ids,attention_mask)
            pred_index = torch.argmax(pred_output,dim=-1)
            # cpu()：因为不涉及张量的计算，因此为了节约GPU资源，可以将数据转到CPU上再处理
            all_pred_result.extend(pred_index.cpu().tolist())
            all_true_result.extend(labels.cpu().tolist())

    # 3- 计算评估指标
    f1score = f1_score(all_true_result,all_pred_result,average="macro")
    # 准确率
    accuracy = accuracy_score(all_true_result,all_pred_result)
    precision = precision_score(all_true_result,all_pred_result,average="macro")
    recall = recall_score(all_true_result,all_pred_result,average="macro")

    return f1score, accuracy, precision, recall

def train_and_eval():
    # 1- 加载数据
    train_dataloader = build_dataloader(datapath=config.train_datapath,shuffle=True)

    # 2- 创建模型
    model = BertClassifierModel().to(device=config.device)

    # 3- 损失函数对象
    loss = nn.CrossEntropyLoss()

    # 4- 优化器对象
    optimizer = torch.optim.AdamW(params=model.parameters(),lr=5e-5)

    # 5- 其他变量
    epochs = 1

    # 6- 训练
    model.train()   # 切换为训练模式
    best_f1score = 0.0  # f1score历史最高分

    for epoch in range(epochs):
        # 6.1- 训练指标
        total_loss = 0.0    # 总损失值

        for i,batch in enumerate(tqdm(train_dataloader),start=1):
            # 6.2- 将数据发送到对应的设备
            input_ids, attention_mask, labels = batch
            input_ids = input_ids.to(config.device)
            attention_mask = attention_mask.to(config.device)
            labels = labels.to(config.device)

            # 6.3- 前向传播：调用模型
            pred_output = model(input_ids,attention_mask)

            # 6.4- 算损失值
            loss_value = loss(pred_output, labels)
            total_loss += loss_value

            # 6.5- 固定代码
            optimizer.zero_grad()
            loss_value.sum().backward()
            optimizer.step()

            # 6.6- 每隔100个批次对已训练的模型进行验证
            # i==len(train_dataloader)为了防止最后不够100个批次
            if i%100==0 or i==len(train_dataloader):
                # 6.6.1- 调用评估函数
                f1score, accuracy, precision, recall = eval_model(model)
                print(f"第{i}批次，f1score={f1score}，accuracy={accuracy}，precision={precision}，recall={recall}")

                # 6.6.2- 如果验证后发现模型效果有提升（也就是f1score比上次的要大），那就保存模型
                if f1score>best_f1score:
                    torch.save(model.state_dict(),config.save_model)
                    best_f1score = f1score  # 更新历史最高分

                # 6.6.3- 将模型的模式切回为训练模式
                model.train()

if __name__ == '__main__':
    train_and_eval()