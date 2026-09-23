# AI 答题控制台

通过 ADB 获取 Android 手机屏幕，使用 RapidOCR 识别文字，调用 DeepSeek API 判断答案并执行带随机误差的点击（模拟人类点击）。
目前的速度较慢，不知道为什么，后续再慢慢更新该项目
目前已测试知到app，只对选择题，多选题，判断题做了测试，其他类型的题目将不会完成答题操作

## 项目说明

本项目仅供学习使用，无其他用途，请勿消极学业，使用本项目产生的后果请自行承担。

## 如何下载

```
git clone https:/github.com/qincnd/aiQuestion.git
pip install -r requirements.txt
python main.py
```
或者在releases处下载压缩包

## Windows 启动

直接执行目录内的 RUN.exe 

启动后会自动打开 http://127.0.0.1:5050 。如果无法运行，可以直接使用 `.venv\Scripts\python.exe main.py`。

## 运行依赖

1. 安装 Android SDK Platform Tools，把 `adb.exe` 及其 DLL 放入 `tools/`(已集成在目录内)，或把 adb 加入 PATH。
2. 手机上开启开发者选项和 USB 调试，连接后执行 `adb devices` 并在手机上授权。
3. OCR 使用 RapidOCR，首次运行会加载 ONNX 模型，不需要安装 Tesseract 或配置语言包。
4. 页面填入 DeepSeek API Key。默认模型为 `deepseek-flash`；图片模式必须使用支持视觉输入的模型。

### 配置说明

页面配置的时间均为秒，误差时间会同时作用于等待和间隔；答题等待和题目间隔采用截止时间计时，不会因后台预识别重复叠加。页面加载上限用于防止卡在旧画面，截图轮询间隔越小响应越快但设备负载越高。点击误差距离为随机圆形半径，单位是屏幕像素。模型需要返回题型和 `clicks` 坐标数组，多选题会逐项点击。默认使用 OCR 文本；开启图片模式后，会把截图作为视觉输入发送给支持图片的模型。

检测到提交/交卷页面后，程序只会停止并按开关发送 Windows 通知，不会替用户点击提交。页面配置和 API Key 会保存到项目根目录的 `config.json`，其中 API Key 为明文，请勿将该文件提交到 Git 或分享给他人，注意隐私保护，防止大肥鱼的token不翼而飞。
