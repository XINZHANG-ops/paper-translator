from flask import Flask, render_template, request, send_from_directory, redirect, url_for, jsonify, session, Response
from werkzeug.utils import secure_filename
import time
import os
import json
import openai
from utils import table_of_content_chunk, chat_completion, table_of_content_exist_checker, page_chunks, check_analysis_exist
from dotenv import load_dotenv
from flask import send_file
import shutil
import tempfile
import zipfile
import tiktoken

app = Flask(__name__)
app.secret_key = "a_complex_string_which_is_difficult_to_guess"
# app.secret_key = os.getenv("SECRET_KEY") if SECRET_KEY is in .env
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads')
app.config['TASK_SHOULD_STOP'] = False
app.config['TASK_RUNNING'] = False
app.config['QUESTION'] = "请用中文告诉我这个章节内容的重点是什么？"
app.config['QUESTION'] = "请用中文告诉我这一页内容的重点是什么？"
analysis_folder = os.path.join(app.root_path, 'static', 'analysis')
progress = 0  # 这个变量将存储进度信息


def get_notes_file_path(filename):
    filename = secure_filename(filename)
    return os.path.join(app.root_path, 'static', 'notes', filename + '.json')


def save_note(filename, page_num, note):
    filepath = get_notes_file_path(filename)

    # 先读取现有的笔记
    if os.path.exists(filepath):
        with open(filepath, 'r') as f:
            notes = json.load(f)
    else:
        notes = {}

    # 更新对应的笔记
    notes[str(page_num)] = note

    # 写回文件
    with open(filepath, 'w') as f:
        json.dump(notes, f)


def load_note(filename, page_num):
    filepath = get_notes_file_path(filename)

    # 如果文件存在，则读取对应的笔记
    if os.path.exists(filepath):
        with open(filepath, 'r') as f:
            notes = json.load(f)
        return notes.get(str(page_num), '')  # 如果没有对应的笔记，则返回空字符串
    else:
        return ''  # 如果文件不存在，则返回空字符串


@app.route('/')
def home():
    pdf_files = os.listdir(app.config['UPLOAD_FOLDER'])  # 修改这里，改为读取上传的 PDF 文件
    return render_template('index.html', pdf_files=pdf_files)


@app.route("/progress")
def get_progress():
    def generate():
        global progress
        while progress < 100:
            yield f"data:{progress}\n\n"
            time.sleep(1)
        progress = 0
    return Response(generate(), mimetype='text/event-stream')


