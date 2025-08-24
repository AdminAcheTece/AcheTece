# gerar_cidades_ibge.py
# ------------------------------------------------------------
# Gera static/cidades_por_uf.json com TODAS as cidades do Brasil
# ------------------------------------------------------------
import json
import os
from pathlib import Path

try:
    import requests
except ImportError:
    raise SystemExit("Instale a lib requests:  pip install requests")

UFs = [
    "AC","AL","AM","AP","BA","CE","DF","ES","GO","MA","MG","MS","MT",
    "PA","PB","PE","PI","PR","RJ","RN","RO","RR","RS","SC","SE","SP","TO"
]

BASE = "https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios?orderBy=nome"

def fetch_cidades(uf: str) -> list[str]:
    url = BASE.format(uf=uf)
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json() or []
    nomes = [item.get("nome") for item in data if isinstance(item, dict) and item.get("nome")]
    return sorted(nomes, key=lambda s: s.casefold())

def main():
    saida = {}
    for uf in UFs:
        print(f"Baixando municípios de {uf} ...")
        saida[uf] = fetch_cidades(uf)

    out_dir = Path("static")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "cidades_por_uf.json"

    with out_file.open("w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Gerado: {out_file.resolve()}")

if __name__ == "__main__":
    main()
