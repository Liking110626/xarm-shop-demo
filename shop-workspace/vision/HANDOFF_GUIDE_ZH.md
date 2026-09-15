# xArm 商品抓取项目：新电脑部署与交接手册

> **历史资料提示：** 本文记录整合前的阶段状态，其中 `.venv`、Python 3.12 和“尚未自动串联”等说明已过期。当前项目使用 Conda 环境 `xarm`，正式入口及配置以根目录 `README.md` 和 `HANDOFF_GUIDE_ZH.md` 为准。


更新日期：2026-09-15

> 2026-09-15 已新增 OCR 与实时商品识别专项交接文档：`HANDOFF_OCR_YOLO_ZH.md`。接手当前“小票识别 + 商品识别”工作时，应先阅读该文档，再按本文继续手眼标定和机械臂抓取联调。

本文给接手同学使用，目标是在一台新的 Windows 电脑上，从零配置 xArm 6、Orbbec Gemini 336 和 Robotiq 2F-85，并完成视觉识别、手眼标定与首次安全抓取。

## 1. 当前实现和交接状态

程序的工作流程是：根据输入商品找到对应观察位姿，机械臂到达并保持该 TCP 朝向；Gemini 336 获取对齐后的彩色图和深度图；YOLO 检测指定类别；将检测框中心附近的深度从 2D 反投影为相机三维点，再通过眼在手上标定矩阵转换到基座坐标系；最后沿工具坐标系 Y 轴左右对齐、沿 Z 轴上下对齐、沿 X 轴深入，闭合夹爪，沿 Z 轴轻抬，再沿 X 轴退出。

当前代码已具备离线测试、相机诊断、手眼标定采集和求解、位姿读取、视觉模式与真实执行入口。真实设备参数尚未全部填写，因此 `config.json` 中 `motion_enabled` 仍为 `false`。在完成 TCP、标定、工作范围、观察位姿和抓取深度测量前，程序会拒绝真实运动。

当前没有自动放置动作，也没有机械臂连杆、相机、夹爪与货架的完整碰撞规划。一次抓取结束后，夹爪保持夹持。

## 2. 新电脑需要准备的硬件和系统

- Windows 10 或 Windows 11，64 位。
- Python 3.12，64 位。当前开发环境使用 Python 3.12.8。
- Git for Windows。
- 支持数据传输的 USB 3.x 接口。Gemini 336 建议直接连接电脑主机接口，不要先接无源 Hub。
- 有线网口，用于连接 xArm 控制器。
- xArm 6 控制器地址：`192.168.1.209`。
- Gemini 336 固定在腕部，属于眼在手上安装方式。
- Robotiq 2F-85 接在 xArm 末端工具接口，通过工具 Modbus 控制。
- 一块刚性、哑光、平整的棋盘格标定板。推荐 9×6 个内角点，即 10×7 个黑白格；格边约 25 mm，并以实测值为准。

## 3. 必须交付给新电脑的内容

推荐保持以下目录结构，因为程序会从 `shop-workspace` 的相邻目录加载 xArm SDK：

```text
xarm-shop-demo/
├─ xArm-Python-SDK/
├─ pyorbbecsdk/
└─ shop-workspace/
   ├─ xarm_grasp/
   ├─ tests/
   ├─ models/
   │  └─ products.pt
   ├─ config.json
   ├─ requirements.txt
   ├─ README.md
   └─ HANDOFF_GUIDE_ZH.md
```

需要交付：

1. 整个 `shop-workspace` 源码，但不要复制 `.venv`、`.test-deps`、`__pycache__` 和 `runs`。
2. `models/products.pt`。该文件被 `.gitignore` 忽略，普通 Git 提交或克隆不会包含它，必须单独传输。
3. `xArm-Python-SDK`。当前参考版本为官方仓库 `xArm-Developer/xArm-Python-SDK`。
4. `pyorbbecsdk`。当前参考版本为官方仓库 `orbbec/pyorbbecsdk` 的 `v2-main` 分支。
5. 如果已经在同一套机械臂、相机支架、夹爪和 TCP 上完成标定，还要单独交付 `calibration/`。该目录也被 Git 忽略。当前项目尚未完成实机手眼标定，所以接手同学需要重新采集。

