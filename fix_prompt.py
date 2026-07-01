"""
修复 SQL 生成 Prompt 的脚本
在 generate_sql 函数的 prompt 中添加更强的字段约束
"""

import re

# 读取文件
with open('app_6.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 找到 generate_sql 函数中的 prompt 定义
# 在 "3. **严格限制**" 这一行后面添加更强的约束

old_rule_3 = '3. **严格限制**：只能使用表结构中明确列出的字段，禁止使用任何未在表结构中出现的字段'

new_rule_3 = '''3. **严格限制**：只能使用表结构中明确列出的字段，禁止使用任何未在表结构中出现的字段
   ⚠️ 特别注意：不要臆造字段名！如果不确定某个字段是否存在，就不要使用它！'''

content = content.replace(old_rule_3, new_rule_3)

# 在 {fb} 前面添加更强的提示
old_fb_line = '{fb}\n直接输出SQL："""'
new_fb_line = '''{fb}

【最后提醒】
- 仔细检查每个字段是否在表结构中明确列出
- 不要使用任何未在上述表结构中出现的字段
- 如果上一轮有错误反馈，必须完全避免那些错误

直接输出SQL："""'''

content = content.replace(old_fb_line, new_fb_line)

# 写回文件
with open('app_6.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("✅ 修复完成")


