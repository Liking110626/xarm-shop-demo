# 模块划分与独立测试

在 D:\Robotics\xarm-shop-demo\shop-workspace 目录中运行命令，使用已有的 xarm Conda 环境：

~~~bat
conda activate xarm
cd /d D:\Robotics\xarm-shop-demo\shop-workspace
~~~

## 1. 每个脚本负责什么

| 文件 | 职责 | 常用函数 / 入口 |
|---|---|---|
| __main__.py | 命令行入口及完整流程：安全位 → OCR → 安全位 → 商品识别 → 抓取 → 安全位 | main()、run(args) |
| motion.py | 根据 TCP 目标点生成分轴计划，按顺序操作夹爪和机械臂 | make_tool_axis_plan_from_tcp()、execute_grasp_plan()、grasp_from_observation() |
| robot.py | 封装 xArm SDK：使能、关节运动、TCP 相对移动、夹爪和控制器检查 | Robot.observe()、Robot.move_tool() |
| coordinates.py | 深度取样、像素反投影、相机系 → TCP 系，以及基座点计算 | pixel_to_camera()、camera_to_tcp()、tcp_to_base() |
| vision/yolo.py | 加载商品模型、筛选指定类别、返回 3D 坐标、保存与回放 RGB-D | get_product_position()、locate_product()、recognize_product() |
| vision/ocr.py | OCR 网络请求、小票文字解析、映射到配置中的商品 | recognize_receipt()、recognize_receipt_file()、select_receipt_product() |
| config.py | 配置读写、商品名称/别名解析、运动参数校验 | load()、resolve_entry() |
| vision/camera.py | Gemini 相机采集、彩色与深度对齐、提供内参 | GeminiCamera.capture() |
| vision/check_camera.py | 检查型号、序列号和取帧 | python -m vision.check_camera |
| vision/camera_preview.py | 实时彩色/深度预览 | python -m vision.camera_preview --show-depth |
| camera_calibration/calibrate.py | 标定采集、求解、独立样本验证 | python -m camera_calibration --help |
| camera_calibration/calibration_probe.py | 棋盘格检测、机器人静止性和重复观测诊断 | python -m camera_calibration.calibration_probe --help |
| __init__.py | Python 包说明 | 无需运行 |

在 shop-workspace 中使用 python -m vision.模块名 运行视觉工具，使用 python -m xarm_grasp 运行完整抓取流程；不要直接运行 python vision/yolo.py。

## 2. 一行函数直接获得 3D 坐标

~~~python
from vision.yolo import get_product_position

xyz = get_product_position("雪碧", coordinate_system="tcp")
x, y, z = xyz
print("TCP 坐标（毫米）:", x, y, z)
~~~

这会加载模型、打开相机、识别一帧、关闭相机，返回 NumPy 数组 [x, y, z]。不会连接机器人。默认配置路径由包位置确定，也可以传入 config_path 参数。

只需要相机系坐标时：

~~~python
xyz_camera = get_product_position("雪碧", coordinate_system="camera")
~~~

如果一次需要两套坐标和检测框，用下面的接口，只采集一次：

~~~python
from vision.yolo import locate_product

result = locate_product("雪碧", output="runs/sprite_test")
print(result["camera_point_mm"])
print(result["tcp_point_mm"])
print(result["detection"]["bbox"])
print(result["detection"]["confidence"])
~~~

output 可省略，省略时只返回数据、不保存图片。

**坐标含义：**

- 所有 XYZ、深度和距离单位都是毫米；位姿的后三项角度单位是度。
- camera_point_mm 是彩色相机坐标系中的点，深度已经对齐到彩色图。
- tcp_point_mm 是相对于配置 TCP 原点、沿 TCP 三个轴表达的点；在你的抓取姿态下，Y+ 向左、X- 向上、Z+ 向前。
- 返回的是 bbox 中心像素对应的可见表面点，还没有加 grasp_depth_offset_mm；该补偿在运动规划时仅加一次。
- TCP 转换使用 config.robot.tcp_offset 和 config.calibration.T_flange_camera，不需要当前机械臂位姿。相机必须与标定序列号一致。
- 独立识别允许输出尚未验收的标定计算结果，并在结果中写入 calibration_validated。实际运动仍需要通过原有的标定、TCP、工作范围等检查。

## 3. 独立运行 YOLO

~~~bat
python -m vision.yolo --item 雪碧 --output runs/sprite_test
~~~

终端打印相机系与 TCP 系坐标。只验证 YOLO 和深度，不使用手眼矩阵：

~~~bat
python -m vision.yolo --item 雪碧 --camera-only --output runs/sprite_camera
~~~

没有目标、出现多个同类别目标、深度无效或不稳定时，函数抛出异常。仅有 RGB 图片不能得到真实的 3D 点，需要同一帧对齐后的深度和相机内参。

保存结果包括：

- result.json：识别及坐标结果。
- detection.jpg：绘制检测框的图片。
- color.png：未绘制检测框的原始彩色帧。
- depth_mm.npy：同一帧对齐的深度，单位毫米。
- frame.json：内参、畸变参数、相机序列号。

有了这一组文件，断开相机后也能重新运行识别和转换：

