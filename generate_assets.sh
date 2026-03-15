#!/bin/bash
# 图像资产生成快速启动脚本

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 默认配置
PROMPTS_FILE="output/1fc071a6_- BITE ME , ᶻᵒᵐᵇⁱᵉˢ⁴ - $ - Wattpad/adapted/rewrite/nano_banana_prompts_global_llm.json"
DELAY=2.0
CONFIG_PREFIX="ai_api_image"
WORKERS=1

# 显示帮助信息
show_help() {
    echo -e "${BLUE}图像资产生成脚本${NC}"
    echo ""
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  -f, --file FILE          提示词 JSON 文件路径"
    echo "  -o, --output DIR         输出目录"
    echo "  -c, --characters-only    仅生成角色图像"
    echo "  -s, --scenes-only        仅生成场景图像"
    echo "  -d, --delay SECONDS      生成延迟（秒），默认 2.0"
    echo "  -w, --workers NUM        并发线程数，默认 1。建议 2-4"
    echo "  --dry-run                模拟运行，不调用 API"
    echo "  --config PREFIX          API 配置前缀，默认 ai_api_image"
    echo "  -h, --help               显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0                                    # 使用默认配置生成所有资产"
    echo "  $0 -c                                 # 仅生成角色"
    echo "  $0 -s                                 # 仅生成场景"
    echo "  $0 --dry-run                          # 预览模式"
    echo "  $0 -f path/to/prompts.json            # 指定提示词文件"
    echo "  $0 -d 5.0                             # 设置 5 秒延迟"
}

# 解析命令行参数
CHARACTERS_ONLY=""
SCENES_ONLY=""
DRY_RUN=""
OUTPUT_DIR=""

while [[ $# -gt 0 ]]; do
    case $1 in
        -f|--file)
            PROMPTS_FILE="$2"
            shift 2
            ;;
        -o|--output)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        -c|--characters-only)
            CHARACTERS_ONLY="--characters-only"
            shift
            ;;
        -s|--scenes-only)
            SCENES_ONLY="--scenes-only"
            shift
            ;;
        -d|--delay)
            DELAY="$2"
            shift 2
            ;;
        -w|--workers)
            WORKERS="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN="--dry-run"
            shift
            ;;
        --config)
            CONFIG_PREFIX="$2"
            shift 2
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo -e "${RED}错误: 未知选项 $1${NC}"
            show_help
            exit 1
            ;;
    esac
done

# 检查提示词文件是否存在
if [ ! -f "$PROMPTS_FILE" ]; then
    echo -e "${RED}错误: 提示词文件不存在: $PROMPTS_FILE${NC}"
    exit 1
fi

# 构建命令
CMD="python3 src/script-converter/generate_image_assets.py \"$PROMPTS_FILE\""

if [ -n "$OUTPUT_DIR" ]; then
    CMD="$CMD --output \"$OUTPUT_DIR\""
fi

if [ -n "$CHARACTERS_ONLY" ]; then
    CMD="$CMD $CHARACTERS_ONLY"
fi

if [ -n "$SCENES_ONLY" ]; then
    CMD="$CMD $SCENES_ONLY"
fi

if [ -n "$DRY_RUN" ]; then
    CMD="$CMD $DRY_RUN"
fi

CMD="$CMD --delay $DELAY --config-prefix $CONFIG_PREFIX --workers $WORKERS"

# 显示配置信息
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}图像资产生成${NC}"
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}提示词文件:${NC} $PROMPTS_FILE"
echo -e "${GREEN}生成延迟:${NC} $DELAY 秒"
echo -e "${GREEN}并发线程数:${NC} $WORKERS"
echo -e "${GREEN}API 配置:${NC} $CONFIG_PREFIX"

if [ -n "$OUTPUT_DIR" ]; then
    echo -e "${GREEN}输出目录:${NC} $OUTPUT_DIR"
fi

if [ -n "$CHARACTERS_ONLY" ]; then
    echo -e "${YELLOW}模式:${NC} 仅生成角色"
elif [ -n "$SCENES_ONLY" ]; then
    echo -e "${YELLOW}模式:${NC} 仅生成场景"
else
    echo -e "${YELLOW}模式:${NC} 生成所有资产（角色 + 场景）"
fi

if [ -n "$DRY_RUN" ]; then
    echo -e "${YELLOW}⚠️  DRY RUN 模式 - 不会实际调用 API${NC}"
fi

echo -e "${BLUE}========================================${NC}"
echo ""

# 执行命令
echo -e "${GREEN}开始执行...${NC}"
echo ""
eval $CMD

# 完成
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}✓ 完成！${NC}"
echo -e "${GREEN}========================================${NC}"
