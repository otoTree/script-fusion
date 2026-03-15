import os
import json
import re

# 排除关键词列表，用于过滤非角色名
EXCLUDE_KEYWORDS = {
    'the', 'camp', 'woods', 'side', 'covenant', 'collective', 'guild', 'relic', 
    'harvest', 'enclave', 'lodge', 'outpost', 'city', 'house', 'room', 'place',
    'area', 'zone', 'region', 'world', 'land', 'sea', 'ocean', 'river', 'mountain',
    'valley', 'hill', 'forest', 'jungle', 'desert', 'swamp', 'marsh', 'island',
    'road', 'street', 'avenue', 'lane', 'path', 'trail', 'way', 'route', 'map',
    'key', 'fruit', 'necklace', 'ring', 'sword', 'shield', 'armor', 'weapon',
    'book', 'scroll', 'letter', 'note', 'paper', 'pen', 'pencil', 'ink', 'bottle',
    'potion', 'elixir', 'food', 'drink', 'water', 'wine', 'beer', 'ale', 'mead',
    'bread', 'meat', 'cheese', 'fruit', 'vegetable', 'plant', 'flower', 'tree',
    'shrub', 'bush', 'grass', 'herb', 'spice', 'salt', 'sugar', 'flour', 'dough',
    'batter', 'cake', 'pie', 'tart', 'cookie', 'biscuit', 'cracker', 'candy',
    'chocolate', 'sweet', 'sour', 'bitter', 'salty', 'spicy', 'hot', 'cold',
    'warm', 'cool', 'wet', 'dry', 'hard', 'soft', 'rough', 'smooth', 'heavy',
    'light', 'dark', 'bright', 'dim', 'sharp', 'dull', 'clean', 'dirty', 'messy',
    'neat', 'tidy', 'organized', 'chaotic', 'loud', 'quiet', 'silent', 'noisy',
    'fast', 'slow', 'quick', 'rapid', 'swift', 'speedy', 'hasty', 'rushed',
    'daywalkers', 'luminari', 'umbra', 'solaris', 'nocturne', 'ancient', 'elder'
}

# 常见的对话动词
DIALOGUE_VERBS = [
    'said', 'asked', 'replied', 'shouted', 'whispered', 'explained', 'stated',
    'implied', 'continued', 'added', 'muttered', 'screamed', 'yelled', 'cried',
    'called', 'answered', 'responded', 'commented', 'remarked', 'observed',
    'declared', 'announced', 'proclaimed', 'pronounced', 'uttered', 'voiced',
    'articulated', 'enunciated', 'intoned', 'drawled', 'barked', 'snapped',
    'hissed', 'growled', 'roared', 'bellowed', 'hollered', 'screeched', 'squealed',
    'whimpered', 'whined', 'moaned', 'groaned', 'sighed', 'gasped', 'panted',
    'breathed', 'laughed', 'giggled', 'chuckled', 'snickered', 'snorted', 'smiled',
    'grinned', 'beamed', 'frowned', 'scowled', 'glared', 'stared', 'gazed',
    'looked', 'nodded', 'shrugged', 'waved', 'pointed', 'gestured', 'motioned',
    'signaled', 'indicated', 'showed', 'revealed', 'displayed', 'exhibited'
]
DIALOGUE_VERBS_PATTERN = '|'.join(DIALOGUE_VERBS)

