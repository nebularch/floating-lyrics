#!/usr/bin/env python3

import json
import re
import html
import subprocess
import threading
import urllib.request
import urllib.parse
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gtk, Gdk, GLib


# =========================================================
# CONFIGURATION
# =========================================================

CONFIG_DIR = Path.home() / ".config" / "fast_lyrics"
CONFIG_FILE = CONFIG_DIR / "window_config.json"
CACHE_DIR = Path.home() / ".cache" / "fast_lyrics" / "lyrics"

CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

BORDER_MARGIN = 14


class MusixmatchOverlay(Gtk.Window):

    def __init__(self):
        super().__init__()

        # -----------------------------------------------------
        # Window state
        # -----------------------------------------------------

        self.win_w = 720
        self.win_h = 180
        self.win_x = None
        self.win_y = None

        self.font_size = 28
        self.bg_alpha = 0.85
        self.is_light_mode = False

        # -----------------------------------------------------
        # Lyrics state
        # -----------------------------------------------------

        self.current_track_id = None
        self.synced_lyrics = []
        self.last_sync_idx = -1

        self.current_player = None
        self.current_artist = ""
        self.current_title = ""
        self.current_file_url = ""
        self.current_position = 0.0

        self.fetch_generation = 0

        # -----------------------------------------------------
        # Load saved state
        # -----------------------------------------------------

        self.load_window_state()

        # -----------------------------------------------------
        # Window
        # -----------------------------------------------------

        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_resizable(True)
        self.set_type_hint(
            Gdk.WindowTypeHint.NOTIFICATION
        )

        self.set_default_size(
            self.win_w,
            self.win_h
        )

        self.set_size_request(
            300,
            100
        )

        # -----------------------------------------------------
        # Mouse events
        # -----------------------------------------------------

        self.add_events(
            Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.SCROLL_MASK
        )

        # -----------------------------------------------------
        # CSS
        # -----------------------------------------------------

        self.css_provider = Gtk.CssProvider()

        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            self.css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.update_css()

        # -----------------------------------------------------
        # Main layout
        # -----------------------------------------------------

        vbox = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4
        )

        vbox.set_margin_top(8)
        vbox.set_margin_bottom(12)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)

        self.add(vbox)

        # -----------------------------------------------------
        # Header
        # -----------------------------------------------------

        hdr = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL
        )

        self.size_badge = Gtk.Label(
            label=f"{self.win_w} × {self.win_h}"
        )

        self.size_badge.get_style_context().add_class(
            "size-badge"
        )

        self.size_badge.set_no_show_all(True)
        self.size_badge.hide()

        hdr.pack_start(
            self.size_badge,
            False,
            False,
            0
        )

        self.close_btn = Gtk.Button(
            label="✕"
        )

        self.close_btn.get_style_context().add_class(
            "close-btn"
        )

        self.close_btn.set_relief(
            Gtk.ReliefStyle.NONE
        )

        self.close_btn.set_no_show_all(True)
        self.close_btn.hide()

        self.close_btn.connect(
            "clicked",
            self.on_close_clicked
        )

        hdr.pack_end(
            self.close_btn,
            False,
            False,
            0
        )

        vbox.pack_start(
            hdr,
            False,
            False,
            0
        )

        # -----------------------------------------------------
        # Lyrics
        # -----------------------------------------------------

        self.prev_lbl = Gtk.Label(
            label=""
        )

        self.curr_lbl = Gtk.Label(
            label="Waiting for media player..."
        )

        self.next_lbl = Gtk.Label(
            label=""
        )

        for lbl, css_class in [
            (self.prev_lbl, "lyric-faded"),
            (self.curr_lbl, "lyric-now-playing"),
            (self.next_lbl, "lyric-faded")
        ]:

            lbl.get_style_context().add_class(
                css_class
            )

            lbl.set_line_wrap(True)

            lbl.set_justify(
                Gtk.Justification.CENTER
            )

            vbox.pack_start(
                lbl,
                True,
                True,
                0
            )

        # -----------------------------------------------------
        # Signals
        # -----------------------------------------------------

        self.connect(
            "size-allocate",
            self.on_size_allocate
        )

        self.connect(
            "enter-notify-event",
            self.on_mouse_enter
        )

        self.connect(
            "leave-notify-event",
            self.on_mouse_leave
        )

        self.connect(
            "motion-notify-event",
            self.on_mouse_motion
        )

        self.connect(
            "button-press-event",
            self.on_button_press
        )

        self.connect(
            "scroll-event",
            self.on_scroll
        )

        self.connect(
            "configure-event",
            self.on_configure_event
        )

        self.connect(
            "delete-event",
            self.on_delete_event
        )

        # -----------------------------------------------------
        # Timers
        # -----------------------------------------------------

        GLib.timeout_add(
            500,
            self.check_background_luminance
        )

        GLib.timeout_add(
            200,
            self.update_lyrics_loop
        )

    # =========================================================
    # PLAYERCTL
    # =========================================================

    def get_player_list(self):

        try:

            result = subprocess.run(
                ["playerctl", "-l"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2
            )

            if result.returncode != 0:
                return []

            return [
                line.strip()
                for line in result.stdout.splitlines()
                if line.strip()
            ]

        except Exception:
            return []

    def get_single_player_info(
        self,
        player
    ):

        metadata_format = (
            "{{status}}|||"
            "{{artist}}|||"
            "{{title}}|||"
            "{{position}}|||"
            "{{xesam:url}}|||"
            "{{mpris:trackid}}"
        )

        try:

            result = subprocess.run(
                [
                    "playerctl",
                    "-p",
                    player,
                    "metadata",
                    "--format",
                    metadata_format
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2
            )

            if result.returncode != 0:
                return None

            output = result.stdout.strip()

            if not output:
                return None

            parts = output.split("|||")

            while len(parts) < 6:
                parts.append("")

            status = parts[0].strip()

            artist = html.unescape(
                parts[1].strip()
            )

            title = html.unescape(
                parts[2].strip()
            )

            # -------------------------------------------------
            # IMPORTANT
            #
            # Different playerctl/MPRIS implementations can
            # expose position differently.
            #
            # Handle:
            #   13
            #   13.42
            #   13000000
            # -------------------------------------------------

            raw_position = parts[3].strip()

            try:

                position = float(
                    raw_position
                )

                # If a value is extremely large,
                # it is almost certainly microseconds.

                if position > 100000:

                    position /= 1000000.0

            except Exception:

                position = 0.0

            file_url = parts[4].strip()
            track_id = parts[5].strip()

            return {
                "player": player,
                "status": status,
                "artist": artist,
                "title": title,
                "position": position,
                "url": file_url,
                "track_id": track_id
            }

        except Exception:
            return None

    def clean_media_metadata(
        self,
        artist,
        title,
        file_url="",
        player_name=""
    ):

        artist = html.unescape(
            str(artist or "")
        ).strip()

        title = html.unescape(
            str(title or "")
        ).strip()

        file_url = str(file_url or "")
        player_name = str(player_name or "")

        player_lower = player_name.lower()
        url_lower = file_url.lower()

        is_browser = any(
            browser in player_lower
            for browser in (
                "firefox",
                "chrome",
                "chromium",
                "brave",
                "edge",
                "vivaldi",
                "opera"
            )
        )

        is_youtube = (
            "youtube.com" in url_lower
            or "youtu.be" in url_lower
        )

        # -----------------------------------------------------
        # Clean common YouTube suffixes
        # -----------------------------------------------------

        suffix_patterns = [

            r"\s*[\(\[]\s*official\s+music\s+video\s*[\)\]]\s*$",

            r"\s*[\(\[]\s*official\s+video\s*[\)\]]\s*$",

            r"\s*[\(\[]\s*official\s+audio\s*[\)\]]\s*$",

            r"\s*[\(\[]\s*official\s*[\)\]]\s*$",

            r"\s*[\(\[]\s*music\s+video\s*[\)\]]\s*$",

            r"\s*[\(\[]\s*lyric\s+video\s*[\)\]]\s*$",

            r"\s*[\(\[]\s*lyrics\s*[\)\]]\s*$",

            r"\s*[\(\[]\s*audio\s*[\)\]]\s*$",

            r"\s*[\(\[]\s*hd\s*[\)\]]\s*$",

            r"\s*[\(\[]\s*4k\s*[\)\]]\s*$",

            r"\s*[\(\[]\s*remastered\s*[\)\]]\s*$",

            r"\s+official\s+music\s+video$",

            r"\s+official\s+video$",

            r"\s+official\s+audio$",

            r"\s+music\s+video$",

            r"\s+lyric\s+video$",

            r"\s+lyrics$",

            r"\s+remastered$"
        ]

        cleaned_title = title

        for pattern in suffix_patterns:

            cleaned_title = re.sub(
                pattern,
                "",
                cleaned_title,
                flags=re.IGNORECASE
            )

        cleaned_title = re.sub(
            r"\s+",
            " ",
            cleaned_title
        ).strip()

        cleaned_title = cleaned_title.strip(
            " -–—"
        )

        cleaned_artist = artist.strip()

        # -----------------------------------------------------
        # Browser / YouTube title:
        #
        # ARTIST - SONG
        # -----------------------------------------------------

        split_match = re.match(
            r"^\s*(.+?)\s+-\s+(.+?)\s*$",
            cleaned_title
        )

        if split_match:

            possible_artist = (
                split_match.group(1).strip()
            )

            possible_title = (
                split_match.group(2).strip()
            )

            if (
                is_browser
                or is_youtube
            ):

                if (
                    possible_artist
                    and possible_title
                ):

                    cleaned_artist = (
                        possible_artist
                    )

                    cleaned_title = (
                        possible_title
                    )

            elif not cleaned_artist:

                cleaned_artist = (
                    possible_artist
                )

                cleaned_title = (
                    possible_title
                )

        # -----------------------------------------------------
        # Remove duplicate artist prefix
        # -----------------------------------------------------

        if cleaned_artist and cleaned_title:

            prefix = (
                cleaned_artist
                + " - "
            )

            if cleaned_title.lower().startswith(
                prefix.lower()
            ):

                cleaned_title = (
                    cleaned_title[
                        len(prefix):
                    ].strip()
                )

        return (
            cleaned_artist,
            cleaned_title
        )

    def get_playerctl_info(self):

        players = self.get_player_list()

        if not players:

            return (
                None,
                None,
                0.0,
                "",
                "",
                ""
            )

        infos = []

        for player in players:

            info = self.get_single_player_info(
                player
            )

            if info:
                infos.append(info)

        if not infos:

            return (
                None,
                None,
                0.0,
                "",
                "",
                ""
            )

        selected = None

        # -----------------------------------------------------
        # Playing first
        # -----------------------------------------------------

        for info in infos:

            if (
                info["status"].lower()
                == "playing"
            ):

                selected = info
                break

        # -----------------------------------------------------
        # Paused second
        # -----------------------------------------------------

        if selected is None:

            for info in infos:

                if (
                    info["status"].lower()
                    == "paused"
                ):

                    selected = info
                    break

        # -----------------------------------------------------
        # Fallback
        # -----------------------------------------------------

        if selected is None:
            selected = infos[0]

        artist, title = (
            self.clean_media_metadata(
                selected["artist"],
                selected["title"],
                selected["url"],
                selected["player"]
            )
        )

        return (
            artist,
            title,
            selected["position"],
            selected["url"],
            selected["player"],
            selected["track_id"]
        )

    # =========================================================
    # LRC PARSER
    # =========================================================

    def parse_lrc(
        self,
        lrc_text
    ):

        lyrics = []

        if not lrc_text:
            return lyrics

        timestamp_pattern = re.compile(
            r"\[(\d+):(\d+(?:\.\d+)?)\]"
        )

        for line in lrc_text.splitlines():

            matches = list(
                timestamp_pattern.finditer(
                    line
                )
            )

            if not matches:
                continue

            text = timestamp_pattern.sub(
                "",
                line
            ).strip()

            text = html.unescape(
                text
            )

            if not text:
                continue

            for match in matches:

                minutes = int(
                    match.group(1)
                )

                seconds = float(
                    match.group(2)
                )

                timestamp = (
                    minutes * 60
                    + seconds
                )

                lyrics.append(
                    (
                        timestamp,
                        text
                    )
                )

        return sorted(
            lyrics,
            key=lambda x: x[0]
        )

    # =========================================================
    # LOCAL LRC
    # =========================================================

    def find_local_lrc(
        self,
        file_url,
        artist,
        title
    ):

        paths = []

        if (
            file_url
            and file_url.startswith(
                "file://"
            )
        ):

            try:

                parsed_path = (
                    urllib.parse.unquote(
                        urllib.parse.urlparse(
                            file_url
                        ).path
                    )
                )

                audio_path = Path(
                    parsed_path
                )

                if audio_path.exists():

                    paths.append(
                        audio_path.with_suffix(
                            ".lrc"
                        )
                    )

                    paths.append(
                        audio_path.parent
                        / f"{artist} - {title}.lrc"
                    )

                    paths.append(
                        audio_path.parent
                        / f"{title}.lrc"
                    )

            except Exception:
                pass

        safe_name = re.sub(
            r'[\\/*?:"<>|]',
            "_",
            f"{artist} - {title}.lrc"
        )

        paths.append(
            CACHE_DIR / safe_name
        )

        music_dir = (
            Path.home()
            / "Music"
        )

        paths.append(
            music_dir
            / f"{artist} - {title}.lrc"
        )

        paths.append(
            music_dir
            / f"{title}.lrc"
        )

        for path in paths:

            if path.is_file():

                try:

                    content = path.read_text(
                        encoding="utf-8",
                        errors="ignore"
                    )

                    if content.strip():
                        return content

                except Exception:
                    pass

        return None

    def save_local_lrc(
        self,
        synced_text,
        file_url,
        artist,
        title
    ):

        if not synced_text:
            return

        saved = False

        if (
            file_url
            and file_url.startswith(
                "file://"
            )
        ):

            try:

                parsed_path = (
                    urllib.parse.unquote(
                        urllib.parse.urlparse(
                            file_url
                        ).path
                    )
                )

                audio_path = Path(
                    parsed_path
                )

                if (
                    audio_path.exists()
                    and audio_path.parent.is_dir()
                ):

                    audio_path.with_suffix(
                        ".lrc"
                    ).write_text(
                        synced_text,
                        encoding="utf-8"
                    )

                    saved = True

            except Exception:
                pass

        if not saved:

            try:

                safe_name = re.sub(
                    r'[\\/*?:"<>|]',
                    "_",
                    f"{artist} - {title}.lrc"
                )

                (
                    CACHE_DIR / safe_name
                ).write_text(
                    synced_text,
                    encoding="utf-8"
                )

            except Exception as e:

                print(
                    f"Error caching LRC: {e}"
                )

    # =========================================================
    # LRCLIB
    # =========================================================

    def lrclib_request(
        self,
        url
    ):

        try:

            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent":
                        "FastLyrics/1.0",
                    "Accept":
                        "application/json"
                }
            )

            with urllib.request.urlopen(
                request,
                timeout=8
            ) as response:

                if response.status != 200:
                    return None

                return json.loads(
                    response.read().decode(
                        "utf-8",
                        errors="replace"
                    )
                )

        except Exception as e:

            print(
                f"LRCLIB request failed: {e}"
            )

            return None

    def normalize_compare(
        self,
        text
    ):

        text = html.unescape(
            str(text or "")
        ).lower()

        text = re.sub(
            r"[^\w\s]",
            "",
            text,
            flags=re.UNICODE
        )

        text = re.sub(
            r"\s+",
            " ",
            text
        )

        return text.strip()

    def lrclib_exact(
        self,
        artist,
        title
    ):

        try:

            query = urllib.parse.urlencode(
                {
                    "artist_name": artist,
                    "track_name": title
                }
            )

            url = (
                "https://lrclib.net/api/get?"
                + query
            )

            print(
                "Trying LRCLIB exact lookup..."
            )

            data = self.lrclib_request(
                url
            )

            if (
                isinstance(data, dict)
                and data.get("syncedLyrics")
            ):

                print(
                    "Lyrics source: LRCLIB exact"
                )

                return data["syncedLyrics"]

        except Exception:
            pass

        return None

    def lrclib_search(
        self,
        artist,
        title
    ):

        queries = []

        if artist and title:

            queries.append(
                f"{artist} {title}"
            )

        if title:
            queries.append(title)

        target_artist = (
            self.normalize_compare(
                artist
            )
        )

        target_title = (
            self.normalize_compare(
                title
            )
        )

        for search_query in queries:

            print(
                f"Trying LRCLIB search: "
                f"{search_query}"
            )

            try:

                query = urllib.parse.urlencode(
                    {
                        "q": search_query
                    }
                )

                data = self.lrclib_request(
                    "https://lrclib.net/api/search?"
                    + query
                )

                if not isinstance(
                    data,
                    list
                ):
                    continue

                # Exact normalized match first

                for item in data:

                    if not isinstance(
                        item,
                        dict
                    ):
                        continue

                    synced = item.get(
                        "syncedLyrics"
                    )

                    if not synced:
                        continue

                    item_artist = (
                        self.normalize_compare(
                            item.get(
                                "artistName",
                                ""
                            )
                        )
                    )

                    item_title = (
                        self.normalize_compare(
                            item.get(
                                "trackName",
                                ""
                            )
                        )
                    )

                    if (
                        item_artist
                        == target_artist
                        and
                        item_title
                        == target_title
                    ):

                        print(
                            "Lyrics source: "
                            "LRCLIB search exact match"
                        )

                        return synced

                # Any synced result

                for item in data:

                    if not isinstance(
                        item,
                        dict
                    ):
                        continue

                    synced = item.get(
                        "syncedLyrics"
                    )

                    if synced:

                        print(
                            "Lyrics source: "
                            "LRCLIB search"
                        )

                        return synced

            except Exception:
                continue

        return None

    # =========================================================
    # LYRIC FETCH
    # =========================================================

    def fetch_lyrics(
        self,
        artist,
        title,
        file_url,
        generation
    ):

        local_content = (
            self.find_local_lrc(
                file_url,
                artist,
                title
            )
        )

        if local_content:

            parsed = self.parse_lrc(
                local_content
            )

            if parsed:

                GLib.idle_add(
                    self.apply_lyrics_result,
                    generation,
                    parsed,
                    "local"
                )

                return

        synced = self.lrclib_exact(
            artist,
            title
        )

        if synced:

            parsed = self.parse_lrc(
                synced
            )

            if parsed:

                self.save_local_lrc(
                    synced,
                    file_url,
                    artist,
                    title
                )

                GLib.idle_add(
                    self.apply_lyrics_result,
                    generation,
                    parsed,
                    "lrclib"
                )

                return

        synced = self.lrclib_search(
            artist,
            title
        )

        if synced:

            parsed = self.parse_lrc(
                synced
            )

            if parsed:

                self.save_local_lrc(
                    synced,
                    file_url,
                    artist,
                    title
                )

                GLib.idle_add(
                    self.apply_lyrics_result,
                    generation,
                    parsed,
                    "lrclib-search"
                )

                return

        GLib.idle_add(
            self.apply_lyrics_result,
            generation,
            [],
            "none"
        )

    def apply_lyrics_result(
        self,
        generation,
        lyrics,
        source
    ):

        if generation != self.fetch_generation:
            return False

        self.synced_lyrics = (
            lyrics or []
        )

        self.last_sync_idx = -1

        if self.synced_lyrics:

            self.prev_lbl.set_text("")

            self.curr_lbl.set_text(
                self.current_title
            )

            self.next_lbl.set_text(
                self.synced_lyrics[0][1]
            )

        else:

            self.curr_lbl.set_text(
                self.current_title
            )

            self.prev_lbl.set_text(
                self.current_artist
            )

            self.next_lbl.set_text(
                "(No synced lyrics found)"
            )

        return False

    # =========================================================
    # LYRIC SEEKING / SYNC
    # =========================================================

    def update_lyrics_loop(self):

        (
            artist,
            title,
            pos_sec,
            file_url,
            player_name,
            track_id
        ) = self.get_playerctl_info()

        # -----------------------------------------------------
        # No player
        # -----------------------------------------------------

        if not artist or not title:

            self.curr_lbl.set_text(
                "No active media player"
            )

            self.prev_lbl.set_text("")
            self.next_lbl.set_text("")

            self.current_track_id = None
            self.current_player = None

            return True

        # -----------------------------------------------------
        # Save current state
        # -----------------------------------------------------

        self.current_player = player_name
        self.current_artist = artist
        self.current_title = title
        self.current_file_url = file_url
        self.current_position = pos_sec

        # -----------------------------------------------------
        # Track identity
        # -----------------------------------------------------

        current_track_id = (
            f"{player_name}|"
            f"{artist}|"
            f"{title}|"
            f"{track_id}"
        )

        # -----------------------------------------------------
        # New song
        # -----------------------------------------------------

        if (
            current_track_id
            != self.current_track_id
        ):

            self.current_track_id = (
                current_track_id
            )

            self.synced_lyrics = []
            self.last_sync_idx = -1

            self.fetch_generation += 1

            generation = (
                self.fetch_generation
            )

            self.curr_lbl.set_text(
                f"Fetching: {title}..."
            )

            self.prev_lbl.set_text(
                artist
            )

            self.next_lbl.set_text("")

            print()
            print("=" * 60)
            print("NOW PLAYING")
            print("=" * 60)
            print(
                f"Player: {player_name}"
            )
            print(
                f"Artist: {artist}"
            )
            print(
                f"Title:  {title}"
            )
            print(
                f"Position: {pos_sec:.2f}s"
            )
            print(
                f"URL:    {file_url}"
            )
            print("=" * 60)
            print()

            print(
                f"Lyrics search: "
                f"{artist} - {title}"
            )

            threading.Thread(
                target=self.fetch_lyrics,
                args=(
                    artist,
                    title,
                    file_url,
                    generation
                ),
                daemon=True
            ).start()

            return True

        # -----------------------------------------------------
        # No lyrics loaded yet
        # -----------------------------------------------------

        if not self.synced_lyrics:
            return True

        # -----------------------------------------------------
        # Find active lyric
        # -----------------------------------------------------

        current_idx = -1

        for idx, (
            timestamp,
            text
        ) in enumerate(
            self.synced_lyrics
        ):

            if pos_sec >= timestamp:

                current_idx = idx

            else:

                break

        # -----------------------------------------------------
        # Before first lyric
        # -----------------------------------------------------

        if current_idx < 0:

            self.prev_lbl.set_text("")

            self.curr_lbl.set_text(
                self.current_title
            )

            self.next_lbl.set_text(
                self.synced_lyrics[0][1]
            )

            return True

        # -----------------------------------------------------
        # Update when line changes
        # -----------------------------------------------------

        if (
            current_idx
            != self.last_sync_idx
        ):

            self.last_sync_idx = (
                current_idx
            )

            prev_txt = ""
            curr_txt = ""
            next_txt = ""

            if current_idx > 0:

                prev_txt = (
                    self.synced_lyrics[
                        current_idx - 1
                    ][1]
                )

            curr_txt = (
                self.synced_lyrics[
                    current_idx
                ][1]
            )

            if (
                current_idx + 1
                < len(self.synced_lyrics)
            ):

                next_txt = (
                    self.synced_lyrics[
                        current_idx + 1
                    ][1]
                )

            self.prev_lbl.set_text(
                prev_txt
            )

            self.curr_lbl.set_text(
                curr_txt or "♪ ♪ ♪"
            )

            self.next_lbl.set_text(
                next_txt
            )

        return True

    # =========================================================
    # BACKGROUND LUMINANCE
    # =========================================================

    def check_background_luminance(self):

        root_win = (
            Gdk.get_default_root_window()
        )

        if (
            not root_win
            or self.win_x is None
            or self.win_y is None
        ):
            return True

        margin = 15

        sample_points = [

            (
                max(
                    0,
                    self.win_x - margin
                ),
                max(
                    0,
                    self.win_y
                    + self.win_h // 2
                )
            ),

            (
                self.win_x
                + self.win_w
                + margin,
                max(
                    0,
                    self.win_y
                    + self.win_h // 2
                )
            ),

            (
                max(
                    0,
                    self.win_x
                    + self.win_w // 2
                ),
                max(
                    0,
                    self.win_y - margin
                )
            ),

            (
                max(
                    0,
                    self.win_x
                    + self.win_w // 2
                ),
                self.win_y
                + self.win_h
                + margin
            )
        ]

        total_lum = 0
        samples = 0

        for sx, sy in sample_points:

            try:

                pixbuf = (
                    Gdk.pixbuf_get_from_window(
                        root_win,
                        int(sx),
                        int(sy),
                        1,
                        1
                    )
                )

                if pixbuf:

                    pixels = (
                        pixbuf.get_pixels()
                    )

                    r = pixels[0]
                    g = pixels[1]
                    b = pixels[2]

                    lum = (
                        0.2126 * r
                        + 0.7152 * g
                        + 0.0722 * b
                    )

                    total_lum += lum
                    samples += 1

            except Exception:
                pass

        if samples > 0:

            avg_lum = (
                total_lum / samples
            )

            should_be_light = (
                avg_lum > 140
            )

            if (
                should_be_light
                != self.is_light_mode
            ):

                self.is_light_mode = (
                    should_be_light
                )

                self.update_css()

        return True

    # =========================================================
    # CSS
    # =========================================================

    def update_css(self):

        main_size = self.font_size

        faded_size = max(
            10,
            int(main_size * 0.55)
        )

        alpha = self.bg_alpha

        if self.is_light_mode:

            bg = (
                f"rgba(240, 240, 245, "
                f"{alpha:.2f})"
            )

            border = (
                f"rgba(0, 0, 0, "
                f"{min(0.25, alpha * 0.15):.2f})"
            )

            badge_bg = (
                "rgba(0, 0, 0, 0.12)"
            )

            badge_fg = (
                "rgba(0, 0, 0, 0.8)"
            )

            close_fg = (
                "rgba(0, 0, 0, 0.5)"
            )

            faded_fg = (
                "rgba(0, 0, 0, 0.45)"
            )

            now_playing_fg = "#111111"

        else:

            bg = (
                f"rgba(10, 10, 14, "
                f"{alpha:.2f})"
            )

            border = (
                f"rgba(255, 255, 255, "
                f"{min(0.25, alpha * 0.15):.2f})"
            )

            badge_bg = (
                "rgba(255, 255, 255, 0.15)"
            )

            badge_fg = (
                "rgba(255, 255, 255, 0.8)"
            )

            close_fg = (
                "rgba(255, 255, 255, 0.5)"
            )

            faded_fg = (
                "rgba(255, 255, 255, 0.35)"
            )

            now_playing_fg = "#FFFFFF"

        css = f"""
        window {{
            background-color: {bg};
            border-radius: 18px;
            border: 1px solid {border};
        }}

        .size-badge {{
            background-color: {badge_bg};
            color: {badge_fg};
            font-size: 11px;
            font-weight: 600;
            border-radius: 6px;
            padding: 2px 8px;
        }}

        .close-btn {{
            color: {close_fg};
            font-size: 14px;
            border: none;
            background: transparent;
            padding: 0px 6px;
        }}

        .close-btn:hover {{
            color: #FF5555;
        }}

        .lyric-faded {{
            color: {faded_fg};
            font-size: {faded_size}px;
            font-weight: 500;
        }}

        .lyric-now-playing {{
            color: {now_playing_fg};
            font-size: {main_size}px;
            font-weight: 800;
        }}
        """.encode("utf-8")

        self.css_provider.load_from_data(
            css
        )

    # =========================================================
    # MOUSE / RESIZE
    # =========================================================

    def on_mouse_enter(
        self,
        widget,
        event
    ):

        self.size_badge.show()
        self.close_btn.show()

        return False

    def on_mouse_leave(
        self,
        widget,
        event
    ):

        if event.detail != Gdk.NotifyType.INFERIOR:

            self.size_badge.hide()
            self.close_btn.hide()

            try:

                window = self.get_window()

                if window:
                    window.set_cursor(None)

            except Exception:
                pass

        return False

    def on_scroll(
        self,
        widget,
        event
    ):

        delta = 0

        if event.direction == Gdk.ScrollDirection.UP:

            delta = 1

        elif event.direction == Gdk.ScrollDirection.DOWN:

            delta = -1

        elif event.direction == Gdk.ScrollDirection.SMOOTH:

            try:

                success, delta_x, delta_y = (
                    event.get_scroll_deltas()
                )

                if success:

                    if delta_y < 0:
                        delta = 1

                    elif delta_y > 0:
                        delta = -1

            except Exception:

                return True

        if delta == 0:
            return True

        if (
            event.state
            & Gdk.ModifierType.CONTROL_MASK
        ):

            self.bg_alpha = max(
                0.15,
                min(
                    1.0,
                    self.bg_alpha
                    + delta * 0.05
                )
            )

        else:

            self.font_size = max(
                16,
                min(
                    64,
                    self.font_size
                    + delta * 2
                )
            )

        self.update_css()
        self.save_window_state()

        return True

    def _get_border_edge(
        self,
        x,
        y,
        w,
        h
    ):

        top = (
            y <= BORDER_MARGIN
        )

        bottom = (
            y >= h - BORDER_MARGIN
        )

        left = (
            x <= BORDER_MARGIN
        )

        right = (
            x >= w - BORDER_MARGIN
        )

        if top and left:
            return Gdk.WindowEdge.NORTH_WEST

        if top and right:
            return Gdk.WindowEdge.NORTH_EAST

        if bottom and left:
            return Gdk.WindowEdge.SOUTH_WEST

        if bottom and right:
            return Gdk.WindowEdge.SOUTH_EAST

        if top:
            return Gdk.WindowEdge.NORTH

        if bottom:
            return Gdk.WindowEdge.SOUTH

        if left:
            return Gdk.WindowEdge.WEST

        if right:
            return Gdk.WindowEdge.EAST

        return None

    def on_mouse_motion(
        self,
        widget,
        event
    ):

        alloc = self.get_allocation()

        edge = self._get_border_edge(
            event.x,
            event.y,
            alloc.width,
            alloc.height
        )

        window = self.get_window()

        if not window:
            return False

        if edge is None:

            window.set_cursor(None)

            return False

        cursor_map = {

            Gdk.WindowEdge.NORTH:
                Gdk.CursorType.TOP_SIDE,

            Gdk.WindowEdge.SOUTH:
                Gdk.CursorType.BOTTOM_SIDE,

            Gdk.WindowEdge.WEST:
                Gdk.CursorType.LEFT_SIDE,

            Gdk.WindowEdge.EAST:
                Gdk.CursorType.RIGHT_SIDE,

            Gdk.WindowEdge.NORTH_WEST:
                Gdk.CursorType.TOP_LEFT_CORNER,

            Gdk.WindowEdge.NORTH_EAST:
                Gdk.CursorType.TOP_RIGHT_CORNER,

            Gdk.WindowEdge.SOUTH_WEST:
                Gdk.CursorType.BOTTOM_LEFT_CORNER,

            Gdk.WindowEdge.SOUTH_EAST:
                Gdk.CursorType.BOTTOM_RIGHT_CORNER
        }

        cursor_type = cursor_map.get(
            edge
        )

        if cursor_type is not None:

            try:

                display = (
                    Gdk.Display.get_default()
                )

                cursor = (
                    Gdk.Cursor.new_for_display(
                        display,
                        cursor_type
                    )
                )

                window.set_cursor(
                    cursor
                )

            except Exception:
                pass

        return False

    def on_button_press(self, widget, event):

        if event.button != 1:
            return False

        # -----------------------------------------------------
        # Do not drag controls
        # -----------------------------------------------------

        if widget == self.close_btn or widget == self.size_badge:
            return False

        alloc = self.get_allocation()

        edge = self._get_border_edge(
            event.x,
            event.y,
            alloc.width,
            alloc.height
        )

        # -----------------------------------------------------
        # Resize
        # -----------------------------------------------------

        if edge is not None:
            try:
                gdk_window = self.get_window()

                if gdk_window is None:
                    return False

                gdk_window.begin_resize_drag(
                    edge,
                    int(event.button),
                    int(event.x_root),
                    int(event.y_root),
                    event.time
                )

                return True

            except Exception as e:
                print(f"Resize error: {e}")
                return False

        # -----------------------------------------------------
        # Move
        # -----------------------------------------------------

        try:
            gdk_window = self.get_window()

            if gdk_window is None:
                return False

            gdk_window.begin_move_drag(
                int(event.button),
                int(event.x_root),
                int(event.y_root),
                event.time
            )

            return True

        except Exception as e:
            print(f"Move error: {e}")
            return False
    # =========================================================
    # WINDOW STATE
    # =========================================================

    def on_size_allocate(
        self,
        widget,
        allocation
    ):

        self.win_w = allocation.width
        self.win_h = allocation.height

        mode_str = (
            "LIGHT"
            if self.is_light_mode
            else "DARK"
        )

        self.size_badge.set_text(
            f"{self.win_w} × "
            f"{self.win_h} "
            f"({mode_str})"
        )

    def on_configure_event(
        self,
        widget,
        event
    ):

        try:

            self.win_x, self.win_y = (
                self.get_position()
            )

        except Exception:
            pass

        self.check_background_luminance()

        return False

    def load_window_state(self):

        if not CONFIG_FILE.exists():
            return

        try:

            data = json.loads(
                CONFIG_FILE.read_text(
                    encoding="utf-8"
                )
            )

            self.win_w = data.get(
                "width",
                self.win_w
            )

            self.win_h = data.get(
                "height",
                self.win_h
            )

            self.win_x = data.get(
                "x",
                None
            )

            self.win_y = data.get(
                "y",
                None
            )

            self.font_size = data.get(
                "font_size",
                self.font_size
            )

            self.bg_alpha = data.get(
                "opacity",
                self.bg_alpha
            )

        except Exception:
            pass

    def save_window_state(self):

        try:

            data = {
                "width": self.win_w,
                "height": self.win_h,
                "x": self.win_x,
                "y": self.win_y,
                "font_size": self.font_size,
                "opacity": self.bg_alpha
            }

            CONFIG_FILE.write_text(
                json.dumps(
                    data,
                    indent=2
                ),
                encoding="utf-8"
            )

        except Exception:
            pass

    # =========================================================
    # CLOSE
    # =========================================================

    def on_close_clicked(
        self,
        button
    ):

        self.save_window_state()
        Gtk.main_quit()

    def on_delete_event(
        self,
        widget,
        event
    ):

        self.save_window_state()
        Gtk.main_quit()

        return True


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    app = MusixmatchOverlay()

    app.show_all()

    app.size_badge.hide()
    app.close_btn.hide()

    Gtk.main()
