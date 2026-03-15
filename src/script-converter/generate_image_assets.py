#!/usr/bin/env python3
"""
图像资产生成脚本

从 nano_banana_prompts_global_llm.json 读取角色和场景提示词，
使用 AI 图像生成 API 创建视觉资产。
"""

import argparse
import base64
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.util.llm import (
    AIAPIError,
    call_ai_image_generation,
    load_ai_api_config,
)

# 线程安全的打印锁
_print_lock = threading.Lock()


def thread_safe_print(*args, **kwargs):
    """线程安全的打印函数"""
    with _print_lock:
        print(*args, **kwargs)


def extract_base64_image(content: str) -> bytes | None:
    """从 markdown 格式的内容中提取 base64 编码的图像数据

    期望格式: ![image](data:image/png;base64,iVBORw0KG...)
    """
    # 匹配 markdown 图像格式中的 base64 数据
    pattern = r'!\[.*?\]\(data:image/[^;]+;base64,([^)]+)\)'
    match = re.search(pattern, content)

    if match:
        base64_data = match.group(1)
        try:
            return base64.b64decode(base64_data)
        except Exception as e:
            thread_safe_print(f"     ✗ Base64 解码失败: {e}")
            return None

    return None


def load_prompts_file(prompts_path: str) -> dict[str, Any]:
    """加载提示词 JSON 文件"""
    if not os.path.exists(prompts_path):
        raise FileNotFoundError(f"提示词文件不存在: {prompts_path}")

    with open(prompts_path, "r", encoding="utf-8") as f:
        return json.load(f)


def generate_character_turnaround(
    character: dict[str, Any],
    output_dir: Path,
    global_style: str,
    config: Any,
    dry_run: bool = False,
) -> str | None:
    """生成角色转身图"""
    canonical_name = character.get("canonical_name", character.get("target_name", "unknown"))
    role_tier = character.get("role_tier", "unknown")
    turnaround_prompt = character.get("turnaround_prompt", "")
    negative_prompt = character.get("negative_prompt", "")

    if not turnaround_prompt:
        thread_safe_print(f"  ⚠️  角色 {canonical_name} 缺少 turnaround_prompt，跳过")
        return None

    # 组合完整提示词（添加 16:9 比例要求和写实风格）
    full_prompt = f"{turnaround_prompt}, {global_style}, photorealistic, realistic, highly detailed, 16:9 aspect ratio, 1920x1080"

    output_filename = f"character_{canonical_name.lower().replace(' ', '_')}_turnaround.png"
    output_path = output_dir / output_filename

    if output_path.exists():
        thread_safe_print(f"  ✓ 角色 {canonical_name} 的图像已存在，跳过")
        return str(output_path)

    thread_safe_print(f"  🎨 生成角色: {canonical_name} ({role_tier})")
    thread_safe_print(f"     提示词: {turnaround_prompt[:80]}...")

    # 打印完整参数
    thread_safe_print(f"     📋 生成参数:")
    thread_safe_print(f"        - 模型: {config.model if config else 'default'}")
    thread_safe_print(f"        - 尺寸: 16:9 (1920x1080)")
    thread_safe_print(f"        - 质量: standard")
    thread_safe_print(f"        - 数量: 1")
    thread_safe_print(f"        - 完整提示词长度: {len(full_prompt)} 字符")
    if len(full_prompt) <= 200:
        thread_safe_print(f"        - 完整提示词: {full_prompt}")
    else:
        thread_safe_print(f"        - 完整提示词: {full_prompt[:200]}... (已截断)")

    if dry_run:
        thread_safe_print(f"     [DRY RUN] 将保存到: {output_path}")
        return None

    try:
        result = call_ai_image_generation(
            prompt=full_prompt,
            config=config,
            n=1,
            quality="standard",
            size="1920x1080",
            response_format="url",
        )

        # 从响应中提取图像数据
        if "choices" in result and len(result["choices"]) > 0:
            message_content = result["choices"][0].get("message", {}).get("content", "")
            if message_content:
                # 提取 base64 编码的图像数据
                image_data = extract_base64_image(message_content)
                if image_data:
                    output_dir.mkdir(parents=True, exist_ok=True)
                    with open(output_path, "wb") as f:
                        f.write(image_data)

                    thread_safe_print(f"     ✓ 已保存: {output_path}")
                    return str(output_path)
                else:
                    thread_safe_print(f"     ✗ 未能从响应中提取图像数据")
                    return None
            else:
                thread_safe_print(f"     ✗ 响应中没有内容")
                return None
        else:
            thread_safe_print(f"     ✗ 生成失败，响应格式无效")
            return None

    except AIAPIError as e:
        thread_safe_print(f"     ✗ API 错误: {e}")
        return None
    except Exception as e:
        thread_safe_print(f"     ✗ 未知错误: {e}")
        return None


