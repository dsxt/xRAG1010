import random

import torch
import torch.nn as nn
import re
from torch.utils.data import Dataset,DataLoader
import matplotlib.pyplot as plt
from tqdm import tqdm
plt.rcParams['font.sans-serif'] = ['SimHei']  # Mac本字体改为: ['Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

# 1- 定义变量
# 运行设备
device = ("cuda" if torch.cuda.is_available() else "cpu")
# 翻译开始标识的索引
SOS_TOKEN = 0
# 翻译结束标识的索引
EOS_TOKEN = 1
# 文件路径
file_path = "../data/eng-fra-v2.txt"
# 句子长度规范中句子的最大长度
MAX_LENGTH = 10

# 2- 数据清洗
def normalize_string(line):
    # 全部转小写，并且去除前后的空白字符
    line = line.lower().strip()

    # 在标点符号的前面增加空格
    line = re.sub(r"([.!?])",r" \1",line)

    # 去除特殊内容（除了26个字母和.!?），替换成空格
    line = re.sub(r"[^a-z.!?]+"," ",line)
    return line

# 3- 数据预处理
def getdata():
    # 1- 读取文件的所有行
    """
        大文件推荐用readline
        小文件用readline、readlines都行
    """
    with open(file_path,mode="r",encoding="UTF-8") as f:
        lines = f.readlines()
    # print(lines)

    # 2- 循环遍历每一行，拆分得到英语句子和法语句子。得到嵌套列表，格式如下
    # [["英语句子1","法语句子1"], ["英语句子2","法语句子2"]]
    # 普通版
    sen_pairs = []
    for line in lines:
        eng_fre = line.split("\t")

        # tmp_pair的格式是：["英语句子","法语句子"]
        tmp_pair = []
        for sen in eng_fre:
            tmp_pair.append(normalize_string(sen))

        sen_pairs.append(tmp_pair)

    # 简洁版：理解
    # sen_pairs = [[normalize_string(sen) for sen in line.split("\t")] for line in lines]
    # print(sen_pairs[:5])

    # 3- 分词
    # 3.1- 初始设置
    english_word2index = {"SOS":SOS_TOKEN,"EOS":EOS_TOKEN}
    english_word_n = 2
    french_word2index = {"SOS": SOS_TOKEN, "EOS": EOS_TOKEN}
    french_word_n = 2

    # 3.2- 分别对英语句子、法语句子进行分词
    for eng_fre in sen_pairs:
        # 英语
        for word in eng_fre[0].split(" "):
            # 去重处理
            if word not in english_word2index:
                english_word2index[word] = english_word_n
                english_word_n += 1

        # 法语
        for word in eng_fre[1].split(" "):
            # 去重处理
            if word not in french_word2index:
                french_word2index[word] = french_word_n
                french_word_n += 1

    # print(english_word2index)

    # 3.3- 将3.2中的词典（key是单词，value是索引）改成key是索引，value是单词的形式
    english_index2word = {value:key for key,value in english_word2index.items()}
    french_index2word = {value:key for key,value in french_word2index.items()}

    return english_word2index,english_index2word,english_word_n,french_word2index,french_index2word,french_word_n,sen_pairs

english_word2index,english_index2word,english_word_n,french_word2index,french_index2word,french_word_n,sen_pairs = getdata()

