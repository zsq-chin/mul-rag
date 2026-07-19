# SAGE
![输入图片说明](https://foruda.gitee.com/images/1752989931350727347/5f881840_5610459.png "屏幕截图")
![输入图片说明](https://foruda.gitee.com/images/1752989834245009003/9eab384f_5610459.png "屏幕截图")
![输入图片说明](https://foruda.gitee.com/images/1752989812875448349/068c801b_5610459.png "屏幕截图")

#### 介绍
SAGE 是一个基于知识图谱的智能对话系统，结合了大型语言模型和结构化知识库，提供以下核心功能：
- 智能对话与问答
- 知识图谱构建与查询
- 多模型支持(LLM, Embedding, Rerank等)
- 前后端分离架构，易于扩展

#### 软件架构
系统采用前后端分离架构：
- 前端：Vue 3 + Vite + Pinia
- 后端：Python + FastAPI + SQLite/Neo4j
- AI模型：支持多种LLM和Embedding模型
- 部署：Docker容器化部署

主要模块：
- 知识库管理(src/core/)
- 智能代理(src/agents/)
- 模型管理(src/models/)
- Web界面(web/)

#### 参与贡献

1.  Fork 本仓库
2.  新建 Feat_xxx 分支
3.  提交代码
4.  新建 Pull Request


#### 项目特色

1. 多模型支持：集成多种大语言模型和Embedding模型
2. 知识图谱：支持结构化知识存储和复杂关系查询
3. 智能代理：基于ReAct框架的自主代理系统
4. 容器化部署：提供完整的Docker部署方案
5. 开发者友好：完善的API文档和测试套件

#### Gitee资源
1. Gitee官方博客 [blog.gitee.com](https://blog.gitee.com)
2. 优秀开源项目 [https://gitee.com/explore](https://gitee.com/explore)
3. GVP项目 [https://gitee.com/gvp](https://gitee.com/gvp)
4. 使用手册 [https://gitee.com/help](https://gitee.com/help)
