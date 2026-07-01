import re

with open('c:/Users/wangh/Desktop/text2sql/overleaf.tex', 'r', encoding='utf-8') as f:
    content = f.read()

before = len(re.findall(r'~?\\eqref\{[^}]+\}', content))

# Remove ~\eqref{...} first, then \eqref{...}
content = re.sub(r'~\\eqref\{[^}]+\}', '', content)
content = re.sub(r'\\eqref\{[^}]+\}', '', content)

with open('c:/Users/wangh/Desktop/text2sql/overleaf.tex', 'w', encoding='utf-8') as f:
    f.write(content)

print(f'Removed {before} eqref references.')