# 4- 自定义数据集Dataset
class MyPairsDataset(Dataset):
    def __init__(self,sen_pairs):
        # 设置属性值
        self.sen_pairs = sen_pairs
        self.sample_cnt = len(self.sen_pairs)   # 获得样本条数，也就是英语和法语句子对有多少对

    def __len__(self):
        # 获得样本条数
        return self.sample_cnt

    def __getitem__(self, index):
        """
        根据索引获得对应的样本数据。index是索引，从0开始
        """

        # 1- 防止index出现负数；防止index越界
        index = min(max(index,0), self.sample_cnt - 1)

        # 2- 获得对应索引的英语句子和法语句子
        x = self.sen_pairs[index][0]    # 英语句子
        y = self.sen_pairs[index][1]    # 法语句子

        # 3- 句子分词转成词索引，最终变成张量
        """
            为什么这里只是增加了句子末尾标识EOS_TOKEN，没有增加句子开始标识SOS_TOKEN？
            答：在seq2seq+注意力机制中，不管是Encoder编码器还是Decoder解码器，都必须明确要有句子末尾标识。
               而开始标识不是必须的，同时我们在后续模型训练的时候再加上开始标识SOS_TOKEN
        """
        # 3.1- 英语句子
        x = [english_word2index[word] for word in x.split(" ")] # 句子分词转成词索引
        x.append(EOS_TOKEN) # 列表最后增加句子末尾标识
        x = torch.tensor(x,dtype=torch.long,device=device)  # 变成张量

        # 3.2- 法语句子
        y = [french_word2index[word] for word in y.split(" ")]  # 句子分词转成词索引
        y.append(EOS_TOKEN)  # 列表最后增加句子末尾标识
        y = torch.tensor(y, dtype=torch.long, device=device)  # 变成张量

        return x,y

# 5- 创建Dataloader
def get_dataloader():
    # 1- 创建Dataset
    dataset = MyPairsDataset(sen_pairs)

    # 2- 创建Dataloader
    # 因为在自定义MyPairsDataset我们并没有对句子长度进行规范，因此这里的batch_size还是只能为1
    dataloader = DataLoader(dataset=dataset,batch_size=1,shuffle=True)

    """
        如果当前项目中，batch_size的值设置超过1，会报如下的错：
        RuntimeError: stack expects each tensor to be equal size, but got [10] at entry 0 and [6] at entry 1
    """
    # dataloader = DataLoader(dataset=dataset,batch_size=2,shuffle=True)

    return dataloader

# 6- 编码器：没有注意力
class Encoder(nn.Module):
    def __init__(self,vocab_size,input_size,hidden_size):
        # 1- 初始化父类
        super().__init__()

        # 2- 设置属性值
        self.vocab_size = vocab_size    # 英语词汇表中词的个数
        self.input_size = input_size    # 词向量维度
        self.hidden_size = hidden_size  # 隐藏状态向量维度

        # 3- 搭建神经网络结构
        # 3.1- 词嵌入层
        """
            参数解释：
                num_embeddings：词汇表中词的个数（去重后的）
                embedding_dim：词向量维度
        """
        self.ebd = nn.Embedding(num_embeddings=self.vocab_size, embedding_dim=self.input_size)

        # 3.2- 循环网络层。GRU
        """
            参数解释：
                input_size：本次输入词向量维度
                hidden_size：隐藏状态向量维度
                num_layers：隐藏层层数
                batch_first：是否将batch_size放在张量的第一个位置。注意：只会调整input和output的形状，不会改变hidden的张量形状
                    例如：[seq_len,batch_size,input_size] -> [batch_size,seq_len,input_size]
        """
        self.gru = nn.GRU(input_size=self.input_size,hidden_size=self.hidden_size,num_layers=1,batch_first=True)

    def forward(self, input, hidden):
        """
        前向传播。输入英语句子，让编码器理解句子的意思
        :param input: 本次输入数据，也就是单词的索引，张量形状：[batch_size,seq_len]
        :param hidden: 上一个时间步的隐藏状态，张量形状：[num_layers,batch_size,hidden_size]
        :return:
        """

        # 1- 词嵌入层：将词索引，变成词向量
        """
            输入参数input形状：[batch_size每个批次中有几个句子,seq_len每条句子中词的个数]
            结果参数embed形状：[batch_size每个批次中有几个句子,seq_len每条句子中词的个数,input_size词向量维度]
        """
        # print("0-input形状-->",input.shape)
        embed = self.ebd(input)
        # print("1-embed形状-->",embed.shape)

        # 2- GRU层
        """
            因为前面设置了batch_first为True，因此张量形状如下
                输入参数：
                    embed：[batch_size每个批次中有几个句子,seq_len每条句子中词的个数,input_size词向量维度]
                    hidden：[num_layers,batch_size,hidden_size]
                    
                返回结果：
                    output：[batch_size每个批次中有几个句子,seq_len每条句子中词的个数,hidden_size]
                    hidden：[num_layers,batch_size,hidden_size]
        """
        output,hidden = self.gru(embed,hidden)

        return output,hidden

    def init_hidden(self):
        # 隐藏状态张量形状：[num_layer,batch_size,hidden_size]
        return torch.zeros(size=(1,1,self.hidden_size), device=device)

