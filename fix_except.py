#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""查找并修复裸露的 except 语句"""

import re
import sys

print("开始检查 app_3.py...")

try:
    # 读取文件
    with open('app_3.py', 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    print(f"文件总行数: {len(lines)}")
    
    # 查找所有裸露的 except:
    bare_excepts = []
    for i, line in enumerate(lines, 1):
        # 匹配 except: 但不匹配 except Exception: 或 except SomeError:
        if re.match(r'^\s*except:\s*$', line):
            bare_excepts.append((i, line.rstrip()))
    
    # 打印结果
    print(f"\n找到 {len(bare_excepts)} 个裸露的 except 语句：")
    for line_num, line_text in bare_excepts[:20]:
        print(f"  Line {line_num}: {line_text}")
    
    # 修复
    if bare_excepts:
        print("\n开始修复...")
        for line_num, _ in bare_excepts:
            idx = line_num - 1
            # 替换 except: 为 except Exception:
            old_line = lines[idx]
            lines[idx] = lines[idx].replace('except:', 'except Exception:')
            print(f"  修复第 {line_num} 行")
        
        # 保存
        with open('app_3.py', 'w', encoding='utf-8') as f:
            f.writelines(lines)
        
        print(f"\n✅ 已修复 {len(bare_excepts)} 个裸露的 except 语句")
    else:
        print("\n✅ 没有找到裸露的 except 语句")
        
except Exception as e:
    print(f"❌ 错误: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
