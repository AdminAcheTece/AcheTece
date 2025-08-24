# gerar_cidades_ibge.py
# ------------------------------------------------------------
# Gera static/cidades_por_uf.json com TODAS as cidades do Brasil (IBGE)
# ------------------------------------------------------------
from typing import List, Dict
from pathlib import Path
import json
import sys

try:
    import requests
except ImportError:
    print("Instale a dependência primeiro:  pip install requests", file=sys.stderr)
    sys.exit(1)

UFs: List[str] = [
    "AC","AL","AM","AP","BA","CE","DF","ES","GO","MA","MG","MS","MT",
    "PA","PB","PE","PI","PR","RJ","RN","RO","RR","RS","SC","SE","SP","TO"
]
IBGE_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios?orderBy=nome"

def fetch_cidades(uf: str) -> List[str]:
    url = IBGE_URL.format(uf=uf)
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json() or []
    nomes = [item.get("nome") for item in data if isinstance(item, dict) and item.get("nome")]
    # ordena de forma estável/insensível a maiúsculas
    return sorted(nomes, key=lambda s: s.casefold())

def main() -> None:
    saida: Dict[str, List[str]] = {}
    for uf in UFs:
        print(f"Baixando municípios de {uf} ...")
        saida[uf] = fetch_cidades(uf)

    out_dir = Path("static")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "cidades_por_uf.json"

    with out_file.open("w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Gerado: {out_file.resolve()}  |  {out_file.stat().st_size} bytes")
    print("Dica: abra o arquivo e confira que começa com '{' e contém chaves AC..TO.")

if __name__ == "__main__":
    main()