# 7- 测试编码器
def use_encoder():
    # 1- 准备数据
    dataloader = get_dataloader()

    # 2- 创建编码器对象
    my_encoder = Encoder(vocab_size=english_word_n,input_size=256,hidden_size=256)
    # 将对象发送到对应的设备
    my_encoder = my_encoder.to(device)

    # 3- 遍历数据，进行前向传播
    for x,y in dataloader:
        # 3.1- 初始化隐藏状态
        hidden = my_encoder.init_hidden()

        # 3.2- 前向传播
        output,hidden = my_encoder(x,hidden)

        print(f"2-output形状-->{output.shape}") # 1,词的个数,256
        print(f"3-hidden形状-->{hidden.shape}") # 1,1,256

        break

# 8- 解码器：无注意力机制
class Decoder(nn.Module):
    def __init__(self,vocab_size,hidden_size):
        """
        :param vocab_size:  解码器中词汇表大小，也就是法语词的个数4345个
        :param hidden_size: 隐藏层隐藏状态的向量维度。
            因为是人为设置embedding_dim和hidden_size，因此该参数也表示词向量的维度。但是你可以设置的不一致
        """

        # 1- 初始化父类
        super().__init__()

        # 2- 设置属性值
        self.vocab_size = vocab_size
        self.hidden_size = hidden_size

        # 3- 搭建网络结构
        # 3.1- 词嵌入层
        self.embedding = nn.Embedding(num_embeddings=self.vocab_size,embedding_dim=self.hidden_size)

        # 3.2- 循环网络层：GRU
        self.gru = nn.GRU(input_size=self.hidden_size,hidden_size=self.hidden_size,num_layers=1,batch_first=True)

        # 3.3- 线性层
        self.out = nn.Linear(in_features=self.hidden_size,out_features=self.vocab_size)

        # 3.4- 输出的激活函数
        self.softmax = nn.LogSoftmax(dim=-1)

    def forward(self,input,hidden):
        """
        前向传播。翻译得到法语词
        :param input: 上一个时间步的预测法语词的词索引（也就是要将上个时间步预测结果作为本次输入使用），
            张量形状：[1每个批次中法语句子的条数,1每次只传递一个法语词]。数据内容举例：[[法语词索引]]
        :param hidden: 上一个时间步的隐藏状态，张量形状：[num_layers,batch_size,hidden_size]
        :return:
        """
        print(f"1-input形状-->{input.shape}")
        print(f"2-hidden形状-->{hidden.shape}")
        # 1- 调用词嵌入层：输入词索引，得到词向量
        ebd = torch.relu(self.embedding(input))
        print(f"3-ebd形状-->{ebd.shape}")

        # 2- 调用GRU
        """
            因为前面__init__中设置了batch_first为True，因此只会影响ebd和output的张量形状
            
            输入参数形状：
                ebd：    [batch_size,seq_len,hidden_size]
                hidden： [num_layers,batch_size,hidden_size]
                
            返回结果形状：
                output： [batch_size,seq_len,hidden_size]
                hidden： [num_layers,batch_size,hidden_size]
        """
        output,hidden = self.gru(ebd,hidden)
        print(f"4-output形状-->{output.shape}")

        # 3- 调用线性层和激活函数，得到预测的概率值
        # 3.1- 将output降维为二维：因为线性层只能处理二维数据
        """
            output[-1]针对：分类场景，也就是N vs 1的情况。取得是最后一个时间步的隐藏状态。一条句子彻底理解完以后才得到最终的预测结果
            output[0]针对：翻译、文本生成的场景，也就是N vs M的情况。取得是第一个时间步的隐藏状态。一次只生成一个词，并将该词作为下一步的输入
        """
        output = output[0]
        print(f"5-output[0]形状-->{output.shape}")

        # 3.2- 调用线性层
        output = self.softmax(self.out(output))

        # 4- 返回
        return output,hidden

    def init_hidden(self):
        return torch.zeros(size=(1,1,self.hidden_size), device=device)

