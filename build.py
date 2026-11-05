#!/usr/bin/env python3
"""平行時空角色年表——靜態網站產生器。

用法：
    python build.py                 # 讀取 data/story.json，輸出到 docs/
    python build.py --data other.json --out public

只用 Python 標準函式庫，不需要安裝任何套件。
資料驗證失敗時會列出所有問題，不會產出半套網站。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DEFAULT_DATA = DATA_DIR / "story.json"
DEFAULT_OUT = ROOT / "docs"
SITE_DIR = ROOT / "site"

TONES = ("moss", "amber", "shared", "bridge")
SIDES = ("left", "right")
EXTERNAL_LINK_PREFIXES = ("http://", "https://")
ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
BACKGROUND_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"}
BACKGROUND_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# ---------------------------------------------------------------------------
# 資料模型
# ---------------------------------------------------------------------------


@dataclass
class World:
    id: str
    name: str
    side: str
    tone: str
    note: str


@dataclass
class Character:
    id: str
    code: str
    name: str
    link: str
    watermark: str
    role: str
    summary: str
    world: World
    lane: int


@dataclass
class Link:
    label: str
    url: str


@dataclass(frozen=True)
class Boundary:
    year: int
    row: int = 1


@dataclass(frozen=True)
class BackgroundSegment:
    image_path: Path
    image_url: str
    opacity: float
    start: Boundary
    end: Boundary | None
    index: int


@dataclass(frozen=True)
class TimelineBackground:
    base_path: Path
    base_url: str
    base_opacity: float
    segments: tuple[BackgroundSegment, ...] = ()

    @property
    def sources(self) -> tuple[Path, ...]:
        paths: list[Path] = [self.base_path]
        for segment in self.segments:
            if segment.image_path not in paths:
                paths.append(segment.image_path)
        return tuple(paths)


@dataclass
class Event:
    id: str
    year: int
    end_year: int
    characters: tuple[Character, ...]
    title: str
    paragraphs: tuple[str, ...]
    links: tuple[Link, ...]
    tone: str
    side: str | None  # None 代表跨越兩側的「交會」事件
    row: int | None = None  # 同一年的列序；相同值 = 同一列（等高對齊）


@dataclass
class Site:
    lang: str
    title: str
    subtitle: str
    eyebrow: str
    intro: str
    description: str
    footer: str
    start_year: int
    end_year: int


@dataclass
class Story:
    site: Site
    worlds: tuple[World, ...]
    characters: tuple[Character, ...]
    events: tuple[Event, ...]
    warnings: tuple[str, ...] = ()
    background: TimelineBackground | None = None

    @property
    def years(self) -> range:
        return range(self.site.start_year, self.site.end_year + 1)

    def characters_on(self, side: str) -> tuple[Character, ...]:
        return tuple(c for c in self.characters if c.world.side == side)

    def lanes_on(self, side: str) -> int:
        chars = self.characters_on(side)
        return max((c.lane for c in chars), default=1)

    @property
    def slots(self) -> int:
        """左右兩側共用的欄位數，取兩側最大 lane 數以維持中央軸對稱。"""
        return max(self.lanes_on("left"), self.lanes_on("right"))


@dataclass
class Placed:
    event: Event
    row: int  # 1 起算的內容列；實際 CSS grid row 會再加 1（保留年份標題列）
    col_start: int | None
    col_end: int | None
    lane_start: int = 0


# ---------------------------------------------------------------------------
# 驗證工具
# ---------------------------------------------------------------------------


class Problems:
    """收集所有資料問題，最後一次回報，方便一次改完。"""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, path: str, message: str) -> None:
        self.errors.append(f"{path}：{message}")

    def warn(self, path: str, message: str) -> None:
        self.warnings.append(f"{path}：{message}")

    def raise_if_errors(self) -> None:
        if self.errors:
            raise DataError(self.errors, self.warnings)


class DataError(Exception):
    def __init__(self, errors: list[str], warnings: list[str]) -> None:
        super().__init__("\n".join(errors))
        self.errors = errors
        self.warnings = warnings


def as_mapping(value: object, path: str, problems: Problems) -> dict:
    if not isinstance(value, dict):
        problems.error(path, "必須是物件（{ ... }）")
        return {}
    return value


def as_text(
    value: object,
    path: str,
    problems: Problems,
    *,
    required: bool = False,
    default: str = "",
) -> str:
    if value is None:
        value = default
    if not isinstance(value, str):
        problems.error(path, "必須是文字")
        return default
    text = value.strip()
    if required and not text:
        problems.error(path, "不可為空")
    return text


def as_int(
    value: object,
    path: str,
    problems: Problems,
    *,
    required: bool = False,
    default: int = 0,
) -> int:
    if value is None:
        if required:
            problems.error(path, "必須是整數")
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        problems.error(path, "必須是整數")
        return default
    return value


def as_ratio(
    value: object,
    path: str,
    problems: Problems,
    *,
    default: float,
) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        problems.error(path, "必須是 0 到 1 之間的數字")
        return default
    number = float(value)
    if not 0.0 <= number <= 1.0:
        problems.error(path, "必須介於 0 到 1 之間")
        return default
    return number


def read_paragraphs(value: object, path: str, problems: Problems) -> tuple[str, ...]:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            problems.error(path, "不可為空")
            return ()
        return tuple(part.strip() for part in re.split(r"\n\s*\n", text) if part.strip())
    if isinstance(value, list):
        paragraphs: list[str] = []
        for index, item in enumerate(value):
            text = as_text(item, f"{path}[{index}]", problems, required=True)
            if text:
                paragraphs.append(text)
        if not paragraphs:
            problems.error(path, "至少需要一段文字")
        return tuple(paragraphs)
    problems.error(path, "必須是文字，或一段文字組成的陣列")
    return ()


def valid_url(url: str) -> bool:
    if not url or any(char in url for char in "\r\n\t\"<>"):
        return False
    parsed = urlsplit(url)
    if parsed.scheme:
        return parsed.scheme.lower() in {"http", "https", "mailto"}
    return url.startswith(("#", "/", "./", "../"))


def read_links(value: object, path: str, problems: Problems) -> tuple[Link, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        problems.error(path, "必須是陣列")
        return ()
    links: list[Link] = []
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, dict):
            problems.error(item_path, "必須是 { \"label\": ..., \"url\": ... } 物件")
            continue
        label = as_text(item.get("label"), f"{item_path}.label", problems, required=True)
        url = as_text(item.get("url"), f"{item_path}.url", problems, required=True)
        if not valid_url(url):
            problems.error(
                f"{item_path}.url",
                "必須是 http(s)、mailto、#錨點、/ 或 ./ 開頭的連結",
            )
            continue
        if urlsplit(url).scheme in {"http", "https"} and not urlsplit(url).netloc:
            problems.warn(f"{item_path}.url", "看起來缺少網域")
        links.append(Link(label=label, url=url))
    return tuple(links)


def parse_site(raw: object, problems: Problems) -> Site:
    data = as_mapping(raw, "site", problems)
    today_year = date.today().year

    start_year = as_int(
        data.get("startYear"), "site.startYear", problems, required=True, default=2000
    )
    end_raw = data.get("endYear", None)
    if end_raw is None or end_raw == "now":
        end_year = today_year
    else:
        end_year = as_int(end_raw, "site.endYear", problems, required=True, default=today_year)

    if start_year < 1900 or start_year > 2200:
        problems.error("site.startYear", "請設定在 1900 到 2200 之間")
    if end_year < start_year:
        problems.error("site.endYear", "不可早於 site.startYear")
    elif end_year - start_year > 150:
        problems.error("site.endYear", "年份區間過長（上限 150 年）")
    if end_year != today_year and end_raw is not None:
        problems.warn("site.endYear", f"目前設定為 {end_year}，不是今年（{today_year}）")

    title = as_text(data.get("title"), "site.title", problems, required=True, default="角色年表")
    subtitle = as_text(data.get("subtitle"), "site.subtitle", problems)
    intro = as_text(data.get("intro"), "site.intro", problems)
    description = as_text(data.get("description"), "site.description", problems) or subtitle or intro
    footer = as_text(data.get("footer"), "site.footer", problems)
    lang = as_text(data.get("lang"), "site.lang", problems, default="zh-Hant") or "zh-Hant"
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", lang):
        problems.error("site.lang", "請使用 BCP 47 語言標記，例如 zh-Hant")
        lang = "zh-Hant"

    eyebrow = as_text(
        data.get("eyebrow"),
        "site.eyebrow",
        problems,
        default="Parallel Worlds / Character Chronicle",
    )

    return Site(
        lang=lang,
        title=title,
        subtitle=subtitle,
        eyebrow=eyebrow,
        intro=intro,
        description=description,
        footer=footer,
        start_year=start_year,
        end_year=end_year,
    )


def parse_worlds(raw: object, problems: Problems) -> tuple[World, ...]:
    if not isinstance(raw, list):
        problems.error("worlds", "必須是陣列")
        return ()
    worlds: list[World] = []
    seen_ids: set[str] = set()
    used_sides: dict[str, str] = {}

    for index, item in enumerate(raw):
        path = f"worlds[{index}]"
        data = as_mapping(item, path, problems)
        world_id = as_text(data.get("id"), f"{path}.id", problems, required=True)
        if world_id:
            if world_id in seen_ids:
                problems.error(f"{path}.id", f"重複的世界線 id「{world_id}」")
            seen_ids.add(world_id)
        name = as_text(data.get("name"), f"{path}.name", problems, default=world_id)
        side = as_text(data.get("side"), f"{path}.side", problems, required=True)
        if side not in SIDES:
            problems.error(f"{path}.side", "必須是 left 或 right")
        elif side in used_sides:
            problems.error(
                f"{path}.side",
                f"同一側只能有一條世界線（{side} 已被 {used_sides[side]} 使用）",
            )
        else:
            used_sides[side] = world_id or path
        tone = as_text(data.get("tone"), f"{path}.tone", problems, default="moss")
        if tone not in TONES or tone == "bridge":
            problems.error(f"{path}.tone", "必須是 moss、amber 或 shared")
            tone = "moss"
        note = as_text(data.get("note"), f"{path}.note", problems)
        worlds.append(World(id=world_id, name=name or world_id, side=side, tone=tone, note=note))

    for side in SIDES:
        if side not in used_sides:
            problems.error("worlds", f"缺少 side 為「{side}」的世界線")
    return tuple(worlds)


def parse_characters(
    raw: object, worlds: tuple[World, ...], problems: Problems
) -> tuple[Character, ...]:
    if not isinstance(raw, list):
        problems.error("characters", "必須是陣列")
        return ()

    world_by_id = {world.id: world for world in worlds}
    parsed: list[
        tuple[dict, str, str, str, str, str, str, str, str, World, int | None]
    ] = []
    seen_ids: set[str] = set()

    for index, item in enumerate(raw):
        path = f"characters[{index}]"
        data = as_mapping(item, path, problems)
        char_id = as_text(data.get("id"), f"{path}.id", problems, required=True)
        if char_id:
            if char_id in seen_ids:
                problems.error(f"{path}.id", f"重複的角色 id「{char_id}」")
            seen_ids.add(char_id)
        code = as_text(data.get("code"), f"{path}.code", problems, required=True)
        name = as_text(data.get("name"), f"{path}.name", problems, required=True)
        link = as_text(data.get("link"), f"{path}.link", problems)
        if link and not valid_url(link):
            problems.error(
                f"{path}.link",
                "必須是 http(s)、mailto、#錨點、/ 或 ./ 開頭的連結",
            )
        watermark = as_text(
            data.get("watermark"),
            f"{path}.watermark",
            problems,
            default=name[:1] if name else "?",
        )
        role = as_text(data.get("role"), f"{path}.role", problems)
        summary = as_text(data.get("summary"), f"{path}.summary", problems, required=True)

        world_id = as_text(data.get("world"), f"{path}.world", problems, required=True)
        world = world_by_id.get(world_id)
        if world is None:
            problems.error(f"{path}.world", f"找不到世界線「{world_id}」")
            continue
        if world.side not in SIDES:
            problems.error(
                f"{path}.world",
                f"世界線「{world_id}」的 side「{world.side}」無效，無法配置角色",
            )
            continue

        lane_raw = data.get("lane")
        lane: int | None = None
        if lane_raw is not None:
            lane = as_int(lane_raw, f"{path}.lane", problems, required=True)
            if lane < 1:
                problems.error(f"{path}.lane", "必須是 1 以上的整數")
                lane = None
        parsed.append(
            (data, path, char_id, code, name, link, watermark, role, summary, world, lane)
        )

    # 先保留手動指定的 lane，再把未指定的角色補進最小的空位。
    used: dict[str, set[int]] = {side: set() for side in SIDES}
    for *_, world, lane in parsed:
        if lane is not None:
            if lane in used[world.side]:
                problems.error("characters", f"{world.side} 側的 lane {lane} 重複")
            used[world.side].add(lane)

    characters: list[Character] = []
    for (
        _data,
        _path,
        char_id,
        code,
        name,
        link,
        watermark,
        role,
        summary,
        world,
        lane,
    ) in parsed:
        if lane is None:
            candidate = 1
            while candidate in used[world.side]:
                candidate += 1
            lane = candidate
            used[world.side].add(lane)
        characters.append(
            Character(
                id=char_id,
                code=code,
                name=name,
                link=link,
                watermark=watermark,
                role=role,
                summary=summary,
                world=world,
                lane=lane,
            )
        )

    for side in SIDES:
        lanes = sorted(used[side])
        if not lanes:
            problems.error("characters", f"{side} 側沒有任何角色")
            continue
        if lanes != list(range(1, len(lanes) + 1)):
            problems.error(
                "characters",
                f"{side} 側的 lane 必須從 1 開始連續編號，目前是 {lanes}",
            )
        if len(lanes) > 4:
            problems.warn("characters", f"{side} 側有 {len(lanes)} 個角色，版面可能過於擁擠")

    return tuple(characters)


def parse_events(
    raw: object, characters: tuple[Character, ...], site: Site, problems: Problems
) -> tuple[Event, ...]:
    if not isinstance(raw, list):
        problems.error("events", "必須是陣列")
        return ()

    char_by_id = {character.id: character for character in characters}
    events: list[Event] = []
    seen_ids: set[str] = set()

    for index, item in enumerate(raw):
        path = f"events[{index}]"
        data = as_mapping(item, path, problems)

        year = as_int(data.get("year"), f"{path}.year", problems, required=True)
        if year and not site.start_year <= year <= site.end_year:
            problems.error(
                f"{path}.year",
                f"必須介於 {site.start_year} 到 {site.end_year} 之間",
            )
        end_raw = data.get("endYear")
        end_year = year if end_raw is None else as_int(end_raw, f"{path}.endYear", problems)
        if end_year < year:
            problems.error(f"{path}.endYear", "不可早於 year")
        if end_year > site.end_year:
            problems.error(
                f"{path}.endYear",
                f"不可晚於年表終點 {site.end_year}",
            )
            end_year = site.end_year

        char_ids = data.get("characters")
        event_chars: list[Character] = []
        if not isinstance(char_ids, list) or not char_ids:
            problems.error(f"{path}.characters", "必須是至少包含一個角色 id 的陣列")
        else:
            for char_index, char_id in enumerate(char_ids):
                if not isinstance(char_id, str) or not char_id.strip():
                    problems.error(f"{path}.characters[{char_index}]", "必須是角色 id 字串")
                    continue
                character = char_by_id.get(char_id.strip())
                if character is None:
                    problems.error(
                        f"{path}.characters[{char_index}]",
                        f"找不到角色「{char_id}」",
                    )
                    continue
                if character in event_chars:
                    problems.warn(f"{path}.characters", f"角色「{char_id}」重複列出，已自動去除")
                    continue
                event_chars.append(character)

        sides = {character.world.side for character in event_chars}
        side: str | None
        if len(sides) == 2:
            side = None
        elif len(sides) == 1:
            side = next(iter(sides))
        else:
            side = None

        title = as_text(data.get("title"), f"{path}.title", problems)
        paragraphs = read_paragraphs(data.get("body"), f"{path}.body", problems)
        links = read_links(data.get("links"), f"{path}.links", problems)

        row_raw = data.get("row")
        row: int | None = None
        if row_raw is not None:
            row = as_int(row_raw, f"{path}.row", problems, required=True)
            if row < 1:
                problems.error(f"{path}.row", "必須是 1 以上的整數")
                row = None

        default_tone = "bridge" if side is None else event_chars[0].world.tone
        tone = as_text(data.get("tone"), f"{path}.tone", problems, default=default_tone)
        if tone not in TONES:
            problems.error(f"{path}.tone", "必須是 moss、amber、shared 或 bridge")
            tone = default_tone

        event_id = as_text(data.get("id"), f"{path}.id", problems)
        if event_id:
            if not ID_PATTERN.fullmatch(event_id):
                problems.error(
                    f"{path}.id",
                    "只能使用英數字、底線與連字號，且必須以英文字母開頭",
                )
            if event_id in seen_ids:
                problems.error(f"{path}.id", f"重複的事件 id「{event_id}」")
            seen_ids.add(event_id)
        else:
            event_id = f"evt-{year}-{index + 1:02d}"
            while event_id in seen_ids:
                event_id += "x"
            seen_ids.add(event_id)

        events.append(
            Event(
                id=event_id,
                year=year,
                end_year=end_year,
                characters=tuple(event_chars),
                title=title,
                paragraphs=paragraphs,
                links=links,
                tone=tone,
                side=side,
                row=row,
            )
        )

    events.sort(key=lambda event: event.year)
    if not events:
        problems.warn("events", "目前沒有任何事件")
    return tuple(events)


def validate_event_rows(events: tuple[Event, ...], problems: Problems) -> None:
    """檢查手動指定的 row 是否有「同一列卻佔用同一欄」的衝突。"""
    by_year: dict[int, list[Event]] = {}
    for event in events:
        by_year.setdefault(event.year, []).append(event)

    for year in sorted(by_year):
        year_events = by_year[year]
        explicit = [event for event in year_events if event.row is not None]
        if not explicit:
            continue
        if len(explicit) != len(year_events):
            problems.warn(
                f"events（{year} 年）",
                "部分事件沒有設定 row，這些事件會排在已指定 row 的列之後",
            )

        groups: dict[int, list[Event]] = {}
        for event in explicit:
            groups.setdefault(event.row, []).append(event)

        for row_key in sorted(groups):
            group = groups[row_key]
            bridges = [event for event in group if event.side is None]
            if len(bridges) > 1:
                ids = "、".join(event.id for event in bridges)
                problems.error(
                    f"events（{year} 年·row {row_key}）",
                    f"同一列只能有一個交會事件；同一列有：{ids}",
                )

            for side in SIDES:
                occupied: dict[int, str] = (
                    {1: bridges[0].id} if bridges else {}
                )
                for event in group:
                    if event.side is None:
                        continue
                    lanes = [
                        character.lane
                        for character in event.characters
                        if character.world.side == side
                    ]
                    if not lanes:
                        continue
                    low, high = min(lanes), max(lanes)
                    for lane in range(low, high + 1):
                        if lane in occupied:
                            problems.error(
                                f"events（{year} 年·row {row_key}）",
                                f"{side} 側 lane {lane} 重疊："
                                f"{event.id} 與 {occupied[lane]}",
                            )
                        else:
                            occupied[lane] = event.id


def parse_background_image(
    value: object, path: str, problems: Problems
) -> tuple[Path, str] | None:
    name = as_text(value, path, problems, required=True)
    if not name:
        return None

    normalized = name.replace("\\", "/")
    if normalized.startswith("data/"):
        normalized = normalized[5:]
    if "/" in normalized or normalized in {"", ".", ".."}:
        problems.error(path, "請把圖片放在 data/ 資料夾，這裡只填檔名（例如 map.jpg）")
        return None
    if not BACKGROUND_NAME.fullmatch(normalized):
        problems.error(path, "檔名只能使用英數、底線、連字號與點")
        return None
    suffix = Path(normalized).suffix.lower()
    if suffix not in BACKGROUND_SUFFIXES:
        problems.error(path, "格式必須是 .jpg、.jpeg、.png、.webp、.avif 或 .gif")
        return None

    source = DATA_DIR / normalized
    if not source.is_file():
        problems.error(path, f"找不到圖片 {source}")
        return None
    return source, f"assets/backgrounds/{normalized}"


def parse_background_boundary(
    value: object,
    path: str,
    site: Site,
    problems: Problems,
    *,
    required: bool,
) -> Boundary | None:
    if value is None:
        if required:
            problems.error(path, "必須指定起始的 year 與 row")
        return None

    data = as_mapping(value, path, problems)
    year = as_int(data.get("year"), f"{path}.year", problems, required=True)
    if not site.start_year <= year <= site.end_year:
        problems.error(
            f"{path}.year",
            f"必須介於 {site.start_year} 到 {site.end_year}",
        )
    row = as_int(data.get("row", 1), f"{path}.row", problems, default=1)
    if row < 1:
        problems.error(f"{path}.row", "必須是 1 以上的整數")
    return Boundary(year=year, row=row)


def parse_background(
    raw: object, site: Site, problems: Problems
) -> TimelineBackground | None:
    if raw is None:
        return None

    data = as_mapping(raw, "timelineBackground", problems)
    base = parse_background_image(
        data.get("baseImage"), "timelineBackground.baseImage", problems
    )
    base_opacity = as_ratio(
        data.get("baseOpacity"),
        "timelineBackground.baseOpacity",
        problems,
        default=0.16,
    )

    segments_raw = data.get("segments", [])
    if not isinstance(segments_raw, list):
        problems.error("timelineBackground.segments", "必須是陣列")
        segments_raw = []

    segments: list[BackgroundSegment] = []
    for index, item in enumerate(segments_raw):
        path = f"timelineBackground.segments[{index}]"
        item_data = as_mapping(item, path, problems)
        image = parse_background_image(
            item_data.get("image"), f"{path}.image", problems
        )
        opacity = as_ratio(
            item_data.get("opacity"),
            f"{path}.opacity",
            problems,
            default=0.22,
        )
        start = parse_background_boundary(
            item_data.get("start"),
            f"{path}.start",
            site,
            problems,
            required=True,
        )
        end = parse_background_boundary(
            item_data.get("end"),
            f"{path}.end",
            site,
            problems,
            required=False,
        )
        if start and end and (end.year, end.row) <= (start.year, start.row):
            problems.error(
                f"{path}.end",
                "必須晚於 start（以 year、row 排序）",
            )
        if image is None or start is None:
            continue
        segments.append(
            BackgroundSegment(
                image_path=image[0],
                image_url=image[1],
                opacity=opacity,
                start=start,
                end=end,
                index=len(segments),
            )
        )

    if base is None:
        return None
    return TimelineBackground(
        base_path=base[0],
        base_url=base[1],
        base_opacity=base_opacity,
        segments=tuple(segments),
    )


def load_story(path: Path) -> Story:
    try:
        raw_text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise DataError([f"找不到資料檔：{path}"], []) from exc
    except OSError as exc:
        raise DataError([f"無法讀取資料檔 {path}：{exc}"], []) from exc

    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise DataError(
            [f"{path} 第 {exc.lineno} 行、第 {exc.colno} 欄：JSON 格式錯誤——{exc.msg}"],
            [],
        ) from exc

    if not isinstance(raw, dict):
        raise DataError(["資料檔最外層必須是物件（{ ... }）"], [])

    problems = Problems()
    site = parse_site(raw.get("site"), problems)
    background = parse_background(raw.get("timelineBackground"), site, problems)
    worlds = parse_worlds(raw.get("worlds"), problems)
    characters = parse_characters(raw.get("characters"), worlds, problems)
    events = parse_events(raw.get("events"), characters, site, problems)
    validate_event_rows(events, problems)
    problems.raise_if_errors()
    return Story(
        site=site,
        worlds=worlds,
        characters=characters,
        events=events,
        warnings=tuple(problems.warnings),
        background=background,
    )


# ---------------------------------------------------------------------------
# 版面計算
# ---------------------------------------------------------------------------


def _event_lanes(event: Event, side: str) -> tuple[int, int] | None:
    lanes = [
        character.lane
        for character in event.characters
        if character.world.side == side
    ]
    if not lanes:
        return None
    return min(lanes), max(lanes)


@dataclass
class RowState:
    left: set[int] = field(default_factory=set)
    right: set[int] = field(default_factory=set)
    bridge: bool = False

    def can_hold(self, event: Event) -> bool:
        if event.side is None:
            return not self.bridge and 1 not in self.left and 1 not in self.right
        lane_range = _event_lanes(event, event.side)
        if lane_range is None:
            return False
        low, high = lane_range
        occupied = self.left if event.side == "left" else self.right
        return occupied.isdisjoint(range(low, high + 1))

    def occupy(self, event: Event) -> None:
        if event.side is None:
            self.bridge = True
            self.left.add(1)
            self.right.add(1)
            return
        lane_range = _event_lanes(event, event.side)
        if lane_range is None:
            return
        low, high = lane_range
        occupied = self.left if event.side == "left" else self.right
        occupied.update(range(low, high + 1))


def layout_band(events: list[Event], slots: int) -> list[Placed]:
    """把同一年的事件排進共用的列。

    - 指定 row 的事件依 row 數字分組；相同 row = 同一列，左右兩側會等高。
    - 未指定 row 的事件，接在指定列之後，依序塞進最早可用的列。
    - 交會事件使用中央欄，可與左右側事件共用一列。
    """
    explicit: dict[int, list[Event]] = {}
    auto: list[Event] = []
    for event in events:
        if event.row is None:
            auto.append(event)
        else:
            explicit.setdefault(event.row, []).append(event)

    rows: list[RowState] = []
    event_rows: dict[str, int] = {}

    for row_key in sorted(explicit):
        state = RowState()
        for event in explicit[row_key]:
            state.occupy(event)
            event_rows[event.id] = len(rows)
        rows.append(state)

    auto_start = len(rows)
    for event in auto:
        target: int | None = None
        for index in range(auto_start, len(rows)):
            if rows[index].can_hold(event):
                target = index
                break
        if target is None:
            rows.append(RowState())
            target = len(rows) - 1
        rows[target].occupy(event)
        event_rows[event.id] = target

    placed: list[Placed] = []
    for event in events:
        row = event_rows[event.id] + 1
        if event.side is None:
            axis_column = slots + 1
            placed.append(
                Placed(
                    event=event,
                    row=row,
                    col_start=axis_column - 1,
                    col_end=axis_column + 2,
                )
            )
            continue

        lane_range = _event_lanes(event, event.side)
        if lane_range is None:
            placed.append(Placed(event=event, row=row, col_start=None, col_end=None))
            continue
        low, high = lane_range
        if event.side == "left":
            col_start = slots - high + 1
            col_end = slots - low + 2
        else:
            col_start = slots + 1 + low
            col_end = slots + high + 2
        placed.append(
            Placed(
                event=event,
                row=row,
                col_start=col_start,
                col_end=col_end,
                lane_start=low,
            )
        )
    return placed


# ---------------------------------------------------------------------------
# HTML 產生
# ---------------------------------------------------------------------------


def esc(text: object) -> str:
    return escape(str(text), quote=True)


def render_paragraphs(paragraphs: tuple[str, ...]) -> str:
    rendered = []
    for paragraph in paragraphs:
        safe = esc(paragraph).replace("\n", "<br>")
        rendered.append(f"<p>{safe}</p>")
    return "\n          ".join(rendered)


def render_hero(site: Site, worlds: tuple[World, ...]) -> str:
    legend = []
    for world in worlds:
        legend.append(
            f'<li class="world tone--{world.tone}">'
            '<span class="world__swatch" aria-hidden="true"></span>'
            f'<span class="world__id">{esc(world.id)}</span>'
            f'<span class="world__note">{esc(world.note)}</span>'
            "</li>"
        )
    subtitle = (
        f'\n  <p class="hero__subtitle">{esc(site.subtitle)}</p>' if site.subtitle else ""
    )
    intro = f'\n  <p class="hero__lede">{esc(site.intro)}</p>' if site.intro else ""
    return f"""<header class="hero wrap">
  <p class="eyebrow">{esc(site.eyebrow)}</p>
  <h1 class="hero__title">{esc(site.title)}</h1>{subtitle}{intro}
  <ul class="worlds" aria-label="世界線圖例">
    {''.join(legend)}
  </ul>
  <p class="hero__jump"><a href="#timeline">直接看年表 <span aria-hidden="true">↓</span></a></p>
