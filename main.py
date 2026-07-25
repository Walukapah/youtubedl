import os
import re
import shutil
import uuid
import logging
import json
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="YouTube Downloader API",
    version="2.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== Config ====================

COOKIES_FILE = os.getenv("COOKIES_FILE", "/app/cookies.txt")
PO_TOKEN = os.getenv("PO_TOKEN", "")
VISITOR_DATA = os.getenv("VISITOR_DATA", "")
PROXY_URL = os.getenv("PROXY_URL", "")

def get_base_ydl_opts():
    """Base yt-dlp options with auth"""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.0",
        "referer": "https://www.youtube.com/",
        "headers": {
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        }
    }

    # Method 1: Cookies file
    if os.path.exists(COOKIES_FILE):
        logger.info(f"Using cookies file: {COOKIES_FILE}")
        opts["cookies"] = COOKIES_FILE

    # Method 2: PO Token + Visitor Data (BEST for servers)
    if PO_TOKEN and VISITOR_DATA:
        logger.info("Using PO Token + Visitor Data")
        opts["extractor_args"] = {
            "youtube": {
                "player_client": ["web"],
                "player_skip": ["webpage", "configs", "js"],
                "po_token": [PO_TOKEN],
                "visitor_data": [VISITOR_DATA],
            }
        }

    # Method 3: Proxy
    if PROXY_URL:
        logger.info(f"Using proxy: {PROXY_URL}")
        opts["proxy"] = PROXY_URL

    return opts

# ==================== Helpers ====================

def parse_quality_key(q):
    match = re.match(r'(\d+)p(\d+)?', q)
    if match:
        return (int(match.group(1)), int(match.group(2)) if match.group(2) else 30)
    return (0, 0)

def map_to_standard_quality(height):
    standard = [144, 240, 360, 480, 720, 1080, 1440, 2160, 4320]
    closest = min(standard, key=lambda x: abs(x - height))
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

def get_available_formats(url):
    ydl_opts = get_base_ydl_opts()
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            logger.info(f"Fetching: {url}")
            info = ydl.extract_info(url, download=False)
            if not info:
                return None, "Empty response from yt-dlp"

            formats = info.get("formats", [])
            video_formats = {}
            audio_formats = []
            combined_formats = []

            for fmt in formats:
                vcodec = fmt.get("vcodec", "none")
                acodec = fmt.get("acodec", "none")

                if vcodec != "none" and acodec == "none":
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

                elif acodec != "none" and vcodec == "none":
                    audio_formats.append({
                        "format_id": fmt.get("format_id", ""),
                        "ext": fmt.get("ext", "unknown"),
                        "abr": fmt.get("abr", 0) or 0,
                        "acodec": fmt.get("acodec", "unknown"),
                        "filesize": fmt.get("filesize") or fmt.get("filesize_approx", 0),
                    })

                elif vcodec != "none" and acodec != "none":
                    combined_formats.append({
                        "format_id": fmt.get("format_id", ""),
                        "ext": fmt.get("ext", "unknown"),
                        "height": fmt.get("height", 0),
                        "quality": fmt.get("quality", ""),
                    })

            audio_formats.sort(key=lambda x: x["abr"], reverse=True)
            sorted_video = dict(sorted(video_formats.items(), key=lambda x: parse_quality_key(x[0])))

            return {
                "title": info.get("title", "Unknown"),
                "duration": info.get("duration", 0),
                "uploader": info.get("uploader", "Unknown"),
                "thumbnail": info.get("thumbnail", ""),
                "video_formats": sorted_video,
                "audio_formats": audio_formats,
                "combined_formats": combined_formats,
            }, None

        except yt_dlp.utils.DownloadError as e:
            err = str(e)
            logger.error(f"yt-dlp error: {err}")
            if "Sign in to confirm" in err:
                return None, "YOUTUBE_BOT_DETECTED: YouTube detected bot. Need cookies or PO token. See /docs for setup."
            return None, f"yt-dlp error: {err}"
        except Exception as e:
            logger.error(f"Error: {e}")
            return None, str(e)

def cleanup_temp(path: str):
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass

def sanitize_filename(name):
    return re.sub(r'[^\w\s-]', '', name).strip()

# ==================== Endpoints ====================

@app.get("/")
async def root():
    return {
        "status": "running",
        "yt_dlp_version": yt_dlp.version.__version__,
        "auth_methods": {
            "cookies_file": os.path.exists(COOKIES_FILE),
            "po_token": bool(PO_TOKEN),
            "visitor_data": bool(VISITOR_DATA),
            "proxy": bool(PROXY_URL),
        },
        "docs": "/docs",
        "setup_guide": "/setup"
    }

