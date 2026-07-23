# Copyright (c) 2025 TheHamkerAlone
# Licensed under the MIT License.

import os
import re

import aiofiles
import aiohttp
import unicodedata

# Maps common "small caps" style unicode letters (from fancy-name/font
# generators popular for bot/channel names) back to plain ASCII, since
# the thumbnail's font has no glyphs for them and would render boxes.
_SMALL_CAPS_MAP = {
    "ᴀ": "a", "ʙ": "b", "ᴄ": "c", "ᴅ": "d", "ᴇ": "e", "ꜰ": "f", "ɢ": "g",
    "ʜ": "h", "ɪ": "i", "ᴊ": "j", "ᴋ": "k", "ʟ": "l", "ᴍ": "m", "ɴ": "n",
    "ᴏ": "o", "ᴘ": "p", "ǫ": "q", "ʀ": "r", "ᴛ": "t", "ᴜ": "u", "ᴠ": "v",
    "ᴡ": "w", "ʏ": "y", "ᴢ": "z",
}

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

CANVAS_SIZE = (1280, 720)
FRAME_RECT = (374, 118, 906, 610)
ART_RECT = (390, 134, 890, 436)
INFO_RECT = (374, 436, 906, 610)
AVATAR_SIZE = 96
AVATAR_POS = (INFO_RECT[0] + 22, INFO_RECT[1] + 22)
TEXT_X = AVATAR_POS[0] + AVATAR_SIZE + 22
TEXT_AREA_WIDTH = INFO_RECT[2] - TEXT_X - 24

# Handles both quoted and unquoted href, matching how different
# Pyrogram/Kurigram versions render User.mention in HTML parse mode.
MENTION_RE = re.compile(r'<a\s+href=["\']?tg://user\?id=(\d+)["\']?\s*>([^<]*)</a>')
MD_MENTION_RE = re.compile(r"\[([^\]]+)\]\(tg://user\?id=(\d+)\)")


