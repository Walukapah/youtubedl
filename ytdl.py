import sys
import yt_dlp
import os
import re

def parse_quality_key(q):
    """Parse quality string like '132p', '720p60', '1080p' into (height, fps) tuple for sorting"""
    match = re.match(r'(\d+)p(\d+)?', q)
    if match:
        height = int(match.group(1))
        fps = int(match.group(2)) if match.group(2) else 30
        return (height, fps)
    return (0, 0)

def map_to_standard_quality(height):
    """Map any height to the nearest standard YouTube quality"""
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
    """Estimate MP3 file size in MB given duration in seconds and bitrate in kbps"""
    if not duration or duration <= 0:
        return None
    size_mb = (duration * bitrate) / (8 * 1024)
    return size_mb

def get_available_formats(url):
    """Get all available video formats from a YouTube URL"""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            formats = info.get("formats", [])

            # Filter video formats (with video stream)
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
                            video_formats[quality_key] = {
                                "formats": [],
                                "actual_heights": set(),
                            }
                        video_formats[quality_key]["formats"].append({
                            "format_id": format_id,
                            "ext": ext,
                            "fps": fps,
                            "filesize": fmt.get("filesize") or fmt.get("filesize_approx", 0),
                            "vcodec": fmt.get("vcodec", "unknown"),
                            "actual_height": height,
                        })
                        video_formats[quality_key]["actual_heights"].add(height)

            # Get audio-only formats with quality info
            audio_formats = []
            for fmt in formats:
                if fmt.get("acodec") != "none" and fmt.get("vcodec") == "none":
                    filesize = fmt.get("filesize") or fmt.get("filesize_approx", 0)
                    abr = fmt.get("abr", 0) or 0
                    audio_formats.append({
                        "format_id": fmt.get("format_id", ""),
                        "ext": fmt.get("ext", "unknown"),
                        "abr": abr,
                        "acodec": fmt.get("acodec", "unknown"),
                        "filesize": filesize,
                    })

            # Sort audio by bitrate (highest first)
            audio_formats.sort(key=lambda x: x["abr"], reverse=True)

            # Get combined formats
            combined_formats = []
            for fmt in formats:
                if fmt.get("vcodec") != "none" and fmt.get("acodec") != "none":
                    combined_formats.append({
                        "format_id": fmt.get("format_id", ""),
                        "ext": fmt.get("ext", "unknown"),
                        "height": fmt.get("height", 0),
                        "quality": fmt.get("quality", ""),
                    })

            sorted_video_formats = dict(sorted(
                video_formats.items(),
                key=lambda x: parse_quality_key(x[0])
            ))

            return {
                "title": info.get("title", "Unknown"),
                "duration": info.get("duration", 0),
                "uploader": info.get("uploader", "Unknown"),
                "video_formats": sorted_video_formats,
                "audio_formats": audio_formats,
                "combined_formats": combined_formats,
            }
        except Exception as e:
            print(f"\n❌ Error fetching formats: {e}")
            return None

def display_formats(info):
    """Display available formats in a user-friendly way"""
    print("\n" + "=" * 60)
    print(f"📹 {info['title']}")
    print(f"👤 {info['uploader']}")
    duration = info['duration']
    if duration:
        mins, secs = divmod(int(duration), 60)
        hrs, mins = divmod(mins, 60)
        print(f"⏱️  Duration: {hrs:02d}:{mins:02d}:{secs:02d}")
    print("=" * 60)

    print("\n🎬 Available Video Qualities:")
    print("-" * 60)

    qualities = list(info["video_formats"].keys())

    for i, quality in enumerate(qualities, 1):
        fmt_data = info["video_formats"][quality]
        formats = fmt_data["formats"]
        actual_heights = fmt_data["actual_heights"]

        best = None
        for f in formats:
            if f["ext"] == "mp4":
                best = f
                break
        if not best:
            best = formats[0]

        ext = best["ext"].upper()
        fps_info = f" @{best['fps']}fps" if best["fps"] and best["fps"] > 30 else ""
        size_mb = best["filesize"] / (1024 * 1024) if best["filesize"] else 0
        size_str = f" (~{size_mb:.1f} MB)" if size_mb > 0 else ""

        actual_info = ""
        if len(actual_heights) == 1:
            actual = list(actual_heights)[0]
            if actual != parse_quality_key(quality)[0]:
                actual_info = f" [actual: {actual}p]"

        print(f"  {i:2d}. {quality:>8} {ext}{fps_info}{size_str}{actual_info}")

    # Combined formats option
    print("\n📦 Combined (Video + Audio) Options:")
    print("-" * 60)
    combined_seen = set()
    combined_list = []
    for fmt in info["combined_formats"]:
        h = fmt["height"]
        if h and h > 0:
            std_h = map_to_standard_quality(h)
            if std_h not in combined_seen:
                combined_seen.add(std_h)
                combined_list.append(std_h)

    combined_list = sorted(combined_list)

    for idx, h in enumerate(combined_list, 1):
        print(f"  {len(qualities) + idx:2d}. {h}p (Combined - No merging needed)")

    # Audio options with quality and size
    print("\n🎵 Audio Only Options:")
    print("-" * 60)

    audio_list = info["audio_formats"]
    audio_choices = []

    for idx, af in enumerate(audio_list, 1):
        abr = af["abr"]
        ext = af["ext"].upper()
        size_mb = af["filesize"] / (1024 * 1024) if af["filesize"] else 0
        size_str = f" (~{size_mb:.1f} MB)" if size_mb > 0 else ""
        codec = af["acodec"].split('.')[0] if af["acodec"] != "unknown" else "Unknown"

        print(f"  {len(qualities) + len(combined_list) + idx:2d}. {int(abr) if abr else '?'}kbps {ext} {codec}{size_str}")
        audio_choices.append(af)

    # MP3 conversion option with estimated size
    mp3_idx = len(qualities) + len(combined_list) + len(audio_choices) + 1
    duration = info.get("duration", 0)
    mp3_size = estimate_mp3_size(duration, bitrate=192)
    mp3_size_str = f" (~{mp3_size:.1f} MB)" if mp3_size else ""
    print(f"  {mp3_idx:2d}. Convert to MP3 (192kbps){mp3_size_str}")

    print("\n" + "=" * 60)
    return qualities, combined_list, audio_choices

