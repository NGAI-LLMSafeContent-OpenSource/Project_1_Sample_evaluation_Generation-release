# EvoBench 价值样例生成与查看工具

发布方 NGAI-LLMSafeContent；版本 1.0；日期 2026-11-04；Apache-2.0。
源仓库 NGAI-LLMSafeContent/Project_1_Sample_evaluation_Generation，固定提交 9826945027de990e8439ecf4ae5d05c962ad0572。

公开样例生成框架与 ValueQA Viewer。依课题一 1-A1，从已批准的 ValueQA 文件仅提取一组 high/medium/low 三联样例及对应标记，原始来源正文已移除；不含历史全量数据、网页抓取正文、QC 材料、模型、日志和开发环境。

## 前置条件与使用

Python >=3.12、pip >=22.3，首次需联网下载 PyPI 依赖。建议使用新 venv，根目录执行：

```sh
python -m pip install -r requirements.txt
python -m benchmark.cli --help
python -m streamlit run scripts/viewer/app.py --server.address 127.0.0.1 --server.port 8501
```

打开 http://127.0.0.1:8501 ，选择 data/samples/value_qa/verified.marked_merged.jsonl。仅三条，不代表完整评测集或整体效果。可根据 Viewer 标记样例，标记与导出结果保留本地，勿提交用户数据。

## 生成与模型配置

默认 LLM_ENABLED=false，用于离线测试；完整 ValueQA 生成需自行准备有权使用的语料和模型服务。复制 .env.example 到本地 .env，设置 LLM_ENABLED=true、LLM_API_BASE、LLM_MODEL 与自己的 LLM_API_KEY，然后按 CLI 帮助使用 `generate-samples --skill-ids value_qa --topic <主题> --corpus-jsonl <自己的语料>`。没有随包交付或代为验证外部模型。样例生成与效果必须另行验收，不把离线测试结果当成模型推理成功。

根目录 `python tools/release_smoke.py` 创建隔离 venv，安装依赖，运行现有 CPU 测试、CLI 帮助与三联样例解析，启动真实 Streamlit HTTP 健康和首页检查。浏览器交互及实际模型生成未验证。

文件、源提交与校验见 RELEASE_MANIFEST.json、SHA256SUMS；许可范围见 THIRD_PARTY_NOTICES.md。
