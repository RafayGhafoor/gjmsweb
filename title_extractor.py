import re
import os
import pdf_title
import gemma
import json

os.chdir('test_files')

pdf_path = [i for i in os.listdir(".") if i.endswith(".pdf")]

def get_title(path):
    text = pdf_title.extract_first_page_text(path)
    prompt = f"""{text}"""
    response = gemma.ask_gemma(prompt)
    response = response.replace('```json', '')
    response = response.replace('```', '')
    answer = json.loads(response)
    title = answer.get('title')
    author = answer.get('author', [])
    author = ", ".join(author)
    if not author:
        return f"{title}"
    author = re.sub('\d', '', author)
    return f"{title} - {author}"

for pdf in pdf_path:
    # text = pdf_title.extract_first_page_text(pdf)
    print(get_title(pdf))

    