</header>"""


def render_cast(story: Story) -> str:
    slots = story.slots
    cards = []
    for character in story.characters:
        side = character.world.side
        column = (
            slots - character.lane + 1
            if side == "left"
            else slots + 1 + character.lane
        )
        name = esc(character.name)
        if character.link:
            external = urlsplit(character.link).scheme.lower() in {"http", "https"}
            attrs = ' target="_blank" rel="noopener noreferrer"' if external else ""
            arrow = (
                '<span class="link-arrow" aria-hidden="true">↗</span>'
                '<span class="sr-only">（另開新視窗）</span>'
                if external
                else ""
            )
            name = f'<a href="{esc(character.link)}"{attrs}>{name}{arrow}</a>'
        cards.append(
            f"""<article class="char-card char-card--{side} tone--{character.world.tone}" style="--col-start:{column};--col-end:{column + 1}">
        <span class="char-card__watermark" aria-hidden="true">{esc(character.watermark)}</span>
        <p class="char-card__code">{esc(character.code)}</p>
        <h3 class="char-card__name">{name}</h3>
        <p class="char-card__role">{esc(character.role)}</p>
        <p class="char-card__summary">{esc(character.summary).replace(chr(10), "<br>")}</p>
      </article>"""
        )
    return f"""<section class="cast wrap" style="--slots:{slots}" aria-labelledby="cast-heading">
  <h2 class="sr-only" id="cast-heading">角色簡介</h2>
  {''.join(cards)}
  <div class="cast__axis" aria-hidden="true"></div>
