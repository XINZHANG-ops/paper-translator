import os
from dotenv import load_dotenv


def set_api_key():
    # 加载环境变量
    load_dotenv()

    # 检查是否已存在OPENAI_API_KEY环境变量
    api_key = os.getenv('OPENAI_API_KEY')
    if api_key:
        print("已存在OPENAI_API_KEY:", api_key)
        return

    # 从用户输入中获取API密钥
    api_key = input("请输入OpenAI API密钥：")

    # 将API密钥写入到.env文件中
    with open('.env', 'a') as env_file:
        env_file.write(f"OPENAI_API_KEY={api_key}\n")

    # 更新当前进程的环境变量
    os.environ['OPENAI_API_KEY'] = api_key


# 调用函数设置API密钥
if __name__ == "__main__":
    set_api_key()
