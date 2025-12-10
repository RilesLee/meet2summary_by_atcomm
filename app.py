from flask import Flask, request, jsonify, send_file, session
import os
import json
import subprocess
import http.client
import mimetypes
from codecs import encode
from urllib.parse import urlparse
import threading
import shutil
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import markdown
from functools import wraps
import pymysql
from pymysql.cursors import DictCursor
import ssl

MAX_CONCURRENT_REQUESTS = 5
REQUEST_DELAY = 0.5
MAX_RETRIES = 3
CHUNK_SIZE = 1024 * 1024

app = Flask(__name__, static_folder='static', static_url_path='')
app.secret_key = 'your-secret-key-change-this-to-random-string'
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
USERS_FILE = os.path.join(BASE_DIR, 'users.json')
DB_CONFIG_FILE = os.path.join(BASE_DIR, 'db_config.json')
TEMP_UPLOAD_DIR = os.path.join(BASE_DIR, 'temp_uploads')
PROMPT_DIR = os.path.join(BASE_DIR, 'prompt')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(BASE_DIR, 'app.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(TEMP_UPLOAD_DIR, exist_ok=True)
os.makedirs(PROMPT_DIR, exist_ok=True)

db_config = None

def init_default_prompt():
    """初始化默认prompt文件"""
    default_prompt_file = os.path.join(PROMPT_DIR, '会议总结.txt')
    if not os.path.exists(default_prompt_file):
        default_content = """请清楚的记住！你是一名世界顶级的商业助理！精通各种行业的商业会议重点提炼和总结以及计划安排，用户会将会议录音转为文字，你需要将文字按照顶级的商业思维进行重点提炼和会议总结以及后续的计划安排（ToDo List），用户提供的文字中可能会因为语音转文字的原因出现一小部分错别字（比如Base可能会识别为北市 背时 被试这种，或者飞往某个国家可能会被识别为非），你应该通过前后语境自行修正。但是要注意！会议总结的核心是提炼会议中的重点以及告诉所有人接下来要做的事，所有要避免长篇大论，需要在提炼重点总结的同时避免太多无用的内容输出。"""
        with open(default_prompt_file, 'w', encoding='utf-8') as f:
            f.write(default_content)
        logger.info("已创建默认prompt文件")

def load_db_config():
    global db_config
    with open(DB_CONFIG_FILE, 'r', encoding='utf-8') as f:
        db_config = json.load(f)
    logger.info("数据库配置加载成功")

def get_db_connection():
    return pymysql.connect(
        host=db_config['host'],
        port=db_config['port'],
        user=db_config['user'],
        password=db_config['password'],
        database=db_config['database'],
        charset=db_config['charset'],
        cursorclass=DictCursor
    )

def save_task_to_db(task):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        sql = """
            INSERT INTO tasks (
                id, username, filename, prompt_name, status, current_step, progress,
                file_path, json_file, html_file, pdf_file, txt_file,
                error, created_at, completed_at, failed_at,
                upload_completed_at, mp3_started_at, mp3_completed_at,
                slice_started_at, slice_completed_at,
                transcribe_started_at, transcribe_completed_at,
                summary_started_at, summary_completed_at,
                pdf_started_at, pdf_completed_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            ) ON DUPLICATE KEY UPDATE
                status = VALUES(status),
                current_step = VALUES(current_step),
                progress = VALUES(progress),
                prompt_name = VALUES(prompt_name),
                json_file = VALUES(json_file),
                html_file = VALUES(html_file),
                pdf_file = VALUES(pdf_file),
                txt_file = VALUES(txt_file),
                error = VALUES(error),
                completed_at = VALUES(completed_at),
                failed_at = VALUES(failed_at),
                upload_completed_at = VALUES(upload_completed_at),
                mp3_started_at = VALUES(mp3_started_at),
                mp3_completed_at = VALUES(mp3_completed_at),
                slice_started_at = VALUES(slice_started_at),
                slice_completed_at = VALUES(slice_completed_at),
                transcribe_started_at = VALUES(transcribe_started_at),
                transcribe_completed_at = VALUES(transcribe_completed_at),
                summary_started_at = VALUES(summary_started_at),
                summary_completed_at = VALUES(summary_completed_at),
                pdf_started_at = VALUES(pdf_started_at),
                pdf_completed_at = VALUES(pdf_completed_at)
        """
        
        cursor.execute(sql, (
            task['id'],
            task['username'],
            task['filename'],
            task.get('prompt_name', '会议总结'),
            task['status'],
            task.get('current_step'),
            task.get('progress', 0),
            task.get('file_path'),
            task.get('json_file'),
            task.get('html_file'),
            task.get('pdf_file'),
            task.get('txt_file'),
            task.get('error'),
            task.get('created_at'),
            task.get('completed_at'),
            task.get('failed_at'),
            task.get('upload_completed_at'),
            task.get('mp3_started_at'),
            task.get('mp3_completed_at'),
            task.get('slice_started_at'),
            task.get('slice_completed_at'),
            task.get('transcribe_started_at'),
            task.get('transcribe_completed_at'),
            task.get('summary_started_at'),
            task.get('summary_completed_at'),
            task.get('pdf_started_at'),
            task.get('pdf_completed_at')
        ))
        
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"保存任务到数据库失败: {str(e)}", exc_info=True)