# 9- 测试解码器
def use_decoder():
    # 1- 准备数据
    dataloader = get_dataloader()

    # 2- 创建编码器和解码器
    my_encoder = Encoder(vocab_size=english_word_n,input_size=256,hidden_size=256).to(device)
    my_decoder = Decoder(vocab_size=french_word_n,hidden_size=256).to(device)

    # 3- 遍历数据加载器进行翻译
    for x,y in dataloader:
        # x是英语句子，里面存放的是单词的索引。张量形状[batch_size,seq_len]
        # y是法语句子，里面存放的是单词的索引。张量形状[batch_size,seq_len]

        # 3.1- 先调用编码器
        h0 = my_encoder.init_hidden()
        output,hidden = my_encoder(x,h0)    # hidden就是中间语义张量C

        # 3.2- 再调用解码器。要一个一个法语词传递进解码器
        for i in range(y.shape[1]):
            # 3.3- 先提取法语词的索引，再将索引变成二维张量，最终形状是[[法语词索引]]
            french_word_index_2dtensor = y[0][i].reshape(1,-1)
            print(f"y={y}，y[0]={y[0]}，y[0][i]={y[0][i]}")

            # 3.4- 调用解码器
            output,hidden = my_decoder(french_word_index_2dtensor, hidden)

            print(f"output形状-->{output.shape}")

        break # 只跑一对英语句子和法语句子

# 10- 解码器：有注意力机制
class AttnDecoder(nn.Module):
    def __init__(self,vocab_size,hidden_size,dropout_p=0.1):
        # 1- 初始化父类
        super().__init__()

        # 2- 设置属性值
        self.vocab_size = vocab_size    # 法语词汇表大小
        self.hidden_size = hidden_size  # 代表如下几个信息：词向量维度、隐藏状态向量维度
        self.dropout_p = dropout_p      # 随机失活概率

        # 3- 搭建网络结构
        # 3.1- 词嵌入层
        self.embedding = nn.Embedding(num_embeddings=self.vocab_size,embedding_dim=self.hidden_size)
        # 词嵌入层后面的随机失活层
        self.dropout = nn.Dropout(p=self.dropout_p)

        # 3.2- 线性层：用来计算Q和K拼接后的相似性
        self.attn = nn.Linear(in_features=self.hidden_size+self.hidden_size,out_features=MAX_LENGTH)

        # 3.3- 线性层：拼接【上一个时间步翻译结果法语词的词向量】和【专属信息包】，然后将张量形状调整为GRU要求的形状
        self.attn_combine = nn.Linear(in_features=self.hidden_size+self.hidden_size,out_features=self.hidden_size)

        # 3.4- GRU循环网络层
        self.gru = nn.GRU(input_size=self.hidden_size,hidden_size=self.hidden_size,num_layers=1,batch_first=True)

        # 3.5- 线性层：输出层
        self.out = nn.Linear(in_features=self.hidden_size,out_features=self.vocab_size)

        # 3.6- 对数激活函数
        self.softmax = nn.LogSoftmax(dim=-1)

    def forward(self,input, key, value):
        """
        :param input: 上一个时间步的预测法语词的词索引。张量形状：[batch_size,seq_len]。seq_len后续我们是一个个法语词进行传递
        :param key: 上一个时间步的隐藏状态，也就是流程图中的prev_hidden。张量形状：[num_layers,batch_size,hidden_size]
        :param value: 编码器端多个词的隐藏状态拼接后的张量。张量形状：[batch_size,seq_len是固定值为MAX_LENGTH,hidden_size]
        :return:
        """
        # 1- input转成词向量，也就是得到Q
        # 方式一：分步骤的版本
        # ebd = self.embedding(input)
        # embedded = self.dropout(ebd)

        # 方式二：合并的版本
        Q = self.dropout(self.embedding(input))

        # 2- Q和K拼接；算相似性；相似性转成权重
        # 方式一：分步骤的版本
        qk_concat = torch.concat((Q,key),dim=-1)     # Q和K拼接
        qk_score = self.attn(qk_concat)                     # 算相似性
        attn_weights = torch.softmax(qk_score,dim=-1)       # 相似性转成权重

        # 方式二：合并的版本
        # attn_weights = torch.softmax(self.attn(torch.concat((Q,key),dim=-1)), dim=-1)

        # 3- 权重矩阵和value进行矩阵乘法运算，得到专属信息包
        attn_applied = torch.bmm(attn_weights,value)

        # 4- 【上一个时间步的预测法语词的词向量】和【专属信息包】拼接；调整拼接后的形状，达到GRU的输入要求
        qc_concat = torch.concat((Q,attn_applied),dim=-1)   # 【上一个时间步的预测法语词的词向量】和【专属信息包】拼接
        gru_input = torch.relu(self.attn_combine(qc_concat))

        # 5- 调用GRU
        output,hidden = self.gru(gru_input,key)

        # 6- 计算预测概率
        output = output[0]
        output = self.softmax(self.out(output))

        # 7- 返回结果
        return output,hidden,attn_weights

    def init_hidden(self):
        return torch.zeros(size=(1,1,self.hidden_size), device=device)

