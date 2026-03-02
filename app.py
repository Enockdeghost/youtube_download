import os
import tempfile
import uuid
import threading
import sqlite3
from flask import Flask, render_template, request, jsonify, send_file, after_this_request, g
import yt_dlp

app = Flask(__name__)
app.config['SECRET_KEY'] = 'WhaEBER$T$##BQBT$%B#%B!#H#$G#$H%G#4bnbvcHVCWELFVU4FG348GF899FG4FG4FG9QFG9EFG93FBFB947R7Y49'  
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
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        db.commit()

init_db()

# --- In-memory download task storage ---
download_tasks = {}  # task_id -> {'progress': int, 'status': str, 'file_path': str, 'error': str}

# --- Helper functions ---
def sanitize_filename(name):
    # Remove invalid characters
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

def background_download(url, format_id, custom_filename, task_id):
    temp_dir = tempfile.mkdtemp()
    outtmpl = os.path.join(temp_dir, '%(title)s.%(ext)s')

    ydl_opts = {
        'format': format_id,
        'outtmpl': outtmpl,
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [progress_hook(task_id)],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            downloaded_files = os.listdir(temp_dir)
            if not downloaded_files:
                raise Exception("No file downloaded")
            original_path = os.path.join(temp_dir, downloaded_files[0])

            if custom_filename:
                ext = os.path.splitext(original_path)[1]
                new_path = os.path.join(temp_dir, sanitize_filename(custom_filename) + ext)
                os.rename(original_path, new_path)
                file_path = new_path
            else:
                file_path = original_path

            download_tasks[task_id]['file_path'] = file_path
            download_tasks[task_id]['status'] = 'done'

            # Save to history - need application context
            with app.app_context():
                title = info.get('title', 'Unknown')
                format_note = info.get('format_note', '')
                db = get_db()
                db.execute(
                    'INSERT INTO downloads (url, title, format_id, format_note) VALUES (?, ?, ?, ?)',
                    (url, title, format_id, format_note)
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
            # Check if it's a playlist
            if 'entries' in info:
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
                # Single video
                formats = []
                for f in info['formats']:
                    if f.get('filesize') is not None or f.get('filesize_approx') is not None:
                        format_note = f.get('format_note', '')
                        resolution = f.get('resolution', 'N/A')
                        ext = f.get('ext', 'N/A')
                        filesize = f.get('filesize') or f.get('filesize_approx') or 0
                        quality = format_note if format_note else resolution
                        formats.append({
                            'format_id': f['format_id'],
                            'ext': ext,
                            'quality': quality,
                            'filesize': filesize,
                            'vcodec': f.get('vcodec', 'none'),
                            'acodec': f.get('acodec', 'none'),
                        })
                formats.sort(key=lambda x: x['filesize'], reverse=True)
                return {
                    'type': 'video',
                    'title': info.get('title', 'Unknown'),
                    'thumbnail': info.get('thumbnail', ''),
                    'duration': info.get('duration', 0),
                    'formats': formats
                }
        except Exception as e:
            return {'error': str(e)}

# --- Routes ---
@app.route('/')
def index():
    return render_template('index.html')

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
    data = request.get_json()
    url = data.get('url')
    format_id = data.get('format_id')
    custom_filename = data.get('custom_filename', '').strip()
    if not url or not format_id:
        return jsonify({'error': 'Missing parameters'}), 400

    task_id = str(uuid.uuid4())
    download_tasks[task_id] = {'progress': 0, 'status': 'starting', 'file_path': None, 'error': None}

    # Start background thread
    thread = threading.Thread(target=background_download, args=(url, format_id, custom_filename, task_id))
    thread.daemon = True
    thread.start()

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
            os.remove(file_path)
            os.rmdir(os.path.dirname(file_path))
            del download_tasks[task_id]
        except Exception as e:
            app.logger.error(f"Cleanup error: {e}")
        return response

    return send_file(file_path, as_attachment=True)

@app.route('/history')
def history():
    db = get_db()
    downloads = db.execute('SELECT * FROM downloads ORDER BY timestamp DESC LIMIT 50').fetchall()
    return jsonify([dict(row) for row in downloads])

if __name__ == '__main__':
    app.run(debug=True)