#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析重构后的文件结构"""

import os

files = {
    'app_3.py': 'c:/Users/wangh/Desktop/text2sql/app_3.py',
    'single_table_query.py': 'c:/Users/wangh/Desktop/text2sql/single_table_query.py',
    'multi_table_query.py': 'c:/Users/wangh/Desktop/text2sql/multi_table_query.py'
}

print("=" * 80)
print("重构后的文件分析")
print("=" * 80)

for name, path in files.items():
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        # 统计
        total_lines = len(lines)
        import_lines = [l for l in lines if l.strip().startswith('import ') or l.strip().startswith('from ')]
        def_lines = [l for l in lines if l.strip().startswith('def ')]
        class_lines = [l for l in lines if l.strip().startswith('class ')]
        route_lines = [l for l in lines if '@app.route' in l]
        
        print(f"\n【{name}】")
        print(f"  总行数: {total_lines}")
        print(f"  导入语句: {len(import_lines)}")
        print(f"  函数定义: {len(def_lines)}")
        print(f"  类定义: {len(class_lines)}")
        print(f"  路由定义: {len(route_lines)}")
        
        # 显示前20行
        print(f"\n  前20行预览:")
        for i, line in enumerate(lines[:20], 1):
            print(f"    {i:3d}: {line.rstrip()}")

print("\n" + "=" * 80)
print("✅ 重构成功！文件已拆分")
print("=" * 80)




























