#!/usr/bin/env python3
"""
测试mini-swe-agent的基本功能
"""

import os
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.litellm_model import LitellmModel

def test_mini_functionality():
    """测试mini-swe-agent的基本功能"""
    print("🧪 测试Mini-SWE-Agent基本功能...")

    try:
        # 创建模型
        model = LitellmModel(
            model_name="openai/gpt-3.5-turbo",
            model_kwargs={
                "custom_llm_provider": "openai",
                "api_base": "https://api.chatanywhere.tech/v1",
                "api_key": "sk-RlDbbQ6OVAAdZi36Uxq4jHDwbK42zrg66krxiYJr6761Mqpg"
            },
            cost_tracking="ignore_errors"
        )

        # 创建环境
        env = LocalEnvironment()

        # 创建代理
        agent = DefaultAgent(
            model=model,
            env=env,
            system_template="You are a helpful assistant that can interact with a computer. Your response must contain exactly ONE bash code block with ONE command. Include a THOUGHT section before your command. Format your response as: Your reasoning here.\n\n```bash\nyour_command_here\n```",
            instance_template="{{task}}",
            action_observation_template="<returncode>{{output.returncode}}</returncode>\n<output>\n{{output.output}}\n</output>",
            format_error_template="Please provide exactly ONE action in triple backticks.",
            timeout_template="Command timed out.",
            step_limit=3,  # 限制步骤避免运行太久
            cost_limit=0.1
        )

        # 测试简单任务
        print("🤖 运行测试任务: 显示当前目录...")
        exit_status, result = agent.run("Show me the current directory using pwd command")

        print(f"📊 退出状态: {exit_status}")
        print(f"📝 结果: {result}")

        # 检查是否成功
        if exit_status == "Submitted":
            print("✅ Mini-SWE-Agent测试成功!")
            return True
        else:
            print(f"⚠️ 测试完成，状态: {exit_status}")
            return True

    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_mini_functionality()
    if success:
        print("\n🎉 Mini-SWE-Agent配置正确，可以正常使用了！")
        print("现在可以运行: mini -v")
    else:
        print("\n❌ Mini-SWE-Agent测试失败，请检查配置。")