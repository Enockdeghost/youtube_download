import os
import tempfile
import uuid
import threading
import sqlite3
import random
from flask import Flask, render_template, request, jsonify, send_file, after_this_request, g, session
import yt_dlp

app = Flask(__name__)
app.config['SECRET_KEY'] = 'CVHJ56345Q@$#%Tewrtxf' 
app.config['DATABASE'] = 'downloads.db'

# --- Database setup ---
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(app.config['DATABASE'])
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_db(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.execute('''
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                format_id TEXT NOT NULL,
                format_note TEXT,
                container TEXT DEFAULT 'mp4',
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor = db.execute("PRAGMA table_info(downloads)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'container' not in columns:
            db.execute("ALTER TABLE downloads ADD COLUMN container TEXT DEFAULT 'mp4'")
        db.commit()

init_db()

# --- In-memory download task storage ---
download_tasks = {}  # task_id -> {'progress': int, 'status': str, 'file_path': str, 'error': str}

# --- Captcha helpers (simple math) ---
def generate_captcha():
    a = random.randint(1, 10)
    b = random.randint(1, 10)
    session['captcha_answer'] = a + b
    session['captcha_question'] = f"{a} + {b} = ?"
    return session['captcha_question']

def verify_captcha(answer):
    try:
        return int(answer) == session.get('captcha_answer')
    except:
        return False

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

def background_download(url, format_id, custom_filename, container, start_time, end_time, audio_only, audio_bitrate, subtitle_langs, thumbnail, task_id):
    temp_dir = tempfile.mkdtemp()
    outtmpl = os.path.join(temp_dir, '%(title)s.%(ext)s')

    ydl_opts = {
        'format': format_id,
        'outtmpl': outtmpl,
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [progress_hook(task_id)],
    }

    # Container for merged formats
    if container and '+' in format_id:
        ydl_opts['merge_output_format'] = container

    # Video trimming
    if start_time or end_time:
        # yt-dlp expects --download-sections "*start-end"
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
        # Force audio-only format if not already
        if format_id not in ['bestaudio', 'bestaudio/best']:
            # Override format to bestaudio if user wants audio only
            ydl_opts['format'] = 'bestaudio/best'

    # Subtitles
    if subtitle_langs:
        ydl_opts['writesubtitles'] = True
        ydl_opts['subtitleslangs'] = subtitle_langs.split(',')
        ydl_opts['subtitlesformat'] = 'vtt/srt'  # prefer vtt, fallback srt

    # Thumbnail
    if thumbnail:
        ydl_opts['writethumbnail'] = True

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            downloaded_files = os.listdir(temp_dir)
            if not downloaded_files:
                raise Exception("No file downloaded")
            
            # But better to return all? We'll zip them if multiple.
            if len(downloaded_files) > 1 and not custom_filename:
                # Create a zip with all files
                import zipfile
                zip_path = os.path.join(temp_dir, "download.zip")
                with zipfile.ZipFile(zip_path, 'w') as zipf:
                    for f in downloaded_files:
                        zipf.write(os.path.join(temp_dir, f), arcname=f)
                file_path = zip_path
            else:
                # Find the main file (largest)
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

            # Save to history (optional)
            with app.app_context():
                title = info.get('title', 'Unknown')
                format_note = info.get('format_note', '')
                db = get_db()
                db.execute(
                    'INSERT INTO downloads (url, title, format_id, format_note, container) VALUES (?, ?, ?, ?, ?)',
                    (url, title, format_id, format_note, container)
                )
                db.commit()
    except Exception as e:
        download_tasks[task_id]['status'] = 'error'
        download_tasks[task_id]['error'] = str(e)

def get_video_info(url):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info:
                # Playlist
                videos = []
                for entry in info['entries']:
                    if entry:
                        videos.append({
                            'url': entry['webpage_url'],
                            'title': entry.get('title', 'Unknown'),
                            'thumbnail': entry.get('thumbnail', ''),
                            'duration': entry.get('duration', 0),
                        })
                return {'type': 'playlist', 'title': info.get('title', 'Playlist'), 'videos': videos}
            else:
                # Single video: collect formats
                formats = []
                for f in info['formats']:
                    if f.get('filesize') is not None or f.get('filesize_approx') is not None:
                        format_item = {
                            'format_id': f['format_id'],
                            'ext': f.get('ext', 'N/A'),
                            'quality': f.get('format_note') or f.get('resolution') or 'N/A',
                            'filesize': f.get('filesize') or f.get('filesize_approx') or 0,
                            'vcodec': f.get('vcodec', 'none'),
                            'acodec': f.get('acodec', 'none'),
                            'fps': f.get('fps'),
                            'audio_channels': f.get('audio_channels'),
                            'tbr': f.get('tbr'),
                            'format_note': f.get('format_note', ''),
                        }
                        formats.append(format_item)

                formats.sort(key=lambda x: x['filesize'], reverse=True)

                # Add best video+audio virtual format
                has_video = any(f['vcodec'] != 'none' for f in formats)
                has_audio = any(f['acodec'] != 'none' for f in formats)
                if has_video and has_audio:
                    formats.append({
                        'format_id': 'bestvideo+bestaudio',
                        'ext': 'mp4',
                        'quality': 'Best Video + Best Audio (merged)',
                        'filesize': 0,
                        'vcodec': 'multiple',
                        'acodec': 'multiple',
                        'fps': None,
                        'audio_channels': None,
                        'tbr': None,
                        'format_note': 'Merged format',
                    })

                return {
                    'type': 'video',
                    'title': info.get('title', 'Unknown'),
                    'thumbnail': info.get('thumbnail', ''),
                    'duration': info.get('duration', 0),
                    'formats': formats,
                    'webpage_url': info.get('webpage_url', url)
                }
        except Exception as e:
            return {'error': str(e)}

# --- Routes ---
@app.route('/')
def index():
    if 'captcha_answer' not in session:
        generate_captcha()
    return render_template('index.html', captcha_question=session.get('captcha_question', ''))

@app.route('/get_info', methods=['POST'])
def get_info():
    url = request.json.get('url')
    if not url:
        return jsonify({'error': 'URL is required'}), 400
    info = get_video_info(url)
    if 'error' in info:
        return jsonify({'error': info['error']}), 400
    return jsonify(info)

@app.route('/start_download', methods=['POST'])
def start_download():
    # Simple CAPTCHA check
    download_count = session.get('download_count', 0)
    if download_count >= 3:
        captcha = request.json.get('captcha')
        if not captcha or not verify_captcha(captcha):
            return jsonify({'error': 'CAPTCHA required or invalid', 'need_captcha': True}), 403
        generate_captcha()

    data = request.get_json()
    url = data.get('url')
    format_id = data.get('format_id')
    custom_filename = data.get('custom_filename', '').strip()
    container = data.get('container', 'mp4')
    start_time = data.get('start_time', '')
    end_time = data.get('end_time', '')
    audio_only = data.get('audio_only', False)
    audio_bitrate = data.get('audio_bitrate', '192k')
    subtitle_langs = data.get('subtitle_langs', '')
    thumbnail = data.get('thumbnail', False)

    if not url or not format_id:
        return jsonify({'error': 'Missing parameters'}), 400

    task_id = str(uuid.uuid4())
    download_tasks[task_id] = {'progress': 0, 'status': 'starting', 'file_path': None, 'error': None}

    thread = threading.Thread(target=background_download, args=(
        url, format_id, custom_filename, container, start_time, end_time,
        audio_only, audio_bitrate, subtitle_langs, thumbnail, task_id
    ))
    thread.daemon = True
    thread.start()

    session['download_count'] = download_count + 1
    return jsonify({'task_id': task_id})

@app.route('/progress/<task_id>')
def progress(task_id):
    task = download_tasks.get(task_id)
    if not task:
        return jsonify({'error': 'Invalid task ID'}), 404
    return jsonify({
        'progress': task['progress'],
        'status': task['status'],
        'error': task.get('error')
    })

@app.route('/download_file/<task_id>')
def download_file(task_id):
    task = download_tasks.get(task_id)
    if not task or task['status'] != 'done' or not task['file_path']:
        return "File not ready or invalid", 404

    file_path = task['file_path']

    @after_this_request
    def cleanup(response):
        try:
            if os.path.isdir(os.path.dirname(file_path)):
                # Delete individual file and maybe the directory
                os.remove(file_path)
                
        except Exception as e:
            app.logger.error(f"Cleanup error: {e}")
        return response

    return send_file(file_path, as_attachment=True)

@app.route('/history')
def history():
    db = get_db()
    downloads = db.execute('SELECT * FROM downloads ORDER BY timestamp DESC LIMIT 50').fetchall()
    return jsonify([dict(row) for row in downloads])

@app.route('/captcha')
def get_captcha():
    question = generate_captcha()
    return jsonify({'question': question})

if __name__ == '__main__':
    app.run(debug=True)
