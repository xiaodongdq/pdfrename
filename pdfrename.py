import os
import fitz  # PyMuPDF
import re
from habanero import Crossref
import requests

def extract_doi_from_pdf(pdf_path):
    with fitz.open(pdf_path) as doc:
        text = ""
        for page_num in range(min(5, doc.page_count)):  # 读取前5页
            page = doc.load_page(page_num)
            text += page.get_text()
    # 使用正则表达式匹配 DOI
    doi_pattern = r'\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b'
    matches = re.findall(doi_pattern, text, re.IGNORECASE)
    if matches:
        return matches[0]
    else:
        return None

def get_metadata_from_doi(doi):
    cr = Crossref()
    try:
        works = cr.works(ids=doi)
        data = works['message']
        return data
    except Exception as e:
        print(f"通过 CrossRef 获取元数据时出错：{e}")
        # 尝试使用 DOI.org 的 Content Negotiation
        try:
            headers = {'Accept': 'application/vnd.citationstyles.csl+json'}
            response = requests.get(f"https://doi.org/{doi}", headers=headers)
            if response.status_code == 200:
                data = response.json()
                return data
            else:
                print(f"从 DOI.org 获取元数据失败，状态码：{response.status_code}")
                return None
        except Exception as e2:
            print(f"通过 DOI.org 获取元数据时出错：{e2}")
            return None

def extract_metadata_from_pdf(pdf_path):
    # 从 PDF 内容中尝试提取元数据
    with fitz.open(pdf_path) as doc:
        text = ""
        for page_num in range(min(5, doc.page_count)):
            page = doc.load_page(page_num)
            text += page.get_text()
    # 简单的正则表达式匹配标题和作者
    title_pattern = r'(?<=\n)[^\n]{10,100}(?=\n)'
    author_pattern = r'(?<=\n)[^\n]{5,50}(?=\n)'
    lines = text.split('\n')
    # 假设标题在前几行中最长的一行
    title_candidates = sorted(lines[:10], key=len, reverse=True)
    title = title_candidates[0] if title_candidates else None
    # 假设作者在标题附近
    author = None
    for line in lines[1:10]:
        if re.match(r'^[A-Za-z\s,]+$', line):
            author = line
            break
    return {'title': title, 'author': author}

def rename_pdfs(folder_path):
    cr = Crossref()
    for filename in os.listdir(folder_path):
        if filename.lower().endswith('.pdf'):
            pdf_path = os.path.join(folder_path, filename)
            try:
                print(f"正在处理文件：{filename}")
                doi = extract_doi_from_pdf(pdf_path)
                metadata = None
                if doi:
                    metadata = get_metadata_from_doi(doi)
                if not metadata:
                    print(f"无法通过 DOI 获取 {filename} 的元数据，尝试从 PDF 内容中提取。")
                    metadata = extract_metadata_from_pdf(pdf_path)
                    if not metadata or not metadata.get('title') or not metadata.get('author'):
                        print(f"{filename} 的元信息缺失，跳过重命名。")
                        continue
                # 提取作者、年份、标题和期刊名称
                if 'author' in metadata and isinstance(metadata['author'], list):
                    authors = metadata.get('author', [])
                    if not authors:
                        print(f"{filename} 的作者信息缺失，跳过重命名。")
                        continue
                    first_author = authors[0]['family']
                else:
                    first_author = metadata.get('author', '').split(',')[0]
                    if not first_author:
                        print(f"{filename} 的作者信息缺失，跳过重命名。")
                        continue
                first_author = first_author.replace(' ', '_')

                if 'issued' in metadata:
                    year = metadata.get('issued', {}).get('date-parts', [[None]])[0][0]
                else:
                    year = metadata.get('published-online', {}).get('date-parts', [[None]])[0][0]
                if not year:
                    year = 'UnknownYear'

                if 'title' in metadata and isinstance(metadata['title'], list):
                    title = metadata.get('title', [''])[0]
                else:
                    title = metadata.get('title', '')
                if not title:
                    print(f"{filename} 的标题信息缺失，跳过重命名。")
                    continue
                title_clean = ''.join(c for c in title if c.isalnum() or c in (' ', '_', '-')).strip()
                title_clean = title_clean.replace(' ', '_')

                # 提取期刊名称
                if 'container-title' in metadata and isinstance(metadata['container-title'], list):
                    journal = metadata.get('container-title', [''])[0]
                else:
                    journal = metadata.get('journal', '')
                if not journal:
                    journal = 'UnknownJournal'
                journal_clean = ''.join(c for c in journal if c.isalnum() or c in (' ', '_', '-')).strip()
                journal_clean = journal_clean.replace(' ', '_')

                # 生成新的文件名，格式为：期刊名称_第一作者_年份_论文题目.pdf
                new_filename = f"{journal_clean}_{first_author}_{year}_{title_clean}.pdf"
                new_pdf_path = os.path.join(folder_path, new_filename)

                # 防止文件名过长
                if len(new_filename) > 255:
                    new_filename = new_filename[:250] + '.pdf'
                    new_pdf_path = os.path.join(folder_path, new_filename)

                # 检查新文件名是否已存在
                if os.path.exists(new_pdf_path):
                    print(f"目标文件名 {new_filename} 已存在，跳过重命名。")
                    continue

                os.rename(pdf_path, new_pdf_path)
                print(f"已将 {filename} 重命名为 {new_filename}")

            except Exception as e:
                print(f"处理文件 {filename} 时出错：{e}")

if __name__ == '__main__':
    folder_path = './'
    rename_pdfs(folder_path)