@app.get("/setup")
async def setup_guide():
    return {
        "problem": "YouTube detects server IPs as bots and requires authentication",
        "solutions": [
            {
                "method": "PO_TOKEN + VISITOR_DATA (RECOMMENDED)",
                "description": "Most reliable for servers. No browser needed.",
                "steps": [
                    "1. Open YouTube in Chrome browser",
                    "2. Press F12 → Console tab",
                    "3. Paste: document.cookie.split(';').find(c => c.trim().startsWith('VISITOR_INFO1_LIVE='))?.split('=')[1]",
                    "4. Copy the value → set as VISITOR_DATA env var",
                    "5. For PO Token, use: https://github.com/yt-dlp/yt-dlp/wiki/Extractors#po-token-guide",
                    "6. Or use: https://github.com/YunzhiYike/yt-dlp-po-token"
                ],
                "docker_env": "-e PO_TOKEN=your_token -e VISITOR_DATA=your_visitor_data"
            },
            {
                "method": "Cookies File",
                "description": "Export cookies from your browser",
                "steps": [
                    "1. Install 'Get cookies.txt LOCALLY' Chrome extension",
                    "2. Go to youtube.com and sign in",
                    "3. Click extension → Export cookies.txt",
                    "4. Save as cookies.txt",
                    "5. Mount to /app/cookies.txt in Docker"
                ],
                "docker_volume": "-v $(pwd)/cookies.txt:/app/cookies.txt"
            },
            {
                "method": "Proxy",
                "description": "Route through residential proxy",
                "docker_env": "-e PROXY_URL=http://user:pass@proxy:port"
            }
        ]
    }

@app.get("/youtube")
async def get_info(url: str):
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
    if "youtube.com" not in url and "youtu.be" not in url:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")

    info, error = get_available_formats(url)
    if not info:
        raise HTTPException(status_code=400, detail=error)

    qualities = list(info["video_formats"].keys())

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

    info, error = get_available_formats(url)
    if not info:
        raise HTTPException(status_code=400, detail=error)

    temp_dir = f"/tmp/ytdl_{uuid.uuid4().hex}"
    os.makedirs(temp_dir, exist_ok=True)

    try:
        ydl_opts = get_base_ydl_opts()
        ydl_opts["outtmpl"] = f"{temp_dir}/file.%(ext)s"
        ydl_opts["noplaylist"] = True

        if quality in info["video_formats"]:
            fmt_data = info["video_formats"][quality]
            video_formats = fmt_data["formats"]
            best_video = next((f for f in video_formats if f["ext"] == "mp4"), video_formats[0])
            ydl_opts["format"] = f"{best_video['format_id']}+bestaudio/best"
            ydl_opts["merge_output_format"] = "mp4"
            display_quality = quality
            ext_hint = ".mp4"
        else:
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
            ydl_opts["format"] = best_combined["format_id"]
            display_quality = quality
            ext_hint = f".{best_combined['ext']}"

        logger.info(f"Downloading video: {ydl_opts}")
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
        raise HTTPException(status_code=500, detail=str(e))

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
        raise HTTPException(status_code=400, detail="Quality required for original audio")

    info, error = get_available_formats(url)
    if not info:
        raise HTTPException(status_code=400, detail=error)

    temp_dir = f"/tmp/ytdl_{uuid.uuid4().hex}"
    os.makedirs(temp_dir, exist_ok=True)

    try:
        ydl_opts = get_base_ydl_opts()
        ydl_opts["outtmpl"] = f"{temp_dir}/file.%(ext)s"
        ydl_opts["noplaylist"] = True

        if type == "mp3":
            ydl_opts["format"] = "bestaudio/best"
            ydl_opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
            display_suffix = "192kbps"
            ext_hint = ".mp3"
        else:
            selected_audio = None
            for af in info["audio_formats"]:
                if af["format_id"] == quality or (af["abr"] and str(int(af["abr"])) == quality):
                    selected_audio = af
                    break
            if not selected_audio:
                raise HTTPException(status_code=400, detail=f"Audio quality '{quality}' not available")
            ydl_opts["format"] = selected_audio["format_id"]
            display_suffix = f"{int(selected_audio['abr']) if selected_audio['abr'] else '?'}kbps"
            ext_hint = f".{selected_audio['ext']}"

        logger.info(f"Downloading audio: {ydl_opts}")
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
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/debug")
async def debug_ytdl(url: str):
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
    
    ydl_opts = get_base_ydl_opts()
    ydl_opts["quiet"] = False
    ydl_opts["no_warnings"] = False

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "success": True,
                "title": info.get("title"),
                "duration": info.get("duration"),
                "formats_count": len(info.get("formats", [])),
                "yt_dlp_version": yt_dlp.version.__version__,
                "auth_active": {
                    "cookies": os.path.exists(COOKIES_FILE),
                    "po_token": bool(PO_TOKEN),
                    "visitor_data": bool(VISITOR_DATA),
                    "proxy": bool(PROXY_URL),
                }
            }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
            "yt_dlp_version": yt_dlp.version.__version__,
            "auth_active": {
                "cookies": os.path.exists(COOKIES_FILE),
                "po_token": bool(PO_TOKEN),
                "visitor_data": bool(VISITOR_DATA),
                "proxy": bool(PROXY_URL),
            }
        }