</section>"""


def render_toolbar(story: Story) -> str:
    options = []
    for year in story.years:
        label = f"{year}（現在）" if year == story.site.end_year else str(year)
        options.append(f'<option value="{year}">{label}</option>')
    focus_buttons = [
        '<button type="button" class="btn" data-focus-side="all" aria-pressed="true">全部</button>'
    ]
    for world in story.worlds:
        focus_buttons.append(
            f'<button type="button" class="btn" data-focus-side="{world.side}" '
            f'aria-pressed="false" title="{esc(world.name)}｜{esc(world.note)}">'
            f"{esc(world.id)}</button>"
        )
    return f"""<div class="topbar">
  <div class="topbar__inner wrap">
    <div class="year-readout">
      <span class="year-readout__label">YEAR</span>
      <span class="year-readout__value" id="current-year">{story.site.start_year}</span>
    </div>
    <p class="topbar__hint">{story.site.start_year}–{story.site.end_year} · 向下閱讀，或跳到指定年份</p>
    <div class="controls">
      <label class="jump">
        <span class="sr-only">跳到指定年份</span>
        <select id="year-jump">
          {''.join(options)}
        </select>
      </label>
      <button type="button" class="btn" id="compact-toggle" aria-controls="timeline" aria-pressed="false"><span class="when-wide">隱藏空白年份</span><span class="when-narrow">精簡</span></button>
      <div class="focus-group" role="group" aria-label="聚焦世界線">
        {''.join(focus_buttons)}
      </div>
    </div>
  </div>
