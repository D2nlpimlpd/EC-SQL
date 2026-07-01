import sys
sys.path.insert(0, 'c:/Users/wangh/Desktop/text2sql')

print("检查重构后的文件...")
print("=" * 80)

# 检查 app_3.py
try:
    with open('c:/Users/wangh/Desktop/text2sql/app_3.py', 'r', encoding='utf-8') as f:
        app3_lines = f.readlines()
    print(f"✅ app_3.py: {len(app3_lines)} 行")
    
    # 检查是否有导入语句
    imports = [l for l in app3_lines[:50] if 'import' in l.lower()]
    print(f"   前50行中的导入语句: {len(imports)} 个")
    if imports:
        print("   导入示例:")
        for imp in imports[:5]:
            print(f"     {imp.strip()}")
except Exception as e:
    print(f"❌ 读取 app_3.py 失败: {e}")

print()

# 检查 single_table_query.py
try:
    with open('c:/Users/wangh/Desktop/text2sql/single_table_query.py', 'r', encoding='utf-8') as f:
        single_lines = f.readlines()
    print(f"✅ single_table_query.py: {len(single_lines)} 行")
    
    funcs = [l for l in single_lines if l.strip().startswith('def ')]
    print(f"   函数定义: {len(funcs)} 个")
except Exception as e:
    print(f"❌ 读取 single_table_query.py 失败: {e}")

print()

# 检查 multi_table_query.py
try:
    with open('c:/Users/wangh/Desktop/text2sql/multi_table_query.py', 'r', encoding='utf-8') as f:
        multi_lines = f.readlines()
    print(f"✅ multi_table_query.py: {len(multi_lines)} 行")
    
    funcs = [l for l in multi_lines if l.strip().startswith('def ')]
    print(f"   函数定义: {len(funcs)} 个")
except Exception as e:
    print(f"❌ 读取 multi_table_query.py 失败: {e}")

print()
print("=" * 80)
print("重构统计:")
print(f"  原始文件: 5774 行")
print(f"  重构后总计: {len(app3_lines) + len(single_lines) + len(multi_lines)} 行")
print(f"  app_3.py: {len(app3_lines)} 行 ({len(app3_lines)/5774*100:.1f}%)")
print(f"  single_table_query.py: {len(single_lines)} 行")
print(f"  multi_table_query.py: {len(multi_lines)} 行")
print("=" * 80)




























