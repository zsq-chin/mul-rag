# 问答统计前端页面集成指南

## 📋 已实现的功能

### 1. **热门问题排行榜（TOP 10）**
- ✅ 展示被问次数最多的10条问题
- ✅ 排名徽章（1-3位有特殊样式）
- ✅ 分类标签彩色展示
- ✅ 问题描述和元信息展示
- ✅ 提问次数、最后提问时间

### 2. **讨论区功能**
- ✅ 点击"讨论"按钮打开讨论抽屉
- ✅ 显示所有讨论评论
- ✅ 用户头像、名称、发布时间显示
- ✅ 评论输入和发布功能
- ✅ 讨论数量统计

### 3. **专家求助功能**
- ✅ 点击"求助"按钮打开求助模态框
- ✅ 求助表单包含：
  - 问题标题
  - 详细描述（支持计数）
  - 联系邮箱
- ✅ 表单验证
- ✅ 提交反馈

### 4. **统计卡片**
- ✅ 热门问题总数
- ✅ 总提问次数
- ✅ 讨论总数
- ✅ 求助统计

### 5. **UI/UX**
- ✅ 响应式设计（支持移动端）
- ✅ 卡片悬停效果
- ✅ 梯度背景
- ✅ 平滑动画转换

---

## 🔗 后端 API 集成说明

目前前端使用的是**模拟数据**。要连接真实的后端，需要实现以下 API 端点：

### 1. **获取热门问题列表**
```
GET /api/statistics/top-questions?limit=10
```

**响应格式：**
```json
{
  "status": "success",
  "data": [
    {
      "id": 1,
      "title": "问题标题",
      "count": 245,
      "category": "技术",
      "lastAsked": "2024-01-12",
      "discussionCount": 12,
      "helpCount": 3,
      "description": "问题描述"
    }
  ]
}
```

### 2. **获取问题的讨论列表**
```
GET /api/statistics/questions/{questionId}/discussions
```

**响应格式：**
```json
{
  "status": "success",
  "data": [
    {
      "id": 1,
      "author": "用户名",
      "avatar": "头像URL",
      "time": "2024-01-12 14:30",
      "content": "评论内容"
    }
  ]
}
```

### 3. **发布讨论评论**
```
POST /api/statistics/questions/{questionId}/discussions
Content-Type: application/json

{
  "content": "评论内容"
}
```

**响应格式：**
```json
{
  "status": "success",
  "message": "评论发布成功",
  "data": {
    "id": 1,
    "content": "评论内容",
    "time": "2024-01-12 14:30"
  }
}
```

### 4. **发布求助**
```
POST /api/statistics/help-requests
Content-Type: application/json

{
  "questionId": 1,
  "title": "求助标题",
  "description": "详细描述",
  "email": "user@example.com"
}
```

**响应格式：**
```json
{
  "status": "success",
  "message": "求助发布成功",
  "data": {
    "id": 1,
    "createdAt": "2024-01-12 14:30"
  }
}
```

---

## 📝 前端代码修改建议

### 修改 1：替换模拟数据为 API 调用

在 `AnswerStatistics.vue` 中，找到 `fetchTopQuestions` 函数，修改为：

```javascript
const fetchTopQuestions = async () => {
  loading.value = true
  try {
    const res = await api.get('/api/statistics/top-questions', {
      params: { limit: 10 }
    })
    if (res.data.status === 'success') {
      topQuestions.value = res.data.data
    }
  } catch (error) {
    message.error('获取数据失败: ' + error.message)
  } finally {
    loading.value = false
  }
}
```

### 修改 2：获取讨论列表

```javascript
const openDiscussion = async (question) => {
  selectedQuestion.value = question
  try {
    const res = await api.get(`/api/statistics/questions/${question.id}/discussions`)
    if (res.data.status === 'success') {
      discussionComments.value = res.data.data
    }
  } catch (error) {
    message.error('获取讨论列表失败')
  }
  showDiscussionDrawer.value = true
}
```

### 修改 3：提交评论

```javascript
const submitComment = async () => {
  if (!newComment.value.trim()) {
    message.warning('请输入评论内容')
    return
  }

  try {
    const res = await api.post(
      `/api/statistics/questions/${selectedQuestion.value.id}/discussions`,
      { content: newComment.value }
    )
    if (res.data.status === 'success') {
      message.success('评论发布成功')
      newComment.value = ''
      // 刷新讨论列表
      const res2 = await api.get(`/api/statistics/questions/${selectedQuestion.value.id}/discussions`)
      discussionComments.value = res2.data.data
    }
  } catch (error) {
    message.error('发布评论失败')
  }
}
```

### 修改 4：提交求助

```javascript
const submitHelp = async () => {
  if (!helpForm.title.trim() || !helpForm.description.trim() || !helpForm.email.trim()) {
    message.warning('请填写所有必填字段')
    return
  }

  try {
    const res = await api.post('/api/statistics/help-requests', {
      questionId: selectedQuestion.value.id,
      title: helpForm.title,
      description: helpForm.description,
      email: helpForm.email
    })
    if (res.data.status === 'success') {
      message.success('求助发布成功，等待专家回复')
      showHelpModal.value = false
      resetHelpForm()
    }
  } catch (error) {
    message.error('求助发布失败: ' + error.message)
  }
}
```

---

## 🎨 自定义样式

### 修改主题颜色

在 `AnswerStatistics.vue` 中，修改 LESS 变量：

```less
// 修改主色调
$primary-color: #1890ff;  // 改为你的品牌色

// 修改分类标签颜色
getCategoryColor(category) {
  const colors = {
    '技术': '#1890ff',
    '模型': '#52c41a',
    '知识库': '#faad14',
  }
  return colors[category] || '#666'
}
```

### 修改排名徽章样式

```less
&.top-three {
  background: linear-gradient(135deg, #ffd89b 0%, #19547b 100%);  // 修改渐变色
  color: white;
  box-shadow: 0 4px 12px rgba(25, 84, 123, 0.3);
}
```

---

## 🚀 部署检查清单

- [ ] 后端已实现上述 API 端点
- [ ] API 返回数据格式与文档一致
- [ ] 前端 API 调用已从模拟数据替换为真实调用
- [ ] 错误处理已完善
- [ ] 接口鉴权已配置（如需要）
- [ ] 跨域 CORS 已配置
- [ ] 已在多个浏览器/设备上测试

---

## 📞 常见问题

### Q: 如何添加搜索和筛选功能？
A: 在页面顶部添加搜索框和分类筛选器，修改 API 调用参数即可。

### Q: 如何实现分页？
A: 在 API 中添加 `page` 和 `pageSize` 参数，在前端使用 Ant Design 的 `a-pagination` 组件。

### Q: 如何添加排序功能？
A: 在 API 中添加 `sortBy` 和 `sortOrder` 参数，点击表头时动态更新。

### Q: 如何保存用户喜欢的问题？
A: 调用收藏 API，在每个问题卡片上添加收藏按钮。

---

## 📚 相关文件

- 主文件：`web/src/views/AnswerStatistics.vue`
- 路由配置：`web/src/router/index.js`
- 导航菜单：`web/src/layouts/AppLayout.vue`
- API 工具：`web/src/api/`（需要配置）

---

祝你实现顺利！🎉
