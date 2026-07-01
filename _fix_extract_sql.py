# -*- coding: utf-8 -*-
import re

with open('app_6.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find the extract_sql function's closing lines and insert alias-quoting before return
# We look for the unique pattern of the last re.sub in extract_sql followed by return sql
pattern = r'(    sql = re\.sub\(r"\\s\+", " ", sql\)\.strip\(\))(\s*\n    return sql)'
match = re.search(pattern, content)
if not match:
    # try with trailing spaces variant
    pattern = r'(    sql = re\.sub\(r"\\s\+", " ", sql\)\.strip\(\)[ \t]*)(\s*\n    return sql[ \t]*)'
    match = re.search(pattern, content)

if not match:
    print('Pattern not found, dumping relevant lines:')
    for i, line in enumerate(content.split('\n')):
        if 'extract_sql' in line or ('return sql' in line and i < 270):
            print(f'{i+1}: {repr(line)}')
else:
    insert = '''
    # Fix unquoted Chinese/special-char aliases (causes ORA-00923 in Oracle)
    def _quote_alias(m):
        keyword = m.group(1)
        alias = m.group(2)
        if alias.startswith('"') or alias.startswith("'"):
            return m.group(0)
        if re.search(r'[^\x00-\x7F]', alias) or '/' in alias:
            return keyword + '"' + alias + '"'
        return m.group(0)
    sql = re.sub(
        r'(\\bAS\\s+)([^\\s,)("\\x27][^,)(]*?)(?=\\s*(?:,|FROM|WHERE|GROUP|ORDER|HAVING|\\)|$))',
        _quote_alias, sql, flags=re.IGNORECASE
    )
'''
    replacement = match.group(1) + insert + '    return sql'
    content = content[:match.start()] + replacement + content[match.end():]
    with open('app_6.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('SUCCESS')


