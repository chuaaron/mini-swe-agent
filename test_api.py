#!/usr/bin/env python3
"""
Mini-SWE-Agent API 测试脚本
测试 chatanywhere API 是否正常工作
"""

import os
import sys
import json
from pathlib import Path
from platformdirs import user_config_dir

def load_config():
    """加载配置文件"""
    config_file = Path(user_config_dir('mini-swe-agent')) / '.env'
    config = {}

    if config_file.exists():
        print(f"📁 加载配置文件: {config_file}")
        with open(config_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        config[key.strip()] = value.strip()
    else:
        print(f"⚠️ 配置文件不存在: {config_file}")

    return config

def test_api_connection():
    """测试API连接"""
    print("\n🧪 开始API连接测试...")

    # 加载配置
    config = load_config()

    # 设置环境变量
    api_key = config.get('OPENAI_API_KEY') or os.getenv('OPENAI_API_KEY')
    api_base = config.get('OPENAI_API_BASE') or os.getenv('OPENAI_API_BASE', 'https://api.chatanywhere.tech/v1')
    model_name = config.get('MSWEA_MODEL_NAME') or os.getenv('MSWEA_MODEL_NAME', 'openai/gpt-3.5-turbo')

    if not api_key:
        print("❌ 未找到API Key。请设置 OPENAI_API_KEY 环境变量或在配置文件中设置。")
        return False

    print(f"🔑 API Key: {api_key[:10]}...")
    print(f"🌐 API Base: {api_base}")
    print(f"🤖 Model: {model_name}")

    try:
        import litellm

        # 测试1: 基本连接
        print("\n📡 测试1: 基本API连接...")
        response = litellm.completion(
            model=model_name,
            messages=[
                {"role": "user", "content": "Say 'API test successful' in exactly those words."}
            ],
            api_key=api_key,
            api_base=api_base,
            temperature=0.0,
            max_tokens=20
        )

        print("✅ 基本连接成功!")
        print(f"📝 响应: {response.choices[0].message.content}")

        # 测试2: 费用信息（如果可用）
        if hasattr(response, 'usage'):
            print(f"📊 Token使用: {response.usage.total_tokens}")

        # 测试3: 模拟mini-swe-agent的格式
        print("\n🤖 测试2: Mini-SWE-Agent格式测试...")
        swe_response = litellm.completion(
            model=model_name,
            messages=[
                {"role": "system", "content": "You are a helpful assistant that can interact with a computer. Your response must contain exactly ONE bash code block with ONE command. Include a THOUGHT section before your command. Format your response as: Your reasoning here.\n\n```bash\nyour_command_here\n```"},
                {"role": "user", "content": "Show me the current directory"}
            ],
            api_key=api_key,
            api_base=api_base,
            temperature=0.0,
            max_tokens=100
        )

        content = swe_response.choices[0].message.content
        print("✅ Mini-SWE-Agent格式测试成功!")
        print(f"📝 SWE响应:\n{content[:200]}...")

        # 检查是否包含bash代码块
        if "```bash" in content:
            print("✅ 响应包含bash代码块")
        else:
            print("⚠️ 响应不包含bash代码块")

        return True

    except litellm.AuthenticationError as e:
        print(f"❌ 认证失败: {e}")
        return False
    except litellm.APIError as e:
        print(f"❌ API错误: {e}")
        return False
    except Exception as e:
        print(f"❌ 未知错误: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_direct_http():
    """直接HTTP请求测试"""
    print("\n🌐 测试3: 直接HTTP请求...")

    config = load_config()
    api_key = config.get('OPENAI_API_KEY') or os.getenv('OPENAI_API_KEY')

    if not api_key:
        print("❌ 未找到API Key")
        return False

    try:
        import requests

        url = "https://api.chatanywhere.tech/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Hello, direct HTTP test!"}],
            "temperature": 0.0,
            "max_tokens": 20
        }

        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()

        result = response.json()
        print("✅ 直接HTTP请求成功!")
        print(f"📝 响应: {result['choices'][0]['message']['content']}")

        return True

    except requests.exceptions.RequestException as e:
        print(f"❌ HTTP请求失败: {e}")
        return False

def main():
    """主函数"""
    print("🚀 Mini-SWE-Agent API 测试工具")
    print("=" * 50)

    # 显示当前配置
    config = load_config()
    print("\n📋 当前配置:")
    for key in ['OPENAI_API_KEY', 'OPENAI_API_BASE', 'MSWEA_MODEL_NAME']:
        value = config.get(key) or os.getenv(key) or '未设置'
        if 'API_KEY' in key and value != '未设置':
            value = value[:10] + '...'
        print(f"  {key}: {value}")

    # 运行测试
    success1 = test_api_connection()
    success2 = test_direct_http()

    print("\n" + "=" * 50)
    if success1 or success2:
        print("✅ API测试完成！至少一项测试成功。")
        if success1:
            print("✅ LiteLLM测试通过")
        if success2:
            print("✅ 直接HTTP测试通过")
        print("\n🎉 你的API配置应该可以正常工作了！")
        print("现在可以运行: mini -v")
    else:
        print("❌ 所有测试都失败了。请检查你的API配置。")
        print("\n🔧 可能的解决方案:")
        print("1. 检查API Key是否正确")
        print("2. 检查网络连接")
        print("3. 检查API服务是否可用")
        print("4. 尝试更换API提供商")

if __name__ == "__main__":
    main()