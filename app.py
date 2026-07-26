from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp
import os
import re
import glob
import uuid
import threading
import time
import subprocess
import json

app = Flask(__name__)
CORS(app)

DOWNLOAD_DIR = os.environ.get('DOWNLOAD_DIR', '/tmp/youtube_downloads')
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ===== COOKIES SETUP =====
COOKIES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt')
COOKIES_AVAILABLE = os.path.exists(COOKIES_PATH)

if COOKIES_AVAILABLE:
    print(f"✅ Cookies loaded from: {COOKIES_PATH}")
else:
    print(f"⚠️ cookies.txt not found at: {COOKIES_PATH}")

# ===== REMOTE COMPONENTS SETUP =====
DENO_AVAILABLE = False
try:
    result = subprocess.run(['deno', '--version'], capture_output=True, text=True, timeout=5)
    if result.returncode == 0:
        DENO_AVAILABLE = True
        print(f"✅ Deno available: {result.stdout.strip().split(chr(10))[0]}")
    else:
        print("⚠️ Deno not available")
except Exception as e:
    print(f"⚠️ Deno check failed: {e}")


def get_base_ydl_opts():
    opts = {
        "quiet": True,
        "no_warnings": True,
    }
    if COOKIES_AVAILABLE:
        opts["cookiefile"] = COOKIES_PATH
    return opts