# 11- 测试解码器
def use_attn_decoder():
    # 1- 准备数据
    dataloader = get_dataloader()

    # 2- 创建编码器和解码器
    my_encoder = Encoder(vocab_size=english_word_n,input_size=256,hidden_size=256).to(device)
    my_decoder = AttnDecoder(vocab_size=french_word_n,hidden_size=256).to(device)

    # 3- 遍历数据加载器进行翻译
    for x,y in dataloader:
        # x是英语句子，里面存放的是单词的索引。张量形状[batch_size,seq_len]
        # y是法语句子，里面存放的是单词的索引。张量形状[batch_size,seq_len]

        # 3.1- 先调用编码器
        # 3.1.1- 解码器
        h0 = my_encoder.init_hidden()
        output,hidden = my_encoder(x,h0)    # hidden就是中间语义张量C

        # 3.1.2- 句子长度规范处理。统一到词个数为10的长度
        # 初始化一个[1,MAX_LENGTH,256]的全0张量
        value = torch.zeros(size=(1,MAX_LENGTH,256), device=device)
        # 计算需要复制的词个数
        copy_len = min(MAX_LENGTH, x.shape[1]) # x.shape[1]获取当前英语句子中词的个数
        # 将已有的数据复制到value中
        """
            为什么用output而不使用hidden来取隐藏状态信息数据？
            答：output记录的是最后一层，每个时间步的隐藏状态
               hidden记录的是每一层，最后一个时间步的隐藏状态
               但是value，是编码器端每个词的隐藏状态的拼接
        """
        value[:,:copy_len,:] = output[:,:copy_len,:]

        # 3.2- 再调用解码器。要一个一个法语词传递进解码器
        for i in range(y.shape[1]):
            # 3.3- 先提取法语词的索引，再将索引变成二维张量，最终形状是[[法语词索引]]
            french_word_index_2dtensor = y[0][i].reshape(1,-1)
            print(f"y={y}，y[0]={y[0]}，y[0][i]={y[0][i]}")

            # 3.4- 调用解码器
            output,hidden,attn_weights = my_decoder(french_word_index_2dtensor, hidden, value)

            print(f'解码output.shape: {output.shape}')  # [1, 4345]
            print(f'解码hidden.shape: {hidden.shape}')  # [1, 1, 256]
            print(f'解码att_weights: {attn_weights}')
            print(f'解码att_weights和: {attn_weights.sum()}')
            print(f'解码att_weights.shape: {attn_weights.shape}')  # [1, 1, 10]

            print("-"*30)
        break # 只跑一对英语句子和法语句子

