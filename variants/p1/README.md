# P1 修正版：提交校验与证据审计分离

原 baseline 和原 OCR 融合代码不变。旧 P1 已备份至 `runs/p1_validation_fix/source_before/`。`runs/p1_full/`、`runs/ocr_fusion/` 的 4643 个文件经 SHA256 检查未变。

## 本次修正

- 提交格式是硬约束；模型生成的 evidence 是审计信息。缺失证据、数量不一、来源字段不完整、页码异常、值差异记入结果的 `evidence_warnings`，不直接将合法答案判为失败。证据警告不代表答案正确，仍需人工复核。
- 区分直接取值、计数、比较筛选、外观/语言判断。不再要求计数等派生答案与原始证据相等。计数、比较、外观题检索保留全文，避免缩小统计范围。
- 接受字符串数组形式的多级表头路径；数值证据比较兼容数字/数字字符串和括号负数。百分比不擅自与无百分号数字视为相等。
- 保留明确“期末与期初余额”的两项数量硬校验；顺序证据不明确记录 `order_unverified`，不把模型自己写的表头当真值。
- 数字答案去千分位；仅明确 number 格式将纯括号金额转换为负数。保留文本中的编号、日期、金额单位、百分比。number 格式收到百分号/单位或数组仍会报错，要求重新按题意作答，不能机械删符号、缩放或拆数组。
- 数组空项保留空字符串及原位置；多结果不得包装错误标量。空数组可表达无匹配结果，除非题目明确要求固定数量。
- 局部结构保留完整表逻辑尺寸、只输出指定单元格；合并格保留左上角与完整跨度。支持简单“前N行和第M列”等并集的越界校验；表头深度和真实表尺寸仍需视觉判断，不猜测。复杂范围表达不自动套用简单并集规则。
- 完整表请求检查覆盖完整性；局部词优先，避免“整个表格中的前1行”被误认为要求全表。网格恢复可直接输出标准 cells，减少局部题全网格构造负担；不自动补格、删冗余ID或把字符串 "null" 当合法空位。
- 合并新增 `--ids` 和 `--fill-blanks-only`。旧基准可含待修复的范围错误，但最终合并必须全部通过范围检查才能写入新文件。重复ID、ID集合、类型、格式及目标文件存在性仍严格检查。

## 离线检查结果

`runs/p1_validation_fix/replay/run_report.json`：仅回放原67道失败题的**最后一份保存响应**，55道通过、12道失败；API调用0。通过不代表内容正确。55道中第227、411题留有证据警告。

新范围检查额外发现原成功题32、51包含范围外单元格。没有生成最终合并提交；旧文件未修改。

建议补跑ID保存在 `runs/p1_validation_fix/rerun_ids.txt`：

```text
32,51,78,106,286,377,400,679,689,743,772,841,869,884
```

原有弃答241、414不在67道失败中，可另行排查。

## 使用

从原项目根目录执行，使用现有 tableocr 环境。修正版应使用新的输出目录，不要指向原 `runs/p1_full`。

```bash
/root/miniconda3/envs/tableocr/bin/python -m unittest discover -s variants/p1 -p 'test*.py'

/root/miniconda3/envs/tableocr/bin/python variants/p1/ocr_pipeline.py \
  --ids 32,51,78,106,286,377,400,679,689,743,772,841,869,884 \
  --output-dir runs/p1_fixed_rerun --recovery-attempts 3 --max-requests 100
```

上述补跑会调用模型。先合入补跑结果，修复原成功答案的范围问题：

```bash
/root/miniconda3/envs/tableocr/bin/python variants/p1/merge_submission.py \
  --base runs/p1_full/submission.xlsx \
  --report runs/p1_fixed_rerun/run_report.json \
  --ids 32,51,78,106,286,377,400,679,689,743,772,841,869,884 \
  --output runs/p1_fixed_rerun/after_rerun.xlsx
```

若32、51未成功修复范围，合并会拒绝写入，需先解决；不要删除最终范围检查。合并成功后再用55道离线恢复结果填空：

```bash
/root/miniconda3/envs/tableocr/bin/python variants/p1/merge_submission.py \
  --base runs/p1_fixed_rerun/after_rerun.xlsx \
  --report runs/p1_validation_fix/replay/run_report.json \
  --fill-blanks-only --output runs/p1_fixed_rerun/candidate_submission.xlsx
```

候选提交仅含 id、answer，不含证据。不会自动发布到根目录或提交平台。最终仍需复核内容并评测准确率。

离线回放工具可复用，输出目录必须不存在：

```bash
/root/miniconda3/envs/tableocr/bin/python variants/p1/revalidate.py \
  --run-dir runs/p1_full --output-dir runs/p1_replay_new
```
