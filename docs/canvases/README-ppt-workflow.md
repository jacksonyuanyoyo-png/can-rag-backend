# PPT / 演示用 Canvas 图

## 打开方式

在 Cursor 中打开 Canvas 文件（任选其一，内容相同）：

| 图 | 文件 |
|----|------|
| **三步业务流**（入库 → hit-test → SSE） | [can-rag-ppt-workflow.canvas.tsx](./can-rag-ppt-workflow.canvas.tsx) |
| **技术栈**（Next.js + LangChain/LangSmith + **AI API Gateway（LangChain 接模型）** + PG/pgvector） | [can-rag-tech-stack.canvas.tsx](./can-rag-tech-stack.canvas.tsx) |

IDE 托管目录（与上表同名）：`~/.cursor/projects/Users-jackson-Documents-project-Fidelity-CAN-RAG-BackEnd/canvases/`

在聊天侧栏点击该文件链接，即可在 IDE 中并排预览。

## 功能

- **三列流程**：Step I 入库 → Step II hit-test → Step III SSE 问答与 citations
- **流动连线**：SVG `stroke-dashoffset` 动画；系统开启「减少动态效果」时自动静止
- **分步高亮**：顶部按钮 `Step I / II / III / 全部`，便于演讲时与 PPT 左侧 bullet 同步
- **IDE 窗口样式**：模拟幻灯片右侧 `workflow.ts` 占位窗口，便于录屏

## 插入 PPT

1. 将 Canvas 面板宽度调至约 **520–560px**（与幻灯片右侧区域接近）
2. 使用 QuickTime / OBS 录 8–12 秒循环
3. 在 Keynote / PowerPoint 中 **插入 → 影片或 GIF**，叠在右侧窗口区域

## 与 Next.js 的关系

本图使用 **Cursor Canvas**（`cursor/canvas` SDK）实现，不依赖 Next.js 运行时。若需在 Next 演示站中复用，可参考 Canvas 内节点与连线路径，将坐标与 `path d` 抄入 React 组件；源码以本 `.canvas.tsx` 为准。

## 架构对照

| PPT 步骤 | 工程要点 |
|----------|----------|
| Step I | `presign` / `web-import` → `import-jobs` → `ImportJobWorker` → `RagPipeline.index_data` → `t_fact_kb_*` |
| Step II | `POST .../hit-test` → `search_data`（pgvector）→ 按 `data_id` 聚合 |
| Step III | `messages:stream` + `knowledgeBaseIds` → SSE `retrieval.*` / `message.delta` → citations JSON |

详见 [rag-architecture-guide.md](../rag-architecture-guide.md)。
