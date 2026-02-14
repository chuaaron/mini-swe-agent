# LocBench Mini-SWE-Agent 实验完整指南

## 实验环境总览

LocBench 是用于代码定位任务的基准测试集，支持三种不同的定位方法：

| 方法 | 依赖 | 特点 | 适用场景 |
|------|------|------|----------|
| **IR-only** | PyTorch + Transformers | 纯检索，速度快 | 检索上限测试 |
| **Bash-only** | Docker | 纯bash命令，基线 | 基础对比 |
| **Tools** | Docker + PyTorch | bash + 语义检索 | 综合性能测试 |

## 目录结构

```
/workspace/locbench/
├── data/Loc-Bench_V1_dataset.jsonl          # 数据集
├── repos/locbench_repos/                    # 代码仓库镜像
├── mini-swe-agent/                          # 主框架
│   ├── locbench/                            # LocBench特定配置
│   │   ├── models/CodeRankEmbed/            # 本地模型
│   │   ├── indexes/...                      # 预建索引
│   │   └── outputs/...                      # 输出结果
│   └── src/minisweagent/...                 # 源代码
├── evaluation/                              # 评估脚本
└── environment.yml                          # conda环境配置
```

## 环境准备

### 1. 系统要求

- **Python**: 3.11
- **GPU**: CUDA 12.8+ (推荐)
- **Docker**: 20.10+ (Bash-only/Tools方法必需)
- **磁盘空间**: 至少 50GB

### 2. Conda 环境创建

```bash
# 克隆或确保在项目目录
cd /workspace/locbench

# 创建conda环境
conda env create -f environment.yml

# 激活环境
conda activate locbench

# 安装mini-swe-agent
cd mini-swe-agent
pip install -e .
```

### 3. 安装额外依赖

```bash
# 安装PyTorch (GPU版本)
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128

# 安装其他依赖
pip install transformers einops
```

### 4. 设置环境变量

```bash
export LOCBENCH_ROOT=/workspace/locbench
export MINISWE_ROOT=$LOCBENCH_ROOT/mini-swe-agent
export LOCBENCH_DATASET=$LOCBENCH_ROOT/data/Loc-Bench_V1_dataset.jsonl
export LOCBENCH_REPOS=$LOCBENCH_ROOT/repos/locbench_repos

# 离线模式设置 (可选)
export TRANSFORMERS_OFFLINE=1
export HF_HUB_OFFLINE=1
```

### 5. Docker 环境准备 (Bash-only/Tools方法)

```bash
# 安装Docker
sudo apt update
sudo apt install -y docker.io
sudo systemctl start docker
sudo systemctl enable docker

# 添加用户到docker组
sudo usermod -aG docker $USER
newgrp docker

# 构建LocBench镜像
cd $MINISWE_ROOT
docker build -t locbench-minisweagent:latest -f locbench/Dockerfile .
```

## 方法一：IR-only (推荐起始方法)

### 配置验证

确保配置文件正确：

```yaml
# $MINISWE_ROOT/src/minisweagent/config/extra/code_search.yaml
chunker: sliding
chunk_size: 800
overlap: 200
embedding_provider: local
embedding_model: /workspace/locbench/mini-swe-agent/locbench/models/CodeRankEmbed
embedding_batch_size: 64
embedding_max_length: 4096
embedding_device: cuda
trust_remote_code: true
index_root: /workspace/locbench/mini-swe-agent/locbench/indexes/llamaindex_code_custom_40_15_800/dense_index_llamaindex_code
max_file_size: 524288
```

### 运行命令

```bash
cd $MINISWE_ROOT

# 单条测试
mini-extra locbench-code-search \
  --dataset $LOCBENCH_DATASET \
  --repos-root $LOCBENCH_REPOS \
  --slice 0:1 \
  --redo-existing

# 小批量测试 (5条)
mini-extra locbench-code-search \
  --dataset $LOCBENCH_DATASET \
  --repos-root $LOCBENCH_REPOS \
  --slice 0:5 \
  --redo-existing

# 对比实验 (20条，固定种子)
SEED=123
mini-extra locbench-code-search \
  --dataset $LOCBENCH_DATASET \
  --repos-root $LOCBENCH_REPOS \
  --shuffle --shuffle-seed $SEED --slice 0:20 \
  --redo-existing

# 全量运行
mini-extra locbench-code-search \
  --dataset $LOCBENCH_DATASET \
  --repos-root $LOCBENCH_REPOS \
  --redo-existing
```

## 方法二：Bash-only

### 运行命令

```bash
cd $MINISWE_ROOT

# 单条测试
mini-extra locbench \
  --dataset $LOCBENCH_DATASET \
  --repos-root $LOCBENCH_REPOS \
  --slice 0:1 \
  --workers 1 \
  --redo-existing

# 对比实验 (与IR-only使用相同样本)
SEED=123
mini-extra locbench \
  --dataset $LOCBENCH_DATASET \
  --repos-root $LOCBENCH_REPOS \
  --shuffle --shuffle-seed $SEED --slice 0:20 \
  --workers 2 \
  --redo-existing
```

