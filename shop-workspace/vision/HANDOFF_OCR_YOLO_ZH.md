# xARM 商超项目交接文档：小票 OCR 与商品识别

> **历史资料提示：** 本文记录整合前的阶段状态，其中 `.venv`、Python 3.12 和“尚未自动串联”等说明已过期。当前项目使用 Conda 环境 `xarm`，正式入口及配置以根目录 `README.md` 和 `HANDOFF_GUIDE_ZH.md` 为准。


更新日期：2026-09-15  
适用项目：xArm 6 + Orbbec Gemini 336 + YOLO + 智谱 GLM-OCR  
项目目录：`C:\Users\LENOVO\Documents\Codex\2026-09-13\nin\work\xarm-shop-demo-review\xarm-shop-demo\shop-workspace`

## 1. 本阶段目标

本项目最终希望完成以下流程：

```text
相机看到小票
    ↓
OCR 识别小票文字
    ↓
从文字中解析待抓商品名称
    ↓
把商品名称映射为 YOLO 类别
    ↓
相机在货架/桌面画面中定位指定商品
    ↓
RGB-D 计算商品的相机三维坐标
    ↓
手眼标定转换为 xARM 基座坐标
    ↓
规划预抓取、接近、夹持、抬升和退出动作
```

当前阶段主要完成了前半部分：

1. Gemini 336 相机驱动、RGB-D 对齐取流已经可用。
2. 现有 YOLO 商品模型已经能够加载和推理。
3. 已经提供实时商品识别窗口，每帧只框置信度最高的商品。
4. 智谱 `glm-ocr` 接口已经接入，可拍摄当前相机画面或读取已有图片并输出文字。
5. OCR 和商品识别目前是两个独立入口，尚未自动串联。
6. 真实抓取仍处于安全锁定状态，不能把当前视觉演示理解成已经完成抓取闭环。

## 2. 当前状态总览

| 模块 | 状态 | 结论 |
| --- | --- | --- |
| Python 虚拟环境 | 已完成 | `.venv` 可直接运行，Python 3.12.8 |
| Gemini 336 彩色取流 | 已实机验证 | 640×480、30 FPS 配置可启动 |
| Gemini 336 深度取流 | 已实机验证 | 深度已对齐到彩色图 |
| YOLO 权重加载 | 已验证 | `models/products.pt` 可由 Ultralytics 加载 |
| YOLO 单帧推理 | 已验证 | 原项目视觉入口可运行 |
| YOLO 连续推理 | 已实机验证 | `live_yolo.py` 已连续处理 3 帧并正常退出 |
| 最高置信度商品框选 | 已完成 | 每帧只画一个最高置信度框 |
| 指定商品过滤 | 已完成 | 支持 `--item 可乐` 等名称/别名 |
| GLM-OCR 鉴权与调用 | 已验证 | 已生成两次本地 OCR 结果 |
| 相机直接拍小票 | 已完成 | `receipt_ocr.py` 未指定图片时拍当前一帧 |
| 已有图片 OCR | 已完成 | 支持 JPG/JPEG/PNG，限制 10 MB |
| OCR 商品名结构化解析 | 未完成 | 当前输出原始 Markdown 文字 |
| OCR → YOLO 自动选择类别 | 未完成 | 需要下一阶段串联 |
| 手眼标定 | 代码已有，现场结果未验收 | `calibration.validated=false` |
| 自动抓取 | 尚不可执行 | `motion_enabled=false`，关键实测参数为空 |
| 放置动作 | 未实现 | 当前抓取框架结束后保持夹持 |

## 3. 硬件与已验证信息

### 3.1 机械臂

- 型号：UFactory xArm 6。
- 控制器 IP：`192.168.1.209`。
- 电脑有线网卡此前配置为：`192.168.1.12/24`。
- UFactory Studio 已能通过 `http://192.168.1.209:18333` 打开。
- 当前视觉脚本不连接机械臂，也不会使能或运动机械臂。
- 机械臂相关的完整部署、安全检查和标定步骤见 `HANDOFF_GUIDE_ZH.md`。

