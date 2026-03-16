import csv
from config import CSV_PATH


def csv_ensure():
    if not CSV_PATH.exists():
        seed = CSV_PATH.parent / "sample_contacts.csv"
        if seed.exists():
            import shutil
            shutil.copy(seed, CSV_PATH)
        else:
            with open(CSV_PATH, "w", newline="") as f:
                csv.DictWriter(
                    f, fieldnames=["name", "email", "company", "phone", "notes"]
                ).writeheader()


def csv_load():
    csv_ensure()
    rows = []
    with open(CSV_PATH, newline="") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


def csv_append(name, email, company="", phone="", notes=""):
    csv_ensure()
    existing = [r["email"].lower() for r in csv_load()]
    if email.lower() in existing:
        return False
    with open(CSV_PATH, "a", newline="") as f:
        csv.DictWriter(
            f, fieldnames=["name", "email", "company", "phone", "notes"]
        ).writerow({"name": name, "email": email, "company": company,
                    "phone": phone, "notes": notes})
    return True