也可以在新电脑执行：

```powershell
git clone https://github.com/xArm-Developer/xArm-Python-SDK.git
git clone --branch v2-main https://github.com/orbbec/pyorbbecsdk.git
```

只克隆 `pyorbbecsdk` 源码并不能直接 `import pyorbbecsdk`。后面的 `pyorbbecsdk2` pip 包提供 Windows Python 扩展，导入名称仍是 `pyorbbecsdk`。
当前交付模型的文件信息如下，新电脑复制后应重新计算并核对：

```text
文件大小：53150885 bytes
SHA-256：98783ACC0A40222677ADB0DC37CC28B049E6F2C37E01D7BCFA6D363E893595EB
```

校验命令：

```powershell
Get-FileHash .\models\products.pt -Algorithm SHA256
```

## 4. 在新电脑创建 Python 环境

以下示例假设接手同学把工程放到 `D:\Robotics\xarm-shop-demo`。如果放在其他位置，只需替换第一行路径。

```powershell
$projectRoot = 'D:\Robotics\xarm-shop-demo'
cd "$projectRoot\shop-workspace"
py -3.12 -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

不要把原电脑的 `.venv` 复制过来。虚拟环境包含绝对路径和本机原生库，换电脑后必须重建。

检查关键依赖：

```powershell
.\.venv\Scripts\python.exe -c "import cv2,numpy,ultralytics,pyorbbecsdk; print('Python dependencies OK')"
```

运行离线测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试使用模拟机器人和合成数据，不会连接或移动机械臂。交接时的基线是 11 项测试通过。

## 5. 在新电脑配置 Gemini 336

先连接 Gemini 336，关闭 Windows 相机、Orbbec Viewer、视频会议软件等可能占用相机的程序。

Windows 首次使用时，从 `shop-workspace` 目录执行 Orbbec 官方环境脚本：

```powershell
.\.venv\Scripts\python.exe ..\pyorbbecsdk\scripts\env_setup\setup_env.py
```

接受管理员权限提示。脚本会为 Orbbec UVC 设备注册 Windows Media Foundation metadata。完成后拔掉相机，等待几秒再插回；仍不能启动时重启电脑。

先运行官方最小示例：

```powershell
.\.venv\Scripts\python.exe ..\pyorbbecsdk\examples\quick_start.py
```

再运行本项目诊断；该命令不连接机械臂：

```powershell
.\.venv\Scripts\python.exe -m xarm_grasp.check_camera
```

成功后检查：

- `runs/camera_check/color.jpg` 能正常显示彩色画面。
- `runs/camera_check/camera.json` 中型号为 Gemini 336，序列号稳定，彩色和对齐深度尺寸一致。
- `runs/camera_check/depth_mm.npy` 的单位是毫米。

当前程序请求 640×480、30 FPS 彩色流，使用 SDK 默认深度流，并把深度软件对齐到彩色图。标定和识别共用同一个相机封装，不能分别改用不同分辨率或不同对齐方式。

如果报 `MFCreateDeviceSource`、`Send control transfer failed` 或找不到路径：重新以管理员身份执行环境脚本，拔插或重启，关闭占用相机的软件，换电脑直连的 USB 3.x 口，并在设备管理器确认 RGB Camera 与 Depth Camera 都存在。

## 6. 配置机械臂网络和末端工具

将新电脑有线网卡设置为与控制器同一网段的未占用静态地址，例如：

```text
IP 地址：192.168.1.100
子网掩码：255.255.255.0
网关：留空
```

不要使用 `192.168.1.209`，这是控制器地址。先检查：

```powershell
ping 192.168.1.209
```

在 xArm Studio 中完成以下工作：

1. 确认型号、固件、急停、使能和错误状态正常。
2. 配置 Robotiq 2F-85 的工具 Modbus 接线及通信，手动测试张开、闭合和物体接触状态。
3. 设置真实工具 TCP 和负载。TCP 建议定义在两指之间的实际夹持中心。
4. 记录 TCP 偏移 `[x,y,z,roll,pitch,yaw]`，单位为 mm 和度，填入 `config.json` 的 `robot.tcp_offset`。
5. 程序会比较控制器当前 TCP 与配置值，二者必须一致。更换夹爪、转接板、相机支架或 TCP 定义后应重新检查。

夹爪的 `open_position` 和 `close_position` 是 Robotiq 原始值 0–255，不是毫米。当前配置用 0 全开、255 全闭，实际抓瓶时可根据瓶径调整闭合目标。

## 7. 核对 YOLO 模型和商品类别

YOLO 的每个检测框内部使用数值 `class_id`，模型还会用 `model.names` 把该数字映射为训练标签。业务商品名、别名和模型类别的关系由 `config.json` 明确配置，因此命令行可以输入中文商品名，而检测时使用对应的数值类别。

当前 `products.pt` 的类别表是：

```text
0:'67', 1:'68', 2:'69', 3:'70', 4:'71',
5:'72', 6:'73', 7:'74', 8:'75', 9:'76'
```

当前配置关系：

| 商品 | class_id | 模型标签 |
|---|---:|---:|
| 可口可乐罐装 | 0 | 67 |
| 雪碧罐装 | 1 | 68 |
| 芬达罐装 | 2 | 69 |
| 水溶C100瓶装 | 5 | 72 |
| 名仁苏打水饮料 | 8 | 75 |
| 东方树叶茉莉花茶 | 9 | 76 |

原始需求中写过“小苏打”，但当前模型类别范围只有 67–76，并不包含此前提到的商品编号 63。当前配置使用的是“名仁苏打水饮料”，class 8、标签 75。若实际要抓的是编号 63 的小苏打商品，必须取得包含该类别的新权重，并据新模型的 `model.names` 修改 `class_id`，不能直接把商品名改成“小苏打”后继续使用现权重。

可用以下命令在新电脑再次打印类别表：

```powershell
.\.venv\Scripts\python.exe -c "from ultralytics import YOLO; print(YOLO('models/products.pt').names)"
```

## 8. 填写商品配置和观察位姿

每个商品至少需要以下字段：

```json
"雪碧罐装": {
  "aliases": ["雪碧", "sprite"],
  "model": "models/products.pt",
  "class_id": 1,
  "observation": {"type": "joint", "values": null},
  "open_position": 0,
  "close_position": 255,
  "grasp_depth_offset_mm": null
}
```

观察位姿推荐保存关节值，避免不同 TCP 定义影响同一个观察姿态：

```json
"observation": {
  "type": "joint",
  "values": [J1, J2, J3, J4, J5, J6]
}
```

也支持 TCP 位姿：

```json
"observation": {
  "type": "tcp",
  "values": [x, y, z, roll, pitch, yaw]
}
```

手动把机械臂移动到观察位置并停稳，然后从 `shop-workspace` 读取位姿：

```powershell
.\.venv\Scripts\python.exe -m xarm_grasp.teach --type joint
# 或
.\.venv\Scripts\python.exe -m xarm_grasp.teach --type tcp
```

该命令只读取，不使能和移动机械臂。把输出的 `observation` 复制到商品配置。同一货架区域的多个商品可以共用观察位姿。

在 xArm Studio 的工具坐标点动模式下，以很小步长现场确认：工具 Y 是左右，工具 Z 是上下，工具 X 是向瓶子深入/反向退出。当前代码还要求工具 Z 与基座竖直方向有足够大的分量，否则拒绝抓取。TCP 姿态在对齐和深入期间保持不变。

`grasp_depth_offset_mm` 表示从深度相机看到的瓶子正面表面，沿工具 X 深入到 TCP 抓取中心还要移动的距离。初始值可参考抓取高度处瓶身半径，但必须根据实物尺寸、相机深度偏差和 TCP 位置逐步测量调整。

还必须把经过现场验证的基座坐标安全范围填到 `workspace_mm`。不要为了通过校验填写过大的虚构范围。

## 9. 执行眼在手上标定

相机与腕部支架固定后不要再移动。棋盘格固定在桌面或支架上，整个采集过程保持不动。`cols` 和 `rows` 是内角点数，`square-mm` 是用卡尺或可靠直尺测得的单格边长。

每次在 xArm Studio 中手动移动到新位姿并停稳，然后执行一次：

```powershell
.\.venv\Scripts\python.exe -m xarm_grasp.calibrate capture `
  --samples calibration/gemini336_samples.json `
  --cols 9 --rows 6 --square-mm 25