@app.route('/uploads/<path:filename>')  # 新增这个路由，用于提供上传的 PDF 文件
def serve_uploaded_pdf(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/upload', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        file = request.files['file']
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        return redirect(url_for('home'))  # 上传文件后，重定向回主界面
    return render_template('upload.html')


@app.route('/delete/<filename>', methods=['POST'])
def delete_file(filename):
    # 安全地处理文件名
    filename = secure_filename(filename)
    # 删除上传的PDF文件
    pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
    # 删除JSON文件
    json_path = os.path.join(analysis_folder, filename + '.json')
    if os.path.exists(json_path):
        os.remove(json_path)
    # 删除 notes 文件
    notes_path = get_notes_file_path(filename)
    if os.path.exists(notes_path):
        os.remove(notes_path)
    # 重定向回主页
    return redirect(url_for('home'))


@app.route('/delete_analysis/<filename>', methods=['POST'])
def delete_analysis(filename):
    # 安全地处理文件名
    filename = secure_filename(filename)
    # 删除 JSON 文件
    json_path = os.path.join(analysis_folder, filename + '.json')
    if os.path.exists(json_path):
        os.remove(json_path)
    # 删除 notes 文件
    # notes_path = get_notes_file_path(filename)
    # if os.path.exists(notes_path):
    #     os.remove(notes_path)
    # 重定向回主页
    return redirect(url_for('home'))


@app.route('/view/<filename>')
def view_pdf(filename):
    session['filename'] = filename
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    chunks_path = os.path.join(analysis_folder, filename + '.json')

    if os.path.exists(chunks_path):
        with open(chunks_path, 'r') as f:
            chunk_data = json.load(f)
            total_usage = chunk_data.get('total_usage', 'N/A')
    else:
        total_usage = 'N/A'  # 如果 chunks 文件不存在，则 total_usage 为 'N/A'

    theme = session.get('theme', 'white')  # 读取主题设置，如果不存在则默认为白色

    return render_template('view_pdf.html', filename=filename, total_usage=total_usage, theme=theme)


@app.route('/api/generate_chunks/<filename>', methods=['POST'])
def generate_chunks(filename):
    def generate():
        global progress
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        chunks_path = os.path.join(analysis_folder, filename + '.json')
        if_toc_valid = table_of_content_exist_checker(file_path)
        if if_toc_valid:
            app.config['QUESTION'] = "请帮我将这段落内容翻译成中文。"
            chunks, pages, chunks_names = table_of_content_chunk(file_path)
            enc = tiktoken.encoding_for_model("gpt-3.5-turbo-16k")
            chunk_lengths = []
            for chunk in chunks:
                tokens = enc.encode(chunk)
                token_counts = len(tokens)
                chunk_lengths.append(token_counts)
            if max(chunk_lengths) >= 15000:
                chunks, pages, chunks_names = page_chunks(file_path)
            else:
                pass
        else:
            app.config['QUESTION'] = "请帮我将这一页的内容翻译成中文。"
            chunks, pages, chunks_names = page_chunks(file_path)
        # app.config['QUESTION'] = "请帮我将这一页的内容翻译成中文。"
        # chunks, pages, chunks_names = page_chunks(file_path)

        chunks_info, start_idx, total_usage = check_analysis_exist(chunks_path)
        chunks = chunks[start_idx:]
        chunks_count = len(chunks)
        if chunks_count == 0:
            print("Data is Complete!")
            chunk_data = {
                'chunks': chunks_info,
                'pages': pages,
                'chunks_names': chunks_names,
                'total_usage': total_usage
            }
            yield f"data: {json.dumps(chunk_data)}\n\n"  # 使用 SSE 格式推送数据
        else:
            app.config['TASK_RUNNING'] = True
            for i, chunk in enumerate(chunks):
                if app.config['TASK_SHOULD_STOP']:
                    app.config['TASK_SHOULD_STOP'] = False  # 重置变量
                    app.config['TASK_RUNNING'] = False
                    return jsonify({'error': 'task stopped by user'})  # 返回特殊响应
                chunk_words_count = len(chunk.split(' '))
                if chunk_words_count >= 5:
                    chunk, usage = chat_completion(app.config['QUESTION'], chunk, temperature=0.1)
                else:
                    chunk = ' '
                    usage = 0
                progress = (i + 1) / chunks_count * 100
                total_usage += usage
                chunks_info.append(chunk)
                chunk_data = {
                    'chunks': chunks_info,
                    'pages': pages,
                    'chunks_names': chunks_names,
                    'total_usage': total_usage
                }
                yield f"data: {json.dumps(chunk_data)}\n\n"  # 使用 SSE 格式推送数据
                with open(chunks_path, 'w') as f:
                    json.dump(chunk_data, f)
            app.config['TASK_RUNNING'] = False

    return Response(generate(), mimetype='text/event-stream')  # 返回 SSE 流


@app.route('/api/upload_chunks/<filename>', methods=['POST'])
def upload_chunks(filename):
    global progress
    progress = 0
    file = request.files['file']
    file.save(os.path.join(analysis_folder, filename + '.json'))
    with open(os.path.join(analysis_folder, filename + '.json')) as f:
        chunk_data = json.load(f)
    return jsonify(chunk_data)  # 返回生成的 chunks 数据


@app.route('/api/chunks/<int:page_num>', methods=['GET'])
def get_chunks(page_num):
    filename = session['filename']
    if not filename:
        return jsonify({'error': 'No file selected'}), 400  # 如果没有选择文件，返回错误信息

    chunks_path = os.path.join(analysis_folder, filename + '.json')

    if not os.path.exists(chunks_path):
        return jsonify({'error': 'Chunks data not found'}), 404  # 如果没有找到 chunks 数据，返回错误信息

    with open(chunks_path, 'r') as f:
        chunk_data = json.load(f)

    chunks = chunk_data['chunks']
    pages = chunk_data['pages']
    chunks_names = chunk_data['chunks_names']

    page_chunks = []
    for i, page in enumerate(pages):
        if page == page_num:
            try:
                page_chunks.append(chunks_names[i] + ': \n' + chunks[i])
            except:
                continue
    return jsonify({'chunks': page_chunks})


@app.route('/download_chunks/<filename>', methods=['GET'])
def download_chunks(filename):
    chunks_path = os.path.join(analysis_folder, filename + '.json')
    if os.path.exists(chunks_path):
        return send_from_directory(directory=analysis_folder, path=filename + '.json', as_attachment=True)
    else:
        return "File not found.", 404


@app.route('/api/notes/<filename>/<int:page_num>', methods=['GET', 'POST'])
def handle_notes(filename, page_num):
    # 根据请求方法进行不同的处理
    if request.method == 'POST':
        # 保存笔记
        note = request.form.get('note')
        save_note(filename, page_num, note)  # 你需要自己实现这个函数
        return 'Note saved.', 200
    else:
        # 加载笔记
        note = load_note(filename, page_num)  # 你需要自己实现这个函数
        return jsonify({'note': note})


@app.route('/api/notes/<filename>/first_page', methods=['GET'])
def get_first_page_note(filename):
    # 读取第一页的笔记
    note = load_note(filename, 1)
    return jsonify({'note': note})


@app.route('/api/notes_first_nonempty/<filename>', methods=['GET'])
def get_first_nonempty_note(filename):
    filepath = get_notes_file_path(filename)
    if os.path.exists(filepath):
        with open(filepath, 'r') as f:
            notes = json.load(f)

        # 将笔记按页码排序
        sorted_notes = sorted(notes.items(), key=lambda x: int(x[0]))

        # 找到第一条非空的笔记
        for page_num, note in sorted_notes:
            if note.strip():  # 如果笔记不为空
                return jsonify({'page_num': page_num, 'note': note})

    # 如果没有找到非空的笔记，或者文件不存在，则返回空字符串
    return jsonify({'page_num': None, 'note': ''})


@app.route('/download_all')
def download_all():
    # 创建一个临时目录来存放要下载的文件
    temp_dir = tempfile.mkdtemp()

    # 为每一种类型的文件创建一个子文件夹，然后将相应的文件复制到这些子文件夹中
    for folder in ['static/uploads', 'static/notes', 'static/analysis']:
        folder_name = os.path.basename(folder)  # 获取文件夹的名称，例如"notes"
        temp_subdir = os.path.join(temp_dir, folder_name)
        os.makedirs(temp_subdir)
        for filename in os.listdir(folder):
            shutil.copy(os.path.join(folder, filename), temp_subdir)

    # 创建一个ZIP文件
    zip_filename = os.path.join(temp_dir, 'all_files.zip')
    with zipfile.ZipFile(zip_filename, 'w') as zipf:
        for root, dirs, files in os.walk(temp_dir):
            for file in files:
                if file != 'all_files.zip':  # 避免将ZIP文件自身添加到ZIP文件中
                    # arcname 参数用于设置ZIP文件中的文件名。它需要包含子文件夹的名称。
                    arcname = os.path.relpath(os.path.join(root, file), temp_dir)
                    zipf.write(os.path.join(root, file), arcname=arcname)

    # 将ZIP文件发送到客户端
    return send_file(zip_filename, as_attachment=True, download_name='all_files.zip')


@app.route('/api/stop_task', methods=['POST'])
def stop_task():
    if app.config['TASK_RUNNING']:
        app.config['TASK_SHOULD_STOP'] = True
    return jsonify({'success': True})


@app.route('/change_theme', methods=['POST'])
def change_theme():
    theme = request.form.get('theme')
    if theme in ['white', 'dark', 'grey']:
        session['theme'] = theme
    return redirect(request.referrer)


if __name__ == "__main__":
    load_dotenv()
    openai.api_key = os.getenv('OPENAI_API_KEY')
    app.run(host='0.0.0.0', debug=True)
