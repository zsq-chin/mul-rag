---
layout: default
title: 语析 - 基于大模型的知识库与知识图谱问答系统
nav_order: 1
description: "语析是一个强大的问答平台，结合了大模型 RAG 知识库与知识图谱技术，基于 Llamaindex + VueJS + FastAPI + Neo4j 构建。"
permalink: /
---

# 语析 - 基于大模型的知识库与知识图谱问答系统

## 项目概述

语析是一个强大的问答平台，结合了大模型 RAG 知识库与知识图谱技术，基于 Llamaindex + VueJS + FastAPI + Neo4j 构建。

### 核心特点

- 🤖 **多模型支持**：适配 OpenAI、各大国内主流大模型平台，以及本地 vllm 部署
- 📚 **灵活知识库**：支持 PDF、TXT、MD 等多种格式文档
- 🕸️ **知识图谱集成**：基于 Neo4j 的知识图谱问答能力
- 🚀 **简单配置**：只需配置对应服务平台的 `API_KEY` 即可使用

![系统界面预览](https://github.com/user-attachments/assets/75010511-4ac5-4924-8268-fea9a589839c)

## 快速开始

### 环境配置

在启动前，您需要提供 API 服务商的 API_KEY，并放置在 `src/.env` 文件中（此文件项目中没有，需要自行参考 [src/.env.template](src/.env.template) 创建）。

默认使用硅基流动的服务，因此**必须**配置：

```env
SILICONFLOW_API_KEY=sk-270ea********8bfa97.e3XOMd****Q1Sk
OPENAI_API_KEY=<API_KEY> # 如果需要配置 openai 则添加此行，并替换 API_KEY
DEEPSEEK_API_KEY=<API_KEY>  # 如果配置 DeepSeek 添加此行，并替换 API_KEY
ZHIPUAI_API_KEY=<API_KEY>  # 如果配置 智谱清言 添加此行，并替换 API_KEY
```

### 启动服务

**开发环境启动**（源代码修改会自动更新）：

```bash
docker compose -f docker/docker-compose.dev.yml --env-file src/.env up --build
```

> 添加 `-d` 参数可在后台运行

**生产环境部署**请使用：

```bash
docker compose -f docker/docker-compose.yml --env-file src/.env up --build -d
```

成功启动后，访问 [http://localhost:5173/](http://localhost:5173/) 即可使用系统。

## 功能特性

### 多模态问答支持

- Deepseek-R1 等推理模型
- 知识图谱检索
- 知识库检索
- 网页检索

### 知识库管理

支持多种格式的知识库文件：
- PDF
- Txt
- Markdown
- Docx

### 知识图谱集成

使用 Neo4j 作为知识图谱存储，支持：
- 自定义知识图谱导入
- 图谱可视化
- 图谱检索

## 模型支持

### 对话模型

| 模型供应商             | 默认模型                            | 配置项目                                       |
| :--------------------- | :---------------------------------- | :--------------------------------------------- |
| `siliconflow` (默认) | `Qwen/Qwen2.5-7B-Instruct` (免费) | `SILICONFLOW_API_KEY`                        |
| `openai`             | `gpt-4o`                          | `OPENAI_API_KEY`                             |
| `deepseek`           | `deepseek-chat`                   | `DEEPSEEK_API_KEY`                           |
| `arc`（豆包方舟）    | `doubao-1-5-pro-32k-250115`       | `ARK_API_KEY`                                |
| `zhipu`（智谱清言）  | `glm-4-flash`                     | `ZHIPUAI_API_KEY`                            |
| `dashscope`（阿里）  | `qwen-max-latest`                 | `DASHSCOPE_API_KEY`                          |

### 向量模型与重排序模型

建议使用硅基流动部署的 bge-m3（免费且无需修改）。其他模型配置参考 [src/static/models.yaml](src/static/models.yaml)。

## 更新日志

- **2025.02.24** - 新增网页检索以及内容展示，需配置 `TAVILY_API_KEY`
- **2025.02.23** - SiliconFlow 的 Rerank 和 Embedding model 支持，现默认使用 SiliconFlow
- **2025.02.20** - DeepSeek-R1 支持，需配置 `DEEPSEEK_API_KEY` 或 `SILICONFLOW_API_KEY`
- **2024.10.12** - 后端修改为 FastAPI，添加 Milvus-Standalone 独立部署

## 常见问题

### 镜像下载问题

如无法直接下载相关镜像，可参考 [DaoCloud/public-image-mirror](https://github.com/DaoCloud/public-image-mirror?tab=readme-ov-file#%E5%BF%AB%E9%80%9F%E5%BC%80%E5%A7%8B)，尝试替换前缀：

```bash
# 以 neo4j 为例，其余类似
docker pull m.daocloud.io/docker.io/library/neo4j:latest

# 然后重命名镜像
docker tag m.daocloud.io/docker.io/library/neo4j:latest neo4j:latest
```

## 贡献指南

欢迎提交 Issue 和 Pull Request 来帮助改进项目。

## 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件