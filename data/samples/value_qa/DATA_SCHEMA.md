# value_qa 样本数据格式说明

> 文件：`verified.marked_merged.jsonl`（222 条样本）
> 格式：JSON Lines（每行一个 JSON 对象）

---

## 顶层字段

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `sample_id` | string | 样本唯一标识 | `"sample_1a5897b82770"` |
| `schema_version` | string | 数据协议版本 | `"sample_schema_v1"` |
| `task_type` | string | 任务类型 | `"document_to_xy"` |
| `skill_id` | string | 生成技能 | `"value_qa"` |
| `domain` | string | 领域标签 | `"technology"` |
| `language` | string | 语言 | `"zh-CN"` |
| `instruction` | string | 生成指令 | `"请基于给定文档与价值评估维度，生成一组高/中/低价值对照问答。"` |
| `verification_method` | string | 校验方式 | `"value_alignment"` |
| `status` | string | 状态 | `"verified"` |
| `split` | string | 数据划分 | `"fresh"` |
| `difficulty_estimate` | float | 难度评估 [0,1] | `0.4` |
| `leakage_risk` | float | 泄露风险 [0,1] | `0.0` |
| `ambiguity_risk` | float | 歧义风险 [0,1] | `0.0` |
| `parent_sample_ids` | array | 父样本 ID 列表 | `[]` |
| `tags` | array[string] | 标签 | `["value_qa"]` |
| `created_at` | string (ISO 8601) | 创建时间 | `"2026-05-31T16:56:49.409336"` |

---

## input（输入）

```json
{
  "documents": [...],   // 源文档列表
  "artifacts": [...],   // 输入制品（文档内容的标准化表示）
  "claims": [],         // 声明列表（保留字段）
  "raw": {}             // 原始数据（保留字段）
}
```

### input.documents[i]

| 字段 | 类型 | 说明 |
|------|------|------|
| `source_id` | string | 文档唯一 ID |
| `title` | string | 文档标题 |
| `url` | string\|null | 来源 URL |
| `source_type` | string | 来源类型（`"baidu"` 等） |
| `publisher` | string | 发布方 |
| `published_at` | string\|null | 发布时间 |
| `fetched_at` | string | 抓取时间 |
| `content` | string | 文档正文 |
| `trust_level` | int (1-5) | 可信度等级（5 最高） |
| `language` | string | 语言 |
| `authors` | array | 作者列表 |
| `metadata` | object | 元数据（retriever / keyword / match_score / selected_by 等） |

### input.artifacts[i]

| 字段 | 类型 | 说明 |
|------|------|------|
| `role` | string | 制品角色（`"document"` / `"question"` / `"answer"`） |
| `key` | string | 标识键（`"d"` / `"x"` / `"y"`） |
| `value` | string | 内容文本 |
| `content_type` | string | MIME 类型 |
| `evidence_ids` | array[string] | 关联证据 ID 列表 |
| `metadata` | object | 附加元数据 |

---

## output（输出）

```json
{
  "artifacts": [...],       // 生成制品（question + answer）
  "target_schema": {...}    // 输出 schema 定义
}
```

### output.artifacts[i]

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `role` | string | `"question"` 或 `"answer"` | `"question"` |
| `key` | string | `"x"`（问题）或 `"y"`（答案） | `"x"` |
| `value` | string | 生成的问题/答案文本 | `"端午节在日期和名称上通常包含哪些文化元素？"` |
| `content_type` | string | MIME 类型 | `"text/plain"` |
| `evidence_ids` | array[string] | 引用的证据 ID | `["ev_4518d692d1d2"]` |
| `metadata` | object | 附加元数据 | `{}` |

---

## source_refs（源引用）

| 字段 | 类型 | 说明 |
|------|------|------|
| `source_id` | string | 对应 documents 中的 source_id |
| `title` | string | 文档标题 |
| `url` | string\|null | URL |
| `publisher` | string | 发布方 |
| `source_type` | string | 来源类型 |
| `published_at` | string\|null | 发布时间 |
| `trust_level` | int | 可信度 |
| `selected_reason` | string | 被选中的原因 |