### 3.2 相机

- 型号：Orbbec Gemini 336。
- 已记录序列号：`CP9E1630019L`。
- 连接方式：Type-C 数据线直连电脑 USB 3.x 接口。
- 彩色配置：640×480、30 FPS、RGB。
- 深度配置：848×480、30 FPS、Y16。
- D2C 对齐后尺寸：640×480。
- 已记录深度有效像素比例：约 93.28%。
- 相机实测信息保存在 `runs/camera_check_external/camera.json`。

相机固定在机械臂末端附近，属于“眼在手上”布置。只要相机支架相对法兰发生拆卸、移动、松动或旋转，就必须重新做手眼标定。

### 3.3 夹爪

- 当前配置目标为 Robotiq 2F-85。
- `config.json` 中开合位置仍是初始值，需要按实物验证。
- 实际 TCP 偏移、负载、抓取深度等参数尚未验收。

## 4. 软件环境

当前已验证环境：

| 软件包 | 版本 |
| --- | --- |
| Python | 3.12.8 |
| numpy | 2.5.3 |
| opencv-python | 4.14.0.94 |
| pyorbbecsdk2 | 2.1.2 |
| ultralytics | 8.4.152 |
| torch | 2.14.0，当前为 CPU 版本 |
| requests | 2.34.2 |

依赖声明位于 `requirements.txt`。新电脑不要复制整个 `.venv`，应重新创建：