def run_ytdlp_cli(args_list):
    cmd = ['yt-dlp']
    if COOKIES_AVAILABLE:
        cmd.extend(['--cookies', COOKIES_PATH])
    cmd.extend(args_list)
    print(f"[CLI] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=DOWNLOAD_DIR)
    return {
        'returncode': result.returncode,
        'stdout': result.stdout,
        'stderr': result.stderr,
        'cmd': ' '.join(cmd)
    }


def parse_quality_key(q):
    match = re.match(r'(\d+)p(\d+)?', q)
    if match:
        height = int(match.group(1))
        fps = int(match.group(2)) if match.group(2) else 30
        return (height, fps)
    return (0, 0)


def map_to_standard_quality(height):
    standard_qualities = [144, 240, 360, 480, 720, 1080, 1440, 2160, 4320]
    closest = min(standard_qualities, key=lambda x: abs(x - height))
    if abs(closest - height) <= 20:
        return closest
    if height <= 150: return 144
    elif height <= 280: return 240
    elif height <= 400: return 360
    elif height <= 560: return 480
    elif height <= 800: return 720
    elif height <= 1200: return 1080
    elif height <= 1800: return 1440
    elif height <= 2800: return 2160
    else: return 4320


def estimate_mp3_size(duration, bitrate=192):
    if not duration or duration <= 0:
        return None
    return (duration * bitrate) / (8 * 1024)


def format_duration(seconds):
    if not seconds or seconds <= 0:
        return "0:00"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_size_mb(size_mb):
    if size_mb is None:
        return None
    if size_mb >= 1000:
        return f"{size_mb/1024:.1f}GB".rstrip('0').rstrip('.') + "GB"
    return f"{int(size_mb)}MB"


def format_audio_quality(abr):
    if abr is None:
        return "0kbps"
    return f"{int(abr)}kbps"


def cleanup_file(filepath, delay=300):
    def delete():
        time.sleep(delay)
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except:
            pass
    threading.Thread(target=delete, daemon=True).start()


def get_info(url):
    ydl_opts = get_base_ydl_opts()
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def safe_int(val, default=0):
    try:
        return int(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def get_base_url():
    host = request.host_url.rstrip('/')
    return host


# ============ Routes ============

@app.route('/')
def index():
    return jsonify({
        "message": "YouTube Downloader API",
        "cookies_loaded": COOKIES_AVAILABLE,
        "deno_available": DENO_AVAILABLE,
        "endpoints": {
            "list_formats": "/youtube?url=<youtube_url>",
            "download_video": "/youtube/video?url=<youtube_url>&quality=<quality>",
            "download_audio": "/youtube/audio?url=<youtube_url>&quality=<quality>&type=<mp3|m4a|webm>"
        }
    })


@app.route('/youtube')
def list_formats():
    url = request.args.get('url')
    if not url:
        error_data = {"success": False, "error": "url parameter is required"}
        response_json = json.dumps(error_data, indent=2, ensure_ascii=False)
        return app.response_class(response_json, status=400, mimetype='application/json')

    try:
        info = get_info(url)
        formats = info.get("formats", [])
        base_url = get_base_url()
        duration = info.get("duration", 0)

        # ===== Video-only formats =====
        video_formats = {}
        for fmt in formats:
            if fmt.get("vcodec") != "none" and fmt.get("acodec") == "none":
                height = fmt.get("height", 0)
                if not height or height <= 0:
                    continue
                std_height = map_to_standard_quality(height)
                fps = safe_int(fmt.get("fps"), 0)
                quality_key = f"{std_height}p" + (f"{fps}" if fps > 30 else "")

                if quality_key not in video_formats:
                    video_formats[quality_key] = []
                video_formats[quality_key].append(fmt)

        video_qualities = []
        for q in sorted(video_formats.keys(), key=parse_quality_key):
            fmts = video_formats[q]
            best = None
            for f in fmts:
                if f.get("ext") == "mp4":
                    best = f
                    break
            if not best:
                best = fmts[0]

            size_mb = 0
            if best.get("filesize"):
                size_mb = best["filesize"] / (1024 * 1024)
            elif best.get("filesize_approx"):
                size_mb = best["filesize_approx"] / (1024 * 1024)

            video_qualities.append({
                "quality": q,
                "format_id": best.get("format_id"),
                "ext": best.get("ext", "unknown"),
                "fps": safe_int(best.get("fps"), 30),
                "size_mb": round(size_mb, 1) if size_mb else None,
                "size_mb_format": format_size_mb(size_mb) if size_mb else None,
                "vcodec": best.get("vcodec", "unknown"),
                "download_url": f"{base_url}/youtube/video?url={url}&quality={q}"
            })

        # ===== Audio formats =====
        audio_formats = []
        for fmt in formats:
            if fmt.get("acodec") != "none" and fmt.get("vcodec") == "none":
                size_mb = 0
                if fmt.get("filesize"):
                    size_mb = fmt["filesize"] / (1024 * 1024)
                elif fmt.get("filesize_approx"):
                    size_mb = fmt["filesize_approx"] / (1024 * 1024)

                abr = fmt.get("abr", 0) or 0
                ext = fmt.get("ext", "unknown")
                audio_formats.append({
                    "quality": abr,
                    "quality_format": format_audio_quality(abr),
                    "format_id": fmt.get("format_id"),
                    "ext": ext,
                    "acodec": fmt.get("acodec", "unknown"),
                    "size_mb": round(size_mb, 1) if size_mb else None,
                    "size_mb_format": format_size_mb(size_mb) if size_mb else None,
                    "download_url": f"{base_url}/youtube/audio?url={url}&quality={format_audio_quality(abr)}&type={ext}"
                })
        audio_formats.sort(key=lambda x: x["quality"], reverse=True)

        # ===== MP3 estimate =====
        mp3_size = estimate_mp3_size(duration)

        response_data = {
            "success": True,
            "video_id": info.get("id"),
            "title": info.get("title"),
            "uploader": info.get("uploader"),
            "thumbnail": info.get("thumbnail"),
            "duration": duration,
            "duration_formatted": format_duration(duration),
            "formats": {
                "video": video_qualities,
                "audio": audio_formats,
                "mp3": {
                    "quality": 192,
                    "quality_format": "192kbps",
                    "bitrate": 192,
                    "estimated_size_mb": round(mp3_size, 1) if mp3_size else None,
                    "size_mb": round(mp3_size, 1) if mp3_size else None,
                    "size_mb_format": format_size_mb(mp3_size) if mp3_size else None,
                    "download_url": f"{base_url}/youtube/audio?url={url}&quality=192kbps&type=mp3"
                }
            }
        }

        response_json = json.dumps(response_data, indent=2, ensure_ascii=False)
        return app.response_class(response_json, mimetype='application/json')

    except Exception as e:
        error_data = {"success": False, "error": str(e)}
        response_json = json.dumps(error_data, indent=2, ensure_ascii=False)
        return app.response_class(response_json, status=500, mimetype='application/json')


@app.route('/youtube/video')
def download_video():
    url = request.args.get('url')
    quality = request.args.get('quality')

    if not url or not quality:
        error_data = {"success": False, "error": "url and quality parameters are required"}
        response_json = json.dumps(error_data, indent=2, ensure_ascii=False)
        return app.response_class(response_json, status=400, mimetype='application/json')

    try:
        info = get_info(url)
        title = info.get("title", "video")
        safe_title = re.sub(r'[^\w\s-]', '', title).strip() or "video"
        download_id = str(uuid.uuid4())
        base_opts = get_base_ydl_opts()

        target_height, _ = parse_quality_key(quality)
        if target_height == 0:
            digits = re.findall(r'\d+', quality)
            if digits:
                target_height = int(digits[0])

        if target_height > 0:
            format_selector = (
                f"best[height={target_height}]/"
                f"best[height<={target_height}]/"
                f"bestvideo[height={target_height}]+bestaudio/"
                f"bestvideo[height<={target_height}]+bestaudio/"
                f"best"
            )
        else:
            format_selector = "bestvideo+bestaudio/best"

        ydl_opts = {
            **base_opts,
            'outtmpl': os.path.join(DOWNLOAD_DIR, f'{download_id}.%(ext)s'),
            'format': format_selector,
            'merge_output_format': 'mp4',
            'noplaylist': True,
        }

        print(f"[VIDEO] URL: {url}")
        print(f"[VIDEO] Quality param: {quality}")
        print(f"[VIDEO] Target height: {target_height}")
        print(f"[VIDEO] Format selector: {format_selector}")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{download_id}.*"))
        if not files:
            error_data = {"success": False, "error": "Download failed - file not found"}
            response_json = json.dumps(error_data, indent=2, ensure_ascii=False)
            return app.response_class(response_json, status=500, mimetype='application/json')

        filepath = files[0]
        actual_ext = os.path.splitext(filepath)[1][1:]

        print(f"[VIDEO] Downloaded: {filepath} ({actual_ext})")
        cleanup_file(filepath)

        return send_file(
            filepath,
            as_attachment=True,
            download_name=f"{safe_title}.{actual_ext}"
        )

    except Exception as e:
        print(f"[VIDEO] ERROR: {str(e)}")
        error_data = {"success": False, "error": str(e)}
        response_json = json.dumps(error_data, indent=2, ensure_ascii=False)
        return app.response_class(response_json, status=500, mimetype='application/json')


@app.route('/youtube/audio')
def download_audio():
    url = request.args.get('url')
    type_ = request.args.get('type', 'mp3')

    if not url:
        error_data = {"success": False, "error": "url parameter is required"}
        response_json = json.dumps(error_data, indent=2, ensure_ascii=False)
        return app.response_class(response_json, status=400, mimetype='application/json')

    try:
        info = get_info(url)
        title = info.get("title", "audio")
        safe_title = re.sub(r'[^\w\s-]', '', title).strip() or "audio"
        download_id = str(uuid.uuid4())
        base_opts = get_base_ydl_opts()

        audio_type = type_.lower().strip()
        print(f"[AUDIO] Requested type: {audio_type}")

        if audio_type == 'mp3':
            # MP3: convert using FFmpeg
            ydl_opts = {
                **base_opts,
                'outtmpl': os.path.join(DOWNLOAD_DIR, f'{download_id}.%(ext)s'),
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'noplaylist': True,
            }
            output_ext = 'mp3'
            print(f"[AUDIO] Downloading MP3 (converting via FFmpeg)")

        elif audio_type == 'm4a':
            # M4A: try to get m4a first, if not convert to m4a
            ydl_opts = {
                **base_opts,
                'outtmpl': os.path.join(DOWNLOAD_DIR, f'{download_id}.%(ext)s'),
                'format': 'bestaudio[ext=m4a]/bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'm4a',
                    'preferredquality': '192',
                }],
                'noplaylist': True,
            }
            output_ext = 'm4a'
            print(f"[AUDIO] Downloading M4A (prefer m4a, convert via FFmpeg if needed)")

        elif audio_type == 'webm':
            # WEBM: try to get webm/opus first, no conversion needed
            ydl_opts = {
                **base_opts,
                'outtmpl': os.path.join(DOWNLOAD_DIR, f'{download_id}.%(ext)s'),
                'format': 'bestaudio[ext=webm]/bestaudio/best',
                'noplaylist': True,
            }
            output_ext = 'webm'
            print(f"[AUDIO] Downloading WEBM (prefer webm/opus)")

        else:
            # Default / any other type
            ydl_opts = {
                **base_opts,
                'outtmpl': os.path.join(DOWNLOAD_DIR, f'{download_id}.%(ext)s'),
                'format': 'bestaudio/best',
                'noplaylist': True,
            }
            output_ext = audio_type
            print(f"[AUDIO] Downloading best audio (type={audio_type})")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{download_id}.*"))
        if not files:
            error_data = {"success": False, "error": "Download failed - file not found"}
            response_json = json.dumps(error_data, indent=2, ensure_ascii=False)
            return app.response_class(response_json, status=500, mimetype='application/json')

        filepath = files[0]
        actual_ext = os.path.splitext(filepath)[1][1:]

        print(f"[AUDIO] Downloaded: {filepath} (actual ext: {actual_ext}, requested: {output_ext})")

        # If the actual extension doesn't match what user requested, 
        # and it's not a conversion we already did, rename the file
        if actual_ext != output_ext and audio_type not in ['mp3', 'm4a']:
            new_filepath = os.path.splitext(filepath)[0] + f'.{output_ext}'
            os.rename(filepath, new_filepath)
            filepath = new_filepath
            actual_ext = output_ext
            print(f"[AUDIO] Renamed to: {filepath}")

        cleanup_file(filepath)

        # Use the requested type as the download extension
        return send_file(
            filepath,
            as_attachment=True,
            download_name=f"{safe_title}.{output_ext}"
        )

    except Exception as e:
        print(f"[AUDIO] ERROR: {str(e)}")
        error_data = {"success": False, "error": str(e)}
        response_json = json.dumps(error_data, indent=2, ensure_ascii=False)
        return app.response_class(response_json, status=500, mimetype='application/json')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
