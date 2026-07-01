import fitz
import sys

doc = fitz.open('c:/Users/wangh/Desktop/text2sql/ICWS_Lingnan__Haopeng_Wang_Shan Comments.pdf')
with open('c:/Users/wangh/Desktop/text2sql/pdf_comments.txt', 'w', encoding='utf-8') as f:
    for i, page in enumerate(doc):
        f.write(f'--- Page {i+1} ---\n')
        f.write(page.get_text())
        f.write('\n')
doc.close()
print('Done')