</div>"""


def render_chip(character: Character) -> str:
    return (
        f'<li class="chip tone--{character.world.tone}">'
        '<span class="chip__dot" aria-hidden="true"></span>'
        f'<span class="chip__code">{esc(character.code)}</span>'
        f'<span class="chip__name">{esc(character.name)}</span>'
        "</li>"
    )


def render_links(links: tuple[Link, ...]) -> str:
    items = []
    for link in links:
        external = link.url.startswith(EXTERNAL_LINK_PREFIXES)
        attrs = ' target="_blank" rel="noopener noreferrer"' if external else ""
        arrow = (
            '<span class="link-arrow" aria-hidden="true">↗</span>' if external else ""
        )
        new_window = (
            '<span class="sr-only">（另開新視窗）</span>' if external else ""
        )
        items.append(
            f'<li><a href="{esc(link.url)}"{attrs}>{esc(link.label)}{new_window}{arrow}</a></li>'
        )
    return f'<ul class="entry__links" aria-label="相關連結">{"".join(items)}</ul>'


def render_entry(
    event: Event,
    placed: Placed,
    mobile_row: int,
) -> str:
    side = event.side
    classes = ["entry"]
    if side is None:
        classes.append("entry--bridge")
    else:
        classes.append(f"entry--{side}")
    if side is not None and len(event.characters) > 1:
        classes.append("entry--merged")
    classes.append(f"tone--{event.tone}")

    style_parts = [
        f"--row:{placed.row + 1}",
        f"--m-row:{mobile_row + 1}",
    ]
    if placed.col_start is not None and placed.col_end is not None:
        style_parts.extend(
            [f"--col-start:{placed.col_start}", f"--col-end:{placed.col_end}"]
        )
    style = f' style="{";".join(style_parts)}"'

    meta = []
    if event.end_year > event.year:
        meta.append(
            f'<span class="entry__range">{event.year}–{event.end_year}</span>'
        )
    if side is None:
        meta.append('<span class="entry__badge entry__badge--bridge">世界線交會</span>')
    elif len(event.characters) > 1:
        meta.append('<span class="entry__badge">共同記事</span>')
    meta_html = (
        f'\n        <div class="entry__meta">{"".join(meta)}</div>' if meta else ""
    )

    title_html = ""
    if event.title:
        title_html = (
            f'\n        <h3 class="entry__title" id="{esc(event.id)}-title">'
            f"{esc(event.title)}</h3>"
        )
    labelled = (
        f' aria-labelledby="{esc(event.id)}-title"' if event.title else ""
    )
    worlds = " ".join(dict.fromkeys(character.world.id for character in event.characters))
    data_side = side if side is not None else "bridge"

    chips = "".join(render_chip(character) for character in event.characters)
    links_html = (
        f'\n        {render_links(event.links)}' if event.links else ""
    )

    return f"""<article class="{' '.join(classes)}" id="{esc(event.id)}" data-year="{event.year}" data-row="{placed.row}" data-side="{data_side}" data-worlds="{esc(worlds)}"{labelled}{style}>{meta_html}{title_html}
        <div class="entry__body">
          {render_paragraphs(event.paragraphs)}
        </div>
        <ul class="entry__cast" aria-label="登場角色">{chips}</ul>{links_html}
      </article>"""


def render_band(story: Story, year: int, now_year: int) -> str:
    events = [event for event in story.events if event.year == year]
    has_content = bool(events)
    classes = ["band", "band--content" if has_content else "band--empty"]
    if year == now_year:
        classes.append("band--now")

    now_label = '<span class="sr-only">（現在）</span>' if year == now_year else ""
    year_label = (
        f'<h2 class="band__year" id="year-label-{year}">'
        f'<span class="band__year-value">{year}</span>{now_label}</h2>'
    )

    placed = layout_band(events, story.slots)
    order_by_id = {event.id: index for index, event in enumerate(events)}

    def mobile_sort_key(item: Placed) -> tuple[int, int, int, int]:
        side_rank = 0 if item.event.side is None else (1 if item.event.side == "left" else 2)
        return (
            item.row,
            side_rank,
            item.lane_start,
            order_by_id[item.event.id],
        )

    placed.sort(key=mobile_sort_key)
    entries_html = "\n    ".join(
        render_entry(item.event, item, index + 1)
        for index, item in enumerate(placed)
    )
    return f"""<div class="{' '.join(classes)}" id="year-{year}" data-year="{year}">
    {year_label}
    {entries_html}
  </div>"""


def render_background_layers(story: Story) -> str:
    background = story.background
    if background is None:
        return ""

    layers = [
        '<div class="timeline__bg timeline__bg--base" aria-hidden="true" '
        f'style="--bg-opacity:{background.base_opacity:.3f}">'
        f'<img class="timeline__bg-image" src="{esc(background.base_url)}" '
        'alt="" decoding="async"></div>'
    ]
    for segment in background.segments:
        end_year = str(segment.end.year) if segment.end else ""
        end_row = str(segment.end.row) if segment.end else ""
        layers.append(
            '<div class="timeline__bg timeline__bg--segment" '
            f'data-bg-start-year="{segment.start.year}" '
            f'data-bg-start-row="{segment.start.row}" '
            f'data-bg-end-year="{end_year}" '
            f'data-bg-end-row="{end_row}" aria-hidden="true" '
            f'style="--bg-opacity:{segment.opacity:.3f}">'
            f'<img class="timeline__bg-image" src="{esc(segment.image_url)}" '
            'alt="" decoding="async"></div>'
        )
    return "\n  ".join(layers)


def render_timeline(story: Story) -> str:
    backgrounds = render_background_layers(story)
    bands = "\n  ".join(
        render_band(story, year, story.site.end_year) for year in story.years
    )
    return f"""<div class="timeline" id="timeline" role="region" style="--slots:{story.slots}" data-start-year="{story.site.start_year}" data-end-year="{story.site.end_year}" aria-label="{story.site.start_year} 年至 {story.site.end_year} 年角色年表">
  {backgrounds}
  {bands}
