# 新脚本与交接文档说明

这个文件夹用于单独交接本轮新增的 OCR、商品识别脚本和说明文档。完整项目根目录仍然是上一级 `shop-workspace`，虚拟环境、模型权重、相机/xArm 支撑代码也仍然放在上一级目录中。

## 文件清单

- `live_yolo.py`：Gemini 336 实时画面中只框出置信度最高的已配置商品。
- `receipt_ocr.py`：调用智谱 `glm-ocr`，从小票图片或相机当前帧识别文字。
- `HANDOFF_OCR_YOLO_ZH.md`：OCR 与 YOLO 当前状态、运行方式、测试结果、注意事项。
- `HANDOFF_GUIDE_ZH.md`：xArm、有线连接、相机、标定、抓取框架的总交接文档。
- `config.json`：当前配置快照；脚本默认仍读取上一级项目根目录的 `config.json`。
- `requirements.txt`：依赖清单快照。
- `README.md`：项目总说明快照。

## 推荐运行方式

在上一级 `shop-workspace` 目录运行：

OCR 小票识别：

```bat
set "ZHIPUAI_API_KEY=你的智谱APIKey"
.\.venv\Scripts\python.exe .\new_scripts_handoff\receipt_ocr.py --image runs\receipt_ocr\你的图片.jpg
```

如果不传 `--image`，`receipt_ocr.py` 会尝试从 Gemini 336 相机拍一帧。

## 不在这里放的内容

- `runs/`：测试图片和 OCR 输出不交接，避免混入临时数据。
- API Key：不要写进任何文件，只在命令行临时设置环境变量。
- YOLO 权重：仍放在上一级 `models/products.pt`，后续建仓库时建议用 Git LFS 管理。
