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
    """Seconds එක MM:SS හෝ HH:MM:SS විදියට format කරනවා"""
    if not seconds or seconds <= 0:
        return "0:00"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


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
    """API base URL එක generate කරනවා"""
    # Production එකේ Render/Hugging Face වගේ platform වල
    # request.host_url use කරනවා
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
            "download_audio": "/youtube/audio?url=<youtube_url>&type=<mp3|audio>",
            "cli_formats": "/youtube/cli-formats?url=<youtube_url>&remote_components=<optional>"
        }
    })


@app.route('/youtube')
def list_formats():
    url = request.args.get('url')
    if not url:
        return jsonify({"success": False, "error": "url parameter is required"}), 400

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
                "vcodec": best.get("vcodec", "unknown"),
                "download_url": f"{base_url}/youtube/video?url={url}&quality={q}"
            })

        # ===== Combined formats =====
        combined_seen = set()
        combined_qualities = []
        for fmt in formats:
            if fmt.get("vcodec") != "none" and fmt.get("acodec") != "none":
                h = fmt.get("height", 0)
                if h and h > 0:
                    std_h = map_to_standard_quality(h)
                    if std_h not in combined_seen:
                        combined_seen.add(std_h)
                        combined_qualities.append({
                            "quality": f"{std_h}p",
                            "format_id": fmt.get("format_id"),
                            "ext": fmt.get("ext", "unknown"),
                            "download_url": f"{base_url}/youtube/video?url={url}&quality={std_h}p"
                        })
        combined_qualities.sort(key=lambda x: parse_quality_key(x["quality"]))

        # ===== Audio formats =====
        audio_formats = []
        for fmt in formats:
            if fmt.get("acodec") != "none" and fmt.get("vcodec") == "none":
                size_mb = 0
                if fmt.get("filesize"):
                    size_mb = fmt["filesize"] / (1024 * 1024)
                elif fmt.get("filesize_approx"):
                    size_mb = fmt["filesize_approx"] / (1024 * 1024)

                audio_formats.append({
                    "format_id": fmt.get("format_id"),
                    "ext": fmt.get("ext", "unknown"),
                    "abr": fmt.get("abr", 0) or 0,
                    "acodec": fmt.get("acodec", "unknown"),
                    "size_mb": round(size_mb, 1) if size_mb else None,
                    "download_url": f"{base_url}/youtube/audio?url={url}&type=audio"
                })
        audio_formats.sort(key=lambda x: x["abr"], reverse=True)

        # ===== MP3 estimate =====
        mp3_size = estimate_mp3_size(duration)

        return jsonify({
            "success": True,
            "video_id": info.get("id"),
            "title": info.get("title"),
            "duration": duration,
            "duration_formatted": format_duration(duration),
            "uploader": info.get("uploader"),
            "thumbnail": info.get("thumbnail"),
            "formats": {
                "video": video_qualities,
                "combined": combined_qualities,
                "audio": audio_formats,
                "mp3": {
                    "bitrate": 192,
                    "estimated_size_mb": round(mp3_size, 1) if mp3_size else None,
                    "download_url": f"{base_url}/youtube/audio?url={url}&type=mp3"
                }
            }
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/youtube/cli-formats')
def cli_list_formats():
    url = request.args.get('url')
    remote_components = request.args.get('remote_components', '')

    if not url:
        return jsonify({"success": False, "error": "url parameter is required"}), 400

    try:
        args = ['-F', '--dump-json', url]

        if remote_components:
            args = ['--remote-components', remote_components] + args

        result = run_ytdlp_cli(args)

        if result['returncode'] != 0:
            return jsonify({
                "success": False,
                "error": result['stderr'] or result['stdout'],
                "cmd": result['cmd']
            }), 500

        lines = result['stdout'].strip().split('\n')
        formats = []
        info = None
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if 'formats' in data:
                    info = data
                else:
                    formats.append(data)
            except:
                pass

        return jsonify({
            "success": True,
            "cli_mode": True,
            "deno_available": DENO_AVAILABLE,
            "remote_components": remote_components or None,
            "info": info,
            "formats_count": len(formats)
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/youtube/cli-download')
def cli_download():
    url = request.args.get('url')
    quality = request.args.get('quality', 'best')
    remote_components = request.args.get('remote_components', '')

    if not url:
        return jsonify({"success": False, "error": "url parameter is required"}), 400

    try:
        download_id = str(uuid.uuid4())
        output_template = os.path.join(DOWNLOAD_DIR, f'{download_id}.%(ext)s')

        args = [
            '-f', quality,
            '-o', output_template,
            '--merge-output-format', 'mp4',
            '--no-playlist',
            url
        ]

        if remote_components:
            args = ['--remote-components', remote_components] + args

        result = run_ytdlp_cli(args)

        if result['returncode'] != 0:
            return jsonify({
                "success": False,
                "error": result['stderr'] or result['stdout'],
                "cmd": result['cmd']
            }), 500

        files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{download_id}.*"))
        if not files:
            return jsonify({"success": False, "error": "Download failed - file not found"}), 500

        filepath = files[0]
        actual_ext = os.path.splitext(filepath)[1][1:]

        info_args = ['--print', '%(title)s', url]
        if COOKIES_AVAILABLE:
            info_args = ['--cookies', COOKIES_PATH] + info_args
        info_result = subprocess.run(['yt-dlp'] + info_args, capture_output=True, text=True, timeout=60)
        title = info_result.stdout.strip() or "video"
        safe_title = re.sub(r'[^\w\s-]', '', title).strip() or "video"

        cleanup_file(filepath)

        return send_file(
            filepath,
            as_attachment=True,
            download_name=f"{safe_title}.{actual_ext}"
        )

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/youtube/video')
def download_video():
    url = request.args.get('url')
    quality = request.args.get('quality')

    if not url or not quality:
        return jsonify({"success": False, "error": "url and quality parameters are required"}), 400

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
            return jsonify({"success": False, "error": "Download failed - file not found"}), 500

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
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/youtube/audio')
def download_audio():
    url = request.args.get('url')
    type_ = request.args.get('type', 'mp3')

    if not url:
        return jsonify({"success": False, "error": "url parameter is required"}), 400

    try:
        info = get_info(url)
        title = info.get("title", "audio")
        safe_title = re.sub(r'[^\w\s-]', '', title).strip() or "audio"
        download_id = str(uuid.uuid4())
        base_opts = get_base_ydl_opts()

        if type_.lower() == 'mp3':
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
            print(f"[AUDIO] Downloading MP3")
        else:
            ydl_opts = {
                **base_opts,
                'outtmpl': os.path.join(DOWNLOAD_DIR, f'{download_id}.%(ext)s'),
                'format': 'bestaudio/best',
                'noplaylist': True,
            }
            print(f"[AUDIO] Downloading best audio")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{download_id}.*"))
        if not files:
            return jsonify({"success": False, "error": "Download failed - file not found"}), 500

        filepath = files[0]
        actual_ext = os.path.splitext(filepath)[1][1:]

        print(f"[AUDIO] Downloaded: {filepath}")
        cleanup_file(filepath)

        return send_file(
            filepath,
            as_attachment=True,
            download_name=f"{safe_title}.{actual_ext}"
        )

    except Exception as e:
        print(f"[AUDIO] ERROR: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
