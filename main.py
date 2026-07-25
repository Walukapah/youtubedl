import os
import re
import shutil
import uuid
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp

app = FastAPI(
    title="YouTube Downloader API",
    description="YouTube Video & Audio Downloader API powered by yt-dlp",
    version="1.0.0",
)

# CORS - Browser එකෙන් call කරන්න පුලුවන්
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== Helper Functions ====================

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

def get_available_formats(url):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            formats = info.get("formats", [])

            # Video-only formats
            video_formats = {}
            for fmt in formats:
                if fmt.get("vcodec") != "none" and fmt.get("acodec") == "none":
                    height = fmt.get("height", 0)
                    ext = fmt.get("ext", "unknown")
                    format_id = fmt.get("format_id", "")
                    fps = fmt.get("fps", 0)
                    std_height = map_to_standard_quality(height)
                    quality_key = f"{std_height}p"
                    if fps and fps > 30:
                        quality_key = f"{std_height}p{int(fps)}"
                    if height and height > 0:
                        if quality_key not in video_formats:
                            video_formats[quality_key] = {"formats": [], "actual_heights": set()}
                        video_formats[quality_key]["formats"].append({
                            "format_id": format_id,
                            "ext": ext,
                            "fps": fps,
                            "filesize": fmt.get("filesize") or fmt.get("filesize_approx", 0),
                            "vcodec": fmt.get("vcodec", "unknown"),
                            "actual_height": height,
                        })
                        video_formats[quality_key]["actual_heights"].add(height)

            # Audio-only formats
            audio_formats = []
            for fmt in formats:
                if fmt.get("acodec") != "none" and fmt.get("vcodec") == "none":
                    audio_formats.append({
                        "format_id": fmt.get("format_id", ""),
                        "ext": fmt.get("ext", "unknown"),
                        "abr": fmt.get("abr", 0) or 0,
                        "acodec": fmt.get("acodec", "unknown"),
                        "filesize": fmt.get("filesize") or fmt.get("filesize_approx", 0),
                    })
            audio_formats.sort(key=lambda x: x["abr"], reverse=True)

            # Combined formats
            combined_formats = []
            for fmt in formats:
                if fmt.get("vcodec") != "none" and fmt.get("acodec") != "none":
                    combined_formats.append({
                        "format_id": fmt.get("format_id", ""),
                        "ext": fmt.get("ext", "unknown"),
                        "height": fmt.get("height", 0),
                        "quality": fmt.get("quality", ""),
                    })

            sorted_video_formats = dict(sorted(video_formats.items(), key=lambda x: parse_quality_key(x[0])))

            return {
                "title": info.get("title", "Unknown"),
                "duration": info.get("duration", 0),
                "uploader": info.get("uploader", "Unknown"),
                "thumbnail": info.get("thumbnail", ""),
                "video_formats": sorted_video_formats,
                "audio_formats": audio_formats,
                "combined_formats": combined_formats,
            }
        except Exception as e:
            print(f"Error fetching formats: {e}")
            return None

def cleanup_temp(path: str):
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass

def sanitize_filename(name):
    return re.sub(r'[^\w\s-]', '', name).strip()

# ==================== API Endpoints ====================

@app.get("/")
async def root():
    return {
        "status": "running",
        "docs": "/docs",
        "endpoints": {
            "formats": "/youtube?url=URL",
            "video": "/youtube/video?url=URL&quality=QUALITY",
            "audio": "/youtube/audio?url=URL&quality=QUALITY&type=TYPE"
        }
    }

@app.get("/youtube")
async def get_info(url: str):
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    info = get_available_formats(url)
    if not info:
        raise HTTPException(status_code=400, detail="Failed to fetch video info. Invalid URL or video unavailable.")

    qualities = list(info["video_formats"].keys())

    # Video options (merge video + audio)
    video_options = []
    for q in qualities:
        fmt_data = info["video_formats"][q]
        formats = fmt_data["formats"]
        best = next((f for f in formats if f["ext"] == "mp4"), formats[0])
        actual_heights = fmt_data["actual_heights"]
        note = ""
        if len(actual_heights) == 1:
            actual = list(actual_heights)[0]
            if actual != parse_quality_key(q)[0]:
                note = f"actual: {actual}p"

        video_options.append({
            "type": "video",
            "quality": q,
            "ext": best["ext"],
            "fps": best["fps"],
            "size_mb": round(best["filesize"] / (1024 * 1024), 1) if best["filesize"] else None,
            "note": note
        })

    # Combined options
    combined_seen = set()
    combined_options = []
    for fmt in info["combined_formats"]:
        h = fmt["height"]
        if h and h > 0:
            std_h = map_to_standard_quality(h)
            if std_h not in combined_seen:
                combined_seen.add(std_h)
                combined_options.append({
                    "type": "combined",
                    "quality": f"{std_h}p",
                    "height": std_h,
                    "ext": fmt["ext"],
                })
    combined_options.sort(key=lambda x: x["height"])

    # Audio options
    audio_options = []
    for af in info["audio_formats"]:
        size_mb = af["filesize"] / (1024 * 1024) if af["filesize"] else 0
        audio_options.append({
            "type": "audio",
            "format_id": af["format_id"],
            "bitrate": int(af["abr"]) if af["abr"] else None,
            "ext": af["ext"],
            "codec": af["acodec"].split('.')[0] if af["acodec"] != "unknown" else "Unknown",
            "size_mb": round(size_mb, 1) if size_mb > 0 else None,
        })

    # MP3 estimate
    mp3_size = estimate_mp3_size(info.get("duration", 0))

    return {
        "title": info["title"],
        "duration": info["duration"],
        "duration_formatted": f"{info['duration']//3600:02d}:{(info['duration']%3600)//60:02d}:{info['duration']%60:02d}" if info["duration"] else None,
        "uploader": info["uploader"],
        "thumbnail": info["thumbnail"],
        "options": {
            "video": video_options,
            "combined": combined_options,
            "audio": audio_options,
            "mp3": {
                "type": "mp3",
                "bitrate": 192,
                "estimated_size_mb": round(mp3_size, 1) if mp3_size else None
            }
        }
    }

