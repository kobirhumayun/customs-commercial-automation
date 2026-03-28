from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class ERPFamily:
    lc_sc_number: str
    buyer_name: str
    lc_sc_date: str


@dataclass(slots=True, frozen=True)
class ERPRegisterRow:
    file_number: str
    lc_sc_number: str
    buyer_name: str
    lc_sc_date: str
    source_row_index: int
    notify_bank: str = ""
    current_lc_value: str = ""
    ship_date: str = ""
    expiry_date: str = ""
    lc_qty: str = ""
    lc_unit: str = ""
    amd_no: str = ""
    amd_date: str = ""
    nego_bank: str = ""
    master_lc_no: str = ""
    master_lc_date: str = ""

    @property
    def family(self) -> ERPFamily:
        return ERPFamily(
            lc_sc_number=self.lc_sc_number,
            buyer_name=self.buyer_name,
            lc_sc_date=self.lc_sc_date,
        )
