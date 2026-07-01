import fitz
import json

pdf_path = 'c:/Users/wangh/Desktop/text2sql/ICWS_Lingnan__Haopeng_Wang_Shan Comments.pdf'
doc = fitz.open(pdf_path)

all_annotations = []
for i, page in enumerate(doc):
    annots = list(page.annots())
    if annots:
        print(f'--- Page {i+1} has {len(annots)} annotations ---')
        for annot in annots:
            info = annot.info
            rect = annot.rect
            annot_type = annot.type
            content = info.get('content', '')
            title = info.get('title', '')
            subject = info.get('subject', '')
            # Expand rect manually
            expanded = fitz.Rect(rect.x0-50, rect.y0-50, rect.x1+50, rect.y1+50)
            nearby_text = page.get_text('text', clip=expanded)
            entry = {
                'page': i+1,
                'type': str(annot_type),
                'title': title,
                'subject': subject,
                'content': content,
                'nearby_text': nearby_text[:300]
            }
            all_annotations.append(entry)
            print(f'  Type: {annot_type}')
            print(f'  Title: {title}')
            print(f'  Subject: {subject}')
            print(f'  Content: {repr(content[:500])}')
            print(f'  Nearby text: {nearby_text[:200]}')
            print()

print(f'Total annotations found: {len(all_annotations)}')

with open('c:/Users/wangh/Desktop/text2sql/annotations.json', 'w', encoding='utf-8') as f:
    json.dump(all_annotations, f, ensure_ascii=False, indent=2)
print('Saved to annotations.json')