def get_task_from_db(task_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM tasks WHERE id = %s", (task_id,))
        task = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        if task:
            datetime_fields = ['created_at', 'completed_at', 'failed_at',
                             'upload_completed_at', 'mp3_started_at', 'mp3_completed_at',
                             'slice_started_at', 'slice_completed_at',
                             'transcribe_started_at', 'transcribe_completed_at',
                             'summary_started_at', 'summary_completed_at',
                             'pdf_started_at', 'pdf_completed_at']
            for key in datetime_fields:
                if task.get(key):
                    task[key] = task[key].isoformat()
        
        return task
    except Exception as e:
        logger.error(f"从数据库获取任务失败: {str(e)}", exc_info=True)
        return None

def get_user_tasks_from_db(username):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT * FROM tasks WHERE username = %s ORDER BY created_at DESC",
            (username,)
        )
        tasks = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        datetime_fields = ['created_at', 'completed_at', 'failed_at',
                         'upload_completed_at', 'mp3_started_at', 'mp3_completed_at',
                         'slice_started_at', 'slice_completed_at',
                         'transcribe_started_at', 'transcribe_completed_at',
                         'summary_started_at', 'summary_completed_at',
                         'pdf_started_at', 'pdf_completed_at']
        for task in tasks:
            for key in datetime_fields:
                if task.get(key):
                    task[key] = task[key].isoformat()
        
        return tasks
    except Exception as e:
        logger.error(f"从数据库获取用户任务失败: {str(e)}", exc_info=True)
        return []

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"users": []}

def save_users(users_data):
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(users_data, f, indent=2, ensure_ascii=False)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return jsonify({'error': '未登录'}), 401
        return f(*args, **kwargs)
    return decorated_function

def get_user_dir(username):
    user_dir = os.path.join(UPLOAD_DIR, username)
    for dir_path in ['file', 'mp3', 'output', 'gpt/summary']:
        os.makedirs(os.path.join(user_dir, dir_path), exist_ok=True)
    return user_dir

def load_config():
    config_path = os.path.join(BASE_DIR, 'config.json')
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_config(config):
    config_path = os.path.join(BASE_DIR, 'config.json')
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

def load_prompt(prompt_name):
    """加载prompt内容"""
    prompt_file = os.path.join(PROMPT_DIR, f'{prompt_name}.txt')
    if os.path.exists(prompt_file):
        with open(prompt_file, 'r', encoding='utf-8') as f:
            return f.read()
    return None

@app.route('/')
def index():
    return send_file('static/index.html')

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username', '').strip()
    
    if not username:
        return jsonify({'error': '用户名不能为空'}), 400
    
    users_data = load_users()
    if username not in users_data['users']:
        users_data['users'].append(username)
        save_users(users_data)
    
    session['username'] = username
    get_user_dir(username)
    
    logger.info(f"用户登录: {username}")
    return jsonify({'message': '登录成功', 'username': username})

@app.route('/api/logout', methods=['POST'])
def logout():
    username = session.get('username')
    session.pop('username', None)
    logger.info(f"用户登出: {username}")
    return jsonify({'message': '登出成功'})

@app.route('/api/current-user', methods=['GET'])
def current_user():
    if 'username' in session:
        return jsonify({'username': session['username']})
    return jsonify({'username': None})

