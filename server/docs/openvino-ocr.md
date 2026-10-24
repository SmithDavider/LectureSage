# OpenVINO OCR 加速部署

课迹服务端的截图 OCR 在 Intel CPU 服务器上默认使用 PaddleX HPI + OpenVINO
加速 PP-OCRv6_medium 的检测和识别模型。

## 依赖

PaddleX 的 HPI 运行时（`ultra-infer-python`）目前只发布 Linux x86_64
预编译包，且 HPI 后端与 PaddlePaddle 3.0/3.1 配套。生产部署建议：

```bash
python -m venv /opt/keji-server/venv
source /opt/keji-server/venv/bin/activate
pip install -r server/requirements.txt
paddlex --install "hpi" --device cpu
```

`paddlex --install hpi` 会按 PaddleX 版本自动选择配套的
`ultra-infer-python` wheel。HPI 当前要求 PaddlePaddle 3.1.x，因此
`server/requirements.txt` 已将 `paddlepaddle` 固定为 3.1.x 系列。

## 环境变量

默认配置已启用 HPI：

```env
KEJI_OCR_ENABLE_HPI=true
```

如需临时或长期回退到普通 Paddle 引擎（本机 Windows 验证等场景）：

```env
KEJI_OCR_ENABLE_HPI=false
```

模型缓存默认写入 `~/.paddlex`，可通过环境变量调整：

```bash
export PADDLE_PDX_CACHE_HOME=/var/lib/keji/.paddlex-cache
```

## 验证

首次启动会自动下载 PP-OCRv6_medium 检测/识别模型，并在服务启动时预热。
启动后上传一张截图，检查日志中出现 `Inference backend: openvino`，以及
截图记录的 `ocr_status=ok` 和识别出的页码/标题。
