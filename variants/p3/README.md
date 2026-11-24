# P3：P2 裁剪 + 表格关系 + 分阶段推理 + 风险复核

P3 建立在 P2 独立副本上。原始代码、P1、P2 和运行结果均保留。用户反馈 P2 总分73.7；P3 尚未运行评测，不能预估提分幅度。

## 三项实现

1. **高风险题复核**：默认对 P2 与原 OCR 融合答案不同的题建立复核池（本项目历史对照约144题，运行时重新读取报告并计算）；默认上限160。P3 初答与两个参考答案均一致时不追加调用。答案有分歧时，模型同时看原页、P2 高清裁剪、OCR和候选答案，重新核实后生成答案。复核答案先经赛题格式校验，错误时保留P3原答；每个题ID的复核响应保存到 `reviews/`，便于审计。它仍是模型复核，不能当人工真值。通过 `--review-source` 和 `--review-compare` 可指定两个参考运行；`--no-review` 关闭。
2. **单元格关系表示**：解析 PP-StructureV3 的完整表格 HTML，恢复候选逻辑网格、rowspan/colspan、0基坐标、多级列头路径及行标签候选；显式 `th/thead` 才标为HTML确定表头，否则标记启发式未核实。关系上下文明确声明是OCR候选，不是真值。默认整份OCR与P2原页、裁剪仍保留；不会用关系JSON截断表内容。每题关系审计保存到 `relation_audit.json`。上下文超过18k字符时，关系模块只保留候选表头和元数据，并明确提示回原文档读取全部数据行。
3. **推理题先取证、后计算**：对216道thinking题先调用一次模型，只定位操作数、行列来源、单位与统计范围；随后把结果作为待核对候选交给原推理流程。第二阶段仍核对原页和裁剪，并使用既有 Decimal 安全计算；证据阶段失败时继续原流程，不把失败变成空答案。阶段响应和解析JSON写入 `reasoning_evidence/` 并可缓存。`--no-reasoning-evidence` 关闭。

P3继承P2的 OCR、缺失/单位边界及局部结构投影。`--visual-mode` 允许在相同 OCR、关系候选和推理设置下比较三种图像输入：`page_only` 只传整页；`page_plus_tables` 同时传整页和高分辨率表格块；`table_chunks` 按相关表格分别传完整表格块，不拆开多级表头。每块上下保留较大页边距，并附带完整 OCR 文本（包含跨页续表文字）；无可靠裁剪时自动回退整页，避免零视觉证据。默认是 `page_plus_tables`。`--evidence-priority ocr_primary` 可改为先用 OCR 文本与表格关系定位、读取，再用原图/裁剪核验疑点；纯视觉问题（如版面方向、logo/二维码）会自动保持视觉优先。默认 `visual_primary` 保持既有提示，便于与旧实验对照。每个模式的 `run_report.json` 记录输入策略、实际 API 请求数/token，以及初次问题请求估算的页图/表格块数量和图像字节数；取证、修复、复核会重复部分图像，实际调用成本以 API token 统计为准。

P3默认新增成本上限约为216次取证请求加160次复核请求，外加主推理、修复调用。原始P2全量约956次API调用，P3全量调用更多。P3仍受基础 `--max-requests` 限制。

## 建议运行

先跑小样本检查关系输出和裁剪：

```bash
/root/miniconda3/envs/tableocr/bin/python variants/p3/ocr_pipeline.py \
  --check-only --ids 26,42,56,241,588,679,689,769 \
  --output-dir runs/p3_check
```

用同一题目范围分别运行以下三组，比较准确率/平台分数与 `run_report.json` 中的调用和 token。三组必须各用新目录；关闭参考答案复核，避免复核策略干扰图像模式对比。运行后可把 `--ids` 换成全量（不传该参数）。

```bash
PY=/root/miniconda3/envs/tableocr/bin/python
$PY variants/p3/ocr_pipeline.py --ids 26,42,56,241,588,679,689,769 --visual-mode page_only --no-review --output-dir runs/p3_ablation_page
$PY variants/p3/ocr_pipeline.py --ids 26,42,56,241,588,679,689,769 --visual-mode page_plus_tables --no-review --output-dir runs/p3_ablation_page_crop
$PY variants/p3/ocr_pipeline.py --ids 26,42,56,241,588,679,689,769 --visual-mode table_chunks --no-review --output-dir runs/p3_ablation_chunks
```

要测试“OCR主证据、原图辅助”，增加 `--evidence-priority ocr_primary`，并给输出使用新的目录。例如：

```bash
$PY variants/p3/ocr_pipeline.py --visual-mode page_plus_tables --evidence-priority ocr_primary --output-dir runs/p3_full_ocr_primary
```

完成小样本后再做独立全量实验：

```bash
/root/miniconda3/envs/tableocr/bin/python variants/p3/ocr_pipeline.py \
  --output-dir runs/p3_full \
  --recovery-attempts 3 --max-requests 3000
```

全量运行时建议保留默认对照文件：`runs/p2_full/run_report.json` 和 `runs/ocr_fusion/run_report.json`。若单独测视觉策略，应固定其他开关（尤其 `--no-review`）和题目集合。`page_only` 是整页基线；`page_plus_tables` 测裁剪作为补充；`table_chunks` 测以完整表格区域替代整页图像。需要对比分块模式与整页+裁剪模式时，查看 token 数/平台成绩；图像输入 token 受模型分辨率处理影响，不应仅用 JPEG 字节量估费用。

每次运行用新目录保留报告。不要把输出指向已有P2目录。P3的报告包含API调用数、token、复核题ID和每题阶段标记；裁剪图、关系候选、取证响应和复核响应各自保存。

## 实现状态与限制

- `relations.py` 生成的是候选结构；`th`/`thead`标记相对可靠，纯`td`表格的表头深度为启发式，仍必须视觉核实。
- 取证阶段也是模型输出，第二阶段必须核对，不会自动相信候选值。
- 复核模型可能受候选答案影响；只有P3与其他运行答案不一致才触发，所有被复核题都保存输入候选与复核响应。
- API调用与 token 会提高。用不同运行目录、关闭单项开关做对照，才能分别判断贡献。
- 当前只实现代码、离线产物与接口，不调用模型、不提交平台。