@app.route('/api/prompts', methods=['GET'])
@login_required
def get_prompts():
    """获取所有prompt列表"""
    try:
        prompts = []
        for filename in os.listdir(PROMPT_DIR):
            if filename.endswith('.txt'):
                name = filename[:-4]
                prompts.append(name)
        return jsonify({'prompts': prompts, 'default': '会议总结'})
    except Exception as e:
        logger.error(f"获取prompt列表失败: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/prompt/<prompt_name>', methods=['GET'])
@login_required
def get_prompt(prompt_name):
    """获取指定prompt内容"""
    try:
        content = load_prompt(prompt_name)
        if content is None:
            return jsonify({'error': 'Prompt不存在'}), 404
        return jsonify({'name': prompt_name, 'content': content})
    except Exception as e:
        logger.error(f"获取prompt失败: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/prompt', methods=['POST'])
@login_required
def save_prompt():
    """保存prompt"""
    try:
        data = request.json
        name = data.get('name', '').strip()
        content = data.get('content', '').strip()
        
        if not name:
            return jsonify({'error': 'Prompt名称不能为空'}), 400
        
        if not content:
            return jsonify({'error': 'Prompt内容不能为空'}), 400
        
        prompt_file = os.path.join(PROMPT_DIR, f'{name}.txt')
        with open(prompt_file, 'w', encoding='utf-8') as f:
            f.write(content)
        
        logger.info(f"Prompt已保存: {name}")
        return jsonify({'message': 'Prompt保存成功', 'name': name})
    except Exception as e:
        logger.error(f"保存prompt失败: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/prompt/<prompt_name>', methods=['DELETE'])
@login_required
def delete_prompt(prompt_name):
    """删除prompt"""
    try:
        if prompt_name == '会议总结':
            return jsonify({'error': '默认prompt不能删除'}), 400
        
        prompt_file = os.path.join(PROMPT_DIR, f'{prompt_name}.txt')
        if not os.path.exists(prompt_file):
            return jsonify({'error': 'Prompt不存在'}), 404
        
        os.remove(prompt_file)
        logger.info(f"Prompt已删除: {prompt_name}")
        return jsonify({'message': 'Prompt删除成功'})
    except Exception as e:
        logger.error(f"删除prompt失败: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/upload-chunk', methods=['POST'])
@login_required
def upload_chunk():
    username = session['username']
    
    chunk = request.files.get('chunk')
    chunk_index = int(request.form.get('chunkIndex'))
    total_chunks = int(request.form.get('totalChunks'))
    filename = request.form.get('filename')
    upload_id = request.form.get('uploadId')
    
    if not chunk or not filename or not upload_id:
        return jsonify({'error': '缺少必要参数'}), 400
    
    temp_dir = os.path.join(TEMP_UPLOAD_DIR, upload_id)
    os.makedirs(temp_dir, exist_ok=True)
    
    chunk_path = os.path.join(temp_dir, f'chunk_{chunk_index}')
    chunk.save(chunk_path)
    
    logger.info(f"[{upload_id}] 收到分块 {chunk_index + 1}/{total_chunks}")
    
    if chunk_index == total_chunks - 1:
        name_without_ext = os.path.splitext(filename)[0]
        ext = os.path.splitext(filename)[1]
        timestamp = datetime.now().strftime('%y%m%d%H%M%S%f')[:-4]
        new_filename = f"{name_without_ext}_{timestamp}{ext}"
        
        user_dir = get_user_dir(username)
        final_path = os.path.join(user_dir, 'file', new_filename)
        
        with open(final_path, 'wb') as final_file:
            for i in range(total_chunks):
                chunk_file = os.path.join(temp_dir, f'chunk_{i}')
                with open(chunk_file, 'rb') as cf:
                    final_file.write(cf.read())
        
        shutil.rmtree(temp_dir)
        
        logger.info(f"[{upload_id}] 文件合并完成: {new_filename}")
        
        return jsonify({
            'message': '上传完成',
            'filename': new_filename,
            'complete': True
        })
    
    return jsonify({
        'message': f'分块 {chunk_index + 1}/{total_chunks} 上传成功',
        'complete': False
    })

@app.route('/api/upload-complete', methods=['POST'])
@login_required
def upload_complete():
    username = session['username']
    data = request.json
    filename = data.get('filename')
    prompt_name = data.get('prompt_name', '会议总结')
    
    if not filename:
        return jsonify({'error': '文件名为空'}), 400
    
    logger.info("=" * 80)
    logger.info(f"用户 {username} 完成文件上传: {filename}, 使用Prompt: {prompt_name}")
    
    user_dir = get_user_dir(username)
    file_path = os.path.join(user_dir, 'file', filename)
    
    if not os.path.exists(file_path):
        logger.error(f"文件不存在: {file_path}")
        return jsonify({'error': '文件不存在'}), 404
    
    base_name = os.path.splitext(filename)[0]
    file_size = os.path.getsize(file_path)
    logger.info(f"文件大小: {file_size / 1024 / 1024:.2f} MB")
    
    upload_start = datetime.now()
    
    task_id = f"{username}_{base_name}_{int(time.time())}"
    task = {
        'id': task_id,
        'username': username,
        'filename': filename,
        'prompt_name': prompt_name,
        'status': 'processing',
        'current_step': '上传完成',
        'progress': 0,
        'file_path': file_path,
        'error': None,
        'created_at': upload_start.isoformat(),
        'upload_completed_at': upload_start.isoformat(),
        'json_file': None,
        'html_file': None,
        'pdf_file': None,
        'txt_file': None,
        'completed_at': None,
        'failed_at': None,
        'mp3_started_at': None,
        'mp3_completed_at': None,
        'slice_started_at': None,
        'slice_completed_at': None,
        'transcribe_started_at': None,
        'transcribe_completed_at': None,
        'summary_started_at': None,
        'summary_completed_at': None,
        'pdf_started_at': None,
        'pdf_completed_at': None
    }
    
    logger.info(f"任务创建: {task_id}")
    save_task_to_db(task)
    
    thread = threading.Thread(target=process_task, args=(task_id,))
    thread.daemon = True
    thread.start()
    logger.info(f"后台处理线程已启动: {task_id}")
    logger.info("=" * 80)
    
    return jsonify({'task_id': task_id, 'message': '上传成功，开始自动处理'})

def process_task(task_id):
    task = get_task_from_db(task_id)
    if not task:
        logger.error(f"任务不存在: {task_id}")
        return
    
    try:
        username = task['username']
        filename = task['filename']
        base_name = os.path.splitext(filename)[0]
        user_dir = get_user_dir(username)
        
        logger.info(f"开始自动处理任务: {task_id}")
        
        task['current_step'] = '转换MP3'
        task['progress'] = 20
        task['mp3_started_at'] = datetime.now().isoformat()
        save_task_to_db(task)
        logger.info(f"[{task_id}] 步骤2: 转换MP3")
        
        source_file = task['file_path']
        ext = os.path.splitext(filename)[1].lower()
        mp3_dir = os.path.join(user_dir, 'mp3', base_name)
        os.makedirs(mp3_dir, exist_ok=True)
        mp3_file = os.path.join(mp3_dir, f"{base_name}.mp3")
        
        if ext == '.mp3':
            shutil.copy(source_file, mp3_file)
        else:
            cmd = ['ffmpeg', '-i', source_file, '-acodec', 'libmp3lame', '-y', mp3_file]
            subprocess.run(cmd, check=True, capture_output=True)
        
        task['mp3_completed_at'] = datetime.now().isoformat()
        save_task_to_db(task)
        logger.info(f"[{task_id}] MP3转换完成")
        
        task['current_step'] = '分段处理'
        task['progress'] = 30
        task['slice_started_at'] = datetime.now().isoformat()
        save_task_to_db(task)
        logger.info(f"[{task_id}] 步骤3: 分段处理")
        
        file_size = os.path.getsize(mp3_file)
        max_size = 5 * 1024 * 1024
        
        if file_size <= max_size:
            slice_files = [mp3_file]
            logger.info(f"[{task_id}] 文件不超过5MB，无需分段")
        else:
            slice_dir = os.path.join(mp3_dir, 'slice')
            os.makedirs(slice_dir, exist_ok=True)
            
            probe_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', 
                        '-of', 'default=noprint_wrappers=1:nokey=1', mp3_file]
            duration = float(subprocess.check_output(probe_cmd).decode().strip())
            
            segment_size = 4 * 1024 * 1024
            num_segments = (file_size + segment_size - 1) // segment_size
            segment_duration = duration / num_segments
            
            cmd = [
                'ffmpeg', '-i', mp3_file, '-f', 'segment',
                '-segment_time', str(segment_duration),
                '-c', 'copy', '-y',
                os.path.join(slice_dir, '%04d.mp3')
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            
            slice_files = sorted([os.path.join(slice_dir, f) for f in os.listdir(slice_dir) if f.endswith('.mp3')])
            logger.info(f"[{task_id}] 分段完成，共{len(slice_files)}个文件")
        
        task['slice_completed_at'] = datetime.now().isoformat()
        save_task_to_db(task)
        
        task['current_step'] = 'Whisper转录'
        task['progress'] = 40
        task['transcribe_started_at'] = datetime.now().isoformat()
        save_task_to_db(task)
        logger.info(f"[{task_id}] 步骤4: Whisper转录")
        
        config = load_config()
        results = [None] * len(slice_files)
        errors = []
        
        def transcribe_single_file(index, file_path):
            try:
                time.sleep(REQUEST_DELAY * index)
                logger.info(f"[{task_id}] 开始转录文件 {index}: {os.path.basename(file_path)}")
                
                result = call_whisper_api(file_path, config)
                logger.info(f"[{task_id}] 文件 {index} API返回: {result}")
                
                if 'text' in result and result['text']:
                    text = result['text']
                    segment_name = os.path.basename(file_path)
                    labeled_text = f"\n[文件: {base_name} | 分段: {segment_name}]\n{text}\n"
                    results[index] = labeled_text
                    logger.info(f"[{task_id}] ✓ 文件 {index} 转录成功，文本长度: {len(text)}")
                    return index, True, None
                elif 'error' in result:
                    error_msg = result['error'].get('message', str(result['error']))
                    logger.error(f"[{task_id}] ✗ 文件 {index} API错误: {error_msg}")
                    
                    if 'too short' in error_msg.lower():
                        results[index] = f"\n[文件: {base_name} | 分段: {os.path.basename(file_path)} - 音频太短]\n"
                        return index, True, None
                    
                    errors.append(f"文件{index}: {error_msg}")
                    return index, False, error_msg
                else:
                    error_msg = f'API返回无文本且无错误信息: {result}'
                    logger.error(f"[{task_id}] ✗ 文件 {index} {error_msg}")
                    errors.append(f"文件{index}: {error_msg}")
                    return index, False, error_msg
            except Exception as e:
                error_msg = f"异常: {str(e)}"
                logger.error(f"[{task_id}] ✗ 文件 {index} {error_msg}", exc_info=True)
                errors.append(f"文件{index}: {error_msg}")
                return index, False, error_msg
        
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_REQUESTS) as executor:
            futures = {
                executor.submit(transcribe_single_file, i, file_path): i 
                for i, file_path in enumerate(slice_files)
            }
            for future in as_completed(futures):
                future.result()
        
        if errors:
            logger.error(f"[{task_id}] 转录错误汇总:")
            for err in errors:
                logger.error(f"[{task_id}]   - {err}")
        
        valid_results = [r for r in results if r is not None]
        if not valid_results:
            error_detail = f'所有文件转录均失败。错误: {"; ".join(errors)}'
            logger.error(f"[{task_id}] {error_detail}")
            raise Exception(error_detail)
        
        full_text = ''.join([
            r if r is not None else f'\n[分段 {i:04d} 转录失败]\n' 
            for i, r in enumerate(results)
        ])
        
        output_file = os.path.join(user_dir, 'output', f'{base_name}.txt')
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(full_text)
        
        task['txt_file'] = output_file
        task['transcribe_completed_at'] = datetime.now().isoformat()
        save_task_to_db(task)
        
        logger.info(f"[{task_id}] 转录完成")
        
        task['current_step'] = 'GPT总结'
        task['progress'] = 70
        task['summary_started_at'] = datetime.now().isoformat()
        save_task_to_db(task)
        logger.info(f"[{task_id}] 步骤5: GPT总结")
        
        prompt_name = task.get('prompt_name', '会议总结')
        system_prompt = load_prompt(prompt_name)
        if not system_prompt:
            logger.warning(f"[{task_id}] Prompt '{prompt_name}' 不存在，使用默认prompt")
            system_prompt = load_prompt('会议总结')
            if not system_prompt:
                system_prompt = "请提炼和总结以下内容。"
        
        logger.info(f"[{task_id}] 使用Prompt: {prompt_name}")
        result = call_gpt_api(full_text, config, system_prompt)
        
        if 'choices' not in result or len(result['choices']) == 0:
            raise Exception(f"GPT API返回格式错误")
        
        summary = result['choices'][0]['message']['content']
        
        json_file = os.path.join(user_dir, 'gpt', 'summary', f'{base_name}.json')
        output_data = {
            'filename': filename,
            'prompt_name': prompt_name,
            'summary': summary,
            'full_response': result,
            'timestamp': datetime.now().isoformat()
        }
        
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
        
        md = markdown.Markdown(extensions=['tables', 'fenced_code', 'nl2br'])
        summary_html = md.convert(summary)
        
        html_file = os.path.join(user_dir, 'gpt', 'summary', f'{base_name}.html')
        html_content = generate_html_content(filename, summary_html, prompt_name)
        
        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        task['json_file'] = json_file
        task['html_file'] = html_file
        task['summary_completed_at'] = datetime.now().isoformat()
        save_task_to_db(task)
        
        logger.info(f"[{task_id}] 总结完成")
        
        task['current_step'] = '生成PDF'
        task['progress'] = 90
        task['pdf_started_at'] = datetime.now().isoformat()
        save_task_to_db(task)
        logger.info(f"[{task_id}] 步骤6: 生成PDF")
        
        pdf_file = os.path.join(user_dir, 'gpt', 'summary', f'{base_name}.pdf')
        
        cmd = [
            'chromium',
            '--headless',
            '--disable-gpu',
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--disable-software-rasterizer',
            '--disable-extensions',
            '--font-render-hinting=none',
            '--disable-font-subpixel-positioning',
            f'--print-to-pdf={pdf_file}',
            '--no-pdf-header-footer',
            f'file://{os.path.abspath(html_file)}'
        ]
        
        subprocess.run(cmd, check=True, capture_output=True, timeout=30)
        
        task['pdf_completed_at'] = datetime.now().isoformat()
        save_task_to_db(task)
        
        logger.info(f"[{task_id}] PDF生成完成")
        
        task['status'] = 'completed'
        task['current_step'] = '全部完成'
        task['progress'] = 100
        task['pdf_file'] = pdf_file
        task['completed_at'] = datetime.now().isoformat()
        
        save_task_to_db(task)
        
        logger.info(f"[{task_id}] 任务全部完成")
        
    except Exception as e:
        logger.error(f"[{task_id}] 任务失败: {str(e)}", exc_info=True)
        task['status'] = 'failed'
        task['error'] = str(e)
        task['failed_at'] = datetime.now().isoformat()
        save_task_to_db(task)

def call_whisper_api(file_path, config, retry_count=0):
    file_name = os.path.basename(file_path)
    logger.info(f"  调用Whisper API: {file_name} (尝试 {retry_count + 1}/{MAX_RETRIES + 1})")
    
    try:
        if not config.get('whisper', {}).get('api_key'):
            raise Exception("Whisper API Key未配置")
        
        if not config.get('whisper', {}).get('base_url'):
            raise Exception("Whisper Base URL未配置")
        
        url_parts = urlparse(config['whisper']['base_url'])
        logger.info(f"  连接到: {url_parts.netloc}")
        
        context = ssl._create_unverified_context()
        conn = http.client.HTTPSConnection(url_parts.netloc, timeout=60, context=context)
        
        dataList = []
        boundary = 'wL36Yn8afVp8Ag7AmP8qZ0SA4n1v9T'
        
        dataList.append(encode('--' + boundary))
        dataList.append(encode(f'Content-Disposition: form-data; name="file"; filename="{file_name}"'))
        
        fileType = mimetypes.guess_type(file_path)[0] or 'application/octet-stream'
        dataList.append(encode(f'Content-Type: {fileType}'))
        dataList.append(encode(''))
        
        with open(file_path, 'rb') as f:
            file_data = f.read()
        
        if len(file_data) == 0:
            raise Exception(f"文件 {file_name} 为空")
        
        logger.info(f"  文件大小: {len(file_data) / 1024:.2f} KB")
        dataList.append(file_data)
        
        dataList.append(encode('--' + boundary))
        dataList.append(encode('Content-Disposition: form-data; name="model"'))
        dataList.append(encode('Content-Type: text/plain'))
        dataList.append(encode(''))
        dataList.append(encode(config['whisper']['selected_model']))
        
        dataList.append(encode('--' + boundary))
        dataList.append(encode('Content-Disposition: form-data; name="temperature"'))
        dataList.append(encode('Content-Type: text/plain'))
        dataList.append(encode(''))
        dataList.append(encode(str(config['whisper'].get('temperature', 0.2))))
        
        dataList.append(encode('--' + boundary + '--'))
        dataList.append(encode(''))
        
        body = b'\r\n'.join(dataList)
        
        headers = {
            'Content-type': f'multipart/form-data; boundary={boundary}',
            'Authorization': f'Bearer {config["whisper"]["api_key"]}'
        }
        
        conn.request("POST", "/v1/audio/transcriptions", body, headers)
        res = conn.getresponse()
        
        status_code = res.status
        data = res.read()
        conn.close()
        
        if status_code != 200:
            logger.error(f"  HTTP错误 {status_code}: {data.decode('utf-8', errors='ignore')}")
            return {
                'error': {
                    'message': f'HTTP {status_code}: {data.decode("utf-8", errors="ignore")}'
                }
            }
        
        try:
            result = json.loads(data.decode("utf-8"))
        except json.JSONDecodeError as e:
            logger.error(f"  JSON解析失败: {str(e)}")
            return {
                'error': {
                    'message': f'JSON解析失败: {str(e)}'
                }
            }
        
        if 'error' in result:
            error_msg = result['error'].get('message', str(result['error']))
            
            if 'too short' in error_msg.lower():
                return result
            
            if retry_count < MAX_RETRIES and ('parsing' in error_msg.lower() or 'eof' in error_msg.lower()):
                time.sleep(2 * (retry_count + 1))
                return call_whisper_api(file_path, config, retry_count + 1)
            
            return result
        
        return result
        
    except Exception as e:
        logger.error(f"  ✗ 异常: {file_name} - {str(e)}", exc_info=True)
        
        if retry_count < MAX_RETRIES:
            time.sleep(2 * (retry_count + 1))
            return call_whisper_api(file_path, config, retry_count + 1)
        else:
            return {
                'error': {
                    'message': f'请求异常: {str(e)}'
                }
            }

def call_gpt_api(text, config, system_prompt):
    url_parts = urlparse(config['gpt']['base_url'])
    context = ssl._create_unverified_context()
    conn = http.client.HTTPSConnection(url_parts.netloc, context=context)
    
    payload = json.dumps({
        "model": config['gpt']['selected_model'],
        "messages": [
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": f"请提炼和总结以下内容：\n\n{text}"
            }
        ],
        "temperature": 0.3
    })
    
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {config["gpt"]["api_key"]}'
    }
    
    conn.request("POST", "/v1/chat/completions", payload, headers)
    res = conn.getresponse()
    data = res.read()
    conn.close()
    
    return json.loads(data.decode("utf-8"))

def generate_html_content(filename, summary_html, prompt_name='会议总结'):
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{filename} - AI总结</title>
    <style>
        @page {{ size: A4; margin: 10mm; }}
        @media print {{ html, body {{ margin: 0; padding: 0; }} }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Microsoft YaHei', 'SimSun', Arial, sans-serif; line-height: 1.8; color: #333; background: #fff; padding: 20px; }}
        .container {{ max-width: 900px; margin: 0 auto; }}
        h1 {{ color: #2c3e50; font-size: 28px; margin-bottom: 20px; padding-bottom: 10px; border-bottom: 2px solid #3498db; text-align: center; }}
        .meta {{ color: #7f8c8d; font-size: 13px; margin-bottom: 30px; padding: 12px; background: #f8f9fa; border-left: 4px solid #3498db; }}
        .content {{ font-size: 15px; }}
        .content h1 {{ color: #2c3e50; font-size: 24px; margin-top: 30px; margin-bottom: 15px; border-bottom: 2px solid #3498db; padding-bottom: 8px; }}
        .content h2 {{ color: #34495e; font-size: 20px; margin-top: 25px; margin-bottom: 12px; border-bottom: 1px solid #e0e0e0; padding-bottom: 6px; }}
        .content h3 {{ color: #34495e; font-size: 18px; margin-top: 20px; margin-bottom: 10px; }}
        .content p {{ margin-bottom: 12px; text-align: justify; }}
        .content ul, .content ol {{ margin-left: 25px; margin-bottom: 15px; }}
        .content li {{ margin-bottom: 8px; }}
        .content strong {{ color: #2c3e50; font-weight: bold; }}
        .content table {{ width: 100%; border-collapse: collapse; margin: 20px 0; font-size: 14px; }}
        .content table th {{ background: #3498db; color: white; padding: 12px; text-align: left; font-weight: bold; }}
        .content table td {{ padding: 10px 12px; border: 1px solid #ddd; }}
        .content table tr:nth-child(even) {{ background: #f8f9fa; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>{filename} - AI总结</h1>
        <div class="meta">
            <strong>生成时间：</strong>{datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')}<br>
            <strong>使用模板：</strong>{prompt_name}
        </div>
        <div class="content">{summary_html}</div>
    </div>
</body>
</html>"""

@app.route('/api/tasks', methods=['GET'])
@login_required
def get_tasks():
    username = session['username']
    tasks = get_user_tasks_from_db(username)
    return jsonify(tasks)

@app.route('/api/task/<task_id>', methods=['GET'])
@login_required
def get_task(task_id):
    task = get_task_from_db(task_id)
    
    if not task:
        return jsonify({'error': '任务不存在'}), 404
    
    if task['username'] != session['username']:
        return jsonify({'error': '无权访问'}), 403
    
    return jsonify(task)

@app.route('/api/retry/<task_id>', methods=['POST'])
@login_required
def retry_task(task_id):
    username = session['username']
    logger.info(f"用户 {username} 请求重试任务: {task_id}")
    
    task = get_task_from_db(task_id)
    
    if not task:
        return jsonify({'error': '任务不存在'}), 404
    
    if task['username'] != username:
        return jsonify({'error': '无权访问'}), 403
    
    if task['status'] != 'failed':
        return jsonify({'error': '只能重试失败的任务'}), 400
    
    task['status'] = 'processing'
    task['current_step'] = '准备重试'
    task['progress'] = 0
    task['error'] = None
    task['failed_at'] = None
    
    save_task_to_db(task)
    
    thread = threading.Thread(target=process_task, args=(task_id,))
    thread.daemon = True
    thread.start()
    logger.info(f"重试任务已启动: {task_id}")
    
    return jsonify({'message': '任务重试已启动', 'task_id': task_id})

@app.route('/api/download/<task_id>/<file_type>', methods=['GET'])
@login_required
def download_file(task_id, file_type):
    username = session['username']
    task = get_task_from_db(task_id)
    
    if not task:
        return jsonify({'error': '任务不存在'}), 404
    
    if task['username'] != username:
        return jsonify({'error': '无权访问此任务'}), 403
    
    file_map = {
        'txt': 'txt_file',
        'json': 'json_file',
        'html': 'html_file',
        'pdf': 'pdf_file'
    }
    
    if file_type not in file_map:
        return jsonify({'error': f'不支持的文件类型: {file_type}'}), 400
    
    file_key = file_map[file_type]
    file_path = task.get(file_key)
    
    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': f'{file_type.upper()}文件不存在'}), 404
    
    try:
        return send_file(
            file_path, 
            as_attachment=True, 
            download_name=os.path.basename(file_path),
            mimetype='application/octet-stream'
        )
    except Exception as e:
        logger.error(f"发送文件失败: {str(e)}", exc_info=True)
        return jsonify({'error': f'下载失败: {str(e)}'}), 500

@app.route('/api/config', methods=['GET'])
@login_required
def get_config():
    config = load_config()
    safe_config = {
        'whisper': {
            'base_url': config['whisper']['base_url'],
            'api_key': '***hidden***',
            'models': config['whisper']['models'],
            'selected_model': config['whisper']['selected_model'],
            'temperature': config['whisper'].get('temperature', 0.2)
        },
        'gpt': {
            'base_url': config['gpt']['base_url'],
            'api_key': '***hidden***',
            'models': config['gpt']['models'],
            'selected_model': config['gpt']['selected_model']
        }
    }
    return jsonify(safe_config)

@app.route('/api/config', methods=['POST'])
@login_required
def update_config():
    try:
        new_config = request.json
        current_config = load_config()
        
        if new_config['whisper']['api_key'] == '***hidden***':
            new_config['whisper']['api_key'] = current_config['whisper']['api_key']
        
        if new_config['gpt']['api_key'] == '***hidden***':
            new_config['gpt']['api_key'] = current_config['gpt']['api_key']
        
        save_config(new_config)
        logger.info("配置已更新")
        return jsonify({'message': '配置已更新'})
    except Exception as e:
        logger.error(f"更新配置失败: {str(e)}")
        return jsonify({'error': str(e)}), 500

def initialize():
    try:
        load_db_config()
        init_default_prompt()
        logger.info("应用初始化完成")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM tasks")
        result = cursor.fetchone()
        logger.info(f"数据库连接成功，当前任务数: {result['count']}")
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"应用初始化失败: {str(e)}", exc_info=True)
        raise

initialize()

if __name__ == '__main__':
    logger.info("服务器启动")
    app.run(host='0.0.0.0', port=5000, debug=False)
