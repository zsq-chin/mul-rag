# 前端构建警告登记（P2-3）

> 状态：**登记为后续工程/性能清理项，本轮不修改**。
> 依据 `CLAUDE_SECOND_REVIEW_FIX_INSTRUCTIONS.md` P2-3：
> 「前端构建成功，但仍有 `defineProps` 多余导入、缺少 `type: module` 和三个超大 chunk 警告。
> 它们不阻塞本轮合并，但应登记为后续性能/工程清理项，不要与 P1 混在一次提交中。」

以下三项均已在生产构建（`vite build`）中复现，构建本身成功（exit 0），仅产生警告。
登记目的是保留一份可追溯的清理清单，避免后续迭代遗忘。

---

## 1. `<script setup>` 中多余的 `defineProps` 导入

`defineProps` 是 `<script setup>` 的编译期宏，无需（也不应）从 `vue` 导入。冗余导入会被
构建工具忽略但属于死代码，且对阅读者造成误导。

| 文件 | 位置 | 说明 |
| --- | --- | --- |
| `web/src/components/LoadingComponent.vue` | L11 | `import { defineProps } from 'vue';` 整行冗余，可删除 |
| `web/src/views/ItemDataBaseInfoView.vue` | L384 | `import { h, defineProps } from 'vue';` — `h` 在渲染函数中使用，需保留；仅移除 `defineProps` |

清理方式（后续执行）：删除冗余宏导入后重新 `vite build` 确认无警告、测试 153/153 仍通过。

## 2. `web/package.json` 缺少 `"type": "module"`

`web/vite.config.js` 使用 ESM 语法，但 `web/package.json` 未声明 `"type": "module"`。
当前构建依赖 Vite 对配置文件的推断，属隐式行为。登记为工程项：统一显式声明
`"type": "module"`（或按项目约定选用 `.mjs`/`.cjs`），消除对工具推断的依赖。

## 3. 三个超大 chunk 超过 Vite 500 kB 默认告警线

`vite build` 输出（按构建产物大小，超过默认 `chunkSizeWarningLimit: 500 kB`）：

| chunk | 大小（约） | 备注 |
| --- | --- | --- |
| `index-C7Aft3DP.js` | 1682 kB | 入口主包 |
| `preset-Dk7T20JI.js` | 1119 kB | 预置逻辑 |
| `index-BEGLWBBM.js` | 1010 kB | 二级入口 |

后续优化方向（未在本轮实施）：
- 在 `vite.config.js` 中配置 `manualChunks` 拆分第三方依赖（如 ECharts/地图类库）；
- 对非首屏组件启用 `defineAsyncComponent` 懒加载；
- 视需要显式设置 `chunkSizeWarningLimit` 并评估分包收益。

---

## 记录口径

- 本轮（RAG-up）**未改动**上述前端源码来“消警告”，避免引入无关大重构。
- 若后续某一次提交实际处理了以上任意项，请在本文件对应条目上标记处理时间与提交哈希。