def load_names(input_json_path):
    try:
        with open(input_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            name_map = data.get('name_map', {})
            names = set()
            for k, v in name_map.items():
                if isinstance(v, str):
                    # 过滤掉包含排除关键词的名字
                    lower_v = v.lower()
                    if not any(keyword in lower_v.split() for keyword in EXCLUDE_KEYWORDS):
                         names.add(v)
            
            # 也可以添加 keys，但通常 values 是改写后的名字
            # for k in name_map.keys():
            #     if isinstance(k, str) and k[0].isupper():
            #          names.add(k)

            # 按长度降序排列
            sorted_names = sorted(list(names), key=len, reverse=True)
            return sorted_names
    except Exception as e:
        print(f"Error loading names from {input_json_path}: {e}")
        return []

def clean_text(text):
    text = text.replace('“', '"').replace('”', '"')
    return text.strip()

def is_chapter_header(line):
    return line.isupper() and ('CHAPTER' in line or 'ACT' in line) and len(line) < 100

def extract_speaker_from_context(context, names):
    """
    从上下文中提取说话者。
    上下文可能是对话后的 "she said" 或对话前的 "He looked at her and said".
    """
    context = context.strip()
    if not context:
        return None
        
    # 1. 检查是否有 "Name verb" 模式 (e.g., "Aurora said")
    for name in names:
        pattern = r'\b' + re.escape(name) + r'\s+(?:' + DIALOGUE_VERBS_PATTERN + r')\b'
        if re.search(pattern, context, re.IGNORECASE):
            return name.upper()
            
    # 2. 检查是否有 "verb Name" 模式 (e.g., "said Aurora")
    for name in names:
        pattern = r'\b(?:' + DIALOGUE_VERBS_PATTERN + r')\s+' + re.escape(name) + r'\b'
        if re.search(pattern, context, re.IGNORECASE):
            return name.upper()
            
    # 3. 如果只是简单的名字出现在上下文中，且没有其他动词，可能就是说话者 (e.g. "Aurora.")
    # 但要小心动作描述。这里我们只匹配名字作为主语的情况。
    # 简化策略：如果在上下文中找到名字，且它是句子的主语（位于句首或逗号后），则认为是说话者
    for name in names:
        if context.startswith(name) or f", {name}" in context:
            return name.upper()
            
    return None

def parse_paragraph(paragraph, names, last_speaker=None):
    # 分割对话和非对话
    # 使用 split 保留分隔符
    segments = re.split(r'(".*?")', paragraph)
    
    parsed_elements = []
    
    # 这一段中的主要说话者
    current_speaker = None
    
    # 预先扫描寻找明确的说话者标签
    # 拼接所有非对话部分作为上下文
    context_text = " ".join([s for s in segments if not (s.startswith('"') and s.endswith('"'))])
    
    extracted_speaker = extract_speaker_from_context(context_text, names)
    if extracted_speaker:
        current_speaker = extracted_speaker
    
    # 如果没找到，尝试从代词推断（这里简化处理，如果找不到且有对话，可能沿用 last_speaker，但这有风险）
    # 更好的策略：如果只有两个人在场，且交替说话... 但这里我们没有全局上下文。
    # 只能标记为 UNKNOWN 或者留空。
    
    # 如果没有找到明确的说话者，但段落中有名字出现，可能就是该名字（比如动作描写暗示说话）
    if not current_speaker:
        for name in names:
            if name in context_text:
                current_speaker = name.upper()
                break
    
    # 处理每个片段
    for i, segment in enumerate(segments):
        segment = segment.strip()
        if not segment:
            continue
            
        if segment.startswith('"') and segment.endswith('"'):
            # 对话
            dialogue = segment[1:-1].strip()
            if dialogue:
                speaker = current_speaker if current_speaker else (last_speaker if last_speaker else "UNKNOWN")
                # 再次检查：如果是 UNKNOWN，且上一段也是 UNKNOWN，那也没办法。
                # 但如果是多人对话，沿用 last_speaker 可能会错。
                # 宁愿 UNKNOWN 也不要标错。
                # 只有在非常确定的情况下（如紧接的动作描述）才归属。
                if not current_speaker:
                    speaker = "UNKNOWN" # 安全起见
                
                parsed_elements.append({
                    'type': 'dialogue',
                    'speaker': speaker,
                    'text': dialogue
                })
                # 更新 last_speaker 为当前的，以便下一句对话可能沿用（如果在同一段）
                if speaker != "UNKNOWN":
                    last_speaker = speaker
        else:
            # 动作
            # 如果这部分仅仅是 "she said" 或 "Aurora asked"，应该被过滤掉或转化
            # 但为了保留文学性，我们可以保留它，但在剧本中通常不写 "she said"。
            # 如果我们已经提取了说话者，可以尝试移除这部分标签，但这需要精准的定位。
            # 简单起见，保留动作描述。
            
            # 清理纯粹的对话标签
            clean_segment = segment
            # 移除 "she said", "said Aurora" 等
            # 这比较激进，先不移除，只是作为 Action 输出
            
            if len(clean_segment) > 1 or clean_segment.isalpha():
                parsed_elements.append({
                    'type': 'action',
                    'text': clean_segment
                })
                
    return parsed_elements, last_speaker

def convert_file(folder_path):
    adapted_path = os.path.join(folder_path, 'adapted.txt')
    input_json_path = os.path.join(folder_path, 'input.json')
    output_path = os.path.join(folder_path, 'script.fountain')
    
    if not os.path.exists(adapted_path):
        return

    names = []
    if os.path.exists(input_json_path):
        names = load_names(input_json_path)
    
    with open(adapted_path, 'r', encoding='utf-8') as f:
        content = f.read()
        
    content = clean_text(content)
    paragraphs = re.split(r'\n\s*\n', content)
    
    fountain_lines = []
    last_speaker = None
    
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
            
        if is_chapter_header(p):
            fountain_lines.append(f"\n.{p}\n") 
            last_speaker = None # 新场景重置
            continue
            
        elements, new_last_speaker = parse_paragraph(p, names, last_speaker)
        if new_last_speaker:
            last_speaker = new_last_speaker
        
        for el in elements:
            if el['type'] == 'dialogue':
                fountain_lines.append(f"\n{el['speaker']}\n{el['text']}\n")
            else:
                fountain_lines.append(f"\n{el['text']}\n")
                
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(fountain_lines))
    
    print(f"Generated script: {output_path}")

def process_all(root_dir):
    for item in os.listdir(root_dir):
        item_path = os.path.join(root_dir, item)
        if os.path.isdir(item_path):
            convert_file(item_path)

if __name__ == "__main__":
    target_dir = "/Users/hjr/Desktop/script-fusion/output/1fc071a6_- BITE ME , ᶻᵒᵐᵇⁱᵉˢ⁴ - $ - Wattpad/adapted/rewrite"
    process_all(target_dir)
