FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 安装Ollama
RUN curl -fsSL https://ollama.com/install.sh | sh

# 复制项目文件
COPY requirements.txt .
COPY config.yaml .
COPY data/ ./data/
COPY src/ ./src/
COPY static/ ./static/
COPY templates/ ./templates/
COPY main.py .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 创建必要的目录
RUN mkdir -p /app/chroma_db /app/logs

# 下载模型
RUN ollama pull qwen3:4b-instruct && \
    ollama pull nomic-embed-text

# 暴露端口
EXPOSE 8000 11434

# 设置启动命令
CMD ["sh", "-c", "ollama serve & python main.py --mode api --host 0.0.0.0 --port 8000"]