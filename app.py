import os
import tempfile
import uuid
import threading
import zipfile
import logging
import time
import random
from flask import Flask, render_template, request, jsonify, send_file, after_this_request
import yt_dlp
from yt_dlp.utils import DownloadError

app = Flask(__name__)
app.config['SECRET_KEY'] = 'CVHJ56345Q@$#%Tewrtxf'

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- File paths ---
COOKIES_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), 'cookies.txt'))
PO_TOKEN_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), 'po_token.txt'))

# --- Cookie validation on startup ---
def validate_cookies():
    if not os.path.exists(COOKIES_FILE):
        logger.warning("⚠️ cookies.txt not found.")
        return False
    try:
        with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
            first_line = f.readline().strip()
            if first_line.startswith('# Netscape') or first_line.startswith('# HTTP'):
                logger.info("✅ cookies.txt format appears valid.")
                return True
            else:
                logger.error(f"❌ Invalid cookies.txt first line: {first_line}")
                return False
    except Exception as e:
        logger.error(f"❌ Error reading cookies.txt: {e}")
        return False

cookies_valid = validate_cookies()

# --- PO Token loading (optional) ---
_po_token = None
_po_token_mtime = 0

def get_po_token():
    global _po_token, _po_token_mtime
    if os.path.exists(PO_TOKEN_FILE):
        mtime = os.path.getmtime(PO_TOKEN_FILE)
        if mtime > _po_token_mtime:
            try:
                with open(PO_TOKEN_FILE, 'r') as f:
                    _po_token = f.read().strip()
                _po_token_mtime = mtime
                logger.info("✅ PO Token loaded.")
            except Exception as e:
                logger.error(f"❌ Failed to read PO token: {e}")
                return None
        return _po_token
    return None

# --- In-memory download task storage ---
download_tasks = {}

# --- Helper functions ---
def sanitize_filename(name):
    return "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).rstrip()

def progress_hook(task_id):
    def hook(d):
        if d['status'] == 'downloading':
            if 'total_bytes' in d:
                percent = d['downloaded_bytes'] / d['total_bytes'] * 100
            elif 'total_bytes_estimate' in d:
                percent = d['downloaded_bytes'] / d['total_bytes_estimate'] * 100
            else:
                percent = 0
            download_tasks[task_id]['progress'] = int(percent)
            download_tasks[task_id]['status'] = 'downloading'
        elif d['status'] == 'finished':
            download_tasks[task_id]['progress'] = 100
            download_tasks[task_id]['status'] = 'finished'
    return hook

