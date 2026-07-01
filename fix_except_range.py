#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""只检查和修复第 268-305 行"""

import re

print("检查第 268-305 行...")

# 读取文件
with open('app_3.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 只检查目标行
start = 267  # 第 268 行的索引
end = 305    # 第 305 行的索引

print(f"\n显示第 268-305 行的代码：")
print("=" * 80)
for i in range(start, min(end, len(lines))):
    line_num = i + 1
    print(f"{line_num:4d}: {lines[i]}", end='')
print("=" * 80)

# 查找这个范围内的裸露 except
bare_excepts = []
for i in range(start, min(end, len(lines))):
    line_num = i + 1
    if re.match(r'^\s*except:\s*$', lines[i]):
        bare_excepts.append((line_num, lines[i].rstrip()))

if bare_excepts:
    print(f"\n找到 {len(bare_excepts)} 个裸露的 except:")
    for line_num, line_text in bare_excepts:
        print(f"  Line {line_num}: {line_text}")
    
    # 修复
    print("\n修复中...")
    for line_num, _ in bare_excepts:
        idx = line_num - 1
        lines[idx] = lines[idx].replace('except:', 'except Exception:')
        print(f"  ✅ 修复第 {line_num} 行")
    
    # 保存
    with open('app_3.py', 'w', encoding='utf-8') as f:
        f.writelines(lines)
    
    print(f"\n✅ 修复完成！")
else:
    print("\n✅ 该范围内没有裸露的 except 语句")




























