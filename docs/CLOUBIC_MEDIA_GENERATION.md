# Cloubic 图像和视频生成集成

本项目已集成 Cloubic 统一 AI 模型接入平台的图像和视频生成功能。

## 功能特性

- ✅ 图像生成（DALL-E 3, GPT Image 1, Gemini, Qwen 等）
- ✅ 图像编辑（多图合成）
- ✅ 视频生成（Sora, Veo, Kling, Vidu 等）
- ✅ 异步视频生成与轮询
- ✅ 视频下载
- ✅ 并发控制和速率限制
- ✅ 自动重试机制

## 环境配置

### 1. 注册 Cloubic 账号

访问 [Cloubic](https://cloubic.com/) 注册账号并获取 API Key。

### 2. 设置环境变量

```bash
# 基础配置
export AI_API_BASE_URL="https://api.cloubic.com/v1"
export AI_API_KEY="your_cloubic_api_key"

# 图像生成模型
export AI_API_MODEL="gpt-image-1"  # 或 "dall-e-3"

# 视频生成模型
export AI_API_MODEL="sora-2"  # 或 "veo-2", "kling-1.6" 等

# 可选配置
export AI_API_TIMEOUT="300"  # 超时时间（秒）
export AI_API_MAX_CONCURRENCY="2"  # 最大并发数（1-4）
export AI_API_MIN_INTERVAL_MS="1000"  # 最小请求间隔（毫秒）
```

## 使用示例

### 图像生成

```python
from src.util.llm import call_ai_image_generation

# 生成图片
result = call_ai_image_generation(
    prompt="一只可爱的橘猫在阳光下打盹",
    n=1,
    quality="hd",  # "standard" 或 "hd"
    size="1024x1024",  # 图片尺寸
    response_format="url"  # "url" 或 "b64_json"
)

# 获取图片 URL
image_url = result["data"][0]["url"]
print(f"图片 URL: {image_url}")
```

### 图像编辑

```python
from src.util.llm import call_ai_image_edit

# 编辑图片
result = call_ai_image_edit(
    prompt="将这些物品组合成一个精美的礼品篮",
    image_paths=[
        "/path/to/image1.png",
        "/path/to/image2.png"
    ],
    quality="high",
    size="1024x1024"
)

edited_url = result["data"][0]["url"]
```

### 视频生成（文生视频）

```python
from src.util.llm import (
    call_ai_video_generation,
    wait_for_video_completion,
    download_ai_video
)

# 1. 创建视频生成任务
result = call_ai_video_generation(
    prompt="一只猫在草地上追逐蝴蝶，阳光明媚",
    seconds=4,  # 4, 8, 或 12 秒
    size="1280x720"  # 分辨率
)

video_id = result["id"]

# 2. 等待视频生成完成
completed_info = wait_for_video_completion(
    video_id=video_id,
    poll_interval=5,  # 轮询间隔（秒）
    max_wait_time=600  # 最大等待时间（秒）
)

# 3. 下载视频
download_ai_video(video_id, "/tmp/generated_video.mp4")
```

### 视频生成（图生视频）

```python
# 从图片生成视频
result = call_ai_video_generation(
    prompt="让这张图片动起来，添加微风吹拂的效果",
    seconds=8,
    size="1280x720",
    input_reference="/path/to/input_image.jpg"  # 输入图片
)
```

### 手动轮询视频状态

```python
from src.util.llm import get_ai_video_status

# 查询视频状态
status_info = get_ai_video_status(video_id)

print(f"状态: {status_info['status']}")  # processing, completed, failed
print(f"进度: {status_info['progress']}%")

if status_info["status"] == "completed":
    print(f"视频 URL: {status_info['url']}")
```

### 使用自定义配置

```python
from src.util.llm import AIAPIConfig, call_ai_image_generation

# 创建自定义配置
config = AIAPIConfig(
    base_url="https://api.cloubic.com/v1",
    api_key="your_api_key",
    model="dall-e-3",
    timeout=300,
    max_concurrency=2
)

# 使用自定义配置
result = call_ai_image_generation(
    prompt="未来城市的夜景",
    config=config,
    quality="hd"
)
```

## 支持的模型

### 图像生成模型

| 模型 | 说明 |
|------|------|
| `gpt-image-1` | GPT Image 1 多模态模型 |
| `dall-e-3` | OpenAI DALL·E 3 |
| Gemini 系列 | Google Gemini 图像生成 |
| Qwen/Wan 系列 | 通义万相等国产模型 |
| `seedream` | 豆包 Seedream |

### 视频生成模型

| 模型 | 说明 |
|------|------|
| `sora-2` | OpenAI Sora |
| `veo-2` | Google Veo |
| `kling-1.6` | 快手可灵 |
| `vidu` | 生数科技 Vidu |
| `hailuo` | MiniMax 海螺 |
| `jimeng` | 即梦 AI |
| `wan` | 万兴 AI |

## 图片尺寸选项

### 图像生成
- `1024x1024` (正方形)
- `1792x1024` (横向)
- `1024x1792` (纵向)

### 视频生成
- `720x1280` (竖屏)
- `1280x720` (横屏)
- `1024x1792` (竖屏高清)
- `1792x1024` (横屏高清)

## API 响应格式

### 图像生成响应

```json
{
  "created": 1766308943,
  "data": [
    {
      "url": "https://...",
      "b64_json": "",
      "revised_prompt": ""
    }
  ],
  "usage": {
    "input_tokens": 52,
    "output_tokens": 4160,
    "total_tokens": 4212
  }
}
```

### 视频生成响应

```json
{
  "id": "video_abc123",
  "object": "video",
  "model": "sora-2",
  "created_at": 1766308943,
  "status": "processing",
  "progress": 45
}
```

### 视频完成响应

```json
{
  "id": "video_abc123",
  "status": "completed",
  "url": "https://...",
  "size": "1280x720",
  "seconds": "4",
  "quality": "high",
  "expires_at": 1766395343
}
```

## 错误处理

所有函数在出错时会抛出 `AIAPIError` 异常：

```python
from src.util.llm import AIAPIError, call_ai_image_generation

try:
    result = call_ai_image_generation(
        prompt="生成一张图片",
        quality="hd"
    )
except AIAPIError as e:
    print(f"生成失败: {e}")
```

## 并发控制

代码自动处理并发控制和速率限制：

- 使用信号量控制最大并发数（通过 `AI_API_MAX_CONCURRENCY` 设置）
- 自动限制请求间隔（通过 `AI_API_MIN_INTERVAL_MS` 设置）
- 客户端实例缓存和复用

## 完整示例

查看 [examples/cloubic_media_generation_example.py](examples/cloubic_media_generation_example.py) 获取完整的使用示例。

## 注意事项

1. **视频生成是异步的**：需要轮询状态或使用 `wait_for_video_completion` 等待完成
2. **视频 URL 有过期时间**：生成后应及时下载
3. **并发限制**：建议设置合理的 `max_concurrency` 避免触发 API 限流
4. **成本控制**：图像和视频生成会消耗较多 token，注意成本控制

## 相关链接

- [Cloubic 官网](https://cloubic.com/)
- [Cloubic 文档](https://docs.cloubic.com/docs/zh-CN)
- [模型广场](https://app.cloubic.com/models)
