# 视觉识别模块

YOLO 商品定位、OCR 小票识别和 Gemini 相机工具集中在本目录。完整抓取流程通过函数调用这些模块。

在已有 xarm Conda 环境中，从 shop-workspace 运行：

~~~bat
conda activate xarm
cd /d D:\Robotics\xarm-shop-demo\shop-workspace
~~~

## 文件职责

| 文件 | 作用 |
|---|---|
| yolo.py | 加载商品模型、识别指定类别、结合深度返回相机系和 TCP 系 3D 点、保存与回放 RGB-D |
| ocr.py | 调用 OCR 服务读取小票，匹配配置中的商品名称 |
| camera.py | Gemini 336 采集、深度和彩色对齐、获取相机内参 |
| check_camera.py | 检查相机型号、序列号和有效深度 |
| camera_preview.py | 实时彩色/深度预览 |
| tests/ | 相机适配、预览、OCR 商品映射的离线测试 |
| __main__.py | python -m vision 的商品识别入口 |
| __init__.py | Python 包声明 |

共用 shop-workspace/config.json、requirements.txt 和 models/products.pt。模型路径仍相对于配置文件所在目录解析。默认测试输出继续写入 shop-workspace/runs/ 下，不需要重新填写商品或标定配置。

坐标数学函数位于 xarm_grasp/coordinates.py；机械臂运动位于 xarm_grasp/robot.py 和 motion.py；标定工具位于 camera_calibration/。

## 独立运行

检查相机、预览图像：

~~~bat
python -m vision.check_camera
python -m vision.camera_preview --show-depth
~~~

识别商品并输出相机系、TCP 系 3D 坐标：

~~~bat
python -m vision.yolo --item 雪碧 --output runs/sprite_test
~~~

等价简写为 python -m vision --item 雪碧。只需要相机系坐标时，加 --camera-only。

小票 OCR：

~~~bat
set ZHIPUAI_API_KEY=你的API密钥
python -m vision.ocr --image D:\data\receipt.jpg --output runs/receipt_test
~~~

省略 --image 时，拍当前 Gemini 相机画面识别。OCR 会上传小票图像至配置中的远程 OCR 服务。以上视觉命令均不会连接或移动机械臂。

## 函数直接返回 3D 坐标

~~~python
from vision.yolo import get_product_position

xyz = get_product_position("雪碧", coordinate_system="tcp")
print(xyz)  # NumPy [x, y, z]，单位毫米
~~~

一次识别获取两套坐标：

~~~python
from vision.yolo import locate_product

result = locate_product("雪碧")
print(result["camera_point_mm"])
print(result["tcp_point_mm"])
~~~

上述函数打开相机采集一帧后关闭。连续识别时可复用相机和模型：

~~~python
from vision.camera import GeminiCamera
from vision.yolo import load_model, recognize_product
from xarm_grasp.config import DEFAULT_CONFIG, load, resolve_entry

config = load(DEFAULT_CONFIG)
name, product = resolve_entry(config, "雪碧")
model = load_model(DEFAULT_CONFIG, product)

with GeminiCamera(config["camera"]) as camera:
    frame = camera.capture()
    result = recognize_product(frame, model, product, config, name)
    print(result["tcp_point_mm"])
~~~

返回的是 bbox 中心像素对应的商品可见表面点，未加抓取深入补偿。TCP 转换使用手眼矩阵和配置中的 TCP 偏移，不需要当前机械臂位姿。没有唯一目标、深度无效或相机序列号与标定不符时会抛出异常。

## OCR 函数

~~~python
from vision.ocr import recognize_receipt_file
from xarm_grasp.config import DEFAULT_CONFIG, load

result = recognize_receipt_file(r"D:\data\receipt.jpg", load(DEFAULT_CONFIG))
print(result["selected_item"])
print(result["ocr_text"])
~~~

已有 BGR 图像时调用 recognize_receipt(bgr, config)；只有文字时调用 select_receipt_product(text, config)，不需要网络。

OCR 返回小票商品名称。机械臂到货架观察位后，YOLO 与 RGB-D 再返回该商品的 3D 坐标。

## 离线回放和测试

使用 YOLO 保存的一组 color.png、depth_mm.npy 和 frame.json：

~~~bat
python -m vision.yolo --item 雪碧 --frame-dir runs/sprite_test --output runs/sprite_replay
~~~

仅运行视觉组件测试：

~~~bat
python -m unittest discover -s vision/tests -v
~~~

运行全部测试，包括视觉定位、OCR 接口、标定和模拟抓取：

~~~bat
python -m unittest discover -s tests -v
~~~

完整抓取入口仍为 python -m xarm_grasp --execute。更多接口说明见 [MODULE_GUIDE_ZH.md](../MODULE_GUIDE_ZH.md)。
