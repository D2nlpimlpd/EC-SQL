#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试导入和基本功能"""

print("开始测试导入...")

try:
    from single_table_query import validate_and_fix_sql
    print("OK: single_table_query 导入成功")
except Exception as e:
    print(f"ERROR: single_table_query 导入失败: {e}")
    import traceback
    traceback.print_exc()

try:
    from app_3 import app
    print("OK: app_3 导入成功")
except Exception as e:
    print(f"ERROR: app_3 导入失败: {e}")
    import traceback
    traceback.print_exc()

print("\n所有导入测试完成")

