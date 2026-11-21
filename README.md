# EvoBench 样例生成与评审工具

基于技能生成、校验、评分和修订基准样例，并通过Streamlit查看与标注结果。

发布单位：NGAI-LLMSafeContent。发布分支：`release/1.0`；更新日期：2026-11-21；自研代码许可：Apache-2.0，第三方保留原许可。

## 功能

- 文档到问题、答案、QA、步骤等技能及ValueQA三档样例生成
- 批量处理、质量检查、Prompt修订和CLI/API调用
- Viewer支持ValueQA、QC评估和Prompt演化视图

## 安装与快速开始

Python ≥3.10；Viewer另需Streamlit、pandas与兼容的PyArrow/NumPy组合。生成需要OpenAI兼容服务和合法语料。

从公开仓下载同一发布分支：

```bash
git clone --branch release/1.0 https://github.com/NGAI-LLMSafeContent-OpenSource/Project_1_Sample_evaluation_Generation-release.git
cd Project_1_Sample_evaluation_Generation-release
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell改用 .venv\Scripts\Activate.ps1
```

以下命令均以仓库根目录为起点，注释中的外部条件需先准备：

```bash
python -m pip install -e ".[dev]"
python -m pip install streamlit pandas pyarrow
cp .env.example .env
# 将.env中的模型、地址、凭据改为自己的配置
python -m benchmark.cli list-skills
python -m benchmark.cli generate-samples --skill-ids value_qa --topic "自选主题" --corpus-jsonl /path/to/corpus.jsonl --samples-jsonl /path/to/generated.jsonl
python -m streamlit run scripts/viewer/app.py --server.address 127.0.0.1 --server.port 8501
```

## 使用与输出

输入语料使用SourceDocument JSONL，包含source_id、title、content等字段。生成后在Viewer中填写实际JSONL路径，查看high/medium/low三联结果并标注、导出。离线演示样例位于 `data/samples/release_demo/value_qa.jsonl`，仅用于界面与格式验证。技能说明见 `benchmark/agents/skills/value_qa/README.md`。

## 配置与外部依赖

原有配置默认值保持原样，包括源仓中的服务地址；这些地址不保证对外可达。数据集、历史样例、QC结果和网页原文需自行准备，不以少量演示记录替换同路径的原数据文件。

真实凭据只在自己的运行环境或平台Secrets中配置，不提交.env、用户上传、模型权重或运行结果。原仓文件中的内网服务地址经发布负责人确认按原样保留，使用者应在本地配置自己的可达服务。

## 验证与已知问题

完成对应依赖准备后运行：

```bash
python tools/release_check.py
```

检查包含发布文件完整性、原仓文件一致性与声明的最小测试。它不会自动下载模型或申请API，也不代表全部真实业务/准确率验收。保留的原仓错误、未运行项及外部依赖见 [KNOWN_ISSUES.md](KNOWN_ISSUES.md)。

## 发布版本与文件清单

本版本以原开发仓主分支提交 `9826945027de990e8439ecf4ae5d05c962ad0572` 为源。功能实现与对应原文件保持一致；README、许可说明和发布辅助材料单独登记。当前功能的必要源码、配置、界面与运行资源保留，明确弃用的原型、历史结果和开发缓存不捆绑。

- [逐文件发布清单](RELEASE_FILES.md)
- [原仓文件与Git对象对应关系](SOURCE_ALIGNMENT.json)
- [完整文件SHA-256及权限](RELEASE_MANIFEST.json)，校验表为SHA256SUMS

校验值按Git存储字节生成；若原.gitattributes规定检出换行转换，请用Git对象或发布检查脚本校验，避免将LF/CRLF误认为源码变化。各公开平台release/1.0使用同一个提交，平台生成的运行分支不属于此发布清单。

## 反馈、贡献与许可证

请通过 [公开仓Issues](https://github.com/NGAI-LLMSafeContent-OpenSource/Project_1_Sample_evaluation_Generation-release/issues) 提交复现步骤、环境和日志摘要，勿附真实凭据或用户数据。代码问题由课题维护者在原开发仓处理，发布负责人同步审核后的版本；不要仅修一个平台造成分歧。

自研代码采用[Apache-2.0](LICENSE)。模型、数据集、外部服务和第三方代码遵循各自许可与使用条件，见[第三方说明](THIRD_PARTY_NOTICES.md)。
