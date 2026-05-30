-- 首次初始化数据目录时由 postgres 官方 entrypoint 执行。
-- 在默认库（POSTGRES_DB）中启用向量类型与索引能力。
CREATE EXTENSION IF NOT EXISTS vector;