</div>"""


def render_footer(story: Story) -> str:
    note = story.site.footer or "以 Python 靜態產生；資料與版面分離，可直接編輯 data/story.json。"
    stamp = datetime.now().strftime("%Y-%m-%d")
    return f"""<footer class="site-footer">
  <div class="wrap site-footer__inner">
    <p class="site-footer__note">{esc(note)}</p>
    <p class="site-footer__meta">Python 靜態產生 · {stamp} · <a href="#top">回到頂端 <span aria-hidden="true">↑</span></a></p>
  </div>
</footer>"""


def render_document(story: Story) -> str:
    template_path = SITE_DIR / "index.html"
    try:
        template = template_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DataError([f"找不到版型檔：{template_path}"], []) from exc

    replacements = {
        "{{LANG}}": esc(story.site.lang),
        "{{TITLE}}": esc(story.site.title),
        "{{PAGE_TITLE}}": esc(
            f"{story.site.title}｜{story.site.subtitle}"
            if story.site.subtitle
            else story.site.title
        ),
        "{{DESCRIPTION}}": esc(story.site.description),
        "{{HERO}}": render_hero(story.site, story.worlds),
        "{{CAST}}": render_cast(story),
        "{{TOOLBAR}}": render_toolbar(story),
        "{{TIMELINE}}": render_timeline(story),
        "{{FOOTER}}": render_footer(story),
        "{{GENERATED}}": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    document = template
    for marker, value in replacements.items():
        document = document.replace(marker, value)

    leftovers = sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", document)))
    if leftovers:
        raise DataError([f"版型中仍有未替換的標記：{', '.join(leftovers)}"], [])
    return document


def write_site(story: Story, out_dir: Path) -> None:
    document = render_document(story)
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    for name in ("style.css", "app.js"):
        source = SITE_DIR / name
        if not source.exists():
            raise DataError([f"找不到前端資源：{source}"], [])
        shutil.copyfile(source, assets_dir / name)

    if story.background is not None:
        backgrounds_dir = assets_dir / "backgrounds"
        backgrounds_dir.mkdir(parents=True, exist_ok=True)
        for source in story.background.sources:
            shutil.copyfile(source, backgrounds_dir / source.name)

    (out_dir / "index.html").write_text(document, encoding="utf-8")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")


# ---------------------------------------------------------------------------
# 命令列入口
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    # Windows 將輸出重導向時預設可能是 cp950；改成 UTF-8 以避免特殊符號錯誤。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and not stream.isatty():
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError):
                pass

    parser = argparse.ArgumentParser(
        description="把 data/story.json 產生為可直接放到 GitHub Pages 的靜態網站。"
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=DEFAULT_DATA,
        help=f"資料檔路徑（預設：{DEFAULT_DATA}）",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"輸出資料夾（預設：{DEFAULT_OUT}）",
    )
    args = parser.parse_args(argv)

    try:
        story = load_story(args.data)
        write_site(story, args.out)
    except DataError as exc:
        for warning in exc.warnings:
            print(f"提示：{warning}", file=sys.stderr)
        print(f"\n資料驗證失敗（{len(exc.errors)} 項）：", file=sys.stderr)
        for error in exc.errors:
            print(f"  - {error}", file=sys.stderr)
        print("\n請修正後再執行一次 python build.py。", file=sys.stderr)
        return 2

    print("✓ 網站已產生")
    print(f"  輸出：{args.out / 'index.html'}")
    print(
        f"  角色 {len(story.characters)} · 事件 {len(story.events)} · "
        f"年份 {story.site.start_year}–{story.site.end_year}"
    )
    if story.warnings:
        print("\n提示：")
        for warning in story.warnings:
            print(f"  - {warning}")
    print("  預覽：python serve.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
