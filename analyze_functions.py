#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
自动识别 app_3.py 中的函数并分类
"""

import re

print("分析 app_3.py 的函数结构...")

# 读取文件
with open('app_3.py', 'r', encoding='utf-8') as f:
    content = f.read()
    lines = content.split('\n')

# 查找所有函数定义
functions = []
for i, line in enumerate(lines, 1):
    if re.match(r'^def\s+\w+\s*\(', line):
        func_name = re.search(r'def\s+(\w+)\s*\(', line).group(1)
        functions.append((i, func_name, line.strip()))

print(f"\n找到 {len(functions)} 个函数定义：\n")

# 分类函数
single_table_funcs = []
multi_table_funcs = []
common_funcs = []
route_funcs = []

# 单表查询相关关键词
single_keywords = [
    'single', 'candidate', 'direct', 'code_table', 'query_code',
    'validate', 'extract_clean', 'build_schema_prompt'
]

# 多表查询相关关键词
multi_keywords = [
    'multi', 'join', 'relation', 'analyze_table', 'optimize_sql',
    'filter_and_prioritize', 'dependency', 'limit_table', 'fewshot'
]

for line_num, func_name, func_line in functions:
    func_lower = func_name.lower()
    
    # 路由函数
    if func_name.startswith('api_'):
        route_funcs.append((line_num, func_name))
    # 单表查询
    elif any(kw in func_lower for kw in single_keywords):
        single_table_funcs.append((line_num, func_name))
    # 多表查询
    elif any(kw in func_lower for kw in multi_keywords):
        multi_table_funcs.append((line_num, func_name))
    # 通用函数
    else:
        common_funcs.append((line_num, func_name))

# 输出分类结果
print("=" * 80)
print("【路由函数】(保留在 app_3.py)")
print("=" * 80)
for line_num, func_name in route_funcs:
    print(f"  Line {line_num:4d}: {func_name}")

print("\n" + "=" * 80)
print("【单表查询函数】(移动到 single_table_query.py)")
print("=" * 80)
for line_num, func_name in single_table_funcs:
    print(f"  Line {line_num:4d}: {func_name}")

print("\n" + "=" * 80)
print("【多表查询函数】(移动到 multi_table_query.py)")
print("=" * 80)
for line_num, func_name in multi_table_funcs:
    print(f"  Line {line_num:4d}: {func_name}")

print("\n" + "=" * 80)
print("【通用函数】(需要手动判断)")
print("=" * 80)
for line_num, func_name in common_funcs[:30]:  # 只显示前30个
    print(f"  Line {line_num:4d}: {func_name}")

if len(common_funcs) > 30:
    print(f"  ... 还有 {len(common_funcs) - 30} 个函数")

print("\n" + "=" * 80)
print("【统计】")
print("=" * 80)
print(f"  路由函数: {len(route_funcs)}")
print(f"  单表查询: {len(single_table_funcs)}")
print(f"  多表查询: {len(multi_table_funcs)}")
print(f"  通用函数: {len(common_funcs)}")
print(f"  总计: {len(functions)}")




























