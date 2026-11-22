# P2：宽松提交解析 + 完整 OCR + 高清局部图

这是独立版本，不修改 `variants/p1`、原始 `tableqa` 包或旧实验。用户反馈的 P1 平台成绩为69.6；目前没有逐题平台反馈，不能仅凭总分确认下降完全由校验或检索导致。

## 默认变化

1. **保留整份 OCR**。不再默认用前两张候选表替换文档上下文。排序只用于选取补充局部图；全部原页仍输入模型。
2. **简化证据提示**。不要求生成 evidence，没有该字段不触发失败。保留已有证据的审计能力；启发式期末/期初数量检查只记警告，不淘汰答案。JSON、数字、数组及结构合法性仍硬校验。
3. **适度兼容**。单值题收到只含一个标量的数组可以解包，多项数组绝不截取第一项；数组题可直接返回裸 JSON 数组。保留空项、日期、百分比和单位，不机械删百分号或金额单位。
4. **局部范围投影**。仅对可明确解析的简单“前N行/第M列”并集移除范围外单元格。保留完整 row_count/col_count、原始行列坐标及合并跨度，记录 scope_removed_cells。表头深度、交集、复杂范围不猜测；网格缺格/非矩形/重复ID不伪修复。
5. **P2 图像增强**。每题默认最多两张完整候选表局部图，表头与数据行一起保留。PDF 按裁剪范围直接以300 DPI重新渲染，最长边3200、像素上限576万；位图从原始像素裁剪，不把插值放大称为新增细节。保留周边边距，完整脚注/标题仍可从原页与全文OCR查阅。
6. **可靠映射与方向**。只使用可唯一对应HTML的表块坐标，核对OCR尺寸、预处理设置及原文档宽高比。坐标/旋转不明确时跳过该裁剪。PDF原生文字方向有至少5行且80%一致时，可旋转补充图到正常阅读方向；不修改原始页。
7. **附图不成为新页**。每张裁剪标明真实源页、表ID和旋转角度，主请求与恢复请求均带完整原页及附图。缓存指纹含附图哈希与元数据；P2拒绝写入旧P1实验目录。

## 运行

在原项目根目录执行。新增入口：`variants/p2/ocr_pipeline.py`。调用模型的命令会产生API费用；本次实现只做离线检查。

全量 P2：

```bash
/root/miniconda3/envs/tableocr/bin/python variants/p2/ocr_pipeline.py \
  --output-dir runs/p2_full \
  --recovery-attempts 3 --max-requests 3000
```

为了区分放宽规则和裁剪本身的效果，可在相同题目/配置上做无裁剪对照：

```bash
/root/miniconda3/envs/tableocr/bin/python variants/p2/ocr_pipeline.py \
  --no-crops --output-dir runs/p2_relaxed_full \
  --recovery-attempts 3 --max-requests 3000
```

两者均默认完整OCR。裁剪会增加图像输入量，但不额外发起定位模型请求。不要仅按非空率判定优劣；按相同ID对比内容与平台成绩。重复运行同一个P2目录会复用成功缓存；保留各轮结果时使用新目录。

小样本检查/推理可以加 `--ids 26,51,241,689,699,869`。子集submission中其余题为空，需使用合并工具写到新文件。`--context-mode ranked` 可作为单独消融，恢复P1的候选表文本筛选；不建议与其他变量同时修改。

零API全量准备（会生成真实裁剪图）：

```bash
/root/miniconda3/envs/tableocr/bin/python variants/p2/ocr_pipeline.py \
  --check-only --output-dir runs/p2_check_full
```

其他参数：`--max-crops 0..4`（默认2）、`--crop-dpi`（默认300，上限600）、`--crop-max-side`（默认3200，上限4096）、`--crop-max-pixels`（默认5760000）。API图像max_pixels仍由基础`--max-pixels`控制；有效清晰度受模型图像处理和原始分辨率共同限制。`--p2-help`查看P2参数，`--help`查看基础推理参数。

## 已验证

- 22项离线测试通过：原始像素坐标、PDF高清渲染、像素限制、旋转、恢复附图、完整runner路由、裁剪缓存指纹、单值兼容、局部范围及安全合并。
- 908题全量离线准备通过：全部保留完整OCR；741题获得至少一张局部图，共896次附图引用，114张唯一裁剪，其中8张按可靠的原生文字方向旋转。
- 26次候选裁剪因无法唯一对应坐标或PDF旋转元数据而跳过；跳过不阻断原图推理。其他无裁剪题包括外观题、无候选表或匹配较弱的题。
- 目视检查第26题账龄表及第689题横置合金表的裁剪；未根据观察结果写入答案。
- 六题无裁剪模式检查通过；指向 `runs/p1_full` 时正确拒绝写入。
- P1源码21个文件与复制前清单一致。未调用模型、未生成新的推理答案、未承诺平台提分。

产物在 `runs/p2_check_full/`：`crop_audit.json`记录每题附图/跳过原因，`retrieval_audit.json`记录定位分数，`question_contexts.json`保存实际文字上下文，`p2_config.json`保存配置和代码摘要，`crops/`保存图片。

运行测试：

```bash
/root/miniconda3/envs/tableocr/bin/python -m unittest discover -s variants/p2 -p 'test*.py'
```
