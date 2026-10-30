
"""
    Transformer框架的结构：
        编码器端_输入部分
            词嵌入层
            位置编码

        编码器
            多头自注意力子层：多头自注意力 + 层归一化 + 残差连接
            前馈网络子层：前馈网络 + 层归一化 + 残差连接

        解码器端_输入部分
            词嵌入层
            位置编码

        解码器
            掩码多头自注意力子层：掩码多头自注意力 + 层归一化 + 残差连接
            多头注意力子层（交叉注意力）：多头注意力 + 层归一化 + 残差连接
            前馈网络子层：前馈网络 + 层归一化 + 残差连接

        输出部分
            线性层
            Softmax激活函数

        结果
"""
from demo04_decoder import *
from demo05_output import *

# 创建Transformer框架类
class MyTransformer(nn.Module):
    def __init__(self, en_embde_pos, encoder, de_embde_pos, decoder, output):
        """
        将前面开发的各个部分组织得到完整的Transformer框架
        :param en_embde_pos: 编码器端_输入部分 实例对象
        :param encoder: 编码器 实例对象
        :param de_embde_pos: 解码器端_输入部分 实例对象
        :param decoder: 解码器 实例对象
        :param output: 输出部分 实例对象
        """
        # 1- 初始化父类
        super().__init__()

        # 2- 设置属性值
        self.en_embde_pos = en_embde_pos
        self.encoder = encoder
        self.de_embde_pos = de_embde_pos
        self.decoder = decoder
        self.output = output

    def forward(self,en_input,de_input,mask):
        """
        Transformer前向传播
        :param en_input: 输入到编码器端的词索引数据
        :param de_input: 输入到解码器端的词索引数据
        :param mask: 解码器使用的掩码
        :return:
        """

        # 1- 编码器端_输入部分：词嵌入层、位置编码
        encoder_data = self.en_embde_pos(en_input)

        # 2- 编码器
        encoder_data = self.encoder(encoder_data)

        # 3- 解码器端_输入部分：词嵌入层、位置编码
        decoder_data = self.de_embde_pos(de_input)

        # 4- 解码器
        decoder_data = self.decoder(
            data=decoder_data,
            mask=mask,
            encoder_output=encoder_data
        )

        # 5- 输出部分
        return self.output(decoder_data)

# 调用Transformer框架
def get_mytransformer():
    # 常用变量
    d_model = 512
    dropout = 0.1
    de_vocab_size = 4345

    # ---------------- 编码器部分 ----------------
    # 词嵌入层
    en_ebd = Embedding(vocab_size=1000, d_model=d_model)
    # 位置编码
    en_pos = PositionalEncoding(d_model=d_model, dropout=dropout, max_len=60)
    # 多头自注意力
    en_multi_self_attn = MultiHeadAttention(d_model=d_model, head=8, dropout_p=dropout)
    # 前馈网络
    en_ff = FeedForward(d_model=d_model, output_dim=1024, dropout_p=dropout)
    # 组装得到编码器层
    encoder_layer = EncoderLayer(
        d_model=d_model,
        multi_head_self_attn=en_multi_self_attn,
        feed_forward=en_ff,
        dropout_p=dropout
    )
    # 得到编码器
    encoder = Encoder(encoder_layer=encoder_layer, N=6)

    # ---------------- 解码器部分 ----------------
    # 词嵌入层
    de_ebd = Embedding(vocab_size=de_vocab_size, d_model=d_model)
    # 位置编码
    de_pos = PositionalEncoding(d_model=d_model, dropout=dropout, max_len=60)
    # 掩码多头自注意力
    de_multi_self_attn = MultiHeadAttention(d_model=d_model, head=8, dropout_p=dropout)
    # 多头注意力
    de_multi_attn = MultiHeadAttention(d_model=d_model, head=8, dropout_p=dropout)
    # 前馈网络
    de_ff = FeedForward(d_model=d_model, output_dim=1024, dropout_p=dropout)
    # 组装得到编码器层
    decoder_layer = DecoderLayer(
        d_model=d_model,
        mask_multi_head_self_attn=de_multi_self_attn,
        multi_head_attn=de_multi_attn,
        feed_forward=de_ff,
        dropout_p=dropout
    )
    # 得到编码器
    decoder = Decoder(decoder_layer=decoder_layer, N=6)

    # ---------------- 输出部分 ----------------
    output = Output(d_model=d_model, vocab_size=de_vocab_size)

    # ---------------- 组装得到Transformer框架 ----------------
    my_transformer = MyTransformer(
        en_embde_pos=nn.Sequential(en_ebd, en_pos),  # 表示 词先经过词嵌入层的处理，然后再经过位置编码的处理。注意：位置不能乱
        encoder=encoder,
        de_embde_pos=nn.Sequential(de_ebd, de_pos),
        decoder=decoder,
        output=output
    )
    print(f"框架结构信息：{my_transformer}")
    return my_transformer

# 测试Transformer框架
def use_mytransformer():
    # 1- 获得我们自己的Transformer框架的实例对象
    my_transformer = get_mytransformer()

    # 2- 准备数据：以前面的英译法案例为例讲解
    # 2.1- 输入到编码器端的原始数据：也就是英语句子
    en_input = torch.tensor([
        [1,2,3,4],
        [5,6,7,8]
    ])

    # 2.2- 输入到解码器端的原始数据：也就是法语句子
    de_input = torch.tensor([
        [223,2344,456,356],
        [2456,131,456,67]
    ])

    # 2.3- 解码器的掩码
    mask = torch.triu(torch.ones(size=(8,4,4)),diagonal=0)

    # 3- 调用Transformer框架
    result = my_transformer(
        en_input,de_input,mask
    )

    print(f"最终输出结果的张量形状：{result.shape}")
    print(f"最终输出结果的张量求和结果：{result.sum()}")

if __name__ == '__main__':
    use_mytransformer()































