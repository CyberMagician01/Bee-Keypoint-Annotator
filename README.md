# 蜜蜂关键点标注器

由我们团队自主开发并开源的蜜蜂标注与人工复核软件，采用 [MIT 许可证](./LICENSE)。

面向蜜蜂 `head` / `tail` 关键点标注与复审的 Windows 桌面工具。软件直接读取 X-AnyLabeling / LabelMe JSON 中已有的检测框，支持 Track ID 复审、关键点传播、中心对称辅助和检测框调整。

当前版本：`v1.8.18`

## 获取软件

普通使用者建议从 GitHub Releases 下载 `Bee-Keypoint-Annotator-v1.8.18-Windows.zip`，完整解压后双击 `蜜蜂关键点标注器.exe`，无需安装 Python。

## 从源码运行

```powershell
pip install -r requirements.txt
python app.py
```

也可以双击 `run_keypoint_annotator.bat`。

## 数据要求

打开包含图片与同名 JSON 的任务文件夹，例如：

```text
区段_04/
├── B-5-3_frame_004465.jpg
├── B-5-3_frame_004465.json
├── B-5-3_frame_004470.jpg
└── B-5-3_frame_004470.json
```

JSON 中应包含矩形检测框；如果需要按轨迹复审，矩形还应带有 Track ID。

## 常用操作

- 左键短按：标注当前关键点；开启中心对称后会自动建议另一个关键点。
- 右键：标注 `tail`。
- 鼠标中键：删除附近关键点。
- 鼠标滚轮：切换检测框。
- 长按框内部：移动检测框，关键点随框移动。
- 长按框的八个控制点：调整框大小，关键点保持图像绝对位置。

## 常用快捷键

| 快捷键 | 功能 |
| --- | --- |
| `Z` / `C` | 上一张 / 下一张图片 |
| `Q` / `E` | 上一个 / 下一个检测框 |
| `A` / `D` | 同一 Track ID 的上一帧 / 下一帧 |
| `W` | 下一个 Track ID |
| `R` | 将当前帧关键点按 Track ID 复制到下一帧 |
| `Ctrl+P` | Track ID 传播 |
| `Space` | 确认当前框关键点 |
| `G` | 打开本帧或 Track ID 清单 |
| `X` | 交换 `head` / `tail` |
| `N` | 下一个待检查或异常框 |
| `Ctrl+S` | 保存 |
| `Ctrl+Z` | 撤销 |
| `F1` | 打开完整帮助 |

快捷键可以在软件的“快捷键”窗口中自定义。完整说明见 [`发布使用说明.txt`](./发布使用说明.txt)。

## 测试

```powershell
python -m unittest discover -s tests -v
```

## 团队工具与数据飞轮

本工具用于头尾关键点补标、方向复核和同 ID 跨帧检查；身份合并与轨迹逐条复查可配合团队开发的 [蜜蜂 Track ID 修正器](https://github.com/CyberMagician01/Bee-TrackID-Corrector)。两者支持人工修正模型候选，修正后的标注可交回数据飞轮汇总。

## 开源许可与依赖

本仓库的软件源码及随附说明采用 [MIT 许可证](./LICENSE)，欢迎复用、修改和贡献改进。软件通过 JSON 格式与 X-AnyLabeling、LabelMe 交换标注，这两个外部项目各自由其开发者维护。

界面使用 Python 标准库 Tkinter，图像处理使用 Pillow，Windows 集成功能使用 pywin32；第三方依赖遵循各自许可证。比赛原视频、任务图像和标注数据由各自的数据授权管理，不随软件许可证开放。
