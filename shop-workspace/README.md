# 小票驱动的 xArm 商品抓取：环境配置与使用手册

本项目使用 **xArm 6 + 腕部 Orbbec Gemini 336 + Robotiq 2F-85**，完成“小票选商品 → 货架定位 → 抓取 → 携物返回初始位”的一次抓取。主入口是 `python -m xarm_grasp`。

本文同时说明安装、配置、运行、故障排查和代码原理。命令以 **Windows CMD / Anaconda Prompt** 为主，示例工程位置为 `C:\Users\LENOVO\Desktop\xarm-shop-demo`；放在其他位置时替换路径。代码目录名是 `xarm_grasp`，下划线前不加反斜杠。

## 目录

- [1. 项目能力与六步流程](#sec-flow)
- [2. 从零配置 Python 环境](#sec-install)
- [3. 连接机械臂、夹爪与相机](#sec-hardware)
- [4. 配置文件与参数](#sec-config)
- [5. 手眼标定与验收](#sec-calibration)
- [6. 先做独立检查](#sec-checks)
- [7. 两种分步测试模式](#sec-steps)
- [8. 自动执行、计划模式与参数总表](#sec-cli)
- [9. 输出文件与结果检查](#sec-output)
- [10. 代码结构与运行原理](#sec-code)
- [11. Python 函数调用与离线回放](#sec-api)
- [12. 故障排查、停止与恢复](#sec-troubleshooting)
- [13. 修改代码与验证](#sec-development)

<a id="sec-flow"></a>
## 1. 项目能力与六步流程

| 步骤 | 动作 | 完成后的位置 / 结果 |
|---|---|---|
| 1 | 移动到 `initial_pose` | 初始中转位 |
| 2 | 移动到 `receipt.observation`，拍小票并 OCR | 停在小票位，得到标准商品名称 |
| 3 | 返回 `initial_pose` | 初始中转位 |
| 4 | 先在 `grasp_observation` 做 YOLO；零目标时可切换备用位，再定位并预检抓取计划 | 停在成功识别的观察位，夹爪尚未动作 |
| 5 | 开爪、Y/X 对齐、Z 深入、夹紧、抬升、退出 | 停在抓取退出点 |
| 6 | 携带商品返回 `initial_pose` | 保持夹持，流程结束 |

目前一次运行只抓一个物体。OCR 要求小票命中一个商品种类，不解析数量并执行多次抓取；出现多个已配置商品种类会拒绝。YOLO 要求当前画面中对应类别的有效检测恰好有一个。项目没有放置、松爪交货、连续订单或自动失败重试流程。

OCR 使用配置中的远程服务，会上传小票图像；YOLO 使用本地模型。分轴抓取保持 TCP 朝向不变，移动到预设关节位时则可能改变朝向。

<a id="sec-install"></a>
## 2. 从零配置 Python 环境

### 2.1 安装 Conda 并打开正确终端

已有 `(xarm)` 环境的机器可以跳过创建步骤。新机器先安装适合系统的 Windows x64 Miniconda，安装后从开始菜单打开 **Anaconda Prompt**。官方步骤见 [Miniconda Windows 安装说明](https://www.anaconda.com/docs/getting-started/miniconda/install/windows-gui-install)。

下文选择 Python 3.10 作为项目环境版本。不要在安装依赖时使用一个 Python、运行时又换成另一个。

```bat
conda --version
conda env list
conda create -n xarm python=3.10 -y
conda activate xarm
cd /d C:\Users\LENOVO\Desktop\xarm-shop-demo\shop-workspace
python --version
python -c "import sys; print(sys.executable)"
```

`conda create` 只在没有 `xarm` 环境时执行。之后每次新开终端只需 `conda activate xarm` 和进入工作目录。

如果使用 PowerShell，进入目录的命令为：

```powershell
Set-Location C:\Users\LENOVO\Desktop\xarm-shop-demo\shop-workspace
```

### 2.2 安装项目依赖与 xArm SDK

在 `shop-workspace` 下执行：

```bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install xarm-python-sdk
python -m pip check
```

当前 [requirements.txt](requirements.txt) 包含：

| 安装包 | 约束 | 用途 |
|---|---|---|
| numpy | `>=1.26,<3` | 矩阵、坐标、深度计算 |
| opencv-python | `>=4.8,<5` | 图像处理、相机预览、棋盘格与手眼标定 |
| pyorbbecsdk2 | `>=2.1.2,<3` | Gemini 相机 Python 绑定及原生运行库 |
| ultralytics | `>=8.3,<9` | 加载 YOLO 权重并推理 |
| requests | `>=2.31,<3` | OCR HTTP 请求 |

**xArm SDK 需要单独安装**，目前没有列在 requirements.txt 中。上述安装命令来自 [xArm 官方 SDK 说明](https://github.com/xArm-Developer/xArm-Python-SDK/blob/master/README.rst)。运行时 `robot.py` 会优先搜索工程根目录的 `xArm-Python-SDK` 源码目录；如果该目录不存在，Python 会继续搜索环境中安装的 `xarm` 包。

本仓库的历史记录包含 SDK 目录引用，但没有提供 `.gitmodules`，不能假设克隆主仓库就已获得 SDK 源码。按上面的 pip 方式安装可避免依赖这些目录。仅下载 Orbbec 源码也不等于安装好了相机原生模块。

检查导入，不连接机械臂：

```bat
python -c "import numpy, cv2, requests, ultralytics; print('Python dependencies OK')"
python -c "from xarm.wrapper import XArmAPI; import xarm; print('xArm SDK:', xarm.__file__)"
python -c "import pyorbbecsdk as ob; print('Orbbec SDK:', ob.__file__); print(ob.Pipeline)"
```

安装包叫 `pyorbbecsdk2`，代码导入名为 `pyorbbecsdk`，两者不同。详细说明见 [Orbbec 官方安装指南](https://orbbec.github.io/pyorbbecsdk/source/2_installation/install_the_package.html)。

本项目没有固定到每个依赖的精确版本，也没有提供 CUDA 专用安装方案。先完成导入和模型加载检查；若需 GPU 加速，再按所用 PyTorch 的官方安装要求配置并单独验证。

### 2.3 准备训练好的商品权重

将与商品类别配置对应的训练权重放到：

```text
C:\Users\LENOVO\Desktop\xarm-shop-demo\shop-workspace\models\products.pt
```

`models/*.pt` 被 Git 忽略，因此新克隆的工程需要另外取得权重。通用 YOLO 权重不能直接代替这个商品模型。

```bat
dir models\products.pt
python -c "from ultralytics import YOLO; m=YOLO('models/products.pt'); print(m.names)"
```

核对模型输出的类别编号是否与 `config.json.products.*.class_id` 一致。程序只检查该编号存在，不能判断“编号对应的商品名称是否填对”。

<a id="sec-hardware"></a>
## 3. 连接机械臂、夹爪与相机

### 3.1 xArm 网络与控制器

当前配置中的控制器 IP 是 `192.168.1.209`。若控制器现场地址不同，修改 `robot.ip`。

在 Windows 网络设置中找到实际连接机械臂的有线网卡，使其与控制器在同一子网。例如，若现场控制器使用 `192.168.1.209/24`，电脑可使用该网段内未占用的地址，如 `192.168.1.12`，掩码 `255.255.255.0`；不要与控制器或其他设备重复。专用直连网卡通常不需要默认网关，上网可使用另一张网卡。

```bat
ipconfig
ping 192.168.1.209
```

随后在 xArm Studio 中确认能连接实际的六轴机械臂、读取关节和 TCP、查看错误状态。ping 通只代表基本网络连通，不代表 SDK、TCP 或运动配置已正确。

夹爪由 xArm 控制器接口操作；检查 Robotiq 接线、供电和控制器配置，先在 Studio 中确认能正常开合。程序实际抓取前会执行夹爪 reset 和 activate。

### 3.2 Gemini 336 首次 Windows 环境设置

相机通过支持数据传输的 USB 3.x 接口连接电脑。首次使用按 Orbbec 官方要求执行 UVC metadata 设置，设置程序可能请求管理员权限。安装包方式无需本地 Orbbec 源码目录。

先定位当前环境自带的脚本：

```bat
python -c "from pathlib import Path; import pyorbbecsdk; p=Path(pyorbbecsdk.__file__).resolve().parent/'shared'/'setup_env.py'; print(p); print('exists:', p.is_file())"
```

将输出的完整脚本路径复制到下列命令中，替换占位路径：

```bat
python "替换为上一步输出的完整路径\setup_env.py"
```

如已有官方源码目录，也可使用其中的 `scripts\env_setup\setup_env.py`。完成设置后重新插拔相机。Windows 设置作用和脚本位置见 [Orbbec 环境设置说明](https://orbbec.github.io/pyorbbecsdk/source/2_installation/install_the_package.html#environment-setup-one-time)。

### 3.3 检查相机与预览

当前配置指定 `Gemini 336`、序列号 `CP9E1630019L`、彩色分辨率 `640×480`、帧率 `30`。更换相机时应使用实际序列号，并重新确认对应标定。

```bat
python -m vision.check_camera --output runs\camera_check
python -m vision.camera_preview
python -m vision.camera_preview --show-depth
```

以上三条按需逐条运行，预览窗口关闭后再运行下一条。预览中 `Q` / `Esc` 退出，`S` 保存当前帧。开始 OCR 或抓取前关闭相机预览、Orbbec Viewer 等可能占用设备的程序。

相机检查输出 `camera.json`、`color.jpg` 和 `depth_mm.npy`。检查型号、序列号、有效深度比例和距离范围是否符合当前场景。

<a id="sec-config"></a>
## 4. 配置文件与参数

默认读取 [config.json](config.json)。它保存现场参数，移植到其他设备时不能直接照搬现有标定矩阵和位姿。JSON 中不能添加注释或末尾多余逗号。可以检查语法而不打印配置内容：

```bat
python -c "import json; from pathlib import Path; json.loads(Path('config.json').read_text(encoding='utf-8-sig')); print('JSON syntax OK')"
```

配置只在程序启动时读取一次，保存修改后需要重新启动。多数命令支持 `--config`；模型的相对路径以配置文件所在目录为基准，`--output` 的相对路径以终端当前目录为基准。

### 4.1 配置项总览

| 字段 | 作用 |
|---|---|
| `motion_enabled` | 真实执行总开关，必须为 `true` 才允许执行运动流程 |
| `robot` | 控制器 IP、TCP 偏移、关节与末端速度 |
| `camera` | 相机选择、彩色流参数、取帧超时和预热帧数 |
| `initial_pose` | 初始 / 中转 / 最终返回位姿 |
| `receipt` | 小票观察位、OCR 环境变量名和请求参数 |
| `grasp_observation` | 商品共用的预抓取观察位姿 |
| `grasp_observation_fallback` | 备用 YOLO 观察位；为 null / 未配置时禁用 |
| `calibration` | 相机序列号、法兰到相机变换、验收标记 |
| `workspace_mm` | 基座系 XYZ 允许范围 |
| `products` | 商品名称、别名、模型类别、夹爪位置和深入补偿 |
| `confidence`、`depth` | 检测置信度和深度取样约束 |
| `tool_motion` | 工具轴角色、方向、抬升量和运动容差 |

所有 `--execute` 模式（包括只去初始位的 `--step 1`）都执行公共配置校验：运动开关、标定、初始位、商品观察位、速度、工具轴和工作区必须有效。开关为 true 只表示配置允许执行，不代表程序自动完成了现场验收。

### 4.2 OCR 密钥：变量名和变量值分开放

配置保持：

```json
"api_key_env": "ZHIPUAI_API_KEY"
```

真实密钥在运行程序的同一终端中设置。**不要把真实密钥写进 api_key_env。**

CMD / Anaconda Prompt：

```bat
set "ZHIPUAI_API_KEY=替换为你的真实API密钥"
if defined ZHIPUAI_API_KEY (echo API key is set) else (echo API key is missing)
```

PowerShell：

```powershell
$env:ZHIPUAI_API_KEY = "替换为你的真实API密钥"
if ($env:ZHIPUAI_API_KEY) { "API key is set" } else { "API key is missing" }
```

这些设置只对当前终端及其子进程有效，重新开窗口需要再次设置。程序不会自动加载 `.env` 文件。不要在日志、截图、Git 中公开密钥。

当前 OCR 配置为 `glm-ocr`，请求地址保存在 `receipt.url`，超时 `receipt.timeout_s=120` 秒，JPEG 质量 `jpeg_quality=95`。如服务调用失败，先用已有图片做独立 OCR 测试，再进入运动流程。

### 4.3 位姿与 TCP

预设位姿支持两种格式：

- `type: "joint"`：`values` 为 `[J1,J2,J3,J4,J5,J6]`，单位度。
- `type: "tcp"`：`values` 为基座坐标系 `[x,y,z,roll,pitch,yaw]`，前三项毫米，后三项度。

当前 `initial_pose`、`receipt.observation`、`grasp_observation` 均使用 joint。用 xArm Studio 手动移动到各个位置并停稳，读取对应关节角，按相同顺序填写。不要把 TCP 六元数组当作关节角。

本项目没有 `xarm_grasp.teach` 命令。若需要用已有 SDK 封装只读 TCP 和当前 TCP offset，可在 Python 中运行：

```python
from xarm_grasp.config import DEFAULT_CONFIG, load
from xarm_grasp.robot import Robot

config = load(DEFAULT_CONFIG)
with Robot(config["robot"]) as robot:
    print("TCP pose:", robot.pose())
    print("TCP offset:", robot.offset())
```

这段代码连接控制器读取数据，没有调用 enable 或运动方法。关节角可从 Studio 获取。

`robot.tcp_offset` 表示法兰到实际工具中心点的位姿，当前为 `[0,0,174,0,0,0]`，必须与 Studio 控制器中的设置一致；程序不会自动修改控制器 TCP。

`workspace_mm` 按 `[[xmin,xmax],[ymin,ymax],[zmin,zmax]]` 填写基座系毫米范围。程序检查目标 TCP 是否在范围内，但不提供机械臂连杆、相机、夹爪与货架的完整碰撞规划；初始位也只是你配置的中转点。

### 4.4 速度与加速度在哪里改

配置备用观察位的方法见第 7.4 节；两个观察位都需要满足相同的 TCP 抓取方向和工作区要求。

修改 config.json 中的 robot 字段：

```json
"tcp_speed_mm_s": 20,
"tcp_acc_mm_s2": 100,
"joint_speed_deg_s": 5,
"joint_acc_deg_s2": 20
```

| 参数 | 单位 | 对应动作 |
|---|---|---|
| `joint_speed_deg_s` | °/s | joint 类型预设位移动；当前第 1、2、3、4、6 步 |
| `joint_acc_deg_s2` | °/s² | 上述关节运动的加速度 |
| `tcp_speed_mm_s` | mm/s | 第 5 步分轴抓取；也用于 tcp 类型预设位 |
| `tcp_acc_mm_s2` | mm/s² | 上述末端直线运动的加速度 |

第 5 步所有平移动作共用同一 TCP 速度，没有按接近、抬升分别配置速度。夹爪速度和力度在 `xarm_grasp/robot.py` 的 `Robot.gripper()` 中，当前为 `speed=64, force=64`，不是毫米速度，也不在 config.json 中。

运动等待当前固定为 30 秒；夹爪等待为 10 秒。大幅降速或长距离运动可能触发等待超时，应结合行程检查，不要只增加超时掩盖控制器故障。

### 4.5 商品和模型类别

当前配置的映射如下；修改时以实际模型 `names` 和现场商品为准：

| 标准商品名 | class_id | aliases | 开爪 / 闭爪原始位置 | 深入补偿 mm |
|---|---:|---|---|---:|
| 雪碧罐装 | 1 | 雪碧、sprite | 0 / 85 | 27 |
| 芬达罐装 | 2 | 芬达、fanta | 0 / 85 | 27 |
| 可口可乐罐装 | 0 | 可口可乐、可乐、coke | 0 / 85 | 27 |
| 东方树叶茉莉花茶 | 9 | 东方树叶、茉莉花茶 | 0 / 81 | 30 |
| 名仁苏打水饮料 | 8 | 苏打水 | 0 / 89 | 25 |
| 水溶C100瓶装 | 5 | 水溶C100、C100 | 0 / 95 | 25 |

`open_position` / `close_position` 是 0–255 的夹爪原始位置，要求闭爪值大于开爪值。它们不是开口毫米数。`grasp_depth_offset_mm` 将商品可见前表面推进到目标夹持深度，非负、按商品实测。

OCR 对文字去除布局符号、统一大小写后，检查标准名称和别名是否包含在文本内。同一商品多个别名命中只算一种商品；多个商品命中会拒绝。别名应避免过短或相互重叠，以免错误匹配。命令行 `--item` 则按配置名称 / 别名精确匹配，不做模糊搜索。

### 4.6 深度和分轴约束

当前 `confidence=0.6`。深度中心邻域半径 `radius=3` 对应 7×7 区域，有效深度范围 `100–2000 mm`；`max_spread_mm=20` 限制邻域第 90 与第 10 百分位的差。至少需要邻域 60% 且不少于 5 个有效样本，再取中位数。

当前工具轴配置：

| 配置 | 当前值 | 含义 |
|---|---|---|
| lateral_axis / vertical_axis / depth_axis | y / x / z | 左右对齐 / 上下对齐与抬升 / 深入退出 |
| approach_axis_sign / lift_axis_sign | +1 / -1 | 沿 Z+ 深入、沿 X- 抬升 |
| lift_mm | 20 | 夹紧后抬升量 |
| axis_deadband_mm | 1 | 小于等于该值的左右 / 上下对齐量归零 |
| max_alignment_mm | 300 | 单轴左右 / 上下对齐幅度上限 |
| min_approach_mm / max_approach_mm | 20 / 600 | 加深入补偿之前的目标有向深度范围 |
| vertical_axis_min_up_component | 0.7 | 抬升轴在基座向上方向的最小投影 |
| position_tolerance_mm | 2 | 分轴移动后的允许位置误差 |
| orientation_tolerance_deg | 0.5 | 分轴移动后的允许朝向变化 |

当前安装姿态下约定工具 Y+ 向左、X- 向上、Z+ 向前。它们随工具姿态变化，不是固定的基座方向。程序检查抬升方向是否充分向上，但不会识别货架是否位于正确朝向。

<a id="sec-calibration"></a>
## 5. 手眼标定与验收

手眼标定得到 `T_flange_camera`，用于把相机看到的点转换到法兰 / 工具坐标。更换相机、改变相机相对法兰的安装后，需要重新标定。当前配置中的矩阵只适用于对应设备和安装。

1. 固定棋盘格，测量方格边长，确认内角点行列数。
2. 用 Studio 手动移动机械臂，停稳后采一帧。
3. 收集不同位置和多轴旋转的视角，建议 15–25 组；代码最低要求 12 组并检查旋转变化。
4. 求解后另外采至少 5 组未参与求解的视角验证。
5. 再验收实际点位精度，最后填写抓取配置。

下面用 **9×6 内角点、25 mm 方格**举例，必须按自己的棋盘格修改。每换一个姿态运行一次 capture，它会追加样本：

```bat
python -m camera_calibration capture --cols 9 --rows 6 --square-mm 25
```

完成采集后：

```bat
python -m camera_calibration solve
```

保持棋盘格固定，在另外的视角逐次执行：

```bat
python -m camera_calibration capture --cols 9 --rows 6 --square-mm 25 --samples camera_calibration/data/validation_samples.json
```

至少 5 个独立验证视角完成后：

```bat
python -m camera_calibration validate
```

默认数据位于 `camera_calibration/data/`：`samples.json`、`result.json`、`validation_samples.json`、`validation_result.json`。capture 读取机械臂状态，不发送运动命令；solve / validate 使用保存文件离线计算。

求解一致性阈值为 5 mm / 2°；独立验证默认阈值为 3 mm / 1°。这些是固定棋盘格观测的一致性指标，不等于对抓取点绝对精度的保证。脚本保持 `validated: false`，不会自动覆盖 config.json。实际验收后手动填写 `calibration.camera_serial`、`T_flange_camera`，再设置 `validated: true`。

使用其他配置时，标定的 `--config` 放在子命令前，例如：

```bat
python -m camera_calibration --config config.json capture --cols 9 --rows 6 --square-mm 25
```

静止多帧诊断工具：

```bat
python -m camera_calibration.calibration_probe capture --label A1 --cols 9 --rows 6 --square-mm 25
python -m camera_calibration.calibration_probe compare
```

多帧诊断默认写入 `camera_calibration/data/probe_v1/`。同一静止姿态下的多帧不能当作多个独立标定视角。更多细节见 [标定工具说明](camera_calibration/README.md)。

<a id="sec-checks"></a>
## 6. 先做独立检查

新设备建议按“导入依赖 → 相机预览 → OCR → YOLO → 标定 / 位姿确认 → 分步实机”的顺序检查。

### 6.1 只测试 OCR

使用已有小票图片，不打开相机：

```bat
python -m vision.ocr --image "C:\data\receipt.jpg" --output runs\ocr_file_001
```

使用相机当前画面：

```bat
python -m vision.ocr --output runs\ocr_camera_001
```

两者都不移动机械臂。相机命令不会自行去小票位，需要你事先准备好视野。返回 `selected_item` 后核对商品是否正确。主入口不带 `--execute`、不带 `--item` 时也只做 OCR：

```bat
python -m xarm_grasp --output runs\receipt_only_001
```

### 6.2 只测试商品定位

相机当前视野中，输出相机系与 TCP 系坐标：

```bat
python -m vision.yolo --item "雪碧" --output runs\sprite_vision_001
```

只检查 YOLO 与深度，不进行手眼转换：

```bat
python -m vision.yolo --item "雪碧" --camera-only --output runs\sprite_camera_001
```

这两个命令不连接机械臂、不移动到货架位。独立 TCP 结果使用配置中的 TCP offset；它允许输出尚未验收的标定计算结果，需检查 `calibration_validated`。

主入口的下列命令只返回相机系坐标，和 `vision.yolo` 默认输出略有不同：

```bat
python -m xarm_grasp --item "雪碧" --output runs\sprite_main_001
```

### 6.3 只检查数学转换

```bat
python -m xarm_grasp.coordinates --camera-point 0 0 500
```

该命令不连接硬件、不联网。输入是相机系毫米坐标，用于验证变换计算，不代表发现了真实商品。

<a id="sec-steps"></a>
## 7. 两种分步测试模式

### 7.1 模式 A：按 Enter 逐步跑完整链路

```bat
conda activate xarm
cd /d C:\Users\LENOVO\Desktop\xarm-shop-demo\shop-workspace
set "ZHIPUAI_API_KEY=替换为你的真实API密钥"
python -m xarm_grasp --execute --step-test --output runs\step_test_001
```

每一步执行前显示提示：

```text
[步骤 1/6] 移动到 initial_pose
确认现场安全后按 Enter 执行；输入 q 后按 Enter 停止：
```

- Enter 执行当前大步骤，完成后等待下一步。
- 在输入提示处输入 q 再 Enter，退出流程。
- 输入其他非空内容也会退出。
- 第 2 步完成后检查 OCR 商品名；第 4 步完成后检查检测框、深度、坐标和轨迹，再确认第 5 步。
- 第 5 步内部多个动作连续执行，不在每个轴移动前等待 Enter。
- 程序在第 1 次确认之前已经校验配置、打开相机、连接并使能机械臂。

若上一次运动流程因程序异常退出并由本程序停在 `state=4`，下一次真实执行会在确认控制器错误码和警告码均为 0、TCP 偏移仍与配置一致后，自动重新使能并切回运动状态。程序不会自动清除错误或警告，也不会自动越过暂停及其他安全状态；遇到这些情况仍须由操作员在确认现场安全后处理。

等待 Enter 期间不要移动机械臂、商品或相机。此模式第 5 步直接使用内存中的第 4 步计划，不具备指定单步模式的跨进程位姿匹配检查。

`--step-test` 必须与 `--execute` 同用，不能同时使用 `--step`、`--item`、`--receipt-image` 或 `--plan-only`。

### 7.2 模式 B：指定一步，执行后退出

**下面每条命令启动后会直接执行，不会等待 Enter。逐条运行，检查完成情况后再输入下一条；不要把整个块一次性粘贴执行。**

同一次测试保持相同 `--output`，这里为 `runs\manual_001`。

第 1 步，只去初始位：

```bat
python -m xarm_grasp --execute --step 1 --output runs\manual_001
```

第 2 步，只去小票位并 OCR，结束后停在小票位：

```bat
python -m xarm_grasp --execute --step 2 --output runs\manual_001
```

第 3 步，只返回初始位：

```bat
python -m xarm_grasp --execute --step 3 --output runs\manual_001
```

第 4 步，只去商品观察位并完成定位与计划，读取同目录的 OCR 结果：

```bat
python -m xarm_grasp --execute --step 4 --output runs\manual_001
```

如果只想测试某商品，可以用这一条替代上一条；它不依赖第 2 步：

```bat
python -m xarm_grasp --execute --step 4 --item "雪碧" --output runs\manual_001
```

第 5 步，只完成抓取、抬升、退出：

```bat
python -m xarm_grasp --execute --step 5 --output runs\manual_001
```

第 6 步，只返回初始位，夹爪不主动松开：

```bat
python -m xarm_grasp --execute --step 6 --output runs\manual_001
```

单步执行不会自动补前置运动。例如单独执行第 2 或第 4 步会从当前位置直接去目标观察位，不会先绕行 initial_pose。第 1、3、6 步的运动目标相同，且不需要打开相机。

### 7.3 第 4、5 步的结果衔接

第 4 步成功后在 `result.json` 写入：

- `status: "step_4_grasp_planned"`
- `item`：商品名称。
- `tcp_point_mm`：检测表面点的 TCP 坐标。
- `tcp_at_capture`：拍摄时的 TCP 位姿。
- `tool_axis_plan`：当时生成的轨迹。

第 5 步读取该结果，用当前配置的深入补偿和工具参数**重新生成计划**，核对当前 TCP 与拍摄 TCP，再预检各个目标，随后抓取。它不打开相机，不重新识别，也不会移动回观察位。第 5 步不能使用 `--item`，商品来自结果文件。

使用限制：

1. 同一轮第 2 / 4 / 5 步应保持同一输出目录。第 4 步通过 `--item` 指定时可不依赖 OCR。
2. 第 4、5 步之间保持商品、机械臂、TCP 和标定 / 抓取配置不变。TCP 位姿匹配检查无法确认商品是否移动，也不验证配置是否改变。
3. 成功抓取后状态改变，不能直接再次执行第 5 步；应重新完成第 4 步。
4. 指定第 5 步仅接受成功的 `--step 4` 输出；连续 Enter 模式或 `--plan-only` 输出不能直接作为它的输入。
5. 输出文件不是可靠的断点恢复记录。失败可能留下旧结果或部分证据。每轮测试换一个目录，执行失败后先确认实际状态，禁止仅凭旧 JSON 重试抓取。

### 7.4 第一处没有目标时，自动换第二个观察位

保留已有 `grasp_observation` 作为第一观察位，在 config.json 的同级字段 `grasp_observation_fallback` 中填写备用位。目前该字段为 `null`，表示尚未配置 / 禁用备用观察位；不会使用猜测坐标运动。

备用位与普通观察位格式相同：`{"type": "joint", "values": [J1,J2,J3,J4,J5,J6]}`，其中 J1–J6 必须替换为 Studio 读取的实测关节角（度），这些字母不是有效 JSON 数值。也支持 `type: "tcp"` 及 `[x,y,z,roll,pitch,yaw]`（毫米 / 度）。移动路径需在现场确认。

配置后，第 4 步内部执行：

1. 到第一观察位，拍摄并检测目标商品。
2. 找到唯一目标：在第一位生成抓取计划，不去第二位。
3. 目标类别检测数为 0：从第一观察位直接移动到备用观察位，重新拍摄、识别，不经过 `initial_pose`。
4. 第二位找到唯一目标：使用第二位拍摄时的真实 TCP 位姿、当前相机帧和深度生成新计划。
5. 两处都是 0：停止，不开爪、不执行抓取。不会循环重试。

只有目标数为 0 触发切换。多个同类目标、深度无效、相机异常、机器人移动 / 控制器错误、规划或逆解失败仍会停止，不会继续尝试另一个位置。

这个逻辑同时适用于 `--step-test`、`--step 4`、自动执行和 `--plan-only`。总流程仍是六步；Enter 模式确认第 4 步后，备用位切换自动执行，不额外等待一次 Enter。独立 `vision.yolo` 不控制机械臂，因此不切换位姿。

识别完成后，`--step 5` 从成功的那个观察位开始抓取。不要先手动把机械臂移回第一观察位。结果新增 `observation_used`（primary / fallback）、`observation_index` 和 `observation_attempts`，记录每次位姿、实际 TCP 和证据目录。

各位置的证据分别保存在以下目录，第一处的失败图像不会被第二处覆盖：

```text
runs/manual_001/
├── result.json                      当前结果，供第 5 步使用
├── color.png / detection.jpg / ...   最近一次尝试的图像和深度
└── observations/
    └── <本次搜索编号>/
        ├── 01_primary/              第一观察位的图像、深度、结果
        └── 02_fallback/             第二观察位证据，仅在尝试后产生
```

每次第 4 步创建不同搜索编号以保留各次证据；根目录仍保持原命令和离线回放的兼容格式。需要回放第一处失败图像时，将 `--frame-dir` 指向对应的 `01_primary` 子目录。

<a id="sec-cli"></a>
## 8. 自动执行、计划模式与参数总表

### 8.1 全自动完整小票流程

完成分步验收后：

```bat
python -m xarm_grasp --execute --output runs\order_001
```

一次性执行六步，中途不等待 Enter。

直接选择商品，省去小票位和 OCR：

```bat
python -m xarm_grasp --item "雪碧" --execute --output runs\sprite_001
```

使用已有小票图片选商品，再移动到商品位抓取：

```bat
python -m xarm_grasp --receipt-image "C:\data\receipt.jpg" --execute --output runs\order_image_001
```

### 8.2 计划模式会真实移动

```bat
python -m xarm_grasp --item "雪碧" --execute --plan-only --output runs\sprite_plan_001
```

它会去 initial_pose、去商品观察位、拍照并计算 / 预检抓取计划，然后返回 initial_pose。不会执行开闭夹爪和 Y/X/Z 抓取动作。省略 `--item` 时仍包含小票 OCR 和中转运动。

它不是纯离线模拟；如果想停在商品观察位检查计划，应使用 `--step 4`。

### 8.3 主入口参数

| 参数 | 作用 / 约束 |
|---|---|
| `--config PATH` | 默认是程序所在 shop-workspace 下的 config.json |
| `--output DIR` | 默认 `runs/latest`；建议每轮使用不同目录 |
| `--execute` | 允许进入真实运动分支 |
| `--item NAME` | 标准名称或别名，绕过 OCR；单步模式只允许第 4 步使用 |
| `--receipt-image PATH` | 已有小票图片，与 --item 互斥 |
| `--plan-only` | 真实移动并规划，跳过抓取；必须配 --execute |
| `--step-test` | 完整六步，执行前逐步 Enter 确认 |
| `--step N` | 仅执行 1–6 中指定一步；必须配 --execute |
| `--help` | 查看参数帮助，不连接硬件 |

`--step` 和 `--step-test` 互斥，两者均不能配 `--plan-only` 或 `--receipt-image`。

<a id="sec-output"></a>
## 9. 输出文件与结果检查

成功进行 OCR 和商品定位后，典型目录如下：

```text
runs/manual_001/
├── receipt/
│   ├── receipt.jpg          小票画面
│   ├── receipt_ocr.md       OCR 文字
│   ├── receipt_ocr.json     OCR 原始响应
│   └── receipt_result.json 商品映射结果或拒绝原因
├── detection.jpg           标注检测框的商品画面
├── color.png               未标注的原始彩色帧
├── depth_mm.npy            对齐后的深度，单位毫米
├── frame.json              内参、畸变、相机序列号
└── result.json             商品坐标、抓取计划及阶段状态
```

单独运行某一步只产生与该步骤相关的文件。OCR 网络失败时不一定保存小票证据；失败目录里的文件也可能是之前运行留下的。

**商品识别失败也会保存本次拍摄证据。** 第 4 步、独立 YOLO 和离线回放在执行推理前先保存 `color.png`、`depth_mm.npy`、`frame.json`，并将 `result.json` 标为 `recognition_pending`，使旧抓取计划失效。未检测到目标、多个同类目标、深度无效或推理异常时，结果改为 `recognition_failed`，包含 `error`、目标 `class_id`、`confidence_threshold` 和记录时间。只要推理已返回，还保存 `detections`（目标类别的框、类别 ID / 模型标签、置信度）、`target_count` 与 `inference_classes`。

推理时明确传入 `classes=[目标 class_id]`，例如芬达为 `[2]`，只保留和绘制目标商品类别。失败时 `detection.jpg` 标注失败提示；完全没有目标框时也会保存画面。`color.png` 始终是未标注原图。检测列表仅包含本次置信度阈值以上的目标类别输出，低于阈值的预测不在其中。保存路径也会打印到终端。类别过滤不会把模型判成其他类别的物体重新分类；若原图里的芬达被模型判为可乐，需要核对训练类别映射与权重，不能把两个商品都配置为同一 class_id 来绕过。

这些文件可直接交给第 11 节的 `--frame-dir` 离线回放。相同输出目录保留最新一轮的这些文件，若要保留历次失败，请每次使用不同的 `--output`。未成功取得 RGB-D 帧时没有图像可保存；本功能不改变 OCR 图像保存时机。

检查 result.json：

| 字段 | 含义 / 检查重点 |
|---|---|
| item / class_id | 是否对应小票和模型类别 |
| detection.bbox / uv / confidence | 检测框、中心像素、置信度 |
| camera_point_mm | 相机系的可见表面点 |
| tcp_point_mm | TCP 系的同一个表面点，尚未加深入补偿 |
| tcp_offset_used | 本次转换使用的 TCP offset |
| tcp_at_capture | 拍摄时基座系 TCP 位姿，仅规划流程生成 |
| base_point_mm | 可见表面点转换到基座系的位置 |
| tool_axis_plan.steps | 工具系增量和各段基座系目标位姿 |
| status | 最近一次写入的程序阶段，不是实时控制器状态 |

常见状态：

| status | 意义 |
|---|---|
| receipt_resolved_no_motion | 只做 OCR 成功 |
| step_2_receipt_resolved | 指定第 2 步成功 |
| vision_only | 已定位；还没有生成抓取轨迹 |
| recognition_pending | 本次帧开始处理，旧抓取计划已失效 |
| recognition_failed | 本次识别 / 深度定位失败，查看 error 和 detections |
| planned / planned_no_grasp | 已生成并预检计划；是否后续执行需看模式 |
| step_4_grasp_planned | 指定第 4 步成功，供指定第 5 步读取 |
| holding_object_after_pullout | 抓取 / 抬升 / 退出完成 |
| planned_no_grasp_returned_to_initial | 计划模式完成并返回初始位 |
| holding_object_at_initial_pose | 程序已执行最终返回 |

`--step 6` 只做返回运动，不调用夹持验证；如果已有 result.json，它会更新最终状态。所以最终状态不能单独证明商品仍被夹住，应检查现场实际夹持。

<a id="sec-code"></a>
## 10. 代码结构与运行原理

### 10.1 目录结构

```text
xarm-shop-demo/
├── xArm-Python-SDK/         可选源码 SDK；缺失时使用环境安装包
├── pyorbbecsdk/             可选官方源码；运行依赖已安装的 Python 绑定
└── shop-workspace/
    ├── README.md           本手册
    ├── requirements.txt    Python 依赖范围
    ├── config.json         现场参数
    ├── models/products.pt  商品权重，需要另行准备
    ├── xarm_grasp/
    │   ├── __main__.py     命令解析、六步流程、分步入口
    │   ├── config.py       配置读写与校验
    │   ├── robot.py        xArm SDK 封装与夹爪操作
    │   ├── motion.py       抓取计划和执行顺序
    │   └── coordinates.py  深度和坐标变换
    ├── vision/
    │   ├── __main__.py     等价于 vision.yolo 的入口
    │   ├── camera.py       Gemini RGB-D 采集与对齐
    │   ├── ocr.py          小票 OCR 和商品匹配
    │   ├── yolo.py         商品定位、结果保存与回放
    │   ├── check_camera.py 相机诊断
    │   ├── camera_preview.py 实时预览
    │   └── tests/          视觉组件测试
    ├── camera_calibration/
    │   ├── __main__.py     标定命令入口
    │   ├── calibrate.py    采样、求解、独立验证
    │   ├── calibration_probe.py 多帧诊断
    │   ├── tests/          标定测试
    │   └── data/           生成的标定数据
    ├── tests/              综合接口与运动模拟测试
    └── runs/               生成的运行证据
```

各包还有 `__init__.py`，用于包声明或公共常量。使用 `python -m 包名` / `python -m 包名.模块名`，由 Python 建立正确的包上下文。直接运行 `python xarm_grasp\__main__.py` 会遇到相对导入问题。

### 10.2 主入口怎样调度

`__main__.py:main()` 解析参数，交给 `run(args)`。run 先读配置，再根据模式分支：

- 无 execute：只做 OCR 或指定商品的相机定位。
- 有 step：交给 `run_isolated_step()`，完成指定阶段后退出。
- 其他 execute 模式：校验配置，创建相机 / Robot 会话，按整条链路执行。
- step-test：在主流程各阶段前调用 `confirm_step()`。
- plan-only：让 `grasp_from_observation()` 只规划，随后由主流程返回初始位。

`grasp_from_observation()` 负责“去商品位 → 取帧定位 → 坐标变换 → 生成计划 → 全部目标预检”。正常模式继续调用 `execute_grasp_plan()`，计划模式则返回报告。

相机和 Robot 都通过 `with` 管理连接。正常退出会关闭相机、断开控制器连接；运动会话中抛异常时，Robot 清理逻辑会尝试向控制器发送 `set_state(4)` 后断开。

### 10.3 从小票得到商品名称

`vision/ocr.py`：

1. 小票图像编码为 JPEG / Base64，携带 Bearer 密钥发送到配置的 URL。
2. 提取响应 `md_results` 中的文字。
3. 文字规范化后，通过标准名和 aliases 查找配置商品。
4. 恰好一种商品时返回 `selected_item`。
5. 主流程用该名称取得 model、class_id、夹爪位置和深入补偿。

OCR 解决“抓什么”；真正的商品空间位置来自之后在货架位拍摄的另一帧。

### 10.4 从商品像素得到三维点

`GeminiCamera.capture()` 同步获取彩色和深度，经 SDK 对齐到彩色图，读取彩色内参和畸变参数；深度原始数值乘 depth scale 后统一为毫米。

`vision/yolo.py:detect()` 对图像推理，筛选指定 class_id 的框。候选数量必须恰好为 1，取框中心 `(u,v)`。随后 `coordinates.py` 在中心邻域筛选有效深度并取中位数。

忽略畸变时，反投影的直观公式为：

```text
X_camera = (u - cx) × Z / fx
Y_camera = (v - cy) × Z / fy
Z_camera = 邻域深度中位数
```

`fx, fy, cx, cy` 来自相机内参。存在非零畸变时，代码先用 OpenCV 修正像素射线。

检测框中心不一定是最佳夹持点：中心落在背景、物体边缘或透明区域时，即使 YOLO 检测正确，深度也可能错误或被拒绝。当前实现没有分割、表面法向或六自由度抓取姿态估计。

### 10.5 相机系、法兰系、TCP 系与基座系

记 `T_A_B` 为“把 B 系坐标转换到 A 系”的 4×4 齐次变换，则：

```text
P_tcp  = inverse(T_flange_tcp) × T_flange_camera × P_camera
P_base = T_base_tcp_at_capture × P_tcp
```

其中点按 `[x,y,z,1]` 参与矩阵运算：

- `T_flange_camera` 来自手眼标定。
- `T_flange_tcp` 来自 TCP offset，包含平移和旋转。
- `T_base_tcp_at_capture` 来自拍摄时读取的真实 TCP 位姿，不是关节角。
- 位姿欧拉角按 `Rz(yaw) × Ry(pitch) × Rx(roll)` 构造旋转矩阵，角度单位度。

计算相机到 TCP 的点不需要机器人当前基座位姿；转换到基座系和预检时才需要它。实际抓取流程读取控制器 TCP offset，独立视觉函数使用配置值。

### 10.6 从三维点生成动作

假设 TCP 表面点是 `[x,y,z]`，深入补偿为 `d`，抬升量为 `h`。在当前轴配置下：

| 顺序 | 计划名 / 操作 | 工具坐标系增量 |
|---|---|---|
| 准备 | reset / activate / open | 夹爪动作 |
| 1 | align_lateral | `[0,y,0]` |
| 2 | align_vertical | `[x,0,0]` |
| 3 | approach_depth | `[0,0,z+d]` |
| 夹持 | close / verify_grasp | 夹爪闭合并检查接触状态 |
| 4 | lift | `[-h,0,0]` |
| 5 | pullout | `[0,0,-(z+d)]` |

抬升和退出后各检查一次夹持。当前夹持判断要求 `gFLT=0`、`gOBJ=2`、`gGTO=1`，不是通过相机确认物体已经抓到。

规划器用拍摄位姿的旋转，把这些增量累计成基座系目标，检查工作区并调用控制器逆解预检。实际动作通过 `set_tool_position` 下发零旋转增量，逐段检查位置和朝向误差。退出只撤销深入量，不撤销左右对齐和抬升，因此第 5 步终点不等于第 4 步拍摄位。

<a id="sec-api"></a>
## 11. Python 函数调用与离线回放

以下 Python 片段在已激活环境、以 shop-workspace 为工作目录的解释器或脚本中运行。它们不是 CMD 命令。

### 11.1 一次调用获得坐标

```python
from vision.yolo import get_product_position, locate_product

xyz_tcp = get_product_position("雪碧", coordinate_system="tcp")
print(xyz_tcp)  # NumPy [x,y,z]，毫米

report = locate_product("雪碧", output="runs/api_001")
print(report["camera_point_mm"], report["tcp_point_mm"])
print(report["detection"])
```

每次函数调用分别加载模型、打开相机、采集后关闭；不连接机器人。`get_product_position` 的 coordinate_system 只支持 camera / tcp。两套坐标都需要时，用一次 locate_product 即可。

### 11.2 复用相机与模型

```python
from vision.camera import GeminiCamera
from vision.yolo import load_model, recognize_product
from xarm_grasp.config import DEFAULT_CONFIG, load, resolve_entry

config = load(DEFAULT_CONFIG)
name, product = resolve_entry(config, "雪碧")
model = load_model(DEFAULT_CONFIG, product)

with GeminiCamera(config["camera"]) as camera:
    for _ in range(5):
        report = recognize_product(camera.capture(), model, product, config, name)
        print(report["tcp_point_mm"])
```

`recognize_product()` 只处理传入的 frame，不负责采集、运动或文件写入；加 `include_tcp=False` 只算相机系坐标。

### 11.3 离线回放已有 RGB-D

保存了 `color.png`、`depth_mm.npy`、`frame.json` 后：

```bat
python -m vision.yolo --item "雪碧" --frame-dir runs\sprite_vision_001 --output runs\sprite_replay_001
```

无需相机或机器人，但仍需模型。回放读取当前配置的手眼矩阵 / TCP offset，适合比较配置调整前后的计算。输出只代表保存时的场景，不可当成当前货架的实时抓取目标。

### 11.4 OCR 与坐标函数

```python
from vision.ocr import recognize_receipt_file, select_receipt_product
from xarm_grasp.config import DEFAULT_CONFIG, load
from xarm_grasp.coordinates import camera_to_tcp

config = load(DEFAULT_CONFIG)
receipt = recognize_receipt_file(r"C:\data\receipt.jpg", config)
print(receipt["selected_item"], receipt["ocr_text"])

# 只有文字时，直接匹配，不联网。
selected = select_receipt_product("雪碧 1瓶", config)
print(selected["name"])

point_tcp = camera_to_tcp(
    [0, 0, 500],
    config["robot"]["tcp_offset"],
    config["calibration"]["T_flange_camera"],
)
print(point_tcp)
```

已有 BGR 图像可用 `recognize_receipt(bgr, config)`；已有 frame 字典可用 `recognize_receipt_frame(frame, config)`。注意返回结构：OCR 报告用 selected_item，纯文字匹配结果用 name。

<a id="sec-troubleshooting"></a>
## 12. 故障排查、停止与恢复

| 现象 / 报错 | 检查与处理 |
|---|---|
| conda 不是内部或外部命令 | 打开 Anaconda Prompt；确认已安装 Conda |
| No module named xarm_grasp / vision | 进入 shop-workspace，再使用 python -m；不要在包内部目录启动 |
| attempted relative import | 不要直接运行 __main__.py，使用 python -m xarm_grasp |
| No module named xarm | 在当前 xarm 环境安装 xarm-python-sdk；核对 python 路径 |
| pyorbbecsdk native module is unavailable / DLL load failed | 检查 pyorbbecsdk2 安装、Python 位数、原生依赖和官方 Windows 环境设置；报错中的旧 D: 路径不代表必须放在那里 |
| No Orbbec camera detected / serial not found | 检查 USB、设备占用、供电和 camera.serial |
| No synchronized D2C-aligned RGB-D frames | 关闭其他相机程序，检查流配置、USB 连接及环境设置 |
| OCR API key environment variable is not set | 同一终端设置 ZHIPUAI_API_KEY；CMD 用 set，PowerShell 用 $env: |
| api_key_env must be an environment variable name | config 中写变量名 ZHIPUAI_API_KEY，不能写密钥值 |
| Receipt OCR request failed / timed out | 独立图片测试，检查联网、服务权限 / 额度、密钥和超时；HTTP 401/403 优先检查鉴权 |
| Receipt does not contain / multiple configured product types | 查看 receipt_ocr.md，检查文字、别名和小票商品种类 |
| Trained YOLO weights required | 准备商品模型；检查路径相对于配置文件目录 |
| class_id does not exist in model.names | 核对训练模型类别与 products 配置 |
| Expected exactly one target, detected 0 / 2 | 无目标或存在多个同类框；检查视野、类别、置信度和摆放 |
| Insufficient valid depth / Unstable depth | 检查框中心是否落在边缘、背景或无效深度区域；查看深度图 |
| motion_enabled is false / Validated hand-eye calibration required | 完成现场配置和标定验收，再设置对应开关 |
| Controller TCP offset differs from config | 对照 Studio 和配置，修正实际 TCP 定义 |
| Robot requires operator attention | 在 Studio 查看错误 / 警告和停止状态，处理后重新启动 |
| TCP outside workspace / TCP limit / IK | 核对坐标、工作区、姿态和可达性，不能仅靠扩大边界解决 |
| lift direction is not sufficiently upward | 商品观察位姿与工具抬升轴方向不一致 |
| No confirmed object contact while closing | 检查是否空抓、闭爪位置是否足够、目标位置和夹爪状态 |
| step 4 requires --item ... | 先做同目录第 2 步，或给第 4 步明确 --item |
| step 5 requires ... isolated step 4 | 使用同目录成功的 --step 4 结果；其他模式的计划不能接续 |
| Robot is no longer at the step-4 capture pose | 确认现场后重新执行第 4 步；不要改 JSON 来绕过检查 |

### 停止后怎样恢复

`q` 只在 Enter 确认提示处生效。程序捕获异常或 Ctrl+C 后会退出；如果已进入运动会话，清理时尝试发送停止指令。Ctrl+C 和软件停止可能受 SDK 等待、网络或进程状态影响，不能当成硬件急停。需要立即停止实际动作时使用现场急停。

停止 / 报错不会自动返回 initial_pose，也不会自动松爪。先检查控制器状态、机械臂位置和商品是否夹住，再决定下一条动作：

- 还没抓取：检查配置和视野后重新开始相应步骤。
- 第 5 步部分执行失败：不要直接重发第 5 步，也不要盲目执行第 6 步；先在现场确认退出路径。
- 只有错误码和警告码均为 0 的 `state=4` 软件停止会在下一次真实执行时自动恢复；使用现场急停、存在错误 / 警告或处于其他安全状态时，仍须在 Studio 中检查和处理。程序不会替你清除故障。
- 重新做定位与抓取时使用新的输出目录，防止误读旧计划。
- 不能在商品仍被夹持时直接开始新一轮抓取：第 5 步会重新 reset / 开爪，需先处理上一件商品。

<a id="sec-development"></a>
## 13. 修改代码与验证

### 13.1 想改功能，去哪里改

| 需求 | 位置 |
|---|---|
| 位姿、速度、商品类别、深入补偿 | 优先修改 config.json |
| 六步顺序、命令行参数、单步执行 | xarm_grasp/__main__.py |
| 夹爪与分轴动作顺序 | xarm_grasp/motion.py: execute_grasp_plan |
| 对齐、深入、抬升和退出的计划 | xarm_grasp/motion.py: make_tool_axis_plan_from_tcp |
| SDK 调用、夹爪速度 / 力度、到位检查 | xarm_grasp/robot.py |
| 框筛选、定位像素、模型加载 | vision/yolo.py |
| 深度取样、反投影、坐标变换 | xarm_grasp/coordinates.py |
| 小票服务调用、文本提取、别名匹配 | vision/ocr.py |
| 相机流、对齐、内参 | vision/camera.py |

修改运动顺序时，同步修改计划和执行顺序，确保预检的目标与实际路径一致。

### 13.2 查看帮助与离线测试

```bat
python -m xarm_grasp --help
python -m vision.yolo --help
python -m vision.ocr --help
python -m camera_calibration --help
python -m unittest discover -s tests -v
```

综合测试会引入视觉和标定测试，使用模拟机器人、模拟检测和模拟 OCR 响应，不连接真实设备或 OCR 服务。只测试某一组件：

```bat
python -m unittest discover -s vision/tests -v
python -m unittest discover -s camera_calibration/tests -v
```

测试通过说明覆盖到的代码逻辑符合预期，不表示已验证当前设备的标定精度、路径无碰撞或抓取成功率。上线前应完成本手册中的相机、OCR、定位和六步实机验证。

### 13.3 日常启动速查

环境和现场参数已经配置好后，在 CMD / Anaconda Prompt 逐条运行：

```bat
conda activate xarm
cd /d C:\Users\LENOVO\Desktop\xarm-shop-demo\shop-workspace
set "ZHIPUAI_API_KEY=替换为你的真实API密钥"
python -m xarm_grasp --execute --step-test --output runs\step_test_002
```

只测试某个阶段时，把最后一条替换为 `--step 1` 至 `--step 6` 对应命令，依赖关系见第 7 节。速度在 robot 配置中修改，新的运行才会读取修改值。