class Thumbnail:
    def __init__(self):
        base = "auro/helpers"
        # Poppins-ExtraBold is the only bundled font with Devanagari
        # coverage, so it's used for every text element (with raqm
        # layout for correct conjunct/matra shaping) instead of mixing
        # in Latin-only fonts that would render Hindi as tofu boxes.
        self.title_font_path = f"{base}/Poppins-ExtraBold.ttf"

        self.font_title = ImageFont.truetype(
            self.title_font_path, 38, layout_engine=ImageFont.Layout.RAQM
        )
        self.font_meta = ImageFont.truetype(
            self.title_font_path, 23, layout_engine=ImageFont.Layout.RAQM
        )
        self.font_brand = ImageFont.truetype(
            self.title_font_path, 19, layout_engine=ImageFont.Layout.RAQM
        )

        self._session: aiohttp.ClientSession | None = None

    # ---------- lifecycle ----------

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ---------- helpers ----------

    def fit_image(self, image, size):
        return ImageOps.fit(
            image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5)
        )

    def contain_image(self, image, size):
        return ImageOps.contain(image, size, Image.Resampling.LANCZOS)

    def add_round_corners(self, image, radius):
        rounded = image.convert("RGBA")
        mask = Image.new("L", rounded.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, rounded.size[0], rounded.size[1]), radius=radius, fill=255
        )
        output = Image.new("RGBA", rounded.size, (0, 0, 0, 0))
        output.paste(rounded, (0, 0), mask)
        return output

    def clean_text(self, text: str) -> str:
        """Light cleanup only — strips control/whitespace noise but
        keeps non-Latin scripts (Hindi, etc.) intact for rendering.
        Also normalizes 'fancy' stylized unicode (mathematical bold/italic,
        fullwidth, circled letters, etc. from name generators) back to
        plain characters, since the thumbnail font has no glyphs for them
        and renders them as boxes."""
        if not text:
            return ""
        text = unicodedata.normalize("NFKC", text)
        text = "".join(_SMALL_CAPS_MAP.get(ch, ch) for ch in text)
        text = re.sub(r"[\r\n\t]+", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def truncate_text(self, draw, text, font, max_width):
        text = self.clean_text(text) or "Unknown"
        if draw.textlength(text, font=font) <= max_width:
            return text
        ellipsis = "..."
        while text and draw.textlength(f"{text}{ellipsis}", font=font) > max_width:
            text = text[:-1]
        return f"{text.rstrip()}{ellipsis}" if text else ellipsis

    def format_views(self, views) -> str:
        """Formats a raw view count into a short YouTube-style string,
        e.g. 1234 -> '1.2K', 1100000000 -> '1.1B'."""
        try:
            views = int(views)
        except (TypeError, ValueError):
            return "N/A"
        if views < 0:
            return "N/A"
        for divisor, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
            if views >= divisor:
                value = views / divisor
                text = f"{value:.1f}".rstrip("0").rstrip(".")
                return f"{text}{suffix}"
        return str(views)

    def parse_mention(self, raw: str):
        """Returns (display_name, user_id | None) from a Pyrogram mention
        string (HTML or Markdown), or from a plain name string."""
        if not raw:
            return "Someone", None
        match = MENTION_RE.search(raw)
        if match:
            name = match.group(2) or "Someone"
            return name, int(match.group(1))
        match = MD_MENTION_RE.search(raw)
        if match:
            return match.group(1), int(match.group(2))
        # Not a recognizable mention format — treat as a plain display name
        # rather than dumping raw markup on the thumbnail.
        if "<a href" in raw or raw.startswith("["):
            return "Someone", None
        return raw, None

    def add_shadow(self, rect, radius, blur, offset=(0, 16), opacity=140):
        shadow = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
        draw = ImageDraw.Draw(shadow)
        x1, y1, x2, y2 = rect
        ox, oy = offset
        draw.rounded_rectangle(
            (x1 + ox, y1 + oy, x2 + ox, y2 + oy), radius=radius, fill=(0, 0, 0, opacity)
        )
        return shadow.filter(ImageFilter.GaussianBlur(blur))

    async def download_file(self, url: str, path: str) -> bool:
        try:
            session = self._get_session()
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return False
                async with aiofiles.open(path, mode="wb") as f:
                    await f.write(await resp.read())
            return True
        except Exception:
            return False

    async def fetch_avatar(self, user_id: int, path: str):
        """Best-effort download of a Telegram user's profile photo."""
        from auro import app

        try:
            async for photo in app.get_chat_photos(user_id, limit=1):
                return await app.download_media(photo.file_id, file_name=path)
        except Exception:
            return None
        return None

    # ---------- background (vivid ambient glow built from the artwork) ----------

    def build_background(self, artwork: Image.Image) -> Image.Image:
        # Zoom in before blurring so the artwork's own colours bleed all
        # the way out to the canvas edges instead of leaving a flat,
        # washed-out ring — this is what gives the "glow" its punch.
        zoomed = self.fit_image(artwork, (int(CANVAS_SIZE[0] * 1.35), int(CANVAS_SIZE[1] * 1.35)))
        left = (zoomed.width - CANVAS_SIZE[0]) // 2
        top = (zoomed.height - CANVAS_SIZE[1]) // 2
        bg = zoomed.crop((left, top, left + CANVAS_SIZE[0], top + CANVAS_SIZE[1])).convert("RGBA")

        bg = bg.filter(ImageFilter.GaussianBlur(70))
        bg = ImageEnhance.Color(bg).enhance(1.75)
        bg = ImageEnhance.Brightness(bg).enhance(0.85)
        bg = ImageEnhance.Contrast(bg).enhance(1.1)

        canvas = Image.new("RGBA", CANVAS_SIZE, (6, 4, 4, 255))
        canvas.alpha_composite(bg)

        # Soft vignette so corners stay moody while the centre glows.
        vignette = Image.new("L", CANVAS_SIZE, 0)
        vdraw = ImageDraw.Draw(vignette)
        vdraw.ellipse((-260, -220, CANVAS_SIZE[0] + 260, CANVAS_SIZE[1] + 220), fill=255)
        vignette = vignette.filter(ImageFilter.GaussianBlur(180))
        dark = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 150))
        canvas = Image.composite(canvas, Image.alpha_composite(canvas, dark), vignette)
        return canvas

    # ---------- main entry ----------

    async def get_thumb(
        self,
        videoid: str,
        title: str,
        channel: str,
        views,
        user_id,
        thumb_url: str,
        app_name: str,
    ) -> str:
        os.makedirs("cache", exist_ok=True)
        final_path = f"cache/{videoid}.png"
        raw_thumb_path = f"cache/thumb_{videoid}.png"
        avatar_path = f"cache/avatar_{videoid}.jpg"

        if os.path.isfile(final_path):
            return final_path

        title = title or "Unknown Title"
        bot_name = app_name or "Bot"
        powered_text = "Powered by - RAJA-BABU"

        ok = bool(thumb_url) and await self.download_file(thumb_url, raw_thumb_path)
        if not ok:
            return thumb_url or ""

        try:
            youtube = Image.open(raw_thumb_path).convert("RGBA")

            canvas = self.build_background(youtube)
            canvas.alpha_composite(self.add_shadow(FRAME_RECT, radius=36, blur=30))

            # --- floating card frame ---
            frame_layer = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
            frame_draw = ImageDraw.Draw(frame_layer)
            frame_draw.rounded_rectangle(
                FRAME_RECT,
                radius=36,
                fill=(255, 255, 255, 40),
                outline=(255, 220, 180, 160),
                width=2,
            )
            canvas.alpha_composite(frame_layer)

            # --- artwork, shown in full (no cropping), letterboxed ---
            art_w, art_h = ART_RECT[2] - ART_RECT[0], ART_RECT[3] - ART_RECT[1]
            art_letterbox = self.fit_image(youtube, (art_w, art_h))
            art_letterbox = art_letterbox.filter(ImageFilter.GaussianBlur(14))
            art_letterbox = ImageEnhance.Brightness(art_letterbox).enhance(0.55)
            art_box = Image.new("RGBA", (art_w, art_h), (0, 0, 0, 255))
            art_box.alpha_composite(art_letterbox)

            contained = self.contain_image(youtube, (art_w, art_h)).convert("RGBA")
            cx = (art_w - contained.width) // 2
            cy = (art_h - contained.height) // 2
            art_box.alpha_composite(contained, (cx, cy))
            art_box = self.add_round_corners(art_box, 26)
            canvas.alpha_composite(art_box, (ART_RECT[0], ART_RECT[1]))

            # --- info panel (glass effect) ---
            info_layer = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
            info_draw = ImageDraw.Draw(info_layer)
            info_draw.rounded_rectangle(
                INFO_RECT, radius=26, fill=(30, 26, 26, 168), outline=(255, 255, 255, 46), width=1
            )
            canvas.alpha_composite(info_layer)

            # --- avatar ---
            avatar_source = None
            if user_id:
                downloaded = await self.fetch_avatar(user_id, avatar_path)
                if downloaded and os.path.isfile(downloaded):
                    avatar_source = Image.open(downloaded).convert("RGBA")
            if avatar_source is None:
                avatar_source = youtube
            avatar = self.add_round_corners(
                self.fit_image(avatar_source, (AVATAR_SIZE, AVATAR_SIZE)), 20
            )
            canvas.alpha_composite(avatar, AVATAR_POS)

            # --- text ---
            draw = ImageDraw.Draw(canvas)
            safe_title = self.truncate_text(draw, title, self.font_title, TEXT_AREA_WIDTH)
            safe_bot_name = self.truncate_text(draw, bot_name, self.font_meta, TEXT_AREA_WIDTH)
            safe_powered = self.truncate_text(draw, powered_text, self.font_brand, TEXT_AREA_WIDTH)

            draw.text((TEXT_X, INFO_RECT[1] + 18), safe_title, fill="white", font=self.font_title)
            draw.text(
                (TEXT_X, INFO_RECT[1] + 68), safe_bot_name, fill=(235, 235, 235), font=self.font_meta
            )
            draw.text(
                (TEXT_X, INFO_RECT[1] + 104), safe_powered, fill=(170, 165, 165), font=self.font_brand
            )

            canvas.convert("RGB").save(final_path, quality=95)
            return final_path
        except Exception:
            return thumb_url or ""
        finally:
            for p in (raw_thumb_path, avatar_path):
                if os.path.isfile(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass

    async def generate(self, media) -> str:
        """Entry point used by anony.core.calls.TgCall.play_media:
        `await thumb.generate(media)`, where `media` is a `Track`."""
        from auro import app

        videoid = getattr(media, "id", None) or "thumb"
        title = getattr(media, "title", None)
        thumb_url = getattr(media, "thumbnail", None)
        app_name = getattr(app, "name", None) or "Bot"

        # user_id is only used for the requester's avatar; channel/views
        # come straight from the track's own metadata.
        _, user_id = self.parse_mention(getattr(media, "user", None))
        channel = getattr(media, "channel", None)
        views = getattr(media, "views", None) or getattr(media, "view_count", None)

        return await self.get_thumb(
            str(videoid), title, channel, views, user_id, thumb_url, app_name
        )
