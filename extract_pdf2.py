import fitz
import os

print('Starting extraction...')
pdf_path = 'c:/Users/wangh/Desktop/text2sql/ICWS_Lingnan__Haopeng_Wang_Shan Comments.pdf'
print('PDF exists:', os.path.exists(pdf_path))

doc = fitz.open(pdf_path)
print('Pages:', doc.page_count)

all_text = ''
for i, page in enumerate(doc):
    t = page.get_text()
    print(f'Page {i+1} chars: {len(t)}')
    all_text += f'--- Page {i+1} ---\n' + t + '\n'

doc.close()

out_path = 'c:/Users/wangh/Desktop/text2sql/pdf_comments.txt'
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(all_text)

print('Written to:', out_path)
print('File size:', os.path.getsize(out_path))











