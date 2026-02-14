#!/bin/bash
# LocBench 运行脚本
# 用法: ./run_locbench.sh [method] [options]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 检查配置是否已加载
if [ -z "$LOCBENCH_ROOT" ] || [ -z "$MSWEA_MODEL_NAME" ]; then
    echo "⚠️  LocBench environment not loaded. Loading config..."
    source "$SCRIPT_DIR/load_locbench_config.sh"
fi

# 默认配置（在配置加载之后设置）
METHOD="${1:-bash-only}"
SLICE="${2:-$LOCBENCH_TEST_SLICE}"
WORKERS="${3:-$LOCBENCH_WORKERS}"
REDO="${4:-$LOCBENCH_REDO_EXISTING}"

# 切换到mini-swe-agent目录
cd "$MINISWE_ROOT"

echo "🚀 Running LocBench $METHOD method"
echo "   Slice: $SLICE"
echo "   Workers: $WORKERS"
echo "   Redo existing: $REDO"
echo ""

case "$METHOD" in
    "bash-only")
        echo "🔧 Running bash-only baseline..."
        python src/minisweagent/run/extra/locbench.py \
            --dataset "$LOCBENCH_DATASET" \
            --repos-root "$LOCBENCH_REPOS" \
            --config src/minisweagent/config/extra/locbench.yaml \
            --model "$MSWEA_MODEL_NAME" \
            --workers "$WORKERS" \
            --slice "$SLICE" \
            --redo-existing
        ;;

    "tools")
        echo "🔧 Running tools method (bash + code search)..."
        python src/minisweagent/run/extra/locbench_tools.py \
            --dataset "$LOCBENCH_DATASET" \
            --repos-root "$LOCBENCH_REPOS" \
            --config src/minisweagent/config/extra/locbench_tools.yaml \
            --model "$MSWEA_MODEL_NAME" \
            --workers "$WORKERS" \
            --slice "$SLICE" \
            --redo-existing
        ;;

    "ir-only")
        echo "🔧 Running IR-only method (pure retrieval)..."
        python src/minisweagent/run/extra/locbench_code_search.py \
            --dataset "$LOCBENCH_DATASET" \
            --repos-root "$LOCBENCH_REPOS" \
            --config src/minisweagent/config/extra/locbench.yaml \
            --model "$MSWEA_MODEL_NAME" \
            --slice "$SLICE" \
            --redo-existing
        ;;

    "test")
        echo "🧪 Running quick test (first 2 instances)..."
        SLICE="0:2"
        python src/minisweagent/run/extra/locbench.py \
            --dataset "$LOCBENCH_DATASET" \
            --repos-root "$LOCBENCH_REPOS" \
            --config src/minisweagent/config/extra/locbench.yaml \
            --model "$MSWEA_MODEL_NAME" \
            --workers 1 \
            --slice "$SLICE" \
            --redo-existing
        ;;

    "mock-test")
        echo "🤖 Running mock test with deterministic model..."
        # 临时修改模型为deterministic来测试LocBench流程
        export MSWEA_MODEL_NAME="deterministic"
        python src/minisweagent/run/extra/locbench.py \
            --dataset "$LOCBENCH_DATASET" \
            --repos-root "$LOCBENCH_REPOS" \
            --config src/minisweagent/config/extra/locbench.yaml \
            --model "$MSWEA_MODEL_NAME" \
            --workers 1 \
            --slice "$SLICE" \
            --redo-existing
        ;;

    "docker-build")
        echo "🐳 Building Docker image..."
        docker build -t locbench-minisweagent:latest -f locbench/Dockerfile .
        ;;

    "eval")
        echo "📊 Running evaluation..."
        if [ $# -lt 2 ]; then
            echo "❌ Usage: $0 eval <results_file.jsonl>"
            exit 1
        fi
        RESULTS_FILE="$2"
        python "$LOCBENCH_ROOT/evaluation/simple_eval.py" "$RESULTS_FILE" "$LOCBENCH_DATASET"
        ;;

    "help"|"-h"|"--help")
        echo "LocBench 运行脚本"
        echo ""
        echo "用法: $0 <method> [slice] [workers] [redo_existing]"
        echo ""
        echo "方法:"
        echo "  bash-only    运行bash-only基线测评 (默认)"
        echo "  tools        运行tools方法 (bash + code search)"
        echo "  ir-only      运行IR-only方法 (纯检索)"
        echo "  test         运行快速测试 (前2个实例)"
        echo "  mock-test    运行模拟测试 (使用deterministic模型)"
        echo "  docker-build 构建Docker镜像"
        echo "  eval         评估结果 (需要结果文件参数)"
        echo "  help         显示此帮助信息"
        echo ""
        echo "示例:"
        echo "  $0 bash-only 0:10    # 运行前10个实例"
        echo "  $0 tools 0:5 2       # 运行前5个实例，使用2个worker"
        echo "  $0 test               # 快速测试"
        echo "  $0 eval results.jsonl # 评估结果"
        exit 0
        ;;

    *)
        echo "❌ Unknown method: $METHOD"
        echo "Run '$0 help' for available methods"
        exit 1
        ;;
esac

echo ""
echo "✅ Task completed!"