# FloatingLyrics

A floating lyrics overlay for Linux desktops.

FloatingLyrics displays synchronized song lyrics in a small desktop window, so you can follow along without switching between applications. It detects the currently playing track, looks up matching lyrics, and keeps them aligned with playback.

Built with Python and GTK3, the project focuses on a simple, unobtrusive experience that fits naturally into a Linux desktop workflow.

## Features

- **Live lyric synchronization** — displays the previous, current, and upcoming lines as a song plays.
- **Automatic track detection** — reads song titles, artists, and playback information through MPRIS using `playerctl`.
- **Lyrics lookup** — retrieves synchronized lyrics from LRCLIB.
- **Local caching** — stores retrieved lyrics locally to reduce repeated requests.
- **Customizable overlay** — supports adjustments to the window's appearance.
- **Browser media support** — works with compatible browsers that expose playback information through MPRIS.

## Technology

- Python
- GTK3 and PyGObject
- `playerctl` / MPRIS
- [LRCLIB](https://lrclib.net/)

Developed primarily on Arch Linux with Hyprland, with additional development on Ubuntu and GNOME.

## Installation

### Arch Linux

Install the required system packages:

```bash
sudo pacman -S --needed python python-gobject gtk3 playerctl git
```

Clone the repository:

```bash
git clone https://github.com/nebularch/floating-lyrics.git
cd floating-lyrics
```

Review `start.sh` and adjust any paths if necessary. Then launch the application:

```bash
./start.sh
```

A compatible media player must expose its playback information through MPRIS for automatic track detection and synchronization.

## Development status

FloatingLyrics is a work in progress. Current development focuses on improving window management under Wayland, refining track detection, and expanding support for songs without synchronized lyrics.

Planned improvements include:

- Manual lyric entry and line-by-line synchronization.
- Saving manually synchronized lyrics as local LRC files.
- More reliable track matching.
- Improved configuration and installation.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
