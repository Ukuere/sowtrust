"""
Sowtrust — Demo Data Seeder
Run: python scripts/seed_demo_data.py
Populates the DB with realistic sample data for dashboard testing.
"""
import sys, os, sqlite3, hashlib, random, uuid
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import config

def hash_pin(p): return hashlib.sha256(p.encode()).hexdigest()

FARMERS = [
    ("Emeka Okafor",    "+2348011111111", "Maize",    "Ogun State",   150000),
    ("Bello Ibrahim",   "+2348022222222", "Rice",     "Kano State",   380000),
    ("Ngozi Eze",       "+2348033333333", "Cassava",  "Enugu State",  65000),
    ("Sule Musa",       "+2348044444444", "Yam",      "Benue State",  250000),
    ("Aisha Garba",     "+2348055555555", "Soybeans", "Kaduna State", 420000),
    ("Chidi Nwachukwu", "+2348066666666", "Palm Oil", "Rivers State", 310000),
]

AGENTS = [
    ("Tunde Adeyemi", "+2348077777777", "Lagos State"),
    ("Fatima Yusuf",  "+2348088888888", "Abuja FCT"),
]

BUYERS = ["+2349011111111", "+2349022222222", "+2349033333333"]

def seed():
    conn = sqlite3.connect(config.DATABASE_PATH)
    pin = hash_pin("1234")

    for name, phone, crop, loc, price in FARMERS:
        try:
            conn.execute(
                "INSERT INTO farmers (name,phone,crop,location,pin_hash,price,balance,kyc_status,credit_score) VALUES (?,?,?,?,?,?,?,?,?)",
                (name, phone, crop, loc, pin, price, random.uniform(10000, 80000), "VERIFIED", random.randint(1, 15))
            )
        except: pass

    for name, phone, loc in AGENTS:
        try:
            conn.execute(
                "INSERT INTO agents (name,phone,pin_hash,location,recruits) VALUES (?,?,?,?,?)",
                (name, phone, pin, loc, random.randint(2, 10))
            )
        except: pass

    for bphone in BUYERS:
        try:
            conn.execute("INSERT OR IGNORE INTO buyers (phone) VALUES (?)", (bphone,))
        except: pass

    import hashlib as hl
    statuses = ["ESCROW_LOCKED", "DELIVERED", "DELIVERED", "EXPIRED"]
    crops    = ["Maize", "Rice", "Cassava", "Yam", "Soybeans"]
    for i in range(12):
        fphone = FARMERS[i % len(FARMERS)][1]
        bphone = BUYERS[i % len(BUYERS)]
        crop   = crops[i % len(crops)]
        amount = random.uniform(50000, 500000)
        fee    = amount * 0.025
        status = statuses[i % len(statuses)]
        code_h = hl.sha256(b"DEMOREL").hexdigest()
        try:
            conn.execute(
                """INSERT INTO escrow_ledger
                   (farmer_phone,buyer_phone,crop,quantity_bags,amount,service_fee,release_code_hash,status)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (fphone, bphone, crop, random.randint(1,10), amount, fee, code_h, status)
            )
        except: pass

    conn.commit()
    conn.close()
    print("✅ Demo data seeded successfully.")

if __name__ == "__main__":
    seed()
