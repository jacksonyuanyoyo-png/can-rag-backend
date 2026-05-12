// @ts-nocheck
import { Divider, H1, H2, Pill, Row, Stack, Text, useHostTheme } from "cursor/canvas";

const architectureRows = [
  {
    label: "接入层",
    groups: [
      { title: "用户入口", items: ["Web / 管理后台", "Swagger API 调试", "业务系统调用"] },
      { title: "FastAPI 应用", items: ["Fidelity RAG Gateway", "API 路由层", "请求校验与编排"] },
      { title: "配置与运行", items: [".env 配置", "Settings", "Uvicorn 服务"] },
    ],
  },
  {
    label: "业务服务层",
    groups: [
      { title: "知识库服务", items: ["KnowledgeBaseService", "创建 / 删除知识库", "文档上传 / 删除"] },
      { title: "元数据管理", items: ["KnowledgeBaseRepository", "JSON 元数据", "backend_refs 映射"] },
      { title: "统一后端协议", items: ["KnowledgeBackend", "search", "chat"] },
    ],
  },
  {
    label: "RAG 后端层",
    groups: [
      { title: "OpenAI Backend", items: ["OpenAIClient", "主检索后端", "托管 File Search"] },
      { title: "Local Backend", items: ["TextChunker", "HashEmbedding", "JsonVectorStore"] },
      { title: "扩展后端", items: ["FastGPT 预留", "Hybrid 预留", "多后端切换"] },
    ],
  },
  {
    label: "OpenAI 托管能力",
    groups: [
      { title: "Files API", items: ["upload file", "list file", "retrieve content", "delete file"] },
      { title: "Vector Stores API", items: ["create vector store", "list / retrieve", "delete vector store"] },
      { title: "Vector Store Files", items: ["create store file", "list / retrieve", "delete store file"] },
    ],
  },
  {
    label: "数据与结果层",
    groups: [
      { title: "知识库数据", items: ["原始文档", "OpenAI file_id", "vector_store_id"] },
      { title: "检索上下文", items: ["matched chunks", "score", "citations"] },
      { title: "模型响应", items: ["answer", "引用来源", "可追溯结果"] },
    ],
  },
];

const flowSteps = [
  {
    title: "1. 用户提问",
    text: "用户在 Web、管理端或业务系统中输入问题，并选择目标知识库。",
  },
  {
    title: "2. FastAPI 接收请求",
    text: "RAG Gateway 校验请求参数，读取配置，进入知识库服务编排流程。",
  },
  {
    title: "3. 定位知识库",
    text: "KnowledgeBaseService 查询 JSON 元数据，拿到知识库对应的 vector_store_id 和文档映射。",
  },
  {
    title: "4. OpenAI 托管检索",
    text: "OpenAI Backend 调用 File Search / Vector Store 检索，获得相关文档片段、分数和引用信息。",
  },
  {
    title: "5. 组装上下文",
    text: "系统将命中的 chunks、citations 和用户问题组合为模型输入上下文。",
  },
  {
    title: "6. 模型生成答案",
    text: "OpenAI 模型基于检索上下文生成回答，并保留引用来源。",
  },
  {
    title: "7. 返回结果",
    text: "FastAPI 将 answer、citations、命中文档信息返回给用户或业务系统。",
  },
];

export default function FidelityRagLayeredAndFlow() {
  const theme = useHostTheme();

  const sectionStyle = {
    border: `1px dashed ${theme.stroke.primary}`,
    background: theme.fill.quaternary,
    borderRadius: 12,
    padding: 12,
  };

  const labelStyle = {
    width: 118,
    color: theme.text.secondary,
    fontWeight: 600,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  };

  const groupStyle = {
    border: `1px dashed ${theme.stroke.secondary}`,
    background: theme.bg.elevated,
    borderRadius: 8,
    padding: 8,
    flex: 1,
    minHeight: 102,
  };

  const itemStyle = {
    background: theme.accent.control,
    color: theme.text.onAccent,
    borderRadius: 4,
    padding: "7px 8px",
    textAlign: "center" as const,
    fontSize: 12,
  };

  const flowCardStyle = {
    border: `1px solid ${theme.stroke.primary}`,
    background: theme.bg.elevated,
    borderRadius: 10,
    padding: 12,
    minHeight: 112,
  };

  return (
    <Stack gap={20}>
      <Stack gap={6}>
        <Row gap={8} align="center">
          <H1>Fidelity RAG 系统架构与流程图</H1>
          <Pill tone="info">参考分层矩阵样式</Pill>
        </Row>
        <Text tone="secondary">
          该图按参考图片的横向分层方式组织：上层展示用户入口和 FastAPI 网关，中层展示知识库服务与后端适配，下层突出 OpenAI Files、Vector Stores 与托管 RAG 检索能力。
        </Text>
      </Stack>

      <Stack gap={10}>
        <H2>一、RAG POC 分层架构图</H2>
        {architectureRows.map((row) => (
          <div key={row.label} style={sectionStyle}>
            <div style={{ display: "flex", gap: 12 }}>
              <div style={labelStyle}>{row.label}</div>
              <div style={{ display: "flex", gap: 8, flex: 1 }}>
                {row.groups.map((group) => (
                  <div key={group.title} style={groupStyle}>
                    <Text size="small" weight="semibold">
                      {group.title}
                    </Text>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 8 }}>
                      {group.items.map((item) => (
                        <div key={item} style={itemStyle}>
                          {item}
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        ))}
      </Stack>

      <Divider />

      <Stack gap={10}>
        <H2>二、用户使用与模型响应流程图</H2>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 34px 1fr 34px 1fr 34px 1fr", gap: 8 }}>
          {flowSteps.slice(0, 4).map((step, index) => (
            <>
              <div key={step.title} style={flowCardStyle}>
                <Text weight="semibold">{step.title}</Text>
                <Text size="small" tone="secondary">
                  {step.text}
                </Text>
              </div>
              {index < 3 ? (
                <div style={{ color: theme.text.tertiary, fontSize: 24, alignSelf: "center", textAlign: "center" }}>→</div>
              ) : null}
            </>
          ))}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 34px 1fr 34px 1fr", gap: 8, marginLeft: 188, marginRight: 188 }}>
          {flowSteps.slice(4).map((step, index) => (
            <>
              <div key={step.title} style={flowCardStyle}>
                <Text weight="semibold">{step.title}</Text>
                <Text size="small" tone="secondary">
                  {step.text}
                </Text>
              </div>
              {index < 2 ? (
                <div style={{ color: theme.text.tertiary, fontSize: 24, alignSelf: "center", textAlign: "center" }}>→</div>
              ) : null}
            </>
          ))}
        </div>
      </Stack>

      <Divider />

      <Stack gap={8}>
        <H2>图中需要表达的重点</H2>
        <Text>OpenAI Vector Stores 是主向量库，OpenAI File Search / RAG 检索是主检索路径。</Text>
        <Text tone="secondary">
          FastAPI 主要承担网关、业务编排、元数据维护和后端适配；本地 RAG 管线是 POC/开发模式能力，可作为后续 hybrid 后端的一部分。
        </Text>
      </Stack>
    </Stack>
  );
}
