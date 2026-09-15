# 手眼标定前的多帧诊断

此脚本只读取设备，不发送使能、拖动模式、回零、运动或夹爪命令。
每个命令自动连续采20帧，代表一个静止姿态，不能当成20组手眼样本。
旧的求解与采样文件不变。每次命令创建带时间和随机后缀的新目录，不会覆盖上次采集。

## 准备

在已激活的 xarm CMD 中进入目录：

```bat
cd /d C:\Users\LENOVO\Desktop\xarm-shop-demo\shop-workspace
python -m xarm_grasp.calibration_probe capture --help
```

板参数默认9×6内角点、24 mm格边；现场测量不同时修改命令。
棋盘、底座和相机支架在整套测试中保持固定。关闭占用相机的预览程序。

## 四次采集

每次调整姿态后，在Studio关闭Manual Mode，等机械臂进入Position模式、静止状态；
松手并等待约2秒，执行一次命令。采集期间不要碰机械臂、棋盘或线缆。
通常每次需要十几至几十秒，包含逐帧读取和相机丢弃预热帧。

1. **A1：近似正视。**棋盘完整可见、靠近画面中心。选择正常工作范围内的姿态。
   在Studio保存这一组关节角为A，后续必须回到相同关节姿态。

```bat
python -m xarm_grasp.calibration_probe capture --label A1 --frames 20 --cols 9 --rows 6 --square-mm 24
```

2. **B1：向一个方向倾斜约25°–30°。**相机光轴相对棋盘法线发生倾斜，
   图像中棋盘出现明显透视变化；只在图像内转圈不算这种倾斜。
   可配合调整位置，让棋盘仍完整、居中。停稳后执行：

```bat
python -m xarm_grasp.calibration_probe capture --label B1 --frames 20 --cols 9 --rows 6 --square-mm 24
```

3. **C1：绕另一个独立方向倾斜约25°–30°。**例如B是左右侧倾，C改成前后俯仰。
   不要仅继续绕同一根轴旋转。停稳后执行：

```bat
python -m xarm_grasp.calibration_probe capture --label C1 --frames 20 --cols 9 --rows 6 --square-mm 24
```

4. **A2：回位。**在Studio低速返回保存的A关节姿态，确认路径无碰撞。
   不要用手凭感觉放回相似位置。停稳后执行：

```bat
python -m xarm_grasp.calibration_probe capture --label A2 --frames 20 --cols 9 --rows 6 --square-mm 24
```

## 离线比较

```bat
python -m xarm_grasp.calibration_probe compare
```

默认读取 `calibration/probe_v1` 中全部会话，生成独立的 `comparison_*.json`。
换棋盘、改变相机安装或开始另一次测试时，用新的输出目录，例如各次采集都添加
`--output calibration/probe_v2`，比较时用 `compare --input calibration/probe_v2`。

## 文件和结果

每次会话保存：

- `frame_001_raw.png`：未画角点、无损原图；每帧都有。
- `frame_001_annotated.png`：角点预览，紫色O圈出角点原点；每帧都有。
- `report.json`：原始和归一化后的角点像素坐标、IPPE候选解、原ITERATIVE结果、
  像素误差、多解提示、机械臂关节角/TCP/TCP offset/world offset/模式/状态、相机内参。
- 时间记录是主机端读取和采集起止时间，不是精确的曝光同步时间戳。

正常有两个IPPE候选解不等于失败；只有两解误差接近且姿态有实质区别时才标记多解疑点。
不根据机器人数据偷偷选择一个更容易通过手眼验证的分支。

- `STABLE_BURST`：本次静止采集通过诊断门槛，不代表手眼标定通过。
- `REVIEW`：检测失败、运动、模式不符、位姿波动或多解等，查看 `review_reasons`。
- `INCOMPLETE`：设备/保存异常或中断，已有数据仍保存在目录中。

诊断门槛：相对静止序列起点的机器人位置/姿态变化0.1 mm/0.1°；
视觉位姿相对均值的最大波动0.5 mm/0.2°。它们是排查用启发式阈值，不是设备精度规格。
报告同时给出最大和RMS波动；旋转参考采用平均旋转，不采用第一帧。

比较报告中：

- A1/A2先检查 `max_joint_difference_deg`，确认真的回到了相同关节姿态。
- `vision_pose_difference` 用于相同姿态回位对比；不同姿态下不能把两边的平移距离直接相减。
- `relative_rotation_angle_mismatch_deg` 比较法兰与相机的相对旋转角，不需要手眼矩阵。
- 任何REVIEW/INCOMPLETE会话的均值仅供诊断，不能直接当合格标定样本。
- 不同会话之间需检查标注图紫色O是否对应棋盘同一个物理角；
  脚本只在单次静止会话内部归一化角点180°反序，不擅自修正跨会话棋盘原点。

请先完成四次诊断，分析后再决定是否正式采集。不要把这80帧直接灌入旧solve命令。