# 12- 模型训练
# 12.1- 单次的训练过程
def train_iters(x,y,my_encoder,my_decoder,encoder_adam,decoder_adam,loss):
    """
        单对样本的训练代码
        :param x: 英语句子，特征数据，形状：[batch_size,seq_len]
        :param y: 法语句子，目标值，形状：[batch_size,seq_len]
        :param my_encoder: 编码器
        :param my_decoder: 解码器
        :param encoder_adam: 编码器优化器
        :param decoder_adam: 解码器优化
        :param loss: 损失函数对象
        :return: 单条样本数据的平均损失值
    """
    # 1- 调用编码器
    encoder_hidden = my_encoder.init_hidden()
    encoder_output,encoder_hidden = my_encoder(x,encoder_hidden)

    # 2- 句子长度规范
    # 2.1- 初始化全零的张量，形状[batch_size,MAX_LENGTH,hidden_size]
    value = torch.zeros(size=(1,MAX_LENGTH,256),device=device)
    # 2.2- 计算复制的长度
    copy_len = min(MAX_LENGTH, x.shape[1])
    # 2.3- 复制值
    value[:, :copy_len, :] = encoder_output[:, :copy_len, :]

    # 3- 参数设置
    # 3.1- 将 编码器最后一个时间步的隐藏状态作为解码器的初始隐藏状态使用
    decoder_hidden = encoder_hidden
    # 3.2- 在法语句子的前面增加SOS_TOKEN，标记翻译工作的开始
    input_y = torch.tensor([[SOS_TOKEN]], device=device)
    # 3.3- 初始损失值
    loss_value = torch.tensor(data=0.0,device=device)
    # 3.4- 获得当前法语句子中法语词的个数
    y_len = y.shape[1]
    # 3.5- teacher_forcing机制的使用标识
    teacher_forcing_flag = True if random.random()<0.5 else False

    # 4- 调用解码器
    if teacher_forcing_flag:
        # 4.1- 使用teacher_forcing机制

        for i in range(y_len):
            # 解码器的前向传播
            # 注意：输入进去和得到的隐藏状态的变量名称要完全相同，为了持续的将隐藏状态往下个时间步传递
            output,decoder_hidden,attn_weights = my_decoder(input_y, decoder_hidden, value)

            # 计算损失值
            y_true = y[0][i].reshape(1) # 法语真实值的词索引
            loss_value += loss(output,y_true)

            # 告知解码器下一个时间步真实的目标值是多少
            input_y = y[0][i].reshape(1,-1) # [[词索引]]
    else:
        # 4.2- 不使用。普通的训练过程
        for i in range(y_len):
            # 解码器的前向传播
            # 注意：输入进去和得到的隐藏状态的变量名称要完全相同，为了持续的将隐藏状态往下个时间步传递
            output, decoder_hidden, attn_weights = my_decoder(input_y, decoder_hidden, value)

            # 计算损失值
            y_true = y[0][i].reshape(1)  # 法语真实值的词索引
            loss_value += loss(output, y_true)

            # 将上一个时间步的预测结果（topi.detach()），作为【本次输出input_y】传递给到下一个时间步
            topv,topi = output.topk(1)  # 只取预测概率最高的那个词
            y_pred_index = topi.detach()
            if y_pred_index==EOS_TOKEN:
                # 说明到了句子的结尾。那么就结束翻译工作
                break
            input_y = y_pred_index

    # 5- 反向传播固定代码
    # 梯度清零
    encoder_adam.zero_grad()
    decoder_adam.zero_grad()

    # 反向传播
    loss_value.sum().backward()

    # 更新参数
    encoder_adam.step()
    decoder_adam.step()

    # 6- 返回平均损失
    return loss_value.item()/y_len

