"""
Cloubic 图像和视频生成示例

使用前需要设置环境变量：
export AI_API_BASE_URL="https://api.cloubic.com/v1"
export AI_API_KEY="your_cloubic_api_key"
export AI_API_MODEL="gpt-image-1"  # 图像生成模型
# 或
export AI_API_MODEL="sora-2"  # 视频生成模型
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.util.llm import (
    AIAPIConfig,
    call_ai_image_generation,
    call_ai_image_edit,
    call_ai_video_generation,
    get_ai_video_status,
    download_ai_video,
    wait_for_video_completion,
)


def example_image_generation():
    """图像生成示例"""
    print("=== 图像生成示例 ===")

    # 使用环境变量配置
    result = call_ai_image_generation(
        prompt="一只可爱的橘猫在阳光下打盹",
        n=1,
        quality="hd",
        size="1024x1024",
    )

    # 获取图片 URL
    image_url = result["data"][0]["url"]
    print(f"生成的图片 URL: {image_url}")
    print(f"Token 使用: {result.get('usage', {})}")


def example_image_generation_with_custom_config():
    """使用自定义配置生成图像"""
    print("\n=== 使用自定义配置生成图像 ===")

    config = AIAPIConfig(
        base_url="https://api.cloubic.com/v1",
        api_key="your_api_key_here",
        model="dall-e-3",
        timeout=300,
        max_concurrency=2,
    )

    result = call_ai_image_generation(
        prompt="未来城市的夜景，赛博朋克风格",
        config=config,
        quality="hd",
        size="1792x1024",
    )

    print(f"生成的图片 URL: {result['data'][0]['url']}")


def example_image_edit():
    """图像编辑示例"""
    print("\n=== 图像编辑示例 ===")

    # 需要提供实际的图片路径
    image_paths = [
        "/path/to/image1.png",
        "/path/to/image2.png",
    ]

    result = call_ai_image_edit(
        prompt="将这些物品组合成一个精美的礼品篮",
        image_paths=image_paths,
        quality="high",
        size="1024x1024",
    )

    print(f"编辑后的图片 URL: {result['data'][0]['url']}")


def example_video_generation():
    """视频生成示例（文生视频）"""
    print("\n=== 视频生成示例 ===")

    # 创建视频生成任务
    result = call_ai_video_generation(
        prompt="一只猫在草地上追逐蝴蝶，阳光明媚",
        seconds=4,
        size="1280x720",
    )

    video_id = result["id"]
    print(f"视频任务 ID: {video_id}")
    print(f"初始状态: {result.get('status')}")

    # 等待视频生成完成
    print("等待视频生成完成...")
    completed_info = wait_for_video_completion(
        video_id=video_id,
        poll_interval=5,
        max_wait_time=600,
    )

    print(f"视频生成完成！")
    print(f"视频 URL: {completed_info.get('url')}")
    print(f"视频时长: {completed_info.get('seconds')}秒")
    print(f"视频分辨率: {completed_info.get('size')}")

    # 下载视频
    output_path = "/tmp/generated_video.mp4"
    download_ai_video(video_id, output_path)
    print(f"视频已下载到: {output_path}")


def example_video_generation_from_image():
    """图生视频示例"""
    print("\n=== 图生视频示例 ===")

    result = call_ai_video_generation(
        prompt="让这张图片动起来，添加微风吹拂的效果",
        seconds=8,
        size="1280x720",
        input_reference="/path/to/input_image.jpg",
    )

    video_id = result["id"]
    print(f"视频任务 ID: {video_id}")

    # 轮询状态
    print("等待视频生成...")
    completed_info = wait_for_video_completion(video_id)
    print(f"视频 URL: {completed_info.get('url')}")


def example_manual_polling():
    """手动轮询视频状态示例"""
    print("\n=== 手动轮询视频状态 ===")

    video_id = "your_video_id_here"

    # 查询状态
    status_info = get_ai_video_status(video_id)

    print(f"状态: {status_info.get('status')}")
    print(f"进度: {status_info.get('progress')}%")

    if status_info.get("status") == "completed":
        print(f"视频 URL: {status_info.get('url')}")

        # 下载视频
        download_ai_video(video_id, "/tmp/video.mp4")
        print("视频下载完成")


if __name__ == "__main__":
    # 运行示例（根据需要取消注释）

    # 图像生成
    # example_image_generation()
    # example_image_generation_with_custom_config()

    # 图像编辑
    # example_image_edit()

    # 视频生成
    # example_video_generation()
    # example_video_generation_from_image()

    # 手动轮询
    # example_manual_polling()

    print("\n请取消注释要运行的示例函数")
