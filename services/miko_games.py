from __future__ import annotations

import json
import logging
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

logger = logging.getLogger("discord_bot")


class MikoGameCatalog:
    _NS = {
        "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    }

    def __init__(self) -> None:
        configured = os.getenv("MIKO_GAMES_PATH", "").strip()
        self.paths = [
            Path(configured) if configured else None,
            Path("data/miko_games_catalog.json"),
            Path("Koikatu Based Games List.xlsx"),
            Path("data/Koikatu Based Games List.xlsx"),
        ]
        self.paths = [p for p in self.paths if p is not None]
        self.games: list[dict[str, str]] = []
        self.source = ""
        self._loaded = False

    @staticmethod
    def _clean(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(text).casefold()).strip()

    def load(self) -> int:
        if self._loaded:
            return len(self.games)
        self._loaded = True
        for path in self.paths:
            if not path.is_file():
                continue
            try:
                if path.suffix.casefold() == ".json":
                    rows = self._load_json(path)
                elif path.suffix.casefold() == ".xlsx":
                    rows = self._load_xlsx(path)
                else:
                    continue
                if rows:
                    self.games = rows
                    self.source = str(path)
                    logger.info("Miko catalog loaded | source=%s rows=%d", path, len(rows))
                    return len(rows)
            except Exception as exc:
                logger.warning("Could not load Miko catalog from %s: %s", path, exc)
        logger.warning("Miko catalog missing. Upload data/miko_games_catalog.json or the XLSX file.")
        return 0

    def _load_json(self, path: Path) -> list[dict[str, str]]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw = raw.get("games", raw.get("catalog", []))
        if not isinstance(raw, list):
            return []
        rows: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = self._clean(item.get("name") or item.get("title"))
            if not name:
                continue
            rows.append({
                "name": name,
                "engine": self._clean(item.get("engine")),
                "type": self._clean(item.get("type")),
                "visuals": self._clean(item.get("visuals")),
                "status": self._clean(item.get("status")),
                "tags": self._clean(item.get("tags")),
            })
        return rows

    def _load_xlsx(self, path: Path) -> list[dict[str, str]]:
        with zipfile.ZipFile(path) as archive:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                for item in root.findall("a:si", self._NS):
                    shared.append("".join((t.text or "") for t in item.iter("{%s}t" % self._NS["a"])))

            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            relmap = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}

            target = None
            for sheet in workbook.find("a:sheets", self._NS):
                if sheet.attrib.get("name") == "Games":
                    target = relmap.get(sheet.attrib.get("{%s}id" % self._NS["r"]))
                    break
            if not target:
                return []

            sheet_path = str(Path("xl") / target.lstrip("/"))
            sheet_root = ET.fromstring(archive.read(sheet_path))
            rows: list[dict[str, str]] = []

            for row in sheet_root.findall(".//a:sheetData/a:row", self._NS):
                values: dict[str, str] = {}
                for cell in row.findall("a:c", self._NS):
                    ref = cell.attrib.get("r", "")
                    col = "".join(ch for ch in ref if ch.isalpha())
                    typ = cell.attrib.get("t")
                    node = cell.find("a:v", self._NS)
                    value = ""
                    if node is not None:
                        raw = node.text or ""
                        if typ == "s":
                            try:
                                value = shared[int(raw)]
                            except (ValueError, IndexError):
                                value = raw
                        else:
                            value = raw
                    else:
                        inline = cell.find("a:is", self._NS)
                        if inline is not None:
                            value = "".join((t.text or "") for t in inline.iter("{%s}t" % self._NS["a"]))
                    values[col] = self._clean(value)

                if row.attrib.get("r") == "1":
                    continue
                if not values.get("C"):
                    continue
                rows.append({
                    "name": values.get("C", ""),
                    "engine": values.get("E", ""),
                    "type": values.get("F", ""),
                    "visuals": values.get("G", ""),
                    "status": values.get("H", ""),
                    "tags": values.get("I", ""),
                })
            return rows

    def search(self, query: str, *, limit: int = 5, include_tags: bool = False) -> list[dict[str, str]]:
        self.load()
        normalized = self._normalize(query)
        if not normalized:
            return []
        tokens = set(normalized.split())
        scored: list[tuple[float, dict[str, str]]] = []

        for game in self.games:
            name = self._normalize(game["name"])
            searchable = self._normalize(" ".join([
                game.get("name", ""),
                game.get("engine", ""),
                game.get("type", ""),
                game.get("visuals", ""),
                game.get("tags", "") if include_tags else "",
                game.get("status", ""),
            ]))
            words = set(searchable.split())
            score = (10.0 if normalized in name else 0.0) + (2.0 * len(tokens & words))
            if self._normalize(game.get("status", "")) == "active":
                score += 0.15
            if score > 0:
                scored.append((score, game))

        scored.sort(key=lambda item: (-item[0], self._normalize(item[1]["name"])))
        return [game for _, game in scored[:max(1, limit)]]

    def prompt_context(self, query: str, *, limit: int = 4, include_tags: bool = False) -> str:
        normalized = self._normalize(query)
        words = set(normalized.split())
        if words & {"recommend", "recommendation", "suggest", "play", "playing", "catalog", "list"}:
            self.load()
            games = [g for g in self.games if self._normalize(g.get("status", "")) == "active"][:limit]
        else:
            games = self.search(query, limit=limit, include_tags=include_tags)

        if not games:
            return ""

        lines = []
        for game in games:
            meta = []
            for key in ("status", "type", "engine", "visuals"):
                if game.get(key):
                    meta.append(f"{key}={game[key]}")
            if include_tags and game.get("tags"):
                meta.append(f"tags={game['tags'][:500]}")
            lines.append("- " + game["name"] + (" (" + "; ".join(meta) + ")" if meta else ""))

        return (
            "Relevant entries from the local game catalog. Treat names and listed fields as authoritative; "
            "do not invent missing details:\n" + "\n".join(lines)
        )
