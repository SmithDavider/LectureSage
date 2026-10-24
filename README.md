# 课迹 LectureSage

[简体中文](README.md) | [English](README.en.md)

课迹是一款面向个人学习的课堂记录工具。Android 端负责麦克风录音、屏幕采集和上传；服务端负责语音转写、课件 OCR、字幕推送、内容校正，以及课程总结、分节小结和思维导图生成。

## 主要功能

- Android 麦克风录音和 MediaProjection 屏幕采集
- 音频与截图断点缓存、失败重试
- SenseVoice 实时转写和课后重转写
- PaddleOCR 页码、标题和正文识别
- WebSocket 推送字幕、课件时间轴和任务状态
- OpenAI 兼容大模型接口
- 基于 OCR 证据的转写校正
- 课程总结、分节小结和可编辑思维导图
- Markdown 和 OPML 导出

## 架构

```text
Android App (Kotlin)
  |  HTTP REST / WebSocket
  v
FastAPI 服务端
  |-- SQLite + 文件存储
  |-- SenseVoice ONNX
  |-- PaddleOCR / OpenVINO
  `-- OpenAI 兼容大模型接口
```

手机端不包含 ASR、OCR 或大语言模型。服务器地址由用户首次登录时填写，并保存在本机设置中；源码和 APK 不内置任何私有服务器地址。

## 目录

```text
app/                Android 客户端
server/             FastAPI 服务端
docs/               接口与设计文档
scripts/            构建脚本
gradle/             Gradle Wrapper
```

## Android 构建

环境要求：

- JDK 21
- Android SDK Platform 35
- Android SDK Build-Tools 36.0.0

Linux / macOS：

```bash
./gradlew :app:assembleDebug
```

Windows：

```powershell
.\gradlew.bat :app:assembleDebug
```

输出：

```text
app/build/outputs/apk/debug/app-debug.apk
```

首次打开 App 时需要填写自己的服务端地址、用户名和密码。服务器地址不会写入源码或 APK 默认配置。

## 服务端运行

建议使用 Python 3.11 或更高版本：

```bash
cd server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

初始化服务端账号：

```bash
python scripts/create_user.py <username> <password>
```

ffmpeg、SenseVoice 模型、标点模型和 OCR 模型需要按 `server/.env.example` 中的变量单独安装和配置。模型文件不包含在本仓库中。

## 管理后台

服务端包含一个基于 Web 的管理后台，默认入口为：

```text
http://<server-host>:8000/admin
```

管理员身份目前按用户名 `admin` 判定。首次使用前可以创建管理员账号：

```bash
python scripts/create_user.py admin <password>
```

管理后台支持：

- 查看、创建和删除用户
- 查看指定用户的课程列表
- 查看课程详情、转写、音频分段、截图和后台任务
- 查看总体及按用户统计的上传、下载流量
- 查看最近 50 条流量记录

管理后台能够访问用户课程数据和转写内容，应仅部署在受信任网络中，并通过 HTTPS 提供服务。

## 模型准备

本仓库只提供模型接入代码和配置方法，不提交模型权重、词表、量化文件或推理缓存。模型体积较大，并可能适用不同的开源许可证，请从模型发布方下载并自行确认许可条款。

### SenseVoice

1. 从 SenseVoice 或 FunASR 的官方模型仓库获取 `SenseVoiceSmall`。
2. 将模型目录放在仓库以外的位置，例如：

```text
/opt/keji/models/SenseVoiceSmall
```

3. 安装服务端依赖并配置：

```text
KEJI_SENSEVOICE_ENABLED=true
KEJI_SENSEVOICE_MODEL=/opt/keji/models/SenseVoiceSmall
KEJI_SENSEVOICE_PUNC_MODEL=iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch
```

标点模型可以由 FunASR 或 ModelScope 在首次运行时下载到本地缓存。请确保服务进程对缓存目录具有读写权限。

### PaddleOCR

本项目通过 PaddleOCR/PaddleX 接口加载 OCR 模型。推荐使用 `PP-OCRv6_medium`，也可以按硬件性能选择其他兼容模型：

```text
KEJI_OCR_MODEL=PP-OCRv6_medium
KEJI_OCR_ENABLE_HPI=true
KEJI_OCR_CPU_THREADS=2
```

PaddleX HPI 模式会在模型缓存目录中准备模型。生产环境建议预先下载，并确保模型缓存目录不位于 Git 仓库内。

### 本地转写校正模型

可选的 Qwen 校正服务使用 GGUF 量化模型。请从模型发布方下载对应 GGUF 文件，并在独立部署目录中启动推理服务：

```text
KEJI_CORRECTION_LLM_ENABLED=true
KEJI_CORRECTION_LLM_BASE_URL=http://127.0.0.1:8081/v1
KEJI_CORRECTION_LLM_MODEL=<model-name>
```

模型路径、服务启动参数和许可证说明应由部署者维护，不要写入本仓库。

## 接口文档

接口定义见 [docs/API_CONTRACT.md](docs/API_CONTRACT.md)。

## 数据与隐私

本项目的识别和生成能力依赖用户自行部署的服务端。使用者应负责：

- 获得课堂录音和截图所需的授权。
- 选择可信的大模型服务。
- 妥善保护服务端数据目录和访问凭据。
- 定期清理不再需要的课堂音频、截图和文字记录。