# 12.2- 整体训练流程
def train():
    # 1. 获取数据集加载器对象
    dataloader = get_dataloader()

    # 2. 模型初始化
    size = 256
    my_encoder = Encoder(vocab_size=english_word_n,input_size=size,hidden_size=size).to(device=device)
    my_decoder = AttnDecoder(vocab_size=french_word_n,hidden_size=size).to(device=device)

    # 3. 优化器初始化
    encoder_adam = torch.optim.Adam(params=my_encoder.parameters(),lr=1e-4)
    decoder_adam = torch.optim.Adam(params=my_decoder.parameters(),lr=1e-4)

    # 4. 损失函数初始化. 使用 NLLLoss() 负数对数似然损失
    loss = nn.NLLLoss()

    # 5. 训练参数初始化
    epochs = 1
    plot_avg_loss_list = [] # 平均损失值列表，用来画图

    # 6. 循环训练
    for epoch in range(epochs):
        # 6.1 外层循环, 控制训练轮数

        plot_total_loss = 0.0
        print_total_loss = 0.0

        # 6.2 内层循环, 遍历数据集的每个样本(即: 每轮具体的训练过程)
        for index,(x,y) in enumerate(tqdm(dataloader),start=1):
            """
                index：样本条数
                x：英语句子
                y：法语句子
            """
            # 具体训练过程看下面的步骤
            myloss = train_iters(x,y,my_encoder,my_decoder,encoder_adam,decoder_adam,loss)

            plot_total_loss += myloss
            print_total_loss += myloss

            # 6.3 记录损失用于绘图(每100个样本记录一次)
            if index%100==0:
                # 计算平均损失 = 总损失 / 100
                avg_loss = plot_total_loss/100
                # 记录指标
                plot_avg_loss_list.append(avg_loss)
                plot_total_loss = 0.0

            # 6.4 打印训练日志 (每1000个样本打印一次)
            if index%1000==0:
                # 计算平均损失 = 总损失 / 1000
                avg_loss = print_total_loss/1000
                print(f"第{epoch+1}轮次，已训练的样本条数{index}，平均损失是{avg_loss}")
                print_total_loss = 0.0

            # if index>10000:
            #     break

    # 7. 保存模型
    torch.save(my_encoder.state_dict(),"../model/encoder.pkl")
    torch.save(my_decoder.state_dict(),"../model/attn_decoder.pkl")

    # 8. 训练结束后, 绘制损失曲线
    plt.figure()
    plt.plot(plot_avg_loss_list)
    plt.show()

# 13- 模型预测
# 13.1- 单条英语句子的翻译
def seq2seq_evaluate(x,my_encoder,my_decoder):
    with torch.no_grad():
        # 1- 调用编码器：让模型理解英语句子的意思
        encoder_hidden = my_encoder.init_hidden()
        encoder_output,encoder_hidden = my_encoder(x,encoder_hidden)

        # 2- 句子长度规范
        # 2.1- 初始化全零的张量。形状：[1,MAX_LENGTH,256]
        value = torch.zeros(size=(1,MAX_LENGTH,256),device=device)
        # 2.2- 计算拷贝的长度
        copy_len = min(MAX_LENGTH, x.shape[1])
        # 2.3- 复制数据
        value[:, :copy_len, :] = encoder_output[:, :copy_len, :]

        # 3- 定义变量
        # 3.1- 解码器的初始隐藏状态：来自编码器端最后一个时间步的隐藏状态
        decoder_hidden = encoder_hidden
        # 3.2- 翻译工作的开始
        input_y = torch.tensor([[SOS_TOKEN]],device=device)
        # 3.3- 记录解码器端每个时间步对应的权重分布
        decoder_attention = torch.zeros(MAX_LENGTH, MAX_LENGTH)  # 形状[10个法语词,10个权重值]
        # 3.4- 翻译的法语词列表
        french_word_list = []

        # 4- 开始翻译
        for i in range(MAX_LENGTH):
            # 4.1- 调用解码器
            output,decoder_hidden,attn_weights = my_decoder(input_y,decoder_hidden,value)

            # 4.2- 记录权重的分布
            decoder_attention[i] = attn_weights

            # 4.3- 得到预测概率最高的那一个法语词
            # 4.3.1- 先得到最高词的词索引
            topv,topi = output.topk(1)
            french_word_index = topi.squeeze().item()
            print(f"topi-->{topi}-->{topi.squeeze()}-->{topi.squeeze().item()}")

            # 4.3.2- 判断是否到了句子的结束标识
            if french_word_index==EOS_TOKEN:
                french_word_list.append("EOS")
                break
            else:
                # 4.3.3- 词索引转成文字内容
                french_word_list.append(french_index2word[french_word_index])

            # 4.3.4- 本次的预测结果法语词索引作为下个时间步的输入数据使用
            input_y = torch.tensor([[french_word_index]],device=device)

        return french_word_list,decoder_attention