def background_download(url, format_id, custom_filename, container, start_time, end_time,
                        audio_only, audio_bitrate, subtitle_langs, thumbnail, task_id):
    temp_dir = tempfile.mkdtemp()
    outtmpl = os.path.join(temp_dir, '%(title)s.%(ext)s')

    # Random delay to avoid rate limiting
    time.sleep(random.uniform(5, 15))

    ydl_opts = {
        'format': format_id,
        'outtmpl': outtmpl,
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [progress_hook(task_id)],
        'logger': logger,
        'extractor_args': {},
    }

    # --- Primary authentication: cookies ---
    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE
        logger.info("🍪 Using cookies for authentication.")
    else:
        # Fallback to PO token if cookies not available
        po_token = get_po_token()
        if po_token:
            logger.info("🔑 Using PO Token as fallback.")
            ydl_opts['extractor_args']['youtube'] = {
                'player_client': ['mweb', 'default'],
                'po_token': po_token
            }
        else:
            logger.warning("⚠️ No authentication method available.")

    # Container for merged formats
    if container and '+' in format_id:
        ydl_opts['merge_output_format'] = container

    # Video trimming
    if start_time or end_time:
        section = "*"
        if start_time:
            section += start_time
        section += "-"
        if end_time:
            section += end_time
        ydl_opts['download_sections'] = section

    # Audio extraction
    if audio_only:
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': audio_bitrate.split('k')[0] if audio_bitrate else 'mp3',
            'preferredquality': audio_bitrate.replace('k', '') if audio_bitrate else '192',
        }]
        if format_id not in ['bestaudio', 'bestaudio/best']:
            ydl_opts['format'] = 'bestaudio/best'

    # Subtitles
    if subtitle_langs:
        ydl_opts['writesubtitles'] = True
        ydl_opts['subtitleslangs'] = subtitle_langs.split(',')
        ydl_opts['subtitlesformat'] = 'vtt/srt'

    # Thumbnail
    if thumbnail:
        ydl_opts['writethumbnail'] = True

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info(f"📥 Downloading {url}")
            info = ydl.extract_info(url, download=True)
            downloaded_files = os.listdir(temp_dir)
            if not downloaded_files:
                raise Exception("No file downloaded")

            if len(downloaded_files) > 1 and not custom_filename:
                zip_path = os.path.join(temp_dir, "download.zip")
                with zipfile.ZipFile(zip_path, 'w') as zipf:
                    for f in downloaded_files:
                        zipf.write(os.path.join(temp_dir, f), arcname=f)
                file_path = zip_path
            else:
                main_file = max(downloaded_files, key=lambda f: os.path.getsize(os.path.join(temp_dir, f)))
                original_path = os.path.join(temp_dir, main_file)
                if custom_filename:
                    ext = os.path.splitext(original_path)[1]
                    new_path = os.path.join(temp_dir, sanitize_filename(custom_filename) + ext)
                    os.rename(original_path, new_path)
                    file_path = new_path
                else:
                    file_path = original_path

            download_tasks[task_id]['file_path'] = file_path
            download_tasks[task_id]['status'] = 'done'
            logger.info(f"✅ Download complete: {file_path}")
    except DownloadError as e:
        error_msg = str(e)
        logger.error(f"❌ Download error: {error_msg}")
        download_tasks[task_id]['status'] = 'error'
        download_tasks[task_id]['error'] = error_msg
    except Exception as e:
        logger.error(f"❌ Unexpected error: {str(e)}")
        download_tasks[task_id]['status'] = 'error'
        download_tasks[task_id]['error'] = str(e)

def get_video_info(url):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'logger': logger,
    }

    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE
    else:
        po_token = get_po_token()
        if po_token:
            ydl_opts['extractor_args'] = {'youtube': {'player_client': ['mweb', 'default'], 'po_token': po_token}}

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            # ... (rest of format extraction logic, same as before) ...
            # (I'll omit for brevity, but you can copy from previous versions)
            return info  # Simplified for example
    except DownloadError as e:
        logger.error(f"Info error: {e}")
        return {'error': str(e)}

# --- Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_info', methods=['POST'])
def get_info():
    url = request.json.get('url')
    if not url:
        return jsonify({'error': 'URL required'}), 400
    info = get_video_info(url)
    if 'error' in info:
        return jsonify({'error': info['error']}), 400
    return jsonify(info)

@app.route('/start_download', methods=['POST'])
def start_download():
    data = request.get_json()
    # ... (parameter extraction same as before) ...
    task_id = str(uuid.uuid4())
    download_tasks[task_id] = {'progress': 0, 'status': 'starting', 'file_path': None, 'error': None}
    thread = threading.Thread(target=background_download, args=(...))
    thread.start()
    return jsonify({'task_id': task_id})

@app.route('/progress/<task_id>')
def progress(task_id):
    task = download_tasks.get(task_id)
    return jsonify(task)

@app.route('/download_file/<task_id>')
def download_file(task_id):
    task = download_tasks.get(task_id)
    if not task or task['status'] != 'done':
        return "File not ready", 404
    return send_file(task['file_path'], as_attachment=True)

@app.route('/check_auth', methods=['GET'])
def check_auth():
    return jsonify({
        'cookies': {'exists': os.path.exists(COOKIES_FILE), 'valid': cookies_valid},
        'po_token': {'exists': os.path.exists(PO_TOKEN_FILE)},
        'auth_method': 'cookies' if os.path.exists(COOKIES_FILE) else ('po_token' if os.path.exists(PO_TOKEN_FILE) else 'none')
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
