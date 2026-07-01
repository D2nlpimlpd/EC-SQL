#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""移除emoji字符"""

import re

def remove_emojis(text):
    """移除所有emoji字符"""
    # 定义emoji替换映射
    emoji_map = {
        '📖': '[INFO]',
        '✅': '[OK]',
        '❌': '[ERROR]',
        '⚠️': '[WARN]',
        '🚀': '[START]',
        '📚': '[LOAD]',
        '💡': '[TIP]',
        '🔥': '[HOT]',
        '📍': '[LOC]',
    }
    
    for emoji, replacement in emoji_map.items():
        text = text.replace(emoji, replacement)
    
    # 移除其他可能的emoji
    emoji_pattern = re.compile("["
        u"\U0001F600-\U0001F64F"  # emoticons
        u"\U0001F300-\U0001F5FF"  # symbols & pictographs
        u"\U0001F680-\U0001F6FF"  # transport & map symbols
        u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
        u"\U00002702-\U000027B0"
        u"\U000024C2-\U0001F251"
        "]+", flags=re.UNICODE)
    
    text = emoji_pattern.sub('[EMOJI]', text)
    
    return text

# 处理多个文件
files = ['app_3.py', 'single_table_query.py', 'multi_table_query.py']

for filename in files:
    try:
        print(f"处理 {filename}...")
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
        
        new_content = remove_emojis(content)
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        print(f"  完成!")
    except Exception as e:
        print(f"  错误: {e}")

print("\n全部完成!")
