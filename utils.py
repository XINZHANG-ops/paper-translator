import openai
import PyPDF2
import re
import fitz
import unicodedata
import numpy as np
from tqdm import tqdm
import os
import json


class MatchFailed(Exception):
    def __init__(self, m):
        self.message = m

    def __str__(self):
        return self.message


# process some wired chars
def decompose_ligatures(text):
    return unicodedata.normalize('NFKD', text)


# extract all text from a pdf file
def extract_text_from_pdf(file_path):
    with open(file_path, 'rb') as file:
        reader = PyPDF2.PdfReader(file)
        doc_text = ''
        for page in reader.pages:
            doc_text += page.extract_text()
    doc_text = decompose_ligatures(doc_text)
    return doc_text


# different ways of chunks
def page_chunks(file_path):
    chunks = []
    pages = []
    chunks_names = []
    with open(file_path, 'rb') as file:
        reader = PyPDF2.PdfReader(file)
        for idx, page in enumerate(reader.pages):
            pages.append(idx + 1)
            chunks_names.append(f"Page{idx + 1}")
            chunks.append(decompose_ligatures(page.extract_text()))
    return chunks, pages, chunks_names


def table_of_content_exist_checker(file_path):
    doc = fitz.open(file_path)
    table_of_contents = doc.get_toc(False)
    if len(table_of_contents) == 0:
        return False
    else:
        first_toc = table_of_contents[0]
        info_dict = first_toc[3]
        if 'page' in info_dict and 'to' in info_dict:
            return True
        else:
            return False


def window_chunks(file_name, chunk_size, overlap):
    text = extract_text_from_pdf(file_name)
    words = text.split()
    chunks = []
    for i in range(0, len(words), chunk_size - overlap):
        chunk = words[i:i + chunk_size]
        chunks.append(' '.join(chunk))
    return chunks


def matching_strings_general(query, target):
    special_chars = ".^$*+?{}[]\\|()#"
    query = decompose_ligatures(query)
    target = decompose_ligatures(target)
    query = query.replace(' ', '')
    searching_name = ''
    for char in query:
        if char in special_chars:
            searching_name += '\\'  # 添加一个转义字符 '\'
        searching_name += char + '[^\w]*'  # 让模式匹配任何非字母数字字符
    pattern = re.compile(searching_name, re.IGNORECASE)
    matches = pattern.finditer(target)
    return list(matches)


def matching_strings_strict(query, target):
    query = decompose_ligatures(query)
    target = decompose_ligatures(target)
    pattern = re.compile(query, re.IGNORECASE)
    matches = pattern.finditer(target)
    return list(matches)


def match_idx_by_page_loc(doc, toc_info):
    query = toc_info[1]
    toc_page = toc_info[3]['page']
    toc_x = toc_info[3]['to'].x
    toc_y = toc_info[3]['to'].y
    toc_location = [toc_x, toc_y]

    doc_whole_words_list = []
    words_raw_locs = []
    words_pages = []
    for page_num, page in enumerate(doc):
        words = page.get_text('words')
        for word_info in words:
            word = word_info[4]
            word = decompose_ligatures(word)
            word_len = len(word)
            doc_whole_words_list.append(f'{word}')
            x_left = word_info[0]
            y_left = word_info[1]
            x_right = word_info[2]
            y_right = word_info[3]
            words_raw_locs.extend([[x_left, y_left, x_right, y_right]] * word_len)
            words_pages.extend([page_num] * word_len)
    doc_whole_words = ''.join(doc_whole_words_list)
    doc_whole_words = decompose_ligatures(doc_whole_words)

    matches = matching_strings_general(query, doc_whole_words)
    matches_locations = []
    matches_pages = []
    matches_dist = []
    for match in matches:
        start, end = match.start(), match.end()
        word_loc = words_raw_locs[start]
        page_num = words_pages[start]
        dist = np.linalg.norm(np.array([word_loc[0], word_loc[3]]) - toc_location)
        matches_locations.append([word_loc[0], word_loc[3]])
        matches_pages.append(page_num)
        matches_dist.append(dist)
    page_idx = list(zip(matches_pages, range(len(matches_pages))))
    valid_page_idx = [i for p, i in page_idx if p == toc_page]
    valid_dist_idx = [(matches_dist[i], i) for i in valid_page_idx]
    valid_dist_idx = sorted(valid_dist_idx, key=lambda x: x[1], reverse=True)

    return valid_dist_idx[0][1]


