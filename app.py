from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp
import os
import re
import glob
import uuid
import threading
import time

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


def get_base_ydl_opts():
    opts = {"quiet": True, "no_warnings": True}
    if COOKIES_AVAILABLE:
        opts["cookiefile"] = COOKIES_PATH
    return opts


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
    if height <= 150:
        return 144
    elif height <= 280:
        return 240
    elif height <= 400:
        return 360
    elif height <= 560:
        return 480
    elif height <= 800:
        return 720
    elif height <= 1200:
        return 1080
    elif height <= 1800:
        return 1440
    elif height <= 2800:
        return 2160
    else:
        return 4320


def estimate_mp3_size(duration, bitrate=192):
    if not duration or duration <= 0:
        return None
    return (duration * bitrate) / (8 * 1024)


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


# ============ Routes ============

@app.route('/')
def index():
    return jsonify({
        "message": "YouTube Downloader API",
        "cookies_loaded": COOKIES_AVAILABLE,
        "endpoints": {
            "list_formats": "/youtube?url=<youtube_url>",
            "download_video": "/youtube/video?url=<youtube_url>&quality=<quality>",
            "download_audio": "/youtube/audio?url=<youtube_url>&quality=<format_id>&type=<mp3|m4a|webm>"
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
                "vcodec": best.get("vcodec", "unknown")
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
                            "ext": fmt.get("ext", "unknown")
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
                    "size_mb": round(size_mb, 1) if size_mb else None
                })
        audio_formats.sort(key=lambda x: x["abr"], reverse=True)

        # ===== MP3 estimate =====
        duration = info.get("duration", 0)
        mp3_size = estimate_mp3_size(duration)

        return jsonify({
            "success": True,
            "video_id": info.get("id"),
            "title": info.get("title"),
            "duration": duration,
            "uploader": info.get("uploader"),
            "thumbnail": info.get("thumbnail"),
            "formats": {
                "video": video_qualities,
                "combined": combined_qualities,
                "audio": audio_formats,
                "mp3": {
                    "bitrate": 192,
                    "estimated_size_mb": round(mp3_size, 1) if mp3_size else None
                }
            }
        })

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

        target_height, target_fps = parse_quality_key(quality)
        filepath = None

        # ===== Strategy 1: Use yt-dlp format selector by height (MOST RELIABLE) =====
        try:
            if target_height > 0:
                # Build format selector: prefer exact height, mp4, then fallback
                if target_fps > 30:
                    fmt_selector = (
                        f"bestvideo[height={target_height}][fps>30][ext=mp4]+bestaudio/"
                        f"bestvideo[height={target_height}][fps>30]+bestaudio/"
                        f"bestvideo[height={target_height}][ext=mp4]+bestaudio/"
                        f"bestvideo[height={target_height}]+bestaudio/"
                        f"best[height={target_height}]"
                    )
                else:
                    fmt_selector = (
                        f"bestvideo[height={target_height}][ext=mp4]+bestaudio/"
                        f"bestvideo[height={target_height}]+bestaudio/"
                        f"best[height={target_height}]"
                    )
            else:
                fmt_selector = "bestvideo+bestaudio/best"

            ydl_opts = {
                **base_opts,
                'outtmpl': os.path.join(DOWNLOAD_DIR, f'{download_id}.%(ext)s'),
                'format': fmt_selector,
                'merge_output_format': 'mp4',
                'noplaylist': True,
            }

            print(f"[VIDEO] Trying format selector: {fmt_selector}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{download_id}.*"))
            if files:
                filepath = files[0]
                print(f"[VIDEO] Success with format selector")

        except Exception as e1:
            print(f"[VIDEO] Format selector failed: {e1}")
            # Clean up partial files
            for f in glob.glob(os.path.join(DOWNLOAD_DIR, f"{download_id}.*")):
                try:
                    os.remove(f)
                except:
                    pass

        # ===== Strategy 2: Try exact format_id from info =====
        if not filepath:
            try:
                formats = info.get("formats", [])
                best_fmt = None

                for fmt in formats:
                    h = fmt.get("height", 0)
                    if not h:
                        continue
                    std_h = map_to_standard_quality(h)
                    fps = safe_int(fmt.get("fps"), 0)
                    qk = f"{std_h}p" + (f"{fps}" if fps > 30 else "")
                    if qk == quality:
                        if not best_fmt or (fmt.get("ext") == "mp4" and best_fmt.get("ext") != "mp4"):
                            best_fmt = fmt

                if best_fmt:
                    new_id = str(uuid.uuid4())
                    ydl_opts = {
                        **base_opts,
                        'outtmpl': os.path.join(DOWNLOAD_DIR, f'{new_id}.%(ext)s'),
                        'format': f"{best_fmt['format_id']}+bestaudio/best",
                        'merge_output_format': 'mp4',
                        'noplaylist': True,
                    }
                    print(f"[VIDEO] Trying exact format_id: {best_fmt['format_id']}")
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([url])

                    files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{new_id}.*"))
                    if files:
                        filepath = files[0]
                        download_id = new_id
                        print(f"[VIDEO] Success with exact format_id")

            except Exception as e2:
                print(f"[VIDEO] Exact format_id failed: {e2}")

        # ===== Strategy 3: Fallback to best available =====
        if not filepath:
            try:
                new_id = str(uuid.uuid4())
                ydl_opts = {
                    **base_opts,
                    'outtmpl': os.path.join(DOWNLOAD_DIR, f'{new_id}.%(ext)s'),
                    'format': 'bestvideo+bestaudio/best',
                    'merge_output_format': 'mp4',
                    'noplaylist': True,
                }
                print(f"[VIDEO] Falling back to best available")
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

                files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{new_id}.*"))
                if files:
                    filepath = files[0]
                    download_id = new_id
                    print(f"[VIDEO] Success with fallback")

            except Exception as e3:
                return jsonify({"success": False, "error": f"All download strategies failed. Last error: {str(e3)}"}), 500

        if not filepath or not os.path.exists(filepath):
            return jsonify({"success": False, "error": "Download failed - file not found"}), 500

        actual_ext = os.path.splitext(filepath)[1][1:]
        cleanup_file(filepath)

        return send_file(
            filepath,
            as_attachment=True,
            download_name=f"{safe_title}.{actual_ext}"
        )

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/youtube/audio')
def download_audio():
    url = request.args.get('url')
    quality = request.args.get('quality', 'best')
    type_ = request.args.get('type', 'mp3')

    if not url:
        return jsonify({"success": False, "error": "url parameter is required"}), 400

    try:
        info = get_info(url)
        title = info.get("title", "audio")
        safe_title = re.sub(r'[^\w\s-]', '', title).strip() or "audio"
        download_id = str(uuid.uuid4())
        base_opts = get_base_ydl_opts()
        filepath = None

        # ===== Strategy 1: MP3 Conversion =====
        if type_.lower() == 'mp3':
            try:
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
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

                files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{download_id}.*"))
                if files:
                    filepath = files[0]
                    print(f"[AUDIO] MP3 success")

            except Exception as e1:
                print(f"[AUDIO] MP3 failed: {e1}")

        # ===== Strategy 2: Specific audio format by format_id =====
        elif quality and quality != 'best':
            try:
                ydl_opts = {
                    **base_opts,
                    'outtmpl': os.path.join(DOWNLOAD_DIR, f'{download_id}.%(ext)s'),
                    'format': quality,
                    'noplaylist': True,
                }
                print(f"[AUDIO] Trying format_id: {quality}")
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

                files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{download_id}.*"))
                if files:
                    filepath = files[0]
                    print(f"[AUDIO] format_id success")

            except Exception as e2:
                print(f"[AUDIO] format_id failed: {e2}")
                # Clean up
                for f in glob.glob(os.path.join(DOWNLOAD_DIR, f"{download_id}.*")):
                    try:
                        os.remove(f)
                    except:
                        pass

        # ===== Strategy 3: Fallback to best audio =====
        if not filepath:
            try:
                new_id = str(uuid.uuid4())
                ydl_opts = {
                    **base_opts,
                    'outtmpl': os.path.join(DOWNLOAD_DIR, f'{new_id}.%(ext)s'),
                    'format': 'bestaudio/best',
                    'noplaylist': True,
                }
                print(f"[AUDIO] Falling back to best audio")
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

                files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{new_id}.*"))
                if files:
                    filepath = files[0]
                    download_id = new_id
                    print(f"[AUDIO] Fallback success")

            except Exception as e3:
                return jsonify({"success": False, "error": f"All audio download strategies failed. Last error: {str(e3)}"}), 500

        if not filepath or not os.path.exists(filepath):
            return jsonify({"success": False, "error": "Download failed - file not found"}), 500

        actual_ext = os.path.splitext(filepath)[1][1:]
        cleanup_file(filepath)

        return send_file(
            filepath,
            as_attachment=True,
            download_name=f"{safe_title}.{actual_ext}"
        )

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