```cmd
cd /d "项目目录"
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

注意：`.venv-stale-20260915` 是此前损坏环境的保留目录，不应继续使用，也不需要交付到新电脑。

## 5. 目录和关键文件

```text
shop-workspace\
├─ config.json                     总配置：机器人、相机、商品映射、安全参数
├─ requirements.txt                Python 依赖
├─ README.md                       常用命令和原项目说明
├─ HANDOFF_GUIDE_ZH.md             完整部署、标定和抓取交接手册
├─ HANDOFF_OCR_YOLO_ZH.md          本文：OCR 与商品识别专项交接
├─ live_yolo.py                    实时商品识别窗口
├─ receipt_ocr.py                  小票拍摄/读取及 GLM-OCR 调用
├─ models\products.pt              已有 YOLO 商品权重
├─ xarm_grasp\camera.py            Gemini 336 RGB-D 相机封装
├─ xarm_grasp\check_camera.py      单帧相机诊断
├─ xarm_grasp\__main__.py          原视觉与抓取主入口
├─ xarm_grasp\geometry.py          深度反投影、坐标转换和抓取分段规划
├─ xarm_grasp\robot.py             xArm/夹爪接口及安全检查
├─ xarm_grasp\calibrate.py         眼在手上标定采集与求解
├─ tests\test_demo_scripts.py      OCR/实时识别辅助逻辑测试
├─ tests\test_camera_adapter.py    相机格式适配测试
├─ tests\test_pipeline.py          几何、规划、机器人异常测试
└─ runs\                           运行输出；默认不纳入版本控制
```

重要提醒：`.gitignore` 当前忽略 `models/*.pt`。如果以后通过 Git 交接，必须单独确认 `models/products.pt` 已复制到新电脑，否则代码存在但模型会缺失。

## 6. 接手后五分钟快速检查

以下命令全部按 Windows CMD 编写，不要在变量名中添加反斜杠。

### 6.1 进入项目

```cmd
cd /d "C:\Users\LENOVO\Documents\Codex\2026-09-13\nin\work\xarm-shop-demo-review\xarm-shop-demo\shop-workspace"
```

### 6.2 检查离线测试

```cmd
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

交接时结果为：15 项测试全部通过。

这些是合成数据和模拟机器人测试，不会连接硬件；它们不能代替真实相机、标定精度和抓取安全验收。

### 6.3 检查相机

先关闭可能占用相机的其他程序，然后运行：

```cmd
.\.venv\Scripts\python.exe -m xarm_grasp.check_camera --output runs\camera_check
```

成功后应生成：

```text
runs\camera_check\color.jpg
runs\camera_check\depth_mm.npy
runs\camera_check\camera.json
```

### 6.4 检查实时 YOLO

```cmd
.\.venv\Scripts\python.exe .\live_yolo.py
```

窗口中：

- `Q` 或 `Esc`：退出。
- `S`：保存当前带框画面。

只检查可乐：

```cmd
.\.venv\Scripts\python.exe .\live_yolo.py --item 可乐
```

临时降低置信度阈值：

```cmd
.\.venv\Scripts\python.exe .\live_yolo.py --item 可乐 --conf 0.4
```

### 6.5 检查 OCR

不要把 API Key 写进代码、`config.json`、README、交接文档或 Git。CMD 当前窗口中临时设置：

```cmd
set "ZHIPUAI_API_KEY=在这里粘贴新密钥"
```

环境变量名必须是：

```text
ZHIPUAI_API_KEY
```

不能写成 `ZHIPUAI\_API\_KEY`。设置密钥时，等号后和结尾都不要多出空格。

从相机拍摄当前一帧并识别：

```cmd
.\.venv\Scripts\python.exe .\receipt_ocr.py
```

识别已有图片：

```cmd
.\.venv\Scripts\python.exe .\receipt_ocr.py --image "C:\完整路径\receipt.jpg"
```

CMD 关闭后，该环境变量失效。项目不保存密钥。

## 7. 实时商品识别实现说明

入口：`live_yolo.py`。

### 7.1 当前工作流程

1. 读取 `config.json`。
2. 读取 `products` 中所有已登记商品及其类别编号。
3. 加载这些商品共同使用的 `models/products.pt`。
4. 打开 Gemini 336 的 RGB-D 数据流。
5. 对每一帧彩色图调用 Ultralytics YOLO。
6. 丢弃未在 `config.json` 中登记的类别。
7. 如果使用 `--item`，再丢弃所有非目标类别。
8. 在剩余检测框中选择置信度最高的一个。
9. 只绘制这个框和置信度。
10. 按键退出或保存截图。

这里的“最高置信度”是每一帧独立计算，并不是跨帧跟踪。商品被短暂遮挡或模型置信度波动时，框可能消失或切换到另一个商品。

### 7.2 当前配置的商品映射

| 业务商品名 | 可用别名 | YOLO class_id | 模型内部标签 |
| --- | --- | ---: | --- |
| 可口可乐罐装 | 可口可乐、可乐、coke | 0 | 67 |
| 雪碧罐装 | 雪碧、sprite | 1 | 68 |
| 芬达罐装 | 芬达、fanta | 2 | 69 |
| 水溶C100瓶装 | 水溶C100、C100 | 5 | 72 |
| 名仁苏打水饮料 | 苏打水 | 8 | 75 |
| 东方树叶茉莉花茶 | 东方树叶、茉莉花茶 | 9 | 76 |

权重实际包含 23 个类别，类别编号为 0–22，模型内部名称是字符串 `67`–`89`。当前代码不依赖这些数字字符串展示商品名，而是依赖 `config.json` 的业务映射。

如果修改或重训模型，务必同时核对：

1. 新模型的 `model.names`。
2. `config.json` 中每种商品的 `class_id`。
3. 商品别名是否覆盖 OCR 可能输出的名称。
4. 新模型文件路径是否正确。

类别顺序一旦变化，只替换 `products.pt` 而不更新 `class_id`，会造成“框的位置正确但商品名称错误”或抓错商品。

### 7.3 当前限制

- 只画一个框，不显示所有候选框。
- 没有跨帧跟踪和连续多帧确认。
- 没有利用深度过滤货架背景或异常距离。
- 没有遮挡处理。
- 当前 PyTorch 是 CPU 版本，推理速度取决于电脑性能。
- OpenCV 默认字体不稳定支持中文，因此窗口标签使用英文短名；内部业务名仍是中文。
- 单纯“全类别最高置信度”适合演示，不适合作为最终抓取决策。最终闭环必须根据 OCR 指定的目标类别过滤。

### 7.4 建议的识别验收方法

不要只拿一帧判断模型是否可用。每类商品建议采集：

- 正面、侧面、轻微旋转。
- 近、中、远三个距离。
- 不同光照和反光条件。
- 单个商品、多个同类、与其他商品混放。
- 相机安装后的真实俯仰角。
- 夹爪或机械臂局部遮挡。

分别统计误检、漏检、置信度和框中心稳定性。如果新相机视角与暑假比赛数据差异明显，旧模型可用于快速验证，但很可能需要用当前相机补拍数据进行微调。

## 8. OCR 实现说明

入口：`receipt_ocr.py`。

### 8.1 当前工作流程

1. 从环境变量 `ZHIPUAI_API_KEY` 读取密钥。
2. 如果提供 `--image`，读取已有 JPG/JPEG/PNG。
3. 如果没有 `--image`，打开 Gemini 336 并拍摄当前一帧彩色图。
4. 将图片编码为 Base64 Data URI。
5. 请求智谱文档解析端点：`https://open.bigmodel.cn/api/paas/v4/layout_parsing`。
6. 模型固定为 `glm-ocr`。
7. 从响应的 `md_results` 中提取 Markdown 文字。
8. 在终端打印文字。
9. 保存 Markdown 和完整 JSON 响应。

官方参考：

- `https://docs.bigmodel.cn/cn/guide/models/vlm/glm-ocr`
- `https://docs.bigmodel.cn/api-reference/模型-api/文档解析`

### 8.2 已验证结果

本机已经生成两次 OCR 结果：

```text
runs\receipt_ocr\receipt_20260915_153016_ocr.md
runs\receipt_ocr\receipt_20260915_153016_ocr.json
runs\receipt_ocr\receipt_20260915_153200_ocr.md
runs\receipt_ocr\receipt_20260915_153200_ocr.json
```

第二次结果实际识别为：

```text
可乐芬达
```

对应响应：模型 `glm-ocr`、1 页、总计 89 Tokens。由此可确认：当前脚本的环境变量读取、图片上传、接口鉴权、请求和响应解析链路能够跑通。

第一次结果主要返回了页面图像标记，说明“接口成功”不等于“业务文字一定正确”。相机画面、小票占比、清晰度、透视、反光以及小票上是否真的有可读文字，都会显著影响结果。

### 8.3 输出文件

如果输入图片名是：

```text
receipt_20260915_153200.jpg
```

会得到：

```text
receipt_20260915_153200_ocr.md    便于人阅读的 OCR 结果
receipt_20260915_153200_ocr.json  完整 API 响应及版面信息
```

默认目录为 `runs/receipt_ocr/`。该目录默认不进入版本控制，因为里面可能包含票据内容和现场图片。

### 8.4 当前限制

- 未指定图片时直接抓当前一帧，没有拍照倒计时和预览确认。
- 目前返回的是原始 OCR 文本，不会自动解析商品列表或数量。
- 没有重试、限流退避、离线缓存和调用费用统计汇总。
- 没有对模糊、过曝、反光、裁切失败做图像质量判断。
- 没有对 API 返回的商品名做业务词典纠错。
- OCR 依赖互联网和有效的智谱账户额度。
- API Key 曾在调试聊天中明文出现过，应废弃旧密钥并重新生成；交接时不能传递旧密钥。

## 9. 下一阶段：把 OCR 和 YOLO 串起来

下一位同学的首要任务不是立即启动机械臂，而是先完成纯视觉闭环：

```text
OCR 原文
  → 商品词语标准化
  → config.json 商品匹配
  → 得到目标 class_id
  → YOLO 只检测该 class_id
  → 连续多帧确认目标稳定
  → 输出目标框、置信度和深度三维点
```

### 9.1 建议新增 `receipt_parser.py`

输入：OCR 返回的字符串。  
输出：标准商品名列表，必要时包含数量。

建议的最小规则：

1. 去除空白、标点差异和常见 OCR 噪声。
2. 先匹配完整商品名，再匹配 `config.json` 中 aliases。
3. 支持“可乐”“可口可乐”“可口可乐罐装”等归一化。
4. 同一商品重复出现时保留数量。
5. 找不到对应商品时明确报错，不要猜测最接近的类别后直接抓取。
6. 多个候选商品同时匹配时要求消歧。

建议中间数据结构：

```json
{
  "ocr_text": "可乐 芬达",
  "requested_items": [
    {"name": "可口可乐罐装", "class_id": 0, "quantity": 1},
    {"name": "芬达罐装", "class_id": 2, "quantity": 1}
  ],
  "unmatched_text": []
}
```

### 9.2 将两个脚本拆出可复用函数

当前两个脚本是独立命令行程序。建议重构但保持原命令仍可用：

- 从 `receipt_ocr.py` 拆出 `ocr_image(image_path) -> dict`。
- 从商品识别逻辑拆出 `detect_target(frame, class_id) -> detection`。
- 新建 `vision_pipeline.py` 负责一次完整的“小票 → 商品目标 → 三维点”流程。
- UI、网络调用、模型推理和机械臂控制分层，避免一个超长脚本同时处理所有状态。

### 9.3 最终识别不能使用“全类别最高置信度”

演示脚本允许从全部商品中选择最高置信度目标；但收到小票目标后，必须只保留小票对应的 `class_id`。

例如小票要求“芬达”，即使画面中的可乐置信度为 0.95、芬达只有 0.75，也必须选择芬达，而不能选择全局最高的可乐。

### 9.4 增加多帧稳定确认

机械臂运动前建议至少要求：

- 连续若干帧检测到相同类别。
- 置信度均超过阈值。
- 框中心或深度三维点波动小于实测阈值。
- 目标深度在工作范围内。
- 如果出现多个同类商品，使用明确的选取规则，例如离预定抓取中心最近，而不是任意取最高置信度。

阈值必须通过现场数据确定，不能直接照抄示例值。

## 10. 从识别到抓取还缺什么

原项目已具备坐标转换和分段动作框架，但以下条件没有完成前，禁止真实执行：

1. 完成法兰到夹爪实际抓取 TCP 的测量和 Studio 设置。
2. 完成 Gemini 336 相机到法兰的眼在手上标定。
3. 使用未参与标定的数据验证基座坐标误差。
4. 填写每种商品的观察位姿。
5. 测量并填写瓶罐表面到夹持中心的 `grasp_depth_offset_mm`。
6. 测量机械臂真实安全工作范围，填写 `workspace_mm`。
7. 验证夹爪开合方向、位置、力和速度。
8. 逐段低速验证工具 Y/Z/X 方向与现场物理方向一致。
9. 验证预抓取、接近、夹持、抬升和退出路径不会碰撞桌面、货架、相机、夹爪或机械臂连杆。
10. 完成以上验收后，才能考虑将 `motion_enabled` 改为 `true`。

当前 `config.json` 明确保留：

```json
"motion_enabled": false
```

这是安全锁，不应为“先看看能不能动”而直接改为 `true`。

## 11. 常见问题排查

### 11.1 CMD 中提示 `$env` 或 `$secureKey` 不是命令

原因：使用了 PowerShell 语法。CMD 应使用：

```cmd
set "ZHIPUAI_API_KEY=密钥"
```

### 11.2 环境变量明明设置了，脚本仍提示未设置

检查是否误写为：

```text
ZHIPUAI\_API\_KEY
```

正确形式没有反斜杠：

```text
ZHIPUAI_API_KEY
```

检查当前 CMD：

```cmd
if defined ZHIPUAI_API_KEY (echo API Key 已设置) else (echo API Key 未设置)
```

不要使用 `echo %ZHIPUAI_API_KEY%`，否则会把密钥再次显示出来。

### 11.3 找不到 Python

必须先进入项目目录，再运行：

```cmd
.\.venv\Scripts\python.exe --version
```

正确路径以 `.\.venv` 开头，不是 `..venv`。

### 11.4 找不到 `receipt_ocr.py`

正确文件名：

```text
receipt_ocr.py
```

不要写成 `receipt\_ocr.py`。

### 11.5 PowerShell 启动时报 `profile.ps1` 禁止运行脚本

这是电脑 PowerShell Profile 的执行策略问题，与项目和 Python 虚拟环境无直接关系。本文命令使用 CMD，不需要为运行项目而修改全局执行策略。

### 11.6 相机提示未检测到或 Access Denied

1. 关闭其他相机窗口、Orbbec Viewer 和此前未退出的 Python 进程。
2. 拔插相机数据线。
3. 优先接电脑 USB 3.x 接口，避免无源 Hub。
4. 在设备管理器确认设备存在。
5. 先运行单帧 `xarm_grasp.check_camera`，再运行实时 YOLO。
6. 确认 `config.json` 的 `expected_model` 仍为 `Gemini 336`。

同一时刻通常只能由一个进程独占相机。不要同时运行实时 YOLO 和相机拍摄 OCR。

### 11.7 YOLO 窗口有画面但没有框

依次检查：

1. 商品是否属于 `config.json` 已配置的六类。
2. 是否错误使用了 `--item` 过滤其他类别。
3. 商品是否过小、被遮挡、反光或视角差异过大。
4. 临时尝试 `--conf 0.4`，但不能把过低阈值直接用于抓取。
5. 确认 `models/products.pt` 存在。
6. 用标注数据验证 `class_id` 映射是否正确。

### 11.8 OCR 返回空文字或图片标记

1. 确保小票占画面主要区域。
2. 小票铺平，减少透视变形。
3. 避免顶部灯光造成反光和过曝。
4. 保证文字方向正确、对焦清晰。
5. 先保存图片并人工查看，再用 `--image` 重复识别。
6. 确认图片为 JPG/JPEG/PNG 且小于 10 MB。

### 11.9 中文终端显示乱码

可在 CMD 中先运行：

```cmd
chcp 65001
```

再执行 Python。若只是在旧版 CMD 中显示异常，应同时检查保存的 `_ocr.md` 是否为正常 UTF-8 内容。

## 12. 测试与验收计划

### 12.1 当前自动化测试

```cmd
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

交接时一共 15 项，覆盖：

- RGB/BGR 相机格式转换。
- 相机 Profile 选择。
- 商品别名映射。
- OCR 图片 Data URI 编码。
- OCR 响应文字提取。
- 深度反投影和无效深度拒绝。
- 相机点到基座点的变换。
- 工具坐标分轴规划。
- 工作空间和占位配置拒绝。
- 空抓拒绝和异常停止。
- 合成手眼标定恢复。

### 12.2 下一阶段应补充的测试

1. OCR 文本中一个商品的解析。
2. OCR 文本中多个商品和数量的解析。
3. 商品别名、空格、标点和常见错字归一化。
4. 未知商品和歧义商品拒绝。
5. OCR 指定类别后，YOLO 不会选择其他更高置信度类别。
6. 同类多个框的选取规则。
7. 多帧稳定性判定。
8. API 超时、401、429、服务端错误和空响应处理。
9. 相机被占用和中途断开处理。
10. 纯视觉闭环测试，确保不导入或调用机械臂运动。

### 12.3 数据集验收

为每个目标商品准备独立测试集，训练集和验收集不能完全重复。建议记录：

- 总图片数。
- 每类样本数。
- 精确率、召回率和混淆矩阵。
- 当前固定相机视角下的漏检率。
- 多商品场景下的误抓候选率。
- 框中心在连续帧中的像素和三维位置波动。

## 13. 交接安全与隐私

- 不交接聊天中出现过的旧 API Key，应在智谱控制台重新生成。
- API Key 只能通过环境变量或后续专用密钥管理机制提供。
- `.env`、`.env.*` 已加入 `.gitignore`；但当前脚本并不要求 `.env`。
- `runs/receipt_ocr/` 可能包含真实小票、消费信息和完整 API 响应，不应上传到公开仓库。
- `runs/` 默认忽略，正式数据应按项目的数据管理要求另行备份。
- 不要在报错截图、演示视频或终端录屏中显示密钥。
- 真实机械臂联调必须有人在急停附近观察，先低速、空载、无障碍验证。

## 14. 建议的后续任务优先级

### P0：先完成纯视觉闭环

1. 新增 OCR 商品解析器。
2. 建立 OCR 名称到 `config.json` 商品的严格映射。
3. 将 OCR 目标传给指定类别 YOLO 检测。
4. 增加多帧稳定确认。
5. 输出统一 JSON，不运动机械臂。

### P1：验证模型和数据

1. 用当前固定相机重新采集六类商品测试数据。
2. 统计旧模型在新视角下的准确率。
3. 根据结果决定是否补数据微调。
4. 固化新模型版本、类别表和数据集说明。

### P2：三维定位

1. 对目标框中心或更稳健的抓取点取深度。
2. 做邻域深度过滤和前景选择。
3. 验证相机坐标三维点稳定性。
4. 完成并验收手眼标定。
5. 验证转换后的基座坐标误差。

### P3：低速抓取联调

1. 测量 TCP、工作范围和每类抓取偏置。
2. 只规划并打印动作，不执行。
3. Studio 手动点动验证各段终点和路径。
4. 低速空载逐段执行。
5. 最后才进行真实瓶罐抓取。

### P4：完整业务流程

1. 支持小票中多个商品和数量。
2. 完成抓取后的放置位置和动作。
3. 处理找不到商品、多个同类和空抓。
4. 增加运行日志、任务状态和人工确认机制。

## 15. 交接清单

交接给下一位同学前逐项确认：

- [ ] 已复制整个项目源代码。
- [ ] 已单独确认 `models/products.pt` 存在且能加载。
- [ ] 已提供 `requirements.txt`，没有复制旧电脑虚拟环境代替安装。
- [ ] 已提供 `config.json`，但没有擅自解除运动安全锁。
- [ ] 已说明相机型号、序列号和安装方式。
- [ ] 已说明机械臂 IP 和电脑网卡静态 IP。
- [ ] 已运行 15 项离线测试。
- [ ] 已运行相机单帧检查。
- [ ] 已运行实时 YOLO 并正确退出。
- [ ] 已使用新 API Key 验证 OCR，但没有把 Key 写进任何文件。
- [ ] 已检查 OCR Markdown 和 JSON 输出。
- [ ] 已说明旧 YOLO 权重的类别映射。
- [ ] 已说明 OCR 与 YOLO 尚未自动串联。
- [ ] 已说明手眼标定、TCP、工作范围和抓取参数尚未验收。
- [ ] 已说明 `motion_enabled=false` 必须保持到安全验收完成。

## 16. 一句话交接结论

当前已经具备“Gemini 336 取流 → YOLO 实时框选商品”和“Gemini 336/本地图片 → GLM-OCR 输出文字”两个可独立运行的能力，并且已有实机和接口成功记录；下一位同学应先完成 OCR 商品名结构化、严格类别映射和指定目标的多帧视觉确认，再进入三维定位、手眼标定与低速抓取联调，不能直接解除机械臂运动安全锁。

