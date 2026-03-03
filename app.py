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
PO_TOKEN_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), 'po_token.txt'))
COOKIES_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), 'cookies.txt'))

# --- PO Token caching with auto-reload ---
_po_token = None
_po_token_mtime = 0

def get_po_token():
    """Read PO token from file, reload if file modified."""
    global _po_token, _po_token_mtime
    if os.path.exists(PO_TOKEN_FILE):
        mtime = os.path.getmtime(PO_TOKEN_FILE)
        if mtime > _po_token_mtime:
            try:
                with open(PO_TOKEN_FILE, 'r') as f:
                    _po_token = f.read().strip()
                _po_token_mtime = mtime
                logger.info("✅ PO Token loaded/refreshed successfully.")
            except Exception as e:
                logger.error(f"❌ Failed to read PO token: {e}")
                return None
        return _po_token
    logger.warning("⚠️ PO token file not found.")
    return None

# --- In-memory download task storage ---
download_tasks = {}  # task_id -> {'progress': int, 'status': str, 'file_path': str, 'error': str}

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

    # Add a random delay to avoid rate limiting (5-15 seconds)
    delay = random.uniform(5, 15)
    logger.info(f"⏱️ Waiting {delay:.1f} seconds before download...")
    time.sleep(delay)

    # Base options
    ydl_opts = {
        'format': format_id,
        'outtmpl': outtmpl,
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [progress_hook(task_id)],
        'logger': logger,
        'extractor_args': {},
    }

    # --- Try PO Token Authentication First ---
    po_token = get_po_token()
    if po_token:
        logger.info("🔑 Attempting authentication with PO Token...")
        ydl_opts['extractor_args']['youtube'] = {
            'player_client': ['mweb', 'default'],
            'po_token': po_token
        }
    else:
        # Fallback to cookies
        if os.path.exists(COOKIES_FILE):
            logger.info("🍪 Falling back to cookies file.")
            ydl_opts['cookiefile'] = COOKIES_FILE
        else:
            logger.warning("⚠️ No authentication method available. YouTube will likely block the request.")

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

    # --- Attempt Download with Retry Logic ---
    max_retries = 2
    for attempt in range(max_retries):
        try:
            logger.info(f"📥 Download attempt {attempt + 1}/{max_retries} for {url}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                downloaded_files = os.listdir(temp_dir)
                if not downloaded_files:
                    raise Exception("No file downloaded")

                # If multiple files, create a zip
                if len(downloaded_files) > 1 and not custom_filename:
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
                logger.info(f"✅ Download complete: {file_path}")
                return  # Success, exit function

        except DownloadError as e:
            error_msg = str(e)
            logger.error(f"❌ Download attempt {attempt + 1} failed: {error_msg}")

            # If this is the first attempt and we used PO token, try without it
            if attempt == 0 and po_token and 'Sign in to confirm' in error_msg:
                logger.warning("🔄 PO Token rejected. Retrying without token...")
                # Remove token and try with default clients
                if 'extractor_args' in ydl_opts:
                    ydl_opts['extractor_args'].pop('youtube', None)
                # Also try cookies if available
                if os.path.exists(COOKIES_FILE):
                    ydl_opts['cookiefile'] = COOKIES_FILE
            else:
                # No more retries, mark as error
                download_tasks[task_id]['status'] = 'error'
                download_tasks[task_id]['error'] = error_msg
                return

        except Exception as e:
            logger.error(f"❌ Unexpected error: {str(e)}")
            download_tasks[task_id]['status'] = 'error'
            download_tasks[task_id]['error'] = str(e)
            return

    # If we exhausted retries
    download_tasks[task_id]['status'] = 'error'
    download_tasks[task_id]['error'] = "All download attempts failed."

def get_video_info(url):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'logger': logger,
        'extractor_args': {},
    }

    po_token = get_po_token()
    if po_token:
        ydl_opts['extractor_args']['youtube'] = {
            'player_client': ['mweb', 'default'],
            'po_token': po_token
        }
    elif os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info(f"🔍 Fetching info for {url}")
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
    except DownloadError as e:
        logger.error(f"❌ Info extraction error: {str(e)}")
        # If token error, try once without token
        if 'Sign in to confirm' in str(e) and po_token:
            logger.warning("🔄 PO Token rejected for info fetch, retrying without...")
            ydl_opts.pop('extractor_args', None)
            ydl_opts.pop('cookiefile', None)
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    return {'type': 'video', 'title': info.get('title', 'Unknown'), 'formats': []}
            except Exception as e2:
                return {'error': f"YouTube blocked the request: {str(e2)}"}
        return {'error': f"YouTube blocked the request: {str(e)}"}
    except Exception as e:
        logger.error(f"❌ Unexpected error in get_video_info: {str(e)}")
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
            if os.path.exists(file_path):
                os.remove(file_path)
            dir_path = os.path.dirname(file_path)
            if os.path.isdir(dir_path):
                os.rmdir(dir_path)
        except Exception as e:
            app.logger.error(f"Cleanup error: {e}")
        return response

    return send_file(file_path, as_attachment=True)

@app.route('/check_auth', methods=['GET'])
def check_auth_endpoint():
    """Diagnostic endpoint to verify authentication status."""
    po_token = get_po_token()
    return jsonify({
        'po_token': {'present': po_token is not None},
        'cookies': {'present': os.path.exists(COOKIES_FILE)},
        'auth_method': 'PO Token' if po_token else ('Cookies' if os.path.exists(COOKIES_FILE) else 'None')
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
