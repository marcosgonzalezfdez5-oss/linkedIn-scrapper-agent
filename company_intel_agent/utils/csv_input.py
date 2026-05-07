import csv
from pathlib import Path

PREFERRED_HEADERS = ("company", "company_name", "name")


def read_company_names(path: str | Path) -> list[str]:
    """Return a deduped, order-preserving list of non-empty company names from a CSV.

    Column resolution:
      1. If headers present and any PREFERRED_HEADERS match (case-insensitive), use that column.
      2. Otherwise use the first column.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"CSV not found: {p}")

    with p.open(newline="", encoding="utf-8-sig") as f:
        sample = f.read(2048)
        f.seek(0)
        has_header = csv.Sniffer().has_header(sample) if sample.strip() else False
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        return []

    if has_header:
        header = [h.strip().lower() for h in rows[0]]
        col = next((header.index(h) for h in PREFERRED_HEADERS if h in header), 0)
        data_rows = rows[1:]
    else:
        col = 0
        data_rows = rows

    seen: set[str] = set()
    out: list[str] = []
    for row in data_rows:
        if col >= len(row):
            continue
        name = row[col].strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out
