# 图像资产生成工具

自动从提示词文件生成角色和场景的图像资产。

## 快速开始

### 1. 配置 API

编辑 `config.json`，确保 `ai_api_image` 配置正确：

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

### 2. 运行生成脚本

#### 使用便捷脚本（推荐）

```bash
# 预览模式（不实际调用 API）
./generate_assets.sh --dry-run

# 生成所有资产（角色 + 场景）
./generate_assets.sh

# 仅生成角色
./generate_assets.sh --characters-only

# 仅生成场景
./generate_assets.sh --scenes-only

# 自定义延迟（避免限流）
./generate_assets.sh --delay 5.0
```

#### 直接使用 Python 脚本

```bash
# 基本用法
python3 src/script-converter/generate_image_assets.py \
  "output/1fc071a6_- BITE ME , ᶻᵒᵐᵇⁱᵉˢ⁴ - $ - Wattpad/adapted/rewrite/nano_banana_prompts_global_llm.json"

# 仅生成角色
python3 src/script-converter/generate_image_assets.py \
  "path/to/prompts.json" \
  --characters-only

# 仅生成场景
python3 src/script-converter/generate_image_assets.py \
  "path/to/prompts.json" \
  --scenes-only

# 指定输出目录
python3 src/script-converter/generate_image_assets.py \
  "path/to/prompts.json" \
  --output "custom/output/dir"

# Dry run（预览）
python3 src/script-converter/generate_image_assets.py \
  "path/to/prompts.json" \
  --dry-run
```

## 输出结构

生成的图像会保存在以下结构中：

```
output/.../adapted/rewrite/assets/
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

## 功能特性

- ✅ 自动读取提示词 JSON 文件
- ✅ 生成角色转身图（character turnaround sheets）
- ✅ 生成场景环境概念图（environment concept art）
- ✅ 断点续传（已存在的图像自动跳过）
- ✅ 可配置生成延迟（避免 API 限流）
- ✅ Dry-run 模式预览
- ✅ 自动下载并保存图像

## 命令行选项

### 便捷脚本选项

| 选项 | 说明 |
|------|------|
| `-f, --file FILE` | 提示词 JSON 文件路径 |
| `-o, --output DIR` | 输出目录 |
| `-c, --characters-only` | 仅生成角色图像 |
| `-s, --scenes-only` | 仅生成场景图像 |
| `-d, --delay SECONDS` | 生成延迟（秒） |
| `--dry-run` | 模拟运行 |
| `--config PREFIX` | API 配置前缀 |
| `-h, --help` | 显示帮助 |

### Python 脚本选项

```
usage: generate_image_assets.py [-h] [-o OUTPUT] [--characters-only]
                                [--scenes-only] [--dry-run] [--delay DELAY]
                                [--config-prefix CONFIG_PREFIX]
                                prompts_file

positional arguments:
  prompts_file          提示词 JSON 文件路径

optional arguments:
  -h, --help            显示帮助信息
  -o OUTPUT, --output OUTPUT
                        输出目录
  --characters-only     仅生成角色图像
  --scenes-only         仅生成场景图像
  --dry-run             模拟运行
  --delay DELAY         生成延迟（秒），默认 2.0
  --config-prefix CONFIG_PREFIX
                        API 配置前缀，默认 ai_api_image
```

## 工作流程

1. **读取提示词文件** - 加载 JSON 文件，提取角色和场景库
2. **加载 API 配置** - 从 `config.json` 读取配置
3. **生成角色图像** - 遍历角色库，调用 API 生成图像
4. **生成场景图像** - 遍历场景库，调用 API 生成图像
5. **保存结果** - 自动下载并保存到指定目录

## 断点续传

脚本会自动检查输出目录：
- ✅ 文件已存在 → 跳过生成
- 🎨 文件不存在 → 调用 API 生成

可以随时中断（Ctrl+C）并重新运行，不会重复生成已有图像。

## 性能建议

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

假设当前项目（8 角色 + 12 场景 = 20 张图片）：
- DALL-E 3: 约 $0.80 (每张 $0.04)
- 实际成本取决于你使用的 API 服务

## 故障排查

### API 调用失败
- 检查 `config.json` 中的 API 密钥
- 确认账户有足够配额
- 测试网络连接

### 图像下载失败
- 增加 `timeout` 配置
- 重新运行脚本（会跳过已成功的）

### 图像质量不佳
- 修改 JSON 文件中的提示词
- 尝试不同的模型
- 调整生成参数

## 相关文档

- 详细使用指南：[docs/generate_image_assets_guide.md](docs/generate_image_assets_guide.md)
- LLM 配置说明：[docs/llm_config_guide.md](docs/llm_config_guide.md)
- 使用示例：[examples/llm_usage_example.py](examples/llm_usage_example.py)

## 文件清单

```
script-fusion/
├── config.json                                    # API 配置
├── generate_assets.sh                             # 便捷启动脚本
├── src/
│   ├── util/
│   │   └── llm.py                                # LLM API 工具
│   └── script-converter/
│       └── generate_image_assets.py              # 图像生成脚本
├── docs/
│   ├── generate_image_assets_guide.md            # 详细使用指南
│   └── llm_config_guide.md                       # 配置说明
├── examples/
│   └── llm_usage_example.py                      # 使用示例
└── output/
    └── .../adapted/rewrite/
        ├── nano_banana_prompts_global_llm.json   # 提示词文件
        └── assets/                                # 生成的图像（输出）
            ├── characters/
            └── scenes/
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
   - 全局风格: cinematic concept art, fantasy realism...

============================================================
🎭 开始生成角色图像
============================================================

[1/8]
  🎨 生成角色: Aurora (lead)
     提示词: character turnaround sheet, three views...
     ✓ 已保存: output/.../assets/characters/character_aurora_turnaround.png

[2/8]
  🎨 生成角色: Caspian (lead)
     提示词: character turnaround sheet, three views...
     ✓ 已保存: output/.../assets/characters/character_caspian_turnaround.png

...

✓ 角色生成完成: 成功 8, 跳过 0, 失败 0

============================================================
🌍 开始生成场景图像
============================================================

[1/12]
  🌄 生成场景: S01 - Solaris Covenant - Golden Hour Camp
     提示词: environment concept art, no people...
     ✓ 已保存: output/.../assets/scenes/scene_s01.png

...

✓ 场景生成完成: 成功 12, 跳过 0, 失败 0

============================================================
🎉 所有图像资产生成完成！
============================================================
输出目录: output/.../assets
```