# 13.2- 多条英语句子的翻译
def use_seq2seq_evaluate():
    # 1- 准备数据
    # 因为以后是手动准备需要翻译的数据，因此不需要加载数据
    # get_dataloader()

    # 2- 加载训练好的模型
    size = 256
    my_encoder = Encoder(vocab_size=english_word_n,input_size=size,hidden_size=size).to(device=device)
    my_encoder.load_state_dict(torch.load("../model/encoder.pkl"))
    my_decoder = AttnDecoder(vocab_size=french_word_n,hidden_size=size).to(device=device)
    # weights_only：只从训练好的模型中加载权重信息，其他信息不加载。作用：加快模型的加载速度
    my_decoder.load_state_dict(torch.load("../model/attn_decoder.pkl",weights_only=True))

    # 3- 准备数据
    my_samplepairs = [
        ['i m impressed with your french .', 'je suis impressionne par votre francais .'],
        ['i m more than a friend .', 'je suis plus qu une amie .'],
        ['she is beautiful like her mother .', 'elle est belle comme sa mere .']
    ]

    # 4- 翻译
    for index,pair in enumerate(my_samplepairs):
        x = pair[0] # 英语句子
        y = pair[1] # 法语句子

        # 4.1- 分词并且转成向量
        x = [english_word2index[word] for word in x.split(" ")]
        x.append(EOS_TOKEN)
        x_tensor = torch.tensor(x, dtype=torch.long, device=device).reshape(1,-1)

        # 4.2- 调用预测代码
        french_word_list,decoder_attention = seq2seq_evaluate(x_tensor,my_encoder,my_decoder)

        # 4.3- 打印结果内容
        print(f"真实法语句子：{y}")
        print(f"预测法语句子：{' '.join(french_word_list)}")
        print(f"权重矩阵：{decoder_attention.shape}")
        print(f"权重矩阵：{decoder_attention.sum()}")
        print(f"权重矩阵：{decoder_attention}")

        # 4.4- 绘制权重矩阵
        plt.matshow(decoder_attention.numpy())
        plt.show()

        print("-"*30)

if __name__ == '__main__':
    # content = " i LOVE heima! "
    # content = " i LOVE hei@ma! "
    # print(f"-{normalize_string(content)}-")

    # getdata()

    dataloader = get_dataloader()
    for x,y in dataloader:
        print(f"英语句子-->{x.shape}-->{x}")
        print(f"法语句子-->{y.shape}-->{y}")

        break

    # print(english_word_n)
    # print(english_word2index)
    # print(english_index2word)

    # print(french_word_n)
    # print(french_word2index)
    # print(french_index2word)

    # get_dataloader()

    # use_encoder()

    # print(french_word_n)

    # use_decoder()

    # use_attn_decoder()

    # train()

    # use_seq2seq_evaluate()

