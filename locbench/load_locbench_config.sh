#!/bin/bash
# LocBench 配置加载脚本
# 用法: source load_locbench_config.sh

set -e

# 获取脚本所在目录的绝对路径
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/locbench_config.yaml"

# 检查配置文件是否存在
if [ ! -f "$CONFIG_FILE" ]; then
    echo "❌ Error: Config file not found: $CONFIG_FILE"
    return 1 2>/dev/null || exit 1
fi

echo "🔧 Loading LocBench configuration from $CONFIG_FILE"

# 使用Python解析YAML并输出环境变量设置命令
ENV_VARS=$(python3 -c "
import yaml
import os
import sys

try:
    with open('$CONFIG_FILE', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    if not config:
        print('echo \"❌ Error: Empty or invalid config file\"', file=sys.stderr)
        sys.exit(1)

    # 获取LocBench根目录
    config_dir = os.path.dirname('$CONFIG_FILE')
    locbench_root_rel = config.get('paths', {}).get('locbench_root', '.')
    locbench_root = os.path.abspath(os.path.join(config_dir, '..', '..', locbench_root_rel))

    # 输出环境变量设置命令
    print(f'export LOCBENCH_ROOT=\"{locbench_root}\"')
    mini_swe_root = os.path.normpath(os.path.join(locbench_root, config['paths']['mini_swe_root']))
    print(f'export MINISWE_ROOT=\"{mini_swe_root}\"')
    print(f'export LOCBENCH_DATASET=\"{os.path.normpath(os.path.join(locbench_root, config[\"paths\"][\"dataset\"]))}\"')
    print(f'export LOCBENCH_REPOS=\"{os.path.normpath(os.path.join(locbench_root, config[\"paths\"][\"repos_root\"]))}\"')

    # 设置代码搜索相关环境变量
    if 'code_search' in config:
        cs = config['code_search']
        print(f'export CODE_SEARCH_MODEL=\"{os.path.normpath(os.path.join(locbench_root, cs[\"model_path\"]))}\"')
        print(f'export CODE_SEARCH_INDEX=\"{os.path.normpath(os.path.join(locbench_root, cs[\"index_root\"]))}\"')
        print(f'export CODE_SEARCH_DEVICE=\"{cs.get(\"device\", \"cuda\")}\"')

    # 设置模型配置
    if 'model' in config:
        model_name = config['model'].get('name', 'chatanywhere/deepseek-v3.2')
        print(f'export MSWEA_MODEL_NAME=\"{model_name}\"')

    # 设置API配置
    print(f'export OPENAI_API_BASE=\"https://api.chatanywhere.tech/v1\"')
    print(f'export OPENAI_API_KEY=\"sk-RlDbbQ6OVAAdZi36Uxq4jHDwbK42zrg66krxiYJr6761Mqpg\"')

    # 设置运行配置
    if 'run' in config:
        run_config = config['run']
        print(f'export LOCBENCH_WORKERS=\"{run_config.get(\"workers\", 1)}\"')
        print(f'export LOCBENCH_REDO_EXISTING=\"{\"true\" if run_config.get(\"redo_existing\", True) else \"false\"}\"')

    # 设置实验配置
    if 'experiment' in config:
        exp_config = config['experiment']
        print(f'export LOCBENCH_SEED=\"{exp_config.get(\"seed\", 42)}\"')
        print(f'export LOCBENCH_TEST_SLICE=\"{exp_config.get(\"test_slice\", \"0:5\")}\"')

    # 设置环境配置
    if 'environment' in config:
        env_config = config['environment']
        if env_config.get('offline_mode', False):
            print(f'export TRANSFORMERS_OFFLINE=\"1\"')
            print(f'export HF_HOME=\"{os.path.normpath(os.path.join(locbench_root, \"mini-swe-agent/locbench/models\"))}\"')

except Exception as e:
    print(f'echo \"❌ Error loading config: {e}\"', file=sys.stderr)
    sys.exit(1)
")

# 执行环境变量设置
eval "$ENV_VARS"

# 检查关键环境变量是否设置成功（在Python中已设置，这里只是确认）
echo "🔍 Verifying environment variables..."

echo ""
echo "📍 Key paths:"
echo "  LocBench root: $LOCBENCH_ROOT"
echo "  Mini-SWE root: $MINISWE_ROOT"
echo "  Dataset: $LOCBENCH_DATASET"
echo "  Repos: $LOCBENCH_REPOS"
echo ""
echo "🚀 You can now run LocBench commands!"
echo "   Example: python \$MINISWE_ROOT/src/minisweagent/run/extra/locbench.py --dataset \$LOCBENCH_DATASET --repos-root \$LOCBENCH_REPOS --model \$MSWEA_MODEL_NAME"