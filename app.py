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


# ============ Helper Functions ============

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
    """Delete file after delay seconds (default 5 min)"""
    def delete():
        time.sleep(delay)
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except:
            pass
    threading.Thread(target=delete, daemon=True).start()


def get_info(url):
    ydl_opts = {"quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


# ============ Routes ============

@app.route('/')
def index():
    return jsonify({
        "message": "YouTube Downloader API",
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
                fps = fmt.get("fps", 0)
                quality_key = f"{std_height}p" + (f"{int(fps)}" if fps and fps > 30 else "")

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
                "fps": best.get("fps", 30),
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
        video_id = info.get("id")
        title = info.get("title", "video")
        safe_title = re.sub(r'[^\w\s-]', '', title).strip() or "video"

        formats = info.get("formats", [])

        # Try combined format first
        combined_match = None
        for fmt in formats:
            if fmt.get("vcodec") != "none" and fmt.get("acodec") != "none":
                h = fmt.get("height", 0)
                if h and map_to_standard_quality(h) == parse_quality_key(quality)[0]:
                    if not combined_match or fmt.get("format_id", "0") > combined_match.get("format_id", "0"):
                        combined_match = fmt

        # Try video-only format
        video_match = None
        for fmt in formats:
            if fmt.get("vcodec") != "none" and fmt.get("acodec") == "none":
                h = fmt.get("height", 0)
                std_h = map_to_standard_quality(h)
                fps = fmt.get("fps", 0)
                qk = f"{std_h}p" + (f"{int(fps)}" if fps and fps > 30 else "")
                if qk == quality:
                    if not video_match or (fmt.get("ext") == "mp4" and video_match.get("ext") != "mp4"):
                        video_match = fmt

        download_id = str(uuid.uuid4())

        if combined_match and not video_match:
            ydl_opts = {
                'outtmpl': os.path.join(DOWNLOAD_DIR, f'{download_id}.%(ext)s'),
                'format': combined_match["format_id"],
                'noplaylist': True,
            }
            print(f"Downloading combined: {combined_match['format_id']}")

        elif video_match:
            ydl_opts = {
                'outtmpl': os.path.join(DOWNLOAD_DIR, f'{download_id}.%(ext)s'),
                'format': f"{video_match['format_id']}+bestaudio/best",
                'merge_output_format': 'mp4',
                'noplaylist': True,
            }
            print(f"Downloading video+merge: {video_match['format_id']}")

        else:
            ydl_opts = {
                'outtmpl': os.path.join(DOWNLOAD_DIR, f'{download_id}.%(ext)s'),
                'format': 'best',
                'noplaylist': True,
            }
            print("Falling back to best")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{download_id}.*"))
        if not files:
            return jsonify({"success": False, "error": "Download failed - file not found"}), 500

        filepath = files[0]
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

        if type_.lower() == 'mp3':
            ydl_opts = {
                'outtmpl': os.path.join(DOWNLOAD_DIR, f'{download_id}.%(ext)s'),
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'noplaylist': True,
            }

        else:
            if quality and quality != 'best':
                ydl_opts = {
                    'outtmpl': os.path.join(DOWNLOAD_DIR, f'{download_id}.%(ext)s'),
                    'format': quality,
                    'noplaylist': True,
                }
            else:
                ydl_opts = {
                    'outtmpl': os.path.join(DOWNLOAD_DIR, f'{download_id}.%(ext)s'),
                    'format': 'bestaudio/best',
                    'noplaylist': True,
                }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{download_id}.*"))
        if not files:
            return jsonify({"success": False, "error": "Download failed - file not found"}), 500

        filepath = files[0]
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
