# 手眼标定

该目录集中存放腕部 Gemini 336 的手眼标定工具。运行命令时，请位于 shop-workspace 并激活已有的 xarm Conda 环境。

~~~bat
conda activate xarm
cd /d D:\Robotics\xarm-shop-demo\shop-workspace
~~~

## 目录职责

- calibrate.py：棋盘格采样、手眼矩阵求解、独立样本验证。
- calibration_probe.py：固定姿态的多帧诊断、棋盘格解的歧义检查、不同采集批次对比。
- tests/：标定求解和诊断的离线测试。
- data/：默认采样、图片和结果目录；首次采集时自动创建，并被 Git 忽略。
- __main__.py：python -m camera_calibration 的入口。

相机采集复用 vision.camera；机器人读数、配置和坐标数学函数复用 xarm_grasp，无需维护两份驱动。默认读取 shop-workspace/config.json。抓取程序读取其中 calibration 字段，不依赖本目录中的求解脚本。

## 采样与求解

先固定棋盘格，手动移动机械臂，停稳后每次运行一次 capture。下例为 9×6 内角点、25 mm 格边，必须换成你的实际尺寸；采集 15–25 个位置和多轴旋转有变化的视角。

~~~bat
python -m camera_calibration capture --cols 9 --rows 6 --square-mm 25
python -m camera_calibration solve
~~~

默认采样文件为 data/samples.json，求解结果为 data/result.json，路径均相对于本标定目录。可以用 --samples 和 --output 指定其他路径，例如读取已有样本：

~~~bat
python -m camera_calibration solve --samples D:\data\samples.json --output D:\data\result.json
~~~

采集与求解沿用现有规则：采样期间相机、棋盘格参数和 TCP 配置保持一致。脚本读取机器人状态，不下发运动指令。

## 独立样本验证

棋盘格保持固定，另外采集至少 5 个没有参与求解的视角，每个位姿停稳后运行：

~~~bat
python -m camera_calibration capture --cols 9 --rows 6 --square-mm 25 --samples camera_calibration/data/validation_samples.json
python -m camera_calibration validate
~~~

默认读取 data/result.json 和 data/validation_samples.json，输出 data/validation_result.json。一致性通过后仍需现场验证实际点位精度；脚本不会自动把 validated 改为 true。

完成验收后，使用现有方式将 camera_serial、T_flange_camera 填入 shop-workspace/config.json 的 calibration 字段，并设置 validated。本次目录迁移没有更改已有标定矩阵，也不要求重新标定。

## 多帧诊断

在一个固定姿态下采集一组画面：

~~~bat
python -m camera_calibration.calibration_probe capture --label A1 --cols 9 --rows 6 --square-mm 25
~~~

换一个姿态后用 B1、C1 等标签再次采集，再离线比较：

~~~bat
python -m camera_calibration.calibration_probe compare
~~~

默认存放于 data/probe_v1/。一组静止画面的多帧用于诊断稳定性，不能算作多个独立手眼标定视角。

## 帮助与离线测试

~~~bat
python -m camera_calibration --help
python -m camera_calibration.calibration_probe --help
python -m unittest discover -s camera_calibration/tests -v
~~~

原来的全项目测试命令仍会包含标定测试：

~~~bat
python -m unittest discover -s tests -v
~~~

旧入口 xarm_grasp.calibrate、xarm_grasp.calibration_probe 已移除，请使用上述新命令。
