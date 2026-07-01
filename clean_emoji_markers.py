#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""清理[EMOJI]标记"""

import re

files = ['single_table_query.py', 'multi_table_query.py']

for filename in files:
    try:
        print(f"处理 {filename}...")
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 移除所有 [EMOJI] 标记
        content = re.sub(r'\[EMOJI\]\s*', '', content)
        
        # 修复一些常见的模式
        content = re.sub(r'\[OK\]', '✓', content)
        content = re.sub(r'\[ERROR\]', '✗', content)
        content = re.sub(r'\[WARN\]', '!', content)
        content = re.sub(r'\[INFO\]', '*', content)
        content = re.sub(r'\[HOT\]', '', content)
        content = re.sub(r'\[TIP\]', '*', content)
        content = re.sub(r'\[LOC\]', '', content)
        content = re.sub(r'\[START\]', '', content)
        content = re.sub(r'\[LOAD\]', '', content)
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"  完成!")
    except Exception as e:
        print(f"  错误: {e}")

print("\n全部完成!")



























