# 图像资产生成脚本使用说明

## 概述

`generate_image_assets.py` 脚本用于从提示词 JSON 文件自动生成角色和场景的图像资产。

## 功能特性

- ✅ 自动读取 `nano_banana_prompts_global_llm.json` 提示词文件
- ✅ 生成角色转身图（character turnaround sheets）
- ✅ 生成场景环境概念图（environment concept art）
- ✅ 支持断点续传（已存在的图像会自动跳过）
- ✅ 可配置生成延迟，避免 API 限流
- ✅ 支持 dry-run 模式预览
- ✅ 自动下载并保存生成的图像

## 前置要求

1. 确保 `config.json` 中配置了图像生成 API：
   ```json
   {
     "ai_api_image": {
       "base_url": "https://api.cloubic.com/v1",
       "api_key": "your-api-key",
       "model": "gemini-3.1-flash-image-preview",
       "timeout": 600,
       "max_concurrency": 1,
       "min_interval_ms": 1000
     }
   }
   ```

2. 安装依赖：
   ```bash
   pip install requests openai
   ```

## 基本用法

### 生成所有资产（角色 + 场景）

```bash
python src/script-converter/generate_image_assets.py \
  "output/1fc071a6_- BITE ME , ᶻᵒᵐᵇⁱᵉˢ⁴ - $ - Wattpad/adapted/rewrite/nano_banana_prompts_global_llm.json"
```

### 仅生成角色图像

```bash
python src/script-converter/generate_image_assets.py \
  "output/1fc071a6_- BITE ME , ᶻᵒᵐᵇⁱᵉˢ⁴ - $ - Wattpad/adapted/rewrite/nano_banana_prompts_global_llm.json" \
  --characters-only
```

### 仅生成场景图像

```bash
python src/script-converter/generate_image_assets.py \
  "output/1fc071a6_- BITE ME , ᶻᵒᵐᵇⁱᵉˢ⁴ - $ - Wattpad/adapted/rewrite/nano_banana_prompts_global_llm.json" \
  --scenes-only
```

### 指定输出目录

```bash
python src/script-converter/generate_image_assets.py \
  "path/to/prompts.json" \
  --output "path/to/output/assets"
```

### Dry Run（预览模式）

```bash
python src/script-converter/generate_image_assets.py \
  "path/to/prompts.json" \
  --dry-run
```

### 自定义延迟时间

```bash
python src/script-converter/generate_image_assets.py \
  "path/to/prompts.json" \
  --delay 5.0  # 每次生成间隔 5 秒
```

### 使用不同的 API 配置

```bash
python src/script-converter/generate_image_assets.py \
  "path/to/prompts.json" \
  --config-prefix "ai_api"  # 使用 ai_api 配置而不是 ai_api_image
```

## 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `prompts_file` | 提示词 JSON 文件路径（必需） | - |
| `-o, --output` | 输出目录 | `{prompts_file_dir}/assets` |
| `--characters-only` | 仅生成角色图像 | False |
| `--scenes-only` | 仅生成场景图像 | False |
| `--dry-run` | 模拟运行，不调用 API | False |
| `--delay` | 每次生成之间的延迟（秒） | 2.0 |
| `--config-prefix` | API 配置前缀 | `ai_api_image` |

## 输出结构

```
assets/
├── characters/
│   ├── character_aurora_turnaround.png
│   ├── character_caspian_turnaround.png
│   ├── character_kael_turnaround.png
│   ├── character_selene_turnaround.png
│   ├── character_orion_turnaround.png
│   ├── character_lyra_turnaround.png
│   ├── character_iris_turnaround.png
│   └── character_commander_slumber_turnaround.png
└── scenes/
    ├── scene_s01.png  # Solaris Covenant - Golden Hour Camp
    ├── scene_s02.png  # Gloomhaven - Neon Nocturne
    ├── scene_s03.png  # The Whispering Woods
    ├── scene_s04.png  # Verdant Enclave
    ├── scene_s05.png  # Campfire Circle
    ├── scene_s06.png  # Watcher's Lodge
    ├── scene_s07.png  # Caspian's Room
    ├── scene_s08.png  # Forest Stream
    ├── scene_s09.png  # The Barrier
    ├── scene_s10.png  # Ancient Ruins
    ├── scene_s11.png  # Training Grounds
    └── scene_s12.png  # Gloomhaven Streets
```

## 工作流程

1. **读取提示词文件**
   - 加载 JSON 文件
   - 提取全局风格提示词
   - 提取角色和场景库

2. **加载 API 配置**
   - 从 `config.json` 读取配置
   - 验证 API 密钥和模型

3. **生成角色图像**
   - 遍历角色库
   - 组合角色提示词 + 全局风格
   - 调用图像生成 API
   - 下载并保存图像

4. **生成场景图像**
   - 遍历场景库
   - 组合场景提示词 + 全局风格
   - 调用图像生成 API
   - 下载并保存图像

## 提示词组合逻辑

### 角色图像
```
完整提示词 = character.turnaround_prompt + global_style_prompt
```

