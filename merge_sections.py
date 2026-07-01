import re, sys
sys.stdout.reconfigure(encoding='utf-8')

with open('c:/Users/wangh/Desktop/text2sql/overleaf.tex', 'r', encoding='utf-8') as f:
    content = f.read()

# Track section and subsection numbers for prefix
# We'll number each enumerate block independently with counters
# Strategy: replace each enumerate/itemize block, numbering items as N.1, N.2, ...
# where N is the block index (1-based)

block_counter = [0]

def replace_enumerate(m):
    block_counter[0] += 1
    N = block_counter[0]
    body = m.group(1)
    # Find all \item entries
    # Split on \item (with optional [...])
    parts = re.split(r'\\item(?:\[[^\]]*\])?', body)
    # parts[0] is before first \item (whitespace), rest are item contents
    items = [p.strip() for p in parts[1:] if p.strip()]
    result_lines = []
    for i, item in enumerate(items, 1):
        # Clean up leading/trailing whitespace and newlines
        item = re.sub(r'^\s+', '', item)
        item = re.sub(r'\s+$', '', item)
        result_lines.append(f'\\textbf{{{N}.{i}}} {item}\n')
    return '\n' + '\n'.join(result_lines) + '\n'

def replace_itemize(m):
    block_counter[0] += 1
    N = block_counter[0]
    body = m.group(1)
    parts = re.split(r'\\item(?:\[[^\]]*\])?', body)
    items = [p.strip() for p in parts[1:] if p.strip()]
    result_lines = []
    for i, item in enumerate(items, 1):
        item = re.sub(r'^\s+', '', item)
        item = re.sub(r'\s+$', '', item)
        result_lines.append(f'\\textbf{{{N}.{i}}} {item}\n')
    return '\n' + '\n'.join(result_lines) + '\n'

# Replace enumerate blocks
content = re.sub(
    r'\\begin\{enumerate\}(.*?)\\end\{enumerate\}',
    replace_enumerate,
    content,
    flags=re.DOTALL
)

# Replace itemize blocks
content = re.sub(
    r'\\begin\{itemize\}(.*?)\\end\{itemize\}',
    replace_itemize,
    content,
    flags=re.DOTALL
)

with open('c:/Users/wangh/Desktop/text2sql/overleaf.tex', 'w', encoding='utf-8') as f:
    f.write(content)

print('Done')
