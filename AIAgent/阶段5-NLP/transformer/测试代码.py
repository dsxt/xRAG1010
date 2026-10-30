import torch
import numpy as np

# 使用PyTorch实现掩码。【掌握】
"""
    总结：
        1- 上三角掩码：右上角的值为1，左下角的值为0
        2- 下三角掩码：右上角的值为0，左下角的值为1
        3- 相同的规律：对角线的时候diagonal为0；如果是正数，并且越来越大，那么往右上角移动；反之往左下角移动；diagonal的取值超过范围不会报错
        4- 【掌握】掩码的作用：为了防止解码器端当前时间步偷看后面时间步的数据，你只能看到你前面时间步已经预测出来的数据。核心是防止模型训练效果不好
        5- 【掌握】代码：torch.triu(t,diagonal=0)
"""
def torch_mask():
    # 创建初始张量
    t = torch.ones(size=(5,5))
    print(f"原始张量：{t}")

    # 进行掩码操作
    # 上三角掩码
    u_0_mask = torch.triu(t,diagonal=0)
    print(f"上三角掩码0：{u_0_mask}")

    u_1_mask = torch.triu(t, diagonal=1)
    print(f"上三角掩码1：{u_1_mask}")

    u__1_mask = torch.triu(t, diagonal=-1)
    print(f"上三角掩码_1：{u__1_mask}")

    u_n_mask = torch.triu(t, diagonal=1000)
    print(f"上三角掩码n：{u_n_mask}")

    print("-"*30)

    # 下三角掩码
    l_0_mask = torch.tril(t, diagonal=0)
    print(f"下三角掩码0：{l_0_mask}")

    l_1_mask = torch.tril(t, diagonal=1)
    print(f"下三角掩码1：{l_1_mask}")

    l__1_mask = torch.tril(t, diagonal=-1)
    print(f"下三角掩码-1：{l__1_mask}")

def np_mask():
    # 1- 准备数据
    arr = np.ones(shape=(5,5))

    # 2- 上三角掩码
    # k和PyTorch中diagonal参数的作用完全一样
    u_result = np.triu(arr,k=0)
    print(f"上三角掩码0：\n{u_result}")

    u_result = np.triu(arr, k=1)
    print(f"上三角掩码1：\n{u_result}")

    u_result = np.triu(arr, k=-1)
    print(f"上三角掩码-1：\n{u_result}")

    # 3- 下三角掩码
    l_result = 1 - u_result
    print(f"下三角掩码：\n{l_result}")

    l_result = np.tril(arr,k=0)
    print(f"下三角掩码0：\n{l_result}")

    l_result = np.tril(arr, k=1)
    print(f"下三角掩码1：\n{l_result}")

    l_result = np.tril(arr, k=-1)
    print(f"下三角掩码-1：\n{l_result}")

def assert_demo(d_model,head):
    print("assert之前")
    assert d_model % head == 0
    print("assert之后")

def mask():
    mask = torch.ones(size=(1,2,4,5))
    scores = torch.ones(size=(2,2,4,5))
    scores = scores.masked_fill(mask == 0, value=-1e9)
    print(scores)

def k_multi_data():
    """
        return self.k * (data - mean)/(std+self.eps) + self.b
        演示 为什么 self.k * data 能够相乘？-> 广播机制
        k的形状是[512]，data形状[2,4,512]
    """

    k = torch.randint(low=1,high=5,size=(3,))
    data = torch.randint(low=1,high=5,size=(2,4,3))

    print(k)
    print(data)
    print(k*data)

if __name__ == '__main__':
    # torch版的掩码
    # torch_mask()

    # np_mask()

    # assert_demo(512,8)
    # assert_demo(512,7)

    # mask()

    k_multi_data()