def cut_matching(toc_name, doc_text, doc, toc):
    for idx in range(len(toc_name)):
        query = toc_name[idx:]
        matches = matching_strings_general(query, doc_text)
        if matches:
            break
        else:
            continue
    if len(matches) == 1:
        match = matches[0]
    else:
        toc[1] = query
        match_idx = match_idx_by_page_loc(doc, toc)
        match = matches[match_idx]
    return match


def table_of_content_chunk(file_path):
    chunks = []
    pages = [1]
    chunks_names = ['First Part']
    start = 0
    toc_start = 0
    doc_text = extract_text_from_pdf(file_path)
    doc = fitz.open(file_path)
    table_of_content = doc.get_toc(False)

    for toc in table_of_content:
        page_num = toc[3]['page'] + 1
        pages.append(page_num)
        toc_name = toc[1]
        toc_name = decompose_ligatures(toc_name)
        chunks_names.append(toc_name)
        matches = matching_strings_general(toc_name, doc_text)
        matches = list(matches)
        if len(matches) == 1:
            match = matches[0]
        elif len(matches) > 1:
            match_idx = match_idx_by_page_loc(doc, toc)
            match = matches[match_idx]
        else:
            match = cut_matching(toc_name, doc_text, doc, toc)
        toc_start, toc_end = match.start(), match.end()
        chunk = doc_text[start:toc_start]
        start = toc_start
        chunks.append(chunk)
    chunks.append(doc_text[toc_start:])
    return chunks, pages, chunks_names


def check_analysis_exist(chunks_path):
    if os.path.exists(chunks_path):
        with open(chunks_path) as handle:
            chunk_data = json.loads(handle.read())
        chunks_info = chunk_data['chunks']
        start_idx = len(chunks_info)
        total_usage = chunk_data['total_usage']
        return chunks_info, start_idx, total_usage
    else:
        return [], 0, 0


# a fast ranking function
def find_top_n_faster(vlist, top_n, method="min", show_progress=True):
    if show_progress:
        print(f'finding top {top_n} values')

    top_n_values = vlist[:top_n]
    top_n_value_dict = dict((i, v) for i, v in enumerate(top_n_values))
    top_n_index_dict = dict((i, i) for i, v in enumerate(top_n_values))
    top_n_inverse_value = {v: k for k, v in top_n_value_dict.items()}
    if method == 'min':
        current_extreme_value = max(top_n_value_dict.values())
        current_extreme_key = top_n_inverse_value[current_extreme_value]
    elif method == 'max':
        current_extreme_value = min(top_n_value_dict.values())
        current_extreme_key = top_n_inverse_value[current_extreme_value]
    else:
        pass

    if show_progress:
        from tqdm import tqdm
        loop_pre_fun = tqdm
    else:
        loop_pre_fun = list
    for idx, value in enumerate(loop_pre_fun(vlist[top_n:])):
        list_index = idx + top_n
        if value < current_extreme_value and method == 'min':
            top_n_value_dict[current_extreme_key] = value
            top_n_index_dict[current_extreme_key] = list_index
            top_n_inverse_value = {v: k for k, v in top_n_value_dict.items()}
            current_extreme_value = max(top_n_inverse_value.keys())
            current_extreme_key = top_n_inverse_value[current_extreme_value]
        elif value > current_extreme_value and method == 'max':
            top_n_value_dict[current_extreme_key] = value
            top_n_index_dict[current_extreme_key] = list_index
            top_n_inverse_value = {v: k for k, v in top_n_value_dict.items()}
            current_extreme_value = min(top_n_inverse_value.keys())
            current_extreme_key = top_n_inverse_value[current_extreme_value]
        else:
            continue
    return list(top_n_value_dict.values()), list(top_n_index_dict.values())


# QA with GPT
def chat_completion(question, context, temperature=0.1):
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo-16k",
        n=1,
        temperature=temperature,
        top_p=1,
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content":
                f"{context}\n{question}"},
        ],
    )
    input_tokens_price = 0.003 / 1000
    output_tokens_price = 0.004 / 1000
    input_tokens = response["usage"]["prompt_tokens"]
    output_tokens = response["usage"]["completion_tokens"]
    usage = input_tokens * input_tokens_price + output_tokens * output_tokens_price
    return response['choices'][0]['message']['content'], usage