def generate_scene_environment(
    scene: dict[str, Any],
    output_dir: Path,
    global_style: str,
    config: Any,
    dry_run: bool = False,
) -> str | None:
    """生成场景环境图"""
    scene_id = scene.get("scene_id", "unknown")
    title = scene.get("title", "Untitled")
    prompt = scene.get("prompt", "")
    negative_prompt = scene.get("negative_prompt", "")

    if not prompt:
        thread_safe_print(f"  ⚠️  场景 {scene_id} 缺少 prompt，跳过")
        return None

    # 组合完整提示词（添加 16:9 比例要求和写实风格）
    full_prompt = f"{prompt}, {global_style}, photorealistic, realistic, highly detailed, 16:9 aspect ratio, 1920x1080"

    output_filename = f"scene_{scene_id.lower()}.png"
    output_path = output_dir / output_filename

    if output_path.exists():
        thread_safe_print(f"  ✓ 场景 {scene_id} 的图像已存在，跳过")
        return str(output_path)

    thread_safe_print(f"  🌄 生成场景: {scene_id} - {title}")
    thread_safe_print(f"     提示词: {prompt[:80]}...")

    # 打印完整参数
    thread_safe_print(f"     📋 生成参数:")
    thread_safe_print(f"        - 模型: {config.model if config else 'default'}")
    thread_safe_print(f"        - 尺寸: 16:9 (1920x1080)")
    thread_safe_print(f"        - 质量: standard")
    thread_safe_print(f"        - 数量: 1")
    thread_safe_print(f"        - 完整提示词长度: {len(full_prompt)} 字符")
    if len(full_prompt) <= 200:
        thread_safe_print(f"        - 完整提示词: {full_prompt}")
    else:
        thread_safe_print(f"        - 完整提示词: {full_prompt[:200]}... (已截断)")

    if dry_run:
        thread_safe_print(f"     [DRY RUN] 将保存到: {output_path}")
        return None

    try:
        result = call_ai_image_generation(
            prompt=full_prompt,
            config=config,
            n=1,
            quality="standard",
            size="1920x1080",
            response_format="url",
        )

        # 从响应中提取图像数据
        if "choices" in result and len(result["choices"]) > 0:
            message_content = result["choices"][0].get("message", {}).get("content", "")
            if message_content:
                # 提取 base64 编码的图像数据
                image_data = extract_base64_image(message_content)
                if image_data:
                    output_dir.mkdir(parents=True, exist_ok=True)
                    with open(output_path, "wb") as f:
                        f.write(image_data)

                    thread_safe_print(f"     ✓ 已保存: {output_path}")
                    return str(output_path)
                else:
                    thread_safe_print(f"     ✗ 未能从响应中提取图像数据")
                    return None
            else:
                thread_safe_print(f"     ✗ 响应中没有内容")
                return None
        else:
            thread_safe_print(f"     ✗ 生成失败，响应格式无效")
            return None

    except AIAPIError as e:
        thread_safe_print(f"     ✗ API 错误: {e}")
        return None
    except Exception as e:
        thread_safe_print(f"     ✗ 未知错误: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="生成图像资产")
    parser.add_argument(
        "prompts_file",
        help="提示词 JSON 文件路径（nano_banana_prompts_global_llm.json）",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="输出目录（默认：prompts_file 同级的 assets 目录）",
    )
    parser.add_argument(
        "--characters-only",
        action="store_true",
        help="仅生成角色图像",
    )
    parser.add_argument(
        "--scenes-only",
        action="store_true",
        help="仅生成场景图像",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="模拟运行，不实际调用 API",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="每次生成之间的延迟（秒），默认 2.0（仅在非并发模式下有效）",
    )
    parser.add_argument(
        "--config-prefix",
        default="ai_api_image",
        help="使用的配置前缀，默认 ai_api_image",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="并发工作线程数，默认 1（顺序执行）。建议 2-4",
    )

    args = parser.parse_args()

    # 加载提示词文件
    print(f"📖 加载提示词文件: {args.prompts_file}")
    try:
        prompts_data = load_prompts_file(args.prompts_file)
    except Exception as e:
        print(f"✗ 加载失败: {e}")
        sys.exit(1)

    # 确定输出目录
    if args.output:
        output_base = Path(args.output)
    else:
        output_base = Path(args.prompts_file).parent / "assets"

    output_base.mkdir(parents=True, exist_ok=True)
    characters_dir = output_base / "characters"
    scenes_dir = output_base / "scenes"

    print(f"📁 输出目录: {output_base}")
    print(f"   - 角色: {characters_dir}")
    print(f"   - 场景: {scenes_dir}")

    # 加载 API 配置
    if not args.dry_run:
        print(f"🔧 加载 API 配置: {args.config_prefix}")
        try:
            api_config = load_ai_api_config(prefix=args.config_prefix)
            print(f"   ✓ 模型: {api_config.model}")
        except AIAPIError as e:
            print(f"   ✗ 配置加载失败: {e}")
            sys.exit(1)
    else:
        api_config = None
        print("🔧 [DRY RUN] 跳过 API 配置加载")

    global_style = prompts_data.get("global_style_prompt", "")
    character_library = prompts_data.get("character_prompt_library", [])
    scene_library = prompts_data.get("scene_prompt_library", [])

    print(f"\n📊 统计:")
    print(f"   - 角色数量: {len(character_library)}")
    print(f"   - 场景数量: {len(scene_library)}")
    print(f"   - 全局风格: {global_style[:60]}...")
    print(f"   - 并发线程数: {args.workers}")

    # 生成角色图像
    if not args.scenes_only:
        print(f"\n{'='*60}")
        print("🎭 开始生成角色图像")
        print(f"{'='*60}")

        success_count = 0
        skip_count = 0
        fail_count = 0

        if args.workers == 1:
            # 顺序执行
            for idx, character in enumerate(character_library, 1):
                print(f"\n[{idx}/{len(character_library)}]")
                result = generate_character_turnaround(
                    character=character,
                    output_dir=characters_dir,
                    global_style=global_style,
                    config=api_config,
                    dry_run=args.dry_run,
                )

                if result:
                    success_count += 1
                elif result is None and not args.dry_run:
                    fail_count += 1
                else:
                    skip_count += 1

                # 延迟
                if idx < len(character_library) and not args.dry_run:
                    time.sleep(args.delay)
        else:
            # 并发执行
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = {}
                for idx, character in enumerate(character_library, 1):
                    future = executor.submit(
                        generate_character_turnaround,
                        character=character,
                        output_dir=characters_dir,
                        global_style=global_style,
                        config=api_config,
                        dry_run=args.dry_run,
                    )
                    futures[future] = (idx, character.get("canonical_name", "unknown"))

                for future in as_completed(futures):
                    idx, name = futures[future]
                    thread_safe_print(f"\n[{idx}/{len(character_library)}] 完成: {name}")
                    try:
                        result = future.result()
                        if result:
                            success_count += 1
                        elif result is None and not args.dry_run:
                            fail_count += 1
                        else:
                            skip_count += 1
                    except Exception as e:
                        thread_safe_print(f"     ✗ 线程异常: {e}")
                        fail_count += 1

        print(f"\n✓ 角色生成完成: 成功 {success_count}, 跳过 {skip_count}, 失败 {fail_count}")

    # 生成场景图像
    if not args.characters_only:
        print(f"\n{'='*60}")
        print("🌍 开始生成场景图像")
        print(f"{'='*60}")

        success_count = 0
        skip_count = 0
        fail_count = 0

        if args.workers == 1:
            # 顺序执行
            for idx, scene in enumerate(scene_library, 1):
                print(f"\n[{idx}/{len(scene_library)}]")
                result = generate_scene_environment(
                    scene=scene,
                    output_dir=scenes_dir,
                    global_style=global_style,
                    config=api_config,
                    dry_run=args.dry_run,
                )

                if result:
                    success_count += 1
                elif result is None and not args.dry_run:
                    fail_count += 1
                else:
                    skip_count += 1

                # 延迟
                if idx < len(scene_library) and not args.dry_run:
                    time.sleep(args.delay)
        else:
            # 并发执行
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = {}
                for idx, scene in enumerate(scene_library, 1):
                    future = executor.submit(
                        generate_scene_environment,
                        scene=scene,
                        output_dir=scenes_dir,
                        global_style=global_style,
                        config=api_config,
                        dry_run=args.dry_run,
                    )
                    futures[future] = (idx, scene.get("scene_id", "unknown"))

                for future in as_completed(futures):
                    idx, scene_id = futures[future]
                    thread_safe_print(f"\n[{idx}/{len(scene_library)}] 完成: {scene_id}")
                    try:
                        result = future.result()
                        if result:
                            success_count += 1
                        elif result is None and not args.dry_run:
                            fail_count += 1
                        else:
                            skip_count += 1
                    except Exception as e:
                        thread_safe_print(f"     ✗ 线程异常: {e}")
                        fail_count += 1

        print(f"\n✓ 场景生成完成: 成功 {success_count}, 跳过 {skip_count}, 失败 {fail_count}")

    print(f"\n{'='*60}")
    print("🎉 所有图像资产生成完成！")
    print(f"{'='*60}")
    print(f"输出目录: {output_base}")


if __name__ == "__main__":
    main()
