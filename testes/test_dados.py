from __future__ import annotations

import csv
import gzip
import hashlib
import json
import sys
import unittest
from collections import defaultdict
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform


RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import gerar_dados  # noqa: E402


class DadosGeradosTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        csv_path = RAIZ / "dados/processados/faccoes_bairro_2010_2022.csv"
        with csv_path.open(encoding="utf-8", newline="") as arquivo:
            cls.linhas = list(csv.DictReader(arquivo))

    def test_painel_completo(self) -> None:
        self.assertEqual(len(self.linhas), 8320)
        self.assertEqual({int(linha["ano"]) for linha in self.linhas}, set(range(2010, 2023)))
        self.assertEqual({linha["faccao"] for linha in self.linhas}, set(gerar_dados.FACCOES))
        self.assertEqual(len({linha["codigo_bairro_2010"] for linha in self.linhas}), 160)

    def test_chave_unica_e_sem_faltantes(self) -> None:
        chaves = {
            (linha["ano"], linha["codigo_bairro_2010"], linha["faccao"])
            for linha in self.linhas
        }
        self.assertEqual(len(chaves), len(self.linhas))
        for linha in self.linhas:
            self.assertTrue(all(valor != "" for valor in linha.values()))

    def test_areas_e_percentuais(self) -> None:
        somas: dict[tuple[str, str], float] = defaultdict(float)
        for linha in self.linhas:
            area_bairro = float(linha["area_bairro_m2"])
            area_faccao = float(linha["area_faccao_bairro_m2"])
            percentual = float(linha["percentual_area_bairro"])
            self.assertGreater(area_bairro, 0)
            self.assertGreaterEqual(area_faccao, 0)
            self.assertGreaterEqual(percentual, 0)
            self.assertLessEqual(percentual, 100.000001)
            somas[(linha["ano"], linha["codigo_bairro_2010"])] += percentual
        self.assertLessEqual(max(somas.values()), 100.00001)

    def test_malha_corresponde_ao_csv(self) -> None:
        malha_path = RAIZ / "dados/processados/bairros_censo_2010.geojson"
        malha = json.loads(malha_path.read_text(encoding="utf-8"))
        self.assertEqual(len(malha["features"]), 160)
        codigos_malha = {
            feature["properties"]["codigo_bairro_2010"] for feature in malha["features"]
        }
        codigos_csv = {linha["codigo_bairro_2010"] for linha in self.linhas}
        self.assertEqual(codigos_malha, codigos_csv)

        para_area = Transformer.from_crs("EPSG:4326", "EPSG:31983", always_xy=True)
        for feature in malha["features"]:
            geometria = shape(feature["geometry"])
            self.assertFalse(geometria.is_empty)
            self.assertTrue(geometria.is_valid)
            area_calculada = transform(para_area.transform, geometria).area
            area_registrada = float(feature["properties"]["area_m2"])
            self.assertAlmostEqual(area_calculada, area_registrada, delta=area_registrada * 0.000001)

    def test_manifesto_confere_com_os_arquivos(self) -> None:
        with (RAIZ / "dados/manifesto.csv").open(encoding="utf-8", newline="") as arquivo:
            manifesto = list(csv.DictReader(arquivo))
        self.assertEqual(len(manifesto), 14)
        for item in manifesto:
            caminho = RAIZ / item["arquivo"]
            self.assertTrue(caminho.exists())
            resumo = hashlib.sha256(caminho.read_bytes()).hexdigest()
            self.assertEqual(resumo, item["sha256"])
            with gzip.open(caminho, "rt", encoding="utf-8") as arquivo:
                payload = json.load(arquivo)
            self.assertEqual(len(payload["features"]), int(item["n_feicoes"]))

    def test_rotulo_desconhecido_falha(self) -> None:
        with self.assertRaises(ValueError):
            gerar_dados.padronizar_faccao("grupo sem classificação")


if __name__ == "__main__":
    unittest.main()
