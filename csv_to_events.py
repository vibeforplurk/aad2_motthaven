#!/usr/bin/env python3
"""把可編輯的 data/events.csv 轉成 data/events.json，或反向匯出。

日常流程：
    python csv_to_events.py
    python build.py

若 events.json 被直接修改，想重新產生 CSV：
    python csv_to_events.py --export
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

FIELDS = [
    "id",
    "year",
    "endYear",
    "row",
    "characters",
    "title",
    "tone",
    "body",
    "links_title",
    "links_url",
    "temporary",
]
LINK_FIELDS = ("links_title", "links_url")
BASE_FIELDS = tuple(field for field in FIELDS if field not in LINK_FIELDS)

ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
TONES = {"moss", "amber", "shared", "bridge"}
LINK_PREFIXES = ("http://", "https://", "mailto:", "#", "/", "./", "../")


class ConversionError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("\n".join(errors))
        self.errors = errors


def read_json(path: Path) -> object:
    if not path.is_file():
        raise ConversionError([f"找不到檔案：{path}"])
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConversionError(
            [f"{path}：JSON 語法錯誤（第 {exc.lineno} 行第 {exc.colno} 欄）"]
        ) from exc


def load_character_ids(path: Path) -> set[str]:
    payload = read_json(path)
    items = payload.get("characters") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ConversionError([f"{path}：characters 必須是陣列"])
    ids: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ConversionError([f"{path}：characters[{index}].id 必須是字串"])
        ids.add(item["id"])
    return ids


def parse_int(
    value: object,
    label: str,
    errors: list[str],
    *,
    required: bool = False,
) -> int | None:
    text = str(value or "").strip()
    if not text:
        if required:
            errors.append(f"{label} 不可為空")
        return None
    try:
        return int(text)
    except ValueError:
        errors.append(f"{label} 必須是整數（目前是「{text}」）")
        return None


def parse_bool(value: object, label: str, errors: list[str]) -> bool:
    text = str(value or "").strip().lower()
    if text in {"", "false", "0", "no", "n"}:
        return False
    if text in {"true", "1", "yes", "y"}:
        return True
    errors.append(f"{label} 必須是 true 或 false（目前是「{value}」）")
    return False


def parse_characters(
    value: object,
    label: str,
    char_ids: set[str],
    errors: list[str],
) -> list[str]:
    text = str(value or "").strip()
    parts = [part for part in re.split(r"[;,]\s*|\s+", text) if part]
    if not parts:
        errors.append(f"{label} 至少要有一個角色 id")
        return []

    unique: list[str] = []
    for part in parts:
        if part not in char_ids:
            errors.append(f"{label} 找不到角色「{part}」")
        if part not in unique:
            unique.append(part)
    return unique


def parse_body(value: object, label: str, errors: list[str]) -> list[str]:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        errors.append(f"{label} 不可為空")
        return []
    paragraphs = [
        part.strip()
        for part in re.split(r"\n[ \t]*\n", text)
        if part.strip()
    ]
    if not paragraphs:
        errors.append(f"{label} 至少要有一段文字")
    return paragraphs


def valid_url(url: str) -> bool:
    return url.startswith(LINK_PREFIXES)


def parse_links(
    titles_value: object,
    urls_value: object,
    label: str,
    errors: list[str],
) -> list[dict[str, str]]:
    titles_text = str(titles_value or "").strip()
    urls_text = str(urls_value or "").strip()
    if not titles_text and not urls_text:
        return []

    titles = [part.strip() for part in titles_text.split("|")]
    urls = [part.strip() for part in urls_text.split("|")]
    if len(titles) != len(urls):
        errors.append(
            f"{label} 的 links_title 與 links_url 數量不一致"
            f"（{len(titles)} 個標題、{len(urls)} 個 URL）"
        )
        return []

    links: list[dict[str, str]] = []
    for index, (link_label, url) in enumerate(zip(titles, urls), start=1):
        if not link_label:
            errors.append(f"{label} 第 {index} 個連結的標題不可為空")
            continue
        if not url:
            errors.append(f"{label} 第 {index} 個連結的 URL 不可為空")
            continue
        if not valid_url(url):
            errors.append(
                f"{label} 第 {index} 個連結的 URL 必須是 http(s)、mailto、"
                "#、/、./ 或 ../ 開頭"
            )
            continue
        links.append({"label": link_label, "url": url})
    return links


def parse_legacy_links(
    value: object,
    label: str,
    errors: list[str],
) -> list[dict[str, str]]:
    """支援舊版單一 links 欄位：每行「標題 | URL」。"""
    text = str(value or "").strip()
    if not text:
        return []
    links: list[dict[str, str]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if "|" not in line:
            errors.append(
                f"{label} 第 {line_number} 行格式必須是「連結文字 | URL」"
            )
            continue
        link_label, url = (part.strip() for part in line.split("|", 1))
        if not link_label or not url:
            errors.append(
                f"{label} 第 {line_number} 行的連結文字與 URL 都不可為空"
            )
            continue
        if not valid_url(url):
            errors.append(
                f"{label} 第 {line_number} 行的 URL 必須是 http(s)、mailto、"
                "#、/、./ 或 ../ 開頭"
            )
            continue
        links.append({"label": link_label, "url": url})
    return links


def import_csv(
    csv_path: Path,
    json_path: Path,
    characters_path: Path,
) -> int:
    char_ids = load_character_ids(characters_path)
    if not csv_path.is_file():
        raise ConversionError([f"找不到 CSV：{csv_path}"])

    errors: list[str] = []
    events: list[dict] = []
    seen_ids: set[str] = set()

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = [name.strip() for name in (reader.fieldnames or [])]
        missing_base = [
            name for name in BASE_FIELDS if name not in fieldnames
        ]
        has_new_links = all(name in fieldnames for name in LINK_FIELDS)
        has_legacy_links = "links" in fieldnames
        missing_links = (
            []
            if has_new_links or has_legacy_links
            else list(LINK_FIELDS)
        )
        if missing_base or missing_links:
            raise ConversionError(
                [
                    f"{csv_path}：缺少欄位 "
                    f"{', '.join(missing_base + missing_links)}；"
                    f"必要欄位是 {', '.join(FIELDS)}"
                ]
            )

        for line_number, row in enumerate(reader, start=2):
            if not any(str(value or "").strip() for value in row.values()):
                continue

            row_errors: list[str] = []
            prefix = f"CSV 第 {line_number} 列"

            year = parse_int(row.get("year"), f"{prefix} year", row_errors, required=True)
            end_year = parse_int(row.get("endYear"), f"{prefix} endYear", row_errors)
            row_number = parse_int(row.get("row"), f"{prefix} row", row_errors)

            if year is None:
                errors.extend(row_errors)
                continue
            if end_year is not None and end_year < year:
                row_errors.append(f"{prefix} endYear 不可早於 year")
            if row_number is not None and row_number < 1:
                row_errors.append(f"{prefix} row 必須是 1 以上的整數")

            characters = parse_characters(
                row.get("characters"),
                f"{prefix} characters",
                char_ids,
                row_errors,
            )
            paragraphs = parse_body(
                row.get("body"),
                f"{prefix} body",
                row_errors,
            )
            links = (
                parse_links(
                    row.get("links_title"),
                    row.get("links_url"),
                    f"{prefix} links",
                    row_errors,
                )
                if has_new_links
                else parse_legacy_links(
                    row.get("links"),
                    f"{prefix} links",
                    row_errors,
                )
            )

            tone = str(row.get("tone") or "").strip()
            if tone and tone not in TONES:
                row_errors.append(
                    f"{prefix} tone 必須是 moss、amber、shared 或 bridge"
                )

            temporary = parse_bool(
                row.get("temporary"),
                f"{prefix} temporary",
                row_errors,
            )

            event_id = str(row.get("id") or "").strip()
            if event_id:
                if not ID_PATTERN.fullmatch(event_id):
                    row_errors.append(
                        f"{prefix} id 只能使用英數字、底線與連字號，且以字母開頭"
                    )
                elif event_id in seen_ids:
                    row_errors.append(f"{prefix} id 重複：「{event_id}」")
            else:
                event_id = f"evt-{year}-{len(events) + 1:02d}"
                while event_id in seen_ids:
                    event_id += "x"

            if row_errors:
                errors.extend(row_errors)
                continue

            seen_ids.add(event_id)
            item: dict = {"id": event_id, "year": year}
            if end_year is not None:
                item["endYear"] = end_year
            item["characters"] = characters
            title = str(row.get("title") or "").strip()
            if title:
                item["title"] = title
            item["body"] = "\n\n".join(paragraphs)
            if links:
                item["links"] = links
            if tone:
                item["tone"] = tone
            if row_number is not None:
                item["row"] = row_number
            if temporary:
                item["temporary"] = True
            events.append(item)

    if errors:
        raise ConversionError(errors)

    json_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = json_path.with_name(json_path.name + ".tmp")
    temporary_path.write_text(
        json.dumps({"events": events}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(json_path)
    print(f"已轉換 {len(events)} 筆事件 → {json_path}")
    print("接著執行：python build.py")
    return 0


def export_csv(json_path: Path, csv_path: Path) -> int:
    payload = read_json(json_path)
    events = payload.get("events") if isinstance(payload, dict) else payload
    if not isinstance(events, list):
        raise ConversionError([f"{json_path}：events 必須是陣列"])

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=FIELDS,
            lineterminator="\r\n",
            quoting=csv.QUOTE_MINIMAL,
        )
        writer.writeheader()
        for event in events:
            if not isinstance(event, dict):
                continue
            body = event.get("body", "")
            if isinstance(body, list):
                body = "\n\n".join(str(part) for part in body)
            link_titles: list[str] = []
            link_urls: list[str] = []
            for link in event.get("links", []) or []:
                if not isinstance(link, dict):
                    continue
                link_label = str(link.get("label", "")).strip()
                url = str(link.get("url", "")).strip()
                if "|" in link_label or "|" in url:
                    raise ConversionError(
                        [
                            f"{json_path}：事件「{event.get('id', '?')}」的連結"
                            "標題或 URL 含有 `|`，無法用 CSV 的 `|` 分隔格式表示"
                        ]
                    )
                link_titles.append(link_label)
                link_urls.append(url)
            writer.writerow(
                {
                    "id": event.get("id", ""),
                    "year": event.get("year", ""),
                    "endYear": event.get("endYear", ""),
                    "row": event.get("row", ""),
                    "characters": ";".join(event.get("characters", []) or []),
                    "title": event.get("title", ""),
                    "tone": event.get("tone", ""),
                    "body": body,
                    "links_title": "|".join(link_titles),
                    "links_url": "|".join(link_urls),
                    "temporary": "true" if event.get("temporary") else "",
                }
            )

    print(f"已匯出 {len(events)} 筆事件 → {csv_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把 events.csv 轉成 events.json，或用 --export 反向匯出。"
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("data/events.csv"),
        help="CSV 檔案路徑（預設 data/events.csv）",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=Path("data/events.json"),
        help="JSON 檔案路徑（預設 data/events.json）",
    )
    parser.add_argument(
        "--characters",
        type=Path,
        default=Path("data/characters.json"),
        help="角色資料路徑，用來驗證 characters（預設 data/characters.json）",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="反向把 events.json 匯出成 CSV",
    )
    args = parser.parse_args()

    try:
        if args.export:
            return export_csv(args.json, args.csv)
        return import_csv(args.csv, args.json, args.characters)
    except ConversionError as exc:
        for message in exc.errors:
            print(f"錯誤：{message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
