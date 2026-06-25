"""Load the bird species list the calendar can show.

Canonical species = entries in the model's labels.txt whose taxonomy class is
`aves`. labels.txt key == species_code == eBird/iNat taxon code, which is also
the join key into taxonomy.csv (multilingual names + class_name).
"""
import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs")
LABELS_PATH = os.path.join(DOCS, "labels.txt")
TAXONOMY_PATH = os.path.join(DOCS, "taxonomy.csv")


def load_taxonomy(path=TAXONOMY_PATH):
    """species_code -> {sci, common, class_name} from taxonomy.csv."""
    by_code = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = row.get("species_code")
            if not code:
                continue
            by_code[code] = {
                "sci": row.get("sci_name", ""),
                "common": row.get("com_name", ""),
                "class_name": (row.get("class_name") or "").lower(),
            }
    return by_code


def load_species(only_aves=True, labels_path=LABELS_PATH, taxonomy_path=TAXONOMY_PATH):
    """Ordered list of {code, sci, common, class_name} for model species."""
    tax = load_taxonomy(taxonomy_path)
    out = []
    with open(labels_path, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if not parts or not parts[0]:
                continue
            code = parts[0]
            t = tax.get(code, {})
            cls = t.get("class_name", "")
            if only_aves and cls != "aves":
                continue
            out.append({
                "code": code,
                "sci": t.get("sci") or (parts[1] if len(parts) > 1 else ""),
                "common": t.get("common") or (parts[2] if len(parts) > 2 else ""),
                "class_name": cls,
            })
    return out


def resolve_sci(sci_names, only_aves=True):
    """Map a list of scientific names to species dicts (case-insensitive)."""
    by_sci = {s["sci"].lower(): s for s in load_species(only_aves=only_aves)}
    out = []
    for name in sci_names:
        s = by_sci.get(name.strip().lower())
        if s:
            out.append(s)
        else:
            print(f"  ! not found in model species: {name}")
    return out


if __name__ == "__main__":
    birds = load_species()
    print(f"{len(birds)} bird species in the model")
    for b in birds[:5]:
        print(" ", b["code"], b["sci"], "—", b["common"])