@app.get("/youtube/video")
async def download_video(url: str, quality: str, background_tasks: BackgroundTasks):
    if not url or not quality:
        raise HTTPException(status_code=400, detail="URL and quality are required")

    info = get_available_formats(url)
    if not info:
        raise HTTPException(status_code=400, detail="Failed to fetch video info")

    temp_dir = f"/tmp/ytdl_{uuid.uuid4().hex}"
    os.makedirs(temp_dir, exist_ok=True)

    try:
        # 1. Try video-only format (merge with audio)
        if quality in info["video_formats"]:
            fmt_data = info["video_formats"][quality]
            video_formats = fmt_data["formats"]
            best_video = next((f for f in video_formats if f["ext"] == "mp4"), video_formats[0])

            ydl_opts = {
                "outtmpl": f"{temp_dir}/file.%(ext)s",
                "format": f"{best_video['format_id']}+bestaudio/best",
                "merge_output_format": "mp4",
                "noplaylist": True,
            }
            display_quality = quality
            ext_hint = ".mp4"

        else:
            # 2. Try combined format
            match = re.match(r'(\d+)p', quality)
            if not match:
                raise HTTPException(status_code=400, detail=f"Quality '{quality}' not available")

            target_height = int(match.group(1))
            best_combined = None
            for fmt in info["combined_formats"]:
                if map_to_standard_quality(fmt["height"]) == target_height:
                    if not best_combined or fmt["format_id"] > best_combined["format_id"]:
                        best_combined = fmt

            if not best_combined:
                raise HTTPException(status_code=400, detail=f"Quality '{quality}' not available")

            ydl_opts = {
                "outtmpl": f"{temp_dir}/file.%(ext)s",
                "format": best_combined["format_id"],
                "noplaylist": True,
            }
            display_quality = quality
            ext_hint = f".{best_combined['ext']}"

        # Download
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        files = [f for f in os.listdir(temp_dir) if os.path.isfile(os.path.join(temp_dir, f))]
        if not files:
            raise HTTPException(status_code=500, detail="Download failed")

        downloaded_file = os.path.join(temp_dir, files[0])

        safe_title = sanitize_filename(info["title"]).replace(" ", "_")
        ext = os.path.splitext(downloaded_file)[1] or ext_hint
        download_name = f"{safe_title}_{display_quality}{ext}"

        background_tasks.add_task(cleanup_temp, temp_dir)

        return FileResponse(
            path=downloaded_file,
            filename=download_name,
            media_type="application/octet-stream"
        )

    except HTTPException:
        cleanup_temp(temp_dir)
        raise
    except Exception as e:
        cleanup_temp(temp_dir)
        raise HTTPException(status_code=500, detail=f"Download error: {str(e)}")

@app.get("/youtube/audio")
async def download_audio(
    url: str,
    quality: str = "",
    type: str = Query("original", enum=["original", "mp3"]),
    background_tasks: BackgroundTasks = None
):
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    if type == "original" and not quality:
        raise HTTPException(status_code=400, detail="Quality is required for original audio")

    info = get_available_formats(url)
    if not info:
        raise HTTPException(status_code=400, detail="Failed to fetch video info")

    temp_dir = f"/tmp/ytdl_{uuid.uuid4().hex}"
    os.makedirs(temp_dir, exist_ok=True)

    try:
        if type == "mp3":
            ydl_opts = {
                "outtmpl": f"{temp_dir}/file.%(ext)s",
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
                "noplaylist": True,
            }
            display_suffix = "192kbps"
            ext_hint = ".mp3"

        else:
            # Find audio by format_id or bitrate
            selected_audio = None
            for af in info["audio_formats"]:
                if af["format_id"] == quality or (af["abr"] and str(int(af["abr"])) == quality):
                    selected_audio = af
                    break

            if not selected_audio:
                raise HTTPException(status_code=400, detail=f"Audio quality '{quality}' not available")

            ydl_opts = {
                "outtmpl": f"{temp_dir}/file.%(ext)s",
                "format": selected_audio["format_id"],
                "noplaylist": True,
            }
            display_suffix = f"{int(selected_audio['abr']) if selected_audio['abr'] else '?'}kbps"
            ext_hint = f".{selected_audio['ext']}"

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        files = [f for f in os.listdir(temp_dir) if os.path.isfile(os.path.join(temp_dir, f))]
        if not files:
            raise HTTPException(status_code=500, detail="Download failed")

        downloaded_file = os.path.join(temp_dir, files[0])

        safe_title = sanitize_filename(info["title"]).replace(" ", "_")
        ext = os.path.splitext(downloaded_file)[1] or ext_hint
        download_name = f"{safe_title}_{display_suffix}{ext}"

        background_tasks.add_task(cleanup_temp, temp_dir)

        return FileResponse(
            path=downloaded_file,
            filename=download_name,
            media_type="application/octet-stream"
        )

    except HTTPException:
        cleanup_temp(temp_dir)
        raise
    except Exception as e:
        cleanup_temp(temp_dir)
        raise HTTPException(status_code=500, detail=f"Download error: {str(e)}")
