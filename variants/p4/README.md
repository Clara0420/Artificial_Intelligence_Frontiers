# P4：高清裁剪与按题 OCR 融合

独立实现，保留原始版本和 P1/P2/P3。默认组合：完整表格裁剪、按题 OCR 关系证据、歧义时视觉定位、推理取证、风险触发的独立视觉复核与冲突裁决。这是待评测方案，不声称最优或超过 79.4；不等于复现同学的裁剪实现。

## 流程

1. 校验现有 PaddleOCR 缓存与源文档哈希。读取 JSON 中的表格 HTML 和周围说明；MD 全文用作回退。
2. 按题选择完整表格。结构、计算、比较及定位不确定时保留更广的范围；跨页数据不按 top-2 截断。输出一次关系 JSON，包含 page/table/cell、行列跨度、候选表头与行标签；未知单元格像素坐标为 null。
3. 关系序列化超过 36k 字符或无表时回退完整 OCR，绝不切掉部分求和行。全文仍受 --ocr-max-chars 限制。该限制不是 token 预算。
4. 从源 PDF/图像裁剪完整表格，默认 300 DPI、最长边 3200，保留上下边距。默认整页加裁剪，最多两个 OCR 裁剪；未裁出的表格仍可从整页读取。
5. 定位歧义或无可靠裁剪时，使用当前回答模型视觉定位（不是另行部署 Qwen2.5-VL），明确每页坐标尺寸、校验边界，再从源文档高清裁剪。最多六个视觉区域；定位失败保留原页及已有裁剪。旋转/EXIF 映射未经验证时安全回退，不强行猜坐标。
6. thinking 题先提取带页码、行列、单位的操作数，再复用 Decimal 计算。取证候选仍需视觉核验。
7. 风险包含跨页、定位歧义、HTML 异常、网格不完整、无裁剪、全文回退、输出证据警告、回答失败和参考提交分歧。先只给图像和题目独立回答；不展示 OCR 或候选答案。与初答不同，再展示候选及 OCR 做一次裁决。裁决需合法答案、resolved=true 和非空证据数组；未解决、解析失败或预算耗尽保留初答。模型证据仍不是人工真值。
8. 输出独立 submission.xlsx；根目录最佳提交不会自动覆盖。

## 离线检查（不调用 API）

从项目根目录执行：

```bash
/root/miniconda3/envs/tableocr/bin/python variants/p4/ocr_pipeline.py \
  --check-only --ids 26,42,56,241,588,679,689,769 \
  --output-dir runs/p4_check
```

省略 --ids 检查全部 908 题。check-only 验证 OCR 和确定性裁剪，不运行视觉定位/复核；后两者已有模拟调用测试。

## 运行完整组合

沿用现有百炼环境变量和依赖；无需安装或下载另一个模型。建议先小样本运行，再全量运行。每次不同实验使用新输出目录。

```bash
/root/miniconda3/envs/tableocr/bin/python variants/p4/ocr_pipeline.py \
  --ids 26,42,56,241,588,679,689,769 \
  --output-dir runs/p4_smoke --max-requests 100 --workers 2

/root/miniconda3/envs/tableocr/bin/python variants/p4/ocr_pipeline.py \
  --output-dir runs/p4_full --max-requests 2500 --workers 2 \
  --recovery-attempts 3
```

默认额外上限：120 道定位、160 道复核（每题最多独立读取+裁决两次），以及推理题取证。实际 API 重试也受全局 --max-requests 约束。阶段限额按本次运行进入阶段的题数计，包括命中阶段缓存；并发下到达顺序可能影响最后可获复核的题，严格对照可 workers=1 或提高上限。限额未用满不代表准确性已验证。

参考 79.4 提交（请使用实际路径）：

```bash
/root/miniconda3/envs/tableocr/bin/python variants/p4/ocr_pipeline.py \
  --reference-submission /absolute/path/submission_79_4.xlsx \
  --output-dir runs/p4_with_reference --max-requests 2500
```

参考答案仅用于判定分歧，**不会进入独立复核或裁决提示词**。结果写 reference_comparison.json；不同不等于错误。

## 消融与合并

- `--context-mode none`：不提供 OCR 文本/关系，但仍可用 Paddle 表格框裁剪。
- `--context-mode text`：完整表格行文本与说明，不提供关系字段。
- `--context-mode relations`：默认完整单元格关系；超限回退全文。
- `--no-localization`、`--no-review`、`--no-reasoning-evidence`：分别关闭额外阶段。
- 默认 `--visual-mode page_plus_tables` 最稳妥。`table_chunks` 是实验选项，可能遗漏未裁出的页面；用于完整比较时优先默认值。
- 所有消融必须固定题目、模型及其他开关。视觉定位会改变图像输入，需要关闭定位才能比较严格相同裁剪的 OCR 消融。
- `--merge-base` 和 `--merged-output` 必须同时提供，并要求 `--approved-ids 1,2,...` 指定人工核实可替换的题号。合并目标必须是新文件；不自动把所有分歧替换进 79.4 提交。

## 产物与缓存

- `question_contexts.json` / `retrieval_audit.json`：实际 OCR 上下文、选择范围、风险。
- `crop_audit.json` / `crops/`：OCR 框对应裁剪与跳过原因。
- `localization/` / `localization_audit/` / `visual_crops/`：定位原始响应与裁剪。
- `reasoning_evidence/`：推理操作数候选。
- `blind_review/` / `adjudication/`：独立视觉答案、冲突裁决原始响应。
- `records/`：主流程初答缓存；`final_records/` 和 `run_report.json`：包含复核后的最终答案。不要将初答缓存误当最终提交。
- `reference_comparison.json`：参考提交分歧及复核记录。

主流程缓存含代码上下文哈希、配置和图像身份；阶段缓存包含题目、配置、提示词、图像配置和裁剪哈希。`--refresh` 同时刷新额外阶段。全局 API 请求数/token 包含额外阶段；继承的 visual_input_audit 仅统计预处理初始图像，视觉定位新增图像见 localization_audit，不据该摘要推算实际费用。

验证：

```bash
/root/miniconda3/envs/tableocr/bin/python -m unittest discover -s variants/p4 -p 'test*.py'
```
