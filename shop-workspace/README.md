# 小票驱动的 xArm 商品抓取

新电脑部署、硬件联调、标定和首次抓取验收请阅读 [HANDOFF_GUIDE_ZH.md](HANDOFF_GUIDE_ZH.md)。新增 OCR/YOLO 的原始资料保留在 `vision/`，正式运行入口统一为 `python -m xarm_grasp`。

硬件为 xArm 6、腕部 Orbbec Gemini 336 和末端 Robotiq 2F-85，控制器 IP 为 `192.168.1.209`。

默认真实流程：机械臂先移动到安全的 `initial_pose` → 移动到小票观察位 → 腕部相机拍摄桌面小票 → GLM-OCR 读取文字 → 严格匹配 `config.json` 中一个商品 → 经 `initial_pose` 返回 → 移动到所有商品共用的抓取预备位 → YOLO 只识别对应类别 → RGB-D 反投影和手眼坐标转换 → 保持 TCP 朝向不变，依次沿工具 Y 左右对齐、工具 X 上下对齐、工具 Z 深入 → 闭合夹爪 → 工具 X 轻抬 → 工具 Z 退出。

若小票没有匹配商品或同时出现多个商品种类，程序停止。当前没有放置动作，所以一张小票暂时只允许一个商品种类，抓取结束后保持夹持。

## 1. 安装

项目统一使用名为 `xarm` 的 Conda 环境：

```powershell
cd D:\Robotics\xarm-shop-demo\shop-workspace
conda create -n xarm python=3.10 -y  # 已有环境时跳过
conda activate xarm
python -m pip install -r requirements.txt
```

`pyorbbecsdk2` 安装后使用 `import pyorbbecsdk`。xArm SDK 从相邻的 `../xArm-Python-SDK` 加载。Windows 首次使用 Gemini 336 时，以管理员权限运行官方 UVC 环境脚本，随后拔插相机：

```powershell
python ..\pyorbbecsdk\scripts\env_setup\setup_env.py
python -m xarm_grasp.check_camera
```

实时查看彩色相机画面（`Q`/`Esc` 退出，`S` 保存当前帧）：

```powershell
python -m xarm_grasp.camera_preview
```

同时查看与彩色画面对齐的深度图：

```powershell
python -m xarm_grasp.camera_preview --show-depth
```

## 2. 小票 OCR 配置

智谱 API Key 只放在当前终端环境变量中，不要写进代码或配置：

```powershell
$env:ZHIPUAI_API_KEY = '你的新API Key'
```

`config.json.receipt` 包含：

- `observation`：腕部相机能清楚看到桌面小票时的六轴关节位或 TCP 位姿。
- `api_key_env`：API Key 环境变量名称，默认 `ZHIPUAI_API_KEY`。
- `url`、`model`、`timeout_s`：GLM-OCR 请求配置。
- `jpeg_quality`：上传小票 JPEG 的质量。

没有 `--execute` 时不会移动机械臂。直接拍当前相机画面并只做 OCR/商品解析：

```powershell
python -m xarm_grasp --output runs/receipt_test
```

用已有图片测试 OCR 和商品映射，不打开相机：

```powershell
python -m xarm_grasp --receipt-image D:\data\receipt.jpg --output runs/receipt_file_test
```

输出位于 `runs/.../receipt/`：`receipt.jpg`、`receipt_ocr.md`、`receipt_ocr.json` 和 `receipt_result.json`。

商品匹配只使用 `config.json.products` 的标准名称和 `aliases`。不会用模糊猜测替代未知商品。当前只支持一张票中一个商品种类；多商品小票会明确报错。

## 3. 商品、TCP 和观察位姿

没有测量的参数保持 `null`，程序不会使用猜测值运动。

- `initial_pose`：小票位与商品观察位之间共同经过的安全关节位姿。
- `receipt.observation`：看小票的预定位姿。
- `grasp_observation`：六种商品共用的货架抓取预备位姿；推荐关节值。
- `robot.tcp_offset`：法兰到实际夹持 TCP 的 `[x,y,z,roll,pitch,yaw]`，必须与控制器/Studio 当前 TCP 一致。
- `grasp_depth_offset_mm`：从相机看到的瓶子前表面，沿工具 Z 到夹持中心的附加深入距离。
- `open_position` / `close_position`：Robotiq 原始位置 0–255，不是毫米。
- `workspace_mm`：现场测量的基座坐标安全范围。

手动在 Studio 中把机械臂移动到小票位或商品位并停稳，然后读取配置片段：

```powershell
python -m xarm_grasp.teach --type joint
# 或
python -m xarm_grasp.teach --type tcp
```

工具坐标已确认为：Y+ 向左（Y- 向右），X- 向上（X+ 向下），Z+ 向前深入（Z- 退出）。配置中的 `approach_axis_sign: 1` 强制只沿 Z+ 接近商品，`lift_axis_sign: -1` 强制夹取后沿 X- 抬升；姿态不符合这两个方向时程序停止。TCP 姿态在分轴动作中保持不变。

## 4. 眼在手上标定

固定棋盘格，测量真实格边，手动移动机械臂采集 15–25 个具有位置和多轴旋转变化的位姿。示例为 9×6 内角点、25 mm 格边，实际命令必须换成实测尺寸：

```powershell
python -m xarm_grasp.calibrate capture --cols 9 --rows 6 --square-mm 25
python -m xarm_grasp.calibrate solve
```

把结果的 `camera_serial` 和 `T_flange_camera` 写入 `config.json.calibration`。使用未参加求解的固定点验证精度后，才设置 `calibration.validated: true`。

完整校准顺序、命令和验收标准见 [CALIBRATION_GUIDE_ZH.md](CALIBRATION_GUIDE_ZH.md)。

## 5. 调试和执行入口

绕过小票，只在相机当前视野检测指定商品；不连接机器人：

```powershell
python -m xarm_grasp --item 雪碧 --output runs/sprite_vision
```

默认小票驱动的完整真实流程：

```powershell
python -m xarm_grasp --execute --output runs/order_001
```

使用已有小票图片，然后真实移动到共享抓取预备位抓取解析出的商品：

```powershell
python -m xarm_grasp --receipt-image D:\data\receipt.jpg --execute --output runs/order_002
```

绕过小票，直接抓指定商品：

```powershell
python -m xarm_grasp --item 雪碧 --execute --output runs/sprite_001
```

首次联调先运行计划模式。它会真实移动经过安全位和观察位，完成识别与目标点预检，并在夹爪动作和 Y/X/Z 抓取运动前停止：

```powershell
python -m xarm_grasp --item 雪碧 --execute --plan-only --output runs/sprite_plan
```

真实执行要求 `motion_enabled: true`，且 TCP、手眼标定、工作范围、小票位、共享抓取预备位和抓取深度均已实测。程序只校验目标 TCP 点、工作范围和逆解，不包含连杆、腕部相机、夹爪和货架的完整碰撞规划。

## 6. 离线测试

```powershell
python -m unittest discover -s tests -v
```

测试不连接相机和机械臂，覆盖坐标转换、工具轴计划、机械臂模拟、安全门禁以及小票文字到商品配置的严格映射。OCR 网络请求、RGB-D 实机、手眼精度和真实抓取仍需接入硬件后验证。



