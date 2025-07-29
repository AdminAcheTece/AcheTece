import os

print("🔍 Procurando definição da classe Empresa...\n")

for root, dirs, files in os.walk("."):
    for file in files:
        if file.endswith(".py"):
            path = os.path.join(root, file)
            with open(path, encoding="utf-8") as f:
                for i, line in enumerate(f):
                    if "class Empresa" in line:
                        print(f"✅ Encontrado em {path}, linha {i+1}")