## 方法三：Tools (Bash + Code Search)

### 运行命令

```bash
cd $MINISWE_ROOT

# 单条测试
mini-extra locbench-tools \
  --dataset $LOCBENCH_DATASET \
  --repos-root $LOCBENCH_REPOS \
  --slice 0:1 \
  --workers 1 \
  --redo-existing

# 对比实验
SEED=123
mini-extra locbench-tools \
  --dataset $LOCBENCH_DATASET \
  --repos-root $LOCBENCH_REPOS \
  --shuffle --shuffle-seed $SEED --slice 0:20 \
  --workers 2 \
  --redo-existing
```

## 输出和评估

### 输出路径

```
# IR-only结果
$MINISWE_ROOT/locbench/loc_output/code_search/local/[model]/loc_outputs_[timestamp].jsonl

# Bash-only/Tools结果
$MINISWE_ROOT/locbench/loc_output/[model]/loc_outputs_[timestamp].jsonl

# 轨迹文件
$MINISWE_ROOT/locbench/outputs/[model]/[timestamp]/[instance_id]/[instance_id].traj.json

# 日志文件
$MINISWE_ROOT/locbench/outputs/[model]/[timestamp]/minisweagent.log
```

### 结果评估

```bash
# 基本评估 (如果有评估脚本)
python $LOCBENCH_ROOT/evaluation/simple_eval.py \
  [loc_outputs.jsonl] \
  $LOCBENCH_DATASET

# 查看结果摘要
python -c "
import json
with open('[loc_outputs.jsonl]', 'r') as f:
    data = json.load(f)
    print(f'Instance: {data[\"instance_id\"]}')
    print(f'Found files: {len(data[\"found_files\"])}')
    print(f'Found entities: {len(data[\"found_entities\"])}')
"
```

## 实验流程建议

### 阶段1：环境验证
1. ✅ 创建conda环境
2. ✅ 安装PyTorch和依赖
3. ✅ 测试IR-only单条样本

### 阶段2：小规模测试
1. ✅ IR-only 5条样本
2. ✅ Bash-only 5条样本 (如果Docker可用)
3. ✅ Tools方法 5条样本 (如果Docker可用)

### 阶段3：对比实验
1. ✅ 使用相同随机种子运行三种方法
2. ✅ 比较性能和准确性
3. ✅ 分析结果差异

### 阶段4：大规模测试
1. 全量数据集运行
2. 多轮重复实验
3. 统计分析和报告

## 常见问题

### Docker相关
```bash
# 检查Docker状态
docker --version
docker ps

# 清理空间
docker system prune -a

# 权限问题
sudo usermod -aG docker $USER
```

### GPU相关
```bash
# 检查GPU
nvidia-smi
python -c "import torch; print(torch.cuda.is_available())"
```

### 内存/磁盘问题
```bash
# 检查空间
df -h
du -sh /workspace/locbench/

# 清理缓存
conda clean --all
pip cache purge
```

### 网络问题
```bash
# 如果需要代理
export HTTP_PROXY=http://proxy:port
export HTTPS_PROXY=http://proxy:port
```

## 性能优化

### IR-only优化
- 调整 `embedding_batch_size` (16-128)
- 使用GPU: `embedding_device: cuda`
- 调整 `chunk_size` 和 `overlap`

### Bash-only优化
- 调整 `workers` 数量
- 使用 `--redo-existing` 避免重复

### 并行运行
```bash
# 多进程运行
mini-extra locbench-code-search \
  --dataset $LOCBENCH_DATASET \
  --repos-root $LOCBENCH_REPOS \
  --workers 4 \
  --slice 0:100
```

## 实验记录

建议记录每次实验的配置：

```bash
# 创建实验记录
echo "实验配置:
方法: IR-only
样本: 0:20
种子: 123
时间: $(date)
GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader)
PyTorch: $(python -c 'import torch; print(torch.__version__)')
" > experiment_log_$(date +%Y%m%d_%H%M%S).txt
```

---

## 快速开始检查清单

- [ ] Conda环境已创建并激活: `conda activate locbench`
- [ ] PyTorch已安装并支持CUDA: `python -c "import torch; print(torch.cuda.is_available())"`
- [ ] 环境变量已设置: `echo $LOCBENCH_DATASET`
- [ ] IR-only方法可运行: 单条测试成功
- [ ] Docker可用 (可选): `docker --version`
- [ ] 数据集和仓库完整: `ls $LOCBENCH_DATASET $LOCBENCH_REPOS`

完成以上检查后，你就可以开始LocBench实验了！🎯