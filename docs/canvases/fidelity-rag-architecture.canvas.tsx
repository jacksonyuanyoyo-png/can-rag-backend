// @ts-nocheck
import { Card, CardBody, Divider, Grid, H1, H2, Pill, Row, Stack, Stat, Text, useHostTheme } from "cursor/canvas";

const layers = [
  {
    title: "调用方",
    items: ["Web / Admin UI", "Swagger / API 调试", "业务系统"],
  },
  {
    title: "FastAPI 网关",
    items: ["路由层", "鉴权与配置", "请求编排"],
  },
  {
    title: "知识库用例层",
    items: ["KnowledgeBaseService", "知识库元数据", "文档生命周期"],
  },
  {
    title: "后端适配层",
    items: ["OpenAI Backend", "Local Backend", "FastGPT Backend", "Hybrid 预留"],
  },
  {
    title: "OpenAI 托管能力",
    items: ["Files API", "Vector Stores API", "File Search / RAG 检索", "Chat / Responses"],
  },
];

const managementSteps = [
  "create vector store",
  "list / retrieve / delete vector store",
  "upload / list / retrieve content / delete file",
  "create vector store file",
  "list / retrieve / delete vector store file",
];

const retrievalSteps = [
  "用户问题进入 FastAPI",
  "选择知识库与 vector_store_id",
  "调用 OpenAI File Search / RAG 检索",
  "返回命中文档片段与引用",
  "生成答案并返回 citations",
];

export default function FidelityRagArchitecture() {
  const theme = useHostTheme();

  const nodeStyle = {
    border: `1px solid ${theme.stroke.primary}`,
    background: theme.bg.elevated,
    color: theme.text.primary,
    borderRadius: 10,
    padding: 14,
    minHeight: 120,
  };

  const arrowStyle = {
    color: theme.text.tertiary,
    fontSize: 24,
    alignSelf: "center",
  };

  return (
    <Stack gap={18}>
      <Stack gap={6}>
        <Row gap={8} align="center">
          <H1>Fidelity RAG POC 架构图</H1>
          <Pill tone="info">FastAPI + OpenAI Vector Stores</Pill>
        </Row>
        <Text tone="secondary">
          当前项目是 RAG 网关骨架：FastAPI 负责接入与编排，OpenAI 托管 Files、Vector Stores 与检索能力，本地 RAG 管线作为开发与后续混合后端预留。
        </Text>
      </Stack>

      <Grid columns={4} gap={12}>
        <Stat value="FastAPI" label="应用入口" />
        <Stat value="OpenAI" label="主 RAG 后端" tone="info" />
        <Stat value="JSON" label="POC 元数据存储" />
        <Stat value="Local RAG" label="本地开发兜底" />
      </Grid>

      <Divider />

      <H2>高层模块视图</H2>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 36px 1fr 36px 1fr 36px 1fr 36px 1fr", gap: 8 }}>
        {layers.map((layer, index) => (
          <>
            <div style={nodeStyle}>
              <Text weight="semibold">{layer.title}</Text>
              <Stack gap={4}>
                {layer.items.map((item) => (
                  <Text key={item} size="small" tone="secondary">
                    {item}
                  </Text>
                ))}
              </Stack>
            </div>
            {index < layers.length - 1 ? <div style={arrowStyle}>→</div> : null}
          </>
        ))}
      </div>

      <Grid columns={2} gap={16}>
        <Card>
          <CardBody>
            <Stack gap={10}>
              <H2>知识库管理链路</H2>
              <Text tone="secondary">
                管理端通过 FastAPI 编排 OpenAI Files API 与 Vector Stores API，用于创建知识库、上传文件、挂载文件、查看状态和清理资源。
              </Text>
              <Stack gap={6}>
                {managementSteps.map((step, index) => (
                  <Row key={step} gap={8} align="center">
                    <Pill>{String(index + 1)}</Pill>
                    <Text size="small">{step}</Text>
                  </Row>
                ))}
              </Stack>
            </Stack>
          </CardBody>
        </Card>

        <Card>
          <CardBody>
            <Stack gap={10}>
              <H2>RAG 检索问答链路</H2>
              <Text tone="secondary">
                用户提问后，服务侧按知识库找到 OpenAI vector store，使用 OpenAI 托管检索获得相关上下文，再生成带引用的答案。
              </Text>
              <Stack gap={6}>
                {retrievalSteps.map((step, index) => (
                  <Row key={step} gap={8} align="center">
                    <Pill tone={index === 2 ? "info" : undefined}>{String(index + 1)}</Pill>
                    <Text size="small">{step}</Text>
                  </Row>
                ))}
              </Stack>
            </Stack>
          </CardBody>
        </Card>
      </Grid>

      <Divider />

      <Grid columns={2} gap={16}>
        <Stack gap={8}>
          <H2>当前代码中的已落地部分</H2>
          <Text>应用启动时创建 Settings、KnowledgeBaseRepository、RagPipeline 和 KnowledgeBaseService。</Text>
          <Text tone="secondary">OpenAIClient 已封装 create/list/retrieve/delete vector store、upload/list files、attach/list vector store files 等基础 REST 调用。</Text>
        </Stack>
        <Stack gap={8}>
          <H2>建议图中强调</H2>
          <Text>OpenAI 是主向量库与检索执行方；FastAPI 不自行实现生产级向量检索，只做业务编排、元数据维护与统一后端适配。</Text>
          <Text tone="secondary">Local RAG 只标为 POC/开发后端：chunk、hash embedding、JSON vector store、rerank、citation。</Text>
        </Stack>
      </Grid>
    </Stack>
  );
}
