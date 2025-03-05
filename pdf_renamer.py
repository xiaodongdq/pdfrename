import os
import glob
import re
import requests
from pdfminer.high_level import extract_text
import configparser
import sys
import xml.etree.ElementTree as ET
from scholarly import scholarly  # For Google Scholar search

# 从 PDF 中提取 DOI
def extract_doi_from_pdf(pdf_path):
    try:
        text = extract_text(pdf_path)
        doi_pattern = r'\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b'
        match = re.search(doi_pattern, text, re.IGNORECASE)
        return match.group(0) if match else None
    except Exception as e:
        print(f"Error extracting DOI from {pdf_path}: {e}")
        return None

import pdfplumber
import re

def extract_title_from_pdf(pdf_path):
    """
    从 PDF 中提取标题，模仿 Zotero 的策略，确保标题从上到下顺序正确。
    策略：
    1. 检查第一页，优先选择字体高度最大的文本作为标题候选。
    2. 使用启发式规则排除非标题内容。
    3. 如果最大高度不合适，尝试顶部靠前的显著文本。
    4. 合并相邻同高度文本，按 bottom 从小到大排序。
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                print(f"No pages found in {pdf_path}")
                return None
            
            # 只检查第一页
            first_page = pdf.pages[0]
            page_height = first_page.height
            text_objects = first_page.extract_words(keep_blank_chars=False, use_text_flow=True)
            
            if not text_objects:
                print(f"No text extracted from {pdf_path}")
                return None

            # 按高度分组文本，并记录位置
            height_groups = {}
            for obj in text_objects:
                height = round(obj.get('height', 0), 1)
                text = obj['text'].strip()
                bottom = obj['bottom']  # 使用 bottom 表示底部 y 坐标
                if height not in height_groups:
                    height_groups[height] = []
                height_groups[height].append((bottom, text))

            if not height_groups:
                print(f"No valid text found in {pdf_path}")
                return None

            # 排除模式
            exclude_patterns = [
                r'^\d+$',  # 纯数字（页码）
                r'^(http|doi:)',  # URL 或 DOI
                r'^\d{4}\s*[-–]\s*\d{4}$',  # 年份范围
                r'^(Received|Accepted|Published)',  # 出版信息
                r'^©|Copyright',  # 版权信息
                r'^[A-Za-z]+\s+[A-Za-z]+$',  # 简单作者名
            ]

            # 第一步：尝试最大高度的文本
            max_height = max(height_groups.keys())
            candidates = height_groups[max_height]
            # 按 bottom 从小到大排序（从上到下）
            candidates.sort(key=lambda x: x[0])  # bottom 越小越靠上
            title_parts = [text for bottom, text in candidates if not any(re.search(p, text) for p in exclude_patterns)]
            title = " ".join(title_parts).strip()

            # 检查标题是否合理
            if 10 < len(title) < 250 and max_height > 10:  # 假设标题字体高度大于 10 点
                print(f"Title from max height {max_height}: {title}")
                return title

            # 第二步：如果最大高度不合适，尝试顶部靠前的显著文本
            all_texts = []
            for height, texts in height_groups.items():
                for bottom, text in texts:
                    # 只考虑页面上半部分的文本（bottom < page_height / 2）
                    if bottom < page_height / 2 and height > 8 and not any(re.search(p, text) for p in exclude_patterns):
                        all_texts.append((height, bottom, text))

            if not all_texts:
                print(f"No plausible title found in upper half of {pdf_path}")
                return None

            # 按 bottom 排序，选择顶部靠前的文本（bottom 越小越靠上）
            all_texts.sort(key=lambda x: x[1])  # 从小到大
            top_texts = all_texts[:3]  # 取前 3 个候选
            for height, bottom, text in top_texts:
                if 10 < len(text) < 250:
                    print(f"Title from top text (height {height}, bottom {bottom}): {text}")
                    return text

            # 第三步：合并顶部相邻的同高度文本
            height_candidates = {}
            for height, bottom, text in all_texts:
                if height not in height_candidates:
                    height_candidates[height] = []
                height_candidates[height].append((bottom, text))

            for height in sorted(height_candidates.keys(), reverse=True):  # 从大到小尝试
                texts = sorted(height_candidates[height], key=lambda x: x[0])  # 按 bottom 从小到大
                title = " ".join(text for _, text in texts).strip()
                if 10 < len(title) < 250:
                    print(f"Title from merged height {height}: {title}")
                    return title

            print(f"No plausible title found in {pdf_path}")
            return None

    except Exception as e:
        print(f"Error extracting title from {pdf_path}: {e}")
        return None
    

# 通过 CrossRef API 获取元信息（基于 DOI）
def get_metadata_from_crossref(doi):
    url = f"https://api.crossref.org/works/{doi}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"CrossRef failed for DOI {doi}: Status {response.status_code}")
            return None
    except Exception as e:
        print(f"Error fetching from CrossRef for DOI {doi}: {e}")
        return None

# 通过 DOI.org 获取元信息（基于 DOI）
def get_metadata_from_doi_org(doi):
    url = f"https://doi.org/{doi}"
    headers = {"Accept": "application/vnd.citationstyles.csl+json"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"DOI.org failed for DOI {doi}: Status {response.status_code}")
            return None
    except Exception as e:
        print(f"Error fetching from DOI.org for DOI {doi}: {e}")
        return None

# 通过 PubMed API 获取元信息（基于 DOI 或标题）
def get_metadata_from_pubmed(query, is_doi=True):
    try:
        if is_doi:
            url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={query}[doi]&retmode=json"
        else:
            url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={query}&retmode=json"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data['esearchresult']['idlist']:
                pmid = data['esearchresult']['idlist'][0]
                fetch_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={pmid}&retmode=xml"
                fetch_response = requests.get(fetch_url, timeout=10)
                if fetch_response.status_code == 200:
                    root = ET.fromstring(fetch_response.content)
                    article = root.find(".//Article")
                    if article is not None:
                        journal = article.findtext(".//Journal/Title") or ""
                        title = article.findtext(".//ArticleTitle") or ""
                        year = article.find(".//PubDate/Year")
                        year = year.text if year is not None else None
                        authors = article.findall(".//Author")
                        first_author = authors[0].findtext(".//LastName") if authors else ""
                        return {'journal': journal, 'title': title, 'year': year, 'first_author': first_author}
        print(f"PubMed failed for query {query}: No matching record")
        return None
    except Exception as e:
        print(f"Error fetching from PubMed for query {query}: {e}")
        return None

# 通过 arXiv API 获取元信息（基于 DOI 或标题）
def get_metadata_from_arxiv(query, is_doi=True):
    url = f"http://export.arxiv.org/api/query?search_query={'doi:' + query if is_doi else query}&start=0&max_results=1"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            root = ET.fromstring(response.content)
            entry = root.find("{http://www.w3.org/2005/Atom}entry")
            if entry is not None:
                title = entry.findtext("{http://www.w3.org/2005/Atom}title") or ""
                year = entry.findtext("{http://www.w3.org/2005/Atom}published")
                year = year[:4] if year else None
                authors = entry.findall("{http://www.w3.org/2005/Atom}author")
                first_author = authors[0].findtext("{http://www.w3.org/2005/Atom}name") if authors else ""
                return {'journal': 'arXiv', 'title': title, 'year': year, 'first_author': first_author}
        print(f"arXiv failed for query {query}: No matching record")
        return None
    except Exception as e:
        print(f"Error fetching from arXiv for query {query}: {e}")
        return None

# 通过 Google Scholar 获取元信息（基于标题）
def get_metadata_from_scholar(title):
    try:
        search_query = scholarly.search_pubs(title)
        result = next(search_query, None)
        print(result)
        if result:
            bib = result['bib']
            return {
                'journal': bib.get('venue'),
                'title': bib.get('title'),
                'year': bib.get('pub_year', None),
                'first_author': bib.get('author', [''])[0].split()[-1] if bib.get('author') else ''
            }
        print(f"Google Scholar failed for title {title}: No matching record")
        return None
    except Exception as e:
        print(f"Error fetching from Google Scholar for title {title}: {e}")
        return None

# 综合获取元信息（DOI优先，标题次之）
def get_metadata(query, is_doi=True):
    if is_doi:
        sources = [
            ("CrossRef", lambda q: get_metadata_from_crossref(q)),
            ("DOI.org", lambda q: get_metadata_from_doi_org(q)),
            ("PubMed", lambda q: get_metadata_from_pubmed(q, True)),
            # ("arXiv", lambda q: get_metadata_from_arxiv(q, True))
        ]
    else:
        sources = [
            ("Google Scholar", lambda q: get_metadata_from_scholar(q)),
            ("PubMed", lambda q: get_metadata_from_pubmed(q, False)),
            # ("arXiv", lambda q: get_metadata_from_arxiv(q, False))
        ]
    for source_name, source_func in sources:
        metadata = source_func(query)
        if metadata:
            print(f"Metadata retrieved from {source_name}")
            print(metadata)
            return metadata
    print(f"No metadata retrieved for query {query} from any source")
    return None

# 从元信息中提取所需字段
def extract_info_from_metadata(metadata):
    if not metadata:
        return None
    if 'message' in metadata:  # CrossRef格式
        message = metadata['message']
        journal = message.get('container-title', [''])[0]
        authors = message.get('author', [])
        first_author = authors[0].get('family', '') if authors else ''
        year = message.get('published-print', {}).get('date-parts', [[None]])[0][0] or \
               message.get('published-online', {}).get('date-parts', [[None]])[0][0]
        title = message.get('title', [''])[0]
    else:  # 其他来源的通用格式
        journal = metadata.get('journal', '')
        first_author = metadata.get('first_author', '')
        year = metadata.get('year', None)
        title = metadata.get('title', '')
    return {
        'journal': journal,
        'first_author': first_author,
        'year': year,
        'title': title
    }

# 替换非法字符
def sanitize_filename(filename):
    return re.sub(r'[\/:*?"<>|]', '_', filename)

# 根据缩写列表替换期刊名称
def abbreviate_journal(journal, abbreviations):
    return abbreviations.get(journal, journal)

# 生成新文件名
def generate_new_filename(info, naming_format, abbreviations):
    journal = abbreviate_journal(info['journal'], abbreviations)
    journal = sanitize_filename(journal)
    first_author = sanitize_filename(info['first_author'])
    year = info['year'] if info['year'] else 'Unknown'
    title = sanitize_filename(info['title'])
    new_name = naming_format.format(journal=journal, first_author=first_author, year=year, title=title)
    return new_name + '.pdf'

# 重命名文件，避免覆盖
def rename_pdf(old_path, new_name):
    new_path = os.path.join(os.path.dirname(old_path), new_name)
    if os.path.exists(new_path):
        base, ext = os.path.splitext(new_name)
        i = 1
        while os.path.exists(new_path):
            new_path = os.path.join(os.path.dirname(old_path), f"{base}({i}){ext}")
            i += 1
    os.rename(old_path, new_path)
    return new_path

def load_abbreviations(config):
    if 'ABBREVIATIONS' not in config:
        return {}
    abbreviations = dict(config['ABBREVIATIONS'])
    print("Parsed abbreviations:", abbreviations)  # 调试输出
    return abbreviations

def create_default_config(config_path):
    """创建默认的 config.ini 模板文件"""
    config = configparser.ConfigParser(delimiters=('=',))
    config.optionxform = str  # 保留大小写
    
    # 设置默认配置
    config['DEFAULT'] = {
        'naming_format': '{journal}_{first_author}_{year}_{title}'
    }
    config['ABBREVIATIONS'] = {
        'Journal of Geophysical Research: Solid Earth': 'JGRSE',
    }
    
    # 写入文件
    with open(config_path, 'w', encoding='utf-8') as configfile:
        config.write(configfile)
    
    return config

def main():
    # 获取当前目录（支持打包后的路径）
    if getattr(sys, 'frozen', False):
        base_path = os.path.dirname(sys.executable)
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))

    config_path = os.path.join(base_path, 'config.ini')
    print('cp:', config_path)

    # 检查 config.ini 是否存在
    config = configparser.ConfigParser(delimiters=('=',))
    config.optionxform = str
    
    if not os.path.exists(config_path):
        print("未找到", config_path, "文件，正在创建默认模板...")
        config = create_default_config(config_path)
        print(f"已创建模板 config.ini 文件，路径：{config_path}")
        print("请检查并编辑 config.ini 文件以满足您的需求，然后重新运行程序。")
        print("程序将退出...")
        sys.exit(0)
    else:
        config.read(config_path)
        print(f"已加载配置文件：{config_path}")

    # 从 [DEFAULT] 获取命名格式
    naming_format = config['DEFAULT'].get('naming_format', '{journal}_{first_author}_{year}_{title}')
    print("Naming format:", naming_format)
    
    # 从 [ABBREVIATIONS] 获取缩写列表
    abbreviations = load_abbreviations(config)
    
    # 获取当前目录下所有 PDF 文件
    pdf_p = os.path.join(base_path, '*.pdf')
    pdf_files = glob.glob(pdf_p)
    print('pdf_p:', pdf_p)
    if not pdf_files:
        print("No PDF files found in the current directory.")
        return

    for pdf in pdf_files:
        print(f"Processing {pdf}...")
        # 首先尝试提取 DOI
        doi = extract_doi_from_pdf(pdf)
        if doi:
            print(f"DOI found: {doi}")
            metadata = get_metadata(doi, is_doi=True)
            info = extract_info_from_metadata(metadata)
            if info:
                new_name = generate_new_filename(info, naming_format, abbreviations)
                new_path = rename_pdf(pdf, new_name)
                print(f"Renamed {pdf} to {os.path.basename(new_path)}")
            else:
                print(f"Failed to get metadata for DOI {doi}")
        else:
            print(f"No DOI found in {pdf}, attempting title extraction...")
            title = extract_title_from_pdf(pdf)
            if title:
                print(f"Title extracted: {title}")
                metadata = get_metadata(title, is_doi=False)
                info = extract_info_from_metadata(metadata)
                if info:
                    new_name = generate_new_filename(info, naming_format, abbreviations)
                    new_path = rename_pdf(pdf, new_name)
                    print(f"Renamed {pdf} to {os.path.basename(new_path)}")
                else:
                    print(f"Failed to get metadata for title {title}")
            else:
                print(f"Failed to extract title from {pdf}")

if __name__ == "__main__":
    main()