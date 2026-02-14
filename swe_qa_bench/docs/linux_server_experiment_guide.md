# Linux服务器上SWE-QA-Bench实验完整指南

本文档专门针对Linux服务器环境，详细说明如何设置和运行SWE-QA-Bench实验。

## 目录
1. [服务器环境要求](#服务器环境要求)
2. [环境准备和安装](#环境准备和安装)
3. [路径配置](#路径配置)
4. [Docker环境配置](#docker环境配置)
5. [API密钥配置](#api密钥配置)
6. [运行实验](#运行实验)
7. [监控和日志](#监控和日志)
8. [结果查看](#结果查看)
9. [故障排除](#故障排除)
10. [性能优化](#性能优化)

---

## 服务器环境要求

### 硬件要求
- **CPU**: 4核以上
- **内存**: 16GB以上（建议32GB）
- **存储**: 500GB以上可用空间
- **网络**: 稳定的互联网连接（用于API调用）

### 软件要求
- **操作系统**: Ubuntu 20.04/22.04 或 CentOS 7/8
- **Python**: 3.10 或 3.11
- **Docker**: 20.10+
- **Git**: 2.25+

---

## 环境准备和安装

### 1. 更新系统
```bash
sudo apt update && sudo apt upgrade -y  # Ubuntu/Debian
# 或
sudo yum update -y  # CentOS/RHEL
```

### 2. 安装基础依赖
```bash
# Ubuntu/Debian
sudo apt install -y python3 python3-pip python3-venv git curl wget htop jq

# CentOS/RHEL
sudo yum install -y python3 python3-pip git curl wget htop jq
```

### 3. 安装Docker
```bash
# Ubuntu/Debian
sudo apt install -y docker.io
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker $USER

# CentOS/RHEL
sudo yum install -y docker
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker $USER
```

**注意**: Docker安装后需要重新登录或运行 `newgrp docker` 使组权限生效。

### 4. 克隆项目代码
```bash
cd /home/$USER/workspace  # 或你喜欢的工作目录
git clone https://github.com/your-repo/locbench.git
cd locbench
git submodule update --init --recursive
```

---

## 路径配置

### 设置环境变量
在你的 `~/.bashrc` 或 `~/.profile` 中添加：

```bash
# SWE-QA-Bench 相关路径
export SWEQA_ROOT=/home/$USER/workspace/locbench/SWE-QA-Bench/SWE-QA-Bench
export SWEQA_DATASET=$SWEQA_ROOT/datasets
export SWEQA_REPOS=$SWEQA_DATASET/repos
export MINISWE_ROOT=/home/$USER/workspace/locbench/mini-swe-agent

# Tools模式需要的路径
export SWEQA_MODEL=$MINISWE_ROOT/swe_qa_bench/models/CodeRankEmbed
export SWEQA_INDEX=$MINISWE_ROOT/swe_qa_bench/indexes

# Python路径
export PYTHONPATH=$MINISWE_ROOT/src:$PYTHONPATH
```

应用环境变量：
```bash
source ~/.bashrc
```

### 验证路径配置
```bash
echo "SWEQA_ROOT: $SWEQA_ROOT"
echo "MINISWE_ROOT: $MINISWE_ROOT"
ls -la $SWEQA_DATASET/questions/ | head -5
ls -la $SWEQA_DATASET/repos/ | head -5
```