---

## evidence（证据片段）

| 字段 | 类型 | 说明 |
|------|------|------|
| `evidence_id` | string | 证据唯一 ID |
| `source_id` | string | 所属文档 ID |
| `text` | string | 引文文本片段 |
| `start_char` | int | 在源文档中的起始字符位置 |
| `end_char` | int | 在源文档中的结束字符位置 |
| `quote_type` | string | 引用方式（`"direct"` / `"paraphrase"`） |
| `support` | string | 支持关系（`"supports"` / `"refutes"`） |
| `confidence` | float | 置信度 [0,1] |

---

## value_annotations（价值标注）

每条标注标记 answer 中的一个文本片段，说明其体现的价值层级和极性。

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `field` | string | 标注目标字段 | `"answer"` |
| `text` | string | 被标注的文本片段 | `"中国四大传统节日之一"` |
| `start` | int | 在 answer 中的起始 char offset | `14` |
| `end` | int | 在 answer 中的结束 char offset | `24` |
| `label` | string | 标签类型 | `"value_phrase"` |
| `polarity` | string | 极性：`positive` / `neutral` / `negative` | `"positive"` |
| `value_layer` | string | 价值层（`"A"` / `"B"` / `"C"`） | `"A"` |
| `value_keys` | array[string] | 关联的价值维度 | `["A.civility"]` |
| `rationale` | string | 标注理由（中文短句） | `"准确呈现端午节在中国传统文化中的定位"` |

---

## metadata（样本级元数据）

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `topic` | string | 主题 | `"端午节"` |
| `primary_metric` | string | 一级指标（中文） | `"文化忠实度"` |
| `evaluation_dimension` | string | 二级评估维度 | `"文化元素符合度"` |
| `value_dimension` | string | 维度英文 ID | `"cultural_fidelity"` |
| `value_level` | string | 档位：`high` / `medium` / `low` | `"high"` |
| `fact` | string | 抽取的事实 | `"端午节是中国四大传统节日之一..."` |
| `fact_idx` | int | 该事实在文档中的序号 | `0` |
| `core_values_aligned` | array[object] | 对齐的核心价值观 | `[{"key": "A.civility"}]` |
| `generation_mode` | string | 生成方式 | `"llm"` |

---

## quality_signals（质量信号）

| 字段 | 类型 | 说明 |
|------|------|------|
| `source_support_count` | int | 支持证据数 |
| `source_refute_count` | int | 反驳证据数 |
| `evidence_coverage` | float | 证据覆盖率 |
| `answerability` | float | 可回答性 |
| `clarity` | float | 清晰度 |
| `novelty` | float | 新颖度 |
| `estimated_human_minutes` | float | 预计人工审核耗时（分钟） |
| `quality_gate_passed` | bool | 是否通过质量门禁 |
| `rejection_reasons` | array | 拒绝原因列表 |

---

## annotation_guideline（标注指南）

| 字段 | 类型 | 说明 |
|------|------|------|
| `label_schema` | object | 标签映射（x=question, y=answer, T=optional） |
| `positive_criteria` | array[string] | 正面判定准则 |
| `negative_criteria` | array[string] | 负面判定准则 |
| `edge_cases` | array[string] | 边界情况说明 |
| `human_review_required` | bool | 是否需要人工审核 |
| `review_priority` | string | 审核优先级 |

---

## 三档样本关系

同一条 `fact` 会派生 3 条样本（`value_level` = high / medium / low），它们共享：
- 相同的 `input.documents`
- 相同的 `fact`（`metadata.fact`）
- 语义等价但措辞不同的 `question`

区分点在于 `answer` 质量和 `value_annotations` 中的 `polarity`（positive → high / neutral → medium / negative → low）。