def download_video(url, choice, qualities, combined_list, audio_choices, info):
    """Download video with selected quality"""

    total_qualities = len(qualities)
    combined_count = len(combined_list)
    audio_count = len(audio_choices)

    # Original audio format choices
    if choice > total_qualities + combined_count and choice <= total_qualities + combined_count + audio_count:
        audio_idx = choice - total_qualities - combined_count - 1
        selected_audio = audio_choices[audio_idx]
        audio_format_id = selected_audio["format_id"]
        ext = selected_audio["ext"]

        ydl_opts = {
            "outtmpl": "%(title)s.%(ext)s",
            "format": audio_format_id,
            "noplaylist": True,
        }
        print(f"\n🎵 Downloading Audio ({selected_audio['abr']}kbps {ext.upper()})...")

    elif choice == total_qualities + combined_count + audio_count + 1:
        # MP3 conversion
        ydl_opts = {
            "outtmpl": "%(title)s.%(ext)s",
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "noplaylist": True,
        }
        print("\n🎵 Downloading & Converting to MP3 (192kbps)...")

    elif choice > total_qualities:
        # Combined format
        combined_idx = choice - total_qualities - 1
        target_height = combined_list[combined_idx]

        best_combined = None
        for fmt in info["combined_formats"]:
            if map_to_standard_quality(fmt["height"]) == target_height:
                if not best_combined or fmt["format_id"] > best_combined["format_id"]:
                    best_combined = fmt

        ydl_opts = {
            "outtmpl": "%(title)s.%(ext)s",
            "format": best_combined["format_id"] if best_combined else "best",
            "noplaylist": True,
        }
        print(f"\n📥 Downloading {target_height}p (Combined)...")

    else:
        # Video + Audio merge
        quality = qualities[choice - 1]
        fmt_data = info["video_formats"][quality]
        video_formats = fmt_data["formats"]

        best_video = None
        for vf in video_formats:
            if vf["ext"] == "mp4":
                best_video = vf
                break
        if not best_video:
            best_video = video_formats[0]

        video_format_id = best_video["format_id"]

        ydl_opts = {
            "outtmpl": "%(title)s.%(ext)s",
            "format": f"{video_format_id}+bestaudio/best",
            "merge_output_format": "mp4",
            "noplaylist": True,
        }
        print(f"\n📥 Downloading {quality} (Video + Audio Merge)...")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        print("\n✅ Download Completed Successfully!")
    except KeyboardInterrupt:
        print("\n\n⚠️ Download cancelled by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Download Error: {e}")
        print("💡 Tip: Make sure you have ffmpeg installed for merging video+audio")

def main():
    if len(sys.argv) != 2:
        print("Usage:")
        print("  python ytdl.py <youtube_url>")
        print("\nExample:")
        print("  python ytdl.py https://youtube.com/watch?v=xxxxx")
        sys.exit(1)

    url = sys.argv[1]

    try:
        print("🔍 Fetching available formats...")
        info = get_available_formats(url)

        if not info:
            sys.exit(1)

        qualities, combined_list, audio_choices = display_formats(info)

        total_options = len(qualities) + len(combined_list) + len(audio_choices) + 1

        while True:
            try:
                choice = input(f"\n👉 Enter your choice (1-{total_options}): ").strip()
                choice = int(choice)

                if 1 <= choice <= total_options:
                    break
                else:
                    print(f"❌ Please enter a number between 1 and {total_options}")
            except ValueError:
                print("❌ Please enter a valid number")
            except KeyboardInterrupt:
                print("\n\n👋 Goodbye!")
                sys.exit(0)

        download_video(url, choice, qualities, combined_list, audio_choices, info)

    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!")
        sys.exit(0)

if __name__ == "__main__":
    main()
