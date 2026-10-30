from flask import Flask,request,jsonify
from bert_predict_service import predict

# 1- 创建App应用
app = Flask(__name__)

# 2- 创建后端接口
@app.route(rule="/predict_api",methods=["POST"])
def predict_api():
    # 获得用户发送过来的请求
    news_data = request.get_json()

    # 调用预测函数
    result = predict(news_data)

    return jsonify(result)

if __name__ == '__main__':
    # 3- 启动接口
    app.run(host="127.0.0.1",port=8888,debug=True)