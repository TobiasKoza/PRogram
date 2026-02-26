import bcrypt

# Zde si doplň reálná hesla pro své hráče
hesla = ["hesloTobi123", "hesloKuba456", "hesloJirka789"]

for heslo in hesla:
    # Vytvoření hashe pomocí bcrypt
    hash_hesla = bcrypt.hashpw(heslo.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    print(f"Heslo: {heslo}")
    print(f"Hash:  {hash_hesla}")
    print("-" * 40)