import os
import re
import yt_dlp
import random
import asyncio
import aiohttp
from pathlib import Path

from py_yt import Playlist, VideosSearch

 # Inflex download API. Get a key from @InflexAPIBot on Telegram.
API_URL = "https://teaminflex.xyz"
API_KEY = "INFLEX20013628D"


class YouTube:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.cookies = []
        self.checked = False
        self.cookie_dir = "VampireMusic/cookies"
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
        """True only for a YouTube link that is malformed."""
        low = (url or "").lower()
        if "youtube.com" in low or "youtu.be" in low:
            return bool(re.match(self.iregex, url))
        return False

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

    async def search_multi(self, query: str, m_id: int, video: bool = False, limit: int = 5) -> list[Track]:
        try:
            _search = VideosSearch(query, limit=limit, with_live=False)
            results = await _search.next()
        except Exception:
            return []
        tracks = []
        if results and results["result"]:
            for data in results["result"]:
                tracks.append(
                    Track(
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
                )
        return tracks

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

    async def _download_audio(self, video_id: str):
        logger.info(f"🎵 [AUDIO] Starting download process for ID: {video_id}")

        path = Path(f"downloads/{video_id}.webm")
        os.makedirs("downloads", exist_ok=True)

        if path.exists():
            logger.info(f"🎵 [LOCAL] Found existing audio for ID {video_id}")
            return str(path)

        payload = {"url": video_id, "type": "audio"}
        headers = {
            "Content-Type": "application/json",
            "X-API-KEY": API_KEY,
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    f"{API_URL}/download",
                    json=payload,
                    headers=headers,
                ) as response:
                    data = await response.json(content_type=None)

                if data and data.get("status") == "error":
                    logger.error(f"[AUDIO] API ERROR → {data}")
                    return None

                retries = 10

                if not data or not data.get("download_url"):
                    logger.warning("[AUDIO] File not ready / JSON missing → retrying...")

                    for i in range(retries):
                        await asyncio.sleep(8)

                        async with session.post(
                            f"{API_URL}/download",
                            json=payload,
                            headers=headers,
                        ) as response:
                            data = await response.json(content_type=None)

                        if data and data.get("status") == "error":
                            logger.error(f"[AUDIO] API ERROR during retry → {data}")
                            return None

                        if data and data.get("status") == "success" and data.get("download_url"):
                            logger.info(f"[AUDIO] Got URL after retry #{i+1}")
                            break

                        logger.warning(f"[AUDIO] Retry {i+1}/{retries} → still not ready")

                if not data or not data.get("download_url"):
                    logger.error(f"[AUDIO] FAILED after all retries → {data}")
                    return None

                download_link = API_URL + data["download_url"]

                async with session.get(download_link) as file_response:
                    if file_response.status != 200:
                        logger.error(f"[AUDIO] Download failed → {file_response.status}")
                        return None

                    with open(path, "wb") as f:
                        async for chunk in file_response.content.iter_chunked(8192):
                            f.write(chunk)

                logger.info(f"🎵 [API] Audio download completed for {video_id}")
                return str(path)

            except Exception as e:
                logger.error(f"[AUDIO] Exception: {e}")
                return None

    async def _download_video(self, video_id: str):
        logger.info(f"🎥 [VIDEO] Starting download process for ID: {video_id}")

        path = Path(f"downloads/{video_id}.mkv")
        os.makedirs("downloads", exist_ok=True)

        if path.exists():
            logger.info(f"🎥 [LOCAL] Found existing video for ID {video_id}")
            return str(path)

        payload = {"url": video_id, "type": "video"}
        headers = {
            "Content-Type": "application/json",
            "X-API-KEY": API_KEY,
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    f"{API_URL}/download",
                    json=payload,
                    headers=headers,
                ) as response:
                    data = await response.json(content_type=None)

                if data and data.get("status") == "error":
                    logger.error(f"[VIDEO] API ERROR → {data}")
                    return None

                retries = 20

                if not data or not data.get("download_url"):
                    logger.warning("[VIDEO] File not ready / JSON missing → retrying...")

                    for i in range(retries):
                        await asyncio.sleep(20)

                        async with session.post(
                            f"{API_URL}/download",
                            json=payload,
                            headers=headers,
                        ) as response:
                            data = await response.json(content_type=None)

                        if data and data.get("status") == "error":
                            logger.error(f"[VIDEO] API ERROR during retry → {data}")
                            return None

                        if data and data.get("status") == "success" and data.get("download_url"):
                            logger.info(f"[VIDEO] Got URL after retry #{i+1}")
                            break

                        logger.warning(f"[VIDEO] Retry {i+1}/{retries} → still not ready")

                if not data or not data.get("download_url"):
                    logger.error(f"[VIDEO] FAILED after all retries → {data}")
                    return None

                download_link = API_URL + data["download_url"]

                async with session.get(download_link) as file_response:
                    if file_response.status != 200:
                        logger.error(f"[VIDEO] Download failed → {file_response.status}")
                        return None

                    with open(path, "wb") as f:
                        async for chunk in file_response.content.iter_chunked(8192):
                            f.write(chunk)

                logger.info(f"🎥 [API] Video download completed for {video_id}")
                return str(path)

            except Exception as e:
                logger.error(f"[VIDEO] Exception: {e}")
                return None

    async def download(self, video_id: str, video: bool = False):
        if video:
            return await self._download_video(video_id)
        return await self._download_audio(video_id)           
            
