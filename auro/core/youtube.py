import os
import re
import yt_dlp
import random
import asyncio
import aiohttp
from pathlib import Path

from py_yt import Playlist, VideosSearch

from auro import config, logger
from auro.helpers import FallenApi, Track, utils


class YouTube:
    def __init__(self):
        self.api = None
        self.base = "https://www.youtube.com/watch?v="
        self.cookies = []
        self.checked = False
        self.cookie_dir = "auro/cookies"
        self.warned = False
        self.regex = re.compile(
            r"(https?://)?(www\.|m\.|music\.)?"
            r"(youtube\.com/(watch\?v=|shorts/|playlist\?list=)|youtu\.be/)"
            r"([A-Za-z0-9_-]{11}|PL[A-Za-z0-9_-]+)([&?][^\s]*)?"
        )
        self.iregex = re.compile(
            r"https?://(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)"
            r"(?!/(watch\?v=[A-Za-z0-9_-]{11}|shorts/[A-Za-z0-9_-]{11}"
            r"|playlist\?list=PL[A-Za-z0-9_-]+|[A-Za-z0-9_-]{11}))\S*"
        )
        if config.API_URL:
            self.api = FallenApi(config.API_URL, config.API_KEY)

    def get_cookies(self):
        if not self.checked:
            for file in os.listdir(self.cookie_dir):
                if file.endswith(".txt"):
                    self.cookies.append(f"{self.cookie_dir}/{file}")
            self.checked = True
        if not self.cookies:
            if not self.warned:
                self.warned = True
                logger.warning("Cookies are missing; downloads might fail.")
            return None
        return random.choice(self.cookies)

    async def save_cookies(self, urls: list[str]) -> None:
        logger.info("Saving cookies from urls...")
        async with aiohttp.ClientSession() as session:
            for url in urls:
                name = url.split("/")[-1]
                link = "https://batbin.me/raw/" + name
                async with session.get(link) as resp:
                    resp.raise_for_status()
                    with open(f"{self.cookie_dir}/{name}.txt", "wb") as fw:
                        fw.write(await resp.read())
        logger.info(f"Cookies saved in {self.cookie_dir}.")

    def valid(self, url: str) -> bool:
        return bool(re.match(self.regex, url))

    def invalid(self, url: str) -> bool:
        return bool(re.match(self.iregex, url))

    async def search(self, query: str, m_id: int, video: bool = False) -> Track | None:
        try:
            _search = VideosSearch(query, limit=1, with_live=False)
            results = await _search.next()
        except Exception:
            return None
        if results and results["result"]:
            data = results["result"][0]
            return Track(
                id=data.get("id"),
                channel_name=data.get("channel", {}).get("name"),
                duration=data.get("duration"),
                duration_sec=utils.to_seconds(data.get("duration")),
                message_id=m_id,
                title=data.get("title")[:25],
                thumbnail=data.get("thumbnails", [{}])[-1].get("url").split("?")[0],
                url=data.get("link"),
                view_count=data.get("viewCount", {}).get("short"),
                video=video,
            )
        return None

    async def playlist(self, limit: int, user: str, url: str, video: bool) -> list[Track | None]:
        tracks = []
        try:
            plist = await Playlist.get(url)
            for data in plist["videos"][:limit]:
                track = Track(
                    id=data.get("id"),
                    channel_name=data.get("channel", {}).get("name", ""),
                    duration=data.get("duration"),
                    duration_sec=utils.to_seconds(data.get("duration")),
                    title=data.get("title")[:25],
                    thumbnail=data.get("thumbnails")[-1].get("url").split("?")[0],
                    url=data.get("link").split("&list=")[0],
                    user=user,
                    view_count="",
                    video=video,
                )
                tracks.append(track)
        except Exception:
            pass
        return tracks

    async def related(self, video_id: str, limit: int = 10) -> list[dict]:
        """Fetch related videos using YouTube's auto-generated mix/radio playlist."""
        def _fetch():
            cookie = self.get_cookies()
            opts = {
                "extract_flat": True,
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "geo_bypass": True,
                "nocheckcertificate": True,
                "cookiefile": cookie,
                "playlistend": limit,
            }
            url = f"{self.base}{video_id}&list=RD{video_id}"
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    return (info or {}).get("entries") or []
            except Exception as ex:
                logger.warning("Autoplay: failed to fetch related videos: %s", ex)
                return []

        return await asyncio.to_thread(_fetch)

    async def autoplay_track(
        self, video_id: str, video: bool = False, exclude: set[str] = None
    ) -> Track | None:
        """Pick the next unplayed related video and return it as a Track."""
        exclude = exclude or set()
        entries = await self.related(video_id)
        for entry in entries:
            if not entry:
                continue
            entry_id = entry.get("id")
            if not entry_id or entry_id == video_id or entry_id in exclude:
                continue

            duration_sec = int(entry.get("duration") or 0)
            thumbs = entry.get("thumbnails") or []
            thumbnail = (
                thumbs[-1].get("url").split("?")[0]
                if thumbs and thumbs[-1].get("url")
                else f"https://i.ytimg.com/vi/{entry_id}/hqdefault.jpg"
            )
            return Track(
                id=entry_id,
                channel_name=entry.get("channel") or entry.get("uploader"),
                duration=utils.format_duration(duration_sec),
                duration_sec=duration_sec,
                title=(entry.get("title") or "Unknown")[:25],
                thumbnail=thumbnail,
                url=self.base + entry_id,
                user="Autoplay",
                view_count="",
                video=video,
            )
        return None

    async def download(self, video_id: str, video: bool = False) -> str | None:
        ext = "mp4" if video else "mp3"
        filename = f"downloads/{video_id}.{ext}"

        if Path(filename).exists():
            return filename

        if self.api and self.api.api_url:
            download_type = "video" if video else "audio"
            url = f"{self.api.api_url.rstrip('/')}/download?url={video_id}&type={download_type}"
            if self.api.api_key:
                url += f"&api_key={self.api.api_key}"
            return url

        cookie = self.get_cookies()
        base_opts = {
            "outtmpl": "downloads/%(id)s.%(ext)s",
            "quiet": True,
            "noplaylist": True,
            "geo_bypass": True,
            "no_warnings": True,
            "overwrites": False,
            "nocheckcertificate": True,
            "cookiefile": cookie,
            "concurrent_fragment_downloads": 4,
            "buffersize": 1024 * 1024,
            "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
        }

        if video:
            ydl_opts = {
                **base_opts,
                "format": "(bestvideo[height<=?720][width<=?1280][ext=mp4])+(bestaudio)",
                "merge_output_format": "mp4",
            }
        else:
            ydl_opts = {
                **base_opts,
                "format": "bestaudio[ext=webm][acodec=opus]/bestaudio/best",
            }

        def _download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    ydl.download([self.base + video_id])
                except (yt_dlp.utils.DownloadError, yt_dlp.utils.ExtractorError):
                    return None
                except Exception as ex:
                    logger.warning("Download failed: %s", ex)
                    return None
            return filename

        return await asyncio.to_thread(_download)
