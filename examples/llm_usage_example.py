"""
LLM API 使用示例

演示如何使用 src/util/llm.py 中的各种 AI API 功能
"""

from src.util.llm import (
    AIAPIConfig,
    call_ai_chat_completion,
    call_ai_image_generation,
    call_ai_video_generation,
    extract_first_message_content,
    get_ai_video_status,
    load_ai_api_config,
    wait_for_video_completion,
)


def example_chat_completion():
    """示例：调用聊天完成 API"""
    print("=== 聊天完成示例 ===")

    # 方式1：使用默认配置（从 config.json 的 ai_api 配置项加载）
    messages = [
        {"role": "system", "content": "你是一个有帮助的助手。"},
        {"role": "user", "content": "请用一句话介绍 Python。"},
    ]

    result = call_ai_chat_completion(messages=messages, temperature=0.7, max_tokens=100)

    content = extract_first_message_content(result)
    print(f"回复: {content}\n")


def example_chat_with_custom_config():
    """示例：使用自定义配置调用聊天 API"""
    print("=== 使用自定义配置 ===")

    # 方式2：加载指定的配置项（例如使用 Claude）
    config = load_ai_api_config(prefix="ai_api_claude")

    messages = [
        {"role": "user", "content": "用一句话解释什么是机器学习。"},
    ]

    result = call_ai_chat_completion(messages=messages, config=config, temperature=0.5)

    content = extract_first_message_content(result)
    print(f"回复: {content}\n")


def example_image_generation():
    """示例：生成图片"""
    print("=== 图片生成示例 ===")

    # 使用专门的图片生成配置
    config = load_ai_api_config(prefix="ai_api_image")

    prompt = "一只可爱的橙色小猫坐在窗台上，背景是夕阳"

    result = call_ai_image_generation(
        prompt=prompt, config=config, n=1, quality="standard", size="1024x1024"
    )

    # 获取生成的图片 URL
    if "data" in result and len(result["data"]) > 0:
        image_url = result["data"][0].get("url")
        print(f"生成的图片 URL: {image_url}\n")
    else:
        print("图片生成失败\n")


def example_video_generation():
    """示例：生成视频（异步）"""
    print("=== 视频生成示例 ===")

    # 使用专门的视频生成配置
    config = load_ai_api_config(prefix="ai_api_video")

    prompt = "一只小猫在草地上追逐蝴蝶"

    # 提交视频生成任务
    result = call_ai_video_generation(
        prompt=prompt, config=config, seconds=4, size="1280x720"
    )

    video_id = result.get("video_id")
    print(f"视频任务 ID: {video_id}")

    # 等待视频生成完成
    print("等待视频生成...")
    completed_info = wait_for_video_completion(
        video_id=video_id, config=config, poll_interval=5, max_wait_time=300
    )

    video_url = completed_info.get("url")
    print(f"视频生成完成！URL: {video_url}\n")


def example_manual_config():
    """示例：手动创建配置对象"""
    print("=== 手动配置示例 ===")

    # 方式3：手动创建配置对象（不从 config.json 读取）
    manual_config = AIAPIConfig(
        base_url="https://api.openai.com/v1",
        api_key="sk-your-manual-key",
        model="gpt-3.5-turbo",
        timeout=120,
        max_concurrency=1,
    )

    messages = [{"role": "user", "content": "Hello!"}]

    # 注意：这个示例需要有效的 API key 才能运行
    # result = call_ai_chat_completion(messages=messages, config=manual_config)
    print("手动配置已创建（需要有效的 API key 才能实际调用）\n")


def main():
    """运行所有示例"""
    print("LLM API 使用示例\n")
    print("注意：运行这些示例需要：")
    print("1. 在 config.json 中配置有效的 API key")
    print("2. 确保网络连接正常")
    print("3. 有足够的 API 配额\n")

    try:
        # 取消注释以运行各个示例
        # example_chat_completion()
        # example_chat_with_custom_config()
        # example_image_generation()
        # example_video_generation()
        example_manual_config()

        print("示例运行完成！")

    except Exception as e:
        print(f"运行示例时出错: {e}")


if __name__ == "__main__":
    main()
