# 课迹局域网服务接口契约

所有 REST 请求都带用户名密码认证：

```http
Authorization: Basic base64(用户名:密码)
```

基础地址例如：

```text
http://localhost:8000
https://server.example.com
```

## REST 接口

### 创建课程

```http
POST /api/v1/courses
```

请求：

```json
{
  "course_id": "client-generated-uuid",
  "title": "课程名称",
  "source": "android",
  "llm_base_url": "https://llm.example.com/v1",
  "llm_api_key": "optional-phone-configured-key",
  "llm_model": "gpt-5-mini"
}
```

`llm_base_url`、`llm_api_key`、`llm_model` 由 Android 设置页传入。若手机端未填写，则服务端回退到 `.env` 配置。服务端不得在课程详情/列表响应中返回 `llm_api_key`。

响应：

```json
{
  "course_id": "client-generated-uuid",
  "status": "recording"
}
```

更新检查接口会返回 APK 的 `size_bytes` 和 `sha256`。App 下载更新后必须校验文件大小、SHA-256 和 APK 结构，校验不通过时不得交给系统安装器。

### 上传音频分段

```http
POST /api/v1/courses/{course_id}/audio
Content-Type: multipart/form-data
```

表单字段：

- `offset_ms`: 该段在课程中的起始毫秒。
- `file`: 音频分段，支持 `audio/aac`、`audio/opus`、`audio/mp3`、`audio/wav`、`audio/mp4`（`.m4a`）等格式。
- `draft_json`: 可选，旧版手机端草稿字段，当前 Android 客户端已取消本地 ASR，不再发送；服务端仍可兼容忽略。
- `realtime`: 可选，`1` 表示课堂实时分段，服务端保存后立即用 SenseVoice 转写并通过 WebSocket 回推确认字幕；导入转写不要传。

App 的“导入音频转写”也复用该接口：先 `POST /courses` 创建课程，再把整个音频文件作为 `offset_ms=0` 的单个分段上传，最后 `POST /courses/{course_id}/finish` 触发课后转写。

### 上传屏幕帧

```http
POST /api/v1/courses/{course_id}/screens
```

表单字段：

- `offset_ms`: 截屏对应课程时间。
- `file`: JPEG 文件，`image/jpeg`。

### 结束课堂并触发重转写

```http
POST /api/v1/courses/{course_id}/finish
```

请求：

```json
{
  "duration_ms": 7200000
}
```

课后转写固定由服务端 SenseVoice ONNX 通道完成，不需要指定模型；提交后通过 WebSocket
推送 `queued -> transcribing -> transcribed` 状态。

### 触发大模型生成

```http
POST /api/v1/courses/{course_id}/generate
```

请求：

```json
{
  "model": null,
  "detail_level": "very_detailed"
}
```

### 课程列表

```http
GET /api/v1/courses
```

### 课程详情

```http
GET /api/v1/courses/{course_id}
```

### 更新可编辑内容

```http
PUT /api/v1/courses/{course_id}/content
```

请求字段：

- `title`
- `summary`
- `sections`
- `mindmap`

### 更改密码

```http
POST /api/v1/me/password
```

请求：

```json
{
  "old_password": "old-password",
  "new_password": "new-password"
}
```

响应：

```json
{
  "status": "changed"
}
```

说明：

- 使用当前用户名密码 Basic 认证。
- 服务端校验旧密码。
- 新密码至少 6 位，且不得和旧密码相同。
- 旧密码错误返回 `401`。
- 成功后用户需用新密码重新认证；App 应更新本地保存的密码。

## WebSocket

```text
GET /api/v1/courses/{course_id}/stream
```

WebSocket 使用查询参数认证：

```text
GET /api/v1/courses/{course_id}/stream?username=<用户名>&password=<密码>
```

服务端推送：

```json
{
  "type": "subtitle",
  "subtitle": {
    "id": "line-id",
    "start_ms": 0,
    "end_ms": 4200,
    "text": "老师讲到的内容",
    "original_text": null,
    "speaker": null,
    "page": null,
    "confirmed": false,
    "review_state": "confirmed"
  }
}
```

当 `confirmed: true` 时，该字幕来自服务端 SenseVoice 实时确认；App 只展示服务端推送的字幕，不再生成或合并本地草稿。

字段约定：

- `text`：始终是最终展示文本。
- `original_text`：可选；只有 `review_state=llm_corrected` 时存在，表示 LLM 校正前的原文。
- `review_state`：当前为 `none/pending/confirmed/needs_correction/llm_corrected`，App 只区分“服务端确认”与“待复核/校正”。
- 手机端应忽略未知或可选字段，不能因为缺少 `original_text` 而失败。

课后转写（`POST /courses/{id}/generate` 前完成的校正）也会把 `original_text` 与 `review_state=llm_corrected` 写入每条转写 JSON；App 的转写解析只使用 `text` 与 `start_ms/end_ms`。

页码/标题时间轴：

```json
{
  "type": "timeline",
  "timeline": {
    "timestamp_ms": 12000,
    "page": 5,
    "title": "卷积神经网络",
    "hash": "optional-frame-hash"
  }
}
```

状态：

```json
{
  "type": "status",
  "status": "transcribing",
  "message": "60%"
}
```

## 约定

- `course_id` 由 Android 端生成，服务端必须接受该 ID 作为主键。
- 音频和截屏上传失败时，Android 端会重试，所以接口必须保证同一分段幂等。
- 课后重转写不阻塞原始数据保存，任务状态通过 WebSocket 推送。
- 第一版不要求说话人分离，但 `SubtitleLine.speaker` 已预留。