~~~bat
python -m vision.yolo --item 雪碧 --frame-dir runs/sprite_test --output runs/sprite_replay
~~~

回放使用当前配置中的手眼矩阵和 TCP 偏移，适合对比参数修改后的结果；结果对应保存图像时看到的场景。

重复识别时，在外面打开相机、加载模型，再调用函数，避免每次重新初始化：

~~~python
from vision.camera import GeminiCamera
from xarm_grasp.config import DEFAULT_CONFIG, load, resolve_entry
from vision.yolo import load_model, recognize_product

config = load(DEFAULT_CONFIG)
name, product = resolve_entry(config, "雪碧")
model = load_model(DEFAULT_CONFIG, product)

with GeminiCamera(config["camera"]) as camera:
    for _ in range(5):
        frame = camera.capture()
        result = recognize_product(frame, model, product, config, name)
        print(result["tcp_point_mm"])
~~~

recognize_product() 只处理传入的图像数据，不负责采集或运动。传入 include_tcp=False 时仅输出相机系坐标。离线图像也可以用 load_saved_frame("runs/sprite_test") 得到同样的 frame 字典。

## 4. 单独测试坐标转换

已有相机系 XYZ 时：

~~~python
from xarm_grasp.config import DEFAULT_CONFIG, load
from xarm_grasp.coordinates import camera_to_tcp

config = load(DEFAULT_CONFIG)
point_tcp = camera_to_tcp(
    [0, 0, 500],
    config["robot"]["tcp_offset"],
    config["calibration"]["T_flange_camera"],
)
print(point_tcp)
~~~

也可以直接运行，不需要连接相机或机械臂：

~~~bat
python -m xarm_grasp.coordinates --camera-point 0 0 500
~~~

转换关系是 P_tcp = inverse(T_flange_tcp) × T_flange_camera × P_camera。这里会处理 TCP 偏移中的平移和旋转，而不仅仅减去一个 Z 偏移。

只有需要生成基座系目标点用于工作范围和逆解检查时，才使用 tcp_to_base(point_tcp, tcp_pose_at_capture)。tcp_pose_at_capture 必须是拍照时的真实 TCP 位姿，格式为 [x,y,z,roll,pitch,yaw]，不能传关节值。实际分轴运动仍通过工具坐标增量下发。

## 5. 单独测试 OCR

在 CMD 中设置已有的 API Key：

~~~bat
set ZHIPUAI_API_KEY=你的API密钥
python -m vision.ocr --image D:\data\receipt.jpg --output runs/receipt_test
~~~

直接拍当前相机画面做 OCR：

~~~bat
python -m vision.ocr --output runs/receipt_test
~~~

这两个命令都不会移动机械臂。OCR 会调用配置中的远程服务，并上传小票图像。

用函数读取已有图片：

~~~python
from xarm_grasp.config import DEFAULT_CONFIG, load
from vision.ocr import recognize_receipt_file

config = load(DEFAULT_CONFIG)
receipt = recognize_receipt_file(r"D:\data\receipt.jpg", config)
item_name = receipt["selected_item"]
print(item_name, receipt["ocr_text"])
~~~

已有 BGR 图像时调用 recognize_receipt(bgr, config)；已有文字、只想测试商品映射时调用 select_receipt_product("雪碧 1瓶", config)，后者不访问网络。

OCR 返回商品名称和文字；商品 3D 坐标需要到货架观察位后，再调用 YOLO 和深度定位。不要把桌面小票图片作为货架商品定位图像。

## 6. 修改抓取动作与总流程的位置

- 修改先到哪里、再 OCR、再去哪里：改 __main__.py 的 run()。
- 修改 bbox 中心点或目标筛选：改 yolo.py 的 detect()。
- 修改深度取样或坐标公式：改 coordinates.py。
- 修改 Y/X/Z 的位移计划：改 motion.py 的 make_tool_axis_plan_from_tcp()。
- 修改夹爪开合、抬升、退出顺序：改 motion.py 的 execute_grasp_plan()。若改变运动顺序，也要同步改变计划里的步骤顺序，保证预检目标与真实路径一致。
- 修改 SDK 调用、到位检查、夹爪速度和力度：看 robot.py。
- 商品类别、深度补偿、位姿和速度参数继续优先修改 config.json。

原有命令仍可使用：

~~~bat
python -m xarm_grasp --item 雪碧
python -m xarm_grasp --item 雪碧 --execute --plan-only
python -m xarm_grasp --execute
~~~

第一条保持原先行为：当前相机视野的商品检测，仅返回相机系点。新的 vision.yolo 入口默认同时返回 TCP 系点。

--execute --plan-only 会真实移动到安全位和观察位、预检分轴计划并返回安全位，但不执行夹爪动作或 Y/X/Z 抓取。完整执行仍按 Y 对齐 → X 对齐 → Z+ 深入 → 夹紧 → X- 抬升 → Z- 退出 → 返回安全位。固定朝向约束适用于分轴抓取阶段；回安全关节位可能改变朝向。

离线回归测试：

~~~bat
python -m unittest discover -s tests -v
~~~

测试使用模拟机器人、模拟检测结果和模拟 OCR 响应。通过这些测试不代表已验证实机标定精度或抓取成功率。
