#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析 app_3.py 中的 GROUP BY 相关代码"""

import re

def analyze_app3():
    with open('app_3.py', 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    print("=" * 80)
    print("分析 app_3.py 中的关键函数")
    print("=" * 80)
    
    # 1. 找到所有函数定义
    print("\n【1. SQL相关函数定义】")
    for i, line in enumerate(lines, 1):
        if line.strip().startswith('def '):
            func_name = line.strip()
            if any(keyword in func_name.lower() for keyword in ['sql', 'generate', 'build', 'query', 'select']):
                print(f"行 {i}: {func_name[:100]}")
    
    # 2. 找到所有 GROUP BY 出现的位置
    print("\n【2. GROUP BY 出现位置】")
    group_by_lines = []
    for i, line in enumerate(lines, 1):
        if 'GROUP BY' in line.upper() or 'group by' in line.lower():
            group_by_lines.append((i, line.strip()))
            if len(group_by_lines) <= 30:
                print(f"行 {i}: {line.strip()[:120]}")
    
    print(f"\n总共找到 {len(group_by_lines)} 处 GROUP BY")
    
    # 3. 找到聚合函数相关代码
    print("\n【3. 聚合函数相关代码】")
    agg_count = 0
    for i, line in enumerate(lines, 1):
        if any(agg in line.upper() for agg in ['COUNT(', 'SUM(', 'AVG(', 'MAX(', 'MIN(']):
            agg_count += 1
            if agg_count <= 20:
                print(f"行 {i}: {line.strip()[:120]}")
    
    print(f"\n总共找到 {agg_count} 处聚合函数")
    
    # 4. 找到 LLM prompt 相关代码
    print("\n【4. LLM Prompt 相关代码（可能影响GROUP BY生成）】")
    prompt_lines = []
    in_prompt = False
    for i, line in enumerate(lines, 1):
        if 'prompt' in line.lower() or '提示词' in line or 'messages' in line.lower():
            if '=' in line or 'def ' in line:
                prompt_lines.append(i)
                if len(prompt_lines) <= 15:
                    print(f"行 {i}: {line.strip()[:120]}")
    
    # 5. 找到可能删除或修改 GROUP BY 的代码
    print("\n【5. 可能删除/修改 GROUP BY 的代码】")
    modify_count = 0
    for i, line in enumerate(lines, 1):
        if any(keyword in line.lower() for keyword in ['replace', 'remove', 'delete', 'strip', 'clean', '删除', '清理', '替换']):
            if 'group' in line.lower() or 'sql' in line.lower():
                modify_count += 1
                if modify_count <= 20:
                    print(f"行 {i}: {line.strip()[:120]}")
    
    # 6. 输出关键行号范围
    print("\n【6. 建议重点检查的行号范围】")
    if group_by_lines:
        print(f"GROUP BY 集中区域: {group_by_lines[0][0]} - {group_by_lines[-1][0]}")
    if prompt_lines:
        print(f"Prompt 定义区域: {min(prompt_lines)} - {max(prompt_lines)}")

if __name__ == '__main__':
    analyze_app3()





























