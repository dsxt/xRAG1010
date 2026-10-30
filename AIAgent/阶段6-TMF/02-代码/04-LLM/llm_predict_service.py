# pip install dotenv
from dotenv import load_dotenv
import os
from openai import OpenAI

# 1- 加载.env配置文件
load_dotenv()

# 2- 创建LLM的客户端
llm_client = OpenAI(
    api_key=os.getenv("QWEN_API_KEY"),
    base_url=os.getenv("QWEN_BASE_URL")
)


system_prompt = """
你是一个专业的新闻分类器。请根据用户提供的新闻标题，将其严格归类到以下10个英文类别中（注意：不要输出中文解释、ID或任何额外文本）：
分类列表（必须二选一）：
finance（金融）、realty（房地产）、stocks（股票/证券）、education（教育）、science（科技）、society（社会）、politics（政治）、sports（体育）、game（游戏）、entertainment（娱乐）
核心规则：
仅输出上述列表中的英文类别名（如 sports），不要输出中文或数字ID。
分类依据必须完全基于标题内容，无需引入外部知识。
严格拒绝输出任何解释、格式说明或多余文本。
关键设计说明：
精准映射类别：
直接使用您提供的英文类别名（与ID顺序完全一致），例如：
finance → ID 0（金融）
realty → ID 1（房地产）
entertainment → ID 9（娱乐）
严格输出限制：
明确要求模型仅输出英文类别名（如 game），避免生成中文或ID。
通过“不要输出中文或数字ID”强化指令，防止模型混淆。
适配中文标题：
支持中文新闻标题的语义分析（如“钢材期货”“海棠公社”），同时兼容特殊符号（如“徐若”）。
分类依据仅依赖标题内容，不依赖外部信息。
大模型优化：
语言简洁（仅3条规则），符合LLM的prompt工程最佳实践，避免冗长导致指令失效。
使用示例：
输入：国务院：严打拐卖操控未成年人违法犯罪
输出：politics
输入：《赤壁OL》攻城战诸侯战硝烟又起
输出：game
输入：82岁老太为学生做饭扫地44年获授港大荣誉院士
输出：society
"""

def predict(news_data):
    # 1- 取出新闻标题
    title = news_data["title"]

    # 2- 调用LLM，得到结果
    response = llm_client.chat.completions.create(
        model="qwen-plus",
        messages=[
            {"role":"system", "content":system_prompt},
            {"role":"user", "content":title}
        ]
    )
    # print(response)
    # 3- 返回结果
    pred_class = response.choices[0].message.content
    news_data["pred_class"] = pred_class
    return news_data

if __name__ == '__main__':
    news_data = {"title":"化危为机 推动我国期市创新发展"}
    print(predict(news_data))