示例：
```
"character turnaround sheet, three views (front view, side view, back view),
T-pose, full body, white background, Aurora a Daywalker, young woman,
sun-kissed skin, hazel-gold eyes, messy honey-blonde wavy hair,
cinematic concept art, fantasy realism, detailed environments,
dramatic lighting, atmospheric"
```

### 场景图像
```
完整提示词 = scene.prompt + global_style_prompt
```

示例：
```
"environment concept art, no people, a permanent Daywalker camp in a
sun-drenched forest clearing, wooden watchtowers, canvas tents,
golden hour sunlight, cinematic concept art, fantasy realism,
detailed environments, dramatic lighting, painterly textures"
```

## 断点续传

脚本会自动检查输出目录中是否已存在图像文件：
- ✅ 如果文件已存在，跳过生成
- 🎨 如果文件不存在，调用 API 生成

这意味着你可以：
- 随时中断脚本（Ctrl+C）
- 重新运行脚本继续生成剩余图像
- 不会重复生成已有的图像

## 错误处理

脚本会处理以下错误情况：
- ❌ API 调用失败：记录错误，继续下一个
- ❌ 图像下载失败：记录错误，继续下一个
- ❌ 配置文件错误：立即退出
- ❌ 提示词文件不存在：立即退出

## 性能优化建议

1. **调整延迟时间**
   - 免费 API：`--delay 5.0` 或更高
   - 付费 API：`--delay 1.0` 或 `--delay 2.0`

2. **分批生成**
   - 先生成角色：`--characters-only`
   - 再生成场景：`--scenes-only`

3. **使用 dry-run 预览**
   - 先运行 `--dry-run` 检查配置
   - 确认无误后再实际生成

## 成本估算

假设使用 DALL-E 3：
- 每张图片成本：约 $0.04 (1024x1024, standard quality)
- 8 个角色 + 12 个场景 = 20 张图片
- 总成本：约 $0.80

实际成本取决于你使用的 API 服务和定价。

## 故障排查

### 问题：API 调用失败

**可能原因**：
- API 密钥无效
- 配额不足
- 网络连接问题

**解决方法**：
1. 检查 `config.json` 中的 API 密钥
2. 确认 API 账户有足够配额
3. 测试网络连接

### 问题：图像下载失败

**可能原因**：
- 图像 URL 过期
- 网络超时

**解决方法**：
1. 增加 `timeout` 配置
2. 重新运行脚本（会跳过已成功的）

### 问题：生成的图像质量不佳

**可能原因**：
- 提示词不够详细
- 模型不适合

**解决方法**：
1. 修改 JSON 文件中的提示词
2. 尝试不同的模型（修改 `config.json`）
3. 调整 `quality` 参数为 "hd"（需修改脚本）

## 高级用法

### 批量生成多个项目

```bash
#!/bin/bash
# batch_generate.sh

for project in output/*/adapted/rewrite/nano_banana_prompts_global_llm.json; do
  echo "Processing: $project"
  python src/script-converter/generate_image_assets.py "$project" --delay 3.0
done
```

### 仅生成特定角色

修改脚本，添加角色过滤：
```python
# 在 main() 函数中
character_library = [
    c for c in prompts_data.get("character_prompt_library", [])
    if c.get("target_name") in ["Aurora", "Caspian"]
]
```

## 示例输出

```
📖 加载提示词文件: output/.../nano_banana_prompts_global_llm.json
📁 输出目录: output/.../assets
   - 角色: output/.../assets/characters
   - 场景: output/.../assets/scenes
🔧 加载 API 配置: ai_api_image
   ✓ 模型: gemini-3.1-flash-image-preview

📊 统计:
   - 角色数量: 8
   - 场景数量: 12
   - 全局风格: cinematic concept art, fantasy realism, detailed...

============================================================
🎭 开始生成角色图像
============================================================

[1/8]
  🎨 生成角色: Aurora (lead)
     提示词: character turnaround sheet, three views (front view, side view...
     ✓ 已保存: output/.../assets/characters/character_aurora_turnaround.png

[2/8]
  🎨 生成角色: Caspian (lead)
     提示词: character turnaround sheet, three views (front view, side view...
     ✓ 已保存: output/.../assets/characters/character_caspian_turnaround.png

...

✓ 角色生成完成: 成功 8, 跳过 0, 失败 0

============================================================
🌍 开始生成场景图像
============================================================

[1/12]
  🌄 生成场景: S01 - Solaris Covenant - Golden Hour Camp
     提示词: environment concept art, no people, a permanent Daywalker camp...
     ✓ 已保存: output/.../assets/scenes/scene_s01.png

...

✓ 场景生成完成: 成功 12, 跳过 0, 失败 0

============================================================
🎉 所有图像资产生成完成！
============================================================
输出目录: output/.../assets
```

## 相关文件

- 脚本：`src/script-converter/generate_image_assets.py`
- 配置：`config.json`
- LLM 工具：`src/util/llm.py`
- 提示词文件：`output/.../nano_banana_prompts_global_llm.json`
