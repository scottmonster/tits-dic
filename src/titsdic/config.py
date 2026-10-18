from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


DEFAULT_CONFIG_PATH = Path("titsdic.yaml")


@dataclass(slots=True)
class SectionOne:
	oo: float
	ot: str


@dataclass(slots=True)
class SectionTwo:
	twoone: str | None = None
	twotwo: str | None = None


@dataclass(slots=True)
class Config:
	path: Path
	sectionone: SectionOne
	sectiontwo: SectionTwo

	@classmethod
	def load(cls, config_path: str | Path = DEFAULT_CONFIG_PATH)->Config:
		path = Path(config_path).expanduser()
		if not path.exists():
			raise FileNotFoundError(f"config file not found: {path}")

		with path.open("r", encoding="utf-8") as handle:
			loaded = yaml.safe_load(handle) or {}

		if not isinstance(loaded, dict):
			raise ValueError("config file must contain a top-level mapping")

		sectionone_data = loaded.get("sectionone")
		if not isinstance(sectionone_data, dict):
			raise ValueError("sectionone must be a mapping")

		sectiontwo_data = loaded.get("sectiontwo")
		sectiontwo_map = _coerce_sectiontwo(sectiontwo_data)

		return cls(
			path=path,
			sectionone=SectionOne(
				oo=float(sectionone_data["oo"]),
				ot=str(sectionone_data["ot"]),
			),
			sectiontwo=SectionTwo(
				twoone=_optional_string(sectiontwo_map, "twoone"),
				twotwo=_optional_string(sectiontwo_map, "twotwo"),
			),
		)


def _coerce_sectiontwo(sectiontwo_data: object)->dict[str, object]:
	if isinstance(sectiontwo_data, dict):
		return sectiontwo_data

	if isinstance(sectiontwo_data, list):
		merged: dict[str, object] = {}
		for item in sectiontwo_data:
			if not isinstance(item, dict):
				raise ValueError("each sectiontwo list item must be a mapping")
			merged.update(item)
		return merged

	raise ValueError("sectiontwo must be a mapping or a list of mappings")


def _optional_string(data: dict[str, object], key: str)->str | None:
	value = data.get(key)
	if value is None:
		return None
	return str(value)
	
  
