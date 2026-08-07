#!/usr/bin/env python3
"""Gera a malha de bairros de 2010 e o painel anual por facção."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform, unary_union


ANOS = tuple(range(2010, 2023))
FACCOES = ("ADA", "CV", "Milicia", "TCP")
CRS_AREA = "EPSG:31983"
URL_MAPA = (
    "https://fogocruzado.org.br/mapadosgruposarmados/Mapview_Final/"
    "Mapa_Controle_Influencia_{ano}.html"
)
URL_CENSO = "https://censo2010.ibge.gov.br/resultados/resumo.html"

ALIASES = {
    "ada": "ADA",
    "amigos dos amigos": "ADA",
    "amigo dos amigos": "ADA",
    "cv": "CV",
    "comando vermelho": "CV",
    "cevada": "CV",
    "milicia": "Milicia",
    "tcp": "TCP",
    "terceiro comando": "TCP",
    "terceiro comando puro": "TCP",
}

PARA_AREA = Transformer.from_crs("EPSG:4326", CRS_AREA, always_xy=True)
PARA_WGS84 = Transformer.from_crs(CRS_AREA, "EPSG:4326", always_xy=True)


def ler_gzip_json(caminho: Path) -> dict[str, Any]:
    with gzip.open(caminho, "rt", encoding="utf-8") as arquivo:
        return json.load(arquivo)


def sha256(caminho: Path) -> str:
    resumo = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            resumo.update(bloco)
    return resumo.hexdigest()


def normalizar_texto(valor: Any) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(texto.lower().split())


def padronizar_faccao(valor: Any) -> str:
    canonico = ALIASES.get(normalizar_texto(valor))
    if canonico not in FACCOES:
        raise ValueError(f"Rótulo de facção desconhecido: {valor!r}")
    return canonico


def geometria_valida(geometria: BaseGeometry) -> BaseGeometry | None:
    if geometria.is_empty:
        return None
    geometria = make_valid(geometria)
    if geometria.is_empty or geometria.area <= 0:
        return None
    return geometria


def projetar(geometria_geojson: dict[str, Any]) -> BaseGeometry | None:
    geometria = geometria_valida(shape(geometria_geojson))
    if geometria is None:
        return None
    return geometria_valida(transform(PARA_AREA.transform, geometria))


def criar_malha(setores_path: Path) -> tuple[list[dict[str, Any]], int]:
    payload = ler_gzip_json(setores_path)
    grupos: dict[tuple[str, str], list[BaseGeometry]] = defaultdict(list)
    codigos_por_nome: dict[str, set[str]] = defaultdict(set)

    for feature in payload.get("features", []):
        props = feature.get("properties") or {}
        codigo = str(props.get("CD_GEOCODB") or "").strip()
        bairro = " ".join(str(props.get("NM_BAIRRO") or "").split())
        if not codigo or not bairro:
            raise ValueError("Setor censitário sem CD_GEOCODB ou NM_BAIRRO.")
        codigos_por_nome[bairro].add(codigo)
        geometria = projetar(feature["geometry"])
        if geometria is not None:
            grupos[(codigo, bairro)].append(geometria)

    ambiguos = {nome: codigos for nome, codigos in codigos_por_nome.items() if len(codigos) != 1}
    if ambiguos:
        raise ValueError(f"Nomes de bairro ligados a mais de um código: {ambiguos}")

    bairros: list[dict[str, Any]] = []
    for (codigo, bairro), geometrias in sorted(grupos.items()):
        uniao = geometria_valida(unary_union(geometrias))
        if uniao is None:
            raise ValueError(f"Geometria vazia para o bairro {bairro}.")
        bairros.append(
            {
                "codigo": codigo,
                "bairro": bairro,
                "area_m2": float(uniao.area),
                "geometry": uniao,
                "n_setores": len(geometrias),
            }
        )

    if len(bairros) != 160:
        raise ValueError(f"Esperados 160 bairros; encontrados {len(bairros)}.")
    return bairros, len(payload.get("features", []))


def carregar_faccoes(caminho: Path) -> tuple[dict[str, BaseGeometry], int]:
    payload = ler_gzip_json(caminho)
    grupos: dict[str, list[BaseGeometry]] = defaultdict(list)
    desconhecidos: set[str] = set()

    for feature in payload.get("features", []):
        props = feature.get("properties") or {}
        rotulo = props.get("faccao") or props.get("faccao_raw")
        try:
            faccao = padronizar_faccao(rotulo)
        except ValueError:
            desconhecidos.add(str(rotulo))
            continue
        geometria = projetar(feature["geometry"])
        if geometria is not None:
            grupos[faccao].append(geometria)

    if desconhecidos:
        raise ValueError(f"Rótulos de facção desconhecidos em {caminho.name}: {sorted(desconhecidos)}")
    faltantes = set(FACCOES) - set(grupos)
    if faltantes:
        raise ValueError(f"Facções ausentes em {caminho.name}: {sorted(faltantes)}")

    unioes: dict[str, BaseGeometry] = {}
    for faccao in FACCOES:
        uniao = geometria_valida(unary_union(grupos[faccao]))
        if uniao is None:
            raise ValueError(f"União geométrica vazia para {faccao} em {caminho.name}.")
        unioes[faccao] = uniao
    return unioes, len(payload.get("features", []))


def gerar_linhas(
    mapas_dir: Path,
    bairros: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[int, int], list[dict[str, Any]]]:
    linhas: list[dict[str, Any]] = []
    contagens: dict[int, int] = {}
    sobreposicoes: list[dict[str, Any]] = []

    for ano in ANOS:
        caminho = mapas_dir / f"{ano}.geojson.gz"
        if not caminho.exists():
            raise FileNotFoundError(f"Arquivo anual ausente: {caminho}")
        print(f"Processando {ano}...")
        unioes, n_features = carregar_faccoes(caminho)
        contagens[ano] = n_features

        for bairro in bairros:
            percentuais: list[float] = []
            for faccao in FACCOES:
                area_coberta = 0.0
                geometria_faccao = unioes[faccao]
                if geometria_faccao.intersects(bairro["geometry"]):
                    area_coberta = float(geometria_faccao.intersection(bairro["geometry"]).area)
                percentual = area_coberta / bairro["area_m2"] * 100.0
                if percentual > 100.000001:
                    raise ValueError(
                        f"Cobertura acima de 100%: {ano}, {bairro['bairro']}, {faccao}, {percentual}"
                    )
                percentuais.append(percentual)
                linhas.append(
                    {
                        "ano": ano,
                        "codigo_bairro_2010": bairro["codigo"],
                        "bairro": bairro["bairro"],
                        "faccao": faccao,
                        "area_bairro_m2": round(bairro["area_m2"], 3),
                        "area_faccao_bairro_m2": round(area_coberta, 3),
                        "percentual_area_bairro": round(percentual, 6),
                    }
                )
            soma = sum(percentuais)
            if soma > 100.000001:
                sobreposicoes.append(
                    {
                        "ano": ano,
                        "codigo_bairro_2010": bairro["codigo"],
                        "bairro": bairro["bairro"],
                        "soma_percentuais": round(soma, 6),
                    }
                )

    linhas.sort(key=lambda item: (item["ano"], item["codigo_bairro_2010"], item["faccao"]))
    return linhas, contagens, sobreposicoes


def gravar_csv(caminho: Path, linhas: Iterable[dict[str, Any]], campos: list[str]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8", newline="") as arquivo:
        writer = csv.DictWriter(arquivo, fieldnames=campos)
        writer.writeheader()
        writer.writerows(linhas)


def gravar_malha(caminho: Path, bairros: list[dict[str, Any]]) -> None:
    features = []
    for bairro in bairros:
        geometria_wgs84 = transform(PARA_WGS84.transform, bairro["geometry"])
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "codigo_bairro_2010": bairro["codigo"],
                    "bairro": bairro["bairro"],
                    "n_setores_2010": bairro["n_setores"],
                    "area_m2": round(bairro["area_m2"], 3),
                },
                "geometry": mapping(geometria_wgs84),
            }
        )
    payload = {"type": "FeatureCollection", "features": features}
    caminho.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def gravar_manifesto(
    caminho: Path,
    raiz: Path,
    mapas_dir: Path,
    setores_path: Path,
    contagens: dict[int, int],
    n_setores: int,
) -> None:
    linhas = []
    for ano in ANOS:
        arquivo = mapas_dir / f"{ano}.geojson.gz"
        linhas.append(
            {
                "tipo": "mapa_faccoes",
                "ano": ano,
                "arquivo": str(arquivo.relative_to(raiz)),
                "fonte_url": URL_MAPA.format(ano=ano),
                "n_feicoes": contagens[ano],
                "sha256": sha256(arquivo),
            }
        )
    linhas.append(
        {
            "tipo": "setores_censitarios",
            "ano": 2010,
            "arquivo": str(setores_path.relative_to(raiz)),
            "fonte_url": URL_CENSO,
            "n_feicoes": n_setores,
            "sha256": sha256(setores_path),
        }
    )
    gravar_csv(caminho, linhas, ["tipo", "ano", "arquivo", "fonte_url", "n_feicoes", "sha256"])


def argumentos() -> argparse.Namespace:
    raiz_padrao = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raiz", type=Path, default=raiz_padrao)
    return parser.parse_args()


def main() -> int:
    raiz = argumentos().raiz.resolve()
    mapas_dir = raiz / "dados/brutos/mapas"
    setores_path = raiz / "dados/brutos/censo/setores_censitarios_rio_2010.geojson.gz"
    saida_dir = raiz / "dados/processados"
    saida_dir.mkdir(parents=True, exist_ok=True)

    bairros, n_setores = criar_malha(setores_path)
    linhas, contagens, sobreposicoes = gerar_linhas(mapas_dir, bairros)

    if len(linhas) != len(ANOS) * len(bairros) * len(FACCOES):
        raise ValueError(f"Quantidade inesperada de linhas: {len(linhas)}")

    csv_path = saida_dir / "faccoes_bairro_2010_2022.csv"
    malha_path = saida_dir / "bairros_censo_2010.geojson"
    manifesto_path = raiz / "dados/manifesto.csv"
    auditoria_path = saida_dir / "auditoria.json"

    gravar_csv(
        csv_path,
        linhas,
        [
            "ano",
            "codigo_bairro_2010",
            "bairro",
            "faccao",
            "area_bairro_m2",
            "area_faccao_bairro_m2",
            "percentual_area_bairro",
        ],
    )
    gravar_malha(malha_path, bairros)
    gravar_manifesto(manifesto_path, raiz, mapas_dir, setores_path, contagens, n_setores)

    auditoria = {
        "anos": list(ANOS),
        "n_anos": len(ANOS),
        "n_bairros": len(bairros),
        "n_faccoes": len(FACCOES),
        "n_linhas": len(linhas),
        "crs_area": CRS_AREA,
        "sobreposicoes_acima_100": sobreposicoes,
    }
    auditoria_path.write_text(json.dumps(auditoria, ensure_ascii=False, indent=2), encoding="utf-8")

    if sobreposicoes:
        raise ValueError(
            f"Foram encontradas {len(sobreposicoes)} combinações bairro-ano com soma acima de 100%. "
            f"Consulte {auditoria_path}."
        )

    print(f"Painel: {csv_path} ({len(linhas)} linhas)")
    print(f"Malha: {malha_path} ({len(bairros)} bairros)")
    print(f"Manifesto: {manifesto_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
