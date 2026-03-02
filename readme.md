# 🎬 Downloader pro

A secure and feature-rich YouTube video & playlist downloader built with Flask and yt-dlp.

This application allows users to fetch video information, select formats, track download progress in real time, and download files securely with rate limiting and CAPTCHA protection.

---

## 🚀 Features

-  Download YouTube videos & playlists
-  Select specific video/audio formats
-  Automatic video + audio merging (bestvideo+bestaudio)
-  Real-time download progress tracking
-  Background threaded downloads
-  Rate limiting with Flask-Limiter
-  Simple math CAPTCHA protection
-  Download history stored in SQLite
-  Automatic file cleanup after download
-  Secure session handling

---

## 🛠 Tech Stack

- Python 3
- Flask
- yt-dlp
- SQLite3
- Flask-Limiter
- Threading
- HTML / JavaScript (Frontend)

---

## ⚙️ Installation

### 1️⃣ Clone the repository



### 2️⃣ Create virtual environment

```bash
python -m venv venv
source venv/bin/activate   # Linux / Mac
venv\Scripts\activate      # Windows
```

### 3️⃣ Install dependencies

```bash
pip install flask flask-limiter yt-dlp
```

Or create a requirements.txt:

```bash
pip freeze > requirements.txt
pip install -r requirements.txt
```

### 4️⃣ Run the app

```bash
python app.py
```

Server will start at:

```
http://127.0.0.1:5000
```

---

## 📌 How It Works

1. User submits a YouTube URL.
2. Backend fetches video info using yt-dlp.
3. User selects format.
4. Download starts in background thread.
5. Progress is tracked via `/progress/<task_id>`.
6. File is served and automatically deleted after download.

---

## 🔐 Security Features

- Rate limiting:
  - 200 requests per day
  - 50 requests per hour
- Download limit per session
- CAPTCHA after multiple downloads
- Secure session handling
- Temporary file cleanup


---

## 📊 API Endpoints

| Endpoint | Method | Description |
|----------|--------|------------|
| `/` | GET | Homepage |
| `/get_info` | POST | Fetch video info |
| `/start_download` | POST | Start background download |
| `/progress/<task_id>` | GET | Track download progress |
| `/download_file/<task_id>` | GET | Download completed file |
| `/history` | GET | View last 50 downloads |
| `/captcha` | GET | Generate new CAPTCHA |

---

## 🗂 Project Structure

```
project/
│
├── app.py
├── downloads.db
├── templates/
└── requirements.txt
```

---

## ⚠️ Disclaimer

This project is for educational purposes only.  
Users are responsible for complying with YouTube's terms of service and local copyright laws.

---

## 👨‍💻 Author

**Enock Deghost**  
Aspiring Software Engineer 🚀 
GitHub: https://github.com/enockdeghost

---
