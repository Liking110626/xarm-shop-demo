# xArm 6 + Gemini 336 + Robotiq 2F-85 现场校准与验收

本文按依赖顺序执行。机械结构、TCP 或相机位置一旦变化，后面的手眼标定都必须重做。

## 1. 开始前

1. 固定机械臂底座、Robotiq、相机支架和线缆。相机不能在支架上晃动。
2. 清空夹爪，准备急停，第一次运动速度保持当前配置的关节 5 deg/s、TCP 20 mm/s。
3. 电脑网卡与控制器在同一网段，确认能访问 `http://192.168.1.209:18333`。
4. Studio 可以保持打开以查看状态，但不要在 Python 正在运动时从 Studio 再发送运动命令。
5. 棋盘固定在桌面或货架上，整个采样期间不能移动。

当前三个关节位姿的用途：

- `initial_pose`：安全中转位。
- `receipt.observation`：小票观察位。
- `grasp_observation`：共享商品观察位。

程序的真实路径现在是：initial → 小票位 → initial → 商品观察位；用 `--item` 跳过小票时是 initial → 商品观察位。关节运动仍没有完整连杆碰撞规划，第一次必须低速目视确认整条轨迹。

## 2. 先设置安装方向、TCP 和负载

### 2.1 安装方向

在 xArm Studio 的设置中选择真实安装方向。安装方向决定重力补偿，必须先于负载识别和手眼标定。

### 2.2 TCP

TCP 原点建议定义在两指之间、实际夹住瓶身时的中心位置，TCP 姿态应满足：

- Z+：向前、深入货架；
- Y+：向左；
- X-：向上。

若 Robotiq、转接板和法兰完全采用官方标准安装，可把官方参考值作为起点；腕部相机和自制转接结构存在时，应在 Studio 使用 TCP 五点标定，不要直接把参考值当最终值。

TCP 标定后做两项验证：

1. **定点旋转测试**：让 TCP 对准一个固定尖点，在多个腕部姿态下重新对准。TCP 原点如果正确，旋转后尖点不会画明显圆弧。建议最大漂移先控制在 2–3 mm 内。
2. **工具轴点动测试**：在商品观察位，用 Studio 工具坐标依次点动 5 mm，再点动 10 mm。确认 Z+ 向前、Y+ 向左、X- 向上。方向不符合时修正 TCP 姿态，不要修改已经确认的分轴角色来掩盖问题。

把 Studio 显示的六个 TCP 值原样填入：

```json
"tcp_offset": [x_mm, y_mm, z_mm, roll_deg, pitch_deg, yaw_deg]
```

程序启动真实运动时会读取控制器当前 TCP，并以 0.05 的容差与配置比较；不一致会停止。

### 2.3 负载与重心

负载应包含机械臂法兰以后随动的全部质量：Robotiq、转接板、相机、支架和随动线缆。重心填这套组件相对法兰工具坐标原点的 X/Y/Z。先用称重和尺寸估算，再按 Studio 的负载辨识流程校正。辨识时夹爪保持空载，并满足 Studio 对安装方式和运动空间的要求。

验收表现：

- 解锁或低速移动时没有明显下坠、抖动；
- 静止时没有异常漂移；
- 低速空载轨迹不会频繁误报碰撞或过流。

抓住商品后总负载会增加。六种饮料质量差异明显，后续宜在抓取成功后按商品更新 TCP 负载；当前代码尚未自动切换产品负载，因此首轮测试必须继续用低速、低加速度。

## 3. 相机检查

在 Conda 环境中运行：

```powershell
cd D:\Robotics\xarm-shop-demo\shop-workspace
conda activate xarm
python -m xarm_grasp.check_camera
```

确认输出型号为 Gemini 336，彩色和深度画面尺寸匹配，深度单位为毫米，并记录序列号。把序列号填入 `camera.serial`；手眼求解后还要与 `calibration.camera_serial` 相同。

相机支架、焦距配置、彩色分辨率或对齐方式变化后，重新做手眼标定。

## 4. 手眼标定

### 4.1 棋盘

推荐使用 9×6 个**内角点**的平面棋盘，即 10×7 个黑白方格。格边可用约 20–30 mm，25 mm 通常合适。打印后贴在不反光的硬平板上，用卡尺测实际格边；命令中的 `--square-mm` 填实测值，不能填打印设计值。棋盘要平整、无翘曲、无覆膜强反光。

### 4.2 求解样本

保持棋盘完全固定。每个姿态都先在 Studio 手动移动并停稳，再运行一次采样命令。脚本只读取机械臂位姿和相机，不会命令机械臂运动。

```powershell
python -m xarm_grasp.calibrate capture --samples calibration\solve_samples.json --cols 9 --rows 6 --square-mm 25.00
```

采集 15–25 张：

- 棋盘分布在画面中心、四周、近处和远处；
- 绕至少两个不同轴有明显旋转，建议覆盖约 ±15° 到 ±35°；
- 棋盘始终完整可见，深度距离处于相机可靠范围；
- 每张图清晰，脚本报告重投影误差不超过 1 px；
- TCP offset、相机位置、分辨率和棋盘全程不变。

不要只平移相机，也不要采集大量几乎相同的姿态。然后求解：

```powershell
python -m xarm_grasp.calibrate solve --samples calibration\solve_samples.json --output calibration\result.json
```

求解脚本当前要求训练样本的最大固定棋盘残差不超过 5 mm、2°。结果中：