```

其中 25 必须换成标定板实测格边。建议采集 15–25 组：覆盖不同距离、画面位置和绕多个轴的明显旋转；棋盘完整可见、清晰、不过曝。只做平移或只绕单轴旋转会导致标定不稳定。

求解：

```powershell
.\.venv\Scripts\python.exe -m xarm_grasp.calibrate solve `
  --samples calibration/gemini336_samples.json `
  --output calibration/result.json
```

把结果中的 `camera_serial` 和 `T_flange_camera` 复制到 `config.json.calibration`。矩阵表示彩色相机坐标到法兰坐标的变换，平移单位为 mm。

求解成功不等于标定通过。必须用没有参加标定的机械臂位姿和已知固定点做独立验证，记录相机计算的基座点与实测点误差。误差满足实际抓取要求后，才把 `calibration.validated` 改为 `true`。相机、支架、转接板、夹爪或 TCP 定义发生变化后重新标定。

## 10. 分阶段联调顺序

### 阶段 A：纯软件

1. 完整安装依赖。
2. 运行 11 项离线测试并全部通过。
3. 确认 `products.pt` 能加载且类别表一致。

### 阶段 B：只接相机

1. 完成 Orbbec Windows 环境注册。
2. 相机诊断成功。
3. 检查彩色图清晰、深度有效、彩色和深度对齐。
4. 把商品放在相机当前视野，执行视觉模式：

```powershell
.\.venv\Scripts\python.exe -m xarm_grasp --item 雪碧 --output runs/sprite_vision
```

此模式不连接机械臂，也不会自动去观察位姿。检查 `detection.jpg`、`depth_mm.npy` 和 `result.json`。

### 阶段 C：只读机械臂和夹爪单测

1. ping 控制器并用 xArm Studio 检查状态。
2. 使用 `teach` 读取关节值和 TCP。
3. 在 Studio 中单独验证夹爪张开、闭合、接触检测。
4. 设置并核对 TCP 与负载。

### 阶段 D：标定与静态精度验证

1. 固定相机和标定板。
2. 采集并求解手眼标定。
3. 使用独立点验证三维坐标误差。
4. 填写相机序列号、标定矩阵、TCP 和工作范围。

### 阶段 E：逐段低速运动验证

当前程序没有 `--plan-only` 或逐步确认模式。接手同学在首次真实抓取前，优先增加一种安全调试方式：到达观察位后只识别并保存 Y/Z/X 计划，不执行对齐和夹持；或让操作者对每一段动作单独确认。完成该功能后，再逐段验证 Y 左右、Z 上下、X 深入、Z 抬起和 X 退出。

首次测试要清空路径周围障碍，使用低速度和低加速度，操作者手持急停并观察整条路径。程序只校验目标 TCP 点、工作范围和逆解，不覆盖连杆、腕部相机、夹爪与货架碰撞。

### 阶段 F：真实抓取

所有 `null` 实测参数填完并验收后，才设置：

```json
"motion_enabled": true
```

执行指定商品：

```powershell
.\.venv\Scripts\python.exe -m xarm_grasp --item 雪碧 --execute --output runs/sprite_001
```

`--execute` 会真实移动机械臂和夹爪。异常时程序停止机器人并断开，不会自动清错、自动重试或自动松开物体。

## 11. 接手同学接下来要完成的任务

按优先级执行：

1. **P0：完成新电脑部署。** 安装依赖，运行离线测试，相机诊断通过，控制器网络连通。
2. **P0：确认实际第 5 种商品。** 明确是 class 8/标签 75 的名仁苏打水饮料，还是模型尚不包含的商品编号 63 小苏打；必要时更换模型和配置。
3. **P0：测量并设置工具。** 在 Studio 设置 Robotiq TCP、负载和 Modbus，填入 `robot.tcp_offset`。
4. **P0：完成手眼标定。** 采集 15–25 组，求解并做独立点位误差验证。
5. **P0：示教观察位姿。** 为六种商品填写关节或 TCP 观察位，并现场验证工具 Y/Z/X 方向。
6. **P0：测量安全参数。** 填写 `workspace_mm` 和每个商品的 `grasp_depth_offset_mm`，调整夹爪开闭位置。
7. **P0：增加计划预览或逐段确认。** 在第一次自动抓取前提供只计算、不抓取的运动计划调试入口。
8. **P0：逐段低速验收。** 观察位、Y 对齐、Z 对齐、X 深入、夹持、Z 抬起、X 退出分别验证。
9. **P1：补充放置动作。** 目前抓取后保持瓶子，需要另行定义放置位、释放和返回策略。
10. **P1：提升现场安全。** 若在货架内长期运行，应加入完整碰撞模型、路径规划、恢复流程、运行日志和明确的人工复位步骤。

## 12. 首次启用前验收清单

- [ ] 新电脑虚拟环境重新创建，依赖安装完整。
- [ ] 11 项离线测试通过。
- [ ] `products.pt` 已单独复制，哈希或文件大小与交付源一致。
- [ ] Gemini 336 型号、序列号、RGB-D 对齐和深度单位验证通过。
- [ ] xArm 控制器 `192.168.1.209` 可达。
- [ ] Robotiq 2F-85 Modbus、开闭和接触状态验证通过。
- [ ] 控制器 TCP 与 `config.json.robot.tcp_offset` 完全一致。
- [ ] 手眼标定使用独立点验证通过，随后才设置 `validated: true`。
- [ ] 六种商品的模型类别、别名、观察位姿和抓取深度已确认。
- [ ] 工具 Y=左右、Z=上下、X=深入/退出已经小步点动确认。
- [ ] 工作范围来自现场安全边界测量。
- [ ] 每一段路径已低速检查，相机、夹爪、腕部和连杆不会碰撞。
- [ ] 操作者知道急停位置，首次执行期间全程在场。
- [ ] 所有条件通过后才设置 `motion_enabled: true` 并使用 `--execute`。

## 13. 主要代码入口

- `xarm_grasp/__main__.py`：商品查找、视觉与完整抓取流程。
- `xarm_grasp/camera.py`：Gemini 336 彩色/深度流、格式转换和 D2C 对齐。
- `xarm_grasp/check_camera.py`：不连接机器人地检查相机。
- `xarm_grasp/calibrate.py`：眼在手上标定采集与求解。
- `xarm_grasp/teach.py`：只读获取观察位姿。
- `xarm_grasp/geometry.py`：2D 到 3D、坐标链和工具轴运动计划。
- `xarm_grasp/robot.py`：xArm 与 Robotiq 通信、运动和状态检查。
- `config.json`：硬件、安全、商品和动作参数。
- `tests/`：不连接硬件的回归测试。

## 14. 交接时已知的设备情况

原电脑 Windows 能枚举到 Gemini 336 的 RGB Camera 和 Depth Camera，设备 VID/PID 为 `2BC5:0803`。`pyorbbecsdk2` 可以导入，官方环境脚本也已写入 UVC metadata；但原电脑在脚本执行后尚未完成拔插或重启复测，曾出现 `MFCreateDeviceSource` 与 control transfer 错误。因此不能把“原电脑相机实机采集已通过”作为交付结论，新电脑仍须按第 5 节完整验证。

最后，配置中的 `null` 是有意的安全门禁，不应使用估计值批量替换。每一个值都应有现场测量或标定记录。


