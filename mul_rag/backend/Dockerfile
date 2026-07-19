# 解决 paddlepaddle-gpu 不识别问题，改用 NVIDIA CUDA 镜像
FROM nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04

# 设置工作目录
WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/opt/conda/bin:${PATH}"

# === 1. 替换系统 apt 源并安装 Miniconda + 依赖 ===
RUN sed -i 's/archive.ubuntu.com/mirrors.aliyun.com/g' /etc/apt/sources.list && \
    sed -i 's/security.ubuntu.com/mirrors.aliyun.com/g' /etc/apt/sources.list && \
    apt-get update && apt-get install -y \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    poppler-utils \
    wget \
    bzip2 \
    && wget https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-latest-Linux-x86_64.sh -O miniconda.sh \
    && bash miniconda.sh -b -p /opt/conda \
    && rm miniconda.sh \
    && rm -rf /var/lib/apt/lists/*

# === 3. 配置 Conda 镜像源 ===
RUN echo "channels:" > /root/.condarc && \
    echo "  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/pytorch/" >> /root/.condarc && \
    echo "  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge/" >> /root/.condarc && \
    echo "  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main/" >> /root/.condarc && \
    echo "  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/free/" >> /root/.condarc && \
    echo "show_channel_urls: true" >> /root/.condarc && \
    echo "default_channels:" >> /root/.condarc && \
    echo "  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main" >> /root/.condarc && \
    echo "  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/r" >> /root/.condarc && \
    echo "custom_channels:" >> /root/.condarc && \
    echo "  conda-forge: https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud" >> /root/.condarc && \
    echo "  pytorch: https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud" >> /root/.condarc && \
    pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple/

# 4. 复制环境配置文件
COPY mul_rag_environment.yml .

# === 5. [核心修复] 全面解除版本冲突 ===
# 1. 移除 Conda 频道配置
RUN sed -i '/channels:/d' mul_rag_environment.yml && \
    sed -i '/- conda-forge/d' mul_rag_environment.yml && \
    sed -i '/- nvidia/d' mul_rag_environment.yml && \
    sed -i '/- defaults/d' mul_rag_environment.yml && \
    # 2. [关键] 强制 Numpy < 2.0.0 (解决 graspologic 冲突)
    sed -i 's/-\ numpy.*/-\ numpy<2.0.0/g' mul_rag_environment.yml && \
    sed -i 's/numpy==[0-9.]*/numpy<2.0.0/g' mul_rag_environment.yml && \
    # 3. [关键] 强制 OpenCV < 4.11 (配合旧版 Numpy)
    sed -i 's/opencv-python==[0-9.]*/opencv-python<4.11/g' mul_rag_environment.yml && \
    sed -i 's/opencv-contrib-python==[0-9.]*/opencv-contrib-python<4.11/g' mul_rag_environment.yml && \
    # 4. [新增] 解锁 unstructured-inference 以适应旧版 OpenCV (解决本次报错)
    sed -i 's/unstructured-inference==[0-9.]*/unstructured-inference/g' mul_rag_environment.yml && \
    sed -i 's/unstructured==[0-9.]*/unstructured/g' mul_rag_environment.yml && \
    # 5. 清理 Pandas 版本锁定 (解决构建字符串错误)
    sed -i 's/-\ pandas.*/-\ pandas/g' mul_rag_environment.yml && \
    sed -i 's/pandas==[0-9.]*/pandas/g' mul_rag_environment.yml && \
    # 6. 清理其他常见冲突包
    sed -i 's/grpcio==[0-9.]*/grpcio/g' mul_rag_environment.yml && \
    sed -i 's/grpcio-status==[0-9.]*/grpcio-status/g' mul_rag_environment.yml && \
    sed -i 's/google-cloud-vision==[0-9.]*/google-cloud-vision/g' mul_rag_environment.yml && \
    sed -i 's/litellm==[0-9.]*/litellm/g' mul_rag_environment.yml

# 6. 创建 Conda 环境 (强制使用镜像源加快 Pip 安装)
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple/
ENV PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn

RUN conda env create -f mul_rag_environment.yml

# 7. 设置环境变量
ENV PATH /opt/conda/envs/mul_rag/bin:$PATH

# 预先激活环境
RUN echo "source activate mul_rag" > ~/.bashrc

# 8. 复制源代码
COPY . .

# 9. 创建必要的存储目录
RUN mkdir -p knowledge_base tmp logs

# 10. 暴露端口
EXPOSE 8002

# 11. 启动命令
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8002"]