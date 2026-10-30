"""
    1- 编码器 由 6层编码器层 组成
    2- 每个 编码器层由如下的结构组成：
        2.1- 第一个子层：多头自注意力子层
            多头自注意力 + 层归一化 + 残差连接
        2.2- 第二个子层：前馈网络子层
            前馈网络 + 层归一化 + 残差连接
"""

from demo02_component import *

# 编码器层
class EncoderLayer(nn.Module):
    def __init__(self,d_model,multi_head_self_attn,feed_forward,dropout_p=0.1):
        """
        定义编码器层的结构
        :param d_model: 词向量维度
        :param multi_head_self_attn: 多头自注意力类的实例对象
        :param feed_forward: 前馈网络类的实例对象
        :param dropout_p:
        """
        # 1- 初始化父类
        super().__init__()

        # 2- 设置属性值
        self.d_model = d_model
        self.dropout_p = dropout_p
        self.multi_head_self_attn = multi_head_self_attn
        self.feed_forward = feed_forward

        # 3- 创建子层
        # 3.1- 第一个子层：多头自注意力子层
        self.multi_layer = SubLayerConnection(self.d_model,self.dropout_p)

        # 3.2- 第二个子层：前馈网络子层
        self.feed_forward_layer = SubLayerConnection(self.d_model,self.dropout_p)

    def forward(self,data):
        # 1- 数据经过第一个层的处理：多头自注意力子层
        multi_output = self.multi_layer(data, lambda x: self.multi_head_self_attn(query=x, key=x, value=x, mask=None))
        # 2- 数据经过第二个层的处理：前馈网络子层
        encoder_output = self.feed_forward_layer(multi_output, lambda x: self.feed_forward(x))

        return encoder_output

# 编码器
class Encoder(nn.Module):
    def __init__(self, encoder_layer:EncoderLayer, N):
        """
        定义编码器结构。也就是将编码器层重复N层
        :param encoder_layer: 编码器层
        :param N:
        """
        # 1- 初始化父类
        super().__init__()

        # 2- 将编码器层创建N对象
        self.encoder_layer_list = clones(encoder_layer,N)

        # 3- 层归一化
        """
            这东西不是必须的，只是为了缓解经过N层处理后的数据，让数据变得更加平稳。这一层不是必须的
        """
        self.layer_norm = LayerNorm(encoder_layer.d_model)

    def forward(self,data):

        """
            注意：for循环中两个data需要与传递给forward的参数名称完全一致，否则6层网络处理的数据就会断开
        """
        for encoder_layer in self.encoder_layer_list:
            data = encoder_layer(data)

        return self.layer_norm(data)

# 测试编码器
# 测试最终的编码器
def use_encoder():
    # 1- 得到词向量数据：词向量 + 位置编码
    position_data = use_positional_encoding()

    d_model = 512
    dropout = 0.1

    # 2- 创建编码器的实例对象
    # 2.1- 多头自注意力子层的实例对象
    multi_head = MultiHeadAttention(d_model=d_model,head=8,dropout_p=dropout)
    # 2.2- 前馈网络子层的实例对象
    ff = FeedForward(d_model=d_model,output_dim=1024,dropout_p=dropout)
    # 2.3- 编码器层的实例对象
    encoder_layder = EncoderLayer(d_model=d_model,multi_head_self_attn=multi_head,feed_forward=ff,dropout_p=dropout)
    # 2.4- 编码器层 堆叠N层得到编码器
    encoder = Encoder(encoder_layder,6)

    # 3- 数据进行前向传播
    result = encoder(data=position_data)

    print(f"编码器最终的输出结果是：{result.shape}")
    return result

if __name__ == '__main__':
    use_encoder()



