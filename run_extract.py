import subprocess, sys
result = subprocess.run([sys.executable, '-m', 'pip', 'show', 'pymupdf'], capture_output=True, text=True)
with open('c:/Users/wangh/Desktop/text2sql/diag.txt', 'w') as f:
    f.write('pip show pymupdf:\n')
    f.write(result.stdout)
    f.write(result.stderr)
    f.write('\n---\n')
try:
    import fitz
    f2 = open('c:/Users/wangh/Desktop/text2sql/diag.txt', 'a')
    f2.write('fitz imported OK, version: ' + fitz.__version__ + '\n')
    doc = fitz.open('c:/Users/wangh/Desktop/text2sql/ICWS_Lingnan__Haopeng_Wang_Shan Comments.pdf')
    f2.write('Pages: ' + str(doc.page_count) + '\n')
    all_text = ''
    for i, page in enumerate(doc):
        t = page.get_text()
        all_text += '--- Page ' + str(i+1) + ' ---\n' + t + '\n'
    doc.close()
    with open('c:/Users/wangh/Desktop/text2sql/pdf_comments.txt', 'w', encoding='utf-8') as out:
        out.write(all_text)
    f2.write('PDF extracted OK\n')
    f2.close()
except Exception as e:
    with open('c:/Users/wangh/Desktop/text2sql/diag.txt', 'a') as f:
        f.write('ERROR: ' + str(e) + '\n')
