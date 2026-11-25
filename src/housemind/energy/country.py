"""Country energy rules as data (E-69): VAT, energy tax, net metering, capacity tariff …"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class CountryRules:
    country: str = "XX"
    currency: str = "EUR"
    vat: float = 0.0
    energy_tax_per_kwh: float = 0.0
    net_metering_until: date | None = None
    export_paid_at_negative_price: bool = False
    quarter_hour_prices: bool = True
    capacity_minimum_kw: float | None = None
    capacity_per_kw_year: float | None = None
    controllable_minimum_kw: float | None = None

    def net_metering(self, day: date) -> bool:
        """Exported electricity is netted against use (worth the full consumer price)."""
        return self.net_metering_until is not None and day <= self.net_metering_until

    def all_in(self, spot_per_kwh: float) -> float:
        """A market price per kWh plus energy tax and VAT (what a household pays)."""
        return (spot_per_kwh + self.energy_tax_per_kwh) * (1 + self.vat)


def _amount(raw: Any) -> float:
    if isinstance(raw, dict):
        raw = raw.get("value")
    return float(raw) if isinstance(raw, int | float) else 0.0


def load_country(directories: list[Path], code: str | None) -> CountryRules:
    """The rules for an HA country code; later directories override earlier ones."""
    if not code:
        return CountryRules()
    found: dict[str, Any] | None = None
    for directory in directories:
        path = directory / f"{code.lower()}.yaml"
        if path.exists():
            found = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not found:
        return CountryRules(country=code.upper())
    energy = found.get("energy") or {}
    metering = energy.get("net_metering") or {}
    capacity = energy.get("capacity_tariff") or {}
    controllable = energy.get("controllable_load") or {}
    until = metering.get("until") if isinstance(metering, dict) else None
    return CountryRules(
        country=str(found.get("country", code)).upper(),
        currency=str(found.get("currency", "EUR")),
        vat=float(energy.get("vat") or 0.0),
        energy_tax_per_kwh=_amount(energy.get("energy_tax_per_kwh")),
        net_metering_until=date.fromisoformat(str(until)) if until else None,
        export_paid_at_negative_price=bool(energy.get("export_paid_at_negative_price", False)),
        quarter_hour_prices=bool(energy.get("quarter_hour_prices", True)),
        capacity_minimum_kw=float(capacity["minimum_kw"]) if "minimum_kw" in capacity else None,
        capacity_per_kw_year=float(capacity["per_kw_year"]) if "per_kw_year" in capacity else None,
        controllable_minimum_kw=float(controllable["minimum_kw"])
        if "minimum_kw" in controllable
        else None,
    )
