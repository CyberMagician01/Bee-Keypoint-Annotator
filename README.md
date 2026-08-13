# 蜜蜂关键点标注器

面向蜜蜂 `head` / `tail` 关键点标注与复审的 Windows 桌面工具。软件直接读取 X-AnyLabeling / LabelMe JSON 中已有的检测框，支持 Track ID 复审、关键点传播、中心对称辅助和检测框调整。

当前版本：`v1.7.4`

## 获取软件

普通使用者建议从 GitHub Releases 下载 `Bee-Keypoint-Annotator-v1.7.4-Windows.zip`，完整解压后双击 `蜜蜂关键点标注器.exe`，无需安装 Python。

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