- `T_flange_camera`：相机坐标到法兰坐标的 4×4 变换，平移单位 mm；
- `camera_serial`：参与标定的相机；
- `tcp_offset`：标定时控制器中的 TCP，用于防止参数被悄悄更换；
- `validated`：仍为 false，表示尚未完成独立验收。

### 4.3 留出样本

求解后不要移动棋盘，再采集 5–10 个没有参与求解的新姿态：

```powershell
python -m xarm_grasp.calibrate capture --samples calibration\validation_samples.json --cols 9 --rows 6 --square-mm 25.00
python -m xarm_grasp.calibrate validate --samples calibration\validation_samples.json --calibration calibration\result.json --output calibration\validation_result.json
```

默认验收门槛是最大平移残差 3 mm、最大旋转残差 1°。这一步检验不同腕部姿态下，同一块固定棋盘是否仍落在同一基座位姿。它通过后 `validated` 仍保持 false，因为一致性好不等于绝对落点一定正确。

### 4.4 实体点位验收

1. 固定一个瓶子或在棋盘上选一个容易重复定位的点。
2. 将 `camera_serial` 和 `T_flange_camera` 复制到 `config.json.calibration`，暂时保留 `validated: false`。
3. 完成工作空间和商品深度偏置的保守初值后，运行 `--plan-only`。它只走安全位和观察位，生成 `runs/.../result.json`，不会动夹爪，也不会执行 Y/X/Z 抓取运动。
4. 查看 `camera_point_mm`、`base_point_mm` 和 `tool_axis_plan.steps`。确认符号符合：左右只在 Y，垂直只在 X，深入只在 Z；approach_depth 为 Z+，lift 为 X-。
5. 在 Studio 中按计划值手动点动，先停在预测接触点前 30 mm，再到 10 mm，最后用 1–2 mm 小步逼近。用尺或尖点比较预测中心与真实目标中心。

建议首轮目标：X/Y/Z 单轴绝对误差各不超过 3 mm，三维误差最大不超过 5 mm，并在至少 5 个不同位置重复。如果误差随腕部姿态变化，优先重做手眼；若所有点存在近似固定方向偏差，复查 TCP、棋盘尺寸和深度；只有与瓶身几何相关的 Z 固定差值才进入商品 `grasp_depth_offset_mm`。

全部通过后才将：

```json
"calibration": {
  "validated": true,
  "camera_serial": "...",
  "T_flange_camera": [[...], [...], [...], [...]]
}
```

## 5. 工作空间

`workspace_mm` 使用基座坐标系的轴对齐范围：

```json
"workspace_mm": [
  [xmin, xmax],
  [ymin, ymax],
  [zmin, zmax]
]
```

测量方法：

1. 分别把机械臂停在 initial、小票位、商品观察位，用 `python -m xarm_grasp.teach --type tcp` 记录 TCP。
2. 再记录预期左右对齐、上下对齐、最深抓取、抬升和退出后的极限点。
3. 对这些安全点取每轴最小/最大值并留 30–50 mm 的正常误差余量，但边界必须仍在真实无碰撞区域内。
4. 单独低速走 initial → 小票位 → initial → 商品观察位，确认连杆、相机、夹爪和线缆都不碰撞。

代码的 workspace 检查只检查 TCP 目标点和逆解，不能发现连杆扫过货架，所以人工轨迹验收不可省略。

## 6. 商品抓取深度与夹爪 offset

`grasp_depth_offset_mm` 是视觉得到瓶身正面表面后，TCP 沿工具 Z+ 继续到夹持中心的距离。初值可以用抓取高度处的瓶身半径，但必须实测；罐装和不同瓶型应分别保存。

逐个商品校准：

1. 把瓶子固定在正常货位，先填实测半径作为保守初值。
2. 运行：
   
   ```powershell
   python -m xarm_grasp --item 雪碧 --execute --plan-only --output runs\sprite_plan
   ```
3. 查看 `result.json` 中 `target_delta_tool_mm` 和五个目标位。确认 lateral≈Y、vertical≈X、depth 为正 Z。
4. 用 Studio 手动复现，先在最终深度前保留 30 mm，再保留 10 mm，最后 1–2 mm 小步调整，记录 TCP 位于瓶身中心时所需的 Z 增量。
5. 把差值写入该商品的 `grasp_depth_offset_mm`，连续换 3 个摆放位置复测。若误差随画面位置变化，不要靠商品 offset 补偿，应回查标定和深度。
6. 手持商品在安全区域单独测试 `open_position`、`close_position`。闭合后状态应表示检测到物体，而不是完全闭合或故障。夹持应稳固且不压坏包装。

## 7. 推荐的首次执行顺序

1. 相机检查。
2. TCP 定点旋转和 ±5/±10 mm 工具轴测试。
3. 空载低速验证三个关节位及中转轨迹。
4. 手眼求解样本与留出样本通过。
5. 实体点位至少 5 点通过。
6. 填写真实 workspace 和一个商品的深度偏置。
7. 保持 `motion_enabled: false` 做离线测试。
8. 设置 `motion_enabled: true`，先运行 `--plan-only`。
9. 人员在急停旁，货架内只放一个目标，执行一次完整抓取。
10. 单个商品连续成功 10 次、无碰撞和误抓后，再校准下一个商品并测试小票全流程。

离线回归命令：

```powershell
conda activate xarm
cd D:\Robotics\xarm-shop-demo\shop-workspace
python -m unittest discover -s tests -v
```
