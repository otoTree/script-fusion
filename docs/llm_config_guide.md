# LLM API 配置说明

## 配置文件位置

配置文件位于项目根目录：`config.json`

## 配置结构

```json
{
  "配置名称": {
    "base_url": "API 服务地址",
    "api_key": "API 密钥",
    "model": "模型名称",
    "timeout": 超时时间（秒）,
    "max_concurrency": 最大并发数,
    "min_interval_ms": 最小请求间隔（毫秒）
  }
}
```

## 配置参数说明

### base_url
- **类型**: 字符串
- **必填**: 是
- **说明**: API 服务的基础 URL，不包含尾部斜杠
- **示例**:
  - OpenAI: `https://api.openai.com/v1`
  - Claude: `https://api.anthropic.com/v1`
  - 自定义服务: `https://your-api-server.com/v1`

### api_key
- **类型**: 字符串
- **必填**: 是
- **说明**: API 访问密钥
- **示例**:
  - OpenAI: `sk-proj-...`
  - Claude: `sk-ant-...`

### model
- **类型**: 字符串
- **必填**: 是
- **说明**: 要使用的模型名称
- **示例**:
  - GPT-4: `gpt-4-turbo-preview`, `gpt-4`, `gpt-4-32k`
  - GPT-3.5: `gpt-3.5-turbo`, `gpt-3.5-turbo-16k`
  - Claude: `claude-3-5-sonnet-20241022`, `claude-3-opus-20240229`
  - DALL-E: `dall-e-3`, `dall-e-2`

### timeout
- **类型**: 整数
- **必填**: 否
- **默认值**: 300
- **说明**: 请求超时时间（秒）
- **建议值**:
  - 聊天完成: 300 秒
  - 图片生成: 600 秒
  - 视频生成: 900 秒

### max_concurrency
- **类型**: 整数
- **必填**: 否
- **默认值**: 1
- **范围**: 1-4
- **说明**: 同时允许的最大并发请求数
- **建议值**:
  - 免费账户: 1
  - 付费账户: 2-3
  - 企业账户: 3-4

### min_interval_ms
- **类型**: 数字（整数或浮点数）
- **必填**: 否
- **默认值**: 1
- **说明**: 两次 API 请求之间的最小间隔时间（毫秒）
- **建议值**:
  - 无限制: 1
  - 避免限流: 100-500
  - 严格限流: 1000-2000

## 配置示例

### 基础配置（OpenAI GPT-4）

```json
{
  "ai_api": {
    "base_url": "https://api.openai.com/v1",
    "api_key": "sk-proj-your-key-here",
    "model": "gpt-4-turbo-preview",
    "timeout": 300,
    "max_concurrency": 2,
    "min_interval_ms": 100
  }
}
```

### 图片生成配置（DALL-E 3）

```json
{
  "ai_api_image": {
    "base_url": "https://api.openai.com/v1",
    "api_key": "sk-proj-your-key-here",
    "model": "dall-e-3",
    "timeout": 600,
    "max_concurrency": 1,
    "min_interval_ms": 1000
  }
}
```

### Claude 配置

```json
{
  "ai_api_claude": {
    "base_url": "https://api.anthropic.com/v1",
    "api_key": "sk-ant-your-key-here",
    "model": "claude-3-5-sonnet-20241022",
    "timeout": 300,
    "max_concurrency": 3,
    "min_interval_ms": 50
  }
}
```

### 本地/自托管服务配置

```json
{
  "ai_api_local": {
    "base_url": "http://localhost:8000/v1",
    "api_key": "local-key",
    "model": "llama-3-70b",
    "timeout": 600,
    "max_concurrency": 4,
    "min_interval_ms": 1
  }
}
```

## 使用方法

### 1. 使用默认配置

```python
from src.util.llm import call_ai_chat_completion

# 自动加载 config.json 中的 "ai_api" 配置
result = call_ai_chat_completion(
    messages=[{"role": "user", "content": "Hello"}]
)
```

### 2. 使用指定配置

```python
from src.util.llm import load_ai_api_config, call_ai_chat_completion

# 加载 config.json 中的 "ai_api_claude" 配置
config = load_ai_api_config(prefix="ai_api_claude")

result = call_ai_chat_completion(
    messages=[{"role": "user", "content": "Hello"}],
    config=config
)
```

### 3. 使用自定义配置文件

```python
from src.util.llm import load_ai_api_config, call_ai_chat_completion

# 从指定路径加载配置
config = load_ai_api_config(
    prefix="ai_api",
    config_path="/path/to/custom-config.json"
)

result = call_ai_chat_completion(
    messages=[{"role": "user", "content": "Hello"}],
    config=config
)
```

### 4. 手动创建配置

```python
from src.util.llm import AIAPIConfig, call_ai_chat_completion

# 手动创建配置对象
config = AIAPIConfig(
    base_url="https://api.openai.com/v1",
    api_key="sk-your-key",
    model="gpt-4",
    timeout=300,
    max_concurrency=2
)

result = call_ai_chat_completion(
    messages=[{"role": "user", "content": "Hello"}],
    config=config
)
```

## 多配置管理

可以在同一个 `config.json` 中配置多个不同的 API：

```json
{
  "ai_api": {
    "base_url": "https://api.openai.com/v1",
    "api_key": "sk-proj-...",
    "model": "gpt-4-turbo-preview",
    "timeout": 300,
    "max_concurrency": 2,
    "min_interval_ms": 100
  },
  "ai_api_fast": {
    "base_url": "https://api.openai.com/v1",
    "api_key": "sk-proj-...",
    "model": "gpt-3.5-turbo",
    "timeout": 120,
    "max_concurrency": 4,
    "min_interval_ms": 50
  },
  "ai_api_image": {
    "base_url": "https://api.openai.com/v1",
    "api_key": "sk-proj-...",
    "model": "dall-e-3",
    "timeout": 600,
    "max_concurrency": 1,
    "min_interval_ms": 1000
  }
}
```

然后根据需要选择不同的配置：

```python
# 使用 GPT-4（默认）
config_gpt4 = load_ai_api_config(prefix="ai_api")

# 使用 GPT-3.5（快速）
config_fast = load_ai_api_config(prefix="ai_api_fast")

# 使用 DALL-E（图片生成）
config_image = load_ai_api_config(prefix="ai_api_image")
```

## 安全建议

1. **不要提交 API 密钥到版本控制**
   - 将 `config.json` 添加到 `.gitignore`
   - 提供 `config.json.example` 作为模板

2. **使用环境变量（可选）**
   - 虽然当前实现使用 JSON 配置，但可以考虑从环境变量读取敏感信息

3. **限制文件权限**
   ```bash
   chmod 600 config.json
   ```

4. **定期轮换 API 密钥**

## 故障排查

### 配置文件不存在
```
AIAPIError: 配置文件不存在: /path/to/config.json
```
**解决方法**: 确保 `config.json` 存在于项目根目录

### 配置项缺失
```
AIAPIError: 配置文件中缺少 'ai_api' 配置项
```
**解决方法**: 检查 `config.json` 中是否有对应的配置项

### 参数类型错误
```
AIAPIError: 配置项 'ai_api.timeout' 必须是整数
```
**解决方法**: 确保配置值的类型正确（timeout 和 max_concurrency 必须是整数）

### 并发数超出范围
```
AIAPIError: 配置项 'ai_api.max_concurrency' 必须在 1~4 之间: 5
```
**解决方法**: 将 max_concurrency 设置为 1-4 之间的值
